"""Phase R4.5 -- Strategy Lab read model over real PostgreSQL (synthetic studies only)."""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from uuid import uuid4

from tests.test_strategy_lab_ledger_v1_postgres import _Clock, _study
from tests.test_strategy_lab_manifest_v1_postgres import tie_evaluator


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class StrategyLabReadModelPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_study_detail_reflects_ledger_manifest_and_candidates(self) -> None:
        from trade_platform.operator_dashboard import (
            DashboardObjectNotFound,
            PostgresOperatorDashboardQueries,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_manifest_v1 import (
            CandidateSelectionRuleV1,
            PostgresStrategyLabManifestStoreV1,
            RankDirectionV1,
            build_study_manifest_v1,
            freeze_candidates_v1,
        )
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            clock = _Clock(datetime(2026, 9, 4, 12, tzinfo=UTC))
            ledger = PostgresStrategyLabLedgerV1(database, clock=clock)
            spec = _study(6)
            run_trial_worker_v1(ledger, spec, tie_evaluator, worker="read", clock=clock)
            manifest = build_study_manifest_v1(ledger, spec)
            store = PostgresStrategyLabManifestStoreV1(database)
            store.record_manifest(manifest)
            chosen = freeze_candidates_v1(manifest, CandidateSelectionRuleV1("score", RankDirectionV1.HIGHER_IS_BETTER, 2))
            store.record_candidate_set(chosen)

            queries = PostgresOperatorDashboardQueries(database)
            detail = queries.strategy_lab_study(spec.study_id)
            self.assertEqual(detail.summary.planned_trial_count, 6)
            self.assertEqual(detail.summary.queue_states, {"SUCCEEDED": 6})
            self.assertEqual(detail.summary.result_count, 6)
            self.assertEqual(detail.summary.authority.authority_status, "NON_AUTHORITATIVE")
            self.assertFalse(detail.summary.authority.promotable)
            self.assertEqual(detail.summary.authority.reasons, list(spec.authority().reasons))
            self.assertEqual([m.manifest_hash for m in detail.manifests], [manifest.manifest_hash])
            self.assertEqual(detail.manifests[0].outcomes, {"EVALUATED": 5, "INADMISSIBLE_PARAMETERS": 1})
            (candidate_set,) = detail.candidate_sets
            self.assertEqual(candidate_set.candidate_set_hash, chosen.candidate_set_hash)
            self.assertEqual(candidate_set.authoritative_rerun, "PENDING_OWNER_DECISION_OR_3")
            self.assertEqual(candidate_set.multiple_testing_trial_count, 6)
            page = queries.strategy_lab_studies(limit=100, offset=0)
            self.assertIn(spec.study_id, [item.study_id for item in page.items])
            with self.assertRaises(DashboardObjectNotFound):
                queries.strategy_lab_study(uuid4())
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
