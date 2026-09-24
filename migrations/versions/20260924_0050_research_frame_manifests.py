"""research frame manifest catalog (Phase R2B columnar data plane)

Revision ID: 20260924_0050
Revises: 20260924_0049
Create Date: 2026-09-24

Phase R2B. PostgreSQL stays the authority and catalog; the high-cardinality
analytical payload lives in content-addressed Parquet objects outside the
database (``research_data_plane_v1.py``). This table catalogs each immutable
frame manifest: its content identity (``logical_content_hash``), manifest
identity (``manifest_hash``), schema fingerprint, size, source dataset and,
for derived frames, the deterministic feature-frame cache key. Rows are
append-only (the shared immutability trigger); the payload itself is never
stored here.

Purely additive; downgrade drops the new table.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260924_0050"
down_revision = "20260924_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE research_frame_manifests (
        manifest_id UUID PRIMARY KEY,
        manifest_hash CHAR(64) NOT NULL UNIQUE CHECK(manifest_hash ~ '^[0-9a-f]{64}$'),
        schema_version TEXT NOT NULL CHECK(length(trim(schema_version))>0),
        frame_kind TEXT NOT NULL CHECK(frame_kind IN ('REFERENCE_PRICE','OHLCV','OPEN_INTEREST','FEATURE')),
        frame_schema_fingerprint CHAR(64) NOT NULL CHECK(frame_schema_fingerprint ~ '^[0-9a-f]{64}$'),
        logical_content_hash CHAR(64) NOT NULL CHECK(logical_content_hash ~ '^[0-9a-f]{64}$'),
        row_count BIGINT NOT NULL CHECK(row_count>=0),
        total_bytes BIGINT NOT NULL CHECK(total_bytes>=0),
        dataset_version_id UUID REFERENCES historical_dataset_versions(dataset_version_id),
        cache_key CHAR(64) CHECK(cache_key IS NULL OR cache_key ~ '^[0-9a-f]{64}$'),
        manifest JSONB NOT NULL CHECK(jsonb_typeof(manifest)='object'),
        registered_at TIMESTAMPTZ NOT NULL,
        CHECK((frame_kind='FEATURE' AND cache_key IS NOT NULL)
              OR (frame_kind<>'FEATURE' AND cache_key IS NULL)))"""
    )
    op.execute(
        "CREATE INDEX research_frame_manifests_dataset_idx "
        "ON research_frame_manifests(dataset_version_id, frame_kind)"
    )
    op.execute(
        "CREATE INDEX research_frame_manifests_cache_key_idx "
        "ON research_frame_manifests(cache_key) WHERE cache_key IS NOT NULL"
    )
    op.execute(immutable_trigger_sql("research_frame_manifests"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS research_frame_manifests_immutable ON research_frame_manifests")
    op.execute("DROP TABLE IF EXISTS research_frame_manifests")
