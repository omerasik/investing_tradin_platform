import {
  getWorkspaceContext,
  getPaperOrderEvidence,
  getPaperOrderDiscovery,
  getPaperReconciliationEvidence,
  stateText,
  utc,
} from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";
import { EvidenceMeta } from "../../components/evidence-meta";

export const dynamic = "force-dynamic";

export default async function PaperPage() {
  const ctx = await getWorkspaceContext();
  const [order, orders, reconciliation] = await Promise.all([
    getPaperOrderEvidence(ctx),
    getPaperOrderDiscovery(ctx),
    getPaperReconciliationEvidence(ctx),
  ]);

  const paperOrder = order.state === "AVAILABLE" ? order.value : undefined;
  const paperOrderRows = orders.state === "AVAILABLE" ? orders.value.items : [];
  const account = reconciliation.state === "AVAILABLE" ? reconciliation.value : undefined;

  return (
    <>
      <PageHeader
        eyebrow="EXECUTION WORKSPACE"
        title="Paper OMS"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Paper lifecycle and account reconciliation evidence. This console cannot submit live or broker orders. Interactive paper order blotter arriving in Module 2B.
      </div>

      <div className="grid-2col margin-bottom-20">
        <article className="panel">
          <h2>
            <span>Bound Paper Order Intent</span>
            <StatusBadge status={paperOrder ? "AVAILABLE" : order.state} />
          </h2>
          {paperOrder ? (
            <>
              <dl>
                <dt>Intent ID / Instrument</dt>
                <dd>
                  <code>{paperOrder.intent_id}</code> &bull; <strong>{paperOrder.instrument_id}</strong>
                </dd>
                <dt>Status / Quantity / Filled</dt>
                <dd>
                  <StatusBadge status={paperOrder.status} /> &bull; Qty: {paperOrder.quantity} (Filled: {paperOrder.filled_quantity})
                </dd>
                <dt>Execution Boundary</dt>
                <dd>
                  <StatusBadge status="PAPER ONLY" />
                </dd>
                <dt>Fills</dt>
                <dd>
                  {paperOrder.fills.length
                    ? paperOrder.fills.map((f) => `${f.external_fill_id}: ${f.quantity} @ ${f.price}`).join("; ")
                    : "No fills recorded."}
                </dd>
                <dt>Lifecycle Timeline</dt>
                <dd>
                  {paperOrder.events.map((e) => `${e.event_type} @ ${utc(e.occurred_at)}`).join(" \u2192 ") || "No lifecycle events."}
                </dd>
              </dl>
              <EvidenceMeta
                source="paper-oms"
                asOf={paperOrder.created_at}
                version="paper-oms-v1"
                limitations={["Paper-only evidence; no broker execution capability."]}
              />
            </>
          ) : (
            <p className="empty-state">{stateText(order)}</p>
          )}
          <span className="status margin-top-auto">PAPER ONLY / READ ONLY</span>
        </article>

        <article className="panel">
          <h2>
            <span>Paper Account Reconciliation</span>
            <StatusBadge status={account ? "AVAILABLE" : reconciliation.state} />
          </h2>
          {account ? (
            <>
              <dl>
                <dt>Account ID / Status</dt>
                <dd>
                  <code>{account.reconciled_account?.evidence_id ?? "RECONCILED ACCOUNT"}</code> &bull;{" "}
                  <StatusBadge
                    status={account.complete ? "AVAILABLE" : "WARNING"}
                    label={account.complete ? "RECONCILED" : "DISCREPANCY"}
                  />
                </dd>
                <dt>Source / Occurred</dt>
                <dd>
                  {account.source} &bull; {utc(account.occurred_at)}
                </dd>
                <dt>Discrepancies</dt>
                <dd>{account.discrepancies.join(", ") || "None. Perfect balance match."}</dd>
              </dl>
              <EvidenceMeta
                source={account.source}
                asOf={account.occurred_at}
                version="reconciliation-v1"
              />
            </>
          ) : (
            <p className="empty-state">{stateText(reconciliation)}</p>
          )}
          <span className="status margin-top-auto">PAPER RECONCILIATION</span>
        </article>
      </div>

      <article className="panel">
        <h2>
          <span>Discovered Paper Orders</span>
          <StatusBadge status={orders.state} />
        </h2>

        {paperOrderRows.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Intent ID / Instrument</th>
                <th>Side</th>
                <th>Lifecycle Status</th>
                <th>Fill State</th>
                <th>Reconciliation</th>
                <th>Created (UTC)</th>
              </tr>
            </thead>
            <tbody>
              {paperOrderRows.map((item) => (
                <tr key={item.intent_id}>
                  <td>
                    <code>{item.intent_id}</code>
                    <br />
                    <strong>{item.canonical_symbol ?? item.instrument_id}</strong>
                  </td>
                  <td>{item.side}</td>
                  <td>
                    <StatusBadge status={item.lifecycle_status} />
                  </td>
                  <td>{item.fill_state}</td>
                  <td>{item.reconciliation_state}</td>
                  <td>{utc(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty-state">{stateText(orders)}</p>
        )}
      </article>
    </>
  );
}
