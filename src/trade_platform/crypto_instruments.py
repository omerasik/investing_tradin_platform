"""First-class crypto instrument, funding-convention and venue-rule authority.

Module 3H.2 (roadmap NEXT-02 phase 2). This EXTENDS
``professional_instruments.PostgresProfessionalInstrumentMaster``; it is not a
second instrument registry. Every crypto instrument must already be registered
there with ``AssetClass.CRYPTO``, and this module refuses to specify one that
is not. Symbol history, identifier mappings, delisting and lifecycle all stay
with that existing authority.

What this module is for:

**Venue identity is part of the instrument.** ``BTCUSDT`` on one venue and
``BTCUSDT`` on another are different tradable instruments with different
order books, different trading rules and different counterparty risk. Every
specification is keyed by venue, resolution requires a venue, and a
symbol lookup that spans venues without one fails closed rather than picking a
winner.

**A spot pair, a perpetual and a dated future on the same underlying are three
different instruments.** ``resolve()`` takes the kind as a required argument,
so asking for BTC spot can never return a BTC perpetual. Each kind's
forbidden fields are rejected in Python and again by database CHECK
constraints: spot carries no expiry, no settlement style and no funding; a
perpetual carries no expiry; a dated future must have one.

**Static identity and venue-revised state are separate authorities.** What
makes an instrument *this* instrument -- kind, base/quote/settlement asset,
linear/inverse/quanto, multiplier, expiry -- lives in the specification and
never changes. What a venue revises -- tick size, quantity step, minimum
quantity and notional, precisions -- lives in ``crypto_venue_trading_rules``
with independent effective and knowledge clocks, because treating a
continuously-revised venue limit as static instrument metadata would make
every historical replay silently wrong.

Deliberately **not** here, and left to the NEXT-03 multi-asset market data
authority: funding-rate, mark-price, index-price, open-interest, trade and
order-book observations. This module records only whether a contract is
*subject* to funding and *which* reference-price semantics it requires --
never an observed value.

Deliberately **not** here at all: margin and leverage tiers. On a crypto venue
those are continuously-revised, position-size- and account-dependent state.
Modelling them as instrument metadata would invent an authority that belongs
to the account/risk layer (roadmap NEXT-10/NEXT-11).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import cast
from uuid import UUID, uuid4

from .domain import AssetClass
from .persistence import PostgresDatabase
from .professional_instruments import (
    MAX_CRYPTO_ASSET_CODE_LENGTH,
    InstrumentType,
    LifecycleStatus,
    PostgresProfessionalInstrumentMaster,
    ProfessionalInstrument,
)


class CryptoInstrumentError(ValueError):
    """Base class: every failure path in this module is fail-closed."""


class CryptoSpecificationError(CryptoInstrumentError):
    pass


class CryptoFundingConventionError(CryptoInstrumentError):
    pass


class CryptoVenueRuleError(CryptoInstrumentError):
    pass


class CryptoResolutionError(CryptoInstrumentError):
    pass


class AmbiguousCryptoVenueError(CryptoResolutionError):
    """A display symbol exists on more than one venue and no venue was given."""


class CryptoInstrumentKind(StrEnum):
    SPOT = "SPOT"
    PERPETUAL = "PERPETUAL"
    DATED_FUTURE = "DATED_FUTURE"


class SettlementStyle(StrEnum):
    #: Settles in the quote asset (BTCUSDT perpetual settling USDT).
    LINEAR = "LINEAR"
    #: Settles in the base asset -- "coin-margined" (BTCUSD settling BTC).
    INVERSE = "INVERSE"
    #: Settles in a third asset that is neither base nor quote.
    QUANTO = "QUANTO"


class CryptoSettlementType(StrEnum):
    PHYSICAL_DELIVERY = "PHYSICAL_DELIVERY"
    CASH_SETTLED = "CASH_SETTLED"


class ReferencePriceRequirement(StrEnum):
    """Which reference-price semantics a contract requires -- never a value."""

    NONE = "NONE"
    INDEX_ONLY = "INDEX_ONLY"
    MARK_AND_INDEX = "MARK_AND_INDEX"


#: Venue codes that name no actual venue. The instrument master's shipped MVP
#: universe registers ``CRYPTO:SPOT:BTCUSD`` at a placeholder venue "CRYPTO",
#: which predates this module; such an instrument is a provider-neutral
#: placeholder rather than something tradable, so it can never receive a crypto
#: specification. Naming the real venue is required.
RESERVED_PLACEHOLDER_VENUES = frozenset({"CRYPTO", "SPOT", "UNKNOWN", "DEFAULT"})


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CryptoInstrumentError(f"{name}_must_be_timezone_aware")


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise CryptoInstrumentError(f"invalid_{name}")


def _require_asset(value: str, name: str) -> None:
    if not 2 <= len(value) <= MAX_CRYPTO_ASSET_CODE_LENGTH or not value.isalnum():
        raise CryptoSpecificationError(f"invalid_{name}")
    if value != value.upper():
        raise CryptoSpecificationError(f"invalid_{name}")


@dataclass(frozen=True, slots=True)
class CryptoInstrumentSpecification:
    """Static crypto contract identity, bound 1:1 to a registered instrument."""

    instrument_id: str
    venue: str
    kind: CryptoInstrumentKind
    base_asset: str
    quote_asset: str
    settlement_type: CryptoSettlementType
    contract_multiplier: Decimal
    contract_size: Decimal
    registered_at: datetime
    source_reference: str
    settlement_asset: str | None = None
    settlement_style: SettlementStyle | None = None
    expiry_at: datetime | None = None
    reference_price_requirement: ReferencePriceRequirement = ReferencePriceRequirement.NONE
    index_reference: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.instrument_id, "instrument_id"),
            (self.venue, "venue"),
            (self.source_reference, "source_reference"),
        ):
            _require_text(value, name)
        if self.venue.upper() in RESERVED_PLACEHOLDER_VENUES:
            raise CryptoSpecificationError(f"placeholder_venue_is_not_a_venue:{self.venue}")
        _require_asset(self.base_asset, "base_asset")
        _require_asset(self.quote_asset, "quote_asset")
        if self.base_asset == self.quote_asset:
            raise CryptoSpecificationError("base_and_quote_asset_must_differ")
        if min(self.contract_multiplier, self.contract_size) <= 0:
            raise CryptoSpecificationError("invalid_contract_units")
        _require_aware(self.registered_at, "registered_at")
        if self.expiry_at is not None:
            _require_aware(self.expiry_at, "expiry_at")
        self._validate_kind_fields()
        self._validate_settlement_coherence()

    def _validate_kind_fields(self) -> None:
        if self.kind is CryptoInstrumentKind.SPOT:
            # A spot pair exchanges both legs on delivery. It has no single
            # settlement asset, no linear/inverse sense, no expiry, no funding
            # and no mark price -- every one of those is a derivative concept.
            if self.expiry_at is not None:
                raise CryptoSpecificationError("spot_instrument_cannot_have_expiry")
            if self.settlement_asset is not None or self.settlement_style is not None:
                raise CryptoSpecificationError("spot_instrument_cannot_have_settlement_style")
            if self.reference_price_requirement is not ReferencePriceRequirement.NONE:
                raise CryptoSpecificationError("spot_instrument_cannot_require_reference_price")
            if self.settlement_type is not CryptoSettlementType.PHYSICAL_DELIVERY:
                raise CryptoSpecificationError("spot_instrument_must_be_physically_delivered")
            return
        if self.settlement_asset is None or self.settlement_style is None:
            raise CryptoSpecificationError("derivative_requires_settlement_asset_and_style")
        _require_asset(self.settlement_asset, "settlement_asset")
        if self.reference_price_requirement is ReferencePriceRequirement.NONE:
            raise CryptoSpecificationError("derivative_requires_reference_price_semantics")
        if self.kind is CryptoInstrumentKind.PERPETUAL and self.expiry_at is not None:
            raise CryptoSpecificationError("perpetual_instrument_cannot_have_expiry")
        if self.kind is CryptoInstrumentKind.DATED_FUTURE and self.expiry_at is None:
            raise CryptoSpecificationError("dated_future_requires_expiry")

    def _validate_settlement_coherence(self) -> None:
        """Linear settles quote, inverse settles base, quanto settles a third asset."""
        if self.settlement_style is None:
            return
        settlement = self.settlement_asset
        if self.settlement_style is SettlementStyle.LINEAR and settlement != self.quote_asset:
            raise CryptoSpecificationError("linear_contract_must_settle_in_quote_asset")
        if self.settlement_style is SettlementStyle.INVERSE and settlement != self.base_asset:
            raise CryptoSpecificationError("inverse_contract_must_settle_in_base_asset")
        if self.settlement_style is SettlementStyle.QUANTO and settlement in (
            self.base_asset,
            self.quote_asset,
        ):
            raise CryptoSpecificationError("quanto_contract_must_settle_in_a_third_asset")

    @property
    def requires_funding(self) -> bool:
        """Only a perpetual is subject to funding. This is a fact about the
        contract, not an observation -- no rate is stored anywhere here."""
        return self.kind is CryptoInstrumentKind.PERPETUAL


@dataclass(frozen=True, slots=True)
class CryptoFundingConvention:
    """A perpetual's funding *schedule*, versioned with two independent clocks."""

    instrument_id: str
    convention_version: int
    interval_hours: Decimal
    first_funding_offset_hours: Decimal
    funding_settlement_asset: str
    effective_from: datetime
    known_at: datetime
    source_reference: str
    source_hash: str
    funding_rate_floor: Decimal | None = None
    funding_rate_cap: Decimal | None = None
    convention_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        for value, name in (
            (self.instrument_id, "instrument_id"),
            (self.source_reference, "source_reference"),
            (self.source_hash, "source_hash"),
        ):
            _require_text(value, name)
        _require_asset(self.funding_settlement_asset, "funding_settlement_asset")
        if self.convention_version < 1:
            raise CryptoFundingConventionError("invalid_convention_version")
        if self.interval_hours <= 0:
            raise CryptoFundingConventionError("invalid_funding_interval")
        if self.first_funding_offset_hours < 0:
            raise CryptoFundingConventionError("invalid_funding_offset")
        if self.first_funding_offset_hours >= self.interval_hours:
            raise CryptoFundingConventionError("funding_offset_exceeds_interval")
        if (
            self.funding_rate_floor is not None
            and self.funding_rate_cap is not None
            and self.funding_rate_floor > self.funding_rate_cap
        ):
            raise CryptoFundingConventionError("funding_rate_floor_exceeds_cap")
        # No ordering between the clocks: a venue announces a schedule change
        # before it applies, and a backfill records one long after.
        _require_aware(self.effective_from, "funding_effective_from")
        _require_aware(self.known_at, "funding_known_at")


@dataclass(frozen=True, slots=True)
class CryptoVenueTradingRules:
    """Venue-revised trading limits, versioned with two independent clocks."""

    instrument_id: str
    rule_version: int
    tick_size: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    price_precision: int
    quantity_precision: int
    effective_from: datetime
    known_at: datetime
    source_reference: str
    source_hash: str
    max_quantity: Decimal | None = None
    min_notional: Decimal | None = None
    rule_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        for value, name in (
            (self.instrument_id, "instrument_id"),
            (self.source_reference, "source_reference"),
            (self.source_hash, "source_hash"),
        ):
            _require_text(value, name)
        if self.rule_version < 1:
            raise CryptoVenueRuleError("invalid_rule_version")
        if min(self.tick_size, self.quantity_step, self.min_quantity) <= 0:
            raise CryptoVenueRuleError("invalid_venue_rule_units")
        if self.max_quantity is not None and self.max_quantity < self.min_quantity:
            raise CryptoVenueRuleError("max_quantity_below_min_quantity")
        if self.min_notional is not None and self.min_notional <= 0:
            raise CryptoVenueRuleError("invalid_min_notional")
        if not 0 <= self.price_precision <= 18 or not 0 <= self.quantity_precision <= 18:
            raise CryptoVenueRuleError("invalid_venue_rule_precision")
        _require_aware(self.effective_from, "rule_effective_from")
        _require_aware(self.known_at, "rule_known_at")


class PostgresCryptoInstrumentAuthority:
    """Persists crypto semantics alongside the existing instrument master."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    # ---- specifications -------------------------------------------------------

    def specify_instrument(self, specification: CryptoInstrumentSpecification) -> None:
        """Bind crypto semantics to an existing CRYPTO instrument, or fail closed."""
        instrument = self._require_registered_crypto(specification.instrument_id)
        if instrument.venue != specification.venue:
            raise CryptoSpecificationError(
                f"venue_differs_from_instrument_master:"
                f"{specification.venue}!={instrument.venue}"
            )
        expected = _EXPECTED_INSTRUMENT_TYPES[specification.kind]
        if instrument.instrument_type is not expected:
            raise CryptoSpecificationError(
                f"instrument_type_does_not_match_crypto_kind:"
                f"{instrument.instrument_type.value}!={expected.value}"
            )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    # Fixed placeholder count; no identifier or value is interpolated.
                    "INSERT INTO crypto_instrument_specifications VALUES ("  # nosec B608
                    + ",".join(["%s"] * 15) + ")",
                    (
                        specification.instrument_id, specification.venue,
                        specification.kind.value, specification.base_asset,
                        specification.quote_asset, specification.settlement_asset,
                        None if specification.settlement_style is None
                        else specification.settlement_style.value,
                        specification.settlement_type.value,
                        specification.contract_multiplier, specification.contract_size,
                        specification.expiry_at,
                        specification.reference_price_requirement.value,
                        specification.index_reference, specification.registered_at,
                        specification.source_reference,
                    ),
                )
        except Exception as error:
            raise CryptoSpecificationError("crypto_specification_duplicate_or_invalid") from error

    def get_specification(
        self, instrument_id: str, *, known_at: datetime
    ) -> CryptoInstrumentSpecification:
        """Read a specification only if this platform already knew it at ``known_at``."""
        _require_aware(known_at, "known_at")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM crypto_instrument_specifications "
                    "WHERE instrument_id=%s AND registered_at<=%s",
                    (instrument_id, known_at),
                )
                row = cursor.fetchone()
        except Exception as error:
            raise CryptoSpecificationError("crypto_specification_read_failed") from error
        if row is None:
            raise CryptoResolutionError(f"crypto_specification_not_found:{instrument_id}")
        return self._specification_from_row(row)

    @staticmethod
    def _specification_from_row(row: Sequence[object]) -> CryptoInstrumentSpecification:
        return CryptoInstrumentSpecification(
            instrument_id=str(row[0]), venue=str(row[1]),
            kind=CryptoInstrumentKind(str(row[2])), base_asset=str(row[3]),
            quote_asset=str(row[4]),
            settlement_asset=None if row[5] is None else str(row[5]),
            settlement_style=None if row[6] is None else SettlementStyle(str(row[6])),
            settlement_type=CryptoSettlementType(str(row[7])),
            contract_multiplier=Decimal(str(row[8])), contract_size=Decimal(str(row[9])),
            expiry_at=cast("datetime | None", row[10]),
            reference_price_requirement=ReferencePriceRequirement(str(row[11])),
            index_reference=None if row[12] is None else str(row[12]),
            registered_at=cast(datetime, row[13]), source_reference=str(row[14]),
        )

    def _require_registered_crypto(self, instrument_id: str) -> ProfessionalInstrument:
        master = PostgresProfessionalInstrumentMaster(self._database)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT registered_at FROM professional_instruments WHERE instrument_id=%s",
                    (instrument_id,),
                )
                row = cursor.fetchone()
        except Exception as error:
            raise CryptoSpecificationError("instrument_master_read_failed") from error
        if row is None:
            raise CryptoSpecificationError(f"instrument_not_registered:{instrument_id}")
        instrument = master.get_as_of(instrument_id, cast(datetime, row[0]))
        if instrument.asset_class is not AssetClass.CRYPTO:
            raise CryptoSpecificationError(
                f"instrument_is_not_crypto:{instrument.asset_class.value}"
            )
        if instrument.lifecycle_status is not LifecycleStatus.ACTIVE:
            raise CryptoSpecificationError("instrument_not_active_at_registration")
        return instrument

    # ---- resolution -----------------------------------------------------------

    def resolve(
        self,
        venue: str,
        base_asset: str,
        quote_asset: str,
        kind: CryptoInstrumentKind,
        *,
        known_at: datetime,
        expiry_at: datetime | None = None,
    ) -> str:
        """Resolve one exact crypto instrument. ``kind`` and ``venue`` are required.

        Because the kind is part of the lookup key, a spot request can never
        return a perpetual or a dated future on the same pair, and vice versa.
        """
        _require_aware(known_at, "known_at")
        if kind is CryptoInstrumentKind.DATED_FUTURE and expiry_at is None:
            raise CryptoResolutionError("dated_future_resolution_requires_expiry")
        if kind is not CryptoInstrumentKind.DATED_FUTURE and expiry_at is not None:
            raise CryptoResolutionError("expiry_not_applicable_to_this_kind")
        statement = (
            "SELECT instrument_id FROM crypto_instrument_specifications "
            "WHERE venue=%s AND base_asset=%s AND quote_asset=%s "
            "AND crypto_instrument_kind=%s AND registered_at<=%s "
            "AND (expiry_at IS NOT DISTINCT FROM %s) LIMIT 2"
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    statement, (venue, base_asset, quote_asset, kind.value, known_at, expiry_at)
                )
                rows = cursor.fetchall()
        except Exception as error:
            raise CryptoResolutionError("crypto_specification_read_failed") from error
        if not rows:
            raise CryptoResolutionError(
                f"crypto_instrument_not_found:{venue}:{base_asset}{quote_asset}:{kind.value}"
            )
        if len(rows) != 1:
            raise CryptoResolutionError(
                f"ambiguous_crypto_instrument:{venue}:{base_asset}{quote_asset}:{kind.value}"
            )
        return str(rows[0][0])

    def resolve_display_symbol(
        self, symbol: str, *, known_at: datetime, venue: str | None = None
    ) -> str:
        """Resolve a venue display symbol through the existing symbol-mapping authority.

        Without a venue this fails closed whenever the symbol exists on more
        than one venue: ``BTCUSDT`` is not one instrument, and guessing which
        venue was meant would silently bind an order to the wrong order book.
        """
        _require_aware(known_at, "known_at")
        _require_text(symbol, "symbol")
        statement = (
            "SELECT DISTINCT s.instrument_id, s.venue FROM professional_symbol_mappings s "
            "JOIN crypto_instrument_specifications c ON c.instrument_id=s.instrument_id "
            "WHERE s.symbol=%s AND (CAST(%s AS text) IS NULL OR s.venue=%s) "
            "AND s.valid_from<=%s AND (s.valid_until IS NULL OR s.valid_until>%s) "
            "AND s.ingested_at<=%s AND c.registered_at<=%s "
            "AND NOT EXISTS (SELECT 1 FROM professional_instrument_lifecycle_events e "
            "WHERE e.instrument_id=s.instrument_id AND e.status='DELISTED' "
            "AND e.effective_at<=%s AND e.ingested_at<=%s) LIMIT 3"
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    statement,
                    (symbol, venue, venue, known_at, known_at, known_at, known_at,
                     known_at, known_at),
                )
                rows = cursor.fetchall()
        except Exception as error:
            raise CryptoResolutionError("crypto_symbol_read_failed") from error
        if not rows:
            raise CryptoResolutionError(f"crypto_symbol_not_found:{venue or 'ANY_VENUE'}:{symbol}")
        if len(rows) != 1:
            venues = sorted({str(row[1]) for row in rows})
            raise AmbiguousCryptoVenueError(
                f"crypto_symbol_ambiguous_across_venues:{symbol}:{','.join(venues)}"
            )
        return str(rows[0][0])

    # ---- funding conventions --------------------------------------------------

    def register_funding_convention(self, convention: CryptoFundingConvention) -> None:
        """Record a perpetual's funding schedule. Rejected for any other kind."""
        specification = self.get_specification(
            convention.instrument_id, known_at=convention.known_at
        )
        if not specification.requires_funding:
            raise CryptoFundingConventionError(
                f"funding_convention_requires_perpetual:{specification.kind.value}"
            )
        if convention.funding_settlement_asset != specification.settlement_asset:
            raise CryptoFundingConventionError("funding_asset_differs_from_settlement_asset")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    # Fixed placeholder count; no identifier or value is interpolated.
                    "INSERT INTO crypto_funding_conventions VALUES ("  # nosec B608
                    + ",".join(["%s"] * 12) + ")",
                    (
                        convention.convention_id, convention.instrument_id,
                        convention.convention_version, convention.interval_hours,
                        convention.first_funding_offset_hours, convention.funding_rate_floor,
                        convention.funding_rate_cap, convention.funding_settlement_asset,
                        convention.effective_from, convention.known_at,
                        convention.source_reference, convention.source_hash,
                    ),
                )
        except Exception as error:
            raise CryptoFundingConventionError(
                "funding_convention_duplicate_or_invalid"
            ) from error

    def funding_convention_point_in_time(
        self, instrument_id: str, *, effective_at: datetime, known_at: datetime
    ) -> CryptoFundingConvention:
        """The funding schedule in force at ``effective_at`` as known at ``known_at``."""
        _require_aware(effective_at, "effective_at")
        _require_aware(known_at, "known_at")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM crypto_funding_conventions WHERE instrument_id=%s "
                    "AND effective_from<=%s AND known_at<=%s "
                    "ORDER BY effective_from DESC, known_at DESC LIMIT 1",
                    (instrument_id, effective_at, known_at),
                )
                row = cursor.fetchone()
        except Exception as error:
            raise CryptoFundingConventionError("funding_convention_read_failed") from error
        if row is None:
            raise CryptoFundingConventionError(
                f"funding_convention_not_available:{instrument_id}"
            )
        return CryptoFundingConvention(
            convention_id=cast(UUID, row[0]), instrument_id=str(row[1]),
            convention_version=int(str(row[2])), interval_hours=Decimal(str(row[3])),
            first_funding_offset_hours=Decimal(str(row[4])),
            funding_rate_floor=None if row[5] is None else Decimal(str(row[5])),
            funding_rate_cap=None if row[6] is None else Decimal(str(row[6])),
            funding_settlement_asset=str(row[7]), effective_from=cast(datetime, row[8]),
            known_at=cast(datetime, row[9]), source_reference=str(row[10]),
            source_hash=str(row[11]),
        )

    # ---- venue trading rules --------------------------------------------------

    def record_venue_trading_rules(self, rules: CryptoVenueTradingRules) -> None:
        self.get_specification(rules.instrument_id, known_at=rules.known_at)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    # Fixed placeholder count; no identifier or value is interpolated.
                    "INSERT INTO crypto_venue_trading_rules VALUES ("  # nosec B608
                    + ",".join(["%s"] * 14) + ")",
                    (
                        rules.rule_id, rules.instrument_id, rules.rule_version, rules.tick_size,
                        rules.quantity_step, rules.min_quantity, rules.max_quantity,
                        rules.min_notional, rules.price_precision, rules.quantity_precision,
                        rules.effective_from, rules.known_at, rules.source_reference,
                        rules.source_hash,
                    ),
                )
        except Exception as error:
            raise CryptoVenueRuleError("venue_trading_rules_duplicate_or_invalid") from error

    def venue_trading_rules_point_in_time(
        self, instrument_id: str, *, effective_at: datetime, known_at: datetime
    ) -> CryptoVenueTradingRules:
        """The venue limits in force at ``effective_at`` as known at ``known_at``."""
        _require_aware(effective_at, "effective_at")
        _require_aware(known_at, "known_at")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM crypto_venue_trading_rules WHERE instrument_id=%s "
                    "AND effective_from<=%s AND known_at<=%s "
                    "ORDER BY effective_from DESC, known_at DESC LIMIT 1",
                    (instrument_id, effective_at, known_at),
                )
                row = cursor.fetchone()
        except Exception as error:
            raise CryptoVenueRuleError("venue_trading_rules_read_failed") from error
        if row is None:
            raise CryptoVenueRuleError(f"venue_trading_rules_not_available:{instrument_id}")
        return CryptoVenueTradingRules(
            rule_id=cast(UUID, row[0]), instrument_id=str(row[1]),
            rule_version=int(str(row[2])), tick_size=Decimal(str(row[3])),
            quantity_step=Decimal(str(row[4])), min_quantity=Decimal(str(row[5])),
            max_quantity=None if row[6] is None else Decimal(str(row[6])),
            min_notional=None if row[7] is None else Decimal(str(row[7])),
            price_precision=int(str(row[8])), quantity_precision=int(str(row[9])),
            effective_from=cast(datetime, row[10]), known_at=cast(datetime, row[11]),
            source_reference=str(row[12]), source_hash=str(row[13]),
        )


#: Each crypto kind must be registered under exactly one instrument-master type,
#: so a perpetual can never be recorded as spot in the canonical registry.
_EXPECTED_INSTRUMENT_TYPES: dict[CryptoInstrumentKind, InstrumentType] = {
    CryptoInstrumentKind.SPOT: InstrumentType.SPOT_CRYPTO,
    CryptoInstrumentKind.PERPETUAL: InstrumentType.CRYPTO_PERPETUAL,
    CryptoInstrumentKind.DATED_FUTURE: InstrumentType.CRYPTO_DATED_FUTURE,
}
