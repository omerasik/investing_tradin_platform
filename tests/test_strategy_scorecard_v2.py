import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trade_platform.strategy_scorecard_v2 import (
    EvidenceState,
    MetricFamily,
    MetricObservation,
    ScorecardStatus,
    ScorecardV2Error,
    StrategyScorecardV2,
    complexity_components,
    performance_metrics,
    tail_risk_metrics,
    trade_activity_metrics,
)


class StrategyScorecardV2Tests(unittest.TestCase):
    def _scorecard(self, *, health: str = "HEALTHY") -> StrategyScorecardV2:
        now = datetime(2025, 1, 1, tzinfo=UTC)
        return StrategyScorecardV2(
            "scorecard-v2",
            uuid4(),
            "fixture-v1",
            uuid4(),
            "dataset-v1",
            ("feature-v1",),
            "cost-v1",
            now,
            now,
            ScorecardStatus.REVIEW_REQUIRED,
            ("Synthetic fixture; no investment conclusion.",),
            performance_metrics((Decimal("0.01"), Decimal("-0.02"))),
            (),
            health,
            (),
            {"research_run": "a" * 64},
        )

    def test_performance_metrics_are_explicit_about_unavailable_annualization_and_zero_volatility(self) -> None:
        metrics = performance_metrics((Decimal("0.01"), Decimal("0.01")))
        by_name = {item.name: item for item in metrics}
        self.assertEqual(by_name["cagr"].state, EvidenceState.UNAVAILABLE)
        self.assertEqual(by_name["sharpe"].state, EvidenceState.UNAVAILABLE)
        self.assertEqual(by_name["number_of_trades"].value, Decimal("2"))

    def test_negative_returns_have_drawdown_tail_and_sortino_evidence(self) -> None:
        metrics = performance_metrics(
            (Decimal("0.1"), Decimal("-0.2"), Decimal("0.05")), periods_per_year=3
        )
        by_name = {item.name: item for item in metrics}
        self.assertEqual(by_name["max_drawdown"].family, MetricFamily.PERFORMANCE)
        self.assertLess(by_name["max_drawdown"].value, Decimal("0"))
        self.assertIsNotNone(by_name["sortino"].value)
        self.assertIsNotNone(by_name["sharpe"].value)
        self.assertIsNotNone(by_name["calmar"].value)
        no_trade = {item.name: item for item in performance_metrics(())}
        self.assertEqual(no_trade["number_of_trades"].value, Decimal("0"))
        self.assertEqual(no_trade["sharpe"].state, EvidenceState.UNAVAILABLE)

    def test_tail_risk_and_complexity_are_explicit_and_non_authoritative(self) -> None:
        tail = tail_risk_metrics((Decimal("-0.02"), Decimal("0.01")), confidence=Decimal("0.5"))
        self.assertEqual(tail[0].family, MetricFamily.RISK)
        self.assertEqual(tail[0].state, EvidenceState.MEASURED)
        low = complexity_components(parameter_count=2, turnover=Decimal("1"), sample_size=100)
        high = complexity_components(parameter_count=2, turnover=Decimal("4"), sample_size=100)
        self.assertLess(low[0].value, high[0].value)
        activity = trade_activity_metrics(
            turnover=None, exposure=Decimal("0.5"), average_holding_period=None
        )
        self.assertEqual(activity[0].state, EvidenceState.UNAVAILABLE)
        self.assertEqual(activity[1].value, Decimal("0.5"))

    def test_blocked_health_cannot_be_represented_as_reviewable_and_manifest_is_required(self) -> None:
        with self.assertRaisesRegex(ScorecardV2Error, "blocked_data_health"):
            self._scorecard(health="BLOCKED").validate()
        with self.assertRaisesRegex(ScorecardV2Error, "evidence_manifest"):
            self._scorecard().__class__(
                "scorecard-v2",
                uuid4(),
                "fixture-v1",
                uuid4(),
                "dataset-v1",
                ("feature-v1",),
                "cost-v1",
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, tzinfo=UTC),
                ScorecardStatus.REVIEW_REQUIRED,
                ("Synthetic fixture; no investment conclusion.",),
                performance_metrics((Decimal("0.01"),)),
                (),
                "HEALTHY",
                (),
                {"research_run": "not-a-hash"},
            ).validate()

    def test_identity_replays_from_canonical_evidence_and_assumptions_remain_labeled(self) -> None:
        scorecard = self._scorecard()
        self.assertEqual(scorecard.scorecard_id, replace(scorecard).scorecard_id)
        observation = MetricObservation(
            MetricFamily.EXECUTION,
            "fee_assumption",
            EvidenceState.ASSUMED,
            Decimal("0.001"),
            "fraction",
        )
        observation.validate()
