# Disaster Recovery

Target controls: encrypted backups for metadata, artifacts, configuration and audit events; tested restores; idempotent event processing; reconciliation after disruption; defined RPO/RTO; rollback procedures and incident communication. Required drills include datastore, queue, cache, provider, network, duplicate-event, clock-drift and corrupt-data failure. No production assets exist yet.

For the PostgreSQL migration path, first run `alembic upgrade head` against an
empty disposable database and retain the generated migration revision. A local
legacy SQLite database must be inspected with
`scripts/backfill_sqlite_to_postgres.py <sqlite-path> --postgres-dsn <dsn>` in
dry-run mode; it reports supported-table row counts and checksums and refuses a
write without explicit identity mapping. Production migrations are forward-only:
do not run schema downgrades against a database with evidence. Restore drills
must restore into a fresh database, run migrations, compare counts/checksums,
then reconcile OMS/risk state before permitting a paper risk increase.
