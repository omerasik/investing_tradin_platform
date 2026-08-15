# Current Status

Last verified: 2026-08-15. The requirement-level source of truth is
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
- A content-addressed validation store retains multi-capital
  OHLCV capacity estimates, six slippage/spread/impact scenarios,
  frequency-aware latency/signal-decay scenarios, seeded bootstrap and
  trade-order Monte Carlo, stress fixtures, parameter stability and
  multiple-testing evidence. Canonical manifest v1 now binds exact strategy and
  dataset identities/versions, feature/cost versions, evidence IDs and hashes,
  limitations, evaluation time and metadata. PostgreSQL CI now verifies the
  migration, exact restart reconstruction, duplicate idempotency, manifest and
  relational tamper rejection, immutable membership and evidence-hash checks.
- Deterministic pre-trade risk, durable kill switches/idempotency/decision
  history, paper OMS lifecycle/fill/reconciliation evidence, and paper-only
  broker abstraction.
- Structured, non-executing AI research roles with source-bound facts,
  inferences, confidence, model/prompt versioning, safety review and human
  final-synthesis review.

## Verified evidence

- The latest implementation baseline passed 274 Python tests without skips in
  GitHub Actions
  [run 31886409990](https://github.com/omerasik/investing_tradin_platform/actions/runs/31886409990)
  at commit 38d8143, including the complete mapped SQLite-to-PostgreSQL APPLY,
  idempotent replay, conflict/unsupported/missing-mapping rejection, exact
  financial NUMERIC checks and restart reconstruction.
  The same run passed scoped Ruff, mypy and Bandit, dependency audit, SBOM,
  committed-secret guard, TypeScript, Next production build and dashboard
  smoke verification.
- Validation-package focused local tests pass (including capacity, slippage, latency,
  bootstrap/Monte Carlo, stability, multiple-testing and persisted-package
  promotion regressions).
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

1. P0 is VERIFIED by the combined fifteen-invariant exit audit and clean CI run
   31887843535.
2. Build Cycle 10's PostgreSQL professional instrument/calendar authority.
3. Keep live trading and external provider connectivity disabled.

## PostgreSQL persistence progress

- An initial Alembic-managed PostgreSQL schema now normalizes the major domain
  identities, versions, provenance, events and financial values; immutable
  evidence/event tables reject mutation at the database boundary.
- The persistence adapter explicitly separates local SQLite from PostgreSQL,
  and paper/production configuration requires PostgreSQL.
- A backfill utility provides dry-run counts/checksums and fails closed pending
  explicit legacy identity mappings. PostgreSQL integration coverage is present
  and CI provisions the database, but this workstation's Docker engine was not
  available for a local live PostgreSQL run. A safe disposable local PostgreSQL
  DSN was not configured for the current manifest cycle; CI remains the
  authoritative integration environment.
- PostgreSQL critical repositories now atomically persist daily risk
  reservation/decision idempotency, paper OMS creation/event evidence, external
  fill deduplication and validation-package creation. They retain no broker or
  execution authority.
- GitHub Actions run [31721923194](https://github.com/omerasik/investing_tradin_platform/actions/runs/31721923194)
  successfully exercised the ephemeral PostgreSQL migration and integration
  suite along with all configured Python/frontend quality gates. Critical live
  application services still use legacy SQLite paths, so this is integration
  evidence for repository adapters—not a completed persistence migration.
- The unsafe low-level validation-package insert path now fails closed.
  PostgresQuantValidationStore is the sole package writer and requires exact
  canonical manifest plus evidence hashes. Migration 0003 marks prior rows
  LEGACY_UNVERIFIABLE and makes package membership immutable. GitHub Actions
  run 31859646575 verifies this boundary against PostgreSQL 16 after restart and
  deliberate manifest, projection, membership and evidence-hash tampering.
- `build_postgres_paper_core` explicitly composes PostgreSQL OMS/event cursor,
  reconciliation view, kill-switch, risk, validation and promotion authorities
  on one connection owner. The legacy `build_paper_runtime` refuses every
  PostgreSQL target before any SQLite constructor can run. The matrix records
  the remaining pre-trade stores as `MUST_MIGRATE`, and the core intentionally
  remains `submission_ready = False` rather than falling back.
- A separate configured PostgreSQL runtime builder now requires reviewed policy,
  stress and model evidence and persists the daily risk reservation/decision
  before appending an approvable keyed assessment or touching OMS. GitHub
  Actions run 31861000439 verifies the configured approval path and proves an
  active PostgreSQL global kill switch rejects before OMS.

All completion/limitation claims must be updated in `MASTER_ROADMAP.md` with
executed evidence; do not infer production readiness from unit tests.

## Execution-program Cycles 7-9

- Cycle 7 adds real PostgreSQL connection-loss and transaction-failure tests at
  risk, OMS, broker cursor/fill, reconciliation and validation/promotion
  boundaries. Run 31886670023 passed all 280 tests without skips.
- Cycle 8 restores a real custom-format `pg_dump` into a separately created
  database, rejects a truncated dump, compares 16 critical tables by count and
  content hash, reconstructs runtime state and holds a durable recovery gate
  closed until reconciliation. Run 31886880648 passed all 281 tests.
- Cycle 9 expands Ruff and Bandit to the complete Python tree/package, runs a
  complete-package mypy debt ratchet plus a zero-error critical PostgreSQL
  slice, adds detect-secrets for tracked source/configuration, dependency audits, Python SBOM, frontend license
  evidence, frozen install, TypeScript, ESLint, production build and dashboard
  smoke. Next.js is pinned to 16.3.1 and `nanoid` to 3.3.18; local production
  audit reports no known vulnerabilities.
- The GitHub repository is PUBLIC. No visibility setting was changed.
