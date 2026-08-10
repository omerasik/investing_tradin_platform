from decimal import Decimal
import unittest

from trade_platform.broker_adapter import BrokerAdapterError, BrokerConfiguration, BrokerMode, InternalAccountState, SandboxPaperBrokerAdapter, reconcile_broker_account
from trade_platform.domain import OrderStatus
from trade_platform.paper_execution import CashBalance, PaperOrder
from tests.test_paper_execution import intent


class BrokerAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        config = BrokerConfiguration("local-sandbox", BrokerMode.SANDBOX_PAPER, "paper-account", "BROKER_TOKEN_REF")
        self.broker = SandboxPaperBrokerAdapter(config, cash=CashBalance("USD", Decimal("10000")))
        self.order = PaperOrder(intent(), OrderStatus.APPROVED)

    def test_paper_adapter_has_no_network_and_supports_events_replace_and_account_sync(self) -> None:
        acknowledged = self.broker.submit(self.order)
        amended = self.broker.replace(acknowledged.intent.intent_id, quantity=Decimal("12"), limit_price=Decimal("101"))
        filled = self.broker.simulate_fill("fill-1", amended.intent.intent_id, Decimal("4"), Decimal("100"), fee=Decimal("1"))
        snapshot = self.broker.sync_account()
        self.assertFalse(self.broker.capabilities.network_connected)
        self.assertEqual(filled.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(snapshot.positions[filled.intent.instrument_id].quantity, Decimal("4"))
        self.assertEqual(snapshot.cash.amount, Decimal("9599"))
        self.assertEqual(tuple(event.event_type for event in self.broker.order_events()), ("ORDER_ACKNOWLEDGED", "ORDER_REPLACED", "FILL"))

    def test_adapter_normalizes_invalid_requests_and_reconciliation_is_comprehensive(self) -> None:
        with self.assertRaisesRegex(BrokerAdapterError, "INVALID_STATE"):
            self.broker.submit(PaperOrder(self.order.intent))
        acknowledged = self.broker.submit(self.order)
        self.broker.simulate_fill("fill-1", acknowledged.intent.intent_id, Decimal("10"), Decimal("100"), fee=Decimal("1"))
        snapshot = self.broker.sync_account()
        internal = InternalAccountState(CashBalance("USD", Decimal("9000")), snapshot.positions, (), (), Decimal("0"), Decimal("0"))
        result = reconcile_broker_account(internal, snapshot)
        self.assertFalse(result.complete)
        self.assertEqual(set(result.discrepancies), {"cash_mismatch", "fill_mismatch", "fee_mismatch"})
        with self.assertRaisesRegex(BrokerAdapterError, "INVALID_FILL"):
            self.broker.simulate_fill("fill-1", acknowledged.intent.intent_id, Decimal("1"), Decimal("100"))


if __name__ == "__main__":
    unittest.main()
