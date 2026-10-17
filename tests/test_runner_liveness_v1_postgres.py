"""Runner liveness -- progress persists append-only and hash-chained (real PostgreSQL)."""

from __future__ import annotations

import os
import unittest
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class RunnerLivenessStorePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")

    def setUp(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.runner_liveness_v1 import PostgresRunnerLivenessStoreV1

        self.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.store = PostgresRunnerLivenessStoreV1(self.database)
        self.trial = str(uuid4())  # unique per run: the shared database keeps every row

    def tearDown(self) -> None:
        self.database.close()

    def test_round_trip_reconstructs_like_memory_and_is_idempotent(self) -> None:
        from tests.test_runner_liveness_v1 import STUDY, SYMBOL, MemoryStore, derive, run_session
        from trade_platform.runner_liveness_v1 import RunnerLivenessError, RunnerProgressV1

        memory = MemoryStore()
        run_session(memory, start_minute=0, minutes=range(60), trial=self.trial, session=uuid4())
        rows = sorted(memory.all(), key=lambda row: row["identity"]["sequence"])
        for row in rows:
            self.assertTrue(self.store.record(RunnerProgressV1.build(row["identity"])))
        self.assertFalse(self.store.record(RunnerProgressV1.build(rows[3]["identity"])))
        with self.assertRaises(RunnerLivenessError):  # different content under a recorded key
            self.store.record(RunnerProgressV1.build({**rows[3]["identity"], "warmup_bars_total": 9}))
        stored = self.store.for_candidate(study_id=str(STUDY), trial_id=self.trial, symbol=SYMBOL)
        self.assertEqual(len(rows), len(stored))
        loaded = MemoryStore()
        loaded.rows = {str(index): row for index, row in enumerate(stored)}
        self.assertEqual(derive(memory, end=60, trial=self.trial).content_hash,
                         derive(loaded, end=60, trial=self.trial).content_hash)
        self.assertEqual(60 * 60_000_000, derive(loaded, end=60, trial=self.trial).proven_micros)
        self.assertEqual([], self.store.for_candidate(study_id=str(STUDY), trial_id=str(uuid4()), symbol=SYMBOL))

    def test_rows_are_immutable_and_chains_stay_inside_their_session(self) -> None:
        import psycopg

        from tests.test_runner_liveness_v1 import MemoryStore, run_session
        from trade_platform.persistence import PersistenceError
        from trade_platform.runner_liveness_v1 import RunnerProgressV1

        memory = MemoryStore()
        session = uuid4()
        run_session(memory, start_minute=0, minutes=range(3), trial=self.trial, session=session)
        rows = sorted(memory.all(), key=lambda row: row["identity"]["sequence"])
        for row in rows:
            self.store.record(RunnerProgressV1.build(row["identity"]))
        with self.database.transaction() as connection, connection.cursor() as cursor, \
                self.assertRaises(psycopg.Error):
            cursor.execute("UPDATE runner_liveness_progress SET bar_count = 0 WHERE runner_session_id = %s",
                           (session,))
        with self.database.transaction() as connection, connection.cursor() as cursor, \
                self.assertRaises(psycopg.Error):
            cursor.execute("DELETE FROM runner_liveness_progress WHERE runner_session_id = %s", (session,))
        # A record of another session cannot name this session's record as its predecessor.
        foreign = {**rows[1]["identity"], "runner_session_id": str(uuid4())}
        with self.assertRaises(PersistenceError) as raised:
            self.store.record(RunnerProgressV1.build(foreign))
        self.assertIsInstance(raised.exception.__cause__, psycopg.errors.ForeignKeyViolation)


if __name__ == "__main__":
    unittest.main()
