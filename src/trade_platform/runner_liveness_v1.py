"""Runner liveness evidence -- was a research/paper runner really processing forward data?

``RESEARCH_ONLY`` and import-light (stdlib plus the canonical identity hash): no
order, no account, no numerical stack. The paper incubation clock
(:mod:`trade_platform.paper_incubation_report_v1`) is a lower bound built from
fills alone, so a quiet but running runner earns no time. This module records
what the runner did between signals and answers, deterministically, which
minutes of a claimed window it *provably* processed for one candidate.

Progress records (:class:`RunnerProgressRecorderV1`)
----------------------------------------------------
A runner process gets a fresh ``runner_session_id``; a restart is a new
session, never a continuation. Each session writes an append-only, hash-chained
sequence of content-addressed records (idempotency key ``session:sequence``):

* ``STARTED`` (sequence 0) carries the *binding*: runner label, symbol, capture
  source, the watched candidates (study, study content hash, trial, authority,
  authority evidence) and the holdout gate. Every later record names the
  binding's hash and the previous record's content hash.
* ``HEARTBEAT`` -- the process was alive at that host instant. It claims
  nothing about market data (``PROCESS_ALIVE_NO_MARKET_DATA_CLAIM``).
* ``BARS`` -- written after the runner evaluated live completed bars and
  persisted any signals: per bar the capture session, the feed segment, the
  bar's stable evidence identity (``closing_record_hash``,
  ``trade_manifest_hash``), its completing arrival and whether the holdout gate
  admitted it for evaluation. Warm-up bars (completed before the session
  started, i.e. replayed history) are only counted, never listed.

Each record carries the host wall clock and the host monotonic clock, so a
reconstruction can see a clock that ran backwards or jumped.

Reconstruction (:func:`derive_runner_liveness_v1`)
--------------------------------------------------
For one candidate (study, trial, symbol) and a claimed window, every minute is
either ``PROVEN`` or ``UNPROVEN`` with one reason. A minute is proven only when
all of the following hold; anything else fails closed:

1. **Runner processing.** It lies inside a *processing run*: consecutive live
   bars of one runner session, one feed segment (the feed restarts its segment
   on any capture hole, interruption or refused record) and one unbroken
   progress chain, each admitted by the gate and processed within
   ``max_processing_lag_nanos`` of its completing arrival. The run proves
   ``[first bar open, last bar open + 1 min)``: inside one feed segment every
   record was processed, so a minute without trades between two bars is a
   legitimate quiet minute, not a hole. A chain breaks on a sequence gap, a
   progress gap above ``max_progress_gap_nanos``, a wall or monotonic clock
   that ran backwards, or wall and monotonic deltas that disagree by more than
   ``max_clock_divergence_nanos``. A break, a restart, a late, unadmitted or
   replayed bar ends the run; nothing is ever bridged across it.
2. **Capture.** Its UTC hour is judged ``COMPLETE`` by the manifest-proven
   hourly acceptance, proven by the capture session that produced every
   processed bar of the run inside that hour.
3. **Sealed evidence.** A sealed, raw-replayed dataset of that capture session
   agrees: a processed bar's minute must be a sealed bar with the same
   ``trade_manifest_hash`` and ``closing_record_hash``; a quiet minute must lie
   inside a sealed dataset's bar range and have no sealed bar (else the runner
   missed one). A disagreement refuses the whole hour.

Heartbeats only decide which reason an unproven minute gets
(``ALIVE_WITHOUT_DATA_PROOF`` instead of ``NO_RUNNER_PROGRESS``); they never
prove a minute. Nothing before a session's start can be proven (replay is
warm-up), so historical coverage is never claimed. A session whose records do
not re-derive, skip their start, repeat a sequence, break the hash chain or
change binding is refused as a whole and listed. The three bounds are
engineering tolerances with no default: the caller states them and the result
records them. The result is a lower bound and never a validation: the first
minute of each hourly capture segment has no bar in either the live feed or the
seal (no earlier trade in that segment), so it stays ``NOT_SEALED`` and a long
window loses about one minute per hour.

Consumers (incubation, readiness) call
:meth:`PostgresRunnerLivenessStoreV1.for_candidate` and pass the rows, the
capture source's hourly acceptance and the sealed datasets
(:meth:`SealedBarsEvidenceV1.from_forward_dataset`) to
:func:`derive_runner_liveness_v1`; :attr:`RunnerLivenessV1.proven_intervals`
is the answer.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any, Final, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .first_party_capture_hourly_acceptance_v1 import HOUR_NANOS, HourlyAcceptanceV1
from .strategy_lab_study_v1 import identity_hash_v1

RUNNER_PROGRESS_SCHEMA_VERSION_V1: Final = "runner-liveness-progress-v1"
RUNNER_LIVENESS_SCHEMA_VERSION_V1: Final = "runner-liveness-v1"
KIND_STARTED: Final = "STARTED"
KIND_HEARTBEAT: Final = "HEARTBEAT"
KIND_BARS: Final = "BARS"
KINDS: Final = (KIND_STARTED, KIND_HEARTBEAT, KIND_BARS)
CLAIM_ALIVE_ONLY: Final = "PROCESS_ALIVE_NO_MARKET_DATA_CLAIM"
CLAIM_BARS_EVALUATED: Final = "LIVE_BARS_EVALUATED_NOT_VALIDATED"
LIVENESS_CLAIM_V1: Final = "PROVEN_FORWARD_PROCESSING_LOWER_BOUND_NOT_VALIDATED"
STATUS_PROVEN: Final = "PROVEN"
STATUS_UNPROVEN: Final = "UNPROVEN"
#: Engineering heartbeat cadence of the operator scripts (not an economic parameter).
DEFAULT_HEARTBEAT_INTERVAL_NANOS: Final = 60_000_000_000

_MINUTE_MICROS: Final = 60_000_000
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.runner_liveness_v1")
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class RunnerLivenessError(ValueError):
    """Raised when progress evidence or a liveness question cannot be used honestly."""


class UnprovenReasonV1(StrEnum):
    """Why one minute of a claimed window is not proven forward processing."""

    NO_RUNNER_PROGRESS = "NO_RUNNER_PROGRESS"
    ALIVE_WITHOUT_DATA_PROOF = "ALIVE_WITHOUT_DATA_PROOF"
    CANDIDATE_NOT_EVALUATED = "CANDIDATE_NOT_EVALUATED_HOLDOUT_GATE"
    BAR_PROCESSED_LATE = "BAR_PROCESSED_LATE"
    BAR_COMPLETED_AFTER_PROCESSING = "BAR_COMPLETED_AFTER_PROCESSING"
    RUNNER_EVIDENCE_CONFLICT = "RUNNER_EVIDENCE_CONFLICT"
    CAPTURE_ACCEPTANCE_DOES_NOT_COVER_SYMBOL = "CAPTURE_ACCEPTANCE_DOES_NOT_COVER_SYMBOL"
    CAPTURE_HOUR_NOT_JUDGED = "CAPTURE_HOUR_NOT_JUDGED"
    CAPTURE_HOUR_SESSION_MISMATCH = "CAPTURE_HOUR_SESSION_MISMATCH"
    NOT_SEALED = "NOT_SEALED"
    BAR_NOT_SEALED = "BAR_NOT_SEALED"
    SEALED_BAR_NOT_PROCESSED = "SEALED_BAR_NOT_PROCESSED"
    SEAL_CONFLICT = "SEAL_CONFLICT"


#: Prefix of the reason for a judged hour that is not COMPLETE, followed by its verdict.
CAPTURE_HOUR_PREFIX: Final = "CAPTURE_HOUR_"


class DiscontinuityV1(StrEnum):
    """Why a runner session's progress chain or processing run broke."""

    SEQUENCE_GAP = "SEQUENCE_GAP"
    PROGRESS_GAP = "PROGRESS_GAP"
    HOST_CLOCK_BACKWARDS = "HOST_CLOCK_BACKWARDS"
    MONOTONIC_CLOCK_BACKWARDS = "MONOTONIC_CLOCK_BACKWARDS"
    HOST_CLOCK_JUMP = "HOST_CLOCK_JUMP"
    BAR_COMPLETED_AFTER_PROCESSING = "BAR_COMPLETED_AFTER_PROCESSING"
    FEED_SEGMENT_BREAK = "FEED_SEGMENT_BREAK"


class SessionRefusalV1(StrEnum):
    """Why a whole runner session's records are excluded from the reconstruction."""

    MALFORMED_RECORD = "MALFORMED_RECORD"
    CONTENT_HASH_DOES_NOT_REDERIVE = "CONTENT_HASH_DOES_NOT_REDERIVE"
    DUPLICATE_SEQUENCE = "DUPLICATE_SEQUENCE"
    RUNNER_SESSION_WITHOUT_START = "RUNNER_SESSION_WITHOUT_START"
    HASH_CHAIN_BROKEN = "HASH_CHAIN_BROKEN"
    SESSION_BINDING_CHANGED = "SESSION_BINDING_CHANGED"


def _micros(value: datetime) -> int:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RunnerLivenessError("instant_must_be_timezone_aware")
    return (value - _EPOCH) // timedelta(microseconds=1)


# ---------------------------------------------------------------------------
# Progress records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunnerProgressV1:
    """One immutable, content-addressed progress record of one runner session."""

    identity: Mapping[str, Any]
    content_hash: str

    @classmethod
    def build(cls, identity: Mapping[str, Any]) -> RunnerProgressV1:
        return cls(identity, identity_hash_v1(dict(identity)))

    @property
    def progress_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"runner-progress:{self.content_hash}")

    @property
    def idempotency_key(self) -> str:
        return f"runner-liveness:{self.identity['runner_session_id']}:{self.identity['sequence']}"


class RunnerProgressStoreV1(Protocol):
    def record(self, progress: RunnerProgressV1) -> bool: ...


def candidate_payload_v1(candidate: Any) -> dict[str, str]:
    """The binding entry of one watched candidate (a ``WatchedCandidateV1``, duck-typed)."""
    return {"study_id": str(candidate.study.study_id), "study_content_hash": str(candidate.study.content_hash),
            "trial_id": str(candidate.trial_id), "authority": str(candidate.authority),
            "authority_evidence_hash": str(candidate.authority_evidence_hash)}


class RunnerProgressRecorderV1:
    """Writes a runner session's progress records. Call :meth:`start`, then :meth:`on_poll` per poll.

    ``on_poll`` must be called after the runner evaluated the poll's bars and its
    signals were persisted: a ``BARS`` record asserts exactly that. A store
    failure propagates, so a runner that cannot record progress stops.
    """

    def __init__(self, store: RunnerProgressStoreV1, *, runner: str, symbol: str, capture_source_id: UUID,
                 candidates: Sequence[Any], holdout_gate: Any,
                 heartbeat_interval_nanos: int = DEFAULT_HEARTBEAT_INTERVAL_NANOS,
                 runner_session_id: UUID | None = None,
                 wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 monotonic_clock: Callable[[], int] = time.monotonic_ns) -> None:
        if not candidates:
            raise RunnerLivenessError("runner_progress_requires_watched_candidates")
        if any(str(candidate.symbol) != symbol for candidate in candidates):
            raise RunnerLivenessError("watched_candidate_symbol_is_not_the_runner_symbol")
        if heartbeat_interval_nanos <= 0:
            raise RunnerLivenessError("heartbeat_interval_must_be_positive")
        self._store = store
        self._symbol = symbol
        self._gate = holdout_gate
        self._heartbeat = heartbeat_interval_nanos
        self._wall = wall_clock
        self._monotonic = monotonic_clock
        self.runner_session_id = runner_session_id or uuid4()
        self.binding = {
            "runner": runner,
            "symbol": symbol,
            "capture_source_id": str(capture_source_id),
            "candidates": sorted((candidate_payload_v1(c) for c in candidates),
                                 key=lambda item: (item["study_id"], item["trial_id"])),
            "holdout_gate": dict(holdout_gate.payload()),
        }
        self._binding_hash = identity_hash_v1(self.binding)
        self._sequence = -1
        self._previous: str | None = None
        self._started_nanos: int | None = None
        self._last_write_monotonic = 0
        self._warmup = 0

    def _write(self, kind: str, *, bars: Sequence[Mapping[str, Any]] = (), feed: Any = None) -> RunnerProgressV1:
        wall, monotonic = _micros(self._wall()), int(self._monotonic())
        identity = {
            "schema_version": RUNNER_PROGRESS_SCHEMA_VERSION_V1,
            "runner_session_id": str(self.runner_session_id),
            "sequence": self._sequence + 1,
            "kind": kind,
            "claim": CLAIM_BARS_EVALUATED if kind == KIND_BARS else CLAIM_ALIVE_ONLY,
            "binding": self.binding if kind == KIND_STARTED else None,
            "binding_hash": self._binding_hash,
            "previous_progress_hash": self._previous,
            "host_wall_micros": wall,
            "host_monotonic_nanos": monotonic,
            "bars": [dict(bar) for bar in bars],
            "warmup_bars_total": self._warmup,
            "feed": None if feed is None else {
                "continuity_breaks": int(feed.continuity_breaks), "refusals": len(feed.refusals),
                "skipped_without_clock_sample": int(feed.skipped_without_clock_sample)},
        }
        progress = RunnerProgressV1.build(identity)
        self._store.record(progress)
        self._sequence += 1
        self._previous = progress.content_hash
        self._last_write_monotonic = monotonic
        if kind == KIND_STARTED:
            self._started_nanos = wall * 1000
        return progress

    def start(self) -> RunnerProgressV1:
        if self._started_nanos is not None:
            raise RunnerLivenessError("runner_progress_session_already_started")
        return self._write(KIND_STARTED)

    def on_poll(self, bars: Iterable[Any], *, feed: Any = None) -> RunnerProgressV1 | None:
        """Record the poll's live bars (``LiveBarV1``, duck-typed), or a heartbeat when one is due."""
        if self._started_nanos is None:
            raise RunnerLivenessError("runner_progress_session_not_started")
        live = []
        for bar in bars:
            if str(bar.symbol) != self._symbol:
                raise RunnerLivenessError("bar_symbol_is_not_the_runner_symbol")
            if int(bar.completed_by_arrival_nanos) < self._started_nanos:
                self._warmup += 1  # replayed history: warm-up, never forward evidence
                continue
            live.append({"capture_session_id": str(bar.session_id), "feed_segment": int(bar.segment),
                         "bar_open_micros": int(bar.bar.bar_open_micros),
                         "closing_record_hash": str(bar.bar.closing_record_hash),
                         "trade_manifest_hash": str(bar.bar.trade_manifest_hash),
                         "completed_by_arrival_nanos": int(bar.completed_by_arrival_nanos),
                         "admitted": bool(self._gate.admits(int(bar.bar.bar_open_micros)))})
        if live:
            return self._write(KIND_BARS, bars=live, feed=feed)
        if int(self._monotonic()) - self._last_write_monotonic >= self._heartbeat:
            return self._write(KIND_HEARTBEAT, feed=feed)
        return None


# ---------------------------------------------------------------------------
# Sealed evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SealedBarsEvidenceV1:
    """The stable bar identities of one sealed, raw-replayed T4 dataset."""

    dataset_version_id: str
    dataset_content_hash: str
    source_id: str
    session_id: str
    exchange_symbol: str
    #: ``bar_open_micros -> (trade_manifest_hash, closing_record_hash)``.
    bars: Mapping[int, tuple[str, str]]

    @classmethod
    def from_forward_dataset(cls, dataset: Any) -> SealedBarsEvidenceV1:
        """From a ``T4ForwardDatasetBarsV1`` (``forward_bars_from_seal_v1``: intact, raw-replayed seal)."""
        bars: dict[int, tuple[str, str]] = {}
        for bar in dataset.bars:
            if int(bar.bar_open_micros) in bars:
                raise RunnerLivenessError("sealed_bar_minute_not_unique")
            bars[int(bar.bar_open_micros)] = (str(bar.trade_manifest_hash), str(bar.clock.closing_record_hash))
        return cls(str(dataset.dataset_version_id), str(dataset.dataset_content_hash), str(dataset.source_id),
                   str(dataset.session_id), str(dataset.exchange_symbol), bars)


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LivenessIntervalV1:
    start_micros: int
    end_micros: int
    status: str
    reason: str | None
    runner_session_id: str | None = None
    capture_session_id: str | None = None
    datasets: tuple[str, ...] = ()
    hours: tuple[int, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {"start_micros": self.start_micros, "end_micros": self.end_micros, "status": self.status,
                "reason": self.reason, "runner_session_id": self.runner_session_id,
                "capture_session_id": self.capture_session_id, "datasets": list(self.datasets),
                "hour_start_utc_nanos": list(self.hours)}


@dataclass(frozen=True, slots=True)
class RunnerLivenessV1:
    """Proven vs unproven minutes of one candidate's claimed window. Deterministic."""

    payload: Mapping[str, Any]
    intervals: tuple[LivenessIntervalV1, ...]

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(dict(self.payload))

    @property
    def proven_intervals(self) -> list[tuple[int, int]]:
        """``[start, end)`` micros of each proven interval (one per runner and capture session run)."""
        return [(item.start_micros, item.end_micros) for item in self.intervals if item.status == STATUS_PROVEN]

    @property
    def proven_micros(self) -> int:
        return sum(end - start for start, end in self.proven_intervals)

    @property
    def unproven_micros_by_reason(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.intervals:
            if item.status == STATUS_UNPROVEN and item.reason is not None:
                out[item.reason] = out.get(item.reason, 0) + item.end_micros - item.start_micros
        return dict(sorted(out.items()))


@dataclass
class _Run:
    runner_session_id: str
    capture_source_id: str
    feed_segment: int
    bars: dict[int, Mapping[str, Any]] = field(default_factory=dict)
    first: int = 0
    last: int = 0


@dataclass
class _Walk:
    runs: list[_Run] = field(default_factory=list)
    flags: dict[int, str] = field(default_factory=dict)
    alive: list[tuple[int, int]] = field(default_factory=list)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    refusals: list[dict[str, str]] = field(default_factory=list)


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RunnerLivenessError("progress_field_not_an_integer")
    return value


def _session_records(items: Sequence[Mapping[str, Any]]) -> list[tuple[str, Mapping[str, Any]]]:
    """The session's ``(content_hash, identity)`` in sequence order, or a :class:`SessionRefusalV1`."""
    out = []
    for item in items:
        identity = item["identity"]
        if not isinstance(identity, Mapping):
            raise _Refused(SessionRefusalV1.MALFORMED_RECORD)
        if identity_hash_v1(dict(identity)) != str(item["content_hash"]).strip():
            raise _Refused(SessionRefusalV1.CONTENT_HASH_DOES_NOT_REDERIVE)
        try:
            if identity["schema_version"] != RUNNER_PROGRESS_SCHEMA_VERSION_V1 or identity["kind"] not in KINDS:
                raise _Refused(SessionRefusalV1.MALFORMED_RECORD)
            for key in ("sequence", "host_wall_micros", "host_monotonic_nanos"):
                _int(identity[key])
        except (KeyError, RunnerLivenessError) as error:
            raise _Refused(SessionRefusalV1.MALFORMED_RECORD) from error
        out.append((str(item["content_hash"]).strip(), identity))
    out.sort(key=lambda pair: int(pair[1]["sequence"]))
    sequences = [int(identity["sequence"]) for _, identity in out]
    if len(set(sequences)) != len(sequences):
        raise _Refused(SessionRefusalV1.DUPLICATE_SEQUENCE)
    first = out[0][1]
    if int(first["sequence"]) != 0 or first["kind"] != KIND_STARTED or first["previous_progress_hash"] is not None:
        raise _Refused(SessionRefusalV1.RUNNER_SESSION_WITHOUT_START)
    binding = first["binding"]
    if not isinstance(binding, Mapping) or identity_hash_v1(dict(binding)) != first["binding_hash"]:
        raise _Refused(SessionRefusalV1.SESSION_BINDING_CHANGED)
    for (previous_hash, previous), (_, identity) in pairwise(out):
        if identity["kind"] == KIND_STARTED or identity["binding"] is not None:
            raise _Refused(SessionRefusalV1.SESSION_BINDING_CHANGED)
        if identity["binding_hash"] != first["binding_hash"]:
            raise _Refused(SessionRefusalV1.SESSION_BINDING_CHANGED)
        if int(identity["sequence"]) == int(previous["sequence"]) + 1 \
                and identity["previous_progress_hash"] != previous_hash:
            raise _Refused(SessionRefusalV1.HASH_CHAIN_BROKEN)
        if (identity["kind"] == KIND_BARS) != bool(identity["bars"]):
            raise _Refused(SessionRefusalV1.MALFORMED_RECORD)
    return out


class _Refused(Exception):
    def __init__(self, reason: SessionRefusalV1) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _break(previous: Mapping[str, Any], current: Mapping[str, Any], *, max_gap: int,
           max_divergence: int) -> DiscontinuityV1 | None:
    if int(current["sequence"]) != int(previous["sequence"]) + 1:
        return DiscontinuityV1.SEQUENCE_GAP
    wall = (int(current["host_wall_micros"]) - int(previous["host_wall_micros"])) * 1000
    monotonic = int(current["host_monotonic_nanos"]) - int(previous["host_monotonic_nanos"])
    if wall < 0:
        return DiscontinuityV1.HOST_CLOCK_BACKWARDS
    if monotonic < 0:
        return DiscontinuityV1.MONOTONIC_CLOCK_BACKWARDS
    if abs(wall - monotonic) > max_divergence:
        return DiscontinuityV1.HOST_CLOCK_JUMP
    if max(wall, monotonic) > max_gap:
        return DiscontinuityV1.PROGRESS_GAP
    return None


def _walk_session(walk: _Walk, records: Sequence[tuple[str, Mapping[str, Any]]], *, max_gap: int,
                  max_divergence: int, max_lag: int) -> None:
    """Chains, processing runs, minute flags and alive spans of one relevant runner session."""
    first = records[0][1]
    session_id = str(first["runner_session_id"])
    source = str(first["binding"]["capture_source_id"])
    started = int(first["host_wall_micros"]) * 1000
    breaks: list[dict[str, Any]] = []
    chain_start, previous = started, first
    run: _Run | None = None

    def close() -> None:
        nonlocal run
        if run is not None and run.bars:
            walk.runs.append(run)
        run = None

    for _, record in records[1:]:
        wall = int(record["host_wall_micros"]) * 1000
        broken = _break(previous, record, max_gap=max_gap, max_divergence=max_divergence)
        if broken is not None:
            breaks.append({"kind": broken.value, "sequence": int(record["sequence"]),
                           "host_wall_micros": int(record["host_wall_micros"])})
            walk.alive.append((chain_start, int(previous["host_wall_micros"]) * 1000))
            chain_start = wall
            close()
        previous = record
        for bar in record["bars"]:
            minute = _int(bar["bar_open_micros"])
            completed = _int(bar["completed_by_arrival_nanos"])
            lag = wall - completed
            if completed < started:
                close()  # replayed history is warm-up, never forward evidence
                continue
            if lag < 0:
                breaks.append({"kind": DiscontinuityV1.BAR_COMPLETED_AFTER_PROCESSING.value,
                               "sequence": int(record["sequence"]), "host_wall_micros": int(record["host_wall_micros"])})
                walk.flags.setdefault(minute, UnprovenReasonV1.BAR_COMPLETED_AFTER_PROCESSING.value)
                close()
                continue
            if lag > max_lag:
                walk.flags.setdefault(minute, UnprovenReasonV1.BAR_PROCESSED_LATE.value)
                close()
                continue
            if bar["admitted"] is not True:
                walk.flags.setdefault(minute, UnprovenReasonV1.CANDIDATE_NOT_EVALUATED.value)
                close()
                continue
            segment = _int(bar["feed_segment"])
            if run is not None and (segment != run.feed_segment or minute <= run.last):
                if segment != run.feed_segment:
                    breaks.append({"kind": DiscontinuityV1.FEED_SEGMENT_BREAK.value,
                                   "sequence": int(record["sequence"]),
                                   "host_wall_micros": int(record["host_wall_micros"])})
                close()
            if run is None:
                run = _Run(session_id, source, segment, first=minute)
            run.bars[minute] = bar
            run.last = minute
    walk.alive.append((chain_start, int(previous["host_wall_micros"]) * 1000))
    close()
    walk.sessions.append({"runner_session_id": session_id, "runner": str(first["binding"]["runner"]),
                          "started_host_wall_micros": int(first["host_wall_micros"]),
                          "last_host_wall_micros": int(previous["host_wall_micros"]),
                          "records": len(records), "discontinuities": breaks})


def _relevant(binding: Mapping[str, Any], study_id: str, trial_id: str, symbol: str) -> bool:
    return binding.get("symbol") == symbol and any(
        item.get("study_id") == study_id and item.get("trial_id") == trial_id for item in binding["candidates"])


def _hour_verdicts(acceptance: HourlyAcceptanceV1, symbol: str) -> dict[int, Any] | None:
    found = [item for item in acceptance.symbols if item.symbol == symbol]
    if not found:
        return None
    first = acceptance.first_hour_start_utc_nanos
    segments = found[0].segments
    if len(found) != 1 or len(segments) != acceptance.hours_judged:
        raise RunnerLivenessError("hourly_acceptance_malformed")
    out = {}
    for index, segment in enumerate(segments):
        if first is None or segment.hour_start_utc_nanos != first + index * HOUR_NANOS:
            raise RunnerLivenessError("hourly_acceptance_malformed")
        out[segment.hour_start_utc_nanos] = segment
    return out


def derive_runner_liveness_v1(records: Sequence[Mapping[str, Any]], *, study_id: str, trial_id: str, symbol: str,
                              window_start: datetime, window_end: datetime, acceptance: HourlyAcceptanceV1,
                              sealed: Sequence[SealedBarsEvidenceV1], max_progress_gap_nanos: int,
                              max_clock_divergence_nanos: int, max_processing_lag_nanos: int) -> RunnerLivenessV1:
    """Proven vs unproven minutes of ``[window_start, window_end)`` for one candidate.

    ``records`` are stored progress rows (``{"content_hash", "identity"}``) -- every
    session may be passed; only sessions watching this candidate on this symbol
    count. ``acceptance`` is the capture source's hourly acceptance and ``sealed``
    the sealed datasets available. The three bounds have no default.
    """
    start, end = _micros(window_start), _micros(window_end)
    if start % _MINUTE_MICROS or end % _MINUTE_MICROS or end <= start:
        raise RunnerLivenessError("window_must_be_a_non_empty_whole_minute_range")
    for bound in (max_progress_gap_nanos, max_clock_divergence_nanos, max_processing_lag_nanos):
        if isinstance(bound, bool) or not isinstance(bound, int) or bound <= 0:
            raise RunnerLivenessError("liveness_bounds_must_be_positive_integer_nanos")

    walk = _Walk()
    by_session: dict[str, list[Mapping[str, Any]]] = {}
    for item in records:
        identity = item.get("identity")
        key = str(identity.get("runner_session_id")) if isinstance(identity, Mapping) else "<malformed>"
        by_session.setdefault(key, []).append(item)
    for session_id, items in sorted(by_session.items()):
        try:
            ordered = _session_records(items)
            relevant = _relevant(ordered[0][1]["binding"], study_id, trial_id, symbol)
        except _Refused as refused:
            walk.refusals.append({"runner_session_id": session_id, "reason": refused.reason.value})
            continue
        except (KeyError, TypeError, AttributeError):
            walk.refusals.append({"runner_session_id": session_id, "reason": SessionRefusalV1.MALFORMED_RECORD.value})
            continue
        if not relevant:
            continue
        try:
            _walk_session(walk, ordered, max_gap=max_progress_gap_nanos, max_divergence=max_clock_divergence_nanos,
                          max_lag=max_processing_lag_nanos)
        except (KeyError, TypeError, RunnerLivenessError):
            walk.refusals.append({"runner_session_id": session_id, "reason": SessionRefusalV1.MALFORMED_RECORD.value})

    # Which run proves each minute (the earliest-started one); disagreeing runs prove nothing there.
    proof: dict[int, _Run] = {}
    conflicts: set[int] = set()
    for run in sorted(walk.runs, key=lambda item: (item.first, item.runner_session_id)):
        for minute in range(max(run.first, start), min(run.last + _MINUTE_MICROS, end), _MINUTE_MICROS):
            held = proof.get(minute)
            if held is None:
                proof[minute] = run
                continue
            mine, theirs = run.bars.get(minute), held.bars.get(minute)
            if (mine is None) != (theirs is None) or (mine is not None and theirs is not None and (
                    mine["trade_manifest_hash"], mine["closing_record_hash"], mine["capture_session_id"]) != (
                    theirs["trade_manifest_hash"], theirs["closing_record_hash"], theirs["capture_session_id"])):
                conflicts.add(minute)

    verdicts = _hour_verdicts(acceptance, symbol)
    sealed_index: dict[tuple[str, str], list[SealedBarsEvidenceV1]] = {}
    for dataset in sealed:
        if dataset.exchange_symbol == symbol:
            sealed_index.setdefault((dataset.source_id, dataset.session_id), []).append(dataset)
    hour_cache: dict[tuple[int, int], tuple[str | None, str | None]] = {}
    hour_micros = HOUR_NANOS // 1000

    def hour_check(hour: int, run: _Run) -> tuple[str | None, str | None]:
        """``(unproven reason or None, capture session proving the hour)`` for this run's part of the hour."""
        key = (hour, id(run))
        if key in hour_cache:
            return hour_cache[key]
        if verdicts is None:
            result: tuple[str | None, str | None] = (
                UnprovenReasonV1.CAPTURE_ACCEPTANCE_DOES_NOT_COVER_SYMBOL.value, None)
        elif hour * 1000 not in verdicts:
            result = (UnprovenReasonV1.CAPTURE_HOUR_NOT_JUDGED.value, None)
        else:
            segment = verdicts[hour * 1000]
            session = None if segment.session_id is None else str(segment.session_id)
            bars = [bar for minute, bar in run.bars.items() if minute - minute % hour_micros == hour]
            if not segment.complete:
                result = (CAPTURE_HOUR_PREFIX + segment.verdict.value, session)
            elif session is None or any(bar["capture_session_id"] != session for bar in bars):
                result = (UnprovenReasonV1.CAPTURE_HOUR_SESSION_MISMATCH.value, session)
            elif any(_sealed_disagrees(sealed_index.get((run.capture_source_id, session), []), bar) for bar in bars):
                result = (UnprovenReasonV1.SEAL_CONFLICT.value, session)
            else:
                result = (None, session)
        hour_cache[key] = result
        return result

    minutes: list[tuple[tuple[Any, ...], int, frozenset[str], int]] = []
    for minute in range(start, end, _MINUTE_MICROS):
        prover = proof.get(minute)
        hour = minute // hour_micros * hour_micros
        if prover is None:
            reason = walk.flags.get(minute) or (
                UnprovenReasonV1.ALIVE_WITHOUT_DATA_PROOF.value
                if any(low <= minute * 1000 < high for low, high in walk.alive)
                else UnprovenReasonV1.NO_RUNNER_PROGRESS.value)
            minutes.append(((STATUS_UNPROVEN, reason), minute, frozenset(), hour))
            continue
        if minute in conflicts:
            minutes.append(((STATUS_UNPROVEN, UnprovenReasonV1.RUNNER_EVIDENCE_CONFLICT.value), minute, frozenset(),
                            hour))
            continue
        failed, session = hour_check(hour, prover)
        datasets: frozenset[str] = frozenset()
        if failed is None:
            candidates = sealed_index.get((prover.capture_source_id, str(session)), [])
            failed, datasets = _sealed_minute(candidates, minute, prover.bars.get(minute))
        if failed is not None:
            minutes.append(((STATUS_UNPROVEN, failed), minute, frozenset(), hour))
        else:
            minutes.append(((STATUS_PROVEN, prover.runner_session_id, session), minute, datasets, hour))

    intervals: list[LivenessIntervalV1] = []
    group: list[tuple[tuple[Any, ...], int, frozenset[str], int]] = []

    def flush() -> None:
        if not group:
            return
        key = group[0][0]
        first_minute, last_minute = group[0][1], group[-1][1] + _MINUTE_MICROS
        if key[0] == STATUS_PROVEN:
            used = sorted({dataset for item in group for dataset in item[2]})
            hours = sorted({item[3] * 1000 for item in group})
            intervals.append(LivenessIntervalV1(first_minute, last_minute, STATUS_PROVEN, None, key[1], key[2],
                                                tuple(used), tuple(hours)))
        else:
            intervals.append(LivenessIntervalV1(first_minute, last_minute, STATUS_UNPROVEN, key[1]))
        group.clear()

    for entry in minutes:
        if group and group[0][0] != entry[0]:
            flush()
        group.append(entry)
    flush()

    sessions = sorted(walk.sessions, key=lambda item: (item["started_host_wall_micros"], item["runner_session_id"]))
    payload = {
        "schema_version": RUNNER_LIVENESS_SCHEMA_VERSION_V1,
        "claim": LIVENESS_CLAIM_V1,
        "candidate": {"study_id": study_id, "trial_id": trial_id, "symbol": symbol},
        "window": {"start_micros": start, "end_micros": end},
        "bounds": {"max_progress_gap_nanos": max_progress_gap_nanos,
                   "max_clock_divergence_nanos": max_clock_divergence_nanos,
                   "max_processing_lag_nanos": max_processing_lag_nanos,
                   "capture_max_unproven_nanos": acceptance.max_unproven_nanos},
        "runner_sessions": sessions,
        "restarts": max(0, len(sessions) - 1),
        "refused_sessions": sorted(walk.refusals, key=lambda item: (item["runner_session_id"], item["reason"])),
        "intervals": [item.payload() for item in intervals],
        "proven_micros": sum(item.end_micros - item.start_micros for item in intervals
                             if item.status == STATUS_PROVEN),
    }
    return RunnerLivenessV1(payload, tuple(intervals))


def _sealed_disagrees(datasets: Sequence[SealedBarsEvidenceV1], bar: Mapping[str, Any]) -> bool:
    mine = (bar["trade_manifest_hash"], bar["closing_record_hash"])
    minute = int(bar["bar_open_micros"])
    return any(minute in dataset.bars and dataset.bars[minute] != mine for dataset in datasets)


def _sealed_minute(datasets: Sequence[SealedBarsEvidenceV1], minute: int,
                   bar: Mapping[str, Any] | None) -> tuple[str | None, frozenset[str]]:
    """Whether the sealed evidence of the capture session agrees with this processed minute."""
    sealed = [dataset for dataset in datasets if minute in dataset.bars]
    if bar is not None:
        if not sealed:
            return UnprovenReasonV1.BAR_NOT_SEALED.value, frozenset()
        return None, frozenset(f"{d.dataset_version_id}:{d.dataset_content_hash}" for d in sealed)
    if sealed:
        return UnprovenReasonV1.SEALED_BAR_NOT_PROCESSED.value, frozenset()
    covering = [dataset for dataset in datasets if dataset.bars and min(dataset.bars) <= minute <= max(dataset.bars)]
    if not covering:
        return UnprovenReasonV1.NOT_SEALED.value, frozenset()
    return None, frozenset(f"{d.dataset_version_id}:{d.dataset_content_hash}" for d in covering)


# ---------------------------------------------------------------------------
# Persistence (migration 20261017_0064)
# ---------------------------------------------------------------------------


class PostgresRunnerLivenessStoreV1:
    """Append-only runner progress (``runner_liveness_progress``)."""

    def __init__(self, database: Any) -> None:
        self._database = database

    def record(self, progress: RunnerProgressV1) -> bool:
        """Insert once per ``session:sequence``; different content under a recorded key is refused."""
        identity = progress.identity
        if identity_hash_v1(dict(identity)) != progress.content_hash:
            raise RunnerLivenessError("progress_identity_does_not_rederive")
        binding = identity["binding"]
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO runner_liveness_progress (progress_id, idempotency_key, content_hash, runner_session_id, "
                "sequence, kind, symbol, capture_source_id, binding_hash, previous_progress_hash, host_wall_at, "
                "bar_count, identity, recorded_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) "
                "ON CONFLICT (idempotency_key) DO NOTHING RETURNING progress_id",
                (progress.progress_id, progress.idempotency_key, progress.content_hash,
                 UUID(str(identity["runner_session_id"])), int(identity["sequence"]), identity["kind"],
                 None if binding is None else binding["symbol"],
                 None if binding is None else UUID(str(binding["capture_source_id"])),
                 identity["binding_hash"], identity["previous_progress_hash"],
                 _EPOCH + timedelta(microseconds=int(identity["host_wall_micros"])), len(identity["bars"]),
                 json.dumps(dict(identity), sort_keys=True), datetime.now(UTC)),
            )
            if cursor.fetchone() is not None:
                return True
            cursor.execute("SELECT content_hash FROM runner_liveness_progress WHERE idempotency_key=%s",
                           (progress.idempotency_key,))
            row = cursor.fetchone()
        if row is None or str(row[0]).strip() != progress.content_hash:
            raise RunnerLivenessError("progress_conflicts_with_a_recorded_record")
        return False

    def for_candidate(self, *, study_id: str, trial_id: str, symbol: str) -> list[dict[str, Any]]:
        """Every record of every runner session that watched this candidate on ``symbol``."""
        wanted = json.dumps([{"study_id": study_id, "trial_id": trial_id}])
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT p.content_hash, p.identity FROM runner_liveness_progress p WHERE p.runner_session_id IN "
                "(SELECT s.runner_session_id FROM runner_liveness_progress s WHERE s.kind='STARTED' AND s.symbol=%s "
                "AND s.identity->'binding'->'candidates' @> %s::jsonb) ORDER BY p.runner_session_id, p.sequence",
                (symbol, wanted),
            )
            rows = cursor.fetchall()
        return [{"content_hash": str(row[0]).strip(),
                 "identity": row[1] if isinstance(row[1], dict) else json.loads(row[1])} for row in rows]


__all__ = [
    "CAPTURE_HOUR_PREFIX",
    "CLAIM_ALIVE_ONLY",
    "CLAIM_BARS_EVALUATED",
    "DEFAULT_HEARTBEAT_INTERVAL_NANOS",
    "KIND_BARS",
    "KIND_HEARTBEAT",
    "KIND_STARTED",
    "LIVENESS_CLAIM_V1",
    "STATUS_PROVEN",
    "STATUS_UNPROVEN",
    "DiscontinuityV1",
    "LivenessIntervalV1",
    "PostgresRunnerLivenessStoreV1",
    "RunnerLivenessError",
    "RunnerLivenessV1",
    "RunnerProgressRecorderV1",
    "RunnerProgressV1",
    "SealedBarsEvidenceV1",
    "SessionRefusalV1",
    "UnprovenReasonV1",
    "candidate_payload_v1",
    "derive_runner_liveness_v1",
]
