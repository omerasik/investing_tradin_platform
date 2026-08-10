import unittest
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.domain import OrderIntent, OrderSide, OrderStatus, Signal, SignalStatus, utc_now
from trade_platform.paper_execution import PaperOrder, progress_to_acknowledged
from trade_platform.shadow_mode import (ShadowModeValidationError, compare_paper_orders, record_failure_drill, requires_incident)


def make_order(intent_id=None) -> PaperOrder:
    now = utc_now(); signal = Signal(uuid4(), "US:NYSE:SPY", "v1", SignalStatus.VALIDATED, now, now + timedelta(hours=1), Decimal("1"), OrderSide.BUY, "fixture")
    intent = OrderIntent(intent_id or uuid4(), signal.signal_id, "US:NYSE:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
    return progress_to_acknowledged(PaperOrder(intent))


class ShadowModeTests(unittest.TestCase):
    def test_matching_paper_orders_produce_no_incident(self) -> None:
        primary = make_order(); shadow = make_order()
        shadow = PaperOrder(primary.intent, shadow.status)
        comparison = compare_paper_orders(primary, shadow)
        self.assertTrue(comparison.matched); self.assertFalse(requires_incident(comparison))

    def test_divergent_fill_is_explicit(self) -> None:
        primary = make_order().fill(Decimal("5"), Decimal("100")); shadow = make_order().fill(Decimal("4"), Decimal("102"))
        comparison = compare_paper_orders(primary, shadow)
        self.assertIn("filled_quantity_mismatch", comparison.differences)
        self.assertIn("fill_price_mismatch", comparison.differences)
        self.assertTrue(requires_incident(comparison))

    def test_failure_drill_records_observed_protection(self) -> None:
        self.assertTrue(record_failure_drill("stale_market", "risk_rejection", "risk_rejection").passed)
        self.assertFalse(record_failure_drill("stale_market", "risk_rejection", "approved").passed)
        with self.assertRaisesRegex(ShadowModeValidationError, "invalid_failure_drill"):
            record_failure_drill("", "risk_rejection", "risk_rejection")


if __name__ == "__main__":
    unittest.main()
