"""Phase R8 core -- live T4 bars, frozen-candidate evaluation and explainable signals.

``RESEARCH_ONLY``. No order, no broker, no account. The live path is

    capture partitions (R1B universe recorder, still being written)
    -> incremental normalization (the unchanged R3A ``T4SegmentNormalizerV1``)
    -> completed 1-minute bars
    -> frozen Strategy Lab candidate, evaluated in Decimal (the OR-3 authority tier)
    -> an explainable, content-addressed signal when the target position changes.

Live bars (:class:`LiveBarFeedV1`)
----------------------------------
Records are read incrementally from each session's partitions: only complete,
hash-verified lines; sequences strictly increasing per session. They feed the
same R3A normalizer the sealed path uses, so the bar rule (a minute is complete
only when a later trade proves it, trade order checked on every trade; the
first minute a fresh normalizer sees is never proven) is the sealed rule. Live
arrivals cannot be bracketed by a *later* clock sample yet, so each record
carries a provisional bound from the latest sample already received in its
session (any day partition; ``LIVE_PROVISIONAL``); bar prices do not depend on
the bound, and the sealed replay later supplies the bracketed knowledge times.

Continuity, as in the sealed path's coverage windows: the session's normalizer
is discarded -- its open minute is dropped, never emitted -- and a new strategy
segment starts whenever

* a recorded interruption (``CONNECTION_LOST``, ``RECONNECT_STARTED``,
  ``CLOCK_DISCONTINUITY``, ``MESSAGE_REJECTED``, ``RECORDER_FAILED``) lies
  between two of the session's records;
* the arrival gap between consecutive records exceeds
  :data:`MAX_LIVE_CONTINUITY_GAP_NANOS` (a host sleep, an outage);
* a record has no clock sample before it, or the normalizer refuses one.

So no emitted bar misses a trade and no minute is closed across a hole. Records
are read before the lifecycle, so an interruption that precedes a read record
is always seen. One engineering tolerance remains, and it is not an economic
parameter: a *new recorder session* (the hourly segment rotation) that starts
within the tolerance continues the strategy segment. Its first minute is
unproven and the previous session's open minute is dropped, so the boundary
minutes are absent from the window (never filled); every signal records how
many such session boundaries its window spans.

Signals (:class:`LiveStrategyRunnerV1`)
---------------------------------------
Each watched candidate is a frozen Strategy Lab trial whose authority is
explicit: ``RESEARCH_WATCH`` (Decimal-authoritative frozen candidate with no
recorded R6 state, not validated) or ``INCUBATING`` (passed a preregistered
holdout; not validated). A candidate R6 rejected is never watched.

The holdout is protected by :class:`LiveHoldoutGateV1` (required): no candidate
is evaluated on a bar that opens at or after its research cycle's holdout start
unless that holdout was opened and the bar opens at or after its end. While the
holdout end is undecided (OR-7) every forward bar of the current cycle could
fall inside it, so nothing is evaluated on it -- live data never previews the
holdout, and incubation evidence is strictly after it.

On every admitted completed bar the candidate's Decimal targets are recomputed
over the current segment; a change of target emits a :class:`LiveSignalV1`
carrying the candidate and evidence identity, the decision instant, the numeric
and cost policies, an exact explanation and the authority label. The decision
instant is on the venue-knowledge clock: never earlier than the bar's complete
market-knowledge time, nor than the host's reading plus the latest clock bound.
Bars completed before the runner started only warm up its history: a replayed
past decision is never back-dated as a live one. A signal is a proposal: its
fill (R10) is the next strictly later bar open. Nothing here can label a signal
validated.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .first_party_capture_archive_v1 import (
    COMPACTED_RECORDS_FILE_NAME,
    RECORDS_FILE_NAME,
    SESSION_FILE_NAME,
    CaptureLifecycleKindV1,
    FirstPartyCaptureRecordV1,
    find_partitions_v1,
    read_lifecycle_v1,
    session_source_id_v1,
)
from .first_party_capture_authority_v1 import FirstPartyCaptureContractV1
from .first_party_t4_normalization_v1 import (
    ArrivalClockBoundEvidenceV1,
    ClockOffsetSampleEvidenceV1,
    T4MinuteBarV1,
    T4NormalizationError,
    T4SegmentNormalizerV1,
    parse_clock_offset_sample_v1,
)
from .persistence import PostgresDatabase
from .strategy_lab_study_v1 import StudySpecV1, identity_hash_v1
from .strategy_sdk_v1 import FAMILIES_V1, BarsV1

LIVE_SIGNAL_SCHEMA_VERSION_V1: Final = "live-strategy-signal-v1"
LIVE_PROVISIONAL: Final = "LIVE_PROVISIONAL_NO_LATER_SAMPLE_YET"
#: Engineering continuity tolerance between consecutive captured records.
MAX_LIVE_CONTINUITY_GAP_NANOS: Final = 60_000_000_000

AUTHORITY_RESEARCH_WATCH: Final = "RESEARCH_WATCH"
AUTHORITY_INCUBATING: Final = "INCUBATING"
_AUTHORITIES: Final = (AUTHORITY_RESEARCH_WATCH, AUTHORITY_INCUBATING)

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.live_signals_v1")
_GATE_ISSUER: Final = object()
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class LiveSignalsError(ValueError):
    """Raised when live evidence or a watched candidate cannot be used honestly."""


# ---------------------------------------------------------------------------
# Live bars
# ---------------------------------------------------------------------------


#: Lifecycle events after which a session's records are no longer one continuous observation.
_INTERRUPTIONS: Final = frozenset({
    CaptureLifecycleKindV1.CONNECTION_LOST.value, CaptureLifecycleKindV1.RECONNECT_STARTED.value,
    CaptureLifecycleKindV1.CLOCK_DISCONTINUITY.value, CaptureLifecycleKindV1.MESSAGE_REJECTED.value,
    CaptureLifecycleKindV1.RECORDER_FAILED.value,
})


@dataclass(frozen=True, slots=True)
class LiveBarV1:
    symbol: str
    segment: int
    session_id: UUID
    bar: T4MinuteBarV1
    completed_by_arrival_nanos: int
    #: ``venue_minus_host_upper_nanos`` of the sample bounding the completing record.
    clock_bound_nanos: int

    def row(self) -> tuple[Any, ...]:
        """The bar in the research-bar row shape the SDK reads."""
        opened = _EPOCH + timedelta(microseconds=self.bar.bar_open_micros)
        return (opened, opened + timedelta(minutes=1), None, self.bar.open_price, self.bar.high_price,
                self.bar.low_price, self.bar.close_price)


@dataclass
class _Session:
    session_id: UUID
    normalizer: T4SegmentNormalizerV1
    last_sequence: int = -1
    last_arrival: int | None = None
    emitted: int = 0
    offsets: dict[str, int] = field(default_factory=dict)
    consumed_compacted: set[str] = field(default_factory=set)
    samples: dict[str, ClockOffsetSampleEvidenceV1] = field(default_factory=dict)
    interruptions: set[int] = field(default_factory=set)


class LiveBarFeedV1:
    """Incremental completed bars for one capture source. Restartable: state is re-derivable."""

    def __init__(self, capture_root: Path, contract: FirstPartyCaptureContractV1) -> None:
        self._root = capture_root
        self._contract = contract
        self._sessions: dict[UUID, _Session] = {}
        self._segment = 0
        self._last_arrival: int | None = None
        self.refusals: list[str] = []
        self.skipped_without_clock_sample = 0
        self.continuity_breaks = 0

    def _partitions(self) -> list[tuple[tuple[int, str, str], Path]]:
        """This source's partitions in chronological order (earliest lifecycle event, then day)."""
        wanted = str(self._contract.source_id)
        out = []
        for directory in find_partitions_v1(self._root):
            if session_source_id_v1(directory) != wanted:
                continue
            session = json.loads((directory / SESSION_FILE_NAME).read_text(encoding="utf-8"))
            events = read_lifecycle_v1(directory)
            started = min((event.arrival_utc_nanos for event in events), default=2**63)
            out.append(((started, str(session["utc_day"]), str(session["session_id"])), directory))
        return sorted(out)

    @staticmethod
    def _read_lifecycle(state: _Session, directory: Path, resolution: int) -> None:
        """Accumulate the session's clock samples and interruptions (every day partition of it)."""
        for event in read_lifecycle_v1(directory):
            if event.kind == CaptureLifecycleKindV1.CLOCK_OFFSET_SAMPLE.value:
                sample = parse_clock_offset_sample_v1(event, session_resolution_nanos=resolution)
                state.samples[sample.sample_hash] = sample
            elif event.kind in _INTERRUPTIONS:
                state.interruptions.add(event.arrival_utc_nanos)

    def _restart(self, state: _Session) -> None:
        """Discard the session's normalizer (its open minute is dropped) and start a new segment."""
        state.normalizer = T4SegmentNormalizerV1(exchange_symbol=self._contract.exchange_symbol,
                                                 session_id=state.session_id)
        state.emitted = 0
        self._segment += 1
        self.continuity_breaks += 1

    def _new_lines(self, state: _Session, directory: Path) -> Iterator[str]:
        key = str(directory)
        raw = directory / RECORDS_FILE_NAME
        if raw.exists():
            offset = state.offsets.get(key, 0)
            with raw.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
            complete = chunk[: chunk.rfind(b"\n") + 1]  # never a torn trailing line
            state.offsets[key] = offset + len(complete)
            yield from complete.decode("utf-8").splitlines()
            return
        compacted = directory / COMPACTED_RECORDS_FILE_NAME
        if compacted.exists() and key not in state.consumed_compacted:
            state.consumed_compacted.add(key)
            with gzip.open(compacted, "rt", encoding="utf-8") as handle:
                yield from (line.rstrip("\n") for line in handle)

    def poll(self) -> list[LiveBarV1]:
        """Every bar completed since the previous poll, in completion order."""
        out: list[LiveBarV1] = []
        for _, directory in self._partitions():
            session_meta = json.loads((directory / SESSION_FILE_NAME).read_text(encoding="utf-8"))
            session_id = UUID(str(session_meta["session_id"]))
            resolution = int(session_meta["clock_resolution_nanos"])
            state = self._sessions.get(session_id)
            if state is None:
                state = _Session(session_id, T4SegmentNormalizerV1(
                    exchange_symbol=self._contract.exchange_symbol, session_id=session_id))
                self._sessions[session_id] = state
            # Records first, then the lifecycle: an interruption written before a record we
            # read is then always known when that record is processed.
            lines = list(self._new_lines(state, directory))
            self._read_lifecycle(state, directory, resolution)
            samples = sorted(state.samples.values(), key=lambda sample: sample.host_receive_utc_nanos)
            for line in lines:
                if not line.strip():
                    continue
                record = FirstPartyCaptureRecordV1.from_json_line(line)
                if record.sequence <= state.last_sequence:
                    continue
                state.last_sequence = record.sequence
                arrival = record.arrival_utc_nanos
                interrupted = state.last_arrival is not None and any(
                    state.last_arrival < instant <= arrival for instant in state.interruptions)
                gapped = self._last_arrival is not None and arrival - self._last_arrival > MAX_LIVE_CONTINUITY_GAP_NANOS
                self._last_arrival = state.last_arrival = arrival
                prior = [s for s in samples if s.host_receive_utc_nanos <= arrival]
                if not prior:
                    self.skipped_without_clock_sample += 1
                    self._restart(state)  # a skipped record is a hole: nothing closes across it
                    continue
                if interrupted or gapped:
                    self._restart(state)
                bound = ArrivalClockBoundEvidenceV1(prior[-1].venue_minus_host_upper_nanos,
                                                    prior[-1].sample_hash, LIVE_PROVISIONAL)
                try:
                    state.normalizer.feed(record, bound)
                except T4NormalizationError as error:
                    # Refused evidence breaks the segment; the next record starts afresh.
                    self.refusals.append(f"{session_id}:{record.sequence}:{error}")
                    self._restart(state)
                    continue
                bars = state.normalizer.output.bars
                for bar in bars[state.emitted:]:
                    out.append(LiveBarV1(self._contract.exchange_symbol, self._segment, session_id, bar,
                                         arrival, prior[-1].venue_minus_host_upper_nanos))
                state.emitted = len(bars)
        return out


# ---------------------------------------------------------------------------
# Watched candidates and signals
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WatchedCandidateV1:
    study: StudySpecV1
    trial_id: str
    symbol: str
    authority: str
    authority_evidence_hash: str

    def __post_init__(self) -> None:
        if self.authority not in _AUTHORITIES:
            raise LiveSignalsError("watched_candidate_authority_must_be_research_watch_or_incubating")
        family = FAMILIES_V1.get(self.study.strategy.family)
        if family is None or family.spec() != self.study.strategy:
            raise LiveSignalsError("watched_candidate_strategy_is_not_this_sdk_version")
        if self.trial_id not in {str(t.trial_id) for t in self.study.trials()}:
            raise LiveSignalsError("watched_candidate_trial_not_in_its_study")

    @property
    def parameters(self) -> dict[str, Any]:
        trial = next(t for t in self.study.trials() if str(t.trial_id) == self.trial_id)
        return self.study.parameter_space.typed_point(trial.parameters)


def watched_from_rerun_v1(study: StudySpecV1, rerun: Any, *, states: Sequence[Mapping[str, Any]],
                          symbol: str) -> list[WatchedCandidateV1]:
    """RESEARCH_WATCH candidates: the Decimal-authoritative selection of an ESTABLISHED rerun.

    ``states`` are the study's recorded R6 candidate states. A selected trial
    with any recorded state is not a RESEARCH_WATCH candidate: a rejected one
    is never watched, an incubating one is watched only as INCUBATING.
    """
    from .strategy_lab_authority_rerun_v1 import SELECTION_ESTABLISHED

    if rerun.identity["study_content_hash"] != study.content_hash:
        raise LiveSignalsError("rerun_is_not_from_this_study")
    selection = rerun.identity["authoritative_selection"]
    if selection["status"] != SELECTION_ESTABLISHED:
        raise LiveSignalsError("research_watch_requires_an_established_decimal_selection")
    stated = {str(event["trial_id"]) for event in states}
    return [WatchedCandidateV1(study, str(item["trial_id"]), symbol, AUTHORITY_RESEARCH_WATCH, rerun.rerun_hash)
            for item in selection["selected"] if str(item["trial_id"]) not in stated]


def watched_from_states_v1(study: StudySpecV1, states: Sequence[Mapping[str, Any]], *,
                           symbol: str) -> list[WatchedCandidateV1]:
    """INCUBATING candidates: only those whose recorded lifecycle (R6) is INCUBATING."""
    from .strategy_lab_validation_v1 import CandidateStateV1, candidate_lifecycle_v1

    latest = candidate_lifecycle_v1(states)
    evidence = {str(event["trial_id"]): str(event["evidence_hash"]) for event in states}
    return [WatchedCandidateV1(study, trial, symbol, AUTHORITY_INCUBATING, evidence[trial])
            for trial, state in sorted(latest.items()) if state == CandidateStateV1.INCUBATING.value]


@dataclass(frozen=True, slots=True)
class LiveHoldoutGateV1:
    """Which live bars a candidate may be evaluated on, given its research cycle's holdout."""

    cycle_id: str
    holdout_start: datetime
    holdout_end_exclusive: datetime | None
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _GATE_ISSUER:
            raise LiveSignalsError("holdout_gate_is_built_only_from_an_issued_cycle")

    @classmethod
    def for_cycle(cls, cycle: Any, opening: Any | None) -> LiveHoldoutGateV1:
        """From a registry-issued cycle and its registry-issued opening (``None`` while unopened)."""
        from .strategy_lab_validation_v1 import cycle_is_issued_v1

        if not cycle_is_issued_v1(cycle):
            raise LiveSignalsError("holdout_gate_requires_an_issued_cycle")
        if opening is None:
            return cls(cycle.cycle_id, cycle.holdout_start, None, _GATE_ISSUER)
        if not opening.issued or opening.cycle_id != cycle.cycle_id:
            raise LiveSignalsError("holdout_gate_requires_this_cycles_issued_opening")
        return cls(cycle.cycle_id, cycle.holdout_start, opening.holdout_end_exclusive, _GATE_ISSUER)

    def admits(self, bar_open_micros: int) -> bool:
        opened = _EPOCH + timedelta(microseconds=bar_open_micros)
        if opened < self.holdout_start:
            return True
        return self.holdout_end_exclusive is not None and opened >= self.holdout_end_exclusive

    def payload(self) -> dict[str, Any]:
        end = self.holdout_end_exclusive
        return {"cycle_id": self.cycle_id, "holdout_start": self.holdout_start.astimezone(UTC).isoformat(),
                "holdout_end_exclusive": None if end is None else end.astimezone(UTC).isoformat()}


@dataclass(frozen=True, slots=True)
class LiveSignalV1:
    identity: Mapping[str, Any]
    signal_id: UUID
    decided_at: datetime


def _micros_ceil(nanos: int) -> int:
    return -(-nanos // 1000)


class LiveStrategyRunnerV1:
    """Evaluates watched candidates on every admitted completed bar; emits a signal on a target change."""

    def __init__(self, candidates: Sequence[WatchedCandidateV1], *, holdout_gate: LiveHoldoutGateV1,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._candidates = list(candidates)
        self._gate = holdout_gate
        self._clock = clock
        started = clock()
        self._started_nanos = (started - _EPOCH) // timedelta(microseconds=1) * 1000
        self._history: dict[str, list[LiveBarV1]] = {}
        self._last_target: dict[tuple[str, str, str], int] = {}
        self.held_back_by_holdout = 0
        self.warmup_bars = 0

    def on_bars(self, bars: Sequence[LiveBarV1]) -> list[LiveSignalV1]:
        signals: list[LiveSignalV1] = []
        for bar in bars:
            history = self._history.setdefault(bar.symbol, [])
            if history and history[-1].segment != bar.segment:
                history.clear()  # a broken segment never feeds a rolling window
            history.append(bar)
            if not self._gate.admits(bar.bar.bar_open_micros):
                self.held_back_by_holdout += 1
                continue
            warmup = bar.completed_by_arrival_nanos < self._started_nanos
            self.warmup_bars += int(warmup)
            window = BarsV1.from_rows([item.row() for item in history],
                                      segment_keys=[item.segment for item in history])
            for candidate in self._candidates:
                if candidate.symbol != bar.symbol:
                    continue
                family = FAMILIES_V1[candidate.study.strategy.family]
                params = candidate.parameters
                targets = family.targets_decimal(window, params)
                key = (bar.symbol, str(candidate.study.study_id), candidate.trial_id)
                previous = self._last_target.get(key, targets[-2] if len(targets) > 1 else 0)
                current = targets[-1]
                self._last_target[key] = current
                if current == previous or warmup:
                    continue
                now_micros = (self._clock() - _EPOCH) // timedelta(microseconds=1)
                decided_micros = max(bar.bar.complete_market_knowledge_micros,
                                     now_micros + _micros_ceil(bar.clock_bound_nanos))
                signals.append(_signal(candidate, bar, previous, current, family.explain_decimal(window, params),
                                       _EPOCH + timedelta(microseconds=decided_micros), history, self._gate))
        return signals


def _signal(candidate: WatchedCandidateV1, bar: LiveBarV1, previous: int, current: int,
            explanation: Mapping[str, str], decided_at: datetime, history: Sequence[LiveBarV1],
            gate: LiveHoldoutGateV1) -> LiveSignalV1:
    study = candidate.study
    boundaries = sum(1 for before, after in pairwise(history)
                     if before.session_id != after.session_id)
    identity = {
        "schema_version": LIVE_SIGNAL_SCHEMA_VERSION_V1,
        "candidate": {"study_id": str(study.study_id), "study_content_hash": study.content_hash,
                      "trial_id": candidate.trial_id, "family": study.strategy.family,
                      "family_version": study.strategy.version,
                      "implementation_sha256": study.strategy.implementation_sha256},
        "authority": candidate.authority,
        "authority_evidence_hash": candidate.authority_evidence_hash,
        "claim": "NOT_VALIDATED_" + candidate.authority,
        "symbol": candidate.symbol,
        "evidence": {"tier": "T4_FIRST_PARTY_CAPTURE_FORWARD_UNSEALED", "session_id": str(bar.session_id),
                     "bar_open_micros": bar.bar.bar_open_micros,
                     "complete_market_knowledge_micros": bar.bar.complete_market_knowledge_micros,
                     "closing_record_hash": bar.bar.closing_record_hash,
                     "trade_manifest_hash": bar.bar.trade_manifest_hash,
                     "window_first_bar_open_micros": history[0].bar.bar_open_micros,
                     "window_bars": len(history), "window_session_boundaries": boundaries,
                     "clock_bound": LIVE_PROVISIONAL},
        "holdout": gate.payload(),
        "target_from": previous,
        "target_to": current,
        "execution": "next strictly later bar open (paper, R10)",
        "numeric_policy_slot": study.numeric_policy_slot,
        "cost_policy_slot": study.cost_policy_slot,
        "explanation": dict(explanation),
    }
    content_hash = identity_hash_v1(identity)
    return LiveSignalV1(identity, uuid5(_NAMESPACE, f"live-signal:{content_hash}"), decided_at)


# ---------------------------------------------------------------------------
# Persistence (migration 20261008_0058)
# ---------------------------------------------------------------------------


class PostgresLiveSignalStoreV1:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def record(self, signal: LiveSignalV1) -> bool:
        identity = signal.identity
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO live_strategy_signals (signal_id, content_hash, study_id, trial_id, symbol, authority, "
                "target_from, target_to, bar_open_at, decided_at, identity, recorded_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (signal_id) DO NOTHING RETURNING signal_id",
                (signal.signal_id, identity_hash_v1(dict(identity)), UUID(identity["candidate"]["study_id"]),
                 UUID(identity["candidate"]["trial_id"]), identity["symbol"], identity["authority"],
                 identity["target_from"], identity["target_to"],
                 _EPOCH + timedelta(microseconds=int(identity["evidence"]["bar_open_micros"])),
                 signal.decided_at, json.dumps(dict(identity), sort_keys=True), datetime.now(UTC)),
            )
            return cursor.fetchone() is not None

    def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT signal_id, symbol, authority, target_from, target_to, bar_open_at, decided_at, "
                           "identity FROM live_strategy_signals ORDER BY decided_at DESC, signal_id LIMIT %s",
                           (limit,))
            rows = cursor.fetchall()
        return [{"signal_id": str(r[0]), "symbol": r[1], "authority": r[2], "target_from": r[3], "target_to": r[4],
                 "bar_open_at": r[5], "decided_at": r[6],
                 "identity": r[7] if isinstance(r[7], dict) else json.loads(r[7])} for r in rows]


__all__ = [
    "AUTHORITY_INCUBATING",
    "AUTHORITY_RESEARCH_WATCH",
    "LIVE_PROVISIONAL",
    "MAX_LIVE_CONTINUITY_GAP_NANOS",
    "LiveBarFeedV1",
    "LiveBarV1",
    "LiveHoldoutGateV1",
    "LiveSignalV1",
    "LiveSignalsError",
    "LiveStrategyRunnerV1",
    "PostgresLiveSignalStoreV1",
    "WatchedCandidateV1",
    "watched_from_rerun_v1",
    "watched_from_states_v1",
]
