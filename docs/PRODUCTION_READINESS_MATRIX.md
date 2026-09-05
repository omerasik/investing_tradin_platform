# Production Readiness Matrix — Module 3A

Companion to [MODULE_3A_PRODUCTION_READINESS_AUDIT.md](MODULE_3A_PRODUCTION_READINESS_AUDIT.md). Verified against `main@11a028c9fb5fc4332113e70e3ae63db1f74c58e0` on 2026-09-05.

> **Module 3C correction (see [MODULE_3C_POSTGRES_RUNTIME_WIRING.md](MODULE_3C_POSTGRES_RUNTIME_WIRING.md)):**
> Several rows below originally said `build_app()` "defaults to SQLite" for the Paper OMS,
> risk decisions, promotion ledger and operational alerts authorities. That wording was
> inaccurate and has been corrected: `build_app()`'s optional store parameters default to
> `None`, not to a SQLite implementation — `build_app()` itself never silently constructs
> any SQLite authority on the caller's behalf. The real defect was that nothing composed
> the existing PostgreSQL authority graph (`postgres_runtime.py`) and handed it to
> `build_app()` for the object Docker actually served (`app = build_app()` in `api.py`,
> run via `Dockerfile`'s `CMD`) — the served application simply had every optional
> authority unset. Module 3C adds `trade_platform.runtime_app`, a canonical composition
> factory that wires the PostgreSQL authority graph into `build_app()` for protected
> (paper/production) runtimes and fails closed if PostgreSQL is missing or unreachable,
> and repoints the Docker entrypoint at it. Rows and the wiring-gap list below are updated
> to reflect what Module 3C actually wired versus what remains a genuine gap.

> **Module 3D update (see [MODULE_3D_PRODUCTION_IDENTITY_SECRETS_AUDIT.md](MODULE_3D_PRODUCTION_IDENTITY_SECRETS_AUDIT.md)):**
> `production` mode previously refused to start unconditionally because production
> identity, a secret-management boundary, and a durable Postgres audit authority did
> not exist. Module 3D implements all three (`oidc_identity.py`, `secrets_manager.py`,
> `postgres_audit.py`, plus durable session revocation and CSRF protection) and updates
> `trade_platform.runtime_app` so `production` starts only once every one of them is
> actually configured — never partially wired. Paper's authentication, RBAC, and
> secrets behavior is unchanged; the Authentication, Authorization/RBAC, Secrets, and
> Audit rows below are updated to distinguish paper (unchanged) from production (new).

## 1. Domain Authority Map

| Domain | Authoritative store | Read authority | Write authority | Immutability | Environment | Current production fitness | Known limitation |
|---|---|---|---|---|---|---|---|
| Instruments | `PostgresInstrumentStore` (postgres_decision_authorities.py:38) | API, decision engines | Same store, admin ingestion | Mutable reference data | Prod-capable; `SQLiteInstrumentStore` (instruments.py:82) is dev/test default | ACCEPTABLE | Two parallel implementations must stay schema-consistent; no shared interface enforced in code |
| Historical datasets | `PostgresHistoricalIngestionCheckpointStore` (provider_ingestion.py:135); bars via `SQLiteBarStore` (market_data.py:83) | Backtest/research engines | Ingestion pipeline | Append-only checkpoints | Checkpoint store is Postgres/prod; bar store has no Postgres equivalent | PARTIAL | Bar data has no confirmed prod-capable store |
| Data Health | `PostgresDataHealthStore` (data_health.py:252) | Operator dashboard, gating checks | Data-health pipeline | Append-only evidence | Postgres-only, no SQLite dev variant | ACCEPTABLE | Cannot run checks against local SQLite dev DB |
| Fundamentals | `SQLiteFundamentalStore` (fundamentals.py:55) raw; `PostgresPitFundamentalStore` (pit_fundamentals.py:164) PIT | Research/backtest (PIT); ingestion (raw) | Ingestion / restatement recorder | PIT store append-only | PIT store prod-capable; raw store SQLite-only | PARTIAL | Ambiguous which store is authoritative for a given consumer |
| Macro | `SQLiteMacroReleaseStore` (macro_data.py:44) raw; `PostgresPitMacroStore` (pit_macro.py:90) PIT | Research (PIT reads) | Ingestion | PIT append-only | Postgres PIT is prod path | PARTIAL | Same raw/PIT split as fundamentals |
| Features | `SQLiteFeatureStore` (feature_platform.py:75) | Model training/backtest | Feature pipeline | Mutable/versioned | SQLite only — no Postgres implementation | MUST MIGRATE | Not prod-capable as-is |
| Strategies | `SQLiteStrategyRegistry` (strategy_validation.py:53) | Research UI, promotion gate | Registry writes | Mutable | SQLite only | MUST MIGRATE | No durable prod authority |
| Experiments | `SQLiteExperimentStore` (research.py:186) | Research UI, promotion gate | Backtest runner | Append-only per experiment | SQLite only | MUST MIGRATE | No durable prod authority |
| Scorecards | `PostgresStrategyScorecardStore` (strategy_scorecard_v2.py:272) | Operator dashboard, governance | Scorecard evaluator | Append-only, content-hashed | Postgres-only, prod-capable | ACCEPTABLE | Depends on upstream experiment data which is SQLite-only |
| Regimes | `PostgresRegimeEngineV2Store` (regime_engine_v2.py:696) | Portfolio construction, scorecards | Regime engine | Append-only, hashed | Postgres, prod-capable | ACCEPTABLE | None material found |
| Portfolio construction | `PostgresPortfolioConstructionV2Store` (portfolio_construction_v2.py:864) | Operator dashboard, risk | Portfolio construction engine | Append-only, hashed | Postgres, prod-capable | ACCEPTABLE | None material found |
| Signals | `SQLiteSignalStore` (signal_engine.py:154) dev; `PostgresSignalStore` (postgres_decision_authorities.py:193) prod | Risk/OMS pretrade checks | Signal engine | Mixed by implementation | Postgres covers only the validated-signal slice | PARTIAL | Full signal lifecycle not yet Postgres-covered |
| Risk | `PostgresRiskStore` (risk.py:393); `SQLiteRiskDecisionStore` (risk.py:620) dev | Pretrade assessment, OMS | Risk engine, reservation repository | Reservations append-only, advisory-locked | Postgres required for paper/production by `PlatformConfig` | ACCEPTABLE | SQLite decision store is test-only by construction |
| Reservations | `PostgresCriticalRepository.reserve_and_record_decision` (postgres_repositories.py:39-67) | Risk/OMS | Same (idempotent, advisory-locked) | Insert-only, idempotency-checked | Postgres-only | ACCEPTABLE | No local/dev path — correct by design for this boundary |
| Paper OMS | `SQLitePaperOms` (paper_oms.py:64) dev; `PostgresPaperOms` (postgres_paper_oms.py:30) prod | api.py order/read endpoints | OMS engine only | Event-sourced | Postgres required in paper/production per config, now composed and wired by `trade_platform.runtime_app` for protected runtimes (Module 3C) | ACCEPTABLE | Resolved by Module 3C — see `docs/MODULE_3C_POSTGRES_RUNTIME_WIRING.md` |
| Reconciliation | `SQLiteReconciledAccountStore`/`PositionStore` (portfolio_evidence.py:71,182) dev; `PostgresPaperOms.latest_reconciled_account/...` (postgres_paper_oms.py:381,419) prod | Operator dashboard | Broker sync / OMS reconciliation job | Snapshot-based | Split authority between dedicated SQLite store and Postgres-OMS-embedded methods | PARTIAL | No single consistent interface across environments |
| Investments | `SQLiteInvestmentStore` (investments.py:382) dev; `PostgresInvestmentEngineV2Store` (investment_engine_v2.py:774) prod | Operator dashboard, engine v2 | Investment engine | Append-only (v2) | Postgres v2 is the prod-capable engine | PARTIAL | Legacy SQLite store and Engine V2 coexist; verify prod callers target v2 |
| News | `SQLiteNewsEventStore` (market_intelligence.py:72) metadata; `PostgresNewsEventIntelligenceStore` (news_event_intelligence.py:704) prod | Research agents (internal retrieval sources) | Ingestion pipeline | Append-only, unique(source_id, source_item_id) | Postgres store is prod path | ACCEPTABLE | No live content acquisition wired (by design at this stage) |
| SRE | `PostgresSreV2Store` (observability_sre_v2.py:480) | Operator dashboard, incident review | SRE evidence pipeline | Append-only, hashed | Postgres-only | ACCEPTABLE | No SQLite/dev equivalent |
| Audit events | `SQLiteAuditStore` (audit.py:22) | api.py audit endpoints | Any service via `.append()` | Append-only, no update/delete exposed | **Explicitly dev/paper-only per its own docstring** | **MUST MIGRATE** | No Postgres audit store exists at all — production-grade immutable audit trail is a real gap |
| Alerts | `SQLiteOperationalAlertStore` (operational_alerts.py:313) dev; `PostgresOperationalAlertStore` (operational_alerts.py:56) prod; `SQLiteFailureDrillStore` (operational_alerts.py:374) | Operator dashboard, SRE | Alerting engine | Append-only | Postgres store is prod-capable and is now wired for protected runtimes by `trade_platform.runtime_app` (Module 3C) | PARTIAL | Failure-drill evidence still has no Postgres counterpart (unchanged by Module 3C) |

**Cross-cutting note:** `PlatformConfig` (config.py:30-49) defaults to SQLite/`:memory:` but its `__post_init__` forbids anything but Postgres for `environment in {"paper","production"}` and unconditionally forbids `live_trading_enabled=True`. Wherever this table shows both a SQLite and Postgres implementation, Postgres is the config-enforced runtime intent for any non-local environment. **Corrected (Module 3C):** `build_app()`'s optional store parameters default to `None`, not to a SQLite implementation, and `build_app()` never reads `PlatformConfig` to auto-select a store — the previously highest-leverage gap was that nothing composed the PostgreSQL authority graph and handed it to `build_app()` for the object the container actually served. `trade_platform.runtime_app.create_runtime_app_from_environment()` now does exactly that for protected (paper/production) runtimes, and the Dockerfile's `CMD` now targets it instead of the unconfigured `trade_platform.api:app`.

## 2. Remaining SQLite Authorities (production-blocking, no Postgres sibling)

- Audit events — `SQLiteAuditStore` (audit.py:22) (Module 3C decision: fail-closed for protected runtime; see `docs/MODULE_3C_POSTGRES_RUNTIME_WIRING.md` §5)
- Investment detail (legacy shape) — `SQLiteInvestmentStore`, `SQLiteFundamentalStore` (not required for protected paper-API startup; unavailable in protected runtime per Module 3C)
- Strategy registry — `SQLiteStrategyRegistry` (strategy_validation.py:53) (not required for protected paper-API startup; unavailable in protected runtime per Module 3C)
- Experiment registry — `SQLiteExperimentStore` (research.py:186) (not required for protected paper-API startup; unavailable in protected runtime per Module 3C)
- AI/agent research — `SQLiteAgentResearchStore` (agent_research.py:235) (not required for protected paper-API startup; unavailable in protected runtime per Module 3C)
- Feature platform — `SQLiteFeatureStore` (feature_platform.py:75)
- Full signal lifecycle beyond the validated-signal slice — `SQLiteSignalStore` (signal_engine.py:154)

## 3. Wiring gap — Module 3C status

Resolved (wired into the protected `trade_platform.runtime_app` composition; safety-critical, isinstance-verified Postgres in tests):

- Paper OMS — `PostgresPaperOms`, wired via `app.state.paper_oms`
- Operational alerts — `PostgresOperationalAlertStore`, wired via `app.state.alert_store`
- Operator dashboard queries — `PostgresOperatorDashboardQueries`, wired via `app.state.operator_dashboard_queries` (unchanged from `dev_app.py`, now also the Docker-served default for protected runtimes)

Still a genuine gap — explicitly `None`/unavailable (HTTP 503) in the protected runtime, never silently SQLite, classified as **future migration blocker** (a Postgres sibling exists but its read interface does not yet cover what the legacy `api.py` route needs):

- Risk decisions by intent — `PostgresRiskStore` exists and is used by pretrade/OMS assessment, but has no `decisions_for_intent` projection the `/paper-oms/{intent_id}` route needs (only `SQLiteRiskDecisionStore` has it)
- Promotion ledger by-id read — `PostgresPromotionLedger` exists (append/append_activation) but has no `get(decision_id)` projection the `/promotion-decisions/{decision_id}` route needs (only `SQLitePromotionLedger` has it)
- Return-history ingestion tracking — `PostgresPortfolioReturnStore` exists but only implements `append`/`observations_as_of`; it has no equivalent of `SQLitePortfolioReturnStore`'s `provider_health`/`ingestion_runs`/`ingestion_commands`/`set_ingestion_cadence`/`due_ingestion_cadences` used by the `/return-history/*` routes
- Instrument/model-registry/pre-trade/policy/kill-switch/quote stores — Postgres siblings exist and are composed via `build_postgres_paper_runtime` for the paper-submission workflow (not the read-only `api.py` HTTP surface, which does not expose them as routes)

## 4. Production Readiness Scorecard

| Category | Status | Evidence |
|---|---|---|
| Repository governance | PARTIAL | Module 3B added CODEOWNERS, Dependabot, CodeQL, `SECURITY.md`, `CONTRIBUTING.md`, and a PR template. Branch protection and private vulnerability reporting remain disabled (verified via `gh api`) — see `docs/MODULE_3B_GOVERNANCE_SECURITY.md` §14 |
| Authentication | PARTIAL (paper) / ACCEPTABLE (production, when configured) | Paper still uses the static local-bearer boundary unchanged from Module 3A/3B (`security.py:212-222`) — a deliberate, documented dev/paper mechanism, not a regression. Module 3D adds a real JWKS-verified external identity path (`oidc_identity.py`) with durable session revocation (`external_identity.py:PostgresSessionRevocationStore`), required for `production` to start at all — see `docs/MODULE_3D_PRODUCTION_IDENTITY_SECRETS_AUDIT.md` |
| Authorization / RBAC | PARTIAL (paper) / ACCEPTABLE (production, when configured) | 6 roles/permissions defined backend-side (`security.py:59-98`); paper still activates exactly one static role per deployment. Production maps external-identity groups to roles via an approved, durably-stored `ExternalIdentityMappingPolicy`, loaded at startup by policy name (`external_identity.py:latest_enabled_policy`); no frontend role logic yet |
| Secrets | PARTIAL (paper) / ACCEPTABLE (production, when configured) | Paper still resolves secrets from environment variables. Module 3D adds a real secret-management boundary (`secrets_manager.py`) with a production-capable `FileSecretProvider` (one file per secret, POSIX-permission-enforced) speaking the on-disk contract mainstream secret managers already populate; production composition never accepts the environment-variable provider |
| Database | PARTIAL | Postgres schema mature (37+ migrations). Module 3C wired the served application's Paper OMS, alert and operator-dashboard authorities to PostgreSQL for protected runtimes with no SQLite fallback; risk-decision, promotion-ledger and return-history read routes remain explicitly unavailable pending additional Postgres query methods (see §3 above) |
| Backup / DR | PARTIAL | CI proves restore mechanics (`verify_postgres_restore.py`); no production RPO/RTO, offsite storage, or real drill — confirmed by the repo's own `DISASTER_RECOVERY.md` |
| Deployment | NOT STARTED | No Kubernetes/Terraform/Railway/Procfile/systemd found anywhere; CI-built container image is never pushed or deployed |
| Observability | PARTIAL | In-process metrics counter + structured logging only; no export format, no tracing, no external alert delivery (`LOCAL_OUTBOX` only) |
| Scheduler / workers | NOT STARTED | Job policy/evidence model exists (`operational_jobs.py`) but nothing executes it on a cadence; confirmed human/CI-script dependency |
| Market data | ARCHITECTURE READY | Adapter interface, raw capture, checkpointing, Data Health, PIT, sealing all exist; only a public unauthenticated CSV source is wired, no commercial vendor |
| Fundamentals | ARCHITECTURE READY | PIT store + auth/terms model exist; no raw capture, no checkpoint, no dedicated health checks |
| Macro | ARCHITECTURE READY | Same shape as fundamentals; self-documented as fixture-backed |
| News | ARCHITECTURE READY | Metadata store + license-approval gate exist; no content acquisition by design at this stage |
| Research (quant) | ACCEPTABLE (v2 engines) / PARTIAL (legacy registries) | v2 regime/portfolio/scorecard engines are Postgres-native and hashed; strategy/experiment registries are SQLite-only |
| Investment (research) | PARTIAL | Engine V2 is Postgres-native; legacy investment store coexists and is SQLite-only |
| Deterministic risk | ACCEPTABLE | `PostgresRiskStore` + advisory-locked idempotent reservations; config-enforced Postgres-only outside local dev |
| Paper OMS | ACCEPTABLE | Design is mature (event-sourced, idempotent, reconciled); Module 3C composes and wires `PostgresPaperOms` into the served protected-runtime application with fail-closed startup if PostgreSQL is unavailable |
| Broker sandbox | NOT STARTED | Only an in-memory simulator exists; no network-connected adapter, no real credentials model |
| Reconciliation | PARTIAL | Implemented for paper-internal flows; authority split between a dedicated SQLite store and Postgres-OMS-embedded methods |
| Audit | PARTIAL (paper stays SQLite by design) / ACCEPTABLE (production) | Module 3D adds `postgres_audit.py:PostgresAuditStore`: append-only, content-hashed, immutable at the schema level, structurally interchangeable with `SQLiteAuditStore` via the new `audit.py:AuditStore` protocol. Production composition uses it unconditionally; paper intentionally keeps `SQLiteAuditStore` (dev/paper simulation is exactly what it was scoped for) |
| Frontend | READY (with minor exceptions) | All 16 claimed workspaces exist and are professionalized; Signals page is thinner than the rest |
| Compliance / licensing | PARTIAL | Provider terms/license-acceptance fields exist structurally across all data domains; nothing has been legally reviewed or activated yet |
| Live readiness | **NOT APPLICABLE / CORRECTLY BLOCKED** | Live trading is forbidden at four independent, verified layers (config constructor, secondary runtime guard, hardcoded API response, frontend type + E2E assertions) |

## 5. Live-Trading Path Audit Result

**LIVE EXECUTION PATHS FOUND: NONE.**

Verified independently via exhaustive grep across `src/`, `web/`, `tests/`, config files, and CI, plus code-path tracing of every enum touching order/execution modes. Full detail and citations in the main audit document §3.
