import unittest
from decimal import Decimal

from trade_platform.trend_strategy_v2 import (
    TrendMarketSeries,
    TrendStrategyDefinitionV2,
    breakout_signals,
    multi_horizon_trend_signals,
    time_series_momentum_signals,
    volatility_scaled_trend_exposure,
)


class TrendStrategyV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        closes = tuple(Decimal(value) for value in ("10", "11", "12", "13", "14", "15"))
        self.series = TrendMarketSeries(closes, closes, closes, (Decimal("100"),) * len(closes))

    def test_family_uses_feature_authority_and_declares_required_contract(self) -> None:
        definition = TrendStrategyDefinitionV2(
            "trend-v2", "time_series_momentum", "Persistent returns can continue.",
            ("multi_horizon_momentum:1.0.0", "realized_volatility:1.0.0"),
            "positive momentum", "non-positive momentum", "volatility scaled", Decimal("0.5"),
            "multi-week", "cost-v1", "Synthetic OHLCV/ADV engineering assumption.",
            (("lookback", "3"),), ("sharp reversal",),
            ("Synthetic fixture only; no alpha conclusion.",),
        )
        definition.validate()
        self.assertEqual(time_series_momentum_signals(self.series, lookback=3)[:2], (Decimal("0"), Decimal("0")))
        self.assertEqual(time_series_momentum_signals(self.series, lookback=3)[-1], Decimal("1"))

    def test_breakout_multi_horizon_and_volatility_scaled_forms_are_bounded(self) -> None:
        self.assertEqual(breakout_signals(self.series, lookback=3)[-1], Decimal("1"))
        self.assertEqual(multi_horizon_trend_signals(self.series, lookbacks=(2, 3))[-1], Decimal("1"))
        exposures = volatility_scaled_trend_exposure(
            self.series, lookback=3, target_volatility=Decimal("0.1"), maximum_exposure=Decimal("0.5")
        )
        self.assertTrue(all(Decimal("0") <= value <= Decimal("0.5") for value in exposures))
