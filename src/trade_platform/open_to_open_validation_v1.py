from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from itertools import combinations
from math import comb, e, log, sqrt
from statistics import NormalDist, median
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .crypto_basis_mean_reversion_v1 import (
    BasisMeanReversionOutcomeV1,
    BasisMeanReversionResearchRunV1,
)
from .research import CostModel
from .signed_price_return_v2 import compute_signed_open_to_open_return
from .tradable_bar_evidence_v2 import AuthoritativeTradableBarSeriesV2

_UTC = timezone.utc
_ONE_BAR_INTERVAL = timedelta(minutes=1)
_EULER_MASCHERONI = 0.5772156649015329
_NAMESPACE = uuid5(NAMESPACE_URL, "trade_platform.open_to_open_validation_v1")

REALIZED_EXIT_DAILY_RETURN_SERIES_KIND = "REALIZED_EXIT_DAILY_RETURN_SERIES_V1"
REALIZED_EXIT_DAILY_RETURN_SERIES_SEMANTIC_VERSION = "1.0.0"
COARSE_1M_GRID_LATENCY_STRESS = "COARSE_1M_GRID_LATENCY_STRESS"
CAPACITY_BLOCKED_STATUS = "BLOCKED"
CAPACITY_BLOCKED_REASON = "MISSING_AUTHORIZED_VOLUME_UNIT_SEMANTICS"
REDUCED_LIQUIDITY_BLOCKED_REASON = "MISSING_AUTHORIZED_VOLUME_UNIT_SEMANTICS"
CSCV_BLOCKS = 8
CSCV_SPLIT_COUNT = comb(8, 4)
CSCV_MINIMUM_OBSERVATIONS_PER_BLOCK = 5
CSCV_MINIMUM_TRIALS = 6
DSR_MINIMUM_OBSERVATIONS = 30
DSR_MINIMUM_TRIALS = 6
NULL_CONTROL_TARGET_RUNS = 999
NULL_CONTROL_MINIMUM_VALID_SHIFTS = 99
_SUPPORTED_BAR_INTERVAL = "1m"
_COST_MULTIPLIERS = (Decimal("1.0"), Decimal("1.5"), Decimal("2.0"), Decimal("3.0"))
_LATENCY_MINUTES = (0, 1, 5, 15)
STATUS_AVAILABLE = "AVAILABLE"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_RECONCILED = "RECONCILED"
STATUS_BLOCKED = "BLOCKED"


class OpenToOpenValidationV1Error(ValueError):
    pass


def _wire(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_wire(item) for item in value]
    return value


def _content_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(_wire(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _identity(kind: str, content_hash: str) -> UUID:
    return uuid5(_NAMESPACE, f"{kind}:{content_hash}")


def _is_canonical_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _bar_series_fingerprint(bar_series: AuthoritativeTradableBarSeriesV2) -> str:
    payload = {
        "dataset_version_id": bar_series.dataset_version_id,
        "instrument_id": bar_series.instrument_id,
        "interval": bar_series.interval,
        "bars": [
            {
                "bar_open_at": bar.bar_open_at,
                "bar_close_at": bar.bar_close_at,
                "open": bar.open,
                "raw_observation_id": bar.raw_observation_id,
                "revision": bar.revision,
            }
            for bar in bar_series.bars
        ],
    }
    return _content_hash(payload)


def _require_matching_bar_series(
    run: BasisMeanReversionResearchRunV1, bar_series: AuthoritativeTradableBarSeriesV2
) -> None:
    bar_series.validate()
    if bar_series.dataset_version_id != run.dataset_version_id:
        raise OpenToOpenValidationV1Error("bar_series_dataset_mismatch")
    if bar_series.instrument_id != run.instrument_id:
        raise OpenToOpenValidationV1Error("bar_series_instrument_mismatch")
    if bar_series.interval != _SUPPORTED_BAR_INTERVAL:
        raise OpenToOpenValidationV1Error("bar_series_interval_not_one_minute")


def _validate_cost_model(cost_model: CostModel) -> None:
    for value in (cost_model.fixed_per_turnover, cost_model.percentage_per_turnover, cost_model.spread_fraction_per_turnover):
        if not value.is_finite() or value < 0:
            raise OpenToOpenValidationV1Error("base_cost_model_component_invalid")


def _verify_base_cost_model_reconciles(run: BasisMeanReversionResearchRunV1, cost_model: CostModel) -> None:
    for trade in run.executed_trades:
        turnover = abs(trade.exposure)
        if cost_model.cost(turnover) != trade.entry_cost or cost_model.cost(turnover) != trade.exit_cost:
            raise OpenToOpenValidationV1Error("base_cost_model_does_not_reconcile_with_run")


def _wire_cost_model(cost_model: CostModel) -> dict[str, Decimal]:
    return {
        "fixed_per_turnover": cost_model.fixed_per_turnover,
        "percentage_per_turnover": cost_model.percentage_per_turnover,
        "spread_fraction_per_turnover": cost_model.spread_fraction_per_turnover,
    }


def _floats(returns: Sequence[Decimal]) -> list[float]:
    return [float(value) for value in returns]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _sample_standard_deviation(values: Sequence[float]) -> float:
    average = _mean(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return sqrt(variance)


def daily_mean(returns: Sequence[Decimal]) -> float | None:
    if not returns:
        return None
    return _mean(_floats(returns))


def sample_standard_deviation(returns: Sequence[Decimal]) -> float | None:
    if len(returns) < 2:
        return None
    return _sample_standard_deviation(_floats(returns))


def non_annualized_daily_sharpe(returns: Sequence[Decimal]) -> float | None:
    if len(returns) < 2:
        return None
    values = _floats(returns)
    deviation = _sample_standard_deviation(values)
    if deviation == 0:
        return None
    return _mean(values) / deviation


def _central_moments(values: Sequence[float]) -> tuple[float, float, float]:
    count = len(values)
    average = _mean(values)
    deviations = [value - average for value in values]
    second = sum(value * value for value in deviations) / count
    third = sum(value ** 3 for value in deviations) / count
    fourth = sum(value ** 4 for value in deviations) / count
    return second, third, fourth


def bias_corrected_sample_skewness(returns: Sequence[Decimal]) -> float | None:
    count = len(returns)
    if count < 4:
        return None
    second, third, _ = _central_moments(_floats(returns))
    if second <= 0:
        return None
    g1 = third / (second ** 1.5)
    return sqrt(count * (count - 1)) / (count - 2) * g1


def bias_corrected_pearson_kurtosis(returns: Sequence[Decimal]) -> float | None:
    count = len(returns)
    if count < 4:
        return None
    second, _, fourth = _central_moments(_floats(returns))
    if second <= 0:
        return None
    g2_excess = fourth / (second ** 2) - 3
    excess_corrected = ((count - 1) / ((count - 2) * (count - 3))) * ((count + 1) * g2_excess + 6)
    return excess_corrected + 3


def trial_sharpe_mean(sharpes: Sequence[float]) -> float | None:
    if not sharpes:
        return None
    return _mean(list(sharpes))


def trial_sharpe_standard_deviation(sharpes: Sequence[float]) -> float | None:
    if len(sharpes) < 2:
        return None
    return _sample_standard_deviation(list(sharpes))


def _require_utc_midnight(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise OpenToOpenValidationV1Error(f"{name}_must_be_utc")
    if (value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0):
        raise OpenToOpenValidationV1Error(f"{name}_must_be_utc_midnight")


def _daily_returns_over_window(
    realized: Iterable[tuple[datetime, Decimal]], window_start: datetime, window_end: datetime
) -> tuple[tuple[date, ...], tuple[Decimal, ...]]:
    buckets: dict[date, list[Decimal]] = {}
    for exit_time, net_return in realized:
        exit_utc = exit_time.astimezone(_UTC)
        if not (window_start <= exit_utc < window_end):
            continue
        buckets.setdefault(exit_utc.date(), []).append(net_return)
    dates: list[date] = []
    daily_returns: list[Decimal] = []
    cursor = window_start
    while cursor < window_end:
        calendar_day = cursor.date()
        compounded = Decimal("1")
        for net_return in buckets.get(calendar_day, ()):
            compounded *= Decimal("1") + net_return
        dates.append(calendar_day)
        daily_returns.append(compounded - Decimal("1"))
        cursor = cursor + timedelta(days=1)
    return tuple(dates), tuple(daily_returns)


@dataclass(frozen=True, slots=True)
class RealizedExitDailyReturnSeriesV1:
    series_kind: str
    semantic_version: str
    source_run_content_hash: str
    dataset_version_id: UUID
    instrument_id: str
    window_start: datetime
    window_end: datetime
    dates: tuple[date, ...]
    daily_returns: tuple[Decimal, ...]
    content_hash: str
    series_id: UUID


def build_realized_exit_daily_return_series_v1(
    *, run: BasisMeanReversionResearchRunV1, window_start: datetime, window_end: datetime
) -> RealizedExitDailyReturnSeriesV1:
    _require_utc_midnight(window_start, "window_start")
    _require_utc_midnight(window_end, "window_end")
    if window_end <= window_start:
        raise OpenToOpenValidationV1Error("window_end_must_be_after_window_start")
    realized = ((trade.exit_time, trade.net_return) for trade in run.executed_trades)
    dates, daily_returns = _daily_returns_over_window(realized, window_start, window_end)
    payload = {
        "series_kind": REALIZED_EXIT_DAILY_RETURN_SERIES_KIND,
        "semantic_version": REALIZED_EXIT_DAILY_RETURN_SERIES_SEMANTIC_VERSION,
        "source_run_content_hash": run.content_hash,
        "dataset_version_id": run.dataset_version_id,
        "instrument_id": run.instrument_id,
        "window_start": window_start,
        "window_end": window_end,
        "dates": dates,
        "daily_returns": daily_returns,
    }
    content_hash = _content_hash(payload)
    return RealizedExitDailyReturnSeriesV1(
        series_kind=REALIZED_EXIT_DAILY_RETURN_SERIES_KIND,
        semantic_version=REALIZED_EXIT_DAILY_RETURN_SERIES_SEMANTIC_VERSION,
        source_run_content_hash=run.content_hash,
        dataset_version_id=run.dataset_version_id,
        instrument_id=run.instrument_id,
        window_start=window_start,
        window_end=window_end,
        dates=dates,
        daily_returns=daily_returns,
        content_hash=content_hash,
        series_id=_identity(REALIZED_EXIT_DAILY_RETURN_SERIES_KIND, content_hash),
    )


@dataclass(frozen=True, slots=True)
class TradeReturnMetricsV1:
    number_of_trades: int
    hit_rate: Decimal | None
    average_trade: Decimal | None
    median_trade: Decimal | None
    win_loss_ratio: Decimal | None
    payoff_ratio: Decimal | None
    profit_factor: Decimal | None
    gains_distribution: tuple[Decimal, ...]
    losses_distribution: tuple[Decimal, ...]
    content_hash: str
    metrics_id: UUID


def trade_return_metrics_v1(trade_returns: tuple[Decimal, ...]) -> TradeReturnMetricsV1:
    for value in trade_returns:
        if not value.is_finite():
            raise OpenToOpenValidationV1Error("trade_return_not_finite")
    count = len(trade_returns)
    gains = tuple(value for value in trade_returns if value > 0)
    losses = tuple(value for value in trade_returns if value < 0)
    hit_rate = None if count == 0 else Decimal(len(gains)) / Decimal(count)
    average_trade = None if count == 0 else sum(trade_returns, Decimal("0")) / Decimal(count)
    median_trade = None if count == 0 else Decimal(str(median(trade_returns)))
    win_loss_ratio = None if not losses else Decimal(len(gains)) / Decimal(len(losses))
    payoff_ratio = (
        None
        if not losses or not gains
        else (sum(gains, Decimal("0")) / Decimal(len(gains))) / abs(sum(losses, Decimal("0")) / Decimal(len(losses)))
    )
    profit_factor = None if not losses else sum(gains, Decimal("0")) / abs(sum(losses, Decimal("0")))
    payload = {
        "trade_returns": trade_returns,
        "number_of_trades": count,
        "hit_rate": hit_rate,
        "average_trade": average_trade,
        "median_trade": median_trade,
        "win_loss_ratio": win_loss_ratio,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,
        "gains_distribution": gains,
        "losses_distribution": losses,
    }
    content_hash = _content_hash(payload)
    return TradeReturnMetricsV1(
        number_of_trades=count,
        hit_rate=hit_rate,
        average_trade=average_trade,
        median_trade=median_trade,
        win_loss_ratio=win_loss_ratio,
        payoff_ratio=payoff_ratio,
        profit_factor=profit_factor,
        gains_distribution=gains,
        losses_distribution=losses,
        content_hash=content_hash,
        metrics_id=_identity("trade-return-metrics-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class _ReplayTrade:
    exposure: Decimal
    entry_time: datetime
    exit_time: datetime
    entry_open: Decimal
    exit_open: Decimal
    gross_return: Decimal
    entry_cost: Decimal
    exit_cost: Decimal
    net_return: Decimal


def _direction(basis_value: Decimal, threshold: Decimal, cap: Decimal) -> Decimal:
    if basis_value > threshold:
        return -cap
    if basis_value < -threshold:
        return cap
    return Decimal("0")


def _replay_open_to_open(
    *,
    decision_inputs: Sequence[tuple[datetime, Decimal]],
    bar_series: AuthoritativeTradableBarSeriesV2,
    cap: Decimal,
    horizon: timedelta,
    cost_model: CostModel,
    latency: timedelta,
    omitted_exit_open_times: frozenset[datetime] = frozenset(),
) -> tuple[tuple[_ReplayTrade, ...], int]:
    trades: list[_ReplayTrade] = []
    excluded = 0
    last_executed_exit_time: datetime | None = None
    for decision_at, exposure in decision_inputs:
        if exposure == 0:
            continue
        if last_executed_exit_time is not None and decision_at <= last_executed_exit_time:
            continue
        entry_bar = bar_series.first_eligible_bar_after(decision_at + latency)
        if entry_bar is None:
            excluded += 1
            continue
        exit_open_at = entry_bar.bar_open_at + horizon
        if exit_open_at in omitted_exit_open_times:
            excluded += 1
            continue
        exit_bar = next((bar for bar in bar_series.bars if bar.bar_open_at == exit_open_at), None)
        if exit_bar is None:
            excluded += 1
            continue
        computed = compute_signed_open_to_open_return(
            entry_bar=entry_bar,
            exit_bar=exit_bar,
            exposure=exposure,
            maximum_absolute_exposure=cap,
            cost_model=cost_model,
        )
        trades.append(
            _ReplayTrade(
                exposure=exposure,
                entry_time=computed.entry_time,
                exit_time=computed.exit_time,
                entry_open=computed.entry_open,
                exit_open=computed.exit_open,
                gross_return=computed.gross_return,
                entry_cost=computed.entry_cost,
                exit_cost=computed.exit_cost,
                net_return=computed.net_return,
            )
        )
        last_executed_exit_time = exit_bar.bar_open_at
    return tuple(trades), excluded


def _decision_inputs(run: BasisMeanReversionResearchRunV1) -> tuple[tuple[datetime, Decimal], ...]:
    return tuple((decision.decision_at, decision.exposure) for decision in run.decisions)


@dataclass(frozen=True, slots=True)
class OpenToOpenTradeCostRecomputationV1:
    gross_return: Decimal
    entry_cost: Decimal
    exit_cost: Decimal
    net_return: Decimal


@dataclass(frozen=True, slots=True)
class OpenToOpenCostScenarioV1:
    cost_multiplier: Decimal
    trades: tuple[OpenToOpenTradeCostRecomputationV1, ...]
    net_returns: tuple[Decimal, ...]


@dataclass(frozen=True, slots=True)
class OpenToOpenCostSensitivityEvidenceV1:
    source_run_content_hash: str
    base_cost_model_version: str
    scenarios: tuple[OpenToOpenCostScenarioV1, ...]
    content_hash: str
    evidence_id: UUID


def _scaled_cost_model(base: CostModel, multiplier: Decimal) -> CostModel:
    return CostModel(
        fixed_per_turnover=base.fixed_per_turnover * multiplier,
        percentage_per_turnover=base.percentage_per_turnover * multiplier,
        spread_fraction_per_turnover=base.spread_fraction_per_turnover * multiplier,
    )


def evaluate_open_to_open_cost_sensitivity_v1(
    *, run: BasisMeanReversionResearchRunV1, base_cost_model: CostModel
) -> OpenToOpenCostSensitivityEvidenceV1:
    _validate_cost_model(base_cost_model)
    cap = run.definition.maximum_absolute_exposure
    scenarios: list[OpenToOpenCostScenarioV1] = []
    for multiplier in _COST_MULTIPLIERS:
        model = _scaled_cost_model(base_cost_model, multiplier)
        recomputations: list[OpenToOpenTradeCostRecomputationV1] = []
        net_returns: list[Decimal] = []
        for trade in run.executed_trades:
            computed = compute_signed_open_to_open_return(
                entry_bar=trade.entry_bar,
                exit_bar=trade.exit_bar,
                exposure=trade.exposure,
                maximum_absolute_exposure=cap,
                cost_model=model,
            )
            if multiplier == Decimal("1.0") and (
                computed.gross_return != trade.gross_return
                or computed.entry_cost != trade.entry_cost
                or computed.exit_cost != trade.exit_cost
                or computed.net_return != trade.net_return
            ):
                raise OpenToOpenValidationV1Error("cost_sensitivity_base_scenario_mismatch")
            recomputations.append(
                OpenToOpenTradeCostRecomputationV1(
                    gross_return=computed.gross_return,
                    entry_cost=computed.entry_cost,
                    exit_cost=computed.exit_cost,
                    net_return=computed.net_return,
                )
            )
            net_returns.append(computed.net_return)
        scenarios.append(OpenToOpenCostScenarioV1(multiplier, tuple(recomputations), tuple(net_returns)))
    payload = {
        "source_run_content_hash": run.content_hash,
        "base_cost_model": _wire_cost_model(base_cost_model),
        "base_cost_model_version": run.cost_model_version,
        "scenarios": [
            {
                "cost_multiplier": scenario.cost_multiplier,
                "trades": [
                    {
                        "gross_return": item.gross_return,
                        "entry_cost": item.entry_cost,
                        "exit_cost": item.exit_cost,
                        "net_return": item.net_return,
                    }
                    for item in scenario.trades
                ],
            }
            for scenario in scenarios
        ],
    }
    content_hash = _content_hash(payload)
    return OpenToOpenCostSensitivityEvidenceV1(
        source_run_content_hash=run.content_hash,
        base_cost_model_version=run.cost_model_version,
        scenarios=tuple(scenarios),
        content_hash=content_hash,
        evidence_id=_identity("open-to-open-cost-sensitivity-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class OpenToOpenLatencyScenarioV1:
    latency_minutes: int
    net_returns: tuple[Decimal, ...]
    gross_returns: tuple[Decimal, ...]
    entry_costs: tuple[Decimal, ...]
    exit_costs: tuple[Decimal, ...]
    executed_count: int
    excluded_count: int


@dataclass(frozen=True, slots=True)
class OpenToOpenLatencySensitivityEvidenceV1:
    evidence_label: str
    source_run_content_hash: str
    bar_series_fingerprint: str
    scenarios: tuple[OpenToOpenLatencyScenarioV1, ...]
    content_hash: str
    evidence_id: UUID


def _verify_zero_latency_reconciles_canonical_run(
    run: BasisMeanReversionResearchRunV1, trades: tuple[_ReplayTrade, ...], excluded: int
) -> None:
    if len(trades) != run.executed_trade_count or excluded != run.excluded_count:
        raise OpenToOpenValidationV1Error("latency_zero_minute_does_not_reconcile_canonical_run")
    for canonical, replay in zip(run.executed_trades, trades, strict=True):
        if (
            canonical.exposure != replay.exposure
            or canonical.entry_time != replay.entry_time
            or canonical.exit_time != replay.exit_time
            or canonical.gross_return != replay.gross_return
            or canonical.entry_cost != replay.entry_cost
            or canonical.exit_cost != replay.exit_cost
            or canonical.net_return != replay.net_return
        ):
            raise OpenToOpenValidationV1Error("latency_zero_minute_does_not_reconcile_canonical_run")


def evaluate_open_to_open_latency_sensitivity_v1(
    *,
    run: BasisMeanReversionResearchRunV1,
    bar_series: AuthoritativeTradableBarSeriesV2,
    base_cost_model: CostModel,
) -> OpenToOpenLatencySensitivityEvidenceV1:
    _require_matching_bar_series(run, bar_series)
    _validate_cost_model(base_cost_model)
    _verify_base_cost_model_reconciles(run, base_cost_model)
    cap = run.definition.maximum_absolute_exposure
    horizon = run.definition.holding_horizon_bars * _ONE_BAR_INTERVAL
    inputs = _decision_inputs(run)
    scenarios: list[OpenToOpenLatencyScenarioV1] = []
    for minutes in _LATENCY_MINUTES:
        trades, excluded = _replay_open_to_open(
            decision_inputs=inputs,
            bar_series=bar_series,
            cap=cap,
            horizon=horizon,
            cost_model=base_cost_model,
            latency=timedelta(minutes=minutes),
        )
        if minutes == 0:
            _verify_zero_latency_reconciles_canonical_run(run, trades, excluded)
        scenarios.append(
            OpenToOpenLatencyScenarioV1(
                latency_minutes=minutes,
                net_returns=tuple(trade.net_return for trade in trades),
                gross_returns=tuple(trade.gross_return for trade in trades),
                entry_costs=tuple(trade.entry_cost for trade in trades),
                exit_costs=tuple(trade.exit_cost for trade in trades),
                executed_count=len(trades),
                excluded_count=excluded,
            )
        )
    bar_fingerprint = _bar_series_fingerprint(bar_series)
    payload = {
        "evidence_label": COARSE_1M_GRID_LATENCY_STRESS,
        "source_run_content_hash": run.content_hash,
        "bar_series_fingerprint": bar_fingerprint,
        "base_cost_model": _wire_cost_model(base_cost_model),
        "scenarios": [
            {
                "latency_minutes": scenario.latency_minutes,
                "net_returns": scenario.net_returns,
                "gross_returns": scenario.gross_returns,
                "entry_costs": scenario.entry_costs,
                "exit_costs": scenario.exit_costs,
                "executed_count": scenario.executed_count,
                "excluded_count": scenario.excluded_count,
            }
            for scenario in scenarios
        ],
    }
    content_hash = _content_hash(payload)
    return OpenToOpenLatencySensitivityEvidenceV1(
        evidence_label=COARSE_1M_GRID_LATENCY_STRESS,
        source_run_content_hash=run.content_hash,
        bar_series_fingerprint=bar_fingerprint,
        scenarios=tuple(scenarios),
        content_hash=content_hash,
        evidence_id=_identity("open-to-open-latency-sensitivity-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class AdverseExitShockScenarioV1:
    shock_magnitude: Decimal
    net_returns: tuple[Decimal, ...]
    gross_returns: tuple[Decimal, ...]


@dataclass(frozen=True, slots=True)
class AdverseExitShockEvidenceV1:
    synthetic_validation_evidence: bool
    source_run_content_hash: str
    scenarios: tuple[AdverseExitShockScenarioV1, ...]
    content_hash: str
    evidence_id: UUID


def evaluate_open_to_open_adverse_exit_shock_v1(
    *,
    run: BasisMeanReversionResearchRunV1,
    base_cost_model: CostModel,
    shock_magnitudes: tuple[Decimal, ...],
) -> AdverseExitShockEvidenceV1:
    _validate_cost_model(base_cost_model)
    _verify_base_cost_model_reconciles(run, base_cost_model)
    if not shock_magnitudes:
        raise OpenToOpenValidationV1Error("adverse_shock_magnitudes_required")
    for magnitude in shock_magnitudes:
        if not magnitude.is_finite() or not (Decimal("0") < magnitude < Decimal("1")):
            raise OpenToOpenValidationV1Error("adverse_shock_magnitude_out_of_bounds")
    scenarios: list[AdverseExitShockScenarioV1] = []
    for magnitude in shock_magnitudes:
        net_returns: list[Decimal] = []
        gross_returns: list[Decimal] = []
        for trade in run.executed_trades:
            if trade.exposure > 0:
                shocked_exit = trade.exit_open * (Decimal("1") - magnitude)
            else:
                shocked_exit = trade.exit_open * (Decimal("1") + magnitude)
            gross = trade.exposure * (shocked_exit / trade.entry_open - Decimal("1"))
            turnover = abs(trade.exposure)
            net = gross - base_cost_model.cost(turnover) - base_cost_model.cost(turnover)
            gross_returns.append(gross)
            net_returns.append(net)
        scenarios.append(AdverseExitShockScenarioV1(magnitude, tuple(net_returns), tuple(gross_returns)))
    payload = {
        "synthetic_validation_evidence": True,
        "source_run_content_hash": run.content_hash,
        "base_cost_model": _wire_cost_model(base_cost_model),
        "scenarios": [
            {
                "shock_magnitude": scenario.shock_magnitude,
                "net_returns": scenario.net_returns,
                "gross_returns": scenario.gross_returns,
            }
            for scenario in scenarios
        ],
    }
    content_hash = _content_hash(payload)
    return AdverseExitShockEvidenceV1(
        synthetic_validation_evidence=True,
        source_run_content_hash=run.content_hash,
        scenarios=tuple(scenarios),
        content_hash=content_hash,
        evidence_id=_identity("adverse-exit-shock-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class MissingBarStressEvidenceV1:
    synthetic_validation_evidence: bool
    source_run_content_hash: str
    bar_series_fingerprint: str
    omitted_exit_bar_open_times: tuple[datetime, ...]
    baseline_executed_count: int
    baseline_excluded_count: int
    stressed_executed_count: int
    stressed_excluded_count: int
    net_returns: tuple[Decimal, ...]
    content_hash: str
    evidence_id: UUID


def evaluate_open_to_open_missing_bar_stress_v1(
    *,
    run: BasisMeanReversionResearchRunV1,
    bar_series: AuthoritativeTradableBarSeriesV2,
    base_cost_model: CostModel,
    omitted_exit_bar_open_times: tuple[datetime, ...],
) -> MissingBarStressEvidenceV1:
    _require_matching_bar_series(run, bar_series)
    _validate_cost_model(base_cost_model)
    _verify_base_cost_model_reconciles(run, base_cost_model)
    if not omitted_exit_bar_open_times:
        raise OpenToOpenValidationV1Error("missing_bar_omissions_required")
    omitted = frozenset(omitted_exit_bar_open_times)
    candidate_timestamps = frozenset(bar.bar_open_at for bar in bar_series.bars)
    if not omitted <= candidate_timestamps:
        raise OpenToOpenValidationV1Error("omitted_exit_bar_timestamp_not_a_candidate_bar")
    cap = run.definition.maximum_absolute_exposure
    horizon = run.definition.holding_horizon_bars * _ONE_BAR_INTERVAL
    inputs = _decision_inputs(run)
    baseline_trades, baseline_excluded = _replay_open_to_open(
        decision_inputs=inputs,
        bar_series=bar_series,
        cap=cap,
        horizon=horizon,
        cost_model=base_cost_model,
        latency=timedelta(0),
    )
    stressed_trades, stressed_excluded = _replay_open_to_open(
        decision_inputs=inputs,
        bar_series=bar_series,
        cap=cap,
        horizon=horizon,
        cost_model=base_cost_model,
        latency=timedelta(0),
        omitted_exit_open_times=omitted,
    )
    ordered_omissions = tuple(sorted(omitted))
    bar_fingerprint = _bar_series_fingerprint(bar_series)
    payload = {
        "synthetic_validation_evidence": True,
        "source_run_content_hash": run.content_hash,
        "bar_series_fingerprint": bar_fingerprint,
        "base_cost_model": _wire_cost_model(base_cost_model),
        "omitted_exit_bar_open_times": ordered_omissions,
        "baseline_executed_count": len(baseline_trades),
        "baseline_excluded_count": baseline_excluded,
        "stressed_executed_count": len(stressed_trades),
        "stressed_excluded_count": stressed_excluded,
        "net_returns": tuple(trade.net_return for trade in stressed_trades),
    }
    content_hash = _content_hash(payload)
    return MissingBarStressEvidenceV1(
        synthetic_validation_evidence=True,
        source_run_content_hash=run.content_hash,
        bar_series_fingerprint=bar_fingerprint,
        omitted_exit_bar_open_times=ordered_omissions,
        baseline_executed_count=len(baseline_trades),
        baseline_excluded_count=baseline_excluded,
        stressed_executed_count=len(stressed_trades),
        stressed_excluded_count=stressed_excluded,
        net_returns=tuple(trade.net_return for trade in stressed_trades),
        content_hash=content_hash,
        evidence_id=_identity("missing-bar-stress-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class CapacityBlockedEvidenceV1:
    status: str
    reason: str
    source_run_content_hash: str
    content_hash: str
    evidence_id: UUID


def build_capacity_blocked_evidence_v1(*, run: BasisMeanReversionResearchRunV1) -> CapacityBlockedEvidenceV1:
    payload = {
        "status": CAPACITY_BLOCKED_STATUS,
        "reason": CAPACITY_BLOCKED_REASON,
        "source_run_content_hash": run.content_hash,
    }
    content_hash = _content_hash(payload)
    return CapacityBlockedEvidenceV1(
        status=CAPACITY_BLOCKED_STATUS,
        reason=CAPACITY_BLOCKED_REASON,
        source_run_content_hash=run.content_hash,
        content_hash=content_hash,
        evidence_id=_identity("capacity-blocked-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class ReducedLiquidityBlockedEvidenceV1:
    status: str
    reason: str
    source_run_content_hash: str
    content_hash: str
    evidence_id: UUID


def build_reduced_liquidity_blocked_evidence_v1(
    *, run: BasisMeanReversionResearchRunV1
) -> ReducedLiquidityBlockedEvidenceV1:
    payload = {
        "status": CAPACITY_BLOCKED_STATUS,
        "reason": REDUCED_LIQUIDITY_BLOCKED_REASON,
        "source_run_content_hash": run.content_hash,
    }
    content_hash = _content_hash(payload)
    return ReducedLiquidityBlockedEvidenceV1(
        status=CAPACITY_BLOCKED_STATUS,
        reason=REDUCED_LIQUIDITY_BLOCKED_REASON,
        source_run_content_hash=run.content_hash,
        content_hash=content_hash,
        evidence_id=_identity("reduced-liquidity-blocked-v1", content_hash),
    )


def canonical_trade_returns_for_monte_carlo(run: BasisMeanReversionResearchRunV1) -> tuple[Decimal, ...]:
    return run.trade_returns


class ResearchTrialRoleV1(StrEnum):
    BASELINE = "BASELINE"
    NEIGHBOR = "NEIGHBOR"
    OTHER = "OTHER"


class ResearchTrialDispositionV1(StrEnum):
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"
    INSPECTED = "INSPECTED"


@dataclass(frozen=True, slots=True)
class ResearchTrialV1:
    trial_role: ResearchTrialRoleV1
    disposition: ResearchTrialDispositionV1
    strategy_definition_content_hash: str
    basis_entry_threshold: Decimal
    holding_horizon_bars: int
    maximum_absolute_exposure: Decimal
    source_run_content_hash: str
    dataset_version_id: UUID
    instrument_id: str
    window_start: datetime
    window_end: datetime
    daily_series_content_hash: str
    daily_returns: tuple[Decimal, ...]
    trial_content_hash: str
    trial_id: UUID


def build_research_trial_v1(
    *,
    run: BasisMeanReversionResearchRunV1,
    series: RealizedExitDailyReturnSeriesV1,
    trial_role: ResearchTrialRoleV1,
    disposition: ResearchTrialDispositionV1,
) -> ResearchTrialV1:
    if series.source_run_content_hash != run.content_hash:
        raise OpenToOpenValidationV1Error("trial_series_run_mismatch")
    if series.dataset_version_id != run.dataset_version_id:
        raise OpenToOpenValidationV1Error("trial_series_dataset_mismatch")
    if series.instrument_id != run.instrument_id:
        raise OpenToOpenValidationV1Error("trial_series_instrument_mismatch")
    definition = run.definition
    definition_content_hash = definition.content_hash()
    identity_payload = {
        "trial_role": trial_role.value,
        "disposition": disposition.value,
        "strategy_definition_content_hash": definition_content_hash,
        "basis_entry_threshold": definition.basis_entry_threshold,
        "holding_horizon_bars": definition.holding_horizon_bars,
        "maximum_absolute_exposure": definition.maximum_absolute_exposure,
        "source_run_content_hash": run.content_hash,
        "dataset_version_id": run.dataset_version_id,
        "instrument_id": run.instrument_id,
        "window_start": series.window_start,
        "window_end": series.window_end,
        "daily_series_content_hash": series.content_hash,
        "daily_returns": series.daily_returns,
    }
    trial_content_hash = _content_hash(identity_payload)
    return ResearchTrialV1(
        trial_role=trial_role,
        disposition=disposition,
        strategy_definition_content_hash=definition_content_hash,
        basis_entry_threshold=definition.basis_entry_threshold,
        holding_horizon_bars=definition.holding_horizon_bars,
        maximum_absolute_exposure=definition.maximum_absolute_exposure,
        source_run_content_hash=run.content_hash,
        dataset_version_id=run.dataset_version_id,
        instrument_id=run.instrument_id,
        window_start=series.window_start,
        window_end=series.window_end,
        daily_series_content_hash=series.content_hash,
        daily_returns=series.daily_returns,
        trial_content_hash=trial_content_hash,
        trial_id=_identity("research-trial-v1", trial_content_hash),
    )


@dataclass(frozen=True, slots=True)
class ResearchTrialLedgerV1:
    trials: tuple[ResearchTrialV1, ...]
    content_hash: str
    ledger_id: UUID


def build_research_trial_ledger_v1(trials: Sequence[ResearchTrialV1]) -> ResearchTrialLedgerV1:
    seen: set[str] = set()
    for trial in trials:
        if trial.trial_content_hash in seen:
            raise OpenToOpenValidationV1Error("duplicate_research_trial")
        seen.add(trial.trial_content_hash)
    ordered = tuple(sorted(trials, key=lambda trial: trial.trial_content_hash))
    payload = {"trials": [trial.trial_content_hash for trial in ordered]}
    content_hash = _content_hash(payload)
    return ResearchTrialLedgerV1(
        trials=ordered,
        content_hash=content_hash,
        ledger_id=_identity("research-trial-ledger-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class CanonicalCscvPboEvidenceV1:
    status: str
    unavailable_reasons: tuple[str, ...]
    cscv_blocks: int
    trial_count: int
    number_of_splits: int
    number_of_valid_splits: int
    is_tie_count: int
    invalid_split_reasons: tuple[str, ...]
    pbo: Decimal | None
    trial_identities: tuple[str, ...]
    content_hash: str
    evidence_id: UUID


def _midranks_ascending(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average_rank = (position + 1 + end + 1) / 2
        for index in range(position, end + 1):
            ranks[order[index]] = average_rank
        position = end + 1
    return ranks


def _select_is_winner(is_values: Sequence[float], identities: Sequence[str]) -> int:
    best = max(is_values)
    winners = [index for index in range(len(is_values)) if is_values[index] == best]
    return min(winners, key=lambda index: identities[index])


def _concatenate_blocks(blocks: Sequence[Sequence[Decimal]], selected: tuple[int, ...]) -> tuple[Decimal, ...]:
    combined: list[Decimal] = []
    for block_index in selected:
        combined.extend(blocks[block_index])
    return tuple(combined)


def evaluate_canonical_cscv_pbo_v1(
    *,
    trial_blocks: Sequence[Sequence[Sequence[Decimal]]],
    trial_identities: Sequence[str],
) -> CanonicalCscvPboEvidenceV1:
    trial_count = len(trial_blocks)
    if trial_count != len(trial_identities):
        raise OpenToOpenValidationV1Error("cscv_trial_identity_count_mismatch")
    for identity in trial_identities:
        if not _is_canonical_sha256_hex(identity):
            raise OpenToOpenValidationV1Error("cscv_trial_identity_not_canonical_hash")

    identities = tuple(trial_identities)
    normalized_blocks = [[tuple(block) for block in blocks] for blocks in trial_blocks]
    reasons: list[str] = []
    if len(set(identities)) != trial_count:
        reasons.append("duplicate_trial_identity")
    if len(set(identities)) < CSCV_MINIMUM_TRIALS:
        reasons.append("insufficient_distinct_trials")

    block_count_ok = all(len(blocks) == CSCV_BLOCKS for blocks in normalized_blocks)
    if not block_count_ok:
        reasons.append("trial_block_count_not_eight")
    else:
        for block_index in range(CSCV_BLOCKS):
            lengths = {len(normalized_blocks[trial][block_index]) for trial in range(trial_count)}
            if len(lengths) > 1:
                reasons.append("block_observation_count_misaligned_across_trials")
                break
        for blocks in normalized_blocks:
            if any(len(block) < CSCV_MINIMUM_OBSERVATIONS_PER_BLOCK for block in blocks):
                reasons.append("block_below_minimum_observations")
                break

    if reasons:
        return _cscv_evidence(
            status=STATUS_UNAVAILABLE,
            reasons=tuple(reasons),
            trial_count=trial_count,
            valid_splits=0,
            is_tie_count=0,
            invalid_reasons=(),
            pbo=None,
            identities=identities,
            blocks=normalized_blocks,
        )

    valid_splits = 0
    lambda_le_zero = 0
    is_tie_count = 0
    invalid_reasons: list[str] = []
    for selected in combinations(range(CSCV_BLOCKS), 4):
        oos = tuple(index for index in range(CSCV_BLOCKS) if index not in selected)
        is_sharpes: list[float | None] = [
            non_annualized_daily_sharpe(_concatenate_blocks(blocks, selected)) for blocks in normalized_blocks
        ]
        oos_sharpes: list[float | None] = [
            non_annualized_daily_sharpe(_concatenate_blocks(blocks, oos)) for blocks in normalized_blocks
        ]
        if any(value is None for value in is_sharpes) or any(value is None for value in oos_sharpes):
            invalid_reasons.append("undefined_sharpe")
            continue
        is_values = [value for value in is_sharpes if value is not None]
        oos_values = [value for value in oos_sharpes if value is not None]
        valid_splits += 1
        best_is = max(is_values)
        if sum(1 for value in is_values if value == best_is) > 1:
            is_tie_count += 1
        winner = _select_is_winner(is_values, identities)
        oos_ranks = _midranks_ascending(oos_values)
        omega = oos_ranks[winner] / (trial_count + 1)
        lambda_c = log(omega / (1 - omega))
        if lambda_c <= 0:
            lambda_le_zero += 1

    if valid_splits != CSCV_SPLIT_COUNT:
        return _cscv_evidence(
            status=STATUS_UNAVAILABLE,
            reasons=("invalid_splits_present",),
            trial_count=trial_count,
            valid_splits=valid_splits,
            is_tie_count=is_tie_count,
            invalid_reasons=tuple(invalid_reasons),
            pbo=None,
            identities=identities,
            blocks=normalized_blocks,
        )
    pbo = Decimal(lambda_le_zero) / Decimal(CSCV_SPLIT_COUNT)
    return _cscv_evidence(
        status=STATUS_AVAILABLE,
        reasons=(),
        trial_count=trial_count,
        valid_splits=valid_splits,
        is_tie_count=is_tie_count,
        invalid_reasons=(),
        pbo=pbo,
        identities=identities,
        blocks=normalized_blocks,
    )


def _cscv_evidence(
    *,
    status: str,
    reasons: tuple[str, ...],
    trial_count: int,
    valid_splits: int,
    is_tie_count: int,
    invalid_reasons: tuple[str, ...],
    pbo: Decimal | None,
    identities: tuple[str, ...],
    blocks: Sequence[Sequence[Sequence[Decimal]]],
) -> CanonicalCscvPboEvidenceV1:
    payload = {
        "status": status,
        "unavailable_reasons": reasons,
        "cscv_blocks": CSCV_BLOCKS,
        "trial_count": trial_count,
        "number_of_splits": CSCV_SPLIT_COUNT,
        "number_of_valid_splits": valid_splits,
        "is_tie_count": is_tie_count,
        "invalid_split_reasons": invalid_reasons,
        "pbo": pbo,
        "trial_identities": identities,
        "trial_blocks": [[list(block) for block in trial] for trial in blocks],
    }
    content_hash = _content_hash(payload)
    return CanonicalCscvPboEvidenceV1(
        status=status,
        unavailable_reasons=reasons,
        cscv_blocks=CSCV_BLOCKS,
        trial_count=trial_count,
        number_of_splits=CSCV_SPLIT_COUNT,
        number_of_valid_splits=valid_splits,
        is_tie_count=is_tie_count,
        invalid_split_reasons=invalid_reasons,
        pbo=pbo,
        trial_identities=identities,
        content_hash=content_hash,
        evidence_id=_identity("canonical-cscv-pbo-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class DeflatedSharpeEvidenceV1:
    status: str
    unavailable_reasons: tuple[str, ...]
    observation_count: int
    trial_count: int
    selected_sharpe: float | None
    skewness: float | None
    kurtosis: float | None
    trial_sharpe_mean: float | None
    trial_sharpe_standard_deviation: float | None
    expected_maximum_sharpe: float | None
    denominator: float | None
    z_statistic: float | None
    deflated_sharpe: float | None
    content_hash: str
    evidence_id: UUID


def evaluate_deflated_sharpe_evidence_v1(
    *, ledger: ResearchTrialLedgerV1, selected_trial_id: UUID
) -> DeflatedSharpeEvidenceV1:
    selected = next((trial for trial in ledger.trials if trial.trial_id == selected_trial_id), None)
    if selected is None:
        raise OpenToOpenValidationV1Error("selected_trial_not_in_ledger")
    observation_count = len(selected.daily_returns)
    trial_count = len(ledger.trials)
    selected_sharpe = non_annualized_daily_sharpe(selected.daily_returns)
    skewness = bias_corrected_sample_skewness(selected.daily_returns)
    kurtosis = bias_corrected_pearson_kurtosis(selected.daily_returns)
    trial_sharpes = [non_annualized_daily_sharpe(trial.daily_returns) for trial in ledger.trials]
    finite_trial_sharpes = [value for value in trial_sharpes if value is not None]
    mu = trial_sharpe_mean(finite_trial_sharpes) if len(finite_trial_sharpes) == trial_count else None
    sigma = (
        trial_sharpe_standard_deviation(finite_trial_sharpes) if len(finite_trial_sharpes) == trial_count else None
    )
    comparable_window = all(
        trial.window_start == selected.window_start
        and trial.window_end == selected.window_end
        and len(trial.daily_returns) == observation_count
        for trial in ledger.trials
    )

    reasons: list[str] = []
    if observation_count < DSR_MINIMUM_OBSERVATIONS:
        reasons.append("insufficient_observations")
    if trial_count < DSR_MINIMUM_TRIALS:
        reasons.append("insufficient_trials")
    if not comparable_window:
        reasons.append("incomparable_trial_evaluation_window")
    if selected_sharpe is None:
        reasons.append("selected_sharpe_unavailable")
    if skewness is None:
        reasons.append("skewness_unavailable")
    if kurtosis is None:
        reasons.append("kurtosis_unavailable")
    if len(finite_trial_sharpes) != trial_count:
        reasons.append("trial_sharpe_unavailable")
    if sigma is None:
        reasons.append("sigma_sr_unavailable")

    expected_maximum: float | None = None
    denominator: float | None = None
    z_statistic: float | None = None
    deflated: float | None = None

    if (
        not reasons
        and selected_sharpe is not None
        and skewness is not None
        and kurtosis is not None
        and mu is not None
        and sigma is not None
    ):
        standard_normal = NormalDist()
        expected_maximum = mu + sigma * (
            (1 - _EULER_MASCHERONI) * standard_normal.inv_cdf(1 - 1 / trial_count)
            + _EULER_MASCHERONI * standard_normal.inv_cdf(1 - 1 / (trial_count * e))
        )
        radicand = 1 - skewness * selected_sharpe + ((kurtosis - 1) / 4) * selected_sharpe ** 2
        if radicand <= 0:
            reasons.append("non_positive_denominator")
        else:
            denominator = sqrt(radicand)
            z_statistic = (selected_sharpe - expected_maximum) * sqrt(observation_count - 1) / denominator
            deflated = standard_normal.cdf(z_statistic)

    status = STATUS_AVAILABLE if not reasons and deflated is not None else STATUS_UNAVAILABLE
    if status == STATUS_UNAVAILABLE:
        deflated = None
    payload = {
        "status": status,
        "unavailable_reasons": tuple(reasons),
        "ledger_content_hash": ledger.content_hash,
        "selected_trial_id": selected_trial_id,
        "observation_count": observation_count,
        "trial_count": trial_count,
        "selected_sharpe": selected_sharpe,
        "skewness": skewness,
        "kurtosis": kurtosis,
        "trial_sharpe_mean": mu,
        "trial_sharpe_standard_deviation": sigma,
        "expected_maximum_sharpe": expected_maximum,
        "denominator": denominator,
        "z_statistic": z_statistic,
        "deflated_sharpe": deflated,
    }
    content_hash = _content_hash(payload)
    return DeflatedSharpeEvidenceV1(
        status=status,
        unavailable_reasons=tuple(reasons),
        observation_count=observation_count,
        trial_count=trial_count,
        selected_sharpe=selected_sharpe,
        skewness=skewness,
        kurtosis=kurtosis,
        trial_sharpe_mean=mu,
        trial_sharpe_standard_deviation=sigma,
        expected_maximum_sharpe=expected_maximum,
        denominator=denominator,
        z_statistic=z_statistic,
        deflated_sharpe=deflated,
        content_hash=content_hash,
        evidence_id=_identity("deflated-sharpe-v1", content_hash),
    )


def circularly_shift_basis_values(values: Sequence[Decimal], offset: int) -> tuple[Decimal, ...]:
    count = len(values)
    if count == 0:
        raise OpenToOpenValidationV1Error("circular_shift_requires_values")
    if offset % count == 0:
        raise OpenToOpenValidationV1Error("zero_shift_forbidden")
    shift = offset % count
    return tuple(values[(index - shift) % count] for index in range(count))


def _eligible_decisions_in_window(
    run: BasisMeanReversionResearchRunV1, window_start: datetime, window_end: datetime
) -> tuple[tuple[datetime, ...], tuple[Decimal, ...]]:
    eligible = tuple(
        decision for decision in run.decisions if window_start <= decision.decision_at < window_end
    )
    return tuple(decision.decision_at for decision in eligible), tuple(decision.basis_value for decision in eligible)


@dataclass(frozen=True, slots=True)
class CircularShiftNullControlEvidenceV1:
    status: str
    unavailable_reasons: tuple[str, ...]
    synthetic_null_control_evidence: bool
    seed: int
    eligible_feature_event_count: int
    target_null_runs: int
    attempted_shifts: int
    valid_shifts: int
    unavailable_shifts: int
    selected_offsets: tuple[int, ...]
    observed_sharpe: float | None
    empirical_p_value: Decimal | None
    content_hash: str
    evidence_id: UUID


def evaluate_circular_shift_null_control_v1(
    *,
    run: BasisMeanReversionResearchRunV1,
    bar_series: AuthoritativeTradableBarSeriesV2,
    base_cost_model: CostModel,
    window_start: datetime,
    window_end: datetime,
    seed: int,
    target_null_runs: int = NULL_CONTROL_TARGET_RUNS,
    minimum_valid_shifts: int = NULL_CONTROL_MINIMUM_VALID_SHIFTS,
) -> CircularShiftNullControlEvidenceV1:
    _require_matching_bar_series(run, bar_series)
    _validate_cost_model(base_cost_model)
    _verify_base_cost_model_reconciles(run, base_cost_model)
    _require_utc_midnight(window_start, "window_start")
    _require_utc_midnight(window_end, "window_end")
    if window_end <= window_start:
        raise OpenToOpenValidationV1Error("window_end_must_be_after_window_start")

    timestamps, basis_values = _eligible_decisions_in_window(run, window_start, window_end)
    event_count = len(basis_values)
    cap = run.definition.maximum_absolute_exposure
    threshold = run.definition.basis_entry_threshold
    horizon = run.definition.holding_horizon_bars * _ONE_BAR_INTERVAL

    observed_series = build_realized_exit_daily_return_series_v1(
        run=run, window_start=window_start, window_end=window_end
    )
    observed_sharpe = non_annualized_daily_sharpe(observed_series.daily_returns)

    attempted = min(target_null_runs, event_count - 1) if event_count >= 2 else 0
    if attempted > 0:
        rng = random.Random(seed)  # nosec B311
        selected_offsets = tuple(sorted(rng.sample(range(1, event_count), attempted)))
    else:
        selected_offsets = ()

    valid_stats: list[float] = []
    unavailable_shifts = 0
    for offset in selected_offsets:
        shifted = circularly_shift_basis_values(basis_values, offset)
        decision_inputs = tuple(
            (timestamps[index], _direction(shifted[index], threshold, cap)) for index in range(event_count)
        )
        trades, _ = _replay_open_to_open(
            decision_inputs=decision_inputs,
            bar_series=bar_series,
            cap=cap,
            horizon=horizon,
            cost_model=base_cost_model,
            latency=timedelta(0),
        )
        _, null_daily = _daily_returns_over_window(
            ((trade.exit_time, trade.net_return) for trade in trades), window_start, window_end
        )
        stat = non_annualized_daily_sharpe(null_daily)
        if stat is None:
            unavailable_shifts += 1
        else:
            valid_stats.append(stat)

    valid_shifts = len(valid_stats)
    reasons: list[str] = []
    if observed_sharpe is None:
        reasons.append("observed_sharpe_unavailable")
    if valid_shifts < minimum_valid_shifts:
        reasons.append("insufficient_valid_shifts")

    if reasons or observed_sharpe is None:
        status = STATUS_UNAVAILABLE
        p_value: Decimal | None = None
    else:
        status = STATUS_AVAILABLE
        greater_or_equal = sum(1 for stat in valid_stats if stat >= observed_sharpe)
        p_value = Decimal(1 + greater_or_equal) / Decimal(1 + valid_shifts)

    bar_fingerprint = _bar_series_fingerprint(bar_series)
    payload = {
        "status": status,
        "unavailable_reasons": tuple(reasons),
        "synthetic_null_control_evidence": True,
        "source_run_content_hash": run.content_hash,
        "bar_series_fingerprint": bar_fingerprint,
        "base_cost_model": _wire_cost_model(base_cost_model),
        "seed": seed,
        "window_start": window_start,
        "window_end": window_end,
        "eligible_feature_event_count": event_count,
        "target_null_runs": target_null_runs,
        "attempted_shifts": attempted,
        "valid_shifts": valid_shifts,
        "unavailable_shifts": unavailable_shifts,
        "selected_offsets": selected_offsets,
        "observed_sharpe": observed_sharpe,
        "empirical_p_value": p_value,
    }
    content_hash = _content_hash(payload)
    return CircularShiftNullControlEvidenceV1(
        status=status,
        unavailable_reasons=tuple(reasons),
        synthetic_null_control_evidence=True,
        seed=seed,
        eligible_feature_event_count=event_count,
        target_null_runs=target_null_runs,
        attempted_shifts=attempted,
        valid_shifts=valid_shifts,
        unavailable_shifts=unavailable_shifts,
        selected_offsets=selected_offsets,
        observed_sharpe=observed_sharpe,
        empirical_p_value=p_value,
        content_hash=content_hash,
        evidence_id=_identity("circular-shift-null-control-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class FullPermutationNullDiagnosticV1:
    is_primary: bool
    status: str
    unavailable_reasons: tuple[str, ...]
    synthetic_null_control_evidence: bool
    seed: int
    attempted_permutations: int
    valid_permutations: int
    observed_sharpe: float | None
    diagnostic_p_value: Decimal | None
    content_hash: str
    evidence_id: UUID


def evaluate_full_permutation_null_diagnostic_v1(
    *,
    run: BasisMeanReversionResearchRunV1,
    bar_series: AuthoritativeTradableBarSeriesV2,
    base_cost_model: CostModel,
    window_start: datetime,
    window_end: datetime,
    seed: int,
    permutations: int = NULL_CONTROL_TARGET_RUNS,
    minimum_valid_permutations: int = NULL_CONTROL_MINIMUM_VALID_SHIFTS,
) -> FullPermutationNullDiagnosticV1:
    _require_matching_bar_series(run, bar_series)
    _validate_cost_model(base_cost_model)
    _verify_base_cost_model_reconciles(run, base_cost_model)
    _require_utc_midnight(window_start, "window_start")
    _require_utc_midnight(window_end, "window_end")
    if window_end <= window_start:
        raise OpenToOpenValidationV1Error("window_end_must_be_after_window_start")
    if permutations < 1:
        raise OpenToOpenValidationV1Error("permutations_must_be_positive")

    timestamps, basis_tuple = _eligible_decisions_in_window(run, window_start, window_end)
    basis_values = list(basis_tuple)
    event_count = len(basis_values)
    cap = run.definition.maximum_absolute_exposure
    threshold = run.definition.basis_entry_threshold
    horizon = run.definition.holding_horizon_bars * _ONE_BAR_INTERVAL

    observed_series = build_realized_exit_daily_return_series_v1(
        run=run, window_start=window_start, window_end=window_end
    )
    observed_sharpe = non_annualized_daily_sharpe(observed_series.daily_returns)

    rng = random.Random(seed)  # nosec B311
    valid_stats: list[float] = []
    for _ in range(permutations):
        shuffled = basis_values[:]
        rng.shuffle(shuffled)
        decision_inputs = tuple(
            (timestamps[index], _direction(shuffled[index], threshold, cap)) for index in range(event_count)
        )
        trades, _ = _replay_open_to_open(
            decision_inputs=decision_inputs,
            bar_series=bar_series,
            cap=cap,
            horizon=horizon,
            cost_model=base_cost_model,
            latency=timedelta(0),
        )
        _, null_daily = _daily_returns_over_window(
            ((trade.exit_time, trade.net_return) for trade in trades), window_start, window_end
        )
        stat = non_annualized_daily_sharpe(null_daily)
        if stat is not None:
            valid_stats.append(stat)

    valid_permutations = len(valid_stats)
    reasons: list[str] = []
    if observed_sharpe is None:
        reasons.append("observed_sharpe_unavailable")
    if valid_permutations < minimum_valid_permutations:
        reasons.append("insufficient_valid_permutations")

    if reasons or observed_sharpe is None:
        status = STATUS_UNAVAILABLE
        p_value: Decimal | None = None
    else:
        status = STATUS_AVAILABLE
        greater_or_equal = sum(1 for stat in valid_stats if stat >= observed_sharpe)
        p_value = Decimal(1 + greater_or_equal) / Decimal(1 + valid_permutations)

    bar_fingerprint = _bar_series_fingerprint(bar_series)
    payload = {
        "is_primary": False,
        "status": status,
        "unavailable_reasons": tuple(reasons),
        "synthetic_null_control_evidence": True,
        "source_run_content_hash": run.content_hash,
        "bar_series_fingerprint": bar_fingerprint,
        "base_cost_model": _wire_cost_model(base_cost_model),
        "seed": seed,
        "window_start": window_start,
        "window_end": window_end,
        "attempted_permutations": permutations,
        "valid_permutations": valid_permutations,
        "observed_sharpe": observed_sharpe,
        "diagnostic_p_value": p_value,
    }
    content_hash = _content_hash(payload)
    return FullPermutationNullDiagnosticV1(
        is_primary=False,
        status=status,
        unavailable_reasons=tuple(reasons),
        synthetic_null_control_evidence=True,
        seed=seed,
        attempted_permutations=permutations,
        valid_permutations=valid_permutations,
        observed_sharpe=observed_sharpe,
        diagnostic_p_value=p_value,
        content_hash=content_hash,
        evidence_id=_identity("full-permutation-null-diagnostic-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class ReconciliationDecisionCheckV1:
    decision_index: int
    discrepancies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpenToOpenReconciliationEvidenceV1:
    status: str
    decision_count: int
    mismatched_decision_count: int
    checks: tuple[ReconciliationDecisionCheckV1, ...]
    content_hash: str
    evidence_id: UUID


def _resolve_expected_outcome(
    *,
    decision_at: datetime,
    basis_value: Decimal,
    threshold: Decimal,
    cap: Decimal,
    bar_series: AuthoritativeTradableBarSeriesV2,
    horizon: timedelta,
    last_executed_exit_time: datetime | None,
    cost_model: CostModel,
) -> tuple[BasisMeanReversionOutcomeV1, Decimal, _ReplayTrade | None]:
    exposure = _direction(basis_value, threshold, cap)
    if exposure == 0:
        return BasisMeanReversionOutcomeV1.FLAT, exposure, None
    if last_executed_exit_time is not None and decision_at <= last_executed_exit_time:
        return BasisMeanReversionOutcomeV1.IGNORED_ACTIVE_TRADE, exposure, None
    entry_bar = bar_series.first_eligible_bar_after(decision_at)
    if entry_bar is None:
        return BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_ENTRY, exposure, None
    exit_open_at = entry_bar.bar_open_at + horizon
    exit_bar = next((bar for bar in bar_series.bars if bar.bar_open_at == exit_open_at), None)
    if exit_bar is None:
        return BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_EXIT, exposure, None
    gross = exposure * (exit_bar.open / entry_bar.open - Decimal("1"))
    turnover = abs(exposure)
    entry_cost = cost_model.cost(turnover)
    exit_cost = cost_model.cost(turnover)
    net = gross - entry_cost - exit_cost
    trade = _ReplayTrade(
        exposure=exposure,
        entry_time=entry_bar.bar_open_at,
        exit_time=exit_bar.bar_open_at,
        entry_open=entry_bar.open,
        exit_open=exit_bar.open,
        gross_return=gross,
        entry_cost=entry_cost,
        exit_cost=exit_cost,
        net_return=net,
    )
    return BasisMeanReversionOutcomeV1.EXECUTED, exposure, trade


def reconcile_open_to_open_trade_ledger_v1(
    *,
    run: BasisMeanReversionResearchRunV1,
    bar_series: AuthoritativeTradableBarSeriesV2,
    base_cost_model: CostModel,
) -> OpenToOpenReconciliationEvidenceV1:
    _require_matching_bar_series(run, bar_series)
    _validate_cost_model(base_cost_model)

    threshold = run.definition.basis_entry_threshold
    cap = run.definition.maximum_absolute_exposure
    horizon = run.definition.holding_horizon_bars * _ONE_BAR_INTERVAL

    checks: list[ReconciliationDecisionCheckV1] = []
    mismatched = 0
    last_executed_exit_time: datetime | None = None

    for index, decision in enumerate(run.decisions):
        discrepancies: list[str] = []
        expected_outcome, expected_exposure, expected_trade = _resolve_expected_outcome(
            decision_at=decision.decision_at,
            basis_value=decision.basis_value,
            threshold=threshold,
            cap=cap,
            bar_series=bar_series,
            horizon=horizon,
            last_executed_exit_time=last_executed_exit_time,
            cost_model=base_cost_model,
        )
        if expected_exposure != decision.exposure:
            discrepancies.append("direction")
        if expected_outcome != decision.outcome:
            discrepancies.append("outcome")

        if expected_outcome is BasisMeanReversionOutcomeV1.EXECUTED:
            if expected_trade is None:
                raise OpenToOpenValidationV1Error("internal_reconciliation_invariant_violation")
            last_executed_exit_time = expected_trade.exit_time
            if decision.trade is None:
                discrepancies.append("missing_executed_trade")
            else:
                trade = decision.trade
                if expected_trade.exposure != trade.exposure:
                    discrepancies.append("exposure")
                if expected_trade.entry_time != trade.entry_time or expected_trade.entry_open != trade.entry_open:
                    discrepancies.append("entry")
                if expected_trade.exit_time != trade.exit_time or expected_trade.exit_open != trade.exit_open:
                    discrepancies.append("exit")
                if expected_trade.gross_return != trade.gross_return:
                    discrepancies.append("gross_return")
                if expected_trade.entry_cost != trade.entry_cost:
                    discrepancies.append("entry_cost")
                if expected_trade.exit_cost != trade.exit_cost:
                    discrepancies.append("exit_cost")
                if expected_trade.net_return != trade.net_return:
                    discrepancies.append("net_return")
        elif decision.trade is not None:
            discrepancies.append("extra_executed_trade")

        if discrepancies:
            mismatched += 1
        checks.append(ReconciliationDecisionCheckV1(index, tuple(discrepancies)))

    status = STATUS_RECONCILED if mismatched == 0 else STATUS_BLOCKED
    bar_fingerprint = _bar_series_fingerprint(bar_series)
    payload = {
        "status": status,
        "source_run_content_hash": run.content_hash,
        "bar_series_fingerprint": bar_fingerprint,
        "base_cost_model": _wire_cost_model(base_cost_model),
        "decision_count": len(checks),
        "mismatched_decision_count": mismatched,
        "checks": [
            {"decision_index": check.decision_index, "discrepancies": check.discrepancies} for check in checks
        ],
    }
    content_hash = _content_hash(payload)
    return OpenToOpenReconciliationEvidenceV1(
        status=status,
        decision_count=len(checks),
        mismatched_decision_count=mismatched,
        checks=tuple(checks),
        content_hash=content_hash,
        evidence_id=_identity("open-to-open-reconciliation-v1", content_hash),
    )
