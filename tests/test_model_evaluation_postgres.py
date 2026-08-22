import os
import unittest
from datetime import timedelta
from decimal import Decimal


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ModelEvaluationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace(
                "postgresql://", "postgresql+psycopg://", 1
            ),
        )
        command.upgrade(config, "head")

    def test_report_observations_restart_immutability_and_validation_link(self) -> None:
        import psycopg

        from tests.test_model_evaluation import NOW, candidate, observations, policy
        from trade_platform.model_evaluation import (
            ModelEvaluationError,
            PostgresModelEvaluationStore,
            evaluate_binary_classifier,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.postgres_decision_authorities import PostgresModelRegistry

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        registry = PostgresModelRegistry(database)
        model = candidate()
        registry.register(model)
        evaluation_policy = policy()
        evidence = observations()
        report = evaluate_binary_classifier(
            model=model,
            policy=evaluation_policy,
            observations=evidence,
            training_end=NOW - timedelta(days=11),
            holdout_start=NOW - timedelta(days=10),
            holdout_end=NOW - timedelta(days=1),
            evaluated_at=NOW,
        )
        store = PostgresModelEvaluationStore(database)
        store.register_policy(evaluation_policy)
        store.register_policy(evaluation_policy)
        store.append_report(report, evidence)
        store.append_report(report, reversed(evidence))
        validation = report.to_model_validation()
        registry.append_validation(validation)
        registry.approve(
            model.model_id,
            validation_id=validation.validation_id,
            actor="integration-model-reviewer",
            reason="reviewed fixture evaluation",
        )
        database.close()

        reopened = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        restarted = PostgresModelEvaluationStore(reopened)
        self.assertEqual(restarted.policy(evaluation_policy.policy_id), evaluation_policy)
        self.assertEqual(restarted.report(report.report_id), report)
        self.assertEqual(restarted.observations(report.report_id), evidence)
        self.assertTrue(PostgresModelRegistry(reopened).is_approved(model.model_id))
        with (
            self.assertRaises(PersistenceError),
            reopened.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE model_evaluation_reports SET outcome='BLOCKED' WHERE report_id=%s",
                (report.report_id,),
            )
        with self.assertRaises(ModelEvaluationError):
            restarted.append_report(
                report.__class__(
                    report.report_id,
                    report.model_id,
                    report.policy_id,
                    report.dataset_version,
                    report.feature_version,
                    report.training_end,
                    report.holdout_start,
                    report.holdout_end,
                    report.evaluated_at,
                    report.observation_count,
                    report.observation_evidence_hash,
                    {**report.candidate_metrics, "brier_score": Decimal("0.9")},
                    report.predecessor_metrics,
                    report.calibration,
                    report.feature_importance,
                    report.outcome,
                    report.reasons,
                    report.limitations,
                    report.content_hash,
                ),
                evidence,
            )
        with psycopg.connect(os.environ["POSTGRES_TEST_DSN"]) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM model_evaluation_observations WHERE report_id=%s",
                (report.report_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 8)
        reopened.close()


if __name__ == "__main__":
    unittest.main()
