"""Pure unit evidence for Module 3I.1 typed settlement / open-interest payloads.

No database and no network. Every price, quantity and identifier is a FIXTURE.
Nothing here was retrieved from or verified against any exchange, and no real
contract specification, settlement price or open-interest figure is claimed.
"""

import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from trade_platform.data_health import (
    FUTURES_SERIES_DATA_HEALTH_CHECKS,
    DataHealthCheck,
    FuturesSeriesObservation,
    detect_futures_series_health,
)
from trade_platform.futures_market_observations import (
    FUTURES_SUPPORTED_OPEN_INTEREST_UNITS,
    OPEN_INTEREST_PAYLOAD_TABLE,
    SETTLEMENT_PAYLOAD_TABLE,
    OpenInterestUnit,
    SettlementFinality,
    canonical_payload_marker,
    parse_open_interest_payload,
    parse_settlement_payload,
)

EFFECTIVE = datetime(2025, 6, 20, 18, 0, tzinfo=UTC)
ALL_UNITS = frozenset(OpenInterestUnit)


def settlement_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "settlement_price": "2350.40",
        "price_currency": "USD",
        "settlement_date": "2025-06-20",
        "settlement_effective_at": "2025-06-20T18:00:00+00:00",
        "finality": "PRELIMINARY",
        "quote_unit": "USD_PER_TROY_OUNCE",
    }
    payload.update(overrides)
    return payload


def open_interest_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "open_interest": "412500",
        "unit": "CONTRACTS",
        "observed_at": "2025-06-20T18:00:00+00:00",
    }
    payload.update(overrides)
    return payload


class SettlementPayloadTests(unittest.TestCase):
    def test_valid_payload_parses_without_issues(self) -> None:
        parsed, issues = parse_settlement_payload(settlement_payload())
        self.assertEqual(issues, ())
        assert parsed is not None
        self.assertEqual(parsed.settlement_price, Decimal("2350.40"))
        self.assertEqual(parsed.settlement_date, date(2025, 6, 20))
        self.assertEqual(parsed.finality, SettlementFinality.PRELIMINARY)

    def test_non_positive_settlement_is_rejected(self) -> None:
        """Invariant 1: a settlement price of zero or below is never a price."""
        for price in ("0", "-1.5"):
            parsed, issues = parse_settlement_payload(settlement_payload(settlement_price=price))
            self.assertIsNone(parsed)
            self.assertIn("non_positive_settlement_price", issues)

    def test_missing_and_unparseable_fields_are_reported_not_guessed(self) -> None:
        parsed, issues = parse_settlement_payload(settlement_payload(settlement_price=None))
        self.assertIsNone(parsed)
        self.assertIn("missing_settlement_price", issues)
        parsed, issues = parse_settlement_payload(settlement_payload(settlement_price="abc"))
        self.assertIsNone(parsed)
        self.assertIn("invalid_settlement_price", issues)

    def test_naive_effective_instant_is_rejected(self) -> None:
        parsed, issues = parse_settlement_payload(
            settlement_payload(settlement_effective_at="2025-06-20T18:00:00")
        )
        self.assertIsNone(parsed)
        self.assertIn("naive_settlement_effective_at", issues)

    def test_unknown_finality_is_rejected(self) -> None:
        parsed, issues = parse_settlement_payload(settlement_payload(finality="MAYBE_FINAL"))
        self.assertIsNone(parsed)
        self.assertIn("invalid_settlement_finality", issues)

    def test_projection_round_trips_the_canonical_values(self) -> None:
        """Invariant 20: the read projection must agree with the canonical row."""
        parsed, _ = parse_settlement_payload(settlement_payload())
        assert parsed is not None
        projection = parsed.as_normalized_projection()
        self.assertEqual(projection["settlement_price"], str(parsed.settlement_price))
        self.assertEqual(projection["finality"], parsed.finality.value)
        self.assertEqual(projection["settlement_date"], parsed.settlement_date.isoformat())
        # The projection carries no field the canonical payload does not.
        self.assertEqual(
            set(projection),
            {"settlement_price", "price_currency", "settlement_date",
             "settlement_effective_at", "finality", "quote_unit"},
        )

    def test_canonical_tuple_changes_with_every_financial_field(self) -> None:
        baseline, _ = parse_settlement_payload(settlement_payload())
        assert baseline is not None
        for change in (
            {"settlement_price": "2350.41"},
            {"price_currency": "EUR"},
            {"finality": "FINAL"},
            {"settlement_date": "2025-06-21"},
            {"quote_unit": "USD_PER_BARREL"},
        ):
            altered, _ = parse_settlement_payload(settlement_payload(**change))
            assert altered is not None
            self.assertNotEqual(baseline.canonical_tuple(), altered.canonical_tuple(), change)


class OpenInterestPayloadTests(unittest.TestCase):
    def test_valid_contract_count_parses(self) -> None:
        parsed, issues = parse_open_interest_payload(
            open_interest_payload(), supported_units=FUTURES_SUPPORTED_OPEN_INTEREST_UNITS
        )
        self.assertEqual(issues, ())
        assert parsed is not None
        self.assertEqual(parsed.unit, OpenInterestUnit.CONTRACTS)
        self.assertIsNone(parsed.unit_asset)

    def test_zero_open_interest_is_valid_but_negative_is_not(self) -> None:
        """Invariant 2: an expiring contract legitimately reaches zero."""
        parsed, issues = parse_open_interest_payload(
            open_interest_payload(open_interest="0"),
            supported_units=FUTURES_SUPPORTED_OPEN_INTEREST_UNITS,
        )
        self.assertEqual(issues, ())
        assert parsed is not None
        self.assertEqual(parsed.open_interest, Decimal(0))

        parsed, issues = parse_open_interest_payload(
            open_interest_payload(open_interest="-1"),
            supported_units=FUTURES_SUPPORTED_OPEN_INTEREST_UNITS,
        )
        self.assertIsNone(parsed)
        self.assertIn("negative_open_interest", issues)

    def test_missing_unit_is_rejected(self) -> None:
        """Invariant 3: open interest is never an unqualified number."""
        parsed, issues = parse_open_interest_payload(
            open_interest_payload(unit=""), supported_units=ALL_UNITS
        )
        self.assertIsNone(parsed)
        self.assertIn("missing_open_interest_unit", issues)

    def test_unknown_unit_is_rejected(self) -> None:
        """Invariant 4, part one: a unit outside the enum."""
        parsed, issues = parse_open_interest_payload(
            open_interest_payload(unit="LOTS"), supported_units=ALL_UNITS
        )
        self.assertIsNone(parsed)
        self.assertIn("unsupported_open_interest_unit", issues)

    def test_unit_valid_but_unsupported_for_futures_is_rejected(self) -> None:
        """Invariant 4, part two: never silently converted into contracts."""
        for unit in ("BASE_ASSET", "QUOTE_NOTIONAL"):
            parsed, issues = parse_open_interest_payload(
                open_interest_payload(unit=unit, unit_asset="BTC"),
                supported_units=FUTURES_SUPPORTED_OPEN_INTEREST_UNITS,
            )
            self.assertIsNone(parsed)
            self.assertIn("unsupported_open_interest_unit", issues)

    def test_asset_denominated_units_require_naming_the_asset(self) -> None:
        parsed, issues = parse_open_interest_payload(
            open_interest_payload(unit="BASE_ASSET"), supported_units=ALL_UNITS
        )
        self.assertIsNone(parsed)
        self.assertIn("open_interest_unit_requires_asset", issues)

    def test_contract_count_cannot_declare_a_unit_asset(self) -> None:
        parsed, issues = parse_open_interest_payload(
            open_interest_payload(unit_asset="BTC"), supported_units=ALL_UNITS
        )
        self.assertIsNone(parsed)
        self.assertIn("contract_count_cannot_declare_unit_asset", issues)

    def test_units_are_modelled_but_never_converted(self) -> None:
        base, _ = parse_open_interest_payload(
            open_interest_payload(unit="BASE_ASSET", unit_asset="BTC"), supported_units=ALL_UNITS
        )
        contracts, _ = parse_open_interest_payload(
            open_interest_payload(), supported_units=ALL_UNITS
        )
        assert base is not None and contracts is not None
        # Same number, different measurement -- and so a different canonical
        # identity. No arithmetic relates the two.
        self.assertEqual(base.open_interest, contracts.open_interest)
        self.assertNotEqual(base.canonical_tuple(), contracts.canonical_tuple())

    def test_projection_agrees_with_canonical_values(self) -> None:
        parsed, _ = parse_open_interest_payload(
            open_interest_payload(), supported_units=ALL_UNITS
        )
        assert parsed is not None
        projection = parsed.as_normalized_projection()
        self.assertEqual(projection["open_interest"], str(parsed.open_interest))
        self.assertEqual(projection["unit"], parsed.unit.value)


class CanonicalMarkerTests(unittest.TestCase):
    def test_marker_carries_no_financial_value(self) -> None:
        """The envelope must never hold a second copy of the value."""
        for table in (SETTLEMENT_PAYLOAD_TABLE, OPEN_INTEREST_PAYLOAD_TABLE):
            marker = canonical_payload_marker(table)
            self.assertEqual(marker, {"canonical_payload_table": table})
            self.assertNotIn("settlement_price", marker)
            self.assertNotIn("open_interest", marker)


class FuturesSeriesDataHealthTests(unittest.TestCase):
    def observation(self, **overrides: object) -> FuturesSeriesObservation:
        from uuid import UUID

        fields: dict[str, object] = {
            "source_id": UUID(int=1),
            "instrument_id": "TESTFIXTURE:FUT:XCEC:GC062025",
            "observation_kind": "SETTLEMENT_PRICE",
            "event_at": EFFECTIVE,
            "ingested_at": EFFECTIVE,
            "revision": 0,
            "settlement_price": Decimal("2350.40"),
            "finality": "PRELIMINARY",
        }
        fields.update(overrides)
        return FuturesSeriesObservation(**fields)  # type: ignore[arg-type]

    def test_clean_series_reports_nothing(self) -> None:
        self.assertEqual(detect_futures_series_health([self.observation()]), ())

    def test_no_cadence_is_invented_when_sessions_are_not_supplied(self) -> None:
        """Settlement and OI have no universal cadence; silence is the honest answer."""
        sparse = [
            self.observation(event_at=datetime(2025, 6, 2, 18, tzinfo=UTC)),
            self.observation(event_at=datetime(2025, 6, 20, 18, tzinfo=UTC)),
        ]
        findings = detect_futures_series_health(sparse)
        self.assertNotIn(
            DataHealthCheck.MISSING_EXPECTED_SESSIONS, {item.check_type for item in findings}
        )

    def test_completeness_is_evaluated_only_against_supplied_sessions(self) -> None:
        findings = detect_futures_series_health(
            [self.observation(event_at=datetime(2025, 6, 2, 18, tzinfo=UTC))],
            expected_sessions=(date(2025, 6, 2), date(2025, 6, 3)),
        )
        missing = [
            item for item in findings
            if item.check_type is DataHealthCheck.MISSING_EXPECTED_SESSIONS
        ]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].detail["missing_sessions"], ["2025-06-03"])

    def test_non_positive_settlement_and_negative_open_interest_block(self) -> None:
        findings = detect_futures_series_health([
            self.observation(settlement_price=Decimal(0)),
            self.observation(
                observation_kind="OPEN_INTEREST", settlement_price=None, finality=None,
                open_interest=Decimal(-5), open_interest_unit="CONTRACTS",
            ),
        ])
        raised = {item.check_type for item in findings}
        self.assertIn(DataHealthCheck.NON_POSITIVE_SETTLEMENT, raised)
        self.assertIn(DataHealthCheck.NEGATIVE_OPEN_INTEREST, raised)

    def test_mixed_open_interest_units_block(self) -> None:
        findings = detect_futures_series_health([
            self.observation(
                observation_kind="OPEN_INTEREST", settlement_price=None, finality=None,
                open_interest=Decimal(10), open_interest_unit="CONTRACTS",
            ),
            self.observation(
                observation_kind="OPEN_INTEREST", settlement_price=None, finality=None,
                open_interest=Decimal(10), open_interest_unit="BASE_ASSET",
            ),
        ])
        self.assertIn(
            DataHealthCheck.OPEN_INTEREST_UNIT_INCONSISTENCY,
            {item.check_type for item in findings},
        )

    def test_final_settlement_demoted_to_preliminary_is_flagged(self) -> None:
        findings = detect_futures_series_health([
            self.observation(revision=0, finality="FINAL"),
            self.observation(revision=1, finality="PRELIMINARY"),
        ])
        self.assertIn(
            DataHealthCheck.SETTLEMENT_FINALITY_REGRESSION,
            {item.check_type for item in findings},
        )

    def test_every_raised_check_belongs_to_the_declared_family(self) -> None:
        findings = detect_futures_series_health(
            [
                self.observation(settlement_price=Decimal(0)),
                self.observation(revision=1, finality="FINAL"),
                self.observation(revision=2, finality="PRELIMINARY"),
                self.observation(
                    observation_kind="OPEN_INTEREST", settlement_price=None, finality=None,
                    open_interest=Decimal(-1), open_interest_unit="CONTRACTS",
                ),
                self.observation(
                    observation_kind="OPEN_INTEREST", settlement_price=None, finality=None,
                    open_interest=Decimal(1), open_interest_unit="BASE_ASSET",
                ),
            ],
            expected_sessions=(date(2025, 6, 20), date(2099, 1, 1)),
        )
        self.assertTrue(findings)
        self.assertLessEqual(
            {item.check_type for item in findings}, set(FUTURES_SERIES_DATA_HEALTH_CHECKS)
        )


if __name__ == "__main__":
    unittest.main()
