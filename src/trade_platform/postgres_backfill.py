"""Fail-closed SQLite evidence inspection and PostgreSQL backfill support."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class BackfillError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackfillReport:
    dry_run: bool
    row_counts: dict[str, int]
    checksums: dict[str, str]
    conflicts: tuple[str, ...]
    migrated_rows: int

    @property
    def clean(self) -> bool:
        return not self.conflicts


SUPPORTED_TABLES = ("research_experiments", "validation_artifacts", "promotion_decisions", "paper_orders", "paper_order_events", "paper_fills")


def inspect_sqlite_source(database_path: str | Path) -> BackfillReport:
    """Count and checksum supported immutable records before any target write."""
    source = sqlite3.connect(str(database_path))
    try:
        existing = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        counts: dict[str, int] = {}; checksums: dict[str, str] = {}
        for table in SUPPORTED_TABLES:
            if table not in existing:
                counts[table] = 0; checksums[table] = hashlib.sha256(b"").hexdigest(); continue
            rows = source.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()  # nosec B608: table is from constant allowlist
            encoded = json.dumps([[None if value is None else str(value) for value in row] for row in rows], separators=(",", ":")).encode()
            counts[table] = len(rows); checksums[table] = hashlib.sha256(encoded).hexdigest()
        return BackfillReport(True, counts, checksums, (), 0)
    finally:
        source.close()


def backfill_sqlite_to_postgres(source_path: str | Path, postgres_dsn: str, *, dry_run: bool = True) -> BackfillReport:
    """Backfill supported source rows transactionally after duplicate detection.

    This utility intentionally refuses to infer missing foreign-key mappings.
    Production operators must map legacy string versions to normalized strategy
    and dataset identities before writing the data.
    """
    report = inspect_sqlite_source(source_path)
    if dry_run:
        return report
    if not postgres_dsn.startswith(("postgres://", "postgresql://")):
        raise BackfillError("postgres_dsn_required")
    # Current local records use string version IDs while the normalized schema
    # uses foreign keys. A blind conversion would orphan provenance, so writing
    # is intentionally blocked until an explicit mapping file is supplied.
    raise BackfillError("legacy_identity_mapping_required")
