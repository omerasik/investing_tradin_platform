"""Local-only API exposing system state and audit events, never execution controls."""

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .agent_research import AgentResearchError, AgentResearchOutput, SQLiteAgentResearchStore
from .audit import AuditEvent, AuditStore, SQLiteAuditStore
from .config import PlatformConfig
from .fundamentals import SQLiteFundamentalStore
from .investments import (
    InvestmentValidationError,
    SQLiteInvestmentStore,
    materialize_fundamental_facts_authorized,
)
from .observability import MetricsRegistry, StructuredEventLogger
from .operational_alerts import (
    AlertError,
    AlertStatus,
    PostgresOperationalAlertStore,
    SQLiteOperationalAlertStore,
)
from .operator_dashboard import (
    DashboardObjectNotFound,
    DashboardQueryError,
    DashboardWorkspaceReferences,
    DataHealthAssessmentPage,
    DataHealthAssessmentView,
    ExperimentDiscoveryPage,
    FeatureDefinitionPage,
    FeatureDefinitionView,
    FeatureMaterializationPage,
    HistoricalDatasetPage,
    InstrumentDetailView,
    InstrumentDiscoveryPage,
    InvestmentPortfolioDiscoveryPage,
    InvestmentThesisDiscoveryPage,
    NewsEventPage,
    PageInfo,
    PaperOrderDiscoveryPage,
    PaperOrderView,
    PaperReconciliationView,
    PortfolioConstructionDiscoveryPage,
    PortfolioConstructionView,
    PostgresOperatorDashboardQueries,
    RegimeRunDiscoveryPage,
    RegimeRunView,
    RiskDecisionPage,
    SignalPage,
    SreOverviewView,
    StrategyDiscoveryPage,
    StrategyScorecardDiscoveryPage,
    StrategyScorecardView,
    classify_research_evidence_from_markers,
)
from .paper_oms import PaperOmsError, SQLitePaperOms
from .persistence import PersistenceTarget
from .portfolio_risk import (
    PortfolioExposure,
    PortfolioRiskPolicy,
    StressScenario,
    evaluate_portfolio,
)
from .postgres_paper_oms import PostgresPaperOms
from .research import CostModel, ResearchValidationError, SQLiteExperimentStore, WalkForwardProtocol
from .return_history import ReturnIngestionCadence, SQLitePortfolioReturnStore
from .risk import SQLiteRiskDecisionStore
from .security import (
    AuthorizationDecisionSink,
    InMemoryRateLimiter,
    OperatorAuthentication,
    OperatorAuthenticator,
    SecurityHeadersMiddleware,
    alert_reviewer_operator,
    audit_writer_operator,
    data_steward_operator,
    protected_operator,
    research_operator,
    risk_reviewer_operator,
)
from .signal_engine import DetailedSignalStatus
from .strategy_promotion import SQLitePromotionLedger
from .strategy_validation import SQLiteStrategyRegistry, StrategyRunCard, StrategyValidationError


class AuditEventRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=120)
    actor: str = Field(min_length=1, max_length=120)
    payload: dict[str, object] = Field(default_factory=dict)


class AuditEventResponse(BaseModel):
    event_id: str
    event_type: str
    occurred_at: str
    actor: str
    payload: dict[str, object]


class ExposureRequest(BaseModel):
    instrument_id: str
    notional: float
    asset_class: str
    sector: str


class PortfolioRiskRequest(BaseModel):
    equity: float
    exposures: list[ExposureRequest]
    shocks: dict[str, float]
    maximum_gross_notional: float
    maximum_single_weight: float
    maximum_scenario_loss: float


class ReturnIngestionCadenceRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=120)
    interval_seconds: int = Field(gt=0, le=31_536_000)
    grace_seconds: int = Field(ge=0, le=31_536_000)


class FundamentalMaterializationRequest(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=120)
    as_of: datetime
    fact_names: list[str] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)


class BacktestLaunchRequest(BaseModel):
    strategy_id: UUID
    dataset_version: str = Field(min_length=1, max_length=120)
    parameters: dict[str, object]
    closes: list[str] = Field(min_length=2, max_length=2_000)
    fixed_per_turnover: str = "0"
    percentage_per_turnover: str = "0"
    spread_fraction_per_turnover: str = "0"
    pessimistic_cost_multiplier: str = "2"
    walk_forward_train_size: int | None = Field(default=None, ge=1, le=1_999)
    walk_forward_validation_size: int | None = Field(default=None, ge=1, le=1_999)
    walk_forward_test_size: int | None = Field(default=None, ge=1, le=1_999)
    walk_forward_step: int | None = Field(default=None, ge=1, le=1_999)
    walk_forward_purge: int = Field(default=0, ge=0, le=1_999)
    walk_forward_embargo: int = Field(default=0, ge=0, le=1_999)
    idempotency_key: str = Field(min_length=1, max_length=200)


class StrategyCreateRequest(BaseModel):
    strategy_version: str = Field(min_length=1, max_length=120); family: str = Field(min_length=1, max_length=80); hypothesis: str = Field(min_length=1, max_length=2_000)
    required_datasets: list[str] = Field(min_length=1, max_length=100); feature_versions: list[str] = Field(min_length=1, max_length=100)
    universe_rules: str = Field(min_length=1, max_length=2_000); entry_logic: str = Field(min_length=1, max_length=2_000); exit_logic: str = Field(min_length=1, max_length=2_000); sizing_policy: str = Field(min_length=1, max_length=2_000); risk_policy: str = Field(min_length=1, max_length=2_000); cost_model_version: str = Field(min_length=1, max_length=120); capacity_model: str = Field(min_length=1, max_length=2_000)
    expected_regimes: list[str] = Field(min_length=1, max_length=50); parameter_schema: dict[str, str] = Field(min_length=1, max_length=100); failure_conditions: list[str] = Field(min_length=1, max_length=100); limitations: list[str] = Field(min_length=1, max_length=100); idempotency_key: str = Field(min_length=1, max_length=200)


class OperatorEvidenceReference(BaseModel):
    id: str
    kind: str


class OperatorEvidenceState(BaseModel):
    """Stable, read-only dashboard contract; no client derives operational truth."""

    id: str
    status: str
    version: str
    source: str
    as_of: str
    freshness: str
    limitations: list[str]
    evidence_references: list[OperatorEvidenceReference]
    details: dict[str, object]


class CommandCenterResponse(BaseModel):
    id: str
    status: str
    version: str
    source: str
    as_of: str
    freshness: str
    limitations: list[str]
    evidence_references: list[OperatorEvidenceReference]
    platform_mode: str
    live_trading_enabled: bool
    states: list[OperatorEvidenceState]


def _serialize(event: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse(
        event_id=str(event.event_id),
        event_type=event.event_type,
        occurred_at=event.occurred_at.isoformat(),
        actor=event.actor,
        payload=event.payload,
    )


_REDACTED_PAYLOAD_KEY_MARKERS = (
    "token", "password", "secret", "dsn", "credential", "authorization",
    "api_key", "apikey", "access_key", "private_key", "session",
)


def _redact_payload(payload: dict[str, object]) -> dict[str, object]:
    """Typed-safe projection of an audit payload: strip keys that look secret-shaped.

    Only a shallow, defensive redaction -- audit payloads are operator-authored free-form
    metadata, never raw request/response bodies, so this is a safety net, not a parser.
    """
    safe: dict[str, object] = {}
    for key, value in payload.items():
        lowered = key.casefold()
        if any(marker in lowered for marker in _REDACTED_PAYLOAD_KEY_MARKERS):
            safe[key] = "REDACTED"
        elif isinstance(value, dict):
            safe[key] = _redact_payload(value)
        else:
            safe[key] = value
    return safe


AUDIT_IMMUTABILITY_GUARANTEE = (
    "Append-only SQLite table with no update or delete operation exposed by this store or "
    "API. This is a development-grade guarantee, not the PostgreSQL BEFORE UPDATE/DELETE "
    "immutability trigger used elsewhere on this platform (see kill_switch_events, "
    "broker_sync_events, paper_order_intents)."
)


class AuditEventProjection(BaseModel):
    event_id: str
    event_type: str
    actor: str
    occurred_at: str
    payload: dict[str, object]


class AuditEventDiscoveryPage(BaseModel):
    state: str
    items: list[AuditEventProjection]
    page: PageInfo
    audit_authority: str = "SQLITE_APPEND_ONLY_STORE"
    immutability_guarantee: str = AUDIT_IMMUTABILITY_GUARANTEE
    mutation_route_exposed_by_dashboard: bool = False


def _audit_projection(event: AuditEvent) -> AuditEventProjection:
    return AuditEventProjection(
        event_id=str(event.event_id),
        event_type=event.event_type,
        actor=event.actor,
        occurred_at=event.occurred_at.isoformat(),
        payload=_redact_payload(event.payload),
    )


def _research_output_response(item: AgentResearchOutput) -> dict[str, object]:
    return {
        "output_id": str(item.output_id),
        "instrument_id": item.instrument_id,
        "role": item.role.value,
        "created_at": item.created_at.isoformat(),
        "facts": item.facts,
        "inferences": item.inferences,
        "source_references": item.source_references,
        "confidence": item.confidence,
        "missing_data": item.missing_data,
        "contradicts_output_ids": [str(value) for value in item.contradicts_output_ids],
        "prompt_version": item.prompt_version,
        "model_version": item.model_version,
    }


def build_app(
    config: PlatformConfig | None = None,
    audit_store: AuditStore | None = None,
    authenticator: OperatorAuthentication | None = None,
    rate_limiter: InMemoryRateLimiter | None = None,
    metrics: MetricsRegistry | None = None,
    return_history: SQLitePortfolioReturnStore | None = None,
    investment_store: SQLiteInvestmentStore | None = None,
    fundamental_store: SQLiteFundamentalStore | None = None,
    alert_store: SQLiteOperationalAlertStore | PostgresOperationalAlertStore | None = None,
    agent_research_store: SQLiteAgentResearchStore | None = None,
    paper_oms: SQLitePaperOms | PostgresPaperOms | None = None,
    risk_decisions: SQLiteRiskDecisionStore | None = None,
    strategy_registry: SQLiteStrategyRegistry | None = None,
    experiment_store: SQLiteExperimentStore | None = None,
    promotion_ledger: SQLitePromotionLedger | None = None,
    operator_dashboard_queries: PostgresOperatorDashboardQueries | None = None,
    authorization_decision_sink: AuthorizationDecisionSink | None = None,
) -> FastAPI:
    platform_config = config or PlatformConfig()
    store = audit_store or SQLiteAuditStore()
    protected_deployment = platform_config.environment in {"paper", "production"}
    app = FastAPI(
        title="Trade Investing Panel",
        version="0.1.0",
        docs_url=None if protected_deployment else "/docs",
        redoc_url=None if protected_deployment else "/redoc",
        openapi_url=None if protected_deployment else "/openapi.json",
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        production=platform_config.environment == "production",
    )
    selected_authenticator = authenticator or OperatorAuthenticator.from_environment()
    if (
        selected_authenticator.requires_durable_authorization_audit
        and not getattr(authorization_decision_sink, "durable", False)
    ):
        raise ValueError("external_session_requires_durable_authorization_audit")
    app.state.authenticator = selected_authenticator
    app.state.authorization_decision_sink = authorization_decision_sink
    app.state.rate_limiter = rate_limiter or InMemoryRateLimiter()
    app.state.metrics = metrics or MetricsRegistry()
    app.state.event_logger = StructuredEventLogger()
    app.state.return_history = return_history
    app.state.investment_store = investment_store
    app.state.fundamental_store = fundamental_store
    app.state.alert_store = alert_store
    app.state.agent_research_store = agent_research_store
    app.state.paper_oms = paper_oms
    app.state.risk_decisions = risk_decisions
    app.state.strategy_registry = strategy_registry
    app.state.experiment_store = experiment_store
    app.state.promotion_ledger = promotion_ledger
    app.state.operator_dashboard_queries = operator_dashboard_queries

    @app.get("/health/live")
    def liveness() -> dict[str, str]:
        """Cheap process health only -- never touches the database. Must stay bounded."""
        app.state.metrics.increment("health.liveness.requests")
        return {"status": "ok"}

    @app.get("/operator-dashboard/command-center", response_model=CommandCenterResponse)
    def command_center(_: None = Depends(protected_operator)) -> CommandCenterResponse:
        """Read-only operating evidence.  Absence is preserved as unavailable evidence."""
        now = datetime.now(timezone.utc).isoformat()
        active_alerts = [] if app.state.alert_store is None else app.state.alert_store.active()
        critical_alerts = [item for item in active_alerts if item.severity.value == "CRITICAL"]
        persistence_status = (
            "POSTGRES_CONFIGURED"
            if platform_config.persistence_target is PersistenceTarget.POSTGRES
            else "SQLITE_NON_PRODUCTION"
        )
        states = [
            OperatorEvidenceState(
                id="database-authority", status=persistence_status, version="platform-config-v1",
                source="platform_config", as_of=now, freshness="CONFIGURATION_AT_REQUEST",
                limitations=["Configuration is not a database connectivity probe."],
                evidence_references=[], details={"persistence_target": platform_config.persistence_target.value},
            ),
            OperatorEvidenceState(
                id="live-trading", status="DISABLED", version="platform-config-v1",
                source="platform_config", as_of=now, freshness="CONFIGURATION_AT_REQUEST",
                limitations=["This build prohibits live execution."], evidence_references=[],
                details={"live_trading_enabled": False, "paper_trading_enabled": platform_config.paper_trading_enabled},
            ),
            OperatorEvidenceState(
                id="critical-alerts", status="AVAILABLE" if app.state.alert_store is not None else "UNAVAILABLE",
                version="operational-alerts-v1", source="operational_alert_store", as_of=now,
                freshness="QUERY_AT_REQUEST" if app.state.alert_store is not None else "NO_CONFIGURED_SOURCE",
                limitations=[] if app.state.alert_store is not None else ["Operational alert store is not configured."],
                evidence_references=[OperatorEvidenceReference(id=str(item.alert_id), kind="operational_alert") for item in critical_alerts],
                details={"active_critical_alert_count": len(critical_alerts)},
            ),
            OperatorEvidenceState(
                id="recovery-reconciliation", status="UNAVAILABLE", version="recovery-v1",
                source="not_configured", as_of=now, freshness="NO_CONFIGURED_SOURCE",
                limitations=["No recovery or reconciliation authority is wired into this API process."],
                evidence_references=[], details={},
            ),
            OperatorEvidenceState(
                id="kill-switch", status="UNAVAILABLE", version="kill-switch-v1",
                source="not_configured", as_of=now, freshness="NO_CONFIGURED_SOURCE",
                limitations=["No kill-switch authority is wired into this API process."],
                evidence_references=[], details={},
            ),
            OperatorEvidenceState(
                id="backup-restore", status="UNAVAILABLE", version="backup-restore-v1",
                source="not_configured", as_of=now, freshness="NO_CONFIGURED_SOURCE",
                limitations=["No backup or restore-drill authority is wired into this API process."],
                evidence_references=[], details={},
            ),
            OperatorEvidenceState(
                id="ci-build", status="UNAVAILABLE", version="ci-evidence-v1",
                source="not_configured", as_of=now, freshness="NO_CONFIGURED_SOURCE",
                limitations=["This runtime has no configured immutable CI evidence source."],
                evidence_references=[], details={},
            ),
        ]
        if app.state.operator_dashboard_queries is not None:
            try:
                for summary in app.state.operator_dashboard_queries.command_summaries():
                    states.append(OperatorEvidenceState(
                        id=summary.id, status=summary.status, version="postgres-authority-v1",
                        source="postgresql", as_of=now if summary.as_of is None else summary.as_of.isoformat(),
                        freshness="LATEST_PERSISTED_RECORD", limitations=[],
                        evidence_references=[] if summary.evidence_id is None else [
                            OperatorEvidenceReference(id=summary.evidence_id, kind=summary.id)
                        ], details={"detail": summary.detail},
                    ))
            except DashboardQueryError:
                states.append(OperatorEvidenceState(
                    id="postgres-authorities", status="ERROR", version="postgres-authority-v1",
                    source="postgresql", as_of=now, freshness="QUERY_FAILED",
                    limitations=["Authoritative dashboard evidence could not be read."],
                    evidence_references=[], details={},
                ))
        return CommandCenterResponse(
            id="command-center", status="AVAILABLE", version="operator-dashboard-v1",
            source="trade_platform.api", as_of=now, freshness="QUERY_AT_REQUEST",
            limitations=["Evidence reflects only authorities injected into this API process."],
            evidence_references=[], platform_mode=platform_config.environment,
            live_trading_enabled=False, states=states,
        )

    def dashboard_queries() -> PostgresOperatorDashboardQueries:
        service = app.state.operator_dashboard_queries
        if service is None:
            raise HTTPException(status_code=503, detail="PostgreSQL dashboard authority unavailable.")
        return service

    def read_dashboard(call: Callable[[], object]) -> object:
        try:
            return call()
        except DashboardObjectNotFound as error:
            raise HTTPException(status_code=404, detail="Dashboard evidence not found.") from error
        except DashboardQueryError as error:
            raise HTTPException(status_code=503, detail="Dashboard evidence unavailable.") from error

    @app.get("/operator-dashboard/workspace-references", response_model=DashboardWorkspaceReferences)
    def dashboard_workspace_references(
        _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        """Read only deterministic defaults; no arbitrary database search."""
        return read_dashboard(queries.workspace_references)

    @app.get("/operator-dashboard/instruments", response_model=InstrumentDiscoveryPage)
    def dashboard_instruments(
        query: str | None = Query(default=None, min_length=1, max_length=160),
        asset_class: str | None = Query(default=None, min_length=1, max_length=40),
        lifecycle_status: str | None = Query(default=None, min_length=1, max_length=40),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.instruments(
            query=query, asset_class=asset_class, lifecycle_status=lifecycle_status, limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/instruments/{instrument_id:path}", response_model=InstrumentDetailView)
    def dashboard_instrument(
        instrument_id: str, _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.instrument(instrument_id))

    @app.get("/operator-dashboard/historical-datasets", response_model=HistoricalDatasetPage)
    def dashboard_historical_datasets(
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.historical_datasets(limit=limit, offset=offset))

    @app.get("/operator-dashboard/data-health/assessments", response_model=DataHealthAssessmentPage)
    def dashboard_data_health_assessments(
        scope_type: str | None = Query(default=None, min_length=1, max_length=40),
        scope_value: str | None = Query(default=None, min_length=1, max_length=160),
        blocking: bool | None = Query(default=None),
        max_action: str | None = Query(default=None, min_length=1, max_length=40),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.data_health_assessments(
            scope_type=scope_type, scope_value=scope_value, blocking=blocking,
            max_action=max_action, limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/data-health/assessments/{assessment_id}", response_model=DataHealthAssessmentView)
    def dashboard_data_health_assessment(
        assessment_id: UUID, _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.data_health_assessment(assessment_id))

    @app.get("/operator-dashboard/strategies", response_model=StrategyDiscoveryPage)
    def dashboard_strategies(
        family: str | None = Query(default=None, min_length=1, max_length=80),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.strategies(family=family, limit=limit, offset=offset))

    @app.get("/operator-dashboard/experiments", response_model=ExperimentDiscoveryPage)
    def dashboard_experiments(
        strategy_id: UUID | None = None, limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10_000), _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.experiments(strategy_id=strategy_id, limit=limit, offset=offset))

    @app.get("/operator-dashboard/investment-theses", response_model=InvestmentThesisDiscoveryPage)
    def dashboard_investment_theses(
        instrument: str | None = Query(default=None, min_length=1, max_length=160),
        status: str | None = Query(default=None, min_length=1, max_length=64),
        review_state: str | None = Query(default=None, min_length=1, max_length=64),
        synthetic_demo: bool | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        """Bounded investment-thesis discovery; no ad-hoc search, no calculation."""
        return read_dashboard(lambda: queries.investment_theses(
            instrument=instrument, status=status, review_state=review_state, synthetic_demo=synthetic_demo,
            limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/investment-portfolios", response_model=InvestmentPortfolioDiscoveryPage)
    def dashboard_investment_portfolios(
        status: str | None = Query(default=None, min_length=1, max_length=64),
        account_id: str | None = Query(default=None, min_length=1, max_length=64),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        """Bounded investment-portfolio discovery; no ad-hoc search, no calculation."""
        return read_dashboard(lambda: queries.investment_portfolios(
            status=status, account_id=account_id, limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/paper-orders", response_model=PaperOrderDiscoveryPage)
    def dashboard_paper_orders(
        account_id: str | None = Query(default=None, min_length=1, max_length=120),
        instrument: str | None = Query(default=None, min_length=1, max_length=120),
        side: str | None = Query(default=None, pattern="^(BUY|SELL)$"),
        lifecycle_status: str | None = Query(default=None, min_length=1, max_length=40),
        fill_state: str | None = Query(default=None, pattern="^(UNFILLED|PARTIAL_OR_FINAL_FILL)$"),
        reconciliation_state: str | None = Query(default=None, pattern="^(HEALTHY|RECONCILIATION_REQUIRED|UNAVAILABLE)$"),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.paper_orders(
            account_id=account_id, instrument=instrument, side=side, lifecycle_status=lifecycle_status,
            fill_state=fill_state, reconciliation_state=reconciliation_state, limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/paper-orders/{intent_id}", response_model=PaperOrderView)
    def dashboard_paper_order(
        intent_id: UUID, _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.paper_order(intent_id))

    @app.get("/operator-dashboard/paper-accounts/{account_id}/reconciliation", response_model=PaperReconciliationView)
    def dashboard_paper_reconciliation(
        account_id: str, _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.paper_reconciliation(account_id))

    @app.get("/operator-dashboard/feature-definitions", response_model=FeatureDefinitionPage)
    def feature_definitions(
        family: str | None = Query(default=None, min_length=1, max_length=40),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.feature_definitions(family=family, limit=limit, offset=offset))

    @app.get("/operator-dashboard/feature-definitions/{feature_id}", response_model=FeatureDefinitionView)
    def feature_definition(
        feature_id: UUID, _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.feature_definition(feature_id))

    @app.get("/operator-dashboard/feature-materializations", response_model=FeatureMaterializationPage)
    def feature_materializations(
        feature_id: UUID, instrument: str = Query(min_length=1, max_length=160),
        dataset_version: str = Query(min_length=1, max_length=160), decision_time: datetime = Query(),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Decision time must be timezone-aware.")
        return read_dashboard(lambda: queries.feature_materializations(
            feature_id=feature_id, instrument=instrument, dataset_version=dataset_version,
            decision_time=decision_time, limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/signals", response_model=SignalPage)
    def signals(
        as_of: datetime = Query(),
        status: str | None = Query(default=None, min_length=1, max_length=32),
        instrument: str | None = Query(default=None, min_length=1, max_length=160),
        strategy_version: str | None = Query(default=None, min_length=1, max_length=160),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Signal as-of time must be timezone-aware.")
        if status is not None and status not in {item.value for item in DetailedSignalStatus}:
            raise HTTPException(status_code=422, detail="Invalid signal status.")
        return read_dashboard(lambda: queries.signals(
            as_of=as_of, status=status, instrument=instrument,
            strategy_version=strategy_version, limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/risk-decisions", response_model=RiskDecisionPage)
    def operator_risk_decisions(
        approved: bool | None = Query(default=None),
        account_id: str | None = Query(default=None, min_length=1, max_length=64),
        policy_version_id: UUID | None = None,
        business_date: date | None = Query(default=None),
        has_reservation: bool | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        """Read immutable risk-policy, decision and reservation evidence only."""
        return read_dashboard(lambda: queries.risk_decisions(
            approved=approved, account_id=account_id, policy_version_id=policy_version_id,
            business_date=business_date, has_reservation=has_reservation, limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/strategy-scorecards", response_model=StrategyScorecardDiscoveryPage)
    def dashboard_strategy_scorecards(
        strategy_id: UUID | None = None,
        status: str | None = Query(default=None, pattern="^(BLOCKED|REVIEW_REQUIRED)$"),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.strategy_scorecards(
            strategy_id=strategy_id, status=status, limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/strategy-scorecards/{scorecard_id}", response_model=StrategyScorecardView)
    def strategy_scorecard(
        scorecard_id: UUID, _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.strategy_scorecard(scorecard_id))

    @app.get("/operator-dashboard/regime-runs", response_model=RegimeRunDiscoveryPage)
    def regime_runs(
        instrument: str | None = Query(default=None, min_length=1, max_length=160),
        status: str | None = Query(default=None, pattern="^(BLOCKED|REVIEW_REQUIRED)$"),
        model_version_id: UUID | None = None,
        dataset_version: str | None = Query(default=None, min_length=1, max_length=160),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        """Bounded regime-run discovery; no ad-hoc search, no calculation."""
        return read_dashboard(lambda: queries.regime_runs(
            instrument=instrument, status=status, model_version_id=model_version_id,
            dataset_version=dataset_version, limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/regime-runs/{run_id}", response_model=RegimeRunView)
    def regime_run(
        run_id: UUID, _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.regime_run(run_id))

    @app.get("/operator-dashboard/portfolio-construction-runs", response_model=PortfolioConstructionDiscoveryPage)
    def portfolio_construction_runs(
        status: str | None = Query(default=None, pattern="^(BLOCKED|REVIEW_REQUIRED)$"),
        policy_version_id: UUID | None = None, regime_run_id: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        """Bounded portfolio-construction-run discovery; no ad-hoc search, no calculation."""
        return read_dashboard(lambda: queries.portfolio_construction_runs(
            status=status, policy_version_id=policy_version_id, regime_run_id=regime_run_id,
            limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/portfolio-construction-runs/{run_id}", response_model=PortfolioConstructionView)
    def portfolio_construction(
        run_id: UUID, _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.portfolio_construction(run_id))

    @app.get("/operator-dashboard/news-events", response_model=NewsEventPage)
    def news_events(
        instrument: str | None = Query(default=None, min_length=1, max_length=160),
        entity: str | None = Query(default=None, min_length=1, max_length=160),
        category: str | None = Query(default=None, min_length=1, max_length=80),
        start: datetime | None = Query(default=None), end: datetime | None = Query(default=None),
        correction_state: str | None = Query(default=None, pattern="^(INITIAL|CORRECTION|RETRACTION|FOLLOW_UP)$"),
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator), queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        for instant in (start, end):
            if instant is not None and (instant.tzinfo is None or instant.utcoffset() is None):
                raise HTTPException(status_code=422, detail="News timestamps must be timezone-aware.")
        if start is not None and end is not None and (end < start or end - start > timedelta(days=366)):
            raise HTTPException(status_code=422, detail="News time range must be ordered and at most 366 days.")
        return read_dashboard(lambda: queries.news_events(
            instrument=instrument, entity=entity, category=category, start=start, end=end,
            correction_state=correction_state, limit=limit, offset=offset,
        ))

    @app.get("/operator-dashboard/sre-overview", response_model=SreOverviewView)
    def sre_overview(
        service_version_id: UUID | None = None, _: None = Depends(protected_operator),
        queries: PostgresOperatorDashboardQueries = Depends(dashboard_queries),
    ) -> object:
        return read_dashboard(lambda: queries.sre_overview(service_version_id))

    @app.get("/alerts")
    def active_alerts(_: None = Depends(protected_operator)) -> list[dict[str, object]]:
        if app.state.alert_store is None: raise HTTPException(status_code=503, detail="Operational alerts unavailable.")
        return [{"alert_id": str(item.alert_id), "source": item.source, "code": item.code, "severity": item.severity.value, "resource": item.resource, "details": item.details, "status": item.status.value, "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()} for item in app.state.alert_store.active()]

    @app.get("/research-agents/workflows/{workflow_id}")
    def research_agent_workflow(workflow_id: UUID, _: None = Depends(protected_operator)) -> dict[str, object]:
        if app.state.agent_research_store is None: raise HTTPException(status_code=503, detail="Agent research evidence unavailable.")
        outputs = app.state.agent_research_store.outputs_for_workflow(workflow_id)
        return {"workflow_id": str(workflow_id), "outputs": [_research_output_response(item) for item in outputs], "reviews": [{"review_id": str(item.review_id), "synthesis_output_id": str(item.synthesis_output_id), "reviewer": item.reviewer, "approved": item.approved, "rationale": item.rationale, "reviewed_at": item.reviewed_at.isoformat()} for item in app.state.agent_research_store.reviews_for_workflow(workflow_id)], "safety_assessments": [{"assessment_id": str(item.assessment_id), "output_id": str(item.output_id), "evaluator": item.evaluator, "policy_version": item.policy_version, "approved": item.approved, "reasons": item.reasons, "assessed_at": item.assessed_at.isoformat()} for item in app.state.agent_research_store.safety_assessments_for_workflow(workflow_id)]}

    @app.get("/research-agents/workflows/{workflow_id}/operator-ready")
    def operator_ready_research_agent_workflow(workflow_id: UUID, _: None = Depends(protected_operator)) -> dict[str, object]:
        if app.state.agent_research_store is None: raise HTTPException(status_code=503, detail="Agent research evidence unavailable.")
        try:
            outputs = app.state.agent_research_store.operator_ready_outputs(workflow_id)
        except AgentResearchError as error:
            raise HTTPException(status_code=409, detail="Research workflow is not operator-ready.") from error
        return {"workflow_id": str(workflow_id), "outputs": [_research_output_response(item) for item in outputs]}

    @app.get("/research-agents/workflows/{workflow_id}/query")
    def query_research_agent_workflow(workflow_id: UUID, q: str = Query(min_length=1, max_length=240), subject: str = Depends(protected_operator)) -> dict[str, object]:
        if app.state.agent_research_store is None: raise HTTPException(status_code=503, detail="Agent research evidence unavailable.")
        try:
            query, results = app.state.agent_research_store.query_workflow(workflow_id, q, actor=subject, requested_at=datetime.now(timezone.utc))
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Unsupported research query.") from error
        return {"query_id": str(query.query_id), "intent": query.intent, "results": results}

    @app.get("/paper-oms/orders/{intent_id}")
    def paper_order_evidence(intent_id: UUID, _: None = Depends(protected_operator)) -> dict[str, object]:
        """Read immutable paper-order lifecycle evidence; never mutate an order."""
        if app.state.paper_oms is None:
            raise HTTPException(status_code=503, detail="Paper OMS evidence unavailable.")
        try:
            order = app.state.paper_oms.get(intent_id)
            events = app.state.paper_oms.events(intent_id)
            fills = app.state.paper_oms.fills(intent_id)
        except PaperOmsError as error:
            raise HTTPException(status_code=404, detail="Paper order evidence not found.") from error
        return {
            "paper_only": True,
            "intent_id": str(order.intent.intent_id),
            "signal_id": str(order.intent.signal_id),
            "instrument_id": order.intent.instrument_id,
            "account_id": order.intent.account_id,
            "side": order.intent.side.value,
            "quantity": str(order.intent.quantity),
            "limit_price": str(order.intent.limit_price),
            "created_at": order.intent.created_at.isoformat(),
            "status": order.status.value,
            "filled_quantity": str(order.filled_quantity),
            "average_fill_price": None if order.average_fill_price is None else str(order.average_fill_price),
            "events": [{"sequence": item.sequence, "event_id": str(item.event_id), "event_type": item.event_type, "occurred_at": item.occurred_at.isoformat(), "payload": item.payload} for item in events],
            "fills": [{"fill_id": str(item.fill_id), "external_fill_id": item.external_fill_id, "occurred_at": item.occurred_at.isoformat(), "quantity": str(item.quantity), "price": str(item.price)} for item in fills],
        }

    @app.get("/paper-oms/accounts/{account_id}/reconciliation")
    def paper_account_reconciliation(account_id: str, _: None = Depends(protected_operator)) -> dict[str, object]:
        """Read the latest reconciliation state; this cannot reconcile or change risk."""
        if app.state.paper_oms is None:
            raise HTTPException(status_code=503, detail="Paper OMS evidence unavailable.")
        record = app.state.paper_oms.latest_reconciliation(account_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Paper reconciliation evidence not found.")
        account = app.state.paper_oms.latest_reconciled_account(account_id, datetime.now(timezone.utc))
        return {
            "paper_only": True,
            "account_id": record.account_id,
            "source": record.source,
            "occurred_at": record.occurred_at.isoformat(),
            "complete": record.complete,
            "discrepancies": record.discrepancies,
            "reconciled_account": None if account is None else {
                "evidence_id": str(account.evidence_id),
                "healthy": account.healthy,
                "ingested_at": account.ingested_at.isoformat(),
                "as_of": account.snapshot.as_of.isoformat(),
                "cash_currency": account.snapshot.cash.currency,
                "cash_amount": str(account.snapshot.cash.amount),
                "buying_power": str(account.snapshot.buying_power),
                "positions": {instrument_id: {"quantity": str(item.quantity), "average_cost": str(item.average_cost)} for instrument_id, item in account.snapshot.positions.items()},
            },
        }

    @app.get("/risk/decisions/{intent_id}")
    def risk_decision_evidence(intent_id: UUID, _: None = Depends(protected_operator)) -> dict[str, object]:
        """Read immutable deterministic-risk outcomes; it cannot evaluate or override risk."""
        if app.state.risk_decisions is None:
            raise HTTPException(status_code=503, detail="Risk decision evidence unavailable.")
        decisions = app.state.risk_decisions.decisions_for_intent(intent_id)
        if not decisions:
            raise HTTPException(status_code=404, detail="Risk decision evidence not found.")
        return {"intent_id": str(intent_id), "decisions": [{"decision_id": str(item.decision_id), "decision": item.decision.value, "reasons": item.reasons, "decided_at": item.decided_at.isoformat()} for item in decisions]}

    @app.get("/research/strategies/{strategy_id}")
    def strategy_research_card(strategy_id: UUID, _: None = Depends(protected_operator)) -> dict[str, object]:
        if app.state.strategy_registry is None:
            raise HTTPException(status_code=503, detail="Strategy research evidence unavailable.")
        try:
            card = app.state.strategy_registry.get(strategy_id)
        except StrategyValidationError as error:
            raise HTTPException(status_code=404, detail="Strategy research card not found.") from error
        return {"strategy_id": str(card.strategy_id), "strategy_version": card.strategy_version, "family": card.family, "hypothesis": card.hypothesis, "required_datasets": card.required_datasets, "feature_versions": card.feature_versions, "universe_rules": card.universe_rules, "entry_logic": card.entry_logic, "exit_logic": card.exit_logic, "sizing_policy": card.sizing_policy, "risk_policy": card.risk_policy, "cost_model_version": card.cost_model_version, "capacity_model": card.capacity_model, "expected_regimes": card.expected_regimes, "parameter_schema": card.parameter_schema, "failure_conditions": card.failure_conditions, "limitations": card.limitations, "created_at": card.created_at.isoformat(), "evidence_classification": classify_research_evidence_from_markers(*card.required_datasets)}

    @app.post("/research/strategies", status_code=201)
    def create_strategy_research_card(request: StrategyCreateRequest, subject: str = Depends(research_operator)) -> dict[str, object]:
        if app.state.strategy_registry is None: raise HTTPException(status_code=503, detail="Strategy research evidence unavailable.")
        try:
            card = StrategyRunCard.create(strategy_version=request.strategy_version, family=request.family, hypothesis=request.hypothesis, required_datasets=tuple(request.required_datasets), feature_versions=tuple(request.feature_versions), universe_rules=request.universe_rules, entry_logic=request.entry_logic, exit_logic=request.exit_logic, sizing_policy=request.sizing_policy, risk_policy=request.risk_policy, cost_model_version=request.cost_model_version, capacity_model=request.capacity_model, expected_regimes=tuple(request.expected_regimes), parameter_schema=request.parameter_schema, failure_conditions=tuple(request.failure_conditions), limitations=tuple(request.limitations)); card = app.state.strategy_registry.create(card, idempotency_key=request.idempotency_key, actor=subject)
        except StrategyValidationError as error: raise HTTPException(status_code=422, detail="Invalid strategy research card.") from error
        return {"strategy_id": str(card.strategy_id), "strategy_version": card.strategy_version, "family": card.family, "idempotency_key": request.idempotency_key}

    @app.get("/research/experiments/{experiment_id}")
    def research_experiment(experiment_id: UUID, _: None = Depends(protected_operator)) -> dict[str, object]:
        if app.state.experiment_store is None:
            raise HTTPException(status_code=503, detail="Backtest research evidence unavailable.")
        try:
            experiment = app.state.experiment_store.get(experiment_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Backtest research experiment not found.") from error
        return {"experiment_id": str(experiment.experiment_id), "strategy_version": experiment.strategy_version, "dataset_version": experiment.dataset_version, "feature_versions": experiment.feature_versions, "cost_model_version": experiment.cost_model_version, "parameters": experiment.parameters, "created_at": experiment.created_at.isoformat(), "report": experiment.report, "evidence_classification": classify_research_evidence_from_markers(experiment.dataset_version)}

    @app.get("/research/promotions/{decision_id}")
    def research_promotion(decision_id: UUID, _: None = Depends(protected_operator)) -> dict[str, object]:
        if app.state.promotion_ledger is None:
            raise HTTPException(status_code=503, detail="Strategy promotion evidence unavailable.")
        try:
            decision = app.state.promotion_ledger.get(decision_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Strategy promotion evidence not found.") from error
        return {"decision_id": str(decision.decision_id), "strategy_id": str(decision.strategy_id), "strategy_version": decision.strategy_version, "status": decision.status.value, "reasons": decision.reasons, "held_out_periods": decision.held_out_periods, "held_out_total_return": None if decision.held_out_total_return is None else str(decision.held_out_total_return), "decided_at": decision.decided_at.isoformat()}

    @app.post("/research/backtests", status_code=201)
    def launch_research_backtest(request: BacktestLaunchRequest, subject: str = Depends(research_operator)) -> dict[str, object]:
        if app.state.strategy_registry is None or app.state.experiment_store is None:
            raise HTTPException(status_code=503, detail="Research launch evidence unavailable.")
        protocol_fields = (request.walk_forward_train_size, request.walk_forward_validation_size, request.walk_forward_test_size, request.walk_forward_step)
        if any(value is not None for value in protocol_fields) and any(value is None for value in protocol_fields):
            raise HTTPException(status_code=422, detail="Complete walk-forward protocol is required.")
        protocol = None if all(value is None for value in protocol_fields) else WalkForwardProtocol(
            request.walk_forward_train_size, request.walk_forward_validation_size, request.walk_forward_test_size,
            request.walk_forward_step, request.walk_forward_purge, request.walk_forward_embargo,
        )
        try:
            card = app.state.strategy_registry.get(request.strategy_id)
            from decimal import Decimal
            costs = CostModel(Decimal(request.fixed_per_turnover), Decimal(request.percentage_per_turnover), Decimal(request.spread_fraction_per_turnover))
            pessimistic_cost_multiplier = Decimal(request.pessimistic_cost_multiplier)
            if any(not value.is_finite() or value < 0 for value in (costs.fixed_per_turnover, costs.percentage_per_turnover, costs.spread_fraction_per_turnover)) or not pessimistic_cost_multiplier.is_finite() or pessimistic_cost_multiplier < 1:
                raise ResearchValidationError("invalid_backtest_costs")
            experiment = app.state.experiment_store.launch(strategy_version=card.strategy_version, family=card.family, dataset_version=request.dataset_version, feature_versions=card.feature_versions, cost_model_version=card.cost_model_version, parameters=request.parameters, closes=tuple(Decimal(item) for item in request.closes), costs=costs, idempotency_key=request.idempotency_key, actor=subject, pessimistic_cost_multiplier=pessimistic_cost_multiplier, walk_forward_protocol=protocol)
        except (StrategyValidationError, ResearchValidationError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Invalid research backtest launch.") from error
        return {"experiment_id": str(experiment.experiment_id), "strategy_id": str(card.strategy_id), "strategy_version": experiment.strategy_version, "report": experiment.report, "idempotency_key": request.idempotency_key}

    @app.post("/alerts/{alert_id}/acknowledge")
    def acknowledge_alert(alert_id: UUID, subject: str = Depends(alert_reviewer_operator)) -> dict[str, object]:
        if app.state.alert_store is None: raise HTTPException(status_code=503, detail="Operational alerts unavailable.")
        try:
            alert = app.state.alert_store.transition(alert_id, AlertStatus.ACKNOWLEDGED, actor=subject)
        except (KeyError, AlertError) as error:
            raise HTTPException(status_code=422, detail="Invalid alert acknowledgement.") from error
        return {"alert_id": str(alert.alert_id), "status": alert.status.value, "updated_at": alert.updated_at.isoformat()}

    @app.get("/health/ready")
    def readiness() -> dict[str, object]:
        """Bounded readiness probe -- never the expensive check liveness must avoid.

        Local/research mode is always ready (no required durable authority). A
        protected deployment (paper/production) is ready only once every authority
        required for its API surface is present *and* a cheap, already-bounded
        PostgreSQL read succeeds. It never blocks indefinitely (the underlying
        connection has a bounded connect timeout) and never claims readiness for an
        authority that is missing or unreachable.
        """
        app.state.metrics.increment("health.readiness.requests")
        if protected_deployment:
            if app.state.paper_oms is None or app.state.operator_dashboard_queries is None:
                raise HTTPException(
                    status_code=503, detail="required_postgres_authority_missing"
                )
            try:
                app.state.operator_dashboard_queries.workspace_references()
            except DashboardQueryError as error:
                raise HTTPException(
                    status_code=503, detail="postgres_authority_unreachable"
                ) from error
        return {
            "status": "ready",
            "environment": platform_config.environment,
            "paper_trading_enabled": platform_config.paper_trading_enabled,
            "live_trading_enabled": False,
        }

    @app.post("/audit/events", response_model=AuditEventResponse, status_code=201)
    def create_audit_event(
        request: AuditEventRequest, subject: str = Depends(audit_writer_operator)
    ) -> AuditEventResponse:
        try:
            event = store.append(request.event_type, subject, request.payload)
            app.state.metrics.increment("audit.events.created")
            app.state.event_logger.emit("audit.event.created", event_id=event.event_id, actor=event.actor)
            return _serialize(event)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/audit/events", response_model=list[AuditEventResponse])
    def list_audit_events(
        limit: int = Query(default=100, ge=1, le=1000), _: None = Depends(protected_operator)
    ) -> list[AuditEventResponse]:
        app.state.metrics.increment("audit.events.listed")
        return [_serialize(event) for event in store.recent(limit)]

    @app.get("/operator-dashboard/audit-events", response_model=AuditEventDiscoveryPage)
    def dashboard_audit_events(
        event_type: str | None = Query(default=None, min_length=1, max_length=120),
        actor: str | None = Query(default=None, min_length=1, max_length=120),
        start: datetime | None = Query(default=None), end: datetime | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0, le=10_000),
        _: None = Depends(protected_operator),
    ) -> AuditEventDiscoveryPage:
        """Bounded, deterministically-ordered read of the append-only audit event store.

        Distinct from ``/alerts``: alerts are operational signal-detection records, audit
        events are actor/action/decision evidence. Neither substitutes for the other.
        """
        for instant in (start, end):
            if instant is not None and (instant.tzinfo is None or instant.utcoffset() is None):
                raise HTTPException(status_code=422, detail="Audit event timestamps must be timezone-aware.")
        if start is not None and end is not None and end < start:
            raise HTTPException(status_code=422, detail="Audit event time range must be ordered.")
        try:
            items, has_more = store.query(
                event_type=event_type, actor=actor, start=start, end=end, limit=limit, offset=offset,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        app.state.metrics.increment("audit.events.listed")
        return AuditEventDiscoveryPage(
            state="AVAILABLE" if items else "UNAVAILABLE",
            items=[_audit_projection(event) for event in items],
            page=PageInfo(limit=limit, offset=offset, returned=len(items), has_more=has_more),
        )

    @app.get("/operator-dashboard/audit-events/{event_id}", response_model=AuditEventProjection)
    def dashboard_audit_event(event_id: UUID, _: None = Depends(protected_operator)) -> AuditEventProjection:
        event = store.get(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Audit event not found.")
        app.state.metrics.increment("audit.events.listed")
        return _audit_projection(event)

    @app.get("/observability/metrics")
    def metrics_snapshot(_: None = Depends(protected_operator)) -> dict[str, int]:
        return app.state.metrics.snapshot()

    @app.get("/investments/theses/{thesis_id}")
    def investment_thesis_history(
        thesis_id: UUID, as_of: datetime | None = None, _: None = Depends(protected_operator)
    ) -> dict[str, object]:
        if app.state.investment_store is None:
            raise HTTPException(status_code=503, detail="Investment evidence unavailable.")
        instant = datetime.now(timezone.utc) if as_of is None else as_of
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Investment as-of timestamp must be timezone-aware.")
        try:
            thesis = app.state.investment_store.get_thesis(thesis_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Unknown investment thesis.") from error
        try:
            facts = app.state.investment_store.facts_as_of(thesis.instrument_id, instant)
        except InvestmentValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        app.state.metrics.increment("investment.thesis_history.viewed")
        schedule = app.state.investment_store.review_schedule_for_thesis(thesis_id)
        return {
            "as_of": instant.isoformat(),
            "thesis": {"thesis_id": str(thesis.thesis_id), "instrument_id": thesis.instrument_id, "version": thesis.version,
                "statement": thesis.statement, "horizon_days": thesis.horizon_days, "catalysts": thesis.catalysts,
                "risks": thesis.risks, "evidence_ids": thesis.evidence_ids, "status": thesis.status, "created_at": thesis.created_at.isoformat()},
            "facts": [{"fact_id": str(fact.fact_id), "metric": fact.metric, "value": str(fact.value), "unit": fact.unit,
                "observed_at": fact.observed_at.isoformat(), "available_at": fact.available_at.isoformat(),
                "ingested_at": fact.ingested_at.isoformat(), "source_reference": fact.source_reference,
                "source_url": fact.source_url, "revision": fact.revision} for fact in facts],
            "analytics": [{"analysis_id": str(value.analysis_id), "as_of": value.as_of.isoformat(), "unit": value.unit,
                "revenue_growth": str(value.revenue_growth), "net_margin": str(value.net_margin),
                "free_cash_flow_margin": str(value.free_cash_flow_margin), "debt_to_equity": str(value.debt_to_equity),
                "net_debt": str(value.net_debt), "source_fact_ids": [str(fact_id) for fact_id in value.source_fact_ids]}
                for value in app.state.investment_store.fundamental_analytics_for_thesis(thesis_id, instant)],
            "valuations": [{"valuation_id": str(value.valuation_id), "as_of": value.as_of.isoformat(), "currency": value.currency,
                "intrinsic_value_per_share": str(value.intrinsic_value_per_share()), "model_version": value.model_version,
                "source_fact_ids": [str(fact_id) for fact_id in value.source_fact_ids]} for value in app.state.investment_store.valuations_for_thesis(thesis_id)],
            "recommendations": [{"recommendation_id": str(value.recommendation_id), "target_weight": str(value.target_weight),
                "maximum_weight": str(value.maximum_weight), "confidence": str(value.confidence), "as_of": value.as_of.isoformat(),
                "rationale_evidence_ids": value.rationale_evidence_ids} for value in app.state.investment_store.recommendations_for_thesis(thesis_id)],
            "reviews": [{"review_id": str(value.review_id), "outcome": value.outcome, "reviewed_at": value.reviewed_at.isoformat(),
                "rationale": value.rationale, "evidence_ids": value.evidence_ids, "next_review_at": value.next_review_at.isoformat(),
                "drift": {"as_of": value.drift_assessment.as_of.isoformat(), "checked_fact_ids": [str(fact_id) for fact_id in value.drift_assessment.checked_fact_ids],
                    "breached_metrics": value.drift_assessment.breached_metrics, "detected": value.drift_assessment.drift_detected}} for value in app.state.investment_store.reviews_for_thesis(thesis_id)],
            "company_research": [{"record_id": row[0], "as_of": row[3], "bull_case": row[4], "base_case": row[5], "bear_case": row[6],
                "catalysts": json.loads(row[7]), "invalidation_conditions": json.loads(row[8]), "position_sizing_rationale": row[9],
                "replacement_candidates": json.loads(row[10]), "evidence_ids": json.loads(row[11])}
                for row in app.state.investment_store.company_research_for_thesis(thesis_id, instant)],
            "theme_discoveries": [{"discovery_id": str(value.discovery_id), "theme": value.theme, "score": str(value.score), "company_research_id": str(value.company_research_id),
                "matched_phrases": value.matched_phrases, "evidence_ids": value.evidence_ids, "rule_version": value.rule_version, "discovered_at": value.discovered_at.isoformat()}
                for value in app.state.investment_store.theme_discoveries_for_thesis(thesis_id, instant)],
            "review_schedule": None if schedule is None else {"cadence_days": schedule.cadence_days, "next_review_at": schedule.next_review_at.isoformat(), "approved_by": schedule.approved_by,
                "expectations": [{"metric": value.metric, "minimum": None if value.minimum is None else str(value.minimum), "maximum": None if value.maximum is None else str(value.maximum)} for value in schedule.expectations]},
            "scheduled_drift_history": [{"assessment_id": row[0], "assessed_at": row[2], "checked_fact_ids": json.loads(row[3]), "breached_metrics": json.loads(row[4]), "scheduled_for": row[5], "detected": bool(json.loads(row[4]))}
                for row in app.state.investment_store.scheduled_drift_history_for_thesis(thesis_id, instant)],
        }

    @app.get("/investments/portfolios/{portfolio_id}")
    def investment_portfolio_history(portfolio_id: str, as_of: datetime | None = None, _: None = Depends(protected_operator)) -> dict[str, object]:
        if app.state.investment_store is None: raise HTTPException(status_code=503, detail="Investment evidence unavailable.")
        instant = datetime.now(timezone.utc) if as_of is None else as_of
        try:
            policy, themes, macro = app.state.investment_store.investment_portfolio_evidence(portfolio_id, instant)
            assessment = app.state.investment_store.assess_investment_portfolio(portfolio_id, instant)
            holdings, decisions, performance = app.state.investment_store.investment_portfolio_history(portfolio_id, instant)
        except InvestmentValidationError as error: raise HTTPException(status_code=404, detail=str(error)) from error
        return {"portfolio_id": portfolio_id, "as_of": instant.isoformat(), "policy": {"base_currency": policy[1], "maximum_investment_value": policy[2], "maximum_single_weight": policy[3]}, "assessment": {"total_value": str(assessment.total_value), "approved": assessment.approved, "reasons": assessment.reasons}, "holdings": [{"instrument_id": x[0], "market_value": x[1], "observed_at": x[2], "source_reference": x[3]} for x in holdings], "rebalance_decisions": [{"decision_id": x[0], "rationale": x[3], "approved_by": x[6]} for x in decisions], "performance": [{"observed_at": x[1], "net_asset_value": x[2], "cumulative_return": x[3], "source_reference": x[4]} for x in performance], "themes": [{"instrument_id": x[0], "theme": x[1], "exposure": x[2], "source_reference": x[4]} for x in themes], "macro_sensitivities": [{"instrument_id": x[0], "series_id": x[1], "sensitivity": x[2], "source_reference": x[4]} for x in macro]}

    @app.post("/investments/fundamental-materializations", status_code=201)
    def materialize_investment_fundamentals(request: FundamentalMaterializationRequest, subject: str = Depends(data_steward_operator)) -> dict[str, object]:
        if app.state.investment_store is None or app.state.fundamental_store is None:
            raise HTTPException(status_code=503, detail="Investment fundamental evidence unavailable.")
        try:
            facts = materialize_fundamental_facts_authorized(app.state.investment_store, app.state.fundamental_store,
                request.instrument_id, request.as_of, fact_names=tuple(request.fact_names), actor=subject,
                idempotency_key=request.idempotency_key)
        except (InvestmentValidationError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception as error:
            app.state.metrics.increment("investment.fundamental_materialization.failed")
            raise HTTPException(status_code=502, detail="Fundamental evidence source unavailable.") from error
        app.state.metrics.increment("investment.fundamental_materialization.succeeded")
        return {"idempotency_key": request.idempotency_key, "actor": subject, "fact_ids": [str(fact.fact_id) for fact in facts], "observation_count": len(facts)}

    @app.get("/investments/fundamental-materializations/{instrument_id}")
    def investment_materialization_history(instrument_id: str, _: None = Depends(protected_operator)) -> list[dict[str, object]]:
        if app.state.investment_store is None:
            raise HTTPException(status_code=503, detail="Investment evidence unavailable.")
        return [{"idempotency_key": key, "actor": actor, "as_of": as_of, "status": status,
                 "fact_ids": [str(fact_id) for fact_id in fact_ids], "error": error, "requested_at": requested.isoformat()}
                for key, actor, as_of, status, fact_ids, error, requested in app.state.investment_store.materialization_commands(instrument_id)]

    @app.get("/data-health/return-providers/{provider}")
    def return_provider_health(provider: str, _: None = Depends(protected_operator)) -> dict[str, object]:
        if app.state.return_history is None:
            raise HTTPException(status_code=503, detail="Return-history evidence unavailable.")
        health = app.state.return_history.provider_health(provider)
        if health is None:
            raise HTTPException(status_code=404, detail="Unknown return provider.")
        checked_at, healthy, reason, failures = health
        app.state.metrics.increment("return_history.provider_health.viewed")
        return {"provider": provider, "checked_at": checked_at.isoformat(), "healthy": healthy, "reason": reason, "consecutive_failures": failures}

    @app.get("/data-health/return-ingestion/accounts/{account_id}")
    def return_ingestion_evidence(account_id: str, _: None = Depends(protected_operator)) -> dict[str, object]:
        if app.state.return_history is None:
            raise HTTPException(status_code=503, detail="Return-history evidence unavailable.")
        app.state.metrics.increment("return_history.ingestion_evidence.viewed")
        return {
            "runs": [{"run_id": str(run_id), "provider": provider, "start_at": start.isoformat(), "end_at": end.isoformat(), "status": status, "observation_count": count, "error": error} for run_id, provider, start, end, status, count, error in app.state.return_history.ingestion_runs(account_id)],
            "commands": [{"idempotency_key": key, "actor": actor, "provider": provider, "start_at": start.isoformat(), "end_at": end.isoformat(), "status": status, "observation_count": count, "error": error, "requested_at": requested.isoformat()} for key, actor, provider, start, end, status, count, error, requested in app.state.return_history.ingestion_commands(account_id)],
        }

    @app.post("/data-health/return-ingestion/cadence", status_code=201)
    def set_return_ingestion_cadence(request: ReturnIngestionCadenceRequest, subject: str = Depends(data_steward_operator)) -> dict[str, object]:
        if app.state.return_history is None:
            raise HTTPException(status_code=503, detail="Return-history evidence unavailable.")
        cadence = ReturnIngestionCadence(request.account_id, request.provider, timedelta(seconds=request.interval_seconds), timedelta(seconds=request.grace_seconds), subject, datetime.now(timezone.utc))
        try:
            app.state.return_history.set_ingestion_cadence(cadence)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        app.state.metrics.increment("return_history.cadence.updated")
        return {"account_id": cadence.account_id, "provider": cadence.provider, "interval_seconds": request.interval_seconds, "grace_seconds": request.grace_seconds, "approved_by": cadence.approved_by, "updated_at": cadence.updated_at.isoformat()}

    @app.get("/data-health/return-ingestion/due")
    def due_return_ingestion(as_of: str | None = None, _: None = Depends(protected_operator)) -> list[dict[str, object]]:
        if app.state.return_history is None:
            raise HTTPException(status_code=503, detail="Return-history evidence unavailable.")
        try:
            now = datetime.now(timezone.utc) if as_of is None else datetime.fromisoformat(as_of)
            due = app.state.return_history.due_ingestion_cadences(now)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        app.state.metrics.increment("return_history.cadence_due.viewed")
        return [{"account_id": cadence.account_id, "provider": cadence.provider, "interval_seconds": int(cadence.interval.total_seconds()), "grace_seconds": int(cadence.grace.total_seconds()), "approved_by": cadence.approved_by, "last_successful_at": None if completed is None else completed.isoformat(), "overdue": overdue} for cadence, completed, overdue in due]

    @app.get("/data-health/return-ingestion/cadences")
    def return_ingestion_cadences(_: None = Depends(protected_operator)) -> list[dict[str, object]]:
        if app.state.return_history is None:
            raise HTTPException(status_code=503, detail="Return-history evidence unavailable.")
        now = datetime.now(timezone.utc)
        due = {(item.account_id, item.provider): (completed, overdue) for item, completed, overdue in app.state.return_history.due_ingestion_cadences(now)}
        return [{"account_id": item.account_id, "provider": item.provider, "interval_seconds": int(item.interval.total_seconds()), "grace_seconds": int(item.grace.total_seconds()), "approved_by": item.approved_by, "updated_at": item.updated_at.isoformat(), "due": (state := due.get((item.account_id, item.provider))) is not None, "overdue": False if state is None else state[1], "last_successful_at": None if state is None or state[0] is None else state[0].isoformat()} for item in app.state.return_history.ingestion_cadences()]

    @app.post("/risk/portfolio")
    def portfolio_risk_report(request: PortfolioRiskRequest, _: None = Depends(risk_reviewer_operator)) -> dict[str, object]:
        from decimal import Decimal
        exposures = [PortfolioExposure(item.instrument_id, Decimal(str(item.notional)), item.asset_class, item.sector) for item in request.exposures]
        scenario = StressScenario("operator_scenario", {key: Decimal(str(value)) for key, value in request.shocks.items()})
        policy = PortfolioRiskPolicy(Decimal(str(request.maximum_gross_notional)), Decimal(str(request.maximum_single_weight)), Decimal(str(request.maximum_scenario_loss)))
        decision = evaluate_portfolio(exposures, Decimal(str(request.equity)), scenario, policy)
        app.state.metrics.increment("risk.portfolio.evaluated")
        return {"approved": decision.approved, "reasons": decision.reasons, "gross": str(decision.gross), "net": str(decision.net), "stress_loss": str(decision.stress_loss)}

    return app


app = build_app()
