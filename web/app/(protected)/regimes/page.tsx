import { getWorkspaceContext, getRegimeEvidence, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";
import { EvidenceMeta } from "../../components/evidence-meta";

export const dynamic = "force-dynamic";

export default async function RegimesPage() {
  const ctx = await getWorkspaceContext();
  const regime = await getRegimeEvidence(ctx);
  const data = regime.state === "AVAILABLE" ? regime.value : undefined;

  return (
    <>
      <PageHeader
        eyebrow="PORTFOLIO & RISK WORKSPACE"
        title="Regime Engine V2"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Regime assessments may reduce or block risk; regimes cannot increase global risk limits. Visual regime transition diagrams arriving in Module 2B.
      </div>

      <article className="panel">
        <h2>
          <span>Regime Assessment</span>
          <StatusBadge status={regime.state} />
        </h2>
        <p className="warning">
          REGIME MAY REDUCE OR BLOCK RISK. REGIME CANNOT INCREASE GLOBAL RISK LIMITS.
        </p>

        {data ? (
          <>
            <dl>
              <dt>Assessment / Model / Rule</dt>
              <dd>
                <code>{data.regime_assessment_id}</code> &bull; Model: {data.model_version} &bull; Rule: {data.rule_version}
              </dd>
              <dt>Dataset / Instrument</dt>
              <dd>
                {data.dataset_version} &bull; <strong>{data.instrument}</strong>
              </dd>
              <dt>As of / Knowledge (UTC)</dt>
              <dd>
                {utc(data.as_of_timestamp)} &bull; Knowledge: {utc(data.knowledge_timestamp)}
              </dd>
              <dt>Status / Evidence Hash</dt>
              <dd>
                <StatusBadge status={data.status} /> &bull; <code>{data.evidence_hash}</code>
              </dd>
            </dl>

            <h3>Observed Dimensions</h3>
            <table>
              <thead>
                <tr>
                  <th>Dimension / Method</th>
                  <th>State Probabilities</th>
                  <th>Uncertainty</th>
                  <th>Evidence State / Hash</th>
                </tr>
              </thead>
              <tbody>
                {data.dimensions.map((item) => (
                  <tr key={item.observation_id}>
                    <td>
                      <strong>{item.dimension}</strong>
                      <br />
                      <small>{item.method}</small>
                    </td>
                    <td>
                      {item.probabilities.map((prob) => (
                        <div key={prob.state}>
                          {prob.state}: <strong className="metric-value">{prob.probability}</strong>
                        </div>
                      ))}
                    </td>
                    <td>{item.uncertainty ?? "UNAVAILABLE"}</td>
                    <td>
                      <StatusBadge status={item.evidence_state} />
                      <br />
                      <code>{item.content_hash}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3>Eligibility &amp; Risk Multipliers</h3>
            {data.risk_effects.length ? (
              data.risk_effects.map((item) => (
                <p key={item.candidate_id}>
                  <strong>{item.action}</strong>: {item.current_risk_multiplier} &rarr;{" "}
                  <strong>{item.proposed_risk_multiplier}</strong> (Max: {item.preapproved_maximum}) &bull;{" "}
                  <StatusBadge status={item.status} /> {item.reasons.join(", ")}
                </p>
              ))
            ) : (
              <p className="empty-state">UNAVAILABLE: no reduction candidate is bound to this run.</p>
            )}

            <EvidenceMeta
              source="PostgreSQL Regime Engine V2"
              asOf={data.as_of_timestamp}
              version={data.model_version}
              limitations={data.limitations}
            />
          </>
        ) : (
          <p className="empty-state">{stateText(regime)}</p>
        )}

        <span className="status margin-top-16 align-self-start">
          RESEARCH ONLY / NO RISK-INCREASE CONTROL
        </span>
      </article>
    </>
  );
}
