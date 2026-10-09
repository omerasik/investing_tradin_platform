"""Activation readiness and the OR-9 watch list over the real R4-R10 tables (real PostgreSQL).

Fixture archive days only. The real cycle-2026-08-20 is never opened: readiness
for it must stay BLOCKED on OR-7. No REJECTED row or feature definition is written.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


def recorded_established_study(temp: Path, database: Any, *, family: str = "breakout_channel") -> dict[str, Any]:
    """A fixture study searched, frozen and Decimal-reran through the real stores (idempotent)."""
    from tests.test_strategy_lab_e2e_fixture import build_window_and_study
    from trade_platform.strategy_lab_authority_rerun_v1 import (
        PostgresAuthorityRerunStoreV1,
        run_authority_rerun_v1,
    )
    from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
    from trade_platform.strategy_lab_manifest_v1 import (
        CandidateSelectionRuleV1,
        PostgresStrategyLabManifestStoreV1,
        RankDirectionV1,
        build_study_manifest_v1,
        freeze_candidates_v1,
    )
    from trade_platform.strategy_lab_study_v1 import ParameterDomainV1, ParameterSpaceV1
    from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1
    from trade_platform.strategy_sdk_v1 import BarStrategyEvaluatorV1

    space = ParameterSpaceV1.of(
        ParameterDomainV1.integer_values("entry_bars", [60, 120]),
        ParameterDomainV1.integer_values("exit_bars", [15, 30]),
        ParameterDomainV1.categorical_set("direction", ["long_only", "long_short"]),
    )
    study, data_root = build_window_and_study(temp, family=family, space=space)
    ledger = PostgresStrategyLabLedgerV1(database)
    run_trial_worker_v1(ledger, study, BarStrategyEvaluatorV1(data_root), worker="readiness-pg-test")
    manifest = build_study_manifest_v1(ledger, study)
    store = PostgresStrategyLabManifestStoreV1(database)
    store.record_manifest(manifest)
    candidates = freeze_candidates_v1(manifest, CandidateSelectionRuleV1("trades", RankDirectionV1.HIGHER_IS_BETTER, 2))
    store.record_candidate_set(candidates)
    rerun = run_authority_rerun_v1(study, manifest, candidates, data_root=data_root)
    PostgresAuthorityRerunStoreV1(database).record(rerun)
    return {"study": study, "data_root": data_root, "manifest": manifest, "candidates": candidates, "rerun": rerun}


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ActivationReadinessPostgresTests(unittest.TestCase):
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
        self.temp = Path(tempfile.mkdtemp(prefix="readiness-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_readiness_derives_every_answer_and_the_watch_list_is_owner_recorded(self) -> None:
        from trade_platform.account_policy_v1 import (
            AccountContextV1,
            AccountKindV1,
            AccountPolicyV1,
            PostgresAccountPolicyStoreV1,
        )
        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.research_watchlist_v1 import (
            PostgresResearchWatchlistStoreV1,
            ResearchWatchlistError,
            ResearchWatchlistV1,
            WatchEntryV1,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            fixture = recorded_established_study(self.temp, database)
            study, rerun = fixture["study"], fixture["rerun"]
            self.assertEqual("ESTABLISHED", rerun.selection_status)  # deterministic fixture
            accounts = PostgresAccountPolicyStoreV1(database)
            account = AccountContextV1(f"act-{uuid4().hex[:8]}", AccountKindV1.PERSONAL_PAPER, "Activation", "USDT")
            accounts.register_account(account)
            accounts.record_policy(AccountPolicyV1(account, {}))

            queries = PostgresOperatorDashboardQueries(database)
            readiness = queries.terminal_activation()
            answers = {answer.key: answer for answer in readiness.answers}
            self.assertEqual(["research_run", "candidate_freeze", "decimal_rerun", "holdout_open", "research_watch",
                              "paper_incubation"], [a.key for a in readiness.answers])
            self.assertEqual(("cycle-2026-08-20", "UNOPENED"), (readiness.cycle_id, readiness.holdout_state))
            mine = {s.identities.get("study_id"): s for s in answers["decimal_rerun"].subjects}
            self.assertEqual("DONE", mine[str(study.study_id)].status)
            self.assertEqual(rerun.rerun_hash, mine[str(study.study_id)].identities["rerun_hash"])
            frozen = {s.identities.get("study_id"): s for s in answers["candidate_freeze"].subjects}
            self.assertEqual("DONE", frozen[str(study.study_id)].status)
            # The real holdout is unopened: holdout, watch and paper all wait on OR-7.
            self.assertEqual("BLOCKED", answers["holdout_open"].status)
            self.assertIn("OR-7", answers["holdout_open"].owner_gates)
            self.assertIn("OR-7", answers["research_watch"].owner_gates)
            self.assertEqual("BLOCKED", answers["paper_incubation"].status)
            self.assertIn("OR-7", answers["paper_incubation"].owner_gates)
            mine_account = next(s for s in answers["paper_incubation"].subjects
                                if s.identities["account_id"] == account.account_id)
            self.assertTrue(mine_account.reasons)
            self.assertTrue(all(r.startswith("BLOCKED_OWNER_DECISION_OR_11:") for r in mine_account.reasons))
            self.assertTrue(set(readiness.owner_gates_open) >= {"OR-7"})
            self.assertEqual(readiness.state_hash, queries.terminal_activation().state_hash)  # deterministic

            # OR-9: only the owner's explicit, approved selection from an ESTABLISHED rerun.
            selected = [UUID(str(item["trial_id"])) for item in rerun.identity["authoritative_selection"]["selected"]]
            watch = PostgresResearchWatchlistStoreV1(database)
            outside = next(t.trial_id for t in study.trials() if t.trial_id not in selected)
            with self.assertRaisesRegex(ResearchWatchlistError, "WATCH_TRIAL_NOT_IN_THE_DECIMAL_SELECTION"):
                watch.record(ResearchWatchlistV1("readiness-watch", (
                    WatchEntryV1(study.study_id, rerun.rerun_hash, outside, "BTCUSDT"),), "pg-test", "2026-10-09"))
            entry = WatchEntryV1(study.study_id, rerun.rerun_hash, selected[0], "BTCUSDT")
            watch.record(ResearchWatchlistV1("readiness-watch", (entry,)))  # DRAFT (a no-op on a rerun)
            approved = ResearchWatchlistV1("readiness-watch", (entry,), "pg-test", "2026-10-09")
            watch.record(approved)
            self.assertFalse(watch.record(approved))  # idempotent
            self.assertEqual(approved.content_hash, watch.latest_active("readiness-watch").content_hash)
            after = {a.key: a for a in queries.terminal_activation().answers}["research_watch"]
            self.assertEqual("BLOCKED", after.status)  # forward bars still wait on the real holdout
            self.assertNotIn("OR-9", after.owner_gates)
            self.assertEqual(approved.content_hash, after.identities["watchlist_hash"])
            self.assertEqual(["READY"], [s.status for s in after.subjects])
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
