"""Owner research watch list versions (Phase R8.2, owner decision OR-9)

Revision ID: 20261009_0061
Revises: 20261009_0060
Create Date: 2026-10-09

* ``research_watchlist_versions`` -- append-only, content-addressed versions of
  the owner's explicit watch selection: (study, ESTABLISHED rerun, trial)
  entries. ``status`` is ACTIVE only for a non-empty, owner-approved version;
  nothing is selected by default.

Research only: a watched candidate's signals stay NOT_VALIDATED. Additive;
downgrade drops the table.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261009_0061"
down_revision = "20261009_0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE research_watchlist_versions (
        content_hash CHAR(64) PRIMARY KEY CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        watchlist_id TEXT NOT NULL CHECK(watchlist_id ~ '^[a-z][a-z0-9_-]{2,63}$'),
        status TEXT NOT NULL CHECK(status IN ('ACTIVE','DRAFT')),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        CHECK(status <> 'ACTIVE' OR (jsonb_array_length(identity->'entries') > 0
              AND identity->>'approved_by' IS NOT NULL AND identity->>'approved_on' IS NOT NULL)))"""
    )
    op.execute("CREATE INDEX research_watchlist_versions_idx ON research_watchlist_versions(watchlist_id, recorded_at)")
    op.execute(immutable_trigger_sql("research_watchlist_versions"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS research_watchlist_versions")
