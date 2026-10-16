"""strategy lab purged walk-forward reports (Stage C.1.1)

Revision ID: 20261016_0063
Revises: 20261009_0062
Create Date: 2026-10-16

Stage C.1.1. ``strategy_lab_walk_forward_reports`` holds immutable,
content-addressed purged walk-forward reports over a registered study's
development window (``strategy_lab_walk_forward_v1``). By CHECK a report stays
development evidence: ``WALK_FORWARD_DEVELOPMENT``, ``SEARCH_NON_AUTHORITATIVE``,
``GROSS_NON_PROMOTABLE``, and its evaluation end never passes the untouched
holdout (2026-08-20T00:00Z). Admitting anything else is a future migration,
never a row value.

Purely additive; downgrade drops the table.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261016_0063"
down_revision = "20261009_0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE strategy_lab_walk_forward_reports (
        report_hash CHAR(64) PRIMARY KEY CHECK(report_hash ~ '^[0-9a-f]{64}$'),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        split_hash CHAR(64) NOT NULL CHECK(split_hash ~ '^[0-9a-f]{64}$'),
        fold_count INTEGER NOT NULL CHECK(fold_count>0),
        evaluation_class TEXT NOT NULL CHECK(evaluation_class='WALK_FORWARD_DEVELOPMENT'),
        numeric_tier TEXT NOT NULL CHECK(numeric_tier='SEARCH_NON_AUTHORITATIVE'),
        cost_mode TEXT NOT NULL CHECK(cost_mode='GROSS_NON_PROMOTABLE'),
        evaluation_end_exclusive TIMESTAMPTZ NOT NULL
            CHECK(evaluation_end_exclusive <= TIMESTAMPTZ '2026-08-20 00:00:00+00'),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute("CREATE INDEX strategy_lab_walk_forward_reports_study_idx ON strategy_lab_walk_forward_reports(study_id)")
    op.execute(immutable_trigger_sql("strategy_lab_walk_forward_reports"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS strategy_lab_walk_forward_reports")
