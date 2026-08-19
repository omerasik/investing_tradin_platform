# Test Strategy

Use unit, integration, contract, property, data-quality, backtest-regression, risk, security, failure-injection, restore and UI tests. Golden data and deterministic runs are required. Critical invariants: no order bypasses risk; expired signals and disabled strategies cannot create orders; duplicate intents cannot create exposure; stale data/reconciliation failures block risk increases; live trading remains impossible.

The CI suite provisions ephemeral PostgreSQL, applies Alembic migrations, and
runs migration/immutable-schema integration coverage when `POSTGRES_TEST_DSN`
is supplied. It also runs compile, deterministic unit tests, full-tree Ruff,
full-package Bandit, complete-package mypy debt ratchet, zero-error mypy for the
critical PostgreSQL slice, Python/frontend dependency audits, tracked source/
configuration secret detection, Python SBOM, frontend license inventory, frozen frontend
install, TypeScript, ESLint, production build and dashboard smoke. Local
machines without a running PostgreSQL service
skip that integration class explicitly; this is an external-environment gap,
not a passing production test.

The first executed CI evidence is GitHub Actions
[run 31721923194](https://github.com/omerasik/investing_tradin_platform/actions/runs/31721923194): PostgreSQL 16 was
provisioned, migrated and used by the integration/concurrency suite. Local
Docker remains unavailable, but that no longer leaves PostgreSQL test execution
unverified.

The PostgreSQL integration suite seeds normalized foreign keys and exercises
OMS/fill idempotency, daily-notional rejection, concurrent reservations and
validation-package rollback.
It is designed to be extended with concurrent reservation/restart/reconciliation
cases as each legacy repository moves to the adapter boundary.

The recovery job creates a custom-format backup, proves a truncated backup is
rejected, restores into a separately created database, compares revision plus
the critical table allow-list hashes/counts, reconstructs runtime state and releases a
durable recovery gate only after reconciliation.

Cycle 10 adds PostgreSQL tests for temporal symbol/provider resolution, overlap
constraints, delisting, ingestion-time visibility, US early-close/holiday/DST,
FX weekend boundaries, crypto 24x7 and restart. Fresh-restore comparison now
includes all seven professional instrument/calendar tables.

Cycle 11 adds real PostgreSQL coverage for immutable raw observations,
provider-ID resolution with separate event/knowledge times, OHLCV revisions,
quality rejection, dividend/split/symbol-change/delisting normalization, sealed
dataset hashing, default exclusion of latest-adjusted values, pre-seal PIT
invisibility and close/reopen reconstruction. Restore comparison includes all
five historical-data tables (**28 critical tables total**).

Cycle 12 locally covers every required detector and all seven action levels.
Real PostgreSQL coverage persists a blocking instrument assessment, proves both
a direct SQL validation and the repository validation fail closed, appends a
later clean assessment, validates successfully, rejects assessment mutation and
reconstructs the exact findings after restart. Restore hashing includes the two
data-health tables (**30 critical tables total**).

The Cycle 14 core adds real PostgreSQL tests for filing acceptance/ingestion
visibility, amended-revision selection, as-reported and standardized value
retention, immutable filing evidence and close/reopen reconstruction. Formula
unit tests cover all requested transparent fundamental metrics. Restore hashing
includes all three PIT fundamental tables (**33 critical tables total**).

The Cycle 15 core tests the controlled catalogue plus initial release,
ingestion delay, revision visibility, immutability and restart in PostgreSQL.
Restore hashing expands to **35 critical tables**.

Cycle 201 adds scorecard unit coverage for insufficient samples, no-trade and
zero-volatility availability, negative-return drawdown/Sortino, tail risk,
transparent complexity penalty, data-health blocking, deterministic identities
and labelled assumptions. The PostgreSQL fixture test creates a complete
immutable validation package, proves a scorecard is ineligible until linked,
rejects wrong dataset/feature/cost bindings and mutation, replays idempotently,
and reconstructs eligibility after restart. The restore allow-list includes
the scorecard, metric, component, Data Health link and package-link tables.

## Cycle 210 — Social/Narrative Intelligence Core

Cycle 210 adds deterministic coverage for rights and derived-use rejection,
raw-storage and geographic privacy limits, author hashing, untrusted-text
sanitization, all six required discussion classes, narrative clustering and
PIT windows. Metric tests cover mention/author acceleration, sentiment change,
disagreement, emotional intensity, influencer concentration, bot/spam/
coordination and pump risk, persistence, emergence and price divergence. Data
Health and high pump risk block evidence; database fields enforce research-only,
false standalone-trigger and false automatic authority.

Migration 0022 adds six immutable tables and expands the fresh-restore allow-list
to **97 critical tables**. The PostgreSQL test requires idempotent publication,
PIT visibility, restart reconstruction, immutable no-authority constraints and
unchanged OMS/strategy-activation counts. Local verification discovers **357
tests**, passes **323**, and skips the **34 PostgreSQL-only tests** explicitly
because no disposable local DSN exists. Corrected PR run `32280796788` applies
migration 0022, runs all **357 tests without skips**, matches all **97 restored
tables** and passes every configured quality, security, dependency, frontend,
build, smoke and browser gate. Initial run `32280620168` failed before tests on
an unsupported JSONB object-length function; the corrected exact-key constraint
uses supported containment/removal operators and is migration-verified.

## Cycle 211 — Signal Lifecycle Operations and Explorer

Cycle 211 adds unit coverage for mandatory actor/reason/evidence metadata,
monotonic lifecycle time, invalid/terminal transitions, deterministic expiry,
idempotent replay and rejection of expired signals by the risk adapter. The
PostgreSQL integration path exercises validation-to-lifecycle persistence,
restart reconstruction and expiry. Operator API tests keep the point-in-time
signal route authenticated, typed, GET-only and bounded; the configured browser
matrix adds a Signal Explorer scenario that verifies reasons while asserting no
execution or strategy-activation controls.

Migration 0023 adds one immutable lifecycle table. Runtime proposal, validation
and lifecycle authorities are now all included in the fresh-restore manifest,
raising it from 97 to **100 critical tables**. Local verification discovers
**358 tests**, passes **324**, and explicitly skips **34 PostgreSQL-only tests**.
PR-head run `32283931250` then verifies migration 0023, restart/immutability,
all **358 tests without skips**, all **100 restore-critical tables**, every
quality/security/supply-chain gate, the production build and all eleven
protected configured browser scenarios. Earlier runs `32283521769` and
`32283691569` exposed and closed, respectively, an over-specific expiry-batch
assertion and stale secret-baseline line metadata.

## Cycle 212 — Per-Trade Stop and Gap-Loss Enforcement

Cycle 212 adds deterministic tests for complete typed risk-policy decoding,
missing and wrong-side stops, intent/signal direction and entry-range mismatch,
maximum stop distance, loss at stop and conservative policy-buffered gap loss.
Coordinator and composed-runtime tests prove the calculations survive keyed
SQLite reopen, breached gap loss cannot reach paper submission and a runtime
cannot start with a legacy policy that omits the three-control set.

The configured PostgreSQL runtime test now asserts the same stop/gap figures in
the immutable assessment payload, and migration 0024 validates that payload's
shape. Local verification discovers **364 tests**, passes **330** and explicitly
skips **34 PostgreSQL-only tests**. Compileall, full Ruff, the tightened
**117/117** complete-package mypy ratchet and a zero-error **34-file** critical
slice pass. PR-head run `32286392247` then verifies migration 0024, all **364
tests without skips**, the unchanged **100-table** restore, PostgreSQL restart
and immutable calculations, every quality/security/supply-chain gate, the
production build and protected browser matrix.

## Cycle 213 — Atomic Risk-Violation Operations

Cycle 213 tests migration 0025, concurrency-safe active-alert deduplication,
immutable alert status events, restart recovery and authenticated actor
transitions. PostgreSQL integration proves a final portfolio rejection opens a
warning without consuming daily notional, a kill-switch rejection opens one
critical alert, simultaneous replay creates one decision/alert/open event, and
an injected alert-event failure rolls the entire risk-decision transaction back.

Local verification discovers **367 tests**, passes **330** and explicitly skips
**37 PostgreSQL-only tests**. Compileall, full Ruff, Bandit, the **117/117**
complete-package mypy ratchet and a zero-error changed-file slice pass. PR-head
run `32288130114` verifies all **367 tests without skips**, migration 0025,
**102 restore-critical tables**, the widened zero-error **35-file** critical
slice, every quality/security/supply-chain gate, production build and protected
browser matrix.

## Cycle 214 — Simulated Paper Operations Evidence

Cycle 214 adds deterministic unit coverage for terminal-state requirements,
exact OMS fill quantity/VWAP reconciliation, BUY/SELL directional slippage,
fill ratio, realized filled-quantity shortfall, intent-to-fill latency, policy
thresholds, numeric scale and mandatory simulated-only limitations. Shadow
rehearsals preserve divergence reasons and explicit non-activation status.

Migration 0026 adds two immutable tables and expands the fresh-restore manifest
to **104 critical tables**. Hosted PostgreSQL exercises policy/content hash
binding, idempotent replay, local threshold/divergence alerts, restart reads,
direct mutation rejection and injected alert-event failure rollback. Local
verification discovers **372 tests**, passes **334**, and explicitly skips **38
PostgreSQL-only tests** because no disposable local DSN exists. Corrected
PR-head run `32290318303` runs all **372 tests without skips**, applies migration
0026, matches all **104 restored tables**, and passes compileall, full Ruff,
Bandit, dependency/SBOM/secret gates, the **117/117** mypy ratchet, zero-error
**36-file** critical slice, frontend checks, production build, smoke and the
protected browser matrix. Initial run `32290146999` correctly retained a prior
Cycle 213 alert; only the over-exclusive new integration assertion failed and
was narrowed to coexist with valid earlier evidence.
