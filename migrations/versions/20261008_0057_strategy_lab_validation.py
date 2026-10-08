"""Strategy Lab research cycles, preregistrations, one-shot holdout, validations, candidate states (Phase R6)

Revision ID: 20261008_0057
Revises: 20261008_0056
Create Date: 2026-10-08

* ``strategy_lab_research_cycles`` -- issued research cycles. The id is derived
  from the holdout start (whole UTC day, unique); ``registered_at`` is set by the
  database, and a cycle's holdout must start strictly after its registration,
  except the current cycle-2026-08-20, which this migration records as
  preregistered by the 3D.9A pilot.
* ``strategy_lab_preregistrations`` -- content-addressed packets (DRAFT or
  AUTHORIZED).
* ``strategy_lab_holdout_openings`` -- at most one per cycle (primary key); only
  for an AUTHORIZED packet (composite foreign key on hash and status); the
  holdout start must be the cycle's own (composite foreign key); the holdout end
  may not lie after the opening instant; and holdout spans of different cycles
  may never overlap (exclusion constraint), so a holdout cannot be re-opened
  under another cycle name.
* ``strategy_lab_holdout_validations`` -- exactly one per cycle, bound to the
  opening and to the holdout dataset's content hash.
* ``strategy_lab_candidate_states`` -- written only with a validation; this
  phase can record only INCUBATING or HOLDOUT_FAILED_REJECTED.

All immutable; additive; downgrade drops the five tables.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261008_0057"
down_revision = "20261008_0056"
branch_labels = None
depends_on = None

_CYCLE_ID = "^cycle-[0-9]{4}-[0-9]{2}-[0-9]{2}$"


def upgrade() -> None:
    op.execute(
        f"""CREATE TABLE strategy_lab_research_cycles (
        cycle_id TEXT PRIMARY KEY CHECK(cycle_id ~ '{_CYCLE_ID}'),
        holdout_start TIMESTAMPTZ NOT NULL UNIQUE,
        registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        note TEXT NOT NULL CHECK(length(trim(note))>0),
        CHECK(cycle_id = 'cycle-' || to_char(holdout_start AT TIME ZONE 'UTC', 'YYYY-MM-DD')),
        CHECK(holdout_start = date_trunc('day', holdout_start AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'),
        CHECK(holdout_start > registered_at OR cycle_id = 'cycle-2026-08-20'),
        UNIQUE(cycle_id, holdout_start))"""
    )
    op.execute(
        "INSERT INTO strategy_lab_research_cycles (cycle_id, holdout_start, note) VALUES "
        "('cycle-2026-08-20', TIMESTAMPTZ '2026-08-20 00:00:00+00', "
        "'untouched holdout preregistered by the 3D.9A pilot; immutable for this cycle')"
    )
    op.execute(immutable_trigger_sql("strategy_lab_research_cycles"))
    # The registration and opening instants are the database's own clock: a
    # supplied value is overwritten, so no cycle can be backdated into an alias of
    # a past (e.g. the current) holdout span, and no opening can predate its span.
    op.execute(
        """CREATE FUNCTION strategy_lab_stamp_registered_at() RETURNS trigger AS $$
        BEGIN NEW.registered_at := now(); RETURN NEW; END; $$ LANGUAGE plpgsql"""
    )
    op.execute(
        "CREATE TRIGGER strategy_lab_research_cycles_stamp BEFORE INSERT ON strategy_lab_research_cycles "
        "FOR EACH ROW EXECUTE FUNCTION strategy_lab_stamp_registered_at()"
    )
    op.execute(
        """CREATE TABLE strategy_lab_preregistrations (
        preregistration_hash CHAR(64) PRIMARY KEY CHECK(preregistration_hash ~ '^[0-9a-f]{64}$'),
        preregistration_id UUID NOT NULL UNIQUE,
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        cycle_id TEXT NOT NULL REFERENCES strategy_lab_research_cycles(cycle_id),
        status TEXT NOT NULL CHECK(status IN ('DRAFT','AUTHORIZED')),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        UNIQUE(preregistration_hash, status))"""
    )
    op.execute(immutable_trigger_sql("strategy_lab_preregistrations"))
    op.execute(
        """CREATE TABLE strategy_lab_holdout_openings (
        cycle_id TEXT PRIMARY KEY,
        holdout_start TIMESTAMPTZ NOT NULL,
        holdout_end_exclusive TIMESTAMPTZ NOT NULL,
        preregistration_hash CHAR(64) NOT NULL,
        preregistration_status TEXT NOT NULL CHECK(preregistration_status='AUTHORIZED'),
        opened_by TEXT NOT NULL CHECK(length(trim(opened_by))>0),
        opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (cycle_id, holdout_start) REFERENCES strategy_lab_research_cycles(cycle_id, holdout_start),
        FOREIGN KEY (preregistration_hash, preregistration_status)
            REFERENCES strategy_lab_preregistrations(preregistration_hash, status),
        CHECK(holdout_end_exclusive > holdout_start),
        CHECK(holdout_end_exclusive <= opened_at),
        UNIQUE(cycle_id, preregistration_hash),
        EXCLUDE USING gist (tstzrange(holdout_start, holdout_end_exclusive) WITH &&))"""
    )
    op.execute(immutable_trigger_sql("strategy_lab_holdout_openings"))
    op.execute(
        """CREATE FUNCTION strategy_lab_stamp_opened_at() RETURNS trigger AS $$
        BEGIN NEW.opened_at := now(); RETURN NEW; END; $$ LANGUAGE plpgsql"""
    )
    op.execute(
        "CREATE TRIGGER strategy_lab_holdout_openings_stamp BEFORE INSERT ON strategy_lab_holdout_openings "
        "FOR EACH ROW EXECUTE FUNCTION strategy_lab_stamp_opened_at()"
    )
    op.execute(
        """CREATE TABLE strategy_lab_holdout_validations (
        validation_hash CHAR(64) PRIMARY KEY CHECK(validation_hash ~ '^[0-9a-f]{64}$'),
        cycle_id TEXT NOT NULL UNIQUE,
        preregistration_hash CHAR(64) NOT NULL,
        dataset_content_hash CHAR(64) NOT NULL CHECK(dataset_content_hash ~ '^[0-9a-f]{64}$'),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        FOREIGN KEY (cycle_id, preregistration_hash)
            REFERENCES strategy_lab_holdout_openings(cycle_id, preregistration_hash))"""
    )
    op.execute(immutable_trigger_sql("strategy_lab_holdout_validations"))
    op.execute(
        """CREATE TABLE strategy_lab_candidate_states (
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        trial_id UUID NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('INCUBATING','HOLDOUT_FAILED_REJECTED')),
        evidence_hash CHAR(64) NOT NULL REFERENCES strategy_lab_holdout_validations(validation_hash),
        reasons JSONB NOT NULL CHECK(jsonb_typeof(reasons)='array'),
        recorded_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (study_id, trial_id, evidence_hash))"""
    )
    op.execute(immutable_trigger_sql("strategy_lab_candidate_states"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS strategy_lab_candidate_states")
    op.execute("DROP TABLE IF EXISTS strategy_lab_holdout_validations")
    op.execute("DROP TABLE IF EXISTS strategy_lab_holdout_openings")
    op.execute("DROP TABLE IF EXISTS strategy_lab_preregistrations")
    op.execute("DROP TABLE IF EXISTS strategy_lab_research_cycles")
    op.execute("DROP FUNCTION IF EXISTS strategy_lab_stamp_opened_at()")
    op.execute("DROP FUNCTION IF EXISTS strategy_lab_stamp_registered_at()")
