import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from tests.test_paper_execution import intent
from trade_platform.broker_adapter import (
    BrokerConfiguration,
    BrokerMode,
    InternalAccountState,
    SandboxPaperBrokerAdapter,
)
from trade_platform.broker_sync import PaperBrokerSyncService, SQLiteBrokerEventStore
from trade_platform.domain import RiskDecision, RiskDecisionType
from trade_platform.operational_alerts import SQLiteOperationalAlertStore
from trade_platform.paper_execution import CashBalance
from trade_platform.paper_oms import SQLitePaperOms
from trade_platform.policy_registry import PolicyDocument, SQLitePolicyRegistry
from trade_platform.portfolio_risk import (
    PortfolioExposure,
    PortfolioRiskPolicy,
    StressScenario,
    evaluate_portfolio,
)
from trade_platform.pretrade_assessment import (
    PreTradeAssessment,
    PreTradeInputEvidence,
    SQLitePreTradeAssessmentStore,
)


class BrokerSyncTests(unittest.TestCase):
    integrity_key = b"test-assessment-integrity-key"
    @staticmethod
    def portfolio_decision(*, approved: bool = True):
        decision = evaluate_portfolio([], Decimal("100"), StressScenario("none", {}), PortfolioRiskPolicy(Decimal("100"), Decimal("1"), Decimal("100")))
        return decision if approved else evaluate_portfolio([PortfolioExposure("US:NYSE:SPY", Decimal("200"), "EQUITY", "INDEX")], Decimal("100"), StressScenario("none", {}), PortfolioRiskPolicy(Decimal("100"), Decimal("1"), Decimal("100")))

    @staticmethod
    def assessment_context(oms, adapter, events, *, alerts=None):
        registry = SQLitePolicyRegistry()
        approved_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        registry.append(PolicyDocument("risk", "risk:default", {"maximum_order_notional": "10000"}, "risk-committee", approved_at))
        registry.append(PolicyDocument("portfolio", "portfolio:default", {"maximum_gross_notional": "10000", "maximum_single_weight": "1", "maximum_scenario_loss": "10000"}, "risk-committee", approved_at))
        assessments = SQLitePreTradeAssessmentStore(integrity_key=BrokerSyncTests.integrity_key)
        return PaperBrokerSyncService(oms, adapter, events, alerts, assessment_store=assessments, policy_registry=registry), assessments, registry

    @staticmethod
    def submit(service, assessments, registry, order_intent, risk_decision, portfolio_decision):
        observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        evidence = PreTradeInputEvidence("quote:1", observed_at, "halt:1", observed_at, "event:1", observed_at, "slippage:1", observed_at, "account:1", observed_at, "exposures:1")
        assessment = PreTradeAssessment(uuid4(), risk_decision, portfolio_decision, risk_policy_version="risk:default", portfolio_policy_version="portfolio:default", risk_policy_digest=registry.get("risk", "risk:default").digest, portfolio_policy_digest=registry.get("portfolio", "portfolio:default").digest, input_evidence=evidence)
        assessments.append(assessment)
        return service.submit_after_assessment(order_intent, assessment)

    def test_risk_to_oms_broker_fill_and_reconciliation_are_durable_and_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.sqlite"
            oms = SQLitePaperOms(path)
            adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local-sandbox", BrokerMode.SANDBOX_PAPER, "paper", "TOKEN_REF"), cash=CashBalance("USD", Decimal("10000")))
            events = SQLiteBrokerEventStore(path)
            service, assessments, registry = self.assessment_context(oms, adapter, events)
            order_intent = intent()
            decision = RiskDecision.create(order_intent.intent_id, RiskDecisionType.APPROVE, [])
            self.assertEqual(self.submit(service, assessments, registry, order_intent, decision, self.portfolio_decision()).status.value, "ACKNOWLEDGED")
            adapter.simulate_fill("fill-1", order_intent.intent_id, Decimal("10"), Decimal("100"), fee=Decimal("1"))
            self.assertEqual(len(service.sync_events()), 1)
            self.assertEqual(service.sync_events(), ())
            self.assertEqual(oms.get(order_intent.intent_id).status.value, "FILLED")
            snapshot = adapter.sync_account()
            internal = InternalAccountState(snapshot.cash, snapshot.positions, snapshot.open_intent_ids, snapshot.fill_ids, snapshot.realized_pnl, snapshot.fees)
            self.assertTrue(service.reconcile(internal).complete)
            self.assertTrue(oms.latest_reconciliation("paper").complete)
            self.assertTrue(assessments.get_for_intent(order_intent.intent_id).approved)
            registry.close(); assessments.close(); events.close(); oms.close()

    def test_rejected_risk_decision_never_reaches_adapter(self) -> None:
        oms = SQLitePaperOms()
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local-sandbox", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("100")))
        events = SQLiteBrokerEventStore()
        service, assessments, registry = self.assessment_context(oms, adapter, events)
        order_intent = intent()
        rejected = RiskDecision.create(order_intent.intent_id, RiskDecisionType.REJECT, ["data"])
        self.assertEqual(self.submit(service, assessments, registry, order_intent, rejected, self.portfolio_decision()).status.value, "RISK_REJECTED")
        self.assertEqual(adapter.order_events(), ())
        registry.close(); assessments.close(); events.close(); oms.close()

    def test_paper_submission_requires_persisted_input_evidence(self) -> None:
        oms = SQLitePaperOms()
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local-sandbox", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("100")))
        events = SQLiteBrokerEventStore()
        service, assessments, registry = self.assessment_context(oms, adapter, events)
        order_intent = intent()
        assessment = PreTradeAssessment(uuid4(), RiskDecision.create(order_intent.intent_id, RiskDecisionType.APPROVE, []), self.portfolio_decision(), risk_policy_version="risk:default", portfolio_policy_version="portfolio:default", risk_policy_digest=registry.get("risk", "risk:default").digest, portfolio_policy_digest=registry.get("portfolio", "portfolio:default").digest)
        assessments.append(assessment)
        with self.assertRaisesRegex(ValueError, "pretrade_input_evidence_unavailable"):
            service.submit_after_assessment(order_intent, assessment)
        self.assertEqual(adapter.order_events(), ())
        registry.close(); assessments.close(); events.close(); oms.close()

    def test_broker_replace_and_cancel_events_are_mirrored_into_oms(self) -> None:
        oms = SQLitePaperOms()
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local-sandbox", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("10000")))
        events = SQLiteBrokerEventStore()
        service, assessments, registry = self.assessment_context(oms, adapter, events)
        order_intent = intent()
        self.submit(service, assessments, registry, order_intent, RiskDecision.create(order_intent.intent_id, RiskDecisionType.APPROVE, []), self.portfolio_decision())
        adapter.replace(order_intent.intent_id, quantity=Decimal("12"), limit_price=Decimal("101"))
        service.sync_events()
        self.assertEqual((oms.get(order_intent.intent_id).intent.quantity, oms.get(order_intent.intent_id).intent.limit_price), (Decimal("12"), Decimal("101")))
        adapter.cancel(order_intent.intent_id)
        service.sync_events()
        self.assertEqual(oms.get(order_intent.intent_id).status.value, "CANCELLED")
        registry.close(); assessments.close(); events.close(); oms.close()

    def test_failed_reconciliation_opens_durable_critical_alert(self) -> None:
        oms = SQLitePaperOms(); alerts = SQLiteOperationalAlertStore()
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local-sandbox", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("10000")))
        events = SQLiteBrokerEventStore(); service = PaperBrokerSyncService(oms, adapter, events, alerts)
        mismatched = InternalAccountState(CashBalance("USD", Decimal("0")), {}, (), (), Decimal("0"), Decimal("0"))
        self.assertFalse(service.reconcile(mismatched).complete)
        self.assertEqual(alerts.active()[0].code, "RECONCILIATION_FAILED")
        alerts.close(); events.close(); oms.close()

    def test_complete_reconciliation_persists_matching_account_and_position_evidence(self) -> None:
        oms = SQLitePaperOms(); events = SQLiteBrokerEventStore()
        adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local-sandbox", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("10000")))
        service = PaperBrokerSyncService(oms, adapter, events)
        broker_snapshot = adapter.sync_account()
        internal = InternalAccountState(broker_snapshot.cash, broker_snapshot.positions, broker_snapshot.open_intent_ids, broker_snapshot.fill_ids, broker_snapshot.realized_pnl, broker_snapshot.fees)
        self.assertTrue(service.reconcile(internal).complete)
        account = oms.latest_reconciled_account("paper", broker_snapshot.as_of + timedelta(seconds=1))
        self.assertEqual((account.snapshot.cash, account.snapshot.buying_power, account.snapshot.positions, account.snapshot.open_intent_ids, account.snapshot.fill_ids), (broker_snapshot.cash, broker_snapshot.buying_power, broker_snapshot.positions, broker_snapshot.open_intent_ids, broker_snapshot.fill_ids))
        self.assertEqual(account.snapshot.positions, broker_snapshot.positions)
        events.close(); oms.close()

    def test_portfolio_risk_block_never_reaches_adapter_and_is_durable(self) -> None:
        oms = SQLitePaperOms(); adapter = SandboxPaperBrokerAdapter(BrokerConfiguration("local-sandbox", BrokerMode.SIMULATED_PAPER, "paper"), cash=CashBalance("USD", Decimal("100")))
        events = SQLiteBrokerEventStore()
        service, assessments, registry = self.assessment_context(oms, adapter, events)
        order_intent = intent()
        result = self.submit(service, assessments, registry, order_intent, RiskDecision.create(order_intent.intent_id, RiskDecisionType.APPROVE, []), self.portfolio_decision(approved=False))
        self.assertEqual(result.status.value, "RISK_REJECTED")
        self.assertEqual(adapter.order_events(), ())
        self.assertFalse(assessments.get_for_intent(order_intent.intent_id).approved)
        registry.close(); assessments.close(); events.close(); oms.close()


if __name__ == "__main__":
    unittest.main()
