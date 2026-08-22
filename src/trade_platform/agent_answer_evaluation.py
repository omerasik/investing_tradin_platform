"""Deterministic quality evidence for retrieval-bound, non-executing agent output."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from uuid import UUID, uuid5

from .agent_research import AgentResearchOutput
from .persistence import PostgresDatabase
from .research_retrieval import ResearchRetrievalReport, RetrievalOutcome


class AgentAnswerEvaluationError(ValueError):
    pass


class ClaimKind(StrEnum):
    FACT = "FACT"
    INFERENCE = "INFERENCE"


class AgentAnswerOutcome(StrEnum):
    BLOCKED = "BLOCKED"
    REVIEW_ELIGIBLE = "REVIEW_ELIGIBLE"


_NS = UUID("2fb6de68-8aa0-4345-9696-9f4aec775851")
_TOKEN = re.compile(r"[a-z0-9]+")
_CAUSAL = re.compile(
    r"\b(?:because|caused by|driven by|due to|results? from)\b", re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AgentAnswerEvaluationPolicy:
    policy_id: UUID
    version: str
    minimum_claim_support_rate: Decimal
    minimum_claim_token_overlap: Decimal
    minimum_citation_utilization: Decimal
    minimum_distinct_sources: int
    maximum_confidence: Decimal
    require_missing_data_when_retrieval_incomplete: bool
    approved_by: str
    approved_at: datetime
    enabled: bool
    content_hash: str

    @classmethod
    def create(
        cls, policy_id: UUID, version: str, minimum_claim_support_rate: Decimal,
        minimum_claim_token_overlap: Decimal, minimum_citation_utilization: Decimal,
        minimum_distinct_sources: int, maximum_confidence: Decimal,
        require_missing_data_when_retrieval_incomplete: bool, approved_by: str,
        approved_at: datetime, *, enabled: bool = True,
    ) -> AgentAnswerEvaluationPolicy:
        values = {
            "policy_id": str(policy_id), "version": version,
            "minimum_claim_support_rate": _decimal_text(minimum_claim_support_rate),
            "minimum_claim_token_overlap": _decimal_text(minimum_claim_token_overlap),
            "minimum_citation_utilization": _decimal_text(minimum_citation_utilization),
            "minimum_distinct_sources": minimum_distinct_sources,
            "maximum_confidence": _decimal_text(maximum_confidence),
            "require_missing_data_when_retrieval_incomplete": (
                require_missing_data_when_retrieval_incomplete
            ),
            "approved_by": approved_by, "approved_at": approved_at.isoformat(),
            "enabled": enabled,
        }
        return cls(
            policy_id, version, minimum_claim_support_rate, minimum_claim_token_overlap,
            minimum_citation_utilization, minimum_distinct_sources, maximum_confidence,
            require_missing_data_when_retrieval_incomplete, approved_by, approved_at,
            enabled, _hash(values),
        )

    def __post_init__(self) -> None:
        decimals = (
            self.minimum_claim_support_rate, self.minimum_claim_token_overlap,
            self.minimum_citation_utilization, self.maximum_confidence,
        )
        if (
            not self.version.strip() or any(value < 0 or value > 1 for value in decimals)
            or any(value != _quantize(value, 12) for value in decimals)
            or self.minimum_claim_support_rate <= 0 or self.minimum_claim_token_overlap <= 0
            or self.minimum_citation_utilization <= 0 or self.maximum_confidence <= 0
            or self.minimum_distinct_sources < 1 or not self.approved_by.strip()
            or not _aware(self.approved_at)
            or self.content_hash != _policy_hash(self)
        ):
            raise AgentAnswerEvaluationError("invalid_agent_answer_evaluation_policy")


@dataclass(frozen=True, slots=True)
class ClaimEvidenceBinding:
    claim_kind: ClaimKind
    claim_index: int
    source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.claim_index < 0 or not self.source_references or any(
            not item.strip() for item in self.source_references
        ) or len(set(self.source_references)) != len(self.source_references):
            raise AgentAnswerEvaluationError("invalid_claim_evidence_binding")


@dataclass(frozen=True, slots=True)
class ClaimEvaluation:
    claim_kind: ClaimKind
    claim_index: int
    claim_text: str
    source_references: tuple[str, ...]
    token_overlap: Decimal
    supported: bool


@dataclass(frozen=True, slots=True)
class AgentAnswerEvaluationReport:
    report_id: UUID
    policy_id: UUID
    policy_content_hash: str
    retrieval_report_id: UUID
    output_id: UUID
    evaluated_at: datetime
    claim_evaluations: tuple[ClaimEvaluation, ...]
    metrics: dict[str, Decimal]
    outcome: AgentAnswerOutcome
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    content_hash: str


def evaluate_agent_answer(
    policy: AgentAnswerEvaluationPolicy,
    retrieval: ResearchRetrievalReport,
    output: AgentResearchOutput,
    bindings: tuple[ClaimEvidenceBinding, ...],
    *,
    evaluated_at: datetime,
) -> AgentAnswerEvaluationReport:
    if not policy.enabled or policy.approved_at > output.created_at:
        raise AgentAnswerEvaluationError("agent_answer_policy_not_approved_at_output_time")
    if policy.content_hash != _policy_hash(policy):
        raise AgentAnswerEvaluationError("agent_answer_policy_hash_mismatch")
    if retrieval.outcome is not RetrievalOutcome.COMPLETE:
        raise AgentAnswerEvaluationError("agent_answer_requires_complete_retrieval")
    request = retrieval.request
    if (
        output.workflow_id != request.workflow_id or output.instrument_id != request.instrument_id
        or output.role is not request.role or output.created_at < request.requested_at
        or not _aware(evaluated_at) or evaluated_at < output.created_at
    ):
        raise AgentAnswerEvaluationError("agent_answer_retrieval_context_mismatch")
    allowed = set(retrieval.allowed_source_references)
    if not set(output.source_references).issubset(allowed):
        raise AgentAnswerEvaluationError("agent_answer_uses_unretrieved_source")
    claims = {
        **{(ClaimKind.FACT, index): value for index, value in enumerate(output.facts)},
        **{(ClaimKind.INFERENCE, index): value for index, value in enumerate(output.inferences)},
    }
    binding_map = {(item.claim_kind, item.claim_index): item for item in bindings}
    if len(binding_map) != len(bindings) or set(binding_map) != set(claims):
        raise AgentAnswerEvaluationError("agent_answer_requires_exact_claim_bindings")
    excerpts = {
        f"retrieval:{retrieval.report_id}:{item.chunk_id}": item.excerpt
        for item in retrieval.results
    }
    evaluations: list[ClaimEvaluation] = []
    for key, claim in claims.items():
        binding = binding_map[key]
        if not set(binding.source_references).issubset(allowed):
            raise AgentAnswerEvaluationError("claim_binding_uses_unretrieved_source")
        claim_tokens = _tokens(claim)
        evidence_tokens = set().union(*(_tokens(excerpts[item]) for item in binding.source_references))
        overlap = _ratio(len(claim_tokens.intersection(evidence_tokens)), len(claim_tokens), 12)
        evaluations.append(ClaimEvaluation(
            key[0], key[1], claim, binding.source_references, overlap,
            overlap >= policy.minimum_claim_token_overlap and not _CAUSAL.search(claim),
        ))
    used_sources = set().union(*(set(item.source_references) for item in evaluations))
    support_rate = _ratio(sum(item.supported for item in evaluations), len(evaluations), 12)
    citation_utilization = _ratio(len(used_sources), len(allowed), 12)
    metrics = {
        "claim_support_rate": support_rate,
        "citation_utilization": citation_utilization,
        "distinct_sources": Decimal(len(used_sources)),
        "declared_confidence": _quantize(Decimal(str(output.confidence)), 12),
        "retrieval_coverage": retrieval.query_term_coverage,
    }
    reasons: list[str] = []
    if support_rate < policy.minimum_claim_support_rate:
        reasons.append("claim_support_rate_below_policy")
    if citation_utilization < policy.minimum_citation_utilization:
        reasons.append("citation_utilization_below_policy")
    if len(used_sources) < policy.minimum_distinct_sources:
        reasons.append("distinct_sources_below_policy")
    confidence_ceiling = min(policy.maximum_confidence, retrieval.query_term_coverage)
    if metrics["declared_confidence"] > confidence_ceiling:
        reasons.append("confidence_exceeds_retrieval_evidence")
    if (
        policy.require_missing_data_when_retrieval_incomplete
        and retrieval.query_term_coverage < Decimal("1") and not output.missing_data
    ):
        reasons.append("incomplete_retrieval_requires_missing_data_declaration")
    if any(_CAUSAL.search(claim) for claim in claims.values()):
        reasons.append("unsupported_causal_language")
    outcome = AgentAnswerOutcome.BLOCKED if reasons else AgentAnswerOutcome.REVIEW_ELIGIBLE
    limitations = (
        "lexical_support_is_not_semantic_truth_or_causal_verification",
        "fixture_evaluation_is_not_external_model_quality_acceptance",
        "evaluation_has_no_model_tool_signal_order_risk_or_approval_authority",
    )
    report_id = uuid5(_NS, f"{policy.content_hash}:{retrieval.report_id}:{output.output_id}")
    draft = AgentAnswerEvaluationReport(
        report_id, policy.policy_id, policy.content_hash, retrieval.report_id,
        output.output_id, evaluated_at,
        tuple(evaluations), metrics, outcome, tuple(reasons) or ("policy_thresholds_met",),
        limitations, "",
    )
    return replace(draft, content_hash=_report_hash(draft))


class PostgresAgentAnswerEvaluationStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append_policy(self, policy: AgentAnswerEvaluationPolicy) -> None:
        with self._database.transaction() as connection:
            row = connection.execute(
                "INSERT INTO agent_answer_evaluation_policy_versions VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (policy_id) "
                "DO NOTHING RETURNING content_hash",
                (policy.policy_id, policy.version, policy.minimum_claim_support_rate,
                 policy.minimum_claim_token_overlap, policy.minimum_citation_utilization,
                 policy.minimum_distinct_sources, policy.maximum_confidence,
                 policy.require_missing_data_when_retrieval_incomplete, policy.approved_by,
                 policy.approved_at, policy.enabled, policy.content_hash),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT content_hash FROM agent_answer_evaluation_policy_versions WHERE policy_id=%s",
                    (policy.policy_id,),
                ).fetchone()
                if row is None or str(row[0]) != policy.content_hash:
                    raise AgentAnswerEvaluationError("conflicting_agent_answer_policy")

    def append_report(self, report: AgentAnswerEvaluationReport) -> None:
        if report.content_hash != _report_hash(report):
            raise AgentAnswerEvaluationError("agent_answer_report_hash_mismatch")
        with self._database.transaction() as connection:
            policy = connection.execute(
                "SELECT content_hash FROM agent_answer_evaluation_policy_versions WHERE policy_id=%s",
                (report.policy_id,),
            ).fetchone()
            if policy is None or str(policy[0]) != report.policy_content_hash:
                raise AgentAnswerEvaluationError("agent_answer_policy_not_registered_or_mismatched")
            row = connection.execute(
                "INSERT INTO agent_answer_evaluation_reports VALUES "
                "(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s) "
                "ON CONFLICT (report_id) DO NOTHING RETURNING content_hash",
                (report.report_id, report.policy_id, report.policy_content_hash,
                 report.retrieval_report_id, report.output_id, report.evaluated_at,
                 json.dumps(_decimal_json(report.metrics)), report.outcome.value,
                 json.dumps(report.reasons), json.dumps(report.limitations), report.content_hash),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT content_hash FROM agent_answer_evaluation_reports WHERE report_id=%s",
                    (report.report_id,),
                ).fetchone()
                if row is None or str(row[0]) != report.content_hash:
                    raise AgentAnswerEvaluationError("conflicting_agent_answer_report")
                return
            for item in report.claim_evaluations:
                connection.execute(
                    "INSERT INTO agent_answer_claim_evaluations VALUES "
                    "(%s,%s,%s,%s,%s::jsonb,%s,%s)",
                    (report.report_id, item.claim_kind.value, item.claim_index, item.claim_text,
                     json.dumps(item.source_references), item.token_overlap, item.supported),
                )

    def report(self, report_id: UUID) -> AgentAnswerEvaluationReport:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM agent_answer_evaluation_reports WHERE report_id=%s", (report_id,),
            ).fetchone()
            claims = connection.execute(
                "SELECT claim_kind,claim_index,claim_text,source_references,token_overlap,supported "
                "FROM agent_answer_claim_evaluations WHERE report_id=%s "
                "ORDER BY claim_kind,claim_index", (report_id,),
            ).fetchall()
        if row is None:
            raise KeyError(str(report_id))
        report = AgentAnswerEvaluationReport(
            row[0], row[1], row[2], row[3], row[4], row[5],
            tuple(ClaimEvaluation(
                ClaimKind(item[0]), item[1], item[2], tuple(item[3]), item[4], item[5],
            ) for item in claims),
            {key: Decimal(value) for key, value in row[6].items()},
            AgentAnswerOutcome(row[7]), tuple(row[8]), tuple(row[9]), row[10],
        )
        if report.content_hash != _report_hash(report):
            raise AgentAnswerEvaluationError("agent_answer_report_hash_mismatch")
        return report


def _policy_hash(policy: AgentAnswerEvaluationPolicy) -> str:
    return _hash({
        "policy_id": str(policy.policy_id), "version": policy.version,
        "minimum_claim_support_rate": _decimal_text(policy.minimum_claim_support_rate),
        "minimum_claim_token_overlap": _decimal_text(policy.minimum_claim_token_overlap),
        "minimum_citation_utilization": _decimal_text(policy.minimum_citation_utilization),
        "minimum_distinct_sources": policy.minimum_distinct_sources,
        "maximum_confidence": _decimal_text(policy.maximum_confidence),
        "require_missing_data_when_retrieval_incomplete": policy.require_missing_data_when_retrieval_incomplete,
        "approved_by": policy.approved_by, "approved_at": policy.approved_at.isoformat(),
        "enabled": policy.enabled,
    })


def _report_hash(report: AgentAnswerEvaluationReport) -> str:
    return _hash({
        "report_id": str(report.report_id), "policy_id": str(report.policy_id),
        "policy_content_hash": report.policy_content_hash,
        "retrieval_report_id": str(report.retrieval_report_id), "output_id": str(report.output_id),
        "evaluated_at": report.evaluated_at.isoformat(),
        "claims": [{"kind": item.claim_kind.value, "index": item.claim_index,
                    "text": item.claim_text, "sources": item.source_references,
                    "overlap": _decimal_text(item.token_overlap), "supported": item.supported}
                   for item in report.claim_evaluations],
        "metrics": _decimal_json(report.metrics), "outcome": report.outcome.value,
        "reasons": report.reasons, "limitations": report.limitations,
    })


def _tokens(value: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(value.casefold()))


def _quantize(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def _ratio(numerator: int, denominator: int, places: int) -> Decimal:
    return _quantize(Decimal(numerator) / Decimal(denominator), places)


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"


def _decimal_json(values: dict[str, Decimal]) -> dict[str, str]:
    return {key: _decimal_text(value) for key, value in values.items()}


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
