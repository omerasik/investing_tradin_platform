const baseUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";
const page = await fetch(`${baseUrl}/`);
if (!page.ok) throw new Error(`Dashboard returned ${page.status}`);
const html = await page.text();
for (const required of [
  "Command Center", "PAPER ONLY", "Market Intelligence", "Instrument Workstation",
  "Strategy Laboratory", "Backtest Review", "Risk Center", "Return Data Health", "Investment Workspace", "Audit Center", "Live trading is deliberately unavailable",
]) {
  if (!html.includes(required)) throw new Error(`Dashboard content missing: ${required}`);
}
const proxy = await fetch(`${baseUrl}/api/risk`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
if (proxy.status !== 503) throw new Error(`Expected unconfigured risk proxy to fail closed with 503, received ${proxy.status}`);
const dataHealth = await fetch(`${baseUrl}/api/data-health?target=/data-health/return-ingestion/due`);
if (dataHealth.status !== 503) throw new Error(`Expected unconfigured data-health proxy to fail closed with 503, received ${dataHealth.status}`);
console.log("PASS: dashboard content and fail-closed risk proxy verified.");
