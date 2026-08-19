"""Immutable PostgreSQL policy and keyed pre-trade assessment authorities."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from .domain import RiskDecision, RiskDecisionType
from .persistence import PostgresDatabase
from .policy_registry import PolicyDocument, PolicyRegistryError, SQLitePolicyRegistry
from .portfolio_risk import PortfolioRiskDecision, StressResult
from .pretrade_assessment import PreTradeAssessment, PreTradeInputEvidence
from .risk import PerTradeRiskEvidence


class PostgresPolicyRegistry:
    """PostgreSQL policy documents with inherited, typed policy decoding."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append(self, document: PolicyDocument) -> None:
        if (
            document.policy_type not in {"risk", "portfolio"}
            or not document.version.strip()
            or not document.approved_by.strip()
            or not document.payload
            or document.approved_at.tzinfo is None
            or document.approved_at.utcoffset() is None
        ):
            raise PolicyRegistryError("invalid_policy_document")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT payload, content_hash, approved_by, approved_at "
                    "FROM runtime_policy_documents WHERE policy_type = %s AND version = %s",
                    (document.policy_type, document.version),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    if (
                        existing[0] == document.payload
                        and str(existing[1]) == document.digest
                        and str(existing[2]) == document.approved_by
                        and existing[3] == document.approved_at
                    ):
                        return
                    raise PolicyRegistryError("duplicate_policy_version_conflict")
                cursor.execute(
                    "INSERT INTO runtime_policy_documents "
                    "(policy_type, version, payload, content_hash, approved_by, approved_at) "
                    "VALUES (%s,%s,%s::jsonb,%s,%s,%s)",
                    (
                        document.policy_type,
                        document.version,
                        json.dumps(document.payload, sort_keys=True),
                        document.digest,
                        document.approved_by,
                        document.approved_at,
                    ),
                )
        except PolicyRegistryError:
            raise
        except Exception as error:
            raise PolicyRegistryError("postgres_policy_persistence_failed") from error

    def get(self, policy_type: str, version: str) -> PolicyDocument:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT policy_type, version, payload, content_hash, approved_by, approved_at "
                    "FROM runtime_policy_documents WHERE policy_type = %s AND version = %s",
                    (policy_type, version),
                )
                row = cursor.fetchone()
            if row is None:
                raise PolicyRegistryError("unknown_policy_version")
            document = PolicyDocument(str(row[0]), str(row[1]), dict(row[2]), str(row[4]), row[5])
            if document.digest != str(row[3]):
                raise PolicyRegistryError("policy_document_digest_mismatch")
            return document
        except PolicyRegistryError:
            raise
        except Exception as error:
            raise PolicyRegistryError("postgres_policy_read_failed") from error

    def resolve_risk_policy(self, version: str):
        # Reuse the backend-neutral decoder; this class does not inherit or
        # initialize the SQLite repository.
        return SQLitePolicyRegistry.resolve_risk_policy(self, version)  # type: ignore[arg-type]

    def resolve_portfolio_policy(self, version: str):
        return SQLitePolicyRegistry.resolve_portfolio_policy(self, version)  # type: ignore[arg-type]

    def resolve_portfolio_scenarios(self, version: str):
        return SQLitePolicyRegistry.resolve_portfolio_scenarios(self, version)  # type: ignore[arg-type]

    def close(self) -> None:
        return None


def _assessment_payload(assessment: PreTradeAssessment) -> dict[str, object]:
    portfolio = assessment.portfolio_decision
    return {
        "assessment_id": str(assessment.assessment_id),
        "risk_decision": {
            "decision_id": str(assessment.risk_decision.decision_id),
            "intent_id": str(assessment.risk_decision.intent_id),
            "decision": assessment.risk_decision.decision.value,
            "reasons": list(assessment.risk_decision.reasons),
        },
        "portfolio_decision": None
        if portfolio is None
        else {
            "approved": portfolio.approved,
            "reasons": list(portfolio.reasons),
            "gross": str(portfolio.gross),
            "net": str(portfolio.net),
            "stress_loss": str(portfolio.stress_loss),
            "var_loss": None if portfolio.var_loss is None else str(portfolio.var_loss),
            "cvar_loss": None if portfolio.cvar_loss is None else str(portfolio.cvar_loss),
            "drawdown_loss": None
            if portfolio.drawdown_loss is None
            else str(portfolio.drawdown_loss),
            "stress_results": [
                {
                    "scenario": result.scenario,
                    "loss": str(result.loss),
                    "contributions": {
                        name: str(value) for name, value in sorted(result.contributions.items())
                    },
                }
                for result in portfolio.stress_results
            ],
        },
        "evidence_block_reason": assessment.evidence_block_reason,
        "assessed_at": assessment.assessed_at.isoformat(),
        "risk_policy_version": assessment.risk_policy_version,
        "portfolio_policy_version": assessment.portfolio_policy_version,
        "risk_policy_digest": assessment.risk_policy_digest,
        "portfolio_policy_digest": assessment.portfolio_policy_digest,
        "input_evidence": None
        if assessment.input_evidence is None
        else assessment.input_evidence.to_payload(),
        "per_trade_risk": None
        if assessment.per_trade_risk is None
        else assessment.per_trade_risk.to_payload(),
    }


def _assessment_from_payload(payload: dict[str, object]) -> PreTradeAssessment:
    try:
        risk_value = payload["risk_decision"]
        if not isinstance(risk_value, dict):
            raise TypeError("invalid_risk_decision")
        risk_payload = risk_value
        portfolio_payload = payload.get("portfolio_decision")
        portfolio = None
        if isinstance(portfolio_payload, dict):
            stress_results = tuple(
                StressResult(
                    str(item["scenario"]),
                    Decimal(str(item["loss"])),
                    {
                        str(name): Decimal(str(value))
                        for name, value in dict(item["contributions"]).items()
                    },
                )
                for item in portfolio_payload.get("stress_results", [])
            )
            portfolio = PortfolioRiskDecision(
                bool(portfolio_payload["approved"]),
                tuple(str(value) for value in portfolio_payload["reasons"]),
                Decimal(str(portfolio_payload["gross"])),
                Decimal(str(portfolio_payload["net"])),
                Decimal(str(portfolio_payload["stress_loss"])),
                None
                if portfolio_payload.get("var_loss") is None
                else Decimal(str(portfolio_payload["var_loss"])),
                None
                if portfolio_payload.get("cvar_loss") is None
                else Decimal(str(portfolio_payload["cvar_loss"])),
                None
                if portfolio_payload.get("drawdown_loss") is None
                else Decimal(str(portfolio_payload["drawdown_loss"])),
                stress_results,
            )
        assessed_at = datetime.fromisoformat(str(payload["assessed_at"]))
        risk = RiskDecision(
            UUID(str(risk_payload["decision_id"])),
            UUID(str(risk_payload["intent_id"])),
            RiskDecisionType(str(risk_payload["decision"])),
            tuple(str(value) for value in risk_payload["reasons"]),
            assessed_at,
        )
        input_payload = payload.get("input_evidence")
        per_trade_payload = payload.get("per_trade_risk")
        return PreTradeAssessment(
            UUID(str(payload["assessment_id"])),
            risk,
            portfolio,
            None
            if payload.get("evidence_block_reason") is None
            else str(payload["evidence_block_reason"]),
            assessed_at,
            str(payload["risk_policy_version"]),
            str(payload["portfolio_policy_version"]),
            str(payload["risk_policy_digest"]),
            str(payload["portfolio_policy_digest"]),
            None
            if not isinstance(input_payload, dict)
            else PreTradeInputEvidence.from_payload(input_payload),
            None
            if not isinstance(per_trade_payload, dict)
            else PerTradeRiskEvidence.from_payload(per_trade_payload),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise ValueError("invalid_postgres_pretrade_assessment_payload") from error


class PostgresPreTradeAssessmentStore:
    """Intent-idempotent assessments revalidated by digest and keyed HMAC."""

    def __init__(self, database: PostgresDatabase, *, integrity_key: bytes) -> None:
        if not integrity_key:
            raise ValueError("invalid_assessment_integrity_key")
        self._database, self._integrity_key = database, integrity_key

    @property
    def keyed_integrity_enabled(self) -> bool:
        return True

    def _mac(self, digest: str) -> str:
        return hmac.new(self._integrity_key, digest.encode(), hashlib.sha256).hexdigest()

    def append(self, assessment: PreTradeAssessment) -> None:
        payload = _assessment_payload(assessment)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT payload, assessment_digest, assessment_mac "
                    "FROM runtime_pretrade_assessments WHERE intent_id = %s",
                    (assessment.risk_decision.intent_id,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    recovered = self._verify(dict(existing[0]), str(existing[1]), str(existing[2]))
                    if recovered == assessment:
                        return
                    raise ValueError("duplicate_pretrade_assessment_intent_conflict")
                cursor.execute(
                    "INSERT INTO runtime_pretrade_assessments "
                    "(assessment_id, intent_id, assessment_digest, assessment_mac, assessed_at, payload) "
                    "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                    (
                        assessment.assessment_id,
                        assessment.risk_decision.intent_id,
                        assessment.digest,
                        self._mac(assessment.digest),
                        assessment.assessed_at,
                        json.dumps(payload, sort_keys=True),
                    ),
                )
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("postgres_pretrade_assessment_persistence_failed") from error

    def _verify(self, payload: dict[str, object], digest: str, mac: str) -> PreTradeAssessment:
        assessment = _assessment_from_payload(payload)
        if assessment.digest != digest:
            raise ValueError("pretrade_assessment_digest_mismatch")
        if not hmac.compare_digest(self._mac(digest), mac):
            raise ValueError("pretrade_assessment_mac_mismatch")
        return assessment

    def get_for_intent(self, intent_id: UUID) -> PreTradeAssessment | None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT payload, assessment_digest, assessment_mac "
                    "FROM runtime_pretrade_assessments WHERE intent_id = %s",
                    (intent_id,),
                )
                row = cursor.fetchone()
            if row is None:
                return None
            return self._verify(dict(row[0]), str(row[1]), str(row[2]))
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("postgres_pretrade_assessment_read_failed") from error

    def close(self) -> None:
        return None
