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
  const studyChecks = [];
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
    const studiesResponse = await page.request.get("/api/authorities?target=" + encodeURIComponent("/operator-dashboard/strategy-lab/studies"));
    assert.equal(studiesResponse.status(), 200);
    const studies = await studiesResponse.json();
    const allowlisted = new Map([
      ["020fe0da-b880-54fb-be1b-52cee81bbd7f", "BTCUSDT"],
      ["761c1b42-fb0e-53ec-aaec-6bb5931895cc", "ETHUSDT"],
      ["c187ee85-932a-535a-b147-13821a9ac24b", "SOLUSDT"],
    ]);
    assert.ok(studies.total >= 0 && studies.total <= 3, "only three canonical studies are admitted");
    assert.equal(studies.items.length, studies.total);
    if (process.argv.includes("--require-three-studies")) assert.equal(studies.total, 3);
    for (const study of studies.items) {
      const symbol = allowlisted.get(study.study_id);
      assert.ok(symbol, "study identity must be derived from an allowlisted dataset");
      assert.equal(study.result_count, 84);
      assert.equal(study.planned_trial_count, 84);
      assert.equal(study.strategy_family, "breakout_channel");
      await visit(`/terminal?symbol=${symbol}#strategy-lab`);
      const table = page.getByRole("region", { name: "Strategy Lab studies", exact: true });
      assert.equal(await table.getByRole("link", { name: new RegExp(`breakout_channel on ${symbol}`) }).count(), 1,
        await page.locator("#strategy-lab").innerText());
      await visit(`/strategy-lab?study=${study.study_id}`);
      const note = await page.getByRole("note", { name: "Lite research limitations" }).innerText();
      assert.ok(note.includes("14 days") && note.includes("SEARCH_NON_AUTHORITATIVE") && note.includes("profitability is not validated"));
      const trialResponse = await page.request.get("/api/authorities?target=" + encodeURIComponent(
        `/operator-dashboard/strategy-lab/studies/${study.study_id}/trials?limit=100`));
      assert.equal(trialResponse.status(), 200);
      const trials = await trialResponse.json();
      assert.equal(trials.items.length, 84);
      assert.equal(trials.items.filter(item => item.outcome === "EVALUATED").length, 64);
      assert.equal(trials.items.filter(item => item.outcome === "INADMISSIBLE_PARAMETERS").length, 20);
      const receipts = fs.readdirSync(path.join(state, "research-pilot")).filter(name => name.endsWith(".json"))
        .sort().reverse().map(name => JSON.parse(fs.readFileSync(path.join(state, "research-pilot", name), "utf8")));
      const receipt = receipts.find(item => item.study_id === study.study_id && item.finished && item.manifest);
      assert.ok(receipt, "completed offline receipt exists");
      assert.equal(receipt.study_content_hash, study.content_hash);
      const dataset = provenance.datasets.find(item => item.symbol === symbol);
      assert.equal(study.datasets.length, 1);
      assert.equal(study.datasets[0].dataset_version_id, dataset.dataset_version_id);
      assert.equal(study.datasets[0].content_hash, dataset.content_hash);
      for (const row of receipt.manifest.results) {
        const actual = trials.items.find(item => item.trial_id === row.trial_id);
        assert.ok(actual, "every manifest trial is discoverable");
        assert.equal(actual.result_content_hash, row.result_content_hash);
        assert.equal(actual.trial_content_hash, row.trial_content_hash);
        assert.equal(actual.outcome, row.outcome);
        const metricText = Object.fromEntries(Object.entries(row.metrics).map(([key, value]) =>
          [key, value === null ? null : String(value)]));
        assert.deepEqual(actual.metrics, metricText, "stored metrics match the canonical manifest verbatim");
      }
      const trial = trials.items.find(item => item.outcome === "EVALUATED");
      await visit(`/strategy-lab?study=${study.study_id}&trial=${trial.trial_id}`);
      const text = await page.locator("main").innerText();
      assert.ok(text.includes(trial.metrics.total_return), "the persisted gross metric is visible verbatim");
      assert.ok(text.includes("UNAVAILABLE"), "unsupported economics remain unavailable");
      await page.screenshot({ path: path.join(output, `${symbol}-study.png`) });
      studyChecks.push({ study_id: study.study_id, symbol, results_verified: 84, representative: trial.metrics });
    }
    if (!studies.total) {
      assert.ok((await page.locator("main").innerText()).includes("unavailable"), "absent study evidence explicitly unavailable");
    }
    await visit("/terminal?symbol=ETHUSDT#compare");
    assert.ok((await page.locator("#compare").innerText()).match(/select|unavailable|no .*stud/i), "comparison explains missing evidence");
    if (studies.total) {
      const ids = studies.items.map(item => item.study_id);
      const comparisonQuery = new URLSearchParams();
      for (const id of ids) comparisonQuery.append("compare_study", id);
      await visit(`/terminal?${comparisonQuery}#compare`);
      assert.ok((await page.locator("#compare").innerText()).includes("No frozen candidate set recorded for"));
      const apiQuery = new URLSearchParams();
      for (const id of ids) apiQuery.append("study_id", id);
      const response = await page.request.get("/api/authorities?target=" + encodeURIComponent(
        `/operator-dashboard/research-terminal/compare?${apiQuery}`));
      assert.equal(response.status(), 200);
      const comparison = (await response.json()).comparison;
      assert.deepEqual(comparison.studies_without_frozen_set, ids);
      assert.deepEqual(comparison.rows, []);
      assert.equal(comparison.composite_score, "NONE");
      assert.equal(comparison.sort, null);
      assert.equal(comparison.holdout, "NOT_READ");
    }
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
      study_checks: studyChecks, comparison: "NO_FROZEN_CANDIDATES_NO_RANKING",
      authentication: "login_invalid_valid_cookie_logout", mutations: "REFUSED", page_errors: errors }, null, 2));
    console.log(JSON.stringify({ status: "PASS", first, last, days, charts: 3, visits: visits.length, output }));
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
