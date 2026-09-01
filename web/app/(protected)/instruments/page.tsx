import { getWorkspaceContext, getInstrumentDiscovery, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";

export const dynamic = "force-dynamic";

export default async function InstrumentsPage() {
  const ctx = await getWorkspaceContext();
  const instruments = await getInstrumentDiscovery(ctx);
  const rows = instruments.state === "AVAILABLE" ? instruments.value.items : [];

  return (
    <>
      <PageHeader
        eyebrow="MARKET & DATA WORKSPACE"
        title="Instrument Workstation"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Canonical, point-in-time instrument discovery. Ambiguous symbols are explicitly displayed. Dedicated asset class filtering and deep symbol mapping view arriving in Module 2B.
      </div>

      <article className="panel">
        <h2>
          <span>Discovered Instruments</span>
          <StatusBadge status={instruments.state} />
        </h2>

        {rows.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Symbol / ID</th>
                <th>Asset Class / Venue</th>
                <th>Lifecycle Status</th>
                <th>Validity (UTC)</th>
                <th>Dataset Version</th>
                <th>Evidence Classification</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr key={item.instrument_id}>
                  <td>
                    <strong>{item.canonical_symbol}</strong>
                    <br />
                    <code>{item.instrument_id}</code>
                  </td>
                  <td>
                    {item.asset_class} &bull; {item.venue}
                  </td>
                  <td>
                    <StatusBadge status={item.lifecycle_status} />
                  </td>
                  <td>
                    {utc(item.valid_from)} &rarr; {utc(item.valid_until)}
                  </td>
                  <td>{item.latest_dataset_version ?? "UNAVAILABLE"}</td>
                  <td>
                    {item.synthetic_demo ? "SYNTHETIC / DEMO" : "AUTHORITATIVE"}
                    <br />
                    <small>
                      Mappings: {item.identifier_mapping_count} &bull;{" "}
                      {item.ambiguous_mapping ? "AMBIGUOUS (not selected)" : "Unambiguous"}
                    </small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty-state">{stateText(instruments)}</p>
        )}

        <span className="status margin-top-16 align-self-start">READ ONLY</span>
      </article>
    </>
  );
}
