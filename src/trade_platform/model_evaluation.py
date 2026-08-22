"""Deterministic, non-serving model evaluation evidence.

The evaluator accepts already-produced chronological holdout predictions.  It
does not train, load, or execute a model and grants no strategy/order authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from .model_registry import ModelValidation, ModelVersion
from .persistence import PersistenceError, PostgresDatabase


class ModelEvaluationError(ValueError):
    """The evaluation contract or retained evidence is invalid."""


class ModelComplexity(str, Enum):
    NAIVE = "NAIVE"
    RULE_BASED = "RULE_BASED"
    LINEAR = "LINEAR"
    REGULARIZED_LINEAR = "REGULARIZED_LINEAR"
    TREE = "TREE"
    GRADIENT_BOOSTING = "GRADIENT_BOOSTING"
    TIME_SERIES = "TIME_SERIES"
    NEURAL = "NEURAL"
    ENSEMBLE = "ENSEMBLE"
    REINFORCEMENT_LEARNING = "REINFORCEMENT_LEARNING"


COMPLEXITY_ORDER = tuple(ModelComplexity)


class ModelEvaluationOutcome(str, Enum):
    BLOCKED = "BLOCKED"
    REVIEW_ELIGIBLE = "REVIEW_ELIGIBLE"


@dataclass(frozen=True, slots=True)
class BinaryEvaluationPolicy:
    policy_id: UUID
    version: str
    task: str
    candidate_complexity: ModelComplexity
    predecessor_complexity: ModelComplexity
    minimum_holdout_observations: int
    calibration_bins: int
    maximum_expected_calibration_error: Decimal
    minimum_brier_improvement: Decimal
    minimum_economic_improvement_after_cost: Decimal
    maximum_half_brier_gap: Decimal
    decision_threshold: Decimal
    assumed_cost_per_positive_decision: Decimal
    approved_by: str
    approved_at: datetime
    enabled: bool
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        version: str,
        task: str,
        candidate_complexity: ModelComplexity,
        predecessor_complexity: ModelComplexity,
        minimum_holdout_observations: int,
        calibration_bins: int,
        maximum_expected_calibration_error: Decimal,
        minimum_brier_improvement: Decimal,
        minimum_economic_improvement_after_cost: Decimal,
        maximum_half_brier_gap: Decimal,
        decision_threshold: Decimal,
        assumed_cost_per_positive_decision: Decimal,
        approved_by: str,
        approved_at: datetime,
        enabled: bool = True,
    ) -> BinaryEvaluationPolicy:
        if (
            not version.strip()
            or not task.strip()
            or not approved_by.strip()
            or not _aware(approved_at)
            or minimum_holdout_observations < 8
            or not 2 <= calibration_bins <= 20
            or not Decimal("0") <= maximum_expected_calibration_error <= Decimal("1")
            or minimum_brier_improvement <= 0
            or minimum_economic_improvement_after_cost < 0
            or not Decimal("0") <= maximum_half_brier_gap <= Decimal("1")
            or not Decimal("0") < decision_threshold < Decimal("1")
            or assumed_cost_per_positive_decision < 0
            or any(
                not value.is_finite()
                for value in (
                    maximum_expected_calibration_error,
                    minimum_brier_improvement,
                    minimum_economic_improvement_after_cost,
                    maximum_half_brier_gap,
                    decision_threshold,
                    assumed_cost_per_positive_decision,
                )
            )
        ):
            raise ModelEvaluationError("invalid_model_evaluation_policy")
        candidate_index = COMPLEXITY_ORDER.index(candidate_complexity)
        if candidate_index == 0 or COMPLEXITY_ORDER[candidate_index - 1] is not predecessor_complexity:
            raise ModelEvaluationError("candidate_must_follow_immediate_predecessor_complexity")
        payload = {
            "version": version,
            "task": task,
            "candidate_complexity": candidate_complexity.value,
            "predecessor_complexity": predecessor_complexity.value,
            "minimum_holdout_observations": minimum_holdout_observations,
            "calibration_bins": calibration_bins,
            "maximum_expected_calibration_error": _decimal_text(maximum_expected_calibration_error),
            "minimum_brier_improvement": _decimal_text(minimum_brier_improvement),
            "minimum_economic_improvement_after_cost": _decimal_text(
                minimum_economic_improvement_after_cost
            ),
            "maximum_half_brier_gap": _decimal_text(maximum_half_brier_gap),
            "decision_threshold": _decimal_text(decision_threshold),
            "assumed_cost_per_positive_decision": _decimal_text(
                assumed_cost_per_positive_decision
            ),
            "approved_by": approved_by,
            "approved_at": approved_at.isoformat(),
            "enabled": enabled,
        }
        content_hash = _hash(payload)
        return cls(
            uuid5(NAMESPACE_URL, f"model-evaluation-policy:{content_hash}"),
            version,
            task,
            candidate_complexity,
            predecessor_complexity,
            minimum_holdout_observations,
            calibration_bins,
            maximum_expected_calibration_error,
            minimum_brier_improvement,
            minimum_economic_improvement_after_cost,
            maximum_half_brier_gap,
            decision_threshold,
            assumed_cost_per_positive_decision,
            approved_by,
            approved_at,
            enabled,
            content_hash,
        )


@dataclass(frozen=True, slots=True)
class BinaryHoldoutObservation:
    observation_id: str
    observed_at: datetime
    actual_outcome: int
    candidate_probability: Decimal
    predecessor_probability: Decimal
    realized_return: Decimal
    explanation_base_probability: Decimal
    feature_contributions: dict[str, Decimal]
    source_reference: str
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        observation_id: str,
        observed_at: datetime,
        actual_outcome: int,
        candidate_probability: Decimal,
        predecessor_probability: Decimal,
        realized_return: Decimal,
        explanation_base_probability: Decimal,
        feature_contributions: dict[str, Decimal],
        source_reference: str,
    ) -> BinaryHoldoutObservation:
        values = (
            candidate_probability,
            predecessor_probability,
            realized_return,
            explanation_base_probability,
            *feature_contributions.values(),
        )
        if (
            not observation_id.strip()
            or not source_reference.strip()
            or not _aware(observed_at)
            or actual_outcome not in (0, 1)
            or not feature_contributions
            or any(not name.strip() for name in feature_contributions)
            or any(not value.is_finite() for value in values)
            or not Decimal("0") <= candidate_probability <= Decimal("1")
            or not Decimal("0") <= predecessor_probability <= Decimal("1")
            or not Decimal("0") <= explanation_base_probability <= Decimal("1")
        ):
            raise ModelEvaluationError("invalid_binary_holdout_observation")
        explained = explanation_base_probability + sum(
            feature_contributions.values(), start=Decimal("0")
        )
        if abs(explained - candidate_probability) > Decimal("0.000000000001"):
            raise ModelEvaluationError("local_explanation_does_not_reconcile")
        payload = {
            "observation_id": observation_id,
            "observed_at": observed_at.isoformat(),
            "actual_outcome": actual_outcome,
            "candidate_probability": _decimal_text(candidate_probability),
            "predecessor_probability": _decimal_text(predecessor_probability),
            "realized_return": _decimal_text(realized_return),
            "explanation_base_probability": _decimal_text(explanation_base_probability),
            "feature_contributions": {
                name: _decimal_text(value)
                for name, value in sorted(feature_contributions.items())
            },
            "source_reference": source_reference,
        }
        return cls(
            observation_id,
            observed_at,
            actual_outcome,
            candidate_probability,
            predecessor_probability,
            realized_return,
            explanation_base_probability,
            dict(feature_contributions),
            source_reference,
            _hash(payload),
        )


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    lower_bound: Decimal
    upper_bound: Decimal
    count: int
    mean_probability: Decimal | None
    observed_frequency: Decimal | None
    absolute_gap: Decimal | None


@dataclass(frozen=True, slots=True)
class BinaryModelEvaluationReport:
    report_id: UUID
    model_id: UUID
    policy_id: UUID
    dataset_version: str
    feature_version: str
    training_end: datetime
    holdout_start: datetime
    holdout_end: datetime
    evaluated_at: datetime
    observation_count: int
    observation_evidence_hash: str
    candidate_metrics: dict[str, Decimal]
    predecessor_metrics: dict[str, Decimal]
    calibration: tuple[CalibrationBin, ...]
    feature_importance: dict[str, Decimal]
    outcome: ModelEvaluationOutcome
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    content_hash: str

    def to_model_validation(self) -> ModelValidation:
        if self.outcome is not ModelEvaluationOutcome.REVIEW_ELIGIBLE:
            raise PermissionError("blocked_evaluation_cannot_create_validation")
        return ModelValidation.create(
            model_id=self.model_id,
            metrics=self.candidate_metrics,
            economic_value_after_cost=self.candidate_metrics["economic_value_after_cost"],
            evidence_reference=(
                f"model-evaluation://{self.report_id}/{self.content_hash}"
            ),
        )


def evaluate_binary_classifier(
    *,
    model: ModelVersion,
    policy: BinaryEvaluationPolicy,
    observations: Iterable[BinaryHoldoutObservation],
    training_end: datetime,
    holdout_start: datetime,
    holdout_end: datetime,
    evaluated_at: datetime,
) -> BinaryModelEvaluationReport:
    ordered = tuple(sorted(observations, key=lambda value: (value.observed_at, value.observation_id)))
    if (
        not all(_aware(value) for value in (training_end, holdout_start, holdout_end, evaluated_at))
        or training_end >= holdout_start
        or holdout_start >= holdout_end
        or evaluated_at < holdout_end
        or policy.approved_at > training_end
        or _policy_hash(policy) != policy.content_hash
        or not policy.enabled
        or model.task != policy.task
        or model.created_at > holdout_start
        or len(ordered) < policy.minimum_holdout_observations
        or any(value.observed_at < holdout_start or value.observed_at >= holdout_end for value in ordered)
        or len({value.observation_id for value in ordered}) != len(ordered)
        or len({value.content_hash for value in ordered}) != len(ordered)
        or any(_observation_hash(value) != value.content_hash for value in ordered)
        or {value.actual_outcome for value in ordered} != {0, 1}
        or any(value.feature_contributions.keys() != ordered[0].feature_contributions.keys() for value in ordered)
    ):
        raise ModelEvaluationError("invalid_chronological_holdout_evidence")

    candidate_probabilities = tuple(value.candidate_probability for value in ordered)
    predecessor_probabilities = tuple(value.predecessor_probability for value in ordered)
    outcomes = tuple(value.actual_outcome for value in ordered)
    returns = tuple(value.realized_return for value in ordered)
    candidate_metrics = _metrics(
        candidate_probabilities,
        outcomes,
        returns,
        policy.decision_threshold,
        policy.assumed_cost_per_positive_decision,
    )
    predecessor_metrics = _metrics(
        predecessor_probabilities,
        outcomes,
        returns,
        policy.decision_threshold,
        policy.assumed_cost_per_positive_decision,
    )
    calibration = _calibration(candidate_probabilities, outcomes, policy.calibration_bins)
    candidate_metrics["expected_calibration_error"] = sum(
        (
            Decimal(value.count) / Decimal(len(ordered)) * cast(Decimal, value.absolute_gap)
            for value in calibration
            if value.count
        ),
        start=Decimal("0"),
    )
    midpoint = len(ordered) // 2
    candidate_metrics["half_brier_gap"] = abs(
        _brier(candidate_probabilities[:midpoint], outcomes[:midpoint])
        - _brier(candidate_probabilities[midpoint:], outcomes[midpoint:])
    )
    candidate_metrics["brier_improvement"] = (
        predecessor_metrics["brier_score"] - candidate_metrics["brier_score"]
    )
    candidate_metrics["economic_improvement_after_cost"] = (
        candidate_metrics["economic_value_after_cost"]
        - predecessor_metrics["economic_value_after_cost"]
    )
    importance = {
        name: sum(
            (abs(value.feature_contributions[name]) for value in ordered),
            start=Decimal("0"),
        )
        / Decimal(len(ordered))
        for name in sorted(ordered[0].feature_contributions)
    }
    reasons: list[str] = []
    if candidate_metrics["brier_improvement"] < policy.minimum_brier_improvement:
        reasons.append("insufficient_brier_improvement_over_predecessor")
    if (
        candidate_metrics["economic_improvement_after_cost"]
        < policy.minimum_economic_improvement_after_cost
    ):
        reasons.append("insufficient_economic_improvement_after_cost")
    if candidate_metrics["expected_calibration_error"] > policy.maximum_expected_calibration_error:
        reasons.append("expected_calibration_error_exceeds_policy")
    if candidate_metrics["half_brier_gap"] > policy.maximum_half_brier_gap:
        reasons.append("holdout_half_stability_gap_exceeds_policy")
    outcome = (
        ModelEvaluationOutcome.REVIEW_ELIGIBLE if not reasons else ModelEvaluationOutcome.BLOCKED
    )
    observation_hash = _hash([value.content_hash for value in ordered])
    limitations = (
        "HOLDOUT_INPUTS_ARE_SUPPLIED_EVIDENCE_NOT_MODEL_EXECUTION",
        "LOCAL_CONTRIBUTIONS_ARE_POST_HOC_NOT_CAUSAL_PROOF",
        "NO_SERVING_SIGNAL_ORDER_OR_RISK_INCREASE_AUTHORITY",
    )
    report_payload: dict[str, object] = {
        "model_id": str(model.model_id),
        "policy_id": str(policy.policy_id),
        "dataset_version": model.dataset_version,
        "feature_version": model.feature_version,
        "training_end": training_end.isoformat(),
        "holdout_start": holdout_start.isoformat(),
        "holdout_end": holdout_end.isoformat(),
        "evaluated_at": evaluated_at.isoformat(),
        "observation_count": len(ordered),
        "observation_evidence_hash": observation_hash,
        "candidate_metrics": _decimal_json(candidate_metrics),
        "predecessor_metrics": _decimal_json(predecessor_metrics),
        "calibration": _calibration_json(calibration),
        "feature_importance": _decimal_json(importance),
        "outcome": outcome.value,
        "reasons": reasons,
        "limitations": limitations,
    }
    content_hash = _hash(report_payload)
    return BinaryModelEvaluationReport(
        uuid5(NAMESPACE_URL, f"binary-model-evaluation:{content_hash}"),
        model.model_id,
        policy.policy_id,
        model.dataset_version,
        model.feature_version,
        training_end,
        holdout_start,
        holdout_end,
        evaluated_at,
        len(ordered),
        observation_hash,
        candidate_metrics,
        predecessor_metrics,
        calibration,
        importance,
        outcome,
        tuple(reasons),
        limitations,
        content_hash,
    )


class PostgresModelEvaluationStore:
    """Append-only policy, observation and report evidence."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register_policy(self, policy: BinaryEvaluationPolicy) -> None:
        if _policy_hash(policy) != policy.content_hash:
            raise ModelEvaluationError("model_evaluation_policy_hash_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO model_evaluation_policy_versions VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (policy_id) DO NOTHING RETURNING content_hash",
                    (
                        policy.policy_id,
                        policy.version,
                        policy.task,
                        policy.candidate_complexity.value,
                        policy.predecessor_complexity.value,
                        policy.minimum_holdout_observations,
                        policy.calibration_bins,
                        policy.maximum_expected_calibration_error,
                        policy.minimum_brier_improvement,
                        policy.minimum_economic_improvement_after_cost,
                        policy.maximum_half_brier_gap,
                        policy.decision_threshold,
                        policy.assumed_cost_per_positive_decision,
                        policy.approved_by,
                        policy.approved_at,
                        policy.enabled,
                        policy.content_hash,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "SELECT content_hash FROM model_evaluation_policy_versions "
                        "WHERE policy_id=%s",
                        (policy.policy_id,),
                    )
                    row = cursor.fetchone()
                    if row is None or str(row[0]) != policy.content_hash:
                        raise ModelEvaluationError("model_evaluation_policy_idempotency_conflict")
        except (ModelEvaluationError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("model_evaluation_policy_write_uncertain") from error

    def policy(self, policy_id: UUID) -> BinaryEvaluationPolicy:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM model_evaluation_policy_versions WHERE policy_id=%s",
                    (policy_id,),
                )
                row = cursor.fetchone()
            if row is None:
                raise KeyError(str(policy_id))
            policy = _policy_from_row(row)
            if _policy_hash(policy) != policy.content_hash:
                raise ModelEvaluationError("model_evaluation_policy_hash_mismatch")
            return policy
        except (KeyError, ModelEvaluationError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("model_evaluation_policy_read_uncertain") from error

    def append_report(
        self,
        report: BinaryModelEvaluationReport,
        observations: Iterable[BinaryHoldoutObservation],
    ) -> None:
        ordered = tuple(sorted(observations, key=lambda value: (value.observed_at, value.observation_id)))
        if (
            len(ordered) != report.observation_count
            or _hash([value.content_hash for value in ordered]) != report.observation_evidence_hash
            or _report_hash(report) != report.content_hash
        ):
            raise ModelEvaluationError("model_evaluation_report_evidence_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO model_evaluation_reports VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,"
                    "%s::jsonb,%s,%s::jsonb,%s::jsonb,%s) "
                    "ON CONFLICT (report_id) DO NOTHING RETURNING content_hash",
                    (
                        report.report_id,
                        report.model_id,
                        report.policy_id,
                        report.dataset_version,
                        report.feature_version,
                        report.training_end,
                        report.holdout_start,
                        report.holdout_end,
                        report.evaluated_at,
                        report.observation_count,
                        report.observation_evidence_hash,
                        json.dumps(_decimal_json(report.candidate_metrics), sort_keys=True),
                        json.dumps(_decimal_json(report.predecessor_metrics), sort_keys=True),
                        json.dumps(_calibration_json(report.calibration), sort_keys=True),
                        json.dumps(_decimal_json(report.feature_importance), sort_keys=True),
                        report.outcome.value,
                        json.dumps(list(report.reasons)),
                        json.dumps(list(report.limitations)),
                        report.content_hash,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "SELECT content_hash FROM model_evaluation_reports WHERE report_id=%s",
                        (report.report_id,),
                    )
                    row = cursor.fetchone()
                    if row is None or str(row[0]) != report.content_hash:
                        raise ModelEvaluationError("model_evaluation_report_idempotency_conflict")
                    return
                for observation in ordered:
                    cursor.execute(
                        "INSERT INTO model_evaluation_observations VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)",
                        (
                            report.report_id,
                            observation.observation_id,
                            observation.observed_at,
                            observation.actual_outcome,
                            observation.candidate_probability,
                            observation.predecessor_probability,
                            observation.realized_return,
                            observation.explanation_base_probability,
                            json.dumps(
                                _decimal_json(observation.feature_contributions), sort_keys=True
                            ),
                            observation.source_reference,
                            observation.content_hash,
                        ),
                    )
        except (ModelEvaluationError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("model_evaluation_report_write_uncertain") from error

    def report(self, report_id: UUID) -> BinaryModelEvaluationReport:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM model_evaluation_reports WHERE report_id=%s", (report_id,)
                )
                row = cursor.fetchone()
                cursor.execute(
                    "SELECT content_hash FROM model_evaluation_observations WHERE report_id=%s "
                    "ORDER BY observed_at,observation_id",
                    (report_id,),
                )
                observation_hashes = [str(value[0]) for value in cursor.fetchall()]
            if row is None:
                raise KeyError(str(report_id))
            report = _report_from_row(row)
            if (
                _report_hash(report) != report.content_hash
                or len(observation_hashes) != report.observation_count
                or _hash(observation_hashes) != report.observation_evidence_hash
            ):
                raise ModelEvaluationError("model_evaluation_report_hash_mismatch")
            return report
        except (KeyError, ModelEvaluationError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("model_evaluation_report_read_uncertain") from error

    def observations(self, report_id: UUID) -> tuple[BinaryHoldoutObservation, ...]:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT observation_id,observed_at,actual_outcome,candidate_probability,"
                    "predecessor_probability,realized_return,explanation_base_probability,"
                    "feature_contributions,source_reference,content_hash "
                    "FROM model_evaluation_observations WHERE report_id=%s "
                    "ORDER BY observed_at,observation_id",
                    (report_id,),
                )
                rows = cursor.fetchall()
                cursor.execute(
                    "SELECT observation_count,observation_evidence_hash "
                    "FROM model_evaluation_reports WHERE report_id=%s",
                    (report_id,),
                )
                report_row = cursor.fetchone()
            observations = tuple(_observation_from_row(row) for row in rows)
            if report_row is None:
                raise KeyError(str(report_id))
            if (
                any(_observation_hash(value) != value.content_hash for value in observations)
                or len(observations) != int(cast(Any, report_row[0]))
                or _hash([value.content_hash for value in observations]) != str(report_row[1])
            ):
                raise ModelEvaluationError("model_evaluation_observation_hash_mismatch")
            return observations
        except (KeyError, ModelEvaluationError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("model_evaluation_observation_read_uncertain") from error


def _metrics(
    probabilities: tuple[Decimal, ...],
    outcomes: tuple[int, ...],
    returns: tuple[Decimal, ...],
    threshold: Decimal,
    cost: Decimal,
) -> dict[str, Decimal]:
    predicted = tuple(value >= threshold for value in probabilities)
    true_positive = sum(1 for guess, actual in zip(predicted, outcomes, strict=True) if guess and actual)
    false_positive = sum(1 for guess, actual in zip(predicted, outcomes, strict=True) if guess and not actual)
    false_negative = sum(1 for guess, actual in zip(predicted, outcomes, strict=True) if not guess and actual)
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = Decimal("0") if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    economic = sum(
        (realized - cost if guess else Decimal("0") for guess, realized in zip(predicted, returns, strict=True)),
        start=Decimal("0"),
    ) / Decimal(len(probabilities))
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "roc_auc": _roc_auc(probabilities, outcomes),
        "pr_auc": _pr_auc(probabilities, outcomes),
        "brier_score": _brier(probabilities, outcomes),
        "log_loss": _log_loss(probabilities, outcomes),
        "information_coefficient": _information_coefficient(probabilities, outcomes),
        "economic_value_after_cost": economic,
    }


def _brier(probabilities: tuple[Decimal, ...], outcomes: tuple[int, ...]) -> Decimal:
    return sum(
        ((probability - Decimal(outcome)) ** 2 for probability, outcome in zip(probabilities, outcomes, strict=True)),
        start=Decimal("0"),
    ) / Decimal(len(probabilities))


def _log_loss(probabilities: tuple[Decimal, ...], outcomes: tuple[int, ...]) -> Decimal:
    epsilon = Decimal("0.000000000000000001")
    total = Decimal("0")
    for probability, outcome in zip(probabilities, outcomes, strict=True):
        bounded = min(max(probability, epsilon), Decimal("1") - epsilon)
        total -= Decimal(outcome) * bounded.ln() + Decimal(1 - outcome) * (1 - bounded).ln()
    return total / Decimal(len(probabilities))


def _roc_auc(probabilities: tuple[Decimal, ...], outcomes: tuple[int, ...]) -> Decimal:
    positives = [probability for probability, outcome in zip(probabilities, outcomes, strict=True) if outcome]
    negatives = [probability for probability, outcome in zip(probabilities, outcomes, strict=True) if not outcome]
    score = Decimal("0")
    for positive in positives:
        for negative in negatives:
            score += Decimal("1") if positive > negative else Decimal("0.5") if positive == negative else Decimal("0")
    return score / Decimal(len(positives) * len(negatives))


def _pr_auc(probabilities: tuple[Decimal, ...], outcomes: tuple[int, ...]) -> Decimal:
    ranked = sorted(zip(probabilities, outcomes, strict=True), key=lambda value: value[0], reverse=True)
    total_positives = sum(outcomes)
    true_positive = 0
    false_positive = 0
    previous_recall = Decimal("0")
    area = Decimal("0")
    for index, (_, outcome) in enumerate(ranked):
        true_positive += outcome
        false_positive += 1 - outcome
        if index + 1 < len(ranked) and ranked[index + 1][0] == ranked[index][0]:
            continue
        recall = Decimal(true_positive) / Decimal(total_positives)
        precision = Decimal(true_positive) / Decimal(true_positive + false_positive)
        area += (recall - previous_recall) * precision
        previous_recall = recall
    return area


def _information_coefficient(
    probabilities: tuple[Decimal, ...], outcomes: tuple[int, ...]
) -> Decimal:
    count = Decimal(len(probabilities))
    mean_probability = sum(probabilities, start=Decimal("0")) / count
    mean_outcome = Decimal(sum(outcomes)) / count
    covariance = sum(
        (
            (probability - mean_probability) * (Decimal(outcome) - mean_outcome)
            for probability, outcome in zip(probabilities, outcomes, strict=True)
        ),
        start=Decimal("0"),
    )
    probability_variance = sum(
        ((value - mean_probability) ** 2 for value in probabilities), start=Decimal("0")
    )
    outcome_variance = sum(
        ((Decimal(value) - mean_outcome) ** 2 for value in outcomes), start=Decimal("0")
    )
    if probability_variance == 0 or outcome_variance == 0:
        return Decimal("0")
    return covariance / (probability_variance * outcome_variance).sqrt()


def _calibration(
    probabilities: tuple[Decimal, ...], outcomes: tuple[int, ...], bin_count: int
) -> tuple[CalibrationBin, ...]:
    width = Decimal("1") / Decimal(bin_count)
    result: list[CalibrationBin] = []
    for index in range(bin_count):
        lower = Decimal(index) * width
        upper = Decimal(index + 1) * width
        members = [
            (probability, outcome)
            for probability, outcome in zip(probabilities, outcomes, strict=True)
            if lower <= probability and (probability < upper or index == bin_count - 1)
        ]
        if not members:
            result.append(CalibrationBin(lower, upper, 0, None, None, None))
            continue
        mean_probability = sum((value[0] for value in members), start=Decimal("0")) / Decimal(len(members))
        observed_frequency = Decimal(sum(value[1] for value in members)) / Decimal(len(members))
        result.append(
            CalibrationBin(
                lower,
                upper,
                len(members),
                mean_probability,
                observed_frequency,
                abs(mean_probability - observed_frequency),
            )
        )
    return tuple(result)


def _ratio(numerator: int, denominator: int) -> Decimal:
    return Decimal("0") if denominator == 0 else Decimal(numerator) / Decimal(denominator)


def _policy_hash(policy: BinaryEvaluationPolicy) -> str:
    return _hash(
        {
            "version": policy.version,
            "task": policy.task,
            "candidate_complexity": policy.candidate_complexity.value,
            "predecessor_complexity": policy.predecessor_complexity.value,
            "minimum_holdout_observations": policy.minimum_holdout_observations,
            "calibration_bins": policy.calibration_bins,
            "maximum_expected_calibration_error": _decimal_text(
                policy.maximum_expected_calibration_error
            ),
            "minimum_brier_improvement": _decimal_text(policy.minimum_brier_improvement),
            "minimum_economic_improvement_after_cost": _decimal_text(
                policy.minimum_economic_improvement_after_cost
            ),
            "maximum_half_brier_gap": _decimal_text(policy.maximum_half_brier_gap),
            "decision_threshold": _decimal_text(policy.decision_threshold),
            "assumed_cost_per_positive_decision": _decimal_text(
                policy.assumed_cost_per_positive_decision
            ),
            "approved_by": policy.approved_by,
            "approved_at": policy.approved_at.isoformat(),
            "enabled": policy.enabled,
        }
    )


def _observation_hash(observation: BinaryHoldoutObservation) -> str:
    return _hash(
        {
            "observation_id": observation.observation_id,
            "observed_at": observation.observed_at.isoformat(),
            "actual_outcome": observation.actual_outcome,
            "candidate_probability": _decimal_text(observation.candidate_probability),
            "predecessor_probability": _decimal_text(observation.predecessor_probability),
            "realized_return": _decimal_text(observation.realized_return),
            "explanation_base_probability": _decimal_text(
                observation.explanation_base_probability
            ),
            "feature_contributions": _decimal_json(observation.feature_contributions),
            "source_reference": observation.source_reference,
        }
    )


def _report_hash(report: BinaryModelEvaluationReport) -> str:
    return _hash(
        {
            "model_id": str(report.model_id),
            "policy_id": str(report.policy_id),
            "dataset_version": report.dataset_version,
            "feature_version": report.feature_version,
            "training_end": report.training_end.isoformat(),
            "holdout_start": report.holdout_start.isoformat(),
            "holdout_end": report.holdout_end.isoformat(),
            "evaluated_at": report.evaluated_at.isoformat(),
            "observation_count": report.observation_count,
            "observation_evidence_hash": report.observation_evidence_hash,
            "candidate_metrics": _decimal_json(report.candidate_metrics),
            "predecessor_metrics": _decimal_json(report.predecessor_metrics),
            "calibration": _calibration_json(report.calibration),
            "feature_importance": _decimal_json(report.feature_importance),
            "outcome": report.outcome.value,
            "reasons": report.reasons,
            "limitations": report.limitations,
        }
    )


def _report_from_row(row: tuple[object, ...]) -> BinaryModelEvaluationReport:
    return BinaryModelEvaluationReport(
        UUID(str(row[0])),
        UUID(str(row[1])),
        UUID(str(row[2])),
        str(row[3]),
        str(row[4]),
        cast(datetime, row[5]),
        cast(datetime, row[6]),
        cast(datetime, row[7]),
        cast(datetime, row[8]),
        int(cast(Any, row[9])),
        str(row[10]),
        _decimal_map(row[11]),
        _decimal_map(row[12]),
        tuple(
            CalibrationBin(
                Decimal(str(value["lower_bound"])),
                Decimal(str(value["upper_bound"])),
                int(cast(Any, value["count"])),
                None if value["mean_probability"] is None else Decimal(str(value["mean_probability"])),
                None if value["observed_frequency"] is None else Decimal(str(value["observed_frequency"])),
                None if value["absolute_gap"] is None else Decimal(str(value["absolute_gap"])),
            )
            for value in cast(list[dict[str, object]], row[13])
        ),
        _decimal_map(row[14]),
        ModelEvaluationOutcome(str(row[15])),
        tuple(str(value) for value in cast(list[object], row[16])),
        tuple(str(value) for value in cast(list[object], row[17])),
        str(row[18]),
    )


def _policy_from_row(row: tuple[object, ...]) -> BinaryEvaluationPolicy:
    return BinaryEvaluationPolicy(
        UUID(str(row[0])),
        str(row[1]),
        str(row[2]),
        ModelComplexity(str(row[3])),
        ModelComplexity(str(row[4])),
        int(cast(Any, row[5])),
        int(cast(Any, row[6])),
        Decimal(str(row[7])),
        Decimal(str(row[8])),
        Decimal(str(row[9])),
        Decimal(str(row[10])),
        Decimal(str(row[11])),
        Decimal(str(row[12])),
        str(row[13]),
        cast(datetime, row[14]),
        bool(row[15]),
        str(row[16]),
    )


def _observation_from_row(row: tuple[object, ...]) -> BinaryHoldoutObservation:
    return BinaryHoldoutObservation(
        str(row[0]),
        cast(datetime, row[1]),
        int(cast(Any, row[2])),
        Decimal(str(row[3])),
        Decimal(str(row[4])),
        Decimal(str(row[5])),
        Decimal(str(row[6])),
        _decimal_map(row[7]),
        str(row[8]),
        str(row[9]),
    )


def _decimal_map(value: object) -> dict[str, Decimal]:
    return {str(key): Decimal(str(item)) for key, item in cast(dict[str, object], value).items()}


def _decimal_json(values: dict[str, Decimal]) -> dict[str, str]:
    return {name: _decimal_text(value) for name, value in sorted(values.items())}


def _calibration_json(values: tuple[CalibrationBin, ...]) -> list[dict[str, object]]:
    return [
        {
            "lower_bound": _decimal_text(value.lower_bound),
            "upper_bound": _decimal_text(value.upper_bound),
            "count": value.count,
            "mean_probability": (
                None if value.mean_probability is None else _decimal_text(value.mean_probability)
            ),
            "observed_frequency": (
                None if value.observed_frequency is None else _decimal_text(value.observed_frequency)
            ),
            "absolute_gap": None if value.absolute_gap is None else _decimal_text(value.absolute_gap),
        }
        for value in values
    ]


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
