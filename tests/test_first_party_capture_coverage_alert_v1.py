"""Measured coverage holes per symbol-hour: an operations alert, never acceptance."""

from __future__ import annotations

import unittest
from uuid import uuid4

from trade_platform.first_party_capture_archive_v1 import CaptureCoverageIntervalV1, ProvenWindowV1
from trade_platform.first_party_capture_coverage_alert_v1 import (
    HOUR_NANOS,
    CoverageAlertError,
    coverage_breaches_v1,
    measured_hour_coverage_v1,
)

SECOND = 1_000_000_000
HOUR = 1_792_026_000 * SECOND  # 2026-10-15T01:00:00Z


def window(start: int, last: int, proof: str = "OPERATOR_BOUNDED_STOP", session=None) -> ProvenWindowV1:
    return ProvenWindowV1(
        session_id=session or uuid4(),
        interval=CaptureCoverageIntervalV1(start, last, proof, 2),
    )


class MeasuredHourCoverageTests(unittest.TestCase):
    def test_a_reconnect_is_measured_as_its_real_hole_not_the_runs_head(self) -> None:
        """OCI-1 BTCUSDT 2026-10-15 01Z: reset at 01:16:47.214, resubscribed 01:16:50.247."""
        session = uuid4()
        windows = [
            window(HOUR - 3 * SECOND, HOUR + 5_663_710_259, session=uuid4()),  # previous segment overrun
            window(HOUR + 5_663_710_260, HOUR + 1_007_214_171_887, "CONNECTION_LOST", session),
            window(HOUR + 1_010_246_560_553, HOUR + HOUR_NANOS + 814_278_505, session=session),
        ]
        hour = measured_hour_coverage_v1("BTCUSDT", windows, HOUR)
        self.assertEqual(hour.holes, ((HOUR + 1_007_214_171_888, HOUR + 1_010_246_560_553),))
        self.assertAlmostEqual(hour.uncovered_nanos / SECOND, 3.032, places=3)

    def test_an_hour_without_any_proven_window_is_uncovered_in_full(self) -> None:
        hour = measured_hour_coverage_v1("SOLUSDT", [], HOUR)
        self.assertEqual(hour.holes, ((HOUR, HOUR + HOUR_NANOS),))
        self.assertEqual(hour.uncovered_nanos, HOUR_NANOS)

    def test_the_hand_off_head_and_a_stop_before_the_hour_end_are_holes(self) -> None:
        windows = [window(HOUR + 21 * SECOND, HOUR + 3_000 * SECOND - 1, "CONNECTION_LOST")]
        hour = measured_hour_coverage_v1("ETHUSDT", windows, HOUR)
        self.assertEqual(hour.holes, ((HOUR, HOUR + 21 * SECOND),
                                      (HOUR + 3_000 * SECOND, HOUR + HOUR_NANOS)))

    def test_overlapping_sessions_merge_and_windows_outside_the_hour_are_ignored(self) -> None:
        windows = [
            window(HOUR - HOUR_NANOS, HOUR - 1),
            window(HOUR, HOUR + 2_000 * SECOND),
            window(HOUR + 1_000 * SECOND, HOUR + HOUR_NANOS + SECOND),
            window(HOUR + 2 * HOUR_NANOS, HOUR + 3 * HOUR_NANOS),
        ]
        self.assertEqual(measured_hour_coverage_v1("BTCUSDT", windows, HOUR).holes, ())

    def test_the_hour_must_be_on_the_utc_grid(self) -> None:
        with self.assertRaises(CoverageAlertError):
            measured_hour_coverage_v1("BTCUSDT", [], HOUR + SECOND)


class CoverageBreachTests(unittest.TestCase):
    def test_only_hours_above_the_operator_threshold_breach(self) -> None:
        small = measured_hour_coverage_v1("BTCUSDT", [window(HOUR + 30 * SECOND, HOUR + HOUR_NANOS)], HOUR)
        large = measured_hour_coverage_v1("ETHUSDT", [window(HOUR + 61 * SECOND, HOUR + HOUR_NANOS)], HOUR)
        exact = measured_hour_coverage_v1("SOLUSDT", [window(HOUR + 60 * SECOND, HOUR + HOUR_NANOS)], HOUR)
        breaches = coverage_breaches_v1([small, large, exact], max_uncovered_nanos=60 * SECOND)
        self.assertEqual(breaches, (large,))

    def test_the_threshold_must_lie_within_one_hour(self) -> None:
        for bad in (-1, HOUR_NANOS):
            with self.assertRaises(CoverageAlertError):
                coverage_breaches_v1([], max_uncovered_nanos=bad)


if __name__ == "__main__":
    unittest.main()
