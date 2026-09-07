"""add first-class futures contract, margin and continuous-series authority

Revision ID: 20260907_0041
Revises: 20260907_0040
Create Date: 2026-09-07

Module 3H.1 (roadmap NEXT-02 phase 1). These are SIBLING tables keyed by
``professional_instruments.instrument_id``, deliberately NOT new columns on
``professional_instruments``: that table is written with a positional
``INSERT INTO professional_instruments VALUES (<31 placeholders>)`` and read
back by positional index in
``PostgresProfessionalInstrumentMaster._instrument_from_row()``, where index 31
is the lifecycle status appended by a ``SELECT p.*, COALESCE(...)`` subquery.
Adding a column to that table would silently shift every one of those indices.
Extending alongside it preserves the single canonical instrument authority
without touching a working read path.

Also widens the two enumerated session-type CHECK constraints so a futures
venue can be modelled at all -- the original 20260815_0008 constraints allow
only US_EQUITY / FX_24X5 / CRYPTO_24X7, none of which describes a nearly-24
hour, five-day CME-style futures session.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260907_0041"
down_revision = "20260907_0040"
branch_labels = None
depends_on = None


TABLES = (
    "futures_contract_series",
    "futures_contract_specifications",
    "futures_margin_requirements",
    "futures_continuous_series_policies",
    "futures_continuous_series_members",
)

_SETTLEMENT = "('PHYSICAL_DELIVERY','CASH_SETTLED')"
_MONTH_CODES = "('F','G','H','J','K','M','N','Q','U','V','X','Z')"
_ROLL_TRIGGERS = (
    "('LAST_TRADE_DATE','FIRST_NOTICE_DATE','CALENDAR_DAYS_BEFORE_LAST_TRADE',"
    "'CALENDAR_DAYS_BEFORE_FIRST_NOTICE','VOLUME_OPEN_INTEREST_CROSSOVER')"
)
_ADJUSTMENTS = "('NONE','BACK_ADJUSTED_DIFFERENCE','BACK_ADJUSTED_RATIO')"
_SESSION_TYPES = "('US_EQUITY','FX_24X5','CRYPTO_24X7','FUTURES_23X5')"


def upgrade() -> None:
    statements = (
        # The multiplier/tick_size/tick_value identity is the database-level
        # discriminator that keeps GC (100 troy oz, $10.00 a tick) and MGC
        # (10 troy oz, $1.00 a tick) structurally impossible to conflate even
        # though both quote gold in USD per troy ounce at a 0.10 tick.
        f"""CREATE TABLE futures_contract_series (
            series_id TEXT PRIMARY KEY,
            root_symbol TEXT NOT NULL,
            exchange_name TEXT NOT NULL,
            venue TEXT NOT NULL,
            mic TEXT,
            asset_class TEXT NOT NULL,
            underlying_reference TEXT NOT NULL,
            currency CHAR(3) NOT NULL,
            contract_multiplier NUMERIC(30,12) NOT NULL CHECK(contract_multiplier > 0),
            unit_of_measure TEXT NOT NULL,
            tick_size NUMERIC(30,12) NOT NULL CHECK(tick_size > 0),
            tick_value NUMERIC(30,12) NOT NULL CHECK(tick_value > 0),
            price_precision SMALLINT NOT NULL CHECK(price_precision BETWEEN 0 AND 18),
            quantity_precision SMALLINT NOT NULL CHECK(quantity_precision BETWEEN 0 AND 18),
            settlement_type TEXT NOT NULL CHECK(settlement_type IN {_SETTLEMENT}),
            trading_timezone TEXT NOT NULL,
            session_type TEXT NOT NULL CHECK(session_type IN {_SESSION_TYPES}),
            registered_at TIMESTAMPTZ NOT NULL,
            source_reference TEXT NOT NULL,
            UNIQUE(venue, root_symbol),
            CONSTRAINT futures_series_tick_value_identity
                CHECK(tick_value = tick_size * contract_multiplier)
        )""",
        f"""CREATE TABLE futures_contract_specifications (
            instrument_id TEXT PRIMARY KEY REFERENCES professional_instruments(instrument_id),
            series_id TEXT NOT NULL REFERENCES futures_contract_series(series_id),
            contract_code TEXT NOT NULL,
            contract_year SMALLINT NOT NULL CHECK(contract_year BETWEEN 1900 AND 2200),
            contract_month SMALLINT NOT NULL CHECK(contract_month BETWEEN 1 AND 12),
            month_code CHAR(1) NOT NULL CHECK(month_code IN {_MONTH_CODES}),
            first_trade_date DATE NOT NULL,
            first_notice_date DATE,
            last_trade_date DATE NOT NULL,
            expiration_date DATE NOT NULL,
            settlement_date DATE NOT NULL,
            settlement_type TEXT NOT NULL CHECK(settlement_type IN {_SETTLEMENT}),
            contract_multiplier NUMERIC(30,12) NOT NULL CHECK(contract_multiplier > 0),
            tick_size NUMERIC(30,12) NOT NULL CHECK(tick_size > 0),
            tick_value NUMERIC(30,12) NOT NULL CHECK(tick_value > 0),
            registered_at TIMESTAMPTZ NOT NULL,
            source_reference TEXT NOT NULL,
            UNIQUE(series_id, contract_year, contract_month),
            UNIQUE(series_id, contract_code),
            CONSTRAINT futures_contract_tick_value_identity
                CHECK(tick_value = tick_size * contract_multiplier),
            CHECK(first_trade_date < last_trade_date),
            CHECK(last_trade_date <= expiration_date),
            CHECK(expiration_date <= settlement_date),
            CHECK(first_notice_date IS NULL OR first_notice_date > first_trade_date),
            CHECK(first_notice_date IS NULL OR first_notice_date <= expiration_date)
        )""",
        (
            "CREATE INDEX futures_contract_series_expiry_idx ON futures_contract_specifications"
            "(series_id, expiration_date, registered_at)"
        ),
        # effective_from (exchange effective date) and known_at (when this
        # platform learned the number) are deliberately unordered relative to
        # each other: exchanges publish margin changes BEFORE they take effect,
        # so known_at < effective_from is the normal case for a live feed and
        # known_at > effective_from is the normal case for a backfill.
        """CREATE TABLE futures_margin_requirements (
            requirement_id UUID PRIMARY KEY,
            series_id TEXT NOT NULL REFERENCES futures_contract_series(series_id),
            instrument_id TEXT REFERENCES professional_instruments(instrument_id),
            tier TEXT NOT NULL CHECK(tier IN ('SPECULATIVE','HEDGER')),
            initial_margin NUMERIC(30,12) NOT NULL CHECK(initial_margin > 0),
            maintenance_margin NUMERIC(30,12) NOT NULL CHECK(maintenance_margin > 0),
            currency CHAR(3) NOT NULL,
            effective_from TIMESTAMPTZ NOT NULL,
            known_at TIMESTAMPTZ NOT NULL,
            source_reference TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            CHECK(maintenance_margin <= initial_margin)
        )""",
        # NULL instrument_id means "series-level requirement". A plain UNIQUE
        # would not deduplicate those rows, because SQL treats NULLs as
        # distinct -- hence two partial indexes instead of one constraint.
        (
            "CREATE UNIQUE INDEX futures_margin_series_level_idx ON futures_margin_requirements"
            "(series_id, tier, effective_from, known_at) WHERE instrument_id IS NULL"
        ),
        (
            "CREATE UNIQUE INDEX futures_margin_contract_level_idx ON futures_margin_requirements"
            "(series_id, instrument_id, tier, effective_from, known_at)"
            " WHERE instrument_id IS NOT NULL"
        ),
        f"""CREATE TABLE futures_continuous_series_policies (
            policy_id UUID PRIMARY KEY,
            series_id TEXT NOT NULL REFERENCES futures_contract_series(series_id),
            policy_version INTEGER NOT NULL CHECK(policy_version >= 1),
            roll_trigger TEXT NOT NULL CHECK(roll_trigger IN {_ROLL_TRIGGERS}),
            roll_offset_days SMALLINT NOT NULL CHECK(roll_offset_days >= 0),
            adjustment_method TEXT NOT NULL CHECK(adjustment_method IN {_ADJUSTMENTS}),
            max_depth SMALLINT NOT NULL CHECK(max_depth BETWEEN 1 AND 12),
            economic_rationale TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            approved_at TIMESTAMPTZ NOT NULL,
            source_reference TEXT NOT NULL,
            UNIQUE(series_id, policy_version)
        )""",
        """CREATE TABLE futures_continuous_series_members (
            member_id UUID PRIMARY KEY,
            policy_id UUID NOT NULL REFERENCES futures_continuous_series_policies(policy_id),
            depth SMALLINT NOT NULL CHECK(depth BETWEEN 1 AND 12),
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            effective_from TIMESTAMPTZ NOT NULL,
            effective_until TIMESTAMPTZ NOT NULL,
            known_at TIMESTAMPTZ NOT NULL,
            roll_reason TEXT NOT NULL,
            CHECK(effective_until > effective_from),
            UNIQUE(policy_id, depth, instrument_id)
        )""",
        """ALTER TABLE futures_continuous_series_members
            ADD CONSTRAINT futures_continuous_member_no_overlap
            EXCLUDE USING gist (
                policy_id WITH =,
                depth WITH =,
                tstzrange(effective_from, effective_until, '[)') WITH &&
            )""",
        (
            "CREATE INDEX futures_continuous_member_asof_idx ON futures_continuous_series_members"
            "(policy_id, depth, effective_from DESC, known_at)"
        ),
        # Widen the two session-type enumerations. The constraint names are
        # PostgreSQL's own single-column defaults from migration 0008's inline
        # CHECKs; DROP ... IF EXISTS keeps this safe if that assumption is
        # wrong, and the newly added named constraint is authoritative either
        # way -- a stale narrow constraint would surface immediately as a
        # rejected FUTURES_23X5 insert rather than silently passing.
        (
            "ALTER TABLE professional_instruments DROP CONSTRAINT IF EXISTS "
            "professional_instruments_market_session_type_check"
        ),
        (
            "ALTER TABLE professional_instruments ADD CONSTRAINT "
            "professional_instrument_session_type_check "
            f"CHECK(market_session_type IN {_SESSION_TYPES})"
        ),
        (
            "ALTER TABLE professional_calendar_definitions DROP CONSTRAINT IF EXISTS "
            "professional_calendar_definitions_session_type_check"
        ),
        (
            "ALTER TABLE professional_calendar_definitions ADD CONSTRAINT "
            "professional_calendar_session_type_check "
            f"CHECK(session_type IN {_SESSION_TYPES})"
        ),
    )
    for statement in statements:
        op.execute(statement)
    for table in TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    op.execute(
        "ALTER TABLE professional_calendar_definitions DROP CONSTRAINT IF EXISTS "
        "professional_calendar_session_type_check"
    )
    op.execute(
        "ALTER TABLE professional_calendar_definitions ADD CONSTRAINT "
        "professional_calendar_definitions_session_type_check "
        "CHECK(session_type IN ('US_EQUITY','FX_24X5','CRYPTO_24X7'))"
    )
    op.execute(
        "ALTER TABLE professional_instruments DROP CONSTRAINT IF EXISTS "
        "professional_instrument_session_type_check"
    )
    op.execute(
        "ALTER TABLE professional_instruments ADD CONSTRAINT "
        "professional_instruments_market_session_type_check "
        "CHECK(market_session_type IN ('US_EQUITY','FX_24X5','CRYPTO_24X7'))"
    )
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
