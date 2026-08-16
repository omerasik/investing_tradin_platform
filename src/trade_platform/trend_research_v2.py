"""Complete research-only Trend V2 orchestration over existing authorities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, TypeAlias
from uuid import NAMESPACE_URL, UUID, uuid5

from .cross_engine import (
    GoldenExecutionScenario,
    GoldenMarketBar,
    IndependentBarEngineReport,
    RealisticGoldenRunReport,
    reconcile_independent_bar_engine,
    run_realistic_golden_vector_event_reconciliation,
)
from .data_health import DataHealthAssessment
from .feature_authority import FeatureDefinitionVersion, FeatureMaterialization
from .persistence import PostgresDatabase
from .postgres_quant_validation import PostgresQuantValidationStore
from .quant_validation import (
    ArtifactIdentity,
    BootstrapEvidence,
    CapacityEvidence,
    LatencySensitivityEvidence,
    MonteCarloEvidence,
    MultipleTestingEvidence,
    ParameterResult,
    ParameterStabilityEvidence,
    SlippageSensitivityEvidence,
    StrategyValidationPackage,
    StressEvidence,
    _identity,
    _wire,
    build_validation_package,
    evaluate_bootstrap,
    evaluate_capacity,
    evaluate_latency_sensitivity,
    evaluate_monte_carlo_trade_sequence,
    evaluate_multiple_testing,
    evaluate_parameter_stability,
    evaluate_slippage_sensitivity,
    evaluate_stress,
)
from .research import BacktestResult, CostModel, WalkForwardProtocol, run_vectorized_backtest
from .research_validation import WalkForwardResult, run_purged_walk_forward
from .strategy_scorecard_v2 import (
    EvidenceState,
    MetricFamily,
    MetricObservation,
    PostgresStrategyScorecardStore,
    ScorecardStatus,
    StrategyScorecardV2,
    complexity_components,
    performance_metrics,
    tail_risk_metrics,
    trade_activity_metrics,
)
from .strategy_validation import purged_walk_forward_splits
from .trend_strategy_v2 import (
    AuthoritativeFeatureSeries,
    TrendSignalSeries,
    TrendStrategyDefinitionV2,
    TrendStrategyKind,
    breakout_signals,
    multi_horizon_trend_signals,
    time_series_momentum_signals,
    volatility_scaled_trend_exposure,
)


class TrendResearchV2Error(ValueError):
    pass


class TrendResearchStatus(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class FeatureAuthorityReader(Protocol):
    def definition(self, feature_id: UUID) -> FeatureDefinitionVersion: ...

    def latest_as_of(
        self, feature_id: UUID, instrument_id: str, dataset_version: str, decision_at: datetime
    ) -> tuple[FeatureMaterialization, ...]: ...


@dataclass(frozen=True, slots=True)
class TrendResearchBar:
    occurred_at: datetime
    close: Decimal
    bid: Decimal
    ask: Decimal
    volume: Decimal

    def validate(self) -> None:
        if (
            self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
            or self.close <= 0
            or self.bid <= 0
            or self.ask < self.bid
            or self.volume <= 0
        ):
            raise TrendResearchV2Error("invalid_trend_research_bar")


@dataclass(frozen=True, slots=True)
class SealedTrendDataset:
    dataset_id: UUID
    dataset_version_id: UUID
    version: str
    dataset_type: str
    instrument_id: str
    asset_class: str
    sealed: bool
    bars: tuple[TrendResearchBar, ...]

    def validate(self) -> None:
        if (
            not self.version.strip()
            or not self.dataset_type.strip()
            or not self.instrument_id.strip()
            or not self.asset_class.strip()
            or not self.sealed
            or len(self.bars) < 2
        ):
            raise TrendResearchV2Error("invalid_or_unsealed_trend_dataset")
        previous: datetime | None = None
        for bar in self.bars:
            bar.validate()
            if previous is not None and bar.occurred_at <= previous:
                raise TrendResearchV2Error("trend_dataset_not_chronological")
            previous = bar.occurred_at


@dataclass(frozen=True, slots=True)
class TrendResearchRequest:
    definition: TrendStrategyDefinitionV2
    dataset: SealedTrendDataset
    cost_model_version: str
    costs: CostModel
    decision_at: datetime
    evaluated_at: datetime
    data_health: DataHealthAssessment
    execution_scenario: GoldenExecutionScenario
    walk_forward_protocol: WalkForwardProtocol
    research_trial_count: int
    bootstrap_resamples: int = 32


@dataclass(frozen=True, slots=True)
class BoundValidationEvidence:
    identity: ArtifactIdentity
    evidence_type: str
    strategy_version: str
    dataset_version: str
    feature_versions: tuple[str, ...]
    cost_model_version: str
    passed: bool
    source_hashes: tuple[str, ...]
    limitations: tuple[str, ...]


QuantEvidence: TypeAlias = (
    CapacityEvidence
    | SlippageSensitivityEvidence
    | LatencySensitivityEvidence
    | BootstrapEvidence
    | MonteCarloEvidence
    | StressEvidence
    | ParameterStabilityEvidence
    | MultipleTestingEvidence
)


@dataclass(frozen=True, slots=True)
class TrendResearchRun:
    run_id: UUID
    content_hash: str
    definition: TrendStrategyDefinitionV2
    dataset: SealedTrendDataset
    feature_materialization_hashes: tuple[str, ...]
    signal_series: TrendSignalSeries
    vector_result: BacktestResult
    independent_result: IndependentBarEngineReport
    event_result: RealisticGoldenRunReport
    walk_forward_results: tuple[WalkForwardResult, ...]
    research_trial_count: int
    quant_evidence: tuple[QuantEvidence, ...]
    validation_evidence: tuple[BoundValidationEvidence, ...]
    scorecard: StrategyScorecardV2
    validation_package: StrategyValidationPackage
    status: TrendResearchStatus
    limitations: tuple[str, ...]
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class TrendResearchOutcome:
    status: TrendResearchStatus
    reasons: tuple[str, ...]
    run: TrendResearchRun | None


class InMemoryFeatureAuthority:
    """Deterministic test adapter with the same read boundary as PostgreSQL."""

    def __init__(
        self,
        definitions: tuple[FeatureDefinitionVersion, ...],
        materializations: tuple[FeatureMaterialization, ...],
    ) -> None:
        self._definitions = {item.feature_id: item for item in definitions}
        self._materializations = materializations

    def definition(self, feature_id: UUID) -> FeatureDefinitionVersion:
        try:
            return self._definitions[feature_id]
        except KeyError as error:
            raise TrendResearchV2Error("feature_definition_not_found") from error

    def latest_as_of(
        self, feature_id: UUID, instrument_id: str, dataset_version: str, decision_at: datetime
    ) -> tuple[FeatureMaterialization, ...]:
        candidates = tuple(
            item
            for item in self._materializations
            if item.feature_id == feature_id
            and item.instrument_id == instrument_id
            and item.dataset_version == dataset_version
            and item.event_at <= decision_at
            and item.effective_at <= decision_at
            and item.knowledge_at <= decision_at
            and item.computed_at <= decision_at
        )
        by_event: dict[datetime, FeatureMaterialization] = {}
        for item in sorted(candidates, key=lambda value: (value.event_at, value.knowledge_at, value.computed_at)):
            by_event[item.event_at] = item
        return tuple(by_event[key] for key in sorted(by_event))


def _hash(value: object) -> str:
    canonical = json.dumps(_wire(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _bound_evidence(
    *,
    evidence_type: str,
    request: TrendResearchRequest,
    passed: bool,
    source_hashes: tuple[str, ...],
    limitations: tuple[str, ...],
) -> BoundValidationEvidence:
    payload = {
        "evidence_type": evidence_type,
        "strategy_version": request.definition.strategy_version,
        "dataset_version": request.dataset.version,
        "feature_versions": request.definition.feature_versions,
        "cost_model_version": request.cost_model_version,
        "passed": passed,
        "source_hashes": source_hashes,
        "limitations": limitations,
    }
    return BoundValidationEvidence(
        _identity(evidence_type, "trend-bound-v2", payload),
        evidence_type,
        request.definition.strategy_version,
        request.dataset.version,
        request.definition.feature_versions,
        request.cost_model_version,
        passed,
        source_hashes,
        limitations,
    )


class TrendResearchOrchestratorV2:
    """Launch one immutable, feature-bound research chain with no execution authority."""

    def __init__(self, features: FeatureAuthorityReader) -> None:
        self._features = features
        self._runs: dict[UUID, TrendResearchRun] = {}

    def launch(self, request: TrendResearchRequest) -> TrendResearchOutcome:
        self._validate_request(request)
        feature_series = self._resolve_features(request)
        expected_times = tuple(item.occurred_at for item in request.dataset.bars)
        for series in feature_series.values():
            observed_times = tuple(item.event_at for item in series.materializations)
            if observed_times != expected_times:
                return TrendResearchOutcome(
                    TrendResearchStatus.UNAVAILABLE,
                    ("required_feature_history_incomplete",),
                    None,
                )
        signals = self._generate_signals(request.definition, feature_series)
        if not signals.eligible:
            reasons = tuple(
                sorted(
                    {
                        item.reason or "required_feature_unavailable"
                        for item in signals.observations
                        if item.exposure is None
                    }
                )
            )
            return TrendResearchOutcome(TrendResearchStatus.UNAVAILABLE, reasons, None)
        exposures = signals.require_available()
        closes = tuple(item.close for item in request.dataset.bars)
        vector = run_vectorized_backtest(list(closes), list(exposures), request.costs)
        independent = reconcile_independent_bar_engine(
            vector,
            closes,
            exposures,
            fixed_per_turnover=request.costs.fixed_per_turnover,
            percentage_per_turnover=request.costs.percentage_per_turnover,
            spread_fraction_per_turnover=request.costs.spread_fraction_per_turnover,
        )
        event = self._event_reconciliation(request, exposures)
        walk_forward = self._walk_forward(request, closes, exposures)
        quant = self._quant_validation(request, closes, exposures, vector, walk_forward)
        run = self._assemble_run(
            request, feature_series, signals, vector, independent, event, walk_forward, quant
        )
        existing = self._runs.get(run.run_id)
        if existing is not None:
            if existing.content_hash != run.content_hash:
                raise TrendResearchV2Error("trend_research_replay_conflict")
            return TrendResearchOutcome(existing.status, (), existing)
        self._runs[run.run_id] = run
        return TrendResearchOutcome(run.status, (), run)

    @staticmethod
    def _validate_request(request: TrendResearchRequest) -> None:
        request.definition.validate()
        request.dataset.validate()
        request.walk_forward_protocol.validate()
        if request.decision_at.tzinfo is None or request.decision_at.utcoffset() is None:
            raise TrendResearchV2Error("trend_research_decision_time_must_be_aware")
        if request.evaluated_at.tzinfo is None or request.evaluated_at.utcoffset() is None:
            raise TrendResearchV2Error("trend_research_evaluation_time_must_be_aware")
        if request.evaluated_at < request.decision_at:
            raise TrendResearchV2Error("trend_research_evaluated_before_decision")
        if request.definition.required_dataset_type != request.dataset.dataset_type:
            raise TrendResearchV2Error("strategy_dataset_type_mismatch")
        if request.cost_model_version != request.definition.cost_model_requirement:
            raise TrendResearchV2Error("strategy_cost_model_mismatch")
        if request.research_trial_count < 1 or request.bootstrap_resamples < 2:
            raise TrendResearchV2Error("invalid_research_trial_count")
        if any(item.occurred_at > request.decision_at for item in request.dataset.bars):
            raise TrendResearchV2Error("future_dataset_observation")
        health = request.data_health
        if health.dataset_version_id != request.dataset.dataset_version_id:
            raise TrendResearchV2Error("data_health_dataset_mismatch")
        if health.evaluated_at > request.decision_at:
            raise TrendResearchV2Error("future_data_health_assessment")
        if health.blocking:
            raise TrendResearchV2Error("research_blocked_by_data_health")
        costs = (
            request.costs.fixed_per_turnover,
            request.costs.percentage_per_turnover,
            request.costs.spread_fraction_per_turnover,
        )
        if any(not item.is_finite() or item < 0 for item in costs):
            raise TrendResearchV2Error("invalid_research_cost_model")

    def _resolve_features(
        self, request: TrendResearchRequest
    ) -> dict[str, AuthoritativeFeatureSeries]:
        result: dict[str, AuthoritativeFeatureSeries] = {}
        for requirement in request.definition.required_features:
            definition = self._features.definition(requirement.feature_id)
            if (
                definition.name != requirement.name
                or definition.semantic_version != requirement.semantic_version
            ):
                raise TrendResearchV2Error("strategy_feature_version_mismatch")
            if request.dataset.dataset_type not in definition.required_dataset_types:
                raise TrendResearchV2Error("feature_dataset_type_mismatch")
            materializations = self._features.latest_as_of(
                requirement.feature_id,
                request.dataset.instrument_id,
                request.dataset.version,
                request.decision_at,
            )
            for item in materializations:
                if item.dataset_version != request.dataset.version:
                    raise TrendResearchV2Error("feature_dataset_version_mismatch")
                if (
                    item.event_at > request.decision_at
                    or item.effective_at > request.decision_at
                    or item.knowledge_at > request.decision_at
                    or item.computed_at > request.decision_at
                ):
                    raise TrendResearchV2Error("future_feature_knowledge")
            result[requirement.name] = AuthoritativeFeatureSeries(
                requirement, request.dataset.version, materializations
            )
        return result

    @staticmethod
    def _generate_signals(
        definition: TrendStrategyDefinitionV2,
        series: Mapping[str, AuthoritativeFeatureSeries],
    ) -> TrendSignalSeries:
        parameters = dict(definition.parameters)
        if definition.kind is TrendStrategyKind.TIME_SERIES_MOMENTUM:
            return time_series_momentum_signals(
                definition,
                series["multi_horizon_momentum"],
                threshold=Decimal(parameters.get("threshold", "0")),
            )
        if definition.kind is TrendStrategyKind.BREAKOUT:
            return breakout_signals(definition, series["breakout"])
        if definition.kind is TrendStrategyKind.MULTI_HORIZON_TREND:
            return multi_horizon_trend_signals(
                definition, series["multi_horizon_momentum"]
            )
        if definition.kind is TrendStrategyKind.VOLATILITY_SCALED_TREND:
            return volatility_scaled_trend_exposure(
                definition,
                series["multi_horizon_momentum"],
                series["realized_volatility"],
                target_volatility=Decimal(parameters["target_volatility"]),
            )
        raise TrendResearchV2Error("unsupported_trend_strategy_kind")

    @staticmethod
    def _event_reconciliation(
        request: TrendResearchRequest, exposures: tuple[Decimal, ...]
    ) -> RealisticGoldenRunReport:
        bars = tuple(
            GoldenMarketBar(
                item.occurred_at,
                item.close,
                item.bid,
                item.ask,
                item.volume,
                request.dataset.instrument_id,
            )
            for item in request.dataset.bars
        )
        report = run_realistic_golden_vector_event_reconciliation(
            dataset_version=request.dataset.version,
            strategy_version=request.definition.strategy_version,
            instrument_id=request.dataset.instrument_id,
            bars=bars,
            signals=exposures,
            scenario=request.execution_scenario,
        )
        artifact_id = uuid5(NAMESPACE_URL, f"trend-golden-v2:{report.payload_digest}")
        return replace(report, artifact_id=artifact_id, created_at=request.evaluated_at)

    @staticmethod
    def _walk_forward(
        request: TrendResearchRequest,
        closes: tuple[Decimal, ...],
        exposures: tuple[Decimal, ...],
    ) -> tuple[WalkForwardResult, ...]:
        protocol = request.walk_forward_protocol
        splits = purged_walk_forward_splits(
            len(closes),
            train_size=protocol.train_size,
            validation_size=protocol.validation_size,
            test_size=protocol.test_size,
            step=protocol.step,
            purge=protocol.purge,
            embargo=protocol.embargo,
        )

        def immutable_signal_prefix(values: list[Decimal]) -> list[Decimal]:
            if len(values) > len(exposures):
                raise TrendResearchV2Error("signal_prefix_outside_immutable_series")
            return list(exposures[: len(values)])

        return tuple(
            run_purged_walk_forward(list(closes), immutable_signal_prefix, splits, request.costs)
        )

    @staticmethod
    def _quant_validation(
        request: TrendResearchRequest,
        closes: tuple[Decimal, ...],
        exposures: tuple[Decimal, ...],
        vector: BacktestResult,
        walk_forward: tuple[WalkForwardResult, ...],
    ) -> tuple[QuantEvidence, ...]:
        strategy = request.definition.strategy_version
        dataset = request.dataset.version
        average_volume = sum((item.volume for item in request.dataset.bars), Decimal("0")) / Decimal(
            len(request.dataset.bars)
        )
        capacity = evaluate_capacity(
            strategy_version=strategy,
            dataset_version=dataset,
            instrument_or_universe=request.dataset.instrument_id,
            closes=closes,
            signals=exposures,
            base_costs=request.costs,
            capital_levels=(Decimal("1000"), Decimal("10000")),
            average_daily_volume=average_volume,
            order_participation_limit=request.execution_scenario.maximum_participation,
            spread_assumption=request.costs.spread_fraction_per_turnover,
            spread_deterioration_assumption=Decimal("0.0001"),
            impact_coefficient=request.execution_scenario.impact_coefficient,
            market_impact_model_version="synthetic-square-root-v1",
            liquidity_assumptions="Synthetic OHLCV/ADV engineering fixture.",
        )
        slippage = evaluate_slippage_sensitivity(
            strategy_version=strategy,
            dataset_version=dataset,
            cost_model_version=request.cost_model_version,
            closes=closes,
            signals=exposures,
            costs=request.costs,
            minimum_economic_return=Decimal("-1"),
        )
        timestamps = tuple(item.occurred_at for item in request.dataset.bars)
        frequency = timestamps[1] - timestamps[0]
        latency = evaluate_latency_sensitivity(
            strategy_version=strategy,
            dataset_version=dataset,
            closes=closes,
            signals=exposures,
            timestamps=timestamps,
            costs=request.costs,
            data_frequency=frequency,
            latency_levels=(timedelta(0), frequency),
            maximum_return_degradation=Decimal("1"),
        )
        bootstrap = evaluate_bootstrap(
            strategy_version=strategy,
            dataset_version=dataset,
            period_returns=vector.period_returns,
            seed=202,
            resamples=request.bootstrap_resamples,
        )
        monte_carlo = evaluate_monte_carlo_trade_sequence(
            strategy_version=strategy,
            dataset_version=dataset,
            trade_returns=vector.period_returns,
            seed=202,
            simulations=request.bootstrap_resamples,
        )
        stress = evaluate_stress(
            strategy_version=strategy,
            dataset_version=dataset,
            closes=closes,
            signals=exposures,
            costs=request.costs,
            scenarios=(
                ("sharp_fixture_selloff", Decimal("-0.10"), Decimal("2")),
                ("cost_deterioration", Decimal("0"), Decimal("3")),
            ),
            minimum_return=Decimal("-1"),
        )
        first_test_start = walk_forward[0].split.test_start
        selection_closes = closes[:first_test_start]
        selection_signals = exposures[:first_test_start]
        selected_parameters = request.definition.parameters + (("exposure_multiplier", "1.0"),)
        parameter_results: list[ParameterResult] = []
        for label, multiplier in (
            ("0.9", Decimal("0.9")),
            ("1.0", Decimal("1")),
            ("1.1", Decimal("1.1")),
        ):
            perturbed = tuple(
                min(request.definition.maximum_research_exposure, item * multiplier)
                for item in selection_signals
            )
            result = run_vectorized_backtest(
                list(selection_closes), list(perturbed), request.costs
            )
            parameter_results.append(
                ParameterResult(
                    request.definition.parameters + (("exposure_multiplier", label),),
                    result.total_return,
                    result.sharpe,
                )
            )
        stability = evaluate_parameter_stability(
            strategy_version=strategy,
            dataset_version=dataset,
            results=tuple(parameter_results),
            selected_parameters=selected_parameters,
            minimum_stability=Decimal("0.5"),
        )
        holdout_passes = tuple(item.result.total_return > Decimal("-1") for item in walk_forward)
        empirical_p = max(
            Decimal("0.001"),
            bootstrap.probability_negative_outcome / Decimal(request.bootstrap_resamples),
        )
        multiple_testing = evaluate_multiple_testing(
            strategy_version=strategy,
            dataset_version=dataset,
            p_values={strategy: empirical_p},
            observed_sharpe=vector.sharpe or Decimal("0"),
            holdout_passed=all(holdout_passes),
            rejected_experiments=(),
            selected_experiments=(strategy,),
            parameter_combinations_tested=request.research_trial_count,
            datasets_or_universes_tested=1,
            feature_combinations_tested=1,
            oos_results=holdout_passes,
        )
        return (
            capacity,
            slippage,
            latency,
            bootstrap,
            monte_carlo,
            stress,
            stability,
            multiple_testing,
        )

    @staticmethod
    def _assemble_run(
        request: TrendResearchRequest,
        feature_series: Mapping[str, AuthoritativeFeatureSeries],
        signals: TrendSignalSeries,
        vector: BacktestResult,
        independent: IndependentBarEngineReport,
        event: RealisticGoldenRunReport,
        walk_forward: tuple[WalkForwardResult, ...],
        quant: tuple[QuantEvidence, ...],
    ) -> TrendResearchRun:
        materialization_hashes = tuple(
            sorted(
                item.content_hash
                for series in feature_series.values()
                for item in series.materializations
            )
        )
        walk_hash = _hash(walk_forward)
        data_quality = _bound_evidence(
            evidence_type="data_quality",
            request=request,
            passed=not request.data_health.blocking,
            source_hashes=(request.data_health.content_hash,),
            limitations=("Synthetic fixture health assessment; not a licensed-feed SLA.",),
        )
        oos = _bound_evidence(
            evidence_type="oos_walk_forward",
            request=request,
            passed=bool(walk_forward),
            source_hashes=(walk_hash,),
            limitations=("Parameters were declared before the untouched holdout.",),
        )
        golden = _bound_evidence(
            evidence_type="golden_reconciliation",
            request=request,
            passed=not event.unexplained_differences,
            source_hashes=(event.payload_digest,),
            limitations=("Synthetic cross-engine golden evidence.",),
        )
        execution = _bound_evidence(
            evidence_type="execution_realism",
            request=request,
            passed=event.reconciled,
            source_hashes=(event.payload_digest,),
            limitations=("Spread, fees, latency, participation and impact are synthetic assumptions.",),
        )
        quant_names = (
            "capacity",
            "slippage",
            "latency",
            "bootstrap",
            "monte_carlo",
            "stress",
            "parameter_stability",
            "multiple_testing",
        )
        bound: list[BoundValidationEvidence] = [data_quality, oos, golden, execution]
        for name, item in zip(quant_names, quant, strict=True):
            if isinstance(item, CapacityEvidence):
                passed = all(level.passed for level in item.levels)
            elif isinstance(item, (StressEvidence, LatencySensitivityEvidence)):
                passed = all(scenario.passed for scenario in item.scenarios)
            elif isinstance(
                item,
                (SlippageSensitivityEvidence, ParameterStabilityEvidence, MultipleTestingEvidence),
            ):
                passed = item.passed
            else:
                passed = True
            bound.append(
                _bound_evidence(
                    evidence_type=name,
                    request=request,
                    passed=passed,
                    source_hashes=(item.identity.content_hash,),
                    limitations=("Synthetic engineering validation only.",),
                )
            )
        evidence_manifest = {
            item.evidence_type: item.identity.content_hash for item in bound
        }
        metrics = list(performance_metrics(vector.period_returns))
        average_exposure = sum(signals.require_available(), Decimal("0")) / Decimal(
            len(signals.observations)
        )
        metrics.extend(
            trade_activity_metrics(
                turnover=vector.turnover,
                exposure=average_exposure,
                average_holding_period=None,
            )
        )
        metrics.extend(tail_risk_metrics(vector.period_returns))
        multiple = next(item for item in quant if isinstance(item, MultipleTestingEvidence))
        bootstrap = next(item for item in quant if isinstance(item, BootstrapEvidence))
        capacity = next(item for item in quant if isinstance(item, CapacityEvidence))
        metrics.extend(
            (
                MetricObservation(
                    MetricFamily.ROBUSTNESS,
                    "bootstrap_negative_outcome_probability",
                    EvidenceState.MEASURED,
                    bootstrap.probability_negative_outcome,
                    "fraction",
                ),
                MetricObservation(
                    MetricFamily.ROBUSTNESS,
                    "backtest_overfitting_probability",
                    EvidenceState.MEASURED,
                    multiple.backtest_overfitting_probability,
                    "fraction",
                ),
                MetricObservation(
                    MetricFamily.EXECUTION,
                    "synthetic_capacity_level",
                    EvidenceState.ASSUMED,
                    capacity.levels[-1].estimated_maximum_usable_capital,
                    "currency",
                ),
                MetricObservation(
                    MetricFamily.EXECUTION,
                    "real_capacity",
                    EvidenceState.UNAVAILABLE,
                    None,
                    "currency",
                ),
                MetricObservation(
                    MetricFamily.EXECUTION,
                    "real_slippage",
                    EvidenceState.UNAVAILABLE,
                    None,
                    "fraction",
                ),
                MetricObservation(
                    MetricFamily.EXECUTION,
                    "real_market_impact",
                    EvidenceState.UNAVAILABLE,
                    None,
                    "fraction",
                ),
                MetricObservation(
                    MetricFamily.DATA_QUALITY,
                    "feature_coverage",
                    EvidenceState.MEASURED,
                    Decimal("1"),
                    "fraction",
                ),
                MetricObservation(
                    MetricFamily.DATA_QUALITY,
                    "blocking_findings",
                    EvidenceState.MEASURED,
                    Decimal("0"),
                    "count",
                ),
                MetricObservation(
                    MetricFamily.ROBUSTNESS,
                    "regime_performance",
                    EvidenceState.UNAVAILABLE,
                    None,
                    "fraction",
                ),
                MetricObservation(
                    MetricFamily.ROBUSTNESS,
                    "live_consistency",
                    EvidenceState.UNAVAILABLE,
                    None,
                    "fraction",
                ),
            )
        )
        semantic_inputs = {
            "strategy_version_id": request.definition.strategy_version_id,
            "dataset_version_id": request.dataset.dataset_version_id,
            "feature_hashes": materialization_hashes,
            "signals": signals,
            "cost_model_version": request.cost_model_version,
            "costs": request.costs,
            "execution_digest": event.payload_digest,
            "walk_forward": walk_hash,
            "research_trial_count": request.research_trial_count,
        }
        content_hash = _hash(semantic_inputs)
        run_id = uuid5(NAMESPACE_URL, f"trend-research-v2:{content_hash}")
        limitations = (
            "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",
            "No authorized real dataset, empirical capacity, live slippage or live consistency.",
            "Research-only; no paper or live execution authority.",
        )
        scorecard = StrategyScorecardV2(
            "strategy-scorecard-v2",
            request.definition.strategy_id,
            request.definition.strategy_version,
            run_id,
            request.dataset.version,
            request.definition.feature_versions,
            request.cost_model_version,
            request.evaluated_at,
            request.decision_at,
            ScorecardStatus.REVIEW_REQUIRED,
            limitations,
            tuple(metrics),
            complexity_components(
                parameter_count=len(request.definition.parameters),
                turnover=vector.turnover,
                sample_size=len(vector.period_returns),
            ),
            "CLEAR_SYNTHETIC_FIXTURE",
            (request.data_health.assessment_id,),
            evidence_manifest,
        )
        scorecard.validate()
        scorecard_evidence = _bound_evidence(
            evidence_type="scorecard",
            request=request,
            passed=True,
            source_hashes=(scorecard.content_hash(),),
            limitations=("Scorecard is synthetic and cannot activate a strategy.",),
        )
        bound.append(scorecard_evidence)
        evidence_ids = {item.evidence_type: item.identity.artifact_id for item in bound}
        evidence_hashes = {item.evidence_type: item.identity.content_hash for item in bound}
        package = build_validation_package(
            strategy_id=request.definition.strategy_id,
            strategy_version_id=request.definition.strategy_version_id,
            strategy_version=request.definition.strategy_version,
            dataset_id=request.dataset.dataset_id,
            dataset_version_id=request.dataset.dataset_version_id,
            dataset_version=request.dataset.version,
            feature_versions=request.definition.feature_versions,
            cost_model_version=request.cost_model_version,
            evidence_ids=evidence_ids,
            evidence_hashes=evidence_hashes,
            limitations=limitations,
            validation_metadata={
                "research_run_id": str(run_id),
                "research_trial_count": request.research_trial_count,
                "parameter_selection_before_holdout": True,
                "evidence_classification": "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",
                "maximum_automatic_state": "REVIEW_REQUIRED",
                "paper_authority": False,
                "live_authority": False,
            },
            evaluated_at=request.evaluated_at,
        )
        status = (
            TrendResearchStatus.BLOCKED
            if not independent.reconciled or event.unexplained_differences
            else TrendResearchStatus.REVIEW_REQUIRED
        )
        return TrendResearchRun(
            run_id,
            content_hash,
            request.definition,
            request.dataset,
            materialization_hashes,
            signals,
            vector,
            independent,
            event,
            walk_forward,
            request.research_trial_count,
            quant,
            tuple(bound),
            scorecard,
            package,
            status,
            limitations,
            request.evaluated_at,
        )


class PostgresTrendResearchV2Store:
    """Persist the complete run through existing normalized append-only tables."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register_strategy(self, definition: TrendStrategyDefinitionV2) -> UUID:
        definition.validate()
        feature_manifest = [
            {
                "feature_id": str(item.feature_id),
                "name": item.name,
                "semantic_version": item.semantic_version,
            }
            for item in definition.required_features
        ]
        contract = _wire(definition)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO strategy_definitions VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (strategy_id) DO NOTHING",
                    (
                        definition.strategy_id,
                        definition.family,
                        definition.economic_hypothesis,
                        definition.created_at,
                    ),
                )
                cursor.execute(
                    "SELECT family,hypothesis,created_at FROM strategy_definitions "
                    "WHERE strategy_id=%s",
                    (definition.strategy_id,),
                )
                strategy_row = cursor.fetchone()
                if strategy_row is None or (
                    str(strategy_row[0]), str(strategy_row[1]), strategy_row[2]
                ) != (
                    definition.family,
                    definition.economic_hypothesis,
                    definition.created_at,
                ):
                    raise TrendResearchV2Error("trend_strategy_definition_conflict")
                cursor.execute(
                    "INSERT INTO strategy_versions VALUES "
                    "(%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s) "
                    "ON CONFLICT (strategy_version_id) DO NOTHING",
                    (
                        definition.strategy_version_id,
                        definition.strategy_id,
                        definition.strategy_version,
                        json.dumps(feature_manifest, sort_keys=True),
                        definition.cost_model_requirement,
                        "synthetic-adv-capacity-v1",
                        json.dumps(contract, sort_keys=True),
                        definition.created_at,
                    ),
                )
                cursor.execute(
                    "SELECT strategy_id,version,feature_manifest,cost_model_version,"
                    "capacity_model_version,contract,created_at FROM strategy_versions "
                    "WHERE strategy_version_id=%s",
                    (definition.strategy_version_id,),
                )
                version_row = cursor.fetchone()
                if version_row is None or (
                    UUID(str(version_row[0])) != definition.strategy_id
                    or str(version_row[1]) != definition.strategy_version
                    or version_row[2] != feature_manifest
                    or str(version_row[3]) != definition.cost_model_requirement
                    or str(version_row[4]) != "synthetic-adv-capacity-v1"
                    or version_row[5] != contract
                    or version_row[6] != definition.created_at
                ):
                    raise TrendResearchV2Error("trend_strategy_version_conflict")
            return definition.strategy_version_id
        except TrendResearchV2Error:
            raise
        except Exception as error:
            raise TrendResearchV2Error("trend_strategy_registration_failed") from error

    def persist_complete(self, run: TrendResearchRun) -> UUID:
        self.register_strategy(run.definition)
        self._append_research_evidence(run)
        validation = PostgresQuantValidationStore(
            self._database,
            strategy_versions={run.definition.strategy_version: run.definition.strategy_version_id},
            dataset_versions={run.dataset.version: run.dataset.dataset_version_id},
        )
        for artifact in run.validation_evidence:
            validation.append(artifact)
        validation.append(run.validation_package)
        scorecards = PostgresStrategyScorecardStore(self._database)
        scorecards.publish(run.scorecard)
        scorecards.link_validation_package(
            run.scorecard.scorecard_id, run.validation_package.identity.artifact_id
        )
        return run.run_id

    def _append_research_evidence(self, run: TrendResearchRun) -> None:
        report = {
            "research_run_id": run.run_id,
            "status": run.status.value,
            "feature_versions": run.definition.feature_versions,
            "feature_materialization_hashes": run.feature_materialization_hashes,
            "signal_series": run.signal_series,
            "vector_result": run.vector_result,
            "independent_result": run.independent_result,
            "event_result": run.event_result,
            "walk_forward_results": run.walk_forward_results,
            "research_trial_count": run.research_trial_count,
            "quant_evidence": run.quant_evidence,
            "scorecard_id": run.scorecard.scorecard_id,
            "validation_package_id": run.validation_package.identity.artifact_id,
            "limitations": run.limitations,
        }
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO research_experiments "
                    "(experiment_id,strategy_version_id,dataset_version_id,parameters,report,"
                    "content_hash,created_at) VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) "
                    "ON CONFLICT (experiment_id) DO NOTHING RETURNING content_hash",
                    (
                        run.run_id,
                        run.definition.strategy_version_id,
                        run.dataset.dataset_version_id,
                        json.dumps(dict(run.definition.parameters), sort_keys=True),
                        json.dumps(_wire(report), sort_keys=True),
                        run.content_hash,
                        run.evaluated_at,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "SELECT content_hash FROM research_experiments WHERE experiment_id=%s",
                        (run.run_id,),
                    )
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != run.content_hash:
                        raise TrendResearchV2Error("trend_research_replay_conflict")
                for index, item in enumerate(run.walk_forward_results):
                    split = item.split
                    split_id = uuid5(NAMESPACE_URL, f"{run.run_id}:walk-forward:{index}")
                    split_report = _wire(item.result)
                    split_hash = _hash(
                        {"run_id": run.run_id, "split_index": index, "result": split_report}
                    )
                    bars = run.dataset.bars
                    cursor.execute(
                        "INSERT INTO walk_forward_evidence "
                        "(walk_forward_id,experiment_id,split_index,train_start,train_end,"
                        "validation_start,validation_end,test_start,test_end,report,content_hash) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) "
                        "ON CONFLICT (walk_forward_id) DO NOTHING",
                        (
                            split_id,
                            run.run_id,
                            index,
                            bars[split.train_start].occurred_at,
                            bars[split.train_end - 1].occurred_at,
                            bars[split.validation_start].occurred_at,
                            bars[split.validation_end - 1].occurred_at,
                            bars[split.test_start].occurred_at,
                            bars[split.test_end - 1].occurred_at,
                            json.dumps(split_report, sort_keys=True),
                            split_hash,
                        ),
                    )
                event_hash = _hash(run.event_result)
                cursor.execute(
                    "INSERT INTO golden_execution_artifacts "
                    "(golden_artifact_id,strategy_version_id,dataset_version_id,scenario_version,"
                    "reconciled,content_hash,report,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s) "
                    "ON CONFLICT (golden_artifact_id) DO NOTHING",
                    (
                        run.event_result.artifact_id,
                        run.definition.strategy_version_id,
                        run.dataset.dataset_version_id,
                        run.event_result.scenario_version,
                        run.event_result.reconciled,
                        event_hash,
                        json.dumps(_wire(run.event_result), sort_keys=True),
                        run.evaluated_at,
                    ),
                )
        except TrendResearchV2Error:
            raise
        except Exception as error:
            raise TrendResearchV2Error("trend_research_persistence_failed") from error

    def get_run_hash(self, run_id: UUID) -> str:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT content_hash FROM research_experiments WHERE experiment_id=%s", (run_id,)
            )
            row = cursor.fetchone()
        if row is None:
            raise TrendResearchV2Error("trend_research_run_not_found")
        return str(row[0])
