"""Phase R6 -- the holdout registry is one-shot per cycle (real PostgreSQL).

Only prospectively declared TEST cycles far in the future are opened (a unique
cycle per run), never the current cycle-2026-08-20. Fixture packets only.
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

    def test_a_cycle_opens_once_and_validation_states_are_recorded(self) -> None:
        from tests.test_strategy_lab_e2e_fixture import build_window_and_study
        from tests.test_strategy_lab_validation_v1 import authorized_packet
        from tests.test_strategy_sdk_v1 import _rows, _walk
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_validation_v1 import (
            PostgresHoldoutRegistryV1,
            StrategyLabValidationError,
            candidate_lifecycle_v1,
            new_research_cycle_v1,
            validate_on_holdout_v1,
        )
        from trade_platform.strategy_sdk_v1 import BarsV1

        study, _ = build_window_and_study(self.temp, family="trend_ma_cross")
        trials = [str(t.trial_id) for t in study.trials()
                  if study.parameter_space.typed_point(t.parameters)["fast_bars"]
                  < study.parameter_space.typed_point(t.parameters)["slow_bars"]][:2]
        start = datetime(2200, 1, 1, tzinfo=UTC) + timedelta(days=random.randrange(0, 300_000))
        cycle = new_research_cycle_v1(holdout_start=start, registered_at=datetime.now(UTC), note="pg test")
        packet = authorized_packet(study, trials, cycle)
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            PostgresStrategyLabLedgerV1(database).register_study(study)
            registry = PostgresHoldoutRegistryV1(database)
            opening = registry.open_holdout(packet, opened_by="pg-test")
            with self.assertRaisesRegex(StrategyLabValidationError, "already_opened"):
                registry.open_holdout(packet, opened_by="pg-test")
            stored = registry.opening(cycle.cycle_id)
            assert stored is not None
            self.assertEqual(packet.content_hash, stored.preregistration_hash)
            bars = BarsV1.from_rows(_rows(_walk(2 * 1440, seed=9), start=start))
            run = validate_on_holdout_v1(packet, opening, bars)
            registry.record_validation(run)
            states = candidate_lifecycle_v1(registry.states(study.study_id))
            for trial in trials:
                self.assertIn(states[trial], {"INCUBATING", "HOLDOUT_FAILED_REJECTED"})
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
