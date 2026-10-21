import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  RETURN_UNIT_LABEL,
  economicsRows,
  netCostability,
  policySummary,
  returnFigure,
  sensitivityLabel,
  sizingText,
  storedEvidenceText,
  targetText,
} from "../app/(protected)/terminal/paper-economics.ts";

// Terminal paper economics and risk: the server decides every status; the terminal restates it.
const UNITS = "RETURN_PER_UNIT_NOTIONAL_SUMMED_NOT_COMPOUNDED_PENDING_OR_11";

function section(overrides = {}) {
  return {
    status: "PROJECTED", reasons: [], units: UNITS, money: "NONE_NOT_A_CURRENCY_AMOUNT",
    sign_convention: "SIGNED_CONTRIBUTION_TO_PNL_POSITIVE_ADDS_COSTS_NONPOSITIVE", economics_hash: "a".repeat(64),
    economics_id: "00000000-0000-4000-8000-000000000000", symbol: "ETHUSDT", cost_policy: null, cost_approval: null,
    exposure: null, gross_realized: { value: "0.012300000000", basis: "CLOSED_ROUND_TRIPS_UNIT_EXPOSURE_FILL_TO_FILL" },
    gross_unrealized: { status: "UNAVAILABLE", value: null, reasons: ["NO_PROVEN_MARK_SUPPLIED"], exposure: 1, mark: null,
      detail: {}, in_net: false },
    unrealized_mark_source: "NO_STORED_PROVEN_T4_MARK_SOURCE_READ_BY_THIS_TERMINAL",
    fees: { status: "MISSING", value: null, by_scenario: null, reasons: ["MISSING_VERIFIED_FEE_SCHEDULE_OR_6"], detail: {} },
    slippage: { status: "MISSING", value: null, by_scenario: null, reasons: ["SLIPPAGE_ENVELOPE_NOT_OWNER_APPROVED_OR_6"], detail: {} },
    funding: { status: "NON_COSTABLE_EVIDENCE", value: null, by_scenario: null, reasons: ["FUNDING_EVIDENCE_IS_NEVER_A_CHARGE_IN_V1"], detail: {} },
    sensitivity_rows: [], net: { status: "NOT_COSTABLE", scope: "CLOSED_ROUND_TRIPS_REALIZED_ONLY_UNREALIZED_EXCLUDED",
      by_scenario: null, reasons: ["COMPONENT_NOT_VERIFIED:fees:MISSING_VERIFIED_FEE_SCHEDULE_OR_6"] },
    settlement_boundaries: null, provenance: null, claim: "PAPER_ECONOMICS_PROJECTION_RESEARCH_ONLY_NOT_VALIDATED",
    ...overrides,
  };
}

// Units: the exact server decimal plus its unit label; never a currency symbol, a percentage or rounding.
assert.equal(returnFigure("-0.000500000000"), `-0.000500000000 (${RETURN_UNIT_LABEL})`);
assert.equal(returnFigure(null), "—");
assert.doesNotMatch(returnFigure("0.0123"), /[$€£%]|USD|profit/i);

// Each component keeps its own status; realized and unrealized are apart; no figure without VERIFIED.
const rows = economicsRows(section());
assert.deepEqual(rows.map((row) => [row.component, row.status]), [
  ["Realized gross (closed round trips)", "REALIZED"],
  ["Unrealized evidence (not in net)", "UNAVAILABLE"],
  ["Fees", "MISSING"],
  ["Slippage", "MISSING"],
  ["Funding", "NON_COSTABLE_EVIDENCE"],
  ["Net (realized, costed)", "NOT_COSTABLE"],
]);
assert.equal(rows[0].figure, `0.012300000000 (${RETURN_UNIT_LABEL})`);
assert.ok(rows.slice(1).every((row) => row.figure === "—"));
assert.deepEqual(rows[1].reasons, ["NO_PROVEN_MARK_SUPPLIED"]);
for (const row of rows.slice(2)) assert.ok(row.reasons.length, `${row.component} states its reasons`);

// A verified component shows its figure (per scenario for slippage); a confirmed net shows per-scenario figures.
const verified = economicsRows(section({
  fees: { status: "VERIFIED", value: "-0.001100000000", by_scenario: null, reasons: [], detail: {} },
  slippage: { status: "VERIFIED", value: null, by_scenario: { stress: { cost_bps_per_side: "2", value: "-0.000400000000" } }, reasons: [], detail: {} },
}));
assert.equal(verified[2].figure, `-0.001100000000 (${RETURN_UNIT_LABEL})`);
assert.equal(verified[3].figure, `stress: -0.000400000000 (${RETURN_UNIT_LABEL})`);
assert.equal(verified[5].figure, "—", "net stays without a figure unless CONFIRMED");

// NOT_LOADED, REFUSED and the net statuses are never conflated.
assert.equal(netCostability(section()), "NOT_COSTABLE");
for (const status of ["NOT_LOADED", "REFUSED"]) {
  const empty = section({ status, reasons: ["CANDIDATE_HAS_NO_PAPER_FILL_IN_THE_CURRENT_CYCLE"], gross_realized: null,
    gross_unrealized: null, fees: null, slippage: null, funding: null, net: null, economics_hash: null });
  assert.equal(netCostability(empty), status);
  assert.deepEqual(economicsRows(empty), [{ component: "Paper economics", status, figure: "—",
    reasons: ["CANDIDATE_HAS_NO_PAPER_FILL_IN_THE_CURRENT_CYCLE"] }]);
}
assert.equal(netCostability(section({ net: { status: "INCOMPLETE", scope: "x", by_scenario: null, reasons: ["POSITION_CHAIN_BROKEN"] } })), "INCOMPLETE");

// Proposed sensitivity rows are labelled and never in net.
assert.match(sensitivityLabel({ name: "conservative-medium-mid", cost_bps_per_side: "2.5", value: "-0.0005", in_net: false }),
  /PROPOSED_SENSITIVITY_SCENARIO · not in net$/);

// Risk: sizing only when ALLOWED; NOT_LOADED and verdicts stay distinct; no target is invented.
const notLoaded = { status: "NOT_LOADED", loaded: false, reasons: ["NO_STORED_INCUBATING_SIGNAL_TARGET_FOR_CANDIDATE_IN_CYCLE"],
  requested_target: null, sizing: null, sizing_absent_reasons: [] };
assert.equal(sizingText(notLoaded), "no sizing (NOT_LOADED)");
assert.equal(targetText(notLoaded), "no recorded target");
const target = { source: "LATEST_STORED_INCUBATING_SIGNAL", signal_id: "s", symbol: "ETHUSDT", study_id: "a", trial_id: "b",
  target_from: 0, target_to: 1, decided_at: "2026-09-02T00:00:01+00:00" };
assert.equal(sizingText({ ...notLoaded, status: "UNRESOLVED", loaded: true, requested_target: target,
  sizing: { open_quantity: "1" }, sizing_absent_reasons: ["VERDICT_UNRESOLVED"] }), "no sizing (VERDICT_UNRESOLVED)");
assert.equal(sizingText({ ...notLoaded, status: "ALLOWED", loaded: true, requested_target: target,
  sizing: { basis: "CLOSE_HELD_POSITION", close_quantity: "-1" } }), "basis=CLOSE_HELD_POSITION, close_quantity=-1");
assert.match(targetText({ ...notLoaded, requested_target: target }), /ETHUSDT 0 → 1 \(stored signal decided/);

// An unconfigured policy is never presented as active; its missing owner values are listed.
assert.deepEqual(policySummary({ status: "UNRESOLVED", blocking: [], unresolved: ["ACCOUNT_POLICY_UNCONFIGURED", "MISSING_OWNER_PAPER_STARTING_CAPITAL_OR_11"] }),
  { status: "UNRESOLVED", reasons: ["ACCOUNT_POLICY_UNCONFIGURED", "MISSING_OWNER_PAPER_STARTING_CAPITAL_OR_11"] });
assert.deepEqual(policySummary(null), { status: "NOT_LOADED", reasons: ["NO_ACCOUNT_RESOLVED_FOR_THIS_VIEW"] });

// Stored parity/funding: a loaded row shows its own instants; NOT_LOADED and REFUSED read differently.
assert.equal(storedEvidenceText({ status: "INCOMPLETE", loaded: true, reasons: ["R"], evidence_as_of: "2026-08-02T00:00:00Z",
  recorded_at: "2026-08-02T01:00:00Z", window_start: "2026-08-01T00:00:00Z", window_end: "2026-08-01T01:29:00Z" }),
"stored evidence as of 2026-08-02T00:00:00Z, recorded 2026-08-02T01:00:00Z; window 2026-08-01T00:00:00Z → 2026-08-01T01:29:00Z");
assert.equal(storedEvidenceText({ status: "NOT_LOADED", loaded: false, reasons: ["NO_STORED_PARITY_ASSESSMENT_FOR_CANDIDATE_IN_CYCLE"] }),
  "nothing stored for this candidate in the cycle");
assert.match(storedEvidenceText({ status: "REFUSED", loaded: false, reasons: ["STORED_EVIDENCE_REFUSED:X"] }), /^stored row refused/);
assert.notEqual(storedEvidenceText({ status: "REFUSED", loaded: false, reasons: [] }),
  storedEvidenceText({ status: "NOT_LOADED", loaded: false, reasons: [] }));

// The terminal never hard-codes an economic number: no bps scenario literal in the terminal sources.
for (const file of ["paper-economics.ts", "compare-section.tsx", "page.tsx"]) {
  const source = readFileSync(new URL(`../app/(protected)/terminal/${file}`, import.meta.url), "utf8");
  assert.doesNotMatch(source, /CONSERVATIVE_MEDIUM|\b2\.5\s*bps|toLocaleString\(|style:\s*["']currency|Intl\.NumberFormat/, file);
}

console.log("terminal paper economics and risk: ok");
