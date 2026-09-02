export type EvidenceStatus = "AVAILABLE" | "EMPTY" | "STALE" | "BLOCKED" | "ERROR" | "UNAVAILABLE" | "EXTERNAL_BLOCKED" | "DISABLED" | "POSTGRES_CONFIGURED" | "SQLITE_NON_PRODUCTION";

export type EvidenceReference = { id: string; kind: string };

export type EvidenceState = {
  id: string;
  status: EvidenceStatus;
  version: string;
  source: string;
  as_of: string;
  freshness: string;
  limitations: string[];
  evidence_references: EvidenceReference[];
  details: Record<string, unknown>;
};

export type CommandCenterEvidence = EvidenceState & {
  platform_mode: string;
  live_trading_enabled: false;
  states: EvidenceState[];
};

export type EvidenceResult<T> =
  | { state: "AVAILABLE"; value: T }
  | { state: "EMPTY"; detail: string }
  | { state: "ERROR"; detail: string }
  | { state: "EXTERNAL_BLOCKED"; detail: string };

export type MetricEvidenceState = "MEASURED" | "ASSUMED" | "UNAVAILABLE";
export type PageInfo = { limit: number; offset: number; returned: number; has_more: boolean };
export type FeatureDefinition = {
  feature_definition_id: string; feature_name: string; family: string; semantic_version: string; status: string;
  required_dataset_types: string[]; required_fields: string[]; frequency: string; timestamp_semantics: string;
  lookback: number; parameters: Record<string, string | number | boolean | null>; missing_value_policy: string;
  outlier_policy: string; leakage_policy: string; units: string; calculation_version: string;
  created_at: string; retired_at: string | null;
};
export type FeatureDefinitionPage = { state: EvidenceStatus; items: FeatureDefinition[]; page: PageInfo };
export type FeatureMaterialization = {
  materialization_id: string; instrument: string; feature_definition_id: string; feature_name: string;
  semantic_version: string; dataset_version: string; event_time: string; effective_time: string;
  knowledge_time: string; computed_time: string; value: string | null; quality_state: string;
  content_hash: string; source_manifest: string[];
};
export type FeatureMaterializationPage = { state: EvidenceStatus; decision_time: string; items: FeatureMaterialization[]; page: PageInfo };

export type IdentifierMapping = {
  mapping_id: string; source_kind: string; namespace: string; value: string;
  valid_from: string; valid_until: string | null; source_reference: string; ingested_at: string;
};
export type SymbolMapping = {
  mapping_id: string; venue: string; symbol: string; valid_from: string;
  valid_until: string | null; source_reference: string; ingested_at: string;
};
export type LifecycleEvent = {
  event_id: string; status: string; effective_at: string; ingested_at: string; reason: string;
};
export type InstrumentDetail = {
  instrument_id: string; canonical_symbol: string; asset_class: string; instrument_type: string;
  exchange_name: string; venue: string; mic: string | null; base_currency: string; quote_currency: string;
  settlement_currency: string; contract_multiplier: string; contract_size: string; tick_size: string;
  lot_size: string; price_precision: number; quantity_precision: number; trading_timezone: string;
  market_session_type: string; representation_kind: string; isin: string | null; cusip: string | null;
  registered_at: string; lifecycle_status: string; synthetic_demo: boolean; ambiguous_mapping: boolean;
  identifier_mappings: IdentifierMapping[]; symbol_mappings: SymbolMapping[];
  lifecycle_events: LifecycleEvent[]; dataset_versions: string[];
};

export type HistoricalDataset = {
  dataset_version_id: string; source_id: string; version: string; normalization_version: string;
  content_hash: string; valid_from: string; valid_until: string | null; created_at: string;
  status: string; provider: string; dataset_name: string; asset_scope: string;
  provider_terms_version: string; authorization_reference: string; authorized_at: string;
  observation_count: number; checkpoint_state: string | null; synthetic_demo: boolean;
};
export type HistoricalDatasetPage = { state: EvidenceStatus; items: HistoricalDataset[]; page: PageInfo };

export type DataHealthFinding = {
  finding_id: string; sequence: number; check_type: string; action: string;
  observed_at: string | null; detail: Record<string, unknown>; content_hash: string;
};
export type DataHealthAssessment = {
  assessment_id: string; dataset_version_id: string | null; dataset_version: string | null;
  scope_type: string; scope_value: string; policy_version: string; evaluated_at: string;
  expected_start: string; expected_end: string; max_action: string; blocking: boolean;
  content_hash: string; summary: Record<string, unknown>; findings: DataHealthFinding[];
  synthetic_demo: boolean;
};
export type DataHealthAssessmentPage = {
  state: EvidenceStatus; overall_state: string; total_assessments: number;
  blocking_count: number; items: DataHealthAssessment[]; page: PageInfo;
};
export type SignalPage = { state: EvidenceStatus; as_of: string; page: PageInfo; items: {
  signal_id: string; instrument: string; strategy_version: string; direction: string; status: string;
  expiry_state: "CURRENT" | "OVERDUE" | "EXPIRED"; created_at: string; expires_at: string;
  strength: string; confidence: string; data_quality_score: string; explanation: string;
  contradicting_evidence: string[]; validation_id: string | null; passed_stages: string[]; failed_stages: string[];
  latest_reason: string; evidence_classification: string; research_or_paper_only: true; automatic_authority: false;
  lifecycle: { event_id: string; from_status: string; to_status: string; actor: string; reason: string;
    evidence_references: string[]; occurred_at: string }[];
}[] };
export type RiskDecisionPage = { state: EvidenceStatus; page: PageInfo; items: {
  risk_decision_id: string; intent_id: string; policy_version_id: string; policy_name: string; policy_version: string;
  policy_content_hash: string; policy_limits: Record<string, unknown>; approved: boolean; reasons: string[];
  decided_at: string; reservation_id: string | null; account_id: string | null; business_date: string | null;
  reserved_notional: string | null; reservation_created_at: string | null; research_or_paper_only: true;
  automatic_authority: false;
}[] };
export type ScorecardMetric = { metric_id: string; family: string; name: string; value: string | null; unit: string; evidence_state: MetricEvidenceState; dimensions: string[]; evidence_reference: string };
export type StrategyScorecard = {
  scorecard_id: string; schema_version: string; strategy_id: string; strategy_version: string; research_run_id: string;
  dataset_version: string; feature_versions: string[]; cost_model_version: string; evaluated_at: string; knowledge_cutoff: string;
  status: string; limitations: string[]; dataset_health_status: string; validation_package_id: string | null;
  validation_package_content_hash: string | null; evidence_classification: string; evidence_manifest_references: string[];
  content_hash: string; groups: { name: string; metrics: ScorecardMetric[] }[];
  complexity_components: { component_id: string; name: string; formula_version: string; value: string | null; rationale: string }[];
};
export type StrategyScorecardDiscovery = {
  scorecard_id: string; strategy_id: string; strategy_version: string; research_run_id: string;
  dataset_version: string; evaluated_at: string; status: string; dataset_health_status: string;
  evidence_classification: string;
};
export type StrategyScorecardDiscoveryPage = { state: EvidenceStatus; items: StrategyScorecardDiscovery[]; page: PageInfo };
export type RegimeRun = {
  regime_assessment_id: string; model_version_id: string; model_version: string; rule_version: string; dataset_version: string;
  instrument: string; as_of_timestamp: string; knowledge_timestamp: string | null; status: string; limitations: string[];
  evidence_hash: string; risk_boundary: string; dimensions: { observation_id: string; event_time: string; method: string;
    dimension: string; evidence_state: string; hard_label: string | null; probabilities: { state: string; probability: string }[];
    uncertainty: string | null; input_materialization_ids: string[]; content_hash: string }[];
  risk_effects: { candidate_id: string; strategy_version_id: string; current_risk_multiplier: string; proposed_risk_multiplier: string;
    preapproved_maximum: string; action: string; status: string; reasons: string[]; automatic_authority: false }[];
};
export type RegimeRunDimensionSummary = {
  dimension: string;
  hard_label: string | null;
  top_probability_state: string | null;
  top_probability: string | null;
  uncertainty: string | null;
};
export type RegimeRunDiscovery = {
  run_id: string; model_version_id: string; model_version: string; rule_version: string; dataset_version: string;
  instrument: string; as_of_timestamp: string; status: string;
  dimension_summary: RegimeRunDimensionSummary[]; uncertainty_summary: string;
};
export type RegimeRunDiscoveryPage = { state: EvidenceStatus; items: RegimeRunDiscovery[]; page: PageInfo };
export type PortfolioConstructionDiscovery = {
  run_id: string; policy_version_id: string; policy_version: string; regime_run_id: string; constructed_at: string;
  status: string; review_only: true; automatic_authority: false; equity: string; target_volatility: string | null;
  portfolio_volatility: string; stressed_volatility: string; risk_gate_approved: boolean;
};
export type PortfolioConstructionDiscoveryPage = { state: EvidenceStatus; items: PortfolioConstructionDiscovery[]; page: PageInfo };
export type PortfolioConstruction = {
  portfolio_construction_run_id: string; policy_version_id: string; policy_version: string; regime_run_id: string;
  constructed_at: string; status: string; review_only: true; automatic_authority: false; equity: string; target_volatility: string | null;
  cash_weight: string; gross_weight: string; net_weight: string; portfolio_volatility: string; stressed_volatility: string;
  risk_gate_approved: boolean; risk_gate_reasons: string[]; limitations: string[]; content_hash: string;
  covariance: { covariance_id: string; dataset_version: string; dataset_content_hash: string; estimation_version: string; observations: number;
    as_of: string; uncertainty: string; correlation_stress: string; source_provider: string; source_terms_version: string;
    provider_backed: boolean; classification: string };
  sleeves: { sleeve_input_id: string; strategy_key: string; requested_allocation: string; review_allocation: string | null;
    effective_notional: string | null; risk_budget: string; capacity_weight: string; liquidity_score: string; drawdown: string;
    regime_current_multiplier: string; regime_proposed_multiplier: string; marginal_risk: string | null; component_risk: string | null;
    adjustment_reasons: string[]; rejected: boolean; rejection_reasons: string[] }[];
  constraints: { constraint_id: string; name: string; state: string; observed: string | null; limit: string | null; reasons: string[] }[];
};
export type NewsEventPage = { state: EvidenceStatus; provider_state: string; page: PageInfo; items: {
  event_id: string; document_revision_id: string; source: string; source_version: string; source_terms_version: string;
  published_at: string; source_updated_at: string; ingested_at: string; correction_or_retraction_at: string | null;
  revision: number; revision_kind: string; headline: string; category: string; novelty: string; credibility: string | null;
  uncertainty: string; urgency: string; horizon: string; assessment_status: string | null; rights_state: string;
  authorization_state: string; provider_activated: boolean; content_fingerprint: string; provenance_reference: string; limitations: string[];
  entities: { entity_link_id: string; instrument: string; method: string; confidence: string; ambiguous: boolean }[];
  correction_chain: { predecessor_id: string; successor_id: string; relation: string }[];
}[] };
export type SreOverview = {
  state: EvidenceStatus; service_version_id: string; subsystem: string; version: string; environment: string; deployment_status: string;
  postgres_state: string; provider_state: string; ingestion_checkpoint_freshness: string; dataset_freshness: string; feature_freshness: string;
  research_job_health: string; signal_freshness: string; risk_status: string; reconciliation_status: string; backup_restore_status: string;
  kill_switch_state: string; dependencies: { dependency: string; status: string; checked_at: string; latency_ms: string | null; reason: string | null }[];
  slos: { slo_policy_version_id: string; name: string; indicator: string; target: string; target_state: "TARGET"; window_seconds: number;
    measured_value: string | null; measured_state: string; window_start: string | null; window_end: string | null; claim_status: string | null }[];
  incidents: { incident_id: string; severity: string; subsystem: string; opened_at: string; acknowledged_at: string | null;
    resolved_at: string | null; status: string; reason: string; evidence_reference: string }[];
  failure_drills: { drill_run_id: string; scenario: string; expected_protection: string; observed_protection: string; completed_at: string; passed: boolean; evidence_reference: string }[];
};

export async function readEvidence<T>(url: string, configured: boolean, missingDetail: string): Promise<EvidenceResult<T>> {
  if (!configured) return { state: "EXTERNAL_BLOCKED", detail: missingDetail };
  try {
    const dashboardViewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN;
    const response = await fetch(url, {
      cache: "no-store",
      headers: dashboardViewToken ? { Authorization: `Bearer ${dashboardViewToken}` } : undefined,
    });
    if (response.ok) return { state: "AVAILABLE", value: await response.json() as T };
    if (response.status === 404) return { state: "EMPTY", detail: "No durable evidence matched this configured reference." };
    return { state: "ERROR", detail: `Evidence source responded ${response.status}.` };
  } catch {
    return { state: "ERROR", detail: "Evidence source could not be reached." };
  }
}
