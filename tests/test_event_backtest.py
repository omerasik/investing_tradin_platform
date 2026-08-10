from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from trade_platform.domain import OrderSide
from trade_platform.corporate_actions import CorporateAction, CorporateActionType
from trade_platform.event_backtest import (
    EventBacktestValidationError,
    EventDrivenBacktester,
    ExecutionAssumptions,
    MarketEvent,
    SimulatedOrderStatus,
    SimulatedOrderType,
    create_order,
)


UTC = timezone.utc
T0 = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)


def event(seconds: int, *, bid: str = "100", ask: str = "101", volume: str = "100", open: bool = True) -> MarketEvent:
    return MarketEvent("US:NYSE:SPY", T0 + timedelta(seconds=seconds), Decimal(bid), Decimal(ask), Decimal(volume), open)


class EventDrivenBacktesterTests(unittest.TestCase):
    def test_market_order_respects_latency_partial_fills_spread_impact_and_fees(self) -> None:
        engine = EventDrivenBacktester(ExecutionAssumptions(latency=timedelta(seconds=1), maximum_participation=Decimal("0.5"), commission_bps=Decimal("10"), fixed_fee=Decimal("1"), impact_coefficient=Decimal("0.1")))
        order = create_order("US:NYSE:SPY", OrderSide.BUY, SimulatedOrderType.MARKET, Decimal("75"), T0)
        engine.submit(order)
        self.assertEqual(engine.process(event(0)), ())
        first = engine.process(event(1))
        self.assertEqual(first[0].quantity, Decimal("50"))
        self.assertEqual(first[0].price, Decimal("108.1417784899841299964485280"))
        self.assertEqual(first[0].fee, Decimal("6.407088924499206499822426400"))
        second = engine.process(event(2))
        self.assertEqual(second[0].quantity, Decimal("25"))
        result = engine.result()
        self.assertEqual(result.orders[0].status, SimulatedOrderStatus.FILLED)
        self.assertEqual(len(result.fills), 2)
        self.assertLess(result.net_cash_change, Decimal("0"))

    def test_limit_stop_and_stop_limit_follow_quote_semantics(self) -> None:
        engine = EventDrivenBacktester(ExecutionAssumptions(maximum_participation=Decimal("1")))
        limit = create_order("US:NYSE:SPY", OrderSide.BUY, SimulatedOrderType.LIMIT, Decimal("1"), T0, limit_price=Decimal("100"))
        stop = create_order("US:NYSE:SPY", OrderSide.BUY, SimulatedOrderType.STOP, Decimal("1"), T0, stop_price=Decimal("105"))
        stop_limit = create_order("US:NYSE:SPY", OrderSide.SELL, SimulatedOrderType.STOP_LIMIT, Decimal("1"), T0, stop_price=Decimal("98"), limit_price=Decimal("99"))
        for order in (limit, stop, stop_limit):
            engine.submit(order)
        self.assertEqual(engine.process(event(1)), ())
        self.assertEqual(len(engine.process(event(2, bid="99", ask="100"))), 1)  # buy limit at ask 100
        self.assertEqual(len(engine.process(event(3, bid="104", ask="105"))), 1)  # buy stop at ask 105
        self.assertEqual(len(engine.process(event(4, bid="98", ask="99"))), 0)  # stop-limit triggers but bid below limit
        self.assertEqual(len(engine.process(event(5, bid="99", ask="100"))), 1)
        self.assertTrue(all(order.status is SimulatedOrderStatus.FILLED for order in engine.result().orders))

    def test_expiry_closed_sessions_cancellation_and_time_regression_are_explicit(self) -> None:
        engine = EventDrivenBacktester(ExecutionAssumptions())
        order = create_order("US:NYSE:SPY", OrderSide.BUY, SimulatedOrderType.MARKET, Decimal("1"), T0, expires_at=T0 + timedelta(seconds=2))
        engine.submit(order)
        self.assertEqual(engine.process(event(1, open=False)), ())
        self.assertEqual(engine.process(event(2)), ())
        self.assertEqual(engine.result().orders[0].status, SimulatedOrderStatus.EXPIRED)
        cancelled = create_order("US:NYSE:SPY", OrderSide.BUY, SimulatedOrderType.MARKET, Decimal("1"), T0 + timedelta(seconds=3))
        engine.submit(cancelled)
        self.assertEqual(engine.cancel(cancelled.order_id).status, SimulatedOrderStatus.CANCELLED)
        with self.assertRaises(EventBacktestValidationError):
            engine.process(event(1))

    def test_invalid_order_contract_is_rejected(self) -> None:
        with self.assertRaises(EventBacktestValidationError):
            create_order("US:NYSE:SPY", OrderSide.BUY, SimulatedOrderType.LIMIT, Decimal("1"), T0)
        with self.assertRaises(EventBacktestValidationError):
            EventDrivenBacktester(ExecutionAssumptions(maximum_participation=Decimal("0")))

    def test_portfolio_accounting_and_corporate_actions_are_applied_once(self) -> None:
        engine = EventDrivenBacktester(ExecutionAssumptions(maximum_participation=Decimal("1")), initial_cash=Decimal("1000"))
        order = create_order("US:NYSE:SPY", OrderSide.BUY, SimulatedOrderType.MARKET, Decimal("10"), T0)
        engine.submit(order)
        engine.process(event(1, bid="99", ask="100"))
        action_time = T0 + timedelta(days=1)
        split = CorporateAction("US:NYSE:SPY", CorporateActionType.SPLIT, T0, action_time, T0, "fixture", "split", ratio=Decimal("2"))
        dividend = CorporateAction("US:NYSE:SPY", CorporateActionType.CASH_DIVIDEND, T0, action_time + timedelta(seconds=1), T0, "fixture", "dividend", amount_per_share=Decimal("1"), currency="USD")
        engine.apply_corporate_actions([split, dividend], as_of=dividend.effective_at)
        engine.apply_corporate_actions([split, dividend], as_of=dividend.effective_at)
        result = engine.result()
        self.assertEqual((result.positions[0].quantity, result.positions[0].average_cost, result.cash.amount), (Decimal("20"), Decimal("50"), Decimal("20")))
        with self.assertRaisesRegex(EventBacktestValidationError, "future_corporate_action"):
            engine.apply_corporate_actions([CorporateAction("US:NYSE:SPY", CorporateActionType.SPLIT, T0, action_time + timedelta(days=1), T0, "fixture", "future", ratio=Decimal("2"))], as_of=action_time)
