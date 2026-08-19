const baseUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";
const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN;
if (!viewToken) throw new Error("TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN is required");
const headers = { Authorization: `Bearer ${viewToken}` };
const page = await fetch(`${baseUrl}/`, { headers });
if (!page.ok) throw new Error(`Dashboard returned ${page.status}`);
const html = await page.text();
for (const required of [
  "Command Center", "PAPER ONLY", "Instrument Workstation",
  "Data Sources / Providers", "Feature Authority", "Signal Explorer", "Strategy Laboratory", "Backtest / Validation", "Strategy Scorecard V2", "Regime Workspace", "Portfolio Construction", "Investment Workspace", "News / Event Intelligence", "Paper OMS", "Operations / SRE", "Audit", "LIVE TRADING: DISABLED", "EXTERNAL_BLOCKED", "Live trading is deliberately unavailable",
]) {
  if (!html.includes(required)) throw new Error(`Dashboard content missing: ${required}`);
}
const proxy = await fetch(`${baseUrl}/api/risk`, { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: "{}" });
if (![502, 503].includes(proxy.status)) throw new Error(`Expected risk proxy to fail closed with 502/503, received ${proxy.status}`);
const dataHealth = await fetch(`${baseUrl}/api/data-health?target=/data-health/return-ingestion/due`, { headers });
if (![502, 503].includes(dataHealth.status)) throw new Error(`Expected data-health proxy to fail closed with 502/503, received ${dataHealth.status}`);
const operator = await fetch(`${baseUrl}/api/operator`, { headers });
if (![502, 503].includes(operator.status)) throw new Error(`Expected operator proxy to fail closed with 502/503, received ${operator.status}`);
const authorities = await fetch(`${baseUrl}/api/authorities?target=${encodeURIComponent("/operator-dashboard/sre-overview")}`, { headers });
if (![502, 503].includes(authorities.status)) throw new Error(`Expected authority proxy to fail closed with 502/503, received ${authorities.status}`);
if (html.includes("Start Live") || html.includes("EXECUTE")) throw new Error("Live-execution UI marker found.");
console.log("PASS: evidence-first dashboard content and fail-closed operator proxies verified.");
