import { expect, test } from "@playwright/test";

test.skip(Boolean(process.env.TRADE_PLATFORM_DASHBOARD_CONFIG_PATH), "Configured suite owns this deployment.");

test("unconfigured dashboard fails closed without losing safety boundaries", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Command Center", level: 1 })).toBeVisible();
  await expect(page.getByText("LIVE TRADING: DISABLED", { exact: true })).toBeVisible();
  await expect(page.locator("#features")).toContainText("EXTERNAL_BLOCKED");
  await expect(page.locator("#news")).toContainText("EXTERNAL_BLOCKED");
  await expect(page.locator("#portfolio").getByRole("button")).toHaveCount(0);
  const proxy = await request.get(`/api/authorities?target=${encodeURIComponent("/operator-dashboard/sre-overview")}`);
  expect([502, 503]).toContain(proxy.status());
});
