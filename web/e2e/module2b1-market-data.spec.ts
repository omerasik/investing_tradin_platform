import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const viewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN ?? "module1b-view-token";
const dashboardUrl = process.env.DASHBOARD_URL ?? "http://127.0.0.1:3000";

test.describe("Module 2B-1 Professional Market & Data Workspaces", () => {
  test("/markets workspace renders the public-data scope, timing ceilings, sealed datasets, and passes WCAG a11y", async ({
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
    await expect(page.getByRole("heading", { name: "Market Data Sources", level: 1 })).toBeVisible();

    // Phase R5 UI-1: public market data is authorized; broker/account/order access is not.
    // The page used to claim "zero external market feeds authorized", which stopped being
    // true when the public Bybit V5 paths were authorized.
    await expect(page.locator(".metric-card", { hasText: "Market Data Scope" })).toContainText("PUBLIC ONLY");
    await expect(page.getByText("No broker, account or order access.")).toBeVisible();
    await expect(page.getByText("Zero external market feeds authorized")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Sources & Timing Ceilings" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Market Data Sources and Timing Ceilings" })).toContainText("T4_FIRST_PARTY_CAPTURE");
    await expect(page.getByRole("region", { name: "Market Data Sources and Timing Ceilings" })).toContainText("UNSET_PENDING_OWNER_DECISION_OR_5");

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

  test("/evidence workspace shows timing ceilings, capture availability and catalogs without claiming verdicts", async ({
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

    await page.goto("/evidence");
    await expect(page.getByRole("heading", { name: "Evidence & Data", level: 1 })).toBeVisible();
    await expect(page.getByRole("region", { name: "Market Data Sources and Timing Ceilings" })).toContainText("T1_RETROSPECTIVE");
    // Capture availability is reported honestly whether or not this host holds an archive.
    await expect(page.getByRole("heading", { name: "First-Party Capture Availability" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sealed First-Party Segments" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Public Trade Archive (T2 Event Time)" })).toBeVisible();
    await expect(page.getByRole("button", { name: /execute|trade|buy|sell/i })).toHaveCount(0);

    const a11yResults = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/chart workspace names its claim ceiling or states that nothing is catalogued, and passes a11y", async ({
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

    await page.goto("/chart");
    await expect(page.getByRole("heading", { name: "Market Chart", level: 1 })).toBeVisible();
    // B.1.3a: the workflow strip marks this step and labels its data as historical, never live.
    const workflow = page.getByRole("navigation", { name: "Research workflow", exact: true });
    await expect(workflow.getByRole("link", { name: /Market Chart/ })).toHaveAttribute("aria-current", "page");
    await expect(workflow).toContainText("not live prices");
    const series = page.getByRole("article", { name: "Chart Series" });
    await expect(series).toBeVisible();
    // Either a series with its claim ceiling, or an honest empty catalogue -- never a bare chart.
    await expect(page.getByText(/CLAIM CEILING: |No chartable bar frame is catalogued/).first()).toBeVisible();
    await expect(page.getByRole("button", { name: /execute|trade|buy|sell/i })).toHaveCount(0);

    const a11yResults = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/strategy-lab lists studies read-only, keeps net profitability unavailable, and passes a11y", async ({
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

    await page.goto("/strategy-lab");
    await expect(page.getByRole("heading", { name: "Strategy Lab Explorer", level: 1 })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Research workflow", exact: true }).getByRole("link", { name: /Strategy Lab/ }))
      .toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("article", { name: "Studies" })).toBeVisible();
    // Either registered studies (each with its economics restated) or an honest empty state.
    await expect(page.getByText(/Registered studies|No study registered/i).first()).toBeVisible();
    const economics = page.getByRole("region", { name: "Economics" });
    if (await economics.count()) {
      await expect(economics).toContainText("Net profitability");
      await expect(economics.locator('[data-economics="net"]')).toContainText("UNAVAILABLE");
      await expect(economics.locator('[data-economics="verified"]')).toContainText("UNAVAILABLE");
    }
    // B.1.2b: the break-even view is a gross sensitivity, never a cost or a profit, and no heatmap is chosen for the operator.
    const breakEven = page.getByRole("article", { name: "Break-even cost distribution" });
    if (await breakEven.count()) {
      await expect(breakEven).toContainText("GROSS-RESULT COST SENSITIVITY");
      await expect(breakEven).toContainText("not a verified cost");
      await expect(page.getByRole("article", { name: "Parameter heatmap" })).not.toContainText(/best trial:|profitable|validated/i);
      await expect(page.getByRole("region", { name: "Parameter heatmap" })).toHaveCount(0);
    }
    await expect(page.getByRole("button", { name: /execute|trade|buy|sell|open holdout|promote|run search/i })).toHaveCount(0);

    const a11yResults = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(a11yResults.violations).toEqual([]);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test("/terminal walks the research workflow, restates every claim, offers only gated commands, and passes a11y", async ({
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

    await page.goto("/terminal");
    await expect(page.getByRole("heading", { name: "Research Terminal", level: 1 })).toBeVisible();
    // B.1.3a: the chart and Strategy Lab are one click away from the terminal and the sidebar.
    const workflow = page.getByRole("navigation", { name: "Research workflow", exact: true });
    await expect(workflow.getByRole("link", { name: /Research Terminal/ })).toHaveAttribute("aria-current", "page");
    await expect(workflow.getByRole("link", { name: /Market Chart/ })).toHaveAttribute("href", "/chart");
    await expect(workflow.getByRole("link", { name: /Strategy Lab/ })).toHaveAttribute("href", "/strategy-lab");
    const sidebar = page.locator(".app-sidebar");
    await expect(sidebar.getByRole("link", { name: /Market Chart/ })).toHaveAttribute("href", "/chart");
    await expect(sidebar.getByRole("link", { name: /Strategy Lab/ })).toHaveAttribute("href", "/strategy-lab");
    await expect(page.getByRole("article", { name: "Market Scan", exact: true }).getByRole("link", { name: "Market Chart" })).toHaveAttribute("href", "/chart");
    const steps = ["Command Center", "Market Scan", "Opportunities", "Strategy Lab", "Compare", "Validation",
      "Candidate readiness", "Paper evidence", "Risk Preview", "Paper", "Monitoring"];
    const nav = page.getByRole("navigation", { name: "Research workflow steps" });
    for (const [index, step] of steps.entries()) {
      await expect(nav.getByRole("link", { name: `${index + 1}. ${step}` })).toBeVisible();
      await expect(page.getByRole("article", { name: step, exact: true })).toBeVisible();
    }
    // Claims are restated on their steps; nothing on the page can validate, size or trade.
    await expect(page.getByRole("article", { name: "Strategy Lab", exact: true })).toContainText("SEARCH_NON_AUTHORITATIVE");
    await expect(page.getByRole("article", { name: "Validation", exact: true })).toContainText("NONE VALIDATED");
    await expect(page.getByRole("article", { name: "Risk Preview", exact: true })).toContainText("OR-11");
    await expect(page.getByRole("article", { name: "Paper evidence", exact: true })).toContainText("NON_COSTABLE");
    // Compare: nothing is ranked by default; the operator selects studies, the metric and any sort explicitly.
    const compare = page.getByRole("article", { name: "Compare", exact: true });
    await expect(compare.getByRole("form", { name: "Candidate comparison selection" })).toBeVisible();
    await expect(compare).toContainText("no composite score and no default ranking");
    await expect(compare.getByLabel("Sort by")).toHaveValue("");
    await expect(compare.getByRole("region", { name: "Candidate comparison table" })).toHaveCount(0);
    const studyChoices = compare.getByRole("checkbox");
    if (await studyChoices.count()) {
      await studyChoices.first().check();
      await compare.getByRole("button", { name: "Compare selected studies" }).click();
      await expect(page).toHaveURL(/compare_study=/);
      const compared = page.getByRole("article", { name: "Compare", exact: true });
      // Either the side-by-side table with its stages kept apart, an honest empty set, or the read's own state.
      await expect(compared.getByText(/Frozen candidates side by side|no frozen candidate recorded|unavailable/i).first()).toBeVisible();
      const table = compared.getByRole("region", { name: "Candidate comparison table" });
      if (await table.count()) {
        await expect(table).toContainText("SEARCH_NON_AUTHORITATIVE");
        await expect(table).toContainText("Walk-forward development");
        await expect(table).toContainText("NONE_VALIDATED");
        await expect(table).toContainText("NON_COSTABLE");
        // Paper economics are labelled as a return per unit of notional (never money); risk only for a recorded target.
        await expect(table).toContainText("Paper economics (return per unit notional)");
        await expect(table).toContainText("Paper risk (recorded target)");
        await expect(compared).toContainText("Composite score: NONE");
        // Parity and funding not loaded by this read are NOT_LOADED, never INCOMPLETE.
        await expect(page.getByRole("region", { name: "Paper evidence" })).toContainText("NOT_LOADED");
        // P7.1: any exposed price resolution keeps the server's exact status/reasons and provenance.
        for (const reference of await page.locator("[data-price-resolution]").all()) {
          const status = await reference.getAttribute("data-price-resolution");
          expect(["AVAILABLE", "UNAVAILABLE"]).toContain(status);
          await expect(reference.locator("summary")).toContainText(
            `Public trade-price reference: ${status} (not a venue settlement mark)`,
          );
          const resolution = JSON.parse((await reference.locator("pre").textContent()) ?? "{}");
          expect(resolution.status).toBe(status);
          if (status === "UNAVAILABLE") {
            expect(resolution.reasons.length).toBeGreaterThan(0);
            expect(resolution.mark).toBeNull();
          } else {
            expect(resolution.reasons).toEqual([]);
            expect(resolution.evidence.price_class).toBe("T4_PUBLIC_TRADE_NOT_VENUE_SETTLEMENT_MARK");
            expect(resolution.evidence.as_of_micros).toBeGreaterThanOrEqual(resolution.mark.knowable_at_micros);
            expect(resolution.selection_policy.policy_hash).toMatch(/^[a-f0-9]{64}$/);
          }
          for (const reason of resolution.reasons) await expect(reference).toContainText(reason);
        }
      }
    }
    // Activation readiness: every next action answered READY/BLOCKED with its owner gate.
    const readiness = page.getByRole("region", { name: "Activation readiness" });
    await expect(readiness).toContainText("Can the holdout open?");
    await expect(readiness).toContainText("Can paper incubation run?");
    await expect(readiness).toContainText("(OR-7)");
    // Commands are gated by the same readiness: paper incubation cannot be queued while OR-7/OR-11 are open.
    const console_ = page.getByRole("form", { name: "Terminal command console" });
    await expect(console_).toBeVisible();
    await console_.getByLabel("Command kind").selectOption("PAPER_INCUBATION_START");
    await expect(console_.getByRole("button", { name: "Queue command (blocked)" })).toBeDisabled();
    await expect(console_).toContainText("BLOCKED_OWNER_DECISION_OR_7");
    await expect(page.getByRole("button", { name: /execute|trade|buy|sell|open holdout|promote/i })).toHaveCount(0);

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
