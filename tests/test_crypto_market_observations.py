"""Pure unit evidence for Module 3I.2 crypto funding / mark / index payloads.

No database and no network. Every rate, price, quantity, venue and identifier is
a FIXTURE. Nothing here was retrieved from or verified against any crypto venue
or data provider, and no real funding rate, mark price, index price,
open-interest figure or contract specification is claimed.
"""

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from trade_platform.crypto_instruments import (
    CryptoFundingConvention,
    CryptoInstrumentKind,
    CryptoInstrumentSpecification,
    CryptoSettlementType,
    ReferencePriceRequirement,
    SettlementStyle,
)
from trade_platform.crypto_market_observations import (
    FUNDING_PAYLOAD_TABLE,
    REFERENCE_PRICE_PAYLOAD_TABLE,
    FundingObservationKind,
    ReferencePriceKind,
    expected_funding_instants,
    funding_instant_matches_convention,
    parse_funding_payload,
    parse_reference_price_payload,
    validate_crypto_funding,
    validate_crypto_open_interest,
    validate_crypto_reference_price,
)
from trade_platform.data_health import (
    CRYPTO_SERIES_DATA_HEALTH_CHECKS,
    CryptoSeriesObservation,
    DataHealthCheck,
    detect_crypto_series_health,
)
from trade_platform.market_observation_payloads import (
    CRYPTO_SUPPORTED_OPEN_INTEREST_UNITS,
    parse_open_interest_payload,
)

VENUE = "TESTFIXVENUE"
PERPETUAL_ID = "TESTFIXTURE:CRYPTO:PERP:BTCUSDT"
REGISTERED_AT = datetime(2025, 7, 1, tzinfo=UTC)
#: 08:00 UTC lies on an eight-hourly, zero-offset schedule anchored at the epoch.
FUNDING_AT = datetime(2025, 7, 10, 8, 0, tzinfo=UTC)
REALIZED = FundingObservationKind.REALIZED
INDICATIVE = FundingObservationKind.INDICATIVE


def specification(**overrides: object) -> CryptoInstrumentSpecification:
    fields: dict[str, object] = {
        "instrument_id": PERPETUAL_ID,
        "venue": VENUE,
        "kind": CryptoInstrumentKind.PERPETUAL,
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "settlement_asset": "USDT",
        "settlement_style": SettlementStyle.LINEAR,
        "settlement_type": CryptoSettlementType.CASH_SETTLED,
        "contract_multiplier": Decimal(1),
        "contract_size": Decimal(1),
        "reference_price_requirement": ReferencePriceRequirement.MARK_AND_INDEX,
        "index_reference": "TESTFIX_BTCUSDT_INDEX",
        "registered_at": REGISTERED_AT,
        "source_reference": "fixture:crypto-specification",
    }
    fields.update(overrides)
    return CryptoInstrumentSpecification(**fields)  # type: ignore[arg-type]


def spot_specification() -> CryptoInstrumentSpecification:
    return specification(
        instrument_id="TESTFIXTURE:CRYPTO:SPOT:BTCUSDT",
        kind=CryptoInstrumentKind.SPOT,
        settlement_asset=None,
        settlement_style=None,
        settlement_type=CryptoSettlementType.PHYSICAL_DELIVERY,
        reference_price_requirement=ReferencePriceRequirement.NONE,
        index_reference=None,
    )


def dated_future_specification() -> CryptoInstrumentSpecification:
    return specification(
        instrument_id="TESTFIXTURE:CRYPTO:FUT:BTCUSDT",
        kind=CryptoInstrumentKind.DATED_FUTURE,
        expiry_at=datetime(2025, 9, 26, 8, tzinfo=UTC),
        reference_price_requirement=ReferencePriceRequirement.INDEX_ONLY,
    )


def convention(**overrides: object) -> CryptoFundingConvention:
    fields: dict[str, object] = {
        "instrument_id": PERPETUAL_ID,
        "convention_version": 1,
        "interval_hours": Decimal(8),
        "first_funding_offset_hours": Decimal(0),
        "funding_settlement_asset": "USDT",
        "funding_rate_floor": Decimal("-0.0075"),
        "funding_rate_cap": Decimal("0.0075"),
        "effective_from": REGISTERED_AT,
        "known_at": REGISTERED_AT,
        "source_reference": "fixture:funding-convention",
        "source_hash": "f" * 64,
    }
    fields.update(overrides)
    return CryptoFundingConvention(**fields)  # type: ignore[arg-type]


def funding_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "funding_rate": "0.0001",
        "target_funding_at": "2025-07-10T08:00:00+00:00",
        "published_at": "2025-07-10T08:00:00+00:00",
        "settlement_asset": "USDT",
    }
    payload.update(overrides)
    return payload


def reference_price_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "price": "58234.50",
        "price_asset": "USDT",
        "observed_at": "2025-07-10T08:00:00+00:00",
        "methodology_reference": "fixture://methodology/mark-v1",
    }
    payload.update(overrides)
    return payload


class FundingPayloadTests(unittest.TestCase):
    def test_valid_payload_parses_without_issues(self) -> None:
        parsed, issues = parse_funding_payload(funding_payload())
        self.assertEqual(issues, ())
        assert parsed is not None
        self.assertEqual(parsed.funding_rate, Decimal("0.0001"))
        self.assertEqual(parsed.target_funding_at, FUNDING_AT)
        self.assertEqual(parsed.settlement_asset, "USDT")
        # The convention binding is the pipeline's job, never the provider's.
        self.assertIsNone(parsed.convention_id)
        self.assertIsNone(parsed.convention_version)

    def test_negative_funding_rate_is_valid_data(self) -> None:
        """Shorts paying longs is a real market state, not a data error."""
        parsed, issues = parse_funding_payload(funding_payload(funding_rate="-0.0003"))
        self.assertEqual(issues, ())
        assert parsed is not None
        self.assertEqual(parsed.funding_rate, Decimal("-0.0003"))

    def test_missing_and_naive_fields_are_reported_not_guessed(self) -> None:
        parsed, issues = parse_funding_payload(funding_payload(funding_rate=None))
        self.assertIsNone(parsed)
        self.assertIn("missing_funding_rate", issues)
        parsed, issues = parse_funding_payload(
            funding_payload(target_funding_at="2025-07-10T08:00:00")
        )
        self.assertIsNone(parsed)
        self.assertIn("naive_target_funding_at", issues)
        parsed, issues = parse_funding_payload(funding_payload(settlement_asset=""))
        self.assertIsNone(parsed)
        self.assertIn("missing_settlement_asset", issues)

    def test_canonical_tuple_changes_with_every_financial_field(self) -> None:
        """Invariant 27: a real payload change must change dataset identity."""
        baseline, _ = parse_funding_payload(funding_payload())
        assert baseline is not None
        for change in (
            {"funding_rate": "0.00011"},
            {"target_funding_at": "2025-07-10T16:00:00+00:00"},
            {"published_at": "2025-07-10T08:00:01+00:00"},
            {"settlement_asset": "USDC"},
        ):
            altered, _ = parse_funding_payload(funding_payload(**change))
            assert altered is not None
            self.assertNotEqual(baseline.canonical_tuple(), altered.canonical_tuple(), change)
        # ...and so must the convention the rate was validated against.
        bound = baseline.bound_to(convention())
        self.assertNotEqual(baseline.canonical_tuple(), bound.canonical_tuple())
        self.assertNotEqual(
            bound.canonical_tuple(),
            baseline.bound_to(convention(convention_version=2)).canonical_tuple(),
        )
        self.assertEqual(baseline.canonical_tuple()[0], FUNDING_PAYLOAD_TABLE)

    def test_projection_agrees_with_canonical_values(self) -> None:
        parsed, _ = parse_funding_payload(funding_payload())
        assert parsed is not None
        bound = parsed.bound_to(convention())
        projection = bound.as_normalized_projection()
        self.assertEqual(projection["funding_rate"], str(bound.funding_rate))
        self.assertEqual(projection["convention_version"], 1)
        # Nothing in the projection says REALIZED or INDICATIVE: that is the
        # envelope's kind alone, and a second copy could disagree with it.
        self.assertNotIn("observation_kind", projection)
        self.assertNotIn("realized", projection)


class FundingConventionCoherenceTests(unittest.TestCase):
    def test_on_schedule_instant_matches(self) -> None:
        for hour in (0, 8, 16):
            self.assertIs(
                funding_instant_matches_convention(
                    datetime(2025, 7, 10, hour, tzinfo=UTC), convention()
                ),
                True,
            )

    def test_off_schedule_instant_does_not_match(self) -> None:
        """Invariant 6: a target instant the schedule never produces is refused."""
        self.assertIs(
            funding_instant_matches_convention(
                datetime(2025, 7, 10, 9, tzinfo=UTC), convention()
            ),
            False,
        )

    def test_offset_shifts_the_whole_schedule(self) -> None:
        shifted = convention(interval_hours=Decimal(4), first_funding_offset_hours=Decimal(1))
        self.assertIs(
            funding_instant_matches_convention(datetime(2025, 7, 10, 9, tzinfo=UTC), shifted),
            True,
        )
        self.assertIs(
            funding_instant_matches_convention(datetime(2025, 7, 10, 8, tzinfo=UTC), shifted),
            False,
        )

    def test_non_integral_schedule_reports_unknown_rather_than_guessing(self) -> None:
        odd = convention(interval_hours=Decimal("0.000001"))
        self.assertIsNone(funding_instant_matches_convention(FUNDING_AT, odd))

    def test_expected_instants_come_only_from_the_convention(self) -> None:
        instants = expected_funding_instants(
            convention(),
            start=datetime(2025, 7, 10, tzinfo=UTC),
            end=datetime(2025, 7, 10, 23, 59, tzinfo=UTC),
        )
        self.assertEqual(
            instants,
            (
                datetime(2025, 7, 10, 0, tzinfo=UTC),
                datetime(2025, 7, 10, 8, tzinfo=UTC),
                datetime(2025, 7, 10, 16, tzinfo=UTC),
            ),
        )

    def test_no_schedule_is_enumerated_when_it_is_not_deterministic(self) -> None:
        self.assertEqual(
            expected_funding_instants(
                convention(interval_hours=Decimal("0.000001")),
                start=datetime(2025, 7, 10, tzinfo=UTC),
                end=datetime(2025, 7, 10, 1, tzinfo=UTC),
            ),
            (),
        )


class FundingValidationTests(unittest.TestCase):
    def validate(self, kind: FundingObservationKind, **overrides: object) -> tuple[str, ...]:
        specification_override = overrides.pop("specification", specification())
        convention_override = overrides.pop("convention", convention())
        event_at = overrides.pop("event_at", FUNDING_AT)
        ingested_at = overrides.pop("ingested_at", FUNDING_AT + timedelta(minutes=5))
        parsed, issues = parse_funding_payload(funding_payload(**overrides))
        self.assertEqual(issues, ())
        assert parsed is not None
        return validate_crypto_funding(
            parsed, kind=kind,
            specification=specification_override,  # type: ignore[arg-type]
            convention=convention_override,  # type: ignore[arg-type]
            event_at=event_at,  # type: ignore[arg-type]
            ingested_at=ingested_at,  # type: ignore[arg-type]
        )

    def test_realized_funding_on_a_perpetual_is_accepted(self) -> None:
        self.assertEqual(self.validate(REALIZED), ())

    def test_indicative_funding_about_a_future_instant_is_accepted(self) -> None:
        published = datetime(2025, 7, 10, 4, tzinfo=UTC)
        self.assertEqual(
            self.validate(
                INDICATIVE, published_at=published.isoformat(), event_at=published,
                ingested_at=published + timedelta(minutes=1),
            ),
            (),
        )

    def test_funding_on_spot_is_rejected(self) -> None:
        """Invariants 1 and 2: neither funding kind may describe a spot pair."""
        for kind in (REALIZED, INDICATIVE):
            issues = self.validate(
                kind, specification=spot_specification(),
                published_at="2025-07-10T04:00:00+00:00",
                event_at=datetime(2025, 7, 10, 4, tzinfo=UTC)
                if kind is INDICATIVE else FUNDING_AT,
            )
            self.assertTrue(any(item.startswith("funding_requires_perpetual") for item in issues))

    def test_funding_on_dated_future_is_rejected_by_default(self) -> None:
        """Invariant 3: a dated future settles at expiry; funding is not implied."""
        issues = self.validate(REALIZED, specification=dated_future_specification())
        self.assertTrue(any(item.startswith("funding_requires_perpetual") for item in issues))

    def test_settlement_asset_mismatch_is_rejected(self) -> None:
        """Invariant 4, against both the convention and the instrument."""
        issues = self.validate(REALIZED, settlement_asset="USDC")
        self.assertIn("funding_settlement_asset_differs_from_convention", issues)
        self.assertIn("funding_settlement_asset_differs_from_instrument", issues)

    def test_target_instant_incompatible_with_the_visible_convention_is_rejected(self) -> None:
        """Invariant 6."""
        off_schedule = datetime(2025, 7, 10, 9, tzinfo=UTC)
        issues = self.validate(
            REALIZED, target_funding_at=off_schedule.isoformat(),
            published_at=off_schedule.isoformat(), event_at=off_schedule,
            ingested_at=off_schedule + timedelta(minutes=1),
        )
        self.assertIn("funding_target_incompatible_with_convention_schedule", issues)

    def test_rate_outside_the_explicit_cap_or_floor_is_rejected(self) -> None:
        """Invariant 7: bounds are only ever the convention's own declared ones."""
        self.assertIn(
            "funding_rate_above_convention_cap", self.validate(REALIZED, funding_rate="0.02")
        )
        self.assertIn(
            "funding_rate_below_convention_floor", self.validate(REALIZED, funding_rate="-0.02")
        )
        # A convention that declares no bound imposes none; no band is invented.
        unbounded = convention(funding_rate_floor=None, funding_rate_cap=None)
        self.assertEqual(
            self.validate(REALIZED, funding_rate="0.02", convention=unbounded), ()
        )

    def test_indicative_record_cannot_masquerade_as_realized(self) -> None:
        """Invariant 8: an estimate about a future instant fails realized semantics."""
        published = datetime(2025, 7, 10, 4, tzinfo=UTC)
        issues = self.validate(
            REALIZED, published_at=published.isoformat(), event_at=published,
            ingested_at=published + timedelta(minutes=1),
        )
        self.assertIn("realized_funding_target_must_equal_event_instant", issues)

    def test_realized_record_cannot_masquerade_as_indicative(self) -> None:
        """Invariant 9: a settled instant is not in the future of its publication."""
        issues = self.validate(INDICATIVE)
        self.assertIn("indicative_funding_target_must_be_in_the_future", issues)

    def test_publication_after_ingestion_is_rejected(self) -> None:
        issues = self.validate(
            REALIZED, published_at="2025-07-10T09:00:00+00:00",
            ingested_at=FUNDING_AT + timedelta(minutes=5),
        )
        self.assertIn("funding_published_after_ingestion", issues)


class ReferencePriceTests(unittest.TestCase):
    def validate(self, kind: ReferencePriceKind, **overrides: object) -> tuple[str, ...]:
        specification_override = overrides.pop("specification", specification())
        event_at = overrides.pop("event_at", FUNDING_AT)
        parsed, issues = parse_reference_price_payload(reference_price_payload(**overrides))
        self.assertEqual(issues, ())
        assert parsed is not None
        return validate_crypto_reference_price(
            parsed, kind=kind,
            specification=specification_override,  # type: ignore[arg-type]
            event_at=event_at,  # type: ignore[arg-type]
        )

    def test_non_positive_price_is_rejected(self) -> None:
        """Invariant 12."""
        for price in ("0", "-1"):
            parsed, issues = parse_reference_price_payload(reference_price_payload(price=price))
            self.assertIsNone(parsed)
            self.assertIn("non_positive_reference_price", issues)

    def test_mark_and_index_are_both_permitted_when_the_contract_requires_both(self) -> None:
        self.assertEqual(self.validate(ReferencePriceKind.MARK), ())
        self.assertEqual(self.validate(ReferencePriceKind.INDEX), ())

    def test_mark_on_a_contract_that_does_not_permit_mark_is_rejected(self) -> None:
        """Invariant 10: INDEX_ONLY semantics do not imply a mark price."""
        issues = self.validate(
            ReferencePriceKind.MARK, specification=dated_future_specification()
        )
        self.assertTrue(
            any(item.startswith("reference_price_not_permitted_by_instrument") for item in issues)
        )

    def test_spot_fails_closed_for_both_reference_prices(self) -> None:
        """Invariant 11: ReferencePriceRequirement.NONE permits neither."""
        for kind in (ReferencePriceKind.MARK, ReferencePriceKind.INDEX):
            issues = self.validate(kind, specification=spot_specification())
            self.assertTrue(
                any(
                    item.startswith("reference_price_not_permitted_by_instrument")
                    for item in issues
                )
            )

    def test_price_asset_must_be_the_contracts_quote_asset(self) -> None:
        issues = self.validate(ReferencePriceKind.MARK, price_asset="BTC")
        self.assertIn("reference_price_asset_differs_from_quote_asset", issues)

    def test_methodology_reference_is_provenance_only(self) -> None:
        """Free text never becomes an authoritative financial value."""
        parsed, _ = parse_reference_price_payload(
            reference_price_payload(methodology_reference="anything at all")
        )
        assert parsed is not None
        self.assertEqual(parsed.methodology_reference, "anything at all")
        # It cannot stand in for a price, and it does not widen what the
        # instrument permits.
        issues = validate_crypto_reference_price(
            parsed, kind=ReferencePriceKind.MARK,
            specification=spot_specification(), event_at=FUNDING_AT,
        )
        self.assertTrue(issues)

    def test_canonical_tuple_changes_with_every_field(self) -> None:
        baseline, _ = parse_reference_price_payload(reference_price_payload())
        assert baseline is not None
        self.assertEqual(baseline.canonical_tuple()[0], REFERENCE_PRICE_PAYLOAD_TABLE)
        for change in (
            {"price": "58234.51"},
            {"price_asset": "USDC"},
            {"observed_at": "2025-07-10T08:00:01+00:00"},
            {"methodology_reference": "fixture://methodology/mark-v2"},
        ):
            altered, _ = parse_reference_price_payload(reference_price_payload(**change))
            assert altered is not None
            self.assertNotEqual(baseline.canonical_tuple(), altered.canonical_tuple(), change)


class CryptoOpenInterestTests(unittest.TestCase):
    def payload(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "open_interest": "1250.5", "unit": "BASE_ASSET", "unit_asset": "BTC",
            "observed_at": "2025-07-10T08:00:00+00:00",
        }
        values.update(overrides)
        return values

    def validate(self, spec: CryptoInstrumentSpecification, **overrides: object) -> tuple[str, ...]:
        parsed, issues = parse_open_interest_payload(
            self.payload(**overrides), supported_units=CRYPTO_SUPPORTED_OPEN_INTEREST_UNITS
        )
        self.assertEqual(issues, ())
        assert parsed is not None
        return validate_crypto_open_interest(parsed, specification=spec)

    def test_all_three_units_are_available_to_a_crypto_derivative(self) -> None:
        self.assertEqual(self.validate(specification()), ())
        self.assertEqual(
            self.validate(specification(), unit="QUOTE_NOTIONAL", unit_asset="USDT"), ()
        )
        self.assertEqual(
            self.validate(specification(), unit="CONTRACTS", unit_asset=""), ()
        )

    def test_negative_open_interest_is_rejected(self) -> None:
        """Invariant 13."""
        parsed, issues = parse_open_interest_payload(
            self.payload(open_interest="-1"),
            supported_units=CRYPTO_SUPPORTED_OPEN_INTEREST_UNITS,
        )
        self.assertIsNone(parsed)
        self.assertIn("negative_open_interest", issues)

    def test_base_asset_unit_must_name_the_contracts_base_asset(self) -> None:
        """Invariant 14."""
        self.assertIn(
            "base_asset_open_interest_unit_asset_mismatch",
            self.validate(specification(), unit_asset="ETH"),
        )

    def test_quote_notional_unit_must_name_the_contracts_quote_asset(self) -> None:
        """Invariant 15."""
        self.assertIn(
            "quote_notional_open_interest_unit_asset_mismatch",
            self.validate(specification(), unit="QUOTE_NOTIONAL", unit_asset="BTC"),
        )

    def test_spot_open_interest_fails_closed(self) -> None:
        issues = self.validate(spot_specification())
        self.assertTrue(
            any(item.startswith("open_interest_requires_derivative") for item in issues)
        )

    def test_no_unit_conversion_exists_anywhere(self) -> None:
        """Invariant 16: the same number in two units is two measurements."""
        base, _ = parse_open_interest_payload(
            self.payload(open_interest="10"),
            supported_units=CRYPTO_SUPPORTED_OPEN_INTEREST_UNITS,
        )
        notional, _ = parse_open_interest_payload(
            self.payload(open_interest="10", unit="QUOTE_NOTIONAL", unit_asset="USDT"),
            supported_units=CRYPTO_SUPPORTED_OPEN_INTEREST_UNITS,
        )
        assert base is not None and notional is not None
        self.assertEqual(base.open_interest, notional.open_interest)
        self.assertNotEqual(base.canonical_tuple(), notional.canonical_tuple())


class CryptoSeriesDataHealthTests(unittest.TestCase):
    def observation(self, **overrides: object) -> CryptoSeriesObservation:
        fields: dict[str, object] = {
            "source_id": UUID(int=2),
            "instrument_id": PERPETUAL_ID,
            "observation_kind": "FUNDING_RATE_REALIZED",
            "event_at": FUNDING_AT,
            "ingested_at": FUNDING_AT,
            "revision": 0,
            "funding_rate": Decimal("0.0001"),
            "target_funding_at": FUNDING_AT,
        }
        fields.update(overrides)
        return CryptoSeriesObservation(**fields)  # type: ignore[arg-type]

    def test_clean_series_reports_nothing(self) -> None:
        self.assertEqual(detect_crypto_series_health([self.observation()]), ())

    def test_realized_completeness_is_evaluated_only_against_the_convention(self) -> None:
        expected = expected_funding_instants(
            convention(),
            start=datetime(2025, 7, 10, tzinfo=UTC),
            end=datetime(2025, 7, 10, 23, tzinfo=UTC),
        )
        findings = detect_crypto_series_health(
            [self.observation()], expected_funding_instants=expected
        )
        missing = [
            item for item in findings
            if item.check_type is DataHealthCheck.MISSING_EXPECTED_FUNDING_EVENTS
        ]
        self.assertEqual(len(missing), 1)
        self.assertEqual(
            missing[0].detail["missing_funding_instants"],
            ["2025-07-10T00:00:00+00:00", "2025-07-10T16:00:00+00:00"],
        )

    def test_no_cadence_is_invented_without_a_convention(self) -> None:
        sparse = [
            self.observation(event_at=datetime(2025, 7, 2, 8, tzinfo=UTC),
                             target_funding_at=datetime(2025, 7, 2, 8, tzinfo=UTC)),
            self.observation(),
        ]
        raised = {item.check_type for item in detect_crypto_series_health(sparse)}
        self.assertNotIn(DataHealthCheck.MISSING_EXPECTED_FUNDING_EVENTS, raised)

    def test_indicative_estimates_never_satisfy_realized_completeness(self) -> None:
        """An estimate is not evidence that a funding event settled."""
        estimate = self.observation(
            observation_kind="FUNDING_RATE_INDICATIVE",
            event_at=datetime(2025, 7, 10, 4, tzinfo=UTC),
        )
        findings = detect_crypto_series_health(
            [estimate], expected_funding_instants=(FUNDING_AT,)
        )
        missing = [
            item for item in findings
            if item.check_type is DataHealthCheck.MISSING_EXPECTED_FUNDING_EVENTS
        ]
        self.assertEqual(len(missing), 1)
        self.assertEqual(
            missing[0].detail["missing_funding_instants"], [FUNDING_AT.isoformat()]
        )

    def test_repeated_indicative_estimates_are_never_a_finding(self) -> None:
        """A venue may revise an estimate any number of times; no count is assumed."""
        estimates = [
            self.observation(
                observation_kind="FUNDING_RATE_INDICATIVE",
                event_at=datetime(2025, 7, 10, hour, tzinfo=UTC),
                funding_rate=Decimal("0.00012"),
            )
            for hour in (1, 2, 3, 4, 5, 6, 7)
        ]
        self.assertEqual(detect_crypto_series_health(estimates), ())

    def test_no_mark_or_index_cadence_is_assumed(self) -> None:
        marks = [
            self.observation(
                observation_kind="MARK_PRICE", funding_rate=None, target_funding_at=None,
                reference_price=Decimal("58000"),
                event_at=datetime(2025, 7, day, 8, tzinfo=UTC),
            )
            for day in (1, 9, 10)
        ]
        self.assertEqual(detect_crypto_series_health(marks), ())

    def test_value_level_problems_are_reported(self) -> None:
        findings = detect_crypto_series_health(
            [
                self.observation(
                    observation_kind="MARK_PRICE", funding_rate=None, target_funding_at=None,
                    reference_price=Decimal(0),
                ),
                self.observation(
                    observation_kind="OPEN_INTEREST", funding_rate=None, target_funding_at=None,
                    open_interest=Decimal(-1), open_interest_unit="BASE_ASSET",
                ),
                self.observation(
                    observation_kind="OPEN_INTEREST", funding_rate=None, target_funding_at=None,
                    open_interest=Decimal(1), open_interest_unit="QUOTE_NOTIONAL",
                ),
            ]
        )
        raised = {item.check_type for item in findings}
        self.assertIn(DataHealthCheck.NON_POSITIVE_REFERENCE_PRICE, raised)
        self.assertIn(DataHealthCheck.NEGATIVE_OPEN_INTEREST, raised)
        self.assertIn(DataHealthCheck.OPEN_INTEREST_UNIT_INCONSISTENCY, raised)

    def test_funding_bounds_are_only_checked_when_supplied(self) -> None:
        wild = self.observation(funding_rate=Decimal("0.9"))
        self.assertEqual(detect_crypto_series_health([wild]), ())
        findings = detect_crypto_series_health(
            [wild], funding_rate_cap=Decimal("0.0075"), funding_rate_floor=Decimal("-0.0075")
        )
        self.assertIn(
            DataHealthCheck.FUNDING_RATE_OUTSIDE_CONVENTION_BOUNDS,
            {item.check_type for item in findings},
        )

    def test_restatement_is_allowed_but_a_duplicate_revision_is_not(self) -> None:
        restated = [self.observation(revision=0), self.observation(revision=1)]
        self.assertEqual(detect_crypto_series_health(restated), ())
        duplicated = [self.observation(revision=0), self.observation(revision=0)]
        self.assertIn(
            DataHealthCheck.DUPLICATE_FUNDING_SETTLEMENT,
            {item.check_type for item in detect_crypto_series_health(duplicated)},
        )

    def test_staleness_is_only_reported_against_an_explicit_rule(self) -> None:
        evaluated_at = FUNDING_AT + timedelta(days=3)
        self.assertEqual(
            detect_crypto_series_health([self.observation()], evaluated_at=evaluated_at), ()
        )
        findings = detect_crypto_series_health(
            [self.observation()], stale_after=timedelta(hours=8), evaluated_at=evaluated_at
        )
        self.assertIn(
            DataHealthCheck.STALE_OBSERVATIONS, {item.check_type for item in findings}
        )

    def test_every_raised_check_belongs_to_the_declared_family(self) -> None:
        findings = detect_crypto_series_health(
            [
                self.observation(revision=0),
                self.observation(revision=0),
                self.observation(
                    observation_kind="MARK_PRICE", funding_rate=None, target_funding_at=None,
                    reference_price=Decimal(0),
                ),
                self.observation(
                    observation_kind="OPEN_INTEREST", funding_rate=None, target_funding_at=None,
                    open_interest=Decimal(-1), open_interest_unit="CONTRACTS",
                ),
                self.observation(
                    observation_kind="OPEN_INTEREST", funding_rate=None, target_funding_at=None,
                    open_interest=Decimal(1), open_interest_unit="BASE_ASSET",
                ),
                self.observation(funding_rate=Decimal("9")),
            ],
            expected_funding_instants=(datetime(2099, 1, 1, tzinfo=UTC),),
            funding_rate_cap=Decimal("0.0075"),
            stale_after=timedelta(seconds=1),
            evaluated_at=datetime(2025, 7, 20, tzinfo=UTC),
        )
        self.assertTrue(findings)
        self.assertLessEqual(
            {item.check_type for item in findings}, set(CRYPTO_SERIES_DATA_HEALTH_CHECKS)
        )


if __name__ == "__main__":
    unittest.main()
