"""add immutable governance reporting and operational cost evidence

Revision ID: 20260822_0034
Revises: 20260822_0033
Create Date: 2026-08-22
"""

from alembic import op

revision = "20260822_0034"
down_revision = "20260822_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE operational_job_policy_versions ADD CONSTRAINT "
        "operational_job_policy_id_hash_unique UNIQUE (policy_id,content_hash)"
    )
    op.execute(
        "ALTER TABLE operational_job_runs ADD CONSTRAINT "
        "operational_job_run_id_hash_unique UNIQUE (run_id,content_hash)"
    )
    op.execute(
        "CREATE TABLE report_schedule_policy_versions ("
        "policy_id UUID PRIMARY KEY, policy_name TEXT NOT NULL CHECK (btrim(policy_name)<>''), "
        "version TEXT NOT NULL CHECK (btrim(version)<>''), report_type TEXT NOT NULL CHECK (report_type IN "
        "('DAILY_MARKET','DAILY_RISK','DAILY_EXECUTION','DAILY_DATA_HEALTH','WEEKLY_STRATEGY',"
        "'WEEKLY_MODEL_DRIFT','WEEKLY_PORTFOLIO','MONTHLY_INVESTMENT_REVIEW',"
        "'MONTHLY_STRATEGY_ATTRIBUTION','MONTHLY_COST','MONTHLY_INCIDENT',"
        "'QUARTERLY_MODEL_GOVERNANCE','LIVE_READINESS')), cadence TEXT NOT NULL CHECK (cadence IN "
        "('DAILY','WEEKLY','MONTHLY','QUARTERLY','ON_DEMAND')), job_policy_id UUID NOT NULL, "
        "job_policy_content_hash CHAR(64) NOT NULL, approved_by TEXT NOT NULL CHECK (btrim(approved_by)<>''), "
        "approved_at TIMESTAMPTZ NOT NULL, enabled BOOLEAN NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE "
        "CHECK (content_hash~'^[0-9a-f]{64}$'), UNIQUE(policy_name,version), UNIQUE(policy_id,content_hash), "
        "FOREIGN KEY (job_policy_id,job_policy_content_hash) REFERENCES "
        "operational_job_policy_versions(policy_id,content_hash), CHECK ((report_type LIKE 'DAILY_%' AND cadence='DAILY') "
        "OR (report_type LIKE 'WEEKLY_%' AND cadence='WEEKLY') OR (report_type LIKE 'MONTHLY_%' AND cadence='MONTHLY') "
        "OR (report_type='QUARTERLY_MODEL_GOVERNANCE' AND cadence='QUARTERLY') "
        "OR (report_type='LIVE_READINESS' AND cadence='ON_DEMAND')))"
    )
    op.execute(
        "CREATE TABLE cost_budget_policy_versions ("
        "policy_id UUID PRIMARY KEY, policy_name TEXT NOT NULL CHECK (btrim(policy_name)<>''), "
        "version TEXT NOT NULL CHECK (btrim(version)<>''), budget_mode TEXT NOT NULL CHECK (budget_mode IN "
        "('LOCAL_RESEARCH','LOW_COST_PAPER','PROFESSIONAL_PAPER','LIMITED_LIVE','SCALED_LIVE')), "
        "currency CHAR(3) NOT NULL CHECK (currency=upper(currency)), period_start TIMESTAMPTZ NOT NULL, "
        "period_end TIMESTAMPTZ NOT NULL, total_limit NUMERIC(30,12) NOT NULL CHECK (total_limit>=0), "
        "category_limits JSONB NOT NULL CHECK (jsonb_typeof(category_limits)='object' AND "
        "category_limits ?& ARRAY['DATA_PROVIDER','NEWS_PROVIDER',"
        "'SOCIAL_PROVIDER','CLOUD_COMPUTE','STORAGE','DATABASE','STREAMING','AI_INFERENCE','BROKER_FEES',"
        "'EXCHANGE_FEES','MONITORING','BACKUP'] AND (category_limits - ARRAY['DATA_PROVIDER','NEWS_PROVIDER',"
        "'SOCIAL_PROVIDER','CLOUD_COMPUTE','STORAGE','DATABASE','STREAMING','AI_INFERENCE','BROKER_FEES',"
        "'EXCHANGE_FEES','MONITORING','BACKUP'])='{}'::jsonb), "
        "minimum_value_to_cost_ratio NUMERIC(30,12) NOT NULL CHECK (minimum_value_to_cost_ratio>=1), "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by)<>''), approved_at TIMESTAMPTZ NOT NULL, "
        "enabled BOOLEAN NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash~'^[0-9a-f]{64}$'), "
        "UNIQUE(policy_name,version), UNIQUE(policy_id,content_hash), CHECK (period_end>period_start), "
        "CHECK (approved_at<=period_start))"
    )
    op.execute(
        "CREATE TABLE operational_cost_observations ("
        "observation_id UUID PRIMARY KEY, category TEXT NOT NULL CHECK (category IN "
        "('DATA_PROVIDER','NEWS_PROVIDER','SOCIAL_PROVIDER','CLOUD_COMPUTE','STORAGE','DATABASE',"
        "'STREAMING','AI_INFERENCE','BROKER_FEES','EXCHANGE_FEES','MONITORING','BACKUP')), "
        "service_reference TEXT NOT NULL CHECK (btrim(service_reference)<>''), "
        "amount NUMERIC(30,12) NOT NULL CHECK (amount>=0), currency CHAR(3) NOT NULL CHECK (currency=upper(currency)), "
        "period_start TIMESTAMPTZ NOT NULL, period_end TIMESTAMPTZ NOT NULL, observed_at TIMESTAMPTZ NOT NULL, "
        "evidence_class TEXT NOT NULL CHECK (evidence_class IN ('FACT','MODEL_ESTIMATE')), "
        "evidence_reference TEXT NOT NULL CHECK (btrim(evidence_reference)<>''), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash~'^[0-9a-f]{64}$'), "
        "UNIQUE(observation_id,content_hash), CHECK (period_end>period_start), "
        "CHECK (observed_at>=period_start AND observed_at<=period_end))"
    )
    op.execute(
        "CREATE TABLE cost_value_assessments ("
        "assessment_id UUID PRIMARY KEY, budget_policy_id UUID NOT NULL, "
        "budget_policy_content_hash CHAR(64) NOT NULL, candidate_type TEXT NOT NULL CHECK "
        "(candidate_type IN ('DATASET','MODEL')), candidate_reference TEXT NOT NULL CHECK "
        "(btrim(candidate_reference)<>''), evaluated_at TIMESTAMPTZ NOT NULL, "
        "incremental_cost NUMERIC(30,12) NOT NULL CHECK (incremental_cost>=0), "
        "measurable_value_estimate NUMERIC(30,12) NOT NULL CHECK (measurable_value_estimate>=0), "
        "currency CHAR(3) NOT NULL CHECK (currency=upper(currency)), value_to_cost_ratio NUMERIC(30,12), "
        "evidence_references JSONB NOT NULL CHECK (jsonb_typeof(evidence_references)='array'), "
        "deterministic_alternative_available BOOLEAN NOT NULL, proposed_ai_inference BOOLEAN NOT NULL, "
        "outcome TEXT NOT NULL CHECK (outcome IN ('JUSTIFIED_FOR_REVIEW','NOT_JUSTIFIED_REVIEW_REQUIRED',"
        "'BLOCKED_POLICY_DISABLED')), reasons JSONB NOT NULL CHECK (jsonb_typeof(reasons)='array'), "
        "limitations JSONB NOT NULL CHECK (jsonb_typeof(limitations)='array'), "
        "procurement_authority TEXT NOT NULL CHECK (procurement_authority='NONE'), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash~'^[0-9a-f]{64}$'), "
        "FOREIGN KEY (budget_policy_id,budget_policy_content_hash) REFERENCES "
        "cost_budget_policy_versions(policy_id,content_hash), CHECK ((incremental_cost=0 AND "
        "value_to_cost_ratio IS NULL) OR (incremental_cost>0 AND value_to_cost_ratio>=0)))"
    )
    op.execute(
        "CREATE TABLE governance_reports ("
        "report_id UUID PRIMARY KEY, schedule_policy_id UUID NOT NULL, schedule_policy_content_hash CHAR(64) NOT NULL, "
        "job_run_id UUID NOT NULL, job_run_content_hash CHAR(64) NOT NULL, report_type TEXT NOT NULL CHECK (report_type IN "
        "('DAILY_MARKET','DAILY_RISK','DAILY_EXECUTION','DAILY_DATA_HEALTH','WEEKLY_STRATEGY',"
        "'WEEKLY_MODEL_DRIFT','WEEKLY_PORTFOLIO','MONTHLY_INVESTMENT_REVIEW',"
        "'MONTHLY_STRATEGY_ATTRIBUTION','MONTHLY_COST','MONTHLY_INCIDENT',"
        "'QUARTERLY_MODEL_GOVERNANCE','LIVE_READINESS')), period_start TIMESTAMPTZ NOT NULL, "
        "period_end TIMESTAMPTZ NOT NULL, generated_at TIMESTAMPTZ NOT NULL, budget_policy_id UUID, "
        "budget_policy_content_hash CHAR(64), total_observed NUMERIC(30,12), total_limit NUMERIC(30,12), "
        "section_evidence_hash CHAR(64) NOT NULL CHECK (section_evidence_hash~'^[0-9a-f]{64}$'), "
        "cost_evidence_hash CHAR(64) NOT NULL CHECK (cost_evidence_hash~'^[0-9a-f]{64}$'), "
        "outcome TEXT NOT NULL CHECK (outcome IN ('READY_FOR_REVIEW','BUDGET_BREACH_REVIEW_REQUIRED',"
        "'BLOCKED_INCOMPLETE_EVIDENCE')), reasons JSONB NOT NULL CHECK (jsonb_typeof(reasons)='array'), "
        "limitations JSONB NOT NULL CHECK (jsonb_typeof(limitations)='array'), "
        "execution_authority TEXT NOT NULL CHECK (execution_authority='NONE'), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash~'^[0-9a-f]{64}$'), "
        "FOREIGN KEY (schedule_policy_id,schedule_policy_content_hash) REFERENCES "
        "report_schedule_policy_versions(policy_id,content_hash), "
        "FOREIGN KEY (job_run_id,job_run_content_hash) REFERENCES operational_job_runs(run_id,content_hash), "
        "FOREIGN KEY (budget_policy_id,budget_policy_content_hash) REFERENCES "
        "cost_budget_policy_versions(policy_id,content_hash) MATCH FULL, CHECK (period_end>period_start), "
        "CHECK (generated_at>=period_end), CHECK ((report_type='MONTHLY_COST' AND budget_policy_id IS NOT NULL "
        "AND total_observed IS NOT NULL AND total_limit IS NOT NULL) OR (report_type<>'MONTHLY_COST' "
        "AND budget_policy_id IS NULL AND total_observed IS NULL AND total_limit IS NULL)))"
    )
    op.execute(
        "CREATE TABLE governance_report_sections ("
        "report_id UUID NOT NULL REFERENCES governance_reports(report_id), evidence_class TEXT NOT NULL CHECK "
        "(evidence_class IN ('FACT','MODEL_ESTIMATE','INFERENCE','UNVERIFIED_INFORMATION','MISSING_DATA')), "
        "entries JSONB NOT NULL CHECK (jsonb_typeof(entries)='array'), content_hash CHAR(64) NOT NULL "
        "CHECK (content_hash~'^[0-9a-f]{64}$'), PRIMARY KEY(report_id,evidence_class))"
    )
    op.execute(
        "CREATE TABLE governance_report_cost_observations ("
        "report_id UUID NOT NULL REFERENCES governance_reports(report_id), observation_id UUID NOT NULL REFERENCES "
        "operational_cost_observations(observation_id), PRIMARY KEY(report_id,observation_id))"
    )
    for table in (
        "report_schedule_policy_versions",
        "cost_budget_policy_versions",
        "operational_cost_observations",
        "cost_value_assessments",
        "governance_reports",
        "governance_report_sections",
        "governance_report_cost_observations",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS governance_report_cost_observations")
    op.execute("DROP TABLE IF EXISTS governance_report_sections")
    op.execute("DROP TABLE IF EXISTS governance_reports")
    op.execute("DROP TABLE IF EXISTS cost_value_assessments")
    op.execute("DROP TABLE IF EXISTS operational_cost_observations")
    op.execute("DROP TABLE IF EXISTS cost_budget_policy_versions")
    op.execute("DROP TABLE IF EXISTS report_schedule_policy_versions")
    op.execute("ALTER TABLE operational_job_runs DROP CONSTRAINT IF EXISTS operational_job_run_id_hash_unique")
    op.execute(
        "ALTER TABLE operational_job_policy_versions DROP CONSTRAINT IF EXISTS operational_job_policy_id_hash_unique"
    )
