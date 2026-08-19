# Deployment Runbook

Development is local and research-only. Future environments are development, test, paper, then production, with signed/versioned artifacts, migration checks, least-privilege secret injection, staged rollout, rollback and backup/restore evidence. No deployment may include live execution without signed readiness approval.

The repository `Dockerfile` is a narrow research API artifact, not the paper or
production deployment recipe. It pins Python 3.12.11 by tag and digest, installs
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

Before any staging/production use, add reviewed image signing and provenance,
registry retention/access controls, IaC, network/TLS and resource policy,
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
CSP's framework-required inline allowances are not a waiver for arbitrary
inline application code. Response headers do not provide OIDC, MFA or RBAC.

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

After any restore, run `scripts/verify_postgres_restore.py --source-dsn ...
--restored-dsn ...` against a separately created database. Do not release the
append-only recovery gate unless revision, critical hashes/counts, validation
manifests, OMS reconstruction, broker cursor, risk reservation, kill switch,
promotion and reconciliation checks all pass. A corrupt or incomplete dump is
a failed deployment.

Operational job policies and terminal run evidence do not deploy or authorize a
scheduler. A deployment-owned scheduler may only invoke separately approved job
entry points and must append terminal evidence with a unique idempotency key.
The monitor evaluates durable evidence and may open/recover local overdue alerts;
it never executes due work.

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
