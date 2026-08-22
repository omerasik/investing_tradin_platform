"""add immutable model evaluation evidence

Revision ID: 20260822_0030
Revises: 20260822_0029
Create Date: 2026-08-22
"""

from alembic import op

revision = "20260822_0030"
down_revision = "20260822_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE model_evaluation_policy_versions ("
        "policy_id UUID PRIMARY KEY, policy_version TEXT NOT NULL UNIQUE "
        "CHECK (btrim(policy_version) <> ''), task TEXT NOT NULL CHECK (btrim(task) <> ''), "
        "candidate_complexity TEXT NOT NULL CHECK (candidate_complexity IN "
        "('RULE_BASED','LINEAR','REGULARIZED_LINEAR','TREE','GRADIENT_BOOSTING',"
        "'TIME_SERIES','NEURAL','ENSEMBLE','REINFORCEMENT_LEARNING')), "
        "predecessor_complexity TEXT NOT NULL CHECK (predecessor_complexity IN "
        "('NAIVE','RULE_BASED','LINEAR','REGULARIZED_LINEAR','TREE','GRADIENT_BOOSTING',"
        "'TIME_SERIES','NEURAL','ENSEMBLE')), "
        "CHECK ((candidate_complexity='RULE_BASED' AND predecessor_complexity='NAIVE') OR "
        "(candidate_complexity='LINEAR' AND predecessor_complexity='RULE_BASED') OR "
        "(candidate_complexity='REGULARIZED_LINEAR' AND predecessor_complexity='LINEAR') OR "
        "(candidate_complexity='TREE' AND predecessor_complexity='REGULARIZED_LINEAR') OR "
        "(candidate_complexity='GRADIENT_BOOSTING' AND predecessor_complexity='TREE') OR "
        "(candidate_complexity='TIME_SERIES' AND predecessor_complexity='GRADIENT_BOOSTING') OR "
        "(candidate_complexity='NEURAL' AND predecessor_complexity='TIME_SERIES') OR "
        "(candidate_complexity='ENSEMBLE' AND predecessor_complexity='NEURAL') OR "
        "(candidate_complexity='REINFORCEMENT_LEARNING' AND predecessor_complexity='ENSEMBLE')), "
        "minimum_holdout_observations INTEGER NOT NULL CHECK (minimum_holdout_observations >= 8), "
        "calibration_bins INTEGER NOT NULL CHECK (calibration_bins BETWEEN 2 AND 20), "
        "maximum_expected_calibration_error NUMERIC(30,12) NOT NULL CHECK "
        "(maximum_expected_calibration_error BETWEEN 0 AND 1), "
        "minimum_brier_improvement NUMERIC(30,12) NOT NULL CHECK (minimum_brier_improvement > 0), "
        "minimum_economic_improvement_after_cost NUMERIC(30,12) NOT NULL CHECK "
        "(minimum_economic_improvement_after_cost >= 0), "
        "maximum_half_brier_gap NUMERIC(30,12) NOT NULL CHECK "
        "(maximum_half_brier_gap BETWEEN 0 AND 1), "
        "decision_threshold NUMERIC(30,12) NOT NULL CHECK "
        "(decision_threshold > 0 AND decision_threshold < 1), "
        "assumed_cost_per_positive_decision NUMERIC(30,12) NOT NULL CHECK "
        "(assumed_cost_per_positive_decision >= 0), "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by) <> ''), approved_at TIMESTAMPTZ NOT NULL, "
        "enabled BOOLEAN NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE "
        "CHECK (content_hash ~ '^[0-9a-f]{64}$'))"
    )
    op.execute(
        "CREATE TABLE model_evaluation_reports ("
        "report_id UUID PRIMARY KEY, model_id UUID NOT NULL REFERENCES runtime_models(model_id), "
        "policy_id UUID NOT NULL REFERENCES model_evaluation_policy_versions(policy_id), "
        "dataset_version TEXT NOT NULL CHECK (btrim(dataset_version) <> ''), "
        "feature_version TEXT NOT NULL CHECK (btrim(feature_version) <> ''), "
        "training_end TIMESTAMPTZ NOT NULL, holdout_start TIMESTAMPTZ NOT NULL, "
        "holdout_end TIMESTAMPTZ NOT NULL, evaluated_at TIMESTAMPTZ NOT NULL, "
        "observation_count INTEGER NOT NULL CHECK (observation_count >= 8), "
        "observation_evidence_hash CHAR(64) NOT NULL CHECK "
        "(observation_evidence_hash ~ '^[0-9a-f]{64}$'), "
        "candidate_metrics JSONB NOT NULL CHECK (jsonb_typeof(candidate_metrics) = 'object'), "
        "predecessor_metrics JSONB NOT NULL CHECK (jsonb_typeof(predecessor_metrics) = 'object'), "
        "calibration JSONB NOT NULL CHECK (jsonb_typeof(calibration) = 'array'), "
        "feature_importance JSONB NOT NULL CHECK (jsonb_typeof(feature_importance) = 'object'), "
        "outcome TEXT NOT NULL CHECK (outcome IN ('BLOCKED','REVIEW_ELIGIBLE')), "
        "reasons JSONB NOT NULL CHECK (jsonb_typeof(reasons) = 'array'), "
        "limitations JSONB NOT NULL CHECK (jsonb_typeof(limitations) = 'array'), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "CHECK (training_end < holdout_start AND holdout_start < holdout_end "
        "AND holdout_end <= evaluated_at))"
    )
    op.execute(
        "CREATE INDEX model_evaluation_report_model_time_idx ON "
        "model_evaluation_reports(model_id, evaluated_at DESC, report_id DESC)"
    )
    op.execute(
        "CREATE TABLE model_evaluation_observations ("
        "report_id UUID NOT NULL REFERENCES model_evaluation_reports(report_id), "
        "observation_id TEXT NOT NULL CHECK (btrim(observation_id) <> ''), "
        "observed_at TIMESTAMPTZ NOT NULL, actual_outcome INTEGER NOT NULL "
        "CHECK (actual_outcome IN (0,1)), candidate_probability NUMERIC(30,18) NOT NULL "
        "CHECK (candidate_probability BETWEEN 0 AND 1), "
        "predecessor_probability NUMERIC(30,18) NOT NULL "
        "CHECK (predecessor_probability BETWEEN 0 AND 1), realized_return NUMERIC(30,18) NOT NULL, "
        "explanation_base_probability NUMERIC(30,18) NOT NULL "
        "CHECK (explanation_base_probability BETWEEN 0 AND 1), "
        "feature_contributions JSONB NOT NULL CHECK (jsonb_typeof(feature_contributions) = 'object' "
        "AND feature_contributions <> '{}'::jsonb), source_reference TEXT NOT NULL "
        "CHECK (btrim(source_reference) <> ''), content_hash CHAR(64) NOT NULL "
        "CHECK (content_hash ~ '^[0-9a-f]{64}$'), PRIMARY KEY(report_id, observation_id), "
        "UNIQUE(report_id, content_hash))"
    )
    for table in (
        "model_evaluation_policy_versions",
        "model_evaluation_reports",
        "model_evaluation_observations",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_evaluation_observations")
    op.execute("DROP TABLE IF EXISTS model_evaluation_reports")
    op.execute("DROP TABLE IF EXISTS model_evaluation_policy_versions")
