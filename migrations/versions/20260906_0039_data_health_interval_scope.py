"""give Data Health assessments an explicit interval identity dimension

Revision ID: 20260906_0039
Revises: 20260906_0038
Create Date: 2026-09-06

Module 3F's ``scheduler.run_data_health_evaluation`` persists one INSTRUMENT-scoped
assessment per (instrument_id, interval) series it finds in the bar authority, using
``scope_value=instrument_id`` alone. Once an instrument has more than one interval
ingested at the same tick (e.g. Module 3G.1's daily + minute Databento bridge), the
second persist collided with the ``(scope_type, scope_value, evaluated_at)`` uniqueness
constraint below, because nothing distinguished "1d" from "1m" for the same instrument.

This migration adds ``interval`` as an explicit, first-class part of that identity
(empty for GLOBAL/STRATEGY/ASSET_CLASS scopes and any genuinely instrument-wide, not
series-specific, assessment) rather than folding it into ``scope_value`` as an
implicit string convention -- ``scope_value`` for INSTRUMENT scope remains exactly
the plain ``instrument_id`` throughout this codebase (the signal-validation trigger,
``PostgresDataHealthStore.active_blocks``, and every other consumer join/filter on it
that way), so no existing scope-value semantics change.
"""

from alembic import op

revision = "20260906_0039"
down_revision = "20260906_0038"
branch_labels = None
depends_on = None

_OLD_UNIQUE = "data_health_assessments_scope_type_scope_value_evaluated_at_key"
# Postgres identifiers are capped at 63 bytes; the "obvious" fully-descriptive name
# (scope_type_scope_value_interval_evaluated_at) is 72 and would be silently
# truncated by CREATE TABLE's auto-naming, but NOT by an explicitly-named ADD
# CONSTRAINT -- so it must be named explicitly here, short enough to fit exactly.
_NEW_UNIQUE = "data_health_assessments_scope_interval_evaluated_at_key"


def upgrade() -> None:
    op.execute("ALTER TABLE data_health_assessments ADD COLUMN interval TEXT NOT NULL DEFAULT ''")
    op.execute(f"ALTER TABLE data_health_assessments DROP CONSTRAINT {_OLD_UNIQUE}")
    op.execute(
        f"ALTER TABLE data_health_assessments ADD CONSTRAINT {_NEW_UNIQUE} "
        "UNIQUE (scope_type, scope_value, interval, evaluated_at)"
    )
    op.execute("DROP INDEX IF EXISTS data_health_gate_idx")
    op.execute(
        "CREATE INDEX data_health_gate_idx ON data_health_assessments"
        "(scope_type, scope_value, interval, evaluated_at DESC, blocking)"
    )
    # Same trigger, same join/filter conditions -- only the "latest as of" partition
    # key gains `interval` so a per-series row for one interval of an instrument can
    # no longer be masked (via DISTINCT ON picking the other row) by, or mask, a
    # different interval's own latest state. GLOBAL/STRATEGY/ASSET_CLASS rows always
    # carry interval='' so this is a no-op for them.
    op.execute(
        """CREATE OR REPLACE FUNCTION enforce_data_health_signal_validation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE blocked BOOLEAN;
        BEGIN
          IF NEW.status <> 'VALIDATED' THEN RETURN NEW; END IF;
          SELECT EXISTS (
            SELECT 1 FROM (
              SELECT DISTINCT ON (h.scope_type, h.scope_value, h.interval) h.blocking
              FROM data_health_assessments h
              JOIN runtime_signal_proposals p ON p.signal_id = NEW.signal_id
              JOIN runtime_instruments i ON i.instrument_id = p.instrument_id
              WHERE h.evaluated_at <= NEW.assessed_at AND (
                (h.scope_type='GLOBAL' AND h.scope_value='*') OR
                (h.scope_type='ASSET_CLASS' AND h.scope_value=i.asset_class) OR
                (h.scope_type='STRATEGY' AND h.scope_value=p.strategy_version) OR
                (h.scope_type='INSTRUMENT' AND h.scope_value=p.instrument_id)
              )
              ORDER BY h.scope_type, h.scope_value, h.interval, h.evaluated_at DESC
            ) latest WHERE latest.blocking
          ) INTO blocked;
          IF blocked THEN RAISE EXCEPTION 'signal validation blocked by data health'; END IF;
          RETURN NEW;
        END $$"""
    )


def downgrade() -> None:
    # Disposable developer databases only; production evidence is forward-only.
    # Reverting collapses any coexisting per-interval assessments' identity back onto
    # (scope_type, scope_value, evaluated_at) -- a genuine loss of the distinction this
    # migration adds, acceptable only for a throwaway schema, never for real evidence.
    op.execute(
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
        END $$"""
    )
    op.execute("DROP INDEX IF EXISTS data_health_gate_idx")
    op.execute(
        "CREATE INDEX data_health_gate_idx ON data_health_assessments"
        "(scope_type, scope_value, evaluated_at DESC, blocking)"
    )
    op.execute(f"ALTER TABLE data_health_assessments DROP CONSTRAINT IF EXISTS {_NEW_UNIQUE}")
    op.execute(
        f"ALTER TABLE data_health_assessments ADD CONSTRAINT {_OLD_UNIQUE} "
        "UNIQUE (scope_type, scope_value, evaluated_at)"
    )
    op.execute("ALTER TABLE data_health_assessments DROP COLUMN IF EXISTS interval")
