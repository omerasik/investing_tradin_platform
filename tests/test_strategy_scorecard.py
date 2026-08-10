from decimal import Decimal
import unittest

from trade_platform.research import CostModel, MovingAverageCrossStrategy
from trade_platform.research_validation import run_purged_walk_forward
from trade_platform.strategy_scorecard import ScorecardError, build_scorecard, calculate_held_out_metrics
from trade_platform.strategy_validation import purged_walk_forward_splits


class StrategyScorecardTests(unittest.TestCase):
    def setUp(self) -> None:
        closes = [Decimal(str(value)) for value in range(100, 140)]
        splits = purged_walk_forward_splits(len(closes), train_size=10, validation_size=5, test_size=5, step=5, purge=2, embargo=1)
        self.results = tuple(run_purged_walk_forward(closes, MovingAverageCrossStrategy("ma-v1", 2, 4).signals, splits, CostModel()))

    def test_held_out_metrics_include_drawdown_tail_and_turnover(self) -> None:
        metrics = calculate_held_out_metrics(self.results)
        self.assertEqual(metrics.periods, 20)
        self.assertGreater(metrics.total_return, Decimal("0"))
        self.assertLessEqual(metrics.max_drawdown, Decimal("0"))
        self.assertGreaterEqual(metrics.value_at_risk, metrics.conditional_value_at_risk)
        self.assertGreater(metrics.turnover, Decimal("0"))

    def test_scorecard_exposes_penalties_and_missing_evidence(self) -> None:
        scorecard = build_scorecard(calculate_held_out_metrics(self.results), probabilistic_sharpe=Decimal("0.99"), pessimistic_total_return=None, data_quality_score=None, parameter_count=3)
        self.assertGreaterEqual(scorecard.aggregate_score, Decimal("0"))
        self.assertIn("cost_robustness", scorecard.limitations)
        self.assertIn("data_quality", scorecard.limitations)
        with self.assertRaisesRegex(ScorecardError, "invalid"):
            build_scorecard(scorecard.metrics, probabilistic_sharpe=Decimal("1.1"), pessimistic_total_return=None, data_quality_score=None, parameter_count=0)


if __name__ == "__main__":
    unittest.main()
