import { getWorkspaceContext, getSignalEvidence, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";

export const dynamic = "force-dynamic";

export default async function SignalsPage() {
  const ctx = await getWorkspaceContext();
  const signals = await getSignalEvidence(ctx);
  const signalData = signals.state === "AVAILABLE" ? signals.value : undefined;

  return (
    <>
      <PageHeader
        eyebrow="RESEARCH & SIGNALS WORKSPACE"
        title="Signal Explorer"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Point-in-time reasoned signal lifecycles. No control here can place orders, activate strategies, or contact brokers. Detailed signal filters and stage diagrams arriving in Module 2B.
      </div>

      <article className="panel">
        <h2>
          <span>Signal Lifecycle Authority</span>
          <StatusBadge status={signals.state} />
        </h2>

        {signalData?.items.length ? (
          <>
            <table>
              <caption>Signal lifecycle as of {utc(signalData.as_of)}</caption>
              <thead>
                <tr>
                  <th>Signal / Instrument</th>
                  <th>Status / Expiry</th>
                  <th>Metrics (Strength/Confidence/Quality)</th>
                  <th>Validation Stages</th>
                  <th>Reason &amp; Contradictions</th>
                </tr>
              </thead>
              <tbody>
                {signalData.items.map((item) => (
                  <tr key={item.signal_id}>
                    <td>
                      <code>{item.signal_id}</code>
                      <br />
                      <strong>{item.instrument}</strong>
                      <br />
                      <small>
                        {item.strategy_version} &bull; {item.direction}
                      </small>
                    </td>
                    <td>
                      <StatusBadge status={item.status} /> &bull; <StatusBadge status={item.expiry_state} />
                      <br />
                      <small>
                        Created: {utc(item.created_at)}
                        <br />
                        Expires: {utc(item.expires_at)}
                      </small>
                    </td>
                    <td>
                      Strength: <strong>{item.strength}</strong>
                      <br />
                      Confidence: <strong>{item.confidence}</strong>
                      <br />
                      Quality: <strong>{item.data_quality_score}</strong>
                    </td>
                    <td>
                      <div>Passed: {item.passed_stages.join(", ") || "None"}</div>
                      <div>Failed: {item.failed_stages.join(", ") || "None"}</div>
                    </td>
                    <td>
                      <div>
                        <strong>{item.latest_reason}</strong>
                      </div>
                      <div>{item.explanation}</div>
                      <small>
                        Contradicting: {item.contradicting_evidence.join("; ") || "None recorded"}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {signalData.items.map((item) => (
              <details key={`${item.signal_id}-timeline`} className="provenance">
                <summary>Lifecycle Timeline for {item.signal_id}</summary>
                <ol>
                  {item.lifecycle.map((event) => (
                    <li key={event.event_id}>
                      {event.from_status} &rarr; {event.to_status} at {utc(event.occurred_at)} by{" "}
                      {event.actor}: {event.reason}; evidence {event.evidence_references.join(", ") || "none"}
                    </li>
                  ))}
                </ol>
              </details>
            ))}
          </>
        ) : (
          <p className="empty-state">
            {signalData
              ? `${signalData.state}: no signal matched this bounded point-in-time scope.`
              : stateText(signals)}
          </p>
        )}

        <span className="status margin-top-16 align-self-start">
          RESEARCH / PAPER ONLY / READ ONLY / NO AUTOMATIC AUTHORITY
        </span>
      </article>
    </>
  );
}
