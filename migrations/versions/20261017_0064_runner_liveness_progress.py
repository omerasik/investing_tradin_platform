"""Runner liveness progress records (R10 incubation evidence)

Revision ID: 20261017_0064
Revises: 20261016_0063
Create Date: 2026-10-17

``runner_liveness_progress`` -- the append-only, hash-chained progress of each
research/paper runner session (``runner_liveness_v1``). One row per
``runner_session_id:sequence`` (the idempotency key), content-addressed. By
CHECK, sequence 0 is the only ``STARTED`` row and the only row without a
previous hash and with the binding columns (symbol, capture source); a ``BARS``
row lists at least one bar and the others none. The previous hash must name a
row of the same session (composite foreign key), so a chain can never borrow
another session's history. A heartbeat claims process liveness only; whether a
minute was proven is derived, never stored.

Purely additive; downgrade drops the table.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261017_0064"
down_revision = "20261016_0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE runner_liveness_progress (
        progress_id UUID PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE CHECK(idempotency_key ~ '^runner-liveness:[0-9a-f-]{36}:[0-9]+$'),
        content_hash CHAR(64) NOT NULL UNIQUE CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        runner_session_id UUID NOT NULL,
        sequence BIGINT NOT NULL CHECK(sequence >= 0),
        kind TEXT NOT NULL CHECK(kind IN ('STARTED','HEARTBEAT','BARS')),
        symbol TEXT CHECK(symbol ~ '^[A-Z0-9]{2,20}$'),
        capture_source_id UUID,
        binding_hash CHAR(64) NOT NULL CHECK(binding_hash ~ '^[0-9a-f]{64}$'),
        previous_progress_hash CHAR(64) CHECK(previous_progress_hash ~ '^[0-9a-f]{64}$'),
        host_wall_at TIMESTAMPTZ NOT NULL,
        bar_count INTEGER NOT NULL CHECK(bar_count >= 0),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        UNIQUE (runner_session_id, sequence),
        UNIQUE (runner_session_id, content_hash),
        FOREIGN KEY (runner_session_id, previous_progress_hash)
            REFERENCES runner_liveness_progress(runner_session_id, content_hash),
        CHECK((sequence = 0) = (kind = 'STARTED')),
        CHECK((kind = 'STARTED') = (previous_progress_hash IS NULL)),
        CHECK((kind = 'STARTED') = (symbol IS NOT NULL AND capture_source_id IS NOT NULL)),
        CHECK((kind <> 'STARTED') = (symbol IS NULL AND capture_source_id IS NULL)),
        CHECK((kind = 'BARS') = (bar_count > 0)))"""
    )
    op.execute("CREATE INDEX runner_liveness_progress_started_idx "
               "ON runner_liveness_progress(symbol, host_wall_at) WHERE kind = 'STARTED'")
    op.execute(immutable_trigger_sql("runner_liveness_progress"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS runner_liveness_progress")
