"""Durable local alerts and failure-drill evidence; delivery channels remain external adapters."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from .domain import utc_now
from .persistence import PersistenceError, PostgresDatabase
from .shadow_mode import FailureDrillResult


class AlertError(ValueError):
    pass


class AlertSeverity(str, Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True, slots=True)
class OperationalAlert:
    alert_id: UUID
    fingerprint: str
    source: str
    code: str
    severity: AlertSeverity
    resource: str
    details: dict[str, str]
    status: AlertStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OperationalAlertEvent:
    event_id: UUID
    alert_id: UUID
    status: AlertStatus
    actor: str
    occurred_at: datetime
    details: dict[str, str]


class PostgresOperationalAlertStore:
    """Concurrency-safe PostgreSQL alert authority with immutable transitions."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def raise_alert(
        self,
        *,
        source: str,
        code: str,
        severity: AlertSeverity,
        resource: str,
        details: dict[str, str],
        occurred_at: datetime | None = None,
    ) -> OperationalAlert:
        try:
            with self._database.transaction() as connection:
                return self.raise_alert_in_transaction(
                    connection,
                    source=source,
                    code=code,
                    severity=severity,
                    resource=resource,
                    details=details,
                    occurred_at=occurred_at,
                )
        except (AlertError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("operational_alert_persistence_uncertain") from error

    def raise_alert_in_transaction(
        self,
        connection: Any,
        *,
        source: str,
        code: str,
        severity: AlertSeverity,
        resource: str,
        details: dict[str, str],
        occurred_at: datetime | None = None,
    ) -> OperationalAlert:
        """Open an alert using the caller's transaction for atomic producers."""
        _validate_alert_fields(source, code, resource, details)
        when = occurred_at or utc_now()
        if when.tzinfo is None or when.utcoffset() is None:
            raise AlertError("alert_time_must_be_timezone_aware")
        fingerprint = f"{source}:{code}:{resource}"
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"operational-alert:{fingerprint}",),
            )
            cursor.execute(
                "SELECT alert_id, payload->>'fingerprint', payload->>'source', "
                "payload->>'code', severity, payload->>'resource', payload->'details', "
                "status, opened_at, COALESCE((SELECT MAX(occurred_at) FROM "
                "operational_alert_events WHERE alert_id=operational_alerts.alert_id), opened_at) "
                "FROM operational_alerts WHERE payload->>'fingerprint'=%s "
                "AND status <> 'RESOLVED'",
                (fingerprint,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                return self._from_row(existing)
            alert_id = uuid4()
            payload = {
                "fingerprint": fingerprint,
                "source": source,
                "code": code,
                "resource": resource,
                "details": details,
            }
            cursor.execute(
                "INSERT INTO operational_alerts "
                "(alert_id, alert_type, severity, status, opened_at, acknowledged_at, payload) "
                "VALUES (%s,%s,%s,'OPEN',%s,NULL,%s::jsonb)",
                (alert_id, code, severity.value, when, json.dumps(payload, sort_keys=True)),
            )
            cursor.execute(
                "INSERT INTO operational_alert_events "
                "(event_id, alert_id, status, actor, occurred_at, details) "
                "VALUES (%s,%s,'OPEN','system',%s,%s::jsonb)",
                (uuid4(), alert_id, when, json.dumps(details, sort_keys=True)),
            )
        return OperationalAlert(
            alert_id,
            fingerprint,
            source,
            code,
            severity,
            resource,
            dict(details),
            AlertStatus.OPEN,
            when,
            when,
        )

    def transition(
        self,
        alert_id: UUID,
        status: AlertStatus,
        *,
        actor: str,
        details: dict[str, str] | None = None,
    ) -> OperationalAlert:
        try:
            with self._database.transaction() as connection:
                self.transition_in_transaction(
                    connection,
                    alert_id,
                    status,
                    actor=actor,
                    details=details,
                )
            return self.get(alert_id)
        except (AlertError, KeyError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("operational_alert_transition_uncertain") from error

    def transition_in_transaction(
        self,
        connection: Any,
        alert_id: UUID,
        status: AlertStatus,
        *,
        actor: str,
        details: dict[str, str] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Append a transition using an owning producer's transaction."""
        if not actor.strip() or status is AlertStatus.OPEN:
            raise AlertError("invalid_alert_transition")
        event_details = details or {}
        _validate_alert_details(event_details)
        when = occurred_at or utc_now()
        if when.tzinfo is None or when.utcoffset() is None:
            raise AlertError("alert_time_must_be_timezone_aware")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, opened_at, COALESCE((SELECT MAX(occurred_at) FROM "
                "operational_alert_events WHERE alert_id=operational_alerts.alert_id), "
                "opened_at) FROM operational_alerts WHERE alert_id=%s FOR UPDATE",
                (alert_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(str(alert_id))
            if AlertStatus(str(row[0])) is AlertStatus.RESOLVED:
                raise AlertError("invalid_alert_transition")
            if when < row[1] or when < row[2]:
                raise AlertError("alert_transition_time_regression")
            cursor.execute(
                "UPDATE operational_alerts SET status=%s, "
                "acknowledged_at=CASE WHEN %s='ACKNOWLEDGED' THEN %s "
                "ELSE acknowledged_at END WHERE alert_id=%s",
                (status.value, status.value, when, alert_id),
            )
            cursor.execute(
                "INSERT INTO operational_alert_events "
                "(event_id, alert_id, status, actor, occurred_at, details) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                (
                    uuid4(),
                    alert_id,
                    status.value,
                    actor,
                    when,
                    json.dumps(event_details, sort_keys=True),
                ),
            )

    def get(self, alert_id: UUID) -> OperationalAlert:
        rows = self._read(
            "WHERE operational_alerts.alert_id=%s",
            (alert_id,),
        )
        if not rows:
            raise KeyError(str(alert_id))
        return rows[0]

    def active(self) -> tuple[OperationalAlert, ...]:
        return tuple(self._read("WHERE status <> 'RESOLVED' ORDER BY opened_at", ()))

    def events(self, alert_id: UUID) -> tuple[OperationalAlertEvent, ...]:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT event_id, alert_id, status, actor, occurred_at, details "
                    "FROM operational_alert_events WHERE alert_id=%s "
                    "ORDER BY occurred_at, event_id",
                    (alert_id,),
                )
                return tuple(
                    OperationalAlertEvent(
                        UUID(str(row[0])),
                        UUID(str(row[1])),
                        AlertStatus(str(row[2])),
                        str(row[3]),
                        row[4],
                        dict(row[5]),
                    )
                    for row in cursor.fetchall()
                )
        except PersistenceError:
            raise
        except Exception as error:
            raise PersistenceError("operational_alert_read_uncertain") from error

    def _read(self, clause: str, parameters: tuple[object, ...]) -> list[OperationalAlert]:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT alert_id, payload->>'fingerprint', payload->>'source', "
                    "payload->>'code', severity, payload->>'resource', payload->'details', "
                    "status, opened_at, COALESCE((SELECT MAX(occurred_at) FROM "
                    "operational_alert_events WHERE alert_id=operational_alerts.alert_id), opened_at) "
                    f"FROM operational_alerts {clause}",  # nosec B608: private fixed clauses
                    parameters,
                )
                return [self._from_row(row) for row in cursor.fetchall()]
        except PersistenceError:
            raise
        except Exception as error:
            raise PersistenceError("operational_alert_read_uncertain") from error

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> OperationalAlert:
        return OperationalAlert(
            UUID(str(row[0])),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            AlertSeverity(str(row[4])),
            str(row[5]),
            dict(cast(dict[str, str], row[6])),
            AlertStatus(str(row[7])),
            cast(datetime, row[8]),
            cast(datetime, row[9]),
        )


def _validate_alert_details(details: dict[str, str]) -> None:
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in details.items()):
        raise AlertError("alert_details_must_be_strings")


def _validate_alert_fields(
    source: str, code: str, resource: str, details: dict[str, str]
) -> None:
    if not source.strip() or not code.strip() or not resource.strip():
        raise AlertError("alert_requires_source_code_resource")
    _validate_alert_details(details)


class SQLiteOperationalAlertStore:
    """Deduplicates active conditions while preserving append-only status transitions."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS operational_alerts (
                alert_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, source TEXT NOT NULL, code TEXT NOT NULL,
                severity TEXT NOT NULL, resource TEXT NOT NULL, details_json TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS active_alert_fingerprint ON operational_alerts(fingerprint) WHERE status != 'RESOLVED';
            CREATE TABLE IF NOT EXISTS operational_alert_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, alert_id TEXT NOT NULL,
                status TEXT NOT NULL, actor TEXT NOT NULL, occurred_at TEXT NOT NULL, details_json TEXT NOT NULL);
        """)
        self._connection.commit()

    def raise_alert(self, *, source: str, code: str, severity: AlertSeverity, resource: str, details: dict[str, str]) -> OperationalAlert:
        if not source.strip() or not code.strip() or not resource.strip():
            raise AlertError("alert_requires_source_code_resource")
        fingerprint = f"{source}:{code}:{resource}"
        row = self._connection.execute("SELECT * FROM operational_alerts WHERE fingerprint = ? AND status != 'RESOLVED'", (fingerprint,)).fetchone()
        if row is not None:
            return self._from_row(row)
        now = utc_now(); alert = OperationalAlert(uuid4(), fingerprint, source, code, severity, resource, details, AlertStatus.OPEN, now, now)
        self._connection.execute("INSERT INTO operational_alerts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(alert.alert_id), alert.fingerprint, source, code, severity.value, resource, json.dumps(details, sort_keys=True), alert.status.value, now.isoformat(), now.isoformat()))
        self._append_event(alert.alert_id, AlertStatus.OPEN, "system", details)
        self._connection.commit()
        return alert

    def transition(self, alert_id: UUID, status: AlertStatus, *, actor: str, details: dict[str, str] | None = None) -> OperationalAlert:
        alert = self.get(alert_id)
        if not actor.strip() or status is AlertStatus.OPEN or (alert.status is AlertStatus.RESOLVED):
            raise AlertError("invalid_alert_transition")
        now = utc_now()
        self._connection.execute("UPDATE operational_alerts SET status = ?, updated_at = ? WHERE alert_id = ?", (status.value, now.isoformat(), str(alert_id)))
        self._append_event(alert_id, status, actor, details or {})
        self._connection.commit()
        return self.get(alert_id)

    def get(self, alert_id: UUID) -> OperationalAlert:
        row = self._connection.execute("SELECT * FROM operational_alerts WHERE alert_id = ?", (str(alert_id),)).fetchone()
        if row is None:
            raise KeyError(str(alert_id))
        return self._from_row(row)

    def active(self) -> tuple[OperationalAlert, ...]:
        rows = self._connection.execute("SELECT * FROM operational_alerts WHERE status != 'RESOLVED' ORDER BY created_at").fetchall()
        return tuple(self._from_row(row) for row in rows)

    def _append_event(self, alert_id: UUID, status: AlertStatus, actor: str, details: dict[str, str]) -> None:
        self._connection.execute("INSERT INTO operational_alert_events (event_id, alert_id, status, actor, occurred_at, details_json) VALUES (?, ?, ?, ?, ?, ?)", (str(uuid4()), str(alert_id), status.value, actor, utc_now().isoformat(), json.dumps(details, sort_keys=True)))

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> OperationalAlert:
        return OperationalAlert(UUID(str(row[0])), str(row[1]), str(row[2]), str(row[3]), AlertSeverity(str(row[4])), str(row[5]), json.loads(str(row[6])), AlertStatus(str(row[7])), datetime.fromisoformat(str(row[8])), datetime.fromisoformat(str(row[9])))

    def close(self) -> None:
        self._connection.close()


class SQLiteFailureDrillStore:
    """Append-only record of observed safeguards, including failed drills."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("CREATE TABLE IF NOT EXISTS failure_drills (drill_id TEXT PRIMARY KEY, scenario TEXT NOT NULL, expected_protection TEXT NOT NULL, observed_protection TEXT NOT NULL, passed INTEGER NOT NULL, recorded_at TEXT NOT NULL)")
        self._connection.commit()

    def append(self, result: FailureDrillResult) -> None:
        self._connection.execute("INSERT INTO failure_drills VALUES (?, ?, ?, ?, ?, ?)", (str(result.drill_id), result.scenario, result.expected_protection, result.observed_protection, int(result.passed), result.recorded_at.isoformat()))
        self._connection.commit()

    def get(self, drill_id: UUID) -> FailureDrillResult:
        row = self._connection.execute("SELECT * FROM failure_drills WHERE drill_id = ?", (str(drill_id),)).fetchone()
        if row is None:
            raise KeyError(str(drill_id))
        return FailureDrillResult(UUID(row[0]), row[1], row[2], row[3], bool(row[4]), datetime.fromisoformat(row[5]))

    def close(self) -> None:
        self._connection.close()
