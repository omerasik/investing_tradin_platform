"""R1B hourly-segment acceptance: manifest-proven COMPLETE hours, never wall time."""

from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from trade_platform.first_party_capture_archive_v1 import (
    END_PROOF_CONNECTION_LOST,
    END_PROOF_OPERATOR_BOUNDED_STOP,
    END_PROOF_UTC_DAY_ROLLOVER,
    CaptureCoverageIntervalV1,
    ProvenWindowV1,
)
from trade_platform.first_party_capture_hourly_acceptance_v1 import (
    HOUR_NANOS,
    HourlySegmentAcceptanceError,
    HourVerdictV1,
    ProvenRunV1,
    chain_proven_runs_v1,
    classify_hour_v1,
    derive_hourly_acceptance_v1,
)

SECOND = 1_000_000_000
#: 2026-10-09T00:00:00Z, a past UTC midnight.
DAY = 1_791_504_000 * SECOND
HEAD = 30 * SECOND


def window(
    start: int, last: int, proof: str = END_PROOF_OPERATOR_BOUNDED_STOP, session: UUID | None = None
) -> ProvenWindowV1:
    return ProvenWindowV1(
        session_id=uuid4() if session is None else session,
        interval=CaptureCoverageIntervalV1(
            start_utc_nanos=start, last_proven_utc_nanos=last, end_proof=proof, record_count=1
        ),
    )


def segment(hour: int, head_seconds: int = 5) -> ProvenWindowV1:
    """A bounded hourly segment for hour index ``hour`` after DAY, ending past the grid."""
    start = DAY + hour * HOUR_NANOS
    return window(start + head_seconds * SECOND, start + HOUR_NANOS + SECOND // 2)


class ChainRunsTests(unittest.TestCase):
    def test_same_session_day_rollover_is_one_run(self) -> None:
        session = uuid4()
        runs = chain_proven_runs_v1(
            [
                window(DAY - HOUR_NANOS + 4 * SECOND, DAY - 1, END_PROOF_UTC_DAY_ROLLOVER, session),
                window(DAY + SECOND // 10, DAY + SECOND, session=session),
            ]
        )
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].rollover_links, 1)
        self.assertEqual(runs[0].end_proof, END_PROOF_OPERATOR_BOUNDED_STOP)

    def test_other_end_proofs_and_other_sessions_never_join(self) -> None:
        session = uuid4()
        runs = chain_proven_runs_v1(
            [
                window(DAY, DAY + 10 * SECOND, END_PROOF_CONNECTION_LOST, session),
                window(DAY + 11 * SECOND, DAY + 20 * SECOND, session=session),
                window(DAY + 20 * SECOND + 1, DAY + 30 * SECOND),
            ]
        )
        self.assertEqual(len(runs), 3)

    def test_rollover_never_bridges_a_missing_day(self) -> None:
        # The day-0 partition of a long-lived session is missing (deleted,
        # unmanifested or failed --verify): its day must not be bridged.
        session = uuid4()
        windows = [
            window(DAY - HOUR_NANOS, DAY - 1, END_PROOF_UTC_DAY_ROLLOVER, session),
            window(DAY + 24 * HOUR_NANOS, DAY + 25 * HOUR_NANOS, session=session),
        ]
        self.assertEqual(len(chain_proven_runs_v1(windows)), 2)
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", windows)], max_head_unproven_nanos=0, required_consecutive_hours=24
        )
        self.assertFalse(acceptance.met)
        self.assertEqual(acceptance.longest_run.hours if acceptance.longest_run else 0, 1)

    def test_rollover_into_another_session_does_not_join(self) -> None:
        runs = chain_proven_runs_v1(
            [
                window(DAY - 10 * SECOND, DAY - 1, END_PROOF_UTC_DAY_ROLLOVER),
                window(DAY, DAY + 10 * SECOND),
            ]
        )
        self.assertEqual(len(runs), 2)


class ClassifyHourTests(unittest.TestCase):
    def runs(self, *windows: ProvenWindowV1) -> tuple[ProvenRunV1, ...]:
        return chain_proven_runs_v1(windows)

    def test_bounded_segment_with_short_head_is_complete(self) -> None:
        verdict = classify_hour_v1(self.runs(segment(0)), DAY, max_head_unproven_nanos=HEAD)
        self.assertIs(verdict.verdict, HourVerdictV1.COMPLETE)
        self.assertEqual(verdict.head_unproven_nanos, 5 * SECOND)

    def test_head_longer_than_the_bound_is_not_complete(self) -> None:
        verdict = classify_hour_v1(
            self.runs(segment(0, head_seconds=31)), DAY, max_head_unproven_nanos=HEAD
        )
        self.assertIs(verdict.verdict, HourVerdictV1.HEAD_UNPROVEN_EXCEEDS_BOUND)
        self.assertEqual(verdict.head_unproven_nanos, 31 * SECOND)

    def test_head_exactly_at_the_bound_is_complete(self) -> None:
        verdict = classify_hour_v1(
            self.runs(segment(0, head_seconds=30)), DAY, max_head_unproven_nanos=HEAD
        )
        self.assertIs(verdict.verdict, HourVerdictV1.COMPLETE)

    def test_coverage_that_stops_inside_the_hour_is_not_complete(self) -> None:
        lost = window(DAY + 5 * SECOND, DAY + 1_800 * SECOND, END_PROOF_CONNECTION_LOST)
        verdict = classify_hour_v1(self.runs(lost), DAY, max_head_unproven_nanos=HEAD)
        self.assertIs(verdict.verdict, HourVerdictV1.COVERAGE_ENDS_BEFORE_HOUR_END)
        self.assertEqual(verdict.detail, END_PROOF_CONNECTION_LOST)

    def test_exclusive_end_must_reach_the_hour_end(self) -> None:
        short = window(DAY + SECOND, DAY + HOUR_NANOS - 2)
        exact = window(DAY + SECOND, DAY + HOUR_NANOS - 1)
        self.assertFalse(classify_hour_v1(self.runs(short), DAY, max_head_unproven_nanos=HEAD).complete)
        self.assertTrue(classify_hour_v1(self.runs(exact), DAY, max_head_unproven_nanos=HEAD).complete)

    def test_two_sessions_never_combine_into_one_complete_hour(self) -> None:
        first = window(DAY + SECOND, DAY + 1_800 * SECOND)
        second = window(DAY + 1_800 * SECOND + 1, DAY + HOUR_NANOS + SECOND)
        verdict = classify_hour_v1(self.runs(first, second), DAY, max_head_unproven_nanos=HEAD)
        self.assertFalse(verdict.complete)

    def test_no_window_is_no_proven_coverage(self) -> None:
        verdict = classify_hour_v1((), DAY, max_head_unproven_nanos=HEAD)
        self.assertIs(verdict.verdict, HourVerdictV1.NO_PROVEN_COVERAGE)

    def test_a_long_session_proves_each_hour_it_spans(self) -> None:
        runs = self.runs(window(DAY + SECOND, DAY + 3 * HOUR_NANOS))
        verdicts = [
            classify_hour_v1(runs, DAY + index * HOUR_NANOS, max_head_unproven_nanos=HEAD)
            for index in range(3)
        ]
        self.assertTrue(all(item.complete for item in verdicts))
        self.assertEqual(verdicts[1].head_unproven_nanos, 0)

    def test_hour_off_the_grid_is_rejected(self) -> None:
        with self.assertRaises(HourlySegmentAcceptanceError):
            classify_hour_v1((), DAY + SECOND, max_head_unproven_nanos=HEAD)


class JointAcceptanceTests(unittest.TestCase):
    def test_joint_run_requires_every_symbol(self) -> None:
        full = [segment(hour) for hour in range(4)]
        holed = [segment(hour) for hour in (0, 1, 3)]
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", full), ("ETHUSDT", holed)],
            max_head_unproven_nanos=HEAD,
            required_consecutive_hours=3,
        )
        self.assertEqual(acceptance.hours_judged, 4)
        self.assertEqual(acceptance.joint_complete, (True, True, False, True))
        self.assertEqual([run.hours for run in acceptance.runs], [2, 1])
        self.assertEqual(acceptance.current_run_hours, 1)
        self.assertFalse(acceptance.met)

    def test_met_when_the_longest_joint_run_reaches_the_requirement(self) -> None:
        hours = [segment(hour) for hour in range(3)]
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", hours), ("ETHUSDT", list(hours))],
            max_head_unproven_nanos=HEAD,
            required_consecutive_hours=3,
        )
        self.assertTrue(acceptance.met)
        self.assertEqual(acceptance.current_run_hours, 3)
        self.assertEqual(acceptance.max_head_unproven_observed_nanos, 5 * SECOND)

    def test_a_symbol_without_evidence_blocks_every_hour(self) -> None:
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", [segment(0), segment(1)]), ("SOLUSDT", [])],
            max_head_unproven_nanos=HEAD,
            required_consecutive_hours=1,
        )
        self.assertEqual(acceptance.joint_complete, (False, False))
        self.assertFalse(acceptance.met)

    def test_the_open_hour_is_never_judged(self) -> None:
        open_hour = window(DAY + HOUR_NANOS + 5 * SECOND, DAY + HOUR_NANOS + 600 * SECOND)
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", [segment(0), open_hour])],
            max_head_unproven_nanos=HEAD,
            required_consecutive_hours=1,
        )
        self.assertEqual(acceptance.hours_judged, 1)

    def test_malformed_questions_fail_closed(self) -> None:
        cases = [
            ([], HEAD, 24),
            ([("BTCUSDT", []), ("BTCUSDT", [])], HEAD, 24),
            ([("BTCUSDT", [])], HOUR_NANOS, 24),
            ([("BTCUSDT", [])], -1, 24),
            ([("BTCUSDT", [])], HEAD, 0),
        ]
        for symbols, head, required in cases:
            with (
                self.subTest(symbols=symbols, head=head, required=required),
                self.assertRaises(HourlySegmentAcceptanceError),
            ):
                derive_hourly_acceptance_v1(
                    symbols, max_head_unproven_nanos=head, required_consecutive_hours=required
                )

    def test_no_evidence_is_not_met(self) -> None:
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", [])], max_head_unproven_nanos=HEAD, required_consecutive_hours=1
        )
        self.assertIsNone(acceptance.first_hour_start_utc_nanos)
        self.assertFalse(acceptance.met)



if __name__ == "__main__":
    unittest.main()
