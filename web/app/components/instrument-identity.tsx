import React from "react";
import { QualityStateBadge } from "./quality-state-badge";

export function InstrumentIdentity({
  symbol,
  venue,
  assetClass,
  lifecycleStatus,
  syntheticDemo,
  ambiguousMapping,
}: {
  symbol: string;
  venue?: string;
  assetClass?: string;
  lifecycleStatus?: string;
  syntheticDemo?: boolean;
  ambiguousMapping?: boolean;
}) {
  return (
    <div className="instrument-identity">
      <div className="instrument-identity-header">
        <strong className="instrument-symbol">{symbol}</strong>
        {syntheticDemo && <span className="demo-mini-tag">DEMO</span>}
        {ambiguousMapping && <span className="warning-mini-tag">AMBIGUOUS</span>}
      </div>
      <div className="instrument-identity-sub">
        {assetClass && <span>{assetClass}</span>}
        {venue && <span>&bull; {venue}</span>}
        {lifecycleStatus && (
          <span className="instrument-lifecycle-badge">
            <QualityStateBadge status={lifecycleStatus} />
          </span>
        )}
      </div>
    </div>
  );
}
