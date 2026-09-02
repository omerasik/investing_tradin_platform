import { utc } from "../lib/data-access";

export type SignalLifecycleEvent = {
  event_id: string;
  from_status: string;
  to_status: string;
  actor: string;
  reason: string;
  evidence_references: string[];
  occurred_at: string;
};

export function SignalLifecycleTimeline({
  signalId,
  events,
}: {
  signalId: string;
  events: SignalLifecycleEvent[];
}) {
  return (
    <details className="provenance" aria-label={`Lifecycle timeline for ${signalId}`}>
      <summary>Lifecycle Timeline for {signalId}</summary>
      {events.length > 0 ? (
        <ol>
          {events.map((event) => (
            <li key={event.event_id}>
              <strong>{event.from_status}</strong> &rarr; <strong>{event.to_status}</strong> at{" "}
              <time dateTime={event.occurred_at}>{utc(event.occurred_at)}</time> by {event.actor}: {event.reason};
              evidence {event.evidence_references.join(", ") || "none"}
            </li>
          ))}
        </ol>
      ) : (
        <p className="empty-notice">No lifecycle events recorded.</p>
      )}
    </details>
  );
}
