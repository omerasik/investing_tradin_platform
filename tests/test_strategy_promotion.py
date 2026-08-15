import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from tests.test_strategy_validation import card
from trade_platform.cross_engine import (
    CrossEngineReport,
    GoldenExecutionScenario,
    GoldenMarketBar,
    run_realistic_golden_vector_event_reconciliation,
)
from trade_platform.research import CostModel, MovingAverageCrossStrategy
from trade_platform.research_validation import benjamini_hochberg, run_purged_walk_forward
from trade_platform.strategy_promotion import (
    PromotionDecision,
    PromotionPolicy,
    PromotionStatus,
    SQLitePromotionLedger,
    StrategyActivation,
    evaluate_promotion,
)
from trade_platform.strategy_validation import purged_walk_forward_splits


class StrategyPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_card = card()
        closes = [Decimal(str(value)) for value in range(100, 140)]
        splits = purged_walk_forward_splits(len(closes), train_size=10, validation_size=5, test_size=5, step=5, purge=2, embargo=1)
        self.walk_forward = tuple(run_purged_walk_forward(closes, MovingAverageCrossStrategy("ma-v1", 2, 4).signals, splits, CostModel()))
        self.cross_engine = CrossEngineReport(True, (), Decimal("1"), Decimal("1"))
        self.multiple_testing = benjamini_hochberg({self.run_card.strategy_version: Decimal("0.01")})
        self.policy = PromotionPolicy(minimum_held_out_periods=20)
        bars = tuple(GoldenMarketBar(datetime(2025, 1, 1 + index, tzinfo=timezone.utc), Decimal("100") + Decimal(index), Decimal("100") + Decimal(index), Decimal("100") + Decimal(index), Decimal("10")) for index in range(4))
        self.golden = run_realistic_golden_vector_event_reconciliation(dataset_version="golden-v1", strategy_version=self.run_card.strategy_version, instrument_id="US:NYSE:SPY", bars=bars, signals=(Decimal("1"), Decimal("1"), Decimal("1"), Decimal("0")), scenario=GoldenExecutionScenario("parity-v1"))

    def test_missing_evidence_or_reconciliation_blocks_promotion(self) -> None:
        decision = evaluate_promotion(self.run_card, self.walk_forward, CrossEngineReport(False, ("fee",), Decimal("1"), Decimal("1")), self.multiple_testing, Decimal("0.99"), {"capacity": "capacity-1"}, self.policy)
        self.assertEqual(decision.status, PromotionStatus.BLOCKED)
        self.assertIn("cross_engine_reconciliation_failed", decision.reasons)
        self.assertIn("unresolved_persisted_validation_package", decision.reasons)

    def test_manually_supplied_evidence_ids_do_not_satisfy_promotion(self) -> None:
        decision = evaluate_promotion(self.run_card, self.walk_forward, self.cross_engine, self.multiple_testing, Decimal("0.99"), {"capacity": "capacity-1", "data_quality": "dq-1", "stress": "stress-1"}, self.policy, golden_run=self.golden)
        self.assertEqual(decision.status, PromotionStatus.BLOCKED)
        self.assertIn("unresolved_persisted_validation_package", decision.reasons)
        with TemporaryDirectory() as directory:
            ledger = SQLitePromotionLedger(Path(directory) / "promotion.sqlite")
            ledger.append(decision)
            self.assertEqual(ledger.get(decision.decision_id), decision)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                ledger.append(decision)
            ledger.close()

    def test_only_reviewed_matching_promotion_can_enable_strategy_point_in_time(self) -> None:
        reviewed = PromotionDecision(uuid4(), self.run_card.strategy_id, self.run_card.strategy_version, PromotionStatus.REVIEW_REQUIRED, (), 20, Decimal(".1"))
        blocked = evaluate_promotion(self.run_card, self.walk_forward, CrossEngineReport(False, (), Decimal("1"), Decimal("1")), self.multiple_testing, Decimal("0.99"), {"capacity": "capacity-1", "data_quality": "dq-1", "stress": "stress-1"}, self.policy)
        with TemporaryDirectory() as directory:
            ledger = SQLitePromotionLedger(Path(directory) / "promotion.sqlite")
            ledger.append(reviewed); ledger.append(blocked)
            at = reviewed.decided_at
            self.assertFalse(ledger.strategy_enabled_as_of(self.run_card.strategy_version, at))
            activation = StrategyActivation(uuid4(), self.run_card.strategy_version, True, "reviewer", at, at, reviewed.decision_id)
            ledger.append_activation(activation)
            self.assertTrue(ledger.strategy_enabled_as_of(self.run_card.strategy_version, at))
            with self.assertRaisesRegex(ValueError, "reviewable"):
                ledger.append_activation(StrategyActivation(uuid4(), self.run_card.strategy_version, True, "reviewer", at, at, blocked.decision_id))
            ledger.append_activation(StrategyActivation(uuid4(), self.run_card.strategy_version, False, "operator", at + timedelta(seconds=1), at + timedelta(seconds=1)))
            self.assertFalse(ledger.strategy_enabled_as_of(self.run_card.strategy_version, at + timedelta(seconds=1)))
            ledger.close()

    def test_missing_or_unexplained_golden_execution_blocks_promotion(self) -> None:
        missing = evaluate_promotion(self.run_card, self.walk_forward, self.cross_engine, self.multiple_testing, Decimal("0.99"), {"capacity": "capacity-1", "data_quality": "dq-1", "stress": "stress-1"}, self.policy)
        self.assertIn("missing_golden_execution_evidence", missing.reasons)
        rejected = evaluate_promotion(self.run_card, self.walk_forward, self.cross_engine, self.multiple_testing, Decimal("0.99"), {"capacity": "capacity-1", "data_quality": "dq-1", "stress": "stress-1"}, self.policy, golden_run=replace(self.golden, reconciled=False, unexplained_differences=("final_equity_mismatch",)))
        self.assertIn("golden_execution_unexplained_divergence", rejected.reasons)


if __name__ == "__main__":
    unittest.main()
