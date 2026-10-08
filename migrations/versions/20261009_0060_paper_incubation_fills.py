"""Paper incubation fills (Phase R10 core)

Revision ID: 20261009_0060
Revises: 20261008_0059
Create Date: 2026-10-09

``paper_incubation_fills`` -- one immutable, content-addressed paper fill (or
recorded non-fill) per INCUBATING live signal. A fill price exists exactly when
the status is ``FILLED``; the fill bar opens strictly after the decision, and a
composite foreign key binds the fill to its stored signal's content hash and
decision instant. Unit exposure only (sizing waits for OR-11); never an order,
never an account.

Additive (one new unique constraint on ``live_strategy_signals``, already
implied by its primary key); downgrade drops both.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261009_0060"
down_revision = "20261008_0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A fill is bound to its stored signal's content and decision instant, so it can
    # never be computed from another copy of the decision.
    op.execute("ALTER TABLE live_strategy_signals ADD CONSTRAINT live_strategy_signals_decision_key "
               "UNIQUE (signal_id, content_hash, decided_at)")
    op.execute(
        """CREATE TABLE paper_incubation_fills (
        fill_id UUID PRIMARY KEY,
        content_hash CHAR(64) NOT NULL UNIQUE CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        signal_id UUID NOT NULL UNIQUE,
        signal_content_hash CHAR(64) NOT NULL,
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        trial_id UUID NOT NULL,
        symbol TEXT NOT NULL CHECK(symbol ~ '^[A-Z0-9]{2,20}$'),
        status TEXT NOT NULL CHECK(status IN
            ('FILLED','FILL_BAR_NOT_OBSERVED','FILL_OPEN_SEQUENCE_AMBIGUOUS')),
        target_from SMALLINT NOT NULL CHECK(target_from IN (-1,0,1)),
        target_to SMALLINT NOT NULL CHECK(target_to IN (-1,0,1)),
        decided_at TIMESTAMPTZ NOT NULL,
        fill_bar_open_at TIMESTAMPTZ NOT NULL,
        fill_price NUMERIC(38,18),
        cost_policy_hash CHAR(64) NOT NULL CHECK(cost_policy_hash ~ '^[0-9a-f]{64}$'),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        FOREIGN KEY (signal_id, signal_content_hash, decided_at)
            REFERENCES live_strategy_signals(signal_id, content_hash, decided_at),
        CHECK(target_from <> target_to),
        CHECK(fill_bar_open_at > decided_at),
        CHECK(fill_bar_open_at <= decided_at + interval '1 minute'),
        CHECK((status = 'FILLED' AND fill_price IS NOT NULL AND fill_price > 0)
              OR (status <> 'FILLED' AND fill_price IS NULL)))"""
    )
    op.execute("CREATE INDEX paper_incubation_fills_candidate_idx "
               "ON paper_incubation_fills(study_id, trial_id, symbol, fill_bar_open_at)")
    op.execute(immutable_trigger_sql("paper_incubation_fills"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_incubation_fills")
    op.execute("ALTER TABLE live_strategy_signals DROP CONSTRAINT IF EXISTS live_strategy_signals_decision_key")
