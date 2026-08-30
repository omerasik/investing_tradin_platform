"""add immutable point-in-time historical analogue evidence

Revision ID: 20260830_0036
Revises: 20260822_0035
Create Date: 2026-08-30
"""

from alembic import op

revision = "20260830_0036"
down_revision = "20260822_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE historical_analogue_policy_versions ("
        "policy_id UUID PRIMARY KEY, version TEXT NOT NULL UNIQUE CHECK (btrim(version)<>''), "
        "minimum_similarity NUMERIC(30,12) NOT NULL CHECK "
        "(minimum_similarity>0 AND minimum_similarity<=1), minimum_analogues INTEGER NOT NULL "
        "CHECK (minimum_analogues>0), maximum_analogues INTEGER NOT NULL CHECK "
        "(maximum_analogues>=minimum_analogues), minimum_distinct_regimes INTEGER NOT NULL "
        "CHECK (minimum_distinct_regimes>0 AND minimum_distinct_regimes<=minimum_analogues), "
        "minimum_distinct_source_families INTEGER NOT NULL CHECK "
        "(minimum_distinct_source_families>0 AND "
        "minimum_distinct_source_families<=minimum_analogues), "
        "maximum_probability_outcome_gap NUMERIC(30,12) NOT NULL CHECK "
        "(maximum_probability_outcome_gap>0 AND maximum_probability_outcome_gap<=1), "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by)<>''), approved_at TIMESTAMPTZ NOT NULL, "
        "enabled BOOLEAN NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash~'^[0-9a-f]{64}$'), UNIQUE(policy_id,content_hash))"
    )
    op.execute(
        "CREATE TABLE model_explanation_targets ("
        "target_id UUID PRIMARY KEY, model_id UUID NOT NULL REFERENCES runtime_models(model_id), "
        "dataset_version TEXT NOT NULL CHECK (btrim(dataset_version)<>''), feature_version TEXT "
        "NOT NULL CHECK (btrim(feature_version)<>''), instrument_id TEXT NOT NULL CHECK "
        "(btrim(instrument_id)<>''), observed_at TIMESTAMPTZ NOT NULL, available_at TIMESTAMPTZ "
        "NOT NULL, predicted_probability NUMERIC(30,18) NOT NULL CHECK "
        "(predicted_probability BETWEEN 0 AND 1), confidence NUMERIC(30,18) NOT NULL CHECK "
        "(confidence BETWEEN 0 AND 1), regime TEXT NOT NULL CHECK (btrim(regime)<>''), "
        "normalized_features JSONB NOT NULL CHECK (jsonb_typeof(normalized_features)='object' AND "
        "normalized_features<>'{}'::jsonb), source_reference TEXT NOT NULL CHECK "
        "(btrim(source_reference)<>''), content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash~'^[0-9a-f]{64}$'), UNIQUE(target_id,content_hash), "
        "CHECK (observed_at<=available_at))"
    )
    op.execute(
        "CREATE TABLE historical_analogue_candidates ("
        "analogue_id UUID PRIMARY KEY, model_id UUID NOT NULL REFERENCES runtime_models(model_id), "
        "dataset_version TEXT NOT NULL CHECK (btrim(dataset_version)<>''), feature_version TEXT "
        "NOT NULL CHECK (btrim(feature_version)<>''), instrument_id TEXT NOT NULL CHECK "
        "(btrim(instrument_id)<>''), regime TEXT NOT NULL CHECK (btrim(regime)<>''), "
        "observed_at TIMESTAMPTZ NOT NULL, available_at TIMESTAMPTZ NOT NULL, "
        "outcome_available_at TIMESTAMPTZ NOT NULL, normalized_features JSONB NOT NULL CHECK "
        "(jsonb_typeof(normalized_features)='object' AND normalized_features<>'{}'::jsonb), "
        "actual_outcome INTEGER NOT NULL CHECK (actual_outcome IN (0,1)), realized_return "
        "NUMERIC(30,18) NOT NULL, source_family TEXT NOT NULL CHECK (btrim(source_family)<>''), "
        "source_reference TEXT NOT NULL CHECK (btrim(source_reference)<>''), content_hash CHAR(64) "
        "NOT NULL UNIQUE CHECK (content_hash~'^[0-9a-f]{64}$'), UNIQUE(analogue_id,content_hash), "
        "CHECK (observed_at<=available_at AND available_at<=outcome_available_at))"
    )
    op.execute(
        "CREATE TABLE historical_analogue_reports ("
        "report_id UUID PRIMARY KEY, policy_id UUID NOT NULL, policy_content_hash CHAR(64) NOT NULL "
        "CHECK (policy_content_hash~'^[0-9a-f]{64}$'), evaluation_report_id UUID NOT NULL, "
        "evaluation_report_content_hash CHAR(64) NOT NULL CHECK "
        "(evaluation_report_content_hash~'^[0-9a-f]{64}$'), target_id UUID NOT NULL, "
        "target_content_hash CHAR(64) NOT NULL CHECK (target_content_hash~'^[0-9a-f]{64}$'), "
        "model_id UUID NOT NULL REFERENCES runtime_models(model_id), evaluated_at TIMESTAMPTZ NOT NULL, "
        "screened_count INTEGER NOT NULL CHECK (screened_count>=0), selected_count INTEGER NOT NULL "
        "CHECK (selected_count>=0 AND selected_count<=screened_count), distinct_regime_count INTEGER "
        "NOT NULL CHECK (distinct_regime_count>=0 AND distinct_regime_count<=selected_count), "
        "distinct_source_family_count INTEGER NOT NULL CHECK "
        "(distinct_source_family_count>=0 AND distinct_source_family_count<=selected_count), "
        "mean_similarity NUMERIC(30,12), weighted_outcome_frequency NUMERIC(30,12), "
        "weighted_realized_return NUMERIC(30,12), probability_outcome_gap NUMERIC(30,12), "
        "analogue_evidence_hash CHAR(64) NOT NULL CHECK (analogue_evidence_hash~'^[0-9a-f]{64}$'), "
        "outcome TEXT NOT NULL CHECK (outcome IN ('READY_FOR_REVIEW','DIVERGENCE_REVIEW_REQUIRED',"
        "'BLOCKED_INSUFFICIENT_EVIDENCE','BLOCKED_POLICY_DISABLED')), reasons JSONB NOT NULL CHECK "
        "(jsonb_typeof(reasons)='array' AND jsonb_array_length(reasons)>0), limitations JSONB NOT NULL "
        "CHECK (jsonb_typeof(limitations)='array' AND jsonb_array_length(limitations)>0), "
        "model_invocation_authority TEXT NOT NULL CHECK (model_invocation_authority='NONE'), "
        "prediction_authority TEXT NOT NULL CHECK (prediction_authority='NONE'), action_authority TEXT "
        "NOT NULL CHECK (action_authority='NONE'), content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash~'^[0-9a-f]{64}$'), FOREIGN KEY(policy_id,policy_content_hash) REFERENCES "
        "historical_analogue_policy_versions(policy_id,content_hash), FOREIGN KEY "
        "(evaluation_report_id,evaluation_report_content_hash) REFERENCES "
        "model_evaluation_reports(report_id,content_hash), FOREIGN KEY(target_id,target_content_hash) "
        "REFERENCES model_explanation_targets(target_id,content_hash), CHECK ((selected_count=0 AND "
        "mean_similarity IS NULL AND weighted_outcome_frequency IS NULL AND "
        "weighted_realized_return IS NULL AND probability_outcome_gap IS NULL) OR "
        "(selected_count>0 AND mean_similarity BETWEEN 0 AND 1 AND weighted_outcome_frequency "
        "BETWEEN 0 AND 1 AND weighted_realized_return IS NOT NULL AND probability_outcome_gap "
        "BETWEEN 0 AND 1)))"
    )
    op.execute(
        "CREATE TABLE historical_analogue_report_members ("
        "report_id UUID NOT NULL REFERENCES historical_analogue_reports(report_id), "
        "analogue_id UUID NOT NULL, analogue_content_hash CHAR(64) NOT NULL CHECK "
        "(analogue_content_hash~'^[0-9a-f]{64}$'), weighted_distance NUMERIC(30,12) NOT NULL CHECK "
        "(weighted_distance BETWEEN 0 AND 1), similarity NUMERIC(30,12) NOT NULL CHECK "
        "(similarity BETWEEN 0 AND 1 AND similarity=1-weighted_distance), selected BOOLEAN NOT NULL, "
        "selection_rank INTEGER, PRIMARY KEY(report_id,analogue_id), UNIQUE(report_id,selection_rank), "
        "FOREIGN KEY(analogue_id,analogue_content_hash) REFERENCES "
        "historical_analogue_candidates(analogue_id,content_hash), CHECK ((selected AND "
        "selection_rank>0) OR (NOT selected AND selection_rank IS NULL)))"
    )
    op.execute(
        "CREATE INDEX historical_analogue_reports_model_time_idx ON "
        "historical_analogue_reports(model_id,evaluated_at DESC,report_id DESC)"
    )
    for table in (
        "historical_analogue_policy_versions",
        "model_explanation_targets",
        "historical_analogue_candidates",
        "historical_analogue_reports",
        "historical_analogue_report_members",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS historical_analogue_report_members")
    op.execute("DROP TABLE IF EXISTS historical_analogue_reports")
    op.execute("DROP TABLE IF EXISTS historical_analogue_candidates")
    op.execute("DROP TABLE IF EXISTS model_explanation_targets")
    op.execute("DROP TABLE IF EXISTS historical_analogue_policy_versions")
