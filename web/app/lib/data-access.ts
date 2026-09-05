import { loadDashboardConfig, resolveDashboardOperatorToken, type DashboardConfig } from "../dashboard-config";
import { resolveDashboardWorkspaceReferences, type DiscoveredWorkspaceReferences, type ResolvedDashboardWorkspace } from "../dashboard-workspace";
import {
  type CommandCenterEvidence,
  type DataHealthAssessment,
  type DataHealthAssessmentPage,
  type EvidenceResult,
  type FeatureDefinition,
  type FeatureDefinitionPage,
  type FeatureMaterializationPage,
  type HistoricalDatasetPage,
  type InstrumentDetail,
  type NewsEventPage,
  type PortfolioConstruction,
  type PortfolioConstructionDiscoveryPage,
  type RegimeRun,
  type RegimeRunDiscoveryPage,
  type RiskDecisionPage,
  type SignalPage,
  type SreOverview,
  type StrategyScorecard,
  type StrategyScorecardDiscoveryPage,
  readEvidence,
} from "../operator-contracts";

export type DataHealth = {
  provider: string;
  healthy: boolean;
  checked_at: string;
  consecutive_failures: number;
  reason: string | null;
};

export type Cadence = {
  account_id: string;
  provider: string;
  due: boolean;
  overdue: boolean;
  last_successful_at: string | null;
  approved_by: string;
};

export type StrategyCard = {
  strategy_id: string;
  strategy_version: string;
  family: string;
  hypothesis: string;
  required_datasets: string[];
  feature_versions: string[];
  universe_rules: string;
  entry_logic: string;
  exit_logic: string;
  sizing_policy: string;
  risk_policy: string;
  cost_model_version: string;
  capacity_model: string;
  parameter_schema: Record<string, unknown>;
  limitations: string[];
  expected_regimes: string[];
  failure_conditions: string[];
  created_at: string;
  evidence_classification: string;
};

export type Experiment = {
  experiment_id: string;
  strategy_version: string;
  dataset_version: string;
  feature_versions: string[];
  cost_model_version: string;
  parameters: Record<string, unknown>;
  created_at: string;
  report: Record<string, string | number | null>;
  evidence_classification: string;
};

export type Promotion = {
  status: string;
  reasons: string[];
  held_out_periods: number;
  held_out_total_return: string | null;
  decided_at: string;
};

export type Thesis = {
  as_of: string;
  thesis: {
    thesis_id: string;
    instrument_id: string;
    version: string;
    statement: string;
    horizon_days: number;
    catalysts: string[];
    risks: string[];
    evidence_ids: string[];
    status: string;
    created_at: string;
  };
  facts: {
    fact_id: string; metric: string; value: string; unit: string;
    observed_at: string; available_at: string; ingested_at: string;
    source_reference: string; source_url: string; revision: number;
  }[];
  analytics: {
    analysis_id: string; as_of: string; unit: string;
    revenue_growth: string; net_margin: string; free_cash_flow_margin: string;
    debt_to_equity: string; net_debt: string; source_fact_ids: string[];
  }[];
  valuations: {
    valuation_id: string;
    model_version: string;
    intrinsic_value_per_share: string;
    as_of: string;
    currency: string;
    source_fact_ids: string[];
  }[];
  recommendations: {
    recommendation_id: string; target_weight: string; maximum_weight: string; confidence: string;
    as_of: string; rationale_evidence_ids: string[];
  }[];
  reviews: {
    review_id: string; outcome: string; reviewed_at: string; rationale: string;
    evidence_ids: string[]; next_review_at: string;
    drift: { as_of: string; checked_fact_ids: string[]; breached_metrics: string[]; detected: boolean };
  }[];
  company_research: {
    record_id: string;
    as_of: string;
    bull_case: string;
    base_case: string;
    bear_case: string;
    catalysts: string[];
    invalidation_conditions: string[];
    position_sizing_rationale: string;
    replacement_candidates: string[];
    evidence_ids: string[];
  }[];
  theme_discoveries: {
    discovery_id: string; theme: string; score: string; company_research_id: string;
    matched_phrases: string[]; evidence_ids: string[]; rule_version: string; discovered_at: string;
  }[];
  review_schedule: {
    cadence_days: number; next_review_at: string; approved_by: string;
    expectations: { metric: string; minimum: string | null; maximum: string | null }[];
  } | null;
  scheduled_drift_history: {
    assessment_id: string; assessed_at: string; checked_fact_ids: string[]; breached_metrics: string[];
    scheduled_for: string; detected: boolean;
  }[];
};

export type InvestmentPortfolio = {
  portfolio_id: string;
  as_of: string;
  policy: { base_currency: string; maximum_investment_value: string; maximum_single_weight: string };
  assessment: { total_value: string; approved: boolean; reasons: string[] };
  holdings: { instrument_id: string; market_value: string; observed_at: string; source_reference: string }[];
  rebalance_decisions: { decision_id: string; rationale: string; approved_by: string }[];
  performance: { observed_at: string; net_asset_value: string; cumulative_return: string; source_reference: string }[];
  themes: { instrument_id: string; theme: string; exposure: string; source_reference: string }[];
  macro_sensitivities: { instrument_id: string; series_id: string; sensitivity: string; source_reference: string }[];
};

export type OperationalAlert = {
  alert_id: string;
  code: string;
  severity: string;
  resource: string;
  status: string;
  created_at: string;
};

export type PaperOrder = {
  paper_only: true;
  intent_id: string;
  instrument_id: string;
  account_id: string;
  side: string;
  quantity: string;
  status: string;
  filled_quantity: string;
  average_fill_price: string | null;
  created_at: string;
  events: { event_id: string; event_type: string; occurred_at: string }[];
  fills: { fill_id: string; external_fill_id: string; quantity: string; price: string; occurred_at: string }[];
};

export type Reconciliation = {
  paper_only: true;
  source: string;
  occurred_at: string;
  complete: boolean;
  discrepancies: string[];
  reconciled_account: { evidence_id: string; healthy: boolean; as_of: string; buying_power: string } | null;
};

export type DiscoveryPage<T> = {
  state: "AVAILABLE" | "UNAVAILABLE";
  items: T[];
  page: { limit: number; offset: number; returned: number; has_more: boolean };
};

export type InstrumentDiscovery = {
  instrument_id: string;
  canonical_symbol: string;
  asset_class: string;
  venue: string;
  lifecycle_status: string;
  valid_from: string;
  valid_until: string | null;
  synthetic_demo: boolean;
  latest_dataset_version: string | null;
  identifier_mapping_count: number;
  ambiguous_mapping: boolean;
};

export type StrategyDiscovery = {
  strategy_id: string;
  strategy_version_id: string;
  version: string;
  family: string;
  hypothesis: string;
  status: string;
  dataset_requirements: string[];
  feature_versions: string[];
  cost_model_version: string;
  created_at: string;
  evidence_classification: string;
};

export type ExperimentDiscovery = {
  experiment_id: string;
  strategy_id: string;
  strategy_version_id: string;
  strategy_version: string;
  dataset_version: string;
  feature_versions: string[];
  cost_model_version: string;
  created_at: string;
  evaluated_at: string | null;
  status: string;
  evidence_classification: string;
};

export type InvestmentThesisDiscovery = {
  thesis_id: string;
  instrument_id: string;
  canonical_symbol: string | null;
  thesis_version: string;
  status: string;
  as_of: string;
  review_state: string | null;
  synthetic_demo: boolean;
  evidence_classification: string;
};

export type InvestmentPortfolioDiscovery = {
  portfolio_id: string;
  as_of: string;
  review_status: string;
  holdings_count: number;
  evidence_classification: string;
};

export type PaperOrderDiscovery = {
  account_id: string;
  intent_id: string;
  instrument_id: string;
  canonical_symbol: string | null;
  side: string;
  quantity: string;
  paper_only: true;
  lifecycle_status: string;
  created_at: string;
  fill_state: string;
  reconciliation_state: string;
};

export type AuditEvent = {
  event_id: string;
  event_type: string;
  actor: string;
  occurred_at: string;
  payload: Record<string, unknown>;
};

export type AuditEventDiscovery = {
  state: "AVAILABLE" | "UNAVAILABLE";
  items: AuditEvent[];
  page: { limit: number; offset: number; returned: number; has_more: boolean };
  audit_authority: string;
  immutability_guarantee: string;
  mutation_route_exposed_by_dashboard: boolean;
};

export function utc(value: string | null | undefined): string {
  if (!value) return "UNAVAILABLE";
  const instant = new Date(value);
  return Number.isNaN(instant.valueOf()) ? "UNAVAILABLE" : instant.toISOString().replace(".000Z", "Z");
}

export function stateText<T>(result: EvidenceResult<T>): string {
  return result.state === "AVAILABLE" ? "AVAILABLE" : `${result.state}: ${result.detail}`;
}

export function authorityUrl(origin: string, target: string): string {
  return `${origin}/api/authorities?target=${encodeURIComponent(target)}`;
}

export type WorkspaceContext = {
  config: DashboardConfig;
  origin: string;
  protectedApi: boolean;
  workspace: ResolvedDashboardWorkspace;
  evidenceTime: string;
};

export async function getWorkspaceContext(): Promise<WorkspaceContext> {
  const config = loadDashboardConfig();
  const origin = config.dashboardOrigin;
  const protectedApi = Boolean(config.apiBaseUrl && resolveDashboardOperatorToken(config));
  const discovery = await readEvidence<DiscoveredWorkspaceReferences>(
    authorityUrl(origin, "/operator-dashboard/workspace-references"),
    protectedApi,
    "Operator discovery credentials are not configured.",
  );
  const workspace = resolveDashboardWorkspaceReferences(
    config,
    discovery.state === "AVAILABLE" ? discovery.value : undefined,
  );
  const evidenceTime = new Date().toISOString();
  return { config, origin, protectedApi, workspace, evidenceTime };
}

export async function getCommandCenterEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<CommandCenterEvidence>> {
  return readEvidence<CommandCenterEvidence>(
    `${ctx.origin}/api/operator`,
    ctx.protectedApi,
    "Operator API credentials are not configured.",
  );
}

export async function getDataHealthEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<DataHealth>> {
  return readEvidence<DataHealth>(
    `${ctx.origin}/api/data-health?target=/data-health/return-providers/${encodeURIComponent(ctx.config.returnProvider ?? "")}`,
    Boolean(ctx.config.returnProvider && ctx.protectedApi),
    "Return provider is not selected or authorized.",
  );
}

export async function getCadenceEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<Cadence[]>> {
  return readEvidence<Cadence[]>(
    `${ctx.origin}/api/data-health?target=/data-health/return-ingestion/cadences`,
    Boolean(ctx.config.returnAccount && ctx.config.returnProvider && ctx.protectedApi),
    "Return account/provider evidence is not configured.",
  );
}

export async function getInstrumentDiscovery(
  ctx: WorkspaceContext,
  params?: { query?: string; asset_class?: string; lifecycle_status?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<DiscoveryPage<InstrumentDiscovery>>> {
  const query = new URLSearchParams({
    limit: String(params?.limit ?? 20),
    offset: String(params?.offset ?? 0),
  });
  if (params?.query) query.set("query", params.query);
  if (params?.asset_class && params.asset_class !== "ALL") query.set("asset_class", params.asset_class);
  if (params?.lifecycle_status && params.lifecycle_status !== "ALL") query.set("lifecycle_status", params.lifecycle_status);
  return readEvidence<DiscoveryPage<InstrumentDiscovery>>(
    authorityUrl(ctx.origin, `/operator-dashboard/instruments?${query.toString()}`),
    ctx.protectedApi,
    "Instrument discovery is unavailable.",
  );
}

export async function getInstrumentDetail(
  ctx: WorkspaceContext,
  instrumentId: string,
): Promise<EvidenceResult<InstrumentDetail>> {
  return readEvidence<InstrumentDetail>(
    authorityUrl(ctx.origin, `/operator-dashboard/instruments/${encodeURIComponent(instrumentId)}`),
    Boolean(instrumentId && ctx.protectedApi),
    "Instrument detail reference is unavailable.",
  );
}

export async function getHistoricalDatasets(
  ctx: WorkspaceContext,
  params?: { limit?: number; offset?: number },
): Promise<EvidenceResult<HistoricalDatasetPage>> {
  const query = new URLSearchParams({
    limit: String(params?.limit ?? 50),
    offset: String(params?.offset ?? 0),
  });
  return readEvidence<HistoricalDatasetPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/historical-datasets?${query.toString()}`),
    ctx.protectedApi,
    "Historical dataset discovery is unavailable.",
  );
}

export async function getDataHealthAssessments(
  ctx: WorkspaceContext,
  params?: { scope_type?: string; scope_value?: string; blocking?: boolean; max_action?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<DataHealthAssessmentPage>> {
  const query = new URLSearchParams({
    limit: String(params?.limit ?? 50),
    offset: String(params?.offset ?? 0),
  });
  if (params?.scope_type && params.scope_type !== "ALL") query.set("scope_type", params.scope_type);
  if (params?.scope_value) query.set("scope_value", params.scope_value);
  if (params?.blocking !== undefined) query.set("blocking", String(params.blocking));
  if (params?.max_action && params.max_action !== "ALL") query.set("max_action", params.max_action);

  return readEvidence<DataHealthAssessmentPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/data-health/assessments?${query.toString()}`),
    ctx.protectedApi,
    "Data health assessments are unavailable.",
  );
}

export async function getDataHealthAssessmentDetail(
  ctx: WorkspaceContext,
  assessmentId: string,
): Promise<EvidenceResult<DataHealthAssessment>> {
  return readEvidence<DataHealthAssessment>(
    authorityUrl(ctx.origin, `/operator-dashboard/data-health/assessments/${encodeURIComponent(assessmentId)}`),
    Boolean(assessmentId && ctx.protectedApi),
    "Data health assessment detail is unavailable.",
  );
}

export async function getFeatureDefinitions(
  ctx: WorkspaceContext,
  params?: { family?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<FeatureDefinitionPage>> {
  const query = new URLSearchParams({
    limit: String(params?.limit ?? 50),
    offset: String(params?.offset ?? 0),
  });
  if (params?.family && params.family !== "ALL") query.set("family", params.family);
  return readEvidence<FeatureDefinitionPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/feature-definitions?${query.toString()}`),
    ctx.protectedApi,
    "Feature definitions discovery is unavailable.",
  );
}

export async function getFeatureDefinition(
  ctx: WorkspaceContext,
  featureId?: string,
): Promise<EvidenceResult<FeatureDefinition>> {
  const id = featureId ?? ctx.workspace.featureDefinitionId ?? "";
  return readEvidence<FeatureDefinition>(
    authorityUrl(ctx.origin, `/operator-dashboard/feature-definitions/${encodeURIComponent(id)}`),
    Boolean(id && ctx.protectedApi),
    "Feature definition reference is unavailable.",
  );
}

export async function getFeatureMaterializations(
  ctx: WorkspaceContext,
  params?: { feature_id?: string; instrument?: string; dataset_version?: string; decision_time?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<FeatureMaterializationPage>> {
  const featureId = params?.feature_id ?? ctx.workspace.featureDefinitionId ?? "";
  const instrument = params?.instrument ?? ctx.workspace.featureInstrument ?? "";
  const datasetVersion = params?.dataset_version ?? ctx.workspace.featureDatasetVersion ?? "";
  const decisionTime = params?.decision_time ?? ctx.workspace.featureDecisionTime ?? ctx.evidenceTime;
  const limit = params?.limit ?? 20;
  const offset = params?.offset ?? 0;

  const query = new URLSearchParams({
    feature_id: featureId,
    instrument,
    dataset_version: datasetVersion,
    decision_time: decisionTime,
    limit: String(limit),
    offset: String(offset),
  });

  return readEvidence<FeatureMaterializationPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/feature-materializations?${query.toString()}`),
    Boolean(featureId && instrument && datasetVersion && decisionTime && ctx.protectedApi),
    "Feature materialization scope is unavailable.",
  );
}

export async function getSignalDiscovery(
  ctx: WorkspaceContext,
  params?: { as_of?: string; status?: string; instrument?: string; strategy_version?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<SignalPage>> {
  const query = new URLSearchParams({
    as_of: params?.as_of ?? ctx.evidenceTime,
    limit: String(params?.limit ?? 20),
    offset: String(params?.offset ?? 0),
  });
  if (params?.status && params.status !== "ALL") query.set("status", params.status);
  if (params?.instrument) query.set("instrument", params.instrument);
  if (params?.strategy_version) query.set("strategy_version", params.strategy_version);
  return readEvidence<SignalPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/signals?${query.toString()}`),
    ctx.protectedApi,
    "Signal lifecycle authority is not configured.",
  );
}

export async function getRiskDecisions(
  ctx: WorkspaceContext,
  params?: {
    approved?: boolean; account_id?: string; policy_version_id?: string;
    business_date?: string; has_reservation?: boolean; limit?: number; offset?: number;
  },
): Promise<EvidenceResult<RiskDecisionPage>> {
  const query = new URLSearchParams({
    limit: String(params?.limit ?? 20),
    offset: String(params?.offset ?? 0),
  });
  if (params?.approved !== undefined) query.set("approved", String(params.approved));
  if (params?.account_id) query.set("account_id", params.account_id);
  if (params?.policy_version_id) query.set("policy_version_id", params.policy_version_id);
  if (params?.business_date) query.set("business_date", params.business_date);
  if (params?.has_reservation !== undefined) query.set("has_reservation", String(params.has_reservation));
  return readEvidence<RiskDecisionPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/risk-decisions?${query.toString()}`),
    ctx.protectedApi,
    "Risk decision authority is not configured.",
  );
}

export async function getRiskEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<RiskDecisionPage>> {
  return getRiskDecisions(ctx, { limit: 20, offset: 0 });
}

export async function getStrategyCard(
  ctx: WorkspaceContext,
  params?: { strategyId?: string },
): Promise<EvidenceResult<StrategyCard>> {
  const strategyId = params?.strategyId ?? ctx.workspace.strategyId ?? "";
  return readEvidence<StrategyCard>(
    `${ctx.origin}/api/research?target=/research/strategies/${encodeURIComponent(strategyId)}`,
    Boolean(strategyId && ctx.protectedApi),
    "Strategy research reference is unavailable.",
  );
}

export async function getStrategyDiscovery(
  ctx: WorkspaceContext,
  params?: { family?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<DiscoveryPage<StrategyDiscovery>>> {
  const query = new URLSearchParams({
    limit: String(params?.limit ?? 20),
    offset: String(params?.offset ?? 0),
  });
  if (params?.family) query.set("family", params.family);
  return readEvidence<DiscoveryPage<StrategyDiscovery>>(
    authorityUrl(ctx.origin, `/operator-dashboard/strategies?${query.toString()}`),
    ctx.protectedApi,
    "Strategy discovery is unavailable.",
  );
}

export async function getExperimentEvidence(
  ctx: WorkspaceContext,
  params?: { experimentId?: string },
): Promise<EvidenceResult<Experiment>> {
  const experimentId = params?.experimentId ?? ctx.workspace.experimentId ?? "";
  return readEvidence<Experiment>(
    `${ctx.origin}/api/research?target=/research/experiments/${encodeURIComponent(experimentId)}`,
    Boolean(experimentId && ctx.protectedApi),
    "Backtest experiment reference is unavailable.",
  );
}

export async function getExperimentDiscovery(
  ctx: WorkspaceContext,
  params?: { strategy_id?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<DiscoveryPage<ExperimentDiscovery>>> {
  const strategyId = params?.strategy_id ?? ctx.workspace.strategyId ?? "";
  const query = new URLSearchParams({
    limit: String(params?.limit ?? 20),
    offset: String(params?.offset ?? 0),
  });
  if (strategyId) query.set("strategy_id", strategyId);
  return readEvidence<DiscoveryPage<ExperimentDiscovery>>(
    authorityUrl(ctx.origin, `/operator-dashboard/experiments?${query.toString()}`),
    ctx.protectedApi,
    "Backtest discovery is unavailable.",
  );
}

export async function getPromotionEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<Promotion>> {
  return readEvidence<Promotion>(
    `${ctx.origin}/api/research?target=/research/promotions/${encodeURIComponent(ctx.config.promotionDecisionId ?? "")}`,
    Boolean(ctx.config.promotionDecisionId && ctx.protectedApi),
    "Promotion decision reference is not configured.",
  );
}

export async function getScorecardEvidence(
  ctx: WorkspaceContext,
  params?: { scorecardId?: string },
): Promise<EvidenceResult<StrategyScorecard>> {
  const scorecardId = params?.scorecardId ?? ctx.workspace.scorecardId ?? "";
  return readEvidence<StrategyScorecard>(
    authorityUrl(ctx.origin, `/operator-dashboard/strategy-scorecards/${encodeURIComponent(scorecardId)}`),
    Boolean(scorecardId && ctx.protectedApi),
    "Strategy scorecard reference is unavailable.",
  );
}

export async function getScorecardDiscovery(
  ctx: WorkspaceContext,
  params?: { strategy_id?: string; status?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<StrategyScorecardDiscoveryPage>> {
  const query = new URLSearchParams({
    limit: String(params?.limit ?? 20),
    offset: String(params?.offset ?? 0),
  });
  if (params?.strategy_id) query.set("strategy_id", params.strategy_id);
  if (params?.status && params.status !== "ALL") query.set("status", params.status);
  return readEvidence<StrategyScorecardDiscoveryPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/strategy-scorecards?${query.toString()}`),
    ctx.protectedApi,
    "Strategy scorecard discovery is unavailable.",
  );
}

export async function getRegimeRunDiscovery(
  ctx: WorkspaceContext,
  params?: { instrument?: string; status?: string; model_version_id?: string; dataset_version?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<RegimeRunDiscoveryPage>> {
  const query = new URLSearchParams({ limit: String(params?.limit ?? 20), offset: String(params?.offset ?? 0) });
  if (params?.instrument) query.set("instrument", params.instrument);
  if (params?.status && params.status !== "ALL") query.set("status", params.status);
  if (params?.model_version_id) query.set("model_version_id", params.model_version_id);
  if (params?.dataset_version) query.set("dataset_version", params.dataset_version);
  return readEvidence<RegimeRunDiscoveryPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/regime-runs?${query.toString()}`),
    ctx.protectedApi,
    "Regime run discovery is unavailable.",
  );
}

export async function getRegimeRun(ctx: WorkspaceContext, params: { runId: string }): Promise<EvidenceResult<RegimeRun>> {
  return readEvidence<RegimeRun>(
    authorityUrl(ctx.origin, `/operator-dashboard/regime-runs/${encodeURIComponent(params.runId)}`),
    Boolean(params.runId && ctx.protectedApi),
    "Regime run reference is unavailable.",
  );
}

export async function getRegimeEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<RegimeRun>> {
  return getRegimeRun(ctx, { runId: ctx.workspace.regimeRunId ?? "" });
}

export async function getPortfolioConstructionRunDiscovery(
  ctx: WorkspaceContext,
  params?: { status?: string; policy_version_id?: string; regime_run_id?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<PortfolioConstructionDiscoveryPage>> {
  const query = new URLSearchParams({ limit: String(params?.limit ?? 20), offset: String(params?.offset ?? 0) });
  if (params?.status && params.status !== "ALL") query.set("status", params.status);
  if (params?.policy_version_id) query.set("policy_version_id", params.policy_version_id);
  if (params?.regime_run_id) query.set("regime_run_id", params.regime_run_id);
  return readEvidence<PortfolioConstructionDiscoveryPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/portfolio-construction-runs?${query.toString()}`),
    ctx.protectedApi,
    "Portfolio construction run discovery is unavailable.",
  );
}

export async function getPortfolioConstructionRun(
  ctx: WorkspaceContext, params: { runId: string },
): Promise<EvidenceResult<PortfolioConstruction>> {
  return readEvidence<PortfolioConstruction>(
    authorityUrl(ctx.origin, `/operator-dashboard/portfolio-construction-runs/${encodeURIComponent(params.runId)}`),
    Boolean(params.runId && ctx.protectedApi),
    "Portfolio construction run reference is unavailable.",
  );
}

export async function getPortfolioConstructionEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<PortfolioConstruction>> {
  return getPortfolioConstructionRun(ctx, { runId: ctx.workspace.portfolioConstructionRunId ?? "" });
}

export async function getInvestmentThesisEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<Thesis>> {
  return getInvestmentThesis(ctx, { thesisId: ctx.workspace.investmentThesisId ?? "" });
}

export async function getInvestmentThesis(ctx: WorkspaceContext, params: { thesisId: string }): Promise<EvidenceResult<Thesis>> {
  return readEvidence<Thesis>(
    `${ctx.origin}/api/investments?target=/investments/theses/${encodeURIComponent(params.thesisId)}`,
    Boolean(params.thesisId && ctx.protectedApi),
    "Investment thesis reference is unavailable.",
  );
}

export async function getInvestmentThesisDiscovery(
  ctx: WorkspaceContext,
  params?: { instrument?: string; status?: string; review_state?: string; synthetic_demo?: boolean; limit?: number; offset?: number },
): Promise<EvidenceResult<DiscoveryPage<InvestmentThesisDiscovery>>> {
  const query = new URLSearchParams({ limit: String(params?.limit ?? 20), offset: String(params?.offset ?? 0) });
  if (params?.instrument) query.set("instrument", params.instrument);
  if (params?.status && params.status !== "ALL") query.set("status", params.status);
  if (params?.review_state && params.review_state !== "ALL") query.set("review_state", params.review_state);
  if (params?.synthetic_demo !== undefined) query.set("synthetic_demo", String(params.synthetic_demo));
  return readEvidence<DiscoveryPage<InvestmentThesisDiscovery>>(
    authorityUrl(ctx.origin, `/operator-dashboard/investment-theses?${query.toString()}`),
    ctx.protectedApi,
    "Investment thesis discovery is unavailable.",
  );
}

export async function getInvestmentPortfolioEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<InvestmentPortfolio>> {
  return getInvestmentPortfolio(ctx, { portfolioId: ctx.workspace.investmentPortfolioId ?? "" });
}

export async function getInvestmentPortfolio(ctx: WorkspaceContext, params: { portfolioId: string }): Promise<EvidenceResult<InvestmentPortfolio>> {
  return readEvidence<InvestmentPortfolio>(
    `${ctx.origin}/api/investments?target=/investments/portfolios/${encodeURIComponent(params.portfolioId)}`,
    Boolean(params.portfolioId && ctx.protectedApi),
    "Investment portfolio reference is unavailable.",
  );
}

export async function getInvestmentPortfolioDiscovery(
  ctx: WorkspaceContext,
  params?: { status?: string; account_id?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<DiscoveryPage<InvestmentPortfolioDiscovery>>> {
  const query = new URLSearchParams({ limit: String(params?.limit ?? 20), offset: String(params?.offset ?? 0) });
  if (params?.status && params.status !== "ALL") query.set("status", params.status);
  if (params?.account_id) query.set("account_id", params.account_id);
  return readEvidence<DiscoveryPage<InvestmentPortfolioDiscovery>>(
    authorityUrl(ctx.origin, `/operator-dashboard/investment-portfolios?${query.toString()}`),
    ctx.protectedApi,
    "Investment portfolio discovery is unavailable.",
  );
}

export async function getNewsEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<NewsEventPage>> {
  return readEvidence<NewsEventPage>(
    authorityUrl(
      ctx.origin,
      `/operator-dashboard/news-events?limit=20&offset=0${ctx.workspace.newsInstrument ? `&instrument=${encodeURIComponent(ctx.workspace.newsInstrument)}` : ""}`,
    ),
    ctx.protectedApi,
    "News/event authority is not configured.",
  );
}

export async function getNewsEventDiscovery(
  ctx: WorkspaceContext,
  params?: {
    instrument?: string; entity?: string; category?: string; start?: string; end?: string;
    correction_state?: string; limit?: number; offset?: number;
  },
): Promise<EvidenceResult<NewsEventPage>> {
  const query = new URLSearchParams({ limit: String(params?.limit ?? 20), offset: String(params?.offset ?? 0) });
  if (params?.instrument) query.set("instrument", params.instrument);
  if (params?.entity) query.set("entity", params.entity);
  if (params?.category && params.category !== "ALL") query.set("category", params.category);
  if (params?.start) query.set("start", params.start);
  if (params?.end) query.set("end", params.end);
  if (params?.correction_state && params.correction_state !== "ALL") query.set("correction_state", params.correction_state);
  return readEvidence<NewsEventPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/news-events?${query.toString()}`),
    ctx.protectedApi,
    "News/event authority is not configured.",
  );
}

export async function getPaperOrderEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<PaperOrder>> {
  return getPaperOrderDetail(ctx, ctx.workspace.paperOrderIntentId ?? "");
}

export async function getPaperOrderDetail(ctx: WorkspaceContext, intentId: string): Promise<EvidenceResult<PaperOrder>> {
  return readEvidence<PaperOrder>(
    authorityUrl(ctx.origin, `/operator-dashboard/paper-orders/${encodeURIComponent(intentId)}`),
    Boolean(intentId && ctx.protectedApi),
    "Paper order evidence reference is unavailable.",
  );
}

export async function getPaperOrderDiscovery(
  ctx: WorkspaceContext,
  params?: {
    account_id?: string; instrument?: string; side?: string; lifecycle_status?: string;
    fill_state?: string; reconciliation_state?: string; limit?: number; offset?: number;
  },
): Promise<EvidenceResult<DiscoveryPage<PaperOrderDiscovery>>> {
  const query = new URLSearchParams({
    limit: String(params?.limit ?? 20),
    offset: String(params?.offset ?? 0),
  });
  if (params?.account_id) query.set("account_id", params.account_id);
  if (params?.instrument) query.set("instrument", params.instrument);
  if (params?.side && params.side !== "ALL") query.set("side", params.side);
  if (params?.lifecycle_status && params.lifecycle_status !== "ALL") query.set("lifecycle_status", params.lifecycle_status);
  if (params?.fill_state && params.fill_state !== "ALL") query.set("fill_state", params.fill_state);
  if (params?.reconciliation_state && params.reconciliation_state !== "ALL") query.set("reconciliation_state", params.reconciliation_state);
  return readEvidence<DiscoveryPage<PaperOrderDiscovery>>(
    authorityUrl(ctx.origin, `/operator-dashboard/paper-orders?${query.toString()}`),
    ctx.protectedApi,
    "Paper OMS discovery is unavailable.",
  );
}

export async function getPaperReconciliationEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<Reconciliation>> {
  return getPaperReconciliationDetail(ctx, ctx.workspace.paperAccountId ?? "");
}

export async function getPaperReconciliationDetail(ctx: WorkspaceContext, accountId: string): Promise<EvidenceResult<Reconciliation>> {
  return readEvidence<Reconciliation>(
    authorityUrl(ctx.origin, `/operator-dashboard/paper-accounts/${encodeURIComponent(accountId)}/reconciliation`),
    Boolean(accountId && ctx.protectedApi),
    "Paper account evidence reference is unavailable.",
  );
}

const BARE_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function utcDayBound(value: string, bound: "start" | "end"): string {
  return BARE_DATE_PATTERN.test(value) ? `${value}T${bound === "start" ? "00:00:00" : "23:59:59"}Z` : value;
}

export async function getAuditEventDiscovery(
  ctx: WorkspaceContext,
  params?: { event_type?: string; actor?: string; start?: string; end?: string; limit?: number; offset?: number },
): Promise<EvidenceResult<AuditEventDiscovery>> {
  const query = new URLSearchParams({
    limit: String(params?.limit ?? 20),
    offset: String(params?.offset ?? 0),
  });
  if (params?.event_type) query.set("event_type", params.event_type);
  if (params?.actor) query.set("actor", params.actor);
  if (params?.start) query.set("start", utcDayBound(params.start, "start"));
  if (params?.end) query.set("end", utcDayBound(params.end, "end"));
  return readEvidence<AuditEventDiscovery>(
    authorityUrl(ctx.origin, `/operator-dashboard/audit-events?${query.toString()}`),
    ctx.protectedApi,
    "Audit event authority is unavailable.",
  );
}

export async function getAuditEventDetail(ctx: WorkspaceContext, eventId: string): Promise<EvidenceResult<AuditEvent>> {
  return readEvidence<AuditEvent>(
    authorityUrl(ctx.origin, `/operator-dashboard/audit-events/${encodeURIComponent(eventId)}`),
    Boolean(eventId && ctx.protectedApi),
    "Audit event reference is unavailable.",
  );
}

export async function getAlertsEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<OperationalAlert[]>> {
  return readEvidence<OperationalAlert[]>(
    `${ctx.origin}/api/alerts`,
    ctx.protectedApi,
    "Operator alert credentials are not configured.",
  );
}

export async function getSreEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<SreOverview>> {
  return readEvidence<SreOverview>(
    authorityUrl(
      ctx.origin,
      `/operator-dashboard/sre-overview${ctx.workspace.sreServiceVersionId ? `?service_version_id=${encodeURIComponent(ctx.workspace.sreServiceVersionId)}` : ""}`,
    ),
    ctx.protectedApi,
    "SRE authority is not configured.",
  );
}

export type AllDashboardEvidence = {
  ctx: WorkspaceContext;
  command: EvidenceResult<CommandCenterEvidence>;
  dataHealth: EvidenceResult<DataHealth>;
  cadence: EvidenceResult<Cadence[]>;
  strategy: EvidenceResult<StrategyCard>;
  experiment: EvidenceResult<Experiment>;
  promotion: EvidenceResult<Promotion>;
  thesis: EvidenceResult<Thesis>;
  portfolio: EvidenceResult<InvestmentPortfolio>;
  alerts: EvidenceResult<OperationalAlert[]>;
  paperOrder: EvidenceResult<PaperOrder>;
  reconciliation: EvidenceResult<Reconciliation>;
  feature: EvidenceResult<FeatureDefinition>;
  featureMaterializations: EvidenceResult<FeatureMaterializationPage>;
  signals: EvidenceResult<SignalPage>;
  risk: EvidenceResult<RiskDecisionPage>;
  scorecard: EvidenceResult<StrategyScorecard>;
  regime: EvidenceResult<RegimeRun>;
  construction: EvidenceResult<PortfolioConstruction>;
  news: EvidenceResult<NewsEventPage>;
  sre: EvidenceResult<SreOverview>;
  auditEvents: EvidenceResult<AuditEventDiscovery>;
  instruments: EvidenceResult<DiscoveryPage<InstrumentDiscovery>>;
  strategies: EvidenceResult<DiscoveryPage<StrategyDiscovery>>;
  experiments: EvidenceResult<DiscoveryPage<ExperimentDiscovery>>;
  investmentTheses: EvidenceResult<DiscoveryPage<InvestmentThesisDiscovery>>;
  investmentPortfolios: EvidenceResult<DiscoveryPage<InvestmentPortfolioDiscovery>>;
  paperOrders: EvidenceResult<DiscoveryPage<PaperOrderDiscovery>>;
};

export async function getAllDashboardEvidence(): Promise<AllDashboardEvidence> {
  const ctx = await getWorkspaceContext();
  const [
    command,
    dataHealth,
    cadence,
    strategy,
    experiment,
    promotion,
    thesis,
    portfolio,
    alerts,
    paperOrder,
    reconciliation,
    instruments,
    strategies,
    experiments,
    investmentTheses,
    investmentPortfolios,
    paperOrders,
    feature,
    featureMaterializations,
    signals,
    risk,
    scorecard,
    regime,
    construction,
    news,
    sre,
    auditEvents,
  ] = await Promise.all([
    getCommandCenterEvidence(ctx),
    getDataHealthEvidence(ctx),
    getCadenceEvidence(ctx),
    getStrategyCard(ctx),
    getExperimentEvidence(ctx),
    getPromotionEvidence(ctx),
    getInvestmentThesisEvidence(ctx),
    getInvestmentPortfolioEvidence(ctx),
    getAlertsEvidence(ctx),
    getPaperOrderEvidence(ctx),
    getPaperReconciliationEvidence(ctx),
    getInstrumentDiscovery(ctx),
    getStrategyDiscovery(ctx),
    getExperimentDiscovery(ctx),
    getInvestmentThesisDiscovery(ctx),
    getInvestmentPortfolioDiscovery(ctx),
    getPaperOrderDiscovery(ctx),
    getFeatureDefinition(ctx),
    getFeatureMaterializations(ctx),
    getSignalDiscovery(ctx),
    getRiskEvidence(ctx),
    getScorecardEvidence(ctx),
    getRegimeEvidence(ctx),
    getPortfolioConstructionEvidence(ctx),
    getNewsEvidence(ctx),
    getSreEvidence(ctx),
    getAuditEventDiscovery(ctx, { limit: 1, offset: 0 }),
  ]);

  return {
    ctx,
    command,
    dataHealth,
    cadence,
    strategy,
    experiment,
    promotion,
    thesis,
    portfolio,
    alerts,
    paperOrder,
    reconciliation,
    instruments,
    strategies,
    experiments,
    investmentTheses,
    investmentPortfolios,
    paperOrders,
    feature,
    featureMaterializations,
    signals,
    risk,
    scorecard,
    regime,
    construction,
    news,
    sre,
    auditEvents,
  };
}
