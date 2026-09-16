"""End-to-end PostgreSQL evidence for the Phase 3B.1 acquisition service.

Every price, quantity and provider response here is a synthetic FIXTURE served
by a scripted in-process transport; nothing is retrieved from or verified
against Bybit, and no socket is opened. The real canonical
``CRYPTO:BYBIT:BTCUSDT:PERP`` is onboarded through the existing onboarding, then
one :meth:`HistoricalAcquisitionService.acquire` call drives the full pipeline
-- provider ingestion, checkpoints, normalization, coverage validation, one
sealed dataset and the canonical feature materialization -- proving the Phase 3A
30/30/30/6 shape end-to-end (96 raw, 96 validated, 0 rejected, one sealed
dataset, 30 tradable bars, 30 mark/index basis features, 5
open-interest-change features).

The test owns a DISPOSABLE database beneath the configured local/CI PostgreSQL
instance (the ``test_module1b_demo_acceptance`` pattern). Onboarding the real
instrument requires timestamps at or after the frozen capture instant, which
would otherwise win shared "latest record" lookups and collide with the
database-wide feature-definition-singleton invariants other integration tests
assert; a private database sidesteps both, so the service's *canonical* feature
definitions are exercised verbatim.
"""

from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

VENUE = "BYBIT"
SYMBOL = "BTCUSDT"
INSTRUMENT_ID = "CRYPTO:BYBIT:BTCUSDT:PERP"

# Every timestamp is at or after the frozen onboarding capture instant
# (2026-09-14T21:44:22Z); a real instrument cannot be onboarded before the
# evidence it derives from was retrieved.
ONBOARDED_AT = datetime(2026, 9, 15, tzinfo=UTC)
START = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
END = datetime(2026, 9, 16, 0, 30, tzinfo=UTC)
NOW = datetime(2026, 9, 16, 1, 0, tzinfo=UTC)  # after the window closes
NORMALIZATION_VERSION = "bybit-v5-md-phase3b1"
DATASET_VERSION = "bybit-v5-linear-btcusdt-phase3b1-acquisition"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)
_FIVE = timedelta(minutes=5)


def _ms(value: datetime) -> int:
    return int((value - _EPOCH).total_seconds()) * 1000


def _envelope(result: dict[str, object]) -> str:
    return json.dumps(
        {"retCode": 0, "retMsg": "OK", "result": result, "retExtInfo": {}, "time": _ms(NOW)}
    )


def _kline_envelope(rows: list[list[str]]) -> str:
    return _envelope({"category": "linear", "symbol": SYMBOL, "list": rows})


def _open_interest_envelope(rows: list[dict[str, str]]) -> str:
    return _envelope(
        {"category": "linear", "symbol": SYMBOL, "list": rows, "nextPageCursor": ""}
    )


def _trade_row(bar_open: datetime, close: str) -> list[str]:
    return [str(_ms(bar_open)), "27000.0", "27100.0", "26900.0", close, "12.5", "337500.0"]


def _reference_row(bar_open: datetime, close: str) -> list[str]:
    return [str(_ms(bar_open)), "27000.0", "27100.0", "26900.0", close]


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


def _disposable_dsn(source_dsn: str, database_name: str) -> str:
    parsed = urlparse(source_dsn)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class HistoricalAcquisitionPostgresTests(unittest.TestCase):
    database_name: str
    dsn: str

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest(
                "Phase 3B.1 acquisition requires a local or CI disposable PostgreSQL DSN"
            )
        cls.database_name = f"historical_acquisition_phase3b1_{os.getpid()}"
        cls.dsn = _disposable_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')

        from alembic import command
        from alembic.config import Config

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option(
            "sqlalchemy.url", cls.dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        old_dsn = os.environ.get("POSTGRES_TEST_DSN")
        try:
            os.environ["POSTGRES_TEST_DSN"] = cls.dsn
            command.upgrade(config, "head")
        finally:
            if old_dsn is not None:
                os.environ["POSTGRES_TEST_DSN"] = old_dsn

    @classmethod
    def tearDownClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    def test_operator_acquisition_end_to_end(self) -> None:
        from trade_platform.bybit_crypto_provider import BybitCryptoHistoricalAdapter
        from trade_platform.bybit_instrument_onboarding import (
            captured_btcusdt_snapshot_v1,
            onboard_bybit_btcusdt_perpetual_v1,
        )
        from trade_platform.crypto_derivatives_features import CRYPTO_MARK_INDEX_BASIS
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
        from trade_platform.open_interest_features import OPEN_INTEREST_CHANGE
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.tradable_bar_evidence_v2 import (
            PostgresTradableBarEvidenceReaderV2,
        )

        database = PostgresDatabase(self.dsn)

        # ---- real canonical BTCUSDT onboarding --------------------------------
        onboarding = onboard_bybit_btcusdt_perpetual_v1(
            database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )
        self.assertEqual(onboarding.instrument_id, INSTRUMENT_ID)
        source_id = onboarding.source_id

        # ---- scripted provider responses (no socket is ever opened) -----------
        bar_opens = [START + index * _MINUTE for index in range(30)]
        oi_instants = [START + index * _FIVE for index in range(6)]
        trade_closes = [f"{27000 + index}.0" for index in range(30)]
        mark_closes = [f"{27010 + index}.0" for index in range(30)]
        index_closes = [f"{27005 + index}.0" for index in range(30)]
        oi_values = [f"{461000 + index * 10}.0" for index in range(6)]

        transport = RoutingTransport(
            {
                "/v5/market/kline": [
                    HttpResponse(
                        200,
                        _kline_envelope(
                            [
                                _trade_row(bar_open, close)
                                for bar_open, close in reversed(
                                    list(zip(bar_opens, trade_closes, strict=True))
                                )
                            ]
                        ),
                    )
                ],
                "/v5/market/mark-price-kline": [
                    HttpResponse(
                        200,
                        _kline_envelope(
                            [
                                _reference_row(bar_open, close)
                                for bar_open, close in reversed(
                                    list(zip(bar_opens, mark_closes, strict=True))
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
                                    list(zip(bar_opens, index_closes, strict=True))
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
                                {"openInterest": value, "timestamp": str(_ms(instant))}
                                for instant, value in reversed(
                                    list(zip(oi_instants, oi_values, strict=True))
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
            observation_kinds=frozenset(
                {
                    ObservationKind.OHLCV,
                    ObservationKind.MARK_PRICE,
                    ObservationKind.INDEX_PRICE,
                    ObservationKind.OPEN_INTEREST,
                }
            ),
            normalization_version=NORMALIZATION_VERSION,
            dataset_version=DATASET_VERSION,
            maximum_pages_per_kind=8,
            materialize_features=True,
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

        # ---- the acquisition result --------------------------------------------
        self.assertEqual(result.status, AcquisitionStatus.SUCCEEDED, result.failure_code)
        self.assertFalse(result.already_completed)
        self.assertIsNotNone(result.dataset_version_id)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(
            result.raw_counts,
            {
                ObservationKind.OHLCV: 30,
                ObservationKind.MARK_PRICE: 30,
                ObservationKind.INDEX_PRICE: 30,
                ObservationKind.OPEN_INTEREST: 6,
            },
        )
        self.assertEqual(sum(result.raw_counts.values()), 96)
        self.assertEqual(sum(result.normalized_counts.values()), 96)
        self.assertIsNotNone(result.feature_counts)
        assert result.feature_counts is not None
        self.assertEqual(result.feature_counts.crypto_mark_index_basis, 30)
        self.assertEqual(result.feature_counts.open_interest_change, 5)
        self.assertEqual(len(result.checkpoints), 4)
        dataset_version_id = result.dataset_version_id
        assert dataset_version_id is not None

        # ---- raw / normalized / dataset persisted evidence ---------------------
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_raw_observations WHERE source_id=%s",
                (source_id,),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 96)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations "
                "WHERE quality_status='VALIDATED'"
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 96)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations "
                "WHERE quality_status<>'VALIDATED'"
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_dataset_versions WHERE status='SEALED'"
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 1)

        # ---- research_query returns every acquired observation ------------------
        pipeline = PostgresHistoricalMarketDataPipeline(database)
        observations = pipeline.research_query(
            dataset_version_id, INSTRUMENT_ID, START, END, NOW
        )
        self.assertEqual(len(observations), 96)

        # ---- tradable-bar reader over the sealed 1m trade bars ------------------
        reader = PostgresTradableBarEvidenceReaderV2(database)
        series = reader.series(
            dataset_version_id=dataset_version_id,
            instrument_id=INSTRUMENT_ID,
            interval="1m",
            research_run_at=NOW,
        )
        self.assertEqual(len(series.bars), 30)
        self.assertEqual([bar.bar_open_at for bar in series.bars], bar_opens)
        self.assertEqual(
            {bar.dataset_content_hash for bar in series.bars},
            {result.dataset_content_hash},
        )

        # ---- both canonical feature families share the one sealed dataset ------
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT d.name, COUNT(*), COUNT(DISTINCT m.dataset_version) "
                "FROM feature_materializations m "
                "JOIN feature_definition_versions d ON d.feature_id=m.feature_id "
                "GROUP BY d.name ORDER BY d.name"
            )
            feature_rows = {
                str(row[0]): (int(row[1]), int(row[2])) for row in cursor.fetchall()
            }
        self.assertEqual(feature_rows[CRYPTO_MARK_INDEX_BASIS], (30, 1))
        self.assertEqual(feature_rows[OPEN_INTEREST_CHANGE], (5, 1))
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT dataset_version FROM feature_materializations")
            dataset_versions = {str(row[0]) for row in cursor.fetchall()}
        self.assertEqual(dataset_versions, {str(dataset_version_id)})

        # ---- funding was never acquired for this source ------------------------
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_raw_observations "
                "WHERE observation_kind LIKE 'FUNDING%%'"
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)

        database.close()


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class HistoricalAcquisitionRetryPostgresTests(unittest.TestCase):
    """Real-PostgreSQL evidence that a retry after COVERAGE_FAILED is restart-safe.

    ``historical_normalized_observations.raw_observation_id`` is UNIQUE. A first
    attempt that captures and normalizes every kind, then fails at the coverage
    boundary because the provider's OHLCV page was one bar short, durably
    persists 95 raw rows and 95 normalized rows without sealing anything. An
    identical retry -- for which the provider now returns the corrected,
    complete OHLCV page -- must reuse those 95 already-normalized rows (never
    calling ``normalize()`` on them again, which would raise on the UNIQUE
    constraint), normalize only the one newly-captured bar, and seal exactly
    one dataset containing all 96 members.

    Owns its own disposable database, independent of
    :class:`HistoricalAcquisitionPostgresTests`, so this test's extra raw/
    normalized rows for the SAME real source never affect that class's
    unscoped row-count assertions.
    """

    database_name: str
    dsn: str

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest(
                "Phase 3B.1 acquisition retry requires a local or CI disposable PostgreSQL DSN"
            )
        cls.database_name = f"historical_acquisition_phase3b1_retry_{os.getpid()}"
        cls.dsn = _disposable_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')

        from alembic import command
        from alembic.config import Config

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option(
            "sqlalchemy.url", cls.dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        old_dsn = os.environ.get("POSTGRES_TEST_DSN")
        try:
            os.environ["POSTGRES_TEST_DSN"] = cls.dsn
            command.upgrade(config, "head")
        finally:
            if old_dsn is not None:
                os.environ["POSTGRES_TEST_DSN"] = old_dsn

    @classmethod
    def tearDownClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    def test_retry_after_coverage_failure_is_restart_safe(self) -> None:
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
        from trade_platform.historical_market_data import ObservationKind
        from trade_platform.persistence import PostgresDatabase

        database = PostgresDatabase(self.dsn)

        onboarding = onboard_bybit_btcusdt_perpetual_v1(
            database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )
        source_id = onboarding.source_id

        bar_opens = [START + index * _MINUTE for index in range(30)]
        oi_instants = [START + index * _FIVE for index in range(6)]
        trade_closes = [f"{27000 + index}.0" for index in range(30)]
        mark_closes = [f"{27010 + index}.0" for index in range(30)]
        index_closes = [f"{27005 + index}.0" for index in range(30)]
        oi_values = [f"{461000 + index * 10}.0" for index in range(6)]

        # First attempt's OHLCV page is missing its last (30th) bar; every
        # other kind's page is already fully complete on the first attempt.
        incomplete_kline_body = _kline_envelope(
            [
                _trade_row(bar_open, close)
                for bar_open, close in reversed(
                    list(zip(bar_opens[:-1], trade_closes[:-1], strict=True))
                )
            ]
        )
        complete_kline_body = _kline_envelope(
            [
                _trade_row(bar_open, close)
                for bar_open, close in reversed(list(zip(bar_opens, trade_closes, strict=True)))
            ]
        )
        mark_body = _kline_envelope(
            [
                _reference_row(bar_open, close)
                for bar_open, close in reversed(list(zip(bar_opens, mark_closes, strict=True)))
            ]
        )
        index_body = _kline_envelope(
            [
                _reference_row(bar_open, close)
                for bar_open, close in reversed(list(zip(bar_opens, index_closes, strict=True)))
            ]
        )
        oi_body = _open_interest_envelope(
            [
                {"openInterest": value, "timestamp": str(_ms(instant))}
                for instant, value in reversed(list(zip(oi_instants, oi_values, strict=True)))
            ]
        )

        transport = RoutingTransport(
            {
                # Real historical data does not change between requests, so
                # the retry's second entry for every already-complete kind is
                # identical to the first -- only OHLCV's second entry differs.
                "/v5/market/kline": [
                    HttpResponse(200, incomplete_kline_body),
                    HttpResponse(200, complete_kline_body),
                ],
                "/v5/market/mark-price-kline": [
                    HttpResponse(200, mark_body),
                    HttpResponse(200, mark_body),
                ],
                "/v5/market/index-price-kline": [
                    HttpResponse(200, index_body),
                    HttpResponse(200, index_body),
                ],
                "/v5/market/open-interest": [
                    HttpResponse(200, oi_body),
                    HttpResponse(200, oi_body),
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

        dataset_version = "bybit-v5-linear-btcusdt-phase3b1-retry"
        request = HistoricalAcquisitionRequest(
            source_id=source_id,
            instrument_id=INSTRUMENT_ID,
            provider="bybit",
            provider_symbol=SYMBOL,
            start=START,
            end=END,
            observation_kinds=frozenset(
                {
                    ObservationKind.OHLCV,
                    ObservationKind.MARK_PRICE,
                    ObservationKind.INDEX_PRICE,
                    ObservationKind.OPEN_INTEREST,
                }
            ),
            normalization_version=NORMALIZATION_VERSION,
            dataset_version=dataset_version,
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

        # ---- first attempt: durable partial evidence, no seal -----------------
        first = service.acquire(request, configuration)
        self.assertEqual(first.status, AcquisitionStatus.COVERAGE_FAILED)

        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_raw_observations "
                "WHERE source_id=%s AND event_at>=%s AND event_at<=%s",
                (source_id, START, END),
            )
            # 29 OHLCV + 30 MARK_PRICE + 30 INDEX_PRICE + 6 OPEN_INTEREST.
            self.assertEqual(int(str(cursor.fetchone()[0])), 95)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r "
                "ON r.raw_observation_id=n.raw_observation_id "
                "WHERE r.source_id=%s AND r.event_at>=%s AND r.event_at<=%s",
                (source_id, START, END),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 95)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_dataset_versions "
                "WHERE source_id=%s AND version=%s",
                (source_id, dataset_version),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)

        # ---- identical retry: corrected coverage, restart-safe normalization --
        second = service.acquire(request, configuration)
        self.assertEqual(second.status, AcquisitionStatus.SUCCEEDED, second.failure_code)
        self.assertFalse(second.rejected_count)
        self.assertEqual(
            second.normalized_counts,
            {
                ObservationKind.OHLCV: 30,
                ObservationKind.MARK_PRICE: 30,
                ObservationKind.INDEX_PRICE: 30,
                ObservationKind.OPEN_INTEREST: 6,
            },
        )

        with database.transaction() as connection, connection.cursor() as cursor:
            # Capture deduplicated the 95 already-persisted raw rows and added
            # exactly the one previously-missing OHLCV bar: 96 total, never 191.
            cursor.execute(
                "SELECT COUNT(*) FROM historical_raw_observations "
                "WHERE source_id=%s AND event_at>=%s AND event_at<=%s",
                (source_id, START, END),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 96)
            # Exactly 96 normalized rows prove the UNIQUE(raw_observation_id)
            # constraint was never hit: 95 rows were reused from the first
            # attempt and exactly 1 new row was created on retry, never 191.
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r "
                "ON r.raw_observation_id=n.raw_observation_id "
                "WHERE r.source_id=%s AND r.event_at>=%s AND r.event_at<=%s",
                (source_id, START, END),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 96)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r "
                "ON r.raw_observation_id=n.raw_observation_id "
                "WHERE r.source_id=%s AND r.event_at>=%s AND r.event_at<=%s "
                "AND n.quality_status<>'VALIDATED'",
                (source_id, START, END),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_dataset_versions "
                "WHERE source_id=%s AND version=%s AND status='SEALED'",
                (source_id, dataset_version),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 1)

        database.close()


if __name__ == "__main__":
    unittest.main()
