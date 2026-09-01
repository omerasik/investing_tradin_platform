import { getWorkspaceContext, getDataHealthEvidence, getCadenceEvidence, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";
import { EvidenceMeta } from "../../components/evidence-meta";

export const dynamic = "force-dynamic";

export default async function MarketsPage() {
  const ctx = await getWorkspaceContext();
  const [dataHealth, cadence] = await Promise.all([
    getDataHealthEvidence(ctx),
    getCadenceEvidence(ctx),
  ]);

  const health = dataHealth.state === "AVAILABLE" ? dataHealth.value : undefined;
  const schedule = cadence.state === "AVAILABLE" ? cadence.value[0] : undefined;

  return (
    <>
      <PageHeader
        eyebrow="MARKET & DATA WORKSPACE"
        title="Market Overview & Providers"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Read-only provider terms, access status, and market ingestion cadences. Detailed interactive provider controls will be introduced in Module 2B.
      </div>

      <div className="grid-2col">
        <article className="panel">
          <h2>
            <span>Provider Authorization &amp; Terms</span>
            <StatusBadge status={health ? "AVAILABLE" : dataHealth.state} />
          </h2>
          <p>Provider credentials and terms never enter the browser.</p>
          <dl>
            <dt>Provider</dt>
            <dd>{health?.provider ?? "EXTERNAL_BLOCKED"}</dd>
            <dt>Authorization / terms</dt>
            <dd>
              {health
                ? "Configured record only; provider activation not asserted"
                : "EXTERNAL_BLOCKED"}
            </dd>
            <dt>Health / freshness</dt>
            <dd>
              {health
                ? `${health.healthy ? "HEALTHY" : "BLOCKING"}; checked ${utc(health.checked_at)}`
                : stateText(dataHealth)}
            </dd>
            <dt>Checkpoint / dataset version</dt>
            <dd>UNAVAILABLE</dd>
          </dl>
          <span className="status margin-top-auto">
            {health ? "READ ONLY" : "EXTERNAL_BLOCKED"}
          </span>
        </article>

        <article className="panel">
          <h2>
            <span>Ingestion Cadences</span>
            <StatusBadge status={schedule ? (schedule.overdue ? "STALE" : schedule.due ? "DUE" : "AVAILABLE") : cadence.state} />
          </h2>
          <dl>
            <dt>Account ID</dt>
            <dd>{schedule?.account_id ?? "UNAVAILABLE"}</dd>
            <dt>Provider</dt>
            <dd>{schedule?.provider ?? "UNAVAILABLE"}</dd>
            <dt>Cadence status</dt>
            <dd>
              {schedule
                ? `${schedule.overdue ? "STALE / OVERDUE" : schedule.due ? "DUE" : "CURRENT"}; last success ${utc(
                    schedule.last_successful_at,
                  )}`
                : stateText(cadence)}
            </dd>
            <dt>Approved by</dt>
            <dd>{schedule?.approved_by ?? "UNAVAILABLE"}</dd>
          </dl>
          {health ? (
            <EvidenceMeta
              source={`return-provider:${health.provider}`}
              asOf={health.checked_at}
              version="return-health-v1"
              limitations={["Dataset-level assessment is unavailable through this configured source."]}
            />
          ) : null}
          <span className="status margin-top-auto">READ ONLY</span>
        </article>
      </div>
    </>
  );
}
