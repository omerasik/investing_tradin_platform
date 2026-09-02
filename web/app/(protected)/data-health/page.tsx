import React from "react";
import {
  getWorkspaceContext,
  getDataHealthEvidence,
  getCadenceEvidence,
  getDataHealthAssessments,
  stateText,
  utc,
} from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { DatasetVersionBadge } from "../../components/dataset-version-badge";
import { DemoEvidenceBanner } from "../../components/demo-evidence-banner";
import { FilterBar } from "../../components/filter-bar";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";

export const dynamic = "force-dynamic";

export default async function DataHealthPage({
  searchParams,
}: {
  searchParams?: Promise<{
    scope_type?: string;
    max_action?: string;
    blocking?: string;
    offset?: string;
    limit?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const scopeType = resolvedParams?.scope_type || undefined;
  const maxAction = resolvedParams?.max_action || undefined;
  const blockingParam = resolvedParams?.blocking;
  const blocking = blockingParam === "true" ? true : blockingParam === "false" ? false : undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 50) || 50;

  const ctx = await getWorkspaceContext();
  const [dataHealth, cadence, assessmentsResult] = await Promise.all([
    getDataHealthEvidence(ctx),
    getCadenceEvidence(ctx),
    getDataHealthAssessments(ctx, {
      scope_type: scopeType,
      max_action: maxAction,
      blocking,
      limit,
      offset,
    }),
  ]);

  const health = dataHealth.state === "AVAILABLE" ? dataHealth.value : undefined;
  const schedule = cadence.state === "AVAILABLE" ? cadence.value[0] : undefined;
  const assessmentsPage = assessmentsResult.state === "AVAILABLE" ? assessmentsResult.value : undefined;
  const assessments = assessmentsPage?.items ?? [];

  const overallState = assessmentsPage?.overall_state ?? (health ? (health.healthy ? "HEALTHY" : "BLOCKING") : "AVAILABLE");
  const blockingCount = assessmentsPage?.blocking_count ?? 0;
  const hasDemo = assessments.some((a) => a.synthetic_demo);

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Data Health & Quality Center"
        subtitle="Authoritative quality evaluations, data health assessments, anomaly findings, and non-bypassable ingestion gates."
        status={overallState}
        statusLabel={`SYSTEM: ${overallState}`}
        asOf={ctx.evidenceTime}
      />

      {hasDemo && (
        <DemoEvidenceBanner message="Quality assessments shown include deterministic synthetic engineering test evaluations. Live provider bypass is disabled." />
      )}

      {/* Primary Invariant Banner */}
      <div className="callout callout-info margin-bottom-20">
        <strong>Quality Gate Invariant:</strong> Blocking assessments strictly halt downstream research, signal generation, and portfolio construction. This console provides <em>zero bypass controls</em>. Corrective re-ingestion or schema remediation is required to unblock.
      </div>

      {/* Quality Summary Metrics */}
      <div className="metrics-strip">
        <div className="metric-card">
          <span className="metric-label">Overall Quality State</span>
          <div className="metric-value">
            <QualityStateBadge status={overallState} />
          </div>
          <span className="metric-sub">
            {blockingCount > 0 ? `${blockingCount} active blocking condition(s)` : "All evaluated gates passing"}
          </span>
        </div>

        <div className="metric-card">
          <span className="metric-label">Total Quality Assessments</span>
          <span className="metric-value tabular-num">{assessmentsPage?.total_assessments ?? assessments.length}</span>
          <span className="metric-sub">Durable assessment records</span>
        </div>

        <div className="metric-card">
          <span className="metric-label">Blocking Count</span>
          <span className={`metric-value tabular-num ${blockingCount > 0 ? "text-danger" : "text-success"}`}>
            {blockingCount}
          </span>
          <span className="metric-sub">{blockingCount > 0 ? "Downstream pipelines blocked" : "Zero active blocks"}</span>
        </div>

        <div className="metric-card">
          <span className="metric-label">Ingestion Cadence</span>
          <div className="metric-value">
            <QualityStateBadge status={schedule ? (schedule.overdue ? "STALE" : schedule.due ? "DUE" : "HEALTHY") : cadence.state} />
          </div>
          <span className="metric-sub">
            {schedule ? `Last success: ${utc(schedule.last_successful_at)}` : stateText(cadence)}
          </span>
        </div>
      </div>

      {/* Filter Bar */}
      <FilterBar
        groups={[
          {
            id: "filter-scope-type",
            name: "scope_type",
            label: "Scope Type",
            defaultValue: scopeType ?? "ALL",
            options: [
              { label: "All Scopes", value: "ALL" },
              { label: "Global", value: "GLOBAL" },
              { label: "Dataset", value: "DATASET" },
              { label: "Instrument", value: "INSTRUMENT" },
              { label: "Feature", value: "FEATURE" },
            ],
          },
          {
            id: "filter-max-action",
            name: "max_action",
            label: "Max Action",
            defaultValue: maxAction ?? "ALL",
            options: [
              { label: "All Actions", value: "ALL" },
              { label: "Info / Allow", value: "INFO" },
              { label: "Warn", value: "WARN" },
              { label: "Degrade Confidence", value: "DEGRADE_CONFIDENCE" },
              { label: "Block Instrument", value: "BLOCK_INSTRUMENT" },
              { label: "Block Strategy", value: "BLOCK_STRATEGY" },
              { label: "Global Block", value: "GLOBAL_BLOCK" },
            ],
          },
          {
            id: "filter-blocking",
            name: "blocking",
            label: "Gate Impact",
            defaultValue: blockingParam ?? "ALL",
            options: [
              { label: "All Assessments", value: "ALL" },
              { label: "Blocking Only", value: "true" },
              { label: "Non-Blocking", value: "false" },
            ],
          },
        ]}
        resetHref="/data-health"
        ariaLabel="Data Health Filter Bar"
      />

      {/* Assessments Ledger */}
      <article className="panel margin-bottom-24">
        <h2>
          <span>Quality Assessments &amp; Findings Ledger</span>
          <span className="badge tabular-num">{assessments.length} assessments</span>
        </h2>
        <p>Comprehensive evaluations of schema, timestamps, gaps, outlier detection, and data completeness.</p>

        {assessments.length > 0 ? (
          <>
            <DataTable caption="Data Health Assessments Table" ariaLabel="Data Health Assessments">
              <thead>
                <tr>
                  <th scope="col">Evaluated (UTC)</th>
                  <th scope="col">Scope</th>
                  <th scope="col">Dataset Version</th>
                  <th scope="col">Max Action</th>
                  <th scope="col">Blocking?</th>
                  <th scope="col">Policy Version</th>
                  <th scope="col">Findings Count</th>
                </tr>
              </thead>
              <tbody>
                {assessments.map((assessment) => (
                  <tr key={assessment.assessment_id} className={assessment.blocking ? "row-blocking" : ""}>
                    <td>
                      <time dateTime={assessment.evaluated_at}>{utc(assessment.evaluated_at)}</time>
                    </td>
                    <td>
                      <strong>{assessment.scope_type}</strong>
                      <br />
                      <code>{assessment.scope_value}</code>
                    </td>
                    <td>
                      <DatasetVersionBadge
                        version={assessment.dataset_version}
                        synthetic={assessment.synthetic_demo}
                      />
                    </td>
                    <td>
                      <QualityStateBadge status={assessment.max_action} />
                    </td>
                    <td>
                      {assessment.blocking ? (
                        <span className="status-badge status-badge-danger">
                          <span className="status-dot" aria-hidden="true" />
                          BLOCKING
                        </span>
                      ) : (
                        <span className="status-badge status-badge-available">
                          <span className="status-dot" aria-hidden="true" />
                          PASS / ALLOW
                        </span>
                      )}
                    </td>
                    <td>
                      <code>{assessment.policy_version}</code>
                    </td>
                    <td className="tabular-num">
                      <strong>{assessment.findings.length}</strong> findings
                    </td>
                  </tr>
                ))}
              </tbody>
            </DataTable>

            {/* Findings Detail Cards for active assessments */}
            <div className="findings-breakdown-section">
              <h3 className="findings-breakdown-title">
                Assessment Findings Breakdown
              </h3>
              <div className="findings-accordion-list">
                {assessments.map((assessment) => (
                  <details
                    key={`findings-${assessment.assessment_id}`}
                    className="provenance-panel margin-top-0"
                  >
                    <summary className="provenance-summary">
                      <span>
                        <strong>{assessment.scope_type}: {assessment.scope_value}</strong> &mdash;{" "}
                        {assessment.findings.length} finding(s) (Action: {assessment.max_action})
                      </span>
                      <time dateTime={assessment.evaluated_at}>{utc(assessment.evaluated_at)}</time>
                    </summary>

                    <div className="provenance-body">
                      {assessment.findings.length > 0 ? (
                        <DataTable caption={`Findings for ${assessment.assessment_id}`}>
                          <thead>
                            <tr>
                              <th scope="col">#</th>
                              <th scope="col">Check Type</th>
                              <th scope="col">Action</th>
                              <th scope="col">Observed At (UTC)</th>
                              <th scope="col">Detail</th>
                            </tr>
                          </thead>
                          <tbody>
                            {assessment.findings.map((finding) => (
                              <tr key={finding.finding_id}>
                                <td className="tabular-num">{finding.sequence}</td>
                                <td><code>{finding.check_type}</code></td>
                                <td><QualityStateBadge status={finding.action} /></td>
                                <td>{finding.observed_at ? <time dateTime={finding.observed_at}>{utc(finding.observed_at)}</time> : "N/A"}</td>
                                <td>
                                  <pre className="cell-pre-json">
                                    {JSON.stringify(finding.detail)}
                                  </pre>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </DataTable>
                      ) : (
                        <p className="empty-notice findings-empty-notice">
                          No findings reported for this assessment (all checks passed).
                        </p>
                      )}

                      <div className="findings-hash-footer">
                        Content Hash: <code className="content-hash">{assessment.content_hash}</code>
                      </div>
                    </div>
                  </details>
                ))}
              </div>
            </div>

            {assessmentsPage && (
              <Pagination
                limit={assessmentsPage.page.limit}
                offset={assessmentsPage.page.offset}
                returned={assessmentsPage.page.returned}
                hasMore={assessmentsPage.page.has_more}
                basePath="/data-health"
                searchParams={{
                  scope_type: scopeType ?? "",
                  max_action: maxAction ?? "",
                  blocking: blockingParam ?? "",
                }}
              />
            )}
          </>
        ) : (
          <p className="empty-state">No quality assessments found matching the selected filters.</p>
        )}
      </article>

      {/* Cadence and Verification Details */}
      <div className="grid-2col">
        <article className="panel">
          <h2>
            <span>Provider Health State</span>
            <QualityStateBadge status={health ? (health.healthy ? "HEALTHY" : "BLOCKING") : dataHealth.state} />
          </h2>
          <KeyValueGrid
            items={[
              { key: "provider_target", label: "Assessment Target", value: health ? `return-provider:${health.provider}` : "UNAVAILABLE" },
              { key: "health_state", label: "Health State", value: health ? (health.healthy ? "HEALTHY / ALLOW" : "BLOCKING / REVIEW REQUIRED") : dataHealth.state },
              { key: "failures", label: "Consecutive Failures", value: health?.consecutive_failures.toString() ?? "0" },
              { key: "failure_reason", label: "Failure Reason", value: health?.reason ?? "None reported" },
              { key: "checked_at", label: "Checked At (UTC)", value: health ? <time dateTime={health.checked_at}>{utc(health.checked_at)}</time> : "UNAVAILABLE" },
            ]}
          />
        </article>

        <article className="panel">
          <h2>
            <span>Ingestion Cadence Verification</span>
            <QualityStateBadge status={schedule ? (schedule.overdue ? "STALE" : schedule.due ? "DUE" : "HEALTHY") : cadence.state} />
          </h2>
          <KeyValueGrid
            items={[
              { key: "account_ref", label: "Account Reference", value: schedule?.account_id ?? "UNAVAILABLE" },
              { key: "provider_ref", label: "Provider Reference", value: schedule?.provider ?? "UNAVAILABLE" },
              { key: "ingestion_status", label: "Ingestion Status", value: schedule ? (schedule.overdue ? "STALE / OVERDUE" : schedule.due ? "DUE FOR INGESTION" : "CURRENT") : stateText(cadence) },
              { key: "last_success", label: "Last Successful Run", value: schedule?.last_successful_at ? <time dateTime={schedule.last_successful_at}>{utc(schedule.last_successful_at)}</time> : "Never" },
            ]}
          />
        </article>
      </div>

      <ProvenancePanel
        source="PostgresOperatorDashboardQueries: data_health_assessments & data_health_findings"
        version="data-health-v1"
        asOf={ctx.evidenceTime}
        limitations={[
          "Quality gates are deterministic and non-bypassable.",
          "Blocking status prevents downstream execution in research and trading workflows.",
        ]}
      />
    </div>
  );
}
