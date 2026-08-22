"""Point-in-time internal retrieval evidence for non-executing research agents."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from uuid import UUID, uuid5

from .agent_research import AgentResearchRequest, ResearchAgentRole
from .persistence import PostgresDatabase


class ResearchRetrievalError(ValueError):
    pass


class ResearchSourceKind(StrEnum):
    INTERNAL_FILING = "INTERNAL_FILING"
    INTERNAL_MARKET_DATA = "INTERNAL_MARKET_DATA"
    INTERNAL_RISK = "INTERNAL_RISK"
    INTERNAL_EVENT = "INTERNAL_EVENT"
    INTERNAL_RESEARCH = "INTERNAL_RESEARCH"


class RetrievalOutcome(StrEnum):
    COMPLETE = "COMPLETE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


_NS = UUID("f26926dc-e3f8-4e40-a78c-537885987024")
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ResearchRetrievalPolicy:
    policy_id: UUID
    version: str
    allowed_source_kinds: tuple[ResearchSourceKind, ...]
    minimum_results: int
    minimum_distinct_sources: int
    maximum_results: int
    minimum_query_term_coverage: Decimal
    approved_by: str
    approved_at: datetime
    enabled: bool
    content_hash: str

    @classmethod
    def create(
        cls,
        policy_id: UUID,
        version: str,
        allowed_source_kinds: tuple[ResearchSourceKind, ...],
        minimum_results: int,
        minimum_distinct_sources: int,
        maximum_results: int,
        minimum_query_term_coverage: Decimal,
        approved_by: str,
        approved_at: datetime,
        *,
        enabled: bool = True,
    ) -> ResearchRetrievalPolicy:
        payload = {
            "policy_id": str(policy_id), "version": version,
            "allowed_source_kinds": sorted(item.value for item in allowed_source_kinds),
            "minimum_results": minimum_results,
            "minimum_distinct_sources": minimum_distinct_sources,
            "maximum_results": maximum_results,
            "minimum_query_term_coverage": _decimal_text(minimum_query_term_coverage),
            "approved_by": approved_by, "approved_at": approved_at.isoformat(),
            "enabled": enabled,
        }
        return cls(
            policy_id, version, allowed_source_kinds, minimum_results,
            minimum_distinct_sources, maximum_results, minimum_query_term_coverage,
            approved_by, approved_at, enabled, _hash(payload),
        )

    def __post_init__(self) -> None:
        if (
            not self.version.strip() or not self.allowed_source_kinds
            or len(set(self.allowed_source_kinds)) != len(self.allowed_source_kinds)
            or self.minimum_results < 1
            or not 1 <= self.minimum_distinct_sources <= self.minimum_results
            or self.maximum_results < self.minimum_results
            or self.maximum_results > 100
            or not Decimal("0") < self.minimum_query_term_coverage <= Decimal("1")
            or self.minimum_query_term_coverage != _quantize(self.minimum_query_term_coverage, 12)
            or not self.approved_by.strip() or not _aware(self.approved_at)
            or self.content_hash != _policy_hash(self)
        ):
            raise ResearchRetrievalError("invalid_research_retrieval_policy")


@dataclass(frozen=True, slots=True)
class InternalEvidenceChunk:
    chunk_id: UUID
    source_document_id: str
    source_version: str
    source_kind: ResearchSourceKind
    instrument_id: str
    title: str
    text: str
    observed_at: datetime
    available_at: datetime
    invalidated_at: datetime | None
    allowed_roles: tuple[ResearchAgentRole, ...]
    content_hash: str

    @classmethod
    def create(
        cls,
        chunk_id: UUID,
        source_document_id: str,
        source_version: str,
        source_kind: ResearchSourceKind,
        instrument_id: str,
        title: str,
        text: str,
        observed_at: datetime,
        available_at: datetime,
        invalidated_at: datetime | None,
        allowed_roles: tuple[ResearchAgentRole, ...],
    ) -> InternalEvidenceChunk:
        content_hash = _hash({
            "chunk_id": str(chunk_id), "source_document_id": source_document_id,
            "source_version": source_version, "source_kind": source_kind.value,
            "instrument_id": instrument_id, "title": title, "text": text,
            "observed_at": observed_at.isoformat(), "available_at": available_at.isoformat(),
            "invalidated_at": invalidated_at.isoformat() if invalidated_at else None,
            "allowed_roles": sorted(item.value for item in allowed_roles),
        })
        return cls(
            chunk_id, source_document_id, source_version, source_kind,
            instrument_id, title, text, observed_at, available_at, invalidated_at,
            allowed_roles, content_hash,
        )

    def __post_init__(self) -> None:
        if (
            not all(value.strip() for value in (
                self.source_document_id, self.source_version, self.instrument_id,
                self.title, self.text,
            ))
            or len(set(self.allowed_roles)) != len(self.allowed_roles) or not self.allowed_roles
            or not _aware(self.observed_at) or not _aware(self.available_at)
            or self.observed_at > self.available_at
            or (self.invalidated_at is not None and (
                not _aware(self.invalidated_at) or self.invalidated_at <= self.available_at
            ))
            or self.content_hash != _chunk_hash(self)
        ):
            raise ResearchRetrievalError("invalid_internal_evidence_chunk")


@dataclass(frozen=True, slots=True)
class ResearchRetrievalRequest:
    request_id: UUID
    workflow_id: UUID
    policy_id: UUID
    instrument_id: str
    role: ResearchAgentRole
    query_text: str
    requested_at: datetime

    def __post_init__(self) -> None:
        if (
            not self.instrument_id.strip() or not self.query_text.strip()
            or len(self.query_text) > 500 or not _aware(self.requested_at)
            or not _tokens(self.query_text)
        ):
            raise ResearchRetrievalError("invalid_research_retrieval_request")


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    rank: int
    chunk_id: UUID
    source_document_id: str
    source_version: str
    source_kind: ResearchSourceKind
    title: str
    observed_at: datetime
    available_at: datetime
    lexical_score: Decimal
    matched_terms: tuple[str, ...]
    excerpt: str
    chunk_content_hash: str


@dataclass(frozen=True, slots=True)
class ResearchRetrievalReport:
    report_id: UUID
    request: ResearchRetrievalRequest
    policy_content_hash: str
    query_terms: tuple[str, ...]
    query_term_coverage: Decimal
    results: tuple[RetrievedEvidence, ...]
    outcome: RetrievalOutcome
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    content_hash: str

    @property
    def allowed_source_references(self) -> tuple[str, ...]:
        if self.outcome is not RetrievalOutcome.COMPLETE:
            raise ResearchRetrievalError("insufficient_retrieval_evidence")
        return tuple(f"retrieval:{self.report_id}:{item.chunk_id}" for item in self.results)

    def agent_request(self, prompt_version: str) -> AgentResearchRequest:
        return AgentResearchRequest(
            self.request.workflow_id, self.request.instrument_id, self.request.role,
            prompt_version, self.allowed_source_references,
        )


def retrieve_internal_evidence(
    policy: ResearchRetrievalPolicy,
    request: ResearchRetrievalRequest,
    chunks: tuple[InternalEvidenceChunk, ...],
) -> ResearchRetrievalReport:
    if request.policy_id != policy.policy_id:
        raise ResearchRetrievalError("retrieval_policy_mismatch")
    if not policy.enabled or policy.approved_at > request.requested_at:
        raise ResearchRetrievalError("retrieval_policy_not_approved_at_request_time")
    query_terms = tuple(sorted(_tokens(request.query_text)))
    candidates: list[tuple[Decimal, tuple[str, ...], InternalEvidenceChunk]] = []
    for chunk in chunks:
        if (
            chunk.source_kind not in policy.allowed_source_kinds
            or chunk.instrument_id not in {request.instrument_id, "GLOBAL"}
            or request.role not in chunk.allowed_roles
            or chunk.available_at > request.requested_at
            or (chunk.invalidated_at is not None and chunk.invalidated_at <= request.requested_at)
        ):
            continue
        matched = tuple(sorted(set(query_terms).intersection(_tokens(f"{chunk.title} {chunk.text}"))))
        if matched:
            candidates.append((_ratio(len(matched), len(query_terms), 18), matched, chunk))
    candidates.sort(key=lambda value: (
        -value[0], value[2].available_at, value[2].source_document_id, str(value[2].chunk_id),
    ))
    selected = candidates[: policy.maximum_results]
    matched_all = set().union(*(set(item[1]) for item in selected)) if selected else set()
    coverage = _ratio(len(matched_all), len(query_terms), 12)
    results = tuple(
        RetrievedEvidence(
            rank, chunk.chunk_id, chunk.source_document_id, chunk.source_version,
            chunk.source_kind, chunk.title, chunk.observed_at, chunk.available_at,
            score, matched, chunk.text, chunk.content_hash,
        )
        for rank, (score, matched, chunk) in enumerate(selected, 1)
    )
    reasons: list[str] = []
    if len(results) < policy.minimum_results:
        reasons.append("minimum_results_not_met")
    if len({item.source_document_id for item in results}) < policy.minimum_distinct_sources:
        reasons.append("minimum_distinct_sources_not_met")
    if coverage < policy.minimum_query_term_coverage:
        reasons.append("minimum_query_term_coverage_not_met")
    outcome = RetrievalOutcome.INSUFFICIENT_EVIDENCE if reasons else RetrievalOutcome.COMPLETE
    limitations = (
        "deterministic_lexical_retrieval_not_semantic_truth_verification",
        "internal_fixture_evidence_not_external_source_activation",
        "retrieval_has_no_model_tool_signal_order_risk_or_approval_authority",
    )
    report_id = uuid5(_NS, f"{request.request_id}:{policy.content_hash}")
    draft = ResearchRetrievalReport(
        report_id, request, policy.content_hash, query_terms, coverage, results,
        outcome, tuple(reasons) or ("policy_thresholds_met",), limitations, "",
    )
    return replace(draft, content_hash=_report_hash(draft))


class PostgresResearchRetrievalStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append_policy(self, policy: ResearchRetrievalPolicy) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO research_retrieval_policy_versions VALUES "
                "(%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (policy.policy_id, policy.version, json.dumps([item.value for item in policy.allowed_source_kinds]),
                 policy.minimum_results, policy.minimum_distinct_sources, policy.maximum_results,
                 policy.minimum_query_term_coverage, policy.approved_by, policy.approved_at,
                 policy.enabled, policy.content_hash),
            )
        if self.policy(policy.policy_id) != policy:
            raise ResearchRetrievalError("conflicting_retrieval_policy")

    def policy(self, policy_id: UUID) -> ResearchRetrievalPolicy:
        row = self._database.connection.execute(
            "SELECT * FROM research_retrieval_policy_versions WHERE policy_id=%s", (policy_id,),
        ).fetchone()
        if row is None:
            raise KeyError(str(policy_id))
        return ResearchRetrievalPolicy(
            row[0], row[1], tuple(ResearchSourceKind(item) for item in row[2]),
            row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10],
        )

    def append_chunk(self, chunk: InternalEvidenceChunk) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO internal_research_evidence_chunks VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING",
                (chunk.chunk_id, chunk.source_document_id, chunk.source_version,
                 chunk.source_kind.value, chunk.instrument_id, chunk.title, chunk.text,
                 chunk.observed_at, chunk.available_at, chunk.invalidated_at,
                 json.dumps([item.value for item in chunk.allowed_roles]), chunk.content_hash),
            )
        if self.chunk(chunk.chunk_id) != chunk:
            raise ResearchRetrievalError("conflicting_retrieval_chunk")

    def chunk(self, chunk_id: UUID) -> InternalEvidenceChunk:
        row = self._database.connection.execute(
            "SELECT * FROM internal_research_evidence_chunks WHERE chunk_id=%s", (chunk_id,),
        ).fetchone()
        if row is None:
            raise KeyError(str(chunk_id))
        return InternalEvidenceChunk(
            row[0], row[1], row[2], ResearchSourceKind(row[3]), row[4], row[5], row[6],
            row[7], row[8], row[9], tuple(ResearchAgentRole(item) for item in row[10]), row[11],
        )

    def append_report(self, report: ResearchRetrievalReport) -> None:
        if report.content_hash != _report_hash(report):
            raise ResearchRetrievalError("retrieval_report_content_hash_mismatch")
        if self.policy(report.request.policy_id).content_hash != report.policy_content_hash:
            raise ResearchRetrievalError("retrieval_report_policy_mismatch")
        for result in report.results:
            chunk = self.chunk(result.chunk_id)
            if (
                chunk.content_hash != result.chunk_content_hash
                or chunk.source_document_id != result.source_document_id
                or chunk.source_version != result.source_version
                or chunk.source_kind is not result.source_kind
                or chunk.title != result.title or chunk.text != result.excerpt
                or chunk.observed_at != result.observed_at
                or chunk.available_at != result.available_at
            ):
                raise ResearchRetrievalError("retrieval_report_chunk_mismatch")
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO research_retrieval_reports VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s) "
                "ON CONFLICT DO NOTHING",
                (report.report_id, report.request.request_id, report.request.workflow_id,
                 report.request.policy_id, report.request.instrument_id, report.request.role.value,
                 report.request.query_text, report.request.requested_at, json.dumps(report.query_terms),
                 report.query_term_coverage, report.outcome.value, json.dumps(report.reasons),
                 json.dumps(report.limitations), report.content_hash),
            )
            for item in report.results:
                connection.execute(
                    "INSERT INTO research_retrieval_results VALUES "
                    "(%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT DO NOTHING",
                    (report.report_id, item.rank, item.chunk_id, item.lexical_score,
                     json.dumps(item.matched_terms), item.excerpt, item.chunk_content_hash),
                )
        if self.report(report.report_id) != report:
            raise ResearchRetrievalError("conflicting_retrieval_report")

    def report(self, report_id: UUID) -> ResearchRetrievalReport:
        row = self._database.connection.execute(
            "SELECT * FROM research_retrieval_reports WHERE report_id=%s", (report_id,),
        ).fetchone()
        if row is None:
            raise KeyError(str(report_id))
        result_rows = self._database.connection.execute(
            "SELECT r.rank,c.chunk_id,c.source_document_id,c.source_version,c.source_kind,c.title,"
            "c.observed_at,c.available_at,r.lexical_score,r.matched_terms,r.excerpt,r.chunk_content_hash "
            ",c.text,c.content_hash "
            "FROM research_retrieval_results r JOIN internal_research_evidence_chunks c "
            "ON c.chunk_id=r.chunk_id WHERE r.report_id=%s ORDER BY r.rank", (report_id,),
        ).fetchall()
        if any(item[10] != item[12] or item[11] != item[13] for item in result_rows):
            raise ResearchRetrievalError("retrieval_result_chunk_mismatch")
        results = tuple(RetrievedEvidence(
            item[0], item[1], item[2], item[3], ResearchSourceKind(item[4]), item[5],
            item[6], item[7], item[8], tuple(item[9]), item[10], item[11],
        ) for item in result_rows)
        request = ResearchRetrievalRequest(row[1], row[2], row[3], row[4], ResearchAgentRole(row[5]), row[6], row[7])
        report = ResearchRetrievalReport(
            row[0], request, self.policy(row[3]).content_hash, tuple(row[8]), row[9], results,
            RetrievalOutcome(row[10]), tuple(row[11]), tuple(row[12]), row[13],
        )
        if report.content_hash != _report_hash(report):
            raise ResearchRetrievalError("retrieval_report_content_hash_mismatch")
        return report


def _tokens(value: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(value.casefold()))


def _policy_hash(policy: ResearchRetrievalPolicy) -> str:
    return _hash({
        "policy_id": str(policy.policy_id), "version": policy.version,
        "allowed_source_kinds": sorted(item.value for item in policy.allowed_source_kinds),
        "minimum_results": policy.minimum_results,
        "minimum_distinct_sources": policy.minimum_distinct_sources,
        "maximum_results": policy.maximum_results,
        "minimum_query_term_coverage": _decimal_text(policy.minimum_query_term_coverage),
        "approved_by": policy.approved_by, "approved_at": policy.approved_at.isoformat(),
        "enabled": policy.enabled,
    })


def _chunk_hash(chunk: InternalEvidenceChunk) -> str:
    return _hash({
        "chunk_id": str(chunk.chunk_id), "source_document_id": chunk.source_document_id,
        "source_version": chunk.source_version, "source_kind": chunk.source_kind.value,
        "instrument_id": chunk.instrument_id, "title": chunk.title, "text": chunk.text,
        "observed_at": chunk.observed_at.isoformat(), "available_at": chunk.available_at.isoformat(),
        "invalidated_at": chunk.invalidated_at.isoformat() if chunk.invalidated_at else None,
        "allowed_roles": sorted(item.value for item in chunk.allowed_roles),
    })


def _report_hash(report: ResearchRetrievalReport) -> str:
    return _hash({
        "report_id": str(report.report_id), "request_id": str(report.request.request_id),
        "workflow_id": str(report.request.workflow_id), "policy_id": str(report.request.policy_id),
        "instrument_id": report.request.instrument_id, "role": report.request.role.value,
        "query_text": report.request.query_text, "requested_at": report.request.requested_at.isoformat(),
        "policy_content_hash": report.policy_content_hash, "query_terms": report.query_terms,
        "query_term_coverage": _decimal_text(report.query_term_coverage),
        "results": [{
            "rank": item.rank, "chunk_id": str(item.chunk_id),
            "source_document_id": item.source_document_id,
            "source_version": item.source_version, "source_kind": item.source_kind.value,
            "title": item.title, "observed_at": item.observed_at.isoformat(),
            "available_at": item.available_at.isoformat(),
            "score": _decimal_text(item.lexical_score), "matched_terms": item.matched_terms,
            "excerpt": item.excerpt, "chunk_content_hash": item.chunk_content_hash,
        } for item in report.results],
        "outcome": report.outcome.value, "reasons": report.reasons,
        "limitations": report.limitations,
    })


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"


def _quantize(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def _ratio(numerator: int, denominator: int, places: int) -> Decimal:
    return _quantize(Decimal(numerator) / Decimal(denominator), places)


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
