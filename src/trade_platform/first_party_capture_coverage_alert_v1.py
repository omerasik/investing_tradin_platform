"""Operational market-data coverage alert: measured holes per symbol and UTC hour.

OCI-1 2026-10-15: a 3 s connection reset read as "1010 s unproven" in the
health snapshot's hour facts, because acceptance asks whether *one* proven run
spans the hour (a reset ends the run by design). That question stays
:mod:`first_party_capture_hourly_acceptance_v1`'s. This module asks a different,
operational one: how much of the hour did *no* proven window of any session
cover? That is the time for which the archive holds no market data -- what an
operator must hear about when it is large (a reconnect loop, a stalled feed, a
recorder that is down).

Rules:

* Only COMPLETE partitions' declared coverage windows count (the caller passes
  :func:`~trade_platform.first_party_capture_hourly_acceptance_v1.proven_windows_between_v1`),
  so a running recorder, a PARTIAL partition or wall-clock time never covers
  anything. An hour with no window at all is uncovered in full.
* Windows of different sessions *are* merged here: two sessions observing
  adjacent stretches both hold data. Merging never feeds acceptance -- an hour
  this module reports as fully covered can still be not COMPLETE.
* The threshold is an explicit operator alert setting with no default. It
  decides whether someone is paged, never whether an hour is COMPLETE, and it
  is not an evidence or acceptance bound.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from trade_platform.first_party_capture_archive_v1 import ProvenWindowV1

HOUR_NANOS: Final = 3_600 * 1_000_000_000


class CoverageAlertError(ValueError):
    """Raised when the coverage question itself is malformed."""


@dataclass(frozen=True, slots=True)
class MeasuredHourCoverageV1:
    """One symbol's UTC hour: every ``[start, end)`` no proven window covers."""

    symbol: str
    hour_start_utc_nanos: int
    holes: tuple[tuple[int, int], ...]

    @property
    def uncovered_nanos(self) -> int:
        return sum(end - start for start, end in self.holes)


def measured_hour_coverage_v1(
    symbol: str, windows: Sequence[ProvenWindowV1], hour_start_utc_nanos: int
) -> MeasuredHourCoverageV1:
    """The holes in ``[hour, hour + 1 h)`` left by the union of ``windows``."""
    if hour_start_utc_nanos % HOUR_NANOS:
        raise CoverageAlertError("hour_start_is_not_on_the_utc_hour_grid")
    hour_end = hour_start_utc_nanos + HOUR_NANOS
    spans = sorted(
        (max(window.interval.start_utc_nanos, hour_start_utc_nanos),
         min(window.interval.end_utc_nanos, hour_end))
        for window in windows
        if window.interval.start_utc_nanos < hour_end
        and window.interval.end_utc_nanos > hour_start_utc_nanos
    )
    holes: list[tuple[int, int]] = []
    reach = hour_start_utc_nanos
    for start, end in spans:
        if start > reach:
            holes.append((reach, start))
        reach = max(reach, end)
    if reach < hour_end:
        holes.append((reach, hour_end))
    return MeasuredHourCoverageV1(symbol, hour_start_utc_nanos, tuple(holes))


def coverage_breaches_v1(
    hours: Sequence[MeasuredHourCoverageV1], *, max_uncovered_nanos: int
) -> tuple[MeasuredHourCoverageV1, ...]:
    """The symbol-hours whose measured holes exceed the operator's alert threshold."""
    if not 0 <= max_uncovered_nanos < HOUR_NANOS:
        raise CoverageAlertError("max_uncovered_must_be_within_one_hour")
    return tuple(hour for hour in hours if hour.uncovered_nanos > max_uncovered_nanos)


__all__ = [
    "CoverageAlertError",
    "MeasuredHourCoverageV1",
    "coverage_breaches_v1",
    "measured_hour_coverage_v1",
]
