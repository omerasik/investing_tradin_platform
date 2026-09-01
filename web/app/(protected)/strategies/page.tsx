import { getWorkspaceContext, getStrategyCard, getStrategyDiscovery, stateText } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";
import { EvidenceMeta } from "../../components/evidence-meta";
import { StrategyCreator } from "../../strategy-creator";

export const dynamic = "force-dynamic";

export default async function StrategiesPage() {
  const ctx = await getWorkspaceContext();
  const [strategy, strategies] = await Promise.all([
    getStrategyCard(ctx),
    getStrategyDiscovery(ctx),
  ]);

  const strategyCard = strategy.state === "AVAILABLE" ? strategy.value : undefined;
  const strategyRows = strategies.state === "AVAILABLE" ? strategies.value.items : [];

  return (
    <>
      <PageHeader
        eyebrow="RESEARCH WORKSPACE"
        title="Strategy Laboratory"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Strategy hypotheses, required dataset versions, and evidence classification. Visual strategy builder and lifecycle promotion arriving in Module 2B.
      </div>

      <div className="grid-2col margin-bottom-20">
        <article className="panel">
          <h2>
            <span>Bound Strategy Definition</span>
            <StatusBadge status={strategyCard ? "AVAILABLE" : strategy.state} />
          </h2>
          {strategyCard ? (
            <>
              <dl>
                <dt>Strategy Family / Version</dt>
                <dd>
                  <strong>{strategyCard.family}</strong> &bull; Version: {strategyCard.strategy_version}
                </dd>
                <dt>Hypothesis</dt>
                <dd>{strategyCard.hypothesis}</dd>
                <dt>Required Features</dt>
                <dd>{strategyCard.feature_versions.join(", ")}</dd>
                <dt>Required Datasets</dt>
                <dd>{strategyCard.required_datasets.join(", ")}</dd>
                <dt>Expected Regimes</dt>
                <dd>{strategyCard.expected_regimes.join(", ")}</dd>
                <dt>Failure Conditions</dt>
                <dd>{strategyCard.failure_conditions.join(", ")}</dd>
                <dt>Classification</dt>
                <dd>
                  {strategyCard.family.toLowerCase().includes("trend")
                    ? "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY"
                    : "RESEARCH EVIDENCE ONLY"}
                </dd>
              </dl>
              <EvidenceMeta
                source="strategy-registry"
                asOf={strategyCard.created_at}
                version={strategyCard.strategy_version}
                limitations={strategyCard.limitations}
              />
            </>
          ) : (
            <p className="empty-state">{stateText(strategy)}</p>
          )}
          <span className="status margin-top-auto">RESEARCH ONLY / READ ONLY</span>
        </article>

        <article className="panel">
          <h2>
            <span>Research Strategy Creator</span>
            <StatusBadge status="RESEARCH ONLY" />
          </h2>
          <p>Generates a transparent baseline strategy contract. This grants no execution authority.</p>
          <StrategyCreator />
        </article>
      </div>

      <article className="panel">
        <h2>
          <span>Discovered Strategies</span>
          <StatusBadge status={strategies.state} />
        </h2>

        {strategyRows.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Strategy ID / Version</th>
                <th>Family &amp; Hypothesis</th>
                <th>Datasets &amp; Features</th>
                <th>Status</th>
                <th>Classification</th>
              </tr>
            </thead>
            <tbody>
              {strategyRows.map((item) => (
                <tr key={item.strategy_version_id}>
                  <td>
                    <code>{item.strategy_id}</code>
                    <br />
                    <strong>{item.version}</strong>
                  </td>
                  <td>
                    <strong>{item.family}</strong>
                    <div>{item.hypothesis}</div>
                  </td>
                  <td>
                    Datasets: {item.dataset_requirements.join(", ")}
                    <br />
                    Features: {item.feature_versions.join(", ")}
                  </td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                  <td>{item.evidence_classification}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty-state">{stateText(strategies)}</p>
        )}
      </article>
    </>
  );
}
