"""add immutable observability and SRE evidence authorities

Revision ID: 20260816_0021
Revises: 20260816_0020
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260816_0021"
down_revision = "20260816_0020"
branch_labels = None
depends_on = None

TABLES = (
    "sre_service_versions",
    "sre_slo_policy_versions",
    "sre_telemetry_events",
    "sre_dependency_probes",
    "sre_sli_windows",
    "sre_alert_policy_versions",
    "sre_alerts",
    "sre_alert_events",
    "sre_incidents",
    "sre_failure_drill_runs",
)


def upgrade() -> None:
    statements = (
        """CREATE TABLE sre_service_versions (
            service_version_id UUID PRIMARY KEY, service_id UUID NOT NULL,
            service_name TEXT NOT NULL, version TEXT NOT NULL, build_sha CHAR(40) NOT NULL,
            environment TEXT NOT NULL CHECK(environment IN ('LOCAL','CI','STAGING_CANDIDATE')),
            deployment_status TEXT NOT NULL CHECK(deployment_status='EVIDENCE_ONLY'),
            dependencies JSONB NOT NULL CHECK(jsonb_typeof(dependencies)='array'),
            created_at TIMESTAMPTZ NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(service_id,version,environment)
        )""",
        """CREATE TABLE sre_slo_policy_versions (
            slo_policy_version_id UUID PRIMARY KEY,
            service_version_id UUID NOT NULL REFERENCES sre_service_versions(service_version_id),
            name TEXT NOT NULL, indicator TEXT NOT NULL,
            objective NUMERIC(18,12) NOT NULL CHECK(objective BETWEEN 0 AND 1),
            window_seconds BIGINT NOT NULL CHECK(window_seconds>0),
            minimum_baseline_windows INTEGER NOT NULL CHECK(minimum_baseline_windows>0),
            status TEXT NOT NULL CHECK(status='CANDIDATE_ONLY'),
            approved BOOLEAN NOT NULL DEFAULT FALSE CHECK(approved=FALSE),
            created_at TIMESTAMPTZ NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(service_version_id,name)
        )""",
        """CREATE TABLE sre_telemetry_events (
            telemetry_event_id UUID PRIMARY KEY,
            service_version_id UUID NOT NULL REFERENCES sre_service_versions(service_version_id),
            trace_id CHAR(32) NOT NULL, span_id CHAR(16) NOT NULL,
            parent_span_id CHAR(16), event_kind TEXT NOT NULL CHECK(event_kind IN ('LOG','SPAN','METRIC')),
            name TEXT NOT NULL, level TEXT NOT NULL CHECK(level IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')),
            occurred_at TIMESTAMPTZ NOT NULL, duration_ms NUMERIC(18,6),
            attributes JSONB NOT NULL CHECK(jsonb_typeof(attributes)='object'),
            content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        "CREATE INDEX sre_telemetry_trace_idx ON sre_telemetry_events(trace_id,occurred_at)",
        """CREATE TABLE sre_dependency_probes (
            probe_id UUID PRIMARY KEY,
            service_version_id UUID NOT NULL REFERENCES sre_service_versions(service_version_id),
            dependency TEXT NOT NULL, probe_type TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('HEALTHY','DEGRADED','UNAVAILABLE')),
            checked_at TIMESTAMPTZ NOT NULL, latency_ms NUMERIC(18,6),
            reason TEXT, trace_id CHAR(32) NOT NULL,
            evidence JSONB NOT NULL CHECK(jsonb_typeof(evidence)='object'),
            content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        """CREATE TABLE sre_sli_windows (
            sli_window_id UUID PRIMARY KEY,
            slo_policy_version_id UUID NOT NULL REFERENCES sre_slo_policy_versions(slo_policy_version_id),
            window_start TIMESTAMPTZ NOT NULL, window_end TIMESTAMPTZ NOT NULL,
            good_events BIGINT NOT NULL CHECK(good_events>=0), total_events BIGINT NOT NULL CHECK(total_events>=0),
            measured_ratio NUMERIC(18,12), baseline_window_count INTEGER NOT NULL CHECK(baseline_window_count>=0),
            evidence_state TEXT NOT NULL CHECK(evidence_state IN ('INSUFFICIENT_BASELINE','MEASURED')),
            claim_status TEXT NOT NULL CHECK(claim_status='ENGINEERING_EVIDENCE_ONLY'),
            content_hash CHAR(64) NOT NULL UNIQUE,
            CHECK(window_end>window_start), CHECK(good_events<=total_events),
            CHECK((total_events=0 AND measured_ratio IS NULL) OR (total_events>0 AND measured_ratio IS NOT NULL))
        )""",
        """CREATE TABLE sre_alert_policy_versions (
            alert_policy_version_id UUID PRIMARY KEY,
            service_version_id UUID NOT NULL REFERENCES sre_service_versions(service_version_id),
            code TEXT NOT NULL, severity TEXT NOT NULL CHECK(severity IN ('INFO','WARNING','CRITICAL')),
            owner TEXT NOT NULL, runbook_uri TEXT NOT NULL, escalation_policy TEXT NOT NULL,
            deduplication_window_seconds BIGINT NOT NULL CHECK(deduplication_window_seconds>0),
            condition JSONB NOT NULL CHECK(jsonb_typeof(condition)='object'),
            status TEXT NOT NULL CHECK(status='EVIDENCE_ONLY'),
            created_at TIMESTAMPTZ NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(service_version_id,code)
        )""",
        """CREATE TABLE sre_alerts (
            alert_id UUID PRIMARY KEY,
            alert_policy_version_id UUID NOT NULL REFERENCES sre_alert_policy_versions(alert_policy_version_id),
            fingerprint CHAR(64) NOT NULL UNIQUE, resource TEXT NOT NULL,
            opened_at TIMESTAMPTZ NOT NULL, evidence_uri TEXT NOT NULL,
            trace_id CHAR(32) NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        """CREATE TABLE sre_alert_events (
            alert_event_id UUID PRIMARY KEY, alert_id UUID NOT NULL REFERENCES sre_alerts(alert_id),
            sequence INTEGER NOT NULL CHECK(sequence>=0),
            status TEXT NOT NULL CHECK(status IN ('OPEN','ACKNOWLEDGED','RESOLVED')),
            actor TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL,
            details JSONB NOT NULL CHECK(jsonb_typeof(details)='object'),
            content_hash CHAR(64) NOT NULL UNIQUE, UNIQUE(alert_id,sequence)
        )""",
        """CREATE TABLE sre_incidents (
            incident_id UUID PRIMARY KEY, alert_id UUID NOT NULL REFERENCES sre_alerts(alert_id),
            severity TEXT NOT NULL CHECK(severity IN ('WARNING','CRITICAL')),
            owner TEXT NOT NULL, runbook_uri TEXT NOT NULL,
            declared_at TIMESTAMPTZ NOT NULL, resolved_at TIMESTAMPTZ,
            post_incident_review_uri TEXT,
            status TEXT NOT NULL CHECK(status IN ('DECLARED','RESOLVED')),
            summary JSONB NOT NULL CHECK(jsonb_typeof(summary)='object'),
            content_hash CHAR(64) NOT NULL UNIQUE,
            CHECK(resolved_at IS NULL OR resolved_at>=declared_at),
            CHECK((status='DECLARED' AND resolved_at IS NULL) OR
                  (status='RESOLVED' AND resolved_at IS NOT NULL AND post_incident_review_uri IS NOT NULL))
        )""",
        """CREATE TABLE sre_failure_drill_runs (
            drill_run_id UUID PRIMARY KEY,
            service_version_id UUID NOT NULL REFERENCES sre_service_versions(service_version_id),
            scenario TEXT NOT NULL, injected_fault TEXT NOT NULL,
            expected_protection TEXT NOT NULL, observed_protection TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ NOT NULL,
            recovery_seconds NUMERIC(18,6) NOT NULL CHECK(recovery_seconds>=0),
            passed BOOLEAN NOT NULL, evidence_uri TEXT NOT NULL,
            environment TEXT NOT NULL CHECK(environment IN ('LOCAL','CI','STAGING_CANDIDATE')),
            content_hash CHAR(64) NOT NULL UNIQUE, CHECK(completed_at>=started_at)
        )""",
    )
    for statement in statements:
        op.execute(statement)
    for table in TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
