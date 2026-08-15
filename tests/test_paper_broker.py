import unittest
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.domain import OrderIntent, OrderSide, OrderStatus, Signal, SignalStatus, utc_now
from trade_platform.paper_broker import (
    FixturePaperBroker,
    PaperBrokerValidationError,
    execution_quality,
)
from trade_platform.paper_execution import PaperOrder


class PaperBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        now = utc_now()
        signal = Signal(uuid4(), "US:NYSE:SPY", "baseline-v1", SignalStatus.VALIDATED, now, now + timedelta(hours=1), Decimal("1"), OrderSide.BUY, "fixture")
        intent = OrderIntent(uuid4(), signal.signal_id, "US:NYSE:SPY", "paper-account", OrderSide.BUY, Decimal("10"), Decimal("100"))
        self.order = PaperOrder(intent, OrderStatus.APPROVED)

    def test_fixture_submission_requires_risk_approval(self) -> None:
        broker = FixturePaperBroker()
        with self.assertRaisesRegex(PaperBrokerValidationError, "risk_approved"):
            broker.submit_paper_order(PaperOrder(self.order.intent))
        self.assertEqual(broker.submit_paper_order(self.order).status, OrderStatus.ACKNOWLEDGED)

    def test_fixture_fill_and_execution_quality_are_explicit(self) -> None:
        broker = FixturePaperBroker(); broker.submit_paper_order(self.order)
        filled = broker.simulate_fill(self.order.intent.intent_id, Decimal("4"), Decimal("101"))
        report = execution_quality(filled, Decimal("100"))
        self.assertEqual(report.fill_rate, Decimal("0.4"))
        self.assertEqual(report.absolute_slippage, Decimal("0.01"))

    def test_cancellation_and_duplicate_protection(self) -> None:
        broker = FixturePaperBroker(); broker.submit_paper_order(self.order)
        self.assertEqual(broker.cancel_paper_order(self.order.intent.intent_id).status, OrderStatus.CANCELLED)
        with self.assertRaisesRegex(PaperBrokerValidationError, "duplicate_paper_broker_intent"):
            broker.submit_paper_order(self.order)

    def test_unknown_order_and_bad_benchmark_are_rejected(self) -> None:
        with self.assertRaisesRegex(PaperBrokerValidationError, "unknown_paper_broker_intent"):
            FixturePaperBroker().get_paper_order(uuid4())
        with self.assertRaisesRegex(PaperBrokerValidationError, "invalid_execution_benchmark"):
            execution_quality(self.order, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
