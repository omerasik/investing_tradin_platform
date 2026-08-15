import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from trade_platform.strategy_validation import (
    SQLiteStrategyRegistry,
    StrategyRunCard,
    StrategyValidationError,
    purged_walk_forward_splits,
)


def card() -> StrategyRunCard:
    return StrategyRunCard.create(strategy_version="trend-v1", family="trend_following", hypothesis="A persistent trend survives base costs.", required_datasets=("bars-v1",), feature_versions=("return:v1",), universe_rules="Liquid ETFs only.", entry_logic="Buy positive trend.", exit_logic="Exit on reversal.", sizing_policy="Volatility target.", risk_policy="Independent limit policy v1.", cost_model_version="cost-v1", capacity_model="participation-v1", expected_regimes=("BULL", "BEAR"), parameter_schema={"lookback": "integer >= 20"}, failure_conditions=("Sideways whipsaw.",), limitations=("No short borrow model.",))


class StrategyValidationTests(unittest.TestCase):
    def test_run_card_is_complete_append_only_and_persistent(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "strategies.sqlite"
            registry = SQLiteStrategyRegistry(path)
            run_card = card(); registry.append(run_card); registry.close()
            reopened = SQLiteStrategyRegistry(path)
            try:
                self.assertEqual(reopened.get(run_card.strategy_id), run_card)
                with self.assertRaisesRegex(StrategyValidationError, "duplicate"):
                    reopened.append(run_card)
            finally:
                reopened.close()

    def test_run_card_and_purged_walk_forward_contracts_reject_leakage(self) -> None:
        incomplete = StrategyRunCard.create(strategy_version="x", family="x", hypothesis="x", required_datasets=(), feature_versions=(), universe_rules="x", entry_logic="x", exit_logic="x", sizing_policy="x", risk_policy="x", cost_model_version="x", capacity_model="x", expected_regimes=(), parameter_schema={}, failure_conditions=(), limitations=())
        with self.assertRaisesRegex(StrategyValidationError, "evidence"):
            incomplete.validate()
        splits = purged_walk_forward_splits(30, train_size=8, validation_size=4, test_size=4, step=4, purge=2, embargo=1)
        self.assertEqual((splits[0].train_end, splits[0].validation_start, splits[0].validation_end, splits[0].test_start), (8, 10, 14, 15))
        self.assertTrue(all(max(split.train_indices) < min(split.validation_indices) < min(split.test_indices) for split in splits))
        self.assertEqual(splits, purged_walk_forward_splits(31, train_size=8, validation_size=4, test_size=4, step=4, purge=2, embargo=1)[:len(splits)])
        with self.assertRaisesRegex(StrategyValidationError, "insufficient"):
            purged_walk_forward_splits(10, train_size=8, validation_size=4, test_size=4, step=1, purge=1, embargo=1)

