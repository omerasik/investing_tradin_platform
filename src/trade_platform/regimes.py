"""Transparent probabilistic market-regime estimates and bounded ensemble allocation."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class RegimeValidationError(ValueError):
    pass


class Regime(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    SIDEWAYS = "SIDEWAYS"


@dataclass(frozen=True, slots=True)
class RegimeEstimate:
    as_of_index: int
    probabilities: dict[Regime, Decimal]
    uncertainty: Decimal

    def __post_init__(self) -> None:
        expected = set(Regime)
        if set(self.probabilities) != expected or any(value < 0 or value > 1 for value in self.probabilities.values()):
            raise RegimeValidationError("invalid_regime_probabilities")
        if sum(self.probabilities.values(), Decimal("0")) != Decimal("1"):
            raise RegimeValidationError("regime_probabilities_must_sum_to_one")
        if self.uncertainty != Decimal("1") - max(self.probabilities.values()):
            raise RegimeValidationError("inconsistent_regime_uncertainty")


def estimate_regime(closes: list[Decimal], lookback: int) -> RegimeEstimate:
    """Uses only the supplied prefix. Probability rises linearly with its observed directional return."""
    if lookback < 2 or len(closes) < lookback or any(value <= 0 for value in closes):
        raise RegimeValidationError("invalid_regime_history")
    observed_return = closes[-1] / closes[-lookback] - Decimal("1")
    directional_confidence = min(abs(observed_return) / Decimal("0.10"), Decimal("0.40"))
    if observed_return > 0:
        probabilities = {Regime.BULLISH: Decimal("0.40") + directional_confidence, Regime.BEARISH: Decimal("0.20"), Regime.SIDEWAYS: Decimal("0.40") - directional_confidence}
    elif observed_return < 0:
        probabilities = {Regime.BULLISH: Decimal("0.20"), Regime.BEARISH: Decimal("0.40") + directional_confidence, Regime.SIDEWAYS: Decimal("0.40") - directional_confidence}
    else:
        probabilities = {Regime.BULLISH: Decimal("0.20"), Regime.BEARISH: Decimal("0.20"), Regime.SIDEWAYS: Decimal("0.60")}
    return RegimeEstimate(len(closes) - 1, probabilities, Decimal("1") - max(probabilities.values()))


def walk_forward_estimates(closes: list[Decimal], lookback: int) -> list[RegimeEstimate]:
    if len(closes) < lookback:
        raise RegimeValidationError("insufficient_history_for_walk_forward")
    return [estimate_regime(closes[:index + 1], lookback) for index in range(lookback - 1, len(closes))]


@dataclass(frozen=True, slots=True)
class StrategyRegimeProfile:
    strategy_id: str
    expected_return_by_regime: dict[Regime, Decimal]

    def __post_init__(self) -> None:
        if not self.strategy_id or set(self.expected_return_by_regime) != set(Regime):
            raise RegimeValidationError("invalid_strategy_regime_profile")


@dataclass(frozen=True, slots=True)
class EnsembleAllocation:
    weights: dict[str, Decimal]
    expected_scores: dict[str, Decimal]
    regime: RegimeEstimate


def allocate_ensemble(regime: RegimeEstimate, profiles: list[StrategyRegimeProfile], maximum_weight: Decimal = Decimal("0.5")) -> EnsembleAllocation:
    if not profiles or maximum_weight <= 0 or maximum_weight > 1 or len({profile.strategy_id for profile in profiles}) != len(profiles):
        raise RegimeValidationError("invalid_ensemble_inputs")
    scores = {profile.strategy_id: sum((regime.probabilities[key] * value for key, value in profile.expected_return_by_regime.items()), Decimal("0")) for profile in profiles}
    positive = {name: score for name, score in scores.items() if score > 0}
    if not positive:
        raise RegimeValidationError("no_positive_ensemble_score")
    total = sum(positive.values(), Decimal("0"))
    weights = {name: score / total for name, score in positive.items()}
    if any(weight > maximum_weight for weight in weights.values()):
        raise RegimeValidationError("ensemble_weight_exceeds_maximum")
    return EnsembleAllocation(weights, scores, regime)
