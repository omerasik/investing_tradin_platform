import hashlib
import os
import unittest
from uuid import NAMESPACE_URL, uuid5

from tests.test_trend_research_v2 import (
    dataset,
    feature_definition,
    health,
    materializations,
    request,
)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class TrendResearchV2PostgresTests(unittest.TestCase):
    def test_complete_run_is_immutable_replayable_and_restartable(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.data_health import PostgresDataHealthStore
        from trade_platform.feature_authority import (
            FeatureFamily,
            PostgresFeatureAuthority,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.trend_research_v2 import (
            PostgresTrendResearchV2Store,
            TrendResearchOrchestratorV2,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")
        database = PostgresDatabase(dsn)
        data = dataset()
        feature = feature_definition("multi_horizon_momentum", FeatureFamily.MOMENTUM)
        dataset_hash = hashlib.sha256(data.version.encode()).hexdigest()
        historical_source_id = uuid5(NAMESPACE_URL, "cycle202:historical-source")
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO datasets VALUES (%s,%s,'synthetic-fixture','terms-test-only',%s) "
                "ON CONFLICT (dataset_id) DO NOTHING",
                (data.dataset_id, "cycle202-trend-engineering-fixture", data.bars[0].occurred_at),
            )
            cursor.execute(
                "INSERT INTO dataset_versions VALUES (%s,%s,%s,%s,NULL,NULL,%s) "
                "ON CONFLICT (dataset_version_id) DO NOTHING",
                (
                    data.dataset_version_id,
                    data.dataset_id,
                    data.version,
                    dataset_hash,
                    data.bars[0].occurred_at,
                ),
            )
            cursor.execute(
                "INSERT INTO historical_data_sources VALUES "
                "(%s,'synthetic-fixture','cycle202-trend','FIXTURE','terms-test-only',"
                "'test-only:cycle202',%s,'US_EQUITIES_ETFS',%s) "
                "ON CONFLICT (source_id) DO NOTHING",
                (historical_source_id, data.bars[0].occurred_at, data.bars[0].occurred_at),
            )
            cursor.execute(
                "INSERT INTO historical_dataset_versions VALUES "
                "(%s,%s,%s,'synthetic-normalization-v1',%s,%s,NULL,%s,'SEALED') "
                "ON CONFLICT (dataset_version_id) DO NOTHING",
                (
                    data.dataset_version_id,
                    historical_source_id,
                    data.version,
                    dataset_hash,
                    data.bars[0].occurred_at,
                    data.bars[0].occurred_at,
                ),
            )
        feature_authority = PostgresFeatureAuthority(database)
        feature_authority.register(feature)
        for item in materializations(feature, data):
            feature_authority.materialize(item)
        assessment = health(data)
        PostgresDataHealthStore(database).persist(assessment)
        run_request = request(feature, data)
        outcome = TrendResearchOrchestratorV2(feature_authority).launch(run_request)
        assert outcome.run is not None
        run = outcome.run
        store = PostgresTrendResearchV2Store(database)
        self.assertEqual(store.persist_complete(run), run.run_id)
        self.assertEqual(store.persist_complete(run), run.run_id)
        self.assertEqual(store.get_run_hash(run.run_id), run.content_hash)
        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE strategy_versions SET version='tampered' WHERE strategy_version_id=%s",
                (run.definition.strategy_version_id,),
            )
        database.close()
        reopened = PostgresDatabase(dsn)
        self.assertEqual(
            PostgresTrendResearchV2Store(reopened).get_run_hash(run.run_id), run.content_hash
        )
        with reopened.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM walk_forward_evidence WHERE experiment_id=%s",
                (run.run_id,),
            )
            self.assertEqual(int(cursor.fetchone()[0]), len(run.walk_forward_results))
            cursor.execute(
                "SELECT COUNT(*) FROM validation_packages WHERE package_id=%s",
                (run.validation_package.identity.artifact_id,),
            )
            self.assertEqual(int(cursor.fetchone()[0]), 1)
            cursor.execute(
                "SELECT COUNT(*) FROM strategy_activation_events WHERE strategy_version_id=%s",
                (run.definition.strategy_version_id,),
            )
            self.assertEqual(int(cursor.fetchone()[0]), 0)
        reopened.close()


if __name__ == "__main__":
    unittest.main()
