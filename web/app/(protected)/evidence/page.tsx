import React from "react";
import {
  getCaptureAvailability,
  getEvidenceCatalog,
  getWorkspaceContext,
  stateText,
  utc,
  type CaptureAvailability,
} from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { DataTable } from "../../components/data-table";
import { ProvenancePanel } from "../../components/provenance-panel";
import { TimingSourcesTable } from "../../components/timing-sources-table";

export const dynamic = "force-dynamic";

function gib(bytes: number | null): string {
  return bytes === null ? "UNAVAILABLE" : `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

function mib(bytes: number): string {
  return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
}

function duration(seconds: number): string {
  if (seconds < 120) return `${seconds.toFixed(1)} s`;
  if (seconds < 7200) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3600).toFixed(2)} h`;
}

function signedSeconds(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)} s`;
}

type CaptureSource = CaptureAvailability["sources"][number];

function CaptureSourceDetail({ source }: { source: CaptureSource }) {
  const label = `${source.exchange_symbol} ${source.purpose.toLowerCase()} capture`;
  return (
    <section className="margin-bottom-24" aria-label={`${label} detail`}>
      <h3>
        {source.exchange_symbol} &middot; {source.purpose}
      </h3>
      {source.windows.length > 0 ? (
        <DataTable caption={`${label}: proven coverage windows`} ariaLabel={`${label} proven coverage windows`}>
          <thead>
            <tr>
              <th scope="col">Session</th>
              <th scope="col">Start (UTC)</th>
              <th scope="col">Last Proven (UTC)</th>
              <th scope="col">Records</th>
              <th scope="col">End Proof</th>
            </tr>
          </thead>
          <tbody>
            {source.windows.map((window) => (
              <tr key={`${window.session_id}-${window.start_at}`}>
                <td><code title={window.session_id}>{window.session_id.slice(0, 8)}&hellip;</code></td>
                <td><time dateTime={window.start_at}>{utc(window.start_at)}</time></td>
                <td><time dateTime={window.last_proven_at}>{utc(window.last_proven_at)}</time></td>
                <td className="tabular-num">{window.record_count.toLocaleString()}</td>
                <td><code>{window.end_proof}</code></td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      ) : (
        <p className="empty-notice">No finalized partition proves any coverage for this source yet.</p>
      )}
      {source.gaps.length > 0 && (
        <DataTable caption={`${label}: unproven gaps`} ariaLabel={`${label} unproven gaps`}>
          <thead>
            <tr>
              <th scope="col">Gap Start (UTC)</th>
              <th scope="col">Gap End (UTC)</th>
              <th scope="col">Kind</th>
            </tr>
          </thead>
          <tbody>
            {source.gaps.map((gap) => (
              <tr key={gap.start_at}>
                <td><time dateTime={gap.start_at}>{utc(gap.start_at)}</time></td>
                <td>{gap.end_at ? <time dateTime={gap.end_at}>{utc(gap.end_at)}</time> : "OPEN"}</td>
                <td><code>{gap.kind}</code></td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      )}
      {source.not_finalized.length > 0 && (
        <DataTable caption={`${label}: partitions that prove nothing`} ariaLabel={`${label} partitions that prove nothing`}>
          <thead>
            <tr>
              <th scope="col">Partition</th>
              <th scope="col">Reasons</th>
            </tr>
          </thead>
          <tbody>
            {source.not_finalized.map((item) => (
              <tr key={item.partition}>
                <td><code>{item.partition}</code></td>
                <td><code>{item.reasons.join(", ")}</code></td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      )}
    </section>
  );
}

export default async function EvidencePage() {
  const ctx = await getWorkspaceContext();
  const [catalogResult, captureResult] = await Promise.all([getEvidenceCatalog(ctx), getCaptureAvailability(ctx)]);
  const catalog = catalogResult.state === "AVAILABLE" ? catalogResult.value : undefined;
  const capture = captureResult.state === "AVAILABLE" ? captureResult.value : undefined;
  const production = capture?.sources.find((source) => source.purpose === "PRODUCTION");
  const clock = production?.latest_clock_offset ?? null;

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Evidence & Data"
        subtitle="What evidence the platform holds, from which source, which timing claim it could support, and what first-party capture has actually proven."
        status={catalog ? catalog.state : catalogResult.state}
        asOf={ctx.evidenceTime}
      />

      <div className="metrics-strip">
        <div className="metric-card">
          <span className="metric-label">Production Capture Proven</span>
          <span className="metric-value tabular-num">{production ? duration(production.proven_seconds) : "UNAVAILABLE"}</span>
          <span className="metric-sub">
            {production
              ? `${production.windows.length} finalized window(s); ${production.not_finalized.length} partition(s) prove nothing`
              : capture ? capture.state : stateText(captureResult)}
          </span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Host Clock Offset</span>
          <span className="metric-value tabular-num">{clock ? signedSeconds(clock.offset_estimate_seconds) : "UNAVAILABLE"}</span>
          <span className="metric-sub">
            {clock
              ? `± ${clock.offset_bound_seconds.toFixed(3)} s vs venue; sampled ${utc(clock.sampled_at)}`
              : "No recorded clock-offset sample"}
          </span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Sealed T4 Segments</span>
          <span className="metric-value tabular-num">{catalog ? catalog.t4_dataset_total : "UNAVAILABLE"}</span>
          <span className="metric-sub">Catalogued first-party seals (identity, not a tier verdict)</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Capture Disk Free</span>
          <span className="metric-value tabular-num">{capture ? gib(capture.disk_free_bytes) : "UNAVAILABLE"}</span>
          <span className="metric-sub">Recorder stops cleanly below its floor</span>
        </div>
      </div>

      <article className="panel margin-bottom-24">
        <h2>
          <span>Sources &amp; Timing Ceilings</span>
          <QualityStateBadge status={catalog ? "AVAILABLE" : catalogResult.state} />
        </h2>
        <p>
          T1 retrospective values, T2 venue event time (needs a declared publication lag), T4 first-party recorder
          arrival. A ceiling is the most a source could support; professional eligibility is decided per dataset.
        </p>
        {catalog ? <TimingSourcesTable sources={catalog.timing_sources} /> : <p className="empty-notice">{stateText(catalogResult)}</p>}
      </article>

      <article className="panel margin-bottom-24" aria-label="First-Party Capture Availability">
        <h2>
          <span>First-Party Capture Availability</span>
          <QualityStateBadge status={capture ? capture.state : captureResult.state} />
        </h2>
        <p>
          Only finalized (COMPLETE) partitions prove coverage. Open or interrupted partitions prove nothing, and gaps
          between proven windows are never bridged. Files cannot show whether a recorder is running right now.
        </p>
        {capture && capture.sources.length > 0 ? (
          <>
            <DataTable caption="Capture Sources" ariaLabel="Capture Sources">
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Purpose</th>
                  <th scope="col">Proven Duration</th>
                  <th scope="col">Proven Records</th>
                  <th scope="col">Gaps</th>
                  <th scope="col">Not Finalized</th>
                  <th scope="col">Latest Clock Offset</th>
                </tr>
              </thead>
              <tbody>
                {capture.sources.map((source) => (
                  <tr key={source.source_id}>
                    <td><strong>{source.exchange_symbol}</strong></td>
                    <td><code>{source.purpose}</code></td>
                    <td className="tabular-num">{duration(source.proven_seconds)}</td>
                    <td className="tabular-num">{source.proven_record_count.toLocaleString()}</td>
                    <td className="tabular-num">{source.gaps.length}</td>
                    <td className="tabular-num">{source.not_finalized.length}</td>
                    <td className="tabular-num">
                      {source.latest_clock_offset ? signedSeconds(source.latest_clock_offset.offset_estimate_seconds) : "NONE"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
            {capture.sources
              .filter((source) => source.purpose === "PRODUCTION")
              .map((source) => <CaptureSourceDetail key={source.source_id} source={source} />)}
            {capture.unattributed.length > 0 && (
              <p className="empty-notice">
                {capture.unattributed.length} partition(s) belong to no authorized capture contract and prove nothing.
              </p>
            )}
          </>
        ) : (
          <p className="empty-notice">
            {capture
              ? capture.state === "AVAILABLE" ? "The configured archive holds no capture partitions." : capture.limitations.join(" ")
              : stateText(captureResult)}
          </p>
        )}
      </article>

      <div className="grid-2col margin-bottom-24">
        <article className="panel" aria-label="Sealed First-Party T4 Segments">
          <h2>
            <span>Sealed First-Party Segments</span>
            <QualityStateBadge status={catalog ? (catalog.t4_dataset_total > 0 ? "AVAILABLE" : "UNAVAILABLE") : catalogResult.state} />
          </h2>
          <p>Catalogued seals of finalized capture partitions. Each tier is re-derived from raw capture when used.</p>
          {catalog && catalog.t4_datasets.length > 0 ? (
            <DataTable caption="Sealed First-Party Segments" ariaLabel="Sealed First-Party Segments">
              <thead>
                <tr>
                  <th scope="col">UTC Day</th>
                  <th scope="col">Knowledge Window (UTC)</th>
                  <th scope="col">Observations</th>
                  <th scope="col">Distinct Knowledge Times</th>
                  <th scope="col">Content Hash</th>
                </tr>
              </thead>
              <tbody>
                {catalog.t4_datasets.map((item) => (
                  <tr key={item.dataset_version_id}>
                    <td>{item.utc_day}</td>
                    <td>{utc(item.first_market_knowledge_at)} &rarr; {utc(item.last_market_knowledge_at)}</td>
                    <td className="tabular-num">{item.observation_count.toLocaleString()}</td>
                    <td className="tabular-num">{item.distinct_knowledge_time_count.toLocaleString()}</td>
                    <td><code className="content-hash" title={item.content_hash}>{item.content_hash.slice(0, 16)}&hellip;</code></td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          ) : (
            <p className="empty-notice">
              {catalog ? "No first-party segment has been sealed yet." : stateText(catalogResult)}
            </p>
          )}
        </article>

        <article className="panel" aria-label="Public Trade Archive Datasets">
          <h2>
            <span>Public Trade Archive (T2 Event Time)</span>
            <QualityStateBadge status={catalog ? (catalog.public_archive_dataset_total > 0 ? "AVAILABLE" : "UNAVAILABLE") : catalogResult.state} />
          </h2>
          <p>Reconstructed from the free public archive. No result is conditional research until a publication lag is declared.</p>
          {catalog && catalog.public_archive_datasets.length > 0 ? (
            <DataTable caption="Public Trade Archive Datasets" ariaLabel="Public Trade Archive Datasets">
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">UTC Days</th>
                  <th scope="col">Trades</th>
                  <th scope="col">Publication Lag</th>
                </tr>
              </thead>
              <tbody>
                {catalog.public_archive_datasets.map((item) => (
                  <tr key={item.dataset_version_id}>
                    <td><strong>{item.symbol}</strong></td>
                    <td>{item.first_utc_day} &rarr; {item.last_utc_day} ({item.file_count})</td>
                    <td className="tabular-num">{item.trade_count.toLocaleString()}</td>
                    <td><code>{item.publication_lag_slot}</code></td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          ) : (
            <p className="empty-notice">
              {catalog ? "No public archive dataset has been catalogued." : stateText(catalogResult)}
            </p>
          )}
        </article>
      </div>

      <div className="grid-2col margin-bottom-24">
        <article className="panel" aria-label="Historical Dataset Sources">
          <h2>
            <span>Historical Datasets by Source</span>
            <QualityStateBadge status={catalog ? (catalog.historical_sources.length > 0 ? "AVAILABLE" : "UNAVAILABLE") : catalogResult.state} />
          </h2>
          {catalog && catalog.historical_sources.length > 0 ? (
            <DataTable caption="Historical Datasets by Source" ariaLabel="Historical Datasets by Source">
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col">Datasets (Sealed)</th>
                  <th scope="col">Tier Ceiling</th>
                  <th scope="col">Latest (UTC)</th>
                </tr>
              </thead>
              <tbody>
                {catalog.historical_sources.map((item) => (
                  <tr key={item.source_id}>
                    <td>
                      <strong>{item.provider}</strong> / {item.dataset_name}
                      {item.synthetic_marker && <> <code>SYNTHETIC</code></>}
                    </td>
                    <td className="tabular-num">{item.dataset_count} ({item.sealed_count})</td>
                    <td><code>{item.tier_ceiling}</code></td>
                    <td>{utc(item.latest_created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          ) : (
            <p className="empty-notice">{catalog ? "No historical dataset versions." : stateText(catalogResult)}</p>
          )}
        </article>

        <article className="panel" aria-label="Research Data Plane Frames">
          <h2>
            <span>Research Data Plane Frames</span>
            <QualityStateBadge status={catalog ? (catalog.research_frames.length > 0 ? "AVAILABLE" : "UNAVAILABLE") : catalogResult.state} />
          </h2>
          <p>Content-addressed Parquet frames catalogued in PostgreSQL.</p>
          {catalog && catalog.research_frames.length > 0 ? (
            <DataTable caption="Research Data Plane Frames" ariaLabel="Research Data Plane Frames">
              <thead>
                <tr>
                  <th scope="col">Frame Kind</th>
                  <th scope="col">Manifests</th>
                  <th scope="col">Rows</th>
                  <th scope="col">Size</th>
                </tr>
              </thead>
              <tbody>
                {catalog.research_frames.map((item) => (
                  <tr key={item.frame_kind}>
                    <td><code>{item.frame_kind}</code></td>
                    <td className="tabular-num">{item.manifest_count}</td>
                    <td className="tabular-num">{item.row_count.toLocaleString()}</td>
                    <td className="tabular-num">{mib(item.total_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          ) : (
            <p className="empty-notice">{catalog ? "No research frames catalogued." : stateText(catalogResult)}</p>
          )}
        </article>
      </div>

      <ProvenancePanel
        source="evidence_catalog_v1: timing contracts, PostgreSQL catalogs, capture archive"
        version={catalog?.version ?? "evidence-catalog-v1"}
        asOf={catalog?.as_of ?? ctx.evidenceTime}
        limitations={[...(catalog?.limitations ?? []), ...(capture?.limitations ?? [])]}
      />
    </div>
  );
}
