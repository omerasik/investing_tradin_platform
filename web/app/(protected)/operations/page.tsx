import Link from "next/link";
import { getWorkspaceContext, getSreEvidence, stateText, utc } from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { StatusBadge } from "../../components/status-badge";
import { DataTable } from "../../components/data-table";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";

export const dynamic = "force-dynamic";

const UUID_PATTERN = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

function AuditEvidenceLink({ reference }: { reference: string }) {
  if (!UUID_PATTERN.test(reference)) return <span>{reference}</span>;
  return (
    <Link href={`/audit?selected=${encodeURIComponent(reference)}`} className="table-link">
      {reference} &rarr; Audit
    </Link>
  );
}

export default async function OperationsPage({
  searchParams,
}: {
  searchParams?: Promise<{ incident?: string }>;
}) {
  const resolvedParams = await searchParams;
  const highlightedIncident = resolvedParams?.incident?.trim() || undefined;

  const ctx = await getWorkspaceContext();
  const sre = await getSreEvidence(ctx);
  const data = sre.state === "AVAILABLE" ? sre.value : undefined;

  const openIncidents = data?.incidents.filter((item) => item.status !== "RESOLVED") ?? [];
  const resolvedIncidents = data?.incidents.filter((item) => item.status === "RESOLVED") ?? [];

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Operations &amp; SRE"
        subtitle="Service identity, dependency health, SLO target vs. measured evidence, incidents, and recovery drills."
        status={sre.state}
        asOf={ctx.evidenceTime}
      />

      <p className="warning" role="note">
        TARGET and MEASURED are distinct evidence classes. A candidate SLO target is never presented as an achieved measurement.
      </p>

      {data ? (
        <>
          <article className="panel">
            <h2><span>System Overview</span></h2>
            <KeyValueGrid
              columns={4}
              items={[
                { key: "postgres", label: "PostgreSQL", value: <StatusBadge status={data.postgres_state} /> },
                { key: "provider", label: "Provider", value: <StatusBadge status={data.provider_state} /> },
                { key: "ingestion", label: "Ingestion Checkpoint", value: <StatusBadge status={data.ingestion_checkpoint_freshness} /> },
                { key: "dataset", label: "Dataset Freshness", value: <StatusBadge status={data.dataset_freshness} /> },
                { key: "feature", label: "Feature Freshness", value: <StatusBadge status={data.feature_freshness} /> },
                { key: "research", label: "Research Jobs", value: <StatusBadge status={data.research_job_health} /> },
                { key: "signals", label: "Signals", value: <StatusBadge status={data.signal_freshness} /> },
                { key: "risk", label: "Risk", value: <StatusBadge status={data.risk_status} /> },
                { key: "reconciliation", label: "Reconciliation", value: <StatusBadge status={data.reconciliation_status} /> },
                { key: "backup", label: "Backup / Restore", value: <StatusBadge status={data.backup_restore_status} /> },
                { key: "kill-switch", label: "Kill Switch", value: <StatusBadge status={data.kill_switch_state} /> },
              ]}
            />
            <p className="empty-notice">All states above are read directly from persisted SRE evidence; none are calculated optimistically.</p>
          </article>

          <article className="panel">
            <h2><span>Service Identity</span></h2>
            <KeyValueGrid
              columns={4}
              items={[
                { key: "subsystem", label: "Subsystem", value: data.subsystem },
                { key: "version", label: "Service Version", value: <code>{data.version}</code> },
                { key: "environment", label: "Environment", value: data.environment },
                { key: "deployment", label: "Deployment Status", value: <StatusBadge status={data.deployment_status} /> },
              ]}
            />
          </article>

          <article className="panel">
            <h2>
              <span>Dependency Health</span>
              <span className="badge tabular-num">{data.dependencies.length} probes</span>
            </h2>
            {data.dependencies.length > 0 ? (
              <DataTable caption="Dependency Probes" ariaLabel="Dependency Health">
                <thead>
                  <tr>
                    <th scope="col">Dependency</th>
                    <th scope="col">Status</th>
                    <th scope="col">Checked At (UTC)</th>
                    <th scope="col">Latency</th>
                    <th scope="col">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {data.dependencies.map((dep) => (
                    <tr key={dep.dependency}>
                      <td><strong>{dep.dependency}</strong></td>
                      <td><StatusBadge status={dep.status} /></td>
                      <td><small>{utc(dep.checked_at)}</small></td>
                      <td className="tabular-num">{dep.latency_ms !== null ? `${dep.latency_ms} ms` : "UNAVAILABLE"}</td>
                      <td>{dep.reason ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            ) : (
              <p className="empty-state">UNAVAILABLE: no persisted dependency probes.</p>
            )}
          </article>

          <article className="panel">
            <h2>
              <span>SLO Workspace</span>
              <span className="badge">TARGET &ne; MEASURED</span>
            </h2>
            {data.slos.length > 0 ? (
              <DataTable caption="Service Level Objectives" ariaLabel="SLO Target vs Measured">
                <thead>
                  <tr>
                    <th scope="col">Objective</th>
                    <th scope="col">Indicator</th>
                    <th scope="col">TARGET</th>
                    <th scope="col">MEASURED</th>
                    <th scope="col">Window</th>
                    <th scope="col">Claim Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.slos.map((item) => (
                    <tr key={item.slo_policy_version_id}>
                      <td><strong>{item.name}</strong></td>
                      <td><small>{item.indicator}</small></td>
                      <td>
                        <span className="badge badge-warning">TARGET</span> {item.target}
                      </td>
                      <td>
                        <StatusBadge status={item.measured_state} />{" "}
                        <strong className="metric-value">{item.measured_value ?? "UNAVAILABLE"}</strong>
                      </td>
                      <td><small>{item.window_start ? utc(item.window_start) : "UNAVAILABLE"} &ndash; {item.window_end ? utc(item.window_end) : "UNAVAILABLE"}</small></td>
                      <td>{item.claim_status ?? "NO MEASUREMENT"}</td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            ) : (
              <p className="empty-state">UNAVAILABLE: no persisted SLO policy evidence.</p>
            )}
          </article>

          <article className="panel">
            <h2>
              <span>Incidents</span>
              <span className="badge tabular-num">{openIncidents.length} open / {resolvedIncidents.length} resolved</span>
            </h2>
            {data.incidents.length > 0 ? (
              <DataTable caption="Incident Ledger" ariaLabel="Incidents">
                <thead>
                  <tr>
                    <th scope="col">Severity</th>
                    <th scope="col">Subsystem</th>
                    <th scope="col">Opened</th>
                    <th scope="col">Acknowledged</th>
                    <th scope="col">Resolved</th>
                    <th scope="col">Status</th>
                    <th scope="col">Reason</th>
                    <th scope="col">Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {data.incidents.map((item) => (
                    <tr
                      key={item.incident_id}
                      className={[
                        item.status === "RESOLVED" ? "incident-resolved" : "incident-open",
                        item.incident_id === highlightedIncident ? "row-selected" : "",
                      ].filter(Boolean).join(" ")}
                    >
                      <td><StatusBadge status={item.severity} /></td>
                      <td>{item.subsystem}</td>
                      <td><small>{utc(item.opened_at)}</small></td>
                      <td><small>{item.acknowledged_at ? utc(item.acknowledged_at) : "UNACKNOWLEDGED"}</small></td>
                      <td><small>{item.resolved_at ? utc(item.resolved_at) : "UNRESOLVED"}</small></td>
                      <td><StatusBadge status={item.status} /></td>
                      <td><small>{item.reason}</small></td>
                      <td><AuditEvidenceLink reference={item.evidence_reference} /></td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            ) : (
              <p className="empty-state">UNAVAILABLE: no persisted incident evidence. (No incidents does not prove the system is healthy.)</p>
            )}
          </article>

          <article className="panel">
            <h2>
              <span>Failure &amp; Recovery Drills</span>
              <span className="badge">ENGINEERING / DRILL EVIDENCE</span>
            </h2>
            {data.failure_drills.length > 0 ? (
              <DataTable caption="Failure and Recovery Drill History" ariaLabel="Failure and Recovery Drills">
                <thead>
                  <tr>
                    <th scope="col">Scenario</th>
                    <th scope="col">Expected Protection</th>
                    <th scope="col">Observed Protection</th>
                    <th scope="col">Completed (UTC)</th>
                    <th scope="col">Result</th>
                    <th scope="col">Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {data.failure_drills.map((item) => (
                    <tr key={item.drill_run_id}>
                      <td><strong>{item.scenario}</strong></td>
                      <td>{item.expected_protection}</td>
                      <td>{item.observed_protection}</td>
                      <td><small>{utc(item.completed_at)}</small></td>
                      <td><StatusBadge status={item.passed ? "PASSED" : "FAILED"} /></td>
                      <td><AuditEvidenceLink reference={item.evidence_reference} /></td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            ) : (
              <p className="empty-state">UNAVAILABLE: no persisted drill evidence.</p>
            )}
            <p className="empty-notice">
              Drill outcomes are engineering/drill evidence, not proof of a production outage response, unless a drill&apos;s evidence reference is itself production-derived.
            </p>
          </article>

          <article className="panel">
            <h2><span>Backup / Restore</span></h2>
            <KeyValueGrid
              columns={2}
              items={[
                { key: "status", label: "Backup / Restore Status", value: <StatusBadge status={data.backup_restore_status} /> },
              ]}
            />
            <p className="empty-notice">
              A passed backup/restore drill demonstrates one exercised recovery path; it is not a general disaster-recovery readiness guarantee.
            </p>
          </article>

          <ProvenancePanel
            source="SRE Overview Authority"
            recordId={data.service_version_id}
            version={data.version}
            asOf={ctx.evidenceTime}
            limitations={["Read-only operational evidence; this workspace cannot acknowledge, resolve, or mutate incidents, drills, or the kill switch."]}
          />
        </>
      ) : (
        <p className="empty-state">{stateText(sre)}</p>
      )}

      <span className="status margin-top-16 align-self-start">
        READ ONLY / TARGET &ne; MEASURED
      </span>
    </div>
  );
}
