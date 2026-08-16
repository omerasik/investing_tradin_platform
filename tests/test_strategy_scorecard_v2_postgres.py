import hashlib
import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class StrategyScorecardV2PostgresTests(unittest.TestCase):
    def test_scorecard_replay_immutability_and_version_bound_promotion_check(self) -> None:
        import psycopg
        from alembic import command
        from alembic.config import Config

        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_quant_validation import PostgresQuantValidationStore
        from trade_platform.quant_validation import (
            REQUIRED_EVIDENCE,
            build_validation_package,
            record_external_evidence,
        )
        from trade_platform.strategy_scorecard_v2 import (
            PostgresStrategyScorecardStore,
            ScorecardStatus,
            ScoreComponentV2,
            StrategyScorecardV2,
            performance_metrics,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")
        now = datetime(2025, 1, 1, tzinfo=UTC)
        strategy_id, strategy_version_id = uuid4(), uuid4()
        dataset_id, dataset_version_id = uuid4(), uuid4()
        health_assessment_id = uuid4()
        suffix = str(strategy_id)[:8]
        with psycopg.connect(dsn) as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO datasets VALUES (%s,%s,'fixture','terms-v1',%s)",
                (dataset_id, f"scorecard-data-{suffix}", now),
            )
            cursor.execute(
                "INSERT INTO dataset_versions VALUES (%s,%s,'v1',%s,NULL,NULL,%s)",
                (
                    dataset_version_id,
                    dataset_id,
                    hashlib.sha256(f"scorecard:{dataset_version_id}".encode()).hexdigest(),
                    now,
                ),
            )
            cursor.execute(
                "INSERT INTO strategy_definitions VALUES (%s,'scorecard','fixture',%s)",
                (strategy_id, now),
            )
            cursor.execute(
                "INSERT INTO strategy_versions VALUES (%s,%s,'v1','{}'::jsonb,'cost-v1','capacity-v1','{}'::jsonb,%s)",
                (strategy_version_id, strategy_id, now),
            )
            cursor.execute(
                "INSERT INTO data_health_assessments VALUES (%s,NULL,'GLOBAL','*','fixture-v1',%s,%s,%s,'INFO',FALSE,%s,'{}'::jsonb)",
                (
                    health_assessment_id,
                    now,
                    now,
                    now,
                    hashlib.sha256(f"health:{health_assessment_id}".encode()).hexdigest(),
                ),
            )

        database = PostgresDatabase(dsn)
        strategy_version, dataset_version = "scorecard-v1", "scorecard-data-v1"
        validation = PostgresQuantValidationStore(
            database,
            strategy_versions={strategy_version: strategy_version_id},
            dataset_versions={dataset_version: dataset_version_id},
        )
        evidence_ids = {}
        evidence_hashes = {}
        for evidence_type in REQUIRED_EVIDENCE:
            artifact = record_external_evidence(
                evidence_type=evidence_type,
                strategy_version=strategy_version,
                dataset_version=dataset_version,
                passed=True,
            )
            evidence_ids[evidence_type] = validation.append(artifact)
            evidence_hashes[evidence_type] = artifact.identity.content_hash
        package = build_validation_package(
            strategy_id=strategy_id,
            strategy_version_id=strategy_version_id,
            strategy_version=strategy_version,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            dataset_version=dataset_version,
            feature_versions=("return:1.0.0",),
            cost_model_version="cost-v1",
            evidence_ids=evidence_ids,
            evidence_hashes=evidence_hashes,
            limitations=("Synthetic engineering fixture only; no investment conclusion.",),
            evaluated_at=now,
        )
        package_id = validation.append(package)
        scorecard = StrategyScorecardV2(
            "scorecard-v2", strategy_id, strategy_version, uuid4(), dataset_version,
            ("return:1.0.0",), "cost-v1", now, now, ScorecardStatus.REVIEW_REQUIRED,
            ("Synthetic engineering fixture only; no investment conclusion.",),
            performance_metrics((Decimal("0.01"), Decimal("-0.02"), Decimal("0.03"))),
            (ScoreComponentV2("performance_component", "component-v1", Decimal("50"), "Transparent and non-authoritative."),),
            "HEALTHY", (health_assessment_id,), {"research_run": "a" * 64},
        )
        store = PostgresStrategyScorecardStore(database)
        self.assertEqual(store.publish(scorecard), scorecard.scorecard_id)
        self.assertEqual(store.publish(scorecard), scorecard.scorecard_id)
        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT assessment_id FROM scorecard_data_health_assessments WHERE scorecard_id=%s",
                (scorecard.scorecard_id,),
            )
            self.assertEqual(cursor.fetchone()[0], health_assessment_id)
        self.assertFalse(store.promotion_eligible(scorecard.scorecard_id, strategy_version=strategy_version, dataset_version=dataset_version, feature_versions=("return:1.0.0",), cost_model_version="cost-v1"))
        store.link_validation_package(scorecard.scorecard_id, package_id)
        self.assertTrue(store.promotion_eligible(scorecard.scorecard_id, strategy_version=strategy_version, dataset_version=dataset_version, feature_versions=("return:1.0.0",), cost_model_version="cost-v1"))
        self.assertFalse(store.promotion_eligible(scorecard.scorecard_id, strategy_version=strategy_version, dataset_version="wrong", feature_versions=("return:1.0.0",), cost_model_version="cost-v1"))
        self.assertFalse(store.promotion_eligible(scorecard.scorecard_id, strategy_version=strategy_version, dataset_version=dataset_version, feature_versions=("wrong-feature",), cost_model_version="cost-v1"))
        self.assertFalse(store.promotion_eligible(scorecard.scorecard_id, strategy_version=strategy_version, dataset_version=dataset_version, feature_versions=("return:1.0.0",), cost_model_version="wrong-cost"))
        with (
            self.assertRaises(psycopg.errors.RaiseException),
            psycopg.connect(dsn) as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE strategy_scorecards SET status='BLOCKED' WHERE scorecard_id=%s",
                (scorecard.scorecard_id,),
            )
        database.close()
        reopened = PostgresDatabase(dsn)
        self.assertTrue(PostgresStrategyScorecardStore(reopened).promotion_eligible(scorecard.scorecard_id, strategy_version=strategy_version, dataset_version=dataset_version, feature_versions=("return:1.0.0",), cost_model_version="cost-v1"))
        reopened.close()
