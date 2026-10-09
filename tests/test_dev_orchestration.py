"""Unit coverage for explicit Module 1B demo orchestration ordering."""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


def _load_dev_module() -> object:
    spec = importlib.util.spec_from_file_location("module1b_dev_orchestrator", ROOT / "scripts" / "dev.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("dev_orchestrator_module_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DevOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dev = _load_dev_module()

    def _run_main(self, *, demo: bool, research: bool = False) -> tuple[list[str], MagicMock, MagicMock]:
        calls: list[str] = []
        backend, frontend = MagicMock(), MagicMock()
        backend.poll.return_value = 0
        frontend.poll.return_value = 0
        argv = ["dev.py", "--postgres-port", "55439", "--api-port", "58000", "--port", "53000"]
        if demo:
            argv.append("--demo")
        if research:
            argv.append("--research")
        with (
            patch.object(sys, "argv", argv),
            patch.object(self.dev, "check_prerequisites", side_effect=lambda *_: ["pnpm"]),
            patch.object(self.dev, "start_postgres", side_effect=lambda *_args, **_kwargs: calls.append("postgres")),
            patch.object(self.dev, "start_research_postgres", side_effect=lambda *_: calls.append("research-postgres")),
            patch.object(self.dev, "run_migrations", side_effect=lambda _port, db: calls.append(f"migrations:{db}")),
            patch.object(self.dev, "seed_demo", side_effect=lambda *_: calls.append("seed")),
            patch.object(self.dev, "start_backend",
                         side_effect=lambda *a: (calls.append(f"backend:{a[3]}"), backend)[1]),
            patch.object(self.dev, "start_frontend", side_effect=lambda *_: (calls.append("frontend"), frontend)[1]),
            patch.object(self.dev, "wait_for_services", side_effect=KeyboardInterrupt),
            patch.object(self.dev, "log"),
            patch.object(self.dev, "log_success"),
            self.assertRaises(SystemExit),
        ):
            self.dev.main()
        return calls, backend, frontend

    def test_demo_migrates_then_seeds_before_services(self) -> None:
        calls, backend, frontend = self._run_main(demo=True)
        self.assertEqual(calls, ["postgres", "migrations:trade_platform", "seed", "backend:trade_platform", "frontend"])
        self.assertEqual((backend.poll.call_count, frontend.poll.call_count), (1, 1))

    def test_normal_start_does_not_seed_demo_evidence(self) -> None:
        calls, _backend, _frontend = self._run_main(demo=False)
        self.assertEqual(calls, ["postgres", "migrations:trade_platform", "backend:trade_platform", "frontend"])

    def test_research_serves_and_migrates_the_research_database_without_compose(self) -> None:
        calls, _backend, _frontend = self._run_main(demo=False, research=True)
        self.assertEqual(calls, ["research-postgres", "migrations:trade_platform_research",
                                 "backend:trade_platform_research", "frontend"])

    def test_research_refuses_seed_or_reset_before_touching_docker(self) -> None:
        for flag in ("--demo", "--reset-db"):
            with (
                patch.object(sys, "argv", ["dev.py", "--research", flag]),
                patch.object(self.dev, "check_prerequisites") as prerequisites,
                patch.object(self.dev.subprocess, "run") as run,
                patch("sys.stderr"),
                self.assertRaises(SystemExit),
            ):
                self.dev.main()
            prerequisites.assert_not_called()
            run.assert_not_called()

    def test_seed_failure_stops_before_starting_backend(self) -> None:
        argv = ["dev.py", "--demo"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(self.dev, "check_prerequisites", return_value=["pnpm"]),
            patch.object(self.dev, "start_postgres"),
            patch.object(self.dev, "run_migrations"),
            patch.object(self.dev, "seed_demo", side_effect=SystemExit(1)),
            patch.object(self.dev, "start_backend") as backend,
            self.assertRaises(SystemExit),
        ):
            self.dev.main()
        backend.assert_not_called()

    def test_reset_rejects_remote_dsn_before_docker_mutation(self) -> None:
        with (
            patch.dict(os.environ, {"POSTGRES_DSN": "postgresql://demo:demo@db.example.invalid/trade"}),  # pragma: allowlist secret
            patch.object(self.dev.subprocess, "run") as run,
            patch.object(self.dev, "log_error"),
            self.assertRaises(SystemExit),
        ):
            self.dev.start_postgres(55439, reset_db=True)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
