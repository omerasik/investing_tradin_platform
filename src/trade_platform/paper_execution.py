"""Deterministic internal paper-order lifecycle; deliberately has no broker client or network path."""

from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from .domain import OrderIntent, OrderStatus, PortfolioState


class OrderTransitionError(ValueError):
    pass


_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PROPOSED: {OrderStatus.RISK_PENDING, OrderStatus.CANCELLED},
    OrderStatus.RISK_PENDING: {OrderStatus.APPROVED, OrderStatus.RISK_REJECTED},
    OrderStatus.APPROVED: {OrderStatus.SUBMISSION_PENDING, OrderStatus.CANCELLED},
    OrderStatus.SUBMISSION_PENDING: {OrderStatus.SUBMITTED, OrderStatus.REJECTED, OrderStatus.UNKNOWN},
    OrderStatus.SUBMITTED: {OrderStatus.ACKNOWLEDGED, OrderStatus.REJECTED, OrderStatus.UNKNOWN},
    OrderStatus.ACKNOWLEDGED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.EXPIRED},
    OrderStatus.PARTIALLY_FILLED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING},
    OrderStatus.CANCEL_PENDING: {OrderStatus.CANCELLED, OrderStatus.UNKNOWN},
}


@dataclass(frozen=True, slots=True)
class PaperOrder:
    intent: OrderIntent
    status: OrderStatus = OrderStatus.PROPOSED
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None

    @property
    def remaining_quantity(self) -> Decimal:
        return self.intent.quantity - self.filled_quantity

    def transition(self, next_status: OrderStatus) -> "PaperOrder":
        if next_status not in _TRANSITIONS.get(self.status, set()):
            raise OrderTransitionError(f"{self.status.value}->{next_status.value} is not allowed")
        return replace(self, status=next_status)

    def fill(self, quantity: Decimal, price: Decimal) -> "PaperOrder":
        if self.status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED}:
            raise OrderTransitionError("fill requires acknowledged order")
        if quantity <= 0 or price <= 0 or quantity > self.remaining_quantity:
            raise OrderTransitionError("invalid_fill")
        filled = self.filled_quantity + quantity
        weighted = quantity * price if self.average_fill_price is None else self.average_fill_price * self.filled_quantity + quantity * price
        status = OrderStatus.FILLED if filled == self.intent.quantity else OrderStatus.PARTIALLY_FILLED
        return replace(self, status=status, filled_quantity=filled, average_fill_price=weighted / filled)


def progress_to_acknowledged(order: PaperOrder) -> PaperOrder:
    for status in (OrderStatus.RISK_PENDING, OrderStatus.APPROVED, OrderStatus.SUBMISSION_PENDING, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED):
        order = order.transition(status)
    return order


@dataclass(frozen=True, slots=True)
class Position:
    instrument_id: str
    quantity: Decimal
    average_cost: Decimal


@dataclass(frozen=True, slots=True)
class CashBalance:
    currency: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class SplitAction:
    instrument_id: str
    ratio: Decimal


@dataclass(frozen=True, slots=True)
class DividendAction:
    instrument_id: str
    amount_per_share: Decimal
    currency: str


def apply_split(position: Position, action: SplitAction) -> Position:
    if position.instrument_id != action.instrument_id or action.ratio <= 0:
        raise ValueError("invalid_split_action")
    return Position(position.instrument_id, position.quantity * action.ratio, position.average_cost / action.ratio)


def apply_dividend(position: Position, cash: CashBalance, action: DividendAction) -> CashBalance:
    if position.instrument_id != action.instrument_id or cash.currency != action.currency or action.amount_per_share < 0:
        raise ValueError("invalid_dividend_action")
    return CashBalance(cash.currency, cash.amount + position.quantity * action.amount_per_share)


def apply_funding(position: Position, cash: CashBalance, mark_price: Decimal, funding_rate: Decimal) -> CashBalance:
    """Positive funding rate is paid by a long and received by a short."""
    if position.quantity == 0 or mark_price <= 0:
        raise ValueError("invalid_funding_inputs")
    return CashBalance(cash.currency, cash.amount - position.quantity * mark_price * funding_rate)


def apply_borrow_cost(short_position: Position, cash: CashBalance, mark_price: Decimal, daily_borrow_rate: Decimal) -> CashBalance:
    if short_position.quantity >= 0 or mark_price <= 0 or daily_borrow_rate < 0:
        raise ValueError("invalid_borrow_inputs")
    return CashBalance(cash.currency, cash.amount - abs(short_position.quantity) * mark_price * daily_borrow_rate)


@dataclass(frozen=True, slots=True)
class MarginPolicy:
    maximum_gross_notional: Decimal
    maximum_leverage: Decimal

    def permits(self, gross_notional_after: Decimal, equity: Decimal) -> bool:
        if equity <= 0 or gross_notional_after < 0:
            return False
        return gross_notional_after <= self.maximum_gross_notional and gross_notional_after / equity <= self.maximum_leverage


@dataclass(frozen=True, slots=True)
class PaperVenueConstraints:
    tick_size: Decimal
    lot_size: Decimal
    maximum_order_notional: Decimal

    def validate(self, quantity: Decimal, price: Decimal, session_open: bool) -> tuple[str, ...]:
        reasons: list[str] = []
        if not session_open:
            reasons.append("invalid_trading_session")
        if quantity <= 0 or price <= 0:
            reasons.append("invalid_quantity_or_price")
        if self.tick_size <= 0 or self.lot_size <= 0:
            reasons.append("invalid_venue_constraints")
        elif quantity % self.lot_size != 0:
            reasons.append("lot_size_violation")
        elif price % self.tick_size != 0:
            reasons.append("tick_size_violation")
        if quantity * price > self.maximum_order_notional:
            reasons.append("venue_order_notional_limit")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    complete: bool
    discrepancies: tuple[str, ...]


def reconcile(internal_positions: dict[str, Position], external_positions: dict[str, Position],
              internal_cash: CashBalance, external_cash: CashBalance, tolerance: Decimal = Decimal("0.01")) -> ReconciliationResult:
    discrepancies: list[str] = []
    if internal_cash.currency != external_cash.currency or abs(internal_cash.amount - external_cash.amount) > tolerance:
        discrepancies.append("cash_mismatch")
    for instrument_id in set(internal_positions) | set(external_positions):
        left, right = internal_positions.get(instrument_id), external_positions.get(instrument_id)
        if left is None or right is None or left.quantity != right.quantity or abs(left.average_cost - right.average_cost) > tolerance:
            discrepancies.append(f"position_mismatch:{instrument_id}")
    return ReconciliationResult(not discrepancies, tuple(discrepancies))


def portfolio_state_from_reconciliation(account_id: str, positions: dict[str, Position], reconciliation: ReconciliationResult) -> PortfolioState:
    """Unresolved reconciliation is deliberately propagated into the independent risk gate."""
    return PortfolioState(account_id, {key: value.quantity * value.average_cost for key, value in positions.items()},
                          reconciliation_complete=reconciliation.complete, risk_increase_allowed=reconciliation.complete)
