import os
import unittest
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from trade_platform.broker_adapter import (
    BrokerAccountSnapshot,
    BrokerConfiguration,
    BrokerMode,
    SandboxPaperBrokerAdapter,
)
from trade_platform.config import PlatformConfig, SecretReferenceError
from trade_platform.domain import AssetClass, Instrument, OrderIntent, OrderSide
from trade_platform.execution_evidence import (
    EventRiskObservation,
    HaltObservation,
    SlippageEstimate,
)
from trade_platform.instruments import InstrumentRiskProfile, TradingSession
from trade_platform.model_registry import ModelValidation, ModelVersion, SQLiteModelRegistry
from trade_platform.paper_execution import CashBalance, ReconciliationResult
from trade_platform.paper_runtime import (
    PaperPolicySelection,
    PaperRuntimeError,
    build_paper_runtime,
)
from trade_platform.policy_registry import PolicyDocument, SQLitePolicyRegistry
from trade_platform.quotes import QuoteObservation
from trade_platform.return_history import (
    AccountPerformanceSnapshot,
    PortfolioReturnObservation,
    SandboxAccountPerformanceProvider,
)
from trade_platform.signal_engine import SignalEngine, SignalProposal, ValidationStage
from trade_platform.strategy_promotion import PromotionDecision, PromotionStatus, StrategyActivation


class PaperRuntimeTests(unittest.TestCase):
    @staticmethod
    def seed_policies(path: Path):
        registry = SQLitePolicyRegistry(path)
        approved_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        registry.append(PolicyDocument("risk", "risk:paper-v1", {
            "maximum_order_notional": "10000", "maximum_per_trade_loss": "100",
            "maximum_stop_distance_fraction": "0.05", "stop_gap_buffer_fraction": "0.02",
        }, "risk-committee", approved_at))
        registry.append(PolicyDocument("portfolio", "portfolio:paper-v1", {"maximum_gross_notional": "10000", "maximum_single_weight": "1", "maximum_scenario_loss": "10000", "maximum_var_loss": "1", "minimum_historical_observations": 1, "return_history_window_observations": 1, "maximum_return_history_age_seconds": 86400, "stress_scenarios": {"risk_off": {"ETF": "-0.1"}}}, "risk-committee", approved_at))
        registry.close()
        models = SQLiteModelRegistry(path)
        model = ModelVersion.create(name="direction", version="v1", task="direction", feature_version="features:v1", dataset_version="bars:v1", artifact_reference="local://model")
        models.register(model)
        validation = ModelValidation.create(model_id=model.model_id, metrics={"brier": Decimal("0.1")}, economic_value_after_cost=Decimal("0.01"), evidence_reference="research://one")
        models.append_validation(validation)
        models.approve(model.model_id, validation_id=validation.validation_id, actor="reviewer", reason="reviewed")
        models.close()
        return model.model_id

    def test_runtime_requires_durable_database_secret_and_approved_policies(self) -> None:
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("1000")))
        config = PlatformConfig(assessment_integrity_key_reference="env:TRADE_PLATFORM_ASSESSMENT_KEY")
        model_id = uuid4()
        selection = PaperPolicySelection("risk:paper-v1", "portfolio:paper-v1", model_id)
        with self.assertRaisesRegex(PaperRuntimeError, "durable_paper_database_required"):
            build_paper_runtime(config=config, database_path=":memory:", adapter=adapter, policy_selection=selection)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.sqlite"
            model_id = self.seed_policies(path)
            selection = PaperPolicySelection("risk:paper-v1", "portfolio:paper-v1", model_id)
            with self.assertRaises(SecretReferenceError):
                build_paper_runtime(config=config, database_path=path, adapter=adapter, policy_selection=selection)
            with patch.dict(os.environ, {"TRADE_PLATFORM_ASSESSMENT_KEY": "test-key"}, clear=True):
                runtime = build_paper_runtime(config=config, database_path=path, adapter=adapter, policy_selection=selection)
                self.assertTrue(runtime.assessments.keyed_integrity_enabled)
                self.assertEqual(runtime.policy_selection, selection)
                self.assertEqual(runtime.stress_scenarios[0].name, "risk_off")
                self.assertIs(runtime.reconciled_accounts._oms, runtime.oms)
                runtime.close()

    def test_runtime_rejects_unknown_approved_policy_selection(self) -> None:
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("1000")))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.sqlite"
            model_id = self.seed_policies(path)
            config = PlatformConfig(assessment_integrity_key_reference="env:TRADE_PLATFORM_ASSESSMENT_KEY")
            with patch.dict(os.environ, {"TRADE_PLATFORM_ASSESSMENT_KEY": "test-key"}, clear=True), self.assertRaisesRegex(ValueError, "unknown_policy_version"):
                build_paper_runtime(config=config, database_path=path, adapter=adapter, policy_selection=PaperPolicySelection("risk:missing", "portfolio:paper-v1", model_id))

    def test_runtime_rejects_policy_without_per_trade_controls(self) -> None:
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("1000")))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.sqlite"
            model_id = self.seed_policies(path)
            registry = SQLitePolicyRegistry(path)
            registry.append(PolicyDocument("risk", "risk:legacy", {"maximum_order_notional": "10000"}, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc)))
            registry.close()
            config = PlatformConfig(assessment_integrity_key_reference="env:TRADE_PLATFORM_ASSESSMENT_KEY")
            with patch.dict(os.environ, {"TRADE_PLATFORM_ASSESSMENT_KEY": "test-key"}, clear=True), self.assertRaisesRegex(PaperRuntimeError, "per_trade_risk_policy_required"):
                build_paper_runtime(config=config, database_path=path, adapter=adapter, policy_selection=PaperPolicySelection("risk:legacy", "portfolio:paper-v1", model_id))

    def test_runtime_rejects_portfolio_policy_without_reviewed_stress_suite(self) -> None:
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("1000")))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.sqlite"
            model_id = self.seed_policies(path)
            registry = SQLitePolicyRegistry(path)
            registry.append(PolicyDocument("portfolio", "portfolio:no-stress", {"maximum_gross_notional": "10000", "maximum_single_weight": "1", "maximum_scenario_loss": "10000"}, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc)))
            registry.close()
            config = PlatformConfig(assessment_integrity_key_reference="env:TRADE_PLATFORM_ASSESSMENT_KEY")
            with patch.dict(os.environ, {"TRADE_PLATFORM_ASSESSMENT_KEY": "test-key"}, clear=True), self.assertRaisesRegex(ValueError, "stress_scenarios_unavailable"):
                build_paper_runtime(config=config, database_path=path, adapter=adapter, policy_selection=PaperPolicySelection("risk:paper-v1", "portfolio:no-stress", model_id))

    def test_runtime_rejects_unapproved_model_selection(self) -> None:
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("1000")))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.sqlite"
            self.seed_policies(path)
            config = PlatformConfig(assessment_integrity_key_reference="env:TRADE_PLATFORM_ASSESSMENT_KEY")
            with patch.dict(os.environ, {"TRADE_PLATFORM_ASSESSMENT_KEY": "test-key"}, clear=True), self.assertRaisesRegex(PaperRuntimeError, "selected_model_not_approved"):
                build_paper_runtime(config=config, database_path=path, adapter=adapter, policy_selection=PaperPolicySelection("risk:paper-v1", "portfolio:paper-v1", uuid4()))

    def test_runtime_ingests_account_returns_only_through_durable_history_authority(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("1000")))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.sqlite"; model_id = self.seed_policies(path)
            with patch.dict(os.environ, {"TRADE_PLATFORM_ASSESSMENT_KEY": "test-key"}, clear=True):
                runtime = build_paper_runtime(config=PlatformConfig(assessment_integrity_key_reference="env:TRADE_PLATFORM_ASSESSMENT_KEY"), database_path=path, adapter=adapter, policy_selection=PaperPolicySelection("risk:paper-v1", "portfolio:paper-v1", model_id))
                provider = SandboxAccountPerformanceProvider(adapter.configuration)
                provider.record_snapshot(AccountPerformanceSnapshot("paper", now - timedelta(days=2), Decimal("100"), "nav-1", now - timedelta(days=2)))
                provider.record_snapshot(AccountPerformanceSnapshot("paper", now - timedelta(days=1), Decimal("101"), "nav-2", now - timedelta(days=1)))
                self.assertEqual(runtime.ingest_return_history(provider, account_id="paper", start=now - timedelta(days=2), end=now - timedelta(days=1), actor="risk-operator", idempotency_key="returns-1"), 1)
                self.assertEqual(runtime.ingest_return_history(provider, account_id="paper", start=now - timedelta(days=2), end=now - timedelta(days=1), actor="risk-operator", idempotency_key="returns-1"), 1)
                self.assertEqual(runtime.return_history.returns_as_of("paper", now), [Decimal("0.01")])
                self.assertTrue(runtime.return_history.provider_health(provider.provider_name)[1])
                runtime.close()

    def test_runtime_derives_evidence_backed_assessment_and_submits_only_approval(self) -> None:
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("10000")))
        now = datetime(2099, 7, 31, 12, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.sqlite"
            model_id = self.seed_policies(path)
            config = PlatformConfig(assessment_integrity_key_reference="env:TRADE_PLATFORM_ASSESSMENT_KEY")
            with patch.dict(os.environ, {"TRADE_PLATFORM_ASSESSMENT_KEY": "test-key"}, clear=True):
                runtime = build_paper_runtime(config=config, database_path=path, adapter=adapter, policy_selection=PaperPolicySelection("risk:paper-v1", "portfolio:paper-v1", model_id))
                runtime.instruments.register(Instrument("TEST:SPY", "SPY", "TEST", AssetClass.ETF, "USD", Decimal("0.01"), Decimal("1")))
                runtime.instruments.append_risk_profile(InstrumentRiskProfile("TEST:SPY", "INDEX", "US", "US_INDEX", Decimal("1"), Decimal("1"), {"market": Decimal("1")}, now, now))
                runtime.instruments.add_trading_session(TradingSession("TEST", now.weekday(), time(0), time(23, 59), "UTC"))
                proposal = SignalProposal.create(instrument_id="TEST:SPY", asset_class=AssetClass.ETF, strategy_version="trend-v1", direction=OrderSide.BUY, entry_low=Decimal("99"), entry_high=Decimal("101"), invalidation_level=Decimal("98"), stop_level=Decimal("98"), target_logic="target", expected_holding_period=timedelta(days=1), strength=Decimal("0.8"), confidence=Decimal("0.8"), expected_return=Decimal("0.02"), expected_volatility=Decimal("0.1"), expected_downside=Decimal("-0.01"), risk_reward_estimate=Decimal("2"), regime="BULL", liquidity_score=Decimal("0.9"), data_quality_score=Decimal("1"), news_risk=Decimal("0.1"), event_risk=Decimal("0.1"), correlation_impact=Decimal("0"), recommended_quantity=Decimal("10"), expires_at=now + timedelta(minutes=5), explanation="evidence", contradicting_evidence=())
                runtime.signals.append(proposal); runtime.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
                promotion = PromotionDecision(uuid4(), uuid4(), "trend-v1", PromotionStatus.REVIEW_REQUIRED, (), 20, Decimal("0.1"), now)
                runtime.promotions.append(promotion)
                runtime.promotions.append_activation(StrategyActivation(uuid4(), "trend-v1", True, "reviewer", now, now, promotion.decision_id))
                runtime.quotes.append(QuoteObservation(uuid4(), "TEST:SPY", Decimal("99.9"), Decimal("100"), Decimal("100"), Decimal("1"), "paper-feed", "quote-1", now, now))
                runtime.execution_evidence.append_halt(HaltObservation(uuid4(), "TEST:SPY", False, "venue", "halt-1", now, now))
                runtime.execution_evidence.append_event_risk(EventRiskObservation(uuid4(), "TEST:SPY", Decimal("0.1"), "events", "event-1", now, now))
                runtime.execution_evidence.append_slippage(SlippageEstimate(uuid4(), "TEST:SPY", OrderSide.BUY, Decimal("0.001"), "slippage", "slip-1", now, now))
                runtime.return_history.append(PortfolioReturnObservation(uuid4(), "paper", now - timedelta(days=2), now - timedelta(days=1), Decimal("-0.01"), "paper-oms", "return-1", now - timedelta(days=1)))
                snapshot = BrokerAccountSnapshot("paper", "local", now, CashBalance("USD", Decimal("10000")), Decimal("10000"), {}, (), (), Decimal("0"), Decimal("0"))
                runtime.oms.record_reconciliation_with_account(snapshot, ReconciliationResult(True, ()), healthy=True, ingested_at=now)
                intent = OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
                result = runtime.assess_and_submit(intent, observed_at=now)
                self.assertTrue(result.assessment.approved, result.assessment.risk_decision.reasons)
                self.assertEqual(result.assessment.portfolio_decision.var_loss, Decimal("0.01"))
                self.assertEqual(result.assessment.per_trade_risk.loss_at_stop, Decimal("20"))
                self.assertEqual(result.assessment.per_trade_risk.gap_adjusted_loss, Decimal("39.60"))
                self.assertTrue(result.assessment.input_evidence.return_history_reference)
                self.assertEqual(result.assessment.input_evidence.return_history_observed_at, now - timedelta(days=1))
                self.assertEqual(runtime.assessments.get_for_intent(intent.intent_id).input_evidence, result.assessment.input_evidence)
                self.assertEqual(runtime.assessments.get_for_intent(intent.intent_id).per_trade_risk, result.assessment.per_trade_risk)
                self.assertEqual(result.paper_order.status.value, "ACKNOWLEDGED")
                self.assertEqual(len(adapter.order_events()), 1)
                runtime.promotions.append_activation(StrategyActivation(uuid4(), "trend-v1", False, "operator", now, now))
                blocked = runtime.assess_and_submit(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("1"), Decimal("100")), observed_at=now)
                self.assertFalse(blocked.assessment.approved)
                self.assertIsNone(blocked.paper_order)
                self.assertIn("strategy_disabled", blocked.assessment.risk_decision.reasons)
                self.assertEqual(len(adapter.order_events()), 1)
                runtime.close()


if __name__ == "__main__":
    unittest.main()
