"""Immutable scheduled-report and operational-cost governance evidence.

This module binds report evidence to the existing durable operational-job
authority.  It records supplied evidence and deterministic review outcomes; it
does not schedule work, call providers or models, spend funds, change risk, or
grant paper/live execution authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

from psycopg import sql

from .operational_jobs import (
    OperationalJobPolicy,
    OperationalJobRun,
    OperationalJobStatus,
    validate_operational_job_policy,
    validate_operational_job_run,
)
from .persistence import PostgresDatabase


class GovernanceReportingError(ValueError):
    pass


class ReportCadence(str, Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ON_DEMAND = "ON_DEMAND"


class GovernanceReportType(str, Enum):
    DAILY_MARKET = "DAILY_MARKET"
    DAILY_RISK = "DAILY_RISK"
    DAILY_EXECUTION = "DAILY_EXECUTION"
    DAILY_DATA_HEALTH = "DAILY_DATA_HEALTH"
    WEEKLY_STRATEGY = "WEEKLY_STRATEGY"
    WEEKLY_MODEL_DRIFT = "WEEKLY_MODEL_DRIFT"
    WEEKLY_PORTFOLIO = "WEEKLY_PORTFOLIO"
    MONTHLY_INVESTMENT_REVIEW = "MONTHLY_INVESTMENT_REVIEW"
    MONTHLY_STRATEGY_ATTRIBUTION = "MONTHLY_STRATEGY_ATTRIBUTION"
    MONTHLY_COST = "MONTHLY_COST"
    MONTHLY_INCIDENT = "MONTHLY_INCIDENT"
    QUARTERLY_MODEL_GOVERNANCE = "QUARTERLY_MODEL_GOVERNANCE"
    LIVE_READINESS = "LIVE_READINESS"


REPORT_CADENCE: Mapping[GovernanceReportType, ReportCadence] = MappingProxyType(
    {
        GovernanceReportType.DAILY_MARKET: ReportCadence.DAILY,
        GovernanceReportType.DAILY_RISK: ReportCadence.DAILY,
        GovernanceReportType.DAILY_EXECUTION: ReportCadence.DAILY,
        GovernanceReportType.DAILY_DATA_HEALTH: ReportCadence.DAILY,
        GovernanceReportType.WEEKLY_STRATEGY: ReportCadence.WEEKLY,
        GovernanceReportType.WEEKLY_MODEL_DRIFT: ReportCadence.WEEKLY,
        GovernanceReportType.WEEKLY_PORTFOLIO: ReportCadence.WEEKLY,
        GovernanceReportType.MONTHLY_INVESTMENT_REVIEW: ReportCadence.MONTHLY,
        GovernanceReportType.MONTHLY_STRATEGY_ATTRIBUTION: ReportCadence.MONTHLY,
        GovernanceReportType.MONTHLY_COST: ReportCadence.MONTHLY,
        GovernanceReportType.MONTHLY_INCIDENT: ReportCadence.MONTHLY,
        GovernanceReportType.QUARTERLY_MODEL_GOVERNANCE: ReportCadence.QUARTERLY,
        GovernanceReportType.LIVE_READINESS: ReportCadence.ON_DEMAND,
    }
)

_CADENCE_INTERVAL_BOUNDS: Mapping[ReportCadence, tuple[int, int] | None] = MappingProxyType(
    {
        ReportCadence.DAILY: (86_400, 86_400),
        ReportCadence.WEEKLY: (604_800, 604_800),
        ReportCadence.MONTHLY: (2_419_200, 2_678_400),
        ReportCadence.QUARTERLY: (7_776_000, 7_948_800),
        ReportCadence.ON_DEMAND: None,
    }
)


class ReportEvidenceClass(str, Enum):
    FACT = "FACT"
    MODEL_ESTIMATE = "MODEL_ESTIMATE"
    INFERENCE = "INFERENCE"
    UNVERIFIED_INFORMATION = "UNVERIFIED_INFORMATION"
    MISSING_DATA = "MISSING_DATA"


class BudgetMode(str, Enum):
    LOCAL_RESEARCH = "LOCAL_RESEARCH"
    LOW_COST_PAPER = "LOW_COST_PAPER"
    PROFESSIONAL_PAPER = "PROFESSIONAL_PAPER"
    LIMITED_LIVE = "LIMITED_LIVE"
    SCALED_LIVE = "SCALED_LIVE"


class OperationalCostCategory(str, Enum):
    DATA_PROVIDER = "DATA_PROVIDER"
    NEWS_PROVIDER = "NEWS_PROVIDER"
    SOCIAL_PROVIDER = "SOCIAL_PROVIDER"
    CLOUD_COMPUTE = "CLOUD_COMPUTE"
    STORAGE = "STORAGE"
    DATABASE = "DATABASE"
    STREAMING = "STREAMING"
    AI_INFERENCE = "AI_INFERENCE"
    BROKER_FEES = "BROKER_FEES"
    EXCHANGE_FEES = "EXCHANGE_FEES"
    MONITORING = "MONITORING"
    BACKUP = "BACKUP"


class CostCandidateType(str, Enum):
    DATASET = "DATASET"
    MODEL = "MODEL"


class CostValueOutcome(str, Enum):
    JUSTIFIED_FOR_REVIEW = "JUSTIFIED_FOR_REVIEW"
    NOT_JUSTIFIED_REVIEW_REQUIRED = "NOT_JUSTIFIED_REVIEW_REQUIRED"
    BLOCKED_POLICY_DISABLED = "BLOCKED_POLICY_DISABLED"


class GovernanceReportOutcome(str, Enum):
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    BUDGET_BREACH_REVIEW_REQUIRED = "BUDGET_BREACH_REVIEW_REQUIRED"
    BLOCKED_INCOMPLETE_EVIDENCE = "BLOCKED_INCOMPLETE_EVIDENCE"


@dataclass(frozen=True, slots=True)
class ReportSchedulePolicy:
    policy_id: UUID
    policy_name: str
    version: str
    report_type: GovernanceReportType
    cadence: ReportCadence
    job_policy_id: UUID
    job_policy_content_hash: str
    approved_by: str
    approved_at: datetime
    enabled: bool
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        policy_name: str,
        version: str,
        report_type: GovernanceReportType,
        job_policy: OperationalJobPolicy,
        approved_by: str,
        approved_at: datetime,
        enabled: bool = True,
    ) -> ReportSchedulePolicy:
        validate_operational_job_policy(job_policy)
        _aware(approved_at, "report_schedule_policy_time_must_be_timezone_aware")
        if (
            not policy_name.strip()
            or not version.strip()
            or not approved_by.strip()
            or approved_at < job_policy.approved_at
            or not _job_interval_matches_cadence(job_policy, REPORT_CADENCE[report_type])
        ):
            raise GovernanceReportingError("invalid_report_schedule_policy")
        draft = cls(
            uuid4(), policy_name, version, report_type, REPORT_CADENCE[report_type],
            job_policy.policy_id, job_policy.content_hash, approved_by, approved_at,
            enabled, "",
        )
        return replace(draft, content_hash=_schedule_policy_hash(draft))


@dataclass(frozen=True, slots=True)
class CostBudgetPolicy:
    policy_id: UUID
    policy_name: str
    version: str
    budget_mode: BudgetMode
    currency: str
    period_start: datetime
    period_end: datetime
    total_limit: Decimal
    category_limits: Mapping[OperationalCostCategory, Decimal]
    minimum_value_to_cost_ratio: Decimal
    approved_by: str
    approved_at: datetime
    enabled: bool
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        policy_name: str,
        version: str,
        budget_mode: BudgetMode,
        currency: str,
        period_start: datetime,
        period_end: datetime,
        total_limit: Decimal,
        category_limits: Mapping[OperationalCostCategory, Decimal],
        minimum_value_to_cost_ratio: Decimal,
        approved_by: str,
        approved_at: datetime,
        enabled: bool = True,
    ) -> CostBudgetPolicy:
        for value in (period_start, period_end, approved_at):
            _aware(value, "cost_budget_policy_time_must_be_timezone_aware")
        limits = dict(category_limits)
        if (
            not policy_name.strip()
            or not version.strip()
            or len(currency) != 3
            or currency != currency.upper()
            or period_end <= period_start
            or approved_at > period_start
            or not _fits_numeric_30_12(total_limit)
            or total_limit < 0
            or set(limits) != set(OperationalCostCategory)
            or any(not _fits_numeric_30_12(value) or value < 0 for value in limits.values())
            or sum(limits.values(), start=Decimal("0")) < total_limit
            or not _fits_numeric_30_12(minimum_value_to_cost_ratio)
            or minimum_value_to_cost_ratio < 1
            or not approved_by.strip()
        ):
            raise GovernanceReportingError("invalid_cost_budget_policy")
        draft = cls(
            uuid4(), policy_name, version, budget_mode, currency, period_start,
            period_end, total_limit, MappingProxyType(limits),
            minimum_value_to_cost_ratio, approved_by, approved_at, enabled, "",
        )
        return replace(draft, content_hash=_cost_policy_hash(draft))


@dataclass(frozen=True, slots=True)
class OperationalCostObservation:
    observation_id: UUID
    category: OperationalCostCategory
    service_reference: str
    amount: Decimal
    currency: str
    period_start: datetime
    period_end: datetime
    observed_at: datetime
    evidence_class: ReportEvidenceClass
    evidence_reference: str
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        category: OperationalCostCategory,
        service_reference: str,
        amount: Decimal,
        currency: str,
        period_start: datetime,
        period_end: datetime,
        observed_at: datetime,
        evidence_class: ReportEvidenceClass,
        evidence_reference: str,
    ) -> OperationalCostObservation:
        for value in (period_start, period_end, observed_at):
            _aware(value, "cost_observation_time_must_be_timezone_aware")
        if (
            not service_reference.strip()
            or not evidence_reference.strip()
            or not _fits_numeric_30_12(amount)
            or amount < 0
            or len(currency) != 3
            or currency != currency.upper()
            or period_end <= period_start
            or not period_start <= observed_at <= period_end
            or evidence_class not in (ReportEvidenceClass.FACT, ReportEvidenceClass.MODEL_ESTIMATE)
        ):
            raise GovernanceReportingError("invalid_operational_cost_observation")
        identity = _hash(
            {
                "category": category.value,
                "service_reference": service_reference,
                "amount": _decimal_text(amount),
                "currency": currency,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "observed_at": observed_at.isoformat(),
                "evidence_class": evidence_class.value,
                "evidence_reference": evidence_reference,
            }
        )
        draft = cls(
            uuid5(_NS, identity), category, service_reference, amount, currency,
            period_start, period_end, observed_at, evidence_class,
            evidence_reference, "",
        )
        return replace(draft, content_hash=_cost_observation_hash(draft))


@dataclass(frozen=True, slots=True)
class GovernanceReportSection:
    evidence_class: ReportEvidenceClass
    entries: tuple[str, ...]
    content_hash: str

    @classmethod
    def create(
        cls, evidence_class: ReportEvidenceClass, entries: tuple[str, ...],
    ) -> GovernanceReportSection:
        if any(not entry.strip() for entry in entries) or len(set(entries)) != len(entries):
            raise GovernanceReportingError("invalid_governance_report_section")
        draft = cls(evidence_class, tuple(sorted(entries)), "")
        return replace(draft, content_hash=_section_hash(draft))


@dataclass(frozen=True, slots=True)
class GovernanceReport:
    report_id: UUID
    schedule_policy_id: UUID
    schedule_policy_content_hash: str
    job_run_id: UUID
    job_run_content_hash: str
    report_type: GovernanceReportType
    period_start: datetime
    period_end: datetime
    generated_at: datetime
    budget_policy_id: UUID | None
    budget_policy_content_hash: str | None
    total_observed: Decimal | None
    total_limit: Decimal | None
    section_evidence_hash: str
    cost_evidence_hash: str
    outcome: GovernanceReportOutcome
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    execution_authority: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class CostValueAssessment:
    assessment_id: UUID
    budget_policy_id: UUID
    budget_policy_content_hash: str
    candidate_type: CostCandidateType
    candidate_reference: str
    evaluated_at: datetime
    incremental_cost: Decimal
    measurable_value_estimate: Decimal
    currency: str
    value_to_cost_ratio: Decimal | None
    evidence_references: tuple[str, ...]
    deterministic_alternative_available: bool
    proposed_ai_inference: bool
    outcome: CostValueOutcome
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    procurement_authority: str
    content_hash: str


def assess_cost_value(
    policy: CostBudgetPolicy,
    *,
    candidate_type: CostCandidateType,
    candidate_reference: str,
    evaluated_at: datetime,
    incremental_cost: Decimal,
    measurable_value_estimate: Decimal,
    currency: str,
    evidence_references: tuple[str, ...],
    deterministic_alternative_available: bool,
    proposed_ai_inference: bool,
) -> CostValueAssessment:
    _validate_cost_policy(policy)
    _aware(evaluated_at, "cost_value_assessment_time_must_be_timezone_aware")
    references = tuple(sorted(evidence_references))
    if (
        not candidate_reference.strip()
        or not references
        or any(not reference.strip() for reference in references)
        or len(set(references)) != len(references)
        or not _fits_numeric_30_12(incremental_cost)
        or incremental_cost < 0
        or not _fits_numeric_30_12(measurable_value_estimate)
        or measurable_value_estimate < 0
        or currency != policy.currency
        or not policy.period_start <= evaluated_at <= policy.period_end
    ):
        raise GovernanceReportingError("invalid_cost_value_assessment_input")
    ratio = None if incremental_cost == 0 else measurable_value_estimate / incremental_cost
    if ratio is not None:
        if not ratio.is_finite() or (ratio != 0 and max(ratio.adjusted() + 1, 0) > 18):
            raise GovernanceReportingError("cost_value_ratio_out_of_range")
        ratio = ratio.quantize(_DECIMAL_QUANTUM)
        if not _fits_numeric_30_12(ratio):
            raise GovernanceReportingError("cost_value_ratio_out_of_range")
    reasons: list[str] = []
    if not policy.enabled:
        outcome = CostValueOutcome.BLOCKED_POLICY_DISABLED
        reasons.append("cost_budget_policy_disabled")
    elif proposed_ai_inference and deterministic_alternative_available:
        outcome = CostValueOutcome.NOT_JUSTIFIED_REVIEW_REQUIRED
        reasons.append("deterministic_code_preferred_over_ai_inference")
    elif ratio is not None and ratio < policy.minimum_value_to_cost_ratio:
        outcome = CostValueOutcome.NOT_JUSTIFIED_REVIEW_REQUIRED
        reasons.append("minimum_value_to_cost_ratio_not_met")
    else:
        outcome = CostValueOutcome.JUSTIFIED_FOR_REVIEW
    limitations = (
        "SUPPLIED_VALUE_ESTIMATE_NOT_INDEPENDENTLY_VERIFIED",
        "NO_PURCHASE_PROVIDER_MODEL_OR_EXECUTION_AUTHORITY",
        "LIVE_TRADING_DISABLED",
    )
    identity = _hash(
        {
            "budget_policy_content_hash": policy.content_hash,
            "candidate_type": candidate_type.value,
            "candidate_reference": candidate_reference,
            "evaluated_at": evaluated_at.isoformat(),
            "incremental_cost": _decimal_text(incremental_cost),
            "measurable_value_estimate": _decimal_text(measurable_value_estimate),
            "currency": currency,
            "evidence_references": references,
            "deterministic_alternative_available": deterministic_alternative_available,
            "proposed_ai_inference": proposed_ai_inference,
        }
    )
    draft = CostValueAssessment(
        uuid5(_NS, identity), policy.policy_id, policy.content_hash,
        candidate_type, candidate_reference, evaluated_at, incremental_cost,
        measurable_value_estimate, currency, ratio, references,
        deterministic_alternative_available, proposed_ai_inference, outcome,
        tuple(reasons), limitations, "NONE", "",
    )
    return replace(draft, content_hash=_cost_value_assessment_hash(draft))


def generate_governance_report(
    schedule_policy: ReportSchedulePolicy,
    job_policy: OperationalJobPolicy,
    job_run: OperationalJobRun,
    *,
    period_start: datetime,
    period_end: datetime,
    generated_at: datetime,
    sections: Mapping[ReportEvidenceClass, tuple[str, ...]],
    cost_policy: CostBudgetPolicy | None = None,
    cost_observations: tuple[OperationalCostObservation, ...] = (),
) -> tuple[GovernanceReport, tuple[GovernanceReportSection, ...]]:
    _validate_schedule_policy(schedule_policy)
    validate_operational_job_policy(job_policy)
    validate_operational_job_run(job_run)
    for value in (period_start, period_end, generated_at):
        _aware(value, "governance_report_time_must_be_timezone_aware")
    if (
        schedule_policy.job_policy_id != job_policy.policy_id
        or schedule_policy.job_policy_content_hash != job_policy.content_hash
        or not _job_interval_matches_cadence(job_policy, schedule_policy.cadence)
        or job_run.policy_id != job_policy.policy_id
        or job_run.status is not OperationalJobStatus.SUCCEEDED
        or not schedule_policy.enabled
        or not job_policy.enabled
        or period_end <= period_start
        or not period_end <= job_run.scheduled_for <= generated_at
        or job_run.completed_at > generated_at
        or generated_at < period_end
        or schedule_policy.approved_at > job_run.scheduled_for
    ):
        raise GovernanceReportingError("invalid_governance_report_job_binding")
    if set(sections) != set(ReportEvidenceClass):
        raise GovernanceReportingError("incomplete_governance_report_sections")
    normalized_sections = tuple(
        GovernanceReportSection.create(evidence_class, tuple(sections[evidence_class]))
        for evidence_class in sorted(ReportEvidenceClass, key=lambda item: item.value)
    )
    facts = sections[ReportEvidenceClass.FACT]
    missing = sections[ReportEvidenceClass.MISSING_DATA]
    reasons: list[str] = []
    outcome = GovernanceReportOutcome.READY_FOR_REVIEW
    budget_id: UUID | None = None
    budget_hash: str | None = None
    total_observed: Decimal | None = None
    total_limit: Decimal | None = None
    ordered_costs: tuple[OperationalCostObservation, ...] = ()

    if schedule_policy.report_type is GovernanceReportType.MONTHLY_COST:
        if cost_policy is None:
            raise GovernanceReportingError("monthly_cost_report_requires_budget_policy")
        _validate_cost_policy(cost_policy)
        if (
            not cost_policy.enabled
            or cost_policy.period_start != period_start
            or cost_policy.period_end != period_end
            or cost_policy.approved_at > period_start
        ):
            raise GovernanceReportingError("invalid_monthly_cost_budget_binding")
        ordered_costs = tuple(sorted(cost_observations, key=lambda item: (item.category.value, str(item.observation_id))))
        if len({item.observation_id for item in ordered_costs}) != len(ordered_costs):
            raise GovernanceReportingError("duplicate_cost_observation")
        for item in ordered_costs:
            _validate_cost_observation(item)
            if (
                item.currency != cost_policy.currency
                or item.period_start != period_start
                or item.period_end != period_end
                or item.observed_at > generated_at
            ):
                raise GovernanceReportingError("cost_observation_report_mismatch")
        budget_id = cost_policy.policy_id
        budget_hash = cost_policy.content_hash
        total_observed = sum((item.amount for item in ordered_costs), start=Decimal("0"))
        total_limit = cost_policy.total_limit
        observed_categories = {item.category for item in ordered_costs}
        missing_categories = set(OperationalCostCategory) - observed_categories
        category_totals = {
            category: sum(
                (item.amount for item in ordered_costs if item.category is category),
                start=Decimal("0"),
            )
            for category in OperationalCostCategory
        }
        if missing_categories:
            if not missing:
                raise GovernanceReportingError("missing_cost_categories_require_disclosure")
            reasons.append("incomplete_operational_cost_category_coverage")
            outcome = GovernanceReportOutcome.BLOCKED_INCOMPLETE_EVIDENCE
        elif total_observed > total_limit or any(
            category_totals[category] > cost_policy.category_limits[category]
            for category in OperationalCostCategory
        ):
            reasons.append("operational_cost_budget_threshold_exceeded")
            outcome = GovernanceReportOutcome.BUDGET_BREACH_REVIEW_REQUIRED
    elif cost_policy is not None or cost_observations:
        raise GovernanceReportingError("cost_evidence_only_allowed_for_monthly_cost_report")

    if not facts:
        if not missing:
            raise GovernanceReportingError("factless_report_requires_missing_data_disclosure")
        reasons.append("no_fact_evidence_supplied")
        outcome = GovernanceReportOutcome.BLOCKED_INCOMPLETE_EVIDENCE

    limitations = (
        "SUPPLIED_EVIDENCE_NOT_INDEPENDENTLY_VERIFIED",
        "NO_SCHEDULER_OR_EXTERNAL_DELIVERY_AUTHORITY",
        "NO_SPENDING_RISK_OR_EXECUTION_AUTHORITY",
        "LIVE_TRADING_DISABLED",
    )
    section_hash = _hash([item.content_hash for item in normalized_sections])
    cost_hash = _hash([item.content_hash for item in ordered_costs])
    report_id = uuid5(
        _NS,
        f"{schedule_policy.content_hash}:{job_run.content_hash}:{period_start.isoformat()}:"
        f"{period_end.isoformat()}:{section_hash}:{cost_hash}:{budget_hash or ''}",
    )
    draft = GovernanceReport(
        report_id, schedule_policy.policy_id, schedule_policy.content_hash,
        job_run.run_id, job_run.content_hash, schedule_policy.report_type,
        period_start, period_end, generated_at, budget_id, budget_hash,
        total_observed, total_limit, section_hash, cost_hash, outcome,
        tuple(sorted(reasons)), limitations, "NONE", "",
    )
    return replace(draft, content_hash=_report_hash(draft)), normalized_sections


class PostgresGovernanceReportingStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append_schedule_policy(self, policy: ReportSchedulePolicy) -> None:
        _validate_schedule_policy(policy)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT content_hash,interval_seconds FROM operational_job_policy_versions WHERE policy_id=%s",
                (policy.job_policy_id,),
            )
            row = cursor.fetchone()
            if (
                row is None
                or str(row[0]) != policy.job_policy_content_hash
                or not _interval_seconds_match_cadence(int(str(row[1])), policy.cadence)
            ):
                raise GovernanceReportingError("operational_job_policy_not_registered_or_mismatched")
            cursor.execute(
                "INSERT INTO report_schedule_policy_versions VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (policy_id) DO NOTHING RETURNING content_hash",
                (
                    policy.policy_id, policy.policy_name, policy.version,
                    policy.report_type.value, policy.cadence.value, policy.job_policy_id,
                    policy.job_policy_content_hash, policy.approved_by,
                    policy.approved_at, policy.enabled, policy.content_hash,
                ),
            )
            self._verify_insert(cursor, "report_schedule_policy_versions", "policy_id", policy.policy_id, policy.content_hash)

    def append_cost_policy(self, policy: CostBudgetPolicy) -> None:
        _validate_cost_policy(policy)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO cost_budget_policy_versions VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s) "
                "ON CONFLICT (policy_id) DO NOTHING RETURNING content_hash",
                (
                    policy.policy_id, policy.policy_name, policy.version,
                    policy.budget_mode.value, policy.currency, policy.period_start,
                    policy.period_end, policy.total_limit,
                    json.dumps({key.value: _decimal_text(value) for key, value in policy.category_limits.items()}),
                    policy.minimum_value_to_cost_ratio, policy.approved_by,
                    policy.approved_at, policy.enabled, policy.content_hash,
                ),
            )
            self._verify_insert(cursor, "cost_budget_policy_versions", "policy_id", policy.policy_id, policy.content_hash)

    def append_cost_observation(self, observation: OperationalCostObservation) -> None:
        _validate_cost_observation(observation)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO operational_cost_observations VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (observation_id) DO NOTHING RETURNING content_hash",
                (
                    observation.observation_id, observation.category.value,
                    observation.service_reference, observation.amount,
                    observation.currency, observation.period_start,
                    observation.period_end, observation.observed_at,
                    observation.evidence_class.value, observation.evidence_reference,
                    observation.content_hash,
                ),
            )
            self._verify_insert(
                cursor, "operational_cost_observations", "observation_id",
                observation.observation_id, observation.content_hash,
            )

    def append_cost_value_assessment(self, assessment: CostValueAssessment) -> None:
        _validate_cost_value_assessment(assessment)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            self._verify_reference(
                cursor, "cost_budget_policy_versions", "policy_id",
                assessment.budget_policy_id, assessment.budget_policy_content_hash,
            )
            cursor.execute(
                "SELECT * FROM cost_budget_policy_versions WHERE policy_id=%s",
                (assessment.budget_policy_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise GovernanceReportingError("registered_budget_policy_disappeared")
            policy = _cost_policy_from_row(row)
            expected = assess_cost_value(
                policy,
                candidate_type=assessment.candidate_type,
                candidate_reference=assessment.candidate_reference,
                evaluated_at=assessment.evaluated_at,
                incremental_cost=assessment.incremental_cost,
                measurable_value_estimate=assessment.measurable_value_estimate,
                currency=assessment.currency,
                evidence_references=assessment.evidence_references,
                deterministic_alternative_available=assessment.deterministic_alternative_available,
                proposed_ai_inference=assessment.proposed_ai_inference,
            )
            if expected != assessment:
                raise GovernanceReportingError("cost_value_assessment_not_reproducible")
            cursor.execute(
                "INSERT INTO cost_value_assessments VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) "
                "ON CONFLICT (assessment_id) DO NOTHING RETURNING content_hash",
                (
                    assessment.assessment_id, assessment.budget_policy_id,
                    assessment.budget_policy_content_hash,
                    assessment.candidate_type.value, assessment.candidate_reference,
                    assessment.evaluated_at, assessment.incremental_cost,
                    assessment.measurable_value_estimate, assessment.currency,
                    assessment.value_to_cost_ratio,
                    json.dumps(assessment.evidence_references),
                    assessment.deterministic_alternative_available,
                    assessment.proposed_ai_inference, assessment.outcome.value,
                    json.dumps(assessment.reasons), json.dumps(assessment.limitations),
                    assessment.procurement_authority, assessment.content_hash,
                ),
            )
            self._verify_insert(
                cursor, "cost_value_assessments", "assessment_id",
                assessment.assessment_id, assessment.content_hash,
            )

    def append_report(
        self,
        report: GovernanceReport,
        sections: tuple[GovernanceReportSection, ...],
        cost_observations: tuple[OperationalCostObservation, ...] = (),
    ) -> None:
        ordered_sections = tuple(sorted(sections, key=lambda item: item.evidence_class.value))
        ordered_costs = tuple(sorted(cost_observations, key=lambda item: (item.category.value, str(item.observation_id))))
        if (
            report.content_hash != _report_hash(report)
            or {section.evidence_class for section in ordered_sections} != set(ReportEvidenceClass)
            or report.section_evidence_hash != _hash([item.content_hash for item in ordered_sections])
            or report.cost_evidence_hash != _hash([item.content_hash for item in ordered_costs])
        ):
            raise GovernanceReportingError("governance_report_evidence_mismatch")
        for section_item in ordered_sections:
            _validate_section(section_item)
        for cost_item in ordered_costs:
            _validate_cost_observation(cost_item)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            self._verify_reference(
                cursor, "report_schedule_policy_versions", "policy_id",
                report.schedule_policy_id, report.schedule_policy_content_hash,
            )
            self._verify_reference(
                cursor, "operational_job_runs", "run_id",
                report.job_run_id, report.job_run_content_hash,
            )
            if report.budget_policy_id is not None and report.budget_policy_content_hash is not None:
                self._verify_reference(
                    cursor, "cost_budget_policy_versions", "policy_id",
                    report.budget_policy_id, report.budget_policy_content_hash,
                )
            elif report.budget_policy_id is not None or report.budget_policy_content_hash is not None:
                raise GovernanceReportingError("incomplete_budget_policy_reference")
            for cost_item in ordered_costs:
                self._verify_reference(
                    cursor, "operational_cost_observations", "observation_id",
                    cost_item.observation_id, cost_item.content_hash,
                )
            cursor.execute(
                "SELECT * FROM report_schedule_policy_versions WHERE policy_id=%s",
                (report.schedule_policy_id,),
            )
            schedule_row = cursor.fetchone()
            if schedule_row is None:
                raise GovernanceReportingError("registered_schedule_policy_disappeared")
            registered_schedule = _schedule_policy_from_row(schedule_row)
            cursor.execute(
                "SELECT policy_id,job_name,version,interval_seconds,grace_seconds,owner,"
                "runbook_uri,approved_by,approved_at,enabled,content_hash FROM "
                "operational_job_policy_versions WHERE policy_id=%s",
                (registered_schedule.job_policy_id,),
            )
            job_policy_row = cursor.fetchone()
            cursor.execute(
                "SELECT run_id,policy_id,idempotency_key,scheduled_for,started_at,completed_at,"
                "status,summary,content_hash FROM operational_job_runs WHERE run_id=%s",
                (report.job_run_id,),
            )
            job_run_row = cursor.fetchone()
            if job_policy_row is None or job_run_row is None:
                raise GovernanceReportingError("registered_job_evidence_disappeared")
            registered_job_policy = _job_policy_from_row(job_policy_row)
            registered_job_run = _job_run_from_row(job_run_row)
            registered_cost_policy: CostBudgetPolicy | None = None
            if report.budget_policy_id is not None:
                cursor.execute(
                    "SELECT * FROM cost_budget_policy_versions WHERE policy_id=%s",
                    (report.budget_policy_id,),
                )
                budget_row = cursor.fetchone()
                if budget_row is None:
                    raise GovernanceReportingError("registered_budget_policy_disappeared")
                registered_cost_policy = _cost_policy_from_row(budget_row)
            expected_report, expected_sections = generate_governance_report(
                registered_schedule,
                registered_job_policy,
                registered_job_run,
                period_start=report.period_start,
                period_end=report.period_end,
                generated_at=report.generated_at,
                sections={item.evidence_class: item.entries for item in ordered_sections},
                cost_policy=registered_cost_policy,
                cost_observations=ordered_costs,
            )
            if expected_report != report or expected_sections != ordered_sections:
                raise GovernanceReportingError("governance_report_not_reproducible_from_registered_evidence")
            cursor.execute(
                "INSERT INTO governance_reports VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) "
                "ON CONFLICT (report_id) DO NOTHING RETURNING content_hash",
                (
                    report.report_id, report.schedule_policy_id,
                    report.schedule_policy_content_hash, report.job_run_id,
                    report.job_run_content_hash, report.report_type.value,
                    report.period_start, report.period_end, report.generated_at,
                    report.budget_policy_id, report.budget_policy_content_hash,
                    report.total_observed, report.total_limit,
                    report.section_evidence_hash, report.cost_evidence_hash,
                    report.outcome.value, json.dumps(report.reasons),
                    json.dumps(report.limitations), report.execution_authority,
                    report.content_hash,
                ),
            )
            self._verify_insert(cursor, "governance_reports", "report_id", report.report_id, report.content_hash)
            for section_item in ordered_sections:
                cursor.execute(
                    "INSERT INTO governance_report_sections VALUES (%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING",
                    (
                        report.report_id, section_item.evidence_class.value,
                        json.dumps(section_item.entries), section_item.content_hash,
                    ),
                )
            for cost_item in ordered_costs:
                cursor.execute(
                    "INSERT INTO governance_report_cost_observations VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (report.report_id, cost_item.observation_id),
                )

    def schedule_policy(self, policy_id: UUID) -> ReportSchedulePolicy:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM report_schedule_policy_versions WHERE policy_id=%s", (policy_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(str(policy_id))
        policy = _schedule_policy_from_row(row)
        _validate_schedule_policy(policy)
        return policy

    def cost_policy(self, policy_id: UUID) -> CostBudgetPolicy:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM cost_budget_policy_versions WHERE policy_id=%s", (policy_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(str(policy_id))
        policy = _cost_policy_from_row(row)
        _validate_cost_policy(policy)
        return policy

    def cost_value_assessment(self, assessment_id: UUID) -> CostValueAssessment:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM cost_value_assessments WHERE assessment_id=%s",
                (assessment_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(str(assessment_id))
        assessment = CostValueAssessment(
            UUID(str(row[0])), UUID(str(row[1])), str(row[2]),
            CostCandidateType(str(row[3])), str(row[4]), cast(datetime, row[5]),
            Decimal(str(row[6])), Decimal(str(row[7])), str(row[8]),
            Decimal(str(row[9])) if row[9] is not None else None,
            tuple(cast(list[str], row[10])), bool(row[11]), bool(row[12]),
            CostValueOutcome(str(row[13])), tuple(cast(list[str], row[14])),
            tuple(cast(list[str], row[15])), str(row[16]), str(row[17]),
        )
        _validate_cost_value_assessment(assessment)
        return assessment

    def report(self, report_id: UUID) -> GovernanceReport:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM governance_reports WHERE report_id=%s", (report_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(str(report_id))
        report = GovernanceReport(
            UUID(str(row[0])), UUID(str(row[1])), str(row[2]), UUID(str(row[3])),
            str(row[4]), GovernanceReportType(str(row[5])), cast(datetime, row[6]),
            cast(datetime, row[7]), cast(datetime, row[8]),
            UUID(str(row[9])) if row[9] is not None else None,
            str(row[10]) if row[10] is not None else None,
            Decimal(str(row[11])) if row[11] is not None else None,
            Decimal(str(row[12])) if row[12] is not None else None,
            str(row[13]), str(row[14]), GovernanceReportOutcome(str(row[15])),
            tuple(cast(list[str], row[16])), tuple(cast(list[str], row[17])),
            str(row[18]), str(row[19]),
        )
        sections = self.report_sections(report_id)
        costs = self.report_cost_observations(report_id)
        if (
            report.content_hash != _report_hash(report)
            or report.section_evidence_hash != _hash([item.content_hash for item in sections])
            or report.cost_evidence_hash != _hash([item.content_hash for item in costs])
        ):
            raise GovernanceReportingError("governance_report_hash_mismatch")
        return report

    def report_sections(self, report_id: UUID) -> tuple[GovernanceReportSection, ...]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT evidence_class,entries,content_hash FROM governance_report_sections "
                "WHERE report_id=%s ORDER BY evidence_class", (report_id,),
            )
            rows = cursor.fetchall()
        sections = tuple(
            GovernanceReportSection(ReportEvidenceClass(str(row[0])), tuple(cast(list[str], row[1])), str(row[2]))
            for row in rows
        )
        if {item.evidence_class for item in sections} != set(ReportEvidenceClass):
            raise GovernanceReportingError("incomplete_persisted_report_sections")
        for item in sections:
            _validate_section(item)
        return sections

    def report_cost_observations(self, report_id: UUID) -> tuple[OperationalCostObservation, ...]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT o.* FROM operational_cost_observations o "
                "JOIN governance_report_cost_observations l ON l.observation_id=o.observation_id "
                "WHERE l.report_id=%s ORDER BY o.category,o.observation_id", (report_id,),
            )
            rows = cursor.fetchall()
        observations = tuple(self._cost_observation_from_row(row) for row in rows)
        for item in observations:
            _validate_cost_observation(item)
        return observations

    @staticmethod
    def _cost_observation_from_row(row: tuple[object, ...]) -> OperationalCostObservation:
        return OperationalCostObservation(
            UUID(str(row[0])), OperationalCostCategory(str(row[1])), str(row[2]),
            Decimal(str(row[3])), str(row[4]), cast(datetime, row[5]),
            cast(datetime, row[6]), cast(datetime, row[7]),
            ReportEvidenceClass(str(row[8])), str(row[9]), str(row[10]),
        )

    @staticmethod
    def _verify_insert(
        cursor: Any, table: str, id_column: str, identity: UUID, content_hash: str,
    ) -> None:
        if cursor.fetchone() is not None:
            return
        query = sql.SQL("SELECT content_hash FROM {} WHERE {}=%s").format(
            sql.Identifier(table), sql.Identifier(id_column),
        )
        cursor.execute(query, (identity,))
        row = cursor.fetchone()
        if row is None or str(row[0]) != content_hash:
            raise GovernanceReportingError(f"conflicting_{table}")

    @staticmethod
    def _verify_reference(
        cursor: Any, table: str, id_column: str, identity: UUID, content_hash: str,
    ) -> None:
        query = sql.SQL("SELECT content_hash FROM {} WHERE {}=%s").format(
            sql.Identifier(table), sql.Identifier(id_column),
        )
        cursor.execute(query, (identity,))
        row = cursor.fetchone()
        if row is None or str(row[0]) != content_hash:
            raise GovernanceReportingError(f"{table}_not_registered_or_mismatched")


def _schedule_policy_from_row(row: tuple[object, ...]) -> ReportSchedulePolicy:
    return ReportSchedulePolicy(
        UUID(str(row[0])), str(row[1]), str(row[2]), GovernanceReportType(str(row[3])),
        ReportCadence(str(row[4])), UUID(str(row[5])), str(row[6]), str(row[7]),
        cast(datetime, row[8]), bool(row[9]), str(row[10]),
    )


def _cost_policy_from_row(row: tuple[object, ...]) -> CostBudgetPolicy:
    raw_limits = cast(dict[str, str], row[8])
    return CostBudgetPolicy(
        UUID(str(row[0])), str(row[1]), str(row[2]), BudgetMode(str(row[3])),
        str(row[4]), cast(datetime, row[5]), cast(datetime, row[6]), Decimal(str(row[7])),
        MappingProxyType({
            OperationalCostCategory(key): Decimal(str(value))
            for key, value in raw_limits.items()
        }),
        Decimal(str(row[9])), str(row[10]), cast(datetime, row[11]),
        bool(row[12]), str(row[13]),
    )


def _job_policy_from_row(row: tuple[object, ...]) -> OperationalJobPolicy:
    return OperationalJobPolicy(
        UUID(str(row[0])), str(row[1]), str(row[2]), int(str(row[3])), int(str(row[4])),
        str(row[5]), str(row[6]), str(row[7]), cast(datetime, row[8]),
        bool(row[9]), str(row[10]),
    )


def _job_run_from_row(row: tuple[object, ...]) -> OperationalJobRun:
    return OperationalJobRun(
        UUID(str(row[0])), UUID(str(row[1])), str(row[2]), cast(datetime, row[3]),
        cast(datetime, row[4]), cast(datetime, row[5]), OperationalJobStatus(str(row[6])),
        dict(cast(dict[str, str], row[7])), str(row[8]),
    )


def _job_interval_matches_cadence(
    job_policy: OperationalJobPolicy, cadence: ReportCadence,
) -> bool:
    return _interval_seconds_match_cadence(job_policy.interval_seconds, cadence)


def _interval_seconds_match_cadence(interval_seconds: int, cadence: ReportCadence) -> bool:
    bounds = _CADENCE_INTERVAL_BOUNDS[cadence]
    return bounds is None or bounds[0] <= interval_seconds <= bounds[1]


def _validate_schedule_policy(policy: ReportSchedulePolicy) -> None:
    _aware(policy.approved_at, "report_schedule_policy_time_must_be_timezone_aware")
    if (
        not policy.policy_name.strip()
        or not policy.version.strip()
        or not policy.approved_by.strip()
        or policy.cadence is not REPORT_CADENCE[policy.report_type]
        or policy.content_hash != _schedule_policy_hash(policy)
    ):
        raise GovernanceReportingError("invalid_or_tampered_report_schedule_policy")


def _validate_cost_policy(policy: CostBudgetPolicy) -> None:
    for value in (policy.period_start, policy.period_end, policy.approved_at):
        _aware(value, "cost_budget_policy_time_must_be_timezone_aware")
    if (
        not policy.policy_name.strip()
        or not policy.version.strip()
        or len(policy.currency) != 3
        or policy.currency != policy.currency.upper()
        or policy.period_end <= policy.period_start
        or policy.approved_at > policy.period_start
        or not _fits_numeric_30_12(policy.total_limit)
        or policy.total_limit < 0
        or set(policy.category_limits) != set(OperationalCostCategory)
        or any(not _fits_numeric_30_12(value) or value < 0 for value in policy.category_limits.values())
        or sum(policy.category_limits.values(), start=Decimal("0")) < policy.total_limit
        or not _fits_numeric_30_12(policy.minimum_value_to_cost_ratio)
        or policy.minimum_value_to_cost_ratio < 1
        or not policy.approved_by.strip()
        or policy.content_hash != _cost_policy_hash(policy)
    ):
        raise GovernanceReportingError("invalid_or_tampered_cost_budget_policy")


def _validate_cost_observation(observation: OperationalCostObservation) -> None:
    for value in (observation.period_start, observation.period_end, observation.observed_at):
        _aware(value, "cost_observation_time_must_be_timezone_aware")
    if (
        not observation.service_reference.strip()
        or not observation.evidence_reference.strip()
        or not _fits_numeric_30_12(observation.amount)
        or observation.amount < 0
        or len(observation.currency) != 3
        or observation.currency != observation.currency.upper()
        or observation.period_end <= observation.period_start
        or not observation.period_start <= observation.observed_at <= observation.period_end
        or observation.evidence_class not in (ReportEvidenceClass.FACT, ReportEvidenceClass.MODEL_ESTIMATE)
        or observation.content_hash != _cost_observation_hash(observation)
    ):
        raise GovernanceReportingError("invalid_or_tampered_operational_cost_observation")


def _validate_cost_value_assessment(assessment: CostValueAssessment) -> None:
    _aware(assessment.evaluated_at, "cost_value_assessment_time_must_be_timezone_aware")
    if (
        not assessment.candidate_reference.strip()
        or not assessment.evidence_references
        or any(not reference.strip() for reference in assessment.evidence_references)
        or assessment.evidence_references != tuple(sorted(assessment.evidence_references))
        or len(set(assessment.evidence_references)) != len(assessment.evidence_references)
        or not _fits_numeric_30_12(assessment.incremental_cost)
        or assessment.incremental_cost < 0
        or not _fits_numeric_30_12(assessment.measurable_value_estimate)
        or assessment.measurable_value_estimate < 0
        or (assessment.incremental_cost == 0) != (assessment.value_to_cost_ratio is None)
        or (
            assessment.value_to_cost_ratio is not None
            and (
                not _fits_numeric_30_12(assessment.value_to_cost_ratio)
                or assessment.value_to_cost_ratio < 0
            )
        )
        or assessment.procurement_authority != "NONE"
        or assessment.content_hash != _cost_value_assessment_hash(assessment)
    ):
        raise GovernanceReportingError("invalid_or_tampered_cost_value_assessment")


def _validate_section(section: GovernanceReportSection) -> None:
    if (
        any(not entry.strip() for entry in section.entries)
        or len(set(section.entries)) != len(section.entries)
        or section.entries != tuple(sorted(section.entries))
        or section.content_hash != _section_hash(section)
    ):
        raise GovernanceReportingError("invalid_or_tampered_governance_report_section")


def _schedule_policy_hash(policy: ReportSchedulePolicy) -> str:
    return _hash(
        {
            "policy_name": policy.policy_name,
            "version": policy.version,
            "report_type": policy.report_type.value,
            "cadence": policy.cadence.value,
            "job_policy_id": str(policy.job_policy_id),
            "job_policy_content_hash": policy.job_policy_content_hash,
            "approved_by": policy.approved_by,
            "approved_at": policy.approved_at.isoformat(),
            "enabled": policy.enabled,
        }
    )


def _cost_policy_hash(policy: CostBudgetPolicy) -> str:
    return _hash(
        {
            "policy_name": policy.policy_name,
            "version": policy.version,
            "budget_mode": policy.budget_mode.value,
            "currency": policy.currency,
            "period_start": policy.period_start.isoformat(),
            "period_end": policy.period_end.isoformat(),
            "total_limit": _decimal_text(policy.total_limit),
            "category_limits": {
                key.value: _decimal_text(value) for key, value in sorted(policy.category_limits.items(), key=lambda item: item[0].value)
            },
            "minimum_value_to_cost_ratio": _decimal_text(policy.minimum_value_to_cost_ratio),
            "approved_by": policy.approved_by,
            "approved_at": policy.approved_at.isoformat(),
            "enabled": policy.enabled,
        }
    )


def _cost_value_assessment_hash(assessment: CostValueAssessment) -> str:
    return _hash(
        {
            "assessment_id": str(assessment.assessment_id),
            "budget_policy_id": str(assessment.budget_policy_id),
            "budget_policy_content_hash": assessment.budget_policy_content_hash,
            "candidate_type": assessment.candidate_type.value,
            "candidate_reference": assessment.candidate_reference,
            "evaluated_at": assessment.evaluated_at.isoformat(),
            "incremental_cost": _decimal_text(assessment.incremental_cost),
            "measurable_value_estimate": _decimal_text(assessment.measurable_value_estimate),
            "currency": assessment.currency,
            "value_to_cost_ratio": (
                _decimal_text(assessment.value_to_cost_ratio)
                if assessment.value_to_cost_ratio is not None
                else None
            ),
            "evidence_references": assessment.evidence_references,
            "deterministic_alternative_available": assessment.deterministic_alternative_available,
            "proposed_ai_inference": assessment.proposed_ai_inference,
            "outcome": assessment.outcome.value,
            "reasons": assessment.reasons,
            "limitations": assessment.limitations,
            "procurement_authority": assessment.procurement_authority,
        }
    )


def _cost_observation_hash(observation: OperationalCostObservation) -> str:
    return _hash(
        {
            "observation_id": str(observation.observation_id),
            "category": observation.category.value,
            "service_reference": observation.service_reference,
            "amount": _decimal_text(observation.amount),
            "currency": observation.currency,
            "period_start": observation.period_start.isoformat(),
            "period_end": observation.period_end.isoformat(),
            "observed_at": observation.observed_at.isoformat(),
            "evidence_class": observation.evidence_class.value,
            "evidence_reference": observation.evidence_reference,
        }
    )


def _section_hash(section: GovernanceReportSection) -> str:
    return _hash({"evidence_class": section.evidence_class.value, "entries": section.entries})


def _report_hash(report: GovernanceReport) -> str:
    return _hash(
        {
            "report_id": str(report.report_id),
            "schedule_policy_id": str(report.schedule_policy_id),
            "schedule_policy_content_hash": report.schedule_policy_content_hash,
            "job_run_id": str(report.job_run_id),
            "job_run_content_hash": report.job_run_content_hash,
            "report_type": report.report_type.value,
            "period_start": report.period_start.isoformat(),
            "period_end": report.period_end.isoformat(),
            "generated_at": report.generated_at.isoformat(),
            "budget_policy_id": str(report.budget_policy_id) if report.budget_policy_id else None,
            "budget_policy_content_hash": report.budget_policy_content_hash,
            "total_observed": _decimal_text(report.total_observed) if report.total_observed is not None else None,
            "total_limit": _decimal_text(report.total_limit) if report.total_limit is not None else None,
            "section_evidence_hash": report.section_evidence_hash,
            "cost_evidence_hash": report.cost_evidence_hash,
            "outcome": report.outcome.value,
            "reasons": report.reasons,
            "limitations": report.limitations,
            "execution_authority": report.execution_authority,
        }
    )


def _aware(value: datetime, message: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GovernanceReportingError(message)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise GovernanceReportingError("non_finite_decimal")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _fits_numeric_30_12(value: Decimal) -> bool:
    if not value.is_finite():
        return False
    normalized = value.normalize()
    exponent = normalized.as_tuple().exponent
    if not isinstance(exponent, int):
        return False
    fractional_digits = max(-exponent, 0)
    integer_digits = max(normalized.adjusted() + 1, 0) if normalized != 0 else 1
    return fractional_digits <= 12 and integer_digits <= 18


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_NS = UUID("b932d05a-6410-44cb-b4e4-132e5217b15b")
_DECIMAL_QUANTUM = Decimal("0.000000000001")
