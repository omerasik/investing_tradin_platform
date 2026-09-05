# Module 3A — Post-Frontend Architecture & Production Readiness Re-Audit

**Audit date:** 2026-09-05
**Verified baseline:** `main@11a028c9fb5fc4332113e70e3ae63db1f74c58e0` (PR #55 merged; exact-main CI `33979810444` SUCCESS)
**Scope:** Architecture and production-readiness audit only. No market-data/news provider activation, no broker integration, no production deployment, no live trading, no broad refactors. This module produces documentation and a readiness inventory only.

---

## 0. Primary Question

> If we wanted to move this platform from a highly developed local/CI research system into a secure staged production environment using real authorized data and later broker sandbox/shadow execution, what exactly is still missing?

**Short answer:** The domain logic (risk, paper OMS, data provenance, evidence classification) is unusually mature and safety-conscious for this stage. What is missing is almost entirely *outside* the domain layer:

1. **Deployment target does not exist** — no container registry push, no IaC, no environment topology, no scheduler daemon. Everything currently runs only inside GitHub Actions CI or a developer's machine.
2. **The live API app (`build_app()` in `src/trade_platform/api.py`) is wired to SQLite by default for several safety-relevant authorities** (audit, investment detail, strategy/experiment registries, agent research, and — critically — Paper OMS, risk decisions, promotion ledger, operational alerts) even though CI-verified Postgres siblings exist for most of them. `PlatformConfig` *forbids* SQLite in `paper`/`production` environments at the config level (`config.py:46-47`), but `build_app()`'s default constructor arguments do not consult that enforcement — a real operator could start the shipped app in a way that never intersects the config guard. This is a wiring/composition gap, not a missing-implementation gap, but it is production-blocking.
3. **Identity is single-operator, not multi-user.** Both the frontend "dashboard view token" and the backend "operator token" are static, shared secrets read from environment variables, not per-user credentials. RBAC exists as backend machinery (6 roles, `security.py:59-98`) but there's no way today to actually have more than one distinguishable human operator — the external-identity module (`external_identity.py`) is an explicit, documented stub with "a deployment must provide signature/key/issuer validation" left as a TODO.
4. **No production scheduler.** Every ingestion / Data Health / reconciliation / retention job is a Python module or CLI script invoked by tests or by a human — there is no APScheduler/Celery/cron daemon that would run these in a real environment.
5. **No real data provider is wired for market data, fundamentals, macro, or news** — this is intentional and confirmed correct for Module 3A (none should be activated), but it also means "provider activation" work is still fully ahead of us, not partially done.
6. **Backup/DR evidence is CI-only.** The repo's own `docs/DISASTER_RECOVERY.md` already says this honestly: CI proves restore mechanics (dump/corrupt-reject/restore/reconcile) but there is no production RPO/RTO, no offsite/encrypted retention, no real environment to drill against.

Everything above is qualified by one very good fact, independently re-verified in this audit: **there is no way, today, for this system to submit a live order.** The live-trading prohibition is enforced redundantly at four independent code layers (see §16).

---

## 1. Re-Audit By Domain

### 1.1 Platform Foundation

- **CI/CD** — One workflow, `.github/workflows/verify.yml`. It runs: Alembic migrations, full Python unit suite, a Postgres backup/corrupt/restore/reconciliation drill (`scripts/verify_postgres_restore.py`), `ruff`/mypy ratchet/`bandit`, `pip-audit` + CycloneDX SBOM, `detect-secrets` scanning, a hardened Docker build + container smoke test (health/readiness/RBAC), Trivy scan + SLSA/Sigstore attestation, a full frontend build (tsc/lint/tests/`pnpm audit`/license check/`next build`), and Playwright E2E against a real Postgres-backed stack. This is a strong **PR gate**, but there is **no separate release/tag/CD workflow** — nothing here ever deploys anywhere.
- **Containerization** — `Dockerfile` is a deliberately scoped, single-stage, non-root (`uid/gid 10001`), digest-pinned image with a `/health/live` `HEALTHCHECK`. It is explicitly documented (`docs/DEPLOYMENT_RUNBOOK.md`) as a "research API artifact," not a production recipe. `compose.dev.yml` only provisions local Postgres — there is no full-stack (app + web + reverse proxy) compose file.
- **Environment configuration** — `PlatformConfig.environment` (`config.py:30-49`) is a plain string (default `"local_research"`) that gates two things at construction time: Postgres-required for `paper`/`production`, and an unconditional `LiveTradingForbiddenError` if `live_trading_enabled=True` is ever passed. There is no environment-specific settings loader (no `.env.production`, no per-stage config module).
- **Secrets** — All secrets are environment variables (`TRADE_PLATFORM_OPERATOR_TOKEN`, `TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN`, `TRADE_PLATFORM_SESSION_SECRET`, Postgres DSN). CI injects literal test values as plain env (not `${{ secrets.* }}` for most of them). No Vault/AWS Secrets Manager/GCP Secret Manager integration exists.
- **Runtime startup / persistence** — `app = build_app()` (`api.py:1102`) is what a production `uvicorn` invocation would serve, and `build_app()` (`api.py:272-290`) defaults **every** store parameter to a SQLite class. See §1.2 for the full breakdown — this is the single most important finding of this audit.
- **Health/readiness** — `GET /health/live` and `GET /health/ready` exist (`api.py:328-331, 873-881`). `/health/ready` reports config state (`environment`, `paper_trading_enabled`, `live_trading_enabled`) but does **not** ping the database or downstream dependencies — it is a config-readiness check, not a dependency-health check.
- **Migrations** — Alembic, 36 files as of `20260830_0036_historical_analogue_evidence.py`. CI runs `alembic upgrade head` plus the backup/restore drill. `docs/DISASTER_RECOVERY.md` documents migrations as forward-only in production (no schema downgrades against a database holding evidence) — a deliberate policy, not an oversight.

**Verdict:** PARTIAL. CI engineering is genuinely strong; there is simply no deployment target and the live app's default wiring does not match the platform's own safety intent for persistence.

### 1.2 SQLite ↔ Postgres Authority Split (critical section)

`build_app()`'s default arguments wire the **live served app** to SQLite for: `SQLiteAuditStore`, `SQLiteInvestmentStore`, `SQLiteFundamentalStore`, `SQLiteOperationalAlertStore`, `SQLiteAgentResearchStore`, `SQLitePaperOms`, `SQLiteRiskDecisionStore`, `SQLiteStrategyRegistry`, `SQLiteExperimentStore`, `SQLitePromotionLedger`. A separate, non-default composition path (`build_postgres_paper_runtime` / `build_postgres_paper_core` in `postgres_runtime.py`) does wire most of these to CI-verified Postgres equivalents, but nothing calls that path from `api.py`'s module-level `app` object — only `scripts/serve_postgres_dashboard_evidence.py` and some tests use it.

This means the existing `docs/POSTGRES_RUNTIME_AUTHORITY_MATRIX.md` is accurate about the *postgres runtime composition function* but does not reflect what the actual served application defaults to.

Full store-by-store table: see [PRODUCTION_READINESS_MATRIX.md](PRODUCTION_READINESS_MATRIX.md).

**Genuinely MUST MIGRATE (no Postgres implementation exists at all today):**
- Audit events (`SQLiteAuditStore`, `audit.py:22`) — no Postgres sibling, no migration. `audit.py`'s own docstring says it is scoped "for development and paper simulation."
- Investment detail (`SQLiteInvestmentStore`, `SQLiteFundamentalStore`) — no Postgres sibling for these exact shapes (a different-shaped `PostgresInvestmentEngineV2Store` exists and is the correct prod path going forward, but the legacy store has no direct migration).
- Strategy/experiment registries (`SQLiteStrategyRegistry`, `SQLiteExperimentStore`) — no Postgres sibling.
- AI/agent research (`SQLiteAgentResearchStore`) — no Postgres sibling.
- Feature platform (`SQLiteFeatureStore`, `feature_platform.py:75`) — no Postgres sibling found anywhere.
- Full signal lifecycle (`SQLiteSignalStore` beyond the validated-signal slice already covered by `PostgresSignalStore`).

**Wiring gap only (Postgres implementation exists, CI-verified, just not the `build_app()` default):**
- Paper OMS (`PostgresPaperOms`), risk decisions (`PostgresRiskStore`), promotion ledger (`PostgresPromotionLedger`), operational alerts (`PostgresOperationalAlertStore`), instrument/model-registry/pre-trade/policy/kill-switch/quote stores (already Postgres-composed per the existing runtime-authority doc).

**Correctly Postgres-native (no SQLite competitor, no migration needed):** `PostgresOperationalJobStore`, `PostgresScheduledAgentWorkflowStore`, `PostgresDataHealthStore`, `PostgresSreV2Store`, `PostgresPortfolioConstructionV2Store`, `PostgresRegimeEngineV2Store`, `PostgresStrategyScorecardStore`.

**Why this matters more than a typical "some SQLite left" finding:** `PlatformConfig.__post_init__` already enforces "Postgres or bust" for `paper`/`production` environments at the config-object level. But `build_app()` does not require or consult a `PlatformConfig` before choosing its stores — the enforcement and the instantiation are two different code paths that currently don't talk to each other. Closing this gap (making `build_app()` refuse to default to SQLite when `environment != "local_research"`, or simply changing its defaults for the wiring-gap-only stores) is a small, well-scoped P0 fix, not a rewrite.

### 1.3 Identity / Security

Full detail in the security subagent's findings; summary:

| Control | State | Evidence |
|---|---|---|
| Authentication | PARTIAL — static shared-secret tokens, not per-user | `web/app/session.ts:42,63-96`, `src/trade_platform/security.py:212-222` |
| OIDC/SSO | ABSENT (explicit scaffold only) | `external_identity.py:1-6,46-49` |
| RBAC | PARTIAL — 6 roles/permissions defined backend-side, but exactly one static role is active per deployment; frontend has none | `security.py:59-98,199-210`; zero role logic in `web/app` |
| Session | PARTIAL — HMAC-signed, 8h expiry, `httpOnly`/`sameSite=lax`, `secure` only if HTTPS-detected; no rotation/revocation | `web/app/session.ts:3-4,63-96,153-166` |
| CSRF | ABSENT — no token/double-submit/Origin check; relies on `sameSite=lax` + Bearer-required mutation endpoints | repo-wide grep, no code hits |
| Secret management | PARTIAL — env vars only, no secret manager; `detect-secrets` guards leakage, doesn't manage secrets | `security.py:207-208`, `.github/workflows/verify.yml:68-69,262` |
| Operator token model | PARTIAL — static shared bearer token, rate-limited by IP, never rotated by the app | `security.py:207,216,225-238` |
| Auditability | READY (for what it covers) — actor is always server-derived from the authenticated principal, client-supplied actor fields are discarded and tested | `api.py:885,888`, `security.py:147-189,324` |
| Encryption (app-level) | ABSENT — no TLS enforcement in app config beyond conditional HSTS header; no at-rest/backup encryption in code | `security.py:48-51`; `docs/KNOWN_LIMITATIONS.md:251,256` |
| CSP | READY — strict backend CSP + nonce-based frontend CSP with `strict-dynamic`/`frame-ancestors 'none'` | `security.py:24-27,44-52`; `web/proxy.ts:8-29` |
| Dependency/image security | PARTIAL — strong scanning (`bandit`, `pip-audit`, `pnpm audit`, Trivy, SBOM, SLSA attestation) but **no Dependabot/Renovate config**, no CodeQL | `verify.yml:62-183,198`; no `.github/dependabot.yml` found |

### 1.4 Data (Market, Fundamentals, Macro, News)

All four domains are **ARCHITECTURE READY, not provider-activated** — confirmed correct and intentional:

| Domain | Adapter interface | Real vendor wired | Auth/terms model | Raw capture | Checkpoint | Data Health | PIT | Sealing |
|---|---|---|---|---|---|---|---|---|
| Market data | Yes (`data_providers.py:152-168`) | No — only `StooqDailyCsvProvider` (public CSV) | Yes | Yes | Yes | Yes (11-check enum) | Yes | Yes |
| Fundamentals | Partial (PIT store only) | No | Yes | No | No | No dedicated checks | Yes | No |
| Macro | Partial (PIT store only) | No | Yes | No | No | No dedicated checks | Yes | No |
| News | Minimal (metadata store) | No | Yes (license-approval flag) | No (by design — "no content acquisition belongs here") | No | No | Yes | No |

No real commercial vendor name (Polygon, Alpaca-as-data, IEX, FMP, FRED, NewsAPI, etc.) is wired anywhere — the only concrete adapter is a public, unauthenticated CSV source. `.env.example` carries zero provider credential fields, confirming there is nothing to accidentally "half-activate."

**Evidence classification is fail-closed, not fail-open** — re-verified independently in this audit (§1.6).

### 1.5 Quant Research / Investment Research / Risk / Execution

- **Strategies, experiments, scorecards, regimes, portfolio construction, signals**: covered in the Authority Map (§2). The v2 engines (`regime_engine_v2.py`, `portfolio_construction_v2.py`, `strategy_scorecard_v2.py`, `investment_engine_v2.py`) are Postgres-native, append-only, content-hashed — genuinely production-shaped. The legacy strategy/experiment registries are SQLite-only with no migration path yet (§1.2).
- **Risk**: `PostgresRiskStore` + `PostgresCriticalRepository.reserve_and_record_decision` (advisory-locked, idempotent per account/day) is a solid, production-capable reservation authority. `PlatformConfig` enforces Postgres for this in non-local environments.
- **Paper OMS**: event-sourced (`PaperOmsEvent`), duplicate-intent protection at both the broker-adapter level (`BrokerErrorCode.DUPLICATE_INTENT`) and the OMS level (`intent_id` conflict detection), a `UNIQUE` constraint on `external_fill_id`. This is a mature, safety-conscious design let down only by the `build_app()` default-to-SQLite wiring gap.
- **Execution/broker readiness** — see the dedicated breakdown in §1.7.

### 1.6 Truthfulness / Classification Audit — **PASS**

Independently re-verified (not just re-reading prior fix commits): `classify_research_evidence()` and `classify_research_evidence_from_markers()` (`operator_dashboard.py:910-959`) are fail-closed by construction — `_AUTHORIZED_REAL_MARKET_DATA_PROVIDERS` is a deliberately empty allowlist, and the docstring states explicitly that "absence of a synthetic marker is never treated as proof of real data." Every call site in the backend routes through one of these two functions; none hardcode a classification string directly. The frontend never locally defaults a classification value — it only passes through backend-supplied strings.

A dedicated regression suite exists: `tests/test_classify_research_evidence.py` (`test_synthetic_provenance_always_wins`, `test_no_resolved_provider_is_unavailable_not_synthetic`, `test_unknown_provider_never_verifies_as_real`, `test_from_markers_never_returns_real`, `test_from_markers_no_marker_is_unavailable`), tracing back to fix commit `92cfd60` (Module 2B-2.1, PR #52).

No fail-open pattern found in this audit for `HEALTHY`, `RECONCILED`, `APPROVED`, or `AUTHORIZED` fields either — each is computed from a live boolean/lineage check, and the absent-data path renders an explicit `UNAVAILABLE`/error state, never a default-positive value.

### 1.7 Execution / Broker Readiness

| Capability | State | Evidence |
|---|---|---|
| Broker-neutral interface | IMPLEMENTED | `broker_adapter.py:112-121` (`PaperBrokerAdapter` Protocol) |
| Paper transport | IMPLEMENTED | `SandboxPaperBrokerAdapter`, `broker_adapter.py:124-217` |
| Sandbox transport (real broker) | ABSENT | `BrokerMode.SANDBOX_PAPER` is just a label consumed by the same in-memory simulator — no network client exists |
| Broker credentials model | PARTIAL (placeholder only) | `BrokerConfiguration.credential_reference` exists generically but no broker API key fields anywhere in config |
| Order idempotency | IMPLEMENTED | `broker_adapter.py:144-145`; `broker_sync.py:214-218,279-286`; `paper_oms.py:84` unique constraint |
| Reconciliation | IMPLEMENTED (paper-internal) | `broker_adapter.py:97-109`; `broker_sync.py:372-389` |
| Partial fills | IMPLEMENTED | `paper_execution.py:19-20,41-48` |
| Cancel/replace | IMPLEMENTED | `broker_adapter.py:153-171`; `broker_sync.py:344-357` |
| Market-session controls | IMPLEMENTED | `pretrade_context.py:34` |
| Pre-trade risk | IMPLEMENTED | `paper_runtime.py:86-181` |
| Post-trade reconciliation | IMPLEMENTED (paper) | `broker_sync.py:372-389` |
| Disconnect/outage handling | PARTIAL — hook exists, never exercised against a real fault | `broker_adapter.py:37-38,116,140-141` (health() always returns True) |
| Shadow mode | PARTIAL — compares two local paper orders only, never a live feed | `shadow_mode.py:1,26-61`; `execution_quality.py:294-295` self-labels `"DOES_NOT_SATISFY_SHADOW_OR_LIVE_ACTIVATION_GATES"` |

**Before BROKER SANDBOX:** a real network-connected adapter implementing `PaperBrokerAdapter` against an actual broker sandbox API (none exists — everything today is in-memory), a genuine credentials model for broker API keys, and disconnect/retry/rate-limit handling exercised against real faults.

**Before SHADOW:** broker-sandbox connectivity first, then extending `shadow_mode.py` to diff a paper order against a live-broker-sandbox order (today it only diffs two locally generated paper orders), plus wiring its `requires_incident` output into a real alerting/monitoring pipeline.

### 1.8 Operations / Scheduler / Observability / Backup-DR

- **Scheduler** — ABSENT as a running service. `operational_jobs.py` models job *policy and due-state evidence* (durable, auditable) but explicitly does not execute anything — `docs/DEPLOYMENT_RUNBOOK.md:92-96` states this outright: "the monitor evaluates durable evidence... it never executes due work." No APScheduler/Celery/cron/scheduled-GitHub-Action exists for ingestion, Data Health, feature materialization, research, reconciliation, SRE probes, retention, or backups. **Confirmed: this platform currently depends entirely on a human or a CI job manually running Python scripts.**
- **Observability** — `observability.py` provides an in-process `MetricsRegistry` (plain `Counter`, no export format — not Prometheus) and structured logging via stdlib `logging`. No OpenTelemetry/tracing. Alerts terminate at `LOCAL_OUTBOX` rows with status `PENDING_EXTERNAL_DELIVERY` — the deployment runbook explicitly forbids configuring network delivery until an approved adapter exists. **Alerts today are an internal DB-table/dashboard construct, not a live external alerting pipeline** (no PagerDuty/Slack integration).
- **SLO/SLI** — confirmed absent; no uptime/latency/error-rate targets anywhere.
- **Backup/DR** — CI proves restore mechanics (`pg_dump`/corrupt-reject/`pg_restore`/SHA-256 content comparison via `scripts/verify_postgres_restore.py`). `docs/DISASTER_RECOVERY.md` itself states this is "CI recovery, not encrypted off-site retention or production RPO/RTO" and "no production assets exist yet." **CI engineering evidence exists; production DR evidence does not.**
- **Deployment/infra** — no Kubernetes, Terraform, Railway/Fly/Vercel config, Procfile, or systemd units anywhere. The only infra artifact is the CI-built, attested container image — never pushed to a registry, never deployed.

### 1.9 Frontend Completion

All 16 claimed workspaces (Markets, Instruments, Data Health, Features, Strategies, Backtests, Scorecards, Signals, Risk, Regimes, Portfolio, Investments, News, Paper, Operations, Audit) exist as real routes under `web/app/(protected)/`, each backed by real `data-access.ts` evidence calls rather than fabricated data.

| Workspace | Classification |
|---|---|
| Markets, Instruments, Data Health, Features, Strategies, Backtests, Scorecards, Risk, Regimes, Portfolio, Investments, News, Paper, Operations, Audit | PROFESSIONALIZED |
| Signals | PARTIAL — thinnest page, single discovery call, no detail drill-down comparable to the others |

None are "blocked by backend" in the sense of fabricating data — every workspace routes through `readEvidence()`, which returns an explicit `UNAVAILABLE` state with a `detail` string when a backend authority isn't configured. Markets is the closest to a structural ceiling: live external feeds are architecturally disabled (`EXTERNAL_BLOCKED`), so it can never show non-synthetic live prices — but this is disclosed, not hidden, by design.

Auth enforcement is centralized in `web/proxy.ts` (fail-closed to 503 if the view token is unset), not duplicated at the `(protected)/layout.tsx` level — functionally correct for Next.js middleware, but worth flagging as a single point of failure: a future refactor that misconfigures the middleware matcher would silently remove protection with no defense-in-depth at the layout.

### 1.10 AI / Model Readiness

**Architecture-only — no live model calls exist anywhere in the codebase.** No `openai`/`anthropic`/`langchain` import exists under `src/`. `agent_research.py` defines an HTTPS transport *contract* (`ConfiguredHttpsResearchAgent`) that would call an external structured-research endpoint if wired to a concrete transport — none exists; only test fakes are used. Retrieval (`research_retrieval.py`) is a policy-checked ledger over already-fetched internal data, not a live RAG pipeline; no embeddings or vector store exist anywhere in the repo. Evaluation machinery (`model_evaluation.py`, `agent_answer_evaluation.py`) operates on classical/statistical model outputs and lexical answer comparison, not semantic LLM evaluation.

**Tool/execution authority is structurally, not just declaratively, absent from AI code paths.** In `api.py`, `agent_research_store` is wired only to three GET-style read endpoints; the OMS/order endpoints never reference it, and vice versa — these are disjoint code paths with no shared function and no data flow from agent research into order submission. `scheduled_agent_workflows.py` additionally hard-codes `tool_authority = model_invocation_authority = action_authority = "NONE"` and fails validation if tampered.

---

## 2. GitHub / Repository Governance

Verified live via `gh` CLI on 2026-09-05, not assumed from prior handoff notes:

| Item | State |
|---|---|
| Visibility | **PUBLIC** |
| Default branch | `main` |
| Branch protection on `main` | **DISABLED** — `gh api repos/.../branches/main/protection` returns `404 Branch not protected`. Confirms the Module 2B-5 handoff concern is still true. |
| Required checks | None (no protection configured, so nothing is "required" at the platform level — CI still runs on push/PR via workflow triggers) |
| Force-push / deletion protection | None (follows from no branch protection) |
| Code owners / review requirements | None found (no `CODEOWNERS` file) |
| Dependabot | **Absent** — no `.github/dependabot.yml` |
| Security scanning workflows | `bandit`, `pip-audit`, `pnpm audit`, Trivy, SBOM/SLSA attestation all run inside `verify.yml`; no separate CodeQL workflow |
| Release/tag practice | No release workflow found; commits are merged directly via PR, no version tags observed in this audit's scope |

**Classification: CRITICAL.** A public repository with no branch protection on `main` means any collaborator with write access (or a compromised token) can push directly to `main`, bypassing CI and review entirely. This is independent of and orthogonal to the strong in-repo safety engineering (live-trading blocks, evidence classification, etc.) — those protections live in code that could itself be pushed around without review. Re-enabling branch protection (require PR, require the `verify` check, require up-to-date branches, disallow force-push/deletion) is the cheapest, highest-leverage fix available in this entire audit.

No repository settings were changed as part of this audit beyond creating the audit branch and PR, per instructions.

---

## 3. Live-Trading Path Audit

**LIVE EXECUTION PATHS FOUND: NONE.**

Re-verified independently via exhaustive grep and code-path tracing (not reused from old docs):

1. `PlatformConfig.__post_init__` raises `LiveTradingForbiddenError` unconditionally whenever `live_trading_enabled=True` is passed to the constructor at all (`config.py:39-43`) — there is no way to construct a config object with this flag true.
2. An independent second guard exists in `postgres_runtime.py:136`.
3. API responses hardcode the literal `False` for `live_trading_enabled` rather than reading it from config state (`api.py:355,413,880`) — even a hypothetical mutated config object couldn't make the API surface report otherwise.
4. The frontend types `live_trading_enabled` as the TypeScript literal `false` (not `boolean`) in `web/app/operator-contracts.ts:19`, and E2E tests (`web/e2e/module2b5-paper-operations-audit.spec.ts`) actively assert the absence of any live-order UI control and the presence of explicit "NO LIVE EXECUTION" text; `verify-dashboard.mjs` fails the build if "Start Live" or "EXECUTE" appear in rendered HTML.

No `OrderMode`/`ExecutionMode`/`TransportMode` enum exists. `BrokerMode` has exactly two values (`SIMULATED_PAPER`, `SANDBOX_PAPER`), both consumed by the same in-memory simulator. The only reference to a real broker name anywhere in the repo is a quarantined, non-imported third-party upstream artifact (`docs/upstream/...` references to `src/trading/alpaca_manager.py`, explicitly marked "DEFER_REFERENCE_ONLY; do not transcode, execute, import or install" and not part of the `trade_platform` package).

---

## 4. Critical Gap List

### P0 — must fix before any staging environment

1. **Re-enable `main` branch protection.** Problem: public repo, no protection, no required checks, force-push/deletion allowed. Risk: any write-access token can bypass CI and land unreviewed code, including safety-critical files (`config.py`, `security.py`). Fix: require PR + passing `verify` check + up-to-date branch, disallow force-push and branch deletion. Dependency: none. Module: repository governance hardening (can be a fast, standalone follow-up).
2. **Close the `build_app()` SQLite-default wiring gap for authorities that already have CI-verified Postgres siblings** (Paper OMS, risk decisions, promotion ledger, operational alerts, instrument/model-registry/pre-trade/policy/kill-switch/quote stores). Problem: `PlatformConfig` forbids SQLite outside `local_research`, but `build_app()` never consults it. Risk: an operator could start the shipped app against Postgres-shaped infrastructure and still be silently running on ephemeral SQLite for order/risk state. Fix: make `build_app()` select stores based on `PlatformConfig.persistence_target`/`environment` rather than hardcoded SQLite defaults, or require explicit Postgres store injection when `environment != "local_research"`. Dependency: none — all target Postgres stores already exist and are CI-verified. Module: production identity/deployment foundation module.
3. **Add real, deployable secret management for staging** (replace bare env-var secrets with a secret manager). Dependency: choice of staging platform (§7 below).

### P1 — must fix before real provider activation

4. **Migrate remaining no-sibling SQLite authorities to Postgres**: audit events, investment detail, strategy/experiment registries, agent research, feature platform, full signal lifecycle. Problem: no durable, prod-capable store exists for these domains at all. Risk: compliance/audit trail, strategy lineage, and feature data would be lost or unrecoverable in any real deployment. Fix: add Postgres schema + store implementation per domain (audit events first, given compliance weight). Dependency: migration authoring + backfill tooling (already patterned in `postgres_backfill.py`). Module: Postgres authority migration module.
5. **Implement production identity**: complete `external_identity.py`'s OIDC/SSO verifier, move from static shared tokens to per-user sessions, and activate the already-defined RBAC roles per real user. Dependency: choice of IdP. Module: production identity/RBAC module.
6. **Add CSRF protection** for cookie-authenticated mutation endpoints (defense-in-depth beyond `sameSite=lax`). Module: security hardening, can ride with #5.
7. **Add Dependabot/Renovate** and a CodeQL (or equivalent SAST) workflow. Module: repository governance hardening (can bundle with #1).
8. **Build a real deployable scheduler** for ingestion/Data Health/reconciliation/retention/SRE-probe jobs (APScheduler/Celery worker or managed cron), since "provider activation" is meaningless without something to run ingestion on a cadence. Module: deployment foundation + scheduler/workers module.

### P2 — must fix before broker sandbox/shadow

9. **Implement a real network-connected broker-sandbox adapter** implementing `PaperBrokerAdapter` against an actual broker's sandbox API, with a genuine credentials model (today: in-memory simulator only, `credential_reference` is unused for broker keys). Module: broker sandbox integration module.
10. **Extend `shadow_mode.py`** to diff a primary paper order against a live-broker-sandbox order (today it only diffs two local paper orders), and wire `requires_incident` into real alerting. Dependency: #9. Module: shadow-mode module.
11. **Exercise real disconnect/outage handling** for broker connectivity — `health()` is currently a stub always returning `True`; needs to be tested against actual network faults once a real adapter exists. Dependency: #9.

### P3 — later product/scale work

12. Deployment topology (Terraform/Railway/managed Postgres) — see §5 staging recommendation.
13. Real observability stack (structured metrics export, tracing, external alert delivery beyond `LOCAL_OUTBOX`).
14. Production DR drills (offsite/encrypted backup retention, RPO/RTO commitments, real restore rehearsal).
15. Session rotation/revocation and encryption-at-rest for Postgres/backups.

---

## 5. Recommended Next Module Sequence

1. **Repository governance & security hardening** — WHY NOW: cheapest fix in the entire audit, closes the CRITICAL branch-protection gap immediately, and adds Dependabot/CodeQL with no dependency on anything else. WHAT IT UNBLOCKS: safe collaboration on every subsequent module. WHAT MUST REMAIN DISABLED: everything (no functional changes).
2. **Postgres authority wiring + migration** (P0 #2 + P1 #4) — WHY NOW: the domain logic and Postgres schemas mostly already exist; this is composition and backfill work, not new design, and it's a prerequisite for calling anything "staging-ready." WHAT IT UNBLOCKS: a real staging deployment can then trust `build_app()`'s defaults. WHAT MUST REMAIN DISABLED: live trading, provider activation.
3. **Deployment foundation + scheduler/workers** — WHY NOW: without a deployment target or a scheduler, "staging" doesn't exist as a concept yet; this module turns the CI-only artifacts (container image, migrations) into something that actually runs continuously somewhere. WHAT IT UNBLOCKS: a real place to point provider credentials and a real place for the scheduler in P1 #8. WHAT MUST REMAIN DISABLED: real provider activation, broker.
4. **Production identity / RBAC / OIDC** — WHY NOW: needed before any real staging environment is exposed to more than one operator, and before any real provider credentials or broker credentials are trusted to the platform. WHAT IT UNBLOCKS: safe multi-operator staging use, meaningful audit attribution. WHAT MUST REMAIN DISABLED: broker, live trading.
5. **Real provider activation (market data first, single vendor) + real-data validation** — WHY NOW: architecture is ready today per §1.4; this is the first module that actually touches "real authorized data," so it should come only after governance/persistence/deployment/identity are solid. WHAT IT UNBLOCKS: genuine Data Health evidence against a live feed, meaningful strategy backtests on non-synthetic history. WHAT MUST REMAIN DISABLED: broker, live trading.
6. **Broker sandbox** — WHY NOW: only after the above; needs a real deployment target and real secret management for broker credentials. WHAT IT UNBLOCKS: shadow mode. WHAT MUST REMAIN DISABLED: live trading (still, always, next-next-next step at earliest).
7. **Shadow mode** — after broker sandbox connectivity exists.

---

## 6. Recommended Staging Architecture

Minimal topology, justified only by current workload (no Kafka/Redis/Kubernetes — nothing in this audit found a workload that needs them):

- **Web/Next.js service** — one instance, serving the existing `web/` app (already built and tested in CI).
- **FastAPI service** — one instance, serving `trade_platform.api:app`, once `build_app()` is fixed to default to Postgres in non-local environments (P0 #2).
- **Managed PostgreSQL** — single managed instance (e.g., RDS/Cloud SQL/Railway Postgres), replacing the ad-hoc CI container; this is the one piece of infra genuinely required by the current codebase's own persistence design.
- **One worker/scheduler process** — a single lightweight cron-style worker (APScheduler process or platform-native scheduled job) to invoke the ingestion/Data Health/reconciliation/retention entry points that currently only run via scripts or tests. This does not need a distributed queue at this workload size — the entry points are already designed as discrete, idempotent functions.
- **Secret manager** — whatever the chosen platform natively offers (AWS Secrets Manager, GCP Secret Manager, or Railway/Fly's built-in secrets) to replace bare environment variables for the operator token, session secret, and (later) provider/broker credentials.
- **Observability** — structured log aggregation from day one (the app already emits structured logs via `observability.py`); metrics export can wait for P3.

No object storage, no Redis, no message queue, no Kubernetes — none of these are justified by any workload found in this audit. If backup artifacts need offsite storage for real DR (P3 #14), a single object-storage bucket can be added then, not now.

---

## 7. Notes on Audit Methodology

This audit was produced by seven independent, read-only research passes over the current `main` branch content, each covering a distinct domain (security/identity, SQLite↔Postgres authority split, real-data readiness, execution/broker/live-trading, deployment/ops/scheduler/DR, frontend/truthfulness, AI readiness + authority map), followed by manual synthesis and cross-checking for internal consistency (e.g., reconciling the `PlatformConfig` Postgres-enforcement claim against the `build_app()` SQLite-default finding, which surfaced the wiring-gap issue in §1.2/§4 P0-2). All citations are file:line references verified at the time of research against the checked-out `antigravity/module3a-production-readiness-audit` branch, which is a fast-forward of verified `main@11a028c9`. Existing docs (`POSTGRES_RUNTIME_AUTHORITY_MATRIX.md`, `DISASTER_RECOVERY.md`, `DEPLOYMENT_RUNBOOK.md`, `KNOWN_LIMITATIONS.md`) were read and cross-verified against code rather than trusted at face value; where they were found accurate, this audit says so explicitly; where a gap between doc and code was found, it is called out above.
