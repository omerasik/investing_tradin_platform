import React from "react";
import type { ChartBucket } from "../lib/data-access";

const WIDTH = 960;
const HEIGHT = 360;
const PAD = { top: 16, right: 88, bottom: 36, left: 12 };

/** Trim a decimal string's trailing zeros for display only; the value itself is never rounded. */
export function decimalText(value: string): string {
  return value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
}

/**
 * Server-rendered candlestick chart. Pixel positions use floating point; every
 * number a reader sees (tooltips, axis labels) is the exact decimal text.
 */
export function BarChart({ buckets, title }: { buckets: ChartBucket[]; title: string }) {
  if (buckets.length === 0) return <p className="empty-notice">No bars in this series.</p>;
  const highs = buckets.map((bucket) => Number(bucket.high));
  const lows = buckets.map((bucket) => Number(bucket.low));
  const maxIndex = highs.indexOf(Math.max(...highs));
  const minIndex = lows.indexOf(Math.min(...lows));
  const top = highs[maxIndex];
  const bottom = lows[minIndex];
  const span = top - bottom || 1;
  const plotWidth = WIDTH - PAD.left - PAD.right;
  const plotHeight = HEIGHT - PAD.top - PAD.bottom;
  const step = plotWidth / buckets.length;
  const body = Math.max(1, step * 0.7);
  const y = (value: number) => PAD.top + ((top - value) / span) * plotHeight;
  const first = buckets[0];
  const last = buckets[buckets.length - 1];

  return (
    <figure className="bar-chart">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={title} preserveAspectRatio="none" width="100%">
        <title>{title}</title>
        <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(top)} y2={y(top)} className="bar-chart-grid" />
        <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(bottom)} y2={y(bottom)} className="bar-chart-grid" />
        {buckets.map((bucket, index) => {
          const open = Number(bucket.open);
          const close = Number(bucket.close);
          const x = PAD.left + index * step + step / 2;
          const rising = close >= open;
          const upper = y(Math.max(open, close));
          const lower = y(Math.min(open, close));
          return (
            <g key={bucket.first_bar_at} className={rising ? "bar-chart-up" : "bar-chart-down"}>
              <title>
                {`${bucket.first_bar_at} → ${bucket.last_bar_at} (${bucket.bar_count} bars)\nO ${decimalText(bucket.open)} H ${decimalText(bucket.high)} L ${decimalText(bucket.low)} C ${decimalText(bucket.close)}\nVolume ${decimalText(bucket.volume)}`}
              </title>
              <line x1={x} x2={x} y1={y(Number(bucket.high))} y2={y(Number(bucket.low))} />
              <rect x={x - body / 2} y={upper} width={body} height={Math.max(1, lower - upper)} />
            </g>
          );
        })}
        <text x={WIDTH - PAD.right + 6} y={y(top) + 4} className="bar-chart-label">{decimalText(buckets[maxIndex].high)}</text>
        <text x={WIDTH - PAD.right + 6} y={y(bottom) + 4} className="bar-chart-label">{decimalText(buckets[minIndex].low)}</text>
        <text x={PAD.left} y={HEIGHT - 12} className="bar-chart-label">{first.first_bar_at.replace(".000", "")}</text>
        <text x={WIDTH - PAD.right} y={HEIGHT - 12} textAnchor="end" className="bar-chart-label">{last.last_bar_at.replace(".000", "")}</text>
      </svg>
    </figure>
  );
}
