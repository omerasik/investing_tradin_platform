from decimal import Decimal
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from trade_platform.cross_engine import CrossEngineReport
from trade_platform.research import CostModel, MovingAverageCrossStrategy
from trade_platform.research_validation import benjamini_hochberg, run_purged_walk_forward
from trade_platform.strategy_promotion import PromotionPolicy, PromotionStatus, SQLitePromotionLedger, StrategyActivation, evaluate_promotion
from trade_platform.strategy_validation import purged_walk_forward_splits
from tests.test_strategy_validation import card


class StrategyPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_card = card()
        closes = [Decimal(str(value)) for value in range(100, 140)]
        splits = purged_walk_forward_splits(len(closes), train_size=10, validation_size=5, test_size=5, step=5, purge=2, embargo=1)
        self.walk_forward = tuple(run_purged_walk_forward(closes, MovingAverageCrossStrategy("ma-v1", 2, 4).signals, splits, CostModel()))
        self.cross_engine = CrossEngineReport(True, (), Decimal("1"), Decimal("1"))
        self.multiple_testing = benjamini_hochberg({self.run_card.strategy_version: Decimal("0.01")})
        self.policy = PromotionPolicy(minimum_held_out_periods=20)

    def test_missing_evidence_or_reconciliation_blocks_promotion(self) -> None:
        decision = evaluate_promotion(self.run_card, self.walk_forward, CrossEngineReport(False, ("fee",), Decimal("1"), Decimal("1")), self.multiple_testing, Decimal("0.99"), {"capacity": "capacity-1"}, self.policy)
        self.assertEqual(decision.status, PromotionStatus.BLOCKED)
        self.assertIn("cross_engine_reconciliation_failed", decision.reasons)
        self.assertIn("missing_stress_evidence", decision.reasons)

    def test_complete_evidence_requires_review_and_is_durable(self) -> None:
        decision = evaluate_promotion(self.run_card, self.walk_forward, self.cross_engine, self.multiple_testing, Decimal("0.99"), {"capacity": "capacity-1", "data_quality": "dq-1", "stress": "stress-1"}, self.policy)
        self.assertEqual(decision.status, PromotionStatus.REVIEW_REQUIRED)
        self.assertEqual(decision.reasons, ())
        with TemporaryDirectory() as directory:
            ledger = SQLitePromotionLedger(Path(directory) / "promotion.sqlite")
            ledger.append(decision)
            self.assertEqual(ledger.get(decision.decision_id), decision)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                ledger.append(decision)
            ledger.close()

    def test_only_reviewed_matching_promotion_can_enable_strategy_point_in_time(self) -> None:
        reviewed = evaluate_promotion(self.run_card, self.walk_forward, self.cross_engine, self.multiple_testing, Decimal("0.99"), {"capacity": "capacity-1", "data_quality": "dq-1", "stress": "stress-1"}, self.policy)
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


if __name__ == "__main__":
    unittest.main()
