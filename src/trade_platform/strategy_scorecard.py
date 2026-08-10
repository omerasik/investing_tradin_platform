"""Transparent strategy quality metrics and penalties computed from held-out returns."""

from dataclasses import dataclass
from decimal import Decimal
from math import sqrt
from statistics import mean, pstdev

from .research_validation import WalkForwardResult


class ScorecardError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HeldOutMetrics:
    periods: int
    total_return: Decimal
    annualized_return: Decimal
    annualized_volatility: Decimal | None
    sharpe: Decimal | None
    sortino: Decimal | None
    max_drawdown: Decimal
    average_drawdown: Decimal
    max_drawdown_duration: int
    ulcer_index: Decimal
    value_at_risk: Decimal
    conditional_value_at_risk: Decimal
    tail_ratio: Decimal | None
    hit_rate: Decimal
    payoff_ratio: Decimal | None
    profit_factor: Decimal | None
    expectancy: Decimal
    largest_win: Decimal
    largest_loss: Decimal
    max_consecutive_wins: int
    max_consecutive_losses: int
    turnover: Decimal


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    name: str
    value: Decimal | int | None
    score: Decimal | None
    rationale: str


@dataclass(frozen=True, slots=True)
class StrategyQualityScorecard:
    metrics: HeldOutMetrics
    components: tuple[ScoreComponent, ...]
    aggregate_score: Decimal
    limitations: tuple[str, ...]


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _streaks(returns: tuple[Decimal, ...]) -> tuple[int, int]:
    maximum_wins = maximum_losses = wins = losses = 0
    for period_return in returns:
        if period_return > 0:
            wins += 1; losses = 0
        elif period_return < 0:
            losses += 1; wins = 0
        else:
            wins = losses = 0
        maximum_wins = max(maximum_wins, wins)
        maximum_losses = max(maximum_losses, losses)
    return maximum_wins, maximum_losses


def calculate_held_out_metrics(results: tuple[WalkForwardResult, ...], *, periods_per_year: int = 252, confidence: Decimal = Decimal("0.05")) -> HeldOutMetrics:
    """Calculate disclosed return, drawdown and tail metrics from out-of-sample periods only."""
    if not results or periods_per_year < 1 or not Decimal("0") < confidence < Decimal("1"):
        raise ScorecardError("invalid_held_out_metric_inputs")
    returns = tuple(period for result in results for period in result.result.period_returns)
    if not returns:
        raise ScorecardError("missing_held_out_returns")
    equity = Decimal("1")
    curve = [equity]
    for period_return in returns:
        if period_return <= Decimal("-1"):
            raise ScorecardError("return_below_negative_one")
        equity *= Decimal("1") + period_return
        curve.append(equity)
    drawdowns = tuple(value / max(curve[: index + 1]) - Decimal("1") for index, value in enumerate(curve))
    underwater = current_duration = maximum_duration = 0
    for drawdown in drawdowns:
        if drawdown < 0:
            current_duration += 1; maximum_duration = max(maximum_duration, current_duration); underwater += 1
        else:
            current_duration = 0
    floats = [float(value) for value in returns]
    volatility = None if len(floats) < 2 else _decimal(pstdev(floats) * sqrt(periods_per_year))
    average_return = sum(returns, Decimal("0")) / Decimal(len(returns))
    sharpe = None if volatility in (None, Decimal("0")) else _decimal(float(average_return) / (float(volatility) / sqrt(periods_per_year)) * sqrt(periods_per_year))
    downside = [min(float(value), 0.0) for value in returns]
    downside_deviation = sqrt(sum(value * value for value in downside) / len(downside))
    sortino = None if downside_deviation == 0 else _decimal(float(average_return) / downside_deviation * sqrt(periods_per_year))
    ordered = sorted(returns)
    tail_count = max(1, int(len(ordered) * float(confidence)))
    losses = [value for value in returns if value < 0]
    gains = [value for value in returns if value > 0]
    average_win = None if not gains else sum(gains, Decimal("0")) / Decimal(len(gains))
    average_loss = None if not losses else sum(losses, Decimal("0")) / Decimal(len(losses))
    tail_loss = sum(ordered[:tail_count], Decimal("0")) / Decimal(tail_count)
    upper_tail = sum(ordered[-tail_count:], Decimal("0")) / Decimal(tail_count)
    return HeldOutMetrics(
        periods=len(returns), total_return=equity - Decimal("1"),
        annualized_return=equity ** (Decimal(periods_per_year) / Decimal(len(returns))) - Decimal("1"),
        annualized_volatility=volatility, sharpe=sharpe, sortino=sortino,
        max_drawdown=min(drawdowns), average_drawdown=sum(drawdowns, Decimal("0")) / Decimal(len(drawdowns)),
        max_drawdown_duration=maximum_duration,
        ulcer_index=_decimal(sqrt(mean(float(value * value) for value in drawdowns))),
        value_at_risk=ordered[tail_count - 1], conditional_value_at_risk=tail_loss,
        tail_ratio=None if tail_loss == 0 else upper_tail / abs(tail_loss),
        hit_rate=Decimal(len(gains)) / Decimal(len(returns)),
        payoff_ratio=None if average_loss in (None, Decimal("0")) else average_win / abs(average_loss),
        profit_factor=None if not losses else sum(gains, Decimal("0")) / abs(sum(losses, Decimal("0"))),
        expectancy=average_return, largest_win=max(returns), largest_loss=min(returns),
        max_consecutive_wins=_streaks(returns)[0], max_consecutive_losses=_streaks(returns)[1],
        turnover=sum((result.result.turnover for result in results), Decimal("0")),
    )


def build_scorecard(metrics: HeldOutMetrics, *, probabilistic_sharpe: Decimal, pessimistic_total_return: Decimal | None, data_quality_score: Decimal | None, parameter_count: int) -> StrategyQualityScorecard:
    """Expose each contribution and penalty; aggregate score is only a navigation aid."""
    if not Decimal("0") <= probabilistic_sharpe <= Decimal("1") or parameter_count < 0:
        raise ScorecardError("invalid_scorecard_inputs")
    components = (
        ScoreComponent("out_of_sample_return", metrics.total_return, max(Decimal("0"), min(Decimal("100"), metrics.total_return * Decimal("500"))), "Held-out compounded return."),
        ScoreComponent("out_of_sample_sharpe", metrics.sharpe, None if metrics.sharpe is None else max(Decimal("0"), min(Decimal("100"), metrics.sharpe * Decimal("25") + Decimal("50"))), "Held-out risk-adjusted return."),
        ScoreComponent("drawdown", metrics.max_drawdown, max(Decimal("0"), Decimal("100") + metrics.max_drawdown * Decimal("500")), "Penalty for maximum held-out drawdown."),
        ScoreComponent("statistical_confidence", probabilistic_sharpe, probabilistic_sharpe * Decimal("100"), "Probabilistic-Sharpe diagnostic."),
        ScoreComponent("cost_robustness", pessimistic_total_return, None if pessimistic_total_return is None else max(Decimal("0"), min(Decimal("100"), pessimistic_total_return * Decimal("500"))), "Pessimistic-cost result; absent input is not assumed favorable."),
        ScoreComponent("data_quality", data_quality_score, None if data_quality_score is None else max(Decimal("0"), min(Decimal("100"), data_quality_score * Decimal("100"))), "Explicit source-quality evidence."),
        ScoreComponent("complexity_penalty", parameter_count, max(Decimal("0"), Decimal("100") - Decimal(parameter_count) * Decimal("5")), "Penalty increases with tuned parameter count."),
        ScoreComponent("sample_size_penalty", metrics.periods, min(Decimal("100"), Decimal(metrics.periods) / Decimal("2")), "Small held-out samples receive a lower score."),
        ScoreComponent("turnover_penalty", metrics.turnover, max(Decimal("0"), Decimal("100") - metrics.turnover * Decimal("10")), "High turnover receives a lower score."),
    )
    scored = [item.score for item in components if item.score is not None]
    limitations = tuple(item.name for item in components if item.score is None) + ("No capacity, regime-diversity, correlation, live-consistency, or signal-decay calculation.",)
    return StrategyQualityScorecard(metrics, components, sum(scored, Decimal("0")) / Decimal(len(scored)), limitations)
