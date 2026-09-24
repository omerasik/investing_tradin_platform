"""first-party T4 segment dataset catalog (Phase R3A)

Revision ID: 20260924_0051
Revises: 20260924_0050
Create Date: 2026-09-24

Phase R3A. A sealed first-party capture segment is catalogued here: its
deterministic identity (``dataset_version_id`` = uuid5 of ``content_hash``),
the full sealed identity payload (partitions, window, segment bounds, bound
clock samples, frame logical hashes, counters, timing facts), the frame
manifest hashes on the R2B data plane, and the host-clock ``sealed_at`` (audit
only; it is not part of the identity). The high-cardinality observations stay
in content-addressed Parquet; none of them is copied into PostgreSQL.

Rows are append-only (the shared immutability trigger). Verdicts are never
stored as authority: provenance and the evidence tier are re-derived from a seal
rebuilt from raw capture.

Purely additive; downgrade drops the new table.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260924_0051"
down_revision = "20260924_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE first_party_t4_datasets (
        dataset_version_id UUID PRIMARY KEY,
        content_hash CHAR(64) NOT NULL UNIQUE CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        schema_version TEXT NOT NULL CHECK(length(trim(schema_version))>0),
        source_id UUID NOT NULL,
        session_id UUID NOT NULL,
        utc_day DATE NOT NULL,
        window_index INTEGER NOT NULL CHECK(window_index>=0),
        start_arrival_nanos BIGINT NOT NULL,
        end_arrival_nanos_inclusive BIGINT NOT NULL,
        first_market_knowledge_at TIMESTAMPTZ NOT NULL,
        last_market_knowledge_at TIMESTAMPTZ NOT NULL,
        observation_count BIGINT NOT NULL CHECK(observation_count>0),
        distinct_knowledge_time_count BIGINT NOT NULL CHECK(distinct_knowledge_time_count>0),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        frame_manifests JSONB NOT NULL CHECK(jsonb_typeof(frame_manifests)='object'),
        sealed_at TIMESTAMPTZ NOT NULL,
        registered_at TIMESTAMPTZ NOT NULL,
        CHECK(end_arrival_nanos_inclusive>=start_arrival_nanos),
        CHECK(last_market_knowledge_at>=first_market_knowledge_at),
        CHECK(distinct_knowledge_time_count<=observation_count))"""
    )
    # One dataset per (window, segment start): only *final* segments are sealed,
    # so the same raw evidence can never be catalogued as two overlapping datasets.
    op.execute(
        "CREATE UNIQUE INDEX first_party_t4_datasets_segment_key "
        "ON first_party_t4_datasets(session_id, utc_day, window_index, start_arrival_nanos)"
    )
    op.execute(immutable_trigger_sql("first_party_t4_datasets"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS first_party_t4_datasets_immutable ON first_party_t4_datasets")
    op.execute("DROP TABLE IF EXISTS first_party_t4_datasets")
