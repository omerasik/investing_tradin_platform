"""strategy lab study manifests and frozen candidate sets (Phase R4.4)

Revision ID: 20261006_0054
Revises: 20261006_0053
Create Date: 2026-10-06

Phase R4.4. Two immutable, content-addressed records over a finished study:

* ``strategy_lab_study_manifests`` -- the study's complete trial accounting
  (planned trial count = multiple-testing denominator, outcome counts, every
  result hash) under one manifest hash.
* ``strategy_lab_candidate_sets`` -- candidates frozen from one manifest by an
  explicit, recorded selection rule. By CHECK they stay search-tier evidence:
  ``numeric_tier`` is ``SEARCH_NON_AUTHORITATIVE`` and the authoritative rerun
  is ``PENDING_OWNER_DECISION_OR_3``. Admitting an authoritative rerun is a
  future migration, never a row value.

Purely additive; downgrade drops both tables.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261006_0054"
down_revision = "20261006_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE strategy_lab_study_manifests (
        manifest_hash CHAR(64) PRIMARY KEY CHECK(manifest_hash ~ '^[0-9a-f]{64}$'),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        planned_trial_count INTEGER NOT NULL CHECK(planned_trial_count>0),
        result_count INTEGER NOT NULL CHECK(result_count>=0 AND result_count<=planned_trial_count),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute("CREATE INDEX strategy_lab_study_manifests_study_idx ON strategy_lab_study_manifests(study_id)")
    op.execute(immutable_trigger_sql("strategy_lab_study_manifests"))
    op.execute(
        """CREATE TABLE strategy_lab_candidate_sets (
        candidate_set_hash CHAR(64) PRIMARY KEY CHECK(candidate_set_hash ~ '^[0-9a-f]{64}$'),
        manifest_hash CHAR(64) NOT NULL REFERENCES strategy_lab_study_manifests(manifest_hash),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        candidate_count INTEGER NOT NULL CHECK(candidate_count>0),
        numeric_tier TEXT NOT NULL CHECK(numeric_tier='SEARCH_NON_AUTHORITATIVE'),
        authoritative_rerun TEXT NOT NULL CHECK(authoritative_rerun='PENDING_OWNER_DECISION_OR_3'),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity)='object'),
        recorded_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute("CREATE INDEX strategy_lab_candidate_sets_study_idx ON strategy_lab_candidate_sets(study_id)")
    op.execute(immutable_trigger_sql("strategy_lab_candidate_sets"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS strategy_lab_candidate_sets")
    op.execute("DROP TABLE IF EXISTS strategy_lab_study_manifests")
