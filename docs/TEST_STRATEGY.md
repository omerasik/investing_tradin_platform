# Test Strategy

Use unit, integration, contract, property, data-quality, backtest-regression, risk, security, failure-injection, restore and UI tests. Golden data and deterministic runs are required. Critical invariants: no order bypasses risk; expired signals and disabled strategies cannot create orders; duplicate intents cannot create exposure; stale data/reconciliation failures block risk increases; live trading remains impossible.

The CI suite provisions ephemeral PostgreSQL, applies Alembic migrations, and
runs migration/immutable-schema integration coverage when `POSTGRES_TEST_DSN`
is supplied. It also runs compile, deterministic unit tests, scoped Ruff/mypy,
Bandit, dependency audit, SBOM generation, TypeScript checking and the built
dashboard smoke workflow. Local machines without a running PostgreSQL service
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
