"""add futures settlement and open-interest observation authority

Revision ID: 20260907_0043
Revises: 20260907_0042
Create Date: 2026-09-07

Module 3I.1 (roadmap NEXT-03 phase 1). Extends the EXISTING historical
market-data pipeline -- no second source registry, dataset registry, Data
Health system or observation envelope is created.

Four things happen here:

1. **Two new observation kinds** (SETTLEMENT_PRICE, OPEN_INTEREST) widen the
   existing ``historical_raw_observations`` kind CHECK, and the FUTURES asset
   scope widens the ``historical_data_sources`` scope CHECK, which previously
   hard-coded ``= 'US_EQUITIES_ETFS'``.

2. **Source capability authorization.** A new ``historical_source_capabilities``
   child table binds a source to the exact observation kinds it may write, and
   a composite foreign key from ``historical_raw_observations`` makes the
   database itself refuse an unauthorized kind. A source authorized for OHLCV
   does not gain SETTLEMENT_PRICE authority by sharing an asset class. This is
   a child of the existing registry, not a parallel one.

3. **Typed canonical payload tables**, one per new kind. These are the ONLY
   durable authority for the normalized financial value; the envelope's
   ``normalized_value`` keeps a non-financial pointer marker instead of a
   second copy. One-to-one integrity is enforced in both directions: the
   typed row's primary key is a foreign key to the envelope, and a DEFERRABLE
   constraint trigger on the envelope requires the typed row by COMMIT.

4. **Data Health gains observation-kind and source identity dimensions**,
   exactly as migration 0039 added ``interval`` -- so settlement, open
   interest and OHLCV health for one instrument are independently tracked
   rather than colliding on one uniqueness constraint.

Adjustment semantics are constrained rather than inherited: equity-oriented
POINT_IN_TIME_ADJUSTED / LATEST_ADJUSTED have no defined meaning for a
settlement price or an open-interest count, so a CHECK restricts the new kinds
to RAW / AS_REPORTED instead of letting those values acquire undefined meaning.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260907_0043"
down_revision = "20260907_0042"
branch_labels = None
depends_on = None


NEW_TABLES = (
    "historical_source_capabilities",
    "futures_settlement_observations",
    "futures_open_interest_observations",
)

_LEGACY_KINDS = "'OHLCV','DIVIDEND','SPLIT','SYMBOL_CHANGE','DELISTING'"
_TYPED_KINDS = "'SETTLEMENT_PRICE','OPEN_INTEREST'"
_ALL_KINDS = f"{_LEGACY_KINDS},{_TYPED_KINDS}"
_SCOPES = "'US_EQUITIES_ETFS','FUTURES'"
_ADJUSTMENTS = "'RAW','AS_REPORTED','POINT_IN_TIME_ADJUSTED','LATEST_ADJUSTED'"
_OLD_HEALTH_UNIQUE = "data_health_assessments_scope_interval_evaluated_at_key"
_NEW_HEALTH_UNIQUE = "data_health_assessments_scope_series_evaluated_at_key"


def upgrade() -> None:
    statements = (
        # ---- 1. widened enumerations ---------------------------------------
        (
            "ALTER TABLE historical_data_sources DROP CONSTRAINT IF EXISTS "
            "historical_data_sources_asset_scope_check"
        ),
        (
            "ALTER TABLE historical_data_sources ADD CONSTRAINT "
            f"historical_source_asset_scope_check CHECK(asset_scope IN ({_SCOPES}))"
        ),
        (
            "ALTER TABLE historical_raw_observations DROP CONSTRAINT IF EXISTS "
            "historical_raw_observations_observation_kind_check"
        ),
        (
            "ALTER TABLE historical_raw_observations ADD CONSTRAINT "
            f"historical_raw_observation_kind_check CHECK(observation_kind IN ({_ALL_KINDS}))"
        ),
        (
            "ALTER TABLE historical_raw_observations ADD CONSTRAINT "
            "historical_raw_typed_kind_adjustment_check CHECK("
            f"observation_kind NOT IN ({_TYPED_KINDS}) "
            "OR adjustment_status IN ('RAW','AS_REPORTED'))"
        ),
        # ---- 2. source capability authorization -----------------------------
        f"""CREATE TABLE historical_source_capabilities (
            source_id UUID NOT NULL REFERENCES historical_data_sources(source_id),
            observation_kind TEXT NOT NULL CHECK(observation_kind IN ({_ALL_KINDS})),
            authorized_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY(source_id, observation_kind)
        )""",
        # Preserve exactly the authority existing sources already exercised, so
        # the composite foreign key below can be added without invalidating any
        # row this repository has already written. No authority is widened: a
        # source gets a capability only for a kind it has actually captured.
        """INSERT INTO historical_source_capabilities (source_id, observation_kind, authorized_at)
            SELECT DISTINCT r.source_id, r.observation_kind, s.authorized_at
            FROM historical_raw_observations r
            JOIN historical_data_sources s ON s.source_id = r.source_id
            ON CONFLICT DO NOTHING""",
        (
            "ALTER TABLE historical_raw_observations ADD CONSTRAINT "
            "historical_raw_source_capability_fkey "
            "FOREIGN KEY (source_id, observation_kind) "
            "REFERENCES historical_source_capabilities(source_id, observation_kind)"
        ),
        # ---- 3. typed canonical payload tables ------------------------------
        """CREATE TABLE futures_settlement_observations (
            normalized_observation_id UUID PRIMARY KEY
                REFERENCES historical_normalized_observations(normalized_observation_id),
            settlement_price NUMERIC(38,18) NOT NULL CHECK(settlement_price > 0),
            price_currency VARCHAR(12) NOT NULL CHECK(LENGTH(price_currency) BETWEEN 3 AND 12),
            settlement_date DATE NOT NULL,
            settlement_effective_at TIMESTAMPTZ NOT NULL,
            finality TEXT NOT NULL CHECK(finality IN ('PRELIMINARY','FINAL')),
            quote_unit TEXT NOT NULL CHECK(LENGTH(TRIM(quote_unit)) > 0)
        )""",
        """CREATE TABLE futures_open_interest_observations (
            normalized_observation_id UUID PRIMARY KEY
                REFERENCES historical_normalized_observations(normalized_observation_id),
            open_interest NUMERIC(38,18) NOT NULL CHECK(open_interest >= 0),
            unit TEXT NOT NULL CHECK(unit IN ('CONTRACTS','BASE_ASSET','QUOTE_NOTIONAL')),
            unit_asset VARCHAR(12),
            observed_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT open_interest_unit_asset_coherence CHECK(
                (unit = 'CONTRACTS' AND unit_asset IS NULL)
                OR (unit <> 'CONTRACTS' AND unit_asset IS NOT NULL)
            )
        )""",
        # A typed payload may only attach to an envelope of its own kind, so a
        # settlement row can never be hung off an OHLCV observation.
        """CREATE FUNCTION require_matching_observation_kind() RETURNS trigger AS $$
            DECLARE actual TEXT;
            BEGIN
                SELECT r.observation_kind INTO actual
                FROM historical_normalized_observations n
                JOIN historical_raw_observations r
                  ON r.raw_observation_id = n.raw_observation_id
                WHERE n.normalized_observation_id = NEW.normalized_observation_id;
                IF actual IS DISTINCT FROM TG_ARGV[0] THEN
                    RAISE EXCEPTION
                        'typed payload % cannot attach to observation kind %',
                        TG_TABLE_NAME, COALESCE(actual, 'MISSING');
                END IF;
                RETURN NULL;
            END; $$ LANGUAGE plpgsql""",
        """CREATE CONSTRAINT TRIGGER futures_settlement_kind_match
            AFTER INSERT ON futures_settlement_observations
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION require_matching_observation_kind('SETTLEMENT_PRICE')""",
        """CREATE CONSTRAINT TRIGGER futures_open_interest_kind_match
            AFTER INSERT ON futures_open_interest_observations
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION require_matching_observation_kind('OPEN_INTEREST')""",
        # ...and an envelope of a typed kind may not exist without its payload.
        # Deferred to COMMIT because the pipeline writes envelope then payload
        # in one transaction.
        """CREATE FUNCTION require_typed_payload_for_kind() RETURNS trigger AS $$
            DECLARE kind TEXT;
            BEGIN
                SELECT r.observation_kind INTO kind
                FROM historical_raw_observations r
                WHERE r.raw_observation_id = NEW.raw_observation_id;
                IF kind = 'SETTLEMENT_PRICE' AND NOT EXISTS (
                    SELECT 1 FROM futures_settlement_observations f
                    WHERE f.normalized_observation_id = NEW.normalized_observation_id
                ) THEN
                    RAISE EXCEPTION
                        'settlement observation requires its canonical typed payload';
                END IF;
                IF kind = 'OPEN_INTEREST' AND NOT EXISTS (
                    SELECT 1 FROM futures_open_interest_observations o
                    WHERE o.normalized_observation_id = NEW.normalized_observation_id
                ) THEN
                    RAISE EXCEPTION
                        'open interest observation requires its canonical typed payload';
                END IF;
                RETURN NULL;
            END; $$ LANGUAGE plpgsql""",
        """CREATE CONSTRAINT TRIGGER historical_normalized_requires_typed_payload
            AFTER INSERT ON historical_normalized_observations
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION require_typed_payload_for_kind()""",
        # ---- 4. Data Health series identity ---------------------------------
        "ALTER TABLE data_health_assessments ADD COLUMN observation_kind TEXT NOT NULL DEFAULT ''",
        (
            "ALTER TABLE data_health_assessments ADD COLUMN source_id UUID "
            "REFERENCES historical_data_sources(source_id)"
        ),
        f"ALTER TABLE data_health_assessments DROP CONSTRAINT IF EXISTS {_OLD_HEALTH_UNIQUE}",
        (
            f"ALTER TABLE data_health_assessments ADD CONSTRAINT {_NEW_HEALTH_UNIQUE} "
            "UNIQUE (scope_type, scope_value, interval, observation_kind, evaluated_at)"
        ),
    )
    for statement in statements:
        op.execute(statement)
    for table in NEW_TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE data_health_assessments DROP CONSTRAINT IF EXISTS {_NEW_HEALTH_UNIQUE}"
    )
    op.execute(
        f"ALTER TABLE data_health_assessments ADD CONSTRAINT {_OLD_HEALTH_UNIQUE} "
        "UNIQUE (scope_type, scope_value, interval, evaluated_at)"
    )
    op.execute(
        "ALTER TABLE data_health_assessments "
        "DROP COLUMN IF EXISTS observation_kind, DROP COLUMN IF EXISTS source_id"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS historical_normalized_requires_typed_payload "
        "ON historical_normalized_observations"
    )
    op.execute("DROP FUNCTION IF EXISTS require_typed_payload_for_kind()")
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP TABLE IF EXISTS futures_open_interest_observations")
    op.execute("DROP TABLE IF EXISTS futures_settlement_observations")
    op.execute("DROP FUNCTION IF EXISTS require_matching_observation_kind()")
    op.execute(
        "ALTER TABLE historical_raw_observations DROP CONSTRAINT IF EXISTS "
        "historical_raw_source_capability_fkey"
    )
    op.execute("DROP TABLE IF EXISTS historical_source_capabilities")
    op.execute(
        "ALTER TABLE historical_raw_observations DROP CONSTRAINT IF EXISTS "
        "historical_raw_typed_kind_adjustment_check"
    )
    op.execute(
        "ALTER TABLE historical_raw_observations DROP CONSTRAINT IF EXISTS "
        "historical_raw_observation_kind_check"
    )
    op.execute(
        "ALTER TABLE historical_raw_observations ADD CONSTRAINT "
        "historical_raw_observations_observation_kind_check "
        f"CHECK(observation_kind IN ({_LEGACY_KINDS}))"
    )
    op.execute(
        "ALTER TABLE historical_data_sources DROP CONSTRAINT IF EXISTS "
        "historical_source_asset_scope_check"
    )
    op.execute(
        "ALTER TABLE historical_data_sources ADD CONSTRAINT "
        "historical_data_sources_asset_scope_check CHECK(asset_scope = 'US_EQUITIES_ETFS')"
    )
