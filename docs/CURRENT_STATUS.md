# Current Status

Last verified: 2026-08-10. The requirement-level source of truth is
[`MASTER_ROADMAP.md`](MASTER_ROADMAP.md); this document is a concise operational
snapshot, not a second roadmap.

## What is implemented locally

- A Python modular monolith with append-only SQLite evidence stores, a protected
  local FastAPI control plane, and a Next.js operator dashboard.
- Point-in-time market/fundamental/macro/corporate-action contracts; normalized
  provider and broker adapter boundaries; no credentials or live transport.
- Research baselines with next-period timing, pessimistic costs, idempotent
  experiment storage, purged/embargoed non-overlapping walk-forward evidence,
  and a separate iterative close-to-close accounting check.
- A deterministic event engine with latency, bid/ask, participation limits,
  partial fills, fees, impact, order types and corporate-action accounting.
- Golden vector-to-event evidence: an immutable single-price close-auction run
  drives signal orders through the event engine, applies point-in-time split or
  cash-dividend records, compares final equity/exposure, and persists a
  content-addressed artifact. It is deliberately limited to matching the
  vector engine's close convention; it is not a realistic execution benchmark.
- Deterministic pre-trade risk, durable kill switches/idempotency/decision
  history, paper OMS lifecycle/fill/reconciliation evidence, and paper-only
  broker abstraction.
- Structured, non-executing AI research roles with source-bound facts,
  inferences, confidence, model/prompt versioning, safety review and human
  final-synthesis review.

## Verified evidence

- 244 local Python tests and Python compilation pass (including golden
  vector-to-event reconciliation tests).
- Next.js production build has passed using the installed locked dependencies.
- Browser E2E has exercised configured paper OMS evidence, investments/alerts,
  research strategy creation, backtest launch with held-out evidence, and an
  invalid backtest failure state.

## Non-negotiable boundaries

- Live trading is disabled in code and must remain disabled.
- No real broker order, account credential, provider credential, or live market
  data request has been used as verification evidence.
- Research, agent, dashboard and paper workflows cannot bypass deterministic
  risk controls or create a live order.

## Immediate P0 work

1. Extend the golden artifact from close-auction parity to identical-data event
   execution scenarios with spread, fees, partial fills, latency and broader
   corporate-action treatment.
2. Add durable capacity, slippage/latency sensitivity, bootstrap, historical
   stress, parameter-stability and multiple-testing artifacts before promotion.
3. Establish Postgres migrations and normalized production domain storage.

All completion/limitation claims must be updated in `MASTER_ROADMAP.md` with
executed evidence; do not infer production readiness from unit tests.
