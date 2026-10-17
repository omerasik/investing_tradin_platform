"""Paper evidence store: live/replay fill parity assessments and forward funding evidence records

Revision ID: 20261017_0066
Revises: 20261017_0065
Create Date: 2026-10-17

Two append-only, immutable tables written only by
:mod:`trade_platform.paper_evidence_store_v1`, which re-derives every content
hash before a row is written and again whenever one is read.

* ``paper_fill_parity_assessments`` -- one row per (parity report, candidate):
  a ``paper-fill-parity-v1`` report is multi-candidate, so it is stored once per
  candidate it names, every row carrying the report hash and its verbatim
  identity. Status ``PASS`` has no reason, ``FAIL`` / ``INCOMPLETE`` at least
  one; the live window's last bar has completed by ``as_of`` and ``as_of`` is at
  or before ``recorded_at``.
* ``paper_funding_evidence_records`` -- one row per verified candidate
  ``funding_evidence`` section (``paper-funding-evidence-v1``) with the bridges
  it binds. Never costable: ``ECONOMICALLY_COSTABLE`` is refused by CHECK and
  the economic blockers are never empty.

Purely additive; downgrade drops both tables.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261017_0066"
down_revision = "20261017_0065"
branch_labels = None
depends_on = None

_HASH = "CHAR(64) NOT NULL CHECK({col} ~ '^[0-9a-f]{{64}}$')"


def upgrade() -> None:
    op.execute(
        f"""CREATE TABLE paper_fill_parity_assessments (
        assessment_id UUID PRIMARY KEY,
        report_content_hash {_HASH.format(col="report_content_hash")},
        report_id UUID NOT NULL,
        schema_version TEXT NOT NULL CHECK(schema_version = 'paper-fill-parity-v1'),
        cycle_id TEXT NOT NULL REFERENCES strategy_lab_research_cycles(cycle_id),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        study_content_hash {_HASH.format(col="study_content_hash")},
        trial_id UUID NOT NULL,
        symbol TEXT NOT NULL CHECK(symbol ~ '^[A-Z0-9]{{2,20}}$'),
        authority_evidence_hash TEXT NOT NULL CHECK(length(trim(authority_evidence_hash)) > 0),
        status TEXT NOT NULL CHECK(status IN ('PASS','FAIL','INCOMPLETE')),
        reasons TEXT[] NOT NULL,
        as_of TIMESTAMPTZ NOT NULL,
        window_start TIMESTAMPTZ NULL,
        window_end TIMESTAMPTZ NULL,
        holdout_start TIMESTAMPTZ NOT NULL,
        holdout_end_exclusive TIMESTAMPTZ NULL,
        source_hashes JSONB NOT NULL CHECK(jsonb_typeof(source_hashes) = 'object'),
        identity JSONB NOT NULL CHECK(jsonb_typeof(identity) = 'object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        UNIQUE(report_content_hash, study_id, trial_id),
        CHECK((status = 'PASS') = (cardinality(reasons) = 0)),
        CHECK(identity->>'schema_version' IS NOT NULL AND identity->>'schema_version' = schema_version),
        CHECK(identity->'assessment'->>'status' IS NOT NULL AND identity->'assessment'->>'status' = status),
        CHECK(identity->'inputs'->'holdout'->>'cycle_id' IS NOT NULL
              AND identity->'inputs'->'holdout'->>'cycle_id' = cycle_id),
        CHECK((window_start IS NULL) = (window_end IS NULL)),
        CHECK(window_start IS NULL OR window_start <= window_end),
        CHECK(window_end IS NULL OR window_end + interval '1 minute' <= as_of),
        CHECK(holdout_end_exclusive IS NULL OR holdout_end_exclusive > holdout_start),
        CHECK(as_of <= recorded_at))"""
    )
    op.execute("CREATE INDEX paper_fill_parity_assessments_candidate_idx "
               "ON paper_fill_parity_assessments(cycle_id, study_id, trial_id, as_of, recorded_at)")
    op.execute(immutable_trigger_sql("paper_fill_parity_assessments"))

    op.execute(
        f"""CREATE TABLE paper_funding_evidence_records (
        record_id UUID PRIMARY KEY,
        evidence_hash {_HASH.format(col="evidence_hash")},
        schema_version TEXT NOT NULL CHECK(schema_version = 'paper-funding-evidence-v1'),
        cycle_id TEXT NOT NULL REFERENCES strategy_lab_research_cycles(cycle_id),
        study_id UUID NOT NULL REFERENCES strategy_lab_studies(study_id),
        trial_id UUID NOT NULL,
        symbol TEXT NOT NULL CHECK(symbol ~ '^[A-Z0-9]{{2,20}}$'),
        state TEXT NOT NULL CHECK(state IN ('NO_FUNDING_EVIDENCE_SUPPLIED','EVIDENCE_LINKED',
                                            'EVIDENCE_COMPLETE','ECONOMICALLY_COSTABLE')),
        completeness_blockers TEXT[] NOT NULL,
        economic_blockers TEXT[] NOT NULL CHECK(cardinality(economic_blockers) > 0),
        as_of TIMESTAMPTZ NOT NULL,
        window_start TIMESTAMPTZ NULL,
        window_end TIMESTAMPTZ NULL,
        bridge_hashes JSONB NOT NULL CHECK(jsonb_typeof(bridge_hashes) = 'object'),
        bridges JSONB NOT NULL CHECK(jsonb_typeof(bridges) = 'array'),
        evidence_validation_hash TEXT NULL,
        evidence JSONB NOT NULL CHECK(jsonb_typeof(evidence) = 'object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        UNIQUE(cycle_id, evidence_hash),
        CHECK(state <> 'ECONOMICALLY_COSTABLE'),
        CHECK(state <> 'EVIDENCE_COMPLETE' OR cardinality(completeness_blockers) = 0),
        CHECK(state <> 'EVIDENCE_LINKED' OR cardinality(completeness_blockers) > 0),
        CHECK((state = 'NO_FUNDING_EVIDENCE_SUPPLIED') = (jsonb_array_length(bridges) = 0)),
        CHECK((jsonb_array_length(bridges) = 0) = (bridge_hashes = '{{}}'::jsonb)),
        CHECK(evidence->>'schema_version' IS NOT NULL AND evidence->>'schema_version' = schema_version),
        CHECK(evidence->>'state' IS NOT NULL AND evidence->>'state' = state),
        CHECK(evidence->>'evidence_hash' IS NOT NULL AND evidence->>'evidence_hash' = evidence_hash),
        CHECK(evidence->'costable' IS NOT NULL AND evidence->'costable' = 'false'::jsonb),
        CHECK((window_start IS NULL) = (window_end IS NULL)),
        CHECK(window_start IS NULL OR window_start <= window_end),
        CHECK(as_of <= recorded_at))"""
    )
    op.execute("CREATE INDEX paper_funding_evidence_records_candidate_idx "
               "ON paper_funding_evidence_records(cycle_id, study_id, trial_id, as_of, recorded_at)")
    op.execute(immutable_trigger_sql("paper_funding_evidence_records"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_funding_evidence_records")
    op.execute("DROP TABLE IF EXISTS paper_fill_parity_assessments")
