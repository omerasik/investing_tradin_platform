"""Persist exact consumed R8 live bars atomically with runner progress.
Revision ID: 20261020_0067
Revises: 20261017_0066
"""
from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = '20261020_0067'
down_revision = '20261017_0066'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE runner_consumed_live_bars (
        observation_id UUID PRIMARY KEY,
        content_hash CHAR(64) NOT NULL UNIQUE CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        runner_session_id UUID NOT NULL,
        progress_hash CHAR(64) NOT NULL,
        observation_index INTEGER NOT NULL CHECK(observation_index >= 0),
        feed_segment INTEGER NOT NULL CHECK(feed_segment >= 0),
        bar_open_micros BIGINT NOT NULL CHECK(bar_open_micros % 60000000 = 0),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        UNIQUE(progress_hash, observation_index),
        UNIQUE(runner_session_id, feed_segment, bar_open_micros),
        FOREIGN KEY(runner_session_id, progress_hash)
            REFERENCES runner_liveness_progress(runner_session_id, content_hash),
        CHECK(identity ?& ARRAY['schema_version','runner_session_id','progress_hash','index','live_bar','admitted','warmup']),
        CHECK(identity->>'schema_version' IS NOT NULL AND identity->>'schema_version' = 'runner-consumed-live-bar-v1'),
        CHECK(identity->>'runner_session_id' IS NOT NULL AND identity->>'runner_session_id' = runner_session_id::text),
        CHECK(identity->>'progress_hash' IS NOT NULL AND identity->>'progress_hash' = trim(progress_hash)),
        CHECK(identity->>'index' IS NOT NULL AND (identity->>'index')::integer = observation_index),
        CHECK(identity->'live_bar'->>'feed_segment' IS NOT NULL
              AND (identity->'live_bar'->>'feed_segment')::integer = feed_segment),
        CHECK(identity->'live_bar'->'bar'->>'bar_open_micros' IS NOT NULL
              AND (identity->'live_bar'->'bar'->>'bar_open_micros')::bigint = bar_open_micros),
        CHECK(identity->'live_bar'->>'knowledge_kind' IS NOT NULL AND identity->'live_bar'->>'knowledge_kind' = 'LIVE_PROVISIONAL_NO_LATER_SAMPLE_YET'))""")
    op.execute('CREATE INDEX runner_consumed_live_bars_session_idx ON runner_consumed_live_bars(runner_session_id)')
    op.execute(immutable_trigger_sql('runner_consumed_live_bars'))


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS runner_consumed_live_bars')
