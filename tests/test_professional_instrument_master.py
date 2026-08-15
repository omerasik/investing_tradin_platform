"""Real PostgreSQL evidence for Cycle 10 instrument and calendar authority."""

import os
import unittest
from datetime import UTC, date, datetime, time


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ProfessionalInstrumentMasterIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace(
                "postgresql://", "postgresql+psycopg://", 1
            ),
        )
        command.upgrade(config, "head")

    def test_temporal_identity_lifecycle_calendars_and_restart(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.professional_instruments import (
            CalendarException,
            CalendarExceptionType,
            IdentifierMapping,
            IdentifierSourceKind,
            InstrumentMappingConflictError,
            InstrumentResolutionError,
            LifecycleStatus,
            PostgresProfessionalInstrumentMaster,
            RepresentationKind,
            SymbolMapping,
            mvp_instrument_universe,
            standard_calendars,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        master = PostgresProfessionalInstrumentMaster(database)
        registered_at = datetime(2023, 1, 1, tzinfo=UTC)
        instruments = mvp_instrument_universe(registered_at)
        for instrument in instruments:
            master.register(instrument)

        by_id = {instrument.instrument_id: instrument for instrument in instruments}
        self.assertEqual(
            {instrument.asset_class.value for instrument in instruments},
            {"EQUITY", "ETF", "FOREX", "CRYPTO"},
        )
        self.assertEqual(
            by_id["US:ARCX:GLD"].representation_kind, RepresentationKind.ETF_PROXY
        )
        self.assertIn("not XAUUSD spot or GC futures", by_id["US:ARCX:GLD"].underlying_reference)
        self.assertNotIn("XAUUSD", by_id)
        self.assertNotIn("GC", by_id)

        changed_at = datetime(2024, 6, 1, tzinfo=UTC)
        master.add_symbol_mapping(
            SymbolMapping(
                "US:XNAS:AAPL", "XNAS", "OLD", registered_at, changed_at,
                registered_at, "fixture://symbol-old",
            )
        )
        master.add_symbol_mapping(
            SymbolMapping(
                "US:XNAS:AAPL", "XNAS", "NEW", changed_at, None,
                changed_at, "fixture://symbol-new",
            )
        )
        self.assertEqual(
            master.resolve_symbol("OLD", "XNAS", datetime(2024, 5, 31, tzinfo=UTC)).instrument_id,
            "US:XNAS:AAPL",
        )
        self.assertEqual(
            master.resolve_symbol("NEW", "XNAS", changed_at).instrument_id,
            "US:XNAS:AAPL",
        )
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol("OLD", "XNAS", changed_at)

        provider_mapping = IdentifierMapping(
            "US:XNAS:AAPL", IdentifierSourceKind.PROVIDER, "FIXTURE:SYMBOL", "aapl-1",
            registered_at, None, registered_at, "fixture://provider-map",
        )
        master.add_identifier_mapping(provider_mapping)
        self.assertEqual(
            master.resolve_identifier("FIXTURE:SYMBOL", "aapl-1", changed_at).instrument_id,
            "US:XNAS:AAPL",
        )
        with self.assertRaisesRegex(
            InstrumentMappingConflictError, "identifier_mapping_overlap_or_duplicate"
        ):
            master.add_identifier_mapping(
                IdentifierMapping(
                    "US:ARCX:SPY", IdentifierSourceKind.PROVIDER, "FIXTURE:SYMBOL", "aapl-1",
                    registered_at, None, registered_at, "fixture://duplicate-provider-map",
                )
            )
        with self.assertRaisesRegex(
            InstrumentMappingConflictError, "symbol_mapping_overlap_or_duplicate"
        ):
            master.add_symbol_mapping(
                SymbolMapping(
                    "US:ARCX:SPY", "XNAS", "NEW", changed_at, None,
                    changed_at, "fixture://ambiguous-symbol-map",
                )
            )

        master.add_identifier_mapping(
            IdentifierMapping(
                "US:ARCX:SPY", IdentifierSourceKind.BROKER, "BROKER:FIXTURE", "spy-42",
                registered_at, None, registered_at, "fixture://broker-map",
            )
        )
        master.add_identifier_mapping(
            IdentifierMapping(
                "US:ARCX:SPY", IdentifierSourceKind.PROVIDER, "LATE:MAP", "spy-late",
                registered_at, None, datetime(2024, 7, 1, tzinfo=UTC), "fixture://late-map",
            )
        )
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_identifier(
                "LATE:MAP", "spy-late", datetime(2024, 6, 30, tzinfo=UTC)
            )
        self.assertEqual(
            master.resolve_identifier(
                "LATE:MAP", "spy-late", datetime(2024, 7, 2, tzinfo=UTC)
            ).instrument_id,
            "US:ARCX:SPY",
        )

        delisted_at = datetime(2024, 12, 1, tzinfo=UTC)
        master.delist("US:XNAS:AAPL", delisted_at, delisted_at, "fixture delisting")
        self.assertEqual(
            master.get_as_of("US:XNAS:AAPL", delisted_at).lifecycle_status,
            LifecycleStatus.DELISTED,
        )
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_symbol("NEW", "XNAS", delisted_at)

        calendar_ids = {}
        for calendar, sessions in standard_calendars(registered_at):
            master.add_calendar(calendar)
            calendar_ids[calendar.venue] = calendar.calendar_id
            for session in sessions:
                master.add_weekly_session(session)
        master.add_calendar_exception(
            CalendarException(
                calendar_ids["ARCX"], date(2024, 11, 28), CalendarExceptionType.HOLIDAY,
                registered_at, "fixture://thanksgiving",
            )
        )
        master.add_calendar_exception(
            CalendarException(
                calendar_ids["ARCX"], date(2024, 11, 29), CalendarExceptionType.EARLY_CLOSE,
                registered_at, "fixture://early-close", time(13),
            )
        )
        self.assertTrue(master.is_open("ARCX", datetime(2024, 11, 29, 17, 59, tzinfo=UTC)))
        self.assertFalse(master.is_open("ARCX", datetime(2024, 11, 29, 18, 1, tzinfo=UTC)))
        self.assertFalse(master.is_open("ARCX", datetime(2024, 11, 28, 17, tzinfo=UTC)))
        self.assertTrue(master.is_open("ARCX", datetime(2024, 3, 8, 14, 30, tzinfo=UTC)))
        self.assertTrue(master.is_open("ARCX", datetime(2024, 3, 11, 13, 30, tzinfo=UTC)))
        self.assertFalse(master.is_open("FX", datetime(2024, 3, 9, 17, tzinfo=UTC)))
        self.assertTrue(master.is_open("FX", datetime(2024, 3, 10, 22, tzinfo=UTC)))
        self.assertFalse(master.is_open("FX", datetime(2024, 3, 15, 22, tzinfo=UTC)))
        self.assertTrue(master.is_open("CRYPTO", datetime(2024, 3, 9, 17, tzinfo=UTC)))

        database.close()
        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        recovered = PostgresProfessionalInstrumentMaster(restarted)
        self.assertEqual(
            recovered.resolve_identifier(
                "BROKER:FIXTURE", "spy-42", datetime(2025, 1, 1, tzinfo=UTC)
            ).instrument_id,
            "US:ARCX:SPY",
        )
        self.assertTrue(recovered.is_open("CRYPTO", datetime(2025, 1, 4, 12, tzinfo=UTC)))
        with restarted.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM professional_instruments WHERE instrument_id = ANY(%s)",
                ([instrument.instrument_id for instrument in instruments],),
            )
            self.assertEqual(cursor.fetchone()[0], 6)
        restarted.close()


if __name__ == "__main__":
    unittest.main()
