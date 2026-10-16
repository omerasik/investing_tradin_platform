"""OR-6 F3 forward -- venue-published mark price observations around a funding settlement instant.

``RESEARCH_ONLY``. Pure and offline: no socket, no database, no migration, no
cost. It reads the sealed ``T4_REFERENCE_PRICE`` frame of first-party T4
datasets (:mod:`trade_platform.first_party_t4_seal_v1`) and answers one
question per funding settlement instant: *which ``markPrice`` observations did
the venue publish on our connection at, or around, that instant, and what does
the sealed evidence prove about them?* It never chooses a mark price the
evidence does not pin down, and it never produces a funding charge.

What a mark observation is
--------------------------
One ``markPrice`` field the venue sent in a ``tickers`` message, as the
canonical normalization (:mod:`trade_platform.first_party_t4_normalization_v1`)
emitted it: component ``MARK`` of the sealed reference-price frame. It is
**venue-published** (:data:`VENUE_PUBLISHED_MARK_PRICE`) and is a different
type from every trade-derived price: :class:`T4MarkObservationV1` can only be
built from a ``MARK`` reference row, and the resolver refuses trade evidence
(:class:`~trade_platform.first_party_t4_forward_bars_v1.T4ForwardDatasetBarsV1`,
:class:`~trade_platform.first_party_t4_forward_bars_v1.T4PriceReferenceV1`) by
name instead of reading a price out of it.

The clocks are kept apart, never merged:

* **event time** -- the ticker message's venue ``ts`` (integer ms). Bybit
  publishes no separate mark-computation time, so this is the time the venue
  stamped the message that carried the mark;
* **host arrival** -- the recorder's raw wall-clock reading (ns);
* **clock uncertainty** -- the venue-minus-host *upper* bound of the session's
  RTT-bounded sample bracket, with its evidence reference
  (:data:`~trade_platform.first_party_t4_normalization_v1.CLOCK_BOUND_RULE_V1`);
* **market knowledge** -- ``ceil_us(arrival) + ceil_us(max(0, bound))``
  (:func:`~trade_platform.first_party_t4_normalization_v1.market_knowledge_micros_v1`),
  re-derived from the raw fields; the stored column is a claim, not an input.

Statuses (rule :data:`EXACT_RULE_V1`)
-------------------------------------
``EXACT_VENUE_TIMESTAMP_MATCH``
    One sealed segment holds one or more mark observations whose venue ``ts``
    equals the settlement millisecond, all with the same price; the same
    segment also holds a ticker row stamped before and one stamped after it.
    Ticker ``ts`` is checked non-decreasing within a segment, so the row after
    the instant proves no further message stamped at the instant follows, and
    the row before it proves none preceded the segment. Distinct prices at the
    instant inside one segment are refused as ambiguous ordering -- the venue
    gave them one timestamp and nothing proven orders them.
``NEARBY_ONLY_UNRESOLVED_SELECTION_CONVENTION``
    One segment brackets the instant (ticker rows before and after it) and
    holds no mark observation stamped at it. The last mark observation before
    and the first after the instant in that segment are *reported* with their
    signed distances; **neither is selected** and no price is returned. Which
    of them (the delta-carried state, the next update, neither) stands for the
    mark at the instant, and within what distance, is an owner convention
    (:data:`UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION`). There
    is deliberately no tolerance parameter.

Everything else is refused (:class:`T4MarkPriceRefusalV1`, a named code, never
a fallback): an instant not bracketed by one sealed segment (a reconnect,
recorder restart or segment boundary between the rows before and after it --
nothing is carried across), any cited row whose host-arrival hour is not
COMPLETE for the symbol under the session that proves it, conflicting exact
prices across supplied datasets, a symbol or venue mismatch, and a result not
yet knowable at the caller's ``known_by``.

Even ``EXACT`` is not the settlement mark. Bybit's funding history publishes
the rate, not the mark it was applied to; that a ticker ``markPrice`` stamped at
the settlement millisecond *is* the settlement mark is unproven, so every result
carries :data:`UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY` and nothing
here is ``CONFIRMED`` or costable.

Hours are judged on host arrival
--------------------------------
Hourly acceptance (:mod:`trade_platform.first_party_capture_hourly_acceptance_v1`)
is built from proven coverage windows, which are host-arrival spans. Each row a
result cites must therefore lie in a COMPLETE host-arrival hour proven by its
own dataset's session; with the host behind the venue, a mark stamped at a
venue hour boundary can arrive in the previous host hour, and that hour is the
one checked. A COMPLETE hour may still leave up to ``max_unproven`` of its head
unproven -- exactly where hourly segments hand off -- so an hour verdict alone
never proves absence at an instant; the one-segment bracket does.

Knowledge and identity
----------------------
A result is knowable at the latest market knowledge of every row it cites
(including the bracketing rows: until the row after the instant arrived, no one
could know that the set at the instant was closed). ``known_by`` is a query
parameter, not identity: the same sealed inputs give the same
:attr:`T4SettlementMarkPriceV1.resolution_hash` whenever they are resolved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from .bybit_forward_settled_funding_v1 import ForwardFundingError, canonical_symbol_identity_v1
from .first_party_capture_authority_v1 import (
    BYBIT_PUBLIC_LINEAR_ENDPOINT_V1,
    ORIGINATING_EXCHANGE_BYBIT_V1,
    resolve_t4_registered_first_party_capture_contract_v1,
)
from .first_party_capture_hourly_acceptance_v1 import HOUR_NANOS, HourlyAcceptanceV1
from .first_party_t4_forward_bars_v1 import (
    T4ForwardDatasetBarsV1,
    T4HourEvidenceV1,
    T4PriceReferenceV1,
)
from .first_party_t4_normalization_v1 import (
    CLOCK_BOUND_RULE_V1,
    COMPONENT_INDEX,
    COMPONENT_MARK,
    T4_NORMALIZATION_SEMANTIC_VERSION_V1,
    T4ReferencePriceV1,
    T4SegmentOutputV1,
    market_knowledge_micros_v1,
    micros_to_datetime_v1,
    sha256_json_v1,
)
from .first_party_t4_seal_v1 import FirstPartyT4SealV1, iter_frame_rows_v1
from .research_data_plane_v1 import T4_REFERENCE_PRICE_FRAME, ResearchFrameStoreV1

T4_SETTLEMENT_MARK_PRICE_SEMANTIC_VERSION_V1: Final = "first-party-t4-settlement-mark-price-1.0.0"
EXACT_RULE_V1: Final = "ticker-mark-venue-ts-equals-settlement-ms-within-one-bracketing-segment-v1"

VENUE_PUBLISHED_MARK_PRICE: Final = "VENUE_PUBLISHED_MARK_PRICE"

UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY: Final = "UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY"
UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION: Final = (
    "UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION")

#: The sealed reference-price column is ``decimal128(38, 18)``.
PRICE_QUANTUM_V1: Final = Decimal(1).scaleb(-T4_REFERENCE_PRICE_FRAME.schema.field("price").type.scale)

_NANOS_PER_MICRO: Final = 1_000
_MICROS_PER_MILLI: Final = 1_000
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class T4MarkEvidenceError(ValueError):
    """Raised when sealed mark evidence is malformed, out of order or does not re-derive."""


class T4MarkPriceRefusalV1(ValueError):
    """A mark-price resolution that cannot be proven. ``code`` names why; nothing is substituted."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class T4SettlementMarkStatusV1(StrEnum):
    EXACT_VENUE_TIMESTAMP_MATCH = "EXACT_VENUE_TIMESTAMP_MATCH"
    NEARBY_ONLY_UNRESOLVED_SELECTION_CONVENTION = "NEARBY_ONLY_UNRESOLVED_SELECTION_CONVENTION"


def _to_micros(value: datetime) -> int:
    return (value - _EPOCH) // timedelta(microseconds=1)


def _aware_micros(value: object, name: str) -> int:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise T4MarkEvidenceError(f"{name}_not_an_aware_instant")
    return _to_micros(value.astimezone(UTC))


def _exact_price(value: object) -> Decimal:
    """``value`` at :data:`PRICE_QUANTUM_V1`: exact or refused, never rounded."""
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise T4MarkEvidenceError("mark_price_not_a_positive_decimal")
    try:
        quantized = value.quantize(PRICE_QUANTUM_V1)
    except InvalidOperation as error:
        raise T4MarkEvidenceError("mark_price_not_representable_at_frame_scale") from error
    if quantized != value:
        raise T4MarkEvidenceError("mark_price_not_exact_at_frame_scale")
    return quantized


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class T4TickerRowClockV1:
    """One sealed ticker reference row: venue time and the clocks of the record that carried it."""

    observation_reference: str
    component: str
    message_type: str
    exchange_ts_millis: int
    session_id: UUID
    record_sequence: int
    record_content_hash: str
    arrival_utc_nanos: int
    clock_bound_nanos: int
    clock_bound_evidence: str

    @property
    def market_knowledge_micros(self) -> int:
        return market_knowledge_micros_v1(self.arrival_utc_nanos, self.clock_bound_nanos)

    def clock_payload(self) -> dict[str, Any]:
        return {
            "observation_reference": self.observation_reference,
            "component": self.component,
            "message_type": self.message_type,
            "event_ts_millis": self.exchange_ts_millis,
            "session_id": str(self.session_id),
            "record_sequence": self.record_sequence,
            "record_content_hash": self.record_content_hash,
            "arrival_utc_nanos": self.arrival_utc_nanos,
            "clock_bound_nanos": self.clock_bound_nanos,
            "clock_bound_evidence": self.clock_bound_evidence,
            "market_knowledge_micros": self.market_knowledge_micros,
        }


@dataclass(frozen=True, slots=True)
class T4MarkObservationV1:
    """One venue-published ``markPrice`` observation. Never a trade price."""

    clock: T4TickerRowClockV1
    price: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.clock, T4TickerRowClockV1) or self.clock.component != COMPONENT_MARK:
            raise T4MarkEvidenceError("mark_observation_requires_a_mark_reference_row")
        if _exact_price(self.price).as_tuple() != self.price.as_tuple():
            raise T4MarkEvidenceError("mark_price_not_at_frame_scale")

    @property
    def evidence_kind(self) -> str:
        return VENUE_PUBLISHED_MARK_PRICE

    @property
    def exchange_ts_millis(self) -> int:
        return self.clock.exchange_ts_millis

    @property
    def market_knowledge_micros(self) -> int:
        return self.clock.market_knowledge_micros

    def payload(self) -> dict[str, Any]:
        return {"evidence_kind": self.evidence_kind, "price": str(self.price), **self.clock.clock_payload()}


def _row_clock(item: T4ReferencePriceV1) -> T4TickerRowClockV1:
    arrival = item.arrival
    return T4TickerRowClockV1(
        observation_reference=item.observation_reference,
        component=item.component,
        message_type=item.message_type,
        exchange_ts_millis=item.exchange_ts_millis,
        session_id=arrival.session_id,
        record_sequence=arrival.record_sequence,
        record_content_hash=arrival.record_content_hash,
        arrival_utc_nanos=arrival.arrival_utc_nanos,
        clock_bound_nanos=arrival.clock_bound_nanos,
        clock_bound_evidence=arrival.clock_bound_evidence,
    )


def mark_observation_from_reference_v1(item: T4ReferencePriceV1) -> T4MarkObservationV1:
    """A normalized ``MARK`` reference as a mark observation; an ``INDEX`` one is refused."""
    if not isinstance(item, T4ReferencePriceV1):
        raise T4MarkEvidenceError("mark_observation_requires_a_mark_reference_row")
    return T4MarkObservationV1(_row_clock(item), _exact_price(item.price))


def _check_order(rows: Sequence[T4TickerRowClockV1]) -> None:
    """Rows in frame order: one record's rows agree; records advance in sequence, arrival and venue ts."""
    previous: T4TickerRowClockV1 | None = None
    for row in rows:
        if row.component not in (COMPONENT_MARK, COMPONENT_INDEX):
            raise T4MarkEvidenceError("reference_component_not_recognized")
        if previous is not None and row.record_sequence == previous.record_sequence:
            same = (row.record_content_hash, row.exchange_ts_millis, row.arrival_utc_nanos,
                    row.clock_bound_nanos, row.clock_bound_evidence, row.message_type) == (
                previous.record_content_hash, previous.exchange_ts_millis, previous.arrival_utc_nanos,
                previous.clock_bound_nanos, previous.clock_bound_evidence, previous.message_type)
            if not same or row.component <= previous.component:
                raise T4MarkEvidenceError("record_rows_disagree_or_repeat_a_component")
        elif previous is not None and (
            row.record_sequence < previous.record_sequence
            or row.arrival_utc_nanos < previous.arrival_utc_nanos
            or row.exchange_ts_millis < previous.exchange_ts_millis
        ):
            raise T4MarkEvidenceError("ticker_order_not_monotonic_within_segment")
        previous = row


def mark_observations_from_segment_output_v1(output: T4SegmentOutputV1) -> tuple[T4MarkObservationV1, ...]:
    """Mark observations straight from the record-at-a-time normalizer (the live path's shape).

    Observations only: a live segment has no sealed dataset or acceptance yet,
    so it can never back a resolution. It exists so live and replay are proven
    to agree observation for observation.
    """
    rows = [_row_clock(item) for item in output.reference_prices]
    _check_order(rows)
    return tuple(mark_observation_from_reference_v1(item) for item in output.reference_prices
                 if item.component == COMPONENT_MARK)


# ---------------------------------------------------------------------------
# Sealed datasets
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class T4MarkEvidenceDatasetV1:
    """Every ticker reference row and mark observation of one sealed, raw-replayed T4 segment."""

    dataset_version_id: UUID
    dataset_content_hash: str
    source_id: UUID
    session_id: UUID
    instrument: str
    exchange_symbol: str
    segment_clock_evidence_hash: str
    ticker_rows: tuple[T4TickerRowClockV1, ...]
    marks: tuple[T4MarkObservationV1, ...]

    def identity_payload(self) -> dict[str, Any]:
        return {
            "dataset_version_id": str(self.dataset_version_id),
            "dataset_content_hash": self.dataset_content_hash,
            "source_id": str(self.source_id),
            "session_id": str(self.session_id),
            "instrument": self.instrument,
            "exchange_symbol": self.exchange_symbol,
            "segment_clock_evidence_hash": self.segment_clock_evidence_hash,
        }


def mark_evidence_from_seal_v1(seal: FirstPartyT4SealV1, *, store: ResearchFrameStoreV1) -> T4MarkEvidenceDatasetV1:
    """The mark evidence of one sealed dataset, read from its own reference-price frame.

    Only an intact seal rebuilt from raw capture (``raw_replayed``) of a
    T4-registered Bybit linear source is accepted; the frame is re-verified
    byte for byte before any row is read, and every row's knowledge time is
    re-derived from its raw clock fields.
    """
    if not isinstance(seal, FirstPartyT4SealV1) or not seal.integrity_verified():
        raise T4MarkEvidenceError("mark_evidence_requires_an_intact_seal")
    if not seal.raw_replayed:
        raise T4MarkEvidenceError("mark_evidence_requires_a_seal_rebuilt_from_raw_capture")
    contract = resolve_t4_registered_first_party_capture_contract_v1(seal.source_id)
    if contract is None:
        raise T4MarkEvidenceError("mark_evidence_requires_a_t4_registered_source")
    if (contract.originating_exchange, contract.endpoint) != (
            ORIGINATING_EXCHANGE_BYBIT_V1, BYBIT_PUBLIC_LINEAR_ENDPOINT_V1):
        raise T4MarkEvidenceError("mark_evidence_requires_the_bybit_public_linear_feed")
    if seal.timing_facts.observations_missing_knowledge_time != 0:
        raise T4MarkEvidenceError("seal_has_observations_without_knowledge_time")
    store.verify(store.load_manifest(seal.frame_manifests[T4_REFERENCE_PRICE_FRAME.kind]))
    identity = seal.identity
    try:
        exchange_symbol = str(identity["exchange_symbol"])
        instrument = str(identity["instrument"])
        session_id = UUID(str(identity["session_id"]))
        clock_hash = str(identity["clock_evidence"]["clock_evidence_hash"])
    except (KeyError, TypeError, ValueError) as error:
        raise T4MarkEvidenceError("seal_identity_malformed") from error
    if (exchange_symbol, instrument) != (contract.exchange_symbol, contract.instrument_scope):
        raise T4MarkEvidenceError("seal_identity_differs_from_its_capture_contract")

    names = T4_REFERENCE_PRICE_FRAME.schema.names
    index = {name: names.index(name) for name in names}
    rows: list[T4TickerRowClockV1] = []
    marks: list[T4MarkObservationV1] = []
    for raw in iter_frame_rows_v1(seal, T4_REFERENCE_PRICE_FRAME.kind, store):
        row = T4TickerRowClockV1(
            observation_reference=str(raw[index["observation_reference"]]),
            component=str(raw[index["component"]]),
            message_type=str(raw[index["message_type"]]),
            exchange_ts_millis=int(str(raw[index["exchange_ts_millis"]])),
            session_id=UUID(str(raw[index["session_id"]])),
            record_sequence=int(str(raw[index["record_sequence"]])),
            record_content_hash=str(raw[index["record_content_hash"]]),
            arrival_utc_nanos=int(str(raw[index["arrival_utc_nanos"]])),
            clock_bound_nanos=int(str(raw[index["clock_bound_nanos"]])),
            clock_bound_evidence=str(raw[index["clock_bound_evidence"]]),
        )
        if row.session_id != session_id:
            raise T4MarkEvidenceError("reference_row_from_another_session")
        if not row.clock_bound_evidence.startswith(f"{CLOCK_BOUND_RULE_V1}:"):
            raise T4MarkEvidenceError("reference_row_clock_evidence_missing")
        if row.observation_reference != f"t4-ref:{row.record_content_hash}:{row.component}":
            raise T4MarkEvidenceError("reference_row_provenance_does_not_rederive")
        # The stored event and knowledge columns are claims; the raw fields decide.
        if _aware_micros(raw[index["event_at"]], "event_at") != row.exchange_ts_millis * _MICROS_PER_MILLI:
            raise T4MarkEvidenceError("reference_event_time_does_not_rederive")
        if _aware_micros(raw[index["market_knowledge_at"]], "market_knowledge_at") != row.market_knowledge_micros:
            raise T4MarkEvidenceError("reference_knowledge_does_not_rederive_from_its_clock")
        if row.exchange_ts_millis * _MICROS_PER_MILLI > row.market_knowledge_micros:
            raise T4MarkEvidenceError("reference_knowledge_before_its_venue_event")
        rows.append(row)
        if row.component == COMPONENT_MARK:
            marks.append(T4MarkObservationV1(row, _exact_price(raw[index["price"]])))
    _check_order(rows)
    return T4MarkEvidenceDatasetV1(
        dataset_version_id=seal.dataset_version_id,
        dataset_content_hash=seal.content_hash,
        source_id=seal.source_id,
        session_id=session_id,
        instrument=instrument,
        exchange_symbol=exchange_symbol,
        segment_clock_evidence_hash=clock_hash,
        ticker_rows=tuple(rows),
        marks=tuple(marks),
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class T4NearbyMarkV1:
    """A mark observation near the instant: reported with its signed distance, never selected."""

    side: str  # "BEFORE" or "AFTER"
    offset_millis: int
    observation: T4MarkObservationV1

    def payload(self) -> dict[str, Any]:
        return {"side": self.side, "offset_millis": self.offset_millis, "observation": self.observation.payload()}


@dataclass(frozen=True, slots=True)
class T4SettlementMarkPriceV1:
    """What sealed T4 evidence proves about the venue mark price at one settlement instant.

    ``mark_price`` is set only for ``EXACT_VENUE_TIMESTAMP_MATCH``. Even then it
    is a ticker mark stamped at the settlement millisecond, not a proven
    settlement mark (see ``unresolved_decisions``).
    """

    status: T4SettlementMarkStatusV1
    settled_at_ms: int
    symbol_identity: dict[str, str]
    dataset: T4MarkEvidenceDatasetV1
    mark_price: Decimal | None
    exact: tuple[T4MarkObservationV1, ...]
    nearby: tuple[T4NearbyMarkV1, ...]
    bracket_before: T4TickerRowClockV1
    bracket_after: T4TickerRowClockV1
    corroborating: tuple[T4MarkObservationV1, ...]
    hours: tuple[T4HourEvidenceV1, ...]
    unresolved_decisions: tuple[str, ...]

    @property
    def evidence_kind(self) -> str:
        return VENUE_PUBLISHED_MARK_PRICE

    def _cited(self) -> list[T4TickerRowClockV1]:
        return [self.bracket_before, self.bracket_after, *(item.clock for item in self.exact),
                *(item.observation.clock for item in self.nearby)]

    @property
    def knowable_at_micros(self) -> int:
        """The latest market knowledge of every row the result cites (an upper bound)."""
        return max(row.market_knowledge_micros for row in self._cited())

    @property
    def knowable_at(self) -> datetime:
        return micros_to_datetime_v1(self.knowable_at_micros)

    @property
    def min_clock_bound_nanos(self) -> int:
        return min(row.clock_bound_nanos for row in self._cited())

    @property
    def max_clock_bound_nanos(self) -> int:
        return max(row.clock_bound_nanos for row in self._cited())

    def payload(self) -> dict[str, Any]:
        return {
            "semantic_version": T4_SETTLEMENT_MARK_PRICE_SEMANTIC_VERSION_V1,
            "normalization_semantic_version": T4_NORMALIZATION_SEMANTIC_VERSION_V1,
            "exact_rule": EXACT_RULE_V1,
            "evidence_kind": self.evidence_kind,
            "status": self.status.value,
            "settled_at_ms": self.settled_at_ms,
            "symbol": self.symbol_identity,
            "dataset": self.dataset.identity_payload(),
            "mark_price": None if self.mark_price is None else str(self.mark_price),
            "price_quantum": str(PRICE_QUANTUM_V1),
            "exact": [item.payload() for item in self.exact],
            "nearby": [item.payload() for item in self.nearby],
            "bracket_before": self.bracket_before.clock_payload(),
            "bracket_after": self.bracket_after.clock_payload(),
            "corroborating": [item.payload() for item in self.corroborating],
            "hours": [item.payload() for item in self.hours],
            "knowable_at_micros": self.knowable_at_micros,
            "clock_bound_nanos": {"min": self.min_clock_bound_nanos, "max": self.max_clock_bound_nanos},
            "unresolved_decisions": list(self.unresolved_decisions),
        }

    @property
    def resolution_hash(self) -> str:
        return sha256_json_v1(self.payload())


def _settlement_ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise T4MarkPriceRefusalV1("settlement_instant_must_be_positive_integer_venue_millis")
    return value


def _hour(acceptance: HourlyAcceptanceV1, exchange_symbol: str, session_id: UUID,
          arrival_utc_nanos: int) -> T4HourEvidenceV1:
    """The COMPLETE host-arrival hour of one cited row, proven by the row's own session."""
    hour_start = arrival_utc_nanos // HOUR_NANOS * HOUR_NANOS
    symbols = [item for item in acceptance.symbols if item.symbol == exchange_symbol]
    if len(symbols) != 1:
        raise T4MarkPriceRefusalV1("hourly_acceptance_does_not_cover_the_symbol")
    first = acceptance.first_hour_start_utc_nanos
    if first is None or not first <= hour_start < first + acceptance.hours_judged * HOUR_NANOS:
        raise T4MarkPriceRefusalV1("hour_not_judged")
    if len(symbols[0].segments) != acceptance.hours_judged:
        raise T4MarkPriceRefusalV1("hourly_acceptance_malformed")
    segment = symbols[0].segments[(hour_start - first) // HOUR_NANOS]
    if segment.hour_start_utc_nanos != hour_start:
        raise T4MarkPriceRefusalV1("hourly_acceptance_malformed")
    if not segment.complete:
        raise T4MarkPriceRefusalV1(f"hour_not_complete:{segment.verdict.value}")
    if segment.session_id is None or segment.unproven_nanos is None:
        raise T4MarkPriceRefusalV1("hourly_acceptance_malformed")
    if segment.session_id != session_id:
        raise T4MarkPriceRefusalV1("hour_proven_by_another_capture_session")
    return T4HourEvidenceV1(hour_start, segment.session_id, segment.unproven_nanos, acceptance.max_unproven_nanos)


@dataclass(frozen=True, slots=True)
class _Bracket:
    before: T4TickerRowClockV1
    after: T4TickerRowClockV1
    exact: tuple[T4MarkObservationV1, ...]


def _bracket(dataset: T4MarkEvidenceDatasetV1, settled_at_ms: int) -> _Bracket | None:
    """The segment's last ticker row before and first after the instant, or ``None``."""
    before = [row for row in dataset.ticker_rows if row.exchange_ts_millis < settled_at_ms]
    after = [row for row in dataset.ticker_rows if row.exchange_ts_millis > settled_at_ms]
    if not before or not after:
        return None
    exact = tuple(item for item in dataset.marks if item.exchange_ts_millis == settled_at_ms)
    return _Bracket(before[-1], after[0], exact)


def resolve_settlement_mark_price_v1(
    datasets: Sequence[T4MarkEvidenceDatasetV1],
    *,
    symbol: str,
    settled_at_ms: int,
    acceptance: HourlyAcceptanceV1,
    known_by: datetime,
) -> T4SettlementMarkPriceV1:
    """The venue mark-price evidence at one settlement instant, or a named refusal.

    ``datasets`` are every sealed mark-evidence dataset the caller holds for
    ``symbol`` around the instant (from any capture session); exactly one must
    bracket the instant with its own hours COMPLETE. The others are checked for
    conflicting observations at the instant. ``settled_at_ms`` is the venue
    settlement instant (``fundingRateTimestamp``) exactly as published.
    """
    instant = _settlement_ms(settled_at_ms)
    try:
        identity = canonical_symbol_identity_v1(symbol)
    except ForwardFundingError as error:
        raise T4MarkPriceRefusalV1("symbol_outside_the_funding_universe") from error
    if not isinstance(known_by, datetime) or known_by.tzinfo is None:
        raise T4MarkPriceRefusalV1("known_by_must_be_an_aware_utc_instant")
    if not datasets:
        raise T4MarkPriceRefusalV1("no_mark_evidence_dataset_supplied")
    for item in datasets:
        if isinstance(item, T4ForwardDatasetBarsV1 | T4PriceReferenceV1):
            raise T4MarkPriceRefusalV1("trade_price_evidence_is_never_mark_price_evidence")
        if not isinstance(item, T4MarkEvidenceDatasetV1):
            raise T4MarkPriceRefusalV1("mark_evidence_must_come_from_a_sealed_reference_frame")
        if item.exchange_symbol != symbol:
            raise T4MarkPriceRefusalV1("dataset_symbol_differs_from_the_settlement_symbol")
    if len({item.dataset_version_id for item in datasets}) != len(datasets):
        raise T4MarkPriceRefusalV1("dataset_supplied_more_than_once")

    ordered = sorted(datasets, key=lambda item: str(item.dataset_version_id))
    bracketing = [(item, found) for item in ordered if (found := _bracket(item, instant)) is not None]
    if not bracketing:
        raise T4MarkPriceRefusalV1("settlement_instant_not_bracketed_by_one_sealed_segment")

    accepted: list[tuple[T4MarkEvidenceDatasetV1, _Bracket, tuple[T4HourEvidenceV1, ...]]] = []
    refusals: list[str] = []
    for item, found in bracketing:
        cited = [found.before, found.after, *(mark.clock for mark in found.exact)]
        try:
            cited_hours = {_hour(acceptance, symbol, item.session_id, row.arrival_utc_nanos) for row in cited}
        except T4MarkPriceRefusalV1 as refusal:
            refusals.append(refusal.code)
            continue
        accepted.append((item, found, tuple(sorted(cited_hours, key=lambda hour: hour.hour_start_utc_nanos))))
    if not accepted:
        raise T4MarkPriceRefusalV1(refusals[0])
    if len(accepted) != 1:
        raise T4MarkPriceRefusalV1("settlement_instant_proven_by_more_than_one_segment")
    dataset, found, hours = accepted[0]

    exact_prices = {item.price for item in found.exact}
    if len(exact_prices) > 1:
        raise T4MarkPriceRefusalV1("ambiguous_ordering_distinct_marks_stamped_at_the_instant")
    others = tuple(mark for item in ordered if item is not dataset for mark in item.marks
                   if mark.exchange_ts_millis == instant)
    if any(mark.price not in exact_prices for mark in others):
        raise T4MarkPriceRefusalV1("conflicting_mark_observations_at_the_instant")

    unresolved = [UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY]
    nearby: tuple[T4NearbyMarkV1, ...] = ()
    if found.exact:
        status = T4SettlementMarkStatusV1.EXACT_VENUE_TIMESTAMP_MATCH
        mark_price: Decimal | None = found.exact[0].price
    else:
        status = T4SettlementMarkStatusV1.NEARBY_ONLY_UNRESOLVED_SELECTION_CONVENTION
        mark_price = None
        unresolved.append(UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION)
        before = [mark for mark in dataset.marks if mark.exchange_ts_millis < instant]
        after = [mark for mark in dataset.marks if mark.exchange_ts_millis > instant]
        candidates = []
        if before:
            candidates.append(T4NearbyMarkV1("BEFORE", before[-1].exchange_ts_millis - instant, before[-1]))
        if after:
            candidates.append(T4NearbyMarkV1("AFTER", after[0].exchange_ts_millis - instant, after[0]))
        extra = {_hour(acceptance, symbol, dataset.session_id, candidate.observation.clock.arrival_utc_nanos)
                 for candidate in candidates}
        hours = tuple(sorted({*hours, *extra}, key=lambda hour: hour.hour_start_utc_nanos))
        nearby = tuple(candidates)

    result = T4SettlementMarkPriceV1(
        status=status,
        settled_at_ms=instant,
        symbol_identity=identity,
        dataset=dataset,
        mark_price=mark_price,
        exact=found.exact,
        nearby=nearby,
        bracket_before=found.before,
        bracket_after=found.after,
        corroborating=others,
        hours=hours,
        unresolved_decisions=tuple(unresolved),
    )
    if result.knowable_at_micros > _to_micros(known_by.astimezone(UTC)):
        raise T4MarkPriceRefusalV1("mark_evidence_not_yet_knowable_at_known_by")
    return result


__all__ = [
    "EXACT_RULE_V1",
    "PRICE_QUANTUM_V1",
    "T4_SETTLEMENT_MARK_PRICE_SEMANTIC_VERSION_V1",
    "UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION",
    "UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY",
    "VENUE_PUBLISHED_MARK_PRICE",
    "T4MarkEvidenceDatasetV1",
    "T4MarkEvidenceError",
    "T4MarkObservationV1",
    "T4MarkPriceRefusalV1",
    "T4NearbyMarkV1",
    "T4SettlementMarkPriceV1",
    "T4SettlementMarkStatusV1",
    "T4TickerRowClockV1",
    "mark_evidence_from_seal_v1",
    "mark_observation_from_reference_v1",
    "mark_observations_from_segment_output_v1",
    "resolve_settlement_mark_price_v1",
]
