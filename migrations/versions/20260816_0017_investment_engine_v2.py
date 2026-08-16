"""add immutable point-in-time investment engine authorities

Revision ID: 20260816_0017
Revises: 20260816_0016
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260816_0017"
down_revision = "20260816_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        "ALTER TABLE investment_theses ADD COLUMN thesis_schema_version TEXT",
        "ALTER TABLE investment_theses ADD COLUMN pit_instrument_id TEXT REFERENCES professional_instruments(instrument_id)",
        "ALTER TABLE investment_theses ADD COLUMN parent_thesis_id UUID REFERENCES investment_theses(thesis_id)",
        "ALTER TABLE investment_theses ADD COLUMN knowledge_cutoff TIMESTAMPTZ",
        "ALTER TABLE investment_theses ADD COLUMN contract JSONB",
        "ALTER TABLE investment_theses ADD COLUMN content_hash CHAR(64)",
        "CREATE UNIQUE INDEX investment_theses_content_hash_idx ON investment_theses(content_hash) WHERE content_hash IS NOT NULL",
        "ALTER TABLE investment_evidence ADD COLUMN evidence_type TEXT",
        "ALTER TABLE investment_evidence ADD COLUMN knowledge_cutoff TIMESTAMPTZ",
        "ALTER TABLE investment_evidence ADD COLUMN content_hash CHAR(64)",
        "CREATE UNIQUE INDEX investment_evidence_content_hash_idx ON investment_evidence(content_hash) WHERE content_hash IS NOT NULL",
        """CREATE TABLE investment_evidence_data_health_assessments (
            investment_evidence_id UUID NOT NULL REFERENCES investment_evidence(investment_evidence_id),
            assessment_id UUID NOT NULL REFERENCES data_health_assessments(assessment_id),
            PRIMARY KEY(investment_evidence_id,assessment_id)
        )""",
        """CREATE TABLE investment_policy_versions (
            policy_version_id UUID PRIMARY KEY, policy_id UUID NOT NULL,
            version TEXT NOT NULL, account_id TEXT NOT NULL REFERENCES accounts(account_id),
            capital_domain TEXT NOT NULL CHECK(capital_domain='LONG_TERM_INVESTMENT'),
            base_currency CHAR(3) NOT NULL,
            maximum_investment_value NUMERIC(30,12) NOT NULL CHECK(maximum_investment_value>0),
            maximum_single_weight NUMERIC(18,12) NOT NULL CHECK(maximum_single_weight>0 AND maximum_single_weight<=1),
            minimum_cash_weight NUMERIC(18,12) NOT NULL CHECK(minimum_cash_weight>=0 AND minimum_cash_weight<1),
            maximum_rebalance_turnover NUMERIC(18,12) NOT NULL CHECK(maximum_rebalance_turnover>=0 AND maximum_rebalance_turnover<=2),
            approved_by TEXT NOT NULL, approved_at TIMESTAMPTZ NOT NULL,
            limitations JSONB NOT NULL CHECK(jsonb_typeof(limitations)='array'),
            content_hash CHAR(64) NOT NULL UNIQUE, UNIQUE(policy_id,version)
        )""",
        """CREATE TABLE investment_rebalance_candidates (
            candidate_id UUID PRIMARY KEY,
            policy_version_id UUID NOT NULL REFERENCES investment_policy_versions(policy_version_id),
            account_id TEXT NOT NULL REFERENCES accounts(account_id),
            as_of TIMESTAMPTZ NOT NULL, holdings_snapshot_hash CHAR(64) NOT NULL,
            analysis_evidence_ids JSONB NOT NULL CHECK(jsonb_typeof(analysis_evidence_ids)='array'),
            current_weights JSONB NOT NULL CHECK(jsonb_typeof(current_weights)='object'),
            candidate_weights JSONB NOT NULL CHECK(jsonb_typeof(candidate_weights)='object'),
            turnover NUMERIC(18,12) NOT NULL CHECK(turnover>=0),
            status TEXT NOT NULL CHECK(status IN ('BLOCKED','REVIEW_REQUIRED')),
            reasons JSONB NOT NULL CHECK(jsonb_typeof(reasons)='array'),
            limitations JSONB NOT NULL CHECK(jsonb_typeof(limitations)='array'),
            execution_authority BOOLEAN NOT NULL DEFAULT FALSE CHECK(execution_authority=FALSE),
            content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        "CREATE INDEX investment_rebalance_candidates_lookup_idx ON investment_rebalance_candidates(account_id,as_of DESC,status)",
    ):
        op.execute(statement)
    for table in (
        "investment_theses",
        "investment_evidence",
        "investment_reviews",
        "investment_evidence_data_health_assessments",
        "investment_policy_versions",
        "investment_rebalance_candidates",
    ):
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    for table in (
        "investment_rebalance_candidates",
        "investment_policy_versions",
        "investment_evidence_data_health_assessments",
        "investment_reviews",
        "investment_evidence",
        "investment_theses",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP TABLE IF EXISTS investment_rebalance_candidates")
    op.execute("DROP TABLE IF EXISTS investment_policy_versions")
    op.execute("DROP TABLE IF EXISTS investment_evidence_data_health_assessments")
    op.execute("DROP INDEX IF EXISTS investment_evidence_content_hash_idx")
    for column in ("content_hash", "knowledge_cutoff", "evidence_type"):
        op.execute(f"ALTER TABLE investment_evidence DROP COLUMN IF EXISTS {column}")
    op.execute("DROP INDEX IF EXISTS investment_theses_content_hash_idx")
    for column in (
        "content_hash",
        "contract",
        "knowledge_cutoff",
        "parent_thesis_id",
        "pit_instrument_id",
        "thesis_schema_version",
    ):
        op.execute(f"ALTER TABLE investment_theses DROP COLUMN IF EXISTS {column}")
