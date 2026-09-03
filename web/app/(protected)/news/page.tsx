import Link from "next/link";
import { getWorkspaceContext, getNewsEventDiscovery, stateText, utc } from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { FilterBar } from "../../components/filter-bar";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";
import { DemoEvidenceBanner } from "../../components/demo-evidence-banner";

export const dynamic = "force-dynamic";

export default async function NewsPage({
  searchParams,
}: {
  searchParams?: Promise<{
    instrument?: string; entity?: string; category?: string; correction_state?: string;
    start?: string; end?: string; selected?: string; offset?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const instrument = resolvedParams?.instrument?.trim() || undefined;
  const entity = resolvedParams?.entity?.trim() || undefined;
  const category = resolvedParams?.category?.trim() || undefined;
  const correctionState = resolvedParams?.correction_state?.trim() || undefined;
  const start = resolvedParams?.start?.trim() || undefined;
  const end = resolvedParams?.end?.trim() || undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;

  const ctx = await getWorkspaceContext();
  const discoveryResult = await getNewsEventDiscovery(ctx, {
    instrument, entity, category, correction_state: correctionState, start, end, limit: 20, offset,
  });
  const discoveryPage = discoveryResult.state === "AVAILABLE" ? discoveryResult.value : undefined;
  const items = discoveryPage?.items ?? [];
  const providerState = discoveryPage?.provider_state ?? "EXTERNAL_BLOCKED";

  const selectedId = resolvedParams?.selected || (items.length > 0 ? items[0].event_id : undefined);
  const selected = items.find((item) => item.event_id === selectedId);

  const baseFilters = {
    instrument: instrument ?? "", entity: entity ?? "", category: category ?? "",
    correction_state: correctionState ?? "", start: start ?? "", end: end ?? "", selected: selectedId ?? "",
  };

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="News / Event Intelligence Workspace"
        subtitle="Persisted, correction-aware research event evidence read directly from PostgreSQL. This page never resembles a live feed and cannot generate an order."
        status={discoveryResult.state}
        asOf={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>NOT LIVE NEWS.</strong> RESEARCH EVIDENCE ONLY. NEWS EVENT &ne; ORDER. No order-generation action exists on this platform.
      </div>

      <article className="panel" aria-label="Provider State">
        <h2>
          <span>Provider State</span>
          <StatusBadge status={providerState} />
        </h2>
        <p className="warning">
          {providerState}{providerState === "EXTERNAL_BLOCKED" ? ": NO EXTERNAL NEWS PROVIDER AUTHORIZED." : "."}
        </p>
        {items.some((item) => !item.provider_activated) && (
          <DemoEvidenceBanner message="Persisted event evidence shown here is synthetic / demo event evidence unless a real news provider has been authorized and activated (it has not)." />
        )}
      </article>

      <FilterBar
        groups={[
          {
            id: "filter-news-correction-state", name: "correction_state", label: "Correction State",
            defaultValue: correctionState ?? "ALL",
            options: [
              { label: "All", value: "ALL" },
              { label: "Initial", value: "INITIAL" },
              { label: "Correction", value: "CORRECTION" },
              { label: "Retraction", value: "RETRACTION" },
              { label: "Follow-Up", value: "FOLLOW_UP" },
            ],
          },
        ]}
        resetHref="/news"
        ariaLabel="News Event Discovery Filters"
      >
        <div className="filter-item">
          <label htmlFor="filter-news-instrument" className="filter-label">Instrument</label>
          <input id="filter-news-instrument" name="instrument" type="text" defaultValue={instrument ?? ""} className="filter-select" />
        </div>
        <div className="filter-item">
          <label htmlFor="filter-news-entity" className="filter-label">Entity / Event Type</label>
          <input id="filter-news-entity" name="entity" type="text" defaultValue={entity ?? ""} className="filter-select" />
        </div>
        <div className="filter-item">
          <label htmlFor="filter-news-category" className="filter-label">Category</label>
          <input id="filter-news-category" name="category" type="text" defaultValue={category ?? ""} className="filter-select" />
        </div>
      </FilterBar>

      <div className="workspace-split-layout">
        <section aria-label="News Event Discovery">
          <article className="panel">
            <h2>
              <span>Event Discovery</span>
              <span className="badge tabular-num">{items.length} returned</span>
            </h2>
            {items.length > 0 ? (
              <>
                <DataTable caption="Persisted News / Event Discovery" ariaLabel="Persisted News / Event Discovery">
                  <thead>
                    <tr>
                      <th scope="col">Headline</th>
                      <th scope="col">Source</th>
                      <th scope="col">Category</th>
                      <th scope="col">Published</th>
                      <th scope="col">Revision</th>
                      <th scope="col">Urgency</th>
                      <th scope="col">Uncertainty</th>
                      <th scope="col">Credibility</th>
                      <th scope="col">Rights State</th>
                      <th scope="col">Linked Instrument(s)</th>
                      <th scope="col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const isSelected = item.event_id === selectedId;
                      const params = new URLSearchParams();
                      for (const [key, value] of Object.entries(baseFilters)) {
                        if (value && key !== "selected") params.set(key, value);
                      }
                      if (offset > 0) params.set("offset", String(offset));
                      params.set("selected", item.event_id);
                      return (
                        <tr key={item.event_id} className={isSelected ? "row-selected" : ""}>
                          <td>{item.headline}</td>
                          <td><small>{item.source}</small></td>
                          <td>{item.category}</td>
                          <td><small>{utc(item.published_at)}</small></td>
                          <td>
                            <StatusBadge status={item.revision_kind} /> <span className="tabular-num">#{item.revision}</span>
                          </td>
                          <td className="tabular-num">{item.urgency}</td>
                          <td className="tabular-num">{item.uncertainty}</td>
                          <td className="tabular-num">{item.credibility ?? "UNAVAILABLE"}</td>
                          <td>{item.rights_state}</td>
                          <td>
                            <small>
                              {item.entities.length > 0
                                ? item.entities.map((entity_) => `${entity_.instrument}${entity_.ambiguous ? " (AMBIGUOUS)" : ""}`).join(", ")
                                : "UNAVAILABLE"}
                            </small>
                          </td>
                          <td className="cell-action">
                            <Link href={`/news?${params.toString()}`} className="table-link" aria-label={`Inspect event ${item.headline}`}>
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
                    limit={discoveryPage.page.limit} offset={discoveryPage.page.offset}
                    returned={discoveryPage.page.returned} hasMore={discoveryPage.page.has_more}
                    basePath="/news" searchParams={baseFilters}
                  />
                )}
              </>
            ) : (
              <p className="empty-state">{stateText(discoveryResult)}</p>
            )}
          </article>
        </section>

        <aside aria-label="News Event Inspector">
          {selected ? (
            <div className="inspector-card">
              <header className="inspector-header">
                <div className="inspector-title-group">
                  <div className="inspector-title-row">
                    <h2>{selected.headline}</h2>
                    <StatusBadge status={selected.revision_kind} />
                  </div>
                  <span className="badge">{selected.assessment_status ?? "UNAVAILABLE"}</span>
                </div>
              </header>

              {!selected.provider_activated && (
                <DemoEvidenceBanner message="SYNTHETIC / DEMO EVENT EVIDENCE. No external news provider is authorized or activated for this event." />
              )}

              <div className="inspector-section inspector-section-first">
                <span className="inspector-section-title">Source</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "source", label: "Source", value: selected.source },
                    { key: "source-version", label: "Source Version", value: selected.source_version },
                    { key: "terms-version", label: "Terms Version", value: selected.source_terms_version },
                    { key: "rights-state", label: "Rights State", value: selected.rights_state },
                    { key: "authorization-state", label: "Authorization State", value: selected.authorization_state },
                    { key: "provider-active", label: "Provider Activated", value: selected.provider_activated ? "YES" : "NO" },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Time Semantics</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "published", label: "Published At", value: utc(selected.published_at) },
                    { key: "source-updated", label: "Source Updated At", value: utc(selected.source_updated_at) },
                    { key: "ingested", label: "Ingested At", value: utc(selected.ingested_at) },
                    { key: "correction-time", label: "Correction / Retraction Time", value: selected.correction_or_retraction_at ? utc(selected.correction_or_retraction_at) : "UNAVAILABLE -- no correction/retraction recorded" },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Category / Novelty / Urgency</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "category", label: "Category", value: selected.category },
                    { key: "novelty", label: "Novelty", value: selected.novelty },
                    { key: "urgency", label: "Urgency", value: selected.urgency },
                    { key: "horizon", label: "Horizon", value: selected.horizon },
                  ]}
                />
              </div>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">Credibility / Uncertainty</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "credibility", label: "Credibility", value: selected.credibility ?? "UNAVAILABLE (never treated as 100%)" },
                    { key: "uncertainty", label: "Uncertainty", value: selected.uncertainty ?? "UNAVAILABLE (never treated as zero)" },
                    { key: "assessment-status", label: "Assessment Status", value: selected.assessment_status ?? "UNAVAILABLE" },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Entity Links</span>
                {selected.entities.length > 0 ? (
                  <DataTable caption="Linked Entities" ariaLabel="Linked Entities">
                    <thead>
                      <tr>
                        <th scope="col">Instrument</th>
                        <th scope="col">Linking Method</th>
                        <th scope="col">Confidence</th>
                        <th scope="col">Ambiguous</th>
                        <th scope="col">Related Thesis</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.entities.map((entity_) => (
                        <tr key={entity_.entity_link_id}>
                          <td>{entity_.instrument}</td>
                          <td>{entity_.method}</td>
                          <td className="tabular-num">{entity_.confidence}</td>
                          <td>
                            {entity_.ambiguous
                              ? <span className="badge badge-warning">AMBIGUOUS &mdash; NOT EXACT</span>
                              : <span className="badge badge-available">RESOLVED</span>}
                          </td>
                          <td>
                            <Link href={`/investments?instrument=${encodeURIComponent(entity_.instrument)}`} className="workspace-link">
                              View thesis &rarr;
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </DataTable>
                ) : (
                  <p className="empty-state">UNAVAILABLE &mdash; no persisted entity link.</p>
                )}
              </div>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">Correction / Retraction Chain</span>
                <ol className="revision-chain" aria-label="Revision chain">
                  <li>
                    <strong>{selected.revision_kind} #{selected.revision}</strong>
                    {" -- "}{utc(selected.published_at)}
                    {selected.revision_kind === "RETRACTION" && (
                      <span className="badge badge-danger"> WITHDRAWN &mdash; DO NOT TREAT INITIAL CLAIM AS CURRENT</span>
                    )}
                  </li>
                  {selected.correction_chain.length > 0 ? (
                    selected.correction_chain.map((link, index) => (
                      <li key={index}>
                        <strong>{link.relation}</strong>: <code>{link.predecessor_id}</code> &rarr; <code>{link.successor_id}</code>
                      </li>
                    ))
                  ) : (
                    <li className="empty-state">No further correction/retraction relationship persisted for this event.</li>
                  )}
                </ol>
                <p><small>Sequence shown is the persisted revision chain (INITIAL &rarr; CORRECTION &rarr; RETRACTION where present). A retracted event is marked WITHDRAWN above; it is never shown as current without this marking.</small></p>
              </div>

              <ProvenancePanel
                source="News / Event Intelligence Authority"
                recordId={selected.event_id}
                version={String(selected.revision)}
                contentHash={selected.content_fingerprint}
                asOf={selected.published_at}
                limitations={[
                  ...selected.limitations,
                  "No historical point-in-time (as-of) query is supported by this authority; only the persisted current revision state is shown.",
                  "No raw credential or provider secret is exposed here.",
                ]}
              />
              <p><small>Provenance reference: {selected.provenance_reference}</small></p>
            </div>
          ) : (
            <div className="inspector-card">
              <p className="empty-state">{stateText(discoveryResult)}</p>
            </div>
          )}
        </aside>
      </div>

      <div className="panel-footer-row">
        <span className="status">NOT LIVE NEWS / RESEARCH EVIDENCE ONLY / NEWS EVENT &ne; ORDER</span>
      </div>
    </div>
  );
}
