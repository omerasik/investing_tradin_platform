import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN ?? "module1b-view-token";
const dashboardUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";

test.describe("Module 2A App Shell & Navigation Foundation", () => {
  test("complete app shell lifecycle: authentication, sidebar navigation, topbar, safety badges, active route highlighting, and sign out", async ({
    browser,
  }) => {
    const consoleErrors: string[] = [];
    const context = await browser.newContext({
      baseURL: dashboardUrl,
      extraHTTPHeaders: {},
    });
    const page = await context.newPage();

    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(msg.text());
      }
    });

    // 1. Unauthenticated visit to / redirects to /login
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Trade Investing Panel", level: 1 })).toBeVisible();

    // 2. Perform authenticated login
    await page.getByLabel("Operator Access Credential").fill(viewToken);
    await page.getByRole("button", { name: "Sign In" }).click();

    // 3. Lands on /dashboard
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();

    // 4. Verify App Shell layout components
    const sidebar = page.locator(".app-sidebar");
    await expect(sidebar).toBeVisible();
    await expect(sidebar.getByText("Trade Investing Panel")).toBeVisible();
    await expect(sidebar.getByText("Operator Workstation")).toBeVisible();

    // Verify Sidebar navigation groups
    for (const group of ["Overview", "Market & Data", "Research", "Portfolio & Risk", "Investing", "Execution", "System"]) {
      await expect(sidebar.locator(".nav-group-title", { hasText: group })).toBeVisible();
    }

    // Verify Dashboard link is marked active
    const dashboardLink = sidebar.getByRole("link", { name: "Dashboard" });
    await expect(dashboardLink).toBeVisible();
    await expect(dashboardLink).toHaveClass(/active/);
    await expect(dashboardLink).toHaveAttribute("aria-current", "page");

    // Verify TopBar elements
    const topbar = page.locator(".app-topbar");
    await expect(topbar).toBeVisible();
    await expect(topbar.getByText("LIVE TRADING: DISABLED")).toBeVisible();
    await expect(topbar.getByRole("button", { name: "Sign Out" })).toBeVisible();

    // Verify Command Center overview cards and panels
    await expect(page.locator(".cards")).toBeVisible();
    await expect(page.locator("#command")).toBeVisible();
    await expect(page.locator("#features")).toBeVisible();
    await expect(page.locator("#risk")).toBeVisible();
    await expect(page.locator("#operations")).toBeVisible();

    // 5. Navigate to /features via Sidebar
    await sidebar.getByRole("link", { name: "Features" }).click();
    await expect(page).toHaveURL(/\/features/);
    await expect(page.getByRole("heading", { name: /Feature/, level: 1 })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "Features" })).toHaveClass(/active/);
    await expect(topbar.getByText("Features")).toBeVisible();

    // 6. Navigate to /risk via Sidebar
    await sidebar.getByRole("link", { name: "Risk" }).click();
    await expect(page).toHaveURL(/\/risk/);
    await expect(page.getByRole("heading", { name: "Risk Workspace", level: 1 })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "Risk" })).toHaveClass(/active/);
    await expect(page.getByText("NO RISK OVERRIDE")).toBeVisible();

    // 7. Navigate to /operations via Sidebar
    await sidebar.getByRole("link", { name: "Operations" }).click();
    await expect(page).toHaveURL(/\/operations/);
    await expect(page.getByRole("heading", { name: "Operations & SRE", level: 1 })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "Operations" })).toHaveClass(/active/);
    await expect(page.getByText("TARGET and MEASURED are distinct")).toBeVisible();

    // 8. Return to /dashboard
    await sidebar.getByRole("link", { name: "Dashboard" }).click();
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();

    // 9. Accessibility scan on App Shell (/dashboard)
    const a11yResults = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(a11yResults.violations).toEqual([]);

    // 10. Verify no browser console runtime errors
    expect(consoleErrors).toEqual([]);

    // 11. Sign Out
    await topbar.getByRole("button", { name: "Sign Out" }).click();
    await expect(page).toHaveURL(/\/login/);

    await context.close();
  });
});
