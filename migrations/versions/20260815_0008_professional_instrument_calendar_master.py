"""add professional instrument and calendar master

Revision ID: 20260815_0008
Revises: 20260815_0007
Create Date: 2026-08-15
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260815_0008"
down_revision = "20260815_0007"
branch_labels = None
depends_on = None


TABLES = (
    "professional_instruments",
    "professional_symbol_mappings",
    "professional_identifier_mappings",
    "professional_instrument_lifecycle_events",
    "professional_calendar_definitions",
    "professional_calendar_weekly_sessions",
    "professional_calendar_exceptions",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    statements = (
        """CREATE TABLE professional_instruments (
            instrument_id TEXT PRIMARY KEY REFERENCES runtime_instruments(instrument_id),
            asset_class TEXT NOT NULL,
            instrument_type TEXT NOT NULL,
            exchange_name TEXT NOT NULL,
            venue TEXT NOT NULL,
            mic TEXT,
            canonical_symbol TEXT NOT NULL,
            listing_date DATE NOT NULL,
            base_currency CHAR(3) NOT NULL,
            quote_currency CHAR(3) NOT NULL,
            settlement_currency CHAR(3) NOT NULL,
            contract_multiplier NUMERIC(30,12) NOT NULL CHECK(contract_multiplier > 0),
            contract_size NUMERIC(30,12) NOT NULL CHECK(contract_size > 0),
            tick_size NUMERIC(30,12) NOT NULL CHECK(tick_size > 0),
            lot_size NUMERIC(30,12) NOT NULL CHECK(lot_size > 0),
            price_precision SMALLINT NOT NULL CHECK(price_precision BETWEEN 0 AND 18),
            quantity_precision SMALLINT NOT NULL CHECK(quantity_precision BETWEEN 0 AND 18),
            trading_timezone TEXT NOT NULL,
            market_session_type TEXT NOT NULL CHECK(market_session_type IN ('US_EQUITY','FX_24X5','CRYPTO_24X7')),
            representation_kind TEXT NOT NULL CHECK(representation_kind IN ('DIRECT','ETF_PROXY','SPOT','FUTURE')),
            underlying_reference TEXT,
            corporate_action_reference TEXT,
            isin TEXT,
            cusip TEXT,
            contract_code TEXT,
            expiration_date DATE,
            first_notice_date DATE,
            last_trade_date DATE,
            continuous_parent_id TEXT,
            roll_rule TEXT,
            registered_at TIMESTAMPTZ NOT NULL,
            CHECK(expiration_date IS NULL OR last_trade_date IS NULL OR last_trade_date <= expiration_date),
            CHECK(first_notice_date IS NULL OR expiration_date IS NULL OR first_notice_date <= expiration_date)
        )""",
        """CREATE TABLE professional_symbol_mappings (
            mapping_id UUID PRIMARY KEY,
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            venue TEXT NOT NULL,
            symbol TEXT NOT NULL,
            valid_from TIMESTAMPTZ NOT NULL,
            valid_until TIMESTAMPTZ,
            ingested_at TIMESTAMPTZ NOT NULL,
            source_reference TEXT NOT NULL,
            CHECK(valid_until IS NULL OR valid_until > valid_from),
            CHECK(ingested_at >= valid_from)
        )""",
        """ALTER TABLE professional_symbol_mappings ADD CONSTRAINT professional_symbol_mapping_no_overlap
            EXCLUDE USING gist (
                venue WITH =,
                symbol WITH =,
                tstzrange(valid_from, COALESCE(valid_until, 'infinity'::timestamptz), '[)') WITH &&
            )""",
        "CREATE INDEX professional_symbol_asof_idx ON professional_symbol_mappings(venue, symbol, valid_from DESC, ingested_at)",
        """CREATE TABLE professional_identifier_mappings (
            mapping_id UUID PRIMARY KEY,
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            source_kind TEXT NOT NULL CHECK(source_kind IN ('PROVIDER','BROKER','STANDARD')),
            namespace TEXT NOT NULL,
            identifier_value TEXT NOT NULL,
            valid_from TIMESTAMPTZ NOT NULL,
            valid_until TIMESTAMPTZ,
            ingested_at TIMESTAMPTZ NOT NULL,
            source_reference TEXT NOT NULL,
            CHECK(valid_until IS NULL OR valid_until > valid_from),
            CHECK(ingested_at >= valid_from)
        )""",
        """ALTER TABLE professional_identifier_mappings ADD CONSTRAINT professional_identifier_mapping_no_overlap
            EXCLUDE USING gist (
                namespace WITH =,
                identifier_value WITH =,
                tstzrange(valid_from, COALESCE(valid_until, 'infinity'::timestamptz), '[)') WITH &&
            )""",
        "CREATE INDEX professional_identifier_asof_idx ON professional_identifier_mappings(namespace, identifier_value, valid_from DESC, ingested_at)",
        """CREATE TABLE professional_instrument_lifecycle_events (
            event_id UUID PRIMARY KEY,
            instrument_id TEXT NOT NULL REFERENCES professional_instruments(instrument_id),
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','DELISTED')),
            effective_at TIMESTAMPTZ NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL CHECK(ingested_at >= effective_at),
            reason TEXT NOT NULL,
            UNIQUE(instrument_id, status, effective_at)
        )""",
        "CREATE INDEX professional_lifecycle_asof_idx ON professional_instrument_lifecycle_events(instrument_id, effective_at DESC, ingested_at DESC)",
        """CREATE TABLE professional_calendar_definitions (
            calendar_id UUID PRIMARY KEY,
            venue TEXT NOT NULL,
            session_type TEXT NOT NULL CHECK(session_type IN ('US_EQUITY','FX_24X5','CRYPTO_24X7')),
            timezone_name TEXT NOT NULL,
            valid_from DATE NOT NULL,
            valid_until DATE,
            ingested_at TIMESTAMPTZ NOT NULL,
            rollover_time TIME,
            source_reference TEXT NOT NULL,
            CHECK(valid_until IS NULL OR valid_until > valid_from)
        )""",
        """ALTER TABLE professional_calendar_definitions ADD CONSTRAINT professional_calendar_no_overlap
            EXCLUDE USING gist (
                venue WITH =,
                daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
            )""",
        """CREATE TABLE professional_calendar_weekly_sessions (
            calendar_id UUID NOT NULL REFERENCES professional_calendar_definitions(calendar_id),
            weekday SMALLINT NOT NULL CHECK(weekday BETWEEN 0 AND 6),
            opens_at TIME NOT NULL,
            closes_at TIME NOT NULL,
            close_day_offset SMALLINT NOT NULL CHECK(close_day_offset IN (0,1)),
            ingested_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY(calendar_id, weekday, opens_at),
            CHECK(close_day_offset = 1 OR closes_at > opens_at)
        )""",
        """CREATE TABLE professional_calendar_exceptions (
            exception_id UUID PRIMARY KEY,
            calendar_id UUID NOT NULL REFERENCES professional_calendar_definitions(calendar_id),
            session_date DATE NOT NULL,
            exception_type TEXT NOT NULL CHECK(exception_type IN ('HOLIDAY','EARLY_CLOSE')),
            closes_at TIME,
            ingested_at TIMESTAMPTZ NOT NULL,
            source_reference TEXT NOT NULL,
            UNIQUE(calendar_id, session_date),
            CHECK((exception_type = 'HOLIDAY' AND closes_at IS NULL) OR (exception_type = 'EARLY_CLOSE' AND closes_at IS NOT NULL))
        )""",
    )
    for statement in statements:
        op.execute(statement)
    for table in TABLES:
        op.execute(immutable_trigger_sql(table))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
