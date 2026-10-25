"""P3A fixture -> real existing PostgreSQL authorities; no provider calls.

All writes, including rejected evidence, roll back after each test. Canonical
AAPL/SPY identities and calendars are exercised without polluting other tests.
"""

import os
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

from tests.test_alpaca_equities_pilot_v1 import NOW, START, fixture_pages, scope, source
from trade_platform.alpaca_equities_pilot_v1 import (
    NAMESPACE,
    PROVIDER,
    AlpacaIexFixtureAdapterV1,
    validate_pilot_coverage,
)
from trade_platform.data_health import (
    DataHealthObservation,
    DataHealthPolicy,
    DataHealthScope,
    PostgresDataHealthStore,
    build_assessment,
)
from trade_platform.data_providers import ProviderHealthRegistry
from trade_platform.historical_bar_bridge import HistoricalBarBridgeError, normalized_ohlcv_to_bar
from trade_platform.historical_market_data import (
    AdjustmentStatus,
    HistoricalDataQualityError,
    HistoricalMarketDataError,
    PostgresHistoricalMarketDataPipeline,
    QualityStatus,
)
from trade_platform.market_data import PrecomputedBarProvider, ingest_from_provider
from trade_platform.postgres_market_data import PostgresHistoricalBarStore
from trade_platform.professional_instruments import (
    CalendarDefinition,
    CalendarException,
    CalendarExceptionType,
    IdentifierMapping,
    IdentifierSourceKind,
    InstrumentResolutionError,
    PostgresProfessionalInstrumentMaster,
    ProfessionalCalendarError,
    SessionType,
    WeeklySession,
    mvp_instrument_universe,
)
from trade_platform.provider_ingestion import (
    HistoricalIngestionRequest,
    ingest_raw_historical_pages,
)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class AlpacaPilotPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(config, "head")

    def setUp(self):
        import psycopg

        connection = psycopg.connect(os.environ["POSTGRES_TEST_DSN"])
        self.addCleanup(connection.close)
        outer = connection.transaction(force_rollback=True)
        outer.__enter__()
        self.addCleanup(outer.__exit__, None, None, None)

        class RollbackDatabase:
            @contextmanager
            def transaction(self):
                with connection.transaction():
                    yield connection

        database = RollbackDatabase()
        self.pipeline = PostgresHistoricalMarketDataPipeline(database)
        self.bars = PostgresHistoricalBarStore(database)
        self.health = PostgresDataHealthStore(database)
        self.master = PostgresProfessionalInstrumentMaster(database)
        registered = datetime(2020, 1, 1, tzinfo=UTC)
        for instrument in mvp_instrument_universe(registered)[:2]:
            try:
                self.master.get_as_of(instrument.instrument_id, NOW)
            except InstrumentResolutionError:
                self.master.register(instrument)
            self.master.add_identifier_mapping(IdentifierMapping(
                instrument.instrument_id, IdentifierSourceKind.PROVIDER, NAMESPACE, instrument.canonical_symbol,
                registered, None, registered, "fixture://p3a/mapping-only",
            ))
            try:
                self.master.is_open(instrument.venue, START, known_at=NOW)
            except ProfessionalCalendarError:
                calendar = CalendarDefinition(uuid4(), instrument.venue, SessionType.US_EQUITY, "America/New_York",
                                              date(2020, 1, 1), None, registered, "fixture://p3a/calendar-only")
                self.master.add_calendar(calendar)
                for day in range(5):
                    self.master.add_weekly_session(WeeklySession(calendar.calendar_id, day, time(9, 30), time(16), 0, registered))
                self.master.add_calendar_exception(CalendarException(calendar.calendar_id, date(2025, 1, 1),
                    CalendarExceptionType.HOLIDAY, None, registered, "fixture://p3a/holiday"))
                self.master.add_calendar_exception(CalendarException(calendar.calendar_id, date(2025, 11, 28),
                    CalendarExceptionType.EARLY_CLOSE, time(13), registered, "fixture://p3a/early-close"))
        self.contract = replace(source(), source_id=uuid4(), provider_terms_version=f"fixture-only-{uuid4()}")
        self.pipeline.register_source(self.contract)

    def test_both_symbols_end_to_end_and_read_only_knowledge_boundaries(self):
        normalized_at = NOW + timedelta(minutes=1)
        all_members = []
        for symbol in ("AAPL", "SPY"):
            adapter = AlpacaIexFixtureAdapterV1(self.contract, self.master, fixture_pages=fixture_pages(symbol), now=lambda: NOW)
            result = ingest_raw_historical_pages(
                adapter, HistoricalIngestionRequest(self.contract.source_id, scope(symbol), maximum_pages=40),
                self.pipeline, ProviderHealthRegistry(lambda: NOW), now=lambda: NOW,
            )
            self.assertEqual(result.state.value, "HEALTHY")
            page1 = adapter.fetch_raw_page(self.contract.source_id, scope(symbol), None)
            page2 = adapter.fetch_raw_page(self.contract.source_id, scope(symbol), page1.next_cursor)
            raw = page1.records + page2.records
            self.assertEqual(self.pipeline.capture_raw(list(raw)), result.captured_raw_ids)
            validate_pilot_coverage(raw, scope(symbol), self.master, known_at=NOW)
            normalized = [self.pipeline.normalize(r.raw_observation_id, "p3a-fixture-normalizer-v1", normalized_at) for r in raw]
            self.assertTrue(all(n.quality_status is QualityStatus.VALIDATED for n in normalized))
            bars = [normalized_ohlcv_to_bar(n, r, PROVIDER) for n, r in zip(normalized, raw, strict=True)]
            version = ingest_from_provider(PrecomputedBarProvider(PROVIDER, bars), self.bars, bars[0].instrument_id,
                                           "1m", START, START + timedelta(minutes=3))
            self.assertEqual(version, self.bars.ingest(bars))
            stored = self.bars.read_range(bars[0].instrument_id, "1m", START, START + timedelta(minutes=3))
            self.assertEqual(len(stored), 3)
            self.assertEqual(stored[0].open, Decimal(str(raw[0].raw_payload["open"])))
            self.assertEqual(self.bars.available_as_of(bars[0].instrument_id, "1m", NOW), [])
            observations = [DataHealthObservation(
                b.provider, b.instrument_id, b.event_at, b.ingested_at, b.revision,
                b.open, b.high, b.low, b.close, b.volume, b.original_timezone, "UTC",
                self.master.is_open(raw[0].raw_payload["listing_mic"], b.event_at, known_at=normalized_at),
            ) for b in stored]
            policy = DataHealthPolicy("p3a-fixture-health-v1", START, START + timedelta(minutes=2), timedelta(minutes=1),
                                      timedelta(minutes=2), Decimal(0), 3)
            assessment = build_assessment(observations, policy, scope_type=DataHealthScope.INSTRUMENT,
                                          scope_value=bars[0].instrument_id, evaluated_at=normalized_at, interval="1m")
            self.assertFalse(assessment.blocking)
            self.health.persist(assessment)
            all_members.extend(n.normalized_observation_id for n in normalized)
            # No provider revision is invented when evidence changes.
            changed = replace(raw[0], raw_payload={**raw[0].raw_payload, "close": "174.9"})
            with self.assertRaisesRegex(HistoricalMarketDataError, "observation_conflict"):
                self.pipeline.capture_raw([changed])
        sealed_at = normalized_at + timedelta(minutes=1)
        dataset = self.pipeline.seal_dataset(self.contract.source_id, "p3a-fixture-only-v1", "p3a-fixture-normalizer-v1",
                                             tuple(all_members), sealed_at)
        for symbol, instrument_id in (("AAPL", "US:XNAS:AAPL"), ("SPY", "US:ARCX:SPY")):
            self.assertEqual(self.pipeline.research_query(dataset.dataset_version_id, instrument_id, START, START + timedelta(minutes=3), NOW), ())
            result = self.pipeline.research_query(dataset.dataset_version_id, instrument_id, START, START + timedelta(minutes=3), sealed_at)
            self.assertEqual(len(result), 3)
            self.assertEqual({r.exchange for r in result}, {"IEXG"})
            self.assertEqual({r.adjustment_status for r in result}, {AdjustmentStatus.RAW})
            self.assertEqual(result, self.pipeline.research_query(dataset.dataset_version_id, instrument_id, START, START + timedelta(minutes=3), sealed_at))
        self.assertEqual(len(dataset.content_hash), 64)

    def test_pit_calendar_holiday_dst_early_close_and_missing_coverage(self):
        from trade_platform.data_providers import ProviderError

        self.assertFalse(self.master.is_open("XNAS", datetime(2025, 1, 1, 15, tzinfo=UTC), known_at=NOW))
        self.assertTrue(self.master.is_open("XNAS", START, known_at=NOW))
        self.assertTrue(self.master.is_open("XNAS", datetime(2025, 3, 10, 13, 30, tzinfo=UTC), known_at=NOW))
        self.assertFalse(self.master.is_open("XNAS", datetime(2025, 11, 28, 18, tzinfo=UTC), known_at=NOW))
        adapter = AlpacaIexFixtureAdapterV1(self.contract, self.master, fixture_pages=fixture_pages(), now=lambda: NOW)
        rows = adapter.fetch_raw_page(self.contract.source_id, scope(), None).records
        with self.assertRaisesRegex(ProviderError, "pilot_coverage"):
            validate_pilot_coverage(rows, scope(), self.master, known_at=NOW)
        observations = [DataHealthObservation(PROVIDER, "US:XNAS:AAPL", r.event_at, r.ingested_at, 0,
            *(Decimal(str(r.raw_payload[key])) for key in ("open", "high", "low", "close", "volume")),
            "UTC", "UTC", True) for r in rows]
        policy = DataHealthPolicy("p3a-missing-fixture-v1", START, START + timedelta(minutes=2), timedelta(minutes=1),
                                  timedelta(minutes=2), Decimal(0), 3)
        assessment = build_assessment(observations, policy, scope_type=DataHealthScope.INSTRUMENT,
                                      scope_value="US:XNAS:AAPL", evaluated_at=NOW, interval="1m")
        self.assertTrue(assessment.blocking)
        self.health.persist(assessment)

    def test_feed_spoof_and_adjusted_evidence_cannot_enter_bars_or_sealed_dataset(self):
        for case in ("adjusted", "feed", "venue"):
            contract = replace(self.contract, source_id=uuid4(), provider_terms_version=f"fixture-negative-{uuid4()}")
            self.pipeline.register_source(contract)
            adapter = AlpacaIexFixtureAdapterV1(contract, self.master, fixture_pages=fixture_pages(), now=lambda: NOW)
            original = adapter.fetch_raw_page(contract.source_id, scope(), None).records[0]
            raw = replace(original, adjustment_status=AdjustmentStatus.LATEST_ADJUSTED) if case == "adjusted" else (
                replace(original, exchange="CONSOLIDATED_TAPE") if case == "venue" else
                replace(original, raw_payload={**original.raw_payload, "feed": "sip"}))
            raw_id, = self.pipeline.capture_raw([raw])
            normalized = self.pipeline.normalize(raw_id, "p3a-negative-v1", NOW)
            self.assertEqual(normalized.quality_status, QualityStatus.REJECTED)
            self.assertIn("alpaca_iex_source_contract_mismatch", normalized.quality_issues)
            with self.assertRaises(HistoricalBarBridgeError):
                normalized_ohlcv_to_bar(normalized, raw, PROVIDER)
            with self.assertRaises(HistoricalDataQualityError):
                self.pipeline.seal_dataset(contract.source_id, "p3a-negative-v1", "p3a-negative-v1",
                                           (normalized.normalized_observation_id,), NOW)
