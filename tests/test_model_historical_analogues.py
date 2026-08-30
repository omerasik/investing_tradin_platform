import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from test_model_evaluation import NOW, evaluate

from trade_platform.model_historical_analogues import (
    HistoricalAnalogueCandidate,
    HistoricalAnalogueError,
    HistoricalAnalogueOutcome,
    HistoricalAnaloguePolicy,
    ModelExplanationTarget,
    evaluate_historical_analogues,
)


def analogue_policy(*, enabled: bool = True) -> HistoricalAnaloguePolicy:
    return HistoricalAnaloguePolicy.create(
        version=f"historical-analogue-v1-{enabled}",
        minimum_similarity=Decimal("0.70"),
        minimum_analogues=3,
        maximum_analogues=3,
        minimum_distinct_regimes=2,
        minimum_distinct_source_families=2,
        maximum_probability_outcome_gap=Decimal("0.30"),
        approved_by="model-risk-owner",
        approved_at=NOW - timedelta(days=1),
        enabled=enabled,
    )


def target(
    evaluation, *, predicted_probability: Decimal = Decimal("0.75")
) -> ModelExplanationTarget:
    return ModelExplanationTarget.create(
        target_id=uuid4(),
        model_id=evaluation.model_id,
        dataset_version=evaluation.dataset_version,
        feature_version=evaluation.feature_version,
        instrument_id="fixture-asset",
        observed_at=NOW + timedelta(days=1),
        available_at=NOW + timedelta(days=1, minutes=1),
        predicted_probability=predicted_probability,
        confidence=Decimal("0.80"),
        regime="target-regime",
        normalized_features={"trend": Decimal("0.80")},
        source_reference="fixture://target/1",
    )


def candidates(
    model_id,
    *,
    outcomes: tuple[int, ...] = (1, 0, 1, 0),
) -> tuple[HistoricalAnalogueCandidate, ...]:
    features = ("0.75", "0.65", "0.90", "0.10")
    regimes = ("bull", "bear", "bull", "flat")
    sources = ("prices", "macro", "prices", "news")
    return tuple(
        HistoricalAnalogueCandidate.create(
            analogue_id=uuid4(),
            model_id=model_id,
            dataset_version="fixture-holdout:v1",
            feature_version="features:v1",
            instrument_id="fixture-asset",
            regime=regimes[index],
            observed_at=NOW - timedelta(days=10 - index),
            available_at=NOW - timedelta(days=10 - index, minutes=-1),
            outcome_available_at=NOW - timedelta(days=9 - index),
            normalized_features={"trend": Decimal(features[index])},
            actual_outcome=outcome,
            realized_return=Decimal("0.02") if outcome else Decimal("-0.01"),
            source_family=sources[index],
            source_reference=f"fixture://analogue/{index}",
        )
        for index, outcome in enumerate(outcomes)
    )


class HistoricalAnalogueTests(unittest.TestCase):
    def test_ready_report_is_point_in_time_ranked_and_non_executing(self) -> None:
        evaluation = evaluate()
        explanation_target = target(evaluation)
        evidence = candidates(evaluation.model_id)
        report, matches = evaluate_historical_analogues(
            analogue_policy(),
            evaluation,
            explanation_target,
            evidence,
            evaluated_at=NOW + timedelta(days=1, minutes=2),
        )
        self.assertEqual(report.outcome, HistoricalAnalogueOutcome.READY_FOR_REVIEW)
        self.assertEqual((report.screened_count, report.selected_count), (4, 3))
        self.assertEqual(report.distinct_regime_count, 2)
        self.assertEqual(report.distinct_source_family_count, 2)
        self.assertEqual(sum(item.selected for item in matches), 3)
        self.assertEqual(
            sorted(item.selection_rank for item in matches if item.selected),
            [1, 2, 3],
        )
        self.assertEqual(
            (
                report.model_invocation_authority,
                report.prediction_authority,
                report.action_authority,
            ),
            ("NONE", "NONE", "NONE"),
        )
        self.assertIn("not_semantic_or_causal", report.limitations[0])

    def test_divergent_historical_outcomes_require_review(self) -> None:
        evaluation = evaluate()
        explanation_target = target(evaluation, predicted_probability=Decimal("0.95"))
        evidence = candidates(evaluation.model_id, outcomes=(0, 0, 0, 0))
        report, _ = evaluate_historical_analogues(
            analogue_policy(),
            evaluation,
            explanation_target,
            evidence,
            evaluated_at=NOW + timedelta(days=1, minutes=2),
        )
        self.assertEqual(
            report.outcome,
            HistoricalAnalogueOutcome.DIVERGENCE_REVIEW_REQUIRED,
        )
        self.assertEqual(report.weighted_outcome_frequency, Decimal("0E-12"))
        self.assertEqual(report.probability_outcome_gap, Decimal("0.950000000000"))

    def test_insufficient_and_disabled_policies_fail_closed(self) -> None:
        evaluation = evaluate()
        explanation_target = target(evaluation)
        evidence = candidates(evaluation.model_id)[:2]
        insufficient, _ = evaluate_historical_analogues(
            analogue_policy(),
            evaluation,
            explanation_target,
            evidence,
            evaluated_at=NOW + timedelta(days=1, minutes=2),
        )
        disabled, _ = evaluate_historical_analogues(
            analogue_policy(enabled=False),
            evaluation,
            explanation_target,
            evidence,
            evaluated_at=NOW + timedelta(days=1, minutes=2),
        )
        self.assertEqual(
            insufficient.outcome,
            HistoricalAnalogueOutcome.BLOCKED_INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(disabled.outcome, HistoricalAnalogueOutcome.BLOCKED_POLICY_DISABLED)

    def test_lookahead_version_and_blocked_evaluation_are_rejected(self) -> None:
        evaluation = evaluate()
        explanation_target = target(evaluation)
        evidence = candidates(evaluation.model_id)
        lookahead = replace(
            evidence[0],
            outcome_available_at=explanation_target.available_at + timedelta(seconds=1),
        )
        with self.assertRaises(HistoricalAnalogueError):
            evaluate_historical_analogues(
                analogue_policy(),
                evaluation,
                explanation_target,
                (lookahead, *evidence[1:]),
                evaluated_at=NOW + timedelta(days=1, minutes=2),
            )
        with self.assertRaises(HistoricalAnalogueError):
            evaluate_historical_analogues(
                analogue_policy(),
                evaluation,
                replace(explanation_target, feature_version="wrong"),
                evidence,
                evaluated_at=NOW + timedelta(days=1, minutes=2),
            )
        with self.assertRaises(HistoricalAnalogueError):
            evaluate_historical_analogues(
                analogue_policy(),
                evaluate(weak=True),
                explanation_target,
                evidence,
                evaluated_at=NOW + timedelta(days=1, minutes=2),
            )

    def test_non_finite_and_tampered_evidence_are_rejected(self) -> None:
        evaluation = evaluate()
        explanation_target = target(evaluation)
        evidence = candidates(evaluation.model_id)
        with self.assertRaises(HistoricalAnalogueError):
            evaluate_historical_analogues(
                analogue_policy(),
                evaluation,
                replace(explanation_target, predicted_probability=Decimal("NaN")),
                evidence,
                evaluated_at=NOW + timedelta(days=1, minutes=2),
            )
        with self.assertRaises(HistoricalAnalogueError):
            evaluate_historical_analogues(
                analogue_policy(),
                evaluation,
                explanation_target,
                (replace(evidence[0], actual_outcome=0), *evidence[1:]),
                evaluated_at=NOW + timedelta(days=1, minutes=2),
            )

    def test_identity_and_member_graph_are_input_order_invariant(self) -> None:
        evaluation = evaluate()
        explanation_target = target(evaluation)
        evidence = candidates(evaluation.model_id)
        kwargs = {"evaluated_at": NOW + timedelta(days=1, minutes=2)}
        first = evaluate_historical_analogues(
            analogue_policy(),
            evaluation,
            explanation_target,
            evidence,
            **kwargs,
        )
        second = evaluate_historical_analogues(
            analogue_policy(),
            evaluation,
            explanation_target,
            tuple(reversed(evidence)),
            **kwargs,
        )
        self.assertEqual(first, second)
        self.assertFalse(
            next(
                item for item in first[1] if item.similarity == Decimal("0.300000000000")
            ).selected,
        )


if __name__ == "__main__":
    unittest.main()
