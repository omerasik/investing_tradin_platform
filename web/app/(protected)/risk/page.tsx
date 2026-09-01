import { getWorkspaceContext, getRiskEvidence, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";

export const dynamic = "force-dynamic";

export default async function RiskPage() {
  const ctx = await getWorkspaceContext();
  const risk = await getRiskEvidence(ctx);
  const riskData = risk.state === "AVAILABLE" ? risk.value : undefined;

  return (
    <>
      <PageHeader
        eyebrow="PORTFOLIO & RISK WORKSPACE"
        title="Risk Workspace"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Immutable policy, decision, and reservation evidence. This workspace cannot evaluate, override, release, reserve, or cancel limits. Interactive policy exploration arriving in Module 2B.
      </div>

      <article className="panel">
        <h2>
          <span>Bounded Risk Decisions</span>
          <StatusBadge status={risk.state} />
        </h2>
        <p>Immutable policy evaluation and reservation records.</p>

        {riskData?.items.length ? (
          <table>
            <caption>Latest bounded risk decisions</caption>
            <thead>
              <tr>
                <th>Decision ID / Hash</th>
                <th>Policy Name &amp; Version</th>
                <th>Outcome &amp; Reasons</th>
                <th>Reservation Evidence</th>
                <th>Decided At (UTC)</th>
                <th>Boundary</th>
              </tr>
            </thead>
            <tbody>
              {riskData.items.map((item) => (
                <tr key={item.risk_decision_id}>
                  <td>
                    <code>{item.risk_decision_id}</code>
                    <br />
                    <code>{item.policy_content_hash}</code>
                  </td>
                  <td>
                    <strong>{item.policy_name}</strong>
                    <br />
                    <span>Version: {item.policy_version}</span>
                  </td>
                  <td>
                    <StatusBadge status={item.approved ? "APPROVED" : "REJECTED"} />
                    <br />
                    <small>{item.reasons.join("; ") || "No violation recorded"}</small>
                  </td>
                  <td>
                    Reservation: <code>{item.reservation_id ?? "UNAVAILABLE"}</code>
                    <br />
                    Account: {item.account_id ?? "UNAVAILABLE"} &bull; Date: {item.business_date ?? "UNAVAILABLE"}
                    <br />
                    Reserved Notional: <strong>{item.reserved_notional ?? "UNAVAILABLE"}</strong>
                  </td>
                  <td>{utc(item.decided_at)}</td>
                  <td>
                    RESEARCH / PAPER ONLY
                    <br />
                    NO AUTOMATIC AUTHORITY
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty-state">
            {riskData
              ? `${riskData.state}: no immutable risk decision matched this bounded scope.`
              : stateText(risk)}
          </p>
        )}

        <span className="status margin-top-16 align-self-start">
          READ ONLY / NO RISK OVERRIDE / NO EXECUTION ACTION
        </span>
      </article>
    </>
  );
}
