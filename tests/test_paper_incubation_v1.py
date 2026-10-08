"""Phase R10 core -- paper fills at the next strictly later proven bar open; policy-neutral reports."""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from tests.test_live_signals_v1 import DAY0, _live_bars, opened_gate
from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from tests.test_strategy_sdk_v1 import _walk
from trade_platform.live_signals_v1 import LiveBarV1, LiveStrategyRunnerV1, WatchedCandidateV1
from trade_platform.paper_incubation_v1 import (
    FILL_STATUS_BAR_NOT_OBSERVED,
    FILL_STATUS_FILLED,
    FILL_STATUS_OPEN_AMBIGUOUS,
    UNRESOLVED_OR7_INCUBATION_DAYS,
    PaperIncubationEngineV1,
    PaperIncubationError,
    PendingSignalV1,
    expected_fill_bar_open_micros_v1,
    fill_parity_v1,
    incubation_report_v1,
)
from trade_platform.strategy_lab_policies_v1 import (
    CostPolicyV1,
    FeeScheduleV1,
    SlippageScenarioV1,
    gross_cost_policy_v1,
)

GROSS = gross_cost_policy_v1()
MINUTE = 60_000_000


def incubating_signals(temp: Path, bars: list[LiveBarV1], authority: str = "INCUBATING") -> tuple[Any, list[Any]]:
    study, _ = build_window_and_study(temp, family="mean_reversion_z")
    trial = next(str(t.trial_id) for t in study.trials()
                 if study.parameter_space.typed_point(t.parameters) ==
                 {"lookback_bars": 30, "entry_z": Decimal("1.5"), "exit_z": Decimal("0.5"),
                  "direction": "long_short"})
    runner = LiveStrategyRunnerV1([WatchedCandidateV1(study, trial, "BTCUSDT", authority, "v" * 64)],
                                  holdout_gate=opened_gate(), clock=lambda: DAY0)
    signals = []
    for bar in bars:
        signals.extend(runner.on_bars([bar]))
    return study, signals


def _run(engine: PaperIncubationEngineV1, signals: list[Any], bars: list[LiveBarV1]) -> list[Any]:
    """Live order: each bar first resolves pending fills, then may add new signals."""
    by_bar: dict[int, list[Any]] = {}
    for signal in signals:
        by_bar.setdefault(int(signal.identity["evidence"]["bar_open_micros"]), []).append(signal)
    fills = []
    for bar in bars:
        fills.extend(engine.on_bars([bar]))
        engine.add(PendingSignalV1.from_signal(s) for s in by_bar.get(bar.bar.bar_open_micros, []))
    return fills


class FillTests(unittest.TestCase):
    temp: Path
    bars: list[LiveBarV1]
    signals: list[Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = Path(tempfile.mkdtemp(prefix="r10-"))
        cls.bars = _live_bars(_walk(600, seed=21))
        _, cls.signals = incubating_signals(cls.temp, cls.bars)
        assert cls.signals

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def test_the_fill_bar_is_the_next_strictly_later_minute(self) -> None:
        at = datetime(2026, 10, 9, 12, 1, 0, 300_000, tzinfo=UTC)
        self.assertEqual(int(datetime(2026, 10, 9, 12, 2, tzinfo=UTC).timestamp()) * 1_000_000,
                         expected_fill_bar_open_micros_v1(at))
        boundary = datetime(2026, 10, 9, 12, 2, tzinfo=UTC)
        self.assertEqual(int(datetime(2026, 10, 9, 12, 3, tzinfo=UTC).timestamp()) * 1_000_000,
                         expected_fill_bar_open_micros_v1(boundary))

    def test_every_signal_fills_at_its_fill_bar_open_price(self) -> None:
        engine = PaperIncubationEngineV1(GROSS)
        fills = _run(engine, self.signals, self.bars)
        by_open = {bar.bar.bar_open_micros: bar for bar in self.bars}
        last = self.bars[-1].bar.bar_open_micros
        expected = [s for s in self.signals if expected_fill_bar_open_micros_v1(s.decided_at) <= last]
        self.assertEqual(len(expected), len(fills))
        self.assertEqual(len(self.signals) - len(expected), engine.pending)
        for fill in fills:
            self.assertEqual(FILL_STATUS_FILLED, fill.status)
            bar = by_open[fill.identity["fill_bar_open_micros"]]
            self.assertEqual(bar.bar.open_price, Decimal(fill.identity["fill_price"]))
            self.assertGreater(fill.identity["fill_bar_open_micros"], fill.identity["decided_at_micros"])
            self.assertEqual("UNIT_EXPOSURE_PENDING_OR_11", fill.identity["sizing"])

    def test_a_missing_fill_bar_is_never_replaced_by_a_later_one(self) -> None:
        first = PendingSignalV1.from_signal(self.signals[0])
        missing = first.fill_bar_open_micros
        bars = [bar for bar in self.bars if bar.bar.bar_open_micros != missing]
        fills = _run(PaperIncubationEngineV1(GROSS), self.signals, bars)
        self.assertEqual(FILL_STATUS_BAR_NOT_OBSERVED, fills[0].status)
        self.assertIsNone(fills[0].identity["fill_price"])

    def test_an_ambiguous_open_is_not_filled(self) -> None:
        missing = PendingSignalV1.from_signal(self.signals[0]).fill_bar_open_micros
        bars = [dataclasses.replace(bar, bar=dataclasses.replace(bar.bar, open_is_sequence_ambiguous=True))
                if bar.bar.bar_open_micros == missing else bar for bar in self.bars]
        fills = _run(PaperIncubationEngineV1(GROSS), self.signals, bars)
        self.assertEqual(FILL_STATUS_OPEN_AMBIGUOUS, fills[0].status)
        self.assertIsNone(fills[0].identity["fill_price"])

    def test_live_and_replayed_fills_are_identical_and_divergence_is_named(self) -> None:
        live = _run(PaperIncubationEngineV1(GROSS), self.signals, self.bars)
        replay = _run(PaperIncubationEngineV1(GROSS), self.signals, self.bars)
        self.assertEqual([], fill_parity_v1(live, replay))
        target = live[0].identity["fill_bar_open_micros"]
        moved = [dataclasses.replace(bar, bar=dataclasses.replace(bar.bar, open_price=bar.bar.open_price + 1))
                 if bar.bar.bar_open_micros == target else bar for bar in self.bars]
        changed = _run(PaperIncubationEngineV1(GROSS), self.signals, moved)
        self.assertEqual([live[0].identity["signal_id"]], fill_parity_v1(live, changed))

    def test_research_watch_signals_are_not_incubated(self) -> None:
        _, watch = incubating_signals(self.temp / "w", self.bars, authority="RESEARCH_WATCH")
        with self.assertRaises(PaperIncubationError):
            PendingSignalV1.from_signal(watch[0])


def _identity(status: str, target_from: int, target_to: int, minute: int, price: str | None,
              policy: CostPolicyV1 = GROSS) -> dict[str, Any]:
    opened = int(DAY0.timestamp()) * 1_000_000 + minute * MINUTE
    return {"signal_id": str(uuid4()), "study_id": "s", "trial_id": "t", "symbol": "BTCUSDT", "status": status,
            "target_from": target_from, "target_to": target_to, "decided_at_micros": opened - MINUTE // 2,
            "fill_bar_open_micros": opened, "fill_price": price,
            "cost_policy_hash": policy.policy().content_hash}


class ReportTests(unittest.TestCase):
    def test_round_trips_are_unit_exposure_gross_and_never_promoted(self) -> None:
        fills = [_identity(FILL_STATUS_FILLED, 0, 1, 10, "100"), _identity(FILL_STATUS_FILLED, 1, -1, 20, "110"),
                 _identity(FILL_STATUS_FILLED, -1, 0, 30, "99")]
        report = incubation_report_v1(fills, GROSS, as_of=DAY0 + timedelta(days=2))
        (candidate,) = report["candidates"]
        self.assertEqual(["0.100000000000", "0.100000000000"], [t["gross_return"] for t in candidate["round_trips"]])
        self.assertEqual("0.200000000000", candidate["gross_return_sum"])
        self.assertEqual(4, candidate["unit_exposure_changes"])
        self.assertEqual("500.0000", candidate["break_even_cost_bps_per_side"])
        self.assertEqual("GROSS_NON_PROMOTABLE_NO_VERIFIED_FEE_SCHEDULE", candidate["net"])
        self.assertEqual(UNRESOLVED_OR7_INCUBATION_DAYS, candidate["required_days"])
        self.assertEqual(("INCUBATING", "INCUBATING"), (candidate["state"], report["state"]))
        self.assertEqual(0, candidate["open_position"])

    def test_net_follows_every_declared_scenario_of_a_verified_fee_schedule(self) -> None:
        policy = CostPolicyV1(
            FeeScheduleV1("BYBIT", "USDT_PERP", "VIP0", "2", "5.5", "owner", "2026-10-09", "fixture"),
            (SlippageScenarioV1("mild", "1", "fixture"), SlippageScenarioV1("harsh", "10", "fixture")))
        fills = [_identity(FILL_STATUS_FILLED, 0, 1, 10, "100", policy),
                 _identity(FILL_STATUS_FILLED, 1, 0, 20, "110", policy)]
        (candidate,) = incubation_report_v1(fills, policy, as_of=DAY0)["candidates"]
        self.assertEqual({"mild": "0.098700000000", "harsh": "0.096900000000"}, candidate["net"])

    def test_a_missing_fill_breaks_the_chain_until_a_fill_from_flat(self) -> None:
        fills = [_identity(FILL_STATUS_FILLED, 1, 0, 5, "90"),  # position before incubation: unknown
                 _identity(FILL_STATUS_FILLED, 0, 1, 10, "100"),
                 _identity(FILL_STATUS_BAR_NOT_OBSERVED, 1, 0, 20, None),
                 _identity(FILL_STATUS_FILLED, 0, -1, 30, "120"),  # would wrongly assume flat at 20
                 _identity(FILL_STATUS_FILLED, -1, 0, 40, "110")]
        (candidate,) = incubation_report_v1(fills, GROSS, as_of=DAY0)["candidates"]
        self.assertEqual(1, candidate["chain_breaks"])
        self.assertEqual(1, candidate["fills_skipped_unknown_position"])
        self.assertEqual(["0.083333333333"], [t["gross_return"] for t in candidate["round_trips"]])

    def test_holding_across_funding_instants_is_counted(self) -> None:
        fills = [_identity(FILL_STATUS_FILLED, 0, 1, 10, "100"),
                 _identity(FILL_STATUS_FILLED, 1, 0, 10 + 17 * 60, "100")]
        (candidate,) = incubation_report_v1(fills, GROSS, as_of=DAY0)["candidates"]
        self.assertEqual(2, candidate["funding_instants_crossed"])  # 08:00 and 16:00

    def test_fills_under_another_cost_policy_are_refused(self) -> None:
        other = CostPolicyV1(slippage_scenarios=(SlippageScenarioV1("mild", "1", "fixture"),))
        with self.assertRaises(PaperIncubationError):
            incubation_report_v1([_identity(FILL_STATUS_FILLED, 0, 1, 10, "100", other)], GROSS, as_of=DAY0)


if __name__ == "__main__":
    unittest.main()
