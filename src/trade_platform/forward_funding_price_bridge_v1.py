"""OR-6 F3 forward -- bind settled funding evidence to proven T4 price evidence, without a cost.

``RESEARCH_ONLY``. Pure and offline: no socket, no database, no migration, no
candidate evaluation. It joins two separately proven evidence kinds per funding
settlement and says, deterministically and with an identity hash, how far the
pair goes toward a forward funding charge -- and, in v1, why it stops short.

Inputs and their relationships
------------------------------
* Funding: :mod:`~trade_platform.bybit_forward_settled_funding_v1` observations,
  loaded only from its append-only :class:`ForwardSettledFundingStoreV1` (every
  record re-derived from its raw body; any drift refuses the load) and judged
  here by its own :func:`forward_funding_window_as_of_v1` over ``[start, end)``
  as of one decision instant -- never a caller-built observation or window. Event time is the venue settlement instant
  (``fundingRateTimestamp``, ms); knowledge time is each observation's own
  bounded knowledge instant. Indicative ticker funding never gets here.
* Price: :mod:`~trade_platform.first_party_t4_forward_bars_v1` sealed datasets
  plus the manifest-proven :class:`HourlyAcceptanceV1`, resolved only through
  :func:`resolve_t4_price_reference_v1` with the caller's declared
  :class:`T4PriceConventionV1` and ``known_by`` = the same decision instant.
* Symbol: the canonical funding symbol (``BYBIT:linear:<SYMBOL>``) must equal
  every dataset's ``exchange_symbol``; a mismatch is a caller error.
* Instant: the settlement instant is passed to the resolver as is. An instant
  off the minute grid is refused there, never rounded.
* Mark: :mod:`~trade_platform.first_party_t4_settlement_mark_price_v1` sealed
  mark-evidence datasets, resolved only through
  :func:`resolve_settlement_mark_price_v1` with the same symbol, the
  settlement's ``settled_at_ms`` as published, the same
  :class:`HourlyAcceptanceV1` and ``known_by`` = the same decision instant.
  ``mark_evidence`` is a required argument: an empty sequence is the explicit
  statement that the caller holds none.
* Knowledge: a settlement's evidence is knowable at ``max(earliest knowledge of
  an available observation that published it, the price reference's
  knowable_at)``; both are upper bounds and both are at or before the decision
  instant by construction. The mark record carries its own ``knowable_at``
  (the resolver refuses one later than the decision instant) and does not move
  the trade-proxy pair's.
* Hours: every hour either path cites is a host-arrival capture hour
  (``HOST_ARRIVAL_UTC``): the trade reference's ``hours`` from its bar's record
  arrival span (``first``/``last_arrival_utc_nanos``), the mark resolution's
  from each cited ticker row's arrival. Neither is taken from the venue minute.

Mark price versus trade proxy
-----------------------------
Bybit's published fee formula values a position at the *mark* price
(:data:`~trade_platform.bybit_funding_history_v1.PUBLISHED_SEMANTICS_V1`). Every
price :func:`resolve_t4_price_reference_v1` returns is a public last-traded
price, so it is recorded under ``price`` as ``TRADE_PRICE_PROXY``. Venue
``markPrice`` observations from sealed ``T4_REFERENCE_PRICE`` frames are
recorded under ``mark_price`` as ``VENUE_PUBLISHED_MARK_PRICE`` -- next to the
proxy, never instead of it, and never merged with it. Each settlement's mark
record has exactly one evidence class:

``EXACT``
    The resolver found ticker marks stamped at the settlement millisecond in
    one bracketing segment, all with one price. That is a venue *timestamp*
    match, not proof of the mark the venue applied at settlement (Bybit's
    funding history publishes the rate, not the mark), so the record carries
    :data:`EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK`.
``NEARBY``
    No mark is stamped at the instant; the last mark before and the first
    after it are reported with signed distances and **none is selected** --
    there is no ``mark_price``. ``mark_nearby_tolerance_ms`` is required and
    has no default: ``None`` declares no tolerance; an integer only restricts
    (each candidate is annotated ``within_caller_tolerance``, and if none is,
    the class is ``REFUSED`` with ``no_nearby_mark_within_the_caller_tolerance``).
    It never selects one, and it is recorded in the identity.
``REFUSED``
    The resolver refused (its code kept verbatim), or the caller tolerance did.
``UNAVAILABLE``
    The caller supplied no mark-evidence dataset.

No class makes a settlement costable: the settlement-mark identity and the
nearby selection convention stay owner decisions.

The historical charge rule :data:`~trade_platform.bybit_funding_history_v1.FUNDING_CHARGE_RULE_V1`
uses the first print at or after the settlement instant as its mark proxy on
T2 bars. ``FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT`` makes the same selection
on T4; ``LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT`` does not, and each settlement
records which. Neither convention is owner-approved for forward use:
:func:`resolve_funding_price_convention_approval_v1` can only represent
``UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION``, so no charge amount is
calculated and ``CONFIRMED`` is reserved -- v1 never produces it.

Hourly segmentation
-------------------
Settlements fall on UTC hours, and the universe recorder writes hourly
segments. A T4 bar needs a trade before its minute *in the same segment*, so
the segment that starts at the hour cannot prove the hour's first minute: the
first-trade convention is refused there as a missing bar, and the settlement
carries ``FIRST_MINUTE_OF_AN_HOURLY_SEGMENT_IS_NOT_PROVABLE`` as a structural
note (a note, not a different status). The last-trade convention reads the
previous segment's last minute, which that hour's COMPLETE verdict must prove.

Statuses
--------
Per settlement: ``PRICE_EVIDENCE_REFUSED`` (the resolver's refusal code kept
verbatim) or ``EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED``. Overall, in precedence
order: ``REFUSED_FUNDING_EVIDENCE`` (the window is not COMPLETE: missing
records, coverage gaps, observations without a clock bound, conflicts, an
interval not known as of the instant, an open window), ``REFUSED_PRICE_EVIDENCE``
(any settlement refused), else ``EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED``.
Every result lists the decisions that keep it from being costable. Mark
evidence never changes either status: its classes are counted in
``mark_evidence_classes`` and named in ``reasons``.

Holdout and acquisition gates
-----------------------------
Nothing here reads, acquires or loads the historical T2 funding path, and no
candidate is evaluated: a consumer that evaluates a candidate on forward bars
still applies :class:`~trade_platform.live_signals_v1.LiveHoldoutGateV1`. A
:class:`~trade_platform.strategy_lab_validation_v1.HoldoutOpeningV1` offered as
an approval is refused by name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from .bybit_forward_settled_funding_v1 import (
    COMPLETE,
    ForwardFundingObservationV1,
    ForwardSettledFundingStoreV1,
    PublishedFundingIntervalV1,
    canonical_symbol_identity_v1,
    forward_funding_window_as_of_v1,
    resolve_forward_funding_acquisition_authorization_v1,
)
from .bybit_forward_settled_funding_v1 import (
    SOURCE_CONTRACT_HASH_V1 as FORWARD_FUNDING_SOURCE_CONTRACT_HASH_V1,
)
from .bybit_funding_history_v1 import (
    CALCULATED_SEMANTICS_V1,
    FUNDING_CHARGE_RULE_V1,
    PUBLISHED_SEMANTICS_V1,
)
from .first_party_capture_hourly_acceptance_v1 import HourlyAcceptanceV1
from .first_party_t4_forward_bars_v1 import (
    T4_FORWARD_BARS_SEMANTIC_VERSION_V1,
    T4ForwardDatasetBarsV1,
    T4PriceConventionV1,
    T4PriceReferenceRefusalV1,
    T4PriceReferenceV1,
    resolve_t4_price_reference_v1,
)
from .first_party_t4_settlement_mark_price_v1 import (
    EXACT_RULE_V1 as MARK_EXACT_RULE_V1,
)
from .first_party_t4_settlement_mark_price_v1 import (
    T4_SETTLEMENT_MARK_PRICE_SEMANTIC_VERSION_V1,
    UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION,
    UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY,
    T4MarkEvidenceDatasetV1,
    T4MarkPriceRefusalV1,
    T4SettlementMarkPriceV1,
    T4SettlementMarkStatusV1,
    resolve_settlement_mark_price_v1,
)
from .first_party_t4_settlement_mark_price_v1 import (
    VENUE_PUBLISHED_MARK_PRICE as _RESOLVER_MARK_KIND,
)
from .strategy_lab_study_v1 import identity_hash_v1
from .strategy_lab_validation_v1 import HoldoutOpeningV1

#: v1.1: settlements carry venue mark evidence next to the trade proxy, and the
#: trade proxy's host-arrival hours.
FUNDING_PRICE_BRIDGE_SCHEMA_VERSION_V1: Final = "forward-funding-price-bridge-v1.1"

#: The resolver's own constant: one evidence kind, one definition.
VENUE_PUBLISHED_MARK_PRICE: Final = _RESOLVER_MARK_KIND
TRADE_PRICE_PROXY: Final = "TRADE_PRICE_PROXY"

#: Mark-price evidence classes, one per settlement.
MARK_EVIDENCE_EXACT: Final = "EXACT"
MARK_EVIDENCE_NEARBY: Final = "NEARBY"
MARK_EVIDENCE_REFUSED: Final = "REFUSED"
MARK_EVIDENCE_UNAVAILABLE: Final = "UNAVAILABLE"
MARK_EVIDENCE_CLASSES_V1: Final = (
    MARK_EVIDENCE_EXACT, MARK_EVIDENCE_NEARBY, MARK_EVIDENCE_REFUSED, MARK_EVIDENCE_UNAVAILABLE)

EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK: Final = (
    "EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK")
NEARBY_MARKS_REPORTED_NONE_SELECTED: Final = "NEARBY_MARKS_REPORTED_NONE_SELECTED"
NO_MARK_EVIDENCE_DATASET_SUPPLIED: Final = "no_mark_evidence_dataset_supplied"
NO_NEARBY_MARK_WITHIN_THE_CALLER_TOLERANCE: Final = "no_nearby_mark_within_the_caller_tolerance"

HOST_ARRIVAL_UTC: Final = "HOST_ARRIVAL_UTC"

UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION: Final = "UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION"

PRICE_EVIDENCE_REFUSED: Final = "PRICE_EVIDENCE_REFUSED"
EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED: Final = "EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED"

REFUSED_FUNDING_EVIDENCE: Final = "REFUSED_FUNDING_EVIDENCE"
REFUSED_PRICE_EVIDENCE: Final = "REFUSED_PRICE_EVIDENCE"
EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED: Final = (
    "EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED")
#: Reserved for owner-approved venue mark-price evidence. v1 never produces it.
CONFIRMED: Final = "CONFIRMED"

FIRST_MINUTE_OF_AN_HOURLY_SEGMENT_IS_NOT_PROVABLE: Final = "FIRST_MINUTE_OF_AN_HOURLY_SEGMENT_IS_NOT_PROVABLE"

LONGS_PAY_SHORTS: Final = "LONGS_PAY_SHORTS"
SHORTS_PAY_LONGS: Final = "SHORTS_PAY_LONGS"
ZERO_RATE_NO_TRANSFER: Final = "ZERO_RATE_NO_TRANSFER"

#: How each declared T4 convention relates to the historical rule's mark proxy.
RELATION_TO_HISTORICAL_CHARGE_RULE_V1: Final = {
    T4PriceConventionV1.FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT.value:
        "SAME_SELECTION_AS_THE_HISTORICAL_POSITION_VALUE_REFERENCE",
    T4PriceConventionV1.LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT.value:
        "DIFFERS_FROM_THE_HISTORICAL_POSITION_VALUE_REFERENCE",
}

BRIDGE_CONTRACT_V1: Final = {
    "schema_version": FUNDING_PRICE_BRIDGE_SCHEMA_VERSION_V1,
    "funding_source_contract_hash": FORWARD_FUNDING_SOURCE_CONTRACT_HASH_V1,
    "price_semantic_version": T4_FORWARD_BARS_SEMANTIC_VERSION_V1,
    "published_fee_formula": PUBLISHED_SEMANTICS_V1["fee_formula"],
    "published_payer_rule": PUBLISHED_SEMANTICS_V1["payer_rule"],
    "historical_charge_rule": FUNDING_CHARGE_RULE_V1,
    "historical_position_value_reference": CALCULATED_SEMANTICS_V1["position_value_reference"],
    "price_evidence_kind": TRADE_PRICE_PROXY,
    "price_hour_time_domain": HOST_ARRIVAL_UTC,
    "mark_price_evidence": "VENUE_TICKER_MARK_BESIDE_THE_TRADE_PROXY_NEVER_INSTEAD_NOT_A_PROVEN_SETTLEMENT_MARK",
    "mark_price_evidence_kind": VENUE_PUBLISHED_MARK_PRICE,
    "mark_price_semantic_version": T4_SETTLEMENT_MARK_PRICE_SEMANTIC_VERSION_V1,
    "mark_exact_rule": MARK_EXACT_RULE_V1,
    "mark_evidence_classes": list(MARK_EVIDENCE_CLASSES_V1),
    "mark_exact_meaning": EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK,
    "mark_nearby_selection": "NONE_SELECTED_CALLER_TOLERANCE_ONLY_RESTRICTS_NO_DEFAULT",
    "mark_hour_time_domain": HOST_ARRIVAL_UTC,
    "mark_known_by": "DECISION_INSTANT",
    "charge": "NOT_CALCULATED_PRICE_CONVENTION_UNAPPROVED",
    "evidence_knowable_at": "MAX_EARLIEST_FUNDING_OBSERVATION_KNOWLEDGE_AND_PRICE_REFERENCE_KNOWABLE_AT",
}
BRIDGE_CONTRACT_HASH_V1: Final = identity_hash_v1(BRIDGE_CONTRACT_V1)

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_HOUR_MS: Final = 3_600_000


class ForwardFundingPriceBridgeError(ValueError):
    """Raised for caller errors: mismatched symbols, an undeclared convention, the wrong kind of approval."""


def _iso_ms(ms: int) -> str:
    return (_EPOCH + timedelta(milliseconds=ms)).isoformat()


# ---------------------------------------------------------------------------
# Price-convention approval (fail closed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FundingPriceConventionApprovalV1:
    """The resolved owner decision on which price values a forward settlement. v1: unresolved only."""

    state: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state != UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION:
            raise ForwardFundingPriceBridgeError("funding_price_convention_approval_state_not_defined_in_v1")


def resolve_funding_price_convention_approval_v1() -> FundingPriceConventionApprovalV1:
    return FundingPriceConventionApprovalV1(UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION, (
        "NO_OWNER_DECISION_ON_THE_FORWARD_SETTLEMENT_POSITION_VALUE_PRICE",
        UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY,
        UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION,
    ))


def _approval(value: object) -> FundingPriceConventionApprovalV1:
    if value is None:
        return resolve_funding_price_convention_approval_v1()
    if isinstance(value, HoldoutOpeningV1):
        raise ForwardFundingPriceBridgeError("holdout_opening_is_not_a_price_convention_approval")
    if not isinstance(value, FundingPriceConventionApprovalV1):
        raise ForwardFundingPriceBridgeError("price_convention_approval_must_be_the_resolved_approval")
    return value


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ForwardFundingPriceBridgeV1:
    """Per-settlement funding/price evidence for one symbol and window, as of one decision instant."""

    identity: Mapping[str, Any]
    content_hash: str

    @property
    def status(self) -> str:
        return str(self.identity["status"])

    @property
    def reasons(self) -> list[str]:
        return list(self.identity["reasons"])

    @property
    def settlements(self) -> list[Mapping[str, Any]]:
        return list(self.identity["settlements"])

    @property
    def unresolved_decisions(self) -> list[str]:
        return list(self.identity["unresolved_decisions"])


def payer_direction_v1(rate_text: str) -> str:
    """The published payer rule applied to a settled rate's sign: who pays whom, no amount."""
    rate = Decimal(rate_text)
    if rate > 0:
        return LONGS_PAY_SHORTS
    if rate < 0:
        return SHORTS_PAY_LONGS
    return ZERO_RATE_NO_TRANSFER


def _funding_knowledge(available: Sequence[ForwardFundingObservationV1], settled_at_ms: int,
                       rate_text: str) -> tuple[int, str]:
    """Earliest knowledge instant (and its observation) at which this exact record was published."""
    known = sorted((int(o.knowledge_at_micros), str(o.observation_id)) for o in available
                   if o.knowledge_at_micros is not None
                   and any(e.settled_at_ms == settled_at_ms and e.rate_text == rate_text for e in o.events()))
    if not known:  # the window only lists events of available observations
        raise ForwardFundingPriceBridgeError("settlement_without_an_available_observation")
    return known[0]


def _mark_record(evidence_class: str, *, refusal: str | None = None, result: T4SettlementMarkPriceV1 | None = None,
                 nearby: list[dict[str, Any]] | None = None, tolerance_ms: int | None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "evidence_kind": VENUE_PUBLISHED_MARK_PRICE,
        "evidence_class": evidence_class,
        "refusal": refusal,
        "meaning": None,
        "mark_price": None,
        "nearby": nearby or [],
        "nearby_tolerance_ms": tolerance_ms,
        "resolver_status": None,
        "resolution_hash": None,
        "resolution": None,
        "knowable_at_micros": None,
        "hour_time_domain": HOST_ARRIVAL_UTC,
        "unresolved_decisions": [],
    }
    if result is not None:
        record.update({
            "resolver_status": result.status.value,
            "resolution_hash": result.resolution_hash,
            "resolution": result.payload(),
            "knowable_at_micros": result.knowable_at_micros,
            "unresolved_decisions": list(result.unresolved_decisions),
        })
    return record


def _mark_settlement(event_ms: int, *, symbol: str, mark_evidence: Sequence[T4MarkEvidenceDatasetV1],
                     acceptance: HourlyAcceptanceV1, as_of: datetime,
                     tolerance_ms: int | None) -> dict[str, Any]:
    """The venue mark evidence at one settlement, in exactly one class. Never a selection."""
    if not mark_evidence:
        return _mark_record(MARK_EVIDENCE_UNAVAILABLE, refusal=NO_MARK_EVIDENCE_DATASET_SUPPLIED,
                            tolerance_ms=tolerance_ms)
    try:
        result = resolve_settlement_mark_price_v1(
            mark_evidence, symbol=symbol, settled_at_ms=event_ms, acceptance=acceptance, known_by=as_of)
    except T4MarkPriceRefusalV1 as refusal:
        return _mark_record(MARK_EVIDENCE_REFUSED, refusal=refusal.code, tolerance_ms=tolerance_ms)
    if result.status is T4SettlementMarkStatusV1.EXACT_VENUE_TIMESTAMP_MATCH:
        record = _mark_record(MARK_EVIDENCE_EXACT, result=result, tolerance_ms=tolerance_ms)
        record["meaning"] = EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK
        record["mark_price"] = str(result.mark_price)
        return record
    nearby = [{
        "side": item.side,
        "offset_millis": item.offset_millis,
        "price": str(item.observation.price),
        "observation_reference": item.observation.clock.observation_reference,
        "market_knowledge_micros": item.observation.market_knowledge_micros,
        "within_caller_tolerance": None if tolerance_ms is None else abs(item.offset_millis) <= tolerance_ms,
    } for item in result.nearby]
    if tolerance_ms is not None and not any(item["within_caller_tolerance"] for item in nearby):
        record = _mark_record(MARK_EVIDENCE_REFUSED, refusal=NO_NEARBY_MARK_WITHIN_THE_CALLER_TOLERANCE,
                              result=result, nearby=nearby, tolerance_ms=tolerance_ms)
    else:
        record = _mark_record(MARK_EVIDENCE_NEARBY, result=result, nearby=nearby, tolerance_ms=tolerance_ms)
    record["meaning"] = NEARBY_MARKS_REPORTED_NONE_SELECTED
    return record


def _settlement(
    event_ms: int, rate_text: str, *, available: Sequence[ForwardFundingObservationV1],
    datasets: Sequence[T4ForwardDatasetBarsV1], acceptance: HourlyAcceptanceV1,
    convention: T4PriceConventionV1, as_of: datetime, symbol: str,
    mark_evidence: Sequence[T4MarkEvidenceDatasetV1], tolerance_ms: int | None,
) -> dict[str, Any]:
    funding_known, observation_id = _funding_knowledge(available, event_ms, rate_text)
    record: dict[str, Any] = {
        "settled_at_ms": event_ms,
        "settled_at": _iso_ms(event_ms),
        "rate": rate_text,
        "payer_direction": payer_direction_v1(rate_text),
        "funding_knowledge_at_micros": funding_known,
        "funding_observation_id": observation_id,
        "price": None,
        "price_refusal": None,
        "structural_note": None,
        "evidence_knowable_at_micros": None,
        "mark_price": _mark_settlement(event_ms, symbol=symbol, mark_evidence=mark_evidence,
                                       acceptance=acceptance, as_of=as_of, tolerance_ms=tolerance_ms),
    }
    try:
        reference = resolve_t4_price_reference_v1(
            datasets, instant=_EPOCH + timedelta(milliseconds=event_ms), convention=convention,
            acceptance=acceptance, known_by=as_of)
    except T4PriceReferenceRefusalV1 as refusal:
        record["price_refusal"] = refusal.code
        if (refusal.code == "no_proven_bar_for_the_minute" and event_ms % _HOUR_MS == 0
                and convention is T4PriceConventionV1.FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT):
            record["structural_note"] = FIRST_MINUTE_OF_AN_HOURLY_SEGMENT_IS_NOT_PROVABLE
        record["status"] = PRICE_EVIDENCE_REFUSED
        return record
    knowable = reference.bar.complete_market_knowledge_micros
    record["price"] = {
        "evidence_kind": TRADE_PRICE_PROXY,
        "convention": convention.value,
        "relation_to_historical_charge_rule": RELATION_TO_HISTORICAL_CHARGE_RULE_V1[convention.value],
        "price": str(reference.price),
        "reference_hash": reference.reference_hash,
        "dataset_version_id": str(reference.dataset_version_id),
        "dataset_content_hash": reference.dataset_content_hash,
        "session_id": str(reference.session_id),
        "bar_content_hash": reference.bar.bar_content_hash,
        "bar_open_micros": reference.bar.bar_open_micros,
        "knowable_at_micros": knowable,
        "max_clock_bound_nanos": reference.bar.clock.max_clock_bound_nanos,
        # PR #232: hours are judged on the bar's records' host-arrival span.
        "first_arrival_utc_nanos": reference.bar.clock.first_arrival_utc_nanos,
        "last_arrival_utc_nanos": reference.bar.clock.last_arrival_utc_nanos,
        "hour_time_domain": HOST_ARRIVAL_UTC,
        "hours": [hour.payload() for hour in reference.hours],
    }
    record["evidence_knowable_at_micros"] = max(funding_known, knowable)
    record["status"] = EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED
    return record


def bridge_forward_funding_prices_v1(
    funding_store: ForwardSettledFundingStoreV1,
    *,
    symbol: str,
    start_ms: int,
    end_ms: int,
    as_of: datetime,
    published_interval: PublishedFundingIntervalV1 | None,
    datasets: Sequence[T4ForwardDatasetBarsV1],
    acceptance: HourlyAcceptanceV1,
    convention: T4PriceConventionV1,
    mark_evidence: Sequence[T4MarkEvidenceDatasetV1],
    mark_nearby_tolerance_ms: int | None,
    approval: object = None,
) -> ForwardFundingPriceBridgeV1:
    """Bind each settlement in ``[start_ms, end_ms)`` known as of ``as_of`` to its proven T4 price, or refuse.

    Each settlement also carries its venue mark evidence (``EXACT``, ``NEARBY``,
    ``REFUSED`` or ``UNAVAILABLE``) beside the trade proxy. ``mark_evidence``
    and ``mark_nearby_tolerance_ms`` have no defaults: ``[]`` states that no
    mark evidence is held, ``None`` that no tolerance is declared.
    """
    canonical = canonical_symbol_identity_v1(symbol)
    if not isinstance(funding_store, ForwardSettledFundingStoreV1):
        raise ForwardFundingPriceBridgeError("funding_evidence_must_come_from_the_forward_funding_store")
    if not isinstance(convention, T4PriceConventionV1):
        raise ForwardFundingPriceBridgeError("price_convention_must_be_declared")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ForwardFundingPriceBridgeError("as_of_must_be_an_aware_utc_instant")
    if any(item.exchange_symbol != symbol for item in datasets):
        raise ForwardFundingPriceBridgeError("price_dataset_symbol_differs_from_the_funding_symbol")
    if isinstance(mark_evidence, str | bytes) or not isinstance(mark_evidence, Sequence):
        raise ForwardFundingPriceBridgeError("mark_evidence_must_be_a_declared_sequence")
    for item in mark_evidence:
        if isinstance(item, T4ForwardDatasetBarsV1 | T4PriceReferenceV1):
            raise ForwardFundingPriceBridgeError("trade_price_evidence_is_never_mark_price_evidence")
        if not isinstance(item, T4MarkEvidenceDatasetV1):
            raise ForwardFundingPriceBridgeError("mark_evidence_must_come_from_a_sealed_reference_frame")
        if item.exchange_symbol != symbol:
            raise ForwardFundingPriceBridgeError("mark_dataset_symbol_differs_from_the_funding_symbol")
    if mark_nearby_tolerance_ms is not None and (
            isinstance(mark_nearby_tolerance_ms, bool) or not isinstance(mark_nearby_tolerance_ms, int)
            or mark_nearby_tolerance_ms < 0):
        raise ForwardFundingPriceBridgeError("mark_nearby_tolerance_must_be_none_or_non_negative_integer_millis")
    resolved_approval = _approval(approval)
    as_of_micros = (as_of.astimezone(UTC) - _EPOCH) // timedelta(microseconds=1)
    observations = funding_store.load(symbol)  # re-derived from raw bodies; drift refuses
    window = forward_funding_window_as_of_v1(
        observations, symbol=symbol, start_ms=start_ms, end_ms=end_ms, as_of_micros=as_of_micros,
        published_interval=published_interval)
    available = [o for o in observations if o.symbol == symbol and o.is_available_at(as_of_micros)]
    settlements = [
        _settlement(event.settled_at_ms, event.rate_text, available=available, datasets=datasets,
                    acceptance=acceptance, convention=convention, as_of=as_of, symbol=symbol,
                    mark_evidence=mark_evidence, tolerance_ms=mark_nearby_tolerance_ms)
        for event in window.events()
    ]

    reasons = [f"FUNDING_WINDOW:{item}" for item in window.irregularities]
    reasons += [f"PRICE_REFUSED:{item['settled_at']}:{item['price_refusal']}"
                for item in settlements if item["status"] == PRICE_EVIDENCE_REFUSED]
    marks = [item["mark_price"] for item in settlements]
    reasons += [f"MARK_{mark['evidence_class']}:{item['settled_at']}:{mark['refusal']}"
                for item, mark in zip(settlements, marks, strict=True)
                if mark["evidence_class"] in (MARK_EVIDENCE_REFUSED, MARK_EVIDENCE_UNAVAILABLE)]
    if any(mark["evidence_class"] == MARK_EVIDENCE_EXACT for mark in marks):
        reasons.append(EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK)
    if any(mark["evidence_class"] == MARK_EVIDENCE_NEARBY for mark in marks):
        reasons.append(NEARBY_MARKS_REPORTED_NONE_SELECTED)
    if window.status != COMPLETE:
        status = REFUSED_FUNDING_EVIDENCE
    elif any(item["status"] == PRICE_EVIDENCE_REFUSED for item in settlements):
        status = REFUSED_PRICE_EVIDENCE
    else:
        status = EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED
    reasons += ["PRICE_IS_A_TRADE_PROXY_NOT_THE_VENUE_MARK_PRICE", f"PRICE_CONVENTION:{resolved_approval.state}"]

    unresolved = [resolved_approval.state, *resolved_approval.reasons]
    acquisition = resolve_forward_funding_acquisition_authorization_v1()
    unresolved += [f"FORWARD_FUNDING_ACQUISITION:{acquisition.state}", *acquisition.reasons]

    identity = {
        "schema_version": FUNDING_PRICE_BRIDGE_SCHEMA_VERSION_V1,
        "bridge_contract_hash": BRIDGE_CONTRACT_HASH_V1,
        "symbol": canonical,
        "start_ms": start_ms,
        "end_ms_exclusive": end_ms,
        "as_of_micros": as_of_micros,
        "convention": convention.value,
        "funding_window": {"content_hash": window.content_hash, "status": window.status,
                           "irregularities": window.irregularities},
        "price_datasets": sorted(str(item.dataset_version_id) for item in datasets),
        "mark_evidence_datasets": sorted([str(item.dataset_version_id), item.dataset_content_hash]
                                         for item in mark_evidence),
        "mark_nearby_tolerance_ms": mark_nearby_tolerance_ms,
        "mark_evidence_classes": {name: sum(mark["evidence_class"] == name for mark in marks)
                                  for name in MARK_EVIDENCE_CLASSES_V1},
        "settlements": settlements,
        "status": status,
        "reasons": reasons,
        "unresolved_decisions": unresolved,
    }
    return ForwardFundingPriceBridgeV1(identity, identity_hash_v1(identity))


__all__ = [
    "BRIDGE_CONTRACT_HASH_V1",
    "BRIDGE_CONTRACT_V1",
    "CONFIRMED",
    "EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED",
    "EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED",
    "EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK",
    "FIRST_MINUTE_OF_AN_HOURLY_SEGMENT_IS_NOT_PROVABLE",
    "FUNDING_PRICE_BRIDGE_SCHEMA_VERSION_V1",
    "HOST_ARRIVAL_UTC",
    "LONGS_PAY_SHORTS",
    "MARK_EVIDENCE_CLASSES_V1",
    "MARK_EVIDENCE_EXACT",
    "MARK_EVIDENCE_NEARBY",
    "MARK_EVIDENCE_REFUSED",
    "MARK_EVIDENCE_UNAVAILABLE",
    "NEARBY_MARKS_REPORTED_NONE_SELECTED",
    "NO_MARK_EVIDENCE_DATASET_SUPPLIED",
    "NO_NEARBY_MARK_WITHIN_THE_CALLER_TOLERANCE",
    "PRICE_EVIDENCE_REFUSED",
    "REFUSED_FUNDING_EVIDENCE",
    "REFUSED_PRICE_EVIDENCE",
    "RELATION_TO_HISTORICAL_CHARGE_RULE_V1",
    "SHORTS_PAY_LONGS",
    "TRADE_PRICE_PROXY",
    "UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION",
    "VENUE_PUBLISHED_MARK_PRICE",
    "ZERO_RATE_NO_TRANSFER",
    "ForwardFundingPriceBridgeError",
    "ForwardFundingPriceBridgeV1",
    "FundingPriceConventionApprovalV1",
    "bridge_forward_funding_prices_v1",
    "payer_direction_v1",
    "resolve_funding_price_convention_approval_v1",
]
