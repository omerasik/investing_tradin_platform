import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from tests.test_risk import approved_inputs
from trade_platform.domain import RiskDecisionType, RiskPolicy
from trade_platform.risk import (
    RiskEngine,
    SQLiteDailyNotionalLedger,
    SQLiteIdempotencyLedger,
    SQLiteRiskDecisionStore,
)


class RiskPersistenceTests(unittest.TestCase):
    def test_idempotency_claim_survives_restart(self) -> None:
        intent, signal, market, portfolio, execution = approved_inputs()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "risk.sqlite"
            first = SQLiteIdempotencyLedger(path)
            self.assertEqual(RiskEngine(RiskPolicy(), idempotency_ledger=first).assess(intent, signal, market, portfolio, execution).decision, RiskDecisionType.APPROVE)
            first.close()
            second = SQLiteIdempotencyLedger(path)
            repeated = RiskEngine(RiskPolicy(), idempotency_ledger=second).assess(intent, signal, market, portfolio, execution)
            self.assertEqual(repeated.reasons, ("duplicate_intent",))
            second.close()

    def test_daily_notional_limit_is_durable_and_decisions_are_auditable(self) -> None:
        first_intent, signal, market, portfolio, execution = approved_inputs()
        second_intent = replace(first_intent, intent_id=uuid4())
        with TemporaryDirectory() as directory:
            path = Path(directory) / "risk.sqlite"
            ids = SQLiteIdempotencyLedger(path); daily = SQLiteDailyNotionalLedger(path); decisions = SQLiteRiskDecisionStore(path)
            policy = RiskPolicy(maximum_daily_order_notional=first_intent.notional)
            engine = RiskEngine(policy, idempotency_ledger=ids, daily_notional_ledger=daily, decision_store=decisions)
            approved = engine.assess(first_intent, signal, market, portfolio, execution)
            blocked = engine.assess(second_intent, signal, market, portfolio, execution)
            self.assertEqual(approved.decision, RiskDecisionType.APPROVE)
            self.assertEqual(blocked.reasons, ("daily_order_notional_limit",))
            self.assertEqual(decisions.get(approved.decision_id), approved)
            ids.close(); daily.close(); decisions.close()

    def test_rejected_intent_does_not_reserve_daily_budget(self) -> None:
        rejected_intent, signal, market, portfolio, execution = approved_inputs()
        accepted_intent = replace(rejected_intent, intent_id=uuid4())
        with TemporaryDirectory() as directory:
            path = Path(directory) / "risk.sqlite"
            ids = SQLiteIdempotencyLedger(path)
            daily = SQLiteDailyNotionalLedger(path)
            engine = RiskEngine(
                RiskPolicy(maximum_daily_order_notional=rejected_intent.notional),
                idempotency_ledger=ids,
                daily_notional_ledger=daily,
            )
            stale = replace(market, observed_at=market.observed_at - timedelta(minutes=2))
            self.assertEqual(engine.assess(rejected_intent, signal, stale, portfolio, execution).decision, RiskDecisionType.REJECT)
            self.assertEqual(engine.assess(accepted_intent, signal, market, portfolio, execution).decision, RiskDecisionType.APPROVE)
            ids.close(); daily.close()


if __name__ == "__main__":
    unittest.main()
