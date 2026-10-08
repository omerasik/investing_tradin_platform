"""Phase R4.6 -- a policy-bound SDK study through the real ledger, worker, manifest and freeze.

Fixture archive days only (synthetic bytes, no network). The study identity is
deterministic, so a rerun against a shared database is a no-op resume; the
assertions hold either way. No REJECTED row or feature definition is written.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class StrategySdkPostgresTests(unittest.TestCase):
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
        self.temp = Path(tempfile.mkdtemp(prefix="sdk-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_policy_bound_study_runs_records_and_freezes(self) -> None:
        from tests.test_strategy_lab_e2e_fixture import build_window_and_study
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_manifest_v1 import (
            CandidateSelectionRuleV1,
            PostgresStrategyLabManifestStoreV1,
            RankDirectionV1,
            build_study_manifest_v1,
            freeze_candidates_v1,
        )
        from trade_platform.strategy_lab_policies_v1 import (
            gross_cost_policy_v1,
            or3_numeric_policy_v1,
        )
        from trade_platform.strategy_lab_study_v1 import ParameterDomainV1, ParameterSpaceV1
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1
        from trade_platform.strategy_sdk_v1 import BarStrategyEvaluatorV1

        space = ParameterSpaceV1.of(
            ParameterDomainV1.integer_values("entry_bars", [60, 120]),
            ParameterDomainV1.integer_values("exit_bars", [15, 120]),
            ParameterDomainV1.categorical_set("direction", ["long_only", "long_short"]),
        )
        study, data_root = build_window_and_study(self.temp, family="breakout_channel", space=space)
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            ledger = PostgresStrategyLabLedgerV1(database)
            run_trial_worker_v1(ledger, study, BarStrategyEvaluatorV1(data_root), worker="sdk-pg-test")
            progress = ledger.progress(study.study_id)
            self.assertTrue(progress.finished)
            self.assertEqual(8, progress.results)
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT numeric_policy_slot, cost_policy_slot FROM strategy_lab_studies "
                               "WHERE study_id=%s", (study.study_id,))
                self.assertEqual((or3_numeric_policy_v1().slot, gross_cost_policy_v1().policy().slot),
                                 cursor.fetchone())
                cursor.execute("SELECT DISTINCT cost_policy_slot, numeric_tier FROM strategy_lab_trial_results "
                               "WHERE study_id=%s", (study.study_id,))
                self.assertEqual([(gross_cost_policy_v1().policy().slot, "SEARCH_NON_AUTHORITATIVE")],
                                 cursor.fetchall())
            manifest = build_study_manifest_v1(ledger, study)
            store = PostgresStrategyLabManifestStoreV1(database)
            store.record_manifest(manifest)
            outcomes = manifest.identity["outcomes"]
            # exit 120 is not shorter than entry 60 or 120 (x2 directions).
            self.assertEqual(4, outcomes["INADMISSIBLE_PARAMETERS"])
            candidates = freeze_candidates_v1(
                manifest, CandidateSelectionRuleV1("trades", RankDirectionV1.HIGHER_IS_BETTER, 2))
            store.record_candidate_set(candidates)
            self.assertEqual(8, candidates.identity["multiple_testing_trial_count"])
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
