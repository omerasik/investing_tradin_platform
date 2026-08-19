"""Provider-neutral paper execution-quality and shadow-rehearsal evidence.

The only evidence class currently accepted is deterministic simulated paper.
It must not be presented as broker-sandbox, live-market, or activation evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, cast
from uuid import UUID, uuid4

from .domain import OrderSide, OrderStatus, utc_now
from .operational_alerts import AlertSeverity, PostgresOperationalAlertStore
from .paper_execution import PaperOrder
from .paper_oms import RecordedFill
from .persistence import PersistenceError, PostgresDatabase
from .shadow_mode import ShadowComparison


class PaperOperationsEvidenceError(ValueError):
    """Raised when paper evidence is incomplete, misleading, or conflicting."""


class PaperEvidenceClass(str, Enum):
    SIMULATED_PAPER_REFERENCE = "SIMULATED_PAPER_REFERENCE"


@dataclass(frozen=True, slots=True)
class ExecutionQualityPolicy:
    version: str
    minimum_fill_ratio: Decimal
    maximum_adverse_arrival_slippage_fraction: Decimal
    maximum_adverse_decision_slippage_fraction: Decimal
    maximum_completion_latency_ms: int

    def validate(self) -> None:
        if (
            not self.version.strip()
            or not Decimal(0) <= self.minimum_fill_ratio <= Decimal(1)
            or self.maximum_adverse_arrival_slippage_fraction < 0
            or self.maximum_adverse_decision_slippage_fraction < 0
            or self.maximum_completion_latency_ms < 0
        ):
            raise PaperOperationsEvidenceError("invalid_execution_quality_policy")


@dataclass(frozen=True, slots=True)
class ExecutionQualityEvidence:
    evidence_id: UUID
    intent_id: UUID
    policy_version: str
    policy_parameters: dict[str, str]
    evidence_class: PaperEvidenceClass
    reference_source: str
    arrival_price: Decimal
    decision_price: Decimal
    requested_quantity: Decimal
    filled_quantity: Decimal
    fill_ratio: Decimal
    vwap: Decimal | None
    adverse_arrival_slippage_fraction: Decimal | None
    adverse_decision_slippage_fraction: Decimal | None
    realized_shortfall_fraction: Decimal | None
    first_fill_latency_ms: int | None
    completion_latency_ms: int | None
    final_status: OrderStatus
    passed: bool
    breach_reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    fill_ids: tuple[UUID, ...]
    evaluated_at: datetime
    content_hash: str


@dataclass(frozen=True, slots=True)
class ShadowRehearsalEvidence:
    evidence_id: UUID
    campaign_reference: str
    primary_intent_id: UUID
    shadow_intent_id: UUID
    evidence_class: PaperEvidenceClass
    matched: bool
    differences: tuple[str, ...]
    requires_incident: bool
    limitations: tuple[str, ...]
    compared_at: datetime
    content_hash: str


def evaluate_execution_quality(
    *,
    order: PaperOrder,
    fills: tuple[RecordedFill, ...],
    arrival_price: Decimal,
    decision_price: Decimal,
    reference_source: str,
    policy: ExecutionQualityPolicy,
    evaluated_at: datetime | None = None,
) -> ExecutionQualityEvidence:
    """Evaluate terminal paper fills without claiming real-market measurement."""
    policy.validate()
    when = evaluated_at or utc_now()
    terminal = {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }
    if (
        order.status not in terminal
        or arrival_price <= 0
        or decision_price <= 0
        or not reference_source.strip()
        or when.tzinfo is None
        or when.utcoffset() is None
    ):
        raise PaperOperationsEvidenceError("invalid_execution_quality_input")
    numeric_inputs = (
        arrival_price,
        decision_price,
        order.intent.quantity,
        *(fill.quantity for fill in fills),
        *(fill.price for fill in fills),
    )
    if any(value != _q12(value) for value in numeric_inputs):
        raise PaperOperationsEvidenceError("execution_evidence_exceeds_numeric_scale")
    if any(
        fill.intent_id != order.intent.intent_id
        or fill.quantity <= 0
        or fill.price <= 0
        or fill.occurred_at.tzinfo is None
        or fill.occurred_at.utcoffset() is None
        or fill.occurred_at < order.intent.created_at
        for fill in fills
    ):
        raise PaperOperationsEvidenceError("invalid_execution_fill_evidence")
    if len({fill.fill_id for fill in fills}) != len(fills):
        raise PaperOperationsEvidenceError("duplicate_execution_fill_evidence")
    if when < order.intent.created_at or any(fill.occurred_at > when for fill in fills):
        raise PaperOperationsEvidenceError("execution_evaluation_precedes_evidence")

    filled_quantity = sum((fill.quantity for fill in fills), Decimal(0))
    if filled_quantity != order.filled_quantity or filled_quantity > order.intent.quantity:
        raise PaperOperationsEvidenceError("execution_fill_quantity_mismatch")
    vwap = (
        None
        if not fills
        else _q12(
            sum((fill.quantity * fill.price for fill in fills), Decimal(0))
            / filled_quantity
        )
    )
    if (order.average_fill_price is None) != (vwap is None) or (
        order.average_fill_price is not None
        and vwap is not None
        and _q12(order.average_fill_price) != vwap
    ):
        raise PaperOperationsEvidenceError("execution_fill_vwap_mismatch")
    direction = Decimal(1) if order.intent.side is OrderSide.BUY else Decimal(-1)
    arrival_slippage = (
        None
        if vwap is None
        else _q12(direction * (vwap - arrival_price) / arrival_price)
    )
    decision_slippage = (
        None
        if vwap is None
        else _q12(direction * (vwap - decision_price) / decision_price)
    )
    fill_ratio = _q12(filled_quantity / order.intent.quantity)
    realized_shortfall = (
        None if decision_slippage is None else _q12(decision_slippage * fill_ratio)
    )
    first_latency = (
        None
        if not fills
        else _milliseconds(min(fill.occurred_at for fill in fills) - order.intent.created_at)
    )
    completion_latency = (
        None
        if order.status is not OrderStatus.FILLED or not fills
        else _milliseconds(max(fill.occurred_at for fill in fills) - order.intent.created_at)
    )
    breaches: list[str] = []
    if fill_ratio < policy.minimum_fill_ratio:
        breaches.append("minimum_fill_ratio")
    if arrival_slippage is None:
        breaches.append("arrival_slippage_unavailable")
    elif arrival_slippage > policy.maximum_adverse_arrival_slippage_fraction:
        breaches.append("maximum_adverse_arrival_slippage")
    if decision_slippage is None:
        breaches.append("decision_slippage_unavailable")
    elif decision_slippage > policy.maximum_adverse_decision_slippage_fraction:
        breaches.append("maximum_adverse_decision_slippage")
    if completion_latency is None:
        breaches.append("completion_latency_unavailable")
    elif completion_latency > policy.maximum_completion_latency_ms:
        breaches.append("maximum_completion_latency")

    limitations = (
        "SIMULATED_PAPER_ONLY_NOT_BROKER_SANDBOX_OR_LIVE_MARKET_EVIDENCE",
        "LATENCY_MEASURED_FROM_INTENT_CREATION_NOT_VENUE_ACKNOWLEDGEMENT",
        "REALIZED_SHORTFALL_EXCLUDES_UNFILLED_OPPORTUNITY_COST_AND_FEES",
    )
    policy_parameters = {
        "minimum_fill_ratio": str(policy.minimum_fill_ratio),
        "maximum_adverse_arrival_slippage_fraction": str(
            policy.maximum_adverse_arrival_slippage_fraction
        ),
        "maximum_adverse_decision_slippage_fraction": str(
            policy.maximum_adverse_decision_slippage_fraction
        ),
        "maximum_completion_latency_ms": str(policy.maximum_completion_latency_ms),
    }
    canonical = {
        "intent_id": str(order.intent.intent_id),
        "policy_version": policy.version,
        "policy_parameters": policy_parameters,
        "evidence_class": PaperEvidenceClass.SIMULATED_PAPER_REFERENCE.value,
        "reference_source": reference_source,
        "arrival_price": _decimal_text(arrival_price),
        "decision_price": _decimal_text(decision_price),
        "requested_quantity": _decimal_text(order.intent.quantity),
        "filled_quantity": _decimal_text(filled_quantity),
        "fill_ratio": _decimal_text(fill_ratio),
        "vwap": None if vwap is None else _decimal_text(vwap),
        "adverse_arrival_slippage_fraction": (
            None if arrival_slippage is None else _decimal_text(arrival_slippage)
        ),
        "adverse_decision_slippage_fraction": (
            None if decision_slippage is None else _decimal_text(decision_slippage)
        ),
        "realized_shortfall_fraction": (
            None if realized_shortfall is None else _decimal_text(realized_shortfall)
        ),
        "first_fill_latency_ms": first_latency,
        "completion_latency_ms": completion_latency,
        "final_status": order.status.value,
        "passed": not breaches,
        "breach_reasons": breaches,
        "limitations": limitations,
        "fill_ids": [str(fill.fill_id) for fill in fills],
        "evaluated_at": when.isoformat(),
    }
    content_hash = _content_hash(canonical)
    return ExecutionQualityEvidence(
        uuid4(),
        order.intent.intent_id,
        policy.version,
        policy_parameters,
        PaperEvidenceClass.SIMULATED_PAPER_REFERENCE,
        reference_source,
        arrival_price,
        decision_price,
        order.intent.quantity,
        filled_quantity,
        fill_ratio,
        vwap,
        arrival_slippage,
        decision_slippage,
        realized_shortfall,
        first_latency,
        completion_latency,
        order.status,
        not breaches,
        tuple(breaches),
        limitations,
        tuple(fill.fill_id for fill in fills),
        when,
        content_hash,
    )


def build_shadow_rehearsal_evidence(
    comparison: ShadowComparison,
    *,
    campaign_reference: str,
) -> ShadowRehearsalEvidence:
    if (
        not campaign_reference.strip()
        or comparison.primary_intent_id == comparison.shadow_intent_id
        or comparison.compared_at.tzinfo is None
        or comparison.compared_at.utcoffset() is None
    ):
        raise PaperOperationsEvidenceError("invalid_shadow_rehearsal")
    limitations = (
        "SIMULATED_PAPER_REHEARSAL_NOT_OPERATIONAL_SHADOW_MODE",
        "NO_LIVE_DATA_OR_BROKER_ORDER_COMPARISON",
        "DOES_NOT_SATISFY_SHADOW_OR_LIVE_ACTIVATION_GATES",
    )
    canonical = {
        "campaign_reference": campaign_reference,
        "primary_intent_id": str(comparison.primary_intent_id),
        "shadow_intent_id": str(comparison.shadow_intent_id),
        "evidence_class": PaperEvidenceClass.SIMULATED_PAPER_REFERENCE.value,
        "matched": comparison.matched,
        "differences": comparison.differences,
        "requires_incident": not comparison.matched,
        "limitations": limitations,
        "compared_at": comparison.compared_at.isoformat(),
    }
    return ShadowRehearsalEvidence(
        uuid4(),
        campaign_reference,
        comparison.primary_intent_id,
        comparison.shadow_intent_id,
        PaperEvidenceClass.SIMULATED_PAPER_REFERENCE,
        comparison.matched,
        comparison.differences,
        not comparison.matched,
        limitations,
        comparison.compared_at,
        _content_hash(canonical),
    )


class PostgresPaperOperationsEvidenceStore:
    """Immutable PostgreSQL authority for simulated paper operations evidence."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        alert_store: PostgresOperationalAlertStore | None = None,
    ) -> None:
        self._database = database
        self._alerts = alert_store

    def append_execution_quality(
        self, evidence: ExecutionQualityEvidence
    ) -> ExecutionQualityEvidence:
        if (
            evidence.evidence_class is not PaperEvidenceClass.SIMULATED_PAPER_REFERENCE
            or _execution_evidence_hash(evidence) != evidence.content_hash
        ):
            raise PaperOperationsEvidenceError("invalid_execution_quality_evidence_hash")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"execution-quality:{evidence.intent_id}:{evidence.policy_version}",),
                )
                existing = self._execution_row(cursor, evidence.intent_id, evidence.policy_version)
                if existing is not None:
                    recovered = self._execution_from_row(existing)
                    if recovered.content_hash != evidence.content_hash:
                        raise PaperOperationsEvidenceError("execution_quality_evidence_conflict")
                    return recovered
                cursor.execute(
                    "INSERT INTO paper_execution_quality_evidence ("
                    "evidence_id,intent_id,policy_version,policy_parameters,evidence_class,reference_source,"
                    "arrival_price,decision_price,requested_quantity,filled_quantity,fill_ratio,"
                    "vwap,adverse_arrival_slippage_fraction,adverse_decision_slippage_fraction,"
                    "realized_shortfall_fraction,first_fill_latency_ms,completion_latency_ms,"
                    "final_status,passed,breach_reasons,limitations,fill_ids,evaluated_at,content_hash) "
                    "VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)",
                    (
                        evidence.evidence_id,
                        evidence.intent_id,
                        evidence.policy_version,
                        json.dumps(evidence.policy_parameters, sort_keys=True),
                        evidence.evidence_class.value,
                        evidence.reference_source,
                        evidence.arrival_price,
                        evidence.decision_price,
                        evidence.requested_quantity,
                        evidence.filled_quantity,
                        evidence.fill_ratio,
                        evidence.vwap,
                        evidence.adverse_arrival_slippage_fraction,
                        evidence.adverse_decision_slippage_fraction,
                        evidence.realized_shortfall_fraction,
                        evidence.first_fill_latency_ms,
                        evidence.completion_latency_ms,
                        evidence.final_status.value,
                        evidence.passed,
                        json.dumps(evidence.breach_reasons),
                        json.dumps(evidence.limitations),
                        json.dumps([str(value) for value in evidence.fill_ids]),
                        evidence.evaluated_at,
                        evidence.content_hash,
                    ),
                )
                if not evidence.passed and self._alerts is not None:
                    self._alerts.raise_alert_in_transaction(
                        connection,
                        source="paper_execution_quality",
                        code="PAPER_EXECUTION_QUALITY_BREACH",
                        severity=AlertSeverity.WARNING,
                        resource=f"intent:{evidence.intent_id}",
                        details={"policy_version": evidence.policy_version, "breaches": ",".join(evidence.breach_reasons), "evidence_class": evidence.evidence_class.value},
                        occurred_at=evidence.evaluated_at,
                    )
                return evidence
        except (PaperOperationsEvidenceError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("paper_execution_quality_persistence_uncertain") from error

    def get_execution_quality(
        self, intent_id: UUID, policy_version: str
    ) -> ExecutionQualityEvidence:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                row = self._execution_row(cursor, intent_id, policy_version)
                if row is None:
                    raise KeyError(f"{intent_id}:{policy_version}")
                return self._execution_from_row(row)
        except (KeyError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("paper_execution_quality_read_uncertain") from error

    def append_shadow_rehearsal(
        self, evidence: ShadowRehearsalEvidence
    ) -> ShadowRehearsalEvidence:
        if (
            evidence.evidence_class is not PaperEvidenceClass.SIMULATED_PAPER_REFERENCE
            or _shadow_evidence_hash(evidence) != evidence.content_hash
        ):
            raise PaperOperationsEvidenceError("invalid_shadow_rehearsal_evidence_hash")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"shadow-rehearsal:{evidence.campaign_reference}:{evidence.primary_intent_id}:{evidence.shadow_intent_id}",),
                )
                cursor.execute(
                    "SELECT evidence_id,campaign_reference,primary_intent_id,shadow_intent_id,"
                    "evidence_class,matched,differences,requires_incident,limitations,compared_at,content_hash "
                    "FROM paper_shadow_rehearsal_evidence WHERE campaign_reference=%s "
                    "AND primary_intent_id=%s AND shadow_intent_id=%s",
                    (evidence.campaign_reference, evidence.primary_intent_id, evidence.shadow_intent_id),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    recovered = self._shadow_from_row(existing)
                    if recovered.content_hash != evidence.content_hash:
                        raise PaperOperationsEvidenceError("shadow_rehearsal_evidence_conflict")
                    return recovered
                cursor.execute(
                    "INSERT INTO paper_shadow_rehearsal_evidence (evidence_id,campaign_reference,"
                    "primary_intent_id,shadow_intent_id,evidence_class,matched,differences,requires_incident,"
                    "limitations,compared_at,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s)",
                    (
                        evidence.evidence_id,
                        evidence.campaign_reference,
                        evidence.primary_intent_id,
                        evidence.shadow_intent_id,
                        evidence.evidence_class.value,
                        evidence.matched,
                        json.dumps(evidence.differences),
                        evidence.requires_incident,
                        json.dumps(evidence.limitations),
                        evidence.compared_at,
                        evidence.content_hash,
                    ),
                )
                if evidence.requires_incident and self._alerts is not None:
                    self._alerts.raise_alert_in_transaction(
                        connection,
                        source="paper_shadow_rehearsal",
                        code="PAPER_SHADOW_DIVERGENCE",
                        severity=AlertSeverity.WARNING,
                        resource=f"campaign:{evidence.campaign_reference}:primary:{evidence.primary_intent_id}",
                        details={"differences": ",".join(evidence.differences), "evidence_class": evidence.evidence_class.value},
                        occurred_at=evidence.compared_at,
                    )
                return evidence
        except (PaperOperationsEvidenceError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("paper_shadow_rehearsal_persistence_uncertain") from error

    def get_shadow_rehearsal(self, evidence_id: UUID) -> ShadowRehearsalEvidence:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT evidence_id,campaign_reference,primary_intent_id,shadow_intent_id,"
                    "evidence_class,matched,differences,requires_incident,limitations,compared_at,content_hash "
                    "FROM paper_shadow_rehearsal_evidence WHERE evidence_id=%s",
                    (evidence_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise KeyError(str(evidence_id))
                return self._shadow_from_row(row)
        except (KeyError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("paper_shadow_rehearsal_read_uncertain") from error

    @staticmethod
    def _execution_row(cursor: Any, intent_id: UUID, policy_version: str) -> Any:
        cursor.execute(
            "SELECT evidence_id,intent_id,policy_version,policy_parameters,evidence_class,reference_source,arrival_price,"
            "decision_price,requested_quantity,filled_quantity,fill_ratio,vwap,"
            "adverse_arrival_slippage_fraction,adverse_decision_slippage_fraction,"
            "realized_shortfall_fraction,first_fill_latency_ms,completion_latency_ms,final_status,"
            "passed,breach_reasons,limitations,fill_ids,evaluated_at,content_hash "
            "FROM paper_execution_quality_evidence WHERE intent_id=%s AND policy_version=%s",
            (intent_id, policy_version),
        )
        return cursor.fetchone()

    @staticmethod
    def _execution_from_row(row: tuple[object, ...]) -> ExecutionQualityEvidence:
        evidence = ExecutionQualityEvidence(
            UUID(str(row[0])), UUID(str(row[1])), str(row[2]), dict(cast(dict[str, str], row[3])),
            PaperEvidenceClass(str(row[4])), str(row[5]), Decimal(str(row[6])), Decimal(str(row[7])),
            Decimal(str(row[8])), Decimal(str(row[9])), Decimal(str(row[10])),
            None if row[11] is None else Decimal(str(row[11])),
            None if row[12] is None else Decimal(str(row[12])),
            None if row[13] is None else Decimal(str(row[13])),
            None if row[14] is None else Decimal(str(row[14])),
            None if row[15] is None else int(str(row[15])),
            None if row[16] is None else int(str(row[16])), OrderStatus(str(row[17])), bool(row[18]),
            tuple(cast(list[str], row[19])), tuple(cast(list[str], row[20])),
            tuple(UUID(value) for value in cast(list[str], row[21])), cast(datetime, row[22]), str(row[23]),
        )
        if _execution_evidence_hash(evidence) != evidence.content_hash:
            raise PaperOperationsEvidenceError("execution_quality_evidence_hash_mismatch")
        return evidence

    @staticmethod
    def _shadow_from_row(row: tuple[object, ...]) -> ShadowRehearsalEvidence:
        evidence = ShadowRehearsalEvidence(
            UUID(str(row[0])), str(row[1]), UUID(str(row[2])), UUID(str(row[3])),
            PaperEvidenceClass(str(row[4])), bool(row[5]), tuple(cast(list[str], row[6])),
            bool(row[7]), tuple(cast(list[str], row[8])), cast(datetime, row[9]), str(row[10]),
        )
        if _shadow_evidence_hash(evidence) != evidence.content_hash:
            raise PaperOperationsEvidenceError("shadow_rehearsal_evidence_hash_mismatch")
        return evidence


def _milliseconds(delta: Any) -> int:
    return int(delta.total_seconds() * 1000)


def _content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _execution_evidence_hash(evidence: ExecutionQualityEvidence) -> str:
    return _content_hash(
        {
            "intent_id": str(evidence.intent_id),
            "policy_version": evidence.policy_version,
            "policy_parameters": evidence.policy_parameters,
            "evidence_class": evidence.evidence_class.value,
            "reference_source": evidence.reference_source,
            "arrival_price": _decimal_text(evidence.arrival_price),
            "decision_price": _decimal_text(evidence.decision_price),
            "requested_quantity": _decimal_text(evidence.requested_quantity),
            "filled_quantity": _decimal_text(evidence.filled_quantity),
            "fill_ratio": _decimal_text(evidence.fill_ratio),
            "vwap": None if evidence.vwap is None else _decimal_text(evidence.vwap),
            "adverse_arrival_slippage_fraction": (
                None
                if evidence.adverse_arrival_slippage_fraction is None
                else _decimal_text(evidence.adverse_arrival_slippage_fraction)
            ),
            "adverse_decision_slippage_fraction": (
                None
                if evidence.adverse_decision_slippage_fraction is None
                else _decimal_text(evidence.adverse_decision_slippage_fraction)
            ),
            "realized_shortfall_fraction": (
                None
                if evidence.realized_shortfall_fraction is None
                else _decimal_text(evidence.realized_shortfall_fraction)
            ),
            "first_fill_latency_ms": evidence.first_fill_latency_ms,
            "completion_latency_ms": evidence.completion_latency_ms,
            "final_status": evidence.final_status.value,
            "passed": evidence.passed,
            "breach_reasons": evidence.breach_reasons,
            "limitations": evidence.limitations,
            "fill_ids": [str(value) for value in evidence.fill_ids],
            "evaluated_at": evidence.evaluated_at.isoformat(),
        }
    )


def _shadow_evidence_hash(evidence: ShadowRehearsalEvidence) -> str:
    return _content_hash(
        {
            "campaign_reference": evidence.campaign_reference,
            "primary_intent_id": str(evidence.primary_intent_id),
            "shadow_intent_id": str(evidence.shadow_intent_id),
            "evidence_class": evidence.evidence_class.value,
            "matched": evidence.matched,
            "differences": evidence.differences,
            "requires_incident": evidence.requires_incident,
            "limitations": evidence.limitations,
            "compared_at": evidence.compared_at.isoformat(),
        }
    )


def _q12(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000000000001"))


def _decimal_text(value: Decimal) -> str:
    return format(_q12(value), "f")
