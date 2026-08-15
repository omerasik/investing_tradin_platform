"""Out-of-sample evaluation and multiple-testing controls for research only."""

import random
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from statistics import NormalDist

from .research import BacktestResult, CostModel, run_vectorized_backtest
from .research import ResearchValidationError as VectorizedValidationError
from .strategy_validation import PurgedWalkForwardSplit


class ResearchValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    split: PurgedWalkForwardSplit
    result: BacktestResult


def run_purged_walk_forward(closes: list[Decimal], signal_factory: Callable[[list[Decimal]], list[Decimal]], splits: list[PurgedWalkForwardSplit], costs: CostModel) -> list[WalkForwardResult]:
    """Run only each held-out test segment and reject a signal function that uses future prices."""
    if not splits or len(closes) < 2:
        raise ResearchValidationError("missing_walk_forward_inputs")
    results: list[WalkForwardResult] = []
    for split in splits:
        split.validate()
        if split.test_end > len(closes) or split.test_start < 1:
            raise ResearchValidationError("walk_forward_split_outside_history")
        prefix = closes[:split.test_end]
        signals = signal_factory(prefix)
        if len(signals) != len(prefix):
            raise ResearchValidationError("signal_factory_length_mismatch")
        # A causal feature/signal cannot change when unobserved suffix data is appended.
        future_signals = signal_factory(closes)
        if len(future_signals) != len(closes) or signals != future_signals[:split.test_end]:
            raise ResearchValidationError("signal_factory_uses_future_data")
        prices = closes[split.test_start - 1 : split.test_end]
        test_signals = signals[split.test_start - 1 : split.test_end]
        try:
            result = run_vectorized_backtest(prices, test_signals, costs)
        except VectorizedValidationError as error:
            raise ResearchValidationError("invalid_walk_forward_test_segment") from error
        results.append(WalkForwardResult(split, result))
    return results


def bootstrap_total_returns(period_returns: tuple[Decimal, ...], *, simulations: int, seed: int) -> tuple[Decimal, ...]:
    """Deterministic resampling of ordered return observations for uncertainty analysis."""
    if not period_returns or simulations < 1:
        raise ResearchValidationError("invalid_bootstrap_inputs")
    generator = random.Random(seed)  # nosec B311 - reproducible bootstrap, not security randomness
    outcomes: list[Decimal] = []
    for _ in range(simulations):
        equity = Decimal("1")
        for _ in period_returns:
            equity *= Decimal("1") + generator.choice(period_returns)
        outcomes.append(equity - Decimal("1"))
    return tuple(outcomes)


@dataclass(frozen=True, slots=True)
class MultipleTestingResult:
    alpha: Decimal
    tested: int
    discoveries: tuple[str, ...]
    threshold: Decimal | None


def benjamini_hochberg(p_values: dict[str, Decimal], *, alpha: Decimal = Decimal("0.05")) -> MultipleTestingResult:
    """False-discovery control; callers must retain all attempted hypotheses, not only winners."""
    if not p_values or not Decimal("0") < alpha < Decimal("1") or any(not Decimal("0") <= value <= Decimal("1") for value in p_values.values()):
        raise ResearchValidationError("invalid_multiple_testing_inputs")
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    threshold: Decimal | None = None
    tested = Decimal(len(ordered))
    for index, (_, p_value) in enumerate(ordered, start=1):
        if p_value <= alpha * Decimal(index) / tested:
            threshold = p_value
    discoveries = () if threshold is None else tuple(name for name, p_value in ordered if p_value <= threshold)
    return MultipleTestingResult(alpha, len(ordered), discoveries, threshold)


def probabilistic_sharpe_exceeds(observed_sharpe: Decimal, benchmark_sharpe: Decimal, *, observations: int) -> Decimal:
    """Normal approximation used only as one confidence diagnostic, never promotion by itself."""
    if observations < 2:
        raise ResearchValidationError("insufficient_sharpe_observations")
    z_score = float((observed_sharpe - benchmark_sharpe) * Decimal(observations - 1).sqrt())
    return Decimal(str(NormalDist().cdf(z_score)))
