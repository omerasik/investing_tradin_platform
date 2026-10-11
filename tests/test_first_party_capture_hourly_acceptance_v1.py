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
            [("BTCUSDT", windows)], max_unproven_nanos=0, required_consecutive_hours=24
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


MS = 1_000_000


def rollover_segment(session: UUID, hour: int, *, before_ns: int, after_ns: int) -> list[ProvenWindowV1]:
    """One hourly segment that crosses the midnight at ``DAY + hour h``, as the recorder writes it.

    The last record of the old day lands ``before_ns`` before midnight (end proof
    UTC_DAY_ROLLOVER), the first record of the new day ``after_ns`` after it.
    """
    midnight = DAY + hour * HOUR_NANOS
    return [
        window(midnight - HOUR_NANOS + 5 * SECOND, midnight - before_ns, END_PROOF_UTC_DAY_ROLLOVER, session),
        window(midnight + after_ns, midnight + SECOND // 2, session=session),
    ]


class RolloverGapTests(unittest.TestCase):
    """A rollover link keeps both intervals; the time between them is never proven."""

    def test_same_session_next_day_window_hours_later_proves_none_of_the_gap(self) -> None:
        # Counterexample: session X proves through midnight, then its next
        # verified window starts at 10:00 the next day. Same session, both valid.
        session = uuid4()
        windows = [
            window(DAY - HOUR_NANOS + 5 * SECOND, DAY - 1, END_PROOF_UTC_DAY_ROLLOVER, session),
            window(DAY + 10 * HOUR_NANOS, DAY + 11 * HOUR_NANOS + SECOND // 2, session=session),
        ]
        runs = chain_proven_runs_v1(windows)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].rollover_links, 1)
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", windows)], max_unproven_nanos=HEAD, required_consecutive_hours=2
        )
        verdicts = [segment.verdict for segment in acceptance.symbols[0].segments]
        self.assertEqual(acceptance.hours_judged, 12)
        self.assertIs(verdicts[0], HourVerdictV1.COMPLETE)  # 23:00, proven to midnight
        self.assertEqual(verdicts[1:11], [HourVerdictV1.NO_PROVEN_COVERAGE] * 10)  # 00:00..09:00
        self.assertIs(verdicts[11], HourVerdictV1.COMPLETE)  # 10:00
        self.assertEqual([run.hours for run in acceptance.runs], [1, 1])
        self.assertFalse(acceptance.met)

    def test_reported_unproven_is_the_actual_missing_interval(self) -> None:
        # The run began in the previous hour, so the old head-only measure said 0 s.
        session = uuid4()
        runs = chain_proven_runs_v1([
            window(DAY - HOUR_NANOS + 5 * SECOND, DAY - 1, END_PROOF_UTC_DAY_ROLLOVER, session),
            window(DAY + 600 * SECOND, DAY + 2 * HOUR_NANOS, session=session),
        ])
        verdict = classify_hour_v1(runs, DAY, max_unproven_nanos=HEAD)
        self.assertIs(verdict.verdict, HourVerdictV1.UNPROVEN_GAP_EXCEEDS_BOUND)
        self.assertEqual(verdict.unproven_nanos, 600 * SECOND)
        self.assertTrue(classify_hour_v1(runs, DAY + HOUR_NANOS, max_unproven_nanos=HEAD).complete)

    def test_missing_or_replay_rejected_middle_partition_leaves_its_day_unproven(self) -> None:
        # Day 0 of a three-day session has no surviving window (deleted,
        # unmanifested, or rejected by --verify): its rollover link must not
        # chain into day +1, and none of day 0 may count.
        session = uuid4()
        windows = [
            window(DAY - 2 * HOUR_NANOS, DAY - 1, END_PROOF_UTC_DAY_ROLLOVER, session),
            window(DAY + 24 * HOUR_NANOS, DAY + 26 * HOUR_NANOS, session=session),
        ]
        self.assertEqual(len(chain_proven_runs_v1(windows)), 2)
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", windows)], max_unproven_nanos=HEAD, required_consecutive_hours=3
        )
        day_zero = acceptance.symbols[0].segments[2:26]
        self.assertTrue(all(item.verdict is HourVerdictV1.NO_PROVEN_COVERAGE for item in day_zero))
        self.assertEqual([run.hours for run in acceptance.runs], [2, 2])
        self.assertFalse(acceptance.met)

    def test_legitimate_midnight_hand_off_is_complete_and_reported(self) -> None:
        session = uuid4()
        windows = [
            *rollover_segment(session, 0, before_ns=MS, after_ns=2 * MS),
            segment(0),
        ]
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", windows)], max_unproven_nanos=HEAD, required_consecutive_hours=2
        )
        before, after = acceptance.symbols[0].segments
        self.assertTrue(before.complete and after.complete)
        # 23:00: 5 s head plus the sub-millisecond tail before midnight.
        self.assertEqual(before.unproven_nanos, 5 * SECOND + MS - 1)
        self.assertEqual(before.session_id, session)
        self.assertEqual(after.unproven_nanos, 5 * SECOND)
        self.assertTrue(acceptance.met)

    def test_one_bound_covers_both_sides_of_a_midnight_hand_off(self) -> None:
        session = uuid4()
        bound = 15 * SECOND
        # Last old-day record 10 s before midnight, first new-day record 20 s after.
        windows = [
            window(DAY - HOUR_NANOS + 5 * SECOND, DAY - 10 * SECOND - 1, END_PROOF_UTC_DAY_ROLLOVER, session),
            window(DAY + 20 * SECOND, DAY + HOUR_NANOS, session=session),
        ]
        runs = chain_proven_runs_v1(windows)
        before = classify_hour_v1(runs, DAY - HOUR_NANOS, max_unproven_nanos=bound)
        after = classify_hour_v1(runs, DAY, max_unproven_nanos=bound)
        self.assertTrue(before.complete)  # 5 s head + 10 s tail = 15 s, at the bound
        self.assertEqual(before.unproven_nanos, 15 * SECOND)
        self.assertIs(after.verdict, HourVerdictV1.UNPROVEN_GAP_EXCEEDS_BOUND)
        self.assertEqual(after.unproven_nanos, 20 * SECOND)
        self.assertTrue(classify_hour_v1(runs, DAY, max_unproven_nanos=20 * SECOND).complete)

    def test_real_hourly_universe_format_is_unchanged(self) -> None:
        # Hourly bounded segments (5 s head, 0.5 s past the grid) for 26 hours,
        # one of them rotating its partition at midnight, for all three symbols.
        def day_of_segments() -> list[ProvenWindowV1]:
            windows = [segment(hour) for hour in range(-2, 24) if hour != -1]
            windows.extend(rollover_segment(uuid4(), 0, before_ns=MS, after_ns=MS))
            return windows

        acceptance = derive_hourly_acceptance_v1(
            [(symbol, day_of_segments()) for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")],
            max_unproven_nanos=HEAD,
            required_consecutive_hours=24,
        )
        self.assertEqual(acceptance.hours_judged, 26)
        self.assertEqual(acceptance.joint_complete, (True,) * 26)
        self.assertTrue(acceptance.met)
        self.assertEqual(acceptance.max_unproven_observed_nanos, 5 * SECOND + MS - 1)


class ClassifyHourTests(unittest.TestCase):
    def runs(self, *windows: ProvenWindowV1) -> tuple[ProvenRunV1, ...]:
        return chain_proven_runs_v1(windows)

    def test_bounded_segment_with_short_head_is_complete(self) -> None:
        verdict = classify_hour_v1(self.runs(segment(0)), DAY, max_unproven_nanos=HEAD)
        self.assertIs(verdict.verdict, HourVerdictV1.COMPLETE)
        self.assertEqual(verdict.unproven_nanos, 5 * SECOND)

    def test_head_longer_than_the_bound_is_not_complete(self) -> None:
        verdict = classify_hour_v1(
            self.runs(segment(0, head_seconds=31)), DAY, max_unproven_nanos=HEAD
        )
        self.assertIs(verdict.verdict, HourVerdictV1.HEAD_UNPROVEN_EXCEEDS_BOUND)
        self.assertEqual(verdict.unproven_nanos, 31 * SECOND)

    def test_head_exactly_at_the_bound_is_complete(self) -> None:
        verdict = classify_hour_v1(
            self.runs(segment(0, head_seconds=30)), DAY, max_unproven_nanos=HEAD
        )
        self.assertIs(verdict.verdict, HourVerdictV1.COMPLETE)

    def test_coverage_that_stops_inside_the_hour_is_not_complete(self) -> None:
        lost = window(DAY + 5 * SECOND, DAY + 1_800 * SECOND, END_PROOF_CONNECTION_LOST)
        verdict = classify_hour_v1(self.runs(lost), DAY, max_unproven_nanos=HEAD)
        self.assertIs(verdict.verdict, HourVerdictV1.COVERAGE_ENDS_BEFORE_HOUR_END)
        self.assertEqual(verdict.detail, END_PROOF_CONNECTION_LOST)

    def test_exclusive_end_must_reach_the_hour_end(self) -> None:
        short = window(DAY + SECOND, DAY + HOUR_NANOS - 2)
        exact = window(DAY + SECOND, DAY + HOUR_NANOS - 1)
        self.assertFalse(classify_hour_v1(self.runs(short), DAY, max_unproven_nanos=HEAD).complete)
        self.assertTrue(classify_hour_v1(self.runs(exact), DAY, max_unproven_nanos=HEAD).complete)

    def test_two_sessions_never_combine_into_one_complete_hour(self) -> None:
        first = window(DAY + SECOND, DAY + 1_800 * SECOND)
        second = window(DAY + 1_800 * SECOND + 1, DAY + HOUR_NANOS + SECOND)
        verdict = classify_hour_v1(self.runs(first, second), DAY, max_unproven_nanos=HEAD)
        self.assertFalse(verdict.complete)

    def test_no_window_is_no_proven_coverage(self) -> None:
        verdict = classify_hour_v1((), DAY, max_unproven_nanos=HEAD)
        self.assertIs(verdict.verdict, HourVerdictV1.NO_PROVEN_COVERAGE)

    def test_a_long_session_proves_each_hour_it_spans(self) -> None:
        runs = self.runs(window(DAY + SECOND, DAY + 3 * HOUR_NANOS))
        verdicts = [
            classify_hour_v1(runs, DAY + index * HOUR_NANOS, max_unproven_nanos=HEAD)
            for index in range(3)
        ]
        self.assertTrue(all(item.complete for item in verdicts))
        self.assertEqual(verdicts[1].unproven_nanos, 0)

    def test_hour_off_the_grid_is_rejected(self) -> None:
        with self.assertRaises(HourlySegmentAcceptanceError):
            classify_hour_v1((), DAY + SECOND, max_unproven_nanos=HEAD)


class JointAcceptanceTests(unittest.TestCase):
    def test_joint_run_requires_every_symbol(self) -> None:
        full = [segment(hour) for hour in range(4)]
        holed = [segment(hour) for hour in (0, 1, 3)]
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", full), ("ETHUSDT", holed)],
            max_unproven_nanos=HEAD,
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
            max_unproven_nanos=HEAD,
            required_consecutive_hours=3,
        )
        self.assertTrue(acceptance.met)
        self.assertEqual(acceptance.current_run_hours, 3)
        self.assertEqual(acceptance.max_unproven_observed_nanos, 5 * SECOND)

    def test_a_symbol_without_evidence_blocks_every_hour(self) -> None:
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", [segment(0), segment(1)]), ("SOLUSDT", [])],
            max_unproven_nanos=HEAD,
            required_consecutive_hours=1,
        )
        self.assertEqual(acceptance.joint_complete, (False, False))
        self.assertFalse(acceptance.met)

    def test_the_open_hour_is_never_judged(self) -> None:
        open_hour = window(DAY + HOUR_NANOS + 5 * SECOND, DAY + HOUR_NANOS + 600 * SECOND)
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", [segment(0), open_hour])],
            max_unproven_nanos=HEAD,
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
                    symbols, max_unproven_nanos=head, required_consecutive_hours=required
                )

    def test_no_evidence_is_not_met(self) -> None:
        acceptance = derive_hourly_acceptance_v1(
            [("BTCUSDT", [])], max_unproven_nanos=HEAD, required_consecutive_hours=1
        )
        self.assertIsNone(acceptance.first_hour_start_utc_nanos)
        self.assertFalse(acceptance.met)



class VerifiedWindowsTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        from trade_platform.first_party_capture_authority_v1 import (
            first_party_bybit_universe_contracts_v1,
        )

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "archive"
        self.btc, self.eth, _ = first_party_bybit_universe_contracts_v1()

    def test_only_fully_replayed_partitions_of_the_source_contribute(self) -> None:
        from tests.test_first_party_capture_archive_v1 import _complete_partition
        from trade_platform.first_party_capture_hourly_acceptance_v1 import verified_windows_v1

        _complete_partition(self.root, contract=self.btc, count=3)
        _complete_partition(self.root, contract=self.eth, count=2, first=10)
        verified = verified_windows_v1(self.root, self.btc)
        self.assertEqual(verified.verified_partitions, 1)
        self.assertEqual(verified.failed, ())
        self.assertEqual(len(verified.windows), 1)
        self.assertEqual(verified.windows[0].interval.record_count, 3)

    def test_a_partition_that_fails_replay_proves_nothing_and_is_named(self) -> None:
        from tests.test_first_party_capture_archive_v1 import (
            _complete_partition,
            _rewrite_manifest,
        )
        from trade_platform.first_party_capture_archive_v1 import derive_archive_availability_v1
        from trade_platform.first_party_capture_hourly_acceptance_v1 import verified_windows_v1

        directory = _complete_partition(self.root, contract=self.btc, count=3)

        def overclaim(manifest: dict) -> None:
            manifest["record_count"] += 1
            manifest["coverage"][0]["record_count"] += 1

        _rewrite_manifest(directory, overclaim)
        # Hash-consistent forged claims pass the manifest-level read ...
        self.assertEqual(
            len(derive_archive_availability_v1(self.root, contract=self.btc).windows), 1
        )
        # ... but not a full replay, so the partition proves nothing here.
        verified = verified_windows_v1(self.root, self.btc)
        self.assertEqual(verified.windows, ())
        self.assertEqual(verified.verified_partitions, 0)
        self.assertEqual(len(verified.failed), 1)
        self.assertEqual(verified.failed[0][0], directory)
        self.assertIn("count", verified.failed[0][1])

    def test_a_replay_rejected_middle_day_is_never_bridged(self) -> None:
        from datetime import UTC, date, datetime

        from tests.test_first_party_capture_archive_v1 import (
            _measurement_payload,
            _rewrite_manifest,
        )
        from trade_platform.first_party_capture_archive_v1 import (
            CaptureClockReadingV1,
            CapturePartitionWriterV1,
            build_capture_record_v1,
        )
        from trade_platform.first_party_capture_hourly_acceptance_v1 import verified_windows_v1

        # One session recording across two midnights: three partitions linked
        # by UTC_DAY_ROLLOVER, sequence continuous, as the recorder writes them.
        session = uuid4()
        midnight = int(datetime(2026, 9, 23, tzinfo=UTC).timestamp()) * SECOND
        days = (date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24))
        arrivals = (
            (midnight - HOUR_NANOS + SECOND, midnight - MS),
            (midnight + MS, midnight + 24 * HOUR_NANOS - MS),
            (midnight + 24 * HOUR_NANOS + MS, midnight + 25 * HOUR_NANOS),
        )
        proofs = (END_PROOF_UTC_DAY_ROLLOVER, END_PROOF_UTC_DAY_ROLLOVER, END_PROOF_OPERATOR_BOUNDED_STOP)
        payload = _measurement_payload(self.btc.exchange_symbol)
        directories = []
        sequence = 0
        for day, times, proof in zip(days, arrivals, proofs, strict=True):
            writer = CapturePartitionWriterV1(
                root=self.root, contract=self.btc, session_id=session, day=day,
                clock_resolution_nanos=15_625_000,
            )
            for arrival in times:
                writer.append_record(build_capture_record_v1(
                    contract=self.btc, session_id=session, sequence=sequence,
                    clock=CaptureClockReadingV1(
                        arrival_utc_nanos=arrival, arrival_monotonic_nanos=arrival - midnight + 10**15
                    ),
                    payload_text=payload,
                ))
                sequence += 1
            writer.declare_coverage(CaptureCoverageIntervalV1(
                start_utc_nanos=times[0], last_proven_utc_nanos=times[1], end_proof=proof, record_count=2
            ))
            directories.append(writer.finalize().parent)

        def judge() -> tuple[int, object]:
            verified = verified_windows_v1(self.root, self.btc)
            return len(verified.failed), derive_hourly_acceptance_v1(
                [("BTCUSDT", verified.windows)], max_unproven_nanos=HEAD, required_consecutive_hours=24
            )

        failed, intact = judge()
        self.assertEqual(failed, 0)
        self.assertEqual([run.hours for run in intact.runs], [26])
        self.assertTrue(intact.met)

        def overclaim(manifest: dict) -> None:
            manifest["record_count"] += 1
            manifest["coverage"][0]["record_count"] += 1

        _rewrite_manifest(directories[1], overclaim)
        failed, forged = judge()
        self.assertEqual(failed, 1)
        middle_day = forged.symbols[0].segments[1:25]
        self.assertTrue(all(item.verdict is HourVerdictV1.NO_PROVEN_COVERAGE for item in middle_day))
        # 23:00 now ends 1 ms short of midnight with nothing after it: not proven either.
        self.assertIs(forged.symbols[0].segments[0].verdict, HourVerdictV1.COVERAGE_ENDS_BEFORE_HOUR_END)
        self.assertEqual([run.hours for run in forged.runs], [1])
        self.assertFalse(forged.met)


if __name__ == "__main__":
    unittest.main()
