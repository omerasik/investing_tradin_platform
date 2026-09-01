import { getWorkspaceContext, getSreEvidence, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";

export const dynamic = "force-dynamic";

export default async function OperationsPage() {
  const ctx = await getWorkspaceContext();
  const sre = await getSreEvidence(ctx);
  const data = sre.state === "AVAILABLE" ? sre.value : undefined;

  return (
    <>
      <PageHeader
        eyebrow="SYSTEM & SRE WORKSPACE"
        title="Operations &amp; SRE"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Service Level Objectives, Target vs. Measured metrics, failure drills, and active incident response. Detailed SLO burn rates and drill runner arriving in Module 2B.
      </div>

      <article className="panel">
        <h2>
          <span>Subsystem Health &amp; Runtime Boundaries</span>
          <StatusBadge status={sre.state} />
        </h2>
        <p className="warning">
          TARGET and MEASURED are distinct. Candidate targets are never presented as achieved operational evidence.
        </p>

        {data ? (
          <>
            <dl>
              <dt>Subsystem / Version / Environment</dt>
              <dd>
                {data.subsystem} &bull; v{data.version} &bull; <strong>{data.environment}</strong>
              </dd>
              <dt>PostgreSQL / Provider State</dt>
              <dd>
                PostgreSQL: <StatusBadge status={data.postgres_state} /> &bull; Provider: <StatusBadge status={data.provider_state} />
              </dd>
              <dt>Freshness (Ingestion / Dataset / Feature)</dt>
              <dd>
                Ingestion: {data.ingestion_checkpoint_freshness} &bull; Dataset: {data.dataset_freshness} &bull; Feature: {data.feature_freshness}
              </dd>
              <dt>Research / Signal / Risk Health</dt>
              <dd>
                Research: {data.research_job_health} &bull; Signals: {data.signal_freshness} &bull; Risk: {data.risk_status}
              </dd>
              <dt>Reconciliation / Backup / Kill Switch</dt>
              <dd>
                Reconciliation: {data.reconciliation_status} &bull; Backup: {data.backup_restore_status} &bull; Kill Switch: <StatusBadge status={data.kill_switch_state} />
              </dd>
            </dl>

            <h3>SLO Evidence</h3>
            <table>
              <thead>
                <tr>
                  <th>Service Level Objective</th>
                  <th>TARGET</th>
                  <th>MEASURED</th>
                  <th>Evidence State</th>
                </tr>
              </thead>
              <tbody>
                {data.slos.map((item) => (
                  <tr key={item.slo_policy_version_id}>
                    <td>
                      <strong>{item.name}</strong>
                      <br />
                      <small>{item.indicator}</small>
                    </td>
                    <td>
                      <strong>{item.target}</strong>
                    </td>
                    <td>
                      <strong className="metric-value">{item.measured_value ?? "UNAVAILABLE"}</strong>
                    </td>
                    <td>
                      <StatusBadge status={item.measured_state} /> &bull; {item.claim_status ?? "NO MEASUREMENT"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3>Active Incidents</h3>
            {data.incidents.length ? (
              data.incidents.map((item) => (
                <p key={item.incident_id}>
                  <code>{item.incident_id}</code>: <StatusBadge status={item.severity} /> {item.subsystem}; opened{" "}
                  {utc(item.opened_at)}; acknowledged {utc(item.acknowledged_at)}; resolved{" "}
                  {utc(item.resolved_at)}; <StatusBadge status={item.status} />; {item.reason}; {item.evidence_reference}
                </p>
              ))
            ) : (
              <p className="empty-state">UNAVAILABLE: no persisted incident evidence.</p>
            )}

            <h3>Failure &amp; Recovery Drills</h3>
            {data.failure_drills.map((item) => (
              <p key={item.drill_run_id}>
                <strong>{item.scenario}</strong>: <StatusBadge status={item.passed ? "PASSED" : "FAILED"} /> &bull; Expected: {item.expected_protection} &bull; Measured: {item.observed_protection} &bull; {item.evidence_reference}
              </p>
            ))}
          </>
        ) : (
          <p className="empty-state">{stateText(sre)}</p>
        )}

        <span className="status margin-top-16 align-self-start">
          READ ONLY / TARGET &ne; MEASURED
        </span>
      </article>
    </>
  );
}
