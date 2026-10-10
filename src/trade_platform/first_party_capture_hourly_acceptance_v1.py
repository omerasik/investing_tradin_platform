"""Phase R1B: manifest-proven hourly-segment acceptance for the capture universe.

The R1B burn-in asks whether the universe recorder (OR-2: BTCUSDT, ETHUSDT,
SOLUSDT) produced a long enough run of consecutive *proven COMPLETE* UTC hours
for every symbol at once (Option B: 24). This module answers that from the
archive's own evidence and nothing else: the proven coverage windows that
COMPLETE, manifested partitions declare (see
:func:`~trade_platform.first_party_capture_archive_v1.derive_archive_availability_v1`).
Wall-clock time elapsed since a recorder started is never evidence: an hour
with no manifested window covering it is not complete, whatever the recorder
was believed to be doing.

Rules, all fail-closed:

* **Runs.** Windows of one session are chained into a single run only across a
  ``UTC_DAY_ROLLOVER`` end proof -- the same connected session rotating its
  partition at midnight -- and only into a window that starts on the very next
  UTC day. Any other end proof ends the run; a different session always starts
  a new one. Two sessions never form one run, however close. A run keeps each
  of its proven intervals: chaining never turns the time between two windows
  into proven coverage.
* **COMPLETE hour.** UTC hour ``[H, H + 1 h)`` of one symbol is COMPLETE when a
  single run reaches ``H + 1 h`` (exclusive end of its last window) and the
  part of the hour that run does *not* prove -- measured against its own
  proven intervals -- totals at most ``max_unproven``. That unproven time is
  the unavoidable hand-off: the head ``[H, run start)`` between bounded hourly
  segments (connect, subscribe, head clock sample), and the instant between
  the last record before and the first record after a midnight rollover. It is
  reported per hour and bounded by one explicit operator parameter with no
  default: how long a hand-off may be is an acceptance choice, not something
  this module invents. A missing, unmanifested or replay-rejected partition
  leaves its time unproven, so the hours it covered can never be COMPLETE.
* **Joint hour.** An hour is jointly COMPLETE only when it is COMPLETE for every
  required symbol. A symbol with no evidence makes every hour incomplete.
* **Horizon.** Only hours that end at or before the latest proven instant of
  any required symbol are judged; the open hour (its partition has no manifest
  yet) is never judged either way.

Metadata-level, like availability itself: a dataset built on these hours must
still verify each partition it uses. Passing this check is evidence for OR-1;
accepting the burn-in stays the owner's decision.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from trade_platform.first_party_capture_archive_v1 import (
    END_PROOF_UTC_DAY_ROLLOVER,
    ProvenWindowV1,
)

HOUR_NANOS: Final = 3_600 * 1_000_000_000
DAY_NANOS: Final = 24 * HOUR_NANOS


class HourlySegmentAcceptanceError(ValueError):
    """Raised when the acceptance question itself is malformed."""


class HourVerdictV1(StrEnum):
    """Why one symbol's UTC hour is, or is not, proven COMPLETE."""

    COMPLETE = "COMPLETE"
    #: No proven window of any run intersects the hour.
    NO_PROVEN_COVERAGE = "NO_PROVEN_COVERAGE"
    #: Coverage exists, but no run starts within the bound and reaches the hour's end.
    COVERAGE_ENDS_BEFORE_HOUR_END = "COVERAGE_ENDS_BEFORE_HOUR_END"
    #: The only runs that reach the hour's end start later than the bound.
    HEAD_UNPROVEN_EXCEEDS_BOUND = "HEAD_UNPROVEN_EXCEEDS_BOUND"
    #: A run spans the hour, but the time between its proven intervals exceeds the bound.
    UNPROVEN_GAP_EXCEEDS_BOUND = "UNPROVEN_GAP_EXCEEDS_BOUND"


@dataclass(frozen=True, slots=True)
class ProvenRunV1:
    """Consecutive proven windows of one session, linked only by day rollovers.

    ``intervals`` holds each window's ``[start, exclusive end)``, in order; the
    time between them is unproven and stays so.
    """

    session_id: UUID
    intervals: tuple[tuple[int, int], ...]
    end_proof: str

    @property
    def start_utc_nanos(self) -> int:
        return self.intervals[0][0]

    @property
    def end_utc_nanos(self) -> int:
        """Exclusive representational bound of the last window."""
        return self.intervals[-1][1]

    @property
    def rollover_links(self) -> int:
        return len(self.intervals) - 1

    def unproven_nanos(self, start_utc_nanos: int, end_utc_nanos: int) -> int:
        """How much of ``[start, end)`` none of this run's proven intervals covers."""
        proven = sum(
            max(0, min(end, end_utc_nanos) - max(start, start_utc_nanos))
            for start, end in self.intervals
        )
        return (end_utc_nanos - start_utc_nanos) - proven


@dataclass(frozen=True, slots=True)
class HourlySegmentV1:
    """One symbol's verdict for the UTC hour starting at ``hour_start_utc_nanos``.

    ``unproven_nanos`` is the part of the hour the named run does not prove.
    """

    hour_start_utc_nanos: int
    verdict: HourVerdictV1
    session_id: UUID | None = None
    unproven_nanos: int | None = None
    detail: str | None = None

    @property
    def complete(self) -> bool:
        return self.verdict is HourVerdictV1.COMPLETE


@dataclass(frozen=True, slots=True)
class ConsecutiveHoursV1:
    """A maximal run of consecutive jointly COMPLETE hours."""

    first_hour_start_utc_nanos: int
    hours: int

    @property
    def end_utc_nanos(self) -> int:
        return self.first_hour_start_utc_nanos + self.hours * HOUR_NANOS


@dataclass(frozen=True, slots=True)
class SymbolHoursV1:
    symbol: str
    segments: tuple[HourlySegmentV1, ...]

    @property
    def complete_hours(self) -> int:
        return sum(1 for segment in self.segments if segment.complete)


@dataclass(frozen=True, slots=True)
class HourlyAcceptanceV1:
    """The joint hourly acceptance over every required symbol."""

    required_consecutive_hours: int
    max_unproven_nanos: int
    first_hour_start_utc_nanos: int | None
    hours_judged: int
    symbols: tuple[SymbolHoursV1, ...]
    joint_complete: tuple[bool, ...]
    runs: tuple[ConsecutiveHoursV1, ...]

    @property
    def longest_run(self) -> ConsecutiveHoursV1 | None:
        if not self.runs:
            return None
        return max(self.runs, key=lambda run: (run.hours, run.first_hour_start_utc_nanos))

    @property
    def current_run_hours(self) -> int:
        """Consecutive jointly COMPLETE hours ending at the last judged hour (0 if it is not)."""
        if not self.joint_complete or not self.joint_complete[-1] or not self.runs:
            return 0
        return self.runs[-1].hours

    @property
    def met(self) -> bool:
        longest = self.longest_run
        return longest is not None and longest.hours >= self.required_consecutive_hours

    @property
    def max_unproven_observed_nanos(self) -> int | None:
        """The longest unproven hand-off inside any COMPLETE hour."""
        observed = [
            segment.unproven_nanos
            for symbol in self.symbols
            for segment in symbol.segments
            if segment.complete and segment.unproven_nanos is not None
        ]
        return max(observed) if observed else None


def chain_proven_runs_v1(windows: Sequence[ProvenWindowV1]) -> tuple[ProvenRunV1, ...]:
    """Chain one source's proven windows into runs, in start order.

    A window continues the previous run only when both belong to the same
    session, the previous window ended with ``UTC_DAY_ROLLOVER`` and this one
    starts on the UTC day right after the previous window's last proven
    instant (a rollover proves one midnight, never a missing day). Linking
    keeps both intervals; the time between them stays unproven.
    """
    ordered = sorted(
        windows, key=lambda window: (window.interval.start_utc_nanos, str(window.session_id))
    )
    open_runs: dict[UUID, ProvenRunV1] = {}
    runs: list[ProvenRunV1] = []
    for window in ordered:
        interval = window.interval
        own = (interval.start_utc_nanos, interval.end_utc_nanos)
        previous = open_runs.pop(window.session_id, None)
        if (
            previous is not None
            and previous.end_proof == END_PROOF_UTC_DAY_ROLLOVER
            and interval.start_utc_nanos >= previous.end_utc_nanos
            and interval.start_utc_nanos // DAY_NANOS
            == (previous.end_utc_nanos - 1) // DAY_NANOS + 1
        ):
            runs.remove(previous)
            run = ProvenRunV1(
                session_id=window.session_id,
                intervals=(*previous.intervals, own),
                end_proof=interval.end_proof,
            )
        else:
            run = ProvenRunV1(session_id=window.session_id, intervals=(own,), end_proof=interval.end_proof)
        runs.append(run)
        open_runs[window.session_id] = run
    return tuple(sorted(runs, key=lambda run: (run.start_utc_nanos, str(run.session_id))))


def classify_hour_v1(
    runs: Sequence[ProvenRunV1], hour_start_utc_nanos: int, *, max_unproven_nanos: int
) -> HourlySegmentV1:
    """One symbol's verdict for one UTC hour, from its proven runs only.

    Every verdict naming a run reports how much of the hour that run leaves
    unproven, measured against its own intervals.
    """
    if hour_start_utc_nanos % HOUR_NANOS:
        raise HourlySegmentAcceptanceError("hour_start_is_not_on_the_utc_hour_grid")
    hour_end = hour_start_utc_nanos + HOUR_NANOS
    head_limit = hour_start_utc_nanos + max_unproven_nanos

    def unproven(run: ProvenRunV1) -> int:
        return run.unproven_nanos(hour_start_utc_nanos, hour_end)

    intersecting = [run for run in runs if unproven(run) < HOUR_NANOS]
    if not intersecting:
        return HourlySegmentV1(hour_start_utc_nanos, HourVerdictV1.NO_PROVEN_COVERAGE)
    spanning = [
        run
        for run in intersecting
        if run.start_utc_nanos <= head_limit and run.end_utc_nanos >= hour_end
    ]
    if spanning:
        best = min(spanning, key=lambda run: (unproven(run), run.start_utc_nanos, str(run.session_id)))
        verdict = (
            HourVerdictV1.COMPLETE
            if unproven(best) <= max_unproven_nanos
            else HourVerdictV1.UNPROVEN_GAP_EXCEEDS_BOUND
        )
        return HourlySegmentV1(
            hour_start_utc_nanos, verdict, session_id=best.session_id, unproven_nanos=unproven(best)
        )
    headed = [run for run in intersecting if run.start_utc_nanos <= head_limit]
    if headed:
        last = max(headed, key=lambda run: (run.end_utc_nanos, str(run.session_id)))
        return HourlySegmentV1(
            hour_start_utc_nanos,
            HourVerdictV1.COVERAGE_ENDS_BEFORE_HOUR_END,
            session_id=last.session_id,
            unproven_nanos=unproven(last),
            detail=last.end_proof,
        )
    late = min(intersecting, key=lambda run: (run.start_utc_nanos, str(run.session_id)))
    return HourlySegmentV1(
        hour_start_utc_nanos,
        HourVerdictV1.HEAD_UNPROVEN_EXCEEDS_BOUND,
        session_id=late.session_id,
        unproven_nanos=unproven(late),
    )


def _consecutive_runs(first_hour: int, joint: Sequence[bool]) -> tuple[ConsecutiveHoursV1, ...]:
    runs: list[ConsecutiveHoursV1] = []
    start: int | None = None
    for index, complete in enumerate([*joint, False]):
        if complete and start is None:
            start = index
        elif not complete and start is not None:
            runs.append(ConsecutiveHoursV1(first_hour + start * HOUR_NANOS, index - start))
            start = None
    return tuple(runs)


def derive_hourly_acceptance_v1(
    windows_by_symbol: Sequence[tuple[str, Sequence[ProvenWindowV1]]],
    *,
    max_unproven_nanos: int,
    required_consecutive_hours: int,
) -> HourlyAcceptanceV1:
    """Judge every complete UTC hour across the required symbols, jointly.

    ``windows_by_symbol`` must name each required symbol once, each with only
    its own source's proven windows -- never merged across sources.
    """
    if not windows_by_symbol:
        raise HourlySegmentAcceptanceError("acceptance_requires_at_least_one_symbol")
    symbols = [symbol for symbol, _ in windows_by_symbol]
    if len(set(symbols)) != len(symbols):
        raise HourlySegmentAcceptanceError("acceptance_symbols_must_be_distinct")
    if not 0 <= max_unproven_nanos < HOUR_NANOS:
        raise HourlySegmentAcceptanceError("max_unproven_must_be_within_one_hour")
    if required_consecutive_hours < 1:
        raise HourlySegmentAcceptanceError("required_consecutive_hours_must_be_positive")

    runs_by_symbol = [(symbol, chain_proven_runs_v1(windows)) for symbol, windows in windows_by_symbol]
    all_runs = [run for _, runs in runs_by_symbol for run in runs]
    if not all_runs:
        return HourlyAcceptanceV1(
            required_consecutive_hours=required_consecutive_hours,
            max_unproven_nanos=max_unproven_nanos,
            first_hour_start_utc_nanos=None,
            hours_judged=0,
            symbols=tuple(SymbolHoursV1(symbol, ()) for symbol in symbols),
            joint_complete=(),
            runs=(),
        )
    first_hour = min(run.start_utc_nanos for run in all_runs) // HOUR_NANOS * HOUR_NANOS
    horizon = max(run.end_utc_nanos for run in all_runs) // HOUR_NANOS * HOUR_NANOS
    hours = max(0, (horizon - first_hour) // HOUR_NANOS)
    symbol_hours = tuple(
        SymbolHoursV1(
            symbol,
            tuple(
                classify_hour_v1(
                    runs,
                    first_hour + index * HOUR_NANOS,
                    max_unproven_nanos=max_unproven_nanos,
                )
                for index in range(hours)
            ),
        )
        for symbol, runs in runs_by_symbol
    )
    joint = tuple(
        all(symbol.segments[index].complete for symbol in symbol_hours) for index in range(hours)
    )
    return HourlyAcceptanceV1(
        required_consecutive_hours=required_consecutive_hours,
        max_unproven_nanos=max_unproven_nanos,
        first_hour_start_utc_nanos=first_hour,
        hours_judged=hours,
        symbols=symbol_hours,
        joint_complete=joint,
        runs=_consecutive_runs(first_hour, joint),
    )


__all__ = [
    "HOUR_NANOS",
    "ConsecutiveHoursV1",
    "HourVerdictV1",
    "HourlyAcceptanceV1",
    "HourlySegmentAcceptanceError",
    "HourlySegmentV1",
    "ProvenRunV1",
    "SymbolHoursV1",
    "chain_proven_runs_v1",
    "classify_hour_v1",
    "derive_hourly_acceptance_v1",
]
