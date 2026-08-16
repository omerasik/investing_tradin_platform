"""add immutable normalized strategy scorecards

Revision ID: 20260816_0015
Revises: 20260816_0014
"""
from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260816_0015"
down_revision = "20260816_0014"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""CREATE TABLE strategy_scorecards (scorecard_id UUID PRIMARY KEY, scorecard_schema_version TEXT NOT NULL, strategy_id UUID NOT NULL, strategy_version TEXT NOT NULL, research_run_id UUID NOT NULL, feature_versions JSONB NOT NULL CHECK(jsonb_typeof(feature_versions)='array'), dataset_version TEXT NOT NULL, cost_model_version TEXT NOT NULL, evaluated_at TIMESTAMPTZ NOT NULL, knowledge_cutoff TIMESTAMPTZ NOT NULL, status TEXT NOT NULL CHECK(status IN ('BLOCKED','REVIEW_REQUIRED')), limitations JSONB NOT NULL CHECK(jsonb_typeof(limitations)='array'), dataset_health_status TEXT NOT NULL, data_health_assessment_ids JSONB NOT NULL CHECK(jsonb_typeof(data_health_assessment_ids)='array'), evidence_manifest JSONB NOT NULL CHECK(jsonb_typeof(evidence_manifest)='object'), content_hash CHAR(64) NOT NULL, CHECK(knowledge_cutoff<=evaluated_at))""")
    op.execute("CREATE INDEX strategy_scorecards_lookup_idx ON strategy_scorecards(strategy_id,strategy_version,dataset_version,evaluated_at DESC,status)")
    op.execute("CREATE UNIQUE INDEX strategy_scorecards_content_hash_idx ON strategy_scorecards(content_hash)")
    op.execute("CREATE INDEX strategy_scorecards_run_idx ON strategy_scorecards(research_run_id,evaluated_at DESC)")
    op.execute("""CREATE TABLE scorecard_metric_observations (metric_id UUID PRIMARY KEY, scorecard_id UUID NOT NULL REFERENCES strategy_scorecards(scorecard_id), family TEXT NOT NULL CHECK(family IN ('PERFORMANCE','ROBUSTNESS','EXECUTION','RISK','DATA_QUALITY','SIGNAL_DECAY')), name TEXT NOT NULL, evidence_state TEXT NOT NULL CHECK(evidence_state IN ('MEASURED','ASSUMED','UNAVAILABLE')), value NUMERIC(38,12), unit TEXT NOT NULL, dimensions JSONB NOT NULL CHECK(jsonb_typeof(dimensions)='array'), CHECK((evidence_state='UNAVAILABLE' AND value IS NULL) OR (evidence_state<>'UNAVAILABLE' AND value IS NOT NULL)), UNIQUE(scorecard_id,family,name,dimensions))""")
    op.execute("""CREATE TABLE scorecard_components (component_id UUID PRIMARY KEY, scorecard_id UUID NOT NULL REFERENCES strategy_scorecards(scorecard_id), name TEXT NOT NULL, formula_version TEXT NOT NULL, value NUMERIC(38,12), rationale TEXT NOT NULL, UNIQUE(scorecard_id,name,formula_version))""")
    op.execute("""CREATE TABLE scorecard_data_health_assessments (scorecard_id UUID NOT NULL REFERENCES strategy_scorecards(scorecard_id), assessment_id UUID NOT NULL REFERENCES data_health_assessments(assessment_id), PRIMARY KEY(scorecard_id,assessment_id))""")
    op.execute("""CREATE TABLE scorecard_validation_packages (scorecard_id UUID PRIMARY KEY REFERENCES strategy_scorecards(scorecard_id), package_id UUID NOT NULL REFERENCES validation_packages(package_id), package_content_hash CHAR(64) NOT NULL, UNIQUE(package_id, scorecard_id))""")
    for table in ('strategy_scorecards','scorecard_metric_observations','scorecard_components','scorecard_data_health_assessments','scorecard_validation_packages'):
        op.execute(immutable_trigger_sql(table))

def downgrade() -> None:
    for table in ('scorecard_validation_packages','scorecard_data_health_assessments','scorecard_components','scorecard_metric_observations','strategy_scorecards'):
        op.execute(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}'); op.execute(f'DROP TABLE IF EXISTS {table}')
