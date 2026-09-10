"""Pure unit evidence for Module 3J.2b.1's signed OPEN-to-OPEN return primitive.

No PostgreSQL: ``AuthoritativeTradableBarV2`` is a plain dataclass, so bars
are built directly rather than read through the Postgres reader.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform import paper_execution
from trade_platform.research import CostModel
from trade_platform.signed_price_return_v2 import (
    SignedPriceReturnV2Error,
    compute_signed_open_to_open_return,
)
from trade_platform.tradable_bar_evidence_v2 import AuthoritativeTradableBarV2

START = datetime(2026, 1, 1, tzinfo=UTC)
DATASET_ID = uuid4()
OTHER_DATASET_ID = uuid4()
INSTRUMENT = "TESTFIXTURE:3J2B1:BTCUSDT:PERP"
ZERO_COST = CostModel(Decimal("0"), Decimal("0"), Decimal("0"))


def bar(
    *,
    bar_open_at: datetime,
    open_price: Decimal,
    instrument_id: str = INSTRUMENT,
    dataset_version_id=DATASET_ID,
) -> AuthoritativeTradableBarV2:
    return AuthoritativeTradableBarV2(
        dataset_version_id=dataset_version_id,
        dataset_content_hash="c" * 64,
        source_id=uuid4(),
        normalized_observation_id=uuid4(),
        raw_observation_id=uuid4(),
        raw_payload_sha256="d" * 64,
        instrument_id=instrument_id,
        interval="1m",
        bar_open_at=bar_open_at,
        bar_close_at=bar_open_at + timedelta(minutes=1),
        normalized_at=bar_open_at + timedelta(minutes=2),
        revision=0,
        open=open_price,
        high=open_price,
        low=open_price,
        close=open_price,
        volume=Decimal("1"),
        provenance_uri="fixture://bar",
    )


ENTRY = bar(bar_open_at=START, open_price=Decimal("100"))
EXIT_UP = bar(bar_open_at=START + timedelta(minutes=1), open_price=Decimal("110"))
EXIT_DOWN = bar(bar_open_at=START + timedelta(minutes=1), open_price=Decimal("90"))


class SignedOpenToOpenReturnTests(unittest.TestCase):
    def test_long_gain(self) -> None:
        result = compute_signed_open_to_open_return(
            entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("1"),
            maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
        )
        self.assertEqual(result.gross_return, Decimal("0.1"))
        self.assertEqual(result.net_return, Decimal("0.1"))

    def test_long_loss(self) -> None:
        result = compute_signed_open_to_open_return(
            entry_bar=ENTRY, exit_bar=EXIT_DOWN, exposure=Decimal("1"),
            maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
        )
        self.assertEqual(result.gross_return, Decimal("-0.1"))

    def test_short_gain_when_price_falls(self) -> None:
        result = compute_signed_open_to_open_return(
            entry_bar=ENTRY, exit_bar=EXIT_DOWN, exposure=Decimal("-1"),
            maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
        )
        self.assertEqual(result.gross_return, Decimal("0.1"))

    def test_short_loss_when_price_rises(self) -> None:
        result = compute_signed_open_to_open_return(
            entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("-1"),
            maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
        )
        self.assertEqual(result.gross_return, Decimal("-0.1"))

    def test_zero_exposure_zero_gross_return(self) -> None:
        result = compute_signed_open_to_open_return(
            entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("0"),
            maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
        )
        self.assertEqual(result.gross_return, Decimal("0"))

    def test_entry_and_exit_costs_charged_separately(self) -> None:
        costs = CostModel(Decimal("0"), Decimal("0.01"), Decimal("0"))
        result = compute_signed_open_to_open_return(
            entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("0.5"),
            maximum_absolute_exposure=Decimal("1"), cost_model=costs,
        )
        expected_leg_cost = Decimal("0.5") * Decimal("0.01")
        self.assertEqual(result.entry_cost, expected_leg_cost)
        self.assertEqual(result.exit_cost, expected_leg_cost)
        self.assertEqual(
            result.net_return, result.gross_return - result.entry_cost - result.exit_cost
        )

    def test_cost_free_result_equals_analytical_formula(self) -> None:
        result = compute_signed_open_to_open_return(
            entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("0.4"),
            maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
        )
        expected = Decimal("0.4") * (Decimal("110") / Decimal("100") - Decimal("1"))
        self.assertEqual(result.gross_return, expected)
        self.assertEqual(result.net_return, expected)

    def test_zero_entry_price_rejected(self) -> None:
        bad_entry = bar(bar_open_at=START, open_price=Decimal("0"))
        with self.assertRaisesRegex(SignedPriceReturnV2Error, "non_positive_bar_price|non_positive_price"):
            compute_signed_open_to_open_return(
                entry_bar=bad_entry, exit_bar=EXIT_UP, exposure=Decimal("1"),
                maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
            )

    def test_negative_exit_price_rejected(self) -> None:
        bad_exit = bar(bar_open_at=START + timedelta(minutes=1), open_price=Decimal("-1"))
        with self.assertRaisesRegex(SignedPriceReturnV2Error, "non_positive_bar_price|non_positive_price"):
            compute_signed_open_to_open_return(
                entry_bar=ENTRY, exit_bar=bad_exit, exposure=Decimal("1"),
                maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
            )

    def test_non_finite_exposure_rejected(self) -> None:
        with self.assertRaisesRegex(SignedPriceReturnV2Error, "non_finite_exposure"):
            compute_signed_open_to_open_return(
                entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("NaN"),
                maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
            )

    def test_exit_not_after_entry_rejected(self) -> None:
        with self.assertRaisesRegex(SignedPriceReturnV2Error, "exit_not_after_entry"):
            compute_signed_open_to_open_return(
                entry_bar=EXIT_UP, exit_bar=ENTRY, exposure=Decimal("1"),
                maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
            )

    def test_wrong_instrument_pairing_rejected(self) -> None:
        other_instrument_exit = bar(
            bar_open_at=START + timedelta(minutes=1), open_price=Decimal("110"),
            instrument_id="TESTFIXTURE:3J2B1:ETHUSDT:PERP",
        )
        with self.assertRaisesRegex(SignedPriceReturnV2Error, "instrument_mismatch"):
            compute_signed_open_to_open_return(
                entry_bar=ENTRY, exit_bar=other_instrument_exit, exposure=Decimal("1"),
                maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
            )

    def test_wrong_dataset_pairing_rejected(self) -> None:
        other_dataset_exit = bar(
            bar_open_at=START + timedelta(minutes=1), open_price=Decimal("110"),
            dataset_version_id=OTHER_DATASET_ID,
        )
        with self.assertRaisesRegex(SignedPriceReturnV2Error, "dataset_mismatch"):
            compute_signed_open_to_open_return(
                entry_bar=ENTRY, exit_bar=other_dataset_exit, exposure=Decimal("1"),
                maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
            )

    def test_exposure_over_cap_rejected(self) -> None:
        with self.assertRaisesRegex(SignedPriceReturnV2Error, "signed_exposure_out_of_bounds"):
            compute_signed_open_to_open_return(
                entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("0.6"),
                maximum_absolute_exposure=Decimal("0.5"), cost_model=ZERO_COST,
            )

    def test_funding_never_appears_in_result(self) -> None:
        result = compute_signed_open_to_open_return(
            entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("1"),
            maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
        )
        self.assertFalse(hasattr(result, "funding"))
        self.assertFalse(hasattr(result, "funding_cashflow"))

    def test_apply_funding_not_called(self) -> None:
        original = paper_execution.apply_funding
        called = False

        def spy(*args: object, **kwargs: object) -> object:
            nonlocal called
            called = True
            return original(*args, **kwargs)  # type: ignore[arg-type]

        paper_execution.apply_funding = spy  # type: ignore[assignment]
        try:
            compute_signed_open_to_open_return(
                entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("1"),
                maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
            )
        finally:
            paper_execution.apply_funding = original  # type: ignore[assignment]
        self.assertFalse(called)

    def test_deterministic_decimal_result(self) -> None:
        first = compute_signed_open_to_open_return(
            entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("0.3"),
            maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
        )
        second = compute_signed_open_to_open_return(
            entry_bar=ENTRY, exit_bar=EXIT_UP, exposure=Decimal("0.3"),
            maximum_absolute_exposure=Decimal("1"), cost_model=ZERO_COST,
        )
        self.assertEqual(first, second)
        self.assertIsInstance(first.net_return, Decimal)

    def test_independent_hand_calculated_fixture_matches_exactly(self) -> None:
        entry = bar(bar_open_at=START, open_price=Decimal("200"))
        exit_bar = bar(bar_open_at=START + timedelta(minutes=1), open_price=Decimal("220"))
        costs = CostModel(Decimal("0"), Decimal("0.001"), Decimal("0.0005"))
        result = compute_signed_open_to_open_return(
            entry_bar=entry, exit_bar=exit_bar, exposure=Decimal("-0.5"),
            maximum_absolute_exposure=Decimal("1"), cost_model=costs,
        )
        # Hand calculation: gross = -0.5 * (220/200 - 1) = -0.5 * 0.1 = -0.05
        # turnover per leg = 0.5; cost per leg = 0.5 * (0.001+0.0005) = 0.00075
        # net = -0.05 - 0.00075 - 0.00075 = -0.0515
        self.assertEqual(result.gross_return, Decimal("-0.05"))
        self.assertEqual(result.entry_cost, Decimal("0.00075"))
        self.assertEqual(result.exit_cost, Decimal("0.00075"))
        self.assertEqual(result.net_return, Decimal("-0.0515"))


if __name__ == "__main__":
    unittest.main()
