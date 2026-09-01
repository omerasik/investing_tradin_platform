import { getWorkspaceContext, getPortfolioConstructionEvidence, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";
import { EvidenceMeta } from "../../components/evidence-meta";

export const dynamic = "force-dynamic";

export default async function PortfolioPage() {
  const ctx = await getWorkspaceContext();
  const construction = await getPortfolioConstructionEvidence(ctx);
  const data = construction.state === "AVAILABLE" ? construction.value : undefined;

  return (
    <>
      <PageHeader
        eyebrow="PORTFOLIO & RISK WORKSPACE"
        title="Portfolio Construction V2"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Review-only requested and constrained allocations. This workspace has no apply or execution action. Deep allocation optimizer views arriving in Module 2B.
      </div>

      <article className="panel">
        <h2>
          <span>Construction Run Details</span>
          <StatusBadge status={construction.state} />
        </h2>
        <p>Review-only requested and constrained allocations. This workspace has no apply or execution action.</p>

        {data ? (
          <>
            <dl>
              <dt>Run ID / Policy</dt>
              <dd>
                <code>{data.portfolio_construction_run_id}</code> &bull; Policy: {data.policy_version}
              </dd>
              <dt>Status / Constructed At</dt>
              <dd>
                <StatusBadge status={data.status} /> &bull; {utc(data.constructed_at)}
              </dd>
              <dt>Equity / Target Volatility</dt>
              <dd>
                Equity: <strong className="metric-value">{data.equity}</strong> &bull; Target Vol: {data.target_volatility ?? "UNAVAILABLE"}
              </dd>
              <dt>Weights (Cash / Gross / Net)</dt>
              <dd>
                Cash: {data.cash_weight} &bull; Gross: {data.gross_weight} &bull; Net: {data.net_weight}
              </dd>
              <dt>Volatility / Stressed</dt>
              <dd>
                Portfolio Vol: {data.portfolio_volatility} &bull; Stressed Vol: {data.stressed_volatility}
              </dd>
              <dt>Covariance Evidence</dt>
              <dd>
                {data.covariance.classification} &bull; Dataset: {data.covariance.dataset_version} ({data.covariance.observations} obs) &bull; Uncertainty: {data.covariance.uncertainty}
              </dd>
              <dt>Risk Gate</dt>
              <dd>
                <StatusBadge status={data.risk_gate_approved ? "APPROVED" : "BLOCKED"} label={data.risk_gate_approved ? "REVIEW ELIGIBLE" : "BLOCKED"} />: {data.risk_gate_reasons.join(", ")}
              </dd>
            </dl>

            <h3>Sleeve Allocations &amp; Reductions</h3>
            <table>
              <thead>
                <tr>
                  <th>Sleeve Key</th>
                  <th>Requested / Review Allocation</th>
                  <th>Risk Budget / Marginal / Component</th>
                  <th>Reductions &amp; Reasons</th>
                </tr>
              </thead>
              <tbody>
                {data.sleeves.map((item) => (
                  <tr key={item.sleeve_input_id}>
                    <td>
                      <strong>{item.strategy_key}</strong>
                    </td>
                    <td>
                      {item.requested_allocation} &rarr;{" "}
                      <strong className="metric-value">{item.review_allocation ?? "REJECTED"}</strong>
                    </td>
                    <td>
                      Budget: {item.risk_budget} &bull; Marginal: {item.marginal_risk ?? "UNAVAILABLE"} &bull; Component: {item.component_risk ?? "UNAVAILABLE"}
                    </td>
                    <td>
                      Capacity: {item.capacity_weight} &bull; Liquidity: {item.liquidity_score} &bull; Drawdown: {item.drawdown} &bull; Regime: {item.regime_current_multiplier}&rarr;{item.regime_proposed_multiplier}
                      <br />
                      <small>{item.adjustment_reasons.join(", ") || "No reductions applied"}</small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3>Constraints</h3>
            {data.constraints.map((item) => (
              <p key={item.constraint_id}>
                <strong>{item.name}</strong>: <StatusBadge status={item.state} /> &bull; Observed: {item.observed ?? "UNAVAILABLE"} &bull; Limit: {item.limit ?? "UNAVAILABLE"} &bull; {item.reasons.join(", ")}
              </p>
            ))}

            <EvidenceMeta
              source="PostgreSQL Portfolio Construction V2"
              asOf={data.constructed_at}
              version={data.policy_version}
              limitations={data.limitations}
            />
          </>
        ) : (
          <p className="empty-state">{stateText(construction)}</p>
        )}

        <span className="status margin-top-16 align-self-start">
          REVIEW ONLY / NO EXECUTION ACTION
        </span>
      </article>
    </>
  );
}
