from __future__ import annotations

import dataclasses
import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import combinations
from math import log, sqrt
from statistics import NormalDist, mean, stdev
from uuid import uuid4

from trade_platform.crypto_basis_mean_reversion_v1 import (
    BasisMeanReversionOutcomeV1,
    BasisMeanReversionResearchRunV1,
    CryptoBasisMeanReversionDefinitionV1,
    run_crypto_basis_mean_reversion_research,
)
from trade_platform.crypto_instruments import CryptoInstrumentKind
from trade_platform.feature_authority import (
    FeatureMaterializationV2,
    FeatureQualityStatus,
    FeatureSubjectType,
)
from trade_platform.open_to_open_validation_v1 import (
    CAPACITY_BLOCKED_REASON,
    COARSE_1M_GRID_LATENCY_STRESS,
    REALIZED_EXIT_DAILY_RETURN_SERIES_KIND,
    OpenToOpenValidationV1Error,
    RealizedExitDailyReturnSeriesV1,
    ResearchTrialDispositionV1,
    ResearchTrialRoleV1,
    _midranks_ascending,
    _ReplayTrade,
    _select_is_winner,
    _verify_zero_latency_reconciles_canonical_run,
    bias_corrected_pearson_kurtosis,
    bias_corrected_sample_skewness,
    build_capacity_blocked_evidence_v1,
    build_realized_exit_daily_return_series_v1,
    build_reduced_liquidity_blocked_evidence_v1,
    build_research_trial_ledger_v1,
    build_research_trial_v1,
    canonical_trade_returns_for_monte_carlo,
    circularly_shift_basis_values,
    evaluate_canonical_cscv_pbo_v1,
    evaluate_circular_shift_null_control_v1,
    evaluate_deflated_sharpe_evidence_v1,
    evaluate_full_permutation_null_diagnostic_v1,
    evaluate_open_to_open_adverse_exit_shock_v1,
    evaluate_open_to_open_cost_sensitivity_v1,
    evaluate_open_to_open_latency_sensitivity_v1,
    evaluate_open_to_open_missing_bar_stress_v1,
    non_annualized_daily_sharpe,
    reconcile_open_to_open_trade_ledger_v1,
    sample_standard_deviation,
    trade_return_metrics_v1,
    trial_sharpe_mean,
    trial_sharpe_standard_deviation,
)
from trade_platform.quant_validation import evaluate_bootstrap, evaluate_monte_carlo_trade_sequence
from trade_platform.research import CostModel
from trade_platform.strategy_feature_binding_v2 import (
    AuthoritativeFeatureSeriesV2,
    ResearchFeatureRequirementV2,
    ResearchQualityPolicyV2,
    SubjectAwareResearchFeatureBundle,
)
from trade_platform.tradable_bar_evidence_v2 import (
    AuthoritativeTradableBarSeriesV2,
    AuthoritativeTradableBarV2,
)
from trade_platform.tradable_research_evidence_v2 import SubjectAwareTradableResearchEvidenceV2

START = datetime(2026, 1, 1, tzinfo=UTC)
DATASET_ID = uuid4()
INSTRUMENT = "TESTFIXTURE:3J2B2B1:BTCUSDT:PERP"
BASIS_FEATURE_ID = uuid4()
ZERO_COST = CostModel()
NONZERO_COST = CostModel(percentage_per_turnover=Decimal("0.001"))


def _hash_id(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _materialization(*, event_at: datetime, value: Decimal) -> FeatureMaterializationV2:
    return FeatureMaterializationV2.create(
        feature_id=BASIS_FEATURE_ID,
        subject_type=FeatureSubjectType.INSTRUMENT,
        subject_id=INSTRUMENT,
        dataset_version=str(DATASET_ID),
        event_at=event_at,
        effective_at=event_at,
        knowledge_at=event_at,
        computed_at=event_at,
        source_observation_manifest=("fixture:manifest",),
        value=value,
        quality_status=FeatureQualityStatus.VALIDATED,
    )


def _bundle(
    offsets_values: list[tuple[int, Decimal]], *, decision_at: datetime = START + timedelta(days=1)
) -> SubjectAwareResearchFeatureBundle:
    materializations = tuple(
        _materialization(event_at=START + timedelta(minutes=offset), value=value)
        for offset, value in offsets_values
    )
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


def _bar(
    offset: int,
    open_price: Decimal,
    *,
    dataset_version_id: object = DATASET_ID,
    instrument_id: str = INSTRUMENT,
) -> AuthoritativeTradableBarV2:
    bar_open_at = START + timedelta(minutes=offset)
    return AuthoritativeTradableBarV2(
        dataset_version_id=dataset_version_id,  # type: ignore[arg-type]
        dataset_content_hash="e" * 64,
        source_id=uuid4(),
        normalized_observation_id=uuid4(),
        raw_observation_id=uuid4(),
        raw_payload_sha256="f" * 64,
        instrument_id=instrument_id,
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


def _bar_series(
    *,
    minutes: int,
    opens: dict[int, Decimal],
    dataset_version_id: object = DATASET_ID,
    instrument_id: str = INSTRUMENT,
) -> AuthoritativeTradableBarSeriesV2:
    bars = tuple(
        _bar(offset, opens.get(offset, Decimal("100")), dataset_version_id=dataset_version_id, instrument_id=instrument_id)
        for offset in range(minutes)
    )
    series = AuthoritativeTradableBarSeriesV2(dataset_version_id, instrument_id, "1m", bars)  # type: ignore[arg-type]
    series.validate()
    return series


def _definition(*, threshold: Decimal = Decimal("0.0005"), horizon: int = 1) -> CryptoBasisMeanReversionDefinitionV1:
    return CryptoBasisMeanReversionDefinitionV1(
        basis_entry_threshold=threshold, holding_horizon_bars=horizon, maximum_absolute_exposure=Decimal("1")
    )


def _run(
    *,
    offsets_values: list[tuple[int, Decimal]],
    minutes: int,
    opens: dict[int, Decimal],
    horizon: int = 1,
    cost_model: CostModel = ZERO_COST,
) -> BasisMeanReversionResearchRunV1:
    bundle = _bundle(offsets_values)
    bar_series = _bar_series(minutes=minutes, opens=opens)
    evidence = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=bar_series)
    return run_crypto_basis_mean_reversion_research(
        definition=_definition(horizon=horizon),
        evidence=evidence,
        instrument_kind=CryptoInstrumentKind.PERPETUAL,
        cost_model=cost_model,
        cost_model_version="cost-model-v1",
    )


def _two_trade_run(cost_model: CostModel = ZERO_COST) -> tuple[BasisMeanReversionResearchRunV1, AuthoritativeTradableBarSeriesV2]:
    offsets_values = [(0, Decimal("-0.001")), (3, Decimal("0.001"))]
    opens = {2: Decimal("110"), 5: Decimal("90")}
    bundle = _bundle(offsets_values)
    bar_series = _bar_series(minutes=8, opens=opens)
    evidence = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=bar_series)
    run = run_crypto_basis_mean_reversion_research(
        definition=_definition(horizon=1),
        evidence=evidence,
        instrument_kind=CryptoInstrumentKind.PERPETUAL,
        cost_model=cost_model,
        cost_model_version="cost-model-v1",
    )
    return run, bar_series


def _run_with_overlap() -> tuple[BasisMeanReversionResearchRunV1, AuthoritativeTradableBarSeriesV2]:
    offsets_values = [(0, Decimal("-0.001")), (1, Decimal("0.001"))]
    opens = {offset: Decimal("100") + Decimal(offset) for offset in range(15)}
    run = _run(offsets_values=offsets_values, minutes=15, opens=opens, horizon=10)
    bar_series = _bar_series(minutes=15, opens=opens)
    return run, bar_series


def _run_with_flat_decision() -> tuple[BasisMeanReversionResearchRunV1, AuthoritativeTradableBarSeriesV2]:
    offsets_values = [(0, Decimal("0")), (1, Decimal("-0.001"))]
    opens = {2: Decimal("100"), 3: Decimal("110")}
    run = _run(offsets_values=offsets_values, minutes=6, opens=opens, horizon=1)
    bar_series = _bar_series(minutes=6, opens=opens)
    return run, bar_series


def _fake_run(*, content_hash: str, definition: CryptoBasisMeanReversionDefinitionV1 | None = None) -> BasisMeanReversionResearchRunV1:
    return BasisMeanReversionResearchRunV1(
        definition=definition or _definition(),
        evidence_content_hash="0" * 64,
        dataset_version_id=DATASET_ID,
        instrument_id=INSTRUMENT,
        cost_model_version="cost-model-v1",
        decisions=(),
        content_hash=content_hash,
        run_id=uuid4(),
    )


def _series(
    run: BasisMeanReversionResearchRunV1, daily_returns: tuple[Decimal, ...], content_hash: str
) -> RealizedExitDailyReturnSeriesV1:
    return RealizedExitDailyReturnSeriesV1(
        series_kind=REALIZED_EXIT_DAILY_RETURN_SERIES_KIND,
        semantic_version="1.0.0",
        source_run_content_hash=run.content_hash,
        dataset_version_id=run.dataset_version_id,
        instrument_id=run.instrument_id,
        window_start=START,
        window_end=START + timedelta(days=len(daily_returns)),
        dates=tuple((START + timedelta(days=index)).date() for index in range(len(daily_returns))),
        daily_returns=daily_returns,
        content_hash=content_hash,
        series_id=uuid4(),
    )


class RealizedExitDailySeriesTests(unittest.TestCase):
    def test_zero_day_filling_and_same_day_compounding(self) -> None:
        run, _ = _two_trade_run()
        series = build_realized_exit_daily_return_series_v1(
            run=run, window_start=START, window_end=START + timedelta(days=3)
        )
        self.assertEqual(len(series.daily_returns), 3)
        self.assertEqual(series.daily_returns[0], Decimal("0.21"))
        self.assertEqual(series.daily_returns[1], Decimal("0"))
        self.assertEqual(series.daily_returns[2], Decimal("0"))
        self.assertEqual(series.series_kind, REALIZED_EXIT_DAILY_RETURN_SERIES_KIND)

    def test_window_must_be_utc_midnight(self) -> None:
        run, _ = _two_trade_run()
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "window_start_must_be_utc_midnight"):
            build_realized_exit_daily_return_series_v1(
                run=run, window_start=START + timedelta(hours=1), window_end=START + timedelta(days=2)
            )

    def test_window_must_be_utc(self) -> None:
        run, _ = _two_trade_run()
        naive = START.replace(tzinfo=None)
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "window_start_must_be_utc"):
            build_realized_exit_daily_return_series_v1(
                run=run, window_start=naive, window_end=START + timedelta(days=2)
            )

    def test_window_end_after_start(self) -> None:
        run, _ = _two_trade_run()
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "window_end_must_be_after_window_start"):
            build_realized_exit_daily_return_series_v1(run=run, window_start=START, window_end=START)

    def test_deterministic_daily_series_hash(self) -> None:
        run, _ = _two_trade_run()
        first = build_realized_exit_daily_return_series_v1(run=run, window_start=START, window_end=START + timedelta(days=3))
        second = build_realized_exit_daily_return_series_v1(run=run, window_start=START, window_end=START + timedelta(days=3))
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.series_id, second.series_id)
        widened = build_realized_exit_daily_return_series_v1(run=run, window_start=START, window_end=START + timedelta(days=4))
        self.assertNotEqual(first.content_hash, widened.content_hash)


class TradeReturnMetricsTests(unittest.TestCase):
    def test_basic_metrics(self) -> None:
        metrics = trade_return_metrics_v1((Decimal("0.1"), Decimal("-0.05"), Decimal("0.2")))
        self.assertEqual(metrics.number_of_trades, 3)
        self.assertEqual(metrics.hit_rate, Decimal("2") / Decimal("3"))
        self.assertEqual(metrics.average_trade, (Decimal("0.1") + Decimal("-0.05") + Decimal("0.2")) / Decimal("3"))
        self.assertEqual(metrics.median_trade, Decimal("0.1"))
        self.assertEqual(metrics.win_loss_ratio, Decimal("2"))
        self.assertEqual(metrics.gains_distribution, (Decimal("0.1"), Decimal("0.2")))
        self.assertEqual(metrics.losses_distribution, (Decimal("-0.05"),))

    def test_undefined_denominators_are_unavailable_not_zero(self) -> None:
        winners_only = trade_return_metrics_v1((Decimal("0.1"), Decimal("0.2")))
        self.assertIsNone(winners_only.win_loss_ratio)
        self.assertIsNone(winners_only.payoff_ratio)
        self.assertIsNone(winners_only.profit_factor)
        empty = trade_return_metrics_v1(())
        self.assertEqual(empty.number_of_trades, 0)
        self.assertIsNone(empty.hit_rate)
        self.assertIsNone(empty.average_trade)
        self.assertIsNone(empty.median_trade)

    def test_no_annualized_field_exposed(self) -> None:
        metrics = trade_return_metrics_v1((Decimal("0.1"), Decimal("-0.05")))
        forbidden = ("sharpe", "sortino", "annualized_return", "annualized_volatility", "cagr", "calmar")
        for name in forbidden:
            self.assertFalse(hasattr(metrics, name))


class CostSensitivityTests(unittest.TestCase):
    def test_base_scenario_reconciles_exactly(self) -> None:
        run, _ = _two_trade_run(cost_model=NONZERO_COST)
        evidence = evaluate_open_to_open_cost_sensitivity_v1(run=run, base_cost_model=NONZERO_COST)
        base = evidence.scenarios[0]
        self.assertEqual(base.cost_multiplier, Decimal("1.0"))
        self.assertEqual(base.net_returns, run.trade_returns)

    def test_degradation_arithmetic(self) -> None:
        run, _ = _two_trade_run(cost_model=NONZERO_COST)
        evidence = evaluate_open_to_open_cost_sensitivity_v1(run=run, base_cost_model=NONZERO_COST)
        multipliers = tuple(scenario.cost_multiplier for scenario in evidence.scenarios)
        self.assertEqual(multipliers, (Decimal("1.0"), Decimal("1.5"), Decimal("2.0"), Decimal("3.0")))
        self.assertEqual(evidence.scenarios[0].net_returns[0], Decimal("0.098"))
        self.assertEqual(evidence.scenarios[1].net_returns[0], Decimal("0.097"))
        self.assertEqual(evidence.scenarios[2].net_returns[0], Decimal("0.096"))
        self.assertEqual(evidence.scenarios[3].net_returns[0], Decimal("0.094"))

    def test_wrong_base_cost_model_rejected(self) -> None:
        run, _ = _two_trade_run(cost_model=NONZERO_COST)
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "cost_sensitivity_base_scenario_mismatch"):
            evaluate_open_to_open_cost_sensitivity_v1(run=run, base_cost_model=ZERO_COST)

    def test_1x_rejects_matching_net_cost_but_mismatched_entry_exit_split(self) -> None:
        run, _ = _two_trade_run(cost_model=NONZERO_COST)
        original_trade = run.executed_trades[0]
        total_cost = original_trade.entry_cost + original_trade.exit_cost
        corrupted_trade = dataclasses.replace(
            original_trade,
            entry_cost=total_cost,
            exit_cost=Decimal("0"),
            net_return=original_trade.gross_return - total_cost,
        )
        corrupted_decision = dataclasses.replace(run.decisions[0], trade=corrupted_trade)
        corrupted_run = dataclasses.replace(run, decisions=(corrupted_decision,) + run.decisions[1:])
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "cost_sensitivity_base_scenario_mismatch"):
            evaluate_open_to_open_cost_sensitivity_v1(run=corrupted_run, base_cost_model=NONZERO_COST)


class LatencySensitivityTests(unittest.TestCase):
    def test_zero_minute_reconciles_with_canonical_run(self) -> None:
        run, bar_series = _two_trade_run()
        evidence = evaluate_open_to_open_latency_sensitivity_v1(
            run=run, bar_series=bar_series, base_cost_model=ZERO_COST
        )
        self.assertEqual(evidence.evidence_label, COARSE_1M_GRID_LATENCY_STRESS)
        zero = evidence.scenarios[0]
        self.assertEqual(zero.latency_minutes, 0)
        self.assertEqual(zero.net_returns, run.trade_returns)

    def test_zero_minute_helper_detects_mismatch(self) -> None:
        run, _ = _two_trade_run()
        canonical = run.executed_trades[0]
        corrupted = _ReplayTrade(
            exposure=canonical.exposure,
            entry_time=canonical.entry_time,
            exit_time=canonical.exit_time,
            entry_open=canonical.entry_open,
            exit_open=canonical.exit_open,
            gross_return=Decimal("999"),
            entry_cost=canonical.entry_cost,
            exit_cost=canonical.exit_cost,
            net_return=canonical.net_return,
        )
        rest = tuple(
            _ReplayTrade(
                exposure=trade.exposure,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                entry_open=trade.entry_open,
                exit_open=trade.exit_open,
                gross_return=trade.gross_return,
                entry_cost=trade.entry_cost,
                exit_cost=trade.exit_cost,
                net_return=trade.net_return,
            )
            for trade in run.executed_trades[1:]
        )
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "latency_zero_minute_does_not_reconcile_canonical_run"):
            _verify_zero_latency_reconciles_canonical_run(run, (corrupted,) + rest, run.excluded_count)

    def test_strict_delayed_entry_and_exact_delayed_exit(self) -> None:
        opens = {offset: Decimal("100") + Decimal(offset) for offset in range(20)}
        run = _run(offsets_values=[(0, Decimal("-0.001"))], minutes=20, opens=opens, horizon=1)
        bar_series = _bar_series(minutes=20, opens=opens)
        evidence = evaluate_open_to_open_latency_sensitivity_v1(
            run=run, bar_series=bar_series, base_cost_model=ZERO_COST
        )
        zero_net = evidence.scenarios[0].net_returns[0]
        five_net = evidence.scenarios[2].net_returns[0]
        self.assertEqual(evidence.scenarios[2].latency_minutes, 5)
        self.assertEqual(zero_net, Decimal("102") / Decimal("101") - Decimal("1"))
        self.assertEqual(five_net, Decimal("107") / Decimal("106") - Decimal("1"))

    def test_missing_delayed_entry_excluded(self) -> None:
        run = _run(offsets_values=[(0, Decimal("-0.001"))], minutes=3, opens={2: Decimal("110")}, horizon=1)
        bar_series = _bar_series(minutes=3, opens={2: Decimal("110")})
        evidence = evaluate_open_to_open_latency_sensitivity_v1(
            run=run, bar_series=bar_series, base_cost_model=ZERO_COST
        )
        fifteen = evidence.scenarios[3]
        self.assertEqual(fifteen.latency_minutes, 15)
        self.assertEqual(fifteen.executed_count, 0)
        self.assertEqual(fifteen.excluded_count, 1)

    def test_latency_changes_non_overlap(self) -> None:
        opens = {offset: Decimal("100") + Decimal(offset) for offset in range(6)}
        run = _run(
            offsets_values=[(0, Decimal("-0.001")), (1, Decimal("0.001"))],
            minutes=6,
            opens=opens,
            horizon=3,
        )
        bar_series = _bar_series(minutes=6, opens=opens)
        evidence = evaluate_open_to_open_latency_sensitivity_v1(
            run=run, bar_series=bar_series, base_cost_model=ZERO_COST
        )
        self.assertEqual(evidence.scenarios[0].executed_count, 1)
        fifteen = evidence.scenarios[3]
        self.assertEqual(fifteen.executed_count, 0)


class AdverseExitShockTests(unittest.TestCase):
    def test_long_and_short_shock_direction(self) -> None:
        run, _ = _two_trade_run()
        evidence = evaluate_open_to_open_adverse_exit_shock_v1(
            run=run, base_cost_model=ZERO_COST, shock_magnitudes=(Decimal("0.02"),)
        )
        self.assertTrue(evidence.synthetic_validation_evidence)
        scenario = evidence.scenarios[0]
        self.assertEqual(scenario.net_returns[0], Decimal("0.078"))
        self.assertEqual(scenario.net_returns[1], Decimal("0.082"))
        self.assertLess(scenario.net_returns[0], Decimal("0.1"))
        self.assertLess(scenario.net_returns[1], Decimal("0.1"))

    def test_shock_magnitude_must_be_positive(self) -> None:
        run, _ = _two_trade_run()
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "adverse_shock_magnitude_out_of_bounds"):
            evaluate_open_to_open_adverse_exit_shock_v1(
                run=run, base_cost_model=ZERO_COST, shock_magnitudes=(Decimal("0"),)
            )

    def test_shock_magnitude_must_be_below_one(self) -> None:
        run, _ = _two_trade_run()
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "adverse_shock_magnitude_out_of_bounds"):
            evaluate_open_to_open_adverse_exit_shock_v1(
                run=run, base_cost_model=ZERO_COST, shock_magnitudes=(Decimal("1"),)
            )


class MissingBarStressTests(unittest.TestCase):
    def test_deterministic_missing_exit_alters_non_overlap(self) -> None:
        opens = {11: Decimal("105"), 12: Decimal("90")}
        run = _run(
            offsets_values=[(0, Decimal("-0.001")), (1, Decimal("0.001"))],
            minutes=14,
            opens=opens,
            horizon=10,
        )
        bar_series = _bar_series(minutes=14, opens=opens)
        evidence = evaluate_open_to_open_missing_bar_stress_v1(
            run=run,
            bar_series=bar_series,
            base_cost_model=ZERO_COST,
            omitted_exit_bar_open_times=(START + timedelta(minutes=11),),
        )
        self.assertTrue(evidence.synthetic_validation_evidence)
        self.assertEqual(evidence.baseline_executed_count, 1)
        self.assertEqual(evidence.stressed_executed_count, 1)
        self.assertEqual(run.trade_returns, (Decimal("0.05"),))
        self.assertEqual(evidence.net_returns, (Decimal("0.1"),))

    def test_omitting_an_entry_bar_timestamp_does_not_shift_entry(self) -> None:
        opens = {1: Decimal("105"), 11: Decimal("110")}
        run = _run(offsets_values=[(0, Decimal("-0.001"))], minutes=14, opens=opens, horizon=10)
        bar_series = _bar_series(minutes=14, opens=opens)
        baseline_trade = run.executed_trades[0]
        self.assertEqual(baseline_trade.entry_time, START + timedelta(minutes=1))
        evidence = evaluate_open_to_open_missing_bar_stress_v1(
            run=run,
            bar_series=bar_series,
            base_cost_model=ZERO_COST,
            omitted_exit_bar_open_times=(START + timedelta(minutes=1),),
        )
        self.assertEqual(evidence.stressed_executed_count, 1)
        self.assertEqual(evidence.net_returns, run.trade_returns)

    def test_omissions_required(self) -> None:
        run, bar_series = _two_trade_run()
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "missing_bar_omissions_required"):
            evaluate_open_to_open_missing_bar_stress_v1(
                run=run, bar_series=bar_series, base_cost_model=ZERO_COST, omitted_exit_bar_open_times=()
            )

    def test_omission_of_non_candidate_timestamp_rejected(self) -> None:
        run, bar_series = _two_trade_run()
        bogus = START + timedelta(days=365)
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "omitted_exit_bar_timestamp_not_a_candidate_bar"):
            evaluate_open_to_open_missing_bar_stress_v1(
                run=run, bar_series=bar_series, base_cost_model=ZERO_COST, omitted_exit_bar_open_times=(bogus,)
            )


class BlockedEvidenceTests(unittest.TestCase):
    def test_capacity_blocked_no_numeric_estimate(self) -> None:
        run, _ = _two_trade_run()
        evidence = build_capacity_blocked_evidence_v1(run=run)
        self.assertEqual(evidence.status, "BLOCKED")
        self.assertEqual(evidence.reason, CAPACITY_BLOCKED_REASON)
        forbidden = ("adv", "participation", "usable_capital", "market_impact", "capacity_estimate")
        for name in forbidden:
            self.assertFalse(hasattr(evidence, name))

    def test_reduced_liquidity_blocked(self) -> None:
        run, _ = _two_trade_run()
        evidence = build_reduced_liquidity_blocked_evidence_v1(run=run)
        self.assertEqual(evidence.status, "BLOCKED")
        self.assertEqual(evidence.reason, CAPACITY_BLOCKED_REASON)


class CostModelValidationTests(unittest.TestCase):
    def test_negative_cost_model_component_rejected_everywhere(self) -> None:
        run, bar_series = _two_trade_run()
        bad_cost = CostModel(percentage_per_turnover=Decimal("-0.01"))
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_component_invalid"):
            evaluate_open_to_open_cost_sensitivity_v1(run=run, base_cost_model=bad_cost)
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_component_invalid"):
            evaluate_open_to_open_latency_sensitivity_v1(run=run, bar_series=bar_series, base_cost_model=bad_cost)
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_component_invalid"):
            evaluate_open_to_open_adverse_exit_shock_v1(run=run, base_cost_model=bad_cost, shock_magnitudes=(Decimal("0.01"),))
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_component_invalid"):
            evaluate_open_to_open_missing_bar_stress_v1(
                run=run, bar_series=bar_series, base_cost_model=bad_cost,
                omitted_exit_bar_open_times=(bar_series.bars[0].bar_open_at,),
            )
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_component_invalid"):
            evaluate_circular_shift_null_control_v1(
                run=run, bar_series=bar_series, base_cost_model=bad_cost,
                window_start=START, window_end=START + timedelta(days=2), seed=1,
            )
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_component_invalid"):
            evaluate_full_permutation_null_diagnostic_v1(
                run=run, bar_series=bar_series, base_cost_model=bad_cost,
                window_start=START, window_end=START + timedelta(days=2), seed=1,
            )
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_component_invalid"):
            reconcile_open_to_open_trade_ledger_v1(run=run, bar_series=bar_series, base_cost_model=bad_cost)

    def test_base_cost_model_must_reconcile_with_run(self) -> None:
        run, bar_series = _two_trade_run(cost_model=NONZERO_COST)
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_does_not_reconcile_with_run"):
            evaluate_open_to_open_latency_sensitivity_v1(run=run, bar_series=bar_series, base_cost_model=ZERO_COST)
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_does_not_reconcile_with_run"):
            evaluate_open_to_open_adverse_exit_shock_v1(run=run, base_cost_model=ZERO_COST, shock_magnitudes=(Decimal("0.01"),))
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_does_not_reconcile_with_run"):
            evaluate_open_to_open_missing_bar_stress_v1(
                run=run, bar_series=bar_series, base_cost_model=ZERO_COST,
                omitted_exit_bar_open_times=(bar_series.bars[0].bar_open_at,),
            )
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_does_not_reconcile_with_run"):
            evaluate_circular_shift_null_control_v1(
                run=run, bar_series=bar_series, base_cost_model=ZERO_COST,
                window_start=START, window_end=START + timedelta(days=2), seed=1,
            )
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "base_cost_model_does_not_reconcile_with_run"):
            evaluate_full_permutation_null_diagnostic_v1(
                run=run, bar_series=bar_series, base_cost_model=ZERO_COST,
                window_start=START, window_end=START + timedelta(days=2), seed=1,
            )


class ProvenanceGateTests(unittest.TestCase):
    def _call(self, name: str, run: BasisMeanReversionResearchRunV1, bar_series: AuthoritativeTradableBarSeriesV2) -> None:
        if name == "latency":
            evaluate_open_to_open_latency_sensitivity_v1(run=run, bar_series=bar_series, base_cost_model=ZERO_COST)
        elif name == "missing_bar":
            evaluate_open_to_open_missing_bar_stress_v1(
                run=run, bar_series=bar_series, base_cost_model=ZERO_COST,
                omitted_exit_bar_open_times=(bar_series.bars[0].bar_open_at,),
            )
        elif name == "circular_shift":
            evaluate_circular_shift_null_control_v1(
                run=run, bar_series=bar_series, base_cost_model=ZERO_COST,
                window_start=START, window_end=START + timedelta(days=2), seed=1,
            )
        elif name == "full_permutation":
            evaluate_full_permutation_null_diagnostic_v1(
                run=run, bar_series=bar_series, base_cost_model=ZERO_COST,
                window_start=START, window_end=START + timedelta(days=2), seed=1,
            )
        elif name == "reconciliation":
            reconcile_open_to_open_trade_ledger_v1(run=run, bar_series=bar_series, base_cost_model=ZERO_COST)

    def test_cross_dataset_rejected(self) -> None:
        run, _ = _two_trade_run()
        mismatched = _bar_series(minutes=8, opens={2: Decimal("110"), 5: Decimal("90")}, dataset_version_id=uuid4())
        for name in ("latency", "missing_bar", "circular_shift", "full_permutation", "reconciliation"):
            with self.subTest(name=name), self.assertRaisesRegex(OpenToOpenValidationV1Error, "bar_series_dataset_mismatch"):
                self._call(name, run, mismatched)

    def test_cross_instrument_rejected(self) -> None:
        run, _ = _two_trade_run()
        mismatched = _bar_series(minutes=8, opens={2: Decimal("110"), 5: Decimal("90")}, instrument_id="OTHER:INSTRUMENT")
        for name in ("latency", "missing_bar", "circular_shift", "full_permutation", "reconciliation"):
            with self.subTest(name=name), self.assertRaisesRegex(OpenToOpenValidationV1Error, "bar_series_instrument_mismatch"):
                self._call(name, run, mismatched)


class SemanticIdentityBindingTests(unittest.TestCase):
    def test_latency_identity_binds_bar_series_provenance_not_just_output(self) -> None:
        run, bar_series = _two_trade_run()
        different_provenance = _bar_series(minutes=8, opens={2: Decimal("110"), 5: Decimal("90")})
        first = evaluate_open_to_open_latency_sensitivity_v1(run=run, bar_series=bar_series, base_cost_model=ZERO_COST)
        second = evaluate_open_to_open_latency_sensitivity_v1(run=run, bar_series=different_provenance, base_cost_model=ZERO_COST)
        self.assertEqual(first.scenarios, second.scenarios)
        self.assertNotEqual(first.bar_series_fingerprint, second.bar_series_fingerprint)
        self.assertNotEqual(first.content_hash, second.content_hash)

    def test_reconciliation_identity_binds_bar_series_provenance_not_just_output(self) -> None:
        run, bar_series = _two_trade_run(cost_model=NONZERO_COST)
        different_provenance = _bar_series(minutes=8, opens={2: Decimal("110"), 5: Decimal("90")})
        first = reconcile_open_to_open_trade_ledger_v1(run=run, bar_series=bar_series, base_cost_model=NONZERO_COST)
        second = reconcile_open_to_open_trade_ledger_v1(run=run, bar_series=different_provenance, base_cost_model=NONZERO_COST)
        self.assertEqual(first.checks, second.checks)
        self.assertEqual(first.status, second.status)
        self.assertNotEqual(first.content_hash, second.content_hash)


class BootstrapExtensionTests(unittest.TestCase):
    RETURNS = (Decimal("0.01"), Decimal("-0.02"), Decimal("0.03"), Decimal("0.015"))

    def test_default_equals_explicit_252_bit_for_bit(self) -> None:
        default = evaluate_bootstrap(
            strategy_version="s", dataset_version="d", period_returns=self.RETURNS, seed=7, resamples=25
        )
        explicit = evaluate_bootstrap(
            strategy_version="s", dataset_version="d", period_returns=self.RETURNS, seed=7, resamples=25,
            periods_per_year=252,
        )
        self.assertEqual(default.identity.content_hash, explicit.identity.content_hash)
        self.assertEqual(default.return_distribution, explicit.return_distribution)
        self.assertEqual(default.sharpe_distribution, explicit.sharpe_distribution)
        self.assertEqual(default.drawdown_distribution, explicit.drawdown_distribution)

    def test_365_changes_only_sharpe_annualization(self) -> None:
        legacy = evaluate_bootstrap(
            strategy_version="s", dataset_version="d", period_returns=self.RETURNS, seed=7, resamples=25
        )
        crypto = evaluate_bootstrap(
            strategy_version="s", dataset_version="d", period_returns=self.RETURNS, seed=7, resamples=25,
            periods_per_year=365,
        )
        self.assertEqual(legacy.return_distribution, crypto.return_distribution)
        self.assertEqual(legacy.drawdown_distribution, crypto.drawdown_distribution)
        self.assertNotEqual(legacy.identity.content_hash, crypto.identity.content_hash)
        expected_ratio = sqrt(365 / 252)
        compared = 0
        for legacy_value, crypto_value in zip(legacy.sharpe_distribution, crypto.sharpe_distribution, strict=True):
            if legacy_value is None or crypto_value is None:
                continue
            self.assertAlmostEqual(float(crypto_value) / float(legacy_value), expected_ratio, places=9)
            compared += 1
        self.assertGreater(compared, 0)

    def test_365_identity_bound_even_in_zero_volatility_sample(self) -> None:
        constant_returns = (Decimal("0.01"),)
        legacy = evaluate_bootstrap(
            strategy_version="s", dataset_version="d", period_returns=constant_returns, seed=1, resamples=5
        )
        crypto = evaluate_bootstrap(
            strategy_version="s", dataset_version="d", period_returns=constant_returns, seed=1, resamples=5,
            periods_per_year=365,
        )
        self.assertEqual(legacy.sharpe_distribution, crypto.sharpe_distribution)
        self.assertTrue(all(value is None for value in legacy.sharpe_distribution))
        self.assertEqual(legacy.return_distribution, crypto.return_distribution)
        self.assertNotEqual(legacy.identity.content_hash, crypto.identity.content_hash)

    def test_invalid_periods_per_year_rejected(self) -> None:
        from trade_platform.quant_validation import QuantValidationError

        with self.assertRaises(QuantValidationError):
            evaluate_bootstrap(
                strategy_version="s", dataset_version="d", period_returns=self.RETURNS, seed=7, resamples=5,
                periods_per_year=0,
            )


class MonteCarloBoundaryTests(unittest.TestCase):
    def test_uses_canonical_trade_returns_unchanged(self) -> None:
        run, _ = _two_trade_run()
        trade_returns = canonical_trade_returns_for_monte_carlo(run)
        self.assertEqual(trade_returns, run.trade_returns)
        daily = build_realized_exit_daily_return_series_v1(
            run=run, window_start=START, window_end=START + timedelta(days=3)
        )
        self.assertNotEqual(trade_returns, daily.daily_returns)
        evidence = evaluate_monte_carlo_trade_sequence(
            strategy_version="s", dataset_version="d", trade_returns=trade_returns, seed=1, simulations=10
        )
        self.assertEqual(evidence.simulations, 10)


class EstimatorTests(unittest.TestCase):
    def test_sample_standard_deviation_ddof_one(self) -> None:
        returns = (Decimal("0.01"), Decimal("0.02"), Decimal("0.03"))
        self.assertAlmostEqual(sample_standard_deviation(returns), 0.01, places=12)
        self.assertAlmostEqual(sample_standard_deviation(returns), stdev([0.01, 0.02, 0.03]), places=12)
        self.assertIsNone(sample_standard_deviation((Decimal("0.01"),)))

    def test_daily_sharpe_and_undefined_cases(self) -> None:
        self.assertAlmostEqual(non_annualized_daily_sharpe((Decimal("0.01"), Decimal("0.02"), Decimal("0.03"))), 2.0, places=12)
        self.assertIsNone(non_annualized_daily_sharpe((Decimal("0.01"),)))
        self.assertIsNone(non_annualized_daily_sharpe((Decimal("0.02"), Decimal("0.02"), Decimal("0.02"))))

    def test_skewness_reference_vector(self) -> None:
        symmetric = (Decimal("-0.02"), Decimal("-0.01"), Decimal("0.01"), Decimal("0.02"))
        self.assertAlmostEqual(bias_corrected_sample_skewness(symmetric), 0.0, places=12)
        self.assertIsNone(bias_corrected_sample_skewness((Decimal("0.01"), Decimal("0.02"), Decimal("0.03"))))

    def test_pearson_kurtosis_reference_vector(self) -> None:
        two_valued = (Decimal("-0.01"), Decimal("-0.01"), Decimal("0.01"), Decimal("0.01"))
        self.assertAlmostEqual(bias_corrected_pearson_kurtosis(two_valued), -3.0, places=12)

    def test_trial_sharpe_mean_and_std(self) -> None:
        self.assertAlmostEqual(trial_sharpe_mean([1.0, 2.0, 3.0]), 2.0, places=12)
        self.assertAlmostEqual(trial_sharpe_standard_deviation([1.0, 2.0, 3.0]), 1.0, places=12)
        self.assertIsNone(trial_sharpe_standard_deviation([1.0]))


class TrialProvenanceTests(unittest.TestCase):
    def test_series_from_different_run_rejected(self) -> None:
        run_one = _fake_run(content_hash="a" * 64)
        run_two = _fake_run(content_hash="b" * 64)
        series = _series(run_one, tuple(Decimal("0.01") for _ in range(30)), "c" * 64)
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "trial_series_run_mismatch"):
            build_research_trial_v1(
                run=run_two, series=series, trial_role=ResearchTrialRoleV1.BASELINE,
                disposition=ResearchTrialDispositionV1.SELECTED,
            )

    def test_series_from_different_dataset_rejected(self) -> None:
        run = _fake_run(content_hash="a" * 64)
        series = _series(run, tuple(Decimal("0.01") for _ in range(30)), "c" * 64)
        mismatched_series = dataclasses.replace(series, dataset_version_id=uuid4())
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "trial_series_dataset_mismatch"):
            build_research_trial_v1(
                run=run, series=mismatched_series, trial_role=ResearchTrialRoleV1.BASELINE,
                disposition=ResearchTrialDispositionV1.SELECTED,
            )

    def test_series_from_different_instrument_rejected(self) -> None:
        run = _fake_run(content_hash="a" * 64)
        series = _series(run, tuple(Decimal("0.01") for _ in range(30)), "c" * 64)
        mismatched_series = dataclasses.replace(series, instrument_id="OTHER:INSTRUMENT")
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "trial_series_instrument_mismatch"):
            build_research_trial_v1(
                run=run, series=mismatched_series, trial_role=ResearchTrialRoleV1.BASELINE,
                disposition=ResearchTrialDispositionV1.SELECTED,
            )


class TrialRoleDispositionTests(unittest.TestCase):
    def test_role_and_disposition_both_bind_identity(self) -> None:
        run = _fake_run(content_hash="a" * 64)
        series = _series(run, tuple(Decimal("0.01") for _ in range(30)), "c" * 64)
        baseline_selected = build_research_trial_v1(
            run=run, series=series, trial_role=ResearchTrialRoleV1.BASELINE, disposition=ResearchTrialDispositionV1.SELECTED
        )
        neighbor_rejected = build_research_trial_v1(
            run=run, series=series, trial_role=ResearchTrialRoleV1.NEIGHBOR, disposition=ResearchTrialDispositionV1.REJECTED
        )
        baseline_rejected = build_research_trial_v1(
            run=run, series=series, trial_role=ResearchTrialRoleV1.BASELINE, disposition=ResearchTrialDispositionV1.REJECTED
        )
        self.assertNotEqual(baseline_selected.trial_content_hash, neighbor_rejected.trial_content_hash)
        self.assertNotEqual(baseline_selected.trial_content_hash, baseline_rejected.trial_content_hash)
        self.assertNotEqual(neighbor_rejected.trial_content_hash, baseline_rejected.trial_content_hash)


class TrialLedgerTests(unittest.TestCase):
    def test_duplicate_trial_rejected(self) -> None:
        run = _fake_run(content_hash="a" * 64)
        series = _series(run, tuple(Decimal("0.01") for _ in range(30)), "b" * 64)
        trial_one = build_research_trial_v1(
            run=run, series=series, trial_role=ResearchTrialRoleV1.BASELINE, disposition=ResearchTrialDispositionV1.SELECTED
        )
        trial_two = build_research_trial_v1(
            run=run, series=series, trial_role=ResearchTrialRoleV1.BASELINE, disposition=ResearchTrialDispositionV1.SELECTED
        )
        self.assertEqual(trial_one.trial_content_hash, trial_two.trial_content_hash)
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "duplicate_research_trial"):
            build_research_trial_ledger_v1([trial_one, trial_two])

    def test_ledger_deterministic_and_order_independent(self) -> None:
        trials = [
            build_research_trial_v1(
                run=_fake_run(content_hash=f"{index:064d}"),
                series=_series(
                    _fake_run(content_hash=f"{index:064d}"),
                    tuple(Decimal(str(index)) / Decimal("100") for _ in range(30)),
                    f"{index + 100:064d}",
                ),
                trial_role=ResearchTrialRoleV1.OTHER,
                disposition=ResearchTrialDispositionV1.INSPECTED,
            )
            for index in range(1, 4)
        ]
        first = build_research_trial_ledger_v1(trials)
        second = build_research_trial_ledger_v1(trials)
        reversed_order = build_research_trial_ledger_v1(list(reversed(trials)))
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.content_hash, reversed_order.content_hash)
        self.assertEqual(first.ledger_id, second.ledger_id)
        self.assertEqual(len(first.trials), 3)


def _dominant_block_matrix() -> tuple[list[list[tuple[Decimal, ...]]], list[str]]:
    pattern = (Decimal("-0.01"), Decimal("0.00"), Decimal("0.01"), Decimal("0.00"), Decimal("0.01"))
    trial_blocks: list[list[tuple[Decimal, ...]]] = []
    identities: list[str] = []
    for index in range(6):
        centre = Decimal("0.10") + Decimal(index) / Decimal("100")
        block = tuple(centre + delta for delta in pattern)
        trial_blocks.append([block for _ in range(8)])
        identities.append(_hash_id(f"trial-{index:02d}"))
    return trial_blocks, identities


def _reference_pbo(trial_blocks: list[list[tuple[Decimal, ...]]], identities: list[str]) -> tuple[int, int]:
    def sharpe(values: list[float]) -> float | None:
        if len(values) < 2:
            return None
        deviation = stdev(values)
        if deviation == 0:
            return None
        return mean(values) / deviation

    trial_count = len(trial_blocks)
    valid = 0
    lambda_le_zero = 0
    for combo in combinations(range(8), 4):
        oos = tuple(index for index in range(8) if index not in combo)
        is_sharpes = [
            sharpe([float(value) for block in combo for value in trial_blocks[trial][block]])
            for trial in range(trial_count)
        ]
        oos_sharpes = [
            sharpe([float(value) for block in oos for value in trial_blocks[trial][block]])
            for trial in range(trial_count)
        ]
        if any(value is None for value in is_sharpes + oos_sharpes):
            continue
        valid += 1
        finite_is = [value for value in is_sharpes if value is not None]
        finite_oos = [value for value in oos_sharpes if value is not None]
        best = max(finite_is)
        winners = [trial for trial in range(trial_count) if finite_is[trial] == best]
        winner = min(winners, key=lambda trial: identities[trial])
        ranks = _midranks_ascending(finite_oos)
        omega = ranks[winner] / (trial_count + 1)
        if log(omega / (1 - omega)) <= 0:
            lambda_le_zero += 1
    return valid, lambda_le_zero


class CanonicalCscvPboTests(unittest.TestCase):
    def test_seventy_splits_and_dominant_trial_pbo_zero(self) -> None:
        trial_blocks, identities = _dominant_block_matrix()
        evidence = evaluate_canonical_cscv_pbo_v1(trial_blocks=trial_blocks, trial_identities=identities)
        self.assertEqual(evidence.status, "AVAILABLE")
        self.assertEqual(evidence.number_of_splits, 70)
        self.assertEqual(evidence.number_of_valid_splits, 70)
        self.assertEqual(evidence.pbo, Decimal("0"))

    def test_matches_independent_reference(self) -> None:
        trial_blocks: list[list[tuple[Decimal, ...]]] = []
        identities: list[str] = []
        base_pattern = (Decimal("-0.01"), Decimal("0.005"), Decimal("0.01"), Decimal("-0.005"), Decimal("0.008"))
        for trial in range(6):
            blocks: list[tuple[Decimal, ...]] = []
            for block_index in range(8):
                centre = Decimal(str(0.001 * ((trial * 7 + block_index * 3) % 11)))
                blocks.append(tuple(centre + delta for delta in base_pattern))
            trial_blocks.append(blocks)
            identities.append(_hash_id(f"mixed-{trial:02d}"))
        evidence = evaluate_canonical_cscv_pbo_v1(trial_blocks=trial_blocks, trial_identities=identities)
        valid, lambda_le_zero = _reference_pbo(trial_blocks, identities)
        self.assertEqual(evidence.number_of_valid_splits, valid)
        if valid == 70:
            self.assertEqual(evidence.pbo, Decimal(lambda_le_zero) / Decimal("70"))
        else:
            self.assertEqual(evidence.status, "UNAVAILABLE")

    def test_zero_variance_split_makes_pbo_unavailable(self) -> None:
        trial_blocks, identities = _dominant_block_matrix()
        trial_blocks[0] = [tuple(Decimal("0.05") for _ in range(5)) for _ in range(8)]
        evidence = evaluate_canonical_cscv_pbo_v1(trial_blocks=trial_blocks, trial_identities=identities)
        self.assertEqual(evidence.status, "UNAVAILABLE")
        self.assertIsNone(evidence.pbo)
        self.assertIn("invalid_splits_present", evidence.unavailable_reasons)

    def test_insufficient_trials_unavailable(self) -> None:
        trial_blocks, identities = _dominant_block_matrix()
        evidence = evaluate_canonical_cscv_pbo_v1(trial_blocks=trial_blocks[:5], trial_identities=identities[:5])
        self.assertEqual(evidence.status, "UNAVAILABLE")
        self.assertIn("insufficient_distinct_trials", evidence.unavailable_reasons)

    def test_duplicate_identity_unavailable_even_with_enough_rows(self) -> None:
        trial_blocks, identities = _dominant_block_matrix()
        seven_blocks = trial_blocks + [trial_blocks[0]]
        seven_identities = identities + [identities[0]]
        evidence = evaluate_canonical_cscv_pbo_v1(trial_blocks=seven_blocks, trial_identities=seven_identities)
        self.assertEqual(evidence.status, "UNAVAILABLE")
        self.assertIn("duplicate_trial_identity", evidence.unavailable_reasons)

    def test_non_canonical_identity_rejected(self) -> None:
        trial_blocks, identities = _dominant_block_matrix()
        bad_identities = list(identities)
        bad_identities[0] = "not-a-hash"
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "cscv_trial_identity_not_canonical_hash"):
            evaluate_canonical_cscv_pbo_v1(trial_blocks=trial_blocks, trial_identities=bad_identities)

    def test_misaligned_block_geometry_unavailable(self) -> None:
        trial_blocks, identities = _dominant_block_matrix()
        misaligned = [list(blocks) for blocks in trial_blocks]
        misaligned[0][0] = misaligned[0][0] + (Decimal("0.02"),)
        evidence = evaluate_canonical_cscv_pbo_v1(trial_blocks=misaligned, trial_identities=identities)
        self.assertEqual(evidence.status, "UNAVAILABLE")
        self.assertIn("block_observation_count_misaligned_across_trials", evidence.unavailable_reasons)

    def test_is_winner_deterministic_tie_break(self) -> None:
        self.assertEqual(_select_is_winner([2.0, 2.0, 1.0], ["bbb", "aaa", "zzz"]), 1)
        self.assertEqual(_select_is_winner([1.0, 3.0, 3.0], ["aaa", "ccc", "bbb"]), 2)

    def test_oos_midrank(self) -> None:
        self.assertEqual(_midranks_ascending([3.0, 1.0, 2.0, 1.0]), [4.0, 1.5, 3.0, 1.5])


def _dsr_ledger() -> tuple[object, object]:
    run = _fake_run(content_hash="a" * 64)
    selected_returns = tuple((Decimal("0.02"), Decimal("-0.01"), Decimal("0.03"))[index % 3] for index in range(30))
    selected_series = _series(run, selected_returns, "1" * 64)
    trials = [
        build_research_trial_v1(
            run=run, series=selected_series, trial_role=ResearchTrialRoleV1.BASELINE,
            disposition=ResearchTrialDispositionV1.SELECTED,
        )
    ]
    for index in range(1, 6):
        returns = tuple(Decimal(str(0.01 * (((index * 3 + step) % 5) - 2 + 0.5))) for step in range(30))
        series = _series(run, returns, f"{index:064d}")
        trials.append(
            build_research_trial_v1(
                run=run, series=series, trial_role=ResearchTrialRoleV1.NEIGHBOR,
                disposition=ResearchTrialDispositionV1.INSPECTED,
            )
        )
    ledger = build_research_trial_ledger_v1(trials)
    return ledger, trials[0]


class DeflatedSharpeTests(unittest.TestCase):
    def test_known_reference_vector(self) -> None:
        ledger, selected = _dsr_ledger()
        evidence = evaluate_deflated_sharpe_evidence_v1(ledger=ledger, selected_trial_id=selected.trial_id)
        self.assertEqual(evidence.status, "AVAILABLE")

        selected_returns = [float(value) for value in selected.daily_returns]
        trial_sharpes = []
        for trial in ledger.trials:
            values = [float(value) for value in trial.daily_returns]
            trial_sharpes.append(mean(values) / stdev(values))
        reference_sharpe = mean(selected_returns) / stdev(selected_returns)
        count = len(selected_returns)
        centred = [value - mean(selected_returns) for value in selected_returns]
        m2 = sum(value ** 2 for value in centred) / count
        m3 = sum(value ** 3 for value in centred) / count
        m4 = sum(value ** 4 for value in centred) / count
        g1 = m3 / (m2 ** 1.5)
        reference_skew = sqrt(count * (count - 1)) / (count - 2) * g1
        g2_excess = m4 / (m2 ** 2) - 3
        reference_kurt = ((count - 1) / ((count - 2) * (count - 3))) * ((count + 1) * g2_excess + 6) + 3
        trial_count = len(trial_sharpes)
        mu = mean(trial_sharpes)
        sigma = stdev(trial_sharpes)
        normal = NormalDist()
        euler = 0.5772156649015329
        from math import e as euler_e

        sr_star = mu + sigma * (
            (1 - euler) * normal.inv_cdf(1 - 1 / trial_count)
            + euler * normal.inv_cdf(1 - 1 / (trial_count * euler_e))
        )
        denominator = sqrt(1 - reference_skew * reference_sharpe + ((reference_kurt - 1) / 4) * reference_sharpe ** 2)
        z = (reference_sharpe - sr_star) * sqrt(count - 1) / denominator
        reference_dsr = normal.cdf(z)

        self.assertAlmostEqual(evidence.selected_sharpe, reference_sharpe, places=10)
        self.assertAlmostEqual(evidence.skewness, reference_skew, places=10)
        self.assertAlmostEqual(evidence.kurtosis, reference_kurt, places=10)
        self.assertAlmostEqual(evidence.trial_sharpe_mean, mu, places=10)
        self.assertAlmostEqual(evidence.trial_sharpe_standard_deviation, sigma, places=10)
        self.assertAlmostEqual(evidence.expected_maximum_sharpe, sr_star, places=10)
        self.assertAlmostEqual(evidence.denominator, denominator, places=10)
        self.assertAlmostEqual(evidence.z_statistic, z, places=10)
        self.assertAlmostEqual(evidence.deflated_sharpe, reference_dsr, places=10)

    def test_insufficient_observations_unavailable(self) -> None:
        run = _fake_run(content_hash="b" * 64)
        short_returns = (Decimal("0.01"), Decimal("-0.01"), Decimal("0.02"))
        selected_series = _series(run, short_returns, "2" * 64)
        trials = [
            build_research_trial_v1(
                run=run, series=selected_series, trial_role=ResearchTrialRoleV1.BASELINE,
                disposition=ResearchTrialDispositionV1.SELECTED,
            )
        ]
        for index in range(1, 6):
            series = _series(run, short_returns, f"{index:064d}")
            trials.append(
                build_research_trial_v1(
                    run=run, series=series, trial_role=ResearchTrialRoleV1.NEIGHBOR,
                    disposition=ResearchTrialDispositionV1.INSPECTED,
                )
            )
        ledger = build_research_trial_ledger_v1(trials)
        evidence = evaluate_deflated_sharpe_evidence_v1(ledger=ledger, selected_trial_id=trials[0].trial_id)
        self.assertEqual(evidence.status, "UNAVAILABLE")
        self.assertIn("insufficient_observations", evidence.unavailable_reasons)

    def test_insufficient_trials_unavailable(self) -> None:
        run = _fake_run(content_hash="c" * 64)
        selected_returns = tuple((Decimal("0.02"), Decimal("-0.01"), Decimal("0.03"))[index % 3] for index in range(30))
        selected_series = _series(run, selected_returns, "3" * 64)
        trial = build_research_trial_v1(
            run=run, series=selected_series, trial_role=ResearchTrialRoleV1.BASELINE,
            disposition=ResearchTrialDispositionV1.SELECTED,
        )
        ledger = build_research_trial_ledger_v1([trial])
        evidence = evaluate_deflated_sharpe_evidence_v1(ledger=ledger, selected_trial_id=trial.trial_id)
        self.assertEqual(evidence.status, "UNAVAILABLE")
        self.assertIn("insufficient_trials", evidence.unavailable_reasons)

    def test_heterogeneous_window_trials_rejected(self) -> None:
        run = _fake_run(content_hash="d" * 64)
        selected_returns = tuple((Decimal("0.02"), Decimal("-0.01"), Decimal("0.03"))[index % 3] for index in range(30))
        selected_series = _series(run, selected_returns, "4" * 64)
        trials = [
            build_research_trial_v1(
                run=run, series=selected_series, trial_role=ResearchTrialRoleV1.BASELINE,
                disposition=ResearchTrialDispositionV1.SELECTED,
            )
        ]
        for index in range(1, 6):
            other_run = _fake_run(content_hash=f"{index + 200:064d}")
            short_series = _series(other_run, tuple(Decimal("0.01") for _ in range(6)), f"{index + 300:064d}")
            trials.append(
                build_research_trial_v1(
                    run=other_run, series=short_series, trial_role=ResearchTrialRoleV1.NEIGHBOR,
                    disposition=ResearchTrialDispositionV1.INSPECTED,
                )
            )
        ledger = build_research_trial_ledger_v1(trials)
        evidence = evaluate_deflated_sharpe_evidence_v1(ledger=ledger, selected_trial_id=trials[0].trial_id)
        self.assertEqual(evidence.status, "UNAVAILABLE")
        self.assertIn("incomparable_trial_evaluation_window", evidence.unavailable_reasons)


class CircularShiftHelperTests(unittest.TestCase):
    def test_shift_preserves_multiset_and_rotation_structure(self) -> None:
        values = (Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"))
        shifted = circularly_shift_basis_values(values, 1)
        self.assertEqual(sorted(shifted), sorted(values))
        self.assertEqual(shifted, (Decimal("4"), Decimal("1"), Decimal("2"), Decimal("3")))

    def test_zero_shift_forbidden(self) -> None:
        values = (Decimal("1"), Decimal("2"), Decimal("3"))
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "zero_shift_forbidden"):
            circularly_shift_basis_values(values, 0)
        with self.assertRaisesRegex(OpenToOpenValidationV1Error, "zero_shift_forbidden"):
            circularly_shift_basis_values(values, 3)


def _null_control_run() -> tuple[BasisMeanReversionResearchRunV1, AuthoritativeTradableBarSeriesV2]:
    offsets_values = [(index, Decimal("-0.001") if index == 0 else Decimal("0")) for index in range(10)]
    opens = {offset: Decimal("100") + Decimal(offset) for offset in range(9)}
    bundle = _bundle(offsets_values)
    bar_series = _bar_series(minutes=9, opens=opens)
    evidence = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=bar_series)
    run = run_crypto_basis_mean_reversion_research(
        definition=_definition(horizon=1),
        evidence=evidence,
        instrument_kind=CryptoInstrumentKind.PERPETUAL,
        cost_model=ZERO_COST,
        cost_model_version="cost-model-v1",
    )
    return run, bar_series


def _independent_null_sharpe(
    run: BasisMeanReversionResearchRunV1,
    bar_series: AuthoritativeTradableBarSeriesV2,
    offset: int,
    window_start: datetime,
    window_end: datetime,
) -> float | None:
    from trade_platform.signed_price_return_v2 import compute_signed_open_to_open_return

    eligible = [decision for decision in run.decisions if window_start <= decision.decision_at < window_end]
    basis = tuple(decision.basis_value for decision in eligible)
    timestamps = [decision.decision_at for decision in eligible]
    shifted = circularly_shift_basis_values(basis, offset)
    cap = run.definition.maximum_absolute_exposure
    threshold = run.definition.basis_entry_threshold
    horizon = timedelta(minutes=run.definition.holding_horizon_bars)
    last_exit: datetime | None = None
    pairs: list[tuple[datetime, Decimal]] = []
    for index, decision_at in enumerate(timestamps):
        value = shifted[index]
        exposure = -cap if value > threshold else cap if value < -threshold else Decimal("0")
        if exposure == 0:
            continue
        if last_exit is not None and decision_at <= last_exit:
            continue
        entry = bar_series.first_eligible_bar_after(decision_at)
        if entry is None:
            continue
        exit_open_at = entry.bar_open_at + horizon
        exit_bar = next((bar for bar in bar_series.bars if bar.bar_open_at == exit_open_at), None)
        if exit_bar is None:
            continue
        computed = compute_signed_open_to_open_return(
            entry_bar=entry, exit_bar=exit_bar, exposure=exposure, maximum_absolute_exposure=cap, cost_model=ZERO_COST
        )
        pairs.append((computed.exit_time, computed.net_return))
        last_exit = exit_bar.bar_open_at
    days = (window_end - window_start).days
    buckets: dict[int, list[Decimal]] = {}
    for exit_time, net_return in pairs:
        index = (exit_time.astimezone(UTC).date() - window_start.date()).days
        if 0 <= index < days:
            buckets.setdefault(index, []).append(net_return)
    daily: list[Decimal] = []
    for index in range(days):
        compounded = Decimal("1")
        for net_return in buckets.get(index, ()):
            compounded *= Decimal("1") + net_return
        daily.append(compounded - Decimal("1"))
    return non_annualized_daily_sharpe(tuple(daily))


class CircularShiftNullControlTests(unittest.TestCase):
    def test_empirical_p_value_uses_valid_shifts_not_attempted(self) -> None:
        run, bar_series = _null_control_run()
        evidence = evaluate_circular_shift_null_control_v1(
            run=run,
            bar_series=bar_series,
            base_cost_model=ZERO_COST,
            window_start=START,
            window_end=START + timedelta(days=2),
            seed=11,
            minimum_valid_shifts=3,
        )
        self.assertEqual(evidence.status, "AVAILABLE")
        self.assertEqual(evidence.attempted_shifts, 9)
        self.assertEqual(evidence.valid_shifts, 6)
        self.assertEqual(evidence.unavailable_shifts, 3)
        self.assertNotEqual(evidence.valid_shifts, evidence.attempted_shifts)
        window_start = START
        window_end = START + timedelta(days=2)
        observed = non_annualized_daily_sharpe(
            build_realized_exit_daily_return_series_v1(
                run=run, window_start=window_start, window_end=window_end
            ).daily_returns
        )
        assert observed is not None
        valid_stats = [
            stat
            for offset in evidence.selected_offsets
            if (stat := _independent_null_sharpe(run, bar_series, offset, window_start, window_end)) is not None
        ]
        self.assertEqual(len(valid_stats), evidence.valid_shifts)
        greater_or_equal = sum(1 for stat in valid_stats if stat >= observed)
        expected_with_valid = Decimal(1 + greater_or_equal) / Decimal(1 + evidence.valid_shifts)
        expected_with_attempted = Decimal(1 + greater_or_equal) / Decimal(1 + evidence.attempted_shifts)
        self.assertEqual(evidence.empirical_p_value, expected_with_valid)
        self.assertNotEqual(evidence.empirical_p_value, expected_with_attempted)

    def test_minimum_valid_shift_gate(self) -> None:
        run, bar_series = _null_control_run()
        evidence = evaluate_circular_shift_null_control_v1(
            run=run,
            bar_series=bar_series,
            base_cost_model=ZERO_COST,
            window_start=START,
            window_end=START + timedelta(days=2),
            seed=11,
        )
        self.assertEqual(evidence.status, "UNAVAILABLE")
        self.assertIsNone(evidence.empirical_p_value)
        self.assertIn("insufficient_valid_shifts", evidence.unavailable_reasons)

    def test_deterministic_offset_selection_and_no_zero_shift(self) -> None:
        run, bar_series = _null_control_run()
        common = {
            "run": run,
            "bar_series": bar_series,
            "base_cost_model": ZERO_COST,
            "window_start": START,
            "window_end": START + timedelta(days=2),
            "target_null_runs": 3,
            "minimum_valid_shifts": 1,
        }
        first = evaluate_circular_shift_null_control_v1(seed=5, **common)
        again = evaluate_circular_shift_null_control_v1(seed=5, **common)
        other = evaluate_circular_shift_null_control_v1(seed=999, **common)
        self.assertEqual(first.selected_offsets, again.selected_offsets)
        self.assertEqual(first.content_hash, again.content_hash)
        self.assertEqual(len(first.selected_offsets), 3)
        self.assertNotIn(0, first.selected_offsets)
        self.assertNotEqual(first.selected_offsets, other.selected_offsets)

    def test_synthetic_null_does_not_mutate_feature_authority(self) -> None:
        run, bar_series = _null_control_run()
        run_hash_before = run.content_hash
        bars_before = bar_series.bars
        decisions_before = run.decisions
        evaluate_circular_shift_null_control_v1(
            run=run,
            bar_series=bar_series,
            base_cost_model=ZERO_COST,
            window_start=START,
            window_end=START + timedelta(days=2),
            seed=11,
            minimum_valid_shifts=3,
        )
        self.assertEqual(run.content_hash, run_hash_before)
        self.assertIs(run.decisions, decisions_before)
        self.assertIs(bar_series.bars, bars_before)
        import trade_platform.open_to_open_validation_v1 as module

        source = module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            contents = handle.read()
        self.assertNotIn("FeatureMaterializationV2", contents)
        self.assertNotIn("feature_authority", contents)

    def test_decisions_outside_window_do_not_affect_result(self) -> None:
        offsets_values = [(index, Decimal("-0.001") if index == 0 else Decimal("0")) for index in range(9)]
        opens = {offset: Decimal("100") + Decimal(offset) for offset in range(9)}
        bar_series = _bar_series(minutes=9, opens=opens)
        base_bundle = _bundle(offsets_values)
        base_evidence = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=base_bundle, bar_series=bar_series)
        base_run = run_crypto_basis_mean_reversion_research(
            definition=_definition(horizon=1), evidence=base_evidence,
            instrument_kind=CryptoInstrumentKind.PERPETUAL, cost_model=ZERO_COST, cost_model_version="cost-model-v1",
        )

        far_future_minutes = 2 * 24 * 60 + 60
        extended_offsets_values = offsets_values + [
            (far_future_minutes, Decimal("0.999")), (far_future_minutes + 1, Decimal("-0.999"))
        ]
        extended_bundle = _bundle(extended_offsets_values, decision_at=START + timedelta(days=3))
        extended_bar_series = _bar_series(minutes=9, opens=opens)
        extended_evidence = SubjectAwareTradableResearchEvidenceV2.create(
            feature_bundle=extended_bundle, bar_series=extended_bar_series
        )
        extended_run = run_crypto_basis_mean_reversion_research(
            definition=_definition(horizon=1), evidence=extended_evidence,
            instrument_kind=CryptoInstrumentKind.PERPETUAL, cost_model=ZERO_COST, cost_model_version="cost-model-v1",
        )

        window_start = START
        window_end = START + timedelta(days=2)
        common = {"base_cost_model": ZERO_COST, "window_start": window_start, "window_end": window_end, "seed": 11, "minimum_valid_shifts": 3}
        base_result = evaluate_circular_shift_null_control_v1(run=base_run, bar_series=bar_series, **common)
        extended_result = evaluate_circular_shift_null_control_v1(run=extended_run, bar_series=extended_bar_series, **common)
        self.assertEqual(base_result.eligible_feature_event_count, extended_result.eligible_feature_event_count)
        self.assertEqual(base_result.observed_sharpe, extended_result.observed_sharpe)
        self.assertEqual(base_result.empirical_p_value, extended_result.empirical_p_value)


class ReconciliationTests(unittest.TestCase):
    def test_clean_ledger_reconciles(self) -> None:
        run, bar_series = _two_trade_run(cost_model=NONZERO_COST)
        evidence = reconcile_open_to_open_trade_ledger_v1(
            run=run, bar_series=bar_series, base_cost_model=NONZERO_COST
        )
        self.assertEqual(evidence.status, "RECONCILED")
        self.assertEqual(evidence.mismatched_decision_count, 0)
        self.assertEqual(evidence.decision_count, len(run.decisions))

    def test_catches_field_corruption(self) -> None:
        run, bar_series = _two_trade_run(cost_model=NONZERO_COST)
        original_trade = run.executed_trades[0]
        corruptions = {
            "gross_return": Decimal("999"),
            "entry_cost": Decimal("999"),
            "exit_cost": Decimal("999"),
            "net_return": Decimal("999"),
            "entry_open": Decimal("999"),
            "exit_open": Decimal("999"),
            "exposure": -original_trade.exposure,
        }
        for field_name, bad_value in corruptions.items():
            corrupted_trade = dataclasses.replace(original_trade, **{field_name: bad_value})
            corrupted_decision = dataclasses.replace(run.decisions[0], trade=corrupted_trade)
            corrupted_run = dataclasses.replace(run, decisions=(corrupted_decision,) + run.decisions[1:])
            evidence = reconcile_open_to_open_trade_ledger_v1(
                run=corrupted_run, bar_series=bar_series, base_cost_model=NONZERO_COST
            )
            self.assertEqual(evidence.status, "BLOCKED", f"corruption {field_name} not caught")
            self.assertGreaterEqual(evidence.mismatched_decision_count, 1)

    def test_catches_missing_executed_trade(self) -> None:
        run, bar_series = _two_trade_run(cost_model=NONZERO_COST)
        corrupted_decision = dataclasses.replace(run.decisions[0], trade=None)
        corrupted_run = dataclasses.replace(run, decisions=(corrupted_decision,) + run.decisions[1:])
        evidence = reconcile_open_to_open_trade_ledger_v1(
            run=corrupted_run, bar_series=bar_series, base_cost_model=NONZERO_COST
        )
        self.assertEqual(evidence.status, "BLOCKED")
        self.assertIn("missing_executed_trade", evidence.checks[0].discrepancies)

    def test_catches_extra_executed_trade(self) -> None:
        run, bar_series = _run_with_flat_decision()
        flat_index = next(
            index for index, decision in enumerate(run.decisions)
            if decision.outcome is BasisMeanReversionOutcomeV1.FLAT
        )
        executed_trade = run.executed_trades[0]
        fabricated_trade = dataclasses.replace(executed_trade, decision_at=run.decisions[flat_index].decision_at)
        corrupted_decision = dataclasses.replace(
            run.decisions[flat_index], outcome=BasisMeanReversionOutcomeV1.EXECUTED, trade=fabricated_trade
        )
        decisions = list(run.decisions)
        decisions[flat_index] = corrupted_decision
        corrupted_run = dataclasses.replace(run, decisions=tuple(decisions))
        evidence = reconcile_open_to_open_trade_ledger_v1(
            run=corrupted_run, bar_series=bar_series, base_cost_model=ZERO_COST
        )
        self.assertEqual(evidence.status, "BLOCKED")
        self.assertIn("outcome", evidence.checks[flat_index].discrepancies)

    def test_catches_overlapping_executed_trade(self) -> None:
        run, bar_series = _run_with_overlap()
        self.assertEqual(run.decisions[1].outcome, BasisMeanReversionOutcomeV1.IGNORED_ACTIVE_TRADE)
        first_trade = run.decisions[0].trade
        assert first_trade is not None
        fabricated_trade = dataclasses.replace(
            first_trade,
            decision_at=run.decisions[1].decision_at,
            basis_value=run.decisions[1].basis_value,
            exposure=run.decisions[1].exposure,
        )
        corrupted_decision = dataclasses.replace(
            run.decisions[1], outcome=BasisMeanReversionOutcomeV1.EXECUTED, trade=fabricated_trade
        )
        corrupted_run = dataclasses.replace(run, decisions=(run.decisions[0], corrupted_decision) + run.decisions[2:])
        evidence = reconcile_open_to_open_trade_ledger_v1(
            run=corrupted_run, bar_series=bar_series, base_cost_model=ZERO_COST
        )
        self.assertEqual(evidence.status, "BLOCKED")
        self.assertIn("outcome", evidence.checks[1].discrepancies)


class DeterminismTests(unittest.TestCase):
    def test_cost_sensitivity_hash_changes_with_cost(self) -> None:
        run, _ = _two_trade_run(cost_model=NONZERO_COST)
        first = evaluate_open_to_open_cost_sensitivity_v1(run=run, base_cost_model=NONZERO_COST)
        second = evaluate_open_to_open_cost_sensitivity_v1(run=run, base_cost_model=NONZERO_COST)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.evidence_id, second.evidence_id)

    def test_no_persistence_or_new_table(self) -> None:
        import trade_platform.open_to_open_validation_v1 as module

        source = module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            contents = handle.read()
        self.assertNotIn("CREATE TABLE", contents.upper())
        self.assertNotIn("INSERT INTO", contents.upper())


if __name__ == "__main__":
    unittest.main()
