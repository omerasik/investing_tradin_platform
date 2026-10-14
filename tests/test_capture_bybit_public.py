"""Unit coverage for the capture operator CLI's segmented supervise mode (R0)."""

from __future__ import annotations

import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "capture_bybit_public_cli", ROOT / "scripts" / "capture_bybit_public.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("capture_cli_module_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLI = _load_cli()
HOUR = 3600.0
#: 2026-10-02T14:35:05Z
NOW = 1_790_951_705.0


def _result(end_proof: str) -> SimpleNamespace:
    return SimpleNamespace(
        sessions=(), end_proof=end_proof, restarts=0, clock_samples=(),
        clock_sample_failures=0, failures={}, compactions=(), compaction_failures=(),
    )


class SegmentBoundTests(unittest.TestCase):
    def test_bound_ends_on_the_next_utc_grid_line(self) -> None:
        self.assertAlmostEqual(CLI.segment_bound_seconds(NOW, HOUR), 3600.0 - 2105.0)

    def test_a_sliver_remainder_folds_into_the_following_segment(self) -> None:
        just_before_the_hour = NOW - 2105.0 + 3600.0 - 30.0
        self.assertAlmostEqual(CLI.segment_bound_seconds(just_before_the_hour, HOUR), 3630.0)

    def test_on_the_grid_line_a_whole_segment_follows(self) -> None:
        self.assertAlmostEqual(CLI.segment_bound_seconds(NOW - 2105.0, HOUR), HOUR)

    def test_a_segment_shorter_than_the_minimum_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            CLI.segment_bound_seconds(NOW, 59.0)


class SegmentedSuperviseTests(unittest.TestCase):
    def _supervise(self, argv: list[str], results: list[SimpleNamespace]) -> tuple[int, list]:
        calls: list = []

        def fleet(contracts, **kwargs):
            calls.append(kwargs)
            return results[len(calls) - 1]

        import tempfile

        with (
            tempfile.TemporaryDirectory() as root,  # the recorder lock lives at the root
            patch.object(CLI, "run_capture_fleet_v1", side_effect=fleet),
            patch.object(CLI, "request_keep_awake_v1", return_value=True),
            patch.object(CLI.time, "time", return_value=NOW),
            redirect_stdout(io.StringIO()),
        ):
            code = CLI.main(["--root", root, "supervise", *argv])
        return code, calls

    def test_bounded_segments_repeat_until_the_disk_floor_stops_capture(self) -> None:
        code, calls = self._supervise(
            ["--segment-seconds", "3600"],
            [
                _result(CLI.END_PROOF_OPERATOR_BOUNDED_STOP),
                _result(CLI.END_PROOF_OPERATOR_BOUNDED_STOP),
                _result(CLI.END_PROOF_DISK_BUDGET_STOP),
            ],
        )
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call["max_seconds"] == 1495.0 for call in calls))
        # The next segment starts at once; compressing the last hour is its job.
        self.assertTrue(all(call["compact_after_bounded_stop"] is False for call in calls))

    def test_any_other_end_proof_ends_the_segmented_loop(self) -> None:
        code, calls = self._supervise(
            ["--segment-seconds", "3600"],
            [_result(CLI.END_PROOF_OPERATOR_BOUNDED_STOP), _result("OPERATOR_INTERRUPT")],
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)

    def test_without_segments_supervise_runs_one_unbounded_session(self) -> None:
        code, calls = self._supervise([], [_result(CLI.END_PROOF_OPERATOR_BOUNDED_STOP)])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["max_seconds"])
        self.assertTrue(calls[0]["compact_after_bounded_stop"])


class UniverseRecorderTests(unittest.TestCase):
    """R1B: the OR-2 universe records exactly its three contracts, hourly by default."""

    def test_universe_records_the_owner_decided_contracts_in_hourly_segments(self) -> None:
        import tempfile

        from trade_platform.first_party_capture_authority_v1 import (
            first_party_bybit_universe_contracts_v1,
        )

        seen: list = []

        def fleet(contracts, **kwargs):
            seen.append((contracts, kwargs))
            return _result(CLI.END_PROOF_DISK_BUDGET_STOP if seen[1:] else CLI.END_PROOF_OPERATOR_BOUNDED_STOP)

        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(CLI, "run_capture_fleet_v1", side_effect=fleet),
            patch.object(CLI, "request_keep_awake_v1", return_value=True),
            patch.object(CLI.time, "time", return_value=NOW),
            redirect_stdout(io.StringIO()),
        ):
            code = CLI.main(["--root", root, "universe"])
        self.assertEqual(code, 2)
        self.assertEqual(2, len(seen))
        for contracts, kwargs in seen:
            self.assertEqual(first_party_bybit_universe_contracts_v1(), contracts)
            self.assertEqual(1495.0, kwargs["max_seconds"])
            self.assertEqual(Path(root), kwargs["archive_root"])
            self.assertFalse(kwargs["compact_after_bounded_stop"])

    def test_the_universe_root_is_its_own_and_lock_guarded(self) -> None:
        import tempfile

        from trade_platform.single_instance_lock_v1 import exclusive_instance_lock_v1

        args = SimpleNamespace(root=None)
        self.assertEqual("capture-universe-r1b", CLI._universe_root(args).name)
        self.assertNotEqual(CLI._root(args), CLI._universe_root(args))
        self.assertNotEqual(CLI._measurement_root(args), CLI._universe_root(args))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with exclusive_instance_lock_v1(root, CLI.RECORDER_LOCK_NAME, description="first"), \
                    patch.object(CLI, "run_capture_fleet_v1") as fleet, redirect_stdout(io.StringIO()):
                code = CLI.main(["--root", str(root), "universe"])
            self.assertEqual(code, CLI.EXIT_ALREADY_RUNNING)
            fleet.assert_not_called()


class SingleRecorderTests(unittest.TestCase):
    def test_a_second_recorder_on_a_held_root_is_refused_before_recording(self) -> None:
        import tempfile

        from trade_platform.single_instance_lock_v1 import exclusive_instance_lock_v1

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with exclusive_instance_lock_v1(root, CLI.RECORDER_LOCK_NAME, description="first"), \
                    patch.object(CLI, "run_capture_fleet_v1") as fleet:
                out = io.StringIO()
                with redirect_stdout(out):
                    code = CLI.main(["--root", str(root), "supervise", "--segment-seconds", "3600"])
            self.assertEqual(code, CLI.EXIT_ALREADY_RUNNING)
            self.assertIn("already running", out.getvalue())
            fleet.assert_not_called()

    def test_status_reports_a_held_root(self) -> None:
        import tempfile

        from trade_platform.single_instance_lock_v1 import exclusive_instance_lock_v1

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with exclusive_instance_lock_v1(root, CLI.RECORDER_LOCK_NAME, description="r0"):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(CLI.main(["--root", str(root), "status"]), 0)
            self.assertIn("production   RUNNING", out.getvalue())


class UniverseAcceptanceCommandTests(unittest.TestCase):
    def test_the_head_bound_has_no_default(self) -> None:
        from contextlib import redirect_stderr

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            CLI.main(["universe-acceptance"])

    def test_an_empty_universe_root_is_not_met(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            out = io.StringIO()
            with redirect_stdout(out):
                code = CLI.main(
                    ["--root", directory, "universe-acceptance", "--max-unproven-seconds", "30"]
                )
        self.assertEqual(code, 1)
        self.assertIn("no proven coverage", out.getvalue())

    def test_a_proven_joint_run_is_reported_per_symbol(self) -> None:
        import tempfile
        from uuid import uuid4

        from trade_platform.first_party_capture_archive_v1 import (
            CaptureArchiveAvailabilityV1,
            CaptureCoverageIntervalV1,
            ProvenWindowV1,
        )
        from trade_platform.first_party_capture_hourly_acceptance_v1 import HOUR_NANOS

        hour = 1_791_504_000 * 1_000_000_000
        proven = CaptureArchiveAvailabilityV1(
            windows=(
                ProvenWindowV1(
                    session_id=uuid4(),
                    interval=CaptureCoverageIntervalV1(
                        start_utc_nanos=hour + 5_000_000_000,
                        last_proven_utc_nanos=hour + HOUR_NANOS,
                        end_proof="OPERATOR_BOUNDED_STOP",
                        record_count=1,
                    ),
                ),
            ),
            gaps=(),
            excluded=(),
        )
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(CLI, "derive_archive_availability_v1", return_value=proven):
            out = io.StringIO()
            with redirect_stdout(out):
                code = CLI.main(
                    [
                        "--root", directory, "universe-acceptance",
                        "--max-unproven-seconds", "30", "--required-hours", "1",
                    ]
                )
        self.assertEqual(code, 0)
        text = out.getvalue()
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            self.assertIn(f"== {symbol}: 1 COMPLETE hour(s)", text)
        self.assertIn("acceptance     MET", text)

    def test_a_stopped_run_is_reported_as_history_not_as_the_current_run(self) -> None:
        """2026-10-11T19:59Z: the CLI said "current run 9 h" 41 h after the 02:19Z loss."""
        import tempfile

        from tests.test_first_party_capture_hourly_acceptance_v1 import at, incident_windows
        from trade_platform.first_party_capture_archive_v1 import CaptureArchiveAvailabilityV1

        (_, windows), *_ = incident_windows()
        proven = CaptureArchiveAvailabilityV1(windows=tuple(windows), gaps=(), excluded=())
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(CLI, "derive_archive_availability_v1", return_value=proven), \
                patch.object(CLI, "_utc_now_nanos", return_value=at("2026-10-11T19:59:09+00:00")):
            out = io.StringIO()
            with redirect_stdout(out):
                code = CLI.main(
                    ["--root", directory, "universe-acceptance", "--max-unproven-seconds", "60"]
                )
        self.assertEqual(code, 1)
        text = out.getvalue()
        self.assertIn("longest run    9 h (historical, proven): 2026-10-09T17:00:00+00:00", text)
        self.assertIn("last judged    hour ending 2026-10-10T02:00:00+00:00: run 9 h ending there", text)
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            self.assertIn(f"INTERRUPTED    {symbol} evidence stops inside the next hour at "
                          "2026-10-10T02:19:23.627044+00:00 (CONNECTION_LOST)", text)
        self.assertIn("41.7 h later; 41 elapsed hour(s) unjudged", text)
        self.assertIn("current run    0 h (INTERRUPTED)", text)
        self.assertIn("24 consecutive jointly COMPLETE hour(s) needed from new evidence", text)
        self.assertNotIn("current run    9 h", text)


class UniverseHoursCommandTests(unittest.TestCase):
    """OCI-1 2026-10-14: health said OK while hours broke; these facts say why, with no verdict."""

    def test_hour_facts_come_from_real_partitions_without_a_bound(self) -> None:
        import tempfile
        from datetime import date
        from uuid import uuid4

        from tests.test_first_party_capture_archive_v1 import _measurement_payload
        from trade_platform.first_party_capture_archive_v1 import (
            CaptureClockReadingV1,
            CaptureCoverageIntervalV1,
            CaptureGapKindV1,
            CaptureGapV1,
            CapturePartitionWriterV1,
            build_capture_record_v1,
        )
        from trade_platform.first_party_capture_authority_v1 import (
            first_party_bybit_universe_contracts_v1,
        )

        second = 1_000_000_000
        hour = 1_791_936_000 * second  # 2026-10-14T00:00:00Z
        btc, eth, _ = first_party_bybit_universe_contracts_v1()

        def partition(root: Path, contract, windows) -> None:
            session = uuid4()
            writer = CapturePartitionWriterV1(
                root=root, contract=contract, session_id=session, day=date(2026, 10, 14),
                clock_resolution_nanos=1_000,
            )
            sequence = 0
            previous_end = None
            for start, last, proof in windows:
                if previous_end is not None:
                    writer.declare_gap(CaptureGapV1(
                        previous_end, start, CaptureGapKindV1.CONNECTION_LOSS.value
                    ))
                for arrival in (start, last):
                    writer.append_record(build_capture_record_v1(
                        contract=contract, session_id=session, sequence=sequence,
                        clock=CaptureClockReadingV1(arrival, arrival - hour + 10**15),
                        payload_text=_measurement_payload(contract.exchange_symbol),
                    ))
                    sequence += 1
                interval = CaptureCoverageIntervalV1(start, last, proof, 2)
                writer.declare_coverage(interval)
                previous_end = interval.end_utc_nanos
            writer.finalize()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partition(root, btc, [(hour + 10_568_000_000, hour + 3_600 * second + 300_000_000,
                                   CLI.END_PROOF_OPERATOR_BOUNDED_STOP)])
            partition(root, eth, [
                (hour + 4_264_000_000, hour + 988_441_000_000, "CONNECTION_LOST"),
                (hour + 1_011_820_000_000, hour + 3_600 * second + 500_000_000,
                 CLI.END_PROOF_OPERATOR_BOUNDED_STOP),
            ])
            out = io.StringIO()
            with patch.object(CLI, "_utc_now_nanos", return_value=hour + 4_200 * second), \
                    redirect_stdout(out):
                code = CLI.main(["--root", directory, "universe-hours", "--hours", "1"])
        self.assertEqual(code, 0)  # facts never fail anything
        text = out.getvalue()
        self.assertIn("no hand-off bound, no verdict", text)
        self.assertIn("2026-10-14T00:00:00+00:00 BTCUSDT: one run reaches the hour end, unproven 10.568 s\n",
                      text)
        self.assertIn("2026-10-14T00:00:00+00:00 ETHUSDT: one run reaches the hour end, unproven "
                      "1011.820 s; stopped after 2026-10-14T00:16:28.441000+00:00 CONNECTION_LOST\n", text)
        self.assertIn("2026-10-14T00:00:00+00:00 SOLUSDT: no run reaches the hour end\n", text)
        self.assertNotIn("COMPLETE hour", text.replace("(COMPLETE hours: universe-acceptance)", ""))


if __name__ == "__main__":
    unittest.main()
