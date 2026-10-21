import type {
  AccountPolicyResolution,
  EconomicsComponent,
  PaperEconomicsSection,
  PaperRiskView,
} from "../../lib/data-access";

/**
 * Pure presentation rules for paper economics and paper risk. The server decides every status; these
 * helpers only restate it. A figure is a return per unit of notional and is never formatted as money,
 * a percentage or a profit: it is shown as the server's exact decimal text with its unit label.
 */

/** Short unit label shown beside every economics figure. */
export const RETURN_UNIT_LABEL = "return per unit notional";

/** The exact decimal text plus its unit; never a currency symbol, a percentage or a rounded number. */
export function returnFigure(value: string | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value} (${RETURN_UNIT_LABEL})`;
}

export type EconomicsRow = { component: string; status: string; figure: string; reasons: string[] };

function componentRow(component: string, item: EconomicsComponent): EconomicsRow {
  const figure = item.status !== "VERIFIED"
    ? "—"
    : item.by_scenario
      ? Object.entries(item.by_scenario).map(([name, row]) => `${name}: ${returnFigure(row.value)}`).join("; ")
      : returnFigure(item.value);
  return { component, status: item.status, figure, reasons: item.reasons };
}

/**
 * One row per economics component, each with its own status: realized gross, unrealized evidence, fees,
 * slippage, funding and net. A section that is not PROJECTED yields one row carrying its own state and reasons.
 */
export function economicsRows(economics: PaperEconomicsSection): EconomicsRow[] {
  if (economics.status !== "PROJECTED" || !economics.gross_realized || !economics.gross_unrealized
      || !economics.fees || !economics.slippage || !economics.funding || !economics.net) {
    return [{ component: "Paper economics", status: economics.status, figure: "—", reasons: economics.reasons }];
  }
  const unrealized = economics.gross_unrealized;
  const net = economics.net;
  return [
    { component: "Realized gross (closed round trips)", status: "REALIZED", figure: returnFigure(economics.gross_realized.value), reasons: [] },
    {
      component: "Unrealized evidence (not in net)", status: unrealized.status,
      figure: unrealized.status === "AVAILABLE" ? returnFigure(unrealized.value) : "—",
      reasons: unrealized.reasons,
    },
    componentRow("Fees", economics.fees),
    componentRow("Slippage", economics.slippage),
    componentRow("Funding", economics.funding),
    {
      component: "Net (realized, costed)", status: net.status,
      figure: net.status === "CONFIRMED" && net.by_scenario
        ? Object.entries(net.by_scenario).map(([name, value]) => `${name}: ${returnFigure(value)}`).join("; ")
        : "—",
      reasons: net.reasons,
    },
  ];
}

/** The net status a summary cell shows: the module's own word, or the section's state when nothing was projected. */
export function netCostability(economics: PaperEconomicsSection): string {
  return economics.status === "PROJECTED" && economics.net ? economics.net.status : economics.status;
}

/** Proposed sensitivity rows only ever come from the server; they are labelled and never part of net. */
export function sensitivityLabel(row: { name: string; cost_bps_per_side: string; value: string; in_net: false }): string {
  return `${row.name} ${row.cost_bps_per_side} bps/side → ${returnFigure(row.value)} · PROPOSED_SENSITIVITY_SCENARIO · not in net`;
}

/** The account policy as resolved: ACTIVE only when the resolver said so, otherwise its reasons. */
export function policySummary(policy: AccountPolicyResolution | null | undefined): { status: string; reasons: string[] } {
  if (!policy) return { status: "NOT_LOADED", reasons: ["NO_ACCOUNT_RESOLVED_FOR_THIS_VIEW"] };
  return { status: policy.status, reasons: [...policy.blocking, ...policy.unresolved] };
}

/** Sizing is shown only for an ALLOWED verdict; anything else shows why it is absent. */
export function sizingText(risk: PaperRiskView): string {
  if (risk.status === "ALLOWED" && risk.sizing) {
    return Object.entries(risk.sizing).map(([key, value]) => `${key}=${value ?? "—"}`).join(", ");
  }
  const why = risk.status === "NOT_LOADED" ? ["NOT_LOADED"] : risk.sizing_absent_reasons.length ? risk.sizing_absent_reasons : [`VERDICT_${risk.status}`];
  return `no sizing (${why.join(", ")})`;
}

type StoredEvidence = {
  status: string; loaded: boolean; reasons: string[]; evidence_as_of?: string | null; recorded_at?: string | null;
  window_start?: string | null; window_end?: string | null;
};

/**
 * What a stored-evidence status means, never conflated: a loaded row (verbatim status, with its own instants),
 * NOT_LOADED (nothing stored for the candidate in the cycle) or REFUSED (a stored row that does not re-derive).
 */
export function storedEvidenceText(evidence: StoredEvidence): string {
  if (evidence.status === "REFUSED") return "stored row refused: it does not re-derive (not shown, not treated as absent)";
  if (!evidence.loaded) return "nothing stored for this candidate in the cycle";
  const window = evidence.window_start || evidence.window_end
    ? `; window ${evidence.window_start ?? "—"} → ${evidence.window_end ?? "—"}` : "";
  return `stored evidence as of ${evidence.evidence_as_of ?? "—"}, recorded ${evidence.recorded_at ?? "—"}${window}`;
}

/** The recorded target a risk view assessed, or why none was assessed. */
export function targetText(risk: PaperRiskView): string {
  const target = risk.requested_target;
  if (!target) return "no recorded target";
  return `${target.symbol} ${target.target_from} → ${target.target_to} (stored signal decided ${target.decided_at})`;
}
