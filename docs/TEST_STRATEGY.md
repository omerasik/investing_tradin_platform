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
