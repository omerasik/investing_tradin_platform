"""Account contexts and owner-approved account policy versions (Phase R9)

Revision ID: 20261008_0059
Revises: 20261008_0058
Create Date: 2026-10-08

* ``account_contexts`` -- one paper account (PERSONAL_PAPER or PROP_PAPER).
* ``account_policy_versions`` -- append-only, content-addressed versions of
  every economic value an account's risk and sizing use. ``status`` is ACTIVE
  only for a complete, owner-approved version; there is no default value
  anywhere (owner decision OR-11 supplies them).

Paper only: no broker, credential or real-money column exists. Additive;
downgrade drops both tables.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261008_0059"
down_revision = "20261008_0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE account_contexts (
        account_id TEXT PRIMARY KEY CHECK(account_id ~ '^[a-z][a-z0-9_-]{2,63}$'),
        kind TEXT NOT NULL CHECK(kind IN ('PERSONAL_PAPER','PROP_PAPER')),
        display_name TEXT NOT NULL CHECK(length(trim(display_name))>0),
        base_currency TEXT NOT NULL CHECK(base_currency ~ '^[A-Z]{3,5}$'),
        created_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute(immutable_trigger_sql("account_contexts"))
    op.execute(
        """CREATE TABLE account_policy_versions (
        policy_version_id UUID PRIMARY KEY,
        content_hash CHAR(64) NOT NULL UNIQUE CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        account_id TEXT NOT NULL REFERENCES account_contexts(account_id),
        status TEXT NOT NULL CHECK(status IN ('ACTIVE','UNCONFIGURED')),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute("CREATE INDEX account_policy_versions_account_idx ON account_policy_versions(account_id, recorded_at)")
    op.execute(immutable_trigger_sql("account_policy_versions"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS account_policy_versions")
    op.execute("DROP TABLE IF EXISTS account_contexts")
