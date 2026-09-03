import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN ?? "module1b-view-token";
const dashboardUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";

test.describe("Module 2B-4 Investment Research & News Intelligence Workspaces", () => {
  test("/investments workspace renders thesis discovery, scenarios, valuation, catalysts, risks, invalidation, review history, and portfolio review evidence with no trading control", async ({ browser }) => {
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

    await page.goto("/investments");
    await expect(page).toHaveURL(/\/investments/);
    await expect(page.getByRole("heading", { name: "Investment Research Workspace", level: 1 })).toBeVisible();

    // Safety boundary, always visible.
    await expect(page.getByText("NOT A REAL INVESTMENT RECOMMENDATION").first()).toBeVisible();
    await expect(page.getByText("REVIEW ONLY", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("NO BUY / SELL AUTHORITY", { exact: false }).first()).toBeVisible();

    // Thesis discovery: professional table, not a UUID-led dump.
    await expect(page.getByLabel("Investment Thesis Discovery Filters")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Thesis Discovery Table" })).toBeVisible();
    await expect(page.getByText("DEMO_EQ_A", { exact: false }).first()).toBeVisible();

    // Thesis Inspector sections.
    await expect(page.getByText("Investment Thesis", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("Quality / Company Research")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Bear Case" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Base Case" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Bull Case" })).toBeVisible();
    await expect(page.getByText("Valuation", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("finite-dcf-v1", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("Catalysts", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("Risks", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("What Would Invalidate This Thesis?")).toBeVisible();
    await expect(page.getByText("fictional revenue decline", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("Review History")).toBeVisible();
    await expect(page.getByText("SYNTHETIC_ENGINEERING_EVIDENCE_ONLY", { exact: false }).first()).toBeVisible();

    // Investment Portfolio, explicitly separate from systematic Portfolio Construction V2.
    await expect(page.getByRole("heading", { name: "Investment Portfolio", exact: true })).toBeVisible();
    await expect(page.getByLabel("Investment Portfolio Discovery Filters")).toBeVisible();
    await expect(page.getByText("Holdings", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Rebalance Candidates").first()).toBeVisible();
    await expect(page.getByText("REBALANCE CANDIDATE", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("TRADE", { exact: true })).toHaveCount(0);

    // No trading/order control anywhere on this page.
    await expect(page.getByRole("button", { name: /buy|sell|execute|order|auto-trade|auto-promote|auto-invest|apply.*portfolio|rebalance(?!.*candidate)/i })).toHaveCount(0);
    const applyButtons = page.getByRole("button", { name: /apply/i });
    await expect(applyButtons).toHaveCount(2);
    await expect(page.getByText("LIVE TRADING: DISABLED").first()).toBeVisible();

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/news workspace renders provider state, correction/retraction chain, credibility/uncertainty, entity links and rights with no order-generation control", async ({ browser }) => {
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

    await page.goto("/news");
    await expect(page).toHaveURL(/\/news/);
    await expect(page.getByRole("heading", { name: "News / Event Intelligence Workspace", level: 1 })).toBeVisible();

    // Safety boundary, always visible.
    await expect(page.getByText("NOT LIVE NEWS").first()).toBeVisible();
    await expect(page.getByText("RESEARCH EVIDENCE ONLY", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("EXTERNAL_BLOCKED", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("NO EXTERNAL NEWS PROVIDER AUTHORIZED", { exact: false }).first()).toBeVisible();

    // Event discovery.
    await expect(page.getByLabel("News Event Discovery Filters")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Event Discovery" })).toBeVisible();
    await expect(page.getByText("Demo issuer retracts fictional guidance", { exact: false }).first()).toBeVisible();

    // Event Inspector: source/rights, time semantics, credibility/uncertainty, entities.
    await expect(page.getByText("Source", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Time Semantics")).toBeVisible();
    await expect(page.getByText("Published At")).toBeVisible();
    await expect(page.getByText("Credibility / Uncertainty")).toBeVisible();
    await expect(page.getByText("Entity Links")).toBeVisible();
    await expect(page.getByText("Provider Activated")).toBeVisible();

    // Correction/retraction chain, the most important News UX feature: a retracted
    // event must look clearly withdrawn, not shown as a live current claim. Scoped to
    // the inspector aside so the filter dropdown's own "Retraction" <option> (present
    // but not rendered visible while the <select> is closed) is never the match.
    const newsInspector = page.getByLabel("News Event Inspector");
    await expect(newsInspector.getByText("Correction / Retraction Chain")).toBeVisible();
    await expect(newsInspector.getByText("RETRACTION", { exact: false }).first()).toBeVisible();
    await expect(newsInspector.getByText("RETRACTS", { exact: false }).first()).toBeVisible();
    await expect(newsInspector.getByText("WITHDRAWN", { exact: false }).first()).toBeVisible();

    // Rights / provenance.
    await expect(page.getByText("Authority Provenance", { exact: false }).first()).toBeVisible();

    // No execution/order-generation control anywhere on this page.
    await expect(page.getByRole("button", { name: /buy|sell|execute|order|auto-trade|trade/i })).toHaveCount(0);
    await expect(page.getByText("LIVE TRADING: DISABLED").first()).toBeVisible();

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("News event ambiguous entity link and cross-link to Investments render distinctly", async ({ browser }) => {
    const context = await browser.newContext({ baseURL: dashboardUrl, extraHTTPHeaders: {} });
    const page = await context.newPage();

    await page.goto("/login");
    await page.getByLabel("Operator Access Credential").fill(viewToken);
    await page.getByRole("button", { name: "Sign In" }).click();
    await expect(page).toHaveURL(/\/dashboard/);

    await page.goto("/news?correction_state=INITIAL");
    await expect(page.getByText("AMBIGUOUS", { exact: false }).first()).toBeVisible();

    await page.getByRole("link", { name: /View thesis/i }).first().click();
    await expect(page).toHaveURL(/\/investments\?instrument=/);
    await expect(page.getByRole("heading", { name: "Investment Research Workspace", level: 1 })).toBeVisible();
  });
});
