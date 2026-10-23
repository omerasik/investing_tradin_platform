# Deployment Runbook

Development is local and research-only. Future environments are development, test, paper, then production, with signed/versioned artifacts, migration checks, least-privilege secret injection, staged rollout, rollback and backup/restore evidence. No deployment may include live execution without signed readiness approval.

The repository `Dockerfile` is a narrow research API artifact, not the paper or
production deployment recipe. It pins Python 3.12.14 by tag and digest, installs
the exact `requirements-runtime.txt` resolution, copies only `src/`, and runs as
UID/GID 10001. Build it from the repository root and inject the temporary bearer
token/role only at runtime. The verified hardened invocation uses a read-only
root filesystem, `--cap-drop=ALL`, `no-new-privileges` and a bounded no-exec
`/tmp` tmpfs; `/health/live` and `/health/ready` must pass before use. CI also
requires readiness to state `local_research`, paper enabled and live disabled.
Do not relabel this SQLite-backed artifact as a PostgreSQL environment.

CI scans the built archive with digest-pinned Trivy without mounting the Docker
socket. It retains the complete JSON inventory and CycloneDX image SBOM, rejects
an EOL distribution and fails on fixable HIGH/CRITICAL findings. Do not describe
a passing gate as vulnerability-free: vendor-unfixed findings remain in the
retained report and require review on every base/scanner/database update.

CI also compresses the exact scanned image, records a portable SHA-256 checksum,
and uses commit-pinned GitHub `actions/attest` with short-lived OIDC/Sigstore
credentials to create SLSA provenance and CycloneDX SBOM attestations. It
retains both signed bundles and verifies both predicates with `gh attestation
verify`. Untrusted fork PRs receive no attestation authority. A consumer must
download the archive and checksum together, verify the checksum, then verify
both attestations against `omerasik/investing_tradin_platform` before use.

Before any staging/production use, add registry-native OCI signing/publication,
release approval and registry retention/access controls, IaC, network/TLS and resource policy,
PostgreSQL migration/restore validation, rollback and soak evidence. A mutable
tag, unsigned image, missing evidence or failed scan is not deployable.

For a PostgreSQL environment, inject the DSN through the deployment secret
manager, set `persistence_target=postgres`, and run `alembic upgrade head`
before application startup. `paper` and `production` configuration reject
SQLite. Perform the SQLite backfill dry-run and resolve every identity conflict
before any mapped write. Downgrades are permitted only for disposable schema
tests; production migrations are forward-only. A failed migration, unavailable
database, stale reconciliation or incomplete restore must leave risk increase
and paper submission fail-closed.

Serve the API and dashboard only behind an HTTPS terminator. Paper/production
API deployments suppress `/docs`, `/redoc` and `/openapi.json`; do not re-enable
them on an Internet-facing process. Verify CSP, HSTS, frame denial, MIME,
referrer, permissions, opener/resource and no-store headers after every proxy or
CDN change, because an intermediary can remove or replace them. The dashboard
CSP must contain request-unique `script-src` and `style-src` nonces, must retain
`strict-dynamic`, and must not contain `unsafe-inline`. Confirm every rendered
script nonce matches that response's policy and that a second request receives
a different nonce. Never cache, normalize, combine or statically replace this
request-bound policy at a proxy/CDN. Response headers do not provide OIDC, MFA
or RBAC.

For the temporary bearer boundary, inject `TRADE_PLATFORM_OPERATOR_TOKEN` only
through the deployment secret manager, set a non-blank
`TRADE_PLATFORM_OPERATOR_SUBJECT`, and assign exactly one
`TRADE_PLATFORM_OPERATOR_ROLE`. Omission defaults to `viewer`; an unrecognized
role fails closed with service unavailable. Use the narrowest role:

- `viewer`: authenticated evidence reads only;
- `researcher`: reads plus strategy creation and research backtest launch;
- `data_steward`: reads plus fundamental materialization and ingestion cadence;
- `risk_reviewer`: reads plus portfolio-risk evaluation and alert acknowledgement;
- `auditor`: reads plus append-only audit-event creation;
- `operator`: all currently defined permissions.

Roles are deployment-owned and never supplied by a browser/API request. Do not
represent this static one-token mapping as OIDC, MFA, production sessions or a
managed identity directory; replace it at that boundary before production.

For an external identity deployment, supply a verifier that performs signature,
trusted-key, algorithm and issuer validation before constructing a
`VerifiedExternalSession`. Register an approved immutable mapping policy with
an exact HTTPS issuer, audience, required authentication methods, maximum age
and allowlisted group-to-role mapping. Compose `ExternalSessionAuthenticator`
only with the PostgreSQL `PostgresIdentitySecurityStore` as the authorization
decision sink; startup rejects a missing/non-durable sink, and an audit write
failure blocks the request. Validate IdP key rotation, token revocation/session
termination, clock skew, group lifecycle and break-glass procedures in staging.
Never pass an unverified decoded JWT payload, client-selected role, raw token or
raw provider session ID into these contracts. This runbook does not select or
activate an IdP.

After any restore, run `scripts/verify_postgres_restore.py --source-dsn ...
--restored-dsn ...` against a separately created database. Do not release the
append-only recovery gate unless revision, critical hashes/counts, validation
manifests, OMS reconstruction, broker cursor, risk reservation, kill switch,
promotion and reconciliation checks all pass. A corrupt or incomplete dump is
a failed deployment.

Operational job policies and terminal run evidence do not, by themselves, authorize
a scheduler to run arbitrary code. Module 3E's deployment-owned scheduler
(`trade_platform.scheduler`, served by `trade_platform.worker_app`) is exactly the
"deployment-owned scheduler" this paragraph anticipated: it invokes only the
separately approved job entry points named in `scheduler.default_job_registry()`
(currently `operational_job_monitor`, `postgres_dependency_probe`, and
`retention_evaluation_sweep` — see `docs/MODULE_3E_STAGING_DEPLOYMENT_AND_SCHEDULER.md`),
and every execution appends terminal evidence with a unique idempotency key via the
existing `PostgresOperationalJobStore.append_run`. A due job policy whose name has no
registered runner is still monitored for overdue alerts but is never executed.
Register job policies for a new environment with
`scripts/seed_operational_job_policies.py --postgres-dsn ... --approved-by ...`
(idempotent when re-run with the same `--version`/`--approved-at`) before starting
the worker.

For a Docker Compose staging deployment, see `docker-compose.staging.yml` and
`docs/MODULE_3E_STAGING_DEPLOYMENT_AND_SCHEDULER.md`: it runs `postgres`, a one-shot
`migrate` step (a separate, disposable image — `Dockerfile.migrate` — that does not
share the hardened research-API image's dependency set), then `api`, `worker`, and
the `web` dashboard as four independently built and deployed services. Copy
`.env.staging.example` to `.env`, fill in real secrets, and run
`docker compose -f docker-compose.staging.yml up -d --build`. A successful staging
deployment is not, and must never be represented as, live-trading readiness.

## Read-only terminal runtime smoke

The existing `analytics` extra is the supported local/staging analytical Python
environment: install the repository with `python -m pip install -e '.[dev,analytics]'`
in an isolated environment. `dev` supplies the in-process HTTP smoke client;
`analytics` supplies NumPy, DuckDB and PyArrow. Use the same reviewed source revision
for the smoke and the process that will serve it. This is a local/staging preparation
path, not a separately scanned analytical container or a production release artifact.
The thin API image and `requirements-runtime.txt` deliberately exclude these packages.

| Capability | Thin API | Analytical environment |
| --- | --- | --- |
| Protected PostgreSQL terminal reads and stored research reports | Available with migrated DB and authentication | Same |
| Search/rerun/columnar research tooling | Not installed | Existing analytics extra and explicit research data required |
| Sealed T4 proven-price resolution | Explicit `UNAVAILABLE` when analytical imports are absent | Requires pinned selection policy and admissible stored/raw evidence |
| Net economics, forward validation, owner authority | Existing evidence/owner gates apply | Same; dependencies confer no authority |

Run the smoke from the repository root in the intended environment. Inject
`POSTGRES_DSN` and `TRADE_PLATFORM_OPERATOR_TOKEN` through the existing local/staging
secret mechanism; do not put their values in commands, logs or policy files. Set
`TRADE_PLATFORM_ENVIRONMENT=paper` explicitly and `PYTHONPATH=src`. Apply existing
Alembic migrations separately before running the smoke. It never migrates, seeds,
fetches, starts workers or calls write endpoints.

```text
python scripts/smoke_terminal_runtime.py --profile analytical
python scripts/smoke_terminal_runtime.py --profile thin
```

Both commands test the canonical ASGI app, protected PostgreSQL readiness,
unauthenticated rejection and an authenticated terminal overview read. `thin` checks
the core path without importing analytics and reports installed modules honestly;
running it in an analytical environment does not prove the thin container's package
boundary. CI's existing hardened-container checks own that boundary. `analytical`
additionally imports all three analytics packages and the real frame/price resolver
modules. `PASS` means `RUNTIME_SMOKE_ONLY`: an empty registry, unopened holdout and
missing price policy remain explicit facts, never successful research validation.
The paper composition uses the existing temporary bearer boundary; this check makes
only read requests and makes no least-privilege or production identity claim.
After that authenticated read, the smoke samples `clock_timestamp()` once through
the canonical app's PostgreSQL connection in a read-only transaction. It reports
`app_before_utc`, `postgres_sample_utc` and `app_after_utc`, plus the application
bracket width and the possible `DB - app` offset interval in microseconds. A strictly
positive interval is observed DB-ahead; a strictly negative one is observed
DB-behind; an interval containing zero overlaps the application bracket. The bracket
width is the measurement uncertainty from this query path, including local work and
database round trip; it is not a separately measured network latency. This single
observation is diagnostic only: it is not market knowledge time, an owner-approved
tolerance, or a continuous clock-health guarantee. No timestamp is corrected and no
readiness or evidence gate is relaxed by this measurement.

Validation on 2026-10-23: the focused pure smoke tests passed locally (13/13),
and the PostgreSQL integration test passed on the disposable PostgreSQL service
in [PR #263's core CI job](https://github.com/omerasik/investing_tradin_platform/actions/runs/39837356817/job/120029048350).
The workstation had no `POSTGRES_TEST_DSN`; a separate local database was not
started because available physical memory was about 278 MB and other running
containers were not identified as disposable for this task. Its Windows Time
service was stopped (`Manual`), and no authorized application/database runtime
was available for an actual clock-offset observation. The CI pass verifies the
query and output contract, not the current deployment's clock alignment. The
next operational check is to repeat this smoke in the authorized deployment
and assess its reported interval alongside that host's time-service status and
the separate market-source clock evidence; one sample cannot establish ongoing
clock health.

For an existing, separately authorized candidate, add `--study-id <UUID> --trial-id
<UUID>` to validate its protected paper-evidence response and report its actual
unrealized availability/reasons. Add `--require-proven-price` only when testing an
installed proven-price deployment; it exits nonzero unless the returned unrealized
mark is `AVAILABLE`. Missing candidate/evidence/configuration never becomes a price.

Minimal proven-price configuration consists of all four existing environment settings:
`TRADE_PLATFORM_TERMINAL_PRICE_SELECTION_PATH`,
`TRADE_PLATFORM_TERMINAL_PRICE_SELECTION_HASH`,
`TRADE_PLATFORM_RESEARCH_DATA_ROOT` and
`TRADE_PLATFORM_UNIVERSE_CAPTURE_ARCHIVE_ROOT`. The JSON policy's canonical identity
hash must match the pin. Its schema is `terminal-paper-price-selection-v1`, with a
nonempty `policy_reference`, aware `declared_at`, and a unique candidate selection
binding `cycle_id`, `study_id`, `trial_id`, stored `entry_fill_hash`, `symbol`,
`source_id`, `session_id`, `instrument`, observation `instant`, explicit integer
`max_age_micros` and `capture_max_unproven_nanos`. These declarations select evidence;
they never contain a supplied price. Consult `terminal_paper_prices_v1.py` for the
existing contract; do not invent age/clock policy values for deployment convenience.

Both roots must contain the exact catalogued frames and independently verifiable
capture partitions referenced by the stored seal. Filesystem access, hash identity,
capture continuity, holdout scope and clock/as-of checks must all succeed. Merely
creating directories is insufficient. Server installation time is an additional
knowledge bound; restarting/reinstalling a policy does not establish its historical
availability. Inspect the smoke's application/DB time observation and retain each
market source's separate clock evidence. Without these prerequisites, keep the
truthful refusal or `UNAVAILABLE` result and leave operator/owner activation gates
closed.

Alert routing currently ends at immutable `LOCAL_OUTBOX` rows whose status is
`PENDING_EXTERNAL_DELIVERY`. Destination references are opaque allowlisted names,
not endpoints. Do not configure network delivery, credentials or a claim of
successful notification until an independently approved adapter implements
retry, current-alert recheck, delivery attempts, escalation and secret handling.

Retention evaluation is evidence-only. Register an approved immutable policy,
then a manifest containing an opaque catalog reference and independently
computed SHA-256. This application does not upload/download object bytes and
must not be given object-store credentials. `ELIGIBLE_FOR_REVIEW` never permits
automatic deletion: verify current policy, legal hold, artifact hash, backup
topology and named human authorization in a separately approved lifecycle
system. Until that system exists, retain the object and append no deletion
claim.
