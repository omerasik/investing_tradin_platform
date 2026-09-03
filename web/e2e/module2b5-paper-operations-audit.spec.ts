import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN ?? "module1b-view-token";
const dashboardUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Operator Access Credential").fill(viewToken);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test.describe("Module 2B-5 Paper OMS, Operations & Audit Workspaces", () => {
  test("/paper workspace renders discovery, lifecycle inspector, fills, and truthful reconciliation with no broker control", async ({ browser }) => {
    const consoleErrors: string[] = [];
    const context = await browser.newContext({ baseURL: dashboardUrl, extraHTTPHeaders: {} });
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await login(page);
    await page.goto("/paper");
    await expect(page).toHaveURL(/\/paper/);
    await expect(page.getByRole("heading", { name: "Paper OMS", level: 1 })).toBeVisible();

    // Safety boundary, always visible.
    await expect(page.getByText("PAPER ONLY", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("NO BROKER CONNECTIVITY", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("NO LIVE ORDER SUBMISSION", { exact: false }).first()).toBeVisible();

    // Order discovery.
    await expect(page.getByLabel("Paper Order Filters")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Paper Order Discovery" })).toBeVisible();
    await expect(page.getByText("DEMO_EQ_A", { exact: false }).first()).toBeVisible();

    // Order Inspector: identity, lifecycle, fills, fill summary.
    const inspector = page.getByLabel("Paper Order Inspector");
    await expect(inspector.getByText("Identity")).toBeVisible();
    await expect(inspector.getByText("Execution Boundary")).toBeVisible();
    await expect(inspector.getByText("NO BROKER TRANSPORT")).toBeVisible();
    await expect(inspector.getByText("NO LIVE EXECUTION")).toBeVisible();
    await expect(inspector.getByText("Lifecycle Timeline")).toBeVisible();
    await expect(inspector.getByText("Fills", { exact: true })).toBeVisible();
    await expect(inspector.getByText("Paper Fill Summary")).toBeVisible();

    // Reconciliation: truthful paper-only labeling, never "BROKER RECONCILED".
    await expect(page.getByRole("heading", { name: "Paper Reconciliation" })).toBeVisible();
    await expect(page.getByText("PAPER ACCOUNT RECONCILED", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("BROKER RECONCILED", { exact: false })).toHaveCount(0);

    // No live/broker controls anywhere on this page.
    await expect(page.getByRole("button", { name: /submit order|cancel.*broker|switch to live|execute live|buy live|sell live/i })).toHaveCount(0);
    await expect(page.getByText("LIVE TRADING: DISABLED").first()).toBeVisible();

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/operations workspace renders service overview, dependency health, SLO target vs measured, incidents, drills, and kill switch with no mutation control", async ({ browser }) => {
    const consoleErrors: string[] = [];
    const context = await browser.newContext({ baseURL: dashboardUrl, extraHTTPHeaders: {} });
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await login(page);
    await page.goto("/operations");
    await expect(page).toHaveURL(/\/operations/);
    await expect(page.getByRole("heading", { name: "Operations & SRE", level: 1 })).toBeVisible();

    await expect(page.getByRole("heading", { name: "System Overview" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Service Identity" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Dependency Health" })).toBeVisible();

    // SLO workspace: TARGET and MEASURED are rendered as distinct evidence classes.
    await expect(page.getByRole("heading", { name: "SLO Workspace" })).toBeVisible();
    await expect(page.getByText("TARGET ≠ MEASURED").first()).toBeVisible();
    await expect(page.getByText("TARGET", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("MEASURED", { exact: true }).first()).toBeVisible();

    // Incidents ledger.
    await expect(page.getByRole("heading", { name: "Incidents" })).toBeVisible();
    await expect(page.getByText("RESOLVED", { exact: false }).first()).toBeVisible();

    // Failure/recovery drills, labeled as engineering evidence.
    await expect(page.getByRole("heading", { name: "Failure & Recovery Drills" })).toBeVisible();
    await expect(page.getByText("ENGINEERING / DRILL EVIDENCE")).toBeVisible();
    await expect(page.getByText("PRODUCTION OUTAGE PROOF")).toHaveCount(0);

    // Kill switch, read directly from persisted authority.
    await expect(page.getByText("Kill Switch", { exact: false }).first()).toBeVisible();

    // No mutation controls (acknowledge/resolve/override) anywhere on this page.
    await expect(page.getByRole("button", { name: /acknowledge|resolve|override|bypass/i })).toHaveCount(0);
    await expect(page.getByText("LIVE TRADING: DISABLED").first()).toBeVisible();

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/audit workspace renders Audit Events distinct from Operational Alerts with truthful immutability messaging, bounded search, and no mutation control", async ({ browser }) => {
    const consoleErrors: string[] = [];
    const context = await browser.newContext({ baseURL: dashboardUrl, extraHTTPHeaders: {} });
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await login(page);
    await page.goto("/audit");
    await expect(page).toHaveURL(/\/audit/);
    await expect(page.getByRole("heading", { name: "Audit Workspace", level: 1 })).toBeVisible();

    // Immutability banner, always visible.
    await expect(page.getByText("READ ONLY", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("IMMUTABLE EVIDENCE", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("NO MUTATION ROUTE EXPOSED BY DASHBOARD", { exact: false }).first()).toBeVisible();

    // Two distinct sections: Audit Events and Operational Alerts.
    await expect(page.getByRole("heading", { name: "Audit Events" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Operational Alerts" })).toBeVisible();
    await expect(page.getByLabel("Audit Event Filters")).toBeVisible();

    // Truthful audit authority disclosure -- SQLite append-only, not the Postgres
    // immutability trigger used elsewhere on this platform.
    await expect(page.getByText("SQLITE_APPEND_ONLY_STORE", { exact: false }).first()).toBeVisible();

    // No secret text or mutation controls anywhere on this page.
    await expect(page.getByText(/authorization|bearer |api_key|password|dsn/i)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /delete|edit|mutate|acknowledge|resolve/i })).toHaveCount(0);
    await expect(page.getByText("LIVE TRADING: DISABLED").first()).toBeVisible();

    const a11yResults = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("human flow: login through Paper OMS, Operations, and Audit workspaces with no console errors", async ({ browser }) => {
    const consoleErrors: string[] = [];
    const context = await browser.newContext({ baseURL: dashboardUrl, extraHTTPHeaders: {} });
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await login(page);
    await page.goto("/paper");
    await expect(page.getByRole("heading", { name: "Paper OMS", level: 1 })).toBeVisible();
    await page.goto("/operations");
    await expect(page.getByRole("heading", { name: "Operations & SRE", level: 1 })).toBeVisible();
    await page.goto("/audit");
    await expect(page.getByRole("heading", { name: "Audit Workspace", level: 1 })).toBeVisible();

    expect(consoleErrors).toEqual([]);
    await context.close();
  });
});
