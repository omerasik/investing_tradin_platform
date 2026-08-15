"""Canonical instrument master; symbols are venue-qualified attributes, never primary keys."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import AssetClass, Instrument


class InstrumentAlreadyExistsError(ValueError):
    pass


class InstrumentNotFoundError(KeyError):
    pass


class InstrumentIdentifierConflictError(ValueError):
    pass


class SymbolHistoryConflictError(ValueError):
    pass


class TradingCalendarError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InstrumentIdentifier:
    instrument_id: str
    namespace: str
    value: str


@dataclass(frozen=True, slots=True)
class SymbolHistory:
    instrument_id: str
    symbol: str
    venue: str
    valid_from: datetime
    valid_until: datetime | None


@dataclass(frozen=True, slots=True)
class TradingSession:
    venue: str
    weekday: int
    opens_at: time
    closes_at: time
    timezone_name: str


@dataclass(frozen=True, slots=True)
class InstrumentRiskProfile:
    instrument_id: str
    sector: str
    country: str
    correlation_cluster: str
    beta: Decimal
    duration: Decimal
    factors: dict[str, Decimal]
    effective_at: datetime
    ingested_at: datetime


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(ZoneInfo("UTC")).isoformat()


def _parse_timestamp(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class SQLiteInstrumentStore:
    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS instruments (
                instrument_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                venue TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                quote_currency TEXT NOT NULL,
                tick_size TEXT NOT NULL,
                lot_size TEXT NOT NULL,
                delisted_at TEXT
            )
            """
        )
        self._connection.execute("""CREATE TABLE IF NOT EXISTS instrument_risk_profiles (
            profile_id INTEGER PRIMARY KEY AUTOINCREMENT, instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
            sector TEXT NOT NULL, country TEXT NOT NULL, correlation_cluster TEXT NOT NULL, beta TEXT NOT NULL,
            duration TEXT NOT NULL, factors_json TEXT NOT NULL, effective_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
            UNIQUE(instrument_id, effective_at, ingested_at))""")
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(instruments)")}
        if "delisted_at" not in columns:
            self._connection.execute("ALTER TABLE instruments ADD COLUMN delisted_at TEXT")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS instrument_identifiers (
                namespace TEXT NOT NULL,
                value TEXT NOT NULL,
                instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
                PRIMARY KEY (namespace, value)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS instrument_symbol_history (
                instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
                symbol TEXT NOT NULL,
                venue TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_until TEXT,
                PRIMARY KEY (instrument_id, symbol, venue, valid_from)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trading_sessions (
                venue TEXT NOT NULL,
                weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
                opens_at TEXT NOT NULL,
                closes_at TEXT NOT NULL,
                timezone_name TEXT NOT NULL,
                PRIMARY KEY (venue, weekday, opens_at, closes_at)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_holidays (
                venue TEXT NOT NULL,
                holiday_date TEXT NOT NULL,
                PRIMARY KEY (venue, holiday_date)
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def register(self, instrument: Instrument) -> None:
        try:
            self._connection.execute(
                "INSERT INTO instruments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    instrument.instrument_id,
                    instrument.symbol,
                    instrument.venue,
                    instrument.asset_class.value,
                    instrument.quote_currency,
                    str(instrument.tick_size),
                    str(instrument.lot_size),
                    _utc_timestamp(instrument.delisted_at) if instrument.delisted_at else None,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise InstrumentAlreadyExistsError(instrument.instrument_id) from error
        self._connection.commit()

    def get(self, instrument_id: str) -> Instrument:
        row = self._connection.execute(
            "SELECT instrument_id, symbol, venue, asset_class, quote_currency, tick_size, lot_size, delisted_at "
            "FROM instruments WHERE instrument_id = ?",
            (instrument_id,),
        ).fetchone()
        if row is None:
            raise InstrumentNotFoundError(instrument_id)
        return Instrument(
            instrument_id=row[0],
            symbol=row[1],
            venue=row[2],
            asset_class=AssetClass(row[3]),
            quote_currency=row[4],
            tick_size=Decimal(row[5]),
            lot_size=Decimal(row[6]),
            delisted_at=_parse_timestamp(row[7]),
        )

    def append_risk_profile(self, profile: InstrumentRiskProfile) -> None:
        self.get(profile.instrument_id)
        if not profile.sector.strip() or not profile.country.strip() or not profile.correlation_cluster.strip() or profile.effective_at.tzinfo is None or profile.effective_at.utcoffset() is None or profile.ingested_at.tzinfo is None or profile.ingested_at.utcoffset() is None or profile.ingested_at < profile.effective_at or not all(name.strip() for name in profile.factors):
            raise ValueError("invalid_instrument_risk_profile")
        try:
            self._connection.execute("INSERT INTO instrument_risk_profiles (instrument_id, sector, country, correlation_cluster, beta, duration, factors_json, effective_at, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (profile.instrument_id, profile.sector, profile.country, profile.correlation_cluster, str(profile.beta), str(profile.duration), json.dumps({key: str(value) for key, value in sorted(profile.factors.items())}), _utc_timestamp(profile.effective_at), _utc_timestamp(profile.ingested_at)))
        except sqlite3.IntegrityError as error:
            raise ValueError("duplicate_instrument_risk_profile") from error
        self._connection.commit()

    def risk_profile_as_of(self, instrument_id: str, decision_at: datetime) -> InstrumentRiskProfile:
        timestamp = _utc_timestamp(decision_at)
        row = self._connection.execute("SELECT instrument_id, sector, country, correlation_cluster, beta, duration, factors_json, effective_at, ingested_at FROM instrument_risk_profiles WHERE instrument_id = ? AND effective_at <= ? AND ingested_at <= ? ORDER BY effective_at DESC, ingested_at DESC, profile_id DESC LIMIT 1", (instrument_id, timestamp, timestamp)).fetchone()
        if row is None:
            raise InstrumentNotFoundError(f"risk profile unavailable: {instrument_id}")
        return InstrumentRiskProfile(row[0], row[1], row[2], row[3], Decimal(row[4]), Decimal(row[5]), {key: Decimal(value) for key, value in json.loads(row[6]).items()}, datetime.fromisoformat(row[7]), datetime.fromisoformat(row[8]))

    def find_by_symbol(self, symbol: str) -> list[Instrument]:
        identifiers = self._connection.execute(
            "SELECT instrument_id FROM instruments WHERE symbol = ? ORDER BY venue, instrument_id", (symbol,)
        ).fetchall()
        return [self.get(row[0]) for row in identifiers]

    def add_identifier(self, identifier: InstrumentIdentifier) -> None:
        self.get(identifier.instrument_id)
        if not identifier.namespace.strip() or not identifier.value.strip():
            raise ValueError("identifier namespace and value are required")
        try:
            self._connection.execute(
                "INSERT INTO instrument_identifiers VALUES (?, ?, ?)",
                (identifier.namespace, identifier.value, identifier.instrument_id),
            )
        except sqlite3.IntegrityError as error:
            raise InstrumentIdentifierConflictError(f"{identifier.namespace}:{identifier.value}") from error
        self._connection.commit()

    def find_by_identifier(self, namespace: str, value: str) -> Instrument:
        row = self._connection.execute(
            "SELECT instrument_id FROM instrument_identifiers WHERE namespace = ? AND value = ?",
            (namespace, value),
        ).fetchone()
        if row is None:
            raise InstrumentNotFoundError(f"{namespace}:{value}")
        return self.get(row[0])

    def add_symbol_history(self, history: SymbolHistory) -> None:
        self.get(history.instrument_id)
        if not history.symbol.strip() or not history.venue.strip():
            raise ValueError("symbol and venue are required")
        valid_from = _utc_timestamp(history.valid_from)
        valid_until = _utc_timestamp(history.valid_until) if history.valid_until else None
        if valid_until is not None and valid_until <= valid_from:
            raise ValueError("symbol history valid_until must follow valid_from")
        overlap = self._connection.execute(
            """
            SELECT 1 FROM instrument_symbol_history
            WHERE symbol = ? AND venue = ?
              AND valid_from < COALESCE(?, '9999-12-31T23:59:59+00:00')
              AND COALESCE(valid_until, '9999-12-31T23:59:59+00:00') > ?
            """,
            (history.symbol, history.venue, valid_until, valid_from),
        ).fetchone()
        if overlap is not None:
            raise SymbolHistoryConflictError(f"ambiguous symbol history: {history.venue}:{history.symbol}")
        self._connection.execute(
            "INSERT INTO instrument_symbol_history VALUES (?, ?, ?, ?, ?)",
            (history.instrument_id, history.symbol, history.venue, valid_from, valid_until),
        )
        self._connection.commit()

    def resolve_symbol(self, symbol: str, venue: str, as_of: datetime) -> Instrument:
        timestamp = _utc_timestamp(as_of)
        row = self._connection.execute(
            """
            SELECT instrument_id FROM instrument_symbol_history
            WHERE symbol = ? AND venue = ? AND valid_from <= ?
              AND (valid_until IS NULL OR valid_until > ?)
            """,
            (symbol, venue, timestamp, timestamp),
        ).fetchone()
        if row is None:
            raise InstrumentNotFoundError(f"{venue}:{symbol} at {timestamp}")
        return self.get(row[0])

    def delist(self, instrument_id: str, delisted_at: datetime) -> None:
        timestamp = _utc_timestamp(delisted_at)
        instrument = self.get(instrument_id)
        if instrument.delisted_at is not None:
            raise ValueError(f"instrument already delisted: {instrument_id}")
        self._connection.execute("UPDATE instruments SET delisted_at = ? WHERE instrument_id = ?", (timestamp, instrument_id))
        self._connection.execute(
            """
            UPDATE instrument_symbol_history SET valid_until = ?
            WHERE instrument_id = ? AND valid_from < ? AND valid_until IS NULL
            """,
            (timestamp, instrument_id, timestamp),
        )
        self._connection.commit()

    def add_trading_session(self, session: TradingSession) -> None:
        if not session.venue.strip() or not 0 <= session.weekday <= 6:
            raise TradingCalendarError("venue and weekday are required")
        if session.closes_at <= session.opens_at:
            raise TradingCalendarError("overnight sessions are not supported")
        try:
            ZoneInfo(session.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise TradingCalendarError(f"unknown timezone: {session.timezone_name}") from error
        try:
            self._connection.execute(
                "INSERT INTO trading_sessions VALUES (?, ?, ?, ?, ?)",
                (session.venue, session.weekday, session.opens_at.isoformat(), session.closes_at.isoformat(), session.timezone_name),
            )
        except sqlite3.IntegrityError as error:
            raise TradingCalendarError("duplicate trading session") from error
        self._connection.commit()

    def add_market_holiday(self, venue: str, holiday: date) -> None:
        if not venue.strip():
            raise TradingCalendarError("venue is required")
        self._connection.execute("INSERT OR IGNORE INTO market_holidays VALUES (?, ?)", (venue, holiday.isoformat()))
        self._connection.commit()

    def is_trading_session(self, venue: str, observed_at: datetime) -> bool:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise TradingCalendarError("observed_at must be timezone-aware")
        rows = self._connection.execute(
            "SELECT weekday, opens_at, closes_at, timezone_name FROM trading_sessions WHERE venue = ?",
            (venue,),
        ).fetchall()
        for weekday, opens_at, closes_at, timezone_name in rows:
            local = observed_at.astimezone(ZoneInfo(timezone_name))
            holiday = self._connection.execute(
                "SELECT 1 FROM market_holidays WHERE venue = ? AND holiday_date = ?", (venue, local.date().isoformat())
            ).fetchone()
            if holiday is None and local.weekday() == weekday and time.fromisoformat(opens_at) <= local.time().replace(tzinfo=None) < time.fromisoformat(closes_at):
                return True
        return False
