"""add immutable PostgreSQL policy and pre-trade assessment evidence

Revision ID: 20260815_0004
Revises: 20260815_0003
Create Date: 2026-08-15
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260815_0004"
down_revision = "20260815_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE runtime_policy_documents ("
        "policy_type TEXT NOT NULL CHECK(policy_type IN ('risk','portfolio')), "
        "version TEXT NOT NULL, payload JSONB NOT NULL CHECK(jsonb_typeof(payload) = 'object'), "
        "content_hash CHAR(64) NOT NULL, approved_by TEXT NOT NULL, "
        "approved_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(policy_type, version), "
        "UNIQUE(content_hash))"
    )
    op.execute(
        "CREATE TABLE runtime_pretrade_assessments ("
        "assessment_id UUID PRIMARY KEY, intent_id UUID NOT NULL UNIQUE, "
        "assessment_digest CHAR(64) NOT NULL UNIQUE, assessment_mac CHAR(64) NOT NULL, "
        "assessed_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL "
        "CHECK(jsonb_typeof(payload) = 'object'))"
    )
    op.execute(immutable_trigger_sql("runtime_policy_documents"))
    op.execute(immutable_trigger_sql("runtime_pretrade_assessments"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS runtime_pretrade_assessments_immutable ON runtime_pretrade_assessments")
    op.execute("DROP TRIGGER IF EXISTS runtime_policy_documents_immutable ON runtime_policy_documents")
    op.execute("DROP TABLE IF EXISTS runtime_pretrade_assessments")
    op.execute("DROP TABLE IF EXISTS runtime_policy_documents")
