import os
import unittest
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4


@unittest.skipUnless(os.getenv("POSTGRES_TEST_DSN"), "requires disposable PostgreSQL")
class PostgresModelMonitoringEvidenceTests(unittest.TestCase):
    def test_restart_idempotency_exact_bindings_and_immutability(self) -> None:
        from tests.test_model_evaluation import NOW, candidate, observations, policy
        from tests.test_model_monitoring_evidence import monitoring_evidence
        from tests.test_model_monitoring_evidence import policy as monitoring_policy
        from trade_platform.model_evaluation import (
            BinaryEvaluationPolicy,
            PostgresModelEvaluationStore,
            evaluate_binary_classifier,
        )
        from trade_platform.model_monitoring_evidence import (
            PostgresModelMonitoringStore,
            evaluate_model_monitoring,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.postgres_decision_authorities import PostgresModelRegistry

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        registry = PostgresModelRegistry(database)
        unique_id = uuid4()
        model = replace(
            candidate(), model_id=unique_id, name=f"monitoring-fixture-{unique_id}",
            version=f"v-{unique_id}",
        )
        registry.register(model)
        base_policy = policy()
        evaluation_policy = BinaryEvaluationPolicy.create(
            version=f"evaluation-{unique_id}", task=base_policy.task,
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
            approved_by=base_policy.approved_by, approved_at=base_policy.approved_at,
        )
        holdout = observations()
        evaluation = evaluate_binary_classifier(
            model=model, policy=evaluation_policy, observations=holdout,
            training_end=NOW - timedelta(days=11),
            holdout_start=NOW - timedelta(days=10),
            holdout_end=NOW - timedelta(days=1), evaluated_at=NOW,
        )
        evaluation_store = PostgresModelEvaluationStore(database)
        evaluation_store.register_policy(evaluation_policy)
        evaluation_store.append_report(evaluation, holdout)

        scenarios, degradation = monitoring_evidence(evaluation)
        evidence_policy = monitoring_policy()
        report = evaluate_model_monitoring(
            evidence_policy, evaluation, scenarios, degradation,
            evaluated_at=NOW + timedelta(hours=2),
        )
        store = PostgresModelMonitoringStore(database)
        store.append_policy(evidence_policy)
        store.append_policy(evidence_policy)
        store.append_report(report, scenarios, degradation)
        store.append_report(report, tuple(reversed(scenarios)), tuple(reversed(degradation)))
        database.close()

        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        restarted_store = PostgresModelMonitoringStore(restarted)
        self.assertEqual(restarted_store.policy(evidence_policy.policy_id), evidence_policy)
        self.assertEqual(restarted_store.report(report.report_id), report)
        self.assertEqual(restarted_store.sensitivity_scenarios(report.report_id), scenarios)
        self.assertEqual(restarted_store.degradation_observations(report.report_id), degradation)
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "UPDATE model_monitoring_reports SET outcome='DEGRADED_REVIEW_REQUIRED' "
                "WHERE report_id=%s", (report.report_id,),
            )
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "UPDATE model_degradation_observations SET score=0.9 WHERE report_id=%s",
                (report.report_id,),
            )
        restarted.close()


if __name__ == "__main__":
    unittest.main()
