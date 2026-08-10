import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trade_platform.cross_engine import ExecutionRecord, compare_execution_records, reconcile_engines
from trade_platform.paper_execution import PaperOrder, Position, progress_to_acknowledged
from trade_platform.research import CostModel, run_vectorized_backtest
from tests.test_paper_execution import intent


class CrossEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vector = run_vectorized_backtest([Decimal("100"), Decimal("110")], [Decimal("1"), Decimal("0")], CostModel())
        self.order = progress_to_acknowledged(PaperOrder(intent())).fill(Decimal("10"), Decimal("100"))

    def test_matching_outputs_reconcile(self) -> None:
        report = reconcile_engines(self.vector, Decimal("1.1"), {"US:NYSE:SPY": Decimal("10")}, {"US:NYSE:SPY": Position("US:NYSE:SPY", Decimal("10"), Decimal("100"))}, Decimal("10"), [self.order])
        self.assertTrue(report.reconciled)

    def test_material_difference_blocks_reconciliation(self) -> None:
        report = reconcile_engines(self.vector, Decimal("1.0"), {}, {}, Decimal("0"), [self.order])
        self.assertFalse(report.reconciled)
        self.assertIn("final_equity_mismatch", report.differences)
        self.assertIn("fill_quantity_mismatch", report.differences)

    def test_fill_evidence_compares_identity_time_quantity_price_and_fee(self) -> None:
        timestamp = datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc)
        expected = ExecutionRecord("order-1", "US:NYSE:SPY", timestamp, "BUY", Decimal("2"), Decimal("100"), Decimal("0.2"))
        actual = ExecutionRecord("order-1", "US:NYSE:SPY", timestamp + timedelta(seconds=1), "BUY", Decimal("1"), Decimal("101"), Decimal("0.3"))
        differences = compare_execution_records((expected,), (actual,))
        self.assertEqual(differences, ("fill:0:timestamp_mismatch", "fill:0:quantity_mismatch", "fill:0:price_mismatch", "fill:0:fee_mismatch"))
        report = reconcile_engines(self.vector, Decimal("1.1"), {"US:NYSE:SPY": Decimal("10")}, {"US:NYSE:SPY": Position("US:NYSE:SPY", Decimal("10"), Decimal("100"))}, Decimal("10"), [self.order], expected_executions=(expected,), event_executions=(actual,))
        self.assertFalse(report.reconciled)
        self.assertIn("fill:0:fee_mismatch", report.differences)


if __name__ == "__main__":
    unittest.main()
