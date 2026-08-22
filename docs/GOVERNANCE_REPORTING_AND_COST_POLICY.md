# Governance Reporting and Cost Policy

## Scope and authority

Cycle 228 implements evidence contracts for Platform §§31–32. It records and
reconstructs supplied governance-report and operational-cost evidence. It does
not schedule processes, collect provider data, call a model, deliver a report,
procure a resource, change risk, create an order or enable live trading.

Every persisted report binds an exact content-addressed operational-job policy
and an exact successful job run. The report period must have ended before the
scheduled run and generation time. Daily, weekly, monthly and quarterly report
types reject job intervals inconsistent with their cadence; live-readiness is
an on-demand evidence type, not an activation operation.

## Required report catalogue

| Cadence | Report types |
| --- | --- |
| Daily | Market, risk, execution, data health |
| Weekly | Strategy, model drift, portfolio |
| Monthly | Investment review, strategy attribution, cost, incident |
| Quarterly | Model-governance review |
| On demand | Live-readiness report |

Each report contains separately content-addressed sections for `FACT`,
`MODEL_ESTIMATE`, `INFERENCE`, `UNVERIFIED_INFORMATION` and `MISSING_DATA`.
All five sections must exist even when a section is empty. A factless report can
only be persisted as `BLOCKED_INCOMPLETE_EVIDENCE` with explicit missing-data
disclosure. Supplied content is not treated as independently verified truth.

## Operational cost governance

The cost catalogue covers data provider, news provider, social provider, cloud
compute, storage, database, streaming, AI inference, broker fees, exchange fees,
monitoring and backup. A monthly cost report must cover all 12 categories under
an exact pre-approved policy and currency. Missing categories fail closed;
total or per-category breaches yield `BUDGET_BREACH_REVIEW_REQUIRED`.

Policies catalogue the five required modes: `LOCAL_RESEARCH`, `LOW_COST_PAPER`,
`PROFESSIONAL_PAPER`, `LIMITED_LIVE` and `SCALED_LIVE`. These names classify a
budget policy only. They do not select runtime mode or authorize funding,
connectivity, paper orders or live orders.

Dataset/model value assessments bind the exact budget-policy hash, attributable
cost/value evidence and a pre-approved minimum value-to-cost ratio. Outcomes are
limited to `JUSTIFIED_FOR_REVIEW`, `NOT_JUSTIFIED_REVIEW_REQUIRED` and
`BLOCKED_POLICY_DISABLED`. Proposed AI inference is not justified when a
deterministic alternative is available. A justified assessment still has
database-enforced procurement authority `NONE`.

## Persistence and verification

Migration 0034 retains report schedules, cost budgets, cost observations,
value assessments, reports, classified sections and report-cost links in seven
immutable tables. Composite foreign keys bind exact policy/run content hashes.
The PostgreSQL store re-derives each report or value assessment from registered
evidence before accepting it, and restart reads re-hash the full object graph.

Corrected PR-head CI run `32554992009` applies migration 0034, runs all 444
tests without skips, reconciles 134 restore-critical tables, passes the 117/117
mypy ratchet and zero-error 45-file slice, and completes all configured security,
supply-chain, container, frontend and browser gates.

## Explicit limitations

- There is no deployed scheduler, durable workflow engine or dead-letter queue.
- No report is generated from real provider, portfolio, broker or invoice data.
- No external report delivery, paging, procurement or budget-enforcement adapter
  exists.
- Value estimates are supplied fixtures and do not prove realized benefit.
- Live readiness and live-named budget modes grant no live authority; live
  trading remains disabled and requires an entirely separate explicit decision.
