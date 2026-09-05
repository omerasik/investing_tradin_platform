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
    op.execute(
        "CREATE TABLE audit_events ("
        "event_id UUID PRIMARY KEY, "
        "event_type TEXT NOT NULL CHECK (btrim(event_type) <> ''), "
        "occurred_at TIMESTAMPTZ NOT NULL, "
        "actor TEXT NOT NULL CHECK (btrim(actor) <> ''), "
        "payload JSONB NOT NULL, "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'))"
    )
    op.execute(
        "CREATE INDEX audit_events_time_idx ON audit_events(occurred_at DESC, event_id DESC)"
    )
    op.execute(
        "CREATE INDEX audit_events_type_time_idx ON "
        "audit_events(event_type, occurred_at DESC, event_id DESC)"
    )
    op.execute(
        "CREATE INDEX audit_events_actor_time_idx ON "
        "audit_events(actor, occurred_at DESC, event_id DESC)"
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
    for table in ("audit_events", "operator_session_events"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operator_session_events")
    op.execute("DROP TABLE IF EXISTS audit_events")
