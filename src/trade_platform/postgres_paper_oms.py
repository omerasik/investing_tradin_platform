"""Event-sourced PostgreSQL persistence for paper OMS and broker synchronization.

Every broker event is one database transaction: the external-event receipt,
cursor update, fill/cancel/replace evidence and lifecycle event either all
commit or all roll back.  Order rows are immutable intents; current state is
reconstructed from immutable events after every restart.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from .broker_adapter import BrokerAccountSnapshot, BrokerOrderEvent
from .domain import OrderIntent, OrderSide, OrderStatus, utc_now
from .paper_execution import CashBalance, PaperOrder, Position, ReconciliationResult
from .paper_oms import (
    PaperOmsError,
    PaperOmsEvent,
    RecordedFill,
    RecordedReconciledAccount,
    RecordedReconciliation,
)
from .persistence import PostgresDatabase


class PostgresPaperOms:
    """The sole PostgreSQL authority for paper order lifecycle evidence."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    @staticmethod
    def _append_event(
        cursor, intent_id: UUID, event_type: str, payload: dict[str, str], occurred_at: datetime
    ) -> None:
        cursor.execute(
            "INSERT INTO oms_events (oms_event_id, intent_id, event_type, occurred_at, payload) VALUES (%s, %s, %s, %s, %s::jsonb)",
            (uuid4(), intent_id, event_type, occurred_at, json.dumps(payload, sort_keys=True)),
        )

    @staticmethod
    def _order(cursor, intent_id: UUID) -> PaperOrder:
        cursor.execute(
            "SELECT intent_id, signal_id, instrument_id, account_id, side, quantity, limit_price, created_at FROM paper_order_intents WHERE intent_id = %s",
            (intent_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise PaperOmsError("unknown_order_intent")
        intent = OrderIntent(
            UUID(str(row[0])),
            UUID(str(row[1])),
            str(row[2]),
            str(row[3]),
            OrderSide(str(row[4])),
            Decimal(row[5]),
            Decimal(row[6]),
            row[7],
        )
        order = PaperOrder(intent)
        cursor.execute(
            "SELECT event_type, payload FROM oms_events WHERE intent_id = %s ORDER BY event_sequence",
            (intent_id,),
        )
        for event_type, payload in cursor.fetchall():
            if event_type == "ORDER_STATUS_CHANGED":
                order = order.transition(OrderStatus(payload["to"]))
            elif event_type == "ORDER_AMENDED":
                order = replace(
                    order,
                    intent=replace(
                        order.intent,
                        quantity=Decimal(payload["quantity"]),
                        limit_price=Decimal(payload["limit_price"]),
                    ),
                )
            elif event_type == "FILL_INGESTED":
                order = order.fill(Decimal(payload["quantity"]), Decimal(payload["price"]))
        return order

    def create(self, order: PaperOrder) -> PaperOrder:
        if order.status is not OrderStatus.PROPOSED:
            raise PaperOmsError("new_order_must_be_proposed")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM paper_order_intents WHERE intent_id = %s",
                    (order.intent.intent_id,),
                )
                if cursor.fetchone() is not None:
                    existing = self._order(cursor, order.intent.intent_id)
                    if existing.intent != order.intent:
                        raise PaperOmsError("idempotency_key_payload_conflict")
                    return existing
                intent = order.intent
                cursor.execute(
                    "INSERT INTO paper_order_intents (intent_id, signal_id, account_id, instrument_id, side, quantity, limit_price, status, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        intent.intent_id,
                        intent.signal_id,
                        intent.account_id,
                        UUID(intent.instrument_id),
                        intent.side.value,
                        intent.quantity,
                        intent.limit_price,
                        OrderStatus.PROPOSED.value,
                        intent.created_at,
                    ),
                )
                self._append_event(
                    cursor,
                    intent.intent_id,
                    "ORDER_CREATED",
                    {"status": OrderStatus.PROPOSED.value},
                    intent.created_at,
                )
                return order
        except PaperOmsError:
            raise
        except Exception as error:
            raise PaperOmsError("postgres_oms_create_failed") from error

    def get(self, intent_id: UUID) -> PaperOrder:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                return self._order(cursor, intent_id)
        except PaperOmsError:
            raise
        except Exception as error:
            raise PaperOmsError("postgres_oms_read_failed") from error

    def transition(
        self, intent_id: UUID, next_status: OrderStatus, *, actor: str = "system"
    ) -> PaperOrder:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                order = self._order(cursor, intent_id)
                try:
                    updated = order.transition(next_status)
                except ValueError as error:
                    raise PaperOmsError("invalid_order_transition") from error
                self._append_event(
                    cursor,
                    intent_id,
                    "ORDER_STATUS_CHANGED",
                    {"from": order.status.value, "to": next_status.value, "actor": actor},
                    utc_now(),
                )
                return updated
        except PaperOmsError:
            raise
        except Exception as error:
            raise PaperOmsError("postgres_oms_transition_failed") from error

    def _ingest_fill(
        self,
        cursor,
        external_fill_id: str,
        intent_id: UUID,
        quantity: Decimal,
        price: Decimal,
        occurred_at: datetime,
    ) -> PaperOrder:
        cursor.execute(
            "SELECT intent_id, quantity, price FROM fills WHERE external_fill_id = %s",
            (external_fill_id,),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if (
                UUID(str(existing[0])) != intent_id
                or Decimal(existing[1]) != quantity
                or Decimal(existing[2]) != price
            ):
                raise PaperOmsError("external_fill_id_conflict")
            return self._order(cursor, intent_id)
        order = self._order(cursor, intent_id)
        try:
            updated = order.fill(quantity, price)
        except ValueError as error:
            raise PaperOmsError("fill_rejected_by_order_state") from error
        cursor.execute(
            "INSERT INTO fills (fill_id, external_fill_id, intent_id, occurred_at, quantity, price) VALUES (%s,%s,%s,%s,%s,%s)",
            (uuid4(), external_fill_id, intent_id, occurred_at, quantity, price),
        )
        self._append_event(
            cursor,
            intent_id,
            "FILL_INGESTED",
            {
                "external_fill_id": external_fill_id,
                "quantity": str(quantity),
                "price": str(price),
                "status": updated.status.value,
            },
            occurred_at,
        )
        return updated

    def ingest_fill(
        self,
        external_fill_id: str,
        intent_id: UUID,
        quantity: Decimal,
        price: Decimal,
        *,
        occurred_at: datetime | None = None,
    ) -> PaperOrder:
        occurred = occurred_at or utc_now()
        if not external_fill_id.strip() or quantity <= 0 or price <= 0 or occurred.tzinfo is None:
            raise PaperOmsError("invalid_fill")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                return self._ingest_fill(
                    cursor, external_fill_id, intent_id, quantity, price, occurred
                )
        except PaperOmsError:
            raise
        except Exception as error:
            raise PaperOmsError("postgres_fill_ingestion_failed") from error

    def cancel(self, intent_id: UUID, *, actor: str = "operator") -> PaperOrder:
        self.transition(intent_id, OrderStatus.CANCEL_PENDING, actor=actor)
        return self.transition(intent_id, OrderStatus.CANCELLED, actor=actor)

    def amend(
        self, intent_id: UUID, *, quantity: Decimal, limit_price: Decimal, actor: str = "operator"
    ) -> PaperOrder:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                order = self._order(cursor, intent_id)
                if (
                    order.status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED}
                    or quantity < order.filled_quantity
                    or limit_price <= 0
                ):
                    raise PaperOmsError("invalid_order_amendment")
                amended = replace(
                    order, intent=replace(order.intent, quantity=quantity, limit_price=limit_price)
                )
                self._append_event(
                    cursor,
                    intent_id,
                    "ORDER_AMENDED",
                    {
                        "previous_quantity": str(order.intent.quantity),
                        "quantity": str(quantity),
                        "previous_limit_price": str(order.intent.limit_price),
                        "limit_price": str(limit_price),
                        "actor": actor,
                    },
                    utc_now(),
                )
                return amended
        except PaperOmsError:
            raise
        except Exception as error:
            raise PaperOmsError("postgres_order_amend_failed") from error

    def events(self, intent_id: UUID) -> list[PaperOmsEvent]:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT event_sequence, oms_event_id, intent_id, event_type, occurred_at, payload FROM oms_events WHERE intent_id = %s ORDER BY event_sequence",
                    (intent_id,),
                )
                return [
                    PaperOmsEvent(
                        int(row[0]),
                        UUID(str(row[1])),
                        UUID(str(row[2])),
                        str(row[3]),
                        row[4],
                        dict(row[5]),
                    )
                    for row in cursor.fetchall()
                ]
        except Exception as error:
            raise PaperOmsError("postgres_oms_events_failed") from error

    def fills(self, intent_id: UUID) -> list[RecordedFill]:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT fill_id, external_fill_id, intent_id, occurred_at, quantity, price FROM fills WHERE intent_id = %s ORDER BY occurred_at, fill_id",
                    (intent_id,),
                )
                return [
                    RecordedFill(
                        UUID(str(row[0])),
                        str(row[1]),
                        UUID(str(row[2])),
                        row[3],
                        Decimal(row[4]),
                        Decimal(row[5]),
                    )
                    for row in cursor.fetchall()
                ]
        except Exception as error:
            raise PaperOmsError("postgres_oms_fills_failed") from error

    def record_reconciliation_with_account(
        self,
        snapshot: BrokerAccountSnapshot,
        result: ReconciliationResult,
        *,
        healthy: bool,
        ingested_at: datetime,
    ) -> tuple[RecordedReconciliation, RecordedReconciledAccount | None]:
        if (
            not snapshot.account_id.strip()
            or not snapshot.source.strip()
            or snapshot.as_of.tzinfo is None
            or ingested_at.tzinfo is None
            or ingested_at < snapshot.as_of
        ):
            raise PaperOmsError("invalid_reconciled_account_snapshot")
        record = RecordedReconciliation(
            uuid4(),
            snapshot.account_id,
            snapshot.source,
            snapshot.as_of,
            result.complete,
            result.discrepancies,
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO reconciliations (reconciliation_id, account_id, source, occurred_at, complete, discrepancies) VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                    (
                        record.run_id,
                        record.account_id,
                        record.source,
                        record.occurred_at,
                        record.complete,
                        json.dumps(record.discrepancies),
                    ),
                )
                if not record.complete:
                    return record, None
                evidence = RecordedReconciledAccount(
                    uuid4(), record.run_id, snapshot, healthy, ingested_at
                )
                cursor.execute(
                    "INSERT INTO reconciled_account_evidence (evidence_id, reconciliation_id, account_id, source, as_of, cash_currency, cash_amount, buying_power, open_intent_ids, fill_ids, realized_pnl, fees, healthy, ingested_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)",
                    (
                        evidence.evidence_id,
                        record.run_id,
                        snapshot.account_id,
                        snapshot.source,
                        snapshot.as_of,
                        snapshot.cash.currency,
                        snapshot.cash.amount,
                        snapshot.buying_power,
                        json.dumps([str(item) for item in snapshot.open_intent_ids]),
                        json.dumps(list(snapshot.fill_ids)),
                        snapshot.realized_pnl,
                        snapshot.fees,
                        healthy,
                        ingested_at,
                    ),
                )
                for instrument_id, position in snapshot.positions.items():
                    cursor.execute(
                        "INSERT INTO reconciled_position_evidence (evidence_id, instrument_id, quantity, average_cost) VALUES (%s,%s,%s,%s)",
                        (
                            evidence.evidence_id,
                            UUID(instrument_id),
                            position.quantity,
                            position.average_cost,
                        ),
                    )
                return record, evidence
        except Exception as error:
            raise PaperOmsError("postgres_reconciliation_persistence_failed") from error

    def latest_reconciled_account(
        self, account_id: str, decision_at: datetime
    ) -> RecordedReconciledAccount | None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT evidence_id, reconciliation_id, source, as_of, cash_currency, cash_amount, buying_power, open_intent_ids, fill_ids, realized_pnl, fees, healthy, ingested_at FROM reconciled_account_evidence WHERE account_id = %s AND as_of <= %s AND ingested_at <= %s ORDER BY as_of DESC, ingested_at DESC LIMIT 1",
                    (account_id, decision_at, decision_at),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                cursor.execute(
                    "SELECT instrument_id, quantity, average_cost FROM reconciled_position_evidence WHERE evidence_id = %s",
                    (row[0],),
                )
                positions = {
                    str(item[0]): Position(str(item[0]), Decimal(item[1]), Decimal(item[2]))
                    for item in cursor.fetchall()
                }
                snapshot = BrokerAccountSnapshot(
                    account_id,
                    str(row[2]),
                    row[3],
                    CashBalance(str(row[4]), Decimal(row[5])),
                    Decimal(row[6]),
                    positions,
                    tuple(UUID(value) for value in row[7]),
                    tuple(row[8]),
                    Decimal(row[9]),
                    Decimal(row[10]),
                )
                return RecordedReconciledAccount(
                    UUID(str(row[0])), UUID(str(row[1])), snapshot, bool(row[11]), row[12]
                )
        except Exception as error:
            raise PaperOmsError("postgres_reconciled_account_read_failed") from error

    def latest_reconciliation(self, account_id: str) -> RecordedReconciliation | None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT reconciliation_id, account_id, source, occurred_at, complete, discrepancies FROM reconciliations WHERE account_id = %s ORDER BY occurred_at DESC, reconciliation_id DESC LIMIT 1",
                    (account_id,),
                )
                row = cursor.fetchone()
                return (
                    None
                    if row is None
                    else RecordedReconciliation(
                        UUID(str(row[0])),
                        str(row[1]),
                        str(row[2]),
                        row[3],
                        bool(row[4]),
                        tuple(row[5]),
                    )
                )
        except Exception as error:
            raise PaperOmsError("postgres_reconciliation_read_failed") from error

    def apply_broker_event(
        self, account_id: str, broker_name: str, event: BrokerOrderEvent
    ) -> bool:
        """Persist receipt, cursor, fill/cancel/replace and OMS state atomically."""
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM broker_sync_events WHERE external_event_id = %s",
                    (event.external_event_id,),
                )
                if cursor.fetchone() is not None:
                    return False
                cursor.execute(
                    "SELECT cursor FROM broker_event_cursors WHERE account_id = %s AND source = %s FOR UPDATE",
                    (account_id, broker_name),
                )
                current = cursor.fetchone()
                previous = int(current[0]) if current is not None else 0
                if event.sequence <= previous:
                    raise PaperOmsError("broker_event_sequence_not_monotonic")
                cursor.execute(
                    "INSERT INTO broker_sync_events (event_id, account_id, source, sequence, external_event_id, intent_id, event_type, status, occurred_at, details) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (
                        event.event_id,
                        account_id,
                        broker_name,
                        event.sequence,
                        event.external_event_id,
                        event.client_intent_id,
                        event.event_type,
                        event.status.value,
                        event.occurred_at,
                        json.dumps(event.details, sort_keys=True),
                    ),
                )
                if event.event_type == "FILL":
                    self._ingest_fill(
                        cursor,
                        event.details["external_fill_id"],
                        event.client_intent_id,
                        Decimal(event.details["quantity"]),
                        Decimal(event.details["price"]),
                        event.occurred_at,
                    )
                elif event.event_type == "ORDER_CANCELLED":
                    order = self._order(cursor, event.client_intent_id)
                    if order.status in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED}:
                        pending = order.transition(OrderStatus.CANCEL_PENDING)
                        self._append_event(
                            cursor,
                            event.client_intent_id,
                            "ORDER_STATUS_CHANGED",
                            {
                                "from": order.status.value,
                                "to": pending.status.value,
                                "actor": "broker_sync",
                            },
                            event.occurred_at,
                        )
                        self._append_event(
                            cursor,
                            event.client_intent_id,
                            "ORDER_STATUS_CHANGED",
                            {
                                "from": pending.status.value,
                                "to": OrderStatus.CANCELLED.value,
                                "actor": "broker_sync",
                            },
                            event.occurred_at,
                        )
                elif event.event_type == "ORDER_REPLACED":
                    order = self._order(cursor, event.client_intent_id)
                    quantity, limit_price = (
                        Decimal(event.details["quantity"]),
                        Decimal(event.details["limit_price"]),
                    )
                    if (
                        order.status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED}
                        or quantity < order.filled_quantity
                        or limit_price <= 0
                    ):
                        raise PaperOmsError("invalid_order_amendment")
                    self._append_event(
                        cursor,
                        event.client_intent_id,
                        "ORDER_AMENDED",
                        {
                            "previous_quantity": str(order.intent.quantity),
                            "quantity": str(quantity),
                            "previous_limit_price": str(order.intent.limit_price),
                            "limit_price": str(limit_price),
                            "actor": "broker_sync",
                        },
                        event.occurred_at,
                    )
                cursor.execute(
                    "INSERT INTO broker_event_cursors (account_id, source, cursor, advanced_at) VALUES (%s,%s,%s,%s) ON CONFLICT(account_id, source) DO UPDATE SET cursor = EXCLUDED.cursor, advanced_at = EXCLUDED.advanced_at",
                    (account_id, broker_name, str(event.sequence), event.occurred_at),
                )
                return True
        except (PaperOmsError, KeyError, ValueError):
            raise
        except Exception as error:
            raise PaperOmsError("postgres_broker_event_transaction_failed") from error

    def close(self) -> None:
        self._database.close()


class PostgresBrokerEventStore:
    """Read-only cursor facade; PostgresPaperOms owns event writes atomically."""

    def __init__(self, database: PostgresDatabase, account_id: str) -> None:
        self._database, self._account_id = database, account_id

    def last_sequence(self, broker_name: str) -> int:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT cursor FROM broker_event_cursors WHERE account_id = %s AND source = %s",
                    (self._account_id, broker_name),
                )
                row = cursor.fetchone()
                return 0 if row is None else int(row[0])
        except Exception as error:
            raise PaperOmsError("postgres_broker_cursor_read_failed") from error

    def close(self) -> None:
        # Connection lifetime is owned by the OMS.
        return None
