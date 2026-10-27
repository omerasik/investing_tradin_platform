import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_platform.dev_app import create_dev_app
from trade_platform.persistence import PersistenceError, PostgresDatabase
from trade_platform.research_terminal_commands_v1 import (
    CommandView,
    PostgresTerminalCommandLedgerV1,
    command_identity_v1,
)
from trade_platform.security import OperatorAuthenticator, OperatorRole


class DevAppTests(unittest.TestCase):
    def test_dev_app_liveness_readiness_and_disabled_live_trading(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TRADE_PLATFORM_OPERATOR_TOKEN": "test-dev-token",
                "POSTGRES_DSN": "postgresql://postgres:postgres@127.0.0.1:5439/trade_platform",  # pragma: allowlist secret
            },
        ):
            mock_db = MagicMock(spec=PostgresDatabase)
            app = create_dev_app(database=mock_db)
            client = TestClient(app)

            live_res = client.get("/health/live")
            self.assertEqual(live_res.status_code, 200)
            self.assertEqual(live_res.json(), {"status": "ok"})

            ready_res = client.get("/health/ready")
            self.assertEqual(ready_res.status_code, 200)
            payload = ready_res.json()
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["environment"], "local_research")
            self.assertEqual(payload["paper_trading_enabled"], True)
            self.assertEqual(payload["live_trading_enabled"], False)

    def test_dev_app_unauthenticated_request_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {"TRADE_PLATFORM_OPERATOR_TOKEN": "test-dev-token"},
        ):
            mock_db = MagicMock(spec=PostgresDatabase)
            app = create_dev_app(database=mock_db)
            client = TestClient(app)

            res = client.get("/audit/events")
            self.assertEqual(res.status_code, 401)

            auth_res = client.get(
                "/audit/events",
                headers={"Authorization": "Bearer test-dev-token"},
            )
            self.assertEqual(auth_res.status_code, 200)


class DevTerminalCommandTests(unittest.TestCase):
    PATH = "/operator-dashboard/research-terminal/commands"
    TOKEN = "fixture-dev-terminal-token"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = {
            "POSTGRES_DSN": "postgresql://fixture.invalid/development",
            "TRADE_PLATFORM_OPERATOR_TOKEN": self.TOKEN,
            "TRADE_PLATFORM_RESEARCH_DATA_ROOT": str(self.root / "research"),
            "TRADE_PLATFORM_CAPTURE_ARCHIVE_ROOT": str(self.root / "capture"),
            "TRADE_PLATFORM_UNIVERSE_CAPTURE_ARCHIVE_ROOT": str(self.root / "universe"),
        }
        self.database = MagicMock(spec=PostgresDatabase)
        with patch.dict(os.environ, self.environment, clear=True):
            self.app = create_dev_app(database=self.database)
        self.ledger = self.app.state.terminal_commands
        self.queries = self.app.state.operator_dashboard_queries
        self.client = TestClient(self.app)
        self.headers = {"Authorization": f"Bearer {self.TOKEN}"}
        self.command = {"kind": "STRATEGY_SEARCH", "inputs": {"family": "trend_ma_cross",
            "dataset_version_id": str(uuid4()), "workers": 1}, "idempotency_key": "fixture-search"}

    @staticmethod
    def _readiness(reasons: list[str] | None = None) -> SimpleNamespace:
        return SimpleNamespace(state_hash="0" * 64, answers=[SimpleNamespace(
            key="research_run", status="BLOCKED" if reasons else "READY", reasons=reasons or [])])

    def _view(self, *, reasons: list[str] | None = None) -> CommandView:
        command_id, _ = command_identity_v1(
            self.command["kind"], self.command["inputs"], self.command["idempotency_key"])
        now = datetime.now(UTC)
        return CommandView(command_id=command_id, kind=self.command["kind"], inputs=self.command["inputs"],
                           requested_by="local-operator", requested_at=now, updated_at=now,
                           state="BLOCKED" if reasons else "REQUESTED", detail={"reasons": reasons or []})

    def test_configured_database_and_evidence_roots_are_shared_with_the_canonical_ledger(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True), \
                patch("trade_platform.dev_app.PostgresDatabase", return_value=self.database) as factory:
            app = create_dev_app()
        factory.assert_called_once_with(self.environment["POSTGRES_DSN"])
        self.assertIsInstance(app.state.terminal_commands, PostgresTerminalCommandLedgerV1)
        self.assertIs(app.state.terminal_commands.database, self.database)
        self.assertIs(app.state.operator_dashboard_queries._database, self.database)
        self.assertEqual(app.state.research_data_root, self.root / "research")
        self.assertEqual(app.state.capture_archive_root, self.root / "capture")
        self.assertEqual(app.state.universe_capture_archive_root, self.root / "universe")

    def test_anonymous_command_requests_are_refused_before_database_access(self) -> None:
        for path in (self.PATH, self.PATH.replace("/commands", "/owner-commands")):
            self.assertEqual(401, self.client.post(path, json=self.command).status_code)
        self.database.transaction.assert_not_called()

    def test_authenticated_requests_reach_the_actual_ledger_and_preserve_idempotency(self) -> None:
        view = self._view()
        cursor = self.database.transaction.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [None, (view.command_id,), (view.command_id,)]
        with patch.object(self.queries, "terminal_activation", return_value=self._readiness()) as readiness, \
                patch.object(self.ledger, "get", return_value=view):
            first = self.client.post(self.PATH, json=self.command, headers=self.headers)
            repeated = self.client.post(self.PATH, json=self.command, headers=self.headers)
            conflict = self.client.post(self.PATH, json={**self.command, "inputs": {
                **self.command["inputs"], "workers": 2}}, headers=self.headers)
        self.assertEqual((202, 202, 422), (first.status_code, repeated.status_code, conflict.status_code))
        self.assertEqual(first.json()["command_id"], repeated.json()["command_id"])
        self.assertIn("idempotency_key_reused_for_a_different_command", conflict.text)
        readiness.assert_called_with(windows=[], cycle_id=None)
        inserts = [call.args for call in cursor.execute.call_args_list if call.args[0].startswith("INSERT")]
        self.assertEqual(2, len(inserts))  # one immutable request and one event, never a duplicate
        self.assertEqual("local-operator", inserts[0][1][5])
        self.assertEqual("REQUESTED", inserts[1][1][2])

    def test_activation_blockers_are_recorded_and_returned_without_execution(self) -> None:
        reasons = ["NO_RESEARCH_WINDOW_CATALOGUED"]
        cursor = self.database.transaction.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        with patch.object(self.queries, "terminal_activation", return_value=self._readiness(reasons)), \
                patch.object(self.ledger, "get", return_value=self._view(reasons=reasons)), \
                patch("trade_platform.api.execute_inline_v1") as execute:
            response = self.client.post(self.PATH, json=self.command, headers=self.headers)
        self.assertEqual(409, response.status_code)
        self.assertEqual(reasons, response.json()["detail"]["reasons"])
        self.assertEqual("BLOCKED", cursor.execute.call_args.args[1][2])
        execute.assert_not_called()

    def test_owner_endpoint_permissions_and_authenticated_approval_identity_are_preserved(self) -> None:
        owner_path = self.PATH.replace("/commands", "/owner-commands")
        command = {"kind": "WATCHLIST_RECORD", "inputs": {"watchlist_id": "fixture-watch", "entries": [],
            "approved_by": "another-owner", "approved_on": "2026-10-09"}, "idempotency_key": "fixture-owner"}
        self.assertEqual(403, self.client.post(self.PATH, json=command, headers=self.headers).status_code)
        self.app.state.authenticator = OperatorAuthenticator(self.TOKEN, role=OperatorRole.RESEARCHER)
        self.assertEqual(403, self.client.post(owner_path, json=command, headers=self.headers).status_code)
        self.app.state.authenticator = OperatorAuthenticator(self.TOKEN)
        with patch.object(self.queries, "terminal_activation", return_value=self._readiness()):
            response = self.client.post(owner_path, json=command, headers=self.headers)
        self.assertEqual(422, response.status_code)
        self.assertIn("approved_by_must_be_the_authenticated_subject", response.text)
        self.database.transaction.assert_not_called()

    def test_missing_or_incompatible_database_authority_never_falls_back(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True), \
                patch("trade_platform.dev_app.PostgresDatabase", side_effect=PersistenceError("unavailable")), \
                self.assertRaisesRegex(PersistenceError, "unavailable"):
            create_dev_app()
        self.database.transaction.side_effect = PersistenceError("incompatible schema")
        response = self.client.post(self.PATH, json=self.command, headers=self.headers)
        self.assertEqual(503, response.status_code)
        self.assertEqual("Activation readiness unavailable.", response.json()["detail"])
        self.app.state.terminal_commands = None
        response = self.client.post(self.PATH, json=self.command, headers=self.headers)
        self.assertEqual(503, response.status_code)
        self.assertEqual("Terminal command authority unavailable.", response.json()["detail"])


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class DevTerminalCommandsPostgresTests(unittest.TestCase):
    """Use the existing migrated disposable CI service; never migrate or start a worker."""

    def test_development_command_gate_and_idempotency_use_postgres(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "POSTGRES_DSN": os.environ["POSTGRES_TEST_DSN"],
            "TRADE_PLATFORM_OPERATOR_TOKEN": DevTerminalCommandTests.TOKEN,
            "TRADE_PLATFORM_RESEARCH_DATA_ROOT": directory,
        }):
            database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
            self.addCleanup(database.close)
            app = create_dev_app(database=database)
            client = TestClient(app)
            path = DevTerminalCommandTests.PATH
            command = {"kind": "STRATEGY_SEARCH", "inputs": {"family": "trend_ma_cross",
                "dataset_version_id": str(uuid4()), "workers": 1}, "idempotency_key": f"dev-fixture-{uuid4()}"}
            headers = {"Authorization": f"Bearer {DevTerminalCommandTests.TOKEN}"}
            self.assertIs(app.state.terminal_commands.database, database)
            self.assertEqual(401, client.post(path, json=command).status_code)
            first = client.post(path, json=command, headers=headers)
            repeated = client.post(path, json=command, headers=headers)
            self.assertEqual((409, 409), (first.status_code, repeated.status_code), first.text)
            self.assertEqual(first.json()["detail"]["command_id"], repeated.json()["detail"]["command_id"])
            self.assertIn("NO_RESEARCH_WINDOW_CATALOGUED", first.json()["detail"]["reasons"])
            view = app.state.terminal_commands.get(first.json()["detail"]["command_id"])
            self.assertEqual("BLOCKED", view.state)
            conflict = client.post(path, json={**command, "inputs": {**command["inputs"], "workers": 2}}, headers=headers)
            self.assertEqual(422, conflict.status_code)


if __name__ == "__main__":
    unittest.main()
