import React from "react";
import Link from "next/link";
import {
  getChartSeries,
  getChartSeriesCatalog,
  getWorkspaceContext,
  stateText,
  utc,
} from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";
import { BarChart, decimalText } from "../../components/bar-chart";

export const dynamic = "force-dynamic";

const CEILING_MEANING: Record<string, string> = {
  T1_RETROSPECTIVE: "Real values with no defensible historical knowledge time: descriptive and data-quality research only, never a performance claim.",
  T4_FIRST_PARTY_CAPTURE: "Our own recorder's arrival times. Professional eligibility is still decided per sealed dataset, not by this chart.",
  NO_TIMING_AUTHORITY: "No timing contract: T2 event-time bars stay unconditional until a publication lag is declared (owner decision OR-5).",
};

export default async function ChartPage({
  searchParams,
}: {
  searchParams?: Promise<{ series?: string; instrument?: string }>;
}) {
  const params = await searchParams;
  const ctx = await getWorkspaceContext();
  const catalogResult = await getChartSeriesCatalog(ctx);
  const catalog = catalogResult.state === "AVAILABLE" ? catalogResult.value.items : [];
  const selected = catalog.find((item) => item.manifest_hash === params?.series) ?? catalog[0];
  const seriesResult = selected
    ? await getChartSeries(ctx, { manifestHash: selected.manifest_hash, instrument: params?.instrument })
    : undefined;
  const series = seriesResult?.state === "AVAILABLE" ? seriesResult.value : undefined;
  const last = series?.buckets[series.buckets.length - 1];

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Instrument Chart"
        subtitle="Bars from one exact catalogued frame, with the claim those bars can support."
        status={selected ? "AVAILABLE" : catalogResult.state === "AVAILABLE" ? "UNAVAILABLE" : catalogResult.state}
        statusLabel={selected ? `CLAIM CEILING: ${selected.tier_ceiling}` : undefined}
        asOf={ctx.evidenceTime}
      />

      <article className="panel margin-bottom-24" aria-label="Chart Series">
        <h2>
          <span>Series</span>
          <QualityStateBadge status={catalog.length > 0 ? "AVAILABLE" : catalogResult.state} />
        </h2>
        {catalog.length > 0 ? (
          <ul className="chart-series-list">
            {catalog.map((item) => (
              <li key={item.manifest_hash}>
                <Link
                  href={`/chart?series=${item.manifest_hash}`}
                  aria-current={item.manifest_hash === selected?.manifest_hash ? "page" : undefined}
                >
                  {item.label} &middot; <code>{item.tier_ceiling}</code>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-notice">
            {catalogResult.state === "AVAILABLE" ? "No chartable bar frame is catalogued." : stateText(catalogResult)}
          </p>
        )}
      </article>

      {selected && (
        <article className="panel margin-bottom-24" aria-label="Instrument Bars">
          <h2>
            <span>{series ? series.instrument : "Bars"}</span>
            <QualityStateBadge status={series ? series.state : seriesResult?.state} label={`CLAIM CEILING: ${selected.tier_ceiling}`} />
          </h2>
          <p>{CEILING_MEANING[selected.tier_ceiling] ?? "Unrecognised ceiling: treat as no timing authority."}</p>
          {series ? (
            <>
              {series.instruments.length > 1 && (
                <ul className="chart-series-list" aria-label="Instruments in frame">
                  {series.instruments.map((instrument) => (
                    <li key={instrument}>
                      <Link
                        href={`/chart?series=${selected.manifest_hash}&instrument=${encodeURIComponent(instrument)}`}
                        aria-current={instrument === series.instrument ? "page" : undefined}
                      >
                        {instrument}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
              <BarChart buckets={series.buckets} title={`${series.instrument} bars, ${series.series.label}`} />
              <KeyValueGrid
                columns={4}
                items={[
                  { key: "bars", label: "Bars in Frame", value: series.bars_in_frame_for_instrument.toLocaleString() },
                  { key: "bucket", label: "Bars per Point", value: series.bars_per_bucket.toLocaleString() },
                  { key: "last", label: "Last Close", value: last ? decimalText(last.close) : "UNAVAILABLE" },
                  { key: "last_at", label: "Last Bar (UTC)", value: last ? utc(last.last_bar_at) : "UNAVAILABLE" },
                ]}
              />
            </>
          ) : (
            <p className="empty-notice">{seriesResult ? stateText(seriesResult) : "UNAVAILABLE"}</p>
          )}
          <ProvenancePanel
            source={`instrument_chart_v1: ${selected.frame_kind} frame`}
            recordId={selected.dataset_version_id}
            contentHash={selected.manifest_hash}
            version={series?.version ?? "instrument-chart-v1"}
            asOf={ctx.evidenceTime}
            limitations={series?.limitations ?? []}
          />
        </article>
      )}
    </div>
  );
}
