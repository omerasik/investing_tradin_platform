import unittest
from decimal import Decimal

from trade_platform.regimes import (Regime, RegimeValidationError, StrategyRegimeProfile, allocate_ensemble, estimate_regime, walk_forward_estimates)


class RegimeTests(unittest.TestCase):
    def test_bullish_estimate_is_probabilistic_and_explicitly_uncertain(self) -> None:
        estimate = estimate_regime([Decimal("100"), Decimal("110")], 2)
        self.assertEqual(estimate.probabilities[Regime.BULLISH], Decimal("0.80"))
        self.assertEqual(estimate.uncertainty, Decimal("0.20"))

    def test_walk_forward_estimates_do_not_change_from_future_append(self) -> None:
        closes = [Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103")]
        before = walk_forward_estimates(closes, 3)
        after = walk_forward_estimates(closes + [Decimal("10")], 3)
        self.assertEqual(before, after[:len(before)])

    def test_ensemble_is_bounded_and_uses_regime_weighted_scores(self) -> None:
        regime = estimate_regime([Decimal("100"), Decimal("105")], 2)
        trend = StrategyRegimeProfile("trend", {Regime.BULLISH: Decimal("0.10"), Regime.BEARISH: Decimal("-0.02"), Regime.SIDEWAYS: Decimal("0.01")})
        defensive = StrategyRegimeProfile("defensive", {Regime.BULLISH: Decimal("0.03"), Regime.BEARISH: Decimal("0.04"), Regime.SIDEWAYS: Decimal("0.02")})
        allocation = allocate_ensemble(regime, [trend, defensive], Decimal("0.8"))
        self.assertEqual(sum(allocation.weights.values(), Decimal("0")), Decimal("1"))
        self.assertGreater(allocation.expected_scores["trend"], allocation.expected_scores["defensive"])

    def test_concentrated_or_invalid_inputs_are_rejected(self) -> None:
        regime = estimate_regime([Decimal("100"), Decimal("110")], 2)
        profile = StrategyRegimeProfile("trend", {Regime.BULLISH: Decimal("0.1"), Regime.BEARISH: Decimal("0.1"), Regime.SIDEWAYS: Decimal("0.1")})
        with self.assertRaisesRegex(RegimeValidationError, "ensemble_weight_exceeds_maximum"):
            allocate_ensemble(regime, [profile])
        with self.assertRaisesRegex(RegimeValidationError, "invalid_regime_history"):
            estimate_regime([Decimal("0")], 2)


if __name__ == "__main__":
    unittest.main()
