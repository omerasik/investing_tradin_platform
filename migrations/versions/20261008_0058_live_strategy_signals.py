"""Live strategy signals from forward T4 capture (Phase R8 core)

Revision ID: 20261008_0058
Revises: 20261008_0057
Create Date: 2026-10-08

``live_strategy_signals`` -- immutable, content-addressed proposals emitted
when a watched frozen candidate's Decimal target changes on a completed live
T4 bar. ``authority`` is ``RESEARCH_WATCH`` or ``INCUBATING`` by CHECK: there is
no value that claims validation. A signal is research evidence, never an order.

Additive; downgrade drops the table.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261008_0058"
down_revision = "20261008_0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE live_strategy_signals (
        signal_id UUID PRIMARY KEY,
        content_hash CHAR(64) NOT NULL UNIQUE CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        trial_id UUID NOT NULL,
        symbol TEXT NOT NULL CHECK(symbol ~ '^[A-Z0-9]{2,20}$'),
        authority TEXT NOT NULL CHECK(authority IN ('RESEARCH_WATCH','INCUBATING')),
        target_from SMALLINT NOT NULL CHECK(target_from IN (-1,0,1)),
        target_to SMALLINT NOT NULL CHECK(target_to IN (-1,0,1)),
        bar_open_at TIMESTAMPTZ NOT NULL,
        decided_at TIMESTAMPTZ NOT NULL,
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        CHECK(target_from <> target_to),
        CHECK(decided_at >= bar_open_at + interval '1 minute'))"""
    )
    op.execute("CREATE INDEX live_strategy_signals_decided_idx ON live_strategy_signals(decided_at DESC)")
    op.execute(immutable_trigger_sql("live_strategy_signals"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS live_strategy_signals")
