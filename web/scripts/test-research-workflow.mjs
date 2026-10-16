import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

import { RESEARCH_WORKFLOW, currentWorkflowStep } from "../app/components/research-workflow.ts";

// Stage B.1.3a: the primary workflow, in order, every step reachable without typing a URL.
assert.deepEqual(RESEARCH_WORKFLOW.map((step) => step.label), ["Research Terminal", "Market Chart", "Strategy Lab", "Validation & Paper"]);

// Every step points at a page that exists; no link to functionality that is not there.
for (const step of RESEARCH_WORKFLOW) {
  const [path, anchor] = step.href.split("#");
  const page = new URL(`../app/(protected)${path}/page.tsx`, import.meta.url);
  assert.ok(existsSync(page), `${step.label}: ${path} has no page`);
  if (anchor) assert.match(readFileSync(page, "utf8"), new RegExp(`id="${anchor}"|\\["${anchor}",`), `${step.label}: #${anchor} is not on ${path}`);
}

// The chart step says what its data is: historical, never live prices.
assert.match(RESEARCH_WORKFLOW.find((step) => step.id === "chart").kind, /Historical.*not live prices/);

// Active page: exact route or a sub-route, never a prefix of another route, never an anchor step.
assert.equal(currentWorkflowStep("/terminal"), "terminal");
assert.equal(currentWorkflowStep("/chart"), "chart");
assert.equal(currentWorkflowStep("/strategy-lab"), "strategy-lab");
assert.equal(currentWorkflowStep("/strategies"), undefined);
assert.equal(currentWorkflowStep("/charting"), undefined);
assert.equal(currentWorkflowStep("/dashboard"), undefined);
assert.ok(!RESEARCH_WORKFLOW.some((step) => step.id === currentWorkflowStep("/terminal") && step.href.includes("#")));

// The sidebar lists workflow pages once, in the workflow group, not again under another group.
const sidebar = readFileSync(new URL("../app/components/sidebar.tsx", import.meta.url), "utf8");
const groups = sidebar.slice(sidebar.indexOf("const NAVIGATION_GROUPS"), sidebar.indexOf("export function Sidebar"));
for (const step of RESEARCH_WORKFLOW) {
  assert.ok(!groups.includes(`href: "${step.href}"`), `${step.href} is repeated outside the workflow group`);
}

console.log("research workflow navigation: ok");
