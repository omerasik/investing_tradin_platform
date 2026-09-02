import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN ?? "module1b-view-token";
const dashboardUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";

test.describe("Module 2B-2 Professional Research, Backtest, Scorecard & Signal Workspaces", () => {
  test("/strategies workspace renders discovery, synthetic classification, hypothesis, failure conditions, and research-only creator", async ({
    browser,
  }) => {
    const consoleErrors: string[] = [];
    const context = await browser.newContext({ baseURL: dashboardUrl, extraHTTPHeaders: {} });
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/login");
    await page.getByLabel("Operator Access Credential").fill(viewToken);
    await page.getByRole("button", { name: "Sign In" }).click();
    await expect(page).toHaveURL(/\/dashboard/);

    await page.goto("/strategies");
    await expect(page).toHaveURL(/\/strategies/);
    await expect(page.getByRole("heading", { name: "Strategy Laboratory", level: 1 })).toBeVisible();

    await expect(page.getByRole("heading", { name: "Discovered Strategies" })).toBeVisible();
    await expect(page.getByLabel("Strategy Discovery Filters")).toBeVisible();

    // Module 1B seed data is entirely synthetic and no real market-data provider is
    // authorized on this platform (Module 2B-2.1): a demo-derived strategy must render
    // the truthful SYNTHETIC classification, never REAL_DATA_RESEARCH_EVIDENCE.
    await expect(page.getByText("SYNTHETIC_ENGINEERING_EVIDENCE_ONLY").first()).toBeVisible();
    await expect(page.getByText("REAL_DATA_RESEARCH_EVIDENCE")).toHaveCount(0);
    await expect(page.getByText("WHEN SHOULD THIS STRATEGY NOT WORK?")).toBeVisible();

    await expect(page.getByRole("heading", { name: "Create Research Strategy" })).toBeVisible();
    await expect(page.getByText("RESEARCH ONLY", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("NO EXECUTION AUTHORITY", { exact: false }).first()).toBeVisible();

    await expect(page.getByRole("button", { name: /execute|trade|buy|sell|order/i })).toHaveCount(0);
    await expect(page.getByText("LIVE TRADING: DISABLED").first()).toBeVisible();

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/backtests workspace renders experiment discovery, dataset and cost model, validation state, and promotion outcome without execution", async ({
    browser,
  }) => {
    const consoleErrors: string[] = [];
    const context = await browser.newContext({ baseURL: dashboardUrl, extraHTTPHeaders: {} });
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/login");
    await page.getByLabel("Operator Access Credential").fill(viewToken);
    await page.getByRole("button", { name: "Sign In" }).click();
    await expect(page).toHaveURL(/\/dashboard/);

    await page.goto("/backtests");
    await expect(page).toHaveURL(/\/backtests/);
    await expect(page.getByRole("heading", { name: "Backtest & Validation Workspace", level: 1 })).toBeVisible();

    await expect(page.getByRole("heading", { name: "Discovered Experiments" })).toBeVisible();
    await expect(page.getByLabel("Backtest Discovery Filters")).toBeVisible();

    await expect(page.getByText("Cost Model", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("Validation", { exact: false }).first()).toBeVisible();
    await expect(page.locator(".inspector-section-title", { hasText: "Promotion Decision" })).toBeVisible();

    // Module 1B seed data is entirely synthetic and no real market-data provider is
    // authorized on this platform (Module 2B-2.1): a demo-derived experiment must render
    // the truthful SYNTHETIC classification, never REAL_DATA_RESEARCH_EVIDENCE.
    await expect(page.getByText("SYNTHETIC_ENGINEERING_EVIDENCE_ONLY").first()).toBeVisible();
    await expect(page.getByText("REAL_DATA_RESEARCH_EVIDENCE")).toHaveCount(0);

    await expect(page.getByRole("heading", { name: "Run Research Experiment" })).toBeVisible();
    await expect(page.getByText("RESEARCH ONLY", { exact: false }).first()).toBeVisible();

    await expect(page.getByRole("button", { name: /execute|trade|buy|sell|order|approve.*live/i })).toHaveCount(0);
    await expect(page.getByText("AUTO_APPROVED LIVE")).toHaveCount(0);

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/scorecards workspace renders all required metric groups and MEASURED/ASSUMED/UNAVAILABLE evidence semantics", async ({
    browser,
  }) => {
    const consoleErrors: string[] = [];
    const context = await browser.newContext({ baseURL: dashboardUrl, extraHTTPHeaders: {} });
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/login");
    await page.getByLabel("Operator Access Credential").fill(viewToken);
    await page.getByRole("button", { name: "Sign In" }).click();
    await expect(page).toHaveURL(/\/dashboard/);

    await page.goto("/scorecards");
    await expect(page).toHaveURL(/\/scorecards/);
    await expect(page.getByRole("heading", { name: "Strategy Scorecard V2", level: 1 })).toBeVisible();

    await expect(page.getByRole("heading", { name: "Discovered Scorecards" })).toBeVisible();
    await expect(page.getByLabel("Scorecard Discovery Filters")).toBeVisible();

    await expect(page.getByText("Evidence Coverage Summary")).toBeVisible();
    await expect(page.getByRole("region", { name: "PERFORMANCE", exact: true })).toBeVisible();
    await expect(page.getByText("MEASURED").first()).toBeVisible();

    // Module 1B seed data is entirely synthetic and no real market-data provider is
    // authorized on this platform (Module 2B-2.1): a demo-derived scorecard must render
    // the truthful SYNTHETIC classification, never REAL_DATA_RESEARCH_EVIDENCE.
    await expect(page.getByText("SYNTHETIC_ENGINEERING_EVIDENCE_ONLY").first()).toBeVisible();
    await expect(page.getByText("REAL_DATA_RESEARCH_EVIDENCE")).toHaveCount(0);

    await expect(page.getByRole("button", { name: /execute|trade|buy|sell|order/i })).toHaveCount(0);

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/signals workspace renders signal list, lifecycle, contradictions, and SIGNAL != ORDER with no execution controls", async ({
    browser,
  }) => {
    const consoleErrors: string[] = [];
    const context = await browser.newContext({ baseURL: dashboardUrl, extraHTTPHeaders: {} });
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/login");
    await page.getByLabel("Operator Access Credential").fill(viewToken);
    await page.getByRole("button", { name: "Sign In" }).click();
    await expect(page).toHaveURL(/\/dashboard/);

    await page.goto("/signals");
    await expect(page).toHaveURL(/\/signals/);
    await expect(page.getByRole("heading", { name: "Signal Explorer", level: 1 })).toBeVisible();

    await expect(page.getByText(/SIGNAL\s*(≠|!=)\s*ORDER/)).toBeVisible();
    await expect(page.getByText("NO EXECUTION AUTHORITY")).toBeVisible();

    await expect(page.getByRole("heading", { name: "Signal Lifecycle Authority" })).toBeVisible();
    await expect(page.getByLabel("Signal Discovery Filters")).toBeVisible();
    await expect(page.getByText("Contradicting Evidence").first()).toBeVisible();
    await expect(page.getByText("Lifecycle Timeline for", { exact: false }).first()).toBeVisible();

    // Module 1B seed data is entirely synthetic and no real market-data provider is
    // authorized on this platform (Module 2B-2.1): a demo-derived signal must render
    // the truthful SYNTHETIC classification, never REAL_DATA_RESEARCH_EVIDENCE -- a
    // VALIDATED signal is not the same claim as real-data provenance.
    await expect(page.getByText("SYNTHETIC_ENGINEERING_EVIDENCE_ONLY").first()).toBeVisible();
    await expect(page.getByText("REAL_DATA_RESEARCH_EVIDENCE")).toHaveCount(0);

    await expect(page.getByRole("button", { name: /execute|trade|buy|sell|order/i })).toHaveCount(0);
    await expect(page.getByText("LIVE TRADING: DISABLED").first()).toBeVisible();

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });
});
