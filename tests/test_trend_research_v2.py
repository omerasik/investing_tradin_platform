import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from trade_platform.cross_engine import GoldenExecutionScenario
from trade_platform.data_health import (
    DataHealthAction,
    DataHealthAssessment,
    DataHealthScope,
)
from trade_platform.feature_authority import (
    FeatureDefinitionVersion,
    FeatureFamily,
    FeatureMaterialization,
    FeatureQualityStatus,
)
from trade_platform.research import CostModel, WalkForwardProtocol
from trade_platform.strategy_scorecard_v2 import EvidenceState, ScorecardStatus
from trade_platform.trend_research_v2 import (
    InMemoryFeatureAuthority,
    SealedTrendDataset,
    TrendResearchBar,
    TrendResearchOrchestratorV2,
    TrendResearchRequest,
    TrendResearchStatus,
    TrendResearchV2Error,
)
from trade_platform.trend_strategy_v2 import (
    TrendFeatureRequirement,
    TrendStrategyDefinitionV2,
    TrendStrategyKind,
)

START = datetime(2025, 1, 1, 20, tzinfo=UTC)


def feature_definition(name: str, family: FeatureFamily) -> FeatureDefinitionVersion:
    return FeatureDefinitionVersion(
        name,
        family,
        "1.0.0",
        "quant",
        f"Cycle 200 transparent {name} engineering definition.",
        ("OHLCV",),
        ("close", "event_at", "knowledge_at"),
        "1d",
        "event/effective/knowledge bounded",
        1,
        {},
        "reject",
        "reject",
        "reject_future_knowledge",
        None,
        None,
        "decimal",
        "transparent-market-v1",
        START,
        feature_id=uuid5(NAMESPACE_URL, f"trend-research-feature:{name}"),
    )


def strategy(definition: FeatureDefinitionVersion) -> TrendStrategyDefinitionV2:
    requirement = TrendFeatureRequirement(
        definition.feature_id, definition.name, definition.semantic_version
    )
    return TrendStrategyDefinitionV2(
        uuid5(NAMESPACE_URL, "cycle202:trend-strategy"),
        "trend-v2-time-series-momentum-1.0.0",
        "trend-v2-code-1",
        TrendStrategyKind.TIME_SERIES_MOMENTUM,
        "Persistent returns can continue after transparent synthetic costs.",
        "Cycle 200 momentum materializations map to bounded long/flat exposure.",
        ("Liquid synthetic US equity fixture",),
        (requirement,),
        "OHLCV",
        (("threshold", "0"),),
        "Enter when authoritative momentum is positive.",
        "Exit when authoritative momentum is non-positive.",
        "Any unavailable feature or blocked data health invalidates the run.",
        "Long/flat with a hard research exposure cap.",
        Decimal("0.5"),
        "Multi-day",
        "cost-v2",
        "Synthetic OHLCV/ADV only.",
        ("Sharp reversal", "Whipsaw"),
        ("SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",),
        START,
    )


def dataset() -> SealedTrendDataset:
    bars = []
    for index in range(36):
        close = Decimal("100") + Decimal(index) + Decimal(index % 3) / Decimal("10")
        bars.append(
            TrendResearchBar(
                START + timedelta(days=index),
                close,
                close - Decimal("0.01"),
                close + Decimal("0.01"),
                Decimal("1000000"),
            )
        )
    return SealedTrendDataset(
        uuid5(NAMESPACE_URL, "cycle202:dataset"),
        uuid5(NAMESPACE_URL, "cycle202:dataset-version"),
        "trend-synthetic-fixture-v2",
        "OHLCV",
        "fixture:SPY",
        "EQUITY",
        True,
        tuple(bars),
    )


def materializations(
    definition: FeatureDefinitionVersion,
    data: SealedTrendDataset,
    *,
    missing_index: int | None = None,
) -> tuple[FeatureMaterialization, ...]:
    result = []
    for index, bar in enumerate(data.bars):
        value = None if index == missing_index else Decimal("1") if index % 8 != 0 else Decimal("-1")
        quality = (
            FeatureQualityStatus.REJECTED
            if index == missing_index
            else FeatureQualityStatus.VALIDATED
        )
        result.append(
            FeatureMaterialization.create(
                feature_id=definition.feature_id,
                instrument_id=data.instrument_id,
                dataset_version=data.version,
                event_at=bar.occurred_at,
                effective_at=bar.occurred_at,
                knowledge_at=bar.occurred_at + timedelta(minutes=1),
                computed_at=bar.occurred_at + timedelta(minutes=1),
                source_observation_manifest=(f"synthetic:bar:{index}",),
                value=value,
                quality_status=quality,
            )
        )
    return tuple(result)


def health(data: SealedTrendDataset, *, blocked: bool = False) -> DataHealthAssessment:
    action = DataHealthAction.BLOCK_STRATEGY if blocked else DataHealthAction.INFO
    return DataHealthAssessment(
        uuid5(NAMESPACE_URL, f"cycle202:health:{blocked}"),
        data.dataset_version_id,
        DataHealthScope.STRATEGY,
        "trend-v2-time-series-momentum-1.0.0",
        "synthetic-health-v1",
        data.bars[-1].occurred_at,
        data.bars[0].occurred_at,
        data.bars[-1].occurred_at,
        action,
        blocked,
        (),
        "a" * 64 if blocked else "b" * 64,
    )


def request(
    feature: FeatureDefinitionVersion,
    data: SealedTrendDataset,
    *,
    blocked: bool = False,
) -> TrendResearchRequest:
    decision_at = data.bars[-1].occurred_at + timedelta(minutes=1)
    return TrendResearchRequest(
        strategy(feature),
        data,
        "cost-v2",
        CostModel(Decimal("0.00001"), Decimal("0.0001"), Decimal("0.0001")),
        decision_at,
        decision_at + timedelta(minutes=1),
        health(data, blocked=blocked),
        GoldenExecutionScenario(
            "synthetic-realism-v2",
            latency=timedelta(days=1),
            maximum_participation=Decimal("0.5"),
            commission_bps=Decimal("1"),
            fixed_fee=Decimal("0"),
            impact_coefficient=Decimal("0.001"),
        ),
        WalkForwardProtocol(8, 4, 4, 4, purge=1, embargo=1),
        3,
        16,
    )


class TrendResearchV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.feature = feature_definition("multi_horizon_momentum", FeatureFamily.MOMENTUM)
        self.data = dataset()

    def test_complete_chain_is_bound_replayable_and_review_only(self) -> None:
        authority = InMemoryFeatureAuthority(
            (self.feature,), materializations(self.feature, self.data)
        )
        orchestrator = TrendResearchOrchestratorV2(authority)
        first = orchestrator.launch(request(self.feature, self.data))
        second = orchestrator.launch(request(self.feature, self.data))
        self.assertEqual(first.status, TrendResearchStatus.REVIEW_REQUIRED)
        self.assertIsNotNone(first.run)
        run = first.run
        assert run is not None
        self.assertEqual(second.run, run)
        self.assertTrue(run.independent_result.reconciled)
        self.assertEqual(run.event_result.unexplained_differences, ())
        self.assertEqual(len(run.quant_evidence), 8)
        self.assertTrue(run.walk_forward_results)
        self.assertEqual(run.scorecard.status, ScorecardStatus.REVIEW_REQUIRED)
        self.assertEqual(run.validation_package.validation_metadata["maximum_automatic_state"], "REVIEW_REQUIRED")
        self.assertEqual(run.validation_package.validation_metadata["paper_authority"], False)
        self.assertEqual(run.validation_package.validation_metadata["live_authority"], False)
        self.assertIn("SYNTHETIC_ENGINEERING_EVIDENCE_ONLY", run.limitations)
        for evidence in run.validation_evidence:
            self.assertEqual(evidence.feature_versions, run.definition.feature_versions)
            self.assertEqual(evidence.dataset_version, run.dataset.version)
            self.assertEqual(evidence.cost_model_version, "cost-v2")
        unavailable = {
            metric.name
            for metric in run.scorecard.metrics
            if metric.state is EvidenceState.UNAVAILABLE
        }
        self.assertTrue({"regime_performance", "real_capacity", "real_slippage", "live_consistency"} <= unavailable)
        self.assertFalse(hasattr(orchestrator, "activate"))
        self.assertFalse(hasattr(orchestrator, "submit"))

    def test_missing_feature_is_an_explicit_unavailable_outcome(self) -> None:
        authority = InMemoryFeatureAuthority(
            (self.feature,), materializations(self.feature, self.data, missing_index=10)
        )
        outcome = TrendResearchOrchestratorV2(authority).launch(request(self.feature, self.data))
        self.assertEqual(outcome.status, TrendResearchStatus.UNAVAILABLE)
        self.assertIn("required_feature_unavailable", outcome.reasons)
        self.assertIsNone(outcome.run)

    def test_feature_version_dataset_future_and_data_health_are_rejected(self) -> None:
        wrong_version = replace(strategy(self.feature), required_features=(
            TrendFeatureRequirement(self.feature.feature_id, self.feature.name, "2.0.0"),
        ))
        base = request(self.feature, self.data)
        authority = InMemoryFeatureAuthority(
            (self.feature,), materializations(self.feature, self.data)
        )
        with self.assertRaisesRegex(TrendResearchV2Error, "feature_version_mismatch"):
            TrendResearchOrchestratorV2(authority).launch(replace(base, definition=wrong_version))
        other_data = replace(self.data, version="other-dataset-v2")
        unavailable = TrendResearchOrchestratorV2(authority).launch(
            replace(base, dataset=other_data)
        )
        self.assertEqual(unavailable.status, TrendResearchStatus.UNAVAILABLE)
        with self.assertRaisesRegex(TrendResearchV2Error, "blocked_by_data_health"):
            TrendResearchOrchestratorV2(authority).launch(request(self.feature, self.data, blocked=True))

        class FutureAuthority(InMemoryFeatureAuthority):
            def latest_as_of(self, feature_id, instrument_id, dataset_version, decision_at):
                items = super().latest_as_of(feature_id, instrument_id, dataset_version, decision_at)
                return items + (replace(items[-1], computed_at=decision_at + timedelta(days=1)),)

        with self.assertRaisesRegex(TrendResearchV2Error, "future_feature_knowledge"):
            TrendResearchOrchestratorV2(
                FutureAuthority((self.feature,), materializations(self.feature, self.data))
            ).launch(base)

    def test_unexplained_event_divergence_blocks_review_eligibility(self) -> None:
        authority = InMemoryFeatureAuthority(
            (self.feature,), materializations(self.feature, self.data)
        )
        from trade_platform import trend_research_v2

        original = trend_research_v2.run_realistic_golden_vector_event_reconciliation

        def divergent(**kwargs):
            report = original(**kwargs)
            return replace(
                report,
                reconciled=False,
                differences=("forced_material_divergence",),
                unexplained_differences=("forced_material_divergence",),
            )

        with patch.object(
            trend_research_v2,
            "run_realistic_golden_vector_event_reconciliation",
            divergent,
        ):
            outcome = TrendResearchOrchestratorV2(authority).launch(
                request(self.feature, self.data)
            )
        self.assertEqual(outcome.status, TrendResearchStatus.BLOCKED)
        assert outcome.run is not None
        golden = next(
            item
            for item in outcome.run.validation_evidence
            if item.evidence_type == "golden_reconciliation"
        )
        self.assertFalse(golden.passed)


if __name__ == "__main__":
    unittest.main()
