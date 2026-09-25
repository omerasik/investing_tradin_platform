import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class FeatureAuthorityPostgresTests(unittest.TestCase):
    def test_definition_versions_and_materializations_are_pit_isolated_immutable_and_restartable(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.feature_authority import (
            FeatureAuthorityError,
            FeatureDefinitionVersion,
            FeatureFamily,
            FeatureMaterialization,
            FeatureQualityStatus,
            PostgresFeatureAuthority,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase

        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(config, "head")
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        authority = PostgresFeatureAuthority(database)
        t0 = datetime(2025, 1, 2, 20, tzinfo=UTC)
        definition = FeatureDefinitionVersion(
            "simple_return", FeatureFamily.PRICE_RETURNS, "1.0.0", "quant",
            "Close-to-close return known only after the source bar is available.",
            ("OHLCV",), ("close", "event_at", "knowledge_at"), "1d",
            "event/effective/knowledge timestamps are all bounded by decision time", 1,
            {"horizon": 1}, "reject", "reject", "reject_future_knowledge",
            None, None, "decimal return", "transparent-market-v1", t0,
        )
        authority.register(definition)
        original = FeatureMaterialization.create(
            feature_id=definition.feature_id, instrument_id="fixture:SPY", dataset_version="fixture-v1",
            event_at=t0, effective_at=t0, knowledge_at=t0 + timedelta(minutes=1),
            computed_at=t0 + timedelta(minutes=1), source_observation_manifest=("raw:bar-1",),
            value=Decimal("0.01"), quality_status=FeatureQualityStatus.VALIDATED,
        )
        authority.materialize(original)
        authority.materialize(original)  # identical deterministic recomputation is idempotent
        self.assertEqual(authority.latest_as_of(definition.feature_id, "fixture:SPY", "fixture-v1", t0), ())
        self.assertEqual(
            authority.latest_as_of(definition.feature_id, "fixture:SPY", "fixture-v1", original.computed_at),
            (original,),
        )
        self.assertEqual(authority.latest_as_of(definition.feature_id, "fixture:SPY", "fixture-v2", original.computed_at), ())
        revised = FeatureMaterialization.create(
            feature_id=definition.feature_id, instrument_id="fixture:SPY", dataset_version="fixture-v1",
            event_at=t0, effective_at=t0, knowledge_at=t0 + timedelta(days=1),
            computed_at=t0 + timedelta(days=1), source_observation_manifest=("raw:bar-1-revision",),
            value=Decimal("0.02"), quality_status=FeatureQualityStatus.VALIDATED,
        )
        authority.materialize(revised)
        self.assertEqual(
            authority.latest_as_of(definition.feature_id, "fixture:SPY", "fixture-v1", original.computed_at),
            (original,),
        )
        self.assertEqual(
            authority.latest_as_of(definition.feature_id, "fixture:SPY", "fixture-v1", revised.computed_at),
            (revised,),
        )
        conflict = replace(original, value=Decimal("0.03"), content_hash="f" * 64)
        with self.assertRaisesRegex(FeatureAuthorityError, "conflict"):
            authority.materialize(conflict)
        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("UPDATE feature_materializations SET value=0 WHERE materialization_id=%s", (original.materialization_id,))
        database.close()
        reopened = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.assertEqual(
            PostgresFeatureAuthority(reopened).latest_as_of(
                definition.feature_id, "fixture:SPY", "fixture-v1", revised.computed_at
            ),
            (revised,),
        )
        reopened.close()

    def test_register_or_resolve_reuses_the_stored_id_and_fails_closed_on_drift(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.feature_authority import (
            FeatureAuthorityError,
            FeatureDefinitionVersion,
            FeatureFamily,
            PostgresFeatureAuthority,
        )
        from trade_platform.persistence import PostgresDatabase

        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(config, "head")
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        authority = PostgresFeatureAuthority(database)

        def build(created_at: datetime, description: str = "Fixture definition for register_or_resolve.") -> FeatureDefinitionVersion:
            return FeatureDefinitionVersion(
                "fixture_register_or_resolve", FeatureFamily.PRICE_RETURNS, "1.0.0", "quant", description,
                ("OHLCV",), ("close",), "1m", "event only", 0, {}, "reject", "reject",
                "reject_future_knowledge", Decimal("-1.0"), Decimal("1"), "dimensionless", "fixture-v1", created_at,
            )

        first = authority.register_or_resolve(build(datetime(2025, 1, 2, tzinfo=UTC)))
        rebuilt = build(datetime(2025, 6, 1, tzinfo=UTC))  # a fresh instance mints a fresh uuid4
        self.assertNotEqual(rebuilt.feature_id, first.feature_id)
        resolved = authority.register_or_resolve(rebuilt)
        self.assertEqual(resolved.feature_id, first.feature_id)
        self.assertEqual(authority.definition(first.feature_id).feature_id, first.feature_id)
        with self.assertRaisesRegex(FeatureAuthorityError, "feature_definition_drift"):
            authority.register_or_resolve(build(datetime(2025, 6, 1, tzinfo=UTC), description="changed semantics"))
        database.close()
