"""End-to-end PostgreSQL evidence for Module 3B.2 OHLCV volume/turnover semantics.

Every provider response here is a synthetic FIXTURE served by a scripted
in-process transport; no socket is opened and nothing is retrieved from or
verified against Bybit. The real canonical ``CRYPTO:BYBIT:BTCUSDT:PERP`` is
onboarded through the existing onboarding, then a small OHLCV-only window is
acquired through the merged :class:`HistoricalAcquisitionService` -- the one
ingestion path -- and the typed volume-semantics authority is proved durable.

Two classes cover the two dataset compatibility classes the phase requires:

* :class:`AuthorizedBybitOhlcvVolumeSemanticsPostgresTests` -- an authorized
  Bybit linear source: every normalized OHLCV row carries a typed sidecar, the
  research projection and the tradable-bar reader expose the semantics with zero
  raw-payload parsing, provider turnover is preserved exactly, and the sealed
  dataset content hash binds and is sensitive to the semantics.
* :class:`LegacyOhlcvVolumeSemanticsPostgresTests` -- an unauthorized source:
  no sidecar, unitless research readback even though the raw payload carries a
  provider turnover, and a sealed content hash that reproduces the pre-3B.2
  legacy formula exactly.

Each class owns a DISPOSABLE database beneath the configured local/CI PostgreSQL
instance, exactly as the Phase 3B.1 acquisition tests do.

LIVE BYBIT CALLS PERFORMED: NO
LIVE ORDER/ACCOUNT CALLS PERFORMED: NO
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

VENUE = "BYBIT"
SYMBOL = "BTCUSDT"
INSTRUMENT_ID = "CRYPTO:BYBIT:BTCUSDT:PERP"

ONBOARDED_AT = datetime(2026, 9, 15, tzinfo=UTC)
START = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
END = datetime(2026, 9, 16, 0, 30, tzinfo=UTC)
NOW = datetime(2026, 9, 16, 1, 0, tzinfo=UTC)
NORMALIZATION_VERSION = "bybit-v5-md-phase3b2"
DATASET_VERSION = "bybit-v5-linear-btcusdt-phase3b2-volume-semantics"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)

# Distinct turnover per bar, deliberately NOT equal to volume*close, so the test
# proves the authority preserves the provider figure rather than deriving it.
BAR_OPENS = [START + index * _MINUTE for index in range(30)]
TRADE_CLOSES = [f"{27000 + index}.0" for index in range(30)]
TURNOVERS = [f"{337500 + index}.5" for index in range(30)]
VOLUME = "12.5"


def _ms(value: datetime) -> int:
    return int((value - _EPOCH).total_seconds()) * 1000


def _envelope(result: dict[str, object]) -> str:
    return json.dumps(
        {"retCode": 0, "retMsg": "OK", "result": result, "retExtInfo": {}, "time": _ms(NOW)}
    )


def _kline_envelope(rows: list[list[str]]) -> str:
    return _envelope({"category": "linear", "symbol": SYMBOL, "list": rows})


def _trade_row(bar_open: datetime, close: str, turnover: str) -> list[str]:
    return [str(_ms(bar_open)), "27000.0", "27100.0", "26900.0", close, VOLUME, turnover]


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _disposable_dsn(source_dsn: str, database_name: str) -> str:
    parsed = urlparse(source_dsn)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


def _migrate(dsn: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    )
    old_dsn = os.environ.get("POSTGRES_TEST_DSN")
    try:
        os.environ["POSTGRES_TEST_DSN"] = dsn
        command.upgrade(config, "head")
    finally:
        if old_dsn is not None:
            os.environ["POSTGRES_TEST_DSN"] = old_dsn


class RoutingTransport:
    def __init__(self, routes: dict[str, list[object]]) -> None:
        self._routes = {path: list(bodies) for path, bodies in routes.items()}
        self.urls: list[str] = []

    def get(self, url: str, timeout_seconds: float) -> object:
        self.urls.append(url)
        for path, bodies in self._routes.items():
            if path in url:
                if not bodies:
                    raise AssertionError(f"exhausted route: {path}")
                return bodies.pop(0)
        raise AssertionError(f"unrouted request: {url}")


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class AuthorizedBybitOhlcvVolumeSemanticsPostgresTests(unittest.TestCase):
    database_name: str
    dsn: str

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest(
                "Phase 3B.2 volume semantics require a local or CI disposable PostgreSQL DSN"
            )
        cls.database_name = f"ohlcv_volume_semantics_phase3b2_{os.getpid()}"
        cls.dsn = _disposable_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')
        _migrate(cls.dsn)

    @classmethod
    def tearDownClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    def test_authorized_bybit_ohlcv_binds_typed_volume_semantics_end_to_end(self) -> None:
        from trade_platform.bybit_crypto_provider import BybitCryptoHistoricalAdapter
        from trade_platform.bybit_instrument_onboarding import (
            captured_btcusdt_snapshot_v1,
            onboard_bybit_btcusdt_perpetual_v1,
        )
        from trade_platform.data_providers import HttpResponse, ProviderConfiguration
        from trade_platform.historical_acquisition import (
            AcquisitionStatus,
            HistoricalAcquisitionRequest,
            HistoricalAcquisitionService,
            acquisition_fingerprint,
        )
        from trade_platform.historical_market_data import (
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
        )
        from trade_platform.ohlcv_volume_semantics import (
            BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION,
            OHLCV_VOLUME_SEMANTICS_CANONICAL_IDENTITY,
            BarVolumeUnit,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.tradable_bar_evidence_v2 import PostgresTradableBarEvidenceReaderV2

        database = PostgresDatabase(self.dsn)

        onboarding = onboard_bybit_btcusdt_perpetual_v1(
            database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )
        self.assertEqual(onboarding.instrument_id, INSTRUMENT_ID)
        source_id = onboarding.source_id

        transport = RoutingTransport(
            {
                "/v5/market/kline": [
                    HttpResponse(
                        200,
                        _kline_envelope(
                            [
                                _trade_row(bar_open, close, turnover)
                                for bar_open, close, turnover in reversed(
                                    list(zip(BAR_OPENS, TRADE_CLOSES, TURNOVERS, strict=True))
                                )
                            ]
                        ),
                    )
                ],
            }
        )

        def adapter_factory(
            configuration: ProviderConfiguration, now: object
        ) -> BybitCryptoHistoricalAdapter:
            return BybitCryptoHistoricalAdapter(
                configuration,
                transport=transport,  # type: ignore[arg-type]
                now=now,  # type: ignore[arg-type]
                sleep=lambda _seconds: None,
            )

        service = HistoricalAcquisitionService.for_postgres(
            database, adapter_factory=adapter_factory, now=lambda: NOW
        )

        request = HistoricalAcquisitionRequest(
            source_id=source_id,
            instrument_id=INSTRUMENT_ID,
            provider="bybit",
            provider_symbol=SYMBOL,
            start=START,
            end=END,
            observation_kinds=frozenset({ObservationKind.OHLCV}),
            normalization_version=NORMALIZATION_VERSION,
            dataset_version=DATASET_VERSION,
            maximum_pages_per_kind=8,
            materialize_features=False,
            idempotency_key="",
        )
        request = replace(request, idempotency_key=acquisition_fingerprint(request))
        configuration = ProviderConfiguration(
            provider="bybit",
            base_url="https://api.bybit.com",
            terms_accepted=True,
            secret_reference=None,
        )

        result = service.acquire(request, configuration)
        self.assertEqual(result.status, AcquisitionStatus.SUCCEEDED, result.failure_code)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(result.raw_counts, {ObservationKind.OHLCV: 30})
        self.assertEqual(result.normalized_counts, {ObservationKind.OHLCV: 30})
        dataset_version_id = result.dataset_version_id
        assert dataset_version_id is not None
        assert result.dataset_content_hash is not None

        # ---- typed sidecar exists 1:1 for every normalized OHLCV row ----------
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM historical_ohlcv_volume_semantics")
            self.assertEqual(int(str(cursor.fetchone()[0])), 30)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r "
                "  ON r.raw_observation_id=n.raw_observation_id "
                "LEFT JOIN historical_ohlcv_volume_semantics vs "
                "  ON vs.normalized_observation_id=n.normalized_observation_id "
                "WHERE r.observation_kind='OHLCV' AND vs.normalized_observation_id IS NULL"
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)
            cursor.execute(
                "SELECT DISTINCT volume_unit,volume_asset,turnover_unit,turnover_asset,"
                "semantic_version FROM historical_ohlcv_volume_semantics"
            )
            distinct = cursor.fetchall()
            self.assertEqual(
                distinct,
                [
                    (
                        "BASE_ASSET",
                        "BTC",
                        "QUOTE_ASSET",
                        "USDT",
                        BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION,
                    )
                ],
            )
            # provider turnover preserved exactly, per bar, never derived.
            cursor.execute(
                "SELECT r.event_at, vs.turnover FROM historical_ohlcv_volume_semantics vs "
                "JOIN historical_normalized_observations n "
                "  ON n.normalized_observation_id=vs.normalized_observation_id "
                "JOIN historical_raw_observations r "
                "  ON r.raw_observation_id=n.raw_observation_id"
            )
            stored_turnover = {row[0]: Decimal(str(row[1])) for row in cursor.fetchall()}
        expected_turnover = {
            bar_open: Decimal(turnover)
            for bar_open, turnover in zip(BAR_OPENS, TURNOVERS, strict=True)
        }
        self.assertEqual(stored_turnover, expected_turnover)

        # ---- research readback exposes semantics, none parsed from raw JSON ---
        pipeline = PostgresHistoricalMarketDataPipeline(database)
        observations = pipeline.research_query(dataset_version_id, INSTRUMENT_ID, START, END, NOW)
        self.assertEqual(len(observations), 30)
        for observation in observations:
            self.assertEqual(observation.volume_unit, "BASE_ASSET")
            self.assertEqual(observation.volume_asset, "BTC")
            self.assertEqual(observation.turnover_unit, "QUOTE_ASSET")
            self.assertEqual(observation.turnover_asset, "USDT")
            self.assertEqual(
                observation.volume_semantic_version,
                BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION,
            )
            self.assertIsNotNone(observation.turnover)
            # volume itself stays in the OHLCV envelope, unchanged and unitless.
            self.assertEqual(observation.normalized_value["volume"], VOLUME)

        # ---- tradable-bar reader exposes semantics from the typed authority ---
        reader = PostgresTradableBarEvidenceReaderV2(database)
        series = reader.series(
            dataset_version_id=dataset_version_id,
            instrument_id=INSTRUMENT_ID,
            interval="1m",
            research_run_at=NOW,
        )
        self.assertEqual(len(series.bars), 30)
        for bar in series.bars:
            self.assertEqual(bar.volume, Decimal(VOLUME))
            self.assertEqual(bar.volume_unit, BarVolumeUnit.BASE_ASSET)
            self.assertEqual(bar.volume_asset, "BTC")
            self.assertEqual(bar.turnover_unit, BarVolumeUnit.QUOTE_ASSET)
            self.assertEqual(bar.turnover_asset, "USDT")
            self.assertIsNotNone(bar.turnover)
            self.assertEqual(
                bar.volume_semantic_version, BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION
            )
        bar_turnover = {bar.bar_open_at: bar.turnover for bar in series.bars}
        self.assertEqual(bar_turnover, expected_turnover)

        # ---- the sealed content hash binds and is sensitive to the semantics --
        members = self._sealed_members(database, dataset_version_id)
        self.assertEqual(len(members), 30)

        def dataset_hash(tuple_for: object) -> str:
            digest = hashlib.sha256()
            for member in sorted(members, key=lambda item: str(item["norm_id"])):
                parts = [
                    member["norm_id"],
                    member["raw_sha256"],
                    "OHLCV",
                    _canonical(member["normalized_value"]),
                    *tuple_for(member),  # type: ignore[operator]
                ]
                digest.update("|".join(parts).encode())
            return digest.hexdigest()

        def real_tuple(member: dict[str, object]) -> tuple[str, ...]:
            return (
                OHLCV_VOLUME_SEMANTICS_CANONICAL_IDENTITY,
                str(member["volume_unit"]),
                str(member["volume_asset"]),
                str(member["turnover"]),
                str(member["turnover_unit"]),
                str(member["turnover_asset"]),
                str(member["semantic_version"]),
                str(member["source_reference"]),
            )

        def legacy_tuple(_member: dict[str, object]) -> tuple[str, ...]:
            return ()

        def contracts_tuple(member: dict[str, object]) -> tuple[str, ...]:
            base = list(real_tuple(member))
            base[1] = "CONTRACTS"
            return tuple(base)

        def other_asset_tuple(member: dict[str, object]) -> tuple[str, ...]:
            base = list(real_tuple(member))
            base[5] = "USDC"
            return tuple(base)

        def other_version_tuple(member: dict[str, object]) -> tuple[str, ...]:
            base = list(real_tuple(member))
            base[6] = "some-other-semantic-version"
            return tuple(base)

        # Production hash equals the recomputation that binds the real semantics.
        self.assertEqual(dataset_hash(real_tuple), result.dataset_content_hash)
        # The semantics are actually bound: dropping them changes the hash.
        self.assertNotEqual(dataset_hash(legacy_tuple), result.dataset_content_hash)
        # ... and it is sensitive to each semantic dimension the phase names.
        self.assertNotEqual(dataset_hash(contracts_tuple), result.dataset_content_hash)
        self.assertNotEqual(dataset_hash(other_asset_tuple), result.dataset_content_hash)
        self.assertNotEqual(dataset_hash(other_version_tuple), result.dataset_content_hash)

        database.close()

    @staticmethod
    def _sealed_members(database: object, dataset_version_id: object) -> list[dict[str, object]]:
        with database.transaction() as connection, connection.cursor() as cursor:  # type: ignore[attr-defined]
            cursor.execute(
                "SELECT n.normalized_observation_id, r.raw_payload_sha256, n.normalized_value, "
                "vs.volume_unit, vs.volume_asset, vs.turnover, vs.turnover_unit, "
                "vs.turnover_asset, vs.semantic_version, vs.source_reference "
                "FROM historical_dataset_members m "
                "JOIN historical_normalized_observations n "
                "  ON n.normalized_observation_id=m.normalized_observation_id "
                "JOIN historical_raw_observations r "
                "  ON r.raw_observation_id=n.raw_observation_id "
                "LEFT JOIN historical_ohlcv_volume_semantics vs "
                "  ON vs.normalized_observation_id=n.normalized_observation_id "
                "WHERE m.dataset_version_id=%s",
                (dataset_version_id,),
            )
            rows = cursor.fetchall()
        members: list[dict[str, object]] = []
        for row in rows:
            members.append(
                {
                    "norm_id": str(row[0]),
                    "raw_sha256": str(row[1]),
                    "normalized_value": row[2],
                    "volume_unit": row[3],
                    "volume_asset": row[4],
                    "turnover": row[5],
                    "turnover_unit": row[6],
                    "turnover_asset": row[7],
                    "semantic_version": row[8],
                    "source_reference": row[9],
                }
            )
        return members


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class LegacyOhlcvVolumeSemanticsPostgresTests(unittest.TestCase):
    """An unauthorized source's OHLCV stays unitless legacy evidence, hash-compatible."""

    database_name: str
    dsn: str

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest(
                "Phase 3B.2 legacy compatibility requires a local or CI disposable PostgreSQL DSN"
            )
        cls.database_name = f"ohlcv_volume_semantics_phase3b2_legacy_{os.getpid()}"
        cls.dsn = _disposable_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')
        _migrate(cls.dsn)

    @classmethod
    def tearDownClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    def test_unauthorized_source_ohlcv_is_unitless_and_hash_compatible(self) -> None:
        from trade_platform.bybit_crypto_provider import BYBIT_V5_SYMBOL_NAMESPACE
        from trade_platform.bybit_instrument_onboarding import (
            captured_btcusdt_snapshot_v1,
            onboard_bybit_btcusdt_perpetual_v1,
        )
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            AssetScope,
            AuthorizedHistoricalSource,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            QualityStatus,
            RawHistoricalObservation,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.tradable_bar_evidence_v2 import (
            BAR_TIMESTAMP_SEMANTICS_MARKER_V1,
            PostgresTradableBarEvidenceReaderV2,
        )

        database = PostgresDatabase(self.dsn)
        onboard_bybit_btcusdt_perpetual_v1(database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT)
        pipeline = PostgresHistoricalMarketDataPipeline(database)

        # A non-Bybit provider with no authorized volume-semantics rule: its
        # OHLCV must never receive Bybit semantics.
        legacy_source = AuthorizedHistoricalSource(
            source_id=uuid4(),
            provider="legacy_generic_md",
            dataset_name="legacy-generic-crypto-ohlcv",
            provider_identifier_namespace=BYBIT_V5_SYMBOL_NAMESPACE,
            provider_terms_version="operator-declared:legacy-fixture:v1",
            authorization_reference="operator-approved legacy fixture source",
            authorized_at=ONBOARDED_AT,
            created_at=ONBOARDED_AT,
            asset_scope=AssetScope.CRYPTO.value,
            authorized_observation_kinds=frozenset({ObservationKind.OHLCV}),
        )
        pipeline.register_source(legacy_source)

        bar_open = START
        bar_close = bar_open + _MINUTE
        ingested_at = bar_close + _MINUTE
        # The raw payload deliberately CARRIES a provider turnover: the unitless
        # readback below proves the consumer reads units from the typed sidecar
        # (absent here), never by parsing this raw payload.
        raw = RawHistoricalObservation(
            source_id=legacy_source.source_id,
            observation_kind=ObservationKind.OHLCV,
            provider_identifier=SYMBOL,
            provider_symbol=SYMBOL,
            exchange=VENUE,
            event_at=bar_open,
            effective_at=bar_close,
            ingested_at=ingested_at,
            adjustment_status=AdjustmentStatus.RAW,
            revision=0,
            provenance_uri="legacy://fixture/ohlcv/BTCUSDT",
            raw_payload={
                "bar_timestamp_semantics": BAR_TIMESTAMP_SEMANTICS_MARKER_V1,
                "interval": "1m",
                "open": "27000.0",
                "high": "27100.0",
                "low": "26900.0",
                "close": "27000.0",
                "volume": VOLUME,
                "provider_turnover": "337500.0",
            },
        )
        (raw_id,) = pipeline.capture_raw([raw])
        normalized = pipeline.normalize(raw_id, NORMALIZATION_VERSION, NOW)
        self.assertIs(normalized.quality_status, QualityStatus.VALIDATED)

        # ---- no sidecar was written for the legacy row -----------------------
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM historical_ohlcv_volume_semantics")
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)

        dataset = pipeline.seal_dataset(
            legacy_source.source_id,
            "legacy-ohlcv-v1",
            NORMALIZATION_VERSION,
            (normalized.normalized_observation_id,),
            NOW,
        )

        # ---- the content hash reproduces the exact pre-3B.2 legacy formula ----
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT n.normalized_observation_id, r.raw_payload_sha256, n.normalized_value "
                "FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r "
                "  ON r.raw_observation_id=n.raw_observation_id "
                "WHERE n.normalized_observation_id=%s",
                (normalized.normalized_observation_id,),
            )
            row = cursor.fetchone()
        legacy_digest = hashlib.sha256()
        legacy_digest.update(
            "|".join((str(row[0]), str(row[1]), "OHLCV", _canonical(row[2]))).encode()
        )
        self.assertEqual(dataset.content_hash, legacy_digest.hexdigest())

        # ---- research readback and tradable-bar reader are unitless ----------
        observations = pipeline.research_query(
            dataset.dataset_version_id, INSTRUMENT_ID, START, END, NOW
        )
        self.assertEqual(len(observations), 1)
        legacy_observation = observations[0]
        self.assertEqual(legacy_observation.normalized_value["volume"], VOLUME)
        self.assertIsNone(legacy_observation.volume_unit)
        self.assertIsNone(legacy_observation.volume_asset)
        self.assertIsNone(legacy_observation.turnover)
        self.assertIsNone(legacy_observation.turnover_unit)
        self.assertIsNone(legacy_observation.turnover_asset)
        self.assertIsNone(legacy_observation.volume_semantic_version)

        reader = PostgresTradableBarEvidenceReaderV2(database)
        series = reader.series(
            dataset_version_id=dataset.dataset_version_id,
            instrument_id=INSTRUMENT_ID,
            interval="1m",
            research_run_at=NOW,
        )
        self.assertEqual(len(series.bars), 1)
        legacy_bar = series.bars[0]
        self.assertEqual(legacy_bar.volume, Decimal(VOLUME))
        self.assertIsNone(legacy_bar.volume_unit)
        self.assertIsNone(legacy_bar.volume_asset)
        self.assertIsNone(legacy_bar.turnover)
        self.assertIsNone(legacy_bar.turnover_unit)
        self.assertIsNone(legacy_bar.turnover_asset)
        self.assertIsNone(legacy_bar.volume_semantic_version)

        database.close()


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ContractsVolumeUnitPostgresTests(unittest.TestCase):
    """Item 4: CONTRACTS round-trips through the typed sidecar without inventing an asset.

    ``volume_asset``/``turnover_asset`` are nullable columns with CHECK
    constraints enforcing the split both ways (migration ``20260916_0048``):
    a ``CONTRACTS`` unit must have a NULL asset, and ``BASE_ASSET``/
    ``QUOTE_ASSET`` must have one. These constraints are exercised directly at
    the database, independent of any resolver rule -- proving the schema
    itself, not merely the Python authority, enforces the model.
    """

    database_name: str
    dsn: str

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest(
                "Phase 3B.2 CONTRACTS representability requires a local or CI disposable PostgreSQL DSN"
            )
        cls.database_name = f"ohlcv_volume_semantics_phase3b2_contracts_{os.getpid()}"
        cls.dsn = _disposable_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')
        _migrate(cls.dsn)

    @classmethod
    def tearDownClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    def _normalized_observation_ids(self, count: int) -> list[object]:
        """Capture and normalize ``count`` distinct legacy OHLCV observations.

        Each sidecar row is 1:1 with a normalized observation, so every test
        case below needs its own fresh, already-VALIDATED observation to
        attach a sidecar row to.
        """
        from trade_platform.bybit_crypto_provider import BYBIT_V5_SYMBOL_NAMESPACE
        from trade_platform.bybit_instrument_onboarding import (
            captured_btcusdt_snapshot_v1,
            onboard_bybit_btcusdt_perpetual_v1,
        )
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            AssetScope,
            AuthorizedHistoricalSource,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            RawHistoricalObservation,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.tradable_bar_evidence_v2 import BAR_TIMESTAMP_SEMANTICS_MARKER_V1

        database = PostgresDatabase(self.dsn)
        onboard_bybit_btcusdt_perpetual_v1(database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT)
        pipeline = PostgresHistoricalMarketDataPipeline(database)
        source = AuthorizedHistoricalSource(
            source_id=uuid4(),
            provider="legacy_generic_md",
            # Unique per call: this helper may be invoked by multiple test
            # methods in this class, and historical_data_sources is UNIQUE on
            # (provider, dataset_name, provider_terms_version).
            dataset_name=f"legacy-generic-crypto-ohlcv-contracts-fixture-{uuid4()}",
            provider_identifier_namespace=BYBIT_V5_SYMBOL_NAMESPACE,
            provider_terms_version="operator-declared:legacy-fixture:v1",
            authorization_reference="operator-approved legacy fixture source",
            authorized_at=ONBOARDED_AT,
            created_at=ONBOARDED_AT,
            asset_scope=AssetScope.CRYPTO.value,
            authorized_observation_kinds=frozenset({ObservationKind.OHLCV}),
        )
        pipeline.register_source(source)

        normalized_ids: list[object] = []
        for index in range(count):
            bar_open = START + index * _MINUTE
            bar_close = bar_open + _MINUTE
            ingested_at = bar_close + _MINUTE
            raw = RawHistoricalObservation(
                source_id=source.source_id,
                observation_kind=ObservationKind.OHLCV,
                provider_identifier=SYMBOL,
                provider_symbol=SYMBOL,
                exchange=VENUE,
                event_at=bar_open,
                effective_at=bar_close,
                ingested_at=ingested_at,
                adjustment_status=AdjustmentStatus.RAW,
                revision=0,
                provenance_uri=f"legacy://fixture/ohlcv/contracts/{index}",
                raw_payload={
                    "bar_timestamp_semantics": BAR_TIMESTAMP_SEMANTICS_MARKER_V1,
                    "interval": "1m",
                    "open": "27000.0",
                    "high": "27100.0",
                    "low": "26900.0",
                    "close": "27000.0",
                    "volume": "1.0",
                },
            )
            (raw_id,) = pipeline.capture_raw([raw])
            normalized = pipeline.normalize(raw_id, NORMALIZATION_VERSION, NOW)
            normalized_ids.append(normalized.normalized_observation_id)
        database.close()
        return normalized_ids

    def test_valid_contracts_semantics_round_trips_with_no_asset(self) -> None:
        from trade_platform.persistence import PostgresDatabase

        (normalized_id,) = self._normalized_observation_ids(1)
        database = PostgresDatabase(self.dsn)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO historical_ohlcv_volume_semantics VALUES "
                "(%s,'CONTRACTS',NULL,%s,'CONTRACTS',NULL,%s,%s)",
                (normalized_id, Decimal("1000"), "fixture-contracts-v1", "fixture:contracts"),
            )
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT volume_unit, volume_asset, turnover_unit, turnover_asset "
                "FROM historical_ohlcv_volume_semantics WHERE normalized_observation_id=%s",
                (normalized_id,),
            )
            row = cursor.fetchone()
        self.assertEqual(row, ("CONTRACTS", None, "CONTRACTS", None))
        database.close()

    def test_contracts_unit_with_asset_rejected_by_database(self) -> None:
        from trade_platform.persistence import PersistenceError, PostgresDatabase

        (normalized_id,) = self._normalized_observation_ids(1)
        database = PostgresDatabase(self.dsn)
        with self.assertRaises(PersistenceError), database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO historical_ohlcv_volume_semantics VALUES "
                "(%s,'CONTRACTS','BTC',%s,'QUOTE_ASSET','USDT',%s,%s)",
                (normalized_id, Decimal("1000"), "fixture-v1", "fixture:contracts"),
            )
        database.close()

    def test_base_asset_unit_with_no_asset_rejected_by_database(self) -> None:
        from trade_platform.persistence import PersistenceError, PostgresDatabase

        (normalized_id,) = self._normalized_observation_ids(1)
        database = PostgresDatabase(self.dsn)
        with self.assertRaises(PersistenceError), database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO historical_ohlcv_volume_semantics VALUES "
                "(%s,'BASE_ASSET',NULL,%s,'QUOTE_ASSET','USDT',%s,%s)",
                (normalized_id, Decimal("1000"), "fixture-v1", "fixture:contracts"),
            )
        database.close()

    def test_quote_asset_unit_with_no_asset_rejected_by_database(self) -> None:
        from trade_platform.persistence import PersistenceError, PostgresDatabase

        (normalized_id,) = self._normalized_observation_ids(1)
        database = PostgresDatabase(self.dsn)
        with self.assertRaises(PersistenceError), database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO historical_ohlcv_volume_semantics VALUES "
                "(%s,'BASE_ASSET','BTC',%s,'QUOTE_ASSET',NULL,%s,%s)",
                (normalized_id, Decimal("1000"), "fixture-v1", "fixture:contracts"),
            )
        database.close()


if __name__ == "__main__":
    unittest.main()
