"""Append-only local audit storage for development and paper simulation."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from .domain import utc_now


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: UUID
    event_type: str
    occurred_at: datetime
    actor: str
    payload: dict[str, object]


class SQLiteAuditStore:
    """Development-only append-only audit table; no update/delete operations are exposed."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def append(self, event_type: str, actor: str, payload: dict[str, object]) -> AuditEvent:
        if not event_type.strip() or not actor.strip():
            raise ValueError("event_type and actor are required")
        event = AuditEvent(uuid4(), event_type, utc_now(), actor, payload)
        self._connection.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)",
            (str(event.event_id), event.event_type, event.occurred_at.isoformat(), event.actor, json.dumps(payload, sort_keys=True)),
        )
        self._connection.commit()
        return event

    def recent(self, limit: int = 100) -> list[AuditEvent]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be from 1 through 1000")
        rows = self._connection.execute(
            "SELECT event_id, event_type, occurred_at, actor, payload_json "
            "FROM audit_events ORDER BY occurred_at DESC, event_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            AuditEvent(UUID(row[0]), row[1], datetime.fromisoformat(row[2]), row[3], json.loads(row[4]))
            for row in rows
        ]

    def query(
        self,
        *,
        event_type: str | None = None,
        actor: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEvent], bool]:
        """Bounded, deterministically-ordered lookup (timestamp DESC, event ID DESC tie-breaker).

        Returns ``(items, has_more)``; ``has_more`` never requires loading unbounded rows.
        """
        if not 1 <= limit <= 200:
            raise ValueError("limit must be from 1 through 200")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        clauses: list[str] = []
        params: list[object] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if actor is not None:
            clauses.append("actor = ?")
            params.append(actor)
        if start is not None:
            clauses.append("occurred_at >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("occurred_at <= ?")
            params.append(end.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            # `where` is assembled only from the fixed literal clause strings above,
            # never from caller-supplied identifiers; every value is bound via `?`.
            "SELECT event_id, event_type, occurred_at, actor, payload_json "
            f"FROM audit_events {where} ORDER BY occurred_at DESC, event_id DESC LIMIT ? OFFSET ?",  # nosec B608
            (*params, limit + 1, offset),
        ).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = [
            AuditEvent(UUID(row[0]), row[1], datetime.fromisoformat(row[2]), row[3], json.loads(row[4]))
            for row in selected
        ]
        return items, has_more

    def get(self, event_id: UUID) -> AuditEvent | None:
        row = self._connection.execute(
            "SELECT event_id, event_type, occurred_at, actor, payload_json "
            "FROM audit_events WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        if row is None:
            return None
        return AuditEvent(UUID(row[0]), row[1], datetime.fromisoformat(row[2]), row[3], json.loads(row[4]))
