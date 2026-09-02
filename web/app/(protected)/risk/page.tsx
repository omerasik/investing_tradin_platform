import Link from "next/link";
import { getWorkspaceContext, getRiskDecisions, stateText, utc } from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { FilterBar } from "../../components/filter-bar";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";

export const dynamic = "force-dynamic";

function sumReservedNotional(items: { reserved_notional: string | null }[]): string {
  const values = items.map((item) => item.reserved_notional).filter((value): value is string => value !== null);
  if (values.length === 0) return "UNAVAILABLE";
  const total = values.reduce((acc, value) => acc + Number(value), 0);
  return Number.isFinite(total) ? String(total) : "UNAVAILABLE";
}

export default async function RiskPage({
  searchParams,
}: {
  searchParams?: Promise<{
    approved?: string; has_reservation?: string; account_id?: string; policy_version_id?: string;
    business_date?: string; selected?: string; offset?: string; limit?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const approvedFilter = resolvedParams?.approved?.trim();
  const hasReservationFilter = resolvedParams?.has_reservation?.trim();
  const accountId = resolvedParams?.account_id?.trim() || undefined;
  const policyVersionId = resolvedParams?.policy_version_id?.trim() || undefined;
  const businessDate = resolvedParams?.business_date?.trim() || undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 20) || 20;

  const ctx = await getWorkspaceContext();
  const riskResult = await getRiskDecisions(ctx, {
    approved: approvedFilter === "true" ? true : approvedFilter === "false" ? false : undefined,
    has_reservation: hasReservationFilter === "true" ? true : hasReservationFilter === "false" ? false : undefined,
    account_id: accountId, policy_version_id: policyVersionId, business_date: businessDate, limit, offset,
  });
  const riskPage = riskResult.state === "AVAILABLE" ? riskResult.value : undefined;
  const items = riskPage?.items ?? [];

  const selectedId = resolvedParams?.selected || (items.length > 0 ? items[0].risk_decision_id : undefined);
  const selected = items.find((item) => item.risk_decision_id === selectedId);

  const approvedCount = items.filter((item) => item.approved).length;
  const rejectedCount = items.length - approvedCount;
  const reservationCount = items.filter((item) => item.reservation_id !== null).length;
  const reservedNotionalSum = sumReservedNotional(items);
  const latestDecisionAt = items[0]?.decided_at;

  const baseFilters = {
    approved: approvedFilter ?? "", has_reservation: hasReservationFilter ?? "",
    account_id: accountId ?? "", policy_version_id: policyVersionId ?? "", business_date: businessDate ?? "",
    selected: selectedId ?? "",
  };

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Risk Workspace"
        subtitle="Immutable risk-policy, decision and reservation evidence. Deterministic policy evaluation only -- no override, no release, no execution authority."
        status={riskResult.state}
        asOf={ctx.evidenceTime}
      />

      <section aria-label="Risk Summary" className="panel">
        <h2>Risk Summary (current page)</h2>
        <KeyValueGrid
          columns={4}
          items={[
            { key: "total", label: "Decisions Returned", value: items.length },
            { key: "approved", label: "Approved", value: approvedCount },
            { key: "rejected", label: "Rejected", value: rejectedCount },
            { key: "reservations", label: "Reservations Present", value: reservationCount },
            { key: "notional", label: "Reserved Notional (sum, page-scoped)", value: reservedNotionalSum },
            { key: "latest", label: "Latest Decision (UTC)", value: latestDecisionAt ? utc(latestDecisionAt) : "UNAVAILABLE" },
          ]}
        />
        <p className="empty-notice">Presentation-level aggregates over the current bounded page only; not a new authoritative risk metric.</p>
      </section>

      <FilterBar
        groups={[
          {
            id: "filter-risk-approved", name: "approved", label: "Outcome",
            defaultValue: approvedFilter ?? "ALL",
            options: [
              { label: "All Outcomes", value: "ALL" },
              { label: "Approved", value: "true" },
              { label: "Rejected", value: "false" },
            ],
          },
          {
            id: "filter-risk-reservation", name: "has_reservation", label: "Reservation",
            defaultValue: hasReservationFilter ?? "ALL",
            options: [
              { label: "All Reservation States", value: "ALL" },
              { label: "Reservation Present", value: "true" },
              { label: "No Reservation", value: "false" },
            ],
          },
        ]}
        resetHref="/risk"
        ariaLabel="Risk Decision Filters"
      >
        <div className="filter-item">
          <label htmlFor="filter-risk-account" className="filter-label">Account</label>
          <input id="filter-risk-account" name="account_id" type="text" defaultValue={accountId ?? ""} className="filter-select" />
        </div>
        <div className="filter-item">
          <label htmlFor="filter-risk-policy-version" className="filter-label">Policy Version ID</label>
          <input id="filter-risk-policy-version" name="policy_version_id" type="text" defaultValue={policyVersionId ?? ""} className="filter-select" />
        </div>
        <div className="filter-item">
          <label htmlFor="filter-risk-business-date" className="filter-label">Business Date</label>
          <input id="filter-risk-business-date" name="business_date" type="date" defaultValue={businessDate ?? ""} className="filter-select" />
        </div>
      </FilterBar>

      <div className="workspace-split-layout">
        <section aria-label="Risk Decision Ledger">
          <article className="panel">
            <h2>
              <span>Risk Decision Ledger</span>
              <span className="badge tabular-num">{items.length} returned</span>
            </h2>

            {items.length > 0 ? (
              <>
                <DataTable caption="Risk Decisions Table" ariaLabel="Risk Decisions">
                  <thead>
                    <tr>
                      <th scope="col">Outcome</th>
                      <th scope="col">Policy</th>
                      <th scope="col">Account</th>
                      <th scope="col">Reserved Notional</th>
                      <th scope="col">Business Date</th>
                      <th scope="col">Reasons</th>
                      <th scope="col">Decided (UTC)</th>
                      <th scope="col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const isSelected = item.risk_decision_id === selectedId;
                      const inspectParams = new URLSearchParams();
                      for (const [key, value] of Object.entries(baseFilters)) {
                        if (value && key !== "selected") inspectParams.set(key, value);
                      }
                      if (offset > 0) inspectParams.set("offset", String(offset));
                      inspectParams.set("selected", item.risk_decision_id);
                      return (
                        <tr key={item.risk_decision_id} className={isSelected ? "row-selected" : ""}>
                          <td><StatusBadge status={item.approved ? "APPROVED" : "REJECTED"} /></td>
                          <td>
                            {item.policy_name} / {item.policy_version}
                          </td>
                          <td>{item.account_id ?? "UNAVAILABLE"}</td>
                          <td>{item.reserved_notional ?? "NO RESERVATION"}</td>
                          <td>{item.business_date ?? "UNAVAILABLE"}</td>
                          <td><small>{item.reasons.join("; ") || "none recorded"}</small></td>
                          <td><small>{utc(item.decided_at)}</small></td>
                          <td className="cell-action">
                            <Link href={`/risk?${inspectParams.toString()}`} className="table-link" aria-label={`Inspect risk decision ${item.risk_decision_id}`}>
                              Inspect &rarr;
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </DataTable>

                {riskPage && (
                  <Pagination
                    limit={riskPage.page.limit}
                    offset={riskPage.page.offset}
                    returned={riskPage.page.returned}
                    hasMore={riskPage.page.has_more}
                    basePath="/risk"
                    searchParams={baseFilters}
                  />
                )}
              </>
            ) : (
              <p className="empty-state">{stateText(riskResult)}</p>
            )}
          </article>
        </section>

        <aside aria-label="Risk Decision Inspector">
          {selected ? (
            <div className="inspector-card">
              <header className="inspector-header">
                <div className="inspector-title-group">
                  <div className="inspector-title-row">
                    <h2>{selected.policy_name}</h2>
                    <StatusBadge status={selected.approved ? "APPROVED" : "REJECTED"} />
                  </div>
                  <code className="inspector-id-code">{selected.risk_decision_id}</code>
                </div>
              </header>

              <div className="inspector-section inspector-section-first">
                <span className="inspector-section-title">Decision Identity</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "decision", label: "Risk Decision ID", value: <code>{selected.risk_decision_id}</code> },
                    { key: "intent", label: "Intent ID", value: <code>{selected.intent_id}</code> },
                    { key: "decided", label: "Decided At (UTC)", value: utc(selected.decided_at) },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Policy Identity</span>
                <KeyValueGrid
                  columns={2}
                  items={[
                    { key: "policy-version-id", label: "Policy Version ID", value: <code>{selected.policy_version_id}</code> },
                    { key: "policy-name", label: "Policy Name", value: selected.policy_name },
                    { key: "policy-version", label: "Version", value: selected.policy_version },
                    { key: "policy-hash", label: "Content Hash", value: <code>{selected.policy_content_hash}</code> },
                  ]}
                />
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Policy Limits</span>
                {Object.keys(selected.policy_limits).length > 0 ? (
                  <KeyValueGrid
                    columns={2}
                    items={Object.entries(selected.policy_limits).map(([key, value]) => ({
                      key, label: key, value: String(value),
                    }))}
                  />
                ) : (
                  <p className="empty-state">UNAVAILABLE</p>
                )}
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Outcome &amp; Reasons</span>
                <p><StatusBadge status={selected.approved ? "APPROVED" : "REJECTED"} /></p>
                <ul>
                  {selected.reasons.length > 0 ? (
                    selected.reasons.map((reason, index) => <li key={index}>{reason}</li>)
                  ) : (
                    <li>No reasons recorded.</li>
                  )}
                </ul>
              </div>

              <div className="inspector-section">
                <span className="inspector-section-title">Reservation</span>
                {selected.reservation_id ? (
                  <KeyValueGrid
                    columns={2}
                    items={[
                      { key: "reservation-id", label: "Reservation ID", value: <code>{selected.reservation_id}</code> },
                      { key: "reservation-account", label: "Account", value: selected.account_id ?? "UNAVAILABLE" },
                      { key: "reservation-date", label: "Business Date", value: selected.business_date ?? "UNAVAILABLE" },
                      { key: "reservation-notional", label: "Reserved Notional", value: selected.reserved_notional ?? "UNAVAILABLE" },
                      { key: "reservation-created", label: "Created At (UTC)", value: selected.reservation_created_at ? utc(selected.reservation_created_at) : "UNAVAILABLE" },
                    ]}
                  />
                ) : (
                  <p className="empty-state">NO RESERVATION</p>
                )}
              </div>

              <div className="inspector-section inspector-section-warning">
                <span className="inspector-section-title">Boundary</span>
                <p>RESEARCH / PAPER ONLY</p>
                <p>NO AUTOMATIC AUTHORITY</p>
                <p>NO RISK OVERRIDE</p>
              </div>

              <ProvenancePanel
                source="PostgreSQL Risk Decision Authority"
                recordId={selected.risk_decision_id}
                version={selected.policy_version}
                contentHash={selected.policy_content_hash}
                asOf={selected.decided_at}
                limitations={["Risk decisions and reservations are immutable read-only evidence; this workspace cannot evaluate, override, release, or reserve."]}
              />
            </div>
          ) : (
            <div className="inspector-card">
              <p className="empty-state">
                {items.length > 0 ? "Select a decision from the ledger to inspect details." : stateText(riskResult)}
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
