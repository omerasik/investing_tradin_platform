import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN ?? "module1b-view-token";
const dashboardUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";

test.describe("Module 2B-3 Risk, Regime & Portfolio Workspaces", () => {
  test("/risk workspace renders the decision ledger, reservation evidence, policy limits, and no risk override", async ({ browser }) => {
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

    await page.goto("/risk");
    await expect(page).toHaveURL(/\/risk/);
    await expect(page.getByRole("heading", { name: "Risk Workspace", level: 1 })).toBeVisible();

    await expect(page.getByRole("heading", { name: "Risk Summary (current page)" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Risk Decision Ledger" })).toBeVisible();
    await expect(page.getByLabel("Risk Decision Filters")).toBeVisible();

    // Module 1B demo evidence seeds one approved and one rejected risk decision.
    await expect(page.getByText("APPROVED").first()).toBeVisible();
    await expect(page.getByText("REJECTED").first()).toBeVisible();
    await expect(page.getByText("paper-only approved", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("synthetic blocked risk decision", { exact: false }).first()).toBeVisible();

    const inspector = page.getByLabel("Risk Decision Inspector");
    await expect(page.getByText("Decision Identity")).toBeVisible();
    await expect(page.getByText("Policy Limits")).toBeVisible();
    await expect(page.getByText("Reservation").first()).toBeVisible();
    await expect(inspector.getByText("NO RESERVATION").first()).toBeVisible();

    await expect(page.getByText("RESEARCH / PAPER ONLY").first()).toBeVisible();
    await expect(page.getByText("NO AUTOMATIC AUTHORITY").first()).toBeVisible();
    await expect(page.getByText("NO RISK OVERRIDE").first()).toBeVisible();

    // "Apply Filters" is a GET-only discovery-filter submit, not a mutation control.
    await expect(page.getByRole("button", { name: /override|increase|release|reserve|approve|execute/i })).toHaveCount(0);
    await expect(page.getByText("LIVE TRADING: DISABLED").first()).toBeVisible();

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/regimes workspace renders discovery, probabilities, uncertainty, and risk effects with no risk-increase control", async ({ browser }) => {
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

    await page.goto("/regimes");
    await expect(page).toHaveURL(/\/regimes/);
    await expect(page.getByRole("heading", { name: "Regime Engine Workspace", level: 1 })).toBeVisible();

    await expect(page.getByRole("heading", { name: "Regime Runs" })).toBeVisible();
    await expect(page.getByLabel("Regime Run Discovery Filters")).toBeVisible();

    await expect(page.getByText("Regime Dimensions")).toBeVisible();
    await expect(page.getByRole("group", { name: /probability distribution/i }).first()).toBeVisible();
    await expect(page.getByText("Uncertainty", { exact: false }).first()).toBeVisible();

    await expect(page.getByText("Regime Risk Effects")).toBeVisible();
    await expect(page.getByText("PROPOSED", { exact: false }).first()).toBeVisible();

    await expect(page.getByText("REGIME MAY REDUCE OR BLOCK RISK").first()).toBeVisible();
    await expect(page.getByText("REGIME CANNOT INCREASE GLOBAL RISK LIMITS").first()).toBeVisible();
    // "Apply Filters" is a GET-only discovery-filter submit, not a risk-increase control.
    await expect(page.getByRole("button", { name: /increase|override|execute/i })).toHaveCount(0);
    const regimeApplyButtons = page.getByRole("button", { name: /apply/i });
    await expect(regimeApplyButtons).toHaveCount(1);
    await expect(regimeApplyButtons.first()).toHaveText("Apply Filters");

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/portfolio workspace renders requested-to-review allocation flow, constraints, and independent risk gate with no execution control", async ({ browser }) => {
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

    await page.goto("/portfolio");
    await expect(page).toHaveURL(/\/portfolio/);
    await expect(page.getByRole("heading", { name: "Portfolio Construction Workspace", level: 1 })).toBeVisible();

    await expect(page.getByRole("heading", { name: "Construction Runs" })).toBeVisible();
    await expect(page.getByLabel("Portfolio Construction Run Filters")).toBeVisible();

    await expect(page.getByText("Allocation Flow (Requested", { exact: false })).toBeVisible();
    await expect(page.getByText("requested", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("review", { exact: false }).first()).toBeVisible();

    await expect(page.getByText("Constraint Ledger").first()).toBeVisible();
    await expect(page.getByText("Independent Portfolio Risk Gate").first()).toBeVisible();
    await expect(page.getByText("REVIEW ELIGIBLE").first()).toBeVisible();
    await expect(page.getByText("APPROVED FOR EXECUTION")).toHaveCount(0);

    await expect(page.getByText("REVIEW ONLY", { exact: false }).first()).toBeVisible();
    // "Apply Filters" is a GET-only discovery-filter submit, not an execution control.
    await expect(page.getByRole("button", { name: /execute|rebalance|buy|sell|order/i })).toHaveCount(0);
    const portfolioApplyButtons = page.getByRole("button", { name: /apply/i });
    await expect(portfolioApplyButtons).toHaveCount(1);
    await expect(portfolioApplyButtons.first()).toHaveText("Apply Filters");
    await expect(page.getByText("LIVE TRADING: DISABLED").first()).toBeVisible();

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });
});
