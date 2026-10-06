"""Phase R4.4 -- study manifests and frozen candidate sets against real PostgreSQL.

Synthetic studies only (see test_strategy_lab_ledger_v1_postgres): random
implementation hash per study, past clock readings, no REJECTED rows.
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime

from tests.test_strategy_lab_ledger_v1_postgres import _Clock, _study


def tie_evaluator(study, parameters):  # type: ignore[no-untyped-def]
    """score = window // 2 as exact text: windows 2k and 2k+1 tie; window 5 is inadmissible."""
    from trade_platform.strategy_lab_ledger_v1 import TrialOutcomeV1
    from trade_platform.strategy_lab_worker_v1 import TrialEvaluationV1

    window = int(parameters["window"])
    if window == 5:
        return TrialEvaluationV1(TrialOutcomeV1.INADMISSIBLE_PARAMETERS, {})
    return TrialEvaluationV1(TrialOutcomeV1.EVALUATED, {"score": f"{window // 2}.0", "trades": window})


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class StrategyLabManifestPostgresTests(unittest.TestCase):
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

    def setUp(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_manifest_v1 import PostgresStrategyLabManifestStoreV1

        self.clock = _Clock(datetime(2026, 9, 3, 12, tzinfo=UTC))
        self.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.ledger = PostgresStrategyLabLedgerV1(self.database, clock=self.clock)
        self.store = PostgresStrategyLabManifestStoreV1(self.database)

    def tearDown(self) -> None:
        self.database.close()

    def _finished(self, trials: int = 8):  # type: ignore[no-untyped-def]
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1

        spec = _study(trials)
        run_trial_worker_v1(self.ledger, spec, tie_evaluator, worker="m", clock=self.clock)
        return spec

    def test_manifest_accounts_for_every_planned_trial_and_is_deterministic(self) -> None:
        from trade_platform.strategy_lab_manifest_v1 import build_study_manifest_v1

        spec = self._finished(8)
        manifest = build_study_manifest_v1(self.ledger, spec)
        self.assertEqual(manifest.planned_trial_count, 8)
        self.assertEqual(manifest.identity["outcomes"], {"EVALUATED": 7, "INADMISSIBLE_PARAMETERS": 1})
        self.assertEqual([r["ordinal"] for r in manifest.results], list(range(8)))
        self.assertEqual(manifest.identity["authority_reasons"][:2],
                         ["NUMERIC_POLICY_UNSET_PENDING_OR_3", "COST_POLICY_UNSET_PENDING_OR_6"])
        self.assertEqual(build_study_manifest_v1(self.ledger, spec).manifest_hash, manifest.manifest_hash)
        self.assertTrue(self.store.record_manifest(manifest))
        self.assertFalse(self.store.record_manifest(manifest))

    def test_unfinished_or_mismatched_studies_are_refused(self) -> None:
        from trade_platform.strategy_lab_ledger_v1 import StrategyLabLedgerError
        from trade_platform.strategy_lab_manifest_v1 import (
            StrategyLabManifestError,
            build_study_manifest_v1,
        )

        spec = _study(3)
        self.ledger.register_study(spec)
        with self.assertRaisesRegex(StrategyLabManifestError, "study_not_finished"):
            build_study_manifest_v1(self.ledger, spec)
        with self.assertRaisesRegex(StrategyLabLedgerError, "study_not_registered"):
            build_study_manifest_v1(self.ledger, _study(3))

    def test_freeze_ranks_exactly_breaks_ties_by_hash_and_flags_a_cutoff_tie(self) -> None:
        from trade_platform.strategy_lab_manifest_v1 import (
            CandidateSelectionRuleV1,
            RankDirectionV1,
            build_study_manifest_v1,
            freeze_candidates_v1,
        )

        spec = self._finished(8)  # windows 1..8; scores 0,1,1,2,(inadmissible),3,3,4
        manifest = build_study_manifest_v1(self.ledger, spec)
        top3 = freeze_candidates_v1(manifest, CandidateSelectionRuleV1("score", RankDirectionV1.HIGHER_IS_BETTER, 3))
        self.assertEqual([c["metric_value"] for c in top3.candidates], ["4", "3", "3"])
        self.assertFalse(top3.identity["cutoff_tie"])
        tied = [c["trial_content_hash"] for c in top3.candidates[1:]]
        self.assertEqual(tied, sorted(tied))
        top2 = freeze_candidates_v1(manifest, CandidateSelectionRuleV1("score", RankDirectionV1.HIGHER_IS_BETTER, 2))
        self.assertTrue(top2.identity["cutoff_tie"])
        self.assertEqual(top2.identity["multiple_testing_trial_count"], 8)
        self.assertEqual(top2.identity["ineligible_count"], 1)
        self.assertEqual(top2.identity["authoritative_rerun"], "PENDING_OWNER_DECISION_OR_3")
        low = freeze_candidates_v1(manifest, CandidateSelectionRuleV1("trades", RankDirectionV1.LOWER_IS_BETTER, 1))
        self.assertEqual(low.candidates[0]["metric_value"], "1")
        self.store.record_manifest(manifest)
        self.assertTrue(self.store.record_candidate_set(top3))
        self.assertFalse(self.store.record_candidate_set(top3))
        self.assertEqual([s.candidate_set_hash for s in self.store.candidate_sets(manifest.study_id)],
                         [top3.candidate_set_hash])

    def test_freeze_fails_closed_without_enough_eligible_results(self) -> None:
        from trade_platform.strategy_lab_manifest_v1 import (
            CandidateSelectionRuleV1,
            RankDirectionV1,
            StrategyLabManifestError,
            build_study_manifest_v1,
            freeze_candidates_v1,
        )

        manifest = build_study_manifest_v1(self.ledger, self._finished(6))
        with self.assertRaisesRegex(StrategyLabManifestError, "fewer_eligible"):
            freeze_candidates_v1(manifest, CandidateSelectionRuleV1("score", RankDirectionV1.HIGHER_IS_BETTER, 6))
        with self.assertRaisesRegex(StrategyLabManifestError, "fewer_eligible"):
            freeze_candidates_v1(manifest, CandidateSelectionRuleV1("missing", RankDirectionV1.HIGHER_IS_BETTER, 1))
        with self.assertRaises(StrategyLabManifestError):
            CandidateSelectionRuleV1("score", RankDirectionV1.HIGHER_IS_BETTER, 0)

    def test_database_keeps_candidate_sets_search_tier(self) -> None:
        from trade_platform.persistence import PersistenceError
        from trade_platform.strategy_lab_manifest_v1 import (
            CandidateSelectionRuleV1,
            RankDirectionV1,
            build_study_manifest_v1,
            freeze_candidates_v1,
        )

        manifest = build_study_manifest_v1(self.ledger, self._finished(4))
        self.store.record_manifest(manifest)
        chosen = freeze_candidates_v1(manifest, CandidateSelectionRuleV1("score", RankDirectionV1.HIGHER_IS_BETTER, 1))
        self.store.record_candidate_set(chosen)
        for sql in (
            "UPDATE strategy_lab_candidate_sets SET authoritative_rerun='DONE' WHERE candidate_set_hash=%s",
            "UPDATE strategy_lab_candidate_sets SET numeric_tier='AUTHORITATIVE' WHERE candidate_set_hash=%s",
            "DELETE FROM strategy_lab_candidate_sets WHERE candidate_set_hash=%s",
        ):
            with self.subTest(sql=sql), self.assertRaises(PersistenceError), \
                    self.database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(sql, (chosen.candidate_set_hash,))


if __name__ == "__main__":
    unittest.main()
