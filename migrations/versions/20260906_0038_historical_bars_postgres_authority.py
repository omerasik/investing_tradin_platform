"""add postgresql historical ohlcv bar authority (Module 3F)

Revision ID: 20260906_0038
Revises: 20260906_0037
Create Date: 2026-09-06

Named distinctly from the pre-existing ``historical_raw_observations`` /
``historical_dataset_versions`` family (see migration 20260815_0009 /
historical_market_data.py): that pipeline is the authorized, multi-source,
raw-to-sealed-dataset research pipeline for OHLCV/dividends/splits/etc. This
table instead backs ``trade_platform.market_data``'s narrower, provider-neutral
OHLCVBar authority (``SQLiteBarStore`` locally, ``PostgresHistoricalBarStore``
here) -- the bar cache consumed directly by ``ingest_from_provider`` and the
Data Health worker job, keyed by the plain string ``instrument_id`` used
throughout ``runtime_instruments`` rather than the UUID-keyed legacy
``instruments``/``market_bars`` tables from the initial schema baseline.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260906_0038"
down_revision = "20260906_0037"
branch_labels = None
depends_on = None

TABLES = ("historical_bars",)


def upgrade() -> None:
    op.execute(
        "CREATE TABLE historical_bars ("
        "instrument_id TEXT NOT NULL CHECK (btrim(instrument_id) <> ''), "
        "interval TEXT NOT NULL CHECK (btrim(interval) <> ''), "
        "event_at TIMESTAMPTZ NOT NULL, "
        "effective_at TIMESTAMPTZ NOT NULL, "
        "ingested_at TIMESTAMPTZ NOT NULL, "
        "open NUMERIC(30,12) NOT NULL CHECK (open > 0), "
        "high NUMERIC(30,12) NOT NULL CHECK (high > 0), "
        "low NUMERIC(30,12) NOT NULL CHECK (low > 0), "
        "close NUMERIC(30,12) NOT NULL CHECK (close > 0), "
        "volume NUMERIC(30,12) NOT NULL CHECK (volume >= 0), "
        "provider TEXT NOT NULL CHECK (btrim(provider) <> ''), "
        "source_identifier TEXT NOT NULL CHECK (btrim(source_identifier) <> ''), "
        "original_timezone TEXT NOT NULL CHECK (btrim(original_timezone) <> ''), "
        "revision INTEGER NOT NULL CHECK (revision >= 0), "
        "data_version TEXT NOT NULL, "
        "quality_score NUMERIC(8,6) NOT NULL CHECK (quality_score BETWEEN 0 AND 1), "
        "processing_status TEXT NOT NULL CHECK (processing_status IN ('RAW','VALIDATED','REJECTED')), "
        "content_hash CHAR(64) NOT NULL, "
        "PRIMARY KEY (instrument_id, interval, event_at, revision), "
        "CHECK (high >= low AND high >= open AND high >= close AND low <= open AND low <= close), "
        "CHECK (effective_at >= event_at AND ingested_at >= event_at))"
    )
    op.execute(
        "CREATE INDEX historical_bars_as_of_idx ON "
        "historical_bars(instrument_id, interval, effective_at, ingested_at)"
    )
    op.execute(
        "CREATE INDEX historical_bars_series_idx ON "
        "historical_bars(instrument_id, interval, event_at)"
    )
    for table in TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
