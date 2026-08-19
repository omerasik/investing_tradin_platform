"""add immutable reasoned signal lifecycle operations

Revision ID: 20260819_0023
Revises: 20260819_0022
Create Date: 2026-08-19
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260819_0023"
down_revision = "20260819_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE runtime_signal_lifecycle_events (
        event_sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        event_id UUID NOT NULL UNIQUE,
        signal_id UUID NOT NULL REFERENCES runtime_signal_proposals(signal_id),
        from_status TEXT NOT NULL CHECK(from_status IN ('CANDIDATE','VALIDATED','BLOCKED_BY_RISK','BLOCKED_BY_DATA','WAITING_FOR_ENTRY','ACTIVE','PARTIALLY_FILLED','FILLED','INVALIDATED','EXPIRED','CLOSED','CANCELLED')),
        to_status TEXT NOT NULL CHECK(to_status IN ('CANDIDATE','VALIDATED','BLOCKED_BY_RISK','BLOCKED_BY_DATA','WAITING_FOR_ENTRY','ACTIVE','PARTIALLY_FILLED','FILLED','INVALIDATED','EXPIRED','CLOSED','CANCELLED')),
        actor TEXT NOT NULL CHECK(length(btrim(actor)) > 0),
        reason TEXT NOT NULL CHECK(length(btrim(reason)) > 0),
        evidence_references JSONB NOT NULL CHECK(jsonb_typeof(evidence_references) = 'array'),
        occurred_at TIMESTAMPTZ NOT NULL
        )"""
    )
    op.execute(
        "CREATE INDEX runtime_signal_lifecycle_asof_idx ON runtime_signal_lifecycle_events(signal_id,occurred_at DESC,event_sequence DESC)"
    )
    op.execute(immutable_trigger_sql("runtime_signal_lifecycle_events"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS runtime_signal_lifecycle_events")
