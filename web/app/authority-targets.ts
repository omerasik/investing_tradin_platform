/**
 * The closed set of backend read targets the dashboard may proxy. GET-only, exact
 * paths, named query keys; anything else is refused before a token is attached.
 *
 * Every target `lib/data-access.ts` requests must be listed here -- a target the
 * proxy refuses renders as "UNAVAILABLE", which reads like "no evidence exists".
 */

const exactUuidPath = /^\/operator-dashboard\/(feature-definitions|strategy-scorecards|regime-runs|portfolio-construction-runs|audit-events|paper-orders|data-health\/assessments)\/[0-9a-fA-F-]{36}$/;
const paperAccountReconciliationPath = /^\/operator-dashboard\/paper-accounts\/[A-Za-z0-9:_-]+\/reconciliation$/;
// Canonical instrument ids contain ":" and arrive percent-encoded ("US%3AXNYS%3AC208").
// Only the ":" escape is accepted, and never "..", so no encoded "/" can reach the backend.
const instrumentDetailPath = /^\/operator-dashboard\/instruments\/(?:[A-Za-z0-9_-]|\.(?!\.)|:|%3[Aa]){1,240}$/;

const chartSeriesPath = /^\/operator-dashboard\/chart-series\/[0-9a-f]{64}$/;
const chartCandlesPath = /^\/operator-dashboard\/chart-series\/[0-9a-f]{64}\/candles$/;

const studyUuid = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const studyDetailPath = new RegExp(`^/operator-dashboard/strategy-lab/studies/${studyUuid}$`);
const studyTrialPath = new RegExp(`^/operator-dashboard/strategy-lab/studies/${studyUuid}/trials/${studyUuid}$`);
const studyTrialsPath = new RegExp(`^/operator-dashboard/strategy-lab/studies/${studyUuid}/trials$`);
const studyTrialsKeys = new Set(["limit", "offset", "sort", "direction", "outcome", "frozen_only"]);
const studyHeatmapPath = new RegExp(`^/operator-dashboard/strategy-lab/studies/${studyUuid}/heatmap$`);
const studyHeatmapKeys = new Set(["x", "y", "metric", "fixed"]);
const studyBreakEvenPath = new RegExp(`^/operator-dashboard/strategy-lab/studies/${studyUuid}/break-even-distribution$`);
const terminalWalkForwardPath = /^\/operator-dashboard\/research-terminal\/walk-forward\/[0-9a-f]{64}$/;
const terminalCandidatePath = new RegExp(`^/operator-dashboard/research-terminal/candidates/${studyUuid}/${studyUuid}$`);
const terminalPaperEvidencePath = new RegExp(`^/operator-dashboard/research-terminal/candidates/${studyUuid}/${studyUuid}/paper-evidence$`);

const parameterlessTargets = new Set([
  "/operator-dashboard/workspace-references",
  "/operator-dashboard/evidence-catalog",
  "/operator-dashboard/capture-availability",
  "/operator-dashboard/chart-series",
  "/operator-dashboard/research-terminal/overview",
  "/operator-dashboard/research-terminal/accounts",
  "/operator-dashboard/research-terminal/incubation",
  "/operator-dashboard/research-terminal/activation",
]);

const queryTargets: Record<string, { allowed: string[]; required?: string[] }> = {
  "/operator-dashboard/instruments": { allowed: ["query", "asset_class", "lifecycle_status", "limit", "offset"] },
  "/operator-dashboard/historical-datasets": { allowed: ["limit", "offset"] },
  "/operator-dashboard/data-health/assessments": {
    allowed: ["scope_type", "scope_value", "blocking", "max_action", "limit", "offset"],
  },
  "/operator-dashboard/feature-definitions": { allowed: ["family", "limit", "offset"] },
  "/operator-dashboard/paper-orders": {
    allowed: ["account_id", "instrument", "side", "lifecycle_status", "fill_state", "reconciliation_state", "limit", "offset"],
  },
  "/operator-dashboard/audit-events": { allowed: ["event_type", "actor", "start", "end", "limit", "offset"] },
  "/operator-dashboard/investment-theses": {
    allowed: ["instrument", "status", "review_state", "synthetic_demo", "limit", "offset"],
  },
  "/operator-dashboard/investment-portfolios": { allowed: ["status", "account_id", "limit", "offset"] },
  "/operator-dashboard/strategies": { allowed: ["family", "limit", "offset"] },
  "/operator-dashboard/experiments": { allowed: ["strategy_id", "limit", "offset"] },
  "/operator-dashboard/strategy-scorecards": { allowed: ["strategy_id", "status", "limit", "offset"] },
  "/operator-dashboard/feature-materializations": {
    allowed: ["feature_id", "instrument", "dataset_version", "decision_time", "limit", "offset"],
    required: ["feature_id", "instrument", "dataset_version", "decision_time"],
  },
  "/operator-dashboard/signals": {
    allowed: ["as_of", "status", "instrument", "strategy_version", "limit", "offset"],
    required: ["as_of"],
  },
  "/operator-dashboard/risk-decisions": {
    allowed: ["approved", "account_id", "policy_version_id", "business_date", "has_reservation", "limit", "offset"],
  },
  "/operator-dashboard/regime-runs": {
    allowed: ["instrument", "status", "model_version_id", "dataset_version", "limit", "offset"],
  },
  "/operator-dashboard/portfolio-construction-runs": {
    allowed: ["status", "policy_version_id", "regime_run_id", "limit", "offset"],
  },
  "/operator-dashboard/news-events": {
    allowed: ["instrument", "entity", "category", "start", "end", "correction_state", "limit", "offset"],
  },
  "/operator-dashboard/sre-overview": { allowed: ["service_version_id"] },
  "/operator-dashboard/strategy-lab/studies": { allowed: ["limit", "offset", "strategy_family", "instrument"] },
  "/operator-dashboard/research-terminal/reruns": { allowed: ["limit"] },
  "/operator-dashboard/research-terminal/validation": { allowed: ["limit"] },
  "/operator-dashboard/research-terminal/walk-forward": { allowed: ["limit", "study_id"] },
  "/operator-dashboard/research-terminal/candidates": { allowed: ["limit"] },
  "/operator-dashboard/research-terminal/compare": {
    allowed: ["study_id", "sort_dimension", "sort_metric", "sort_direction"],
    required: ["study_id"],
  },
  "/operator-dashboard/research-terminal/signals": { allowed: ["limit"] },
  "/operator-dashboard/research-terminal/commands": { allowed: ["limit"] },
  "/operator-dashboard/research-terminal/paper-account": { allowed: ["account_id"], required: ["account_id"] },
};

export function allowedAuthorityTarget(target: string): boolean {
  const parsed = new URL(target, "http://dashboard.local");
  if (parsed.origin !== "http://dashboard.local") return false;
  const bare = parsed.search === "";
  if (bare && parameterlessTargets.has(parsed.pathname)) return true;
  if (bare && exactUuidPath.test(parsed.pathname)) return true;
  if (bare && paperAccountReconciliationPath.test(parsed.pathname)) return true;
  if (bare && instrumentDetailPath.test(parsed.pathname)) return true;
  if (bare && (studyDetailPath.test(parsed.pathname) || studyTrialPath.test(parsed.pathname))) return true;
  if (bare && studyBreakEvenPath.test(parsed.pathname)) return true;
  if (bare && (terminalWalkForwardPath.test(parsed.pathname) || terminalCandidatePath.test(parsed.pathname))) return true;
  if (bare && terminalPaperEvidencePath.test(parsed.pathname)) return true;
  if (studyTrialsPath.test(parsed.pathname)) {
    return [...parsed.searchParams.keys()].every((key) => studyTrialsKeys.has(key));
  }
  if (studyHeatmapPath.test(parsed.pathname)) {
    return [...parsed.searchParams.keys()].every((key) => studyHeatmapKeys.has(key));
  }
  if (chartSeriesPath.test(parsed.pathname)) {
    return [...parsed.searchParams.keys()].every((key) => key === "instrument" || key === "max_points");
  }
  if (chartCandlesPath.test(parsed.pathname)) {
    return parsed.searchParams.has("timeframe")
      && [...parsed.searchParams.keys()].every((key) => key === "timeframe" || key === "end" || key === "limit");
  }
  const rule = queryTargets[parsed.pathname];
  if (!rule) return false;
  const allowed = new Set(rule.allowed);
  return (rule.required ?? []).every((key) => parsed.searchParams.has(key))
    && [...parsed.searchParams.keys()].every((key) => allowed.has(key));
}
