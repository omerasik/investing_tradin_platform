"""Pure, offline tests for the Module 3J.1a deterministic derivatives formulas.

No PostgreSQL required. See ``tests/test_derivatives_features_postgres.py``
for the end-to-end Feature Authority materialization evidence.
"""

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trade_platform.derivatives_features import (
    CALCULATION_VERSION,
    DerivativesFeatureError,
    annualized_calendar_spread_rate,
    curve_curvature,
    derivatives_feature_definitions,
    normalized_calendar_spread,
    year_fraction,
)
from trade_platform.feature_authority import FeatureFamily
from trade_platform.futures_term_structure import DayCountConvention


class YearFractionTests(unittest.TestCase):
    def test_act_365f(self) -> None:
        from datetime import date

        yf = year_fraction(date(2025, 1, 1), date(2025, 7, 1), DayCountConvention.ACT_365F)
        self.assertEqual(yf, Decimal(181) / Decimal(365))

    def test_act_360(self) -> None:
        from datetime import date

        yf = year_fraction(date(2025, 1, 1), date(2025, 7, 1), DayCountConvention.ACT_360)
        self.assertEqual(yf, Decimal(181) / Decimal(360))

    def test_zero_year_fraction_rejected(self) -> None:
        from datetime import date

        with self.assertRaisesRegex(DerivativesFeatureError, "non_positive_year_fraction"):
            year_fraction(date(2025, 1, 1), date(2025, 1, 1), DayCountConvention.ACT_365F)

    def test_negative_year_fraction_rejected(self) -> None:
        from datetime import date

        with self.assertRaisesRegex(DerivativesFeatureError, "non_positive_year_fraction"):
            year_fraction(date(2025, 7, 1), date(2025, 1, 1), DayCountConvention.ACT_365F)


class NormalizedCalendarSpreadTests(unittest.TestCase):
    def test_contango_spread(self) -> None:
        value = normalized_calendar_spread(Decimal("100.00"), Decimal("105.00"))
        self.assertEqual(value, Decimal("0.05"))

    def test_backwardation_spread(self) -> None:
        value = normalized_calendar_spread(Decimal("105.00"), Decimal("100.00"))
        self.assertEqual(value, (Decimal("100.00") - Decimal("105.00")) / Decimal("105.00"))

    def test_zero_front_price_rejected(self) -> None:
        with self.assertRaisesRegex(DerivativesFeatureError, "non_positive_settlement_price"):
            normalized_calendar_spread(Decimal("0"), Decimal("105.00"))

    def test_negative_back_price_rejected(self) -> None:
        with self.assertRaisesRegex(DerivativesFeatureError, "non_positive_settlement_price"):
            normalized_calendar_spread(Decimal("100.00"), Decimal("-1"))


class AnnualizedCalendarSpreadRateTests(unittest.TestCase):
    def test_matches_spread_over_year_fraction(self) -> None:
        from datetime import date

        front_expiration = date(2025, 6, 1)
        back_expiration = date(2025, 12, 1)
        rate = annualized_calendar_spread_rate(
            front_price=Decimal("100.00"), back_price=Decimal("105.00"),
            front_expiration=front_expiration, back_expiration=back_expiration,
            convention=DayCountConvention.ACT_365F,
        )
        expected_spread = Decimal("0.05")
        expected_yf = Decimal((back_expiration - front_expiration).days) / Decimal(365)
        self.assertEqual(rate, expected_spread / expected_yf)

    def test_non_positive_price_still_rejected(self) -> None:
        from datetime import date

        with self.assertRaisesRegex(DerivativesFeatureError, "non_positive_settlement_price"):
            annualized_calendar_spread_rate(
                front_price=Decimal("0"), back_price=Decimal("105.00"),
                front_expiration=date(2025, 6, 1), back_expiration=date(2025, 12, 1),
                convention=DayCountConvention.ACT_365F,
            )

    def test_non_positive_year_fraction_rejected(self) -> None:
        from datetime import date

        with self.assertRaisesRegex(DerivativesFeatureError, "non_positive_year_fraction"):
            annualized_calendar_spread_rate(
                front_price=Decimal("100.00"), back_price=Decimal("105.00"),
                front_expiration=date(2025, 12, 1), back_expiration=date(2025, 6, 1),
                convention=DayCountConvention.ACT_365F,
            )


class CurveCurvatureTests(unittest.TestCase):
    def test_known_unequal_spacing_curvature(self) -> None:
        from datetime import date

        front_expiration = date(2025, 1, 1)
        mid_expiration = date(2025, 5, 1)
        back_expiration = date(2026, 1, 1)
        front_price = Decimal("100")
        mid_price = Decimal("103")
        back_price = Decimal("110")
        value = curve_curvature(
            front_price=front_price, mid_price=mid_price, back_price=back_price,
            front_expiration=front_expiration, mid_expiration=mid_expiration,
            back_expiration=back_expiration, convention=DayCountConvention.ACT_365F,
        )
        h1 = year_fraction(front_expiration, mid_expiration, DayCountConvention.ACT_365F)
        h2 = year_fraction(mid_expiration, back_expiration, DayCountConvention.ACT_365F)
        expected_second_derivative = (Decimal(2) / (h1 + h2)) * (
            (back_price - mid_price) / h2 - (mid_price - front_price) / h1
        )
        self.assertEqual(value, expected_second_derivative / front_price)

    def test_deterministic_repeat_call(self) -> None:
        from datetime import date

        # No interpolation/extrapolation shortcut for near-equal spacing --
        # the estimator always uses the real, independently computed gaps,
        # and is purely deterministic given identical inputs.
        front_expiration = date(2025, 1, 1)
        mid_expiration = date(2025, 7, 1)
        back_expiration = date(2026, 1, 1)
        kwargs = {
            "front_price": Decimal("100"), "mid_price": Decimal("105"), "back_price": Decimal("108"),
            "front_expiration": front_expiration, "mid_expiration": mid_expiration,
            "back_expiration": back_expiration, "convention": DayCountConvention.ACT_365F,
        }
        first = curve_curvature(**kwargs)
        second = curve_curvature(**kwargs)
        self.assertIsInstance(first, Decimal)
        self.assertEqual(first, second)

    def test_non_positive_price_rejected(self) -> None:
        from datetime import date

        with self.assertRaisesRegex(DerivativesFeatureError, "non_positive_settlement_price"):
            curve_curvature(
                front_price=Decimal("100"), mid_price=Decimal("0"), back_price=Decimal("110"),
                front_expiration=date(2025, 1, 1), mid_expiration=date(2025, 5, 1),
                back_expiration=date(2026, 1, 1), convention=DayCountConvention.ACT_365F,
            )

    def test_non_positive_gap_rejected(self) -> None:
        from datetime import date

        with self.assertRaisesRegex(DerivativesFeatureError, "non_positive_year_fraction"):
            curve_curvature(
                front_price=Decimal("100"), mid_price=Decimal("103"), back_price=Decimal("110"),
                front_expiration=date(2025, 1, 1), mid_expiration=date(2025, 1, 1),
                back_expiration=date(2026, 1, 1), convention=DayCountConvention.ACT_365F,
            )


class DerivativesFeatureDefinitionsTests(unittest.TestCase):
    def test_three_definitions_are_derivatives_family_v1(self) -> None:
        created_at = datetime(2026, 9, 9, tzinfo=UTC)
        spread, rate, curvature = derivatives_feature_definitions(created_at)
        for definition in (spread, rate, curvature):
            definition.validate()
            self.assertEqual(definition.family, FeatureFamily.DERIVATIVES)
            self.assertEqual(definition.semantic_version, "1.0.0")
            self.assertEqual(definition.calculation_version, CALCULATION_VERSION)
            self.assertEqual(definition.required_dataset_types, ("FUTURES_TERM_STRUCTURE",))
            self.assertIsNone(definition.expected_minimum)
            self.assertIsNone(definition.expected_maximum)
            self.assertEqual(definition.leakage_policy, "reject_future_knowledge")
            self.assertEqual(definition.missing_value_policy, "fail_closed_no_materialization")
        self.assertEqual(spread.name, "futures_front_back_normalized_spread")
        self.assertEqual(spread.units, "dimensionless")
        self.assertEqual(rate.name, "futures_annualized_calendar_spread_rate")
        self.assertEqual(rate.units, "1/year")
        self.assertIn("carry_day_count_convention", rate.required_fields)
        self.assertEqual(curvature.name, "futures_curve_curvature")
        self.assertEqual(curvature.units, "year^-2")
        self.assertIn("carry_day_count_convention", curvature.required_fields)
        # Three distinct names/definitions, not a duplicated formula wearing
        # two labels -- proposal section 8 decision 4.
        self.assertEqual(
            {spread.name, rate.name, curvature.name},
            {
                "futures_front_back_normalized_spread",
                "futures_annualized_calendar_spread_rate",
                "futures_curve_curvature",
            },
        )


if __name__ == "__main__":
    unittest.main()
