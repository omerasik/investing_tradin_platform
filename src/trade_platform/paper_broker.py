"""Fixture-only paper-broker boundary; no network, credentials, or real-order capability."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from .domain import OrderStatus
from .paper_execution import OrderTransitionError, PaperOrder


class PaperBrokerValidationError(ValueError):
    pass


class PaperBrokerAdapter(Protocol):
    def submit_paper_order(self, order: PaperOrder) -> PaperOrder: ...
    def cancel_paper_order(self, intent_id: UUID) -> PaperOrder: ...
    def get_paper_order(self, intent_id: UUID) -> PaperOrder: ...


@dataclass(frozen=True, slots=True)
class ExecutionQualityReport:
    intent_id: UUID
    requested_quantity: Decimal
    filled_quantity: Decimal
    fill_rate: Decimal
    benchmark_price: Decimal
    average_fill_price: Decimal | None
    absolute_slippage: Decimal | None
    final_status: OrderStatus


def execution_quality(order: PaperOrder, benchmark_price: Decimal) -> ExecutionQualityReport:
    if benchmark_price <= 0:
        raise PaperBrokerValidationError("invalid_execution_benchmark")
    fill_rate = order.filled_quantity / order.intent.quantity
    slippage = None if order.average_fill_price is None else abs(order.average_fill_price - benchmark_price) / benchmark_price
    return ExecutionQualityReport(order.intent.intent_id, order.intent.quantity, order.filled_quantity, fill_rate, benchmark_price, order.average_fill_price, slippage, order.status)


class FixturePaperBroker:
    """A deterministic local adapter to prove OMS/reconciliation contracts before sandbox credentials exist."""

    def __init__(self) -> None:
        self._orders: dict[UUID, PaperOrder] = {}

    def submit_paper_order(self, order: PaperOrder) -> PaperOrder:
        if order.intent.intent_id in self._orders:
            raise PaperBrokerValidationError("duplicate_paper_broker_intent")
        if order.status != OrderStatus.APPROVED:
            raise PaperBrokerValidationError("paper_submission_requires_risk_approved_order")
        submitted = order.transition(OrderStatus.SUBMISSION_PENDING).transition(OrderStatus.SUBMITTED).transition(OrderStatus.ACKNOWLEDGED)
        self._orders[order.intent.intent_id] = submitted
        return submitted

    def simulate_fill(self, intent_id: UUID, quantity: Decimal, price: Decimal) -> PaperOrder:
        order = self.get_paper_order(intent_id)
        try:
            filled = order.fill(quantity, price)
        except OrderTransitionError as error:
            raise PaperBrokerValidationError("invalid_fixture_fill") from error
        self._orders[intent_id] = filled
        return filled

    def cancel_paper_order(self, intent_id: UUID) -> PaperOrder:
        order = self.get_paper_order(intent_id)
        try:
            cancelled = order.transition(OrderStatus.CANCEL_PENDING).transition(OrderStatus.CANCELLED)
        except OrderTransitionError as error:
            raise PaperBrokerValidationError("invalid_fixture_cancellation") from error
        self._orders[intent_id] = cancelled
        return cancelled

    def get_paper_order(self, intent_id: UUID) -> PaperOrder:
        try:
            return self._orders[intent_id]
        except KeyError as error:
            raise PaperBrokerValidationError("unknown_paper_broker_intent") from error
