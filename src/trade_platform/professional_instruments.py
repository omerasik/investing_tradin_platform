"""PostgreSQL-authoritative professional instrument and trading-calendar master."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import cast
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import AssetClass
from .persistence import PostgresDatabase


class InstrumentMasterError(ValueError):
    pass


class InstrumentResolutionError(InstrumentMasterError):
    pass


class AmbiguousInstrumentMappingError(InstrumentResolutionError):
    pass


class InstrumentMappingConflictError(InstrumentMasterError):
    pass


class ProfessionalCalendarError(InstrumentMasterError):
    pass


class InstrumentType(StrEnum):
    COMMON_STOCK = "COMMON_STOCK"
    ETF = "ETF"
    SPOT_FX = "SPOT_FX"
    SPOT_CRYPTO = "SPOT_CRYPTO"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FIXED_INCOME = "FIXED_INCOME"


class LifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELISTED = "DELISTED"


class SessionType(StrEnum):
    US_EQUITY = "US_EQUITY"
    FX_24X5 = "FX_24X5"
    CRYPTO_24X7 = "CRYPTO_24X7"


class RepresentationKind(StrEnum):
    DIRECT = "DIRECT"
    ETF_PROXY = "ETF_PROXY"
    SPOT = "SPOT"
    FUTURE = "FUTURE"


class IdentifierSourceKind(StrEnum):
    PROVIDER = "PROVIDER"
    BROKER = "BROKER"
    STANDARD = "STANDARD"


class CalendarExceptionType(StrEnum):
    HOLIDAY = "HOLIDAY"
    EARLY_CLOSE = "EARLY_CLOSE"


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InstrumentMasterError(f"{name}_must_be_timezone_aware")


def _require_code(value: str, name: str, *, length: int | None = None) -> None:
    if not value.strip() or (length is not None and len(value) != length):
        raise InstrumentMasterError(f"invalid_{name}")


@dataclass(frozen=True, slots=True)
class ProfessionalInstrument:
    instrument_id: str
    asset_class: AssetClass
    instrument_type: InstrumentType
    exchange_name: str
    venue: str
    mic: str | None
    canonical_symbol: str
    listing_date: date
    base_currency: str
    quote_currency: str
    settlement_currency: str
    contract_multiplier: Decimal
    contract_size: Decimal
    tick_size: Decimal
    lot_size: Decimal
    price_precision: int
    quantity_precision: int
    trading_timezone: str
    market_session_type: SessionType
    representation_kind: RepresentationKind
    registered_at: datetime
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE
    underlying_reference: str | None = None
    corporate_action_reference: str | None = None
    isin: str | None = None
    cusip: str | None = None
    contract_code: str | None = None
    expiration_date: date | None = None
    first_notice_date: date | None = None
    last_trade_date: date | None = None
    continuous_parent_id: str | None = None
    roll_rule: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.instrument_id, "instrument_id"),
            (self.exchange_name, "exchange_name"),
            (self.venue, "venue"),
            (self.canonical_symbol, "canonical_symbol"),
        ):
            _require_code(value, name)
        for value, name in (
            (self.base_currency, "base_currency"),
            (self.quote_currency, "quote_currency"),
            (self.settlement_currency, "settlement_currency"),
        ):
            _require_code(value, name, length=3)
        if min(self.contract_multiplier, self.contract_size, self.tick_size, self.lot_size) <= 0:
            raise InstrumentMasterError("invalid_instrument_units")
        if not 0 <= self.price_precision <= 18 or not 0 <= self.quantity_precision <= 18:
            raise InstrumentMasterError("invalid_instrument_precision")
        try:
            ZoneInfo(self.trading_timezone)
        except ZoneInfoNotFoundError as error:
            raise InstrumentMasterError("invalid_trading_timezone") from error
        _require_aware(self.registered_at, "registered_at")
        if self.representation_kind is RepresentationKind.ETF_PROXY and not self.underlying_reference:
            raise InstrumentMasterError("etf_proxy_requires_underlying_reference")
        if self.instrument_type is InstrumentType.FUTURE:
            required = (self.contract_code, self.expiration_date, self.last_trade_date, self.roll_rule)
            if any(value is None for value in required):
                raise InstrumentMasterError("future_contract_metadata_incomplete")


@dataclass(frozen=True, slots=True)
class SymbolMapping:
    instrument_id: str
    venue: str
    symbol: str
    valid_from: datetime
    valid_until: datetime | None
    ingested_at: datetime
    source_reference: str
    mapping_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        for value, name in (
            (self.instrument_id, "instrument_id"),
            (self.venue, "venue"),
            (self.symbol, "symbol"),
            (self.source_reference, "source_reference"),
        ):
            _require_code(value, name)
        _require_aware(self.valid_from, "valid_from")
        _require_aware(self.ingested_at, "ingested_at")
        if self.valid_until is not None:
            _require_aware(self.valid_until, "valid_until")
            if self.valid_until <= self.valid_from:
                raise InstrumentMasterError("invalid_symbol_mapping_range")
        if self.ingested_at < self.valid_from:
            raise InstrumentMasterError("symbol_mapping_ingested_before_valid")


@dataclass(frozen=True, slots=True)
class IdentifierMapping:
    instrument_id: str
    source_kind: IdentifierSourceKind
    namespace: str
    value: str
    valid_from: datetime
    valid_until: datetime | None
    ingested_at: datetime
    source_reference: str
    mapping_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        for value, name in (
            (self.instrument_id, "instrument_id"),
            (self.namespace, "namespace"),
            (self.value, "identifier_value"),
            (self.source_reference, "source_reference"),
        ):
            _require_code(value, name)
        _require_aware(self.valid_from, "valid_from")
        _require_aware(self.ingested_at, "ingested_at")
        if self.valid_until is not None:
            _require_aware(self.valid_until, "valid_until")
            if self.valid_until <= self.valid_from:
                raise InstrumentMasterError("invalid_identifier_mapping_range")
        if self.ingested_at < self.valid_from:
            raise InstrumentMasterError("identifier_mapping_ingested_before_valid")


@dataclass(frozen=True, slots=True)
class CalendarDefinition:
    calendar_id: UUID
    venue: str
    session_type: SessionType
    timezone_name: str
    valid_from: date
    valid_until: date | None
    ingested_at: datetime
    source_reference: str
    rollover_time: time | None = None

    def __post_init__(self) -> None:
        _require_code(self.venue, "calendar_venue")
        _require_code(self.source_reference, "calendar_source_reference")
        _require_aware(self.ingested_at, "calendar_ingested_at")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ProfessionalCalendarError("invalid_calendar_range")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ProfessionalCalendarError("invalid_calendar_timezone") from error


@dataclass(frozen=True, slots=True)
class WeeklySession:
    calendar_id: UUID
    weekday: int
    opens_at: time
    closes_at: time
    close_day_offset: int
    ingested_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.ingested_at, "session_ingested_at")
        if not 0 <= self.weekday <= 6 or self.close_day_offset not in (0, 1):
            raise ProfessionalCalendarError("invalid_weekly_session")
        if self.close_day_offset == 0 and self.closes_at <= self.opens_at:
            raise ProfessionalCalendarError("invalid_same_day_session")


@dataclass(frozen=True, slots=True)
class CalendarException:
    calendar_id: UUID
    session_date: date
    exception_type: CalendarExceptionType
    ingested_at: datetime
    source_reference: str
    closes_at: time | None = None
    exception_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _require_aware(self.ingested_at, "calendar_exception_ingested_at")
        _require_code(self.source_reference, "calendar_exception_source_reference")
        if (self.exception_type is CalendarExceptionType.HOLIDAY) != (self.closes_at is None):
            raise ProfessionalCalendarError("invalid_calendar_exception")


class PostgresProfessionalInstrumentMaster:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register(self, instrument: ProfessionalInstrument) -> None:
        if instrument.lifecycle_status is not LifecycleStatus.ACTIVE:
            raise InstrumentMasterError("registration_status_must_be_active")
        values = (
            instrument.instrument_id, instrument.asset_class.value, instrument.instrument_type.value,
            instrument.exchange_name, instrument.venue, instrument.mic, instrument.canonical_symbol,
            instrument.listing_date, instrument.base_currency, instrument.quote_currency,
            instrument.settlement_currency, instrument.contract_multiplier, instrument.contract_size,
            instrument.tick_size, instrument.lot_size, instrument.price_precision,
            instrument.quantity_precision, instrument.trading_timezone,
            instrument.market_session_type.value, instrument.representation_kind.value,
            instrument.underlying_reference, instrument.corporate_action_reference, instrument.isin,
            instrument.cusip, instrument.contract_code, instrument.expiration_date,
            instrument.first_notice_date, instrument.last_trade_date, instrument.continuous_parent_id,
            instrument.roll_rule, instrument.registered_at,
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO runtime_instruments VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)",
                    (instrument.instrument_id, instrument.canonical_symbol, instrument.venue,
                     instrument.asset_class.value, instrument.quote_currency, instrument.tick_size,
                     instrument.lot_size),
                )
                cursor.execute(
                    # Fixed placeholder count; no identifier or value is interpolated.
                    "INSERT INTO professional_instruments VALUES (" + ",".join(["%s"] * 31) + ")",  # nosec B608
                    values,
                )
                cursor.execute(
                    "INSERT INTO professional_instrument_lifecycle_events VALUES (%s,%s,'ACTIVE',%s,%s,%s)",
                    (uuid4(), instrument.instrument_id, instrument.registered_at,
                     instrument.registered_at, "initial_registration"),
                )
        except Exception as error:
            raise InstrumentMasterError("professional_instrument_registration_failed") from error

    def get_as_of(self, instrument_id: str, as_of: datetime) -> ProfessionalInstrument:
        _require_aware(as_of, "as_of")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT p.*, COALESCE((SELECT status FROM professional_instrument_lifecycle_events e WHERE e.instrument_id=p.instrument_id AND e.effective_at<=%s AND e.ingested_at<=%s ORDER BY e.effective_at DESC,e.ingested_at DESC LIMIT 1),'ACTIVE') FROM professional_instruments p WHERE p.instrument_id=%s AND p.registered_at<=%s",
                    (as_of, as_of, instrument_id, as_of),
                )
                row = cursor.fetchone()
        except Exception as error:
            raise InstrumentResolutionError("instrument_master_read_failed") from error
        if row is None:
            raise InstrumentResolutionError(f"instrument_not_found:{instrument_id}")
        return self._instrument_from_row(row)

    @staticmethod
    def _instrument_from_row(row: tuple[object, ...]) -> ProfessionalInstrument:
        return ProfessionalInstrument(
            instrument_id=str(row[0]), asset_class=AssetClass(str(row[1])),
            instrument_type=InstrumentType(str(row[2])), exchange_name=str(row[3]),
            venue=str(row[4]), mic=None if row[5] is None else str(row[5]),
            canonical_symbol=str(row[6]), listing_date=cast(date, row[7]), base_currency=str(row[8]),
            quote_currency=str(row[9]), settlement_currency=str(row[10]),
            contract_multiplier=Decimal(str(row[11])), contract_size=Decimal(str(row[12])),
            tick_size=Decimal(str(row[13])), lot_size=Decimal(str(row[14])), price_precision=int(str(row[15])),
            quantity_precision=int(str(row[16])), trading_timezone=str(row[17]),
            market_session_type=SessionType(str(row[18])),
            representation_kind=RepresentationKind(str(row[19])),
            underlying_reference=None if row[20] is None else str(row[20]),
            corporate_action_reference=None if row[21] is None else str(row[21]),
            isin=None if row[22] is None else str(row[22]),
            cusip=None if row[23] is None else str(row[23]),
            contract_code=None if row[24] is None else str(row[24]),
            expiration_date=cast(date | None, row[25]),
            first_notice_date=cast(date | None, row[26]),
            last_trade_date=cast(date | None, row[27]),
            continuous_parent_id=None if row[28] is None else str(row[28]),
            roll_rule=None if row[29] is None else str(row[29]),
            registered_at=cast(datetime, row[30]),
            lifecycle_status=LifecycleStatus(str(row[31])),
        )

    def add_symbol_mapping(self, mapping: SymbolMapping) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO professional_symbol_mappings VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (mapping.mapping_id, mapping.instrument_id, mapping.venue, mapping.symbol,
                     mapping.valid_from, mapping.valid_until, mapping.ingested_at,
                     mapping.source_reference),
                )
        except Exception as error:
            raise InstrumentMappingConflictError("symbol_mapping_overlap_or_duplicate") from error

    def add_identifier_mapping(self, mapping: IdentifierMapping) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO professional_identifier_mappings VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (mapping.mapping_id, mapping.instrument_id, mapping.source_kind.value,
                     mapping.namespace, mapping.value, mapping.valid_from, mapping.valid_until,
                     mapping.ingested_at, mapping.source_reference),
                )
        except Exception as error:
            raise InstrumentMappingConflictError("identifier_mapping_overlap_or_duplicate") from error

    def resolve_symbol(self, symbol: str, venue: str, as_of: datetime) -> ProfessionalInstrument:
        return self._resolve("professional_symbol_mappings", "symbol", symbol, "venue", venue, as_of)

    def resolve_identifier(self, namespace: str, value: str, as_of: datetime) -> ProfessionalInstrument:
        return self._resolve(
            "professional_identifier_mappings", "identifier_value", value, "namespace", namespace, as_of
        )

    def resolve_identifier_point_in_time(
        self, namespace: str, value: str, effective_at: datetime, known_at: datetime
    ) -> ProfessionalInstrument:
        """Resolve historical identity by its validity time and separate knowledge time."""
        _require_aware(effective_at, "effective_at")
        _require_aware(known_at, "known_at")
        if known_at < effective_at:
            raise InstrumentResolutionError("identifier_known_before_effective")
        statement = (
            "SELECT instrument_id FROM professional_identifier_mappings "
            "WHERE identifier_value=%s AND namespace=%s AND valid_from<=%s "
            "AND (valid_until IS NULL OR valid_until>%s) AND ingested_at<=%s "
            "AND NOT EXISTS (SELECT 1 FROM professional_instrument_lifecycle_events e "
            "WHERE e.instrument_id=professional_identifier_mappings.instrument_id "
            "AND e.status='DELISTED' AND e.effective_at<=%s AND e.ingested_at<=%s) "
            "ORDER BY valid_from DESC LIMIT 2"
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    statement,
                    (value, namespace, effective_at, effective_at, known_at, effective_at, known_at),
                )
                rows = cursor.fetchall()
        except Exception as error:
            raise InstrumentResolutionError("instrument_mapping_read_failed") from error
        if not rows:
            raise InstrumentResolutionError(f"mapping_not_found:{namespace}:{value}")
        if len(rows) != 1:
            raise AmbiguousInstrumentMappingError(f"ambiguous_mapping:{namespace}:{value}")
        return self.get_as_of(str(rows[0][0]), known_at)

    def _resolve(self, table: str, value_column: str, value: str, scope_column: str, scope: str,
                 as_of: datetime) -> ProfessionalInstrument:
        _require_aware(as_of, "as_of")
        if table not in {"professional_symbol_mappings", "professional_identifier_mappings"}:
            raise InstrumentResolutionError("unsupported_mapping_table")
        statement = (
            f"SELECT instrument_id FROM {table} WHERE {value_column}=%s AND {scope_column}=%s "  # nosec B608 - internal allow-list and fixed columns
            "AND valid_from<=%s AND (valid_until IS NULL OR valid_until>%s) AND ingested_at<=%s "
            "AND NOT EXISTS (SELECT 1 FROM professional_instrument_lifecycle_events e "
            f"WHERE e.instrument_id={table}.instrument_id AND e.status='DELISTED' "
            "AND e.effective_at<=%s AND e.ingested_at<=%s) ORDER BY valid_from DESC LIMIT 2"
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(statement, (value, scope, as_of, as_of, as_of, as_of, as_of))
                rows = cursor.fetchall()
        except Exception as error:
            raise InstrumentResolutionError("instrument_mapping_read_failed") from error
        if not rows:
            raise InstrumentResolutionError(f"mapping_not_found:{scope}:{value}")
        if len(rows) != 1:
            raise AmbiguousInstrumentMappingError(f"ambiguous_mapping:{scope}:{value}")
        return self.get_as_of(str(rows[0][0]), as_of)

    def delist(self, instrument_id: str, effective_at: datetime, ingested_at: datetime, reason: str) -> None:
        _require_aware(effective_at, "delisting_effective_at")
        _require_aware(ingested_at, "delisting_ingested_at")
        _require_code(reason, "delisting_reason")
        if ingested_at < effective_at:
            raise InstrumentMasterError("delisting_ingested_before_effective")
        self.get_as_of(instrument_id, effective_at)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO professional_instrument_lifecycle_events VALUES (%s,%s,'DELISTED',%s,%s,%s)",
                    (uuid4(), instrument_id, effective_at, ingested_at, reason),
                )
        except Exception as error:
            raise InstrumentMasterError("duplicate_or_invalid_delisting") from error

    def add_calendar(self, calendar: CalendarDefinition) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO professional_calendar_definitions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (calendar.calendar_id, calendar.venue, calendar.session_type.value,
                     calendar.timezone_name, calendar.valid_from, calendar.valid_until,
                     calendar.ingested_at, calendar.rollover_time, calendar.source_reference),
                )
        except Exception as error:
            raise ProfessionalCalendarError("calendar_overlap_or_duplicate") from error

    def add_weekly_session(self, session: WeeklySession) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO professional_calendar_weekly_sessions VALUES (%s,%s,%s,%s,%s,%s)",
                    (session.calendar_id, session.weekday, session.opens_at, session.closes_at,
                     session.close_day_offset, session.ingested_at),
                )
        except Exception as error:
            raise ProfessionalCalendarError("weekly_session_duplicate_or_invalid") from error

    def add_calendar_exception(self, exception: CalendarException) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO professional_calendar_exceptions VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (exception.exception_id, exception.calendar_id, exception.session_date,
                     exception.exception_type.value, exception.closes_at, exception.ingested_at,
                     exception.source_reference),
                )
        except Exception as error:
            raise ProfessionalCalendarError("calendar_exception_duplicate_or_invalid") from error

    def is_open(self, venue: str, observed_at: datetime, *, known_at: datetime | None = None) -> bool:
        _require_aware(observed_at, "observed_at")
        knowledge_time = observed_at if known_at is None else known_at
        _require_aware(knowledge_time, "known_at")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT calendar_id,timezone_name FROM professional_calendar_definitions WHERE venue=%s AND valid_from<=%s::date AND (valid_until IS NULL OR valid_until>%s::date) AND ingested_at<=%s ORDER BY valid_from DESC LIMIT 2",
                    (venue, observed_at, observed_at, knowledge_time),
                )
                calendars = cursor.fetchall()
                if len(calendars) != 1:
                    if not calendars:
                        raise ProfessionalCalendarError(f"calendar_not_found:{venue}")
                    raise ProfessionalCalendarError(f"ambiguous_calendar:{venue}")
                calendar_id, timezone_name = calendars[0]
                cursor.execute(
                    "SELECT weekday,opens_at,closes_at,close_day_offset FROM professional_calendar_weekly_sessions WHERE calendar_id=%s AND ingested_at<=%s ORDER BY weekday,opens_at",
                    (calendar_id, knowledge_time),
                )
                sessions = cursor.fetchall()
                cursor.execute(
                    "SELECT session_date,exception_type,closes_at FROM professional_calendar_exceptions WHERE calendar_id=%s AND ingested_at<=%s",
                    (calendar_id, knowledge_time),
                )
                exceptions = {row[0]: (str(row[1]), row[2]) for row in cursor.fetchall()}
        except ProfessionalCalendarError:
            raise
        except Exception as error:
            raise ProfessionalCalendarError("calendar_read_failed") from error
        zone = ZoneInfo(str(timezone_name))
        local = observed_at.astimezone(zone)
        for session_date in (local.date() - timedelta(days=1), local.date()):
            exception = exceptions.get(session_date)
            if exception is not None and exception[0] == CalendarExceptionType.HOLIDAY.value:
                continue
            for weekday, opens_at, closes_at, close_day_offset in sessions:
                if session_date.weekday() != int(weekday):
                    continue
                opened = datetime.combine(session_date, opens_at, zone)
                closed = datetime.combine(
                    session_date + timedelta(days=int(close_day_offset)), closes_at, zone
                )
                if exception is not None and exception[0] == CalendarExceptionType.EARLY_CLOSE.value:
                    closed = datetime.combine(session_date, exception[1], zone)
                if opened <= local < closed:
                    return True
        return False


_MVP_NAMESPACE = UUID("79b6c27a-6de1-4c53-9f21-179135b3bedf")


def mvp_instrument_universe(registered_at: datetime) -> tuple[ProfessionalInstrument, ...]:
    """Provider-neutral initial universe; these records grant no data or trading authority."""
    _require_aware(registered_at, "registered_at")

    def build(
        instrument_id: str,
        asset_class: AssetClass,
        instrument_type: InstrumentType,
        exchange_name: str,
        venue: str,
        mic: str | None,
        symbol: str,
        base_currency: str,
        tick_size: Decimal,
        price_precision: int,
        quantity_precision: int,
        timezone_name: str,
        session_type: SessionType,
        representation: RepresentationKind,
        *,
        lot_size: Decimal = Decimal(1),
        underlying_reference: str | None = None,
    ) -> ProfessionalInstrument:
        return ProfessionalInstrument(
            instrument_id, asset_class, instrument_type, exchange_name, venue, mic, symbol,
            date(2000, 1, 1), base_currency, "USD", "USD", Decimal(1), Decimal(1),
            tick_size, lot_size, price_precision, quantity_precision, timezone_name,
            session_type, representation, registered_at, LifecycleStatus.ACTIVE,
            underlying_reference,
        )

    return (
        build("US:XNAS:AAPL", AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ",
              "XNAS", "XNAS", "AAPL", "USD", Decimal("0.01"), 2, 0,
              "America/New_York", SessionType.US_EQUITY, RepresentationKind.DIRECT),
        build("US:ARCX:SPY", AssetClass.ETF, InstrumentType.ETF, "NYSE Arca", "ARCX",
              "ARCX", "SPY", "USD", Decimal("0.01"), 2, 0, "America/New_York",
              SessionType.US_EQUITY, RepresentationKind.DIRECT),
        build("FX:OTC:EURUSD", AssetClass.FOREX, InstrumentType.SPOT_FX, "OTC FX", "FX",
              None, "EURUSD", "EUR", Decimal("0.00001"), 5, 2, "America/New_York",
              SessionType.FX_24X5, RepresentationKind.SPOT, lot_size=Decimal("0.01")),
        build("US:ARCX:GLD", AssetClass.ETF, InstrumentType.ETF, "NYSE Arca", "ARCX",
              "ARCX", "GLD", "USD", Decimal("0.01"), 2, 0, "America/New_York",
              SessionType.US_EQUITY, RepresentationKind.ETF_PROXY,
              underlying_reference="GOLD; GLD is not XAUUSD spot or GC futures"),
        build("CRYPTO:SPOT:BTCUSD", AssetClass.CRYPTO, InstrumentType.SPOT_CRYPTO,
              "Crypto Spot", "CRYPTO", None, "BTCUSD", "BTC", Decimal("0.01"), 2, 8,
              "UTC", SessionType.CRYPTO_24X7, RepresentationKind.SPOT,
              lot_size=Decimal("0.00000001")),
        build("CRYPTO:SPOT:ETHUSD", AssetClass.CRYPTO, InstrumentType.SPOT_CRYPTO,
              "Crypto Spot", "CRYPTO", None, "ETHUSD", "ETH", Decimal("0.01"), 2, 8,
              "UTC", SessionType.CRYPTO_24X7, RepresentationKind.SPOT,
              lot_size=Decimal("0.00000001")),
    )


def standard_calendars(ingested_at: datetime) -> tuple[
    tuple[CalendarDefinition, tuple[WeeklySession, ...]], ...
]:
    """Versioned convention fixtures; holidays/early closes are separate source evidence."""
    _require_aware(ingested_at, "ingested_at")
    start = date(2000, 1, 1)
    equity_id = uuid5(_MVP_NAMESPACE, "calendar:US_EQUITY:v1")
    fx_id = uuid5(_MVP_NAMESPACE, "calendar:FX_24X5:v1")
    crypto_id = uuid5(_MVP_NAMESPACE, "calendar:CRYPTO_24X7:v1")
    equity = CalendarDefinition(equity_id, "ARCX", SessionType.US_EQUITY,
        "America/New_York", start, None, ingested_at, "fixture://us-equity-calendar-v1")
    equity_sessions = tuple(
        WeeklySession(equity_id, weekday, time(9, 30), time(16), 0, ingested_at)
        for weekday in range(5)
    )
    fx = CalendarDefinition(fx_id, "FX", SessionType.FX_24X5, "America/New_York",
        start, None, ingested_at, "fixture://fx-24x5-convention-v1", time(17))
    fx_sessions = (
        WeeklySession(fx_id, 6, time(17), time(0), 1, ingested_at),
        *(WeeklySession(fx_id, weekday, time(0), time(0), 1, ingested_at) for weekday in range(4)),
        WeeklySession(fx_id, 4, time(0), time(17), 0, ingested_at),
    )
    crypto = CalendarDefinition(crypto_id, "CRYPTO", SessionType.CRYPTO_24X7, "UTC",
        start, None, ingested_at, "fixture://crypto-24x7-convention-v1")
    crypto_sessions = tuple(
        WeeklySession(crypto_id, weekday, time(0), time(0), 1, ingested_at)
        for weekday in range(7)
    )
    return ((equity, equity_sessions), (fx, fx_sessions), (crypto, crypto_sessions))
