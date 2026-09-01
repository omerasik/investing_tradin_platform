import { getWorkspaceContext, getScorecardEvidence, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";
import { EvidenceStateBadge } from "../../components/evidence-state-badge";
import { EvidenceMeta } from "../../components/evidence-meta";

export const dynamic = "force-dynamic";

export default async function ScorecardsPage() {
  const ctx = await getWorkspaceContext();
  const scorecard = await getScorecardEvidence(ctx);
  const data = scorecard.state === "AVAILABLE" ? scorecard.value : undefined;

  return (
    <>
      <PageHeader
        eyebrow="RESEARCH WORKSPACE"
        title="Strategy Scorecard V2"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Multidimensional scorecard evaluation with explicit metric evidence separation (MEASURED, ASSUMED, UNAVAILABLE). Deep scorecard comparison arriving in Module 2B.
      </div>

      <article className="panel">
        <h2>
          <span>Scorecard Evaluation</span>
          <StatusBadge status={scorecard.state} />
        </h2>
        <p>No opaque aggregate score is used. Metric evidence state is independent from workspace availability.</p>

        {data ? (
          <>
            <p className="warning">{data.evidence_classification}</p>
            <dl>
              <dt>Scorecard ID / Strategy</dt>
              <dd>
                <code>{data.scorecard_id}</code> &bull; <strong>{data.strategy_version}</strong>
              </dd>
              <dt>Research Run / Dataset</dt>
              <dd>
                <code>{data.research_run_id}</code> &bull; {data.dataset_version}
              </dd>
              <dt>Features / Cost Model</dt>
              <dd>
                {data.feature_versions.join(", ")} &bull; {data.cost_model_version}
              </dd>
              <dt>Evaluated / Cutoff (UTC)</dt>
              <dd>
                {utc(data.evaluated_at)} &bull; Cutoff: {utc(data.knowledge_cutoff)}
              </dd>
              <dt>Status / Package</dt>
              <dd>
                <StatusBadge status={data.status} /> &bull; Package: {data.validation_package_id ?? "UNAVAILABLE"}
              </dd>
              <dt>Content Hash</dt>
              <dd>
                <code>{data.content_hash}</code>
              </dd>
            </dl>

            {data.groups.map((group) => (
              <section key={group.name} aria-labelledby={`scorecard-${group.name}`} className="margin-top-20">
                <h3 id={`scorecard-${group.name}`}>{group.name}</h3>
                {group.metrics.length ? (
                  <table>
                    <thead>
                      <tr>
                        <th>Metric</th>
                        <th>Value &amp; Unit</th>
                        <th>Evidence State</th>
                        <th>Reference</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.metrics.map((metric) => (
                        <tr key={metric.metric_id}>
                          <td>
                            <strong>{metric.name}</strong>
                          </td>
                          <td>
                            <strong className="metric-value">
                              {metric.value ?? "UNAVAILABLE"}
                            </strong>{" "}
                            {metric.unit}
                          </td>
                          <td>
                            <EvidenceStateBadge state={metric.evidence_state} />
                          </td>
                          <td>
                            <code>{metric.evidence_reference}</code>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="empty-state">UNAVAILABLE</p>
                )}
              </section>
            ))}

            <section aria-labelledby="scorecard-complexity" className="margin-top-20">
              <h3 id="scorecard-complexity">COMPLEXITY ANALYSIS</h3>
              {data.complexity_components.map((comp) => (
                <div key={comp.component_id} className="margin-bottom-8">
                  <strong>{comp.name}</strong>: {comp.value ?? "UNAVAILABLE"} ({comp.formula_version}) &mdash; {comp.rationale}
                </div>
              ))}
            </section>

            <EvidenceMeta
              source="PostgreSQL Strategy Scorecard V2"
              asOf={data.evaluated_at}
              version={data.schema_version}
              limitations={data.limitations}
            />
          </>
        ) : (
          <p className="empty-state">{stateText(scorecard)}</p>
        )}

        <span className="status margin-top-16 align-self-start">
          RESEARCH EVIDENCE / READ ONLY
        </span>
      </article>
    </>
  );
}
