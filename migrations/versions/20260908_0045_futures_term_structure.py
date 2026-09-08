"""add deterministic futures term-structure derivation authority

Revision ID: 20260908_0045
Revises: 20260907_0044
Create Date: 2026-09-08

Module 3I.3 (roadmap NEXT-03 phase 3). FUTURES ONLY. Crypto dated-future term
structure is explicitly out of scope: crypto has ``MARK_PRICE``/``INDEX_PRICE``
but no first-class settlement-price authority, so a crypto curve would be a
different semantic artifact and must never be silently treated as the exchange
settlement curve this module produces.

A futures term structure is derived evidence, not raw market data. This
migration therefore creates a **separate derived-artifact authority** -- no new
``ObservationKind``, no write into ``historical_raw_observations``, no second
market-data source registry, and no mutation of the canonical settlement
observations Module 3I.1 already sealed. The dependency chain is:

    AuthorizedHistoricalSource -> RawHistoricalObservation -> SETTLEMENT_PRICE
        -> sealed HistoricalDatasetVersion -> futures_term_structure_curves

Three tables:

1. ``futures_term_structure_methods`` -- a versioned, content-hashed method
   definition. Every field that defines the method's behaviour (finality
   policy, session/staleness policy, classification threshold, day-count
   convention) is folded into ``content_hash``, so a changed method cannot
   masquerade as the same version. CHECK constraints make several invariants
   impossible at the schema level rather than merely application-enforced:
   staleness fields only exist under the tolerance session policy, a
   classification threshold only exists when classification is enabled, and a
   day-count convention only exists when carry/annualization is declared
   supported -- so no annualized metric or classification label can ever be
   emitted without its governing declaration existing first.

2. ``futures_term_structure_curves`` -- one immutable derived curve instance,
   keyed by the natural PIT identity (series, sealed dataset, method, as_of,
   knowledge_at). Re-deriving the same natural key is idempotent at the
   application layer (same content hash -> no-op); a hash mismatch on the same
   key is a conflict, never silently overwritten, because these rows are
   INSERT-only.

3. ``futures_term_structure_points`` -- one row per real 3H.1 contract that
   participated. ``instrument_id`` is a foreign key into
   ``futures_contract_specifications``, which makes a continuous synthetic
   series, an equity, a crypto instrument or any instrument never specified as
   a futures contract mechanically impossible as a point -- not merely
   application-refused. A deferred constraint trigger additionally proves, at
   COMMIT, that every point's ``settlement_price`` and ``settlement_finality``
   equal the canonical ``futures_settlement_observations`` row its
   ``normalized_observation_id`` references, and that the contract's own
   series matches the curve's series. The settlement price is a **copied
   snapshot** of the canonical value (for the derived artifact's own
   immutability and read efficiency), never a second competing authority: the
   trigger is what keeps that snapshot from ever diverging from the canonical
   settlement payload it was taken from.

No interpolation, no extrapolation and no synthetic maturities exist anywhere
in this schema or the module built on it: a missing contract is simply absent
from the point set, and the base method's minimum point count decides whether
that leaves enough evidence for a curve at all.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260908_0045"
down_revision = "20260907_0044"
branch_labels = None
depends_on = None


NEW_TABLES = (
    "futures_term_structure_methods",
    "futures_term_structure_curves",
    "futures_term_structure_points",
)


def upgrade() -> None:
    statements = (
        """CREATE TABLE futures_term_structure_methods (
            method_id UUID PRIMARY KEY,
            method_name TEXT NOT NULL CHECK(LENGTH(TRIM(method_name)) > 0),
            method_version INTEGER NOT NULL CHECK(method_version >= 1),
            allowed_observation_kind TEXT NOT NULL
                CHECK(allowed_observation_kind = 'SETTLEMENT_PRICE'),
            minimum_point_count INTEGER NOT NULL CHECK(minimum_point_count >= 1),
            settlement_finality_policy TEXT NOT NULL
                CHECK(settlement_finality_policy IN ('FINAL_ONLY','LATEST_KNOWN_ALLOW_PRELIMINARY')),
            session_policy TEXT NOT NULL
                CHECK(session_policy IN ('SAME_SESSION_STRICT','SAME_SESSION_WITH_STALENESS_TOLERANCE')),
            max_staleness_days INTEGER,
            stale_points_flagged BOOLEAN NOT NULL DEFAULT false,
            classification_permitted_with_stale_points BOOLEAN NOT NULL DEFAULT false,
            classification_enabled BOOLEAN NOT NULL,
            classification_minimum_point_count INTEGER,
            classification_flat_threshold NUMERIC(10,6),
            carry_enabled BOOLEAN NOT NULL DEFAULT false,
            day_count_convention TEXT CHECK(day_count_convention IN ('ACT_365F','ACT_360')),
            method_definition JSONB NOT NULL,
            effective_from TIMESTAMPTZ NOT NULL,
            known_at TIMESTAMPTZ NOT NULL,
            content_hash CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE(method_name, method_version),
            -- Postgres CHECK constraints only reject a definite FALSE -- a bare
            -- comparison against a NULL column (e.g. "x > 0" when x IS NULL)
            -- evaluates to NULL, which a CHECK silently treats as satisfied.
            -- Every nullable field compared here is therefore also given an
            -- explicit IS [NOT] NULL test so the constraint cannot be defeated
            -- by leaving a companion field NULL.
            CONSTRAINT term_structure_staleness_only_under_tolerance CHECK(
                (session_policy = 'SAME_SESSION_STRICT'
                    AND max_staleness_days IS NULL
                    AND stale_points_flagged = false
                    AND classification_permitted_with_stale_points = false)
                OR (session_policy = 'SAME_SESSION_WITH_STALENESS_TOLERANCE'
                    AND max_staleness_days IS NOT NULL
                    AND max_staleness_days > 0)
            ),
            CONSTRAINT term_structure_classification_requires_declaration CHECK(
                (classification_enabled = false
                    AND classification_minimum_point_count IS NULL
                    AND classification_flat_threshold IS NULL)
                OR (classification_enabled = true
                    AND classification_minimum_point_count IS NOT NULL
                    AND classification_minimum_point_count >= 2
                    AND classification_flat_threshold IS NOT NULL
                    AND classification_flat_threshold > 0)
            ),
            CONSTRAINT term_structure_carry_requires_day_count CHECK(
                (carry_enabled = false AND day_count_convention IS NULL)
                OR (carry_enabled = true AND day_count_convention IS NOT NULL)
            )
        )""",
        """CREATE TABLE futures_term_structure_curves (
            curve_id UUID PRIMARY KEY,
            series_id TEXT NOT NULL REFERENCES futures_contract_series(series_id),
            dataset_version_id UUID NOT NULL
                REFERENCES historical_dataset_versions(dataset_version_id),
            method_id UUID NOT NULL REFERENCES futures_term_structure_methods(method_id),
            as_of DATE NOT NULL,
            knowledge_at TIMESTAMPTZ NOT NULL,
            quote_currency VARCHAR(12) NOT NULL
                CHECK(LENGTH(quote_currency) BETWEEN 3 AND 12),
            quote_unit TEXT NOT NULL CHECK(LENGTH(TRIM(quote_unit)) > 0),
            point_count INTEGER NOT NULL CHECK(point_count >= 1),
            contains_preliminary_point BOOLEAN NOT NULL,
            classification TEXT CHECK(classification IN ('CONTANGO','BACKWARDATION','FLAT')),
            content_hash CHAR(64) NOT NULL,
            derived_at TIMESTAMPTZ NOT NULL,
            UNIQUE(series_id, dataset_version_id, method_id, as_of, knowledge_at)
        )""",
        """CREATE TABLE futures_term_structure_points (
            curve_id UUID NOT NULL REFERENCES futures_term_structure_curves(curve_id),
            sequence INTEGER NOT NULL CHECK(sequence >= 1),
            instrument_id TEXT NOT NULL
                REFERENCES futures_contract_specifications(instrument_id),
            contract_expiration_date DATE NOT NULL,
            settlement_session_date DATE NOT NULL,
            is_stale BOOLEAN NOT NULL,
            settlement_price NUMERIC(38,18) NOT NULL CHECK(settlement_price > 0),
            settlement_finality TEXT NOT NULL CHECK(settlement_finality IN ('PRELIMINARY','FINAL')),
            provider_revision INTEGER NOT NULL CHECK(provider_revision >= 0),
            normalized_observation_id UUID NOT NULL
                REFERENCES historical_normalized_observations(normalized_observation_id),
            time_to_expiry_days INTEGER NOT NULL CHECK(time_to_expiry_days >= 0),
            point_hash CHAR(64) NOT NULL,
            PRIMARY KEY(curve_id, sequence),
            UNIQUE(curve_id, instrument_id),
            UNIQUE(curve_id, normalized_observation_id)
        )""",
        # A point's frozen snapshot values may never diverge from the canonical
        # settlement observation they were copied from, and a point's contract
        # may never belong to a series other than its own curve's series.
        """CREATE FUNCTION require_term_structure_point_matches_canonical() RETURNS trigger AS $$
            DECLARE canonical_price NUMERIC(38,18);
            DECLARE canonical_finality TEXT;
            DECLARE contract_series TEXT;
            DECLARE curve_series TEXT;
            BEGIN
                SELECT f.settlement_price, f.finality INTO canonical_price, canonical_finality
                FROM futures_settlement_observations f
                WHERE f.normalized_observation_id = NEW.normalized_observation_id;
                IF canonical_price IS NULL THEN
                    RAISE EXCEPTION
                        'term structure point references a non-settlement observation %',
                        NEW.normalized_observation_id;
                END IF;
                IF canonical_price <> NEW.settlement_price THEN
                    RAISE EXCEPTION
                        'term structure point price % diverges from canonical settlement price %',
                        NEW.settlement_price, canonical_price;
                END IF;
                IF canonical_finality IS DISTINCT FROM NEW.settlement_finality THEN
                    RAISE EXCEPTION
                        'term structure point finality % diverges from canonical settlement finality %',
                        NEW.settlement_finality, canonical_finality;
                END IF;
                SELECT c.series_id INTO contract_series FROM futures_contract_specifications c
                WHERE c.instrument_id = NEW.instrument_id;
                SELECT t.series_id INTO curve_series FROM futures_term_structure_curves t
                WHERE t.curve_id = NEW.curve_id;
                IF contract_series IS DISTINCT FROM curve_series THEN
                    RAISE EXCEPTION
                        'term structure point contract series % does not match curve series %',
                        contract_series, curve_series;
                END IF;
                RETURN NULL;
            END; $$ LANGUAGE plpgsql""",
        """CREATE CONSTRAINT TRIGGER futures_term_structure_point_matches_canonical
            AFTER INSERT ON futures_term_structure_points
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION require_term_structure_point_matches_canonical()""",
    )
    for statement in statements:
        op.execute(statement)
    for table in NEW_TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS futures_term_structure_point_matches_canonical "
        "ON futures_term_structure_points"
    )
    op.execute("DROP FUNCTION IF EXISTS require_term_structure_point_matches_canonical()")
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP TABLE IF EXISTS futures_term_structure_points")
    op.execute("DROP TABLE IF EXISTS futures_term_structure_curves")
    op.execute("DROP TABLE IF EXISTS futures_term_structure_methods")
