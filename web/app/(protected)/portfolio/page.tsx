import Link from "next/link";
import { getWorkspaceContext, getPortfolioConstructionRunDiscovery, getPortfolioConstructionRun, stateText, utc } from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { FilterBar } from "../../components/filter-bar";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";

export const dynamic = "force-dynamic";

function toPercent(value: string | null): number | null {
  if (value === null) return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(0, Math.min(1, numeric)) * 100;
}

export default async function PortfolioPage({
  searchParams,
}: {
  searchParams?: Promise<{
    status?: string; policy_version_id?: string; regime_run_id?: string;
    selected?: string; offset?: string; limit?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const status = resolvedParams?.status?.trim() || undefined;
  const policyVersionId = resolvedParams?.policy_version_id?.trim() || undefined;
  const regimeRunId = resolvedParams?.regime_run_id?.trim() || undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 20) || 20;

  const ctx = await getWorkspaceContext();
  const discoveryResult = await getPortfolioConstructionRunDiscovery(ctx, {
    status, policy_version_id: policyVersionId, regime_run_id: regimeRunId, limit, offset,
  });
  const discoveryPage = discoveryResult.state === "AVAILABLE" ? discoveryResult.value : undefined;
  const items = discoveryPage?.items ?? [];

  const selectedId = resolvedParams?.selected || (items.length > 0 ? items[0].run_id : undefined);
  const runResult = selectedId ? await getPortfolioConstructionRun(ctx, { runId: selectedId }) : undefined;
  const data = runResult?.state === "AVAILABLE" ? runResult.value : undefined;

  const baseFilters = {
    status: status ?? "", policy_version_id: policyVersionId ?? "", regime_run_id: regimeRunId ?? "",
    selected: selectedId ?? "",
  };

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Portfolio Construction Workspace"
        subtitle="Requested-to-constrained allocation flow, constraint ledger and independent risk gate. Output is review-only evidence -- no apply, rebalance, or execution action exists here."
        status={discoveryResult.state}
        asOf={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>REVIEW ONLY.</strong> This workspace has no apply, rebalance-now, or execution control. Output is review-eligible evidence, never approval for execution.
      </div>

      <FilterBar
        groups={[
          {
            id: "filter-portfolio-status", name: "status", label: "Status",
            defaultValue: status ?? "ALL",
            options: [
              { label: "All Statuses", value: "ALL" },
              { label: "Blocked", value: "BLOCKED" },
              { label: "Review Required", value: "REVIEW_REQUIRED" },
            ],
          },
        ]}
        resetHref="/portfolio"
        ariaLabel="Portfolio Construction Run Filters"
      >
        <div className="filter-item">
          <label htmlFor="filter-portfolio-policy" className="filter-label">Policy Version ID</label>
          <input id="filter-portfolio-policy" name="policy_version_id" type="text" defaultValue={policyVersionId ?? ""} className="filter-select" />
        </div>
        <div className="filter-item">
          <label htmlFor="filter-portfolio-regime" className="filter-label">Regime Run ID</label>
          <input id="filter-portfolio-regime" name="regime_run_id" type="text" defaultValue={regimeRunId ?? ""} className="filter-select" />
        </div>
      </FilterBar>

      <div className="workspace-split-layout">
        <section aria-label="Portfolio Construction Run Discovery">
          <article className="panel">
            <h2>
              <span>Construction Runs</span>
              <span className="badge tabular-num">{items.length} returned</span>
            </h2>

            {items.length > 0 ? (
              <>
                <DataTable caption="Portfolio Construction Runs Table" ariaLabel="Portfolio Construction Runs">
                  <thead>
                    <tr>
                      <th scope="col">Constructed (UTC)</th>
                      <th scope="col">Policy Version</th>
                      <th scope="col">Regime Run</th>
                      <th scope="col">Status</th>
                      <th scope="col">Equity / Target Vol</th>
                      <th scope="col">Portfolio / Stressed Vol</th>
                      <th scope="col">Risk Gate</th>
                      <th scope="col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const isSelected = item.run_id === selectedId;
                      const inspectParams = new URLSearchParams();
                      for (const [key, value] of Object.entries(baseFilters)) {
                        if (value && key !== "selected") inspectParams.set(key, value);
                      }
                      if (offset > 0) inspectParams.set("offset", String(offset));
                      inspectParams.set("selected", item.run_id);
                      return (
                        <tr key={item.run_id} className={isSelected ? "row-selected" : ""}>
                          <td><small>{utc(item.constructed_at)}</small></td>
                          <td>{item.policy_version}</td>
                          <td>
                            <Link href={`/regimes?selected=${encodeURIComponent(item.regime_run_id)}`} className="table-link">
                              <code>{item.regime_run_id.slice(0, 8)}&hellip;</code>
                            </Link>
                          </td>
                          <td><StatusBadge status={item.status} /></td>
                          <td>{item.equity} / {item.target_volatility ?? "UNAVAILABLE"}</td>
                          <td>{item.portfolio_volatility} / {item.stressed_volatility}</td>
                          <td>
                            <StatusBadge
                              status={item.risk_gate_approved ? "APPROVED" : "BLOCKED"}
                              label={item.risk_gate_approved ? "REVIEW ELIGIBLE" : "BLOCKED"}
                            />
                          </td>
                          <td className="cell-action">
                            <Link href={`/portfolio?${inspectParams.toString()}`} className="table-link" aria-label={`Inspect portfolio construction run ${item.run_id}`}>
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
                    basePath="/portfolio"
                    searchParams={baseFilters}
                  />
                )}
              </>
            ) : (
              <p className="empty-state">{stateText(discoveryResult)}</p>
            )}
          </article>
        </section>

        <aside aria-label="Portfolio Construction Run Inspector">
          {data ? (
            <div className="inspector-card">
              <header className="inspector-header">
                <div className="inspector-title-group">
                  <div className="inspector-title-row">
                    <h2>{data.policy_version}</h2>
                    <StatusBadge status={data.status} />
                  </div>
                  <code className="inspector-id-code">{data.portfolio_construction_run_id}</code>
                </div>
              </header>

              <div className="inspector-section inspector-section-first">
                <span className="inspector-section-title">Run Summary</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "run-id", label: "Run ID", value: <code>{data.portfolio_construction_run_id}</code> },
                    { key: "policy-version-id", label: "Policy Version ID", value: <code>{data.policy_version_id}</code> },
                    {
                      key: "regime-run", label: "Regime Run",
                      value: (
                        <Link href={`/regimes?selected=${encodeURIComponent(data.regime_run_id)}`} className="table-link">
                          <code>{data.regime_run_id}</code>
                        </Link>
                      ),
                    },
                    { key: "constructed", label: "Constructed At (UTC)", value: utc(data.constructed_at) },
                    { key: "equity", label: "Equity", value: data.equity },
                    { key: "target-vol", label: "Target Volatility", value: data.target_volatility ?? "UNAVAILABLE" },
                    { key: "cash", label: "Cash Weight", value: data.cash_weight },
                    { key: "gross", label: "Gross Weight", value: data.gross_weight },
                    { key: "net", label: "Net Weight", value: data.net_weight },
                    { key: "portfolio-vol", label: "Portfolio Volatility", value: data.portfolio_volatility },
                    { key: "stressed-vol", label: "Stressed Volatility", value: data.stressed_volatility },
                    { key: "review-only", label: "Review Only", value: String(data.review_only) },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Allocation Flow (Requested &rarr; Review)</span>
                {data.sleeves.map((sleeve) => {
                  const requestedPct = toPercent(sleeve.requested_allocation) ?? 0;
                  const reviewPct = toPercent(sleeve.review_allocation) ?? 0;
                  return (
                    <div key={sleeve.sleeve_input_id} className="margin-bottom-16">
                      <h3>{sleeve.strategy_key}</h3>
                      <p className="tabular-num">
                        {sleeve.requested_allocation} requested &rarr;{" "}
                        <strong className="metric-value">{sleeve.review_allocation ?? "REJECTED"}</strong> review
                      </p>
                      <div className="probability-bars" role="group" aria-label={`${sleeve.strategy_key} requested versus review allocation`}>
                        <div className="probability-bar-row">
                          <span className="probability-bar-label">Requested</span>
                          <svg className="probability-bar-track" viewBox="0 0 100 10" preserveAspectRatio="none" role="img" aria-label={`Requested allocation ${sleeve.requested_allocation}`}>
                            <rect className="probability-bar-track-bg" x="0" y="0" width="100" height="10" />
                            <rect className="probability-bar-fill" x="0" y="0" width={requestedPct} height="10" />
                          </svg>
                          <span className="probability-bar-value tabular-num">{sleeve.requested_allocation}</span>
                        </div>
                        <div className="probability-bar-row">
                          <span className="probability-bar-label">Review</span>
                          {sleeve.review_allocation !== null ? (
                            <>
                              <svg className="probability-bar-track" viewBox="0 0 100 10" preserveAspectRatio="none" role="img" aria-label={`Review allocation ${sleeve.review_allocation}`}>
                                <rect className="probability-bar-track-bg" x="0" y="0" width="100" height="10" />
                                <rect className="probability-bar-fill" x="0" y="0" width={reviewPct} height="10" />
                              </svg>
                              <span className="probability-bar-value tabular-num">{sleeve.review_allocation}</span>
                            </>
                          ) : (
                            <span className="empty-state">REJECTED</span>
                          )}
                        </div>
                      </div>
                      <KeyValueGrid
                        columns={4}
                        items={[
                          { key: "effective-notional", label: "Effective Notional", value: sleeve.effective_notional ?? "UNAVAILABLE" },
                          { key: "risk-budget", label: "Risk Budget", value: sleeve.risk_budget },
                          { key: "capacity", label: "Capacity Weight", value: sleeve.capacity_weight },
                          { key: "liquidity", label: "Liquidity Score", value: sleeve.liquidity_score },
                          { key: "drawdown", label: "Drawdown", value: sleeve.drawdown },
                          { key: "regime-current", label: "Regime Multiplier (Current)", value: sleeve.regime_current_multiplier },
                          { key: "regime-proposed", label: "Regime Multiplier (Proposed)", value: sleeve.regime_proposed_multiplier },
                          { key: "marginal-risk", label: "Marginal Risk", value: sleeve.marginal_risk ?? "UNAVAILABLE" },
                          { key: "component-risk", label: "Component Risk", value: sleeve.component_risk ?? "UNAVAILABLE" },
                        ]}
                      />
                      <div className="reason-chips" aria-label={`${sleeve.strategy_key} adjustment reasons`}>
                        {sleeve.adjustment_reasons.length > 0 ? (
                          sleeve.adjustment_reasons.map((reason, index) => (
                            <span key={index} className="badge badge-warning reason-chip">{reason}</span>
                          ))
                        ) : (
                          <span className="empty-state">No adjustment reasons recorded.</span>
                        )}
                      </div>
                      {sleeve.rejected && (
                        <p className="warning">
                          REJECTED &mdash; {sleeve.rejection_reasons.join(", ") || "no rejection reason recorded"}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Constraint Ledger</span>
                <DataTable caption="Portfolio Constraints" ariaLabel="Portfolio Constraints">
                  <thead>
                    <tr>
                      <th scope="col">Constraint</th>
                      <th scope="col">State</th>
                      <th scope="col">Observed</th>
                      <th scope="col">Limit</th>
                      <th scope="col">Reasons</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.constraints.map((constraint) => (
                      <tr key={constraint.constraint_id}>
                        <td>{constraint.name}</td>
                        <td><StatusBadge status={constraint.state} /></td>
                        <td>{constraint.observed ?? "UNAVAILABLE"}</td>
                        <td>{constraint.limit ?? "UNAVAILABLE"}</td>
                        <td><small>{constraint.reasons.join(", ") || "none recorded"}</small></td>
                      </tr>
                    ))}
                  </tbody>
                </DataTable>
              </div>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">Independent Portfolio Risk Gate</span>
                <p>
                  <StatusBadge
                    status={data.risk_gate_approved ? "APPROVED" : "BLOCKED"}
                    label={data.risk_gate_approved ? "REVIEW ELIGIBLE" : "BLOCKED"}
                  />
                </p>
                <ul>
                  {data.risk_gate_reasons.length > 0 ? (
                    data.risk_gate_reasons.map((reason, index) => <li key={index}>{reason}</li>)
                  ) : (
                    <li>No reasons recorded.</li>
                  )}
                </ul>
                <p><small>automatic_authority: {String(data.automatic_authority)}; review_only: {String(data.review_only)}</small></p>
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Covariance / Correlation Evidence</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "dataset-version", label: "Dataset Version", value: data.covariance.dataset_version },
                    { key: "dataset-hash", label: "Dataset Content Hash", value: <code>{data.covariance.dataset_content_hash}</code> },
                    { key: "estimation-version", label: "Estimation Version", value: data.covariance.estimation_version },
                    { key: "observations", label: "Observations", value: data.covariance.observations },
                    { key: "as-of", label: "As Of (UTC)", value: utc(data.covariance.as_of) },
                    { key: "uncertainty", label: "Uncertainty", value: <strong className="warning">{data.covariance.uncertainty}</strong> },
                    { key: "correlation-stress", label: "Correlation Stress", value: data.covariance.correlation_stress },
                    { key: "source-provider", label: "Source Provider", value: data.covariance.source_provider },
                    { key: "source-terms", label: "Source Terms Version", value: data.covariance.source_terms_version },
                    { key: "provider-backed", label: "Provider-Backed", value: String(data.covariance.provider_backed) },
                  ]}
                />
                <p className={data.covariance.provider_backed ? "" : "warning"}>{data.covariance.classification}</p>
              </div>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">Limitations</span>
                <ul>
                  {data.limitations.map((limitation, index) => (
                    <li key={index}>{limitation}</li>
                  ))}
                </ul>
              </div>

              <ProvenancePanel
                source="PostgreSQL Portfolio Construction V2"
                recordId={data.portfolio_construction_run_id}
                version={data.policy_version}
                datasetVersion={data.covariance.dataset_version}
                contentHash={data.content_hash}
                asOf={data.constructed_at}
                limitations={data.limitations}
              />
            </div>
          ) : (
            <div className="inspector-card">
              <p className="empty-state">
                {items.length > 0 ? (runResult ? stateText(runResult) : "Select a run from the table to inspect details.") : stateText(discoveryResult)}
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
