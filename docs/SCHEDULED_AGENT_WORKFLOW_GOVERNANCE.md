# Scheduled Agent Workflow Governance

Cycle 229 adds an immutable governance boundary for scheduled research-agent
evidence. It evaluates already-produced internal retrieval and answer-evaluation
records. It does not schedule or execute an agent.

## Allowed purposes

The policy catalogue covers the master specification's ten permitted LLM uses:
news summarization, event extraction, entity linking, report generation,
strategy explanation, research assistance, natural-language querying, log
investigation, documentation and code-review assistance. A schedule selects a
non-empty subset of purposes and existing research roles from a pre-approved
governance policy.

## Exact evidence bindings

Every schedule binds the exact content hash of an immutable operational-job
policy. Every assessment binds that schedule, its governance policy and the
exact completed job-run hash. Each candidate then binds one workflow/role to the
exact point-in-time retrieval-report and answer-evaluation-report hashes.
Cross-report, tampered, duplicate workflow/role, premature or unregistered
evidence fails closed. The PostgreSQL store reloads the registered graph and
re-derives the assessment before insertion.

## Budget and review gates

Policies bound minimum schedule interval, workflows per run, input and output
tokens per workflow, total tokens per run and total estimated cost in one
currency. Disabled policy, schedule or job evidence produces
`BLOCKED_POLICY_DISABLED`. Failed/empty/incomplete retrieval or blocked answer
evidence produces `BLOCKED_INCOMPLETE_EVIDENCE`. Aggregate workflow, token or
cost excess produces `BLOCKED_BUDGET`.

Only complete evidence within every bound can reach
`READY_FOR_HUMAN_REVIEW`. That state always retains
`EXPLICIT_HUMAN_REVIEW`; it is not approval.

## Authority boundary

Database constraints fix scheduler, tool, model-invocation and action authority
to `NONE`. The module contains no scheduler, queue, model/connector transport,
credential, prompt construction, external ingestion, delivery adapter, signal,
order, risk mutation or approval path. Estimated tokens and cost are supplied
fixture evidence, not measured provider usage or invoices. Live trading remains
disabled.

## Verification

Migration 0035 adds four immutable tables and composite foreign keys for exact
job, retrieval and answer-evaluation hashes. PR-head run `32556281237` applies
the migration, passes all 451 tests without skips, reconciles all 138
restore-critical tables, passes the 117/117 mypy ratchet and zero-error 46-file
slice, and completes every configured downstream gate.
