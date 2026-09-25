import React from "react";
import Link from "next/link";
import {
  getWorkspaceContext,
  getDataHealthEvidence,
  getCadenceEvidence,
  getEvidenceCatalog,
  getHistoricalDatasets,
  stateText,
  utc,
} from "../../lib/data-access";
import { TimingSourcesTable } from "../../components/timing-sources-table";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { ResearchStatusBadge } from "../../components/research-status-badge";
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
  const [dataHealth, cadence, datasetsResult, catalogResult] = await Promise.all([
    getDataHealthEvidence(ctx),
    getCadenceEvidence(ctx),
    getHistoricalDatasets(ctx, { limit, offset }),
    getEvidenceCatalog(ctx),
  ]);

  const health = dataHealth.state === "AVAILABLE" ? dataHealth.value : undefined;
  const schedule = cadence.state === "AVAILABLE" ? cadence.value[0] : undefined;
  const datasetsPage = datasetsResult.state === "AVAILABLE" ? datasetsResult.value : undefined;
  const datasets = datasetsPage?.items ?? [];
  const catalog = catalogResult.state === "AVAILABLE" ? catalogResult.value : undefined;
  const registeredTimingSources = catalog?.timing_sources.filter((source) => source.timing_contract_hash) ?? [];

  const hasDemoDatasets = datasets.some((d) => d.synthetic_demo);

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Market Data Sources"
        subtitle="Public market-data sources, the timing claim each can support, and sealed historical dataset versions."
        status={catalog ? "AVAILABLE" : catalogResult.state}
        statusLabel={catalog ? "PUBLIC MARKET DATA ONLY" : catalogResult.state}
        asOf={ctx.evidenceTime}
      />

      {hasDemoDatasets && (
        <DemoEvidenceBanner message="Market datasets listed below include sealed synthetic engineering versions produced in controlled sandbox environments. They are engineering evidence, not market observations." />
      )}

      {/* Scope and Ingestion Checkpoints Strip */}
      <div className="metrics-strip">
        <div className="metric-card">
          <span className="metric-label">Market Data Scope</span>
          <div className="metric-value">
            <QualityStateBadge status="AVAILABLE" label="PUBLIC ONLY" />
          </div>
          <span className="metric-sub">Public exchange data only. No broker, account or order access.</span>
        </div>

        <div className="metric-card">
          <span className="metric-label">Timing Contracts</span>
          <span className="metric-value tabular-num">{catalog ? registeredTimingSources.length : "UNAVAILABLE"}</span>
          <span className="metric-sub">Sources granted a historical timing ceiling</span>
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
        {/* Source Authorization Boundary */}
        <article className="panel">
          <h2>
            <span>Source Authorization Boundary</span>
            <QualityStateBadge status="AVAILABLE" label="PUBLIC DATA" />
          </h2>
          <p>
            Authorized: public Bybit V5 REST history, first-party capture of the public Bybit V5 WebSocket, and
            the free public trade archive. Still gated and fail-closed: provider credentials, paid data, broker and
            account APIs, and order placement.
          </p>
          <KeyValueGrid
            items={[
              { key: "credentials", label: "Provider Credentials", value: <code>NONE USED</code> },
              { key: "paid", label: "Paid Market Data", value: <code>NONE (RECURRING COST $0)</code> },
              { key: "broker", label: "Broker / Account / Orders", value: <code>NOT AUTHORIZED</code> },
              { key: "freshness", label: "Return Cadence Freshness", value: health ? `${health.healthy ? "HEALTHY" : "BLOCKING"}; checked ${utc(health.checked_at)}` : stateText(dataHealth) },
            ]}
          />
          <p>
            <Link href="/evidence" className="workspace-link">Open Evidence &amp; Data &rarr;</Link>
          </p>
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

      {/* Sources and Timing Ceilings */}
      <article className="panel margin-bottom-24">
        <h2>
          <span>Sources &amp; Timing Ceilings</span>
          <QualityStateBadge status={catalog ? "AVAILABLE" : catalogResult.state} />
        </h2>
        <p>
          The highest historical timing claim each source could ever support. A ceiling is not a verdict: a dataset
          reaches a tier only when its evidence is re-derived and proven.
        </p>
        {catalog ? (
          <TimingSourcesTable sources={catalog.timing_sources} />
        ) : (
          <p className="empty-notice">{stateText(catalogResult)}</p>
        )}
      </article>

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
                  <th scope="col">Evidence</th>
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
                    <td title={dataset.provenance_reasons.join(", ")}>
                      <ResearchStatusBadge classification={dataset.evidence_classification} />
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
          <p className="empty-notice">
            {datasetsResult.state === "AVAILABLE"
              ? "No sealed historical dataset versions found in database."
              : stateText(datasetsResult)}
          </p>
        )}

        <ProvenancePanel
          source="PostgresOperatorDashboardQueries: historical_dataset_versions"
          version="market-datasets-v1"
          asOf={ctx.evidenceTime}
          limitations={[
            "Only public market data is acquired; broker, account and order APIs stay unauthorized.",
            "All datasets shown are durable sealed snapshots in PostgreSQL authority.",
            "The Evidence column is provenance (real vs synthetic), not an evidence-tier verdict.",
          ]}
        />
      </article>
    </div>
  );
}
