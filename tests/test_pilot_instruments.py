"""Module 3G.1e.1: real PostgreSQL evidence for the corrected pilot instrument universe.

Companion to test_professional_instrument_master.py (the pre-existing fixture/demo
coverage, left completely untouched). Proves the two-clock (real effective time vs.
system knowledge time) correction: every ProfessionalInstrument.registered_at and
SymbolMapping/IdentifierMapping.ingested_at is the real onboarding timestamp, never a
backdated historical fact date -- and that historical point-in-time resolution still
works correctly through resolve_identifier_point_in_time()/resolve_symbol_point_in_time(),
which take effective_at and known_at as two separate parameters for exactly this reason.
One sequential test method, matching this suite's existing convention for this module.
"""

import os
import unittest
from datetime import UTC, date, datetime, timedelta


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

    def test_pilot_universe_two_clock_identity_lineage_and_restart(self) -> None:
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
        # onboarded_at is the real wall-clock moment this backfill runs -- deliberately
        # not equal to, and far later than, any of the historical dates below.
        onboarded_at = datetime(2026, 9, 7, tzinfo=UTC)

        for spec in PILOT_INSTRUMENTS:
            master.register(pilot_instrument(spec, onboarded_at))
        symbol_mappings = pilot_symbol_mappings(onboarded_at)
        for mapping in symbol_mappings:
            master.add_symbol_mapping(mapping)
        identifier_mappings = pilot_databento_identifier_mappings(onboarded_at)
        for mapping in identifier_mappings:
            master.add_identifier_mapping(mapping)
        for instrument_id, effective_date, reason, _source in pilot_delistings():
            effective_at = datetime.combine(effective_date, datetime.min.time(), tzinfo=UTC)
            master.delist(instrument_id, effective_at, onboarded_at, reason)

        # -- Real listing dates are not the fixture 2000-01-01 placeholder. --
        # get_as_of queries must be at/after onboarded_at now that registered_at is
        # honestly the onboarding time, not a backdated historical fact.
        aapl = master.get_as_of("PILOT:AAPL", onboarded_at)
        self.assertEqual(aapl.listing_date, date(1980, 12, 12))
        self.assertNotEqual(aapl.listing_date, date(2000, 1, 1))
        twtr = master.get_as_of("PILOT:TWTR", onboarded_at)
        self.assertEqual(twtr.listing_date, date(2013, 11, 7))
        self.assertNotEqual(twtr.listing_date, date(2000, 1, 1))

        # -- D: no pilot mapping has ingested_at == valid_from; every one is onboarded_at. --
        # None of this backfill has real contemporaneous ingestion evidence.
        for mapping in (*symbol_mappings, *identifier_mappings):
            self.assertEqual(mapping.ingested_at, onboarded_at)
            self.assertNotEqual(mapping.ingested_at, mapping.valid_from)

        # -- A: historical validity, via the two-clock resolver, not resolve_symbol(). --
        during_fb_era = datetime(2015, 6, 1, tzinfo=UTC)
        after_rename = datetime(2023, 1, 1, tzinfo=UTC)
        self.assertEqual(
            master.resolve_symbol_point_in_time("FB", "XNAS", during_fb_era, onboarded_at).instrument_id,
            "PILOT:META",
        )
        self.assertEqual(
            master.resolve_symbol_point_in_time("META", "XNAS", after_rename, onboarded_at).instrument_id,
            "PILOT:META",
        )
        # No back-projection: META must not resolve during the FB era, nor FB after the rename.
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol_point_in_time("META", "XNAS", during_fb_era, onboarded_at)
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol_point_in_time("FB", "XNAS", after_rename, onboarded_at)
        # The identifier-namespace resolver proves the same history independently.
        self.assertEqual(
            master.resolve_identifier_point_in_time(
                DATABENTO_RAW_SYMBOL_NAMESPACE, "FB", during_fb_era, onboarded_at
            ).instrument_id,
            "PILOT:META",
        )
        self.assertEqual(
            master.resolve_identifier_point_in_time(
                DATABENTO_RAW_SYMBOL_NAMESPACE, "META", after_rename, onboarded_at
            ).instrument_id,
            "PILOT:META",
        )

        # -- B: knowledge time -- a mapping onboarded at onboarded_at must NOT resolve --
        # -- when known_at is before onboarded_at, even for a fully valid effective_at. --
        before_onboarding = onboarded_at - timedelta(days=1)
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol_point_in_time("META", "XNAS", after_rename, before_onboarding)
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_identifier_point_in_time(
                DATABENTO_RAW_SYMBOL_NAMESPACE, "META", after_rename, before_onboarding
            )

        # -- C: backfill -- the same historical mapping resolves once known_at reaches --
        # -- or passes the real onboarding timestamp (already proven by A above; --
        # -- reconfirmed here at exactly onboarded_at, the earliest valid known_at). --
        self.assertEqual(
            master.resolve_symbol_point_in_time("FB", "XNAS", during_fb_era, onboarded_at).instrument_id,
            "PILOT:META",
        )

        # -- E: TWTR -- real delisting effective_at, onboarding-time ingested_at, --
        # -- historical resolution works with a post-onboarding known_at, and --
        # -- post-delisting effective-time resolution fails appropriately. --
        self.assertEqual(
            master.get_as_of("PILOT:TWTR", onboarded_at).lifecycle_status, LifecycleStatus.DELISTED
        )
        pre_delisting_2021 = datetime(2021, 6, 1, tzinfo=UTC)
        self.assertEqual(
            master.resolve_symbol_point_in_time("TWTR", "XNYS", pre_delisting_2021, onboarded_at).instrument_id,
            "PILOT:TWTR",
        )
        after_delisting = datetime(2022, 10, 29, tzinfo=UTC)
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol_point_in_time("TWTR", "XNYS", after_delisting, onboarded_at)

        # -- GOOGL final lineage: continuous Class A security since its real 2004-08-19 --
        # -- IPO under ticker GOOG, renamed to GOOGL in 2014 -- not the 2014 date itself, --
        # -- and never confused with the separate, newly-created Class C (which now --
        # -- holds the GOOG ticker and is not modeled in this pilot universe at all). --
        googl = master.get_as_of("PILOT:GOOGL", onboarded_at)
        self.assertEqual(googl.canonical_symbol, "GOOGL")
        self.assertEqual(googl.listing_date, date(2004, 8, 19))
        with self.assertRaises(InstrumentResolutionError):
            master.get_as_of("PILOT:GOOG", onboarded_at)
        during_goog_era = datetime(2010, 1, 1, tzinfo=UTC)
        after_googl_rename = datetime(2020, 1, 1, tzinfo=UTC)
        self.assertEqual(
            master.resolve_symbol_point_in_time("GOOG", "XNAS", during_goog_era, onboarded_at).instrument_id,
            "PILOT:GOOGL",
        )
        self.assertEqual(
            master.resolve_symbol_point_in_time("GOOGL", "XNAS", after_googl_rename, onboarded_at).instrument_id,
            "PILOT:GOOGL",
        )
        # GOOG must not resolve after the 2014 rename (that ticker now belongs to an
        # unmodeled, economically distinct Class C security -- not this instrument).
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol_point_in_time("GOOG", "XNAS", after_googl_rename, onboarded_at)

        # -- QQQ: fund inception (1999-03-10, AMEX) is the real listing_date; the current --
        # -- XNAS/QQQ symbol mapping is deliberately scoped to the pilot window and must --
        # -- NOT claim coverage before its disclosed 2015-01-01 scope boundary. --
        qqq = master.get_as_of("PILOT:QQQ", onboarded_at)
        self.assertEqual(qqq.listing_date, date(1999, 3, 10))
        self.assertEqual(qqq.venue, "XNAS")
        within_pilot_window = datetime(2021, 1, 1, tzinfo=UTC)
        before_scope_boundary = datetime(2010, 1, 1, tzinfo=UTC)
        self.assertEqual(
            master.resolve_symbol_point_in_time("QQQ", "XNAS", within_pilot_window, onboarded_at).instrument_id,
            "PILOT:QQQ",
        )
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol_point_in_time("QQQ", "XNAS", before_scope_boundary, onboarded_at)

        # -- ETF venue resolution: NYSE Arca vs. Nasdaq-listed ETFs are correctly distinguished. --
        expected_venue = {
            "PILOT:SPY": "ARCX", "PILOT:GLD": "ARCX", "PILOT:VTI": "ARCX", "PILOT:IVV": "ARCX",
            "PILOT:QQQ": "XNAS",
        }
        for instrument_id, venue in expected_venue.items():
            instrument = master.get_as_of(instrument_id, onboarded_at)
            self.assertEqual(instrument.venue, venue, instrument_id)
            self.assertEqual(instrument.asset_class.value, "ETF", instrument_id)

        # -- A recycled/duplicate identifier is rejected at write time, never silently ambiguous. --
        with self.assertRaisesRegex(InstrumentMappingConflictError, "identifier_mapping_overlap_or_duplicate"):
            master.add_identifier_mapping(IdentifierMapping(
                "PILOT:AAPL", IdentifierSourceKind.PROVIDER, DATABENTO_RAW_SYMBOL_NAMESPACE, "META",
                after_rename, None, onboarded_at, "test://deliberately-conflicting",
            ))

        # -- Restart/persistence correctness: a fresh connection recovers the same 16 instruments. --
        database.close()
        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        recovered = PostgresProfessionalInstrumentMaster(restarted)
        self.assertEqual(
            recovered.resolve_symbol_point_in_time(
                "AAPL", "XNAS", onboarded_at, onboarded_at + timedelta(days=1)
            ).instrument_id,
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
