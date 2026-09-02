import Link from "next/link";
import {
  getWorkspaceContext,
  getExperimentEvidence,
  getExperimentDiscovery,
  getPromotionEvidence,
  getStrategyDiscovery,
  stateText,
  utc,
} from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { FilterBar } from "../../components/filter-bar";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { ResearchStatusBadge } from "../../components/research-status-badge";
import { StrategyIdentity } from "../../components/strategy-identity";
import { ParameterTable } from "../../components/parameter-table";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";
import { ResearchLauncher } from "../../research-launcher";

export const dynamic = "force-dynamic";

export default async function BacktestsPage({
  searchParams,
}: {
  searchParams?: Promise<{ strategy_id?: string; selected?: string; offset?: string; limit?: string }>;
}) {
  const resolvedParams = await searchParams;
  const strategyId = resolvedParams?.strategy_id?.trim() || "";
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 20) || 20;

  const ctx = await getWorkspaceContext();
  const [discoveryResult, strategyOptionsResult] = await Promise.all([
    getExperimentDiscovery(ctx, { strategy_id: strategyId, limit, offset }),
    getStrategyDiscovery(ctx, { limit: 100 }),
  ]);
  const discoveryPage = discoveryResult.state === "AVAILABLE" ? discoveryResult.value : undefined;
  const items = discoveryPage?.items ?? [];
  const strategyOptions = strategyOptionsResult.state === "AVAILABLE" ? strategyOptionsResult.value.items : [];

  const selectedId = resolvedParams?.selected || (items.length > 0 ? items[0].experiment_id : undefined);
  const selectedItem = items.find((item) => item.experiment_id === selectedId);
  const [experimentResult, promotionResult] = await Promise.all([
    selectedId ? getExperimentEvidence(ctx, { experimentId: selectedId }) : Promise.resolve(undefined),
    getPromotionEvidence(ctx),
  ]);
  const backtest = experimentResult?.state === "AVAILABLE" ? experimentResult.value : undefined;
  const report = backtest?.report ?? {};
  const detailUnavailable = Boolean(selectedItem) && !backtest;

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Backtest & Validation Workspace"
        subtitle="Experiment evidence, validation stages, cost-model assumptions, and promotion outcomes."
        status={discoveryResult.state}
        asOf={ctx.evidenceTime}
      />

      <FilterBar
        groups={[
          {
            id: "filter-strategy-id",
            name: "strategy_id",
            label: "Strategy",
            defaultValue: strategyId || "ALL",
            options: [
              { label: "All Strategies", value: "ALL" },
              ...strategyOptions.map((option) => ({
                label: `${option.family} ${option.version}`,
                value: option.strategy_id,
              })),
            ],
          },
        ]}
        resetHref="/backtests"
        ariaLabel="Backtest Discovery Filters"
      />

      <div className="workspace-split-layout">
        <section aria-label="Discovered Experiments List">
          <article className="panel">
            <h2>
              <span>Discovered Experiments</span>
              <span className="badge tabular-num">{items.length} returned</span>
            </h2>

            {items.length > 0 ? (
              <>
                <DataTable caption="Backtest Experiments Table" ariaLabel="Backtest Experiments">
                  <thead>
                    <tr>
                      <th scope="col">Strategy</th>
                      <th scope="col">Dataset / Cost Model</th>
                      <th scope="col">Created / Evaluated</th>
                      <th scope="col">Status</th>
                      <th scope="col">Evidence</th>
                      <th scope="col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const isSelected = item.experiment_id === selectedId;
                      const inspectParams = new URLSearchParams();
                      if (strategyId) inspectParams.set("strategy_id", strategyId);
                      if (offset > 0) inspectParams.set("offset", String(offset));
                      inspectParams.set("selected", item.experiment_id);
                      return (
                        <tr key={item.experiment_id} className={isSelected ? "row-selected" : ""}>
                          <td>
                            <StrategyIdentity strategyId={item.strategy_id} version={item.strategy_version} />
                          </td>
                          <td>
                            <small>
                              {item.dataset_version} &bull; {item.cost_model_version}
                            </small>
                          </td>
                          <td>
                            <small>{utc(item.created_at)} &rarr; {utc(item.evaluated_at)}</small>
                          </td>
                          <td>
                            <StatusBadge status={item.status} />
                          </td>
                          <td>
                            <ResearchStatusBadge classification={item.evidence_classification} />
                          </td>
                          <td className="cell-action">
                            <Link href={`/backtests?${inspectParams.toString()}`} className="table-link" aria-label={`Inspect experiment ${item.experiment_id}`}>
                              Inspect &rarr;
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </DataTable>

                {discoveryPage && (
                  <Pagination
                    limit={discoveryPage.page.limit}
                    offset={discoveryPage.page.offset}
                    returned={discoveryPage.page.returned}
                    hasMore={discoveryPage.page.has_more}
                    basePath="/backtests"
                    searchParams={{ strategy_id: strategyId || "", selected: selectedId ?? "" }}
                  />
                )}
              </>
            ) : (
              <p className="empty-state">{stateText(discoveryResult)}</p>
            )}
          </article>
        </section>

        <aside aria-label="Experiment Inspector">
          {selectedItem ? (
            <div className="inspector-card">
              <header className="inspector-header">
                <div className="inspector-title-group">
                  <div className="inspector-title-row">
                    <h2>{backtest?.strategy_version ?? selectedItem.strategy_version}</h2>
                    <ResearchStatusBadge classification={backtest?.evidence_classification ?? selectedItem.evidence_classification} />
                  </div>
                  <code className="inspector-id-code">{selectedId}</code>
                </div>
              </header>

              {detailUnavailable && (
                <p className="empty-notice">
                  Detail evidence unavailable ({stateText(experimentResult!)}) &mdash; showing discovery-level evidence only.
                </p>
              )}

              <div className="inspector-section inspector-section-first">
                <span className="inspector-section-title">Parameters</span>
                <ParameterTable schema={backtest?.parameters ?? {}} />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Performance (Persisted Report Values Only)</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "total_return", label: "Total Return", value: String(report.total_return ?? "UNAVAILABLE") },
                    { key: "annualized_return", label: "Annualized Return", value: String(report.annualized_return ?? "UNAVAILABLE") },
                    { key: "sharpe", label: "Sharpe", value: String(report.sharpe ?? "UNAVAILABLE") },
                    { key: "max_drawdown", label: "Max Drawdown", value: String(report.max_drawdown ?? "UNAVAILABLE") },
                    { key: "hit_rate", label: "Hit Rate", value: String(report.hit_rate ?? "UNAVAILABLE") },
                    { key: "turnover", label: "Turnover", value: String(report.turnover ?? "UNAVAILABLE") },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Validation</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    {
                      key: "walk_forward",
                      label: "Walk-Forward Status",
                      value: String(report.walk_forward_status ?? "UNAVAILABLE"),
                    },
                    {
                      key: "independent_accounting",
                      label: "Independent Accounting",
                      value: (
                        <StatusBadge
                          status={report.independent_bar_engine_reconciled === "1" ? "AVAILABLE" : "UNAVAILABLE"}
                          label={report.independent_bar_engine_reconciled === "1" ? "RECONCILED" : "UNAVAILABLE OR DIVERGENT"}
                        />
                      ),
                    },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Cost Model</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "cost_model_version", label: "Cost Model Version", value: backtest?.cost_model_version ?? selectedItem.cost_model_version },
                    {
                      key: "pessimistic_multiplier",
                      label: "Pessimistic Multiplier (ASSUMED)",
                      value: String(report.pessimistic_cost_multiplier ?? "UNAVAILABLE"),
                    },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Promotion Decision</span>
                {promotionResult.state === "AVAILABLE" ? (
                  <p>
                    <StatusBadge status={promotionResult.value.status} /> &bull; {promotionResult.value.reasons.join(", ") || "No reasons recorded."}
                  </p>
                ) : (
                  <p className="empty-notice">{stateText(promotionResult)}</p>
                )}
              </div>

              <ProvenancePanel
                source="ResearchExperiment store"
                recordId={selectedId}
                version={backtest?.cost_model_version ?? selectedItem.cost_model_version}
                datasetVersion={backtest?.dataset_version ?? selectedItem.dataset_version}
                asOf={backtest?.created_at ?? selectedItem.created_at}
                limitations={["No aggregate score hides unavailable validation evidence."]}
              />
            </div>
          ) : (
            <div className="inspector-card">
              <p className="empty-state">Select an experiment from the table to inspect details.</p>
            </div>
          )}
        </aside>
      </div>

      <article className="panel panel-isolated">
        <h2>
          <span>Run Research Experiment</span>
          <StatusBadge status="RESEARCH ONLY" />
        </h2>
        <p className="warning">RESEARCH ONLY &middot; NO LIVE ACTION</p>
        <ResearchLauncher strategyId={ctx.workspace.strategyId} />
      </article>
    </div>
  );
}
