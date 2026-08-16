"""make existing strategy authorities immutable for Trend V2

Revision ID: 20260816_0016
Revises: 20260816_0015
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260816_0016"
down_revision = "20260816_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("strategy_definitions", "strategy_versions"):
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    for table in ("strategy_versions", "strategy_definitions"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
