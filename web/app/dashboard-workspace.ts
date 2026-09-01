import type { DashboardConfig } from "./dashboard-config";

export type DiscoveredWorkspaceReferences = {
  state: "AVAILABLE" | "UNAVAILABLE";
  feature_definition_id: string | null;
  feature_instrument: string | null;
  feature_dataset_version: string | null;
  feature_decision_time: string | null;
  scorecard_id: string | null;
  regime_run_id: string | null;
  portfolio_construction_run_id: string | null;
  sre_service_version_id: string | null;
  news_instrument: string | null;
  instrument_id: string | null;
  strategy_id: string | null;
  experiment_id: string | null;
  investment_thesis_id: string | null;
  investment_portfolio_id: string | null;
  paper_intent_id: string | null;
  paper_account_id: string | null;
};

export type ResolvedDashboardWorkspace = {
  featureDefinitionId?: string; featureInstrument?: string; featureDatasetVersion?: string; featureDecisionTime?: string;
  scorecardId?: string; regimeRunId?: string; portfolioConstructionRunId?: string; sreServiceVersionId?: string; newsInstrument?: string;
  instrumentId?: string; strategyId?: string; experimentId?: string; investmentThesisId?: string; investmentPortfolioId?: string; paperOrderIntentId?: string; paperAccountId?: string;
};

/** Explicit deployment config always wins. Discovery is safe to use on an empty DB. */
export function resolveDashboardWorkspaceReferences(config: DashboardConfig, discovered?: DiscoveredWorkspaceReferences): ResolvedDashboardWorkspace {
  return {
    featureDefinitionId: config.featureDefinitionId ?? discovered?.feature_definition_id ?? undefined,
    featureInstrument: config.featureInstrument ?? discovered?.feature_instrument ?? undefined,
    featureDatasetVersion: config.featureDatasetVersion ?? discovered?.feature_dataset_version ?? undefined,
    featureDecisionTime: config.featureDecisionTime ?? discovered?.feature_decision_time ?? undefined,
    scorecardId: config.scorecardId ?? discovered?.scorecard_id ?? undefined,
    regimeRunId: config.regimeRunId ?? discovered?.regime_run_id ?? undefined,
    portfolioConstructionRunId: config.portfolioConstructionRunId ?? discovered?.portfolio_construction_run_id ?? undefined,
    sreServiceVersionId: config.sreServiceVersionId ?? discovered?.sre_service_version_id ?? undefined,
    newsInstrument: config.newsInstrument ?? discovered?.news_instrument ?? undefined,
    instrumentId: discovered?.instrument_id ?? undefined,
    strategyId: config.strategyId ?? discovered?.strategy_id ?? undefined,
    experimentId: config.experimentId ?? discovered?.experiment_id ?? undefined,
    investmentThesisId: config.investmentThesisId ?? discovered?.investment_thesis_id ?? undefined,
    investmentPortfolioId: config.investmentPortfolioId ?? discovered?.investment_portfolio_id ?? undefined,
    paperOrderIntentId: config.paperOrderIntentId ?? discovered?.paper_intent_id ?? undefined,
    paperAccountId: config.paperAccountId ?? discovered?.paper_account_id ?? undefined,
  };
}
