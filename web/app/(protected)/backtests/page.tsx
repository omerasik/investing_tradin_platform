import {
  getWorkspaceContext,
  getExperimentEvidence,
  getExperimentDiscovery,
  getPromotionEvidence,
  stateText,
  utc,
} from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";
import { EvidenceMeta } from "../../components/evidence-meta";
import { ResearchLauncher } from "../../research-launcher";

export const dynamic = "force-dynamic";

export default async function BacktestsPage() {
  const ctx = await getWorkspaceContext();
  const [experiment, experiments, promotion] = await Promise.all([
    getExperimentEvidence(ctx),
    getExperimentDiscovery(ctx),
    getPromotionEvidence(ctx),
  ]);

  const backtest = experiment.state === "AVAILABLE" ? experiment.value : undefined;
  const experimentRows = experiments.state === "AVAILABLE" ? experiments.value.items : [];

  return (
    <>
      <PageHeader
        eyebrow="RESEARCH WORKSPACE"
        title="Backtest &amp; Validation"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Purged walk-forward validation, pessimistic cost models, and independent bar accounting checks. Visual backtest analytics arriving in Module 2B.
      </div>

      <div className="grid-2col margin-bottom-20">
        <article className="panel">
          <h2>
            <span>Bound Backtest Experiment</span>
            <StatusBadge status={backtest ? "AVAILABLE" : experiment.state} />
          </h2>
          {backtest ? (
            <>
              <dl>
                <dt>Strategy / Dataset</dt>
                <dd>
                  <strong>{backtest.strategy_version}</strong> &bull; Dataset: {backtest.dataset_version}
                </dd>
                <dt>Feature Versions</dt>
                <dd>{backtest.feature_versions.join(", ")}</dd>
                <dt>Cost Model Version</dt>
                <dd>{backtest.cost_model_version}</dd>
                <dt>Total Return</dt>
                <dd>
                  <strong className="metric-value">{String(backtest.report.total_return ?? "UNAVAILABLE")}</strong>
                </dd>
                <dt>Independent Accounting</dt>
                <dd>
                  <StatusBadge
                    status={backtest.report.independent_bar_engine_reconciled === "1" ? "AVAILABLE" : "WARNING"}
                    label={backtest.report.independent_bar_engine_reconciled === "1" ? "RECONCILED" : "UNAVAILABLE OR DIVERGENT"}
                  />
                </dd>
                <dt>Walk-Forward Status</dt>
                <dd>{String(backtest.report.walk_forward_status ?? "UNAVAILABLE")}</dd>
                <dt>Pessimistic Multiplier</dt>
                <dd>{String(backtest.report.pessimistic_cost_multiplier ?? "UNAVAILABLE")}</dd>
                <dt>Promotion Decision</dt>
                <dd>
                  {promotion.state === "AVAILABLE" ? (
                    <span>
                      <StatusBadge status={promotion.value.status} /> &bull; {promotion.value.reasons.join(", ")}
                    </span>
                  ) : (
                    stateText(promotion)
                  )}
                </dd>
              </dl>
              <EvidenceMeta
                source="experiment-store"
                asOf={backtest.created_at}
                version={backtest.cost_model_version}
                limitations={["No aggregate score hides unavailable validation evidence."]}
              />
            </>
          ) : (
            <p className="empty-state">{stateText(experiment)}</p>
          )}
          <span className="status margin-top-auto">HISTORICAL / RESEARCH ONLY</span>
        </article>

        <article className="panel">
          <h2>
            <span>Historical Research Launcher</span>
            <StatusBadge status="RESEARCH ONLY" />
          </h2>
          <ResearchLauncher strategyId={ctx.workspace.strategyId} />
        </article>
      </div>

      <article className="panel">
        <h2>
          <span>Discovered Experiments</span>
          <StatusBadge status={experiments.state} />
        </h2>

        {experimentRows.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Experiment ID</th>
                <th>Strategy Version</th>
                <th>Dataset Version</th>
                <th>Cost Model</th>
                <th>Created / Evaluated</th>
                <th>Status / Classification</th>
              </tr>
            </thead>
            <tbody>
              {experimentRows.map((item) => (
                <tr key={item.experiment_id}>
                  <td>
                    <code>{item.experiment_id}</code>
                  </td>
                  <td>
                    <strong>{item.strategy_version}</strong>
                  </td>
                  <td>{item.dataset_version}</td>
                  <td>{item.cost_model_version}</td>
                  <td>
                    {utc(item.created_at)} &rarr; {utc(item.evaluated_at)}
                  </td>
                  <td>
                    <StatusBadge status={item.status} />
                    <br />
                    <small>{item.evidence_classification}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty-state">{stateText(experiments)}</p>
        )}
      </article>
    </>
  );
}
