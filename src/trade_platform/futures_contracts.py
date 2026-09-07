"""First-class futures contract, margin and continuous-series authority.

Module 3H.1 (roadmap NEXT-02 phase 1). This EXTENDS
``professional_instruments.PostgresProfessionalInstrumentMaster``; it is not a
second instrument master. Every listed contract must already be registered
there as an ``InstrumentType.FUTURE`` instrument, and this module refuses to
specify one that is not. Resolution always returns a real contract's
``instrument_id`` -- a continuous series is a policy-versioned derived view
over real contracts and is never itself a tradable instrument.

Three invariants this module exists to enforce:

1. **A contract's economics are explicit, not inferred.** ``tick_value`` is
   stored and required to equal ``tick_size * contract_multiplier`` exactly
   (in Python and again as a database CHECK). GC and MGC both quote gold in
   USD per troy ounce at a 0.10 tick; they differ only in multiplier (100 vs
   10) and therefore tick value ($10.00 vs $1.00). Storing tick value without
   binding it to the multiplier would let those two collapse into one another
   in exactly the way the platform's instrument rules forbid.

2. **Margin has two clocks.** Exchanges publish margin changes *before* they
   take effect, so ``known_at < effective_from`` is normal for a live feed
   while ``known_at > effective_from`` is normal for a backfill. Neither
   ordering is enforced; both timestamps are always retained and every
   point-in-time read gates them separately.

3. **A roll is a versioned policy decision, not a formula in a backtest.**
   Roll schedules are materialized from an approved, content-hashed policy and
   the contracts known at a stated knowledge time, then persisted as immutable
   non-overlapping members. A schedule can therefore be reproduced and audited
   rather than recomputed differently by each consumer.

Deliberately not provided here: continuous-price adjustment (the policy records
the declared ``adjustment_method``, but no price series is adjusted -- that
belongs with the historical bar authority), open-interest/volume-driven rolls
(fail closed until a real open-interest authority exists, roadmap NEXT-03), and
any exchange-published contract data. Nothing in this module retrieves data
from an exchange; it records what a caller supplies with its source reference.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import AssetClass
from .persistence import PostgresDatabase
from .professional_instruments import (
    InstrumentType,
    LifecycleStatus,
    PostgresProfessionalInstrumentMaster,
    ProfessionalInstrument,
    SessionType,
)


class FuturesAuthorityError(ValueError):
    """Base class: every failure path in this module is fail-closed."""


class FuturesContractSpecificationError(FuturesAuthorityError):
    pass


class FuturesMarginError(FuturesAuthorityError):
    pass


class ContinuousSeriesPolicyError(FuturesAuthorityError):
    pass


class ContinuousSeriesResolutionError(FuturesAuthorityError):
    pass


class SettlementType(StrEnum):
    PHYSICAL_DELIVERY = "PHYSICAL_DELIVERY"
    CASH_SETTLED = "CASH_SETTLED"


class MarginTier(StrEnum):
    SPECULATIVE = "SPECULATIVE"
    HEDGER = "HEDGER"


class RollTrigger(StrEnum):
    LAST_TRADE_DATE = "LAST_TRADE_DATE"
    FIRST_NOTICE_DATE = "FIRST_NOTICE_DATE"
    CALENDAR_DAYS_BEFORE_LAST_TRADE = "CALENDAR_DAYS_BEFORE_LAST_TRADE"
    CALENDAR_DAYS_BEFORE_FIRST_NOTICE = "CALENDAR_DAYS_BEFORE_FIRST_NOTICE"
    # Enumerated so a policy can name the professionally standard trigger, but
    # unusable until an open-interest/volume authority exists. Materializing a
    # schedule under this trigger raises rather than substituting a date rule.
    VOLUME_OPEN_INTEREST_CROSSOVER = "VOLUME_OPEN_INTEREST_CROSSOVER"


class ContinuousAdjustmentMethod(StrEnum):
    NONE = "NONE"
    BACK_ADJUSTED_DIFFERENCE = "BACK_ADJUSTED_DIFFERENCE"
    BACK_ADJUSTED_RATIO = "BACK_ADJUSTED_RATIO"


#: CME/ISO delivery-month letter codes, indexed by calendar month number.
MONTH_CODES: tuple[str, ...] = (
    "F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z",
)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FuturesAuthorityError(f"{name}_must_be_timezone_aware")


def _require_text(value: str, name: str, *, length: int | None = None) -> None:
    if not value.strip() or (length is not None and len(value) != length):
        raise FuturesAuthorityError(f"invalid_{name}")


def _require_zone(name: str, error: type[FuturesAuthorityError]) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as failure:
        raise error("invalid_trading_timezone") from failure


def _require_tick_value_identity(
    tick_value: Decimal, tick_size: Decimal, multiplier: Decimal, scope: str
) -> None:
    if min(tick_value, tick_size, multiplier) <= 0:
        raise FuturesContractSpecificationError(f"invalid_{scope}_units")
    if tick_value != tick_size * multiplier:
        raise FuturesContractSpecificationError(f"{scope}_tick_value_not_multiplier_consistent")


@dataclass(frozen=True, slots=True)
class FuturesContractSeries:
    """The root of a futures product -- GC, MGC, ES, CL -- never a listed month."""

    series_id: str
    root_symbol: str
    exchange_name: str
    venue: str
    mic: str | None
    asset_class: AssetClass
    underlying_reference: str
    currency: str
    contract_multiplier: Decimal
    unit_of_measure: str
    tick_size: Decimal
    tick_value: Decimal
    price_precision: int
    quantity_precision: int
    settlement_type: SettlementType
    trading_timezone: str
    session_type: SessionType
    registered_at: datetime
    source_reference: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.series_id, "series_id"),
            (self.root_symbol, "root_symbol"),
            (self.exchange_name, "exchange_name"),
            (self.venue, "venue"),
            (self.underlying_reference, "underlying_reference"),
            (self.unit_of_measure, "unit_of_measure"),
            (self.source_reference, "source_reference"),
        ):
            _require_text(value, name)
        _require_text(self.currency, "currency", length=3)
        _require_tick_value_identity(
            self.tick_value, self.tick_size, self.contract_multiplier, "series"
        )
        if not 0 <= self.price_precision <= 18 or not 0 <= self.quantity_precision <= 18:
            raise FuturesContractSpecificationError("invalid_series_precision")
        _require_zone(self.trading_timezone, FuturesContractSpecificationError)
        _require_aware(self.registered_at, "series_registered_at")


@dataclass(frozen=True, slots=True)
class FuturesContractSpecification:
    """One listed delivery month, bound 1:1 to an already-registered instrument."""

    instrument_id: str
    series_id: str
    contract_code: str
    contract_year: int
    contract_month: int
    first_trade_date: date
    last_trade_date: date
    expiration_date: date
    settlement_date: date
    settlement_type: SettlementType
    contract_multiplier: Decimal
    tick_size: Decimal
    tick_value: Decimal
    registered_at: datetime
    source_reference: str
    first_notice_date: date | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.instrument_id, "instrument_id"),
            (self.series_id, "series_id"),
            (self.contract_code, "contract_code"),
            (self.source_reference, "source_reference"),
        ):
            _require_text(value, name)
        if not 1900 <= self.contract_year <= 2200 or not 1 <= self.contract_month <= 12:
            raise FuturesContractSpecificationError("invalid_contract_delivery_month")
        _require_tick_value_identity(
            self.tick_value, self.tick_size, self.contract_multiplier, "contract"
        )
        if not self.first_trade_date < self.last_trade_date <= self.expiration_date:
            raise FuturesContractSpecificationError("invalid_contract_trading_dates")
        if self.settlement_date < self.expiration_date:
            raise FuturesContractSpecificationError("settlement_before_expiration")
        if self.first_notice_date is not None and not (
            self.first_trade_date < self.first_notice_date <= self.expiration_date
        ):
            raise FuturesContractSpecificationError("invalid_first_notice_date")
        # A cash-settled contract has no delivery and therefore no notice
        # period; accepting one would silently license a first-notice roll on
        # a product that can never issue a delivery notice.
        if self.settlement_type is SettlementType.CASH_SETTLED and self.first_notice_date is not None:
            raise FuturesContractSpecificationError("cash_settled_contract_cannot_have_first_notice")
        _require_aware(self.registered_at, "contract_registered_at")

    @property
    def month_code(self) -> str:
        return MONTH_CODES[self.contract_month - 1]


@dataclass(frozen=True, slots=True)
class FuturesMarginRequirement:
    """Exchange margin metadata with independent effective and knowledge clocks."""

    series_id: str
    tier: MarginTier
    initial_margin: Decimal
    maintenance_margin: Decimal
    currency: str
    effective_from: datetime
    known_at: datetime
    source_reference: str
    source_hash: str
    instrument_id: str | None = None
    requirement_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        for value, name in (
            (self.series_id, "series_id"),
            (self.source_reference, "source_reference"),
            (self.source_hash, "source_hash"),
        ):
            _require_text(value, name)
        _require_text(self.currency, "currency", length=3)
        if self.instrument_id is not None:
            _require_text(self.instrument_id, "instrument_id")
        if min(self.initial_margin, self.maintenance_margin) <= 0:
            raise FuturesMarginError("invalid_margin_amount")
        if self.maintenance_margin > self.initial_margin:
            raise FuturesMarginError("maintenance_margin_exceeds_initial_margin")
        # No ordering is asserted between the two clocks -- see the module
        # docstring; an exchange announcement legitimately precedes its own
        # effective date, and a backfill legitimately follows it by years.
        _require_aware(self.effective_from, "margin_effective_from")
        _require_aware(self.known_at, "margin_known_at")


@dataclass(frozen=True, slots=True)
class ContinuousSeriesPolicy:
    """An approved, content-hashed roll rule. AI/LLM output cannot create one."""

    series_id: str
    policy_version: int
    roll_trigger: RollTrigger
    roll_offset_days: int
    adjustment_method: ContinuousAdjustmentMethod
    max_depth: int
    economic_rationale: str
    approved_at: datetime
    source_reference: str
    policy_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        for value, name in (
            (self.series_id, "series_id"),
            (self.economic_rationale, "economic_rationale"),
            (self.source_reference, "source_reference"),
        ):
            _require_text(value, name)
        if self.policy_version < 1:
            raise ContinuousSeriesPolicyError("invalid_policy_version")
        if not 0 <= self.roll_offset_days <= 365:
            raise ContinuousSeriesPolicyError("invalid_roll_offset_days")
        if not 1 <= self.max_depth <= 12:
            raise ContinuousSeriesPolicyError("invalid_max_depth")
        # An offset is meaningless for the two "roll exactly on the date"
        # triggers; silently ignoring a non-zero one would make two materially
        # different policies hash differently but behave identically.
        if self.roll_offset_days and self.roll_trigger in (
            RollTrigger.LAST_TRADE_DATE,
            RollTrigger.FIRST_NOTICE_DATE,
        ):
            raise ContinuousSeriesPolicyError("roll_offset_not_applicable_to_exact_date_trigger")
        _require_aware(self.approved_at, "policy_approved_at")

    def content_hash(self) -> str:
        payload = "|".join(
            (
                self.series_id,
                str(self.policy_version),
                self.roll_trigger.value,
                str(self.roll_offset_days),
                self.adjustment_method.value,
                str(self.max_depth),
                self.economic_rationale,
                self.source_reference,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ContinuousSeriesMember:
    """Which real contract a continuous series references over a closed window."""

    policy_id: UUID
    depth: int
    instrument_id: str
    effective_from: datetime
    effective_until: datetime
    known_at: datetime
    roll_reason: str
    member_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _require_text(self.instrument_id, "instrument_id")
        _require_text(self.roll_reason, "roll_reason")
        if not 1 <= self.depth <= 12:
            raise ContinuousSeriesPolicyError("invalid_continuous_depth")
        for value, name in (
            (self.effective_from, "member_effective_from"),
            (self.effective_until, "member_effective_until"),
            (self.known_at, "member_known_at"),
        ):
            _require_aware(value, name)
        if self.effective_until <= self.effective_from:
            raise ContinuousSeriesPolicyError("invalid_continuous_member_range")


def roll_date_for(policy: ContinuousSeriesPolicy, contract: FuturesContractSpecification) -> date:
    """The calendar date on which a continuous series stops referencing ``contract``.

    Offsets are calendar days, not exchange sessions. That is a deliberate,
    disclosed simplification: a session-aware offset would require this module
    to consult the professional calendar authority for the contract's venue,
    which is only meaningful once a real futures calendar has been onboarded.
    A calendar-day offset is reproducible and never silently wrong about which
    days an exchange was open -- it simply does not claim to know.
    """
    if policy.roll_trigger is RollTrigger.VOLUME_OPEN_INTEREST_CROSSOVER:
        raise ContinuousSeriesPolicyError(
            "volume_open_interest_roll_requires_open_interest_authority"
        )
    if policy.roll_trigger is RollTrigger.LAST_TRADE_DATE:
        return contract.last_trade_date
    if policy.roll_trigger is RollTrigger.CALENDAR_DAYS_BEFORE_LAST_TRADE:
        return contract.last_trade_date - timedelta(days=policy.roll_offset_days)
    if contract.first_notice_date is None:
        raise ContinuousSeriesPolicyError(
            f"first_notice_roll_requires_first_notice_date:{contract.contract_code}"
        )
    if policy.roll_trigger is RollTrigger.FIRST_NOTICE_DATE:
        return contract.first_notice_date
    return contract.first_notice_date - timedelta(days=policy.roll_offset_days)


def build_continuous_series_schedule(
    policy: ContinuousSeriesPolicy,
    contracts: Iterable[FuturesContractSpecification],
    *,
    trading_timezone: str,
    known_at: datetime,
    depth: int = 1,
) -> tuple[ContinuousSeriesMember, ...]:
    """Deterministically derive one depth's roll schedule -- pure, no database.

    Only contracts already registered at ``known_at`` participate, so a
    schedule materialized at a past knowledge time can never reference a
    contract this platform had not yet onboarded. The result is closed on both
    ends: the final member stops at its own roll, because the contract that
    would succeed it is not yet known. Resolution past that point fails closed
    rather than extending the last contract indefinitely.
    """
    _require_aware(known_at, "schedule_known_at")
    if not 1 <= depth <= policy.max_depth:
        raise ContinuousSeriesPolicyError("depth_exceeds_policy_max_depth")
    zone = _require_zone(trading_timezone, ContinuousSeriesPolicyError)

    known = [
        contract
        for contract in contracts
        if contract.series_id == policy.series_id and contract.registered_at <= known_at
    ]
    ordered = sorted(
        known, key=lambda item: (item.expiration_date, item.contract_year, item.contract_month)
    )
    if len(ordered) < depth:
        raise ContinuousSeriesPolicyError("insufficient_known_contracts_for_depth")

    def midnight(day: date) -> datetime:
        return datetime.combine(day, time(0, 0), zone)

    rolls: list[datetime] = []
    for contract in ordered:
        roll_day = roll_date_for(policy, contract)
        if roll_day <= contract.first_trade_date:
            raise ContinuousSeriesPolicyError(
                f"roll_offset_precedes_first_trade_date:{contract.contract_code}"
            )
        rolls.append(midnight(roll_day))
    if any(later <= earlier for earlier, later in pairwise(rolls)):
        raise ContinuousSeriesPolicyError("non_monotonic_roll_schedule")

    members: list[ContinuousSeriesMember] = []
    for index in range(depth - 1, len(ordered)):
        contract = ordered[index]
        predecessor = index - depth
        starts_at = (
            midnight(contract.first_trade_date) if predecessor < 0 else rolls[predecessor]
        )
        ends_at = rolls[predecessor + 1]
        if predecessor >= 0 and midnight(contract.first_trade_date) > starts_at:
            # The contract was not yet listed when the policy says it should
            # already have been the depth-N leg. Emitting the segment anyway
            # would fabricate a price history; narrowing it would leave a
            # silent gap in a supposedly continuous series.
            raise ContinuousSeriesPolicyError(
                f"contract_not_listed_at_roll:{contract.contract_code}"
            )
        members.append(
            ContinuousSeriesMember(
                policy_id=policy.policy_id,
                depth=depth,
                instrument_id=contract.instrument_id,
                effective_from=starts_at,
                effective_until=ends_at,
                known_at=known_at,
                roll_reason=f"{policy.roll_trigger.value}:offset={policy.roll_offset_days}",
            )
        )
    return tuple(members)


class PostgresFuturesContractAuthority:
    """Persists futures semantics alongside the existing instrument master."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    # ---- series and contracts -------------------------------------------------

    def register_series(self, series: FuturesContractSeries) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    # Fixed placeholder count; no identifier or value is interpolated.
                    "INSERT INTO futures_contract_series VALUES (" + ",".join(["%s"] * 19) + ")",  # nosec B608
                    (
                        series.series_id, series.root_symbol, series.exchange_name, series.venue,
                        series.mic, series.asset_class.value, series.underlying_reference,
                        series.currency, series.contract_multiplier, series.unit_of_measure,
                        series.tick_size, series.tick_value, series.price_precision,
                        series.quantity_precision, series.settlement_type.value,
                        series.trading_timezone, series.session_type, series.registered_at,
                        series.source_reference,
                    ),
                )
        except Exception as error:
            raise FuturesContractSpecificationError("futures_series_duplicate_or_invalid") from error

    def get_series(self, series_id: str) -> FuturesContractSeries:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM futures_contract_series WHERE series_id=%s", (series_id,)
                )
                row = cursor.fetchone()
        except Exception as error:
            raise FuturesContractSpecificationError("futures_series_read_failed") from error
        if row is None:
            raise FuturesContractSpecificationError(f"futures_series_not_found:{series_id}")
        return FuturesContractSeries(
            series_id=str(row[0]), root_symbol=str(row[1]), exchange_name=str(row[2]),
            venue=str(row[3]), mic=None if row[4] is None else str(row[4]),
            asset_class=AssetClass(str(row[5])), underlying_reference=str(row[6]),
            currency=str(row[7]), contract_multiplier=Decimal(str(row[8])),
            unit_of_measure=str(row[9]), tick_size=Decimal(str(row[10])),
            tick_value=Decimal(str(row[11])), price_precision=int(str(row[12])),
            quantity_precision=int(str(row[13])), settlement_type=SettlementType(str(row[14])),
            trading_timezone=str(row[15]), session_type=SessionType(str(row[16])),
            registered_at=cast(datetime, row[17]), source_reference=str(row[18]),
        )

    def specify_contract(self, contract: FuturesContractSpecification) -> None:
        """Bind a listed month to an existing FUTURE instrument, or fail closed."""
        series = self.get_series(contract.series_id)
        instrument = self._require_registered_future(contract.instrument_id)
        if instrument.venue != series.venue:
            raise FuturesContractSpecificationError(
                f"contract_venue_differs_from_series:{instrument.venue}!={series.venue}"
            )
        if instrument.quote_currency != series.currency:
            raise FuturesContractSpecificationError("contract_currency_differs_from_series")
        # A listed month may legitimately differ from its root only where the
        # exchange itself changed the spec; requiring that difference to be
        # stated per-contract keeps a silent multiplier mismatch from turning
        # a mini contract into a full-size one.
        if contract.settlement_type is not series.settlement_type:
            raise FuturesContractSpecificationError("contract_settlement_differs_from_series")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    # Fixed placeholder count; no identifier or value is interpolated.
                    "INSERT INTO futures_contract_specifications VALUES ("  # nosec B608
                    + ",".join(["%s"] * 17) + ")",
                    (
                        contract.instrument_id, contract.series_id, contract.contract_code,
                        contract.contract_year, contract.contract_month, contract.month_code,
                        contract.first_trade_date, contract.first_notice_date,
                        contract.last_trade_date, contract.expiration_date,
                        contract.settlement_date, contract.settlement_type.value,
                        contract.contract_multiplier, contract.tick_size, contract.tick_value,
                        contract.registered_at, contract.source_reference,
                    ),
                )
        except Exception as error:
            raise FuturesContractSpecificationError("futures_contract_duplicate_or_invalid") from error

    def contracts_for_series(
        self, series_id: str, *, known_at: datetime
    ) -> tuple[FuturesContractSpecification, ...]:
        _require_aware(known_at, "known_at")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM futures_contract_specifications WHERE series_id=%s "
                    "AND registered_at<=%s ORDER BY expiration_date",
                    (series_id, known_at),
                )
                rows = cursor.fetchall()
        except Exception as error:
            raise FuturesContractSpecificationError("futures_contract_read_failed") from error
        return tuple(self._contract_from_row(row) for row in rows)

    @staticmethod
    def _contract_from_row(row: Sequence[object]) -> FuturesContractSpecification:
        return FuturesContractSpecification(
            instrument_id=str(row[0]), series_id=str(row[1]), contract_code=str(row[2]),
            contract_year=int(str(row[3])), contract_month=int(str(row[4])),
            first_trade_date=cast(date, row[6]),
            first_notice_date=cast("date | None", row[7]),
            last_trade_date=cast(date, row[8]), expiration_date=cast(date, row[9]),
            settlement_date=cast(date, row[10]), settlement_type=SettlementType(str(row[11])),
            contract_multiplier=Decimal(str(row[12])), tick_size=Decimal(str(row[13])),
            tick_value=Decimal(str(row[14])), registered_at=cast(datetime, row[15]),
            source_reference=str(row[16]),
        )

    def _require_registered_future(self, instrument_id: str) -> ProfessionalInstrument:
        master = PostgresProfessionalInstrumentMaster(self._database)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT registered_at FROM professional_instruments WHERE instrument_id=%s",
                    (instrument_id,),
                )
                row = cursor.fetchone()
        except Exception as error:
            raise FuturesContractSpecificationError("instrument_master_read_failed") from error
        if row is None:
            raise FuturesContractSpecificationError(
                f"contract_instrument_not_registered:{instrument_id}"
            )
        instrument = master.get_as_of(instrument_id, cast(datetime, row[0]))
        if instrument.instrument_type is not InstrumentType.FUTURE:
            raise FuturesContractSpecificationError(
                f"instrument_is_not_a_future:{instrument.instrument_type.value}"
            )
        if instrument.lifecycle_status is not LifecycleStatus.ACTIVE:
            raise FuturesContractSpecificationError("instrument_not_active_at_registration")
        return instrument

    # ---- margin ---------------------------------------------------------------

    def record_margin_requirement(self, requirement: FuturesMarginRequirement) -> None:
        self.get_series(requirement.series_id)
        if requirement.instrument_id is not None:
            self._require_specified_contract(requirement.series_id, requirement.instrument_id)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    # Fixed placeholder count; no identifier or value is interpolated.
                    "INSERT INTO futures_margin_requirements VALUES ("  # nosec B608
                    + ",".join(["%s"] * 11) + ")",
                    (
                        requirement.requirement_id, requirement.series_id,
                        requirement.instrument_id, requirement.tier.value,
                        requirement.initial_margin, requirement.maintenance_margin,
                        requirement.currency, requirement.effective_from, requirement.known_at,
                        requirement.source_reference, requirement.source_hash,
                    ),
                )
        except Exception as error:
            raise FuturesMarginError("margin_requirement_duplicate_or_invalid") from error

    def margin_point_in_time(
        self,
        series_id: str,
        tier: MarginTier,
        *,
        effective_at: datetime,
        known_at: datetime,
        instrument_id: str | None = None,
    ) -> FuturesMarginRequirement:
        """The margin in force at ``effective_at`` as this platform knew it at ``known_at``.

        A contract-specific requirement outranks the series-level default; among
        equals the latest effective date wins, and among those the latest
        knowledge time (a revision supersedes what it revised).
        """
        _require_aware(effective_at, "effective_at")
        _require_aware(known_at, "known_at")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT requirement_id,series_id,instrument_id,tier,initial_margin,"
                    "maintenance_margin,currency,effective_from,known_at,source_reference,"
                    "source_hash FROM futures_margin_requirements WHERE series_id=%s AND tier=%s "
                    "AND (instrument_id=%s OR instrument_id IS NULL) "
                    "AND effective_from<=%s AND known_at<=%s "
                    "ORDER BY (instrument_id IS NULL), effective_from DESC, known_at DESC LIMIT 1",
                    (series_id, tier.value, instrument_id, effective_at, known_at),
                )
                row = cursor.fetchone()
        except Exception as error:
            raise FuturesMarginError("margin_requirement_read_failed") from error
        if row is None:
            raise FuturesMarginError(f"margin_requirement_not_available:{series_id}:{tier.value}")
        return FuturesMarginRequirement(
            requirement_id=cast(UUID, row[0]), series_id=str(row[1]),
            instrument_id=None if row[2] is None else str(row[2]), tier=MarginTier(str(row[3])),
            initial_margin=Decimal(str(row[4])), maintenance_margin=Decimal(str(row[5])),
            currency=str(row[6]), effective_from=cast(datetime, row[7]),
            known_at=cast(datetime, row[8]), source_reference=str(row[9]),
            source_hash=str(row[10]),
        )

    def _require_specified_contract(self, series_id: str, instrument_id: str) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM futures_contract_specifications "
                    "WHERE series_id=%s AND instrument_id=%s",
                    (series_id, instrument_id),
                )
                found = cursor.fetchone()
        except Exception as error:
            raise FuturesAuthorityError("futures_contract_read_failed") from error
        if found is None:
            raise FuturesAuthorityError(f"contract_not_specified_for_series:{instrument_id}")

    # ---- continuous series ----------------------------------------------------

    def register_continuous_policy(self, policy: ContinuousSeriesPolicy) -> None:
        self.get_series(policy.series_id)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    # Fixed placeholder count; no identifier or value is interpolated.
                    "INSERT INTO futures_continuous_series_policies VALUES ("  # nosec B608
                    + ",".join(["%s"] * 11) + ")",
                    (
                        policy.policy_id, policy.series_id, policy.policy_version,
                        policy.roll_trigger.value, policy.roll_offset_days,
                        policy.adjustment_method.value, policy.max_depth,
                        policy.economic_rationale, policy.content_hash(), policy.approved_at,
                        policy.source_reference,
                    ),
                )
        except Exception as error:
            raise ContinuousSeriesPolicyError("continuous_policy_duplicate_or_invalid") from error

    def get_continuous_policy(self, series_id: str, policy_version: int) -> ContinuousSeriesPolicy:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT policy_id,series_id,policy_version,roll_trigger,roll_offset_days,"
                    "adjustment_method,max_depth,economic_rationale,policy_hash,approved_at,"
                    "source_reference FROM futures_continuous_series_policies "
                    "WHERE series_id=%s AND policy_version=%s",
                    (series_id, policy_version),
                )
                row = cursor.fetchone()
        except Exception as error:
            raise ContinuousSeriesPolicyError("continuous_policy_read_failed") from error
        if row is None:
            raise ContinuousSeriesPolicyError(
                f"continuous_policy_not_found:{series_id}:v{policy_version}"
            )
        policy = ContinuousSeriesPolicy(
            policy_id=cast(UUID, row[0]), series_id=str(row[1]), policy_version=int(str(row[2])),
            roll_trigger=RollTrigger(str(row[3])), roll_offset_days=int(str(row[4])),
            adjustment_method=ContinuousAdjustmentMethod(str(row[5])),
            max_depth=int(str(row[6])), economic_rationale=str(row[7]),
            approved_at=cast(datetime, row[9]), source_reference=str(row[10]),
        )
        if policy.content_hash() != str(row[8]):
            raise ContinuousSeriesPolicyError("continuous_policy_hash_mismatch")
        return policy

    def materialize_continuous_series(
        self, series_id: str, policy_version: int, *, known_at: datetime, depth: int = 1
    ) -> tuple[ContinuousSeriesMember, ...]:
        """Derive and persist one depth's schedule atomically from stored evidence."""
        policy = self.get_continuous_policy(series_id, policy_version)
        if policy.approved_at > known_at:
            raise ContinuousSeriesPolicyError("policy_not_approved_at_known_at")
        series = self.get_series(series_id)
        members = build_continuous_series_schedule(
            policy,
            self.contracts_for_series(series_id, known_at=known_at),
            trading_timezone=series.trading_timezone,
            known_at=known_at,
            depth=depth,
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                for member in members:
                    cursor.execute(
                        "INSERT INTO futures_continuous_series_members "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            member.member_id, member.policy_id, member.depth,
                            member.instrument_id, member.effective_from, member.effective_until,
                            member.known_at, member.roll_reason,
                        ),
                    )
        except Exception as error:
            raise ContinuousSeriesPolicyError("continuous_member_overlap_or_duplicate") from error
        return members

    def resolve_continuous_contract(
        self,
        series_id: str,
        policy_version: int,
        *,
        effective_at: datetime,
        known_at: datetime,
        depth: int = 1,
    ) -> str:
        """The real contract ``instrument_id`` a continuous series pointed at, then."""
        _require_aware(effective_at, "effective_at")
        _require_aware(known_at, "known_at")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT m.instrument_id FROM futures_continuous_series_members m "
                    "JOIN futures_continuous_series_policies p ON p.policy_id=m.policy_id "
                    "WHERE p.series_id=%s AND p.policy_version=%s AND m.depth=%s "
                    "AND m.effective_from<=%s AND m.effective_until>%s "
                    "AND m.known_at<=%s AND p.approved_at<=%s LIMIT 2",
                    (
                        series_id, policy_version, depth, effective_at, effective_at,
                        known_at, known_at,
                    ),
                )
                rows = cursor.fetchall()
        except Exception as error:
            raise ContinuousSeriesResolutionError("continuous_member_read_failed") from error
        if not rows:
            raise ContinuousSeriesResolutionError(
                f"no_continuous_contract:{series_id}:v{policy_version}:depth{depth}"
            )
        if len(rows) != 1:
            raise ContinuousSeriesResolutionError(
                f"ambiguous_continuous_contract:{series_id}:v{policy_version}:depth{depth}"
            )
        return str(rows[0][0])
