"""Pure unit tests for Module 3I.3 deterministic futures term-structure derivation.

No database, no network. Every price, date and identifier here is a FIXTURE;
nothing was retrieved from or verified against any exchange.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trade_platform.futures_term_structure import (
    CurveClassification,
    DayCountConvention,
    FuturesTermStructureMethod,
    FuturesTermStructureMethodError,
    SessionPolicy,
    SettlementFinalityPolicy,
    classify_curve,
)


def method(**overrides: object) -> FuturesTermStructureMethod:
    defaults: dict[str, object] = {
        "method_name": "SETTLEMENT_CURVE_V1",
        "method_version": 1,
        "minimum_point_count": 2,
        "settlement_finality_policy": SettlementFinalityPolicy.FINAL_ONLY,
        "session_policy": SessionPolicy.SAME_SESSION_STRICT,
        "classification_enabled": False,
        "carry_enabled": False,
        "effective_from": datetime(2026, 1, 1, tzinfo=UTC),
        "known_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return FuturesTermStructureMethod(**defaults)  # type: ignore[arg-type]


class FuturesTermStructureMethodTests(unittest.TestCase):
    def test_valid_method_round_trips_content_hash(self) -> None:
        one = method()
        two = method(method_id=one.method_id, created_at=one.created_at)
        self.assertEqual(one.content_hash(), two.content_hash())
        self.assertEqual(len(one.content_hash()), 64)

    def test_content_hash_changes_with_method_version(self) -> None:
        self.assertNotEqual(method(method_version=1).content_hash(), method(method_version=2).content_hash())

    def test_content_hash_changes_with_finality_policy(self) -> None:
        strict = method(settlement_finality_policy=SettlementFinalityPolicy.FINAL_ONLY)
        loose = method(
            settlement_finality_policy=SettlementFinalityPolicy.LATEST_KNOWN_ALLOW_PRELIMINARY
        )
        self.assertNotEqual(strict.content_hash(), loose.content_hash())

    def test_content_hash_excludes_known_at_and_method_id(self) -> None:
        one = method(known_at=datetime(2026, 1, 1, tzinfo=UTC))
        two = method(known_at=datetime(2026, 6, 1, tzinfo=UTC), method_id=uuid4())
        self.assertEqual(one.content_hash(), two.content_hash())

    # ---- invariant 26: classification never implicit ---------------------

    def test_classification_enabled_requires_minimum_point_count(self) -> None:
        with self.assertRaises(FuturesTermStructureMethodError) as raised:
            method(classification_enabled=True, classification_flat_threshold=Decimal("0.01"))
        self.assertIn("classification_requires_minimum_point_count", str(raised.exception))

    def test_classification_enabled_requires_positive_threshold(self) -> None:
        with self.assertRaises(FuturesTermStructureMethodError) as raised:
            method(classification_enabled=True, classification_minimum_point_count=2)
        self.assertIn("classification_requires_positive_flat_threshold", str(raised.exception))

    def test_classification_fields_require_classification_enabled(self) -> None:
        with self.assertRaises(FuturesTermStructureMethodError) as raised:
            method(classification_enabled=False, classification_minimum_point_count=2)
        self.assertIn("classification_fields_require_classification_enabled", str(raised.exception))

    def test_classification_flat_threshold_must_be_positive(self) -> None:
        with self.assertRaises(FuturesTermStructureMethodError):
            method(
                classification_enabled=True, classification_minimum_point_count=2,
                classification_flat_threshold=Decimal("0"),
            )

    # ---- invariant 27: no hidden annualization ----------------------------

    def test_carry_enabled_requires_day_count_convention(self) -> None:
        with self.assertRaises(FuturesTermStructureMethodError) as raised:
            method(carry_enabled=True)
        self.assertIn("carry_requires_explicit_day_count_convention", str(raised.exception))

    def test_day_count_convention_requires_carry_enabled(self) -> None:
        with self.assertRaises(FuturesTermStructureMethodError) as raised:
            method(carry_enabled=False, day_count_convention=DayCountConvention.ACT_365F)
        self.assertIn("day_count_convention_requires_carry_enabled", str(raised.exception))

    # ---- session / staleness policy declaration ---------------------------

    def test_strict_session_policy_forbids_staleness_fields(self) -> None:
        with self.assertRaises(FuturesTermStructureMethodError) as raised:
            method(session_policy=SessionPolicy.SAME_SESSION_STRICT, max_staleness_days=1)
        self.assertIn("staleness_fields_not_applicable_to_strict_session_policy", str(raised.exception))

    def test_tolerance_session_policy_requires_positive_staleness(self) -> None:
        with self.assertRaises(FuturesTermStructureMethodError) as raised:
            method(session_policy=SessionPolicy.SAME_SESSION_WITH_STALENESS_TOLERANCE)
        self.assertIn("staleness_tolerance_requires_positive_max_staleness_days", str(raised.exception))

    def test_tolerance_session_policy_accepts_positive_staleness(self) -> None:
        built = method(
            session_policy=SessionPolicy.SAME_SESSION_WITH_STALENESS_TOLERANCE,
            max_staleness_days=3,
        )
        self.assertEqual(built.max_staleness_days, 3)


class ClassifyCurveTests(unittest.TestCase):
    def test_contango_when_back_exceeds_front_beyond_threshold(self) -> None:
        prices = (Decimal("100"), Decimal("101"), Decimal("110"))
        self.assertEqual(classify_curve(prices, Decimal("0.01")), CurveClassification.CONTANGO)

    def test_backwardation_when_back_below_front_beyond_threshold(self) -> None:
        prices = (Decimal("110"), Decimal("105"), Decimal("100"))
        self.assertEqual(classify_curve(prices, Decimal("0.01")), CurveClassification.BACKWARDATION)

    def test_flat_within_threshold(self) -> None:
        prices = (Decimal("100"), Decimal("100.05"))
        self.assertEqual(classify_curve(prices, Decimal("0.01")), CurveClassification.FLAT)

    def test_requires_at_least_two_points(self) -> None:
        with self.assertRaises(ValueError):
            classify_curve((Decimal("100"),), Decimal("0.01"))

    def test_requires_positive_threshold(self) -> None:
        with self.assertRaises(ValueError):
            classify_curve((Decimal("100"), Decimal("101")), Decimal("0"))


if __name__ == "__main__":
    unittest.main()
