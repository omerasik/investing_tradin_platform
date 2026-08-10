from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trade_platform.domain import AssetClass, OrderSide, utc_now
from trade_platform.signal_engine import DetailedSignalStatus, SignalEngine, SignalEngineError, SignalProposal, SQLiteSignalStore, ValidationStage


def proposal() -> SignalProposal:
    now = utc_now()
    return SignalProposal.create(instrument_id="US:NYSE:SPY", asset_class=AssetClass.ETF, strategy_version="trend-v1", direction=OrderSide.BUY, entry_low=Decimal("100"), entry_high=Decimal("101"), invalidation_level=Decimal("98"), stop_level=Decimal("99"), target_logic="Trail the 20-day low.", expected_holding_period=timedelta(days=5), strength=Decimal("0.7"), confidence=Decimal("0.8"), expected_return=Decimal("0.04"), expected_volatility=Decimal("0.1"), expected_downside=Decimal("-0.02"), risk_reward_estimate=Decimal("2"), regime="BULL", liquidity_score=Decimal("0.9"), data_quality_score=Decimal("0.99"), news_risk=Decimal("0.1"), event_risk=Decimal("0.2"), correlation_impact=Decimal("0.1"), recommended_quantity=Decimal("10"), expires_at=now + timedelta(hours=1), explanation="Trend remains positive.", contradicting_evidence=("Earnings risk is elevated.",))


class SignalEngineTests(unittest.TestCase):
    def test_all_required_stages_are_explicit_and_data_failure_blocks(self) -> None:
        engine = SignalEngine()
        stages = {stage: True for stage in ValidationStage}; stages[ValidationStage.DATA] = False
        result = engine.validate(proposal(), stages)
        self.assertEqual(result.status, DetailedSignalStatus.BLOCKED_BY_DATA)
        self.assertIn(ValidationStage.DATA, result.failures)
        with self.assertRaisesRegex(SignalEngineError, "all_stages"):
            engine.validate(proposal(), {ValidationStage.DATA: True})

    def test_validated_signal_is_durable_but_not_an_order(self) -> None:
        signal = proposal(); result = SignalEngine().validate(signal, {stage: True for stage in ValidationStage})
        self.assertEqual(result.status, DetailedSignalStatus.VALIDATED)
        with TemporaryDirectory() as directory:
            store = SQLiteSignalStore(Path(directory) / "signals.sqlite")
            store.append(signal); store.append_validation(result)
            self.assertEqual(store.get_validation(result.assessment_id), result)
            with self.assertRaisesRegex(SignalEngineError, "duplicate_signal"):
                store.append(signal)
            store.close()

    def test_lifecycle_is_append_only_and_rejects_skipped_or_terminal_transitions(self) -> None:
        signal = proposal(); result = SignalEngine().validate(signal, {stage: True for stage in ValidationStage})
        with TemporaryDirectory() as directory:
            store = SQLiteSignalStore(Path(directory) / "signals.sqlite")
            store.append(signal); store.append_validation(result)
            self.assertEqual(store.status(signal.signal_id), DetailedSignalStatus.VALIDATED)
            with self.assertRaisesRegex(SignalEngineError, "invalid_signal_transition"):
                store.transition(signal.signal_id, DetailedSignalStatus.FILLED, actor="operator")
            store.transition(signal.signal_id, DetailedSignalStatus.WAITING_FOR_ENTRY, actor="operator")
            store.transition(signal.signal_id, DetailedSignalStatus.ACTIVE, actor="operator")
            store.transition(signal.signal_id, DetailedSignalStatus.FILLED, actor="broker_evidence")
            store.transition(signal.signal_id, DetailedSignalStatus.CLOSED, actor="operator")
            self.assertEqual(len(store.lifecycle_events(signal.signal_id)), 5)
            with self.assertRaisesRegex(SignalEngineError, "invalid_signal_transition"):
                store.transition(signal.signal_id, DetailedSignalStatus.ACTIVE, actor="operator")
            store.close()

    def test_only_current_unexpired_validated_signal_adapts_to_shared_risk_contract(self) -> None:
        signal = proposal(); result = SignalEngine().validate(signal, {stage: True for stage in ValidationStage})
        store = SQLiteSignalStore(); store.append(signal); store.append_validation(result)
        adapted = store.risk_signal(signal.signal_id, as_of=signal.created_at)
        self.assertEqual(adapted.signal_id, signal.signal_id)
        self.assertEqual(adapted.instrument_id, signal.instrument_id)
        self.assertEqual(adapted.data_quality_score, signal.data_quality_score)
        store.transition(signal.signal_id, DetailedSignalStatus.WAITING_FOR_ENTRY, actor="operator")
        with self.assertRaisesRegex(SignalEngineError, "current_validated"):
            store.risk_signal(signal.signal_id, as_of=signal.created_at)
        store.close()


if __name__ == "__main__":
    unittest.main()
