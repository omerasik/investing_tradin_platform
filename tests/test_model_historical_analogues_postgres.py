import os
import unittest
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4


@unittest.skipUnless(os.getenv("POSTGRES_TEST_DSN"), "requires disposable PostgreSQL")
class PostgresHistoricalAnalogueTests(unittest.TestCase):
    def test_restart_idempotency_exact_graph_and_immutability(self) -> None:
        from tests.test_model_evaluation import NOW, candidate, observations, policy
        from tests.test_model_historical_analogues import (
            analogue_policy,
            candidates,
            target,
        )
        from trade_platform.model_evaluation import (
            BinaryEvaluationPolicy,
            PostgresModelEvaluationStore,
            evaluate_binary_classifier,
        )
        from trade_platform.model_historical_analogues import (
            PostgresHistoricalAnalogueStore,
            evaluate_historical_analogues,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.postgres_decision_authorities import PostgresModelRegistry

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        unique_id = uuid4()
        model = replace(
            candidate(),
            model_id=unique_id,
            name=f"historical-analogue-fixture-{unique_id}",
            version=f"v-{unique_id}",
        )
        PostgresModelRegistry(database).register(model)
        base_policy = policy()
        evaluation_policy = BinaryEvaluationPolicy.create(
            version=f"historical-analogue-evaluation-{unique_id}",
            task=base_policy.task,
            candidate_complexity=base_policy.candidate_complexity,
            predecessor_complexity=base_policy.predecessor_complexity,
            minimum_holdout_observations=base_policy.minimum_holdout_observations,
            calibration_bins=base_policy.calibration_bins,
            maximum_expected_calibration_error=base_policy.maximum_expected_calibration_error,
            minimum_brier_improvement=base_policy.minimum_brier_improvement,
            minimum_economic_improvement_after_cost=(
                base_policy.minimum_economic_improvement_after_cost
            ),
            maximum_half_brier_gap=base_policy.maximum_half_brier_gap,
            decision_threshold=base_policy.decision_threshold,
            assumed_cost_per_positive_decision=base_policy.assumed_cost_per_positive_decision,
            approved_by=base_policy.approved_by,
            approved_at=base_policy.approved_at,
        )
        holdout = observations()
        evaluation = evaluate_binary_classifier(
            model=model,
            policy=evaluation_policy,
            observations=holdout,
            training_end=NOW - timedelta(days=11),
            holdout_start=NOW - timedelta(days=10),
            holdout_end=NOW - timedelta(days=1),
            evaluated_at=NOW,
        )
        evaluation_store = PostgresModelEvaluationStore(database)
        evaluation_store.register_policy(evaluation_policy)
        evaluation_store.append_report(evaluation, holdout)

        explanation_target = target(evaluation)
        evidence = candidates(model.model_id)
        base_analogue_policy = analogue_policy()
        evidence_policy = base_analogue_policy.create(
            version=f"historical-analogue-{unique_id}",
            minimum_similarity=base_analogue_policy.minimum_similarity,
            minimum_analogues=base_analogue_policy.minimum_analogues,
            maximum_analogues=base_analogue_policy.maximum_analogues,
            minimum_distinct_regimes=base_analogue_policy.minimum_distinct_regimes,
            minimum_distinct_source_families=(
                base_analogue_policy.minimum_distinct_source_families
            ),
            maximum_probability_outcome_gap=(base_analogue_policy.maximum_probability_outcome_gap),
            approved_by=base_analogue_policy.approved_by,
            approved_at=base_analogue_policy.approved_at,
        )
        report, matches = evaluate_historical_analogues(
            evidence_policy,
            evaluation,
            explanation_target,
            evidence,
            evaluated_at=NOW + timedelta(days=1, minutes=2),
        )
        store = PostgresHistoricalAnalogueStore(database)
        store.append_policy(evidence_policy)
        store.append_policy(evidence_policy)
        store.append_target(explanation_target)
        store.append_target(explanation_target)
        for item in evidence:
            store.append_candidate(item)
            store.append_candidate(item)
        store.append_report(report, evidence)
        store.append_report(report, tuple(reversed(evidence)))
        database.close()

        reopened = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        restarted = PostgresHistoricalAnalogueStore(reopened)
        self.assertEqual(restarted.policy(evidence_policy.policy_id), evidence_policy)
        self.assertEqual(restarted.target(explanation_target.target_id), explanation_target)
        self.assertEqual(restarted.candidate(evidence[0].analogue_id), evidence[0])
        self.assertEqual(restarted.report(report.report_id), (report, matches))
        with self.assertRaises(PersistenceError), reopened.transaction() as connection:
            connection.execute(
                "UPDATE historical_analogue_reports SET action_authority='LIVE' WHERE report_id=%s",
                (report.report_id,),
            )
        with self.assertRaises(PersistenceError), reopened.transaction() as connection:
            connection.execute(
                "DELETE FROM historical_analogue_candidates WHERE analogue_id=%s",
                (evidence[0].analogue_id,),
            )
        reopened.close()


if __name__ == "__main__":
    unittest.main()
