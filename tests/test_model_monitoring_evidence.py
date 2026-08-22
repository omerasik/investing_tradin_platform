import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from tests.test_model_evaluation import NOW, evaluate
from trade_platform.model_evaluation import ModelEvaluationError
from trade_platform.model_monitoring_evidence import (
    DriftDimension,
    ExplanationSensitivityScenario,
    ModelDegradationObservation,
    ModelMonitoringError,
    ModelMonitoringOutcome,
    ModelMonitoringPolicy,
    evaluate_model_monitoring,
)


def policy() -> ModelMonitoringPolicy:
    return ModelMonitoringPolicy.create(
        version="monitoring-v1",
        minimum_sensitivity_scenarios=2,
        maximum_probability_shift=Decimal("0.20"),
        maximum_confidence_degradation=Decimal("0.15"),
        dimension_thresholds={dimension: Decimal("0.20") for dimension in DriftDimension},
        approved_by="model-risk-owner",
        approved_at=NOW - timedelta(days=1),
    )


def monitoring_evidence(evaluation):
    scenarios = (
        ExplanationSensitivityScenario.create(
            scenario_id="trend-down", model_id=evaluation.model_id,
            feature_name="trend", perturbation=Decimal("-0.1"),
            baseline_probability=Decimal("0.8"), perturbed_probability=Decimal("0.72"),
            baseline_confidence=Decimal("0.8"), perturbed_confidence=Decimal("0.74"),
            observed_at=NOW + timedelta(hours=1), source_reference="fixture://sensitivity/down",
        ),
        ExplanationSensitivityScenario.create(
            scenario_id="trend-up", model_id=evaluation.model_id,
            feature_name="trend", perturbation=Decimal("0.1"),
            baseline_probability=Decimal("0.8"), perturbed_probability=Decimal("0.85"),
            baseline_confidence=Decimal("0.8"), perturbed_confidence=Decimal("0.78"),
            observed_at=NOW + timedelta(hours=1), source_reference="fixture://sensitivity/up",
        ),
    )
    degradation = tuple(
        ModelDegradationObservation.create(
            observation_id=f"drift-{dimension.value.lower()}",
            model_id=evaluation.model_id, dimension=dimension, score=Decimal("0.10"),
            observed_at=NOW + timedelta(hours=1),
            source_reference=f"fixture://drift/{dimension.value.lower()}",
        )
        for dimension in DriftDimension
    )
    return scenarios, degradation


def fixture():
    evaluation = evaluate()
    scenarios, degradation = monitoring_evidence(evaluation)
    return evaluation, scenarios, degradation


class ModelMonitoringEvidenceTests(unittest.TestCase):
    def test_complete_bounded_fixture_has_no_observed_threshold_breach(self) -> None:
        evaluation, scenarios, degradation = fixture()
        report = evaluate_model_monitoring(
            policy(), evaluation, scenarios, degradation,
            evaluated_at=NOW + timedelta(hours=2),
        )
        self.assertEqual(
            report.outcome, ModelMonitoringOutcome.NO_THRESHOLD_BREACH_OBSERVED,
        )
        self.assertEqual(report.metrics["drift_dimensions_observed"], Decimal("8"))
        self.assertIn("not_causal", report.limitations[1])

    def test_sensitivity_confidence_and_drift_breaches_require_review(self) -> None:
        evaluation, scenarios, degradation = fixture()
        breached_scenario = ExplanationSensitivityScenario.create(
            scenario_id="trend-down", model_id=evaluation.model_id,
            feature_name="trend", perturbation=Decimal("-0.1"),
            baseline_probability=Decimal("0.8"), perturbed_probability=Decimal("0.4"),
            baseline_confidence=Decimal("0.8"), perturbed_confidence=Decimal("0.4"),
            observed_at=NOW + timedelta(hours=1), source_reference="fixture://sensitivity/down",
        )
        breached_drift = tuple(
            ModelDegradationObservation.create(
                observation_id=item.observation_id, model_id=item.model_id,
                dimension=item.dimension,
                score=(
                    Decimal("0.40")
                    if item.dimension is DriftDimension.PERFORMANCE else item.score
                ),
                observed_at=item.observed_at,
                source_reference=item.source_reference,
            )
            for item in degradation
        )
        report = evaluate_model_monitoring(
            policy(), evaluation, (breached_scenario, scenarios[1]), breached_drift,
            evaluated_at=NOW + timedelta(hours=2),
        )
        self.assertEqual(report.outcome, ModelMonitoringOutcome.DEGRADED_REVIEW_REQUIRED)
        self.assertIn("probability_sensitivity_exceeds_policy", report.reasons)
        self.assertIn("confidence_degradation_exceeds_policy", report.reasons)
        self.assertIn("performance_drift_exceeds_policy", report.reasons)

    def test_all_dimensions_features_and_matching_model_are_required(self) -> None:
        evaluation, scenarios, degradation = fixture()
        with self.assertRaisesRegex(ModelMonitoringError, "invalid_model_monitoring_evidence"):
            evaluate_model_monitoring(
                policy(), evaluation, scenarios, degradation[:-1],
                evaluated_at=NOW + timedelta(hours=2),
            )
        wrong = ExplanationSensitivityScenario.create(
            scenario_id="wrong-model", model_id=uuid4(), feature_name="trend",
            perturbation=Decimal("0.1"), baseline_probability=Decimal("0.8"),
            perturbed_probability=Decimal("0.8"), baseline_confidence=Decimal("0.8"),
            perturbed_confidence=Decimal("0.8"), observed_at=NOW + timedelta(hours=1),
            source_reference="fixture://wrong",
        )
        with self.assertRaisesRegex(ModelMonitoringError, "invalid_model_monitoring_evidence"):
            evaluate_model_monitoring(
                policy(), evaluation, (wrong, scenarios[1]), degradation,
                evaluated_at=NOW + timedelta(hours=2),
            )

    def test_policy_time_blocked_evaluation_and_hashes_fail_closed(self) -> None:
        evaluation, scenarios, degradation = fixture()
        future_policy = ModelMonitoringPolicy.create(
            version="future", minimum_sensitivity_scenarios=2,
            maximum_probability_shift=Decimal("0.2"),
            maximum_confidence_degradation=Decimal("0.2"),
            dimension_thresholds={dimension: Decimal("0.2") for dimension in DriftDimension},
            approved_by="owner", approved_at=NOW + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ModelMonitoringError, "invalid_model_monitoring_evidence"):
            evaluate_model_monitoring(
                future_policy, evaluation, scenarios, degradation,
                evaluated_at=NOW + timedelta(hours=2),
            )
        with self.assertRaises(ModelMonitoringError):
            replace(scenarios[0], content_hash="0" * 64)
        with self.assertRaisesRegex(ModelEvaluationError, "report_hash_mismatch"):
            evaluate_model_monitoring(
                policy(), replace(evaluation, feature_importance={"trend": Decimal("0.9")}),
                scenarios, degradation, evaluated_at=NOW + timedelta(hours=2),
            )

    def test_report_identity_is_deterministic_under_evidence_order(self) -> None:
        evaluation, scenarios, degradation = fixture()
        first = evaluate_model_monitoring(
            policy(), evaluation, scenarios, degradation,
            evaluated_at=NOW + timedelta(hours=2),
        )
        second = evaluate_model_monitoring(
            policy(), evaluation, tuple(reversed(scenarios)), tuple(reversed(degradation)),
            evaluated_at=NOW + timedelta(hours=2),
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
