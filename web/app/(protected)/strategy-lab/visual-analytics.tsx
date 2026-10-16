import React from "react";
import Link from "next/link";
import type {
  StrategyLabBreakEvenDistribution,
  StrategyLabEvidenceIdentity,
  StrategyLabHeatmap,
  StrategyLabHeatmapCell,
} from "../../lib/data-access";
import { DataTable } from "../../components/data-table";
import { KeyValueGrid } from "../../components/key-value-grid";

/**
 * Stage B.1.2b visual analytics for one study. Every value shown is the stored text the
 * server returned; signs, ranks, bins and counts are computed on the server with Decimal.
 * The browser only maps an integer rank or count onto a shade or a bar height -- it never
 * parses, compares or aggregates a financial value.
 */

const HEAT_BUCKETS = 10;

function Claim({ label }: { label: string }) {
  return <span className="badge" data-claim={label}>{label}</span>;
}

/** The evidence a visualization was computed from must be the study on screen. */
export function evidenceMismatch(evidence: StrategyLabEvidenceIdentity, studyId: string, contentHash: string | undefined): string | null {
  if (evidence.study_id !== studyId) return "the response is for a different study";
  if (contentHash !== undefined && evidence.study_content_hash !== contentHash) return "the study content hash differs from the study shown";
  return null;
}

function heatBucket(cell: StrategyLabHeatmapCell, distinct: number): number {
  if (cell.rank === null || distinct <= 1) return 0;
  return Math.round(((cell.rank - 1) * (HEAT_BUCKETS - 1)) / (distinct - 1));
}

const CELL_TEXT: Record<string, string> = {
  METRIC_UNAVAILABLE: "metric UNAVAILABLE",
  INADMISSIBLE_PARAMETERS: "inadmissible",
  FAILED: "FAILED",
  CANCELLED: "CANCELLED",
  PENDING: "pending",
  NO_TRIAL: "no trial",
};

function rerunMark(status: string | null): string {
  if (status === null) return "";
  return status === "ESTABLISHED" ? " · D" : " · D✕";
}

function HeatCell({ cell, distinct, trialHref }: { cell: StrategyLabHeatmapCell; distinct: number; trialHref: (trialId: string) => string }) {
  const trial = cell.trial_ids.length === 1 ? cell.trial_ids[0] : undefined;
  const marks = `${cell.frozen_rank !== null ? ` · F${cell.frozen_rank}` : ""}${rerunMark(cell.decimal_rerun_status)}`;
  let body: React.ReactNode;
  if (cell.state === "VALUE") {
    body = <>{cell.value}{marks ? <span className="heatmap-mark">{marks}</span> : null}</>;
  } else if (cell.state === "AMBIGUOUS") {
    body = <>AMBIGUOUS ({cell.trial_ids.length} trials)</>;
  } else {
    body = <>{CELL_TEXT[cell.state] ?? cell.state}{marks ? <span className="heatmap-mark">{marks}</span> : null}</>;
  }
  const title = [
    cell.ordinal !== null ? `trial #${cell.ordinal}` : null,
    cell.state,
    cell.reason ? `reason ${cell.reason}` : null,
    cell.rank !== null ? `rank ${cell.rank} of ${distinct} distinct stored values (1 = lowest)` : null,
    cell.decimal_rerun_status ? `Decimal rerun ${cell.decimal_rerun_status}` : null,
  ].filter(Boolean).join(" · ");
  const className = cell.state === "VALUE" ? `heatmap-cell heat-${heatBucket(cell, distinct)}` : `heatmap-cell heatmap-cell-${cell.state.toLowerCase().replaceAll("_", "-")}`;
  return (
    <td className={className} data-state={cell.state} data-x={cell.x} data-y={cell.y} title={title}>
      {trial ? <Link href={trialHref(trial)} aria-label={`${title}: open trial detail`}>{body}</Link> : body}
    </td>
  );
}

export function HeatmapView({ heatmap, metricLabel, trialHref }: {
  heatmap: StrategyLabHeatmap; metricLabel: string; trialHref: (trialId: string) => string;
}) {
  const rows = heatmap.y_values.map((y) => heatmap.cells.filter((cell) => cell.y === y));
  return (
    <>
      <p>
        Each cell is the one stored trial with exactly these parameters, showing its stored {metricLabel} text verbatim.
        Shade is the exact rank of that value among these cells (lightest = lowest, {heatmap.distinct_values} distinct values) —
        an ordering, not a quality verdict: for drawdown a higher value is worse. No cell is highlighted as best.
        F<em>n</em> = frozen search rank; D = a Decimal authority rerun of the frozen set covers the trial (D✕ = rerun selection not established).
      </p>
      <DataTable caption={`${heatmap.metric} by ${heatmap.y} (rows) × ${heatmap.x} (columns) — search tier, gross`} ariaLabel="Parameter heatmap">
        <thead>
          <tr>
            <th scope="col">{heatmap.y} \ {heatmap.x}</th>
            {heatmap.x_values.map((x) => <th key={x} scope="col" className="tabular-num">{x}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, index) => (
            <tr key={heatmap.y_values[index]}>
              <th scope="row" className="tabular-num">{heatmap.y_values[index]}</th>
              {cells.map((cell) => <HeatCell key={`${cell.x}|${cell.y}`} cell={cell} distinct={heatmap.distinct_values} trialHref={trialHref} />)}
            </tr>
          ))}
        </tbody>
      </DataTable>
      <p data-heatmap-counts>
        Cells: {Object.entries(heatmap.counts).map(([state, count]) => `${state} ${count}`).join(" · ") || "none"}.
      </p>
      <ul className="selection-criteria" aria-label="Heatmap selection criteria">
        {heatmap.selection.map((line) => <li key={line}>{line}</li>)}
      </ul>
    </>
  );
}

const SLOT = 22;
const GAP = 4;
const PLOT_HEIGHT = 140;
const LABEL_SPACE = 34;

export function BreakEvenView({ distribution }: { distribution: StrategyLabBreakEvenDistribution }) {
  const negative = distribution.bins.filter((bin) => bin.side === "NEGATIVE");
  const positive = distribution.bins.filter((bin) => bin.side === "POSITIVE");
  const slots: { key: string; count: number; kind: "negative" | "zero" | "positive" }[] = [
    ...negative.map((bin) => ({ key: `n${bin.lower}`, count: bin.count, kind: "negative" as const })),
    { key: "zero", count: distribution.zero, kind: "zero" as const },
    ...positive.map((bin) => ({ key: `p${bin.upper}`, count: bin.count, kind: "positive" as const })),
  ];
  const maxCount = Math.max(1, ...slots.map((slot) => slot.count));
  const width = slots.length * (SLOT + GAP) + GAP;
  const height = PLOT_HEIGHT + LABEL_SPACE;
  const zeroIndex = negative.length;
  const stats = distribution.order_statistics;
  const summary = `Recorded break-even cost per side over ${distribution.eligible_count} evaluated trials: ${distribution.negative} negative, ${distribution.zero} zero, ${distribution.positive} positive, ${distribution.unavailable} unavailable.`;
  return (
    <>
      <KeyValueGrid
        columns={4}
        items={[
          { key: "eligible", label: "Eligible (evaluated) trials", value: distribution.eligible_count },
          { key: "negative", label: "Negative (gross loss before any cost)", value: distribution.negative },
          { key: "zero", label: "Exactly zero", value: distribution.zero },
          { key: "positive", label: "Positive (gross gain before costs)", value: distribution.positive },
          { key: "unavailable", label: "Value UNAVAILABLE", value: distribution.unavailable },
          { key: "outcomes", label: "All trials by recorded state", value: Object.entries(distribution.trial_outcomes).map(([k, v]) => `${k} ${v}`).join(" · ") || "none" },
          { key: "range", label: "Stored min .. max (bps/side)", value: stats.min !== undefined ? `${stats.min} .. ${stats.max}` : "UNAVAILABLE" },
          { key: "quartiles", label: "Nearest-rank p25 / median / p75", value: stats.median !== undefined ? `${stats.p25} / ${stats.median} / ${stats.p75}` : "UNAVAILABLE" },
        ]}
      />
      <figure className="break-even-figure">
        <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} role="img" aria-label={summary} className="break-even-svg">
          {slots.map((slot, index) => {
            const barHeight = slot.count === 0 ? 0 : Math.max(2, Math.round((slot.count * PLOT_HEIGHT) / maxCount));
            const x = GAP + index * (SLOT + GAP);
            return (
              <g key={slot.key}>
                <rect x={x} y={PLOT_HEIGHT - barHeight} width={SLOT} height={barHeight} className={`break-even-bar break-even-${slot.kind}`} />
                {slot.count ? <text x={x + SLOT / 2} y={Math.max(10, PLOT_HEIGHT - barHeight - 3)} textAnchor="middle" className="break-even-count">{slot.count}</text> : null}
              </g>
            );
          })}
          <line x1={0} x2={width} y1={PLOT_HEIGHT} y2={PLOT_HEIGHT} className="break-even-axis" />
          {negative.length ? <text x={GAP} y={PLOT_HEIGHT + 14} className="break-even-label">{negative[0].lower}</text> : null}
          <text x={GAP + zeroIndex * (SLOT + GAP) + SLOT / 2} y={PLOT_HEIGHT + 14} textAnchor="middle" className="break-even-label">0</text>
          {positive.length ? <text x={width - GAP} y={PLOT_HEIGHT + 14} textAnchor="end" className="break-even-label">{positive[positive.length - 1].upper}</text> : null}
          <text x={GAP} y={PLOT_HEIGHT + 30} className="break-even-label">{"< 0 bps/side"}</text>
          <text x={width - GAP} y={PLOT_HEIGHT + 30} textAnchor="end" className="break-even-label">{"> 0 bps/side"}</text>
        </svg>
        <figcaption>
          {summary} Bars within each sign are equal-width in value; the two signs have different widths, so compare counts, not bar
          positions, across zero. The table below is the exact record.
        </figcaption>
      </figure>
      <DataTable caption="Break-even bins (exact edges, server-side Decimal)" ariaLabel="Break-even bins">
        <thead><tr><th scope="col">Sign</th><th scope="col">Range (bps/side)</th><th scope="col">Trials</th></tr></thead>
        <tbody>
          {negative.map((bin) => (
            <tr key={`n${bin.lower}`} data-side="NEGATIVE"><td>negative</td><td className="tabular-num">[{bin.lower}, {bin.upper})</td><td className="tabular-num">{bin.count}</td></tr>
          ))}
          <tr data-side="ZERO"><td>zero</td><td className="tabular-num">exactly 0</td><td className="tabular-num">{distribution.zero}</td></tr>
          {positive.map((bin) => (
            <tr key={`p${bin.upper}`} data-side="POSITIVE"><td>positive</td><td className="tabular-num">({bin.lower}, {bin.upper}]</td><td className="tabular-num">{bin.count}</td></tr>
          ))}
          <tr data-side="UNAVAILABLE"><td>unavailable</td><td>absent, null or non-numeric stored value</td><td className="tabular-num">{distribution.unavailable}</td></tr>
        </tbody>
      </DataTable>
      <ul className="selection-criteria" aria-label="Break-even selection criteria">
        {distribution.selection.map((line) => <li key={line}>{line}</li>)}
      </ul>
    </>
  );
}

export function EvidenceLine({ evidence }: { evidence: StrategyLabEvidenceIdentity }) {
  return (
    <p className="evidence-identity">
      Computed from study <code>{evidence.study_content_hash.slice(0, 12)}</code>, manifest{" "}
      <code>{evidence.manifest_hash ? evidence.manifest_hash.slice(0, 12) : "none recorded"}</code>, {evidence.result_count} stored results
      of {evidence.trial_count} trials (results digest <code title={evidence.results_digest}>{evidence.results_digest.slice(0, 12)}</code>).
    </p>
  );
}

export { Claim as AnalyticsClaim };
