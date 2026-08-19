import hashlib
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from tests.test_social_narrative_intelligence import (
    INSTRUMENT,
    complete_observations,
    source,
)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class SocialNarrativeIntelligencePostgresTests(unittest.TestCase):
    def test_restart_immutability_pit_and_no_trading_authority(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.professional_instruments import (
            PostgresProfessionalInstrumentMaster,
            mvp_instrument_universe,
        )
        from trade_platform.social_narrative_intelligence import (
            PostgresSocialNarrativeStore,
            build_narrative_window,
            cluster_social_observations,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")
        database = PostgresDatabase(dsn)
        now = datetime(2026, 8, 19, 12, tzinfo=UTC)
        instrument = replace(
            mvp_instrument_universe(datetime(2024, 1, 1, tzinfo=UTC))[0],
            instrument_id=INSTRUMENT,
            canonical_symbol="CYCLE210",
        )
        PostgresProfessionalInstrumentMaster(database).register(instrument)
        health_id = uuid4()
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO data_health_assessments "
                "(assessment_id,dataset_version_id,scope_type,scope_value,policy_version,"
                "evaluated_at,expected_start,expected_end,max_action,blocking,content_hash,summary) "
                "VALUES (%s,NULL,'INSTRUMENT',%s,'cycle210-health-v1',%s,%s,%s,'INFO',"
                "FALSE,%s,'{}'::jsonb)",
                (
                    health_id,
                    INSTRUMENT,
                    now,
                    now - timedelta(days=1),
                    now,
                    hashlib.sha256(b"cycle210-health").hexdigest(),
                ),
            )
            cursor.execute("SELECT COUNT(*) FROM paper_order_intents")
            prior_order_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM strategy_activation_events")
            prior_activation_count = int(cursor.fetchone()[0])

        policy = source()
        observations = complete_observations(policy)
        clusters = cluster_social_observations(
            observations,
            model_version="cycle210-fixture-topic-v1",
            created_at=now + timedelta(minutes=30),
        )
        window = build_narrative_window(
            policy,
            clusters[0],
            observations,
            window_start=now,
            window_end=now + timedelta(hours=1),
            evaluated_at=now + timedelta(hours=1),
            data_health_status="HEALTHY",
            data_health_assessment_ids=(health_id,),
            price_return=Decimal("0.1"),
        )
        store = PostgresSocialNarrativeStore(database)
        self.assertEqual(store.publish_source(policy), policy.source_policy_version_id)
        self.assertEqual(store.publish_source(policy), policy.source_policy_version_id)
        ids = store.publish_evidence(policy, observations, clusters, (window,))
        self.assertEqual(ids, (window.window_id,))
        self.assertEqual(store.publish_evidence(policy, observations, clusters, (window,)), ids)
        self.assertEqual(
            store.windows_for_instrument_as_of(INSTRUMENT, now + timedelta(minutes=59)),
            (),
        )
        after = store.windows_for_instrument_as_of(INSTRUMENT, window.evaluated_at)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0][0], "REVIEW_REQUIRED")
        self.assertEqual(after[0][1], window.sentiment)
        self.assertTrue(after[0][4])

        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE social_narrative_metric_windows "
                "SET standalone_trade_trigger=TRUE WHERE window_id=%s",
                (window.window_id,),
            )
        database.close()

        reopened = PostgresDatabase(dsn)
        restarted = PostgresSocialNarrativeStore(reopened)
        self.assertEqual(
            restarted.windows_for_instrument_as_of(INSTRUMENT, window.evaluated_at),
            after,
        )
        with reopened.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT research_only,standalone_trade_trigger,automatic_authority,"
                "evidence_label FROM social_narrative_metric_windows WHERE window_id=%s",
                (window.window_id,),
            )
            self.assertEqual(
                cursor.fetchone(),
                (True, False, False, "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY"),
            )
            cursor.execute("SELECT COUNT(*) FROM paper_order_intents")
            self.assertEqual(int(cursor.fetchone()[0]), prior_order_count)
            cursor.execute("SELECT COUNT(*) FROM strategy_activation_events")
            self.assertEqual(int(cursor.fetchone()[0]), prior_activation_count)
        reopened.close()


if __name__ == "__main__":
    unittest.main()
