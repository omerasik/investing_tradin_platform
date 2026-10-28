/* Bounded local browser smoke against the dedicated Lite ports; never starts services. */
import { chromium } from "@playwright/test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

async function main() {
  const state = path.join(os.homedir(), ".trade_platform", "terminal-lite");
  const secrets = JSON.parse(fs.readFileSync(path.join(state, "settings.local.json"), "utf8"));
  const pointerPath = path.join(state, "active-subset.local.json");
  const pointer = fs.existsSync(pointerPath) ? JSON.parse(fs.readFileSync(pointerPath, "utf8")) : undefined;
  const root = path.resolve(state, pointer?.relative_root ?? "evidence");
  assert.ok(root.startsWith(state + path.sep), "subset stays inside Lite state");
  const provenance = JSON.parse(fs.readFileSync(path.join(root, "lite-provenance.json"), "utf8"));
  const first = provenance.first_utc_day ?? provenance.utc_day;
  const last = provenance.last_utc_day ?? provenance.utc_day;
  const days = provenance.day_count ?? 1;
  const output = path.join(state, "browser-smoke", `${first}_${last}`);
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ baseURL: "http://127.0.0.1:3011", viewport: { width: 1440, height: 1050 } });
  const page = await context.newPage();
  page.setDefaultTimeout(20_000);
  const errors = [];
  const visits = [];
  page.on("pageerror", error => errors.push(error.message));
  async function visit(url) {
    const start = Date.now();
    const response = await page.goto(url, { waitUntil: "networkidle", timeout: 60_000 });
    assert.equal(response.status(), 200, url);
    visits.push({ path: url, milliseconds: Date.now() - start });
    const html = await page.content();
    for (const key of ["operator_token", "session_secret", "db_password", "reader_password"])
      assert.ok(!html.includes(secrets[key]), `secret must not appear in DOM: ${key}`);
  }
  try {
    await visit("/terminal");
    assert.ok(page.url().includes("/login"), "unauthenticated terminal redirects");
    await page.getByLabel("Operator Access Credential").fill("incorrect-lite-password");
    await page.getByRole("button", { name: "Sign In" }).click();
    await page.waitForURL(/error=/);
    await page.getByLabel("Operator Access Credential").fill(secrets.dashboard_password);
    await page.getByRole("button", { name: "Sign In" }).click();
    await page.waitForURL(url => !url.pathname.includes("login"));
    const cookie = (await context.cookies()).find(c => c.name === "dashboard_session");
    assert.ok(cookie?.httpOnly && cookie.sameSite === "Lax", "protected session cookie");
    await visit("/terminal");
    assert.ok((await page.getByRole("status", { name: "Research Terminal Lite limits" }).textContent()).includes("REAL HISTORICAL"));
    assert.equal(await page.getByRole("form", { name: "Terminal command console" }).count(), 0);
    await page.screenshot({ path: path.join(output, "terminal.png") });
    for (const symbol of ["BTCUSDT", "ETHUSDT", "SOLUSDT"]) {
      await visit(`/chart?symbol=${symbol}&tf=1h`);
      await page.locator("canvas").first().waitFor({ state: "visible" });
      const text = await page.locator("main").innerText();
      assert.ok(text.includes(symbol) && text.includes(first) && text.includes(last), `verified historical ${symbol} window`);
      assert.ok(!text.includes("Chart unavailable"), "chart availability");
      await page.screenshot({ path: path.join(output, `${symbol}.png`) });
    }
    await visit("/chart?symbol=BTCUSDT&tf=1d");
    await page.locator("canvas").first().waitFor({ state: "visible" });
    const dailyText = await page.locator("main").innerText();
    assert.ok(dailyText.includes(first) && dailyText.includes(last), "daily chart spans admitted dates");
    await visit("/chart?symbol=BTCUSDT&tf=4h");
    await page.locator("canvas").first().waitFor({ state: "visible" });
    await visit("/chart?symbol=BTCUSDT&tf=1m");
    await page.locator("canvas").first().waitFor({ state: "visible" });
    if (days > 1) {
      await page.getByRole("link", { name: /^Earlier candles/ }).click();
      await page.waitForURL(url => url.searchParams.has("end"));
      await page.locator("canvas").first().waitFor({ state: "visible" });
    }
    await visit("/strategy-lab");
    assert.ok((await page.locator("main").innerText()).includes("unavailable"), "study evidence explicitly unavailable");
    await visit("/terminal?symbol=ETHUSDT#compare");
    assert.ok((await page.locator("#compare").innerText()).match(/select|unavailable|no .*stud/i), "comparison explains missing evidence");
    await visit("/evidence");
    assert.equal(await page.getByRole("table", { name: "Lite historical windows" }).getByRole("row").count(), 4);
    const evidenceText = await page.getByRole("table", { name: "Lite historical windows" }).innerText();
    assert.ok(evidenceText.includes(first) && evidenceText.includes(last));
    for (const item of provenance.datasets) assert.ok(evidenceText.includes(item.content_hash));
    await page.getByRole("link", { name: "BTCUSDT", exact: true }).click();
    await page.waitForURL(/\/chart\?/);
    await page.locator("canvas").first().waitFor({ state: "visible" });
    // Browser-side command API also refuses, with the session in place.
    const refused = await page.evaluate(async () => {
      const result = await fetch("/api/terminal/commands", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "PAPER_INCUBATION_START", inputs: {}, idempotency_key: "lite-smoke-refused" }) });
      return { status: result.status, text: await result.text() };
    });
    assert.equal(refused.status, 403);
    assert.ok(refused.text.includes("LITE_READ_ONLY"));
    await page.getByRole("button", { name: "Sign Out" }).click();
    await page.waitForURL(/\/login/);
    assert.ok(!(await context.cookies()).some(c => c.name === "dashboard_session"));
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ status: "PASS", first, last, days, visits, charts: 3,
      authentication: "login_invalid_valid_cookie_logout", mutations: "REFUSED", page_errors: errors }, null, 2));
    console.log(JSON.stringify({ status: "PASS", first, last, days, charts: 3, visits: visits.length, output }));
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
