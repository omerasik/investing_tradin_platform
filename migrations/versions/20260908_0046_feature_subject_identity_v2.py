"""generalize Feature Authority to an explicit subject identity (INSTRUMENT | FUTURES_SERIES)

Revision ID: 20260908_0046
Revises: 20260908_0045
Create Date: 2026-09-08

Module 3J.0 (roadmap NEXT-03 phase 4, prerequisite for RQ-009 cross-asset
Feature Authority integration). Generalizes the existing durable Feature
Authority (``feature_authority.py``) in place -- no second
feature-materialization table or store is created, and no derivative feature
family is implemented here. This module fixes subject identity only.

**Why.** ``FeatureMaterialization.instrument_id`` and the table's uniqueness
key are instrument-centric, which is correct for equity/crypto instrument
features but wrong for the Module 3I.3 futures term-structure artifact, which
belongs to a ``FUTURES_SERIES`` (a product root like GC), not to one listed
contract. Putting a series ID into ``instrument_id`` would let a series
collide with an unrelated instrument that happens to share the same text, and
manufacturing a fake ``ProfessionalInstrument`` merely to satisfy the old
column would fabricate identity that was never registered anywhere.

**What changes, and what deliberately does not.**

1. Two new columns, ``subject_type`` and ``subject_id`` -- the canonical
   identity pair, replacing ``instrument_id`` as the table's uniqueness and
   read-path key. Every existing row is backfilled to
   ``subject_type='INSTRUMENT'``, ``subject_id=instrument_id`` via
   metadata-only ``ADD COLUMN ... DEFAULT`` where the value is a per-row copy
   of ``instrument_id`` (see the ``DISABLE TRIGGER`` note below); nothing is
   recomputed, and no historical value is rewritten.
2. ``instrument_id`` is retained (now nullable) purely as a legacy
   convenience column for the unchanged instrument-only read path
   (``PostgresFeatureAuthority.latest_as_of``), and a CHECK constraint keeps
   it permanently coherent with the canonical subject pair: for an
   ``INSTRUMENT`` subject it must equal ``subject_id``; for a
   ``FUTURES_SERIES`` subject it must be ``NULL``. There is exactly one
   authoritative identity (subject_type + subject_id); ``instrument_id`` can
   never independently disagree with it.
3. ``hash_version`` (``V1`` | ``V2``) distinguishes the pre-existing content
   hash formula (which includes a literal ``instrument_id`` key and is used by
   every row written through the unchanged ``materialize()``/
   ``FeatureMaterialization.create`` path) from the new generalized formula
   (which includes ``subject_type``/``subject_id`` explicitly, used only by
   the new ``materialize_subject()``/``FeatureMaterializationV2.create`` path).
   V1 hashes are never recomputed and remain valid forever; V2 identity is
   structurally distinct from V1 by construction (different JSON key shape),
   so a V2 ``INSTRUMENT`` row can never collide with a V1 row for the same
   instrument even if they share every other field.
4. Uniqueness moves from ``(feature_id, instrument_id, dataset_version,
   event_at, effective_at, knowledge_at)`` to ``(feature_id, subject_type,
   subject_id, dataset_version, event_at, effective_at, knowledge_at)`` --
   the old constraint is dropped outright, not kept alongside the new one, so
   there is exactly one uniqueness authority. Keeping both would have let two
   ``FUTURES_SERIES`` materializations collide under the old constraint
   (``instrument_id`` NULL for both) while disagreeing under the new one.
5. A deferred constraint trigger, ``require_valid_feature_subject``, proves at
   COMMIT that an ``INSTRUMENT`` subject resolves to a real row in
   ``professional_instruments`` and a ``FUTURES_SERIES`` subject resolves to a
   real row in ``futures_contract_series`` (the existing 3H.1 authority; no
   parallel subject registry). This check applies **only to ``hash_version =
   'V2'`` rows**. The pre-existing V1 instrument-only path never had (or was
   ever specified to have) an instrument-existence check -- the existing
   ``FeatureAuthorityPostgresTests`` and ``TrendResearchV2``/
   ``RegimeEngineV2`` Postgres suites already materialize features for
   fixture instrument identifiers (e.g. ``fixture:SPY``) that were never
   registered in ``professional_instruments``, and retroactively enforcing
   existence there would break that already-established, still-supported
   backward-compatible behaviour. The new invariant applies to the new
   generalized entry point, not retroactively to the old one.

Nothing about feature *definitions* changes here (no new ``FeatureFamily``, no
broadened ``required_dataset_types``); this migration is subject-identity
infrastructure only.
"""

from alembic import op

revision = "20260908_0046"
down_revision = "20260908_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = (
        # ---- 1. new columns, backfilled without recomputing anything -------
        # Metadata-only for the two constant columns: Postgres 11+ does not
        # rewrite existing rows or fire per-row triggers for ADD COLUMN with a
        # non-volatile DEFAULT (verified locally: it does not invoke this
        # table's own BEFORE UPDATE immutability trigger).
        "ALTER TABLE feature_materializations ADD COLUMN subject_type TEXT NOT NULL DEFAULT 'INSTRUMENT'",
        "ALTER TABLE feature_materializations ADD COLUMN hash_version TEXT NOT NULL DEFAULT 'V1'",
        "ALTER TABLE feature_materializations ADD COLUMN subject_id TEXT",
        # subject_id copies instrument_id per row, which genuinely is an
        # UPDATE (the value differs per row) and would otherwise be blocked by
        # this table's own immutability trigger -- disabled only for this one
        # backfill statement, on existing rows only, changing no financial
        # value or content_hash.
        "ALTER TABLE feature_materializations DISABLE TRIGGER feature_materializations_immutable",
        "UPDATE feature_materializations SET subject_id = instrument_id WHERE subject_id IS NULL",
        "ALTER TABLE feature_materializations ENABLE TRIGGER feature_materializations_immutable",
        "ALTER TABLE feature_materializations ALTER COLUMN subject_id SET NOT NULL",
        "ALTER TABLE feature_materializations ALTER COLUMN subject_type DROP DEFAULT",
        "ALTER TABLE feature_materializations ALTER COLUMN hash_version DROP DEFAULT",
        (
            "ALTER TABLE feature_materializations ADD CONSTRAINT "
            "feature_materialization_subject_type_check "
            "CHECK(subject_type IN ('INSTRUMENT','FUTURES_SERIES'))"
        ),
        (
            "ALTER TABLE feature_materializations ADD CONSTRAINT "
            "feature_materialization_hash_version_check CHECK(hash_version IN ('V1','V2'))"
        ),
        # ---- 2. legacy instrument_id becomes a coherence-checked convenience -
        "ALTER TABLE feature_materializations ALTER COLUMN instrument_id DROP NOT NULL",
        (
            "ALTER TABLE feature_materializations ADD CONSTRAINT "
            "feature_materialization_legacy_instrument_coherence CHECK("
            "(subject_type='INSTRUMENT' AND instrument_id IS NOT NULL AND instrument_id=subject_id) "
            "OR (subject_type='FUTURES_SERIES' AND instrument_id IS NULL)"
            ")"
        ),
        # ---- 3. one uniqueness authority, not two --------------------------
        (
            "ALTER TABLE feature_materializations DROP CONSTRAINT "
            "feature_materializations_feature_id_instrument_id_dataset_v_key"
        ),
        (
            "ALTER TABLE feature_materializations ADD CONSTRAINT "
            "feature_materialization_subject_natural_key UNIQUE(feature_id,subject_type,subject_id,"
            "dataset_version,event_at,effective_at,knowledge_at)"
        ),
        "DROP INDEX feature_materializations_asof_idx",
        (
            "CREATE INDEX feature_materializations_subject_asof_idx ON feature_materializations("
            "feature_id,subject_type,subject_id,dataset_version,event_at,knowledge_at DESC,"
            "computed_at DESC)"
        ),
        # ---- 4. database-enforced subject existence, V2 rows only ----------
        """CREATE FUNCTION require_valid_feature_subject() RETURNS trigger AS $$
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
                    -- Unreachable given feature_materialization_subject_type_check, kept
                    -- as defense-in-depth against a future widening of that CHECK.
                    RAISE EXCEPTION 'unsupported feature subject type %', NEW.subject_type;
                END IF;
                RETURN NULL;
            END; $$ LANGUAGE plpgsql""",
        """CREATE CONSTRAINT TRIGGER feature_materialization_subject_exists
            AFTER INSERT ON feature_materializations
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION require_valid_feature_subject()""",
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS feature_materialization_subject_exists ON feature_materializations"
    )
    op.execute("DROP FUNCTION IF EXISTS require_valid_feature_subject()")
    op.execute("DROP INDEX IF EXISTS feature_materializations_subject_asof_idx")
    op.execute(
        "CREATE INDEX feature_materializations_asof_idx ON feature_materializations("
        "feature_id,instrument_id,dataset_version,event_at,knowledge_at DESC,computed_at DESC)"
    )
    op.execute(
        "ALTER TABLE feature_materializations DROP CONSTRAINT "
        "feature_materialization_subject_natural_key"
    )
    # Best-effort: fails if any FUTURES_SERIES row (NULL instrument_id) or any
    # duplicate-under-the-old-key row exists -- downgrading past this point is
    # only meaningful before a FUTURES_SERIES subject was ever materialized.
    op.execute(
        "ALTER TABLE feature_materializations ADD CONSTRAINT "
        "feature_materializations_feature_id_instrument_id_dataset_v_key "
        "UNIQUE(feature_id,instrument_id,dataset_version,event_at,effective_at,knowledge_at)"
    )
    op.execute(
        "ALTER TABLE feature_materializations DROP CONSTRAINT "
        "feature_materialization_legacy_instrument_coherence"
    )
    op.execute("ALTER TABLE feature_materializations ALTER COLUMN instrument_id SET NOT NULL")
    op.execute(
        "ALTER TABLE feature_materializations DROP CONSTRAINT "
        "feature_materialization_hash_version_check"
    )
    op.execute(
        "ALTER TABLE feature_materializations DROP CONSTRAINT "
        "feature_materialization_subject_type_check"
    )
    op.execute("ALTER TABLE feature_materializations DROP COLUMN subject_id")
    op.execute("ALTER TABLE feature_materializations DROP COLUMN hash_version")
    op.execute("ALTER TABLE feature_materializations DROP COLUMN subject_type")
