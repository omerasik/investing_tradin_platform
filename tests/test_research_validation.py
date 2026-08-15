import unittest
from decimal import Decimal

from trade_platform.research import CostModel, MovingAverageCrossStrategy
from trade_platform.research_validation import (
    ResearchValidationError,
    benjamini_hochberg,
    bootstrap_total_returns,
    probabilistic_sharpe_exceeds,
    run_purged_walk_forward,
)
from trade_platform.strategy_validation import purged_walk_forward_splits


class ResearchValidationTests(unittest.TestCase):
    def test_purged_walk_forward_executes_only_held_out_segments_and_rejects_future_signals(self) -> None:
        closes = [Decimal(str(value)) for value in range(100, 140)]
        splits = purged_walk_forward_splits(len(closes), train_size=10, validation_size=5, test_size=5, step=5, purge=2, embargo=1)
        strategy = MovingAverageCrossStrategy("ma-v1", 2, 4)
        results = run_purged_walk_forward(closes, strategy.signals, splits, CostModel())
        self.assertEqual(len(results), len(splits))
        # One prior close is included to measure the first held-out return, so
        # the resulting returns still match the exclusive test-index range.
        self.assertTrue(all(len(item.result.period_returns) == item.split.test_end - item.split.test_start for item in results))
        def leaking_factory(values: list[Decimal]) -> list[Decimal]:
            return [Decimal("1") if values[-1] > Decimal("130") else Decimal("0")] * len(values)
        with self.assertRaisesRegex(ResearchValidationError, "future"):
            run_purged_walk_forward(closes, leaking_factory, splits, CostModel())

    def test_bootstrap_multiple_testing_and_sharpe_confidence_are_deterministic(self) -> None:
        returns = (Decimal("0.1"), Decimal("-0.05"), Decimal("0.02"))
        self.assertEqual(bootstrap_total_returns(returns, simulations=5, seed=7), bootstrap_total_returns(returns, simulations=5, seed=7))
        result = benjamini_hochberg({"trend": Decimal("0.01"), "noise": Decimal("0.8"), "value": Decimal("0.03")})
        self.assertEqual((result.discoveries, result.threshold), (("trend", "value"), Decimal("0.03")))
        self.assertGreater(probabilistic_sharpe_exceeds(Decimal("1"), Decimal("0"), observations=20), Decimal("0.99"))
        with self.assertRaisesRegex(ResearchValidationError, "invalid_multiple"):
            benjamini_hochberg({"bad": Decimal("1.1")})
