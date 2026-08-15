import hashlib
import os
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PitMacroPostgresTests(unittest.TestCase):
    def test_initial_release_revision_and_restart_are_point_in_time(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.pit_macro import (
            AuthorizedMacroSource,
            MacroCategory,
            PitMacroObservation,
            PostgresPitMacroStore,
        )

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")
        db = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        store = PostgresPitMacroStore(db)
        t0 = datetime(2024, 2, 1, 13, 30, tzinfo=UTC)
        source = AuthorizedMacroSource(
            "cycle15_fixture", "test-v1", "test-authorization://cycle15", t0, t0
        )
        store.register_source(source)
        initial = PitMacroObservation(
            source.source_id,
            MacroCategory.EMPLOYMENT,
            "PAYEMS",
            date(2024, 1, 1),
            t0,
            t0,
            Decimal("100"),
            Decimal("95"),
            Decimal("90"),
            0,
            t0 + timedelta(minutes=1),
            "fixture://payems/0",
            hashlib.sha256(b"v0").hexdigest(),
        )
        revised = replace(
            initial,
            observation_id=__import__("uuid").uuid4(),
            actual=Decimal("98"),
            revision=1,
            release_at=t0 + timedelta(days=30),
            ingested_at=t0 + timedelta(days=30, minutes=1),
            provenance_uri="fixture://payems/1",
            raw_payload_sha256=hashlib.sha256(b"v1").hexdigest(),
        )
        store.ingest((initial, revised))
        self.assertEqual(store.available_as_of("PAYEMS", t0), ())
        self.assertEqual(
            store.available_as_of("PAYEMS", initial.ingested_at)[0].actual, Decimal("100")
        )
        latest = store.available_as_of("PAYEMS", revised.ingested_at)
        self.assertEqual(latest[0].actual, Decimal("98"))
        with (
            self.assertRaises(PersistenceError),
            db.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE pit_macro_observations SET actual=0 WHERE observation_id=%s",
                (initial.observation_id,),
            )
        db.close()
        reopened = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.assertEqual(
            PostgresPitMacroStore(reopened).available_as_of("PAYEMS", revised.ingested_at), latest
        )
        reopened.close()
