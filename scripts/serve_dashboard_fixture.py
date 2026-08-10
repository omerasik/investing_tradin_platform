"""Local-only, deterministic dashboard fixture service for browser E2E verification.

It deliberately exposes only the paper-safe/read-only application API and is not
an operational server or a source of market data.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import uvicorn

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.investments import (CompanyResearchRecord, InvestmentHolding, InvestmentPerformanceSnapshot,
    InvestmentPortfolioPolicy, InvestmentRebalanceDecision, InvestmentThesis, SQLiteInvestmentStore, ThesisStatus)
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator
from trade_platform.operational_alerts import AlertSeverity, SQLiteOperationalAlertStore
from trade_platform.domain import OrderIntent, OrderSide, OrderStatus, RiskDecision, RiskDecisionType
from trade_platform.paper_execution import CashBalance, PaperOrder, Position, ReconciliationResult
from trade_platform.paper_oms import SQLitePaperOms
from trade_platform.broker_adapter import BrokerAccountSnapshot
from trade_platform.risk import SQLiteRiskDecisionStore
from trade_platform.research import SQLiteExperimentStore
from trade_platform.strategy_promotion import PromotionDecision, PromotionStatus, SQLitePromotionLedger
from trade_platform.strategy_validation import SQLiteStrategyRegistry, StrategyRunCard


def application():
    now = datetime(2026, 7, 1, tzinfo=timezone.utc); store = SQLiteInvestmentStore(); thesis_id = UUID("11111111-1111-1111-1111-111111111111"); oms = SQLitePaperOms(); decisions = SQLiteRiskDecisionStore(); strategies = SQLiteStrategyRegistry(); experiments = SQLiteExperimentStore(); promotions = SQLitePromotionLedger()
    alerts = SQLiteOperationalAlertStore()
    thesis = InvestmentThesis(thesis_id, "US:NASDAQ:ACME", "fixture-v1", "Cash generation supports value", 365, ("margin recovery",), ("cash flow falls",), ("filing:q1",), ThesisStatus.ACTIVE, now)
    store.append_thesis(thesis)
    store.append_company_research(CompanyResearchRecord(UUID("22222222-2222-2222-2222-222222222222"), thesis_id, thesis.instrument_id, now, "Demand exceeds plan", "Demand normalizes", "Margins compress", ("launch",), ("two revenue misses",), "bounded by downside", ("US:NYSE:ALT",), ("filing:q1",)))
    store.set_investment_portfolio_policy(InvestmentPortfolioPolicy("core", "USD", Decimal("1000"), Decimal("0.8"), "committee", now))
    store.append_investment_holding(InvestmentHolding("core", thesis.instrument_id, Decimal("500"), now, "custody:fixture:1"))
    store.append_performance_snapshot(InvestmentPerformanceSnapshot("core", now, Decimal("500"), Decimal("0.05"), "custody:fixture:nav"))
    store.append_rebalance_decision(InvestmentRebalanceDecision(UUID("33333333-3333-3333-3333-333333333333"), "core", now, "Review concentration", ("assessment:1",), {thesis.instrument_id: Decimal("0.8")}, "committee"))
    alerts.raise_alert(source="investment_review", code="THESIS_DRIFT_DETECTED", severity=AlertSeverity.WARNING, resource=f"thesis:{thesis_id}", details={"breached_metrics":"free_cash_flow"})
    intent = OrderIntent(UUID("44444444-4444-4444-4444-444444444444"), UUID("55555555-5555-5555-5555-555555555555"), "US:NYSE:SPY", "paper-account", OrderSide.BUY, Decimal("10"), Decimal("100"), now)
    oms.create(PaperOrder(intent))
    for status in (OrderStatus.RISK_PENDING, OrderStatus.APPROVED, OrderStatus.SUBMISSION_PENDING, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED): oms.transition(intent.intent_id, status)
    oms.ingest_fill("fixture-fill-1", intent.intent_id, Decimal("4"), Decimal("101"), occurred_at=now)
    decisions.append(RiskDecision.create(intent.intent_id, RiskDecisionType.REJECT, ["order_notional_limit"]))
    card = StrategyRunCard(UUID("66666666-6666-6666-6666-666666666666"), "trend-v1", "trend_following", "Trend persists after costs.", ("bars-v1",), ("return:v1",), "Liquid ETFs", "Enter on trend", "Exit on reversal", "Volatility target", "risk-v1", "cost-v1", "capacity-v1", ("BULL",), {"lookback": "integer"}, ("whipsaw",), ("no short borrow",), now); strategies.append(card)
    experiment = experiments.append("trend-v1", "bars-v1", ("return:v1",), "cost-v1", {"lookback": 20}, {"total_return": Decimal("0.1"), "periods": 10}, experiment_id=UUID("77777777-7777-7777-7777-777777777777"))
    promotion = PromotionDecision(UUID("88888888-8888-8888-8888-888888888888"), card.strategy_id, "trend-v1", PromotionStatus.BLOCKED, ("missing_stress_evidence",), 10, Decimal("0.1"), now); promotions.append(promotion)
    snapshot = BrokerAccountSnapshot("paper-account", "fixture-sandbox", now, CashBalance("USD", Decimal("1000")), Decimal("900"), {"US:NYSE:SPY": Position("US:NYSE:SPY", Decimal("4"), Decimal("101"))}, (intent.intent_id,), ("fixture-fill-1",), Decimal("0"), Decimal("1"))
    oms.record_reconciliation_with_account(snapshot, ReconciliationResult(True, ()), healthy=True, ingested_at=now)
    return build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("fixture-token"), InMemoryRateLimiter(max_requests=100), investment_store=store, alert_store=alerts, paper_oms=oms, risk_decisions=decisions, strategy_registry=strategies, experiment_store=experiments, promotion_ledger=promotions)


if __name__ == "__main__":
    uvicorn.run(application(), host="127.0.0.1", port=8765)
