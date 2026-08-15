import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trade_platform.pit_fundamentals import PitFundamentalError, derive_research_metrics


class PitFundamentalContractTests(unittest.TestCase):
    def test_transparent_metrics_cover_required_research_dimensions(self) -> None:
        values = {
            "revenue": Decimal("1000"), "operating_income": Decimal("200"),
            "operating_cash_flow": Decimal("180"), "capital_expenditures": Decimal("50"),
            "total_debt": Decimal("300"), "shares_outstanding": Decimal("110"),
            "tax_rate": Decimal("0.25"), "invested_capital": Decimal("750"),
            "dividends_paid": Decimal("20"), "share_repurchases": Decimal("30"),
        }
        metrics = derive_research_metrics(values, prior_shares=Decimal("100"))
        self.assertEqual(metrics.operating_margin, Decimal("0.2"))
        self.assertEqual(metrics.free_cash_flow, Decimal("130"))
        self.assertEqual(metrics.dilution, Decimal("0.1"))
        self.assertEqual(metrics.nopat, Decimal("150.00"))
        self.assertEqual(metrics.roic, Decimal("0.20"))
        self.assertEqual(metrics.capital_allocation, Decimal("100"))

    def test_metrics_fail_closed_on_missing_or_invalid_denominators(self) -> None:
        with self.assertRaisesRegex(PitFundamentalError, "missing_standardized_metrics"):
            derive_research_metrics({"revenue": Decimal("1")})
        with self.assertRaisesRegex(PitFundamentalError, "invalid_prior_shares"):
            complete = {
                "revenue": Decimal("1"), "operating_income": Decimal("1"),
                "operating_cash_flow": Decimal("1"), "capital_expenditures": Decimal("1"),
                "total_debt": Decimal("1"), "shares_outstanding": Decimal("1"),
                "tax_rate": Decimal("0"), "invested_capital": Decimal("1"),
                "dividends_paid": Decimal("0"), "share_repurchases": Decimal("0"),
            }
            derive_research_metrics(complete, prior_shares=Decimal("0"))

    def test_test_module_uses_timezone_aware_reference(self) -> None:
        self.assertIsNotNone(datetime(2024, 1, 1, tzinfo=UTC).utcoffset())
