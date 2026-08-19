import unittest
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.domain import OrderIntent, OrderSide, OrderStatus, utc_now
from trade_platform.execution_quality import (
    ExecutionQualityPolicy,
    PaperEvidenceClass,
    PaperOperationsEvidenceError,
    build_shadow_rehearsal_evidence,
    evaluate_execution_quality,
)
from trade_platform.paper_execution import PaperOrder, progress_to_acknowledged
from trade_platform.paper_oms import RecordedFill
from trade_platform.shadow_mode import compare_paper_orders


class ExecutionQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = utc_now()
        self.policy = ExecutionQualityPolicy(
            "paper-quality:v1",
            Decimal("0.95"),
            Decimal("0.01"),
            Decimal("0.01"),
            5_000,
        )

    def _order(self, side: OrderSide = OrderSide.BUY) -> PaperOrder:
        intent = OrderIntent(
            uuid4(),
            uuid4(),
            str(uuid4()),
            "paper",
            side,
            Decimal(10),
            Decimal(100),
            self.now,
        )
        return progress_to_acknowledged(PaperOrder(intent))

    def test_terminal_fill_metrics_are_directional_and_explicitly_simulated(self) -> None:
        acknowledged = self._order()
        final = acknowledged.fill(Decimal(4), Decimal(100)).fill(Decimal(6), Decimal(101))
        fills = (
            RecordedFill(
                uuid4(),
                "fill-1",
                final.intent.intent_id,
                self.now + timedelta(seconds=1),
                Decimal(4),
                Decimal(100),
            ),
            RecordedFill(
                uuid4(),
                "fill-2",
                final.intent.intent_id,
                self.now + timedelta(seconds=2),
                Decimal(6),
                Decimal(101),
            ),
        )
        evidence = evaluate_execution_quality(
            order=final,
            fills=fills,
            arrival_price=Decimal(100),
            decision_price=Decimal(100),
            reference_source="deterministic-fixture-v1",
            policy=self.policy,
            evaluated_at=self.now + timedelta(seconds=3),
        )
        self.assertEqual(evidence.evidence_class, PaperEvidenceClass.SIMULATED_PAPER_REFERENCE)
        self.assertEqual(evidence.vwap, Decimal("100.6"))
        self.assertEqual(evidence.adverse_arrival_slippage_fraction, Decimal("0.006"))
        self.assertEqual(evidence.realized_shortfall_fraction, Decimal("0.006"))
        self.assertEqual(evidence.first_fill_latency_ms, 1_000)
        self.assertEqual(evidence.completion_latency_ms, 2_000)
        self.assertTrue(evidence.passed)
        self.assertIn("NOT_BROKER_SANDBOX", evidence.limitations[0])

    def test_cancelled_partial_fill_fails_fill_and_completion_thresholds(self) -> None:
        acknowledged = self._order(OrderSide.SELL)
        partial = acknowledged.fill(Decimal(4), Decimal(99)).transition(
            OrderStatus.CANCEL_PENDING
        ).transition(OrderStatus.CANCELLED)
        fills = (
            RecordedFill(
                uuid4(),
                "fill-partial",
                partial.intent.intent_id,
                self.now + timedelta(seconds=1),
                Decimal(4),
                Decimal(99),
            ),
        )
        evidence = evaluate_execution_quality(
            order=partial,
            fills=fills,
            arrival_price=Decimal(100),
            decision_price=Decimal(100),
            reference_source="deterministic-fixture-v1",
            policy=self.policy,
            evaluated_at=self.now + timedelta(seconds=2),
        )
        self.assertEqual(evidence.adverse_arrival_slippage_fraction, Decimal("0.01"))
        self.assertFalse(evidence.passed)
        self.assertEqual(
            evidence.breach_reasons,
            ("minimum_fill_ratio", "completion_latency_unavailable"),
        )

    def test_fill_set_must_exactly_reconcile_to_terminal_order(self) -> None:
        final = self._order().fill(Decimal(10), Decimal(100))
        with self.assertRaisesRegex(
            PaperOperationsEvidenceError, "execution_fill_quantity_mismatch"
        ):
            evaluate_execution_quality(
                order=final,
                fills=(),
                arrival_price=Decimal(100),
                decision_price=Decimal(100),
                reference_source="fixture",
                policy=self.policy,
                evaluated_at=self.now,
            )

    def test_shadow_rehearsal_carries_non_activation_limitations(self) -> None:
        primary = self._order().fill(Decimal(10), Decimal(100))
        shadow = self._order().fill(Decimal(9), Decimal(102))
        comparison = compare_paper_orders(primary, shadow)
        evidence = build_shadow_rehearsal_evidence(
            comparison, campaign_reference="cycle214-fixture"
        )
        self.assertTrue(evidence.requires_incident)
        self.assertIn("DOES_NOT_SATISFY", evidence.limitations[-1])


if __name__ == "__main__":
    unittest.main()
