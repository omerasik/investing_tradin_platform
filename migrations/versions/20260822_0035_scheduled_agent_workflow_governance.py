"""add immutable scheduled agent workflow governance evidence

Revision ID: 20260822_0035
Revises: 20260822_0034
Create Date: 2026-08-22
"""

from alembic import op

revision = "20260822_0035"
down_revision = "20260822_0034"
branch_labels = None
depends_on = None


_PURPOSES = (
    "'NEWS_SUMMARIZATION','EVENT_EXTRACTION','ENTITY_LINKING','REPORT_GENERATION',"
    "'STRATEGY_EXPLANATION','RESEARCH_ASSISTANCE','NATURAL_LANGUAGE_QUERY',"
    "'LOG_INVESTIGATION','DOCUMENTATION','CODE_REVIEW_ASSISTANCE'"
)
_ROLES = (
    "'TECHNICAL','FUNDAMENTAL','MACRO','NEWS','SENTIMENT','BULL','BEAR',"
    "'RISK_CHALLENGE','PORTFOLIO_REVIEW','FINAL_SYNTHESIS'"
)
_PURPOSES_JSON = (
    "'[\"NEWS_SUMMARIZATION\",\"EVENT_EXTRACTION\",\"ENTITY_LINKING\","
    "\"REPORT_GENERATION\",\"STRATEGY_EXPLANATION\",\"RESEARCH_ASSISTANCE\","
    "\"NATURAL_LANGUAGE_QUERY\",\"LOG_INVESTIGATION\",\"DOCUMENTATION\","
    "\"CODE_REVIEW_ASSISTANCE\"]'::jsonb"
)
_ROLES_JSON = (
    "'[\"TECHNICAL\",\"FUNDAMENTAL\",\"MACRO\",\"NEWS\",\"SENTIMENT\","
    "\"BULL\",\"BEAR\",\"RISK_CHALLENGE\",\"PORTFOLIO_REVIEW\","
    "\"FINAL_SYNTHESIS\"]'::jsonb"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE research_retrieval_reports ADD CONSTRAINT "
        "research_retrieval_report_id_hash_unique UNIQUE (report_id,content_hash)"
    )
    op.execute(
        "ALTER TABLE agent_answer_evaluation_reports ADD CONSTRAINT "
        "agent_answer_evaluation_report_id_hash_unique UNIQUE (report_id,content_hash)"
    )
    op.execute(
        "CREATE TABLE agent_workflow_governance_policy_versions ("
        "policy_id UUID PRIMARY KEY, version TEXT NOT NULL UNIQUE CHECK (btrim(version)<>''), "
        "allowed_purposes JSONB NOT NULL CHECK (jsonb_typeof(allowed_purposes)='array' AND "
        "jsonb_array_length(allowed_purposes)>0 AND allowed_purposes <@ " + _PURPOSES_JSON + "), "
        "allowed_roles JSONB NOT NULL CHECK "
        "(jsonb_typeof(allowed_roles)='array' AND jsonb_array_length(allowed_roles)>0 "
        "AND allowed_roles <@ " + _ROLES_JSON + "), "
        "minimum_schedule_interval_seconds INTEGER NOT NULL CHECK (minimum_schedule_interval_seconds>0), "
        "maximum_workflows_per_run INTEGER NOT NULL CHECK (maximum_workflows_per_run>0), "
        "maximum_input_tokens_per_workflow INTEGER NOT NULL CHECK (maximum_input_tokens_per_workflow>0), "
        "maximum_output_tokens_per_workflow INTEGER NOT NULL CHECK (maximum_output_tokens_per_workflow>0), "
        "maximum_total_tokens_per_run INTEGER NOT NULL CHECK (maximum_total_tokens_per_run>0), "
        "maximum_estimated_cost_per_run NUMERIC(30,12) NOT NULL CHECK (maximum_estimated_cost_per_run>=0), "
        "cost_currency CHAR(3) NOT NULL CHECK (cost_currency=upper(cost_currency)), "
        "require_complete_retrieval BOOLEAN NOT NULL, require_review_eligible_answer BOOLEAN NOT NULL, "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by)<>''), approved_at TIMESTAMPTZ NOT NULL, "
        "enabled BOOLEAN NOT NULL, tool_authority TEXT NOT NULL CHECK (tool_authority='NONE'), "
        "model_invocation_authority TEXT NOT NULL CHECK (model_invocation_authority='NONE'), "
        "action_authority TEXT NOT NULL CHECK (action_authority='NONE'), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash~'^[0-9a-f]{64}$'), "
        "UNIQUE(policy_id,content_hash), CHECK (maximum_total_tokens_per_run >= "
        "maximum_input_tokens_per_workflow+maximum_output_tokens_per_workflow))"
    )
    op.execute(
        "CREATE TABLE agent_workflow_schedule_policy_versions ("
        "schedule_policy_id UUID PRIMARY KEY, schedule_name TEXT NOT NULL CHECK (btrim(schedule_name)<>''), "
        "version TEXT NOT NULL CHECK (btrim(version)<>''), governance_policy_id UUID NOT NULL, "
        "governance_policy_content_hash CHAR(64) NOT NULL, job_policy_id UUID NOT NULL, "
        "job_policy_content_hash CHAR(64) NOT NULL, allowed_purposes JSONB NOT NULL CHECK "
        "(jsonb_typeof(allowed_purposes)='array' AND jsonb_array_length(allowed_purposes)>0 "
        "AND allowed_purposes <@ " + _PURPOSES_JSON + "), "
        "allowed_roles JSONB NOT NULL CHECK (jsonb_typeof(allowed_roles)='array' AND "
        "jsonb_array_length(allowed_roles)>0 AND allowed_roles <@ " + _ROLES_JSON + "), "
        "effective_from TIMESTAMPTZ NOT NULL, "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by)<>''), approved_at TIMESTAMPTZ NOT NULL, "
        "enabled BOOLEAN NOT NULL, scheduler_authority TEXT NOT NULL CHECK (scheduler_authority='NONE'), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash~'^[0-9a-f]{64}$'), "
        "UNIQUE(schedule_name,version), UNIQUE(schedule_policy_id,content_hash), "
        "FOREIGN KEY (governance_policy_id,governance_policy_content_hash) REFERENCES "
        "agent_workflow_governance_policy_versions(policy_id,content_hash), "
        "FOREIGN KEY (job_policy_id,job_policy_content_hash) REFERENCES "
        "operational_job_policy_versions(policy_id,content_hash), CHECK (approved_at<=effective_from))"
    )
    op.execute(
        "CREATE TABLE scheduled_agent_workflow_assessments ("
        "assessment_id UUID PRIMARY KEY, schedule_policy_id UUID NOT NULL, "
        "schedule_policy_content_hash CHAR(64) NOT NULL, governance_policy_id UUID NOT NULL, "
        "governance_policy_content_hash CHAR(64) NOT NULL, job_run_id UUID NOT NULL, "
        "job_run_content_hash CHAR(64) NOT NULL, evaluated_at TIMESTAMPTZ NOT NULL, "
        "workflow_count INTEGER NOT NULL CHECK (workflow_count>=0), total_input_tokens INTEGER NOT NULL "
        "CHECK (total_input_tokens>=0), total_output_tokens INTEGER NOT NULL CHECK "
        "(total_output_tokens>=0), total_estimated_cost NUMERIC(30,12) NOT NULL CHECK "
        "(total_estimated_cost>=0), cost_currency CHAR(3) NOT NULL CHECK (cost_currency=upper(cost_currency)), "
        "candidate_evidence_hash CHAR(64) NOT NULL CHECK (candidate_evidence_hash~'^[0-9a-f]{64}$'), "
        "outcome TEXT NOT NULL CHECK (outcome IN ('READY_FOR_HUMAN_REVIEW','BLOCKED_POLICY_DISABLED',"
        "'BLOCKED_INCOMPLETE_EVIDENCE','BLOCKED_BUDGET')), reasons JSONB NOT NULL CHECK "
        "(jsonb_typeof(reasons)='array'), limitations JSONB NOT NULL CHECK (jsonb_typeof(limitations)='array'), "
        "approval_requirement TEXT NOT NULL CHECK (approval_requirement='EXPLICIT_HUMAN_REVIEW'), "
        "tool_authority TEXT NOT NULL CHECK (tool_authority='NONE'), model_invocation_authority TEXT NOT NULL "
        "CHECK (model_invocation_authority='NONE'), action_authority TEXT NOT NULL CHECK "
        "(action_authority='NONE'), content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash~'^[0-9a-f]{64}$'), FOREIGN KEY "
        "(schedule_policy_id,schedule_policy_content_hash) REFERENCES "
        "agent_workflow_schedule_policy_versions(schedule_policy_id,content_hash), FOREIGN KEY "
        "(governance_policy_id,governance_policy_content_hash) REFERENCES "
        "agent_workflow_governance_policy_versions(policy_id,content_hash), FOREIGN KEY "
        "(job_run_id,job_run_content_hash) REFERENCES operational_job_runs(run_id,content_hash))"
    )
    op.execute(
        "CREATE TABLE scheduled_agent_workflow_candidates ("
        "assessment_id UUID NOT NULL REFERENCES scheduled_agent_workflow_assessments(assessment_id), "
        "workflow_id UUID NOT NULL, role TEXT NOT NULL CHECK (role IN (" + _ROLES + ")), "
        "purpose TEXT NOT NULL CHECK (purpose IN (" + _PURPOSES + ")), "
        "retrieval_report_id UUID NOT NULL, retrieval_report_content_hash CHAR(64) NOT NULL, "
        "answer_evaluation_report_id UUID NOT NULL, answer_evaluation_report_content_hash CHAR(64) NOT NULL, "
        "estimated_input_tokens INTEGER NOT NULL CHECK (estimated_input_tokens>0), "
        "estimated_output_tokens INTEGER NOT NULL CHECK (estimated_output_tokens>0), "
        "estimated_cost NUMERIC(30,12) NOT NULL CHECK (estimated_cost>=0), candidate_hash CHAR(64) NOT NULL "
        "CHECK (candidate_hash~'^[0-9a-f]{64}$'), PRIMARY KEY(assessment_id,workflow_id,role), "
        "FOREIGN KEY (retrieval_report_id,retrieval_report_content_hash) REFERENCES "
        "research_retrieval_reports(report_id,content_hash), FOREIGN KEY "
        "(answer_evaluation_report_id,answer_evaluation_report_content_hash) REFERENCES "
        "agent_answer_evaluation_reports(report_id,content_hash))"
    )
    for table in (
        "agent_workflow_governance_policy_versions",
        "agent_workflow_schedule_policy_versions",
        "scheduled_agent_workflow_assessments",
        "scheduled_agent_workflow_candidates",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scheduled_agent_workflow_candidates")
    op.execute("DROP TABLE IF EXISTS scheduled_agent_workflow_assessments")
    op.execute("DROP TABLE IF EXISTS agent_workflow_schedule_policy_versions")
    op.execute("DROP TABLE IF EXISTS agent_workflow_governance_policy_versions")
    op.execute("ALTER TABLE agent_answer_evaluation_reports DROP CONSTRAINT IF EXISTS agent_answer_evaluation_report_id_hash_unique")
    op.execute("ALTER TABLE research_retrieval_reports DROP CONSTRAINT IF EXISTS research_retrieval_report_id_hash_unique")
