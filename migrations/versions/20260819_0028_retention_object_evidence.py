"""add immutable retention and object-manifest evidence

Revision ID: 20260819_0028
Revises: 20260819_0027
Create Date: 2026-08-19
"""

from alembic import op

revision = "20260819_0028"
down_revision = "20260819_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE retention_policy_versions ("
        "policy_id UUID PRIMARY KEY, "
        "policy_name TEXT NOT NULL CHECK (btrim(policy_name) <> ''), "
        "version TEXT NOT NULL CHECK (btrim(version) <> ''), "
        "classification TEXT NOT NULL CHECK (classification IN "
        "('BACKUP','RAW_DATA','MODEL_ARTIFACT','CONFIGURATION','AUDIT_EVIDENCE')), "
        "retention_seconds BIGINT NOT NULL CHECK (retention_seconds > 0), "
        "legal_hold BOOLEAN NOT NULL, owner TEXT NOT NULL CHECK (btrim(owner) <> ''), "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by) <> ''), "
        "approved_at TIMESTAMPTZ NOT NULL, enabled BOOLEAN NOT NULL, "
        "disposition_authority TEXT NOT NULL CHECK "
        "(disposition_authority = 'REVIEW_ONLY_NO_DELETE'), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "UNIQUE(policy_name, version), UNIQUE(policy_name, approved_at))"
    )
    op.execute(
        "CREATE INDEX retention_policy_latest_idx ON "
        "retention_policy_versions(policy_name, approved_at DESC, policy_id DESC)"
    )
    op.execute(
        "CREATE TABLE object_evidence_manifests ("
        "manifest_id UUID PRIMARY KEY, object_reference TEXT NOT NULL UNIQUE CHECK "
        "(object_reference ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' "
        "AND object_reference !~ '\\.\\.' AND object_reference !~ '//' "
        "AND object_reference !~ '/$'), "
        "object_kind TEXT NOT NULL CHECK (object_kind IN "
        "('DATABASE_BACKUP','RAW_PROVIDER_PAYLOAD','MODEL_ARTIFACT',"
        "'CONFIGURATION_SNAPSHOT','AUDIT_EXPORT')), "
        "media_type TEXT NOT NULL CHECK "
        "(media_type ~ '^[A-Za-z0-9][A-Za-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*$'), "
        "byte_size BIGINT NOT NULL CHECK (byte_size >= 0), "
        "sha256 CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'), "
        "source_reference TEXT NOT NULL CHECK "
        "(source_reference ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$' "
        "AND source_reference !~ '\\.\\.' AND source_reference !~ '//' "
        "AND source_reference !~ '/$'), "
        "policy_id UUID NOT NULL REFERENCES retention_policy_versions(policy_id), "
        "captured_at TIMESTAMPTZ NOT NULL, "
        "storage_state TEXT NOT NULL CHECK (storage_state = 'MANIFEST_ONLY'), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'))"
    )
    op.execute(
        "CREATE INDEX object_evidence_policy_captured_idx ON "
        "object_evidence_manifests(policy_id, captured_at, manifest_id)"
    )
    op.execute(
        "CREATE TABLE retention_evaluations ("
        "evaluation_id UUID PRIMARY KEY, "
        "manifest_id UUID NOT NULL REFERENCES object_evidence_manifests(manifest_id), "
        "policy_id UUID NOT NULL REFERENCES retention_policy_versions(policy_id), "
        "idempotency_key TEXT NOT NULL UNIQUE CHECK (btrim(idempotency_key) <> ''), "
        "evaluated_at TIMESTAMPTZ NOT NULL, retain_until TIMESTAMPTZ NOT NULL, "
        "disposition TEXT NOT NULL CHECK "
        "(disposition IN ('RETAIN','ELIGIBLE_FOR_REVIEW')), "
        "reason TEXT NOT NULL CHECK (reason IN "
        "('POLICY_DISABLED','LEGAL_HOLD','RETENTION_WINDOW_ACTIVE',"
        "'RETENTION_WINDOW_ELAPSED_REVIEW_REQUIRED')), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "CHECK (evaluated_at >= retain_until OR disposition = 'RETAIN'))"
    )
    op.execute(
        "CREATE INDEX retention_evaluations_manifest_time_idx ON "
        "retention_evaluations(manifest_id, evaluated_at DESC, evaluation_id DESC)"
    )
    for table in (
        "retention_policy_versions",
        "object_evidence_manifests",
        "retention_evaluations",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS retention_evaluations")
    op.execute("DROP TABLE IF EXISTS object_evidence_manifests")
    op.execute("DROP TABLE IF EXISTS retention_policy_versions")
