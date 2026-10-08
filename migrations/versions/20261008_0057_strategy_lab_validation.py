"""Strategy Lab preregistrations, one-shot holdout openings, validations and candidate states (Phase R6)

Revision ID: 20261008_0057
Revises: 20261008_0056
Create Date: 2026-10-08

* ``strategy_lab_preregistrations`` -- content-addressed packets (DRAFT or
  AUTHORIZED) binding a study, its Decimal authority rerun and the owner's OR-7
  inputs.
* ``strategy_lab_holdout_openings`` -- the untouched holdout of a research cycle
  is opened at most once: ``cycle_id`` is the primary key, so a second opening
  of the same cycle cannot be recorded.
* ``strategy_lab_holdout_validations`` -- immutable holdout validation results.
* ``strategy_lab_candidate_states`` -- append-only lifecycle events. The state
  set has no shortcut to execution and ``PROFESSIONALLY_VALIDATED`` is reserved
  (no code path writes it in this phase).

All immutable; additive; downgrade drops the four tables.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261008_0057"
down_revision = "20261008_0056"
branch_labels = None
depends_on = None

_STATES = (
    "'SEARCH_NON_AUTHORITATIVE','DECIMAL_AUTHORITATIVE','PREREGISTERED','HOLDOUT_FAILED_REJECTED',"
    "'INCUBATING','INCUBATION_FAILED_REJECTED','PROFESSIONALLY_VALIDATED'"
)


def upgrade() -> None:
    op.execute(
        """CREATE TABLE strategy_lab_preregistrations (
        preregistration_hash CHAR(64) PRIMARY KEY CHECK(preregistration_hash ~ '^[0-9a-f]{64}$'),
        preregistration_id UUID NOT NULL UNIQUE,
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        cycle_id TEXT NOT NULL CHECK(cycle_id ~ '^cycle-[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
        status TEXT NOT NULL CHECK(status IN ('DRAFT','AUTHORIZED')),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute(immutable_trigger_sql("strategy_lab_preregistrations"))
    op.execute(
        """CREATE TABLE strategy_lab_holdout_openings (
        cycle_id TEXT PRIMARY KEY CHECK(cycle_id ~ '^cycle-[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
        preregistration_hash CHAR(64) NOT NULL REFERENCES strategy_lab_preregistrations(preregistration_hash),
        holdout_start TIMESTAMPTZ NOT NULL,
        holdout_end_exclusive TIMESTAMPTZ NOT NULL,
        opened_by TEXT NOT NULL CHECK(length(trim(opened_by))>0),
        opened_at TIMESTAMPTZ NOT NULL,
        CHECK(holdout_end_exclusive>holdout_start))"""
    )
    op.execute(immutable_trigger_sql("strategy_lab_holdout_openings"))
    op.execute(
        """CREATE TABLE strategy_lab_holdout_validations (
        validation_hash CHAR(64) PRIMARY KEY CHECK(validation_hash ~ '^[0-9a-f]{64}$'),
        preregistration_hash CHAR(64) NOT NULL REFERENCES strategy_lab_preregistrations(preregistration_hash),
        cycle_id TEXT NOT NULL REFERENCES strategy_lab_holdout_openings(cycle_id),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute(immutable_trigger_sql("strategy_lab_holdout_validations"))
    op.execute(
        f"""CREATE TABLE strategy_lab_candidate_states (
        event_hash CHAR(64) PRIMARY KEY CHECK(event_hash ~ '^[0-9a-f]{{64}}$'),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        trial_id UUID NOT NULL,
        state TEXT NOT NULL CHECK(state IN ({_STATES})),
        evidence_hash CHAR(64) NOT NULL CHECK(evidence_hash ~ '^[0-9a-f]{{64}}$'),
        reasons JSONB NOT NULL CHECK(jsonb_typeof(reasons)='array'),
        recorded_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute("CREATE INDEX strategy_lab_candidate_states_study_idx ON strategy_lab_candidate_states(study_id)")
    op.execute(immutable_trigger_sql("strategy_lab_candidate_states"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS strategy_lab_candidate_states")
    op.execute("DROP TABLE IF EXISTS strategy_lab_holdout_validations")
    op.execute("DROP TABLE IF EXISTS strategy_lab_holdout_openings")
    op.execute("DROP TABLE IF EXISTS strategy_lab_preregistrations")
