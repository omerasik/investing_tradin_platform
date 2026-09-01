const baseUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";
const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN;
if (!viewToken) throw new Error("TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN is required");
const headers = { Authorization: `Bearer ${viewToken}` };
const page = await fetch(`${baseUrl}/`, { headers });
if (!page.ok) throw new Error(`Dashboard returned ${page.status}`);
const policy = page.headers.get("content-security-policy") ?? "";
if (policy.includes("'unsafe-inline'")) throw new Error("Dashboard CSP permits unsafe inline content");
if (!/script-src 'self' 'nonce-[a-f0-9]{32}' 'strict-dynamic'/.test(policy)) throw new Error("Dashboard CSP is missing a per-request script nonce");
if (!/style-src 'self' 'nonce-[a-f0-9]{32}'/.test(policy)) throw new Error("Dashboard CSP is missing a per-request style nonce");
const unauthenticated = await fetch(`${baseUrl}/`, { redirect: "manual" });
const isRedirectToLogin = [302, 307, 308].includes(unauthenticated.status) && (unauthenticated.headers.get("location")?.includes("/login") ?? false);
const deniedPolicy = unauthenticated.headers.get("content-security-policy") ?? "";
if (!isRedirectToLogin || deniedPolicy.includes("'unsafe-inline'") || !deniedPolicy.includes("'nonce-")) {
  throw new Error(`Dashboard unauthenticated navigation did not redirect to /login with strict CSP (status: ${unauthenticated.status})`);
}
const unauthenticatedApi = await fetch(`${baseUrl}/api/operator`);
if (unauthenticatedApi.status !== 401) {
  throw new Error(`Expected unauthenticated API request to return 401, received ${unauthenticatedApi.status}`);
}
const html = await page.text();
for (const required of [
  "Command Center", "PAPER ONLY", "Instrument Workstation",
  "Market Overview & Providers", "Feature Authority", "Signal Explorer", "Strategy Laboratory", "Backtest / Validation", "Strategy Scorecard V2", "Regime Workspace", "Portfolio Construction", "Investment Workspace", "News / Event Intelligence", "Paper OMS", "Operations / SRE", "Audit", "LIVE TRADING: DISABLED", "EXTERNAL_BLOCKED", "Live trading is deliberately unavailable",
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
