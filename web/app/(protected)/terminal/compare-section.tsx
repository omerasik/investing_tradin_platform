import React from "react";
import {
  utc,
  type CandidatePaperEvidence,
  type ComparisonMetric,
  type ComparisonRow,
  type ComparisonSort,
  type TerminalComparison,
} from "../../lib/data-access";
import { DataTable } from "../../components/data-table";

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

export type CompareSelection = { studies: string[]; metric?: string; sort?: ComparisonSort };

/** The operator's selection form. Plain GET: nothing is written, nothing runs. */
function CompareForm({ studies, selection, metrics }: {
  studies: { study_id: string; label: string; strategy_family: string }[];
  selection: CompareSelection;
  metrics: string[];
}) {
  return (
    <form method="get" action="/terminal#compare" aria-label="Candidate comparison selection" className="margin-bottom-24">
      <fieldset>
        <legend>Studies to compare (at most {COMPARE_MAX_STUDIES})</legend>
        {studies.length ? studies.map((study) => (
          <div key={study.study_id}>
            <label>
              <input type="checkbox" name="compare_study" value={study.study_id} defaultChecked={selection.studies.includes(study.study_id)} />{" "}
              {study.label} <small>({study.strategy_family})</small>
            </label>
          </div>
        )) : <p>No study registered.</p>}
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
      <button type="submit">Compare selected studies</button>
    </form>
  );
}

function ParityCell({ paper }: { paper: CandidatePaperEvidence | undefined }) {
  if (!paper) return <Badge label="NOT_LOADED" />;
  return <><Badge label={paper.parity.status} />{paper.parity.loaded ? null : " no parity evidence loaded"}</>;
}

function FundingCell({ paper }: { paper: CandidatePaperEvidence | undefined }) {
  if (!paper) return <><Badge label="NOT_LOADED" /> <Badge label="NON_COSTABLE" /></>;
  return <><Badge label={paper.funding.status} /> <Badge label={paper.funding.cost_status} /></>;
}

/** Selected studies' frozen candidates side by side, one column per candidate. */
export function ComparisonSection({ studies, selection, comparison, unavailable }: {
  studies: { study_id: string; label: string; strategy_family: string }[] | undefined;
  selection: CompareSelection;
  comparison: TerminalComparison | undefined;
  unavailable: string | undefined;
}) {
  const rows = comparison?.comparison.rows ?? [];
  const paper = new Map((comparison?.paper_evidence ?? []).map((item) => [key(item), item]));
  const metric = selection.metric;
  return (
    <section aria-label="Candidate comparison">
      <h3>Side-by-side candidates</h3>
      <CompareForm studies={studies ?? []} selection={selection} metrics={metricKeys(rows)} />
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
                <tr>
                  <th scope="row">Next</th>
                  {rows.map((row) => (
                    <td key={key(row)}>
                      <a href="#candidate-readiness">Readiness</a> · <a href={paperEvidenceHref(row.identity.study_id, row.identity.trial_id)}>Paper evidence</a>
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
        <Badge label="NOT_LOADED" /> means this read loaded no such evidence, which is not the same as incomplete evidence. Funding is always{" "}
        <Badge label="NON_COSTABLE" />: no funding charge is calculated and no return changes.
      </p>
      <DataTable caption="Paper evidence by candidate (evidence, never a promotion decision)" ariaLabel="Paper evidence">
        <thead><tr><th scope="col">Candidate</th><th scope="col">Symbol</th><th scope="col">Live side</th><th scope="col">Fill parity</th><th scope="col">Forward funding</th><th scope="col">Costable</th></tr></thead>
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
            </tr>
          ))}
        </tbody>
      </DataTable>
      {items.map((item) => (
        <details key={`paper-${key(item)}`} className="margin-bottom-24" data-paper-evidence-detail={item.trial_id}>
          <summary>{item.trial_id.slice(0, 8)}… — parity and funding reasons, bound identities</summary>
          <p>
            Study <code>{item.study_id}</code> ({short(item.study_content_hash)}) · trial {short(item.trial_content_hash)} · resolved
            server-side from stored rows · assessed <time dateTime={item.as_of}>{utc(item.as_of)}</time>.
          </p>
          <h4>Live/replay fill parity: <Badge label={item.parity.status} /></h4>
          {item.parity.loaded ? <p>Report {short(item.parity.report_hash)}</p> : null}
          {reasonsList(item.parity.reasons)}
          <h4>Forward funding: <Badge label={item.funding.status} /> <Badge label={item.funding.cost_status} /></h4>
          <p>Charge {item.funding.charge}; evidence {short(item.funding.evidence_hash)}.</p>
          <h5>Why not loaded</h5>
          {reasonsList(item.funding.reasons)}
          <h5>Completeness blockers</h5>
          {reasonsList(item.funding.completeness_blockers)}
          <h5>Why not costable</h5>
          {reasonsList(item.funding.economic_blockers.length ? item.funding.economic_blockers : ["NO_FUNDING_EVIDENCE_SECTION_FOR_THIS_CANDIDATE"])}
          <p>{item.claim} · execution authority {item.execution_authority} · {item.validated_claim}.</p>
        </details>
      ))}
    </>
  );
}
