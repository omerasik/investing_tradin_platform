"""strategy lab forward incubation exit criteria (Phase R10, OR-7 exit contract)

Revision ID: 20261017_0065
Revises: 20261017_0064
Create Date: 2026-10-17

``strategy_lab_incubation_exit_criteria`` holds immutable, content-addressed
exit-criteria packets (``incubation_exit_criteria_v1``): one candidate (cycle,
study, trial) bound to the preregistration it was preregistered under (foreign
key), ``DRAFT`` or ``AUTHORIZED``. By CHECK an ``AUTHORIZED`` row has no
unresolved reason and names its ``authorized_by`` / ``authorized_on``, a
``DRAFT`` row has at least one, and no row promotes or activates anything. At
most one ``AUTHORIZED`` packet per candidate and cycle (partial unique index);
``recorded_at`` is the database's clock (a supplied value is overwritten), so a
packet cannot be backdated before the forward evidence it judges.

Purely additive; downgrade drops the table and its stamp function.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261017_0065"
down_revision = "20261017_0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE strategy_lab_incubation_exit_criteria (
        criteria_hash CHAR(64) PRIMARY KEY CHECK(criteria_hash ~ '^[0-9a-f]{64}$'),
        criteria_id UUID NOT NULL UNIQUE,
        cycle_id TEXT NOT NULL REFERENCES strategy_lab_research_cycles(cycle_id),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        trial_id UUID NOT NULL,
        preregistration_hash CHAR(64) NOT NULL REFERENCES strategy_lab_preregistrations(preregistration_hash),
        status TEXT NOT NULL CHECK(status IN ('DRAFT','AUTHORIZED')),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK(identity ? 'unresolved' AND jsonb_typeof(identity->'unresolved')='array'),
        CHECK(identity ? 'status' AND identity->>'status' IS NOT NULL AND identity->>'status' = status),
        CHECK((status = 'AUTHORIZED') = (jsonb_array_length(identity->'unresolved') = 0)),
        CHECK(status <> 'AUTHORIZED' OR (identity->>'authorized_by' IS NOT NULL
                                         AND identity->>'authorized_on' IS NOT NULL)),
        CHECK(identity ? 'promotion' AND identity->>'promotion' IS NOT NULL
              AND identity->>'promotion' = 'PROFESSIONALLY_VALIDATED_RESERVED_NEVER_PROMOTED_HERE'),
        CHECK(identity ? 'account_activation' AND identity->>'account_activation' IS NOT NULL
              AND identity->>'account_activation' = 'NEVER_ACTIVATES_AN_ACCOUNT'))"""
    )
    op.execute(
        "CREATE UNIQUE INDEX strategy_lab_incubation_exit_criteria_one_authorized "
        "ON strategy_lab_incubation_exit_criteria(cycle_id, study_id, trial_id) WHERE status='AUTHORIZED'"
    )
    op.execute(
        "CREATE INDEX strategy_lab_incubation_exit_criteria_candidate_idx "
        "ON strategy_lab_incubation_exit_criteria(cycle_id, study_id, trial_id)"
    )
    op.execute(immutable_trigger_sql("strategy_lab_incubation_exit_criteria"))
    op.execute(
        """CREATE FUNCTION strategy_lab_stamp_exit_criteria_recorded_at() RETURNS trigger AS $$
        BEGIN NEW.recorded_at := now(); RETURN NEW; END; $$ LANGUAGE plpgsql"""
    )
    op.execute(
        "CREATE TRIGGER strategy_lab_incubation_exit_criteria_stamp BEFORE INSERT "
        "ON strategy_lab_incubation_exit_criteria FOR EACH ROW "
        "EXECUTE FUNCTION strategy_lab_stamp_exit_criteria_recorded_at()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS strategy_lab_incubation_exit_criteria")
    op.execute("DROP FUNCTION IF EXISTS strategy_lab_stamp_exit_criteria_recorded_at()")
