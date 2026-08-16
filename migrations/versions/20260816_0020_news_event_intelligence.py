"""add correction-aware news and event intelligence authorities

Revision ID: 20260816_0020
Revises: 20260816_0019
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260816_0020"
down_revision = "20260816_0019"
branch_labels = None
depends_on = None


TABLES = (
    "news_source_policy_versions",
    "news_document_revisions",
    "news_document_entity_links",
    "news_event_extractions",
    "news_event_clusters",
    "news_event_cluster_members",
    "news_event_lineage",
    "news_event_assessments",
    "news_event_assessment_data_health",
    "news_event_research_candidates",
)


def upgrade() -> None:
    statements = (
        """CREATE TABLE news_source_policy_versions (
            source_policy_version_id UUID PRIMARY KEY, source_id UUID NOT NULL,
            provider TEXT NOT NULL, version TEXT NOT NULL, terms_version TEXT NOT NULL,
            authorization_reference TEXT NOT NULL,
            rights_status TEXT NOT NULL CHECK(rights_status IN ('APPROVED','NOT_APPROVED')),
            raw_storage_allowed BOOLEAN NOT NULL, derived_use_allowed BOOLEAN NOT NULL,
            permitted_languages JSONB NOT NULL CHECK(jsonb_typeof(permitted_languages)='array'),
            reliability NUMERIC(18,12) NOT NULL CHECK(reliability BETWEEN 0 AND 1),
            status TEXT NOT NULL CHECK(status='CONFIGURATION_ONLY'),
            provider_activated BOOLEAN NOT NULL DEFAULT FALSE CHECK(provider_activated=FALSE),
            created_at TIMESTAMPTZ NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(source_id,version)
        )""",
        """CREATE TABLE news_document_revisions (
            document_revision_id UUID PRIMARY KEY,
            source_policy_version_id UUID NOT NULL REFERENCES news_source_policy_versions(source_policy_version_id),
            source_item_id TEXT NOT NULL, root_source_item_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision>=0),
            revision_kind TEXT NOT NULL CHECK(revision_kind IN ('INITIAL','CORRECTION','RETRACTION','FOLLOW_UP')),
            supersedes_document_revision_id UUID REFERENCES news_document_revisions(document_revision_id),
            source_url TEXT NOT NULL, language TEXT NOT NULL, headline TEXT NOT NULL,
            published_at TIMESTAMPTZ NOT NULL, source_updated_at TIMESTAMPTZ NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL,
            content_fingerprint CHAR(64) NOT NULL, raw_payload_sha256 CHAR(64) NOT NULL,
            officiality TEXT NOT NULL CHECK(officiality IN ('OFFICIAL','REPORTED','RUMOR')),
            sanitization_flags JSONB NOT NULL CHECK(jsonb_typeof(sanitization_flags)='array'),
            payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
            content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(source_policy_version_id,source_item_id,revision),
            CHECK(source_updated_at>=published_at), CHECK(ingested_at>=source_updated_at),
            CHECK((revision=0 AND revision_kind='INITIAL' AND supersedes_document_revision_id IS NULL)
               OR (revision>0 AND revision_kind<>'INITIAL' AND supersedes_document_revision_id IS NOT NULL))
        )""",
        "CREATE INDEX news_document_pit_idx ON news_document_revisions(root_source_item_id,published_at,ingested_at,revision)",
        """CREATE TABLE news_document_entity_links (
            entity_link_id UUID PRIMARY KEY,
            document_revision_id UUID NOT NULL REFERENCES news_document_revisions(document_revision_id),
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            link_method TEXT NOT NULL CHECK(link_method IN ('PROVIDER_IDENTIFIER','IDENTIFIER_MAPPING','DETERMINISTIC_ALIAS','REVIEWED')),
            confidence NUMERIC(18,12) NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            ambiguous BOOLEAN NOT NULL, evidence JSONB NOT NULL CHECK(jsonb_typeof(evidence)='object'),
            content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(document_revision_id,instrument_id)
        )""",
        """CREATE TABLE news_event_extractions (
            extraction_id UUID PRIMARY KEY,
            document_revision_id UUID NOT NULL REFERENCES news_document_revisions(document_revision_id),
            event_type TEXT NOT NULL,
            taxonomy_version TEXT NOT NULL, model_version TEXT NOT NULL,
            novelty NUMERIC(18,12) NOT NULL CHECK(novelty BETWEEN 0 AND 1),
            urgency NUMERIC(18,12) NOT NULL CHECK(urgency BETWEEN 0 AND 1),
            uncertainty NUMERIC(18,12) NOT NULL CHECK(uncertainty BETWEEN 0 AND 1),
            horizon TEXT NOT NULL CHECK(horizon IN ('INTRADAY','DAYS','WEEKS','LONG_TERM')),
            extracted_at TIMESTAMPTZ NOT NULL,
            facts JSONB NOT NULL CHECK(jsonb_typeof(facts)='array'),
            limitations JSONB NOT NULL CHECK(jsonb_typeof(limitations)='array'),
            content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(document_revision_id,taxonomy_version,model_version)
        )""",
        """CREATE TABLE news_event_clusters (
            cluster_id UUID PRIMARY KEY, cluster_key CHAR(64) NOT NULL,
            canonical_document_revision_id UUID NOT NULL REFERENCES news_document_revisions(document_revision_id),
            created_at TIMESTAMPTZ NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        """CREATE TABLE news_event_cluster_members (
            cluster_id UUID NOT NULL REFERENCES news_event_clusters(cluster_id),
            document_revision_id UUID NOT NULL REFERENCES news_document_revisions(document_revision_id),
            duplicate_kind TEXT NOT NULL CHECK(duplicate_kind IN ('CANONICAL','EXACT_CONTENT','NORMALIZED_HEADLINE','FOLLOW_UP')),
            PRIMARY KEY(cluster_id,document_revision_id)
        )""",
        """CREATE TABLE news_event_lineage (
            predecessor_document_revision_id UUID NOT NULL REFERENCES news_document_revisions(document_revision_id),
            successor_document_revision_id UUID NOT NULL REFERENCES news_document_revisions(document_revision_id),
            relation TEXT NOT NULL CHECK(relation IN ('CORRECTS','RETRACTS','FOLLOWS_UP')),
            PRIMARY KEY(predecessor_document_revision_id,successor_document_revision_id)
        )""",
        """CREATE TABLE news_event_assessments (
            assessment_id UUID PRIMARY KEY,
            document_revision_id UUID NOT NULL REFERENCES news_document_revisions(document_revision_id),
            extraction_id UUID NOT NULL REFERENCES news_event_extractions(extraction_id),
            entity_link_id UUID REFERENCES news_document_entity_links(entity_link_id),
            assessed_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('BLOCKED','REVIEW_REQUIRED','WITHDRAWN')),
            credibility NUMERIC(18,12) NOT NULL CHECK(credibility BETWEEN 0 AND 1),
            reasons JSONB NOT NULL CHECK(jsonb_typeof(reasons)='array'),
            report JSONB NOT NULL CHECK(jsonb_typeof(report)='object'),
            content_hash CHAR(64) NOT NULL UNIQUE
        )""",
        """CREATE TABLE news_event_assessment_data_health (
            assessment_id UUID NOT NULL REFERENCES news_event_assessments(assessment_id),
            data_health_assessment_id UUID NOT NULL REFERENCES data_health_assessments(assessment_id),
            PRIMARY KEY(assessment_id,data_health_assessment_id)
        )""",
        """CREATE TABLE news_event_research_candidates (
            candidate_id UUID PRIMARY KEY,
            assessment_id UUID NOT NULL UNIQUE REFERENCES news_event_assessments(assessment_id),
            instrument_id TEXT REFERENCES professional_instruments(instrument_id),
            strategy_version_id UUID REFERENCES strategy_versions(strategy_version_id),
            thesis_id UUID REFERENCES investment_theses(thesis_id),
            confidence_action TEXT NOT NULL CHECK(confidence_action IN ('MAINTAIN','REDUCE','WITHDRAW')),
            maximum_confidence NUMERIC(18,12) NOT NULL CHECK(maximum_confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL CHECK(status IN ('BLOCKED','REVIEW_REQUIRED','WITHDRAWN')),
            research_only BOOLEAN NOT NULL DEFAULT TRUE CHECK(research_only=TRUE),
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
