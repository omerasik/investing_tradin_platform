import { utc } from "../lib/data-access";

export function EvidenceMeta({
  source,
  asOf,
  version,
  limitations,
  evidenceHash,
}: {
  source: string;
  asOf: string;
  version?: string;
  limitations?: string[];
  evidenceHash?: string;
}) {
  return (
    <details className="provenance">
      <summary>Evidence &amp; provenance</summary>
      <dl>
        <dt>Source</dt>
        <dd>{source}</dd>
        <dt>As of (UTC)</dt>
        <dd>
          <time dateTime={asOf}>{utc(asOf)}</time>
        </dd>
        {version ? (
          <>
            <dt>Version</dt>
            <dd>{version}</dd>
          </>
        ) : null}
        {evidenceHash ? (
          <>
            <dt>Content hash</dt>
            <dd><code>{evidenceHash}</code></dd>
          </>
        ) : null}
        <dt>Limitations</dt>
        <dd>{limitations && limitations.length > 0 ? limitations.join("; ") : "None recorded"}</dd>
      </dl>
    </details>
  );
}
