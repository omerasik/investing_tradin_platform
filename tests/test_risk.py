import unittest
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.domain import (
    AssetClass,
    MarketSnapshot,
    OrderIntent,
    OrderSide,
    PortfolioState,
    RiskDecisionType,
    RiskPolicy,
    Signal,
    SignalStatus,
    utc_now,
)
from trade_platform.risk import KillSwitchRegistry, PreTradeExecutionContext, RiskEngine


def approved_inputs() -> tuple[OrderIntent, Signal, MarketSnapshot, PortfolioState, PreTradeExecutionContext]:
    now = utc_now()
    signal = Signal(uuid4(), "US:ETF:SPY", "trend-v1", SignalStatus.VALIDATED, now, now + timedelta(minutes=5), Decimal("0.99"), OrderSide.BUY, "test")
    intent = OrderIntent(uuid4(), signal.signal_id, signal.instrument_id, "paper-main", OrderSide.BUY, Decimal("10"), Decimal("100"))
    market = MarketSnapshot(signal.instrument_id, now, Decimal("99.90"), Decimal("100.00"), Decimal("100"), Decimal("0.99"))
    portfolio = PortfolioState("paper-main", {}, reconciliation_complete=True)
    execution = PreTradeExecutionContext(True, False, True, Decimal("0.001"), Decimal("0.1"), Decimal("10000"), True, True, True)
    return intent, signal, market, portfolio, execution


class RiskEngineTests(unittest.TestCase):
    def test_valid_paper_intent_is_approved(self) -> None:
        intent, signal, market, portfolio, execution = approved_inputs()
        result = RiskEngine(RiskPolicy()).assess(intent, signal, market, portfolio, execution)
        self.assertEqual(result.decision, RiskDecisionType.APPROVE)

    def test_stale_data_is_rejected(self) -> None:
        intent, signal, market, portfolio, execution = approved_inputs()
        stale = MarketSnapshot(market.instrument_id, utc_now() - timedelta(minutes=2), market.bid, market.ask, market.last, market.data_quality_score)
        result = RiskEngine(RiskPolicy()).assess(intent, signal, stale, portfolio, execution)
        self.assertEqual(result.decision, RiskDecisionType.REJECT)
        self.assertIn("stale_market_data", result.reasons)

    def test_unreconciled_portfolio_is_rejected(self) -> None:
        intent, signal, market, portfolio, execution = approved_inputs()
        result = RiskEngine(RiskPolicy()).assess(intent, signal, market, PortfolioState(portfolio.account_id, {}, False), execution)
        self.assertEqual(result.decision, RiskDecisionType.REJECT)
        self.assertIn("reconciliation_or_risk_increase_block", result.reasons)

    def test_global_kill_switch_blocks_intent(self) -> None:
        intent, signal, market, portfolio, execution = approved_inputs()
        switches = KillSwitchRegistry(); switches.activate("GLOBAL")
        result = RiskEngine(RiskPolicy(), switches).assess(intent, signal, market, portfolio, execution)
        self.assertEqual(result.decision, RiskDecisionType.REJECT)
        self.assertIn("kill_switch_active", result.reasons)

    def test_sell_reducing_existing_position_is_allowed(self) -> None:
        intent, signal, market, _, execution = approved_inputs()
        sell = OrderIntent(intent.intent_id, signal.signal_id, intent.instrument_id, intent.account_id, OrderSide.SELL, Decimal("10"), Decimal("100"))
        portfolio = PortfolioState("paper-main", {intent.instrument_id: Decimal("2000")}, True)
        result = RiskEngine(RiskPolicy()).assess(sell, signal, market, portfolio, execution)
        self.assertEqual(result.decision, RiskDecisionType.APPROVE)

    def test_kill_switch_requires_explicit_reactivation(self) -> None:
        switches = KillSwitchRegistry(); switches.activate("GLOBAL")
        with self.assertRaises(PermissionError):
            switches.deactivate("GLOBAL", explicit_operator_approval=False)

    def test_duplicate_intent_is_rejected_after_first_assessment(self) -> None:
        intent, signal, market, portfolio, execution = approved_inputs()
        engine = RiskEngine(RiskPolicy())
        self.assertEqual(
            engine.assess(intent, signal, market, portfolio, execution).decision, RiskDecisionType.APPROVE
        )
        repeated = engine.assess(intent, signal, market, portfolio, execution)
        self.assertEqual(repeated.decision, RiskDecisionType.REJECT)
        self.assertEqual(repeated.reasons, ("duplicate_intent",))

    def test_execution_context_prevents_event_slippage_broker_and_buying_power_bypass(self) -> None:
        intent, signal, market, portfolio, execution = approved_inputs()
        blocked = PreTradeExecutionContext(False, True, False, Decimal("0.03"), Decimal("0.9"), Decimal("0"), False, False, False)
        result = RiskEngine(RiskPolicy()).assess(intent, signal, market, portfolio, blocked)
        self.assertEqual(result.decision, RiskDecisionType.REJECT)
        self.assertEqual(set(result.reasons), {"invalid_trading_session", "instrument_halted", "quote_missing", "expected_slippage_limit", "event_risk_limit", "insufficient_buying_power", "broker_state_uncertain", "strategy_disabled", "model_not_approved"})
        missing = RiskEngine(RiskPolicy()).assess(OrderIntent(uuid4(), signal.signal_id, signal.instrument_id, intent.account_id, intent.side, intent.quantity, intent.limit_price), signal, market, portfolio)
        self.assertIn("missing_execution_context", missing.reasons)


if __name__ == "__main__":
    unittest.main()
