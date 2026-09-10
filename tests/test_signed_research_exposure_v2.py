"""Pure unit evidence for Module 3J.2b.1's signed research exposure abstraction.

No PostgreSQL: this module is entirely in-memory, so every test here is a
plain dataclass/validation exercise.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_platform.signed_research_exposure_v2 import (
    SignedResearchExposureV2Error,
    SignedResearchSignalObservationV2,
    SignedResearchSignalSeriesV2,
)
from trade_platform.trend_strategy_v2 import (
    TrendSignalObservation,
    TrendSignalState,
    TrendStrategyV2Error,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
INSTRUMENT = "TESTFIXTURE:3J2B1:BTCUSDT:PERP"
EVIDENCE_HASH = "a" * 64
OTHER_EVIDENCE_HASH = "b" * 64


def observation(
    *,
    decision_at: datetime = START,
    exposure: Decimal = Decimal("0.25"),
    cap: Decimal = Decimal("0.5"),
    evidence_content_hash: str = EVIDENCE_HASH,
    instrument_id: str = INSTRUMENT,
) -> SignedResearchSignalObservationV2:
    return SignedResearchSignalObservationV2(
        instrument_id=instrument_id,
        decision_at=decision_at,
        exposure=exposure,
        maximum_absolute_exposure=cap,
        evidence_content_hash=evidence_content_hash,
    )


class SignedResearchSignalObservationV2Tests(unittest.TestCase):
    def test_short_valid(self) -> None:
        observation(exposure=Decimal("-0.4"), cap=Decimal("0.5")).validate()

    def test_flat_valid(self) -> None:
        observation(exposure=Decimal("0")).validate()

    def test_long_valid(self) -> None:
        observation(exposure=Decimal("0.4"), cap=Decimal("0.5")).validate()

    def test_direction_property(self) -> None:
        self.assertEqual(observation(exposure=Decimal("-0.1")).direction, "SHORT")
        self.assertEqual(observation(exposure=Decimal("0")).direction, "FLAT")
        self.assertEqual(observation(exposure=Decimal("0.1")).direction, "LONG")

    def test_cap_exactly_one_valid(self) -> None:
        observation(exposure=Decimal("1"), cap=Decimal("1")).validate()
        observation(exposure=Decimal("-1"), cap=Decimal("1")).validate()

    def test_cap_zero_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "maximum_absolute_exposure_out_of_bounds"):
            observation(exposure=Decimal("0"), cap=Decimal("0")).validate()

    def test_cap_negative_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "maximum_absolute_exposure_out_of_bounds"):
            observation(exposure=Decimal("0"), cap=Decimal("-0.5")).validate()

    def test_cap_above_one_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "maximum_absolute_exposure_out_of_bounds"):
            observation(exposure=Decimal("0.5"), cap=Decimal("1.5")).validate()

    def test_exposure_above_cap_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "signed_exposure_out_of_bounds"):
            observation(exposure=Decimal("0.6"), cap=Decimal("0.5")).validate()

    def test_exposure_below_negative_cap_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "signed_exposure_out_of_bounds"):
            observation(exposure=Decimal("-0.6"), cap=Decimal("0.5")).validate()

    def test_nan_exposure_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "exposure_must_be_finite"):
            observation(exposure=Decimal("NaN")).validate()

    def test_infinite_exposure_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "exposure_must_be_finite"):
            observation(exposure=Decimal("Infinity")).validate()

    def test_infinite_cap_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "maximum_absolute_exposure_must_be_finite"):
            observation(exposure=Decimal("0"), cap=Decimal("Infinity")).validate()

    def test_naive_decision_timestamp_rejected(self) -> None:
        naive = datetime(2026, 1, 1)  # noqa: DTZ001 -- intentionally naive
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "decision_at_must_be_timezone_aware"):
            observation(decision_at=naive).validate()

    def test_blank_instrument_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "signed_research_signal_instrument_missing"):
            observation(instrument_id="  ").validate()

    def test_missing_evidence_hash_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "evidence_content_hash_missing"):
            observation(evidence_content_hash="short").validate()


class SignedResearchSignalSeriesV2Tests(unittest.TestCase):
    def test_valid_series_short_flat_long(self) -> None:
        series = SignedResearchSignalSeriesV2.create(
            instrument_id=INSTRUMENT,
            maximum_absolute_exposure=Decimal("0.5"),
            observations=(
                observation(decision_at=START, exposure=Decimal("-0.3")),
                observation(decision_at=START + timedelta(hours=1), exposure=Decimal("0")),
                observation(decision_at=START + timedelta(hours=2), exposure=Decimal("0.3")),
            ),
        )
        self.assertEqual(len(series.observations), 3)
        self.assertEqual(len(series.content_hash), 64)

    def test_mixed_instrument_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "signed_research_series_mixed_instrument"):
            SignedResearchSignalSeriesV2.create(
                instrument_id=INSTRUMENT,
                maximum_absolute_exposure=Decimal("0.5"),
                observations=(
                    observation(decision_at=START, instrument_id=INSTRUMENT),
                    observation(
                        decision_at=START + timedelta(hours=1),
                        instrument_id="TESTFIXTURE:3J2B1:ETHUSDT:PERP",
                    ),
                ),
            )

    def test_duplicate_decision_identity_rejected(self) -> None:
        with self.assertRaisesRegex(
            SignedResearchExposureV2Error, "duplicate_signed_research_decision_identity|not_chronological"
        ):
            SignedResearchSignalSeriesV2.create(
                instrument_id=INSTRUMENT,
                maximum_absolute_exposure=Decimal("0.5"),
                observations=(
                    observation(decision_at=START, exposure=Decimal("0.1")),
                    observation(decision_at=START, exposure=Decimal("0.2")),
                ),
            )

    def test_non_chronological_series_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "signed_research_series_not_chronological"):
            SignedResearchSignalSeriesV2.create(
                instrument_id=INSTRUMENT,
                maximum_absolute_exposure=Decimal("0.5"),
                observations=(
                    observation(decision_at=START + timedelta(hours=1)),
                    observation(decision_at=START),
                ),
            )

    def test_empty_series_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "signed_research_series_requires_observations"):
            SignedResearchSignalSeriesV2.create(
                instrument_id=INSTRUMENT, maximum_absolute_exposure=Decimal("0.5"), observations=()
            )

    def test_cap_mismatch_rejected(self) -> None:
        with self.assertRaisesRegex(SignedResearchExposureV2Error, "signed_research_series_cap_mismatch"):
            SignedResearchSignalSeriesV2.create(
                instrument_id=INSTRUMENT,
                maximum_absolute_exposure=Decimal("0.5"),
                observations=(observation(cap=Decimal("0.3")),),
            )

    def test_deterministic_content_hash(self) -> None:
        def build() -> SignedResearchSignalSeriesV2:
            return SignedResearchSignalSeriesV2.create(
                instrument_id=INSTRUMENT,
                maximum_absolute_exposure=Decimal("0.5"),
                observations=(
                    observation(decision_at=START, exposure=Decimal("0.2")),
                    observation(decision_at=START + timedelta(hours=1), exposure=Decimal("-0.1")),
                ),
            )

        first = build()
        second = build()
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.series_id, second.series_id)

    def test_changed_exposure_changes_series_hash(self) -> None:
        base = SignedResearchSignalSeriesV2.create(
            instrument_id=INSTRUMENT,
            maximum_absolute_exposure=Decimal("0.5"),
            observations=(observation(decision_at=START, exposure=Decimal("0.2")),),
        )
        changed = SignedResearchSignalSeriesV2.create(
            instrument_id=INSTRUMENT,
            maximum_absolute_exposure=Decimal("0.5"),
            observations=(observation(decision_at=START, exposure=Decimal("0.21")),),
        )
        self.assertNotEqual(base.content_hash, changed.content_hash)

    def test_changed_evidence_hash_changes_series_hash(self) -> None:
        base = SignedResearchSignalSeriesV2.create(
            instrument_id=INSTRUMENT,
            maximum_absolute_exposure=Decimal("0.5"),
            observations=(observation(decision_at=START, evidence_content_hash=EVIDENCE_HASH),),
        )
        changed = SignedResearchSignalSeriesV2.create(
            instrument_id=INSTRUMENT,
            maximum_absolute_exposure=Decimal("0.5"),
            observations=(observation(decision_at=START, evidence_content_hash=OTHER_EVIDENCE_HASH),),
        )
        self.assertNotEqual(base.content_hash, changed.content_hash)


class TrendV2UnaffectedTests(unittest.TestCase):
    """Proves the new signed abstraction did not retrofit Trend V2's invariant."""

    def test_trend_signal_still_rejects_negative_exposure(self) -> None:
        signal = TrendSignalObservation(
            event_at=START, exposure=Decimal("-0.1"), state=TrendSignalState.AVAILABLE, reason=None
        )
        with self.assertRaisesRegex(TrendStrategyV2Error, "trend_signal_exposure_out_of_bounds"):
            signal.validate(Decimal("1"))


if __name__ == "__main__":
    unittest.main()
