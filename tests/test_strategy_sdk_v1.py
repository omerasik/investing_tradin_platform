"""Phase R4.6 -- Strategy SDK v1: causal execution, tier agreement, near-tie flags, evaluator."""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from trade_platform.strategy_sdk_v1 import (
    FAMILIES_V1,
    BarStrategyEvaluatorV1,
    BarsV1,
    StrategySdkError,
    held_positions_v1,
    restrict_to_bound_v1,
)

START = datetime(2026, 6, 1, tzinfo=UTC)
LAG_US = 2_000_000


def _rows(closes: list[Decimal], *, start: datetime = START, skip_minutes: frozenset[int] = frozenset(),
          day_gap_after: int | None = None) -> list[tuple[Any, ...]]:
    rows = []
    minute = 0
    previous = closes[0]
    for index, close in enumerate(closes):
        while minute in skip_minutes:
            minute += 1
        if day_gap_after is not None and index == day_gap_after:
            minute += 2 * 1440  # a whole missing UTC day
        opened = start + timedelta(minutes=minute)
        high = max(previous, close) + Decimal("0.5")
        low = min(previous, close) - Decimal("0.5")
        rows.append((opened, opened + timedelta(minutes=1), None, previous, high, low, close))
        previous = close
        minute += 1
    return rows


def _walk(n: int, seed: int = 7) -> list[Decimal]:
    rng = random.Random(seed)
    price = Decimal("30000.0")
    out = []
    for _ in range(n):
        price += Decimal(rng.choice(range(-40, 41))) / 10
        out.append(price)
    return out


def _params(family: str) -> list[dict[str, Any]]:
    space = FAMILIES_V1[family].parameter_space()
    points = [space.typed_point(space.point_at(i)) for i in range(0, space.cardinality, max(1, space.cardinality // 12))]
    return [p for p in points if FAMILIES_V1[family].inadmissible(p) is None][:6]


class ExecutionConventionTests(unittest.TestCase):
    def test_a_decision_fills_at_the_next_strictly_later_open(self) -> None:
        bars = BarsV1.from_rows(_rows(_walk(10)))
        targets = [0, 1, 1, 1, 0, 0, 0, 0, 0, 0]
        held = held_positions_v1(bars, targets, LAG_US)
        # Decided at close(1)+2s -> fills at open(3) (open(2) == close(1) is not later).
        self.assertEqual([0, 0, 0, 1, 1, 1, 0, 0, 0, 0], held)
        held_60 = held_positions_v1(bars, targets, 60_000_000)
        # close(1)+60s == open(3): not strictly later -> open(4).
        self.assertEqual([0, 0, 0, 0, 1, 1, 1, 0, 0, 0], held_60)

    def test_positions_are_flat_across_a_missing_day_and_at_the_end(self) -> None:
        bars = BarsV1.from_rows(_rows(_walk(12), day_gap_after=6))
        self.assertEqual([0] * 6 + [1] * 6, list(bars.segment))
        held = held_positions_v1(bars, [1] * 12, LAG_US)
        self.assertEqual(0, held[5])  # last bar of the first segment
        self.assertEqual(0, held[6])  # first bar of the next segment: nothing decided there yet
        self.assertEqual(0, held[-1])
        # A decision in segment 0 whose fill would land in segment 1 never fills.
        targets = [0, 0, 0, 0, 0, 1] + [0] * 6
        self.assertEqual([0] * 12, held_positions_v1(bars, targets, LAG_US))


class CausalityAndTierTests(unittest.TestCase):
    def test_targets_never_depend_on_later_bars(self) -> None:
        closes = _walk(3000)
        base = BarsV1.from_rows(_rows(closes))
        cut = 2000
        changed = closes[:cut] + [c + Decimal("1234.5") for c in closes[cut:]]
        other = BarsV1.from_rows(_rows(changed))
        for family_name, family in FAMILIES_V1.items():
            for params in _params(family_name):
                with self.subTest(family=family_name, params=params):
                    a, _ = family.targets_f64(base, params)
                    b, _ = family.targets_f64(other, params)
                    self.assertTrue(np.array_equal(a[:cut], b[:cut]))
                    self.assertEqual(family.targets_decimal(base, params)[:cut],
                                     family.targets_decimal(other, params)[:cut])

    def test_float_search_and_decimal_authority_agree_without_near_ties(self) -> None:
        bars = BarsV1.from_rows(_rows(_walk(4000, seed=11), skip_minutes=frozenset({50, 51, 900})))
        compared = 0
        for family_name, family in FAMILIES_V1.items():
            for params in _params(family_name):
                with self.subTest(family=family_name, params=params):
                    fast, ties = family.targets_f64(bars, params)
                    exact = family.targets_decimal(bars, params)
                    if ties == 0:
                        compared += 1
                        self.assertEqual(exact, [int(v) for v in fast])
        self.assertGreaterEqual(compared, 8)  # never vacuous

    def test_long_drifting_series_never_diverge_between_tiers(self) -> None:
        """PIT review regression: exact decisions, zero thresholds included, no flag needed."""
        rng = random.Random(42)
        price = Decimal("30000.0")
        closes = []
        for _ in range(120_000):
            price += Decimal(rng.choice(range(-40, 45))) / 10  # upward drift
            closes.append(price)
        bars = BarsV1.from_rows(_rows(closes))
        points = {
            "mean_reversion_z": [
                {"lookback_bars": 30, "entry_z": Decimal("2"), "exit_z": Decimal("0.5"), "direction": "long_short"},
                {"lookback_bars": 30, "entry_z": Decimal("1.5"), "exit_z": Decimal("0"), "direction": "long_short"},
                {"lookback_bars": 240, "entry_z": Decimal("2.5"), "exit_z": Decimal("0"), "direction": "long_only"},
                {"lookback_bars": 1440, "entry_z": Decimal("3.5"), "exit_z": Decimal("1"), "direction": "long_short"},
            ],
            "trend_ma_cross": [
                {"fast_bars": 15, "slow_bars": 60, "band": Decimal("0"), "direction": "long_short"},
                {"fast_bars": 30, "slow_bars": 120, "band": Decimal("0.0005"), "direction": "long_short"},
                {"fast_bars": 480, "slow_bars": 5760, "band": Decimal("0.005"), "direction": "long_only"},
            ],
            "breakout_channel": [
                {"entry_bars": 60, "exit_bars": 15, "direction": "long_short"},
                {"entry_bars": 2880, "exit_bars": 480, "direction": "long_short"},
            ],
        }
        for family_name, params_list in points.items():
            family = FAMILIES_V1[family_name]
            for params in params_list:
                with self.subTest(family=family_name, params=params):
                    fast, _ = family.targets_f64(bars, params)
                    self.assertEqual(family.targets_decimal(bars, params), [int(v) for v in fast])

    def test_trend_refuses_rather_than_wraps_on_very_fine_price_grids(self) -> None:
        # PIT review probe6: 9-decimal prices near 5200 made an unguarded int64 product wrap.
        closes = [Decimal("5200.000000001") + Decimal(i % 7) / 1_000_000_000 for i in range(6000)]
        bars = BarsV1.from_rows(_rows(closes))
        family = FAMILIES_V1["trend_ma_cross"]
        params = {"fast_bars": 480, "slow_bars": 5760, "band": Decimal("0.0005"), "direction": "long_short"}
        with self.assertRaises(StrategySdkError):
            family.targets_f64(bars, params)

    def test_a_bound_restricted_window_keeps_its_exact_ticks(self) -> None:
        bars = BarsV1.from_rows(_rows(_walk(500)))
        cut = restrict_to_bound_v1(bars, START + timedelta(minutes=300), timedelta(seconds=2))
        self.assertEqual(cut.size, cut.close_ticks.size)
        self.assertEqual((bars.close_scale, bars.close_centre), (cut.close_scale, cut.close_centre))
        params = {"lookback_bars": 30, "entry_z": Decimal("2"), "exit_z": Decimal("0.5"), "direction": "long_short"}
        family = FAMILIES_V1["mean_reversion_z"]
        fast, _ = family.targets_f64(cut, params)
        self.assertEqual(family.targets_decimal(cut, params), [int(v) for v in fast])

    def test_an_exact_boundary_is_flagged_as_a_near_tie(self) -> None:
        # Constant prices make fast/slow - 1 exactly 0 == band 0 on every ready bar.
        bars = BarsV1.from_rows(_rows([Decimal("100")] * 300))
        family = FAMILIES_V1["trend_ma_cross"]
        _, ties = family.targets_f64(bars, {"fast_bars": 15, "slow_bars": 60, "band": Decimal("0"),
                                             "direction": "long_short"})
        self.assertGreater(ties, 0)


class EvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="sdk-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_a_policy_bound_study_evaluates_one_trial_end_to_end(self) -> None:
        from tests.test_strategy_lab_e2e_fixture import build_window_and_study

        study, data_root = build_window_and_study(self.temp, family="breakout_channel")
        evaluator = BarStrategyEvaluatorV1(data_root)
        trial = next(iter(study.trials()))
        params = study.parameter_space.typed_point(trial.parameters)
        evaluation = evaluator(study, params)
        if FAMILIES_V1["breakout_channel"].inadmissible(params) is None:
            self.assertEqual("EVALUATED", evaluation.outcome.value)
            self.assertEqual("SEARCH_NON_AUTHORITATIVE", evaluation.metrics["numeric_tier"])
            self.assertEqual("GROSS_NON_PROMOTABLE", evaluation.metrics["cost_mode"])
            self.assertTrue(all(not isinstance(v, float) for v in evaluation.metrics.values()))
        else:
            self.assertEqual("INADMISSIBLE_PARAMETERS", evaluation.outcome.value)

    def test_bars_beyond_the_bound_are_never_evaluated(self) -> None:
        bars = BarsV1.from_rows(_rows(_walk(100)))
        bound = START + timedelta(minutes=50)
        cut = restrict_to_bound_v1(bars, bound, timedelta(seconds=2))
        self.assertTrue(int(cut.close_us[-1]) + LAG_US < int((bound - datetime(1970, 1, 1, tzinfo=UTC))
                                                             // timedelta(microseconds=1)))
        with self.assertRaises(StrategySdkError):
            restrict_to_bound_v1(bars, START, timedelta(seconds=2))


if __name__ == "__main__":
    unittest.main()
