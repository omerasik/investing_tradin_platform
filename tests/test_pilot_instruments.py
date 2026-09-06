"""Module 3G.1e: real PostgreSQL evidence for the source-backed pilot instrument universe.

Companion to test_professional_instrument_master.py (the pre-existing fixture/demo
coverage, left completely untouched). This proves the pilot's 16 real instruments
register, resolve, and survive a restart through the exact same
PostgresProfessionalInstrumentMaster authority -- no parallel instrument master.
One sequential test method, matching this suite's existing convention for this module.
"""

import os
import unittest
from datetime import UTC, date, datetime


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PilotInstrumentOnboardingIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_pilot_universe_identity_lifecycle_and_restart(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.pilot_instruments import (
            DATABENTO_RAW_SYMBOL_NAMESPACE,
            PILOT_INSTRUMENTS,
            pilot_databento_identifier_mappings,
            pilot_delistings,
            pilot_instrument,
            pilot_symbol_mappings,
        )
        from trade_platform.professional_instruments import (
            IdentifierMapping,
            IdentifierSourceKind,
            InstrumentMappingConflictError,
            InstrumentResolutionError,
            LifecycleStatus,
            PostgresProfessionalInstrumentMaster,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        master = PostgresProfessionalInstrumentMaster(database)
        registered_at = datetime(2026, 9, 6, tzinfo=UTC)

        for spec in PILOT_INSTRUMENTS:
            master.register(pilot_instrument(spec))
        for mapping in pilot_symbol_mappings():
            master.add_symbol_mapping(mapping)
        for mapping in pilot_databento_identifier_mappings():
            master.add_identifier_mapping(mapping)
        for instrument_id, effective_date, reason, _source in pilot_delistings():
            effective_at = datetime.combine(effective_date, datetime.min.time(), tzinfo=UTC)
            master.delist(instrument_id, effective_at, effective_at, reason)

        # -- Real listing dates are not the fixture 2000-01-01 placeholder. --
        aapl = master.get_as_of("PILOT:AAPL", registered_at)
        self.assertEqual(aapl.listing_date, date(1980, 12, 12))
        self.assertNotEqual(aapl.listing_date, date(2000, 1, 1))
        twtr_ipo = master.get_as_of("PILOT:TWTR", datetime(2013, 11, 8, tzinfo=UTC))
        self.assertEqual(twtr_ipo.listing_date, date(2013, 11, 7))
        self.assertNotEqual(twtr_ipo.listing_date, date(2000, 1, 1))

        # -- FB -> META historical symbol mapping is time-bounded, not back-projected. --
        during_fb_era = datetime(2015, 6, 1, tzinfo=UTC)
        after_rename = datetime(2023, 1, 1, tzinfo=UTC)
        self.assertEqual(master.resolve_symbol("FB", "XNAS", during_fb_era).instrument_id, "PILOT:META")
        self.assertEqual(master.resolve_symbol("META", "XNAS", after_rename).instrument_id, "PILOT:META")
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol("META", "XNAS", during_fb_era)
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol("FB", "XNAS", after_rename)

        # -- Databento-namespace PIT identifier resolution follows the same rename. --
        # effective_at is the real historical moment being asked about; known_at is today
        # (this onboarding's own run time) -- resolve_identifier_point_in_time is the one
        # method in this master that correctly separates the two, unlike resolve_symbol().
        resolved_old = master.resolve_identifier_point_in_time(
            DATABENTO_RAW_SYMBOL_NAMESPACE, "FB", during_fb_era, registered_at
        )
        resolved_new = master.resolve_identifier_point_in_time(
            DATABENTO_RAW_SYMBOL_NAMESPACE, "META", after_rename, registered_at
        )
        self.assertEqual(resolved_old.instrument_id, "PILOT:META")
        self.assertEqual(resolved_new.instrument_id, "PILOT:META")

        # -- TWTR is active before, and delisted after, its real 2022-10-28 delisting date. --
        before_delisting = datetime(2022, 10, 27, tzinfo=UTC)
        after_delisting = datetime(2022, 10, 29, tzinfo=UTC)
        self.assertEqual(
            master.get_as_of("PILOT:TWTR", before_delisting).lifecycle_status, LifecycleStatus.ACTIVE
        )
        self.assertEqual(
            master.get_as_of("PILOT:TWTR", after_delisting).lifecycle_status, LifecycleStatus.DELISTED
        )
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol("TWTR", "XNYS", after_delisting)
        self.assertEqual(
            master.resolve_symbol("TWTR", "XNYS", before_delisting).instrument_id, "PILOT:TWTR"
        )

        # -- GOOGL is its own instrument, dated to the real 2014-04-03 reclassification, --
        # -- never confused with GOOG (which does not exist in this universe at all). --
        googl = master.get_as_of("PILOT:GOOGL", registered_at)
        self.assertEqual(googl.canonical_symbol, "GOOGL")
        self.assertEqual(googl.listing_date, date(2014, 4, 3))
        with self.assertRaises(InstrumentResolutionError):
            master.get_as_of("PILOT:GOOG", registered_at)

        # -- ETF venue resolution: NYSE Arca vs. Nasdaq-listed ETFs are correctly distinguished. --
        expected_venue = {
            "PILOT:SPY": "ARCX", "PILOT:GLD": "ARCX", "PILOT:VTI": "ARCX", "PILOT:IVV": "ARCX",
            "PILOT:QQQ": "XNAS",
        }
        for instrument_id, venue in expected_venue.items():
            instrument = master.get_as_of(instrument_id, registered_at)
            self.assertEqual(instrument.venue, venue, instrument_id)
            self.assertEqual(instrument.asset_class.value, "ETF", instrument_id)

        # -- A recycled/duplicate identifier is rejected at write time, never silently ambiguous. --
        with self.assertRaisesRegex(InstrumentMappingConflictError, "identifier_mapping_overlap_or_duplicate"):
            master.add_identifier_mapping(IdentifierMapping(
                "PILOT:AAPL", IdentifierSourceKind.PROVIDER, DATABENTO_RAW_SYMBOL_NAMESPACE, "META",
                datetime(2023, 1, 1, tzinfo=UTC), None, registered_at, "test://deliberately-conflicting",
            ))

        # -- Restart/persistence correctness: a fresh connection recovers the same 16 instruments. --
        database.close()
        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        recovered = PostgresProfessionalInstrumentMaster(restarted)
        self.assertEqual(
            recovered.resolve_symbol("AAPL", "XNAS", datetime(2026, 9, 7, tzinfo=UTC)).instrument_id,
            "PILOT:AAPL",
        )
        with restarted.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM professional_instruments WHERE instrument_id LIKE 'PILOT:%'"
            )
            self.assertEqual(cursor.fetchone()[0], 16)
        restarted.close()


if __name__ == "__main__":
    unittest.main()
