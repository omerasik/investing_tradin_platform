import Link from "next/link";
import { getWorkspaceContext, getScorecardEvidence, getScorecardDiscovery, stateText, utc } from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { FilterBar } from "../../components/filter-bar";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { ResearchStatusBadge } from "../../components/research-status-badge";
import { EvidenceStateBadge } from "../../components/evidence-state-badge";
import { StrategyIdentity } from "../../components/strategy-identity";
import { EvidenceMeta } from "../../components/evidence-meta";

export const dynamic = "force-dynamic";

export default async function ScorecardsPage({
  searchParams,
}: {
  searchParams?: Promise<{ strategy_id?: string; status?: string; selected?: string; offset?: string; limit?: string }>;
}) {
  const resolvedParams = await searchParams;
  const strategyId = resolvedParams?.strategy_id?.trim() || undefined;
  const status = resolvedParams?.status?.trim() || undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 20) || 20;

  const ctx = await getWorkspaceContext();
  const discoveryResult = await getScorecardDiscovery(ctx, { strategy_id: strategyId, status, limit, offset });
  const discoveryPage = discoveryResult.state === "AVAILABLE" ? discoveryResult.value : undefined;
  const items = discoveryPage?.items ?? [];

  const selectedId = resolvedParams?.selected || (items.length > 0 ? items[0].scorecard_id : undefined);
  const scorecardResult = selectedId ? await getScorecardEvidence(ctx, { scorecardId: selectedId }) : undefined;
  const data = scorecardResult?.state === "AVAILABLE" ? scorecardResult.value : undefined;

  const measuredCount = data ? data.groups.flatMap((g) => g.metrics).filter((m) => m.evidence_state === "MEASURED").length : 0;
  const assumedCount = data ? data.groups.flatMap((g) => g.metrics).filter((m) => m.evidence_state === "ASSUMED").length : 0;
  const unavailableCount = data ? data.groups.flatMap((g) => g.metrics).filter((m) => m.evidence_state === "UNAVAILABLE").length : 0;

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Strategy Scorecard V2"
        subtitle="Decision-quality evaluation workspace. No opaque aggregate score; metric evidence state stays independent from workspace availability."
        status={discoveryResult.state}
        asOf={ctx.evidenceTime}
      />

      <FilterBar
        groups={[
          {
            id: "filter-scorecard-status",
            name: "status",
            label: "Status",
            defaultValue: status ?? "ALL",
            options: [
              { label: "All Statuses", value: "ALL" },
              { label: "Blocked", value: "BLOCKED" },
              { label: "Review Required", value: "REVIEW_REQUIRED" },
            ],
          },
        ]}
        resetHref="/scorecards"
        ariaLabel="Scorecard Discovery Filters"
      />

      <div className="workspace-split-layout">
        <section aria-label="Discovered Scorecards List">
          <article className="panel">
            <h2>
              <span>Discovered Scorecards</span>
              <span className="badge tabular-num">{items.length} returned</span>
            </h2>

            {items.length > 0 ? (
              <>
                <DataTable caption="Strategy Scorecards Table" ariaLabel="Strategy Scorecards">
                  <thead>
                    <tr>
                      <th scope="col">Strategy</th>
                      <th scope="col">Evaluated</th>
                      <th scope="col">Status</th>
                      <th scope="col">Data Health</th>
                      <th scope="col">Evidence</th>
                      <th scope="col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const isSelected = item.scorecard_id === selectedId;
                      const inspectParams = new URLSearchParams();
                      if (strategyId) inspectParams.set("strategy_id", strategyId);
                      if (status) inspectParams.set("status", status);
                      if (offset > 0) inspectParams.set("offset", String(offset));
                      inspectParams.set("selected", item.scorecard_id);
                      return (
                        <tr key={item.scorecard_id} className={isSelected ? "row-selected" : ""}>
                          <td>
                            <StrategyIdentity strategyId={item.strategy_id} version={item.strategy_version} />
                          </td>
                          <td>
                            <small>{utc(item.evaluated_at)}</small>
                          </td>
                          <td>
                            <StatusBadge status={item.status} />
                          </td>
                          <td>
                            <QualityStateBadge status={item.dataset_health_status} />
                          </td>
                          <td>
                            <ResearchStatusBadge classification={item.evidence_classification} />
                          </td>
                          <td className="cell-action">
                            <Link href={`/scorecards?${inspectParams.toString()}`} className="table-link" aria-label={`Inspect scorecard ${item.scorecard_id}`}>
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
                    basePath="/scorecards"
                    searchParams={{ strategy_id: strategyId ?? "", status: status ?? "", selected: selectedId ?? "" }}
                  />
                )}
              </>
            ) : (
              <p className="empty-state">{stateText(discoveryResult)}</p>
            )}
          </article>
        </section>

        <aside aria-label="Scorecard Inspector">
          {data ? (
            <div className="inspector-card">
              <header className="inspector-header">
                <div className="inspector-title-group">
                  <div className="inspector-title-row">
                    <h2>{data.strategy_version}</h2>
                    <StatusBadge status={data.status} />
                  </div>
                  <code className="inspector-id-code">{data.scorecard_id}</code>
                </div>
              </header>

              <p className="warning">{data.evidence_classification}</p>

              <div className="inspector-section inspector-section-first">
                <span className="inspector-section-title">Header</span>
                <dl>
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
                  <dt>Validation Package</dt>
                  <dd>{data.validation_package_id ?? "UNAVAILABLE"}</dd>
                </dl>
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Evidence Coverage Summary</span>
                <p>
                  <strong className="metric-value">{measuredCount}</strong> measured &bull;{" "}
                  <strong className="metric-value">{assumedCount}</strong> assumed &bull;{" "}
                  <strong className="metric-value">{unavailableCount}</strong> unavailable
                </p>
              </div>

              {data.groups.map((group) => (
                <section key={group.name} aria-labelledby={`scorecard-${group.name}`} className="inspector-section">
                  <span className="inspector-section-title" id={`scorecard-${group.name}`}>{group.name}</span>
                  {group.metrics.length ? (
                    <DataTable caption={`${group.name} Metrics`}>
                      <thead>
                        <tr>
                          <th scope="col">Metric</th>
                          <th scope="col">Value &amp; Unit</th>
                          <th scope="col">Evidence State</th>
                          <th scope="col">Reference</th>
                        </tr>
                      </thead>
                      <tbody>
                        {group.metrics.map((metric) => (
                          <tr key={metric.metric_id}>
                            <td><strong>{metric.name}</strong></td>
                            <td>
                              <strong className="metric-value">{metric.value ?? "UNAVAILABLE"}</strong> {metric.unit}
                            </td>
                            <td>
                              <EvidenceStateBadge state={metric.evidence_state} />
                            </td>
                            <td><code>{metric.evidence_reference}</code></td>
                          </tr>
                        ))}
                      </tbody>
                    </DataTable>
                  ) : (
                    <p className="empty-state">UNAVAILABLE</p>
                  )}
                </section>
              ))}

              <section aria-labelledby="scorecard-complexity" className="inspector-section">
                <span className="inspector-section-title" id="scorecard-complexity">Complexity Analysis</span>
                <p className="empty-notice">Complexity penalty is a diagnostic component, not an authoritative alpha score.</p>
                {data.complexity_components.map((comp) => (
                  <div key={comp.component_id} className="margin-bottom-8">
                    <strong>{comp.name}</strong>: {comp.value ?? "UNAVAILABLE"} ({comp.formula_version}) &mdash; {comp.rationale}
                  </div>
                ))}
              </section>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">Limitations</span>
                <ul>
                  {data.limitations.map((limitation, index) => (
                    <li key={index}>{limitation}</li>
                  ))}
                </ul>
              </div>

              <EvidenceMeta
                source="PostgreSQL Strategy Scorecard V2"
                asOf={data.evaluated_at}
                version={data.schema_version}
                limitations={data.limitations}
                evidenceHash={data.content_hash}
              />
            </div>
          ) : (
            <div className="inspector-card">
              <p className="empty-state">
                {scorecardResult ? stateText(scorecardResult) : "Select a scorecard from the table to inspect details."}
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
