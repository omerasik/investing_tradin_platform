"""add immutable model sensitivity and degradation evidence

Revision ID: 20260822_0033
Revises: 20260822_0032
Create Date: 2026-08-22
"""

from alembic import op

revision = "20260822_0033"
down_revision = "20260822_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE model_evaluation_reports ADD CONSTRAINT "
        "model_evaluation_report_id_hash_unique UNIQUE(report_id,content_hash)"
    )
    op.execute(
        "CREATE TABLE model_monitoring_policy_versions ("
        "policy_id UUID PRIMARY KEY, version TEXT NOT NULL UNIQUE CHECK (btrim(version)<>''), "
        "minimum_sensitivity_scenarios INTEGER NOT NULL CHECK "
        "(minimum_sensitivity_scenarios>=1), maximum_probability_shift NUMERIC(30,12) "
        "NOT NULL CHECK (maximum_probability_shift>0 AND maximum_probability_shift<=1), "
        "maximum_confidence_degradation NUMERIC(30,12) NOT NULL CHECK "
        "(maximum_confidence_degradation>0 AND maximum_confidence_degradation<=1), "
        "dimension_thresholds JSONB NOT NULL CHECK (jsonb_typeof(dimension_thresholds)='object' "
        "AND dimension_thresholds ?& ARRAY['DATA','FEATURE','PREDICTION','CALIBRATION',"
        "'PERFORMANCE','REGIME','EXECUTION','COST'] AND dimension_thresholds - ARRAY["
        "'DATA','FEATURE','PREDICTION','CALIBRATION','PERFORMANCE','REGIME','EXECUTION','COST']"
        "='{}'::jsonb), approved_by TEXT NOT NULL CHECK (btrim(approved_by)<>''), "
        "approved_at TIMESTAMPTZ NOT NULL, enabled BOOLEAN NOT NULL, content_hash CHAR(64) "
        "NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "UNIQUE(policy_id,content_hash))"
    )
    op.execute(
        "CREATE TABLE model_monitoring_reports ("
        "report_id UUID PRIMARY KEY, policy_id UUID NOT NULL, policy_content_hash CHAR(64) "
        "NOT NULL CHECK (policy_content_hash ~ '^[0-9a-f]{64}$'), "
        "evaluation_report_id UUID NOT NULL, evaluation_report_content_hash CHAR(64) NOT NULL "
        "CHECK (evaluation_report_content_hash ~ '^[0-9a-f]{64}$'), model_id UUID NOT NULL "
        "REFERENCES runtime_models(model_id), evaluated_at TIMESTAMPTZ NOT NULL, "
        "scenario_evidence_hash CHAR(64) NOT NULL CHECK "
        "(scenario_evidence_hash ~ '^[0-9a-f]{64}$'), degradation_evidence_hash CHAR(64) "
        "NOT NULL CHECK (degradation_evidence_hash ~ '^[0-9a-f]{64}$'), metrics JSONB NOT NULL "
        "CHECK (jsonb_typeof(metrics)='object'), dimension_scores JSONB NOT NULL CHECK "
        "(jsonb_typeof(dimension_scores)='object'), outcome TEXT NOT NULL CHECK (outcome IN "
        "('NO_THRESHOLD_BREACH_OBSERVED','DEGRADED_REVIEW_REQUIRED')), reasons JSONB NOT NULL "
        "CHECK (jsonb_typeof(reasons)='array'), limitations JSONB NOT NULL CHECK "
        "(jsonb_typeof(limitations)='array'), content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash ~ '^[0-9a-f]{64}$'), FOREIGN KEY(policy_id,policy_content_hash) "
        "REFERENCES model_monitoring_policy_versions(policy_id,content_hash), FOREIGN KEY "
        "(evaluation_report_id,evaluation_report_content_hash) REFERENCES "
        "model_evaluation_reports(report_id,content_hash), UNIQUE(policy_id,evaluation_report_id,"
        "scenario_evidence_hash,degradation_evidence_hash))"
    )
    op.execute(
        "CREATE TABLE model_explanation_sensitivity_scenarios ("
        "report_id UUID NOT NULL REFERENCES model_monitoring_reports(report_id), "
        "scenario_id TEXT NOT NULL CHECK (btrim(scenario_id)<>''), model_id UUID NOT NULL "
        "REFERENCES runtime_models(model_id), feature_name TEXT NOT NULL CHECK "
        "(btrim(feature_name)<>''), perturbation NUMERIC(30,18) NOT NULL CHECK "
        "(perturbation<>0 AND abs(perturbation)<=1), baseline_probability NUMERIC(30,18) "
        "NOT NULL CHECK (baseline_probability BETWEEN 0 AND 1), perturbed_probability "
        "NUMERIC(30,18) NOT NULL CHECK (perturbed_probability BETWEEN 0 AND 1), "
        "baseline_confidence NUMERIC(30,18) NOT NULL CHECK (baseline_confidence BETWEEN 0 AND 1), "
        "perturbed_confidence NUMERIC(30,18) NOT NULL CHECK "
        "(perturbed_confidence BETWEEN 0 AND 1), observed_at TIMESTAMPTZ NOT NULL, "
        "source_reference TEXT NOT NULL CHECK (btrim(source_reference)<>''), content_hash "
        "CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "PRIMARY KEY(report_id,scenario_id), UNIQUE(report_id,content_hash))"
    )
    op.execute(
        "CREATE TABLE model_degradation_observations ("
        "report_id UUID NOT NULL REFERENCES model_monitoring_reports(report_id), observation_id "
        "TEXT NOT NULL CHECK (btrim(observation_id)<>''), model_id UUID NOT NULL REFERENCES "
        "runtime_models(model_id), dimension TEXT NOT NULL CHECK (dimension IN "
        "('DATA','FEATURE','PREDICTION','CALIBRATION','PERFORMANCE','REGIME','EXECUTION','COST')), "
        "score NUMERIC(30,12) NOT NULL CHECK (score BETWEEN 0 AND 1), observed_at TIMESTAMPTZ "
        "NOT NULL, source_reference TEXT NOT NULL CHECK (btrim(source_reference)<>''), "
        "content_hash CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "PRIMARY KEY(report_id,observation_id), UNIQUE(report_id,dimension), "
        "UNIQUE(report_id,content_hash))"
    )
    for table in (
        "model_monitoring_policy_versions", "model_monitoring_reports",
        "model_explanation_sensitivity_scenarios", "model_degradation_observations",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_degradation_observations")
    op.execute("DROP TABLE IF EXISTS model_explanation_sensitivity_scenarios")
    op.execute("DROP TABLE IF EXISTS model_monitoring_reports")
    op.execute("DROP TABLE IF EXISTS model_monitoring_policy_versions")
    op.execute(
        "ALTER TABLE model_evaluation_reports DROP CONSTRAINT IF EXISTS "
        "model_evaluation_report_id_hash_unique"
    )
