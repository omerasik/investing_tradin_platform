"""Auditable, non-executing AI research workflow records.

This is intentionally a ledger and validation layer, not a model client. Any
provider/model invocation must submit its structured output through this contract;
the resulting evidence can never submit or alter an order.
"""

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from .data_providers import HttpResponse, ProviderConfigurationError, RetryPolicy


class AgentResearchError(ValueError):
    pass


class ResearchAgentRole(StrEnum):
    TECHNICAL = "TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    MACRO = "MACRO"
    NEWS = "NEWS"
    SENTIMENT = "SENTIMENT"
    BULL = "BULL"
    BEAR = "BEAR"
    RISK_CHALLENGE = "RISK_CHALLENGE"
    PORTFOLIO_REVIEW = "PORTFOLIO_REVIEW"
    FINAL_SYNTHESIS = "FINAL_SYNTHESIS"


_REQUIRED_PRE_SYNTHESIS_ROLES = frozenset(item for item in ResearchAgentRole if item is not ResearchAgentRole.FINAL_SYNTHESIS)


@dataclass(frozen=True, slots=True)
class AgentResearchOutput:
    output_id: UUID
    workflow_id: UUID
    instrument_id: str
    role: ResearchAgentRole
    created_at: datetime
    facts: tuple[str, ...]
    inferences: tuple[str, ...]
    source_references: tuple[str, ...]
    confidence: float
    missing_data: tuple[str, ...]
    contradicts_output_ids: tuple[UUID, ...]
    prompt_version: str
    model_version: str

    def __post_init__(self) -> None:
        if (not self.instrument_id or not self.facts or not self.inferences or not self.source_references
                or not 0 <= self.confidence <= 1 or not self.prompt_version or not self.model_version
                or self.created_at.tzinfo is None or self.created_at.utcoffset() is None
                or any(not value.strip() for values in (self.facts, self.inferences, self.source_references, self.missing_data) for value in values)
                or len(set(self.contradicts_output_ids)) != len(self.contradicts_output_ids)):
            raise AgentResearchError("invalid_agent_research_output")


@dataclass(frozen=True, slots=True)
class AgentResearchRequest:
    workflow_id: UUID
    instrument_id: str
    role: ResearchAgentRole
    prompt_version: str
    allowed_source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.instrument_id or not self.prompt_version or not self.allowed_source_references or any(not value.strip() for value in self.allowed_source_references):
            raise AgentResearchError("invalid_agent_research_request")


class StructuredResearchAgent(Protocol):
    provider_name: str
    model_version: str
    def generate(self, request: AgentResearchRequest) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class ResearchModelConfiguration:
    provider_name: str
    base_url: str
    model_version: str
    secret_reference: str
    terms_accepted: bool = False
    request_timeout_seconds: float = 20.0

    def validate(self) -> None:
        if (not self.provider_name or not self.base_url.startswith("https://") or not self.model_version
                or not self.secret_reference or self.request_timeout_seconds <= 0):
            raise ProviderConfigurationError("invalid_research_model_configuration")


class ResearchModelTransport(Protocol):
    def post(self, url: str, payload: dict[str, object], timeout_seconds: float, secret_reference: str) -> HttpResponse: ...


class ConfiguredHttpsResearchAgent:
    """Credential-reference-only structured model transport.

    The deployment transport resolves the secret reference and may add its own
    authorization header. This adapter passes no tools, endpoints, order IDs, or
    broker/risk context to the model.
    """
    def __init__(self, configuration: ResearchModelConfiguration, transport: ResearchModelTransport, *, endpoint_path: str = "/v1/research/structured", retry_policy: RetryPolicy = RetryPolicy(), sleep=time.sleep) -> None:
        configuration.validate(); retry_policy.validate()
        if not endpoint_path.startswith("/") or "?" in endpoint_path: raise ProviderConfigurationError("invalid_research_model_endpoint")
        self.provider_name, self.model_version = configuration.provider_name, configuration.model_version
        self._config, self._transport, self._endpoint_path, self._retry_policy, self._sleep = configuration, transport, endpoint_path, retry_policy, sleep

    def generate(self, request: AgentResearchRequest) -> dict[str, object]:
        if not self._config.terms_accepted: raise ProviderConfigurationError("research_model_terms_not_accepted")
        body = {"instrument_id": request.instrument_id, "role": request.role.value, "prompt_version": request.prompt_version, "allowed_source_references": list(request.allowed_source_references), "response_schema": "agent-research-v1"}
        status = "network"
        for attempt in range(self._retry_policy.maximum_attempts):
            response = self._transport.post(f"{self._config.base_url.rstrip('/')}{self._endpoint_path}", body, self._config.request_timeout_seconds, self._config.secret_reference); status = str(response.status_code)
            if response.status_code == 200:
                try:
                    result = json.loads(response.body)
                except json.JSONDecodeError as error:
                    raise AgentResearchError("invalid_research_model_json") from error
                if not isinstance(result, dict): raise AgentResearchError("invalid_research_model_json")
                return result
            if response.status_code not in {429, 500, 502, 503, 504} or attempt + 1 == self._retry_policy.maximum_attempts: break
            delay = response.headers.get("Retry-After")
            self._sleep(float(delay) if delay and delay.isdigit() else self._retry_policy.base_delay.total_seconds() * (2 ** attempt))
        raise AgentResearchError(f"research_model_http_status:{status}")


@dataclass(frozen=True, slots=True)
class AgentResearchReview:
    review_id: UUID
    workflow_id: UUID
    synthesis_output_id: UUID
    reviewer: str
    approved: bool
    rationale: str
    reviewed_at: datetime

    def __post_init__(self) -> None:
        if not self.reviewer or not self.rationale or self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise AgentResearchError("invalid_agent_research_review")


@dataclass(frozen=True, slots=True)
class AgentResearchSafetyAssessment:
    """Versioned, non-executing quality decision for one model output."""

    assessment_id: UUID
    workflow_id: UUID
    output_id: UUID
    evaluator: str
    policy_version: str
    approved: bool
    reasons: tuple[str, ...]
    assessed_at: datetime

    def __post_init__(self) -> None:
        if (not self.evaluator or not self.policy_version or not self.reasons
                or any(not item.strip() for item in self.reasons)
                or self.assessed_at.tzinfo is None or self.assessed_at.utcoffset() is None):
            raise AgentResearchError("invalid_agent_research_safety_assessment")


_OPERATIONAL_INSTRUCTION = re.compile(
    r"\b(?:place|submit|cancel|replace|execute)\s+(?:an?\s+)?order\b",
    re.IGNORECASE,
)


def assess_agent_research_output(
    output: AgentResearchOutput,
    *,
    evaluator: str,
    policy_version: str,
    assessed_at: datetime,
) -> AgentResearchSafetyAssessment:
    """Conservatively block text that attempts to direct an order action.

    This narrow deterministic check is not a truth or investment-suitability
    judgment. It only prevents a research response from being operator-ready
    when it contains a direct OMS-like instruction.
    """
    evidence = (*output.facts, *output.inferences)
    findings = tuple(
        "direct_operational_instruction_detected"
        for item in evidence
        if _OPERATIONAL_INSTRUCTION.search(item)
    )
    return AgentResearchSafetyAssessment(
        uuid4(), output.workflow_id, output.output_id, evaluator, policy_version,
        not findings,
        findings or ("no_direct_operational_instruction_detected",),
        assessed_at,
    )


@dataclass(frozen=True, slots=True)
class AgentResearchQuery:
    query_id: UUID
    workflow_id: UUID
    actor: str
    query_text: str
    intent: str
    requested_at: datetime

    def __post_init__(self) -> None:
        if not self.actor or not self.query_text or len(self.query_text) > 240 or self.intent not in {"facts", "inferences", "missing_data", "contradictions", "reviews"} or self.requested_at.tzinfo is None or self.requested_at.utcoffset() is None:
            raise AgentResearchError("invalid_agent_research_query")


def run_structured_research_agent(agent: StructuredResearchAgent, request: AgentResearchRequest, *, created_at: datetime) -> AgentResearchOutput:
    """Validate an untrusted model response against explicit source and schema boundaries."""
    if created_at.tzinfo is None or created_at.utcoffset() is None or not agent.provider_name or not agent.model_version:
        raise AgentResearchError("invalid_structured_agent_configuration")
    try:
        response = agent.generate(request)
        facts = tuple(str(item) for item in response["facts"]); inferences = tuple(str(item) for item in response["inferences"])
        sources = tuple(str(item) for item in response["source_references"]); confidence = float(response["confidence"])
        missing = tuple(str(item) for item in response.get("missing_data", ())); contradictions = tuple(UUID(str(item)) for item in response.get("contradicts_output_ids", ()))
    except (KeyError, TypeError, ValueError) as error:
        raise AgentResearchError("invalid_structured_agent_response") from error
    if not set(sources).issubset(set(request.allowed_source_references)):
        raise AgentResearchError("agent_response_uses_unapproved_source")
    return AgentResearchOutput(uuid4(), request.workflow_id, request.instrument_id, request.role, created_at, facts, inferences, sources, confidence, missing, contradictions, request.prompt_version, f"{agent.provider_name}:{agent.model_version}")


class SQLiteAgentResearchStore:
    """Append-only structured agent research, isolated from all execution services."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS agent_research_outputs (
            output_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, instrument_id TEXT NOT NULL, role TEXT NOT NULL,
            created_at TEXT NOT NULL, facts_json TEXT NOT NULL, inferences_json TEXT NOT NULL, sources_json TEXT NOT NULL,
            confidence REAL NOT NULL, missing_data_json TEXT NOT NULL, contradicts_json TEXT NOT NULL,
            prompt_version TEXT NOT NULL, model_version TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS agent_research_reviews (
            review_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, synthesis_output_id TEXT NOT NULL,
            reviewer TEXT NOT NULL, approved INTEGER NOT NULL, rationale TEXT NOT NULL, reviewed_at TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS agent_research_safety_assessments (
            assessment_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, output_id TEXT NOT NULL,
            evaluator TEXT NOT NULL, policy_version TEXT NOT NULL, approved INTEGER NOT NULL,
            reasons_json TEXT NOT NULL, assessed_at TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS agent_research_queries (
            query_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, actor TEXT NOT NULL, query_text TEXT NOT NULL,
            intent TEXT NOT NULL, requested_at TEXT NOT NULL)""")
        self._connection.commit()

    def append(self, output: AgentResearchOutput) -> None:
        if output.role is ResearchAgentRole.FINAL_SYNTHESIS:
            existing = self.outputs_for_workflow(output.workflow_id)
            if {item.role for item in existing} != _REQUIRED_PRE_SYNTHESIS_ROLES:
                raise AgentResearchError("agent_research_synthesis_requires_all_roles")
        for prior_id in output.contradicts_output_ids:
            prior = self.get(prior_id)
            if prior.workflow_id != output.workflow_id:
                raise AgentResearchError("agent_research_contradiction_outside_workflow")
        try:
            self._connection.execute("INSERT INTO agent_research_outputs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(output.output_id), str(output.workflow_id), output.instrument_id, output.role.value, output.created_at.isoformat(), json.dumps(output.facts), json.dumps(output.inferences), json.dumps(output.source_references), output.confidence, json.dumps(output.missing_data), json.dumps([str(item) for item in output.contradicts_output_ids]), output.prompt_version, output.model_version)); self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise AgentResearchError("duplicate_agent_research_output") from error

    def get(self, output_id: UUID) -> AgentResearchOutput:
        row = self._connection.execute("SELECT * FROM agent_research_outputs WHERE output_id = ?", (str(output_id),)).fetchone()
        if row is None: raise KeyError(str(output_id))
        return self._from_row(row)

    def outputs_for_workflow(self, workflow_id: UUID) -> tuple[AgentResearchOutput, ...]:
        rows = self._connection.execute("SELECT * FROM agent_research_outputs WHERE workflow_id = ? ORDER BY created_at, output_id", (str(workflow_id),)).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def append_review(self, review: AgentResearchReview) -> None:
        synthesis = self.get(review.synthesis_output_id)
        if synthesis.workflow_id != review.workflow_id or synthesis.role is not ResearchAgentRole.FINAL_SYNTHESIS:
            raise AgentResearchError("agent_research_review_requires_final_synthesis")
        try:
            self._connection.execute("INSERT INTO agent_research_reviews VALUES (?, ?, ?, ?, ?, ?, ?)", (str(review.review_id), str(review.workflow_id), str(review.synthesis_output_id), review.reviewer, int(review.approved), review.rationale, review.reviewed_at.isoformat())); self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise AgentResearchError("duplicate_agent_research_review") from error

    def reviews_for_workflow(self, workflow_id: UUID) -> tuple[AgentResearchReview, ...]:
        rows = self._connection.execute("SELECT * FROM agent_research_reviews WHERE workflow_id = ? ORDER BY reviewed_at, review_id", (str(workflow_id),)).fetchall()
        return tuple(AgentResearchReview(UUID(row[0]), UUID(row[1]), UUID(row[2]), row[3], bool(row[4]), row[5], datetime.fromisoformat(row[6])) for row in rows)

    def append_safety_assessment(self, assessment: AgentResearchSafetyAssessment) -> None:
        output = self.get(assessment.output_id)
        if output.workflow_id != assessment.workflow_id:
            raise AgentResearchError("agent_research_safety_assessment_outside_workflow")
        try:
            self._connection.execute(
                "INSERT INTO agent_research_safety_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(assessment.assessment_id), str(assessment.workflow_id), str(assessment.output_id),
                 assessment.evaluator, assessment.policy_version, int(assessment.approved),
                 json.dumps(assessment.reasons), assessment.assessed_at.isoformat()),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise AgentResearchError("duplicate_agent_research_safety_assessment") from error

    def safety_assessments_for_workflow(self, workflow_id: UUID) -> tuple[AgentResearchSafetyAssessment, ...]:
        rows = self._connection.execute(
            "SELECT * FROM agent_research_safety_assessments WHERE workflow_id = ? ORDER BY assessed_at, assessment_id",
            (str(workflow_id),),
        ).fetchall()
        return tuple(
            AgentResearchSafetyAssessment(
                UUID(row[0]), UUID(row[1]), UUID(row[2]), row[3], row[4], bool(row[5]),
                tuple(json.loads(row[6])), datetime.fromisoformat(row[7]),
            )
            for row in rows
        )

    def operator_ready_outputs(self, workflow_id: UUID) -> tuple[AgentResearchOutput, ...]:
        """Return research only after all outputs pass safety and synthesis review."""
        outputs = self.outputs_for_workflow(workflow_id)
        if {item.role for item in outputs} != frozenset(ResearchAgentRole):
            raise AgentResearchError("agent_research_operator_view_requires_all_roles")
        latest_assessment = {
            item.output_id: item
            for item in self.safety_assessments_for_workflow(workflow_id)
        }
        if any(item.output_id not in latest_assessment or not latest_assessment[item.output_id].approved for item in outputs):
            raise AgentResearchError("agent_research_operator_view_requires_safe_outputs")
        final = next(item for item in outputs if item.role is ResearchAgentRole.FINAL_SYNTHESIS)
        reviews = [item for item in self.reviews_for_workflow(workflow_id) if item.synthesis_output_id == final.output_id]
        if not reviews or not reviews[-1].approved:
            raise AgentResearchError("agent_research_operator_view_requires_approved_review")
        return outputs

    def query_workflow(self, workflow_id: UUID, query_text: str, *, actor: str, requested_at: datetime) -> tuple[AgentResearchQuery, tuple[dict[str, object], ...]]:
        intent, role = _parse_research_query(query_text)
        query = AgentResearchQuery(uuid4(), workflow_id, actor, query_text.strip(), intent, requested_at)
        self._connection.execute("INSERT INTO agent_research_queries VALUES (?, ?, ?, ?, ?, ?)", (str(query.query_id), str(query.workflow_id), query.actor, query.query_text, query.intent, query.requested_at.isoformat())); self._connection.commit()
        if intent == "reviews":
            return query, tuple({"review_id": str(item.review_id), "approved": item.approved, "reviewer": item.reviewer, "rationale": item.rationale} for item in self.reviews_for_workflow(workflow_id))
        outputs = self.outputs_for_workflow(workflow_id)
        if role is not None: outputs = tuple(item for item in outputs if item.role is role)
        field = {"facts": "facts", "inferences": "inferences", "missing_data": "missing_data", "contradictions": "contradicts_output_ids"}[intent]
        return query, tuple({"output_id": str(item.output_id), "role": item.role.value, "value": [str(value) for value in getattr(item, field)]} for item in outputs)

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> AgentResearchOutput:
        return AgentResearchOutput(UUID(row[0]), UUID(row[1]), row[2], ResearchAgentRole(row[3]), datetime.fromisoformat(row[4]), tuple(json.loads(row[5])), tuple(json.loads(row[6])), tuple(json.loads(row[7])), float(row[8]), tuple(json.loads(row[9])), tuple(UUID(item) for item in json.loads(row[10])), row[11], row[12])

    def close(self) -> None:
        self._connection.close()


def _parse_research_query(query_text: str) -> tuple[str, ResearchAgentRole | None]:
    value = query_text.strip().casefold()
    if not value or len(value) > 240: raise AgentResearchError("invalid_agent_research_query")
    intent = next((key for key, tokens in {"facts": ("fact",), "inferences": ("inference",), "missing_data": ("missing",), "contradictions": ("contradict",), "reviews": ("review", "approval")}.items() if any(token in value for token in tokens)), None)
    if intent is None: raise AgentResearchError("unsupported_agent_research_query")
    role = next((role for role in ResearchAgentRole if role.value.replace("_", " ").casefold() in value), None)
    return intent, role
