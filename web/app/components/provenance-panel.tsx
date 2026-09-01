import React from "react";
import { utc } from "../lib/data-access";

export function ProvenancePanel({
  source,
  recordId,
  version,
  datasetVersion,
  contentHash,
  asOf,
  limitations = [],
  open = false,
}: {
  source: string;
  recordId?: string;
  version?: string;
  datasetVersion?: string;
  contentHash?: string;
  asOf?: string;
  limitations?: string[];
  open?: boolean;
}) {
  return (
    <details className="provenance-panel" open={open}>
      <summary className="provenance-summary">
        <span className="provenance-title">Authority Provenance &amp; Verification</span>
        {asOf && <span className="provenance-asof">As of {utc(asOf)}</span>}
      </summary>
      <div className="provenance-body">
        <dl className="provenance-grid">
          <dt>Source Authority</dt>
          <dd><strong>{source}</strong></dd>

          {recordId && (
            <>
              <dt>Record ID</dt>
              <dd><code>{recordId}</code></dd>
            </>
          )}

          {version && (
            <>
              <dt>Contract / Schema Version</dt>
              <dd><code>{version}</code></dd>
            </>
          )}

          {datasetVersion && (
            <>
              <dt>Dataset Version</dt>
              <dd><code>{datasetVersion}</code></dd>
            </>
          )}

          {contentHash && (
            <>
              <dt>Cryptographic Hash (SHA-256)</dt>
              <dd><code className="content-hash">{contentHash}</code></dd>
            </>
          )}

          {asOf && (
            <>
              <dt>Knowledge Timestamp (UTC)</dt>
              <dd><time dateTime={asOf}>{utc(asOf)}</time></dd>
            </>
          )}
        </dl>

        {limitations.length > 0 && (
          <div className="provenance-limitations">
            <span className="limitations-heading">Authority Limitations:</span>
            <ul>
              {limitations.map((limitation, index) => (
                <li key={index}>{limitation}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}
