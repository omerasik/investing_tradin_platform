"""Frozen-candidate comparison over the real Strategy Lab tables (real PostgreSQL, fixture data only).

The study, frozen set, Decimal rerun and walk-forward report are deterministic, so a rerun against a
shared database is idempotent; nothing REJECTED and no feature definition is written.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class CandidateComparisonPostgresTests(unittest.TestCase):
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
        self.temp = Path(tempfile.mkdtemp(prefix="comparison-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_recorded_evidence_re_derives_and_projects_per_dimension(self) -> None:
        from tests.test_activation_readiness_v1_postgres import recorded_established_study
        from tests.test_research_terminal_e2e_acceptance_postgres import DAY0, H
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.research_candidate_comparison_v1 import (
            CandidateComparisonNotFound,
            ComparisonSortV1,
            SortDimensionV1,
            StudySelectionV1,
            read_candidate_comparison_v1,
        )
        from trade_platform.strategy_lab_manifest_v1 import RankDirectionV1
        from trade_platform.strategy_lab_splits_v1 import SplitModeV1, WalkForwardPlanV1
        from trade_platform.strategy_lab_walk_forward_v1 import (
            PostgresWalkForwardStoreV1,
            WalkForwardSelectionRuleV1,
            run_walk_forward_v1,
        )
        from trade_platform.strategy_sdk_v1 import load_bars_v1

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            fixture = recorded_established_study(self.temp, database)
            study, rerun, candidates = fixture["study"], fixture["rerun"], fixture["candidates"]
            (binding,) = study.datasets
            bars = load_bars_v1(fixture["data_root"], binding.dataset_version_id,
                                expected_content_hash=binding.content_hash)
            plan = WalkForwardPlanV1(mode=SplitModeV1.ROLLING, train_duration=12 * H, test_duration=6 * H,
                                     step=6 * H, embargo=timedelta(minutes=10), first_test_start=DAY0 + 12 * H,
                                     evaluation_end_exclusive=DAY0 + 48 * H)
            report = run_walk_forward_v1(
                study, plan, WalkForwardSelectionRuleV1("total_return", RankDirectionV1.HIGHER_IS_BETTER), bars=bars)
            PostgresWalkForwardStoreV1(database).record(report)

            sort = ComparisonSortV1(SortDimensionV1.DECIMAL_BASELINE, "trades", "DESC")
            with database.transaction() as connection, connection.cursor() as cursor:
                result = read_candidate_comparison_v1(
                    cursor, studies=[StudySelectionV1(study.study_id, candidates.candidate_set_hash)], sort=sort)
                again = read_candidate_comparison_v1(
                    cursor, studies=[StudySelectionV1(study.study_id, candidates.candidate_set_hash)], sort=sort)
                from dataclasses import replace

                from tests.test_research_account_fee_evidence_v1 import binding_for

                fee_binding = replace(binding_for(study.content_hash, candidates.candidate_set_hash,
                    tuple(d.content_hash for d in study.datasets)), observation_start=DAY0.isoformat(),
                    observation_end_exclusive=study.evaluation_upper_bound_exclusive.isoformat())
                with_fees = read_candidate_comparison_v1(cursor,
                    studies=[StudySelectionV1(study.study_id, candidates.candidate_set_hash)], sort=sort,
                    fee_bindings={study.content_hash: fee_binding})
                self.assertEqual([row.identity for row in result.rows], [row.identity for row in with_fees.rows])
                for row in with_fees.rows:
                    self.assertEqual("VERIFIED", row.cost_fragility.verified_account_fees)
                    self.assertEqual(fee_binding.evidence_hash, row.cost_fragility.account_fee_evidence.evidence_hash)
                    self.assertIsNotNone(row.cost_fragility.account_fee_evidence.remaining_after_fee_bps_per_side)
                    self.assertEqual("NOT_ASSERTED", row.cost_fragility.account_fee_evidence.owner_approval)
                    self.assertFalse(row.promotable)
                    self.assertEqual("NONE", row.execution_authority)
                with self.assertRaises(CandidateComparisonNotFound):
                    read_candidate_comparison_v1(cursor, studies=[StudySelectionV1(uuid4())], sort=None)
            self.assertEqual(result.output_hash, again.output_hash)
            self.assertEqual(len(candidates.candidates), len(result.rows))
            selected = {str(item["trial_id"]) for item in rerun.identity["authoritative_selection"]["selected"]}
            for row in result.rows:
                self.assertEqual((str(study.study_id), study.content_hash, candidates.candidate_set_hash),
                                 (row.identity.study_id, row.identity.study_content_hash,
                                  row.identity.candidate_set_hash))
                self.assertEqual(("DECIMAL_SELECTION_ESTABLISHED", rerun.rerun_hash),
                                 (row.decimal_rerun.claim, row.decimal_rerun.rerun_hash))
                self.assertEqual(row.identity.trial_id in selected, row.decimal_rerun.in_decimal_selection)
                self.assertEqual([], row.decimal_rerun.sweep_lags_missing)
                self.assertIn(report.report_hash, {r.report_hash for r in row.walk_forward.reports})
                self.assertEqual("GROSS_ONLY", row.economics.completeness)
                baseline_metrics = {m.key: m.value for m in row.decimal_rerun.baseline.metrics}
                self.assertEqual(baseline_metrics["break_even_bps_per_side"],
                                 row.cost_fragility.threshold_bps_per_side)
                self.assertEqual("UNAVAILABLE", row.cost_fragility.verified_account_fees)
                self.assertTrue(all(not s.in_net for s in row.cost_fragility.scenarios))
                self.assertEqual(binding.content_hash, row.provenance.datasets[0].content_hash)
                self.assertFalse(row.promotable)
                self.assertEqual("VALUE", row.sort_state)
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
