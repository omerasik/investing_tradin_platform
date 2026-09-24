"""free public-archive (T2 event-time) dataset catalog (Phase R3B)

Revision ID: 20260924_0052
Revises: 20260924_0051
Create Date: 2026-09-24

Phase R3B. Catalogs a deterministic reconstruction over verified files of
Bybit's free public trade archive: identity (``dataset_version_id`` = uuid5 of
``content_hash``), the full identity payload (source contract hash, per-file
SHA-256, frame logical hashes, declared day gaps) and the frame manifest hashes
on the R2B plane. Trades and bars stay in Parquet.

``publication_lag_slot`` is recorded and, by CHECK, can only be the unset
marker: a T2 publication lag is owner decision OR-5, and admitting one is a
future migration, never a row value.

Purely additive and append-only; downgrade drops the table.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260924_0052"
down_revision = "20260924_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE public_archive_datasets (
        dataset_version_id UUID PRIMARY KEY,
        content_hash CHAR(64) NOT NULL UNIQUE CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        source_id UUID NOT NULL,
        symbol TEXT NOT NULL CHECK(length(trim(symbol))>0),
        first_utc_day DATE NOT NULL,
        last_utc_day DATE NOT NULL,
        file_count INTEGER NOT NULL CHECK(file_count>0),
        trade_count BIGINT NOT NULL CHECK(trade_count>=0),
        publication_lag_slot TEXT NOT NULL
            CHECK(publication_lag_slot='UNSET_PENDING_OWNER_DECISION_OR_5'),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        frame_manifests JSONB NOT NULL CHECK(jsonb_typeof(frame_manifests)='object'),
        registered_at TIMESTAMPTZ NOT NULL,
        CHECK(last_utc_day>=first_utc_day))"""
    )
    op.execute(
        "CREATE INDEX public_archive_datasets_symbol_idx "
        "ON public_archive_datasets(symbol, first_utc_day)"
    )
    op.execute(immutable_trigger_sql("public_archive_datasets"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS public_archive_datasets_immutable ON public_archive_datasets")
    op.execute("DROP TABLE IF EXISTS public_archive_datasets")
