"""Version-bound, review-only multi-strategy portfolio construction evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .persistence import PostgresDatabase
from .portfolio_risk import (
    PortfolioExposure,
    PortfolioRiskPolicy,
    StressScenario,
    evaluate_portfolio,
)


class PortfolioConstructionV2Error(ValueError):
    pass


class ConstructionStatus(StrEnum):
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ConstraintState(StrEnum):
    SATISFIED = "SATISFIED"
    REDUCED = "REDUCED"
    BLOCKED = "BLOCKED"


LIMITATIONS = (
    "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",
    "REVIEW_ONLY_TARGET_WEIGHT_CANDIDATE",
    "NO_SIGNAL_ORDER_OMS_BROKER_OR_EXECUTION_AUTHORITY",
    "NO_AUTOMATIC_STRATEGY_ACTIVATION_OR_RISK_LIMIT_INCREASE",
)

GROUP_NAMES = (
    "asset_class",
    "sector",
    "country",
    "currency",
    "broker",
    "exchange",
    "correlation_cluster",
)


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[object, ...]) -> object: ...
    def fetchone(self) -> tuple[object, ...] | None: ...
    def fetchall(self) -> list[tuple[object, ...]]: ...


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PortfolioConstructionV2Error(f"{name}_must_be_timezone_aware")


def _decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise PortfolioConstructionV2Error("non_finite_portfolio_value")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _matrix_payload(matrix: tuple[tuple[Decimal, ...], ...]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(_decimal(value) for value in row) for row in matrix)


@dataclass(frozen=True, slots=True)
class PortfolioConstructionPolicyV2:
    policy_id: UUID
    version: str
    implementation_version: str
    equity: Decimal
    target_volatility: Decimal
    maximum_gross_leverage: Decimal
    maximum_net_leverage: Decimal
    maximum_sleeve_weight: Decimal
    maximum_stress_loss_weight: Decimal
    minimum_liquidity_score: Decimal
    maximum_drawdown: Decimal
    minimum_decay_score: Decimal
    covariance_uncertainty: Decimal
    correlation_stress: Decimal
    stress_shock: Decimal
    group_weight_limits: tuple[tuple[str, Decimal], ...]
    factor_exposure_limits: tuple[tuple[str, Decimal], ...]
    created_at: datetime
    status: str = "RESEARCH_ONLY"
    automatic_authority: bool = False
    policy_version_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "portfolio_policy_created_at")
        group_keys = tuple(key for key, _ in self.group_weight_limits)
        factor_keys = tuple(key for key, _ in self.factor_exposure_limits)
        valid_group_keys = all(
            key.partition(":")[0] in GROUP_NAMES and bool(key.partition(":")[2])
            for key in group_keys
        )
        if (
            not self.version.strip()
            or not self.implementation_version.strip()
            or self.status != "RESEARCH_ONLY"
            or self.automatic_authority
            or self.equity <= 0
            or self.target_volatility <= 0
            or self.maximum_gross_leverage <= 0
            or self.maximum_net_leverage < 0
            or not Decimal("0") < self.maximum_sleeve_weight <= Decimal("1")
            or not Decimal("0") <= self.maximum_stress_loss_weight <= Decimal("1")
            or not Decimal("0") <= self.minimum_liquidity_score <= Decimal("1")
            or not Decimal("0") <= self.maximum_drawdown <= Decimal("1")
            or not Decimal("0") <= self.minimum_decay_score <= Decimal("1")
            or not Decimal("0") <= self.covariance_uncertainty <= Decimal("1")
            or not Decimal("0") <= self.correlation_stress <= Decimal("1")
            or not Decimal("-1") <= self.stress_shock <= Decimal("0")
            or len(set(group_keys)) != len(group_keys)
            or len(set(factor_keys)) != len(factor_keys)
            or not valid_group_keys
            or any(limit < 0 for _, limit in self.group_weight_limits)
            or any(not key.strip() or limit < 0 for key, limit in self.factor_exposure_limits)
        ):
            raise PortfolioConstructionV2Error("invalid_portfolio_construction_policy")
        payload = self.payload()
        content_hash = _digest(payload)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "policy_version_id",
            uuid5(NAMESPACE_URL, f"portfolio-construction-policy:{content_hash}"),
        )

    def payload(self) -> dict[str, object]:
        return {
            "policy_id": str(self.policy_id),
            "version": self.version,
            "implementation_version": self.implementation_version,
            "equity": _decimal(self.equity),
            "target_volatility": _decimal(self.target_volatility),
            "maximum_gross_leverage": _decimal(self.maximum_gross_leverage),
            "maximum_net_leverage": _decimal(self.maximum_net_leverage),
            "maximum_sleeve_weight": _decimal(self.maximum_sleeve_weight),
            "maximum_stress_loss_weight": _decimal(self.maximum_stress_loss_weight),
            "minimum_liquidity_score": _decimal(self.minimum_liquidity_score),
            "maximum_drawdown": _decimal(self.maximum_drawdown),
            "minimum_decay_score": _decimal(self.minimum_decay_score),
            "covariance_uncertainty": _decimal(self.covariance_uncertainty),
            "correlation_stress": _decimal(self.correlation_stress),
            "stress_shock": _decimal(self.stress_shock),
            "group_weight_limits": tuple(
                (key, _decimal(value)) for key, value in self.group_weight_limits
            ),
            "factor_exposure_limits": tuple(
                (key, _decimal(value)) for key, value in self.factor_exposure_limits
            ),
            "created_at": self.created_at,
            "status": self.status,
            "automatic_authority": self.automatic_authority,
        }


@dataclass(frozen=True, slots=True)
class StrategySleeveInputV2:
    strategy_key: str
    strategy_version_id: UUID
    scorecard_id: UUID
    validation_package_id: UUID
    regime_candidate_id: UUID
    normalized_signal: Decimal
    standalone_volatility: Decimal
    risk_budget: Decimal
    capacity_weight: Decimal
    liquidity_score: Decimal
    drawdown: Decimal
    decay_score: Decimal
    regime_current_multiplier: Decimal
    regime_proposed_multiplier: Decimal
    delta_adjusted_multiplier: Decimal
    pending_order_weight: Decimal
    fx_weight: Decimal
    groups: tuple[tuple[str, str], ...]
    factor_loadings: tuple[tuple[str, Decimal], ...]
    scorecard_status: ConstructionStatus = ConstructionStatus.REVIEW_REQUIRED
    validation_integrity: str = "VERIFIED"
    regime_status: ConstructionStatus = ConstructionStatus.REVIEW_REQUIRED
    automatic_authority: bool = False
    sleeve_input_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        group_names = tuple(name for name, _ in self.groups)
        factor_names = tuple(name for name, _ in self.factor_loadings)
        if (
            not self.strategy_key.strip()
            or not Decimal("-1") <= self.normalized_signal <= Decimal("1")
            or self.standalone_volatility <= 0
            or self.risk_budget <= 0
            or not Decimal("0") <= self.capacity_weight <= Decimal("1")
            or not Decimal("0") <= self.liquidity_score <= Decimal("1")
            or not Decimal("0") <= self.drawdown <= Decimal("1")
            or not Decimal("0") <= self.decay_score <= Decimal("1")
            or self.regime_current_multiplier <= 0
            or self.regime_proposed_multiplier < 0
            or self.delta_adjusted_multiplier <= 0
            or len(set(group_names)) != len(group_names)
            or len(set(factor_names)) != len(factor_names)
            or any(name not in GROUP_NAMES or not value.strip() for name, value in self.groups)
            or any(not name.strip() for name, _ in self.factor_loadings)
            or self.automatic_authority
        ):
            raise PortfolioConstructionV2Error("invalid_strategy_sleeve_input")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "sleeve_input_id",
            uuid5(NAMESPACE_URL, f"portfolio-sleeve:{content_hash}"),
        )

    def payload(self) -> dict[str, object]:
        return {
            "strategy_key": self.strategy_key,
            "strategy_version_id": str(self.strategy_version_id),
            "scorecard_id": str(self.scorecard_id),
            "validation_package_id": str(self.validation_package_id),
            "regime_candidate_id": str(self.regime_candidate_id),
            "normalized_signal": _decimal(self.normalized_signal),
            "standalone_volatility": _decimal(self.standalone_volatility),
            "risk_budget": _decimal(self.risk_budget),
            "capacity_weight": _decimal(self.capacity_weight),
            "liquidity_score": _decimal(self.liquidity_score),
            "drawdown": _decimal(self.drawdown),
            "decay_score": _decimal(self.decay_score),
            "regime_current_multiplier": _decimal(self.regime_current_multiplier),
            "regime_proposed_multiplier": _decimal(self.regime_proposed_multiplier),
            "delta_adjusted_multiplier": _decimal(self.delta_adjusted_multiplier),
            "pending_order_weight": _decimal(self.pending_order_weight),
            "fx_weight": _decimal(self.fx_weight),
            "groups": self.groups,
            "factor_loadings": tuple(
                (name, _decimal(value)) for name, value in self.factor_loadings
            ),
            "scorecard_status": self.scorecard_status.value,
            "validation_integrity": self.validation_integrity,
            "regime_status": self.regime_status.value,
            "automatic_authority": self.automatic_authority,
        }


@dataclass(frozen=True, slots=True)
class CovarianceEstimateV2:
    strategy_keys: tuple[str, ...]
    matrix: tuple[tuple[Decimal, ...], ...]
    dataset_version_id: UUID
    dataset_version: str
    dataset_content_hash: str
    estimation_version: str
    observations: int
    as_of: datetime
    covariance_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.as_of, "covariance_as_of")
        size = len(self.strategy_keys)
        valid = (
            size > 0
            and tuple(sorted(self.strategy_keys)) == self.strategy_keys
            and len(set(self.strategy_keys)) == size
            and all(key.strip() for key in self.strategy_keys)
            and bool(self.dataset_version.strip())
            and len(self.dataset_content_hash) == 64
            and len(self.matrix) == size
            and all(len(row) == size for row in self.matrix)
            and self.observations >= 2
            and bool(self.estimation_version.strip())
        )
        if not valid:
            raise PortfolioConstructionV2Error("invalid_covariance_contract")
        for row in self.matrix:
            for value in row:
                _decimal(value)
        for index in range(size):
            if self.matrix[index][index] <= 0:
                raise PortfolioConstructionV2Error("covariance_diagonal_must_be_positive")
            for other in range(size):
                if self.matrix[index][other] != self.matrix[other][index]:
                    raise PortfolioConstructionV2Error("covariance_must_be_symmetric")
                bound = (self.matrix[index][index] * self.matrix[other][other]).sqrt()
                if abs(self.matrix[index][other]) > bound:
                    raise PortfolioConstructionV2Error("invalid_covariance_correlation")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "covariance_id",
            uuid5(NAMESPACE_URL, f"portfolio-covariance:{content_hash}"),
        )

    def payload(self) -> dict[str, object]:
        return {
            "strategy_keys": self.strategy_keys,
            "matrix": _matrix_payload(self.matrix),
            "dataset_version_id": str(self.dataset_version_id),
            "dataset_version": self.dataset_version,
            "dataset_content_hash": self.dataset_content_hash,
            "estimation_version": self.estimation_version,
            "observations": self.observations,
            "as_of": self.as_of,
        }

    def stressed(self, policy: PortfolioConstructionPolicyV2) -> tuple[tuple[Decimal, ...], ...]:
        stress = max(policy.covariance_uncertainty, policy.correlation_stress)
        rows: list[tuple[Decimal, ...]] = []
        for index, row in enumerate(self.matrix):
            values: list[Decimal] = []
            for other, covariance in enumerate(row):
                if index == other:
                    values.append(covariance)
                    continue
                scale = (self.matrix[index][index] * self.matrix[other][other]).sqrt()
                correlation = covariance / scale
                stressed_correlation = correlation + stress * (Decimal("1") - correlation)
                values.append(stressed_correlation * scale)
            rows.append(tuple(values))
        return tuple(rows)


@dataclass(frozen=True, slots=True)
class ConstraintEvaluationV2:
    name: str
    state: ConstraintState
    observed: Decimal | None
    limit: Decimal | None
    reasons: tuple[str, ...]
    constraint_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise PortfolioConstructionV2Error("invalid_constraint_name")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "constraint_id", uuid5(NAMESPACE_URL, f"portfolio-constraint:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "state": self.state.value,
            "observed": None if self.observed is None else _decimal(self.observed),
            "limit": None if self.limit is None else _decimal(self.limit),
            "reasons": self.reasons,
        }


@dataclass(frozen=True, slots=True)
class TargetSleeveWeightV2:
    sleeve_input_id: UUID
    strategy_key: str
    target_weight: Decimal
    effective_notional: Decimal
    marginal_risk: Decimal
    component_risk: Decimal
    adjustment_reasons: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "sleeve_input_id": str(self.sleeve_input_id),
            "strategy_key": self.strategy_key,
            "target_weight": _decimal(self.target_weight),
            "effective_notional": _decimal(self.effective_notional),
            "marginal_risk": _decimal(self.marginal_risk),
            "component_risk": _decimal(self.component_risk),
            "adjustment_reasons": self.adjustment_reasons,
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskGateEvidenceV2:
    approved: bool
    gross_notional: Decimal
    net_notional: Decimal
    stress_loss: Decimal
    reasons: tuple[str, ...]
    report: dict[str, object]
    automatic_authority: bool = False
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.automatic_authority:
            raise PortfolioConstructionV2Error("portfolio_risk_gate_cannot_grant_authority")
        object.__setattr__(self, "content_hash", _digest(self.payload()))

    def payload(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "gross_notional": _decimal(self.gross_notional),
            "net_notional": _decimal(self.net_notional),
            "stress_loss": _decimal(self.stress_loss),
            "reasons": self.reasons,
            "report": self.report,
            "automatic_authority": self.automatic_authority,
        }


@dataclass(frozen=True, slots=True)
class PortfolioTargetCandidateV2:
    status: ConstructionStatus
    cash_weight: Decimal
    gross_weight: Decimal
    net_weight: Decimal
    portfolio_volatility: Decimal
    stressed_volatility: Decimal
    created_at: datetime
    sleeves: tuple[TargetSleeveWeightV2, ...]
    automatic_authority: bool = False
    candidate_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "portfolio_candidate_created_at")
        if self.automatic_authority or self.gross_weight < 0:
            raise PortfolioConstructionV2Error("invalid_portfolio_target_candidate")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "candidate_id", uuid5(NAMESPACE_URL, f"portfolio-target:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "cash_weight": _decimal(self.cash_weight),
            "gross_weight": _decimal(self.gross_weight),
            "net_weight": _decimal(self.net_weight),
            "portfolio_volatility": _decimal(self.portfolio_volatility),
            "stressed_volatility": _decimal(self.stressed_volatility),
            "created_at": self.created_at,
            "sleeves": tuple(item.payload() for item in self.sleeves),
            "automatic_authority": self.automatic_authority,
        }


@dataclass(frozen=True, slots=True)
class PortfolioConstructionRunV2:
    policy: PortfolioConstructionPolicyV2
    regime_run_id: UUID
    regime_content_hash: str
    covariance: CovarianceEstimateV2
    stressed_covariance: tuple[tuple[Decimal, ...], ...]
    sleeves: tuple[StrategySleeveInputV2, ...]
    data_health_status: str
    data_health_assessment_ids: tuple[UUID, ...]
    constructed_at: datetime
    status: ConstructionStatus
    reasons: tuple[str, ...]
    constraints: tuple[ConstraintEvaluationV2, ...]
    target: PortfolioTargetCandidateV2
    risk_gate: PortfolioRiskGateEvidenceV2
    limitations: tuple[str, ...] = LIMITATIONS
    run_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.constructed_at, "portfolio_run_constructed_at")
        if len(self.regime_content_hash) != 64 or not self.sleeves or not self.data_health_assessment_ids:
            raise PortfolioConstructionV2Error("invalid_portfolio_construction_run")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "run_id", uuid5(NAMESPACE_URL, f"portfolio-construction-run:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "policy_version_id": str(self.policy.policy_version_id),
            "regime_run_id": str(self.regime_run_id),
            "regime_content_hash": self.regime_content_hash,
            "covariance": self.covariance.payload(),
            "stressed_covariance": _matrix_payload(self.stressed_covariance),
            "sleeves": tuple(item.payload() for item in self.sleeves),
            "data_health_status": self.data_health_status,
            "data_health_assessment_ids": tuple(str(item) for item in self.data_health_assessment_ids),
            "constructed_at": self.constructed_at,
            "status": self.status.value,
            "reasons": self.reasons,
            "constraints": tuple(item.payload() for item in self.constraints),
            "target": self.target.payload(),
            "risk_gate": self.risk_gate.payload(),
            "limitations": self.limitations,
        }


def _capped_risk_weights(
    sleeves: tuple[StrategySleeveInputV2, ...], maximum_weight: Decimal
) -> dict[str, Decimal]:
    scores = {
        item.strategy_key: item.risk_budget / item.standalone_volatility * abs(item.normalized_signal)
        for item in sleeves
        if item.normalized_signal != 0
    }
    result = {item.strategy_key: Decimal("0") for item in sleeves}
    active = dict(scores)
    remaining = Decimal("1")
    while active and remaining > 0:
        total = sum(active.values(), Decimal("0"))
        over = tuple(
            key for key, score in active.items() if remaining * score / total > maximum_weight
        )
        if not over:
            for key, score in active.items():
                result[key] = remaining * score / total
            break
        for key in over:
            result[key] = maximum_weight
            remaining -= maximum_weight
            del active[key]
    return result


def _portfolio_volatility(weights: tuple[Decimal, ...], matrix: tuple[tuple[Decimal, ...], ...]) -> Decimal:
    variance = sum(
        (weights[row] * weights[column] * matrix[row][column]
         for row in range(len(weights)) for column in range(len(weights))),
        Decimal("0"),
    )
    if variance < Decimal("-0.000000000001"):
        raise PortfolioConstructionV2Error("covariance_not_positive_semidefinite_for_weights")
    return max(variance, Decimal("0")).sqrt()


def _effective_weights(
    sleeves: tuple[StrategySleeveInputV2, ...], targets: dict[str, Decimal], scale: Decimal
) -> dict[str, Decimal]:
    return {
        item.strategy_key: targets[item.strategy_key] * scale * item.delta_adjusted_multiplier
        + item.pending_order_weight
        + item.fx_weight
        for item in sleeves
    }


def _aggregate_constraints_satisfied(
    policy: PortfolioConstructionPolicyV2,
    sleeves: tuple[StrategySleeveInputV2, ...],
    effective: dict[str, Decimal],
) -> bool:
    gross = sum((abs(value) for value in effective.values()), Decimal("0"))
    net = sum(effective.values(), Decimal("0"))
    if gross > policy.maximum_gross_leverage or abs(net) > policy.maximum_net_leverage:
        return False
    groups: dict[str, Decimal] = {}
    factors: dict[str, Decimal] = {}
    for item in sleeves:
        value = effective[item.strategy_key]
        for name, group_value in item.groups:
            key = f"{name}:{group_value}"
            groups[key] = groups.get(key, Decimal("0")) + value
        for name, loading in item.factor_loadings:
            factors[name] = factors.get(name, Decimal("0")) + value * loading
    return all(
        abs(groups.get(key, Decimal("0"))) <= limit
        for key, limit in policy.group_weight_limits
    ) and all(
        abs(factors.get(key, Decimal("0"))) <= limit
        for key, limit in policy.factor_exposure_limits
    )


def _maximum_safe_scale(
    policy: PortfolioConstructionPolicyV2,
    sleeves: tuple[StrategySleeveInputV2, ...],
    targets: dict[str, Decimal],
) -> tuple[Decimal, bool]:
    if not _aggregate_constraints_satisfied(policy, sleeves, _effective_weights(sleeves, targets, Decimal("0"))):
        return Decimal("0"), False
    if _aggregate_constraints_satisfied(policy, sleeves, _effective_weights(sleeves, targets, Decimal("1"))):
        return Decimal("1"), True
    low, high = Decimal("0"), Decimal("1")
    for _ in range(64):
        midpoint = (low + high) / Decimal("2")
        if _aggregate_constraints_satisfied(policy, sleeves, _effective_weights(sleeves, targets, midpoint)):
            low = midpoint
        else:
            high = midpoint
    return low, True


def _exposures(
    policy: PortfolioConstructionPolicyV2,
    sleeves: tuple[StrategySleeveInputV2, ...],
    effective: dict[str, Decimal],
) -> list[PortfolioExposure]:
    result: list[PortfolioExposure] = []
    for item in sleeves:
        groups = dict(item.groups)
        result.append(
            PortfolioExposure(
                instrument_id=f"SLEEVE:{item.strategy_key}",
                notional=effective[item.strategy_key] * policy.equity,
                asset_class=groups.get("asset_class", "UNAVAILABLE"),
                sector=groups.get("sector", "UNAVAILABLE"),
                country=groups.get("country", ""),
                currency=groups.get("currency", ""),
                broker=groups.get("broker", ""),
                exchange=groups.get("exchange", ""),
                correlation_cluster=groups.get("correlation_cluster", ""),
                factors=dict(item.factor_loadings),
            )
        )
    return result


def construct_multi_strategy_portfolio(
    policy: PortfolioConstructionPolicyV2,
    *,
    regime_run_id: UUID,
    regime_content_hash: str,
    covariance: CovarianceEstimateV2,
    sleeves: tuple[StrategySleeveInputV2, ...],
    data_health_status: str,
    data_health_assessment_ids: tuple[UUID, ...],
    constructed_at: datetime,
) -> PortfolioConstructionRunV2:
    _aware(constructed_at, "portfolio_constructed_at")
    ordered = tuple(sorted(sleeves, key=lambda item: item.strategy_key))
    if not ordered or len({item.strategy_key for item in ordered}) != len(ordered):
        raise PortfolioConstructionV2Error("missing_or_duplicate_strategy_sleeve")
    if tuple(item.strategy_key for item in ordered) != covariance.strategy_keys:
        raise PortfolioConstructionV2Error("covariance_strategy_binding_mismatch")
    if covariance.as_of > constructed_at:
        raise PortfolioConstructionV2Error("future_covariance_rejected")
    if len(regime_content_hash) != 64 or not data_health_assessment_ids:
        raise PortfolioConstructionV2Error("missing_portfolio_authority_binding")

    blocking_reasons: list[str] = []
    constraints: list[ConstraintEvaluationV2] = []
    if data_health_status in {"BLOCKED", "UNAVAILABLE"}:
        blocking_reasons.append("portfolio_data_health_blocked")
        constraints.append(
            ConstraintEvaluationV2(
                "data_health", ConstraintState.BLOCKED, None, None,
                ("portfolio_data_health_blocked",),
            )
        )
    else:
        constraints.append(ConstraintEvaluationV2("data_health", ConstraintState.SATISFIED, None, None, ()))

    adjustment_reasons: dict[str, list[str]] = {item.strategy_key: [] for item in ordered}
    for item in ordered:
        reasons: list[str] = []
        if item.scorecard_status is not ConstructionStatus.REVIEW_REQUIRED:
            reasons.append("scorecard_not_review_eligible")
        if item.validation_integrity != "VERIFIED":
            reasons.append("validation_package_not_verified")
        if item.regime_status is not ConstructionStatus.REVIEW_REQUIRED:
            reasons.append("regime_candidate_blocked")
        if item.regime_proposed_multiplier > item.regime_current_multiplier:
            reasons.append("regime_risk_increase_prohibited")
        if reasons:
            blocking_reasons.extend(f"{item.strategy_key}:{reason}" for reason in reasons)
            constraints.append(
                ConstraintEvaluationV2(
                    f"eligibility:{item.strategy_key}", ConstraintState.BLOCKED,
                    None, None, tuple(reasons),
                )
            )
        else:
            constraints.append(
                ConstraintEvaluationV2(
                    f"eligibility:{item.strategy_key}", ConstraintState.SATISFIED,
                    None, None, (),
                )
            )

    magnitudes = (
        {item.strategy_key: Decimal("0") for item in ordered}
        if blocking_reasons
        else _capped_risk_weights(ordered, policy.maximum_sleeve_weight)
    )
    targets: dict[str, Decimal] = {}
    for item in ordered:
        direction = Decimal("1") if item.normalized_signal > 0 else Decimal("-1") if item.normalized_signal < 0 else Decimal("0")
        regime_multiplier = min(
            Decimal("1"), item.regime_proposed_multiplier / item.regime_current_multiplier
        )
        magnitude = magnitudes[item.strategy_key] * regime_multiplier * item.capacity_weight
        if regime_multiplier < 1:
            adjustment_reasons[item.strategy_key].append("regime_reduction")
        if item.capacity_weight < 1:
            adjustment_reasons[item.strategy_key].append("capacity_reduction")
        if item.liquidity_score < policy.minimum_liquidity_score:
            magnitude = Decimal("0")
            adjustment_reasons[item.strategy_key].append("liquidity_disabled")
        if item.drawdown > policy.maximum_drawdown:
            magnitude = Decimal("0")
            adjustment_reasons[item.strategy_key].append("drawdown_disabled")
        if item.decay_score < policy.minimum_decay_score:
            magnitude = Decimal("0")
            adjustment_reasons[item.strategy_key].append("signal_decay_disabled")
        targets[item.strategy_key] = direction * magnitude

    stressed_covariance = covariance.stressed(policy)
    target_vector = tuple(targets[key] for key in covariance.strategy_keys)
    stressed_volatility = _portfolio_volatility(target_vector, stressed_covariance)
    volatility_scale = min(
        Decimal("1"),
        policy.target_volatility / stressed_volatility if stressed_volatility > 0 else Decimal("1"),
    )
    if volatility_scale < 1:
        for key in targets:
            targets[key] *= volatility_scale
            adjustment_reasons[key].append("target_volatility_reduction")
    constraints.append(
        ConstraintEvaluationV2(
            "stressed_target_volatility",
            ConstraintState.REDUCED if volatility_scale < 1 else ConstraintState.SATISFIED,
            stressed_volatility,
            policy.target_volatility,
            ("correlation_uncertainty_can_only_reduce",) if volatility_scale < 1 else (),
        )
    )

    aggregate_scale, base_feasible = _maximum_safe_scale(policy, ordered, targets)
    if not base_feasible:
        blocking_reasons.append("pending_or_fx_hidden_leverage_exceeds_policy")
    if aggregate_scale < 1:
        for key in targets:
            targets[key] *= aggregate_scale
            adjustment_reasons[key].append("aggregate_constraint_reduction")
    effective = _effective_weights(ordered, targets, Decimal("1"))
    aggregate_ok = _aggregate_constraints_satisfied(policy, ordered, effective)
    if not aggregate_ok:
        blocking_reasons.append("aggregate_constraints_not_satisfied")
    constraints.append(
        ConstraintEvaluationV2(
            "aggregate_exposure_limits",
            ConstraintState.BLOCKED if not aggregate_ok else ConstraintState.REDUCED if aggregate_scale < 1 else ConstraintState.SATISFIED,
            sum((abs(value) for value in effective.values()), Decimal("0")),
            policy.maximum_gross_leverage,
            tuple(reason for reason in blocking_reasons if "aggregate" in reason or "hidden" in reason),
        )
    )

    final_vector = tuple(targets[key] for key in covariance.strategy_keys)
    final_raw_volatility = _portfolio_volatility(final_vector, covariance.matrix)
    final_stressed_volatility = _portfolio_volatility(final_vector, stressed_covariance)
    exposures = _exposures(policy, ordered, effective)
    stress_scenario = StressScenario(
        "portfolio-construction-policy-stress",
        {item.instrument_id: policy.stress_shock for item in exposures},
    )
    risk_policy = PortfolioRiskPolicy(
        maximum_gross_notional=policy.equity * policy.maximum_gross_leverage,
        maximum_single_weight=policy.maximum_sleeve_weight,
        maximum_scenario_loss=policy.equity * policy.maximum_stress_loss_weight,
        maximum_net_notional=policy.equity * policy.maximum_net_leverage,
        maximum_group_notionals=tuple_to_dict_scaled(policy.group_weight_limits, policy.equity),
        maximum_factor_notionals=tuple_to_dict_scaled(policy.factor_exposure_limits, policy.equity),
    )
    decision = evaluate_portfolio(exposures, policy.equity, stress_scenario, risk_policy)
    if not decision.approved:
        blocking_reasons.extend(f"independent_risk_gate:{reason}" for reason in decision.reasons)
    risk_gate = PortfolioRiskGateEvidenceV2(
        decision.approved,
        decision.gross,
        decision.net,
        decision.stress_loss,
        decision.reasons,
        {
            "engine": "trade_platform.portfolio_risk.evaluate_portfolio",
            "stress_scenario": stress_scenario.name,
            "stress_results": tuple(
                {
                    "scenario": result.scenario,
                    "loss": _decimal(result.loss),
                    "contributions": tuple(
                        (key, _decimal(value)) for key, value in sorted(result.contributions.items())
                    ),
                }
                for result in decision.stress_results
            ),
        },
    )
    constraints.append(
        ConstraintEvaluationV2(
            "independent_portfolio_risk_gate",
            ConstraintState.SATISFIED if decision.approved else ConstraintState.BLOCKED,
            decision.gross / policy.equity,
            policy.maximum_gross_leverage,
            decision.reasons,
        )
    )

    status = ConstructionStatus.BLOCKED if blocking_reasons else ConstructionStatus.REVIEW_REQUIRED
    covariance_product = tuple(
        sum((stressed_covariance[row][column] * final_vector[column] for column in range(len(final_vector))), Decimal("0"))
        for row in range(len(final_vector))
    )
    target_sleeves: list[TargetSleeveWeightV2] = []
    for index, item in enumerate(ordered):
        marginal = covariance_product[index] / final_stressed_volatility if final_stressed_volatility > 0 else Decimal("0")
        component = final_vector[index] * marginal
        target_sleeves.append(
            TargetSleeveWeightV2(
                item.sleeve_input_id,
                item.strategy_key,
                targets[item.strategy_key],
                effective[item.strategy_key] * policy.equity,
                marginal,
                component,
                tuple(adjustment_reasons[item.strategy_key]),
            )
        )
    gross_weight = sum((abs(value) for value in effective.values()), Decimal("0"))
    net_weight = sum(effective.values(), Decimal("0"))
    target = PortfolioTargetCandidateV2(
        status,
        Decimal("1") - net_weight,
        gross_weight,
        net_weight,
        final_raw_volatility,
        final_stressed_volatility,
        constructed_at,
        tuple(target_sleeves),
    )
    return PortfolioConstructionRunV2(
        policy,
        regime_run_id,
        regime_content_hash,
        covariance,
        stressed_covariance,
        ordered,
        data_health_status,
        data_health_assessment_ids,
        constructed_at,
        status,
        tuple(dict.fromkeys(blocking_reasons)),
        tuple(constraints),
        target,
        risk_gate,
    )


def tuple_to_dict_scaled(items: tuple[tuple[str, Decimal], ...], scale: Decimal) -> dict[str, Decimal]:
    return {key: value * scale for key, value in items}


class PostgresPortfolioConstructionV2Store:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def publish_policy(self, policy: PortfolioConstructionPolicyV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO portfolio_construction_policy_versions VALUES "
                    "(%s,%s,%s,%s,%s,%s::jsonb,FALSE,%s,%s) "
                    "ON CONFLICT (policy_version_id) DO NOTHING RETURNING content_hash",
                    (
                        policy.policy_version_id, policy.policy_id, policy.version,
                        policy.implementation_version, policy.status,
                        _canonical(policy.payload()), policy.created_at, policy.content_hash,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "SELECT content_hash FROM portfolio_construction_policy_versions WHERE policy_version_id=%s",
                        (policy.policy_version_id,),
                    )
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != policy.content_hash:
                        raise PortfolioConstructionV2Error("portfolio_policy_replay_conflict")
            return policy.policy_version_id
        except PortfolioConstructionV2Error:
            raise
        except Exception as error:
            raise PortfolioConstructionV2Error("portfolio_policy_publication_failed") from error

    def _verify_sleeve(self, cursor: _Cursor, run: PortfolioConstructionRunV2, sleeve: StrategySleeveInputV2) -> None:
        cursor.execute(
            "SELECT sc.status,sc.dataset_health_status,p.integrity_status,"
            "rc.run_id,rc.strategy_version_id,rc.current_risk_multiplier,"
            "rc.proposed_risk_multiplier,rc.status,rc.automatic_authority "
            "FROM strategy_versions sv "
            "JOIN strategy_scorecards sc ON sc.scorecard_id=%s "
            "AND sc.strategy_id=sv.strategy_id AND sc.strategy_version=sv.version "
            "JOIN scorecard_validation_packages l ON l.scorecard_id=sc.scorecard_id AND l.package_id=%s "
            "JOIN validation_packages p ON p.package_id=l.package_id "
            "JOIN regime_risk_adjustment_candidates rc ON rc.candidate_id=%s "
            "WHERE sv.strategy_version_id=%s",
            (
                sleeve.scorecard_id, sleeve.validation_package_id,
                sleeve.regime_candidate_id, sleeve.strategy_version_id,
            ),
        )
        row = cursor.fetchone()
        valid = bool(
            row
            and str(row[0]) == sleeve.scorecard_status.value
            and str(row[1]) != "BLOCKED"
            and str(row[2]) == sleeve.validation_integrity
            and UUID(str(row[3])) == run.regime_run_id
            and UUID(str(row[4])) == sleeve.strategy_version_id
            and Decimal(str(row[5])) == sleeve.regime_current_multiplier
            and Decimal(str(row[6])) == sleeve.regime_proposed_multiplier
            and str(row[7]) == sleeve.regime_status.value
            and row[8] is False
        )
        if not valid:
            raise PortfolioConstructionV2Error("portfolio_sleeve_authority_binding_mismatch")

    def publish_run(self, run: PortfolioConstructionRunV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT content_hash,status FROM regime_runs WHERE run_id=%s",
                    (run.regime_run_id,),
                )
                regime = cursor.fetchone()
                if regime is None or str(regime[0]) != run.regime_content_hash or str(regime[1]) == "BLOCKED":
                    raise PortfolioConstructionV2Error("portfolio_regime_run_binding_mismatch")
                for assessment_id in run.data_health_assessment_ids:
                    cursor.execute(
                        "SELECT dataset_version_id,blocking FROM data_health_assessments WHERE assessment_id=%s",
                        (assessment_id,),
                    )
                    health = cursor.fetchone()
                    if (
                        health is None
                        or UUID(str(health[0])) != run.covariance.dataset_version_id
                        or health[1] is True
                    ):
                        raise PortfolioConstructionV2Error("portfolio_data_health_binding_blocked")
                cursor.execute(
                    "SELECT version,content_hash,status FROM historical_dataset_versions WHERE dataset_version_id=%s",
                    (run.covariance.dataset_version_id,),
                )
                covariance_dataset = cursor.fetchone()
                if covariance_dataset is None or (
                    str(covariance_dataset[0]),
                    str(covariance_dataset[1]),
                    str(covariance_dataset[2]),
                ) != (
                    run.covariance.dataset_version,
                    run.covariance.dataset_content_hash,
                    "SEALED",
                ):
                    raise PortfolioConstructionV2Error("portfolio_covariance_dataset_binding_mismatch")
                for sleeve in run.sleeves:
                    self._verify_sleeve(cursor, run, sleeve)
                cursor.execute(
                    "INSERT INTO portfolio_construction_runs VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s) "
                    "ON CONFLICT (run_id) DO NOTHING RETURNING content_hash",
                    (
                        run.run_id, run.policy.policy_version_id, run.regime_run_id,
                        run.regime_content_hash, run.constructed_at, run.status.value,
                        run.policy.equity, _canonical(run.limitations),
                        _canonical(run.payload()), run.content_hash,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "SELECT content_hash FROM portfolio_construction_runs WHERE run_id=%s",
                        (run.run_id,),
                    )
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != run.content_hash:
                        raise PortfolioConstructionV2Error("portfolio_run_replay_conflict")
                for assessment_id in run.data_health_assessment_ids:
                    cursor.execute(
                        "INSERT INTO portfolio_construction_run_data_health_assessments VALUES (%s,%s) ON CONFLICT DO NOTHING",
                        (run.run_id, assessment_id),
                    )
                for sequence, sleeve in enumerate(run.sleeves):
                    cursor.execute(
                        "INSERT INTO portfolio_sleeve_inputs VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (sleeve_input_id) DO NOTHING",
                        (
                            sleeve.sleeve_input_id, run.run_id, sequence,
                            sleeve.strategy_version_id, sleeve.scorecard_id,
                            sleeve.validation_package_id, sleeve.regime_candidate_id,
                            sleeve.strategy_key, _canonical(sleeve.payload()), sleeve.content_hash,
                        ),
                    )
                cursor.execute(
                    "INSERT INTO portfolio_covariance_estimates VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s) ON CONFLICT (covariance_id) DO NOTHING",
                    (
                        run.covariance.covariance_id, run.run_id,
                        run.covariance.dataset_version_id, run.covariance.dataset_version,
                        run.covariance.dataset_content_hash,
                        run.covariance.estimation_version, run.covariance.observations,
                        run.covariance.as_of, _canonical(_matrix_payload(run.covariance.matrix)),
                        _canonical(_matrix_payload(run.stressed_covariance)),
                        run.policy.covariance_uncertainty, run.policy.correlation_stress,
                        run.covariance.content_hash,
                    ),
                )
                cursor.execute(
                    "INSERT INTO portfolio_target_candidates VALUES "
                    "(%s,%s,%s,FALSE,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (candidate_id) DO NOTHING",
                    (
                        run.target.candidate_id, run.run_id, run.target.status.value,
                        run.target.cash_weight, run.target.gross_weight, run.target.net_weight,
                        run.target.portfolio_volatility, run.target.stressed_volatility,
                        run.target.created_at, run.target.content_hash,
                    ),
                )
                for target in run.target.sleeves:
                    cursor.execute(
                        "INSERT INTO portfolio_target_sleeve_weights VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                        (
                            run.target.candidate_id, target.sleeve_input_id,
                            target.target_weight, target.effective_notional,
                            target.marginal_risk, target.component_risk,
                            _canonical(target.adjustment_reasons),
                        ),
                    )
                for constraint in run.constraints:
                    cursor.execute(
                        "INSERT INTO portfolio_constraint_evaluations VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (constraint_id) DO NOTHING",
                        (
                            constraint.constraint_id, run.run_id, constraint.name,
                            constraint.state.value, constraint.observed, constraint.limit,
                            _canonical(constraint.reasons), constraint.content_hash,
                        ),
                    )
                cursor.execute(
                    "INSERT INTO portfolio_risk_gate_evidence VALUES "
                    "(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,FALSE,%s) ON CONFLICT (run_id) DO NOTHING",
                    (
                        run.run_id, run.risk_gate.approved, run.risk_gate.gross_notional,
                        run.risk_gate.net_notional, run.risk_gate.stress_loss,
                        _canonical(run.risk_gate.reasons), _canonical(run.risk_gate.report),
                        run.risk_gate.content_hash,
                    ),
                )
                cursor.execute(
                    "SELECT sleeve_input_id FROM portfolio_sleeve_inputs WHERE run_id=%s ORDER BY sequence",
                    (run.run_id,),
                )
                if tuple(UUID(str(row[0])) for row in cursor.fetchall()) != tuple(
                    item.sleeve_input_id for item in run.sleeves
                ):
                    raise PortfolioConstructionV2Error("portfolio_sleeve_membership_conflict")
                cursor.execute(
                    "SELECT COUNT(*) FROM portfolio_target_sleeve_weights WHERE candidate_id=%s",
                    (run.target.candidate_id,),
                )
                if int(cursor.fetchone()[0]) != len(run.target.sleeves):
                    raise PortfolioConstructionV2Error("portfolio_target_membership_conflict")
            return run.run_id
        except PortfolioConstructionV2Error:
            raise
        except Exception as error:
            raise PortfolioConstructionV2Error("portfolio_run_publication_failed") from error
