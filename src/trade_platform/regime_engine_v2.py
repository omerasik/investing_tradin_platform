"""Feature-bound, multidimensional, research-only market-regime evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .feature_authority import (
    FeatureDefinitionVersion,
    FeatureMaterialization,
    FeatureQualityStatus,
)
from .persistence import PostgresDatabase


class RegimeEngineV2Error(ValueError):
    pass


class RegimeMethod(StrEnum):
    RULE_BASED = "RULE_BASED"
    PROBABILISTIC_ENSEMBLE = "PROBABILISTIC_ENSEMBLE"


class RegimeDimension(StrEnum):
    TREND = "TREND"
    VOLATILITY_LEVEL = "VOLATILITY_LEVEL"
    VOLATILITY_DIRECTION = "VOLATILITY_DIRECTION"
    LIQUIDITY_LEVEL = "LIQUIDITY_LEVEL"
    LIQUIDITY_DIRECTION = "LIQUIDITY_DIRECTION"
    RISK_APPETITE = "RISK_APPETITE"
    MACRO_CYCLE = "MACRO_CYCLE"
    STRESS = "STRESS"


class RegimeState(StrEnum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    VOLATILITY_CONTRACTION = "VOLATILITY_CONTRACTION"
    HIGH_LIQUIDITY = "HIGH_LIQUIDITY"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    TIGHTENING_LIQUIDITY = "TIGHTENING_LIQUIDITY"
    EASING_LIQUIDITY = "EASING_LIQUIDITY"
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    INFLATIONARY_GROWTH = "INFLATIONARY_GROWTH"
    INFLATIONARY_CONTRACTION = "INFLATIONARY_CONTRACTION"
    DISINFLATIONARY_GROWTH = "DISINFLATIONARY_GROWTH"
    DEFLATIONARY_CONTRACTION = "DEFLATIONARY_CONTRACTION"
    NORMAL_STRESS = "NORMAL_STRESS"
    CREDIT_STRESS = "CREDIT_STRESS"
    CURRENCY_STRESS = "CURRENCY_STRESS"
    COMMODITY_SHOCK = "COMMODITY_SHOCK"
    CORRELATION_BREAKDOWN = "CORRELATION_BREAKDOWN"
    CRISIS = "CRISIS"


class EvidenceState(StrEnum):
    MEASURED = "MEASURED"
    UNAVAILABLE = "UNAVAILABLE"


class ReviewStatus(StrEnum):
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class RiskAdjustmentAction(StrEnum):
    DISABLE = "DISABLE"
    REDUCE = "REDUCE"
    MAINTAIN = "MAINTAIN"
    INCREASE = "INCREASE"


LIMITATIONS = (
    "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",
    "RULE_AND_TRANSPARENT_ENSEMBLE_METHODS_ONLY",
    "NO_AUTOMATIC_STRATEGY_OR_RISK_AUTHORITY",
)


DIMENSION_STATES: dict[RegimeDimension, tuple[RegimeState, ...]] = {
    RegimeDimension.TREND: (RegimeState.BULL_TREND, RegimeState.BEAR_TREND, RegimeState.SIDEWAYS),
    RegimeDimension.VOLATILITY_LEVEL: (RegimeState.HIGH_VOLATILITY, RegimeState.LOW_VOLATILITY),
    RegimeDimension.VOLATILITY_DIRECTION: (RegimeState.VOLATILITY_EXPANSION, RegimeState.VOLATILITY_CONTRACTION),
    RegimeDimension.LIQUIDITY_LEVEL: (RegimeState.HIGH_LIQUIDITY, RegimeState.LOW_LIQUIDITY),
    RegimeDimension.LIQUIDITY_DIRECTION: (RegimeState.TIGHTENING_LIQUIDITY, RegimeState.EASING_LIQUIDITY),
    RegimeDimension.RISK_APPETITE: (RegimeState.RISK_ON, RegimeState.RISK_OFF),
    RegimeDimension.MACRO_CYCLE: (
        RegimeState.INFLATIONARY_GROWTH,
        RegimeState.INFLATIONARY_CONTRACTION,
        RegimeState.DISINFLATIONARY_GROWTH,
        RegimeState.DEFLATIONARY_CONTRACTION,
    ),
    RegimeDimension.STRESS: (
        RegimeState.NORMAL_STRESS,
        RegimeState.CREDIT_STRESS,
        RegimeState.CURRENCY_STRESS,
        RegimeState.COMMODITY_SHOCK,
        RegimeState.CORRELATION_BREAKDOWN,
        RegimeState.CRISIS,
    ),
}


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RegimeEngineV2Error(f"{name}_must_be_timezone_aware")


def _decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise RegimeEngineV2Error("non_finite_regime_value")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _clamp(value: Decimal, low: Decimal = Decimal("-1"), high: Decimal = Decimal("1")) -> Decimal:
    return min(high, max(low, value))


@dataclass(frozen=True, slots=True)
class RegimeFeatureRequirement:
    role: str
    feature_id: UUID
    semantic_version: str
    required: bool = True

    def validate(self) -> None:
        if not self.role.strip() or not self.semantic_version.strip():
            raise RegimeEngineV2Error("invalid_regime_feature_requirement")


@dataclass(frozen=True, slots=True)
class RegimeModelDefinitionV2:
    model_id: UUID
    version: str
    implementation_version: str
    trained_through: datetime
    created_at: datetime
    feature_requirements: tuple[RegimeFeatureRequirement, ...]
    methods: tuple[RegimeMethod, ...] = (
        RegimeMethod.RULE_BASED,
        RegimeMethod.PROBABILISTIC_ENSEMBLE,
    )
    status: str = "RESEARCH_ONLY"
    limitations: tuple[str, ...] = LIMITATIONS
    model_version_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.trained_through, "regime_trained_through")
        _aware(self.created_at, "regime_definition_created_at")
        for requirement in self.feature_requirements:
            requirement.validate()
        roles = tuple(item.role for item in self.feature_requirements)
        if (
            not self.version.strip()
            or not self.implementation_version.strip()
            or self.trained_through > self.created_at
            or len(set(roles)) != len(roles)
            or not self.feature_requirements
            or set(self.methods) != set(RegimeMethod)
            or self.status != "RESEARCH_ONLY"
            or not self.limitations
        ):
            raise RegimeEngineV2Error("invalid_regime_model_definition")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "model_version_id", uuid5(NAMESPACE_URL, f"regime-model:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "model_id": str(self.model_id), "version": self.version,
            "implementation_version": self.implementation_version,
            "trained_through": self.trained_through, "created_at": self.created_at,
            "feature_requirements": tuple(
                {"role": item.role, "feature_id": str(item.feature_id),
                 "semantic_version": item.semantic_version, "required": item.required}
                for item in self.feature_requirements
            ),
            "methods": tuple(item.value for item in self.methods),
            "method_availability": {
                "RULE_BASED": "IMPLEMENTED",
                "PROBABILISTIC_ENSEMBLE": "IMPLEMENTED",
                "HIDDEN_MARKOV_MODEL": "UNAVAILABLE_NO_TRAINED_MODEL",
                "CHANGE_POINT": "UNAVAILABLE_NO_VALIDATED_CALIBRATION",
                "CLUSTERING": "UNAVAILABLE_NO_VALIDATED_CALIBRATION",
                "BAYESIAN_STATE": "UNAVAILABLE_NO_TRAINED_MODEL",
                "TREE_CLASSIFIER": "UNAVAILABLE_NO_TRAINED_MODEL",
            },
            "status": self.status, "limitations": self.limitations,
            "thresholds": {
                "trend": "0.05", "volatility": "0.02", "liquidity": "1000000",
                "stress": "0.5",
            },
        }


@dataclass(frozen=True, slots=True)
class RegimeOutcomeLabel:
    event_at: datetime
    dimension: RegimeDimension
    state: RegimeState
    knowledge_at: datetime
    source_reference: str
    label_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.event_at, "regime_label_event_at")
        _aware(self.knowledge_at, "regime_label_knowledge_at")
        if (
            self.state not in DIMENSION_STATES[self.dimension]
            or self.knowledge_at <= self.event_at
            or not self.source_reference.strip()
        ):
            raise RegimeEngineV2Error("invalid_regime_outcome_label")
        content_hash = _digest(
            {"event_at": self.event_at, "dimension": self.dimension.value,
             "state": self.state.value, "knowledge_at": self.knowledge_at,
             "source_reference": self.source_reference}
        )
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "label_id", uuid5(NAMESPACE_URL, f"regime-label:{content_hash}"))


@dataclass(frozen=True, slots=True)
class RegimeObservationV2:
    event_at: datetime
    method: RegimeMethod
    dimension: RegimeDimension
    evidence_state: EvidenceState
    probabilities: tuple[tuple[RegimeState, Decimal], ...]
    hard_label: RegimeState | None
    uncertainty: Decimal | None
    input_materialization_ids: tuple[UUID, ...]
    content_hash: str = field(init=False)
    observation_id: UUID = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.event_at, "regime_observation_event_at")
        expected = set(DIMENSION_STATES[self.dimension])
        actual = {name for name, _ in self.probabilities}
        values = tuple(value for _, value in self.probabilities)
        if self.evidence_state is EvidenceState.UNAVAILABLE:
            if self.probabilities or self.hard_label is not None or self.uncertainty is not None or self.input_materialization_ids:
                raise RegimeEngineV2Error("unavailable_regime_has_evidence")
        elif (
            actual != expected
            or any(value < 0 or value > 1 for value in values)
            or sum(values, Decimal("0")) != Decimal("1")
            or self.hard_label not in expected
            or self.uncertainty != Decimal("1") - max(values)
            or not self.input_materialization_ids
        ):
            raise RegimeEngineV2Error("invalid_regime_probability_evidence")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "observation_id", uuid5(NAMESPACE_URL, f"regime-observation:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "event_at": self.event_at, "method": self.method.value,
            "dimension": self.dimension.value, "evidence_state": self.evidence_state.value,
            "probabilities": tuple((name.value, _decimal(value)) for name, value in self.probabilities),
            "hard_label": None if self.hard_label is None else self.hard_label.value,
            "uncertainty": None if self.uncertainty is None else _decimal(self.uncertainty),
            "input_materialization_ids": tuple(str(value) for value in self.input_materialization_ids),
        }


@dataclass(frozen=True, slots=True)
class RegimeMethodEvaluationV2:
    method: RegimeMethod
    dimension: RegimeDimension
    sample_count: int
    brier_score: Decimal | None
    accuracy: Decimal | None
    evidence_state: EvidenceState
    label_ids: tuple[UUID, ...]
    content_hash: str = field(init=False)
    evaluation_id: UUID = field(init=False)

    def __post_init__(self) -> None:
        if self.sample_count < 0 or (self.evidence_state is EvidenceState.UNAVAILABLE) != (self.brier_score is None):
            raise RegimeEngineV2Error("invalid_regime_method_evaluation")
        if self.evidence_state is EvidenceState.MEASURED and (self.accuracy is None or not self.label_ids):
            raise RegimeEngineV2Error("measured_regime_evaluation_missing_evidence")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "evaluation_id", uuid5(NAMESPACE_URL, f"regime-evaluation:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "method": self.method.value, "dimension": self.dimension.value,
            "sample_count": self.sample_count,
            "brier_score": None if self.brier_score is None else _decimal(self.brier_score),
            "accuracy": None if self.accuracy is None else _decimal(self.accuracy),
            "evidence_state": self.evidence_state.value,
            "label_ids": tuple(str(value) for value in self.label_ids),
        }


@dataclass(frozen=True, slots=True)
class RegimeRunV2:
    definition: RegimeModelDefinitionV2
    dataset_version_id: UUID
    dataset_version: str
    dataset_content_hash: str
    instrument_id: str
    evaluated_at: datetime
    data_health_status: str
    data_health_assessment_ids: tuple[UUID, ...]
    observations: tuple[RegimeObservationV2, ...]
    evaluations: tuple[RegimeMethodEvaluationV2, ...]
    status: ReviewStatus
    reasons: tuple[str, ...]
    limitations: tuple[str, ...] = LIMITATIONS
    content_hash: str = field(init=False)
    run_id: UUID = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.evaluated_at, "regime_run_evaluated_at")
        if (
            not self.dataset_version.strip()
            or len(self.dataset_content_hash) != 64
            or not self.instrument_id.strip()
            or self.data_health_status not in {"HEALTHY", "BLOCKED"}
            or not self.data_health_assessment_ids
            or not self.observations
            or not self.evaluations
            or (self.status is ReviewStatus.BLOCKED and not self.reasons)
            or not self.limitations
        ):
            raise RegimeEngineV2Error("invalid_regime_run")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "run_id", uuid5(NAMESPACE_URL, f"regime-run:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "model_version_id": str(self.definition.model_version_id),
            "model_content_hash": self.definition.content_hash,
            "dataset_version_id": str(self.dataset_version_id),
            "dataset_version": self.dataset_version,
            "dataset_content_hash": self.dataset_content_hash,
            "instrument_id": self.instrument_id, "evaluated_at": self.evaluated_at,
            "data_health_status": self.data_health_status,
            "data_health_assessment_ids": tuple(str(value) for value in self.data_health_assessment_ids),
            "observation_hashes": tuple(item.content_hash for item in self.observations),
            "evaluation_hashes": tuple(item.content_hash for item in self.evaluations),
            "status": self.status.value, "reasons": self.reasons,
            "limitations": self.limitations,
        }


class FeatureAuthority(Protocol):
    def definition(self, feature_id: UUID) -> FeatureDefinitionVersion: ...
    def latest_as_of(
        self, feature_id: UUID, instrument_id: str, dataset_version: str, decision_at: datetime,
    ) -> tuple[FeatureMaterialization, ...]: ...


def _available(
    authority: FeatureAuthority,
    definition: RegimeModelDefinitionV2,
    instrument_id: str,
    dataset_version: str,
    event_at: datetime,
) -> dict[str, FeatureMaterialization | None]:
    result: dict[str, FeatureMaterialization | None] = {}
    for requirement in definition.feature_requirements:
        authoritative = authority.definition(requirement.feature_id)
        if authoritative.semantic_version != requirement.semantic_version:
            raise RegimeEngineV2Error("regime_feature_version_mismatch")
        history = authority.latest_as_of(requirement.feature_id, instrument_id, dataset_version, event_at)
        selected = next((item for item in reversed(history) if item.event_at == event_at), None)
        if selected is not None and (
            selected.knowledge_at > event_at
            or selected.computed_at > event_at
            or selected.dataset_version != dataset_version
        ):
            raise RegimeEngineV2Error("future_or_mismatched_regime_feature")
        if selected is None or selected.quality_status is not FeatureQualityStatus.VALIDATED or selected.value is None:
            result[requirement.role] = None
        else:
            result[requirement.role] = selected
    return result


def _probabilities(states: tuple[RegimeState, ...], weights: tuple[Decimal, ...]) -> tuple[tuple[RegimeState, Decimal], ...]:
    if len(states) != len(weights) or any(value < 0 for value in weights):
        raise RegimeEngineV2Error("invalid_regime_probability_weights")
    total = sum(weights, Decimal("0"))
    if total <= 0:
        raise RegimeEngineV2Error("empty_regime_probability_weights")
    normalized = tuple(value / total for value in weights)
    residual = Decimal("1") - sum(normalized, Decimal("0"))
    adjusted = (*normalized[:-1], normalized[-1] + residual)
    return tuple(zip(states, adjusted, strict=True))


def _observation(
    event_at: datetime,
    method: RegimeMethod,
    dimension: RegimeDimension,
    weights: tuple[Decimal, ...] | None,
    input_materialization_ids: tuple[UUID, ...],
) -> RegimeObservationV2:
    if weights is None:
        return RegimeObservationV2(event_at, method, dimension, EvidenceState.UNAVAILABLE, (), None, None, ())
    probabilities = _probabilities(DIMENSION_STATES[dimension], weights)
    hard_label, maximum = max(probabilities, key=lambda item: item[1])
    return RegimeObservationV2(
        event_at, method, dimension, EvidenceState.MEASURED, probabilities,
        hard_label, Decimal("1") - maximum,
        tuple(sorted(set(input_materialization_ids), key=str)),
    )


def _value(features: dict[str, FeatureMaterialization | None], role: str) -> Decimal | None:
    item = features.get(role)
    return None if item is None else item.value


def _input_ids(features: dict[str, FeatureMaterialization | None], roles: tuple[str, ...]) -> tuple[UUID, ...]:
    return tuple(item.materialization_id for role in roles if (item := features.get(role)) is not None)


def _rule_observations(
    event_at: datetime,
    features: dict[str, FeatureMaterialization | None],
    previous: dict[str, FeatureMaterialization | None] | None,
) -> tuple[RegimeObservationV2, ...]:
    method = RegimeMethod.RULE_BASED
    trend = _value(features, "trend")
    volatility = _value(features, "volatility")
    liquidity = _value(features, "liquidity")
    inflation = _value(features, "inflation")
    policy = _value(features, "policy")
    trend_weights = None if trend is None else (
        max(_clamp(trend / Decimal("0.05")), Decimal("0")),
        max(-_clamp(trend / Decimal("0.05")), Decimal("0")),
        Decimal("1") - abs(_clamp(trend / Decimal("0.05"))),
    )
    vol_level = None if volatility is None else (
        max(volatility / Decimal("0.02"), Decimal("0.01")), Decimal("1"),
    )
    previous_vol = None if previous is None else _value(previous, "volatility")
    vol_direction = None if volatility is None or previous_vol is None else (
        Decimal("1") if volatility >= previous_vol else Decimal("0.1"),
        Decimal("1") if volatility < previous_vol else Decimal("0.1"),
    )
    liquidity_level = None if liquidity is None else (
        max(liquidity / Decimal("1000000"), Decimal("0.01")), Decimal("1"),
    )
    previous_liquidity = None if previous is None else _value(previous, "liquidity")
    liquidity_direction = None if liquidity is None or previous_liquidity is None else (
        Decimal("1") if liquidity < previous_liquidity else Decimal("0.1"),
        Decimal("1") if liquidity >= previous_liquidity else Decimal("0.1"),
    )
    risk = None if trend is None or volatility is None else (
        max(Decimal("0.01"), Decimal("1") + _clamp(trend / Decimal("0.05")) - _clamp(volatility / Decimal("0.04"), Decimal("0"), Decimal("1"))),
        max(Decimal("0.01"), Decimal("1") - _clamp(trend / Decimal("0.05")) + _clamp(volatility / Decimal("0.04"), Decimal("0"), Decimal("1"))),
    )
    if inflation is None or policy is None:
        macro = None
    else:
        inflationary = inflation >= 0
        growth = trend is not None and trend >= 0 and policy <= 0
        selected = (
            RegimeState.INFLATIONARY_GROWTH if inflationary and growth else
            RegimeState.INFLATIONARY_CONTRACTION if inflationary else
            RegimeState.DISINFLATIONARY_GROWTH if growth else
            RegimeState.DEFLATIONARY_CONTRACTION
        )
        macro = tuple(Decimal("0.7") if state is selected else Decimal("0.1") for state in DIMENSION_STATES[RegimeDimension.MACRO_CYCLE])
    stress_roles = ("credit", "currency", "commodity", "correlation")
    stress_values = tuple(_value(features, role) for role in stress_roles)
    if any(value is None for value in stress_values):
        stress = None
    else:
        concrete = tuple(abs(value) for value in stress_values if value is not None)
        crisis = max(Decimal("0"), sum((max(value - Decimal("0.5"), Decimal("0")) for value in concrete), Decimal("0")))
        stress = (Decimal("1"), concrete[0], concrete[1], concrete[2], concrete[3], crisis)
    return (
        _observation(event_at, method, RegimeDimension.TREND, trend_weights, _input_ids(features, ("trend",))),
        _observation(event_at, method, RegimeDimension.VOLATILITY_LEVEL, vol_level, _input_ids(features, ("volatility",))),
        _observation(event_at, method, RegimeDimension.VOLATILITY_DIRECTION, vol_direction, _input_ids(features, ("volatility",))),
        _observation(event_at, method, RegimeDimension.LIQUIDITY_LEVEL, liquidity_level, _input_ids(features, ("liquidity",))),
        _observation(event_at, method, RegimeDimension.LIQUIDITY_DIRECTION, liquidity_direction, _input_ids(features, ("liquidity",))),
        _observation(event_at, method, RegimeDimension.RISK_APPETITE, risk, _input_ids(features, ("trend", "volatility"))),
        _observation(event_at, method, RegimeDimension.MACRO_CYCLE, macro, _input_ids(features, ("trend", "inflation", "policy"))),
        _observation(event_at, method, RegimeDimension.STRESS, stress, _input_ids(features, stress_roles)),
    )


def _ensemble_observations(rule: tuple[RegimeObservationV2, ...]) -> tuple[RegimeObservationV2, ...]:
    result = []
    for item in rule:
        if item.evidence_state is EvidenceState.UNAVAILABLE:
            result.append(_observation(item.event_at, RegimeMethod.PROBABILISTIC_ENSEMBLE, item.dimension, None, ()))
            continue
        count = Decimal(len(item.probabilities))
        softened = tuple(value * Decimal("0.75") + Decimal("0.25") / count for _, value in item.probabilities)
        result.append(
            _observation(
                item.event_at,
                RegimeMethod.PROBABILISTIC_ENSEMBLE,
                item.dimension,
                softened,
                item.input_materialization_ids,
            )
        )
    return tuple(result)


def _evaluate(
    observations: tuple[RegimeObservationV2, ...],
    labels: tuple[RegimeOutcomeLabel, ...],
    method: RegimeMethod,
    dimension: RegimeDimension,
) -> RegimeMethodEvaluationV2:
    selected_labels = tuple(item for item in labels if item.dimension is dimension)
    by_time = {
        item.event_at: item for item in observations
        if item.method is method and item.dimension is dimension and item.evidence_state is EvidenceState.MEASURED
    }
    pairs = tuple((label, by_time[label.event_at]) for label in selected_labels if label.event_at in by_time)
    if not pairs:
        return RegimeMethodEvaluationV2(method, dimension, 0, None, None, EvidenceState.UNAVAILABLE, ())
    squared = Decimal("0")
    correct = 0
    for label, observation in pairs:
        probabilities = dict(observation.probabilities)
        squared += sum(
            ((probability - (Decimal("1") if state is label.state else Decimal("0"))) ** 2
             for state, probability in probabilities.items()),
            Decimal("0"),
        )
        correct += int(observation.hard_label is label.state)
    return RegimeMethodEvaluationV2(
        method, dimension, len(pairs), squared / Decimal(len(pairs)),
        Decimal(correct) / Decimal(len(pairs)), EvidenceState.MEASURED,
        tuple(label.label_id for label, _ in pairs),
    )


class RegimeResearchOrchestratorV2:
    def __init__(self, feature_authority: FeatureAuthority) -> None:
        self._features = feature_authority

    def run(
        self,
        definition: RegimeModelDefinitionV2,
        *,
        dataset_version_id: UUID,
        dataset_version: str,
        dataset_content_hash: str,
        instrument_id: str,
        decision_times: tuple[datetime, ...],
        labels: tuple[RegimeOutcomeLabel, ...],
        evaluated_at: datetime,
        data_health_status: str,
        data_health_assessment_ids: tuple[UUID, ...],
    ) -> RegimeRunV2:
        _aware(evaluated_at, "regime_evaluated_at")
        if (
            not decision_times
            or tuple(sorted(decision_times)) != decision_times
            or len(set(decision_times)) != len(decision_times)
            or definition.trained_through >= decision_times[0]
            or any(time > evaluated_at for time in decision_times)
            or any(label.knowledge_at > evaluated_at for label in labels)
            or len(dataset_content_hash) != 64
            or data_health_status not in {"HEALTHY", "BLOCKED"}
            or not data_health_assessment_ids
        ):
            raise RegimeEngineV2Error("invalid_regime_research_protocol")
        feature_rows: list[dict[str, FeatureMaterialization | None]] = []
        observations: list[RegimeObservationV2] = []
        for event_at in decision_times:
            current = _available(self._features, definition, instrument_id, dataset_version, event_at)
            rule = _rule_observations(event_at, current, feature_rows[-1] if feature_rows else None)
            observations.extend(rule)
            observations.extend(_ensemble_observations(rule))
            feature_rows.append(current)
        observation_tuple = tuple(observations)
        evaluations = tuple(
            _evaluate(observation_tuple, labels, method, dimension)
            for method in definition.methods for dimension in RegimeDimension
        )
        reasons = []
        required_roles = {item.role for item in definition.feature_requirements if item.required}
        if any(any(row.get(role) is None for role in required_roles) for row in feature_rows):
            reasons.append("required_regime_feature_unavailable")
        if data_health_status == "BLOCKED":
            reasons.append("data_health_blocked")
        if not any(item.evidence_state is EvidenceState.MEASURED for item in evaluations):
            reasons.append("oos_regime_validation_unavailable")
        status = ReviewStatus.BLOCKED if reasons else ReviewStatus.REVIEW_REQUIRED
        return RegimeRunV2(
            definition, dataset_version_id, dataset_version, dataset_content_hash,
            instrument_id, evaluated_at, data_health_status, data_health_assessment_ids,
            observation_tuple, evaluations, status, tuple(reasons),
        )


@dataclass(frozen=True, slots=True)
class RegimeRiskAdjustmentCandidateV2:
    run_id: UUID
    strategy_version_id: UUID
    current_risk_multiplier: Decimal
    proposed_risk_multiplier: Decimal
    preapproved_maximum: Decimal
    action: RiskAdjustmentAction
    status: ReviewStatus
    reasons: tuple[str, ...]
    created_at: datetime
    automatic_authority: bool = False
    content_hash: str = field(init=False)
    candidate_id: UUID = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "regime_candidate_created_at")
        if (
            min(self.current_risk_multiplier, self.proposed_risk_multiplier, self.preapproved_maximum) < 0
            or self.automatic_authority
            or (self.status is ReviewStatus.BLOCKED and not self.reasons)
        ):
            raise RegimeEngineV2Error("invalid_regime_risk_candidate")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "candidate_id", uuid5(NAMESPACE_URL, f"regime-risk-candidate:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id), "strategy_version_id": str(self.strategy_version_id),
            "current_risk_multiplier": _decimal(self.current_risk_multiplier),
            "proposed_risk_multiplier": _decimal(self.proposed_risk_multiplier),
            "preapproved_maximum": _decimal(self.preapproved_maximum),
            "action": self.action.value, "status": self.status.value,
            "reasons": self.reasons, "created_at": self.created_at,
            "automatic_authority": self.automatic_authority,
        }


def build_regime_risk_candidate(
    run: RegimeRunV2,
    *,
    strategy_version_id: UUID,
    current_risk_multiplier: Decimal,
    proposed_risk_multiplier: Decimal,
    preapproved_maximum: Decimal,
    created_at: datetime,
) -> RegimeRiskAdjustmentCandidateV2:
    reasons: list[str] = []
    if run.status is ReviewStatus.BLOCKED:
        reasons.append("regime_run_blocked")
    if proposed_risk_multiplier > preapproved_maximum:
        reasons.append("preapproved_regime_risk_bound_exceeded")
    if proposed_risk_multiplier > current_risk_multiplier:
        reasons.append("automatic_risk_increase_prohibited")
    action = (
        RiskAdjustmentAction.DISABLE if proposed_risk_multiplier == 0 else
        RiskAdjustmentAction.REDUCE if proposed_risk_multiplier < current_risk_multiplier else
        RiskAdjustmentAction.INCREASE if proposed_risk_multiplier > current_risk_multiplier else
        RiskAdjustmentAction.MAINTAIN
    )
    status = ReviewStatus.BLOCKED if reasons else ReviewStatus.REVIEW_REQUIRED
    return RegimeRiskAdjustmentCandidateV2(
        run.run_id, strategy_version_id, current_risk_multiplier, proposed_risk_multiplier,
        preapproved_maximum, action, status, tuple(reasons), created_at,
    )


class PostgresRegimeEngineV2Store:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def publish_definition(self, definition: RegimeModelDefinitionV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO regime_model_versions VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) ON CONFLICT (model_version_id) DO NOTHING RETURNING content_hash",
                    (definition.model_version_id, definition.model_id, definition.version,
                     definition.implementation_version, definition.status, definition.trained_through,
                     _canonical(tuple({"role": item.role, "feature_id": str(item.feature_id), "version": item.semantic_version, "required": item.required} for item in definition.feature_requirements)),
                     _canonical(definition.payload()), definition.created_at, definition.content_hash),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute("SELECT content_hash FROM regime_model_versions WHERE model_version_id=%s", (definition.model_version_id,))
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != definition.content_hash:
                        raise RegimeEngineV2Error("regime_definition_replay_conflict")
            return definition.model_version_id
        except RegimeEngineV2Error:
            raise
        except Exception as error:
            raise RegimeEngineV2Error("regime_definition_publication_failed") from error

    def publish_run(self, run: RegimeRunV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT version,content_hash,status FROM historical_dataset_versions WHERE dataset_version_id=%s",
                    (run.dataset_version_id,),
                )
                dataset = cursor.fetchone()
                if dataset is None or (str(dataset[0]), str(dataset[1]), str(dataset[2])) != (
                    run.dataset_version, run.dataset_content_hash, "SEALED"
                ):
                    raise RegimeEngineV2Error("regime_sealed_dataset_binding_mismatch")
                cursor.execute(
                    "INSERT INTO regime_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s) ON CONFLICT (run_id) DO NOTHING RETURNING content_hash",
                    (run.run_id, run.definition.model_version_id, run.dataset_version_id,
                     run.dataset_version, run.dataset_content_hash, run.instrument_id,
                     run.evaluated_at, run.status.value, _canonical(run.limitations),
                     _canonical(run.payload()), run.content_hash),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute("SELECT content_hash FROM regime_runs WHERE run_id=%s", (run.run_id,))
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != run.content_hash:
                        raise RegimeEngineV2Error("regime_run_replay_conflict")
                for assessment_id in run.data_health_assessment_ids:
                    cursor.execute("INSERT INTO regime_run_data_health_assessments VALUES (%s,%s) ON CONFLICT DO NOTHING", (run.run_id, assessment_id))
                cursor.execute(
                    "SELECT assessment_id FROM regime_run_data_health_assessments WHERE run_id=%s",
                    (run.run_id,),
                )
                if {UUID(str(row[0])) for row in cursor.fetchall()} != set(run.data_health_assessment_ids):
                    raise RegimeEngineV2Error("regime_data_health_membership_conflict")
                for sequence, item in enumerate(run.observations):
                    cursor.execute(
                        "INSERT INTO regime_observations VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s) ON CONFLICT (observation_id) DO NOTHING",
                        (item.observation_id, item.event_at, item.method.value,
                         item.dimension.value, item.evidence_state.value,
                         None if item.hard_label is None else item.hard_label.value,
                         _canonical({name.value: _decimal(value) for name, value in item.probabilities}),
                         item.uncertainty,
                         _canonical(tuple(str(value) for value in item.input_materialization_ids)),
                         item.content_hash),
                    )
                    cursor.execute(
                        "INSERT INTO regime_run_observations VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (run.run_id, item.observation_id, sequence),
                    )
                for evaluation in run.evaluations:
                    cursor.execute(
                        "INSERT INTO regime_method_evaluations VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (evaluation_id) DO NOTHING",
                        (evaluation.evaluation_id, evaluation.method.value, evaluation.dimension.value,
                         evaluation.sample_count, evaluation.brier_score, evaluation.accuracy,
                         evaluation.evidence_state.value,
                         _canonical(tuple(str(value) for value in evaluation.label_ids)), evaluation.content_hash),
                    )
                    cursor.execute(
                        "INSERT INTO regime_run_method_evaluations VALUES (%s,%s) ON CONFLICT DO NOTHING",
                        (run.run_id, evaluation.evaluation_id),
                    )
                cursor.execute(
                    "SELECT observation_id FROM regime_run_observations WHERE run_id=%s ORDER BY sequence",
                    (run.run_id,),
                )
                if tuple(UUID(str(row[0])) for row in cursor.fetchall()) != tuple(
                    item.observation_id for item in run.observations
                ):
                    raise RegimeEngineV2Error("regime_observation_membership_conflict")
                cursor.execute(
                    "SELECT evaluation_id FROM regime_run_method_evaluations WHERE run_id=%s",
                    (run.run_id,),
                )
                if {UUID(str(row[0])) for row in cursor.fetchall()} != {
                    item.evaluation_id for item in run.evaluations
                }:
                    raise RegimeEngineV2Error("regime_evaluation_membership_conflict")
            return run.run_id
        except RegimeEngineV2Error:
            raise
        except Exception as error:
            raise RegimeEngineV2Error("regime_run_publication_failed") from error

    def publish_candidate(self, candidate: RegimeRiskAdjustmentCandidateV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO regime_risk_adjustment_candidates VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,FALSE,%s,%s) ON CONFLICT (candidate_id) DO NOTHING RETURNING content_hash",
                    (candidate.candidate_id, candidate.run_id, candidate.strategy_version_id,
                     candidate.current_risk_multiplier, candidate.proposed_risk_multiplier,
                     candidate.preapproved_maximum, candidate.action.value, candidate.status.value,
                     _canonical(candidate.reasons), candidate.created_at, candidate.content_hash),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute("SELECT content_hash FROM regime_risk_adjustment_candidates WHERE candidate_id=%s", (candidate.candidate_id,))
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != candidate.content_hash:
                        raise RegimeEngineV2Error("regime_candidate_replay_conflict")
            return candidate.candidate_id
        except RegimeEngineV2Error:
            raise
        except Exception as error:
            raise RegimeEngineV2Error("regime_candidate_publication_failed") from error
