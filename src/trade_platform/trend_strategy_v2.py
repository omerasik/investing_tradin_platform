"""Feature-authority-bound, research-only Trend V2 strategy definitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from .feature_authority import FeatureMaterialization, FeatureQualityStatus


class TrendStrategyV2Error(ValueError):
    pass


class TrendStrategyKind(StrEnum):
    TIME_SERIES_MOMENTUM = "TIME_SERIES_MOMENTUM"
    BREAKOUT = "BREAKOUT"
    MULTI_HORIZON_TREND = "MULTI_HORIZON_TREND"
    VOLATILITY_SCALED_TREND = "VOLATILITY_SCALED_TREND"


class TrendStrategyStatus(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY"


class TrendSignalState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class TrendFeatureRequirement:
    feature_id: UUID
    name: str
    semantic_version: str

    @property
    def version_key(self) -> str:
        return f"{self.name}:{self.semantic_version}"

    def validate(self) -> None:
        if not self.name.strip() or not self.semantic_version.strip():
            raise TrendStrategyV2Error("invalid_trend_feature_requirement")


@dataclass(frozen=True, slots=True)
class TrendStrategyDefinitionV2:
    strategy_id: UUID
    strategy_version: str
    implementation_version: str
    kind: TrendStrategyKind
    economic_hypothesis: str
    economic_rationale: str
    universe_requirements: tuple[str, ...]
    required_features: tuple[TrendFeatureRequirement, ...]
    required_dataset_type: str
    parameters: tuple[tuple[str, str], ...]
    entry_logic: str
    exit_logic: str
    invalidation_logic: str
    sizing_policy: str
    maximum_research_exposure: Decimal
    holding_horizon: str
    cost_model_requirement: str
    capacity_assumptions: str
    expected_failure_regimes: tuple[str, ...]
    limitations: tuple[str, ...]
    created_at: datetime
    status: TrendStrategyStatus = TrendStrategyStatus.RESEARCH_ONLY
    family: str = field(default="TREND", init=False)

    @property
    def feature_versions(self) -> tuple[str, ...]:
        return tuple(item.version_key for item in self.required_features)

    @property
    def strategy_version_id(self) -> UUID:
        return uuid5(NAMESPACE_URL, f"trend-strategy-version:{self.content_hash()}")

    def content_hash(self) -> str:
        payload = {
            "strategy_id": str(self.strategy_id),
            "strategy_version": self.strategy_version,
            "implementation_version": self.implementation_version,
            "family": self.family,
            "kind": self.kind.value,
            "economic_hypothesis": self.economic_hypothesis,
            "economic_rationale": self.economic_rationale,
            "universe_requirements": self.universe_requirements,
            "required_features": tuple(
                (str(item.feature_id), item.name, item.semantic_version)
                for item in self.required_features
            ),
            "required_dataset_type": self.required_dataset_type,
            "parameters": self.parameters,
            "entry_logic": self.entry_logic,
            "exit_logic": self.exit_logic,
            "invalidation_logic": self.invalidation_logic,
            "sizing_policy": self.sizing_policy,
            "maximum_research_exposure": str(self.maximum_research_exposure),
            "holding_horizon": self.holding_horizon,
            "cost_model_requirement": self.cost_model_requirement,
            "capacity_assumptions": self.capacity_assumptions,
            "expected_failure_regimes": self.expected_failure_regimes,
            "limitations": self.limitations,
            "created_at": self.created_at.isoformat(),
            "status": self.status.value,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def validate(self) -> None:
        text = (
            self.strategy_version,
            self.implementation_version,
            self.economic_hypothesis,
            self.economic_rationale,
            self.required_dataset_type,
            self.entry_logic,
            self.exit_logic,
            self.invalidation_logic,
            self.sizing_policy,
            self.holding_horizon,
            self.cost_model_requirement,
            self.capacity_assumptions,
        )
        if (
            self.family != "TREND"
            or self.status is not TrendStrategyStatus.RESEARCH_ONLY
            or not all(value.strip() for value in text)
            or not self.universe_requirements
            or not self.required_features
            or not self.parameters
            or not self.expected_failure_regimes
            or not self.limitations
            or self.maximum_research_exposure <= 0
            or self.maximum_research_exposure > 1
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise TrendStrategyV2Error("incomplete_trend_strategy_definition")
        for requirement in self.required_features:
            requirement.validate()
        if len(set(self.feature_versions)) != len(self.feature_versions):
            raise TrendStrategyV2Error("duplicate_trend_feature_requirement")
        if len(set(self.parameters)) != len(self.parameters):
            raise TrendStrategyV2Error("duplicate_trend_parameter")


@dataclass(frozen=True, slots=True)
class AuthoritativeFeatureSeries:
    requirement: TrendFeatureRequirement
    dataset_version: str
    materializations: tuple[FeatureMaterialization, ...]

    def validate(self) -> None:
        self.requirement.validate()
        if not self.dataset_version.strip() or not self.materializations:
            raise TrendStrategyV2Error("feature_series_unavailable")
        previous: datetime | None = None
        for item in self.materializations:
            item.validate()
            if item.feature_id != self.requirement.feature_id:
                raise TrendStrategyV2Error("feature_series_id_mismatch")
            if item.dataset_version != self.dataset_version:
                raise TrendStrategyV2Error("feature_series_dataset_mismatch")
            if previous is not None and item.event_at <= previous:
                raise TrendStrategyV2Error("feature_series_not_chronological")
            previous = item.event_at


@dataclass(frozen=True, slots=True)
class TrendSignalObservation:
    event_at: datetime
    exposure: Decimal | None
    state: TrendSignalState
    reason: str | None

    def validate(self, maximum_exposure: Decimal) -> None:
        if self.event_at.tzinfo is None or self.event_at.utcoffset() is None:
            raise TrendStrategyV2Error("trend_signal_timestamp_must_be_aware")
        if self.state is TrendSignalState.UNAVAILABLE:
            if self.exposure is not None or not self.reason:
                raise TrendStrategyV2Error("invalid_unavailable_trend_signal")
            return
        if self.exposure is None or self.reason is not None:
            raise TrendStrategyV2Error("invalid_available_trend_signal")
        if not Decimal("0") <= self.exposure <= maximum_exposure:
            raise TrendStrategyV2Error("trend_signal_exposure_out_of_bounds")


@dataclass(frozen=True, slots=True)
class TrendSignalSeries:
    strategy_version: str
    dataset_version: str
    feature_versions: tuple[str, ...]
    observations: tuple[TrendSignalObservation, ...]
    maximum_exposure: Decimal

    @property
    def eligible(self) -> bool:
        return bool(self.observations) and all(
            item.state is TrendSignalState.AVAILABLE for item in self.observations
        )

    def require_available(self) -> tuple[Decimal, ...]:
        if not self.eligible:
            raise TrendStrategyV2Error("trend_signal_series_unavailable")
        values: list[Decimal] = []
        for item in self.observations:
            if item.exposure is None:
                raise TrendStrategyV2Error("trend_signal_series_unavailable")
            values.append(item.exposure)
        return tuple(values)

    def validate(self) -> None:
        if not self.strategy_version.strip() or not self.dataset_version.strip():
            raise TrendStrategyV2Error("trend_signal_identity_missing")
        if not self.feature_versions or not self.observations:
            raise TrendStrategyV2Error("trend_signal_evidence_missing")
        previous: datetime | None = None
        for observation in self.observations:
            observation.validate(self.maximum_exposure)
            if previous is not None and observation.event_at <= previous:
                raise TrendStrategyV2Error("trend_signals_not_chronological")
            previous = observation.event_at


def _unavailable(event_at: datetime, reason: str) -> TrendSignalObservation:
    return TrendSignalObservation(event_at, None, TrendSignalState.UNAVAILABLE, reason)


def _available(event_at: datetime, exposure: Decimal) -> TrendSignalObservation:
    return TrendSignalObservation(event_at, exposure, TrendSignalState.AVAILABLE, None)


def _validated_value(item: FeatureMaterialization) -> Decimal | None:
    if item.quality_status is not FeatureQualityStatus.VALIDATED:
        return None
    return item.value


def _single_feature_signals(
    definition: TrendStrategyDefinitionV2,
    feature: AuthoritativeFeatureSeries,
    transform: Callable[[Decimal], Decimal],
) -> TrendSignalSeries:
    definition.validate()
    feature.validate()
    if feature.requirement not in definition.required_features:
        raise TrendStrategyV2Error("strategy_feature_mismatch")
    observations: list[TrendSignalObservation] = []
    for item in feature.materializations:
        value = _validated_value(item)
        if value is None:
            observations.append(_unavailable(item.event_at, "required_feature_unavailable"))
        else:
            observations.append(_available(item.event_at, transform(value)))
    result = TrendSignalSeries(
        definition.strategy_version,
        feature.dataset_version,
        definition.feature_versions,
        tuple(observations),
        definition.maximum_research_exposure,
    )
    result.validate()
    return result


def time_series_momentum_signals(
    definition: TrendStrategyDefinitionV2,
    feature: AuthoritativeFeatureSeries,
    *,
    threshold: Decimal = Decimal("0"),
) -> TrendSignalSeries:
    if definition.kind is not TrendStrategyKind.TIME_SERIES_MOMENTUM:
        raise TrendStrategyV2Error("wrong_trend_strategy_kind")
    return _single_feature_signals(
        definition,
        feature,
        lambda value: definition.maximum_research_exposure if value > threshold else Decimal("0"),
    )


def breakout_signals(
    definition: TrendStrategyDefinitionV2,
    feature: AuthoritativeFeatureSeries,
) -> TrendSignalSeries:
    if definition.kind is not TrendStrategyKind.BREAKOUT:
        raise TrendStrategyV2Error("wrong_trend_strategy_kind")
    return _single_feature_signals(
        definition,
        feature,
        lambda value: definition.maximum_research_exposure if value >= 0 else Decimal("0"),
    )


def multi_horizon_trend_signals(
    definition: TrendStrategyDefinitionV2,
    feature: AuthoritativeFeatureSeries,
) -> TrendSignalSeries:
    if definition.kind is not TrendStrategyKind.MULTI_HORIZON_TREND:
        raise TrendStrategyV2Error("wrong_trend_strategy_kind")
    return _single_feature_signals(
        definition,
        feature,
        lambda value: min(
            definition.maximum_research_exposure,
            max(Decimal("0"), value * definition.maximum_research_exposure),
        ),
    )


def volatility_scaled_trend_exposure(
    definition: TrendStrategyDefinitionV2,
    momentum: AuthoritativeFeatureSeries,
    volatility: AuthoritativeFeatureSeries,
    *,
    target_volatility: Decimal,
) -> TrendSignalSeries:
    if definition.kind is not TrendStrategyKind.VOLATILITY_SCALED_TREND:
        raise TrendStrategyV2Error("wrong_trend_strategy_kind")
    if target_volatility <= 0:
        raise TrendStrategyV2Error("invalid_target_volatility")
    definition.validate()
    momentum.validate()
    volatility.validate()
    if momentum.dataset_version != volatility.dataset_version:
        raise TrendStrategyV2Error("feature_series_dataset_mismatch")
    if {momentum.requirement, volatility.requirement} != set(definition.required_features):
        raise TrendStrategyV2Error("strategy_feature_mismatch")
    by_time = {item.event_at: item for item in volatility.materializations}
    observations: list[TrendSignalObservation] = []
    for momentum_item in momentum.materializations:
        volatility_item = by_time.get(momentum_item.event_at)
        momentum_value = _validated_value(momentum_item)
        volatility_value = None if volatility_item is None else _validated_value(volatility_item)
        if momentum_value is None or volatility_value is None:
            observations.append(
                _unavailable(momentum_item.event_at, "required_feature_unavailable")
            )
        elif momentum_value <= 0:
            observations.append(_available(momentum_item.event_at, Decimal("0")))
        elif volatility_value <= 0:
            observations.append(
                _unavailable(momentum_item.event_at, "non_positive_realized_volatility")
            )
        else:
            observations.append(
                _available(
                    momentum_item.event_at,
                    min(definition.maximum_research_exposure, target_volatility / volatility_value),
                )
            )
    result = TrendSignalSeries(
        definition.strategy_version,
        momentum.dataset_version,
        definition.feature_versions,
        tuple(observations),
        definition.maximum_research_exposure,
    )
    result.validate()
    return result
