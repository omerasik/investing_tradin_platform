"""Real PostgreSQL consumed-bar persistence, restart reads and immutability."""
from __future__ import annotations

import copy
import os
import unittest
from datetime import UTC, datetime

from tests.test_live_bar_evidence_v1 import fixture
from tests.test_runner_liveness_v1 import STUDY, SYMBOL
from trade_platform.live_bar_evidence_v1 import PostgresLiveBarStoreV1
from trade_platform.runner_liveness_v1 import (
    PostgresRunnerLivenessStoreV1,
    RunnerLivenessError,
    RunnerProgressV1,
)


@unittest.skipUnless(os.environ.get('POSTGRES_TEST_DSN'), 'POSTGRES_TEST_DSN not configured')
class ConsumedLiveBarsPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from alembic import command
        from alembic.config import Config
        config = Config('alembic.ini')
        config.set_main_option('sqlalchemy.url', os.environ['POSTGRES_TEST_DSN'].replace(
            'postgresql://', 'postgresql+psycopg://', 1))
        command.upgrade(config, 'head')

    def setUp(self):
        from trade_platform.persistence import PostgresDatabase
        self.database = PostgresDatabase(os.environ['POSTGRES_TEST_DSN'])
        self.store = PostgresRunnerLivenessStoreV1(self.database)
        self.start, self.progress, self.bar = fixture()

    def tearDown(self):
        self.database.close()

    def test_atomic_roundtrip_idempotency_conflict_and_point_in_time(self):
        # Unique candidate as the shared database retains evidence from all runs.
        from uuid import uuid4
        trial = str(uuid4())
        start_data = copy.deepcopy(self.start.identity)
        start_data['binding']['candidates'][0]['trial_id'] = trial
        from trade_platform.strategy_lab_study_v1 import identity_hash_v1
        start_data['binding_hash'] = identity_hash_v1(start_data['binding'])
        start = RunnerProgressV1.build(start_data)
        progress_data = copy.deepcopy(self.progress.identity)
        progress_data['binding_hash'] = start_data['binding_hash']
        progress_data['previous_progress_hash'] = start.content_hash
        progress = RunnerProgressV1.build(progress_data)
        self.assertTrue(self.store.record(start))
        before = datetime.now(UTC)
        self.assertTrue(self.store.record(progress))
        self.assertFalse(self.store.record(progress))
        repo = PostgresLiveBarStoreV1(self.database)
        self.assertIsNone(repo.for_candidate(study_id=str(STUDY), trial_id=trial, symbol=SYMBOL, as_of=before))
        self.assertEqual([self.bar], repo.for_candidate(study_id=str(STUDY), trial_id=trial,
                                                       symbol=SYMBOL, as_of=datetime.now(UTC)))
        conflict = copy.deepcopy(progress.identity)
        conflict['consumed_live_bars'][0]['live_bar']['bar']['close_price'] = '12'
        with self.assertRaises(RunnerLivenessError):
            self.store.record(RunnerProgressV1.build(conflict))
        import psycopg
        with self.database.transaction() as connection, connection.cursor() as cursor, self.assertRaises(psycopg.Error):
            cursor.execute('DELETE FROM runner_consumed_live_bars WHERE runner_session_id=%s',
                           (start.identity["runner_session_id"],))
        from trade_platform.persistence import PersistenceError
        duplicate = copy.deepcopy(progress.identity)
        duplicate['sequence'] += 1
        duplicate['previous_progress_hash'] = progress.content_hash
        duplicate['host_wall_micros'] += 1
        with self.assertRaises(PersistenceError):
            self.store.record(RunnerProgressV1.build(duplicate))
        with self.database.transaction() as connection, connection.cursor() as cursor, self.assertRaises(psycopg.Error):
            cursor.execute('UPDATE runner_consumed_live_bars SET observation_index=99 WHERE runner_session_id=%s',
                           (start.identity["runner_session_id"],))
        # Restart means a new repository instance, with the same complete verified result.
        self.assertEqual([self.bar], PostgresLiveBarStoreV1(self.database).for_candidate(
            study_id=str(STUDY), trial_id=trial, symbol=SYMBOL, as_of=datetime.now(UTC)))

    def test_invalid_source_rolls_back_progress(self):
        self.store.record(self.start)
        changed = copy.deepcopy(self.progress.identity)
        from uuid import uuid4
        changed['consumed_live_bars'][0]['live_bar']['capture_source_id'] = str(uuid4())
        with self.assertRaises(RunnerLivenessError):
            self.store.record(RunnerProgressV1.build(changed))
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM runner_liveness_progress WHERE runner_session_id=%s',
                           (self.start.identity['runner_session_id'],))
            self.assertEqual(1, cursor.fetchone()[0])
