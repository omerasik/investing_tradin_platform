import React from "react";
import {
  getCaptureAvailability,
  getStrategyLabStudies,
  getTerminalAccounts,
  getTerminalActivation,
  getTerminalCommands,
  getTerminalIncubation,
  getTerminalOverview,
  getTerminalReruns,
  getTerminalSignals,
  getTerminalValidation,
  getWorkspaceContext,
  stateText,
  utc,
  type ActivationReadiness,
  type ReadinessAnswer,
  type TerminalCommand,
  type TerminalCycle,
} from "../../lib/data-access";
import { CommandConsole } from "./command-console";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { DataTable } from "../../components/data-table";

export const dynamic = "force-dynamic";

/** The workflow, in order. Each step reads recorded evidence only; none of them acts. */
const STEPS = [
  ["command-center", "Command Center"],
  ["market-scan", "Market Scan"],
  ["opportunities", "Opportunities"],
  ["strategy-lab", "Strategy Lab"],
  ["compare", "Compare"],
  ["validation", "Validation"],
  ["risk-preview", "Risk Preview"],
  ["paper", "Paper"],
  ["monitoring", "Monitoring"],
] as const;

function hours(seconds: number): string {
  return `${(seconds / 3600).toFixed(2)} h`;
}

function counts(record: Record<string, number> | undefined): string {
  const entries = Object.entries(record ?? {});
  return entries.length ? entries.map(([key, value]) => `${key} ${value}`).join(" · ") : "none recorded";
}

function target(value: number): string {
  return value === 1 ? "LONG" : value === -1 ? "SHORT" : "FLAT";
}

function Claim({ label }: { label: string }) {
  return <span className="badge" data-claim={label}>{label}</span>;
}

function Step({ id, title, claim, children }: { id: string; title: string; claim?: string; children: React.ReactNode }) {
  return (
    <article className="panel margin-bottom-24" id={id} aria-label={title}>
      <h2>
        <span>{title}</span>
        {claim ? <Claim label={claim} /> : null}
      </h2>
      {children}
    </article>
  );
}

/** Operator-friendly names for the owner gates; the exact code stays visible next to them. */
const GATE_LABELS: Record<string, string> = {
  "OR-6": "Fees & slippage envelope",
  "OR-7": "Holdout & acceptance rules",
  "OR-9": "Watch list",
  "OR-11": "Paper account & risk limits",
};

function ActivationPanel({ readiness }: { readiness: ActivationReadiness }) {
  return (
    <>
      <DataTable caption={`What stops the next action — ${readiness.cycle_id}, holdout ${readiness.holdout_state}`} ariaLabel="Activation readiness">
        <thead><tr><th scope="col">Question</th><th scope="col">Answer</th><th scope="col">Waiting on owner</th><th scope="col">Next action</th></tr></thead>
        <tbody>
          {readiness.answers.map((answer) => (
            <tr key={answer.key} data-readiness={answer.key}>
              <td>{answer.question}</td>
              <td><Claim label={answer.status} /></td>
              <td>{answer.owner_gates.length ? answer.owner_gates.map((gate) => `${GATE_LABELS[gate] ?? gate} (${gate})`).join(" · ") : "—"}</td>
              <td>{answer.next_action}</td>
            </tr>
          ))}
        </tbody>
      </DataTable>
      {readiness.answers.map((answer) => (
        <details key={answer.key} className="margin-bottom-24">
          <summary>{answer.question} — exact reasons and bound identities</summary>
          {answer.reasons.length ? <ul>{answer.reasons.map((reason) => <li key={reason}><code>{reason}</code></li>)}</ul> : <p>No blocking reason.</p>}
          <p>Bound: <code>{JSON.stringify(answer.identities)}</code></p>
          <p>Evidence: <code>{JSON.stringify(answer.evidence)}</code></p>
          {answer.subjects.length ? (
            <DataTable caption="Per subject" ariaLabel={`${answer.key} subjects`}>
              <thead><tr><th scope="col">Subject</th><th scope="col">State</th><th scope="col">Reasons</th></tr></thead>
              <tbody>
                {answer.subjects.map((subject) => (
                  <tr key={`${answer.key}-${subject.subject}-${JSON.stringify(subject.identities)}`}>
                    <td title={JSON.stringify(subject.identities)}>{subject.subject}</td><td><Claim label={subject.status} /></td>
                    <td>{subject.reasons.length ? subject.reasons.join(", ") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          ) : null}
        </details>
      ))}
      <p>Readiness state <code>{readiness.state_hash.slice(0, 12)}</code> (same authorities, same hash).</p>
    </>
  );
}

/** Command kinds gated by a readiness answer (mirrors READINESS_GATES in research_terminal_commands_v1). */
const COMMAND_GATES: Record<string, ReadinessAnswer["key"]> = {
  STRATEGY_SEARCH: "research_run",
  RESEARCH_WATCH_START: "research_watch",
  PAPER_INCUBATION_START: "paper_incubation",
};

function blockedCommands(readiness: ActivationReadiness | undefined): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [kind, key] of Object.entries(COMMAND_GATES)) {
    const answer = readiness?.answers.find((item) => item.key === key);
    if (!answer) out[kind] = ["ACTIVATION_READINESS_UNAVAILABLE"];
    else if (answer.status === "BLOCKED") out[kind] = answer.reasons;
  }
  return out;
}

function CommandsSection({ readiness, commands, unavailable }: {
  readiness: ActivationReadiness | undefined; commands: TerminalCommand[] | undefined; unavailable: string;
}) {
  return (
    <section aria-label="Terminal commands" className="margin-bottom-24">
      <h3>Commands</h3>
      <p>
        Every command runs through the authority that owns it, is checked against the readiness above, and is recorded
        with its outcome. Search, freeze, rerun, validation and runners are carried out by the command worker
        (<code>scripts/research_terminal_worker.py</code>). Owner decisions — watch list (OR-9), account and
        account policy (OR-11), preregistration authorization and the one-shot holdout opening (OR-7) — are
        submitted with the owner&apos;s own credential (<code>scripts/terminal_command.py</code>), never through this
        page&apos;s shared token, and every approval is bound to that authenticated owner.
      </p>
      <CommandConsole blocked={blockedCommands(readiness)} />
      {commands && commands.length ? (
        <DataTable caption="Recent commands" ariaLabel="Recent commands">
          <thead><tr><th scope="col">Requested (UTC)</th><th scope="col">Command</th><th scope="col">By</th><th scope="col">State</th><th scope="col">Outcome</th></tr></thead>
          <tbody>
            {commands.map((command) => (
              <tr key={command.command_id}>
                <td><time dateTime={command.requested_at}>{utc(command.requested_at)}</time></td>
                <td title={JSON.stringify(command.inputs)}>{command.kind}</td>
                <td>{command.requested_by}</td>
                <td><Claim label={command.state} /></td>
                <td><code>{JSON.stringify(command.detail).slice(0, 240)}</code></td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      ) : <p className="empty-notice">{commands ? "No command recorded yet." : unavailable}</p>}
    </section>
  );
}

function CycleSummary({ cycle }: { cycle: TerminalCycle }) {
  return (
    <p>
      Research cycle <code>{cycle.cycle_id}</code>: holdout from <time dateTime={cycle.holdout_start}>{utc(cycle.holdout_start)}</time>{" "}
      is <strong>{cycle.holdout_state}</strong>
      {cycle.holdout_end_exclusive ? <> until <time dateTime={cycle.holdout_end_exclusive}>{utc(cycle.holdout_end_exclusive)}</time></> : null}.
      Preregistrations: {counts(cycle.preregistrations)}. Validation recorded: {cycle.validated ? "yes" : "no"}.
    </p>
  );
}

export default async function ResearchTerminalPage() {
  const ctx = await getWorkspaceContext();
  const [overviewResult, captureResult, signalsResult, studiesResult, rerunsResult, validationResult, accountsResult, incubationResult,
    activationResult, commandsResult] =
    await Promise.all([
      getTerminalOverview(ctx), getCaptureAvailability(ctx), getTerminalSignals(ctx), getStrategyLabStudies(ctx),
      getTerminalReruns(ctx), getTerminalValidation(ctx), getTerminalAccounts(ctx), getTerminalIncubation(ctx),
      getTerminalActivation(ctx), getTerminalCommands(ctx),
    ]);
  const activation = activationResult.state === "AVAILABLE" ? activationResult.value : undefined;
  const commands = commandsResult.state === "AVAILABLE" ? commandsResult.value : undefined;
  const overview = overviewResult.state === "AVAILABLE" ? overviewResult.value : undefined;
  const capture = captureResult.state === "AVAILABLE" ? captureResult.value : undefined;
  const signals = signalsResult.state === "AVAILABLE" ? signalsResult.value : undefined;
  const studies = studiesResult.state === "AVAILABLE" ? studiesResult.value : undefined;
  const reruns = rerunsResult.state === "AVAILABLE" ? rerunsResult.value : undefined;
  const validation = validationResult.state === "AVAILABLE" ? validationResult.value : undefined;
  const accounts = accountsResult.state === "AVAILABLE" ? accountsResult.value : undefined;
  const incubation = incubationResult.state === "AVAILABLE" ? incubationResult.value : undefined;

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Research Terminal"
        subtitle="From evidence to paper incubation on one page. Every step shows its recorded claim verbatim; nothing here searches, opens a holdout, sizes a position or places an order."
        status={overview ? "AVAILABLE" : overviewResult.state}
        asOf={ctx.evidenceTime}
      />

      <nav aria-label="Research workflow steps" className="margin-bottom-24">
        <ol className="workflow-steps">
          {STEPS.map(([id, label], index) => (
            <li key={id}><a href={`#${id}`}>{index + 1}. {label}</a></li>
          ))}
        </ol>
      </nav>

      <Step id="command-center" title="Command Center">
        {activation ? <ActivationPanel readiness={activation} /> : <p className="empty-notice">{stateText(activationResult)}</p>}
        <CommandsSection readiness={activation} commands={commands} unavailable={stateText(commandsResult)} />
        {overview ? (
          <>
            <div className="metrics-strip">
              <div className="metric-card">
                <span className="metric-label">Studies</span>
                <span className="metric-value tabular-num">{overview.studies}</span>
                <span className="metric-sub">{overview.finished_studies} finished · search tier only</span>
              </div>
              <div className="metric-card">
                <span className="metric-label">Decimal Reruns</span>
                <span className="metric-value tabular-num">{Object.values(overview.reruns).reduce((a, b) => a + b, 0)}</span>
                <span className="metric-sub">{counts(overview.reruns)}</span>
              </div>
              <div className="metric-card">
                <span className="metric-label">Holdout</span>
                <span className="metric-value">{overview.cycle.holdout_state}</span>
                <span className="metric-sub">{overview.cycle.cycle_id}</span>
              </div>
              <div className="metric-card">
                <span className="metric-label">Candidates</span>
                <span className="metric-value tabular-num">{counts(overview.candidate_states)}</span>
                <span className="metric-sub">None validated</span>
              </div>
            </div>
            <DataTable caption="Owner gates this terminal waits on" ariaLabel="Owner gates">
              <thead><tr><th scope="col">Gate</th><th scope="col">Topic</th><th scope="col">Status</th><th scope="col">Evidence</th></tr></thead>
              <tbody>
                {overview.owner_gates.map((gate) => (
                  <tr key={gate.gate}><td>{gate.gate}</td><td>{gate.topic}</td><td><Claim label={gate.status} /></td><td>{gate.evidence}</td></tr>
                ))}
              </tbody>
            </DataTable>
            <DataTable caption="What each label may claim" ariaLabel="Authority legend">
              <thead><tr><th scope="col">Label</th><th scope="col">Meaning</th></tr></thead>
              <tbody>
                {Object.entries(overview.authority_legend).map(([label, meaning]) => (
                  <tr key={label}><td><Claim label={label} /></td><td>{meaning}</td></tr>
                ))}
              </tbody>
            </DataTable>
          </>
        ) : <p className="empty-notice">{stateText(overviewResult)}</p>}
      </Step>

      <Step id="market-scan" title="Market Scan" claim="T4 FORWARD CAPTURE">
        <p>What first-party capture has proven per source. Gaps are shown as gaps, never bridged.</p>
        {capture ? (
          <DataTable caption="First-party capture by source" ariaLabel="Capture by source">
            <thead><tr><th scope="col">Symbol</th><th scope="col">Purpose</th><th scope="col">Proven</th><th scope="col">Finalized windows</th><th scope="col">Gaps</th><th scope="col">Partitions proving nothing</th></tr></thead>
            <tbody>
              {capture.sources.map((source) => (
                <tr key={`${source.exchange_symbol}-${source.purpose}`}>
                  <td>{source.exchange_symbol}</td><td>{source.purpose}</td><td className="tabular-num">{hours(source.proven_seconds)}</td>
                  <td className="tabular-num">{source.windows.length}</td><td className="tabular-num">{source.gaps.length}</td><td className="tabular-num">{source.not_finalized.length}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : <p className="empty-notice">{stateText(captureResult)}</p>}
      </Step>

      <Step id="opportunities" title="Opportunities" claim="NOT VALIDATED">
        <p>Live signals of watched frozen candidates: proposals only, filled on paper at the next strictly later bar open.</p>
        {signals && signals.length ? (
          <DataTable caption="Recent live signals" ariaLabel="Live signals">
            <thead><tr><th scope="col">Decided (UTC)</th><th scope="col">Symbol</th><th scope="col">Target</th><th scope="col">Claim</th><th scope="col">Why</th></tr></thead>
            <tbody>
              {signals.map((signal) => (
                <tr key={signal.signal_id}>
                  <td><time dateTime={signal.decided_at}>{utc(signal.decided_at)}</time></td>
                  <td>{signal.symbol}</td>
                  <td>{target(signal.target_from)} → {target(signal.target_to)}</td>
                  <td><Claim label={signal.claim} /></td>
                  <td>{Object.entries(signal.explanation).map(([key, value]) => `${key}=${value}`).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : <p className="empty-notice">{signals ? "No live signal recorded. While the holdout is unopened (OR-7) no forward bar is evaluated." : stateText(signalsResult)}</p>}
      </Step>

      <Step id="strategy-lab" title="Strategy Lab" claim="SEARCH_NON_AUTHORITATIVE">
        {studies && studies.items.length ? (
          <DataTable caption="Registered studies" ariaLabel="Strategy Lab studies">
            <thead><tr><th scope="col">Study</th><th scope="col">Family</th><th scope="col">Trials</th><th scope="col">Progress</th><th scope="col">Why not authoritative</th></tr></thead>
            <tbody>
              {studies.items.map((study) => (
                <tr key={study.study_id}>
                  <td>{study.label}</td><td>{study.strategy_family}</td><td className="tabular-num">{study.planned_trial_count}</td>
                  <td>{counts(study.queue_states)}</td><td>{study.authority.reasons.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : <p className="empty-notice">{studies ? "No study registered." : stateText(studiesResult)}</p>}
      </Step>

      <Step id="compare" title="Compare" claim="GROSS · CONDITIONAL">
        <p>Each frozen top-k recomputed in Decimal (OR-3). An established selection is numerically authoritative; its economics stay gross (no verified fee schedule) and T2-conditional.</p>
        {reruns && reruns.length ? reruns.map((rerun) => (
          <DataTable key={rerun.rerun_hash} caption={`${rerun.study_label} — ${rerun.metric || "metric"}`} ariaLabel={`Rerun ${rerun.rerun_hash.slice(0, 8)}`}>
            <thead><tr><th scope="col">Rank</th><th scope="col">Trial</th><th scope="col">Decimal metric</th><th scope="col">Claim</th></tr></thead>
            <tbody>
              {rerun.selected.length ? rerun.selected.map((item) => (
                <tr key={item.trial_id}>
                  <td className="tabular-num">{item.rank}</td><td><code title={item.trial_id}>{item.trial_id.slice(0, 8)}…</code></td>
                  <td className="tabular-num">{item.metric_value}</td><td><Claim label={rerun.claim} /></td>
                </tr>
              )) : <tr><td colSpan={4}><Claim label={rerun.claim} /> no selection</td></tr>}
            </tbody>
          </DataTable>
        )) : <p className="empty-notice">{reruns ? "No Decimal authority rerun recorded." : stateText(rerunsResult)}</p>}
      </Step>

      <Step id="validation" title="Validation" claim="NONE VALIDATED">
        {validation ? (
          <>
            <CycleSummary cycle={validation.cycle} />
            {validation.candidates.length ? (
              <DataTable caption="Recorded candidate states" ariaLabel="Candidate states">
                <thead><tr><th scope="col">Study</th><th scope="col">Trial</th><th scope="col">State</th><th scope="col">Reasons</th></tr></thead>
                <tbody>
                  {validation.candidates.map((item) => (
                    <tr key={`${item.study_id}-${item.trial_id}-${item.recorded_at}`}>
                      <td>{item.study_label}</td><td><code title={item.trial_id}>{item.trial_id.slice(0, 8)}…</code></td>
                      <td><Claim label={item.label} /></td><td>{item.reasons.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            ) : <p className="empty-notice">No candidate has been through a holdout. Opening it is one-shot and needs the owner&apos;s OR-7 packet.</p>}
          </>
        ) : <p className="empty-notice">{stateText(validationResult)}</p>}
      </Step>

      <Step id="risk-preview" title="Risk Preview" claim="OR-11">
        <p>Account policies carry every economic value risk and sizing use. Nothing is defaulted: a policy missing any value is UNCONFIGURED and yields no risk policy.</p>
        {accounts && accounts.length ? (
          <DataTable caption="Account contexts" ariaLabel="Account policies">
            <thead><tr><th scope="col">Account</th><th scope="col">Kind</th><th scope="col">Policy</th><th scope="col">Missing owner values</th></tr></thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.account_id}>
                  <td>{account.display_name}</td><td>{account.kind}</td><td><Claim label={account.policy_status} /></td>
                  <td>{account.unresolved.length ? account.unresolved.join(", ") : "—"}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : <p className="empty-notice">{accounts ? "No account context registered (OR-11 open)." : stateText(accountsResult)}</p>}
      </Step>

      <Step id="paper" title="Paper" claim="INCUBATING · UNIT EXPOSURE · GROSS">
        {incubation && incubation.state === "AVAILABLE" ? (
          <DataTable caption="Paper incubation by candidate" ariaLabel="Paper incubation">
            <thead><tr><th scope="col">Symbol</th><th scope="col">Trial</th><th scope="col">Fills</th><th scope="col">Round trips</th><th scope="col">Gross sum</th><th scope="col">Break-even bps/side</th><th scope="col">Chain breaks</th><th scope="col">Days / required</th></tr></thead>
            <tbody>
              {incubation.report.candidates.map((item) => (
                <tr key={`${item.study_id}-${item.trial_id}-${item.symbol}`}>
                  <td>{item.symbol}</td><td><code title={item.trial_id}>{item.trial_id.slice(0, 8)}…</code></td>
                  <td className="tabular-num">{counts(item.fills_by_status)}</td><td className="tabular-num">{item.round_trips.length}</td>
                  <td className="tabular-num">{item.gross_return_sum}</td><td className="tabular-num">{item.break_even_cost_bps_per_side ?? "—"}</td>
                  <td className="tabular-num">{item.chain_breaks}</td><td>{item.elapsed_days} / {String(item.required_days)}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : <p className="empty-notice">{incubation ? "No paper fill recorded: incubation starts only for candidates that passed an opened holdout." : stateText(incubationResult)}</p>}
        {incubation ? <p>Sizing {incubation.report.sizing}; costs {incubation.report.cost_mode}; funding {incubation.report.funding}; cost-complete: {incubation.report.cost_complete ? "yes" : "no"}.</p> : null}
      </Step>

      <Step id="monitoring" title="Monitoring">
        <ul>
          <li>Latest live signal: {overview?.latest_signal_at ? <time dateTime={overview.latest_signal_at}>{utc(overview.latest_signal_at)}</time> : "none"}</li>
          <li>Paper fills: {counts(overview?.incubation_fills)}</li>
          <li>Capture state: {capture ? capture.state : stateText(captureResult)} — details on <a href="/evidence">Evidence &amp; Data</a> and <a href="/data-health">Data Health</a>.</li>
        </ul>
        <QualityStateBadge status={overview ? "AVAILABLE" : overviewResult.state} />
      </Step>
    </div>
  );
}
