"""add point-in-time fundamental filing vertical slice

Revision ID: 20260815_0011
Revises: 20260815_0010
Create Date: 2026-08-15
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260815_0011"
down_revision = "20260815_0010"
branch_labels = None
depends_on = None

TABLES = ("pit_fundamental_sources", "pit_fundamental_filings", "pit_fundamental_facts")


def upgrade() -> None:
    statements = (
        """CREATE TABLE pit_fundamental_sources (
            source_id UUID PRIMARY KEY, provider TEXT NOT NULL, terms_version TEXT NOT NULL,
            authorization_reference TEXT NOT NULL, authorized_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, UNIQUE(provider, terms_version)
        )""",
        """CREATE TABLE pit_fundamental_filings (
            filing_record_id UUID PRIMARY KEY,
            source_id UUID NOT NULL REFERENCES pit_fundamental_sources(source_id),
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            filing_id TEXT NOT NULL, form_type TEXT NOT NULL,
            filing_at TIMESTAMPTZ NOT NULL, accepted_at TIMESTAMPTZ NOT NULL,
            reporting_period_start DATE NOT NULL, reporting_period_end DATE NOT NULL,
            fiscal_year INTEGER NOT NULL, fiscal_period TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 0), ingested_at TIMESTAMPTZ NOT NULL,
            provenance_uri TEXT NOT NULL, raw_payload_sha256 CHAR(64) NOT NULL,
            CHECK(reporting_period_end >= reporting_period_start),
            CHECK(filing_at >= reporting_period_end), CHECK(accepted_at >= filing_at),
            CHECK(ingested_at >= accepted_at), UNIQUE(source_id, filing_id, revision)
        )""",
        "CREATE INDEX pit_filing_asof_idx ON pit_fundamental_filings(instrument_id, accepted_at, ingested_at, reporting_period_end, revision DESC)",
        """CREATE TABLE pit_fundamental_facts (
            fact_id UUID PRIMARY KEY,
            filing_record_id UUID NOT NULL REFERENCES pit_fundamental_filings(filing_record_id),
            taxonomy_namespace TEXT NOT NULL, concept TEXT NOT NULL,
            statement_type TEXT NOT NULL CHECK(statement_type IN ('INCOME_STATEMENT','BALANCE_SHEET','CASH_FLOW','OTHER')),
            as_reported_value NUMERIC(38,12) NOT NULL, unit TEXT NOT NULL, currency CHAR(3),
            standardized_metric TEXT, standardized_value NUMERIC(38,12),
            standardization_version TEXT, dimensions JSONB NOT NULL,
            content_hash CHAR(64) NOT NULL UNIQUE,
            CHECK((standardized_metric IS NULL AND standardized_value IS NULL AND standardization_version IS NULL) OR
                  (standardized_metric IS NOT NULL AND standardized_value IS NOT NULL AND standardization_version IS NOT NULL)),
            UNIQUE(filing_record_id, taxonomy_namespace, concept, unit, content_hash)
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
