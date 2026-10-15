"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type IChartApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from "lightweight-charts";
import { decimalText } from "../lib/decimal-text";

/** One drawn candle. Every value a reader sees is this exact decimal text; floats only place pixels. */
export type MarketChartPoint = {
  open_at: string;
  close_at: string | null;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  bar_count: number;
  /** ``null`` when the frame declares no bar interval (completeness unknowable). */
  expected_bar_count: number | null;
  complete: boolean | null;
};

/** Never more empty slots than this are drawn for gaps; the gap list still names every one. */
const MAX_WHITESPACE = 5_000;

function seconds(iso: string): UTCTimestamp {
  return Math.floor(Date.parse(iso) / 1000) as UTCTimestamp;
}

function utcLabel(time: number, withSeconds = false): string {
  const iso = new Date(time * 1000).toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, withSeconds ? 19 : 16)} UTC`;
}

function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export function MarketChart({
  points,
  stepSeconds,
  title,
}: {
  points: MarketChartPoint[];
  /** The candle width in seconds; ``null`` for count-based buckets (gaps then cannot be drawn). */
  stepSeconds: number | null;
  title: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [hovered, setHovered] = useState<{ time: number; index: number | null } | null>(null);

  const byTime = useMemo(() => new Map(points.map((point, index) => [seconds(point.open_at), index])), [points]);

  useEffect(() => {
    const element = container.current;
    if (!element || points.length === 0) return;
    const up = token("--status-available", "#86efac");
    const down = token("--status-danger", "#fca5a5");
    const partial = token("--status-warning", "#fcd34d");
    const muted = token("--text-muted", "#94a3b8");
    const grid = token("--border-subtle", "#17243c");
    const chart = createChart(element, {
      autoSize: true,
      layout: {
        background: { color: "transparent" }, textColor: muted, fontFamily: token("--font-mono", "monospace"),
        // The built-in logo injects a <style> element (refused by the CSP) and puts this page's
        // URL in its outbound link; the figure carries a static attribution instead.
        attributionLogo: false,
      },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      crosshair: { mode: 0 },
      localization: { timeFormatter: (time: Time) => utcLabel(time as number) },
      timeScale: { timeVisible: stepSeconds === null || stepSeconds < 86_400, secondsVisible: false, borderColor: grid },
      rightPriceScale: { borderColor: grid },
    });
    chartRef.current = chart;
    const series = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });

    const data: (CandlestickData<Time> | WhitespaceData<Time>)[] = [];
    const markers: SeriesMarker<Time>[] = [];
    let whitespace = 0;
    let previous: number | null = null;
    for (const point of points) {
      const time = seconds(point.open_at);
      if (stepSeconds !== null && previous !== null && time - previous > stepSeconds) {
        const missing = (time - previous) / stepSeconds - 1;
        // Empty slots keep the time axis honest; no value is ever drawn in them.
        for (let slot = 1; slot <= missing && whitespace < MAX_WHITESPACE; slot += 1, whitespace += 1) {
          data.push({ time: (previous + slot * stepSeconds) as UTCTimestamp });
        }
        markers.push({ time, position: "aboveBar", shape: "arrowDown", color: partial, text: `gap ${missing}` });
      }
      const candle: CandlestickData<Time> = {
        time, open: Number(point.open), high: Number(point.high), low: Number(point.low), close: Number(point.close),
      };
      if (point.complete === false) {
        Object.assign(candle, { color: "transparent", borderColor: partial, wickColor: partial });
      }
      data.push(candle);
      previous = time;
    }
    series.setData(data);
    if (markers.length > 0) createSeriesMarkers(series, markers);
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((param) => {
      const time = param.time === undefined ? null : (param.time as number);
      setHovered(time === null ? null : { time, index: byTime.get(time as UTCTimestamp) ?? null });
    });
    return () => {
      chartRef.current = null;
      chart.remove();
    };
  }, [points, stepSeconds, byTime]);

  function zoom(factor: number) {
    const scale = chartRef.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    if (!scale || !range) return;
    const middle = (range.from + range.to) / 2;
    const half = Math.max(5, ((range.to - range.from) / 2) * factor);
    scale.setVisibleLogicalRange({ from: middle - half, to: middle + half });
  }

  if (points.length === 0) return <p className="empty-notice">No candles in this view.</p>;
  const shown = hovered === null ? points[points.length - 1] : hovered.index === null ? null : points[hovered.index];

  return (
    <figure className="market-chart">
      <div className="market-chart-legend">
        {shown === null ? (
          <span className="market-chart-legend-time">
            Crosshair: {utcLabel(hovered?.time ?? 0)} <span className="market-chart-partial">NO SOURCE BAR (gap, not filled)</span>
          </span>
        ) : (
          <>
            <span className="market-chart-legend-time">
              {hovered === null ? "Last candle in view" : "Crosshair"}: {utcLabel(seconds(shown.open_at))}
              {shown.close_at ? ` → ${utcLabel(seconds(shown.close_at))}` : ""}
            </span>
            <span>O <strong>{decimalText(shown.open)}</strong></span>
            <span>H <strong>{decimalText(shown.high)}</strong></span>
            <span>L <strong>{decimalText(shown.low)}</strong></span>
            <span>C <strong>{decimalText(shown.close)}</strong></span>
            <span>Vol <strong>{decimalText(shown.volume)}</strong></span>
            <span>
              Bars {shown.bar_count}
              {shown.expected_bar_count !== null ? ` of ${shown.expected_bar_count}` : ""}
            </span>
            {shown.complete === false && <span className="market-chart-partial">PARTIAL CANDLE</span>}
          </>
        )}
      </div>
      <div ref={container} className="market-chart-canvas" role="img" aria-label={title} />
      <figcaption className="market-chart-controls">
        <button type="button" onClick={() => zoom(0.5)}>Zoom in</button>
        <button type="button" onClick={() => zoom(2)}>Zoom out</button>
        <button type="button" onClick={() => chartRef.current?.timeScale().fitContent()}>Fit all</button>
        <span className="market-chart-hint">Drag to pan, scroll or pinch to zoom, hover to inspect. All times UTC.</span>
        <span className="market-chart-hint">
          Charting by <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">TradingView</a> Lightweight Charts
        </span>
      </figcaption>
    </figure>
  );
}
