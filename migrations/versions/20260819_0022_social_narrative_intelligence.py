"""add rights-gated social and narrative intelligence authorities

Revision ID: 20260819_0022
Revises: 20260816_0021
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260819_0022"
down_revision = "20260816_0021"
branch_labels = None
depends_on = None


TABLES = (
    "social_source_policy_versions",
    "social_content_observations",
    "social_narrative_clusters",
    "social_narrative_cluster_members",
    "social_narrative_metric_windows",
    "social_narrative_window_data_health",
)


def upgrade() -> None:
    statements = (
        """CREATE TABLE social_source_policy_versions (
            source_policy_version_id UUID PRIMARY KEY, source_id UUID NOT NULL,
            provider TEXT NOT NULL, version TEXT NOT NULL, terms_version TEXT NOT NULL,
            authorization_reference TEXT NOT NULL,
            rights_status TEXT NOT NULL CHECK(rights_status IN ('APPROVED','NOT_APPROVED')),
            raw_storage_allowed BOOLEAN NOT NULL, derived_use_allowed BOOLEAN NOT NULL,
            permitted_classes JSONB NOT NULL CHECK(jsonb_typeof(permitted_classes)='array'),
            permitted_languages JSONB NOT NULL CHECK(jsonb_typeof(permitted_languages)='array'),
            geographic_processing_allowed BOOLEAN NOT NULL,
            status TEXT NOT NULL CHECK(status='CONFIGURATION_ONLY'),
            provider_activated BOOLEAN NOT NULL DEFAULT FALSE CHECK(provider_activated=FALSE),
            created_at TIMESTAMPTZ NOT NULL, content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(source_id,version),
            CHECK(rights_status='APPROVED' OR
                  (raw_storage_allowed=FALSE AND derived_use_allowed=FALSE))
        )""",
        """CREATE TABLE social_content_observations (
            observation_id UUID PRIMARY KEY,
            source_policy_version_id UUID NOT NULL
                REFERENCES social_source_policy_versions(source_policy_version_id),
            source_item_id TEXT NOT NULL,
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            discussion_class TEXT NOT NULL CHECK(discussion_class IN
                ('RETAIL_SENTIMENT','PROFESSIONAL_COMMENTARY','NEWS_AMPLIFICATION',
                 'AUTOMATED_SPAM','PROMOTIONAL_ACTIVITY','ORGANIC_DISCUSSION')),
            author_identifier_hash CHAR(64) NOT NULL, language TEXT NOT NULL,
            topic_key CHAR(64) NOT NULL, topic_label TEXT NOT NULL, text_excerpt TEXT,
            published_at TIMESTAMPTZ NOT NULL, ingested_at TIMESTAMPTZ NOT NULL,
            sentiment NUMERIC(18,12) NOT NULL CHECK(sentiment BETWEEN -1 AND 1),
            emotional_intensity NUMERIC(18,12) NOT NULL
                CHECK(emotional_intensity BETWEEN 0 AND 1),
            bot_likelihood NUMERIC(18,12) NOT NULL CHECK(bot_likelihood BETWEEN 0 AND 1),
            spam_likelihood NUMERIC(18,12) NOT NULL CHECK(spam_likelihood BETWEEN 0 AND 1),
            coordinated_promotion_likelihood NUMERIC(18,12) NOT NULL
                CHECK(coordinated_promotion_likelihood BETWEEN 0 AND 1),
            influencer_score NUMERIC(18,12) NOT NULL CHECK(influencer_score BETWEEN 0 AND 1),
            geography_bucket TEXT, content_fingerprint CHAR(64) NOT NULL,
            raw_payload_sha256 CHAR(64) NOT NULL,
            sanitization_flags JSONB NOT NULL CHECK(jsonb_typeof(sanitization_flags)='array'),
            evidence_label TEXT NOT NULL
                CHECK(evidence_label='SYNTHETIC_ENGINEERING_EVIDENCE_ONLY'),
            content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(source_policy_version_id,source_item_id),
            CHECK(ingested_at>=published_at)
        )""",
        (
            "CREATE INDEX social_observation_pit_idx ON "
            "social_content_observations(instrument_id,topic_key,published_at,ingested_at)"
        ),
        """CREATE TABLE social_narrative_clusters (
            cluster_id UUID PRIMARY KEY,
            source_policy_version_id UUID NOT NULL
                REFERENCES social_source_policy_versions(source_policy_version_id),
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            model_version TEXT NOT NULL, topic_key CHAR(64) NOT NULL,
            topic_label TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL,
            content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(source_policy_version_id,instrument_id,model_version,topic_key)
        )""",
        """CREATE TABLE social_narrative_cluster_members (
            cluster_id UUID NOT NULL REFERENCES social_narrative_clusters(cluster_id),
            observation_id UUID NOT NULL REFERENCES social_content_observations(observation_id),
            PRIMARY KEY(cluster_id,observation_id)
        )""",
        """CREATE TABLE social_narrative_metric_windows (
            window_id UUID PRIMARY KEY,
            source_policy_version_id UUID NOT NULL
                REFERENCES social_source_policy_versions(source_policy_version_id),
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            cluster_id UUID NOT NULL REFERENCES social_narrative_clusters(cluster_id),
            window_start TIMESTAMPTZ NOT NULL, window_end TIMESTAMPTZ NOT NULL,
            evaluated_at TIMESTAMPTZ NOT NULL,
            mention_volume INTEGER NOT NULL CHECK(mention_volume>0),
            mention_acceleration NUMERIC(38,12) NOT NULL,
            unique_author_count INTEGER NOT NULL CHECK(unique_author_count>0),
            unique_author_growth NUMERIC(38,12) NOT NULL,
            bot_likelihood NUMERIC(18,12) NOT NULL CHECK(bot_likelihood BETWEEN 0 AND 1),
            sentiment NUMERIC(18,12) NOT NULL CHECK(sentiment BETWEEN -1 AND 1),
            sentiment_change NUMERIC(18,12) NOT NULL CHECK(sentiment_change BETWEEN -1 AND 1),
            disagreement NUMERIC(18,12) NOT NULL CHECK(disagreement BETWEEN 0 AND 1),
            emotional_intensity NUMERIC(18,12) NOT NULL
                CHECK(emotional_intensity BETWEEN 0 AND 1),
            influencer_concentration NUMERIC(18,12) NOT NULL
                CHECK(influencer_concentration BETWEEN 0 AND 1),
            crowding_score NUMERIC(18,12) NOT NULL CHECK(crowding_score BETWEEN 0 AND 1),
            spam_concentration NUMERIC(18,12) NOT NULL
                CHECK(spam_concentration BETWEEN 0 AND 1),
            coordinated_promotion_likelihood NUMERIC(18,12) NOT NULL
                CHECK(coordinated_promotion_likelihood BETWEEN 0 AND 1),
            pump_and_dump_risk NUMERIC(18,12) NOT NULL
                CHECK(pump_and_dump_risk BETWEEN 0 AND 1),
            narrative_persistence NUMERIC(18,12) NOT NULL
                CHECK(narrative_persistence BETWEEN 0 AND 1),
            price_sentiment_divergence NUMERIC(18,12) NOT NULL
                CHECK(price_sentiment_divergence BETWEEN 0 AND 1),
            category_distribution JSONB NOT NULL
                CHECK(jsonb_typeof(category_distribution)='object'),
            language_distribution JSONB NOT NULL CHECK(jsonb_typeof(language_distribution)='object'),
            geography_distribution JSONB NOT NULL CHECK(jsonb_typeof(geography_distribution)='object'),
            emerging_topic BOOLEAN NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('BLOCKED','REVIEW_REQUIRED')),
            reasons JSONB NOT NULL CHECK(jsonb_typeof(reasons)='array'),
            research_only BOOLEAN NOT NULL DEFAULT TRUE CHECK(research_only=TRUE),
            standalone_trade_trigger BOOLEAN NOT NULL DEFAULT FALSE
                CHECK(standalone_trade_trigger=FALSE),
            automatic_authority BOOLEAN NOT NULL DEFAULT FALSE CHECK(automatic_authority=FALSE),
            evidence_label TEXT NOT NULL
                CHECK(evidence_label='SYNTHETIC_ENGINEERING_EVIDENCE_ONLY'),
            content_hash CHAR(64) NOT NULL UNIQUE,
            UNIQUE(source_policy_version_id,instrument_id,cluster_id,window_start,window_end),
            CHECK(window_end>window_start), CHECK(evaluated_at>=window_end),
            CHECK(jsonb_object_length(category_distribution)=6),
            CHECK(category_distribution ?& ARRAY['RETAIL_SENTIMENT',
                  'PROFESSIONAL_COMMENTARY','NEWS_AMPLIFICATION','AUTOMATED_SPAM',
                  'PROMOTIONAL_ACTIVITY','ORGANIC_DISCUSSION'])
        )""",
        (
            "CREATE INDEX social_narrative_window_pit_idx ON "
            "social_narrative_metric_windows(instrument_id,window_end,evaluated_at)"
        ),
        """CREATE TABLE social_narrative_window_data_health (
            window_id UUID NOT NULL REFERENCES social_narrative_metric_windows(window_id),
            data_health_assessment_id UUID NOT NULL
                REFERENCES data_health_assessments(assessment_id),
            PRIMARY KEY(window_id,data_health_assessment_id)
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
