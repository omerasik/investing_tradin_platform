"""add versioned point-in-time feature authority

Revision ID: 20260816_0014
Revises: 20260816_0013
Create Date: 2026-08-16
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260816_0014"
down_revision = "20260816_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE feature_definition_versions (
        feature_id UUID PRIMARY KEY, name TEXT NOT NULL,
        family TEXT NOT NULL CHECK(family IN ('PRICE_RETURNS','TREND','MOMENTUM','VOLATILITY','LIQUIDITY','FUNDAMENTAL','MACRO')),
        semantic_version TEXT NOT NULL, owner TEXT NOT NULL, description TEXT NOT NULL,
        required_dataset_types JSONB NOT NULL CHECK(jsonb_typeof(required_dataset_types)='array'),
        required_fields JSONB NOT NULL CHECK(jsonb_typeof(required_fields)='array'),
        frequency TEXT NOT NULL, timestamp_semantics TEXT NOT NULL, lookback INTEGER NOT NULL CHECK(lookback>=0),
        parameters JSONB NOT NULL CHECK(jsonb_typeof(parameters)='object'),
        missing_value_policy TEXT NOT NULL, outlier_policy TEXT NOT NULL, leakage_policy TEXT NOT NULL,
        expected_minimum NUMERIC(38,12), expected_maximum NUMERIC(38,12), units TEXT NOT NULL,
        calculation_version TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, retired_at TIMESTAMPTZ,
        CHECK(expected_minimum IS NULL OR expected_maximum IS NULL OR expected_minimum<=expected_maximum),
        CHECK(retired_at IS NULL OR retired_at>=created_at), UNIQUE(name,semantic_version))"""
    )
    op.execute(
        """CREATE TABLE feature_materializations (
        materialization_id UUID PRIMARY KEY, feature_id UUID NOT NULL REFERENCES feature_definition_versions(feature_id),
        instrument_id TEXT NOT NULL, dataset_version TEXT NOT NULL, event_at TIMESTAMPTZ NOT NULL,
        effective_at TIMESTAMPTZ NOT NULL, knowledge_at TIMESTAMPTZ NOT NULL, computed_at TIMESTAMPTZ NOT NULL,
        source_observation_manifest JSONB NOT NULL CHECK(jsonb_typeof(source_observation_manifest)='array'),
        value NUMERIC(38,12), quality_status TEXT NOT NULL CHECK(quality_status IN ('VALIDATED','DEGRADED','REJECTED')),
        content_hash CHAR(64) NOT NULL, CHECK(effective_at>=event_at AND knowledge_at>=effective_at AND computed_at>=knowledge_at),
        CHECK((quality_status='VALIDATED' AND value IS NOT NULL) OR quality_status<>'VALIDATED'),
        UNIQUE(feature_id,instrument_id,dataset_version,event_at,effective_at,knowledge_at))"""
    )
    op.execute("CREATE INDEX feature_materializations_asof_idx ON feature_materializations(feature_id,instrument_id,dataset_version,event_at,knowledge_at DESC,computed_at DESC)")
    op.execute(immutable_trigger_sql("feature_definition_versions"))
    op.execute(immutable_trigger_sql("feature_materializations"))


def downgrade() -> None:
    for table in ("feature_materializations", "feature_definition_versions"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
