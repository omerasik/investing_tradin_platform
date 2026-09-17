from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from trade_platform import open_to_open_validation_orchestration_v1 as orchestration
from trade_platform.crypto_basis_mean_reversion_v1 import (
    BasisMeanReversionOutcomeV1,
    CryptoBasisMeanReversionDefinitionV1,
    CryptoBasisMeanReversionV1Error,
)
from trade_platform.crypto_instruments import CryptoInstrumentKind
from trade_platform.crypto_liquidity_capacity_v1 import (
    REASON_MISSING_CANONICAL_QUOTE_TURNOVER,
    AuthorizedInstrumentLiquidityContractV1,
    CryptoLiquidityCapacityV1Error,
    LiquidityCapacityPolicyV1,
)
from trade_platform.feature_authority import (
    FeatureMaterializationV2,
    FeatureQualityStatus,
    FeatureSubjectType,
)
from trade_platform.open_to_open_validation_orchestration_v1 import (
    CANONICAL_FEATURE_VERSIONS,
    CANONICAL_STRATEGY_ID,
    DIMENSION_DAILY_REALIZED_EXIT,
    DIMENSION_PRE_HOLDOUT_OOS,
    DIMENSION_TRADE_LEVEL,
    DIMENSION_UNTOUCHED_HOLDOUT,
    PBO_NOT_DIVISIBLE_REASON,
    REQUIRED_SCORECARD_LIMITATIONS,
    OpenToOpenNeighborStepsV1,
    OpenToOpenProfessionalValidationRequestV1,
    OpenToOpenSegmentAdmissionV1,
    OpenToOpenSegmentKindV1,
    OpenToOpenValidationOrchestrationV1Error,
    OpenToOpenWalkForwardProtocolV1,
    admit_open_to_open_segment_decisions_v1,
    build_equal_pre_holdout_blocks_v1,
    build_open_to_open_multiple_testing_summary_v1,
    build_open_to_open_neighbor_definitions_v1,
    canonical_feature_decision_at,
    definition_parameter_tuple,
    derive_open_to_open_evaluation_span_v1,
    evaluate_open_to_open_pbo_orchestration_v1,
    run_open_to_open_professional_validation_v1,
    run_open_to_open_segment_research_v1,
    slice_authoritative_bar_series_v1,
)
from trade_platform.open_to_open_validation_v1 import (
    CSCV_BLOCKS,
    STATUS_AVAILABLE,
    STATUS_BLOCKED,
    STATUS_RECONCILED,
    STATUS_UNAVAILABLE,
    RealizedExitDailyReturnSeriesV1,
    ResearchTrialDispositionV1,
    ResearchTrialRoleV1,
    build_realized_exit_daily_return_series_v1,
)
from trade_platform.quant_validation import (
    REQUIRED_EVIDENCE,
    evaluate_bootstrap,
    evaluate_monte_carlo_trade_sequence,
)
from trade_platform.research import CostModel
from trade_platform.strategy_feature_binding_v2 import (
    AuthoritativeFeatureSeriesV2,
    ResearchFeatureRequirementV2,
    ResearchQualityPolicyV2,
    SubjectAwareResearchFeatureBundle,
)
from trade_platform.strategy_scorecard_v2 import EvidenceState, MetricFamily, ScorecardStatus
from trade_platform.tradable_bar_evidence_v2 import (
    AuthoritativeTradableBarSeriesV2,
    AuthoritativeTradableBarV2,
)
from trade_platform.tradable_research_evidence_v2 import SubjectAwareTradableResearchEvidenceV2

START = datetime(2026, 1, 1, tzinfo=UTC)
INSTRUMENT = "TESTFIXTURE:3J2B2B2:BTCUSDT:PERP"
DATASET_ID = uuid5(NAMESPACE_URL, "fixture-3j2b2b2-dataset-version")
VALIDATION_DATASET_ID = uuid5(NAMESPACE_URL, "fixture-3j2b2b2-validation-dataset")
BASIS_FEATURE_ID = uuid5(NAMESPACE_URL, "fixture-3j2b2b2-basis-feature")
FIXTURE_SOURCE_ID = uuid5(NAMESPACE_URL, "fixture-3j2b2b2-source")

SPAN_DAYS = 150
SHORT_HOLDOUT_SPAN_DAYS = 140
ACTIVE_DAY_STEP = 6
BARS_PER_ACTIVE_DAY = 5
COST_MODEL = CostModel(percentage_per_turnover=Decimal("0.0001"))
COST_MODEL_VERSION = "cost-model-fixture-v1"

BASIS_CYCLE = (
    Decimal("0.0020"),
    Decimal("-0.0030"),
    Decimal("0.0012"),
    Decimal("-0.0008"),
    Decimal("0.0025"),
    Decimal("-0.0020"),
    Decimal("0.0030"),
    Decimal("-0.0012"),
    Decimal("0.0008"),
    Decimal("-0.0025"),
)

BASELINE = CryptoBasisMeanReversionDefinitionV1(
    basis_entry_threshold=Decimal("0.0010"),
    holding_horizon_bars=2,
    maximum_absolute_exposure=Decimal("0.5"),
)
NEIGHBOR_STEPS = OpenToOpenNeighborStepsV1(
    basis_threshold_step=Decimal("0.0005"),
    holding_horizon_step_bars=1,
    exposure_step=Decimal("0.25"),
)
PROTOCOL = OpenToOpenWalkForwardProtocolV1(
    initial_train_days=30, validation_days=10, test_days=10, step_days=10
)


def _bar(bar_open_at: datetime, open_price: Decimal) -> AuthoritativeTradableBarV2:
    seed = bar_open_at.isoformat()
    return AuthoritativeTradableBarV2(
        dataset_version_id=DATASET_ID,
        dataset_content_hash="e" * 64,
        source_id=FIXTURE_SOURCE_ID,
        normalized_observation_id=uuid5(NAMESPACE_URL, f"fixture-normalized:{seed}"),
        raw_observation_id=uuid5(NAMESPACE_URL, f"fixture-raw:{seed}"),
        raw_payload_sha256="f" * 64,
        instrument_id=INSTRUMENT,
        interval="1m",
        bar_open_at=bar_open_at,
        bar_close_at=bar_open_at + timedelta(minutes=1),
        normalized_at=bar_open_at + timedelta(minutes=2),
        revision=0,
        open=open_price,
        high=open_price + Decimal("1"),
        low=open_price - Decimal("1"),
        close=open_price + Decimal("0.5"),
        volume=Decimal("10"),
        provenance_uri="fixture://bar",
    )


def _materialization(
    *, event_at: datetime, value: Decimal, computed_at: datetime | None = None
) -> FeatureMaterializationV2:
    computed = event_at if computed_at is None else computed_at
    created = FeatureMaterializationV2.create(
        feature_id=BASIS_FEATURE_ID,
        subject_type=FeatureSubjectType.INSTRUMENT,
        subject_id=INSTRUMENT,
        dataset_version=str(DATASET_ID),
        event_at=event_at,
        effective_at=event_at,
        knowledge_at=event_at,
        computed_at=computed,
        source_observation_manifest=("fixture:manifest",),
        value=value,
        quality_status=FeatureQualityStatus.VALIDATED,
    )
    return replace(
        created,
        materialization_id=uuid5(
            NAMESPACE_URL, f"fixture-materialization:{event_at.isoformat()}:{computed.isoformat()}"
        ),
    )


def _bundle(
    materializations: tuple[FeatureMaterializationV2, ...], *, decision_at: datetime
) -> SubjectAwareResearchFeatureBundle:
    requirement = ResearchFeatureRequirementV2(
        feature_id=BASIS_FEATURE_ID,
        name="crypto_mark_index_basis",
        semantic_version="1.0.0",
        expected_subject_type=FeatureSubjectType.INSTRUMENT,
    )
    series = AuthoritativeFeatureSeriesV2(
        requirement=requirement,
        subject_type=FeatureSubjectType.INSTRUMENT,
        subject_id=INSTRUMENT,
        dataset_version=str(DATASET_ID),
        materializations=materializations,
    )
    return SubjectAwareResearchFeatureBundle.create(
        dataset_version_id=DATASET_ID,
        subject_type=FeatureSubjectType.INSTRUMENT,
        subject_id=INSTRUMENT,
        decision_at=decision_at,
        quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY,
        feature_series=(series,),
    )


def _bar_series(bars: tuple[AuthoritativeTradableBarV2, ...]) -> AuthoritativeTradableBarSeriesV2:
    series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", bars)
    series.validate()
    return series


def _active_days(span_days: int) -> tuple[int, ...]:
    return tuple(range(0, span_days - 2, ACTIVE_DAY_STEP))


def _day_open(index: int, minute: int) -> Decimal:
    base = Decimal("100") + Decimal(index % 13)
    tick = Decimal("0.01") * Decimal(1 + (index % 7))
    if index % 3 == 0:
        tick = -tick
    return base + Decimal(minute) * tick


def _fixture_evidence(
    *,
    span_days: int = SPAN_DAYS,
    value_overrides: dict[int, Decimal] | None = None,
    bar_overrides: dict[datetime, AuthoritativeTradableBarV2] | None = None,
) -> SubjectAwareTradableResearchEvidenceV2:
    overrides = {} if value_overrides is None else value_overrides
    bar_overrides = {} if bar_overrides is None else bar_overrides
    bars: list[AuthoritativeTradableBarV2] = []
    materializations: list[FeatureMaterializationV2] = []
    for index, day in enumerate(_active_days(span_days)):
        day_start = START + timedelta(days=day)
        for minute in range(BARS_PER_ACTIVE_DAY):
            bar_open_at = day_start + timedelta(minutes=minute)
            bars.append(bar_overrides.get(bar_open_at, _bar(bar_open_at, _day_open(index, minute))))
        materializations.append(
            _materialization(
                event_at=day_start,
                value=overrides.get(day, BASIS_CYCLE[index % len(BASIS_CYCLE)]),
            )
        )
    tail_open_at = START + timedelta(days=span_days - 1, hours=23, minutes=59)
    bars.append(bar_overrides.get(tail_open_at, _bar(tail_open_at, Decimal("100"))))
    bundle = _bundle(tuple(materializations), decision_at=START + timedelta(days=span_days))
    return SubjectAwareTradableResearchEvidenceV2.create(
        feature_bundle=bundle, bar_series=_bar_series(tuple(bars))
    )


def _mutated_bar(bar_open_at: datetime, base_open: Decimal) -> AuthoritativeTradableBarV2:
    base = _bar(bar_open_at, base_open)
    return replace(
        base,
        open=base.open + Decimal("50"),
        high=base.high + Decimal("50"),
        low=base.low + Decimal("50"),
        close=base.close + Decimal("50"),
        raw_payload_sha256="a" * 64,
        raw_observation_id=uuid5(NAMESPACE_URL, f"mutated-raw:{bar_open_at.isoformat()}"),
        normalized_observation_id=uuid5(
            NAMESPACE_URL, f"mutated-normalized:{bar_open_at.isoformat()}"
        ),
    )


def _fixture_evidence_with_unused_future_evidence(
    span_days: int = SPAN_DAYS,
) -> SubjectAwareTradableResearchEvidenceV2:
    bars: list[AuthoritativeTradableBarV2] = []
    materializations: list[FeatureMaterializationV2] = []
    for index, day in enumerate(_active_days(span_days)):
        day_start = START + timedelta(days=day)
        for minute in range(BARS_PER_ACTIVE_DAY):
            bars.append(_bar(day_start + timedelta(minutes=minute), _day_open(index, minute)))
        materializations.append(
            _materialization(event_at=day_start, value=BASIS_CYCLE[index % len(BASIS_CYCLE)])
        )
    bars.append(_bar(START + timedelta(days=span_days - 1, hours=23, minutes=59), Decimal("100")))
    future_bar_open_at = START + timedelta(days=span_days, minutes=5)
    bars.append(_bar(future_bar_open_at, Decimal("100")))
    materializations.append(_materialization(event_at=future_bar_open_at, value=Decimal("0.0050")))
    bundle = _bundle(
        tuple(materializations), decision_at=START + timedelta(days=span_days, minutes=10)
    )
    return SubjectAwareTradableResearchEvidenceV2.create(
        feature_bundle=bundle, bar_series=_bar_series(tuple(bars))
    )


def _request(
    evidence: SubjectAwareTradableResearchEvidenceV2,
    *,
    span_days: int = SPAN_DAYS,
    protocol: OpenToOpenWalkForwardProtocolV1 = PROTOCOL,
    baseline: CryptoBasisMeanReversionDefinitionV1 = BASELINE,
    neighbor_steps: OpenToOpenNeighborStepsV1 = NEIGHBOR_STEPS,
) -> OpenToOpenProfessionalValidationRequestV1:
    return OpenToOpenProfessionalValidationRequestV1(
        evidence=evidence,
        baseline_definition=baseline,
        instrument_kind=CryptoInstrumentKind.PERPETUAL,
        cost_model=COST_MODEL,
        cost_model_version=COST_MODEL_VERSION,
        protocol=protocol,
        neighbor_steps=neighbor_steps,
        bootstrap_seed=11,
        bootstrap_resamples=8,
        monte_carlo_seed=13,
        monte_carlo_simulations=8,
        null_seed=17,
        adverse_exit_shock_magnitudes=(Decimal("0.001"), Decimal("0.005")),
        missing_exit_stress_bar_open_times=(START + timedelta(minutes=3),),
        validation_dataset_id=VALIDATION_DATASET_ID,
        evaluated_at=START + timedelta(days=span_days + 1),
    )


def _span_only_evidence(span_days: int) -> AuthoritativeTradableBarSeriesV2:
    return _bar_series(
        (
            _bar(START, Decimal("100")),
            _bar(START + timedelta(days=span_days - 1, hours=23, minutes=59), Decimal("100")),
        )
    )


def _pre_holdout_baseline_run(
    evidence: SubjectAwareTradableResearchEvidenceV2,
    span: orchestration.OpenToOpenEvaluationSpanV1,
    definition: CryptoBasisMeanReversionDefinitionV1 = BASELINE,
):
    admission = admit_open_to_open_segment_decisions_v1(
        evidence=evidence,
        definition=definition,
        segment_kind=OpenToOpenSegmentKindV1.FULL_PRE_HOLDOUT,
        segment_start=span.evaluation_start,
        segment_end=span.holdout_start,
    )
    return run_open_to_open_segment_research_v1(
        evidence=evidence,
        definition=definition,
        instrument_kind=CryptoInstrumentKind.PERPETUAL,
        cost_model=COST_MODEL,
        cost_model_version=COST_MODEL_VERSION,
        admission=admission,
    )


def _compound(returns: tuple[Decimal, ...]) -> Decimal:
    equity = Decimal("1")
    for value in returns:
        equity *= Decimal("1") + value
    return equity - Decimal("1")


def _pre_holdout_daily_series(
    evidence: SubjectAwareTradableResearchEvidenceV2,
    span: orchestration.OpenToOpenEvaluationSpanV1,
    definition: CryptoBasisMeanReversionDefinitionV1,
) -> RealizedExitDailyReturnSeriesV1:
    return build_realized_exit_daily_return_series_v1(
        run=_pre_holdout_baseline_run(evidence, span, definition),
        window_start=span.evaluation_start,
        window_end=span.holdout_start,
    )


def _aggregated_validation_daily_returns(
    evidence: SubjectAwareTradableResearchEvidenceV2,
    result: orchestration.OpenToOpenProfessionalValidationRunV1,
    definition: CryptoBasisMeanReversionDefinitionV1 = BASELINE,
) -> tuple[Decimal, ...]:
    aggregated: list[Decimal] = []
    for fold in result.walk_forward_evidence.folds:
        admission = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=definition,
            segment_kind=OpenToOpenSegmentKindV1.VALIDATION,
            segment_start=fold.validation_start,
            segment_end=fold.validation_end,
        )
        run = run_open_to_open_segment_research_v1(
            evidence=evidence,
            definition=definition,
            instrument_kind=CryptoInstrumentKind.PERPETUAL,
            cost_model=COST_MODEL,
            cost_model_version=COST_MODEL_VERSION,
            admission=admission,
        )
        series = build_realized_exit_daily_return_series_v1(
            run=run, window_start=fold.validation_start, window_end=fold.validation_end
        )
        aggregated.extend(series.daily_returns)
    return tuple(aggregated)


def _holdout_baseline_run(
    evidence: SubjectAwareTradableResearchEvidenceV2,
    span: orchestration.OpenToOpenEvaluationSpanV1,
    definition: CryptoBasisMeanReversionDefinitionV1 = BASELINE,
):
    admission = admit_open_to_open_segment_decisions_v1(
        evidence=evidence,
        definition=definition,
        segment_kind=OpenToOpenSegmentKindV1.UNTOUCHED_HOLDOUT,
        segment_start=span.holdout_start,
        segment_end=span.evaluation_end,
    )
    return run_open_to_open_segment_research_v1(
        evidence=evidence,
        definition=definition,
        instrument_kind=CryptoInstrumentKind.PERPETUAL,
        cost_model=COST_MODEL,
        cost_model_version=COST_MODEL_VERSION,
        admission=admission,
    )


class EvaluationSpanDerivationTests(unittest.TestCase):
    def test_exact_eighty_twenty_holdout_derivation(self) -> None:
        span = derive_open_to_open_evaluation_span_v1(bar_series=_span_only_evidence(SPAN_DAYS))
        self.assertEqual(span.evaluation_start, START)
        self.assertEqual(span.evaluation_end, START + timedelta(days=150))
        self.assertEqual(span.holdout_start, START + timedelta(days=120))
        self.assertEqual(span.pre_holdout_complete_days, 120)
        self.assertEqual(span.holdout_complete_days, 30)
        self.assertEqual(span.holdout_status, STATUS_AVAILABLE)

    def test_exact_midnight_raw_holdout_start_is_unchanged(self) -> None:
        span = derive_open_to_open_evaluation_span_v1(bar_series=_span_only_evidence(SPAN_DAYS))
        self.assertEqual(span.raw_holdout_start, START + timedelta(days=120))
        self.assertEqual(span.holdout_start, span.raw_holdout_start)

    def test_raw_holdout_start_is_ceiled_to_utc_midnight(self) -> None:
        span = derive_open_to_open_evaluation_span_v1(bar_series=_span_only_evidence(151))
        self.assertEqual(span.raw_holdout_start, START + timedelta(days=120, hours=19, minutes=12))
        self.assertEqual(span.holdout_start, START + timedelta(days=121))
        self.assertEqual(span.pre_holdout_complete_days, 121)
        self.assertEqual(span.holdout_complete_days, 30)
        self.assertEqual(span.holdout_status, STATUS_AVAILABLE)

    def test_evaluation_start_ceils_and_evaluation_end_floors_to_midnight(self) -> None:
        bars = _bar_series(
            (
                _bar(START + timedelta(minutes=1), Decimal("100")),
                _bar(START + timedelta(days=150, hours=6), Decimal("100")),
            )
        )
        span = derive_open_to_open_evaluation_span_v1(bar_series=bars)
        self.assertEqual(span.evaluation_start, START + timedelta(days=1))
        self.assertEqual(span.evaluation_end, START + timedelta(days=150))

    def test_insufficient_pre_holdout_days_makes_holdout_unavailable(self) -> None:
        span = derive_open_to_open_evaluation_span_v1(bar_series=_span_only_evidence(100))
        self.assertEqual(span.pre_holdout_complete_days, 80)
        self.assertEqual(span.holdout_status, STATUS_UNAVAILABLE)
        self.assertIn("insufficient_pre_holdout_complete_days", span.holdout_unavailable_reasons)

    def test_insufficient_holdout_days_makes_holdout_unavailable(self) -> None:
        span = derive_open_to_open_evaluation_span_v1(
            bar_series=_span_only_evidence(SHORT_HOLDOUT_SPAN_DAYS)
        )
        self.assertEqual(span.pre_holdout_complete_days, 112)
        self.assertEqual(span.holdout_complete_days, 28)
        self.assertEqual(span.holdout_status, STATUS_UNAVAILABLE)
        self.assertEqual(
            span.holdout_unavailable_reasons, ("insufficient_untouched_holdout_complete_days",)
        )

    def test_twenty_percent_rule_is_never_relaxed_to_make_holdout_available(self) -> None:
        span = derive_open_to_open_evaluation_span_v1(
            bar_series=_span_only_evidence(SHORT_HOLDOUT_SPAN_DAYS)
        )
        elapsed = span.evaluation_end - span.evaluation_start
        self.assertEqual(span.raw_holdout_start, span.evaluation_start + elapsed * 8 // 10)


class WalkForwardProtocolTests(unittest.TestCase):
    def test_expanding_utc_walk_forward_geometry(self) -> None:
        folds = PROTOCOL.folds(
            evaluation_start=START, holdout_start=START + timedelta(days=120)
        )
        self.assertEqual(len(folds), 8)
        for fold in folds:
            self.assertEqual(fold.train_start, START)
            for boundary in fold.boundaries:
                self.assertEqual(boundary.utcoffset(), timedelta(0))
                self.assertEqual(
                    (boundary.hour, boundary.minute, boundary.second, boundary.microsecond),
                    (0, 0, 0, 0),
                )
        self.assertEqual(folds[0].train_end, START + timedelta(days=30))
        self.assertEqual(folds[0].validation_end, START + timedelta(days=40))
        self.assertEqual(folds[0].test_end, START + timedelta(days=50))
        self.assertEqual(folds[7].train_end, START + timedelta(days=100))
        self.assertEqual(folds[7].test_end, START + timedelta(days=120))

    def test_training_window_expands_while_start_stays_fixed(self) -> None:
        folds = PROTOCOL.folds(evaluation_start=START, holdout_start=START + timedelta(days=120))
        widths = [fold.train_end - fold.train_start for fold in folds]
        self.assertEqual(widths, sorted(widths))
        self.assertEqual(len(set(widths)), len(widths))

    def test_test_windows_never_overlap(self) -> None:
        folds = PROTOCOL.folds(evaluation_start=START, holdout_start=START + timedelta(days=120))
        for earlier, later in pairwise(folds):
            self.assertLessEqual(earlier.test_end, later.test_start)

    def test_no_fold_test_window_reaches_into_the_holdout(self) -> None:
        holdout_start = START + timedelta(days=120)
        folds = PROTOCOL.folds(evaluation_start=START, holdout_start=holdout_start)
        for fold in folds:
            self.assertLessEqual(fold.test_end, holdout_start)

    def test_insufficient_history_rejects_protocol(self) -> None:
        protocol = OpenToOpenWalkForwardProtocolV1(
            initial_train_days=200, validation_days=10, test_days=10, step_days=10
        )
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error) as error:
            protocol.folds(evaluation_start=START, holdout_start=START + timedelta(days=120))
        self.assertEqual(
            str(error.exception), "walk_forward_requires_at_least_one_complete_fold"
        )

    def test_step_smaller_than_test_is_rejected(self) -> None:
        protocol = OpenToOpenWalkForwardProtocolV1(
            initial_train_days=30, validation_days=10, test_days=10, step_days=5
        )
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error) as error:
            protocol.validate()
        self.assertEqual(
            str(error.exception), "step_days_must_not_be_smaller_than_test_days"
        )

    def test_non_positive_day_sizes_are_rejected(self) -> None:
        for field_name in ("initial_train_days", "validation_days", "test_days", "step_days"):
            protocol = replace(PROTOCOL, **{field_name: 0})
            with self.assertRaises(OpenToOpenValidationOrchestrationV1Error) as error:
                protocol.validate()
            self.assertIn("must_be_positive", str(error.exception))


class SegmentAdmissionTests(unittest.TestCase):
    def _two_day_evidence(
        self, *, decision_minute: int, computed_minute: int | None = None, value: Decimal
    ) -> SubjectAwareTradableResearchEvidenceV2:
        bars = tuple(
            _bar(START + timedelta(minutes=minute), Decimal("100") + Decimal(minute))
            for minute in range(1435, 1446)
        )
        decision_at = START + timedelta(minutes=decision_minute)
        computed_at = (
            None if computed_minute is None else START + timedelta(minutes=computed_minute)
        )
        materialization = _materialization(
            event_at=decision_at, value=value, computed_at=computed_at
        )
        bundle = _bundle((materialization,), decision_at=START + timedelta(days=2))
        return SubjectAwareTradableResearchEvidenceV2.create(
            feature_bundle=bundle, bar_series=_bar_series(bars)
        )

    def test_decision_assignment_uses_canonical_max_of_four_timestamps(self) -> None:
        evidence = self._two_day_evidence(
            decision_minute=1435, computed_minute=1437, value=Decimal("0.0020")
        )
        materialization = evidence.feature_bundle.feature_series[0].materializations[0]
        self.assertEqual(
            canonical_feature_decision_at(materialization), START + timedelta(minutes=1437)
        )
        admission = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=BASELINE,
            segment_kind=OpenToOpenSegmentKindV1.TEST,
            segment_start=START,
            segment_end=START + timedelta(days=2),
        )
        self.assertEqual(admission.admitted_decision_at, (START + timedelta(minutes=1437),))
        run = run_open_to_open_segment_research_v1(
            evidence=evidence,
            definition=BASELINE,
            instrument_kind=CryptoInstrumentKind.PERPETUAL,
            cost_model=COST_MODEL,
            cost_model_version=COST_MODEL_VERSION,
            admission=admission,
        )
        self.assertEqual(run.executed_trade_count, 1)
        self.assertEqual(run.executed_trades[0].entry_time, START + timedelta(minutes=1438))
        self.assertNotEqual(run.executed_trades[0].entry_time, START + timedelta(minutes=1436))

    def test_fold_boundary_trade_is_purged_and_never_becomes_a_missing_exit(self) -> None:
        evidence = self._two_day_evidence(decision_minute=1438, value=Decimal("0.0020"))
        admission = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=BASELINE,
            segment_kind=OpenToOpenSegmentKindV1.TEST,
            segment_start=START,
            segment_end=START + timedelta(days=1),
        )
        self.assertEqual(admission.purged_decision_count, 1)
        self.assertEqual(admission.admitted_decision_at, ())
        wider = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=BASELINE,
            segment_kind=OpenToOpenSegmentKindV1.TEST,
            segment_start=START,
            segment_end=START + timedelta(days=2),
        )
        self.assertEqual(wider.purged_decision_count, 0)
        run = run_open_to_open_segment_research_v1(
            evidence=evidence,
            definition=BASELINE,
            instrument_kind=CryptoInstrumentKind.PERPETUAL,
            cost_model=COST_MODEL,
            cost_model_version=COST_MODEL_VERSION,
            admission=wider,
        )
        self.assertEqual(
            [decision.outcome for decision in run.decisions],
            [BasisMeanReversionOutcomeV1.EXECUTED],
        )

    def test_purge_width_follows_each_definition_holding_horizon(self) -> None:
        evidence = self._two_day_evidence(decision_minute=1437, value=Decimal("0.0020"))
        short = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=replace(BASELINE, holding_horizon_bars=1),
            segment_kind=OpenToOpenSegmentKindV1.TEST,
            segment_start=START,
            segment_end=START + timedelta(days=1),
        )
        long = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=replace(BASELINE, holding_horizon_bars=3),
            segment_kind=OpenToOpenSegmentKindV1.TEST,
            segment_start=START,
            segment_end=START + timedelta(days=1),
        )
        self.assertEqual(short.purge_bars, 1)
        self.assertEqual(short.purged_decision_count, 0)
        self.assertEqual(long.purge_bars, 3)
        self.assertEqual(long.purged_decision_count, 1)

    def test_flat_decision_is_never_purged(self) -> None:
        evidence = self._two_day_evidence(decision_minute=1438, value=Decimal("0.0005"))
        admission = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=BASELINE,
            segment_kind=OpenToOpenSegmentKindV1.TEST,
            segment_start=START,
            segment_end=START + timedelta(days=1),
        )
        self.assertEqual(admission.purged_decision_count, 0)
        self.assertEqual(admission.admitted_decision_at, (START + timedelta(minutes=1438),))

    def test_executed_trade_can_never_cross_a_segment_boundary(self) -> None:
        evidence = self._two_day_evidence(decision_minute=1438, value=Decimal("0.0020"))
        forced = OpenToOpenSegmentAdmissionV1(
            segment_kind=OpenToOpenSegmentKindV1.TEST,
            segment_start=START,
            segment_end=START + timedelta(days=1),
            purge_bars=BASELINE.holding_horizon_bars,
            embargo_bars=BASELINE.holding_horizon_bars,
            admitted_decision_at=(START + timedelta(minutes=1438),),
            purged_decision_count=0,
            embargo_excluded_decision_count=0,
        )
        run = run_open_to_open_segment_research_v1(
            evidence=evidence,
            definition=BASELINE,
            instrument_kind=CryptoInstrumentKind.PERPETUAL,
            cost_model=COST_MODEL,
            cost_model_version=COST_MODEL_VERSION,
            admission=forced,
        )
        self.assertEqual(run.executed_trade_count, 0)
        self.assertEqual(
            [decision.outcome for decision in run.decisions],
            [BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_EXIT],
        )
        for trade in run.executed_trades:
            self.assertGreaterEqual(trade.entry_time, forced.segment_start)
            self.assertLess(trade.exit_time, forced.segment_end)

    def test_segment_bar_slicing_excludes_bars_outside_the_window(self) -> None:
        evidence = self._two_day_evidence(decision_minute=1438, value=Decimal("0.0020"))
        sliced = slice_authoritative_bar_series_v1(
            evidence.bar_series,
            window_start=START,
            window_end=START + timedelta(days=1),
        )
        self.assertTrue(all(bar.bar_open_at < START + timedelta(days=1) for bar in sliced.bars))
        self.assertTrue(all(bar.bar_close_at <= START + timedelta(days=1) for bar in sliced.bars))
        self.assertLess(len(sliced.bars), len(evidence.bar_series.bars))

    def test_embargo_excludes_decisions_at_the_test_window_start(self) -> None:
        evidence = _fixture_evidence()
        test_start = START + timedelta(days=60)
        embargo = BASELINE.holding_horizon_bars * timedelta(minutes=1)
        embargoed = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=BASELINE,
            segment_kind=OpenToOpenSegmentKindV1.TEST,
            segment_start=test_start,
            segment_end=test_start + timedelta(days=10),
            embargo_intervals=((test_start, test_start + embargo),),
        )
        unembargoed = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=BASELINE,
            segment_kind=OpenToOpenSegmentKindV1.TEST,
            segment_start=test_start,
            segment_end=test_start + timedelta(days=10),
        )
        self.assertEqual(embargoed.embargo_excluded_decision_count, 1)
        self.assertEqual(embargoed.embargo_bars, BASELINE.holding_horizon_bars)
        self.assertNotIn(test_start, embargoed.admitted_decision_at)
        self.assertIn(test_start, unembargoed.admitted_decision_at)
        self.assertEqual(
            len(unembargoed.admitted_decision_at) - len(embargoed.admitted_decision_at), 1
        )

    def test_prior_test_post_embargo_is_excluded_from_a_later_expanding_train(self) -> None:
        evidence = _fixture_evidence()
        embargo = BASELINE.holding_horizon_bars * timedelta(minutes=1)
        prior_test_end = START + timedelta(days=60)
        train = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=BASELINE,
            segment_kind=OpenToOpenSegmentKindV1.TRAIN,
            segment_start=START,
            segment_end=START + timedelta(days=70),
            embargo_intervals=((prior_test_end, prior_test_end + embargo),),
        )
        self.assertEqual(train.embargo_excluded_decision_count, 1)
        self.assertNotIn(prior_test_end, train.admitted_decision_at)


class NeighborhoodTests(unittest.TestCase):
    def test_baseline_plus_exactly_six_valid_neighbors(self) -> None:
        neighbors = build_open_to_open_neighbor_definitions_v1(
            baseline_definition=BASELINE, neighbor_steps=NEIGHBOR_STEPS
        )
        self.assertEqual(len(neighbors), 6)
        for neighbor in neighbors:
            neighbor.validate()
        self.assertEqual(
            [
                (
                    neighbor.basis_entry_threshold,
                    neighbor.holding_horizon_bars,
                    neighbor.maximum_absolute_exposure,
                )
                for neighbor in neighbors
            ],
            [
                (Decimal("0.0005"), 2, Decimal("0.5")),
                (Decimal("0.0015"), 2, Decimal("0.5")),
                (Decimal("0.0010"), 1, Decimal("0.5")),
                (Decimal("0.0010"), 3, Decimal("0.5")),
                (Decimal("0.0010"), 2, Decimal("0.25")),
                (Decimal("0.0010"), 2, Decimal("0.75")),
            ],
        )

    def test_neighbors_retain_fixed_strategy_identity_constants(self) -> None:
        for neighbor in build_open_to_open_neighbor_definitions_v1(
            baseline_definition=BASELINE, neighbor_steps=NEIGHBOR_STEPS
        ):
            self.assertEqual(neighbor.strategy_name, BASELINE.strategy_name)
            self.assertEqual(neighbor.semantic_version, BASELINE.semantic_version)
            self.assertIs(neighbor.lifecycle, BASELINE.lifecycle)
            self.assertIs(neighbor.required_instrument_kind, BASELINE.required_instrument_kind)
            self.assertEqual(neighbor.required_feature_name, BASELINE.required_feature_name)

    def test_invalid_neighborhood_is_rejected_without_clipping(self) -> None:
        steps = OpenToOpenNeighborStepsV1(
            basis_threshold_step=Decimal("0.0010"),
            holding_horizon_step_bars=1,
            exposure_step=Decimal("0.25"),
        )
        with self.assertRaises(CryptoBasisMeanReversionV1Error):
            build_open_to_open_neighbor_definitions_v1(
                baseline_definition=BASELINE, neighbor_steps=steps
            )

    def test_exposure_neighbor_above_one_is_rejected_without_clipping(self) -> None:
        steps = OpenToOpenNeighborStepsV1(
            basis_threshold_step=Decimal("0.0005"),
            holding_horizon_step_bars=1,
            exposure_step=Decimal("0.75"),
        )
        with self.assertRaises(CryptoBasisMeanReversionV1Error):
            build_open_to_open_neighbor_definitions_v1(
                baseline_definition=BASELINE, neighbor_steps=steps
            )

    def test_non_positive_neighbor_steps_are_rejected(self) -> None:
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            replace(NEIGHBOR_STEPS, basis_threshold_step=Decimal("0")).validate()
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            replace(NEIGHBOR_STEPS, holding_horizon_step_bars=0).validate()
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            replace(NEIGHBOR_STEPS, exposure_step=Decimal("-0.1")).validate()


class EqualBlockConstructionTests(unittest.TestCase):
    def test_eight_equal_contiguous_blocks(self) -> None:
        daily = tuple(Decimal(index) for index in range(120))
        blocks = build_equal_pre_holdout_blocks_v1(daily)
        self.assertIsNotNone(blocks)
        assert blocks is not None
        self.assertEqual(len(blocks), CSCV_BLOCKS)
        self.assertEqual({len(block) for block in blocks}, {15})
        rebuilt: list[Decimal] = []
        for block in blocks:
            rebuilt.extend(block)
        self.assertEqual(tuple(rebuilt), daily)

    def test_non_divisible_observation_count_yields_no_blocks(self) -> None:
        self.assertIsNone(build_equal_pre_holdout_blocks_v1(tuple(Decimal(i) for i in range(121))))
        self.assertIsNone(build_equal_pre_holdout_blocks_v1(()))


class ChronologicalOrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = _fixture_evidence()
        cls.result = run_open_to_open_professional_validation_v1(_request(cls.evidence))

    def test_span_and_fold_geometry(self) -> None:
        self.assertEqual(self.result.span.evaluation_start, START)
        self.assertEqual(self.result.span.evaluation_end, START + timedelta(days=150))
        self.assertEqual(self.result.span.holdout_start, START + timedelta(days=120))
        self.assertEqual(self.result.span.holdout_status, STATUS_AVAILABLE)
        self.assertEqual(self.result.walk_forward_evidence.fold_count, 8)

    def test_walk_forward_test_windows_never_overlap(self) -> None:
        folds = self.result.walk_forward_evidence.folds
        for earlier, later in pairwise(folds):
            self.assertLessEqual(earlier.test_end, later.test_start)
        self.assertEqual(len({fold.test_start for fold in folds}), len(folds))

    def test_fold_evidence_records_purge_and_embargo_accounting(self) -> None:
        folds = self.result.walk_forward_evidence.folds
        for fold in folds:
            self.assertEqual(fold.purge_bars, BASELINE.holding_horizon_bars)
            self.assertEqual(fold.embargo_bars, BASELINE.holding_horizon_bars)
            self.assertEqual(len(fold.test_daily_returns), 10)
            self.assertGreaterEqual(fold.test_executed_trade_count, 1)
        self.assertGreater(
            sum(fold.test_embargo_excluded_decision_count for fold in folds), 0
        )
        self.assertGreater(
            sum(fold.train_embargo_excluded_decision_count for fold in folds), 0
        )

    def test_walk_forward_evidence_exposes_no_binary_pass_threshold(self) -> None:
        fold = self.result.walk_forward_evidence.folds[0]
        self.assertFalse(hasattr(fold, "passed"))
        self.assertFalse(hasattr(self.result.walk_forward_evidence, "passed"))

    def test_trial_ledger_has_exactly_seven_aligned_trials(self) -> None:
        trials = self.result.trial_ledger.trials
        self.assertEqual(len(trials), 7)
        self.assertEqual({trial.window_start for trial in trials}, {START})
        self.assertEqual(
            {trial.window_end for trial in trials}, {START + timedelta(days=120)}
        )
        self.assertEqual({len(trial.daily_returns) for trial in trials}, {120})
        self.assertEqual({trial.dataset_version_id for trial in trials}, {DATASET_ID})
        self.assertEqual({trial.instrument_id for trial in trials}, {INSTRUMENT})
        self.assertEqual(len({trial.trial_content_hash for trial in trials}), 7)

    def test_baseline_is_permanently_selected_and_neighbors_only_inspected(self) -> None:
        trials = self.result.trial_ledger.trials
        baselines = [
            trial for trial in trials if trial.trial_role is ResearchTrialRoleV1.BASELINE
        ]
        neighbors = [
            trial for trial in trials if trial.trial_role is ResearchTrialRoleV1.NEIGHBOR
        ]
        self.assertEqual(len(baselines), 1)
        self.assertEqual(len(neighbors), 6)
        self.assertIs(baselines[0].disposition, ResearchTrialDispositionV1.SELECTED)
        for trial in neighbors:
            self.assertIs(trial.disposition, ResearchTrialDispositionV1.INSPECTED)

    def test_better_performing_neighbor_never_replaces_the_baseline(self) -> None:
        stability = self.result.parameter_stability
        selected = definition_parameter_tuple(BASELINE)
        self.assertEqual(stability.selected_parameters, selected)
        self.assertEqual(len(stability.tested_parameter_grid), 7)
        best = max(stability.tested_parameter_grid, key=lambda item: item.total_return)
        baseline_result = next(
            item for item in stability.tested_parameter_grid if item.parameters == selected
        )
        self.assertGreaterEqual(best.total_return, baseline_result.total_return)
        self.assertEqual(stability.selected_parameters, selected)
        self.assertEqual(
            self.result.baseline_trial.strategy_definition_content_hash, BASELINE.content_hash()
        )
        self.assertIs(
            self.result.baseline_trial.disposition, ResearchTrialDispositionV1.SELECTED
        )

    def test_parameter_stability_consumes_validation_windows_not_the_holdout(self) -> None:
        stability = self.result.parameter_stability
        self.assertEqual(stability.dataset_version, str(DATASET_ID))
        grid_parameters = {item.parameters for item in stability.tested_parameter_grid}
        expected = {definition_parameter_tuple(BASELINE)} | {
            definition_parameter_tuple(definition)
            for definition in self.result.neighbor_definitions
        }
        self.assertEqual(grid_parameters, expected)
        baseline_result = next(
            item
            for item in stability.tested_parameter_grid
            if item.parameters == definition_parameter_tuple(BASELINE)
        )
        aggregated = _aggregated_validation_daily_returns(self.evidence, self.result)
        self.assertEqual(len(aggregated), 80)
        self.assertEqual(baseline_result.total_return, _compound(aggregated))
        holdout_total = _compound(self.result.holdout.daily_returns)
        self.assertTrue(self.result.holdout.daily_returns)
        self.assertNotEqual(baseline_result.total_return, holdout_total)

    def test_trial_series_share_identical_calendar_dates(self) -> None:
        baseline_dates = _pre_holdout_daily_series(self.evidence, self.result.span, BASELINE).dates
        self.assertEqual(len(baseline_dates), 120)
        for neighbor in self.result.neighbor_definitions:
            neighbor_series = _pre_holdout_daily_series(self.evidence, self.result.span, neighbor)
            self.assertEqual(neighbor_series.dates, baseline_dates)
            self.assertEqual(neighbor_series.window_start, self.result.span.evaluation_start)
            self.assertEqual(neighbor_series.window_end, self.result.span.holdout_start)

    def test_canonical_strategy_and_feature_identity_cannot_be_overridden(self) -> None:
        self.assertEqual(
            CANONICAL_STRATEGY_ID,
            uuid5(NAMESPACE_URL, "strategy:crypto_basis_mean_reversion"),
        )
        self.assertEqual(CANONICAL_FEATURE_VERSIONS, ("crypto_mark_index_basis:1.0.0",))
        self.assertEqual(self.result.strategy_id, CANONICAL_STRATEGY_ID)
        self.assertEqual(self.result.strategy_version_id, BASELINE.definition_id)
        self.assertEqual(self.result.feature_versions, CANONICAL_FEATURE_VERSIONS)
        self.assertEqual(self.result.dataset_version_id, DATASET_ID)
        self.assertEqual(self.result.validation_dataset_id, VALIDATION_DATASET_ID)

    def test_null_control_covers_every_trial(self) -> None:
        self.assertEqual(len(self.result.null_controls), 7)
        self.assertEqual(
            {evidence.seed for evidence in self.result.null_controls}, {17}
        )
        self.assertEqual(len(self.result.multiple_testing.primary_p_values), 7)
        self.assertEqual(
            {identity for identity, _ in self.result.multiple_testing.primary_p_values},
            {trial.trial_content_hash for trial in self.result.trial_ledger.trials},
        )

    def test_unavailable_trial_p_value_makes_bh_unavailable_without_dropping_it(self) -> None:
        summary = self.result.multiple_testing
        unavailable = [
            identity for identity, value in summary.primary_p_values if value is None
        ]
        self.assertEqual(summary.trial_count, 7)
        if unavailable:
            self.assertEqual(summary.status, STATUS_UNAVAILABLE)
            self.assertIn("primary_null_p_value_unavailable", summary.unavailable_reasons)
            for identity in unavailable:
                self.assertIn(
                    f"trial_p_value_unavailable:{identity}", summary.unavailable_reasons
                )
            self.assertEqual(summary.bh_discoveries, ())
            self.assertIsNone(summary.bh_threshold)
        else:
            self.assertEqual(summary.status, STATUS_AVAILABLE)
            self.assertEqual(summary.bh_tested, 7)
        self.assertEqual(len(summary.primary_unavailable_reasons), 7)

    def test_bh_denominator_retains_every_trial_when_all_p_values_exist(self) -> None:
        ledger = self.result.trial_ledger
        p_values = {
            trial.trial_content_hash: Decimal("0.01") * Decimal(index + 1)
            for index, trial in enumerate(ledger.trials)
        }
        summary = build_open_to_open_multiple_testing_summary_v1(
            ledger=ledger,
            primary_p_values=p_values,
            primary_unavailable_reasons={},
            pbo_orchestration=self.result.pbo_orchestration,
            deflated_sharpe_evidence=self.result.deflated_sharpe,
        )
        self.assertEqual(summary.status, STATUS_AVAILABLE)
        self.assertEqual(summary.bh_tested, 7)
        self.assertEqual(summary.bh_alpha, Decimal("0.05"))
        self.assertEqual(summary.datasets_or_universes_examined, 1)
        self.assertEqual(summary.feature_combinations_examined, 1)
        self.assertEqual(summary.ledger_content_hash, ledger.content_hash)

    def test_multiple_testing_rejects_a_partial_trial_map(self) -> None:
        ledger = self.result.trial_ledger
        partial = {
            trial.trial_content_hash: Decimal("0.01") for trial in ledger.trials[:6]
        }
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error) as error:
            build_open_to_open_multiple_testing_summary_v1(
                ledger=ledger,
                primary_p_values=partial,
                primary_unavailable_reasons={},
                pbo_orchestration=self.result.pbo_orchestration,
                deflated_sharpe_evidence=self.result.deflated_sharpe,
            )
        self.assertEqual(
            str(error.exception), "multiple_testing_requires_every_ledger_trial"
        )

    def test_pbo_uses_eight_equal_contiguous_blocks_of_pre_holdout_days(self) -> None:
        pbo = self.result.pbo_orchestration
        self.assertEqual(pbo.block_count, CSCV_BLOCKS)
        self.assertEqual(pbo.pre_holdout_observation_count, 120)
        self.assertEqual(pbo.observations_per_block, 15)
        self.assertIsNotNone(pbo.evidence)
        assert pbo.evidence is not None
        self.assertEqual(pbo.evidence.trial_count, 7)
        self.assertEqual(
            set(pbo.evidence.trial_identities),
            {trial.trial_content_hash for trial in self.result.trial_ledger.trials},
        )

    def test_non_divisible_pre_holdout_count_makes_pbo_unavailable_without_trimming(self) -> None:
        ledger = self.result.trial_ledger
        trimmed = replace(
            ledger,
            trials=tuple(
                replace(trial, daily_returns=trial.daily_returns[:119])
                for trial in ledger.trials
            ),
        )
        pbo = evaluate_open_to_open_pbo_orchestration_v1(ledger=trimmed)
        self.assertEqual(pbo.status, STATUS_UNAVAILABLE)
        self.assertEqual(pbo.unavailable_reasons, (PBO_NOT_DIVISIBLE_REASON,))
        self.assertIsNone(pbo.evidence)
        self.assertIsNone(pbo.observations_per_block)
        self.assertEqual(pbo.pre_holdout_observation_count, 119)

    def test_deflated_sharpe_consumes_the_pre_holdout_ledger_only(self) -> None:
        deflated = self.result.deflated_sharpe
        self.assertEqual(deflated.trial_count, 7)
        self.assertEqual(deflated.observation_count, 120)
        self.assertEqual(
            deflated.observation_count, self.result.span.pre_holdout_complete_days
        )
        self.assertNotEqual(
            deflated.observation_count,
            self.result.span.pre_holdout_complete_days + self.result.span.holdout_complete_days,
        )

    def test_bootstrap_uses_three_hundred_sixty_five_periods_per_year(self) -> None:
        daily = self.result.baseline_trial.daily_returns
        expected = evaluate_bootstrap(
            strategy_version=BASELINE.semantic_version,
            dataset_version=str(DATASET_ID),
            period_returns=daily,
            seed=11,
            resamples=8,
            periods_per_year=365,
        )
        calendar_252 = evaluate_bootstrap(
            strategy_version=BASELINE.semantic_version,
            dataset_version=str(DATASET_ID),
            period_returns=daily,
            seed=11,
            resamples=8,
            periods_per_year=252,
        )
        self.assertEqual(
            self.result.bootstrap.identity.content_hash, expected.identity.content_hash
        )
        self.assertNotEqual(
            self.result.bootstrap.identity.content_hash, calendar_252.identity.content_hash
        )

    def test_monte_carlo_uses_canonical_trade_returns_not_daily_returns(self) -> None:
        baseline_run = _pre_holdout_baseline_run(self.evidence, self.result.span)
        trade_based = evaluate_monte_carlo_trade_sequence(
            strategy_version=BASELINE.semantic_version,
            dataset_version=str(DATASET_ID),
            trade_returns=baseline_run.trade_returns,
            seed=13,
            simulations=8,
        )
        daily_based = evaluate_monte_carlo_trade_sequence(
            strategy_version=BASELINE.semantic_version,
            dataset_version=str(DATASET_ID),
            trade_returns=self.result.baseline_trial.daily_returns,
            seed=13,
            simulations=8,
        )
        self.assertEqual(
            self.result.monte_carlo.identity.content_hash, trade_based.identity.content_hash
        )
        self.assertNotEqual(
            self.result.monte_carlo.identity.content_hash, daily_based.identity.content_hash
        )
        self.assertLess(len(baseline_run.trade_returns), 120)

    def test_independent_reconciliation_and_stress_composite(self) -> None:
        self.assertEqual(self.result.reconciliation.status, STATUS_RECONCILED)
        self.assertEqual(self.result.reconciliation.mismatched_decision_count, 0)
        summary = self.result.stress_summary
        self.assertTrue(summary.synthetic_validation_evidence)
        # Module 3B.3: this fixture's bars are legacy/unitless, so the canonical
        # reduced-liquidity ladder is UNAVAILABLE (a data gap) rather than the
        # retired unconditional BLOCKED.
        self.assertEqual(summary.reduced_liquidity_status, STATUS_UNAVAILABLE)
        self.assertEqual(
            summary.reduced_liquidity_reason, REASON_MISSING_CANONICAL_QUOTE_TURNOVER
        )
        self.assertEqual(self.result.reduced_liquidity.envelopes, ())
        self.assertEqual(
            summary.cost_sensitivity_content_hash, self.result.cost_sensitivity.content_hash
        )
        self.assertEqual(
            summary.adverse_exit_shock_content_hash, self.result.adverse_exit_shock.content_hash
        )
        self.assertEqual(
            summary.missing_bar_stress_content_hash, self.result.missing_bar_stress.content_hash
        )
        self.assertEqual(
            summary.reduced_liquidity_content_hash, self.result.reduced_liquidity.content_hash
        )

    def test_cost_and_latency_grids_are_evaluated(self) -> None:
        self.assertEqual(
            [scenario.cost_multiplier for scenario in self.result.cost_sensitivity.scenarios],
            [Decimal("1.0"), Decimal("1.5"), Decimal("2.0"), Decimal("3.0")],
        )
        self.assertEqual(
            [scenario.latency_minutes for scenario in self.result.latency_sensitivity.scenarios],
            [0, 1, 5, 15],
        )
        self.assertEqual(
            [scenario.shock_magnitude for scenario in self.result.adverse_exit_shock.scenarios],
            [Decimal("0.001"), Decimal("0.005")],
        )
        self.assertEqual(
            self.result.missing_bar_stress.omitted_exit_bar_open_times,
            (START + timedelta(minutes=3),),
        )

    def test_execution_realism_is_blocked(self) -> None:
        self.assertEqual(self.result.execution_realism.status, STATUS_BLOCKED)
        self.assertEqual(
            self.result.execution_realism.reason,
            "NO_AUTHORIZED_TOP_OF_BOOK_OR_BROKER_FILL_EVIDENCE",
        )
        self.assertFalse(hasattr(self.result.execution_realism, "spread_estimate"))
        self.assertFalse(hasattr(self.result.execution_realism, "fill_quality"))

    def test_capacity_is_unavailable_on_legacy_bars_and_data_quality_is_blocked(self) -> None:
        # Module 3B.3 case A: these fixture bars carry no Phase 3B.2 typed
        # volume/turnover semantics, so no canonical quote-turnover liquidity
        # exists and capacity must stay UNAVAILABLE -- never a numeric estimate,
        # and never the retired MISSING_AUTHORIZED_VOLUME_UNIT_SEMANTICS claim.
        self.assertEqual(self.result.capacity.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            self.result.capacity.unavailable_reasons, (REASON_MISSING_CANONICAL_QUOTE_TURNOVER,)
        )
        self.assertIsNone(self.result.capacity.baseline_envelope)
        self.assertEqual(self.result.capacity.complete_days, ())
        self.assertFalse(self.result.capacity.order_book_evidence)
        self.assertIsNone(self.result.capacity.policy_version)
        self.assertEqual(self.result.data_quality.status, STATUS_BLOCKED)
        self.assertEqual(self.result.data_quality.reason, "FIXTURE_ONLY_MARKET_DATA")
        self.assertEqual(self.result.reduced_liquidity.status, STATUS_UNAVAILABLE)
        self.assertIn(
            f"CAPACITY_{STATUS_UNAVAILABLE}:{REASON_MISSING_CANONICAL_QUOTE_TURNOVER}",
            self.result.blocking_reasons,
        )

    def test_methodology_freeze_binds_every_pre_holdout_artifact(self) -> None:
        freeze = self.result.methodology_freeze
        self.assertEqual(freeze.baseline_definition_content_hash, BASELINE.content_hash())
        self.assertEqual(len(freeze.neighbor_definition_content_hashes), 6)
        self.assertEqual(freeze.protocol_content_hash, PROTOCOL.content_hash())
        self.assertEqual(freeze.fold_count, 8)
        self.assertEqual(freeze.neighbor_steps_content_hash, NEIGHBOR_STEPS.content_hash())
        self.assertEqual(freeze.cost_model_version, COST_MODEL_VERSION)
        self.assertEqual(freeze.null_seed, 17)
        self.assertEqual(freeze.bootstrap_seed, 11)
        self.assertEqual(freeze.monte_carlo_seed, 13)
        self.assertEqual(
            freeze.research_trial_ledger_content_hash, self.result.trial_ledger.content_hash
        )
        self.assertEqual(
            freeze.walk_forward_evidence_content_hash,
            self.result.walk_forward_evidence.content_hash,
        )
        self.assertEqual(
            freeze.multiple_testing_content_hash, self.result.multiple_testing.content_hash
        )
        self.assertEqual(
            freeze.deflated_sharpe_content_hash, self.result.deflated_sharpe.content_hash
        )
        robustness = dict(freeze.pre_holdout_robustness_content_hashes)
        self.assertEqual(
            robustness["golden_reconciliation"], self.result.reconciliation.content_hash
        )
        self.assertEqual(robustness["bootstrap"], self.result.bootstrap.identity.content_hash)
        self.assertEqual(robustness["monte_carlo"], self.result.monte_carlo.identity.content_hash)

    def test_holdout_binds_the_methodology_freeze_hash_and_evaluates_baseline_only(self) -> None:
        holdout = self.result.holdout
        self.assertEqual(holdout.status, STATUS_AVAILABLE)
        self.assertEqual(
            holdout.methodology_freeze_content_hash, self.result.methodology_freeze.content_hash
        )
        self.assertEqual(holdout.baseline_definition_content_hash, BASELINE.content_hash())
        self.assertEqual(holdout.holdout_start, START + timedelta(days=120))
        self.assertEqual(holdout.holdout_end, START + timedelta(days=150))
        self.assertEqual(len(holdout.daily_returns), 30)
        self.assertGreaterEqual(holdout.executed_trade_count, 1)
        expected = _holdout_baseline_run(self.evidence, self.result.span)
        self.assertEqual(holdout.run_content_hash, expected.content_hash)
        for neighbor in self.result.neighbor_definitions:
            neighbor_run = _holdout_baseline_run(self.evidence, self.result.span, neighbor)
            self.assertNotEqual(holdout.run_content_hash, neighbor_run.content_hash)

    def test_holdout_exposes_no_pass_threshold_or_promotion_action(self) -> None:
        holdout = self.result.holdout
        for forbidden in ("passed", "promotion", "selected_parameters", "promote"):
            self.assertFalse(hasattr(holdout, forbidden))

    def test_headline_metric_source_is_the_untouched_holdout(self) -> None:
        self.assertEqual(self.result.headline_metric_source, DIMENSION_UNTOUCHED_HOLDOUT)

    def test_daily_metrics_are_dimensioned_daily_realized_exit(self) -> None:
        daily = [
            metric
            for metric in self.result.scorecard.metrics
            if DIMENSION_DAILY_REALIZED_EXIT in metric.dimensions
        ]
        self.assertEqual(
            {metric.name for metric in daily},
            {
                "total_return",
                "cagr",
                "annualized_return",
                "annualized_volatility",
                "sharpe",
                "sortino",
                "calmar",
                "max_drawdown",
                "max_drawdown_duration",
                "downside_deviation",
            },
        )
        for metric in daily:
            self.assertEqual(
                metric.dimensions, (DIMENSION_DAILY_REALIZED_EXIT, DIMENSION_UNTOUCHED_HOLDOUT)
            )

    def test_daily_series_never_supplies_trade_level_metrics(self) -> None:
        for metric in self.result.scorecard.metrics:
            if DIMENSION_DAILY_REALIZED_EXIT in metric.dimensions:
                self.assertNotIn(
                    metric.name,
                    {
                        "hit_rate",
                        "win_loss_ratio",
                        "payoff_ratio",
                        "profit_factor",
                        "average_trade",
                        "median_trade",
                        "number_of_trades",
                    },
                )

    def test_trade_metrics_are_dimensioned_trade_level(self) -> None:
        trade = [
            metric
            for metric in self.result.scorecard.metrics
            if metric.dimensions == (DIMENSION_TRADE_LEVEL,)
        ]
        names = {metric.name for metric in trade}
        self.assertTrue(
            {
                "number_of_trades",
                "hit_rate",
                "average_trade",
                "median_trade",
                "win_loss_ratio",
                "payoff_ratio",
                "profit_factor",
                "value_at_risk",
                "conditional_value_at_risk",
            }
            <= names
        )

    def test_scorecard_contains_no_trade_level_sharpe(self) -> None:
        for metric in self.result.scorecard.metrics:
            if DIMENSION_TRADE_LEVEL in metric.dimensions:
                self.assertNotIn(metric.name, {"sharpe", "sortino", "calmar"})

    def test_trade_tail_risk_is_dimensioned_trade_level(self) -> None:
        tail = [
            metric
            for metric in self.result.scorecard.metrics
            if metric.family is MetricFamily.RISK
        ]
        self.assertEqual(
            {metric.name for metric in tail}, {"value_at_risk", "conditional_value_at_risk"}
        )
        for metric in tail:
            self.assertEqual(metric.dimensions, (DIMENSION_TRADE_LEVEL,))

    def test_robustness_metrics_preserve_unavailable_as_unavailable(self) -> None:
        robustness = {
            metric.name: metric
            for metric in self.result.scorecard.metrics
            if metric.family is MetricFamily.ROBUSTNESS
        }
        self.assertTrue(
            {
                "circular_shift_null_empirical_p_value",
                "cscv_pbo",
                "deflated_sharpe",
                "parameter_stability_score",
                "untouched_holdout_available",
            }
            <= set(robustness)
        )
        for metric in robustness.values():
            if metric.state is EvidenceState.UNAVAILABLE:
                self.assertIsNone(metric.value)
            else:
                self.assertIsNotNone(metric.value)
        self.assertEqual(
            robustness["untouched_holdout_available"].value, Decimal("1")
        )

    def test_scorecard_status_and_dataset_health_are_always_blocked(self) -> None:
        self.assertIs(self.result.scorecard.status, ScorecardStatus.BLOCKED)
        self.assertEqual(self.result.scorecard.dataset_health_status, STATUS_BLOCKED)
        self.assertEqual(self.result.scorecard.data_health_assessment_ids, ())
        self.assertEqual(self.result.status, STATUS_BLOCKED)

    def test_scorecard_identity_and_temporal_contract(self) -> None:
        scorecard = self.result.scorecard
        self.assertEqual(scorecard.strategy_id, CANONICAL_STRATEGY_ID)
        self.assertEqual(scorecard.feature_versions, CANONICAL_FEATURE_VERSIONS)
        self.assertEqual(scorecard.dataset_version, str(DATASET_ID))
        self.assertEqual(scorecard.evaluated_at, START + timedelta(days=151))
        self.assertEqual(scorecard.knowledge_cutoff, START + timedelta(days=150))
        self.assertLessEqual(scorecard.knowledge_cutoff, scorecard.evaluated_at)

    def test_scorecard_research_run_id_is_the_holdout_run_when_holdout_available(self) -> None:
        expected = _holdout_baseline_run(self.evidence, self.result.span)
        self.assertEqual(self.result.holdout.status, STATUS_AVAILABLE)
        self.assertEqual(self.result.scorecard.research_run_id, expected.run_id)
        self.assertNotEqual(
            self.result.scorecard.research_run_id,
            self.result.walk_forward_evidence.evidence_id,
        )

    def test_scorecard_evidence_manifest_binds_freeze_holdout_and_headline_source(self) -> None:
        manifest = self.result.scorecard.evidence_manifest
        self.assertEqual(manifest["methodology_freeze"], self.result.methodology_freeze.content_hash)
        self.assertEqual(manifest["untouched_holdout"], self.result.holdout.content_hash)
        self.assertEqual(manifest["headline_metric_source"], self.result.holdout.content_hash)
        self.assertEqual(self.result.headline_metric_source, DIMENSION_UNTOUCHED_HOLDOUT)

    def test_package_evidence_map_still_has_exactly_thirteen_categories(self) -> None:
        package = self.result.validation_package
        self.assertEqual(set(package.evidence_ids), set(REQUIRED_EVIDENCE))
        self.assertEqual(len(package.evidence_ids), 13)
        self.assertNotIn("methodology_freeze", package.evidence_ids)
        self.assertNotIn("untouched_holdout", package.evidence_ids)
        self.assertNotIn("headline_metric_source", package.evidence_ids)

    def test_unused_future_bar_and_feature_do_not_change_knowledge_cutoff_or_identity(self) -> None:
        extended_evidence = _fixture_evidence_with_unused_future_evidence()
        extended = run_open_to_open_professional_validation_v1(_request(extended_evidence))
        self.assertEqual(self.result.span.evaluation_end, extended.span.evaluation_end)
        self.assertEqual(self.result.scorecard.knowledge_cutoff, self.result.span.evaluation_end)
        self.assertEqual(
            self.result.scorecard.knowledge_cutoff, extended.scorecard.knowledge_cutoff
        )
        self.assertEqual(self.result.scorecard.content_hash(), extended.scorecard.content_hash())
        self.assertEqual(
            self.result.methodology_freeze.content_hash, extended.methodology_freeze.content_hash
        )
        self.assertEqual(self.result.data_quality.bar_count, extended.data_quality.bar_count)

    def test_all_required_limitations_are_present(self) -> None:
        for limitation in REQUIRED_SCORECARD_LIMITATIONS:
            self.assertIn(limitation, self.result.scorecard.limitations)
            self.assertIn(limitation, self.result.validation_package.limitations)

    def test_complexity_components_disclose_three_parameters_and_turnover(self) -> None:
        components = self.result.scorecard.components
        self.assertEqual([component.name for component in components], ["complexity_penalty"])
        holdout_run = _holdout_baseline_run(self.evidence, self.result.span)
        turnover = Decimal("0")
        for trade in holdout_run.executed_trades:
            turnover += Decimal("2") * abs(trade.exposure)
        expected = (
            Decimal(3) * Decimal("2") + turnover * Decimal("10") + Decimal("100") / Decimal(30)
        )
        self.assertEqual(components[0].value, expected)

    def test_validation_package_has_exactly_the_thirteen_required_categories(self) -> None:
        package = self.result.validation_package
        self.assertEqual(set(package.evidence_ids), set(REQUIRED_EVIDENCE))
        self.assertEqual(set(package.evidence_hashes), set(REQUIRED_EVIDENCE))
        self.assertEqual(len(REQUIRED_EVIDENCE), 13)
        self.assertEqual(
            package.evidence_hashes["oos_walk_forward"],
            self.result.walk_forward_evidence.content_hash,
        )
        self.assertEqual(
            package.evidence_hashes["golden_reconciliation"],
            self.result.reconciliation.content_hash,
        )
        self.assertEqual(
            package.evidence_hashes["slippage"], self.result.cost_sensitivity.content_hash
        )
        self.assertEqual(
            package.evidence_hashes["latency"], self.result.latency_sensitivity.content_hash
        )
        self.assertEqual(
            package.evidence_hashes["stress"], self.result.stress_summary.content_hash
        )
        self.assertEqual(
            package.evidence_hashes["multiple_testing"], self.result.multiple_testing.content_hash
        )
        self.assertEqual(
            package.evidence_hashes["execution_realism"],
            self.result.execution_realism.content_hash,
        )
        self.assertEqual(
            package.evidence_hashes["data_quality"], self.result.data_quality.content_hash
        )
        self.assertEqual(
            package.evidence_hashes["capacity"], self.result.capacity.content_hash
        )
        self.assertEqual(
            package.evidence_hashes["scorecard"], self.result.scorecard.content_hash()
        )

    def test_package_identity_uses_the_caller_supplied_validation_dataset(self) -> None:
        package = self.result.validation_package
        self.assertEqual(package.dataset_id, VALIDATION_DATASET_ID)
        self.assertEqual(package.dataset_version_id, DATASET_ID)
        self.assertEqual(package.dataset_version, str(DATASET_ID))
        self.assertEqual(package.strategy_id, CANONICAL_STRATEGY_ID)
        self.assertEqual(package.strategy_version_id, BASELINE.definition_id)
        self.assertEqual(package.feature_versions, CANONICAL_FEATURE_VERSIONS)

    def test_package_completeness_does_not_change_the_blocked_state(self) -> None:
        package = self.result.validation_package
        self.assertEqual(package.promotion_status, "REVIEW_REQUIRED_OR_BLOCKED")
        self.assertEqual(self.result.status, STATUS_BLOCKED)
        self.assertEqual(package.validation_metadata["maximum_automatic_state"], STATUS_BLOCKED)
        self.assertEqual(package.validation_metadata["scorecard_status"], STATUS_BLOCKED)

    def test_package_paper_shadow_and_live_authority_are_all_false(self) -> None:
        metadata = self.result.validation_package.validation_metadata
        self.assertIs(metadata["paper_authority"], False)
        self.assertIs(metadata["shadow_authority"], False)
        self.assertIs(metadata["live_authority"], False)
        self.assertIs(metadata["fixture_only"], True)
        self.assertEqual(metadata["research_mode"], "RESEARCH_ONLY")
        self.assertEqual(metadata["trial_count"], 7)
        self.assertIs(metadata["parameter_selection_before_holdout"], True)
        self.assertIs(metadata["baseline_remained_selected"], True)
        self.assertEqual(metadata["capacity_status"], STATUS_UNAVAILABLE)
        self.assertEqual(metadata["capacity_reason"], REASON_MISSING_CANONICAL_QUOTE_TURNOVER)
        self.assertIsNone(metadata["capacity_policy_version"])
        self.assertIs(metadata["automatic_live_capacity_gating"], False)
        self.assertEqual(metadata["execution_realism_status"], STATUS_BLOCKED)
        self.assertEqual(
            metadata["methodology_freeze_hash"], self.result.methodology_freeze.content_hash
        )
        self.assertEqual(metadata["holdout_status"], STATUS_AVAILABLE)
        self.assertEqual(
            metadata["holdout_start"], (START + timedelta(days=120)).isoformat()
        )

    def test_blocking_reasons_are_recorded(self) -> None:
        self.assertIn("FIXTURE_ONLY_MARKET_DATA", self.result.blocking_reasons)
        self.assertIn(
            "NO_AUTHORIZED_TOP_OF_BOOK_OR_BROKER_FILL_EVIDENCE", self.result.blocking_reasons
        )
        self.assertIn(
            "RESEARCH_ONLY_NO_PAPER_OR_LIVE_AUTHORITY", self.result.blocking_reasons
        )


class DeterminismAndIsolationTests(unittest.TestCase):
    def test_identical_inputs_produce_identical_identities(self) -> None:
        first = run_open_to_open_professional_validation_v1(_request(_fixture_evidence()))
        second = run_open_to_open_professional_validation_v1(_request(_fixture_evidence()))
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(first.methodology_freeze.content_hash, second.methodology_freeze.content_hash)
        self.assertEqual(first.scorecard.scorecard_id, second.scorecard.scorecard_id)
        self.assertEqual(
            first.validation_package.identity.artifact_id,
            second.validation_package.identity.artifact_id,
        )
        self.assertEqual(
            first.validation_package.identity.content_hash,
            second.validation_package.identity.content_hash,
        )

    def test_holdout_mutation_leaves_every_pre_holdout_identity_unchanged(self) -> None:
        baseline = run_open_to_open_professional_validation_v1(_request(_fixture_evidence()))
        mutated_evidence = _fixture_evidence(value_overrides={126: Decimal("-0.0045")})
        mutated = run_open_to_open_professional_validation_v1(_request(mutated_evidence))
        self.assertEqual(
            baseline.methodology_freeze.content_hash, mutated.methodology_freeze.content_hash
        )
        self.assertEqual(
            baseline.walk_forward_evidence.content_hash,
            mutated.walk_forward_evidence.content_hash,
        )
        self.assertEqual(baseline.trial_ledger.content_hash, mutated.trial_ledger.content_hash)
        self.assertEqual(
            baseline.parameter_stability.identity.content_hash,
            mutated.parameter_stability.identity.content_hash,
        )
        self.assertEqual(
            baseline.multiple_testing.content_hash, mutated.multiple_testing.content_hash
        )
        self.assertEqual(
            baseline.deflated_sharpe.content_hash, mutated.deflated_sharpe.content_hash
        )
        self.assertEqual(
            baseline.pbo_orchestration.content_hash, mutated.pbo_orchestration.content_hash
        )
        self.assertEqual(baseline.bootstrap.identity.content_hash, mutated.bootstrap.identity.content_hash)
        self.assertNotEqual(baseline.holdout.content_hash, mutated.holdout.content_hash)

    def test_holdout_bar_mutation_leaves_every_pre_holdout_identity_unchanged(self) -> None:
        baseline_evidence = _fixture_evidence()
        baseline = run_open_to_open_professional_validation_v1(_request(baseline_evidence))

        holdout_day = 126
        holdout_day_index = list(_active_days(SPAN_DAYS)).index(holdout_day)
        entry_bar_open_at = START + timedelta(days=holdout_day, minutes=1)
        self.assertGreaterEqual(entry_bar_open_at, baseline.span.holdout_start)
        self.assertLess(entry_bar_open_at, baseline.span.evaluation_end)
        mutated_entry_bar = _mutated_bar(
            entry_bar_open_at, _day_open(holdout_day_index, 1)
        )
        mutated_evidence = _fixture_evidence(bar_overrides={entry_bar_open_at: mutated_entry_bar})
        mutated = run_open_to_open_professional_validation_v1(_request(mutated_evidence))

        expected_baseline_holdout_run = _holdout_baseline_run(baseline_evidence, baseline.span)
        self.assertGreaterEqual(expected_baseline_holdout_run.executed_trade_count, 1)
        self.assertTrue(
            any(
                trade.entry_bar.bar_open_at == entry_bar_open_at
                for trade in expected_baseline_holdout_run.executed_trades
            )
        )

        self.assertEqual(
            baseline.methodology_freeze.content_hash, mutated.methodology_freeze.content_hash
        )
        self.assertEqual(
            baseline.walk_forward_evidence.content_hash,
            mutated.walk_forward_evidence.content_hash,
        )
        for earlier_fold, later_fold in zip(
            baseline.walk_forward_evidence.folds, mutated.walk_forward_evidence.folds, strict=True
        ):
            self.assertEqual(earlier_fold.test_run_content_hash, later_fold.test_run_content_hash)
        self.assertEqual(baseline.trial_ledger.content_hash, mutated.trial_ledger.content_hash)
        self.assertEqual(
            baseline.parameter_stability.identity.content_hash,
            mutated.parameter_stability.identity.content_hash,
        )
        self.assertEqual(
            baseline.pbo_orchestration.content_hash, mutated.pbo_orchestration.content_hash
        )
        self.assertEqual(
            baseline.deflated_sharpe.content_hash, mutated.deflated_sharpe.content_hash
        )
        self.assertEqual(
            baseline.multiple_testing.content_hash, mutated.multiple_testing.content_hash
        )
        self.assertEqual(
            baseline.bootstrap.identity.content_hash, mutated.bootstrap.identity.content_hash
        )
        self.assertEqual(
            baseline.reconciliation.content_hash, mutated.reconciliation.content_hash
        )
        self.assertEqual(
            baseline.latency_sensitivity.content_hash, mutated.latency_sensitivity.content_hash
        )
        self.assertEqual(
            [item.content_hash for item in baseline.null_controls],
            [item.content_hash for item in mutated.null_controls],
        )

        self.assertNotEqual(baseline.holdout.content_hash, mutated.holdout.content_hash)
        self.assertNotEqual(baseline.content_hash, mutated.content_hash)

    def test_semantic_input_change_changes_the_run_identity(self) -> None:
        evidence = _fixture_evidence()
        first = run_open_to_open_professional_validation_v1(_request(evidence))
        reseeded = replace(_request(evidence), null_seed=19)
        second = run_open_to_open_professional_validation_v1(reseeded)
        self.assertNotEqual(first.content_hash, second.content_hash)
        self.assertNotEqual(
            first.methodology_freeze.content_hash, second.methodology_freeze.content_hash
        )

    def test_module_creates_no_persistence_and_reads_no_wall_clock(self) -> None:
        source = Path(orchestration.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "sqlite3",
            "psycopg",
            "PostgresDatabase",
            "INSERT INTO",
            "CREATE TABLE",
            "SQLiteValidationEvidenceStore",
            "PostgresStrategyScorecardStore",
            "PostgresValidationPackageStore",
            "from .persistence",
            "utc_now",
        ):
            self.assertNotIn(forbidden, source)

    def test_module_never_reaches_for_the_legacy_close_to_close_apis(self) -> None:
        source = Path(orchestration.__file__).read_text(encoding="utf-8")
        for legacy in (
            "run_purged_walk_forward",
            "evaluate_multiple_testing",
            "run_vectorized_backtest",
            "PurgedWalkForwardSplit",
            "evaluate_slippage_sensitivity",
            "evaluate_latency_sensitivity",
            "evaluate_capacity",
            "evaluate_stress",
        ):
            self.assertNotIn(legacy, source)


class CapacityPolicyPlumbingTests(unittest.TestCase):
    """Module 3B.3's optional capacity policy: wired, but never self-authorizing.

    The orchestration fixture's bars are legacy/unitless, so supplying a policy
    must NOT make capacity available -- the phase's explicit requirement that a
    data-authority fix does not silently turn into an economic claim. What a
    supplied policy does do is reach the evidence and change request identity.
    """

    policy = LiquidityCapacityPolicyV1(
        policy_version="fixture-orchestration-capacity-policy-v1",
        lookback_complete_days=2,
        minimum_complete_days=2,
        maximum_participation=Decimal("0.05"),
        reduced_liquidity_multipliers=(Decimal("0.25"), Decimal("0.50")),
    )
    capital_levels = (Decimal("1000000"),)

    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = _fixture_evidence()
        cls.without_policy = _request(cls.evidence)
        cls.with_policy = replace(
            cls.without_policy,
            capacity_policy=cls.policy,
            capacity_capital_levels=cls.capital_levels,
        )

    def test_a_request_written_before_this_phase_keeps_its_exact_identity(self) -> None:
        self.assertIsNone(self.without_policy.capacity_policy)
        self.assertEqual(self.without_policy.capacity_capital_levels, ())
        self.assertNotEqual(
            self.with_policy.content_hash(), self.without_policy.content_hash()
        )

    def test_a_policy_reaches_the_capacity_evidence_without_authorizing_it(self) -> None:
        result = run_open_to_open_professional_validation_v1(self.with_policy)
        self.assertEqual(result.capacity.policy_version, self.policy.policy_version)
        self.assertEqual(result.capacity.policy_content_hash, self.policy.content_hash())
        self.assertEqual(result.capacity.capital_levels, self.capital_levels)
        # Legacy/unitless bars: still UNAVAILABLE, policy or no policy.
        self.assertEqual(result.capacity.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            result.capacity.unavailable_reasons, (REASON_MISSING_CANONICAL_QUOTE_TURNOVER,)
        )
        self.assertIsNone(result.capacity.baseline_envelope)
        self.assertEqual(result.reduced_liquidity.envelopes, ())
        self.assertEqual(result.status, STATUS_BLOCKED)

    def test_capacity_policy_requires_capital_levels(self) -> None:
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            replace(self.without_policy, capacity_policy=self.policy).validate()

    def test_invalid_capacity_capital_levels_rejected(self) -> None:
        for levels in ((Decimal("0"),), (Decimal("5"), Decimal("1")), (Decimal("1"), Decimal("1"))):
            with (
                self.subTest(str(levels)),
                self.assertRaises(OpenToOpenValidationOrchestrationV1Error),
            ):
                replace(
                    self.without_policy,
                    capacity_policy=self.policy,
                    capacity_capital_levels=levels,
                ).validate()

    def test_instrument_contract_is_optional_and_bound_to_request_identity(self) -> None:
        contract = AuthorizedInstrumentLiquidityContractV1(
            instrument_id=INSTRUMENT,
            venue="TESTFIXTUREVENUE",
            base_asset="BTC",
            quote_asset="USDT",
            contract_reference="fixture://orchestration-instrument-contract-v1",
        )
        with_contract = replace(self.without_policy, capacity_instrument_contract=contract)
        with_contract.validate()
        self.assertNotEqual(
            with_contract.content_hash(), self.without_policy.content_hash()
        )
        result = run_open_to_open_professional_validation_v1(with_contract)
        # The contract is now known, so the reason is the one that actually
        # describes these bars: they carry no typed semantics at all.
        self.assertEqual(result.capacity.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            result.capacity.unavailable_reasons, (REASON_MISSING_CANONICAL_QUOTE_TURNOVER,)
        )

    def test_instrument_contract_for_another_instrument_rejected(self) -> None:
        contract = AuthorizedInstrumentLiquidityContractV1(
            instrument_id="TESTFIXTURE:3J2B2B2:ETHUSDT:PERP",
            venue="TESTFIXTUREVENUE",
            base_asset="ETH",
            quote_asset="USDT",
            contract_reference="fixture://orchestration-instrument-contract-v1",
        )
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            replace(self.without_policy, capacity_instrument_contract=contract).validate()

    def test_invalid_capacity_policy_rejected_by_the_request(self) -> None:
        with self.assertRaises(CryptoLiquidityCapacityV1Error):
            replace(
                self.without_policy,
                capacity_policy=replace(self.policy, maximum_participation=Decimal("2")),
                capacity_capital_levels=self.capital_levels,
            ).validate()


class UnavailableHoldoutOrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = _fixture_evidence(span_days=SHORT_HOLDOUT_SPAN_DAYS)
        cls.result = run_open_to_open_professional_validation_v1(
            _request(cls.evidence, span_days=SHORT_HOLDOUT_SPAN_DAYS)
        )

    def test_unavailable_holdout_does_not_abort_the_run(self) -> None:
        self.assertEqual(self.result.span.holdout_status, STATUS_UNAVAILABLE)
        self.assertEqual(self.result.holdout.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            self.result.holdout.unavailable_reasons,
            ("insufficient_untouched_holdout_complete_days",),
        )
        self.assertIsNone(self.result.holdout.run_content_hash)
        self.assertEqual(self.result.holdout.daily_returns, ())
        self.assertEqual(self.result.status, STATUS_BLOCKED)

    def test_scorecard_and_package_still_exist_and_stay_blocked(self) -> None:
        self.assertIs(self.result.scorecard.status, ScorecardStatus.BLOCKED)
        self.assertEqual(self.result.scorecard.dataset_health_status, STATUS_BLOCKED)
        self.assertEqual(
            set(self.result.validation_package.evidence_ids), set(REQUIRED_EVIDENCE)
        )
        self.assertEqual(
            self.result.validation_package.promotion_status, "REVIEW_REQUIRED_OR_BLOCKED"
        )
        self.assertEqual(
            self.result.validation_package.validation_metadata["holdout_status"],
            STATUS_UNAVAILABLE,
        )

    def test_headline_metrics_fall_back_to_pre_holdout_oos_test_evidence(self) -> None:
        self.assertEqual(self.result.headline_metric_source, DIMENSION_PRE_HOLDOUT_OOS)
        daily = [
            metric
            for metric in self.result.scorecard.metrics
            if DIMENSION_DAILY_REALIZED_EXIT in metric.dimensions
        ]
        self.assertTrue(daily)
        for metric in daily:
            self.assertEqual(
                metric.dimensions, (DIMENSION_DAILY_REALIZED_EXIT, DIMENSION_PRE_HOLDOUT_OOS)
            )
        expected_days = sum(
            len(fold.test_daily_returns) for fold in self.result.walk_forward_evidence.folds
        )
        self.assertEqual(expected_days, 70)
        trade_count = next(
            metric.value
            for metric in self.result.scorecard.metrics
            if metric.name == "number_of_trades" and DIMENSION_TRADE_LEVEL in metric.dimensions
        )
        assert trade_count is not None
        expected_trades = sum(
            fold.test_executed_trade_count for fold in self.result.walk_forward_evidence.folds
        )
        self.assertEqual(trade_count, Decimal(expected_trades))
        turnover = trade_count * Decimal("2") * BASELINE.maximum_absolute_exposure
        self.assertEqual(
            self.result.scorecard.components[0].value,
            Decimal(3) * Decimal("2")
            + turnover * Decimal("10")
            + Decimal("100") / Decimal(expected_days),
        )

    def test_untouched_holdout_metric_is_reported_as_zero_availability(self) -> None:
        robustness = {
            metric.name: metric
            for metric in self.result.scorecard.metrics
            if metric.family is MetricFamily.ROBUSTNESS
        }
        self.assertEqual(robustness["untouched_holdout_available"].value, Decimal("0"))
        self.assertEqual(
            robustness["untouched_holdout_available"].dimensions, (DIMENSION_UNTOUCHED_HOLDOUT,)
        )

    def test_scorecard_research_run_id_is_the_walk_forward_evidence_id_when_holdout_unavailable(
        self,
    ) -> None:
        self.assertEqual(self.result.holdout.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            self.result.scorecard.research_run_id, self.result.walk_forward_evidence.evidence_id
        )
        self.assertNotEqual(
            self.result.scorecard.research_run_id,
            uuid5(NAMESPACE_URL, "crypto-basis-mean-reversion-run-v1:not-a-real-run"),
        )

    def test_scorecard_evidence_manifest_headline_source_matches_walk_forward_evidence(
        self,
    ) -> None:
        manifest = self.result.scorecard.evidence_manifest
        self.assertEqual(manifest["methodology_freeze"], self.result.methodology_freeze.content_hash)
        self.assertEqual(manifest["untouched_holdout"], self.result.holdout.content_hash)
        self.assertEqual(
            manifest["headline_metric_source"], self.result.walk_forward_evidence.content_hash
        )
        self.assertNotEqual(manifest["headline_metric_source"], self.result.holdout.content_hash)


class RequestValidationTests(unittest.TestCase):
    def test_non_perpetual_instrument_is_rejected(self) -> None:
        request = replace(
            _request(_fixture_evidence(span_days=10)),
            instrument_kind=CryptoInstrumentKind.SPOT,
        )
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error) as error:
            request.validate()
        self.assertEqual(
            str(error.exception), "orchestration_requires_perpetual_instrument"
        )

    def test_missing_scenario_inputs_are_rejected(self) -> None:
        base = _request(_fixture_evidence(span_days=10))
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            replace(base, adverse_exit_shock_magnitudes=()).validate()
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            replace(base, missing_exit_stress_bar_open_times=()).validate()
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            replace(base, bootstrap_resamples=0).validate()
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            replace(base, monte_carlo_simulations=0).validate()
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            replace(base, cost_model_version=" ").validate()

    def test_naive_evaluated_at_is_rejected(self) -> None:
        request = replace(
            _request(_fixture_evidence(span_days=10)),
            evaluated_at=datetime(2026, 6, 1),  # noqa: DTZ001
        )
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error) as error:
            request.validate()
        self.assertEqual(str(error.exception), "evaluated_at_must_be_timezone_aware")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
