import os
import unittest
from datetime import UTC, datetime, timedelta


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PostgresAuditStoreTests(unittest.TestCase):
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

    def setUp(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_audit import PostgresAuditStore

        self.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.store = PostgresAuditStore(self.database)

    def tearDown(self) -> None:
        self.database.close()

    def test_append_recent_query_and_get_round_trip(self) -> None:
        first = self.store.append("unit.test.first", "operator-a", {"n": 1})
        second = self.store.append("unit.test.second", "operator-b", {"n": 2})

        recent = self.store.recent(2)
        self.assertEqual([event.event_id for event in recent], [second.event_id, first.event_id])

        by_type, has_more = self.store.query(event_type="unit.test.first", limit=10)
        self.assertEqual([event.event_id for event in by_type], [first.event_id])
        self.assertFalse(has_more)

        fetched = self.store.get(first.event_id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.actor, "operator-a")
        self.assertEqual(fetched.payload, {"n": 1})

        self.assertIsNone(self.store.get(second.event_id.__class__(int=0)))

    def test_query_bounds_and_pagination(self) -> None:
        for index in range(3):
            self.store.append("unit.test.paginated", "operator-a", {"index": index})
        page, has_more = self.store.query(event_type="unit.test.paginated", limit=2, offset=0)
        self.assertEqual(len(page), 2)
        self.assertTrue(has_more)
        with self.assertRaises(ValueError):
            self.store.query(limit=0)
        with self.assertRaises(ValueError):
            self.store.query(limit=1, offset=-1)

    def test_query_time_window_filters(self) -> None:
        anchor = datetime(2026, 9, 6, 12, tzinfo=UTC)
        self.store.append("unit.test.window", "operator-a", {"marker": "one"})
        before, _ = self.store.query(
            event_type="unit.test.window", end=anchor - timedelta(days=1)
        )
        self.assertEqual(before, [])

    def test_events_are_immutable_at_the_schema_level(self) -> None:
        from trade_platform.persistence import PersistenceError

        event = self.store.append("unit.test.immutable", "operator-a", {})
        with (
            self.assertRaises(PersistenceError),
            self.database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE audit_events SET actor='tampered' WHERE event_id=%s",
                (event.event_id,),
            )

    def test_append_requires_event_type_and_actor(self) -> None:
        with self.assertRaises(ValueError):
            self.store.append("", "operator-a", {})
        with self.assertRaises(ValueError):
            self.store.append("unit.test", "  ", {})


if __name__ == "__main__":
    unittest.main()
