"""Phase R3A -- canonical first-party T4 normalization: clock bounds and streaming state.

``RESEARCH_ONLY``. Pure and offline: no socket, no database, no file. This module
turns *verified* first-party capture records into canonical observations whose
market knowledge time is re-derivable from sealed evidence. Sealing, frames and
authority live in :mod:`trade_platform.first_party_t4_seal_v1`; the doctrine that
consumes these clocks is :mod:`trade_platform.knowledge_time_doctrine_v1`.

It deliberately imports neither the doctrine nor the evidence-tier or provenance
authorities, so the provenance authority can depend on the seal without an
import cycle. The one piece of arithmetic it shares with the doctrine --
``market_knowledge_at = ceil_us(arrival) + ceil_us(bound)`` -- is restated in
:func:`market_knowledge_micros_v1` and asserted equal to
:meth:`~trade_platform.knowledge_time_doctrine_v1.HostClockBoundV1.venue_upper_bound`
in the tests.

One implementation for replay and live
--------------------------------------
:class:`T4SegmentNormalizerV1` is a record-at-a-time state machine. Historical
sealing feeds it a verified partition replay; the future R8 streaming path is
meant to feed it records as they arrive, with the same state rules, so that
*captured window replay -> same normalization -> same features* holds by
construction rather than by keeping two implementations in step. Only the clock
bound differs between the two modes (a replay can bracket an arrival with a
later sample; live cannot), which is why the bound is an input, not a detail.

The clock bound (rule :data:`CLOCK_BOUND_RULE_V1`)
--------------------------------------------------
Arrivals are host wall-clock readings. The host ran ~9.8-10 s *behind* the
venue on 2026-09-24 with time sync stopped, so a raw arrival would label a
message as known ~10 s before it arrived -- look-ahead against venue-timed data.
Every capture session records RTT-bounded ``CLOCK_OFFSET_SAMPLE`` events in its
sealed lifecycle log. For one sample with host send ``s``, host receive ``r``,
venue server reading ``v`` and host resolution ``q``::

    upper = (v - (s + r) // 2) + (r - s) // 2 + q

bounds ``venue - host`` from above, provided the server read its clock between
the two host readings. This module re-derives that from the raw fields and
refuses a sample whose stated estimate or bound disagree (the stored derived
fields are claims, never inputs).

An arrival ``h`` is admissible only when it is *bracketed* within one session:
the latest sample with ``r_p <= h`` and the earliest with ``s_n >= h`` both
exist and are consecutive (``n = p + 1``, or ``n = p + 2`` when ``h`` falls
inside the middle sample's own measurement, which is then included). Its bound
is the maximum ``upper`` over ``p..n``. The bracket is refused when

* the two samples are more than :data:`MAX_CLOCK_BRACKET_NANOS` apart (an
  engineering tolerance tied to the recorder's 15-minute sampling cadence:
  at most one missed sample; it carries no economic meaning), or
* a *barrier* lies inside it: a recorded ``CLOCK_DISCONTINUITY`` (a wall-clock
  step the recorder detected) or the span of a same-session partition that is
  not COMPLETE (whose samples, if any, are not evidence).

The named assumptions: between two consecutive admissible samples the host's
offset from the venue moves monotonically (a free-running oscillator with no
step, which the barriers above exclude where the recorder saw one); the
venue's REST server clock (``/v5/market/time``) is the clock that stamps the
WebSocket messages; and the server reading's own resolution is below the
RTT/resolution slack already in the bound. An arrival with no admissible
bracket -- e.g. the tail after a session's last sample -- has **no** knowledge
time: it is excluded, never extrapolated. The ~9.47 s figure observed earlier
is one session's evidence, never a correction constant; every session is
bounded by its own samples.

State rules (from the capture contract, not re-invented)
--------------------------------------------------------
* Mark and index are independent components; each keeps its own event time and
  arrival. A ``snapshot`` replaces state (a component it does not carry becomes
  unobserved); a ``delta`` changes only the fields it carries.
* A basis exists only once both components are observed *in the current
  segment*. ``market_knowledge_at = max(mark, index)`` and ``event_at = max``
  of their venue timestamps; they are never presented as synchronized.
* A segment boundary (reconnect, contract violation, clock discontinuity, UTC
  partition rollover, the end of clock admissibility) resets all state. Nothing
  is carried across a gap and nothing is filled forward across one.

Trade bars (rule :data:`BAR_COMPLETION_RULE_V1`)
------------------------------------------------
Trades are ordered by evidence actually present: venue trade time ``T``, venue
``seq``, then arrival order (record sequence, position in the message). The
trade id is a UUID and carries no order, so it is never used as a tiebreak.
A leading/trailing ``(T, seq)`` group with more than one price is reported as
sequence-ambiguous rather than resolved by pretending.

A minute ``[open, close)`` produces a bar only when the segment *proves* it saw
the whole minute: a trade with venue time ``T < open`` arrived earlier in the
segment, and a later trade with ``T >= close`` arrived in the same segment.
This relies on the venue delivering public trades in non-decreasing
``(T, seq)`` on one connection -- which is *checked* on every trade, not
assumed: a regression (or a trade stamped later than its own message) ends the
segment's normalization with a named error, so the segment contributes nothing
rather than a bar built on an unproven order. Then::

    open_market_knowledge_at     = knowledge of the selected first trade
    complete_market_knowledge_at = max(close boundary, every contributing
                                       trade's knowledge, the knowledge of the
                                       record carrying the closing trade)

No empty bar, no forward fill; a minute still open when the segment ends is
dropped and counted. The ``open/close_is_sequence_ambiguous`` flags describe the
whole leading/trailing ``(T, seq)`` group, which may span later records, so
they are knowable only at ``complete_market_knowledge_at`` -- never at the
open's knowledge time. Volume and turnover are exact sums (refused, not
rounded, if a sum is not representable at its quantum).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from itertools import pairwise
from typing import Any, Final
from uuid import UUID

from .first_party_capture_archive_v1 import (
    CaptureLifecycleEventV1,
    CaptureLifecycleKindV1,
    FirstPartyCaptureRecordV1,
)
from .first_party_capture_authority_v1 import BybitMessageTypeV1, BybitPublicChannelV1

T4_NORMALIZATION_SEMANTIC_VERSION_V1: Final = "first-party-t4-normalization-1.0.0"
CLOCK_BOUND_RULE_V1: Final = "session-rtt-sample-bracket-max-upper-v1"
BAR_COMPLETION_RULE_V1: Final = "same-segment-trade-message-bracket-v1"
BASIS_FORMULA_V1: Final = "(mark_price - index_price) / index_price"

#: Same quantum as the 3D.9S.2A capture basis: exact and far below price steps.
BASIS_QUANTUM_V1: Final = Decimal("1E-18")
#: Bybit BTCUSDT steps are 0.1 USDT and 0.001 BTC; 1e-8 keeps every sum exact.
VOLUME_QUANTUM_V1: Final = Decimal("1E-8")
TURNOVER_QUANTUM_V1: Final = Decimal("1E-8")

_NANOS_PER_MICRO: Final = 1_000
_NANOS_PER_MILLI: Final = 1_000_000
_MICROS_PER_MINUTE: Final = 60_000_000
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)

COMPONENT_MARK: Final = "MARK"
COMPONENT_INDEX: Final = "INDEX"
#: Ordered by component name so one record's rows follow the frame sort key.
_COMPONENT_FIELDS: Final = ((COMPONENT_INDEX, "indexPrice"), (COMPONENT_MARK, "markPrice"))


class T4NormalizationError(ValueError):
    """Raised when evidence is malformed, ambiguous or violates a checked ordering rule."""


def canonical_json_v1(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def sha256_json_v1(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_v1(payload).encode()).hexdigest()


def micros_to_datetime_v1(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


def ceil_micros_v1(nanos: int) -> int:
    """Nanoseconds to microseconds, rounded *up* -- never earlier than the evidence."""
    return -(-nanos // _NANOS_PER_MICRO)


def market_knowledge_micros_v1(arrival_utc_nanos: int, bound_nanos: int) -> int:
    """The doctrine's T4 knowledge instant, in integer microseconds.

    ``ceil_us(arrival) + ceil_us(max(0, bound))`` -- exactly
    ``HostClockBoundV1(bound, ...).venue_upper_bound(nanos_to_datetime(arrival))``
    (a host running *ahead* of the venue adds nothing: that is conservative).
    """
    return ceil_micros_v1(arrival_utc_nanos) + ceil_micros_v1(max(0, bound_nanos))


def _millis_to_micros(millis: int) -> int:
    return millis * 1_000


# ---------------------------------------------------------------------------
# Clock evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClockOffsetSampleEvidenceV1:
    """One RTT-bounded offset sample, re-derived from its raw fields."""

    host_send_utc_nanos: int
    host_receive_utc_nanos: int
    server_utc_nanos: int
    host_clock_resolution_nanos: int
    recorded_at_utc_nanos: int
    sample_hash: str

    @property
    def offset_estimate_nanos(self) -> int:
        return self.server_utc_nanos - (self.host_send_utc_nanos + self.host_receive_utc_nanos) // 2

    @property
    def offset_bound_nanos(self) -> int:
        return (self.host_receive_utc_nanos - self.host_send_utc_nanos) // 2 + (
            self.host_clock_resolution_nanos
        )

    @property
    def venue_minus_host_upper_nanos(self) -> int:
        return self.offset_estimate_nanos + self.offset_bound_nanos

    def payload(self) -> dict[str, int | str]:
        return {
            "host_send_utc_nanos": self.host_send_utc_nanos,
            "host_receive_utc_nanos": self.host_receive_utc_nanos,
            "server_utc_nanos": self.server_utc_nanos,
            "host_clock_resolution_nanos": self.host_clock_resolution_nanos,
            "recorded_at_utc_nanos": self.recorded_at_utc_nanos,
            "venue_minus_host_upper_nanos": self.venue_minus_host_upper_nanos,
            "sample_hash": self.sample_hash,
        }


def _strict_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise T4NormalizationError(f"clock_sample_{name}_malformed")
    return value


def parse_clock_offset_sample_v1(
    event: CaptureLifecycleEventV1, *, session_resolution_nanos: int
) -> ClockOffsetSampleEvidenceV1:
    """Re-derive one ``CLOCK_OFFSET_SAMPLE`` from its raw readings, or refuse it.

    The stated ``offset_estimate_nanos`` / ``offset_bound_nanos`` must equal the
    values re-derived from the raw readings, the resolution must be the
    session's measured one, and the sample must have been recorded no earlier
    than it was received. Any disagreement is incoherent clock evidence.
    """
    if event.kind != CaptureLifecycleKindV1.CLOCK_OFFSET_SAMPLE.value or event.detail is None:
        raise T4NormalizationError("clock_sample_event_kind_mismatch")
    try:
        raw = json.loads(event.detail)
    except json.JSONDecodeError as error:
        raise T4NormalizationError("clock_sample_detail_is_not_json") from error
    if not isinstance(raw, dict):
        raise T4NormalizationError("clock_sample_detail_is_not_an_object")
    sample = ClockOffsetSampleEvidenceV1(
        host_send_utc_nanos=_strict_int(raw.get("host_send_utc_nanos"), "host_send"),
        host_receive_utc_nanos=_strict_int(raw.get("host_receive_utc_nanos"), "host_receive"),
        server_utc_nanos=_strict_int(raw.get("server_utc_nanos"), "server"),
        host_clock_resolution_nanos=_strict_int(raw.get("host_clock_resolution_nanos"), "resolution"),
        recorded_at_utc_nanos=event.arrival_utc_nanos,
        sample_hash=hashlib.sha256(event.to_json_line().encode()).hexdigest(),
    )
    if sample.host_receive_utc_nanos < sample.host_send_utc_nanos:
        raise T4NormalizationError("clock_sample_received_before_sent")
    if sample.host_clock_resolution_nanos < 1:
        raise T4NormalizationError("clock_sample_resolution_not_positive")
    if sample.host_clock_resolution_nanos != session_resolution_nanos:
        raise T4NormalizationError("clock_sample_resolution_differs_from_session")
    if sample.recorded_at_utc_nanos < sample.host_receive_utc_nanos:
        raise T4NormalizationError("clock_sample_recorded_before_received")
    if raw.get("offset_estimate_nanos") != sample.offset_estimate_nanos:
        raise T4NormalizationError("clock_sample_stated_estimate_not_derivable")
    if raw.get("offset_bound_nanos") != sample.offset_bound_nanos:
        raise T4NormalizationError("clock_sample_stated_bound_not_derivable")
    return sample


@dataclass(frozen=True, slots=True)
class ArrivalClockBoundEvidenceV1:
    """The bound one arrival receives, and exactly which samples justify it."""

    venue_minus_host_upper_bound_nanos: int
    prior_sample_hash: str
    next_sample_hash: str

    @property
    def evidence_reference(self) -> str:
        return (
            f"{CLOCK_BOUND_RULE_V1}:prior={self.prior_sample_hash}:next={self.next_sample_hash}"
        )


#: At most one missed 15-minute sample between the two samples of a bracket.
#: An engineering tolerance on the recorder's sampling cadence; no economic meaning.
MAX_CLOCK_BRACKET_NANOS: Final = 1_800_000_000_000


class SessionClockEvidenceV1:
    """One session's clock samples and barriers, ordered and bracket-searchable.

    ``barriers`` are half-open host-clock intervals ``[start, end)`` no bracket
    may span: a recorded clock discontinuity (a zero-width instant, given as
    ``(t, t + 1)``) or the UTC-day span of a same-session partition that is not
    COMPLETE.
    """

    __slots__ = ("_barriers", "_receives", "_samples", "_sends", "_valid_pairs", "session_id")

    def __init__(
        self,
        session_id: UUID,
        samples: Iterable[ClockOffsetSampleEvidenceV1],
        *,
        barriers: Iterable[tuple[int, int]] = (),
    ) -> None:
        ordered = sorted(samples, key=lambda item: (item.host_send_utc_nanos, item.sample_hash))
        for previous, following in pairwise(ordered):
            if following.host_send_utc_nanos < previous.host_receive_utc_nanos:
                # Two measurements that overlap in host time cannot come from one
                # sequential sampler; the evidence is not one coherent series.
                raise T4NormalizationError("clock_samples_overlap_within_session")
        self.session_id = session_id
        self._samples = tuple(ordered)
        self._barriers = tuple(sorted(barriers))
        self._receives = [item.host_receive_utc_nanos for item in self._samples]
        self._sends = [item.host_send_utc_nanos for item in self._samples]
        self._valid_pairs = [
            self._pair_ok(first, second) for first, second in pairwise(self._samples)
        ]

    def _pair_ok(self, first: ClockOffsetSampleEvidenceV1, second: ClockOffsetSampleEvidenceV1) -> bool:
        low, high = first.host_receive_utc_nanos, second.host_send_utc_nanos
        if high - low > MAX_CLOCK_BRACKET_NANOS:
            return False
        return not any(start < high and low < end for start, end in self._barriers)

    @property
    def samples(self) -> tuple[ClockOffsetSampleEvidenceV1, ...]:
        return self._samples

    @property
    def barriers(self) -> tuple[tuple[int, int], ...]:
        return self._barriers

    def admissible_intervals(self) -> tuple[tuple[int, int], ...]:
        """Maximal inclusive host-arrival intervals that every arrival in them can be bracketed.

        Pair ``i`` covers ``[r_i, s_{i+1}]``; the measurement window of sample
        ``i+1`` (``(s_{i+1}, r_{i+1})``) is covered only when pairs ``i`` and
        ``i+1`` are both admissible.
        """
        intervals: list[list[int]] = []
        for index, ok in enumerate(self._valid_pairs):
            if not ok:
                continue
            start = self._samples[index].host_receive_utc_nanos
            end = self._samples[index + 1].host_send_utc_nanos
            if (
                intervals
                and index > 0
                and self._valid_pairs[index - 1]
                and intervals[-1][1] == self._samples[index].host_send_utc_nanos
            ):
                intervals[-1][1] = end
            else:
                intervals.append([start, end])
        return tuple((start, end) for start, end in intervals if start <= end)

    def admissible_range(self) -> tuple[int, int] | None:
        """The hull of :meth:`admissible_intervals` (for reporting only)."""
        intervals = self.admissible_intervals()
        return None if not intervals else (intervals[0][0], intervals[-1][1])

    def bracket(self, arrival_utc_nanos: int) -> tuple[int, int] | None:
        """Indices ``(p, n)`` of the admissible bracket for one arrival, or ``None``."""
        from bisect import bisect_left, bisect_right

        prior = bisect_right(self._receives, arrival_utc_nanos) - 1
        following = bisect_left(self._sends, arrival_utc_nanos)
        if prior < 0 or following >= len(self._samples):
            return None
        if following == prior + 1:
            return (prior, following) if self._valid_pairs[prior] else None
        if following == prior + 2:
            # Inside the middle sample's own measurement: both pairs must hold.
            if self._valid_pairs[prior] and self._valid_pairs[prior + 1]:
                return (prior, following)
            return None
        return None

    def bound_for(self, arrival_utc_nanos: int) -> ArrivalClockBoundEvidenceV1 | None:
        """The bracketed bound for one arrival, or ``None`` (no knowledge time)."""
        bracket = self.bracket(arrival_utc_nanos)
        if bracket is None:
            return None
        used = self._samples[bracket[0]: bracket[1] + 1]
        return ArrivalClockBoundEvidenceV1(
            venue_minus_host_upper_bound_nanos=max(item.venue_minus_host_upper_nanos for item in used),
            prior_sample_hash=used[0].sample_hash,
            next_sample_hash=used[-1].sample_hash,
        )

    def evidence_hash(self) -> str:
        return sha256_json_v1(
            {
                "rule": CLOCK_BOUND_RULE_V1,
                "max_bracket_nanos": MAX_CLOCK_BRACKET_NANOS,
                "session_id": str(self.session_id),
                "samples": [item.payload() for item in self._samples],
                "barriers": [list(item) for item in self._barriers],
            }
        )


# ---------------------------------------------------------------------------
# Normalized observations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class T4ArrivalV1:
    """The clocks every normalized observation carries from its carrying record."""

    session_id: UUID
    record_sequence: int
    record_content_hash: str
    arrival_utc_nanos: int
    clock_bound_nanos: int
    clock_bound_evidence: str

    @property
    def market_knowledge_micros(self) -> int:
        return market_knowledge_micros_v1(self.arrival_utc_nanos, self.clock_bound_nanos)


@dataclass(frozen=True, slots=True)
class T4ReferencePriceV1:
    """One observed mark or index update, bound to the record that carried it."""

    component: str
    price: Decimal
    exchange_ts_millis: int
    message_type: str
    arrival: T4ArrivalV1

    @property
    def observation_reference(self) -> str:
        return f"t4-ref:{self.arrival.record_content_hash}:{self.component}"

    @property
    def event_micros(self) -> int:
        return _millis_to_micros(self.exchange_ts_millis)


@dataclass(frozen=True, slots=True)
class T4BasisV1:
    """A basis over the current mark and index, never claimed synchronized."""

    mark: T4ReferencePriceV1
    index: T4ReferencePriceV1
    basis: Decimal
    emitting_record_hash: str

    @property
    def event_micros(self) -> int:
        return max(self.mark.event_micros, self.index.event_micros)

    @property
    def market_knowledge_micros(self) -> int:
        return max(self.mark.arrival.market_knowledge_micros, self.index.arrival.market_knowledge_micros)


@dataclass(frozen=True, slots=True)
class T4TradeV1:
    trade_id: str
    trade_ts_millis: int
    message_ts_millis: int
    venue_seq: int
    side: str
    price: Decimal
    quantity: Decimal
    block_trade: bool | None
    rpi: bool | None
    entry_index: int
    arrival: T4ArrivalV1

    @property
    def ordering_key(self) -> tuple[int, int, int, int]:
        """Evidence-only order: venue time, venue seq, arrival sequence, message position."""
        return (self.trade_ts_millis, self.venue_seq, self.arrival.record_sequence, self.entry_index)

    @property
    def observation_reference(self) -> str:
        return f"t4-trade:{self.arrival.record_content_hash}:{self.entry_index}"


@dataclass(frozen=True, slots=True)
class T4MinuteBarV1:
    bar_open_micros: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    base_volume: Decimal
    quote_turnover: Decimal
    trade_count: int
    block_trade_count: int
    rpi_trade_count: int
    first_trade_reference: str
    last_trade_reference: str
    open_market_knowledge_micros: int
    complete_market_knowledge_micros: int
    open_is_sequence_ambiguous: bool
    close_is_sequence_ambiguous: bool
    closing_record_hash: str
    trade_manifest_hash: str

    @property
    def bar_close_micros(self) -> int:
        return self.bar_open_micros + _MICROS_PER_MINUTE


@dataclass(slots=True)
class T4NormalizationCountsV1:
    records: int = 0
    ticker_records: int = 0
    trade_records: int = 0
    ticker_snapshots: int = 0
    ticker_deltas: int = 0
    reference_prices: int = 0
    mark_updates: int = 0
    index_updates: int = 0
    basis_observations: int = 0
    ticker_records_before_state_complete: int = 0
    trades: int = 0
    bars: int = 0
    bars_open_ambiguous: int = 0
    bars_close_ambiguous: int = 0
    minutes_without_open_proof: int = 0
    minutes_open_at_segment_end: int = 0

    def payload(self) -> dict[str, int]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def _decimal_positive(raw: object, name: str) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, str | int):
        raise T4NormalizationError(f"{name}_malformed")
    text = str(raw).strip()
    if not text:
        raise T4NormalizationError(f"{name}_empty")
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise T4NormalizationError(f"{name}_malformed") from error
    if not value.is_finite() or value <= 0:
        raise T4NormalizationError(f"{name}_not_positive_finite")
    return value


def _optional_bool(raw: object, name: str) -> bool | None:
    if raw is None:
        return None
    if not isinstance(raw, bool):
        raise T4NormalizationError(f"{name}_malformed")
    return raw


def compute_basis_v1(mark: Decimal, index: Decimal) -> Decimal:
    """``(mark - index) / index`` in a fixed decimal context, then quantized half-even.

    A fixed context makes the result independent of whatever ambient context
    the caller runs under; 50 significant digits is far beyond the 1e-18 quantum.
    """
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return ((mark - index) / index).quantize(BASIS_QUANTUM_V1, rounding=ROUND_HALF_EVEN)


def exact_sum_v1(values: Iterable[Decimal], quantum: Decimal, name: str) -> Decimal:
    """A sum that must be exactly representable at ``quantum``: exact or refused, never rounded."""
    with localcontext() as context:
        context.prec = 60
        total = sum(values, Decimal(0))
        quantized = total.quantize(quantum)
    if quantized != total:
        raise T4NormalizationError(f"{name}_not_exact_at_its_quantum")
    return quantized


@dataclass(slots=True)
class T4SegmentOutputV1:
    reference_prices: list[T4ReferencePriceV1] = field(default_factory=list)
    basis: list[T4BasisV1] = field(default_factory=list)
    trades: list[T4TradeV1] = field(default_factory=list)
    bars: list[T4MinuteBarV1] = field(default_factory=list)


class T4SegmentNormalizerV1:
    """Record-at-a-time normalization of one segment. Shared by replay and (later) live.

    Feed records in arrival order with :meth:`feed`, each with the clock bound
    its arrival is admitted under; call :meth:`finish` at the segment end. The
    object holds only the segment's state, so a new segment is a new object --
    which is how the reset-at-every-boundary rule is enforced structurally.
    """

    def __init__(self, *, exchange_symbol: str, session_id: UUID) -> None:
        if not exchange_symbol.strip():
            raise T4NormalizationError("exchange_symbol_required")
        self._symbol = exchange_symbol
        self._session_id = session_id
        self._mark: T4ReferencePriceV1 | None = None
        self._index: T4ReferencePriceV1 | None = None
        self._last_ticker_cs: int | None = None
        self._last_ticker_ts: int | None = None
        self._last_trade_key: tuple[int, int] | None = None
        self._minute: int | None = None
        self._minute_trades: list[T4TradeV1] = []
        self._minute_open_proven = False
        self._last_sequence: int | None = None
        self._last_arrival: int | None = None
        self._seen_trade_ids: set[str] = set()
        self._finished = False
        self.counts = T4NormalizationCountsV1()
        self.output = T4SegmentOutputV1()

    # -- entry point ---------------------------------------------------------

    def feed(self, record: FirstPartyCaptureRecordV1, bound: ArrivalClockBoundEvidenceV1) -> None:
        if self._finished:
            raise T4NormalizationError("segment_already_finished")
        if record.session_id != self._session_id:
            raise T4NormalizationError("record_from_another_session")
        if self._last_sequence is not None and record.sequence <= self._last_sequence:
            raise T4NormalizationError("record_sequence_not_increasing")
        if self._last_arrival is not None and record.arrival_utc_nanos < self._last_arrival:
            raise T4NormalizationError("record_arrival_regressed")
        self._last_sequence = record.sequence
        self._last_arrival = record.arrival_utc_nanos
        arrival = T4ArrivalV1(
            session_id=record.session_id,
            record_sequence=record.sequence,
            record_content_hash=record.content_hash,
            arrival_utc_nanos=record.arrival_utc_nanos,
            clock_bound_nanos=bound.venue_minus_host_upper_bound_nanos,
            clock_bound_evidence=bound.evidence_reference,
        )
        payload = json.loads(record.payload_text)
        ts = payload.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, int) or ts != record.exchange_timestamp_millis:
            raise T4NormalizationError("record_exchange_timestamp_disagrees_with_payload")
        if _millis_to_micros(ts) > arrival.market_knowledge_micros:
            # The venue stamped this message after the latest instant our bound
            # says it can have arrived: the clock evidence is wrong somewhere.
            raise T4NormalizationError("clock_bound_places_knowledge_before_venue_event")
        self.counts.records += 1
        if record.channel == BybitPublicChannelV1.TICKERS.value:
            self._feed_ticker(payload, record.message_type, arrival)
        elif record.channel == BybitPublicChannelV1.PUBLIC_TRADE.value:
            self._feed_trades(payload, arrival)
        else:
            raise T4NormalizationError("record_channel_not_normalizable")

    # -- tickers -------------------------------------------------------------

    def _feed_ticker(self, payload: Mapping[str, Any], message_type: str | None, arrival: T4ArrivalV1) -> None:
        self.counts.ticker_records += 1
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("symbol") != self._symbol:
            raise T4NormalizationError("ticker_payload_malformed")
        cs = payload.get("cs")
        if cs is not None:
            if isinstance(cs, bool) or not isinstance(cs, int):
                raise T4NormalizationError("ticker_cross_sequence_malformed")
            if self._last_ticker_cs is not None and cs < self._last_ticker_cs:
                raise T4NormalizationError("ticker_cross_sequence_regressed")
            self._last_ticker_cs = cs
        ts = int(payload["ts"])
        if self._last_ticker_ts is not None and ts < self._last_ticker_ts:
            # Component and basis event times must move forward within a segment;
            # checked here so a seal is never issued over a regressing stream.
            raise T4NormalizationError("ticker_ts_regressed_within_segment")
        self._last_ticker_ts = ts
        observed: dict[str, T4ReferencePriceV1] = {}
        for component, name in _COMPONENT_FIELDS:
            if name not in data:
                continue
            observed[component] = T4ReferencePriceV1(
                component=component,
                price=_decimal_positive(data[name], f"ticker_{name}"),
                exchange_ts_millis=ts,
                message_type=str(message_type),
                arrival=arrival,
            )
        if message_type == BybitMessageTypeV1.SNAPSHOT.value:
            self.counts.ticker_snapshots += 1
            self._mark = observed.get(COMPONENT_MARK)
            self._index = observed.get(COMPONENT_INDEX)
        elif message_type == BybitMessageTypeV1.DELTA.value:
            self.counts.ticker_deltas += 1
            self._mark = observed.get(COMPONENT_MARK, self._mark)
            self._index = observed.get(COMPONENT_INDEX, self._index)
        else:
            raise T4NormalizationError("ticker_message_type_not_recognized")
        for item in observed.values():
            self.output.reference_prices.append(item)
            self.counts.reference_prices += 1
            if item.component == COMPONENT_MARK:
                self.counts.mark_updates += 1
            else:
                self.counts.index_updates += 1
        if not observed:
            return
        if self._mark is None or self._index is None:
            self.counts.ticker_records_before_state_complete += 1
            return
        mark, index = self._mark, self._index
        # Causality, re-asserted: both components were in hand at this record.
        if max(mark.arrival.arrival_utc_nanos, index.arrival.arrival_utc_nanos) > arrival.arrival_utc_nanos:
            raise T4NormalizationError("ticker_future_component_leak")
        self.output.basis.append(
            T4BasisV1(
                mark=mark,
                index=index,
                basis=compute_basis_v1(mark.price, index.price),
                emitting_record_hash=arrival.record_content_hash,
            )
        )
        self.counts.basis_observations += 1

    # -- trades and bars -----------------------------------------------------

    def _feed_trades(self, payload: Mapping[str, Any], arrival: T4ArrivalV1) -> None:
        self.counts.trade_records += 1
        message_ts = int(payload["ts"])
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise T4NormalizationError("trade_payload_malformed")
        for position, entry in enumerate(data):
            if not isinstance(entry, dict) or entry.get("s") != self._symbol:
                raise T4NormalizationError("trade_entry_malformed")
            trade_id = entry.get("i")
            if not isinstance(trade_id, str) or not trade_id.strip():
                raise T4NormalizationError("trade_id_missing")
            trade_ts = entry.get("T")
            if isinstance(trade_ts, bool) or not isinstance(trade_ts, int) or trade_ts <= 0:
                raise T4NormalizationError("trade_time_malformed")
            if trade_ts > message_ts:
                raise T4NormalizationError("trade_time_after_its_message")
            venue_seq = entry.get("seq")
            if isinstance(venue_seq, bool) or not isinstance(venue_seq, int):
                raise T4NormalizationError("trade_venue_seq_missing")
            side = entry.get("S")
            if side not in ("Buy", "Sell"):
                raise T4NormalizationError("trade_side_malformed")
            if trade_id in self._seen_trade_ids:
                raise T4NormalizationError("trade_id_repeated_within_segment")
            self._seen_trade_ids.add(trade_id)
            key = (trade_ts, venue_seq)
            if self._last_trade_key is not None and key < self._last_trade_key:
                # The whole bar rule rests on this order; it is checked on every
                # trade, and a violation is refused rather than re-sorted.
                raise T4NormalizationError("trade_order_regressed_within_segment")
            trade = T4TradeV1(
                trade_id=trade_id,
                trade_ts_millis=trade_ts,
                message_ts_millis=message_ts,
                venue_seq=venue_seq,
                side=side,
                price=_decimal_positive(entry.get("p"), "trade_price"),
                quantity=_decimal_positive(entry.get("v"), "trade_quantity"),
                block_trade=_optional_bool(entry.get("BT"), "trade_block_flag"),
                rpi=_optional_bool(entry.get("RPI"), "trade_rpi_flag"),
                entry_index=position,
                arrival=arrival,
            )
            minute = (_millis_to_micros(trade_ts) // _MICROS_PER_MINUTE) * _MICROS_PER_MINUTE
            if self._minute is not None and minute != self._minute:
                # A trade at or past the open minute's close proves, under the
                # checked order, that no further trade of that minute exists.
                self._close_minute(arrival)
            if self._minute is None:
                self._minute = minute
                # Proven from its start only if an earlier trade (necessarily
                # before this minute, by the checked order) was already seen.
                self._minute_open_proven = self._last_trade_key is not None
            self._minute_trades.append(trade)
            self._last_trade_key = key
            self.output.trades.append(trade)
            self.counts.trades += 1

    def _close_minute(self, closing: T4ArrivalV1) -> None:
        minute = self._minute
        if minute is None:
            return
        trades = self._minute_trades
        self._minute = None
        self._minute_trades = []
        if not self._minute_open_proven:
            self.counts.minutes_without_open_proof += 1
            return
        self._emit_bar(minute, trades, closing)

    def _emit_bar(self, minute: int, trades: Sequence[T4TradeV1], closing: T4ArrivalV1) -> None:
        ordered = sorted(trades, key=lambda trade: trade.ordering_key)
        first, last = ordered[0], ordered[-1]
        leading = [t for t in ordered if (t.trade_ts_millis, t.venue_seq) == (first.trade_ts_millis, first.venue_seq)]
        trailing = [t for t in ordered if (t.trade_ts_millis, t.venue_seq) == (last.trade_ts_millis, last.venue_seq)]
        open_ambiguous = len({t.price for t in leading}) > 1
        close_ambiguous = len({t.price for t in trailing}) > 1
        complete = max(
            minute + _MICROS_PER_MINUTE,
            max(t.arrival.market_knowledge_micros for t in ordered),
            closing.market_knowledge_micros,
        )
        manifest = sha256_json_v1(
            {
                "semantic_version": T4_NORMALIZATION_SEMANTIC_VERSION_V1,
                "bar_open_micros": minute,
                "trades": [t.observation_reference for t in ordered],
            }
        )
        self.output.bars.append(
            T4MinuteBarV1(
                bar_open_micros=minute,
                open_price=first.price,
                high_price=max(t.price for t in ordered),
                low_price=min(t.price for t in ordered),
                close_price=last.price,
                base_volume=exact_sum_v1((t.quantity for t in ordered), VOLUME_QUANTUM_V1, "bar_volume"),
                quote_turnover=exact_sum_v1(
                    (t.price * t.quantity for t in ordered), TURNOVER_QUANTUM_V1, "bar_turnover"
                ),
                trade_count=len(ordered),
                block_trade_count=sum(1 for t in ordered if t.block_trade),
                rpi_trade_count=sum(1 for t in ordered if t.rpi),
                first_trade_reference=first.observation_reference,
                last_trade_reference=last.observation_reference,
                open_market_knowledge_micros=first.arrival.market_knowledge_micros,
                complete_market_knowledge_micros=complete,
                open_is_sequence_ambiguous=open_ambiguous,
                close_is_sequence_ambiguous=close_ambiguous,
                closing_record_hash=closing.record_content_hash,
                trade_manifest_hash=manifest,
            )
        )
        self.counts.bars += 1
        self.counts.bars_open_ambiguous += int(open_ambiguous)
        self.counts.bars_close_ambiguous += int(close_ambiguous)

    def finish(self) -> T4SegmentOutputV1:
        """End the segment. Minutes without a closing proof produce nothing."""
        if self._finished:
            raise T4NormalizationError("segment_already_finished")
        self._finished = True
        self.counts.minutes_open_at_segment_end = 0 if self._minute is None else 1
        self._minute = None
        self._minute_trades = []
        return self.output


__all__ = [
    "BAR_COMPLETION_RULE_V1",
    "BASIS_FORMULA_V1",
    "BASIS_QUANTUM_V1",
    "CLOCK_BOUND_RULE_V1",
    "COMPONENT_INDEX",
    "COMPONENT_MARK",
    "T4_NORMALIZATION_SEMANTIC_VERSION_V1",
    "ArrivalClockBoundEvidenceV1",
    "ClockOffsetSampleEvidenceV1",
    "SessionClockEvidenceV1",
    "T4ArrivalV1",
    "T4BasisV1",
    "T4MinuteBarV1",
    "T4NormalizationCountsV1",
    "T4NormalizationError",
    "T4ReferencePriceV1",
    "T4SegmentNormalizerV1",
    "T4SegmentOutputV1",
    "T4TradeV1",
    "canonical_json_v1",
    "ceil_micros_v1",
    "compute_basis_v1",
    "market_knowledge_micros_v1",
    "micros_to_datetime_v1",
    "parse_clock_offset_sample_v1",
    "sha256_json_v1",
]
