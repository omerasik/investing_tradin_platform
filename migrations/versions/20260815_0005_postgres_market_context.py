"""add immutable point-in-time PostgreSQL pre-trade market context

Revision ID: 20260815_0005
Revises: 20260815_0004
Create Date: 2026-08-15
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260815_0005"
down_revision = "20260815_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE runtime_quote_observations (quote_id UUID PRIMARY KEY, "
        "instrument_id TEXT NOT NULL, bid NUMERIC(30,12) NOT NULL CHECK(bid > 0), "
        "ask NUMERIC(30,12) NOT NULL CHECK(ask >= bid), last NUMERIC(30,12) NOT NULL CHECK(last > 0), "
        "quality_score NUMERIC(8,6) NOT NULL CHECK(quality_score BETWEEN 0 AND 1), "
        "provider TEXT NOT NULL, source_reference TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL, "
        "ingested_at TIMESTAMPTZ NOT NULL CHECK(ingested_at >= observed_at), "
        "UNIQUE(instrument_id, provider, source_reference))"
    )
    op.execute(
        "CREATE INDEX runtime_quotes_asof_idx ON runtime_quote_observations "
        "(instrument_id, observed_at DESC, ingested_at DESC)"
    )
    op.execute(
        "CREATE TABLE runtime_execution_evidence (evidence_id UUID PRIMARY KEY, "
        "evidence_type TEXT NOT NULL CHECK(evidence_type IN ('HALT','EVENT_RISK','SLIPPAGE')), "
        "instrument_id TEXT NOT NULL, side TEXT CHECK(side IN ('BUY','SELL')), value NUMERIC(30,12) NOT NULL, "
        "source TEXT NOT NULL, source_reference TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL, "
        "ingested_at TIMESTAMPTZ NOT NULL CHECK(ingested_at >= observed_at), "
        "CHECK((evidence_type = 'SLIPPAGE' AND side IS NOT NULL) OR "
        "(evidence_type <> 'SLIPPAGE' AND side IS NULL)), "
        "UNIQUE(evidence_type, instrument_id, side, source, source_reference))"
    )
    op.execute(
        "CREATE INDEX runtime_execution_asof_idx ON runtime_execution_evidence "
        "(instrument_id, evidence_type, side, observed_at DESC, ingested_at DESC)"
    )
    op.execute(
        "CREATE TABLE runtime_portfolio_returns (observation_id UUID PRIMARY KEY, account_id TEXT NOT NULL, "
        "period_start TIMESTAMPTZ NOT NULL, period_end TIMESTAMPTZ NOT NULL CHECK(period_end > period_start), "
        "return_fraction NUMERIC(30,12) NOT NULL CHECK(return_fraction > -1), provider TEXT NOT NULL, "
        "source_reference TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL CHECK(ingested_at >= period_end), "
        "UNIQUE(account_id, provider, source_reference))"
    )
    op.execute(
        "CREATE INDEX runtime_returns_asof_idx ON runtime_portfolio_returns "
        "(account_id, period_end, ingested_at)"
    )
    for table in (
        "runtime_quote_observations",
        "runtime_execution_evidence",
        "runtime_portfolio_returns",
    ):
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    for table in (
        "runtime_portfolio_returns",
        "runtime_execution_evidence",
        "runtime_quote_observations",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
