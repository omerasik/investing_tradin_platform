"""UI-2 -- the research terminal read model over real R4-R10 tables (real PostgreSQL)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ResearchTerminalReadModelPostgresTests(unittest.TestCase):
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
        self.temp = Path(tempfile.mkdtemp(prefix="ui2-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_every_view_reads_recorded_evidence_and_keeps_its_claim(self) -> None:
        from tests.test_live_signals_v1 import _live_bars
        from tests.test_paper_incubation_v1 import incubating_signals
        from tests.test_strategy_sdk_v1 import _walk
        from trade_platform.account_policy_v1 import (
            AccountContextV1,
            AccountKindV1,
            AccountPolicyV1,
            PostgresAccountPolicyStoreV1,
        )
        from trade_platform.live_signals_v1 import PostgresLiveSignalStoreV1
        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
        from trade_platform.paper_incubation_v1 import (
            PaperIncubationEngineV1,
            PostgresPaperIncubationStoreV1,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1

        bars = _live_bars(_walk(300, seed=7))
        study, signals = incubating_signals(self.temp, bars)
        self.assertTrue(signals)
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            PostgresStrategyLabLedgerV1(database).register_study(study)
            for signal in signals:
                PostgresLiveSignalStoreV1(database).record(signal)
            fills = PostgresPaperIncubationStoreV1(database)
            engine = PaperIncubationEngineV1(gross_cost_policy_v1())
            ours = {signal.signal_id for signal in signals}
            engine.add(item for item in fills.pending_signals("BTCUSDT") if item.signal_id in ours)
            for fill in engine.on_bars(bars):
                fills.record(fill)
            accounts = PostgresAccountPolicyStoreV1(database)
            account = AccountContextV1(f"ui2-{uuid4().hex[:8]}", AccountKindV1.PERSONAL_PAPER, "UI-2 paper", "USDT")
            accounts.register_account(account)
            accounts.record_policy(AccountPolicyV1(account, {}))  # nothing decided: UNCONFIGURED

            queries = PostgresOperatorDashboardQueries(database)
            overview = queries.terminal_overview()
            self.assertEqual("cycle-2026-08-20", overview.cycle.cycle_id)
            self.assertGreaterEqual(overview.signals.get("INCUBATING", 0), len(signals))
            # Another test in a shared database may have recorded an ACTIVE watch list.
            or9 = next(gate for gate in overview.owner_gates if gate.gate == "OR-9")
            self.assertEqual(or9.status == "OPEN", or9.evidence == "no ACTIVE watch list recorded")
            self.assertIn("UNCONFIGURED", overview.accounts)

            recent = {view.signal_id: view for view in queries.terminal_signals(limit=500)}
            for signal in signals:
                if signal.signal_id in recent:
                    self.assertEqual("NOT_VALIDATED_INCUBATING", recent[signal.signal_id].claim)
            mine = next(view for view in queries.terminal_accounts() if view.account_id == account.account_id)
            self.assertEqual("UNCONFIGURED", mine.policy_status)
            self.assertTrue(all(reason.endswith("_OR_11") for reason in mine.unresolved))

            incubation = queries.terminal_incubation()
            self.assertEqual("AVAILABLE", incubation.state)
            self.assertEqual(("INCUBATING", False), (incubation.report["state"], incubation.report["cost_complete"]))
            self.assertIn(str(study.study_id), {item["study_id"] for item in incubation.report["candidates"]})

            validation = queries.terminal_validation()
            self.assertEqual("NONE_VALIDATED", validation.validated_claim)
            self.assertTrue(all(item.label in {"INCUBATING", "REJECTED"} for item in validation.candidates))
            for rerun in queries.terminal_reruns():
                self.assertEqual("GROSS_CONDITIONAL_NON_PROMOTABLE", rerun.economics)
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
