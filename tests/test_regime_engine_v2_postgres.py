import hashlib
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from tests.test_regime_engine_v2 import (
    DATASET_HASH,
    DATASET_ID,
    HEALTH_ID,
    TIMES,
    definition,
    feature_definitions,
    labels,
    materializations,
)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class RegimeEngineV2PostgresTests(unittest.TestCase):
    def test_regime_authority_is_immutable_replayable_restartable_and_non_executing(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.feature_authority import PostgresFeatureAuthority
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.professional_instruments import (
            PostgresProfessionalInstrumentMaster,
            mvp_instrument_universe,
        )
        from trade_platform.regime_engine_v2 import (
            PostgresRegimeEngineV2Store,
            RegimeResearchOrchestratorV2,
            ReviewStatus,
            build_regime_risk_candidate,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")
        database = PostgresDatabase(dsn)
        now = datetime(2025, 1, 20, tzinfo=UTC)
        instrument = replace(
            mvp_instrument_universe(datetime(2024, 1, 1, tzinfo=UTC))[0],
            instrument_id="US:XNYS:CYCLE204",
            canonical_symbol="CYCLE204",
        )
        PostgresProfessionalInstrumentMaster(database).register(instrument)
        source_id = uuid4()
        strategy_id, strategy_version_id = uuid4(), uuid4()
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO historical_data_sources VALUES (%s,'cycle204-fixture','regime-inputs','FIXTURE','test-only','fixture://cycle204/authorization',%s,'US_EQUITIES_ETFS',%s)",
                (source_id, TIMES[0], TIMES[0]),
            )
            cursor.execute(
                "INSERT INTO historical_dataset_versions VALUES (%s,%s,'cycle204-v1','fixture-normalization-v1',%s,%s,NULL,%s,'SEALED')",
                (DATASET_ID, source_id, DATASET_HASH, TIMES[0], TIMES[0]),
            )
            cursor.execute(
                "INSERT INTO data_health_assessments VALUES (%s,%s,'GLOBAL','*','cycle204-health-v1',%s,%s,%s,'INFO',FALSE,%s,'{}'::jsonb)",
                (
                    HEALTH_ID,
                    DATASET_ID,
                    now,
                    TIMES[0],
                    TIMES[-1],
                    hashlib.sha256(b"cycle204-health").hexdigest(),
                ),
            )
            cursor.execute(
                "INSERT INTO strategy_definitions VALUES (%s,'TREND','cycle204 fixture strategy',%s)",
                (strategy_id, TIMES[0]),
            )
            cursor.execute(
                "INSERT INTO strategy_versions VALUES (%s,%s,'cycle204-v1','[]'::jsonb,'cost-v1','capacity-v1','{}'::jsonb,%s)",
                (strategy_version_id, strategy_id, TIMES[0]),
            )

        definitions = feature_definitions()
        feature_store = PostgresFeatureAuthority(database)
        for item in definitions:
            feature_store.register(item)
        for item in materializations(definitions):
            feature_store.materialize(item)
        model = definition(definitions)
        run = RegimeResearchOrchestratorV2(feature_store).run(
            model,
            dataset_version_id=DATASET_ID,
            dataset_version="cycle204-v1",
            dataset_content_hash=DATASET_HASH,
            instrument_id=instrument.instrument_id,
            decision_times=TIMES,
            labels=labels(),
            evaluated_at=now,
            data_health_status="HEALTHY",
            data_health_assessment_ids=(HEALTH_ID,),
        )
        self.assertEqual(run.status, ReviewStatus.REVIEW_REQUIRED)
        candidate = build_regime_risk_candidate(
            run,
            strategy_version_id=strategy_version_id,
            current_risk_multiplier=Decimal("1"),
            proposed_risk_multiplier=Decimal("0.5"),
            preapproved_maximum=Decimal("1"),
            created_at=now,
        )
        store = PostgresRegimeEngineV2Store(database)
        self.assertEqual(store.publish_definition(model), model.model_version_id)
        self.assertEqual(store.publish_definition(model), model.model_version_id)
        self.assertEqual(store.publish_run(run), run.run_id)
        self.assertEqual(store.publish_run(run), run.run_id)
        self.assertEqual(store.publish_candidate(candidate), candidate.candidate_id)
        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE regime_runs SET status='BLOCKED' WHERE run_id=%s", (run.run_id,)
            )
        database.close()

        reopened = PostgresDatabase(dsn)
        with reopened.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT content_hash FROM regime_runs WHERE run_id=%s", (run.run_id,))
            self.assertEqual(str(cursor.fetchone()[0]), run.content_hash)
            cursor.execute("SELECT COUNT(*) FROM regime_run_observations WHERE run_id=%s", (run.run_id,))
            self.assertEqual(int(cursor.fetchone()[0]), len(run.observations))
            cursor.execute(
                "SELECT COUNT(*) FROM regime_run_method_evaluations WHERE run_id=%s", (run.run_id,)
            )
            self.assertEqual(int(cursor.fetchone()[0]), len(run.evaluations))
            cursor.execute(
                "SELECT status,automatic_authority FROM regime_risk_adjustment_candidates WHERE candidate_id=%s",
                (candidate.candidate_id,),
            )
            self.assertEqual(cursor.fetchone(), ("REVIEW_REQUIRED", False))
            cursor.execute(
                "SELECT COUNT(*) FROM strategy_activation_events WHERE strategy_version_id=%s",
                (strategy_version_id,),
            )
            self.assertEqual(int(cursor.fetchone()[0]), 0)
        reopened.close()


if __name__ == "__main__":
    unittest.main()
