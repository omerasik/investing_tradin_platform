import { expect, test } from "@playwright/test";

const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN ?? "module1b-view-token";

test("Module 1B synthetic demo auto-discovers every read-only workspace", async ({ browser }) => {
  const context = await browser.newContext({
    baseURL: process.env.DASHBOARD_URL ?? "http://127.0.0.1:3001",
    extraHTTPHeaders: {},
  });
  const page = await context.newPage();

  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Operator Access Credential").fill(viewToken);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();
  await expect(page.getByText("LIVE TRADING: DISABLED", { exact: true })).toBeVisible();

  for (const [selector, label] of [
    ["#instrument", "AVAILABLE"], ["#features", "demo_"], ["#strategy", "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY"],
    ["#backtest", "module1b-demo-evidence-v1"], ["#scorecard", "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY"],
    ["#regime", "UPTREND"], ["#portfolio", "REVIEW ELIGIBLE"], ["#investment", "SYNTHETIC / DEMO"],
    ["#news", "Demo issuer retracts fictional guidance"], ["#signals", "DEMO:XNAS:DEMO_EQ_A"],
    ["#risk", "APPROVED"], ["#paper-oms", "PAPER ONLY"], ["#operations", "PostgreSQL"],
  ] as const) {
    await expect(page.locator(selector)).toContainText(label);
  }

  await expect(page.locator("#news")).toContainText("NOT LIVE NEWS");
  await expect(page.locator("#investment")).toContainText("NOT A REAL INVESTMENT RECOMMENDATION");
  await expect(page.locator("#data-sources")).toContainText("EXTERNAL_BLOCKED");
  await expect(page.getByRole("button", { name: /execute|submit|buy|sell/i })).toHaveCount(0);

  // The Instrument card is a bounded preview: the first 20 canonical instruments by
  // symbol, unfiltered. A specific demo row appearing there is alphabetical luck, not
  // a guarantee -- onboarding the real Bybit BTCUSDT perpetual is what pushed
  // DEMO_EQ_A off it. So this spec asserts only what the card actually promises, that
  // it resolves; that the demo instrument stays discoverable by an explicit search is
  // asserted deterministically in tests/test_module1b_demo_acceptance.py instead of
  // depending on where it happens to land in an alphabetical preview.

  // Module 2B-5: the dashboard's Operations card was intentionally trimmed to a concise
  // summary (PostgreSQL, service health, active incident count, kill switch); detailed
  // dependency/SLO-target-vs-measured/incident/drill evidence now lives on /operations.
  await page.locator("#operations").getByRole("link", { name: "Open Operations" }).click();
  await expect(page).toHaveURL(/\/operations/);
  await expect(page.getByText("postgres-availability")).toBeVisible();
  await expect(page.getByText("TARGET ≠ MEASURED").first()).toBeVisible();
  await page.goto("/dashboard");

  // Module 2B-3: the dashboard's Portfolio Construction card was intentionally trimmed to a
  // concise summary with a link out; the detailed constraint-reduction evidence it used to
  // inline now lives on the dedicated /portfolio workspace instead.
  await page.getByRole("link", { name: "Open Portfolio Workspace" }).click();
  await expect(page).toHaveURL(/\/portfolio/);
  await expect(page.getByText("reduced_to_review_limit")).toBeVisible();
  await expect(page.getByRole("button", { name: /execute|submit|buy|sell|apply.*portfolio|rebalance/i })).toHaveCount(0);

  await context.close();
});
