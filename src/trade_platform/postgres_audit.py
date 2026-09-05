"""Durable, immutable PostgreSQL-backed audit authority for protected/production runtimes.

``SQLiteAuditStore`` (:mod:`trade_platform.audit`) is explicitly scoped, by its own
docstring, to "development and paper simulation" -- see
``docs/PRODUCTION_READINESS_MATRIX.md``, which classifies it **BLOCKED** for
production because no PostgreSQL audit store exists at all. :class:`PostgresAuditStore`
is that store: append-only, content-hashed, database-trigger-enforced immutable (no
``UPDATE``/``DELETE`` is exposed here or possible at the schema level -- see migration
``20260906_0037``), and structurally interchangeable with ``SQLiteAuditStore`` via
:class:`trade_platform.audit.AuditStore` so ``build_app`` never needs to know which
backend it was handed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from .audit import AuditEvent
from .domain import utc_now
from .persistence import PersistenceError, PostgresDatabase

__all__ = ["PostgresAuditStore"]


class PostgresAuditStore:
    """Append-only PostgreSQL audit evidence; no update/delete method is exposed."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append(self, event_type: str, actor: str, payload: dict[str, object]) -> AuditEvent:
        if not event_type.strip() or not actor.strip():
            raise ValueError("event_type and actor are required")
        event = AuditEvent(uuid4(), event_type, utc_now(), actor, payload)
        content_hash = _event_hash(event)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO audit_events VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                    (
                        event.event_id,
                        event.event_type,
                        event.occurred_at,
                        event.actor,
                        json.dumps(payload, sort_keys=True),
                        content_hash,
                    ),
                )
        except PersistenceError:
            raise
        except Exception as error:
            raise PersistenceError("audit_event_persistence_uncertain") from error
        return event

    def recent(self, limit: int = 100) -> list[AuditEvent]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be from 1 through 1000")
        rows = self._select(
            "SELECT event_id,event_type,occurred_at,actor,payload FROM audit_events "
            "ORDER BY occurred_at DESC, event_id DESC LIMIT %s",
            (limit,),
        )
        return [_event_from_row(row) for row in rows]

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
        """Bounded, deterministically-ordered lookup mirroring ``SQLiteAuditStore.query``."""
        if not 1 <= limit <= 200:
            raise ValueError("limit must be from 1 through 200")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        clauses: list[str] = []
        params: list[object] = []
        if event_type is not None:
            clauses.append("event_type = %s")
            params.append(event_type)
        if actor is not None:
            clauses.append("actor = %s")
            params.append(actor)
        if start is not None:
            clauses.append("occurred_at >= %s")
            params.append(start)
        if end is not None:
            clauses.append("occurred_at <= %s")
            params.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._select(
            # `where` is assembled only from the fixed literal clause strings above,
            # never from caller-supplied identifiers; every value is bound via %s.
            "SELECT event_id,event_type,occurred_at,actor,payload FROM audit_events "
            f"{where} ORDER BY occurred_at DESC, event_id DESC LIMIT %s OFFSET %s",  # nosec B608
            (*params, limit + 1, offset),
        )
        has_more = len(rows) > limit
        selected = rows[:limit]
        return [_event_from_row(row) for row in selected], has_more

    def get(self, event_id: UUID) -> AuditEvent | None:
        rows = self._select(
            "SELECT event_id,event_type,occurred_at,actor,payload FROM audit_events "
            "WHERE event_id=%s",
            (event_id,),
        )
        return _event_from_row(rows[0]) if rows else None

    def _select(self, statement: str, parameters: tuple[object, ...]) -> list[tuple[Any, ...]]:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(statement, parameters)
                return cast(list[tuple[Any, ...]], cursor.fetchall())
        except PersistenceError:
            raise
        except Exception as error:
            raise PersistenceError("audit_event_read_uncertain") from error


def _event_hash(event: AuditEvent) -> str:
    payload = {
        "event_id": str(event.event_id),
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "actor": event.actor,
        "payload": event.payload,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _event_from_row(row: tuple[Any, ...]) -> AuditEvent:
    return AuditEvent(row[0], str(row[1]), row[2], str(row[3]), cast(dict[str, object], row[4]))
