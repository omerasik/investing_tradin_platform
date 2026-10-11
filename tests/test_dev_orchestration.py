"""Unit coverage for explicit Module 1B demo orchestration ordering."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
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
            patch.object(self.dev, "preflight_dashboard_config"),
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
            patch.object(self.dev, "preflight_dashboard_config"),
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


API_PORT, DASHBOARD_PORT = 58000, 53000
TOKEN = "unit-operator-token-value"  # pragma: allowlist secret


class DashboardConfigPreflightTests(unittest.TestCase):
    """dashboard.config.json wins over dev.py's env; a disagreement must stop the launch."""

    def setUp(self) -> None:
        self.dev = _load_dev_module()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.config = self.tmp / "dashboard.config.json"

    def _conflicts(self, document: object | None, environ: dict[str, str] | None = None) -> list[str]:
        if document is not None:
            self.config.write_text(
                document if isinstance(document, str) else json.dumps(document), encoding="utf-8"
            )
        return self.dev.dashboard_config_conflicts(
            self.config, api_port=API_PORT, dashboard_port=DASHBOARD_PORT,
            operator_token=TOKEN, environ=environ or {},
        )

    def _token_file(self, value: str) -> str:
        path = self.tmp / "operator.token"
        path.write_text(value + "\n", encoding="utf-8")
        return str(path)

    def test_absent_config_conflicts_with_nothing(self) -> None:
        self.assertEqual(self._conflicts(None), [])

    def test_matching_config_is_accepted(self) -> None:
        document = {
            "api_base_url": f"http://127.0.0.1:{API_PORT}/",
            "dashboard_origin": f"http://127.0.0.1:{DASHBOARD_PORT}",
            "operator_token_file": self._token_file(TOKEN),
            "strategy_id": "00000000-0000-0000-0000-000000000000",
        }
        self.assertEqual(self._conflicts(document), [])

    def test_pinned_api_port_and_origin_are_refused(self) -> None:
        conflicts = self._conflicts(
            {"api_base_url": "http://127.0.0.1:8765", "dashboard_origin": "http://127.0.0.1:3001"}
        )
        self.assertEqual(len(conflicts), 2)
        self.assertIn("api_base_url='http://127.0.0.1:8765'", conflicts[0])
        self.assertIn(f"http://127.0.0.1:{API_PORT}", conflicts[0])
        self.assertIn("dashboard_origin", conflicts[1])

    def test_localhost_api_is_refused_because_the_api_binds_ipv4_only(self) -> None:
        self.assertEqual(len(self._conflicts({"api_base_url": f"http://localhost:{API_PORT}"})), 1)

    def test_token_file_with_another_token_is_refused_without_echoing_it(self) -> None:
        other = "the-other-e2e-token-value"  # pragma: allowlist secret
        conflicts = self._conflicts({"operator_token_file": self._token_file(other)})
        self.assertEqual(len(conflicts), 1)
        self.assertIn("operator_token_file", conflicts[0])
        for message in conflicts:
            self.assertNotIn(other, message)
            self.assertNotIn(TOKEN, message)

    def test_unreadable_token_file_is_refused(self) -> None:
        self.assertEqual(len(self._conflicts({"operator_token_file": str(self.tmp / "missing.token")})), 1)

    def test_custom_token_env_must_hold_the_launch_token(self) -> None:
        document = {"operator_token_env": "CUSTOM_OPERATOR_TOKEN"}
        self.assertEqual(len(self._conflicts(document, {"CUSTOM_OPERATOR_TOKEN": "different"})), 1)
        self.assertEqual(len(self._conflicts(document, {})), 1)
        self.assertEqual(self._conflicts(document, {"CUSTOM_OPERATOR_TOKEN": TOKEN}), [])
        self.assertEqual(self._conflicts({"operator_token_env": "TRADE_PLATFORM_OPERATOR_TOKEN"}), [])

    def test_both_token_sources_are_refused(self) -> None:
        document = {"operator_token_file": self._token_file(TOKEN), "operator_token_env": "X_TOKEN"}
        self.assertEqual(len(self._conflicts(document)), 1)

    def test_malformed_document_is_refused(self) -> None:
        self.assertEqual(len(self._conflicts("{not json")), 1)
        self.assertEqual(len(self._conflicts("[]")), 1)

    def test_config_path_env_is_resolved_like_the_dashboard(self) -> None:
        self.assertEqual(self.dev.dashboard_config_path({}), self.dev.WEB_DIR / "dashboard.config.json")
        self.assertEqual(
            self.dev.dashboard_config_path({"TRADE_PLATFORM_DASHBOARD_CONFIG_PATH": "alt.json"}),
            self.dev.WEB_DIR / "alt.json",
        )
        self.assertEqual(
            self.dev.dashboard_config_path({"TRADE_PLATFORM_DASHBOARD_CONFIG_PATH": str(self.config)}),
            self.config,
        )

    def test_main_stops_before_touching_docker_and_never_logs_the_token(self) -> None:
        other = "the-other-e2e-token-value"  # pragma: allowlist secret
        self.config.write_text(json.dumps({
            "api_base_url": "http://127.0.0.1:8765",
            "operator_token_file": self._token_file(other),
        }), encoding="utf-8")
        stderr = io.StringIO()
        argv = ["dev.py", "--research", "--api-port", str(API_PORT), "--port", str(DASHBOARD_PORT)]
        with (
            patch.object(sys, "argv", argv),
            patch.dict(os.environ, {"TRADE_PLATFORM_DASHBOARD_CONFIG_PATH": str(self.config),
                                    "TRADE_PLATFORM_OPERATOR_TOKEN": TOKEN}),
            patch.object(self.dev, "check_prerequisites") as prerequisites,
            patch.object(self.dev.subprocess, "run") as run,
            patch("sys.stderr", stderr),
            self.assertRaises(SystemExit) as stopped,
        ):
            self.dev.main()
        self.assertEqual(stopped.exception.code, 1)
        prerequisites.assert_not_called()
        run.assert_not_called()
        output = stderr.getvalue()
        self.assertIn("api_base_url", output)
        self.assertIn("operator_token_file", output)
        self.assertNotIn(TOKEN, output)
        self.assertNotIn(other, output)


if __name__ == "__main__":
    unittest.main()
