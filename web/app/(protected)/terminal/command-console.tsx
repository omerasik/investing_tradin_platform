"use client";

import { FormEvent, useState } from "react";

/**
 * Input templates per command kind. Every value is an empty placeholder: the
 * backend refuses a request until the operator types each field explicitly, so
 * no strategy or economic parameter is ever chosen here.
 */
const TEMPLATES: Record<string, { label: string; inputs: Record<string, unknown> }> = {
  STRATEGY_SEARCH: { label: "Run a Strategy Lab search", inputs: { family: "", dataset_version_id: "", workers: null } },
  CANDIDATE_FREEZE: { label: "Freeze a study's top-k", inputs: { family: "", dataset_version_id: "", metric: "", direction: "", top_k: null } },
  DECIMAL_RERUN: { label: "Decimal authority rerun", inputs: { family: "", dataset_version_id: "", metric: "", direction: "", top_k: null, workers: null } },
  HOLDOUT_VALIDATE: { label: "Validate on the opened holdout", inputs: { preregistration_hash: "" } },
  RESEARCH_WATCH_START: { label: "Start the research watch runner", inputs: { family: "", dataset_version_id: "", symbol: "", watchlist_id: "" } },
  RESEARCH_WATCH_STOP: { label: "Stop a research watch runner", inputs: { start_command_id: "" } },
  PAPER_INCUBATION_START: { label: "Start paper incubation", inputs: { family: "", dataset_version_id: "", symbol: "" } },
  PAPER_INCUBATION_STOP: { label: "Stop paper incubation", inputs: { start_command_id: "" } },
};

function newKey(): string {
  return `terminal-${crypto.randomUUID()}`;
}

export function CommandConsole({ blocked }: { blocked: Record<string, string[]> }) {
  const [kind, setKind] = useState("STRATEGY_SEARCH");
  const [inputs, setInputs] = useState(JSON.stringify(TEMPLATES.STRATEGY_SEARCH.inputs, null, 1));
  const [key, setKey] = useState(newKey);
  const [result, setResult] = useState<string>();
  const reasons = blocked[kind] ?? [];

  function choose(next: string) {
    setKind(next);
    setInputs(JSON.stringify(TEMPLATES[next].inputs, null, 1));
    setKey(newKey());
    setResult(undefined);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    let parsed: unknown;
    try { parsed = JSON.parse(inputs); } catch { setResult("Inputs are not valid JSON."); return; }
    setResult("Submitting…");
    const response = await fetch("/api/terminal/commands", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, inputs: parsed, idempotency_key: key }),
    });
    const body: unknown = await response.json().catch(() => ({}));
    const record = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
    const detail = record.detail;
    if (response.ok) {
      setResult(`Command ${String(record.command_id)}: ${String(record.state)}.`);
      setKey(newKey());
    } else if (detail && typeof detail === "object" && "reasons" in detail) {
      setResult(`Blocked: ${(detail as { reasons: string[] }).reasons.join(", ")}`);
    } else {
      setResult(`Refused (${response.status}): ${typeof detail === "string" ? detail : "invalid command"}`);
    }
  }

  return (
    <form onSubmit={submit} aria-label="Terminal command console">
      <label>Command
        <select aria-label="Command kind" value={kind} onChange={(event) => choose(event.target.value)}>
          {Object.entries(TEMPLATES).map(([value, template]) => (
            <option key={value} value={value}>{template.label} ({value})</option>
          ))}
        </select>
      </label>
      <label>Inputs (every field explicit)
        <textarea aria-label="Command inputs" rows={8} value={inputs} onChange={(event) => setInputs(event.target.value)} spellCheck={false} />
      </label>
      <label>Idempotency key
        <input aria-label="Command idempotency key" value={key} onChange={(event) => setKey(event.target.value)} required />
      </label>
      {reasons.length ? (
        <p role="status">Waiting on: {reasons.map((reason) => <code key={reason}>{reason} </code>)}</p>
      ) : null}
      <button type="submit" disabled={reasons.length > 0}>{reasons.length ? "Queue command (blocked)" : "Queue command"}</button>
      <p aria-live="polite">{result ?? "No command submitted."}</p>
    </form>
  );
}
