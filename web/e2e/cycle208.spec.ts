import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync, writeFileSync } from "node:fs";

const apiUrl = process.env.CYCLE208_API_URL ?? "http://127.0.0.1:8766";
const dashboardUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";
const configPath = process.env.TRADE_PLATFORM_DASHBOARD_CONFIG_PATH;

test.skip(!configPath, "Configured disposable PostgreSQL dashboard evidence is required");

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();
});

test("authenticated command center and disabled execution boundary render", async ({ page, request }) => {
  await expect(page.getByText("LIVE TRADING: DISABLED", { exact: true })).toBeVisible();
  await expect(page.getByRole("main")).toContainText("POSTGRES_CONFIGURED");
  const unauthenticated = await request.get(`${apiUrl}/operator-dashboard/command-center`);
  expect(unauthenticated.status()).toBe(401);
  const unauthenticatedPage = await fetch(dashboardUrl, { redirect: "manual" });
  expect([302, 307, 308]).toContain(unauthenticatedPage.status);
  expect(unauthenticatedPage.headers.get("location")).toContain("/login");
  const body = await page.locator("body").innerText();
  expect(body).not.toContain("fixture-token");
  expect(body).not.toContain("fixture-view-token");
  expect(body).not.toContain("postgresql://");
});

test("configured dashboard headers and WCAG A/AA scan pass", async ({ page }) => {
  const response = await page.goto("/");
  expect(response?.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response?.headers()["x-frame-options"]).toBe("DENY");
  const policy = response?.headers()["content-security-policy"] ?? "";
  expect(policy).toContain("frame-ancestors 'none'");
  expect(policy).not.toContain("'unsafe-inline'");
  expect(policy).toContain("'strict-dynamic'");
  const nonce = policy.match(/'nonce-([a-f0-9]{32})'/)?.[1];
  expect(nonce).toBeTruthy();
  const scriptNonces = await page.locator("script").evaluateAll((scripts) =>
    // Browsers deliberately hide nonce values from getAttribute("nonce"); the
    // reflected nonce property is the standards-defined inspection surface.
    scripts.map((script) => script.nonce),
  );
  expect(scriptNonces.length).toBeGreaterThan(0);
  expect(scriptNonces.every((value) => value === nonce)).toBe(true);
  const second = await page.request.get("/");
  const secondNonce = second.headers()["content-security-policy"]
    ?.match(/'nonce-([a-f0-9]{32})'/)?.[1];
  expect(secondNonce).toBeTruthy();
  expect(secondNonce).not.toBe(nonce);
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(results.violations).toEqual([]);
});

test("Feature Authority exposes versioned PIT provenance and unavailable is not zero", async ({ page }) => {
  const workspace = page.locator("#features");
  await expect(workspace.getByRole("heading", { name: "Feature Authority" })).toBeVisible();
  await expect(workspace).toContainText("cycle208_simple_return / 1.0.0");
  await expect(workspace).toContainText("PIT event/effective/knowledge");
  await expect(workspace).toContainText("raw:revision");
  await expect(workspace).toContainText("0.02");
  expect((await workspace.getByText(/UNAVAILABLE/).allTextContents()).join(" ")).not.toContain("UNAVAILABLE: 0");
});

test("Signal Explorer exposes reasons and expiry without execution controls", async ({ page }) => {
  const workspace = page.locator("#signals");
  await expect(workspace.getByRole("heading", { name: "Signal Explorer" })).toBeVisible();
  await expect(workspace).toContainText("all_validation_stages_passed");
  await expect(workspace).toContainText("RESEARCH / PAPER ONLY / READ ONLY / NO AUTOMATIC AUTHORITY");
  await expect(workspace.getByRole("button")).toHaveCount(0);
  await expect(workspace).not.toContainText("Execute trade");
  await expect(workspace).not.toContainText("Activate strategy");
});

test("Risk Workspace remains evidence-only without override or execution controls", async ({ page }) => {
  const workspace = page.locator("#risk");
  await expect(workspace.getByRole("heading", { name: "Risk Workspace" })).toBeVisible();
  await expect(workspace).toContainText("NO RISK OVERRIDE");
  await expect(workspace.getByRole("button")).toHaveCount(0);
  await expect(workspace).not.toContainText("Override risk");
  await expect(workspace).not.toContainText("Execute trade");
  await expect(workspace).not.toContainText("Release reservation");
});

test("scorecard groups preserve synthetic and metric evidence distinctions", async ({ page }) => {
  const workspace = page.locator("#scorecard");
  for (const group of ["PERFORMANCE", "ROBUSTNESS", "EXECUTION", "RISK", "DATA_QUALITY", "COMPLEXITY"]) {
    await expect(workspace.getByRole("heading", { name: group, exact: true })).toBeVisible();
  }
  await expect(workspace).toContainText("SYNTHETIC_ENGINEERING_EVIDENCE_ONLY");
  await expect(workspace).toContainText("MEASURED");
  await expect(workspace).toContainText("ASSUMED");
  await expect(workspace).toContainText("UNAVAILABLE");
  await expect(workspace).not.toContainText("proven alpha");
});

test("regime probabilities and uncertainty render without risk-increase control", async ({ page }) => {
  const workspace = page.locator("#regime");
  await expect(workspace).toContainText("Uncertainty");
  await expect(workspace).toContainText("REGIME CANNOT INCREASE GLOBAL RISK LIMITS");
  await expect(workspace.getByRole("button")).toHaveCount(0);
  await expect(workspace).not.toContainText("Increase risk");

  // Module 2B-3: the dashboard card is a concise summary with a link out; the full
  // per-state probability breakdown it used to inline now lives on /regimes instead.
  await workspace.getByRole("link", { name: "Open Regime Workspace" }).click();
  await expect(page).toHaveURL(/\/regimes/);
  await expect(page.getByText("BULL_TREND", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("0.8", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("REGIME CANNOT INCREASE GLOBAL RISK LIMITS").first()).toBeVisible();
  await expect(page.getByRole("button", { name: /increase.*risk/i })).toHaveCount(0);
});

test("portfolio allocations and reductions are review-only with no execution action", async ({ page }) => {
  const workspace = page.locator("#portfolio");
  await expect(workspace).toContainText("REVIEW ONLY / NO EXECUTION ACTION");
  await expect(workspace.getByRole("button")).toHaveCount(0);
  await expect(workspace).not.toContainText("Apply portfolio");
  await expect(workspace).not.toContainText("Execute trade");

  // Module 2B-3: the dashboard card is a concise summary with a link out; the detailed
  // requested/review allocation, reduction reasons, and covariance evidence it used to
  // inline now live on the dedicated /portfolio workspace instead.
  await workspace.getByRole("link", { name: "Open Portfolio Workspace" }).click();
  await expect(page).toHaveURL(/\/portfolio/);
  await expect(page.getByText("capacity_reduction")).toBeVisible();
  await expect(page.getByText("regime_reduction")).toBeVisible();
  await expect(page.getByText("NO_REAL_PROVIDER_BACKED_COVARIANCE_EVIDENCE", { exact: false })).toBeVisible();
  await expect(page.getByText("REVIEW ONLY", { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /apply.*portfolio|execute|rebalance/i })).toHaveCount(0);
});

test("news correction evidence remains externally blocked and never appears live", async ({ page }) => {
  const workspace = page.locator("#news");
  await expect(workspace).toContainText("EXTERNAL_BLOCKED");
  await expect(workspace).toContainText("NOT LIVE NEWS");
  await expect(workspace.getByRole("button")).toHaveCount(0);
  await expect(workspace).not.toContainText("Execute trade");

  // Module 2B-4: the dashboard card is a concise summary with a link out; the full
  // revision chain (RETRACTION #1 / RETRACTS) it used to inline now lives on the
  // dedicated /news workspace instead.
  await workspace.getByRole("link", { name: "Open News Intelligence" }).click();
  await expect(page).toHaveURL(/\/news/);
  // Scoped to the inspector aside so the correction-state filter's own "Retraction"
  // <option> (present in the DOM but not visible while the <select> is closed) is
  // never the match.
  const newsInspector = page.getByLabel("News Event Inspector");
  await expect(newsInspector.getByText("RETRACTION", { exact: false }).first()).toBeVisible();
  await expect(newsInspector.getByText("RETRACTS", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("NOT LIVE NEWS").first()).toBeVisible();
  await expect(page.getByRole("button", { name: /execute|trade|buy|sell|order/i })).toHaveCount(0);
});

test("SRE incident, reconciliation, and TARGET versus MEASURED evidence render", async ({ page }) => {
  // Module 2B-5: the dashboard's Operations card was intentionally trimmed to a concise
  // summary (PostgreSQL, service health, active incident count, kill switch); the detailed
  // SLO target/measured table, incident ledger, and reconciliation/backup evidence checked
  // here now live on the dedicated /operations workspace instead.
  const summary = page.locator("#operations");
  await expect(summary).toContainText("PostgreSQL");
  await expect(summary).toContainText("Kill Switch");

  await summary.getByRole("link", { name: "Open Operations" }).click();
  await expect(page).toHaveURL(/\/operations/);
  const workspace = page.locator("main");
  await expect(workspace.getByRole("columnheader", { name: "TARGET" })).toBeVisible();
  await expect(workspace.getByRole("columnheader", { name: "MEASURED" })).toBeVisible();
  await expect(workspace).toContainText("0.99");
  await expect(workspace).toContainText("0.98");
  await expect(workspace).toContainText("DECLARED");
  await expect(workspace).toContainText("UNAVAILABLE");
  await expect(workspace).toContainText("PASSED");
});

test("authority payload and DOM do not disclose credentials", async ({ page, request }) => {
  const config = configPath ? JSON.parse(readFileSync(configPath, "utf8")) as Record<string, string> : undefined;
  test.skip(!config, "Configured PostgreSQL browser fixture is required");
  if (!config) return;
  const response = await request.get(`${apiUrl}/operator-dashboard/sre-overview?service_version_id=${config.sre_service_version_id}`, {
    headers: { Authorization: "Bearer fixture-token" },
  });
  expect(response.status()).toBe(200);
  const payload = (await response.text()).toLowerCase();
  expect(payload).not.toContain("fixture-token");
  expect(payload).not.toContain("postgresql://");
  expect(payload).not.toContain("password");
  expect((await page.locator("html").innerHTML()).toLowerCase()).not.toContain("fixture-token");
});

test("backend outage renders a safe ERROR state", async ({ page }) => {
  test.skip(!configPath, "Mutable disposable dashboard config is required");
  const original = readFileSync(configPath!, "utf8");
  const config = JSON.parse(original) as Record<string, string>;
  try {
    writeFileSync(configPath!, JSON.stringify({ ...config, api_base_url: "http://127.0.0.1:1" }));
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();
    await expect(page.locator("#scorecard")).toContainText("ERROR");
    await expect(page.getByText("LIVE TRADING: DISABLED", { exact: true })).toBeVisible();
  } finally {
    writeFileSync(configPath!, original);
  }
});

test("invalid object ID produces a controlled empty state without client crash", async ({ page }) => {
  test.skip(!configPath, "Mutable disposable dashboard config is required");
  const original = readFileSync(configPath!, "utf8");
  const config = JSON.parse(original) as Record<string, string>;
  try {
    writeFileSync(configPath!, JSON.stringify({ ...config, scorecard_id: "00000000-0000-0000-0000-000000000000" }));
    const response = await page.goto("/");
    expect(response?.status()).toBe(200);
    await expect(page.locator("#scorecard")).toContainText("EMPTY: No durable evidence matched this configured reference.");
    await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();
  } finally {
    writeFileSync(configPath!, original);
  }
});
