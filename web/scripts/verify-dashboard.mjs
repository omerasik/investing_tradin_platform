const baseUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";
const page = await fetch(`${baseUrl}/`);
if (!page.ok) throw new Error(`Dashboard returned ${page.status}`);
const html = await page.text();
for (const required of [
  "Command Center", "PAPER ONLY", "Instrument Workstation",
  "Data Sources / Providers", "Feature Explorer", "Strategy Laboratory", "Backtest / Validation", "Strategy Scorecard", "Regime Workspace", "Portfolio Construction", "Investment Workspace", "News / Event Intelligence", "Paper OMS", "Operations / SRE", "Audit", "LIVE TRADING: DISABLED", "EXTERNAL_BLOCKED", "Live trading is deliberately unavailable",
]) {
  if (!html.includes(required)) throw new Error(`Dashboard content missing: ${required}`);
}
const proxy = await fetch(`${baseUrl}/api/risk`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
if (![502, 503].includes(proxy.status)) throw new Error(`Expected risk proxy to fail closed with 502/503, received ${proxy.status}`);
const dataHealth = await fetch(`${baseUrl}/api/data-health?target=/data-health/return-ingestion/due`);
if (![502, 503].includes(dataHealth.status)) throw new Error(`Expected data-health proxy to fail closed with 502/503, received ${dataHealth.status}`);
const operator = await fetch(`${baseUrl}/api/operator`);
if (![502, 503].includes(operator.status)) throw new Error(`Expected operator proxy to fail closed with 502/503, received ${operator.status}`);
if (html.includes("Start Live") || html.includes("EXECUTE")) throw new Error("Live-execution UI marker found.");
console.log("PASS: evidence-first dashboard content and fail-closed operator proxies verified.");
