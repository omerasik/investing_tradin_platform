from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from trade_platform.domain import RiskDecisionType, RiskPolicy
from trade_platform.paper_execution import Position, ReconciliationResult
from trade_platform.paper_oms import SQLitePaperOms
from trade_platform.portfolio_context import portfolio_state_from_oms_reconciliation
from trade_platform.risk import RiskEngine
from tests.test_risk import approved_inputs


class PortfolioContextTests(unittest.TestCase):
    def test_only_recent_complete_oms_reconciliation_allows_risk_increase(self) -> None:
        current = datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc)
        oms = SQLitePaperOms()
        oms.record_reconciliation("paper-main", "sandbox", ReconciliationResult(True, ()), occurred_at=current)
        state = portfolio_state_from_oms_reconciliation(oms, "paper-main", {"US:ETF:SPY": Position("US:ETF:SPY", Decimal("10"), Decimal("100"))}, now=current + timedelta(minutes=1))
        self.assertTrue(state.reconciliation_complete)
        self.assertTrue(state.risk_increase_allowed)
        stale = portfolio_state_from_oms_reconciliation(oms, "paper-main", {}, now=current + timedelta(minutes=6))
        self.assertFalse(stale.reconciliation_complete)
        oms.close()

    def test_incomplete_oms_reconciliation_blocks_risk_assessment(self) -> None:
        intent, signal, market, _, execution = approved_inputs()
        oms = SQLitePaperOms()
        oms.record_reconciliation(intent.account_id, "sandbox", ReconciliationResult(False, ("cash_mismatch",)))
        state = portfolio_state_from_oms_reconciliation(oms, intent.account_id, {})
        result = RiskEngine(RiskPolicy()).assess(intent, signal, market, state, execution)
        self.assertEqual(result.decision, RiskDecisionType.REJECT)
        self.assertIn("reconciliation_or_risk_increase_block", result.reasons)
        oms.close()


if __name__ == "__main__":
    unittest.main()
