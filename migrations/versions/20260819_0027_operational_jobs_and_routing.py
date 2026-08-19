"""add immutable operational job and local routing evidence

Revision ID: 20260819_0027
Revises: 20260819_0026
Create Date: 2026-08-19
"""

from alembic import op

revision = "20260819_0027"
down_revision = "20260819_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE operational_job_policy_versions ("
        "policy_id UUID PRIMARY KEY, job_name TEXT NOT NULL CHECK (btrim(job_name) <> ''), "
        "version TEXT NOT NULL CHECK (btrim(version) <> ''), "
        "interval_seconds BIGINT NOT NULL CHECK (interval_seconds > 0), "
        "grace_seconds BIGINT NOT NULL CHECK (grace_seconds >= 0), "
        "owner TEXT NOT NULL CHECK (btrim(owner) <> ''), "
        "runbook_uri TEXT NOT NULL CHECK (btrim(runbook_uri) <> ''), "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by) <> ''), "
        "approved_at TIMESTAMPTZ NOT NULL, enabled BOOLEAN NOT NULL, "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "UNIQUE(job_name, version), UNIQUE(job_name, approved_at))"
    )
    op.execute(
        "CREATE INDEX operational_job_policy_latest_idx ON "
        "operational_job_policy_versions(job_name, approved_at DESC, policy_id DESC)"
    )
    op.execute(
        "CREATE TABLE operational_job_runs ("
        "run_id UUID PRIMARY KEY, policy_id UUID NOT NULL REFERENCES "
        "operational_job_policy_versions(policy_id), "
        "idempotency_key TEXT NOT NULL UNIQUE CHECK (btrim(idempotency_key) <> ''), "
        "scheduled_for TIMESTAMPTZ NOT NULL, started_at TIMESTAMPTZ NOT NULL, "
        "completed_at TIMESTAMPTZ NOT NULL, "
        "status TEXT NOT NULL CHECK (status IN ('SUCCEEDED','FAILED','SKIPPED')), "
        "summary JSONB NOT NULL CHECK (jsonb_typeof(summary) = 'object'), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "CHECK (started_at >= scheduled_for), CHECK (completed_at >= started_at))"
    )
    op.execute(
        "CREATE INDEX operational_job_runs_policy_completed_idx ON "
        "operational_job_runs(policy_id, completed_at DESC, run_id DESC)"
    )
    op.execute(
        "CREATE TABLE operational_alert_route_policy_versions ("
        "route_policy_id UUID PRIMARY KEY, "
        "route_name TEXT NOT NULL CHECK (btrim(route_name) <> ''), "
        "version TEXT NOT NULL CHECK (btrim(version) <> ''), "
        "alert_code TEXT NOT NULL CHECK (btrim(alert_code) <> ''), "
        "minimum_severity TEXT NOT NULL CHECK (minimum_severity IN ('WARNING','CRITICAL')), "
        "channel TEXT NOT NULL CHECK (channel = 'LOCAL_OUTBOX'), "
        "destination_reference TEXT NOT NULL CHECK "
        "(destination_reference ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'), "
        "owner TEXT NOT NULL CHECK (btrim(owner) <> ''), "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by) <> ''), "
        "approved_at TIMESTAMPTZ NOT NULL, enabled BOOLEAN NOT NULL, "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "UNIQUE(route_name, version), UNIQUE(route_name, approved_at))"
    )
    op.execute(
        "CREATE INDEX operational_alert_route_policy_latest_idx ON "
        "operational_alert_route_policy_versions(route_name, approved_at DESC, route_policy_id DESC)"
    )
    op.execute(
        "CREATE TABLE operational_alert_delivery_outbox ("
        "delivery_id UUID PRIMARY KEY, alert_id UUID NOT NULL REFERENCES operational_alerts(alert_id), "
        "route_policy_id UUID NOT NULL REFERENCES operational_alert_route_policy_versions(route_policy_id), "
        "channel TEXT NOT NULL CHECK (channel = 'LOCAL_OUTBOX'), "
        "destination_reference TEXT NOT NULL CHECK (btrim(destination_reference) <> ''), "
        "status TEXT NOT NULL CHECK (status = 'PENDING_EXTERNAL_DELIVERY'), "
        "enqueued_at TIMESTAMPTZ NOT NULL, "
        "payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'), "
        "UNIQUE(alert_id, route_policy_id))"
    )
    op.execute(
        "CREATE INDEX operational_alert_delivery_outbox_pending_idx ON "
        "operational_alert_delivery_outbox(enqueued_at, delivery_id)"
    )
    for table in (
        "operational_job_policy_versions",
        "operational_job_runs",
        "operational_alert_route_policy_versions",
        "operational_alert_delivery_outbox",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operational_alert_delivery_outbox")
    op.execute("DROP TABLE IF EXISTS operational_alert_route_policy_versions")
    op.execute("DROP TABLE IF EXISTS operational_job_runs")
    op.execute("DROP TABLE IF EXISTS operational_job_policy_versions")
