"""Execution schedule parity with the unchanged SDK; no market or benchmark runs."""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import numpy as np

from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from tests.test_strategy_lab_walk_forward_v1 import RULE, _plan
from tests.test_strategy_sdk_v1 import LAG_US, START, _params, _rows, _walk
from trade_platform.strategy_lab_study_v1 import ParameterSpaceV1, SearchModeV1, SearchPlanV1
from trade_platform.strategy_lab_walk_forward_v1 import (
    _held_decision_indices_v1,
    _positions_from_schedule_v1,
    run_walk_forward_v1,
)
from trade_platform.strategy_sdk_v1 import (
    FAMILIES_V1,
    BarsV1,
    held_positions_v1,
    restrict_to_bound_v1,
)


class ExecutionScheduleParityTests(unittest.TestCase):
    def test_reference_parity_for_lags_gaps_segment_edges_and_fill_collisions(self) -> None:
        rows = _rows(_walk(4500), skip_minutes=frozenset(range(100, 111)), day_gap_after=2000)
        # Include single-bar segments and explicit live-session boundaries.
        keys = [i // 1500 if i != 1501 else -1 for i in range(len(rows))]
        bars = BarsV1.from_rows(rows, segment_keys=keys)
        rng = np.random.default_rng(81)
        targets = rng.integers(-1, 2, bars.size)
        original = targets.copy()
        for lag in (0, 2_000_000, 5_000_000, 30_000_000, 60_000_000, 600_000_000, 10**12):
            with self.subTest(lag=lag):
                schedule = _held_decision_indices_v1(bars, lag)
                actual = _positions_from_schedule_v1(targets, schedule)
                np.testing.assert_array_equal(held_positions_v1(bars, targets, lag), actual)
                self.assertFalse(schedule.flags.writeable)
                np.testing.assert_array_equal(targets, original)

    def test_future_targets_do_not_change_earlier_positions(self) -> None:
        bars = BarsV1.from_rows(_rows(_walk(400)))
        targets = np.ones(bars.size, dtype=np.int64)
        changed = targets.copy()
        changed[200:] = -1
        schedule = _held_decision_indices_v1(bars, LAG_US)
        np.testing.assert_array_equal(_positions_from_schedule_v1(targets, schedule)[:200],
                                      _positions_from_schedule_v1(changed, schedule)[:200])

    def test_restricted_windows_and_all_family_ties_keep_sdk_and_decimal_decisions(self) -> None:
        for closes in (_walk(4000), [Decimal("100")] * 4000):
            bars = BarsV1.from_rows(_rows(closes, day_gap_after=2200))
            cut = restrict_to_bound_v1(bars, START + timedelta(days=4), timedelta(seconds=2))
            schedule = _held_decision_indices_v1(cut, LAG_US)
            for name, family in FAMILIES_V1.items():
                for params in _params(name):
                    with self.subTest(family=name, params=params, flat=closes[0] == closes[-1]):
                        targets, ties = family.targets_f64(cut, params)
                        exact = family.targets_decimal(cut, params)
                        np.testing.assert_array_equal(exact, targets)
                        np.testing.assert_array_equal(held_positions_v1(cut, exact, LAG_US),
                            _positions_from_schedule_v1(targets, schedule))
                        repeated, repeated_ties = family.targets_f64(cut, params)
                        np.testing.assert_array_equal(targets, repeated)
                        self.assertEqual(ties, repeated_ties)


class WalkForwardReferenceParityTests(unittest.TestCase):
    def test_complete_reports_and_trial_identities_match_the_reference_for_all_families(self) -> None:
        with tempfile.TemporaryDirectory(prefix="performance-parity-") as directory:
            base, root = build_window_and_study(Path(directory), family="trend_ma_cross")
            from trade_platform.strategy_sdk_v1 import load_bars_v1

            binding, = base.datasets
            bars = load_bars_v1(root, binding.dataset_version_id, expected_content_hash=binding.content_hash)
            for family in FAMILIES_V1.values():
                # Two declared values per numeric dimension, both directions: <=16 trials.
                domains = tuple(dataclasses.replace(domain, values=domain.values[:2])
                                for domain in family.parameter_space().domains)
                space = ParameterSpaceV1.of(*domains)
                study = dataclasses.replace(base, strategy=family.spec(), parameter_space=space,
                    search=SearchPlanV1(SearchModeV1.EXHAUSTIVE, space.cardinality))
                before = (study.content_hash, tuple(study.trials()))
                optimized = run_walk_forward_v1(study, _plan(), RULE, bars=bars)
                lag = study.timing_lag
                cut = restrict_to_bound_v1(bars, _plan().evaluation_end_exclusive, lag)
                with patch("trade_platform.strategy_lab_walk_forward_v1._positions_from_schedule_v1",
                    side_effect=lambda targets, schedule, cut=cut, lag=lag: held_positions_v1(
                        cut, targets, lag // timedelta(microseconds=1))):
                    reference = run_walk_forward_v1(study, _plan(), RULE, bars=bars)
                with self.subTest(family=family.family):
                    self.assertEqual(reference.identity, optimized.identity)
                    self.assertEqual(reference.report_hash, optimized.report_hash)
                    self.assertEqual(before, (study.content_hash, tuple(study.trials())))
                    self.assertEqual(optimized.report_hash,
                                     run_walk_forward_v1(study, _plan(), RULE, bars=bars).report_hash)


if __name__ == "__main__":
    unittest.main()
