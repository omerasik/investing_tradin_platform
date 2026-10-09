"""Research terminal commands through the protected API and the worker (real PostgreSQL).

Fixture archive days only. The real cycle-2026-08-20 is never opened: every
command that needs it is refused with BLOCKED_OWNER_DECISION_OR_7.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class TerminalCommandsPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="terminal-cmd-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _client(self, database, role, data_root):  # type: ignore[no-untyped-def]
        from fastapi.testclient import TestClient

        from trade_platform.api import build_app
        from trade_platform.audit import SQLiteAuditStore
        from trade_platform.config import PlatformConfig
        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
        from trade_platform.research_terminal_commands_v1 import PostgresTerminalCommandLedgerV1
        from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator

        app = build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("t", "owner", role),
                        InMemoryRateLimiter(max_requests=1000),
                        operator_dashboard_queries=PostgresOperatorDashboardQueries(database),
                        terminal_commands=PostgresTerminalCommandLedgerV1(database), research_data_root=data_root)
        return TestClient(app, headers={"Authorization": "Bearer t"})

    def _drain(self, data_root: Path) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from research_terminal_worker import Worker

        Worker(argparse.Namespace(dsn=os.environ["POSTGRES_TEST_DSN"], data_root=data_root,
                                  archive_root=self.temp / "archive", capture_root=None, poll_seconds=0.1,
                                  once=True, disk_guard_gib=0.0)).run()

    def test_commands_are_gated_idempotent_and_carried_out_by_existing_authorities(self) -> None:
        from tests.test_activation_readiness_v1_postgres import recorded_established_study
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.security import OperatorRole

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            fixture = recorded_established_study(self.temp, database)
            data_root, study, rerun = fixture["data_root"], fixture["study"], fixture["rerun"]
            window = {"family": "breakout_channel", "dataset_version_id": str(study.datasets[0].dataset_version_id)}
            research = self._client(database, OperatorRole.RESEARCHER, data_root)
            owner = self._client(database, OperatorRole.RISK_REVIEWER, data_root)
            run = uuid4().hex[:8]

            def post(client, kind, inputs, key, path="commands"):  # type: ignore[no-untyped-def]
                return client.post(f"/operator-dashboard/research-terminal/{path}",
                                   json={"kind": kind, "inputs": inputs, "idempotency_key": f"{run}-{key}"})

            # Permission split: an owner record never goes through the research endpoint.
            self.assertEqual(403, post(research, "ACCOUNT_REGISTER", {}, "x").status_code)
            # Queue a search; the same key is idempotent, a different payload under it is refused.
            first = post(research, "STRATEGY_SEARCH", {**window, "workers": 1}, "search")
            self.assertEqual((202, "REQUESTED"), (first.status_code, first.json()["state"]))
            self.assertEqual(first.json()["command_id"],
                             post(research, "STRATEGY_SEARCH", {**window, "workers": 1}, "search").json()["command_id"])
            self.assertEqual(422, post(research, "STRATEGY_SEARCH", {**window, "workers": 2}, "search").status_code)
            freeze = post(research, "CANDIDATE_FREEZE", {**window, "metric": "trades", "direction": "HIGHER_IS_BETTER",
                                                        "top_k": 2}, "freeze")
            rerun_cmd = post(research, "DECIMAL_RERUN", {**window, "metric": "trades", "direction": "HIGHER_IS_BETTER",
                                                         "top_k": 2, "workers": 1}, "rerun")
            self.assertEqual(202, rerun_cmd.status_code)
            self._drain(data_root)
            done = {c["kind"]: c for c in research.get("/operator-dashboard/research-terminal/commands?limit=200").json()
                    if c["command_id"] in {first.json()["command_id"], freeze.json()["command_id"],
                                           rerun_cmd.json()["command_id"]}}
            self.assertEqual("SUCCEEDED", done["STRATEGY_SEARCH"]["state"], done["STRATEGY_SEARCH"]["detail"])
            self.assertEqual("SUCCEEDED", done["CANDIDATE_FREEZE"]["state"], done["CANDIDATE_FREEZE"]["detail"])
            self.assertTrue(done["CANDIDATE_FREEZE"]["detail"]["candidate_set_hash"])
            rerun_detail = done["DECIMAL_RERUN"]["detail"]
            self.assertEqual("SUCCEEDED", done["DECIMAL_RERUN"]["state"], rerun_detail)

            # A preregistration draft is recorded and names exactly what the owner must decide.
            prereg = post(owner, "PREREGISTRATION_RECORD", {**window, "rerun_hash": rerun_detail["rerun_hash"],
                                                           "symbol": "BTCUSDT", "cycle_id": "cycle-2026-08-20"},
                          "prereg", "owner-commands")
            self.assertEqual(202, prereg.status_code)
            self._drain(data_root)
            draft = next(c for c in owner.get("/operator-dashboard/research-terminal/commands?limit=200").json()
                         if c["command_id"] == prereg.json()["command_id"])
            self.assertEqual(("SUCCEEDED", "DRAFT"), (draft["state"], draft["detail"]["status"]))
            gates = {r.split(":")[0] for r in draft["detail"]["unresolved"]}
            self.assertTrue({"BLOCKED_OWNER_DECISION_OR_7", "BLOCKED_OWNER_DECISION_OR_6"} <= gates)

            # The real holdout cannot be opened: the refusal names the owner gate.
            opening = post(owner, "HOLDOUT_OPEN", {"preregistration_hash": draft["detail"]["preregistration_hash"],
                                                  "confirm_cycle_id": "cycle-2026-08-20", "opened_by": "owner"},
                           "open", "owner-commands")
            self.assertEqual(409, opening.status_code)
            self.assertTrue(any(r.startswith("BLOCKED_OWNER_DECISION_OR_7") for r in opening.json()["detail"]["reasons"]))
            paper = post(research, "PAPER_INCUBATION_START", {**window, "symbol": "BTCUSDT"}, "paper")
            self.assertEqual(409, paper.status_code)
            self.assertIn("BLOCKED_OWNER_DECISION_OR_7", "".join(paper.json()["detail"]["reasons"]))

            # Inline owner records go through their own authorities and never invent a value.
            account = f"cmd-{run}"
            self.assertEqual("SUCCEEDED", post(owner, "ACCOUNT_REGISTER", {
                "account_id": account, "kind": "PERSONAL_PAPER", "display_name": "Cmd", "base_currency": "USDT"},
                "acct", "owner-commands").json()["state"])
            policy = post(owner, "ACCOUNT_POLICY_RECORD", {"account_id": account, "values": {}}, "pol",
                          "owner-commands").json()
            self.assertEqual(("SUCCEEDED", "UNCONFIGURED"), (policy["state"], policy["detail"]["status"]))
            self.assertIn("MISSING_OWNER_PAPER_STARTING_CAPITAL_OR_11", policy["detail"]["unresolved"])
            selected = rerun.identity["authoritative_selection"]["selected"][0]["trial_id"]
            # An approval names the authenticated owner, never someone else typed in as free text.
            forged = post(owner, "WATCHLIST_RECORD", {"watchlist_id": f"cmd-{run}", "entries": [], "approved_by":
                                                      "someone-else", "approved_on": "2026-10-09"}, "forged",
                          "owner-commands")
            self.assertEqual(422, forged.status_code)
            self.assertIn("approved_by_must_be_the_authenticated_subject", forged.text)
            watch = post(owner, "WATCHLIST_RECORD", {"watchlist_id": f"cmd-{run}", "entries": [
                {"study_id": str(study.study_id), "rerun_hash": rerun.rerun_hash, "trial_id": selected,
                 "symbol": "BTCUSDT"}], "approved_by": "owner", "approved_on": "2026-10-09"}, "watch",
                "owner-commands").json()
            self.assertEqual(("SUCCEEDED", "ACTIVE"), (watch["state"], watch["detail"]["status"]))
            start = post(research, "RESEARCH_WATCH_START", {**window, "symbol": "BTCUSDT",
                                                            "watchlist_id": f"cmd-{run}"}, "watchstart")
            self.assertEqual(409, start.status_code)  # forward bars wait on the real holdout (OR-7)
            # The database itself refuses a second claim or a second outcome of one command.
            from trade_platform.persistence import PersistenceError
            from trade_platform.research_terminal_commands_v1 import (
                PostgresTerminalCommandLedgerV1,
                TerminalCommandError,
            )

            ledger = PostgresTerminalCommandLedgerV1(database)
            done_id = first.json()["command_id"]
            with self.assertRaises(PersistenceError), database.transaction() as connection, \
                    connection.cursor() as cursor:
                cursor.execute("INSERT INTO research_terminal_command_events (event_id, command_id, state, detail, "
                               "actor, occurred_at) VALUES (gen_random_uuid(), %s, 'CLAIMED', '{}'::jsonb, 'x', now())",
                               (done_id,))
            with self.assertRaisesRegex(TerminalCommandError, "already_terminal"):
                ledger.record(UUID(done_id), "FAILED", {}, actor="x")
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
