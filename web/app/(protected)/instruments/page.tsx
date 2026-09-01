import React from "react";
import Link from "next/link";
import {
  getWorkspaceContext,
  getInstrumentDiscovery,
  getInstrumentDetail,
  stateText,
  utc,
} from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { FilterBar } from "../../components/filter-bar";
import { SearchField } from "../../components/search-field";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { InstrumentIdentity } from "../../components/instrument-identity";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { DatasetVersionBadge } from "../../components/dataset-version-badge";
import { KeyValueGrid } from "../../components/key-value-grid";
import { DemoEvidenceBanner } from "../../components/demo-evidence-banner";
import { ProvenancePanel } from "../../components/provenance-panel";

export const dynamic = "force-dynamic";

export default async function InstrumentsPage({
  searchParams,
}: {
  searchParams?: Promise<{
    query?: string;
    asset_class?: string;
    lifecycle_status?: string;
    selected?: string;
    offset?: string;
    limit?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const query = resolvedParams?.query?.trim() || undefined;
  const assetClass = resolvedParams?.asset_class || undefined;
  const lifecycleStatus = resolvedParams?.lifecycle_status || undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 20) || 20;

  const ctx = await getWorkspaceContext();
  const discoveryResult = await getInstrumentDiscovery(ctx, {
    query,
    asset_class: assetClass,
    lifecycle_status: lifecycleStatus,
    limit,
    offset,
  });

  const discoveryPage = discoveryResult.state === "AVAILABLE" ? discoveryResult.value : undefined;
  const items = discoveryPage?.items ?? [];

  const selectedId = resolvedParams?.selected || (items.length > 0 ? items[0].instrument_id : undefined);

  let selectedDetailResult = undefined;
  if (selectedId) {
    selectedDetailResult = await getInstrumentDetail(ctx, selectedId);
  }

  const selectedDetail = selectedDetailResult?.state === "AVAILABLE" ? selectedDetailResult.value : undefined;

  const hasDemo = items.some((i) => i.synthetic_demo) || (selectedDetail?.synthetic_demo ?? false);

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Instrument Workstation"
        subtitle="Canonical point-in-time instrument identities, venue symbol mappings, and lifecycle provenance."
        status={discoveryResult.state}
        asOf={ctx.evidenceTime}
      />

      {hasDemo && (
        <DemoEvidenceBanner message="Some instruments listed below are synthetic test instruments designated with DEMO tags. No live market orders or broker execution are supported." />
      )}

      {/* Filter and Search Bar */}
      <FilterBar
        groups={[
          {
            id: "filter-asset-class",
            name: "asset_class",
            label: "Asset Class",
            defaultValue: assetClass ?? "ALL",
            options: [
              { label: "All Asset Classes", value: "ALL" },
              { label: "Equity", value: "EQUITY" },
              { label: "ETF", value: "ETF" },
              { label: "Commodity", value: "COMMODITY" },
              { label: "Fixed Income", value: "FIXED_INCOME" },
              { label: "FX", value: "FX" },
              { label: "Crypto", value: "CRYPTO" },
            ],
          },
          {
            id: "filter-lifecycle-status",
            name: "lifecycle_status",
            label: "Lifecycle Status",
            defaultValue: lifecycleStatus ?? "ALL",
            options: [
              { label: "All Statuses", value: "ALL" },
              { label: "Active", value: "ACTIVE" },
              { label: "Delisted", value: "DELISTED" },
              { label: "Suspended", value: "SUSPENDED" },
              { label: "Halted", value: "HALTED" },
            ],
          },
        ]}
        resetHref="/instruments"
        ariaLabel="Instrument Discovery Filters"
      >
        <SearchField
          id="instrument-search"
          name="query"
          defaultValue={query ?? ""}
          placeholder="Symbol or ID (e.g. SPY, AAPL, DEMO)..."
          label="Search Instruments"
        />
      </FilterBar>

      <div className="workspace-split-layout">
        {/* Left Column: Instruments Table */}
        <section aria-label="Discovered Instruments List">
          <article className="panel">
            <h2>
              <span>Discovered Instruments</span>
              <span className="badge tabular-num">{items.length} returned</span>
            </h2>

            {items.length > 0 ? (
              <>
                <DataTable caption="Canonical Instruments Table" ariaLabel="Canonical Instruments">
                  <thead>
                    <tr>
                      <th scope="col">Instrument</th>
                      <th scope="col">Lifecycle</th>
                      <th scope="col">Validity (UTC)</th>
                      <th scope="col">Dataset</th>
                      <th scope="col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const isSelected = item.instrument_id === selectedId;
                      const inspectParams = new URLSearchParams();
                      if (query) inspectParams.set("query", query);
                      if (assetClass && assetClass !== "ALL") inspectParams.set("asset_class", assetClass);
                      if (lifecycleStatus && lifecycleStatus !== "ALL") inspectParams.set("lifecycle_status", lifecycleStatus);
                      if (offset > 0) inspectParams.set("offset", String(offset));
                      inspectParams.set("selected", item.instrument_id);

                      return (
                        <tr key={item.instrument_id} className={isSelected ? "row-selected" : ""}>
                          <td>
                            <InstrumentIdentity
                              symbol={item.canonical_symbol}
                              venue={item.venue}
                              assetClass={item.asset_class}
                              syntheticDemo={item.synthetic_demo}
                              ambiguousMapping={item.ambiguous_mapping}
                            />
                            <code className="inspector-id-code">{item.instrument_id}</code>
                          </td>
                          <td>
                            <QualityStateBadge status={item.lifecycle_status} />
                          </td>
                          <td>
                            <small>
                              {utc(item.valid_from)}
                              {item.valid_until ? ` → ${utc(item.valid_until)}` : " → Present"}
                            </small>
                          </td>
                          <td>
                            <DatasetVersionBadge version={item.latest_dataset_version} />
                          </td>
                          <td className="cell-action">
                            <Link
                              href={`/instruments?${inspectParams.toString()}`}
                              className="table-link"
                              aria-label={`Inspect ${item.canonical_symbol}`}
                            >
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
                    basePath="/instruments"
                    searchParams={{
                      query: query ?? "",
                      asset_class: assetClass ?? "",
                      lifecycle_status: lifecycleStatus ?? "",
                      selected: selectedId ?? "",
                    }}
                  />
                )}
              </>
            ) : (
              <p className="empty-state">{stateText(discoveryResult)}</p>
            )}
          </article>
        </section>

        {/* Right Column: Deep Instrument Inspector */}
        <aside aria-label="Instrument Detail Inspector">
          {selectedDetail ? (
            <div className="inspector-card">
              <header className="inspector-header">
                <div className="inspector-title-group">
                  <div className="inspector-title-row">
                    <h2>{selectedDetail.canonical_symbol}</h2>
                    {selectedDetail.synthetic_demo && <span className="demo-mini-tag">DEMO</span>}
                    {selectedDetail.ambiguous_mapping && <span className="warning-mini-tag">AMBIGUOUS</span>}
                    <QualityStateBadge status={selectedDetail.lifecycle_status} />
                  </div>
                  <code className="inspector-id-code">{selectedDetail.instrument_id}</code>
                </div>
              </header>

              {/* Canonical Specifications */}
              <div className="inspector-section inspector-section-first">
                <span className="inspector-section-title">Canonical Specifications</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "asset_class", label: "Asset Class", value: selectedDetail.asset_class },
                    { key: "instrument_type", label: "Type", value: selectedDetail.instrument_type },
                    { key: "exchange", label: "Exchange / Venue", value: `${selectedDetail.exchange_name} (${selectedDetail.venue})` },
                    { key: "mic", label: "MIC Code", value: selectedDetail.mic ?? "N/A" },
                    { key: "currency", label: "Base / Quote", value: `${selectedDetail.base_currency} / ${selectedDetail.quote_currency}` },
                    { key: "settlement", label: "Settlement", value: selectedDetail.settlement_currency },
                    { key: "lot_size", label: "Lot Size", value: selectedDetail.lot_size },
                    { key: "tick_size", label: "Tick Size", value: selectedDetail.tick_size },
                    { key: "precision", label: "Price / Qty Decimals", value: `${selectedDetail.price_precision} / ${selectedDetail.quantity_precision}` },
                    { key: "timezone", label: "Trading Timezone", value: selectedDetail.trading_timezone },
                    { key: "isin", label: "ISIN", value: selectedDetail.isin ?? "N/A" },
                    { key: "cusip", label: "CUSIP", value: selectedDetail.cusip ?? "N/A" },
                    { key: "registered_at", label: "Registered At (UTC)", value: <time dateTime={selectedDetail.registered_at}>{utc(selectedDetail.registered_at)}</time> },
                  ]}
                />
              </div>

              {/* Identifier Mappings */}
              <div className="inspector-section">
                <span className="inspector-section-title">
                  Identifier Mappings ({selectedDetail.identifier_mappings.length})
                </span>
                {selectedDetail.identifier_mappings.length > 0 ? (
                  <DataTable caption="Identifier Mappings Table">
                    <thead>
                      <tr>
                        <th scope="col">Namespace</th>
                        <th scope="col">Value</th>
                        <th scope="col">Valid (UTC)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedDetail.identifier_mappings.map((m) => (
                        <tr key={m.mapping_id}>
                          <td><strong>{m.namespace}</strong></td>
                          <td><code>{m.value}</code></td>
                          <td>
                            <small>{utc(m.valid_from)} &rarr; {m.valid_until ? utc(m.valid_until) : "Present"}</small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </DataTable>
                ) : (
                  <p className="empty-notice">No external identifier mappings recorded.</p>
                )}
              </div>

              {/* Symbol Mappings */}
              <div className="inspector-section">
                <span className="inspector-section-title">
                  Venue Symbol Mappings ({selectedDetail.symbol_mappings.length})
                </span>
                {selectedDetail.symbol_mappings.length > 0 ? (
                  <DataTable caption="Venue Symbol Mappings Table">
                    <thead>
                      <tr>
                        <th scope="col">Venue</th>
                        <th scope="col">Symbol</th>
                        <th scope="col">Valid (UTC)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedDetail.symbol_mappings.map((m) => (
                        <tr key={m.mapping_id}>
                          <td><code>{m.venue}</code></td>
                          <td><strong>{m.symbol}</strong></td>
                          <td>
                            <small>{utc(m.valid_from)} &rarr; {m.valid_until ? utc(m.valid_until) : "Present"}</small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </DataTable>
                ) : (
                  <p className="empty-notice">No venue symbol mappings recorded.</p>
                )}
              </div>

              {/* Lifecycle Events */}
              <div className="inspector-section">
                <span className="inspector-section-title">
                  Lifecycle Events ({selectedDetail.lifecycle_events.length})
                </span>
                {selectedDetail.lifecycle_events.length > 0 ? (
                  <DataTable caption="Instrument Lifecycle Events Table">
                    <thead>
                      <tr>
                        <th scope="col">Status</th>
                        <th scope="col">Effective (UTC)</th>
                        <th scope="col">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedDetail.lifecycle_events.map((e) => (
                        <tr key={e.event_id}>
                          <td><QualityStateBadge status={e.status} /></td>
                          <td><time dateTime={e.effective_at}>{utc(e.effective_at)}</time></td>
                          <td>{e.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </DataTable>
                ) : (
                  <p className="empty-notice">No lifecycle events recorded.</p>
                )}
              </div>

              {/* Associated Dataset Versions */}
              <div className="inspector-section">
                <span className="inspector-section-title">
                  Associated Dataset Versions ({selectedDetail.dataset_versions.length})
                </span>
                {selectedDetail.dataset_versions.length > 0 ? (
                  <div className="badge-wrap-group">
                    {selectedDetail.dataset_versions.map((ver) => (
                      <DatasetVersionBadge key={ver} version={ver} />
                    ))}
                  </div>
                ) : (
                  <p className="empty-notice">No associated sealed datasets.</p>
                )}
              </div>

              <ProvenancePanel
                source="PostgresOperatorDashboardQueries: professional_instruments"
                recordId={selectedDetail.instrument_id}
                version="instruments-v1"
                asOf={ctx.evidenceTime}
                limitations={[
                  "Ambiguous symbol lookups are rejected at boundary.",
                  "Zero broker or trading execution actions available.",
                ]}
              />
            </div>
          ) : (
            <div className="inspector-card">
              <p className="empty-state">Select an instrument from the table to inspect details.</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
