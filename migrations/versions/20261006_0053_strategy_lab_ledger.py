"""strategy lab study / trial ledger and work queue (Phase R4.1)

Revision ID: 20261006_0053
Revises: 20260924_0052
Create Date: 2026-10-06

Phase R4.1. Persists the content-addressed identities of
``strategy_lab_study_v1`` and a resumable, idempotent trial queue:

* ``strategy_lab_studies`` / ``strategy_lab_trials`` -- immutable identity rows.
  The OR-3 numeric-policy and OR-6 cost-policy slots can, by CHECK, only hold
  their unset markers, and the evaluation bound can never pass the untouched
  holdout boundary. Admitting a policy is a future migration, never a row value.
* ``strategy_lab_trial_queue`` -- mutable *work state* (not evidence): one row
  per trial, claimed with ``FOR UPDATE SKIP LOCKED`` under a lease. A trigger
  makes ``SUCCEEDED`` and ``CANCELLED`` terminal and forbids deletes.
* ``strategy_lab_trial_events`` -- append-only lifecycle evidence.
* ``strategy_lab_trial_results`` -- one immutable result per trial, always
  ``SEARCH_NON_AUTHORITATIVE`` with the cost slot unset.

Purely additive; downgrade drops the five tables and the guard function.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261006_0053"
down_revision = "20260924_0052"
branch_labels = None
depends_on = None

_HASH = "CHAR(64) NOT NULL CHECK({col} ~ '^[0-9a-f]{{64}}$')"
_OR3 = "'UNSET_PENDING_OWNER_DECISION_OR_3'"
_OR6 = "'UNSET_PENDING_OWNER_DECISION_OR_6'"


def upgrade() -> None:
    op.execute(
        f"""CREATE TABLE strategy_lab_studies (
        study_id UUID PRIMARY KEY,
        content_hash {_HASH.format(col="content_hash")} UNIQUE,
        schema_version TEXT NOT NULL CHECK(schema_version='strategy-lab-study-v1'),
        strategy_family TEXT NOT NULL CHECK(length(trim(strategy_family))>0),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        planned_trial_count INTEGER NOT NULL CHECK(planned_trial_count>0),
        evaluation_upper_bound_exclusive TIMESTAMPTZ NOT NULL
            CHECK(evaluation_upper_bound_exclusive<=TIMESTAMPTZ '2026-08-20 00:00:00+00'),
        numeric_policy_slot TEXT NOT NULL CHECK(numeric_policy_slot={_OR3}),
        cost_policy_slot TEXT NOT NULL CHECK(cost_policy_slot={_OR6}),
        label TEXT NOT NULL,
        registered_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute(immutable_trigger_sql("strategy_lab_studies"))

    op.execute(
        f"""CREATE TABLE strategy_lab_trials (
        trial_id UUID PRIMARY KEY,
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        content_hash {_HASH.format(col="content_hash")} UNIQUE,
        ordinal INTEGER NOT NULL CHECK(ordinal>=0),
        space_index BIGINT NOT NULL CHECK(space_index>=0),
        parameters JSONB NOT NULL CHECK(jsonb_typeof(parameters)='object'),
        UNIQUE(study_id, ordinal),
        UNIQUE(study_id, space_index))"""
    )
    op.execute(immutable_trigger_sql("strategy_lab_trials"))

    op.execute(
        """CREATE TABLE strategy_lab_trial_queue (
        trial_id UUID PRIMARY KEY REFERENCES strategy_lab_trials(trial_id),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        ordinal INTEGER NOT NULL CHECK(ordinal>=0),
        state TEXT NOT NULL CHECK(state IN ('PENDING','CLAIMED','SUCCEEDED','FAILED','CANCELLED')),
        attempt INTEGER NOT NULL CHECK(attempt>=0),
        lease_owner TEXT NULL,
        lease_expires_at TIMESTAMPTZ NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CHECK((state='CLAIMED') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)))"""
    )
    op.execute(
        "CREATE INDEX strategy_lab_trial_queue_claim_idx "
        "ON strategy_lab_trial_queue(study_id, state, ordinal)"
    )
    op.execute(
        """CREATE FUNCTION strategy_lab_queue_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'strategy_lab_trial_queue rows are never deleted';
            END IF;
            IF OLD.state IN ('SUCCEEDED','CANCELLED') THEN
                RAISE EXCEPTION 'strategy_lab_trial_queue: % is terminal', OLD.state;
            END IF;
            IF NEW.trial_id <> OLD.trial_id OR NEW.study_id <> OLD.study_id
               OR NEW.ordinal <> OLD.ordinal OR NEW.attempt < OLD.attempt THEN
                RAISE EXCEPTION 'strategy_lab_trial_queue identity is immutable';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER strategy_lab_trial_queue_guard BEFORE UPDATE OR DELETE "
        "ON strategy_lab_trial_queue FOR EACH ROW EXECUTE FUNCTION strategy_lab_queue_guard()"
    )

    op.execute(
        """CREATE TABLE strategy_lab_trial_events (
        event_id UUID PRIMARY KEY,
        trial_id UUID NOT NULL REFERENCES strategy_lab_trials(trial_id),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        attempt INTEGER NOT NULL CHECK(attempt>=0),
        event_type TEXT NOT NULL CHECK(event_type IN
            ('CLAIMED','LEASE_EXPIRED','SUCCEEDED','FAILED','RETRY_REQUESTED','CANCELLED')),
        worker TEXT NULL,
        detail JSONB NOT NULL CHECK(jsonb_typeof(detail)='object'),
        occurred_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute(
        "CREATE INDEX strategy_lab_trial_events_trial_idx "
        "ON strategy_lab_trial_events(trial_id, occurred_at)"
    )
    op.execute(immutable_trigger_sql("strategy_lab_trial_events"))

    op.execute(
        f"""CREATE TABLE strategy_lab_trial_results (
        trial_id UUID PRIMARY KEY REFERENCES strategy_lab_trials(trial_id),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        attempt INTEGER NOT NULL CHECK(attempt>0),
        result_content_hash {_HASH.format(col="result_content_hash")},
        numeric_tier TEXT NOT NULL CHECK(numeric_tier='SEARCH_NON_AUTHORITATIVE'),
        cost_policy_slot TEXT NOT NULL CHECK(cost_policy_slot={_OR6}),
        outcome TEXT NOT NULL CHECK(outcome IN ('EVALUATED','INADMISSIBLE_PARAMETERS')),
        metrics JSONB NOT NULL CHECK(jsonb_typeof(metrics)='object'),
        worker TEXT NOT NULL CHECK(length(trim(worker))>0),
        produced_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute(
        "CREATE INDEX strategy_lab_trial_results_study_idx ON strategy_lab_trial_results(study_id)"
    )
    op.execute(immutable_trigger_sql("strategy_lab_trial_results"))


def downgrade() -> None:
    for table in (
        "strategy_lab_trial_results",
        "strategy_lab_trial_events",
        "strategy_lab_trial_queue",
        "strategy_lab_trials",
        "strategy_lab_studies",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS strategy_lab_queue_guard()")
