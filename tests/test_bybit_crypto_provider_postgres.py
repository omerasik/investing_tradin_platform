"""Real PostgreSQL evidence for the Bybit V5 public market-data adapter.

Every price, quantity, symbol mapping, venue record and provider response in
this file is a FIXTURE. Nothing here was retrieved from or verified against
Bybit or any other venue; no real OHLCV, mark price, index price or
open-interest figure is claimed, and no network call is made -- the adapter is
driven entirely by a scripted in-process transport. Instruments are registered
under ``operator_dashboard.RESERVED_TEST_FIXTURE_PREFIX`` (``TESTFIXTURE:``).
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

VENUE = "BYBIT"
SYMBOL = "BTCUSDT"
NAMESPACE = "bybit_v5_symbol"
INSTRUMENT_ID = "TESTFIXTURE:BYBITV5:BTCUSDT:PERP"

REGISTERED_AT = datetime(2026, 5, 1, tzinfo=UTC)
BAR_START = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
BAR_END = datetime(2026, 6, 1, 0, 3, tzinfo=UTC)
OPEN_INTEREST_END = datetime(2026, 6, 1, 0, 15, tzinfo=UTC)
KLINE_RETRIEVED_AT = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
OPEN_INTEREST_RETRIEVED_AT = datetime(2026, 6, 1, 0, 20, tzinfo=UTC)
REJECTED_START = datetime(2026, 6, 1, 0, 20, tzinfo=UTC)
REJECTED_END = datetime(2026, 6, 1, 0, 22, tzinfo=UTC)
REJECTED_RETRIEVED_AT = datetime(2026, 6, 1, 0, 25, tzinfo=UTC)
NORMALIZED_AT = datetime(2026, 6, 1, 0, 30, tzinfo=UTC)
SEALED_AT = datetime(2026, 6, 1, 1, 0, tzinfo=UTC)
DECISION_AT = datetime(2026, 6, 1, 2, 0, tzinfo=UTC)

NORMALIZATION_VERSION = "bybit-v5-md-v1"
VALUE_SCALE = Decimal("1E-12")

TRADE_CLOSES = ("27050.0", "27060.5", "27045.25")
MARK_CLOSES = ("27011.5", "27021.25", "27008.75")
INDEX_CLOSES = ("27000.5", "27010.0", "27002.25")
OPEN_INTEREST_VALUES = ("461134.0", "461200.5", "461050.25")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _milliseconds(value: datetime) -> int:
    return int((value - _EPOCH).total_seconds()) * 1000


def _envelope(result: dict[str, object]) -> str:
    return json.dumps(
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": result,
            "retExtInfo": {},
            "time": _milliseconds(KLINE_RETRIEVED_AT),
        }
    )


def _kline_envelope(rows: list[list[str]]) -> str:
    return _envelope({"category": "linear", "symbol": SYMBOL, "list": rows})


def _open_interest_envelope(rows: list[dict[str, str]]) -> str:
    return _envelope(
        {"category": "linear", "symbol": SYMBOL, "list": rows, "nextPageCursor": ""}
    )


def _trade_row(bar_open: datetime, close: str, *, volume: str = "12.5") -> list[str]:
    return [
        str(_milliseconds(bar_open)),
        "27000.0",
        "27100.0",
        "26900.0",
        close,
        volume,
        "337500.0",
    ]


def _reference_row(bar_open: datetime, close: str) -> list[str]:
    return [str(_milliseconds(bar_open)), "27000.0", "27100.0", "26900.0", close]


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


def _recomputed_dataset_hash(database: object, normalized_ids: tuple[UUID, ...]) -> str:
    from trade_platform.crypto_market_observations import REFERENCE_PRICE_PAYLOAD_TABLE
    from trade_platform.market_observation_payloads import (
        OPEN_INTEREST_CANONICAL_PAYLOAD_IDENTITY,
    )

    placeholders = ",".join(["%s"] * len(normalized_ids))
    with database.transaction() as connection, connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(
            "SELECT n.normalized_observation_id,r.raw_payload_sha256,r.observation_kind,"
            "n.normalized_value,oi.open_interest,oi.unit,oi.observed_at,oi.unit_asset,"
            "rp.price,rp.price_asset,rp.observed_at,rp.methodology_reference "
            "FROM historical_normalized_observations n "
            "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
            "LEFT JOIN open_interest_observations oi "
            "ON oi.normalized_observation_id=n.normalized_observation_id "
            "LEFT JOIN crypto_reference_price_observations rp "
            "ON rp.normalized_observation_id=n.normalized_observation_id "
            f"WHERE n.normalized_observation_id IN ({placeholders})",  # nosec B608 - placeholders only
            normalized_ids,
        )
        rows = cursor.fetchall()
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: str(item[0])):
        kind = str(row[2])
        if kind == "OPEN_INTEREST":
            typed: tuple[str, ...] = (
                OPEN_INTEREST_CANONICAL_PAYLOAD_IDENTITY,
                str(Decimal(str(row[4]))),
                str(row[5]),
                row[6].isoformat(),
                str(row[7]) if row[7] is not None else "",
            )
        elif kind in {"MARK_PRICE", "INDEX_PRICE"}:
            typed = (
                REFERENCE_PRICE_PAYLOAD_TABLE,
                str(Decimal(str(row[8]))),
                str(row[9]),
                row[10].isoformat(),
                str(row[11]) if row[11] is not None else "",
            )
        else:
            typed = ()
        canonical = "|".join(
            (
                str(row[0]),
                str(row[1]),
                kind,
                json.dumps(row[3], sort_keys=True, separators=(",", ":"), allow_nan=False),
                *typed,
            )
        )
        digest.update(canonical.encode())
    return digest.hexdigest()


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class BybitCryptoProviderPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_bybit_adapter_feeds_the_sealed_authority_end_to_end(self) -> None:
        from dataclasses import replace

        from trade_platform.bybit_crypto_provider import (
            BYBIT_PROVIDER_VERSION,
            BYBIT_V5_SYMBOL_NAMESPACE,
            INDEX_PRICE_METHODOLOGY_REFERENCE,
            MARK_PRICE_METHODOLOGY_REFERENCE,
            BybitCryptoHistoricalAdapter,
            BybitUnsupportedObservationKindError,
        )
        from trade_platform.crypto_derivatives_features import (
            PostgresCryptoDerivativesFeatureCalculator,
            crypto_mark_index_basis_definition,
        )
        from trade_platform.crypto_instruments import (
            CryptoInstrumentKind,
            CryptoInstrumentSpecification,
            CryptoSettlementType,
            PostgresCryptoInstrumentAuthority,
            ReferencePriceRequirement,
            SettlementStyle,
        )
        from trade_platform.data_providers import (
            HttpResponse,
            ProviderConfiguration,
            ProviderError,
            ProviderHealthRegistry,
            ProviderOperationalStatus,
        )
        from trade_platform.domain import AssetClass
        from trade_platform.feature_authority import PostgresFeatureAuthority
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            AssetScope,
            AuthorizedHistoricalSource,
            HistoricalDataQualityError,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            QualityStatus,
            normalize_payload,
        )
        from trade_platform.open_interest_features import (
            PostgresOpenInterestFeatureCalculator,
            open_interest_change_definition,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.professional_instruments import (
            IdentifierMapping,
            IdentifierSourceKind,
            InstrumentType,
            LifecycleStatus,
            PostgresProfessionalInstrumentMaster,
            ProfessionalInstrument,
            RepresentationKind,
            SessionType,
        )
        from trade_platform.provider_ingestion import (
            HistoricalIngestionRequest,
            ingest_raw_historical_pages,
        )
        from trade_platform.tradable_bar_evidence_v2 import (
            BAR_TIMESTAMP_SEMANTICS_MARKER_V1,
            PostgresTradableBarEvidenceReaderV2,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        database = PostgresDatabase(dsn)
        master = PostgresProfessionalInstrumentMaster(database)
        crypto = PostgresCryptoInstrumentAuthority(database)
        pipeline = PostgresHistoricalMarketDataPipeline(database)

        # ---- fixture instrument master + 3H.2 crypto semantics ----------------
        master.register(
            ProfessionalInstrument(
                instrument_id=INSTRUMENT_ID,
                asset_class=AssetClass.CRYPTO,
                instrument_type=InstrumentType.CRYPTO_PERPETUAL,
                exchange_name=VENUE,
                venue=VENUE,
                mic=None,
                canonical_symbol="TESTFIXBYBITV5BTCUSDTP",
                listing_date=date(2026, 1, 2),
                base_currency="BTC",
                quote_currency="USDT",
                settlement_currency="USDT",
                contract_multiplier=Decimal(1),
                contract_size=Decimal(1),
                tick_size=Decimal("0.1"),
                lot_size=Decimal("0.001"),
                price_precision=2,
                quantity_precision=3,
                trading_timezone="UTC",
                market_session_type=SessionType.CRYPTO_24X7,
                representation_kind=RepresentationKind.PERPETUAL,
                registered_at=REGISTERED_AT,
                lifecycle_status=LifecycleStatus.ACTIVE,
            )
        )
        master.add_identifier_mapping(
            IdentifierMapping(
                instrument_id=INSTRUMENT_ID,
                source_kind=IdentifierSourceKind.PROVIDER,
                namespace=BYBIT_V5_SYMBOL_NAMESPACE,
                value=SYMBOL,
                valid_from=REGISTERED_AT,
                valid_until=None,
                ingested_at=REGISTERED_AT,
                source_reference="fixture:bybit-v5-symbol",
            )
        )
        self.assertEqual(BYBIT_V5_SYMBOL_NAMESPACE, NAMESPACE)
        crypto.specify_instrument(
            CryptoInstrumentSpecification(
                instrument_id=INSTRUMENT_ID,
                venue=VENUE,
                kind=CryptoInstrumentKind.PERPETUAL,
                base_asset="BTC",
                quote_asset="USDT",
                settlement_asset="USDT",
                settlement_style=SettlementStyle.LINEAR,
                settlement_type=CryptoSettlementType.CASH_SETTLED,
                contract_multiplier=Decimal(1),
                contract_size=Decimal(1),
                reference_price_requirement=ReferencePriceRequirement.MARK_AND_INDEX,
                index_reference="TESTFIX_BYBITV5_BTCUSDT_INDEX",
                registered_at=REGISTERED_AT,
                source_reference="fixture:bybit-v5-specification",
            )
        )

        # ---- one authorized source for all four supported kinds ---------------
        source = AuthorizedHistoricalSource(
            provider="bybit",
            dataset_name="bybit-v5-linear-btcusdt",
            provider_identifier_namespace=BYBIT_V5_SYMBOL_NAMESPACE,
            provider_terms_version="bybit-v5-public-market-terms-v1",
            authorization_reference="fixture://authorization/bybit-v5-public-market",
            authorized_at=REGISTERED_AT,
            created_at=REGISTERED_AT,
            asset_scope=AssetScope.CRYPTO.value,
            authorized_observation_kinds=frozenset(
                {
                    ObservationKind.OHLCV,
                    ObservationKind.MARK_PRICE,
                    ObservationKind.INDEX_PRICE,
                    ObservationKind.OPEN_INTEREST,
                }
            ),
        )
        source.validate()
        self.assertEqual(
            sorted(kind.value for kind in source.resolved_capabilities()),
            ["INDEX_PRICE", "MARK_PRICE", "OHLCV", "OPEN_INTEREST"],
        )
        pipeline.register_source(source)

        # ---- scripted provider responses (no socket is ever opened) -----------
        bar_opens = [BAR_START + timedelta(minutes=offset) for offset in range(3)]
        open_interest_instants = [BAR_START + timedelta(minutes=5 * offset) for offset in range(3)]
        transport = RoutingTransport(
            {
                "/v5/market/mark-price-kline": [
                    HttpResponse(
                        200,
                        _kline_envelope(
                            [
                                _reference_row(bar_open, close)
                                for bar_open, close in reversed(
                                    list(zip(bar_opens, MARK_CLOSES, strict=True))
                                )
                            ]
                        ),
                    )
                ],
                "/v5/market/index-price-kline": [
                    HttpResponse(
                        200,
                        _kline_envelope(
                            [
                                _reference_row(bar_open, close)
                                for bar_open, close in reversed(
                                    list(zip(bar_opens, INDEX_CLOSES, strict=True))
                                )
                            ]
                        ),
                    )
                ],
                "/v5/market/open-interest": [
                    HttpResponse(
                        200,
                        _open_interest_envelope(
                            [
                                {
                                    "openInterest": value,
                                    "timestamp": str(_milliseconds(instant)),
                                }
                                for instant, value in reversed(
                                    list(
                                        zip(
                                            open_interest_instants,
                                            OPEN_INTEREST_VALUES,
                                            strict=True,
                                        )
                                    )
                                )
                            ]
                        ),
                    )
                ],
                "/v5/market/kline": [
                    HttpResponse(
                        200,
                        _kline_envelope(
                            [
                                _trade_row(bar_open, close)
                                for bar_open, close in reversed(
                                    list(zip(bar_opens, TRADE_CLOSES, strict=True))
                                )
                            ]
                        ),
                    )
                ],
            }
        )
        configuration = ProviderConfiguration(
            provider="bybit",
            base_url="https://api.bybit.com",
            terms_accepted=True,
            secret_reference=None,
        )
        kline_adapter = BybitCryptoHistoricalAdapter(
            configuration,
            transport=transport,  # type: ignore[arg-type]
            now=lambda: KLINE_RETRIEVED_AT,
            sleep=lambda _seconds: None,
        )
        open_interest_adapter = BybitCryptoHistoricalAdapter(
            configuration,
            transport=transport,  # type: ignore[arg-type]
            now=lambda: OPEN_INTEREST_RETRIEVED_AT,
            sleep=lambda _seconds: None,
        )

        # A funding request never reaches the transport at all.
        with self.assertRaisesRegex(
            BybitUnsupportedObservationKindError,
            "bybit_unsupported_observation_kind:FUNDING_RATE_REALIZED",
        ):
            kline_adapter.fetch_raw_page(
                source.source_id,
                self._scope(ObservationKind.FUNDING_RATE_REALIZED, "1", BAR_START, BAR_END),
                None,
            )
        self.assertEqual(transport.urls, [])

        # ---- real adapter -> ingest_raw_historical_pages -> capture_raw -------
        health = ProviderHealthRegistry(lambda: KLINE_RETRIEVED_AT)
        captured: dict[ObservationKind, tuple[UUID, ...]] = {}
        for kind, interval, window_end, adapter, checked_at in (
            (ObservationKind.OHLCV, "1", BAR_END, kline_adapter, KLINE_RETRIEVED_AT),
            (ObservationKind.MARK_PRICE, "1", BAR_END, kline_adapter, KLINE_RETRIEVED_AT),
            (ObservationKind.INDEX_PRICE, "1", BAR_END, kline_adapter, KLINE_RETRIEVED_AT),
            (
                ObservationKind.OPEN_INTEREST,
                "5min",
                OPEN_INTEREST_END,
                open_interest_adapter,
                OPEN_INTEREST_RETRIEVED_AT,
            ),
        ):
            outcome = ingest_raw_historical_pages(
                adapter,
                HistoricalIngestionRequest(
                    source.source_id, self._scope(kind, interval, BAR_START, window_end)
                ),
                pipeline,
                health,
                now=lambda instant=checked_at: instant,  # type: ignore[misc]
            )
            self.assertEqual(outcome.state, ProviderOperationalStatus.HEALTHY, kind.value)
            self.assertEqual(outcome.checkpoint.adapter_name, "bybit")
            self.assertEqual(outcome.checkpoint.provider_version, BYBIT_PROVIDER_VERSION)
            self.assertEqual(len(outcome.captured_raw_ids), 3, kind.value)
            captured[kind] = outcome.captured_raw_ids

        self.assertEqual(len(transport.urls), 4)
        self.assertTrue(
            all(url.startswith("https://api.bybit.com/v5/market/") for url in transport.urls)
        )

        # ---- every raw row is Bybit-attributed, with no bypass path -----------
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT observation_kind,provider_identifier,provider_symbol,exchange,"
                "provenance_uri,adjustment_status,revision "
                "FROM historical_raw_observations WHERE source_id=%s",
                (source.source_id,),
            )
            raw_rows = cursor.fetchall()
        self.assertEqual(len(raw_rows), 12)
        for row in raw_rows:
            self.assertEqual((str(row[1]), str(row[2]), str(row[3])), (SYMBOL, SYMBOL, VENUE))
            self.assertTrue(str(row[4]).startswith("bybit://v5/"))
            self.assertEqual(str(row[5]), AdjustmentStatus.RAW.value)
            self.assertEqual(int(str(row[6])), 0)
        self.assertEqual(
            sorted({str(row[4]) for row in raw_rows}),
            [
                "bybit://v5/index-kline/linear/BTCUSDT",
                "bybit://v5/mark-kline/linear/BTCUSDT",
                "bybit://v5/open-interest/linear/BTCUSDT",
                "bybit://v5/trade-kline/linear/BTCUSDT",
            ],
        )

        # ---- normalize: every row resolves to the same canonical instrument ---
        normalized: dict[ObservationKind, list[object]] = {}
        for kind, raw_ids in captured.items():
            normalized[kind] = [
                pipeline.normalize(raw_id, NORMALIZATION_VERSION, NORMALIZED_AT)
                for raw_id in raw_ids
            ]
            for item in normalized[kind]:
                self.assertEqual(item.instrument_id, INSTRUMENT_ID)  # type: ignore[attr-defined]
                self.assertEqual(item.quality_status, QualityStatus.VALIDATED)  # type: ignore[attr-defined]

        member_ids = tuple(
            item.normalized_observation_id  # type: ignore[attr-defined]
            for kind in (
                ObservationKind.OHLCV,
                ObservationKind.MARK_PRICE,
                ObservationKind.INDEX_PRICE,
                ObservationKind.OPEN_INTEREST,
            )
            for item in normalized[kind]
        )
        self.assertEqual(len(member_ids), 12)

        # ---- exactly one sealed dataset version carrying all four kinds -------
        dataset = pipeline.seal_dataset(
            source.source_id,
            "bybit-v5-linear-btcusdt-phase1",
            NORMALIZATION_VERSION,
            member_ids,
            SEALED_AT,
        )
        self.assertEqual(len(dataset.content_hash), 64)
        self.assertEqual(dataset.content_hash, _recomputed_dataset_hash(database, member_ids))
        self.assertEqual(dataset.valid_from, BAR_START)
        self.assertEqual(dataset.valid_until, open_interest_instants[-1])

        # ---- research_query returns exact typed values ------------------------
        observations = pipeline.research_query(
            dataset.dataset_version_id,
            INSTRUMENT_ID,
            BAR_START,
            open_interest_instants[-1],
            DECISION_AT,
        )
        by_kind: dict[ObservationKind, list[object]] = {}
        for observation in observations:
            by_kind.setdefault(observation.observation_kind, []).append(observation)
        self.assertEqual(
            sorted(kind.value for kind in by_kind),
            ["INDEX_PRICE", "MARK_PRICE", "OHLCV", "OPEN_INTEREST"],
        )
        self.assertEqual(len(observations), 12)
        for observation in observations:
            self.assertEqual(observation.provider, "bybit")  # type: ignore[attr-defined]
            self.assertEqual(observation.provider_identifier, SYMBOL)  # type: ignore[attr-defined]
            self.assertEqual(observation.exchange, VENUE)  # type: ignore[attr-defined]

        trade_bars = by_kind[ObservationKind.OHLCV]
        self.assertEqual(
            [item.event_at for item in trade_bars],  # type: ignore[attr-defined]
            bar_opens,
        )
        self.assertEqual(
            [
                Decimal(str(item.normalized_value["close"]))  # type: ignore[attr-defined]
                for item in trade_bars
            ],
            [Decimal(value) for value in TRADE_CLOSES],
        )
        self.assertEqual(
            {item.normalized_value["interval"] for item in trade_bars},  # type: ignore[attr-defined]
            {"1m"},
        )
        self.assertEqual(
            [item.effective_at for item in trade_bars],  # type: ignore[attr-defined]
            [bar_open + timedelta(minutes=1) for bar_open in bar_opens],
        )

        bar_closes = [bar_open + timedelta(minutes=1) for bar_open in bar_opens]
        for kind, closes, methodology in (
            (ObservationKind.MARK_PRICE, MARK_CLOSES, MARK_PRICE_METHODOLOGY_REFERENCE),
            (ObservationKind.INDEX_PRICE, INDEX_CLOSES, INDEX_PRICE_METHODOLOGY_REFERENCE),
        ):
            items = by_kind[kind]
            self.assertEqual([item.event_at for item in items], bar_closes)  # type: ignore[attr-defined]
            self.assertEqual([item.effective_at for item in items], bar_closes)  # type: ignore[attr-defined]
            for item, close, bar_close_at in zip(items, closes, bar_closes, strict=True):
                value = item.normalized_value  # type: ignore[attr-defined]
                self.assertEqual(Decimal(str(value["price"])), Decimal(close))
                self.assertEqual(value["price_asset"], "USDT")
                self.assertEqual(value["observed_at"], bar_close_at.isoformat())
                self.assertEqual(value["methodology_reference"], methodology)

        # A mark price is never an index price and neither is the trade close.
        self.assertNotEqual(
            by_kind[ObservationKind.MARK_PRICE][0].normalized_value["price"],  # type: ignore[attr-defined]
            by_kind[ObservationKind.INDEX_PRICE][0].normalized_value["price"],  # type: ignore[attr-defined]
        )
        self.assertNotIn(
            Decimal(str(by_kind[ObservationKind.MARK_PRICE][0].normalized_value["price"])),  # type: ignore[attr-defined]
            {Decimal(value) for value in TRADE_CLOSES},
        )

        open_interest_rows = by_kind[ObservationKind.OPEN_INTEREST]
        self.assertEqual(
            [item.event_at for item in open_interest_rows],  # type: ignore[attr-defined]
            open_interest_instants,
        )
        for item, instant, value in zip(
            open_interest_rows, open_interest_instants, OPEN_INTEREST_VALUES, strict=True
        ):
            projected = item.normalized_value  # type: ignore[attr-defined]
            self.assertEqual(Decimal(str(projected["open_interest"])), Decimal(value))
            self.assertEqual(projected["unit"], "BASE_ASSET")
            self.assertEqual(projected["unit_asset"], "BTC")
            self.assertEqual(projected["observed_at"], instant.isoformat())

        # ---- 3J.2b.1 tradable-bar reader over the sealed 1m trade bars --------
        reader = PostgresTradableBarEvidenceReaderV2(database)
        series = reader.series(
            dataset_version_id=dataset.dataset_version_id,
            instrument_id=INSTRUMENT_ID,
            interval="1m",
            research_run_at=DECISION_AT,
        )
        self.assertEqual([bar.bar_open_at for bar in series.bars], bar_opens)
        self.assertEqual([bar.bar_close_at for bar in series.bars], bar_closes)
        self.assertEqual(
            [bar.close for bar in series.bars], [Decimal(value) for value in TRADE_CLOSES]
        )
        self.assertEqual({bar.dataset_content_hash for bar in series.bars}, {dataset.content_hash})
        self.assertEqual(
            {bar.provenance_uri for bar in series.bars},
            {"bybit://v5/trade-kline/linear/BTCUSDT"},
        )
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT raw_payload->>'bar_timestamp_semantics' "
                "FROM historical_raw_observations "
                "WHERE source_id=%s AND observation_kind='OHLCV'",
                (source.source_id,),
            )
            markers = {str(row[0]) for row in cursor.fetchall()}
        self.assertEqual(markers, {BAR_TIMESTAMP_SEMANTICS_MARKER_V1})

        # ---- 3J.1c mark/index basis on the SAME dataset_version_id ------------
        feature_authority = PostgresFeatureAuthority(database)
        basis_definition = replace(
            crypto_mark_index_basis_definition(REGISTERED_AT),
            semantic_version="1.0.0-bybitv5",
        )
        feature_authority.register(basis_definition)
        basis = PostgresCryptoDerivativesFeatureCalculator(database).materialize_crypto_mark_index_basis(
            feature_id=basis_definition.feature_id,
            instrument_id=INSTRUMENT_ID,
            dataset_version_id=dataset.dataset_version_id,
            event_at=bar_closes[0],
            decision_at=DECISION_AT,
        )
        self.assertIsNotNone(basis)
        assert basis is not None
        self.assertEqual(basis.dataset_version, str(dataset.dataset_version_id))
        self.assertEqual(
            basis.value,
            (
                (Decimal(MARK_CLOSES[0]) - Decimal(INDEX_CLOSES[0])) / Decimal(INDEX_CLOSES[0])
            ).quantize(VALUE_SCALE),
        )

        # ---- 3J.1b open-interest change on the SAME dataset_version_id --------
        change_definition = replace(
            open_interest_change_definition(REGISTERED_AT),
            name="bybit_v5_fixture_oi_change",
            semantic_version="1.0.0-bybitv5",
        )
        self.assertEqual(
            change_definition.calculation_version, open_interest_change_definition(
                REGISTERED_AT
            ).calculation_version,
        )
        feature_authority.register(change_definition)
        open_interest_calculator = PostgresOpenInterestFeatureCalculator(database)
        change = open_interest_calculator.materialize_open_interest_change(
            feature_id=change_definition.feature_id,
            instrument_id=INSTRUMENT_ID,
            dataset_version_id=dataset.dataset_version_id,
            event_at=open_interest_instants[1],
            decision_at=DECISION_AT,
        )
        self.assertIsNotNone(change)
        assert change is not None
        self.assertEqual(
            change.value,
            (Decimal(OPEN_INTEREST_VALUES[1]) - Decimal(OPEN_INTEREST_VALUES[0])).quantize(
                VALUE_SCALE
            ),
        )
        self.assertIsNone(
            open_interest_calculator.materialize_open_interest_change(
                feature_id=change_definition.feature_id,
                instrument_id=INSTRUMENT_ID,
                dataset_version_id=dataset.dataset_version_id,
                event_at=open_interest_instants[0],
                decision_at=DECISION_AT,
            )
        )

        # ---- no funding evidence exists anywhere for this source --------------
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM crypto_funding_observations f "
                "JOIN historical_normalized_observations n "
                "ON n.normalized_observation_id=f.normalized_observation_id "
                "JOIN historical_raw_observations r "
                "ON r.raw_observation_id=n.raw_observation_id WHERE r.source_id=%s",
                (source.source_id,),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_raw_observations "
                "WHERE source_id=%s AND observation_kind LIKE 'FUNDING%%'",
                (source.source_id,),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)
            cursor.execute(
                "SELECT observation_kind FROM historical_source_capabilities "
                "WHERE source_id=%s ORDER BY observation_kind",
                (source.source_id,),
            )
            self.assertEqual(
                [str(row[0]) for row in cursor.fetchall()],
                ["INDEX_PRICE", "MARK_PRICE", "OHLCV", "OPEN_INTEREST"],
            )

        # ---- Data Health: the pipeline owns row-level validation --------------
        rejected_adapter = BybitCryptoHistoricalAdapter(
            configuration,
            transport=RoutingTransport(  # type: ignore[arg-type]
                {
                    "/v5/market/mark-price-kline": [
                        HttpResponse(
                            200, _kline_envelope([_reference_row(REJECTED_START, "0")])
                        )
                    ],
                    "/v5/market/open-interest": [
                        HttpResponse(
                            200,
                            _open_interest_envelope(
                                [
                                    {
                                        "openInterest": "-1",
                                        "timestamp": str(_milliseconds(REJECTED_START)),
                                    }
                                ]
                            ),
                        )
                    ],
                }
            ),
            now=lambda: REJECTED_RETRIEVED_AT,
            sleep=lambda _seconds: None,
        )
        for kind, interval, expected_issue in (
            (ObservationKind.MARK_PRICE, "1", "non_positive_reference_price"),
            (ObservationKind.OPEN_INTEREST, "5min", "negative_open_interest"),
        ):
            page = rejected_adapter.fetch_raw_page(
                source.source_id,
                self._scope(kind, interval, REJECTED_START, REJECTED_END),
                None,
            )
            (rejected_raw_id,) = pipeline.capture_raw(list(page.records))
            with self.subTest(kind=kind), self.assertRaisesRegex(
                HistoricalDataQualityError, f"typed_observation_rejected:{expected_issue}"
            ):
                pipeline.normalize(rejected_raw_id, NORMALIZATION_VERSION, NORMALIZED_AT)

        malformed_bar = dict(trade_bars[0].normalized_value)  # type: ignore[attr-defined]
        malformed_bar["close"] = "0"
        malformed_bar["volume"] = "-5"
        _, ohlcv_issues = normalize_payload(ObservationKind.OHLCV, malformed_bar)
        self.assertIn("non_positive_price", ohlcv_issues)
        self.assertIn("negative_volume", ohlcv_issues)

        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r "
                "ON r.raw_observation_id=n.raw_observation_id "
                "WHERE r.source_id=%s AND n.quality_status<>'VALIDATED'",
                (source.source_id,),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)

        # Provider-shape corruption never reaches capture at all.
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_raw_observations WHERE source_id=%s",
                (source.source_id,),
            )
            before = int(str(cursor.fetchone()[0]))
        corrupt_adapter = BybitCryptoHistoricalAdapter(
            configuration,
            transport=RoutingTransport(  # type: ignore[arg-type]
                {
                    "/v5/market/kline": [
                        HttpResponse(200, _kline_envelope([["1780000000000", "1", "2"]]))
                    ]
                }
            ),
            now=lambda: REJECTED_RETRIEVED_AT,
            sleep=lambda _seconds: None,
        )
        with self.assertRaisesRegex(ProviderError, "bybit_unexpected_kline_row_shape"):
            corrupt_adapter.fetch_raw_page(
                source.source_id,
                self._scope(ObservationKind.OHLCV, "1", REJECTED_START, REJECTED_END),
                None,
            )
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_raw_observations WHERE source_id=%s",
                (source.source_id,),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), before)

        # ---- durability across a fresh connection -----------------------------
        restarted = PostgresHistoricalMarketDataPipeline(PostgresDatabase(dsn))
        self.assertEqual(
            len(
                restarted.research_query(
                    dataset.dataset_version_id,
                    INSTRUMENT_ID,
                    BAR_START,
                    open_interest_instants[-1],
                    DECISION_AT,
                )
            ),
            12,
        )

    @staticmethod
    def _scope(kind: object, interval: str, start: datetime, end: datetime) -> dict[str, object]:
        return {
            "observation_kind": kind.value,  # type: ignore[attr-defined]
            "category": "linear",
            "symbol": SYMBOL,
            "interval": interval,
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "settlement_asset": "USDT",
            "start": start.isoformat(),
            "end": end.isoformat(),
        }


if __name__ == "__main__":
    unittest.main()
