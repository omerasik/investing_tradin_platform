"""three-clock feature materializations (hash_version V3)

Revision ID: 20260924_0049
Revises: 20260916_0048
Create Date: 2026-09-24

Phase R2A.2. Wires the R2A.1 knowledge-time doctrine
(``knowledge_time_doctrine_v1.py``) into the durable Feature Authority, in
place -- no second feature table.

**Why.** Every V1/V2 row stores one ``knowledge_at`` that is
``max(normalized_at, dataset.created_at)``: when *this platform* ingested or
sealed the evidence, not when the *market* could know the value. On the T1
composite all 216,000 basis rows share it, which collapses every decision time
(``UNPROVEN_DISTINCT_FEATURE_DECISION_TIMES``).

**What changes, additively.**

1. Six nullable columns, populated only by ``hash_version='V3'`` rows:
   ``market_knowledge_at`` (NULL = undefined, the honest T0/T1 answer),
   ``platform_recorded_at`` (the old instant under its honest name),
   ``claim_ceiling``, ``feature_knowledge`` (the doctrine's market identity
   payload), ``feature_knowledge_hash`` and ``knowledge_inputs`` (every input
   observation's recorded clock facts, so a later reader can re-derive the
   knowledge time from the genuine evidence-tier verdict instead of trusting
   the row). Existing rows get NULLs through a
   metadata-only ``ADD COLUMN``: nothing is recomputed, no immutability
   trigger is disabled, no V1/V2 hash changes.
2. ``hash_version`` admits ``V3``. CHECKs keep the columns coherent: V1/V2
   rows carry none of them; a V3 row carries all but ``market_knowledge_at``,
   mirrors ``platform_recorded_at`` into the legacy ``knowledge_at`` (so the
   table's temporal CHECK keeps its meaning), and has a defined market
   knowledge time exactly when its claim is CONDITIONAL or PROFESSIONAL, never
   before its own ``effective_at`` (a value is not knowable before it is
   complete). Every nullable comparison is guarded by an
   explicit ``IS [NOT] NULL`` -- a bare comparison against NULL passes a CHECK.
3. A V3 natural key that excludes every operational clock -- subject, dataset,
   ``event_at``, ``effective_at`` -- as a partial unique index, so recomputing
   the same evidence later reconciles to the same row instead of adding one.
4. V3 rows get the same deferred subject-existence proof as V2 (the existing
   ``require_valid_feature_subject()`` function is widened, not duplicated).
   The read paths keep the two families apart: legacy reads exclude V3 rows,
   and the historical V3 read admits only V3 rows with a defined market
   knowledge time.

**Downgrade** is best-effort and fails closed (the V1/V2-only CHECK cannot be
re-added) once any V3 row exists -- the same shape as ``20260908_0046``.
"""

from alembic import op

revision = "20260924_0049"
down_revision = "20260916_0048"
branch_labels = None
depends_on = None

_TABLE = "feature_materializations"

_V3_ONLY_COLUMNS = (
    "market_knowledge_at", "platform_recorded_at", "claim_ceiling", "feature_knowledge",
    "feature_knowledge_hash", "knowledge_inputs",
)

_SUBJECT_FUNCTION_V2_ONLY = """CREATE OR REPLACE FUNCTION require_valid_feature_subject() RETURNS trigger AS $$
    BEGIN
        IF NEW.hash_version <> 'V2' THEN
            RETURN NULL;
        END IF;
        IF NEW.subject_type = 'INSTRUMENT' THEN
            IF NOT EXISTS (
                SELECT 1 FROM professional_instruments WHERE instrument_id = NEW.subject_id
            ) THEN
                RAISE EXCEPTION
                    'feature subject INSTRUMENT % does not exist', NEW.subject_id;
            END IF;
        ELSIF NEW.subject_type = 'FUTURES_SERIES' THEN
            IF NOT EXISTS (
                SELECT 1 FROM futures_contract_series WHERE series_id = NEW.subject_id
            ) THEN
                RAISE EXCEPTION
                    'feature subject FUTURES_SERIES % does not exist', NEW.subject_id;
            END IF;
        ELSE
            RAISE EXCEPTION 'unsupported feature subject type %', NEW.subject_type;
        END IF;
        RETURN NULL;
    END; $$ LANGUAGE plpgsql"""

_SUBJECT_FUNCTION_V2_V3 = """CREATE OR REPLACE FUNCTION require_valid_feature_subject() RETURNS trigger AS $$
    BEGIN
        IF NEW.hash_version NOT IN ('V2','V3') THEN
            RETURN NULL;
        END IF;
        IF NEW.subject_type = 'INSTRUMENT' THEN
            IF NOT EXISTS (
                SELECT 1 FROM professional_instruments WHERE instrument_id = NEW.subject_id
            ) THEN
                RAISE EXCEPTION
                    'feature subject INSTRUMENT % does not exist', NEW.subject_id;
            END IF;
        ELSIF NEW.subject_type = 'FUTURES_SERIES' THEN
            IF NOT EXISTS (
                SELECT 1 FROM futures_contract_series WHERE series_id = NEW.subject_id
            ) THEN
                RAISE EXCEPTION
                    'feature subject FUTURES_SERIES % does not exist', NEW.subject_id;
            END IF;
        ELSE
            RAISE EXCEPTION 'unsupported feature subject type %', NEW.subject_type;
        END IF;
        RETURN NULL;
    END; $$ LANGUAGE plpgsql"""


def upgrade() -> None:
    statements = (
        f"ALTER TABLE {_TABLE} ADD COLUMN market_knowledge_at TIMESTAMPTZ",
        f"ALTER TABLE {_TABLE} ADD COLUMN platform_recorded_at TIMESTAMPTZ",
        f"ALTER TABLE {_TABLE} ADD COLUMN claim_ceiling TEXT",
        f"ALTER TABLE {_TABLE} ADD COLUMN feature_knowledge JSONB",
        f"ALTER TABLE {_TABLE} ADD COLUMN feature_knowledge_hash CHAR(64)",
        f"ALTER TABLE {_TABLE} ADD COLUMN knowledge_inputs JSONB",
        f"ALTER TABLE {_TABLE} DROP CONSTRAINT feature_materialization_hash_version_check",
        (
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT feature_materialization_hash_version_check "
            "CHECK(hash_version IN ('V1','V2','V3'))"
        ),
        (
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT feature_materialization_three_clock_coherence "
            "CHECK("
            "(hash_version IN ('V1','V2') AND market_knowledge_at IS NULL "
            "AND platform_recorded_at IS NULL AND claim_ceiling IS NULL "
            "AND feature_knowledge IS NULL AND feature_knowledge_hash IS NULL "
            "AND knowledge_inputs IS NULL) "
            "OR (hash_version='V3' AND platform_recorded_at IS NOT NULL "
            "AND knowledge_at=platform_recorded_at AND feature_knowledge IS NOT NULL "
            "AND jsonb_typeof(feature_knowledge)='object' AND feature_knowledge_hash IS NOT NULL "
            "AND feature_knowledge_hash ~ '^[0-9a-f]{64}$' AND knowledge_inputs IS NOT NULL "
            "AND jsonb_typeof(knowledge_inputs)='array' AND jsonb_array_length(knowledge_inputs)>0 "
            "AND claim_ceiling IS NOT NULL AND ("
            "(market_knowledge_at IS NOT NULL AND claim_ceiling IN ('CONDITIONAL','PROFESSIONAL') "
            "AND market_knowledge_at>=effective_at) "
            "OR (market_knowledge_at IS NULL AND claim_ceiling IN ('NONE','DESCRIPTIVE')))))"
        ),
        (
            f"CREATE UNIQUE INDEX feature_materializations_v3_natural_key ON {_TABLE}("
            "feature_id,subject_type,subject_id,dataset_version,event_at,effective_at) "
            "WHERE hash_version='V3'"
        ),
        (
            f"CREATE INDEX feature_materializations_v3_market_asof_idx ON {_TABLE}("
            "feature_id,subject_type,subject_id,dataset_version,market_knowledge_at) "
            "WHERE hash_version='V3' AND market_knowledge_at IS NOT NULL"
        ),
        _SUBJECT_FUNCTION_V2_V3,
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    op.execute(_SUBJECT_FUNCTION_V2_ONLY)
    op.execute("DROP INDEX IF EXISTS feature_materializations_v3_market_asof_idx")
    op.execute("DROP INDEX IF EXISTS feature_materializations_v3_natural_key")
    op.execute(
        f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS "
        "feature_materialization_three_clock_coherence"
    )
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT feature_materialization_hash_version_check")
    # Fails closed once any V3 row exists: downgrading past this revision is only
    # meaningful before a three-clock materialization was ever written.
    op.execute(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT feature_materialization_hash_version_check "
        "CHECK(hash_version IN ('V1','V2'))"
    )
    for column in reversed(_V3_ONLY_COLUMNS):
        op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN {column}")
