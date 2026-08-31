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
    "instruments",
    "signals",
    "signal_validations",
    "policy_documents",
    "model_versions",
    "model_validations",
    "model_approval_events",
    "paper_orders",
    "paper_order_events",
    "paper_fills",
    "risk_daily_notional",
    "risk_decisions",
    "kill_switch_events",
    "broker_events",
    "broker_cursors",
    "paper_reconciliation_runs",
    "paper_reconciled_accounts",
    "validation_artifacts",
    "promotion_decisions",
    "strategy_activations",
    "audit_events",
)
LEGACY_TABLES = (
    "research_experiments",
    *SUPPORTED_TABLES,
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
    source_fingerprint: str = ""
    mapping_fingerprint: str = ""
    source_row_hashes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    destination_row_hashes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    destination_counts: dict[str, int] = field(default_factory=dict)
    skipped_records: tuple[str, ...] = ()
    applied_records: tuple[str, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None

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
        row_hashes: dict[str, tuple[str, ...]] = {}
        unsupported: list[str] = []
        for table in LEGACY_TABLES:
            rows = _source_rows(source, table) if table in existing else []
            counts[table] = len(rows)
            checksums[table] = _canonical(rows)
            row_hashes[table] = tuple(
                _row_hash(table, str(index), row) for index, row in enumerate(rows, 1)
            )
            if table not in SUPPORTED_TABLES and rows:
                unsupported.append(f"{table}:{len(rows)}:no_explicit_normalized_mapping")
        for table in sorted(existing - set(LEGACY_TABLES) - {"sqlite_sequence"}):
            count = int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # nosec B608: inspected SQLite identifier
            if count:
                unsupported.append(f"{table}:{count}:unsupported_legacy_record")
        source_fingerprint = hashlib.sha256(
            json.dumps(checksums, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return BackfillReport(
            True,
            counts,
            checksums,
            (),
            0,
            migration_run_id=uuid4(),
            unsupported_records=tuple(unsupported),
            source_fingerprint=source_fingerprint,
            source_row_hashes=row_hashes,
        )
    finally:
        source.close()


def load_identity_mapping(mapping_path: str | Path) -> dict[str, dict[str, str]]:
    """Load deterministic legacy->normalized mappings; missing keys are errors."""
    try:
        raw = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackfillError("identity_mapping_unreadable") from error
    allowed = {
        "accounts",
        "signals",
        "instruments",
        "strategy_versions",
        "dataset_versions",
        "policy_versions",
        "risk_decision_policies",
        "risk_reservations",
        "broker_accounts",
        "promotion_packages",
    }
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise BackfillError("identity_mapping_invalid_sections")
    result: dict[str, dict[str, str]] = {}
    for section in ("accounts", "signals", "instruments"):
        values = raw.get(section)
        if not isinstance(values, dict) or not values:
            raise BackfillError(f"identity_mapping_{section}_required")
        result[section] = {str(source): str(destination) for source, destination in values.items()}
    for section in sorted(allowed - {"accounts", "signals", "instruments"}):
        values = raw.get(section, {})
        if not isinstance(values, dict):
            raise BackfillError(f"identity_mapping_{section}_invalid")
        result[section] = {str(source): str(destination) for source, destination in values.items()}
    for section in (
        "signals",
        "instruments",
        "strategy_versions",
        "dataset_versions",
        "risk_decision_policies",
        "risk_reservations",
        "promotion_packages",
    ):
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


def _validate_required_mappings(
    source: sqlite3.Connection,
    report: BackfillReport,
    mapping: dict[str, dict[str, str]],
) -> None:
    """Resolve every unsafe identity before opening a destination transaction."""
    for row in _source_rows(source, "instruments") if report.row_counts["instruments"] else []:
        _require(mapping, "instruments", row[0])
    for row in _source_rows(source, "signals") if report.row_counts["signals"] else []:
        payload = json.loads(str(row[1]))
        _require(mapping, "signals", row[0])
        _require(mapping, "instruments", payload["instrument_id"])
        _require(mapping, "strategy_versions", payload["strategy_version"])
    for row in _source_rows(source, "policy_documents") if report.row_counts["policy_documents"] else []:
        _require(mapping, "policy_versions", f"{row[0]}:{row[1]}")
    for row in _source_rows(source, "model_versions") if report.row_counts["model_versions"] else []:
        _require(mapping, "dataset_versions", row[5])
    for row in _source_rows(source, "paper_orders") if report.row_counts["paper_orders"] else []:
        _require(mapping, "signals", row[1])
        _require(mapping, "instruments", row[2])
        _require(mapping, "accounts", row[3])
    for row in _source_rows(source, "risk_daily_notional") if report.row_counts["risk_daily_notional"] else []:
        _require(mapping, "accounts", row[1])
        _require(mapping, "risk_reservations", row[0])
    for row in _source_rows(source, "risk_decisions") if report.row_counts["risk_decisions"] else []:
        _require(mapping, "risk_decision_policies", row[0])
    for row in _source_rows(source, "broker_events") if report.row_counts["broker_events"] else []:
        _require(mapping, "broker_accounts", row[1])
    for row in _source_rows(source, "broker_cursors") if report.row_counts["broker_cursors"] else []:
        _require(mapping, "broker_accounts", row[0])
    for row in _source_rows(source, "paper_reconciliation_runs") if report.row_counts["paper_reconciliation_runs"] else []:
        _require(mapping, "accounts", row[1])
    for row in _source_rows(source, "paper_reconciled_accounts") if report.row_counts["paper_reconciled_accounts"] else []:
        _require(mapping, "accounts", row[2])
        snapshot = json.loads(str(row[4]))
        for instrument_id in snapshot["positions"]:
            _require(mapping, "instruments", instrument_id)
    for row in _source_rows(source, "validation_artifacts") if report.row_counts["validation_artifacts"] else []:
        _require(mapping, "strategy_versions", row[3])
        _require(mapping, "dataset_versions", row[4])
    for row in _source_rows(source, "promotion_decisions") if report.row_counts["promotion_decisions"] else []:
        _require(mapping, "strategy_versions", row[2])
        _require(mapping, "promotion_packages", row[0])
    for row in _source_rows(source, "strategy_activations") if report.row_counts["strategy_activations"] else []:
        _require(mapping, "strategy_versions", row[1])


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
    connection = None
    try:
        _validate_required_mappings(source, report, identity_mapping)
        connection = psycopg.connect(postgres_dsn)
        run_id = uuid4()
        destination_hashes: dict[str, list[str]] = {table: [] for table in SUPPORTED_TABLES}
        migrated = 0
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
            instruments = (
                _source_rows(source, "instruments") if report.row_counts["instruments"] else []
            )
            for row in instruments:
                (
                    legacy_id,
                    symbol,
                    venue,
                    asset_class,
                    currency,
                    tick_size,
                    lot_size,
                    delisted_at,
                ) = row
                identity = _require(identity_mapping, "instruments", legacy_id)
                row_hash = _row_hash("instruments", str(legacy_id), row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'runtime_instruments' "
                    "AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(f"destination_conflict:instruments:{legacy_id}")
                    destination_hashes["instruments"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO runtime_instruments "
                    "(instrument_id, symbol, venue, asset_class, quote_currency, tick_size, "
                    "lot_size, delisted_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity,
                        symbol,
                        venue,
                        asset_class,
                        currency,
                        tick_size,
                        lot_size,
                        delisted_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'instruments',%s,'runtime_instruments',%s,%s)",
                    (run_id, str(legacy_id), identity, row_hash),
                )
                destination_hashes["instruments"].append(row_hash)
                migrated += 1

            signals = _source_rows(source, "signals") if report.row_counts["signals"] else []
            for row in signals:
                legacy_id, payload_json, created_at, _current_status = row
                payload = json.loads(str(payload_json))
                identity = _require(identity_mapping, "signals", legacy_id)
                instrument_id = _require(
                    identity_mapping, "instruments", payload["instrument_id"]
                )
                strategy_version = _require(
                    identity_mapping, "strategy_versions", payload["strategy_version"]
                )
                row_hash = _row_hash("signals", str(legacy_id), row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'runtime_signal_proposals' "
                    "AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(f"destination_conflict:signals:{legacy_id}")
                    destination_hashes["signals"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO runtime_signal_proposals "
                    "(signal_id, instrument_id, strategy_version, created_at, expires_at, payload) "
                    "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                    (
                        UUID(identity),
                        instrument_id,
                        strategy_version,
                        created_at,
                        payload["expires_at"],
                        json.dumps(payload, sort_keys=True),
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'signals',%s,'runtime_signal_proposals',%s,%s)",
                    (run_id, str(legacy_id), identity, row_hash),
                )
                destination_hashes["signals"].append(row_hash)
                migrated += 1

            validations = (
                _source_rows(source, "signal_validations")
                if report.row_counts["signal_validations"]
                else []
            )
            for row in validations:
                assessment_id, signal_id, status, passed, failures, assessed_at = row
                identity = str(assessment_id)
                row_hash = _row_hash("signal_validations", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'runtime_signal_validations' "
                    "AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:signal_validations:{identity}"
                        )
                    destination_hashes["signal_validations"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO runtime_signal_validations VALUES "
                    "(%s,%s,%s,%s::jsonb,%s::jsonb,%s)",
                    (
                        UUID(identity),
                        UUID(_require(identity_mapping, "signals", signal_id)),
                        status,
                        passed,
                        failures,
                        assessed_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'signal_validations',%s,'runtime_signal_validations',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["signal_validations"].append(row_hash)
                migrated += 1

            policies = (
                _source_rows(source, "policy_documents")
                if report.row_counts["policy_documents"]
                else []
            )
            for row in policies:
                policy_type, version, payload, digest, approved_by, approved_at = row
                source_identity = f"{policy_type}:{version}"
                destination_version = _require(
                    identity_mapping, "policy_versions", source_identity
                )
                destination_identity = f"{policy_type}:{destination_version}"
                row_hash = _row_hash("policy_documents", source_identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'runtime_policy_documents' "
                    "AND destination_identity = %s",
                    (destination_identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:policy_documents:{source_identity}"
                        )
                    destination_hashes["policy_documents"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO runtime_policy_documents VALUES "
                    "(%s,%s,%s::jsonb,%s,%s,%s)",
                    (
                        policy_type,
                        destination_version,
                        payload,
                        digest,
                        approved_by,
                        approved_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'policy_documents',%s,'runtime_policy_documents',%s,%s)",
                    (run_id, source_identity, destination_identity, row_hash),
                )
                destination_hashes["policy_documents"].append(row_hash)
                migrated += 1

            models = (
                _source_rows(source, "model_versions")
                if report.row_counts["model_versions"]
                else []
            )
            for row in models:
                (
                    model_id,
                    name,
                    version,
                    task,
                    feature_version,
                    dataset_version,
                    artifact_reference,
                    created_at,
                ) = row
                identity = str(model_id)
                row_hash = _row_hash("model_versions", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'runtime_models' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(f"destination_conflict:model_versions:{identity}")
                    destination_hashes["model_versions"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO runtime_models VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        UUID(identity),
                        name,
                        version,
                        task,
                        feature_version,
                        _require(identity_mapping, "dataset_versions", dataset_version),
                        artifact_reference,
                        created_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'model_versions',%s,'runtime_models',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["model_versions"].append(row_hash)
                migrated += 1

            model_validations = (
                _source_rows(source, "model_validations")
                if report.row_counts["model_validations"]
                else []
            )
            for row in model_validations:
                validation_id, model_id, metrics, value, reference, validated_at = row
                identity = str(validation_id)
                row_hash = _row_hash("model_validations", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'runtime_model_validations' "
                    "AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:model_validations:{identity}"
                        )
                    destination_hashes["model_validations"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO runtime_model_validations VALUES "
                    "(%s,%s,%s::jsonb,%s,%s,%s)",
                    (UUID(identity), UUID(str(model_id)), metrics, value, reference, validated_at),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'model_validations',%s,'runtime_model_validations',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["model_validations"].append(row_hash)
                migrated += 1

            approval_events = (
                _source_rows(source, "model_approval_events")
                if report.row_counts["model_approval_events"]
                else []
            )
            for row in approval_events:
                (
                    _sequence,
                    event_id,
                    model_id,
                    status,
                    actor,
                    reason,
                    validation_id,
                    occurred_at,
                ) = row
                identity = str(event_id)
                row_hash = _row_hash("model_approval_events", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'runtime_model_approval_events' "
                    "AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:model_approval_events:{identity}"
                        )
                    destination_hashes["model_approval_events"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO runtime_model_approval_events "
                    "(event_id, model_id, status, actor, reason, validation_id, occurred_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        UUID(identity),
                        UUID(str(model_id)),
                        status,
                        actor,
                        reason,
                        None if validation_id is None else UUID(str(validation_id)),
                        occurred_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'model_approval_events',%s,'runtime_model_approval_events',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["model_approval_events"].append(row_hash)
                migrated += 1
            orders = (
                _source_rows(source, "paper_orders") if report.row_counts["paper_orders"] else []
            )
            for row in orders:
                (
                    intent_id,
                    order_signal_id,
                    order_instrument_id,
                    order_account_id,
                    order_side,
                    order_quantity,
                    order_limit_price,
                    order_created_at,
                    _order_status,
                    _order_filled,
                    _order_average,
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
                        UUID(_require(identity_mapping, "signals", order_signal_id)),
                        _require(identity_mapping, "accounts", order_account_id),
                        UUID(_require(identity_mapping, "instruments", order_instrument_id)),
                        order_side,
                        order_quantity,
                        order_limit_price,
                        order_created_at,
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
            reservations = (
                _source_rows(source, "risk_daily_notional")
                if report.row_counts["risk_daily_notional"]
                else []
            )
            for row in reservations:
                intent_id, account_id, trading_day, notional = row
                identity = _require(identity_mapping, "risk_reservations", intent_id)
                row_hash = _row_hash("risk_daily_notional", str(intent_id), row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'risk_reservations' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:risk_daily_notional:{intent_id}"
                        )
                    destination_hashes["risk_daily_notional"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO risk_reservations "
                    "(reservation_id, account_id, intent_id, business_date, notional, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        UUID(identity),
                        _require(identity_mapping, "accounts", account_id),
                        UUID(str(intent_id)),
                        trading_day,
                        notional,
                        datetime.now(UTC),
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'risk_daily_notional',%s,'risk_reservations',%s,%s)",
                    (run_id, str(intent_id), identity, row_hash),
                )
                destination_hashes["risk_daily_notional"].append(row_hash)
                migrated += 1

            risk_decisions = (
                _source_rows(source, "risk_decisions")
                if report.row_counts["risk_decisions"]
                else []
            )
            for row in risk_decisions:
                decision_id, intent_id, decision, reasons, decided_at = row
                identity = str(decision_id)
                row_hash = _row_hash("risk_decisions", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'risk_decisions' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(f"destination_conflict:risk_decisions:{identity}")
                    destination_hashes["risk_decisions"].append(row_hash)
                    continue
                policy_version_id = _require(
                    identity_mapping, "risk_decision_policies", decision_id
                )
                cursor.execute(
                    "INSERT INTO risk_decisions "
                    "(risk_decision_id, intent_id, risk_policy_version_id, approved, "
                    "reasons, decided_at) VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                    (
                        UUID(identity),
                        UUID(str(intent_id)),
                        UUID(policy_version_id),
                        str(decision) == "APPROVE",
                        reasons,
                        decided_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'risk_decisions',%s,'risk_decisions',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["risk_decisions"].append(row_hash)
                migrated += 1

            switches = (
                _source_rows(source, "kill_switch_events")
                if report.row_counts["kill_switch_events"]
                else []
            )
            for row in switches:
                _sequence, event_id, scope, active, actor, occurred_at = row
                identity = str(event_id)
                row_hash = _row_hash("kill_switch_events", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'kill_switch_events' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:kill_switch_events:{identity}"
                        )
                    destination_hashes["kill_switch_events"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO kill_switch_events VALUES (%s,%s,%s,%s,%s)",
                    (UUID(identity), scope, bool(active), actor, occurred_at),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'kill_switch_events',%s,'kill_switch_events',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["kill_switch_events"].append(row_hash)
                migrated += 1

            broker_events = (
                _source_rows(source, "broker_events")
                if report.row_counts["broker_events"]
                else []
            )
            for row in broker_events:
                (
                    event_id,
                    broker_name,
                    sequence,
                    external_event_id,
                    intent_id,
                    event_type,
                    status,
                    occurred_at,
                ) = row
                identity = str(event_id)
                row_hash = _row_hash("broker_events", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'broker_sync_events' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(f"destination_conflict:broker_events:{identity}")
                    destination_hashes["broker_events"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO broker_sync_events "
                    "(event_id, account_id, source, sequence, external_event_id, intent_id, "
                    "event_type, status, occurred_at, details) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb)",
                    (
                        UUID(identity),
                        _require(identity_mapping, "broker_accounts", broker_name),
                        broker_name,
                        sequence,
                        external_event_id,
                        UUID(str(intent_id)),
                        event_type,
                        status,
                        occurred_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'broker_events',%s,'broker_sync_events',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["broker_events"].append(row_hash)
                migrated += 1

            cursors = (
                _source_rows(source, "broker_cursors")
                if report.row_counts["broker_cursors"]
                else []
            )
            for row in cursors:
                broker_name, sequence = row
                account_id = _require(identity_mapping, "broker_accounts", broker_name)
                identity = f"{account_id}:{broker_name}"
                row_hash = _row_hash("broker_cursors", str(broker_name), row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'broker_event_cursors' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(f"destination_conflict:broker_cursors:{broker_name}")
                    destination_hashes["broker_cursors"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO broker_event_cursors "
                    "(account_id, source, cursor, advanced_at) VALUES (%s,%s,%s,%s)",
                    (account_id, broker_name, str(sequence), datetime.now(UTC)),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'broker_cursors',%s,'broker_event_cursors',%s,%s)",
                    (run_id, str(broker_name), identity, row_hash),
                )
                destination_hashes["broker_cursors"].append(row_hash)
                migrated += 1

            reconciliations = (
                _source_rows(source, "paper_reconciliation_runs")
                if report.row_counts["paper_reconciliation_runs"]
                else []
            )
            for row in reconciliations:
                run, account_id, source_name, occurred_at, complete, discrepancies = row
                identity = str(run)
                row_hash = _row_hash("paper_reconciliation_runs", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'reconciliations' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:paper_reconciliation_runs:{identity}"
                        )
                    destination_hashes["paper_reconciliation_runs"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO reconciliations VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                    (
                        UUID(identity),
                        _require(identity_mapping, "accounts", account_id),
                        source_name,
                        occurred_at,
                        bool(complete),
                        discrepancies,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'paper_reconciliation_runs',%s,'reconciliations',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["paper_reconciliation_runs"].append(row_hash)
                migrated += 1

            reconciled_accounts = (
                _source_rows(source, "paper_reconciled_accounts")
                if report.row_counts["paper_reconciled_accounts"]
                else []
            )
            for row in reconciled_accounts:
                (
                    evidence_id,
                    reconciliation_id,
                    account_id,
                    source_name,
                    snapshot_json,
                    healthy,
                    ingested_at,
                ) = row
                identity = str(evidence_id)
                snapshot = json.loads(str(snapshot_json))
                row_hash = _row_hash("paper_reconciled_accounts", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'reconciled_account_evidence' "
                    "AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:paper_reconciled_accounts:{identity}"
                        )
                    destination_hashes["paper_reconciled_accounts"].append(row_hash)
                    continue
                target_account = _require(identity_mapping, "accounts", account_id)
                cursor.execute(
                    "INSERT INTO reconciled_account_evidence "
                    "(evidence_id, reconciliation_id, account_id, source, as_of, cash_currency, "
                    "cash_amount, buying_power, open_intent_ids, fill_ids, realized_pnl, fees, "
                    "healthy, ingested_at) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)",
                    (
                        UUID(identity),
                        UUID(str(reconciliation_id)),
                        target_account,
                        source_name,
                        snapshot["as_of"],
                        snapshot["cash_currency"],
                        snapshot["cash_amount"],
                        snapshot["buying_power"],
                        json.dumps(snapshot["open_intent_ids"]),
                        json.dumps(snapshot["fill_ids"]),
                        snapshot["realized_pnl"],
                        snapshot["fees"],
                        bool(healthy),
                        ingested_at,
                    ),
                )
                for legacy_instrument, values in sorted(snapshot["positions"].items()):
                    cursor.execute(
                        "INSERT INTO reconciled_position_evidence "
                        "(evidence_id, instrument_id, quantity, average_cost) "
                        "VALUES (%s,%s,%s,%s)",
                        (
                            UUID(identity),
                            UUID(
                                _require(
                                    identity_mapping, "instruments", legacy_instrument
                                )
                            ),
                            values[0],
                            values[1],
                        ),
                    )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'paper_reconciled_accounts',%s,'reconciled_account_evidence',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["paper_reconciled_accounts"].append(row_hash)
                migrated += 1
            validation_rows = (
                _source_rows(source, "validation_artifacts")
                if report.row_counts["validation_artifacts"]
                else []
            )
            for row in [item for item in validation_rows if item[2] != "StrategyValidationPackage"]:
                artifact_id, content_hash, _artifact_class, strategy, dataset, payload_json, created_at = row
                identity = str(artifact_id)
                payload = json.loads(str(payload_json))
                evidence_type = str(payload.get("evidence_type", ""))
                if not evidence_type:
                    raise BackfillError(f"unsupported_validation_artifact:{identity}")
                row_hash = _row_hash("validation_artifacts", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'quant_validation_artifacts' "
                    "AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:validation_artifacts:{identity}"
                        )
                    destination_hashes["validation_artifacts"].append(row_hash)
                    continue
                identity_payload = payload.get("identity", {})
                cursor.execute(
                    "INSERT INTO quant_validation_artifacts "
                    "(quant_artifact_id, artifact_type, strategy_version_id, "
                    "dataset_version_id, artifact_version, content_hash, passed, "
                    "evaluated_at, payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (
                        UUID(identity),
                        evidence_type,
                        UUID(_require(identity_mapping, "strategy_versions", strategy)),
                        UUID(_require(identity_mapping, "dataset_versions", dataset)),
                        str(identity_payload.get("artifact_version", "legacy-v1")),
                        content_hash,
                        payload.get("passed"),
                        identity_payload.get("evaluated_at", created_at),
                        json.dumps(payload, sort_keys=True),
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'validation_artifacts',%s,'quant_validation_artifacts',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["validation_artifacts"].append(row_hash)
                migrated += 1

            for row in [item for item in validation_rows if item[2] == "StrategyValidationPackage"]:
                package_id, content_hash, _artifact_class, strategy, dataset, payload_json, created_at = row
                identity = str(package_id)
                payload = json.loads(str(payload_json))
                row_hash = _row_hash("validation_artifacts", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'validation_packages' "
                    "AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:validation_artifacts:{identity}"
                        )
                    destination_hashes["validation_artifacts"].append(row_hash)
                    continue
                limitations = list(payload.get("limitations", []))
                if "legacy_unverifiable" not in limitations:
                    limitations.append("legacy_unverifiable")
                cursor.execute(
                    "INSERT INTO validation_packages "
                    "(package_id, strategy_version_id, dataset_version_id, cost_model_version, "
                    "content_hash, status, created_at, limitations, feature_versions, "
                    "manifest_version, canonical_manifest, integrity_status) "
                    "VALUES (%s,%s,%s,%s,%s,'REVIEW_REQUIRED_OR_BLOCKED',%s,%s::jsonb,"
                    "%s::jsonb,NULL,NULL,'LEGACY_UNVERIFIABLE')",
                    (
                        UUID(identity),
                        UUID(_require(identity_mapping, "strategy_versions", strategy)),
                        UUID(_require(identity_mapping, "dataset_versions", dataset)),
                        str(payload.get("cost_model_version", "legacy-unverified")),
                        content_hash,
                        created_at,
                        json.dumps(limitations),
                        json.dumps(payload.get("feature_versions", [])),
                    ),
                )
                evidence_ids = payload.get("evidence_ids", {})
                if not isinstance(evidence_ids, dict):
                    raise BackfillError(f"invalid_validation_package_evidence:{identity}")
                for evidence_type, artifact_id in sorted(evidence_ids.items()):
                    cursor.execute(
                        "INSERT INTO validation_package_artifacts "
                        "(package_id, quant_artifact_id, evidence_type) VALUES (%s,%s,%s)",
                        (UUID(identity), UUID(str(artifact_id)), evidence_type),
                    )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'validation_artifacts',%s,'validation_packages',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["validation_artifacts"].append(row_hash)
                migrated += 1

            promotions = (
                _source_rows(source, "promotion_decisions")
                if report.row_counts["promotion_decisions"]
                else []
            )
            for row in promotions:
                (
                    decision_id,
                    _strategy_id,
                    promotion_strategy_version,
                    promotion_status,
                    promotion_reasons,
                    promotion_held_out_periods,
                    promotion_held_out_return,
                    promotion_decided_at,
                ) = row
                identity = str(decision_id)
                row_hash = _row_hash("promotion_decisions", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'strategy_promotion_decisions' "
                    "AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:promotion_decisions:{identity}"
                        )
                    destination_hashes["promotion_decisions"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO strategy_promotion_decisions "
                    "(decision_id, package_id, strategy_version_id, status, reasons, "
                    "held_out_periods, held_out_total_return, decided_at) "
                    "VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
                    (
                        UUID(identity),
                        UUID(_require(identity_mapping, "promotion_packages", decision_id)),
                        UUID(
                            _require(
                                identity_mapping,
                                "strategy_versions",
                                promotion_strategy_version,
                            )
                        ),
                        promotion_status,
                        promotion_reasons,
                        promotion_held_out_periods,
                        promotion_held_out_return,
                        promotion_decided_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'promotion_decisions',%s,'strategy_promotion_decisions',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["promotion_decisions"].append(row_hash)
                migrated += 1

            activations = (
                _source_rows(source, "strategy_activations")
                if report.row_counts["strategy_activations"]
                else []
            )
            for row in activations:
                (
                    activation_id,
                    activation_strategy_version,
                    activation_active,
                    activation_actor,
                    activation_effective_at,
                    activation_ingested_at,
                    activation_promotion_decision_id,
                ) = row
                identity = str(activation_id)
                row_hash = _row_hash("strategy_activations", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'strategy_activation_events' "
                    "AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(
                            f"destination_conflict:strategy_activations:{identity}"
                        )
                    destination_hashes["strategy_activations"].append(row_hash)
                    continue
                cursor.execute(
                    "INSERT INTO strategy_activation_events VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        UUID(identity),
                        UUID(
                            _require(
                                identity_mapping,
                                "strategy_versions",
                                activation_strategy_version,
                            )
                        ),
                        bool(activation_active),
                        activation_actor,
                        activation_effective_at,
                        activation_ingested_at,
                        None
                        if activation_promotion_decision_id is None
                        else UUID(str(activation_promotion_decision_id)),
                    ),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'strategy_activations',%s,'strategy_activation_events',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["strategy_activations"].append(row_hash)
                migrated += 1
            audit_rows = (
                _source_rows(source, "audit_events")
                if report.row_counts["audit_events"]
                else []
            )
            for row in audit_rows:
                event_id, event_type, occurred_at, actor, payload = row
                identity = str(event_id)
                row_hash = _row_hash("audit_events", identity, row)
                cursor.execute(
                    "SELECT content_hash FROM postgres_backfill_rows "
                    "WHERE destination_table = 'audit_events' AND destination_identity = %s",
                    (identity,),
                )
                existing_hash = cursor.fetchone()
                if existing_hash is not None:
                    if str(existing_hash[0]) != row_hash:
                        raise BackfillError(f"destination_conflict:audit_events:{identity}")
                    destination_hashes["audit_events"].append(row_hash)
                    continue
                audit_hash = hashlib.sha256(
                    json.dumps(
                        {
                            "actor": actor,
                            "event_id": identity,
                            "event_type": event_type,
                            "occurred_at": str(occurred_at),
                            "payload": json.loads(str(payload)),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                cursor.execute(
                    "INSERT INTO audit_events "
                    "(audit_event_id, actor, event_type, occurred_at, entity_type, "
                    "entity_id, payload, content_hash) "
                    "VALUES (%s,%s,%s,%s,'legacy_workflow',%s,%s::jsonb,%s)",
                    (UUID(identity), actor, event_type, occurred_at, identity, payload, audit_hash),
                )
                cursor.execute(
                    "INSERT INTO postgres_backfill_rows VALUES "
                    "(%s,'audit_events',%s,'audit_events',%s,%s)",
                    (run_id, identity, identity, row_hash),
                )
                destination_hashes["audit_events"].append(row_hash)
                migrated += 1
            checksums = {
                table: _canonical([(item,) for item in hashes])
                for table, hashes in destination_hashes.items()
            }
            destination_counts = {
                table: len(hashes) for table, hashes in destination_hashes.items()
            }
            reconciled = destination_counts == {
                table: report.row_counts[table] for table in SUPPORTED_TABLES
            }
            if not reconciled:
                raise BackfillError("post_write_row_reconciliation_mismatch")
            completed_at = datetime.now(UTC)
            saved_report = {
                "migrated_rows": migrated,
                "destination_checksums": checksums,
                "destination_counts": destination_counts,
                "source_counts": report.row_counts,
                "source_checksums": report.checksums,
                "source_row_hashes": report.source_row_hashes,
                "destination_row_hashes": {
                    table: tuple(hashes) for table, hashes in destination_hashes.items()
                },
                "unsupported_records": [],
                "skipped_records": [],
                "applied_records": [
                    f"{table}:{count}"
                    for table, count in destination_counts.items()
                    if count
                ],
                "source_fingerprint": source_fingerprint,
                "mapping_fingerprint": mapping_fingerprint,
                "reconciliation_result": "MATCHED",
            }
            cursor.execute(
                "UPDATE postgres_backfill_runs SET completed_at = %s, status = 'COMPLETED', report = %s::jsonb WHERE run_id = %s",
                (completed_at, json.dumps(saved_report, sort_keys=True), run_id),
            )
        return BackfillReport(
            False,
            report.row_counts,
            report.checksums,
            (),
            migrated,
            run_id,
            checksums,
            (),
            True,
            source_fingerprint,
            mapping_fingerprint,
            report.source_row_hashes,
            {table: tuple(hashes) for table, hashes in destination_hashes.items()},
            destination_counts,
            (),
            tuple(
                f"{table}:{count}"
                for table, count in destination_counts.items()
                if count
            ),
            None,
            completed_at,
        )
    except BackfillError:
        raise
    except Exception as error:
        raise BackfillError("backfill_transaction_failed") from error
    finally:
        source.close()
        if connection is not None:
            connection.close()
