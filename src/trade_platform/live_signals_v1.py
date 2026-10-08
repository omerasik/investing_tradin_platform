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
only when a later trade proves it, trade order checked on every trade) is
identical by construction. Live arrivals cannot be bracketed by a *later* clock
sample yet, so each record carries a provisional bound from the latest sample
already received (``LIVE_PROVISIONAL``); bar prices do not depend on the bound,
and the sealed replay later supplies the bracketed knowledge times.

Continuity: consecutive bars stay in one strategy segment unless the arrival
gap between consecutive records exceeds :data:`MAX_LIVE_CONTINUITY_GAP_NANOS`
or the normalizer refused a record. That is an engineering tolerance for the
recorder's hourly segment boundaries (a few seconds), not an economic
parameter: a host sleep or an outage breaks the segment, minutes lost inside a
short boundary gap are simply absent (never filled), and no rolling window
spans a broken segment.

Signals (:class:`LiveStrategyRunnerV1`)
---------------------------------------
Each watched candidate is a frozen Strategy Lab trial whose authority is
explicit: ``RESEARCH_WATCH`` (Decimal-authoritative frozen candidate, not
validated) or ``INCUBATING`` (passed a preregistered holdout; not validated).
On every completed bar the candidate's Decimal targets are recomputed over the
current segment; a change of target emits a :class:`LiveSignalV1` carrying the
candidate and evidence identity, the decision instant (the host instant the
decision was computed, never earlier than the closing record's arrival), the
numeric and cost policies, an exact explanation and the authority label. A
signal is a proposal: its fill (R10) is the next strictly later bar open.
Nothing here can label a signal validated.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class LiveSignalsError(ValueError):
    """Raised when live evidence or a watched candidate cannot be used honestly."""


# ---------------------------------------------------------------------------
# Live bars
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveBarV1:
    symbol: str
    segment: int
    session_id: UUID
    bar: T4MinuteBarV1
    completed_by_arrival_nanos: int

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
    emitted: int = 0
    offsets: dict[str, int] = field(default_factory=dict)
    consumed_compacted: set[str] = field(default_factory=set)


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
    def _samples(directory: Path, resolution: int) -> list[ClockOffsetSampleEvidenceV1]:
        samples = []
        for event in read_lifecycle_v1(directory):
            if event.kind == CaptureLifecycleKindV1.CLOCK_OFFSET_SAMPLE.value:
                samples.append(parse_clock_offset_sample_v1(event, session_resolution_nanos=resolution))
        return sorted(samples, key=lambda sample: sample.host_receive_utc_nanos)

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
            samples = self._samples(directory, resolution)
            for line in self._new_lines(state, directory):
                if not line.strip():
                    continue
                record = FirstPartyCaptureRecordV1.from_json_line(line)
                if record.sequence <= state.last_sequence:
                    continue
                state.last_sequence = record.sequence
                prior = [s for s in samples if s.host_receive_utc_nanos <= record.arrival_utc_nanos]
                if not prior:
                    self.skipped_without_clock_sample += 1
                    continue
                if self._last_arrival is not None and (
                    record.arrival_utc_nanos - self._last_arrival > MAX_LIVE_CONTINUITY_GAP_NANOS
                ):
                    self._segment += 1
                self._last_arrival = record.arrival_utc_nanos
                bound = ArrivalClockBoundEvidenceV1(prior[-1].venue_minus_host_upper_nanos,
                                                    prior[-1].sample_hash, LIVE_PROVISIONAL)
                try:
                    state.normalizer.feed(record, bound)
                except T4NormalizationError as error:
                    # Refused evidence breaks the segment; the next record starts afresh.
                    self.refusals.append(f"{session_id}:{record.sequence}:{error}")
                    state.normalizer = T4SegmentNormalizerV1(
                        exchange_symbol=self._contract.exchange_symbol, session_id=session_id)
                    state.emitted = 0
                    self._segment += 1
                    continue
                bars = state.normalizer.output.bars
                for bar in bars[state.emitted:]:
                    out.append(LiveBarV1(self._contract.exchange_symbol, self._segment, session_id, bar,
                                         record.arrival_utc_nanos))
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


@dataclass(frozen=True, slots=True)
class LiveSignalV1:
    identity: Mapping[str, Any]
    signal_id: UUID
    decided_at: datetime


def _rows_and_keys(bars: Sequence[LiveBarV1]) -> tuple[list[tuple[Any, ...]], list[int]]:
    return [bar.row() for bar in bars], [bar.segment for bar in bars]


class LiveStrategyRunnerV1:
    """Evaluates watched candidates on every completed bar; emits a signal on a target change."""

    def __init__(self, candidates: Sequence[WatchedCandidateV1], *,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._candidates = list(candidates)
        self._clock = clock
        self._history: dict[str, list[LiveBarV1]] = {}
        self._last_target: dict[tuple[str, str], int] = {}

    def on_bars(self, bars: Sequence[LiveBarV1]) -> list[LiveSignalV1]:
        signals: list[LiveSignalV1] = []
        for bar in bars:
            history = self._history.setdefault(bar.symbol, [])
            if history and history[-1].segment != bar.segment:
                history.clear()  # a broken segment never feeds a rolling window
            history.append(bar)
            rows, keys = _rows_and_keys(history)
            window = BarsV1.from_rows(rows, segment_keys=keys)
            for candidate in self._candidates:
                if candidate.symbol != bar.symbol:
                    continue
                family = FAMILIES_V1[candidate.study.strategy.family]
                params = candidate.parameters
                targets = family.targets_decimal(window, params)
                key = (candidate.study.strategy.family, candidate.trial_id)
                previous = self._last_target.get(key, targets[-2] if len(targets) > 1 else 0)
                current = targets[-1]
                self._last_target[key] = current
                if current == previous:
                    continue
                decided_at = max(self._clock(), _EPOCH + timedelta(microseconds=bar.completed_by_arrival_nanos // 1000))
                signals.append(_signal(candidate, bar, previous, current, family.explain_decimal(window, params),
                                       decided_at, window))
        return signals


def _signal(candidate: WatchedCandidateV1, bar: LiveBarV1, previous: int, current: int,
            explanation: Mapping[str, str], decided_at: datetime, window: BarsV1) -> LiveSignalV1:
    study = candidate.study
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
                     "segment": bar.segment, "bar_open_micros": bar.bar.bar_open_micros,
                     "closing_record_hash": bar.bar.closing_record_hash,
                     "trade_manifest_hash": bar.bar.trade_manifest_hash, "window_bars": window.size,
                     "clock_bound": LIVE_PROVISIONAL},
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
    "LiveSignalV1",
    "LiveSignalsError",
    "LiveStrategyRunnerV1",
    "PostgresLiveSignalStoreV1",
    "WatchedCandidateV1",
]
