"""Durable, paper-only OMS state and immutable lifecycle evidence.

The store deliberately has no broker SDK or network code.  It provides the
state/idempotency boundary that a future sandbox adapter must use before it can
be considered for activation.
"""

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from .broker_adapter import BrokerAccountSnapshot
from .domain import OrderIntent, OrderSide, OrderStatus, utc_now
from .paper_execution import CashBalance, PaperOrder, Position, ReconciliationResult


class PaperOmsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaperOmsEvent:
    sequence: int
    event_id: UUID
    intent_id: UUID
    event_type: str
    occurred_at: datetime
    payload: dict[str, str]


@dataclass(frozen=True, slots=True)
class RecordedFill:
    fill_id: UUID
    external_fill_id: str
    intent_id: UUID
    occurred_at: datetime
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class RecordedReconciliation:
    run_id: UUID
    account_id: str
    source: str
    occurred_at: datetime
    complete: bool
    discrepancies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecordedReconciledAccount:
    evidence_id: UUID
    reconciliation_id: UUID
    snapshot: BrokerAccountSnapshot
    healthy: bool
    ingested_at: datetime


class SQLitePaperOms:
    """Current order state plus append-only events/fills/reconciliation records."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS paper_orders (
                intent_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
                account_id TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL, limit_price TEXT NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL, filled_quantity TEXT NOT NULL,
                average_fill_price TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_order_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                intent_id TEXT NOT NULL REFERENCES paper_orders(intent_id), event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_fills (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, fill_id TEXT NOT NULL UNIQUE, external_fill_id TEXT NOT NULL UNIQUE,
                intent_id TEXT NOT NULL REFERENCES paper_orders(intent_id), occurred_at TEXT NOT NULL,
                quantity TEXT NOT NULL, price TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_reconciliation_runs (
                run_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, source TEXT NOT NULL,
                occurred_at TEXT NOT NULL, complete INTEGER NOT NULL, discrepancies_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_reconciled_accounts (
                evidence_id TEXT PRIMARY KEY, reconciliation_id TEXT NOT NULL UNIQUE REFERENCES paper_reconciliation_runs(run_id),
                account_id TEXT NOT NULL, source TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                healthy INTEGER NOT NULL, ingested_at TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def create(self, order: PaperOrder) -> PaperOrder:
        if order.status is not OrderStatus.PROPOSED:
            raise PaperOmsError("new_order_must_be_proposed")
        try:
            existing = self.get(order.intent.intent_id)
        except PaperOmsError:
            existing = None
        if existing is not None:
            if existing.intent != order.intent:
                raise PaperOmsError("idempotency_key_payload_conflict")
            return existing
        intent = order.intent
        self._connection.execute(
            "INSERT INTO paper_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(intent.intent_id), str(intent.signal_id), intent.instrument_id, intent.account_id,
                intent.side.value, str(intent.quantity), str(intent.limit_price), intent.created_at.isoformat(),
                order.status.value, str(order.filled_quantity), None,
            ),
        )
        self._append_event(intent.intent_id, "ORDER_CREATED", {"status": order.status.value})
        self._connection.commit()
        return order

    def get(self, intent_id: UUID) -> PaperOrder:
        row = self._connection.execute("SELECT * FROM paper_orders WHERE intent_id = ?", (str(intent_id),)).fetchone()
        if row is None:
            raise PaperOmsError("unknown_order_intent")
        intent = OrderIntent(UUID(row[0]), UUID(row[1]), row[2], row[3], OrderSide(row[4]), Decimal(row[5]), Decimal(row[6]), datetime.fromisoformat(row[7]))
        return PaperOrder(intent, OrderStatus(row[8]), Decimal(row[9]), None if row[10] is None else Decimal(row[10]))

    def transition(self, intent_id: UUID, next_status: OrderStatus, *, actor: str = "system") -> PaperOrder:
        order = self.get(intent_id)
        try:
            updated = order.transition(next_status)
        except ValueError as error:
            raise PaperOmsError("invalid_order_transition") from error
        self._write_order(updated)
        self._append_event(intent_id, "ORDER_STATUS_CHANGED", {"from": order.status.value, "to": next_status.value, "actor": actor})
        self._connection.commit()
        return updated

    def ingest_fill(self, external_fill_id: str, intent_id: UUID, quantity: Decimal, price: Decimal, *, occurred_at: datetime | None = None) -> PaperOrder:
        if not external_fill_id.strip() or quantity <= 0 or price <= 0:
            raise PaperOmsError("invalid_fill")
        occurred = occurred_at or utc_now()
        if occurred.tzinfo is None:
            raise PaperOmsError("fill_timestamp_must_be_timezone_aware")
        existing = self._connection.execute("SELECT intent_id, quantity, price FROM paper_fills WHERE external_fill_id = ?", (external_fill_id,)).fetchone()
        if existing is not None:
            if existing != (str(intent_id), str(quantity), str(price)):
                raise PaperOmsError("external_fill_id_conflict")
            return self.get(intent_id)
        order = self.get(intent_id)
        try:
            updated = order.fill(quantity, price)
        except ValueError as error:
            raise PaperOmsError("fill_rejected_by_order_state") from error
        fill_id = uuid4()
        self._connection.execute(
            "INSERT INTO paper_fills (fill_id, external_fill_id, intent_id, occurred_at, quantity, price) VALUES (?, ?, ?, ?, ?, ?)",
            (str(fill_id), external_fill_id, str(intent_id), occurred.isoformat(), str(quantity), str(price)),
        )
        self._write_order(updated)
        self._append_event(intent_id, "FILL_INGESTED", {"external_fill_id": external_fill_id, "quantity": str(quantity), "price": str(price), "status": updated.status.value})
        self._connection.commit()
        return updated

    def cancel(self, intent_id: UUID, *, actor: str = "operator") -> PaperOrder:
        pending = self.transition(intent_id, OrderStatus.CANCEL_PENDING, actor=actor)
        return self.transition(pending.intent.intent_id, OrderStatus.CANCELLED, actor=actor)

    def amend(self, intent_id: UUID, *, quantity: Decimal, limit_price: Decimal, actor: str = "operator") -> PaperOrder:
        """Persist a broker-confirmed paper amendment without changing the immutable intent ID."""
        order = self.get(intent_id)
        if order.status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED} or quantity < order.filled_quantity or limit_price <= 0:
            raise PaperOmsError("invalid_order_amendment")
        amended = replace(order, intent=replace(order.intent, quantity=quantity, limit_price=limit_price))
        self._write_order(amended)
        self._append_event(intent_id, "ORDER_AMENDED", {"previous_quantity": str(order.intent.quantity), "quantity": str(quantity), "previous_limit_price": str(order.intent.limit_price), "limit_price": str(limit_price), "actor": actor})
        self._connection.commit()
        return amended

    def events(self, intent_id: UUID) -> list[PaperOmsEvent]:
        rows = self._connection.execute(
            "SELECT sequence, event_id, intent_id, event_type, occurred_at, payload_json FROM paper_order_events WHERE intent_id = ? ORDER BY sequence",
            (str(intent_id),),
        ).fetchall()
        return [PaperOmsEvent(row[0], UUID(row[1]), UUID(row[2]), row[3], datetime.fromisoformat(row[4]), json.loads(row[5])) for row in rows]

    def fills(self, intent_id: UUID) -> list[RecordedFill]:
        rows = self._connection.execute(
            "SELECT fill_id, external_fill_id, intent_id, occurred_at, quantity, price FROM paper_fills WHERE intent_id = ? ORDER BY occurred_at, sequence",
            (str(intent_id),),
        ).fetchall()
        return [RecordedFill(UUID(row[0]), row[1], UUID(row[2]), datetime.fromisoformat(row[3]), Decimal(row[4]), Decimal(row[5])) for row in rows]

    def record_reconciliation(self, account_id: str, source: str, result: ReconciliationResult, *, occurred_at: datetime | None = None) -> RecordedReconciliation:
        if not account_id.strip() or not source.strip():
            raise PaperOmsError("reconciliation_requires_account_and_source")
        occurred = occurred_at or utc_now()
        if occurred.tzinfo is None:
            raise PaperOmsError("reconciliation_timestamp_must_be_timezone_aware")
        record = RecordedReconciliation(uuid4(), account_id, source, occurred, result.complete, result.discrepancies)
        self._connection.execute(
            "INSERT INTO paper_reconciliation_runs VALUES (?, ?, ?, ?, ?, ?)",
            (str(record.run_id), record.account_id, record.source, record.occurred_at.isoformat(), int(record.complete), json.dumps(record.discrepancies)),
        )
        self._connection.commit()
        return record

    def record_reconciliation_with_account(self, snapshot: BrokerAccountSnapshot, result: ReconciliationResult, *, healthy: bool, ingested_at: datetime) -> tuple[RecordedReconciliation, RecordedReconciledAccount | None]:
        """Atomically persist a reconciliation and, only when complete, its broker account snapshot."""
        if not snapshot.account_id.strip() or not snapshot.source.strip() or snapshot.as_of.tzinfo is None or snapshot.as_of.utcoffset() is None or ingested_at.tzinfo is None or ingested_at.utcoffset() is None or ingested_at < snapshot.as_of:
            raise PaperOmsError("invalid_reconciled_account_snapshot")
        record = RecordedReconciliation(uuid4(), snapshot.account_id, snapshot.source, snapshot.as_of, result.complete, result.discrepancies)
        evidence = None
        positions = {key: [str(value.quantity), str(value.average_cost)] for key, value in snapshot.positions.items()}
        payload = json.dumps({"cash_currency": snapshot.cash.currency, "cash_amount": str(snapshot.cash.amount), "buying_power": str(snapshot.buying_power), "positions": positions, "open_intent_ids": [str(value) for value in snapshot.open_intent_ids], "fill_ids": list(snapshot.fill_ids), "realized_pnl": str(snapshot.realized_pnl), "fees": str(snapshot.fees), "as_of": snapshot.as_of.isoformat()}, sort_keys=True)
        try:
            with self._connection:
                self._connection.execute("INSERT INTO paper_reconciliation_runs VALUES (?, ?, ?, ?, ?, ?)", (str(record.run_id), record.account_id, record.source, record.occurred_at.isoformat(), int(record.complete), json.dumps(record.discrepancies)))
                if record.complete:
                    evidence = RecordedReconciledAccount(uuid4(), record.run_id, snapshot, healthy, ingested_at)
                    self._connection.execute("INSERT INTO paper_reconciled_accounts VALUES (?, ?, ?, ?, ?, ?, ?)", (str(evidence.evidence_id), str(record.run_id), snapshot.account_id, snapshot.source, payload, int(healthy), ingested_at.isoformat()))
        except sqlite3.IntegrityError as error:
            raise PaperOmsError("reconciled_account_transaction_failed") from error
        return record, evidence

    def latest_reconciled_account(self, account_id: str, decision_at: datetime) -> RecordedReconciledAccount | None:
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise PaperOmsError("reconciled_account_decision_time_must_be_timezone_aware")
        row = self._connection.execute("SELECT evidence_id, reconciliation_id, account_id, source, snapshot_json, healthy, ingested_at FROM paper_reconciled_accounts WHERE account_id = ? AND ingested_at <= ? AND json_extract(snapshot_json, '$.as_of') <= ? ORDER BY json_extract(snapshot_json, '$.as_of') DESC, ingested_at DESC, rowid DESC LIMIT 1", (account_id, decision_at.isoformat(), decision_at.isoformat())).fetchone()
        if row is None:
            return None
        payload = json.loads(row[4])
        positions = {instrument_id: Position(instrument_id, Decimal(values[0]), Decimal(values[1])) for instrument_id, values in payload["positions"].items()}
        snapshot = BrokerAccountSnapshot(row[2], row[3], datetime.fromisoformat(payload["as_of"]), CashBalance(payload["cash_currency"], Decimal(payload["cash_amount"])), Decimal(payload["buying_power"]), positions, tuple(UUID(value) for value in payload["open_intent_ids"]), tuple(payload["fill_ids"]), Decimal(payload["realized_pnl"]), Decimal(payload["fees"]))
        return RecordedReconciledAccount(UUID(row[0]), UUID(row[1]), snapshot, bool(row[5]), datetime.fromisoformat(row[6]))

    def latest_reconciliation(self, account_id: str) -> RecordedReconciliation | None:
        row = self._connection.execute(
            "SELECT run_id, account_id, source, occurred_at, complete, discrepancies_json FROM paper_reconciliation_runs WHERE account_id = ? ORDER BY occurred_at DESC, rowid DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return RecordedReconciliation(UUID(row[0]), row[1], row[2], datetime.fromisoformat(row[3]), bool(row[4]), tuple(json.loads(row[5])))

    def reconciliation(self, run_id: UUID) -> RecordedReconciliation:
        row = self._connection.execute(
            "SELECT run_id, account_id, source, occurred_at, complete, discrepancies_json FROM paper_reconciliation_runs WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        if row is None:
            raise PaperOmsError("unknown_reconciliation_run")
        return RecordedReconciliation(UUID(row[0]), row[1], row[2], datetime.fromisoformat(row[3]), bool(row[4]), tuple(json.loads(row[5])))

    def _write_order(self, order: PaperOrder) -> None:
        cursor = self._connection.execute(
            "UPDATE paper_orders SET quantity = ?, limit_price = ?, status = ?, filled_quantity = ?, average_fill_price = ? WHERE intent_id = ?",
            (str(order.intent.quantity), str(order.intent.limit_price), order.status.value, str(order.filled_quantity), None if order.average_fill_price is None else str(order.average_fill_price), str(order.intent.intent_id)),
        )
        if cursor.rowcount != 1:
            raise PaperOmsError("unknown_order_intent")

    def _append_event(self, intent_id: UUID, event_type: str, payload: dict[str, str]) -> None:
        self._connection.execute(
            "INSERT INTO paper_order_events (event_id, intent_id, event_type, occurred_at, payload_json) VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), str(intent_id), event_type, utc_now().isoformat(), json.dumps(payload, sort_keys=True)),
        )

    def close(self) -> None:
        self._connection.close()
