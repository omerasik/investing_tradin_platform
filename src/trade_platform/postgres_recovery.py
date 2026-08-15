"""Append-only recovery gate that blocks risk until restore reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from .persistence import PostgresDatabase


class PostgresRecoveryGate:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def require_reconciliation(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("recovery_reason_required")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO runtime_recovery_events "
                "(event_id, state, reason, occurred_at) VALUES (%s,'RECONCILIATION_REQUIRED',%s,%s)",
                (uuid4(), reason, datetime.now(UTC)),
            )

    def mark_reconciled(self, reason: str) -> None:
        if not reason.strip() or not self.blocks():
            raise ValueError("active_recovery_gate_required")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO runtime_recovery_events "
                "(event_id, state, reason, occurred_at) VALUES (%s,'RECONCILED',%s,%s)",
                (uuid4(), reason, datetime.now(UTC)),
            )

    def blocks(self) -> bool:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT state FROM runtime_recovery_events ORDER BY event_sequence DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return row is not None and str(row[0]) == "RECONCILIATION_REQUIRED"
