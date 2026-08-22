"""add immutable retrieval-bound agent answer evaluation

Revision ID: 20260822_0032
Revises: 20260822_0031
Create Date: 2026-08-22
"""

from alembic import op

revision = "20260822_0032"
down_revision = "20260822_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE agent_answer_evaluation_policy_versions ("
        "policy_id UUID PRIMARY KEY, version TEXT NOT NULL UNIQUE CHECK (btrim(version)<>''), "
        "minimum_claim_support_rate NUMERIC(30,12) NOT NULL CHECK "
        "(minimum_claim_support_rate>0 AND minimum_claim_support_rate<=1), "
        "minimum_claim_token_overlap NUMERIC(30,12) NOT NULL CHECK "
        "(minimum_claim_token_overlap>0 AND minimum_claim_token_overlap<=1), "
        "minimum_citation_utilization NUMERIC(30,12) NOT NULL CHECK "
        "(minimum_citation_utilization>0 AND minimum_citation_utilization<=1), "
        "minimum_distinct_sources INTEGER NOT NULL CHECK (minimum_distinct_sources>=1), "
        "maximum_confidence NUMERIC(30,12) NOT NULL CHECK "
        "(maximum_confidence>0 AND maximum_confidence<=1), "
        "require_missing_data_when_retrieval_incomplete BOOLEAN NOT NULL, "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by)<>''), approved_at TIMESTAMPTZ NOT NULL, "
        "enabled BOOLEAN NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash ~ '^[0-9a-f]{64}$'), UNIQUE(policy_id,content_hash))"
    )
    op.execute(
        "CREATE TABLE agent_answer_evaluation_reports ("
        "report_id UUID PRIMARY KEY, policy_id UUID NOT NULL, policy_content_hash CHAR(64) NOT NULL "
        "CHECK (policy_content_hash ~ '^[0-9a-f]{64}$'), retrieval_report_id UUID NOT NULL "
        "REFERENCES research_retrieval_reports(report_id), output_id UUID NOT NULL, "
        "evaluated_at TIMESTAMPTZ NOT NULL, metrics JSONB NOT NULL CHECK (jsonb_typeof(metrics)='object'), "
        "outcome TEXT NOT NULL CHECK (outcome IN ('BLOCKED','REVIEW_ELIGIBLE')), reasons JSONB NOT NULL "
        "CHECK (jsonb_typeof(reasons)='array'), limitations JSONB NOT NULL CHECK "
        "(jsonb_typeof(limitations)='array'), content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash ~ '^[0-9a-f]{64}$'), FOREIGN KEY(policy_id,policy_content_hash) REFERENCES "
        "agent_answer_evaluation_policy_versions(policy_id,content_hash), "
        "UNIQUE(policy_id,retrieval_report_id,output_id))"
    )
    op.execute(
        "CREATE TABLE agent_answer_claim_evaluations ("
        "report_id UUID NOT NULL REFERENCES agent_answer_evaluation_reports(report_id), "
        "claim_kind TEXT NOT NULL CHECK (claim_kind IN ('FACT','INFERENCE')), "
        "claim_index INTEGER NOT NULL CHECK (claim_index>=0), claim_text TEXT NOT NULL CHECK "
        "(btrim(claim_text)<>''), source_references JSONB NOT NULL CHECK "
        "(jsonb_typeof(source_references)='array' AND jsonb_array_length(source_references)>0), "
        "token_overlap NUMERIC(30,12) NOT NULL CHECK (token_overlap BETWEEN 0 AND 1), "
        "supported BOOLEAN NOT NULL, PRIMARY KEY(report_id,claim_kind,claim_index))"
    )
    for table in (
        "agent_answer_evaluation_policy_versions", "agent_answer_evaluation_reports",
        "agent_answer_claim_evaluations",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_answer_claim_evaluations")
    op.execute("DROP TABLE IF EXISTS agent_answer_evaluation_reports")
    op.execute("DROP TABLE IF EXISTS agent_answer_evaluation_policy_versions")
