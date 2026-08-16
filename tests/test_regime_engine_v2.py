import hashlib
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from trade_platform.feature_authority import (
    FeatureDefinitionVersion,
    FeatureFamily,
    FeatureMaterialization,
    FeatureQualityStatus,
)
from trade_platform.regime_engine_v2 import (
    DIMENSION_STATES,
    EvidenceState,
    RegimeDimension,
    RegimeEngineV2Error,
    RegimeFeatureRequirement,
    RegimeMethod,
    RegimeModelDefinitionV2,
    RegimeOutcomeLabel,
    RegimeResearchOrchestratorV2,
    RegimeState,
    ReviewStatus,
    RiskAdjustmentAction,
    build_regime_risk_candidate,
)

TIMES = tuple(datetime(2025, 1, day, tzinfo=UTC) for day in (10, 11, 12))
ROLES = (
    "trend",
    "volatility",
    "liquidity",
    "inflation",
    "policy",
    "credit",
    "currency",
    "commodity",
    "correlation",
)
HEALTH_ID = uuid5(NAMESPACE_URL, "cycle204:data-health")
DATASET_ID = uuid5(NAMESPACE_URL, "cycle204:dataset")
DATASET_HASH = hashlib.sha256(b"cycle204-regime-dataset").hexdigest()


class FixtureFeatureAuthority:
    def __init__(
        self,
        definitions: tuple[FeatureDefinitionVersion, ...],
        materializations: tuple[FeatureMaterialization, ...],
    ) -> None:
        self.definitions = {item.feature_id: item for item in definitions}
        self.materializations = materializations

    def definition(self, feature_id: UUID) -> FeatureDefinitionVersion:
        return self.definitions[feature_id]

    def latest_as_of(
        self,
        feature_id: UUID,
        instrument_id: str,
        dataset_version: str,
        decision_at: datetime,
    ) -> tuple[FeatureMaterialization, ...]:
        return tuple(
            item
            for item in self.materializations
            if item.feature_id == feature_id
            and item.instrument_id == instrument_id
            and item.dataset_version == dataset_version
            and item.event_at <= decision_at
        )


def feature_definitions() -> tuple[FeatureDefinitionVersion, ...]:
    created_at = datetime(2024, 12, 31, tzinfo=UTC)
    result = []
    for role in ROLES:
        family = (
            FeatureFamily.TREND
            if role == "trend"
            else FeatureFamily.VOLATILITY
            if role == "volatility"
            else FeatureFamily.LIQUIDITY
            if role == "liquidity"
            else FeatureFamily.MACRO
        )
        result.append(
            FeatureDefinitionVersion(
                f"regime_{role}",
                family,
                "1.0.0",
                "cycle204",
                f"Fixture {role} input.",
                ("REGIME_FIXTURE",),
                ("value", "event_at", "knowledge_at"),
                "1d",
                "point-in-time",
                1,
                {},
                "reject",
                "reject",
                "reject_future_knowledge",
                None,
                None,
                "decimal",
                "cycle204-fixture-v1",
                created_at,
                feature_id=uuid5(NAMESPACE_URL, f"cycle204:feature:{role}"),
            )
        )
    return tuple(result)


def definition(
    definitions: tuple[FeatureDefinitionVersion, ...] | None = None,
) -> RegimeModelDefinitionV2:
    selected = definitions or feature_definitions()
    by_name = {item.name.removeprefix("regime_"): item for item in selected}
    return RegimeModelDefinitionV2(
        uuid5(NAMESPACE_URL, "cycle204:regime-model"),
        "1.0.0",
        "regime-engine-v2.0.0",
        datetime(2024, 12, 30, tzinfo=UTC),
        datetime(2024, 12, 31, tzinfo=UTC),
        tuple(RegimeFeatureRequirement(role, by_name[role].feature_id, "1.0.0") for role in ROLES),
    )


def materializations(
    definitions: tuple[FeatureDefinitionVersion, ...] | None = None,
) -> tuple[FeatureMaterialization, ...]:
    selected = definitions or feature_definitions()
    values = {
        "trend": ("0.01", "0.03", "-0.02"),
        "volatility": ("0.01", "0.015", "0.03"),
        "liquidity": ("2000000", "1800000", "900000"),
        "inflation": ("-0.01", "0.01", "0.02"),
        "policy": ("-0.01", "0", "0.01"),
        "credit": ("0.1", "0.2", "0.8"),
        "currency": ("0.1", "0.1", "0.7"),
        "commodity": ("0.1", "0.2", "0.6"),
        "correlation": ("0.1", "0.3", "0.9"),
    }
    result = []
    for feature in selected:
        role = feature.name.removeprefix("regime_")
        for event_at, value in zip(TIMES, values[role], strict=True):
            result.append(
                replace(
                    FeatureMaterialization.create(
                        feature_id=feature.feature_id,
                        instrument_id="US:XNYS:CYCLE204",
                        dataset_version="cycle204-v1",
                        event_at=event_at,
                        effective_at=event_at,
                        knowledge_at=event_at,
                        computed_at=event_at,
                        source_observation_manifest=(
                            f"fixture://cycle204/{role}/{event_at.date()}",
                        ),
                        value=Decimal(value),
                        quality_status=FeatureQualityStatus.VALIDATED,
                    ),
                    materialization_id=uuid5(
                        NAMESPACE_URL, f"cycle204:materialization:{role}:{event_at.isoformat()}"
                    ),
                )
            )
    return tuple(result)


def labels() -> tuple[RegimeOutcomeLabel, ...]:
    states = (RegimeState.BULL_TREND, RegimeState.BULL_TREND, RegimeState.BEAR_TREND)
    return tuple(
        RegimeOutcomeLabel(
            event_at,
            RegimeDimension.TREND,
            state,
            event_at + timedelta(days=1),
            f"fixture://cycle204/label/{event_at.date()}",
        )
        for event_at, state in zip(TIMES, states, strict=True)
    )


def run_fixture(*, health: str = "HEALTHY", rows: tuple[FeatureMaterialization, ...] | None = None):
    definitions = feature_definitions()
    authority = FixtureFeatureAuthority(
        definitions, rows if rows is not None else materializations(definitions)
    )
    return RegimeResearchOrchestratorV2(authority).run(
        definition(definitions),
        dataset_version_id=DATASET_ID,
        dataset_version="cycle204-v1",
        dataset_content_hash=DATASET_HASH,
        instrument_id="US:XNYS:CYCLE204",
        decision_times=TIMES,
        labels=labels(),
        evaluated_at=datetime(2025, 1, 20, tzinfo=UTC),
        data_health_status=health,
        data_health_assessment_ids=(HEALTH_ID,),
    )


class RegimeEngineV2Tests(unittest.TestCase):
    def test_multidimensional_probabilities_methods_and_oos_evidence_are_bound(self) -> None:
        run = run_fixture()
        self.assertEqual(run.status, ReviewStatus.REVIEW_REQUIRED)
        self.assertEqual(run, run_fixture())
        latest_rule = tuple(
            item
            for item in run.observations
            if item.event_at == TIMES[-1] and item.method is RegimeMethod.RULE_BASED
        )
        self.assertEqual({item.dimension for item in latest_rule}, set(RegimeDimension))
        for item in latest_rule:
            self.assertEqual(
                {name for name, _ in item.probabilities}, set(DIMENSION_STATES[item.dimension])
            )
            self.assertEqual(
                sum((value for _, value in item.probabilities), Decimal("0")), Decimal("1")
            )
        evaluations = {
            item.method: item for item in run.evaluations if item.dimension is RegimeDimension.TREND
        }
        self.assertEqual(evaluations[RegimeMethod.RULE_BASED].sample_count, 3)
        self.assertEqual(evaluations[RegimeMethod.PROBABILISTIC_ENSEMBLE].sample_count, 3)
        self.assertNotEqual(
            evaluations[RegimeMethod.RULE_BASED].brier_score,
            evaluations[RegimeMethod.PROBABILISTIC_ENSEMBLE].brier_score,
        )

    def test_prefix_is_invariant_and_missing_values_remain_unavailable(self) -> None:
        complete = run_fixture()
        definitions = feature_definitions()
        prefix_rows = tuple(
            item for item in materializations(definitions) if item.event_at != TIMES[-1]
        )
        prefix = RegimeResearchOrchestratorV2(
            FixtureFeatureAuthority(definitions, prefix_rows)
        ).run(
            definition(definitions),
            dataset_version_id=DATASET_ID,
            dataset_version="cycle204-v1",
            dataset_content_hash=DATASET_HASH,
            instrument_id="US:XNYS:CYCLE204",
            decision_times=TIMES[:2],
            labels=labels()[:2],
            evaluated_at=datetime(2025, 1, 20, tzinfo=UTC),
            data_health_status="HEALTHY",
            data_health_assessment_ids=(HEALTH_ID,),
        )
        complete_prefix = tuple(
            item for item in complete.observations if item.event_at in TIMES[:2]
        )
        self.assertEqual(prefix.observations, complete_prefix)
        missing = tuple(
            item
            for item in materializations(definitions)
            if not (item.event_at == TIMES[-1] and item.feature_id == definitions[0].feature_id)
        )
        blocked = run_fixture(rows=missing)
        self.assertEqual(blocked.status, ReviewStatus.BLOCKED)
        unavailable = tuple(
            item
            for item in blocked.observations
            if item.event_at == TIMES[-1] and item.dimension is RegimeDimension.TREND
        )
        self.assertTrue(
            all(item.evidence_state is EvidenceState.UNAVAILABLE for item in unavailable)
        )
        self.assertTrue(all(not item.probabilities for item in unavailable))

    def test_version_future_and_data_health_violations_fail_closed(self) -> None:
        definitions = feature_definitions()
        wrong = replace(definitions[0], semantic_version="2.0.0")
        with self.assertRaisesRegex(RegimeEngineV2Error, "feature_version_mismatch"):
            RegimeResearchOrchestratorV2(
                FixtureFeatureAuthority((wrong, *definitions[1:]), materializations(definitions))
            ).run(
                definition(definitions),
                dataset_version_id=DATASET_ID,
                dataset_version="cycle204-v1",
                dataset_content_hash=DATASET_HASH,
                instrument_id="US:XNYS:CYCLE204",
                decision_times=TIMES,
                labels=labels(),
                evaluated_at=datetime(2025, 1, 20, tzinfo=UTC),
                data_health_status="HEALTHY",
                data_health_assessment_ids=(HEALTH_ID,),
            )
        rows = materializations(definitions)
        future = replace(
            rows[0],
            knowledge_at=rows[0].event_at + timedelta(hours=1),
            computed_at=rows[0].event_at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(RegimeEngineV2Error, "future_or_mismatched"):
            run_fixture(rows=(future, *rows[1:]))
        unhealthy = run_fixture(health="BLOCKED")
        self.assertEqual(unhealthy.status, ReviewStatus.BLOCKED)
        self.assertIn("data_health_blocked", unhealthy.reasons)

    def test_risk_adjustments_are_review_candidates_and_increases_are_blocked(self) -> None:
        run = run_fixture()
        strategy_version_id = uuid5(NAMESPACE_URL, "cycle204:strategy-version")
        reduction = build_regime_risk_candidate(
            run,
            strategy_version_id=strategy_version_id,
            current_risk_multiplier=Decimal("1"),
            proposed_risk_multiplier=Decimal("0.5"),
            preapproved_maximum=Decimal("1"),
            created_at=datetime(2025, 1, 20, tzinfo=UTC),
        )
        self.assertEqual(reduction.action, RiskAdjustmentAction.REDUCE)
        self.assertEqual(reduction.status, ReviewStatus.REVIEW_REQUIRED)
        self.assertFalse(reduction.automatic_authority)
        increase = build_regime_risk_candidate(
            run,
            strategy_version_id=strategy_version_id,
            current_risk_multiplier=Decimal("0.5"),
            proposed_risk_multiplier=Decimal("0.75"),
            preapproved_maximum=Decimal("1"),
            created_at=datetime(2025, 1, 20, tzinfo=UTC),
        )
        self.assertEqual(increase.status, ReviewStatus.BLOCKED)
        self.assertIn("automatic_risk_increase_prohibited", increase.reasons)


if __name__ == "__main__":
    unittest.main()
