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
