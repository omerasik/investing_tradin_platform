"""add append-only PostgreSQL recovery reconciliation gate

Revision ID: 20260815_0007
Revises: 20260815_0006
Create Date: 2026-08-15
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260815_0007"
down_revision = "20260815_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE runtime_recovery_events ("
        "event_sequence BIGSERIAL PRIMARY KEY, event_id UUID NOT NULL UNIQUE, "
        "state TEXT NOT NULL CHECK(state IN ('RECONCILIATION_REQUIRED','RECONCILED')), "
        "reason TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL)"
    )
    op.execute(immutable_trigger_sql("runtime_recovery_events"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS runtime_recovery_events_immutable ON runtime_recovery_events")
    op.execute("DROP TABLE IF EXISTS runtime_recovery_events")
