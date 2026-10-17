"""UI-2 -- the research terminal read model over real R4-R10 tables (real PostgreSQL)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4


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

            # The report is the current cycle's: these fixture fills' INCUBATING evidence is no recorded
            # validation of cycle-2026-08-20, so they are never part of it (cycle isolation).
            incubation = queries.terminal_incubation()
            self.assertFalse(incubation.report["cost_complete"])
            self.assertNotIn(str(study.study_id), {item["study_id"] for item in incubation.report["candidates"]})

            validation = queries.terminal_validation()
            self.assertEqual("NONE_VALIDATED", validation.validated_claim)
            self.assertTrue(all(item.label in {"INCUBATING", "REJECTED"} for item in validation.candidates))
            for rerun in queries.terminal_reruns():
                self.assertEqual("GROSS_CONDITIONAL_NON_PROMOTABLE", rerun.economics)
        finally:
            database.close()

    def test_candidate_lifecycle_links_the_stored_chain_and_keeps_draft_criteria_draft(self) -> None:
        from datetime import UTC, datetime, timedelta

        from tests.test_activation_readiness_v1_postgres import recorded_established_study
        from trade_platform.candidate_lifecycle_readiness_v1 import (
            STAGE_DECIMAL_SELECTION_ESTABLISHED,
            STAGE_SEARCH_ONLY,
            CandidateEvidenceV1,
            assess_candidate_readiness_v1,
        )
        from trade_platform.incubation_exit_criteria_v1 import (
            STAGE_UNRESOLVED,
            ExitCriteriaPacketV1,
            PostgresExitCriteriaRegistryV1,
        )
        from trade_platform.operator_dashboard import (
            DashboardObjectNotFound,
            PostgresOperatorDashboardQueries,
        )
        from trade_platform.paper_account_v1 import cycle_cost_policy_v1
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.research_terminal_read_model_v1 import (
            NOT_LOADED_ACCOUNT_POLICY,
            NOT_LOADED_PARITY,
            NOT_LOADED_RUNNER_LIVENESS,
        )
        from trade_platform.strategy_lab_manifest_v1 import RankDirectionV1
        from trade_platform.strategy_lab_splits_v1 import SplitModeV1, WalkForwardPlanV1
        from trade_platform.strategy_lab_validation_v1 import (
            PostgresHoldoutRegistryV1,
            PreregistrationV1,
        )
        from trade_platform.strategy_lab_walk_forward_v1 import (
            PostgresWalkForwardStoreV1,
            WalkForwardSelectionRuleV1,
            run_walk_forward_v1,
        )
        from trade_platform.strategy_sdk_v1 import load_bars_v1

        day0, hour = datetime(2026, 6, 1, tzinfo=UTC), timedelta(hours=1)
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            fixture = recorded_established_study(self.temp, database)
            study, rerun = fixture["study"], fixture["rerun"]
            (binding,) = study.datasets
            selected = [str(item["trial_id"]) for item in rerun.identity["authoritative_selection"]["selected"]]
            unselected = next(str(t.trial_id) for t in study.trials() if str(t.trial_id) not in selected)
            bars = load_bars_v1(fixture["data_root"], binding.dataset_version_id,
                                expected_content_hash=binding.content_hash)
            plan = WalkForwardPlanV1(mode=SplitModeV1.ROLLING, train_duration=12 * hour, test_duration=6 * hour,
                                     step=6 * hour, embargo=timedelta(minutes=10), first_test_start=day0 + 12 * hour,
                                     evaluation_end_exclusive=day0 + 48 * hour)
            report = run_walk_forward_v1(
                study, plan, WalkForwardSelectionRuleV1("total_return", RankDirectionV1.HIGHER_IS_BETTER), bars=bars)
            PostgresWalkForwardStoreV1(database).record(report)
            draft = PreregistrationV1(study=study, rerun=rerun, symbol="BTCUSDT")
            PostgresHoldoutRegistryV1(database).record_preregistration(draft)
            criteria = ExitCriteriaPacketV1(cycle_id="cycle-2026-08-20", study_id=str(study.study_id),
                                            trial_id=selected[0], preregistration_hash=draft.content_hash,
                                            symbol="BTCUSDT")  # no owner value: DRAFT
            PostgresExitCriteriaRegistryV1(database).record(criteria)

            queries = PostgresOperatorDashboardQueries(database)
            walk = queries.terminal_walk_forward_report(report.report_hash)
            self.assertEqual((study.study_id, False), (walk.study_id, walk.promotable))
            self.assertIn(report.report_hash, {view.report_hash for view in
                                               queries.terminal_walk_forward(study_id=study.study_id)})
            with self.assertRaises(DashboardObjectNotFound):
                queries.terminal_walk_forward_report("0" * 64)

            view = queries.terminal_candidate(study.study_id, UUID(selected[0]))
            self.assertEqual((rerun.rerun_hash, draft.content_hash, "DRAFT", False, None),
                             (view.links.rerun_hash, view.links.preregistration_hash,
                              view.links.preregistration_status, view.links.holdout_opened_for_this_preregistration,
                              view.links.validation_hash))
            self.assertEqual([report.report_hash], [item.report_hash for item in view.links.walk_forward])
            self.assertEqual((STAGE_DECIMAL_SELECTION_ESTABLISHED, "DECIMAL_SELECTION_ESTABLISHED",
                              "GROSS_NON_PROMOTABLE", "NONE_VALIDATED"),
                             (view.readiness.stage, view.claim, view.economics, view.validated_claim))
            # The route assesses exactly what the readiness module assesses on the same stored rows.
            with database.transaction() as connection, connection.cursor() as cursor:
                cost = cycle_cost_policy_v1(cursor, "cycle-2026-08-20")
            expected = assess_candidate_readiness_v1(CandidateEvidenceV1(
                study_id=str(study.study_id), trial_id=selected[0], rerun_identity=rerun.identity,
                rerun_hash=rerun.rerun_hash, packet_identity=draft.identity(), packet_hash=draft.content_hash),
                cost_policy=cost, as_of=view.as_of)
            self.assertEqual(list(expected.blockers), view.readiness.blockers)
            self.assertTrue({"OR-6", "OR-7", "OR-11"} <= set(view.readiness.owner_gates))
            self.assertEqual(("DRAFT", criteria.content_hash, None, STAGE_UNRESOLVED),
                             (view.exit_criteria.status, view.exit_criteria.criteria_hash,
                              view.exit_criteria.authorized_by, view.exit_criteria.stage))
            self.assertTrue(all(value == "UNRESOLVED" for value in view.exit_criteria.thresholds.values()))
            self.assertIn("MISSING_OWNER_AUTHORIZATION", view.exit_criteria.unresolved)
            self.assertEqual([NOT_LOADED_RUNNER_LIVENESS, NOT_LOADED_PARITY, NOT_LOADED_ACCOUNT_POLICY],
                             view.evidence_not_loaded)

            other = queries.terminal_candidate(study.study_id, UUID(unselected))
            self.assertEqual((STAGE_SEARCH_ONLY, "SEARCH_NON_AUTHORITATIVE", "NOT_RECORDED", None),
                             (other.readiness.stage, other.claim, other.exit_criteria.status,
                              other.links.preregistration_hash))
            self.assertIn((study.study_id, UUID(selected[0])),
                          {(item.links.study_id, item.links.trial_id) for item in queries.terminal_candidates(limit=100)})
            with self.assertRaises(DashboardObjectNotFound):
                queries.terminal_candidate(uuid4(), uuid4())
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
