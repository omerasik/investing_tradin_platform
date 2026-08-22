# Observability Plan

Emit structured logs, metrics and health/readiness checks for data freshness, quality events, job duration, queue depth, simulator lifecycle, risk rejections, reconciliation and kill-switch state. Alert severities are INFO, WARNING, HIGH and CRITICAL. A critical event includes stale active data, duplicate exposure suspicion, reconciliation failure, risk breach, kill-switch failure or secret exposure.
## Shadow-mode evidence

Shadow comparison records retain both paper outcomes, tolerated differences and incident-worthy divergence reasons. Failure drills retain the scenario, the expected protection and the observed protection; a mismatch is a failed drill, not a passing operational signal. These local records do not represent a live broker or market-data comparison.

## Governance-report job evidence

Cycle 228 binds each required report cadence to an exact immutable operational-
job policy and successful run. Cadence mismatch, failed/tampered job evidence or
generation before period close fails closed. Report sections and operational
cost/value evidence are content-addressed and restart-verifiable. See
`GOVERNANCE_REPORTING_AND_COST_POLICY.md` for the complete authority boundary.

This is observability evidence for supplied job runs, not a deployed scheduler,
workflow engine, dead-letter queue, external delivery route or report-generation
service.

## Scheduled-agent governance evidence

Cycle 229 binds each assessment to an exact immutable operational-job run and
to exact point-in-time retrieval and answer-evaluation reports. It records
workflow count, estimated input/output tokens and estimated cost under a
pre-approved envelope. Failed jobs, incomplete/blocked evidence, disabled
policies and aggregate budget breaches fail closed.

No scheduler or agent is operated by this evidence layer. Scheduler, tool,
model-invocation and action authority are all `NONE`, and external telemetry,
provider billing and production orchestration remain absent.
