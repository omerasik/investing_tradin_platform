"""add external identity mapping and authorization evidence

Revision ID: 20260822_0029
Revises: 20260819_0028
Create Date: 2026-08-22
"""

from alembic import op

revision = "20260822_0029"
down_revision = "20260819_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE external_identity_mapping_policies ("
        "policy_id UUID PRIMARY KEY, "
        "policy_name TEXT NOT NULL CHECK (btrim(policy_name) <> ''), "
        "version TEXT NOT NULL CHECK (btrim(version) <> ''), "
        "issuer TEXT NOT NULL CHECK (issuer ~ '^https://[^/?#]+[^?#]*[^/?#]$'), "
        "audience TEXT NOT NULL CHECK (btrim(audience) <> ''), "
        "group_role_map JSONB NOT NULL CHECK (jsonb_typeof(group_role_map) = 'object' "
        "AND group_role_map <> '{}'::jsonb), "
        "required_authentication_methods TEXT[] NOT NULL CHECK "
        "(cardinality(required_authentication_methods) > 0), "
        "maximum_session_age_seconds INTEGER NOT NULL CHECK "
        "(maximum_session_age_seconds BETWEEN 60 AND 86400), "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by) <> ''), "
        "approved_at TIMESTAMPTZ NOT NULL, enabled BOOLEAN NOT NULL, "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "UNIQUE(policy_name, version))"
    )
    op.execute(
        "CREATE INDEX external_identity_mapping_policy_latest_idx ON "
        "external_identity_mapping_policies(policy_name, approved_at DESC, policy_id DESC)"
    )
    op.execute(
        "CREATE TABLE authorization_decisions ("
        "decision_id UUID PRIMARY KEY, occurred_at TIMESTAMPTZ NOT NULL, "
        "subject TEXT NOT NULL CHECK (btrim(subject) <> ''), "
        "role TEXT CHECK (role IN "
        "('viewer','researcher','data_steward','risk_reviewer','auditor','operator')), "
        "requested_permission TEXT NOT NULL CHECK (requested_permission IN "
        "('read_evidence','run_research','manage_data','review_risk',"
        "'acknowledge_alert','write_audit')), "
        "outcome TEXT NOT NULL CHECK (outcome IN ('ALLOW','DENY')), "
        "reason TEXT NOT NULL CHECK (btrim(reason) <> ''), "
        "authentication_method TEXT NOT NULL CHECK (btrim(authentication_method) <> ''), "
        "session_id_hash CHAR(64) CHECK "
        "(session_id_hash IS NULL OR session_id_hash ~ '^[0-9a-f]{64}$'), "
        "mapping_policy_id UUID REFERENCES external_identity_mapping_policies(policy_id), "
        "mapping_policy_version TEXT, "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'))"
    )
    op.execute(
        "CREATE INDEX authorization_decisions_subject_time_idx ON "
        "authorization_decisions(subject, occurred_at DESC, decision_id DESC)"
    )
    op.execute(
        "CREATE INDEX authorization_decisions_outcome_time_idx ON "
        "authorization_decisions(outcome, occurred_at DESC, decision_id DESC)"
    )
    for table in (
        "external_identity_mapping_policies",
        "authorization_decisions",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS authorization_decisions")
    op.execute("DROP TABLE IF EXISTS external_identity_mapping_policies")
