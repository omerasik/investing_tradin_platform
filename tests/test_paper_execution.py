import unittest
from decimal import Decimal
from uuid import uuid4

from trade_platform.domain import OrderIntent, OrderSide, OrderStatus
from trade_platform.paper_execution import (
    CashBalance,
    DividendAction,
    MarginPolicy,
    OrderTransitionError,
    PaperOrder,
    PaperVenueConstraints,
    Position,
    SplitAction,
    apply_borrow_cost,
    apply_dividend,
    apply_funding,
    apply_split,
    portfolio_state_from_reconciliation,
    progress_to_acknowledged,
    reconcile,
)


def intent() -> OrderIntent:
    return OrderIntent(uuid4(), uuid4(), "US:NYSE:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))


class PaperExecutionTests(unittest.TestCase):
    def test_lifecycle_reaches_acknowledged_only_through_valid_transitions(self) -> None:
        self.assertEqual(progress_to_acknowledged(PaperOrder(intent())).status, OrderStatus.ACKNOWLEDGED)

    def test_partial_and_final_fill_preserve_weighted_average(self) -> None:
        order = progress_to_acknowledged(PaperOrder(intent())).fill(Decimal("4"), Decimal("100"))
        filled = order.fill(Decimal("6"), Decimal("110"))
        self.assertEqual(filled.status, OrderStatus.FILLED)
        self.assertEqual(filled.average_fill_price, Decimal("106"))

    def test_invalid_transition_or_overfill_is_rejected(self) -> None:
        with self.assertRaises(OrderTransitionError):
            PaperOrder(intent()).transition(OrderStatus.FILLED)
        with self.assertRaises(OrderTransitionError):
            progress_to_acknowledged(PaperOrder(intent())).fill(Decimal("11"), Decimal("100"))

    def test_split_preserves_position_notional_and_dividend_updates_cash(self) -> None:
        position = Position("US:NYSE:SPY", Decimal("10"), Decimal("100"))
        split = apply_split(position, SplitAction(position.instrument_id, Decimal("2")))
        self.assertEqual((split.quantity, split.average_cost), (Decimal("20"), Decimal("50")))
        cash = apply_dividend(split, CashBalance("USD", Decimal("10")), DividendAction(position.instrument_id, Decimal("1"), "USD"))
        self.assertEqual(cash.amount, Decimal("30"))

    def test_reconciliation_mismatch_is_explicit(self) -> None:
        position = Position("US:NYSE:SPY", Decimal("10"), Decimal("100"))
        result = reconcile({position.instrument_id: position}, {}, CashBalance("USD", Decimal("100")), CashBalance("USD", Decimal("100")))
        self.assertFalse(result.complete)
        self.assertIn("position_mismatch:US:NYSE:SPY", result.discrepancies)
        state = portfolio_state_from_reconciliation("paper", {position.instrument_id: position}, result)
        self.assertFalse(state.reconciliation_complete)
        self.assertFalse(state.risk_increase_allowed)

    def test_funding_borrow_and_margin_constraints_are_deterministic(self) -> None:
        cash = CashBalance("USD", Decimal("1000"))
        funded = apply_funding(Position("CRYPTO:BTC", Decimal("2"), Decimal("100")), cash, Decimal("100"), Decimal("0.01"))
        self.assertEqual(funded.amount, Decimal("998"))
        borrowed = apply_borrow_cost(Position("US:NYSE:SPY", Decimal("-2"), Decimal("100")), cash, Decimal("100"), Decimal("0.01"))
        self.assertEqual(borrowed.amount, Decimal("998"))
        policy = MarginPolicy(Decimal("1000"), Decimal("2"))
        self.assertTrue(policy.permits(Decimal("1000"), Decimal("500")))
        self.assertFalse(policy.permits(Decimal("1001"), Decimal("500")))

    def test_venue_constraints_reject_off_tick_lot_and_closed_session(self) -> None:
        constraints = PaperVenueConstraints(Decimal("0.05"), Decimal("2"), Decimal("1000"))
        self.assertEqual(constraints.validate(Decimal("4"), Decimal("100"), True), ())
        reasons = constraints.validate(Decimal("3"), Decimal("100.03"), False)
        self.assertIn("invalid_trading_session", reasons)
        self.assertIn("lot_size_violation", reasons)


if __name__ == "__main__":
    unittest.main()
