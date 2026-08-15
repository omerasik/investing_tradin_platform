"""add point-in-time macro catalogue

Revision ID: 20260815_0012
Revises: 20260815_0011
Create Date: 2026-08-15
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260815_0012"
down_revision = "20260815_0011"
branch_labels = None
depends_on = None
TABLES = ("pit_macro_sources", "pit_macro_observations")


def upgrade() -> None:
    statements = (
        """CREATE TABLE pit_macro_sources (
        source_id UUID PRIMARY KEY, provider TEXT NOT NULL, terms_version TEXT NOT NULL,
        authorization_reference TEXT NOT NULL, authorized_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, UNIQUE(provider,terms_version))""",
        """CREATE TABLE pit_macro_observations (
        observation_id UUID PRIMARY KEY, source_id UUID NOT NULL REFERENCES pit_macro_sources(source_id),
        category TEXT NOT NULL CHECK(category IN ('POLICY_RATE','CPI','EMPLOYMENT','GDP','YIELD_CURVE','LIQUIDITY_CREDIT')),
        series_code TEXT NOT NULL, observation_period DATE NOT NULL,
        initial_release_at TIMESTAMPTZ NOT NULL, release_at TIMESTAMPTZ NOT NULL,
        actual NUMERIC(38,12), expected NUMERIC(38,12), prior NUMERIC(38,12),
        revision INTEGER NOT NULL CHECK(revision>=0), ingested_at TIMESTAMPTZ NOT NULL,
        provenance_uri TEXT NOT NULL, raw_payload_sha256 CHAR(64) NOT NULL,
        CHECK(release_at>=initial_release_at), CHECK(ingested_at>=release_at),
        CHECK((revision=0 AND release_at=initial_release_at) OR revision>0),
        UNIQUE(source_id,series_code,observation_period,revision))""",
        "CREATE INDEX pit_macro_asof_idx ON pit_macro_observations(series_code,observation_period,release_at,ingested_at,revision DESC)",
    )
    for statement in statements:
        op.execute(statement)
    for table in TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
