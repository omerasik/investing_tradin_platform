"""add durable risk-violation alert transitions

Revision ID: 20260819_0025
Revises: 20260819_0024
Create Date: 2026-08-19
"""

from alembic import op

revision = "20260819_0025"
down_revision = "20260819_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE operational_alert_events ("
        "event_id UUID PRIMARY KEY, "
        "alert_id UUID NOT NULL REFERENCES operational_alerts(alert_id), "
        "status TEXT NOT NULL CHECK (status IN ('OPEN','ACKNOWLEDGED','RESOLVED')), "
        "actor TEXT NOT NULL CHECK (btrim(actor) <> ''), "
        "occurred_at TIMESTAMPTZ NOT NULL, "
        "details JSONB NOT NULL CHECK (jsonb_typeof(details) = 'object'))"
    )
    op.execute(
        "CREATE INDEX operational_alert_events_alert_time_idx "
        "ON operational_alert_events(alert_id, occurred_at, event_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX operational_alerts_active_fingerprint_idx "
        "ON operational_alerts ((payload->>'fingerprint')) "
        "WHERE status <> 'RESOLVED' AND payload ? 'fingerprint'"
    )
    op.execute(
        "ALTER TABLE operational_alerts ADD CONSTRAINT "
        "operational_alerts_pretrade_risk_shape CHECK ("
        "alert_type <> 'PRETRADE_RISK_REJECTED' OR ("
        "severity IN ('WARNING','CRITICAL') AND "
        "status IN ('OPEN','ACKNOWLEDGED','RESOLVED') AND "
        "jsonb_typeof(payload) = 'object' AND "
        "payload ?& ARRAY['fingerprint','source','code','resource','details'] AND "
        "btrim(payload->>'fingerprint') <> '' AND "
        "btrim(payload->>'source') <> '' AND "
        "payload->>'code' = 'PRETRADE_RISK_REJECTED' AND "
        "btrim(payload->>'resource') <> '' AND "
        "jsonb_typeof(payload->'details') = 'object')) NOT VALID"
    )
    op.execute(
        "ALTER TABLE operational_alerts VALIDATE CONSTRAINT "
        "operational_alerts_pretrade_risk_shape"
    )
    op.execute(
        "CREATE TRIGGER operational_alert_events_immutable BEFORE UPDATE OR DELETE "
        "ON operational_alert_events FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE operational_alerts DROP CONSTRAINT IF EXISTS "
        "operational_alerts_pretrade_risk_shape"
    )
    op.execute("DROP INDEX IF EXISTS operational_alerts_active_fingerprint_idx")
    op.execute("DROP TABLE IF EXISTS operational_alert_events")
