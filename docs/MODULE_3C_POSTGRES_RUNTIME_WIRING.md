# Module 3C — PostgreSQL Runtime Composition & Authority Wiring

Companion to [PRODUCTION_READINESS_MATRIX.md](PRODUCTION_READINESS_MATRIX.md) (Module 3A) and
[MODULE_3B_GOVERNANCE_SECURITY.md](MODULE_3B_GOVERNANCE_SECURITY.md). This module does not change
paper-trading behavior, does not enable live trading, does not add a market-data/news/broker
network integration, and does not mark anything production-ready.

## 1. Corrected audit model

Module 3A's matrix said, in several rows, that `build_app()` "defaults to SQLite" for the
Paper OMS, risk-decision, promotion-ledger and operational-alert authorities. That wording is
inaccurate and is corrected here and in `PRODUCTION_READINESS_MATRIX.md`:

1. `build_app()` (`src/trade_platform/api.py:272`) does **not** literally instantiate SQLite
   implementations for every optional store. Every optional store parameter (`return_history`,
   `investment_store`, `fundamental_store`, `alert_store`, `agent_research_store`, `paper_oms`,
   `risk_decisions`, `strategy_registry`, `experiment_store`, `promotion_ledger`,
   `operator_dashboard_queries`) defaults to `None`. Only `audit_store` defaults to a concrete
   implementation (`SQLiteAuditStore()`), and that is a deliberate, documented choice (see §5).
2. The module-level object `app = build_app()` at `src/trade_platform/api.py:1102` is exactly
   what the container serves: `Dockerfile`'s `CMD` (before this module) ran
   `uvicorn trade_platform.api:app`, i.e. that same unconfigured object with every optional
   authority `None`.
3. `build_postgres_paper_core()` / `build_postgres_paper_runtime()`
   (`src/trade_platform/postgres_runtime.py:126-250`) already compose a substantial PostgreSQL
   authority graph (OMS, risk, kill-switches, promotions, policies, pre-trade assessments,
   quotes, execution evidence, return history, instruments, signals, models, recovery, alerts,
   paper-operations evidence, operational jobs, retention evidence, identity security) — but
   nothing wired that graph, or any subset of it, into the object Docker serves.

**The real defect was: the default served application bypasses the PostgreSQL composition root
entirely (every optional authority is `None`), not that `build_app()` silently manufactures
SQLite authorities.** This module fixes the actual defect: a canonical composition factory that
wires the existing PostgreSQL authorities into `build_app()` for protected runtimes, and a Docker
entrypoint that targets that factory instead of the unconfigured default.

## 2. Old composition vs. new composition

| | Before Module 3C | After Module 3C |
|---|---|---|
| Docker `CMD` target | `trade_platform.api:app` | `trade_platform.runtime_app:app` |
| What that object is | `build_app()` with every optional authority `None` | `create_runtime_app_from_environment()`: reads `TRADE_PLATFORM_ENVIRONMENT`/`POSTGRES_DSN` and composes accordingly |
| `local_research` (no env set) | `build_app()`, SQLite audit, everything else absent | Unchanged: `build_app(config=PlatformConfig(environment="local_research"))` |
| `paper` / `production` | Same as above — **bug**: protected environment names had no effect on what was served | Requires `POSTGRES_DSN`; composes `PostgresPaperOms`, `PostgresOperationalAlertStore`, `PostgresOperatorDashboardQueries` and wires them into `app.state`; fails closed (raises, no app object) if PostgreSQL is missing/invalid/unreachable |
| `scripts/dev.py --reset-db --demo` | Targets `trade_platform.dev_app:create_dev_app` | Unchanged — untouched by this module |

## 3. The runtime factory

- **Location:** `src/trade_platform/runtime_app.py`
- **Entrypoint:** `create_runtime_app_from_environment(*, env=os.environ.get) -> FastAPI`
- **Modes:** `RuntimeMode.LOCAL_RESEARCH`, `RuntimeMode.PAPER`, `RuntimeMode.PRODUCTION`
  (`TRADE_PLATFORM_ENVIRONMENT`, defaults to `local_research`)

Behavior per mode:

- **LOCAL_RESEARCH** — unchanged local/dev composition: `build_app(config=PlatformConfig(environment="local_research"))`, i.e. SQLite audit store only, every other optional authority absent, exactly as today. `scripts/dev.py --reset-db --demo` (which targets `trade_platform.dev_app:create_dev_app`, a separate, already-Postgres-backed dev factory) is untouched.
- **PAPER** — requires `POSTGRES_DSN` (a `postgres://`/`postgresql://` DSN). Missing the variable raises `RuntimeCompositionError` before any app object exists. The DSN is used to open a `PostgresDatabase`, which connects eagerly in its `__post_init__` (see `persistence.py`) — an invalid DSN or unreachable server raises `PersistenceError`, converted to `RuntimeCompositionError`. Once connected, `PostgresPaperOms`, `PostgresOperationalAlertStore` and `PostgresOperatorDashboardQueries` are composed and wired into `build_app(...)`'s `paper_oms=`, `alert_store=` and `operator_dashboard_queries=` parameters.
- **PRODUCTION** — also requires PostgreSQL, but **always fails closed today**, regardless of whether a valid, reachable DSN is supplied. Production-grade operator identity, a secret manager, and a durable (non-SQLite) audit authority do not exist yet in this codebase; rather than serve a partially-wired application under the label "production", `create_runtime_app_from_environment()` raises `RuntimeCompositionError("production_mode_not_yet_supported: ...")` unconditionally. `compose_protected_postgres_app()` (the lower-level composer) does accept `RuntimeMode.PRODUCTION` and will compose successfully if called directly — the refusal lives in the environment-driven entrypoint deliberately, as the honest "this could theoretically be composed but the platform doesn't consider it ready" boundary. No test or code path here claims production readiness.

No-fallback rule (verified by tests in `tests/test_runtime_app.py`):

- Missing `POSTGRES_DSN` in `paper`/`production` → `RuntimeCompositionError`, no app object returned.
- Invalid DSN scheme (does not start with `postgres://`/`postgresql://`) → `RuntimeCompositionError`.
- Unreachable PostgreSQL host → `RuntimeCompositionError` (bounded: `PostgresDatabase` now passes `connect_timeout=10` to `psycopg.connect`, so an unreachable/firewalled host fails within ~10s rather than hanging on the OS TCP timeout — this was a real gap found while writing this module's tests and is fixed in `persistence.py`).
- There is no code path in `runtime_app.py` that falls back from PostgreSQL to SQLite or to an in-memory store for `paper`/`production`.

## 4. Protected-runtime authority graph

| Authority | Composed in | Wired at | Used by (api.py routes) |
|---|---|---|---|
| `PostgresPaperOms` | `runtime_app.compose_protected_postgres_app` | `app.state.paper_oms` | `GET /paper-oms/{intent_id}` (order/events/fills), `GET /paper-oms/accounts/{account_id}/reconciliation` |
| `PostgresOperationalAlertStore` | `runtime_app.compose_protected_postgres_app` | `app.state.alert_store` | `GET /operational-alerts`, `POST /operational-alerts/{alert_id}/acknowledge`, and the command-center's `critical-alerts` evidence state |
| `PostgresOperatorDashboardQueries` | `runtime_app.compose_protected_postgres_app` (same class already used by `dev_app.py`) | `app.state.operator_dashboard_queries` | Every `/operator-dashboard/*` route (instruments, historical datasets, data-health, strategies, experiments, investment theses/portfolios, paper orders, paper reconciliation, feature definitions/materializations, signals, risk-decisions (dashboard projection), strategy scorecards, regime runs, command-center Postgres evidence states) |

Left explicitly `None` (→ HTTP 503 "unavailable", never silently SQLite) for the protected runtime,
each with a documented reason (see also `PRODUCTION_READINESS_MATRIX.md` §2/§3):

| Authority | `app.state` field | Reason |
|---|---|---|
| Risk decisions (by intent) | `risk_decisions` | `PostgresRiskStore` has no `decisions_for_intent` projection; only `SQLiteRiskDecisionStore` does. **Future migration blocker** — a small, well-scoped follow-up (add the read method to `PostgresRiskStore`), not attempted in this module to avoid touching risk-safety code without a dedicated review. |
| Promotion ledger (by id) | `promotion_ledger` | `PostgresPromotionLedger` only implements `append`/`append_activation`; no `get(decision_id)`. **Future migration blocker**, same reasoning. |
| Return-history ingestion tracking | `return_history` | `PostgresPortfolioReturnStore` only implements `append`/`observations_as_of`; the `/return-history/*` routes need `provider_health`/`ingestion_runs`/`ingestion_commands`/`set_ingestion_cadence`/`due_ingestion_cadences`, which have no Postgres implementation. **Future migration blocker.** |
| Strategy registry, experiments, investment store, fundamental store, agent research store | `strategy_registry`, `experiment_store`, `investment_store`, `fundamental_store`, `agent_research_store` | No Postgres sibling exists at all (out of scope for this module per its hard constraints). **Not required for protected paper-API startup** — the protected app starts and serves its Postgres-backed routes; these specific research-domain routes return 503. |

None of the above is a safety-critical authority silently regressing to SQLite: every one of them
is either genuinely absent (503) in the protected graph, or Postgres-backed and verified so by
`isinstance` assertions in `tests/test_runtime_app.py`.

## 5. Audit-store decision (Step 5): Option A — fail-closed is *not* what we chose; explicit staging label was

We evaluated the two options the module specification poses:

- (A) protected runtime cannot start until a durable (non-SQLite) audit authority is supplied.
- (B) protected runtime may start in a clearly-labeled non-production staging mode with audit
  explicitly classified as a remaining blocker.

**We chose (B).** Rationale: `SQLiteAuditStore` records structured audit events (operator actions,
authorization decisions) that are useful even when not durable to the standard the platform will
eventually require, and gating the entire protected paper runtime on an audit authority that does
not exist yet would make it impossible to exercise or review any of the PostgreSQL wiring this
module adds. `PAPER` mode is explicitly a staging-shaped, non-production mode — it is never
described as production-ready anywhere in this codebase, `PRODUCTION` mode fails closed
unconditionally (§3), and `PRODUCTION_READINESS_MATRIX.md` continues to list "Audit" as
**BLOCKED** for actual production use. This module does not build a Postgres audit
implementation (explicitly out of scope) and does not claim production-grade auditability for
`PAPER` mode anywhere.

## 6. Route audit summary

Every endpoint in `api.py` reading `app.state.paper_oms`, `app.state.alert_store`, or
`app.state.operator_dashboard_queries` is, for the protected (`paper`) runtime built by
`runtime_app.py`, provably Postgres-backed: `tests/test_runtime_app.py`'s
`ProtectedRuntimePostgresCompositionTests` asserts `isinstance(app.state.paper_oms, PostgresPaperOms)`,
`isinstance(app.state.alert_store, PostgresOperationalAlertStore)`, and
`isinstance(app.state.operator_dashboard_queries, PostgresOperatorDashboardQueries)`, and further
asserts `app.state.risk_decisions`, `app.state.promotion_ledger`, `app.state.investment_store`
and `app.state.strategy_registry` are `None` (explicit unavailability) rather than silently
SQLite. Legacy `/paper-oms/*` routes (order/events/fills, reconciliation) are Postgres-backed via
`PostgresPaperOms`, consistent with Module 2B-5's existing operator-dashboard paper detail/
reconciliation Postgres wiring (`PostgresOperatorDashboardQueries`, unchanged by this module).

## 7. Startup failure rules

Summarized from §3: any of (a) unknown `TRADE_PLATFORM_ENVIRONMENT` value, (b) missing
`POSTGRES_DSN` for `paper`/`production`, (c) an invalid DSN scheme, (d) an unreachable/refused
PostgreSQL connection, or (e) any exception while composing a required PostgreSQL authority,
raises `RuntimeCompositionError` and returns no app object. `production` mode raises
unconditionally regardless of (a)-(e). There is no retry-with-SQLite, no partial app,
and no "degraded" mode that still claims to be `ready`.

## 8. Resource ownership (connection lifecycle)

`compose_protected_postgres_app()` opens exactly one `PostgresDatabase` (one `psycopg` connection)
and passes it to every authority it constructs (`PostgresPaperOms`, `PostgresOperationalAlertStore`,
`PostgresOperatorDashboardQueries`) — matching the existing convention in
`postgres_runtime.build_postgres_paper_core()`, where "every adapter shares this connection; the
composition root owns its lifetime and closes it exactly once." The composition root:

- Wraps `ProtectedPostgresAuthorities` (a small holder with `.close()`) around the shared
  connection and the three authorities.
- Stores it at `app.state.postgres_authorities` for introspection/testing.
- Registers a shutdown handler (`app.on_event("shutdown")`) that calls `authorities.close()`
  exactly once, so the process does not leak the connection on graceful shutdown.
- On any composition failure after the connection opens (e.g. an authority constructor raises),
  closes the connection before re-raising — no leaked connection on the failure path either.

`PostgresDatabase.__post_init__` (`persistence.py`) now passes `connect_timeout=10` to
`psycopg.connect`, so an unreachable host fails within a bounded time instead of hanging on the
OS-level TCP timeout (this was previously unbounded and is a small, low-risk fix applicable
everywhere `PostgresDatabase` is used, not just this module).

## 9. Docker entrypoint change

- **Before:** `CMD ["python", "-m", "uvicorn", "trade_platform.api:app", ...]`
- **After:** `CMD ["python", "-m", "uvicorn", "trade_platform.runtime_app:app", ...]`
- `HEALTHCHECK` is unchanged: it still probes `/health/live`, which remains a cheap,
  database-free liveness check (see `api.py`'s `liveness()` docstring). Readiness
  (`/health/ready`, pre-existing endpoint, now enhanced — see below) is intentionally not used
  for the container `HEALTHCHECK`, matching the module's requirement that liveness stay cheap.
- `compose.dev.yml` only defines the disposable dev PostgreSQL service (no app container); it is
  unchanged. `scripts/dev.py` already targets `trade_platform.dev_app:create_dev_app` for local
  Postgres-backed dev flows and `--reset-db --demo`; unchanged.

## 10. `/health/ready`

`/health/ready` already existed in `api.py` prior to this module (returning
`{"status": "ready", "environment", "paper_trading_enabled", "live_trading_enabled": false}`).
This module makes it fail closed for protected deployments: if `platform_config.environment` is
`paper`/`production`, it now additionally requires `app.state.paper_oms` and
`app.state.operator_dashboard_queries` to be present and performs one cheap, already-bounded
Postgres read (`operator_dashboard_queries.workspace_references()`), returning HTTP 503 on
`DashboardQueryError` or a missing authority. `local_research` mode is unaffected (always ready,
no Postgres dependency). `/health/live` is untouched and remains a pure process-health check with
no database access, as required.

## 11. Test coverage added

`tests/test_runtime_app.py`:

- `LocalResearchModeTests` — default/explicit `local_research` never requires Postgres.
- `PersistenceTargetRejectionTests` — unknown environment name rejected; `paper` without
  `POSTGRES_DSN` fails closed; `production` always fails closed today (even with a valid DSN);
  `paper` with an unreachable DSN fails closed (not SQLite); `compose_protected_postgres_app`
  rejects `LOCAL_RESEARCH`.
- `DatabaseUnavailableStartupTests` — invalid DSN scheme fails closed.
- `ProtectedRuntimePostgresCompositionTests` (gated on `POSTGRES_TEST_DSN`, mirrors
  `tests/test_postgres_integration.py`'s setup pattern: Alembic `upgrade head` against the
  disposable CI/local Postgres service) —
  - composes the protected runtime, asserts `isinstance` on every safety-critical authority,
    asserts the explicitly-`None` authorities, starts the API via `TestClient`, hits
    `/health/live` and `/health/ready` (200), and reads `/operator-dashboard/command-center`
    with a bearer token, confirming `live_trading_enabled` is `False`;
  - closes the underlying connection and confirms `/health/ready` fails closed (503) rather than
    hanging or silently returning 200.

Also touched: `src/trade_platform/api.py` widened `paper_oms`/`alert_store` parameter types to
accept their Postgres siblings (`SQLitePaperOms | PostgresPaperOms | None`,
`SQLiteOperationalAlertStore | PostgresOperationalAlertStore | None`) and enhanced the existing
`/health/ready` handler; no existing test was weakened. `tests/test_api.py` passes unchanged.

## 12. Local verification performed

Run from this worktree (`Windows`/PowerShell), against the repo's existing disposable dev
PostgreSQL container (`compose.dev.yml`, port 5439):

- `python -m compileall src tests migrations scripts` — clean.
- `python -m unittest tests.test_api tests.test_runtime_app -v` — 36 tests, all pass (2 of
  `test_runtime_app`'s skip locally without `POSTGRES_TEST_DSN`; unittest discovery of the full
  suite was also attempted — see the PR body/final report for what ran end-to-end).
- `python -m unittest tests.test_runtime_app -v` with `POSTGRES_TEST_DSN` pointed at the local
  Postgres container and Alembic migrated to `head` — 10/10 pass, including the
  Postgres-authority composition and fail-closed readiness tests.

CI (`verify`/`codeql` GitHub Actions workflows) is the authoritative signal for anything not
reproducible on this host (ruff/mypy/bandit baselines, pip-audit, TypeScript/ESLint/Next build,
E2E/cycle208) — see the PR for run links and status; no local result here should be read as a
substitute for a green CI run.

## 13. Remaining blockers (explicitly not solved by this module)

- **Production identity** — no per-user credential system; static bearer tokens only.
- **Secret manager** — environment variables only; no KMS/Vault/Secrets Manager integration.
- **Production-grade audit durability** — `SQLiteAuditStore` remains the only audit
  implementation; no Postgres audit store exists. See §5.
- **Risk-decisions-by-intent / promotion-ledger-by-id / return-history ingestion tracking** —
  Postgres siblings exist but lack the specific read methods these three legacy routes need
  (§4). Small, well-scoped follow-ups.
- **Research-domain SQLite stores** (`SQLiteInvestmentStore`, `SQLiteFundamentalStore`,
  `SQLiteStrategyRegistry`, `SQLiteExperimentStore`, `SQLiteAgentResearchStore`,
  `SQLiteFeatureStore`) — out of scope for this module per its hard constraints; unavailable
  (not silently SQLite) in the protected runtime.
- **`PRODUCTION` mode** — deliberately not runnable at all yet (§3); this is a documented,
  intentional refusal, not an oversight.

Nothing in this module claims production readiness anywhere; `PRODUCTION_READINESS_MATRIX.md` is
updated only for the rows this module actually changed.
