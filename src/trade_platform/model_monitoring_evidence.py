"""Immutable, non-executing model sensitivity and degradation evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from .model_evaluation import (
    BinaryModelEvaluationReport,
    ModelEvaluationOutcome,
    validate_binary_model_evaluation_report,
)
from .persistence import PostgresDatabase


class ModelMonitoringError(ValueError):
    pass


class DriftDimension(StrEnum):
    DATA = "DATA"
    FEATURE = "FEATURE"
    PREDICTION = "PREDICTION"
    CALIBRATION = "CALIBRATION"
    PERFORMANCE = "PERFORMANCE"
    REGIME = "REGIME"
    EXECUTION = "EXECUTION"
    COST = "COST"


class ModelMonitoringOutcome(StrEnum):
    NO_THRESHOLD_BREACH_OBSERVED = "NO_THRESHOLD_BREACH_OBSERVED"
    DEGRADED_REVIEW_REQUIRED = "DEGRADED_REVIEW_REQUIRED"


_DIMENSION_ORDER = {dimension: index for index, dimension in enumerate(DriftDimension)}


@dataclass(frozen=True, slots=True)
class ModelMonitoringPolicy:
    policy_id: UUID
    version: str
    minimum_sensitivity_scenarios: int
    maximum_probability_shift: Decimal
    maximum_confidence_degradation: Decimal
    dimension_thresholds: tuple[tuple[DriftDimension, Decimal], ...]
    approved_by: str
    approved_at: datetime
    enabled: bool
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        version: str,
        minimum_sensitivity_scenarios: int,
        maximum_probability_shift: Decimal,
        maximum_confidence_degradation: Decimal,
        dimension_thresholds: dict[DriftDimension, Decimal],
        approved_by: str,
        approved_at: datetime,
        enabled: bool = True,
    ) -> ModelMonitoringPolicy:
        ordered = tuple(
            (dimension, dimension_thresholds[dimension])
            for dimension in DriftDimension if dimension in dimension_thresholds
        )
        payload = _policy_payload(
            version, minimum_sensitivity_scenarios, maximum_probability_shift,
            maximum_confidence_degradation, ordered, approved_by, approved_at, enabled,
        )
        content_hash = _hash(payload)
        return cls(
            uuid5(NAMESPACE_URL, f"model-monitoring-policy:{content_hash}"), version,
            minimum_sensitivity_scenarios, maximum_probability_shift,
            maximum_confidence_degradation, ordered, approved_by, approved_at,
            enabled, content_hash,
        )

    def __post_init__(self) -> None:
        values = (
            self.maximum_probability_shift, self.maximum_confidence_degradation,
            *(value for _, value in self.dimension_thresholds),
        )
        if (
            not self.version.strip() or self.minimum_sensitivity_scenarios < 1
            or not self.approved_by.strip() or not _aware(self.approved_at)
            or tuple(item for item, _ in self.dimension_thresholds) != tuple(DriftDimension)
            or any(not value.is_finite() for value in values)
            or any(value <= 0 or value > 1 or value != _quantize(value) for value in values)
            or self.content_hash != _policy_hash(self)
        ):
            raise ModelMonitoringError("invalid_model_monitoring_policy")


@dataclass(frozen=True, slots=True)
class ExplanationSensitivityScenario:
    scenario_id: str
    model_id: UUID
    feature_name: str
    perturbation: Decimal
    baseline_probability: Decimal
    perturbed_probability: Decimal
    baseline_confidence: Decimal
    perturbed_confidence: Decimal
    observed_at: datetime
    source_reference: str
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        scenario_id: str,
        model_id: UUID,
        feature_name: str,
        perturbation: Decimal,
        baseline_probability: Decimal,
        perturbed_probability: Decimal,
        baseline_confidence: Decimal,
        perturbed_confidence: Decimal,
        observed_at: datetime,
        source_reference: str,
    ) -> ExplanationSensitivityScenario:
        content_hash = _hash(_scenario_payload(
            scenario_id, model_id, feature_name, perturbation, baseline_probability,
            perturbed_probability, baseline_confidence, perturbed_confidence,
            observed_at, source_reference,
        ))
        return cls(
            scenario_id, model_id, feature_name, perturbation, baseline_probability,
            perturbed_probability, baseline_confidence, perturbed_confidence,
            observed_at, source_reference, content_hash,
        )

    def __post_init__(self) -> None:
        probabilities = (
            self.baseline_probability, self.perturbed_probability,
            self.baseline_confidence, self.perturbed_confidence,
        )
        values = (*probabilities, self.perturbation)
        if (
            not self.scenario_id.strip() or not self.feature_name.strip()
            or not self.source_reference.strip() or not _aware(self.observed_at)
            or any(not value.is_finite() for value in values)
            or self.perturbation == 0 or abs(self.perturbation) > 1
            or any(value < 0 or value > 1 for value in probabilities)
            or any(value != _quantize(value, 18) for value in values)
            or self.content_hash != _scenario_hash(self)
        ):
            raise ModelMonitoringError("invalid_explanation_sensitivity_scenario")


@dataclass(frozen=True, slots=True)
class ModelDegradationObservation:
    observation_id: str
    model_id: UUID
    dimension: DriftDimension
    score: Decimal
    observed_at: datetime
    source_reference: str
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        observation_id: str,
        model_id: UUID,
        dimension: DriftDimension,
        score: Decimal,
        observed_at: datetime,
        source_reference: str,
    ) -> ModelDegradationObservation:
        content_hash = _hash(_degradation_payload(
            observation_id, model_id, dimension, score, observed_at, source_reference,
        ))
        return cls(
            observation_id, model_id, dimension, score, observed_at, source_reference,
            content_hash,
        )

    def __post_init__(self) -> None:
        if (
            not self.observation_id.strip() or not self.source_reference.strip()
            or not _aware(self.observed_at) or not self.score.is_finite()
            or self.score < 0 or self.score > 1
            or self.score != _quantize(self.score)
            or self.content_hash != _degradation_hash(self)
        ):
            raise ModelMonitoringError("invalid_model_degradation_observation")


@dataclass(frozen=True, slots=True)
class ModelMonitoringReport:
    report_id: UUID
    policy_id: UUID
    policy_content_hash: str
    evaluation_report_id: UUID
    evaluation_report_content_hash: str
    model_id: UUID
    evaluated_at: datetime
    scenario_evidence_hash: str
    degradation_evidence_hash: str
    metrics: dict[str, Decimal]
    dimension_scores: dict[DriftDimension, Decimal]
    outcome: ModelMonitoringOutcome
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    content_hash: str


def evaluate_model_monitoring(
    policy: ModelMonitoringPolicy,
    evaluation: BinaryModelEvaluationReport,
    scenarios: tuple[ExplanationSensitivityScenario, ...],
    degradation: tuple[ModelDegradationObservation, ...],
    *,
    evaluated_at: datetime,
) -> ModelMonitoringReport:
    validate_binary_model_evaluation_report(evaluation)
    ordered_scenarios = tuple(sorted(scenarios, key=lambda item: (item.feature_name, item.scenario_id)))
    ordered_degradation = tuple(sorted(
        degradation, key=lambda item: _DIMENSION_ORDER[item.dimension],
    ))
    if (
        not policy.enabled or policy.content_hash != _policy_hash(policy)
        or evaluation.outcome is not ModelEvaluationOutcome.REVIEW_ELIGIBLE
        or not _aware(evaluated_at) or policy.approved_at > evaluation.evaluated_at
        or len(ordered_scenarios) < policy.minimum_sensitivity_scenarios
        or len({item.scenario_id for item in ordered_scenarios}) != len(ordered_scenarios)
        or len({item.content_hash for item in ordered_scenarios}) != len(ordered_scenarios)
        or any(item.content_hash != _scenario_hash(item) for item in ordered_scenarios)
        or {item.feature_name for item in ordered_scenarios} != set(evaluation.feature_importance)
        or tuple(item.dimension for item in ordered_degradation) != tuple(DriftDimension)
        or len({item.observation_id for item in ordered_degradation}) != len(ordered_degradation)
        or any(item.content_hash != _degradation_hash(item) for item in ordered_degradation)
        or any(item.model_id != evaluation.model_id for item in ordered_scenarios)
        or any(item.model_id != evaluation.model_id for item in ordered_degradation)
        or any(item.observed_at < evaluation.evaluated_at or item.observed_at > evaluated_at
               for item in ordered_scenarios)
        or any(item.observed_at < evaluation.evaluated_at or item.observed_at > evaluated_at
               for item in ordered_degradation)
    ):
        raise ModelMonitoringError("invalid_model_monitoring_evidence")

    probability_shift = max(
        abs(item.perturbed_probability - item.baseline_probability)
        for item in ordered_scenarios
    )
    confidence_degradation = max(
        max(Decimal("0"), item.baseline_confidence - item.perturbed_confidence)
        for item in ordered_scenarios
    )
    dimension_scores = {item.dimension: item.score for item in ordered_degradation}
    thresholds = dict(policy.dimension_thresholds)
    reasons: list[str] = []
    if probability_shift > policy.maximum_probability_shift:
        reasons.append("probability_sensitivity_exceeds_policy")
    if confidence_degradation > policy.maximum_confidence_degradation:
        reasons.append("confidence_degradation_exceeds_policy")
    reasons.extend(
        f"{dimension.value.lower()}_drift_exceeds_policy"
        for dimension, score in dimension_scores.items() if score > thresholds[dimension]
    )
    metrics = {
        "maximum_probability_shift": probability_shift,
        "maximum_confidence_degradation": confidence_degradation,
        "sensitivity_scenario_count": Decimal(len(ordered_scenarios)),
        "features_tested": Decimal(len({item.feature_name for item in ordered_scenarios})),
        "drift_dimensions_observed": Decimal(len(dimension_scores)),
        "breached_drift_dimensions": Decimal(sum(
            score > thresholds[dimension] for dimension, score in dimension_scores.items()
        )),
    }
    outcome = (
        ModelMonitoringOutcome.DEGRADED_REVIEW_REQUIRED
        if reasons else ModelMonitoringOutcome.NO_THRESHOLD_BREACH_OBSERVED
    )
    scenario_hash = _hash([item.content_hash for item in ordered_scenarios])
    degradation_hash = _hash([item.content_hash for item in ordered_degradation])
    report_id = uuid5(
        NAMESPACE_URL,
        f"model-monitoring:{policy.content_hash}:{evaluation.content_hash}:"
        f"{scenario_hash}:{degradation_hash}",
    )
    draft = ModelMonitoringReport(
        report_id, policy.policy_id, policy.content_hash, evaluation.report_id,
        evaluation.content_hash, evaluation.model_id, evaluated_at, scenario_hash,
        degradation_hash, metrics, dimension_scores, outcome,
        tuple(reasons) or ("no_monitoring_threshold_breach_observed",),
        (
            "fixture_scores_are_not_production_drift_or_model_quality_acceptance",
            "post_hoc_sensitivity_is_not_causal_explanation",
            "monitoring_has_no_model_prediction_signal_order_risk_or_approval_authority",
        ),
        "",
    )
    return replace(draft, content_hash=_report_hash(draft))


class PostgresModelMonitoringStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append_policy(self, policy: ModelMonitoringPolicy) -> None:
        with self._database.transaction() as connection:
            row = connection.execute(
                "INSERT INTO model_monitoring_policy_versions VALUES "
                "(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s) "
                "ON CONFLICT (policy_id) DO NOTHING RETURNING content_hash",
                (
                    policy.policy_id, policy.version, policy.minimum_sensitivity_scenarios,
                    policy.maximum_probability_shift, policy.maximum_confidence_degradation,
                    json.dumps({key.value: _decimal_text(value) for key, value in policy.dimension_thresholds}),
                    policy.approved_by, policy.approved_at, policy.enabled, policy.content_hash,
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT content_hash FROM model_monitoring_policy_versions WHERE policy_id=%s",
                    (policy.policy_id,),
                ).fetchone()
                if row is None or str(row[0]) != policy.content_hash:
                    raise ModelMonitoringError("conflicting_model_monitoring_policy")

    def policy(self, policy_id: UUID) -> ModelMonitoringPolicy:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM model_monitoring_policy_versions WHERE policy_id=%s",
                (policy_id,),
            ).fetchone()
        if row is None:
            raise KeyError(str(policy_id))
        thresholds = {
            DriftDimension(key): Decimal(value) for key, value in row[5].items()
        }
        return ModelMonitoringPolicy(
            row[0], row[1], row[2], row[3], row[4],
            tuple((dimension, thresholds[dimension]) for dimension in DriftDimension),
            row[6], row[7], row[8], row[9],
        )

    def append_report(
        self,
        report: ModelMonitoringReport,
        scenarios: tuple[ExplanationSensitivityScenario, ...],
        degradation: tuple[ModelDegradationObservation, ...],
    ) -> None:
        ordered_scenarios = tuple(sorted(scenarios, key=lambda item: (item.feature_name, item.scenario_id)))
        ordered_degradation = tuple(sorted(
            degradation, key=lambda item: _DIMENSION_ORDER[item.dimension],
        ))
        if (
            report.content_hash != _report_hash(report)
            or report.scenario_evidence_hash != _hash([item.content_hash for item in ordered_scenarios])
            or report.degradation_evidence_hash != _hash([item.content_hash for item in ordered_degradation])
        ):
            raise ModelMonitoringError("model_monitoring_report_evidence_mismatch")
        with self._database.transaction() as connection:
            policy = connection.execute(
                "SELECT content_hash FROM model_monitoring_policy_versions WHERE policy_id=%s",
                (report.policy_id,),
            ).fetchone()
            evaluation = connection.execute(
                "SELECT content_hash FROM model_evaluation_reports WHERE report_id=%s",
                (report.evaluation_report_id,),
            ).fetchone()
            if policy is None or str(policy[0]) != report.policy_content_hash:
                raise ModelMonitoringError("model_monitoring_policy_not_registered_or_mismatched")
            if evaluation is None or str(evaluation[0]) != report.evaluation_report_content_hash:
                raise ModelMonitoringError("model_evaluation_report_not_registered_or_mismatched")
            row = connection.execute(
                "INSERT INTO model_monitoring_reports VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s) "
                "ON CONFLICT (report_id) DO NOTHING RETURNING content_hash",
                (
                    report.report_id, report.policy_id, report.policy_content_hash,
                    report.evaluation_report_id, report.evaluation_report_content_hash,
                    report.model_id, report.evaluated_at, report.scenario_evidence_hash,
                    report.degradation_evidence_hash, json.dumps(_decimal_json(report.metrics)),
                    json.dumps({key.value: _decimal_text(value) for key, value in report.dimension_scores.items()}),
                    report.outcome.value, json.dumps(report.reasons),
                    json.dumps(report.limitations), report.content_hash,
                ),
            ).fetchone()
            if row is None:
                existing = connection.execute(
                    "SELECT content_hash FROM model_monitoring_reports WHERE report_id=%s",
                    (report.report_id,),
                ).fetchone()
                if existing is None or str(existing[0]) != report.content_hash:
                    raise ModelMonitoringError("conflicting_model_monitoring_report")
                return
            for scenario in ordered_scenarios:
                connection.execute(
                    "INSERT INTO model_explanation_sensitivity_scenarios VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        report.report_id, scenario.scenario_id, scenario.model_id,
                        scenario.feature_name, scenario.perturbation,
                        scenario.baseline_probability, scenario.perturbed_probability,
                        scenario.baseline_confidence, scenario.perturbed_confidence,
                        scenario.observed_at, scenario.source_reference, scenario.content_hash,
                    ),
                )
            for observation in ordered_degradation:
                connection.execute(
                    "INSERT INTO model_degradation_observations VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        report.report_id, observation.observation_id, observation.model_id,
                        observation.dimension.value, observation.score,
                        observation.observed_at, observation.source_reference,
                        observation.content_hash,
                    ),
                )

    def report(self, report_id: UUID) -> ModelMonitoringReport:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM model_monitoring_reports WHERE report_id=%s", (report_id,),
            ).fetchone()
        if row is None:
            raise KeyError(str(report_id))
        report = ModelMonitoringReport(
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8],
            {key: Decimal(value) for key, value in row[9].items()},
            {DriftDimension(key): Decimal(value) for key, value in row[10].items()},
            ModelMonitoringOutcome(row[11]), tuple(row[12]), tuple(row[13]), row[14],
        )
        scenarios = self.sensitivity_scenarios(report_id)
        degradation = self.degradation_observations(report_id)
        if (
            report.content_hash != _report_hash(report)
            or report.scenario_evidence_hash != _hash([item.content_hash for item in scenarios])
            or report.degradation_evidence_hash != _hash([
                item.content_hash for item in degradation
            ])
        ):
            raise ModelMonitoringError("model_monitoring_report_hash_mismatch")
        return report

    def sensitivity_scenarios(
        self, report_id: UUID,
    ) -> tuple[ExplanationSensitivityScenario, ...]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT scenario_id,model_id,feature_name,perturbation,baseline_probability,"
                "perturbed_probability,baseline_confidence,perturbed_confidence,observed_at,"
                "source_reference,content_hash FROM model_explanation_sensitivity_scenarios "
                "WHERE report_id=%s ORDER BY feature_name,scenario_id", (report_id,),
            ).fetchall()
        return tuple(ExplanationSensitivityScenario(*row) for row in rows)

    def degradation_observations(
        self, report_id: UUID,
    ) -> tuple[ModelDegradationObservation, ...]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT observation_id,model_id,dimension,score,observed_at,source_reference,"
                "content_hash FROM model_degradation_observations WHERE report_id=%s",
                (report_id,),
            ).fetchall()
        observations = tuple(
            ModelDegradationObservation(
                row[0], row[1], DriftDimension(row[2]), row[3], row[4], row[5], row[6],
            )
            for row in rows
        )
        return tuple(sorted(
            observations, key=lambda item: _DIMENSION_ORDER[item.dimension],
        ))


def _policy_payload(
    version: str,
    minimum_sensitivity_scenarios: int,
    maximum_probability_shift: Decimal,
    maximum_confidence_degradation: Decimal,
    dimension_thresholds: tuple[tuple[DriftDimension, Decimal], ...],
    approved_by: str,
    approved_at: datetime,
    enabled: bool,
) -> dict[str, object]:
    return {
        "version": version,
        "minimum_sensitivity_scenarios": minimum_sensitivity_scenarios,
        "maximum_probability_shift": _decimal_text(maximum_probability_shift),
        "maximum_confidence_degradation": _decimal_text(maximum_confidence_degradation),
        "dimension_thresholds": {
            key.value: _decimal_text(value) for key, value in dimension_thresholds
        },
        "approved_by": approved_by, "approved_at": approved_at.isoformat(), "enabled": enabled,
    }


def _policy_hash(policy: ModelMonitoringPolicy) -> str:
    return _hash(_policy_payload(
        policy.version, policy.minimum_sensitivity_scenarios,
        policy.maximum_probability_shift, policy.maximum_confidence_degradation,
        policy.dimension_thresholds, policy.approved_by, policy.approved_at, policy.enabled,
    ))


def _scenario_payload(
    scenario_id: str,
    model_id: UUID,
    feature_name: str,
    perturbation: Decimal,
    baseline_probability: Decimal,
    perturbed_probability: Decimal,
    baseline_confidence: Decimal,
    perturbed_confidence: Decimal,
    observed_at: datetime,
    source_reference: str,
) -> dict[str, str]:
    return {
        "scenario_id": scenario_id, "model_id": str(model_id),
        "feature_name": feature_name, "perturbation": _decimal_text(perturbation),
        "baseline_probability": _decimal_text(baseline_probability),
        "perturbed_probability": _decimal_text(perturbed_probability),
        "baseline_confidence": _decimal_text(baseline_confidence),
        "perturbed_confidence": _decimal_text(perturbed_confidence),
        "observed_at": observed_at.isoformat(), "source_reference": source_reference,
    }


def _scenario_hash(item: ExplanationSensitivityScenario) -> str:
    return _hash(_scenario_payload(
        item.scenario_id, item.model_id, item.feature_name, item.perturbation,
        item.baseline_probability, item.perturbed_probability,
        item.baseline_confidence, item.perturbed_confidence,
        item.observed_at, item.source_reference,
    ))


def _degradation_payload(
    observation_id: str,
    model_id: UUID,
    dimension: DriftDimension,
    score: Decimal,
    observed_at: datetime,
    source_reference: str,
) -> dict[str, str]:
    return {
        "observation_id": observation_id, "model_id": str(model_id),
        "dimension": dimension.value, "score": _decimal_text(score),
        "observed_at": observed_at.isoformat(), "source_reference": source_reference,
    }


def _degradation_hash(item: ModelDegradationObservation) -> str:
    return _hash(_degradation_payload(
        item.observation_id, item.model_id, item.dimension, item.score,
        item.observed_at, item.source_reference,
    ))


def _report_hash(report: ModelMonitoringReport) -> str:
    return _hash({
        "report_id": str(report.report_id), "policy_id": str(report.policy_id),
        "policy_content_hash": report.policy_content_hash,
        "evaluation_report_id": str(report.evaluation_report_id),
        "evaluation_report_content_hash": report.evaluation_report_content_hash,
        "model_id": str(report.model_id), "evaluated_at": report.evaluated_at.isoformat(),
        "scenario_evidence_hash": report.scenario_evidence_hash,
        "degradation_evidence_hash": report.degradation_evidence_hash,
        "metrics": _decimal_json(report.metrics),
        "dimension_scores": {
            key.value: _decimal_text(value) for key, value in report.dimension_scores.items()
        },
        "outcome": report.outcome.value, "reasons": report.reasons,
        "limitations": report.limitations,
    })


def _decimal_json(values: dict[str, Decimal]) -> dict[str, str]:
    return {key: _decimal_text(value) for key, value in values.items()}


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"


def _quantize(value: Decimal, places: int = 12) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
