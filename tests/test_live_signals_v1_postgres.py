"""Phase R8 core -- live signals persist immutably and cannot claim validation (real PostgreSQL)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class LiveSignalStorePostgresTests(unittest.TestCase):
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
        self.temp = Path(tempfile.mkdtemp(prefix="live-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_signals_round_trip_and_are_idempotent(self) -> None:
        from tests.test_live_signals_v1 import DAY0, _live_bars, opened_gate
        from tests.test_strategy_lab_e2e_fixture import build_window_and_study
        from tests.test_strategy_sdk_v1 import _walk
        from trade_platform.live_signals_v1 import (
            LiveStrategyRunnerV1,
            PostgresLiveSignalStoreV1,
            WatchedCandidateV1,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1

        study, _ = build_window_and_study(self.temp, family="mean_reversion_z")
        trial = next(str(t.trial_id) for t in study.trials()
                     if study.parameter_space.typed_point(t.parameters) ==
                     {"lookback_bars": 30, "entry_z": Decimal("1.5"), "exit_z": Decimal("0.5"),
                      "direction": "long_short"})
        candidate = WatchedCandidateV1(study, trial, "BTCUSDT", "RESEARCH_WATCH", "e" * 64)
        runner = LiveStrategyRunnerV1([candidate], holdout_gate=opened_gate(), clock=lambda: DAY0)
        signals = []
        for bar in _live_bars(_walk(400, seed=21)):
            signals.extend(runner.on_bars([bar]))
        self.assertTrue(signals)
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            PostgresStrategyLabLedgerV1(database).register_study(study)
            store = PostgresLiveSignalStoreV1(database)
            for signal in signals:
                store.record(signal)
                self.assertFalse(store.record(signal))  # idempotent by content identity
            recent = {item["signal_id"]: item for item in store.recent(limit=1000)}
            for signal in signals:
                self.assertEqual("RESEARCH_WATCH", recent[str(signal.signal_id)]["authority"])
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
