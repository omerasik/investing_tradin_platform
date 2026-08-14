"""Controlled, fail-closed SQLite-to-PostgreSQL backfill for paper OMS evidence.

The source is never treated as a source of inferred foreign keys.  Operators
must provide every account, signal and instrument mapping explicitly.  The
utility is dry-run by default, records a durable run ID in apply mode, and can
be safely re-run because each source identity has one recorded destination.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4


class BackfillError(RuntimeError):
    pass


SUPPORTED_TABLES = (
    "paper_orders",
    "paper_order_events",
    "paper_fills",
)
LEGACY_TABLES = (
    "research_experiments",
    "validation_artifacts",
    "promotion_decisions",
    "paper_orders",
    "paper_order_events",
    "paper_fills",
)


@dataclass(frozen=True, slots=True)
class BackfillReport:
    dry_run: bool
    row_counts: dict[str, int]
    checksums: dict[str, str]
    conflicts: tuple[str, ...]
    migrated_rows: int
    migration_run_id: UUID | None = None
    destination_checksums: dict[str, str] = field(default_factory=dict)
    unsupported_records: tuple[str, ...] = ()
    post_write_reconciled: bool = False

    @property
    def clean(self) -> bool:
        return not self.conflicts


def _canonical(rows: list[tuple[object, ...]]) -> str:
    encoded = json.dumps(
        [[None if value is None else str(value) for value in row] for row in rows],
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_rows(source: sqlite3.Connection, table: str) -> list[tuple[object, ...]]:
    return source.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()  # nosec B608: fixed allow-list only


def inspect_sqlite_source(database_path: str | Path) -> BackfillReport:
    """Return counts/checksums and unsupported safety records without writing."""
    source = sqlite3.connect(str(database_path))
    try:
        existing = {
            str(row[0])
            for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        counts: dict[str, int] = {}
        checksums: dict[str, str] = {}
        unsupported: list[str] = []
        for table in LEGACY_TABLES:
            rows = _source_rows(source, table) if table in existing else []
            counts[table] = len(rows)
            checksums[table] = _canonical(rows)
            if table not in SUPPORTED_TABLES and rows:
                unsupported.append(f"{table}:{len(rows)}:no_explicit_normalized_mapping")
        for table in sorted(existing - set(LEGACY_TABLES)):
            if table.startswith(
                ("risk_", "kill_switch", "broker_", "paper_reconciliation", "paper_reconciled")
            ):
                count = int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # nosec B608: inspected SQLite identifier
                if count:
                    unsupported.append(f"{table}:{count}:unsupported_safety_record")
        return BackfillReport(
            True, counts, checksums, (), 0, unsupported_records=tuple(unsupported)
        )
    finally:
        source.close()


def load_identity_mapping(mapping_path: str | Path) -> dict[str, dict[str, str]]:
    """Load deterministic legacy->normalized mappings; missing keys are errors."""
    try:
        raw = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackfillError("identity_mapping_unreadable") from error
    if not isinstance(raw, dict) or set(raw) - {"accounts", "signals", "instruments"}:
        raise BackfillError("identity_mapping_invalid_sections")
    result: dict[str, dict[str, str]] = {}
    for section in ("accounts", "signals", "instruments"):
        values = raw.get(section)
        if not isinstance(values, dict) or not values:
            raise BackfillError(f"identity_mapping_{section}_required")
        result[section] = {str(source): str(destination) for source, destination in values.items()}
    for section in ("signals", "instruments"):
        try:
            for target in result[section].values():
                UUID(target)
        except ValueError as error:
            raise BackfillError(f"identity_mapping_{section}_uuid_required") from error
    return result


def _mapping_fingerprint(mapping: dict[str, dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _row_hash(table: str, source_identity: str, row: tuple[object, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            [table, source_identity, [None if value is None else str(value) for value in row]],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _require(mapping: dict[str, dict[str, str]], section: str, source: object) -> str:
    try:
        return mapping[section][str(source)]
    except KeyError as error:
        raise BackfillError(f"unmapped_{section}:{source}") from error


def backfill_sqlite_to_postgres(
    source_path: str | Path,
    postgres_dsn: str,
    *,
    dry_run: bool = True,
    identity_mapping: dict[str, dict[str, str]] | None = None,
) -> BackfillReport:
    """Migrate mapped paper OMS evidence, or inspect only when ``dry_run``.

    Unsupported source records are reported rather than silently guessed or
    dropped.  Apply mode refuses them because a partial historical authority is
    unsafe.  A previous completed matching run is returned idempotently.
    """
    report = inspect_sqlite_source(source_path)
    if dry_run:
        return report
    if not postgres_dsn.startswith(("postgres://", "postgresql://")):
        raise BackfillError("postgres_dsn_required")
    if identity_mapping is None:
        raise BackfillError("legacy_identity_mapping_required")
    if report.unsupported_records:
        raise BackfillError("unsupported_records_require_operator_resolution")
    mapping_fingerprint = _mapping_fingerprint(identity_mapping)
    source_fingerprint = hashlib.sha256(
        json.dumps(report.checksums, sort_keys=True).encode()
    ).hexdigest()
    try:
        import psycopg
    except ImportError as error:
        raise BackfillError("postgres_driver_unavailable") from error
    source = sqlite3.connect(str(source_path))
    connection = psycopg.connect(postgres_dsn)
    run_id = uuid4()
    destination_hashes: dict[str, list[str]] = {table: [] for table in SUPPORTED_TABLES}
    migrated = 0
    try:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT run_id, status, report FROM postgres_backfill_runs WHERE source_fingerprint = %s AND mapping_fingerprint = %s AND mode = 'APPLY'",
                (source_fingerprint, mapping_fingerprint),
            )
            existing = cursor.fetchone()
            if existing is not None and existing[1] == "COMPLETED":
                saved = existing[2]
                return BackfillReport(
                    False,
                    report.row_counts,
                    report.checksums,
                    (),
                    int(saved["migrated_rows"]),
                    UUID(str(existing[0])),
                    dict(saved["destination_checksums"]),
                    tuple(saved.get("unsupported_records", [])),
                    True,
                )
            if existing is not None:
                run_id = UUID(str(existing[0]))
                cursor.execute(
                    "UPDATE postgres_backfill_runs SET status = 'RUNNING', completed_at = NULL WHERE run_id = %s",
                    (run_id,),
                )
            else:
                cursor.execute(
                    "INSERT INTO postgres_backfill_runs (run_id, source_fingerprint, mapping_fingerprint, mode, started_at, status, report) VALUES (%s,%s,%s,'APPLY',%s,'RUNNING','{}'::jsonb)",
                    (run_id, source_fingerprint, mapping_fingerprint, datetime.now(UTC)),
                )
            orders = (
                _source_rows(source, "paper_orders") if report.row_counts["paper_orders"] else []
            )
            for row in orders:
                (
                    intent_id,
                    signal_id,
                    instrument_id,
                    account_id,
                    side,
                    quantity,
                    limit_price,
                    created_at,
                    _status,
                    _filled,
                    _average,
                ) = row
                identity = str(intent_id)
                row_hash = _row_hash("paper_orders", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows WHERE destination_table = 'paper_order_intents' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(f"destination_conflict:paper_orders:{identity}")
                    destination_hashes["paper_orders"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO paper_order_intents (intent_id, signal_id, account_id, instrument_id, side, quantity, limit_price, status, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'PROPOSED',%s)",
                    (
                        UUID(identity),
                        UUID(_require(identity_mapping, "signals", signal_id)),
                        _require(identity_mapping, "accounts", account_id),
                        UUID(_require(identity_mapping, "instruments", instrument_id)),
                        side,
                        quantity,
                        limit_price,
                        created_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES (%s,'paper_orders',%s,'paper_order_intents',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["paper_orders"].append(row_hash)
                migrated += 1
            events = (
                _source_rows(source, "paper_order_events")
                if report.row_counts["paper_order_events"]
                else []
            )
            for row in events:
                _sequence, event_id, intent_id, event_type, occurred_at, payload = row
                identity = str(event_id)
                row_hash = _row_hash("paper_order_events", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows WHERE destination_table = 'oms_events' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(f"destination_conflict:paper_order_events:{identity}")
                    destination_hashes["paper_order_events"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO oms_events (oms_event_id, intent_id, event_type, occurred_at, payload) VALUES (%s,%s,%s,%s,%s::jsonb)",
                    (UUID(identity), UUID(str(intent_id)), event_type, occurred_at, payload),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES (%s,'paper_order_events',%s,'oms_events',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["paper_order_events"].append(row_hash)
                migrated += 1
            fills = _source_rows(source, "paper_fills") if report.row_counts["paper_fills"] else []
            for row in fills:
                _sequence, fill_id, external_fill_id, intent_id, occurred_at, quantity, price = row
                identity = str(fill_id)
                row_hash = _row_hash("paper_fills", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows WHERE destination_table = 'fills' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(f"destination_conflict:paper_fills:{identity}")
                    destination_hashes["paper_fills"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO fills (fill_id, external_fill_id, intent_id, occurred_at, quantity, price) VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        UUID(identity),
                        external_fill_id,
                        UUID(str(intent_id)),
                        occurred_at,
                        quantity,
                        price,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES (%s,'paper_fills',%s,'fills',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["paper_fills"].append(row_hash)
                migrated += 1
            checksums = {
                table: _canonical([(item,) for item in hashes])
                for table, hashes in destination_hashes.items()
            }
            expected = {
                table: _canonical(
                    [
                        (_row_hash(table, str(row[0] if table == "paper_orders" else row[1]), row),)
                        for row in (_source_rows(source, table) if report.row_counts[table] else [])
                    ]
                )
                for table in SUPPORTED_TABLES
            }
            reconciled = checksums == expected
            if not reconciled:
                raise BackfillError("post_write_checksum_mismatch")
            saved_report = {
                "migrated_rows": migrated,
                "destination_checksums": checksums,
                "unsupported_records": [],
            }
            cursor.execute(
                "UPDATE postgres_backfill_runs SET completed_at = %s, status = 'COMPLETED', report = %s::jsonb WHERE run_id = %s",
                (datetime.now(UTC), json.dumps(saved_report, sort_keys=True), run_id),
            )
        return BackfillReport(
            False, report.row_counts, report.checksums, (), migrated, run_id, checksums, (), True
        )
    except BackfillError:
        raise
    except Exception as error:
        raise BackfillError("backfill_transaction_failed") from error
    finally:
        source.close()
        connection.close()
