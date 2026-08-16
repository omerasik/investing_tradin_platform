import hashlib
import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from tests.test_portfolio_construction_v2 import (
    NOW,
    covariance,
    policy,
    sleeve,
)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PortfolioConstructionV2PostgresTests(unittest.TestCase):
    def test_authority_is_immutable_replayable_restartable_and_non_executing(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.portfolio_construction_v2 import (
            ConstructionStatus,
            PostgresPortfolioConstructionV2Store,
            construct_multi_strategy_portfolio,
        )
        from trade_platform.professional_instruments import (
            PostgresProfessionalInstrumentMaster,
            mvp_instrument_universe,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")
        database = PostgresDatabase(dsn)
        now = NOW
        suffix = str(uuid4())[:8]
        instrument = replace(
            mvp_instrument_universe(datetime(2024, 1, 1, tzinfo=UTC))[0],
            instrument_id=f"US:XNYS:CYCLE205-{suffix}",
            canonical_symbol=f"C205{suffix[:4]}",
        )
        PostgresProfessionalInstrumentMaster(database).register(instrument)

        dataset_id, dataset_version_id = uuid4(), uuid4()
        source_id, historical_dataset_id = uuid4(), uuid4()
        regime_model_id, regime_model_version_id, regime_run_id = uuid4(), uuid4(), uuid4()
        regime_hash = hashlib.sha256(f"regime-run:{suffix}".encode()).hexdigest()
        strategy_records: list[dict[str, object]] = []
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO datasets VALUES (%s,%s,'fixture','terms-v1',%s)",
                (dataset_id, f"cycle205-validation-{suffix}", now),
            )
            cursor.execute(
                "INSERT INTO dataset_versions VALUES (%s,%s,'cycle205-v1',%s,NULL,NULL,%s)",
                (
                    dataset_version_id,
                    dataset_id,
                    hashlib.sha256(f"dataset:{suffix}".encode()).hexdigest(),
                    now,
                ),
            )
            cursor.execute(
                "INSERT INTO historical_data_sources VALUES "
                "(%s,%s,'portfolio-inputs','FIXTURE','test-only',%s,%s,'US_EQUITIES_ETFS',%s)",
                (
                    source_id,
                    f"cycle205-source-{suffix}",
                    f"fixture://cycle205/{suffix}",
                    now - timedelta(days=200),
                    now - timedelta(days=200),
                ),
            )
            cursor.execute(
                "INSERT INTO historical_dataset_versions VALUES "
                "(%s,%s,'cycle205-v1','fixture-normalization-v1',%s,%s,NULL,%s,'SEALED')",
                (
                    historical_dataset_id,
                    source_id,
                    hashlib.sha256(f"historical:{suffix}".encode()).hexdigest(),
                    now - timedelta(days=200),
                    now - timedelta(days=1),
                ),
            )
            health_id = uuid4()
            cursor.execute(
                "INSERT INTO data_health_assessments VALUES "
                "(%s,%s,'GLOBAL','*','cycle205-health-v1',%s,%s,%s,'INFO',FALSE,%s,'{}'::jsonb)",
                (
                    health_id,
                    historical_dataset_id,
                    now,
                    now - timedelta(days=200),
                    now,
                    hashlib.sha256(f"health:{suffix}".encode()).hexdigest(),
                ),
            )
            cursor.execute(
                "INSERT INTO regime_model_versions VALUES "
                "(%s,%s,'cycle205-v1','regime-v2','RESEARCH_ONLY',%s,'[]'::jsonb,'{}'::jsonb,%s,%s)",
                (
                    regime_model_version_id,
                    regime_model_id,
                    now - timedelta(days=2),
                    now - timedelta(days=1),
                    hashlib.sha256(f"regime-model:{suffix}".encode()).hexdigest(),
                ),
            )
            cursor.execute(
                "INSERT INTO regime_runs VALUES "
                "(%s,%s,%s,'cycle205-v1',%s,%s,%s,'REVIEW_REQUIRED','[]'::jsonb,'{}'::jsonb,%s)",
                (
                    regime_run_id,
                    regime_model_version_id,
                    historical_dataset_id,
                    hashlib.sha256(f"historical:{suffix}".encode()).hexdigest(),
                    instrument.instrument_id,
                    now,
                    regime_hash,
                ),
            )
            for index, key in enumerate(("breakout", "trend")):
                strategy_id, strategy_version_id = uuid4(), uuid4()
                scorecard_id, package_id, regime_candidate_id = uuid4(), uuid4(), uuid4()
                strategy_version = f"cycle205-{key}-v1"
                cursor.execute(
                    "INSERT INTO strategy_definitions VALUES (%s,'TREND',%s,%s)",
                    (strategy_id, f"cycle205 {key} fixture", now - timedelta(days=2)),
                )
                cursor.execute(
                    "INSERT INTO strategy_versions VALUES "
                    "(%s,%s,%s,'[]'::jsonb,'cost-v1','capacity-v1','{}'::jsonb,%s)",
                    (strategy_version_id, strategy_id, strategy_version, now - timedelta(days=2)),
                )
                manifest = json.dumps(
                    {"cycle": 205, "strategy": key, "synthetic": True},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                package_hash = hashlib.sha256(manifest.encode()).hexdigest()
                cursor.execute(
                    "INSERT INTO validation_packages VALUES "
                    "(%s,%s,%s,'cost-v1',%s,'REVIEW_REQUIRED_OR_BLOCKED',%s,'[]'::jsonb,"
                    "'validation-package-manifest-v1',%s,'VERIFIED')",
                    (
                        package_id,
                        strategy_version_id,
                        dataset_version_id,
                        package_hash,
                        now - timedelta(days=1),
                        manifest,
                    ),
                )
                cursor.execute(
                    "INSERT INTO strategy_scorecards VALUES "
                    "(%s,'scorecard-v2',%s,%s,%s,'[]'::jsonb,'cycle205-v1','cost-v1',"
                    "%s,%s,'REVIEW_REQUIRED','[]'::jsonb,'HEALTHY','[]'::jsonb,'{}'::jsonb,%s)",
                    (
                        scorecard_id,
                        strategy_id,
                        strategy_version,
                        uuid4(),
                        now,
                        now,
                        hashlib.sha256(f"scorecard:{key}:{suffix}".encode()).hexdigest(),
                    ),
                )
                cursor.execute(
                    "INSERT INTO scorecard_validation_packages VALUES (%s,%s,%s)",
                    (scorecard_id, package_id, package_hash),
                )
                proposed = Decimal("0.8") if key == "trend" else Decimal("1")
                action = "REDUCE" if proposed < 1 else "MAINTAIN"
                cursor.execute(
                    "INSERT INTO regime_risk_adjustment_candidates VALUES "
                    "(%s,%s,%s,1,%s,1,%s,'REVIEW_REQUIRED','[]'::jsonb,FALSE,%s,%s)",
                    (
                        regime_candidate_id,
                        regime_run_id,
                        strategy_version_id,
                        proposed,
                        action,
                        now,
                        hashlib.sha256(f"regime-candidate:{key}:{suffix}".encode()).hexdigest(),
                    ),
                )
                strategy_records.append(
                    {
                        "key": key,
                        "strategy_version_id": strategy_version_id,
                        "scorecard_id": scorecard_id,
                        "package_id": package_id,
                        "regime_candidate_id": regime_candidate_id,
                        "proposed": proposed,
                    }
                )
            cursor.execute("SELECT COUNT(*) FROM signals")
            initial_signal_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM paper_order_intents")
            initial_order_count = int(cursor.fetchone()[0])

        inputs = tuple(
            replace(
                sleeve(str(record["key"]), volatility="0.20" if record["key"] == "breakout" else "0.30"),
                strategy_version_id=record["strategy_version_id"],
                scorecard_id=record["scorecard_id"],
                validation_package_id=record["package_id"],
                regime_candidate_id=record["regime_candidate_id"],
                regime_proposed_multiplier=record["proposed"],
            )
            for record in strategy_records
        )
        result = construct_multi_strategy_portfolio(
            policy(),
            regime_run_id=regime_run_id,
            regime_content_hash=regime_hash,
            covariance=replace(
                covariance(),
                dataset_version_id=historical_dataset_id,
                dataset_version="cycle205-v1",
                dataset_content_hash=hashlib.sha256(f"historical:{suffix}".encode()).hexdigest(),
            ),
            sleeves=inputs,
            data_health_status="HEALTHY",
            data_health_assessment_ids=(health_id,),
            constructed_at=now,
        )
        self.assertEqual(result.status, ConstructionStatus.REVIEW_REQUIRED)
        store = PostgresPortfolioConstructionV2Store(database)
        self.assertEqual(store.publish_policy(result.policy), result.policy.policy_version_id)
        self.assertEqual(store.publish_policy(result.policy), result.policy.policy_version_id)
        self.assertEqual(store.publish_run(result), result.run_id)
        self.assertEqual(store.publish_run(result), result.run_id)
        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE portfolio_target_candidates SET status='BLOCKED' WHERE candidate_id=%s",
                (result.target.candidate_id,),
            )
        database.close()

        reopened = PostgresDatabase(dsn)
        with reopened.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT content_hash,status FROM portfolio_construction_runs WHERE run_id=%s",
                (result.run_id,),
            )
            self.assertEqual(cursor.fetchone(), (result.content_hash, "REVIEW_REQUIRED"))
            cursor.execute(
                "SELECT COUNT(*) FROM portfolio_sleeve_inputs WHERE run_id=%s",
                (result.run_id,),
            )
            self.assertEqual(int(cursor.fetchone()[0]), 2)
            cursor.execute(
                "SELECT status,automatic_authority FROM portfolio_target_candidates WHERE candidate_id=%s",
                (result.target.candidate_id,),
            )
            self.assertEqual(cursor.fetchone(), ("REVIEW_REQUIRED", False))
            cursor.execute(
                "SELECT approved,automatic_authority FROM portfolio_risk_gate_evidence WHERE run_id=%s",
                (result.run_id,),
            )
            self.assertEqual(cursor.fetchone(), (True, False))
            cursor.execute(
                "SELECT COUNT(*) FROM strategy_activation_events WHERE strategy_version_id=ANY(%s)",
                ([record["strategy_version_id"] for record in strategy_records],),
            )
            self.assertEqual(int(cursor.fetchone()[0]), 0)
            cursor.execute("SELECT COUNT(*) FROM signals")
            signal_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM paper_order_intents")
            self.assertEqual(signal_count, initial_signal_count)
            self.assertEqual(int(cursor.fetchone()[0]), initial_order_count)
        reopened.close()


if __name__ == "__main__":
    unittest.main()
