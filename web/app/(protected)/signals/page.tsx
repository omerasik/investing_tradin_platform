import { getWorkspaceContext, getSignalDiscovery, stateText, utc } from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { FilterBar } from "../../components/filter-bar";
import { SearchField } from "../../components/search-field";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { ResearchStatusBadge } from "../../components/research-status-badge";
import { SignalLifecycleTimeline } from "../../components/signal-lifecycle-timeline";
import { ContradictionPanel } from "../../components/contradiction-panel";
import { SafetyBanner } from "../../components/safety-banner";

export const dynamic = "force-dynamic";

export default async function SignalsPage({
  searchParams,
}: {
  searchParams?: Promise<{
    status?: string;
    instrument?: string;
    strategy_version?: string;
    offset?: string;
    limit?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const status = resolvedParams?.status?.trim() || undefined;
  const instrument = resolvedParams?.instrument?.trim() || undefined;
  const strategyVersion = resolvedParams?.strategy_version?.trim() || undefined;
  const offset = Number(resolvedParams?.offset ?? 0) || 0;
  const limit = Number(resolvedParams?.limit ?? 20) || 20;

  const ctx = await getWorkspaceContext();
  const signals = await getSignalDiscovery(ctx, { status, instrument, strategy_version: strategyVersion, limit, offset });
  const signalData = signals.state === "AVAILABLE" ? signals.value : undefined;
  const items = signalData?.items ?? [];

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Signal Explorer"
        subtitle="Point-in-time reasoned signal lifecycles, evidence classification, and contradicting evidence."
        status={signals.state}
        asOf={signalData?.as_of ?? ctx.evidenceTime}
      />

      <SafetyBanner message="SIGNAL ≠ ORDER. NO EXECUTION AUTHORITY. No control on this page can place orders, activate strategies, or contact brokers." />

      <FilterBar
        groups={[
          {
            id: "filter-signal-status",
            name: "status",
            label: "Status",
            defaultValue: status ?? "ALL",
            options: [
              { label: "All Statuses", value: "ALL" },
              { label: "Candidate", value: "CANDIDATE" },
              { label: "Validated", value: "VALIDATED" },
              { label: "Blocked By Risk", value: "BLOCKED_BY_RISK" },
              { label: "Blocked By Data", value: "BLOCKED_BY_DATA" },
              { label: "Waiting For Entry", value: "WAITING_FOR_ENTRY" },
              { label: "Active", value: "ACTIVE" },
              { label: "Partially Filled", value: "PARTIALLY_FILLED" },
              { label: "Filled", value: "FILLED" },
              { label: "Invalidated", value: "INVALIDATED" },
              { label: "Expired", value: "EXPIRED" },
              { label: "Closed", value: "CLOSED" },
              { label: "Cancelled", value: "CANCELLED" },
            ],
          },
        ]}
        resetHref="/signals"
        ariaLabel="Signal Discovery Filters"
      >
        <SearchField id="signal-instrument-search" name="instrument" defaultValue={instrument ?? ""} placeholder="Instrument ID..." label="Filter by Instrument" />
      </FilterBar>

      <article className="panel">
        <h2>
          <span>Signal Lifecycle Authority</span>
          <span className="badge tabular-num">{items.length} returned</span>
        </h2>

        {items.length > 0 ? (
          <>
            <DataTable caption={`Signal lifecycle as of ${utc(signalData?.as_of)}`} ariaLabel="Signal Lifecycle">
              <thead>
                <tr>
                  <th scope="col">Instrument / Strategy</th>
                  <th scope="col">Direction / Status</th>
                  <th scope="col">Expiry</th>
                  <th scope="col">Metrics</th>
                  <th scope="col">Evidence</th>
                  <th scope="col">Created / Expires</th>
                  <th scope="col">Latest Reason</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.signal_id}>
                    <td>
                      <strong>{item.instrument}</strong>
                      <br />
                      <small>{item.strategy_version}</small>
                      <br />
                      <code className="inspector-id-code">{item.signal_id}</code>
                    </td>
                    <td>
                      {item.direction}
                      <br />
                      <StatusBadge status={item.status} />
                    </td>
                    <td>
                      <StatusBadge status={item.expiry_state} />
                    </td>
                    <td>
                      Strength: <strong>{item.strength}</strong>
                      <br />
                      Confidence: <strong>{item.confidence}</strong>
                      <br />
                      Quality: <strong>{item.data_quality_score}</strong>
                    </td>
                    <td>
                      <ResearchStatusBadge classification={item.evidence_classification} />
                    </td>
                    <td>
                      <small>
                        {utc(item.created_at)}
                        <br />
                        {utc(item.expires_at)}
                      </small>
                    </td>
                    <td>
                      <strong>{item.latest_reason}</strong>
                      <div>{item.explanation}</div>
                      <div>Passed: {item.passed_stages.join(", ") || "None"}</div>
                      <div>Failed: {item.failed_stages.join(", ") || "None"}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </DataTable>

            {signalData && (
              <Pagination
                limit={signalData.page.limit}
                offset={signalData.page.offset}
                returned={signalData.page.returned}
                hasMore={signalData.page.has_more}
                basePath="/signals"
                searchParams={{ status: status ?? "", instrument: instrument ?? "", strategy_version: strategyVersion ?? "" }}
              />
            )}

            {items.map((item) => (
              <div key={item.signal_id} className="margin-top-16">
                <ContradictionPanel items={item.contradicting_evidence} />
                <SignalLifecycleTimeline signalId={item.signal_id} events={item.lifecycle} />
              </div>
            ))}
          </>
        ) : (
          <p className="empty-state">
            {signalData ? `${signalData.state}: no signal matched this bounded point-in-time scope.` : stateText(signals)}
          </p>
        )}

        <span className="status margin-top-16 align-self-start">
          RESEARCH / PAPER ONLY / READ ONLY / NO AUTOMATIC AUTHORITY
        </span>
      </article>
    </div>
  );
}
