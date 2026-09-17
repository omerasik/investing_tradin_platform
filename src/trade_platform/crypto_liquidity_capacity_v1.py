"""Module 3B.3 -- canonical crypto liquidity / participation / capacity evidence.

Phase 3B.2 established *typed* OHLCV semantics: for an authorized Bybit linear
perpetual, ``volume`` counts BASE_ASSET and ``turnover`` is the exact
provider-published QUOTE_ASSET notional. This module is the first consumer of
that authority, and it exists because the generic
:func:`trade_platform.quant_validation.evaluate_capacity` cannot serve this
path: it builds a notional as ``average_daily_volume * average_price``, which is
a *synthesized* measurement. Wherever an authoritative quote turnover exists,
synthesizing one is strictly worse evidence, so nothing here multiplies a volume
by a price. ``evaluate_capacity()`` keeps its existing trend/legacy consumers
untouched; this is a separate, additive canonical path.

**Provider-published quote turnover is the liquidity notional authority.** The
only liquidity figure this module ever sums is
:attr:`AuthoritativeTradableBarV2.turnover`, and only when the Phase 3B.2 typed
semantics on that same bar prove it is a QUOTE_ASSET quantity. No ``volume *
close``, no ``volume * VWAP``, no ``average_volume * average_price``, no
conversion between units, ever.

**A liquidity day is a whole day or it is not a day at all.** A UTC date
contributes a daily quote turnover only when the evidence holds the complete
1-minute grid for it: exactly :data:`COMPLETE_UTC_DAY_BAR_COUNT` bars opening at
00:00 through 23:59 UTC, no gap and no duplicate open. Partial days are recorded
as excluded diagnostics and never scaled, annualized or extrapolated into a
daily average. The Phase 3A 30-minute pilot therefore cannot produce an
authoritative daily-liquidity statistic -- it correctly stays insufficient
history rather than becoming a 48x extrapolation.

**Causality.** A trade at ``T`` may only be justified by liquidity that was
knowable before ``T``. The trailing reference for an order timestamp is built
exclusively from complete UTC days *strictly before* the UTC date containing
``T``: never a future day, never the current (still-incomplete, still-unknown)
day, and never the entry bar's own realized turnover -- that figure only exists
once the bar has closed, so using it to authorize a fill at the bar's open would
be look-ahead. This module exposes no same-bar realized turnover at all.

**Policy is owner input, never a default.** :class:`LiquidityCapacityPolicyV1`
carries no economically meaningful default: a lookback, a minimum day count, a
participation limit and a stress ladder are risk decisions, not data-authority
decisions. Without an explicit policy this module returns
``BLOCKED``/:data:`REASON_MISSING_AUTHORIZED_CAPACITY_POLICY` rather than
inventing one -- a data-authority fix must never quietly become an invented risk
policy.

**What this evidence is not.** It is OHLCV/provider-turnover based daily
liquidity participation evidence and says so in
:data:`CRYPTO_LIQUIDITY_BASIS`. It makes no order-book, top-of-book or L2 claim;
it models no spread, queue position, market impact or fill probability; and it
never asserts that a trade would actually have filled. Top-of-book/L2 remains
deferred.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from itertools import pairwise
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .crypto_basis_mean_reversion_v1 import BasisMeanReversionResearchRunV1
from .ohlcv_volume_semantics import BarVolumeUnit
from .tradable_bar_evidence_v2 import (
    AuthoritativeTradableBarSeriesV2,
    AuthoritativeTradableBarV2,
)

_UTC: Final = timezone.utc
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.crypto_liquidity_capacity_v1")

#: Every arithmetic step below runs inside this context, so a caller's ambient
#: ``decimal`` precision can never change a content-addressed result.
_ARITHMETIC_CONTEXT: Final = Context(prec=34, rounding=ROUND_HALF_EVEN)

#: The frozen identity of this liquidity semantics. It is bound into every
#: content hash, so evidence produced under a future revision of these rules can
#: never collide with evidence produced under these ones.
CRYPTO_LIQUIDITY_SEMANTIC_VERSION: Final = "crypto-liquidity-capacity-v1"

#: An explicit, self-describing statement of what the participation figures are
#: derived from. Carried on the evidence so no reader can mistake it for an
#: order-book measurement.
CRYPTO_LIQUIDITY_BASIS: Final = "OHLCV_PROVIDER_PUBLISHED_QUOTE_TURNOVER_DAILY_V1"

#: The exact number of 1-minute bars a complete UTC liquidity day must hold.
COMPLETE_UTC_DAY_BAR_COUNT: Final = 1440

#: v1 is bounded to the same single interval the tradable-bar reader supports.
SUPPORTED_BAR_INTERVAL: Final = "1m"

_ONE_MINUTE: Final = timedelta(minutes=1)

STATUS_AVAILABLE: Final = "AVAILABLE"
STATUS_UNAVAILABLE: Final = "UNAVAILABLE"
STATUS_BLOCKED: Final = "BLOCKED"

#: No explicit owner-authorized capacity policy was supplied. This is a
#: governance gap, not a data gap, hence BLOCKED rather than UNAVAILABLE.
REASON_MISSING_AUTHORIZED_CAPACITY_POLICY: Final = "MISSING_AUTHORIZED_CAPACITY_POLICY"
#: Legacy/unitless OHLCV: the bars carry no Phase 3B.2 typed semantics at all.
REASON_MISSING_CANONICAL_QUOTE_TURNOVER: Final = "MISSING_CANONICAL_QUOTE_TURNOVER_SEMANTICS"
REASON_UNSUPPORTED_TURNOVER_UNIT: Final = "UNSUPPORTED_TURNOVER_UNIT"
REASON_UNSUPPORTED_VOLUME_UNIT: Final = "UNSUPPORTED_VOLUME_UNIT"
REASON_MIXED_TURNOVER_ASSET: Final = "MIXED_TURNOVER_ASSET"
REASON_MIXED_VOLUME_ASSET: Final = "MIXED_VOLUME_ASSET"
REASON_MIXED_VOLUME_SEMANTIC_VERSION: Final = "MIXED_VOLUME_SEMANTIC_VERSION"
REASON_UNSUPPORTED_BAR_INTERVAL: Final = "UNSUPPORTED_BAR_INTERVAL"
REASON_INSUFFICIENT_COMPLETE_LIQUIDITY_DAYS: Final = "INSUFFICIENT_COMPLETE_LIQUIDITY_DAYS"
REASON_INSUFFICIENT_PRIOR_LIQUIDITY_HISTORY: Final = "INSUFFICIENT_PRIOR_LIQUIDITY_HISTORY"
REASON_NO_CANONICAL_ORDER_EVENTS: Final = "NO_CANONICAL_ORDER_EVENTS"

#: Why a UTC date was kept out of the daily-average statistic.
EXCLUDED_DAY_INCOMPLETE_MINUTE_GRID: Final = "INCOMPLETE_UTC_MINUTE_GRID"

#: Why one order event could not be given a causal liquidity reference.
EVENT_INSUFFICIENT_PRIOR_COMPLETE_DAYS: Final = "INSUFFICIENT_PRIOR_COMPLETE_DAYS"
EVENT_NON_POSITIVE_TRAILING_LIQUIDITY: Final = "NON_POSITIVE_TRAILING_LIQUIDITY"


class CryptoLiquidityCapacityV1Error(ValueError):
    """Raised only for structurally contradictory or untrustworthy evidence.

    Ordinary data insufficiency -- legacy bars, partial days, too little prior
    history, no explicit policy -- is never an exception: it is a deterministic
    ``UNAVAILABLE``/``BLOCKED`` evidence result, matching this repository's
    established convention. This exception is reserved for inputs that cannot be
    reconciled at all: a run and a bar series describing different datasets or
    instruments, a duplicate bar-open timestamp, a non-finite turnover that
    survived upstream validation, or an invalid policy/capital ladder.
    """


def _wire(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


def _content_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(_wire(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _identity(kind: str, content_hash: str) -> UUID:
    return uuid5(_NAMESPACE, f"{kind}:{content_hash}")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CryptoLiquidityCapacityV1Error(f"{field_name}_must_be_timezone_aware")


# ---------------------------------------------------------------------------
# Explicit owner policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiquidityCapacityPolicyV1:
    """The explicit, owner-authorized capacity policy. No economic defaults.

    Every field is required and none of them carries a default value: a 20-day
    lookback, a 1% participation limit or a 50% stress haircut are owner risk
    decisions this module must not invent on the owner's behalf. Tests construct
    explicit fixture policies; production must pass one before any ``AVAILABLE``
    capacity evidence can exist.
    """

    policy_version: str
    #: The maximum number of most-recent complete prior UTC days the trailing
    #: liquidity reference may span.
    lookback_complete_days: int
    #: The minimum number of complete prior UTC days required before any
    #: liquidity reference may be claimed at all.
    minimum_complete_days: int
    #: The participation ceiling an order event must satisfy, as a fraction of
    #: the trailing average daily quote turnover.
    maximum_participation: Decimal
    #: The reduced-liquidity stress ladder, each ``0 < m <= 1``.
    reduced_liquidity_multipliers: tuple[Decimal, ...]

    def validate(self) -> None:
        if not self.policy_version.strip():
            raise CryptoLiquidityCapacityV1Error("capacity_policy_version_required")
        if self.lookback_complete_days < 1:
            raise CryptoLiquidityCapacityV1Error("lookback_complete_days_must_be_positive")
        if self.minimum_complete_days < 1:
            raise CryptoLiquidityCapacityV1Error("minimum_complete_days_must_be_positive")
        if self.minimum_complete_days > self.lookback_complete_days:
            raise CryptoLiquidityCapacityV1Error("minimum_complete_days_exceeds_lookback")
        limit = self.maximum_participation
        if not limit.is_finite() or not (Decimal("0") < limit <= Decimal("1")):
            raise CryptoLiquidityCapacityV1Error("maximum_participation_out_of_bounds")
        if not self.reduced_liquidity_multipliers:
            raise CryptoLiquidityCapacityV1Error("reduced_liquidity_multipliers_required")
        previous: Decimal | None = None
        for multiplier in self.reduced_liquidity_multipliers:
            if not multiplier.is_finite() or not (Decimal("0") < multiplier <= Decimal("1")):
                raise CryptoLiquidityCapacityV1Error("reduced_liquidity_multiplier_out_of_bounds")
            if previous is not None and multiplier <= previous:
                raise CryptoLiquidityCapacityV1Error(
                    "reduced_liquidity_multipliers_must_ascend_uniquely"
                )
            previous = multiplier

    def content_hash(self) -> str:
        return _content_hash(
            {
                "policy_version": self.policy_version,
                "lookback_complete_days": self.lookback_complete_days,
                "minimum_complete_days": self.minimum_complete_days,
                "maximum_participation": self.maximum_participation,
                "reduced_liquidity_multipliers": self.reduced_liquidity_multipliers,
            }
        )


# ---------------------------------------------------------------------------
# Canonical liquidity semantics judged over the whole bar series
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CanonicalTurnoverSemanticsV1:
    """The one coherent typed-semantics identity every eligible bar shares."""

    volume_unit: BarVolumeUnit
    volume_asset: str
    turnover_unit: BarVolumeUnit
    turnover_asset: str
    volume_semantic_version: str


def canonical_turnover_semantics(
    bar_series: AuthoritativeTradableBarSeriesV2,
) -> tuple[CanonicalTurnoverSemanticsV1 | None, tuple[str, ...]]:
    """Prove one coherent quote-turnover authority across every bar, fail closed.

    Returns ``(None, reasons)`` whenever the series cannot supply canonical
    quote-turnover liquidity: legacy/unitless bars, a turnover that is not a
    QUOTE_ASSET quantity, a volume that is not a BASE_ASSET quantity, or any
    disagreement between bars about the asset or the semantic version. Nothing
    is converted and no majority wins: a single incoherent bar disqualifies the
    whole series, because a daily sum over mixed units is not a measurement.
    """
    if bar_series.interval != SUPPORTED_BAR_INTERVAL:
        return None, (REASON_UNSUPPORTED_BAR_INTERVAL,)
    if not bar_series.bars:
        return None, (REASON_MISSING_CANONICAL_QUOTE_TURNOVER,)

    reasons: list[str] = []
    volume_units: set[BarVolumeUnit] = set()
    turnover_units: set[BarVolumeUnit] = set()
    volume_assets: set[str] = set()
    turnover_assets: set[str] = set()
    semantic_versions: set[str] = set()

    for bar in bar_series.bars:
        if (
            bar.volume_semantic_version is None
            or bar.turnover is None
            or bar.turnover_unit is None
            or bar.volume_unit is None
        ):
            return None, (REASON_MISSING_CANONICAL_QUOTE_TURNOVER,)
        if not bar.turnover.is_finite():
            raise CryptoLiquidityCapacityV1Error("non_finite_canonical_turnover")
        if bar.turnover < 0:
            raise CryptoLiquidityCapacityV1Error("negative_canonical_turnover")
        volume_units.add(bar.volume_unit)
        turnover_units.add(bar.turnover_unit)
        semantic_versions.add(bar.volume_semantic_version)
        if bar.volume_asset is not None:
            volume_assets.add(bar.volume_asset)
        if bar.turnover_asset is not None:
            turnover_assets.add(bar.turnover_asset)

    if turnover_units != {BarVolumeUnit.QUOTE_ASSET}:
        reasons.append(REASON_UNSUPPORTED_TURNOVER_UNIT)
    if volume_units != {BarVolumeUnit.BASE_ASSET}:
        reasons.append(REASON_UNSUPPORTED_VOLUME_UNIT)
    if len(turnover_assets) != 1:
        reasons.append(REASON_MIXED_TURNOVER_ASSET)
    if len(volume_assets) != 1:
        reasons.append(REASON_MIXED_VOLUME_ASSET)
    if len(semantic_versions) != 1:
        reasons.append(REASON_MIXED_VOLUME_SEMANTIC_VERSION)
    if reasons:
        return None, tuple(dict.fromkeys(reasons))

    return (
        CanonicalTurnoverSemanticsV1(
            volume_unit=BarVolumeUnit.BASE_ASSET,
            volume_asset=next(iter(volume_assets)),
            turnover_unit=BarVolumeUnit.QUOTE_ASSET,
            turnover_asset=next(iter(turnover_assets)),
            volume_semantic_version=next(iter(semantic_versions)),
        ),
        (),
    )


# ---------------------------------------------------------------------------
# Complete UTC liquidity days
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompleteLiquidityDayV1:
    """One whole UTC day of provider-published quote turnover."""

    day: date
    bar_count: int
    quote_turnover: Decimal


@dataclass(frozen=True, slots=True)
class ExcludedLiquidityDayV1:
    """A UTC date that exists in the evidence but is not a liquidity day.

    Recorded purely as a diagnostic. It never enters a daily-average statistic,
    and it is never scaled up to a notional full day.
    """

    day: date
    observed_bar_count: int
    reason: str


def _expected_minute_grid(day: date) -> tuple[datetime, ...]:
    midnight = datetime(day.year, day.month, day.day, tzinfo=_UTC)
    return tuple(midnight + index * _ONE_MINUTE for index in range(COMPLETE_UTC_DAY_BAR_COUNT))


def complete_liquidity_days(
    bars: Sequence[AuthoritativeTradableBarV2],
) -> tuple[tuple[CompleteLiquidityDayV1, ...], tuple[ExcludedLiquidityDayV1, ...]]:
    """Split bars into complete UTC liquidity days and excluded diagnostics.

    A UTC date qualifies only when it holds exactly the complete 1-minute grid
    ``00:00``..``23:59`` for that date: :data:`COMPLETE_UTC_DAY_BAR_COUNT`
    distinct bar opens with no gap. A duplicate bar-open timestamp is not an
    incomplete day but an untrustworthy one -- two rows claim to be the same
    minute and no authority here can say which is the bar -- so it fails closed.
    The daily figure is the exact sum of the provider-published quote turnover;
    it is never derived from volume and a price.
    """
    by_day: dict[date, dict[datetime, Decimal]] = {}
    for bar in bars:
        _require_aware(bar.bar_open_at, "bar_open_at")
        opened_at = bar.bar_open_at.astimezone(_UTC)
        turnover = bar.turnover
        if turnover is None:
            raise CryptoLiquidityCapacityV1Error("liquidity_day_requires_canonical_turnover")
        minutes = by_day.setdefault(opened_at.date(), {})
        if opened_at in minutes:
            raise CryptoLiquidityCapacityV1Error(
                f"duplicate_bar_open_for_liquidity_day:{opened_at.isoformat()}"
            )
        minutes[opened_at] = turnover

    complete: list[CompleteLiquidityDayV1] = []
    excluded: list[ExcludedLiquidityDayV1] = []
    for day in sorted(by_day):
        minutes = by_day[day]
        if len(minutes) != COMPLETE_UTC_DAY_BAR_COUNT or set(minutes) != set(
            _expected_minute_grid(day)
        ):
            excluded.append(
                ExcludedLiquidityDayV1(
                    day=day,
                    observed_bar_count=len(minutes),
                    reason=EXCLUDED_DAY_INCOMPLETE_MINUTE_GRID,
                )
            )
            continue
        total = Decimal("0")
        for opened_at in sorted(minutes):
            total = total + minutes[opened_at]
        complete.append(
            CompleteLiquidityDayV1(day=day, bar_count=len(minutes), quote_turnover=total)
        )
    return tuple(complete), tuple(excluded)


# ---------------------------------------------------------------------------
# Causal trailing liquidity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrailingLiquidityReferenceV1:
    """The causal liquidity reference for one order timestamp.

    Every day named here closed strictly before the UTC date containing
    ``order_at``, so nothing in it was unknowable at the order's own timestamp.
    """

    order_at: datetime
    days_used: tuple[date, ...]
    daily_quote_turnovers: tuple[Decimal, ...]
    average_daily_quote_turnover: Decimal
    minimum_daily_quote_turnover: Decimal
    lookback_complete_days: int


def trailing_liquidity_reference(
    *,
    order_at: datetime,
    complete_days: Sequence[CompleteLiquidityDayV1],
    policy: LiquidityCapacityPolicyV1,
) -> tuple[TrailingLiquidityReferenceV1 | None, str | None]:
    """The trailing liquidity knowable before ``order_at``, or why there is none.

    Eligibility is by UTC *date*, strictly: a day qualifies only when
    ``day < order_at.date()`` in UTC. The current UTC day is excluded even if the
    evidence happens to hold all 1440 of its minutes, because at ``order_at``
    those later minutes had not happened yet; a future day is excluded for the
    same reason, more obviously.
    """
    _require_aware(order_at, "order_at")
    cutoff = order_at.astimezone(_UTC).date()
    eligible = [day for day in complete_days if day.day < cutoff]
    if len(eligible) < policy.minimum_complete_days:
        return None, EVENT_INSUFFICIENT_PRIOR_COMPLETE_DAYS
    window = sorted(eligible, key=lambda item: item.day)[-policy.lookback_complete_days :]
    turnovers = tuple(item.quote_turnover for item in window)
    total = Decimal("0")
    for turnover in turnovers:
        total = total + turnover
    average = _ARITHMETIC_CONTEXT.divide(total, Decimal(len(turnovers)))
    if average <= 0:
        return None, EVENT_NON_POSITIVE_TRAILING_LIQUIDITY
    return (
        TrailingLiquidityReferenceV1(
            order_at=order_at,
            days_used=tuple(item.day for item in window),
            daily_quote_turnovers=turnovers,
            average_daily_quote_turnover=average,
            minimum_daily_quote_turnover=min(turnovers),
            lookback_complete_days=policy.lookback_complete_days,
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Order events (exposure transitions), participation and capacity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiquidityOrderEventV1:
    """One required exposure transition and the liquidity knowable before it.

    ``exposure_delta`` is the absolute change in the strategy's *dimensionless*
    signed exposure at this timestamp. A candidate capital ``C`` turns it into a
    quote-asset order notional as ``C * exposure_delta`` -- no price is involved,
    because the liquidity it is measured against is already a quote-asset
    figure.
    """

    order_at: datetime
    previous_exposure: Decimal
    target_exposure: Decimal
    exposure_delta: Decimal
    trailing_liquidity: TrailingLiquidityReferenceV1 | None
    unavailable_reason: str | None


def _exposure_transitions(
    run: BasisMeanReversionResearchRunV1,
) -> tuple[tuple[datetime, Decimal], ...]:
    """The strategy's required (timestamp, target exposure) path, chronologically.

    Each executed trade requires two transitions: flat -> signed exposure at its
    entry bar open, and signed exposure -> flat at its exit bar open. Two
    transitions claiming the same timestamp would make the required order size
    at that instant ambiguous, so that fails closed rather than being merged by
    an arbitrary rule.
    """
    transitions: list[tuple[datetime, Decimal]] = []
    for trade in run.executed_trades:
        _require_aware(trade.entry_time, "trade_entry_time")
        _require_aware(trade.exit_time, "trade_exit_time")
        transitions.append((trade.entry_time.astimezone(_UTC), trade.exposure))
        transitions.append((trade.exit_time.astimezone(_UTC), Decimal("0")))
    transitions.sort(key=lambda item: item[0])
    for previous, current in pairwise(transitions):
        if previous[0] == current[0]:
            raise CryptoLiquidityCapacityV1Error(
                f"ambiguous_exposure_transition_timestamp:{current[0].isoformat()}"
            )
    return tuple(transitions)


def _order_events(
    *,
    run: BasisMeanReversionResearchRunV1,
    complete_days: Sequence[CompleteLiquidityDayV1],
    policy: LiquidityCapacityPolicyV1,
) -> tuple[LiquidityOrderEventV1, ...]:
    events: list[LiquidityOrderEventV1] = []
    previous_exposure = Decimal("0")
    for order_at, target in _exposure_transitions(run):
        delta = abs(target - previous_exposure)
        reference, reason = trailing_liquidity_reference(
            order_at=order_at, complete_days=complete_days, policy=policy
        )
        events.append(
            LiquidityOrderEventV1(
                order_at=order_at,
                previous_exposure=previous_exposure,
                target_exposure=target,
                exposure_delta=delta,
                trailing_liquidity=reference,
                unavailable_reason=reason,
            )
        )
        previous_exposure = target
    return tuple(events)


@dataclass(frozen=True, slots=True)
class CapacityLevelEvidenceV1:
    """Participation evidence for exactly one candidate capital level.

    ``maximum_participation_satisfied`` is deliberately conservative: it is
    ``True`` only when every order event could be measured *and* the worst
    measured participation is within the policy limit. A level with an
    unmeasurable order event never claims to satisfy the limit on the strength
    of the events that happened to be measurable.
    """

    capital: Decimal
    maximum_order_notional: Decimal | None
    maximum_participation: Decimal | None
    average_participation: Decimal | None
    evaluated_order_event_count: int
    unavailable_order_event_count: int
    maximum_participation_satisfied: bool


@dataclass(frozen=True, slots=True)
class LiquidityCapacityEnvelopeV1:
    """One capacity envelope: every capital level under one liquidity scaling.

    ``capital_ceiling`` is the largest capital at which every *measured* order
    event stays within the participation limit. It follows directly from the
    definition of participation and nothing else::

        participation(C, event) = C * delta_event / liquidity_event <= P
                              <=> C <= P * liquidity_event / delta_event

    so the ceiling is the minimum of ``P * liquidity_event / delta_event`` over
    the measured events. There is no market-impact model, no square-root law, no
    spread term and no fill assumption in it.
    """

    liquidity_multiplier: Decimal
    capital_ceiling: Decimal | None
    capital_ceiling_covers_every_order_event: bool
    levels: tuple[CapacityLevelEvidenceV1, ...]


def _build_envelope(
    *,
    events: Sequence[LiquidityOrderEventV1],
    capital_levels: Sequence[Decimal],
    policy: LiquidityCapacityPolicyV1,
    multiplier: Decimal,
) -> LiquidityCapacityEnvelopeV1:
    measured: list[tuple[LiquidityOrderEventV1, Decimal]] = []
    unavailable = 0
    for event in events:
        reference = event.trailing_liquidity
        if reference is None:
            unavailable += 1
            continue
        liquidity = _ARITHMETIC_CONTEXT.multiply(
            reference.average_daily_quote_turnover, multiplier
        )
        if liquidity <= 0:
            unavailable += 1
            continue
        measured.append((event, liquidity))

    ceiling: Decimal | None = None
    for event, liquidity in measured:
        if event.exposure_delta <= 0:
            # A zero-size transition consumes no liquidity and therefore
            # imposes no ceiling; it still counts as measured.
            continue
        candidate = _ARITHMETIC_CONTEXT.divide(
            _ARITHMETIC_CONTEXT.multiply(policy.maximum_participation, liquidity),
            event.exposure_delta,
        )
        ceiling = candidate if ceiling is None else min(ceiling, candidate)

    levels: list[CapacityLevelEvidenceV1] = []
    for capital in capital_levels:
        notionals: list[Decimal] = []
        participations: list[Decimal] = []
        for event, liquidity in measured:
            notional = _ARITHMETIC_CONTEXT.multiply(capital, event.exposure_delta)
            notionals.append(notional)
            participations.append(_ARITHMETIC_CONTEXT.divide(notional, liquidity))
        if participations:
            total = Decimal("0")
            for participation in participations:
                total = total + participation
            average = _ARITHMETIC_CONTEXT.divide(total, Decimal(len(participations)))
            worst = max(participations)
            levels.append(
                CapacityLevelEvidenceV1(
                    capital=capital,
                    maximum_order_notional=max(notionals),
                    maximum_participation=worst,
                    average_participation=average,
                    evaluated_order_event_count=len(participations),
                    unavailable_order_event_count=unavailable,
                    maximum_participation_satisfied=(
                        unavailable == 0 and worst <= policy.maximum_participation
                    ),
                )
            )
        else:
            levels.append(
                CapacityLevelEvidenceV1(
                    capital=capital,
                    maximum_order_notional=None,
                    maximum_participation=None,
                    average_participation=None,
                    evaluated_order_event_count=0,
                    unavailable_order_event_count=unavailable,
                    maximum_participation_satisfied=False,
                )
            )
    return LiquidityCapacityEnvelopeV1(
        liquidity_multiplier=multiplier,
        capital_ceiling=ceiling,
        capital_ceiling_covers_every_order_event=(unavailable == 0),
        levels=tuple(levels),
    )


# ---------------------------------------------------------------------------
# The evidence artifact
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CryptoLiquidityCapacityEvidenceV1:
    """Immutable, content-addressed canonical liquidity/participation/capacity evidence.

    ``status`` is ``AVAILABLE`` only in the fully proven case: typed
    quote-turnover semantics, enough complete prior UTC days for the explicit
    policy at at least one order event, and an explicit policy. Everything else
    is ``UNAVAILABLE`` (a data gap) or ``BLOCKED`` (a governance gap), with the
    reasons named.
    """

    status: str
    reason: str
    unavailable_reasons: tuple[str, ...]
    liquidity_semantic_version: str
    liquidity_basis: str
    order_book_evidence: bool
    dataset_version_id: UUID
    dataset_content_hash: str | None
    instrument_id: str
    interval: str
    turnover_unit: str | None
    turnover_asset: str | None
    volume_unit: str | None
    volume_asset: str | None
    volume_semantic_version: str | None
    policy_version: str | None
    policy_content_hash: str | None
    lookback_complete_days: int | None
    minimum_complete_days: int | None
    maximum_participation: Decimal | None
    complete_days: tuple[CompleteLiquidityDayV1, ...]
    excluded_days: tuple[ExcludedLiquidityDayV1, ...]
    order_events: tuple[LiquidityOrderEventV1, ...]
    capital_levels: tuple[Decimal, ...]
    baseline_envelope: LiquidityCapacityEnvelopeV1 | None
    stress_envelopes: tuple[LiquidityCapacityEnvelopeV1, ...]
    source_run_content_hash: str
    content_hash: str
    evidence_id: UUID


def _validate_capital_levels(capital_levels: Sequence[Decimal]) -> tuple[Decimal, ...]:
    ordered = tuple(capital_levels)
    for capital in ordered:
        if not capital.is_finite() or capital <= 0:
            raise CryptoLiquidityCapacityV1Error("capacity_capital_levels_must_be_positive")
    if tuple(sorted(set(ordered))) != ordered:
        raise CryptoLiquidityCapacityV1Error("capacity_capital_levels_must_be_sorted_unique")
    return ordered


def _envelope_payload(envelope: LiquidityCapacityEnvelopeV1) -> dict[str, Any]:
    return {
        "liquidity_multiplier": envelope.liquidity_multiplier,
        "capital_ceiling": envelope.capital_ceiling,
        "capital_ceiling_covers_every_order_event": (
            envelope.capital_ceiling_covers_every_order_event
        ),
        "levels": [
            {
                "capital": level.capital,
                "maximum_order_notional": level.maximum_order_notional,
                "maximum_participation": level.maximum_participation,
                "average_participation": level.average_participation,
                "evaluated_order_event_count": level.evaluated_order_event_count,
                "unavailable_order_event_count": level.unavailable_order_event_count,
                "maximum_participation_satisfied": level.maximum_participation_satisfied,
            }
            for level in envelope.levels
        ],
    }


def _order_event_payload(event: LiquidityOrderEventV1) -> dict[str, Any]:
    reference = event.trailing_liquidity
    return {
        "order_at": event.order_at,
        "previous_exposure": event.previous_exposure,
        "target_exposure": event.target_exposure,
        "exposure_delta": event.exposure_delta,
        "unavailable_reason": event.unavailable_reason,
        "trailing_liquidity": None
        if reference is None
        else {
            "days_used": reference.days_used,
            "daily_quote_turnovers": reference.daily_quote_turnovers,
            "average_daily_quote_turnover": reference.average_daily_quote_turnover,
            "minimum_daily_quote_turnover": reference.minimum_daily_quote_turnover,
            "lookback_complete_days": reference.lookback_complete_days,
        },
    }


def _build_evidence(
    *,
    status: str,
    reasons: tuple[str, ...],
    bar_series: AuthoritativeTradableBarSeriesV2,
    run: BasisMeanReversionResearchRunV1,
    semantics: CanonicalTurnoverSemanticsV1 | None,
    policy: LiquidityCapacityPolicyV1 | None,
    complete_days: tuple[CompleteLiquidityDayV1, ...],
    excluded_days: tuple[ExcludedLiquidityDayV1, ...],
    order_events: tuple[LiquidityOrderEventV1, ...],
    capital_levels: tuple[Decimal, ...],
    baseline_envelope: LiquidityCapacityEnvelopeV1 | None,
    stress_envelopes: tuple[LiquidityCapacityEnvelopeV1, ...],
) -> CryptoLiquidityCapacityEvidenceV1:
    dataset_content_hash = bar_series.bars[0].dataset_content_hash if bar_series.bars else None
    payload: dict[str, Any] = {
        "status": status,
        "unavailable_reasons": reasons,
        "liquidity_semantic_version": CRYPTO_LIQUIDITY_SEMANTIC_VERSION,
        "liquidity_basis": CRYPTO_LIQUIDITY_BASIS,
        "order_book_evidence": False,
        "dataset_version_id": bar_series.dataset_version_id,
        "dataset_content_hash": dataset_content_hash,
        "instrument_id": bar_series.instrument_id,
        "interval": bar_series.interval,
        "turnover_unit": None if semantics is None else semantics.turnover_unit.value,
        "turnover_asset": None if semantics is None else semantics.turnover_asset,
        "volume_unit": None if semantics is None else semantics.volume_unit.value,
        "volume_asset": None if semantics is None else semantics.volume_asset,
        "volume_semantic_version": None if semantics is None else semantics.volume_semantic_version,
        "policy_version": None if policy is None else policy.policy_version,
        "policy_content_hash": None if policy is None else policy.content_hash(),
        "lookback_complete_days": None if policy is None else policy.lookback_complete_days,
        "minimum_complete_days": None if policy is None else policy.minimum_complete_days,
        "maximum_participation": None if policy is None else policy.maximum_participation,
        "reduced_liquidity_multipliers": ()
        if policy is None
        else policy.reduced_liquidity_multipliers,
        "complete_days": [
            {"day": item.day, "bar_count": item.bar_count, "quote_turnover": item.quote_turnover}
            for item in complete_days
        ],
        "excluded_days": [
            {"day": item.day, "observed_bar_count": item.observed_bar_count, "reason": item.reason}
            for item in excluded_days
        ],
        "order_events": [_order_event_payload(event) for event in order_events],
        "capital_levels": capital_levels,
        "baseline_envelope": None
        if baseline_envelope is None
        else _envelope_payload(baseline_envelope),
        "stress_envelopes": [_envelope_payload(item) for item in stress_envelopes],
        "source_run_content_hash": run.content_hash,
    }
    content_hash = _content_hash(payload)
    return CryptoLiquidityCapacityEvidenceV1(
        status=status,
        reason=reasons[0] if reasons else "",
        unavailable_reasons=reasons,
        liquidity_semantic_version=CRYPTO_LIQUIDITY_SEMANTIC_VERSION,
        liquidity_basis=CRYPTO_LIQUIDITY_BASIS,
        order_book_evidence=False,
        dataset_version_id=bar_series.dataset_version_id,
        dataset_content_hash=dataset_content_hash,
        instrument_id=bar_series.instrument_id,
        interval=bar_series.interval,
        turnover_unit=None if semantics is None else semantics.turnover_unit.value,
        turnover_asset=None if semantics is None else semantics.turnover_asset,
        volume_unit=None if semantics is None else semantics.volume_unit.value,
        volume_asset=None if semantics is None else semantics.volume_asset,
        volume_semantic_version=None if semantics is None else semantics.volume_semantic_version,
        policy_version=None if policy is None else policy.policy_version,
        policy_content_hash=None if policy is None else policy.content_hash(),
        lookback_complete_days=None if policy is None else policy.lookback_complete_days,
        minimum_complete_days=None if policy is None else policy.minimum_complete_days,
        maximum_participation=None if policy is None else policy.maximum_participation,
        complete_days=complete_days,
        excluded_days=excluded_days,
        order_events=order_events,
        capital_levels=capital_levels,
        baseline_envelope=baseline_envelope,
        stress_envelopes=stress_envelopes,
        source_run_content_hash=run.content_hash,
        content_hash=content_hash,
        evidence_id=_identity("crypto-liquidity-capacity-v1", content_hash),
    )


def evaluate_crypto_liquidity_capacity_v1(
    *,
    bar_series: AuthoritativeTradableBarSeriesV2,
    run: BasisMeanReversionResearchRunV1,
    capital_levels: Sequence[Decimal] = (),
    policy: LiquidityCapacityPolicyV1 | None = None,
) -> CryptoLiquidityCapacityEvidenceV1:
    """Build canonical liquidity/participation/capacity evidence, fail closed.

    The four outcomes this phase distinguishes, in the order they are decided:

    A. legacy/unitless (or otherwise incoherent) OHLCV -> ``UNAVAILABLE``;
    B. typed bars but not one complete UTC liquidity day exists, or not enough
       complete days strictly before any order event -> ``UNAVAILABLE``;
    C. typed bars with at least one complete day but no explicit owner policy ->
       ``BLOCKED``/:data:`REASON_MISSING_AUTHORIZED_CAPACITY_POLICY`;
    D. typed bars, sufficient causal history and an explicit valid policy ->
       ``AVAILABLE``.

    The A/B checks precede the policy check on purpose: a data gap is a data
    gap whether or not an owner ever authorizes a policy, and the "zero complete
    days" case needs no policy to decide -- no lookback can make an evidence set
    with no whole day produce a daily statistic.
    """
    bar_series.validate()
    if run.dataset_version_id != bar_series.dataset_version_id:
        raise CryptoLiquidityCapacityV1Error("liquidity_capacity_dataset_mismatch")
    if run.instrument_id != bar_series.instrument_id:
        raise CryptoLiquidityCapacityV1Error("liquidity_capacity_instrument_mismatch")
    levels = _validate_capital_levels(capital_levels)
    if policy is not None:
        policy.validate()

    with localcontext(_ARITHMETIC_CONTEXT):
        semantics, semantic_reasons = canonical_turnover_semantics(bar_series)
        if semantics is None:
            return _build_evidence(
                status=STATUS_UNAVAILABLE,
                reasons=semantic_reasons,
                bar_series=bar_series,
                run=run,
                semantics=None,
                policy=policy,
                complete_days=(),
                excluded_days=(),
                order_events=(),
                capital_levels=levels,
                baseline_envelope=None,
                stress_envelopes=(),
            )

        complete_days, excluded_days = complete_liquidity_days(bar_series.bars)
        if not complete_days:
            return _build_evidence(
                status=STATUS_UNAVAILABLE,
                reasons=(REASON_INSUFFICIENT_COMPLETE_LIQUIDITY_DAYS,),
                bar_series=bar_series,
                run=run,
                semantics=semantics,
                policy=policy,
                complete_days=(),
                excluded_days=excluded_days,
                order_events=(),
                capital_levels=levels,
                baseline_envelope=None,
                stress_envelopes=(),
            )

        if policy is None:
            return _build_evidence(
                status=STATUS_BLOCKED,
                reasons=(REASON_MISSING_AUTHORIZED_CAPACITY_POLICY,),
                bar_series=bar_series,
                run=run,
                semantics=semantics,
                policy=None,
                complete_days=complete_days,
                excluded_days=excluded_days,
                order_events=(),
                capital_levels=levels,
                baseline_envelope=None,
                stress_envelopes=(),
            )

        order_events = _order_events(run=run, complete_days=complete_days, policy=policy)
        if not order_events:
            return _build_evidence(
                status=STATUS_UNAVAILABLE,
                reasons=(REASON_NO_CANONICAL_ORDER_EVENTS,),
                bar_series=bar_series,
                run=run,
                semantics=semantics,
                policy=policy,
                complete_days=complete_days,
                excluded_days=excluded_days,
                order_events=(),
                capital_levels=levels,
                baseline_envelope=None,
                stress_envelopes=(),
            )
        if all(event.trailing_liquidity is None for event in order_events):
            return _build_evidence(
                status=STATUS_UNAVAILABLE,
                reasons=(REASON_INSUFFICIENT_PRIOR_LIQUIDITY_HISTORY,),
                bar_series=bar_series,
                run=run,
                semantics=semantics,
                policy=policy,
                complete_days=complete_days,
                excluded_days=excluded_days,
                order_events=order_events,
                capital_levels=levels,
                baseline_envelope=None,
                stress_envelopes=(),
            )

        baseline = _build_envelope(
            events=order_events, capital_levels=levels, policy=policy, multiplier=Decimal("1")
        )
        stress = tuple(
            _build_envelope(
                events=order_events, capital_levels=levels, policy=policy, multiplier=multiplier
            )
            for multiplier in policy.reduced_liquidity_multipliers
        )
        return _build_evidence(
            status=STATUS_AVAILABLE,
            reasons=(),
            bar_series=bar_series,
            run=run,
            semantics=semantics,
            policy=policy,
            complete_days=complete_days,
            excluded_days=excluded_days,
            order_events=order_events,
            capital_levels=levels,
            baseline_envelope=baseline,
            stress_envelopes=stress,
        )


# ---------------------------------------------------------------------------
# Reduced-liquidity stress projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CryptoReducedLiquidityStressEvidenceV1:
    """The reduced-liquidity stress view of one capacity evidence artifact.

    Stress lives only here. It never rewrites historical OHLCV, never
    synthesizes an alternative provider observation, and is never written back
    to the Historical Data Authority: it is a scaling of the canonical trailing
    liquidity inside a validation artifact, nothing more.
    """

    status: str
    reason: str
    synthetic_validation_evidence: bool
    liquidity_semantic_version: str
    policy_version: str | None
    reduced_liquidity_multipliers: tuple[Decimal, ...]
    envelopes: tuple[LiquidityCapacityEnvelopeV1, ...]
    capacity_content_hash: str
    source_run_content_hash: str
    content_hash: str
    evidence_id: UUID


def build_reduced_liquidity_stress_evidence_v1(
    *, capacity: CryptoLiquidityCapacityEvidenceV1
) -> CryptoReducedLiquidityStressEvidenceV1:
    """Project a capacity artifact's stress ladder into its own sealed artifact.

    When the underlying capacity evidence is not ``AVAILABLE`` this carries the
    exact same status and reason: a stress result computed on top of liquidity
    the repository could not establish would be a fabricated number wearing a
    stress label.
    """
    multipliers = tuple(item.liquidity_multiplier for item in capacity.stress_envelopes)
    payload: dict[str, Any] = {
        "synthetic_validation_evidence": True,
        "status": capacity.status,
        "reason": capacity.reason,
        "liquidity_semantic_version": capacity.liquidity_semantic_version,
        "policy_version": capacity.policy_version,
        "policy_content_hash": capacity.policy_content_hash,
        "reduced_liquidity_multipliers": multipliers,
        "envelopes": [_envelope_payload(item) for item in capacity.stress_envelopes],
        "capacity_content_hash": capacity.content_hash,
        "source_run_content_hash": capacity.source_run_content_hash,
    }
    content_hash = _content_hash(payload)
    return CryptoReducedLiquidityStressEvidenceV1(
        status=capacity.status,
        reason=capacity.reason,
        synthetic_validation_evidence=True,
        liquidity_semantic_version=capacity.liquidity_semantic_version,
        policy_version=capacity.policy_version,
        reduced_liquidity_multipliers=multipliers,
        envelopes=capacity.stress_envelopes,
        capacity_content_hash=capacity.content_hash,
        source_run_content_hash=capacity.source_run_content_hash,
        content_hash=content_hash,
        evidence_id=_identity("crypto-reduced-liquidity-stress-v1", content_hash),
    )
