import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { allowedAuthorityTarget } from "../app/authority-targets.ts";

const uuid = "00000000-0000-0000-0000-000000000001";

// Every target lib/data-access.ts requests, built the way it builds them. A target
// the proxy refuses renders as UNAVAILABLE, which reads like "no evidence exists".
const requested = [
  "/operator-dashboard/workspace-references",
  "/operator-dashboard/evidence-catalog",
  "/operator-dashboard/capture-availability",
  "/operator-dashboard/instruments?limit=20&offset=0&query=BTC&asset_class=CRYPTO&lifecycle_status=ACTIVE",
  `/operator-dashboard/instruments/${encodeURIComponent("CRYPTO:BYBIT:BTCUSDT:PERP")}`,
  `/operator-dashboard/instruments/${encodeURIComponent("DEMO:XNAS:DEMO_EQ_A")}`,
  "/operator-dashboard/historical-datasets?limit=50&offset=0",
  "/operator-dashboard/data-health/assessments?limit=50&offset=0&scope_type=GLOBAL&blocking=true",
  `/operator-dashboard/data-health/assessments/${uuid}`,
  "/operator-dashboard/feature-definitions?limit=50&offset=0&family=PRICE_RETURNS",
  `/operator-dashboard/feature-definitions/${uuid}`,
  `/operator-dashboard/feature-materializations?feature_id=${uuid}&instrument=x&dataset_version=v&decision_time=2026-01-01T00%3A00%3A00Z`,
  "/operator-dashboard/signals?as_of=2026-01-01T00%3A00%3A00Z&limit=20",
  "/operator-dashboard/risk-decisions?limit=20&offset=0",
  "/operator-dashboard/strategies?limit=20&offset=0",
  "/operator-dashboard/experiments?limit=20&offset=0",
  `/operator-dashboard/strategy-scorecards/${uuid}`,
  "/operator-dashboard/strategy-scorecards?limit=20",
  "/operator-dashboard/regime-runs?limit=20",
  `/operator-dashboard/regime-runs/${uuid}`,
  "/operator-dashboard/portfolio-construction-runs?limit=20",
  `/operator-dashboard/portfolio-construction-runs/${uuid}`,
  "/operator-dashboard/investment-theses?limit=20",
  "/operator-dashboard/investment-portfolios?limit=20",
  "/operator-dashboard/news-events?limit=20",
  `/operator-dashboard/paper-orders/${uuid}`,
  "/operator-dashboard/paper-orders?limit=20",
  "/operator-dashboard/paper-accounts/demo-paper/reconciliation",
  "/operator-dashboard/audit-events?limit=20",
  `/operator-dashboard/audit-events/${uuid}`,
  "/operator-dashboard/sre-overview",
];
for (const target of requested) {
  assert.equal(allowedAuthorityTarget(target), true, `refused: ${target}`);
}

// Every path data-access.ts names is covered by the list above.
const source = readFileSync(new URL("../app/lib/data-access.ts", import.meta.url), "utf8");
const named = [...source.matchAll(/authorityUrl\((?:ctx\.)?origin, [`"](\/operator-dashboard\/[a-z/-]+)/g)].map((m) => m[1]);
assert.ok(named.length > 20, "data-access targets were found");
for (const path of named) {
  assert.ok(
    requested.some((target) => target.startsWith(path)),
    `data-access.ts requests ${path} but this test does not prove the proxy allows it`,
  );
}

const refused = [
  "https://evil.example/operator-dashboard/sre-overview",
  "/operator-dashboard/evidence-catalog?limit=5",
  "/operator-dashboard/historical-datasets?dsn=x",
  "/operator-dashboard/instruments/..%2F..%2Fhealth",
  "/operator-dashboard/instruments/a%2Fb",
  "/operator-dashboard/instruments/..",
  "/operator-dashboard/signals?limit=5",
  "/research/backtests",
  "/paper-oms/orders/x",
  `/operator-dashboard/paper-orders/${uuid}?x=1`,
];
for (const target of refused) {
  assert.equal(allowedAuthorityTarget(target), false, `allowed: ${target}`);
}

console.log(`authority target allowlist: ${requested.length} allowed, ${refused.length} refused`);
