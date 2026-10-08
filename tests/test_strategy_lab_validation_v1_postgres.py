"""Phase R6 -- the registry's one-shot guarantees against real PostgreSQL.

The real cycle-2026-08-20 is NEVER opened here. A TEST cycle is inserted with
an explicit past registration instant (production code never supplies one;
the database stamps it) on a random day in 2000-2020, so reruns against a
shared database do not collide. Fixture packets and fixture archive bytes only.
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

    def test_open_once_validate_once_and_refuse_everything_else(self) -> None:
        from tests.test_strategy_lab_e2e_fixture import SyntheticDays, build_window_and_study
        from tests.test_strategy_lab_validation_v1 import authorized_packet
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.public_archive_research_bars_v1 import (
            acquire_and_derive_day_v1,
            build_research_bar_dataset_v1,
        )
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_validation_v1 import (
            CURRENT_CYCLE_V1,
            PostgresHoldoutRegistryV1,
            StrategyLabValidationError,
            candidate_lifecycle_v1,
            validate_on_holdout_v1,
        )

        study, data_root = build_window_and_study(self.temp, family="trend_ma_cross")
        trials = [str(t.trial_id) for t in study.trials()
                  if study.parameter_space.typed_point(t.parameters)["fast_bars"]
                  < study.parameter_space.typed_point(t.parameters)["slow_bars"]][:2]
        start = datetime(2000, 1, 1, tzinfo=UTC) + timedelta(days=random.randrange(0, 7600))
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            PostgresStrategyLabLedgerV1(database).register_study(study)
            cycle_id = f"cycle-{start.date().isoformat()}"
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO strategy_lab_research_cycles (cycle_id, holdout_start, registered_at, "
                               "note) VALUES (%s,%s,%s,%s)", (cycle_id, start, start - timedelta(days=1), "pg test"))
            registry = PostgresHoldoutRegistryV1(database)
            cycle = registry.cycle(cycle_id)
            # A future cycle is registered prospectively, but cannot be opened before its span has passed.
            future = registry.register_cycle(holdout_start=datetime(2400, 1, 1, tzinfo=UTC)
                                             + timedelta(days=random.randrange(0, 30000)), note="pg future")
            with self.assertRaises(Exception):  # noqa: B017 - the database CHECK refuses an open-ended future span
                registry.open_holdout(authorized_packet(study, trials, future), opened_by="pg-test")
            with self.assertRaises(StrategyLabValidationError):
                registry.register_cycle(holdout_start=datetime(2026, 8, 1, tzinfo=UTC), note="retroactive")
            draft = authorized_packet(study, trials, cycle, minimum_trades=None)
            with self.assertRaises(StrategyLabValidationError):
                registry.open_holdout(draft, opened_by="pg-test")
            packet = authorized_packet(study, trials, cycle)
            opening = registry.open_holdout(packet, opened_by="pg-test")
            with self.assertRaisesRegex(StrategyLabValidationError, "already_opened"):
                registry.open_holdout(packet, opened_by="pg-test")
            self.assertIsNone(registry.opening(CURRENT_CYCLE_V1.cycle_id))  # the real holdout stays closed
            store = ResearchFrameStoreV1(data_root)
            archive = data_root.parent / "archive"
            fetch = SyntheticDays()
            for offset in range(2):
                acquire_and_derive_day_v1(archive, "BTCUSDT", start.date() + timedelta(days=offset), store=store,
                                          evict=True, fetch=fetch)
            holdout = build_research_bar_dataset_v1(archive, "BTCUSDT", start.date(),
                                                    start.date() + timedelta(days=1), store=store)
            run = validate_on_holdout_v1(packet, opening, holdout, store=store)
            registry.record_validation(run)
            with self.assertRaisesRegex(StrategyLabValidationError, "already_validated"):
                registry.record_validation(run)
            states = candidate_lifecycle_v1(registry.states(study.study_id))
            for trial in trials:
                self.assertIn(states[trial], {"INCUBATING", "HOLDOUT_FAILED_REJECTED"})
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
