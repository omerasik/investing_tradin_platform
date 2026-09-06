"""Real PostgreSQL evidence for Module 3G.1b's OHLCV bridge.

Databento adapter -> provider_ingestion checkpoint -> immutable raw capture ->
historical_market_data normalization [canonical instrument resolution] ->
historical_bar_bridge -> ingest_from_provider() -> PostgresHistoricalBarStore ->
Data Health -- exercised end to end against a real database, using fixture raw
records shaped like the disabled Databento adapter would actually produce (no real
Databento call is made anywhere in this file).
"""

import os
import unittest
from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class DatabentoBarBridgePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def setUp(self) -> None:
        from trade_platform.data_health import PostgresDataHealthStore
        from trade_platform.historical_market_data import PostgresHistoricalMarketDataPipeline
        from trade_platform.operational_alerts import PostgresOperationalAlertStore
        from trade_platform.operational_jobs import PostgresOperationalJobStore
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_market_data import PostgresHistoricalBarStore
        from trade_platform.professional_instruments import PostgresProfessionalInstrumentMaster
        from trade_platform.retention_evidence import PostgresRetentionEvidenceStore
        from trade_platform.scheduler import JobContext

        self.dsn = os.environ["POSTGRES_TEST_DSN"]
        self.suffix = uuid4().hex[:8]
        self.database = PostgresDatabase(self.dsn)
        self.instruments = PostgresProfessionalInstrumentMaster(self.database)
        self.pipeline = PostgresHistoricalMarketDataPipeline(self.database)
        self.bar_store = PostgresHistoricalBarStore(self.database)
        self.data_health_store = PostgresDataHealthStore(self.database)
        alerts = PostgresOperationalAlertStore(self.database)
        self.context = JobContext(
            database=self.database,
            job_store=PostgresOperationalJobStore(self.database, alerts=alerts),
            alerts=alerts,
            retention_store=PostgresRetentionEvidenceStore(self.database),
            bar_store=self.bar_store,
            data_health_store=self.data_health_store,
        )

    def tearDown(self) -> None:
        self.database.close()

    def _register_aapl_like_instrument(self, *, venue: str = "XNAS", asset_class=None):
        from dataclasses import replace as instrument_replace

        from trade_platform.domain import AssetClass
        from trade_platform.professional_instruments import (
            IdentifierMapping,
            IdentifierSourceKind,
            mvp_instrument_universe,
        )

        registered_at = datetime(2020, 1, 1, tzinfo=UTC)
        base = mvp_instrument_universe(registered_at)[0]  # AAPL, XNAS
        instrument_id = f"US:{venue}:BRIDGE{self.suffix}"
        instrument = instrument_replace(
            base, instrument_id=instrument_id, canonical_symbol=f"BRIDGE{self.suffix}",
            venue=venue, mic=venue, asset_class=asset_class or AssetClass.EQUITY,
        )
        self.instruments.register(instrument)
        namespace = f"DATABENTO:INSTRUMENT_ID:{self.suffix}"
        self.instruments.add_identifier_mapping(
            IdentifierMapping(
                instrument_id, IdentifierSourceKind.PROVIDER, namespace, "101",
                registered_at, None, registered_at, f"fixture://3g1b/{self.suffix}/mapping",
            )
        )
        return instrument, namespace

    def _register_source(self, namespace: str):
        from trade_platform.historical_market_data import AuthorizedHistoricalSource

        registered_at = datetime(2020, 1, 1, tzinfo=UTC)
        source = AuthorizedHistoricalSource(
            "databento", f"us-equities-bridge-{self.suffix}", namespace,
            "test-terms-v1", f"test-authorization://3g1b/{self.suffix}", registered_at, registered_at,
        )
        self.pipeline.register_source(source)
        return source

    def test_daily_and_minute_ohlcv_flow_from_raw_capture_through_the_bar_store_to_data_health(self) -> None:
        from trade_platform.historical_bar_bridge import normalized_ohlcv_to_bar
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            QualityStatus,
            RawHistoricalObservation,
        )
        from trade_platform.market_data import PrecomputedBarProvider, ingest_from_provider
        from trade_platform.scheduler import run_data_health_evaluation

        instrument, namespace = self._register_aapl_like_instrument()
        source = self._register_source(namespace)
        event_daily = datetime(2025, 1, 2, tzinfo=UTC)
        event_minute = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
        ingested_daily = event_daily + timedelta(minutes=5)
        ingested_minute = event_minute + timedelta(minutes=5)

        raw_daily = RawHistoricalObservation(
            source.source_id, ObservationKind.OHLCV, "101", "AAPL", "CONSOLIDATED_TAPE",
            event_daily, event_daily, ingested_daily, AdjustmentStatus.RAW, 0,
            "databento://batch/ohlcv-1d",
            {"interval": "1d", "open": "100", "high": "102", "low": "99", "close": "101.5", "volume": "1000000"},
        )
        raw_minute = RawHistoricalObservation(
            source.source_id, ObservationKind.OHLCV, "101", "AAPL", "CONSOLIDATED_TAPE",
            event_minute, event_minute, ingested_minute, AdjustmentStatus.RAW, 0,
            "databento://batch/ohlcv-1m",
            {"interval": "1m", "open": "100.1", "high": "100.3", "low": "100.0", "close": "100.2", "volume": "5000"},
        )
        raw_ids = self.pipeline.capture_raw([raw_daily, raw_minute])
        normalized_at = ingested_minute + timedelta(minutes=1)
        normalized = [self.pipeline.normalize(raw_id, "databento-normalizer-v1", normalized_at) for raw_id in raw_ids]

        self.assertTrue(all(item.quality_status == QualityStatus.VALIDATED for item in normalized))
        self.assertEqual({item.normalized_value["interval"] for item in normalized}, {"1d", "1m"})

        bars = [
            normalized_ohlcv_to_bar(item, raw, "databento")
            for item, raw in zip(normalized, (raw_daily, raw_minute), strict=True)
        ]
        for bar in bars:
            ingest_from_provider(
                PrecomputedBarProvider("databento", [bar]), self.bar_store,
                bar.instrument_id, bar.interval, event_daily - timedelta(days=1), event_minute + timedelta(days=1),
            )

        daily_stored = self.bar_store.read_range(instrument.instrument_id, "1d", event_daily - timedelta(days=1), event_daily + timedelta(days=1))
        minute_stored = self.bar_store.read_range(instrument.instrument_id, "1m", event_minute - timedelta(hours=1), event_minute + timedelta(hours=1))
        self.assertEqual((len(daily_stored), len(minute_stored)), (1, 1))
        self.assertEqual(daily_stored[0].close, Decimal("101.5"))
        self.assertEqual(minute_stored[0].close, Decimal("100.2"))
        # PIT: available only from normalization time, not the earlier raw capture time.
        self.assertEqual(daily_stored[0].ingested_at, normalized_at)

        # Consumable by the existing, unmodified Data Health authority -- scoped to
        # exactly this test's own series (a single interval; see
        # test_a_bridged_bar_series_is_consumable_by_the_existing_data_health_authority
        # for why this test does not also evaluate both intervals of the same
        # instrument in one run_data_health_evaluation call) so it never persists a
        # shared GLOBAL block.
        class _ScopedBarStore:
            def __init__(self, real_store: object, series: list[tuple[str, str]]) -> None:
                self._real_store = real_store
                self._series = series

            def known_series(self) -> list[tuple[str, str]]:
                return self._series

            def read_range(self, instrument_id: str, interval: str, start: datetime, end: datetime):
                return self._real_store.read_range(instrument_id, interval, start, end)

        scoped_context = dc_replace(
            self.context, bar_store=_ScopedBarStore(self.bar_store, [(instrument.instrument_id, "1d")]),
        )
        summary = run_data_health_evaluation(scoped_context, normalized_at + timedelta(hours=1))
        self.assertEqual(summary["series_checked"], "1")
        self.assertEqual(summary["assessments_persisted"], "1")
        # A single bar is INCOMPLETE_DATASET (too few observations for the window)
        # but never a fabricated pass and never a provider-specific bypass -- exactly
        # the same outcome any provider's bars would produce under the unmodified
        # detect_data_health policy.
        self.assertEqual(summary["blocking_assessments"], "1")

    def test_a_bridged_bar_series_is_consumable_by_the_existing_data_health_authority(self) -> None:
        """Documents a pre-existing, out-of-scope gap discovered while writing this test.

        ``scheduler.run_data_health_evaluation`` scopes each persisted assessment by
        ``instrument_id`` alone (``DataHealthScope.INSTRUMENT, scope_value=instrument_id``),
        not by ``(instrument_id, interval)``. If ``known_series()`` ever returns two
        different intervals for the SAME instrument evaluated in the SAME tick, the
        second ``INSTRUMENT``-scoped persist collides with the immutable
        ``data_health_assessments`` table's ``(scope_type, scope_value, evaluated_at)``
        uniqueness and raises ``DataHealthError``. This module (3G.1b) reproduces that
        only when it is actually exercised -- it is a Module 3E/3F scheduler concern,
        not a bar-bridge defect, and is deliberately NOT fixed here (out of scope: "do
        not redesign unrelated modules"). This test instead evaluates one interval at a
        time, exactly like the test above, and flags the gap here for a follow-up.
        """
        from trade_platform.historical_bar_bridge import normalized_ohlcv_to_bar
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            RawHistoricalObservation,
        )
        from trade_platform.market_data import PrecomputedBarProvider, ingest_from_provider
        from trade_platform.scheduler import run_data_health_evaluation

        instrument, namespace = self._register_aapl_like_instrument()
        source = self._register_source(namespace)
        event = datetime(2025, 1, 20, tzinfo=UTC)
        raw = RawHistoricalObservation(
            source.source_id, ObservationKind.OHLCV, "101", "AAPL", "CONSOLIDATED_TAPE",
            event, event, event + timedelta(minutes=5), AdjustmentStatus.RAW, 0,
            "databento://batch/ohlcv-1m",
            {"interval": "1m", "open": "50", "high": "51", "low": "49.5", "close": "50.5", "volume": "2000"},
        )
        raw_id, = self.pipeline.capture_raw([raw])
        normalized_at = event + timedelta(minutes=6)
        normalized = self.pipeline.normalize(raw_id, "databento-normalizer-v1", normalized_at)
        bar = normalized_ohlcv_to_bar(normalized, raw, "databento")
        ingest_from_provider(
            PrecomputedBarProvider("databento", [bar]), self.bar_store,
            bar.instrument_id, bar.interval, event - timedelta(hours=1), event + timedelta(hours=1),
        )

        class _ScopedBarStore:
            def __init__(self, real_store: object, series: list[tuple[str, str]]) -> None:
                self._real_store = real_store
                self._series = series

            def known_series(self) -> list[tuple[str, str]]:
                return self._series

            def read_range(self, instrument_id: str, interval: str, start: datetime, end: datetime):
                return self._real_store.read_range(instrument_id, interval, start, end)

        scoped_context = dc_replace(
            self.context, bar_store=_ScopedBarStore(self.bar_store, [(instrument.instrument_id, "1m")]),
        )
        summary = run_data_health_evaluation(scoped_context, normalized_at + timedelta(minutes=1))
        self.assertEqual(summary["series_checked"], "1")
        self.assertEqual(summary["assessments_persisted"], "1")

    def test_consolidated_tape_exchange_validates_against_the_resolved_instruments_own_venue(self) -> None:
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            QualityStatus,
            RawHistoricalObservation,
        )

        _instrument, namespace = self._register_aapl_like_instrument(venue="XNAS")
        source = self._register_source(namespace)
        event = datetime(2025, 2, 1, tzinfo=UTC)
        raw = RawHistoricalObservation(
            source.source_id, ObservationKind.OHLCV, "101", "AAPL", "CONSOLIDATED_TAPE",
            event, event, event + timedelta(minutes=1), AdjustmentStatus.RAW, 0,
            "databento://batch/ohlcv-1d",
            {"interval": "1d", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "1"},
        )
        raw_id, = self.pipeline.capture_raw([raw])
        normalized = self.pipeline.normalize(raw_id, "databento-normalizer-v1", event + timedelta(minutes=2))
        self.assertEqual(normalized.quality_status, QualityStatus.VALIDATED)
        self.assertNotIn("exchange_instrument_mismatch", normalized.quality_issues)

    def test_consolidated_tape_exchange_is_rejected_for_a_venue_not_modeled_as_eligible(self) -> None:
        from trade_platform.domain import AssetClass
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            QualityStatus,
            RawHistoricalObservation,
        )

        # "XNAS" and "ARCX" are the only venues this repository's instrument master
        # currently models as consolidated-tape-eligible; use a real but unmodeled
        # venue (London Stock Exchange) to prove the check still actually rejects
        # rather than passing every consolidated-tape claim unconditionally.
        _instrument, namespace = self._register_aapl_like_instrument(venue="XLON", asset_class=AssetClass.EQUITY)
        source = self._register_source(namespace)
        event = datetime(2025, 2, 1, tzinfo=UTC)
        raw = RawHistoricalObservation(
            source.source_id, ObservationKind.OHLCV, "101", "AAPL", "CONSOLIDATED_TAPE",
            event, event, event + timedelta(minutes=1), AdjustmentStatus.RAW, 0,
            "databento://batch/ohlcv-1d",
            {"interval": "1d", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "1"},
        )
        raw_id, = self.pipeline.capture_raw([raw])
        normalized = self.pipeline.normalize(raw_id, "databento-normalizer-v1", event + timedelta(minutes=2))
        self.assertEqual(normalized.quality_status, QualityStatus.REJECTED)
        self.assertIn("exchange_instrument_mismatch", normalized.quality_issues)

    def test_unresolved_instrument_never_reaches_the_bar_store(self) -> None:
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            HistoricalDataResolutionError,
            ObservationKind,
            RawHistoricalObservation,
        )

        instrument, namespace = self._register_aapl_like_instrument()
        source = self._register_source(namespace)
        event = datetime(2025, 3, 1, tzinfo=UTC)
        raw = RawHistoricalObservation(
            source.source_id, ObservationKind.OHLCV, "NOT_A_REGISTERED_IDENTIFIER", "GHOST", "CONSOLIDATED_TAPE",
            event, event, event + timedelta(minutes=1), AdjustmentStatus.RAW, 0,
            "databento://batch/ohlcv-1d",
            {"interval": "1d", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "1"},
        )
        raw_id, = self.pipeline.capture_raw([raw])
        with self.assertRaises(HistoricalDataResolutionError):
            self.pipeline.normalize(raw_id, "databento-normalizer-v1", event + timedelta(minutes=2))
        # The shared test database accumulates rows across test methods -- assert this
        # test's own instrument never appears, not that the whole table is empty.
        self.assertFalse(any(row[0] == instrument.instrument_id for row in self.bar_store.known_series()))

    def test_rejected_normalized_observation_never_reaches_the_bar_store(self) -> None:
        from trade_platform.historical_bar_bridge import (
            HistoricalBarBridgeError,
            normalized_ohlcv_to_bar,
        )
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            QualityStatus,
            RawHistoricalObservation,
        )

        instrument, namespace = self._register_aapl_like_instrument()
        source = self._register_source(namespace)
        event = datetime(2025, 4, 1, tzinfo=UTC)
        raw = RawHistoricalObservation(
            source.source_id, ObservationKind.OHLCV, "101", "AAPL", "CONSOLIDATED_TAPE",
            event, event, event + timedelta(minutes=1), AdjustmentStatus.RAW, 0,
            "databento://batch/ohlcv-1d",
            {"interval": "1d", "open": "10", "high": "9", "low": "8", "close": "10", "volume": "-1"},  # impossible OHLC + negative volume
        )
        raw_id, = self.pipeline.capture_raw([raw])
        normalized = self.pipeline.normalize(raw_id, "databento-normalizer-v1", event + timedelta(minutes=2))
        self.assertEqual(normalized.quality_status, QualityStatus.REJECTED)
        with self.assertRaises(HistoricalBarBridgeError):
            normalized_ohlcv_to_bar(normalized, raw, "databento")
        self.assertFalse(any(row[0] == instrument.instrument_id for row in self.bar_store.known_series()))

    def test_replaying_the_same_bridged_bar_is_idempotent_and_conflicting_content_is_rejected(self) -> None:
        from trade_platform.historical_bar_bridge import normalized_ohlcv_to_bar
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            RawHistoricalObservation,
        )
        from trade_platform.market_data import (
            DataQualityError,
            PrecomputedBarProvider,
            ingest_from_provider,
        )

        _instrument, namespace = self._register_aapl_like_instrument()
        source = self._register_source(namespace)
        event = datetime(2025, 5, 1, tzinfo=UTC)
        raw = RawHistoricalObservation(
            source.source_id, ObservationKind.OHLCV, "101", "AAPL", "CONSOLIDATED_TAPE",
            event, event, event + timedelta(minutes=1), AdjustmentStatus.RAW, 0,
            "databento://batch/ohlcv-1d",
            {"interval": "1d", "open": "100", "high": "101", "low": "99", "close": "100.5", "volume": "10"},
        )
        raw_id, = self.pipeline.capture_raw([raw])
        normalized = self.pipeline.normalize(raw_id, "databento-normalizer-v1", event + timedelta(minutes=2))
        bar = normalized_ohlcv_to_bar(normalized, raw, "databento")

        window = (event - timedelta(days=1), event + timedelta(days=1))
        first_digest = ingest_from_provider(PrecomputedBarProvider("databento", [bar]), self.bar_store, bar.instrument_id, bar.interval, *window)
        second_digest = ingest_from_provider(PrecomputedBarProvider("databento", [bar]), self.bar_store, bar.instrument_id, bar.interval, *window)
        self.assertEqual(first_digest, second_digest)
        self.assertEqual(len(self.bar_store.read_range(bar.instrument_id, bar.interval, *window)), 1)

        # Same bar identity (instrument_id, interval, event_at, revision=0) as `bar`
        # above, but different content -- a second, independently-normalized reading
        # of the SAME raw record (same raw_observation_id, matching the bridge's own
        # cross-check) that disagrees on price.
        conflicting_normalized = dc_replace(
            normalized, normalized_observation_id=uuid4(),
            normalized_value={"interval": "1d", "open": "999", "high": "999", "low": "999", "close": "999", "volume": "1"},
        )
        conflicting_bar = normalized_ohlcv_to_bar(conflicting_normalized, raw, "databento")
        with self.assertRaisesRegex(DataQualityError, "conflicting_duplicate_bar_revision"):
            ingest_from_provider(PrecomputedBarProvider("databento", [conflicting_bar]), self.bar_store, bar.instrument_id, bar.interval, *window)
        self.assertEqual(len(self.bar_store.read_range(bar.instrument_id, bar.interval, *window)), 1)
        self.assertEqual(self.bar_store.read_range(bar.instrument_id, bar.interval, *window)[0].close, Decimal("100.5"))


if __name__ == "__main__":
    unittest.main()
