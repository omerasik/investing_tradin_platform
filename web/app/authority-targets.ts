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

const parameterlessTargets = new Set([
  "/operator-dashboard/workspace-references",
  "/operator-dashboard/evidence-catalog",
  "/operator-dashboard/capture-availability",
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
};

export function allowedAuthorityTarget(target: string): boolean {
  const parsed = new URL(target, "http://dashboard.local");
  if (parsed.origin !== "http://dashboard.local") return false;
  const bare = parsed.search === "";
  if (bare && parameterlessTargets.has(parsed.pathname)) return true;
  if (bare && exactUuidPath.test(parsed.pathname)) return true;
  if (bare && paperAccountReconciliationPath.test(parsed.pathname)) return true;
  if (bare && instrumentDetailPath.test(parsed.pathname)) return true;
  const rule = queryTargets[parsed.pathname];
  if (!rule) return false;
  const allowed = new Set(rule.allowed);
  return (rule.required ?? []).every((key) => parsed.searchParams.has(key))
    && [...parsed.searchParams.keys()].every((key) => allowed.has(key));
}
