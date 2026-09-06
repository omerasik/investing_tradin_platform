# Module 3E: Staging Deployment Foundation + Scheduler/Worker Runtime

Companion to [MODULE_3D_PRODUCTION_IDENTITY_SECRETS_AUDIT.md](MODULE_3D_PRODUCTION_IDENTITY_SECRETS_AUDIT.md).
Builds a real staging deployment topology on top of the existing canonical
`trade_platform.runtime_app` composition root, and turns the durable operational-job
authority (`trade_platform.operational_jobs`) from a monitoring-only evidence store
into something that actually executes a bounded, explicitly-approved set of internal
maintenance jobs.

## 1. Deployment topology

Four independently owned, separately built container images, orchestrated by
[`docker-compose.staging.yml`](../docker-compose.staging.yml):

| Service | Image | Built from | Role |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | (upstream) | The single authoritative datastore. Named volume `trade-platform-postgres-data`; never falls back to anything else. |
| `migrate` | `trade-platform-migrate` | `Dockerfile.migrate` | One-shot: `alembic upgrade head`, then exits. `restart: "no"`. |
| `api` | `trade-platform-research` | `Dockerfile` (unchanged) | Serves `trade_platform.runtime_app:app` — the exact same canonical composition root Module 3C/3D built. |
| `worker` | `trade-platform-research` (same image, different `command:`) | `Dockerfile` (unchanged) | Serves `trade_platform.worker_app:app` — the new scheduler/worker composition root (§2). |
| `web` | `trade-platform-dashboard` | `web/Dockerfile` (new) | The Next.js operator dashboard, built and deployed independently of the API. |

Why this shape and not more:

- **No Kubernetes, Kafka, Redis, or Celery.** The actual workload is a handful of
  internal maintenance jobs on multi-minute-or-slower intervals (§3). PostgreSQL's
  own session-level advisory locks (`pg_try_advisory_lock`/`pg_advisory_unlock`)
  already give exactly the mutual-exclusion primitive a job queue would provide, at
  zero additional operational surface. Nothing about the current workload
  demonstrates a concrete need for more.
- **Migration is a separate, disposable image**, not a step baked into `api`'s
  startup command and not run from inside the hardened research-API image at all.
  `Dockerfile`'s `requirements-runtime.txt` deliberately does not include
  `alembic`/`sqlalchemy` — the API and worker processes have no code path that can
  run a migration, by construction, not by convention. `api`/`worker` declare
  `depends_on: migrate: condition: service_completed_successfully`, so they cannot
  start against unmigrated schema.
- **API and worker share one image, two `command:`s** (`trade_platform.runtime_app:app`
  vs. `trade_platform.worker_app:app`), not two separately-versioned images. They
  need the exact same dependency closure and the exact same fail-closed PostgreSQL
  composition philosophy — this is one artifact serving two roles, not two
  artifacts that could drift apart.
- **The dashboard is a genuinely separate image and service.** It has its own
  `Dockerfile`, builds independently, and is deployed on its own schedule. It talks
  to `api` over HTTP exactly the way any other client would (see
  `web/app/session.ts`, `web/proxy.ts`) — nothing here gives it backend
  credentials or database access.

All of `api`, `worker`, and `web` run hardened: `read_only: true`, `cap_drop: [ALL]`,
`security_opt: [no-new-privileges:true]`, a bounded no-exec `/tmp` tmpfs — the exact
posture Module 3A/3B already established for the research-API container, extended
uniformly to the new services.

## 2. Startup / liveness / readiness / restart / graceful-shutdown contracts

**API** (`trade_platform.runtime_app`): unchanged from Module 3C/3D.
`/health/live` is process-only; `/health/ready` reports environment and paper/live
flags and fails closed if a required protected-runtime authority is missing.

**Worker** (`trade_platform.worker_app`, new): mirrors the API's composition
philosophy for the same `PostgresDatabase`, but composes it inside the ASGI
`lifespan` rather than at import time (documented in the module's own docstring) —
importing the module (e.g. during test collection) is always safe; only actually
*starting* the process requires `POSTGRES_DSN` to be present and reachable, at which
point it fails closed exactly like the API does.

- **Startup**: `lifespan` composes `JobContext` (the durable job/alert/retention
  authorities) and starts an `asyncio` background task running the tick loop.
- **Liveness** (`/health/live`): process-only, no database round trip — matches the
  API's own liveness philosophy.
- **Readiness** (`/health/ready`): `503` until at least one tick has completed, and
  again if the most recent tick is more than `3 × poll_seconds` old — a stalled tick
  loop (e.g. a wedged event loop) is distinguishable from "hasn't started yet" and
  from "healthy."
- **Restart**: `restart: unless-stopped`, identical to the API service. Restart
  safety follows directly from two properties: (1) a job is only "due" again after a
  *recorded* `SUCCEEDED` run advances its due-state (`operational_jobs.due_jobs`), so
  a crash mid-execution simply leaves the job due and it is retried on the next tick
  by whichever worker instance is alive; (2) a PostgreSQL session-level advisory lock
  is automatically released by the server the moment the holding connection closes —
  a crashed worker's lock cannot leak or need manual clearing.
- **Graceful shutdown**: `WorkerRuntime.stop()` sets a stop flag, cancels the
  in-flight tick task, awaits it, and only then closes the database connection —
  never severs the connection out from under a running tick.

## 3. Scheduler/worker architecture

`trade_platform.scheduler.SchedulerWorker.run_tick(as_of)`, called once per
`TRADE_PLATFORM_WORKER_POLL_SECONDS` (default 30s) by the worker process loop:

1. Calls `PostgresOperationalJobStore.due_jobs(as_of)` — the same due-state
   evaluation `operational_jobs.py` already had before this module.
2. For each due policy whose `job_name` has a registered runner
   (`default_job_registry()`), attempts to claim a PostgreSQL session-level
   advisory lock keyed by `(job_name, due_at)`. If another worker process already
   holds it (a rolling restart, an operator running one manually), this tick skips
   that job — no double execution.
3. Runs the registered function, catching any exception (a runner's own failure must
   never crash the tick loop or block other due jobs in the same tick).
4. Records exactly one `OperationalJobRun` via the existing, already-idempotent
   `append_run` (keyed by `f"{policy_id}:{scheduled_for}:{started_at}"`, so a
   duplicate submission of the identical attempt is a safe no-op, while a genuinely
   new attempt after a prior failure gets its own row — full audit trail of every
   attempt, not just the final one).
5. On failure, additionally raises a durable, fingerprint-deduplicated alert
   (`OPERATIONAL_JOB_EXECUTION_FAILED`) via the existing `PostgresOperationalAlertStore`.
6. Releases the advisory lock in a `finally`, regardless of outcome.

A due job policy with **no** registered runner is left entirely alone by the
worker — never executed, but still visible to (and alertable by) the
`operational_job_monitor` job below if it goes overdue. Registering a new safe job
means adding one function to `default_job_registry()`; nothing about the tick loop
itself changes.

## 4. Jobs actually enabled

Three, all operating purely on already-durable internal PostgreSQL state — none
makes an external network call, activates a market-data/news provider, or touches a
broker:

| `job_name` | What it does | Durable evidence |
|---|---|---|
| `operational_job_monitor` | Runs the existing `PostgresOperationalJobStore.monitor_overdue` — the due-state monitor that predates this module — as a first-class, idempotently-recorded due job instead of an unconditional per-tick call. | `operational_job_runs`; opens/resolves `operational_alerts` rows. |
| `postgres_dependency_probe` | A real internal-only SRE-style probe: executes `SELECT 1` on the worker's own PostgreSQL connection and measures latency. Raises a `CRITICAL` alert (`POSTGRES_DEPENDENCY_UNHEALTHY`) on failure, resolves it on the next success. | `operational_job_runs` (`latency_ms` in the summary); `operational_alerts`. |
| `retention_evaluation_sweep` | Finds already-recorded `ObjectEvidenceManifest`s whose retention window has never been evaluated or may have just elapsed (`PostgresRetentionEvidenceStore.manifests_due_for_evaluation`, new in this module) and calls the existing, independently idempotent `evaluate()` on each. | `retention_evaluations`. Never deletes or claims deletion of anything — `ELIGIBLE_FOR_REVIEW` still requires a separately approved human lifecycle decision, unchanged from before this module (see `docs/DEPLOYMENT_RUNBOOK.md`). |

Bootstrapping these three job (and one alert-route) policies for a fresh environment
is an explicit, idempotent, separately-approved deployment step:
`scripts/seed_operational_job_policies.py --postgres-dsn ... --approved-by ...`.

## 5. Jobs deliberately blocked / deferred

Named in the task brief as candidates, and deliberately **not** wired in this
module, with the specific reason:

- **Data Health evaluation** (`detect_data_health`) — its input, `DataHealthObservation`
  records, come from an ingested bar/observation store. The confirmed prod-capable
  store for that data (`SQLiteBarStore`) is SQLite-only per `docs/PRODUCTION_READINESS_MATRIX.md`
  §2 — there is no PostgreSQL-backed source of observations for a protected runtime
  to re-evaluate. Wiring this job today would mean either a permanent no-op or
  inventing a new data path, which risks quietly implying a market-data-provider
  activation this module must not do. Deferred pending a PostgreSQL bar-store
  migration.
- **Reconciliation** (`paper_execution.reconcile`) — real reconciliation compares
  the OMS's internal ledger against a broker/paper-session's reported positions.
  That comparison already happens, per-request, inside the API's own OMS flow
  (`PostgresPaperOms.record_reconciliation_with_account`); there is no persistent,
  worker-shareable "external" position state independent of that in-flight request
  for a background job to compare against. Scheduling a synthetic reconciliation
  from the worker would not add real evidence beyond what the OMS already records.
  Deferred; revisit if/when a durable, worker-readable "expected external state"
  authority exists independently of the request path.
- **Backup/restore** — implemented as a documented **staging runbook procedure**
  (§7), not a hot in-process scheduled job. Running `pg_dump`/`pg_restore` from a
  long-lived worker process against production infrastructure is a
  higher-blast-radius operation than the three read/re-evaluate-only jobs above; it
  is better suited to a deliberately-invoked maintenance procedure (already proven
  in CI via `scripts/verify_postgres_restore.py`) than an automatic interval job at
  this stage.

## 6. Persistence authorities

No new tables. All three enabled jobs read/write tables that already existed before
this module (`operational_job_policy_versions`, `operational_job_runs`,
`operational_alerts`, `operational_alert_events`, `retention_policy_versions`,
`object_evidence_manifests`, `retention_evaluations`). The only schema-adjacent
change is one new read method, `PostgresRetentionEvidenceStore.manifests_due_for_evaluation`
— a query, not a new table.

## 7. Backup/restore, RPO/RTO (staging)

Staging targets (not yet production SLAs — see `docs/DISASTER_RECOVERY.md` for the
full existing caveats this module does not change):

- **RPO**: bounded by backup cadence. Take a `pg_dump --format=custom` snapshot of
  the staging database on whatever cadence the deployer's infrastructure provides
  (e.g. a scheduled CI job); nothing in this module changes that mechanism.
- **RTO target for staging**: restore into a fresh database, run
  `alembic upgrade head`, then run `scripts/verify_postgres_restore.py --source-dsn
  ... --restored-dsn ...` — the same script and count/checksum evidence CI already
  produces on every PR (see `.github/workflows/verify.yml`'s "PostgreSQL backup,
  fresh restore and reconciliation drill" step). A failed or incomplete verification
  is a failed restore; do not point `api`/`worker` at a database that has not passed it.
- This module's compose topology names the PostgreSQL data as a durable, named
  volume (`trade-platform-postgres-data`) specifically so a restore procedure has an
  unambiguous, singular target to replace.

## 8. Rollback / failure behavior

- **Migration failure**: `migrate` exits non-zero; Compose's
  `condition: service_completed_successfully` means `api`/`worker` never start
  against a partially-migrated schema. Nothing runs. Roll back by fixing the
  migration and re-running `migrate` alone (`docker compose ... run migrate`), or by
  restoring the pre-migration backup per §7.
- **A due job fails**: recorded as a `FAILED` run (never silently dropped), a
  durable `OPERATIONAL_JOB_EXECUTION_FAILED` alert is raised, and the job remains
  due — the worker retries it on its own next tick with no operator action needed.
- **The worker process itself crashes or is killed**: `restart: unless-stopped`
  brings it back; any advisory lock it held is already released by PostgreSQL; any
  job it was mid-executing without having recorded a run is simply still due.
- **Rolling deploy of a new worker image**: the outgoing and incoming worker
  processes may briefly overlap. The advisory-lock claim in `run_tick` means at most
  one of them executes a given due occurrence; this is exercised directly in
  `tests/test_scheduler_postgres.py::test_a_held_advisory_lock_blocks_a_concurrent_claim_and_releases_cleanly`.

## 9. Staging deployment evidence

- `tests/test_staging_deployment_contract.py` — static assertions on
  `docker-compose.staging.yml`, `Dockerfile.migrate`, and `web/Dockerfile` (hardening
  flags present, no duplicate `build:` races, no Kubernetes/Kafka/Redis/Celery, no
  hardcoded secrets).
- CI's `verify` job now brings up the *entire* Compose stack (`postgres`, `migrate`,
  `api`, `worker`, `web`) on every PR and asserts `/health/ready` on `api` and
  `worker` and a `200` from the dashboard's `/login`, in addition to the pre-existing
  standalone research-API container smoke test.
- This was also validated manually end-to-end against a live Docker Compose stack
  during development: built all four images, brought the stack up, confirmed
  `migrate` exited 0 before `api`/`worker` started, confirmed all `/health/ready`
  endpoints, registered a short-interval job policy against the running stack, and
  confirmed the worker executed it autonomously (`last_successful_at` populated)
  with zero manual intervention beyond the initial policy registration.

## 10. Remaining production blockers (unchanged or newly visible)

- Everything already listed as a blocker in `docs/PRODUCTION_READINESS_MATRIX.md`
  before this module remains a blocker — this module does not touch identity,
  secrets, audit, RBAC, CSRF, or deterministic risk controls.
- Data Health and reconciliation jobs remain unwired for the reasons in §5.
- Backup/restore is a documented manual/CI procedure, not yet an automated,
  continuously-scheduled job or off-site retention system (`docs/DISASTER_RECOVERY.md`
  already says this and this module does not change it).
- No container registry publication, release approval workflow, or IaC exists yet
  for this topology (`docs/DEPLOYMENT_RUNBOOK.md` already lists this as pre-staging
  work); `docker-compose.staging.yml` builds images locally from source, it does not
  push or pull from a registry.

## 11. Hard constraints confirmed intact

Live trading remains disabled (`PlatformConfig`'s unconditional rejection of
`live_trading_enabled=True`, unchanged); no broker integration, broker sandbox, or
network-connected order execution was added; no market-data, fundamental, macro, or
news provider was activated, and none of the three enabled jobs makes any external
network call; no provider credentials exist anywhere in this module; no automatic
order submission exists; protected-runtime services still refuse to start against
anything but PostgreSQL (`api`/`worker` both require `POSTGRES_DSN`, fail closed
otherwise); Module 3D's OIDC/RBAC/session-revocation/CSRF/secrets/audit controls are
untouched by this module — `docker-compose.staging.yml`'s `api` service exposes the
exact same identity/secret environment variables Module 3D defined, unchanged;
deterministic risk controls are untouched; branch protection and CI are unchanged
in kind (CI gained one additional verification step, nothing was weakened or removed).
