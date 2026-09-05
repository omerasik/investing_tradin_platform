"""add durable postgres audit events and operator session revocation ledger

Revision ID: 20260906_0037
Revises: 20260830_0036
Create Date: 2026-09-06
"""

from alembic import op

revision = "20260906_0037"
down_revision = "20260830_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Named distinctly from the pre-existing ``audit_events`` table (see
    # migration 20260813_0001 / postgres_schema.py): that table is a one-time
    # legacy-migration backfill target with a different shape (audit_event_id,
    # entity_type, entity_id) -- not an application-facing audit authority. This
    # table is the durable store trade_platform.postgres_audit.PostgresAuditStore
    # actually reads and writes.
    op.execute(
        "CREATE TABLE production_audit_events ("
        "event_id UUID PRIMARY KEY, "
        "event_type TEXT NOT NULL CHECK (btrim(event_type) <> ''), "
        "occurred_at TIMESTAMPTZ NOT NULL, "
        "actor TEXT NOT NULL CHECK (btrim(actor) <> ''), "
        "payload JSONB NOT NULL, "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'))"
    )
    op.execute(
        "CREATE INDEX production_audit_events_time_idx ON "
        "production_audit_events(occurred_at DESC, event_id DESC)"
    )
    op.execute(
        "CREATE INDEX production_audit_events_type_time_idx ON "
        "production_audit_events(event_type, occurred_at DESC, event_id DESC)"
    )
    op.execute(
        "CREATE INDEX production_audit_events_actor_time_idx ON "
        "production_audit_events(actor, occurred_at DESC, event_id DESC)"
    )
    op.execute(
        "CREATE TABLE operator_session_events ("
        "event_id UUID PRIMARY KEY, "
        "session_id_hash CHAR(64) NOT NULL CHECK (session_id_hash ~ '^[0-9a-f]{64}$'), "
        "event_type TEXT NOT NULL CHECK (event_type IN ('issued','revoked')), "
        "occurred_at TIMESTAMPTZ NOT NULL, "
        "actor TEXT NOT NULL CHECK (btrim(actor) <> ''), "
        "reason TEXT NOT NULL CHECK (btrim(reason) <> ''), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'))"
    )
    op.execute(
        "CREATE INDEX operator_session_events_session_idx ON "
        "operator_session_events(session_id_hash, occurred_at DESC, event_id DESC)"
    )
    for table in ("production_audit_events", "operator_session_events"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operator_session_events")
    op.execute("DROP TABLE IF EXISTS production_audit_events")
