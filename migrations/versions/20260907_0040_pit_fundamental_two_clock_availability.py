"""add two-clock research-availability columns to pit_fundamental_filings

Revision ID: 20260907_0040
Revises: 20260906_0039
Create Date: 2026-09-07

Corrects a single-clock bug in PostgresPitFundamentalStore.available_as_of():
it required both `accepted_at <= as_of` and `ingested_at <= as_of` against one
timestamp, which cannot honestly represent a historical backfill (a filing
publicly accepted in 2024 but first ingested by this platform in 2026). The
fix adds a `research_available_at` column -- a platform-derived, versioned,
conservative estimate of when a filing's facts are safe to treat as available
for PIT research (never earlier than `accepted_at`; SEC's own documentation
states there is no exact public-dissemination timestamp, so this is never
claimed to be an SEC-provided fact) -- plus its `availability_policy_version`
label, so `available_point_in_time(effective_at, known_at)` can gate real-world
availability and platform knowledge separately without ever backdating
`ingested_at`.
"""

from alembic import op

revision = "20260907_0040"
down_revision = "20260906_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A bare ADD COLUMN ... NOT NULL (no backfill step) is deliberate, not an
    # oversight: pit_fundamental_filings is DB-trigger immutable (see
    # postgres_schema.immutable_trigger_sql, applied by the original migration
    # 20260815_0011) -- even a migration-time UPDATE to backfill existing rows
    # is rejected by that trigger ("immutable evidence cannot be updated or
    # deleted"), confirmed by attempting exactly that while authoring this
    # migration. Safely backfilling would require this migration to also drop
    # and recreate that trigger, a materially bigger and more sensitive change
    # than this correction warrants. This is acceptable now because this
    # repository has never ingested a real filing -- Module 3G.1f.2 is the
    # first -- so the table is empty in every real environment this has run
    # against. A future migration on this table that must run against
    # genuinely populated production data will need to manage the immutability
    # trigger explicitly and deliberately; that is out of scope here.
    op.execute(
        "ALTER TABLE pit_fundamental_filings "
        "ADD COLUMN research_available_at TIMESTAMPTZ NOT NULL, "
        "ADD COLUMN availability_policy_version TEXT NOT NULL"
    )
    op.execute(
        "ALTER TABLE pit_fundamental_filings "
        "ADD CONSTRAINT pit_filing_research_available_not_before_accepted "
        "CHECK (research_available_at >= accepted_at)"
    )
    op.execute(
        "CREATE INDEX pit_filing_pit_idx ON pit_fundamental_filings"
        "(instrument_id, research_available_at, ingested_at, reporting_period_end, revision DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS pit_filing_pit_idx")
    op.execute(
        "ALTER TABLE pit_fundamental_filings "
        "DROP CONSTRAINT IF EXISTS pit_filing_research_available_not_before_accepted"
    )
    op.execute(
        "ALTER TABLE pit_fundamental_filings "
        "DROP COLUMN IF EXISTS research_available_at, "
        "DROP COLUMN IF EXISTS availability_policy_version"
    )
