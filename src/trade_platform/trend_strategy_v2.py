"""Research-only Trend V2 strategies using the Cycle 200 Feature Authority."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .feature_authority import TransparentFeatureCalculator, TransparentMarketWindow


class TrendStrategyV2Error(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TrendStrategyDefinitionV2:
    version: str
    family: str
    hypothesis: str
    required_feature_versions: tuple[str, ...]
    entry_logic: str
    exit_logic: str
    sizing_policy: str
    maximum_exposure: Decimal
    expected_holding_period: str
    cost_model_version: str
    capacity_assumptions: str
    parameter_set: tuple[tuple[str, str], ...]
    failure_regimes: tuple[str, ...]
    limitations: tuple[str, ...]

    def validate(self) -> None:
        text = (
            self.version, self.family, self.hypothesis, self.entry_logic, self.exit_logic,
            self.sizing_policy, self.expected_holding_period, self.cost_model_version,
            self.capacity_assumptions,
        )
        if (
            not all(value.strip() for value in text)
            or not self.required_feature_versions
            or not self.parameter_set
            or not self.failure_regimes
            or not self.limitations
            or self.maximum_exposure <= 0
            or self.maximum_exposure > 1
        ):
            raise TrendStrategyV2Error("incomplete_trend_strategy_definition")


@dataclass(frozen=True, slots=True)
class TrendMarketSeries:
    closes: tuple[Decimal, ...]
    highs: tuple[Decimal, ...]
    lows: tuple[Decimal, ...]
    volumes: tuple[Decimal, ...]

    def window(self, end: int, lookback: int) -> TransparentMarketWindow:
        if lookback < 2 or end + 1 < lookback:
            raise TrendStrategyV2Error("trend_feature_history_insufficient")
        start = end + 1 - lookback
        return TransparentMarketWindow(
            self.closes[start : end + 1],
            self.highs[start : end + 1],
            self.lows[start : end + 1],
            self.volumes[start : end + 1],
        )


def _feature(series: TrendMarketSeries, end: int, lookback: int, name: str) -> Decimal | None:
    if end + 1 < lookback:
        return None
    return TransparentFeatureCalculator.calculate(name, series.window(end, lookback))


def time_series_momentum_signals(
    series: TrendMarketSeries, *, lookback: int, threshold: Decimal = Decimal("0")
) -> tuple[Decimal, ...]:
    """Use the versioned multi-horizon momentum definition; no inline indicator."""
    if lookback < 2:
        raise TrendStrategyV2Error("invalid_trend_lookback")
    return tuple(
        Decimal("0") if (value := _feature(series, index, lookback, "multi_horizon_momentum")) is None
        else Decimal("1") if value > threshold else Decimal("0")
        for index in range(len(series.closes))
    )


def breakout_signals(series: TrendMarketSeries, *, lookback: int) -> tuple[Decimal, ...]:
    if lookback < 2:
        raise TrendStrategyV2Error("invalid_trend_lookback")
    return tuple(
        Decimal("0") if (value := _feature(series, index, lookback, "breakout")) is None
        else Decimal("1") if value >= 0 else Decimal("0")
        for index in range(len(series.closes))
    )


def multi_horizon_trend_signals(
    series: TrendMarketSeries, *, lookbacks: tuple[int, ...]
) -> tuple[Decimal, ...]:
    if not lookbacks or len(set(lookbacks)) != len(lookbacks) or min(lookbacks) < 2:
        raise TrendStrategyV2Error("invalid_trend_horizons")
    return tuple(
        Decimal("0") if not (values := tuple(_feature(series, index, window, "multi_horizon_return") for window in lookbacks)) or any(value is None for value in values)
        else Decimal(sum(value > 0 for value in values)) / Decimal(len(values))
        for index in range(len(series.closes))
    )


def volatility_scaled_trend_exposure(
    series: TrendMarketSeries, *, lookback: int, target_volatility: Decimal, maximum_exposure: Decimal
) -> tuple[Decimal, ...]:
    if lookback < 2 or target_volatility <= 0 or maximum_exposure <= 0 or maximum_exposure > 1:
        raise TrendStrategyV2Error("invalid_volatility_scaled_trend_parameters")
    exposures: list[Decimal] = []
    for index in range(len(series.closes)):
        momentum = _feature(series, index, lookback, "multi_horizon_momentum")
        volatility = _feature(series, index, lookback, "realized_volatility")
        exposures.append(
            Decimal("0") if momentum is None or volatility is None or momentum <= 0 or volatility <= 0
            else min(maximum_exposure, target_volatility / volatility)
        )
    return tuple(exposures)
