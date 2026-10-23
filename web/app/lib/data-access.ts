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

/** Phase R5 UI-1: a source's tier *ceiling* under its timing contract -- never a dataset verdict. */
export type TimingSource = {
  source_id: string;
  label: string;
  timing_authority: string;
  tier_ceiling: string;
  timing_contract_hash: string | null;
  requires_declared_publication_lag: boolean;
  publication_lag_state: string;
  tier_ceiling_reason: string | null;
};

export type EvidenceCatalog = {
  state: "AVAILABLE" | "UNAVAILABLE";
  version: string;
  as_of: string;
  timing_sources: TimingSource[];
  historical_sources: {
    source_id: string; provider: string; dataset_name: string; dataset_count: number;
    sealed_count: number; latest_created_at: string; tier_ceiling: string; synthetic_marker: boolean;
  }[];
  t4_dataset_total: number;
  t4_datasets: {
    dataset_version_id: string; content_hash: string; source_id: string; session_id: string;
    utc_day: string; window_index: number; first_market_knowledge_at: string;
    last_market_knowledge_at: string; observation_count: number;
    distinct_knowledge_time_count: number; sealed_at: string; registered_at: string;
  }[];
  public_archive_dataset_total: number;
  public_archive_datasets: {
    dataset_version_id: string; content_hash: string; source_id: string; symbol: string;
    first_utc_day: string; last_utc_day: string; file_count: number; trade_count: number;
    publication_lag_slot: string; registered_at: string;
  }[];
  research_frames: { frame_kind: string; manifest_count: number; row_count: number; total_bytes: number }[];
  limitations: string[];
};

export type CaptureExclusion = { partition: string; reasons: string[] };

export type CaptureAvailability = {
  state: "AVAILABLE" | "UNCONFIGURED" | "UNAVAILABLE";
  version: string;
  as_of: string;
  disk_free_bytes: number | null;
  sources: {
    /** The archive root's directory name; R0 and the R1B universe are separate roots, never merged. */
    archive: string;
    source_id: string;
    exchange_symbol: string;
    purpose: "PRODUCTION" | "UNIVERSE" | "MEASUREMENT";
    proven_record_count: number;
    proven_seconds: number;
    windows: { session_id: string; start_at: string; last_proven_at: string; end_proof: string; record_count: number }[];
    gaps: { start_at: string; end_at: string | null; kind: string; detail: string | null }[];
    not_finalized: CaptureExclusion[];
    latest_clock_offset: {
      sampled_at: string; offset_estimate_seconds: number; offset_bound_seconds: number; partition: string;
    } | null;
  }[];
  unattributed: CaptureExclusion[];
  limitations: string[];
};

/** Phase R5 UI-1b: a catalogued bar frame and its claim ceiling. */
export type ChartSeriesRef = {
  manifest_hash: string;
  frame_kind: "OHLCV" | "T2_ARCHIVE_OHLCV_1M" | "T4_OHLCV_1M";
  label: string;
  dataset_version_id: string;
  instrument: string | null;
  row_count: number;
  tier_ceiling: string;
  /** Timeframes the candle read serves; empty when the frame declares no bar interval. */
  timeframes: string[];
  /** Set only for a catalogued T2 research window. */
  window: {
    content_hash: string; source_id: string; first_utc_day: string; last_utc_day: string; files: number;
    not_published_days: string[]; rejected_days: string[]; day_gaps: { after: string; before: string }[];
    bar_logical_content_hash: string; last_bar_close_at: string;
  } | null;
};

/** Stage B.1.1: one UTC-aligned candle; PARTIAL when ``bar_count < expected_bar_count``. */
export type ChartCandle = {
  open_at: string; close_at: string; first_bar_at: string; last_bar_at: string;
  bar_count: number; expected_bar_count: number; complete: boolean;
  open: string; high: string; low: string; close: string; volume: string;
};

export type ChartGap = { after_open_at: string; before_open_at: string; missing_candles: number };

export type ChartCandles = {
  state: "AVAILABLE" | "UNAVAILABLE";
  version: string;
  series: ChartSeriesRef;
  timeframe: string;
  coverage: {
    first_bar_at: string | null; last_bar_close_at: string | null; source_bars: number; source_bar_seconds: number;
    expected_source_bars: number; missing_source_bars: number;
    holdout_boundary: string | null;
  };
  candles: ChartCandle[];
  partial_candles: number;
  gaps: ChartGap[];
  end: string | null;
  earlier_end: string | null;
  limitations: string[];
};

export type ChartSeriesRefPage = { state: "AVAILABLE" | "UNAVAILABLE"; version: string; items: ChartSeriesRef[] };

/** Exact buckets: first open, last close, max high, min low, summed volume -- decimal text. */
export type ChartBucket = {
  first_bar_at: string; last_bar_at: string; bar_count: number;
  open: string; high: string; low: string; close: string; volume: string;
};

export type ChartSeries = {
  state: "AVAILABLE" | "UNAVAILABLE";
  version: string;
  series: ChartSeriesRef;
  instrument: string;
  instruments: string[];
  bars_in_frame_for_instrument: number;
  bars_per_bucket: number;
  buckets: ChartBucket[];
  limitations: string[];
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

export async function getEvidenceCatalog(ctx: WorkspaceContext): Promise<EvidenceResult<EvidenceCatalog>> {
  return readEvidence<EvidenceCatalog>(
    authorityUrl(ctx.origin, "/operator-dashboard/evidence-catalog"),
    ctx.protectedApi,
    "Evidence catalog is unavailable.",
  );
}

export async function getCaptureAvailability(ctx: WorkspaceContext): Promise<EvidenceResult<CaptureAvailability>> {
  return readEvidence<CaptureAvailability>(
    authorityUrl(ctx.origin, "/operator-dashboard/capture-availability"),
    ctx.protectedApi,
    "Capture availability is unavailable.",
  );
}

// ---------------------------------------------------------------------------
// UI-2 research terminal (R4 -> R10). Every view carries its own claim; the
// terminal shows it verbatim and never upgrades it.
// ---------------------------------------------------------------------------

export type TerminalCycle = {
  cycle_id: string;
  holdout_start: string;
  holdout_state: "UNOPENED" | "OPENED";
  holdout_end_exclusive: string | null;
  opened_at: string | null;
  preregistrations: Record<string, number>;
  validated: boolean;
};

export type TerminalOverview = {
  generated_at: string;
  studies: number;
  finished_studies: number;
  reruns: Record<string, number>;
  cycle: TerminalCycle;
  candidate_states: Record<string, number>;
  signals: Record<string, number>;
  latest_signal_at: string | null;
  incubation_fills: Record<string, number>;
  accounts: Record<string, number>;
  owner_gates: { gate: string; topic: string; status: "OPEN" | "SATISFIED"; evidence: string }[];
  authority_legend: Record<string, string>;
};

export type TerminalRerun = {
  rerun_hash: string;
  study_id: string;
  study_label: string;
  strategy_family: string;
  candidate_set_hash: string;
  selection_status: string;
  claim: "DECIMAL_SELECTION_ESTABLISHED" | "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED";
  economics: "GROSS_CONDITIONAL_NON_PROMOTABLE";
  rerun_count: number;
  metric: string;
  selected: { rank: number; trial_id: string; metric_value: string }[];
  recorded_at: string;
};

/** A recorded C.1.1 walk-forward report: development evidence, search tier, gross; never validation. */
export type TerminalWalkForward = {
  report_hash: string;
  study_id: string;
  study_label: string;
  study_content_hash: string;
  dataset_content_hash: string;
  split_hash: string;
  evaluation_class: "WALK_FORWARD_DEVELOPMENT";
  numeric_tier: "SEARCH_NON_AUTHORITATIVE";
  cost_mode: "GROSS_NON_PROMOTABLE";
  promotable: false;
  rule: Record<string, unknown>;
  multiple_testing_trial_count: number;
  evaluation_end_exclusive: string;
  folds: {
    fold: number; status: string; test_start: string; test_end_exclusive: string; selected_trial_id: string | null;
    train_metric_value: string | null; test_metrics: Record<string, unknown>; flags: string[];
  }[];
  authority_reasons: string[];
  recorded_at: string;
};

/** One candidate's readiness and exit criteria, read from its stored chain. An assessment, never a decision. */
export type TerminalCandidate = {
  links: {
    study_id: string; study_label: string; trial_id: string; cycle_id: string; rerun_hash: string | null;
    walk_forward: { report_hash: string; fold_count: number; folds_selecting_trial: number; recorded_at: string }[];
    preregistration_hash: string | null; preregistration_status: "DRAFT" | "AUTHORIZED" | null;
    holdout_opened_for_this_preregistration: boolean; validation_hash: string | null; criteria_hash: string | null;
  };
  claim: "SEARCH_NON_AUTHORITATIVE" | "DECIMAL_SELECTION_ESTABLISHED" | "INCUBATING" | "REJECTED" | "EVIDENCE_INCONSISTENT";
  economics: "GROSS_NON_PROMOTABLE" | "VERIFIED_FEES_NOT_COST_COMPLETE";
  readiness: {
    stage: string; canonical_state: string | null; blockers: string[]; owner_gates: string[];
    evidence: Record<string, unknown>; assessment_hash: string;
    claim: "READINESS_ASSESSMENT_NOT_A_DECISION"; promotion: "PROFESSIONALLY_VALIDATED_RESERVED_NEVER_PROMOTED_HERE";
  };
  exit_criteria: {
    status: "NOT_RECORDED" | "DRAFT" | "AUTHORIZED" | "REFUSED"; criteria_hash: string | null; recorded_at: string | null;
    authorized_by: string | null; authorized_on: string | null; unresolved: string[]; thresholds: Record<string, unknown>;
    stage: string; evidence_inputs: Record<string, string>; blockers: string[]; refusals: string[]; owner_gates: string[];
    claim: "EXIT_READINESS_ASSESSMENT_NOT_A_DECISION"; account_activation: "NEVER_ACTIVATES_AN_ACCOUNT";
  };
  evidence_not_loaded: string[];
  validated_claim: "NONE_VALIDATED";
  as_of: string;
};

export type TerminalValidation = {
  cycle: TerminalCycle;
  candidates: {
    study_id: string;
    study_label: string;
    trial_id: string;
    state: string;
    label: "INCUBATING" | "REJECTED";
    reasons: string[];
    recorded_at: string;
  }[];
  validated_claim: "NONE_VALIDATED";
};

export type TerminalSignal = {
  signal_id: string;
  symbol: string;
  authority: string;
  claim: string;
  target_from: number;
  target_to: number;
  bar_open_at: string;
  decided_at: string;
  study_id: string;
  trial_id: string;
  explanation: Record<string, string>;
};

export type TerminalAccount = {
  account_id: string;
  kind: string;
  display_name: string;
  base_currency: string;
  policy_status: "NO_POLICY" | "UNCONFIGURED" | "ACTIVE";
  policy_version_id: string | null;
  unresolved: string[];
  recorded_at: string | null;
};

export type TerminalIncubationCandidate = {
  study_id: string;
  trial_id: string;
  symbol: string;
  fills: number;
  fills_by_status: Record<string, number>;
  chain_breaks: number;
  fills_skipped_unknown_position: number;
  chain_inconsistencies: number;
  open_position: number | null;
  round_trips: { direction: number; holding_minutes: number; gross_return: string }[];
  gross_return_sum: string;
  closed_sides: number;
  break_even_cost_bps_per_side?: string;
  net: string | Record<string, string>;
  elapsed_days: string;
  required_days: number | string;
  state: string;
};

export type TerminalIncubation = {
  state: "AVAILABLE" | "NO_FILLS";
  report: { sizing: string; cost_mode: string; funding: string; cost_complete: boolean; claim: string;
    candidates: TerminalIncubationCandidate[] };
};

export type ReadinessSubject = {
  subject: string;
  status: "READY" | "BLOCKED" | "DONE";
  reasons: string[];
  identities: Record<string, string>;
};

export type ReadinessAnswer = {
  key: "research_run" | "candidate_freeze" | "decimal_rerun" | "holdout_open" | "research_watch" | "paper_incubation";
  question: string;
  status: "READY" | "BLOCKED";
  reasons: string[];
  owner_gates: string[];
  identities: Record<string, unknown>;
  evidence: Record<string, unknown>;
  next_action: string;
  subjects: ReadinessSubject[];
};

export type ActivationReadiness = {
  generated_at: string;
  cycle_id: string;
  holdout_state: "UNOPENED" | "OPENED";
  answers: ReadinessAnswer[];
  owner_gates_open: string[];
  state_hash: string;
};

export function getTerminalActivation(ctx: WorkspaceContext): Promise<EvidenceResult<ActivationReadiness>> {
  return terminal(ctx, authorityUrl(ctx.origin, "/operator-dashboard/research-terminal/activation"), "Activation readiness is unavailable.");
}

export type TerminalCommand = {
  command_id: string;
  kind: string;
  inputs: Record<string, unknown>;
  requested_by: string;
  requested_at: string;
  state: string;
  detail: Record<string, unknown>;
  updated_at: string;
};

export function getTerminalCommands(ctx: WorkspaceContext): Promise<EvidenceResult<TerminalCommand[]>> {
  return terminal(ctx, authorityUrl(ctx.origin, "/operator-dashboard/research-terminal/commands?limit=30"), "Terminal commands are unavailable.");
}

export type PaperAccountLedger = {
  state: "AVAILABLE" | "BLOCKED";
  account_id: string;
  reasons?: string[];
  summary?: { decisions: Record<string, number>; rejections_by_reason: Record<string, number>;
    equity_by_scenario: Record<string, string>; open_positions: number };
  ledger?: { policy_version_id: string; cost_mode: string; risk_scenario: string; not_applicable_controls: string[];
    starting_capital: string; claim: string; content_hash: string };
  /** The account's authoritative policy resolution and each recorded target's paper risk verdict. */
  risk?: AccountRisk;
};

export function getTerminalPaperAccount(ctx: WorkspaceContext, accountId: string): Promise<EvidenceResult<PaperAccountLedger>> {
  return terminal(ctx, authorityUrl(ctx.origin, `/operator-dashboard/research-terminal/paper-account?account_id=${encodeURIComponent(accountId)}`), "Paper account ledger is unavailable.");
}

export type StrategyLabDatasetRef = {
  role: string;
  dataset_version_id: string;
  content_hash: string;
  evidence_tier: string;
  knowledge_upper_bound_exclusive: string | null;
  resolution: "RESEARCH_WINDOW_CATALOGUE" | "UNRESOLVED";
  symbol: string | null;
  first_utc_day: string | null;
  last_utc_day: string | null;
  bars: number | null;
  continuous: boolean | null;
  not_published_days: string[];
  rejected_days: string[];
};

export type StrategyLabStudySummary = {
  study_id: string;
  content_hash: string;
  strategy_family: string;
  strategy_version: string;
  label: string;
  planned_trial_count: number;
  evaluation_upper_bound_exclusive: string;
  registered_at: string;
  queue_states: Record<string, number>;
  result_count: number;
  authority: { authority_status: "NON_AUTHORITATIVE"; promotable: false; reasons: string[] };
  instrument: string | null;
  instrument_source: "RESEARCH_WINDOW_CATALOGUE" | "UNRESOLVED";
  datasets: StrategyLabDatasetRef[];
};

export type StrategyLabPageInfo = { limit: number; offset: number; returned: number; has_more: boolean };

export type StrategyLabStudyPage = {
  state: "AVAILABLE" | "UNAVAILABLE";
  items: StrategyLabStudySummary[];
  page: StrategyLabPageInfo;
  total: number | null;
  filters: Record<string, string>;
  facets: { strategy_families: string[]; instruments: string[]; instrument_catalogue: boolean };
};

export type StrategyLabCandidateSet = {
  candidate_set_hash: string;
  manifest_hash: string;
  rule: Record<string, unknown>;
  candidates: { rank: number; trial_id: string; trial_content_hash: string; result_content_hash: string; metric_value: string }[];
  eligible_count: number;
  ineligible_count: number;
  cutoff_tie: boolean;
  multiple_testing_trial_count: number;
  numeric_tier: "SEARCH_NON_AUTHORITATIVE";
  authoritative_rerun: string;
  recorded_at: string;
};

export type StrategyLabRerun = {
  rerun_hash: string;
  candidate_set_hash: string;
  selection_status: string;
  claim: "DECIMAL_SELECTION_ESTABLISHED" | "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED";
  economics: "GROSS_CONDITIONAL_NON_PROMOTABLE";
  claim_ceiling: string | null;
  rerun_count: number;
  rule: Record<string, unknown>;
  baseline_lag_micros: number | null;
  sweep_lags_micros: number[];
  selected: { rank: number; trial_id: string; metric_value: string }[];
  decimal_cutoff: string | null;
  cutoff_tie: boolean | null;
  changed_versus_search_selection: boolean | null;
  undefined_metric_trials: string[];
  reason: string | null;
  reconciliation: Record<string, number>;
  held_divergence_bars: number;
  trade_count_divergence: number;
  recorded_at: string;
};

export type StrategyLabCandidateState = { trial_id: string; state: string; evidence_hash: string; reasons: string[]; recorded_at: string };

export type StrategyLabEconomics = {
  gross_results: "GROSS_SEARCH_TIER";
  cost_mode: string;
  cost_policy_slot: string;
  funding: string | null;
  fill_liquidity: string | null;
  hypothetical_slippage_scenarios: Record<string, unknown>[];
  hypothetical_costs: "BOUND_TO_STUDY" | "NONE_BOUND";
  break_even_metric: string | null;
  verified_costs: "UNAVAILABLE";
  verified_cost_reasons: string[];
  net_profitability: "UNAVAILABLE";
  net_profitability_reasons: string[];
};

export type StrategyLabStudyDetail = {
  summary: StrategyLabStudySummary;
  strategy: Record<string, unknown>;
  parameter_space: { kind: string; name: string; values?: string[] }[];
  datasets: Record<string, unknown>[];
  search: Record<string, unknown>;
  numeric_policy_slot: string;
  cost_policy_slot: string;
  manifests: { manifest_hash: string; planned_trial_count: number; result_count: number; outcomes: Record<string, number>; recorded_at: string }[];
  candidate_sets: StrategyLabCandidateSet[];
  policies: Record<string, unknown>;
  reruns: StrategyLabRerun[];
  candidate_states: StrategyLabCandidateState[];
  economics: StrategyLabEconomics | null;
  holdout: { boundary: string; evaluation_upper_bound_exclusive: string; evaluation_before_boundary: boolean; cycle_id: string; state: "UNOPENED" | "OPENED" } | null;
};

export type StrategyLabTrial = {
  trial_id: string;
  ordinal: number;
  trial_content_hash: string;
  parameters: Record<string, string>;
  outcome: "EVALUATED" | "INADMISSIBLE_PARAMETERS" | "PENDING";
  numeric_tier: string | null;
  cost_policy_slot: string | null;
  result_content_hash: string | null;
  metrics: Record<string, string | null>;
  produced_at: string | null;
  frozen_rank: number | null;
  rerun: { rerun_hash: string; selection_status: string; reasons: string[]; reconciliation_by_lag: Record<string, string>; decimal_rank: number | null; decimal_metric_value: string | null } | null;
  candidate_state: string | null;
};

export type StrategyLabTrialPage = {
  state: "AVAILABLE" | "UNAVAILABLE";
  study_id: string;
  numeric_tier: "SEARCH_NON_AUTHORITATIVE";
  economics: "GROSS_NON_PROMOTABLE";
  sort: string;
  direction: "asc" | "desc";
  outcome: string;
  frozen_only: boolean;
  metric_keys: string[];
  items: StrategyLabTrial[];
  page: StrategyLabPageInfo;
  total: number;
};

export type StrategyLabTrialDetail = {
  trial: StrategyLabTrial;
  study_label: string;
  strategy_family: string;
  rerun_lags: { lag_micros: number; reconciliation: string; held_divergence_bars: number | null; trade_count_divergence: number | null; float_near_tie_decisions: number | null; metrics: Record<string, string | null> }[];
  candidate_states: StrategyLabCandidateState[];
  economics: StrategyLabEconomics;
};

export type StrategyLabTrialQuery = {
  limit: number;
  offset: number;
  sort: string;
  direction: "asc" | "desc";
  outcome: "ALL" | "EVALUATED" | "INADMISSIBLE_PARAMETERS" | "PENDING";
  frozenOnly: boolean;
};

/** What a Stage B.1.2b visualization was computed from; compare with the study shown before trusting it. */
export type StrategyLabEvidenceIdentity = {
  study_id: string;
  study_content_hash: string;
  manifest_hash: string | null;
  results_digest: string;
  result_count: number;
  trial_count: number;
};

export type StrategyLabHeatmapCellState =
  | "VALUE" | "METRIC_UNAVAILABLE" | "INADMISSIBLE_PARAMETERS" | "FAILED" | "CANCELLED" | "PENDING" | "NO_TRIAL" | "AMBIGUOUS";

export type StrategyLabHeatmapCell = {
  x: string;
  y: string;
  state: StrategyLabHeatmapCellState;
  trial_ids: string[];
  ordinal: number | null;
  /** Stored metric text, verbatim. Never parsed in the browser. */
  value: string | null;
  /** Server-side ``Decimal`` sign and dense rank (1 = lowest); order only, never a quality verdict. */
  sign: -1 | 0 | 1 | null;
  rank: number | null;
  reason: string | null;
  frozen_rank: number | null;
  decimal_rerun_status: string | null;
};

export type StrategyLabHeatmap = {
  state: "AVAILABLE" | "SELECTION_REQUIRED" | "SELECTION_INVALID" | "UNAVAILABLE";
  numeric_tier: "SEARCH_NON_AUTHORITATIVE";
  economics: "GROSS_NON_PROMOTABLE";
  evidence: StrategyLabEvidenceIdentity;
  dimensions: { name: string; kind: string; values: string[] }[];
  x: string | null;
  y: string | null;
  metric: string | null;
  fixed: Record<string, string>;
  required_fixed: string[];
  reasons: string[];
  x_values: string[];
  y_values: string[];
  cells: StrategyLabHeatmapCell[];
  counts: Record<string, number>;
  distinct_values: number;
  selection: string[];
};

export type StrategyLabHeatmapQuery = { x?: string; y?: string; metric?: string; fixed: [string, string][] };

export type StrategyLabBreakEvenDistribution = {
  state: "AVAILABLE" | "UNAVAILABLE";
  numeric_tier: "SEARCH_NON_AUTHORITATIVE";
  claim: "GROSS_RESULT_COST_SENSITIVITY";
  verified_costs: "UNAVAILABLE";
  net_profitability: "UNAVAILABLE";
  metric: "break_even_bps_per_side";
  evidence: StrategyLabEvidenceIdentity;
  reasons: string[];
  trial_outcomes: Record<string, number>;
  eligible_count: number;
  positive: number;
  zero: number;
  negative: number;
  unavailable: number;
  bins: { side: "NEGATIVE" | "POSITIVE"; lower: string; upper: string; lower_inclusive: boolean; upper_inclusive: boolean; count: number }[];
  order_statistics: Record<string, string>;
  selection: string[];
};

/** ``url`` is already an ``authorityUrl(...)``; wrapping it again made the proxy refuse every terminal read. */
function terminal<T>(ctx: WorkspaceContext, url: string, unavailable: string): Promise<EvidenceResult<T>> {
  return readEvidence<T>(url, ctx.protectedApi, unavailable);
}

export function getTerminalOverview(ctx: WorkspaceContext): Promise<EvidenceResult<TerminalOverview>> {
  return terminal(ctx, authorityUrl(ctx.origin, "/operator-dashboard/research-terminal/overview"), "Research terminal overview is unavailable.");
}

export function getTerminalReruns(ctx: WorkspaceContext): Promise<EvidenceResult<TerminalRerun[]>> {
  return terminal(ctx, authorityUrl(ctx.origin, "/operator-dashboard/research-terminal/reruns?limit=50"), "Authority reruns are unavailable.");
}

export function getTerminalValidation(ctx: WorkspaceContext): Promise<EvidenceResult<TerminalValidation>> {
  return terminal(ctx, authorityUrl(ctx.origin, "/operator-dashboard/research-terminal/validation?limit=200"), "Validation state is unavailable.");
}

export function getTerminalWalkForward(ctx: WorkspaceContext): Promise<EvidenceResult<TerminalWalkForward[]>> {
  return terminal(ctx, authorityUrl(ctx.origin, "/operator-dashboard/research-terminal/walk-forward?limit=50"), "Walk-forward reports are unavailable.");
}

export function getTerminalCandidates(ctx: WorkspaceContext): Promise<EvidenceResult<TerminalCandidate[]>> {
  return terminal(ctx, authorityUrl(ctx.origin, "/operator-dashboard/research-terminal/candidates?limit=50"), "Candidate readiness is unavailable.");
}

export function getTerminalSignals(ctx: WorkspaceContext, symbol?: string): Promise<EvidenceResult<TerminalSignal[]>> {
  const query = new URLSearchParams({ limit: "50" });
  if (symbol !== undefined) query.set("symbol", symbol);
  return terminal(ctx, authorityUrl(ctx.origin, `/operator-dashboard/research-terminal/signals?${query}`), "Live signals are unavailable.");
}

export function getTerminalAccounts(ctx: WorkspaceContext): Promise<EvidenceResult<TerminalAccount[]>> {
  return terminal(ctx, authorityUrl(ctx.origin, "/operator-dashboard/research-terminal/accounts"), "Account policies are unavailable.");
}

export function getTerminalIncubation(ctx: WorkspaceContext): Promise<EvidenceResult<TerminalIncubation>> {
  return terminal(ctx, authorityUrl(ctx.origin, "/operator-dashboard/research-terminal/incubation"), "Paper incubation is unavailable.");
}

/** A recorded value is ``RECORDED`` with its stored text, or ``MISSING`` -- never zero. */
export type ComparisonMetric = { key: string; state: "RECORDED" | "MISSING"; value: string | null };

export type ComparisonRerunLag = {
  lag_micros: number; reconciliation: string; held_divergence_bars: number | null;
  trade_count_divergence: number | null; metrics: ComparisonMetric[];
};

export type ComparisonDispersion = {
  metric: string; folds_with_value: number; folds_missing: number; folds_not_numeric: number;
  min: string | null; max: string | null; median_nearest_rank: string | null; spread: string | null;
  positive: number; zero: number; negative: number;
};

/** One frozen candidate with every evidence dimension kept apart (research_candidate_comparison_v1). */
export type CostFragility = {
  status: string; threshold_bps_per_side: string | null; search_threshold_bps_per_side: string | null;
  lag_micros: number | null; claim: string; units: string; basis: string; threshold_precision: string;
  verified_account_fees: string; observed_spread_comparison: string; funding: string;
  net_profitability: string; independent_validation: string; reasons: string[];
  scenarios: { name: string; cost_bps_per_side: string; rationale: string; source: string; status: string;
    remaining_bps_per_side: string | null; relation: string; in_net: false }[];
};

export type ComparisonRow = {
  identity: {
    study_id: string; study_content_hash: string; study_label: string; strategy_family: string; manifest_hash: string;
    candidate_set_hash: string; freeze_rule: Record<string, unknown>; trial_id: string; trial_content_hash: string;
    result_content_hash: string; frozen_rank: number; parameters: Record<string, string>;
  };
  search: {
    claim: "SEARCH_NON_AUTHORITATIVE"; outcome: string; metrics: ComparisonMetric[]; frozen_metric: string;
    frozen_metric_value: string; multiple_testing_trial_count: number; eligible_count: number; ineligible_count: number;
    cutoff_tie: boolean;
  };
  decimal_rerun: {
    claim: "DECIMAL_SELECTION_ESTABLISHED" | "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED" | "NO_DECIMAL_RERUN_RECORDED";
    economics: "GROSS_CONDITIONAL_NON_PROMOTABLE"; reruns_recorded: number; rerun_hash: string | null;
    selection_status: string | null; in_decimal_selection: boolean | null; decimal_rank: number | null;
    decimal_selection_changed_versus_search: boolean | null; claim_ceiling: string | null;
    baseline: ComparisonRerunLag | null; sweep: ComparisonRerunLag[]; sweep_lags_missing: number[];
  };
  walk_forward: {
    claim: "WALK_FORWARD_DEVELOPMENT" | "NO_WALK_FORWARD_REPORT_RECORDED"; numeric_tier: "SEARCH_NON_AUTHORITATIVE";
    cost_mode: "GROSS_NON_PROMOTABLE";
    reports: {
      report_hash: string; fold_count: number; folds_excluded_no_eligible_trial: number; folds_selected_other_trial: number;
      folds_selected_this_candidate: number[]; dispersion: ComparisonDispersion[]; multiple_testing_trial_count: number;
    }[];
  };
  economics: {
    completeness: "GROSS_ONLY" | "COST_SCENARIO_APPLIED_UNVERIFIED"; study_cost_mode: string;
    verified_costs: "UNAVAILABLE"; net_profitability: "UNAVAILABLE"; reasons: string[];
  };
  cost_fragility?: CostFragility;
  provenance: {
    datasets: { role: string; content_hash: string; evidence_tier: string; tier_claim: string }[];
    baseline_lag_micros: number | null; timing_claim_ceiling: string | null; evaluation_before_holdout: boolean | null;
    evaluation_upper_bound_exclusive?: string | null; holdout_boundary?: string;
  };
  candidate_state: { state: string; evidence_hash: string; reasons: string[] } | null;
  restated_claims: string[];
  authority_reasons: string[];
  promotable: false;
  execution_authority: "NONE";
  sort_state: "VALUE" | "MISSING" | "NOT_NUMERIC" | "UNSORTED";
  sort_value: string | null;
  sort_position: number | null;
  tied_with: number;
};

/**
 * Stored evidence: a loaded row's status verbatim; ``NOT_LOADED`` (nothing stored) -- distinct from a loaded
 * ``INCOMPLETE`` verdict; ``REFUSED`` (a stored row that does not re-derive; reasons ``STORED_EVIDENCE_REFUSED:*``).
 */
export type PaperParityEvidence = {
  status: string; loaded: boolean; reasons: string[]; report_hash: string | null;
  summary: Record<string, unknown>; live_side: { live_signals_recorded?: number; paper_fills_in_cycle?: number;
    fills_by_status?: Record<string, number> };
  assessment_id?: string | null; evidence_as_of?: string | null; recorded_at?: string | null;
  window_start?: string | null; window_end?: string | null;
  claim: "READ_ONLY_PARITY_EVIDENCE_NOT_A_PROMOTION_DECISION";
};

export type PaperFundingEvidence = {
  status: string; loaded: boolean; costable: false; cost_status: "NON_COSTABLE"; charge: "NOT_CALCULATED";
  reasons: string[]; completeness_blockers: string[]; economic_blockers: string[]; evidence_hash: string | null;
  section: Record<string, unknown> | null;
  record_id?: string | null; evidence_as_of?: string | null; recorded_at?: string | null;
  window_start?: string | null; window_end?: string | null; bridge_hashes?: Record<string, string>;
};

/** One cost component of paper_economics_v1, verbatim. A figure exists only when VERIFIED. */
export type EconomicsComponent = {
  status: "VERIFIED" | "PROPOSED_SENSITIVITY_SCENARIO" | "MISSING" | "NON_COSTABLE_EVIDENCE";
  value: string | null; by_scenario: Record<string, { cost_bps_per_side: string; value: string }> | null;
  reasons: string[]; detail: Record<string, unknown>;
};

export type EconomicsSensitivityRow = {
  name: string; status: "PROPOSED_SENSITIVITY_SCENARIO"; source: string; cost_bps_per_side: string;
  rationale: string; value: string; in_net: false; scenario_hash: string;
};

/**
 * Per-candidate paper economics (server-projected from stored fills). Every figure is a return per unit of
 * notional (``units``), never money. NOT_LOADED / REFUSED / PROJECTED are distinct states.
 */
export type PaperEconomicsSection = {
  status: "NOT_LOADED" | "REFUSED" | "PROJECTED";
  reasons: string[];
  units: "RETURN_PER_UNIT_NOTIONAL_SUMMED_NOT_COMPOUNDED_PENDING_OR_11";
  money: "NONE_NOT_A_CURRENCY_AMOUNT";
  sign_convention: string | null; economics_hash: string | null; economics_id: string | null; symbol: string | null;
  cost_policy: { slot: string; mode: string } | null;
  cost_approval: { packet_hash: string | null; authorized_by: string; authorized_on: string } | null;
  exposure: { sizing: string; open_position_signed: number | null; closed_round_trips: number; closed_sides: number } | null;
  gross_realized: { value: string; basis: string } | null;
  gross_unrealized: {
    status: "AVAILABLE" | "UNAVAILABLE" | "NOT_APPLICABLE_FLAT"; value: string | null; reasons: string[];
    exposure: number | null; mark: Record<string, unknown> | null; detail: Record<string, unknown>; in_net: false;
  } | null;
  unrealized_mark_source: string;
  price_resolution?: {
    status: "AVAILABLE" | "UNAVAILABLE"; reasons: string[];
    evidence: Record<string, unknown> | null;
    selection_policy?: { policy_hash: string; installed_at: string; identity: Record<string, unknown> };
  } | null;
  fees: EconomicsComponent | null; slippage: EconomicsComponent | null; funding: EconomicsComponent | null;
  sensitivity_rows: EconomicsSensitivityRow[];
  net: { status: "CONFIRMED" | "NOT_COSTABLE" | "INCOMPLETE"; scope: string; by_scenario: Record<string, string> | null;
    reasons: string[] } | null;
  settlement_boundaries: Record<string, unknown> | null; provenance: Record<string, unknown> | null;
  claim: "PAPER_ECONOMICS_PROJECTION_RESEARCH_ONLY_NOT_VALIDATED";
};

/** An account's authoritative policy (resolve_account_policy_v1). ACTIVE only when the resolver says so. */
export type AccountPolicyResolution = {
  account_id: string; as_of: string; status: "ACTIVE" | "UNRESOLVED" | "BLOCKED"; policy_status: string | null;
  policy_version_id: string | null; policy_content_hash: string | null; policy_recorded_at: string | null;
  approved_by: string | null; approved_on: string | null; account: Record<string, unknown> | null;
  not_applicable_controls: string[]; versions_recorded: number; versions_known_at_as_of: number;
  blocking: string[]; unresolved: string[]; integrity_failures: Record<string, string>[];
  claim: "ACCOUNT_POLICY_RESOLUTION_NEVER_ACTIVATES_AN_ACCOUNT";
};

export type PaperRiskControl = {
  control: string; policy_fields: string[]; status: string; reason: string | null; evidence: Record<string, unknown>;
};

/** A recorded paper target assessed under a stored policy. NOT_LOADED is never a verdict; sizing only when ALLOWED. */
export type PaperRiskView = {
  status: "NOT_LOADED" | "ALLOWED" | "BLOCKED" | "UNRESOLVED"; loaded: boolean; reasons: string[];
  account_id: string | null; account_source: "AUTHORIZED_EXIT_CRITERIA_BINDING" | "OPERATOR_SELECTED_ACCOUNT" | "NONE";
  requested_target: { source: string; signal_id: string; symbol: string; study_id: string; trial_id: string;
    target_from: number; target_to: number; decided_at: string } | null;
  assessed_as_of: string | null; policy_resolution: Record<string, unknown> | null;
  current_policy: AccountPolicyResolution | null; order_action: string | null; controls: PaperRiskControl[];
  blocking_reasons: string[]; unresolved_reasons: string[]; sizing: Record<string, string | null> | null;
  sizing_absent_reasons: string[]; assessment_hash: string | null; assessment_id: string | null;
  execution_authority: false; claim: "PAPER_RISK_ASSESSMENT_NOT_EXECUTION_AUTHORITY";
};

export type AccountRisk = {
  account_id: string; as_of: string; cycle_id: string; current_policy: AccountPolicyResolution;
  candidates: PaperRiskView[]; reasons: string[]; execution_authority: false;
  claim: "PAPER_RISK_ASSESSMENT_NOT_EXECUTION_AUTHORITY";
};

export type CandidatePaperEvidence = {
  study_id: string; study_content_hash: string; trial_id: string; trial_content_hash: string; cycle_id: string;
  symbol: string | null; incubation: Record<string, unknown> | null; parity: PaperParityEvidence;
  funding: PaperFundingEvidence; economics: PaperEconomicsSection; risk: PaperRiskView;
  claim: "PAPER_EVIDENCE_NOT_A_PROMOTION_DECISION"; execution_authority: "NONE";
  validated_claim: "NONE_VALIDATED"; as_of: string;
};

export type TerminalComparison = {
  claim: "RESEARCH_COMPARISON_NON_AUTHORITATIVE";
  composite_score: "NONE";
  holdout: "NOT_READ";
  validated_claim: "NONE_VALIDATED";
  evidence_stages: { stage: string; label: string; claim: string; meaning: string }[];
  cycle: TerminalCycle;
  comparison: {
    schema_version: string; restated_claims: string[]; sort: { dimension: string; metric: string; direction: string } | null;
    tie_break: string; studies: string[]; studies_without_frozen_set: string[]; rows: ComparisonRow[];
    sort_counts: Record<string, number>; cross_study: { differences: string[] }; output_hash: string;
  };
  paper_evidence: CandidatePaperEvidence[];
  as_of: string;
};

export type ComparisonSort = { dimension: "SEARCH" | "DECIMAL_BASELINE"; metric: string; direction: "ASC" | "DESC" };

/** The operator's selection only: studies to compare and, optionally, one explicit sort. No default ranking. */
export function getTerminalComparison(
  ctx: WorkspaceContext, studyIds: string[], sort?: ComparisonSort,
): Promise<EvidenceResult<TerminalComparison>> {
  const query = new URLSearchParams();
  for (const id of studyIds) query.append("study_id", id);
  if (sort) {
    query.set("sort_dimension", sort.dimension);
    query.set("sort_metric", sort.metric);
    query.set("sort_direction", sort.direction);
  }
  return terminal(ctx, authorityUrl(ctx.origin, `/operator-dashboard/research-terminal/compare?${query.toString()}`), "Candidate comparison is unavailable.");
}

export function getTerminalPaperEvidence(ctx: WorkspaceContext, studyId: string, trialId: string): Promise<EvidenceResult<CandidatePaperEvidence>> {
  return terminal(ctx, authorityUrl(ctx.origin, `/operator-dashboard/research-terminal/candidates/${encodeURIComponent(studyId)}/${encodeURIComponent(trialId)}/paper-evidence`), "Paper evidence is unavailable.");
}

export function getStrategyLabStudies(
  ctx: WorkspaceContext,
  filters: { strategyFamily?: string; instrument?: string; limit?: number; offset?: number } = {},
): Promise<EvidenceResult<StrategyLabStudyPage>> {
  const query = new URLSearchParams({ limit: String(filters.limit ?? 50) });
  if (filters.offset) query.set("offset", String(filters.offset));
  if (filters.strategyFamily) query.set("strategy_family", filters.strategyFamily);
  if (filters.instrument) query.set("instrument", filters.instrument);
  return terminal(ctx, authorityUrl(ctx.origin, `/operator-dashboard/strategy-lab/studies?${query.toString()}`), "Strategy Lab studies are unavailable.");
}

export function getStrategyLabStudy(ctx: WorkspaceContext, studyId: string): Promise<EvidenceResult<StrategyLabStudyDetail>> {
  return terminal(ctx, authorityUrl(ctx.origin, `/operator-dashboard/strategy-lab/studies/${encodeURIComponent(studyId)}`), "Strategy Lab study is unavailable.");
}

export function getStrategyLabTrials(
  ctx: WorkspaceContext, studyId: string, params: StrategyLabTrialQuery,
): Promise<EvidenceResult<StrategyLabTrialPage>> {
  const query = new URLSearchParams({
    limit: String(params.limit), offset: String(params.offset), sort: params.sort, direction: params.direction,
    outcome: params.outcome, frozen_only: params.frozenOnly ? "true" : "false",
  });
  return terminal(ctx, authorityUrl(ctx.origin, `/operator-dashboard/strategy-lab/studies/${encodeURIComponent(studyId)}/trials?${query.toString()}`), "Strategy Lab trials are unavailable.");
}

export function getStrategyLabTrial(ctx: WorkspaceContext, studyId: string, trialId: string): Promise<EvidenceResult<StrategyLabTrialDetail>> {
  return terminal(ctx, authorityUrl(ctx.origin, `/operator-dashboard/strategy-lab/studies/${encodeURIComponent(studyId)}/trials/${encodeURIComponent(trialId)}`), "Strategy Lab trial is unavailable.");
}

export function getStrategyLabHeatmap(
  ctx: WorkspaceContext, studyId: string, params: StrategyLabHeatmapQuery,
): Promise<EvidenceResult<StrategyLabHeatmap>> {
  const query = new URLSearchParams();
  if (params.x) query.set("x", params.x);
  if (params.y) query.set("y", params.y);
  if (params.metric) query.set("metric", params.metric);
  for (const [name, value] of params.fixed) query.append("fixed", `${name}:${value}`);
  const text = query.toString();
  return terminal(ctx, authorityUrl(ctx.origin, `/operator-dashboard/strategy-lab/studies/${encodeURIComponent(studyId)}/heatmap${text ? `?${text}` : ""}`), "Strategy Lab heatmap is unavailable.");
}

export function getStrategyLabBreakEvenDistribution(ctx: WorkspaceContext, studyId: string): Promise<EvidenceResult<StrategyLabBreakEvenDistribution>> {
  return terminal(ctx, authorityUrl(ctx.origin, `/operator-dashboard/strategy-lab/studies/${encodeURIComponent(studyId)}/break-even-distribution`), "Break-even distribution is unavailable.");
}

export async function getChartSeriesCatalog(ctx: WorkspaceContext): Promise<EvidenceResult<ChartSeriesRefPage>> {
  return readEvidence<ChartSeriesRefPage>(
    authorityUrl(ctx.origin, "/operator-dashboard/chart-series"),
    ctx.protectedApi,
    "Chart series catalog is unavailable.",
  );
}

export async function getChartSeries(
  ctx: WorkspaceContext,
  params: { manifestHash: string; instrument?: string; maxPoints?: number },
): Promise<EvidenceResult<ChartSeries>> {
  const query = new URLSearchParams({ max_points: String(params.maxPoints ?? 400) });
  if (params.instrument) query.set("instrument", params.instrument);
  return readEvidence<ChartSeries>(
    authorityUrl(ctx.origin, `/operator-dashboard/chart-series/${encodeURIComponent(params.manifestHash)}?${query.toString()}`),
    Boolean(/^[0-9a-f]{64}$/.test(params.manifestHash) && ctx.protectedApi),
    "Chart series reference is unavailable.",
  );
}

export const CHART_TIMEFRAMES = ["1m", "1h", "4h", "1d"] as const;
const UTC_INSTANT_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;

export async function getChartCandles(
  ctx: WorkspaceContext,
  params: { manifestHash: string; timeframe: string; end?: string; limit?: number },
): Promise<EvidenceResult<ChartCandles>> {
  const query = new URLSearchParams({ timeframe: params.timeframe, limit: String(params.limit ?? 2000) });
  const end = params.end && UTC_INSTANT_PATTERN.test(params.end) ? params.end : undefined;
  if (end) query.set("end", end);
  return readEvidence<ChartCandles>(
    authorityUrl(ctx.origin, `/operator-dashboard/chart-series/${encodeURIComponent(params.manifestHash)}/candles?${query.toString()}`),
    Boolean(
      /^[0-9a-f]{64}$/.test(params.manifestHash)
      && (CHART_TIMEFRAMES as readonly string[]).includes(params.timeframe)
      && ctx.protectedApi,
    ),
    "Chart candles are unavailable.",
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
