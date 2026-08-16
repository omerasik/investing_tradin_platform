"""add append-only historical provider-ingestion checkpoints

Revision ID: 20260816_0013
Revises: 20260815_0012
Create Date: 2026-08-16
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260816_0013"
down_revision = "20260815_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE historical_ingestion_checkpoints (
            checkpoint_id UUID PRIMARY KEY,
            source_id UUID NOT NULL REFERENCES historical_data_sources(source_id),
            adapter_name TEXT NOT NULL,
            scope JSONB NOT NULL CHECK(jsonb_typeof(scope) = 'object'),
            state TEXT NOT NULL CHECK(state IN ('HEALTHY','STALE','ERROR')),
            provider_version TEXT,
            resume_cursor TEXT,
            captured_count INTEGER NOT NULL CHECK(captured_count >= 0),
            error_code TEXT,
            recorded_at TIMESTAMPTZ NOT NULL,
            CHECK((state = 'ERROR' AND error_code IS NOT NULL) OR
                  (state <> 'ERROR' AND error_code IS NULL))
        )"""
    )
    op.execute(
        "CREATE INDEX historical_ingestion_checkpoint_latest_idx ON "
        "historical_ingestion_checkpoints(source_id,adapter_name,recorded_at DESC)"
    )
    op.execute(immutable_trigger_sql("historical_ingestion_checkpoints"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS historical_ingestion_checkpoints_immutable ON historical_ingestion_checkpoints")
    op.execute("DROP TABLE IF EXISTS historical_ingestion_checkpoints")
