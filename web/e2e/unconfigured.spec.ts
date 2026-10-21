import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.skip(Boolean(process.env.TRADE_PLATFORM_DASHBOARD_CONFIG_PATH), "Configured suite owns this deployment.");

test("unconfigured dashboard fails closed without losing safety boundaries", async ({ page, request }) => {
  const response = await page.goto("/");
  expect(response?.headers()["x-frame-options"]).toBe("DENY");
  const policy = response?.headers()["content-security-policy"] ?? "";
  expect(policy).toContain("frame-ancestors 'none'");
  expect(policy).not.toContain("'unsafe-inline'");
  expect(policy).toMatch(/script-src 'self' 'nonce-[a-f0-9]{32}' 'strict-dynamic'/);
  await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();
  await expect(page.getByText("LIVE TRADING: DISABLED", { exact: true })).toBeVisible();
  await expect(page.locator("#features")).toContainText("EXTERNAL_BLOCKED");
  await expect(page.locator("#news")).toContainText("EXTERNAL_BLOCKED");
  await expect(page.locator("#portfolio").getByRole("button")).toHaveCount(0);
  const proxy = await request.get(`/api/authorities?target=${encodeURIComponent("/operator-dashboard/sre-overview")}`);
  expect([502, 503]).toContain(proxy.status());
});

test("unconfigured dashboard has no automated WCAG A or AA violations", async ({ page }) => {
  await page.goto("/");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(results.violations).toEqual([]);
});

test("unconfigured terminal distinguishes unavailable reads from empty evidence", async ({ page }) => {
  await page.goto("/terminal");
  await expect(page.getByRole("heading", { name: "Research Terminal", level: 1 })).toBeVisible();
  const monitoring = page.getByRole("article", { name: "Monitoring", exact: true });
  await expect(monitoring).toContainText("Latest live signal: EXTERNAL_BLOCKED: Research terminal overview is unavailable.");
  await expect(monitoring).toContainText("Paper fills: EXTERNAL_BLOCKED: Research terminal overview is unavailable.");
  await expect(monitoring).not.toContainText("none recorded");
  const comparison = page.getByRole("form", { name: "Candidate comparison selection" });
  await expect(comparison).toContainText("EXTERNAL_BLOCKED");
  await expect(comparison).not.toContainText("No study registered");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(results.violations).toEqual([]);
});
