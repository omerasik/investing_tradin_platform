"""add first-class crypto instrument, funding-convention and venue-rule authority

Revision ID: 20260907_0042
Revises: 20260907_0041
Create Date: 2026-09-07

Module 3H.2 (roadmap NEXT-02 phase 2). Like 3H.1 these are SIBLING tables keyed
by ``professional_instruments.instrument_id``; ``professional_instruments``
remains the single canonical instrument registry and its positional
INSERT/read path in ``PostgresProfessionalInstrumentMaster`` is untouched.

Two in-place changes to existing objects, neither of which alters column count
or ordering (so the positional ``INSERT INTO professional_instruments
VALUES (<31 placeholders>)`` and ``_instrument_from_row()``'s indices are
unaffected):

1. The three currency columns widen from CHAR(3) to VARCHAR(12). ISO 4217 does
   not cover crypto assets, so a three-character column cannot represent USDT,
   USDC or most token tickers. Python-side validation stays strictly ISO-4217
   three-letter for every non-crypto asset class (see
   ``professional_instruments._require_asset_code``), so this widening cannot
   silently loosen equity, ETF, FX or futures instruments. CHAR(3) also
   space-pads; VARCHAR does not, which is why existing values are trimmed
   during the conversion rather than left with trailing blanks.

2. ``representation_kind`` admits PERPETUAL. A perpetual swap tracks spot via a
   periodic funding payment instead of converging to it at an expiry, so it is
   neither SPOT nor FUTURE and forcing it into either would misstate what the
   instrument is.

Deliberately NOT in this migration: funding-rate, mark-price, index-price,
open-interest, trade and order-book observations. Those are time-series market
data and belong to the NEXT-03 multi-asset data authority. This migration
stores only what a contract *is* and what reference-price/funding semantics it
*requires*.

Also deliberately absent: margin and leverage tiers. On a crypto venue those
are continuously-revised, position-size- and account-dependent state, not
static instrument identity; modelling them here would invent an authority that
belongs to the account/risk layer (NEXT-10/NEXT-11).
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260907_0042"
down_revision = "20260907_0041"
branch_labels = None
depends_on = None


TABLES = (
    "crypto_instrument_specifications",
    "crypto_funding_conventions",
    "crypto_venue_trading_rules",
)

_KINDS = "('SPOT','PERPETUAL','DATED_FUTURE')"
_STYLES = "('LINEAR','INVERSE','QUANTO')"
_SETTLEMENT = "('PHYSICAL_DELIVERY','CASH_SETTLED')"
_REFERENCE = "('NONE','INDEX_ONLY','MARK_AND_INDEX')"
_REPRESENTATIONS = "('DIRECT','ETF_PROXY','SPOT','FUTURE','PERPETUAL')"


def upgrade() -> None:
    statements = (
        (
            "ALTER TABLE professional_instruments "
            "ALTER COLUMN base_currency TYPE VARCHAR(12) USING TRIM(base_currency), "
            "ALTER COLUMN quote_currency TYPE VARCHAR(12) USING TRIM(quote_currency), "
            "ALTER COLUMN settlement_currency TYPE VARCHAR(12) USING TRIM(settlement_currency)"
        ),
        # PostgresProfessionalInstrumentMaster.register() writes runtime_instruments
        # in the same transaction, so widening only professional_instruments would
        # still reject USDT at the first statement.
        (
            "ALTER TABLE runtime_instruments "
            "ALTER COLUMN quote_currency TYPE VARCHAR(12) USING TRIM(quote_currency)"
        ),
        (
            "ALTER TABLE professional_instruments DROP CONSTRAINT IF EXISTS "
            "professional_instruments_representation_kind_check"
        ),
        (
            "ALTER TABLE professional_instruments ADD CONSTRAINT "
            "professional_instrument_representation_kind_check "
            f"CHECK(representation_kind IN {_REPRESENTATIONS})"
        ),
        # Static contract identity only. Everything a venue revises over time
        # (tick size, quantity step, minimum notional) lives in
        # crypto_venue_trading_rules with its own two clocks.
        f"""CREATE TABLE crypto_instrument_specifications (
            instrument_id TEXT PRIMARY KEY REFERENCES professional_instruments(instrument_id),
            venue TEXT NOT NULL,
            crypto_instrument_kind TEXT NOT NULL CHECK(crypto_instrument_kind IN {_KINDS}),
            base_asset VARCHAR(12) NOT NULL,
            quote_asset VARCHAR(12) NOT NULL,
            settlement_asset VARCHAR(12),
            settlement_style TEXT CHECK(settlement_style IN {_STYLES}),
            settlement_type TEXT NOT NULL CHECK(settlement_type IN {_SETTLEMENT}),
            contract_multiplier NUMERIC(38,18) NOT NULL CHECK(contract_multiplier > 0),
            contract_size NUMERIC(38,18) NOT NULL CHECK(contract_size > 0),
            expiry_at TIMESTAMPTZ,
            reference_price_requirement TEXT NOT NULL
                CHECK(reference_price_requirement IN {_REFERENCE}),
            index_reference TEXT,
            registered_at TIMESTAMPTZ NOT NULL,
            source_reference TEXT NOT NULL,
            CONSTRAINT crypto_base_and_quote_differ CHECK(base_asset <> quote_asset),
            -- A spot pair exchanges both legs, so it has no single settlement
            -- asset and no linear/inverse/quanto sense; a derivative always has
            -- both. Expiry is required by exactly one kind and forbidden by the
            -- other two.
            CONSTRAINT crypto_spot_has_no_derivative_fields CHECK(
                (crypto_instrument_kind = 'SPOT'
                    AND settlement_asset IS NULL AND settlement_style IS NULL
                    AND expiry_at IS NULL AND reference_price_requirement = 'NONE')
                OR (crypto_instrument_kind <> 'SPOT'
                    AND settlement_asset IS NOT NULL AND settlement_style IS NOT NULL
                    AND reference_price_requirement <> 'NONE')
            ),
            CONSTRAINT crypto_expiry_matches_kind CHECK(
                (crypto_instrument_kind = 'DATED_FUTURE' AND expiry_at IS NOT NULL)
                OR (crypto_instrument_kind <> 'DATED_FUTURE' AND expiry_at IS NULL)
            ),
            -- Linear settles in the quote asset, inverse in the base asset,
            -- quanto in a third asset. Anything else is an incoherent contract.
            CONSTRAINT crypto_settlement_style_asset_coherence CHECK(
                settlement_style IS NULL
                OR (settlement_style = 'LINEAR' AND settlement_asset = quote_asset)
                OR (settlement_style = 'INVERSE' AND settlement_asset = base_asset)
                OR (settlement_style = 'QUANTO'
                    AND settlement_asset <> quote_asset AND settlement_asset <> base_asset)
            )
        )""",
        # NULL expiry would defeat a plain UNIQUE, so the perpetual/spot and
        # dated cases get separate partial indexes.
        (
            "CREATE UNIQUE INDEX crypto_undated_identity_idx ON crypto_instrument_specifications"
            "(venue, base_asset, quote_asset, crypto_instrument_kind) WHERE expiry_at IS NULL"
        ),
        (
            "CREATE UNIQUE INDEX crypto_dated_identity_idx ON crypto_instrument_specifications"
            "(venue, base_asset, quote_asset, crypto_instrument_kind, expiry_at) "
            "WHERE expiry_at IS NOT NULL"
        ),
        (
            "CREATE INDEX crypto_specification_venue_idx ON crypto_instrument_specifications"
            "(venue, crypto_instrument_kind, registered_at)"
        ),
        # Convention/schedule metadata only -- never an observed funding rate.
        # effective_from and known_at are independent: a venue announces a
        # schedule change before it applies, and a backfill records it after.
        """CREATE TABLE crypto_funding_conventions (
            convention_id UUID PRIMARY KEY,
            instrument_id TEXT NOT NULL
                REFERENCES crypto_instrument_specifications(instrument_id),
            convention_version INTEGER NOT NULL CHECK(convention_version >= 1),
            interval_hours NUMERIC(9,6) NOT NULL CHECK(interval_hours > 0),
            first_funding_offset_hours NUMERIC(9,6) NOT NULL
                CHECK(first_funding_offset_hours >= 0),
            funding_rate_floor NUMERIC(20,12),
            funding_rate_cap NUMERIC(20,12),
            funding_settlement_asset VARCHAR(12) NOT NULL,
            effective_from TIMESTAMPTZ NOT NULL,
            known_at TIMESTAMPTZ NOT NULL,
            source_reference TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            UNIQUE(instrument_id, convention_version),
            UNIQUE(instrument_id, effective_from, known_at),
            CHECK(first_funding_offset_hours < interval_hours),
            CHECK(funding_rate_floor IS NULL OR funding_rate_cap IS NULL
                  OR funding_rate_floor <= funding_rate_cap)
        )""",
        (
            "CREATE INDEX crypto_funding_convention_pit_idx ON crypto_funding_conventions"
            "(instrument_id, effective_from DESC, known_at DESC)"
        ),
        # The venue-revised half of a crypto contract. Two clocks for the same
        # reason as funding conventions.
        """CREATE TABLE crypto_venue_trading_rules (
            rule_id UUID PRIMARY KEY,
            instrument_id TEXT NOT NULL
                REFERENCES crypto_instrument_specifications(instrument_id),
            rule_version INTEGER NOT NULL CHECK(rule_version >= 1),
            tick_size NUMERIC(38,18) NOT NULL CHECK(tick_size > 0),
            quantity_step NUMERIC(38,18) NOT NULL CHECK(quantity_step > 0),
            min_quantity NUMERIC(38,18) NOT NULL CHECK(min_quantity > 0),
            max_quantity NUMERIC(38,18),
            min_notional NUMERIC(38,18),
            price_precision SMALLINT NOT NULL CHECK(price_precision BETWEEN 0 AND 18),
            quantity_precision SMALLINT NOT NULL CHECK(quantity_precision BETWEEN 0 AND 18),
            effective_from TIMESTAMPTZ NOT NULL,
            known_at TIMESTAMPTZ NOT NULL,
            source_reference TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            UNIQUE(instrument_id, rule_version),
            UNIQUE(instrument_id, effective_from, known_at),
            CHECK(max_quantity IS NULL OR max_quantity >= min_quantity),
            CHECK(min_notional IS NULL OR min_notional > 0)
        )""",
        (
            "CREATE INDEX crypto_venue_rule_pit_idx ON crypto_venue_trading_rules"
            "(instrument_id, effective_from DESC, known_at DESC)"
        ),
    )
    for statement in statements:
        op.execute(statement)
    for table in TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute(
        "ALTER TABLE professional_instruments DROP CONSTRAINT IF EXISTS "
        "professional_instrument_representation_kind_check"
    )
    op.execute(
        "ALTER TABLE professional_instruments ADD CONSTRAINT "
        "professional_instruments_representation_kind_check "
        "CHECK(representation_kind IN ('DIRECT','ETF_PROXY','SPOT','FUTURE'))"
    )
    # Narrowing back to CHAR(3) would truncate any crypto asset code longer
    # than three characters, so this reversal only succeeds while no such row
    # exists -- which is the honest behaviour for an irreversible widening.
    op.execute(
        "ALTER TABLE professional_instruments "
        "ALTER COLUMN base_currency TYPE CHAR(3), "
        "ALTER COLUMN quote_currency TYPE CHAR(3), "
        "ALTER COLUMN settlement_currency TYPE CHAR(3)"
    )
    op.execute(
        "ALTER TABLE runtime_instruments ALTER COLUMN quote_currency TYPE CHAR(3)"
    )
