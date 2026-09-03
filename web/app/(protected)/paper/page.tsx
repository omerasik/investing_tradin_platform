import Link from "next/link";
import {
  getWorkspaceContext,
  getPaperOrderDetail,
  getPaperOrderDiscovery,
  getPaperReconciliationDetail,
  stateText,
  utc,
} from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { SafetyBanner } from "../../components/safety-banner";
import { FilterBar } from "../../components/filter-bar";
import { SearchField } from "../../components/search-field";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";

export const dynamic = "force-dynamic";

function remainingQuantity(order: { quantity: string; filled_quantity: string }): string {
  const ordered = Number(order.quantity);
  const filled = Number(order.filled_quantity);
  if (!Number.isFinite(ordered) || !Number.isFinite(filled)) return "UNAVAILABLE";
  const remaining = ordered - filled;
  return Number.isFinite(remaining) ? String(remaining) : "UNAVAILABLE";
}

export default async function PaperPage({
  searchParams,
}: {
  searchParams?: Promise<{
    account?: string; instrument?: string; side?: string; status?: string;
    fill_state?: string; reconciliation_state?: string; selected?: string;
    offset?: string; limit?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const accountFilter = resolvedParams?.account?.trim() || undefined;
  const instrumentFilter = resolvedParams?.instrument?.trim() || undefined;
  const sideFilter = resolvedParams?.side?.trim() || undefined;
  const lifecycleFilter = resolvedParams?.status?.trim() || undefined;
  const fillStateFilter = resolvedParams?.fill_state?.trim() || undefined;
  const reconciliationFilter = resolvedParams?.reconciliation_state?.trim() || undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 20) || 20;

  const ctx = await getWorkspaceContext();
  const orders = await getPaperOrderDiscovery(ctx, {
    account_id: accountFilter, instrument: instrumentFilter, side: sideFilter,
    lifecycle_status: lifecycleFilter, fill_state: fillStateFilter,
    reconciliation_state: reconciliationFilter, limit, offset,
  });
  const orderPage = orders.state === "AVAILABLE" ? orders.value : undefined;
  const items = orderPage?.items ?? [];

  const selectedId = resolvedParams?.selected || (items.length > 0 ? items[0].intent_id : undefined);
  const selectedRow = items.find((item) => item.intent_id === selectedId);

  const [detailResult, reconciliationResult] = await Promise.all([
    selectedId ? getPaperOrderDetail(ctx, selectedId) : Promise.resolve(undefined),
    selectedRow ? getPaperReconciliationDetail(ctx, selectedRow.account_id) : Promise.resolve(undefined),
  ]);
  const order = detailResult && detailResult.state === "AVAILABLE" ? detailResult.value : undefined;
  const reconciliation = reconciliationResult && reconciliationResult.state === "AVAILABLE" ? reconciliationResult.value : undefined;

  const baseFilters = {
    account: accountFilter ?? "", instrument: instrumentFilter ?? "", side: sideFilter ?? "",
    status: lifecycleFilter ?? "", fill_state: fillStateFilter ?? "", reconciliation_state: reconciliationFilter ?? "",
    selected: selectedId ?? "",
  };

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Paper OMS"
        subtitle="Paper order lifecycle, fills, and account reconciliation evidence. Simulation only."
        status={orders.state}
        asOf={ctx.evidenceTime}
      />

      <SafetyBanner message="PAPER ONLY. NO BROKER CONNECTIVITY. NO LIVE ORDER SUBMISSION. This console cannot place, cancel, or route a live order." />

      <FilterBar
        groups={[
          {
            id: "filter-paper-side", name: "side", label: "Side",
            defaultValue: sideFilter ?? "ALL",
            options: [
              { label: "All Sides", value: "ALL" },
              { label: "Buy", value: "BUY" },
              { label: "Sell", value: "SELL" },
            ],
          },
          {
            id: "filter-paper-fill-state", name: "fill_state", label: "Fill State",
            defaultValue: fillStateFilter ?? "ALL",
            options: [
              { label: "All Fill States", value: "ALL" },
              { label: "Unfilled", value: "UNFILLED" },
              { label: "Partial or Final Fill", value: "PARTIAL_OR_FINAL_FILL" },
            ],
          },
          {
            id: "filter-paper-reconciliation", name: "reconciliation_state", label: "Reconciliation",
            defaultValue: reconciliationFilter ?? "ALL",
            options: [
              { label: "All Reconciliation States", value: "ALL" },
              { label: "Healthy", value: "HEALTHY" },
              { label: "Reconciliation Required", value: "RECONCILIATION_REQUIRED" },
              { label: "Unavailable", value: "UNAVAILABLE" },
            ],
          },
        ]}
        resetHref="/paper"
        ariaLabel="Paper Order Filters"
      >
        <SearchField id="paper-account-search" name="account" defaultValue={accountFilter ?? ""} placeholder="Account ID..." label="Filter by Account" />
        <SearchField id="paper-instrument-search" name="instrument" defaultValue={instrumentFilter ?? ""} placeholder="Symbol or instrument ID..." label="Filter by Instrument" />
        <div className="filter-item">
          <label htmlFor="paper-status-search" className="filter-label">Lifecycle Status</label>
          <input id="paper-status-search" name="status" type="text" defaultValue={lifecycleFilter ?? ""} className="filter-select" placeholder="e.g. FILLED" />
        </div>
      </FilterBar>

      <div className="workspace-split-layout">
        <section aria-label="Discovered Paper Orders">
          <article className="panel">
            <h2>
              <span>Paper Order Discovery</span>
              <span className="badge tabular-num">{items.length} returned</span>
            </h2>

            {items.length > 0 ? (
              <>
                <DataTable caption="Paper Order Blotter" ariaLabel="Paper Orders">
                  <thead>
                    <tr>
                      <th scope="col">Instrument</th>
                      <th scope="col">Side</th>
                      <th scope="col">Quantity</th>
                      <th scope="col">Lifecycle Status</th>
                      <th scope="col">Fill State</th>
                      <th scope="col">Reconciliation</th>
                      <th scope="col">Account</th>
                      <th scope="col">Created (UTC)</th>
                      <th scope="col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const isSelected = item.intent_id === selectedId;
                      const inspectParams = new URLSearchParams();
                      for (const [key, value] of Object.entries(baseFilters)) {
                        if (value && key !== "selected") inspectParams.set(key, value);
                      }
                      if (offset > 0) inspectParams.set("offset", String(offset));
                      inspectParams.set("selected", item.intent_id);
                      return (
                        <tr key={item.intent_id} className={isSelected ? "row-selected" : ""}>
                          <td>
                            <strong>{item.canonical_symbol ?? item.instrument_id}</strong>
                          </td>
                          <td>{item.side}</td>
                          <td className="tabular-num">{item.quantity}</td>
                          <td><StatusBadge status={item.lifecycle_status} /></td>
                          <td>{item.fill_state}</td>
                          <td><StatusBadge status={item.reconciliation_state} /></td>
                          <td>{item.account_id}</td>
                          <td><small>{utc(item.created_at)}</small></td>
                          <td className="cell-action">
                            <Link href={`/paper?${inspectParams.toString()}`} className="table-link" aria-label={`Inspect paper order ${item.intent_id}`}>
                              Inspect &rarr;
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </DataTable>

                {orderPage && (
                  <Pagination
                    limit={orderPage.page.limit}
                    offset={orderPage.page.offset}
                    returned={orderPage.page.returned}
                    hasMore={orderPage.page.has_more}
                    basePath="/paper"
                    searchParams={baseFilters}
                  />
                )}
              </>
            ) : (
              <p className="empty-state">{stateText(orders)}</p>
            )}
          </article>
        </section>

        <aside aria-label="Paper Order Inspector">
          {order ? (
            <div className="inspector-card">
              <header className="inspector-header">
                <div className="inspector-title-group">
                  <div className="inspector-title-row">
                    <h2>{order.instrument_id}</h2>
                    <StatusBadge status={order.status} />
                  </div>
                  <code className="inspector-id-code">{order.intent_id}</code>
                </div>
              </header>

              <div className="inspector-section inspector-section-first">
                <span className="inspector-section-title">Identity</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "intent", label: "Intent ID", value: <code>{order.intent_id}</code> },
                    { key: "account", label: "Account", value: order.account_id },
                    { key: "instrument", label: "Instrument", value: order.instrument_id },
                    { key: "side", label: "Side", value: order.side },
                    { key: "quantity", label: "Quantity", value: order.quantity },
                    { key: "created", label: "Created At (UTC)", value: utc(order.created_at) },
                  ]}
                />
              </div>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">Execution Boundary</span>
                <p>PAPER ONLY</p>
                <p>NO BROKER TRANSPORT</p>
                <p>NO LIVE EXECUTION</p>
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Lifecycle Timeline</span>
                {order.events.length > 0 ? (
                  <ol className="lifecycle-timeline">
                    {order.events.map((event) => (
                      <li key={event.event_id}>
                        <strong>{event.event_type}</strong> @ {utc(event.occurred_at)}
                        <br />
                        <code className="inspector-id-code">{event.event_id}</code>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="empty-state">No persisted lifecycle events.</p>
                )}
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Fills</span>
                {order.fills.length > 0 ? (
                  <DataTable caption="Paper Fills" ariaLabel="Paper Fills">
                    <thead>
                      <tr>
                        <th scope="col">Fill ID</th>
                        <th scope="col">External Fill ID</th>
                        <th scope="col">Quantity</th>
                        <th scope="col">Price</th>
                        <th scope="col">Occurred (UTC)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {order.fills.map((fill) => (
                        <tr key={fill.fill_id}>
                          <td><code>{fill.fill_id}</code></td>
                          <td>{fill.external_fill_id}</td>
                          <td className="tabular-num">{fill.quantity}</td>
                          <td className="tabular-num">{fill.price}</td>
                          <td><small>{utc(fill.occurred_at)}</small></td>
                        </tr>
                      ))}
                    </tbody>
                  </DataTable>
                ) : (
                  <p className="empty-state">No fills recorded.</p>
                )}
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Paper Fill Summary</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "ordered", label: "Ordered Quantity", value: order.quantity },
                    { key: "filled", label: "Filled Quantity", value: order.filled_quantity },
                    { key: "remaining", label: "Remaining Quantity", value: remainingQuantity(order) },
                    { key: "avg-price", label: "Average Fill Price", value: order.average_fill_price ?? "UNAVAILABLE" },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Cross-Links</span>
                <p>
                  <Link href={`/risk?account_id=${encodeURIComponent(order.account_id)}`} className="table-link">
                    View risk evidence for this account &rarr;
                  </Link>
                </p>
              </div>

              <ProvenancePanel
                source="Paper OMS Authority"
                recordId={order.intent_id}
                version="paper-oms-v1"
                asOf={order.created_at}
                limitations={["Paper-only evidence; no broker execution capability; cannot submit, cancel, or route a live order."]}
              />
            </div>
          ) : (
            <div className="inspector-card">
              <p className="empty-state">
                {items.length > 0 ? "Select an order from the discovery table to inspect its lifecycle." : (detailResult ? stateText(detailResult) : "No paper order selected.")}
              </p>
            </div>
          )}
        </aside>
      </div>

      <article className="panel">
        <h2>
          <span>Paper Reconciliation</span>
          <StatusBadge status={reconciliation ? (reconciliation.complete ? "HEALTHY" : "WARNING") : (reconciliationResult?.state ?? "UNAVAILABLE")} />
        </h2>
        {reconciliation ? (
          <>
            <KeyValueGrid
              columns={2}
              items={[
                { key: "source", label: "Reconciliation Source", value: reconciliation.source },
                { key: "occurred", label: "Occurred At (UTC)", value: utc(reconciliation.occurred_at) },
                {
                  key: "complete", label: "Status",
                  value: <StatusBadge status={reconciliation.complete ? "AVAILABLE" : "WARNING"} label={reconciliation.complete ? "PAPER ACCOUNT RECONCILED" : "DISCREPANCY"} />,
                },
                { key: "buying-power", label: "Buying Power", value: reconciliation.reconciled_account?.buying_power ?? "UNAVAILABLE" },
                {
                  key: "discrepancies", label: "Discrepancies",
                  value: reconciliation.discrepancies.length ? reconciliation.discrepancies.join(", ") : "None recorded.",
                },
              ]}
            />
            <p className="warning">
              This reflects paper-account reconciliation only. It is not broker reconciliation and does not imply any live-broker connectivity.
            </p>
          </>
        ) : (
          <p className="empty-state">
            {selectedRow
              ? (reconciliationResult ? stateText(reconciliationResult) : "UNAVAILABLE: no reconciliation evidence recorded for this account.")
              : "Select a paper order to view its account reconciliation evidence."}
          </p>
        )}
      </article>
    </div>
  );
}
