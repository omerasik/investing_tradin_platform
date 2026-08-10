"""Portfolio-level deterministic risk metrics and generalized stress scenarios."""

from dataclasses import dataclass, field
from decimal import Decimal
from math import ceil


@dataclass(frozen=True, slots=True)
class PortfolioExposure:
    instrument_id: str
    notional: Decimal
    asset_class: str
    sector: str
    country: str = ""
    currency: str = ""
    broker: str = ""
    exchange: str = ""
    correlation_cluster: str = ""
    beta: Decimal = Decimal("0")
    duration: Decimal = Decimal("0")
    factors: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StressScenario:
    name: str
    shocks: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class StressResult:
    scenario: str
    loss: Decimal
    contributions: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class PortfolioRiskPolicy:
    maximum_gross_notional: Decimal
    maximum_single_weight: Decimal
    maximum_scenario_loss: Decimal
    maximum_net_notional: Decimal | None = None
    maximum_var_loss: Decimal | None = None
    maximum_cvar_loss: Decimal | None = None
    maximum_group_notionals: dict[str, Decimal] = field(default_factory=dict)
    maximum_factor_notionals: dict[str, Decimal] = field(default_factory=dict)
    maximum_beta_notional: Decimal | None = None
    maximum_duration_notional: Decimal | None = None
    maximum_drawdown_loss: Decimal | None = None
    minimum_historical_observations: int | None = None
    return_history_window_observations: int | None = None
    maximum_return_history_age_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class PortfolioRiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    gross: Decimal
    net: Decimal
    stress_loss: Decimal
    var_loss: Decimal | None
    cvar_loss: Decimal | None
    drawdown_loss: Decimal | None
    stress_results: tuple[StressResult, ...] = ()


@dataclass(frozen=True, slots=True)
class HistoricalRiskResult:
    confidence: Decimal
    observations: int
    var_loss: Decimal
    cvar_loss: Decimal


def gross_and_net(exposures: list[PortfolioExposure]) -> tuple[Decimal, Decimal]:
    return sum((abs(item.notional) for item in exposures), Decimal("0")), sum((item.notional for item in exposures), Decimal("0"))


def stress(exposures: list[PortfolioExposure], scenario: StressScenario) -> StressResult:
    contributions = {item.instrument_id: item.notional * scenario.shocks.get(item.instrument_id, scenario.shocks.get(item.asset_class, Decimal("0"))) for item in exposures}
    return StressResult(scenario.name, sum(contributions.values(), Decimal("0")), contributions)


def concentration_breaches(exposures: list[PortfolioExposure], equity: Decimal, maximum_single_weight: Decimal) -> tuple[str, ...]:
    if equity <= 0 or maximum_single_weight <= 0:
        raise ValueError("invalid_concentration_limits")
    return tuple(item.instrument_id for item in exposures if abs(item.notional) / equity > maximum_single_weight)


def grouped_exposures(exposures: list[PortfolioExposure], group: str) -> dict[str, Decimal]:
    if group not in {"asset_class", "sector", "country", "currency", "broker", "exchange", "correlation_cluster"}:
        raise ValueError("unsupported_exposure_group")
    totals: dict[str, Decimal] = {}
    for item in exposures:
        name = getattr(item, group)
        if name:
            totals[name] = totals.get(name, Decimal("0")) + item.notional
    return totals


def factor_exposures(exposures: list[PortfolioExposure]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for item in exposures:
        for factor, loading in item.factors.items():
            totals[factor] = totals.get(factor, Decimal("0")) + item.notional * loading
    return totals


def weighted_exposure(exposures: list[PortfolioExposure], field_name: str) -> Decimal:
    if field_name not in {"beta", "duration"}:
        raise ValueError("unsupported_weighted_exposure")
    return sum((item.notional * getattr(item, field_name) for item in exposures), Decimal("0"))


def maximum_drawdown_loss(period_returns: list[Decimal]) -> Decimal:
    if not period_returns:
        raise ValueError("missing_drawdown_history")
    equity = peak = Decimal("1")
    drawdown = Decimal("0")
    for period_return in period_returns:
        if period_return <= Decimal("-1"):
            raise ValueError("return_below_negative_one")
        equity *= Decimal("1") + period_return
        peak = max(peak, equity)
        drawdown = max(drawdown, Decimal("1") - equity / peak)
    return drawdown


def historical_var_cvar(period_returns: list[Decimal], *, confidence: Decimal = Decimal("0.05")) -> HistoricalRiskResult:
    """Historical one-period loss estimates; results are non-negative loss magnitudes."""
    if not period_returns or not Decimal("0") < confidence < Decimal("1"):
        raise ValueError("invalid_historical_risk_inputs")
    ordered = sorted(period_returns)
    tail_count = max(1, ceil(len(ordered) * float(confidence)))
    tail = ordered[:tail_count]
    var_loss = max(Decimal("0"), -tail[-1])
    cvar_loss = max(Decimal("0"), -sum(tail, Decimal("0")) / Decimal(len(tail)))
    return HistoricalRiskResult(confidence, len(ordered), var_loss, cvar_loss)


def evaluate_portfolio(exposures: list[PortfolioExposure], equity: Decimal, scenarios: StressScenario | tuple[StressScenario, ...],
                       policy: PortfolioRiskPolicy, *, historical_returns: list[Decimal] | None = None,
                       confidence: Decimal = Decimal("0.05")) -> PortfolioRiskDecision:
    if equity <= 0:
        raise ValueError("portfolio_equity_must_be_positive")
    if (policy.minimum_historical_observations is not None and policy.minimum_historical_observations < 1) or (policy.return_history_window_observations is not None and policy.return_history_window_observations < 1) or (policy.maximum_return_history_age_seconds is not None and policy.maximum_return_history_age_seconds < 0):
        raise ValueError("invalid_minimum_historical_observations")
    selected_scenarios = (scenarios,) if isinstance(scenarios, StressScenario) else scenarios
    if not selected_scenarios or any(not scenario.name.strip() for scenario in selected_scenarios) or len({scenario.name for scenario in selected_scenarios}) != len(selected_scenarios):
        raise ValueError("invalid_stress_scenarios")
    gross, net = gross_and_net(exposures)
    stress_results = tuple(stress(exposures, scenario) for scenario in selected_scenarios)
    worst_stress_loss = min(result.loss for result in stress_results)
    reasons: list[str] = []
    if gross > policy.maximum_gross_notional:
        reasons.append("gross_exposure_limit")
    reasons.extend(f"concentration:{instrument}" for instrument in concentration_breaches(exposures, equity, policy.maximum_single_weight))
    if policy.maximum_net_notional is not None and abs(net) > policy.maximum_net_notional:
        reasons.append("net_exposure_limit")
    for key, limit in policy.maximum_group_notionals.items():
        group, separator, name = key.partition(":")
        if separator != ":" or not name or limit < 0:
            raise ValueError("invalid_group_exposure_limit")
        if abs(grouped_exposures(exposures, group).get(name, Decimal("0"))) > limit:
            reasons.append(f"group_exposure_limit:{key}")
    for factor, limit in policy.maximum_factor_notionals.items():
        if limit < 0:
            raise ValueError("invalid_factor_exposure_limit")
        if abs(factor_exposures(exposures).get(factor, Decimal("0"))) > limit:
            reasons.append(f"factor_exposure_limit:{factor}")
    if policy.maximum_beta_notional is not None and abs(weighted_exposure(exposures, "beta")) > policy.maximum_beta_notional:
        reasons.append("beta_exposure_limit")
    if policy.maximum_duration_notional is not None and abs(weighted_exposure(exposures, "duration")) > policy.maximum_duration_notional:
        reasons.append("duration_exposure_limit")
    for result in stress_results:
        if result.loss < -policy.maximum_scenario_loss:
            reasons.append(f"scenario_loss_limit:{result.scenario}")
    insufficient_history = policy.minimum_historical_observations is not None and (historical_returns is None or len(historical_returns) < policy.minimum_historical_observations)
    metrics = None if historical_returns is None or not historical_returns else historical_var_cvar(historical_returns, confidence=confidence)
    if policy.maximum_var_loss is not None:
        if metrics is None:
            reasons.append("missing_var_history")
        elif insufficient_history:
            reasons.append("insufficient_historical_return_observations")
        elif metrics.var_loss > policy.maximum_var_loss:
            reasons.append("var_limit")
    if policy.maximum_cvar_loss is not None:
        if metrics is None:
            reasons.append("missing_cvar_history")
        elif insufficient_history:
            reasons.append("insufficient_historical_return_observations")
        elif metrics.cvar_loss > policy.maximum_cvar_loss:
            reasons.append("cvar_limit")
    drawdown_loss = None if historical_returns is None else maximum_drawdown_loss(historical_returns)
    if policy.maximum_drawdown_loss is not None:
        if drawdown_loss is None:
            reasons.append("missing_drawdown_history")
        elif insufficient_history:
            reasons.append("insufficient_historical_return_observations")
        elif drawdown_loss > policy.maximum_drawdown_loss:
            reasons.append("drawdown_limit")
    return PortfolioRiskDecision(not reasons, tuple(dict.fromkeys(reasons)), gross, net, worst_stress_loss, None if metrics is None else metrics.var_loss, None if metrics is None else metrics.cvar_loss, drawdown_loss, stress_results)
