"""add immutable internal research retrieval evidence

Revision ID: 20260822_0031
Revises: 20260822_0030
Create Date: 2026-08-22
"""

from alembic import op

revision = "20260822_0031"
down_revision = "20260822_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE research_retrieval_policy_versions ("
        "policy_id UUID PRIMARY KEY, version TEXT NOT NULL UNIQUE CHECK (btrim(version)<>''), "
        "allowed_source_kinds JSONB NOT NULL CHECK (jsonb_typeof(allowed_source_kinds)='array' "
        "AND jsonb_array_length(allowed_source_kinds)>0), minimum_results INTEGER NOT NULL CHECK "
        "(minimum_results>=1), minimum_distinct_sources INTEGER NOT NULL CHECK "
        "(minimum_distinct_sources>=1 AND minimum_distinct_sources<=minimum_results), "
        "maximum_results INTEGER NOT NULL CHECK (maximum_results>=minimum_results AND maximum_results<=100), "
        "minimum_query_term_coverage NUMERIC(30,12) NOT NULL CHECK "
        "(minimum_query_term_coverage>0 AND minimum_query_term_coverage<=1), "
        "approved_by TEXT NOT NULL CHECK (btrim(approved_by)<>''), approved_at TIMESTAMPTZ NOT NULL, "
        "enabled BOOLEAN NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash ~ '^[0-9a-f]{64}$'))"
    )
    op.execute(
        "CREATE TABLE internal_research_evidence_chunks ("
        "chunk_id UUID PRIMARY KEY, source_document_id TEXT NOT NULL CHECK "
        "(btrim(source_document_id)<>''), source_version TEXT NOT NULL CHECK "
        "(btrim(source_version)<>''), source_kind TEXT NOT NULL CHECK (source_kind IN "
        "('INTERNAL_FILING','INTERNAL_MARKET_DATA','INTERNAL_RISK','INTERNAL_EVENT',"
        "'INTERNAL_RESEARCH')), instrument_id TEXT NOT NULL CHECK (btrim(instrument_id)<>''), "
        "title TEXT NOT NULL CHECK (btrim(title)<>''), text TEXT NOT NULL CHECK (btrim(text)<>''), "
        "observed_at TIMESTAMPTZ NOT NULL, available_at TIMESTAMPTZ NOT NULL, "
        "invalidated_at TIMESTAMPTZ, allowed_roles JSONB NOT NULL CHECK "
        "(jsonb_typeof(allowed_roles)='array' AND jsonb_array_length(allowed_roles)>0), "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'), "
        "CHECK (observed_at<=available_at AND (invalidated_at IS NULL OR invalidated_at>available_at)), "
        "UNIQUE(source_document_id,source_version,chunk_id), UNIQUE(chunk_id,content_hash))"
    )
    op.execute(
        "CREATE TABLE research_retrieval_reports ("
        "report_id UUID PRIMARY KEY, request_id UUID NOT NULL UNIQUE, workflow_id UUID NOT NULL, "
        "policy_id UUID NOT NULL REFERENCES research_retrieval_policy_versions(policy_id), "
        "instrument_id TEXT NOT NULL CHECK (btrim(instrument_id)<>''), role TEXT NOT NULL CHECK "
        "(btrim(role)<>''), query_text TEXT NOT NULL CHECK (btrim(query_text)<>''), "
        "requested_at TIMESTAMPTZ NOT NULL, query_terms JSONB NOT NULL CHECK "
        "(jsonb_typeof(query_terms)='array' AND jsonb_array_length(query_terms)>0), "
        "query_term_coverage NUMERIC(30,12) NOT NULL CHECK (query_term_coverage BETWEEN 0 AND 1), "
        "outcome TEXT NOT NULL CHECK (outcome IN ('COMPLETE','INSUFFICIENT_EVIDENCE')), "
        "reasons JSONB NOT NULL CHECK (jsonb_typeof(reasons)='array'), limitations JSONB NOT NULL "
        "CHECK (jsonb_typeof(limitations)='array'), content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash ~ '^[0-9a-f]{64}$'))"
    )
    op.execute(
        "CREATE TABLE research_retrieval_results ("
        "report_id UUID NOT NULL REFERENCES research_retrieval_reports(report_id), "
        "rank INTEGER NOT NULL CHECK (rank>=1), chunk_id UUID NOT NULL REFERENCES "
        "internal_research_evidence_chunks(chunk_id), lexical_score NUMERIC(30,18) NOT NULL CHECK "
        "(lexical_score>0 AND lexical_score<=1), matched_terms JSONB NOT NULL CHECK "
        "(jsonb_typeof(matched_terms)='array' AND jsonb_array_length(matched_terms)>0), "
        "excerpt TEXT NOT NULL CHECK (btrim(excerpt)<>''), chunk_content_hash CHAR(64) NOT NULL CHECK "
        "(chunk_content_hash ~ '^[0-9a-f]{64}$'), FOREIGN KEY (chunk_id,chunk_content_hash) "
        "REFERENCES internal_research_evidence_chunks(chunk_id,content_hash), PRIMARY KEY(report_id,rank), "
        "UNIQUE(report_id,chunk_id))"
    )
    for table in (
        "research_retrieval_policy_versions", "internal_research_evidence_chunks",
        "research_retrieval_reports", "research_retrieval_results",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS research_retrieval_results")
    op.execute("DROP TABLE IF EXISTS research_retrieval_reports")
    op.execute("DROP TABLE IF EXISTS internal_research_evidence_chunks")
    op.execute("DROP TABLE IF EXISTS research_retrieval_policy_versions")
