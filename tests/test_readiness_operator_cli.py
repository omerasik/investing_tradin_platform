"""Operator handoff uses the protected projection and environment-only DSNs."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from scripts import strategy_lab, terminal_command


class TerminalReadinessCliTests(unittest.TestCase):
    def invoke(self, flags: list[str], status: int, body: object) -> tuple[int, object]:
        output = io.StringIO()
        with patch("sys.argv", ["terminal_command.py", "--api", "http://operator.invalid", *flags]), \
                patch.dict("os.environ", {"TRADE_PLATFORM_OPERATOR_TOKEN": "fixture-token"}, clear=True), \
                patch.object(terminal_command, "_call", return_value=(status, body)) as call, \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            terminal_command.main()
        self.assertNotIn("fixture-token", output.getvalue())
        self.assertEqual(body, json.loads(output.getvalue()))
        return exited.exception.code, call.call_args

    def test_readiness_is_only_a_get_and_preserves_blocked_projection(self) -> None:
        body = {"state_hash": "recorded", "answers": [{"status": "BLOCKED", "reasons": ["OR-7"]}]}
        code, call = self.invoke(["--readiness"], 200, body)
        self.assertEqual(0, code)  # successful read is not a readiness verdict
        self.assertEqual(("http://operator.invalid/operator-dashboard/research-terminal/activation", "fixture-token"),
                         call.args)
        self.assertEqual({}, call.kwargs)  # no command body

    def test_failed_reads_exit_nonzero_including_list(self) -> None:
        for flag, status in (("--readiness", 401), ("--readiness", 503), ("--list", 403)):
            with self.subTest(flag=flag, status=status):
                code, _ = self.invoke([flag], status, {"detail": "Unavailable"})
                self.assertEqual(1, code)

    def test_read_actions_cannot_be_combined_with_writes(self) -> None:
        for flags in (["--readiness", "--kind", "HOLDOUT_OPEN"], ["--readiness", "--inputs", "owner.json"],
                      ["--list", "--key", "owner-key"]):
            with self.subTest(flags=flags), patch("sys.argv", ["terminal_command.py", "--api", "http://invalid", *flags]), \
                    patch.object(terminal_command, "_call") as call, contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as exited:
                terminal_command.main()
            self.assertEqual(2, exited.exception.code)
            call.assert_not_called()


class StrategyLabDsnCliTests(unittest.TestCase):
    def test_environment_dsn_and_explicit_override_reach_existing_runner(self) -> None:
        for extra, expected in (([], "fixture-env-dsn"), (["--dsn", "fixture-override"], "fixture-override")):
            with self.subTest(extra=extra), \
                    patch("sys.argv", ["strategy_lab.py", "status", "--family", "trend_ma_cross", "--dataset",
                                       "00000000-0000-0000-0000-000000000001", *extra]), \
                    patch.dict("os.environ", {"TRADE_PLATFORM_RESEARCH_DSN": "fixture-env-dsn"}, clear=True), \
                    patch.object(strategy_lab, "build_study"), patch.object(strategy_lab, "PostgresDatabase") as db, \
                    patch.object(strategy_lab, "run_strategy_lab_command", return_value={}) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                strategy_lab.main()
            db.assert_called_once_with(expected)
            self.assertEqual(expected, run.call_args.kwargs["dsn"])
            db.return_value.close.assert_called_once()

    def test_missing_dsn_refuses_before_loading_data(self) -> None:
        with patch("sys.argv", ["strategy_lab.py", "status", "--family", "trend_ma_cross", "--dataset",
                                "00000000-0000-0000-0000-000000000001"]), \
                patch.dict("os.environ", {}, clear=True), patch.object(strategy_lab, "build_study") as build, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exited:
            strategy_lab.main()
        self.assertEqual(2, exited.exception.code)
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
