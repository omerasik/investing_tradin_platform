"""widen feature_definition_versions.family to add DERIVATIVES

Revision ID: 20260909_0047
Revises: 20260908_0046
Create Date: 2026-09-09

Module 3J.1a (roadmap NEXT-03 phase 5, first consumer of the Module 3J.0
generalized ``FUTURES_SERIES`` subject). Adds exactly one new stable
``FeatureFamily`` member, ``DERIVATIVES``, per the owner-decided scope in
``docs/MODULE_3J1_PROPOSAL_MULTI_ASSET_DERIVATIVES_FEATURE_PACK.md`` section 5
and section 8 decision 1: one family for all seven proposed derivatives
features (three of which -- the futures-curve feature pack -- are registered
by this module; the remaining four ship in later 3J.1 phases per the same
family, no further schema change needed for those).

**This migration does nothing else.** It widens the existing
``feature_definition_versions_family_check`` CHECK constraint in place; it
creates no new table, no new feature-materialization authority, no new
subject registry and no new dataset registry -- ``feature_authority.py``'s
existing ``PostgresFeatureAuthority`` (``register`` /
``materialize_subject`` / ``latest_as_of_subject``) and the
``feature_materializations`` table from migration ``20260816_0014`` (further
generalized by ``20260908_0046``) remain the single Feature Authority.
Every pre-existing ``feature_definition_versions`` row and every pre-existing
``feature_materializations`` row is left completely untouched: this is a
constraint-only DDL change, not a data migration, and neither table's
immutability trigger (``prevent_immutable_mutation()``) is disabled at any
point in this file.

Downgrading is only meaningful before a ``DERIVATIVES`` feature definition has
ever been registered: re-adding the narrower seven-member CHECK will itself
fail (correctly) if any ``family='DERIVATIVES'`` row already exists, the same
"best-effort, fails closed if already used" downgrade shape as
``20260908_0046``.
"""

from alembic import op

revision = "20260909_0047"
down_revision = "20260908_0046"
branch_labels = None
depends_on = None

_ORIGINAL_FAMILIES = (
    "'PRICE_RETURNS','TREND','MOMENTUM','VOLATILITY','LIQUIDITY','FUNDAMENTAL','MACRO'"
)
_WIDENED_FAMILIES = f"{_ORIGINAL_FAMILIES},'DERIVATIVES'"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE feature_definition_versions DROP CONSTRAINT IF EXISTS "
        "feature_definition_versions_family_check"
    )
    op.execute(
        "ALTER TABLE feature_definition_versions ADD CONSTRAINT "
        f"feature_definition_version_family_check CHECK(family IN ({_WIDENED_FAMILIES}))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE feature_definition_versions DROP CONSTRAINT IF EXISTS "
        "feature_definition_version_family_check"
    )
    op.execute(
        "ALTER TABLE feature_definition_versions ADD CONSTRAINT "
        f"feature_definition_versions_family_check CHECK(family IN ({_ORIGINAL_FAMILIES}))"
    )
