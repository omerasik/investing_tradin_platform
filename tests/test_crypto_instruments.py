"""Pure unit evidence for Module 3H.2 crypto instrument semantics.

No database and no network. Every asset, venue and contract parameter here is a
FIXTURE. Venue names such as BINANCE or COINBASE are used only as realistic
placeholder strings; nothing in this file was retrieved from, or verified
against, any exchange, and no venue's real trading rules are claimed.
"""

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trade_platform.crypto_instruments import (
    CryptoFundingConvention,
    CryptoFundingConventionError,
    CryptoInstrumentError,
    CryptoInstrumentKind,
    CryptoInstrumentSpecification,
    CryptoSettlementType,
    CryptoSpecificationError,
    CryptoVenueRuleError,
    CryptoVenueTradingRules,
    ReferencePriceRequirement,
    SettlementStyle,
)

REGISTERED_AT = datetime(2024, 1, 2, tzinfo=UTC)
EXPIRY = datetime(2025, 6, 27, 8, 0, tzinfo=UTC)


def spot(**overrides: object) -> CryptoInstrumentSpecification:
    fields: dict[str, object] = {
        "instrument_id": "TESTFIXTURE:CRY:BINANCE:BTCUSDT:SPOT",
        "venue": "BINANCE",
        "kind": CryptoInstrumentKind.SPOT,
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "settlement_type": CryptoSettlementType.PHYSICAL_DELIVERY,
        "contract_multiplier": Decimal(1),
        "contract_size": Decimal(1),
        "registered_at": REGISTERED_AT,
        "source_reference": "fixture:venue-instrument-list",
    }
    fields.update(overrides)
    return CryptoInstrumentSpecification(**fields)  # type: ignore[arg-type]


def perpetual(**overrides: object) -> CryptoInstrumentSpecification:
    fields: dict[str, object] = {
        "instrument_id": "TESTFIXTURE:CRY:BINANCE:BTCUSDT:PERP",
        "venue": "BINANCE",
        "kind": CryptoInstrumentKind.PERPETUAL,
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "settlement_asset": "USDT",
        "settlement_style": SettlementStyle.LINEAR,
        "settlement_type": CryptoSettlementType.CASH_SETTLED,
        "contract_multiplier": Decimal(1),
        "contract_size": Decimal(1),
        "reference_price_requirement": ReferencePriceRequirement.MARK_AND_INDEX,
        "registered_at": REGISTERED_AT,
        "source_reference": "fixture:venue-instrument-list",
    }
    fields.update(overrides)
    return CryptoInstrumentSpecification(**fields)  # type: ignore[arg-type]


def dated(**overrides: object) -> CryptoInstrumentSpecification:
    fields: dict[str, object] = {
        "instrument_id": "TESTFIXTURE:CRY:BINANCE:BTCUSDT:20250627",
        "kind": CryptoInstrumentKind.DATED_FUTURE,
        "expiry_at": EXPIRY,
    }
    fields.update(overrides)
    return perpetual(**fields)


class KindFieldInvariantTests(unittest.TestCase):
    """Invariants 1-4: each kind's forbidden and required fields."""

    def test_spot_with_expiry_is_rejected(self) -> None:
        with self.assertRaises(CryptoSpecificationError) as raised:
            spot(expiry_at=EXPIRY)
        self.assertIn("spot_instrument_cannot_have_expiry", str(raised.exception))

    def test_spot_with_settlement_style_is_rejected(self) -> None:
        with self.assertRaises(CryptoSpecificationError) as raised:
            spot(settlement_asset="USDT", settlement_style=SettlementStyle.LINEAR)
        self.assertIn("spot_instrument_cannot_have_settlement_style", str(raised.exception))

    def test_spot_requiring_a_mark_price_is_rejected(self) -> None:
        """A mark price is a derivative construct; spot has only its own trades."""
        with self.assertRaises(CryptoSpecificationError) as raised:
            spot(reference_price_requirement=ReferencePriceRequirement.MARK_AND_INDEX)
        self.assertIn("spot_instrument_cannot_require_reference_price", str(raised.exception))

    def test_spot_cannot_be_cash_settled(self) -> None:
        with self.assertRaises(CryptoSpecificationError) as raised:
            spot(settlement_type=CryptoSettlementType.CASH_SETTLED)
        self.assertIn("spot_instrument_must_be_physically_delivered", str(raised.exception))

    def test_perpetual_with_expiry_is_rejected(self) -> None:
        with self.assertRaises(CryptoSpecificationError) as raised:
            perpetual(expiry_at=EXPIRY)
        self.assertIn("perpetual_instrument_cannot_have_expiry", str(raised.exception))

    def test_dated_future_without_expiry_is_rejected(self) -> None:
        with self.assertRaises(CryptoSpecificationError) as raised:
            dated(expiry_at=None)
        self.assertIn("dated_future_requires_expiry", str(raised.exception))

    def test_derivative_without_settlement_style_is_rejected(self) -> None:
        with self.assertRaises(CryptoSpecificationError) as raised:
            perpetual(settlement_style=None)
        self.assertIn("derivative_requires_settlement_asset_and_style", str(raised.exception))

    def test_derivative_without_reference_price_semantics_is_rejected(self) -> None:
        with self.assertRaises(CryptoSpecificationError) as raised:
            perpetual(reference_price_requirement=ReferencePriceRequirement.NONE)
        self.assertIn("derivative_requires_reference_price_semantics", str(raised.exception))

    def test_only_a_perpetual_is_subject_to_funding(self) -> None:
        """Invariant 2, stated positively -- funding is a property of the kind."""
        self.assertTrue(perpetual().requires_funding)
        self.assertFalse(spot().requires_funding)
        self.assertFalse(dated().requires_funding)


class SettlementCoherenceTests(unittest.TestCase):
    """Invariants 5 and 8: linear/inverse/quanto and asset combinations."""

    def test_linear_must_settle_in_the_quote_asset(self) -> None:
        with self.assertRaises(CryptoSpecificationError) as raised:
            perpetual(settlement_asset="BTC", settlement_style=SettlementStyle.LINEAR)
        self.assertIn("linear_contract_must_settle_in_quote_asset", str(raised.exception))

    def test_inverse_must_settle_in_the_base_asset(self) -> None:
        """A coin-margined contract settling its quote asset is incoherent."""
        with self.assertRaises(CryptoSpecificationError) as raised:
            perpetual(
                quote_asset="USD",
                settlement_asset="USD",
                settlement_style=SettlementStyle.INVERSE,
            )
        self.assertIn("inverse_contract_must_settle_in_base_asset", str(raised.exception))

    def test_coherent_inverse_contract_is_accepted(self) -> None:
        contract = perpetual(
            instrument_id="TESTFIXTURE:CRY:BINANCE:BTCUSD:PERP",
            quote_asset="USD",
            settlement_asset="BTC",
            settlement_style=SettlementStyle.INVERSE,
        )
        self.assertEqual(contract.settlement_asset, contract.base_asset)

    def test_quanto_must_settle_in_a_third_asset(self) -> None:
        with self.assertRaises(CryptoSpecificationError) as raised:
            perpetual(settlement_asset="USDT", settlement_style=SettlementStyle.QUANTO)
        self.assertIn("quanto_contract_must_settle_in_a_third_asset", str(raised.exception))

    def test_coherent_quanto_contract_is_accepted(self) -> None:
        contract = perpetual(
            instrument_id="TESTFIXTURE:CRY:BINANCE:ETHUSD:QUANTO",
            base_asset="ETH",
            quote_asset="USD",
            settlement_asset="BTC",
            settlement_style=SettlementStyle.QUANTO,
        )
        self.assertNotIn(contract.settlement_asset, (contract.base_asset, contract.quote_asset))

    def test_identical_base_and_quote_is_rejected(self) -> None:
        with self.assertRaises(CryptoSpecificationError) as raised:
            spot(base_asset="BTC", quote_asset="BTC")
        self.assertIn("base_and_quote_asset_must_differ", str(raised.exception))

    def test_malformed_asset_codes_are_rejected(self) -> None:
        for bad in ("", "b", "usdt", "US DT", "USD-T", "A" * 13):
            with self.assertRaises(CryptoSpecificationError):
                spot(quote_asset=bad)


class VenueIdentityTests(unittest.TestCase):
    """Invariant 7 and the venue-is-part-of-the-instrument rule."""

    def test_placeholder_venue_is_rejected(self) -> None:
        """The shipped MVP universe's venue-less "CRYPTO" placeholder is not a venue."""
        for placeholder in ("CRYPTO", "crypto", "UNKNOWN", "DEFAULT"):
            with self.assertRaises(CryptoSpecificationError) as raised:
                spot(venue=placeholder)
            self.assertIn("placeholder_venue_is_not_a_venue", str(raised.exception))

    def test_same_pair_on_two_venues_are_two_specifications(self) -> None:
        binance = spot()
        coinbase = spot(
            instrument_id="TESTFIXTURE:CRY:COINBASE:BTCUSDT:SPOT", venue="COINBASE"
        )
        self.assertNotEqual(binance.instrument_id, coinbase.instrument_id)
        self.assertNotEqual(binance.venue, coinbase.venue)

    def test_blank_venue_is_rejected(self) -> None:
        with self.assertRaises(CryptoInstrumentError) as raised:
            spot(venue="   ")
        self.assertIn("invalid_venue", str(raised.exception))


class KindSeparationTests(unittest.TestCase):
    """Invariant 14: spot, perpetual and dated future never stand in for each other."""

    def test_three_kinds_on_one_pair_are_three_distinct_instruments(self) -> None:
        instruments = (spot(), perpetual(), dated())
        identifiers = {contract.instrument_id for contract in instruments}
        kinds = {contract.kind for contract in instruments}
        self.assertEqual(len(identifiers), 3)
        self.assertEqual(len(kinds), 3)

    def test_only_the_dated_future_carries_an_expiry(self) -> None:
        self.assertIsNone(spot().expiry_at)
        self.assertIsNone(perpetual().expiry_at)
        self.assertEqual(dated().expiry_at, EXPIRY)

    def test_only_derivatives_require_reference_prices(self) -> None:
        self.assertEqual(spot().reference_price_requirement, ReferencePriceRequirement.NONE)
        for derivative in (perpetual(), dated()):
            self.assertNotEqual(
                derivative.reference_price_requirement, ReferencePriceRequirement.NONE
            )


class FundingConventionTests(unittest.TestCase):
    def build(self, **overrides: object) -> CryptoFundingConvention:
        fields: dict[str, object] = {
            "instrument_id": "TESTFIXTURE:CRY:BINANCE:BTCUSDT:PERP",
            "convention_version": 1,
            "interval_hours": Decimal(8),
            "first_funding_offset_hours": Decimal(0),
            "funding_settlement_asset": "USDT",
            "effective_from": datetime(2024, 1, 2, tzinfo=UTC),
            "known_at": datetime(2024, 1, 2, tzinfo=UTC),
            "source_reference": "fixture:venue-funding-schedule",
            "source_hash": "0" * 64,
        }
        fields.update(overrides)
        return CryptoFundingConvention(**fields)  # type: ignore[arg-type]

    def test_announcement_before_effective_date_is_accepted(self) -> None:
        convention = self.build(
            effective_from=datetime(2025, 3, 10, tzinfo=UTC),
            known_at=datetime(2025, 3, 1, tzinfo=UTC),
        )
        self.assertLess(convention.known_at, convention.effective_from)

    def test_backfill_after_effective_date_is_accepted(self) -> None:
        convention = self.build(
            effective_from=datetime(2020, 3, 10, tzinfo=UTC),
            known_at=datetime(2026, 9, 7, tzinfo=UTC),
        )
        self.assertGreater(convention.known_at, convention.effective_from)

    def test_offset_at_or_beyond_the_interval_is_rejected(self) -> None:
        for offset in (Decimal(8), Decimal(9)):
            with self.assertRaises(CryptoFundingConventionError) as raised:
                self.build(first_funding_offset_hours=offset)
            self.assertIn("funding_offset_exceeds_interval", str(raised.exception))

    def test_non_positive_interval_is_rejected(self) -> None:
        for interval in (Decimal(0), Decimal(-8)):
            with self.assertRaises(CryptoFundingConventionError):
                self.build(interval_hours=interval)

    def test_floor_above_cap_is_rejected(self) -> None:
        with self.assertRaises(CryptoFundingConventionError) as raised:
            self.build(
                funding_rate_floor=Decimal("0.01"), funding_rate_cap=Decimal("-0.01")
            )
        self.assertIn("funding_rate_floor_exceeds_cap", str(raised.exception))

    def test_convention_stores_no_observed_rate(self) -> None:
        """NEXT-03 owns observations; this layer stores only the schedule."""
        fields = {field for field in CryptoFundingConvention.__dataclass_fields__}
        self.assertNotIn("funding_rate", fields)
        self.assertNotIn("mark_price", fields)
        self.assertNotIn("index_price", fields)
        self.assertNotIn("open_interest", fields)


class VenueTradingRuleTests(unittest.TestCase):
    """Invariant 6, plus the static-identity vs venue-revised-state boundary."""

    def build(self, **overrides: object) -> CryptoVenueTradingRules:
        fields: dict[str, object] = {
            "instrument_id": "TESTFIXTURE:CRY:BINANCE:BTCUSDT:SPOT",
            "rule_version": 1,
            "tick_size": Decimal("0.01"),
            "quantity_step": Decimal("0.00001"),
            "min_quantity": Decimal("0.00001"),
            "price_precision": 2,
            "quantity_precision": 5,
            "effective_from": datetime(2024, 1, 2, tzinfo=UTC),
            "known_at": datetime(2024, 1, 2, tzinfo=UTC),
            "source_reference": "fixture:venue-trading-rules",
            "source_hash": "0" * 64,
        }
        fields.update(overrides)
        return CryptoVenueTradingRules(**fields)  # type: ignore[arg-type]

    def test_zero_or_negative_tick_size_is_rejected(self) -> None:
        for tick in (Decimal(0), Decimal("-0.01")):
            with self.assertRaises(CryptoVenueRuleError) as raised:
                self.build(tick_size=tick)
            self.assertIn("invalid_venue_rule_units", str(raised.exception))

    def test_zero_or_negative_quantity_step_is_rejected(self) -> None:
        for step in (Decimal(0), Decimal("-1")):
            with self.assertRaises(CryptoVenueRuleError):
                self.build(quantity_step=step)

    def test_zero_or_negative_minimum_quantity_is_rejected(self) -> None:
        for minimum in (Decimal(0), Decimal("-1")):
            with self.assertRaises(CryptoVenueRuleError):
                self.build(min_quantity=minimum)

    def test_max_below_min_quantity_is_rejected(self) -> None:
        with self.assertRaises(CryptoVenueRuleError) as raised:
            self.build(min_quantity=Decimal(2), max_quantity=Decimal(1))
        self.assertIn("max_quantity_below_min_quantity", str(raised.exception))

    def test_non_positive_min_notional_is_rejected(self) -> None:
        with self.assertRaises(CryptoVenueRuleError) as raised:
            self.build(min_notional=Decimal(0))
        self.assertIn("invalid_min_notional", str(raised.exception))

    def test_out_of_range_precision_is_rejected(self) -> None:
        for precision in (-1, 19):
            with self.assertRaises(CryptoVenueRuleError):
                self.build(price_precision=precision)

    def test_naive_timestamps_are_rejected(self) -> None:
        with self.assertRaises(Exception) as raised:
            self.build(effective_from=datetime(2024, 1, 2))  # noqa: DTZ001
        self.assertIn("must_be_timezone_aware", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
