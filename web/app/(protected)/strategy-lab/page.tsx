import React from "react";
import Link from "next/link";
import {
  getChartSeriesCatalog,
  getStrategyLabBreakEvenDistribution,
  getStrategyLabHeatmap,
  getStrategyLabStudies,
  getStrategyLabStudy,
  getStrategyLabTrial,
  getStrategyLabTrials,
  getWorkspaceContext,
  stateText,
  utc,
  type ChartSeriesRef,
  type StrategyLabBreakEvenDistribution,
  type StrategyLabDatasetRef,
  type StrategyLabEconomics,
  type StrategyLabHeatmap,
  type StrategyLabStudyDetail,
  type StrategyLabTrial,
  type StrategyLabTrialQuery,
} from "../../lib/data-access";
import type { EvidenceResult as Evidence } from "../../operator-contracts";
import { BreakEvenView, EvidenceLine, HeatmapView } from "./visual-analytics";
import { crossViewMismatch, evidenceMismatch, type StudyOnScreen } from "./evidence-consistency";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { DataTable } from "../../components/data-table";
import { KeyValueGrid } from "../../components/key-value-grid";
import { Pagination } from "../../components/pagination";
import { ProvenancePanel } from "../../components/provenance-panel";
import { ResearchWorkflowNav } from "../../components/research-workflow-nav";
import { candidateHref, researchChart, researchChartHref, terminalContext, terminalQuery, type CompareSelection } from "../terminal/compare-section";
import { terminalSymbol } from "../terminal/symbol-navigation";

export const dynamic = "force-dynamic";

/**
 * Stage B.1.2a Strategy Lab Explorer: a read-only window onto persisted studies and trials.
 *
 * Every number on this page is the stored text, rendered verbatim: nothing is parsed into
 * a JavaScript number, rounded or recomputed. Sorting, filtering and paging run on the
 * server. Search-tier results are gross and SEARCH_NON_AUTHORITATIVE; a Decimal rerun makes
 * a selection authoritative, never the economics. Verified costs and net profitability are
 * UNAVAILABLE (no verified fee schedule, OR-6) and are never shown as zero.
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const FAMILY = /^[a-z][a-z0-9_]*$/;
const SYMBOL = /^[A-Z0-9]{1,32}$/;
const SORT = /^(ordinal|[a-z][a-z0-9_]{0,63})$/;
const OUTCOMES = ["ALL", "EVALUATED", "INADMISSIBLE_PARAMETERS", "PENDING"] as const;
const PAGE_SIZES = [10, 25, 50, 100] as const;
const DEFAULT_PAGE_SIZE = 25;
/** Columns shown in the trial table when the study records them; every key is in the trial detail. */
const PREFERRED_METRICS = [
  "total_return", "sharpe_daily_annualized", "max_drawdown", "trades", "break_even_bps_per_side",
  "positive_month_fraction", "worst_month_return", "funding_window_crossings",
] as const;
const DESCRIPTIVE_KEYS = new Set(["cost_mode", "numeric_tier", "reason"]);
const METRIC_LABELS: Record<string, string> = {
  total_return: "Gross total return",
  sharpe_daily_annualized: "Gross Sharpe (daily, annualized)",
  max_drawdown: "Gross max drawdown",
  trades: "Trades",
  break_even_bps_per_side: "Break-even cost (bps/side)",
  positive_month_fraction: "Positive months",
  worst_month_return: "Gross worst month",
  funding_window_crossings: "Funding windows crossed",
  exposure: "Exposure",
  turnover: "Turnover",
  bars_evaluated: "Bars evaluated",
  near_tie_decisions: "Near-tie decisions",
};

type Params = {
  terminal_context?: string;
  study?: string; family?: string; instrument?: string; study_offset?: string;
  sort?: string; dir?: string; outcome?: string; frozen?: string; offset?: string; limit?: string; trial?: string;
  /** Heatmap: x and y parameters, metric, and fixed values as comma-joined ``name:value`` pairs. */
  hx?: string; hy?: string; hm?: string; hf?: string;
};
type RawParams = Omit<Params, "hf"> & { hf?: string | string[] };

const PARAMETER = /^[a-z][a-z0-9_]{0,63}$/;
const FIXED_PAIR = /^([a-z][a-z0-9_]{0,63}):([A-Za-z0-9_.-]{1,64})$/;

function pick(value: string | undefined, pattern: RegExp): string | undefined {
  return value && pattern.test(value) ? value : undefined;
}

/**
 * Fixed heatmap values from the URL, exactly as given. Malformed pairs and pairs naming a
 * current axis are dropped (an axis cannot also be fixed); nothing is defaulted, and a
 * repeated name is passed on so the server refuses it rather than this page choosing one.
 */
function fixedPairs(raw: string | string[] | undefined, axes: (string | undefined)[]): [string, string][] {
  const items = (Array.isArray(raw) ? raw : raw ? [raw] : []).flatMap((value) => value.split(","));
  const pairs: [string, string][] = [];
  for (const item of items) {
    const match = FIXED_PAIR.exec(item);
    if (match && !axes.includes(match[1])) pairs.push([match[1], match[2]]);
  }
  return pairs;
}

/** Hidden inputs carrying the page state a form does not itself edit. */
function Carry({ params, keys }: { params: Params; keys: (keyof Params)[] }) {
  return <>{params.terminal_context && !keys.includes("terminal_context")
    ? <input type="hidden" name="terminal_context" value={params.terminal_context} /> : null}
    {keys.map((key) => (params[key] ? <input key={key} type="hidden" name={key} value={params[key]} /> : null))}</>;
}

function boundedInt(value: string | undefined, max: number): number {
  const parsed = value && /^\d{1,6}$/.test(value) ? Number(value) : 0;
  return Math.min(parsed, max);
}

function metricLabel(key: string): string {
  return METRIC_LABELS[key] ?? key;
}

function lagText(micros: number | string): string {
  const value = typeof micros === "number" ? micros : /^\d+$/.test(micros) ? Number(micros) : NaN;
  return Number.isInteger(value) ? `${value / 1_000_000} s` : String(micros);
}

function counts(record: Record<string, number> | undefined): string {
  const entries = Object.entries(record ?? {});
  return entries.length ? entries.map(([key, value]) => `${key} ${value}`).join(" · ") : "none recorded";
}

function Claim({ label }: { label: string }) {
  return <span className="badge" data-claim={label}>{label}</span>;
}

function short(id: string): string {
  return `${id.slice(0, 8)}…`;
}

/** The stored text, verbatim; a missing value is named, never zero. */
function Stored({ value }: { value: string | null | undefined }) {
  return value === null || value === undefined ? <span className="chart-option-unavailable">UNAVAILABLE</span> : <>{value}</>;
}

function href(params: Params, overrides: Partial<Params>): string {
  const merged: Record<string, string | undefined> = { ...params, ...overrides };
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(merged)) if (value) query.set(key, value);
  const text = query.toString();
  return text ? `/strategy-lab?${text}` : "/strategy-lab";
}

/**
 * Market Chart links for the research windows a study is bound to. A window is linked only
 * when the chart catalogue carries the exact same ``dataset_version_id`` and window identity;
 * anything else is named as not charted rather than pointed at a different series.
 */
function StudyDataLinks({ datasets, charts, selection }: { datasets: StrategyLabDatasetRef[]; charts: ChartSeriesRef[] | undefined; selection: CompareSelection }) {
  const windows = datasets.filter((ref) => ref.resolution === "RESEARCH_WINDOW_CATALOGUE");
  return (
    <ul className="context-links" aria-label="Study data on the Market Chart">
      {windows.length ? windows.map((ref) => {
        const chart = researchChart(charts, ref.symbol ?? undefined, undefined, ref);
        const series = chart.series;
        const name = `${ref.symbol ?? "UNRESOLVED"} ${ref.first_utc_day}..${ref.last_utc_day}`;
        return (
          <li key={`${ref.dataset_version_id}:${ref.content_hash}`}>
            {series
              ? <><Link href={researchChartHref(series, selection)}>Historical bars for {name} on the Market Chart</Link> (not live prices)</>
              : <span className="chart-option-unavailable">{name}: not charted ({chart.reason})</span>}
          </li>
        );
      }) : <li className="chart-option-unavailable">No research window resolved for this study: nothing to chart.</li>}
      <li><a href={`/terminal?${terminalQuery(selection)}#validation`}>Recorded validation status and blockers (Research Terminal)</a></li>
    </ul>
  );
}

function EconomicsTable({ economics }: { economics: StrategyLabEconomics }) {
  const scenarios = economics.hypothetical_slippage_scenarios;
  return (
    <DataTable caption="What these results are: gross, hypothetical, verified, net" ariaLabel="Economics">
      <thead><tr><th scope="col">Layer</th><th scope="col">State</th><th scope="col">What it means here</th></tr></thead>
      <tbody>
        <tr data-economics="gross">
          <td>Gross results</td><td><Claim label="GROSS" /></td>
          <td>Every recorded return, Sharpe and drawdown is gross of fees, spread, slippage and funding (cost mode <code>{economics.cost_mode}</code>). A positive gross number is not a profit.</td>
        </tr>
        <tr data-economics="hypothetical">
          <td>Hypothetical costs</td><td><Claim label={economics.hypothetical_costs === "BOUND_TO_STUDY" ? "HYPOTHETICAL" : "NONE BOUND"} /></td>
          <td>
            {scenarios.length
              ? <>Slippage scenarios bound to this study (owner-named, hypothetical): <code>{JSON.stringify(scenarios)}</code>.</>
              : <>No slippage scenario is bound to this study and none is approved (OR-6).</>}{" "}
            {economics.break_even_metric
              ? <>Each trial records <code>{economics.break_even_metric}</code>: the per-side cost at which its gross result would be zero — a sensitivity figure, not a cost estimate.</>
              : null}
          </td>
        </tr>
        <tr data-economics="verified">
          <td>Verified costs</td><td><Claim label={economics.verified_costs} /></td>
          <td>{economics.verified_cost_reasons.join(", ")}. Fill liquidity {economics.fill_liquidity ?? "UNAVAILABLE"}; funding <code>{economics.funding ?? "UNAVAILABLE"}</code>.</td>
        </tr>
        <tr data-economics="net">
          <td>Net profitability</td><td><Claim label={economics.net_profitability} /></td>
          <td>Not computed and not shown: {economics.net_profitability_reasons.join(", ")}.</td>
        </tr>
      </tbody>
    </DataTable>
  );
}

function FrozenCandidates({ detail, params }: { detail: StrategyLabStudyDetail; params: Params }) {
  const set = detail.candidate_sets.at(-1);
  const rerun = detail.reruns.filter((item) => !set || item.candidate_set_hash === set.candidate_set_hash).at(-1);
  if (!set) return <p className="empty-notice">No frozen candidate set is recorded for this study.</p>;
  const decimal = new Map((rerun?.selected ?? []).map((item) => [item.trial_id, item]));
  const states = new Map(detail.candidate_states.map((item) => [item.trial_id, item]));
  const metric = String(set.rule.metric ?? "metric");
  return (
    <>
      <p>
        Frozen top-{String(set.rule.top_k ?? set.candidates.length)} by <code>{metric}</code> ({String(set.rule.direction ?? "")}), {set.eligible_count} eligible
        of {set.multiple_testing_trial_count} trials tested (multiple-testing count), cutoff tie: {set.cutoff_tie ? "yes" : "no"}.
        Frozen <time dateTime={set.recorded_at}>{utc(set.recorded_at)}</time>; set <code>{set.candidate_set_hash.slice(0, 12)}</code>.
      </p>
      {rerun ? (
        <p data-rerun={rerun.claim}>
          Decimal authority rerun <code>{rerun.rerun_hash.slice(0, 12)}</code>: <Claim label={rerun.claim} />{" "}
          over {rerun.rerun_count} trials at baseline lag {rerun.baseline_lag_micros === null ? "UNAVAILABLE" : lagText(rerun.baseline_lag_micros)} and
          sweep lags {rerun.sweep_lags_micros.map(lagText).join(", ") || "none"}. Reconciliation: {counts(rerun.reconciliation)};
          held-bar divergence {rerun.held_divergence_bars}, trade-count divergence {rerun.trade_count_divergence}.
          Selection changed versus search: {rerun.changed_versus_search_selection === null ? "UNAVAILABLE" : rerun.changed_versus_search_selection ? "yes" : "no"}.
          Economics: <Claim label={rerun.economics} />{rerun.claim_ceiling ? <> (ceiling <code>{rerun.claim_ceiling}</code>)</> : null}.
          {rerun.reason ? <> Reason: <code>{rerun.reason}</code>.</> : null}
        </p>
      ) : (
        <p className="empty-notice">No Decimal authority rerun is recorded for this set: its ranks are search-tier only.</p>
      )}
      <DataTable caption="Frozen candidates" ariaLabel="Frozen candidates">
        <thead>
          <tr>
            <th scope="col">Search rank</th><th scope="col">Trial</th><th scope="col">Search {metric} (float64)</th>
            <th scope="col">Decimal rank</th><th scope="col">Decimal {metric}</th><th scope="col">Recorded state</th>
          </tr>
        </thead>
        <tbody>
          {set.candidates.map((candidate) => {
            const chosen = decimal.get(candidate.trial_id);
            const state = states.get(candidate.trial_id);
            return (
              <tr key={candidate.trial_id}>
                <td className="tabular-num">{candidate.rank}</td>
                <td><Link href={href(params, { trial: candidate.trial_id })}><code title={candidate.trial_id}>{short(candidate.trial_id)}</code></Link></td>
                <td className="tabular-num">{candidate.metric_value}</td>
                <td className="tabular-num">{chosen ? chosen.rank : rerun?.claim === "DECIMAL_SELECTION_ESTABLISHED" ? "not selected" : "UNAVAILABLE"}</td>
                <td className="tabular-num"><Stored value={chosen?.metric_value} /></td>
                <td>{state ? <Claim label={state.state} /> : <span>NOT VALIDATED (no holdout result)</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </DataTable>
    </>
  );
}

function SortHeader({ label, sortKey, query, params }: { label: string; sortKey: string; query: StrategyLabTrialQuery; params: Params }) {
  const active = query.sort === sortKey;
  const next = active && query.direction === "desc" ? "asc" : "desc";
  return (
    <th scope="col" aria-sort={active ? (query.direction === "asc" ? "ascending" : "descending") : "none"}>
      <Link href={href(params, { sort: sortKey, dir: sortKey === "ordinal" && !active ? "asc" : next, offset: undefined, trial: undefined })}>
        {label}{active ? (query.direction === "asc" ? " ▲" : " ▼") : ""}
      </Link>
    </th>
  );
}

function TrialRow({ trial, columns, params }: { trial: StrategyLabTrial; columns: string[]; params: Params }) {
  return (
    <tr data-outcome={trial.outcome}>
      <td className="tabular-num"><Link href={href(params, { trial: trial.trial_id })}>{trial.ordinal}</Link></td>
      <td>{Object.entries(trial.parameters).map(([key, value]) => `${key}=${value}`).join(", ")}</td>
      <td><Claim label={trial.outcome} /></td>
      {trial.outcome === "EVALUATED"
        ? columns.map((key) => <td key={key} className="tabular-num"><Stored value={trial.metrics[key]} /></td>)
        : <td colSpan={columns.length}>{trial.outcome === "PENDING" ? "No stored result yet." : `Not evaluated: ${trial.metrics.reason ?? "no reason recorded"}`}</td>}
      <td className="tabular-num">{trial.frozen_rank ?? "—"}</td>
      <td>
        {trial.rerun
          ? <>{trial.rerun.selection_status !== "ESTABLISHED"
            ? "selection NOT ESTABLISHED (fail closed)"
            : trial.rerun.decimal_rank !== null ? `Decimal #${trial.rerun.decimal_rank}` : "rerun, not selected"} · {[...new Set(Object.values(trial.rerun.reconciliation_by_lag))].join("/")}</>
          : "—"}
      </td>
    </tr>
  );
}

function HeatmapPanel({ detail, study, metricKeys, params, fixed, result, crossMismatch }: {
  detail: StrategyLabStudyDetail | undefined; study: StudyOnScreen; metricKeys: string[]; params: Params;
  fixed: [string, string][]; result: Evidence<StrategyLabHeatmap> | undefined; crossMismatch: string | null;
}) {
  const dimensions = detail?.parameter_space.map((dim) => ({ name: String(dim.name), values: (dim.values ?? []).map(String) })) ?? [];
  const fixedByName = new Map(fixed);
  const heatmap = result?.state === "AVAILABLE" ? result.value : undefined;
  const mismatch = heatmap ? evidenceMismatch(heatmap.evidence, study) : null;
  return (
    <article className="panel margin-bottom-24" aria-label="Parameter heatmap" id="heatmap">
      <h2><span>Parameter heatmap</span><Claim label="SEARCH_NON_AUTHORITATIVE · GROSS" /></h2>
      {dimensions.length < 2 ? (
        <p className="empty-notice">UNAVAILABLE: this study records {dimensions.length} parameter{dimensions.length === 1 ? "" : "s"}; a heatmap needs two.</p>
      ) : (
        <form method="GET" action="/strategy-lab#heatmap" className="filter-bar" aria-label="Heatmap selection">
          <Carry params={params} keys={["study", "family", "instrument", "study_offset", "sort", "dir", "outcome", "frozen", "offset", "limit"]} />
          <div className="filter-groups">
            <div className="filter-item">
              <label htmlFor="heatmap-x" className="filter-label">X parameter</label>
              <select id="heatmap-x" name="hx" defaultValue={params.hx ?? ""} className="filter-select">
                <option value="">— choose —</option>
                {dimensions.map((dim) => <option key={dim.name} value={dim.name}>{dim.name}</option>)}
              </select>
            </div>
            <div className="filter-item">
              <label htmlFor="heatmap-y" className="filter-label">Y parameter</label>
              <select id="heatmap-y" name="hy" defaultValue={params.hy ?? ""} className="filter-select">
                <option value="">— choose —</option>
                {dimensions.map((dim) => <option key={dim.name} value={dim.name}>{dim.name}</option>)}
              </select>
            </div>
            <div className="filter-item">
              <label htmlFor="heatmap-metric" className="filter-label">Stored metric</label>
              <select id="heatmap-metric" name="hm" defaultValue={params.hm ?? ""} className="filter-select">
                <option value="">— choose —</option>
                {metricKeys.map((key) => <option key={key} value={key}>{metricLabel(key)}</option>)}
              </select>
            </div>
            {dimensions.filter((dim) => dim.name !== params.hx && dim.name !== params.hy).map((dim) => (
              <div className="filter-item" key={dim.name}>
                <label htmlFor={`heatmap-fixed-${dim.name}`} className="filter-label">Fixed {dim.name}</label>
                <select id={`heatmap-fixed-${dim.name}`} name="hf" defaultValue={fixedByName.has(dim.name) ? `${dim.name}:${fixedByName.get(dim.name)}` : ""} className="filter-select">
                  <option value="">— choose —</option>
                  {dim.values.map((value) => <option key={value} value={`${dim.name}:${value}`}>{value}</option>)}
                </select>
              </div>
            ))}
          </div>
          <div className="filter-actions">
            <button type="submit" className="filter-apply-btn">Show heatmap</button>
          </div>
        </form>
      )}
      <p>
        Choose two recorded parameters and a stored metric. Every other parameter must be fixed to one recorded value: nothing is
        defaulted, no best trial is picked and no configurations are averaged. After changing an axis, submit again to fix the
        parameter it released.
      </p>
      {!result ? (
        dimensions.length >= 2 ? <p className="empty-notice">No heatmap selected.</p> : null
      ) : !heatmap ? (
        <p className="empty-notice">{stateText(result)}</p>
      ) : mismatch ? (
        <p className="empty-notice" data-evidence="MISMATCH">EVIDENCE MISMATCH: {mismatch}; the heatmap is not shown.</p>
      ) : crossMismatch ? (
        <p className="empty-notice" data-evidence="RESULTS_CHANGED">RESULTS CHANGED: {crossMismatch}; neither view is shown. Reload the page.</p>
      ) : (
        <>
          <EvidenceLine evidence={heatmap.evidence} />
          {heatmap.state === "AVAILABLE" ? (
            <HeatmapView heatmap={heatmap} metricLabel={metricLabel(heatmap.metric ?? "")}
              trialHref={(trial) => `${href(params, { trial })}#trial-detail`} />
          ) : (
            <p className="empty-notice" data-heatmap-state={heatmap.state}>
              {heatmap.state === "SELECTION_REQUIRED" ? "Selection incomplete" : heatmap.state === "SELECTION_INVALID" ? "Selection invalid" : "UNAVAILABLE"}:{" "}
              {heatmap.reasons.join(", ")}.
            </p>
          )}
        </>
      )}
    </article>
  );
}

function BreakEvenPanel({ study, result, crossMismatch }: {
  study: StudyOnScreen; result: Evidence<StrategyLabBreakEvenDistribution> | undefined; crossMismatch: string | null;
}) {
  const distribution = result?.state === "AVAILABLE" ? result.value : undefined;
  const mismatch = distribution ? evidenceMismatch(distribution.evidence, study) : null;
  return (
    <article className="panel margin-bottom-24" aria-label="Break-even cost distribution" id="break-even">
      <h2>
        <span>Break-even cost distribution</span>
        <Claim label="GROSS-RESULT COST SENSITIVITY" />
      </h2>
      <p>
        Each evaluated trial records <code>break_even_bps_per_side</code>: the per-side cost at which its <em>gross</em> search-tier
        result would be zero. It is a sensitivity of a gross result, not a verified cost, a fee estimate or net profitability. No fee,
        spread, slippage or funding assumption is applied here. Verified costs: <Claim label="UNAVAILABLE" /> Net profitability: <Claim label="UNAVAILABLE" />
      </p>
      {!result ? <p className="empty-notice">UNAVAILABLE</p> : !distribution ? (
        <p className="empty-notice">{stateText(result)}</p>
      ) : mismatch ? (
        <p className="empty-notice" data-evidence="MISMATCH">EVIDENCE MISMATCH: {mismatch}; the distribution is not shown.</p>
      ) : crossMismatch ? (
        <p className="empty-notice" data-evidence="RESULTS_CHANGED">RESULTS CHANGED: {crossMismatch}; neither view is shown. Reload the page.</p>
      ) : (
        <>
          <EvidenceLine evidence={distribution.evidence} />
          {distribution.state === "AVAILABLE" ? <BreakEvenView distribution={distribution} /> : (
            <p className="empty-notice">UNAVAILABLE: {distribution.reasons.join(", ")}. Recorded outcomes: {counts(distribution.trial_outcomes)}.</p>
          )}
        </>
      )}
    </article>
  );
}

async function TrialDetailPanel({ studyId, trialId, ctx, params }: {
  studyId: string; trialId: string; ctx: Awaited<ReturnType<typeof getWorkspaceContext>>; params: Params;
}) {
  const result = await getStrategyLabTrial(ctx, studyId, trialId);
  const detail = result.state === "AVAILABLE" ? result.value : undefined;
  return (
    <article className="panel margin-bottom-24" aria-label="Trial Detail" id="trial-detail">
      <h2>
        <span>Trial {detail ? `#${detail.trial.ordinal}` : short(trialId)}</span>
        <Claim label="SEARCH_NON_AUTHORITATIVE" />
      </h2>
      <p><Link href={href(params, { trial: undefined })}>Close trial detail</Link></p>
      {detail ? (
        <>
          <nav aria-label="Trial in Research Terminal">
            <a href={candidateHref(studyId, detail.trial.trial_id, terminalContext(params.terminal_context))}>Review candidate, risk and paper</a>{" · "}
            <a href={`/terminal?${terminalQuery({...terminalContext(params.terminal_context), paperStudy: studyId, paperTrial: detail.trial.trial_id})}#validation`}>Validation status with this candidate selected</a>
          </nav>
          <KeyValueGrid
            columns={3}
            items={[
              { key: "outcome", label: "Outcome", value: detail.trial.outcome },
              { key: "frozen", label: "Frozen rank", value: detail.trial.frozen_rank ?? "not frozen" },
              { key: "state", label: "Recorded candidate state", value: detail.trial.candidate_state ?? "none (not validated)" },
              { key: "trial", label: "Trial content hash", value: <code>{detail.trial.trial_content_hash}</code> },
              { key: "result", label: "Result content hash", value: <code>{detail.trial.result_content_hash ?? "UNAVAILABLE"}</code> },
              { key: "produced", label: "Result produced (UTC)", value: utc(detail.trial.produced_at) },
            ]}
          />
          <DataTable caption="Parameters" ariaLabel="Trial parameters">
            <thead><tr><th scope="col">Parameter</th><th scope="col">Value</th></tr></thead>
            <tbody>
              {Object.entries(detail.trial.parameters).map(([key, value]) => <tr key={key}><td>{key}</td><td className="tabular-num">{value}</td></tr>)}
            </tbody>
          </DataTable>
          <DataTable caption={`Stored search-tier metrics (${detail.trial.numeric_tier ?? "no result"}, gross)`} ariaLabel="Search metrics">
            <thead><tr><th scope="col">Metric</th><th scope="col">Stored value</th></tr></thead>
            <tbody>
              {Object.keys(detail.trial.metrics).length
                ? Object.entries(detail.trial.metrics).map(([key, value]) => (
                  <tr key={key}><td>{metricLabel(key) === key ? <code>{key}</code> : <>{metricLabel(key)} <code>{key}</code></>}</td><td className="tabular-num"><Stored value={value} /></td></tr>
                ))
                : <tr><td colSpan={2}>No stored metric.</td></tr>}
            </tbody>
          </DataTable>
          {detail.rerun_lags.length ? (
            <DataTable caption="Decimal authority rerun per lag (selection authority only; economics gross and T2-conditional)" ariaLabel="Decimal rerun lags">
              <thead>
                <tr>
                  <th scope="col">Lag</th><th scope="col">Reconciliation</th><th scope="col">Held-bar divergence</th><th scope="col">Trade divergence</th>
                  {PREFERRED_METRICS.slice(0, 5).map((key) => <th key={key} scope="col">Decimal {metricLabel(key)}</th>)}
                </tr>
              </thead>
              <tbody>
                {detail.rerun_lags.map((lag) => (
                  <tr key={lag.lag_micros}>
                    <td className="tabular-num">{lagText(lag.lag_micros)}</td><td><Claim label={lag.reconciliation} /></td>
                    <td className="tabular-num"><Stored value={lag.held_divergence_bars === null ? null : String(lag.held_divergence_bars)} /></td>
                    <td className="tabular-num"><Stored value={lag.trade_count_divergence === null ? null : String(lag.trade_count_divergence)} /></td>
                    {PREFERRED_METRICS.slice(0, 5).map((key) => <td key={key} className="tabular-num"><Stored value={lag.metrics[key]} /></td>)}
                  </tr>
                ))}
              </tbody>
            </DataTable>
          ) : <p className="empty-notice">No Decimal authority rerun covers this trial: its metrics are search-tier only.</p>}
          {detail.candidate_states.length ? (
            <DataTable caption="Recorded candidate states" ariaLabel="Trial candidate states">
              <thead><tr><th scope="col">Recorded (UTC)</th><th scope="col">State</th><th scope="col">Reasons</th></tr></thead>
              <tbody>
                {detail.candidate_states.map((state) => (
                  <tr key={state.evidence_hash}><td>{utc(state.recorded_at)}</td><td><Claim label={state.state} /></td><td>{state.reasons.join(", ")}</td></tr>
                ))}
              </tbody>
            </DataTable>
          ) : <p className="empty-notice">No holdout result is recorded for this trial: it is not validated.</p>}
        </>
      ) : <p className="empty-notice">{stateText(result)}</p>}
    </article>
  );
}

export default async function StrategyLabExplorerPage({ searchParams }: { searchParams?: Promise<RawParams> }) {
  const raw = (await searchParams) ?? {};
  const context = terminalContext(raw.terminal_context);
  const family = pick(raw.family, FAMILY);
  const instrument = pick(raw.instrument, SYMBOL);
  const studyOffset = boundedInt(raw.study_offset, 10_000);
  const limitValue = Number(raw.limit);
  const query: StrategyLabTrialQuery = {
    limit: (PAGE_SIZES as readonly number[]).includes(limitValue) ? limitValue : DEFAULT_PAGE_SIZE,
    offset: boundedInt(raw.offset, 100_000),
    sort: pick(raw.sort, SORT) ?? "ordinal",
    direction: raw.dir === "desc" ? "desc" : "asc",
    outcome: (OUTCOMES as readonly string[]).includes(raw.outcome ?? "") ? raw.outcome as StrategyLabTrialQuery["outcome"] : "ALL",
    frozenOnly: raw.frozen === "1",
  };
  const trialId = pick(raw.trial, UUID);
  const heatX = pick(raw.hx, PARAMETER);
  const heatY = pick(raw.hy, PARAMETER);
  const heatMetric = pick(raw.hm, PARAMETER);
  const heatFixed = fixedPairs(raw.hf, [heatX, heatY]);
  const ctx = await getWorkspaceContext();
  const studiesResult = await getStrategyLabStudies(ctx, { strategyFamily: family, instrument, limit: 50, offset: studyOffset });
  const studies = studiesResult.state === "AVAILABLE" ? studiesResult.value : undefined;
  const studyId = pick(raw.study, UUID) ?? studies?.items[0]?.study_id;
  // Links keep the page state that is valid; anything malformed is dropped, not echoed.
  const params: Params = {
    terminal_context: terminalQuery(context).toString() || undefined,
    study: studyId, family, instrument, study_offset: studyOffset ? String(studyOffset) : undefined,
    sort: query.sort === "ordinal" ? undefined : query.sort, dir: query.direction === "desc" ? "desc" : undefined,
    outcome: query.outcome === "ALL" ? undefined : query.outcome, frozen: query.frozenOnly ? "1" : undefined,
    offset: query.offset ? String(query.offset) : undefined,
    limit: query.limit === DEFAULT_PAGE_SIZE ? undefined : String(query.limit), trial: trialId,
    hx: heatX, hy: heatY, hm: heatMetric,
    hf: heatFixed.length ? heatFixed.map(([name, value]) => `${name}:${value}`).join(",") : undefined,
  };

  // The heatmap is read only once all three choices are made; fixed values are the server's to check.
  const wantsHeatmap = Boolean(studyId && heatX && heatY && heatMetric);
  const [detailResult, trialsResult, heatmapResult, breakEvenResult, chartsResult] = studyId
    ? await Promise.all([
      getStrategyLabStudy(ctx, studyId), getStrategyLabTrials(ctx, studyId, query),
      wantsHeatmap ? getStrategyLabHeatmap(ctx, studyId, { x: heatX, y: heatY, metric: heatMetric, fixed: heatFixed }) : Promise.resolve(undefined),
      getStrategyLabBreakEvenDistribution(ctx, studyId),
      // Navigation only: lets the study link to its exact research window on the Market Chart.
      getChartSeriesCatalog(ctx),
    ])
    : [undefined, undefined, undefined, undefined, undefined];
  const detail = detailResult?.state === "AVAILABLE" ? detailResult.value : undefined;
  const charts = chartsResult?.state === "AVAILABLE" ? chartsResult.value.items : undefined;
  const trials = trialsResult?.state === "AVAILABLE" ? trialsResult.value : undefined;
  const metricKeys = (trials?.metric_keys ?? []).filter((key) => !DESCRIPTIVE_KEYS.has(key));
  // Both visualizations bind to the study as displayed, and to each other: any disagreement withholds both.
  const latestManifest = detail?.manifests[detail.manifests.length - 1];
  const studyOnScreen: StudyOnScreen = {
    studyId: studyId ?? "", contentHash: detail?.summary.content_hash,
    latestManifestHash: detail ? (latestManifest?.manifest_hash ?? null) : undefined, resultCount: detail?.summary.result_count,
  };
  const analyticsMismatch = crossViewMismatch(
    heatmapResult?.state === "AVAILABLE" ? heatmapResult.value.evidence : undefined,
    breakEvenResult?.state === "AVAILABLE" ? breakEvenResult.value.evidence : undefined,
  );
  const preferred = PREFERRED_METRICS.filter((key) => metricKeys.includes(key));
  const columns: string[] = preferred.length ? [...preferred] : metricKeys;
  const summary = detail?.summary;
  const symbol = summary?.instrument_source === "RESEARCH_WINDOW_CATALOGUE" ? terminalSymbol(summary.instrument) : undefined;
  const bindings = summary?.datasets.filter(ref => ref.resolution === "RESEARCH_WINDOW_CATALOGUE") ?? [];
  const chart = summary ? bindings.length === 1 ? researchChart(charts, symbol, undefined, bindings[0])
    : {reason: "No unique research window resolved for this study."} : researchChart(charts, context.symbol, context.series);
  // An all-markets comparison must not become a one-market filter merely by viewing a study.
  const selection: CompareSelection = raw.terminal_context !== undefined ? context : {...context,
    symbol, series: chart.series?.manifest_hash, studies: summary ? [summary.study_id] : []};
  params.terminal_context = terminalQuery(selection).toString() || undefined;
  const selectedTrial = trialId && summary ? {...selection, paperStudy: summary.study_id, paperTrial: trialId} : selection;

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Strategy Lab Explorer"
        subtitle="Persisted studies and trials, read-only. Results are gross and search-tier; a Decimal rerun establishes a selection, never profitability. Nothing here runs a search, opens the holdout, promotes a candidate or trades."
        status={studies ? "AVAILABLE" : studiesResult.state}
        statusLabel="SEARCH_NON_AUTHORITATIVE · GROSS"
        asOf={ctx.evidenceTime}
      />

      <ResearchWorkflowNav current="strategy-lab" hrefs={{terminal: `/terminal?${terminalQuery(selectedTrial)}#compare`,
        "strategy-lab": href(params, {}),
        chart: chart.series ? researchChartHref(chart.series, selection) : null,
        validation: `/terminal?${terminalQuery(selectedTrial)}#validation`}} />

      <article className="panel margin-bottom-24" aria-label="Studies">
        <h2><span>Studies</span><QualityStateBadge status={studies ? studies.state : studiesResult.state} /></h2>
        <form method="GET" className="filter-bar" aria-label="Study filters">
          <Carry params={params} keys={[]} />
          <div className="filter-groups">
            <div className="filter-item">
              <label htmlFor="study-family" className="filter-label">Strategy family</label>
              <select id="study-family" name="family" defaultValue={family ?? ""} className="filter-select">
                <option value="">All families</option>
                {(studies?.facets.strategy_families ?? []).map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </div>
            <div className="filter-item">
              <label htmlFor="study-instrument" className="filter-label">Instrument</label>
              <select id="study-instrument" name="instrument" defaultValue={instrument ?? ""} className="filter-select"
                disabled={studies ? !studies.facets.instrument_catalogue : true}>
                <option value="">All instruments</option>
                {(studies?.facets.instruments ?? []).map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </div>
          </div>
          <div className="filter-actions">
            <button type="submit" className="filter-apply-btn">Apply Filters</button>
            <Link href={href({terminal_context: params.terminal_context}, {})} className="filter-reset-btn">Reset</Link>
          </div>
        </form>
        {studies && !studies.facets.instrument_catalogue ? (
          <p className="empty-notice">Instrument UNRESOLVED: no research window catalogue is configured, so studies cannot be matched to an instrument (labels are never parsed).</p>
        ) : null}
        {studies && studies.items.length ? (
          <>
            <DataTable caption={`Registered studies (${studies.total ?? studies.items.length} matching)`} ariaLabel="Strategy Lab studies">
              <thead>
                <tr>
                  <th scope="col">Study</th><th scope="col">Instrument</th><th scope="col">Family</th><th scope="col">Data window</th>
                  <th scope="col">Trials (planned / results)</th><th scope="col">Progress</th><th scope="col">Claim</th>
                </tr>
              </thead>
              <tbody>
                {studies.items.map((study) => {
                  const window = study.datasets.find((ref) => ref.resolution === "RESEARCH_WINDOW_CATALOGUE");
                  return (
                    <tr key={study.study_id}>
                      <td><Link href={href(params, { study: study.study_id, offset: undefined, trial: undefined, hx: undefined, hy: undefined, hm: undefined, hf: undefined })} aria-current={study.study_id === studyId ? "page" : undefined}>{study.label}</Link></td>
                      <td>{study.instrument ?? "UNRESOLVED"}</td>
                      <td>{study.strategy_family} {study.strategy_version}</td>
                      <td>{window ? `${window.first_utc_day}..${window.last_utc_day}` : "UNRESOLVED"}</td>
                      <td className="tabular-num">{study.planned_trial_count} / {study.result_count}</td>
                      <td>{counts(study.queue_states)}</td>
                      <td><Claim label="SEARCH_NON_AUTHORITATIVE" /></td>
                    </tr>
                  );
                })}
              </tbody>
            </DataTable>
            <Pagination limit={studies.page.limit} offset={studies.page.offset} returned={studies.page.returned} hasMore={studies.page.has_more}
              basePath="/strategy-lab" searchParams={{ study: params.study, family, instrument, terminal_context: params.terminal_context }} offsetParam="study_offset" limitParam="study_limit" />
          </>
        ) : (
          <p className="empty-notice">{studies ? (family || instrument ? "No study matches these filters." : "No study registered.") : stateText(studiesResult)}</p>
        )}
      </article>

      {studyId ? (
        <article className="panel margin-bottom-24" aria-label="Study" id="study">
          <h2>
            <span>{summary?.label ?? short(studyId)}</span>
            <Claim label="SEARCH_NON_AUTHORITATIVE" />
          </h2>
          {detail && summary ? (
            <>
              <p>
                Why not authoritative: {summary.authority.reasons.join(", ")}. Promotable: no. Every metric below is the stored text, never
                recomputed in this browser.
              </p>
              <KeyValueGrid
                columns={4}
                items={[
                  { key: "instrument", label: "Instrument", value: summary.instrument ?? "UNRESOLVED" },
                  { key: "family", label: "Strategy", value: `${summary.strategy_family} ${summary.strategy_version}` },
                  { key: "trials", label: "Planned trials", value: summary.planned_trial_count },
                  { key: "outcomes", label: "Recorded outcomes", value: counts(detail.manifests.at(-1)?.outcomes) },
                  { key: "registered", label: "Registered (UTC)", value: utc(summary.registered_at) },
                  { key: "eval", label: "Evaluation ends before (UTC)", value: utc(summary.evaluation_upper_bound_exclusive) },
                  {
                    key: "holdout", label: "Holdout",
                    value: detail.holdout
                      ? `${detail.holdout.state} from ${detail.holdout.boundary.slice(0, 10)} (${detail.holdout.cycle_id}); study ${detail.holdout.evaluation_before_boundary ? "ends before it" : "CROSSES IT"}`
                      : "UNAVAILABLE",
                  },
                  { key: "search", label: "Search", value: `${String(detail.search.mode ?? "UNAVAILABLE")} · max ${String(detail.search.max_trials ?? "UNAVAILABLE")}` },
                ]}
              />
              <StudyDataLinks datasets={summary.datasets} charts={charts} selection={selection} />
              {detail.economics ? <EconomicsTable economics={detail.economics} /> : <p className="empty-notice">Economics UNAVAILABLE for this study.</p>}
              <section aria-label="Frozen candidates and Decimal rerun" className="margin-bottom-24">
                <h3>Frozen candidates and Decimal rerun reconciliation</h3>
                <FrozenCandidates detail={detail} params={params} />
              </section>
              <ProvenancePanel
                source={`strategy_lab_read_model_v1: study ${summary.study_id}`}
                recordId={summary.study_id}
                version={String(detail.strategy.execution_convention ?? "strategy-lab-study-v1")}
                datasetVersion={summary.datasets.map((ref) => `${ref.role} ${ref.dataset_version_id} (${ref.evidence_tier})`).join("; ") || undefined}
                contentHash={summary.content_hash}
                asOf={ctx.evidenceTime}
                limitations={[
                  ...summary.datasets.map((ref) => ref.resolution === "RESEARCH_WINDOW_CATALOGUE"
                    ? `${ref.symbol} research window ${ref.first_utc_day}..${ref.last_utc_day}, ${ref.bars?.toLocaleString("en-US") ?? "UNAVAILABLE"} bars, ${ref.continuous ? "no day gap" : "DAY GAPS"}; not-published days: ${ref.not_published_days.join(", ") || "none"}; rejected days: ${ref.rejected_days.join(", ") || "none"}; window identity ${ref.content_hash}; knowledge before ${ref.knowledge_upper_bound_exclusive ?? "UNAVAILABLE"}.`
                    : `Dataset ${ref.dataset_version_id} (${ref.content_hash}) is UNRESOLVED in the research window catalogue.`),
                  `Numeric policy ${detail.numeric_policy_slot}; cost policy ${detail.cost_policy_slot}.`,
                  `Implementation ${String(detail.strategy.implementation_sha256 ?? "UNAVAILABLE")}.`,
                  "Parameter space: " + detail.parameter_space.map((dim) => `${dim.name} ∈ {${(dim.values ?? []).join(", ")}}`).join("; "),
                ]}
              />
            </>
          ) : <p className="empty-notice">{detailResult ? stateText(detailResult) : "UNAVAILABLE"}</p>}
        </article>
      ) : null}

      {studyId ? (
        <HeatmapPanel detail={detail} study={studyOnScreen} metricKeys={metricKeys} params={params} fixed={heatFixed}
          result={heatmapResult} crossMismatch={analyticsMismatch} />
      ) : null}

      {studyId ? (
        <BreakEvenPanel study={studyOnScreen} result={breakEvenResult} crossMismatch={analyticsMismatch} />
      ) : null}

      {studyId ? (
        <article className="panel margin-bottom-24" aria-label="Trials" id="trials">
          <h2><span>Trials</span><Claim label="SEARCH_NON_AUTHORITATIVE · GROSS" /></h2>
          <form method="GET" className="filter-bar" aria-label="Trial filters">
            <input type="hidden" name="study" value={studyId} />
            {family ? <input type="hidden" name="family" value={family} /> : null}
            {instrument ? <input type="hidden" name="instrument" value={instrument} /> : null}
            <Carry params={params} keys={["hx", "hy", "hm", "hf"]} />
            <div className="filter-groups">
              <div className="filter-item">
                <label htmlFor="trial-outcome" className="filter-label">Outcome</label>
                <select id="trial-outcome" name="outcome" defaultValue={query.outcome} className="filter-select">
                  {OUTCOMES.map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </div>
              <div className="filter-item">
                <label htmlFor="trial-sort" className="filter-label">Sort by</label>
                <select id="trial-sort" name="sort" defaultValue={query.sort} className="filter-select">
                  <option value="ordinal">Trial ordinal</option>
                  {metricKeys.map((key) => <option key={key} value={key}>{metricLabel(key)}</option>)}
                </select>
              </div>
              <div className="filter-item">
                <label htmlFor="trial-dir" className="filter-label">Direction</label>
                <select id="trial-dir" name="dir" defaultValue={query.direction} className="filter-select">
                  <option value="asc">Ascending</option>
                  <option value="desc">Descending</option>
                </select>
              </div>
              <div className="filter-item">
                <label htmlFor="trial-limit" className="filter-label">Rows per page</label>
                <select id="trial-limit" name="limit" defaultValue={String(query.limit)} className="filter-select">
                  {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
                </select>
              </div>
              <div className="filter-item">
                <label htmlFor="trial-frozen" className="filter-label">Frozen candidates only</label>
                <input id="trial-frozen" type="checkbox" name="frozen" value="1" defaultChecked={query.frozenOnly} />
              </div>
            </div>
            <div className="filter-actions">
              <button type="submit" className="filter-apply-btn">Apply Filters</button>
              <Link href={href({ study: studyId, family, instrument, terminal_context: params.terminal_context }, {})} className="filter-reset-btn">Reset</Link>
            </div>
          </form>
          <p>
            Sorted on the server by the exact stored value; missing or inadmissible values sort last; ties keep trial order.
            Values are float64 search-tier text (OR-3), gross of every cost; the Decimal rerun of the frozen set is shown above.
          </p>
          {trials && trials.items.length ? (
            <>
              <DataTable caption={`Trials ${trials.page.offset + 1}–${trials.page.offset + trials.page.returned} of ${trials.total}`} ariaLabel="Trial results">
                <thead>
                  <tr>
                    <SortHeader label="Trial" sortKey="ordinal" query={query} params={params} />
                    <th scope="col">Parameters</th>
                    <th scope="col">Outcome</th>
                    {columns.map((key) => <SortHeader key={key} label={metricLabel(key)} sortKey={key} query={query} params={params} />)}
                    <th scope="col">Frozen rank</th>
                    <th scope="col">Decimal rerun</th>
                  </tr>
                </thead>
                <tbody>
                  {trials.items.map((trial) => <TrialRow key={trial.trial_id} trial={trial} columns={columns} params={params} />)}
                </tbody>
              </DataTable>
              <Pagination limit={trials.page.limit} offset={trials.page.offset} returned={trials.page.returned} hasMore={trials.page.has_more}
                basePath="/strategy-lab" searchParams={{ ...params, offset: undefined, limit: undefined, trial: undefined }} />
            </>
          ) : (
            <p className="empty-notice">{trials ? (trials.total ? "This page is past the last trial." : "No trial matches these filters.") : trialsResult ? stateText(trialsResult) : "UNAVAILABLE"}</p>
          )}
        </article>
      ) : null}

      {studyId && trialId ? <TrialDetailPanel studyId={studyId} trialId={trialId} ctx={ctx} params={params} /> : null}
    </div>
  );
}
