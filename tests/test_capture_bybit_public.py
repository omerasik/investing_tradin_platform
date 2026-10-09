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
                    ["--root", directory, "universe-acceptance", "--max-head-unproven-seconds", "30"]
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
                        "--max-head-unproven-seconds", "30", "--required-hours", "1",
                    ]
                )
        self.assertEqual(code, 0)
        text = out.getvalue()
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            self.assertIn(f"== {symbol}: 1 COMPLETE hour(s)", text)
        self.assertIn("acceptance     MET", text)


if __name__ == "__main__":
    unittest.main()
