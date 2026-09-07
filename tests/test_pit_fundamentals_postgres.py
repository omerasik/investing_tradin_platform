"""Real PostgreSQL PIT filing/revision/restart evidence."""

import hashlib
import os
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PitFundamentalsPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_filing_cannot_be_seen_before_acceptance_or_ingestion(self) -> None:
        from trade_platform.fundamentals import StatementType
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.pit_fundamentals import (
            AuthorizedFilingSource,
            FilingFact,
            FundamentalFiling,
            PostgresPitFundamentalStore,
        )
        from trade_platform.professional_instruments import (
            PostgresProfessionalInstrumentMaster,
            mvp_instrument_universe,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        registered = datetime(2023, 1, 1, tzinfo=UTC)
        instrument = replace(
            mvp_instrument_universe(registered)[0], instrument_id="US:XNAS:CYCLE14_SEC",
            canonical_symbol="CYCLE14_SEC",
        )
        PostgresProfessionalInstrumentMaster(database).register(instrument)
        store = PostgresPitFundamentalStore(database)
        source = AuthorizedFilingSource(
            "cycle14_sec_fixture", "test-terms-v1", "test-authorization://cycle14",
            registered, registered,
        )
        store.register_source(source)
        filed = datetime(2024, 5, 1, 12, tzinfo=UTC)
        accepted = filed + timedelta(minutes=5)
        initial = FundamentalFiling(
            source.source_id, instrument.instrument_id, "0001", "10-Q", filed, accepted,
            date(2024, 1, 1), date(2024, 3, 31), 2024, "Q1", 0,
            accepted + timedelta(minutes=5), "fixture://cycle14/0001",
            hashlib.sha256(b"filing-v0").hexdigest(),
            accepted, "test-availability-v1",
        )
        initial_fact = FilingFact(
            initial.filing_record_id, "us-gaap", "Revenue", StatementType.INCOME_STATEMENT,
            Decimal("1000"), "USD", "USD", "revenue", Decimal("1000"), "sec-map-v1",
        )
        store.ingest(initial, (initial_fact,))
        revised = replace(
            initial, filing_record_id=uuid4(), revision=1,
            accepted_at=accepted + timedelta(days=30), ingested_at=accepted + timedelta(days=30, minutes=5),
            provenance_uri="fixture://cycle14/0001-amendment",
            raw_payload_sha256=hashlib.sha256(b"filing-v1").hexdigest(),
            research_available_at=accepted + timedelta(days=30),
        )
        revised_fact = replace(
            initial_fact, fact_id=uuid4(), filing_record_id=revised.filing_record_id,
            as_reported_value=Decimal("900"), standardized_value=Decimal("900"),
        )
        store.ingest(revised, (revised_fact,))
        self.assertEqual(store.available_as_of(instrument.instrument_id, filed), ())
        first = store.available_as_of(instrument.instrument_id, initial.ingested_at, standardized_metric="revenue")
        latest = store.available_as_of(instrument.instrument_id, revised.ingested_at, standardized_metric="revenue")
        self.assertEqual(first[0].fact.as_reported_value, Decimal("1000"))
        self.assertEqual(latest[0].fact.as_reported_value, Decimal("900"))
        with (
            self.assertRaises(PersistenceError), database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("UPDATE pit_fundamental_filings SET form_type='MUTATED' WHERE filing_record_id=%s", (initial.filing_record_id,))
        database.close()
        reopened = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        recovered = PostgresPitFundamentalStore(reopened).available_as_of(
            instrument.instrument_id, revised.ingested_at, standardized_metric="revenue"
        )
        self.assertEqual(recovered, latest)
        reopened.close()

    def test_available_point_in_time_separates_effective_and_known_clocks(self) -> None:
        """The exact scenario from the owner's bug report: a filing publicly
        accepted in 2024, first ingested by this platform in 2026 (a real
        historical backfill, not a backdated fabrication). PIT research must
        see it for an effective_at in 2024 once known_at is honestly at/after
        the real 2026 ingestion -- and never before.
        """
        from trade_platform.fundamentals import StatementType
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.pit_fundamentals import (
            AuthorizedFilingSource,
            FilingFact,
            FundamentalFiling,
            PitFundamentalError,
            PostgresPitFundamentalStore,
        )
        from trade_platform.professional_instruments import (
            PostgresProfessionalInstrumentMaster,
            mvp_instrument_universe,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        registered = datetime(2023, 1, 1, tzinfo=UTC)
        # Canonical symbol deliberately sorts after "DEMO_EQ_A" (not "CYCLE...") --
        # the operator dashboard's instrument panel is a plain alphabetically
        # sorted, LIMIT-paginated, unscoped listing (see operator_dashboard.py's
        # list_instruments ORDER BY canonical_symbol LIMIT ...), and the shared
        # test suite already has enough "before D" fixture instruments across
        # files that one more risks pushing the Module 1B demo's synthetic
        # "DEMO_EQ_A" instrument off that page in test_module1b_demo_acceptance.py's
        # E2E assertion, sharing this same database within a CI run.
        instrument = replace(
            mvp_instrument_universe(registered)[0], instrument_id="US:XNAS:ZCYCLE14_SEC_TWOCLOCK",
            canonical_symbol="ZCYCLE14_SEC_TWOCLOCK",
        )
        PostgresProfessionalInstrumentMaster(database).register(instrument)
        store = PostgresPitFundamentalStore(database)
        source = AuthorizedFilingSource(
            "cycle14_sec_twoclock_fixture", "test-terms-v1",
            "test-authorization://cycle14-twoclock", registered, registered,
        )
        store.register_source(source)

        real_accepted_2024 = datetime(2024, 5, 1, 20, 0, tzinfo=UTC)
        real_ingested_2026 = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
        filing = FundamentalFiling(
            source.source_id, instrument.instrument_id, "0001-backfill", "10-Q",
            real_accepted_2024, real_accepted_2024, date(2024, 1, 1), date(2024, 3, 31),
            2024, "Q1", 0, real_ingested_2026, "fixture://cycle14/backfill-0001",
            hashlib.sha256(b"backfill-filing").hexdigest(),
            real_accepted_2024, "test-availability-v1",
        )
        fact = FilingFact(
            filing.filing_record_id, "us-gaap", "Revenue", StatementType.INCOME_STATEMENT,
            Decimal("500"), "USD", "USD", "revenue", Decimal("500"), "sec-map-v1",
        )
        store.ingest(filing, (fact,))

        effective_2024 = datetime(2024, 6, 1, tzinfo=UTC)
        # known_at strictly before the real ingestion: must NOT be visible, even
        # though effective_at is well after the filing's own accepted_at. Seeing
        # it here would mean the store lied about when the platform actually knew.
        known_before_real_ingestion = datetime(2025, 1, 1, tzinfo=UTC)
        self.assertEqual(
            store.available_point_in_time(
                instrument.instrument_id, effective_2024, known_before_real_ingestion,
                standardized_metric="revenue",
            ),
            (),
        )
        # known_at at/after the real 2026 ingestion: now correctly visible for
        # the 2024 effective date, without ever backdating ingested_at.
        known_after_real_ingestion = datetime(2026, 9, 8, tzinfo=UTC)
        visible = store.available_point_in_time(
            instrument.instrument_id, effective_2024, known_after_real_ingestion,
            standardized_metric="revenue",
        )
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0].fact.as_reported_value, Decimal("500"))
        self.assertEqual(visible[0].filing.ingested_at, real_ingested_2026)

        # known_at before effective_at is a caller error, not a legitimate query.
        with self.assertRaises(PitFundamentalError):
            store.available_point_in_time(
                instrument.instrument_id, effective_2024, datetime(2024, 1, 1, tzinfo=UTC),
            )

        database.close()
