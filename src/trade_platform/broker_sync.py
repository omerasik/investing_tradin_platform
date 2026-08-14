"""Paper-only orchestration between a broker-neutral adapter, OMS and reconciliation evidence."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from .broker_adapter import (
    BrokerAccountSnapshot,
    BrokerOrderEvent,
    InternalAccountState,
    PaperBrokerAdapter,
    reconcile_broker_account,
)
from .domain import OrderIntent, OrderStatus, RiskDecision, RiskDecisionType, utc_now
from .operational_alerts import AlertSeverity, SQLiteOperationalAlertStore
from .paper_execution import PaperOrder, ReconciliationResult
from .paper_oms import PaperOmsError, SQLitePaperOms
from .portfolio_risk import PortfolioRiskDecision
from .pretrade_assessment import PreTradeAssessment

if TYPE_CHECKING:
    from .policy_registry import SQLitePolicyRegistry
    from .pretrade_assessment import SQLitePreTradeAssessmentStore


class BrokerSyncError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StoredBrokerEvent:
    event_id: UUID
    broker_name: str
    sequence: int
    external_event_id: str
    intent_id: UUID
    event_type: str
    status: OrderStatus
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PreTradeDecision:
    decision_id: UUID
    intent_id: UUID
    approved: bool
    reasons: tuple[str, ...]
    decided_at: datetime


def evaluate_pre_trade(
    intent: OrderIntent, risk_decision: RiskDecision, portfolio_decision: PortfolioRiskDecision
) -> PreTradeDecision:
    if risk_decision.intent_id != intent.intent_id:
        raise BrokerSyncError("risk_decision_intent_mismatch")
    reasons = tuple(f"individual_risk:{reason}" for reason in risk_decision.reasons) + tuple(
        f"portfolio_risk:{reason}" for reason in portfolio_decision.reasons
    )
    return PreTradeDecision(
        uuid4(),
        intent.intent_id,
        risk_decision.decision is RiskDecisionType.APPROVE and portfolio_decision.approved,
        reasons,
        utc_now(),
    )


class SQLitePreTradeDecisionStore:
    """Append-only proof that individual and portfolio gates both passed before paper submission."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS pre_trade_decisions (decision_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, approved INTEGER NOT NULL, reasons_json TEXT NOT NULL, decided_at TEXT NOT NULL)"
        )
        self._connection.commit()

    def append(self, decision: PreTradeDecision) -> None:
        self._connection.execute(
            "INSERT INTO pre_trade_decisions VALUES (?, ?, ?, ?, ?)",
            (
                str(decision.decision_id),
                str(decision.intent_id),
                int(decision.approved),
                json.dumps(decision.reasons),
                decision.decided_at.isoformat(),
            ),
        )
        self._connection.commit()

    def get(self, decision_id: UUID) -> PreTradeDecision:
        row = self._connection.execute(
            "SELECT * FROM pre_trade_decisions WHERE decision_id = ?", (str(decision_id),)
        ).fetchone()
        if row is None:
            raise KeyError(str(decision_id))
        return PreTradeDecision(
            UUID(row[0]),
            UUID(row[1]),
            bool(row[2]),
            tuple(json.loads(row[3])),
            datetime.fromisoformat(row[4]),
        )

    def latest_for_intent(self, intent_id: UUID) -> PreTradeDecision:
        row = self._connection.execute(
            "SELECT * FROM pre_trade_decisions WHERE intent_id = ? ORDER BY decided_at DESC, rowid DESC LIMIT 1",
            (str(intent_id),),
        ).fetchone()
        if row is None:
            raise KeyError(str(intent_id))
        return PreTradeDecision(
            UUID(row[0]),
            UUID(row[1]),
            bool(row[2]),
            tuple(json.loads(row[3])),
            datetime.fromisoformat(row[4]),
        )

    def close(self) -> None:
        self._connection.close()


class SQLiteBrokerEventStore:
    """Durable source cursor and external-event de-duplication for a paper adapter."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS broker_events (event_id TEXT PRIMARY KEY, broker_name TEXT NOT NULL, sequence INTEGER NOT NULL, external_event_id TEXT NOT NULL UNIQUE, intent_id TEXT NOT NULL, event_type TEXT NOT NULL, status TEXT NOT NULL, occurred_at TEXT NOT NULL)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS broker_cursors (broker_name TEXT PRIMARY KEY, sequence INTEGER NOT NULL)"
        )
        self._connection.commit()

    def last_sequence(self, broker_name: str) -> int:
        row = self._connection.execute(
            "SELECT sequence FROM broker_cursors WHERE broker_name = ?", (broker_name,)
        ).fetchone()
        return 0 if row is None else row[0]

    def append(self, broker_name: str, event: BrokerOrderEvent) -> bool:
        if event.sequence <= 0 or not event.external_event_id.strip():
            raise BrokerSyncError("invalid_broker_event")
        try:
            self._connection.execute(
                "INSERT INTO broker_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(event.event_id),
                    broker_name,
                    event.sequence,
                    event.external_event_id,
                    str(event.client_intent_id),
                    event.event_type,
                    event.status.value,
                    event.occurred_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError:
            return False
        previous = self.last_sequence(broker_name)
        if event.sequence > previous:
            self._connection.execute(
                "INSERT INTO broker_cursors VALUES (?, ?) ON CONFLICT(broker_name) DO UPDATE SET sequence = excluded.sequence",
                (broker_name, event.sequence),
            )
        self._connection.commit()
        return True

    def close(self) -> None:
        self._connection.close()


class PaperBrokerSyncService:
    """Coordinates paper intent submission only after independent risk approval; no network transport exists here."""

    def __init__(
        self,
        oms: SQLitePaperOms | Any,
        adapter: PaperBrokerAdapter,
        event_store: SQLiteBrokerEventStore | Any,
        alerts: SQLiteOperationalAlertStore | None = None,
        pre_trade_store: SQLitePreTradeDecisionStore | None = None,
        assessment_store: "SQLitePreTradeAssessmentStore | None" = None,
        policy_registry: "SQLitePolicyRegistry | None" = None,
    ) -> None:
        if adapter.capabilities.network_connected:
            raise BrokerSyncError("network_connected_adapter_not_permitted")
        self._oms, self._adapter, self._events, self._alerts, self._pre_trade_store = (
            oms,
            adapter,
            event_store,
            alerts,
            pre_trade_store,
        )
        self._assessment_store, self._policy_registry = assessment_store, policy_registry

    def _submit_checked_assessment(
        self,
        intent: OrderIntent,
        risk_decision: RiskDecision,
        portfolio_decision: PortfolioRiskDecision,
    ) -> PaperOrder:
        decision = evaluate_pre_trade(intent, risk_decision, portfolio_decision)
        if self._pre_trade_store is not None:
            self._pre_trade_store.append(decision)
        try:
            order = self._oms.create(PaperOrder(intent))
        except PaperOmsError as error:
            raise BrokerSyncError("unable_to_create_oms_order") from error
        if order.status is not OrderStatus.PROPOSED:
            raise BrokerSyncError("duplicate_or_non_proposed_oms_order")
        pending = self._oms.transition(intent.intent_id, OrderStatus.RISK_PENDING, actor="risk")
        if not decision.approved:
            return self._oms.transition(
                pending.intent.intent_id, OrderStatus.RISK_REJECTED, actor="pre_trade"
            )
        approved = self._oms.transition(
            pending.intent.intent_id, OrderStatus.APPROVED, actor="risk"
        )
        try:
            acknowledged = self._adapter.submit(approved)
        except Exception as error:
            self._oms.transition(
                intent.intent_id, OrderStatus.SUBMISSION_PENDING, actor="broker_sync"
            )
            self._oms.transition(intent.intent_id, OrderStatus.REJECTED, actor="broker_sync")
            raise BrokerSyncError("paper_broker_submission_failed") from error
        for status in (
            OrderStatus.SUBMISSION_PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.ACKNOWLEDGED,
        ):
            self._oms.transition(intent.intent_id, status, actor="broker_sync")
        if acknowledged.status is not OrderStatus.ACKNOWLEDGED:
            raise BrokerSyncError("unexpected_paper_broker_status")
        self.sync_events()
        return self._oms.get(intent.intent_id)

    def submit_after_assessment(
        self, intent: OrderIntent, assessment: PreTradeAssessment
    ) -> PaperOrder:
        """Paper-only handoff from the checked pre-trade coordinator to the existing combined gate."""
        if self._assessment_store is None or self._policy_registry is None:
            raise BrokerSyncError("durable_assessment_and_policy_evidence_required")
        if not self._assessment_store.keyed_integrity_enabled:
            raise BrokerSyncError("keyed_assessment_integrity_required")
        try:
            stored = self._assessment_store.get_for_intent(intent.intent_id)
        except ValueError as error:
            raise BrokerSyncError("durable_assessment_evidence_invalid") from error
        if stored is None:
            raise BrokerSyncError("durable_assessment_evidence_unavailable")
        if stored.assessment_id != assessment.assessment_id:
            raise BrokerSyncError("assessment_evidence_mismatch")
        if stored.input_evidence is None:
            raise BrokerSyncError("pretrade_input_evidence_unavailable")
        try:
            risk_document = self._policy_registry.get("risk", stored.risk_policy_version)
            portfolio_document = self._policy_registry.get(
                "portfolio", stored.portfolio_policy_version
            )
        except ValueError as error:
            raise BrokerSyncError("policy_evidence_unavailable") from error
        if (
            stored.risk_policy_digest != risk_document.digest
            or stored.portfolio_policy_digest != portfolio_document.digest
        ):
            raise BrokerSyncError("policy_evidence_mismatch")
        assessment = stored
        if assessment.risk_decision.intent_id != intent.intent_id:
            raise BrokerSyncError("assessment_intent_mismatch")
        try:
            existing = self._oms.get(intent.intent_id)
        except PaperOmsError:
            existing = None
        if existing is not None:
            if existing.intent != intent:
                raise BrokerSyncError("assessment_intent_payload_conflict")
            return existing
        portfolio_decision = assessment.portfolio_decision
        if portfolio_decision is None:
            portfolio_decision = PortfolioRiskDecision(
                False,
                ("assessment_incomplete",),
                Decimal(0),
                Decimal(0),
                Decimal(0),
                None,
                None,
                None,
            )
        return self._submit_checked_assessment(intent, assessment.risk_decision, portfolio_decision)

    def sync_events(self) -> tuple[StoredBrokerEvent, ...]:
        broker_name = self._adapter.configuration.broker_name
        stored: list[StoredBrokerEvent] = []
        for event in self._adapter.order_events(
            after_sequence=self._events.last_sequence(broker_name)
        ):
            # PostgreSQL OMS owns the broker event, cursor and lifecycle update
            # in one transaction.  Never fall back to independently committed
            # event/OMS writes when that authoritative path is available.
            apply_atomic = getattr(self._oms, "apply_broker_event", None)
            if apply_atomic is not None:
                try:
                    account_id = self._oms.get(event.client_intent_id).intent.account_id
                    applied = apply_atomic(account_id, broker_name, event)
                except (KeyError, ValueError, PaperOmsError) as error:
                    raise BrokerSyncError("broker_event_atomic_persistence_failed") from error
                if applied:
                    stored.append(
                        StoredBrokerEvent(
                            event.event_id,
                            broker_name,
                            event.sequence,
                            event.external_event_id,
                            event.client_intent_id,
                            event.event_type,
                            event.status,
                            event.occurred_at,
                        )
                    )
                continue
            if not self._events.append(broker_name, event):
                continue
            if event.event_type == "FILL":
                try:
                    self._oms.ingest_fill(
                        event.details["external_fill_id"],
                        event.client_intent_id,
                        Decimal(event.details["quantity"]),
                        Decimal(event.details["price"]),
                        occurred_at=event.occurred_at,
                    )
                except (KeyError, ValueError, PaperOmsError) as error:
                    raise BrokerSyncError("broker_fill_event_rejected") from error
            elif event.event_type == "ORDER_CANCELLED":
                order = self._oms.get(event.client_intent_id)
                if order.status in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED}:
                    self._oms.cancel(event.client_intent_id, actor="broker_sync")
            elif event.event_type == "ORDER_REPLACED":
                try:
                    self._oms.amend(
                        event.client_intent_id,
                        quantity=Decimal(event.details["quantity"]),
                        limit_price=Decimal(event.details["limit_price"]),
                        actor="broker_sync",
                    )
                except (KeyError, ValueError, PaperOmsError) as error:
                    raise BrokerSyncError("broker_replace_event_rejected") from error
            stored.append(
                StoredBrokerEvent(
                    event.event_id,
                    broker_name,
                    event.sequence,
                    event.external_event_id,
                    event.client_intent_id,
                    event.event_type,
                    event.status,
                    event.occurred_at,
                )
            )
        return tuple(stored)

    def reconcile(self, internal: InternalAccountState) -> ReconciliationResult:
        snapshot: BrokerAccountSnapshot = self._adapter.sync_account()
        result = reconcile_broker_account(internal, snapshot)
        recorded = ReconciliationResult(result.complete, result.discrepancies)
        ingested_at = utc_now()
        ingested_at = max(ingested_at, snapshot.as_of)
        self._oms.record_reconciliation_with_account(
            snapshot, recorded, healthy=self._adapter.health(), ingested_at=ingested_at
        )
        if not recorded.complete and self._alerts is not None:
            self._alerts.raise_alert(
                source="broker_sync",
                code="RECONCILIATION_FAILED",
                severity=AlertSeverity.CRITICAL,
                resource=f"account:{snapshot.account_id}",
                details={"discrepancies": ",".join(recorded.discrepancies)},
            )
        return recorded
