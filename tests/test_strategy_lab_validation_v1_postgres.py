"""Phase R6 -- the registry's database-level holdout guarantees (real PostgreSQL).

The real cycle-2026-08-20 is NEVER opened here. Registration and opening
instants are stamped by the database, so a past span can never be registered or
opened by a test (or anyone else); these tests therefore prove the refusals. The
open -> validate path is covered offline with the private issuers.
"""

from __future__ import annotations

import os
import random
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class HoldoutRegistryPostgresTests(unittest.TestCase):
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
        self.temp = Path(tempfile.mkdtemp(prefix="r6-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_the_database_refuses_aliases_backdating_premature_and_draft_openings(self) -> None:
        from tests.test_strategy_lab_e2e_fixture import build_window_and_study
        from tests.test_strategy_lab_validation_v1 import authorized_packet, established_rerun
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_validation_v1 import (
            CURRENT_CYCLE_V1,
            PostgresHoldoutRegistryV1,
            PreregistrationV1,
            StrategyLabValidationError,
            ValidationRunV1,
        )

        study, _ = build_window_and_study(self.temp, family="trend_ma_cross")
        trials = [str(t.trial_id) for t in study.trials()
                  if study.parameter_space.typed_point(t.parameters)["fast_bars"]
                  < study.parameter_space.typed_point(t.parameters)["slow_bars"]][:2]
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            PostgresStrategyLabLedgerV1(database).register_study(study)
            registry = PostgresHoldoutRegistryV1(database)
            # N2 regression: a backdated alias of the current holdout cannot be inserted,
            # because the database stamps registered_at with its own clock.
            with self.assertRaises(PersistenceError), database.transaction() as connection, \
                    connection.cursor() as cursor:
                cursor.execute("INSERT INTO strategy_lab_research_cycles (cycle_id, holdout_start, registered_at, "
                               "note) VALUES ('cycle-2026-08-21', TIMESTAMPTZ '2026-08-21 00:00:00+00', "
                               "TIMESTAMPTZ '2026-08-01 00:00:00+00', 'alias attempt')")
            with self.assertRaises(StrategyLabValidationError):
                registry.register_cycle(holdout_start=datetime(2026, 9, 1, tzinfo=UTC), note="retroactive")
            future = registry.register_cycle(
                holdout_start=datetime(2400, 1, 1, tzinfo=UTC) + timedelta(days=random.randrange(0, 30000)),
                note="pg future")
            self.assertGreater(future.holdout_start, future.registered_at)
            # Prospective, but its span has not passed: the database refuses the opening.
            with self.assertRaises(PersistenceError):
                registry.open_holdout(authorized_packet(study, trials, future), opened_by="pg-test")
            draft = PreregistrationV1(study=study, rerun=established_rerun(study, trials), symbol="BTCUSDT")
            with self.assertRaises(StrategyLabValidationError):
                registry.open_holdout(draft, opened_by="pg-test")
            self.assertIsNone(registry.opening(CURRENT_CYCLE_V1.cycle_id))  # the real holdout stays closed
            forged = ValidationRunV1({"cycle_id": CURRENT_CYCLE_V1.cycle_id, "preregistration_hash": "a" * 64,
                                      "study_id": str(study.study_id), "candidates": [],
                                      "holdout": {"dataset_content_hash": "b" * 64}}, "c" * 64)
            with self.assertRaises(StrategyLabValidationError):
                registry.record_validation(forged)
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
