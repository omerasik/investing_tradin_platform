"""Pure unit evidence for Module 3H.1 futures contract semantics.

No database and no network. Every contract specification here is a FIXTURE
modelled on publicly documented CME/COMEX product parameters; none of it was
retrieved from an exchange, and it is not exchange-verified reference data.
"""

import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import pairwise

from trade_platform.domain import AssetClass
from trade_platform.futures_contracts import (
    ContinuousAdjustmentMethod,
    ContinuousSeriesPolicy,
    ContinuousSeriesPolicyError,
    FuturesAuthorityError,
    FuturesContractSeries,
    FuturesContractSpecification,
    FuturesContractSpecificationError,
    FuturesMarginError,
    FuturesMarginRequirement,
    MarginTier,
    RollTrigger,
    SettlementType,
    build_continuous_series_schedule,
    roll_date_for,
)
from trade_platform.professional_instruments import SessionType

REGISTERED_AT = datetime(2024, 1, 2, tzinfo=UTC)


def gold_series(
    series_id: str = "FUT:XCEC:GC",
    root_symbol: str = "GC",
    multiplier: Decimal = Decimal(100),
    tick_value: Decimal = Decimal("10.00"),
) -> FuturesContractSeries:
    return FuturesContractSeries(
        series_id=series_id,
        root_symbol=root_symbol,
        exchange_name="COMEX",
        venue="XCEC",
        mic="XCEC",
        asset_class=AssetClass.COMMODITY,
        underlying_reference="Gold",
        currency="USD",
        contract_multiplier=multiplier,
        unit_of_measure="TROY_OUNCE",
        tick_size=Decimal("0.10"),
        tick_value=tick_value,
        price_precision=2,
        quantity_precision=0,
        settlement_type=SettlementType.PHYSICAL_DELIVERY,
        trading_timezone="America/New_York",
        session_type=SessionType.FUTURES_23X5,
        registered_at=REGISTERED_AT,
        source_reference="fixture:cme-product-parameters",
    )


def gold_contract(
    month: int,
    *,
    year: int = 2025,
    series_id: str = "FUT:XCEC:GC",
    multiplier: Decimal = Decimal(100),
    tick_value: Decimal = Decimal("10.00"),
    registered_at: datetime = REGISTERED_AT,
    first_trade_date: date | None = None,
) -> FuturesContractSpecification:
    """One quarterly gold contract; dates are internally consistent fixtures."""
    last_trade = date(year, month, 26)
    return FuturesContractSpecification(
        instrument_id=f"FUT:XCEC:GC{month:02d}{year}",
        series_id=series_id,
        contract_code=f"GC{month:02d}{year}",
        contract_year=year,
        contract_month=month,
        first_trade_date=first_trade_date or date(year - 2, month, 1),
        first_notice_date=date(year, month, 25),
        last_trade_date=last_trade,
        expiration_date=last_trade,
        settlement_date=date(year, month, 28),
        settlement_type=SettlementType.PHYSICAL_DELIVERY,
        contract_multiplier=multiplier,
        tick_size=Decimal("0.10"),
        tick_value=tick_value,
        registered_at=registered_at,
        source_reference="fixture:cme-contract-calendar",
    )


class TickValueIdentityTests(unittest.TestCase):
    """The single invariant that keeps GC and MGC from collapsing into one product."""

    def test_full_size_and_micro_gold_are_structurally_distinct_products(self) -> None:
        full = gold_series()
        micro = gold_series(
            series_id="FUT:XCEC:MGC",
            root_symbol="MGC",
            multiplier=Decimal(10),
            tick_value=Decimal("1.00"),
        )
        # Same underlying, same currency, same quoted tick -- and yet one tick
        # is worth ten times more on the full-size contract.
        self.assertEqual(full.tick_size, micro.tick_size)
        self.assertEqual(full.underlying_reference, micro.underlying_reference)
        self.assertNotEqual(full.tick_value, micro.tick_value)
        self.assertEqual(full.tick_value / micro.tick_value, Decimal(10))

    def test_series_tick_value_inconsistent_with_multiplier_is_rejected(self) -> None:
        with self.assertRaises(FuturesContractSpecificationError) as raised:
            gold_series(multiplier=Decimal(10), tick_value=Decimal("10.00"))
        self.assertIn("tick_value_not_multiplier_consistent", str(raised.exception))

    def test_contract_tick_value_inconsistent_with_multiplier_is_rejected(self) -> None:
        with self.assertRaises(FuturesContractSpecificationError):
            gold_contract(6, multiplier=Decimal(10), tick_value=Decimal("10.00"))

    def test_zero_or_negative_units_are_rejected(self) -> None:
        for multiplier in (Decimal(0), Decimal(-100)):
            with self.assertRaises(FuturesContractSpecificationError):
                gold_series(multiplier=multiplier, tick_value=Decimal("0.10") * multiplier)


class ContractSpecificationValidationTests(unittest.TestCase):
    def test_last_trade_after_expiration_is_rejected(self) -> None:
        with self.assertRaises(FuturesContractSpecificationError) as raised:
            FuturesContractSpecification(
                instrument_id="FUT:XCEC:GCBAD",
                series_id="FUT:XCEC:GC",
                contract_code="GCBAD",
                contract_year=2025,
                contract_month=6,
                first_trade_date=date(2023, 6, 1),
                last_trade_date=date(2025, 6, 27),
                expiration_date=date(2025, 6, 26),
                settlement_date=date(2025, 6, 30),
                settlement_type=SettlementType.PHYSICAL_DELIVERY,
                contract_multiplier=Decimal(100),
                tick_size=Decimal("0.10"),
                tick_value=Decimal("10.00"),
                registered_at=REGISTERED_AT,
                source_reference="fixture",
            )
        self.assertIn("invalid_contract_trading_dates", str(raised.exception))

    def test_settlement_before_expiration_is_rejected(self) -> None:
        with self.assertRaises(FuturesContractSpecificationError) as raised:
            FuturesContractSpecification(
                instrument_id="FUT:XCEC:GCBAD",
                series_id="FUT:XCEC:GC",
                contract_code="GCBAD",
                contract_year=2025,
                contract_month=6,
                first_trade_date=date(2023, 6, 1),
                last_trade_date=date(2025, 6, 26),
                expiration_date=date(2025, 6, 26),
                settlement_date=date(2025, 6, 25),
                settlement_type=SettlementType.PHYSICAL_DELIVERY,
                contract_multiplier=Decimal(100),
                tick_size=Decimal("0.10"),
                tick_value=Decimal("10.00"),
                registered_at=REGISTERED_AT,
                source_reference="fixture",
            )
        self.assertIn("settlement_before_expiration", str(raised.exception))

    def test_cash_settled_contract_cannot_carry_a_first_notice_date(self) -> None:
        """A cash-settled product never issues a delivery notice."""
        with self.assertRaises(FuturesContractSpecificationError) as raised:
            FuturesContractSpecification(
                instrument_id="FUT:XCME:ES062025",
                series_id="FUT:XCME:ES",
                contract_code="ESM25",
                contract_year=2025,
                contract_month=6,
                first_trade_date=date(2023, 6, 1),
                first_notice_date=date(2025, 6, 19),
                last_trade_date=date(2025, 6, 20),
                expiration_date=date(2025, 6, 20),
                settlement_date=date(2025, 6, 20),
                settlement_type=SettlementType.CASH_SETTLED,
                contract_multiplier=Decimal(50),
                tick_size=Decimal("0.25"),
                tick_value=Decimal("12.50"),
                registered_at=REGISTERED_AT,
                source_reference="fixture",
            )
        self.assertIn("cash_settled_contract_cannot_have_first_notice", str(raised.exception))

    def test_month_code_is_derived_not_supplied(self) -> None:
        self.assertEqual(gold_contract(6).month_code, "M")
        self.assertEqual(gold_contract(12).month_code, "Z")
        self.assertEqual(gold_contract(2).month_code, "G")

    def test_naive_registration_timestamp_is_rejected(self) -> None:
        with self.assertRaises(FuturesAuthorityError) as raised:
            gold_contract(6, registered_at=datetime(2024, 1, 2))  # noqa: DTZ001
        self.assertIn("must_be_timezone_aware", str(raised.exception))


class MarginTwoClockTests(unittest.TestCase):
    def build(self, *, effective: datetime, known: datetime) -> FuturesMarginRequirement:
        return FuturesMarginRequirement(
            series_id="FUT:XCEC:GC",
            tier=MarginTier.SPECULATIVE,
            initial_margin=Decimal("12000.00"),
            maintenance_margin=Decimal("11000.00"),
            currency="USD",
            effective_from=effective,
            known_at=known,
            source_reference="fixture:exchange-margin-notice",
            source_hash="0" * 64,
        )

    def test_exchange_announcement_before_effective_date_is_accepted(self) -> None:
        """Margin changes are published ahead of time; known_at may precede effect."""
        requirement = self.build(
            effective=datetime(2025, 3, 10, tzinfo=UTC),
            known=datetime(2025, 3, 5, tzinfo=UTC),
        )
        self.assertLess(requirement.known_at, requirement.effective_from)

    def test_historical_backfill_after_effective_date_is_accepted(self) -> None:
        requirement = self.build(
            effective=datetime(2020, 3, 10, tzinfo=UTC),
            known=datetime(2026, 9, 7, tzinfo=UTC),
        )
        self.assertGreater(requirement.known_at, requirement.effective_from)

    def test_maintenance_above_initial_is_rejected(self) -> None:
        with self.assertRaises(FuturesMarginError) as raised:
            FuturesMarginRequirement(
                series_id="FUT:XCEC:GC",
                tier=MarginTier.SPECULATIVE,
                initial_margin=Decimal("11000.00"),
                maintenance_margin=Decimal("12000.00"),
                currency="USD",
                effective_from=datetime(2025, 3, 10, tzinfo=UTC),
                known_at=datetime(2025, 3, 5, tzinfo=UTC),
                source_reference="fixture",
                source_hash="0" * 64,
            )
        self.assertIn("maintenance_margin_exceeds_initial_margin", str(raised.exception))


class ContinuousSeriesPolicyTests(unittest.TestCase):
    def policy(
        self,
        trigger: RollTrigger = RollTrigger.CALENDAR_DAYS_BEFORE_LAST_TRADE,
        offset: int = 5,
        max_depth: int = 2,
    ) -> ContinuousSeriesPolicy:
        return ContinuousSeriesPolicy(
            series_id="FUT:XCEC:GC",
            policy_version=1,
            roll_trigger=trigger,
            roll_offset_days=offset,
            adjustment_method=ContinuousAdjustmentMethod.BACK_ADJUSTED_DIFFERENCE,
            max_depth=max_depth,
            economic_rationale="Roll before first notice to avoid delivery obligation.",
            approved_at=REGISTERED_AT,
            source_reference="fixture:policy",
        )

    def test_content_hash_is_stable_and_parameter_sensitive(self) -> None:
        baseline = self.policy().content_hash()
        self.assertEqual(baseline, self.policy().content_hash())
        self.assertNotEqual(baseline, self.policy(offset=6).content_hash())
        self.assertNotEqual(baseline, self.policy(max_depth=3).content_hash())

    def test_offset_on_an_exact_date_trigger_is_rejected(self) -> None:
        with self.assertRaises(ContinuousSeriesPolicyError) as raised:
            self.policy(trigger=RollTrigger.LAST_TRADE_DATE, offset=5)
        self.assertIn("roll_offset_not_applicable", str(raised.exception))

    def test_volume_open_interest_roll_fails_closed(self) -> None:
        """The professionally standard trigger is named but unusable without OI data."""
        policy = self.policy(trigger=RollTrigger.VOLUME_OPEN_INTEREST_CROSSOVER, offset=0)
        with self.assertRaises(ContinuousSeriesPolicyError) as raised:
            roll_date_for(policy, gold_contract(6))
        self.assertIn("requires_open_interest_authority", str(raised.exception))

    def test_first_notice_roll_without_a_notice_date_fails_closed(self) -> None:
        policy = self.policy(trigger=RollTrigger.FIRST_NOTICE_DATE, offset=0)
        cash_settled = FuturesContractSpecification(
            instrument_id="FUT:XCME:ES062025",
            series_id="FUT:XCEC:GC",
            contract_code="ESM25",
            contract_year=2025,
            contract_month=6,
            first_trade_date=date(2023, 6, 1),
            last_trade_date=date(2025, 6, 20),
            expiration_date=date(2025, 6, 20),
            settlement_date=date(2025, 6, 20),
            settlement_type=SettlementType.CASH_SETTLED,
            contract_multiplier=Decimal(50),
            tick_size=Decimal("0.25"),
            tick_value=Decimal("12.50"),
            registered_at=REGISTERED_AT,
            source_reference="fixture",
        )
        with self.assertRaises(ContinuousSeriesPolicyError) as raised:
            roll_date_for(policy, cash_settled)
        self.assertIn("first_notice_roll_requires_first_notice_date", str(raised.exception))

    def test_roll_dates_honour_trigger_and_offset(self) -> None:
        contract = gold_contract(6)
        self.assertEqual(
            roll_date_for(self.policy(trigger=RollTrigger.LAST_TRADE_DATE, offset=0), contract),
            date(2025, 6, 26),
        )
        self.assertEqual(
            roll_date_for(self.policy(offset=5), contract),
            date(2025, 6, 21),
        )
        self.assertEqual(
            roll_date_for(
                self.policy(trigger=RollTrigger.CALENDAR_DAYS_BEFORE_FIRST_NOTICE, offset=3),
                contract,
            ),
            date(2025, 6, 22),
        )


class ContinuousSeriesScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ContinuousSeriesPolicy(
            series_id="FUT:XCEC:GC",
            policy_version=1,
            roll_trigger=RollTrigger.CALENDAR_DAYS_BEFORE_FIRST_NOTICE,
            roll_offset_days=5,
            adjustment_method=ContinuousAdjustmentMethod.NONE,
            max_depth=2,
            economic_rationale="Exit five calendar days before any delivery notice.",
            approved_at=REGISTERED_AT,
            source_reference="fixture:policy",
        )
        self.contracts = [gold_contract(month) for month in (2, 4, 6, 8, 12)]

    def test_front_month_schedule_is_contiguous_and_ordered(self) -> None:
        members = build_continuous_series_schedule(
            self.policy,
            self.contracts,
            trading_timezone="America/New_York",
            known_at=datetime(2025, 1, 1, tzinfo=UTC),
            depth=1,
        )
        self.assertEqual(len(members), len(self.contracts))
        self.assertEqual(
            [member.instrument_id for member in members],
            [contract.instrument_id for contract in self.contracts],
        )
        for earlier, later in pairwise(members):
            # Gapless and non-overlapping: the successor starts exactly where
            # the predecessor stops.
            self.assertEqual(earlier.effective_until, later.effective_from)
            self.assertLess(later.effective_from, later.effective_until)

    def test_final_member_is_closed_not_open_ended(self) -> None:
        """Beyond the last known contract's roll, the series must fail closed."""
        members = build_continuous_series_schedule(
            self.policy,
            self.contracts,
            trading_timezone="America/New_York",
            known_at=datetime(2025, 1, 1, tzinfo=UTC),
            depth=1,
        )
        last = members[-1]
        # The December contract is the front month from the August roll until
        # its own roll, and then the series simply stops -- the contract that
        # would succeed it is not yet known, so nothing is extrapolated.
        self.assertEqual(last.effective_from.date(), date(2025, 8, 20))
        self.assertEqual(last.effective_until.date(), date(2025, 12, 20))

    def test_second_depth_trails_the_front_month_by_exactly_one_contract(self) -> None:
        front = build_continuous_series_schedule(
            self.policy,
            self.contracts,
            trading_timezone="America/New_York",
            known_at=datetime(2025, 1, 1, tzinfo=UTC),
            depth=1,
        )
        second = build_continuous_series_schedule(
            self.policy,
            self.contracts,
            trading_timezone="America/New_York",
            known_at=datetime(2025, 1, 1, tzinfo=UTC),
            depth=2,
        )
        at = datetime(2025, 3, 1, tzinfo=UTC)

        def active(members: tuple, moment: datetime) -> str:
            return next(
                member.instrument_id
                for member in members
                if member.effective_from <= moment < member.effective_until
            )

        self.assertEqual(active(front, at), "FUT:XCEC:GC042025")
        self.assertEqual(active(second, at), "FUT:XCEC:GC062025")

    def test_depth_beyond_policy_maximum_is_rejected(self) -> None:
        with self.assertRaises(ContinuousSeriesPolicyError) as raised:
            build_continuous_series_schedule(
                self.policy,
                self.contracts,
                trading_timezone="America/New_York",
                known_at=datetime(2025, 1, 1, tzinfo=UTC),
                depth=3,
            )
        self.assertIn("depth_exceeds_policy_max_depth", str(raised.exception))

    def test_contracts_unknown_at_knowledge_time_are_excluded(self) -> None:
        """A schedule materialized in the past cannot reference a later onboarding."""
        late = gold_contract(10, registered_at=datetime(2026, 1, 1, tzinfo=UTC))
        members = build_continuous_series_schedule(
            self.policy,
            [*self.contracts, late],
            trading_timezone="America/New_York",
            known_at=datetime(2025, 1, 1, tzinfo=UTC),
            depth=1,
        )
        self.assertNotIn(late.instrument_id, [member.instrument_id for member in members])

        with_later_knowledge = build_continuous_series_schedule(
            self.policy,
            [*self.contracts, late],
            trading_timezone="America/New_York",
            known_at=datetime(2026, 6, 1, tzinfo=UTC),
            depth=1,
        )
        self.assertIn(
            late.instrument_id, [member.instrument_id for member in with_later_knowledge]
        )

    def test_contracts_from_another_series_are_excluded(self) -> None:
        foreign = gold_contract(3, series_id="FUT:XCEC:MGC")
        members = build_continuous_series_schedule(
            self.policy,
            [*self.contracts, foreign],
            trading_timezone="America/New_York",
            known_at=datetime(2025, 1, 1, tzinfo=UTC),
            depth=1,
        )
        self.assertEqual(len(members), len(self.contracts))

    def test_contract_not_yet_listed_at_its_roll_fails_closed(self) -> None:
        """Rather than fabricate a segment or leave a silent gap in the series."""
        contracts = [
            gold_contract(2),
            gold_contract(4, first_trade_date=date(2025, 3, 1)),
        ]
        with self.assertRaises(ContinuousSeriesPolicyError) as raised:
            build_continuous_series_schedule(
                self.policy,
                contracts,
                trading_timezone="America/New_York",
                known_at=datetime(2025, 1, 1, tzinfo=UTC),
                depth=1,
            )
        self.assertIn("contract_not_listed_at_roll", str(raised.exception))

    def test_insufficient_known_contracts_for_depth_fails_closed(self) -> None:
        with self.assertRaises(ContinuousSeriesPolicyError) as raised:
            build_continuous_series_schedule(
                self.policy,
                [gold_contract(2)],
                trading_timezone="America/New_York",
                known_at=datetime(2025, 1, 1, tzinfo=UTC),
                depth=2,
            )
        self.assertIn("insufficient_known_contracts_for_depth", str(raised.exception))

    def test_unknown_timezone_fails_closed(self) -> None:
        with self.assertRaises(ContinuousSeriesPolicyError) as raised:
            build_continuous_series_schedule(
                self.policy,
                self.contracts,
                trading_timezone="Mars/Olympus_Mons",
                known_at=datetime(2025, 1, 1, tzinfo=UTC),
                depth=1,
            )
        self.assertIn("invalid_trading_timezone", str(raised.exception))

    def test_schedule_is_deterministic_across_input_ordering(self) -> None:
        forward = build_continuous_series_schedule(
            self.policy,
            self.contracts,
            trading_timezone="America/New_York",
            known_at=datetime(2025, 1, 1, tzinfo=UTC),
            depth=1,
        )
        reversed_input = build_continuous_series_schedule(
            self.policy,
            list(reversed(self.contracts)),
            trading_timezone="America/New_York",
            known_at=datetime(2025, 1, 1, tzinfo=UTC),
            depth=1,
        )
        self.assertEqual(
            [(m.instrument_id, m.effective_from, m.effective_until) for m in forward],
            [(m.instrument_id, m.effective_from, m.effective_until) for m in reversed_input],
        )


if __name__ == "__main__":
    unittest.main()
