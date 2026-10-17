import React from "react";
import { utc, type TerminalCandidate, type TerminalWalkForward } from "../../lib/data-access";
import { DataTable } from "../../components/data-table";
import { paperEvidenceHref } from "./compare-section";

/** Owner-gated blockers carry this prefix; everything else is evidence or engineering. */
const OWNER_PREFIX = "BLOCKED_OWNER_DECISION_";

function short(hash: string | null): React.ReactNode {
  return hash ? <code title={hash}>{hash.slice(0, 10)}…</code> : "—";
}

function Badge({ label }: { label: string }) {
  return <span className="badge" data-claim={label}>{label}</span>;
}

function trialHref(studyId: string, trialId: string): string {
  return `/strategy-lab?${new URLSearchParams({ study: studyId, trial: trialId }).toString()}#trial-detail`;
}

/** Recorded walk-forward reports. Every one is development evidence on the search tier, gross: never validation. */
export function WalkForwardSection({ reports, unavailable }: { reports: TerminalWalkForward[] | undefined; unavailable: string }) {
  if (!reports) return <p className="empty-notice">{unavailable}</p>;
  if (!reports.length) return <p className="empty-notice">No walk-forward report recorded.</p>;
  return (
    <DataTable caption="Walk-forward reports (development evidence, not validation)" ariaLabel="Walk-forward reports">
      <thead><tr><th scope="col">Study</th><th scope="col">Report</th><th scope="col">Folds</th><th scope="col">Trials tested</th><th scope="col">Ends before</th><th scope="col">Claim</th></tr></thead>
      <tbody>
        {reports.map((report) => (
          <tr key={report.report_hash} data-walk-forward={report.report_hash}>
            <td><a href={`/strategy-lab?study=${report.study_id}`}>{report.study_label}</a></td>
            <td>{short(report.report_hash)}</td>
            <td className="tabular-num">{report.folds.length} ({report.folds.filter((fold) => fold.selected_trial_id).length} with a selection)</td>
            <td className="tabular-num">{report.multiple_testing_trial_count}</td>
            <td><time dateTime={report.evaluation_end_exclusive}>{utc(report.evaluation_end_exclusive)}</time></td>
            <td><Badge label={report.evaluation_class} /> <Badge label={report.numeric_tier} /> <Badge label={report.cost_mode} /></td>
          </tr>
        ))}
      </tbody>
    </DataTable>
  );
}

function ExitCriteria({ exit }: { exit: TerminalCandidate["exit_criteria"] }) {
  if (exit.status === "AUTHORIZED") {
    return <><Badge label="AUTHORIZED" /> by {exit.authorized_by} on {exit.authorized_on}</>;
  }
  if (exit.status === "DRAFT") {
    return <><Badge label="DRAFT" /> not authorized — every threshold UNRESOLVED ({exit.unresolved.length} owner values missing)</>;
  }
  if (exit.status === "REFUSED") return <><Badge label="REFUSED" /> {exit.refusals.join(", ")}</>;
  return <><Badge label="NOT_RECORDED" /> no exit criteria recorded (OR-7)</>;
}

/** Per-candidate readiness and exit criteria, with the identities that link the chain. Read-only. */
export function CandidateLifecycleSection({ candidates, unavailable }: { candidates: TerminalCandidate[] | undefined; unavailable: string }) {
  if (!candidates) return <p className="empty-notice">{unavailable}</p>;
  if (!candidates.length) return <p className="empty-notice">No candidate is named by a Decimal selection, a recorded state or exit criteria yet.</p>;
  return (
    <>
      <DataTable caption="Candidate readiness (an assessment, never a decision)" ariaLabel="Candidate readiness">
        <thead><tr><th scope="col">Candidate</th><th scope="col">Claim</th><th scope="col">Readiness stage</th><th scope="col">Waiting on owner</th><th scope="col">Exit criteria</th><th scope="col">Economics</th><th scope="col">Next</th></tr></thead>
        <tbody>
          {candidates.map((item) => (
            <tr key={`${item.links.study_id}-${item.links.trial_id}`} data-candidate={item.links.trial_id}>
              <td>{item.links.study_label} · <a href={trialHref(item.links.study_id, item.links.trial_id)}><code title={item.links.trial_id}>{item.links.trial_id.slice(0, 8)}…</code></a></td>
              <td><Badge label={item.claim} /></td>
              <td><Badge label={item.readiness.stage} /></td>
              <td>{item.readiness.owner_gates.length ? item.readiness.owner_gates.join(" · ") : "—"}</td>
              <td><ExitCriteria exit={item.exit_criteria} /></td>
              <td><Badge label={item.economics} /></td>
              <td><a href={paperEvidenceHref(item.links.study_id, item.links.trial_id)}>Paper evidence</a></td>
            </tr>
          ))}
        </tbody>
      </DataTable>
      {candidates.map((item) => {
        const owner = item.readiness.blockers.filter((blocker) => blocker.startsWith(OWNER_PREFIX));
        const evidence = item.readiness.blockers.filter((blocker) => !blocker.startsWith(OWNER_PREFIX));
        return (
          <details key={`detail-${item.links.study_id}-${item.links.trial_id}`} className="margin-bottom-24" data-candidate-detail={item.links.trial_id}>
            <summary>{item.links.study_label} · {item.links.trial_id.slice(0, 8)}… — identities, blockers and exit readiness</summary>
            <p>
              Study <code>{item.links.study_id}</code> → rerun {short(item.links.rerun_hash)} → walk-forward{" "}
              {item.links.walk_forward.length ? item.links.walk_forward.map((walk) => (
                <span key={walk.report_hash}>{short(walk.report_hash)} (selected in {walk.folds_selecting_trial}/{walk.fold_count} folds) </span>
              )) : "none"}{" "}
              → preregistration {short(item.links.preregistration_hash)} {item.links.preregistration_status ? <Badge label={item.links.preregistration_status} /> : null}
              {" "}→ holdout {item.links.holdout_opened_for_this_preregistration ? "opened for it" : "not opened for it"} → validation {short(item.links.validation_hash)}
              {" "}→ exit criteria {short(item.links.criteria_hash)} ({item.links.cycle_id}).
            </p>
            {item.evidence_not_loaded.length ? (
              <p><Badge label="NOT_LOADED" /> Not derived by this read (not loaded, which is not the same as incomplete), so its blockers stay listed: {item.evidence_not_loaded.join(", ")}.</p>
            ) : null}
            <h4>Owner-gated ({owner.length})</h4>
            {owner.length ? <ul>{owner.map((blocker) => <li key={blocker}><code>{blocker}</code></li>)}</ul> : <p>None.</p>}
            <h4>Evidence still missing ({evidence.length})</h4>
            {evidence.length ? <ul>{evidence.map((blocker) => <li key={blocker}><code>{blocker}</code></li>)}</ul> : <p>None.</p>}
            <h4>Exit readiness: <Badge label={item.exit_criteria.stage} /></h4>
            <p>Forward evidence inputs: {Object.entries(item.exit_criteria.evidence_inputs).map(([name, state]) => `${name} ${state}`).join(" · ") || "—"}</p>
            {item.exit_criteria.refusals.length ? <p>Refused: {item.exit_criteria.refusals.join(", ")}</p> : null}
            <p>
              Assessed <time dateTime={item.as_of}>{utc(item.as_of)}</time>; readiness <code>{item.readiness.assessment_hash.slice(0, 12)}</code>.{" "}
              {item.readiness.claim} · {item.exit_criteria.account_activation} · {item.validated_claim}.
            </p>
          </details>
        );
      })}
    </>
  );
}
