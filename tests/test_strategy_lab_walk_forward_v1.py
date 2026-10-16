"""Stage C.1.1 -- purged walk-forward evaluation over a policy-bound fixture study."""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from trade_platform.strategy_lab_manifest_v1 import RankDirectionV1
from trade_platform.strategy_lab_splits_v1 import (
    SplitModeV1,
    StrategyLabSplitError,
    WalkForwardPlanV1,
)
from trade_platform.strategy_lab_study_v1 import ParameterDomainV1, ParameterSpaceV1
from trade_platform.strategy_lab_walk_forward_v1 import (
    FOLD_EXCLUDED_NO_ELIGIBLE,
    FOLD_SELECTED,
    REASON_WALK_FORWARD_NOT_VALIDATION,
    StrategyLabWalkForwardError,
    WalkForwardSelectionRuleV1,
    interval_metrics_f64,
    interval_observations_v1,
    run_walk_forward_v1,
)
from trade_platform.strategy_sdk_v1 import BarsV1, load_bars_v1

DAY0 = datetime(2026, 6, 1, tzinfo=UTC)
H = timedelta(hours=1)
SPACE = ParameterSpaceV1.of(
    ParameterDomainV1.integer_values("fast_bars", [15, 30]),
    ParameterDomainV1.integer_values("slow_bars", [60, 120]),
    ParameterDomainV1.decimal_set("band", ["0"]),
    ParameterDomainV1.categorical_set("direction", ["long_only", "long_short"]),
)
RULE = WalkForwardSelectionRuleV1("total_return", RankDirectionV1.HIGHER_IS_BETTER)


def _plan(**overrides: object) -> WalkForwardPlanV1:
    values: dict[str, object] = {
        "mode": SplitModeV1.ROLLING, "train_duration": 12 * H, "test_duration": 6 * H, "step": 6 * H,
        "embargo": timedelta(minutes=10), "first_test_start": DAY0 + 12 * H,
        "evaluation_end_exclusive": DAY0 + 48 * H,
    }
    values.update(overrides)
    return WalkForwardPlanV1(**values)  # type: ignore[arg-type]


def _with_opens(bars: BarsV1, start: int, stop: int, factor: float) -> BarsV1:
    """Same bars with the float prices of ``[start, stop)`` scaled (what the search tier reads)."""
    def scaled(values: np.ndarray) -> np.ndarray:
        out = values.copy()
        out[start:stop] *= factor
        return out
    return dataclasses.replace(bars, open_f=scaled(bars.open_f), high_f=scaled(bars.high_f),
                               low_f=scaled(bars.low_f), close_f=scaled(bars.close_f))


class WalkForwardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = Path(tempfile.mkdtemp(prefix="walk-forward-"))
        cls.study, data_root = build_window_and_study(cls.temp, family="trend_ma_cross", space=SPACE)
        (binding,) = cls.study.datasets
        cls.bars = load_bars_v1(data_root, binding.dataset_version_id, expected_content_hash=binding.content_hash)
        cls.lag_us = cls.study.timing_lag // timedelta(microseconds=1)
        cls.report = run_walk_forward_v1(cls.study, _plan(), RULE, bars=cls.bars)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def test_report_is_development_evidence_with_explicit_provenance(self) -> None:
        identity = self.report.identity
        self.assertEqual(identity["evaluation_class"], "WALK_FORWARD_DEVELOPMENT")
        self.assertEqual(identity["numeric_tier"], "SEARCH_NON_AUTHORITATIVE")
        self.assertEqual(identity["cost_mode"], "GROSS_NON_PROMOTABLE")
        self.assertIs(identity["promotable"], False)
        self.assertIn(REASON_WALK_FORWARD_NOT_VALIDATION, identity["authority_reasons"])
        self.assertEqual(identity["study_content_hash"], self.study.content_hash)
        self.assertEqual(identity["multiple_testing_trial_count"], 8)
        self.assertEqual(identity["plan"], _plan().payload())
        self.assertEqual(identity["rule"]["tie_break"], "TRIAL_CONTENT_HASH_ASCENDING")
        self.assertEqual([f["fold"] for f in self.report.folds], [0, 1, 2, 3, 4, 5])
        for fold in self.report.folds:
            self.assertEqual(fold["status"], FOLD_SELECTED)
            self.assertGreater(fold["purged_intervals"], 0)
            self.assertEqual(fold["eligible_trials"] + fold["ineligible_trials"], 8)
            self.assertIn("test_metrics", fold["selected"])

    def test_identity_is_deterministic_and_binds_plan_and_rule(self) -> None:
        again = run_walk_forward_v1(self.study, _plan(), RULE, bars=self.bars)
        self.assertEqual(again.report_hash, self.report.report_hash)
        embargoed = run_walk_forward_v1(self.study, _plan(embargo=30 * timedelta(minutes=1)), RULE, bars=self.bars)
        self.assertNotEqual(embargoed.identity["split_hash"], self.report.identity["split_hash"])
        lower = WalkForwardSelectionRuleV1("max_drawdown", RankDirectionV1.LOWER_IS_BETTER)
        self.assertNotEqual(run_walk_forward_v1(self.study, _plan(), lower, bars=self.bars).report_hash,
                            self.report.report_hash)

    def test_nothing_after_the_plan_end_is_ever_read(self) -> None:
        plan = _plan(evaluation_end_exclusive=DAY0 + 36 * H)
        base = run_walk_forward_v1(self.study, plan, RULE, bars=self.bars)
        # From the bar opening at 35:59: it closes at 36:00, so close + lag is already past the end.
        cut = int(np.searchsorted(self.bars.open_us, (DAY0 + 36 * H - timedelta(minutes=1) - datetime(1970, 1, 1, tzinfo=UTC))
                                  // timedelta(microseconds=1)))
        perturbed = _with_opens(self.bars, cut, self.bars.size, 1.5)
        self.assertEqual(run_walk_forward_v1(self.study, plan, RULE, bars=perturbed).report_hash, base.report_hash)

    def test_selection_reads_the_train_side_only(self) -> None:
        fold0 = self.report.folds[0]
        start = int(np.searchsorted(self.bars.open_us, (DAY0 + 12 * H - datetime(1970, 1, 1, tzinfo=UTC))
                                    // timedelta(microseconds=1)))
        stop = int(np.searchsorted(self.bars.open_us, (DAY0 + 18 * H - datetime(1970, 1, 1, tzinfo=UTC))
                                   // timedelta(microseconds=1)))
        changed = run_walk_forward_v1(self.study, _plan(), RULE, bars=_with_opens(self.bars, start, stop, 1.2)).folds[0]
        self.assertEqual(changed["selected"]["trial_id"], fold0["selected"]["trial_id"])
        self.assertEqual(changed["selected"]["train_metric_value"], fold0["selected"]["train_metric_value"])

    def test_observations_follow_the_execution_convention(self) -> None:
        observations = interval_observations_v1(self.bars, self.lag_us)
        self.assertEqual(len(observations), self.bars.size - 1)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        for j in (0, 1, 2, 100, self.bars.size - 2):
            o = observations[j]
            self.assertLessEqual(o.decision_at, epoch + timedelta(microseconds=int(self.bars.open_us[j])))
            self.assertEqual(o.label_end_at, epoch + timedelta(microseconds=int(self.bars.close_us[j + 1]) + self.lag_us))
        decisions = [o.decision_at for o in observations]
        self.assertEqual(decisions, sorted(decisions))

    def test_a_fold_with_no_defined_train_metric_is_recorded_as_excluded(self) -> None:
        # Train [00:00, 00:30): every slow window (>= 60 bars) is still warming up -> no turnover.
        plan = _plan(train_duration=30 * timedelta(minutes=1), test_duration=H, step=H, embargo=timedelta(0),
                     first_test_start=DAY0 + 30 * timedelta(minutes=1), evaluation_end_exclusive=DAY0 + 4 * H)
        rule = WalkForwardSelectionRuleV1("break_even_bps_per_side", RankDirectionV1.HIGHER_IS_BETTER)
        fold0 = run_walk_forward_v1(self.study, plan, rule, bars=self.bars).folds[0]
        self.assertEqual(fold0["status"], FOLD_EXCLUDED_NO_ELIGIBLE)
        self.assertIsNone(fold0["selected"])
        self.assertEqual(fold0["ineligible_trials"], 8)

    def test_missing_or_out_of_bound_evidence_fails_closed(self) -> None:
        with self.assertRaisesRegex(StrategyLabWalkForwardError, "bound_dataset_knowledge"):
            run_walk_forward_v1(self.study, _plan(evaluation_end_exclusive=DAY0 + 72 * H), RULE, bars=self.bars)
        # A test fold lying beyond the last bar has no member: refused, not skipped.
        with self.assertRaisesRegex(StrategyLabSplitError, "without_test_members"):
            run_walk_forward_v1(self.study, _plan(), RULE, bars=_truncate(self.bars, DAY0 + 30 * H))
        with self.assertRaises(StrategyLabWalkForwardError):
            WalkForwardSelectionRuleV1("sharpe_daily_annualized", RankDirectionV1.HIGHER_IS_BETTER)
        with self.assertRaises(StrategyLabWalkForwardError):
            run_walk_forward_v1(self.study, _plan(), "total_return", bars=self.bars)  # type: ignore[arg-type]

    def test_interval_metrics_measure_only_the_members(self) -> None:
        held = [0] * self.bars.size
        held[10:20] = [1] * 10
        metrics = interval_metrics_f64(self.bars, held, list(range(12, 18)))
        self.assertEqual(metrics["intervals"], 6)
        self.assertEqual(metrics["trades"], 0)  # the entry at 10 is outside the member set
        self.assertIsNone(metrics["break_even_bps_per_side"])
        expected = float(np.prod(self.bars.open_f[13:19] / self.bars.open_f[12:18]) - 1.0)
        self.assertAlmostEqual(float(metrics["total_return"]), expected, places=12)


def _truncate(bars: BarsV1, before: datetime) -> BarsV1:
    from trade_platform.strategy_sdk_v1 import restrict_to_bound_v1

    return restrict_to_bound_v1(bars, before, timedelta(0))


if __name__ == "__main__":
    unittest.main()
