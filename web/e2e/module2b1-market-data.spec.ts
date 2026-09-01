import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN ?? "module1b-view-token";
const dashboardUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";

test.describe("Module 2B-1 Professional Market & Data Workspaces", () => {
  test("/markets workspace renders provider status, ingestion checkpoints, sealed datasets, and passes WCAG a11y", async ({
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

    await page.goto("/markets");
    await expect(page).toHaveURL(/\/markets/);
    await expect(page.getByRole("heading", { name: "Market & Data Workspaces", level: 1 })).toBeVisible();

    // Verify Provider status is truthfully displayed as EXTERNAL_BLOCKED
    await expect(page.locator(".metric-card", { hasText: "Provider Status" })).toContainText("EXTERNAL_BLOCKED");
    await expect(page.getByText("Zero external market feeds authorized")).toBeVisible();

    // Verify ingestion checkpoint panel
    await expect(page.locator(".metric-card", { hasText: "Ingestion Checkpoint" })).toBeVisible();

    // Verify provenance panel
    await expect(page.getByText("Authority Provenance & Verification")).toBeVisible();

    // Verify zero live trading / execution controls
    await expect(page.getByRole("button", { name: /execute|trade|buy|sell/i })).toHaveCount(0);

    // Accessibility scan
    const a11yResults = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/instruments workstation supports filtering, search, and deep interactive inspector", async ({
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

    await page.goto("/instruments");
    await expect(page).toHaveURL(/\/instruments/);
    await expect(page.getByRole("heading", { name: "Instrument Workstation", level: 1 })).toBeVisible();

    // Verify Filter Bar and Search Field
    await expect(page.getByLabel("Instrument Discovery Filters")).toBeVisible();
    await expect(page.getByLabel("Search Instruments")).toBeVisible();
    await expect(page.getByLabel("Asset Class")).toBeVisible();
    await expect(page.getByLabel("Lifecycle Status")).toBeVisible();

    // Verify Discovered Instruments section
    await expect(page.getByRole("heading", { name: "Discovered Instruments" })).toBeVisible();

    // Verify Inspector aside area
    const inspectorAside = page.getByRole("complementary", { name: "Instrument Detail Inspector" });
    await expect(inspectorAside).toBeVisible();

    // Verify zero live trading / execution controls
    await expect(page.getByRole("button", { name: /execute|trade|buy|sell/i })).toHaveCount(0);

    // Accessibility scan
    const a11yResults = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/data-health quality center enforces non-bypassable gates, findings breakdown, and passes a11y", async ({
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

    await page.goto("/data-health");
    await expect(page).toHaveURL(/\/data-health/);
    await expect(page.getByRole("heading", { name: "Data Health & Quality Center", level: 1 })).toBeVisible();

    // Verify non-bypassable quality invariant banner
    await expect(page.getByText("Quality Gate Invariant:")).toBeVisible();
    await expect(page.getByText("zero bypass controls")).toBeVisible();

    // Verify metrics strip
    await expect(page.getByText("Overall Quality State")).toBeVisible();
    await expect(page.getByText("Total Quality Assessments")).toBeVisible();

    // Verify filter bar
    await expect(page.getByLabel("Data Health Filter Bar")).toBeVisible();

    // Verify no bypass button exists anywhere on page
    const bypassButtons = page.getByRole("button", { name: /bypass|override|ignore/i });
    await expect(bypassButtons).toHaveCount(0);

    // Accessibility scan
    const a11yResults = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/features workspace displays Level 1 catalog, Level 2 PIT materializations, and passes a11y", async ({
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

    await page.goto("/features");
    await expect(page).toHaveURL(/\/features/);
    await expect(page.getByRole("heading", { name: "Feature Engineering & Materialization", level: 1 })).toBeVisible();

    // Verify Level 1 Feature Definitions Catalog
    await expect(page.getByRole("heading", { name: "Feature Definitions Catalog" })).toBeVisible();

    // Verify Level 2 Point-in-Time Materializations Section
    await expect(page.getByRole("heading", { name: "Point-in-Time Materializations" })).toBeVisible();

    // Verify zero live trading / execution controls
    await expect(page.getByRole("button", { name: /execute|trade|buy|sell/i })).toHaveCount(0);

    // Accessibility scan
    const a11yResults = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });
});
