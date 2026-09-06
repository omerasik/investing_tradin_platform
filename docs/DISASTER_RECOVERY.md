# Disaster Recovery

Target controls: encrypted backups for metadata, artifacts, configuration and audit events; tested restores; idempotent event processing; reconciliation after disruption; defined RPO/RTO; rollback procedures and incident communication. Required drills include datastore, queue, cache, provider, network, duplicate-event, clock-drift and corrupt-data failure. No production assets exist yet.

**Staging (Module 3E — see `docs/MODULE_3E_STAGING_DEPLOYMENT_AND_SCHEDULER.md` §7):**
RPO is bounded by whatever backup cadence the deployer's infrastructure runs
`pg_dump --format=custom` on against the staging database (this module introduces no
new backup mechanism of its own). RTO target: restore into a fresh database, run
`alembic upgrade head`, then run `scripts/verify_postgres_restore.py --source-dsn ...
--restored-dsn ...` — the same script and count/checksum evidence CI already
produces on every PR. A failed or incomplete verification is a failed restore; never
point `api`/`worker` at a database that has not passed it. This is still not an
encrypted, off-site, continuously-scheduled backup system, nor a production RPO/RTO
commitment — it is the staging-shaped restore procedure and evidence this module
adds on top of the existing CI drill described below.

For the PostgreSQL migration path, first run `alembic upgrade head` against an
empty disposable database and retain the generated migration revision. A local
legacy SQLite database must be inspected with
`scripts/backfill_sqlite_to_postgres.py <sqlite-path> --postgres-dsn <dsn>` in
dry-run mode; it reports supported-table row counts and checksums and refuses a
write without explicit identity mapping. Production migrations are forward-only:
do not run schema downgrades against a database with evidence. Restore drills
must restore into a fresh database, run migrations, compare counts/checksums,
then reconcile OMS/risk state before permitting a paper risk increase.

CI now executes `pg_dump --format=custom`, corrupt-backup rejection, separate
database creation, `pg_restore`, Alembic revision comparison, count plus SHA-256
content comparison for the critical table allow-list (35 tables after the Cycle
15 core), manifest classification and OMS/
cursor/risk/kill-switch/promotion/reconciliation reconstruction. Migration 0007
adds an append-only recovery gate; risk increase remains blocked until checks
and reconciliation release it. This proves logical CI recovery, not encrypted
off-site retention or production RPO/RTO.
