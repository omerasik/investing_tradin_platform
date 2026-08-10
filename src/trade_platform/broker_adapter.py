"""Broker-neutral, paper-only adapter contracts and deterministic sandbox-compatible implementation."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol
from uuid import UUID, uuid4

from .domain import OrderIntent, OrderStatus, utc_now
from .paper_execution import CashBalance, PaperOrder, Position, ReconciliationResult, reconcile


class BrokerAdapterError(ValueError):
    pass


class BrokerMode(str, Enum):
    SIMULATED_PAPER = "SIMULATED_PAPER"
    SANDBOX_PAPER = "SANDBOX_PAPER"


class BrokerErrorCode(str, Enum):
    DUPLICATE_INTENT = "DUPLICATE_INTENT"
    UNKNOWN_ORDER = "UNKNOWN_ORDER"
    INVALID_STATE = "INVALID_STATE"
    INVALID_REPLACEMENT = "INVALID_REPLACEMENT"
    INVALID_FILL = "INVALID_FILL"


@dataclass(frozen=True, slots=True)
class BrokerCapabilities:
    supports_paper_orders: bool
    supports_cancel_replace: bool
    supports_order_events: bool
    supports_account_sync: bool
    supports_fill_sync: bool
    network_connected: bool = False


@dataclass(frozen=True, slots=True)
class BrokerConfiguration:
    broker_name: str
    mode: BrokerMode
    account_id: str
    credential_reference: str | None = None

    def validate(self) -> None:
        if not self.broker_name.strip() or not self.account_id.strip():
            raise BrokerAdapterError("broker_name_and_account_required")
        if self.credential_reference is not None and not self.credential_reference.strip():
            raise BrokerAdapterError("invalid_credential_reference")


@dataclass(frozen=True, slots=True)
class BrokerOrderEvent:
    sequence: int
    event_id: UUID
    client_intent_id: UUID
    status: OrderStatus
    event_type: str
    occurred_at: datetime
    external_event_id: str
    details: dict[str, str]


@dataclass(frozen=True, slots=True)
class BrokerAccountSnapshot:
    account_id: str
    source: str
    as_of: datetime
    cash: CashBalance
    buying_power: Decimal
    positions: dict[str, Position]
    open_intent_ids: tuple[UUID, ...]
    fill_ids: tuple[str, ...]
    realized_pnl: Decimal
    fees: Decimal


@dataclass(frozen=True, slots=True)
class InternalAccountState:
    cash: CashBalance
    positions: dict[str, Position]
    open_intent_ids: tuple[UUID, ...]
    fill_ids: tuple[str, ...]
    realized_pnl: Decimal
    fees: Decimal


@dataclass(frozen=True, slots=True)
class BrokerReconciliationResult:
    complete: bool
    discrepancies: tuple[str, ...]


def reconcile_broker_account(internal: InternalAccountState, snapshot: BrokerAccountSnapshot, *, tolerance: Decimal = Decimal("0.01")) -> BrokerReconciliationResult:
    """Compare all available account evidence; any difference blocks risk increases upstream."""
    base: ReconciliationResult = reconcile(internal.positions, snapshot.positions, internal.cash, snapshot.cash, tolerance)
    differences = list(base.discrepancies)
    if set(internal.open_intent_ids) != set(snapshot.open_intent_ids):
        differences.append("open_order_mismatch")
    if set(internal.fill_ids) != set(snapshot.fill_ids):
        differences.append("fill_mismatch")
    if abs(internal.realized_pnl - snapshot.realized_pnl) > tolerance:
        differences.append("realized_pnl_mismatch")
    if abs(internal.fees - snapshot.fees) > tolerance:
        differences.append("fee_mismatch")
    return BrokerReconciliationResult(not differences, tuple(differences))


class PaperBrokerAdapter(Protocol):
    configuration: BrokerConfiguration
    capabilities: BrokerCapabilities

    def health(self) -> bool: ...
    def sync_account(self) -> BrokerAccountSnapshot: ...
    def submit(self, order: PaperOrder) -> PaperOrder: ...
    def cancel(self, intent_id: UUID) -> PaperOrder: ...
    def replace(self, intent_id: UUID, *, quantity: Decimal, limit_price: Decimal) -> PaperOrder: ...
    def order_events(self, *, after_sequence: int = 0) -> tuple[BrokerOrderEvent, ...]: ...


class SandboxPaperBrokerAdapter:
    """Deterministic implementation of the paper adapter contract; it never opens a network connection."""

    capabilities = BrokerCapabilities(True, True, True, True, True, network_connected=False)

    def __init__(self, configuration: BrokerConfiguration, *, cash: CashBalance) -> None:
        configuration.validate()
        self.configuration = configuration
        self._cash = cash
        self._orders: dict[UUID, PaperOrder] = {}
        self._positions: dict[str, Position] = {}
        self._events: list[BrokerOrderEvent] = []
        self._fill_ids: list[str] = []
        self._realized_pnl = Decimal("0")
        self._fees = Decimal("0")

    def health(self) -> bool:
        return True

    def submit(self, order: PaperOrder) -> PaperOrder:
        if order.intent.intent_id in self._orders:
            raise BrokerAdapterError(BrokerErrorCode.DUPLICATE_INTENT.value)
        if order.status is not OrderStatus.APPROVED:
            raise BrokerAdapterError(BrokerErrorCode.INVALID_STATE.value)
        submitted = order.transition(OrderStatus.SUBMISSION_PENDING).transition(OrderStatus.SUBMITTED).transition(OrderStatus.ACKNOWLEDGED)
        self._orders[submitted.intent.intent_id] = submitted
        self._event(submitted, "ORDER_ACKNOWLEDGED", {"mode": self.configuration.mode.value})
        return submitted

    def cancel(self, intent_id: UUID) -> PaperOrder:
        order = self._order(intent_id)
        try:
            cancelled = order.transition(OrderStatus.CANCEL_PENDING).transition(OrderStatus.CANCELLED)
        except ValueError as error:
            raise BrokerAdapterError(BrokerErrorCode.INVALID_STATE.value) from error
        self._orders[intent_id] = cancelled
        self._event(cancelled, "ORDER_CANCELLED", {})
        return cancelled

    def replace(self, intent_id: UUID, *, quantity: Decimal, limit_price: Decimal) -> PaperOrder:
        order = self._order(intent_id)
        if order.status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED} or quantity < order.filled_quantity or limit_price <= 0:
            raise BrokerAdapterError(BrokerErrorCode.INVALID_REPLACEMENT.value)
        amended_intent: OrderIntent = replace(order.intent, quantity=quantity, limit_price=limit_price)
        amended = replace(order, intent=amended_intent)
        self._orders[intent_id] = amended
        self._event(amended, "ORDER_REPLACED", {"quantity": str(quantity), "limit_price": str(limit_price)})
        return amended

    def simulate_fill(self, external_fill_id: str, intent_id: UUID, quantity: Decimal, price: Decimal, *, fee: Decimal = Decimal("0")) -> PaperOrder:
        if not external_fill_id.strip() or fee < 0 or external_fill_id in self._fill_ids:
            raise BrokerAdapterError(BrokerErrorCode.INVALID_FILL.value)
        order = self._order(intent_id)
        try:
            filled = order.fill(quantity, price)
        except ValueError as error:
            raise BrokerAdapterError(BrokerErrorCode.INVALID_FILL.value) from error
        notional = quantity * price
        existing = self._positions.get(order.intent.instrument_id)
        position = Position(order.intent.instrument_id, Decimal("0"), Decimal("0")) if existing is None else existing
        signed_quantity = quantity if order.intent.side.value == "BUY" else -quantity
        new_quantity = position.quantity + signed_quantity
        if position.quantity == 0 or position.quantity * signed_quantity > 0:
            average_cost = (position.average_cost * abs(position.quantity) + price * quantity) / abs(new_quantity)
        else:
            average_cost = position.average_cost if new_quantity != 0 else Decimal("0")
            self._realized_pnl += (price - position.average_cost) * min(abs(position.quantity), quantity) * (Decimal("1") if position.quantity > 0 else Decimal("-1"))
        self._positions[order.intent.instrument_id] = Position(order.intent.instrument_id, new_quantity, average_cost)
        cash_delta = -notional if order.intent.side.value == "BUY" else notional
        self._cash = CashBalance(self._cash.currency, self._cash.amount + cash_delta - fee)
        self._fees += fee
        self._orders[intent_id] = filled
        self._fill_ids.append(external_fill_id)
        self._event(filled, "FILL", {"external_fill_id": external_fill_id, "quantity": str(quantity), "price": str(price), "fee": str(fee)})
        return filled

    def sync_account(self) -> BrokerAccountSnapshot:
        open_statuses = {OrderStatus.SUBMISSION_PENDING, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCEL_PENDING}
        return BrokerAccountSnapshot(self.configuration.account_id, self.configuration.broker_name, utc_now(), self._cash, self._cash.amount, dict(self._positions), tuple(intent_id for intent_id, order in self._orders.items() if order.status in open_statuses), tuple(self._fill_ids), self._realized_pnl, self._fees)

    def order_events(self, *, after_sequence: int = 0) -> tuple[BrokerOrderEvent, ...]:
        if after_sequence < 0:
            raise BrokerAdapterError("invalid_event_sequence")
        return tuple(event for event in self._events if event.sequence > after_sequence)

    def _order(self, intent_id: UUID) -> PaperOrder:
        try:
            return self._orders[intent_id]
        except KeyError as error:
            raise BrokerAdapterError(BrokerErrorCode.UNKNOWN_ORDER.value) from error

    def _event(self, order: PaperOrder, event_type: str, details: dict[str, str]) -> None:
        sequence = len(self._events) + 1
        self._events.append(BrokerOrderEvent(sequence, uuid4(), order.intent.intent_id, order.status, event_type, utc_now(), f"sandbox:{sequence}", details))
