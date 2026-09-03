import { getWorkspaceContext, getAlertsEvidence, getAuditEventDiscovery, getAuditEventDetail, stateText, utc } from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { SafetyBanner } from "../../components/safety-banner";
import { FilterBar } from "../../components/filter-bar";
import { SearchField } from "../../components/search-field";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function AuditPage({
  searchParams,
}: {
  searchParams?: Promise<{
    event_type?: string; actor?: string; start?: string; end?: string;
    selected?: string; offset?: string; limit?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const eventTypeFilter = resolvedParams?.event_type?.trim() || undefined;
  const actorFilter = resolvedParams?.actor?.trim() || undefined;
  const startFilter = resolvedParams?.start?.trim() || undefined;
  const endFilter = resolvedParams?.end?.trim() || undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 20) || 20;

  const ctx = await getWorkspaceContext();
  const [auditResult, alertsResult] = await Promise.all([
    getAuditEventDiscovery(ctx, {
      event_type: eventTypeFilter, actor: actorFilter, start: startFilter, end: endFilter, limit, offset,
    }),
    getAlertsEvidence(ctx),
  ]);
  const auditPage = auditResult.state === "AVAILABLE" ? auditResult.value : undefined;
  const auditItems = auditPage?.items ?? [];
  const alertRows = alertsResult.state === "AVAILABLE" ? alertsResult.value : undefined;

  const selectedId = resolvedParams?.selected || undefined;
  const selectedDetailResult = selectedId ? await getAuditEventDetail(ctx, selectedId) : undefined;
  const selectedEvent = selectedDetailResult && selectedDetailResult.state === "AVAILABLE" ? selectedDetailResult.value : undefined;

  const baseFilters = {
    event_type: eventTypeFilter ?? "", actor: actorFilter ?? "", start: startFilter ?? "", end: endFilter ?? "",
    selected: selectedId ?? "",
  };

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Audit Workspace"
        subtitle="Immutable audit event evidence, kept distinct from operational alerts."
        status={auditResult.state}
        asOf={ctx.evidenceTime}
      />

      <SafetyBanner message="READ ONLY. IMMUTABLE EVIDENCE. NO MUTATION ROUTE EXPOSED BY DASHBOARD." />

      <article className="panel">
        <h2>
          <span>Audit Events</span>
          <span className="badge tabular-num">{auditItems.length} returned</span>
        </h2>
        <p>
          Audit events are actor / action / decision evidence: who did what, to which object, when, and why. They are
          a separate domain from operational alerts below.
        </p>
        {auditPage && (
          <KeyValueGrid
            columns={3}
            items={[
              { key: "authority", label: "Audit Authority", value: <code>{auditPage.audit_authority}</code> },
              { key: "mutation", label: "Mutation Route Exposed by Dashboard", value: auditPage.mutation_route_exposed_by_dashboard ? "YES" : "NO" },
              { key: "immutability", label: "Immutability Guarantee", value: <small>{auditPage.immutability_guarantee}</small> },
            ]}
          />
        )}

        <FilterBar
          resetHref="/audit"
          ariaLabel="Audit Event Filters"
        >
          <SearchField id="audit-event-type-search" name="event_type" defaultValue={eventTypeFilter ?? ""} placeholder="e.g. audit.event.created" label="Filter by Event Type" />
          <SearchField id="audit-actor-search" name="actor" defaultValue={actorFilter ?? ""} placeholder="Actor..." label="Filter by Actor" />
          <div className="filter-item">
            <label htmlFor="audit-start" className="filter-label">Start Date (UTC)</label>
            <input id="audit-start" name="start" type="date" defaultValue={startFilter ?? ""} className="filter-select" />
          </div>
          <div className="filter-item">
            <label htmlFor="audit-end" className="filter-label">End Date (UTC)</label>
            <input id="audit-end" name="end" type="date" defaultValue={endFilter ?? ""} className="filter-select" />
          </div>
        </FilterBar>

        <div className="workspace-split-layout">
          <section aria-label="Audit Event Ledger">
            {auditItems.length > 0 ? (
              <>
                <DataTable caption="Audit Event Ledger" ariaLabel="Audit Events">
                  <thead>
                    <tr>
                      <th scope="col">Event Type</th>
                      <th scope="col">Actor</th>
                      <th scope="col">Occurred (UTC)</th>
                      <th scope="col">Event ID</th>
                      <th scope="col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditItems.map((item) => {
                      const isSelected = item.event_id === selectedId;
                      const inspectParams = new URLSearchParams();
                      for (const [key, value] of Object.entries(baseFilters)) {
                        if (value && key !== "selected") inspectParams.set(key, value);
                      }
                      if (offset > 0) inspectParams.set("offset", String(offset));
                      inspectParams.set("selected", item.event_id);
                      return (
                        <tr key={item.event_id} className={isSelected ? "row-selected" : ""}>
                          <td><strong>{item.event_type}</strong></td>
                          <td>{item.actor}</td>
                          <td><small>{utc(item.occurred_at)}</small></td>
                          <td><code className="inspector-id-code">{item.event_id}</code></td>
                          <td className="cell-action">
                            <Link href={`/audit?${inspectParams.toString()}`} className="table-link" aria-label={`Inspect audit event ${item.event_id}`}>
                              Inspect &rarr;
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </DataTable>
                {auditPage && (
                  <Pagination
                    limit={auditPage.page.limit}
                    offset={auditPage.page.offset}
                    returned={auditPage.page.returned}
                    hasMore={auditPage.page.has_more}
                    basePath="/audit"
                    searchParams={baseFilters}
                  />
                )}
              </>
            ) : (
              <p className="empty-state">
                {auditResult.state === "AVAILABLE" ? "EMPTY: no audit events matched this bounded query." : `${stateText(auditResult)} No audit events does not mean nothing happened -- it means no matching record was persisted or the audit authority is unavailable.`}
              </p>
            )}
          </section>

          <aside aria-label="Audit Event Inspector">
            {selectedEvent ? (
              <div className="inspector-card">
                <header className="inspector-header">
                  <div className="inspector-title-group">
                    <div className="inspector-title-row">
                      <h2>{selectedEvent.event_type}</h2>
                    </div>
                    <code className="inspector-id-code">{selectedEvent.event_id}</code>
                  </div>
                </header>

                <div className="inspector-section inspector-section-first">
                  <span className="inspector-section-title">Identity</span>
                  <KeyValueGrid
                    columns={2}
                    items={[
                      { key: "event-id", label: "Event ID", value: <code>{selectedEvent.event_id}</code> },
                      { key: "event-type", label: "Event Type / Action", value: selectedEvent.event_type },
                      { key: "actor", label: "Actor", value: selectedEvent.actor },
                      { key: "occurred", label: "Occurred At (UTC)", value: utc(selectedEvent.occurred_at) },
                    ]}
                  />
                </div>

                <div className="inspector-section">
                  <span className="inspector-section-title">Evidence (redacted, typed projection)</span>
                  {Object.keys(selectedEvent.payload).length > 0 ? (
                    <KeyValueGrid
                      columns={2}
                      items={Object.entries(selectedEvent.payload).map(([key, value]) => ({
                        key, label: key, value: typeof value === "object" ? JSON.stringify(value) : String(value),
                      }))}
                    />
                  ) : (
                    <p className="empty-state">No additional metadata recorded.</p>
                  )}
                </div>

                <ProvenancePanel
                  source="Audit Event Authority (SQLite append-only store)"
                  recordId={selectedEvent.event_id}
                  asOf={selectedEvent.occurred_at}
                  limitations={[
                    "No update or delete route is exposed for this record by store or API.",
                    "Development-grade append-only guarantee, not the PostgreSQL immutability trigger used elsewhere on this platform.",
                    "Sensitive-looking payload keys are redacted before leaving the backend.",
                  ]}
                />
              </div>
            ) : (
              <div className="inspector-card">
                <p className="empty-state">
                  {auditItems.length > 0 ? "Select an event from the ledger to inspect its full evidence." : (selectedDetailResult ? stateText(selectedDetailResult) : "No audit event selected.")}
                </p>
              </div>
            )}
          </aside>
        </div>
      </article>

      <article className="panel">
        <h2>
          <span>Operational Alerts</span>
          <StatusBadge status={alertsResult.state} />
        </h2>
        <p>
          Operational alerts are signal-detection records raised by monitoring, not actor/decision evidence. They do
          not substitute for audit events.
        </p>

        {alertRows?.length ? (
          <DataTable caption="Operational Alerts" ariaLabel="Operational Alerts">
            <thead>
              <tr>
                <th scope="col">Alert ID</th>
                <th scope="col">Code / Severity</th>
                <th scope="col">Resource</th>
                <th scope="col">Status</th>
                <th scope="col">Created At (UTC)</th>
              </tr>
            </thead>
            <tbody>
              {alertRows.map((item) => (
                <tr key={item.alert_id}>
                  <td><code>{item.alert_id}</code></td>
                  <td><strong>{item.code}</strong> &bull; <StatusBadge status={item.severity} /></td>
                  <td>{item.resource}</td>
                  <td><StatusBadge status={item.status} /></td>
                  <td><small>{utc(item.created_at)}</small></td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : (
          <p className="empty-state">
            {alertRows ? "No active alerts recorded. (No alert does not mean no audit event.)" : stateText(alertsResult)}
          </p>
        )}
      </article>
    </div>
  );
}
