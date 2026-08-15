"""create normalized PostgreSQL production persistence schema

Revision ID: 20260813_0001
Revises:
Create Date: 2026-08-13
"""

from alembic import op

from trade_platform.postgres_schema import (
    IMMUTABLE_TABLES,
    INITIAL_SCHEMA_SQL,
    drop_schema_sql,
    immutable_trigger_sql,
)

revision = "20260813_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in INITIAL_SCHEMA_SQL:
        op.execute(statement)
    for table in IMMUTABLE_TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    # Never run against production: production migrations are forward-only.
    for statement in drop_schema_sql():
        op.execute(statement)
