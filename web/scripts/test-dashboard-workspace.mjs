import assert from "node:assert/strict";

import { resolveDashboardWorkspaceReferences } from "../app/dashboard-workspace.ts";

const discovered = {
  state: "AVAILABLE",
  feature_definition_id: "00000000-0000-0000-0000-000000000001",
  feature_instrument: "DEMO:XNAS:DEMO_EQ_A",
  feature_dataset_version: "module1b-demo-evidence-v1",
  feature_decision_time: "2025-02-03T04:00:00Z",
  scorecard_id: "00000000-0000-0000-0000-000000000002",
  regime_run_id: "00000000-0000-0000-0000-000000000003",
  portfolio_construction_run_id: "00000000-0000-0000-0000-000000000004",
  sre_service_version_id: "00000000-0000-0000-0000-000000000005",
  news_instrument: "DEMO:XNAS:DEMO_EQ_A",
  instrument_id: "DEMO:XNAS:DEMO_EQ_A",
  strategy_id: "00000000-0000-0000-0000-000000000006",
  experiment_id: "00000000-0000-0000-0000-000000000007",
  investment_thesis_id: "00000000-0000-0000-0000-000000000008",
  investment_portfolio_id: "demo-investment",
  paper_intent_id: "00000000-0000-0000-0000-000000000009",
  paper_account_id: "demo-paper",
};

const auto = resolveDashboardWorkspaceReferences({ dashboardOrigin: "http://127.0.0.1:3000" }, discovered);
assert.equal(auto.scorecardId, discovered.scorecard_id);
assert.equal(auto.strategyId, discovered.strategy_id);
assert.equal(auto.paperAccountId, "demo-paper");

const configured = resolveDashboardWorkspaceReferences({
  dashboardOrigin: "http://127.0.0.1:3000",
  scorecardId: "11111111-1111-1111-1111-111111111111",
  strategyId: "22222222-2222-2222-2222-222222222222",
  investmentPortfolioId: "reviewed-portfolio",
}, discovered);
assert.equal(configured.scorecardId, "11111111-1111-1111-1111-111111111111");
assert.equal(configured.strategyId, "22222222-2222-2222-2222-222222222222");
assert.equal(configured.investmentPortfolioId, "reviewed-portfolio");

const empty = resolveDashboardWorkspaceReferences({ dashboardOrigin: "http://127.0.0.1:3000" });
assert.equal(empty.scorecardId, undefined);
assert.equal(empty.paperOrderIntentId, undefined);

console.log("PASS: dashboard discovery resolver preserves explicit configuration and empty availability.");
