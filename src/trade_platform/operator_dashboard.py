"""Bounded, read-only PostgreSQL query models for the operator dashboard.

This module projects the immutable Cycle 200--207 authorities.  It contains no
calculation, provider access, job trigger, order path, or mutation authority.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from threading import RLock
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from .persistence import PostgresDatabase

Availability = Literal["AVAILABLE", "UNAVAILABLE", "STALE", "BLOCKED", "ERROR", "EXTERNAL_BLOCKED"]
MetricEvidence = Literal["MEASURED", "ASSUMED", "UNAVAILABLE"]


class DashboardQueryError(RuntimeError):
    """Fail-closed read-layer error with no database details in its message."""


class DashboardObjectNotFound(DashboardQueryError):
    pass


class PageInfo(BaseModel):
    limit: int
    offset: int
    returned: int
    has_more: bool


class DashboardWorkspaceReferences(BaseModel):
    """Small, safe default-selection projection for the dashboard server.

    This is deliberately not a database search API: every field is a typed
    authority reference selected by an explicit timestamp/identity ordering.
    """

    state: Availability
    as_of: datetime | None
    feature_definition_id: UUID | None
    feature_instrument: str | None
    feature_dataset_version: str | None
    feature_decision_time: datetime | None
    scorecard_id: UUID | None
    regime_run_id: UUID | None
    portfolio_construction_run_id: UUID | None
    sre_service_version_id: UUID | None
    news_instrument: str | None
    instrument_id: str | None
    strategy_id: UUID | None
    experiment_id: UUID | None
    investment_thesis_id: UUID | None
    investment_portfolio_id: str | None
    paper_intent_id: UUID | None
    paper_account_id: str | None
    source: str = "postgresql_authoritative_discovery"
    limitations: list[str]


class InstrumentDiscoveryView(BaseModel):
    instrument_id: str
    canonical_symbol: str
    asset_class: str
    venue: str
    lifecycle_status: str
    valid_from: datetime
    valid_until: datetime | None
    synthetic_demo: bool
    latest_dataset_version: str | None
    identifier_mapping_count: int
    ambiguous_mapping: bool


class InstrumentDiscoveryPage(BaseModel):
    state: Availability
    items: list[InstrumentDiscoveryView]
    page: PageInfo


class IdentifierMappingView(BaseModel):
    mapping_id: UUID
    source_kind: str
    namespace: str
    value: str
    valid_from: datetime
    valid_until: datetime | None
    source_reference: str
    ingested_at: datetime


class SymbolMappingView(BaseModel):
    mapping_id: UUID
    venue: str
    symbol: str
    valid_from: datetime
    valid_until: datetime | None
    source_reference: str
    ingested_at: datetime


class LifecycleEventView(BaseModel):
    event_id: UUID
    status: str
    effective_at: datetime
    ingested_at: datetime
    reason: str


class InstrumentDetailView(BaseModel):
    instrument_id: str
    canonical_symbol: str
    asset_class: str
    instrument_type: str
    exchange_name: str
    venue: str
    mic: str | None
    base_currency: str
    quote_currency: str
    settlement_currency: str
    contract_multiplier: str
    contract_size: str
    tick_size: str
    lot_size: str
    price_precision: int
    quantity_precision: int
    trading_timezone: str
    market_session_type: str
    representation_kind: str
    isin: str | None
    cusip: str | None
    registered_at: datetime
    lifecycle_status: str
    synthetic_demo: bool
    ambiguous_mapping: bool
    identifier_mappings: list[IdentifierMappingView]
    symbol_mappings: list[SymbolMappingView]
    lifecycle_events: list[LifecycleEventView]
    dataset_versions: list[str]


class HistoricalDatasetView(BaseModel):
    dataset_version_id: UUID
    source_id: UUID
    version: str
    normalization_version: str
    content_hash: str
    valid_from: datetime
    valid_until: datetime | None
    created_at: datetime
    status: str
    provider: str
    dataset_name: str
    asset_scope: str
    provider_terms_version: str
    authorization_reference: str
    authorized_at: datetime
    observation_count: int
    checkpoint_state: str | None
    synthetic_demo: bool


class HistoricalDatasetPage(BaseModel):
    state: Availability
    items: list[HistoricalDatasetView]
    page: PageInfo


class DataHealthFindingView(BaseModel):
    finding_id: UUID
    sequence: int
    check_type: str
    action: str
    observed_at: datetime | None
    detail: dict[str, Any]
    content_hash: str


class DataHealthAssessmentView(BaseModel):
    assessment_id: UUID
    dataset_version_id: UUID | None
    dataset_version: str | None
    scope_type: str
    scope_value: str
    policy_version: str
    evaluated_at: datetime
    expected_start: datetime
    expected_end: datetime
    max_action: str
    blocking: bool
    content_hash: str
    summary: dict[str, Any]
    findings: list[DataHealthFindingView]
    synthetic_demo: bool


class DataHealthAssessmentPage(BaseModel):
    state: Availability
    overall_state: str
    total_assessments: int
    blocking_count: int
    items: list[DataHealthAssessmentView]
    page: PageInfo


class StrategyDiscoveryView(BaseModel):
    strategy_id: UUID
    strategy_version_id: UUID
    version: str
    family: str
    hypothesis: str
    status: str
    dataset_requirements: list[str]
    feature_versions: list[str]
    cost_model_version: str
    created_at: datetime
    evidence_classification: str


class StrategyDiscoveryPage(BaseModel):
    state: Availability
    items: list[StrategyDiscoveryView]
    page: PageInfo


class ExperimentDiscoveryView(BaseModel):
    experiment_id: UUID
    strategy_id: UUID
    strategy_version_id: UUID
    strategy_version: str
    dataset_version: str
    feature_versions: list[str]
    cost_model_version: str
    created_at: datetime
    evaluated_at: datetime | None
    status: str
    evidence_classification: str


class ExperimentDiscoveryPage(BaseModel):
    state: Availability
    items: list[ExperimentDiscoveryView]
    page: PageInfo


class InvestmentThesisDiscoveryView(BaseModel):
    thesis_id: UUID
    instrument_id: str
    canonical_symbol: str | None
    thesis_version: str
    status: str
    as_of: datetime
    review_state: str | None
    synthetic_demo: bool
    evidence_classification: str


class InvestmentThesisDiscoveryPage(BaseModel):
    state: Availability
    items: list[InvestmentThesisDiscoveryView]
    page: PageInfo


class InvestmentPortfolioDiscoveryView(BaseModel):
    portfolio_id: str
    as_of: datetime
    review_status: str
    holdings_count: int
    evidence_classification: str


class InvestmentPortfolioDiscoveryPage(BaseModel):
    state: Availability
    items: list[InvestmentPortfolioDiscoveryView]
    page: PageInfo


class PaperOrderDiscoveryView(BaseModel):
    account_id: str
    intent_id: UUID
    instrument_id: str
    canonical_symbol: str | None
    side: str
    quantity: str
    paper_only: bool = True
    lifecycle_status: str
    created_at: datetime
    fill_state: str
    reconciliation_state: str


class PaperOrderDiscoveryPage(BaseModel):
    state: Availability
    items: list[PaperOrderDiscoveryView]
    page: PageInfo


class PaperOrderEventView(BaseModel):
    event_id: UUID
    event_type: str
    occurred_at: datetime


class PaperFillView(BaseModel):
    fill_id: UUID
    external_fill_id: str
    quantity: str
    price: str
    occurred_at: datetime


class PaperOrderView(BaseModel):
    paper_only: Literal[True] = True
    intent_id: UUID
    account_id: str
    instrument_id: str
    canonical_symbol: str | None
    side: str
    quantity: str
    limit_price: str
    status: str
    filled_quantity: str
    average_fill_price: str | None
    created_at: datetime
    events: list[PaperOrderEventView]
    fills: list[PaperFillView]


class PaperReconciledAccountView(BaseModel):
    evidence_id: UUID
    healthy: bool
    as_of: datetime
    cash_currency: str
    cash_amount: str
    buying_power: str


class PaperReconciliationView(BaseModel):
    paper_only: Literal[True] = True
    account_id: str
    source: str
    occurred_at: datetime
    complete: bool
    discrepancies: list[str]
    reconciled_account: PaperReconciledAccountView | None


class FeatureDefinitionView(BaseModel):
    feature_definition_id: UUID
    feature_name: str
    family: str
    semantic_version: str
    status: str
    required_dataset_types: list[str]
    required_fields: list[str]
    frequency: str
    timestamp_semantics: str
    lookback: int
    parameters: dict[str, str | int | float | bool | None]
    missing_value_policy: str
    outlier_policy: str
    leakage_policy: str
    units: str
    calculation_version: str
    created_at: datetime
    retired_at: datetime | None


class FeatureDefinitionPage(BaseModel):
    state: Availability
    items: list[FeatureDefinitionView]
    page: PageInfo


class FeatureMaterializationView(BaseModel):
    materialization_id: UUID
    instrument: str
    feature_definition_id: UUID
    feature_name: str
    semantic_version: str
    dataset_version: str
    event_time: datetime
    effective_time: datetime
    knowledge_time: datetime
    computed_time: datetime
    value: str | None
    quality_state: str
    content_hash: str
    source_manifest: list[str]


class FeatureMaterializationPage(BaseModel):
    state: Availability
    decision_time: datetime
    items: list[FeatureMaterializationView]
    page: PageInfo


class SignalLifecycleEventView(BaseModel):
    event_id: UUID
    from_status: str
    to_status: str
    actor: str
    reason: str
    evidence_references: list[str]
    occurred_at: datetime


class SignalView(BaseModel):
    signal_id: UUID
    instrument: str
    strategy_version: str
    direction: str
    status: str
    expiry_state: Literal["CURRENT", "OVERDUE", "EXPIRED"]
    created_at: datetime
    expires_at: datetime
    strength: str
    confidence: str
    data_quality_score: str
    explanation: str
    contradicting_evidence: list[str]
    validation_id: UUID | None
    passed_stages: list[str]
    failed_stages: list[str]
    latest_reason: str
    lifecycle: list[SignalLifecycleEventView]
    evidence_classification: str
    research_or_paper_only: Literal[True]
    automatic_authority: Literal[False]


class SignalPage(BaseModel):
    state: Availability
    as_of: datetime
    items: list[SignalView]
    page: PageInfo


class RiskDecisionView(BaseModel):
    risk_decision_id: UUID
    intent_id: UUID
    policy_version_id: UUID
    policy_name: str
    policy_version: str
    policy_content_hash: str
    policy_limits: dict[str, Any]
    approved: bool
    reasons: list[str]
    decided_at: datetime
    reservation_id: UUID | None
    account_id: str | None
    business_date: str | None
    reserved_notional: str | None
    reservation_created_at: datetime | None
    research_or_paper_only: Literal[True]
    automatic_authority: Literal[False]


class RiskDecisionPage(BaseModel):
    state: Availability
    items: list[RiskDecisionView]
    page: PageInfo


class ScorecardMetricView(BaseModel):
    metric_id: UUID
    family: str
    name: str
    value: str | None
    unit: str
    evidence_state: MetricEvidence
    dimensions: list[str]
    evidence_reference: str


class ScorecardGroupView(BaseModel):
    name: str
    metrics: list[ScorecardMetricView]


class ScorecardComponentView(BaseModel):
    component_id: UUID
    name: str
    formula_version: str
    value: str | None
    rationale: str


class StrategyScorecardView(BaseModel):
    scorecard_id: UUID
    schema_version: str
    strategy_id: UUID
    strategy_version: str
    research_run_id: UUID
    dataset_version: str
    feature_versions: list[str]
    cost_model_version: str
    evaluated_at: datetime
    knowledge_cutoff: datetime
    status: str
    limitations: list[str]
    dataset_health_status: str
    validation_package_id: UUID | None
    validation_package_content_hash: str | None
    evidence_classification: str
    evidence_manifest_references: list[str]
    content_hash: str
    groups: list[ScorecardGroupView]
    complexity_components: list[ScorecardComponentView]


class StrategyScorecardDiscoveryView(BaseModel):
    scorecard_id: UUID
    strategy_id: UUID
    strategy_version: str
    research_run_id: UUID
    dataset_version: str
    evaluated_at: datetime
    status: str
    dataset_health_status: str
    evidence_classification: str


class StrategyScorecardDiscoveryPage(BaseModel):
    state: Availability
    items: list[StrategyScorecardDiscoveryView]
    page: PageInfo


class RegimeProbabilityView(BaseModel):
    state: str
    probability: str


class RegimeDimensionView(BaseModel):
    observation_id: UUID
    event_time: datetime
    method: str
    dimension: str
    evidence_state: str
    hard_label: str | None
    probabilities: list[RegimeProbabilityView]
    uncertainty: str | None
    input_materialization_ids: list[UUID]
    content_hash: str


class RegimeRiskEffectView(BaseModel):
    candidate_id: UUID
    strategy_version_id: UUID
    current_risk_multiplier: str
    proposed_risk_multiplier: str
    preapproved_maximum: str
    action: str
    status: str
    reasons: list[str]
    automatic_authority: Literal[False]


class RegimeRunView(BaseModel):
    regime_assessment_id: UUID
    model_version_id: UUID
    model_version: str
    rule_version: str
    dataset_version: str
    instrument: str
    as_of_timestamp: datetime
    knowledge_timestamp: datetime | None
    status: str
    limitations: list[str]
    evidence_hash: str
    dimensions: list[RegimeDimensionView]
    risk_effects: list[RegimeRiskEffectView]
    risk_boundary: Literal["REGIME MAY REDUCE OR BLOCK RISK; REGIME CANNOT INCREASE GLOBAL RISK LIMITS"]


class RegimeRunDimensionSummaryView(BaseModel):
    dimension: str
    hard_label: str | None
    top_probability_state: str | None
    top_probability: str | None
    uncertainty: str | None


class RegimeRunDiscoveryView(BaseModel):
    run_id: UUID
    model_version_id: UUID
    model_version: str
    rule_version: str
    dataset_version: str
    instrument: str
    as_of_timestamp: datetime
    status: str
    dimension_summary: list[RegimeRunDimensionSummaryView]
    uncertainty_summary: str


class RegimeRunDiscoveryPage(BaseModel):
    state: Availability
    items: list[RegimeRunDiscoveryView]
    page: PageInfo


class PortfolioSleeveView(BaseModel):
    sleeve_input_id: UUID
    strategy_key: str
    requested_allocation: str
    review_allocation: str | None
    effective_notional: str | None
    risk_budget: str
    capacity_weight: str
    liquidity_score: str
    drawdown: str
    regime_current_multiplier: str
    regime_proposed_multiplier: str
    marginal_risk: str | None
    component_risk: str | None
    adjustment_reasons: list[str]
    rejected: bool
    rejection_reasons: list[str]


class PortfolioConstraintView(BaseModel):
    constraint_id: UUID
    name: str
    state: str
    observed: str | None
    limit: str | None
    reasons: list[str]


class CovarianceEvidenceView(BaseModel):
    covariance_id: UUID
    dataset_version: str
    dataset_content_hash: str
    estimation_version: str
    observations: int
    as_of: datetime
    uncertainty: str
    correlation_stress: str
    source_provider: str
    source_terms_version: str
    provider_backed: bool
    classification: str


class PortfolioConstructionView(BaseModel):
    portfolio_construction_run_id: UUID
    policy_version_id: UUID
    policy_version: str
    regime_run_id: UUID
    constructed_at: datetime
    status: str
    review_only: Literal[True]
    automatic_authority: Literal[False]
    equity: str
    target_volatility: str | None
    cash_weight: str
    gross_weight: str
    net_weight: str
    portfolio_volatility: str
    stressed_volatility: str
    risk_gate_approved: bool
    risk_gate_reasons: list[str]
    limitations: list[str]
    content_hash: str
    covariance: CovarianceEvidenceView
    sleeves: list[PortfolioSleeveView]
    constraints: list[PortfolioConstraintView]


class PortfolioConstructionDiscoveryView(BaseModel):
    run_id: UUID
    policy_version_id: UUID
    policy_version: str
    regime_run_id: UUID
    constructed_at: datetime
    status: str
    review_only: Literal[True]
    automatic_authority: Literal[False]
    equity: str
    target_volatility: str | None
    portfolio_volatility: str
    stressed_volatility: str
    risk_gate_approved: bool


class PortfolioConstructionDiscoveryPage(BaseModel):
    state: Availability
    items: list[PortfolioConstructionDiscoveryView]
    page: PageInfo


class NewsEntityView(BaseModel):
    entity_link_id: UUID
    instrument: str
    method: str
    confidence: str
    ambiguous: bool


class NewsLineageView(BaseModel):
    predecessor_id: UUID
    successor_id: UUID
    relation: str


class NewsEventView(BaseModel):
    event_id: UUID
    document_revision_id: UUID
    source: str
    source_version: str
    source_terms_version: str
    published_at: datetime
    source_updated_at: datetime
    ingested_at: datetime
    correction_or_retraction_at: datetime | None
    revision: int
    revision_kind: str
    headline: str
    category: str
    novelty: str
    credibility: str | None
    uncertainty: str
    urgency: str
    horizon: str
    assessment_status: str | None
    rights_state: str
    authorization_state: str
    provider_activated: bool
    content_fingerprint: str
    provenance_reference: str
    limitations: list[str]
    entities: list[NewsEntityView]
    correction_chain: list[NewsLineageView]


class NewsEventPage(BaseModel):
    state: Availability
    provider_state: str
    items: list[NewsEventView]
    page: PageInfo


class DependencyHealthView(BaseModel):
    dependency: str
    status: str
    checked_at: datetime
    latency_ms: str | None
    reason: str | None


class SloEvidenceView(BaseModel):
    slo_policy_version_id: UUID
    name: str
    indicator: str
    target: str
    target_state: Literal["TARGET"]
    window_seconds: int
    measured_value: str | None
    measured_state: str
    window_start: datetime | None
    window_end: datetime | None
    claim_status: str | None


class IncidentView(BaseModel):
    incident_id: UUID
    severity: str
    subsystem: str
    opened_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    status: str
    reason: str
    evidence_reference: str


class FailureDrillView(BaseModel):
    drill_run_id: UUID
    scenario: str
    expected_protection: str
    observed_protection: str
    completed_at: datetime
    passed: bool
    evidence_reference: str


class SreOverviewView(BaseModel):
    state: Availability
    service_version_id: UUID
    subsystem: str
    version: str
    environment: str
    deployment_status: str
    postgres_state: str
    provider_state: str
    ingestion_checkpoint_freshness: str
    dataset_freshness: str
    feature_freshness: str
    research_job_health: str
    signal_freshness: str
    risk_status: str
    reconciliation_status: str
    backup_restore_status: str
    kill_switch_state: str
    dependencies: list[DependencyHealthView]
    slos: list[SloEvidenceView]
    incidents: list[IncidentView]
    failure_drills: list[FailureDrillView]


class AuthoritySummary(BaseModel):
    id: str
    status: Availability
    as_of: datetime | None
    evidence_id: str | None
    detail: str


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ()) -> object: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


def _json(value: object, default: Any) -> Any:
    if value is None:
        return default
    return value if isinstance(value, (dict, list)) else json.loads(str(value))


def _strings(value: object) -> list[str]:
    return [str(item) for item in _json(value, [])]


def _mapping(value: object) -> dict[str, Any]:
    return {str(key): item for key, item in _json(value, {}).items()}


def _decimal(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DashboardQueryError("non_finite_dashboard_decimal")
        normalized = value.normalize()
        return "0" if normalized == 0 else format(normalized, "f")
    if isinstance(value, (int, float)):
        dec = Decimal(str(value))
        if not dec.is_finite():
            raise DashboardQueryError("non_finite_dashboard_decimal")
        normalized = dec.normalize()
        return "0" if normalized == 0 else format(normalized, "f")
    if isinstance(value, str):
        try:
            dec = Decimal(value)
            if not dec.is_finite():
                raise DashboardQueryError("non_finite_dashboard_decimal")
            normalized = dec.normalize()
            return "0" if normalized == 0 else format(normalized, "f")
        except (ArithmeticError, ValueError):
            return value
    return str(value)


def _page(rows: list[tuple[Any, ...]], limit: int, offset: int) -> tuple[list[tuple[Any, ...]], PageInfo]:
    has_more = len(rows) > limit
    selected = rows[:limit]
    return selected, PageInfo(limit=limit, offset=offset, returned=len(selected), has_more=has_more)


def _top_probability(probabilities: dict[str, Any]) -> tuple[str | None, str | None]:
    """Pick the persisted probability with the largest value; never infers a new one."""
    best_state: str | None = None
    best_value: str | None = None
    best_numeric: float | None = None
    for state, value in probabilities.items():
        try:
            numeric = float(str(value))
        except (TypeError, ValueError):
            continue
        if best_numeric is None or numeric > best_numeric:
            best_numeric, best_state, best_value = numeric, state, str(value)
    return best_state, best_value


_SYNTHETIC_EVIDENCE_MARKERS = ("demo", "synthetic", "fixture", "module1b")

# No real market-data provider is authorized/activated on this platform yet (see
# docs/MODULE_2B2_RESEARCH_WORKSPACES.md).  This allowlist is deliberately empty: adding
# a provider name here is the *only* way classify_research_evidence() can ever return
# REAL_DATA_RESEARCH_EVIDENCE for it, so it must never be populated to make a demo/test
# fixture pass -- only when a real provider is actually integrated and authorized.
_AUTHORIZED_REAL_MARKET_DATA_PROVIDERS: frozenset[str] = frozenset()


def _has_synthetic_marker(*values: str | None) -> bool:
    return any(marker in value.casefold() for value in values if value for marker in _SYNTHETIC_EVIDENCE_MARKERS)


def _provenance_flags(provider: str | None, *synthetic_texts: str | None) -> tuple[bool, bool, bool]:
    """Resolve (synthetic_provenance, real_data_provenance_verified, lineage_complete).

    ``provider`` must come from a persisted ``datasets.provider`` /
    ``historical_data_sources.provider`` column reached through an explicit foreign-key
    join -- never from a dataset name, instrument symbol, or generated identifier.
    ``synthetic_texts`` are additional structured, persisted fields (declared dataset
    requirements, validation-package limitations, a scorecard's own dataset_version) that
    may defensively prove synthetic provenance even when no provider row resolves.
    """
    synthetic = _has_synthetic_marker(provider, *synthetic_texts)
    lineage_complete = provider is not None
    real_verified = provider is not None and provider in _AUTHORIZED_REAL_MARKET_DATA_PROVIDERS
    return synthetic, real_verified, lineage_complete


def classify_research_evidence(
    *, synthetic_provenance: bool, real_data_provenance_verified: bool, lineage_complete: bool,
) -> str:
    """Fail-closed synthetic-vs-real classification from positive authoritative provenance.

    Absence of a synthetic marker is never treated as proof of real data: this only ever
    returns REAL_DATA_RESEARCH_EVIDENCE when a resolved dataset provider is explicitly on
    the authorized real-provider allowlist AND the lineage that reached it is complete.
    Everything else -- including a real-looking dataset/provider name whose provenance
    chain never resolves -- returns UNAVAILABLE. Synthetic provenance always wins over an
    unresolved chain. This is the single source of truth for evidence_classification
    across strategies/experiments/scorecards/signals so no endpoint re-derives it
    independently.
    """
    if synthetic_provenance:
        return "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY"
    if real_data_provenance_verified and lineage_complete:
        return "REAL_DATA_RESEARCH_EVIDENCE"
    return "UNAVAILABLE"


def classify_research_evidence_from_markers(*values: str | None) -> str:
    """Fail-closed classification from free-text fields alone (no persisted provider row).

    Used only where no authoritative dataset-provider lineage exists to query -- e.g. the
    legacy in-memory strategy/experiment research-card registry, which stores dataset
    names as plain text with no dataset/provider table binding at all.  A synthetic
    marker in the text still proves SYNTHETIC_ENGINEERING_EVIDENCE_ONLY, but absence of
    one can never prove REAL_DATA_RESEARCH_EVIDENCE -- it always yields UNAVAILABLE.
    """
    synthetic, real_verified, complete = _provenance_flags(None, *values)
    return classify_research_evidence(
        synthetic_provenance=synthetic, real_data_provenance_verified=real_verified, lineage_complete=complete,
    )


class PostgresOperatorDashboardQueries:
    """Centralized bounded projections over existing immutable PostgreSQL tables."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._read_lock = RLock()

    def _read(self, operation: Any) -> Any:
        try:
            with self._read_lock, self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                return operation(cursor)
        except DashboardObjectNotFound:
            raise
        except Exception as error:
            raise DashboardQueryError("operator_dashboard_query_failed") from error

    @staticmethod
    def _definition(row: tuple[Any, ...]) -> FeatureDefinitionView:
        return FeatureDefinitionView(
            feature_definition_id=row[0], feature_name=str(row[1]), family=str(row[2]),
            semantic_version=str(row[3]), status="RETIRED" if row[20] else "ACTIVE",
            required_dataset_types=_strings(row[6]), required_fields=_strings(row[7]),
            frequency=str(row[8]), timestamp_semantics=str(row[9]), lookback=int(row[10]),
            parameters=_mapping(row[11]), missing_value_policy=str(row[12]),
            outlier_policy=str(row[13]), leakage_policy=str(row[14]), units=str(row[17]),
            calculation_version=str(row[18]), created_at=row[19], retired_at=row[20],
        )

    def instruments(
        self, *, query: str | None = None, asset_class: str | None = None,
        lifecycle_status: str | None = None, limit: int = 50, offset: int = 0,
    ) -> InstrumentDiscoveryPage:
        """List canonical instruments with optional bounded filtering."""
        def operation(cursor: _Cursor) -> InstrumentDiscoveryPage:
            search_param = f"%{query.strip()}%" if query and query.strip() else None
            cursor.execute(
                "SELECT p.instrument_id,p.canonical_symbol,p.asset_class,p.venue,"
                "COALESCE((SELECT e.status FROM professional_instrument_lifecycle_events e "
                "WHERE e.instrument_id=p.instrument_id ORDER BY e.effective_at DESC,e.event_id DESC LIMIT 1),'ACTIVE'),"
                "p.registered_at,(SELECT MIN(m.valid_until) FROM professional_symbol_mappings m "
                "WHERE m.instrument_id=p.instrument_id AND m.valid_until IS NOT NULL),"
                "(STARTS_WITH(p.instrument_id, 'DEMO:') OR STARTS_WITH(p.canonical_symbol, 'DEMO_')),"
                "(SELECT h.version FROM historical_dataset_versions h "
                "JOIN historical_dataset_members hm ON hm.dataset_version_id=h.dataset_version_id "
                "JOIN historical_normalized_observations n ON n.normalized_observation_id=hm.normalized_observation_id "
                "WHERE n.instrument_id=p.instrument_id ORDER BY h.created_at DESC,h.dataset_version_id DESC LIMIT 1),"
                "(SELECT COUNT(*) FROM professional_identifier_mappings i WHERE i.instrument_id=p.instrument_id),"
                "(SELECT COUNT(*) FROM professional_symbol_mappings s WHERE s.instrument_id=p.instrument_id "
                "AND s.valid_until IS NULL) "
                "FROM professional_instruments p "
                "WHERE (CAST(%s AS text) IS NULL OR p.canonical_symbol ILIKE %s OR p.instrument_id ILIKE %s) "
                "AND (CAST(%s AS text) IS NULL OR p.asset_class=%s) "
                "AND (CAST(%s AS text) IS NULL OR COALESCE((SELECT e.status FROM professional_instrument_lifecycle_events e WHERE e.instrument_id=p.instrument_id ORDER BY e.effective_at DESC,e.event_id DESC LIMIT 1),'ACTIVE')=%s) "
                "ORDER BY p.canonical_symbol,p.instrument_id LIMIT %s OFFSET %s",
                (search_param, search_param, search_param, asset_class, asset_class, lifecycle_status, lifecycle_status, limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            return InstrumentDiscoveryPage(
                state="AVAILABLE" if rows else "UNAVAILABLE",
                items=[InstrumentDiscoveryView(
                    instrument_id=str(row[0]), canonical_symbol=str(row[1]), asset_class=str(row[2]),
                    venue=str(row[3]), lifecycle_status=str(row[4]), valid_from=row[5], valid_until=row[6],
                    synthetic_demo=bool(row[7]), latest_dataset_version=None if row[8] is None else str(row[8]),
                    identifier_mapping_count=int(row[9]), ambiguous_mapping=int(row[10]) > 1,
                ) for row in rows], page=page,
            )
        return self._read(operation)

    def instrument(self, instrument_id: str) -> InstrumentDetailView:
        """Read deep authoritative instrument identity, mappings, and lifecycle events."""
        def operation(cursor: _Cursor) -> InstrumentDetailView:
            cursor.execute(
                "SELECT p.instrument_id, p.asset_class, p.instrument_type, p.exchange_name, p.venue, p.mic, "
                "p.canonical_symbol, p.listing_date, p.base_currency, p.quote_currency, p.settlement_currency, "
                "p.contract_multiplier, p.contract_size, p.tick_size, p.lot_size, p.price_precision, "
                "p.quantity_precision, p.trading_timezone, p.market_session_type, p.representation_kind, "
                "p.underlying_reference, p.corporate_action_reference, p.isin, p.cusip, p.contract_code, "
                "p.expiration_date, p.first_notice_date, p.last_trade_date, p.continuous_parent_id, p.roll_rule, "
                "p.registered_at, "
                "COALESCE((SELECT e.status FROM professional_instrument_lifecycle_events e "
                "WHERE e.instrument_id=p.instrument_id ORDER BY e.effective_at DESC,e.event_id DESC LIMIT 1),'ACTIVE'), "
                "(STARTS_WITH(p.instrument_id, 'DEMO:') OR STARTS_WITH(p.canonical_symbol, 'DEMO_')), "
                "(SELECT COUNT(*) FROM professional_symbol_mappings s WHERE s.instrument_id=p.instrument_id AND s.valid_until IS NULL) "
                "FROM professional_instruments p WHERE p.instrument_id=%s",
                (instrument_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise DashboardObjectNotFound("instrument_not_found")

            cursor.execute(
                "SELECT mapping_id, source_kind, namespace, identifier_value, valid_from, valid_until, source_reference, ingested_at "
                "FROM professional_identifier_mappings WHERE instrument_id=%s ORDER BY valid_from DESC, mapping_id",
                (instrument_id,),
            )
            identifier_rows = cursor.fetchall()
            identifier_mappings = [
                IdentifierMappingView(
                    mapping_id=r[0], source_kind=str(r[1]), namespace=str(r[2]), value=str(r[3]),
                    valid_from=r[4], valid_until=r[5], source_reference=str(r[6]), ingested_at=r[7],
                ) for r in identifier_rows
            ]

            cursor.execute(
                "SELECT mapping_id, venue, symbol, valid_from, valid_until, source_reference, ingested_at "
                "FROM professional_symbol_mappings WHERE instrument_id=%s ORDER BY valid_from DESC, mapping_id",
                (instrument_id,),
            )
            symbol_rows = cursor.fetchall()
            symbol_mappings = [
                SymbolMappingView(
                    mapping_id=r[0], venue=str(r[1]), symbol=str(r[2]), valid_from=r[3],
                    valid_until=r[4], source_reference=str(r[5]), ingested_at=r[6],
                ) for r in symbol_rows
            ]

            cursor.execute(
                "SELECT event_id, status, effective_at, ingested_at, reason "
                "FROM professional_instrument_lifecycle_events WHERE instrument_id=%s ORDER BY effective_at DESC, event_id DESC",
                (instrument_id,),
            )
            lifecycle_rows = cursor.fetchall()
            lifecycle_events = [
                LifecycleEventView(
                    event_id=r[0], status=str(r[1]), effective_at=r[2], ingested_at=r[3], reason=str(r[4]),
                ) for r in lifecycle_rows
            ]

            cursor.execute(
                "SELECT DISTINCT h.version FROM historical_dataset_versions h "
                "JOIN historical_dataset_members hm ON hm.dataset_version_id=h.dataset_version_id "
                "JOIN historical_normalized_observations n ON n.normalized_observation_id=hm.normalized_observation_id "
                "WHERE n.instrument_id=%s ORDER BY h.version",
                (instrument_id,),
            )
            dataset_rows = cursor.fetchall()
            dataset_versions = [str(r[0]) for r in dataset_rows]

            return InstrumentDetailView(
                instrument_id=str(row[0]), asset_class=str(row[1]), instrument_type=str(row[2]),
                exchange_name=str(row[3]), venue=str(row[4]), mic=None if row[5] is None else str(row[5]),
                canonical_symbol=str(row[6]), base_currency=str(row[8]), quote_currency=str(row[9]),
                settlement_currency=str(row[10]), contract_multiplier=str(row[11]), contract_size=str(row[12]),
                tick_size=str(row[13]), lot_size=str(row[14]), price_precision=int(row[15]),
                quantity_precision=int(row[16]), trading_timezone=str(row[17]), market_session_type=str(row[18]),
                representation_kind=str(row[19]), isin=None if row[22] is None else str(row[22]),
                cusip=None if row[23] is None else str(row[23]), registered_at=row[30],
                lifecycle_status=str(row[31]), synthetic_demo=bool(row[32]), ambiguous_mapping=int(row[33]) > 1,
                identifier_mappings=identifier_mappings, symbol_mappings=symbol_mappings,
                lifecycle_events=lifecycle_events, dataset_versions=dataset_versions,
            )
        return self._read(operation)

    def historical_datasets(self, *, limit: int = 50, offset: int = 0) -> HistoricalDatasetPage:
        """Read sealed historical dataset versions with provider and checkpoint details."""
        def operation(cursor: _Cursor) -> HistoricalDatasetPage:
            cursor.execute(
                "SELECT h.dataset_version_id, h.source_id, h.version, h.normalization_version, h.content_hash, "
                "h.valid_from, h.valid_until, h.created_at, h.status, "
                "s.provider, s.dataset_name, s.asset_scope, s.provider_terms_version, s.authorization_reference, s.authorized_at, "
                "(SELECT COUNT(*) FROM historical_dataset_members m WHERE m.dataset_version_id=h.dataset_version_id), "
                "(SELECT c.state FROM historical_ingestion_checkpoints c WHERE c.source_id=s.source_id ORDER BY c.recorded_at DESC LIMIT 1), "
                "(s.provider = 'SYNTHETIC_DEMO_ENGINEERING_EVIDENCE' OR STARTS_WITH(h.version, 'demo') OR STARTS_WITH(h.version, 'module1b')) "
                "FROM historical_dataset_versions h "
                "JOIN historical_data_sources s USING(source_id) "
                "ORDER BY h.created_at DESC, h.dataset_version_id DESC LIMIT %s OFFSET %s",
                (limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items = [HistoricalDatasetView(
                dataset_version_id=row[0], source_id=row[1], version=str(row[2]),
                normalization_version=str(row[3]), content_hash=str(row[4]), valid_from=row[5],
                valid_until=row[6], created_at=row[7], status=str(row[8]), provider=str(row[9]),
                dataset_name=str(row[10]), asset_scope=str(row[11]), provider_terms_version=str(row[12]),
                authorization_reference=str(row[13]), authorized_at=row[14], observation_count=int(row[15]),
                checkpoint_state=None if row[16] is None else str(row[16]), synthetic_demo=bool(row[17]),
            ) for row in rows]
            return HistoricalDatasetPage(state="AVAILABLE" if items else "UNAVAILABLE", items=items, page=page)
        return self._read(operation)

    def data_health_assessments(
        self, *, scope_type: str | None = None, scope_value: str | None = None,
        blocking: bool | None = None, max_action: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> DataHealthAssessmentPage:
        def operation(cursor: _Cursor) -> DataHealthAssessmentPage:
            cursor.execute(
                "SELECT COUNT(*), COUNT(*) FILTER (WHERE a.blocking) FROM data_health_assessments a "
                "WHERE (CAST(%s AS text) IS NULL OR a.scope_type=%s) "
                "AND (CAST(%s AS text) IS NULL OR a.scope_value=%s) "
                "AND (CAST(%s AS boolean) IS NULL OR a.blocking=%s) "
                "AND (CAST(%s AS text) IS NULL OR a.max_action=%s)",
                (scope_type, scope_type, scope_value, scope_value, blocking, blocking, max_action, max_action),
            )
            count_row = cursor.fetchone()
            total_count = int(count_row[0]) if count_row else 0
            blocking_count = int(count_row[1]) if count_row else 0

            cursor.execute(
                "SELECT a.assessment_id, a.dataset_version_id, h.version, a.scope_type, a.scope_value, "
                "a.policy_version, a.evaluated_at, a.expected_start, a.expected_end, a.max_action, "
                "a.blocking, a.content_hash, a.summary, "
                "(a.policy_version LIKE '%%demo%%' OR a.policy_version LIKE '%%module1b%%') "
                "FROM data_health_assessments a "
                "LEFT JOIN historical_dataset_versions h USING(dataset_version_id) "
                "WHERE (CAST(%s AS text) IS NULL OR a.scope_type=%s) "
                "AND (CAST(%s AS text) IS NULL OR a.scope_value=%s) "
                "AND (CAST(%s AS boolean) IS NULL OR a.blocking=%s) "
                "AND (CAST(%s AS text) IS NULL OR a.max_action=%s) "
                "ORDER BY a.evaluated_at DESC, a.assessment_id DESC LIMIT %s OFFSET %s",
                (scope_type, scope_type, scope_value, scope_value, blocking, blocking, max_action, max_action, limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items: list[DataHealthAssessmentView] = []
            for row in rows:
                assessment_id = row[0]
                cursor.execute(
                    "SELECT finding_id, finding_sequence, check_type, action, observed_at, detail, content_hash "
                    "FROM data_health_findings WHERE assessment_id=%s ORDER BY finding_sequence",
                    (assessment_id,),
                )
                finding_rows = cursor.fetchall()
                findings = [DataHealthFindingView(
                    finding_id=fr[0], sequence=int(fr[1]), check_type=str(fr[2]), action=str(fr[3]),
                    observed_at=fr[4], detail=_mapping(fr[5]), content_hash=str(fr[6]),
                ) for fr in finding_rows]
                items.append(DataHealthAssessmentView(
                    assessment_id=row[0], dataset_version_id=row[1],
                    dataset_version=None if row[2] is None else str(row[2]),
                    scope_type=str(row[3]), scope_value=str(row[4]), policy_version=str(row[5]),
                    evaluated_at=row[6], expected_start=row[7], expected_end=row[8], max_action=str(row[9]),
                    blocking=bool(row[10]), content_hash=str(row[11]), summary=_mapping(row[12]),
                    findings=findings, synthetic_demo=bool(row[13]),
                ))
            overall = "BLOCKING" if blocking_count > 0 else ("HEALTHY" if total_count > 0 else "AVAILABLE")
            return DataHealthAssessmentPage(
                state="AVAILABLE" if items or total_count == 0 else "UNAVAILABLE",
                overall_state=overall, total_assessments=total_count,
                blocking_count=blocking_count, items=items, page=page,
            )
        return self._read(operation)

    def data_health_assessment(self, assessment_id: UUID) -> DataHealthAssessmentView:
        """Read single Data Health assessment by ID."""
        def operation(cursor: _Cursor) -> DataHealthAssessmentView:
            cursor.execute(
                "SELECT a.assessment_id, a.dataset_version_id, h.version, a.scope_type, a.scope_value, "
                "a.policy_version, a.evaluated_at, a.expected_start, a.expected_end, a.max_action, "
                "a.blocking, a.content_hash, a.summary, "
                "(a.policy_version LIKE '%%demo%%' OR a.policy_version LIKE '%%module1b%%') "
                "FROM data_health_assessments a "
                "LEFT JOIN historical_dataset_versions h USING(dataset_version_id) "
                "WHERE a.assessment_id=%s",
                (assessment_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise DashboardObjectNotFound("data_health_assessment_not_found")
            cursor.execute(
                "SELECT finding_id, finding_sequence, check_type, action, observed_at, detail, content_hash "
                "FROM data_health_findings WHERE assessment_id=%s ORDER BY finding_sequence",
                (assessment_id,),
            )
            finding_rows = cursor.fetchall()
            findings = [DataHealthFindingView(
                finding_id=fr[0], sequence=int(fr[1]), check_type=str(fr[2]), action=str(fr[3]),
                observed_at=fr[4], detail=_mapping(fr[5]), content_hash=str(fr[6]),
            ) for fr in finding_rows]
            return DataHealthAssessmentView(
                assessment_id=row[0], dataset_version_id=row[1],
                dataset_version=None if row[2] is None else str(row[2]),
                scope_type=str(row[3]), scope_value=str(row[4]), policy_version=str(row[5]),
                evaluated_at=row[6], expected_start=row[7], expected_end=row[8], max_action=str(row[9]),
                blocking=bool(row[10]), content_hash=str(row[11]), summary=_mapping(row[12]),
                findings=findings, synthetic_demo=bool(row[13]),
            )
        return self._read(operation)

    def strategies(self, *, family: str | None, limit: int, offset: int) -> StrategyDiscoveryPage:
        def operation(cursor: _Cursor) -> StrategyDiscoveryPage:
            cursor.execute(
                "SELECT d.strategy_id,v.strategy_version_id,v.version,d.family,d.hypothesis,"
                "v.feature_manifest,v.cost_model_version,v.contract,v.created_at,"
                "(SELECT ds.provider FROM research_experiments e "
                "JOIN dataset_versions dsv ON dsv.dataset_version_id=e.dataset_version_id "
                "JOIN datasets ds ON ds.dataset_id=dsv.dataset_id "
                "WHERE e.strategy_version_id=v.strategy_version_id "
                "ORDER BY e.created_at DESC LIMIT 1) "
                "FROM strategy_versions v JOIN strategy_definitions d USING(strategy_id) "
                "WHERE (CAST(%s AS text) IS NULL OR d.family=%s) "
                "ORDER BY v.created_at DESC,v.strategy_version_id DESC LIMIT %s OFFSET %s",
                (family, family, limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items: list[StrategyDiscoveryView] = []
            for row in rows:
                contract = _mapping(row[7])
                dataset_requirements = _strings(contract.get("required_datasets"))
                synthetic, real_verified, complete = _provenance_flags(
                    None if row[9] is None else str(row[9]),
                    *dataset_requirements, str(contract.get("evidence_classification", "")),
                )
                items.append(StrategyDiscoveryView(
                    strategy_id=row[0], strategy_version_id=row[1], version=str(row[2]), family=str(row[3]),
                    hypothesis=str(row[4]), status="RESEARCH_ONLY", dataset_requirements=dataset_requirements,
                    feature_versions=_strings(row[5]), cost_model_version=str(row[6]), created_at=row[8],
                    evidence_classification=classify_research_evidence(
                        synthetic_provenance=synthetic, real_data_provenance_verified=real_verified, lineage_complete=complete,
                    ),
                ))
            return StrategyDiscoveryPage(state="AVAILABLE" if rows else "UNAVAILABLE", items=items, page=page)
        return self._read(operation)

    def experiments(self, *, strategy_id: UUID | None, limit: int, offset: int) -> ExperimentDiscoveryPage:
        def operation(cursor: _Cursor) -> ExperimentDiscoveryPage:
            cursor.execute(
                "SELECT e.experiment_id,d.strategy_id,v.strategy_version_id,v.version,dv.version,"
                "v.feature_manifest,v.cost_model_version,e.created_at,"
                "(SELECT MAX(w.test_end) FROM walk_forward_evidence w WHERE w.experiment_id=e.experiment_id),"
                "ds.provider "
                "FROM research_experiments e JOIN strategy_versions v USING(strategy_version_id) "
                "JOIN strategy_definitions d USING(strategy_id) JOIN dataset_versions dv USING(dataset_version_id) "
                "JOIN datasets ds ON ds.dataset_id=dv.dataset_id "
                "WHERE (CAST(%s AS uuid) IS NULL OR d.strategy_id=%s) "
                "ORDER BY e.created_at DESC,e.experiment_id DESC LIMIT %s OFFSET %s",
                (strategy_id, strategy_id, limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items: list[ExperimentDiscoveryView] = []
            for row in rows:
                synthetic, real_verified, complete = _provenance_flags(str(row[9]), str(row[4]))
                items.append(ExperimentDiscoveryView(
                    experiment_id=row[0], strategy_id=row[1], strategy_version_id=row[2], strategy_version=str(row[3]),
                    dataset_version=str(row[4]), feature_versions=_strings(row[5]), cost_model_version=str(row[6]),
                    created_at=row[7], evaluated_at=row[8], status="RESEARCH_ONLY",
                    evidence_classification=classify_research_evidence(
                        synthetic_provenance=synthetic, real_data_provenance_verified=real_verified, lineage_complete=complete,
                    ),
                ))
            return ExperimentDiscoveryPage(state="AVAILABLE" if rows else "UNAVAILABLE", items=items, page=page)
        return self._read(operation)

    def investment_theses(
        self, *, instrument: str | None, status: str | None, review_state: str | None,
        synthetic_demo: bool | None, limit: int, offset: int,
    ) -> InvestmentThesisDiscoveryPage:
        """Bounded investment-thesis discovery; no calculation, only persisted evidence ordering.

        ``instrument`` matches either the PIT instrument reference or the canonical symbol
        exactly (never a substring scan). ``synthetic_demo`` filters on the same positive
        ``DEMO:``-prefix marker used elsewhere in this file (``instruments()``), not a
        heuristic. ``evidence_classification`` is resolved through the shared fail-closed
        ``classify_research_evidence_from_markers`` helper -- absence of a synthetic marker
        is never treated as proof of real data.
        """
        def operation(cursor: _Cursor) -> InvestmentThesisDiscoveryPage:
            cursor.execute(
                "SELECT t.thesis_id,COALESCE(t.pit_instrument_id,t.instrument_id::text) AS instrument_ref,"
                "p.canonical_symbol,t.version,t.status,t.created_at,lr.status AS review_state "
                "FROM investment_theses t "
                "LEFT JOIN professional_instruments p ON p.instrument_id=t.pit_instrument_id "
                "LEFT JOIN LATERAL (SELECT r.status FROM investment_reviews r WHERE r.thesis_id=t.thesis_id "
                "ORDER BY r.reviewed_at DESC,r.review_id DESC LIMIT 1) lr ON TRUE "
                "WHERE (CAST(%s AS text) IS NULL OR COALESCE(t.pit_instrument_id,t.instrument_id::text)=%s "
                "OR p.canonical_symbol=%s) "
                "AND (CAST(%s AS text) IS NULL OR t.status=%s) "
                "AND (CAST(%s AS text) IS NULL OR lr.status=%s) "
                "AND (CAST(%s AS boolean) IS NULL OR (COALESCE(t.pit_instrument_id,t.instrument_id::text) LIKE 'DEMO:%%')=%s) "
                "ORDER BY t.created_at DESC,t.thesis_id DESC LIMIT %s OFFSET %s",
                (
                    instrument, instrument, instrument, status, status, review_state, review_state,
                    synthetic_demo, synthetic_demo, limit + 1, offset,
                ),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items: list[InvestmentThesisDiscoveryView] = []
            for row in rows:
                instrument_ref = str(row[1])
                items.append(InvestmentThesisDiscoveryView(
                    thesis_id=row[0], instrument_id=instrument_ref, canonical_symbol=None if row[2] is None else str(row[2]),
                    thesis_version=str(row[3]), status=str(row[4]), as_of=row[5],
                    review_state=None if row[6] is None else str(row[6]),
                    synthetic_demo=instrument_ref.startswith("DEMO:"),
                    evidence_classification=classify_research_evidence_from_markers(instrument_ref),
                ))
            return InvestmentThesisDiscoveryPage(state="AVAILABLE" if items else "UNAVAILABLE", items=items, page=page)
        return self._read(operation)

    def investment_portfolios(
        self, *, status: str | None, account_id: str | None, limit: int, offset: int,
    ) -> InvestmentPortfolioDiscoveryPage:
        """Bounded investment-portfolio discovery over persisted rebalance candidates.

        ``evidence_classification`` previously returned a hardcoded authority-disclaimer
        string for every row regardless of provenance -- a Module 2B-2.1-style truthfulness
        defect (see docs/MODULE_2B4_INVESTMENTS_NEWS.md). It now resolves through the same
        fail-closed ``classify_research_evidence_from_markers`` helper used everywhere else,
        scanning the account id, persisted limitations text, and candidate instrument keys
        for a positive synthetic marker; absence of one never proves real data.
        """
        def operation(cursor: _Cursor) -> InvestmentPortfolioDiscoveryPage:
            cursor.execute(
                "SELECT c.account_id,c.as_of,c.status,c.candidate_weights,c.limitations "
                "FROM investment_rebalance_candidates c "
                "WHERE (CAST(%s AS text) IS NULL OR c.status=%s) "
                "AND (CAST(%s AS text) IS NULL OR c.account_id=%s) "
                "ORDER BY c.as_of DESC,c.candidate_id DESC LIMIT %s OFFSET %s",
                (status, status, account_id, account_id, limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items: list[InvestmentPortfolioDiscoveryView] = []
            for row in rows:
                weights = _mapping(row[3])
                limitations_text = " ".join(_strings(row[4]))
                items.append(InvestmentPortfolioDiscoveryView(
                    portfolio_id=str(row[0]), as_of=row[1], review_status=str(row[2]), holdings_count=len(weights),
                    evidence_classification=classify_research_evidence_from_markers(
                        str(row[0]), limitations_text, *weights.keys(),
                    ),
                ))
            return InvestmentPortfolioDiscoveryPage(state="AVAILABLE" if items else "UNAVAILABLE", items=items, page=page)
        return self._read(operation)

    def paper_orders(
        self, *, account_id: str | None = None, instrument: str | None = None, side: str | None = None,
        lifecycle_status: str | None = None, fill_state: str | None = None, reconciliation_state: str | None = None,
        limit: int, offset: int,
    ) -> PaperOrderDiscoveryPage:
        """Bounded paper-order discovery; lifecycle/fill/reconciliation state is derived from
        persisted OMS events and reconciliation rows, never inferred -- missing reconciliation
        evidence always resolves to ``UNAVAILABLE``, never ``HEALTHY``."""
        def operation(cursor: _Cursor) -> PaperOrderDiscoveryPage:
            cursor.execute(
                "WITH order_state AS ("
                "SELECT o.account_id,o.intent_id,o.instrument_id,i.canonical_symbol,o.side,o.quantity,o.created_at,"
                "COALESCE((SELECT e.payload->>'to' FROM oms_events e WHERE e.intent_id=o.intent_id "
                "AND e.event_type='ORDER_STATUS_CHANGED' ORDER BY e.event_sequence DESC LIMIT 1),o.status) AS lifecycle_status,"
                "CASE WHEN (SELECT COUNT(*) FROM fills f WHERE f.intent_id=o.intent_id)=0 THEN 'UNFILLED' ELSE 'PARTIAL_OR_FINAL_FILL' END AS fill_state,"
                "COALESCE((SELECT CASE WHEN r.complete THEN 'HEALTHY' ELSE 'RECONCILIATION_REQUIRED' END "
                "FROM reconciliations r WHERE r.account_id=o.account_id ORDER BY r.occurred_at DESC,r.reconciliation_id DESC LIMIT 1),'UNAVAILABLE') AS reconciliation_state "
                "FROM paper_order_intents o JOIN instruments i ON i.instrument_id=o.instrument_id"
                ") "
                "SELECT * FROM order_state WHERE "
                "(CAST(%s AS text) IS NULL OR account_id=%s) "
                "AND (CAST(%s AS text) IS NULL OR canonical_symbol=%s OR instrument_id::text=%s) "
                "AND (CAST(%s AS text) IS NULL OR side=%s) "
                "AND (CAST(%s AS text) IS NULL OR lifecycle_status=%s) "
                "AND (CAST(%s AS text) IS NULL OR fill_state=%s) "
                "AND (CAST(%s AS text) IS NULL OR reconciliation_state=%s) "
                "ORDER BY created_at DESC,intent_id DESC LIMIT %s OFFSET %s",
                (
                    account_id, account_id, instrument, instrument, instrument, side, side,
                    lifecycle_status, lifecycle_status, fill_state, fill_state,
                    reconciliation_state, reconciliation_state, limit + 1, offset,
                ),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            return PaperOrderDiscoveryPage(
                state="AVAILABLE" if rows else "UNAVAILABLE",
                items=[PaperOrderDiscoveryView(
                    account_id=str(row[0]), intent_id=row[1], instrument_id=str(row[2]),
                    canonical_symbol=None if row[3] is None else str(row[3]), side=str(row[4]),
                    quantity=_decimal(row[5]) or "0", created_at=row[6],
                    lifecycle_status=str(row[7]), fill_state=str(row[8]),
                    reconciliation_state=str(row[9]),
                ) for row in rows], page=page,
            )
        return self._read(operation)

    def paper_order(self, intent_id: UUID) -> PaperOrderView:
        """Full paper-order lifecycle evidence from the same PostgreSQL authority as
        discovery -- never the dev-only SQLite paper OMS store."""
        def operation(cursor: _Cursor) -> PaperOrderView:
            cursor.execute(
                "SELECT o.intent_id,o.account_id,o.instrument_id,i.canonical_symbol,o.side,o.quantity,"
                "o.limit_price,o.status,o.created_at FROM paper_order_intents o "
                "JOIN instruments i ON i.instrument_id=o.instrument_id WHERE o.intent_id=%s",
                (intent_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise DashboardObjectNotFound("paper_order_not_found")
            cursor.execute(
                "SELECT oms_event_id,event_type,occurred_at,payload FROM oms_events "
                "WHERE intent_id=%s ORDER BY event_sequence",
                (intent_id,),
            )
            event_rows = cursor.fetchall()
            events = [PaperOrderEventView(event_id=item[0], event_type=str(item[1]), occurred_at=item[2]) for item in event_rows]
            status_changes = [item for item in event_rows if item[1] == "ORDER_STATUS_CHANGED"]
            lifecycle_status = str(_mapping(status_changes[-1][3]).get("to")) if status_changes else str(row[7])

            cursor.execute(
                "SELECT fill_id,external_fill_id,quantity,price,occurred_at FROM fills "
                "WHERE intent_id=%s ORDER BY occurred_at,fill_id",
                (intent_id,),
            )
            fill_rows = cursor.fetchall()
            fills = [PaperFillView(
                fill_id=item[0], external_fill_id=str(item[1]),
                quantity=_decimal(item[2]) or "0", price=_decimal(item[3]) or "0", occurred_at=item[4],
            ) for item in fill_rows]
            filled_quantity = sum((Decimal(str(item[2])) for item in fill_rows), Decimal(0))
            notional = sum((Decimal(str(item[2])) * Decimal(str(item[3])) for item in fill_rows), Decimal(0))
            average_fill_price = _decimal(notional / filled_quantity) if filled_quantity > 0 else None

            return PaperOrderView(
                intent_id=row[0], account_id=str(row[1]), instrument_id=str(row[2]),
                canonical_symbol=None if row[3] is None else str(row[3]), side=str(row[4]),
                quantity=_decimal(row[5]) or "0", limit_price=_decimal(row[6]) or "0",
                status=lifecycle_status, filled_quantity=_decimal(filled_quantity) or "0",
                average_fill_price=average_fill_price, created_at=row[8], events=events, fills=fills,
            )
        return self._read(operation)

    def paper_reconciliation(self, account_id: str) -> PaperReconciliationView:
        """Latest paper-account reconciliation evidence; missing evidence raises
        ``DashboardObjectNotFound`` (surfaced as EMPTY, never a fabricated HEALTHY state)."""
        def operation(cursor: _Cursor) -> PaperReconciliationView:
            cursor.execute(
                "SELECT reconciliation_id,source,occurred_at,complete,discrepancies FROM reconciliations "
                "WHERE account_id=%s ORDER BY occurred_at DESC,reconciliation_id DESC LIMIT 1",
                (account_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise DashboardObjectNotFound("paper_reconciliation_not_found")
            cursor.execute(
                "SELECT evidence_id,healthy,as_of,cash_currency,cash_amount,buying_power "
                "FROM reconciled_account_evidence WHERE reconciliation_id=%s",
                (row[0],),
            )
            evidence_row = cursor.fetchone()
            reconciled_account = None if evidence_row is None else PaperReconciledAccountView(
                evidence_id=evidence_row[0], healthy=bool(evidence_row[1]), as_of=evidence_row[2],
                cash_currency=str(evidence_row[3]), cash_amount=_decimal(evidence_row[4]) or "0",
                buying_power=_decimal(evidence_row[5]) or "0",
            )
            return PaperReconciliationView(
                account_id=account_id, source=str(row[1]), occurred_at=row[2], complete=bool(row[3]),
                discrepancies=_strings(row[4]), reconciled_account=reconciled_account,
            )
        return self._read(operation)

    def feature_definitions(self, *, family: str | None, limit: int, offset: int) -> FeatureDefinitionPage:
        def operation(cursor: _Cursor) -> FeatureDefinitionPage:
            cursor.execute(
                "SELECT * FROM feature_definition_versions WHERE (CAST(%s AS text) IS NULL OR family=%s) "
                "ORDER BY family,name,semantic_version,feature_id LIMIT %s OFFSET %s",
                (family, family, limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items = [self._definition(row) for row in rows]
            return FeatureDefinitionPage(state="AVAILABLE" if items else "UNAVAILABLE", items=items, page=page)
        return self._read(operation)

    def feature_definition(self, feature_id: UUID) -> FeatureDefinitionView:
        def operation(cursor: _Cursor) -> FeatureDefinitionView:
            cursor.execute("SELECT * FROM feature_definition_versions WHERE feature_id=%s", (feature_id,))
            row = cursor.fetchone()
            if row is None:
                raise DashboardObjectNotFound("feature_definition_not_found")
            return self._definition(row)
        return self._read(operation)

    def feature_materializations(
        self, *, feature_id: UUID, instrument: str, dataset_version: str,
        decision_time: datetime, limit: int, offset: int,
    ) -> FeatureMaterializationPage:
        def operation(cursor: _Cursor) -> FeatureMaterializationPage:
            cursor.execute(
                "SELECT x.*,d.name,d.semantic_version FROM (SELECT m.*,ROW_NUMBER() OVER "
                "(PARTITION BY m.event_at ORDER BY m.knowledge_at DESC,m.computed_at DESC,m.materialization_id) rank "
                "FROM feature_materializations m WHERE m.feature_id=%s AND m.instrument_id=%s "
                "AND m.dataset_version=%s AND m.event_at<=%s AND m.effective_at<=%s "
                "AND m.knowledge_at<=%s AND m.computed_at<=%s) x "
                "JOIN feature_definition_versions d ON d.feature_id=x.feature_id WHERE x.rank=1 "
                "ORDER BY x.event_at DESC,x.materialization_id LIMIT %s OFFSET %s",
                (feature_id, instrument, dataset_version, decision_time, decision_time,
                 decision_time, decision_time, limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items = [FeatureMaterializationView(
                materialization_id=row[0], feature_definition_id=row[1], instrument=str(row[2]),
                dataset_version=str(row[3]), event_time=row[4], effective_time=row[5],
                knowledge_time=row[6], computed_time=row[7], source_manifest=_strings(row[8]),
                value=_decimal(row[9]), quality_state=str(row[10]), content_hash=str(row[11]),
                feature_name=str(row[13]), semantic_version=str(row[14]),
            ) for row in rows]
            return FeatureMaterializationPage(
                state="AVAILABLE" if items else "UNAVAILABLE", decision_time=decision_time,
                items=items, page=page,
            )
        return self._read(operation)

    def signals(
        self, *, as_of: datetime, status: str | None, instrument: str | None,
        strategy_version: str | None, limit: int, offset: int,
    ) -> SignalPage:
        """Point-in-time signal projection; overdue state is visible but never mutated on read."""
        def operation(cursor: _Cursor) -> SignalPage:
            cursor.execute(
                "SELECT p.signal_id,p.instrument_id,p.strategy_version,p.created_at,p.expires_at,p.payload,"
                "COALESCE(e.to_status,v.status,'CANDIDATE') current_status,v.assessment_id,v.passed_stages,v.failures,e.reason,"
                "COALESCE("
                "(SELECT ds1.provider FROM strategy_versions sv "
                "JOIN research_experiments ex ON ex.strategy_version_id=sv.strategy_version_id "
                "JOIN dataset_versions dsv1 ON dsv1.dataset_version_id=ex.dataset_version_id "
                "JOIN datasets ds1 ON ds1.dataset_id=dsv1.dataset_id "
                "WHERE sv.version=p.strategy_version ORDER BY ex.created_at DESC LIMIT 1),"
                "(SELECT ds2.provider FROM strategy_scorecards sc "
                "JOIN scorecard_validation_packages svp ON svp.scorecard_id=sc.scorecard_id "
                "JOIN validation_packages vp ON vp.package_id=svp.package_id "
                "JOIN dataset_versions dsv2 ON dsv2.dataset_version_id=vp.dataset_version_id "
                "JOIN datasets ds2 ON ds2.dataset_id=dsv2.dataset_id "
                "WHERE sc.strategy_version=p.strategy_version ORDER BY sc.evaluated_at DESC LIMIT 1)"
                ") bound_provider "
                "FROM runtime_signal_proposals p "
                "LEFT JOIN LATERAL (SELECT to_status,reason FROM runtime_signal_lifecycle_events WHERE signal_id=p.signal_id AND occurred_at<=%s ORDER BY event_sequence DESC LIMIT 1) e ON TRUE "
                "LEFT JOIN LATERAL (SELECT assessment_id,status,passed_stages,failures FROM runtime_signal_validations WHERE signal_id=p.signal_id AND assessed_at<=%s ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1) v ON TRUE "
                "WHERE p.created_at<=%s AND (CAST(%s AS text) IS NULL OR COALESCE(e.to_status,v.status,'CANDIDATE')=%s) "
                "AND (CAST(%s AS text) IS NULL OR p.instrument_id=%s) AND (CAST(%s AS text) IS NULL OR p.strategy_version=%s) "
                "ORDER BY p.created_at DESC,p.signal_id LIMIT %s OFFSET %s",
                (as_of, as_of, as_of, status, status, instrument, instrument,
                 strategy_version, strategy_version, limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items: list[SignalView] = []
            for row in rows:
                payload = _mapping(row[5])
                cursor.execute(
                    "SELECT event_id,from_status,to_status,actor,reason,evidence_references,occurred_at "
                    "FROM runtime_signal_lifecycle_events WHERE signal_id=%s AND occurred_at<=%s ORDER BY event_sequence",
                    (row[0], as_of),
                )
                lifecycle = [SignalLifecycleEventView(
                    event_id=event[0], from_status=str(event[1]), to_status=str(event[2]),
                    actor=str(event[3]), reason=str(event[4]), evidence_references=_strings(event[5]),
                    occurred_at=event[6],
                ) for event in cursor.fetchall()]
                current_status = str(row[6])
                if current_status == "EXPIRED":
                    expiry_state: Literal["CURRENT", "OVERDUE", "EXPIRED"] = "EXPIRED"
                elif row[4] <= as_of and current_status in {"VALIDATED", "WAITING_FOR_ENTRY", "ACTIVE"}:
                    expiry_state = "OVERDUE"
                else:
                    expiry_state = "CURRENT"
                contradicting = payload.get("contradicting_evidence", [])
                synthetic, real_verified, complete = _provenance_flags(None if row[11] is None else str(row[11]))
                items.append(SignalView(
                    signal_id=row[0], instrument=str(row[1]), strategy_version=str(row[2]),
                    direction=str(payload.get("direction", "UNAVAILABLE")), status=current_status,
                    expiry_state=expiry_state, created_at=row[3], expires_at=row[4],
                    strength=str(payload.get("strength", "UNAVAILABLE")),
                    confidence=str(payload.get("confidence", "UNAVAILABLE")),
                    data_quality_score=str(payload.get("data_quality_score", "UNAVAILABLE")),
                    explanation=str(payload.get("explanation", "UNAVAILABLE")),
                    contradicting_evidence=[str(item) for item in contradicting] if isinstance(contradicting, list) else [],
                    validation_id=row[7], passed_stages=_strings(row[8]), failed_stages=_strings(row[9]),
                    latest_reason=str(row[10] or "candidate_not_yet_assessed"), lifecycle=lifecycle,
                    evidence_classification=classify_research_evidence(
                        synthetic_provenance=synthetic, real_data_provenance_verified=real_verified, lineage_complete=complete,
                    ),
                    research_or_paper_only=True, automatic_authority=False,
                ))
            state: Availability = "BLOCKED" if any(item.expiry_state == "OVERDUE" for item in items) else ("AVAILABLE" if items else "UNAVAILABLE")
            return SignalPage(state=state, as_of=as_of, items=items, page=page)
        return self._read(operation)

    def risk_decisions(
        self, *, approved: bool | None, account_id: str | None, policy_version_id: UUID | None,
        business_date: date | None, has_reservation: bool | None, limit: int, offset: int,
    ) -> RiskDecisionPage:
        """Immutable risk-decision evidence only; reading cannot evaluate or override risk."""
        def operation(cursor: _Cursor) -> RiskDecisionPage:
            cursor.execute(
                "SELECT d.risk_decision_id,d.intent_id,d.risk_policy_version_id,p.name,pv.version,"
                "pv.content_hash,pv.limits,d.approved,d.reasons,d.decided_at,r.reservation_id,"
                "r.account_id,r.business_date,r.notional,r.created_at "
                "FROM risk_decisions d JOIN risk_policy_versions pv ON pv.risk_policy_version_id=d.risk_policy_version_id "
                "JOIN risk_policies p ON p.risk_policy_id=pv.risk_policy_id "
                "LEFT JOIN risk_reservations r ON r.intent_id=d.intent_id "
                "WHERE (CAST(%s AS boolean) IS NULL OR d.approved=%s) "
                "AND (CAST(%s AS text) IS NULL OR r.account_id=%s) "
                "AND (CAST(%s AS uuid) IS NULL OR d.risk_policy_version_id=%s) "
                "AND (CAST(%s AS date) IS NULL OR r.business_date=%s) "
                "AND (CAST(%s AS boolean) IS NULL OR (r.reservation_id IS NOT NULL)=%s) "
                "ORDER BY d.decided_at DESC,d.risk_decision_id DESC LIMIT %s OFFSET %s",
                (
                    approved, approved, account_id, account_id, policy_version_id, policy_version_id,
                    business_date, business_date, has_reservation, has_reservation, limit + 1, offset,
                ),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items = [RiskDecisionView(
                risk_decision_id=row[0], intent_id=row[1], policy_version_id=row[2], policy_name=str(row[3]),
                policy_version=str(row[4]), policy_content_hash=str(row[5]), policy_limits=_mapping(row[6]),
                approved=bool(row[7]), reasons=_strings(row[8]), decided_at=row[9], reservation_id=row[10],
                account_id=None if row[11] is None else str(row[11]),
                business_date=None if row[12] is None else row[12].isoformat(), reserved_notional=_decimal(row[13]),
                reservation_created_at=row[14], research_or_paper_only=True, automatic_authority=False,
            ) for row in rows]
            return RiskDecisionPage(state="AVAILABLE" if items else "UNAVAILABLE", items=items, page=page)
        return self._read(operation)

    def strategy_scorecard(self, scorecard_id: UUID) -> StrategyScorecardView:
        def operation(cursor: _Cursor) -> StrategyScorecardView:
            cursor.execute(
                "SELECT s.*,v.package_id,v.package_content_hash,ds.provider,vp.limitations "
                "FROM strategy_scorecards s "
                "LEFT JOIN scorecard_validation_packages v USING(scorecard_id) "
                "LEFT JOIN validation_packages vp ON vp.package_id=v.package_id "
                "LEFT JOIN dataset_versions dsv ON dsv.dataset_version_id=vp.dataset_version_id "
                "LEFT JOIN datasets ds ON ds.dataset_id=dsv.dataset_id "
                "WHERE s.scorecard_id=%s",
                (scorecard_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise DashboardObjectNotFound("strategy_scorecard_not_found")
            cursor.execute(
                "SELECT * FROM scorecard_metric_observations WHERE scorecard_id=%s ORDER BY family,name,metric_id",
                (scorecard_id,),
            )
            metrics = [ScorecardMetricView(
                metric_id=item[0], family=str(item[2]), name=str(item[3]), evidence_state=str(item[4]),
                value=_decimal(item[5]), unit=str(item[6]), dimensions=_strings(item[7]),
                evidence_reference=f"scorecard:{scorecard_id}:metric:{item[0]}",
            ) for item in cursor.fetchall()]
            cursor.execute("SELECT * FROM scorecard_components WHERE scorecard_id=%s ORDER BY name", (scorecard_id,))
            components = [ScorecardComponentView(
                component_id=item[0], name=str(item[2]), formula_version=str(item[3]),
                value=_decimal(item[4]), rationale=str(item[5]),
            ) for item in cursor.fetchall()]
            groups = [ScorecardGroupView(name=family, metrics=[item for item in metrics if item.family == family])
                      for family in ("PERFORMANCE", "ROBUSTNESS", "EXECUTION", "RISK", "DATA_QUALITY", "SIGNAL_DECAY")]
            limitations = _strings(row[11])
            manifest = _mapping(row[14])
            synthetic, real_verified, complete = _provenance_flags(
                None if row[18] is None else str(row[18]), str(row[6]), *limitations, *_strings(row[19]),
            )
            return StrategyScorecardView(
                scorecard_id=row[0], schema_version=str(row[1]), strategy_id=row[2],
                strategy_version=str(row[3]), research_run_id=row[4], feature_versions=_strings(row[5]),
                dataset_version=str(row[6]), cost_model_version=str(row[7]), evaluated_at=row[8],
                knowledge_cutoff=row[9], status=str(row[10]), limitations=limitations,
                dataset_health_status=str(row[12]), validation_package_id=row[16],
                validation_package_content_hash=None if row[17] is None else str(row[17]),
                evidence_classification=classify_research_evidence(
                    synthetic_provenance=synthetic, real_data_provenance_verified=real_verified, lineage_complete=complete,
                ),
                evidence_manifest_references=[f"{key}:{value}" for key, value in sorted(manifest.items())],
                content_hash=str(row[15]), groups=groups, complexity_components=components,
            )
        return self._read(operation)

    def strategy_scorecards(
        self, *, strategy_id: UUID | None, status: str | None, limit: int, offset: int,
    ) -> StrategyScorecardDiscoveryPage:
        def operation(cursor: _Cursor) -> StrategyScorecardDiscoveryPage:
            cursor.execute(
                "SELECT s.scorecard_id,s.strategy_id,s.strategy_version,s.research_run_id,s.dataset_version,"
                "s.evaluated_at,s.status,s.dataset_health_status,s.limitations,ds.provider,vp.limitations "
                "FROM strategy_scorecards s "
                "LEFT JOIN scorecard_validation_packages svp ON svp.scorecard_id=s.scorecard_id "
                "LEFT JOIN validation_packages vp ON vp.package_id=svp.package_id "
                "LEFT JOIN dataset_versions dsv ON dsv.dataset_version_id=vp.dataset_version_id "
                "LEFT JOIN datasets ds ON ds.dataset_id=dsv.dataset_id "
                "WHERE (CAST(%s AS uuid) IS NULL OR s.strategy_id=%s) AND (CAST(%s AS text) IS NULL OR s.status=%s) "
                "ORDER BY s.evaluated_at DESC,s.scorecard_id DESC LIMIT %s OFFSET %s",
                (strategy_id, strategy_id, status, status, limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items: list[StrategyScorecardDiscoveryView] = []
            for row in rows:
                synthetic, real_verified, complete = _provenance_flags(
                    None if row[9] is None else str(row[9]), str(row[4]), *_strings(row[8]), *_strings(row[10]),
                )
                items.append(StrategyScorecardDiscoveryView(
                    scorecard_id=row[0], strategy_id=row[1], strategy_version=str(row[2]), research_run_id=row[3],
                    dataset_version=str(row[4]), evaluated_at=row[5], status=str(row[6]),
                    dataset_health_status=str(row[7]),
                    evidence_classification=classify_research_evidence(
                        synthetic_provenance=synthetic, real_data_provenance_verified=real_verified, lineage_complete=complete,
                    ),
                ))
            return StrategyScorecardDiscoveryPage(state="AVAILABLE" if rows else "UNAVAILABLE", items=items, page=page)
        return self._read(operation)

    def latest_scorecard_id(self) -> UUID | None:
        return self._read(lambda cursor: self._latest_id(cursor, "strategy_scorecards", "scorecard_id", "evaluated_at"))

    def workspace_references(self) -> DashboardWorkspaceReferences:
        """Resolve coherent optional dashboard defaults in one read-only query.

        Empty databases intentionally return explicit null references.  The
        caller must never substitute an arbitrary row or fabricate evidence.
        """
        def operation(cursor: _Cursor) -> DashboardWorkspaceReferences:
            # Scorecard -> experiment -> strategy is the strongest complete
            # research chain.  Fallbacks remain deterministic for an empty or
            # partially migrated deployment, but never choose a database row
            # merely because it happened to be returned first.
            cursor.execute(
                "SELECT scorecard_id,strategy_id,research_run_id,dataset_version FROM strategy_scorecards "
                "ORDER BY evaluated_at DESC,scorecard_id DESC LIMIT 1"
            )
            scorecard_row = cursor.fetchone()
            scorecard = None if scorecard_row is None else UUID(str(scorecard_row[0]))
            scorecard_strategy = None if scorecard_row is None else UUID(str(scorecard_row[1]))
            scorecard_experiment = None if scorecard_row is None else UUID(str(scorecard_row[2]))
            scorecard_dataset = None if scorecard_row is None else str(scorecard_row[3])
            if scorecard_experiment is not None:
                cursor.execute(
                    "SELECT e.experiment_id,d.strategy_id FROM research_experiments e "
                    "JOIN strategy_versions v USING(strategy_version_id) "
                    "JOIN strategy_definitions d USING(strategy_id) WHERE e.experiment_id=%s",
                    (scorecard_experiment,),
                )
                experiment_row = cursor.fetchone()
            else:
                experiment_row = None
            if experiment_row is None:
                cursor.execute(
                    "SELECT e.experiment_id,d.strategy_id FROM research_experiments e "
                    "JOIN strategy_versions v USING(strategy_version_id) "
                    "JOIN strategy_definitions d USING(strategy_id) "
                    "WHERE (CAST(%s AS uuid) IS NULL OR d.strategy_id=%s) "
                    "ORDER BY e.created_at DESC,e.experiment_id DESC LIMIT 1",
                    (scorecard_strategy, scorecard_strategy),
                )
                experiment_row = cursor.fetchone()
            experiment = None if experiment_row is None else UUID(str(experiment_row[0]))
            strategy = scorecard_strategy if scorecard_strategy is not None else (
                None if experiment_row is None else UUID(str(experiment_row[1]))
            )
            if strategy is None:
                cursor.execute(
                    "SELECT strategy_id FROM strategy_versions ORDER BY created_at DESC,strategy_version_id DESC LIMIT 1"
                )
                row = cursor.fetchone()
                strategy = None if row is None else UUID(str(row[0]))
            cursor.execute(
                "SELECT feature_id,instrument_id,dataset_version,computed_at FROM feature_materializations "
                "WHERE (CAST(%s AS text) IS NULL OR dataset_version=%s) "
                "ORDER BY computed_at DESC,materialization_id DESC LIMIT 1"
                , (scorecard_dataset, scorecard_dataset)
            )
            feature = cursor.fetchone()
            cursor.execute(
                "SELECT run_id,regime_run_id FROM portfolio_construction_runs "
                "ORDER BY constructed_at DESC,run_id DESC LIMIT 1"
            )
            portfolio_row = cursor.fetchone()
            portfolio = None if portfolio_row is None else UUID(str(portfolio_row[0]))
            regime = None if portfolio_row is None else UUID(str(portfolio_row[1]))
            if regime is None:
                regime = self._latest_id(cursor, "regime_runs", "run_id", "evaluated_at")
            service = self._latest_id(cursor, "sre_service_versions", "service_version_id", "created_at")
            cursor.execute(
                "SELECT l.instrument_id FROM news_document_entity_links l "
                "JOIN news_document_revisions d USING(document_revision_id) "
                "ORDER BY d.published_at DESC,l.entity_link_id DESC LIMIT 1"
            )
            news = cursor.fetchone()
            cursor.execute("SELECT instrument_id FROM professional_instruments ORDER BY registered_at DESC,instrument_id DESC LIMIT 1")
            instrument = cursor.fetchone()
            cursor.execute("SELECT thesis_id FROM investment_theses ORDER BY created_at DESC,thesis_id DESC LIMIT 1")
            thesis = cursor.fetchone()
            cursor.execute(
                "SELECT account_id FROM investment_rebalance_candidates "
                "ORDER BY as_of DESC,candidate_id DESC LIMIT 1"
            )
            investment_portfolio = cursor.fetchone()
            cursor.execute("SELECT intent_id,account_id FROM paper_order_intents ORDER BY created_at DESC,intent_id DESC LIMIT 1")
            paper = cursor.fetchone()
            available = any(value is not None for value in (
                feature, scorecard, regime, portfolio, service, news, instrument, strategy, experiment,
                thesis, investment_portfolio, paper,
            ))
            return DashboardWorkspaceReferences(
                state="AVAILABLE" if available else "UNAVAILABLE",
                as_of=None if feature is None else feature[3],
                feature_definition_id=None if feature is None else feature[0],
                feature_instrument=None if feature is None else str(feature[1]),
                feature_dataset_version=None if feature is None else str(feature[2]),
                feature_decision_time=None if feature is None else feature[3],
                scorecard_id=scorecard, regime_run_id=regime,
                portfolio_construction_run_id=portfolio, sre_service_version_id=service,
                news_instrument=None if news is None else str(news[0]),
                instrument_id=None if instrument is None else str(instrument[0]),
                strategy_id=strategy, experiment_id=experiment,
                investment_thesis_id=None if thesis is None else thesis[0],
                investment_portfolio_id=None if investment_portfolio is None else str(investment_portfolio[0]),
                paper_intent_id=None if paper is None else paper[0],
                paper_account_id=None if paper is None else str(paper[1]),
                limitations=["Defaults are bounded latest authoritative records; explicit deployment configuration overrides them."]
            )
        return self._read(operation)

    @staticmethod
    def _latest_id(cursor: _Cursor, table: str, identity: str, timestamp: str) -> UUID | None:
        statements = {
            ("strategy_scorecards", "scorecard_id", "evaluated_at"): "SELECT scorecard_id FROM strategy_scorecards ORDER BY evaluated_at DESC,scorecard_id DESC LIMIT 1",
            ("regime_runs", "run_id", "evaluated_at"): "SELECT run_id FROM regime_runs ORDER BY evaluated_at DESC,run_id DESC LIMIT 1",
            ("portfolio_construction_runs", "run_id", "constructed_at"): "SELECT run_id FROM portfolio_construction_runs ORDER BY constructed_at DESC,run_id DESC LIMIT 1",
            ("sre_service_versions", "service_version_id", "created_at"): "SELECT service_version_id FROM sre_service_versions ORDER BY created_at DESC,service_version_id DESC LIMIT 1",
        }
        statement = statements.get((table, identity, timestamp))
        if statement is None:
            raise DashboardQueryError("invalid_latest_query")
        cursor.execute(statement)
        row = cursor.fetchone()
        return None if row is None else UUID(str(row[0]))

    def regime_runs(
        self, *, instrument: str | None, status: str | None, model_version_id: UUID | None,
        dataset_version: str | None, limit: int, offset: int,
    ) -> RegimeRunDiscoveryPage:
        """Bounded regime-run discovery; no calculation, only persisted evidence ordering."""
        def operation(cursor: _Cursor) -> RegimeRunDiscoveryPage:
            cursor.execute(
                "SELECT r.run_id,r.model_version_id,m.version,m.implementation_version,r.dataset_version,"
                "r.instrument_id,r.evaluated_at,r.status "
                "FROM regime_runs r JOIN regime_model_versions m USING(model_version_id) "
                "WHERE (CAST(%s AS text) IS NULL OR r.instrument_id=%s) "
                "AND (CAST(%s AS text) IS NULL OR r.status=%s) "
                "AND (CAST(%s AS uuid) IS NULL OR r.model_version_id=%s) "
                "AND (CAST(%s AS text) IS NULL OR r.dataset_version=%s) "
                "ORDER BY r.evaluated_at DESC,r.run_id DESC LIMIT %s OFFSET %s",
                (
                    instrument, instrument, status, status, model_version_id, model_version_id,
                    dataset_version, dataset_version, limit + 1, offset,
                ),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            run_ids = [row[0] for row in rows]
            dims_by_run: dict[UUID, list[RegimeRunDimensionSummaryView]] = {UUID(str(value)): [] for value in run_ids}
            if run_ids:
                cursor.execute(
                    "SELECT ro.run_id,o.dimension,o.hard_label,o.probabilities,o.uncertainty "
                    "FROM regime_run_observations ro JOIN regime_observations o USING(observation_id) "
                    "WHERE ro.run_id=ANY(%s) ORDER BY ro.run_id,ro.sequence",
                    (run_ids,),
                )
                for item in cursor.fetchall():
                    run_key = UUID(str(item[0]))
                    probability_map = _mapping(item[3])
                    top_state, top_value = _top_probability(probability_map)
                    dims_by_run.setdefault(run_key, []).append(RegimeRunDimensionSummaryView(
                        dimension=str(item[1]), hard_label=None if item[2] is None else str(item[2]),
                        top_probability_state=top_state, top_probability=top_value, uncertainty=_decimal(item[4]),
                    ))
            items: list[RegimeRunDiscoveryView] = []
            for row in rows:
                dims = dims_by_run.get(UUID(str(row[0])), [])
                uncertainty_summary = "; ".join(
                    f"{dim.dimension}={dim.uncertainty if dim.uncertainty is not None else 'UNAVAILABLE'}"
                    for dim in dims
                ) if dims else "UNAVAILABLE"
                items.append(RegimeRunDiscoveryView(
                    run_id=row[0], model_version_id=row[1], model_version=str(row[2]), rule_version=str(row[3]),
                    dataset_version=str(row[4]), instrument=str(row[5]), as_of_timestamp=row[6], status=str(row[7]),
                    dimension_summary=dims, uncertainty_summary=uncertainty_summary,
                ))
            return RegimeRunDiscoveryPage(state="AVAILABLE" if items else "UNAVAILABLE", items=items, page=page)
        return self._read(operation)

    def regime_run(self, run_id: UUID) -> RegimeRunView:
        def operation(cursor: _Cursor) -> RegimeRunView:
            cursor.execute(
                "SELECT r.*,m.version,m.implementation_version FROM regime_runs r "
                "JOIN regime_model_versions m USING(model_version_id) WHERE r.run_id=%s", (run_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise DashboardObjectNotFound("regime_run_not_found")
            cursor.execute(
                "SELECT o.* FROM regime_run_observations ro JOIN regime_observations o USING(observation_id) "
                "WHERE ro.run_id=%s ORDER BY ro.sequence", (run_id,),
            )
            dimensions: list[RegimeDimensionView] = []
            materialization_ids: list[UUID] = []
            for item in cursor.fetchall():
                probability_map = _mapping(item[6])
                inputs = [UUID(value) for value in _strings(item[8])]
                materialization_ids.extend(inputs)
                dimensions.append(RegimeDimensionView(
                    observation_id=item[0], event_time=item[1], method=str(item[2]), dimension=str(item[3]),
                    evidence_state=str(item[4]), hard_label=None if item[5] is None else str(item[5]),
                    probabilities=[RegimeProbabilityView(state=key, probability=str(value)) for key, value in sorted(probability_map.items())],
                    uncertainty=_decimal(item[7]), input_materialization_ids=inputs, content_hash=str(item[9]),
                ))
            knowledge = None
            if materialization_ids:
                cursor.execute("SELECT MAX(knowledge_at) FROM feature_materializations WHERE materialization_id=ANY(%s)", (materialization_ids,))
                found = cursor.fetchone()
                knowledge = None if found is None else found[0]
            cursor.execute("SELECT * FROM regime_risk_adjustment_candidates WHERE run_id=%s ORDER BY created_at,candidate_id", (run_id,))
            effects = [RegimeRiskEffectView(
                candidate_id=item[0], strategy_version_id=item[2], current_risk_multiplier=str(item[3]),
                proposed_risk_multiplier=str(item[4]), preapproved_maximum=str(item[5]), action=str(item[6]),
                status=str(item[7]), reasons=_strings(item[8]), automatic_authority=False,
            ) for item in cursor.fetchall()]
            return RegimeRunView(
                regime_assessment_id=row[0], model_version_id=row[1], dataset_version=str(row[3]),
                instrument=str(row[5]), as_of_timestamp=row[6], knowledge_timestamp=knowledge,
                status=str(row[7]), limitations=_strings(row[8]), evidence_hash=str(row[10]),
                model_version=str(row[11]), rule_version=str(row[12]), dimensions=dimensions,
                risk_effects=effects,
                risk_boundary="REGIME MAY REDUCE OR BLOCK RISK; REGIME CANNOT INCREASE GLOBAL RISK LIMITS",
            )
        return self._read(operation)

    def latest_regime_run_id(self) -> UUID | None:
        return self._read(lambda cursor: self._latest_id(cursor, "regime_runs", "run_id", "evaluated_at"))

    def portfolio_construction_runs(
        self, *, status: str | None, policy_version_id: UUID | None, regime_run_id: UUID | None,
        limit: int, offset: int,
    ) -> PortfolioConstructionDiscoveryPage:
        """Bounded portfolio-construction-run discovery; every run carries risk-gate evidence."""
        def operation(cursor: _Cursor) -> PortfolioConstructionDiscoveryPage:
            cursor.execute(
                "SELECT r.run_id,r.policy_version_id,p.version,r.regime_run_id,r.constructed_at,r.status,"
                "r.equity,p.policy,t.portfolio_volatility,t.stressed_volatility,g.approved "
                "FROM portfolio_construction_runs r "
                "JOIN portfolio_construction_policy_versions p USING(policy_version_id) "
                "JOIN portfolio_target_candidates t USING(run_id) "
                "JOIN portfolio_risk_gate_evidence g USING(run_id) "
                "WHERE (CAST(%s AS text) IS NULL OR r.status=%s) "
                "AND (CAST(%s AS uuid) IS NULL OR r.policy_version_id=%s) "
                "AND (CAST(%s AS uuid) IS NULL OR r.regime_run_id=%s) "
                "ORDER BY r.constructed_at DESC,r.run_id DESC LIMIT %s OFFSET %s",
                (
                    status, status, policy_version_id, policy_version_id, regime_run_id, regime_run_id,
                    limit + 1, offset,
                ),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            items = [PortfolioConstructionDiscoveryView(
                run_id=row[0], policy_version_id=row[1], policy_version=str(row[2]), regime_run_id=row[3],
                constructed_at=row[4], status=str(row[5]), review_only=True, automatic_authority=False,
                equity=str(row[6]), target_volatility=_decimal(_mapping(row[7]).get("target_volatility")),
                portfolio_volatility=str(row[8]), stressed_volatility=str(row[9]), risk_gate_approved=bool(row[10]),
            ) for row in rows]
            return PortfolioConstructionDiscoveryPage(state="AVAILABLE" if items else "UNAVAILABLE", items=items, page=page)
        return self._read(operation)

    def portfolio_construction(self, run_id: UUID) -> PortfolioConstructionView:
        def operation(cursor: _Cursor) -> PortfolioConstructionView:
            cursor.execute(
                "SELECT r.run_id,r.policy_version_id,r.regime_run_id,r.constructed_at,r.status,"
                "r.equity,r.limitations,r.content_hash,p.version,p.policy,"
                "t.cash_weight,t.gross_weight,t.net_weight,t.portfolio_volatility,t.stressed_volatility,"
                "c.covariance_id,c.dataset_version,c.dataset_content_hash,c.estimation_version,"
                "c.observations,c.as_of,c.uncertainty,c.correlation_stress,"
                "hs.provider,hs.provider_identifier_namespace,hs.provider_terms_version,"
                "hs.authorization_reference "
                "FROM portfolio_construction_runs r "
                "JOIN portfolio_construction_policy_versions p USING(policy_version_id) "
                "JOIN portfolio_target_candidates t USING(run_id) JOIN portfolio_covariance_estimates c USING(run_id) "
                "JOIN historical_dataset_versions hd ON hd.dataset_version_id=c.dataset_version_id "
                "JOIN historical_data_sources hs ON hs.source_id=hd.source_id "
                "WHERE r.run_id=%s", (run_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise DashboardObjectNotFound("portfolio_construction_not_found")
            policy = _mapping(row[9])
            cursor.execute(
                "SELECT s.sleeve_input_id,s.strategy_key,s.payload,w.target_weight,w.effective_notional,"
                "w.marginal_risk,w.component_risk,w.adjustment_reasons FROM portfolio_sleeve_inputs s "
                "LEFT JOIN portfolio_target_candidates t ON t.run_id=s.run_id "
                "LEFT JOIN portfolio_target_sleeve_weights w ON w.candidate_id=t.candidate_id AND w.sleeve_input_id=s.sleeve_input_id "
                "WHERE s.run_id=%s ORDER BY s.sequence", (run_id,),
            )
            sleeves: list[PortfolioSleeveView] = []
            for item in cursor.fetchall():
                payload = _mapping(item[2])
                review = _decimal(item[3])
                reasons = _strings(item[7])
                rejected = review is None or review == "0"
                sleeves.append(PortfolioSleeveView(
                    sleeve_input_id=item[0], strategy_key=str(item[1]),
                    requested_allocation=str(payload.get("risk_budget", "0")), review_allocation=review,
                    effective_notional=_decimal(item[4]), risk_budget=str(payload.get("risk_budget", "0")),
                    capacity_weight=str(payload.get("capacity_weight", "0")), liquidity_score=str(payload.get("liquidity_score", "0")),
                    drawdown=str(payload.get("drawdown", "0")), regime_current_multiplier=str(payload.get("regime_current_multiplier", "0")),
                    regime_proposed_multiplier=str(payload.get("regime_proposed_multiplier", "0")),
                    marginal_risk=_decimal(item[5]), component_risk=_decimal(item[6]), adjustment_reasons=reasons,
                    rejected=rejected, rejection_reasons=reasons if rejected else [],
                ))
            cursor.execute("SELECT * FROM portfolio_constraint_evaluations WHERE run_id=%s ORDER BY name", (run_id,))
            constraints = [PortfolioConstraintView(
                constraint_id=item[0], name=str(item[2]), state=str(item[3]), observed=_decimal(item[4]),
                limit=_decimal(item[5]), reasons=_strings(item[6]),
            ) for item in cursor.fetchall()]
            cursor.execute("SELECT approved,reasons FROM portfolio_risk_gate_evidence WHERE run_id=%s", (run_id,))
            gate = cursor.fetchone()
            if gate is None:
                raise DashboardQueryError("portfolio_risk_gate_missing")
            dataset_version = str(row[16])
            # Module 2B-3 truthfulness fix: provider-backed status must come from the same
            # fail-closed allowlist as classify_research_evidence(), never from a heuristic
            # over provider_identifier_namespace/authorization_reference text -- those are
            # free-text fields any synthetic fixture can populate, and doing so previously
            # let a demo/fixture covariance render as PROVIDER_BACKED_COVARIANCE.
            provider_backed = str(row[23]).strip() in _AUTHORIZED_REAL_MARKET_DATA_PROVIDERS
            return PortfolioConstructionView(
                portfolio_construction_run_id=row[0], policy_version_id=row[1], regime_run_id=row[2],
                constructed_at=row[3], status=str(row[4]), review_only=True, automatic_authority=False,
                equity=str(row[5]), limitations=_strings(row[6]), content_hash=str(row[7]),
                policy_version=str(row[8]), target_volatility=_decimal(policy.get("target_volatility")),
                cash_weight=str(row[10]), gross_weight=str(row[11]), net_weight=str(row[12]),
                portfolio_volatility=str(row[13]), stressed_volatility=str(row[14]),
                risk_gate_approved=bool(gate[0]), risk_gate_reasons=_strings(gate[1]),
                covariance=CovarianceEvidenceView(
                    covariance_id=row[15], dataset_version=dataset_version, dataset_content_hash=str(row[17]),
                    estimation_version=str(row[18]), observations=int(row[19]), as_of=row[20],
                    uncertainty=str(row[21]), correlation_stress=str(row[22]),
                    source_provider=str(row[23]), source_terms_version=str(row[25]), provider_backed=provider_backed,
                    classification=(
                        f"PROVIDER_BACKED_COVARIANCE; SOURCE={row[23]}; TERMS={row[25]}"
                        if provider_backed else
                        f"NO_REAL_PROVIDER_BACKED_COVARIANCE_EVIDENCE; SOURCE={row[23]}; TERMS={row[25]}"
                    ),
                ), sleeves=sleeves, constraints=constraints,
            )
        return self._read(operation)

    def latest_portfolio_run_id(self) -> UUID | None:
        return self._read(lambda cursor: self._latest_id(cursor, "portfolio_construction_runs", "run_id", "constructed_at"))

    def news_events(
        self, *, instrument: str | None, entity: str | None, category: str | None,
        start: datetime | None, end: datetime | None, correction_state: str | None,
        limit: int, offset: int,
    ) -> NewsEventPage:
        def operation(cursor: _Cursor) -> NewsEventPage:
            cursor.execute(
                "SELECT d.document_revision_id,d.source_policy_version_id,d.revision,d.revision_kind,d.source_url,d.headline,"
                "d.published_at,d.source_updated_at,d.ingested_at,d.content_fingerprint,d.content_hash,"
                "s.provider,s.version,s.terms_version,s.rights_status,s.provider_activated,s.authorization_reference,"
                "x.extraction_id,x.event_type,x.novelty,x.urgency,x.uncertainty,x.horizon,x.limitations,"
                "a.status,a.credibility FROM news_document_revisions d "
                "JOIN news_source_policy_versions s USING(source_policy_version_id) "
                "JOIN news_event_extractions x USING(document_revision_id) "
                "LEFT JOIN news_event_assessments a ON a.document_revision_id=d.document_revision_id AND a.extraction_id=x.extraction_id "
                "WHERE (CAST(%s AS text) IS NULL OR x.event_type=%s) "
                "AND (CAST(%s AS timestamptz) IS NULL OR d.published_at>=%s) "
                "AND (CAST(%s AS timestamptz) IS NULL OR d.published_at<=%s) "
                "AND (CAST(%s AS text) IS NULL OR d.revision_kind=%s) "
                "AND (CAST(%s AS text) IS NULL OR EXISTS (SELECT 1 FROM news_document_entity_links l WHERE l.document_revision_id=d.document_revision_id AND l.instrument_id=%s)) "
                "AND (CAST(%s AS text) IS NULL OR d.headline ILIKE '%%' || %s || '%%') "
                "ORDER BY d.published_at DESC,d.document_revision_id,x.extraction_id LIMIT %s OFFSET %s",
                (category, category, start, start, end, end, correction_state, correction_state,
                 instrument, instrument, entity, entity, limit + 1, offset),
            )
            rows, page = _page(cursor.fetchall(), limit, offset)
            document_ids = [row[0] for row in rows]
            entity_map: dict[UUID, list[NewsEntityView]] = {UUID(str(value)): [] for value in document_ids}
            lineage_map: dict[UUID, list[NewsLineageView]] = {UUID(str(value)): [] for value in document_ids}
            if document_ids:
                cursor.execute(
                    "SELECT document_revision_id,entity_link_id,instrument_id,link_method,confidence,ambiguous "
                    "FROM news_document_entity_links WHERE document_revision_id=ANY(%s) "
                    "ORDER BY document_revision_id,instrument_id",
                    (document_ids,),
                )
                for linked_entity in cursor.fetchall():
                    entity_map[UUID(str(linked_entity[0]))].append(NewsEntityView(
                        entity_link_id=linked_entity[1], instrument=str(linked_entity[2]), method=str(linked_entity[3]),
                        confidence=str(linked_entity[4]), ambiguous=bool(linked_entity[5]),
                    ))
                cursor.execute(
                    "SELECT predecessor_document_revision_id,successor_document_revision_id,relation "
                    "FROM news_event_lineage WHERE predecessor_document_revision_id=ANY(%s) "
                    "OR successor_document_revision_id=ANY(%s) "
                    "ORDER BY predecessor_document_revision_id,successor_document_revision_id",
                    (document_ids, document_ids),
                )
                for lineage_row in cursor.fetchall():
                    view = NewsLineageView(
                        predecessor_id=lineage_row[0], successor_id=lineage_row[1], relation=str(lineage_row[2])
                    )
                    for member in (UUID(str(lineage_row[0])), UUID(str(lineage_row[1]))):
                        if member in lineage_map:
                            lineage_map[member].append(view)
            items: list[NewsEventView] = []
            activated = False
            approved = False
            for row in rows:
                entities = entity_map[UUID(str(row[0]))]
                lineage_views = lineage_map[UUID(str(row[0]))]
                activated = activated or bool(row[15])
                approved = approved or str(row[14]) == "APPROVED"
                items.append(NewsEventView(
                    event_id=row[17], document_revision_id=row[0], source=str(row[11]), source_version=str(row[12]),
                    source_terms_version=str(row[13]), published_at=row[6], source_updated_at=row[7], ingested_at=row[8],
                    correction_or_retraction_at=row[7] if str(row[3]) in {"CORRECTION", "RETRACTION"} else None,
                    revision=int(row[2]), revision_kind=str(row[3]), headline=str(row[5]), category=str(row[18]),
                    novelty=str(row[19]), credibility=_decimal(row[25]), uncertainty=str(row[21]), urgency=str(row[20]),
                    horizon=str(row[22]), assessment_status=None if row[24] is None else str(row[24]),
                    rights_state=str(row[14]), authorization_state=("AUTHORIZED" if str(row[14]) == "APPROVED" else "NOT_AUTHORIZED"),
                    provider_activated=bool(row[15]), content_fingerprint=str(row[9]), provenance_reference=str(row[4]),
                    limitations=_strings(row[23]), entities=entities, correction_chain=lineage_views,
                ))
            provider_state = "AVAILABLE" if activated and approved else "EXTERNAL_BLOCKED"
            state: Availability = "AVAILABLE" if items else ("EXTERNAL_BLOCKED" if not activated else "UNAVAILABLE")
            return NewsEventPage(state=state, provider_state=provider_state, items=items, page=page)
        return self._read(operation)

    def sre_overview(self, service_version_id: UUID | None = None) -> SreOverviewView:
        def operation(cursor: _Cursor) -> SreOverviewView:
            selected = service_version_id or self._latest_id(cursor, "sre_service_versions", "service_version_id", "created_at")
            if selected is None:
                raise DashboardObjectNotFound("sre_overview_not_found")
            cursor.execute("SELECT * FROM sre_service_versions WHERE service_version_id=%s", (selected,))
            row = cursor.fetchone()
            if row is None:
                raise DashboardObjectNotFound("sre_overview_not_found")
            cursor.execute("SELECT DISTINCT ON (dependency) dependency,status,checked_at,latency_ms,reason FROM sre_dependency_probes WHERE service_version_id=%s ORDER BY dependency,checked_at DESC,probe_id DESC", (selected,))
            dependencies = [DependencyHealthView(dependency=str(item[0]), status=str(item[1]), checked_at=item[2], latency_ms=_decimal(item[3]), reason=None if item[4] is None else str(item[4])) for item in cursor.fetchall()]
            cursor.execute(
                "SELECT p.slo_policy_version_id,p.name,p.indicator,p.objective,p.window_seconds,w.measured_ratio,w.evidence_state,w.window_start,w.window_end,w.claim_status "
                "FROM sre_slo_policy_versions p LEFT JOIN LATERAL (SELECT * FROM sre_sli_windows w WHERE w.slo_policy_version_id=p.slo_policy_version_id ORDER BY w.window_end DESC,w.sli_window_id DESC LIMIT 1) w ON TRUE "
                "WHERE p.service_version_id=%s ORDER BY p.name", (selected,),
            )
            slos = [SloEvidenceView(
                slo_policy_version_id=item[0], name=str(item[1]), indicator=str(item[2]), target=str(item[3]),
                target_state="TARGET", window_seconds=int(item[4]), measured_value=_decimal(item[5]),
                measured_state="UNAVAILABLE" if item[6] is None else str(item[6]), window_start=item[7], window_end=item[8],
                claim_status=None if item[9] is None else str(item[9]),
            ) for item in cursor.fetchall()]
            cursor.execute(
                "SELECT i.incident_id,i.severity,p.code,i.declared_at,ack.acknowledged_at,i.resolved_at,i.status,i.summary,i.runbook_uri "
                "FROM sre_incidents i JOIN sre_alerts a USING(alert_id) JOIN sre_alert_policy_versions p USING(alert_policy_version_id) "
                "LEFT JOIN LATERAL (SELECT MIN(occurred_at) acknowledged_at FROM sre_alert_events e WHERE e.alert_id=i.alert_id AND e.status='ACKNOWLEDGED') ack ON TRUE "
                "WHERE p.service_version_id=%s ORDER BY i.declared_at DESC,i.incident_id", (selected,),
            )
            incidents = [IncidentView(
                incident_id=item[0], severity=str(item[1]), subsystem=str(item[2]), opened_at=item[3],
                acknowledged_at=item[4], resolved_at=item[5], status=str(item[6]),
                reason="; ".join(f"{key}={value}" for key, value in sorted(_mapping(item[7]).items())),
                evidence_reference=str(item[8]),
            ) for item in cursor.fetchall()]
            cursor.execute("SELECT drill_run_id,scenario,expected_protection,observed_protection,completed_at,passed,evidence_uri FROM sre_failure_drill_runs WHERE service_version_id=%s ORDER BY completed_at DESC,drill_run_id", (selected,))
            drills = [FailureDrillView(drill_run_id=item[0], scenario=str(item[1]), expected_protection=str(item[2]), observed_protection=str(item[3]), completed_at=item[4], passed=bool(item[5]), evidence_reference=str(item[6])) for item in cursor.fetchall()]
            by_name = {item.dependency.casefold(): item for item in dependencies}
            def status(*names: str) -> str:
                found = next((item.status for name in names for key, item in by_name.items() if name in key), None)
                return found or "UNAVAILABLE"
            overall: Availability = "BLOCKED" if any(item.status == "UNAVAILABLE" for item in dependencies) else "AVAILABLE"
            return SreOverviewView(
                state=overall, service_version_id=row[0], subsystem=str(row[2]), version=str(row[3]),
                environment=str(row[5]), deployment_status=str(row[6]), postgres_state=status("postgres", "database"),
                provider_state=status("provider"), ingestion_checkpoint_freshness=status("ingestion"),
                dataset_freshness=status("dataset"), feature_freshness=status("feature"),
                research_job_health=status("research", "strategy"), signal_freshness=status("signal"),
                risk_status=status("risk"), reconciliation_status=status("reconciliation"),
                backup_restore_status=("PASSED" if drills and drills[0].passed else "UNAVAILABLE"),
                kill_switch_state=status("kill-switch", "kill switch"), dependencies=dependencies,
                slos=slos, incidents=incidents, failure_drills=drills,
            )
        return self._read(operation)

    def command_summaries(self) -> list[AuthoritySummary]:
        """Concise latest-record links; underlying workspaces remain authoritative."""
        scorecard_id = self.latest_scorecard_id()
        regime_id = self.latest_regime_run_id()
        portfolio_id = self.latest_portfolio_run_id()
        def operation(cursor: _Cursor) -> list[AuthoritySummary]:
            cursor.execute("SELECT MAX(computed_at) FROM feature_materializations")
            feature_row = cursor.fetchone()
            feature_at = None if feature_row is None else feature_row[0]
            cursor.execute(
                "SELECT source_id,provider,provider_terms_version,authorized_at FROM historical_data_sources "
                "ORDER BY authorized_at DESC,source_id DESC LIMIT 1"
            )
            provider = cursor.fetchone()
            cursor.execute(
                "SELECT dataset_version_id,version,status,created_at FROM historical_dataset_versions "
                "ORDER BY created_at DESC,dataset_version_id DESC LIMIT 1"
            )
            dataset = cursor.fetchone()
            cursor.execute(
                "SELECT assessment_id,blocking,evaluated_at,max_action FROM data_health_assessments "
                "ORDER BY evaluated_at DESC,assessment_id DESC LIMIT 1"
            )
            health = cursor.fetchone()
            cursor.execute(
                "SELECT x.extraction_id,d.published_at,s.rights_status,s.provider_activated "
                "FROM news_event_extractions x JOIN news_document_revisions d USING(document_revision_id) "
                "JOIN news_source_policy_versions s USING(source_policy_version_id) "
                "ORDER BY d.published_at DESC,x.extraction_id DESC LIMIT 1"
            )
            news = cursor.fetchone()
            cursor.execute(
                "SELECT p.signal_id,p.created_at,(p.expires_at<=now()),COALESCE(e.to_status,v.status,'CANDIDATE') "
                "FROM runtime_signal_proposals p "
                "LEFT JOIN LATERAL (SELECT to_status FROM runtime_signal_lifecycle_events WHERE signal_id=p.signal_id ORDER BY event_sequence DESC LIMIT 1) e ON TRUE "
                "LEFT JOIN LATERAL (SELECT status FROM runtime_signal_validations WHERE signal_id=p.signal_id ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1) v ON TRUE "
                "ORDER BY p.created_at DESC,p.signal_id DESC LIMIT 1"
            )
            signal = cursor.fetchone()
            cursor.execute("SELECT incident_id,declared_at,status FROM sre_incidents ORDER BY declared_at DESC,incident_id DESC LIMIT 1")
            incident = cursor.fetchone()
            return [
                AuthoritySummary(id="provider-authorization", status="UNAVAILABLE" if provider is None else "AVAILABLE", as_of=None if provider is None else provider[3], evidence_id=None if provider is None else str(provider[0]), detail="No provider contact: latest configured authorization record" if provider is None else f"{provider[1]} terms {provider[2]} configured; activation is not implied"),
                AuthoritySummary(id="dataset", status="UNAVAILABLE" if dataset is None else "AVAILABLE", as_of=None if dataset is None else dataset[3], evidence_id=None if dataset is None else str(dataset[0]), detail="Latest sealed dataset unavailable" if dataset is None else f"{dataset[1]}: {dataset[2]}"),
                AuthoritySummary(id="data-health", status="UNAVAILABLE" if health is None else ("BLOCKED" if bool(health[1]) else "AVAILABLE"), as_of=None if health is None else health[2], evidence_id=None if health is None else str(health[0]), detail="Latest Data Health assessment unavailable" if health is None else f"Latest blocking={bool(health[1])}; max action={health[3]}"),
                AuthoritySummary(id="features", status="AVAILABLE" if feature_at else "UNAVAILABLE", as_of=feature_at, evidence_id=None, detail="Latest feature materialization"),
                AuthoritySummary(id="signals", status="UNAVAILABLE" if signal is None else ("BLOCKED" if bool(signal[2]) and str(signal[3]) in {"VALIDATED", "WAITING_FOR_ENTRY", "ACTIVE"} else "AVAILABLE"), as_of=None if signal is None else signal[1], evidence_id=None if signal is None else str(signal[0]), detail="Latest immutable reasoned signal lifecycle; no execution authority"),
                AuthoritySummary(id="scorecard", status="AVAILABLE" if scorecard_id else "UNAVAILABLE", as_of=None, evidence_id=None if scorecard_id is None else str(scorecard_id), detail="Latest strategy scorecard"),
                AuthoritySummary(id="regime", status="AVAILABLE" if regime_id else "UNAVAILABLE", as_of=None, evidence_id=None if regime_id is None else str(regime_id), detail="Latest regime run"),
                AuthoritySummary(id="portfolio-construction", status="AVAILABLE" if portfolio_id else "UNAVAILABLE", as_of=None, evidence_id=None if portfolio_id is None else str(portfolio_id), detail="Latest review-only construction"),
                AuthoritySummary(id="news", status="AVAILABLE" if news is not None and str(news[2]) == "APPROVED" and bool(news[3]) else "EXTERNAL_BLOCKED", as_of=None if news is None else news[1], evidence_id=None if news is None else str(news[0]), detail="Provider authorization and activation are authoritative source-policy fields"),
                AuthoritySummary(id="operations", status="UNAVAILABLE" if incident is None else ("BLOCKED" if str(incident[2]) == "DECLARED" else "AVAILABLE"), as_of=None if incident is None else incident[1], evidence_id=None if incident is None else str(incident[0]), detail="Latest SRE incident state"),
            ]
        return self._read(operation)
