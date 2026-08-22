"""Immutable, tool-free governance for scheduled research-agent evidence.

This module evaluates already-produced retrieval and answer-evaluation records.
It never schedules work, invokes a model, exposes a tool, or performs an action.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any, cast
from uuid import UUID, uuid5

from psycopg import sql

from .agent_answer_evaluation import (
    AgentAnswerEvaluationReport,
    AgentAnswerOutcome,
    PostgresAgentAnswerEvaluationStore,
    validate_agent_answer_evaluation_report,
)
from .agent_research import ResearchAgentRole
from .operational_jobs import (
    OperationalJobPolicy,
    OperationalJobRun,
    OperationalJobStatus,
    validate_operational_job_policy,
    validate_operational_job_run,
)
from .persistence import PostgresDatabase
from .research_retrieval import (
    PostgresResearchRetrievalStore,
    ResearchRetrievalReport,
    RetrievalOutcome,
    validate_research_retrieval_report,
)


class ScheduledAgentWorkflowError(ValueError):
    pass


class AgentWorkflowPurpose(StrEnum):
    NEWS_SUMMARIZATION = "NEWS_SUMMARIZATION"
    EVENT_EXTRACTION = "EVENT_EXTRACTION"
    ENTITY_LINKING = "ENTITY_LINKING"
    REPORT_GENERATION = "REPORT_GENERATION"
    STRATEGY_EXPLANATION = "STRATEGY_EXPLANATION"
    RESEARCH_ASSISTANCE = "RESEARCH_ASSISTANCE"
    NATURAL_LANGUAGE_QUERY = "NATURAL_LANGUAGE_QUERY"
    LOG_INVESTIGATION = "LOG_INVESTIGATION"
    DOCUMENTATION = "DOCUMENTATION"
    CODE_REVIEW_ASSISTANCE = "CODE_REVIEW_ASSISTANCE"


class ScheduledAgentWorkflowOutcome(StrEnum):
    READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
    BLOCKED_POLICY_DISABLED = "BLOCKED_POLICY_DISABLED"
    BLOCKED_INCOMPLETE_EVIDENCE = "BLOCKED_INCOMPLETE_EVIDENCE"
    BLOCKED_BUDGET = "BLOCKED_BUDGET"


_NS = UUID("fdf09f33-a293-45dd-8fc0-f0c41d0845cc")
_NONE = "NONE"
_HUMAN_REVIEW = "EXPLICIT_HUMAN_REVIEW"
_PG_INTEGER_MAX = 2_147_483_647


@dataclass(frozen=True, slots=True)
class AgentWorkflowGovernancePolicy:
    policy_id: UUID
    version: str
    allowed_purposes: tuple[AgentWorkflowPurpose, ...]
    allowed_roles: tuple[ResearchAgentRole, ...]
    minimum_schedule_interval_seconds: int
    maximum_workflows_per_run: int
    maximum_input_tokens_per_workflow: int
    maximum_output_tokens_per_workflow: int
    maximum_total_tokens_per_run: int
    maximum_estimated_cost_per_run: Decimal
    cost_currency: str
    require_complete_retrieval: bool
    require_review_eligible_answer: bool
    approved_by: str
    approved_at: datetime
    enabled: bool
    tool_authority: str
    model_invocation_authority: str
    action_authority: str
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        policy_id: UUID,
        version: str,
        allowed_purposes: tuple[AgentWorkflowPurpose, ...],
        allowed_roles: tuple[ResearchAgentRole, ...],
        minimum_schedule_interval_seconds: int,
        maximum_workflows_per_run: int,
        maximum_input_tokens_per_workflow: int,
        maximum_output_tokens_per_workflow: int,
        maximum_total_tokens_per_run: int,
        maximum_estimated_cost_per_run: Decimal,
        cost_currency: str,
        require_complete_retrieval: bool,
        require_review_eligible_answer: bool,
        approved_by: str,
        approved_at: datetime,
        enabled: bool = True,
    ) -> AgentWorkflowGovernancePolicy:
        draft = cls(
            policy_id, version, _sorted_unique(allowed_purposes),
            _sorted_unique(allowed_roles), minimum_schedule_interval_seconds,
            maximum_workflows_per_run, maximum_input_tokens_per_workflow,
            maximum_output_tokens_per_workflow, maximum_total_tokens_per_run,
            maximum_estimated_cost_per_run, cost_currency,
            require_complete_retrieval, require_review_eligible_answer,
            approved_by, approved_at, enabled, _NONE, _NONE, _NONE, "",
        )
        return replace(draft, content_hash=_governance_policy_hash(draft))


@dataclass(frozen=True, slots=True)
class AgentWorkflowSchedulePolicy:
    schedule_policy_id: UUID
    schedule_name: str
    version: str
    governance_policy_id: UUID
    governance_policy_content_hash: str
    job_policy_id: UUID
    job_policy_content_hash: str
    allowed_purposes: tuple[AgentWorkflowPurpose, ...]
    allowed_roles: tuple[ResearchAgentRole, ...]
    effective_from: datetime
    approved_by: str
    approved_at: datetime
    enabled: bool
    scheduler_authority: str
    content_hash: str

    @classmethod
    def create(
        cls,
        governance_policy: AgentWorkflowGovernancePolicy,
        job_policy: OperationalJobPolicy,
        *,
        schedule_policy_id: UUID,
        schedule_name: str,
        version: str,
        allowed_purposes: tuple[AgentWorkflowPurpose, ...],
        allowed_roles: tuple[ResearchAgentRole, ...],
        effective_from: datetime,
        approved_by: str,
        approved_at: datetime,
        enabled: bool = True,
    ) -> AgentWorkflowSchedulePolicy:
        validate_agent_workflow_governance_policy(governance_policy)
        validate_operational_job_policy(job_policy)
        draft = cls(
            schedule_policy_id, schedule_name, version, governance_policy.policy_id,
            governance_policy.content_hash, job_policy.policy_id, job_policy.content_hash,
            _sorted_unique(allowed_purposes), _sorted_unique(allowed_roles), effective_from,
            approved_by, approved_at, enabled, _NONE, "",
        )
        schedule = replace(draft, content_hash=_schedule_policy_hash(draft))
        validate_agent_workflow_schedule_policy(schedule, governance_policy, job_policy)
        return schedule


@dataclass(frozen=True, slots=True)
class ScheduledAgentWorkflowCandidate:
    purpose: AgentWorkflowPurpose
    retrieval_report: ResearchRetrievalReport
    answer_evaluation_report: AgentAnswerEvaluationReport
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost: Decimal

    @property
    def workflow_id(self) -> UUID:
        return self.retrieval_report.request.workflow_id

    @property
    def role(self) -> ResearchAgentRole:
        return self.retrieval_report.request.role


@dataclass(frozen=True, slots=True)
class ScheduledAgentWorkflowAssessment:
    assessment_id: UUID
    schedule_policy_id: UUID
    schedule_policy_content_hash: str
    governance_policy_id: UUID
    governance_policy_content_hash: str
    job_run_id: UUID
    job_run_content_hash: str
    evaluated_at: datetime
    workflow_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_estimated_cost: Decimal
    cost_currency: str
    candidate_evidence_hash: str
    outcome: ScheduledAgentWorkflowOutcome
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    approval_requirement: str
    tool_authority: str
    model_invocation_authority: str
    action_authority: str
    content_hash: str


def evaluate_scheduled_agent_workflows(
    governance_policy: AgentWorkflowGovernancePolicy,
    schedule_policy: AgentWorkflowSchedulePolicy,
    job_policy: OperationalJobPolicy,
    job_run: OperationalJobRun,
    candidates: tuple[ScheduledAgentWorkflowCandidate, ...],
    *,
    evaluated_at: datetime,
) -> ScheduledAgentWorkflowAssessment:
    validate_agent_workflow_governance_policy(governance_policy)
    validate_agent_workflow_schedule_policy(schedule_policy, governance_policy, job_policy)
    validate_operational_job_run(job_run)
    if job_run.policy_id != job_policy.policy_id:
        raise ScheduledAgentWorkflowError("scheduled_agent_job_run_policy_mismatch")
    if not _aware(evaluated_at) or evaluated_at < job_run.completed_at:
        raise ScheduledAgentWorkflowError("invalid_scheduled_agent_evaluation_time")
    if job_run.scheduled_for < schedule_policy.effective_from:
        raise ScheduledAgentWorkflowError("scheduled_agent_run_precedes_schedule")
    ordered = tuple(sorted(candidates, key=_candidate_sort_key))
    identities = [(item.workflow_id, item.role) for item in ordered]
    if len(set(identities)) != len(identities):
        raise ScheduledAgentWorkflowError("duplicate_scheduled_agent_workflow_role")
    for item in ordered:
        _validate_candidate(
            item, schedule_policy, governance_policy,
            evidence_cutoff=job_run.completed_at,
        )

    total_input = sum(item.estimated_input_tokens for item in ordered)
    total_output = sum(item.estimated_output_tokens for item in ordered)
    total_cost = _quantize(sum((item.estimated_cost for item in ordered), Decimal("0")), 12)
    if (
        len(ordered) > _PG_INTEGER_MAX or total_input > _PG_INTEGER_MAX
        or total_output > _PG_INTEGER_MAX or not _fits_numeric_30_12(total_cost)
    ):
        raise ScheduledAgentWorkflowError("scheduled_agent_totals_out_of_storage_bounds")
    reasons: list[str] = []
    if not governance_policy.enabled or not schedule_policy.enabled or not job_policy.enabled:
        reasons.append("scheduled_agent_policy_disabled")
    evidence_incomplete = (
        not ordered
        or job_run.status is not OperationalJobStatus.SUCCEEDED
        or any(
            (governance_policy.require_complete_retrieval
             and item.retrieval_report.outcome is not RetrievalOutcome.COMPLETE)
            or (governance_policy.require_review_eligible_answer
                and item.answer_evaluation_report.outcome is not AgentAnswerOutcome.REVIEW_ELIGIBLE)
            for item in ordered
        )
    )
    if evidence_incomplete:
        reasons.append("scheduled_agent_evidence_incomplete_or_blocked")
    budget_exceeded = (
        len(ordered) > governance_policy.maximum_workflows_per_run
        or total_input + total_output > governance_policy.maximum_total_tokens_per_run
        or total_cost > governance_policy.maximum_estimated_cost_per_run
    )
    if budget_exceeded:
        reasons.append("scheduled_agent_run_budget_exceeded")
    if "scheduled_agent_policy_disabled" in reasons:
        outcome = ScheduledAgentWorkflowOutcome.BLOCKED_POLICY_DISABLED
    elif "scheduled_agent_evidence_incomplete_or_blocked" in reasons:
        outcome = ScheduledAgentWorkflowOutcome.BLOCKED_INCOMPLETE_EVIDENCE
    elif budget_exceeded:
        outcome = ScheduledAgentWorkflowOutcome.BLOCKED_BUDGET
    else:
        outcome = ScheduledAgentWorkflowOutcome.READY_FOR_HUMAN_REVIEW
    evidence_hash = _candidate_evidence_hash(ordered)
    assessment_id = uuid5(
        _NS, f"{schedule_policy.content_hash}:{job_run.content_hash}:{evidence_hash}",
    )
    draft = ScheduledAgentWorkflowAssessment(
        assessment_id, schedule_policy.schedule_policy_id, schedule_policy.content_hash,
        governance_policy.policy_id, governance_policy.content_hash,
        job_run.run_id, job_run.content_hash, evaluated_at, len(ordered), total_input,
        total_output, total_cost, governance_policy.cost_currency, evidence_hash,
        outcome, tuple(reasons) or ("bounded_evidence_ready_for_explicit_human_review",),
        (
            "fixture_evidence_is_not_external_model_or_scheduler_acceptance",
            "lexical_answer_evaluation_is_not_semantic_truth_or_causal_verification",
            "assessment_does_not_schedule_invoke_deliver_execute_or_approve",
        ),
        _HUMAN_REVIEW, _NONE, _NONE, _NONE, "",
    )
    return replace(draft, content_hash=_assessment_hash(draft))


def validate_agent_workflow_governance_policy(policy: AgentWorkflowGovernancePolicy) -> None:
    numeric = (
        policy.minimum_schedule_interval_seconds, policy.maximum_workflows_per_run,
        policy.maximum_input_tokens_per_workflow, policy.maximum_output_tokens_per_workflow,
        policy.maximum_total_tokens_per_run,
    )
    if (
        not policy.version.strip() or not policy.allowed_purposes or not policy.allowed_roles
        or policy.allowed_purposes != _sorted_unique(policy.allowed_purposes)
        or policy.allowed_roles != _sorted_unique(policy.allowed_roles)
        or any(value <= 0 or value > _PG_INTEGER_MAX for value in numeric)
        or policy.maximum_total_tokens_per_run
        < policy.maximum_input_tokens_per_workflow + policy.maximum_output_tokens_per_workflow
        or not _fits_numeric_30_12(policy.maximum_estimated_cost_per_run)
        or policy.maximum_estimated_cost_per_run < 0
        or len(policy.cost_currency) != 3 or policy.cost_currency != policy.cost_currency.upper()
        or not policy.approved_by.strip() or not _aware(policy.approved_at)
        or (policy.tool_authority, policy.model_invocation_authority, policy.action_authority)
        != (_NONE, _NONE, _NONE)
        or policy.content_hash != _governance_policy_hash(policy)
    ):
        raise ScheduledAgentWorkflowError("invalid_or_tampered_agent_workflow_governance_policy")


def validate_agent_workflow_schedule_policy(
    schedule: AgentWorkflowSchedulePolicy,
    governance: AgentWorkflowGovernancePolicy,
    job_policy: OperationalJobPolicy,
) -> None:
    validate_agent_workflow_governance_policy(governance)
    validate_operational_job_policy(job_policy)
    if (
        not schedule.schedule_name.strip() or not schedule.version.strip()
        or schedule.governance_policy_id != governance.policy_id
        or schedule.governance_policy_content_hash != governance.content_hash
        or schedule.job_policy_id != job_policy.policy_id
        or schedule.job_policy_content_hash != job_policy.content_hash
        or not schedule.allowed_purposes or not schedule.allowed_roles
        or schedule.allowed_purposes != _sorted_unique(schedule.allowed_purposes)
        or schedule.allowed_roles != _sorted_unique(schedule.allowed_roles)
        or not set(schedule.allowed_purposes).issubset(governance.allowed_purposes)
        or not set(schedule.allowed_roles).issubset(governance.allowed_roles)
        or job_policy.interval_seconds < governance.minimum_schedule_interval_seconds
        or not _aware(schedule.effective_from) or not _aware(schedule.approved_at)
        or governance.approved_at > schedule.approved_at
        or job_policy.approved_at > schedule.approved_at
        or schedule.approved_at > schedule.effective_from
        or not schedule.approved_by.strip() or schedule.scheduler_authority != _NONE
        or schedule.content_hash != _schedule_policy_hash(schedule)
    ):
        raise ScheduledAgentWorkflowError("invalid_or_tampered_agent_workflow_schedule_policy")


class PostgresScheduledAgentWorkflowStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append_governance_policy(self, policy: AgentWorkflowGovernancePolicy) -> None:
        validate_agent_workflow_governance_policy(policy)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO agent_workflow_governance_policy_versions VALUES "
                "(%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (policy_id) DO NOTHING RETURNING content_hash",
                (
                    policy.policy_id, policy.version,
                    json.dumps([item.value for item in policy.allowed_purposes]),
                    json.dumps([item.value for item in policy.allowed_roles]),
                    policy.minimum_schedule_interval_seconds, policy.maximum_workflows_per_run,
                    policy.maximum_input_tokens_per_workflow,
                    policy.maximum_output_tokens_per_workflow,
                    policy.maximum_total_tokens_per_run, policy.maximum_estimated_cost_per_run,
                    policy.cost_currency, policy.require_complete_retrieval,
                    policy.require_review_eligible_answer, policy.approved_by,
                    policy.approved_at, policy.enabled, policy.tool_authority,
                    policy.model_invocation_authority, policy.action_authority,
                    policy.content_hash,
                ),
            )
            self._verify_insert(cursor, "agent_workflow_governance_policy_versions", "policy_id", policy.policy_id, policy.content_hash)

    def append_schedule_policy(
        self,
        schedule: AgentWorkflowSchedulePolicy,
        governance: AgentWorkflowGovernancePolicy,
        job_policy: OperationalJobPolicy,
    ) -> None:
        validate_agent_workflow_schedule_policy(schedule, governance, job_policy)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            self._verify_reference(cursor, "agent_workflow_governance_policy_versions", "policy_id", governance.policy_id, governance.content_hash)
            self._verify_reference(cursor, "operational_job_policy_versions", "policy_id", job_policy.policy_id, job_policy.content_hash)
            cursor.execute(
                "INSERT INTO agent_workflow_schedule_policy_versions VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (schedule_policy_id) DO NOTHING RETURNING content_hash",
                (
                    schedule.schedule_policy_id, schedule.schedule_name, schedule.version,
                    schedule.governance_policy_id, schedule.governance_policy_content_hash,
                    schedule.job_policy_id, schedule.job_policy_content_hash,
                    json.dumps([item.value for item in schedule.allowed_purposes]),
                    json.dumps([item.value for item in schedule.allowed_roles]),
                    schedule.effective_from, schedule.approved_by, schedule.approved_at,
                    schedule.enabled, schedule.scheduler_authority, schedule.content_hash,
                ),
            )
            self._verify_insert(cursor, "agent_workflow_schedule_policy_versions", "schedule_policy_id", schedule.schedule_policy_id, schedule.content_hash)

    def append_assessment(
        self,
        assessment: ScheduledAgentWorkflowAssessment,
        candidates: tuple[ScheduledAgentWorkflowCandidate, ...],
    ) -> None:
        schedule = self.schedule_policy(assessment.schedule_policy_id)
        governance = self.governance_policy(assessment.governance_policy_id)
        job_policy, job_run = self._job_evidence(assessment.job_run_id)
        retrieval_store = PostgresResearchRetrievalStore(self._database)
        answer_store = PostgresAgentAnswerEvaluationStore(self._database)
        registered_candidates = tuple(
            replace(
                item,
                retrieval_report=retrieval_store.report(item.retrieval_report.report_id),
                answer_evaluation_report=answer_store.report(
                    item.answer_evaluation_report.report_id,
                ),
            )
            for item in candidates
        )
        expected = evaluate_scheduled_agent_workflows(
            governance, schedule, job_policy, job_run, registered_candidates,
            evaluated_at=assessment.evaluated_at,
        )
        if expected != assessment:
            raise ScheduledAgentWorkflowError("scheduled_agent_assessment_not_reproducible")
        ordered = tuple(sorted(registered_candidates, key=_candidate_sort_key))
        with self._database.transaction() as connection, connection.cursor() as cursor:
            self._verify_reference(cursor, "agent_workflow_schedule_policy_versions", "schedule_policy_id", assessment.schedule_policy_id, assessment.schedule_policy_content_hash)
            self._verify_reference(cursor, "agent_workflow_governance_policy_versions", "policy_id", assessment.governance_policy_id, assessment.governance_policy_content_hash)
            self._verify_reference(cursor, "operational_job_runs", "run_id", assessment.job_run_id, assessment.job_run_content_hash)
            for item in ordered:
                self._verify_reference(cursor, "research_retrieval_reports", "report_id", item.retrieval_report.report_id, item.retrieval_report.content_hash)
                self._verify_reference(cursor, "agent_answer_evaluation_reports", "report_id", item.answer_evaluation_report.report_id, item.answer_evaluation_report.content_hash)
            cursor.execute(
                "INSERT INTO scheduled_agent_workflow_assessments VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s) "
                "ON CONFLICT (assessment_id) DO NOTHING RETURNING content_hash",
                (
                    assessment.assessment_id, assessment.schedule_policy_id,
                    assessment.schedule_policy_content_hash, assessment.governance_policy_id,
                    assessment.governance_policy_content_hash, assessment.job_run_id,
                    assessment.job_run_content_hash, assessment.evaluated_at,
                    assessment.workflow_count, assessment.total_input_tokens,
                    assessment.total_output_tokens, assessment.total_estimated_cost,
                    assessment.cost_currency, assessment.candidate_evidence_hash,
                    assessment.outcome.value, json.dumps(assessment.reasons),
                    json.dumps(assessment.limitations), assessment.approval_requirement,
                    assessment.tool_authority, assessment.model_invocation_authority,
                    assessment.action_authority, assessment.content_hash,
                ),
            )
            self._verify_insert(cursor, "scheduled_agent_workflow_assessments", "assessment_id", assessment.assessment_id, assessment.content_hash)
            for item in ordered:
                cursor.execute(
                    "INSERT INTO scheduled_agent_workflow_candidates VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (
                        assessment.assessment_id, item.workflow_id, item.role.value,
                        item.purpose.value, item.retrieval_report.report_id,
                        item.retrieval_report.content_hash,
                        item.answer_evaluation_report.report_id,
                        item.answer_evaluation_report.content_hash,
                        item.estimated_input_tokens, item.estimated_output_tokens,
                        item.estimated_cost, _candidate_hash(item),
                    ),
                )

    def governance_policy(self, policy_id: UUID) -> AgentWorkflowGovernancePolicy:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM agent_workflow_governance_policy_versions WHERE policy_id=%s", (policy_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(str(policy_id))
        policy = _governance_policy_from_row(row)
        validate_agent_workflow_governance_policy(policy)
        return policy

    def schedule_policy(self, policy_id: UUID) -> AgentWorkflowSchedulePolicy:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM agent_workflow_schedule_policy_versions WHERE schedule_policy_id=%s", (policy_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(str(policy_id))
        schedule = _schedule_policy_from_row(row)
        if schedule.content_hash != _schedule_policy_hash(schedule):
            raise ScheduledAgentWorkflowError("scheduled_agent_schedule_hash_mismatch")
        return schedule

    def assessment(
        self, assessment_id: UUID,
    ) -> tuple[ScheduledAgentWorkflowAssessment, tuple[ScheduledAgentWorkflowCandidate, ...]]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM scheduled_agent_workflow_assessments WHERE assessment_id=%s", (assessment_id,))
            row = cursor.fetchone()
            cursor.execute(
                "SELECT workflow_id,role,purpose,retrieval_report_id,answer_evaluation_report_id,"
                "estimated_input_tokens,estimated_output_tokens,estimated_cost,candidate_hash "
                "FROM scheduled_agent_workflow_candidates WHERE assessment_id=%s "
                "ORDER BY workflow_id,role", (assessment_id,),
            )
            candidate_rows = cursor.fetchall()
        if row is None:
            raise KeyError(str(assessment_id))
        retrieval_store = PostgresResearchRetrievalStore(self._database)
        answer_store = PostgresAgentAnswerEvaluationStore(self._database)
        candidates = tuple(
            ScheduledAgentWorkflowCandidate(
                AgentWorkflowPurpose(str(item[2])), retrieval_store.report(UUID(str(item[3]))),
                answer_store.report(UUID(str(item[4]))), int(str(item[5])), int(str(item[6])),
                Decimal(str(item[7])),
            )
            for item in candidate_rows
        )
        if any(str(row_value[8]) != _candidate_hash(candidate) for row_value, candidate in zip(candidate_rows, candidates, strict=True)):
            raise ScheduledAgentWorkflowError("scheduled_agent_candidate_hash_mismatch")
        assessment = _assessment_from_row(row)
        if assessment.content_hash != _assessment_hash(assessment) or assessment.candidate_evidence_hash != _candidate_evidence_hash(candidates):
            raise ScheduledAgentWorkflowError("scheduled_agent_assessment_hash_mismatch")
        return assessment, candidates

    def _job_evidence(self, run_id: UUID) -> tuple[OperationalJobPolicy, OperationalJobRun]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT r.run_id,r.policy_id,r.idempotency_key,r.scheduled_for,r.started_at,"
                "r.completed_at,r.status,r.summary,r.content_hash,p.policy_id,p.job_name,p.version,"
                "p.interval_seconds,p.grace_seconds,p.owner,p.runbook_uri,p.approved_by,p.approved_at,"
                "p.enabled,p.content_hash FROM operational_job_runs r JOIN "
                "operational_job_policy_versions p ON p.policy_id=r.policy_id WHERE r.run_id=%s",
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(str(run_id))
        run = OperationalJobRun(
            UUID(str(row[0])), UUID(str(row[1])), str(row[2]), cast(datetime, row[3]),
            cast(datetime, row[4]), cast(datetime, row[5]), OperationalJobStatus(str(row[6])),
            dict(cast(dict[str, str], row[7])), str(row[8]),
        )
        policy = OperationalJobPolicy(
            UUID(str(row[9])), str(row[10]), str(row[11]), int(str(row[12])),
            int(str(row[13])), str(row[14]), str(row[15]), str(row[16]),
            cast(datetime, row[17]), bool(row[18]), str(row[19]),
        )
        validate_operational_job_policy(policy)
        validate_operational_job_run(run)
        return policy, run

    @staticmethod
    def _verify_insert(cursor: Any, table: str, id_column: str, identity: UUID, content_hash: str) -> None:
        if cursor.fetchone() is not None:
            return
        query = sql.SQL("SELECT content_hash FROM {} WHERE {}=%s").format(sql.Identifier(table), sql.Identifier(id_column))
        cursor.execute(query, (identity,))
        row = cursor.fetchone()
        if row is None or str(row[0]) != content_hash:
            raise ScheduledAgentWorkflowError(f"conflicting_{table}")

    @staticmethod
    def _verify_reference(cursor: Any, table: str, id_column: str, identity: UUID, content_hash: str) -> None:
        query = sql.SQL("SELECT content_hash FROM {} WHERE {}=%s").format(sql.Identifier(table), sql.Identifier(id_column))
        cursor.execute(query, (identity,))
        row = cursor.fetchone()
        if row is None or str(row[0]) != content_hash:
            raise ScheduledAgentWorkflowError(f"{table}_not_registered_or_mismatched")


def _validate_candidate(
    candidate: ScheduledAgentWorkflowCandidate,
    schedule: AgentWorkflowSchedulePolicy,
    governance: AgentWorkflowGovernancePolicy,
    evidence_cutoff: datetime,
) -> None:
    validate_research_retrieval_report(candidate.retrieval_report)
    validate_agent_answer_evaluation_report(candidate.answer_evaluation_report)
    retrieval = candidate.retrieval_report
    answer = candidate.answer_evaluation_report
    if (
        candidate.purpose not in schedule.allowed_purposes
        or candidate.role not in schedule.allowed_roles
        or answer.retrieval_report_id != retrieval.report_id
        or retrieval.request.requested_at > evidence_cutoff
        or answer.evaluated_at > evidence_cutoff
        or candidate.estimated_input_tokens <= 0
        or candidate.estimated_output_tokens <= 0
        or candidate.estimated_input_tokens > _PG_INTEGER_MAX
        or candidate.estimated_output_tokens > _PG_INTEGER_MAX
        or candidate.estimated_input_tokens > governance.maximum_input_tokens_per_workflow
        or candidate.estimated_output_tokens > governance.maximum_output_tokens_per_workflow
        or not _fits_numeric_30_12(candidate.estimated_cost)
        or candidate.estimated_cost < 0
    ):
        raise ScheduledAgentWorkflowError("invalid_or_unbound_scheduled_agent_candidate")


def _governance_policy_from_row(row: tuple[object, ...]) -> AgentWorkflowGovernancePolicy:
    return AgentWorkflowGovernancePolicy(
        UUID(str(row[0])), str(row[1]),
        tuple(AgentWorkflowPurpose(item) for item in cast(list[str], row[2])),
        tuple(ResearchAgentRole(item) for item in cast(list[str], row[3])),
        int(str(row[4])), int(str(row[5])), int(str(row[6])), int(str(row[7])),
        int(str(row[8])), Decimal(str(row[9])), str(row[10]), bool(row[11]), bool(row[12]),
        str(row[13]), cast(datetime, row[14]), bool(row[15]), str(row[16]), str(row[17]),
        str(row[18]), str(row[19]),
    )


def _schedule_policy_from_row(row: tuple[object, ...]) -> AgentWorkflowSchedulePolicy:
    return AgentWorkflowSchedulePolicy(
        UUID(str(row[0])), str(row[1]), str(row[2]), UUID(str(row[3])), str(row[4]),
        UUID(str(row[5])), str(row[6]),
        tuple(AgentWorkflowPurpose(item) for item in cast(list[str], row[7])),
        tuple(ResearchAgentRole(item) for item in cast(list[str], row[8])),
        cast(datetime, row[9]), str(row[10]), cast(datetime, row[11]), bool(row[12]),
        str(row[13]), str(row[14]),
    )


def _assessment_from_row(row: tuple[object, ...]) -> ScheduledAgentWorkflowAssessment:
    return ScheduledAgentWorkflowAssessment(
        UUID(str(row[0])), UUID(str(row[1])), str(row[2]), UUID(str(row[3])), str(row[4]),
        UUID(str(row[5])), str(row[6]), cast(datetime, row[7]), int(str(row[8])),
        int(str(row[9])), int(str(row[10])), Decimal(str(row[11])), str(row[12]),
        str(row[13]), ScheduledAgentWorkflowOutcome(str(row[14])),
        tuple(cast(list[str], row[15])), tuple(cast(list[str], row[16])), str(row[17]),
        str(row[18]), str(row[19]), str(row[20]), str(row[21]),
    )


def _governance_policy_hash(policy: AgentWorkflowGovernancePolicy) -> str:
    return _hash({
        "policy_id": str(policy.policy_id), "version": policy.version,
        "allowed_purposes": [item.value for item in policy.allowed_purposes],
        "allowed_roles": [item.value for item in policy.allowed_roles],
        "minimum_schedule_interval_seconds": policy.minimum_schedule_interval_seconds,
        "maximum_workflows_per_run": policy.maximum_workflows_per_run,
        "maximum_input_tokens_per_workflow": policy.maximum_input_tokens_per_workflow,
        "maximum_output_tokens_per_workflow": policy.maximum_output_tokens_per_workflow,
        "maximum_total_tokens_per_run": policy.maximum_total_tokens_per_run,
        "maximum_estimated_cost_per_run": _decimal_text(policy.maximum_estimated_cost_per_run),
        "cost_currency": policy.cost_currency,
        "require_complete_retrieval": policy.require_complete_retrieval,
        "require_review_eligible_answer": policy.require_review_eligible_answer,
        "approved_by": policy.approved_by, "approved_at": policy.approved_at.isoformat(),
        "enabled": policy.enabled, "tool_authority": policy.tool_authority,
        "model_invocation_authority": policy.model_invocation_authority,
        "action_authority": policy.action_authority,
    })


def _schedule_policy_hash(policy: AgentWorkflowSchedulePolicy) -> str:
    return _hash({
        "schedule_policy_id": str(policy.schedule_policy_id), "schedule_name": policy.schedule_name,
        "version": policy.version, "governance_policy_id": str(policy.governance_policy_id),
        "governance_policy_content_hash": policy.governance_policy_content_hash,
        "job_policy_id": str(policy.job_policy_id),
        "job_policy_content_hash": policy.job_policy_content_hash,
        "allowed_purposes": [item.value for item in policy.allowed_purposes],
        "allowed_roles": [item.value for item in policy.allowed_roles],
        "effective_from": policy.effective_from.isoformat(), "approved_by": policy.approved_by,
        "approved_at": policy.approved_at.isoformat(), "enabled": policy.enabled,
        "scheduler_authority": policy.scheduler_authority,
    })


def _assessment_hash(assessment: ScheduledAgentWorkflowAssessment) -> str:
    return _hash({
        "assessment_id": str(assessment.assessment_id),
        "schedule_policy_id": str(assessment.schedule_policy_id),
        "schedule_policy_content_hash": assessment.schedule_policy_content_hash,
        "governance_policy_id": str(assessment.governance_policy_id),
        "governance_policy_content_hash": assessment.governance_policy_content_hash,
        "job_run_id": str(assessment.job_run_id),
        "job_run_content_hash": assessment.job_run_content_hash,
        "evaluated_at": assessment.evaluated_at.isoformat(),
        "workflow_count": assessment.workflow_count,
        "total_input_tokens": assessment.total_input_tokens,
        "total_output_tokens": assessment.total_output_tokens,
        "total_estimated_cost": _decimal_text(assessment.total_estimated_cost),
        "cost_currency": assessment.cost_currency,
        "candidate_evidence_hash": assessment.candidate_evidence_hash,
        "outcome": assessment.outcome.value, "reasons": assessment.reasons,
        "limitations": assessment.limitations,
        "approval_requirement": assessment.approval_requirement,
        "tool_authority": assessment.tool_authority,
        "model_invocation_authority": assessment.model_invocation_authority,
        "action_authority": assessment.action_authority,
    })


def _candidate_hash(candidate: ScheduledAgentWorkflowCandidate) -> str:
    return _hash({
        "workflow_id": str(candidate.workflow_id), "role": candidate.role.value,
        "purpose": candidate.purpose.value,
        "retrieval_report_id": str(candidate.retrieval_report.report_id),
        "retrieval_report_content_hash": candidate.retrieval_report.content_hash,
        "answer_evaluation_report_id": str(candidate.answer_evaluation_report.report_id),
        "answer_evaluation_report_content_hash": candidate.answer_evaluation_report.content_hash,
        "estimated_input_tokens": candidate.estimated_input_tokens,
        "estimated_output_tokens": candidate.estimated_output_tokens,
        "estimated_cost": _decimal_text(candidate.estimated_cost),
    })


def _candidate_evidence_hash(candidates: tuple[ScheduledAgentWorkflowCandidate, ...]) -> str:
    return _hash([_candidate_hash(item) for item in sorted(candidates, key=_candidate_sort_key)])


def _candidate_sort_key(candidate: ScheduledAgentWorkflowCandidate) -> tuple[str, str]:
    return str(candidate.workflow_id), candidate.role.value


def _sorted_unique(values: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(sorted(set(values), key=lambda item: item.value))


def _fits_numeric_30_12(value: Decimal) -> bool:
    if not value.is_finite() or value != _quantize(value, 12):
        return False
    sign, digits, exponent = value.as_tuple()
    del sign
    integer_digits = max(len(digits) + cast(int, exponent), 0)
    return integer_digits <= 18


def _quantize(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
