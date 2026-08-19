"""validate immutable per-trade risk evidence payloads

Revision ID: 20260819_0024
Revises: 20260819_0023
Create Date: 2026-08-19
"""

from alembic import op

revision = "20260819_0024"
down_revision = "20260819_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE runtime_pretrade_assessments ADD CONSTRAINT "
        "runtime_pretrade_assessments_per_trade_risk_shape CHECK ("
        "payload->'per_trade_risk' IS NULL OR "
        "payload->'per_trade_risk' = 'null'::jsonb OR "
        "jsonb_typeof(payload->'per_trade_risk') = 'object') NOT VALID"
    )
    op.execute(
        "ALTER TABLE runtime_pretrade_assessments VALIDATE CONSTRAINT "
        "runtime_pretrade_assessments_per_trade_risk_shape"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE runtime_pretrade_assessments DROP CONSTRAINT IF EXISTS "
        "runtime_pretrade_assessments_per_trade_risk_shape"
    )
