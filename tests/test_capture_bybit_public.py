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

        with (
            patch.object(CLI, "run_capture_fleet_v1", side_effect=fleet),
            patch.object(CLI, "request_keep_awake_v1", return_value=True),
            patch.object(CLI.time, "time", return_value=NOW),
            redirect_stdout(io.StringIO()),
        ):
            code = CLI.main(["--root", "unused-root", "supervise", *argv])
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


if __name__ == "__main__":
    unittest.main()
