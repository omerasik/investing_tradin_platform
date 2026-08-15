import unittest
from decimal import Decimal

from trade_platform.research import (
    BreakoutStrategy,
    CostModel,
    FeatureDefinition,
    FeatureRegistry,
    MeanReversionStrategy,
    MomentumStrategy,
    MovingAverageCrossStrategy,
    SQLiteExperimentStore,
    WalkForwardProtocol,
    chronological_splits,
    inverse_risk_allocate,
    performance_report,
    run_vectorized_backtest,
    simple_moving_average,
)


class ResearchTests(unittest.TestCase):
    def test_feature_registry_versions_definitions(self) -> None:
        registry = FeatureRegistry(); definition = FeatureDefinition("sma", "v1", 3, "close_at_t", "null_until_ready", "prefix_invariance")
        registry.register(definition)
        self.assertEqual(registry.get("sma", "v1"), definition)

    def test_moving_average_is_prefix_invariant(self) -> None:
        prefix = [Decimal(value) for value in ("1", "2", "3", "4")]
        extended = prefix + [Decimal("100")]
        self.assertEqual(simple_moving_average(prefix, 3), simple_moving_average(extended, 3)[:4])

    def test_vectorized_engine_applies_signal_next_period_only(self) -> None:
        closes = [Decimal(value) for value in ("100", "110", "121")]
        result = run_vectorized_backtest(closes, [Decimal("1"), Decimal("0"), Decimal("0")], CostModel())
        self.assertEqual(result.total_return, Decimal("0.1"))

    def test_costs_reduce_result_and_turnover_is_recorded(self) -> None:
        closes = [Decimal(value) for value in ("100", "110", "121")]
        result = run_vectorized_backtest(closes, [Decimal("1"), Decimal("0"), Decimal("0")], CostModel(percentage_per_turnover=Decimal("0.01")))
        self.assertEqual(result.turnover, Decimal("2"))
        self.assertLess(result.total_return, Decimal("0.1"))

    def test_baseline_strategy_requires_ordered_windows(self) -> None:
        with self.assertRaises(ValueError):
            MovingAverageCrossStrategy("v1", 5, 5).signals([Decimal("1")] * 6)

    def test_standard_report_and_experiment_are_versioned(self) -> None:
        result = run_vectorized_backtest([Decimal("100"), Decimal("110"), Decimal("121")], [Decimal("1"), Decimal("0"), Decimal("0")], CostModel())
        report = performance_report(result)
        store = SQLiteExperimentStore()
        experiment = store.append("ma-v1", "fixture-v1", ("sma-v1",), "cost-v1", {"fast": 2}, report)
        loaded = store.get(experiment.experiment_id)
        self.assertEqual(report["periods"], 2)
        self.assertEqual(loaded.report["total_return"], "0.10")

    def test_backtest_launch_is_idempotent_and_uses_transparent_family(self) -> None:
        store = SQLiteExperimentStore(); closes = tuple(Decimal(value) for value in ("10", "11", "12", "13", "14"))
        launch = {"strategy_version": "momentum-v1", "family": "momentum", "dataset_version": "fixture-v1", "feature_versions": ("return:v1",), "cost_model_version": "cost-v1", "parameters": {"lookback": 2}, "closes": closes, "costs": CostModel(), "idempotency_key": "launch-1", "actor": "operator"}
        first = store.launch(**launch); self.assertEqual(store.launch(**launch), first)
        with self.assertRaisesRegex(ValueError, "idempotency_conflict"):
            store.launch(**{**launch, "parameters": {"lookback": 3}})
        with self.assertRaisesRegex(ValueError, "unsupported_baseline"):
            store.launch(**{**launch, "idempotency_key": "launch-2", "family": "factor"})

    def test_backtest_launch_persists_a_pessimistic_cost_scenario(self) -> None:
        store = SQLiteExperimentStore(); closes = tuple(Decimal(value) for value in ("10", "11", "12", "13", "14"))
        experiment = store.launch(strategy_version="momentum-v1", family="momentum", dataset_version="fixture-v1", feature_versions=("return:v1",), cost_model_version="cost-v1", parameters={"lookback": 2}, closes=closes, costs=CostModel(percentage_per_turnover=Decimal("0.01")), idempotency_key="pessimistic-1", actor="operator", pessimistic_cost_multiplier=Decimal("3"))
        self.assertEqual(experiment.report["pessimistic_cost_multiplier"], "3")
        self.assertLess(Decimal(experiment.report["pessimistic_total_return"]), Decimal(experiment.report["total_return"]))
        with self.assertRaisesRegex(ValueError, "invalid_pessimistic"):
            store.launch(strategy_version="momentum-v1", family="momentum", dataset_version="fixture-v1", feature_versions=("return:v1",), cost_model_version="cost-v1", parameters={"lookback": 2}, closes=closes, costs=CostModel(), idempotency_key="pessimistic-2", actor="operator", pessimistic_cost_multiplier=Decimal("0.9"))

    def test_backtest_launch_retains_independent_and_non_overlapping_walk_forward_evidence(self) -> None:
        store = SQLiteExperimentStore()
        closes = tuple(Decimal(str(value)) for value in range(100, 145))
        protocol = WalkForwardProtocol(train_size=10, validation_size=4, test_size=5, step=5, purge=1, embargo=1)
        experiment = store.launch(
            strategy_version="momentum-v1", family="momentum", dataset_version="fixture-v2",
            feature_versions=("return:v1",), cost_model_version="cost-v1", parameters={"lookback": 2},
            closes=closes, costs=CostModel(percentage_per_turnover=Decimal("0.001")),
            idempotency_key="validated-launch", actor="operator", walk_forward_protocol=protocol,
        )
        self.assertEqual(experiment.report["independent_bar_engine_reconciled"], "1")
        self.assertEqual(experiment.report["independent_bar_engine_differences"], "")
        self.assertEqual(experiment.report["walk_forward_status"], "EXECUTED")
        self.assertGreater(int(experiment.report["walk_forward_splits"]), 0)
        self.assertEqual(int(experiment.report["walk_forward_held_out_periods"]) % 5, 0)
        self.assertEqual(store.get(experiment.experiment_id).report, experiment.report)

    def test_walk_forward_protocol_rejects_overlapping_or_insufficient_evidence(self) -> None:
        store = SQLiteExperimentStore(); closes = tuple(Decimal(str(value)) for value in range(100, 125))
        with self.assertRaisesRegex(ValueError, "overlaps"):
            store.launch(strategy_version="momentum-v1", family="momentum", dataset_version="fixture-v1", feature_versions=("return:v1",), cost_model_version="cost-v1", parameters={"lookback": 2}, closes=closes, costs=CostModel(), idempotency_key="overlap", actor="operator", walk_forward_protocol=WalkForwardProtocol(10, 3, 5, 4))
        with self.assertRaisesRegex(ValueError, "walk_forward_validation_failed"):
            store.launch(strategy_version="momentum-v1", family="momentum", dataset_version="fixture-v1", feature_versions=("return:v1",), cost_model_version="cost-v1", parameters={"lookback": 2}, closes=closes, costs=CostModel(), idempotency_key="short", actor="operator", walk_forward_protocol=WalkForwardProtocol(20, 3, 5, 5))

    def test_transparent_baseline_strategy_signals(self) -> None:
        closes = [Decimal(value) for value in ("10", "9", "8", "11", "12")]
        self.assertEqual(BreakoutStrategy("v1", 2).signals(closes), [Decimal("0"), Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1")])
        self.assertEqual(MomentumStrategy("v1", 2).signals(closes), [Decimal("0"), Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1")])
        self.assertEqual(MeanReversionStrategy("v1", 2, Decimal("0.05")).signals(closes)[2], Decimal("1"))

    def test_chronological_splits_never_overlap_future_test_data(self) -> None:
        splits = chronological_splits(20, 8, 4, 4, 2)
        self.assertEqual((splits[0].train_end, splits[0].validation_end, splits[0].test_end), (8, 12, 16))
        self.assertTrue(all(item.train_end < item.validation_end < item.test_end for item in splits))

    def test_inverse_risk_allocation_is_bounded_and_normalized(self) -> None:
        allocation = inverse_risk_allocate({"trend": Decimal("0.2"), "mean": Decimal("0.3")}, Decimal("0.7"))
        self.assertEqual(sum(allocation.weights.values(), Decimal("0")), Decimal("1"))
        with self.assertRaises(ValueError):
            inverse_risk_allocate({"trend": Decimal("0.1"), "mean": Decimal("1")}, Decimal("0.5"))


if __name__ == "__main__":
    unittest.main()
