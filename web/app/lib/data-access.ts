import { loadDashboardConfig, resolveDashboardOperatorToken, type DashboardConfig } from "../dashboard-config";
import { resolveDashboardWorkspaceReferences, type DiscoveredWorkspaceReferences, type ResolvedDashboardWorkspace } from "../dashboard-workspace";
import {
  type CommandCenterEvidence,
  type EvidenceResult,
  type FeatureDefinition,
  type FeatureMaterializationPage,
  type NewsEventPage,
  type PortfolioConstruction,
  type RegimeRun,
  type RiskDecisionPage,
  type SignalPage,
  type SreOverview,
  type StrategyScorecard,
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
  strategy_version: string;
  family: string;
  hypothesis: string;
  required_datasets: string[];
  feature_versions: string[];
  cost_model_version: string;
  limitations: string[];
  expected_regimes: string[];
  failure_conditions: string[];
  created_at: string;
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
    status: string;
    catalysts: string[];
    risks: string[];
    evidence_ids: string[];
  };
  valuations: {
    model_version: string;
    intrinsic_value_per_share: string;
    as_of: string;
  }[];
  company_research: {
    bull_case: string;
    base_case: string;
    bear_case: string;
    evidence_ids: string[];
  }[];
  reviews: { outcome: string; reviewed_at: string }[];
};

export type InvestmentPortfolio = {
  portfolio_id: string;
  as_of: string;
  assessment: { total_value: string; approved: boolean; reasons: string[] };
  holdings: { instrument_id: string; market_value: string; source_reference: string }[];
  rebalance_decisions: { rationale: string; approved_by: string }[];
  performance: { observed_at: string; net_asset_value: string; cumulative_return: string }[];
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
  paper_only: true;
  lifecycle_status: string;
  created_at: string;
  fill_state: string;
  reconciliation_state: string;
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

export async function getInstrumentDiscovery(ctx: WorkspaceContext): Promise<EvidenceResult<DiscoveryPage<InstrumentDiscovery>>> {
  return readEvidence<DiscoveryPage<InstrumentDiscovery>>(
    authorityUrl(ctx.origin, "/operator-dashboard/instruments?limit=20&offset=0"),
    ctx.protectedApi,
    "Instrument discovery is unavailable.",
  );
}

export async function getFeatureDefinition(ctx: WorkspaceContext): Promise<EvidenceResult<FeatureDefinition>> {
  return readEvidence<FeatureDefinition>(
    authorityUrl(ctx.origin, `/operator-dashboard/feature-definitions/${encodeURIComponent(ctx.workspace.featureDefinitionId ?? "")}`),
    Boolean(ctx.workspace.featureDefinitionId && ctx.protectedApi),
    "Feature definition reference is unavailable.",
  );
}

export async function getFeatureMaterializations(ctx: WorkspaceContext): Promise<EvidenceResult<FeatureMaterializationPage>> {
  return readEvidence<FeatureMaterializationPage>(
    authorityUrl(
      ctx.origin,
      `/operator-dashboard/feature-materializations?feature_id=${encodeURIComponent(ctx.workspace.featureDefinitionId ?? "")}&instrument=${encodeURIComponent(ctx.workspace.featureInstrument ?? "")}&dataset_version=${encodeURIComponent(ctx.workspace.featureDatasetVersion ?? "")}&decision_time=${encodeURIComponent(ctx.workspace.featureDecisionTime ?? "")}&limit=20&offset=0`,
    ),
    Boolean(
      ctx.workspace.featureDefinitionId &&
        ctx.workspace.featureInstrument &&
        ctx.workspace.featureDatasetVersion &&
        ctx.workspace.featureDecisionTime &&
        ctx.protectedApi,
    ),
    "Feature materialization scope is unavailable.",
  );
}

export async function getSignalEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<SignalPage>> {
  return readEvidence<SignalPage>(
    authorityUrl(ctx.origin, `/operator-dashboard/signals?as_of=${encodeURIComponent(ctx.evidenceTime)}&limit=20&offset=0`),
    ctx.protectedApi,
    "Signal lifecycle authority is not configured.",
  );
}

export async function getRiskEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<RiskDecisionPage>> {
  return readEvidence<RiskDecisionPage>(
    authorityUrl(ctx.origin, "/operator-dashboard/risk-decisions?limit=20&offset=0"),
    ctx.protectedApi,
    "Risk decision authority is not configured.",
  );
}

export async function getStrategyCard(ctx: WorkspaceContext): Promise<EvidenceResult<StrategyCard>> {
  return readEvidence<StrategyCard>(
    `${ctx.origin}/api/research?target=/research/strategies/${encodeURIComponent(ctx.workspace.strategyId ?? "")}`,
    Boolean(ctx.workspace.strategyId && ctx.protectedApi),
    "Strategy research reference is unavailable.",
  );
}

export async function getStrategyDiscovery(ctx: WorkspaceContext): Promise<EvidenceResult<DiscoveryPage<StrategyDiscovery>>> {
  return readEvidence<DiscoveryPage<StrategyDiscovery>>(
    authorityUrl(ctx.origin, "/operator-dashboard/strategies?limit=20&offset=0"),
    ctx.protectedApi,
    "Strategy discovery is unavailable.",
  );
}

export async function getExperimentEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<Experiment>> {
  return readEvidence<Experiment>(
    `${ctx.origin}/api/research?target=/research/experiments/${encodeURIComponent(ctx.workspace.experimentId ?? "")}`,
    Boolean(ctx.workspace.experimentId && ctx.protectedApi),
    "Backtest experiment reference is unavailable.",
  );
}

export async function getExperimentDiscovery(ctx: WorkspaceContext): Promise<EvidenceResult<DiscoveryPage<ExperimentDiscovery>>> {
  return readEvidence<DiscoveryPage<ExperimentDiscovery>>(
    authorityUrl(
      ctx.origin,
      `/operator-dashboard/experiments?strategy_id=${encodeURIComponent(ctx.workspace.strategyId ?? "")}&limit=20&offset=0`,
    ),
    Boolean(ctx.workspace.strategyId && ctx.protectedApi),
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

export async function getScorecardEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<StrategyScorecard>> {
  return readEvidence<StrategyScorecard>(
    authorityUrl(ctx.origin, `/operator-dashboard/strategy-scorecards/${encodeURIComponent(ctx.workspace.scorecardId ?? "")}`),
    Boolean(ctx.workspace.scorecardId && ctx.protectedApi),
    "Strategy scorecard reference is unavailable.",
  );
}

export async function getRegimeEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<RegimeRun>> {
  return readEvidence<RegimeRun>(
    authorityUrl(ctx.origin, `/operator-dashboard/regime-runs/${encodeURIComponent(ctx.workspace.regimeRunId ?? "")}`),
    Boolean(ctx.workspace.regimeRunId && ctx.protectedApi),
    "Regime run reference is unavailable.",
  );
}

export async function getPortfolioConstructionEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<PortfolioConstruction>> {
  return readEvidence<PortfolioConstruction>(
    authorityUrl(
      ctx.origin,
      `/operator-dashboard/portfolio-construction-runs/${encodeURIComponent(ctx.workspace.portfolioConstructionRunId ?? "")}`,
    ),
    Boolean(ctx.workspace.portfolioConstructionRunId && ctx.protectedApi),
    "Portfolio construction run reference is unavailable.",
  );
}

export async function getInvestmentThesisEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<Thesis>> {
  return readEvidence<Thesis>(
    `${ctx.origin}/api/investments?target=/investments/theses/${encodeURIComponent(ctx.workspace.investmentThesisId ?? "")}`,
    Boolean(ctx.workspace.investmentThesisId && ctx.protectedApi),
    "Investment thesis reference is unavailable.",
  );
}

export async function getInvestmentThesisDiscovery(ctx: WorkspaceContext): Promise<EvidenceResult<DiscoveryPage<InvestmentThesisDiscovery>>> {
  return readEvidence<DiscoveryPage<InvestmentThesisDiscovery>>(
    authorityUrl(ctx.origin, "/operator-dashboard/investment-theses?limit=20&offset=0"),
    ctx.protectedApi,
    "Investment thesis discovery is unavailable.",
  );
}

export async function getInvestmentPortfolioEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<InvestmentPortfolio>> {
  return readEvidence<InvestmentPortfolio>(
    `${ctx.origin}/api/investments?target=/investments/portfolios/${encodeURIComponent(ctx.workspace.investmentPortfolioId ?? "")}`,
    Boolean(ctx.workspace.investmentPortfolioId && ctx.protectedApi),
    "Investment portfolio reference is unavailable.",
  );
}

export async function getInvestmentPortfolioDiscovery(ctx: WorkspaceContext): Promise<EvidenceResult<DiscoveryPage<InvestmentPortfolioDiscovery>>> {
  return readEvidence<DiscoveryPage<InvestmentPortfolioDiscovery>>(
    authorityUrl(ctx.origin, "/operator-dashboard/investment-portfolios?limit=20&offset=0"),
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

export async function getPaperOrderEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<PaperOrder>> {
  return readEvidence<PaperOrder>(
    `${ctx.origin}/api/paper-oms?target=/paper-oms/orders/${encodeURIComponent(ctx.workspace.paperOrderIntentId ?? "")}`,
    Boolean(ctx.workspace.paperOrderIntentId && ctx.protectedApi),
    "Paper order evidence reference is unavailable.",
  );
}

export async function getPaperOrderDiscovery(ctx: WorkspaceContext): Promise<EvidenceResult<DiscoveryPage<PaperOrderDiscovery>>> {
  return readEvidence<DiscoveryPage<PaperOrderDiscovery>>(
    authorityUrl(ctx.origin, "/operator-dashboard/paper-orders?limit=20&offset=0"),
    ctx.protectedApi,
    "Paper OMS discovery is unavailable.",
  );
}

export async function getPaperReconciliationEvidence(ctx: WorkspaceContext): Promise<EvidenceResult<Reconciliation>> {
  return readEvidence<Reconciliation>(
    `${ctx.origin}/api/paper-oms?target=/paper-oms/accounts/${encodeURIComponent(ctx.workspace.paperAccountId ?? "")}/reconciliation`,
    Boolean(ctx.workspace.paperAccountId && ctx.protectedApi),
    "Paper account evidence reference is unavailable.",
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
    getSignalEvidence(ctx),
    getRiskEvidence(ctx),
    getScorecardEvidence(ctx),
    getRegimeEvidence(ctx),
    getPortfolioConstructionEvidence(ctx),
    getNewsEvidence(ctx),
    getSreEvidence(ctx),
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
  };
}
