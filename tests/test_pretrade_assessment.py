import unittest
from dataclasses import fields, replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from inspect import Parameter, signature
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from trade_platform.broker_adapter import (
    BrokerAccountSnapshot,
    BrokerConfiguration,
    BrokerMode,
    SandboxPaperBrokerAdapter,
)
from trade_platform.broker_sync import PaperBrokerSyncService, SQLiteBrokerEventStore
from trade_platform.domain import (
    AssetClass,
    Instrument,
    MarketSnapshot,
    OrderIntent,
    OrderSide,
    RiskDecisionType,
)
from trade_platform.execution_evidence import (
    EventRiskObservation,
    HaltObservation,
    SlippageEstimate,
    SQLiteExecutionEvidenceStore,
)
from trade_platform.instruments import InstrumentRiskProfile, SQLiteInstrumentStore, TradingSession
from trade_platform.model_registry import ModelValidation, ModelVersion, SQLiteModelRegistry
from trade_platform.paper_execution import CashBalance, Position, ReconciliationResult
from trade_platform.paper_oms import SQLitePaperOms
from trade_platform.policy_registry import PolicyDocument, SQLitePolicyRegistry
from trade_platform.portfolio_evidence import OmsReconciledAccountStore
from trade_platform.portfolio_risk import PortfolioExposure, PortfolioRiskPolicy, StressScenario
from trade_platform.pretrade_assessment import (
    PreTradeInputEvidence,
    SQLitePreTradeAssessmentStore,
    assess_pretrade,
)
from trade_platform.quotes import QuoteObservation, SQLiteQuoteStore
from trade_platform.return_history import PortfolioReturnObservation, SQLitePortfolioReturnStore
from trade_platform.signal_engine import (
    SignalEngine,
    SignalProposal,
    SQLiteSignalStore,
    ValidationStage,
)


class PreTradeAssessmentTests(unittest.TestCase):
    integrity_key = b"test-assessment-integrity-key"
    def setUp(self) -> None:
        # Fixed far-future fixture time keeps proposal expiry deterministic when
        # the suite is run after the original 2026 test data date.
        self.now = datetime(2099, 7, 31, 12, tzinfo=timezone.utc)
        self.instruments = SQLiteInstrumentStore(); self.instruments.register(Instrument("TEST:SPY", "SPY", "TEST", AssetClass.ETF, "USD", Decimal("0.01"), Decimal("1")))
        self.instruments.append_risk_profile(InstrumentRiskProfile("TEST:SPY", "INDEX", "US", "US_INDEX", Decimal("1"), Decimal("1"), {"market": Decimal("1")}, self.now, self.now))
        self.instruments.register(Instrument("TEST:QQQ", "QQQ", "TEST", AssetClass.ETF, "USD", Decimal("0.01"), Decimal("1")))
        self.instruments.append_risk_profile(InstrumentRiskProfile("TEST:QQQ", "QQQ", "US", "US_INDEX", Decimal("1"), Decimal("1"), {"market": Decimal("1")}, self.now, self.now))
        self.instruments.add_trading_session(TradingSession("TEST", self.now.weekday(), time(0), time(23, 59), "UTC"))
        self.models = SQLiteModelRegistry(); self.model = ModelVersion.create(name="direction", version="v1", task="direction", feature_version="features:v1", dataset_version="bars:v1", artifact_reference="local://model")
        self.models.register(self.model)
        validation = ModelValidation.create(model_id=self.model.model_id, metrics={"brier": Decimal("0.1")}, economic_value_after_cost=Decimal("0.01"), evidence_reference="research://one")
        self.models.append_validation(validation); self.models.approve(self.model.model_id, validation_id=validation.validation_id, actor="reviewer", reason="reviewed")
        self.signals = SQLiteSignalStore()
        self.policy_registry = SQLitePolicyRegistry()
        approved_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.policy_registry.append(PolicyDocument("risk", "risk:default", {"maximum_order_notional": "10000"}, "risk-committee", approved_at))
        self.policy_registry.append(PolicyDocument("portfolio", "portfolio:default", {"maximum_gross_notional": "10000", "maximum_single_weight": "1", "maximum_scenario_loss": "10000"}, "risk-committee", approved_at))
        self.execution_evidence = SQLiteExecutionEvidenceStore()
        self.execution_evidence.append_halt(HaltObservation(uuid4(), "TEST:SPY", False, "venue", "halt-1", self.now, self.now))
        self.execution_evidence.append_event_risk(EventRiskObservation(uuid4(), "TEST:SPY", Decimal("0.1"), "events", "event-1", self.now, self.now))
        self.execution_evidence.append_slippage(SlippageEstimate(uuid4(), "TEST:SPY", OrderSide.BUY, Decimal("0.001"), "slippage", "slip-1", self.now, self.now))
        self.quotes = SQLiteQuoteStore()
        self.quote = QuoteObservation(uuid4(), "TEST:SPY", Decimal("99.9"), Decimal("100"), Decimal("100"), Decimal("1"), "paper-feed", "quote-1", self.now, self.now)
        self.quotes.append(self.quote)
        self.quotes.append(QuoteObservation(uuid4(), "TEST:QQQ", Decimal("99.9"), Decimal("100"), Decimal("100"), Decimal("1"), "paper-feed", "quote-qqq", self.now, self.now))
        self.return_history = SQLitePortfolioReturnStore()
        self.oms = SQLitePaperOms()
        self.broker_snapshot = BrokerAccountSnapshot("paper", "local", self.now, CashBalance("USD", Decimal("10000")), Decimal("10000"), {"TEST:QQQ": Position("TEST:QQQ", Decimal("5"), Decimal("90"))}, (), (), Decimal("0"), Decimal("0"))
        self.oms.record_reconciliation_with_account(self.broker_snapshot, ReconciliationResult(True, ()), healthy=True, ingested_at=self.now)
        self.reconciled_accounts = OmsReconciledAccountStore(self.oms)
        self.account_evidence = self.reconciled_accounts.latest_as_of("paper", self.now)

    def tearDown(self) -> None:
        self.signals.close(); self.models.close(); self.instruments.close(); self.execution_evidence.close(); self.quotes.close(); self.return_history.close(); self.oms.close(); self.policy_registry.close()

    def _proposal(self) -> SignalProposal:
        return SignalProposal.create(instrument_id="TEST:SPY", asset_class=AssetClass.ETF, strategy_version="trend-v1", direction=OrderSide.BUY, entry_low=Decimal("99"), entry_high=Decimal("101"), invalidation_level=Decimal("98"), stop_level=Decimal("98"), target_logic="target", expected_holding_period=timedelta(days=1), strength=Decimal("0.8"), confidence=Decimal("0.8"), expected_return=Decimal("0.02"), expected_volatility=Decimal("0.1"), expected_downside=Decimal("-0.01"), risk_reward_estimate=Decimal("2"), regime="BULL", liquidity_score=Decimal("0.9"), data_quality_score=Decimal("1"), news_risk=Decimal("0.1"), event_risk=Decimal("0.1"), correlation_impact=Decimal("0"), recommended_quantity=Decimal("10"), expires_at=self.now + timedelta(minutes=5), explanation="evidence", contradicting_evidence=())

    def _assess(self, intent: OrderIntent, assessment_store: SQLitePreTradeAssessmentStore | None = None, evidence: PreTradeInputEvidence | None = None, market: MarketSnapshot | None = None, projected_exposures: list[PortfolioExposure] | None = None, portfolio_policy: PortfolioRiskPolicy | None = None, risk_policy_version: str = "risk:default", portfolio_policy_version: str = "portfolio:default", policy_registry: SQLitePolicyRegistry | None = None, return_history_store: SQLitePortfolioReturnStore | None = None, historical_returns: list[Decimal] | None = None):
        snapshot = self.broker_snapshot
        market = market or self.quote.market_snapshot()
        _, expected_exposures = self.reconciled_accounts.projected_exposures_as_of(account_id="paper", intent=intent, decision_at=self.now, instruments=self.instruments, quotes=self.quotes)
        exposures = projected_exposures or expected_exposures
        registry = policy_registry or self.policy_registry
        if portfolio_policy is not None:
            portfolio_policy_version = f"portfolio:override:{uuid4()}"
            payload: dict[str, object] = {}
            for item in fields(portfolio_policy):
                value = getattr(portfolio_policy, item.name)
                if isinstance(value, Decimal):
                    payload[item.name] = str(value)
                elif isinstance(value, dict) and value:
                    payload[item.name] = {name: str(limit) for name, limit in value.items()}
                elif value is not None:
                    payload[item.name] = value
            registry.append(PolicyDocument("portfolio", portfolio_policy_version, payload, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        stored = self.execution_evidence.snapshot_as_of("TEST:SPY", OrderSide.BUY, self.now)
        input_evidence = evidence or stored.pretrade_input_evidence(market_reference=f"{self.quote.provider}:{self.quote.source_reference}:{self.quote.quote_id}", market=market, exposure_reference=self.account_evidence.exposure_reference, exposure_observed_at=self.now, exposures=exposures)
        return assess_pretrade(intent=intent, signal_store=self.signals, market=market, portfolio_state=self.reconciled_accounts.portfolio_state_as_of("paper", self.now), instruments=self.instruments, broker_snapshot=snapshot, broker_healthy=True, models=self.models, model_id=self.model.model_id, strategy_enabled=True, instrument_halted=False, quote_available=True, expected_slippage_fraction=Decimal("0.001"), event_risk=Decimal("0.1"), projected_exposures=exposures, portfolio_equity=Decimal("10000"), scenario=StressScenario("none", {}), historical_returns=historical_returns, observed_at=self.now, assessment_store=assessment_store, input_evidence=input_evidence, execution_evidence_store=self.execution_evidence, quote_store=self.quotes, reconciled_accounts=self.reconciled_accounts, return_history_store=return_history_store, risk_policy_version=risk_policy_version, portfolio_policy_version=portfolio_policy_version, policy_registry=registry)

    def test_checked_signal_context_and_portfolio_produce_combined_nonexecuting_assessment(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        result = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")))
        self.assertTrue(result.approved, (result.risk_decision.reasons, result.portfolio_decision.reasons)); self.assertEqual(result.risk_decision.decision, RiskDecisionType.APPROVE); self.assertTrue(result.portfolio_decision.approved)

    def test_pretrade_requires_registry_and_explicit_policy_versions(self) -> None:
        parameters = signature(assess_pretrade).parameters
        self.assertNotIn("risk_engine", parameters)
        self.assertNotIn("portfolio_policy", parameters)
        for name in ("policy_registry", "risk_policy_version", "portfolio_policy_version"):
            self.assertIs(parameters[name].default, Parameter.empty)

    def test_assessment_persists_explicit_policy_versions(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        store = SQLitePreTradeAssessmentStore()
        self.policy_registry.append(PolicyDocument("risk", "risk:2026-01", {"maximum_order_notional": "10000"}, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        self.policy_registry.append(PolicyDocument("portfolio", "portfolio:2026-01", {"maximum_gross_notional": "10000", "maximum_single_weight": "1", "maximum_scenario_loss": "10000"}, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        assessment = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), store, risk_policy_version="risk:2026-01", portfolio_policy_version="portfolio:2026-01")
        restored = store.get_for_intent(assessment.risk_decision.intent_id)
        self.assertEqual((assessment.risk_policy_version, restored.portfolio_policy_version), ("risk:2026-01", "portfolio:2026-01"))
        self.assertEqual((restored.risk_policy_digest, restored.portfolio_policy_digest), (self.policy_registry.get("risk", "risk:2026-01").digest, self.policy_registry.get("portfolio", "portfolio:2026-01").digest))
        self.assertEqual(restored.input_evidence, assessment.input_evidence)
        store.close()

    def test_unknown_registry_risk_policy_fails_closed(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        registry = SQLitePolicyRegistry()
        result = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), risk_policy_version="risk:missing", policy_registry=registry)
        self.assertIn("risk_policy_evidence_unavailable", result.evidence_block_reason)
        registry.close()

    def test_registry_resolves_both_approved_policy_versions(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        registry = SQLitePolicyRegistry()
        approved_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        registry.append(PolicyDocument("risk", "risk:2026-01", {"maximum_order_notional": "2000"}, "risk-committee", approved_at))
        registry.append(PolicyDocument("portfolio", "portfolio:2026-01", {"maximum_gross_notional": "2000", "maximum_single_weight": "1", "maximum_scenario_loss": "2000"}, "risk-committee", approved_at))
        result = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), risk_policy_version="risk:2026-01", portfolio_policy_version="portfolio:2026-01", policy_registry=registry)
        self.assertTrue(result.approved, result.evidence_block_reason)
        registry.close()

    def test_unknown_registry_portfolio_policy_fails_closed(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        registry = SQLitePolicyRegistry()
        registry.append(PolicyDocument("risk", "risk:2026-01", {"maximum_order_notional": "2000"}, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        result = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), risk_policy_version="risk:2026-01", portfolio_policy_version="portfolio:missing", policy_registry=registry)
        self.assertIn("portfolio_policy_evidence_unavailable", result.evidence_block_reason)
        registry.close()

    def test_missing_detailed_validation_fails_closed_before_portfolio_assessment(self) -> None:
        result = self._assess(OrderIntent(uuid4(), uuid4(), "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")))
        self.assertFalse(result.approved); self.assertIsNone(result.portfolio_decision)
        self.assertIn("detailed_signal_evidence_unavailable", result.risk_decision.reasons[0])

    def test_successful_assessment_is_the_only_route_to_paper_adapter_submission(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        order = OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
        assessments = SQLitePreTradeAssessmentStore(integrity_key=self.integrity_key)
        assessment = self._assess(order, assessments)
        oms = SQLitePaperOms(); events = SQLiteBrokerEventStore()
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("10000")))
        service = PaperBrokerSyncService(oms, adapter, events, assessment_store=assessments, policy_registry=self.policy_registry)
        self.assertEqual(service.submit_after_assessment(order, assessment).status.value, "ACKNOWLEDGED")
        self.assertEqual(service.submit_after_assessment(order, assessment).status.value, "ACKNOWLEDGED")
        self.assertEqual(len(adapter.order_events()), 1)
        assessments.close(); events.close(); oms.close()

    def test_paper_submission_rejects_unpersisted_or_policy_mismatched_assessment(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        order = OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
        assessments = SQLitePreTradeAssessmentStore(integrity_key=self.integrity_key)
        unpersisted = self._assess(order)
        oms = SQLitePaperOms(); events = SQLiteBrokerEventStore()
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("10000")))
        service = PaperBrokerSyncService(oms, adapter, events, assessment_store=assessments, policy_registry=self.policy_registry)
        with self.assertRaisesRegex(ValueError, "durable_assessment_evidence_unavailable"):
            service.submit_after_assessment(order, unpersisted)
        self.assertEqual(adapter.order_events(), ())
        assessments.close(); events.close(); oms.close()

    def test_paper_submission_rejects_a_tampered_durable_assessment(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        order = OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
        assessments = SQLitePreTradeAssessmentStore(integrity_key=self.integrity_key)
        assessment = self._assess(order, assessments)
        assessments._connection.execute("UPDATE pretrade_assessments SET risk_reasons_json = ? WHERE intent_id = ?", ('["tampered"]', str(order.intent_id)))
        assessments._connection.commit()
        oms = SQLitePaperOms(); events = SQLiteBrokerEventStore()
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("10000")))
        service = PaperBrokerSyncService(oms, adapter, events, assessment_store=assessments, policy_registry=self.policy_registry)
        with self.assertRaisesRegex(ValueError, "durable_assessment_evidence_invalid"):
            service.submit_after_assessment(order, assessment)
        self.assertEqual(adapter.order_events(), ())
        assessments.close(); events.close(); oms.close()

    def test_assessment_provenance_is_persistent_and_reused_for_same_intent(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        intent = OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
        store = SQLitePreTradeAssessmentStore()
        first = self._assess(intent, store)
        repeated = self._assess(intent, store)
        self.assertEqual(repeated.assessment_id, first.assessment_id)
        restored = store.get_for_intent(intent.intent_id)
        self.assertEqual((restored.assessment_id, restored.risk_decision.decision, restored.risk_policy_version, restored.portfolio_policy_version), (first.assessment_id, first.risk_decision.decision, first.risk_policy_version, first.portfolio_policy_version))
        store.close()

    def test_assessment_provenance_survives_reopen(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        intent = OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "assessments.sqlite"
            first_store = SQLitePreTradeAssessmentStore(path)
            first = self._assess(intent, first_store); first_store.close()
            reopened = SQLitePreTradeAssessmentStore(path)
            restored = reopened.get_for_intent(intent.intent_id)
            self.assertEqual((restored.assessment_id, restored.risk_decision.decision, restored.risk_policy_version, restored.portfolio_policy_version), (first.assessment_id, first.risk_decision.decision, first.risk_policy_version, first.portfolio_policy_version))
            reopened.close()

    def test_keyed_assessment_integrity_rejects_wrong_key_after_reopen(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        intent = OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "assessments.sqlite"
            store = SQLitePreTradeAssessmentStore(path, integrity_key=self.integrity_key)
            self._assess(intent, store); store.close()
            wrong_key = SQLitePreTradeAssessmentStore(path, integrity_key=b"wrong-integrity-key")
            with self.assertRaisesRegex(ValueError, "mac_mismatch"):
                wrong_key.get_for_intent(intent.intent_id)
            wrong_key.close()

    def test_missing_or_stale_input_provenance_fails_closed(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        missing = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), evidence=PreTradeInputEvidence("", self.now, "halts", self.now, "events", self.now, "slippage", self.now, "exposure", self.now, "digest"))
        stale = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), evidence=PreTradeInputEvidence("market", self.now - timedelta(minutes=6), "halts", self.now, "events", self.now, "slippage", self.now, "exposure", self.now, "digest"))
        self.assertIn("pretrade_input_evidence_invalid", missing.evidence_block_reason)
        self.assertIn("pretrade_input_evidence_invalid", stale.evidence_block_reason)

    def test_historical_risk_uses_the_stored_window_and_rejects_a_tampered_reference(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        version = "portfolio:historical-risk"
        self.policy_registry.append(PolicyDocument("portfolio", version, {
            "maximum_gross_notional": "10000", "maximum_single_weight": "1", "maximum_scenario_loss": "10000",
            "maximum_var_loss": "1", "maximum_cvar_loss": "1", "maximum_drawdown_loss": "1",
            "minimum_historical_observations": 1, "return_history_window_observations": 1,
            "maximum_return_history_age_seconds": 3600,
        }, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        self.return_history.append(PortfolioReturnObservation(
            uuid4(), "paper", self.now - timedelta(minutes=2), self.now - timedelta(minutes=1),
            Decimal("-0.01"), "paper-oms", "return-1", self.now - timedelta(minutes=1),
        ))
        intent = OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
        _, exposures = self.reconciled_accounts.projected_exposures_as_of(account_id="paper", intent=intent, decision_at=self.now, instruments=self.instruments, quotes=self.quotes)
        base_evidence = self.execution_evidence.snapshot_as_of("TEST:SPY", OrderSide.BUY, self.now).pretrade_input_evidence(
            market_reference=f"{self.quote.provider}:{self.quote.source_reference}:{self.quote.quote_id}", market=self.quote.market_snapshot(),
            exposure_reference=self.account_evidence.exposure_reference, exposure_observed_at=self.now, exposures=exposures,
        )
        history = self.return_history.evidence_for_policy("paper", self.now, minimum_observations=1, window_observations=1, maximum_age_seconds=3600)
        valid_evidence = replace(base_evidence, return_history_reference=history.reference, return_history_observed_at=history.observed_at)
        approved = self._assess(intent, evidence=valid_evidence, portfolio_policy_version=version, return_history_store=self.return_history, historical_returns=[Decimal("-0.99")])
        self.assertTrue(approved.approved, approved.evidence_block_reason)
        self.assertEqual(approved.portfolio_decision.var_loss, Decimal("0.01"))
        tampered = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), evidence=replace(valid_evidence, return_history_reference="caller-controlled"), portfolio_policy_version=version, return_history_store=self.return_history)
        self.assertIn("stored_execution_evidence_invalid:stored_execution_evidence_mismatch", tampered.evidence_block_reason)

    def test_store_mismatch_rejects_caller_execution_value_override(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        intent = OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
        snapshot = self.broker_snapshot
        market = MarketSnapshot("TEST:SPY", self.now, Decimal("99.9"), Decimal("100"), Decimal("100"), Decimal("1"))
        _, exposures = self.reconciled_accounts.projected_exposures_as_of(account_id="paper", intent=intent, decision_at=self.now, instruments=self.instruments, quotes=self.quotes)
        evidence = self.execution_evidence.snapshot_as_of("TEST:SPY", OrderSide.BUY, self.now).pretrade_input_evidence(market_reference=f"{self.quote.provider}:{self.quote.source_reference}:{self.quote.quote_id}", market=market, exposure_reference=self.account_evidence.exposure_reference, exposure_observed_at=self.now, exposures=exposures)
        result = assess_pretrade(intent=intent, signal_store=self.signals, market=market, portfolio_state=self.reconciled_accounts.portfolio_state_as_of("paper", self.now), instruments=self.instruments, broker_snapshot=snapshot, broker_healthy=True, models=self.models, model_id=self.model.model_id, strategy_enabled=True, instrument_halted=True, quote_available=True, expected_slippage_fraction=Decimal("0.001"), event_risk=Decimal("0.1"), projected_exposures=exposures, portfolio_equity=Decimal("10000"), scenario=StressScenario("none", {}), observed_at=self.now, input_evidence=evidence, execution_evidence_store=self.execution_evidence, quote_store=self.quotes, reconciled_accounts=self.reconciled_accounts, risk_policy_version="risk:default", portfolio_policy_version="portfolio:default", policy_registry=self.policy_registry)
        self.assertIn("stored_execution_evidence_invalid", result.evidence_block_reason)

    def test_quote_store_rejects_caller_market_override(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        overridden = MarketSnapshot("TEST:SPY", self.now, Decimal("99"), Decimal("100"), Decimal("100"), Decimal("1"))
        result = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), market=overridden)
        self.assertIn("stored_quote_evidence_invalid:stored_quote_evidence_mismatch", result.evidence_block_reason)

    def test_reconciled_positions_reject_caller_projected_exposure_override(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        override = [PortfolioExposure("TEST:SPY", Decimal("1"), "ETF", "")]
        result = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), projected_exposures=override)
        self.assertIn("stored_portfolio_evidence_invalid:stored_portfolio_evidence_mismatch", result.evidence_block_reason)

    def test_oms_derived_profile_factor_blocks_portfolio_limit(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        result = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), portfolio_policy=PortfolioRiskPolicy(Decimal("10000"), Decimal("1"), Decimal("10000"), maximum_factor_notionals={"market": Decimal("999")}))
        self.assertTrue(result.risk_decision.decision.value == "APPROVE")
        self.assertFalse(result.portfolio_decision.approved)
        self.assertIn("factor_exposure_limit:market", result.portfolio_decision.reasons)

    def test_oms_derived_profile_group_beta_and_duration_block_limits(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        policies_and_reasons = (
            (PortfolioRiskPolicy(Decimal("10000"), Decimal("1"), Decimal("10000"), maximum_group_notionals={"sector:INDEX": Decimal("999")}), "group_exposure_limit:sector:INDEX"),
            (PortfolioRiskPolicy(Decimal("10000"), Decimal("1"), Decimal("10000"), maximum_beta_notional=Decimal("999")), "beta_exposure_limit"),
            (PortfolioRiskPolicy(Decimal("10000"), Decimal("1"), Decimal("10000"), maximum_duration_notional=Decimal("999")), "duration_exposure_limit"),
        )
        for policy, reason in policies_and_reasons:
            result = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), portfolio_policy=policy)
            self.assertFalse(result.portfolio_decision.approved)
            self.assertIn(reason, result.portfolio_decision.reasons)

    def test_oms_derived_multi_instrument_group_aggregates(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        result = self._assess(OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100")), portfolio_policy=PortfolioRiskPolicy(Decimal("10000"), Decimal("1"), Decimal("10000"), maximum_group_notionals={"country:US": Decimal("1499")}))
        self.assertIn("group_exposure_limit:country:US", result.portfolio_decision.reasons)

    def test_reconciled_account_rejects_caller_buying_power_override(self) -> None:
        proposal = self._proposal(); self.signals.append(proposal); self.signals.append_validation(SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}))
        altered = BrokerAccountSnapshot("paper", "local", self.now, CashBalance("USD", Decimal("10000")), Decimal("1"), {}, (), (), Decimal("0"), Decimal("0"))
        intent = OrderIntent(uuid4(), proposal.signal_id, "TEST:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"))
        snapshot = self.execution_evidence.snapshot_as_of("TEST:SPY", OrderSide.BUY, self.now)
        _, exposures = self.reconciled_accounts.projected_exposures_as_of(account_id="paper", intent=intent, decision_at=self.now, instruments=self.instruments, quotes=self.quotes)
        evidence = snapshot.pretrade_input_evidence(market_reference=f"{self.quote.provider}:{self.quote.source_reference}:{self.quote.quote_id}", market=self.quote.market_snapshot(), exposure_reference=self.account_evidence.exposure_reference, exposure_observed_at=self.now, exposures=exposures)
        result = assess_pretrade(intent=intent, signal_store=self.signals, market=self.quote.market_snapshot(), portfolio_state=self.reconciled_accounts.portfolio_state_as_of("paper", self.now), instruments=self.instruments, broker_snapshot=altered, broker_healthy=True, models=self.models, model_id=self.model.model_id, strategy_enabled=True, instrument_halted=False, quote_available=True, expected_slippage_fraction=Decimal("0.001"), event_risk=Decimal("0.1"), projected_exposures=exposures, portfolio_equity=Decimal("10000"), scenario=StressScenario("none", {}), observed_at=self.now, input_evidence=evidence, execution_evidence_store=self.execution_evidence, quote_store=self.quotes, reconciled_accounts=self.reconciled_accounts, risk_policy_version="risk:default", portfolio_policy_version="portfolio:default", policy_registry=self.policy_registry)
        self.assertIn("stored_account_evidence_invalid:stored_account_evidence_mismatch", result.evidence_block_reason)


if __name__ == "__main__":
    unittest.main()
