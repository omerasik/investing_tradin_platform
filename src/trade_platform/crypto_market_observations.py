"""Typed canonical payloads for crypto funding, mark-price and index-price observations.

Module 3I.2 (roadmap NEXT-03 phase 2). Like 3I.1 this is **not** a second
pipeline: capture, normalization, sealing, Data Health and research reads all
remain ``historical_market_data.PostgresHistoricalMarketDataPipeline``, which
imports these contracts and writes the typed row inside the same transaction as
the normalized envelope it belongs to. Crypto open interest reuses the single
cross-asset authority in ``market_observation_payloads`` -- there is no crypto
open-interest pipeline.

Four refusals define this module.

**A realized funding rate and an indicative one are different evidence and
never substitute for each other.** ``FUNDING_RATE_REALIZED`` is what a venue
published as actually applied at a funding settlement; ``FUNDING_RATE_INDICATIVE``
is a venue- or provider-published estimate of a funding rate that has not
happened yet. They are separate ``ObservationKind`` values, separately
authorized per source, and the envelope kind -- not any column in the typed
table -- is the sole authority for which one a record is. Their time semantics
are mutually exclusive by construction: a realized record's funding instant *is*
its event instant, an indicative record's funding instant is strictly in the
future of its publication instant. Neither can be reinterpreted as the other.

**An internally computed funding forecast is not market data.** Only a venue- or
external-provider-published estimate is admissible here. A model's opinion about
a future funding rate belongs to Feature Authority / Model Registry / strategy
research, and this module has no path that would let one enter historical
market-data evidence.

**Multiple successive estimates for the same future funding instant are all
preserved.** Each publication is its own observation keyed by its own event
instant, so a replay at time T sees exactly the estimates published on or before
T -- never a later, better one.

**A mark price, an index price, a settlement price, an OHLCV close and a last
trade are five different measurements.** Each has its own kind; none is ever a
fallback for another. Which of mark/index an instrument may even carry comes
from its 3H.2 ``ReferencePriceRequirement``, so a spot pair -- which requires
neither -- fails closed for both.

``methodology_reference`` is provenance metadata, never an authoritative
financial value: it records what the publisher said its methodology was, and no
code treats free text as evidence that a methodology was followed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID

from .crypto_instruments import (
    CryptoFundingConvention,
    CryptoInstrumentKind,
    CryptoInstrumentSpecification,
    ReferencePriceRequirement,
)
from .market_observation_payloads import (
    OpenInterestPayload,
    OpenInterestUnit,
    decimal_field,
    instant_field,
    text_field,
)

FUNDING_PAYLOAD_TABLE: Final = "crypto_funding_observations"
REFERENCE_PRICE_PAYLOAD_TABLE: Final = "crypto_reference_price_observations"

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_SECONDS_PER_HOUR: Final = Decimal(3600)


class FundingObservationKind(StrEnum):
    """Which funding evidence a record is. Mirrors the envelope's observation kind.

    Declared here so this module needs no import from the pipeline, but it is
    never stored: the envelope kind remains the only authority, and a second
    stored discriminator would be a second authority that could disagree with it.
    """

    REALIZED = "REALIZED"
    INDICATIVE = "INDICATIVE"


class ReferencePriceKind(StrEnum):
    MARK = "MARK"
    INDEX = "INDEX"


#: Which 3H.2 reference-price semantics permit which reference-price kind. A
#: contract that requires no reference price (every spot pair) permits neither,
#: and an index-only contract does not acquire a mark price by implication.
_PERMITTED_REFERENCE_PRICES: Final[dict[ReferencePriceRequirement, frozenset[ReferencePriceKind]]] = {
    ReferencePriceRequirement.NONE: frozenset(),
    ReferencePriceRequirement.INDEX_ONLY: frozenset({ReferencePriceKind.INDEX}),
    ReferencePriceRequirement.MARK_AND_INDEX: frozenset(
        {ReferencePriceKind.MARK, ReferencePriceKind.INDEX}
    ),
}

#: Open interest is a derivative-position measurement. A spot pair has no open
#: positions to count, so spot open interest fails closed rather than being
#: recorded as an unexplained number.
OPEN_INTEREST_ELIGIBLE_CRYPTO_KINDS: Final = frozenset(
    {CryptoInstrumentKind.PERPETUAL, CryptoInstrumentKind.DATED_FUTURE}
)

#: Only a perpetual is subject to funding. A dated future settles at expiry
#: instead; if a venue ever defines funding on a dated product, that needs its
#: own explicitly modelled product semantics, so the default is to refuse.
FUNDING_ELIGIBLE_CRYPTO_KINDS: Final = frozenset({CryptoInstrumentKind.PERPETUAL})


@dataclass(frozen=True, slots=True)
class CryptoFundingPayload:
    """One funding-rate record, realized or indicative per its envelope kind.

    ``convention_id`` / ``convention_version`` are bound by the pipeline from the
    3H.2 convention that was *visible* at this observation's effective and
    knowledge times -- never supplied by the provider and never resolved from a
    convention learned later. They are part of the canonical serialization
    because a rate validated against one funding schedule is different evidence
    from the same number validated against another.
    """

    funding_rate: Decimal
    target_funding_at: datetime
    published_at: datetime
    settlement_asset: str
    convention_id: UUID | None = None
    convention_version: int | None = None

    def bound_to(self, convention: CryptoFundingConvention) -> CryptoFundingPayload:
        """Return this payload bound to the funding convention it was validated against."""
        return replace(
            self,
            convention_id=convention.convention_id,
            convention_version=convention.convention_version,
        )

    def canonical_tuple(self) -> tuple[str, ...]:
        return (
            FUNDING_PAYLOAD_TABLE,
            str(self.funding_rate),
            self.target_funding_at.isoformat(),
            self.published_at.isoformat(),
            self.settlement_asset,
            "" if self.convention_id is None else str(self.convention_id),
            "" if self.convention_version is None else str(self.convention_version),
        )

    def as_normalized_projection(self) -> dict[str, object]:
        return {
            "funding_rate": str(self.funding_rate),
            "target_funding_at": self.target_funding_at.isoformat(),
            "published_at": self.published_at.isoformat(),
            "settlement_asset": self.settlement_asset,
            "convention_id": None if self.convention_id is None else str(self.convention_id),
            "convention_version": self.convention_version,
        }


@dataclass(frozen=True, slots=True)
class CryptoReferencePricePayload:
    """One mark or index price. Which it is comes from the envelope kind alone."""

    price: Decimal
    price_asset: str
    observed_at: datetime
    methodology_reference: str | None = None

    def canonical_tuple(self) -> tuple[str, ...]:
        return (
            REFERENCE_PRICE_PAYLOAD_TABLE,
            str(self.price),
            self.price_asset,
            self.observed_at.isoformat(),
            self.methodology_reference or "",
        )

    def as_normalized_projection(self) -> dict[str, object]:
        return {
            "price": str(self.price),
            "price_asset": self.price_asset,
            "observed_at": self.observed_at.isoformat(),
            "methodology_reference": self.methodology_reference,
        }


def parse_funding_payload(
    payload: dict[str, object],
) -> tuple[CryptoFundingPayload | None, tuple[str, ...]]:
    """Parse without repairing. The convention binding happens separately."""
    issues: list[str] = []
    rate = decimal_field(payload, "funding_rate", issues)
    target_at = instant_field(payload, "target_funding_at", issues)
    published_at = instant_field(payload, "published_at", issues)
    settlement_asset = text_field(payload, "settlement_asset", issues).upper()

    # A funding rate is legitimately negative -- that is shorts paying longs, not
    # bad data -- so there is no sign check here. Magnitude is only ever judged
    # against an explicit convention floor/cap, never against a guessed bound.
    if settlement_asset and not 2 <= len(settlement_asset) <= 12:
        issues.append("invalid_settlement_asset")
    # Explicit narrowing rather than `assert`, which is stripped under -O.
    if issues or rate is None or target_at is None or published_at is None:
        return None, tuple(dict.fromkeys(issues)) or ("invalid_funding_payload",)
    return (
        CryptoFundingPayload(
            funding_rate=rate,
            target_funding_at=target_at,
            published_at=published_at,
            settlement_asset=settlement_asset,
        ),
        (),
    )


def parse_reference_price_payload(
    payload: dict[str, object],
) -> tuple[CryptoReferencePricePayload | None, tuple[str, ...]]:
    issues: list[str] = []
    price = decimal_field(payload, "price", issues)
    price_asset = text_field(payload, "price_asset", issues).upper()
    observed_at = instant_field(payload, "observed_at", issues)
    methodology = str(payload.get("methodology_reference", "")).strip() or None

    if price is not None and price <= 0:
        issues.append("non_positive_reference_price")
    if price_asset and not 2 <= len(price_asset) <= 12:
        issues.append("invalid_price_asset")
    # Explicit narrowing rather than `assert`, which is stripped under -O.
    if issues or price is None or observed_at is None:
        return None, tuple(dict.fromkeys(issues)) or ("invalid_reference_price_payload",)
    return (
        CryptoReferencePricePayload(
            price=price,
            price_asset=price_asset,
            observed_at=observed_at,
            methodology_reference=methodology,
        ),
        (),
    )


def funding_instant_matches_convention(
    target_funding_at: datetime, convention: CryptoFundingConvention
) -> bool | None:
    """Whether a funding instant lies on the convention's schedule.

    Returns ``None`` when the schedule is not deterministically checkable -- an
    interval or offset that is not a whole number of seconds. Reporting "unknown"
    is the honest answer there; inventing a tolerance would let a genuinely
    off-schedule instant pass.
    """
    interval_seconds = convention.interval_hours * _SECONDS_PER_HOUR
    offset_seconds = convention.first_funding_offset_hours * _SECONDS_PER_HOUR
    if interval_seconds != interval_seconds.to_integral_value():
        return None
    if offset_seconds != offset_seconds.to_integral_value():
        return None
    elapsed = target_funding_at - _EPOCH
    if elapsed.microseconds:
        return False
    return (Decimal(int(elapsed.total_seconds())) - offset_seconds) % interval_seconds == 0


def expected_funding_instants(
    convention: CryptoFundingConvention, *, start: datetime, end: datetime
) -> tuple[datetime, ...]:
    """The funding instants a convention actually schedules in ``[start, end]``.

    This is the **only** admissible basis for a realized-funding completeness
    check. A universal "one funding event per eight hours" assumption would be a
    guess about venues this platform has not modelled; a versioned convention is
    a recorded fact about one instrument, and a schedule the venue changed later
    is a different convention version that this one does not speak for.

    Returns an empty tuple when the schedule is not deterministically
    enumerable (an interval or offset that is not a whole number of seconds) --
    no completeness claim is better than an invented one.
    """
    if start > end:
        raise ValueError("invalid_funding_window")
    interval_seconds = convention.interval_hours * _SECONDS_PER_HOUR
    offset_seconds = convention.first_funding_offset_hours * _SECONDS_PER_HOUR
    if interval_seconds != interval_seconds.to_integral_value():
        return ()
    if offset_seconds != offset_seconds.to_integral_value():
        return ()
    interval = int(interval_seconds)
    offset = int(offset_seconds)
    first_elapsed = int((start - _EPOCH).total_seconds())
    remainder = (first_elapsed - offset) % interval
    if remainder:
        first_elapsed += interval - remainder
    instants: list[datetime] = []
    last_elapsed = int((end - _EPOCH).total_seconds())
    while first_elapsed <= last_elapsed:
        instants.append(_EPOCH + timedelta(seconds=first_elapsed))
        first_elapsed += interval
    return tuple(instants)


def validate_crypto_funding(
    payload: CryptoFundingPayload,
    *,
    kind: FundingObservationKind,
    specification: CryptoInstrumentSpecification,
    convention: CryptoFundingConvention,
    event_at: datetime,
    ingested_at: datetime,
) -> tuple[str, ...]:
    """Judge a funding record against the schedule visible at its own two clocks.

    ``convention`` must already have been resolved point-in-time by the caller at
    (effective ``target_funding_at``, known ``ingested_at``). A schedule the
    venue announced later is invisible to this record by construction, so a
    historical replay keeps using the schedule that was actually in force.
    """
    issues: list[str] = []
    if specification.kind not in FUNDING_ELIGIBLE_CRYPTO_KINDS:
        issues.append(f"funding_requires_perpetual:{specification.kind.value}")
    if payload.settlement_asset != convention.funding_settlement_asset:
        issues.append("funding_settlement_asset_differs_from_convention")
    if payload.settlement_asset != specification.settlement_asset:
        issues.append("funding_settlement_asset_differs_from_instrument")

    if kind is FundingObservationKind.REALIZED:
        # A realized funding record IS the funding event: its event instant is
        # the settlement instant, and it cannot have been published before the
        # thing it reports happened.
        if payload.target_funding_at != event_at:
            issues.append("realized_funding_target_must_equal_event_instant")
        if payload.published_at < payload.target_funding_at:
            issues.append("realized_funding_published_before_settlement")
    else:
        # An indicative record IS a publication about a funding event that has
        # not happened yet. Making the event instant the publication instant is
        # what lets successive estimates for one funding instant coexist and
        # what makes point-in-time replay see only those already published.
        if payload.published_at != event_at:
            issues.append("indicative_funding_event_must_be_its_publication_instant")
        if payload.target_funding_at <= payload.published_at:
            issues.append("indicative_funding_target_must_be_in_the_future")
    if payload.published_at > ingested_at:
        issues.append("funding_published_after_ingestion")

    matches = funding_instant_matches_convention(payload.target_funding_at, convention)
    if matches is False:
        issues.append("funding_target_incompatible_with_convention_schedule")
    if convention.funding_rate_cap is not None and payload.funding_rate > convention.funding_rate_cap:
        issues.append("funding_rate_above_convention_cap")
    if (
        convention.funding_rate_floor is not None
        and payload.funding_rate < convention.funding_rate_floor
    ):
        issues.append("funding_rate_below_convention_floor")
    return tuple(dict.fromkeys(issues))


def validate_crypto_reference_price(
    payload: CryptoReferencePricePayload,
    *,
    kind: ReferencePriceKind,
    specification: CryptoInstrumentSpecification,
    event_at: datetime,
) -> tuple[str, ...]:
    """Judge a mark/index record against the contract's own reference-price semantics."""
    issues: list[str] = []
    permitted = _PERMITTED_REFERENCE_PRICES[specification.reference_price_requirement]
    if kind not in permitted:
        issues.append(
            f"reference_price_not_permitted_by_instrument:{kind.value}:"
            f"{specification.reference_price_requirement.value}"
        )
    if payload.price_asset != specification.quote_asset:
        # A mark or index price is quoted in the contract's quote asset, whatever
        # asset the contract settles in. Accepting another asset would silently
        # change what the number means.
        issues.append("reference_price_asset_differs_from_quote_asset")
    if payload.observed_at != event_at:
        issues.append("reference_price_observation_instant_differs_from_event")
    return tuple(dict.fromkeys(issues))


def validate_crypto_open_interest(
    payload: OpenInterestPayload, *, specification: CryptoInstrumentSpecification
) -> tuple[str, ...]:
    """Bind a crypto open-interest unit to the instrument's own assets.

    No unit is ever converted into another: a base-asset quantity must name the
    contract's base asset and a quote notional must name its quote asset, or the
    record is refused. Reaching a contract count from either would require a
    mark, index or last price that an open-interest record does not carry.
    """
    issues: list[str] = []
    if specification.kind not in OPEN_INTEREST_ELIGIBLE_CRYPTO_KINDS:
        issues.append(f"open_interest_requires_derivative:{specification.kind.value}")
    if (
        payload.unit is OpenInterestUnit.BASE_ASSET
        and payload.unit_asset != specification.base_asset
    ):
        issues.append("base_asset_open_interest_unit_asset_mismatch")
    if (
        payload.unit is OpenInterestUnit.QUOTE_NOTIONAL
        and payload.unit_asset != specification.quote_asset
    ):
        issues.append("quote_notional_open_interest_unit_asset_mismatch")
    return tuple(dict.fromkeys(issues))
