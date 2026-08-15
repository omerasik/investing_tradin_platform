"""Immutable, research-only quantitative validation evidence.

This module deliberately has no dependency on OMS, brokers, signals, or risk
approval.  It evaluates already-produced close-to-close research results and
persists the assumptions needed to reproduce the result.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil, sqrt
from pathlib import Path
from statistics import NormalDist, mean, pstdev
from typing import Any, Iterable
from uuid import UUID, uuid5

from .domain import OrderSide, utc_now
from .event_backtest import (
    EventDrivenBacktester,
    ExecutionAssumptions,
    MarketEvent,
    SimulatedOrderType,
    create_order,
)
from .research import BacktestResult, CostModel, run_vectorized_backtest


class QuantValidationError(ValueError):
    pass


_NAMESPACE = UUID("2deedce0-57ef-49d8-9ec3-d8d4f27b789f")


def _wire(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value):
        return _wire(asdict(value))
    if isinstance(value, dict):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    """Return the versioned manifest's exact UTF-8 serialization.

    Manifest v1 uses JSON with Unicode preserved, lexicographically sorted
    object keys, no insignificant whitespace and _wire conversions for
    UUID/datetime/Decimal values. Arrays retain their declared order.
    """

    return json.dumps(
        _wire(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    artifact_id: UUID
    artifact_version: str
    content_hash: str
    evaluated_at: datetime


def _identity(kind: str, version: str, payload: dict[str, Any]) -> ArtifactIdentity:
    if not kind.strip() or not version.strip():
        raise QuantValidationError("artifact_requires_kind_and_version")
    content_hash = hashlib.sha256(json.dumps(_wire(payload), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ArtifactIdentity(uuid5(_NAMESPACE, f"{kind}:{content_hash}"), version, content_hash, utc_now())


def _metrics(result: BacktestResult, *, periods_per_year: int = 252) -> tuple[Decimal, Decimal | None, Decimal | None, Decimal, Decimal]:
    returns = result.period_returns
    annualized = None if not returns else (Decimal("1") + result.total_return) ** (Decimal(periods_per_year) / Decimal(len(returns))) - Decimal("1")
    downside = [min(float(item), 0.0) for item in returns]
    deviation = sqrt(sum(item * item for item in downside) / len(downside)) if downside else 0.0
    average = float(sum(returns, Decimal("0")) / Decimal(len(returns))) if returns else 0.0
    sortino = None if deviation == 0 else Decimal(str(average / deviation * sqrt(periods_per_year)))
    execution_cost = sum((abs(result.period_returns[index] - (Decimal("0"))) for index in range(0)), Decimal("0"))
    # Cost is not recoverable from BacktestResult.  Every caller passes its
    # explicit model and computes it from turnover below.
    return result.total_return, annualized, result.sharpe, sortino, result.max_drawdown


@dataclass(frozen=True, slots=True)
class CapacityLevel:
    capital_level: Decimal
    turnover: Decimal
    average_order_size: Decimal
    maximum_observed_order_size: Decimal
    participation_rate: Decimal
    expected_fill_percentage: Decimal
    unfilled_quantity: Decimal
    estimated_market_impact: Decimal
    post_impact_return: Decimal
    post_impact_sharpe: Decimal | None
    post_impact_drawdown: Decimal
    estimated_maximum_usable_capital: Decimal
    passed: bool


@dataclass(frozen=True, slots=True)
class CapacityEvidence:
    identity: ArtifactIdentity
    strategy_version: str
    dataset_version: str
    instrument_or_universe: str
    average_daily_volume: Decimal
    average_traded_volume: Decimal
    liquidity_assumptions: str
    order_participation_limit: Decimal
    spread_assumption: Decimal
    spread_deterioration_assumption: Decimal
    market_impact_model_version: str
    data_quality: str
    levels: tuple[CapacityLevel, ...]
    limitations: tuple[str, ...]


def evaluate_capacity(*, strategy_version: str, dataset_version: str, instrument_or_universe: str,
                      closes: tuple[Decimal, ...], signals: tuple[Decimal, ...], base_costs: CostModel,
                      capital_levels: tuple[Decimal, ...], average_daily_volume: Decimal,
                      order_participation_limit: Decimal, spread_assumption: Decimal,
                      spread_deterioration_assumption: Decimal, impact_coefficient: Decimal,
                      market_impact_model_version: str, liquidity_assumptions: str,
                      artifact_version: str = "capacity-v1") -> CapacityEvidence:
    """Estimate capacity from OHLCV.  It never claims order-book precision."""
    if (not strategy_version.strip() or not dataset_version.strip() or not instrument_or_universe.strip()
            or len(closes) < 2 or len(closes) != len(signals) or not capital_levels
            or average_daily_volume <= 0 or not Decimal("0") < order_participation_limit <= Decimal("1")
            or any(value < 0 for value in (spread_assumption, spread_deterioration_assumption, impact_coefficient))
            or not market_impact_model_version.strip() or not liquidity_assumptions.strip()):
        raise QuantValidationError("invalid_capacity_inputs")
    if any(level <= 0 for level in capital_levels) or tuple(sorted(set(capital_levels))) != capital_levels:
        raise QuantValidationError("capacity_levels_must_be_positive_sorted_unique")
    average_price = sum(closes, Decimal("0")) / Decimal(len(closes))
    adv_notional = average_daily_volume * average_price
    turn_steps = tuple(abs(signals[index - 1] - (signals[index - 2] if index > 1 else Decimal("0"))) for index in range(1, len(signals)))
    max_turn = max(turn_steps, default=Decimal("0"))
    average_turn = sum(turn_steps, Decimal("0")) / Decimal(len(turn_steps))
    levels: list[CapacityLevel] = []
    for capital in capital_levels:
        average_order = capital * average_turn
        max_order = capital * max_turn
        participation = Decimal("0") if adv_notional == 0 else max_order / adv_notional
        fill = Decimal("1") if participation <= order_participation_limit else order_participation_limit / participation
        unfilled = max(Decimal("0"), max_order - adv_notional * order_participation_limit) / average_price
        impact = impact_coefficient * Decimal(str(sqrt(float(max(participation, Decimal("0"))))))
        extra_cost = spread_assumption + spread_deterioration_assumption * participation + impact
        result = run_vectorized_backtest(list(closes), list(signals), CostModel(
            base_costs.fixed_per_turnover, base_costs.percentage_per_turnover + extra_cost,
            base_costs.spread_fraction_per_turnover,
        ))
        usable = adv_notional * order_participation_limit / max(max_turn, Decimal("0.00000001"))
        levels.append(CapacityLevel(capital, result.turnover, average_order, max_order, participation, fill,
                                    unfilled, impact, result.total_return, result.sharpe, result.max_drawdown,
                                    usable, participation <= order_participation_limit and fill == Decimal("1")))
    payload = {"strategy_version": strategy_version, "dataset_version": dataset_version, "instrument_or_universe": instrument_or_universe,
               "average_daily_volume": average_daily_volume, "average_traded_volume": average_daily_volume,
               "liquidity_assumptions": liquidity_assumptions, "order_participation_limit": order_participation_limit,
               "spread_assumption": spread_assumption, "spread_deterioration_assumption": spread_deterioration_assumption,
               "market_impact_model_version": market_impact_model_version, "data_quality": "OHLCV_ESTIMATE", "levels": levels,
               "limitations": ("OHLCV-only capacity estimate; no order-book or queue-priority precision.",)}
    return CapacityEvidence(_identity("capacity", artifact_version, payload), **payload)


@dataclass(frozen=True, slots=True)
class SlippageScenario:
    name: str
    slippage_multiplier: Decimal
    spread_multiplier: Decimal
    impact_multiplier: Decimal
    total_return: Decimal
    annualized_return: Decimal | None
    sharpe: Decimal | None
    sortino: Decimal | None
    maximum_drawdown: Decimal
    turnover: Decimal
    total_execution_cost: Decimal
    degradation_from_baseline: Decimal
    meets_economic_threshold: bool


@dataclass(frozen=True, slots=True)
class SlippageSensitivityEvidence:
    identity: ArtifactIdentity
    strategy_version: str
    dataset_version: str
    cost_model_version: str
    minimum_economic_return: Decimal
    scenarios: tuple[SlippageScenario, ...]
    passed: bool
    limitations: tuple[str, ...]


def evaluate_slippage_sensitivity(*, strategy_version: str, dataset_version: str, cost_model_version: str,
                                  closes: tuple[Decimal, ...], signals: tuple[Decimal, ...], costs: CostModel,
                                  minimum_economic_return: Decimal = Decimal("0"), artifact_version: str = "slippage-v1") -> SlippageSensitivityEvidence:
    if not all(item.strip() for item in (strategy_version, dataset_version, cost_model_version)) or len(closes) < 2:
        raise QuantValidationError("invalid_slippage_inputs")
    definitions = (("base", Decimal("1"), Decimal("1"), Decimal("1")), ("slippage_1_5x", Decimal("1.5"), Decimal("1"), Decimal("1")),
                   ("slippage_2x", Decimal("2"), Decimal("1"), Decimal("1")), ("slippage_3x", Decimal("3"), Decimal("1"), Decimal("1")),
                   ("wider_spread", Decimal("1"), Decimal("2"), Decimal("1")), ("higher_market_impact", Decimal("1"), Decimal("1"), Decimal("2")))
    results: list[SlippageScenario] = []
    baseline: Decimal | None = None
    for name, slip, spread, impact in definitions:
        model = CostModel(costs.fixed_per_turnover * slip, costs.percentage_per_turnover * slip * impact,
                          costs.spread_fraction_per_turnover * spread)
        result = run_vectorized_backtest(list(closes), list(signals), model)
        total_cost = sum((model.cost(abs(signals[index - 1] - (signals[index - 2] if index > 1 else Decimal("0")))) for index in range(1, len(signals))), Decimal("0"))
        total, annualized, sharpe, sortino, drawdown = _metrics(result)
        if baseline is None:
            baseline = total
        results.append(SlippageScenario(name, slip, spread, impact, total, annualized, sharpe, sortino, drawdown,
                                        result.turnover, total_cost, total - baseline, total >= minimum_economic_return))
    payload = {"strategy_version": strategy_version, "dataset_version": dataset_version, "cost_model_version": cost_model_version,
               "minimum_economic_return": minimum_economic_return, "scenarios": results,
               "passed": all(item.meets_economic_threshold for item in results),
               "limitations": ("Execution costs are model assumptions, not observed broker fills.",)}
    return SlippageSensitivityEvidence(_identity("slippage", artifact_version, payload), **payload)


@dataclass(frozen=True, slots=True)
class LatencyScenario:
    latency: timedelta
    signal_timestamp: datetime
    executable_timestamp: datetime | None
    delayed_entry_price: Decimal | None
    missed_fills: int
    changed_fills: int
    fill_percentage: Decimal
    return_degradation: Decimal
    sharpe_degradation: Decimal | None
    drawdown_degradation: Decimal
    signal_decay_estimate: Decimal
    passed: bool


@dataclass(frozen=True, slots=True)
class LatencySensitivityEvidence:
    identity: ArtifactIdentity
    strategy_version: str
    dataset_version: str
    data_frequency: timedelta
    baseline_total_return: Decimal
    scenarios: tuple[LatencyScenario, ...]
    limitations: tuple[str, ...]


def evaluate_latency_sensitivity(*, strategy_version: str, dataset_version: str, closes: tuple[Decimal, ...],
                                 signals: tuple[Decimal, ...], timestamps: tuple[datetime, ...], costs: CostModel,
                                 data_frequency: timedelta, latency_levels: tuple[timedelta, ...],
                                 maximum_return_degradation: Decimal, artifact_version: str = "latency-v1") -> LatencySensitivityEvidence:
    if (not strategy_version.strip() or not dataset_version.strip() or len(closes) < 2 or len(closes) != len(signals) != len(timestamps)
            or data_frequency <= timedelta(0) or not latency_levels or any(item < timedelta(0) for item in latency_levels)):
        raise QuantValidationError("invalid_latency_inputs")
    if any(timestamps[index] >= timestamps[index + 1] for index in range(len(timestamps) - 1)):
        raise QuantValidationError("latency_timestamps_must_be_ordered")
    baseline = run_vectorized_backtest(list(closes), list(signals), costs)
    first_signal_index = next((index for index, item in enumerate(signals) if item != 0), 0)

    def event_fills(latency: timedelta) -> tuple[int, int, Decimal | None]:
        """Use the independent event engine for actual fill/miss observations."""
        assumptions = ExecutionAssumptions(latency=latency, maximum_participation=Decimal("1"),
            commission_bps=(costs.percentage_per_turnover + costs.spread_fraction_per_turnover) * Decimal("10000"),
            fixed_fee=costs.fixed_per_turnover)
        engine = EventDrivenBacktester(assumptions, initial_cash=Decimal("1000000"))
        desired = Decimal("0"); submitted = 0
        for index, (signal, timestamp, price) in enumerate(zip(signals, timestamps, closes)):
            if signal != desired:
                side = OrderSide.BUY if signal > desired else OrderSide.SELL
                engine.submit(create_order("VALIDATION:INSTRUMENT", side, SimulatedOrderType.MARKET, abs(signal - desired), timestamp))
                submitted += 1; desired = signal
            engine.process(MarketEvent("VALIDATION:INSTRUMENT", timestamp, price, price, Decimal("1000000")))
        result = engine.result()
        missed = sum(order.remaining_quantity > 0 for order in result.orders)
        return submitted, missed, None if not result.fills else result.fills[0].price

    base_submitted, _, _ = event_fills(timedelta(0))
    scenarios: list[LatencyScenario] = []
    frequency_seconds = data_frequency.total_seconds()
    for latency in latency_levels:
        bars = int(ceil(latency.total_seconds() / frequency_seconds))
        delayed = [Decimal("0")] * len(signals)
        for index, signal in enumerate(signals):
            if index + bars < len(signals):
                delayed[index + bars] = signal
        result = run_vectorized_backtest(list(closes), delayed, costs)
        submitted, missed, delayed_price = event_fills(latency)
        changed = sum(1 for index in range(len(signals)) if signals[index] != delayed[index])
        fill_pct = Decimal("0") if base_submitted == 0 else Decimal(submitted - missed) / Decimal(base_submitted)
        degradation = baseline.total_return - result.total_return
        sharpe_decay = None if baseline.sharpe is None or result.sharpe is None else baseline.sharpe - result.sharpe
        executable_index = first_signal_index + bars
        scenarios.append(LatencyScenario(latency, timestamps[first_signal_index], timestamps[executable_index] if executable_index < len(timestamps) else None,
                                         delayed_price, missed, changed, fill_pct,
                                         degradation, sharpe_decay, result.max_drawdown - baseline.max_drawdown,
                                         degradation, degradation <= maximum_return_degradation))
    payload = {"strategy_version": strategy_version, "dataset_version": dataset_version, "data_frequency": data_frequency,
               "baseline_total_return": baseline.total_return, "scenarios": scenarios,
               "limitations": ("Latency is evaluated in whole data-frequency bars; no artificial sub-bar precision.",)}
    return LatencySensitivityEvidence(_identity("latency", artifact_version, payload), **payload)


def _quantile(values: Iterable[Decimal], probability: Decimal) -> Decimal:
    ordered = sorted(values)
    if not ordered or not Decimal("0") <= probability <= Decimal("1"):
        raise QuantValidationError("invalid_quantile")
    return ordered[min(len(ordered) - 1, int(float(probability) * (len(ordered) - 1)))]


@dataclass(frozen=True, slots=True)
class BootstrapEvidence:
    identity: ArtifactIdentity
    strategy_version: str
    dataset_version: str
    seed: int
    resamples: int
    return_distribution: tuple[Decimal, ...]
    sharpe_distribution: tuple[Decimal | None, ...]
    drawdown_distribution: tuple[Decimal, ...]
    percentiles: dict[str, Decimal]
    probability_negative_outcome: Decimal
    confidence_interval: tuple[Decimal, Decimal]


def evaluate_bootstrap(*, strategy_version: str, dataset_version: str, period_returns: tuple[Decimal, ...], seed: int,
                       resamples: int, artifact_version: str = "bootstrap-v1") -> BootstrapEvidence:
    if not strategy_version.strip() or not dataset_version.strip() or not period_returns or resamples < 1:
        raise QuantValidationError("invalid_bootstrap_inputs")
    rng = random.Random(seed); returns: list[Decimal] = []; sharpes: list[Decimal | None] = []; drawdowns: list[Decimal] = []
    for _ in range(resamples):
        sample = tuple(rng.choice(period_returns) for _ in period_returns)
        equity = Decimal("1"); curve = [equity]
        for value in sample: equity *= Decimal("1") + value; curve.append(equity)
        returns.append(equity - Decimal("1")); drawdowns.append(min(item / max(curve[: index + 1]) - Decimal("1") for index, item in enumerate(curve)))
        volatility = pstdev([float(value) for value in sample]) if len(sample) > 1 else 0
        sharpes.append(None if volatility == 0 else Decimal(str(mean([float(value) for value in sample]) / volatility * sqrt(252))))
    percentiles = {"p05_return": _quantile(returns, Decimal(".05")), "p50_return": _quantile(returns, Decimal(".5")), "p95_return": _quantile(returns, Decimal(".95")), "p05_drawdown": _quantile(drawdowns, Decimal(".05"))}
    payload = {"strategy_version": strategy_version, "dataset_version": dataset_version, "seed": seed, "resamples": resamples,
               "return_distribution": tuple(returns), "sharpe_distribution": tuple(sharpes), "drawdown_distribution": tuple(drawdowns),
               "percentiles": percentiles, "probability_negative_outcome": Decimal(sum(value < 0 for value in returns)) / Decimal(resamples),
               "confidence_interval": (percentiles["p05_return"], percentiles["p95_return"])}
    return BootstrapEvidence(_identity("bootstrap", artifact_version, payload), **payload)


@dataclass(frozen=True, slots=True)
class MonteCarloEvidence:
    identity: ArtifactIdentity
    strategy_version: str
    dataset_version: str
    seed: int
    simulations: int
    terminal_equity_distribution: tuple[Decimal, ...]
    maximum_drawdown_distribution: tuple[Decimal, ...]
    ruin_or_threshold_breach_probability: Decimal
    downside_percentiles: dict[str, Decimal]
    limitations: tuple[str, ...]


def evaluate_monte_carlo_trade_sequence(*, strategy_version: str, dataset_version: str, trade_returns: tuple[Decimal, ...],
                                        seed: int, simulations: int, ruin_threshold: Decimal = Decimal(".5"),
                                        artifact_version: str = "monte-carlo-v1") -> MonteCarloEvidence:
    if not strategy_version.strip() or not dataset_version.strip() or not trade_returns or simulations < 1 or ruin_threshold <= 0:
        raise QuantValidationError("invalid_monte_carlo_inputs")
    rng = random.Random(seed); terminal: list[Decimal] = []; drawdowns: list[Decimal] = []; breached = 0
    for _ in range(simulations):
        sample = list(trade_returns); rng.shuffle(sample); equity = Decimal("1"); curve = [equity]
        for value in sample: equity *= Decimal("1") + value; curve.append(equity)
        terminal.append(equity); dd = min(item / max(curve[: index + 1]) - Decimal("1") for index, item in enumerate(curve)); drawdowns.append(dd)
        breached += int(min(curve) <= ruin_threshold)
    percentiles = {"p01_terminal_equity": _quantile(terminal, Decimal(".01")), "p05_terminal_equity": _quantile(terminal, Decimal(".05")), "p05_max_drawdown": _quantile(drawdowns, Decimal(".05"))}
    payload = {"strategy_version": strategy_version, "dataset_version": dataset_version, "seed": seed, "simulations": simulations,
               "terminal_equity_distribution": tuple(terminal), "maximum_drawdown_distribution": tuple(drawdowns),
               "ruin_or_threshold_breach_probability": Decimal(breached) / Decimal(simulations), "downside_percentiles": percentiles,
               "limitations": ("Trade-order randomization is path-risk analysis, not a forecast of future market returns.",)}
    return MonteCarloEvidence(_identity("monte_carlo", artifact_version, payload), **payload)


@dataclass(frozen=True, slots=True)
class StressScenario:
    name: str
    return_shock: Decimal
    cost_multiplier: Decimal
    total_return: Decimal
    maximum_drawdown: Decimal
    passed: bool
    assumptions: str


@dataclass(frozen=True, slots=True)
class StressEvidence:
    identity: ArtifactIdentity
    strategy_version: str
    dataset_version: str
    scenarios: tuple[StressScenario, ...]
    limitations: tuple[str, ...]


def evaluate_stress(*, strategy_version: str, dataset_version: str, closes: tuple[Decimal, ...], signals: tuple[Decimal, ...],
                    costs: CostModel, scenarios: tuple[tuple[str, Decimal, Decimal], ...],
                    minimum_return: Decimal, artifact_version: str = "stress-v1") -> StressEvidence:
    if not strategy_version.strip() or not dataset_version.strip() or not scenarios:
        raise QuantValidationError("invalid_stress_inputs")
    reports: list[StressScenario] = []
    for name, shock, multiplier in scenarios:
        if not name.strip() or multiplier < 1:
            raise QuantValidationError("invalid_stress_scenario")
        stressed = list(closes); stressed[-1] = stressed[-1] * (Decimal("1") + shock)
        result = run_vectorized_backtest(stressed, list(signals), CostModel(costs.fixed_per_turnover * multiplier, costs.percentage_per_turnover * multiplier, costs.spread_fraction_per_turnover * multiplier))
        reports.append(StressScenario(name, shock, multiplier, result.total_return, result.max_drawdown, result.total_return >= minimum_return, "Deterministic historical/fixture shock with explicit cost deterioration."))
    payload = {"strategy_version": strategy_version, "dataset_version": dataset_version, "scenarios": reports,
               "limitations": ("Scenarios are reusable deterministic fixtures where full historical regime data is unavailable.",)}
    return StressEvidence(_identity("stress", artifact_version, payload), **payload)


@dataclass(frozen=True, slots=True)
class ParameterResult:
    parameters: tuple[tuple[str, str], ...]
    total_return: Decimal
    sharpe: Decimal | None


@dataclass(frozen=True, slots=True)
class ParameterStabilityEvidence:
    identity: ArtifactIdentity
    strategy_version: str
    dataset_version: str
    tested_parameter_grid: tuple[ParameterResult, ...]
    selected_parameters: tuple[tuple[str, str], ...]
    neighboring_results: tuple[ParameterResult, ...]
    stability_score: Decimal
    plateau_width: int
    narrow_performance_spike: bool
    passed: bool


def evaluate_parameter_stability(*, strategy_version: str, dataset_version: str, results: tuple[ParameterResult, ...],
                                 selected_parameters: tuple[tuple[str, str], ...], minimum_stability: Decimal = Decimal(".6"),
                                 artifact_version: str = "parameter-stability-v1") -> ParameterStabilityEvidence:
    if not strategy_version.strip() or not dataset_version.strip() or not results or selected_parameters not in tuple(item.parameters for item in results):
        raise QuantValidationError("invalid_parameter_stability_inputs")
    selected = next(item for item in results if item.parameters == selected_parameters)
    # Neighbours differ in exactly one declared parameter. This works for sparse grids too.
    neighbours = tuple(item for item in results if item.parameters != selected_parameters and sum(left != right for left, right in zip(item.parameters, selected_parameters)) == 1)
    if not neighbours:
        raise QuantValidationError("parameter_grid_has_no_neighbours")
    plateau = tuple(item for item in results if item.total_return >= selected.total_return * Decimal(".9"))
    score = Decimal(sum(item.total_return >= selected.total_return * Decimal(".75") for item in neighbours)) / Decimal(len(neighbours))
    narrow = len(plateau) <= 1 or score < minimum_stability
    payload = {"strategy_version": strategy_version, "dataset_version": dataset_version, "tested_parameter_grid": results,
               "selected_parameters": selected_parameters, "neighboring_results": neighbours, "stability_score": score,
               "plateau_width": len(plateau), "narrow_performance_spike": narrow, "passed": not narrow}
    return ParameterStabilityEvidence(_identity("parameter_stability", artifact_version, payload), **payload)


@dataclass(frozen=True, slots=True)
class MultipleTestingEvidence:
    identity: ArtifactIdentity
    strategy_version: str
    dataset_version: str
    strategies_tested: int
    parameter_combinations_tested: int
    datasets_or_universes_tested: int
    feature_combinations_tested: int
    rejected_experiments: tuple[str, ...]
    selected_experiments: tuple[str, ...]
    bh_discoveries: tuple[str, ...]
    probabilistic_sharpe: Decimal
    deflated_sharpe_probability: Decimal
    backtest_overfitting_probability: Decimal
    untouched_holdout_passed: bool
    passed: bool


def evaluate_multiple_testing(*, strategy_version: str, dataset_version: str, p_values: dict[str, Decimal], observed_sharpe: Decimal,
                              holdout_passed: bool, rejected_experiments: tuple[str, ...], selected_experiments: tuple[str, ...],
                              parameter_combinations_tested: int, datasets_or_universes_tested: int, feature_combinations_tested: int,
                              oos_results: tuple[bool, ...], artifact_version: str = "multiple-testing-v1") -> MultipleTestingEvidence:
    if not strategy_version.strip() or not dataset_version.strip() or not p_values or min(parameter_combinations_tested, datasets_or_universes_tested, feature_combinations_tested) < 1 or not oos_results:
        raise QuantValidationError("invalid_multiple_testing_evidence_inputs")
    if any(not Decimal("0") <= value <= Decimal("1") for value in p_values.values()):
        raise QuantValidationError("invalid_multiple_testing_p_values")
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0])); total = Decimal(len(ordered)); threshold: Decimal | None = None
    for index, (_, p_value) in enumerate(ordered, 1):
        if p_value <= Decimal(".05") * Decimal(index) / total: threshold = p_value
    discoveries = tuple(name for name, p in ordered if threshold is not None and p <= threshold)
    observations = max(2, len(oos_results)); probabilistic = Decimal(str(NormalDist().cdf(float(observed_sharpe * Decimal(observations - 1).sqrt()))))
    # Expected maximum Sharpe under independent Gaussian null trials; this is an
    # explicitly conservative diagnostic, not a magic correction.
    trial_count = max(1, len(p_values) * parameter_combinations_tested * feature_combinations_tested)
    expected_max = Decimal(str(sqrt(2 * __import__("math").log(trial_count)))) if trial_count > 1 else Decimal("0")
    deflated = Decimal(str(NormalDist().cdf(float((observed_sharpe - expected_max) * Decimal(observations - 1).sqrt()))))
    pbo = Decimal(sum(not item for item in oos_results)) / Decimal(len(oos_results))
    payload = {"strategy_version": strategy_version, "dataset_version": dataset_version, "strategies_tested": len(p_values),
               "parameter_combinations_tested": parameter_combinations_tested, "datasets_or_universes_tested": datasets_or_universes_tested,
               "feature_combinations_tested": feature_combinations_tested, "rejected_experiments": rejected_experiments,
               "selected_experiments": selected_experiments, "bh_discoveries": discoveries, "probabilistic_sharpe": probabilistic,
               "deflated_sharpe_probability": deflated, "backtest_overfitting_probability": pbo, "untouched_holdout_passed": holdout_passed,
               "passed": strategy_version in discoveries and holdout_passed and pbo <= Decimal(".5") and deflated >= Decimal(".5")}
    return MultipleTestingEvidence(_identity("multiple_testing", artifact_version, payload), **payload)


@dataclass(frozen=True, slots=True)
class ExternalEvidence:
    """Typed reference for evidence evaluated by another bounded service."""
    identity: ArtifactIdentity
    evidence_type: str
    strategy_version: str
    dataset_version: str
    passed: bool
    limitations: tuple[str, ...]


def record_external_evidence(*, evidence_type: str, strategy_version: str, dataset_version: str, passed: bool,
                             limitations: tuple[str, ...] = (), artifact_version: str = "external-v1") -> ExternalEvidence:
    if not all(item.strip() for item in (evidence_type, strategy_version, dataset_version)):
        raise QuantValidationError("invalid_external_evidence")
    payload = {"evidence_type": evidence_type, "strategy_version": strategy_version, "dataset_version": dataset_version, "passed": passed, "limitations": limitations}
    return ExternalEvidence(_identity(evidence_type, artifact_version, payload), **payload)


@dataclass(frozen=True, slots=True)
class StrategyValidationPackage:
    identity: ArtifactIdentity
    manifest_version: str
    canonical_manifest: str
    strategy_id: UUID
    strategy_version_id: UUID
    strategy_version: str
    dataset_id: UUID
    dataset_version_id: UUID
    dataset_version: str
    feature_versions: tuple[str, ...]
    cost_model_version: str
    evidence_ids: dict[str, UUID]
    evidence_hashes: dict[str, str]
    limitations: tuple[str, ...]
    promotion_status: str
    validation_metadata: dict[str, Any] = field(default_factory=dict)


REQUIRED_EVIDENCE = ("data_quality", "oos_walk_forward", "golden_reconciliation", "execution_realism", "capacity", "slippage", "latency", "bootstrap", "monte_carlo", "stress", "parameter_stability", "multiple_testing", "scorecard")
VALIDATION_PACKAGE_MANIFEST_VERSION = "validation-package-manifest-v1"


def _validation_package_manifest(
    *,
    package_id: UUID,
    strategy_id: UUID,
    strategy_version_id: UUID,
    strategy_version: str,
    dataset_id: UUID,
    dataset_version_id: UUID,
    dataset_version: str,
    feature_versions: tuple[str, ...],
    cost_model_version: str,
    evidence_ids: dict[str, UUID],
    evidence_hashes: dict[str, str],
    limitations: tuple[str, ...],
    evaluated_at: datetime,
    promotion_status: str,
    validation_metadata: dict[str, Any],
    manifest_version: str,
) -> dict[str, Any]:
    return {
        "manifest_version": manifest_version,
        "package_id": package_id,
        "strategy": {
            "strategy_id": strategy_id,
            "strategy_version_id": strategy_version_id,
            "version": strategy_version,
        },
        "dataset": {
            "dataset_id": dataset_id,
            "dataset_version_id": dataset_version_id,
            "version": dataset_version,
        },
        "feature_versions": feature_versions,
        "cost_model_version": cost_model_version,
        "evidence": {
            evidence_type: {
                "artifact_id": artifact_id,
                "content_hash": evidence_hashes[evidence_type],
            }
            for evidence_type, artifact_id in evidence_ids.items()
        },
        "limitations": limitations,
        "evaluated_at": evaluated_at,
        "promotion_eligibility_state": promotion_status,
        "validation_metadata": validation_metadata,
    }


def _package_semantic_id(
    *,
    strategy_id: UUID,
    strategy_version_id: UUID,
    strategy_version: str,
    dataset_id: UUID,
    dataset_version_id: UUID,
    dataset_version: str,
    feature_versions: tuple[str, ...],
    cost_model_version: str,
    evidence_ids: dict[str, UUID],
    evidence_hashes: dict[str, str],
    limitations: tuple[str, ...],
    promotion_status: str,
    validation_metadata: dict[str, Any],
    manifest_version: str,
) -> UUID:
    semantic_payload = {
        "manifest_version": manifest_version,
        "strategy_id": strategy_id,
        "strategy_version_id": strategy_version_id,
        "strategy_version": strategy_version,
        "dataset_id": dataset_id,
        "dataset_version_id": dataset_version_id,
        "dataset_version": dataset_version,
        "feature_versions": feature_versions,
        "cost_model_version": cost_model_version,
        "evidence_ids": evidence_ids,
        "evidence_hashes": evidence_hashes,
        "limitations": limitations,
        "promotion_status": promotion_status,
        "validation_metadata": validation_metadata,
    }
    semantic_hash = hashlib.sha256(_canonical_json(semantic_payload).encode("utf-8")).hexdigest()
    return uuid5(_NAMESPACE, f"validation_package_manifest:{semantic_hash}")


def verify_validation_package(package: StrategyValidationPackage) -> dict[str, Any]:
    """Verify exact manifest bytes and their complete semantic projection."""

    if package.manifest_version != VALIDATION_PACKAGE_MANIFEST_VERSION:
        raise QuantValidationError("unsupported_validation_package_manifest_version")
    try:
        parsed = json.loads(package.canonical_manifest)
    except (TypeError, json.JSONDecodeError) as error:
        raise QuantValidationError("invalid_validation_package_manifest_json") from error
    if _canonical_json(parsed) != package.canonical_manifest:
        raise QuantValidationError("validation_package_manifest_not_canonical")
    actual_hash = hashlib.sha256(package.canonical_manifest.encode("utf-8")).hexdigest()
    if package.identity.content_hash != actual_hash:
        raise QuantValidationError("validation_package_content_hash_mismatch")
    if package.identity.artifact_id != _package_semantic_id(
        strategy_id=package.strategy_id,
        strategy_version_id=package.strategy_version_id,
        strategy_version=package.strategy_version,
        dataset_id=package.dataset_id,
        dataset_version_id=package.dataset_version_id,
        dataset_version=package.dataset_version,
        feature_versions=package.feature_versions,
        cost_model_version=package.cost_model_version,
        evidence_ids=package.evidence_ids,
        evidence_hashes=package.evidence_hashes,
        limitations=package.limitations,
        promotion_status=package.promotion_status,
        validation_metadata=package.validation_metadata,
        manifest_version=package.manifest_version,
    ):
        raise QuantValidationError("validation_package_identity_mismatch")
    expected = _validation_package_manifest(
        package_id=package.identity.artifact_id,
        strategy_id=package.strategy_id,
        strategy_version_id=package.strategy_version_id,
        strategy_version=package.strategy_version,
        dataset_id=package.dataset_id,
        dataset_version_id=package.dataset_version_id,
        dataset_version=package.dataset_version,
        feature_versions=package.feature_versions,
        cost_model_version=package.cost_model_version,
        evidence_ids=package.evidence_ids,
        evidence_hashes=package.evidence_hashes,
        limitations=package.limitations,
        evaluated_at=package.identity.evaluated_at,
        promotion_status=package.promotion_status,
        validation_metadata=package.validation_metadata,
        manifest_version=package.manifest_version,
    )
    if parsed != _wire(expected):
        raise QuantValidationError("validation_package_manifest_content_mismatch")
    return parsed


def build_validation_package(
    *,
    strategy_id: UUID,
    strategy_version_id: UUID,
    strategy_version: str,
    dataset_id: UUID,
    dataset_version_id: UUID,
    dataset_version: str,
    feature_versions: tuple[str, ...],
    cost_model_version: str,
    evidence_ids: dict[str, UUID],
    evidence_hashes: dict[str, str],
    limitations: tuple[str, ...] = (),
    validation_metadata: dict[str, Any] | None = None,
    evaluated_at: datetime | None = None,
    manifest_version: str = VALIDATION_PACKAGE_MANIFEST_VERSION,
) -> StrategyValidationPackage:
    missing = set(REQUIRED_EVIDENCE) - set(evidence_ids)
    hashes_mismatch = set(evidence_hashes) != set(evidence_ids)
    invalid_hash = any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in evidence_hashes.values()
    )
    if (
        not strategy_version.strip()
        or not dataset_version.strip()
        or not feature_versions
        or len(set(feature_versions)) != len(feature_versions)
        or not cost_model_version.strip()
        or missing
        or hashes_mismatch
        or invalid_hash
        or manifest_version != VALIDATION_PACKAGE_MANIFEST_VERSION
    ):
        raise QuantValidationError("validation_package_requires_complete_evidence")
    evaluated = evaluated_at or utc_now()
    if evaluated.tzinfo is None or evaluated.utcoffset() is None:
        raise QuantValidationError("validation_package_requires_aware_evaluation_time")
    metadata = {} if validation_metadata is None else dict(validation_metadata)
    promotion_status = "REVIEW_REQUIRED_OR_BLOCKED"
    package_id = _package_semantic_id(
        strategy_id=strategy_id,
        strategy_version_id=strategy_version_id,
        strategy_version=strategy_version,
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        dataset_version=dataset_version,
        feature_versions=feature_versions,
        cost_model_version=cost_model_version,
        evidence_ids=evidence_ids,
        evidence_hashes=evidence_hashes,
        limitations=limitations,
        promotion_status=promotion_status,
        validation_metadata=metadata,
        manifest_version=manifest_version,
    )
    manifest = _validation_package_manifest(
        package_id=package_id,
        strategy_id=strategy_id,
        strategy_version_id=strategy_version_id,
        strategy_version=strategy_version,
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        dataset_version=dataset_version,
        feature_versions=feature_versions,
        cost_model_version=cost_model_version,
        evidence_ids=evidence_ids,
        evidence_hashes=evidence_hashes,
        limitations=limitations,
        evaluated_at=evaluated,
        promotion_status=promotion_status,
        validation_metadata=metadata,
        manifest_version=manifest_version,
    )
    canonical_manifest = _canonical_json(manifest)
    identity = ArtifactIdentity(
        package_id,
        manifest_version,
        hashlib.sha256(canonical_manifest.encode("utf-8")).hexdigest(),
        evaluated,
    )
    package = StrategyValidationPackage(
        identity,
        manifest_version,
        canonical_manifest,
        strategy_id,
        strategy_version_id,
        strategy_version,
        dataset_id,
        dataset_version_id,
        dataset_version,
        feature_versions,
        cost_model_version,
        dict(evidence_ids),
        dict(evidence_hashes),
        limitations,
        promotion_status,
        metadata,
    )
    verify_validation_package(package)
    return package


class SQLiteValidationEvidenceStore:
    """Append-only content-addressed evidence storage, queryable by strategy/version."""
    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS validation_artifacts (
            artifact_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE, artifact_type TEXT NOT NULL,
            strategy_version TEXT NOT NULL, dataset_version TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
        self._connection.commit()

    def append(self, artifact: Any) -> UUID:
        identity = artifact.identity
        strategy_version = artifact.strategy_version; dataset_version = artifact.dataset_version
        if isinstance(artifact, StrategyValidationPackage):
            verify_validation_package(artifact)
        payload = _wire(artifact)
        try:
            self._connection.execute("INSERT INTO validation_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(identity.artifact_id), identity.content_hash, type(artifact).__name__, strategy_version, dataset_version,
                 json.dumps(payload, sort_keys=True, separators=(",", ":")), identity.evaluated_at.isoformat()))
        except sqlite3.IntegrityError as error:
            if isinstance(artifact, StrategyValidationPackage):
                existing = self._connection.execute(
                    "SELECT content_hash, artifact_type, payload_json FROM validation_artifacts WHERE artifact_id = ?",
                    (str(identity.artifact_id),),
                ).fetchone()
                expected_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                if existing == (identity.content_hash, type(artifact).__name__, expected_payload):
                    return identity.artifact_id
                raise QuantValidationError("validation_package_idempotency_conflict") from error
            raise QuantValidationError("duplicate_validation_artifact") from error
        self._connection.commit(); return identity.artifact_id

    def get(self, artifact_id: UUID) -> dict[str, Any]:
        row = self._connection.execute("SELECT artifact_type, payload_json FROM validation_artifacts WHERE artifact_id = ?", (str(artifact_id),)).fetchone()
        if row is None: raise KeyError(str(artifact_id))
        payload = json.loads(row[1])
        if row[0] == "StrategyValidationPackage":
            try:
                package = StrategyValidationPackage(
                    ArtifactIdentity(
                        UUID(payload["identity"]["artifact_id"]),
                        str(payload["identity"]["artifact_version"]),
                        str(payload["identity"]["content_hash"]),
                        datetime.fromisoformat(payload["identity"]["evaluated_at"]),
                    ),
                    str(payload["manifest_version"]),
                    str(payload["canonical_manifest"]),
                    UUID(payload["strategy_id"]),
                    UUID(payload["strategy_version_id"]),
                    str(payload["strategy_version"]),
                    UUID(payload["dataset_id"]),
                    UUID(payload["dataset_version_id"]),
                    str(payload["dataset_version"]),
                    tuple(payload["feature_versions"]),
                    str(payload["cost_model_version"]),
                    {key: UUID(value) for key, value in payload["evidence_ids"].items()},
                    {key: str(value) for key, value in payload["evidence_hashes"].items()},
                    tuple(payload["limitations"]),
                    str(payload["promotion_status"]),
                    dict(payload["validation_metadata"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise QuantValidationError("invalid_validation_package_payload") from error
            verify_validation_package(package)
            payload["artifact_type"] = row[0]
            return payload
        identity = payload.get("identity", {})
        candidate = dict(payload); candidate.pop("identity", None)
        actual_hash = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if identity.get("content_hash") != actual_hash:
            raise QuantValidationError("validation_artifact_content_hash_mismatch")
        payload["artifact_type"] = row[0]; return payload

    def by_strategy_version(self, strategy_version: str) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute("SELECT artifact_type, payload_json FROM validation_artifacts WHERE strategy_version = ? ORDER BY created_at, artifact_id", (strategy_version,)).fetchall()
        return tuple(dict(json.loads(row[1]), artifact_type=row[0]) for row in rows)

    def close(self) -> None: self._connection.close()
