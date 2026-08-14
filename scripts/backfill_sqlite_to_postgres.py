"""Inspect a local SQLite evidence database before a mapped PostgreSQL backfill."""

from __future__ import annotations

import argparse
import json

from trade_platform.postgres_backfill import (
    backfill_sqlite_to_postgres,
    load_identity_mapping,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sqlite_path")
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--apply", action="store_true", help="perform a write; dry-run remains the default")
    parser.add_argument("--identity-mapping", help="JSON mapping with explicit accounts, signals and instruments sections; required with --apply")
    args = parser.parse_args()
    if args.apply and not args.identity_mapping:
        parser.error("--apply requires --identity-mapping")
    mapping = load_identity_mapping(args.identity_mapping) if args.identity_mapping else None
    report = backfill_sqlite_to_postgres(args.sqlite_path, args.postgres_dsn, dry_run=not args.apply, identity_mapping=mapping)
    print(json.dumps({"dry_run": report.dry_run, "migration_run_id": None if report.migration_run_id is None else str(report.migration_run_id), "row_counts": report.row_counts, "checksums": report.checksums, "destination_checksums": report.destination_checksums, "conflicts": report.conflicts, "unsupported_records": report.unsupported_records, "migrated_rows": report.migrated_rows, "post_write_reconciled": report.post_write_reconciled}, sort_keys=True))


if __name__ == "__main__":
    main()
