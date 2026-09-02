import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000",
    extraHTTPHeaders: process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN
      ? { Authorization: `Bearer ${process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN}` }
      : undefined,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npx next start -p 3000",
    url: "http://127.0.0.1:3000/login",
    reuseExistingServer: true,
    timeout: 30_000,
    env: {
      TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN: process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN ?? "module1b-view-token",
    },
  },
});
