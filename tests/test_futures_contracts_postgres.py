"""Real PostgreSQL evidence for Module 3H.1 futures contract authority.

Every integration test file in this suite shares one PostgreSQL database for
the whole CI run with no reset between files, so this file registers its own
uniquely-namespaced fixture instruments rather than reusing
``mvp_instrument_universe()``.

Those ids sit under ``operator_dashboard.RESERVED_TEST_FIXTURE_PREFIX``
(``TESTFIXTURE:``), which keeps them off the operator's unfiltered instrument
discovery page. Earlier modules relied on naming fixtures so they sorted last
under ``ORDER BY canonical_symbol``; the reserved prefix replaces that
convention with something the code enforces.

Contract parameters are FIXTURES modelled on publicly documented COMEX/CME
product specifications. Nothing here was retrieved from an exchange, and no
row in these tables is exchange-verified reference data.
"""

import os
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class FuturesContractAuthorityPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_futures_authority_end_to_end(self) -> None:
        from trade_platform.domain import AssetClass
        from trade_platform.futures_contracts import (
            ContinuousAdjustmentMethod,
            ContinuousSeriesPolicy,
            ContinuousSeriesPolicyError,
            ContinuousSeriesResolutionError,
            FuturesContractSeries,
            FuturesContractSpecification,
            FuturesContractSpecificationError,
            FuturesMarginError,
            FuturesMarginRequirement,
            MarginTier,
            PostgresFuturesContractAuthority,
            RollTrigger,
            SettlementType,
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

        dsn = os.environ["POSTGRES_TEST_DSN"]
        database = PostgresDatabase(dsn)
        master = PostgresProfessionalInstrumentMaster(database)
        authority = PostgresFuturesContractAuthority(database)
        registered_at = datetime(2024, 1, 2, tzinfo=UTC)

        def register_future(
            suffix: str, symbol: str, *, multiplier: Decimal, tick_value: Decimal
        ) -> str:
            instrument_id = f"TESTFIXTURE:FUT:XCEC:{suffix}"
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id,
                    asset_class=AssetClass.COMMODITY,
                    instrument_type=InstrumentType.FUTURE,
                    exchange_name="COMEX",
                    venue="XCEC",
                    mic="XCEC",
                    canonical_symbol=symbol,
                    listing_date=date(2023, 1, 3),
                    base_currency="USD",
                    quote_currency="USD",
                    settlement_currency="USD",
                    contract_multiplier=multiplier,
                    contract_size=multiplier,
                    tick_size=Decimal("0.10"),
                    lot_size=Decimal(1),
                    price_precision=2,
                    quantity_precision=0,
                    trading_timezone="America/New_York",
                    market_session_type=SessionType.FUTURES_23X5,
                    representation_kind=RepresentationKind.FUTURE,
                    registered_at=registered_at,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                    contract_code=symbol,
                    expiration_date=date(2025, 6, 26),
                    first_notice_date=date(2025, 6, 25),
                    last_trade_date=date(2025, 6, 26),
                    roll_rule="FUTTEST_POLICY_V1",
                )
            )
            return instrument_id

        # The FUTURES_23X5 session type is new in migration 0041; registering
        # at all proves the widened CHECK constraint took effect.
        series = FuturesContractSeries(
            series_id="TESTFIXTURE:FUT:XCEC:GC",
            root_symbol="TESTFIXGC",
            exchange_name="COMEX",
            venue="XCEC",
            mic="XCEC",
            asset_class=AssetClass.COMMODITY,
            underlying_reference="Gold",
            currency="USD",
            contract_multiplier=Decimal(100),
            unit_of_measure="TROY_OUNCE",
            tick_size=Decimal("0.10"),
            tick_value=Decimal("10.00"),
            price_precision=2,
            quantity_precision=0,
            settlement_type=SettlementType.PHYSICAL_DELIVERY,
            trading_timezone="America/New_York",
            session_type=SessionType.FUTURES_23X5,
            registered_at=registered_at,
            source_reference="fixture:cme-product-parameters",
        )
        authority.register_series(series)
        reloaded = authority.get_series(series.series_id)
        self.assertEqual(reloaded.tick_value, Decimal("10.000000000000"))
        self.assertEqual(reloaded.session_type, SessionType.FUTURES_23X5)

        # ---- GC and MGC never collapse into one product -----------------------
        micro = FuturesContractSeries(
            series_id="TESTFIXTURE:FUT:XCEC:MGC",
            root_symbol="TESTFIXMGC",
            exchange_name="COMEX",
            venue="XCEC",
            mic="XCEC",
            asset_class=AssetClass.COMMODITY,
            underlying_reference="Gold",
            currency="USD",
            contract_multiplier=Decimal(10),
            unit_of_measure="TROY_OUNCE",
            tick_size=Decimal("0.10"),
            tick_value=Decimal("1.00"),
            price_precision=2,
            quantity_precision=0,
            settlement_type=SettlementType.PHYSICAL_DELIVERY,
            trading_timezone="America/New_York",
            session_type=SessionType.FUTURES_23X5,
            registered_at=registered_at,
            source_reference="fixture:cme-product-parameters",
        )
        authority.register_series(micro)
        self.assertNotEqual(
            authority.get_series("TESTFIXTURE:FUT:XCEC:GC").tick_value,
            authority.get_series("TESTFIXTURE:FUT:XCEC:MGC").tick_value,
        )

        # The database itself, not just the Python contract, rejects a tick
        # value that contradicts the multiplier.
        with self.assertRaises(Exception), database.transaction() as connection, connection.cursor() as cursor:  # noqa: B017
            cursor.execute(
                "INSERT INTO futures_contract_series VALUES (" + ",".join(["%s"] * 19) + ")",
                (
                    "TESTFIXTURE:FUT:XCEC:BROKEN", "TESTFIXBROKEN", "COMEX", "XCEC", "XCEC", "COMMODITY",
                    "Gold", "USD", Decimal(10), "TROY_OUNCE", Decimal("0.10"), Decimal("10.00"),
                    2, 0, "PHYSICAL_DELIVERY", "America/New_York", "FUTURES_23X5", registered_at,
                    "fixture",
                ),
            )

        # ---- contracts must bind to a registered FUTURE instrument ------------
        months = (2, 4, 6, 8, 12)
        instrument_ids = {
            month: register_future(
                f"GC{month:02d}2025", f"TESTFIXGC{month:02d}25",
                multiplier=Decimal(100), tick_value=Decimal("10.00"),
            )
            for month in months
        }

        def specification(month: int, **overrides: object) -> FuturesContractSpecification:
            fields: dict[str, object] = {
                "instrument_id": instrument_ids[month],
                "series_id": "TESTFIXTURE:FUT:XCEC:GC",
                "contract_code": f"TESTFIXGC{month:02d}25",
                "contract_year": 2025,
                "contract_month": month,
                "first_trade_date": date(2023, month, 1),
                "first_notice_date": date(2025, month, 25),
                "last_trade_date": date(2025, month, 26),
                "expiration_date": date(2025, month, 26),
                "settlement_date": date(2025, month, 28),
                "settlement_type": SettlementType.PHYSICAL_DELIVERY,
                "contract_multiplier": Decimal(100),
                "tick_size": Decimal("0.10"),
                "tick_value": Decimal("10.00"),
                "registered_at": registered_at,
                "source_reference": "fixture:cme-contract-calendar",
            }
            fields.update(overrides)
            return FuturesContractSpecification(**fields)  # type: ignore[arg-type]

        with self.assertRaises(FuturesContractSpecificationError) as raised:
            authority.specify_contract(
                specification(6, instrument_id="TESTFIXTURE:FUT:XCEC:NEVER_REGISTERED")
            )
        self.assertIn("contract_instrument_not_registered", str(raised.exception))

        # An equity instrument can never be given a futures contract spec.
        master.register(
            ProfessionalInstrument(
                instrument_id="TESTFIXTURE:FUT:XNAS:EQUITY",
                asset_class=AssetClass.EQUITY,
                instrument_type=InstrumentType.COMMON_STOCK,
                exchange_name="NASDAQ",
                venue="XNAS",
                mic="XNAS",
                canonical_symbol="TESTFIXEQ",
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
                registered_at=registered_at,
                lifecycle_status=LifecycleStatus.ACTIVE,
            )
        )
        with self.assertRaises(FuturesContractSpecificationError) as raised:
            authority.specify_contract(specification(6, instrument_id="TESTFIXTURE:FUT:XNAS:EQUITY"))
        self.assertIn("instrument_is_not_a_future", str(raised.exception))

        for month in months:
            authority.specify_contract(specification(month))

        # Two different delivery months of one series cannot share a month slot.
        duplicate_instrument = register_future(
            "GC062025DUP", "TESTFIXGC0625D", multiplier=Decimal(100), tick_value=Decimal("10.00")
        )
        with self.assertRaises(FuturesContractSpecificationError):
            authority.specify_contract(
                specification(6, instrument_id=duplicate_instrument, contract_code="TESTFIXDUP")
            )

        stored = authority.contracts_for_series("TESTFIXTURE:FUT:XCEC:GC", known_at=registered_at)
        self.assertEqual(len(stored), len(months))
        self.assertEqual([contract.month_code for contract in stored], ["G", "J", "M", "Q", "Z"])
        self.assertEqual(stored[0].tick_value, Decimal("10.000000000000"))

        # ---- margin has two independent clocks --------------------------------
        announced_at = datetime(2025, 3, 1, tzinfo=UTC)
        effective_at = datetime(2025, 3, 10, tzinfo=UTC)
        authority.record_margin_requirement(
            FuturesMarginRequirement(
                series_id="TESTFIXTURE:FUT:XCEC:GC",
                tier=MarginTier.SPECULATIVE,
                initial_margin=Decimal("12000.00"),
                maintenance_margin=Decimal("11000.00"),
                currency="USD",
                effective_from=datetime(2024, 1, 2, tzinfo=UTC),
                known_at=datetime(2024, 1, 2, tzinfo=UTC),
                source_reference="fixture:margin-baseline",
                source_hash="a" * 64,
            )
        )
        authority.record_margin_requirement(
            FuturesMarginRequirement(
                series_id="TESTFIXTURE:FUT:XCEC:GC",
                tier=MarginTier.SPECULATIVE,
                initial_margin=Decimal("15000.00"),
                maintenance_margin=Decimal("13500.00"),
                currency="USD",
                effective_from=effective_at,
                known_at=announced_at,
                source_reference="fixture:margin-increase-notice",
                source_hash="b" * 64,
            )
        )

        # Announced on 1 March, effective 10 March: on 5 March we already know
        # about it, but it is not yet in force.
        in_force = authority.margin_point_in_time(
            "TESTFIXTURE:FUT:XCEC:GC",
            MarginTier.SPECULATIVE,
            effective_at=datetime(2025, 3, 5, tzinfo=UTC),
            known_at=datetime(2025, 3, 5, tzinfo=UTC),
        )
        self.assertEqual(in_force.initial_margin, Decimal("12000.000000000000"))
        after = authority.margin_point_in_time(
            "TESTFIXTURE:FUT:XCEC:GC",
            MarginTier.SPECULATIVE,
            effective_at=datetime(2025, 3, 15, tzinfo=UTC),
            known_at=datetime(2025, 3, 15, tzinfo=UTC),
        )
        self.assertEqual(after.initial_margin, Decimal("15000.000000000000"))
        # And a replay that predates the announcement cannot see it at all,
        # even though the effective date has passed.
        unknown_yet = authority.margin_point_in_time(
            "TESTFIXTURE:FUT:XCEC:GC",
            MarginTier.SPECULATIVE,
            effective_at=datetime(2025, 3, 15, tzinfo=UTC),
            known_at=datetime(2025, 2, 1, tzinfo=UTC),
        )
        self.assertEqual(unknown_yet.initial_margin, Decimal("12000.000000000000"))

        # A contract-level requirement outranks the series-level default.
        authority.record_margin_requirement(
            FuturesMarginRequirement(
                series_id="TESTFIXTURE:FUT:XCEC:GC",
                instrument_id=instrument_ids[6],
                tier=MarginTier.SPECULATIVE,
                initial_margin=Decimal("18000.00"),
                maintenance_margin=Decimal("16000.00"),
                currency="USD",
                effective_from=datetime(2025, 3, 12, tzinfo=UTC),
                known_at=datetime(2025, 3, 12, tzinfo=UTC),
                source_reference="fixture:spot-month-margin",
                source_hash="c" * 64,
            )
        )
        contract_level = authority.margin_point_in_time(
            "TESTFIXTURE:FUT:XCEC:GC",
            MarginTier.SPECULATIVE,
            effective_at=datetime(2025, 3, 20, tzinfo=UTC),
            known_at=datetime(2025, 3, 20, tzinfo=UTC),
            instrument_id=instrument_ids[6],
        )
        self.assertEqual(contract_level.initial_margin, Decimal("18000.000000000000"))
        self.assertEqual(contract_level.instrument_id, instrument_ids[6])
        # A sibling contract still gets the series-level number.
        sibling = authority.margin_point_in_time(
            "TESTFIXTURE:FUT:XCEC:GC",
            MarginTier.SPECULATIVE,
            effective_at=datetime(2025, 3, 20, tzinfo=UTC),
            known_at=datetime(2025, 3, 20, tzinfo=UTC),
            instrument_id=instrument_ids[8],
        )
        self.assertIsNone(sibling.instrument_id)
        self.assertEqual(sibling.initial_margin, Decimal("15000.000000000000"))
        # No hedger requirement was ever recorded: fail closed, never fall back
        # to the speculative tier.
        with self.assertRaises(FuturesMarginError) as raised:
            authority.margin_point_in_time(
                "TESTFIXTURE:FUT:XCEC:GC",
                MarginTier.HEDGER,
                effective_at=datetime(2025, 3, 20, tzinfo=UTC),
                known_at=datetime(2025, 3, 20, tzinfo=UTC),
            )
        self.assertIn("margin_requirement_not_available", str(raised.exception))

        # ---- continuous series is a versioned, materialized policy ------------
        policy = ContinuousSeriesPolicy(
            series_id="TESTFIXTURE:FUT:XCEC:GC",
            policy_version=1,
            roll_trigger=RollTrigger.CALENDAR_DAYS_BEFORE_FIRST_NOTICE,
            roll_offset_days=5,
            adjustment_method=ContinuousAdjustmentMethod.BACK_ADJUSTED_DIFFERENCE,
            max_depth=2,
            economic_rationale="Exit five calendar days before any delivery notice.",
            approved_at=registered_at,
            source_reference="fixture:policy",
        )
        authority.register_continuous_policy(policy)
        self.assertEqual(
            authority.get_continuous_policy("TESTFIXTURE:FUT:XCEC:GC", 1).content_hash(),
            policy.content_hash(),
        )

        materialization_known_at = datetime(2025, 1, 1, tzinfo=UTC)
        members = authority.materialize_continuous_series(
            "TESTFIXTURE:FUT:XCEC:GC", 1, known_at=materialization_known_at, depth=1
        )
        self.assertEqual(len(members), len(months))

        resolved = authority.resolve_continuous_contract(
            "TESTFIXTURE:FUT:XCEC:GC",
            1,
            effective_at=datetime(2025, 3, 1, tzinfo=UTC),
            known_at=datetime(2025, 3, 1, tzinfo=UTC),
        )
        self.assertEqual(resolved, instrument_ids[4])

        # Materializing the same depth twice is refused by the database's own
        # non-overlap constraint rather than silently duplicating history.
        with self.assertRaises(ContinuousSeriesPolicyError) as raised:
            authority.materialize_continuous_series(
                "TESTFIXTURE:FUT:XCEC:GC", 1, known_at=materialization_known_at, depth=1
            )
        self.assertIn("continuous_member_overlap_or_duplicate", str(raised.exception))

        # A read that predates the materialization sees nothing -- knowledge
        # time gates the derived series exactly as it gates raw evidence.
        with self.assertRaises(ContinuousSeriesResolutionError) as raised:
            authority.resolve_continuous_contract(
                "TESTFIXTURE:FUT:XCEC:GC",
                1,
                effective_at=datetime(2025, 3, 1, tzinfo=UTC),
                known_at=datetime(2024, 6, 1, tzinfo=UTC),
            )
        self.assertIn("no_continuous_contract", str(raised.exception))

        # Past the final known roll the series stops rather than extrapolating.
        with self.assertRaises(ContinuousSeriesResolutionError):
            authority.resolve_continuous_contract(
                "TESTFIXTURE:FUT:XCEC:GC",
                1,
                effective_at=datetime(2026, 1, 1, tzinfo=UTC),
                known_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

        # Depth 2 coexists with depth 1 and names a different real contract.
        authority.materialize_continuous_series(
            "TESTFIXTURE:FUT:XCEC:GC", 1, known_at=materialization_known_at, depth=2
        )
        self.assertEqual(
            authority.resolve_continuous_contract(
                "TESTFIXTURE:FUT:XCEC:GC",
                1,
                effective_at=datetime(2025, 3, 1, tzinfo=UTC),
                known_at=datetime(2025, 3, 1, tzinfo=UTC),
                depth=2,
            ),
            instrument_ids[6],
        )

        # ---- immutability and restart durability -----------------------------
        for table, column in (
            ("futures_contract_series", "root_symbol"),
            ("futures_contract_specifications", "contract_code"),
            ("futures_margin_requirements", "source_reference"),
            ("futures_continuous_series_policies", "economic_rationale"),
            ("futures_continuous_series_members", "roll_reason"),
        ):
            with self.assertRaises(Exception), database.transaction() as connection, connection.cursor() as cursor:  # noqa: B017
                cursor.execute(f"UPDATE {table} SET {column} = 'tampered'")
            with self.assertRaises(Exception), database.transaction() as connection, connection.cursor() as cursor:  # noqa: B017
                cursor.execute(f"DELETE FROM {table}")

        restarted = PostgresFuturesContractAuthority(PostgresDatabase(dsn))
        self.assertEqual(
            restarted.resolve_continuous_contract(
                "TESTFIXTURE:FUT:XCEC:GC",
                1,
                effective_at=datetime(2025, 3, 1, tzinfo=UTC),
                known_at=datetime(2025, 3, 1, tzinfo=UTC),
            ),
            instrument_ids[4],
        )
        self.assertEqual(
            restarted.get_series("TESTFIXTURE:FUT:XCEC:GC").contract_multiplier,
            Decimal("100.000000000000"),
        )
        self.assertEqual(
            restarted.get_continuous_policy("TESTFIXTURE:FUT:XCEC:GC", 1).roll_offset_days, 5
        )

        # ---- the existing instrument authority is untouched --------------------
        equity = master.get_as_of("TESTFIXTURE:FUT:XNAS:EQUITY", datetime(2025, 1, 1, tzinfo=UTC))
        self.assertEqual(equity.instrument_type, InstrumentType.COMMON_STOCK)
        self.assertEqual(equity.market_session_type, SessionType.US_EQUITY)


if __name__ == "__main__":
    unittest.main()
