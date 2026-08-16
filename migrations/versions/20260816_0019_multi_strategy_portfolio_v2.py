"""add immutable multi-strategy portfolio construction authorities

Revision ID: 20260816_0019
Revises: 20260816_0018
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260816_0019"
down_revision = "20260816_0018"
branch_labels = None
depends_on = None


TABLES = (
    "portfolio_construction_policy_versions",
    "portfolio_construction_runs",
    "portfolio_construction_run_data_health_assessments",
    "portfolio_sleeve_inputs",
    "portfolio_covariance_estimates",
    "portfolio_target_candidates",
    "portfolio_target_sleeve_weights",
    "portfolio_constraint_evaluations",
    "portfolio_risk_gate_evidence",
)


def upgrade() -> None:
    statements = (
        """CREATE TABLE portfolio_construction_policy_versions (
            policy_version_id UUID PRIMARY KEY, policy_id UUID NOT NULL,
            version TEXT NOT NULL, implementation_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status='RESEARCH_ONLY'),
            policy JSONB NOT NULL CHECK(jsonb_typeof(policy)='object'),
            automatic_authority BOOLEAN NOT NULL DEFAULT FALSE CHECK(automatic_authority=FALSE),
            created_at TIMESTAMPTZ NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(policy_id,version)
        )""",
        """CREATE TABLE portfolio_construction_runs (
            run_id UUID PRIMARY KEY,
            policy_version_id UUID NOT NULL REFERENCES portfolio_construction_policy_versions(policy_version_id),
            regime_run_id UUID NOT NULL REFERENCES regime_runs(run_id),
            regime_content_hash CHAR(64) NOT NULL,
            constructed_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('BLOCKED','REVIEW_REQUIRED')),
            equity NUMERIC(30,12) NOT NULL CHECK(equity>0),
            limitations JSONB NOT NULL CHECK(jsonb_typeof(limitations)='array'),
            report JSONB NOT NULL CHECK(jsonb_typeof(report)='object'),
            content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        """CREATE TABLE portfolio_construction_run_data_health_assessments (
            run_id UUID NOT NULL REFERENCES portfolio_construction_runs(run_id),
            assessment_id UUID NOT NULL REFERENCES data_health_assessments(assessment_id),
            PRIMARY KEY(run_id,assessment_id)
        )""",
        """CREATE TABLE portfolio_sleeve_inputs (
            sleeve_input_id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES portfolio_construction_runs(run_id),
            sequence INTEGER NOT NULL CHECK(sequence>=0),
            strategy_version_id UUID NOT NULL REFERENCES strategy_versions(strategy_version_id),
            scorecard_id UUID NOT NULL REFERENCES strategy_scorecards(scorecard_id),
            validation_package_id UUID NOT NULL REFERENCES validation_packages(package_id),
            regime_candidate_id UUID NOT NULL REFERENCES regime_risk_adjustment_candidates(candidate_id),
            strategy_key TEXT NOT NULL, payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
            content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(run_id,sequence), UNIQUE(run_id,strategy_key)
        )""",
        """CREATE TABLE portfolio_covariance_estimates (
            covariance_id UUID PRIMARY KEY,
            run_id UUID NOT NULL UNIQUE REFERENCES portfolio_construction_runs(run_id),
            dataset_version_id UUID NOT NULL REFERENCES historical_dataset_versions(dataset_version_id),
            dataset_version TEXT NOT NULL, dataset_content_hash CHAR(64) NOT NULL,
            estimation_version TEXT NOT NULL, observations INTEGER NOT NULL CHECK(observations>=2),
            as_of TIMESTAMPTZ NOT NULL,
            raw_matrix JSONB NOT NULL CHECK(jsonb_typeof(raw_matrix)='array'),
            stressed_matrix JSONB NOT NULL CHECK(jsonb_typeof(stressed_matrix)='array'),
            uncertainty NUMERIC(18,12) NOT NULL CHECK(uncertainty BETWEEN 0 AND 1),
            correlation_stress NUMERIC(18,12) NOT NULL CHECK(correlation_stress BETWEEN 0 AND 1),
            content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        """CREATE TABLE portfolio_target_candidates (
            candidate_id UUID PRIMARY KEY,
            run_id UUID NOT NULL UNIQUE REFERENCES portfolio_construction_runs(run_id),
            status TEXT NOT NULL CHECK(status IN ('BLOCKED','REVIEW_REQUIRED')),
            automatic_authority BOOLEAN NOT NULL DEFAULT FALSE CHECK(automatic_authority=FALSE),
            cash_weight NUMERIC(18,12) NOT NULL,
            gross_weight NUMERIC(18,12) NOT NULL CHECK(gross_weight>=0),
            net_weight NUMERIC(18,12) NOT NULL,
            portfolio_volatility NUMERIC(18,12) NOT NULL CHECK(portfolio_volatility>=0),
            stressed_volatility NUMERIC(18,12) NOT NULL CHECK(stressed_volatility>=0),
            created_at TIMESTAMPTZ NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        """CREATE TABLE portfolio_target_sleeve_weights (
            candidate_id UUID NOT NULL REFERENCES portfolio_target_candidates(candidate_id),
            sleeve_input_id UUID NOT NULL REFERENCES portfolio_sleeve_inputs(sleeve_input_id),
            target_weight NUMERIC(18,12) NOT NULL,
            effective_notional NUMERIC(30,12) NOT NULL,
            marginal_risk NUMERIC(18,12) NOT NULL,
            component_risk NUMERIC(18,12) NOT NULL,
            adjustment_reasons JSONB NOT NULL CHECK(jsonb_typeof(adjustment_reasons)='array'),
            PRIMARY KEY(candidate_id,sleeve_input_id)
        )""",
        """CREATE TABLE portfolio_constraint_evaluations (
            constraint_id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES portfolio_construction_runs(run_id),
            name TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('SATISFIED','REDUCED','BLOCKED')),
            observed NUMERIC(30,12), limit_value NUMERIC(30,12),
            reasons JSONB NOT NULL CHECK(jsonb_typeof(reasons)='array'),
            content_hash CHAR(64) NOT NULL UNIQUE, UNIQUE(run_id,name)
        )""",
        """CREATE TABLE portfolio_risk_gate_evidence (
            run_id UUID PRIMARY KEY REFERENCES portfolio_construction_runs(run_id),
            approved BOOLEAN NOT NULL,
            gross_notional NUMERIC(30,12) NOT NULL CHECK(gross_notional>=0),
            net_notional NUMERIC(30,12) NOT NULL,
            stress_loss NUMERIC(30,12) NOT NULL,
            reasons JSONB NOT NULL CHECK(jsonb_typeof(reasons)='array'),
            report JSONB NOT NULL CHECK(jsonb_typeof(report)='object'),
            automatic_authority BOOLEAN NOT NULL DEFAULT FALSE CHECK(automatic_authority=FALSE),
            content_hash CHAR(64) NOT NULL UNIQUE
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
