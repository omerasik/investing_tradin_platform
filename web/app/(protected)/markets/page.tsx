import React from "react";
import {
  getWorkspaceContext,
  getDataHealthEvidence,
  getCadenceEvidence,
  getHistoricalDatasets,
  stateText,
  utc,
} from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { DatasetVersionBadge } from "../../components/dataset-version-badge";
import { DemoEvidenceBanner } from "../../components/demo-evidence-banner";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";

export const dynamic = "force-dynamic";

export default async function MarketsPage({
  searchParams,
}: {
  searchParams?: Promise<{ offset?: string; limit?: string }>;
}) {
  const resolvedParams = await searchParams;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 50) || 50;

  const ctx = await getWorkspaceContext();
  const [dataHealth, cadence, datasetsResult] = await Promise.all([
    getDataHealthEvidence(ctx),
    getCadenceEvidence(ctx),
    getHistoricalDatasets(ctx, { limit, offset }),
  ]);

  const health = dataHealth.state === "AVAILABLE" ? dataHealth.value : undefined;
  const schedule = cadence.state === "AVAILABLE" ? cadence.value[0] : undefined;
  const datasetsPage = datasetsResult.state === "AVAILABLE" ? datasetsResult.value : undefined;
  const datasets = datasetsPage?.items ?? [];

  const hasDemoDatasets = datasets.some((d) => d.synthetic_demo);

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Market & Data Workspaces"
        subtitle="Provider authorization status, ingestion checkpoints, and historical sealed dataset versions."
        status={health ? "AVAILABLE" : dataHealth.state}
        statusLabel={health ? "EXTERNAL_BLOCKED (TRUTHFUL)" : dataHealth.state}
        asOf={ctx.evidenceTime}
      />

      {hasDemoDatasets && (
        <DemoEvidenceBanner message="Market datasets listed below include sealed synthetic engineering versions produced in controlled sandbox environments. Live market data feeds remain disabled." />
      )}

      {/* Provider Status and Ingestion Checkpoints Strip */}
      <div className="metrics-strip">
        <div className="metric-card">
          <span className="metric-label">Provider Status</span>
          <div className="metric-value">
            <QualityStateBadge status="EXTERNAL_BLOCKED" label="EXTERNAL_BLOCKED" />
          </div>
          <span className="metric-sub">Zero external market feeds authorized</span>
        </div>

        <div className="metric-card">
          <span className="metric-label">Ingestion Checkpoint</span>
          <div className="metric-value">
            <QualityStateBadge status={schedule ? (schedule.overdue ? "STALE" : schedule.due ? "DUE" : "HEALTHY") : cadence.state} />
          </div>
          <span className="metric-sub">
            {schedule ? `Last success: ${utc(schedule.last_successful_at)}` : stateText(cadence)}
          </span>
        </div>

        <div className="metric-card">
          <span className="metric-label">Sealed Datasets</span>
          <span className="metric-value tabular-num">{datasets.length}</span>
          <span className="metric-sub">Durable content-addressed versions</span>
        </div>
      </div>

      <div className="grid-2col margin-bottom-24">
        {/* Provider Authorization & Terms */}
        <article className="panel">
          <h2>
            <span>Provider Authorization &amp; Terms</span>
            <QualityStateBadge status="EXTERNAL_BLOCKED" label="BLOCKED" />
          </h2>
          <p>
            Cryptographic credential and provider isolation policy. External market data feeds are blocked by system invariant.
          </p>
          <KeyValueGrid
            items={[
              { key: "provider", label: "Active Provider", value: health?.provider ?? "EXTERNAL_BLOCKED" },
              { key: "authorization", label: "Authorization Reference", value: "EXTERNAL_BLOCKED (NO_LIVE_PROVIDER_AUTHORIZED)" },
              { key: "freshness", label: "Cadence Freshness", value: health ? `${health.healthy ? "HEALTHY" : "BLOCKING"}; checked ${utc(health.checked_at)}` : stateText(dataHealth) },
              { key: "sandbox_mode", label: "Sandbox Invariant", value: <code>LIVE_MARKET_FEED: BLOCKED</code> },
            ]}
          />
        </article>

        {/* Ingestion Cadences */}
        <article className="panel">
          <h2>
            <span>Ingestion Cadence Verification</span>
            <QualityStateBadge status={schedule ? (schedule.overdue ? "STALE" : schedule.due ? "DUE" : "HEALTHY") : cadence.state} />
          </h2>
          <p>Expected arrival cadence and operational checkpoint ledger.</p>
          <KeyValueGrid
            items={[
              { key: "account", label: "Return Account ID", value: schedule?.account_id ?? "UNAVAILABLE" },
              { key: "provider_sched", label: "Cadence Provider", value: schedule?.provider ?? "UNAVAILABLE" },
              {
                key: "cadence_status",
                label: "Cadence Status",
                value: schedule
                  ? `${schedule.overdue ? "STALE / OVERDUE" : schedule.due ? "DUE" : "CURRENT"}; last success ${utc(schedule.last_successful_at)}`
                  : stateText(cadence),
              },
              { key: "approved_by", label: "Cadence Approver", value: schedule?.approved_by ?? "UNAVAILABLE" },
            ]}
          />
        </article>
      </div>

      {/* Historical Sealed Dataset Versions */}
      <article className="panel">
        <h2>
          <span>Historical Sealed Dataset Versions</span>
          <QualityStateBadge status={datasetsPage?.state ?? datasetsResult.state} />
        </h2>
        <p>
          Immutable, point-in-time sealed dataset versions used for research, feature engineering, and strategy evaluation.
        </p>

        {datasets.length > 0 ? (
          <>
            <DataTable caption="Historical Sealed Dataset Versions Table" ariaLabel="Historical Sealed Dataset Versions">
              <thead>
                <tr>
                  <th scope="col">Dataset Version</th>
                  <th scope="col">Provider</th>
                  <th scope="col">Dataset Name</th>
                  <th scope="col">Asset Scope</th>
                  <th scope="col">Observations</th>
                  <th scope="col">Sealed At (UTC)</th>
                  <th scope="col">Content Hash (SHA-256)</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {datasets.map((dataset) => (
                  <tr key={dataset.dataset_version_id}>
                    <td>
                      <DatasetVersionBadge
                        version={dataset.version}
                        sealed={dataset.status === "SEALED"}
                        synthetic={dataset.synthetic_demo}
                      />
                    </td>
                    <td>{dataset.provider}</td>
                    <td><strong>{dataset.dataset_name}</strong></td>
                    <td><code>{dataset.asset_scope}</code></td>
                    <td className="tabular-num">{dataset.observation_count.toLocaleString()}</td>
                    <td>
                      <time dateTime={dataset.created_at}>{utc(dataset.created_at)}</time>
                    </td>
                    <td>
                      <code className="content-hash" title={dataset.content_hash}>
                        {dataset.content_hash.slice(0, 16)}&hellip;
                      </code>
                    </td>
                    <td>
                      <QualityStateBadge status={dataset.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </DataTable>

            {datasetsPage && (
              <Pagination
                limit={datasetsPage.page.limit}
                offset={datasetsPage.page.offset}
                returned={datasetsPage.page.returned}
                hasMore={datasetsPage.page.has_more}
                basePath="/markets"
                searchParams={{ limit: String(limit) }}
              />
            )}
          </>
        ) : (
          <p className="empty-notice">No sealed historical dataset versions found in database.</p>
        )}

        <ProvenancePanel
          source="PostgresOperatorDashboardQueries: historical_dataset_versions"
          version="market-datasets-v1"
          asOf={ctx.evidenceTime}
          limitations={[
            "External provider data feeds are blocked by policy.",
            "All datasets shown are durable sealed snapshots in PostgreSQL authority.",
          ]}
        />
      </article>
    </div>
  );
}
