"""add PostgreSQL instrument, signal and model decision authorities

Revision ID: 20260815_0006
Revises: 20260815_0005
Create Date: 2026-08-15
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260815_0006"
down_revision = "20260815_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = (
        "CREATE TABLE runtime_instruments (instrument_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, venue TEXT NOT NULL, asset_class TEXT NOT NULL, quote_currency CHAR(3) NOT NULL, tick_size NUMERIC(30,12) NOT NULL CHECK(tick_size > 0), lot_size NUMERIC(30,12) NOT NULL CHECK(lot_size > 0), delisted_at TIMESTAMPTZ)",
        "CREATE TABLE runtime_instrument_risk_profiles (profile_id BIGSERIAL PRIMARY KEY, instrument_id TEXT NOT NULL REFERENCES runtime_instruments(instrument_id), sector TEXT NOT NULL, country TEXT NOT NULL, correlation_cluster TEXT NOT NULL, beta NUMERIC(30,12) NOT NULL, duration NUMERIC(30,12) NOT NULL, factors JSONB NOT NULL, effective_at TIMESTAMPTZ NOT NULL, ingested_at TIMESTAMPTZ NOT NULL CHECK(ingested_at >= effective_at), UNIQUE(instrument_id, effective_at, ingested_at))",
        "CREATE TABLE runtime_trading_sessions (venue TEXT NOT NULL, weekday SMALLINT NOT NULL CHECK(weekday BETWEEN 0 AND 6), opens_at TIME NOT NULL, closes_at TIME NOT NULL CHECK(closes_at > opens_at), timezone_name TEXT NOT NULL, PRIMARY KEY(venue, weekday, opens_at))",
        "CREATE TABLE runtime_signal_proposals (signal_id UUID PRIMARY KEY, instrument_id TEXT NOT NULL REFERENCES runtime_instruments(instrument_id), strategy_version TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL CHECK(expires_at > created_at), payload JSONB NOT NULL)",
        "CREATE TABLE runtime_signal_validations (assessment_id UUID PRIMARY KEY, signal_id UUID NOT NULL REFERENCES runtime_signal_proposals(signal_id), status TEXT NOT NULL, passed_stages JSONB NOT NULL, failures JSONB NOT NULL, assessed_at TIMESTAMPTZ NOT NULL)",
        "CREATE TABLE runtime_models (model_id UUID PRIMARY KEY, name TEXT NOT NULL, model_version TEXT NOT NULL, task TEXT NOT NULL, feature_version TEXT NOT NULL, dataset_version TEXT NOT NULL, artifact_reference TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, UNIQUE(name, model_version))",
        "CREATE TABLE runtime_model_validations (validation_id UUID PRIMARY KEY, model_id UUID NOT NULL REFERENCES runtime_models(model_id), metrics JSONB NOT NULL, economic_value_after_cost NUMERIC(30,12) NOT NULL, evidence_reference TEXT NOT NULL, validated_at TIMESTAMPTZ NOT NULL)",
        "CREATE TABLE runtime_model_approval_events (event_sequence BIGSERIAL PRIMARY KEY, event_id UUID NOT NULL UNIQUE, model_id UUID NOT NULL REFERENCES runtime_models(model_id), status TEXT NOT NULL CHECK(status IN ('APPROVED','DISABLED')), actor TEXT NOT NULL, reason TEXT NOT NULL, validation_id UUID REFERENCES runtime_model_validations(validation_id), occurred_at TIMESTAMPTZ NOT NULL)",
    )
    for statement in statements:
        op.execute(statement)
    for table in (
        "runtime_instruments",
        "runtime_instrument_risk_profiles",
        "runtime_trading_sessions",
        "runtime_signal_proposals",
        "runtime_signal_validations",
        "runtime_models",
        "runtime_model_validations",
        "runtime_model_approval_events",
    ):
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    for table in (
        "runtime_model_approval_events",
        "runtime_model_validations",
        "runtime_models",
        "runtime_signal_validations",
        "runtime_signal_proposals",
        "runtime_trading_sessions",
        "runtime_instrument_risk_profiles",
        "runtime_instruments",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
