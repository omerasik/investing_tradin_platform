import { getWorkspaceContext, getDataHealthEvidence, getCadenceEvidence, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";
import { EvidenceMeta } from "../../components/evidence-meta";

export const dynamic = "force-dynamic";

export default async function DataHealthPage() {
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
        title="Data Health & Ingestion"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Ingestion health verification and non-bypassable blocking controls. Detailed failure diagnostics arriving in Module 2B.
      </div>

      <div className="grid-2col">
        <article className="panel">
          <h2>
            <span>Provider Health Status</span>
            <StatusBadge status={health ? (health.healthy ? "HEALTHY" : "BLOCKING") : dataHealth.state} />
          </h2>
          <p>Blocking evidence is not bypassable from this console.</p>
          <dl>
            <dt>Assessment Target</dt>
            <dd>{health ? `return-provider:${health.provider}` : "UNAVAILABLE"}</dd>
            <dt>Health State</dt>
            <dd>
              {health
                ? health.healthy
                  ? "HEALTHY / ALLOW"
                  : "BLOCKING / REVIEW REQUIRED"
                : dataHealth.state}
            </dd>
            <dt>Consecutive Failures</dt>
            <dd>{health?.consecutive_failures.toString() ?? "UNAVAILABLE"}</dd>
            <dt>Failure Reason</dt>
            <dd>{health?.reason ?? "None reported"}</dd>
            <dt>Checked At (UTC)</dt>
            <dd>{health ? <time dateTime={health.checked_at}>{utc(health.checked_at)}</time> : "UNAVAILABLE"}</dd>
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

        <article className="panel">
          <h2>
            <span>Cadence Verification</span>
            <StatusBadge status={schedule ? (schedule.overdue ? "STALE" : schedule.due ? "DUE" : "AVAILABLE") : cadence.state} />
          </h2>
          <dl>
            <dt>Account Reference</dt>
            <dd>{schedule?.account_id ?? "UNAVAILABLE"}</dd>
            <dt>Provider Reference</dt>
            <dd>{schedule?.provider ?? "UNAVAILABLE"}</dd>
            <dt>Ingestion Status</dt>
            <dd>
              {schedule
                ? `${schedule.overdue ? "STALE / OVERDUE" : schedule.due ? "DUE FOR INGESTION" : "CURRENT"}`
                : stateText(cadence)}
            </dd>
            <dt>Last Successful Run</dt>
            <dd>{schedule?.last_successful_at ? <time dateTime={schedule.last_successful_at}>{utc(schedule.last_successful_at)}</time> : "Never"}</dd>
            <dt>Authorization</dt>
            <dd>{schedule?.approved_by ?? "UNAVAILABLE"}</dd>
          </dl>
          <span className="status margin-top-auto">READ ONLY</span>
        </article>
      </div>
    </>
  );
}
