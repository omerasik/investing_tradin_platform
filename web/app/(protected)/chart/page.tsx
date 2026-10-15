import React from "react";
import Link from "next/link";
import {
  CHART_TIMEFRAMES,
  getChartCandles,
  getChartSeries,
  getChartSeriesCatalog,
  getWorkspaceContext,
  stateText,
  utc,
  type ChartSeriesRef,
} from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";
import { MarketChart, type MarketChartPoint } from "../../components/market-chart";
import { decimalText } from "../../lib/decimal-text";

export const dynamic = "force-dynamic";

const CEILING_MEANING: Record<string, string> = {
  T1_RETROSPECTIVE: "Real values with no defensible historical knowledge time: descriptive and data-quality research only, never a performance claim.",
  T4_FIRST_PARTY_CAPTURE: "Our own recorder's arrival times. Professional eligibility is still decided per sealed dataset, not by this chart.",
  NO_TIMING_AUTHORITY: "No timing contract: T2 event-time bars stay unconditional until a publication lag is declared (owner decision OR-5).",
};

/** The terminal's research universe; a symbol without a catalogued window is shown as unavailable. */
const UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;
const STEP_SECONDS: Record<string, number> = { "1m": 60, "1h": 3_600, "4h": 14_400, "1d": 86_400 };
const DEFAULT_TIMEFRAME = "1d";

function chartHref(series: string, timeframe?: string, end?: string): string {
  const query = new URLSearchParams({ series });
  if (timeframe) query.set("tf", timeframe);
  if (end) query.set("end", end);
  return `/chart?${query.toString()}`;
}

function ageText(from: string | null | undefined, to: string): string {
  if (!from) return "UNAVAILABLE";
  const hours = (Date.parse(to) - Date.parse(from)) / 3_600_000;
  if (Number.isNaN(hours)) return "UNAVAILABLE";
  return hours >= 48 ? `${Math.floor(hours / 24)} days` : `${Math.max(0, Math.floor(hours))} hours`;
}

export default async function ChartPage({
  searchParams,
}: {
  searchParams?: Promise<{ series?: string; instrument?: string; tf?: string; end?: string }>;
}) {
  const params = await searchParams;
  const ctx = await getWorkspaceContext();
  const catalogResult = await getChartSeriesCatalog(ctx);
  const catalog = catalogResult.state === "AVAILABLE" ? catalogResult.value.items : [];
  const windows = catalog.filter((item) => item.window);
  const selected = catalog.find((item) => item.manifest_hash === params?.series) ?? windows[0] ?? catalog[0];
  const timeframes = selected?.timeframes ?? [];
  const timeframe = timeframes.includes(params?.tf ?? "") ? params!.tf! : timeframes.includes(DEFAULT_TIMEFRAME) ? DEFAULT_TIMEFRAME : timeframes[0];

  const [candlesResult, bucketsResult] = await Promise.all([
    selected && timeframe
      ? getChartCandles(ctx, { manifestHash: selected.manifest_hash, timeframe, end: params?.end })
      : Promise.resolve(undefined),
    // A frame without a declared bar interval keeps the count-bucketed read.
    selected && !timeframe
      ? getChartSeries(ctx, { manifestHash: selected.manifest_hash, instrument: params?.instrument })
      : Promise.resolve(undefined),
  ]);
  const candles = candlesResult?.state === "AVAILABLE" ? candlesResult.value : undefined;
  const buckets = bucketsResult?.state === "AVAILABLE" ? bucketsResult.value : undefined;
  const seriesResult = candlesResult ?? bucketsResult;

  const points: MarketChartPoint[] = candles
    ? candles.candles.map((c) => ({ ...c }))
    : (buckets?.buckets ?? []).map((b) => ({
      open_at: b.first_bar_at, close_at: null, open: b.open, high: b.high, low: b.low, close: b.close,
      volume: b.volume, bar_count: b.bar_count, expected_bar_count: null, complete: null,
    }));
  const instrument = candles?.series.instrument ?? buckets?.instrument ?? selected?.instrument ?? "Bars";
  // Candles know when their last source bar closed; count buckets only know when it opened.
  const lastBar = candles
    ? { at: candles.coverage.last_bar_close_at, verb: "closed" }
    : { at: buckets?.buckets.at(-1)?.last_bar_at ?? null, verb: "opened" };
  const windowFor = (symbol: string): ChartSeriesRef | undefined => windows.find((item) => item.instrument === symbol);

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Instrument Chart"
        subtitle="Historical research bars from one exact catalogued frame, with the claim those bars can support. Never live prices."
        status={selected ? "AVAILABLE" : catalogResult.state === "AVAILABLE" ? "UNAVAILABLE" : catalogResult.state}
        statusLabel={selected ? `CLAIM CEILING: ${selected.tier_ceiling}` : undefined}
        asOf={ctx.evidenceTime}
      />

      <article className="panel margin-bottom-24" aria-label="Market Chart">
        <h2>
          <span>{instrument}{timeframe ? ` · ${timeframe}` : ""}</span>
          <QualityStateBadge
            status={selected ? (candles ?? buckets)?.state ?? seriesResult?.state : catalogResult.state}
            label={selected ? `CLAIM CEILING: ${selected.tier_ceiling}` : undefined}
          />
        </h2>

        <nav aria-label="Instrument">
          <ul className="chart-series-list">
            {UNIVERSE.map((symbol) => {
              const item = windowFor(symbol);
              return (
                <li key={symbol}>
                  {item ? (
                    <Link href={chartHref(item.manifest_hash, timeframe)} aria-current={item.manifest_hash === selected?.manifest_hash ? "page" : undefined}>
                      {symbol}
                    </Link>
                  ) : (
                    <span className="chart-option-unavailable">{symbol}: UNAVAILABLE (no catalogued T2 research window)</span>
                  )}
                </li>
              );
            })}
          </ul>
        </nav>

        {selected && (
          <nav aria-label="Timeframe">
            <ul className="chart-series-list">
              {CHART_TIMEFRAMES.map((tf) => (
                <li key={tf}>
                  {timeframes.includes(tf) ? (
                    <Link href={chartHref(selected.manifest_hash, tf)} aria-current={tf === timeframe ? "page" : undefined}>{tf}</Link>
                  ) : (
                    <span className="chart-option-unavailable">{tf}: UNAVAILABLE</span>
                  )}
                </li>
              ))}
            </ul>
          </nav>
        )}

        {selected && (
          <p className="chart-historical-notice" role="note">
            <strong>HISTORICAL RESEARCH BARS: NOT LIVE PRICES.</strong>{" "}
            {lastBar.at
              ? <>Last bar {lastBar.verb} <time dateTime={lastBar.at}>{utc(lastBar.at)}</time>, {ageText(lastBar.at, ctx.evidenceTime)} before this page was rendered. The chart never updates on its own.</>
              : "No bar is available for this series."}
          </p>
        )}
        {selected && <p>{CEILING_MEANING[selected.tier_ceiling] ?? "Unrecognised ceiling: treat as no timing authority."}</p>}
        {selected && !timeframe && (
          <p className="empty-notice">
            Timeframes UNAVAILABLE: this frame declares no bar interval, so it is shown as consecutive-bar buckets
            {buckets ? ` (${buckets.bars_per_bucket.toLocaleString("en-US")} bars per point)` : ""}.
          </p>
        )}
        {selected && buckets && buckets.instruments.length > 1 && (
          <ul className="chart-series-list" aria-label="Instruments in frame">
            {buckets.instruments.map((name) => (
              <li key={name}>
                <Link
                  href={`/chart?series=${selected.manifest_hash}&instrument=${encodeURIComponent(name)}`}
                  aria-current={name === buckets.instrument ? "page" : undefined}
                >
                  {name}
                </Link>
              </li>
            ))}
          </ul>
        )}

        {selected && (candles ?? buckets) && points.length > 0 ? (
          <>
            <MarketChart
              points={points}
              stepSeconds={timeframe ? STEP_SECONDS[timeframe] : null}
              title={`${instrument} ${timeframe ?? "bucketed"} candlestick chart, ${selected.label}, ${points.length} candles, times in UTC`}
            />
            {candles && (
              <nav aria-label="Chart history" className="chart-paging">
                {candles.earlier_end
                  ? <Link href={chartHref(selected.manifest_hash, timeframe, candles.earlier_end)}>Earlier candles (before {utc(candles.earlier_end)})</Link>
                  : <span className="chart-option-unavailable">Earliest candle of the series is shown</span>}
                {candles.end && <Link href={chartHref(selected.manifest_hash, timeframe)}>Latest candles</Link>}
              </nav>
            )}
          </>
        ) : selected ? (
          <p className="empty-notice">
            {seriesResult && seriesResult.state !== "AVAILABLE"
              ? stateText(seriesResult)
              : (candles ?? buckets)?.state === "UNAVAILABLE"
                ? "UNAVAILABLE: no bar of this frame may be charted (see limitations below)."
                : "UNAVAILABLE"}
          </p>
        ) : null}

        {candles && (
          <KeyValueGrid
            columns={4}
            items={[
              { key: "first", label: "First Bar (UTC)", value: utc(candles.coverage.first_bar_at) },
              { key: "last", label: "Last Bar Close (UTC)", value: utc(candles.coverage.last_bar_close_at) },
              { key: "source", label: "1m Source Bars", value: candles.coverage.source_bars.toLocaleString("en-US") },
              {
                key: "missing", label: "Minutes Without a Bar",
                value: `${candles.coverage.missing_source_bars.toLocaleString("en-US")} of ${candles.coverage.expected_source_bars.toLocaleString("en-US")}`,
              },
              { key: "shown", label: "Candles Shown", value: candles.candles.length.toLocaleString("en-US") },
              { key: "partial", label: "Partial Candles Shown", value: candles.partial_candles.toLocaleString("en-US") },
              { key: "gaps", label: "Gaps Shown", value: candles.gaps.length.toLocaleString("en-US") },
              {
                key: "holdout", label: "Holdout Excluded",
                value: candles.coverage.holdout_boundary
                  ? `Every bar on/after ${candles.coverage.holdout_boundary.slice(0, 10)} (never read)`
                  : "Not applicable (not T2 research bars)",
              },
            ]}
          />
        )}
        {buckets && (
          <KeyValueGrid
            columns={4}
            items={[
              { key: "bars", label: "Bars in Frame", value: buckets.bars_in_frame_for_instrument.toLocaleString("en-US") },
              { key: "bucket", label: "Bars per Point", value: buckets.bars_per_bucket.toLocaleString("en-US") },
              { key: "last", label: "Last Close", value: buckets.buckets.at(-1) ? decimalText(buckets.buckets.at(-1)!.close) : "UNAVAILABLE" },
              { key: "last_at", label: "Last Bar (UTC)", value: utc(buckets.buckets.at(-1)?.last_bar_at) },
            ]}
          />
        )}

        {candles && (candles.gaps.length > 0 || candles.partial_candles > 0) && (
          <details className="chart-gap-list">
            <summary>Missing periods and partial candles in this view</summary>
            <ul>
              {candles.gaps.map((gap) => (
                <li key={gap.after_open_at}>
                  GAP: {gap.missing_candles.toLocaleString("en-US")} {timeframe} candle(s) with no source bar between {utc(gap.after_open_at)} and {utc(gap.before_open_at)}; not filled.
                </li>
              ))}
              {candles.candles.filter((c) => !c.complete).map((c) => (
                <li key={c.open_at}>
                  PARTIAL: {utc(c.open_at)} built from {c.bar_count} of {c.expected_bar_count} one-minute bars.
                </li>
              ))}
            </ul>
          </details>
        )}

        {selected && (
          <ProvenancePanel
            source={selected.window
              ? `instrument_chart_v1: T2 research window ${selected.window.first_utc_day}..${selected.window.last_utc_day} (Bybit public trade archive, ${selected.window.files} daily files)`
              : `instrument_chart_v1: ${selected.frame_kind} frame`}
            recordId={selected.dataset_version_id}
            datasetVersion={selected.window ? `window identity ${selected.window.content_hash}` : undefined}
            contentHash={selected.manifest_hash}
            version={(candles ?? buckets)?.version ?? "instrument-chart-v1"}
            asOf={ctx.evidenceTime}
            limitations={[
              ...(selected.window
                ? [
                  `Not-published days: ${selected.window.not_published_days.length ? selected.window.not_published_days.join(", ") : "none"}; rejected days: ${selected.window.rejected_days.length ? selected.window.rejected_days.join(", ") : "none"}; day gaps: ${selected.window.day_gaps.length}.`,
                  `Bar frame logical content hash ${selected.window.bar_logical_content_hash}.`,
                ]
                : []),
              ...((candles ?? buckets)?.limitations ?? []),
            ]}
          />
        )}
      </article>

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
                  href={chartHref(item.manifest_hash)}
                  aria-current={item.manifest_hash === selected?.manifest_hash ? "page" : undefined}
                >
                  {item.label} &middot; <code>CLAIM CEILING: {item.tier_ceiling}</code>
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
    </div>
  );
}
