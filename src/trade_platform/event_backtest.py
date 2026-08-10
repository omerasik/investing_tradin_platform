"""Deterministic event-driven order simulation for research validation.

This module deliberately has no network, broker, or live-order integration.  It is
an independent execution model used to validate a vectorized research result under
explicit timing, liquidity, and cost assumptions.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from .corporate_actions import CorporateAction, CorporateActionError, apply_actions
from .domain import OrderSide
from .paper_execution import CashBalance, Position


class EventBacktestValidationError(ValueError):
    pass


class SimulatedOrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class SimulatedOrderStatus(StrEnum):
    PENDING_LATENCY = "PENDING_LATENCY"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    instrument_id: str
    occurred_at: datetime
    bid: Decimal
    ask: Decimal
    available_volume: Decimal
    session_open: bool = True

    def validate(self) -> None:
        if self.bid <= 0 or self.ask < self.bid or self.available_volume < 0:
            raise EventBacktestValidationError("invalid_market_event")


@dataclass(frozen=True, slots=True)
class ExecutionAssumptions:
    latency: timedelta = timedelta(0)
    maximum_participation: Decimal = Decimal("0.10")
    commission_bps: Decimal = Decimal("0")
    fixed_fee: Decimal = Decimal("0")
    impact_coefficient: Decimal = Decimal("0")

    def validate(self) -> None:
        if self.latency < timedelta(0) or not Decimal("0") < self.maximum_participation <= Decimal("1"):
            raise EventBacktestValidationError("invalid_execution_assumptions")
        if self.commission_bps < 0 or self.fixed_fee < 0 or self.impact_coefficient < 0:
            raise EventBacktestValidationError("invalid_execution_assumptions")


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    order_id: UUID
    instrument_id: str
    side: OrderSide
    order_type: SimulatedOrderType
    quantity: Decimal
    submitted_at: datetime
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    expires_at: datetime | None = None
    status: SimulatedOrderStatus = SimulatedOrderStatus.PENDING_LATENCY
    triggered: bool = False
    filled_quantity: Decimal = Decimal("0")

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    fill_id: UUID
    order_id: UUID
    instrument_id: str
    occurred_at: datetime
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal


@dataclass(frozen=True, slots=True)
class EventBacktestResult:
    orders: tuple[SimulatedOrder, ...]
    fills: tuple[SimulatedFill, ...]
    total_fees: Decimal
    net_cash_change: Decimal
    positions: tuple[Position, ...]
    cash: CashBalance


def create_order(
    instrument_id: str,
    side: OrderSide,
    order_type: SimulatedOrderType,
    quantity: Decimal,
    submitted_at: datetime,
    *,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    expires_at: datetime | None = None,
) -> SimulatedOrder:
    if not instrument_id or quantity <= 0 or submitted_at.tzinfo is None:
        raise EventBacktestValidationError("invalid_order")
    if order_type is SimulatedOrderType.LIMIT and (limit_price is None or limit_price <= 0):
        raise EventBacktestValidationError("limit_order_requires_limit_price")
    if order_type is SimulatedOrderType.STOP and (stop_price is None or stop_price <= 0):
        raise EventBacktestValidationError("stop_order_requires_stop_price")
    if order_type is SimulatedOrderType.STOP_LIMIT and (
        stop_price is None or stop_price <= 0 or limit_price is None or limit_price <= 0
    ):
        raise EventBacktestValidationError("stop_limit_order_requires_prices")
    if expires_at is not None and expires_at <= submitted_at:
        raise EventBacktestValidationError("invalid_order_expiry")
    return SimulatedOrder(uuid4(), instrument_id, side, order_type, quantity, submitted_at, limit_price, stop_price, expires_at)


class EventDrivenBacktester:
    """Processes events in chronological order; no event can fill before its latency window."""

    def __init__(self, assumptions: ExecutionAssumptions, *, initial_cash: Decimal = Decimal("0"), cash_currency: str = "USD") -> None:
        assumptions.validate()
        if initial_cash < 0 or not cash_currency.strip():
            raise EventBacktestValidationError("invalid_initial_portfolio")
        self._assumptions = assumptions
        self._orders: dict[UUID, SimulatedOrder] = {}
        self._fills: list[SimulatedFill] = []
        self._last_event_at: datetime | None = None
        self._positions: dict[str, Position] = {}
        self._cash = CashBalance(cash_currency, initial_cash)
        self._applied_corporate_actions: set[tuple[str, str, int]] = set()

    def submit(self, order: SimulatedOrder) -> None:
        if order.order_id in self._orders:
            raise EventBacktestValidationError("duplicate_order_id")
        self._orders[order.order_id] = order

    def cancel(self, order_id: UUID) -> SimulatedOrder:
        try:
            order = self._orders[order_id]
        except KeyError as error:
            raise EventBacktestValidationError("unknown_order") from error
        if order.status in {SimulatedOrderStatus.FILLED, SimulatedOrderStatus.CANCELLED, SimulatedOrderStatus.EXPIRED}:
            raise EventBacktestValidationError("order_not_cancellable")
        cancelled = replace(order, status=SimulatedOrderStatus.CANCELLED)
        self._orders[order_id] = cancelled
        return cancelled

    def process(self, event: MarketEvent) -> tuple[SimulatedFill, ...]:
        event.validate()
        if self._last_event_at is not None and event.occurred_at < self._last_event_at:
            raise EventBacktestValidationError("event_time_regression")
        self._last_event_at = event.occurred_at
        event_fills: list[SimulatedFill] = []
        for order_id, original in tuple(self._orders.items()):
            order = self._advance_order(original, event)
            self._orders[order_id] = order
            if order.status not in {SimulatedOrderStatus.WORKING, SimulatedOrderStatus.PARTIALLY_FILLED}:
                continue
            if not event.session_open or order.instrument_id != event.instrument_id or not self._is_executable(order, event):
                continue
            quantity = min(order.remaining_quantity, event.available_volume * self._assumptions.maximum_participation)
            if quantity <= 0:
                continue
            price = self._execution_price(order.side, event, quantity)
            fee = self._fee(quantity, price)
            fill = SimulatedFill(uuid4(), order.order_id, order.instrument_id, event.occurred_at, order.side, quantity, price, fee)
            self._fills.append(fill)
            event_fills.append(fill)
            self._apply_fill_to_portfolio(fill)
            filled_quantity = order.filled_quantity + quantity
            status = SimulatedOrderStatus.FILLED if filled_quantity == order.quantity else SimulatedOrderStatus.PARTIALLY_FILLED
            self._orders[order_id] = replace(order, filled_quantity=filled_quantity, status=status)
        return tuple(event_fills)

    def result(self) -> EventBacktestResult:
        fills = tuple(self._fills)
        total_fees = sum((fill.fee for fill in fills), Decimal("0"))
        cash = sum(
            ((-fill.quantity * fill.price if fill.side is OrderSide.BUY else fill.quantity * fill.price) - fill.fee for fill in fills),
            Decimal("0"),
        )
        return EventBacktestResult(tuple(self._orders.values()), fills, total_fees, cash, tuple(sorted(self._positions.values(), key=lambda item: item.instrument_id)), self._cash)

    def apply_corporate_actions(self, actions: list[CorporateAction], *, as_of: datetime) -> None:
        """Apply each effective, known action exactly once to simulated positions/cash."""
        if as_of.tzinfo is None:
            raise EventBacktestValidationError("corporate_action_timestamp_must_be_timezone_aware")
        for action in sorted(actions, key=lambda item: (item.effective_at, item.provider, item.source_identifier, item.revision)):
            key = (action.provider, action.source_identifier, action.revision)
            if key in self._applied_corporate_actions:
                continue
            if action.effective_at > as_of:
                raise EventBacktestValidationError("future_corporate_action")
            position = self._positions.get(action.instrument_id)
            if position is None or position.quantity == 0:
                self._applied_corporate_actions.add(key)
                continue
            try:
                applied = apply_actions(position, self._cash, [action], as_of=as_of)
            except CorporateActionError as error:
                raise EventBacktestValidationError("corporate_action_rejected") from error
            del self._positions[action.instrument_id]
            if applied.position.quantity != 0:
                existing = self._positions.get(applied.position.instrument_id)
                if existing is not None and existing.quantity != 0:
                    raise EventBacktestValidationError("corporate_action_position_merge_unsupported")
                self._positions[applied.position.instrument_id] = applied.position
            self._cash = applied.cash
            self._applied_corporate_actions.add(key)

    def _advance_order(self, order: SimulatedOrder, event: MarketEvent) -> SimulatedOrder:
        if order.status in {SimulatedOrderStatus.FILLED, SimulatedOrderStatus.CANCELLED, SimulatedOrderStatus.EXPIRED}:
            return order
        if order.expires_at is not None and event.occurred_at >= order.expires_at:
            return replace(order, status=SimulatedOrderStatus.EXPIRED)
        if event.occurred_at < order.submitted_at + self._assumptions.latency:
            return order
        if order.order_type in {SimulatedOrderType.STOP, SimulatedOrderType.STOP_LIMIT} and not order.triggered:
            triggered = event.ask >= order.stop_price if order.side is OrderSide.BUY else event.bid <= order.stop_price
            if not triggered:
                return replace(order, status=SimulatedOrderStatus.WORKING)
            order = replace(order, triggered=True, status=SimulatedOrderStatus.WORKING)
        elif order.status is SimulatedOrderStatus.PENDING_LATENCY:
            order = replace(order, status=SimulatedOrderStatus.WORKING)
        return order

    @staticmethod
    def _is_executable(order: SimulatedOrder, event: MarketEvent) -> bool:
        if order.order_type is SimulatedOrderType.LIMIT or (order.order_type is SimulatedOrderType.STOP_LIMIT and order.triggered):
            return event.ask <= order.limit_price if order.side is OrderSide.BUY else event.bid >= order.limit_price
        return order.order_type is SimulatedOrderType.MARKET or (order.order_type is SimulatedOrderType.STOP and order.triggered)

    def _execution_price(self, side: OrderSide, event: MarketEvent, quantity: Decimal) -> Decimal:
        reference = event.ask if side is OrderSide.BUY else event.bid
        impact = self._assumptions.impact_coefficient * (quantity / event.available_volume).sqrt()
        return reference * (Decimal("1") + impact if side is OrderSide.BUY else Decimal("1") - impact)

    def _fee(self, quantity: Decimal, price: Decimal) -> Decimal:
        return self._assumptions.fixed_fee + quantity * price * self._assumptions.commission_bps / Decimal("10000")

    def _apply_fill_to_portfolio(self, fill: SimulatedFill) -> None:
        existing = self._positions.get(fill.instrument_id, Position(fill.instrument_id, Decimal("0"), Decimal("0")))
        delta = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        updated_quantity = existing.quantity + delta
        if existing.quantity == 0 or existing.quantity * delta > 0:
            average_cost = (abs(existing.quantity) * existing.average_cost + abs(delta) * fill.price) / abs(updated_quantity)
        elif updated_quantity == 0:
            average_cost = Decimal("0")
        elif existing.quantity * updated_quantity < 0:
            average_cost = fill.price
        else:
            average_cost = existing.average_cost
        self._positions[fill.instrument_id] = Position(fill.instrument_id, updated_quantity, average_cost)
        direction = Decimal("-1") if fill.side is OrderSide.BUY else Decimal("1")
        self._cash = CashBalance(self._cash.currency, self._cash.amount + direction * fill.quantity * fill.price - fill.fee)
