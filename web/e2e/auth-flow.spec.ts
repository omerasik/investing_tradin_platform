import { expect, test } from "@playwright/test";

const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN ?? "fixture-view-token";
const dashboardUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";

test.describe("Human Browser Authentication Flow", () => {
  test("complete human login, query-param rejection, session cookie, and logout lifecycle", async ({
    browser,
  }) => {
    // Create a pure clean browser context without any extra custom headers
    const context = await browser.newContext({
      baseURL: dashboardUrl,
      extraHTTPHeaders: {},
    });
    const page = await context.newPage();

    // 1. Unauthenticated visit to / redirects to /login
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Trade Investing Panel", level: 1 })).toBeVisible();
    await expect(page.getByText("LIVE TRADING: DISABLED")).toBeVisible();
    await expect(page.getByText("RESEARCH / PAPER ONLY")).toBeVisible();

    // 2. Query string authentication must be strictly rejected
    await page.goto(`/?token=${encodeURIComponent(viewToken)}`);
    await expect(page).toHaveURL(/\/login/);
    const cookiesAfterQueryParam = await context.cookies();
    expect(cookiesAfterQueryParam.find((c) => c.name === "dashboard_session")).toBeUndefined();
    expect(cookiesAfterQueryParam.find((c) => c.name === "dashboard_view_token")).toBeUndefined();

    // 3. Attempting invalid credential fails cleanly
    await page.goto("/login");
    await page.locator("form[data-hydrated='true']").waitFor();
    await page.getByLabel("Operator Access Credential").fill("incorrect-secret-password");
    await page.getByRole("button", { name: "Sign In" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("alert")).toContainText("Invalid");
    await expect(page).toHaveURL(/\/login/);

    // 4. Submitting valid credential logs in successfully and issues HttpOnly session cookie
    await page.locator("form[data-hydrated='true']").waitFor();
    await page.getByLabel("Operator Access Credential").fill(viewToken);
    await page.getByRole("button", { name: "Sign In" }).click();

    // Arrives at dashboard
    await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();
    await expect(page.getByText("LIVE TRADING: DISABLED", { exact: true })).toBeVisible();

    // Verify session cookie properties
    const cookies = await context.cookies();
    const sessionCookie = cookies.find((c) => c.name === "dashboard_session");
    expect(sessionCookie).toBeDefined();
    expect(sessionCookie?.httpOnly).toBe(true);
    expect(sessionCookie?.sameSite).toBe("Lax");
    expect(sessionCookie?.path).toBe("/");
    expect(sessionCookie?.value).toContain(".");

    // Verify backend operator token is not in rendered HTML
    const pageHtml = await page.content();
    expect(pageHtml).not.toContain("local-dev-operator-token");
    expect(pageHtml).not.toContain("fixture-token");

    // 5. Visiting /login while authenticated redirects back to /
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();

    // 6. Direct API access without authentication header or session is protected
    const unauthFetch = await fetch(`${dashboardUrl}/api/operator`, {
      headers: {},
    });
    expect(unauthFetch.status).toBe(401);

    // 7. Tampering with the session cookie invalidates access
    await context.addCookies([
      {
        name: "dashboard_session",
        value: "tampered.payload.signature",
        url: dashboardUrl,
      },
    ]);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);

    // Re-authenticate to test logout button
    await page.getByLabel("Operator Access Credential").fill(viewToken);
    await page.getByRole("button", { name: "Sign In" }).click();
    await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();

    // 8. Sign Out action clears session and redirects to /login
    await page.getByRole("button", { name: "Sign Out" }).click();
    await expect(page).toHaveURL(/\/login/);

    // After logout, navigating to / must redirect to /login
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);

    await context.close();
  });
});
