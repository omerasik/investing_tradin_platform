# Deployment Runbook

Development is local and research-only. Future environments are development, test, paper, then production, with signed/versioned artifacts, migration checks, least-privilege secret injection, staged rollout, rollback and backup/restore evidence. No deployment may include live execution without signed readiness approval.

For a PostgreSQL environment, inject the DSN through the deployment secret
manager, set `persistence_target=postgres`, and run `alembic upgrade head`
before application startup. `paper` and `production` configuration reject
SQLite. Perform the SQLite backfill dry-run and resolve every identity conflict
before any mapped write. Downgrades are permitted only for disposable schema
tests; production migrations are forward-only. A failed migration, unavailable
database, stale reconciliation or incomplete restore must leave risk increase
and paper submission fail-closed.

After any restore, run `scripts/verify_postgres_restore.py --source-dsn ...
--restored-dsn ...` against a separately created database. Do not release the
append-only recovery gate unless revision, critical hashes/counts, validation
manifests, OMS reconstruction, broker cursor, risk reservation, kill switch,
promotion and reconciliation checks all pass. A corrupt or incomplete dump is
a failed deployment.
