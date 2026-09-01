import { getWorkspaceContext, getAlertsEvidence, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";

export const dynamic = "force-dynamic";

export default async function AuditPage() {
  const ctx = await getWorkspaceContext();
  const alerts = await getAlertsEvidence(ctx);
  const alertRows = alerts.state === "AVAILABLE" ? alerts.value : undefined;

  return (
    <>
      <PageHeader
        eyebrow="SYSTEM WORKSPACE"
        title="Audit Log &amp; Alerts"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Immutable operational and alert audit trail. NO MUTATION ROUTE EXPOSED BY THIS DASHBOARD. Comprehensive audit search arriving in Module 2B.
      </div>

      <article className="panel">
        <h2>
          <span>Immutable Operational Alerts</span>
          <StatusBadge status={alerts.state} />
        </h2>
        <p>
          Audit evidence is immutable and read-only: actor, action, domain object, version, timestamp,
          decision, reasons, and evidence IDs belong to protected backend records.
        </p>

        {alertRows?.length ? (
          <table>
            <thead>
              <tr>
                <th>Alert ID</th>
                <th>Code / Severity</th>
                <th>Resource</th>
                <th>Status</th>
                <th>Created At (UTC)</th>
              </tr>
            </thead>
            <tbody>
              {alertRows.map((item) => (
                <tr key={item.alert_id}>
                  <td>
                    <code>{item.alert_id}</code>
                  </td>
                  <td>
                    <strong>{item.code}</strong> &bull; <StatusBadge status={item.severity} />
                  </td>
                  <td>{item.resource}</td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                  <td>{utc(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty-state">
            {alertRows ? "No active alerts recorded." : stateText(alerts)}
          </p>
        )}

        <dl className="margin-top-20">
          <dt>Audit Authority</dt>
          <dd>IMMUTABLE POSTGRESQL LEDGER</dd>
          <dt>Mutation Boundary</dt>
          <dd>NO MUTATION ROUTE EXPOSED BY THIS DASHBOARD</dd>
        </dl>

        <span className="status margin-top-16 align-self-start">READ ONLY</span>
      </article>
    </>
  );
}
