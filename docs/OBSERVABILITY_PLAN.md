# Observability Plan

Emit structured logs, metrics and health/readiness checks for data freshness, quality events, job duration, queue depth, simulator lifecycle, risk rejections, reconciliation and kill-switch state. Alert severities are INFO, WARNING, HIGH and CRITICAL. A critical event includes stale active data, duplicate exposure suspicion, reconciliation failure, risk breach, kill-switch failure or secret exposure.
## Shadow-mode evidence

Shadow comparison records retain both paper outcomes, tolerated differences and incident-worthy divergence reasons. Failure drills retain the scenario, the expected protection and the observed protection; a mismatch is a failed drill, not a passing operational signal. These local records do not represent a live broker or market-data comparison.
