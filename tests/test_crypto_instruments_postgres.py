"""Real PostgreSQL evidence for Module 3H.2 crypto instrument authority.

Every integration test file in this suite shares one PostgreSQL database for
the whole CI run with no reset between files, so this file registers its own
fixture instruments under ``operator_dashboard.RESERVED_TEST_FIXTURE_PREFIX``
(``TESTFIXTURE:``), which keeps them off the operator's unfiltered instrument
discovery page.

Asset, venue and contract parameters are FIXTURES. Venue names such as BINANCE
or COINBASE are realistic placeholder strings only; nothing here was retrieved
from or verified against any exchange, and no venue's real trading rules,
funding schedule or instrument list is claimed.
"""

import os
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class CryptoInstrumentAuthorityPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_crypto_authority_end_to_end(self) -> None:
        from trade_platform.crypto_instruments import (
            AmbiguousCryptoVenueError,
            CryptoFundingConvention,
            CryptoFundingConventionError,
            CryptoInstrumentKind,
            CryptoInstrumentSpecification,
            CryptoResolutionError,
            CryptoSettlementType,
            CryptoSpecificationError,
            CryptoVenueRuleError,
            CryptoVenueTradingRules,
            PostgresCryptoInstrumentAuthority,
            ReferencePriceRequirement,
            SettlementStyle,
        )
        from trade_platform.domain import AssetClass
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.professional_instruments import (
            InstrumentType,
            LifecycleStatus,
            PostgresProfessionalInstrumentMaster,
            ProfessionalInstrument,
            RepresentationKind,
            SessionType,
            SymbolMapping,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        database = PostgresDatabase(dsn)
        master = PostgresProfessionalInstrumentMaster(database)
        authority = PostgresCryptoInstrumentAuthority(database)
        registered_at = datetime(2024, 1, 2, tzinfo=UTC)
        expiry = datetime(2025, 6, 27, 8, 0, tzinfo=UTC)

        def register(
            instrument_id: str,
            venue: str,
            symbol: str,
            instrument_type: InstrumentType,
            representation: RepresentationKind,
            *,
            base: str = "BTC",
            quote: str = "USDT",
            settlement: str = "USDT",
            asset_class: AssetClass = AssetClass.CRYPTO,
        ) -> str:
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id,
                    asset_class=asset_class,
                    instrument_type=instrument_type,
                    exchange_name=venue,
                    venue=venue,
                    mic=None,
                    canonical_symbol=symbol,
                    listing_date=date(2020, 1, 1),
                    base_currency=base,
                    quote_currency=quote,
                    settlement_currency=settlement,
                    contract_multiplier=Decimal(1),
                    contract_size=Decimal(1),
                    tick_size=Decimal("0.01"),
                    lot_size=Decimal("0.00001"),
                    price_precision=2,
                    quantity_precision=5,
                    trading_timezone=(
                        "UTC" if asset_class is AssetClass.CRYPTO else "America/New_York"
                    ),
                    market_session_type=(
                        SessionType.CRYPTO_24X7 if asset_class is AssetClass.CRYPTO
                        else SessionType.US_EQUITY
                    ),
                    representation_kind=representation,
                    registered_at=registered_at,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                )
            )
            return instrument_id

        # A four-character quote asset is the whole point of migration 0042's
        # currency widening: USDT cannot be expressed under the original CHAR(3).
        spot_id = register(
            "TESTFIXTURE:CRY:BINANCE:BTCUSDT:SPOT", "BINANCE", "TESTFIXBTCUSDT",
            InstrumentType.SPOT_CRYPTO, RepresentationKind.SPOT,
        )
        perp_id = register(
            "TESTFIXTURE:CRY:BINANCE:BTCUSDT:PERP", "BINANCE", "TESTFIXBTCUSDTPERP",
            InstrumentType.CRYPTO_PERPETUAL, RepresentationKind.PERPETUAL,
        )
        dated_id = register(
            "TESTFIXTURE:CRY:BINANCE:BTCUSDT:20250627", "BINANCE", "TESTFIXBTCUSDT0627",
            InstrumentType.CRYPTO_DATED_FUTURE, RepresentationKind.FUTURE,
        )
        inverse_id = register(
            "TESTFIXTURE:CRY:BINANCE:BTCUSD:PERP", "BINANCE", "TESTFIXBTCUSDPERP",
            InstrumentType.CRYPTO_PERPETUAL, RepresentationKind.PERPETUAL,
            quote="USD", settlement="BTC",
        )
        other_venue_id = register(
            "TESTFIXTURE:CRY:COINBASE:BTCUSDT:SPOT", "COINBASE", "TESTFIXBTCUSDTCB",
            InstrumentType.SPOT_CRYPTO, RepresentationKind.SPOT,
        )

        reloaded = master.get_as_of(spot_id, registered_at)
        self.assertEqual(reloaded.quote_currency, "USDT")
        self.assertEqual(reloaded.base_currency, "BTC")

        def specification(
            instrument_id: str, kind: CryptoInstrumentKind, **overrides: object
        ) -> CryptoInstrumentSpecification:
            derivative = kind is not CryptoInstrumentKind.SPOT
            fields: dict[str, object] = {
                "instrument_id": instrument_id,
                "venue": "BINANCE",
                "kind": kind,
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "settlement_asset": "USDT" if derivative else None,
                "settlement_style": SettlementStyle.LINEAR if derivative else None,
                "settlement_type": (
                    CryptoSettlementType.CASH_SETTLED if derivative
                    else CryptoSettlementType.PHYSICAL_DELIVERY
                ),
                "contract_multiplier": Decimal(1),
                "contract_size": Decimal(1),
                "expiry_at": expiry if kind is CryptoInstrumentKind.DATED_FUTURE else None,
                "reference_price_requirement": (
                    ReferencePriceRequirement.MARK_AND_INDEX if derivative
                    else ReferencePriceRequirement.NONE
                ),
                "registered_at": registered_at,
                "source_reference": "fixture:venue-instrument-list",
            }
            fields.update(overrides)
            return CryptoInstrumentSpecification(**fields)  # type: ignore[arg-type]

        # ---- an instrument must exist, be crypto, and match its venue ---------
        with self.assertRaises(CryptoSpecificationError) as raised:
            authority.specify_instrument(
                specification("TESTFIXTURE:CRY:NEVER_REGISTERED", CryptoInstrumentKind.SPOT)
            )
        self.assertIn("instrument_not_registered", str(raised.exception))

        equity_id = register(
            "TESTFIXTURE:CRY:XNAS:EQUITY", "XNAS", "TESTFIXCRYEQ",
            InstrumentType.COMMON_STOCK, RepresentationKind.DIRECT,
            base="USD", quote="USD", settlement="USD", asset_class=AssetClass.EQUITY,
        )
        with self.assertRaises(CryptoSpecificationError) as raised:
            authority.specify_instrument(
                specification(equity_id, CryptoInstrumentKind.SPOT, venue="XNAS")
            )
        self.assertIn("instrument_is_not_crypto", str(raised.exception))

        with self.assertRaises(CryptoSpecificationError) as raised:
            authority.specify_instrument(
                specification(other_venue_id, CryptoInstrumentKind.SPOT)
            )
        self.assertIn("venue_differs_from_instrument_master", str(raised.exception))

        # A perpetual registered in the master as spot must not be specifiable
        # as a perpetual -- the canonical registry and this layer must agree.
        with self.assertRaises(CryptoSpecificationError) as raised:
            authority.specify_instrument(
                specification(spot_id, CryptoInstrumentKind.PERPETUAL)
            )
        self.assertIn("instrument_type_does_not_match_crypto_kind", str(raised.exception))

        authority.specify_instrument(specification(spot_id, CryptoInstrumentKind.SPOT))
        authority.specify_instrument(specification(perp_id, CryptoInstrumentKind.PERPETUAL))
        authority.specify_instrument(
            specification(dated_id, CryptoInstrumentKind.DATED_FUTURE)
        )
        authority.specify_instrument(
            specification(
                inverse_id,
                CryptoInstrumentKind.PERPETUAL,
                quote_asset="USD",
                settlement_asset="BTC",
                settlement_style=SettlementStyle.INVERSE,
            )
        )
        authority.specify_instrument(
            specification(other_venue_id, CryptoInstrumentKind.SPOT, venue="COINBASE")
        )

        # Re-specifying the same instrument is refused by the primary key.
        with self.assertRaises(CryptoSpecificationError) as raised:
            authority.specify_instrument(specification(spot_id, CryptoInstrumentKind.SPOT))
        self.assertIn("crypto_specification_duplicate_or_invalid", str(raised.exception))

        # ---- the database enforces the kind invariants independently ----------
        for label, values in (
            ("spot_with_expiry", (
                "TESTFIXTURE:CRY:RAW:SPOTEXPIRY", "BINANCE", "SPOT", "BTC", "USDT",
                None, None, "PHYSICAL_DELIVERY", Decimal(1), Decimal(1), expiry, "NONE",
            )),
            ("perpetual_with_expiry", (
                "TESTFIXTURE:CRY:RAW:PERPEXPIRY", "BINANCE", "PERPETUAL", "BTC", "USDT",
                "USDT", "LINEAR", "CASH_SETTLED", Decimal(1), Decimal(1), expiry,
                "MARK_AND_INDEX",
            )),
            ("dated_without_expiry", (
                "TESTFIXTURE:CRY:RAW:DATEDNOEXP", "BINANCE", "DATED_FUTURE", "BTC", "USDT",
                "USDT", "LINEAR", "CASH_SETTLED", Decimal(1), Decimal(1), None,
                "MARK_AND_INDEX",
            )),
            ("inverse_settling_quote", (
                "TESTFIXTURE:CRY:RAW:BADINVERSE", "BINANCE", "PERPETUAL", "BTC", "USDT",
                "USDT", "INVERSE", "CASH_SETTLED", Decimal(1), Decimal(1), None,
                "MARK_AND_INDEX",
            )),
        ):
            with (
                self.subTest(label),
                self.assertRaises(Exception),  # noqa: B017
                database.transaction() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "INSERT INTO crypto_instrument_specifications "
                    "VALUES (" + ",".join(["%s"] * 15) + ")",
                    (*values, None, registered_at, "fixture"),
                )

        # ---- spot, perpetual and dated future never resolve to each other -----
        self.assertEqual(
            authority.resolve(
                "BINANCE", "BTC", "USDT", CryptoInstrumentKind.SPOT, known_at=registered_at
            ),
            spot_id,
        )
        self.assertEqual(
            authority.resolve(
                "BINANCE", "BTC", "USDT", CryptoInstrumentKind.PERPETUAL, known_at=registered_at
            ),
            perp_id,
        )
        self.assertEqual(
            authority.resolve(
                "BINANCE", "BTC", "USDT", CryptoInstrumentKind.DATED_FUTURE,
                known_at=registered_at, expiry_at=expiry,
            ),
            dated_id,
        )
        # Three different instruments, never interchangeable.
        self.assertEqual(len({spot_id, perp_id, dated_id}), 3)

        with self.assertRaises(CryptoResolutionError) as raised:
            authority.resolve(
                "BINANCE", "BTC", "USDT", CryptoInstrumentKind.DATED_FUTURE,
                known_at=registered_at,
            )
        self.assertIn("dated_future_resolution_requires_expiry", str(raised.exception))
        with self.assertRaises(CryptoResolutionError) as raised:
            authority.resolve(
                "BINANCE", "BTC", "USDT", CryptoInstrumentKind.SPOT,
                known_at=registered_at, expiry_at=expiry,
            )
        self.assertIn("expiry_not_applicable_to_this_kind", str(raised.exception))

        # The same pair on another venue is a different instrument.
        self.assertEqual(
            authority.resolve(
                "COINBASE", "BTC", "USDT", CryptoInstrumentKind.SPOT, known_at=registered_at
            ),
            other_venue_id,
        )

        # ---- cross-venue display symbols fail closed without a venue ----------
        shared_symbol = "TESTFIXSHAREDBTC"
        for instrument_id, venue in ((spot_id, "BINANCE"), (other_venue_id, "COINBASE")):
            master.add_symbol_mapping(
                SymbolMapping(
                    instrument_id=instrument_id, venue=venue, symbol=shared_symbol,
                    valid_from=registered_at, valid_until=None, ingested_at=registered_at,
                    source_reference="fixture:venue-symbol-list",
                )
            )
        with self.assertRaises(AmbiguousCryptoVenueError) as raised:
            authority.resolve_display_symbol(shared_symbol, known_at=registered_at)
        self.assertIn("crypto_symbol_ambiguous_across_venues", str(raised.exception))
        self.assertIn("BINANCE", str(raised.exception))
        self.assertIn("COINBASE", str(raised.exception))
        # Disambiguated by venue, it resolves exactly.
        self.assertEqual(
            authority.resolve_display_symbol(
                shared_symbol, known_at=registered_at, venue="COINBASE"
            ),
            other_venue_id,
        )

        # ---- two clocks on funding conventions --------------------------------
        with self.assertRaises(CryptoFundingConventionError) as raised:
            authority.register_funding_convention(
                CryptoFundingConvention(
                    instrument_id=spot_id, convention_version=1,
                    interval_hours=Decimal(8), first_funding_offset_hours=Decimal(0),
                    funding_settlement_asset="USDT",
                    effective_from=registered_at, known_at=registered_at,
                    source_reference="fixture", source_hash="0" * 64,
                )
            )
        self.assertIn("funding_convention_requires_perpetual", str(raised.exception))

        authority.register_funding_convention(
            CryptoFundingConvention(
                instrument_id=perp_id, convention_version=1,
                interval_hours=Decimal(8), first_funding_offset_hours=Decimal(0),
                funding_settlement_asset="USDT",
                effective_from=registered_at, known_at=registered_at,
                source_reference="fixture:funding-schedule-v1", source_hash="a" * 64,
            )
        )
        # Announced 2025-03-01, effective 2025-03-10.
        authority.register_funding_convention(
            CryptoFundingConvention(
                instrument_id=perp_id, convention_version=2,
                interval_hours=Decimal(4), first_funding_offset_hours=Decimal(0),
                funding_settlement_asset="USDT",
                effective_from=datetime(2025, 3, 10, tzinfo=UTC),
                known_at=datetime(2025, 3, 1, tzinfo=UTC),
                source_reference="fixture:funding-schedule-v2", source_hash="b" * 64,
            )
        )
        known_but_not_effective = authority.funding_convention_point_in_time(
            perp_id,
            effective_at=datetime(2025, 3, 5, tzinfo=UTC),
            known_at=datetime(2025, 3, 5, tzinfo=UTC),
        )
        self.assertEqual(known_but_not_effective.interval_hours, Decimal("8.000000"))
        in_force = authority.funding_convention_point_in_time(
            perp_id,
            effective_at=datetime(2025, 3, 15, tzinfo=UTC),
            known_at=datetime(2025, 3, 15, tzinfo=UTC),
        )
        self.assertEqual(in_force.interval_hours, Decimal("4.000000"))
        # A replay predating the announcement cannot see it even though the
        # effective date has passed -- no future knowledge leaks backwards.
        not_yet_known = authority.funding_convention_point_in_time(
            perp_id,
            effective_at=datetime(2025, 3, 15, tzinfo=UTC),
            known_at=datetime(2025, 2, 1, tzinfo=UTC),
        )
        self.assertEqual(not_yet_known.interval_hours, Decimal("8.000000"))

        # ---- two clocks on venue trading rules --------------------------------
        authority.record_venue_trading_rules(
            CryptoVenueTradingRules(
                instrument_id=spot_id, rule_version=1, tick_size=Decimal("0.01"),
                quantity_step=Decimal("0.00001"), min_quantity=Decimal("0.00001"),
                min_notional=Decimal(10), price_precision=2, quantity_precision=5,
                effective_from=registered_at, known_at=registered_at,
                source_reference="fixture:venue-rules-v1", source_hash="c" * 64,
            )
        )
        authority.record_venue_trading_rules(
            CryptoVenueTradingRules(
                instrument_id=spot_id, rule_version=2, tick_size=Decimal("0.10"),
                quantity_step=Decimal("0.0001"), min_quantity=Decimal("0.0001"),
                min_notional=Decimal(20), price_precision=1, quantity_precision=4,
                effective_from=datetime(2025, 6, 1, tzinfo=UTC),
                known_at=datetime(2025, 6, 1, tzinfo=UTC),
                source_reference="fixture:venue-rules-v2", source_hash="d" * 64,
            )
        )
        before = authority.venue_trading_rules_point_in_time(
            spot_id,
            effective_at=datetime(2025, 1, 1, tzinfo=UTC),
            known_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        after = authority.venue_trading_rules_point_in_time(
            spot_id,
            effective_at=datetime(2025, 7, 1, tzinfo=UTC),
            known_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.assertEqual(before.tick_size, Decimal("0.010000000000000000"))
        self.assertEqual(after.tick_size, Decimal("0.100000000000000000"))
        self.assertEqual(before.min_notional, Decimal("10.000000000000000000"))

        # A specification unknown at the query's knowledge time is invisible.
        with self.assertRaises(CryptoResolutionError) as raised:
            authority.get_specification(spot_id, known_at=datetime(2023, 1, 1, tzinfo=UTC))
        self.assertIn("crypto_specification_not_found", str(raised.exception))
        with self.assertRaises(CryptoResolutionError):
            authority.resolve(
                "BINANCE", "BTC", "USDT", CryptoInstrumentKind.SPOT,
                known_at=datetime(2023, 1, 1, tzinfo=UTC),
            )
        with self.assertRaises(CryptoVenueRuleError) as raised:
            authority.venue_trading_rules_point_in_time(
                spot_id,
                effective_at=datetime(2023, 1, 1, tzinfo=UTC),
                known_at=datetime(2023, 1, 1, tzinfo=UTC),
            )
        self.assertIn("venue_trading_rules_not_available", str(raised.exception))

        # ---- immutability and restart durability ------------------------------
        for table, column in (
            ("crypto_instrument_specifications", "source_reference"),
            ("crypto_funding_conventions", "source_reference"),
            ("crypto_venue_trading_rules", "source_reference"),
        ):
            with (
                self.subTest(table),
                self.assertRaises(Exception),  # noqa: B017
                database.transaction() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(f"UPDATE {table} SET {column} = 'tampered'")
            with (
                self.subTest(table),
                self.assertRaises(Exception),  # noqa: B017
                database.transaction() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(f"DELETE FROM {table}")

        restarted = PostgresCryptoInstrumentAuthority(PostgresDatabase(dsn))
        durable = restarted.get_specification(inverse_id, known_at=registered_at)
        self.assertEqual(durable.settlement_style, SettlementStyle.INVERSE)
        self.assertEqual(durable.settlement_asset, "BTC")
        self.assertTrue(durable.requires_funding)
        self.assertEqual(
            restarted.funding_convention_point_in_time(
                perp_id,
                effective_at=datetime(2025, 3, 15, tzinfo=UTC),
                known_at=datetime(2025, 3, 15, tzinfo=UTC),
            ).interval_hours,
            Decimal("4.000000"),
        )

        # ---- existing equity and futures authorities are untouched ------------
        equity = master.get_as_of(equity_id, datetime(2025, 1, 1, tzinfo=UTC))
        self.assertEqual(equity.instrument_type, InstrumentType.COMMON_STOCK)
        self.assertEqual(equity.quote_currency, "USD")
        self.assertEqual(equity.market_session_type, SessionType.US_EQUITY)

    def test_reserved_fixture_prefix_is_excluded_from_unfiltered_discovery(self) -> None:
        """The scoped fix for the shared-database discovery fragility.

        Fixture instruments must never displace real instruments from the
        operator's first page, but must stay reachable by explicit search and
        by the detail read so nothing becomes unauditable.
        """
        from trade_platform.domain import AssetClass
        from trade_platform.operator_dashboard import (
            RESERVED_TEST_FIXTURE_PREFIX,
            PostgresOperatorDashboardQueries,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.professional_instruments import (
            InstrumentType,
            LifecycleStatus,
            PostgresProfessionalInstrumentMaster,
            ProfessionalInstrument,
            RepresentationKind,
            SessionType,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        queries = PostgresOperatorDashboardQueries(database)

        # Registered here rather than relying on another test file having run
        # first, so this holds under `unittest discover` in any order.
        marker = f"{RESERVED_TEST_FIXTURE_PREFIX}DISCOVERY:AAAAA"
        PostgresProfessionalInstrumentMaster(database).register(
            ProfessionalInstrument(
                instrument_id=marker,
                asset_class=AssetClass.EQUITY,
                instrument_type=InstrumentType.COMMON_STOCK,
                exchange_name="NASDAQ",
                venue="XNAS",
                mic="XNAS",
                # Deliberately sorts to the very front of ORDER BY
                # canonical_symbol: under the old convention this fixture would
                # have taken the operator's first discovery slot.
                canonical_symbol="AAAAADISCOVERY",
                listing_date=date(2020, 1, 2),
                base_currency="USD",
                quote_currency="USD",
                settlement_currency="USD",
                contract_multiplier=Decimal(1),
                contract_size=Decimal(1),
                tick_size=Decimal("0.01"),
                lot_size=Decimal(1),
                price_precision=2,
                quantity_precision=0,
                trading_timezone="America/New_York",
                market_session_type=SessionType.US_EQUITY,
                representation_kind=RepresentationKind.DIRECT,
                registered_at=datetime(2024, 1, 2, tzinfo=UTC),
                lifecycle_status=LifecycleStatus.ACTIVE,
            )
        )

        unfiltered = queries.instruments(limit=200, offset=0)
        self.assertEqual(
            [
                item.instrument_id
                for item in unfiltered.items
                if item.instrument_id.startswith(RESERVED_TEST_FIXTURE_PREFIX)
            ],
            [],
        )

        # Still fully discoverable under an explicit search, and still readable
        # in detail -- excluded from a default listing, never hidden.
        searched = queries.instruments(query=RESERVED_TEST_FIXTURE_PREFIX, limit=200, offset=0)
        self.assertIn(marker, [item.instrument_id for item in searched.items])
        self.assertEqual(queries.instrument(marker).instrument_id, marker)


if __name__ == "__main__":
    unittest.main()
