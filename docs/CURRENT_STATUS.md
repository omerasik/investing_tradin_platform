# Current Status

Last synchronized: 2026-08-16. The requirement-level source of truth is
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

- A fresh local audit on 2026-08-16 discovered **328 Python tests** and passed
  all locally runnable tests. It skipped **27 PostgreSQL-dependent tests**
  plus the Cycle 203/204 PostgreSQL tests (**29 total**) because no disposable PostgreSQL DSN is
  configured on this workstation; this is not substituted for CI evidence.
- The latest no-skip PostgreSQL evidence is Cycle 203 mainline GitHub Actions
  run `31920718692` on merge commit `bd38d35`: migration 0017, all **323 tests
  without skips**,
  matched **54-table** restore/reconciliation, Ruff, mypy 120/120 ratchet,
  zero-error 22-file critical slice, security/dependency/SBOM/secret gates and
  frontend/dashboard gates. This closes the Cycle 203 `VERIFIED` exit gate.

- A historic implementation baseline passed 274 Python tests without skips in
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
- Cycle 201 verified adds an immutable, normalized PostgreSQL scorecard
  authority. It derives deterministic identities from canonical raw evidence,
  records measured/assumed/unavailable metrics and transparent non-authoritative
  components, and refuses scorecard eligibility until a hash-verified,
  version-bound validation package is linked. Mainline CI run `31918548340` on
  `3a45028` passed without skips; synthetic fixtures are not an investment conclusion.
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

1. P0 remains VERIFIED by the combined fifteen-invariant exit audit and clean
   CI run 31887843535; Cycles 10–15 subsequently verified provider-neutral
   PostgreSQL instrument, historical-data, Data Health, fundamental and macro
   authorities.
2. Keep authorized real-data activation EXTERNAL_BLOCKED until the operator
   approves a provider and its terms; continue provider-independent work.
3. Keep live trading and external provider connectivity disabled.
4. Begin Cycle 203 with PostgreSQL-native, point-in-time investment evidence;
   retain strict separation from trading capital and execution authority.

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

## Cycle 10 verified

Migration 0008 and `PostgresProfessionalInstrumentMaster` add PostgreSQL
professional instrument/calendar authority. Temporal symbol and external-ID
mappings are point-in-time and overlap-protected; lifecycle events make
delisting effective without rewriting history. The provider-neutral MVP covers
US equity/ETF, EURUSD, GLD-as-ETF-proxy, BTC and ETH, with US/DST/holiday/early-
close, FX 24x5 and crypto 24x7 calendars. GitHub Actions verify run
`31888506314` passed on commit `083a7bf`: migration 0008, all 284 tests without
skips, a fresh 23-table restore and every quality/security/frontend gate passed.
This is not a licensed feed or permission for broker connectivity or live
trading.

## Cycle 11 provider-neutral core verified / real provider externally blocked

Migration 0009 and `PostgresHistoricalMarketDataPipeline` implement immutable
authorized-source, raw observation, normalization, sealed dataset and research
query layers for US equities/ETFs. The contract retains OHLCV and corporate-
action provenance/revisions, resolves provider IDs point-in-time, rejects bad
data and excludes latest-adjusted prices by default. GitHub Actions verify run
`31889028646` passed on commit `9337b8f`: migration 0009, all 288 tests without
skips, a fresh 28-table restore and every quality/security/frontend gate passed.
The implementation uses synthetic fixtures; selecting a provider and accepting
its legal/storage terms is `EXTERNAL_BLOCKED`, so Cycle 11 is not yet a verified
authorized-real-data slice.

## Cycle 12 verified

Migration 0010 and `data_health.py` persist policy-versioned assessments and
all eleven required finding types with seven action levels. Applicable global,
asset-class, strategy and instrument scopes resolve point-in-time. Blocking
health prevents a PostgreSQL signal from becoming `VALIDATED` through both the
repository and a database trigger; a later clean assessment reopens only the
same scope. GitHub Actions verify run `31889499296` passed on commit `4ab25dc`:
migration 0010, all 291 tests without skips, a fresh 30-table restore and every
quality/security/frontend gate passed.

## Cycle 13 externally blocked

The required authorized real market dataset is unavailable because no provider
and legal/storage terms were approved. No synthetic result is being relabelled
as a real-data proof or alpha.

## Cycle 14 provider-neutral core verified / SEC retrieval externally blocked

Migration 0011 and `pit_fundamentals.py` add authorized-source, SEC-style filing
and filing-fact PostgreSQL authorities. PIT reads enforce acceptance and
ingestion visibility and revision selection. Transparent formula v1 covers
revenue, operating margin, FCF, debt, shares/dilution, ROIC components and
capital allocation. After failed run `31889938442` exposed an untyped nullable
PostgreSQL parameter, commit `88a76af` fixed the query. Verify run `31890008661`
then passed migration 0011, all 295 tests without skips, the fresh 33-table
restore and every quality/security/frontend gate. Actual SEC network ingestion
is `EXTERNAL_BLOCKED` until terms and operator identity are approved.

## Cycle 15 provider-neutral core verified / source activation externally blocked

Migration 0012 and `pit_macro.py` add the controlled macro catalogue and
immutable initial-release/revision PostgreSQL path. PIT reads cannot expose a
revision before release and ingestion. GitHub Actions verify run `31890332414`
passed on commit `b67a35f`: migration 0012, all 297 tests without skips, a
fresh 35-table restore and every quality/security/frontend gate. Actual
authoritative source activation remains `EXTERNAL_BLOCKED` until terms are approved.

## Cycle 200 verified — Feature Platform V2

Migration 0014 adds versioned feature-definition and immutable feature-
materialization authorities. Every materialization records instrument, feature
version, dataset version, event/effective/knowledge/computed times, source
manifest, value, quality status and content hash. Decision-time reads are
dataset-isolated and reject future knowledge. Transparent offline market
baselines now cover price/returns, trend, momentum, volatility and liquidity;
fundamental/macro definitions are declared but deliberately receive no
fabricated value without authorized PIT input. The activation package keeps
provider, SEC and macro activation `EXTERNAL_BLOCKED`.

## Cycle 201 verified — immutable strategy scorecards

Migration 0015 introduces immutable `strategy_scorecards`, normalized raw
metric observations, transparent components and validation-package bindings.
The scorecard identity includes the strategy/research/dataset/feature/cost/PIT
cutoffs, data-health status, limitations and evidence manifest; replay is
idempotent only for identical canonical evidence. Performance and tail-risk
helpers mark unavailable statistics explicitly, while arbitrary robustness,
execution, data-quality and signal-decay observations retain their evidence
state rather than inventing values. Components, including a disclosed
complexity penalty, are navigation aids and cannot promote a strategy.

Promotion eligibility remains `REVIEW_REQUIRED` only and fails closed without
a verified complete validation package whose manifest matches the scorecard's
strategy, dataset, feature and cost bindings. The PostgreSQL fixture test covers
missing-package blocking, immutable rows, replay/restart and wrong-binding
rejection. GitHub Actions run `31918548340` on merge commit `3a45028` passed
the no-skip PostgreSQL migration/integration path, 43-table fresh restore,
security, dependency and frontend/dashboard gates. No provider, broker or
real-market endpoint was called.

## Cycle 202 VERIFIED — Professional Trend Strategy Engine V2

Four immutable `RESEARCH_ONLY` Trend definitions consume exact Cycle 200
Feature Authority IDs/versions and produce deterministic bounded signal series.
Unavailable/rejected feature values remain explicitly `UNAVAILABLE`; version,
dataset, future-knowledge, incomplete-history and Data Health violations fail
closed.

One deterministic orchestration binds a sealed dataset, strategy, feature
materializations and cost model, then executes next-period vector accounting,
separate iterative accounting, realistic event simulation, golden divergence
reconciliation, purged/embargoed walk-forward holdout, the eight-part quant
validation chain, Scorecard V2 and a complete immutable package. PostgreSQL
persists and replays definition/version, experiment, walk-forward, golden,
validation, scorecard and package evidence without adding execution authority.

PR CI run `31919806804` passed on the final PR head `bd07172`; PR #4 then merged
as `72c48bd`. Mainline CI run `31919886932` passed migration 0016, all 318 tests
without skips and the 48-table restore plus every configured backend, security,
dependency, frontend and dashboard gate. Evidence is explicitly
`SYNTHETIC_ENGINEERING_EVIDENCE_ONLY`; real capacity/slippage/impact, regime
performance and live consistency are unavailable. Maximum automatic state is
`REVIEW_REQUIRED`; there is no paper/live activation. Cycle 202 is `VERIFIED`.

## Cycle 203 VERIFIED — Professional Long-Term Investment Engine V2

The first Cycle 203 increment extends the existing normalized investment thesis
and evidence tables instead of introducing a parallel authority. Immutable
thesis versions now bind their complete contract, PIT instrument, knowledge
cutoff, parent version and content hash. One deterministic orchestrator reads
the Cycle 14/15 PIT fundamental and macro authorities, rejects future evidence,
requires explicit Data Health evidence, and produces source-bound company
quality, transparent finite-DCF valuation and thesis-drift evidence. Missing
inputs remain `UNAVAILABLE` and block review; fixture valuations are explicitly
not price targets or recommendations.

Versioned investment policy is restricted to `INVESTMENT` accounts. Rebalance
candidates bind the policy, holdings hash and analysis evidence, enforce cash,
single-weight and turnover limits, and are limited to `REVIEW_REQUIRED` or
`BLOCKED` with `execution_authority = false`. Migration 0017 adds the smallest
missing normalized policy/candidate authorities and makes the existing thesis,
evidence and review tables immutable. The restore manifest now covers 54
critical tables. Local evidence is 323 tests discovered with 28 PostgreSQL-only
skips, Ruff, compileall, mypy 120/120 and a zero-error 22-file critical slice.
PR #5's final run `31920627370` passed on `b737797`, then merged as `bd38d35`.
Exact-merge mainline CI run `31920718692` passed migration 0017, all 323 tests
without skips, matched the 54-table restore, and passed every configured
backend, security, dependency, frontend and dashboard gate. Cycle 203 is
`VERIFIED`.

## Cycle 204 PR-verified — Regime Engine V2

The Cycle 204 branch replaces the unbound three-label helper gap with an
immutable, feature-authority-bound research path. A versioned model consumes
exact feature IDs/versions from a sealed dataset and emits probabilities,
labels and uncertainty across trend, volatility level/direction, liquidity
level/direction, risk appetite, macro cycle and stress. Together those eight
dimensions cover every state listed in platform section 10, including crisis,
credit/currency/commodity stress and correlation breakdown.

Rule-based and transparent probabilistic-ensemble outputs are compared against
immutable out-of-sample labels with Brier score and accuracy. HMM,
change-point, clustering, Bayesian-state and tree methods remain explicitly
`UNAVAILABLE` without trained, calibrated models. Reads occur as of each event,
prefix invariance is tested, and missing/rejected/future features or blocked
Data Health fail closed. Regime risk changes are non-executing candidates only;
automatic increases are prohibited and the database enforces no automatic
authority.

Migration 0018 adds normalized immutable model, run, observation, method
evaluation, evidence-membership and risk-candidate tables. The restore manifest
now covers 62 critical tables. Local verification discovered 328 tests with 29
PostgreSQL-only skips and passed compileall, Ruff, mypy 120/120, the zero-error
23-file critical slice, and Alembic's sole 0018 head. PR CI run `31921339310`
on `62a5316` applied migration 0018, ran all 328 tests without skips, matched
the 62-table restore, and passed every configured backend, security, dependency,
frontend and dashboard gate. Final `VERIFIED` status still requires PR #6 merge
and green CI on the exact mainline merge commit.
