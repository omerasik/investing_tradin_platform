"""add persistent data-health assessments and signal gate

Revision ID: 20260815_0010
Revises: 20260815_0009
Create Date: 2026-08-15
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260815_0010"
down_revision = "20260815_0009"
branch_labels = None
depends_on = None


TABLES = ("data_health_assessments", "data_health_findings")


def upgrade() -> None:
    action_check = "('INFO','WARN','DEGRADE_CONFIDENCE','BLOCK_INSTRUMENT','BLOCK_STRATEGY','BLOCK_ASSET_CLASS','GLOBAL_BLOCK')"
    statements = (
        f"""CREATE TABLE data_health_assessments (
            assessment_id UUID PRIMARY KEY,
            dataset_version_id UUID REFERENCES historical_dataset_versions(dataset_version_id),
            scope_type TEXT NOT NULL CHECK(scope_type IN ('INSTRUMENT','STRATEGY','ASSET_CLASS','GLOBAL')),
            scope_value TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            evaluated_at TIMESTAMPTZ NOT NULL,
            expected_start TIMESTAMPTZ NOT NULL,
            expected_end TIMESTAMPTZ NOT NULL,
            max_action TEXT NOT NULL CHECK(max_action IN {action_check}),
            blocking BOOLEAN NOT NULL,
            content_hash CHAR(64) NOT NULL UNIQUE,
            summary JSONB NOT NULL,
            CHECK(expected_end >= expected_start),
            CHECK((scope_type = 'GLOBAL' AND scope_value = '*') OR scope_type <> 'GLOBAL'),
            UNIQUE(scope_type, scope_value, evaluated_at)
        )""",
        f"""CREATE TABLE data_health_findings (
            finding_id UUID PRIMARY KEY,
            assessment_id UUID NOT NULL REFERENCES data_health_assessments(assessment_id),
            finding_sequence INTEGER NOT NULL CHECK(finding_sequence >= 0),
            check_type TEXT NOT NULL CHECK(check_type IN
                ('MISSING_BARS','DUPLICATE_BARS','TIMESTAMP_REGRESSION','IMPOSSIBLE_OHLC',
                 'INVALID_VOLUME','STALE_OBSERVATIONS','GAPS','CORPORATE_ACTION_MISMATCH',
                 'PROVIDER_DISAGREEMENT','TIMEZONE_SESSION_MISMATCH','INCOMPLETE_DATASET')),
            action TEXT NOT NULL CHECK(action IN {action_check}),
            observed_at TIMESTAMPTZ,
            detail JSONB NOT NULL,
            content_hash CHAR(64) NOT NULL,
            UNIQUE(assessment_id, content_hash),
            UNIQUE(assessment_id, finding_sequence)
        )""",
        "CREATE INDEX data_health_gate_idx ON data_health_assessments(scope_type, scope_value, evaluated_at DESC, blocking)",
        """CREATE OR REPLACE FUNCTION enforce_data_health_signal_validation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE blocked BOOLEAN;
        BEGIN
          IF NEW.status <> 'VALIDATED' THEN RETURN NEW; END IF;
          SELECT EXISTS (
            SELECT 1 FROM (
              SELECT DISTINCT ON (h.scope_type, h.scope_value) h.blocking
              FROM data_health_assessments h
              JOIN runtime_signal_proposals p ON p.signal_id = NEW.signal_id
              JOIN runtime_instruments i ON i.instrument_id = p.instrument_id
              WHERE h.evaluated_at <= NEW.assessed_at AND (
                (h.scope_type='GLOBAL' AND h.scope_value='*') OR
                (h.scope_type='ASSET_CLASS' AND h.scope_value=i.asset_class) OR
                (h.scope_type='STRATEGY' AND h.scope_value=p.strategy_version) OR
                (h.scope_type='INSTRUMENT' AND h.scope_value=p.instrument_id)
              )
              ORDER BY h.scope_type, h.scope_value, h.evaluated_at DESC
            ) latest WHERE latest.blocking
          ) INTO blocked;
          IF blocked THEN RAISE EXCEPTION 'signal validation blocked by data health'; END IF;
          RETURN NEW;
        END $$""",
        "CREATE TRIGGER runtime_signal_validation_data_health BEFORE INSERT ON runtime_signal_validations FOR EACH ROW EXECUTE FUNCTION enforce_data_health_signal_validation()",
    )
    for statement in statements:
        op.execute(statement)
    for table in TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS runtime_signal_validation_data_health ON runtime_signal_validations")
    op.execute("DROP FUNCTION IF EXISTS enforce_data_health_signal_validation()")
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
