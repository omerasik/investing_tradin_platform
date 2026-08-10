import unittest
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from trade_platform.domain import AssetClass, Instrument
from trade_platform.instruments import (
    InstrumentAlreadyExistsError,
    InstrumentIdentifier,
    InstrumentIdentifierConflictError,
    InstrumentNotFoundError,
    InstrumentRiskProfile,
    SQLiteInstrumentStore,
    SymbolHistory,
    SymbolHistoryConflictError,
    TradingCalendarError,
    TradingSession,
)


def sample(instrument_id: str, venue: str = "NYSE") -> Instrument:
    return Instrument(instrument_id, "SPY", venue, AssetClass.ETF, "USD", Decimal("0.01"), Decimal("1"))


class InstrumentStoreTests(unittest.TestCase):
    def test_risk_profile_is_point_in_time_and_survives_reopen(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "instruments.sqlite"
            store = SQLiteInstrumentStore(path); store.register(sample("US:NYSE:SPY"))
            store.append_risk_profile(InstrumentRiskProfile("US:NYSE:SPY", "INDEX", "US", "US_INDEX", Decimal("1"), Decimal("0"), {"market": Decimal("1")}, start, start))
            store.append_risk_profile(InstrumentRiskProfile("US:NYSE:SPY", "INDEX", "US", "US_INDEX", Decimal("0.8"), Decimal("0"), {"market": Decimal("0.8")}, start + timedelta(days=1), start + timedelta(days=2)))
            self.assertEqual(store.risk_profile_as_of("US:NYSE:SPY", start + timedelta(days=1, hours=12)).beta, Decimal("1"))
            store.close()
            reopened = SQLiteInstrumentStore(path)
            self.assertEqual(reopened.risk_profile_as_of("US:NYSE:SPY", start + timedelta(days=2)).factors, {"market": Decimal("0.8")})
            reopened.close()

    def test_symbol_can_map_to_multiple_venue_qualified_instruments(self) -> None:
        store = SQLiteInstrumentStore()
        store.register(sample("US:NYSE:SPY", "NYSE"))
        store.register(sample("US:ARCA:SPY", "ARCA"))
        self.assertEqual([item.instrument_id for item in store.find_by_symbol("SPY")], ["US:ARCA:SPY", "US:NYSE:SPY"])

    def test_primary_identifier_is_unique(self) -> None:
        store = SQLiteInstrumentStore(); instrument = sample("US:NYSE:SPY")
        store.register(instrument)
        with self.assertRaises(InstrumentAlreadyExistsError):
            store.register(instrument)

    def test_unknown_identifier_is_rejected(self) -> None:
        with self.assertRaises(InstrumentNotFoundError):
            SQLiteInstrumentStore().get("unknown")

    def test_namespaced_identifiers_are_unique_and_resolvable(self) -> None:
        store = SQLiteInstrumentStore()
        store.register(sample("US:NYSE:SPY"))
        store.register(sample("US:ARCA:SPY", "ARCA"))
        store.add_identifier(InstrumentIdentifier("US:NYSE:SPY", "ISIN", "US78462F1030"))
        self.assertEqual(store.find_by_identifier("ISIN", "US78462F1030").instrument_id, "US:NYSE:SPY")
        with self.assertRaises(InstrumentIdentifierConflictError):
            store.add_identifier(InstrumentIdentifier("US:ARCA:SPY", "ISIN", "US78462F1030"))

    def test_symbol_history_is_time_bounded_and_unambiguous(self) -> None:
        store = SQLiteInstrumentStore()
        store.register(sample("US:NYSE:ABC"))
        store.register(sample("US:NYSE:XYZ"))
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        changed = datetime(2025, 6, 1, tzinfo=timezone.utc)
        store.add_symbol_history(SymbolHistory("US:NYSE:ABC", "ABC", "NYSE", start, changed))
        store.add_symbol_history(SymbolHistory("US:NYSE:ABC", "XYZ", "NYSE", changed, None))
        self.assertEqual(store.resolve_symbol("ABC", "NYSE", start).instrument_id, "US:NYSE:ABC")
        self.assertEqual(store.resolve_symbol("XYZ", "NYSE", changed).instrument_id, "US:NYSE:ABC")
        with self.assertRaises(SymbolHistoryConflictError):
            store.add_symbol_history(SymbolHistory("US:NYSE:XYZ", "XYZ", "NYSE", changed, None))

    def test_delisting_closes_active_symbol_history(self) -> None:
        store = SQLiteInstrumentStore()
        store.register(sample("US:NYSE:ABC"))
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        delisted = datetime(2025, 5, 1, tzinfo=timezone.utc)
        store.add_symbol_history(SymbolHistory("US:NYSE:ABC", "ABC", "NYSE", start, None))
        store.delist("US:NYSE:ABC", delisted)
        self.assertEqual(store.get("US:NYSE:ABC").delisted_at, delisted)
        with self.assertRaises(InstrumentNotFoundError):
            store.resolve_symbol("ABC", "NYSE", delisted)

    def test_calendar_uses_venue_timezone_holidays_and_weekdays(self) -> None:
        store = SQLiteInstrumentStore()
        store.add_trading_session(TradingSession("NYSE", 0, time(9, 30), time(16), "America/New_York"))
        open_monday = datetime(2025, 1, 6, 15, tzinfo=timezone.utc)
        self.assertTrue(store.is_trading_session("NYSE", open_monday))
        store.add_market_holiday("NYSE", date(2025, 1, 6))
        self.assertFalse(store.is_trading_session("NYSE", open_monday))
        with self.assertRaises(TradingCalendarError):
            store.add_trading_session(TradingSession("NYSE", 1, time(16), time(9, 30), "America/New_York"))

    def test_identifier_symbol_history_and_calendar_survive_reopen(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "instruments.sqlite"
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            first = SQLiteInstrumentStore(database)
            first.register(sample("US:NYSE:ABC"))
            first.add_identifier(InstrumentIdentifier("US:NYSE:ABC", "BROKER:paper", "abc-42"))
            first.add_symbol_history(SymbolHistory("US:NYSE:ABC", "ABC", "NYSE", start, None))
            first.add_trading_session(TradingSession("NYSE", 0, time(9, 30), time(16), "America/New_York"))
            first.close()

            reopened = SQLiteInstrumentStore(database)
            self.assertEqual(reopened.find_by_identifier("BROKER:paper", "abc-42").instrument_id, "US:NYSE:ABC")
            self.assertEqual(reopened.resolve_symbol("ABC", "NYSE", start).instrument_id, "US:NYSE:ABC")
            self.assertTrue(reopened.is_trading_session("NYSE", datetime(2025, 1, 6, 15, tzinfo=timezone.utc)))
            reopened.close()


if __name__ == "__main__":
    unittest.main()
