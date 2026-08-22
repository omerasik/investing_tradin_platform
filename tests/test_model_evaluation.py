import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.model_evaluation import (
    BinaryEvaluationPolicy,
    BinaryHoldoutObservation,
    ModelComplexity,
    ModelEvaluationError,
    ModelEvaluationOutcome,
    evaluate_binary_classifier,
)
from trade_platform.model_registry import ModelVersion

NOW = datetime(2026, 1, 20, tzinfo=UTC)


def policy(*, minimum_brier_improvement: Decimal = Decimal("0.10")) -> BinaryEvaluationPolicy:
    return BinaryEvaluationPolicy.create(
        version="binary-direction-v1",
        task="return_direction_probability",
        candidate_complexity=ModelComplexity.LINEAR,
        predecessor_complexity=ModelComplexity.RULE_BASED,
        minimum_holdout_observations=8,
        calibration_bins=2,
        maximum_expected_calibration_error=Decimal("0.11"),
        minimum_brier_improvement=minimum_brier_improvement,
        minimum_economic_improvement_after_cost=Decimal("0.004"),
        maximum_half_brier_gap=Decimal("0.01"),
        decision_threshold=Decimal("0.5"),
        assumed_cost_per_positive_decision=Decimal("0"),
        approved_by="model-risk-owner",
        approved_at=NOW - timedelta(days=20),
    )


def candidate() -> ModelVersion:
    return ModelVersion(
        uuid4(),
        "fixture-direction",
        "v1",
        "return_direction_probability",
        "features:v1",
        "fixture-holdout:v1",
        "research://fixture-model/v1",
        NOW - timedelta(days=12),
    )


def observations(*, weak: bool = False) -> tuple[BinaryHoldoutObservation, ...]:
    values = []
    for index in range(8):
        actual = index % 2
        probability = (
            Decimal("0.55") if actual else Decimal("0.45")
        ) if weak else (Decimal("0.9") if actual else Decimal("0.1"))
        contribution = probability - Decimal("0.5")
        values.append(
            BinaryHoldoutObservation.create(
                observation_id=f"fixture-{index}",
                observed_at=NOW - timedelta(days=9 - index),
                actual_outcome=actual,
                candidate_probability=probability,
                predecessor_probability=Decimal("0.5"),
                realized_return=Decimal("0.01") if actual else Decimal("-0.01"),
                explanation_base_probability=Decimal("0.5"),
                feature_contributions={"trend": contribution},
                source_reference=f"fixture://holdout/{index}",
            )
        )
    return tuple(values)


def evaluate(*, weak: bool = False):
    return evaluate_binary_classifier(
        model=candidate(),
        policy=policy(),
        observations=reversed(observations(weak=weak)),
        training_end=NOW - timedelta(days=11),
        holdout_start=NOW - timedelta(days=10),
        holdout_end=NOW - timedelta(days=1),
        evaluated_at=NOW,
    )


class ModelEvaluationTests(unittest.TestCase):
    def test_chronological_holdout_is_calibrated_explainable_and_review_only(self) -> None:
        report = evaluate()
        self.assertEqual(report.outcome, ModelEvaluationOutcome.REVIEW_ELIGIBLE)
        self.assertEqual(report.candidate_metrics["precision"], Decimal("1"))
        self.assertEqual(report.candidate_metrics["recall"], Decimal("1"))
        self.assertEqual(report.candidate_metrics["roc_auc"], Decimal("1"))
        self.assertEqual(report.candidate_metrics["brier_score"], Decimal("0.01"))
        self.assertEqual(report.candidate_metrics["expected_calibration_error"], Decimal("0.1"))
        self.assertEqual(report.feature_importance, {"trend": Decimal("0.4")})
        self.assertIn("POST_HOC_NOT_CAUSAL", report.limitations[1])
        validation = report.to_model_validation()
        self.assertEqual(validation.model_id, report.model_id)
        self.assertIn(str(report.report_id), validation.evidence_reference)
        self.assertNotIn("APPROVED", report.outcome.value)

    def test_weak_candidate_is_blocked_and_cannot_create_registry_validation(self) -> None:
        report = evaluate(weak=True)
        self.assertEqual(report.outcome, ModelEvaluationOutcome.BLOCKED)
        self.assertIn("insufficient_brier_improvement_over_predecessor", report.reasons)
        self.assertIn("expected_calibration_error_exceeds_policy", report.reasons)
        with self.assertRaises(PermissionError):
            report.to_model_validation()

    def test_invalid_split_duplicate_and_incomplete_labels_fail_closed(self) -> None:
        model = candidate()
        evidence = observations()
        with self.assertRaises(ModelEvaluationError):
            evaluate_binary_classifier(
                model=model,
                policy=policy(),
                observations=evidence,
                training_end=NOW - timedelta(days=10),
                holdout_start=NOW - timedelta(days=10),
                holdout_end=NOW - timedelta(days=1),
                evaluated_at=NOW,
            )
        with self.assertRaises(ModelEvaluationError):
            evaluate_binary_classifier(
                model=model,
                policy=policy(),
                observations=(*evidence[:-1], evidence[0]),
                training_end=NOW - timedelta(days=11),
                holdout_start=NOW - timedelta(days=10),
                holdout_end=NOW - timedelta(days=1),
                evaluated_at=NOW,
            )
        with self.assertRaises(ModelEvaluationError):
            evaluate_binary_classifier(
                model=model,
                policy=policy(),
                observations=tuple(value for value in evidence if value.actual_outcome == 1) * 2,
                training_end=NOW - timedelta(days=11),
                holdout_start=NOW - timedelta(days=10),
                holdout_end=NOW - timedelta(days=1),
                evaluated_at=NOW,
            )

    def test_complexity_progression_and_local_explanation_are_exact(self) -> None:
        with self.assertRaises(ModelEvaluationError):
            BinaryEvaluationPolicy.create(
                version="bad",
                task="return_direction_probability",
                candidate_complexity=ModelComplexity.NEURAL,
                predecessor_complexity=ModelComplexity.LINEAR,
                minimum_holdout_observations=8,
                calibration_bins=2,
                maximum_expected_calibration_error=Decimal("0.1"),
                minimum_brier_improvement=Decimal("0.01"),
                minimum_economic_improvement_after_cost=Decimal("0"),
                maximum_half_brier_gap=Decimal("0.1"),
                decision_threshold=Decimal("0.5"),
                assumed_cost_per_positive_decision=Decimal("0"),
                approved_by="owner",
                approved_at=NOW,
            )
        with self.assertRaises(ModelEvaluationError):
            BinaryHoldoutObservation.create(
                observation_id="bad-explanation",
                observed_at=NOW,
                actual_outcome=1,
                candidate_probability=Decimal("0.9"),
                predecessor_probability=Decimal("0.5"),
                realized_return=Decimal("0.01"),
                explanation_base_probability=Decimal("0.5"),
                feature_contributions={"trend": Decimal("0.3")},
                source_reference="fixture://bad",
            )

    def test_report_identity_is_deterministic_under_input_order(self) -> None:
        model = candidate()
        evidence = observations()
        kwargs = {
            "model": model,
            "policy": policy(),
            "training_end": NOW - timedelta(days=11),
            "holdout_start": NOW - timedelta(days=10),
            "holdout_end": NOW - timedelta(days=1),
            "evaluated_at": NOW,
        }
        first = evaluate_binary_classifier(observations=evidence, **kwargs)
        second = evaluate_binary_classifier(observations=reversed(evidence), **kwargs)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
