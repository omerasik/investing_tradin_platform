import React from "react";
import {
  utc,
  type CandidatePaperEvidence,
  type ComparisonMetric,
  type ComparisonRow,
  type ComparisonSort,
  type PaperAccountLedger,
  type TerminalAccount,
  type TerminalComparison,
  type StrategyLabPageInfo,
} from "../../lib/data-access";
import type { EvidenceResult } from "../../operator-contracts";
import { DataTable } from "../../components/data-table";
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
} from "./paper-economics";

/**
 * Compare and Paper evidence: recorded evidence side by side. The operator picks the
 * studies and, explicitly, the metric shown and any sort; there is no composite score and
 * no default ranking. Search tier, Decimal rerun, walk-forward development evidence and
 * holdout validation stay in separate rows, and none of them is a validation.
 */

export const COMPARE_MAX_STUDIES = 20;

function Badge({ label }: { label: string }) {
  return <span className="badge" data-claim={label}>{label}</span>;
}

function short(hash: string | null | undefined): React.ReactNode {
  return hash ? <code title={hash}>{hash.slice(0, 10)}…</code> : "—";
}

function key(row: { identity: { study_id: string; trial_id: string } } | CandidatePaperEvidence): string {
  return "identity" in row ? `${row.identity.study_id}/${row.identity.trial_id}` : `${row.study_id}/${row.trial_id}`;
}

function recorded(metrics: ComparisonMetric[] | undefined, metric: string | undefined): React.ReactNode {
  if (!metric) return "—";
  const found = metrics?.find((item) => item.key === metric);
  if (!found || found.state === "MISSING") return <Badge label="MISSING" />;
  return <span className="tabular-num">{found.value}</span>;
}

function trialHref(studyId: string, trialId: string): string {
  return `/strategy-lab?${new URLSearchParams({ study: studyId, trial: trialId }).toString()}#trial-detail`;
}

export function paperEvidenceHref(studyId: string, trialId: string): string {
  return `/terminal?${new URLSearchParams({ paper_study: studyId, paper_trial: trialId }).toString()}#paper-evidence`;
}

/** Carry the comparison context through candidate review. */
export function candidateHref(studyId: string, trialId: string, selection?: CompareSelection): string {
  const query = terminalQuery(selection);
  query.set("paper_study", studyId);
  query.set("paper_trial", trialId);
  return `/terminal?${query.toString()}#opportunity-detail`;
}

/** One URL context for study paging, comparison and candidate review. */
export function terminalQuery(selection?: CompareSelection): URLSearchParams {
  const query = new URLSearchParams();
  if (selection?.studyOffset) query.set("study_offset", String(selection.studyOffset));
  if (selection?.paperStudy && selection.paperTrial) {
    query.set("paper_study", selection.paperStudy);
    query.set("paper_trial", selection.paperTrial);
  }
  if (selection?.symbol) query.set("symbol", selection.symbol);
  for (const id of selection?.studies ?? []) query.append("compare_study", id);
  if (selection?.metric) query.set("compare_metric", selection.metric);
  if (selection?.sort) {
    query.set("compare_dimension", selection.sort.dimension);
    query.set("compare_direction", selection.sort.direction);
  }
  return query;
}

/** Every recorded metric key a row carries, for the operator's explicit choice (never a preset). */
function metricKeys(rows: ComparisonRow[]): string[] {
  const keys = new Set<string>();
  for (const row of rows) {
    row.search.metrics.forEach((item) => keys.add(item.key));
    row.decimal_rerun.baseline?.metrics.forEach((item) => keys.add(item.key));
  }
  return [...keys].sort();
}

function position(row: ComparisonRow): React.ReactNode {
  if (row.sort_state !== "VALUE") return <Badge label={row.sort_state} />;
  return <>#{row.sort_position}{row.tied_with ? ` (tied with ${row.tied_with})` : ""}</>;
}

function walkForward(row: ComparisonRow, metric: string | undefined): React.ReactNode {
  if (!row.walk_forward.reports.length) return <Badge label={row.walk_forward.claim} />;
  return (
    <>
      <Badge label={row.walk_forward.claim} />
      {row.walk_forward.reports.map((report) => {
        const spread = metric ? report.dispersion.find((item) => item.metric === metric) : undefined;
        return (
          <div key={report.report_hash}>
            {short(report.report_hash)} selected in {report.folds_selected_this_candidate.length}/{report.fold_count} folds
            {spread ? ` · ${metric} median ${spread.median_nearest_rank ?? "—"} [${spread.min ?? "—"} … ${spread.max ?? "—"}], ${spread.folds_with_value} value(s)` : ""}
          </div>
        );
      })}
    </>
  );
}

export type CompareSelection = { symbol?: string; studies: string[]; metric?: string; sort?: ComparisonSort;
  studyOffset?: number; paperStudy?: string; paperTrial?: string };

/** The operator's selection form. Plain GET: nothing is written, nothing runs. */
function CompareForm({ studies, selection, metrics, unavailable, studyPage }: {
  studies: { study_id: string; label: string; strategy_family: string }[];
  selection: CompareSelection;
  metrics: string[];
  unavailable?: string;
  studyPage?: StrategyLabPageInfo;
}) {
  return (
    <form id="terminal-comparison-selection" method="get" action="/terminal#compare" aria-label="Candidate comparison selection" className="margin-bottom-24">
      {selection.studies.filter((id) => !studies.some((study) => study.study_id === id)).map((id) =>
        <input key={id} type="hidden" name="compare_study" value={id} />)}
      {selection.symbol ? <input type="hidden" name="symbol" value={selection.symbol} /> : null}
      {selection.paperStudy && selection.paperTrial ? <>
        <input type="hidden" name="paper_study" value={selection.paperStudy} />
        <input type="hidden" name="paper_trial" value={selection.paperTrial} />
      </> : null}
      <fieldset>
        <legend>Studies to compare (at most {COMPARE_MAX_STUDIES})</legend>
        {studies.length ? studies.map((study) => (
          <div key={study.study_id}>
            <label>
              <input type="checkbox" name="compare_study" value={study.study_id} defaultChecked={selection.studies.includes(study.study_id)} />{" "}
              {study.label} <small>({study.strategy_family})</small>
            </label>
          </div>
        )) : <p>{unavailable ?? "No study registered."}</p>}
      </fieldset>
      <fieldset>
        <legend>Metric and optional sort (your explicit choice; no default ranking, no composite score)</legend>
        <label>
          Metric shown{" "}
          <input name="compare_metric" list="compare-metric-keys" defaultValue={selection.metric ?? ""} pattern="[a-z][a-z0-9_]{0,63}" />
        </label>
        <datalist id="compare-metric-keys">{metrics.map((item) => <option key={item} value={item} />)}</datalist>{" "}
        <label>
          Sort by{" "}
          <select name="compare_dimension" defaultValue={selection.sort?.dimension ?? ""}>
            <option value="">No sort (identity order)</option>
            <option value="SEARCH">Search-tier metric</option>
            <option value="DECIMAL_BASELINE">Decimal rerun baseline metric</option>
          </select>
        </label>{" "}
        <label>
          Direction{" "}
          <select name="compare_direction" defaultValue={selection.sort?.direction ?? ""}>
            <option value="">—</option>
            <option value="ASC">Ascending</option>
            <option value="DESC">Descending</option>
          </select>
        </label>
      </fieldset>
      <button type="submit" name="study_offset" value={selection.studyOffset ?? 0}>Compare selected studies</button>
      {studyPage ? <nav aria-label="Comparison study pages">
        <button type="submit" name="study_offset" value={Math.max(0, studyPage.offset - studyPage.limit)}
          disabled={studyPage.offset === 0}>Previous studies, retaining selection</button>{" "}
        <button type="submit" name="study_offset" value={studyPage.offset + studyPage.limit}
          disabled={!studyPage.has_more || studyPage.offset + studyPage.limit > 10_000}>Next studies, retaining selection</button>
      </nav> : null}
    </form>
  );
}

function ParityCell({ paper }: { paper: CandidatePaperEvidence | undefined }) {
  if (!paper) return <Badge label="NOT_LOADED" />;
  return <><Badge label={paper.parity.status} />{paper.parity.loaded ? null : paper.parity.status === "REFUSED" ? " stored row refused" : " no stored parity assessment"}</>;
}

function FundingCell({ paper }: { paper: CandidatePaperEvidence | undefined }) {
  if (!paper) return <><Badge label="NOT_LOADED" /> <Badge label="NON_COSTABLE" /></>;
  return <><Badge label={paper.funding.status} /> <Badge label={paper.funding.cost_status} /></>;
}

function EconomicsCell({ paper }: { paper: CandidatePaperEvidence | undefined }) {
  if (!paper) return <Badge label="NOT_LOADED" />;
  const economics = paper.economics;
  return (
    <>
      Net <Badge label={netCostability(economics)} />
      {economics.gross_realized ? <div>Realized gross {returnFigure(economics.gross_realized.value)}</div> : null}
    </>
  );
}

function RiskCell({ paper }: { paper: CandidatePaperEvidence | undefined }) {
  if (!paper) return <Badge label="NOT_LOADED" />;
  return <><Badge label={paper.risk.status} /> <div>{sizingText(paper.risk)}</div></>;
}

/** Economics by component: each with its own status and reasons; figures are returns per unit notional. */
function EconomicsDetail({ item }: { item: CandidatePaperEvidence }) {
  const economics = item.economics;
  return (
    <section aria-label={`Paper economics ${item.trial_id.slice(0, 8)}`} data-paper-economics={item.trial_id}>
      <h4>Paper economics: <Badge label={economics.status} /> <Badge label={economics.claim} /></h4>
      <p>
        Units <code>{economics.units}</code> ({RETURN_UNIT_LABEL}; money: {economics.money}). Realized and unrealized
        are kept apart; unrealized is never part of net.
        {economics.economics_hash ? <> Projection {short(economics.economics_hash)}.</> : null}
      </p>
      <DataTable caption="Economics by component (each status is the module's own)" ariaLabel={`Economics components ${item.trial_id.slice(0, 8)}`}>
        <thead><tr><th scope="col">Component</th><th scope="col">Status</th><th scope="col">Figure</th><th scope="col">Reasons</th></tr></thead>
        <tbody>
          {economicsRows(economics).map((row) => (
            <tr key={row.component} data-economics-component={row.component}>
              <td>{row.component}</td><td><Badge label={row.status} /></td><td className="tabular-num">{row.figure}</td>
              <td>{row.reasons.length ? row.reasons.map((reason) => <div key={reason}><code>{reason}</code></div>) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </DataTable>
      {economics.price_resolution ? (
        <details data-price-resolution={economics.price_resolution.status}>
          <summary>Public trade-price reference: {economics.price_resolution.status} (not a venue settlement mark)</summary>
          <p>The server resolves the exact declared observation against sealed T4 evidence and capture acceptance.
            Freshness and coverage bounds are explicit policy declarations. This reference never costs funding or net.</p>
          {reasonsList(economics.price_resolution.reasons)}
          <pre>{JSON.stringify(economics.price_resolution, null, 2)}</pre>
        </details>
      ) : null}
      {economics.sensitivity_rows.length ? (
        <>
          <h5>Proposed sensitivity scenarios (not owner-approved, never in net)</h5>
          <ul>{economics.sensitivity_rows.map((row) => <li key={row.name}>{sensitivityLabel(row)} <small>({row.source})</small></li>)}</ul>
        </>
      ) : null}
    </section>
  );
}

/** Risk of the candidate's recorded paper target under its bound account; sizing only when ALLOWED. */
export function RiskDetail({ item }: { item: CandidatePaperEvidence }) {
  const risk = item.risk;
  const current = policySummary(risk.current_policy);
  return (
    <section aria-label={`Paper risk ${item.trial_id.slice(0, 8)}`} data-paper-risk={item.trial_id}>
      <h4>Paper risk: <Badge label={risk.status} /> <Badge label={risk.claim} /></h4>
      <p>
        Account {risk.account_id ? <code>{risk.account_id}</code> : "none"} ({risk.account_source}) · current policy{" "}
        <Badge label={current.status} />{risk.current_policy?.policy_status ? <> stored as {risk.current_policy.policy_status}</> : null} ·
        target {targetText(risk)} · {sizingText(risk)} · execution authority: none.
      </p>
      <h5>Reasons</h5>
      {reasonsList(risk.reasons)}
      {current.reasons.length ? <><h5>Current policy reasons</h5>{reasonsList(current.reasons)}</> : null}
      {risk.controls.length ? (
        <DataTable caption="Risk controls (stored policy values only; nothing defaulted)" ariaLabel={`Risk controls ${item.trial_id.slice(0, 8)}`}>
          <thead><tr><th scope="col">Control</th><th scope="col">Status</th><th scope="col">Reason</th></tr></thead>
          <tbody>
            {risk.controls.map((control) => (
              <tr key={control.control}><td>{control.control}</td><td><Badge label={control.status} /></td><td>{control.reason ?? "—"}</td></tr>
            ))}
          </tbody>
        </DataTable>
      ) : null}
    </section>
  );
}

/** Selected studies' frozen candidates side by side, one column per candidate. */
export function ComparisonSection({ studies, studiesUnavailable, selection, comparison, unavailable, studyPage }: {
  studies: { study_id: string; label: string; strategy_family: string }[] | undefined;
  studiesUnavailable?: string;
  selection: CompareSelection;
  comparison: TerminalComparison | undefined;
  unavailable: string | undefined;
  studyPage?: StrategyLabPageInfo;
}) {
  const rows = comparison?.comparison.rows ?? [];
  const paper = new Map((comparison?.paper_evidence ?? []).map((item) => [key(item), item]));
  const metric = selection.metric;
  return (
    <section aria-label="Candidate comparison">
      <h3>Side-by-side candidates</h3>
      <CompareForm studies={studies ?? []} unavailable={studiesUnavailable} selection={selection} metrics={metricKeys(rows)} studyPage={studyPage} />
      {!selection.studies.length ? (
        <p className="empty-notice">Select one or more studies to compare their frozen candidates. Nothing is ranked until you choose a metric and a direction.</p>
      ) : unavailable ? <p className="empty-notice">{unavailable}</p> : comparison ? (
        <>
          <DataTable caption="What each evidence stage may claim (none is a validation)" ariaLabel="Evidence stages">
            <thead><tr><th scope="col">Stage</th><th scope="col">Claim</th><th scope="col">Meaning</th></tr></thead>
            <tbody>
              {comparison.evidence_stages.map((stage) => (
                <tr key={stage.stage} data-stage={stage.stage}><td>{stage.label}</td><td><Badge label={stage.claim} /></td><td>{stage.meaning}</td></tr>
              ))}
            </tbody>
          </DataTable>
          <p>
            <Badge label={comparison.claim} /> Composite score: <strong>{comparison.composite_score}</strong> · holdout {comparison.holdout} ·{" "}
            {comparison.comparison.sort
              ? <>sorted by {comparison.comparison.sort.dimension} <code>{comparison.comparison.sort.metric}</code> {comparison.comparison.sort.direction} (ties: {comparison.comparison.tie_break})</>
              : "unsorted (identity order: study selection order, then frozen rank)"}
            {" "}· output <code>{comparison.comparison.output_hash.slice(0, 12)}</code>.
          </p>
          {comparison.comparison.cross_study.differences.length ? (
            <p>Not like for like: {comparison.comparison.cross_study.differences.map((item) => <Badge key={item} label={item} />)}</p>
          ) : null}
          {comparison.comparison.studies_without_frozen_set.length ? (
            <p>No frozen candidate set recorded for: {comparison.comparison.studies_without_frozen_set.map((id) => <code key={id}>{id.slice(0, 8)}… </code>)}</p>
          ) : null}
          {rows.length ? (
            <DataTable caption={`Frozen candidates side by side${metric ? ` — metric ${metric}` : ""}`} ariaLabel="Candidate comparison table">
              <thead>
                <tr>
                  <th scope="col">Evidence</th>
                  {rows.map((row) => (
                    <th scope="col" key={key(row)} data-compare-candidate={row.identity.trial_id}>
                      {row.identity.study_label} · <a href={trialHref(row.identity.study_id, row.identity.trial_id)}><code title={row.identity.trial_id}>{row.identity.trial_id.slice(0, 8)}…</code></a>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr><th scope="row">Sort position</th>{rows.map((row) => <td key={key(row)}>{position(row)}</td>)}</tr>
                <tr><th scope="row">Frozen rank (search freeze rule)</th>{rows.map((row) => <td key={key(row)} className="tabular-num">{row.identity.frozen_rank}</td>)}</tr>
                <tr data-stage-row="SEARCH">
                  <th scope="row">Search tier <Badge label="SEARCH_NON_AUTHORITATIVE" /></th>
                  {rows.map((row) => (
                    <td key={key(row)}>
                      {row.search.frozen_metric} {row.search.frozen_metric_value}{metric ? <> · {metric} {recorded(row.search.metrics, metric)}</> : null}
                      <div>of {row.search.multiple_testing_trial_count} trials{row.search.cutoff_tie ? " · cutoff tie" : ""}</div>
                    </td>
                  ))}
                </tr>
                <tr data-stage-row="DECIMAL_RERUN">
                  <th scope="row">Decimal rerun <Badge label="GROSS_CONDITIONAL_NON_PROMOTABLE" /></th>
                  {rows.map((row) => (
                    <td key={key(row)}>
                      <Badge label={row.decimal_rerun.claim} />
                      <div>
                        In Decimal selection: {row.decimal_rerun.in_decimal_selection === null ? "—" : row.decimal_rerun.in_decimal_selection ? `yes (rank ${row.decimal_rerun.decimal_rank})` : "no"}
                        {metric ? <> · baseline {metric} {recorded(row.decimal_rerun.baseline?.metrics, metric)}</> : null}
                      </div>
                      {row.decimal_rerun.sweep_lags_missing.length ? <div>Sweep lags missing: {row.decimal_rerun.sweep_lags_missing.join(", ")}</div> : null}
                    </td>
                  ))}
                </tr>
                <tr data-stage-row="WALK_FORWARD">
                  <th scope="row">Walk-forward development <Badge label="NOT VALIDATION" /></th>
                  {rows.map((row) => <td key={key(row)}>{walkForward(row, metric)}</td>)}
                </tr>
                <tr data-stage-row="VALIDATION">
                  <th scope="row">Holdout validation <Badge label={comparison.validated_claim} /></th>
                  {rows.map((row) => (
                    <td key={key(row)}>
                      {row.candidate_state ? <Badge label={row.candidate_state.state} /> : <>no holdout state recorded · holdout {comparison.cycle.holdout_state}</>}
                    </td>
                  ))}
                </tr>
                <tr>
                  <th scope="row">Economics</th>
                  {rows.map((row) => (
                    <td key={key(row)}><Badge label={row.economics.completeness} /> net profitability {row.economics.net_profitability}</td>
                  ))}
                </tr>
                <tr>
                  <th scope="row">Evidence tier</th>
                  {rows.map((row) => (
                    <td key={key(row)}>{row.provenance.datasets.map((item) => `${item.evidence_tier} (${item.tier_claim})`).join(", ") || "—"}</td>
                  ))}
                </tr>
                <tr data-stage-row="PARITY">
                  <th scope="row">Live/replay fill parity</th>
                  {rows.map((row) => <td key={key(row)}><ParityCell paper={paper.get(key(row))} /></td>)}
                </tr>
                <tr data-stage-row="FUNDING">
                  <th scope="row">Forward funding</th>
                  {rows.map((row) => <td key={key(row)}><FundingCell paper={paper.get(key(row))} /></td>)}
                </tr>
                <tr data-stage-row="PAPER_ECONOMICS">
                  <th scope="row">Paper economics ({RETURN_UNIT_LABEL})</th>
                  {rows.map((row) => <td key={key(row)}><EconomicsCell paper={paper.get(key(row))} /></td>)}
                </tr>
                <tr data-stage-row="PAPER_RISK">
                  <th scope="row">Paper risk (recorded target)</th>
                  {rows.map((row) => <td key={key(row)}><RiskCell paper={paper.get(key(row))} /></td>)}
                </tr>
                <tr>
                  <th scope="row">Next</th>
                  {rows.map((row) => (
                    <td key={key(row)}>
                      <a href={candidateHref(row.identity.study_id, row.identity.trial_id, selection)}>Review candidate, risk and paper</a>
                    </td>
                  ))}
                </tr>
              </tbody>
            </DataTable>
          ) : <p className="empty-notice">The selected studies have no frozen candidate recorded.</p>}
          <p>Every column restates {rows[0]?.restated_claims.join(" · ") ?? "its claims"}; promotable: no; execution authority: NONE.</p>
        </>
      ) : null}
    </section>
  );
}

/**
 * Risk Preview per registered account: the authoritative policy resolution (ACTIVE only when resolved) and the
 * paper risk verdict of each candidate's latest recorded target. Nothing here sizes for an order or activates.
 */
export function AccountRiskSection({ accounts, reads }: {
  accounts: TerminalAccount[];
  reads: EvidenceResult<PaperAccountLedger>[];
}) {
  if (!accounts.length) return null;
  return (
    <section aria-label="Account risk" className="margin-bottom-24">
      <h3>Authoritative policy and paper risk by account</h3>
      <p>
        The policy is the latest version recorded now, whatever its status: <Badge label="ACTIVE" /> only when every owner
        value is set and approved, otherwise <Badge label="UNRESOLVED" /> with each missing owner value. Risk assesses only a
        candidate&apos;s latest stored signal target at its decision instant; sizing appears only when <Badge label="ALLOWED" />.
      </p>
      {accounts.map((account, index) => {
        const read = reads[index];
        const risk = read?.state === "AVAILABLE" ? read.value.risk : undefined;
        if (!risk) {
          return <p key={account.account_id} className="empty-notice">{account.display_name}: risk read unavailable ({read ? read.state : "NOT_LOADED"}).</p>;
        }
        const current = policySummary(risk.current_policy);
        return (
          <div key={account.account_id} data-account-risk={account.account_id}>
            <h4>{account.display_name}: policy <Badge label={current.status} /> {risk.current_policy.policy_status ? <>(stored {risk.current_policy.policy_status})</> : null}</h4>
            {current.reasons.length ? reasonsList(current.reasons) : null}
            {risk.candidates.length ? (
              <DataTable caption={`Recorded targets under ${account.display_name} (paper risk, never execution authority)`} ariaLabel={`Account risk ${account.account_id}`}>
                <thead><tr><th scope="col">Trial</th><th scope="col">Target</th><th scope="col">Verdict</th><th scope="col">Reasons</th><th scope="col">Sizing</th></tr></thead>
                <tbody>
                  {risk.candidates.map((item) => (
                    <tr key={item.requested_target?.signal_id ?? item.assessment_hash ?? "none"}>
                      <td>{item.requested_target ? <code title={item.requested_target.trial_id}>{item.requested_target.trial_id.slice(0, 8)}…</code> : "—"}</td>
                      <td>{targetText(item)}</td><td><Badge label={item.status} /></td>
                      <td>{item.reasons.length ? item.reasons.join(", ") : "—"}</td><td>{sizingText(item)}</td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            ) : <p>No recorded target assessed: {risk.reasons.join(", ") || "—"}.</p>}
          </div>
        );
      })}
    </section>
  );
}

function reasonsList(reasons: string[]): React.ReactNode {
  return reasons.length ? <ul>{reasons.map((reason) => <li key={reason}><code>{reason}</code></li>)}</ul> : <p>None.</p>;
}

/** Per-candidate live/replay parity and forward funding. NOT_LOADED is shown as such, never as INCOMPLETE. */
export function PaperEvidenceSection({ items, unavailable }: { items: CandidatePaperEvidence[]; unavailable: string | undefined }) {
  if (unavailable) return <p className="empty-notice">{unavailable}</p>;
  if (!items.length) {
    return (
      <p className="empty-notice">
        Choose a candidate: compare studies above, or follow &quot;Paper evidence&quot; from the candidate readiness table.
      </p>
    );
  }
  return (
    <>
      <p>
        Status words: <Badge label="PASS" /> <Badge label="FAIL" /> <Badge label="INCOMPLETE" /> are a loaded parity report&apos;s own verdict;{" "}
        <Badge label="NOT_LOADED" /> means nothing is stored for the candidate in the cycle, which is not the same as incomplete evidence;{" "}
        <Badge label="REFUSED" /> means a stored row did not re-derive and is not shown. Funding is always{" "}
        <Badge label="NON_COSTABLE" />: no funding charge is calculated and no return changes. Economics figures are a{" "}
        {RETURN_UNIT_LABEL}, never money; net is <Badge label="CONFIRMED" />, <Badge label="NOT_COSTABLE" /> or{" "}
        <Badge label="INCOMPLETE" /> as the projection decides. Risk is <Badge label="ALLOWED" />, <Badge label="BLOCKED" /> or{" "}
        <Badge label="UNRESOLVED" /> only for a recorded target under a bound account, and sizing appears only when ALLOWED.
      </p>
      <DataTable caption="Paper evidence by candidate (evidence, never a promotion decision)" ariaLabel="Paper evidence">
        <thead><tr><th scope="col">Candidate</th><th scope="col">Symbol</th><th scope="col">Live side</th><th scope="col">Fill parity</th><th scope="col">Forward funding</th><th scope="col">Costable</th><th scope="col">Realized gross</th><th scope="col">Net</th><th scope="col">Risk</th></tr></thead>
        <tbody>
          {items.map((item) => (
            <tr key={key(item)} data-paper-evidence={item.trial_id}>
              <td><a href={trialHref(item.study_id, item.trial_id)}><code title={item.trial_id}>{item.trial_id.slice(0, 8)}…</code></a> ({item.cycle_id})</td>
              <td>{item.symbol ?? "—"}</td>
              <td className="tabular-num">
                {item.parity.live_side.live_signals_recorded ?? 0} live signals · {item.parity.live_side.paper_fills_in_cycle ?? 0} paper fills
              </td>
              <td><Badge label={item.parity.status} /></td>
              <td><Badge label={item.funding.status} /></td>
              <td><Badge label={item.funding.cost_status} /></td>
              <td className="tabular-num">{item.economics.gross_realized ? returnFigure(item.economics.gross_realized.value) : <Badge label={item.economics.status} />}</td>
              <td><Badge label={netCostability(item.economics)} /></td>
              <td><Badge label={item.risk.status} /></td>
            </tr>
          ))}
        </tbody>
      </DataTable>
      {items.map((item) => (
        <details key={`paper-${key(item)}`} className="margin-bottom-24" data-paper-evidence-detail={item.trial_id}>
          <summary>{item.trial_id.slice(0, 8)}… — parity, funding, economics and risk reasons, bound identities</summary>
          <p>
            Study <code>{item.study_id}</code> ({short(item.study_content_hash)}) · trial {short(item.trial_content_hash)} · resolved
            server-side from stored rows · assessed <time dateTime={item.as_of}>{utc(item.as_of)}</time>.
          </p>
          <h4>Live/replay fill parity: <Badge label={item.parity.status} /></h4>
          <p data-stored-evidence="parity">{storedEvidenceText(item.parity)}{item.parity.loaded ? <> · report {short(item.parity.report_hash)}</> : null}</p>
          {reasonsList(item.parity.reasons)}
          <h4>Forward funding: <Badge label={item.funding.status} /> <Badge label={item.funding.cost_status} /></h4>
          <p data-stored-evidence="funding">{storedEvidenceText(item.funding)} · charge {item.funding.charge}; evidence {short(item.funding.evidence_hash)}.</p>
          <h5>{item.funding.status === "REFUSED" ? "Why refused" : item.funding.loaded ? "Load reasons" : "Why not loaded"}</h5>
          {reasonsList(item.funding.reasons)}
          <h5>Completeness blockers</h5>
          {reasonsList(item.funding.completeness_blockers)}
          <h5>Why not costable</h5>
          {reasonsList(item.funding.economic_blockers.length ? item.funding.economic_blockers : ["NO_FUNDING_EVIDENCE_SECTION_FOR_THIS_CANDIDATE"])}
          <EconomicsDetail item={item} />
          <RiskDetail item={item} />
          <p>{item.claim} · execution authority {item.execution_authority} · {item.validated_claim}.</p>
        </details>
      ))}
    </>
  );
}

/** Exact stored identity is kept throughout this read-only review. */
export function CandidateJourney({ item, selected, unavailable }: {
  item?: CandidatePaperEvidence; selected: boolean; unavailable?: string;
}) {
  return <section id="opportunity-detail" className="panel margin-bottom-24" aria-label="Selected candidate review">
    <h2>Opportunity detail: selected research candidate</h2>
    <p>A recorded research candidate is not a recommendation or a validated opportunity.</p>
    {!selected ? <p className="empty-notice">Compare studies and choose Review candidate, risk and paper, or review a recorded signal.</p>
      : !item ? <p className="empty-notice">{unavailable ?? "Selected candidate evidence is unavailable."} No risk or price is inferred.</p>
      : <>
        <h3>{item.symbol ?? "Symbol unavailable"} / {item.trial_id.slice(0, 8)}</h3>
        <p>Study <code>{item.study_id}</code>; trial <code>{item.trial_id}</code>; cycle <code>{item.cycle_id}</code>; assessed {utc(item.as_of)}.</p>
        <p><Badge label={item.claim} /> <Badge label={item.validated_claim} /> Execution authority: {item.execution_authority}.</p>
        <nav aria-label="Selected candidate steps"><a href="#compare">Back to comparison</a> / <a href="#selected-risk">Review risk</a> / <a href="#selected-paper">Review paper evidence</a></nav>
        <div id="selected-risk"><RiskDetail item={item} /></div>
        <div id="selected-paper"><h3>Paper evidence for this candidate</h3><PaperEvidenceSection items={[item]} unavailable={undefined} /></div>
      </>}
  </section>;
}
