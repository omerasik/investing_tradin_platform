"""Durable local alerts and failure-drill evidence; delivery channels remain external adapters."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

from .domain import utc_now
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
