"""PostgreSQL-gated tests for the provider-neutral historical bar authority (Module 3F)."""

import os
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PostgresHistoricalBarStoreTests(unittest.TestCase):
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
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_market_data import PostgresHistoricalBarStore

        self.dsn = os.environ["POSTGRES_TEST_DSN"]
        self.database = PostgresDatabase(self.dsn)
        self.store = PostgresHistoricalBarStore(self.database)
        self.instrument_id = f"US:NYSE:M3F{uuid4().hex[:8]}"
        self.now = datetime(2026, 9, 6, 12, tzinfo=UTC)

    def tearDown(self) -> None:
        self.database.close()

    def _bar(self, minutes: int = 0, **changes: object):
        from trade_platform.domain import DataProcessingStatus, OHLCVBar

        event = self.now + timedelta(minutes=minutes)
        values: dict[str, object] = {
            "instrument_id": self.instrument_id, "interval": "1m", "event_at": event,
            "effective_at": event, "ingested_at": event, "open": Decimal("100.123456789012"),
            "high": Decimal("101.5"), "low": Decimal("99.25"), "close": Decimal("100.75"),
            "volume": Decimal("1000.5"), "provider": "fixture", "source_identifier": f"bar-{minutes}",
            "original_timezone": "UTC", "revision": 0, "data_version": "v1",
            "quality_score": Decimal("1"), "processing_status": DataProcessingStatus.RAW,
        }
        values.update(changes)
        return OHLCVBar(**values)

    def test_exact_decimal_round_trip(self) -> None:
        bar = self._bar(open=Decimal("100.123456789012"), volume=Decimal("999999.999999"))
        self.store.ingest([bar])
        [stored] = self.store.available_as_of(self.instrument_id, "1m", bar.ingested_at)
        self.assertEqual(stored.open, Decimal("100.123456789012"))
        self.assertEqual(stored.volume, Decimal("999999.999999"))
        self.assertIsInstance(stored.open, Decimal)

    def test_pit_as_of_correctness(self) -> None:
        from trade_platform.domain import DataProcessingStatus, OHLCVBar

        past_effective = self.now
        future_ingested = OHLCVBar(
            instrument_id=self.instrument_id, interval="1m", event_at=self.now,
            effective_at=self.now, ingested_at=self.now + timedelta(hours=1),
            open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
            volume=Decimal("1000"), provider="fixture", source_identifier="late-ingest",
            original_timezone="UTC", revision=0, data_version="v1",
            quality_score=Decimal("1"), processing_status=DataProcessingStatus.RAW,
        )
        self.store.ingest([future_ingested])
        # Effective at decision time, but not yet ingested -- must not leak.
        self.assertEqual(self.store.available_as_of(self.instrument_id, "1m", past_effective), [])
        visible = self.store.available_as_of(
            self.instrument_id, "1m", self.now + timedelta(hours=1)
        )
        self.assertEqual([bar.source_identifier for bar in visible], ["late-ingest"])

    def test_multiple_revisions_are_all_retained_and_ranked_by_revision(self) -> None:
        first = self._bar(revision=0, close=Decimal("100"))
        corrected = self._bar(revision=1, close=Decimal("100.10"))
        self.store.ingest([first])
        self.store.ingest([corrected])
        visible = self.store.available_as_of(self.instrument_id, "1m", first.ingested_at)
        self.assertEqual([bar.revision for bar in visible], [0, 1])
        self.assertEqual(visible[1].close, Decimal("100.10"))
        # The original revision's content must be unchanged (no silent mutation).
        self.assertEqual(visible[0].close, Decimal("100"))

    def test_duplicate_ingestion_of_identical_content_is_idempotent(self) -> None:
        bar = self._bar()
        first_digest = self.store.ingest([bar])
        second_digest = self.store.ingest([bar])
        self.assertEqual(first_digest, second_digest)
        visible = self.store.available_as_of(self.instrument_id, "1m", bar.ingested_at)
        self.assertEqual(len(visible), 1)

    def test_conflicting_duplicate_same_identity_different_content_is_rejected(self) -> None:
        from trade_platform.market_data import DataQualityError

        bar = self._bar(close=Decimal("100"))
        conflicting = self._bar(close=Decimal("100.50"))
        self.store.ingest([bar])
        with self.assertRaisesRegex(DataQualityError, "conflicting_duplicate_bar_revision"):
            self.store.ingest([conflicting])
        # The original record must remain exactly as first written.
        [stored] = self.store.available_as_of(self.instrument_id, "1m", bar.ingested_at)
        self.assertEqual(stored.close, Decimal("100"))

    def test_provider_provenance_mismatch_is_rejected_before_any_persistence(self) -> None:
        from trade_platform.market_data import (
            DataQualityError,
            FixtureBarProvider,
            ingest_from_provider,
        )

        mismatched = self._bar(provider="untrusted-source")
        with self.assertRaises(DataQualityError):
            ingest_from_provider(
                FixtureBarProvider([mismatched]), self.store, mismatched.instrument_id,
                mismatched.interval, mismatched.event_at, mismatched.event_at,
            )
        # known_series() scans the whole shared table (other tests in this run also
        # write to it), so assert this test's own series was never persisted rather
        # than asserting global emptiness.
        self.assertNotIn((self.instrument_id, mismatched.interval), self.store.known_series())

    def test_ingest_from_provider_works_against_the_postgres_authority(self) -> None:
        from trade_platform.market_data import FixtureBarProvider, ingest_from_provider

        bar = self._bar()
        provider = FixtureBarProvider([bar])
        digest = ingest_from_provider(
            provider, self.store, bar.instrument_id, bar.interval, bar.event_at, bar.event_at
        )
        self.assertEqual(len(digest), 64)
        self.assertIn((self.instrument_id, "1m"), self.store.known_series())

    def test_restart_persistence(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_market_data import PostgresHistoricalBarStore

        bar = self._bar()
        self.store.ingest([bar])
        self.database.close()

        reopened = PostgresDatabase(self.dsn)
        try:
            reopened_store = PostgresHistoricalBarStore(reopened)
            visible = reopened_store.available_as_of(self.instrument_id, "1m", bar.ingested_at)
            self.assertEqual(len(visible), 1)
            self.assertEqual(visible[0].close, bar.close)
        finally:
            reopened.close()
        # setUp's tearDown will also close self.database; make that a harmless no-op.
        self.database = reopened
        self.database.close()

    def test_read_range_returns_bars_in_the_event_time_window(self) -> None:
        inside = self._bar(minutes=0)
        outside = self._bar(minutes=120, source_identifier="outside")
        self.store.ingest([inside])
        self.store.ingest([outside])
        window = self.store.read_range(
            self.instrument_id, "1m", self.now - timedelta(minutes=1), self.now + timedelta(minutes=1)
        )
        self.assertEqual([bar.source_identifier for bar in window], ["bar-0"])


if __name__ == "__main__":
    unittest.main()
