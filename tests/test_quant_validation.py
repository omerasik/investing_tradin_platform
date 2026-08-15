import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5

from tests.test_strategy_validation import card
from trade_platform.cross_engine import CrossEngineReport
from trade_platform.quant_validation import (
    ParameterResult,
    QuantValidationError,
    SQLiteValidationEvidenceStore,
    build_validation_package,
    evaluate_bootstrap,
    evaluate_capacity,
    evaluate_latency_sensitivity,
    evaluate_monte_carlo_trade_sequence,
    evaluate_multiple_testing,
    evaluate_parameter_stability,
    evaluate_slippage_sensitivity,
    evaluate_stress,
    record_external_evidence,
)
from trade_platform.research import CostModel, MovingAverageCrossStrategy
from trade_platform.research_validation import run_purged_walk_forward
from trade_platform.strategy_promotion import (
    PromotionPolicy,
    PromotionStatus,
    evaluate_promotion_package,
)
from trade_platform.strategy_validation import purged_walk_forward_splits


class QuantValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.closes = tuple(Decimal(value) for value in range(100, 141))
        self.signals = tuple(Decimal("1") for _ in self.closes)
        self.costs = CostModel(Decimal(".001"), Decimal(".002"), Decimal(".001"))
        self.timestamps = tuple(datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=index) for index in range(len(self.closes)))

    def test_capacity_is_ohlcv_estimate_and_high_participation_fails(self) -> None:
        evidence = evaluate_capacity(strategy_version="trend-v1", dataset_version="daily-v1", instrument_or_universe="US:SPY",
            closes=self.closes, signals=self.signals, base_costs=self.costs, capital_levels=(Decimal("1000"), Decimal("100000000")),
            average_daily_volume=Decimal("1000"), order_participation_limit=Decimal(".1"), spread_assumption=Decimal(".001"),
            spread_deterioration_assumption=Decimal(".001"), impact_coefficient=Decimal(".01"), market_impact_model_version="sqrt-v1", liquidity_assumptions="daily ADV")
        self.assertEqual(evidence.data_quality, "OHLCV_ESTIMATE")
        self.assertTrue(evidence.levels[0].passed)
        self.assertFalse(evidence.levels[-1].passed)
        self.assertLess(evidence.levels[-1].post_impact_return, evidence.levels[0].post_impact_return)

    def test_slippage_and_spread_monotonically_reduce_returns(self) -> None:
        evidence = evaluate_slippage_sensitivity(strategy_version="trend-v1", dataset_version="daily-v1", cost_model_version="cost-v1",
            closes=self.closes, signals=self.signals, costs=self.costs, minimum_economic_return=Decimal("0"))
        by_name = {item.name: item for item in evidence.scenarios}
        self.assertLess(by_name["slippage_3x"].total_return, by_name["base"].total_return)
        self.assertLess(by_name["wider_spread"].total_return, by_name["base"].total_return)

    def test_latency_uses_bars_and_records_missed_or_changed_fills(self) -> None:
        signals = tuple(Decimal("1") if index in {1, len(self.closes) - 1} else Decimal("0") for index in range(len(self.closes)))
        evidence = evaluate_latency_sensitivity(strategy_version="trend-v1", dataset_version="daily-v1", closes=self.closes,
            signals=signals, timestamps=self.timestamps, costs=self.costs, data_frequency=timedelta(days=1),
            latency_levels=(timedelta(days=1), timedelta(days=2)), maximum_return_degradation=Decimal("1"))
        self.assertEqual(evidence.scenarios[0].executable_timestamp, self.timestamps[2])
        self.assertGreater(evidence.scenarios[-1].missed_fills, 0)
        self.assertGreater(evidence.scenarios[0].changed_fills, 0)

    def test_bootstrap_and_monte_carlo_are_seeded_and_path_analysis_is_disclosed(self) -> None:
        returns = (Decimal(".1"), Decimal("-.08"), Decimal(".03"), Decimal("-.01"))
        first = evaluate_bootstrap(strategy_version="trend-v1", dataset_version="daily-v1", period_returns=returns, seed=7, resamples=30)
        second = evaluate_bootstrap(strategy_version="trend-v1", dataset_version="daily-v1", period_returns=returns, seed=7, resamples=30)
        self.assertEqual(first.return_distribution, second.return_distribution)
        monte = evaluate_monte_carlo_trade_sequence(strategy_version="trend-v1", dataset_version="daily-v1", trade_returns=returns, seed=9, simulations=30)
        self.assertIn("not a forecast", monte.limitations[0])
        self.assertEqual(len(monte.maximum_drawdown_distribution), 30)

    def test_fragile_parameter_peak_fails_stability(self) -> None:
        selected = (("lookback", "10"),)
        evidence = evaluate_parameter_stability(strategy_version="trend-v1", dataset_version="daily-v1", selected_parameters=selected,
            results=(ParameterResult((("lookback", "9"),), Decimal(".01"), Decimal(".1")), ParameterResult(selected, Decimal(".5"), Decimal("2")), ParameterResult((("lookback", "11"),), Decimal(".01"), Decimal(".1"))))
        self.assertTrue(evidence.narrow_performance_spike)
        self.assertFalse(evidence.passed)

    def test_multiple_testing_penalizes_many_trials_and_needs_holdout(self) -> None:
        weak = evaluate_multiple_testing(strategy_version="trend-v1", dataset_version="daily-v1", p_values={"trend-v1": Decimal(".01")}, observed_sharpe=Decimal("1"), holdout_passed=False,
            rejected_experiments=("x",), selected_experiments=("trend-v1",), parameter_combinations_tested=1000,
            datasets_or_universes_tested=2, feature_combinations_tested=1000, oos_results=(False, True))
        self.assertFalse(weak.passed)
        self.assertGreater(weak.backtest_overfitting_probability, Decimal("0"))

    def test_persisted_complete_package_only_reaches_review_required(self) -> None:
        run_card = card()
        dataset = run_card.required_datasets[0]
        with TemporaryDirectory() as directory:
            store = SQLiteValidationEvidenceStore(Path(directory) / "validation.sqlite")
            external = {name: record_external_evidence(evidence_type=name, strategy_version=run_card.strategy_version, dataset_version=dataset, passed=True)
                        for name in ("data_quality", "oos_walk_forward", "golden_reconciliation", "execution_realism", "scorecard")}
            capacity = evaluate_capacity(strategy_version=run_card.strategy_version, dataset_version=dataset, instrument_or_universe="US:SPY", closes=self.closes, signals=self.signals, base_costs=self.costs,
                capital_levels=(Decimal("1000"),), average_daily_volume=Decimal("10000000"), order_participation_limit=Decimal(".5"), spread_assumption=Decimal("0"), spread_deterioration_assumption=Decimal("0"), impact_coefficient=Decimal("0"), market_impact_model_version="sqrt-v1", liquidity_assumptions="fixture")
            slippage = evaluate_slippage_sensitivity(strategy_version=run_card.strategy_version, dataset_version=dataset, cost_model_version=run_card.cost_model_version, closes=self.closes, signals=self.signals, costs=self.costs, minimum_economic_return=Decimal("0"))
            latency = evaluate_latency_sensitivity(strategy_version=run_card.strategy_version, dataset_version=dataset, closes=self.closes, signals=self.signals, timestamps=self.timestamps, costs=self.costs, data_frequency=timedelta(days=1), latency_levels=(timedelta(days=1),), maximum_return_degradation=Decimal("1"))
            bootstrap = evaluate_bootstrap(strategy_version=run_card.strategy_version, dataset_version=dataset, period_returns=(Decimal(".01"), Decimal(".02")), seed=1, resamples=5)
            monte = evaluate_monte_carlo_trade_sequence(strategy_version=run_card.strategy_version, dataset_version=dataset, trade_returns=(Decimal(".01"), Decimal("-.005")), seed=1, simulations=5)
            stress = evaluate_stress(strategy_version=run_card.strategy_version, dataset_version=dataset, closes=self.closes, signals=self.signals, costs=self.costs, scenarios=(("sharp_equity_selloff", Decimal("-.1"), Decimal("1")),), minimum_return=Decimal("0"))
            stability = evaluate_parameter_stability(strategy_version=run_card.strategy_version, dataset_version=dataset, selected_parameters=(("lookback", "10"),), results=(ParameterResult((("lookback", "9"),), Decimal(".1"), Decimal("1")), ParameterResult((("lookback", "10"),), Decimal(".11"), Decimal("1")), ParameterResult((("lookback", "11"),), Decimal(".1"), Decimal("1"))))
            multiple = evaluate_multiple_testing(strategy_version=run_card.strategy_version, dataset_version=dataset, p_values={run_card.strategy_version: Decimal(".001")}, observed_sharpe=Decimal("3"), holdout_passed=True, rejected_experiments=(), selected_experiments=(run_card.strategy_version,), parameter_combinations_tested=1, datasets_or_universes_tested=1, feature_combinations_tested=1, oos_results=(True, True, True))
            evidence = {name: store.append(item) for name, item in external.items()}
            for name, item in (("capacity", capacity), ("slippage", slippage), ("latency", latency), ("bootstrap", bootstrap), ("monte_carlo", monte), ("stress", stress), ("parameter_stability", stability), ("multiple_testing", multiple)):
                evidence[name] = store.append(item)
            evidence_hashes = {
                name: str(store.get(artifact_id)["identity"]["content_hash"])
                for name, artifact_id in evidence.items()
            }
            strategy_version_id = uuid5(NAMESPACE_URL, f"strategy-version:{run_card.strategy_version}")
            dataset_id = uuid5(NAMESPACE_URL, f"dataset:{dataset}")
            dataset_version_id = uuid5(NAMESPACE_URL, f"dataset-version:{dataset}")
            package = build_validation_package(strategy_id=run_card.strategy_id, strategy_version_id=strategy_version_id,
                strategy_version=run_card.strategy_version, dataset_id=dataset_id, dataset_version_id=dataset_version_id,
                dataset_version=dataset, feature_versions=run_card.feature_versions, cost_model_version=run_card.cost_model_version,
                evidence_ids=evidence, evidence_hashes=evidence_hashes)
            package_id = store.append(package)
            self.assertEqual(store.append(package), package_id)
            splits = purged_walk_forward_splits(len(self.closes), train_size=10, validation_size=5, test_size=5, step=5, purge=1, embargo=1)
            held_out = tuple(run_purged_walk_forward(list(self.closes), MovingAverageCrossStrategy("ma", 2, 4).signals, splits, CostModel()))
            decision = evaluate_promotion_package(run_card=run_card, package_id=package_id, evidence_store=store, walk_forward_results=held_out, cross_engine_report=CrossEngineReport(True, (), Decimal("1"), Decimal("1")), probabilistic_sharpe=Decimal(".99"), policy=PromotionPolicy(minimum_held_out_periods=20))
            self.assertEqual(decision.status, PromotionStatus.REVIEW_REQUIRED)
            # Versions and failed persisted evidence are checked after lookup,
            # rather than trusting an ID supplied to the gate.
            for label, replacement, expected_reason in (
                ("wrong_strategy", record_external_evidence(evidence_type="golden_reconciliation", strategy_version="other-v1", dataset_version=dataset, passed=True), "golden_reconciliation_strategy_mismatch"),
                ("wrong_dataset", record_external_evidence(evidence_type="golden_reconciliation", strategy_version=run_card.strategy_version, dataset_version="other-data-v1", passed=True), "golden_reconciliation_dataset_mismatch"),
                ("failed_golden", record_external_evidence(evidence_type="golden_reconciliation", strategy_version=run_card.strategy_version, dataset_version=dataset, passed=False), "golden_reconciliation_failed"),
                ("weak_stress", record_external_evidence(evidence_type="stress", strategy_version=run_card.strategy_version, dataset_version=dataset, passed=False), "stress_evidence_weak"),
            ):
                changed = dict(evidence); changed["golden_reconciliation" if "golden" in label or label.startswith("wrong") else "stress"] = store.append(replacement)
                changed_hashes = {
                    name: str(store.get(artifact_id)["identity"]["content_hash"])
                    for name, artifact_id in changed.items()
                }
                changed_package = build_validation_package(strategy_id=run_card.strategy_id, strategy_version_id=strategy_version_id,
                    strategy_version=run_card.strategy_version, dataset_id=dataset_id, dataset_version_id=dataset_version_id,
                    dataset_version=dataset, feature_versions=run_card.feature_versions, cost_model_version=run_card.cost_model_version,
                    evidence_ids=changed, evidence_hashes=changed_hashes, limitations=(label,))
                blocked = evaluate_promotion_package(run_card=run_card, package_id=store.append(changed_package), evidence_store=store, walk_forward_results=held_out, cross_engine_report=CrossEngineReport(True, (), Decimal("1"), Decimal("1")), probabilistic_sharpe=Decimal(".99"), policy=PromotionPolicy(minimum_held_out_periods=20))
                self.assertEqual(blocked.status, PromotionStatus.BLOCKED)
                self.assertIn(expected_reason, blocked.reasons)
            self.assertFalse(hasattr(store, "submit"))
            store.close()

    def test_missing_or_wrong_persisted_evidence_blocks_promotion(self) -> None:
        # A package cannot be made without all named artefacts, which prevents
        # manually invented IDs from entering the promotion gate.
        with self.assertRaisesRegex(QuantValidationError, "complete_evidence"):
            build_validation_package(strategy_id=card().strategy_id, strategy_version_id=uuid5(NAMESPACE_URL, "strategy-version:trend-v1"),
                strategy_version="trend-v1", dataset_id=uuid5(NAMESPACE_URL, "dataset:daily"),
                dataset_version_id=uuid5(NAMESPACE_URL, "dataset-version:daily-v1"), dataset_version="daily-v1",
                feature_versions=("f-v1",), cost_model_version="cost-v1", evidence_ids={}, evidence_hashes={})


if __name__ == "__main__":
    unittest.main()
