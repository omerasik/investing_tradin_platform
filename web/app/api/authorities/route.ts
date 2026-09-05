import { NextRequest, NextResponse } from "next/server";

import { loadDashboardConfig, resolveDashboardOperatorToken } from "../../dashboard-config";

const exactUuidPath = /^\/operator-dashboard\/(feature-definitions|strategy-scorecards|regime-runs|portfolio-construction-runs|audit-events|paper-orders)\/[0-9a-fA-F-]{36}$/;
const paperAccountReconciliationPath = /^\/operator-dashboard\/paper-accounts\/[A-Za-z0-9:_-]+\/reconciliation$/;

function allowedTarget(target: string): boolean {
  const parsed = new URL(target, "http://dashboard.local");
  if (parsed.origin !== "http://dashboard.local") return false;
  if (exactUuidPath.test(parsed.pathname) && parsed.search === "") return true;
  if (paperAccountReconciliationPath.test(parsed.pathname) && parsed.search === "") return true;
  if (parsed.pathname === "/operator-dashboard/workspace-references" && parsed.search === "") return true;
  if (parsed.pathname === "/operator-dashboard/instruments") {
    return [...parsed.searchParams.keys()].every((key) => key === "limit" || key === "offset");
  }
  if (parsed.pathname === "/operator-dashboard/paper-orders") {
    const allowed = new Set(["account_id", "instrument", "side", "lifecycle_status", "fill_state", "reconciliation_state", "limit", "offset"]);
    return [...parsed.searchParams.keys()].every((key) => allowed.has(key));
  }
  if (parsed.pathname === "/operator-dashboard/audit-events") {
    const allowed = new Set(["event_type", "actor", "start", "end", "limit", "offset"]);
    return [...parsed.searchParams.keys()].every((key) => allowed.has(key));
  }
  if (parsed.pathname === "/operator-dashboard/investment-theses") {
    const allowed = new Set(["instrument", "status", "review_state", "synthetic_demo", "limit", "offset"]);
    return [...parsed.searchParams.keys()].every((key) => allowed.has(key));
  }
  if (parsed.pathname === "/operator-dashboard/investment-portfolios") {
    const allowed = new Set(["status", "account_id", "limit", "offset"]);
    return [...parsed.searchParams.keys()].every((key) => allowed.has(key));
  }
  if (parsed.pathname === "/operator-dashboard/strategies") {
    return [...parsed.searchParams.keys()].every((key) => key === "family" || key === "limit" || key === "offset");
  }
  if (parsed.pathname === "/operator-dashboard/experiments") {
    return [...parsed.searchParams.keys()].every((key) => key === "strategy_id" || key === "limit" || key === "offset");
  }
  if (parsed.pathname === "/operator-dashboard/strategy-scorecards") {
    return [...parsed.searchParams.keys()].every((key) => key === "strategy_id" || key === "status" || key === "limit" || key === "offset");
  }
  if (parsed.pathname === "/operator-dashboard/feature-materializations") {
    const allowed = new Set(["feature_id", "instrument", "dataset_version", "decision_time", "limit", "offset"]);
    return ["feature_id", "instrument", "dataset_version", "decision_time"].every((key) => parsed.searchParams.has(key))
      && [...parsed.searchParams.keys()].every((key) => allowed.has(key));
  }
  if (parsed.pathname === "/operator-dashboard/signals") {
    const allowed = new Set(["as_of", "status", "instrument", "strategy_version", "limit", "offset"]);
    return parsed.searchParams.has("as_of") && [...parsed.searchParams.keys()].every((key) => allowed.has(key));
  }
  if (parsed.pathname === "/operator-dashboard/risk-decisions") {
    const allowed = new Set(["approved", "account_id", "policy_version_id", "business_date", "has_reservation", "limit", "offset"]);
    return [...parsed.searchParams.keys()].every((key) => allowed.has(key));
  }
  if (parsed.pathname === "/operator-dashboard/regime-runs") {
    const allowed = new Set(["instrument", "status", "model_version_id", "dataset_version", "limit", "offset"]);
    return [...parsed.searchParams.keys()].every((key) => allowed.has(key));
  }
  if (parsed.pathname === "/operator-dashboard/portfolio-construction-runs") {
    const allowed = new Set(["status", "policy_version_id", "regime_run_id", "limit", "offset"]);
    return [...parsed.searchParams.keys()].every((key) => allowed.has(key));
  }
  if (parsed.pathname === "/operator-dashboard/news-events") {
    const allowed = new Set(["instrument", "entity", "category", "start", "end", "correction_state", "limit", "offset"]);
    return [...parsed.searchParams.keys()].every((key) => allowed.has(key));
  }
  if (parsed.pathname === "/operator-dashboard/sre-overview") {
    return [...parsed.searchParams.keys()].every((key) => key === "service_version_id");
  }
  return false;
}

/** GET-only server boundary; the operator token and arbitrary backend paths never reach the browser. */
export async function GET(request: NextRequest) {
  try {
    const target = request.nextUrl.searchParams.get("target");
    if (!target || !allowedTarget(target)) {
      return NextResponse.json({ detail: "Unsupported operator authority target." }, { status: 400 });
    }
    const config = loadDashboardConfig();
    const token = resolveDashboardOperatorToken(config);
    if (!config.apiBaseUrl || !token) {
      return NextResponse.json({ detail: "Server-side authority configuration is unavailable." }, { status: 503 });
    }
    const response = await fetch(`${config.apiBaseUrl}${target}`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "Operator authority backend is unavailable." }, { status: 502 });
  }
}
