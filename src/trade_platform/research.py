"""Deterministic research primitives with explicit no-look-ahead timing semantics."""

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import sqrt
from statistics import mean, pstdev
from pathlib import Path
from uuid import UUID, uuid4

from .domain import utc_now


class ResearchValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    name: str
    version: str
    lookback: int
    timestamp_semantics: str
    missing_value_policy: str
    leakage_test: str


class FeatureRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], FeatureDefinition] = {}

    def register(self, definition: FeatureDefinition) -> None:
        key = (definition.name, definition.version)
        if definition.lookback < 1 or not definition.timestamp_semantics or key in self._definitions:
            raise ResearchValidationError("invalid_or_duplicate_feature_definition")
        self._definitions[key] = definition

    def get(self, name: str, version: str) -> FeatureDefinition:
        return self._definitions[(name, version)]


def simple_moving_average(closes: list[Decimal], lookback: int) -> list[Decimal | None]:
    if lookback < 1:
        raise ResearchValidationError("lookback_must_be_positive")
    values: list[Decimal | None] = []
    for index in range(len(closes)):
        if index + 1 < lookback:
            values.append(None)
        else:
            values.append(sum(closes[index + 1 - lookback : index + 1], Decimal("0")) / Decimal(lookback))
    return values


@dataclass(frozen=True, slots=True)
class MovingAverageCrossStrategy:
    version: str
    fast_window: int
    slow_window: int

    def signals(self, closes: list[Decimal]) -> list[Decimal]:
        if self.fast_window >= self.slow_window:
            raise ResearchValidationError("fast_window_must_be_less_than_slow_window")
        fast, slow = simple_moving_average(closes, self.fast_window), simple_moving_average(closes, self.slow_window)
        return [Decimal("0") if a is None or b is None else Decimal("1") if a > b else Decimal("0") for a, b in zip(fast, slow)]


@dataclass(frozen=True, slots=True)
class BreakoutStrategy:
    version: str
    lookback: int

    def signals(self, closes: list[Decimal]) -> list[Decimal]:
        if self.lookback < 1:
            raise ResearchValidationError("lookback_must_be_positive")
        result: list[Decimal] = []
        for index, close in enumerate(closes):
            prior = closes[max(0, index - self.lookback):index]
            result.append(Decimal("1") if len(prior) == self.lookback and close > max(prior) else Decimal("0"))
        return result


@dataclass(frozen=True, slots=True)
class MeanReversionStrategy:
    version: str
    lookback: int
    entry_zscore: Decimal

    def signals(self, closes: list[Decimal]) -> list[Decimal]:
        averages = simple_moving_average(closes, self.lookback)
        signals: list[Decimal] = []
        for close, average in zip(closes, averages):
            if average is None or average <= 0:
                signals.append(Decimal("0"))
            else:
                deviation = (close - average) / average
                signals.append(Decimal("1") if deviation <= -self.entry_zscore else Decimal("0"))
        return signals


@dataclass(frozen=True, slots=True)
class MomentumStrategy:
    version: str
    lookback: int

    def signals(self, closes: list[Decimal]) -> list[Decimal]:
        if self.lookback < 1:
            raise ResearchValidationError("lookback_must_be_positive")
        return [Decimal("0") if index < self.lookback else Decimal("1") if close > closes[index - self.lookback] else Decimal("0") for index, close in enumerate(closes)]


@dataclass(frozen=True, slots=True)
class CostModel:
    fixed_per_turnover: Decimal = Decimal("0")
    percentage_per_turnover: Decimal = Decimal("0")
    spread_fraction_per_turnover: Decimal = Decimal("0")

    def cost(self, turnover: Decimal) -> Decimal:
        if turnover < 0:
            raise ResearchValidationError("negative_turnover")
        return (self.fixed_per_turnover if turnover else Decimal("0")) + turnover * (
            self.percentage_per_turnover + self.spread_fraction_per_turnover
        )


@dataclass(frozen=True, slots=True)
class BacktestResult:
    period_returns: tuple[Decimal, ...]
    equity_curve: tuple[Decimal, ...]
    total_return: Decimal
    max_drawdown: Decimal
    sharpe: Decimal | None
    turnover: Decimal


@dataclass(frozen=True, slots=True)
class WalkForwardProtocol:
    """Explicit non-overlapping held-out evaluation geometry for one launch."""

    train_size: int
    validation_size: int
    test_size: int
    step: int
    purge: int = 0
    embargo: int = 0

    def validate(self) -> None:
        if min(self.train_size, self.validation_size, self.test_size, self.step) < 1 or self.purge < 0 or self.embargo < 0:
            raise ResearchValidationError("invalid_walk_forward_protocol")
        # A report must not compound duplicate held-out return observations.
        if self.step < self.test_size:
            raise ResearchValidationError("walk_forward_step_overlaps_held_out_periods")


def performance_report(result: BacktestResult, periods_per_year: int = 252) -> dict[str, Decimal | int | None]:
    if periods_per_year < 1:
        raise ResearchValidationError("periods_per_year_must_be_positive")
    periods = len(result.period_returns)
    annualized_return = None if periods == 0 else (Decimal("1") + result.total_return) ** (Decimal(periods_per_year) / Decimal(periods)) - Decimal("1")
    volatility = None
    if len(result.period_returns) > 1:
        volatility = Decimal(str(pstdev([float(value) for value in result.period_returns]) * sqrt(periods_per_year)))
    positive = [value for value in result.period_returns if value > 0]
    negative = [value for value in result.period_returns if value < 0]
    profit_factor = None if not negative else sum(positive, Decimal("0")) / abs(sum(negative, Decimal("0")))
    return {"periods": periods, "total_return": result.total_return, "annualized_return": annualized_return,
            "annualized_volatility": volatility, "sharpe": result.sharpe, "max_drawdown": result.max_drawdown,
            "hit_rate": Decimal(len(positive)) / Decimal(periods) if periods else None,
            "profit_factor": profit_factor, "turnover": result.turnover}


@dataclass(frozen=True, slots=True)
class ResearchExperiment:
    experiment_id: UUID
    strategy_version: str
    dataset_version: str
    feature_versions: tuple[str, ...]
    cost_model_version: str
    parameters: dict[str, object]
    created_at: datetime
    report: dict[str, str | int | None]


class SQLiteExperimentStore:
    """Append-only experiment registry. Parameters/report are serialized only after deterministic run completion."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS research_experiments (
            experiment_id TEXT PRIMARY KEY, strategy_version TEXT NOT NULL, dataset_version TEXT NOT NULL,
            feature_versions_json TEXT NOT NULL, cost_model_version TEXT NOT NULL, parameters_json TEXT NOT NULL,
            created_at TEXT NOT NULL, report_json TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS research_experiment_launches (
            idempotency_key TEXT PRIMARY KEY, payload_digest TEXT NOT NULL, experiment_id TEXT NOT NULL UNIQUE,
            actor TEXT NOT NULL, requested_at TEXT NOT NULL)""")
        self._connection.commit()

    def append(self, strategy_version: str, dataset_version: str, feature_versions: tuple[str, ...],
               cost_model_version: str, parameters: dict[str, object], report: dict[str, Decimal | int | None], *, experiment_id: UUID | None = None) -> ResearchExperiment:
        if not strategy_version or not dataset_version or not cost_model_version:
            raise ResearchValidationError("experiment_requires_versioned_inputs")
        serialized_report = {key: None if value is None else str(value) for key, value in report.items()}
        experiment = ResearchExperiment(experiment_id or uuid4(), strategy_version, dataset_version, feature_versions, cost_model_version,
                                        parameters, utc_now(), serialized_report)
        self._connection.execute("INSERT INTO research_experiments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(experiment.experiment_id), strategy_version, dataset_version, json.dumps(feature_versions), cost_model_version,
             json.dumps(parameters, sort_keys=True), experiment.created_at.isoformat(), json.dumps(serialized_report, sort_keys=True)))
        self._connection.commit()
        return experiment

    def get(self, experiment_id: UUID) -> ResearchExperiment:
        row = self._connection.execute("SELECT * FROM research_experiments WHERE experiment_id = ?", (str(experiment_id),)).fetchone()
        if row is None:
            raise KeyError(str(experiment_id))
        return ResearchExperiment(UUID(row[0]), row[1], row[2], tuple(json.loads(row[3])), row[4], json.loads(row[5]), datetime.fromisoformat(row[6]), json.loads(row[7]))

    def launch(self, *, strategy_version: str, family: str, dataset_version: str, feature_versions: tuple[str, ...], cost_model_version: str, parameters: dict[str, object], closes: tuple[Decimal, ...], costs: CostModel, idempotency_key: str, actor: str, pessimistic_cost_multiplier: Decimal = Decimal("2"), walk_forward_protocol: WalkForwardProtocol | None = None) -> ResearchExperiment:
        if not idempotency_key.strip() or not actor.strip() or not feature_versions:
            raise ResearchValidationError("invalid_backtest_launch")
        cost_inputs = (costs.fixed_per_turnover, costs.percentage_per_turnover, costs.spread_fraction_per_turnover)
        if any(not value.is_finite() or value < 0 for value in cost_inputs) or not pessimistic_cost_multiplier.is_finite() or pessimistic_cost_multiplier < Decimal("1"):
            raise ResearchValidationError("invalid_pessimistic_cost_scenario")
        if walk_forward_protocol is not None:
            walk_forward_protocol.validate()
        protocol_payload = None if walk_forward_protocol is None else {
            "train_size": walk_forward_protocol.train_size, "validation_size": walk_forward_protocol.validation_size,
            "test_size": walk_forward_protocol.test_size, "step": walk_forward_protocol.step,
            "purge": walk_forward_protocol.purge, "embargo": walk_forward_protocol.embargo,
        }
        payload = {"strategy_version": strategy_version, "family": family, "dataset_version": dataset_version, "feature_versions": feature_versions, "cost_model_version": cost_model_version, "parameters": parameters, "closes": [str(item) for item in closes], "costs": [str(value) for value in cost_inputs], "pessimistic_cost_multiplier": str(pessimistic_cost_multiplier), "walk_forward_protocol": protocol_payload}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        existing = self._connection.execute("SELECT payload_digest, experiment_id FROM research_experiment_launches WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if existing is not None:
            if existing[0] != digest: raise ResearchValidationError("backtest_launch_idempotency_conflict")
            return self.get(UUID(existing[1]))
        signals = baseline_signals(family, strategy_version, parameters, list(closes))
        result = run_vectorized_backtest(list(closes), signals, costs)
        report = performance_report(result)
        # Import at call time: validation engines depend on the immutable result
        # type but do not depend on this persistent experiment store.
        from .cross_engine import reconcile_independent_bar_engine
        cross_engine = reconcile_independent_bar_engine(
            result, closes, tuple(signals), fixed_per_turnover=costs.fixed_per_turnover,
            percentage_per_turnover=costs.percentage_per_turnover,
            spread_fraction_per_turnover=costs.spread_fraction_per_turnover,
        )
        report.update({
            "independent_bar_engine_reconciled": int(cross_engine.reconciled),
            "independent_bar_engine_differences": ",".join(cross_engine.differences),
            "independent_bar_engine_final_equity": cross_engine.independent_final_equity,
            "independent_bar_engine_turnover": cross_engine.independent_turnover,
        })
        pessimistic_costs = CostModel(*(value * pessimistic_cost_multiplier for value in cost_inputs))
        pessimistic_report = performance_report(run_vectorized_backtest(list(closes), signals, pessimistic_costs))
        report.update({
            "pessimistic_cost_multiplier": pessimistic_cost_multiplier,
            "pessimistic_total_return": pessimistic_report["total_return"],
            "pessimistic_max_drawdown": pessimistic_report["max_drawdown"],
            "pessimistic_turnover": pessimistic_report["turnover"],
        })
        if walk_forward_protocol is None:
            report.update({"walk_forward_status": "NOT_REQUESTED", "walk_forward_splits": 0, "walk_forward_held_out_periods": 0})
        else:
            from .research_validation import ResearchValidationError as WalkForwardValidationError, run_purged_walk_forward
            from .strategy_validation import StrategyValidationError, purged_walk_forward_splits
            try:
                splits = purged_walk_forward_splits(
                    len(closes), train_size=walk_forward_protocol.train_size,
                    validation_size=walk_forward_protocol.validation_size, test_size=walk_forward_protocol.test_size,
                    step=walk_forward_protocol.step, purge=walk_forward_protocol.purge, embargo=walk_forward_protocol.embargo,
                )
                signal_factory = lambda values: baseline_signals(family, strategy_version, parameters, values)
                held_out = run_purged_walk_forward(list(closes), signal_factory, splits, costs)
            except (StrategyValidationError, WalkForwardValidationError, ValueError) as error:
                raise ResearchValidationError("walk_forward_validation_failed") from error
            held_out_returns = tuple(period for item in held_out for period in item.result.period_returns)
            held_out_equity = Decimal("1")
            for period_return in held_out_returns:
                held_out_equity *= Decimal("1") + period_return
            report.update({
                "walk_forward_status": "EXECUTED", "walk_forward_splits": len(held_out),
                "walk_forward_held_out_periods": len(held_out_returns),
                "walk_forward_total_return": held_out_equity - Decimal("1"),
                "walk_forward_protocol": json.dumps(protocol_payload, sort_keys=True, separators=(",", ":")),
            })
        experiment = self.append(strategy_version, dataset_version, feature_versions, cost_model_version, parameters, report)
        try:
            self._connection.execute("INSERT INTO research_experiment_launches VALUES (?, ?, ?, ?, ?)", (idempotency_key, digest, str(experiment.experiment_id), actor, utc_now().isoformat())); self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise ResearchValidationError("backtest_launch_persistence_failed") from error
        return experiment


def baseline_signals(family: str, version: str, parameters: dict[str, object], closes: list[Decimal]) -> list[Decimal]:
    """Select only transparent baseline implementations for research experiments."""
    try:
        if family == "trend_following": return MovingAverageCrossStrategy(version, int(parameters["fast_window"]), int(parameters["slow_window"])).signals(closes)
        if family == "breakout": return BreakoutStrategy(version, int(parameters["lookback"])).signals(closes)
        if family == "momentum": return MomentumStrategy(version, int(parameters["lookback"])).signals(closes)
        if family == "mean_reversion": return MeanReversionStrategy(version, int(parameters["lookback"]), Decimal(str(parameters["entry_zscore"]))).signals(closes)
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise ResearchValidationError("invalid_baseline_strategy_parameters") from error
    raise ResearchValidationError("unsupported_baseline_strategy_family")


@dataclass(frozen=True, slots=True)
class ChronologicalSplit:
    train_end: int
    validation_end: int
    test_end: int


def chronological_splits(length: int, train_size: int, validation_size: int, test_size: int, step: int) -> list[ChronologicalSplit]:
    if min(train_size, validation_size, test_size, step) < 1:
        raise ResearchValidationError("invalid_split_sizes")
    splits: list[ChronologicalSplit] = []
    train_end = train_size
    while train_end + validation_size + test_size <= length:
        splits.append(ChronologicalSplit(train_end, train_end + validation_size, train_end + validation_size + test_size))
        train_end += step
    if not splits:
        raise ResearchValidationError("insufficient_history_for_split")
    return splits


@dataclass(frozen=True, slots=True)
class StrategyAllocation:
    weights: dict[str, Decimal]


def inverse_risk_allocate(volatilities: dict[str, Decimal], maximum_weight: Decimal = Decimal("0.5")) -> StrategyAllocation:
    if not volatilities or maximum_weight <= 0:
        raise ResearchValidationError("invalid_allocation_inputs")
    if any(value <= 0 for value in volatilities.values()):
        raise ResearchValidationError("non_positive_strategy_volatility")
    raw = {name: Decimal("1") / volatility for name, volatility in volatilities.items()}
    total = sum(raw.values(), Decimal("0"))
    weights = {name: value / total for name, value in raw.items()}
    # Re-normalize only the unconstrained remainder; rejecting instead avoids concealed concentration.
    if any(weight > maximum_weight for weight in weights.values()):
        raise ResearchValidationError("allocation_exceeds_maximum_weight")
    return StrategyAllocation(weights)


def run_vectorized_backtest(closes: list[Decimal], signals: list[Decimal], costs: CostModel) -> BacktestResult:
    """Signal at t is deliberately applied to t+1 return; it cannot trade on unknown close-to-close data."""
    if len(closes) != len(signals) or len(closes) < 2 or any(price <= 0 for price in closes):
        raise ResearchValidationError("invalid_price_or_signal_series")
    equity, peak, turnover = Decimal("1"), Decimal("1"), Decimal("0")
    returns: list[Decimal] = []
    curve = [equity]
    previous_signal = Decimal("0")
    for index in range(1, len(closes)):
        position = signals[index - 1]
        turnover_step = abs(position - previous_signal)
        turnover += turnover_step
        gross = position * (closes[index] / closes[index - 1] - Decimal("1"))
        net = gross - costs.cost(turnover_step)
        equity *= Decimal("1") + net
        peak = max(peak, equity)
        returns.append(net); curve.append(equity); previous_signal = position
    max_drawdown = min((value / max(curve[: idx + 1]) - Decimal("1") for idx, value in enumerate(curve)), default=Decimal("0"))
    values = [float(value) for value in returns]
    volatility = pstdev(values) if len(values) > 1 else 0.0
    sharpe = None if volatility == 0 else Decimal(str(mean(values) / volatility * sqrt(252)))
    return BacktestResult(tuple(returns), tuple(curve), equity - Decimal("1"), max_drawdown, sharpe, turnover)
