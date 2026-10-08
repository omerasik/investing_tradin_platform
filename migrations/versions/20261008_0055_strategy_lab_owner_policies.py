"""admit the owner-approved OR-3 and OR-6 policy slots (Phase R4.6)

Revision ID: 20261008_0055
Revises: 20261006_0054
Create Date: 2026-10-08

The owner decided OR-3 (numeric doctrine) and OR-6 (research cost methodology)
on 2026-10-08. Migration 0053 pinned both study slots (and the result's cost
slot) to their unset markers by CHECK, stating that admitting a policy is a
future migration, never a row value. This is that migration:

* ``numeric_policy_slot`` admits the unset marker or exactly the approved OR-3
  policy identity (``or3-numeric-policy-v1:<sha256>`` of the payload in
  ``trade_platform.strategy_lab_policies_v1``). A different numeric policy is a
  new migration.
* ``cost_policy_slot`` (studies and results) admits the unset marker or an
  ``or6-cost-policy-v1:<sha256>`` identity: fee schedules and stress scenarios
  are versioned operator inputs, so each version has its own identity, but the
  schema is fixed.

Results stay ``SEARCH_NON_AUTHORITATIVE`` (unchanged CHECK) and the evaluation
bound stays at the 2026-08-20 untouched holdout (unchanged CHECK). The old
constraints are dropped by name and replaced; downgrade restores them.
"""

from alembic import op

revision = "20261008_0055"
down_revision = "20261006_0054"
branch_labels = None
depends_on = None

_OR3_UNSET = "'UNSET_PENDING_OWNER_DECISION_OR_3'"
_OR6_UNSET = "'UNSET_PENDING_OWNER_DECISION_OR_6'"
_OR3_APPROVED = "'or3-numeric-policy-v1:a54772a82a9ebfd12e016643b4d6c2c81b17815e3d50211eb9b0adf72c1226a5'"
_OR6_PATTERN = "'^or6-cost-policy-v1:[0-9a-f]{64}$'"


def upgrade() -> None:
    op.execute("ALTER TABLE strategy_lab_studies DROP CONSTRAINT strategy_lab_studies_numeric_policy_slot_check")
    op.execute("ALTER TABLE strategy_lab_studies DROP CONSTRAINT strategy_lab_studies_cost_policy_slot_check")
    op.execute(
        "ALTER TABLE strategy_lab_trial_results DROP CONSTRAINT strategy_lab_trial_results_cost_policy_slot_check"
    )
    op.execute(
        "ALTER TABLE strategy_lab_studies ADD CONSTRAINT strategy_lab_studies_numeric_policy_slot_check "
        f"CHECK(numeric_policy_slot IN ({_OR3_UNSET}, {_OR3_APPROVED}))"
    )
    op.execute(
        "ALTER TABLE strategy_lab_studies ADD CONSTRAINT strategy_lab_studies_cost_policy_slot_check "
        f"CHECK(cost_policy_slot = {_OR6_UNSET} OR cost_policy_slot ~ {_OR6_PATTERN})"
    )
    op.execute(
        "ALTER TABLE strategy_lab_trial_results ADD CONSTRAINT strategy_lab_trial_results_cost_policy_slot_check "
        f"CHECK(cost_policy_slot = {_OR6_UNSET} OR cost_policy_slot ~ {_OR6_PATTERN})"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_lab_studies DROP CONSTRAINT strategy_lab_studies_numeric_policy_slot_check")
    op.execute("ALTER TABLE strategy_lab_studies DROP CONSTRAINT strategy_lab_studies_cost_policy_slot_check")
    op.execute(
        "ALTER TABLE strategy_lab_trial_results DROP CONSTRAINT strategy_lab_trial_results_cost_policy_slot_check"
    )
    op.execute(
        "ALTER TABLE strategy_lab_studies ADD CONSTRAINT strategy_lab_studies_numeric_policy_slot_check "
        f"CHECK(numeric_policy_slot = {_OR3_UNSET})"
    )
    op.execute(
        "ALTER TABLE strategy_lab_studies ADD CONSTRAINT strategy_lab_studies_cost_policy_slot_check "
        f"CHECK(cost_policy_slot = {_OR6_UNSET})"
    )
    op.execute(
        "ALTER TABLE strategy_lab_trial_results ADD CONSTRAINT strategy_lab_trial_results_cost_policy_slot_check "
        f"CHECK(cost_policy_slot = {_OR6_UNSET})"
    )
