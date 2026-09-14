"""Unit evidence for source-backed Bybit BTCUSDT instrument onboarding.

Every case here runs against the FROZEN captured response committed in
``bybit_instrument_onboarding`` -- no network call is made, attempted or
mocked, and nothing in this file contacts Bybit. The single live
``instruments-info`` request that produced that snapshot was a one-off
development step; CI only ever reads the frozen copy.
"""

from __future__ import annotations

import copy
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from trade_platform import bybit_instrument_onboarding as onboarding
from trade_platform.bybit_instrument_onboarding import (
    BYBIT_AUTHORIZED_OBSERVATION_KINDS,
    BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
    CAPTURED_BTCUSDT_PAYLOAD_SHA256,
    CAPTURED_BTCUSDT_RETRIEVED_AT,
    BybitInstrumentMetadataError,
    BybitInstrumentOnboardingError,
    bybit_authorized_historical_source,
    bybit_btcusdt_crypto_specification,
    bybit_btcusdt_identifier_mapping,
    bybit_btcusdt_professional_instrument,
    bybit_btcusdt_symbol_mapping,
    bybit_btcusdt_venue_trading_rules,
    canonical_payload_hash,
    captured_btcusdt_envelope_v1,
    captured_btcusdt_snapshot_v1,
    parse_bybit_instrument_metadata,
)
from trade_platform.crypto_instruments import (
    CryptoInstrumentKind,
    CryptoSettlementType,
    ReferencePriceRequirement,
    SettlementStyle,
)
from trade_platform.domain import AssetClass
from trade_platform.historical_market_data import AssetScope, ObservationKind
from trade_platform.professional_instruments import (
    IdentifierSourceKind,
    InstrumentType,
    RepresentationKind,
    SessionType,
)

ONBOARDED_AT = datetime(2026, 9, 14, 22, 0, tzinfo=UTC)
LAUNCH_TIME = datetime(2020, 3, 15, tzinfo=UTC)


def _envelope() -> dict[str, object]:
    return copy.deepcopy(captured_btcusdt_envelope_v1())


def _payload(envelope: dict[str, object]) -> dict[str, object]:
    result = envelope["result"]
    assert isinstance(result, dict)
    rows = result["list"]
    assert isinstance(rows, list)
    instrument = rows[0]
    assert isinstance(instrument, dict)
    return instrument


def _with_field(key: str, value: object) -> dict[str, object]:
    envelope = _envelope()
    _payload(envelope)[key] = value
    return envelope


def _with_filter_field(filter_key: str, key: str, value: object) -> dict[str, object]:
    envelope = _envelope()
    nested = _payload(envelope)[filter_key]
    assert isinstance(nested, dict)
    nested[key] = value
    return envelope


def _parse(envelope: dict[str, object]) -> onboarding.BybitInstrumentMetadataSnapshotV1:
    return parse_bybit_instrument_metadata(
        envelope, retrieved_at=CAPTURED_BTCUSDT_RETRIEVED_AT
    )


class BybitInstrumentMetadataParsingTests(unittest.TestCase):
    def test_captured_snapshot_parses_to_the_exact_provider_values(self) -> None:
        snapshot = captured_btcusdt_snapshot_v1()

        self.assertEqual("BTCUSDT", snapshot.symbol)
        self.assertEqual("LinearPerpetual", snapshot.contract_type)
        self.assertEqual("Trading", snapshot.status)
        self.assertEqual("BTC", snapshot.base_coin)
        self.assertEqual("USDT", snapshot.quote_coin)
        self.assertEqual("USDT", snapshot.settle_coin)
        self.assertEqual(LAUNCH_TIME, snapshot.launch_time)
        self.assertEqual(0, snapshot.delivery_time)
        self.assertEqual(Decimal("0.10"), snapshot.tick_size)
        self.assertEqual(2, snapshot.price_scale)
        self.assertEqual(Decimal("0.001"), snapshot.qty_step)
        self.assertEqual(Decimal("0.001"), snapshot.min_order_qty)
        self.assertEqual(Decimal("5"), snapshot.min_notional_value)
        self.assertEqual(Decimal("1500.000"), snapshot.max_order_qty)
        self.assertEqual(Decimal("150.000"), snapshot.max_market_order_qty)
        self.assertEqual(480, snapshot.funding_interval_minutes)
        self.assertEqual(Decimal("-0.00333"), snapshot.lower_funding_rate)
        self.assertEqual(Decimal("0.00333"), snapshot.upper_funding_rate)
        self.assertEqual(CAPTURED_BTCUSDT_RETRIEVED_AT, snapshot.retrieved_at)
        self.assertEqual(
            datetime(2026, 9, 14, 21, 44, 23, 85000, tzinfo=UTC),
            snapshot.provider_response_time,
        )
        self.assertEqual(CAPTURED_BTCUSDT_PAYLOAD_SHA256, snapshot.canonical_payload_hash)

    def test_snapshot_is_immutable(self) -> None:
        snapshot = captured_btcusdt_snapshot_v1()
        # FrozenInstanceError subclasses AttributeError.
        with self.assertRaises(AttributeError):
            snapshot.tick_size = Decimal("0.01")  # type: ignore[misc]

    def test_source_reference_binds_the_record_to_its_exact_snapshot(self) -> None:
        snapshot = captured_btcusdt_snapshot_v1()
        self.assertEqual(
            "bybit:v5:instruments-info:linear:BTCUSDT:"
            f"sha256={CAPTURED_BTCUSDT_PAYLOAD_SHA256}",
            snapshot.source_reference,
        )

    def test_malformed_envelopes_fail_closed(self) -> None:
        cases: dict[str, dict[str, object]] = {}

        missing_ret_code = _envelope()
        del missing_ret_code["retCode"]
        cases["missing_retCode"] = missing_ret_code

        boolean_ret_code = _envelope()
        boolean_ret_code["retCode"] = True
        cases["boolean_retCode"] = boolean_ret_code

        missing_time = _envelope()
        del missing_time["time"]
        cases["missing_time"] = missing_time

        non_positive_time = _envelope()
        non_positive_time["time"] = 0
        cases["non_positive_time"] = non_positive_time

        non_dict_result = _envelope()
        non_dict_result["result"] = []
        cases["non_dict_result"] = non_dict_result

        missing_list = _envelope()
        result = missing_list["result"]
        assert isinstance(result, dict)
        del result["list"]
        cases["missing_list"] = missing_list

        non_dict_instrument = _envelope()
        result = non_dict_instrument["result"]
        assert isinstance(result, dict)
        result["list"] = ["BTCUSDT"]
        cases["non_dict_instrument"] = non_dict_instrument

        for name, envelope in cases.items():
            with self.subTest(case=name), self.assertRaises(BybitInstrumentMetadataError):
                _parse(envelope)

    def test_non_zero_return_code_fails_closed(self) -> None:
        envelope = _envelope()
        envelope["retCode"] = 10001
        envelope["retMsg"] = "params error"
        with self.assertRaises(BybitInstrumentMetadataError) as caught:
            _parse(envelope)
        self.assertIn("bybit_provider_error:10001", str(caught.exception))

    def test_list_length_other_than_one_fails_closed(self) -> None:
        for rows in ([], [dict(onboarding.CAPTURED_BTCUSDT_INSTRUMENT_PAYLOAD_V1)] * 2):
            with self.subTest(length=len(rows)):
                envelope = _envelope()
                result = envelope["result"]
                assert isinstance(result, dict)
                result["list"] = rows
                with self.assertRaises(BybitInstrumentMetadataError) as caught:
                    _parse(envelope)
                self.assertIn("bybit_unexpected_list_length", str(caught.exception))

    def test_wrong_category_fails_closed(self) -> None:
        envelope = _envelope()
        result = envelope["result"]
        assert isinstance(result, dict)
        result["category"] = "inverse"
        with self.assertRaises(BybitInstrumentMetadataError) as caught:
            _parse(envelope)
        self.assertIn("bybit_unexpected_category", str(caught.exception))

    def test_wrong_symbol_fails_closed(self) -> None:
        with self.assertRaises(BybitInstrumentMetadataError) as caught:
            _parse(_with_field("symbol", "ETHUSDT"))
        self.assertIn("bybit_unexpected_symbol", str(caught.exception))

    def test_non_trading_status_fails_closed(self) -> None:
        for status in ("PreLaunch", "Delivering", "Closed"):
            with self.subTest(status=status):
                with self.assertRaises(BybitInstrumentMetadataError) as caught:
                    _parse(_with_field("status", status))
                self.assertIn("bybit_unexpected_status", str(caught.exception))

    def test_non_linear_perpetual_contract_type_fails_closed(self) -> None:
        for contract_type in ("InversePerpetual", "LinearFutures", "InverseFutures"):
            with self.subTest(contract_type=contract_type):
                with self.assertRaises(BybitInstrumentMetadataError) as caught:
                    _parse(_with_field("contractType", contract_type))
                self.assertIn("bybit_unexpected_contract_type", str(caught.exception))

    def test_wrong_base_quote_or_settle_coin_fails_closed(self) -> None:
        cases = (
            ("baseCoin", "ETH", "base_coin"),
            ("quoteCoin", "USDC", "quote_coin"),
            ("settleCoin", "BTC", "settle_coin"),
        )
        for key, value, marker in cases:
            with self.subTest(field=key):
                with self.assertRaises(BybitInstrumentMetadataError) as caught:
                    _parse(_with_field(key, value))
                self.assertIn(f"bybit_unexpected_{marker}", str(caught.exception))

    def test_pre_listing_contract_fails_closed(self) -> None:
        with self.assertRaises(BybitInstrumentMetadataError) as caught:
            _parse(_with_field("isPreListing", True))
        self.assertIn("bybit_contract_is_pre_listing", str(caught.exception))

    def test_invalid_launch_time_fails_closed(self) -> None:
        for value, marker in (
            ("0", "bybit_invalid_launch_time"),
            ("", "bybit_missing_or_invalid_field"),
            ("-1584230400000", "bybit_invalid_launch_time"),
            ("not-a-number", "bybit_invalid_launch_time"),
        ):
            with self.subTest(launch_time=value):
                with self.assertRaises(BybitInstrumentMetadataError) as caught:
                    _parse(_with_field("launchTime", value))
                self.assertIn(marker, str(caught.exception))

    def test_non_zero_delivery_time_fails_closed(self) -> None:
        with self.assertRaises(BybitInstrumentMetadataError) as caught:
            _parse(_with_field("deliveryTime", "1735689600000"))
        self.assertIn("bybit_contract_has_delivery", str(caught.exception))

    def test_invalid_tick_step_quantity_and_notional_fail_closed(self) -> None:
        cases = (
            ("priceFilter", "tickSize", "0"),
            ("priceFilter", "tickSize", "-0.10"),
            ("priceFilter", "tickSize", "abc"),
            ("lotSizeFilter", "qtyStep", "0"),
            ("lotSizeFilter", "qtyStep", "-0.001"),
            ("lotSizeFilter", "minOrderQty", "0"),
            ("lotSizeFilter", "minOrderQty", "-1"),
            ("lotSizeFilter", "minNotionalValue", "0"),
            ("lotSizeFilter", "minNotionalValue", "-5"),
            ("lotSizeFilter", "maxOrderQty", "0"),
            ("lotSizeFilter", "maxMktOrderQty", "0"),
        )
        for filter_key, key, value in cases:
            with (
                self.subTest(field=key, value=value),
                self.assertRaises(BybitInstrumentMetadataError),
            ):
                _parse(_with_filter_field(filter_key, key, value))

    def test_missing_filter_objects_fail_closed(self) -> None:
        for key in ("priceFilter", "lotSizeFilter"):
            with self.subTest(filter=key), self.assertRaises(BybitInstrumentMetadataError):
                _parse(_with_field(key, None))

    def test_price_scale_disagreeing_with_tick_size_fails_closed(self) -> None:
        with self.assertRaises(BybitInstrumentMetadataError) as caught:
            _parse(_with_field("priceScale", "4"))
        self.assertIn("bybit_price_scale_disagrees_with_tick_size", str(caught.exception))

    def test_naive_retrieved_at_fails_closed(self) -> None:
        with self.assertRaises(BybitInstrumentMetadataError):
            parse_bybit_instrument_metadata(
                # Naive on purpose: this is the case under test.
                _envelope(),
                retrieved_at=datetime(2026, 9, 14, 21, 44),  # noqa: DTZ001
            )

    def test_expected_hash_mismatch_fails_closed(self) -> None:
        with self.assertRaises(BybitInstrumentMetadataError) as caught:
            parse_bybit_instrument_metadata(
                _envelope(),
                retrieved_at=CAPTURED_BTCUSDT_RETRIEVED_AT,
                expected_payload_hash="0" * 64,
            )
        self.assertIn("bybit_payload_hash_mismatch", str(caught.exception))


class BybitCanonicalHashTests(unittest.TestCase):
    def test_hash_is_deterministic_and_key_order_independent(self) -> None:
        payload = dict(onboarding.CAPTURED_BTCUSDT_INSTRUMENT_PAYLOAD_V1)
        shuffled = dict(reversed(list(payload.items())))

        self.assertNotEqual(list(payload), list(shuffled))
        self.assertEqual(canonical_payload_hash(payload), canonical_payload_hash(shuffled))
        self.assertEqual(CAPTURED_BTCUSDT_PAYLOAD_SHA256, canonical_payload_hash(payload))
        self.assertEqual(
            captured_btcusdt_snapshot_v1().canonical_payload_hash,
            captured_btcusdt_snapshot_v1().canonical_payload_hash,
        )

    def test_mutating_one_value_changes_the_hash(self) -> None:
        baseline = canonical_payload_hash(
            dict(onboarding.CAPTURED_BTCUSDT_INSTRUMENT_PAYLOAD_V1)
        )
        mutations: tuple[tuple[str, object], ...] = (
            ("status", "Closed"),
            ("fundingInterval", 240),
            ("launchTime", "1584230400001"),
            ("upperFundingRate", "0.00334"),
        )
        for key, value in mutations:
            with self.subTest(field=key):
                mutated = dict(onboarding.CAPTURED_BTCUSDT_INSTRUMENT_PAYLOAD_V1)
                mutated[key] = value
                self.assertNotEqual(baseline, canonical_payload_hash(mutated))

        nested = copy.deepcopy(
            dict(onboarding.CAPTURED_BTCUSDT_INSTRUMENT_PAYLOAD_V1)
        )
        price_filter = nested["priceFilter"]
        assert isinstance(price_filter, dict)
        price_filter["tickSize"] = "0.20"
        self.assertNotEqual(baseline, canonical_payload_hash(nested))

    def test_hash_rejects_non_canonical_payloads(self) -> None:
        with self.assertRaises(BybitInstrumentMetadataError):
            canonical_payload_hash({"value": float("nan")})


class BybitPrecisionDerivationTests(unittest.TestCase):
    def test_precisions_come_from_the_exact_decimal_step_representation(self) -> None:
        snapshot = captured_btcusdt_snapshot_v1()

        self.assertEqual(2, snapshot.price_precision)
        self.assertEqual(3, snapshot.quantity_precision)
        # "0.10" carries two decimal places even though it normalizes to 0.1 --
        # the provider's exact representation is what the venue quotes in.
        self.assertEqual(-2, snapshot.tick_size.as_tuple().exponent)
        self.assertEqual(snapshot.price_scale, snapshot.price_precision)

    def test_precision_derivation_never_uses_float_arithmetic(self) -> None:
        cases = (
            ("0.1", "0.1", 1, 1),
            ("0.10", "0.001", 2, 3),
            ("0.000001", "0.00000001", 6, 8),
            ("1", "1", 0, 0),
            ("10", "100", 0, 0),
        )
        for tick, step, price_precision, quantity_precision in cases:
            with self.subTest(tick=tick, step=step):
                envelope = _with_filter_field("priceFilter", "tickSize", tick)
                nested = _payload(envelope)["lotSizeFilter"]
                assert isinstance(nested, dict)
                nested["qtyStep"] = step
                _payload(envelope)["priceScale"] = str(price_precision)
                snapshot = _parse(envelope)
                self.assertEqual(price_precision, snapshot.price_precision)
                self.assertEqual(quantity_precision, snapshot.quantity_precision)


class BybitRecordBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = captured_btcusdt_snapshot_v1()

    def test_professional_instrument_carries_the_canonical_identity(self) -> None:
        instrument = bybit_btcusdt_professional_instrument(self.snapshot, ONBOARDED_AT)

        self.assertEqual(BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID, instrument.instrument_id)
        self.assertEqual("CRYPTO:BYBIT:BTCUSDT:PERP", instrument.instrument_id)
        self.assertIs(AssetClass.CRYPTO, instrument.asset_class)
        self.assertIs(InstrumentType.CRYPTO_PERPETUAL, instrument.instrument_type)
        self.assertEqual("Bybit", instrument.exchange_name)
        self.assertEqual("BYBIT", instrument.venue)
        self.assertIsNone(instrument.mic)
        self.assertEqual("BTCUSDT", instrument.canonical_symbol)
        self.assertIs(RepresentationKind.PERPETUAL, instrument.representation_kind)
        self.assertIs(SessionType.CRYPTO_24X7, instrument.market_session_type)
        self.assertEqual("UTC", instrument.trading_timezone)
        self.assertIsNone(instrument.expiration_date)

    def test_contract_units_are_base_coin_quantity_with_no_inverse_semantics(
        self,
    ) -> None:
        instrument = bybit_btcusdt_professional_instrument(self.snapshot, ONBOARDED_AT)
        specification = bybit_btcusdt_crypto_specification(self.snapshot, ONBOARDED_AT)

        self.assertEqual(Decimal(1), instrument.contract_multiplier)
        self.assertEqual(Decimal(1), instrument.contract_size)
        self.assertEqual(Decimal(1), specification.contract_multiplier)
        self.assertEqual(Decimal(1), specification.contract_size)
        self.assertIs(SettlementStyle.LINEAR, specification.settlement_style)
        self.assertEqual("BTC", specification.base_asset)
        self.assertEqual("USDT", specification.quote_asset)
        self.assertEqual("USDT", specification.settlement_asset)

    def test_listing_date_comes_from_launch_time_not_onboarding_time(self) -> None:
        instrument = bybit_btcusdt_professional_instrument(self.snapshot, ONBOARDED_AT)

        self.assertEqual(date(2020, 3, 15), instrument.listing_date)
        self.assertNotEqual(ONBOARDED_AT.date(), instrument.listing_date)

    def test_knowledge_clocks_are_the_real_onboarding_time(self) -> None:
        instrument = bybit_btcusdt_professional_instrument(self.snapshot, ONBOARDED_AT)
        symbol = bybit_btcusdt_symbol_mapping(self.snapshot, ONBOARDED_AT)
        identifier = bybit_btcusdt_identifier_mapping(self.snapshot, ONBOARDED_AT)
        specification = bybit_btcusdt_crypto_specification(self.snapshot, ONBOARDED_AT)
        rules = bybit_btcusdt_venue_trading_rules(self.snapshot, ONBOARDED_AT)
        source = bybit_authorized_historical_source(self.snapshot, ONBOARDED_AT)

        for value in (
            instrument.registered_at,
            symbol.ingested_at,
            identifier.ingested_at,
            specification.registered_at,
            rules.known_at,
            source.authorized_at,
            source.created_at,
        ):
            self.assertEqual(ONBOARDED_AT, value)

        # Real-world validity clocks stay on the provider's own facts.
        self.assertEqual(LAUNCH_TIME, symbol.valid_from)
        self.assertEqual(LAUNCH_TIME, identifier.valid_from)
        self.assertEqual(self.snapshot.retrieved_at, rules.effective_from)
        self.assertNotEqual(rules.effective_from, rules.known_at)

    def test_onboarding_cannot_precede_its_own_evidence(self) -> None:
        with self.assertRaises(BybitInstrumentOnboardingError) as caught:
            bybit_btcusdt_professional_instrument(
                self.snapshot, self.snapshot.retrieved_at.replace(year=2020)
            )
        self.assertIn("onboarded_before_metadata_was_retrieved", str(caught.exception))

    def test_naive_onboarding_time_fails_closed(self) -> None:
        with self.assertRaises(BybitInstrumentOnboardingError):
            bybit_btcusdt_professional_instrument(
                # Naive on purpose: this is the case under test.
                self.snapshot,
                datetime(2026, 9, 14, 22, 0),  # noqa: DTZ001
            )

    def test_mappings_use_the_provider_namespace_and_stay_open_ended(self) -> None:
        symbol = bybit_btcusdt_symbol_mapping(self.snapshot, ONBOARDED_AT)
        identifier = bybit_btcusdt_identifier_mapping(self.snapshot, ONBOARDED_AT)

        self.assertEqual("BYBIT", symbol.venue)
        self.assertEqual("BTCUSDT", symbol.symbol)
        self.assertIsNone(symbol.valid_until)
        self.assertIs(IdentifierSourceKind.PROVIDER, identifier.source_kind)
        self.assertEqual("bybit_v5_symbol", identifier.namespace)
        self.assertEqual("BTCUSDT", identifier.value)
        self.assertIsNone(identifier.valid_until)

    def test_every_record_references_the_exact_snapshot_evidence(self) -> None:
        reference = self.snapshot.source_reference
        rules = bybit_btcusdt_venue_trading_rules(self.snapshot, ONBOARDED_AT)

        self.assertEqual(
            reference,
            bybit_btcusdt_symbol_mapping(self.snapshot, ONBOARDED_AT).source_reference,
        )
        self.assertEqual(
            reference,
            bybit_btcusdt_identifier_mapping(
                self.snapshot, ONBOARDED_AT
            ).source_reference,
        )
        self.assertEqual(
            reference,
            bybit_btcusdt_crypto_specification(
                self.snapshot, ONBOARDED_AT
            ).source_reference,
        )
        self.assertEqual(reference, rules.source_reference)
        self.assertEqual(self.snapshot.canonical_payload_hash, rules.source_hash)
        self.assertIn(
            CAPTURED_BTCUSDT_PAYLOAD_SHA256,
            bybit_authorized_historical_source(
                self.snapshot, ONBOARDED_AT
            ).authorization_reference,
        )

    def test_specification_records_perpetual_semantics_without_inventing_an_index(
        self,
    ) -> None:
        specification = bybit_btcusdt_crypto_specification(self.snapshot, ONBOARDED_AT)

        self.assertIs(CryptoInstrumentKind.PERPETUAL, specification.kind)
        self.assertIs(CryptoSettlementType.CASH_SETTLED, specification.settlement_type)
        self.assertIs(
            ReferencePriceRequirement.MARK_AND_INDEX,
            specification.reference_price_requirement,
        )
        self.assertIsNone(specification.expiry_at)
        self.assertIsNone(specification.index_reference)
        self.assertTrue(specification.requires_funding)


class BybitVenueTradingRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = captured_btcusdt_snapshot_v1()
        self.rules = bybit_btcusdt_venue_trading_rules(self.snapshot, ONBOARDED_AT)

    def test_rules_map_the_provider_filters_exactly(self) -> None:
        self.assertEqual(1, self.rules.rule_version)
        self.assertEqual(Decimal("0.10"), self.rules.tick_size)
        self.assertEqual(Decimal("0.001"), self.rules.quantity_step)
        self.assertEqual(Decimal("0.001"), self.rules.min_quantity)
        self.assertEqual(Decimal("5"), self.rules.min_notional)
        self.assertEqual(2, self.rules.price_precision)
        self.assertEqual(3, self.rules.quantity_precision)

    def test_the_two_provider_maxima_are_retained_but_never_collapsed(self) -> None:
        # Bybit states a limit-order maximum and a market-order maximum. The
        # single max_quantity field cannot express both, so it stays unset
        # rather than silently asserting a limit Bybit never gave.
        self.assertIsNone(self.rules.max_quantity)
        self.assertEqual(Decimal("1500.000"), self.snapshot.max_order_qty)
        self.assertEqual(Decimal("150.000"), self.snapshot.max_market_order_qty)
        self.assertNotEqual(self.snapshot.max_order_qty, self.snapshot.max_market_order_qty)


class BybitFundingScopeTests(unittest.TestCase):
    def test_funding_fields_are_preserved_only_inside_the_snapshot(self) -> None:
        snapshot = captured_btcusdt_snapshot_v1()

        self.assertEqual(480, snapshot.funding_interval_minutes)
        self.assertEqual(Decimal("-0.00333"), snapshot.lower_funding_rate)
        self.assertEqual(Decimal("0.00333"), snapshot.upper_funding_rate)

    def test_this_phase_builds_no_funding_convention(self) -> None:
        # instruments-info states no first-funding offset, so registering a
        # convention would have to invent one. Nothing here produces one.
        exported = {
            name
            for name in dir(onboarding)
            if not name.startswith("_")
        }
        self.assertNotIn("CryptoFundingConvention", exported)
        self.assertFalse({name for name in exported if "funding_convention" in name})

    def test_source_authorizes_exactly_the_four_approved_kinds(self) -> None:
        source = bybit_authorized_historical_source(
            captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )
        source.validate()

        self.assertEqual(
            frozenset(
                {
                    ObservationKind.OHLCV,
                    ObservationKind.MARK_PRICE,
                    ObservationKind.INDEX_PRICE,
                    ObservationKind.OPEN_INTEREST,
                }
            ),
            source.resolved_capabilities(),
        )
        self.assertEqual(BYBIT_AUTHORIZED_OBSERVATION_KINDS, source.resolved_capabilities())

    def test_source_has_no_funding_capability(self) -> None:
        source = bybit_authorized_historical_source(
            captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )
        capabilities = source.resolved_capabilities()

        self.assertNotIn(ObservationKind.FUNDING_RATE_REALIZED, capabilities)
        self.assertNotIn(ObservationKind.FUNDING_RATE_INDICATIVE, capabilities)

    def test_source_identity_matches_the_bybit_adapter_namespace(self) -> None:
        source = bybit_authorized_historical_source(
            captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )

        self.assertEqual("bybit", source.provider)
        self.assertEqual("bybit_v5_symbol", source.provider_identifier_namespace)
        self.assertEqual(AssetScope.CRYPTO, source.scope())
        self.assertIn(
            "operator-approved public Bybit V5 market-data pilot authority",
            source.authorization_reference,
        )


if __name__ == "__main__":
    unittest.main()
