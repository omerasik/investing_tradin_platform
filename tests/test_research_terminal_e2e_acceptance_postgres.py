"""UI-2 acceptance -- one read-only path through the terminal over the real R4-R10 tables (real PostgreSQL).

Market Data -> Strategy Lab -> Walk-Forward -> Candidate Readiness -> Risk Preview
-> Paper Incubation Report -> Monitoring, each step read from what the previous one
recorded, with its identity carried across the handoff and its claim restated.

Fixture archive days only (2026-06, before the holdout). The real cycle-2026-08-20
is never opened, no packet is authorized, no fill or liveness record is written and
nothing incubates: every later step must therefore name its blocker. No REJECTED row
and no feature definition is written; the account id is unique per run.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

REAL_CYCLE = "cycle-2026-08-20"
DAY0 = datetime(2026, 6, 1, tzinfo=UTC)
H = timedelta(hours=1)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ResearchTerminalAcceptancePostgresTests(unittest.TestCase):
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
        self.temp = Path(tempfile.mkdtemp(prefix="ui2-acceptance-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_the_operator_walks_the_whole_path_and_every_missing_gate_is_named(self) -> None:
        from fastapi.testclient import TestClient

        from tests.test_activation_readiness_v1_postgres import recorded_established_study
        from tests.test_runner_liveness_v1 import BOUNDS, acceptance
        from trade_platform.account_policy_v1 import (
            AccountContextV1,
            AccountKindV1,
            AccountPolicyV1,
            PostgresAccountPolicyStoreV1,
        )
        from trade_platform.api import build_app
        from trade_platform.audit import SQLiteAuditStore
        from trade_platform.candidate_lifecycle_readiness_v1 import (
            NEED_HOLDOUT_OPENING,
            NEED_LIVENESS,
            STAGE_DECIMAL_SELECTION_ESTABLISHED,
            STAGE_SEARCH_ONLY,
            CandidateEvidenceV1,
            assess_candidate_readiness_v1,
        )
        from trade_platform.config import PlatformConfig
        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
        from trade_platform.paper_account_v1 import cycle_cost_policy_v1
        from trade_platform.paper_incubation_registry_v1 import (
            REGISTRY_SOURCE_V1,
            UNRESOLVED_HOLDOUT_NOT_OPENED,
        )
        from trade_platform.paper_incubation_report_v1 import UNRESOLVED_OR7_INCUBATION_DAYS
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.research_terminal_read_model_v1 import read_walk_forward_v1
        from trade_platform.runner_liveness_v1 import (
            PostgresRunnerLivenessStoreV1,
            derive_runner_liveness_v1,
        )
        from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator
        from trade_platform.strategy_lab_manifest_v1 import RankDirectionV1
        from trade_platform.strategy_lab_splits_v1 import SplitModeV1, WalkForwardPlanV1
        from trade_platform.strategy_lab_study_v1 import identity_hash_v1
        from trade_platform.strategy_lab_validation_v1 import (
            UNRESOLVED_AUTHORIZATION,
            UNRESOLVED_COST_BASIS,
            PostgresHoldoutRegistryV1,
            PreregistrationV1,
            StrategyLabValidationError,
        )
        from trade_platform.strategy_lab_walk_forward_v1 import (
            PostgresWalkForwardStoreV1,
            WalkForwardSelectionRuleV1,
            run_walk_forward_v1,
        )
        from trade_platform.strategy_sdk_v1 import load_bars_v1

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            queries = PostgresOperatorDashboardQueries(database)
            client = TestClient(build_app(
                PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"),
                InMemoryRateLimiter(max_requests=1000), operator_dashboard_queries=queries))
            headers = {"Authorization": "Bearer test-token"}

            def get(path: str) -> Any:
                response = client.get(f"/operator-dashboard/{path}", headers=headers)
                self.assertEqual(200, response.status_code, f"{path}: {response.text[:500]}")
                return response.json()

            # 1. Market data -> Strategy Lab: the study binds the window by content hash; the search,
            #    the frozen set and the Decimal rerun all name that study.
            fixture = recorded_established_study(self.temp, database)
            study, rerun, candidates = fixture["study"], fixture["rerun"], fixture["candidates"]
            (binding,) = study.datasets
            bars = load_bars_v1(fixture["data_root"], binding.dataset_version_id,
                                expected_content_hash=binding.content_hash)
            self.assertEqual(study.content_hash, fixture["manifest"].identity["study_content_hash"])
            self.assertEqual((study.content_hash, candidates.candidate_set_hash),
                             (rerun.identity["study_content_hash"], rerun.identity["candidate_set_hash"]))
            self.assertEqual("ESTABLISHED", rerun.selection_status)
            selected = [str(item["trial_id"]) for item in rerun.identity["authoritative_selection"]["selected"]]
            unselected = next(str(t.trial_id) for t in study.trials() if str(t.trial_id) not in selected)

            detail = get(f"strategy-lab/studies/{study.study_id}")
            self.assertEqual(study.content_hash, detail["summary"]["content_hash"])
            self.assertEqual(("NON_AUTHORITATIVE", False), (detail["summary"]["authority"]["authority_status"],
                                                            detail["summary"]["authority"]["promotable"]))
            self.assertEqual(binding.content_hash, detail["summary"]["datasets"][0]["content_hash"])
            frozen = {item["candidate_set_hash"]: item for item in detail["candidate_sets"]}
            self.assertEqual("SEARCH_NON_AUTHORITATIVE", frozen[candidates.candidate_set_hash]["numeric_tier"])
            self.assertEqual(("UNAVAILABLE", "UNAVAILABLE"), (detail["economics"]["verified_costs"],
                                                              detail["economics"]["net_profitability"]))
            self.assertEqual("UNOPENED", detail["holdout"]["state"])

            self.assertIn(rerun.rerun_hash, {item["rerun_hash"] for item in detail["reruns"]})
            reruns = {item["rerun_hash"]: item for item in get("research-terminal/reruns?limit=200")}
            mine = reruns[rerun.rerun_hash]
            self.assertEqual((str(study.study_id), candidates.candidate_set_hash),
                             (mine["study_id"], mine["candidate_set_hash"]))
            self.assertEqual(("DECIMAL_SELECTION_ESTABLISHED", "GROSS_CONDITIONAL_NON_PROMOTABLE"),
                             (mine["claim"], mine["economics"]))
            self.assertEqual(selected, [item["trial_id"] for item in mine["selected"]])

            # 2. Walk-forward over the same bars: development evidence of this study and this dataset.
            plan = WalkForwardPlanV1(mode=SplitModeV1.ROLLING, train_duration=12 * H, test_duration=6 * H,
                                     step=6 * H, embargo=timedelta(minutes=10), first_test_start=DAY0 + 12 * H,
                                     evaluation_end_exclusive=DAY0 + 48 * H)
            report = run_walk_forward_v1(
                study, plan, WalkForwardSelectionRuleV1("total_return", RankDirectionV1.HIGHER_IS_BETTER), bars=bars)
            PostgresWalkForwardStoreV1(database).record(report)
            with database.transaction() as connection, connection.cursor() as cursor:
                views = {view.report_hash: view for view in read_walk_forward_v1(cursor, limit=500)}
            walk = views[report.report_hash]
            self.assertEqual((study.study_id, study.content_hash, binding.content_hash),
                             (walk.study_id, walk.study_content_hash, walk.dataset_content_hash))
            self.assertEqual(("WALK_FORWARD_DEVELOPMENT", "SEARCH_NON_AUTHORITATIVE", "GROSS_NON_PROMOTABLE", False),
                             (walk.evaluation_class, walk.numeric_tier, walk.cost_mode, walk.promotable))
            self.assertEqual(study.planned_trial_count, walk.multiple_testing_trial_count)
            trial_ids = {t.trial_id for t in study.trials()}
            self.assertTrue(walk.folds)
            self.assertTrue(all(fold.selected_trial_id in trial_ids for fold in walk.folds
                                if fold.selected_trial_id is not None))

            # 3. Candidate readiness from the stored rows: the prepared DRAFT packet re-derives and
            #    names each owner gap; the unopened holdout is a blocker, never a default.
            draft = PreregistrationV1(study=study, rerun=rerun, symbol="BTCUSDT")
            self.assertEqual("DRAFT", draft.status)
            registry = PostgresHoldoutRegistryV1(database)
            registry.record_preregistration(draft)
            with self.assertRaises(StrategyLabValidationError):
                registry.open_holdout(draft, opened_by="ui2-acceptance")  # a draft is never opened
            self.assertIsNone(registry.opening(REAL_CYCLE))
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT identity FROM strategy_lab_authority_reruns WHERE rerun_hash=%s",
                               (rerun.rerun_hash,))
                stored_rerun = _row_json(cursor.fetchone())
                cursor.execute("SELECT identity FROM strategy_lab_preregistrations WHERE preregistration_hash=%s",
                               (draft.content_hash,))
                stored_packet = _row_json(cursor.fetchone())
                cost = cycle_cost_policy_v1(cursor, REAL_CYCLE)
            self.assertEqual(rerun.rerun_hash, identity_hash_v1(stored_rerun))
            self.assertEqual(draft.content_hash, identity_hash_v1(stored_packet))
            self.assertEqual(rerun.rerun_hash, stored_packet["rerun_hash"])
            self.assertEqual("GROSS_NON_PROMOTABLE", cost.mode)  # no verified fee schedule (OR-6)

            accounts = PostgresAccountPolicyStoreV1(database)
            account = AccountContextV1(f"ui2-acc-{uuid4().hex[:8]}", AccountKindV1.PERSONAL_PAPER, "UI-2", "USDT")
            accounts.register_account(account)
            accounts.record_policy(AccountPolicyV1(account, {}))  # nothing decided: UNCONFIGURED
            account_view = next(item for item in get("research-terminal/accounts")
                                if item["account_id"] == account.account_id)
            stored_account = {"status": account_view["policy_status"], "unresolved": account_view["unresolved"]}

            as_of = datetime.now(UTC)
            ready = assess_candidate_readiness_v1(CandidateEvidenceV1(
                study_id=str(study.study_id), trial_id=selected[0], rerun_identity=stored_rerun,
                rerun_hash=rerun.rerun_hash, packet_identity=stored_packet, packet_hash=draft.content_hash,
                account_policy=stored_account), cost_policy=cost, as_of=as_of)
            self.assertEqual(STAGE_DECIMAL_SELECTION_ESTABLISHED, ready.stage)
            self.assertIsNone(ready.canonical_state)
            for blocker in (f"BLOCKED_OWNER_DECISION_OR_7:{UNRESOLVED_AUTHORIZATION}",
                            f"BLOCKED_OWNER_DECISION_OR_6:{UNRESOLVED_COST_BASIS}", NEED_HOLDOUT_OPENING,
                            NEED_LIVENESS):
                self.assertIn(blocker, ready.blockers)
            self.assertTrue({"OR-6", "OR-7", "OR-11"} <= set(ready.owner_gates))
            search_only = assess_candidate_readiness_v1(CandidateEvidenceV1(
                study_id=str(study.study_id), trial_id=unselected, rerun_identity=stored_rerun,
                rerun_hash=rerun.rerun_hash), cost_policy=cost, as_of=as_of)
            self.assertEqual(STAGE_SEARCH_ONLY, search_only.stage)
            self.assertEqual("CANDIDATE_NOT_IN_THE_DECIMAL_SELECTION", search_only.blockers[0])

            activation = {item["key"]: item for item in get("research-terminal/activation")["answers"]}
            rerun_subjects = {s["identities"].get("study_id"): s for s in activation["decimal_rerun"]["subjects"]}
            self.assertEqual(rerun.rerun_hash, rerun_subjects[str(study.study_id)]["identities"]["rerun_hash"])
            self.assertEqual("BLOCKED", activation["holdout_open"]["status"])
            self.assertIn("OR-7", activation["holdout_open"]["owner_gates"])

            # 4. Risk preview: an UNCONFIGURED policy yields no risk policy and no paper ledger.
            self.assertEqual("UNCONFIGURED", account_view["policy_status"])
            self.assertTrue(account_view["unresolved"])
            self.assertTrue(all(reason.endswith("_OR_11") for reason in account_view["unresolved"]))
            for reason in account_view["unresolved"]:
                self.assertIn(f"BLOCKED_OWNER_DECISION_OR_11:{reason}", ready.blockers)
            ledger = get(f"research-terminal/paper-account?account_id={account.account_id}")
            risk = ledger.pop("risk")
            self.assertEqual({"state": "BLOCKED", "account_id": account.account_id,
                              "reasons": ["BLOCKED_OWNER_DECISION_OR_11:NO_ACTIVE_ACCOUNT_POLICY"]}, ledger)
            # Its authoritative policy resolution is UNRESOLVED (never ACTIVE) with every missing owner value.
            self.assertEqual(("UNRESOLVED", "UNCONFIGURED", False),
                             (risk["current_policy"]["status"], risk["current_policy"]["policy_status"],
                              risk["execution_authority"]))
            for reason in account_view["unresolved"]:
                self.assertIn(reason, risk["current_policy"]["unresolved"])
            self.assertTrue(all(item["sizing"] is None for item in risk["candidates"]))

            # 5. Paper incubation report: registry-resolved, unopened, gross, never cost-complete.
            incubation = get("research-terminal/incubation")["report"]
            self.assertEqual({"cycle_id": REAL_CYCLE, "source": REGISTRY_SOURCE_V1, "bound_candidates": 0,
                              "unresolved": [UNRESOLVED_HOLDOUT_NOT_OPENED]}, incubation["preregistration_registry"])
            self.assertEqual((False, "NOT_MODELLED", "PAPER_INCUBATION_NOT_VALIDATED", "INCUBATING"),
                             (incubation["cost_complete"], incubation["funding"], incubation["claim"],
                              incubation["state"]))
            self.assertEqual(cost.policy().content_hash, incubation["cost_policy_hash"])
            self.assertNotIn(str(study.study_id), {item["study_id"] for item in incubation["candidates"]})
            self.assertTrue(all(item["required_days"] == UNRESOLVED_OR7_INCUBATION_DAYS
                                for item in incubation["candidates"]))

            # 6. Monitoring: no signal and no runner progress for this candidate -> nothing proven.
            self.assertNotIn(str(study.study_id), {item["study_id"] for item in
                                                   get("research-terminal/signals?limit=500")})
            records = PostgresRunnerLivenessStoreV1(database).for_candidate(
                study_id=str(study.study_id), trial_id=selected[0], symbol="BTCUSDT")
            self.assertEqual([], records)
            window_start = datetime(2026, 10, 1, tzinfo=UTC)
            liveness = derive_runner_liveness_v1(
                records, study_id=str(study.study_id), trial_id=selected[0], symbol="BTCUSDT",
                window_start=window_start, window_end=window_start + 2 * H, acceptance=acceptance(), sealed=[],
                **BOUNDS)
            self.assertEqual(0, liveness.proven_micros)
            self.assertEqual({"NO_RUNNER_PROGRESS": 2 * H // timedelta(microseconds=1)},
                             liveness.unproven_micros_by_reason)

            # The overview restates every open gate; nothing on the path was decided by the terminal.
            overview = get("research-terminal/overview")
            self.assertEqual((REAL_CYCLE, "UNOPENED", False), (overview["cycle"]["cycle_id"],
                                                               overview["cycle"]["holdout_state"],
                                                               overview["cycle"]["validated"]))
            gates = {gate["gate"]: gate for gate in overview["owner_gates"]}
            self.assertEqual("OPEN", gates["OR-7"]["status"])
            self.assertEqual("OPEN", gates["OR-6 fee schedule"]["status"])
            self.assertGreaterEqual(overview["cycle"]["preregistrations"].get("DRAFT", 0), 1)
            self.assertEqual("NONE_VALIDATED", get("research-terminal/validation?limit=500")["validated_claim"])
            self.assertIsNone(registry.opening(REAL_CYCLE), "the real holdout must stay closed")
        finally:
            database.close()


def _row_json(row: tuple[Any, ...] | None) -> dict[str, Any]:
    import json

    assert row is not None
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


if __name__ == "__main__":
    unittest.main()
