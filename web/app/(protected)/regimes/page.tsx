import Link from "next/link";
import { getWorkspaceContext, getRegimeRunDiscovery, getRegimeRun, stateText, utc } from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { FilterBar } from "../../components/filter-bar";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";

export const dynamic = "force-dynamic";

function probabilityPercent(value: string): number | null {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  const clamped = Math.max(0, Math.min(1, numeric));
  return Math.round(clamped * 1000) / 10;
}

function dominantSummary(items: { dimension: string; hard_label: string | null }[]): string {
  if (items.length === 0) return "UNAVAILABLE";
  return items.map((item) => `${item.dimension}=${item.hard_label ?? "UNAVAILABLE"}`).join("; ");
}

export default async function RegimesPage({
  searchParams,
}: {
  searchParams?: Promise<{
    instrument?: string; status?: string; model_version_id?: string; dataset_version?: string;
    selected?: string; offset?: string; limit?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const instrument = resolvedParams?.instrument?.trim() || undefined;
  const status = resolvedParams?.status?.trim() || undefined;
  const modelVersionId = resolvedParams?.model_version_id?.trim() || undefined;
  const datasetVersion = resolvedParams?.dataset_version?.trim() || undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 20) || 20;

  const ctx = await getWorkspaceContext();
  const discoveryResult = await getRegimeRunDiscovery(ctx, {
    instrument, status, model_version_id: modelVersionId, dataset_version: datasetVersion, limit, offset,
  });
  const discoveryPage = discoveryResult.state === "AVAILABLE" ? discoveryResult.value : undefined;
  const items = discoveryPage?.items ?? [];

  const selectedId = resolvedParams?.selected || (items.length > 0 ? items[0].run_id : undefined);
  const runResult = selectedId ? await getRegimeRun(ctx, { runId: selectedId }) : undefined;
  const data = runResult?.state === "AVAILABLE" ? runResult.value : undefined;

  const baseFilters = {
    instrument: instrument ?? "", status: status ?? "", model_version_id: modelVersionId ?? "",
    dataset_version: datasetVersion ?? "", selected: selectedId ?? "",
  };

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Regime Engine Workspace"
        subtitle="Persisted regime assessments, dimension probabilities and risk-multiplier effects. No calculation runs here -- every value is read directly from PostgreSQL evidence."
        status={discoveryResult.state}
        asOf={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>REGIME MAY REDUCE OR BLOCK RISK.</strong> REGIME CANNOT INCREASE GLOBAL RISK LIMITS. No risk-increase control exists on this platform.
      </div>

      <FilterBar
        groups={[
          {
            id: "filter-regime-status", name: "status", label: "Status",
            defaultValue: status ?? "ALL",
            options: [
              { label: "All Statuses", value: "ALL" },
              { label: "Blocked", value: "BLOCKED" },
              { label: "Review Required", value: "REVIEW_REQUIRED" },
            ],
          },
        ]}
        resetHref="/regimes"
        ariaLabel="Regime Run Discovery Filters"
      >
        <div className="filter-item">
          <label htmlFor="filter-regime-instrument" className="filter-label">Instrument</label>
          <input id="filter-regime-instrument" name="instrument" type="text" defaultValue={instrument ?? ""} className="filter-select" />
        </div>
        <div className="filter-item">
          <label htmlFor="filter-regime-model-version" className="filter-label">Model Version ID</label>
          <input id="filter-regime-model-version" name="model_version_id" type="text" defaultValue={modelVersionId ?? ""} className="filter-select" />
        </div>
      </FilterBar>

      <div className="workspace-split-layout">
        <section aria-label="Regime Run Discovery">
          <article className="panel">
            <h2>
              <span>Regime Runs</span>
              <span className="badge tabular-num">{items.length} returned</span>
            </h2>

            {items.length > 0 ? (
              <>
                <DataTable caption="Regime Runs Table" ariaLabel="Regime Runs">
                  <thead>
                    <tr>
                      <th scope="col">As Of</th>
                      <th scope="col">Instrument</th>
                      <th scope="col">Model / Rule Version</th>
                      <th scope="col">Dataset Version</th>
                      <th scope="col">Status</th>
                      <th scope="col">Dominant Regime</th>
                      <th scope="col">Uncertainty</th>
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
                          <td><small>{utc(item.as_of_timestamp)}</small></td>
                          <td>{item.instrument}</td>
                          <td>{item.model_version} / {item.rule_version}</td>
                          <td>{item.dataset_version}</td>
                          <td><StatusBadge status={item.status} /></td>
                          <td><small>{dominantSummary(item.dimension_summary)}</small></td>
                          <td><small className="warning">{item.uncertainty_summary}</small></td>
                          <td className="cell-action">
                            <Link href={`/regimes?${inspectParams.toString()}`} className="table-link" aria-label={`Inspect regime run ${item.run_id}`}>
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
                    basePath="/regimes"
                    searchParams={baseFilters}
                  />
                )}
              </>
            ) : (
              <p className="empty-state">{stateText(discoveryResult)}</p>
            )}
          </article>
        </section>

        <aside aria-label="Regime Assessment Inspector">
          {data ? (
            <div className="inspector-card">
              <header className="inspector-header">
                <div className="inspector-title-group">
                  <div className="inspector-title-row">
                    <h2>{data.instrument}</h2>
                    <StatusBadge status={data.status} />
                  </div>
                  <code className="inspector-id-code">{data.regime_assessment_id}</code>
                </div>
              </header>

              <div className="inspector-section inspector-section-first">
                <span className="inspector-section-title">Identity</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "model", label: "Model Version", value: data.model_version },
                    { key: "rule", label: "Rule Version", value: data.rule_version },
                    { key: "dataset", label: "Dataset Version", value: data.dataset_version },
                    { key: "instrument", label: "Instrument", value: data.instrument },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Point-in-Time Semantics</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "as-of", label: "As-Of Timestamp (UTC)", value: utc(data.as_of_timestamp) },
                    { key: "knowledge", label: "Knowledge Timestamp (UTC)", value: data.knowledge_timestamp ? utc(data.knowledge_timestamp) : "UNAVAILABLE" },
                  ]}
                />
              </div>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">Risk Boundary</span>
                <p>{data.risk_boundary}</p>
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Regime Dimensions</span>
                {data.dimensions.map((dim) => (
                  <div key={dim.observation_id} className="margin-bottom-16">
                    <h3>
                      {dim.dimension} <small>({dim.method})</small>
                    </h3>
                    <KeyValueGrid
                      columns={3}
                      items={[
                        { key: "evidence-state", label: "Evidence State", value: dim.evidence_state },
                        { key: "hard-label", label: "Hard Label", value: dim.hard_label ?? "UNAVAILABLE" },
                        {
                          key: "uncertainty", label: "Uncertainty",
                          value: dim.uncertainty !== null
                            ? <strong className="metric-value warning">{dim.uncertainty}</strong>
                            : <strong className="metric-value warning">UNAVAILABLE</strong>,
                        },
                      ]}
                    />
                    <div className="probability-bars" role="group" aria-label={`${dim.dimension} probability distribution`}>
                      {dim.probabilities.length > 0 ? (
                        dim.probabilities.map((prob) => {
                          const percent = probabilityPercent(prob.probability) ?? 0;
                          return (
                            <div key={prob.state} className="probability-bar-row">
                              <span className="probability-bar-label">{prob.state}</span>
                              <svg
                                className="probability-bar-track" viewBox="0 0 100 10" preserveAspectRatio="none"
                                role="img" aria-label={`${prob.state} probability ${prob.probability}`}
                              >
                                <rect className="probability-bar-track-bg" x="0" y="0" width="100" height="10" />
                                <rect className="probability-bar-fill" x="0" y="0" width={percent} height="10" />
                              </svg>
                              <span className="probability-bar-value tabular-num">{prob.probability}</span>
                            </div>
                          );
                        })
                      ) : (
                        <p className="empty-state">UNAVAILABLE</p>
                      )}
                    </div>
                    <p><code>{dim.content_hash}</code></p>
                  </div>
                ))}
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Regime Risk Effects</span>
                {data.risk_effects.length > 0 ? (
                  data.risk_effects.map((effect) => {
                    const currentNum = Number(effect.current_risk_multiplier);
                    const proposedNum = Number(effect.proposed_risk_multiplier);
                    const maxNum = Number(effect.preapproved_maximum);
                    const invariantHolds = Number.isFinite(currentNum) && Number.isFinite(proposedNum) && Number.isFinite(maxNum)
                      && proposedNum <= currentNum && proposedNum <= maxNum;
                    return (
                      <div key={effect.candidate_id} className="margin-bottom-16">
                        <KeyValueGrid
                          columns={2}
                          items={[
                            { key: "strategy-version", label: "Strategy Version", value: <code>{effect.strategy_version_id}</code> },
                            { key: "current", label: "Current Multiplier", value: effect.current_risk_multiplier },
                            { key: "proposed", label: "Proposed Multiplier", value: effect.proposed_risk_multiplier },
                            { key: "max", label: "Pre-Approved Maximum", value: effect.preapproved_maximum },
                            { key: "action", label: "Action", value: effect.action },
                            { key: "status", label: "Status", value: <StatusBadge status={effect.status} /> },
                          ]}
                        />
                        <p>
                          {invariantHolds ? (
                            <span className="badge badge-available">PROPOSED &le; CURRENT / PREAPPROVED MAXIMUM</span>
                          ) : (
                            <span className="badge badge-danger">ERROR / BLOCKED &mdash; INVARIANT VIOLATED IN PERSISTED EVIDENCE</span>
                          )}
                        </p>
                        <p><small>{effect.reasons.join(", ") || "No reasons recorded."}</small></p>
                        <p><small>automatic_authority: {String(effect.automatic_authority)}</small></p>
                      </div>
                    );
                  })
                ) : (
                  <p className="empty-state">UNAVAILABLE: no risk-adjustment candidate is bound to this run.</p>
                )}
              </div>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">Core Safety Boundary</span>
                <p>REGIME MAY REDUCE OR BLOCK RISK</p>
                <p>REGIME CANNOT INCREASE GLOBAL RISK LIMITS</p>
              </div>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">Limitations</span>
                <ul>
                  {data.limitations.map((limitation, index) => (
                    <li key={index}>{limitation}</li>
                  ))}
                </ul>
              </div>

              <p>
                Related portfolio runs:{" "}
                <Link href={`/portfolio?regime_run_id=${encodeURIComponent(data.regime_assessment_id)}`} className="workspace-link">
                  View constructions using this run &rarr;
                </Link>
              </p>

              <ProvenancePanel
                source="PostgreSQL Regime Engine V2"
                recordId={data.regime_assessment_id}
                version={data.model_version}
                datasetVersion={data.dataset_version}
                contentHash={data.evidence_hash}
                asOf={data.as_of_timestamp}
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
