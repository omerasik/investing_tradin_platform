import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trade_platform.feature_authority import (
    FeatureAuthorityError,
    FeatureFamily,
    TransparentFeatureCalculator,
    TransparentMarketWindow,
    transparent_feature_definitions,
)


class TransparentFeatureAuthorityTests(unittest.TestCase):
    def test_market_baselines_are_transparent_and_require_inputs(self) -> None:
        window = TransparentMarketWindow(
            (Decimal("100"), Decimal("110"), Decimal("121")),
            (Decimal("101"), Decimal("111"), Decimal("122")),
            (Decimal("99"), Decimal("109"), Decimal("120")),
            (Decimal("10"), Decimal("20"), Decimal("30")), Decimal("100"),
        )
        self.assertEqual(TransparentFeatureCalculator.calculate("simple_return", window), Decimal("0.21"))
        self.assertEqual(TransparentFeatureCalculator.calculate("sma", window), Decimal("110.3333333333333333333333333"))
        self.assertEqual(TransparentFeatureCalculator.calculate("turnover", window), Decimal("0.3"))
        self.assertEqual(TransparentFeatureCalculator.calculate("breakout", window), Decimal("0"))
        with self.assertRaisesRegex(FeatureAuthorityError, "unsupported"):
            TransparentFeatureCalculator.calculate("revenue_growth", window)

    def test_full_catalogue_declares_but_does_not_fabricate_unavailable_inputs(self) -> None:
        definitions = transparent_feature_definitions(datetime(2025, 1, 1, tzinfo=UTC))
        names = {definition.name for definition in definitions}
        self.assertTrue({"simple_return", "ema", "amihud_proxy", "revenue_growth", "rate_level_change"} <= names)
        self.assertEqual(next(item for item in definitions if item.name == "revenue_growth").family, FeatureFamily.FUNDAMENTAL)
        self.assertEqual(next(item for item in definitions if item.name == "rate_level_change").required_dataset_types, ("MACRO",))
