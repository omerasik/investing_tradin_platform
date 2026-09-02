import Link from "next/link";
import { getWorkspaceContext, getStrategyCard, getStrategyDiscovery, stateText, utc } from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { FilterBar } from "../../components/filter-bar";
import { SearchField } from "../../components/search-field";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { ResearchStatusBadge } from "../../components/research-status-badge";
import { StrategyIdentity } from "../../components/strategy-identity";
import { ParameterTable } from "../../components/parameter-table";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";
import { StrategyCreator } from "../../strategy-creator";

export const dynamic = "force-dynamic";

export default async function StrategiesPage({
  searchParams,
}: {
  searchParams?: Promise<{ family?: string; selected?: string; offset?: string; limit?: string }>;
}) {
  const resolvedParams = await searchParams;
  const family = resolvedParams?.family?.trim() || undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 20) || 20;

  const ctx = await getWorkspaceContext();
  const discoveryResult = await getStrategyDiscovery(ctx, { family, limit, offset });
  const discoveryPage = discoveryResult.state === "AVAILABLE" ? discoveryResult.value : undefined;
  const items = discoveryPage?.items ?? [];

  const selectedId = resolvedParams?.selected || (items.length > 0 ? items[0].strategy_id : undefined);
  const selectedItem = items.find((item) => item.strategy_id === selectedId);
  const strategyResult = selectedId ? await getStrategyCard(ctx, { strategyId: selectedId }) : undefined;
  const strategyCard = strategyResult?.state === "AVAILABLE" ? strategyResult.value : undefined;
  const detailUnavailable = Boolean(selectedItem) && !strategyCard;

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Strategy Laboratory"
        subtitle="Research strategy registry: hypotheses, dataset/feature requirements, cost models, and evidence classification."
        status={discoveryResult.state}
        asOf={ctx.evidenceTime}
      />

      <FilterBar resetHref="/strategies" ariaLabel="Strategy Discovery Filters">
        <SearchField
          id="strategy-family-search"
          name="family"
          defaultValue={family ?? ""}
          placeholder="Exact family, e.g. TREND, MEAN_REVERSION..."
          label="Filter by Family"
        />
      </FilterBar>

      <div className="workspace-split-layout">
        <section aria-label="Discovered Strategies List">
          <article className="panel">
            <h2>
              <span>Discovered Strategies</span>
              <span className="badge tabular-num">{items.length} returned</span>
            </h2>

            {items.length > 0 ? (
              <>
                <DataTable caption="Strategy Registry Table" ariaLabel="Strategy Registry">
                  <thead>
                    <tr>
                      <th scope="col">Strategy</th>
                      <th scope="col">Hypothesis</th>
                      <th scope="col">Datasets / Features</th>
                      <th scope="col">Status</th>
                      <th scope="col">Evidence</th>
                      <th scope="col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const isSelected = item.strategy_id === selectedId;
                      const inspectParams = new URLSearchParams();
                      if (family) inspectParams.set("family", family);
                      if (offset > 0) inspectParams.set("offset", String(offset));
                      inspectParams.set("selected", item.strategy_id);
                      return (
                        <tr key={item.strategy_version_id} className={isSelected ? "row-selected" : ""}>
                          <td>
                            <StrategyIdentity strategyId={item.strategy_id} version={item.version} family={item.family} />
                          </td>
                          <td>{item.hypothesis}</td>
                          <td>
                            <small>
                              Datasets: {item.dataset_requirements.join(", ") || "UNAVAILABLE"}
                              <br />
                              Features: {item.feature_versions.join(", ") || "UNAVAILABLE"}
                            </small>
                          </td>
                          <td>
                            <StatusBadge status={item.status} />
                          </td>
                          <td>
                            <ResearchStatusBadge classification={item.evidence_classification} />
                          </td>
                          <td className="cell-action">
                            <Link href={`/strategies?${inspectParams.toString()}`} className="table-link" aria-label={`Inspect ${item.family} ${item.version}`}>
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
                    basePath="/strategies"
                    searchParams={{ family: family ?? "", selected: selectedId ?? "" }}
                  />
                )}
              </>
            ) : (
              <p className="empty-state">{stateText(discoveryResult)}</p>
            )}
          </article>
        </section>

        <aside aria-label="Strategy Inspector">
          {selectedItem ? (
            <div className="inspector-card">
              <header className="inspector-header">
                <div className="inspector-title-group">
                  <div className="inspector-title-row">
                    <h2>{strategyCard?.family ?? selectedItem.family}</h2>
                    <ResearchStatusBadge classification={strategyCard?.evidence_classification ?? selectedItem.evidence_classification} />
                  </div>
                  <code className="inspector-id-code">{selectedItem.strategy_id}</code>
                </div>
              </header>

              {detailUnavailable && (
                <p className="empty-notice">
                  Detail evidence unavailable ({stateText(strategyResult!)}) &mdash; showing discovery-level evidence only.
                </p>
              )}

              <div className="inspector-section inspector-section-first">
                <span className="inspector-section-title">Economic Hypothesis</span>
                <p>{strategyCard?.hypothesis ?? selectedItem.hypothesis}</p>
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Implementation Rules</span>
                <KeyValueGrid
                  columns={1}
                  items={[
                    { key: "entry_logic", label: "Entry", value: strategyCard?.entry_logic ?? "UNAVAILABLE" },
                    { key: "exit_logic", label: "Exit", value: strategyCard?.exit_logic ?? "UNAVAILABLE" },
                    { key: "sizing_policy", label: "Sizing", value: strategyCard?.sizing_policy ?? "UNAVAILABLE" },
                    { key: "risk_policy", label: "Risk Policy", value: strategyCard?.risk_policy ?? "UNAVAILABLE" },
                    { key: "capacity_model", label: "Capacity Model", value: strategyCard?.capacity_model ?? "UNAVAILABLE" },
                    { key: "universe_rules", label: "Universe Rules", value: strategyCard?.universe_rules ?? "UNAVAILABLE" },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Inputs</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "datasets", label: "Required Datasets", value: (strategyCard?.required_datasets ?? selectedItem.dataset_requirements).join(", ") || "UNAVAILABLE" },
                    { key: "features", label: "Feature Versions", value: (strategyCard?.feature_versions ?? selectedItem.feature_versions).join(", ") || "UNAVAILABLE" },
                    { key: "cost_model", label: "Cost Model Version", value: strategyCard?.cost_model_version ?? selectedItem.cost_model_version },
                    { key: "expected_regimes", label: "Expected Regimes", value: strategyCard?.expected_regimes.join(", ") || "UNAVAILABLE" },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Parameter Schema</span>
                <ParameterTable schema={strategyCard?.parameter_schema ?? {}} />
              </div>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">WHEN SHOULD THIS STRATEGY NOT WORK?</span>
                {strategyCard && strategyCard.failure_conditions.length > 0 ? (
                  <ul>
                    {strategyCard.failure_conditions.map((condition, index) => (
                      <li key={index}>{condition}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="empty-notice">UNAVAILABLE</p>
                )}
              </div>

              <ProvenancePanel
                source="StrategyRunCard research registry"
                recordId={selectedItem.strategy_id}
                version={strategyCard?.strategy_version ?? selectedItem.version}
                asOf={strategyCard?.created_at ?? selectedItem.created_at}
                limitations={strategyCard?.limitations ?? []}
              />
            </div>
          ) : (
            <div className="inspector-card">
              <p className="empty-state">Select a strategy from the table to inspect details.</p>
            </div>
          )}
        </aside>
      </div>

      <article className="panel panel-isolated">
        <h2>
          <span>Create Research Strategy</span>
          <StatusBadge status="RESEARCH ONLY" />
        </h2>
        <p className="warning">RESEARCH ONLY &middot; NO EXECUTION AUTHORITY &middot; NO AUTOMATIC PROMOTION</p>
        <p>Generates a transparent baseline strategy contract. This grants no execution authority.</p>
        <StrategyCreator />
      </article>

      <p className="page-header-meta">Evidence time: <time dateTime={ctx.evidenceTime}>{utc(ctx.evidenceTime)}</time> UTC</p>
    </div>
  );
}
