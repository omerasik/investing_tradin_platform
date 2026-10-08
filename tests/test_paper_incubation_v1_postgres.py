"""Phase R10 core -- paper fills persist once per signal; a divergent refill is refused (real PostgreSQL)."""

from __future__ import annotations

import dataclasses
import os
import shutil
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PaperIncubationStorePostgresTests(unittest.TestCase):
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
        self.temp = Path(tempfile.mkdtemp(prefix="r10-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_fills_round_trip_idempotently_and_parity_is_enforced(self) -> None:
        from tests.test_live_signals_v1 import _live_bars
        from tests.test_paper_incubation_v1 import incubating_signals
        from tests.test_strategy_sdk_v1 import _walk
        from trade_platform.live_signals_v1 import PostgresLiveSignalStoreV1
        from trade_platform.paper_incubation_v1 import (
            PaperIncubationEngineV1,
            PaperIncubationError,
            PostgresPaperIncubationStoreV1,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1

        bars = _live_bars(_walk(400, seed=21))
        study, signals = incubating_signals(self.temp, bars)
        self.assertTrue(signals)
        ours = {signal.signal_id for signal in signals}
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            PostgresStrategyLabLedgerV1(database).register_study(study)
            live_store = PostgresLiveSignalStoreV1(database)
            for signal in signals:
                live_store.record(signal)
            store = PostgresPaperIncubationStoreV1(database)
            pending = [item for item in store.pending_signals("BTCUSDT") if item.signal_id in ours]
            self.assertEqual(len(signals), len(pending))
            engine = PaperIncubationEngineV1(gross_cost_policy_v1())
            engine.add(pending)
            fills = engine.on_bars(bars)
            self.assertTrue(fills)
            for fill in fills:
                self.assertTrue(store.record(fill))
                self.assertFalse(store.record(fill))  # same fill again: idempotent
            filled = {fill.identity["signal_id"] for fill in fills}
            still = {str(item.signal_id) for item in store.pending_signals("BTCUSDT") if item.signal_id in ours}
            self.assertEqual(set(), still & filled)
            stored = {item["signal_id"]: item for item in store.fills(symbol="BTCUSDT")}
            for fill in fills:
                self.assertEqual(dict(fill.identity), stored[fill.identity["signal_id"]])
            # A replay that disagrees with a recorded fill is a parity violation, not a second fill.
            first = fills[0]
            moved = [dataclasses.replace(bar, bar=dataclasses.replace(bar.bar, open_price=bar.bar.open_price + 1))
                     if bar.bar.bar_open_micros == first.identity["fill_bar_open_micros"] else bar for bar in bars]
            replay = PaperIncubationEngineV1(gross_cost_policy_v1())
            replay.add(pending)
            diverged = next(f for f in replay.on_bars(moved) if f.identity["signal_id"] == first.identity["signal_id"])
            with self.assertRaises(PaperIncubationError):
                store.record(diverged)
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
