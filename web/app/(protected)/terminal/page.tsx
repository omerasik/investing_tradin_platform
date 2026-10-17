import React from "react";
import {
  getCaptureAvailability,
  getStrategyLabStudies,
  getTerminalAccounts,
  getTerminalActivation,
  getTerminalCandidates,
  getTerminalCommands,
  getTerminalPaperAccount,
  getTerminalIncubation,
  getTerminalComparison,
  getTerminalOverview,
  getTerminalPaperEvidence,
  getTerminalReruns,
  getTerminalSignals,
  getTerminalValidation,
  getTerminalWalkForward,
  getWorkspaceContext,
  stateText,
  utc,
  type ActivationReadiness,
  type CandidatePaperEvidence,
  type ComparisonSort,
  type ReadinessAnswer,
  type TerminalCommand,
  type TerminalCycle,
} from "../../lib/data-access";
import { CommandConsole } from "./command-console";
import { CandidateLifecycleSection, WalkForwardSection } from "./candidate-lifecycle";
import { AccountRiskSection, COMPARE_MAX_STUDIES, ComparisonSection, PaperEvidenceSection, type CompareSelection } from "./compare-section";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { DataTable } from "../../components/data-table";
import { ResearchWorkflowNav } from "../../components/research-workflow-nav";

export const dynamic = "force-dynamic";

/** The workflow, in order. Each step reads recorded evidence only; none of them acts. */
const STEPS = [
  ["command-center", "Command Center"],
  ["market-scan", "Market Scan"],
  ["opportunities", "Opportunities"],
  ["strategy-lab", "Strategy Lab"],
  ["compare", "Compare"],
  ["validation", "Validation"],
  ["candidate-readiness", "Candidate readiness"],
  ["paper-evidence", "Paper evidence"],
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

/** The trial's detail in the Strategy Lab Explorer, with its study kept. */
function TrialLink({ studyId, trialId }: { studyId: string; trialId: string }) {
  const query = new URLSearchParams({ study: studyId, trial: trialId });
  return <a href={`/strategy-lab?${query.toString()}#trial-detail`}><code title={trialId}>{trialId.slice(0, 8)}…</code></a>;
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

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const METRIC = /^[a-z][a-z0-9_]{0,63}$/;

type RawParams = Record<string, string | string[] | undefined>;

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/** The operator's comparison choice from the URL; malformed values are dropped, nothing is defaulted. */
function compareSelection(raw: RawParams): CompareSelection {
  const listed = ([] as string[]).concat(raw.compare_study ?? []);
  const studies = [...new Set(listed.filter((id) => UUID.test(id)))].slice(0, COMPARE_MAX_STUDIES);
  const metricText = one(raw.compare_metric);
  const metric = metricText && METRIC.test(metricText) ? metricText : undefined;
  const dimension = one(raw.compare_dimension);
  const direction = one(raw.compare_direction);
  const sort: ComparisonSort | undefined = metric && (dimension === "SEARCH" || dimension === "DECIMAL_BASELINE")
    && (direction === "ASC" || direction === "DESC") ? { dimension, metric, direction } : undefined;
  return { studies, metric, sort };
}

export default async function ResearchTerminalPage({ searchParams }: { searchParams?: Promise<RawParams> }) {
  const raw = (await searchParams) ?? {};
  const selection = compareSelection(raw);
  const paperStudy = one(raw.paper_study);
  const paperTrial = one(raw.paper_trial);
  const paperTarget = paperStudy && paperTrial && UUID.test(paperStudy) && UUID.test(paperTrial) ? [paperStudy, paperTrial] as const : undefined;
  const ctx = await getWorkspaceContext();
  const selectedReads = Promise.all([
    selection.studies.length ? getTerminalComparison(ctx, selection.studies, selection.sort) : Promise.resolve(undefined),
    paperTarget ? getTerminalPaperEvidence(ctx, paperTarget[0], paperTarget[1]) : Promise.resolve(undefined),
  ]);
  const [overviewResult, captureResult, signalsResult, studiesResult, rerunsResult, validationResult, accountsResult, incubationResult,
    activationResult, commandsResult, walkForwardResult, candidatesResult] =
    await Promise.all([
      getTerminalOverview(ctx), getCaptureAvailability(ctx), getTerminalSignals(ctx), getStrategyLabStudies(ctx),
      getTerminalReruns(ctx), getTerminalValidation(ctx), getTerminalAccounts(ctx), getTerminalIncubation(ctx),
      getTerminalActivation(ctx), getTerminalCommands(ctx), getTerminalWalkForward(ctx), getTerminalCandidates(ctx),
    ]);
  const [comparisonResult, paperResult] = await selectedReads;
  const comparison = comparisonResult?.state === "AVAILABLE" ? comparisonResult.value : undefined;
  const paperItems: CandidatePaperEvidence[] = [];
  if (paperResult?.state === "AVAILABLE") paperItems.push(paperResult.value);
  for (const item of comparison?.paper_evidence ?? []) {
    if (!paperItems.some((seen) => seen.study_id === item.study_id && seen.trial_id === item.trial_id)) paperItems.push(item);
  }
  const activation = activationResult.state === "AVAILABLE" ? activationResult.value : undefined;
  const commands = commandsResult.state === "AVAILABLE" ? commandsResult.value : undefined;
  const registeredAccounts = accountsResult.state === "AVAILABLE" ? accountsResult.value : [];
  // Every registered account: the read restates its authoritative policy resolution and paper risk verdicts.
  const accountReads = await Promise.all(registeredAccounts.map((account) => getTerminalPaperAccount(ctx, account.account_id)));
  const activeAccounts = registeredAccounts.filter((a) => a.policy_status === "ACTIVE");
  const ledgers = activeAccounts.map((account) => accountReads[registeredAccounts.indexOf(account)]);
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

      <ResearchWorkflowNav current="terminal" />

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
        <p>
          This is forward capture from our own recorder, not the historical research data studies are searched on. To inspect the
          historical bars, open the <a href="/chart">Market Chart</a> (historical research bars, never live prices).
        </p>
        {capture ? (
          <DataTable caption="First-party capture by source" ariaLabel="Capture by source">
            <thead><tr><th scope="col">Symbol</th><th scope="col">Purpose</th><th scope="col">Archive</th><th scope="col">Proven</th><th scope="col">Finalized windows</th><th scope="col">Gaps</th><th scope="col">Partitions proving nothing</th></tr></thead>
            <tbody>
              {capture.sources.map((source) => (
                <tr key={`${source.archive}-${source.source_id}`}>
                  <td>{source.exchange_symbol}</td><td>{source.purpose}</td><td>{source.archive}</td><td className="tabular-num">{hours(source.proven_seconds)}</td>
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
        <p>
          Browse every persisted trial, its parameters, stored metrics and Decimal rerun reconciliation in the{" "}
          <a href="/strategy-lab">Strategy Lab Explorer</a> (read-only).
        </p>
        {studies && studies.items.length ? (
          <DataTable caption="Registered studies" ariaLabel="Strategy Lab studies">
            <thead><tr><th scope="col">Study</th><th scope="col">Family</th><th scope="col">Trials</th><th scope="col">Progress</th><th scope="col">Why not authoritative</th><th scope="col">Next</th></tr></thead>
            <tbody>
              {studies.items.map((study) => (
                <tr key={study.study_id}>
                  <td><a href={`/strategy-lab?study=${study.study_id}`}>{study.label}</a></td><td>{study.strategy_family}</td><td className="tabular-num">{study.planned_trial_count}</td>
                  <td>{counts(study.queue_states)}</td><td>{study.authority.reasons.join(", ")}</td>
                  <td><a href={`/terminal?${new URLSearchParams({ compare_study: study.study_id }).toString()}#compare`}>Compare its frozen candidates</a></td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : <p className="empty-notice">{studies ? "No study registered." : stateText(studiesResult)}</p>}
      </Step>

      <Step id="compare" title="Compare" claim="GROSS · CONDITIONAL">
        <p>
          Frozen candidates side by side from recorded evidence only: the search tier, the Decimal rerun, walk-forward development
          evidence and holdout validation stay in separate rows, and none of them is a validation. You choose the studies, the metric
          shown and any sort; there is no composite score and no default ranking.
        </p>
        <ComparisonSection
          studies={studies?.items}
          selection={selection}
          comparison={comparison}
          unavailable={comparisonResult && comparisonResult.state !== "AVAILABLE" ? stateText(comparisonResult) : undefined}
        />
        <h3>Decimal reruns</h3>
        <p>Each frozen top-k recomputed in Decimal (OR-3). An established selection is numerically authoritative; its economics stay gross (no verified fee schedule) and T2-conditional.</p>
        {reruns && reruns.length ? reruns.map((rerun) => (
          <DataTable key={rerun.rerun_hash} caption={`${rerun.study_label} — ${rerun.metric || "metric"}`} ariaLabel={`Rerun ${rerun.rerun_hash.slice(0, 8)}`}>
            <thead><tr><th scope="col">Rank</th><th scope="col">Trial</th><th scope="col">Decimal metric</th><th scope="col">Claim</th></tr></thead>
            <tbody>
              {rerun.selected.length ? rerun.selected.map((item) => (
                <tr key={item.trial_id}>
                  <td className="tabular-num">{item.rank}</td><td><TrialLink studyId={rerun.study_id} trialId={item.trial_id} /></td>
                  <td className="tabular-num">{item.metric_value}</td><td><Claim label={rerun.claim} /></td>
                </tr>
              )) : <tr><td colSpan={4}><Claim label={rerun.claim} /> no selection</td></tr>}
            </tbody>
          </DataTable>
        )) : <p className="empty-notice">{reruns ? "No Decimal authority rerun recorded." : stateText(rerunsResult)}</p>}
        <h3>Walk-forward</h3>
        <WalkForwardSection reports={walkForwardResult.state === "AVAILABLE" ? walkForwardResult.value : undefined} unavailable={stateText(walkForwardResult)} />
      </Step>

      <Step id="validation" title="Validation" claim="NONE VALIDATED">
        <p>
          What still blocks the holdout, watching and paper incubation, and the owner gate each waits on, is answered in the{" "}
          <a href="#command-center">Command Center readiness table</a>. Trial links open the recorded trial in the Strategy Lab Explorer.
        </p>
        {validation ? (
          <>
            <CycleSummary cycle={validation.cycle} />
            {validation.candidates.length ? (
              <DataTable caption="Recorded candidate states" ariaLabel="Candidate states">
                <thead><tr><th scope="col">Study</th><th scope="col">Trial</th><th scope="col">State</th><th scope="col">Reasons</th></tr></thead>
                <tbody>
                  {validation.candidates.map((item) => (
                    <tr key={`${item.study_id}-${item.trial_id}-${item.recorded_at}`}>
                      <td>{item.study_label}</td><td><TrialLink studyId={item.study_id} trialId={item.trial_id} /></td>
                      <td><Claim label={item.label} /></td><td>{item.reasons.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            ) : <p className="empty-notice">No candidate has been through a holdout. Opening it is one-shot and needs the owner&apos;s OR-7 packet.</p>}
          </>
        ) : <p className="empty-notice">{stateText(validationResult)}</p>}
      </Step>

      <Step id="candidate-readiness" title="Candidate readiness" claim="ASSESSMENT · NOT A DECISION">
        <p>Each candidate&apos;s stored chain (study → rerun → walk-forward → preregistration → validation → exit criteria), its blockers and the owner gate each waits on.</p>
        <CandidateLifecycleSection candidates={candidatesResult.state === "AVAILABLE" ? candidatesResult.value : undefined} unavailable={stateText(candidatesResult)} />
      </Step>

      <Step id="paper-evidence" title="Paper evidence" claim="NON_COSTABLE · NOT A PROMOTION DECISION">
        <p>Live/replay paper-fill parity and linked forward funding evidence per candidate, resolved server-side from stored rows. Nothing here promotes, sizes or charges.</p>
        <PaperEvidenceSection
          items={paperItems}
          unavailable={paperResult && paperResult.state !== "AVAILABLE" ? stateText(paperResult) : undefined}
        />
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
        <AccountRiskSection accounts={registeredAccounts} reads={accountReads} />
      </Step>

      <Step id="paper" title="Paper" claim="INCUBATING · UNIT EXPOSURE · GROSS">
        <p>Recorded simulation status, never execution authority. Incubation starts only through the gated command above, once readiness allows it; blockers are listed in the <a href="#command-center">Command Center readiness table</a>.</p>
        {incubation && incubation.state === "AVAILABLE" ? (
          <DataTable caption="Paper incubation by candidate" ariaLabel="Paper incubation">
            <thead><tr><th scope="col">Symbol</th><th scope="col">Trial</th><th scope="col">Fills</th><th scope="col">Round trips</th><th scope="col">Gross sum</th><th scope="col">Break-even bps/side</th><th scope="col">Chain breaks</th><th scope="col">Days / required</th></tr></thead>
            <tbody>
              {incubation.report.candidates.map((item) => (
                <tr key={`${item.study_id}-${item.trial_id}-${item.symbol}`}>
                  <td>{item.symbol}</td><td><TrialLink studyId={item.study_id} trialId={item.trial_id} /></td>
                  <td className="tabular-num">{counts(item.fills_by_status)}</td><td className="tabular-num">{item.round_trips.length}</td>
                  <td className="tabular-num">{item.gross_return_sum}</td><td className="tabular-num">{item.break_even_cost_bps_per_side ?? "—"}</td>
                  <td className="tabular-num">{item.chain_breaks}</td><td>{item.elapsed_days} / {String(item.required_days)}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : <p className="empty-notice">{incubation ? "No paper fill recorded: incubation starts only for candidates that passed an opened holdout." : stateText(incubationResult)}</p>}
        {incubation ? <p>Sizing {incubation.report.sizing}; costs {incubation.report.cost_mode}; funding {incubation.report.funding}; cost-complete: {incubation.report.cost_complete ? "yes" : "no"}.</p> : null}
        <DataTable caption="Paper accounts under their ACTIVE policy (simulation, never execution authority)" ariaLabel="Paper account ledgers">
          <thead><tr><th scope="col">Account</th><th scope="col">State</th><th scope="col">Orders</th><th scope="col">Rejected because</th><th scope="col">Equity by cost scenario</th><th scope="col">Not applicable (owner)</th></tr></thead>
          <tbody>
            {activeAccounts.length ? activeAccounts.map((account, index) => {
              const result = ledgers[index];
              const value = result.state === "AVAILABLE" ? result.value : undefined;
              return (
                <tr key={account.account_id}>
                  <td>{account.display_name}</td>
                  <td><Claim label={value?.state ?? result.state} /></td>
                  <td>{value?.summary ? counts(value.summary.decisions) : (value?.reasons ?? [stateText(result)]).join(", ")}</td>
                  <td>{value?.summary ? counts(value.summary.rejections_by_reason) : "—"}</td>
                  <td className="tabular-num">{value?.summary ? Object.entries(value.summary.equity_by_scenario).map(([name, equity]) => `${name} ${equity}`).join(" · ") : "—"}</td>
                  <td>{value?.ledger?.not_applicable_controls.length ? value.ledger.not_applicable_controls.join(", ") : "—"}</td>
                </tr>
              );
            }) : <tr><td colSpan={6}>No ACTIVE account policy yet (OR-11): money P&amp;L and risk checks start when the owner completes one.</td></tr>}
          </tbody>
        </DataTable>
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
