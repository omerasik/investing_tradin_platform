from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from math import sqrt
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .crypto_basis_mean_reversion_v1 import (
    BasisMeanReversionResearchRunV1,
    BasisMeanReversionTradeV1,
    CryptoBasisMeanReversionDefinitionV1,
    run_crypto_basis_mean_reversion_research,
)
from .crypto_instruments import CryptoInstrumentKind
from .feature_authority import FeatureMaterializationV2
from .open_to_open_validation_v1 import (
    CSCV_BLOCKS,
    STATUS_AVAILABLE,
    STATUS_BLOCKED,
    STATUS_RECONCILED,
    STATUS_UNAVAILABLE,
    AdverseExitShockEvidenceV1,
    CanonicalCscvPboEvidenceV1,
    CapacityBlockedEvidenceV1,
    CircularShiftNullControlEvidenceV1,
    DeflatedSharpeEvidenceV1,
    MissingBarStressEvidenceV1,
    OpenToOpenCostSensitivityEvidenceV1,
    OpenToOpenLatencySensitivityEvidenceV1,
    OpenToOpenReconciliationEvidenceV1,
    RealizedExitDailyReturnSeriesV1,
    ReducedLiquidityBlockedEvidenceV1,
    ResearchTrialDispositionV1,
    ResearchTrialLedgerV1,
    ResearchTrialRoleV1,
    ResearchTrialV1,
    TradeReturnMetricsV1,
    _direction,
    build_capacity_blocked_evidence_v1,
    build_realized_exit_daily_return_series_v1,
    build_reduced_liquidity_blocked_evidence_v1,
    build_research_trial_ledger_v1,
    build_research_trial_v1,
    canonical_trade_returns_for_monte_carlo,
    evaluate_canonical_cscv_pbo_v1,
    evaluate_circular_shift_null_control_v1,
    evaluate_deflated_sharpe_evidence_v1,
    evaluate_open_to_open_adverse_exit_shock_v1,
    evaluate_open_to_open_cost_sensitivity_v1,
    evaluate_open_to_open_latency_sensitivity_v1,
    evaluate_open_to_open_missing_bar_stress_v1,
    non_annualized_daily_sharpe,
    reconcile_open_to_open_trade_ledger_v1,
    trade_return_metrics_v1,
)
from .quant_validation import (
    BootstrapEvidence,
    MonteCarloEvidence,
    ParameterResult,
    ParameterStabilityEvidence,
    StrategyValidationPackage,
    build_validation_package,
    evaluate_bootstrap,
    evaluate_monte_carlo_trade_sequence,
    evaluate_parameter_stability,
)
from .research import CostModel
from .research_validation import benjamini_hochberg
from .strategy_feature_binding_v2 import (
    AuthoritativeFeatureSeriesV2,
    SubjectAwareResearchFeatureBundle,
)
from .strategy_scorecard_v2 import (
    EvidenceState,
    MetricFamily,
    MetricObservation,
    ScorecardStatus,
    StrategyScorecardV2,
    complexity_components,
    performance_metrics,
    tail_risk_metrics,
)
from .tradable_bar_evidence_v2 import AuthoritativeTradableBarSeriesV2
from .tradable_research_evidence_v2 import SubjectAwareTradableResearchEvidenceV2

_UTC = timezone.utc
_ONE_DAY = timedelta(days=1)
_ONE_BAR_INTERVAL = timedelta(minutes=1)
_NAMESPACE = uuid5(NAMESPACE_URL, "trade_platform.open_to_open_validation_orchestration_v1")

CANONICAL_STRATEGY_NAME = "crypto_basis_mean_reversion"
CANONICAL_STRATEGY_ID = uuid5(NAMESPACE_URL, f"strategy:{CANONICAL_STRATEGY_NAME}")
CANONICAL_FEATURE_VERSIONS = ("crypto_mark_index_basis:1.0.0",)
CANONICAL_SCORECARD_SCHEMA_VERSION = "strategy-scorecard-v2"

MINIMUM_PRE_HOLDOUT_COMPLETE_DAYS = 90
MINIMUM_HOLDOUT_COMPLETE_DAYS = 30
HOLDOUT_SPLIT_NUMERATOR = 8
HOLDOUT_SPLIT_DENOMINATOR = 10

EXPECTED_TRIAL_COUNT = 7
EXPECTED_NEIGHBOR_COUNT = 6
STRATEGY_PARAMETER_COUNT = 3
BH_ALPHA = Decimal("0.05")
DATASETS_OR_UNIVERSES_EXAMINED = 1
FEATURE_COMBINATIONS_EXAMINED = 1
PRESENTATION_PERIODS_PER_YEAR = 365

PBO_NOT_DIVISIBLE_REASON = "preholdout_daily_observation_count_not_divisible_by_eight"
EXECUTION_REALISM_BLOCKED_REASON = "NO_AUTHORIZED_TOP_OF_BOOK_OR_BROKER_FILL_EVIDENCE"
DATA_QUALITY_BLOCKED_REASON = "FIXTURE_ONLY_MARKET_DATA"
RESEARCH_MODE = "RESEARCH_ONLY"
PROMOTION_STATUS_REVIEW_REQUIRED_OR_BLOCKED = "REVIEW_REQUIRED_OR_BLOCKED"

DIMENSION_TRADE_LEVEL = "TRADE_LEVEL"
DIMENSION_DAILY_REALIZED_EXIT = "DAILY_REALIZED_EXIT"
DIMENSION_UNTOUCHED_HOLDOUT = "UNTOUCHED_HOLDOUT"
DIMENSION_PRE_HOLDOUT_OOS = "PRE_HOLDOUT_OOS"

HEADLINE_SOURCE_UNTOUCHED_HOLDOUT = DIMENSION_UNTOUCHED_HOLDOUT
HEADLINE_SOURCE_PRE_HOLDOUT_OOS = DIMENSION_PRE_HOLDOUT_OOS

DAILY_METRIC_NAMES = (
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
)

REQUIRED_SCORECARD_LIMITATIONS = (
    "FIXTURE_ONLY_MARKET_DATA",
    "NO_REAL_TOP_OF_BOOK",
    "NO_BROKER_FILL_EVIDENCE",
    "NO_AUTHORIZED_EXECUTION_REALISM",
    "NO_FUNDING_ACCOUNTING",
    "REALIZED_ON_EXIT_NOT_MARK_TO_MARKET",
    "NO_INTRATRADE_DRAWDOWN_VISIBILITY",
    "RESEARCH_ONLY_NO_PAPER_OR_LIVE_AUTHORITY",
)

PARAMETER_NAME_ORDER = (
    "basis_entry_threshold",
    "holding_horizon_bars",
    "maximum_absolute_exposure",
)


class OpenToOpenValidationOrchestrationV1Error(ValueError):
    pass


class OpenToOpenSegmentKindV1(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"
    FULL_PRE_HOLDOUT = "FULL_PRE_HOLDOUT"
    UNTOUCHED_HOLDOUT = "UNTOUCHED_HOLDOUT"


def _wire(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_wire(item) for item in value]
    return value


def _content_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(_wire(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _identity(kind: str, content_hash: str) -> UUID:
    return uuid5(_NAMESPACE, f"{kind}:{content_hash}")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OpenToOpenValidationOrchestrationV1Error(f"{name}_must_be_timezone_aware")


def _require_utc_midnight(value: datetime, name: str) -> None:
    _require_aware(value, name)
    if value.utcoffset() != timedelta(0):
        raise OpenToOpenValidationOrchestrationV1Error(f"{name}_must_be_utc")
    if (value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0):
        raise OpenToOpenValidationOrchestrationV1Error(f"{name}_must_be_utc_midnight")


def _floor_to_utc_midnight(value: datetime) -> datetime:
    utc = value.astimezone(_UTC)
    return utc.replace(hour=0, minute=0, second=0, microsecond=0)


def _ceil_to_utc_midnight(value: datetime) -> datetime:
    utc = value.astimezone(_UTC)
    floored = _floor_to_utc_midnight(utc)
    return floored if floored == utc else floored + _ONE_DAY


def _complete_days(start: datetime, end: datetime) -> int:
    if end <= start:
        return 0
    return (end - start) // _ONE_DAY


def _compound(returns: Sequence[Decimal]) -> Decimal:
    equity = Decimal("1")
    for value in returns:
        equity *= Decimal("1") + value
    return equity - Decimal("1")


def _annualized_daily_sharpe(returns: Sequence[Decimal]) -> Decimal | None:
    value = non_annualized_daily_sharpe(returns)
    if value is None:
        return None
    return Decimal(str(value * sqrt(PRESENTATION_PERIODS_PER_YEAR)))


def _wire_cost_model(cost_model: CostModel) -> dict[str, str]:
    return {
        "fixed_per_turnover": str(cost_model.fixed_per_turnover),
        "percentage_per_turnover": str(cost_model.percentage_per_turnover),
        "spread_fraction_per_turnover": str(cost_model.spread_fraction_per_turnover),
    }


def _cost_model_content_hash(cost_model: CostModel) -> str:
    return _content_hash({"cost_model": _wire_cost_model(cost_model)})


def canonical_feature_decision_at(materialization: FeatureMaterializationV2) -> datetime:
    return max(
        materialization.event_at,
        materialization.effective_at,
        materialization.knowledge_at,
        materialization.computed_at,
    )


def _sole_basis_feature_series(
    evidence: SubjectAwareTradableResearchEvidenceV2,
) -> AuthoritativeFeatureSeriesV2:
    feature_series = evidence.feature_bundle.feature_series
    if len(feature_series) != 1:
        raise OpenToOpenValidationOrchestrationV1Error(
            "orchestration_requires_exactly_one_feature_series"
        )
    return feature_series[0]


@dataclass(frozen=True, slots=True)
class OpenToOpenEvaluationSpanV1:
    evaluation_start: datetime
    evaluation_end: datetime
    raw_holdout_start: datetime
    holdout_start: datetime
    pre_holdout_complete_days: int
    holdout_complete_days: int
    holdout_status: str
    holdout_unavailable_reasons: tuple[str, ...]
    content_hash: str
    span_id: UUID


def derive_open_to_open_evaluation_span_v1(
    *, bar_series: AuthoritativeTradableBarSeriesV2
) -> OpenToOpenEvaluationSpanV1:
    bar_series.validate()
    if not bar_series.bars:
        raise OpenToOpenValidationOrchestrationV1Error("evaluation_span_requires_bar_evidence")
    for bar in bar_series.bars:
        _require_aware(bar.bar_open_at, "bar_open_at")
        _require_aware(bar.bar_close_at, "bar_close_at")
    first_open = min(bar.bar_open_at for bar in bar_series.bars).astimezone(_UTC)
    final_close = max(bar.bar_close_at for bar in bar_series.bars).astimezone(_UTC)
    evaluation_start = _ceil_to_utc_midnight(first_open)
    evaluation_end = _floor_to_utc_midnight(final_close)
    if evaluation_end <= evaluation_start:
        raise OpenToOpenValidationOrchestrationV1Error(
            "evaluation_end_must_be_after_evaluation_start"
        )
    elapsed_microseconds = (evaluation_end - evaluation_start) // timedelta(microseconds=1)
    offset = timedelta(
        microseconds=elapsed_microseconds * HOLDOUT_SPLIT_NUMERATOR // HOLDOUT_SPLIT_DENOMINATOR
    )
    raw_holdout_start = evaluation_start + offset
    holdout_start = _ceil_to_utc_midnight(raw_holdout_start)
    pre_holdout_days = _complete_days(evaluation_start, holdout_start)
    holdout_days = _complete_days(holdout_start, evaluation_end)
    reasons: list[str] = []
    if pre_holdout_days < MINIMUM_PRE_HOLDOUT_COMPLETE_DAYS:
        reasons.append("insufficient_pre_holdout_complete_days")
    if holdout_days < MINIMUM_HOLDOUT_COMPLETE_DAYS:
        reasons.append("insufficient_untouched_holdout_complete_days")
    status = STATUS_UNAVAILABLE if reasons else STATUS_AVAILABLE
    payload = {
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "raw_holdout_start": raw_holdout_start,
        "holdout_start": holdout_start,
        "pre_holdout_complete_days": pre_holdout_days,
        "holdout_complete_days": holdout_days,
        "holdout_status": status,
        "holdout_unavailable_reasons": tuple(reasons),
    }
    content_hash = _content_hash(payload)
    return OpenToOpenEvaluationSpanV1(
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        raw_holdout_start=raw_holdout_start,
        holdout_start=holdout_start,
        pre_holdout_complete_days=pre_holdout_days,
        holdout_complete_days=holdout_days,
        holdout_status=status,
        holdout_unavailable_reasons=tuple(reasons),
        content_hash=content_hash,
        span_id=_identity("open-to-open-evaluation-span-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class OpenToOpenFoldGeometryV1:
    fold_index: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime

    @property
    def boundaries(self) -> tuple[datetime, ...]:
        return (
            self.train_start,
            self.train_end,
            self.validation_start,
            self.validation_end,
            self.test_start,
            self.test_end,
        )


@dataclass(frozen=True, slots=True)
class OpenToOpenWalkForwardProtocolV1:
    initial_train_days: int
    validation_days: int
    test_days: int
    step_days: int

    def validate(self) -> None:
        for name in ("initial_train_days", "validation_days", "test_days", "step_days"):
            value: int = getattr(self, name)
            if value < 1:
                raise OpenToOpenValidationOrchestrationV1Error(f"{name}_must_be_positive")
        if self.step_days < self.test_days:
            raise OpenToOpenValidationOrchestrationV1Error(
                "step_days_must_not_be_smaller_than_test_days"
            )

    def content_hash(self) -> str:
        return _content_hash(
            {
                "initial_train_days": self.initial_train_days,
                "validation_days": self.validation_days,
                "test_days": self.test_days,
                "step_days": self.step_days,
            }
        )

    def folds(
        self, *, evaluation_start: datetime, holdout_start: datetime
    ) -> tuple[OpenToOpenFoldGeometryV1, ...]:
        self.validate()
        _require_utc_midnight(evaluation_start, "evaluation_start")
        _require_utc_midnight(holdout_start, "holdout_start")
        if holdout_start <= evaluation_start:
            raise OpenToOpenValidationOrchestrationV1Error(
                "walk_forward_requires_positive_pre_holdout_span"
            )
        geometries: list[OpenToOpenFoldGeometryV1] = []
        index = 0
        while True:
            train_end = evaluation_start + (self.initial_train_days + index * self.step_days) * _ONE_DAY
            validation_end = train_end + self.validation_days * _ONE_DAY
            test_end = validation_end + self.test_days * _ONE_DAY
            if test_end > holdout_start:
                break
            geometries.append(
                OpenToOpenFoldGeometryV1(
                    fold_index=index,
                    train_start=evaluation_start,
                    train_end=train_end,
                    validation_start=train_end,
                    validation_end=validation_end,
                    test_start=validation_end,
                    test_end=test_end,
                )
            )
            index += 1
        if not geometries:
            raise OpenToOpenValidationOrchestrationV1Error(
                "walk_forward_requires_at_least_one_complete_fold"
            )
        return tuple(geometries)


@dataclass(frozen=True, slots=True)
class OpenToOpenNeighborStepsV1:
    basis_threshold_step: Decimal
    holding_horizon_step_bars: int
    exposure_step: Decimal

    def validate(self) -> None:
        for name in ("basis_threshold_step", "exposure_step"):
            value: Decimal = getattr(self, name)
            if not value.is_finite() or value <= 0:
                raise OpenToOpenValidationOrchestrationV1Error(f"{name}_must_be_positive")
        if self.holding_horizon_step_bars < 1:
            raise OpenToOpenValidationOrchestrationV1Error(
                "holding_horizon_step_bars_must_be_positive"
            )

    def content_hash(self) -> str:
        return _content_hash(
            {
                "basis_threshold_step": self.basis_threshold_step,
                "holding_horizon_step_bars": self.holding_horizon_step_bars,
                "exposure_step": self.exposure_step,
            }
        )


def build_open_to_open_neighbor_definitions_v1(
    *,
    baseline_definition: CryptoBasisMeanReversionDefinitionV1,
    neighbor_steps: OpenToOpenNeighborStepsV1,
) -> tuple[CryptoBasisMeanReversionDefinitionV1, ...]:
    baseline_definition.validate()
    neighbor_steps.validate()
    neighbors = (
        replace(
            baseline_definition,
            basis_entry_threshold=baseline_definition.basis_entry_threshold
            - neighbor_steps.basis_threshold_step,
        ),
        replace(
            baseline_definition,
            basis_entry_threshold=baseline_definition.basis_entry_threshold
            + neighbor_steps.basis_threshold_step,
        ),
        replace(
            baseline_definition,
            holding_horizon_bars=baseline_definition.holding_horizon_bars
            - neighbor_steps.holding_horizon_step_bars,
        ),
        replace(
            baseline_definition,
            holding_horizon_bars=baseline_definition.holding_horizon_bars
            + neighbor_steps.holding_horizon_step_bars,
        ),
        replace(
            baseline_definition,
            maximum_absolute_exposure=baseline_definition.maximum_absolute_exposure
            - neighbor_steps.exposure_step,
        ),
        replace(
            baseline_definition,
            maximum_absolute_exposure=baseline_definition.maximum_absolute_exposure
            + neighbor_steps.exposure_step,
        ),
    )
    for neighbor in neighbors:
        neighbor.validate()
        if (
            neighbor.strategy_name != baseline_definition.strategy_name
            or neighbor.semantic_version != baseline_definition.semantic_version
            or neighbor.lifecycle is not baseline_definition.lifecycle
            or neighbor.required_subject_type is not baseline_definition.required_subject_type
            or neighbor.required_instrument_kind is not baseline_definition.required_instrument_kind
            or neighbor.required_feature_name != baseline_definition.required_feature_name
            or neighbor.required_feature_semantic_version
            != baseline_definition.required_feature_semantic_version
        ):
            raise OpenToOpenValidationOrchestrationV1Error("neighbor_strategy_identity_drift")
    hashes = {neighbor.content_hash() for neighbor in neighbors}
    hashes.add(baseline_definition.content_hash())
    if len(hashes) != EXPECTED_TRIAL_COUNT:
        raise OpenToOpenValidationOrchestrationV1Error("neighborhood_definitions_not_distinct")
    if len(neighbors) != EXPECTED_NEIGHBOR_COUNT:
        raise OpenToOpenValidationOrchestrationV1Error("neighborhood_must_contain_six_neighbors")
    return neighbors


def definition_parameter_tuple(
    definition: CryptoBasisMeanReversionDefinitionV1,
) -> tuple[tuple[str, str], ...]:
    values = (
        str(definition.basis_entry_threshold),
        str(definition.holding_horizon_bars),
        str(definition.maximum_absolute_exposure),
    )
    return tuple(zip(PARAMETER_NAME_ORDER, values))


@dataclass(frozen=True, slots=True)
class OpenToOpenSegmentAdmissionV1:
    segment_kind: OpenToOpenSegmentKindV1
    segment_start: datetime
    segment_end: datetime
    purge_bars: int
    embargo_bars: int
    admitted_decision_at: tuple[datetime, ...]
    purged_decision_count: int
    embargo_excluded_decision_count: int


def admit_open_to_open_segment_decisions_v1(
    *,
    evidence: SubjectAwareTradableResearchEvidenceV2,
    definition: CryptoBasisMeanReversionDefinitionV1,
    segment_kind: OpenToOpenSegmentKindV1,
    segment_start: datetime,
    segment_end: datetime,
    embargo_intervals: Sequence[tuple[datetime, datetime]] = (),
) -> OpenToOpenSegmentAdmissionV1:
    definition.validate()
    _require_utc_midnight(segment_start, "segment_start")
    _require_utc_midnight(segment_end, "segment_end")
    if segment_end <= segment_start:
        raise OpenToOpenValidationOrchestrationV1Error("segment_end_must_be_after_segment_start")
    series = _sole_basis_feature_series(evidence)
    bar_series = evidence.bar_series
    threshold = definition.basis_entry_threshold
    cap = definition.maximum_absolute_exposure
    horizon = definition.holding_horizon_bars * _ONE_BAR_INTERVAL
    admitted: list[datetime] = []
    purged = 0
    embargo_excluded = 0
    for materialization in series.materializations:
        decision_at = canonical_feature_decision_at(materialization)
        if not (segment_start <= decision_at < segment_end):
            continue
        if any(start <= decision_at < end for start, end in embargo_intervals):
            embargo_excluded += 1
            continue
        basis_value = materialization.value
        if basis_value is None or not basis_value.is_finite():
            raise OpenToOpenValidationOrchestrationV1Error("non_finite_basis_value")
        if _direction(basis_value, threshold, cap) != 0:
            entry_bar = bar_series.first_eligible_bar_after(decision_at)
            if entry_bar is not None and entry_bar.bar_open_at + horizon >= segment_end:
                purged += 1
                continue
        admitted.append(decision_at)
    return OpenToOpenSegmentAdmissionV1(
        segment_kind=segment_kind,
        segment_start=segment_start,
        segment_end=segment_end,
        purge_bars=definition.holding_horizon_bars,
        embargo_bars=definition.holding_horizon_bars,
        admitted_decision_at=tuple(admitted),
        purged_decision_count=purged,
        embargo_excluded_decision_count=embargo_excluded,
    )


def slice_authoritative_bar_series_v1(
    bar_series: AuthoritativeTradableBarSeriesV2,
    *,
    window_start: datetime,
    window_end: datetime,
) -> AuthoritativeTradableBarSeriesV2:
    _require_aware(window_start, "window_start")
    _require_aware(window_end, "window_end")
    if window_end <= window_start:
        raise OpenToOpenValidationOrchestrationV1Error("window_end_must_be_after_window_start")
    selected = tuple(
        bar
        for bar in bar_series.bars
        if window_start <= bar.bar_open_at < window_end and bar.bar_close_at <= window_end
    )
    sliced = AuthoritativeTradableBarSeriesV2(
        bar_series.dataset_version_id, bar_series.instrument_id, bar_series.interval, selected
    )
    sliced.validate()
    return sliced


def build_windowed_research_evidence_v1(
    *,
    evidence: SubjectAwareTradableResearchEvidenceV2,
    admitted_decision_at: Sequence[datetime],
    window_start: datetime,
    window_end: datetime,
) -> SubjectAwareTradableResearchEvidenceV2:
    series = _sole_basis_feature_series(evidence)
    admitted = frozenset(admitted_decision_at)
    selected = tuple(
        materialization
        for materialization in series.materializations
        if canonical_feature_decision_at(materialization) in admitted
    )
    if not selected:
        raise OpenToOpenValidationOrchestrationV1Error(
            "windowed_evidence_requires_admitted_feature_decisions"
        )
    decision_at = max(canonical_feature_decision_at(item) for item in selected)
    windowed_series = AuthoritativeFeatureSeriesV2(
        requirement=series.requirement,
        subject_type=series.subject_type,
        subject_id=series.subject_id,
        dataset_version=series.dataset_version,
        materializations=selected,
    )
    bundle = SubjectAwareResearchFeatureBundle.create(
        dataset_version_id=evidence.feature_bundle.dataset_version_id,
        subject_type=evidence.feature_bundle.subject_type,
        subject_id=evidence.feature_bundle.subject_id,
        decision_at=decision_at,
        quality_policy=evidence.feature_bundle.quality_policy,
        feature_series=(windowed_series,),
    )
    sliced_bar_series = slice_authoritative_bar_series_v1(
        evidence.bar_series, window_start=window_start, window_end=window_end
    )
    return SubjectAwareTradableResearchEvidenceV2.create(
        feature_bundle=bundle, bar_series=sliced_bar_series
    )


def run_open_to_open_segment_research_v1(
    *,
    evidence: SubjectAwareTradableResearchEvidenceV2,
    definition: CryptoBasisMeanReversionDefinitionV1,
    instrument_kind: CryptoInstrumentKind,
    cost_model: CostModel,
    cost_model_version: str,
    admission: OpenToOpenSegmentAdmissionV1,
) -> BasisMeanReversionResearchRunV1:
    windowed = build_windowed_research_evidence_v1(
        evidence=evidence,
        admitted_decision_at=admission.admitted_decision_at,
        window_start=admission.segment_start,
        window_end=admission.segment_end,
    )
    run = run_crypto_basis_mean_reversion_research(
        definition=definition,
        evidence=windowed,
        instrument_kind=instrument_kind,
        cost_model=cost_model,
        cost_model_version=cost_model_version,
    )
    for trade in run.executed_trades:
        entry = trade.entry_time.astimezone(_UTC)
        exit_time = trade.exit_time.astimezone(_UTC)
        if entry < admission.segment_start or exit_time >= admission.segment_end:
            raise OpenToOpenValidationOrchestrationV1Error(
                "executed_trade_crosses_segment_boundary"
            )
    return run


@dataclass(frozen=True, slots=True)
class OpenToOpenWalkForwardFoldEvidenceV1:
    fold_index: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    purge_bars: int
    embargo_bars: int
    train_purged_decision_count: int
    train_embargo_excluded_decision_count: int
    validation_purged_decision_count: int
    validation_embargo_excluded_decision_count: int
    test_purged_decision_count: int
    test_embargo_excluded_decision_count: int
    test_run_content_hash: str
    test_daily_series_content_hash: str
    test_daily_returns: tuple[Decimal, ...]
    test_executed_trade_count: int
    test_excluded_decision_count: int
    test_total_return: Decimal
    test_annualized_daily_sharpe: Decimal | None


@dataclass(frozen=True, slots=True)
class OpenToOpenWalkForwardEvidenceV1:
    protocol_content_hash: str
    baseline_definition_content_hash: str
    evaluation_start: datetime
    holdout_start: datetime
    fold_count: int
    folds: tuple[OpenToOpenWalkForwardFoldEvidenceV1, ...]
    content_hash: str
    evidence_id: UUID


def _fold_payload(fold: OpenToOpenWalkForwardFoldEvidenceV1) -> dict[str, Any]:
    return {
        "fold_index": fold.fold_index,
        "train_start": fold.train_start,
        "train_end": fold.train_end,
        "validation_start": fold.validation_start,
        "validation_end": fold.validation_end,
        "test_start": fold.test_start,
        "test_end": fold.test_end,
        "purge_bars": fold.purge_bars,
        "embargo_bars": fold.embargo_bars,
        "train_purged_decision_count": fold.train_purged_decision_count,
        "train_embargo_excluded_decision_count": fold.train_embargo_excluded_decision_count,
        "validation_purged_decision_count": fold.validation_purged_decision_count,
        "validation_embargo_excluded_decision_count": (
            fold.validation_embargo_excluded_decision_count
        ),
        "test_purged_decision_count": fold.test_purged_decision_count,
        "test_embargo_excluded_decision_count": fold.test_embargo_excluded_decision_count,
        "test_run_content_hash": fold.test_run_content_hash,
        "test_daily_series_content_hash": fold.test_daily_series_content_hash,
        "test_daily_returns": fold.test_daily_returns,
        "test_executed_trade_count": fold.test_executed_trade_count,
        "test_excluded_decision_count": fold.test_excluded_decision_count,
        "test_total_return": fold.test_total_return,
        "test_annualized_daily_sharpe": fold.test_annualized_daily_sharpe,
    }


def build_open_to_open_walk_forward_evidence_v1(
    *,
    protocol: OpenToOpenWalkForwardProtocolV1,
    baseline_definition: CryptoBasisMeanReversionDefinitionV1,
    evaluation_start: datetime,
    holdout_start: datetime,
    folds: Sequence[OpenToOpenWalkForwardFoldEvidenceV1],
) -> OpenToOpenWalkForwardEvidenceV1:
    if not folds:
        raise OpenToOpenValidationOrchestrationV1Error(
            "walk_forward_requires_at_least_one_complete_fold"
        )
    ordered = tuple(folds)
    for earlier, later in pairwise(ordered):
        if later.test_start < earlier.test_end:
            raise OpenToOpenValidationOrchestrationV1Error("walk_forward_test_windows_overlap")
    payload = {
        "protocol_content_hash": protocol.content_hash(),
        "baseline_definition_content_hash": baseline_definition.content_hash(),
        "evaluation_start": evaluation_start,
        "holdout_start": holdout_start,
        "fold_count": len(ordered),
        "folds": [_fold_payload(fold) for fold in ordered],
    }
    content_hash = _content_hash(payload)
    return OpenToOpenWalkForwardEvidenceV1(
        protocol_content_hash=protocol.content_hash(),
        baseline_definition_content_hash=baseline_definition.content_hash(),
        evaluation_start=evaluation_start,
        holdout_start=holdout_start,
        fold_count=len(ordered),
        folds=ordered,
        content_hash=content_hash,
        evidence_id=_identity("open-to-open-walk-forward-v1", content_hash),
    )


def build_equal_pre_holdout_blocks_v1(
    daily_returns: Sequence[Decimal],
) -> tuple[tuple[Decimal, ...], ...] | None:
    count = len(daily_returns)
    if count == 0 or count % CSCV_BLOCKS != 0:
        return None
    size = count // CSCV_BLOCKS
    return tuple(
        tuple(daily_returns[index * size : (index + 1) * size]) for index in range(CSCV_BLOCKS)
    )


@dataclass(frozen=True, slots=True)
class OpenToOpenPboOrchestrationV1:
    status: str
    unavailable_reasons: tuple[str, ...]
    block_count: int
    pre_holdout_observation_count: int
    observations_per_block: int | None
    evidence: CanonicalCscvPboEvidenceV1 | None
    pbo: Decimal | None
    content_hash: str
    evidence_id: UUID


def evaluate_open_to_open_pbo_orchestration_v1(
    *, ledger: ResearchTrialLedgerV1
) -> OpenToOpenPboOrchestrationV1:
    observation_counts = {len(trial.daily_returns) for trial in ledger.trials}
    if len(observation_counts) != 1:
        raise OpenToOpenValidationOrchestrationV1Error("pbo_trial_observation_counts_misaligned")
    observation_count = observation_counts.pop()
    blocks_per_trial = tuple(
        build_equal_pre_holdout_blocks_v1(trial.daily_returns) for trial in ledger.trials
    )
    if any(blocks is None for blocks in blocks_per_trial):
        return _pbo_orchestration(
            status=STATUS_UNAVAILABLE,
            reasons=(PBO_NOT_DIVISIBLE_REASON,),
            observation_count=observation_count,
            observations_per_block=None,
            evidence=None,
            ledger_content_hash=ledger.content_hash,
        )
    trial_blocks = [blocks for blocks in blocks_per_trial if blocks is not None]
    evidence = evaluate_canonical_cscv_pbo_v1(
        trial_blocks=trial_blocks,
        trial_identities=[trial.trial_content_hash for trial in ledger.trials],
    )
    return _pbo_orchestration(
        status=evidence.status,
        reasons=evidence.unavailable_reasons,
        observation_count=observation_count,
        observations_per_block=observation_count // CSCV_BLOCKS,
        evidence=evidence,
        ledger_content_hash=ledger.content_hash,
    )


def _pbo_orchestration(
    *,
    status: str,
    reasons: tuple[str, ...],
    observation_count: int,
    observations_per_block: int | None,
    evidence: CanonicalCscvPboEvidenceV1 | None,
    ledger_content_hash: str,
) -> OpenToOpenPboOrchestrationV1:
    payload = {
        "status": status,
        "unavailable_reasons": reasons,
        "block_count": CSCV_BLOCKS,
        "pre_holdout_observation_count": observation_count,
        "observations_per_block": observations_per_block,
        "ledger_content_hash": ledger_content_hash,
        "evidence_content_hash": None if evidence is None else evidence.content_hash,
    }
    content_hash = _content_hash(payload)
    return OpenToOpenPboOrchestrationV1(
        status=status,
        unavailable_reasons=reasons,
        block_count=CSCV_BLOCKS,
        pre_holdout_observation_count=observation_count,
        observations_per_block=observations_per_block,
        evidence=evidence,
        pbo=None if evidence is None else evidence.pbo,
        content_hash=content_hash,
        evidence_id=_identity("open-to-open-cscv-pbo-orchestration-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class OpenToOpenMultipleTestingSummaryV1:
    status: str
    unavailable_reasons: tuple[str, ...]
    ledger_content_hash: str
    trial_count: int
    datasets_or_universes_examined: int
    feature_combinations_examined: int
    primary_p_values: tuple[tuple[str, Decimal | None], ...]
    primary_unavailable_reasons: tuple[tuple[str, tuple[str, ...]], ...]
    bh_alpha: Decimal
    bh_tested: int
    bh_discoveries: tuple[str, ...]
    bh_threshold: Decimal | None
    pbo_status: str
    pbo_reference_content_hash: str
    pbo: Decimal | None
    deflated_sharpe_status: str
    deflated_sharpe_reference_content_hash: str
    deflated_sharpe: float | None
    content_hash: str
    evidence_id: UUID


def build_open_to_open_multiple_testing_summary_v1(
    *,
    ledger: ResearchTrialLedgerV1,
    primary_p_values: Mapping[str, Decimal | None],
    primary_unavailable_reasons: Mapping[str, tuple[str, ...]],
    pbo_orchestration: OpenToOpenPboOrchestrationV1,
    deflated_sharpe_evidence: DeflatedSharpeEvidenceV1,
    datasets_or_universes_examined: int = DATASETS_OR_UNIVERSES_EXAMINED,
    feature_combinations_examined: int = FEATURE_COMBINATIONS_EXAMINED,
    bh_alpha: Decimal = BH_ALPHA,
) -> OpenToOpenMultipleTestingSummaryV1:
    identities = tuple(trial.trial_content_hash for trial in ledger.trials)
    if set(primary_p_values) != set(identities):
        raise OpenToOpenValidationOrchestrationV1Error(
            "multiple_testing_requires_every_ledger_trial"
        )
    trial_count = len(identities)
    ordered_p_values = tuple(sorted(primary_p_values.items()))
    ordered_reasons = tuple(
        (identity, tuple(primary_unavailable_reasons.get(identity, ())))
        for identity in sorted(identities)
    )
    unavailable = tuple(sorted(key for key, value in ordered_p_values if value is None))
    reasons: list[str] = []
    discoveries: tuple[str, ...] = ()
    threshold: Decimal | None = None
    tested = 0
    if unavailable:
        reasons.append("primary_null_p_value_unavailable")
        reasons.extend(f"trial_p_value_unavailable:{identity}" for identity in unavailable)
    else:
        available = {key: value for key, value in ordered_p_values if value is not None}
        if len(available) != trial_count:
            raise OpenToOpenValidationOrchestrationV1Error(
                "multiple_testing_denominator_must_retain_every_trial"
            )
        result = benjamini_hochberg(available, alpha=bh_alpha)
        discoveries = result.discoveries
        threshold = result.threshold
        tested = result.tested
    status = STATUS_UNAVAILABLE if reasons else STATUS_AVAILABLE
    payload = {
        "status": status,
        "unavailable_reasons": tuple(reasons),
        "ledger_content_hash": ledger.content_hash,
        "trial_count": trial_count,
        "datasets_or_universes_examined": datasets_or_universes_examined,
        "feature_combinations_examined": feature_combinations_examined,
        "primary_p_values": [[key, value] for key, value in ordered_p_values],
        "primary_unavailable_reasons": [[key, value] for key, value in ordered_reasons],
        "bh_alpha": bh_alpha,
        "bh_tested": tested,
        "bh_discoveries": discoveries,
        "bh_threshold": threshold,
        "pbo_status": pbo_orchestration.status,
        "pbo_reference_content_hash": pbo_orchestration.content_hash,
        "pbo": pbo_orchestration.pbo,
        "deflated_sharpe_status": deflated_sharpe_evidence.status,
        "deflated_sharpe_reference_content_hash": deflated_sharpe_evidence.content_hash,
        "deflated_sharpe": deflated_sharpe_evidence.deflated_sharpe,
    }
    content_hash = _content_hash(payload)
    return OpenToOpenMultipleTestingSummaryV1(
        status=status,
        unavailable_reasons=tuple(reasons),
        ledger_content_hash=ledger.content_hash,
        trial_count=trial_count,
        datasets_or_universes_examined=datasets_or_universes_examined,
        feature_combinations_examined=feature_combinations_examined,
        primary_p_values=ordered_p_values,
        primary_unavailable_reasons=ordered_reasons,
        bh_alpha=bh_alpha,
        bh_tested=tested,
        bh_discoveries=discoveries,
        bh_threshold=threshold,
        pbo_status=pbo_orchestration.status,
        pbo_reference_content_hash=pbo_orchestration.content_hash,
        pbo=pbo_orchestration.pbo,
        deflated_sharpe_status=deflated_sharpe_evidence.status,
        deflated_sharpe_reference_content_hash=deflated_sharpe_evidence.content_hash,
        deflated_sharpe=deflated_sharpe_evidence.deflated_sharpe,
        content_hash=content_hash,
        evidence_id=_identity("open-to-open-multiple-testing-summary-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class OpenToOpenStressSummaryV1:
    synthetic_validation_evidence: bool
    reduced_liquidity_status: str
    cost_sensitivity_evidence_id: UUID
    cost_sensitivity_content_hash: str
    adverse_exit_shock_evidence_id: UUID
    adverse_exit_shock_content_hash: str
    missing_bar_stress_evidence_id: UUID
    missing_bar_stress_content_hash: str
    reduced_liquidity_evidence_id: UUID
    reduced_liquidity_content_hash: str
    content_hash: str
    evidence_id: UUID


def build_open_to_open_stress_summary_v1(
    *,
    cost_sensitivity: OpenToOpenCostSensitivityEvidenceV1,
    adverse_exit_shock: AdverseExitShockEvidenceV1,
    missing_bar_stress: MissingBarStressEvidenceV1,
    reduced_liquidity: ReducedLiquidityBlockedEvidenceV1,
) -> OpenToOpenStressSummaryV1:
    if not adverse_exit_shock.synthetic_validation_evidence:
        raise OpenToOpenValidationOrchestrationV1Error("adverse_shock_must_stay_synthetic_evidence")
    if not missing_bar_stress.synthetic_validation_evidence:
        raise OpenToOpenValidationOrchestrationV1Error("missing_bar_stress_must_stay_synthetic_evidence")
    if reduced_liquidity.status != STATUS_BLOCKED:
        raise OpenToOpenValidationOrchestrationV1Error("reduced_liquidity_must_remain_blocked")
    payload = {
        "synthetic_validation_evidence": True,
        "reduced_liquidity_status": reduced_liquidity.status,
        "cost_sensitivity_evidence_id": cost_sensitivity.evidence_id,
        "cost_sensitivity_content_hash": cost_sensitivity.content_hash,
        "adverse_exit_shock_evidence_id": adverse_exit_shock.evidence_id,
        "adverse_exit_shock_content_hash": adverse_exit_shock.content_hash,
        "missing_bar_stress_evidence_id": missing_bar_stress.evidence_id,
        "missing_bar_stress_content_hash": missing_bar_stress.content_hash,
        "reduced_liquidity_evidence_id": reduced_liquidity.evidence_id,
        "reduced_liquidity_content_hash": reduced_liquidity.content_hash,
    }
    content_hash = _content_hash(payload)
    return OpenToOpenStressSummaryV1(
        synthetic_validation_evidence=True,
        reduced_liquidity_status=reduced_liquidity.status,
        cost_sensitivity_evidence_id=cost_sensitivity.evidence_id,
        cost_sensitivity_content_hash=cost_sensitivity.content_hash,
        adverse_exit_shock_evidence_id=adverse_exit_shock.evidence_id,
        adverse_exit_shock_content_hash=adverse_exit_shock.content_hash,
        missing_bar_stress_evidence_id=missing_bar_stress.evidence_id,
        missing_bar_stress_content_hash=missing_bar_stress.content_hash,
        reduced_liquidity_evidence_id=reduced_liquidity.evidence_id,
        reduced_liquidity_content_hash=reduced_liquidity.content_hash,
        content_hash=content_hash,
        evidence_id=_identity("open-to-open-stress-summary-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class ExecutionRealismBlockedEvidenceV1:
    status: str
    reason: str
    source_run_content_hash: str
    content_hash: str
    evidence_id: UUID


def build_execution_realism_blocked_evidence_v1(
    *, run: BasisMeanReversionResearchRunV1
) -> ExecutionRealismBlockedEvidenceV1:
    payload = {
        "status": STATUS_BLOCKED,
        "reason": EXECUTION_REALISM_BLOCKED_REASON,
        "source_run_content_hash": run.content_hash,
    }
    content_hash = _content_hash(payload)
    return ExecutionRealismBlockedEvidenceV1(
        status=STATUS_BLOCKED,
        reason=EXECUTION_REALISM_BLOCKED_REASON,
        source_run_content_hash=run.content_hash,
        content_hash=content_hash,
        evidence_id=_identity("open-to-open-execution-realism-blocked-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class FixtureMarketDataQualityEvidenceV1:
    status: str
    reason: str
    dataset_version_id: UUID
    instrument_id: str
    interval: str
    bar_count: int
    evaluation_start: datetime
    evaluation_end: datetime
    content_hash: str
    evidence_id: UUID


def build_fixture_market_data_quality_evidence_v1(
    *, bar_series: AuthoritativeTradableBarSeriesV2, span: OpenToOpenEvaluationSpanV1
) -> FixtureMarketDataQualityEvidenceV1:
    scoped = slice_authoritative_bar_series_v1(
        bar_series, window_start=span.evaluation_start, window_end=span.evaluation_end
    )
    payload = {
        "status": STATUS_BLOCKED,
        "reason": DATA_QUALITY_BLOCKED_REASON,
        "dataset_version_id": bar_series.dataset_version_id,
        "instrument_id": bar_series.instrument_id,
        "interval": bar_series.interval,
        "bar_count": len(scoped.bars),
        "evaluation_start": span.evaluation_start,
        "evaluation_end": span.evaluation_end,
    }
    content_hash = _content_hash(payload)
    return FixtureMarketDataQualityEvidenceV1(
        status=STATUS_BLOCKED,
        reason=DATA_QUALITY_BLOCKED_REASON,
        dataset_version_id=bar_series.dataset_version_id,
        instrument_id=bar_series.instrument_id,
        interval=bar_series.interval,
        bar_count=len(scoped.bars),
        evaluation_start=span.evaluation_start,
        evaluation_end=span.evaluation_end,
        content_hash=content_hash,
        evidence_id=_identity("open-to-open-fixture-data-quality-blocked-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class PreHoldoutMethodologyFreezeV1:
    baseline_definition_content_hash: str
    neighbor_definition_content_hashes: tuple[str, ...]
    protocol_content_hash: str
    fold_count: int
    fold_boundaries: tuple[tuple[datetime, ...], ...]
    neighbor_steps_content_hash: str
    cost_model_content_hash: str
    cost_model_version: str
    bootstrap_seed: int
    bootstrap_resamples: int
    monte_carlo_seed: int
    monte_carlo_simulations: int
    null_seed: int
    adverse_exit_shock_magnitudes: tuple[Decimal, ...]
    missing_exit_stress_bar_open_times: tuple[datetime, ...]
    research_trial_ledger_content_hash: str
    walk_forward_evidence_content_hash: str
    parameter_stability_content_hash: str
    multiple_testing_content_hash: str
    pbo_status: str
    pbo_reference_content_hash: str
    deflated_sharpe_content_hash: str
    pre_holdout_robustness_content_hashes: tuple[tuple[str, str], ...]
    content_hash: str
    freeze_id: UUID


def build_pre_holdout_methodology_freeze_v1(
    *,
    baseline_definition: CryptoBasisMeanReversionDefinitionV1,
    neighbor_definitions: Sequence[CryptoBasisMeanReversionDefinitionV1],
    protocol: OpenToOpenWalkForwardProtocolV1,
    fold_geometries: Sequence[OpenToOpenFoldGeometryV1],
    neighbor_steps: OpenToOpenNeighborStepsV1,
    cost_model: CostModel,
    cost_model_version: str,
    bootstrap_seed: int,
    bootstrap_resamples: int,
    monte_carlo_seed: int,
    monte_carlo_simulations: int,
    null_seed: int,
    adverse_exit_shock_magnitudes: tuple[Decimal, ...],
    missing_exit_stress_bar_open_times: tuple[datetime, ...],
    ledger: ResearchTrialLedgerV1,
    walk_forward_evidence: OpenToOpenWalkForwardEvidenceV1,
    parameter_stability: ParameterStabilityEvidence,
    multiple_testing: OpenToOpenMultipleTestingSummaryV1,
    pbo_orchestration: OpenToOpenPboOrchestrationV1,
    deflated_sharpe_evidence: DeflatedSharpeEvidenceV1,
    pre_holdout_robustness_content_hashes: Sequence[tuple[str, str]],
) -> PreHoldoutMethodologyFreezeV1:
    neighbor_hashes = tuple(definition.content_hash() for definition in neighbor_definitions)
    fold_boundaries = tuple(geometry.boundaries for geometry in fold_geometries)
    robustness = tuple(sorted(pre_holdout_robustness_content_hashes))
    payload = {
        "baseline_definition_content_hash": baseline_definition.content_hash(),
        "neighbor_definition_content_hashes": neighbor_hashes,
        "protocol_content_hash": protocol.content_hash(),
        "fold_count": len(fold_boundaries),
        "fold_boundaries": fold_boundaries,
        "neighbor_steps_content_hash": neighbor_steps.content_hash(),
        "cost_model_content_hash": _cost_model_content_hash(cost_model),
        "cost_model_version": cost_model_version,
        "bootstrap_seed": bootstrap_seed,
        "bootstrap_resamples": bootstrap_resamples,
        "monte_carlo_seed": monte_carlo_seed,
        "monte_carlo_simulations": monte_carlo_simulations,
        "null_seed": null_seed,
        "adverse_exit_shock_magnitudes": adverse_exit_shock_magnitudes,
        "missing_exit_stress_bar_open_times": missing_exit_stress_bar_open_times,
        "research_trial_ledger_content_hash": ledger.content_hash,
        "walk_forward_evidence_content_hash": walk_forward_evidence.content_hash,
        "parameter_stability_content_hash": parameter_stability.identity.content_hash,
        "multiple_testing_content_hash": multiple_testing.content_hash,
        "pbo_status": pbo_orchestration.status,
        "pbo_reference_content_hash": pbo_orchestration.content_hash,
        "deflated_sharpe_content_hash": deflated_sharpe_evidence.content_hash,
        "pre_holdout_robustness_content_hashes": [[key, value] for key, value in robustness],
    }
    content_hash = _content_hash(payload)
    return PreHoldoutMethodologyFreezeV1(
        baseline_definition_content_hash=baseline_definition.content_hash(),
        neighbor_definition_content_hashes=neighbor_hashes,
        protocol_content_hash=protocol.content_hash(),
        fold_count=len(fold_boundaries),
        fold_boundaries=fold_boundaries,
        neighbor_steps_content_hash=neighbor_steps.content_hash(),
        cost_model_content_hash=_cost_model_content_hash(cost_model),
        cost_model_version=cost_model_version,
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
        monte_carlo_seed=monte_carlo_seed,
        monte_carlo_simulations=monte_carlo_simulations,
        null_seed=null_seed,
        adverse_exit_shock_magnitudes=adverse_exit_shock_magnitudes,
        missing_exit_stress_bar_open_times=missing_exit_stress_bar_open_times,
        research_trial_ledger_content_hash=ledger.content_hash,
        walk_forward_evidence_content_hash=walk_forward_evidence.content_hash,
        parameter_stability_content_hash=parameter_stability.identity.content_hash,
        multiple_testing_content_hash=multiple_testing.content_hash,
        pbo_status=pbo_orchestration.status,
        pbo_reference_content_hash=pbo_orchestration.content_hash,
        deflated_sharpe_content_hash=deflated_sharpe_evidence.content_hash,
        pre_holdout_robustness_content_hashes=robustness,
        content_hash=content_hash,
        freeze_id=_identity("pre-holdout-methodology-freeze-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class UntouchedHoldoutEvidenceV1:
    status: str
    unavailable_reasons: tuple[str, ...]
    methodology_freeze_content_hash: str
    baseline_definition_content_hash: str
    cost_model_version: str
    holdout_start: datetime
    holdout_end: datetime
    run_content_hash: str | None
    daily_series_content_hash: str | None
    daily_returns: tuple[Decimal, ...]
    trade_returns: tuple[Decimal, ...]
    executed_trade_count: int
    total_return: Decimal | None
    annualized_daily_sharpe: Decimal | None
    content_hash: str
    evidence_id: UUID


def _holdout_evidence(
    *,
    status: str,
    reasons: tuple[str, ...],
    freeze: PreHoldoutMethodologyFreezeV1,
    baseline_definition: CryptoBasisMeanReversionDefinitionV1,
    cost_model_version: str,
    holdout_start: datetime,
    holdout_end: datetime,
    run: BasisMeanReversionResearchRunV1 | None,
    series: RealizedExitDailyReturnSeriesV1 | None,
) -> UntouchedHoldoutEvidenceV1:
    daily_returns = () if series is None else series.daily_returns
    trade_returns: tuple[Decimal, ...] = () if run is None else run.trade_returns
    total_return = None if series is None else _compound(daily_returns)
    sharpe = None if series is None else _annualized_daily_sharpe(daily_returns)
    payload = {
        "status": status,
        "unavailable_reasons": reasons,
        "methodology_freeze_content_hash": freeze.content_hash,
        "baseline_definition_content_hash": baseline_definition.content_hash(),
        "cost_model_version": cost_model_version,
        "holdout_start": holdout_start,
        "holdout_end": holdout_end,
        "run_content_hash": None if run is None else run.content_hash,
        "daily_series_content_hash": None if series is None else series.content_hash,
        "daily_returns": daily_returns,
        "trade_returns": trade_returns,
        "executed_trade_count": 0 if run is None else run.executed_trade_count,
        "total_return": total_return,
        "annualized_daily_sharpe": sharpe,
    }
    content_hash = _content_hash(payload)
    return UntouchedHoldoutEvidenceV1(
        status=status,
        unavailable_reasons=reasons,
        methodology_freeze_content_hash=freeze.content_hash,
        baseline_definition_content_hash=baseline_definition.content_hash(),
        cost_model_version=cost_model_version,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        run_content_hash=None if run is None else run.content_hash,
        daily_series_content_hash=None if series is None else series.content_hash,
        daily_returns=daily_returns,
        trade_returns=trade_returns,
        executed_trade_count=0 if run is None else run.executed_trade_count,
        total_return=total_return,
        annualized_daily_sharpe=sharpe,
        content_hash=content_hash,
        evidence_id=_identity("untouched-holdout-evidence-v1", content_hash),
    )


@dataclass(frozen=True, slots=True)
class OpenToOpenProfessionalValidationRequestV1:
    evidence: SubjectAwareTradableResearchEvidenceV2
    baseline_definition: CryptoBasisMeanReversionDefinitionV1
    instrument_kind: CryptoInstrumentKind
    cost_model: CostModel
    cost_model_version: str
    protocol: OpenToOpenWalkForwardProtocolV1
    neighbor_steps: OpenToOpenNeighborStepsV1
    bootstrap_seed: int
    bootstrap_resamples: int
    monte_carlo_seed: int
    monte_carlo_simulations: int
    null_seed: int
    adverse_exit_shock_magnitudes: tuple[Decimal, ...]
    missing_exit_stress_bar_open_times: tuple[datetime, ...]
    validation_dataset_id: UUID
    evaluated_at: datetime

    def validate(self) -> None:
        self.evidence.feature_bundle.validate()
        self.evidence.bar_series.validate()
        self.baseline_definition.validate()
        if self.instrument_kind is not CryptoInstrumentKind.PERPETUAL:
            raise OpenToOpenValidationOrchestrationV1Error(
                "orchestration_requires_perpetual_instrument"
            )
        if not self.cost_model_version.strip():
            raise OpenToOpenValidationOrchestrationV1Error("cost_model_version_required")
        for name in (
            "fixed_per_turnover",
            "percentage_per_turnover",
            "spread_fraction_per_turnover",
        ):
            component: Decimal = getattr(self.cost_model, name)
            if not component.is_finite() or component < 0:
                raise OpenToOpenValidationOrchestrationV1Error(f"cost_model_{name}_invalid")
        self.protocol.validate()
        self.neighbor_steps.validate()
        if self.bootstrap_resamples < 1:
            raise OpenToOpenValidationOrchestrationV1Error("bootstrap_resamples_must_be_positive")
        if self.monte_carlo_simulations < 1:
            raise OpenToOpenValidationOrchestrationV1Error(
                "monte_carlo_simulations_must_be_positive"
            )
        if not self.adverse_exit_shock_magnitudes:
            raise OpenToOpenValidationOrchestrationV1Error("adverse_shock_magnitudes_required")
        for magnitude in self.adverse_exit_shock_magnitudes:
            if not magnitude.is_finite() or not (Decimal("0") < magnitude < Decimal("1")):
                raise OpenToOpenValidationOrchestrationV1Error(
                    "adverse_shock_magnitude_out_of_bounds"
                )
        if not self.missing_exit_stress_bar_open_times:
            raise OpenToOpenValidationOrchestrationV1Error("missing_exit_stress_timestamps_required")
        for timestamp in self.missing_exit_stress_bar_open_times:
            _require_aware(timestamp, "missing_exit_stress_bar_open_at")
        _require_aware(self.evaluated_at, "evaluated_at")

    def content_hash(self) -> str:
        return _content_hash(
            {
                "evidence_content_hash": self.evidence.content_hash,
                "baseline_definition_content_hash": self.baseline_definition.content_hash(),
                "instrument_kind": self.instrument_kind.value,
                "cost_model": _wire_cost_model(self.cost_model),
                "cost_model_version": self.cost_model_version,
                "protocol_content_hash": self.protocol.content_hash(),
                "neighbor_steps_content_hash": self.neighbor_steps.content_hash(),
                "bootstrap_seed": self.bootstrap_seed,
                "bootstrap_resamples": self.bootstrap_resamples,
                "monte_carlo_seed": self.monte_carlo_seed,
                "monte_carlo_simulations": self.monte_carlo_simulations,
                "null_seed": self.null_seed,
                "adverse_exit_shock_magnitudes": self.adverse_exit_shock_magnitudes,
                "missing_exit_stress_bar_open_times": self.missing_exit_stress_bar_open_times,
                "validation_dataset_id": self.validation_dataset_id,
                "evaluated_at": self.evaluated_at,
            }
        )


@dataclass(frozen=True, slots=True)
class OpenToOpenProfessionalValidationRunV1:
    status: str
    blocking_reasons: tuple[str, ...]
    request_content_hash: str
    strategy_id: UUID
    strategy_version_id: UUID
    dataset_version_id: UUID
    validation_dataset_id: UUID
    feature_versions: tuple[str, ...]
    span: OpenToOpenEvaluationSpanV1
    protocol: OpenToOpenWalkForwardProtocolV1
    baseline_definition: CryptoBasisMeanReversionDefinitionV1
    neighbor_definitions: tuple[CryptoBasisMeanReversionDefinitionV1, ...]
    walk_forward_evidence: OpenToOpenWalkForwardEvidenceV1
    trial_ledger: ResearchTrialLedgerV1
    baseline_trial: ResearchTrialV1
    parameter_stability: ParameterStabilityEvidence
    null_controls: tuple[CircularShiftNullControlEvidenceV1, ...]
    pbo_orchestration: OpenToOpenPboOrchestrationV1
    deflated_sharpe: DeflatedSharpeEvidenceV1
    multiple_testing: OpenToOpenMultipleTestingSummaryV1
    reconciliation: OpenToOpenReconciliationEvidenceV1
    cost_sensitivity: OpenToOpenCostSensitivityEvidenceV1
    latency_sensitivity: OpenToOpenLatencySensitivityEvidenceV1
    adverse_exit_shock: AdverseExitShockEvidenceV1
    missing_bar_stress: MissingBarStressEvidenceV1
    reduced_liquidity: ReducedLiquidityBlockedEvidenceV1
    capacity: CapacityBlockedEvidenceV1
    bootstrap: BootstrapEvidence
    monte_carlo: MonteCarloEvidence
    stress_summary: OpenToOpenStressSummaryV1
    execution_realism: ExecutionRealismBlockedEvidenceV1
    data_quality: FixtureMarketDataQualityEvidenceV1
    methodology_freeze: PreHoldoutMethodologyFreezeV1
    holdout: UntouchedHoldoutEvidenceV1
    headline_metric_source: str
    scorecard: StrategyScorecardV2
    validation_package: StrategyValidationPackage
    content_hash: str
    run_id: UUID


def _trade_level_metrics(metrics: TradeReturnMetricsV1) -> tuple[MetricObservation, ...]:
    entries: tuple[tuple[str, Decimal | None, str], ...] = (
        ("number_of_trades", Decimal(metrics.number_of_trades), "count"),
        ("hit_rate", metrics.hit_rate, "fraction"),
        ("average_trade", metrics.average_trade, "fraction"),
        ("median_trade", metrics.median_trade, "fraction"),
        ("win_loss_ratio", metrics.win_loss_ratio, "ratio"),
        ("payoff_ratio", metrics.payoff_ratio, "ratio"),
        ("profit_factor", metrics.profit_factor, "ratio"),
    )
    return tuple(
        MetricObservation(
            MetricFamily.PERFORMANCE,
            name,
            EvidenceState.MEASURED if value is not None else EvidenceState.UNAVAILABLE,
            value,
            unit,
            (DIMENSION_TRADE_LEVEL,),
        )
        for name, value, unit in entries
    )


def _daily_level_metrics(
    daily_returns: tuple[Decimal, ...], headline_source: str
) -> tuple[MetricObservation, ...]:
    dimensions = (DIMENSION_DAILY_REALIZED_EXIT, headline_source)
    observations = performance_metrics(
        daily_returns, periods_per_year=PRESENTATION_PERIODS_PER_YEAR
    )
    return tuple(
        replace(observation, dimensions=dimensions)
        for observation in observations
        if observation.name in DAILY_METRIC_NAMES
    )


def _trade_level_tail_risk_metrics(
    trade_returns: tuple[Decimal, ...],
) -> tuple[MetricObservation, ...]:
    if not trade_returns:
        return (
            MetricObservation(
                MetricFamily.RISK,
                "value_at_risk",
                EvidenceState.UNAVAILABLE,
                None,
                "fraction",
                (DIMENSION_TRADE_LEVEL,),
            ),
            MetricObservation(
                MetricFamily.RISK,
                "conditional_value_at_risk",
                EvidenceState.UNAVAILABLE,
                None,
                "fraction",
                (DIMENSION_TRADE_LEVEL,),
            ),
        )
    return tuple(
        replace(observation, dimensions=(DIMENSION_TRADE_LEVEL,))
        for observation in tail_risk_metrics(trade_returns)
    )


def _robustness_metrics(
    *,
    baseline_null: CircularShiftNullControlEvidenceV1,
    pbo_orchestration: OpenToOpenPboOrchestrationV1,
    deflated_sharpe_evidence: DeflatedSharpeEvidenceV1,
    parameter_stability: ParameterStabilityEvidence,
    holdout: UntouchedHoldoutEvidenceV1,
) -> tuple[MetricObservation, ...]:
    deflated = (
        None
        if deflated_sharpe_evidence.deflated_sharpe is None
        else Decimal(str(deflated_sharpe_evidence.deflated_sharpe))
    )
    entries: tuple[tuple[str, Decimal | None, str, tuple[str, ...]], ...] = (
        (
            "circular_shift_null_empirical_p_value",
            baseline_null.empirical_p_value,
            "probability",
            (DIMENSION_PRE_HOLDOUT_OOS,),
        ),
        ("cscv_pbo", pbo_orchestration.pbo, "probability", (DIMENSION_PRE_HOLDOUT_OOS,)),
        ("deflated_sharpe", deflated, "probability", (DIMENSION_PRE_HOLDOUT_OOS,)),
        (
            "parameter_stability_score",
            parameter_stability.stability_score,
            "fraction",
            (DIMENSION_PRE_HOLDOUT_OOS,),
        ),
        (
            "untouched_holdout_available",
            Decimal("1") if holdout.status == STATUS_AVAILABLE else Decimal("0"),
            "boolean",
            (DIMENSION_UNTOUCHED_HOLDOUT,),
        ),
    )
    return tuple(
        MetricObservation(
            MetricFamily.ROBUSTNESS,
            name,
            EvidenceState.MEASURED if value is not None else EvidenceState.UNAVAILABLE,
            value,
            unit,
            dimensions,
        )
        for name, value, unit, dimensions in entries
    )


def _headline_turnover(trades: Sequence[BasisMeanReversionTradeV1]) -> Decimal:
    turnover = Decimal("0")
    for trade in trades:
        turnover += Decimal("2") * abs(trade.exposure)
    return turnover


def run_open_to_open_professional_validation_v1(
    request: OpenToOpenProfessionalValidationRequestV1,
) -> OpenToOpenProfessionalValidationRunV1:
    request.validate()
    evidence = request.evidence
    bar_series = evidence.bar_series
    baseline_definition = request.baseline_definition
    cost_model = request.cost_model
    cost_model_version = request.cost_model_version
    dataset_version_id = evidence.feature_bundle.dataset_version_id
    dataset_version = str(dataset_version_id)
    strategy_version = baseline_definition.semantic_version

    span = derive_open_to_open_evaluation_span_v1(bar_series=bar_series)
    fold_geometries = request.protocol.folds(
        evaluation_start=span.evaluation_start, holdout_start=span.holdout_start
    )
    neighbor_definitions = build_open_to_open_neighbor_definitions_v1(
        baseline_definition=baseline_definition, neighbor_steps=request.neighbor_steps
    )
    trial_definitions = (baseline_definition, *neighbor_definitions)
    if len(trial_definitions) != EXPECTED_TRIAL_COUNT:
        raise OpenToOpenValidationOrchestrationV1Error("orchestration_requires_seven_trials")

    embargo = baseline_definition.holding_horizon_bars * _ONE_BAR_INTERVAL
    fold_evidence: list[OpenToOpenWalkForwardFoldEvidenceV1] = []
    validation_daily_by_definition: dict[str, list[Decimal]] = {
        definition.content_hash(): [] for definition in trial_definitions
    }
    test_daily_returns: list[Decimal] = []
    test_trades: list[BasisMeanReversionTradeV1] = []

    for geometry in fold_geometries:
        post_test_intervals = tuple(
            (earlier.test_end, earlier.test_end + embargo)
            for earlier in fold_geometries
            if earlier.fold_index < geometry.fold_index and earlier.test_end < geometry.train_end
        )
        train_admission = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=baseline_definition,
            segment_kind=OpenToOpenSegmentKindV1.TRAIN,
            segment_start=geometry.train_start,
            segment_end=geometry.train_end,
            embargo_intervals=post_test_intervals,
        )
        test_embargo_intervals = ((geometry.test_start, geometry.test_start + embargo),)
        test_admission = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=baseline_definition,
            segment_kind=OpenToOpenSegmentKindV1.TEST,
            segment_start=geometry.test_start,
            segment_end=geometry.test_end,
            embargo_intervals=test_embargo_intervals,
        )
        test_run = run_open_to_open_segment_research_v1(
            evidence=evidence,
            definition=baseline_definition,
            instrument_kind=request.instrument_kind,
            cost_model=cost_model,
            cost_model_version=cost_model_version,
            admission=test_admission,
        )
        test_series = build_realized_exit_daily_return_series_v1(
            run=test_run, window_start=geometry.test_start, window_end=geometry.test_end
        )
        test_daily_returns.extend(test_series.daily_returns)
        test_trades.extend(test_run.executed_trades)

        baseline_validation_admission: OpenToOpenSegmentAdmissionV1 | None = None
        for definition in trial_definitions:
            validation_admission = admit_open_to_open_segment_decisions_v1(
                evidence=evidence,
                definition=definition,
                segment_kind=OpenToOpenSegmentKindV1.VALIDATION,
                segment_start=geometry.validation_start,
                segment_end=geometry.validation_end,
            )
            if definition.content_hash() == baseline_definition.content_hash():
                baseline_validation_admission = validation_admission
            validation_run = run_open_to_open_segment_research_v1(
                evidence=evidence,
                definition=definition,
                instrument_kind=request.instrument_kind,
                cost_model=cost_model,
                cost_model_version=cost_model_version,
                admission=validation_admission,
            )
            validation_series = build_realized_exit_daily_return_series_v1(
                run=validation_run,
                window_start=geometry.validation_start,
                window_end=geometry.validation_end,
            )
            validation_daily_by_definition[definition.content_hash()].extend(
                validation_series.daily_returns
            )
        if baseline_validation_admission is None:
            raise OpenToOpenValidationOrchestrationV1Error("baseline_validation_admission_missing")

        fold_evidence.append(
            OpenToOpenWalkForwardFoldEvidenceV1(
                fold_index=geometry.fold_index,
                train_start=geometry.train_start,
                train_end=geometry.train_end,
                validation_start=geometry.validation_start,
                validation_end=geometry.validation_end,
                test_start=geometry.test_start,
                test_end=geometry.test_end,
                purge_bars=baseline_definition.holding_horizon_bars,
                embargo_bars=baseline_definition.holding_horizon_bars,
                train_purged_decision_count=train_admission.purged_decision_count,
                train_embargo_excluded_decision_count=(
                    train_admission.embargo_excluded_decision_count
                ),
                validation_purged_decision_count=(
                    baseline_validation_admission.purged_decision_count
                ),
                validation_embargo_excluded_decision_count=(
                    baseline_validation_admission.embargo_excluded_decision_count
                ),
                test_purged_decision_count=test_admission.purged_decision_count,
                test_embargo_excluded_decision_count=(
                    test_admission.embargo_excluded_decision_count
                ),
                test_run_content_hash=test_run.content_hash,
                test_daily_series_content_hash=test_series.content_hash,
                test_daily_returns=test_series.daily_returns,
                test_executed_trade_count=test_run.executed_trade_count,
                test_excluded_decision_count=test_run.excluded_count,
                test_total_return=_compound(test_series.daily_returns),
                test_annualized_daily_sharpe=_annualized_daily_sharpe(test_series.daily_returns),
            )
        )

    walk_forward_evidence = build_open_to_open_walk_forward_evidence_v1(
        protocol=request.protocol,
        baseline_definition=baseline_definition,
        evaluation_start=span.evaluation_start,
        holdout_start=span.holdout_start,
        folds=fold_evidence,
    )

    pre_holdout_runs: dict[str, BasisMeanReversionResearchRunV1] = {}
    pre_holdout_series: dict[str, RealizedExitDailyReturnSeriesV1] = {}
    trials: list[ResearchTrialV1] = []
    for definition in trial_definitions:
        admission = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=definition,
            segment_kind=OpenToOpenSegmentKindV1.FULL_PRE_HOLDOUT,
            segment_start=span.evaluation_start,
            segment_end=span.holdout_start,
        )
        run = run_open_to_open_segment_research_v1(
            evidence=evidence,
            definition=definition,
            instrument_kind=request.instrument_kind,
            cost_model=cost_model,
            cost_model_version=cost_model_version,
            admission=admission,
        )
        series = build_realized_exit_daily_return_series_v1(
            run=run, window_start=span.evaluation_start, window_end=span.holdout_start
        )
        pre_holdout_runs[definition.content_hash()] = run
        pre_holdout_series[definition.content_hash()] = series
        is_baseline = definition.content_hash() == baseline_definition.content_hash()
        trials.append(
            build_research_trial_v1(
                run=run,
                series=series,
                trial_role=(
                    ResearchTrialRoleV1.BASELINE if is_baseline else ResearchTrialRoleV1.NEIGHBOR
                ),
                disposition=(
                    ResearchTrialDispositionV1.SELECTED
                    if is_baseline
                    else ResearchTrialDispositionV1.INSPECTED
                ),
            )
        )

    observation_counts = {len(series.daily_returns) for series in pre_holdout_series.values()}
    date_sets = {series.dates for series in pre_holdout_series.values()}
    if len(observation_counts) != 1 or len(date_sets) != 1:
        raise OpenToOpenValidationOrchestrationV1Error("trial_series_not_aligned")
    ledger = build_research_trial_ledger_v1(trials)
    if len(ledger.trials) != EXPECTED_TRIAL_COUNT:
        raise OpenToOpenValidationOrchestrationV1Error("orchestration_requires_seven_trials")
    baseline_trial = next(
        trial for trial in ledger.trials if trial.trial_role is ResearchTrialRoleV1.BASELINE
    )
    baseline_run = pre_holdout_runs[baseline_definition.content_hash()]
    baseline_series = pre_holdout_series[baseline_definition.content_hash()]

    parameter_results: list[ParameterResult] = []
    for definition in trial_definitions:
        aggregated = tuple(validation_daily_by_definition[definition.content_hash()])
        parameter_results.append(
            ParameterResult(
                parameters=definition_parameter_tuple(definition),
                total_return=_compound(aggregated),
                sharpe=_annualized_daily_sharpe(aggregated),
            )
        )
    parameter_stability = evaluate_parameter_stability(
        strategy_version=strategy_version,
        dataset_version=dataset_version,
        results=tuple(parameter_results),
        selected_parameters=definition_parameter_tuple(baseline_definition),
    )

    pre_holdout_bar_series = slice_authoritative_bar_series_v1(
        bar_series, window_start=span.evaluation_start, window_end=span.holdout_start
    )

    null_controls: list[CircularShiftNullControlEvidenceV1] = []
    primary_p_values: dict[str, Decimal | None] = {}
    primary_reasons: dict[str, tuple[str, ...]] = {}
    baseline_null: CircularShiftNullControlEvidenceV1 | None = None
    for trial in ledger.trials:
        run = next(
            candidate
            for candidate in pre_holdout_runs.values()
            if candidate.content_hash == trial.source_run_content_hash
        )
        null_evidence = evaluate_circular_shift_null_control_v1(
            run=run,
            bar_series=pre_holdout_bar_series,
            base_cost_model=cost_model,
            window_start=span.evaluation_start,
            window_end=span.holdout_start,
            seed=request.null_seed,
        )
        null_controls.append(null_evidence)
        if trial.trial_content_hash == baseline_trial.trial_content_hash:
            baseline_null = null_evidence
        primary_p_values[trial.trial_content_hash] = null_evidence.empirical_p_value
        primary_reasons[trial.trial_content_hash] = null_evidence.unavailable_reasons
    if baseline_null is None:
        raise OpenToOpenValidationOrchestrationV1Error("baseline_null_control_missing")

    pbo_orchestration = evaluate_open_to_open_pbo_orchestration_v1(ledger=ledger)
    deflated_sharpe_evidence = evaluate_deflated_sharpe_evidence_v1(
        ledger=ledger, selected_trial_id=baseline_trial.trial_id
    )
    multiple_testing = build_open_to_open_multiple_testing_summary_v1(
        ledger=ledger,
        primary_p_values=primary_p_values,
        primary_unavailable_reasons=primary_reasons,
        pbo_orchestration=pbo_orchestration,
        deflated_sharpe_evidence=deflated_sharpe_evidence,
    )

    reconciliation = reconcile_open_to_open_trade_ledger_v1(
        run=baseline_run, bar_series=pre_holdout_bar_series, base_cost_model=cost_model
    )
    cost_sensitivity = evaluate_open_to_open_cost_sensitivity_v1(
        run=baseline_run, base_cost_model=cost_model
    )
    latency_sensitivity = evaluate_open_to_open_latency_sensitivity_v1(
        run=baseline_run, bar_series=pre_holdout_bar_series, base_cost_model=cost_model
    )
    adverse_exit_shock = evaluate_open_to_open_adverse_exit_shock_v1(
        run=baseline_run,
        base_cost_model=cost_model,
        shock_magnitudes=request.adverse_exit_shock_magnitudes,
    )
    missing_bar_stress = evaluate_open_to_open_missing_bar_stress_v1(
        run=baseline_run,
        bar_series=pre_holdout_bar_series,
        base_cost_model=cost_model,
        omitted_exit_bar_open_times=request.missing_exit_stress_bar_open_times,
    )
    reduced_liquidity = build_reduced_liquidity_blocked_evidence_v1(run=baseline_run)
    capacity = build_capacity_blocked_evidence_v1(run=baseline_run)
    execution_realism = build_execution_realism_blocked_evidence_v1(run=baseline_run)
    data_quality = build_fixture_market_data_quality_evidence_v1(bar_series=bar_series, span=span)
    bootstrap = evaluate_bootstrap(
        strategy_version=strategy_version,
        dataset_version=dataset_version,
        period_returns=baseline_series.daily_returns,
        seed=request.bootstrap_seed,
        resamples=request.bootstrap_resamples,
        periods_per_year=PRESENTATION_PERIODS_PER_YEAR,
    )
    monte_carlo_trade_returns = canonical_trade_returns_for_monte_carlo(baseline_run)
    if not monte_carlo_trade_returns:
        raise OpenToOpenValidationOrchestrationV1Error(
            "monte_carlo_requires_canonical_trade_returns"
        )
    monte_carlo = evaluate_monte_carlo_trade_sequence(
        strategy_version=strategy_version,
        dataset_version=dataset_version,
        trade_returns=monte_carlo_trade_returns,
        seed=request.monte_carlo_seed,
        simulations=request.monte_carlo_simulations,
    )
    stress_summary = build_open_to_open_stress_summary_v1(
        cost_sensitivity=cost_sensitivity,
        adverse_exit_shock=adverse_exit_shock,
        missing_bar_stress=missing_bar_stress,
        reduced_liquidity=reduced_liquidity,
    )

    methodology_freeze = build_pre_holdout_methodology_freeze_v1(
        baseline_definition=baseline_definition,
        neighbor_definitions=neighbor_definitions,
        protocol=request.protocol,
        fold_geometries=fold_geometries,
        neighbor_steps=request.neighbor_steps,
        cost_model=cost_model,
        cost_model_version=cost_model_version,
        bootstrap_seed=request.bootstrap_seed,
        bootstrap_resamples=request.bootstrap_resamples,
        monte_carlo_seed=request.monte_carlo_seed,
        monte_carlo_simulations=request.monte_carlo_simulations,
        null_seed=request.null_seed,
        adverse_exit_shock_magnitudes=request.adverse_exit_shock_magnitudes,
        missing_exit_stress_bar_open_times=request.missing_exit_stress_bar_open_times,
        ledger=ledger,
        walk_forward_evidence=walk_forward_evidence,
        parameter_stability=parameter_stability,
        multiple_testing=multiple_testing,
        pbo_orchestration=pbo_orchestration,
        deflated_sharpe_evidence=deflated_sharpe_evidence,
        pre_holdout_robustness_content_hashes=(
            ("golden_reconciliation", reconciliation.content_hash),
            ("cost_sensitivity", cost_sensitivity.content_hash),
            ("latency_sensitivity", latency_sensitivity.content_hash),
            ("adverse_exit_shock", adverse_exit_shock.content_hash),
            ("missing_bar_stress", missing_bar_stress.content_hash),
            ("reduced_liquidity", reduced_liquidity.content_hash),
            ("capacity", capacity.content_hash),
            ("execution_realism", execution_realism.content_hash),
            ("data_quality", data_quality.content_hash),
            ("bootstrap", bootstrap.identity.content_hash),
            ("monte_carlo", monte_carlo.identity.content_hash),
            ("stress_summary", stress_summary.content_hash),
        ),
    )

    holdout_run: BasisMeanReversionResearchRunV1 | None = None
    if span.holdout_status == STATUS_AVAILABLE:
        holdout_admission = admit_open_to_open_segment_decisions_v1(
            evidence=evidence,
            definition=baseline_definition,
            segment_kind=OpenToOpenSegmentKindV1.UNTOUCHED_HOLDOUT,
            segment_start=span.holdout_start,
            segment_end=span.evaluation_end,
        )
        holdout_run = run_open_to_open_segment_research_v1(
            evidence=evidence,
            definition=baseline_definition,
            instrument_kind=request.instrument_kind,
            cost_model=cost_model,
            cost_model_version=cost_model_version,
            admission=holdout_admission,
        )
        holdout_series = build_realized_exit_daily_return_series_v1(
            run=holdout_run,
            window_start=span.holdout_start,
            window_end=span.evaluation_end,
        )
        holdout = _holdout_evidence(
            status=STATUS_AVAILABLE,
            reasons=(),
            freeze=methodology_freeze,
            baseline_definition=baseline_definition,
            cost_model_version=cost_model_version,
            holdout_start=span.holdout_start,
            holdout_end=span.evaluation_end,
            run=holdout_run,
            series=holdout_series,
        )
        headline_source = HEADLINE_SOURCE_UNTOUCHED_HOLDOUT
        headline_daily_returns = holdout_series.daily_returns
        headline_trades: tuple[BasisMeanReversionTradeV1, ...] = holdout_run.executed_trades
    else:
        holdout = _holdout_evidence(
            status=STATUS_UNAVAILABLE,
            reasons=span.holdout_unavailable_reasons,
            freeze=methodology_freeze,
            baseline_definition=baseline_definition,
            cost_model_version=cost_model_version,
            holdout_start=span.holdout_start,
            holdout_end=span.evaluation_end,
            run=None,
            series=None,
        )
        headline_source = HEADLINE_SOURCE_PRE_HOLDOUT_OOS
        headline_daily_returns = tuple(test_daily_returns)
        headline_trades = tuple(test_trades)

    headline_trade_returns = tuple(trade.net_return for trade in headline_trades)
    metrics = (
        _daily_level_metrics(headline_daily_returns, headline_source)
        + _trade_level_metrics(trade_return_metrics_v1(headline_trade_returns))
        + _trade_level_tail_risk_metrics(headline_trade_returns)
        + _robustness_metrics(
            baseline_null=baseline_null,
            pbo_orchestration=pbo_orchestration,
            deflated_sharpe_evidence=deflated_sharpe_evidence,
            parameter_stability=parameter_stability,
            holdout=holdout,
        )
    )
    components = complexity_components(
        parameter_count=STRATEGY_PARAMETER_COUNT,
        turnover=_headline_turnover(headline_trades),
        sample_size=max(1, len(headline_daily_returns)),
    )

    evidence_ids: dict[str, UUID] = {
        "data_quality": data_quality.evidence_id,
        "oos_walk_forward": walk_forward_evidence.evidence_id,
        "golden_reconciliation": reconciliation.evidence_id,
        "execution_realism": execution_realism.evidence_id,
        "capacity": capacity.evidence_id,
        "slippage": cost_sensitivity.evidence_id,
        "latency": latency_sensitivity.evidence_id,
        "bootstrap": bootstrap.identity.artifact_id,
        "monte_carlo": monte_carlo.identity.artifact_id,
        "stress": stress_summary.evidence_id,
        "parameter_stability": parameter_stability.identity.artifact_id,
        "multiple_testing": multiple_testing.evidence_id,
    }
    evidence_hashes: dict[str, str] = {
        "data_quality": data_quality.content_hash,
        "oos_walk_forward": walk_forward_evidence.content_hash,
        "golden_reconciliation": reconciliation.content_hash,
        "execution_realism": execution_realism.content_hash,
        "capacity": capacity.content_hash,
        "slippage": cost_sensitivity.content_hash,
        "latency": latency_sensitivity.content_hash,
        "bootstrap": bootstrap.identity.content_hash,
        "monte_carlo": monte_carlo.identity.content_hash,
        "stress": stress_summary.content_hash,
        "parameter_stability": parameter_stability.identity.content_hash,
        "multiple_testing": multiple_testing.content_hash,
    }

    knowledge_cutoff = span.evaluation_end
    if knowledge_cutoff > request.evaluated_at:
        raise OpenToOpenValidationOrchestrationV1Error(
            "evaluated_at_precedes_required_knowledge_cutoff"
        )

    scorecard_research_run_id = (
        holdout_run.run_id if holdout_run is not None else walk_forward_evidence.evidence_id
    )
    headline_metric_source_content_hash = (
        holdout.content_hash if holdout.status == STATUS_AVAILABLE else walk_forward_evidence.content_hash
    )
    scorecard_evidence_manifest = dict(evidence_hashes)
    scorecard_evidence_manifest["methodology_freeze"] = methodology_freeze.content_hash
    scorecard_evidence_manifest["untouched_holdout"] = holdout.content_hash
    scorecard_evidence_manifest["headline_metric_source"] = headline_metric_source_content_hash

    scorecard = StrategyScorecardV2(
        scorecard_schema_version=CANONICAL_SCORECARD_SCHEMA_VERSION,
        strategy_id=CANONICAL_STRATEGY_ID,
        strategy_version=strategy_version,
        research_run_id=scorecard_research_run_id,
        dataset_version=dataset_version,
        feature_versions=CANONICAL_FEATURE_VERSIONS,
        cost_model_version=cost_model_version,
        evaluated_at=request.evaluated_at,
        knowledge_cutoff=knowledge_cutoff,
        status=ScorecardStatus.BLOCKED,
        limitations=REQUIRED_SCORECARD_LIMITATIONS,
        metrics=metrics,
        components=components,
        dataset_health_status=STATUS_BLOCKED,
        data_health_assessment_ids=(),
        evidence_manifest=scorecard_evidence_manifest,
    )
    scorecard.validate()
    evidence_ids["scorecard"] = scorecard.scorecard_id
    evidence_hashes["scorecard"] = scorecard.content_hash()

    validation_metadata: dict[str, Any] = {
        "research_mode": RESEARCH_MODE,
        "fixture_only": True,
        "scorecard_status": ScorecardStatus.BLOCKED.value,
        "maximum_automatic_state": STATUS_BLOCKED,
        "paper_authority": False,
        "shadow_authority": False,
        "live_authority": False,
        "holdout_status": holdout.status,
        "holdout_start": span.holdout_start.isoformat(),
        "methodology_freeze_hash": methodology_freeze.content_hash,
        "trial_count": EXPECTED_TRIAL_COUNT,
        "parameter_selection_before_holdout": True,
        "baseline_remained_selected": True,
        "capacity_status": capacity.status,
        "execution_realism_status": execution_realism.status,
    }
    validation_package = build_validation_package(
        strategy_id=CANONICAL_STRATEGY_ID,
        strategy_version_id=baseline_definition.definition_id,
        strategy_version=strategy_version,
        dataset_id=request.validation_dataset_id,
        dataset_version_id=dataset_version_id,
        dataset_version=dataset_version,
        feature_versions=CANONICAL_FEATURE_VERSIONS,
        cost_model_version=cost_model_version,
        evidence_ids=evidence_ids,
        evidence_hashes=evidence_hashes,
        limitations=REQUIRED_SCORECARD_LIMITATIONS,
        validation_metadata=validation_metadata,
        evaluated_at=request.evaluated_at,
    )
    if validation_package.promotion_status != PROMOTION_STATUS_REVIEW_REQUIRED_OR_BLOCKED:
        raise OpenToOpenValidationOrchestrationV1Error("validation_package_promotion_status_invalid")

    blocking_reasons = [
        DATA_QUALITY_BLOCKED_REASON,
        EXECUTION_REALISM_BLOCKED_REASON,
        f"CAPACITY_{capacity.status}:{capacity.reason}",
        "RESEARCH_ONLY_NO_PAPER_OR_LIVE_AUTHORITY",
    ]
    if reconciliation.status != STATUS_RECONCILED:
        blocking_reasons.append("GOLDEN_RECONCILIATION_NOT_RECONCILED")
    if holdout.status != STATUS_AVAILABLE:
        blocking_reasons.extend(
            f"UNTOUCHED_HOLDOUT_UNAVAILABLE:{reason}" for reason in holdout.unavailable_reasons
        )
    if multiple_testing.status != STATUS_AVAILABLE:
        blocking_reasons.append("MULTIPLE_TESTING_UNAVAILABLE")
    if pbo_orchestration.status != STATUS_AVAILABLE:
        blocking_reasons.append("CSCV_PBO_UNAVAILABLE")
    if deflated_sharpe_evidence.status != STATUS_AVAILABLE:
        blocking_reasons.append("DEFLATED_SHARPE_UNAVAILABLE")

    request_content_hash = request.content_hash()
    payload = {
        "status": STATUS_BLOCKED,
        "blocking_reasons": tuple(blocking_reasons),
        "request_content_hash": request_content_hash,
        "strategy_id": CANONICAL_STRATEGY_ID,
        "strategy_version_id": baseline_definition.definition_id,
        "dataset_version_id": dataset_version_id,
        "validation_dataset_id": request.validation_dataset_id,
        "feature_versions": CANONICAL_FEATURE_VERSIONS,
        "span_content_hash": span.content_hash,
        "walk_forward_evidence_content_hash": walk_forward_evidence.content_hash,
        "trial_ledger_content_hash": ledger.content_hash,
        "baseline_trial_content_hash": baseline_trial.trial_content_hash,
        "parameter_stability_content_hash": parameter_stability.identity.content_hash,
        "null_control_content_hashes": [item.content_hash for item in null_controls],
        "pbo_reference_content_hash": pbo_orchestration.content_hash,
        "deflated_sharpe_content_hash": deflated_sharpe_evidence.content_hash,
        "multiple_testing_content_hash": multiple_testing.content_hash,
        "methodology_freeze_content_hash": methodology_freeze.content_hash,
        "holdout_content_hash": holdout.content_hash,
        "headline_metric_source": headline_source,
        "scorecard_content_hash": scorecard.content_hash(),
        "validation_package_content_hash": validation_package.identity.content_hash,
        "evidence_hashes": evidence_hashes,
    }
    content_hash = _content_hash(payload)
    return OpenToOpenProfessionalValidationRunV1(
        status=STATUS_BLOCKED,
        blocking_reasons=tuple(blocking_reasons),
        request_content_hash=request_content_hash,
        strategy_id=CANONICAL_STRATEGY_ID,
        strategy_version_id=baseline_definition.definition_id,
        dataset_version_id=dataset_version_id,
        validation_dataset_id=request.validation_dataset_id,
        feature_versions=CANONICAL_FEATURE_VERSIONS,
        span=span,
        protocol=request.protocol,
        baseline_definition=baseline_definition,
        neighbor_definitions=neighbor_definitions,
        walk_forward_evidence=walk_forward_evidence,
        trial_ledger=ledger,
        baseline_trial=baseline_trial,
        parameter_stability=parameter_stability,
        null_controls=tuple(null_controls),
        pbo_orchestration=pbo_orchestration,
        deflated_sharpe=deflated_sharpe_evidence,
        multiple_testing=multiple_testing,
        reconciliation=reconciliation,
        cost_sensitivity=cost_sensitivity,
        latency_sensitivity=latency_sensitivity,
        adverse_exit_shock=adverse_exit_shock,
        missing_bar_stress=missing_bar_stress,
        reduced_liquidity=reduced_liquidity,
        capacity=capacity,
        bootstrap=bootstrap,
        monte_carlo=monte_carlo,
        stress_summary=stress_summary,
        execution_realism=execution_realism,
        data_quality=data_quality,
        methodology_freeze=methodology_freeze,
        holdout=holdout,
        headline_metric_source=headline_source,
        scorecard=scorecard,
        validation_package=validation_package,
        content_hash=content_hash,
        run_id=_identity("open-to-open-professional-validation-run-v1", content_hash),
    )
