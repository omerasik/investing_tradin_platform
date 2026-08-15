"""add authorized historical market-data vertical slice

Revision ID: 20260815_0009
Revises: 20260815_0008
Create Date: 2026-08-15
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260815_0009"
down_revision = "20260815_0008"
branch_labels = None
depends_on = None


TABLES = (
    "historical_data_sources",
    "historical_raw_observations",
    "historical_normalized_observations",
    "historical_dataset_versions",
    "historical_dataset_members",
)


def upgrade() -> None:
    statements = (
        """CREATE TABLE historical_data_sources (
            source_id UUID PRIMARY KEY,
            provider TEXT NOT NULL,
            dataset_name TEXT NOT NULL,
            provider_identifier_namespace TEXT NOT NULL,
            provider_terms_version TEXT NOT NULL,
            authorization_reference TEXT NOT NULL,
            authorized_at TIMESTAMPTZ NOT NULL,
            asset_scope TEXT NOT NULL CHECK(asset_scope = 'US_EQUITIES_ETFS'),
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE(provider, dataset_name, provider_terms_version)
        )""",
        """CREATE TABLE historical_raw_observations (
            raw_observation_id UUID PRIMARY KEY,
            source_id UUID NOT NULL REFERENCES historical_data_sources(source_id),
            observation_kind TEXT NOT NULL CHECK(observation_kind IN
                ('OHLCV','DIVIDEND','SPLIT','SYMBOL_CHANGE','DELISTING')),
            provider_identifier TEXT NOT NULL,
            provider_symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            event_at TIMESTAMPTZ NOT NULL,
            effective_at TIMESTAMPTZ NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL,
            adjustment_status TEXT NOT NULL CHECK(adjustment_status IN
                ('RAW','AS_REPORTED','POINT_IN_TIME_ADJUSTED','LATEST_ADJUSTED')),
            revision INTEGER NOT NULL CHECK(revision >= 0),
            provenance_uri TEXT NOT NULL,
            raw_payload JSONB NOT NULL,
            raw_payload_sha256 CHAR(64) NOT NULL,
            UNIQUE(source_id, provider_identifier, observation_kind, event_at, revision)
        )""",
        "CREATE INDEX historical_raw_asof_idx ON historical_raw_observations(source_id, provider_identifier, event_at, effective_at, ingested_at, revision DESC)",
        """CREATE TABLE historical_normalized_observations (
            normalized_observation_id UUID PRIMARY KEY,
            raw_observation_id UUID NOT NULL UNIQUE REFERENCES historical_raw_observations(raw_observation_id),
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            normalization_version TEXT NOT NULL,
            normalized_value JSONB NOT NULL,
            quality_status TEXT NOT NULL CHECK(quality_status IN ('VALIDATED','REJECTED')),
            quality_issues JSONB NOT NULL,
            normalized_at TIMESTAMPTZ NOT NULL
        )""",
        "CREATE INDEX historical_normalized_instrument_idx ON historical_normalized_observations(instrument_id, normalized_at)",
        """CREATE TABLE historical_dataset_versions (
            dataset_version_id UUID PRIMARY KEY,
            source_id UUID NOT NULL REFERENCES historical_data_sources(source_id),
            version TEXT NOT NULL,
            normalization_version TEXT NOT NULL,
            content_hash CHAR(64) NOT NULL UNIQUE,
            valid_from TIMESTAMPTZ NOT NULL,
            valid_until TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL CHECK(status = 'SEALED'),
            CHECK(valid_until IS NULL OR valid_until >= valid_from),
            UNIQUE(source_id, version)
        )""",
        """CREATE TABLE historical_dataset_members (
            dataset_version_id UUID NOT NULL REFERENCES historical_dataset_versions(dataset_version_id),
            normalized_observation_id UUID NOT NULL REFERENCES historical_normalized_observations(normalized_observation_id),
            PRIMARY KEY(dataset_version_id, normalized_observation_id)
        )""",
    )
    for statement in statements:
        op.execute(statement)
    for table in TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
