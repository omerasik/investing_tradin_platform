import { getWorkspaceContext, getNewsEvidence, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";

export const dynamic = "force-dynamic";

export default async function NewsPage() {
  const ctx = await getWorkspaceContext();
  const news = await getNewsEvidence(ctx);
  const data = news.state === "AVAILABLE" ? news.value : undefined;

  return (
    <>
      <PageHeader
        eyebrow="INVESTING & INTELLIGENCE WORKSPACE"
        title="News &amp; Event Intelligence"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Persisted correction-aware research events. Never presented as a live feed and cannot create orders. Real-time NLP entity clustering arriving in Module 2B.
      </div>

      <article className="panel">
        <h2>
          <span>Persisted News Events</span>
          <StatusBadge status={data?.provider_state ?? news.state} />
        </h2>
        <p>Persisted correction-aware research evidence is never presented as a live feed and cannot create an order.</p>
        <p className="warning">
          {data?.provider_state ?? "EXTERNAL_BLOCKED"}
          {data?.provider_state === "EXTERNAL_BLOCKED"
            ? ": no external provider is authorized or activated."
            : ""}
        </p>

        {data?.items.length ? (
          data.items.map((item) => (
            <section key={item.event_id} aria-labelledby={`news-detail-${item.event_id}`} className="news-item-section">
              <h3 id={`news-detail-${item.event_id}`}>{item.headline}</h3>
              <dl>
                <dt>Source / Rights</dt>
                <dd>
                  <strong>{item.source}</strong> &bull; Terms: {item.source_terms_version} &bull; Rights: {item.rights_state} &bull; Provider Active: {item.provider_activated ? "YES" : "NO"}
                </dd>
                <dt>Published / Ingested (UTC)</dt>
                <dd>
                  Pub: {utc(item.published_at)} &bull; Ingested: {utc(item.ingested_at)}
                  {item.correction_or_retraction_at ? ` \u2022 Corrected: ${utc(item.correction_or_retraction_at)}` : ""}
                </dd>
                <dt>Novelty / Urgency</dt>
                <dd>
                  Novelty: {item.novelty} &bull; Urgency: {item.urgency} &bull; Horizon: {item.horizon} &bull; Uncertainty: {item.uncertainty}
                </dd>
                <dt>Revision &amp; Chain</dt>
                <dd>
                  {item.revision_kind} #{item.revision} &bull;{" "}
                  {item.correction_chain.map((c) => `${c.relation} ${c.predecessor_id}\u2192${c.successor_id}`).join("; ") || "No revision chain"}
                </dd>
                <dt>Linked Entities</dt>
                <dd>
                  {item.entities.map((e) => `${e.instrument} (${e.method}: ${e.confidence})`).join(", ") || "None"}
                </dd>
                <dt>Fingerprint / Provenance</dt>
                <dd>
                  <code>{item.content_fingerprint}</code> &bull; {item.provenance_reference}
                </dd>
              </dl>
            </section>
          ))
        ) : (
          <p className="empty-state">
            {data
              ? `${data.state}: no persisted event matched this scope.`
              : stateText(news)}
          </p>
        )}

        <span className="status margin-top-16 align-self-start">
          {data?.provider_state ?? "EXTERNAL_BLOCKED"} / READ ONLY / NOT LIVE NEWS
        </span>
      </article>
    </>
  );
}
