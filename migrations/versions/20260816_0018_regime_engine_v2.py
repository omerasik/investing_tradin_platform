"""add immutable multidimensional regime research authorities

Revision ID: 20260816_0018
Revises: 20260816_0017
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260816_0018"
down_revision = "20260816_0017"
branch_labels = None
depends_on = None


TABLES = (
    "regime_model_versions",
    "regime_runs",
    "regime_run_data_health_assessments",
    "regime_observations",
    "regime_run_observations",
    "regime_method_evaluations",
    "regime_run_method_evaluations",
    "regime_risk_adjustment_candidates",
)


def upgrade() -> None:
    statements = (
        """CREATE TABLE regime_model_versions (
            model_version_id UUID PRIMARY KEY, model_id UUID NOT NULL, version TEXT NOT NULL,
            implementation_version TEXT NOT NULL, status TEXT NOT NULL CHECK(status='RESEARCH_ONLY'),
            trained_through TIMESTAMPTZ NOT NULL, feature_manifest JSONB NOT NULL CHECK(jsonb_typeof(feature_manifest)='array'),
            contract JSONB NOT NULL CHECK(jsonb_typeof(contract)='object'), created_at TIMESTAMPTZ NOT NULL,
            content_hash CHAR(64) NOT NULL UNIQUE, UNIQUE(model_id,version), CHECK(trained_through<=created_at)
        )""",
        """CREATE TABLE regime_runs (
            run_id UUID PRIMARY KEY, model_version_id UUID NOT NULL REFERENCES regime_model_versions(model_version_id),
            dataset_version_id UUID NOT NULL REFERENCES historical_dataset_versions(dataset_version_id),
            dataset_version TEXT NOT NULL, dataset_content_hash CHAR(64) NOT NULL,
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            evaluated_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL CHECK(status IN ('BLOCKED','REVIEW_REQUIRED')),
            limitations JSONB NOT NULL CHECK(jsonb_typeof(limitations)='array'), report JSONB NOT NULL,
            content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        """CREATE TABLE regime_run_data_health_assessments (
            run_id UUID NOT NULL REFERENCES regime_runs(run_id),
            assessment_id UUID NOT NULL REFERENCES data_health_assessments(assessment_id),
            PRIMARY KEY(run_id,assessment_id)
        )""",
        """CREATE TABLE regime_observations (
            observation_id UUID PRIMARY KEY, event_at TIMESTAMPTZ NOT NULL,
            method TEXT NOT NULL CHECK(method IN ('RULE_BASED','PROBABILISTIC_ENSEMBLE')),
            dimension TEXT NOT NULL, evidence_state TEXT NOT NULL CHECK(evidence_state IN ('MEASURED','UNAVAILABLE')),
            hard_label TEXT, probabilities JSONB NOT NULL CHECK(jsonb_typeof(probabilities)='object'),
            uncertainty NUMERIC(18,12), input_materialization_ids JSONB NOT NULL CHECK(jsonb_typeof(input_materialization_ids)='array'),
            content_hash CHAR(64) NOT NULL UNIQUE,
            CHECK((evidence_state='UNAVAILABLE' AND hard_label IS NULL AND uncertainty IS NULL) OR
                  (evidence_state='MEASURED' AND hard_label IS NOT NULL AND uncertainty IS NOT NULL))
        )""",
        "CREATE INDEX regime_observations_lookup_idx ON regime_observations(event_at,method,dimension)",
        """CREATE TABLE regime_run_observations (
            run_id UUID NOT NULL REFERENCES regime_runs(run_id),
            observation_id UUID NOT NULL REFERENCES regime_observations(observation_id),
            sequence INTEGER NOT NULL CHECK(sequence>=0), PRIMARY KEY(run_id,sequence),
            UNIQUE(run_id,observation_id)
        )""",
        """CREATE TABLE regime_method_evaluations (
            evaluation_id UUID PRIMARY KEY, method TEXT NOT NULL, dimension TEXT NOT NULL,
            sample_count INTEGER NOT NULL CHECK(sample_count>=0),
            brier_score NUMERIC(18,12), accuracy NUMERIC(18,12), evidence_state TEXT NOT NULL CHECK(evidence_state IN ('MEASURED','UNAVAILABLE')),
            label_ids JSONB NOT NULL CHECK(jsonb_typeof(label_ids)='array'), content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        """CREATE TABLE regime_run_method_evaluations (
            run_id UUID NOT NULL REFERENCES regime_runs(run_id),
            evaluation_id UUID NOT NULL REFERENCES regime_method_evaluations(evaluation_id),
            PRIMARY KEY(run_id,evaluation_id)
        )""",
        """CREATE TABLE regime_risk_adjustment_candidates (
            candidate_id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES regime_runs(run_id),
            strategy_version_id UUID NOT NULL REFERENCES strategy_versions(strategy_version_id),
            current_risk_multiplier NUMERIC(18,12) NOT NULL CHECK(current_risk_multiplier>=0),
            proposed_risk_multiplier NUMERIC(18,12) NOT NULL CHECK(proposed_risk_multiplier>=0),
            preapproved_maximum NUMERIC(18,12) NOT NULL CHECK(preapproved_maximum>=0),
            action TEXT NOT NULL CHECK(action IN ('DISABLE','REDUCE','MAINTAIN','INCREASE')),
            status TEXT NOT NULL CHECK(status IN ('BLOCKED','REVIEW_REQUIRED')),
            reasons JSONB NOT NULL CHECK(jsonb_typeof(reasons)='array'),
            automatic_authority BOOLEAN NOT NULL DEFAULT FALSE CHECK(automatic_authority=FALSE),
            created_at TIMESTAMPTZ NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE
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
