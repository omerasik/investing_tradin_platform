"""Decimal authority reruns of frozen Strategy Lab candidates (Phase R4.7)

Revision ID: 20261008_0056
Revises: 20261008_0055
Create Date: 2026-10-08

Owner decision OR-3 (2026-10-08): float64 is the search tier and Decimal the
authoritative tier.

* ``strategy_lab_authority_reruns`` -- one immutable, content-addressed record
  per Decimal rerun of a frozen candidate set: the rerun set and why each
  candidate is in it, per-candidate reconciliation (``RECONCILED`` or
  ``DIVERGED_DECIMAL_WINS``) with divergence counts, the Decimal metrics at the
  OR-5 baseline lag and every mandatory sweep lag, and the re-established
  selection (``ESTABLISHED`` or ``FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED``).
  ``numeric_tier`` is ``DECIMAL_AUTHORITATIVE`` by CHECK; the numeric policy
  slot must be the OR-3 identity admitted by 0055.
* ``strategy_lab_candidate_sets.authoritative_rerun`` additionally admits
  ``REQUIRED_DECIMAL_RERUN_OR_3``: a set frozen from an OR-3-bound study states
  that a rerun is required (sets frozen before OR-3 keep their marker).

Additive; downgrade drops the table and restores the single marker.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261008_0056"
down_revision = "20261008_0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE strategy_lab_authority_reruns (
        rerun_hash CHAR(64) PRIMARY KEY CHECK(rerun_hash ~ '^[0-9a-f]{64}$'),
        candidate_set_hash CHAR(64) NOT NULL REFERENCES strategy_lab_candidate_sets(candidate_set_hash),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        numeric_tier TEXT NOT NULL CHECK(numeric_tier='DECIMAL_AUTHORITATIVE'),
        numeric_policy_slot TEXT NOT NULL CHECK(numeric_policy_slot ~ '^or3-numeric-policy-v1:[0-9a-f]{64}$'),
        selection_status TEXT NOT NULL
            CHECK(selection_status IN ('ESTABLISHED','FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED')),
        rerun_count INTEGER NOT NULL CHECK(rerun_count>0),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute("CREATE INDEX strategy_lab_authority_reruns_set_idx ON strategy_lab_authority_reruns(candidate_set_hash)")
    op.execute(immutable_trigger_sql("strategy_lab_authority_reruns"))
    op.execute(
        "ALTER TABLE strategy_lab_candidate_sets DROP CONSTRAINT strategy_lab_candidate_sets_authoritative_rerun_check"
    )
    op.execute(
        "ALTER TABLE strategy_lab_candidate_sets ADD CONSTRAINT strategy_lab_candidate_sets_authoritative_rerun_check "
        "CHECK(authoritative_rerun IN ('PENDING_OWNER_DECISION_OR_3','REQUIRED_DECIMAL_RERUN_OR_3'))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS strategy_lab_authority_reruns")
    op.execute(
        "ALTER TABLE strategy_lab_candidate_sets DROP CONSTRAINT strategy_lab_candidate_sets_authoritative_rerun_check"
    )
    op.execute(
        "ALTER TABLE strategy_lab_candidate_sets ADD CONSTRAINT strategy_lab_candidate_sets_authoritative_rerun_check "
        "CHECK(authoritative_rerun='PENDING_OWNER_DECISION_OR_3')"
    )
