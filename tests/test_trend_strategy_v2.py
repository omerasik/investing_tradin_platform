import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from trade_platform.feature_authority import FeatureMaterialization, FeatureQualityStatus
from trade_platform.trend_strategy_v2 import (
    AuthoritativeFeatureSeries,
    TrendFeatureRequirement,
    TrendSignalState,
    TrendStrategyDefinitionV2,
    TrendStrategyKind,
    breakout_signals,
    multi_horizon_trend_signals,
    time_series_momentum_signals,
    volatility_scaled_trend_exposure,
)

START = datetime(2025, 1, 1, tzinfo=UTC)


def requirement(name: str) -> TrendFeatureRequirement:
    return TrendFeatureRequirement(uuid5(NAMESPACE_URL, f"feature:{name}"), name, "1.0.0")


def definition(
    kind: TrendStrategyKind, required: tuple[TrendFeatureRequirement, ...]
) -> TrendStrategyDefinitionV2:
    return TrendStrategyDefinitionV2(
        uuid5(NAMESPACE_URL, f"strategy:{kind.value}"),
        f"trend-v2-{kind.value.lower()}",
        "trend-v2-code-1",
        kind,
        "Persistent directional information can survive transparent costs.",
        "A versioned trend feature is mapped to bounded long/flat research exposure.",
        ("Liquid synthetic US equity fixture",),
        required,
        "OHLCV",
        (("target_volatility", "0.10"),)
        if kind is TrendStrategyKind.VOLATILITY_SCALED_TREND
        else (("threshold", "0"),),
        "Enter only on an available positive authoritative feature.",
        "Exit on a non-positive authoritative feature.",
        "Unavailable or rejected required feature invalidates eligibility.",
        "Bounded long/flat research exposure.",
        Decimal("0.5"),
        "Multi-day fixture horizon.",
        "cost-v2",
        "Synthetic OHLCV/ADV assumptions only.",
        ("Sharp reversal", "Range-bound whipsaw"),
        ("SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",),
        START,
    )


def series(
    item: TrendFeatureRequirement,
    values: tuple[Decimal | None, ...],
    *,
    quality: FeatureQualityStatus = FeatureQualityStatus.VALIDATED,
) -> AuthoritativeFeatureSeries:
    materializations = []
    for index, value in enumerate(values):
        at = START + timedelta(days=index)
        materializations.append(
            FeatureMaterialization.create(
                feature_id=item.feature_id,
                instrument_id="fixture:SPY",
                dataset_version="trend-fixture-v2",
                event_at=at,
                effective_at=at,
                knowledge_at=at + timedelta(minutes=1),
                computed_at=at + timedelta(minutes=1),
                source_observation_manifest=(f"bar:{index}",),
                value=value,
                quality_status=quality,
            )
        )
    return AuthoritativeFeatureSeries(item, "trend-fixture-v2", tuple(materializations))


class TrendStrategyV2Tests(unittest.TestCase):
    def test_all_four_families_are_feature_bound_deterministic_and_bounded(self) -> None:
        momentum = requirement("multi_horizon_momentum")
        breakout = requirement("breakout")
        volatility = requirement("realized_volatility")
        momentum_values = series(momentum, (Decimal("-0.1"), Decimal("0.2"), Decimal("0.4")))

        outputs = (
            time_series_momentum_signals(
                definition(TrendStrategyKind.TIME_SERIES_MOMENTUM, (momentum,)), momentum_values
            ),
            breakout_signals(
                definition(TrendStrategyKind.BREAKOUT, (breakout,)),
                series(breakout, (Decimal("-0.1"), Decimal("0"), Decimal("0.1"))),
            ),
            multi_horizon_trend_signals(
                definition(TrendStrategyKind.MULTI_HORIZON_TREND, (momentum,)), momentum_values
            ),
            volatility_scaled_trend_exposure(
                definition(
                    TrendStrategyKind.VOLATILITY_SCALED_TREND,
                    (momentum, volatility),
                ),
                momentum_values,
                series(volatility, (Decimal("0.2"), Decimal("0.1"), Decimal("0.05"))),
                target_volatility=Decimal("0.1"),
            ),
        )
        for output in outputs:
            self.assertTrue(output.eligible)
            self.assertTrue(all(Decimal("0") <= item <= Decimal("0.5") for item in output.require_available()))

    def test_missing_feature_is_explicitly_unavailable_not_zero(self) -> None:
        momentum = requirement("multi_horizon_momentum")
        output = time_series_momentum_signals(
            definition(TrendStrategyKind.TIME_SERIES_MOMENTUM, (momentum,)),
            series(
                momentum,
                (Decimal("0.1"), None),
                quality=FeatureQualityStatus.DEGRADED,
            ),
        )
        self.assertFalse(output.eligible)
        self.assertIsNone(output.observations[-1].exposure)
        self.assertEqual(output.observations[-1].state, TrendSignalState.UNAVAILABLE)

    def test_future_append_does_not_change_existing_signal_prefix(self) -> None:
        momentum = requirement("multi_horizon_momentum")
        strategy = definition(TrendStrategyKind.TIME_SERIES_MOMENTUM, (momentum,))
        prefix = time_series_momentum_signals(
            strategy, series(momentum, (Decimal("-0.1"), Decimal("0.2")))
        )
        appended = time_series_momentum_signals(
            strategy,
            series(momentum, (Decimal("-0.1"), Decimal("0.2"), Decimal("-0.5"))),
        )
        self.assertEqual(prefix.observations, appended.observations[:2])


if __name__ == "__main__":
    unittest.main()
