"""add crypto funding, mark/index and cross-asset open-interest observation authority

Revision ID: 20260907_0044
Revises: 20260907_0043
Create Date: 2026-09-07

Module 3I.2 (roadmap NEXT-03 phase 2). Extends the EXISTING historical
market-data pipeline again -- no second source registry, dataset registry, Data
Health system or observation envelope is created, and no crypto open-interest
pipeline is created either.

Five things happen here:

1. **Four new observation kinds.** ``FUNDING_RATE_REALIZED``,
   ``FUNDING_RATE_INDICATIVE``, ``MARK_PRICE`` and ``INDEX_PRICE`` widen the
   kind CHECK on ``historical_raw_observations`` and on the 3I.1
   ``historical_source_capabilities`` child table, and a new ``CRYPTO`` asset
   scope widens the source scope CHECK. Realized and indicative funding are two
   kinds rather than one kind with a status column, because what a venue applied
   and what it estimated it would apply are different evidence; the composite
   capability foreign key therefore authorizes them independently.

2. **The open-interest table is generalized, and its canonical identity is
   not.** ``futures_open_interest_observations`` is renamed to
   ``open_interest_observations``. The semantics were always cross-asset -- its
   ``unit`` CHECK has accepted ``BASE_ASSET`` and ``QUOTE_NOTIONAL`` since
   3I.1 -- and only the physical name was futures-specific, which would have
   become a lie the moment a crypto perpetual's open interest was written to it.
   This is a pure ``ALTER TABLE ... RENAME``: every existing row, primary key,
   foreign key, CHECK and immutability guarantee survives it untouched.

   Crucially, the *canonical payload identity* used in sealed dataset content
   hashes and in ``normalized_value``'s pointer marker is frozen at the old
   string (``market_observation_payloads.OPEN_INTEREST_CANONICAL_PAYLOAD_IDENTITY``),
   so generalizing a storage name changed the identity of exactly zero
   already-sealed datasets. A 3I.1 futures open-interest dataset reproduces
   bit-identically after this migration.

3. **Two new typed canonical payload tables.** ``crypto_funding_observations``
   serves both funding kinds and ``crypto_reference_price_observations`` serves
   both reference-price kinds: the financial shape within each pair is
   identical, while the envelope kind remains the sole authority for meaning.
   Neither table stores a discriminator column -- a second stored authority
   could disagree with the envelope -- and the constraint trigger below refuses
   a payload attached to an envelope outside its own kind set, so the two kinds
   sharing a table can never be mixed.

4. **The kind-match trigger becomes set-valued.** ``require_matching_observation_kind``
   previously compared against a single ``TG_ARGV[0]``; it now accepts any
   number of permitted kinds so one shared table can serve a kind pair without
   loosening anything: a mark-price row still cannot attach to an OHLCV,
   settlement or funding envelope.

5. **The typed-payload requirement trigger covers the new kinds**, so an
   envelope of any typed kind still cannot reach COMMIT without its canonical
   payload row.

Adjustment semantics stay constrained rather than inherited: equity-oriented
POINT_IN_TIME_ADJUSTED / LATEST_ADJUSTED have no defined meaning for a funding
rate, a mark price, an index price or an open-interest count either, so the
existing CHECK is widened to hold all six typed kinds to RAW / AS_REPORTED.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260907_0044"
down_revision = "20260907_0043"
branch_labels = None
depends_on = None


NEW_TABLES = (
    "crypto_funding_observations",
    "crypto_reference_price_observations",
)

_LEGACY_KINDS = "'OHLCV','DIVIDEND','SPLIT','SYMBOL_CHANGE','DELISTING'"
_TYPED_KINDS_3I1 = "'SETTLEMENT_PRICE','OPEN_INTEREST'"
_TYPED_KINDS_3I2 = (
    "'FUNDING_RATE_REALIZED','FUNDING_RATE_INDICATIVE','MARK_PRICE','INDEX_PRICE'"
)
_ALL_TYPED_KINDS = f"{_TYPED_KINDS_3I1},{_TYPED_KINDS_3I2}"
_ALL_KINDS_3I1 = f"{_LEGACY_KINDS},{_TYPED_KINDS_3I1}"
_ALL_KINDS = f"{_LEGACY_KINDS},{_ALL_TYPED_KINDS}"
_SCOPES_3I1 = "'US_EQUITIES_ETFS','FUTURES'"
_SCOPES = f"{_SCOPES_3I1},'CRYPTO'"

_KIND_MATCH_FUNCTION = """CREATE OR REPLACE FUNCTION require_matching_observation_kind()
    RETURNS trigger AS $$
    DECLARE actual TEXT;
    BEGIN
        SELECT r.observation_kind INTO actual
        FROM historical_normalized_observations n
        JOIN historical_raw_observations r
          ON r.raw_observation_id = n.raw_observation_id
        WHERE n.normalized_observation_id = NEW.normalized_observation_id;
        IF actual IS NULL OR NOT (actual = ANY(TG_ARGV)) THEN
            RAISE EXCEPTION
                'typed payload % cannot attach to observation kind %',
                TG_TABLE_NAME, COALESCE(actual, 'MISSING');
        END IF;
        RETURN NULL;
    END; $$ LANGUAGE plpgsql"""

_SINGLE_KIND_MATCH_FUNCTION = """CREATE OR REPLACE FUNCTION require_matching_observation_kind()
    RETURNS trigger AS $$
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
    END; $$ LANGUAGE plpgsql"""


def _typed_payload_requirement(open_interest_table: str, *, include_crypto: bool) -> str:
    """The envelope-side trigger body, parameterised by what exists at this revision."""
    crypto = (
        """
                IF kind IN ('FUNDING_RATE_REALIZED','FUNDING_RATE_INDICATIVE')
                   AND NOT EXISTS (
                    SELECT 1 FROM crypto_funding_observations f
                    WHERE f.normalized_observation_id = NEW.normalized_observation_id
                ) THEN
                    RAISE EXCEPTION
                        'funding observation requires its canonical typed payload';
                END IF;
                IF kind IN ('MARK_PRICE','INDEX_PRICE') AND NOT EXISTS (
                    SELECT 1 FROM crypto_reference_price_observations p
                    WHERE p.normalized_observation_id = NEW.normalized_observation_id
                ) THEN
                    RAISE EXCEPTION
                        'reference price observation requires its canonical typed payload';
                END IF;"""
        if include_crypto
        else ""
    )
    return f"""CREATE OR REPLACE FUNCTION require_typed_payload_for_kind() RETURNS trigger AS $$
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
                    SELECT 1 FROM {open_interest_table} o
                    WHERE o.normalized_observation_id = NEW.normalized_observation_id
                ) THEN
                    RAISE EXCEPTION
                        'open interest observation requires its canonical typed payload';
                END IF;{crypto}
                RETURN NULL;
            END; $$ LANGUAGE plpgsql"""


def upgrade() -> None:
    statements = (
        # ---- 1. widened enumerations ---------------------------------------
        (
            "ALTER TABLE historical_data_sources DROP CONSTRAINT IF EXISTS "
            "historical_source_asset_scope_check"
        ),
        (
            "ALTER TABLE historical_data_sources ADD CONSTRAINT "
            f"historical_source_asset_scope_check CHECK(asset_scope IN ({_SCOPES}))"
        ),
        (
            "ALTER TABLE historical_raw_observations DROP CONSTRAINT IF EXISTS "
            "historical_raw_observation_kind_check"
        ),
        (
            "ALTER TABLE historical_raw_observations ADD CONSTRAINT "
            f"historical_raw_observation_kind_check CHECK(observation_kind IN ({_ALL_KINDS}))"
        ),
        (
            "ALTER TABLE historical_raw_observations DROP CONSTRAINT IF EXISTS "
            "historical_raw_typed_kind_adjustment_check"
        ),
        (
            "ALTER TABLE historical_raw_observations ADD CONSTRAINT "
            "historical_raw_typed_kind_adjustment_check CHECK("
            f"observation_kind NOT IN ({_ALL_TYPED_KINDS}) "
            "OR adjustment_status IN ('RAW','AS_REPORTED'))"
        ),
        (
            "ALTER TABLE historical_source_capabilities DROP CONSTRAINT IF EXISTS "
            "historical_source_capabilities_observation_kind_check"
        ),
        (
            "ALTER TABLE historical_source_capabilities ADD CONSTRAINT "
            "historical_source_capability_kind_check "
            f"CHECK(observation_kind IN ({_ALL_KINDS}))"
        ),
        # ---- 2. one cross-asset open-interest authority ---------------------
        # A rename, not a copy: rows, keys, CHECKs and the immutability trigger
        # all carry over, so no historical evidence is rewritten or re-derived.
        "ALTER TABLE futures_open_interest_observations RENAME TO open_interest_observations",
        (
            "ALTER TRIGGER futures_open_interest_observations_immutable "
            "ON open_interest_observations RENAME TO open_interest_observations_immutable"
        ),
        (
            "ALTER TRIGGER futures_open_interest_kind_match "
            "ON open_interest_observations RENAME TO open_interest_kind_match"
        ),
        (
            "COMMENT ON TABLE open_interest_observations IS "
            "'Cross-asset open-interest canonical payloads (Module 3I.2). Renamed from "
            "futures_open_interest_observations, which remains the frozen canonical payload "
            "identity token in sealed dataset content hashes and in normalized_value markers "
            "so that generalizing this name changed no historical dataset identity.'"
        ),
        # ---- 3. typed canonical payload tables ------------------------------
        # One funding table for both funding kinds. No REALIZED/INDICATIVE
        # column: the envelope's observation_kind is the sole authority, and a
        # stored discriminator would be a second one that could disagree.
        """CREATE TABLE crypto_funding_observations (
            normalized_observation_id UUID PRIMARY KEY
                REFERENCES historical_normalized_observations(normalized_observation_id),
            funding_rate NUMERIC(20,12) NOT NULL,
            target_funding_at TIMESTAMPTZ NOT NULL,
            published_at TIMESTAMPTZ NOT NULL,
            settlement_asset VARCHAR(12) NOT NULL
                CHECK(LENGTH(settlement_asset) BETWEEN 2 AND 12),
            convention_id UUID NOT NULL
                REFERENCES crypto_funding_conventions(convention_id),
            convention_version INTEGER NOT NULL CHECK(convention_version >= 1)
        )""",
        # A funding rate is legitimately negative -- shorts paying longs -- so
        # no sign CHECK exists here. Magnitude is judged only against the
        # explicit floor/cap of the convention visible at the observation's own
        # two clocks, which is a per-row comparison Python performs and this
        # table records the identity of.
        """CREATE TABLE crypto_reference_price_observations (
            normalized_observation_id UUID PRIMARY KEY
                REFERENCES historical_normalized_observations(normalized_observation_id),
            price NUMERIC(38,18) NOT NULL CHECK(price > 0),
            price_asset VARCHAR(12) NOT NULL CHECK(LENGTH(price_asset) BETWEEN 2 AND 12),
            observed_at TIMESTAMPTZ NOT NULL,
            methodology_reference TEXT
                CHECK(methodology_reference IS NULL
                      OR LENGTH(TRIM(methodology_reference)) > 0)
        )""",
        # ---- 4. set-valued kind matching ------------------------------------
        _KIND_MATCH_FUNCTION,
        """CREATE CONSTRAINT TRIGGER crypto_funding_kind_match
            AFTER INSERT ON crypto_funding_observations
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION require_matching_observation_kind(
                'FUNDING_RATE_REALIZED', 'FUNDING_RATE_INDICATIVE')""",
        """CREATE CONSTRAINT TRIGGER crypto_reference_price_kind_match
            AFTER INSERT ON crypto_reference_price_observations
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION require_matching_observation_kind('MARK_PRICE', 'INDEX_PRICE')""",
        # ---- 5. every typed kind still requires its payload by COMMIT --------
        _typed_payload_requirement("open_interest_observations", include_crypto=True),
    )
    for statement in statements:
        op.execute(statement)
    for table in NEW_TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    op.execute(
        _typed_payload_requirement("futures_open_interest_observations", include_crypto=False)
    )
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP TABLE IF EXISTS crypto_reference_price_observations")
    op.execute("DROP TABLE IF EXISTS crypto_funding_observations")
    op.execute(_SINGLE_KIND_MATCH_FUNCTION)
    op.execute(
        "ALTER TRIGGER open_interest_kind_match ON open_interest_observations "
        "RENAME TO futures_open_interest_kind_match"
    )
    op.execute(
        "ALTER TRIGGER open_interest_observations_immutable ON open_interest_observations "
        "RENAME TO futures_open_interest_observations_immutable"
    )
    op.execute(
        "ALTER TABLE open_interest_observations RENAME TO futures_open_interest_observations"
    )
    op.execute(
        "ALTER TABLE historical_source_capabilities DROP CONSTRAINT IF EXISTS "
        "historical_source_capability_kind_check"
    )
    op.execute(
        "ALTER TABLE historical_source_capabilities ADD CONSTRAINT "
        "historical_source_capabilities_observation_kind_check "
        f"CHECK(observation_kind IN ({_ALL_KINDS_3I1}))"
    )
    op.execute(
        "ALTER TABLE historical_raw_observations DROP CONSTRAINT IF EXISTS "
        "historical_raw_typed_kind_adjustment_check"
    )
    op.execute(
        "ALTER TABLE historical_raw_observations ADD CONSTRAINT "
        "historical_raw_typed_kind_adjustment_check CHECK("
        f"observation_kind NOT IN ({_TYPED_KINDS_3I1}) "
        "OR adjustment_status IN ('RAW','AS_REPORTED'))"
    )
    op.execute(
        "ALTER TABLE historical_raw_observations DROP CONSTRAINT IF EXISTS "
        "historical_raw_observation_kind_check"
    )
    op.execute(
        "ALTER TABLE historical_raw_observations ADD CONSTRAINT "
        f"historical_raw_observation_kind_check CHECK(observation_kind IN ({_ALL_KINDS_3I1}))"
    )
    op.execute(
        "ALTER TABLE historical_data_sources DROP CONSTRAINT IF EXISTS "
        "historical_source_asset_scope_check"
    )
    op.execute(
        "ALTER TABLE historical_data_sources ADD CONSTRAINT "
        f"historical_source_asset_scope_check CHECK(asset_scope IN ({_SCOPES_3I1}))"
    )
