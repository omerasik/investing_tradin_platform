"""Inspect a local SQLite evidence database before a mapped PostgreSQL backfill."""

from __future__ import annotations

import argparse
import json

from trade_platform.postgres_backfill import backfill_sqlite_to_postgres


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sqlite_path")
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--apply", action="store_true", help="requires explicit legacy identity mapping; otherwise fails closed")
    args = parser.parse_args()
    report = backfill_sqlite_to_postgres(args.sqlite_path, args.postgres_dsn, dry_run=not args.apply)
    print(json.dumps({"dry_run": report.dry_run, "row_counts": report.row_counts, "checksums": report.checksums, "conflicts": report.conflicts, "migrated_rows": report.migrated_rows}, sort_keys=True))


if __name__ == "__main__":
    main()
