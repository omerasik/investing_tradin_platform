import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_platform.agent_research import (
    AgentResearchOutput,
    ResearchAgentRole,
    SQLiteAgentResearchStore,
)
from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.broker_adapter import BrokerAccountSnapshot
from trade_platform.config import PlatformConfig
from trade_platform.domain import (
    OrderIntent,
    OrderSide,
    OrderStatus,
    RiskDecision,
    RiskDecisionType,
)
from trade_platform.fundamentals import FundamentalFact, SQLiteFundamentalStore, StatementType
from trade_platform.investments import (
    CompanyResearchRecord,
    DcfValuation,
    FundamentalAnalytics,
    InvestmentHolding,
    InvestmentMacroSensitivity,
    InvestmentPerformanceSnapshot,
    InvestmentPortfolioPolicy,
    InvestmentRebalanceDecision,
    InvestmentRecommendation,
    InvestmentReviewSchedule,
    InvestmentThemeExposure,
    InvestmentThesis,
    SourceBackedFact,
    SQLiteInvestmentStore,
    ThemeDiscoveryRule,
    ThesisDriftAssessment,
    ThesisExpectation,
    ThesisReview,
    ThesisReviewOutcome,
    ThesisStatus,
    discover_themes_from_company_research,
)
from trade_platform.operational_alerts import AlertSeverity, SQLiteOperationalAlertStore
from trade_platform.paper_execution import CashBalance, PaperOrder, Position, ReconciliationResult
from trade_platform.paper_oms import SQLitePaperOms
from trade_platform.research import SQLiteExperimentStore
from trade_platform.return_history import PortfolioReturnObservation, SQLitePortfolioReturnStore
from trade_platform.risk import SQLiteRiskDecisionStore
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator
from trade_platform.strategy_promotion import (
    PromotionDecision,
    PromotionStatus,
    SQLitePromotionLedger,
)
from trade_platform.strategy_validation import SQLiteStrategyRegistry, StrategyRunCard


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            build_app(
                PlatformConfig(),
                SQLiteAuditStore(),
                OperatorAuthenticator("test-token"),
                InMemoryRateLimiter(max_requests=2),
            )
        )
        self.headers = {"Authorization": "Bearer test-token"}

    def test_health_never_advertises_live_trading(self) -> None:
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["live_trading_enabled"])

    def test_operational_alerts_are_protected_and_acknowledged_with_actor(self) -> None:
        alerts = SQLiteOperationalAlertStore(); alert = alerts.raise_alert(source="investment_review", code="THESIS_DRIFT_DETECTED", severity=AlertSeverity.WARNING, resource="thesis:test", details={"breached_metrics":"free_cash_flow"})
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), alert_store=alerts))
        self.assertEqual(client.get("/alerts").status_code, 401)
        active = client.get("/alerts", headers=self.headers)
        acknowledged = client.post(f"/alerts/{alert.alert_id}/acknowledge", headers=self.headers)
        self.assertEqual((active.json()[0]["code"], acknowledged.status_code, acknowledged.json()["status"]), ("THESIS_DRIFT_DETECTED", 200, "ACKNOWLEDGED"))

    def test_agent_research_workflow_is_authenticated_and_read_only(self) -> None:
        now = datetime(2026, 7, 1, tzinfo=timezone.utc); workflow_id = uuid4(); agents = SQLiteAgentResearchStore()
        entry = AgentResearchOutput(uuid4(), workflow_id, "US:NASDAQ:ACME", ResearchAgentRole.TECHNICAL, now, ("fact",), ("inference",), ("source:1",), 0.5, ("missing",), (), "prompt-v1", "model-v1"); agents.append(entry)
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), agent_research_store=agents))
        self.assertEqual(client.get(f"/research-agents/workflows/{workflow_id}").status_code, 401)
        response = client.get(f"/research-agents/workflows/{workflow_id}", headers=self.headers)
        self.assertEqual((response.status_code, response.json()["outputs"][0]["role"], response.json()["outputs"][0]["facts"], response.json()["reviews"]), (200, "TECHNICAL", ["fact"], []))
        queried = client.get(f"/research-agents/workflows/{workflow_id}/query", params={"q":"show technical facts"}, headers=self.headers)
        self.assertEqual((queried.status_code, queried.json()["intent"], queried.json()["results"][0]["role"]), (200, "facts", "TECHNICAL"))
        self.assertEqual(client.get(f"/research-agents/workflows/{workflow_id}/operator-ready").status_code, 401)
        self.assertEqual(client.get(f"/research-agents/workflows/{workflow_id}/operator-ready", headers=self.headers).status_code, 409)
        self.assertEqual(client.post(f"/research-agents/workflows/{workflow_id}", headers=self.headers).status_code, 405)

    def test_paper_oms_evidence_views_are_authenticated_and_read_only(self) -> None:
        now = datetime(2026, 7, 1, tzinfo=timezone.utc); oms = SQLitePaperOms()
        order = PaperOrder(OrderIntent(uuid4(), uuid4(), "US:NYSE:SPY", "paper-account", OrderSide.BUY, Decimal("10"), Decimal("100"), now))
        oms.create(order)
        for status in (OrderStatus.RISK_PENDING, OrderStatus.APPROVED, OrderStatus.SUBMISSION_PENDING, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED):
            oms.transition(order.intent.intent_id, status)
        oms.ingest_fill("fixture-fill", order.intent.intent_id, Decimal("4"), Decimal("101"), occurred_at=now)
        snapshot = BrokerAccountSnapshot("paper-account", "fixture-sandbox", now, CashBalance("USD", Decimal("1000")), Decimal("900"), {"US:NYSE:SPY": Position("US:NYSE:SPY", Decimal("4"), Decimal("101"))}, (order.intent.intent_id,), ("fixture-fill",), Decimal("0"), Decimal("1"))
        oms.record_reconciliation_with_account(snapshot, ReconciliationResult(True, ()), healthy=True, ingested_at=now)
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), paper_oms=oms))
        order_path = f"/paper-oms/orders/{order.intent.intent_id}"; reconciliation_path = "/paper-oms/accounts/paper-account/reconciliation"
        self.assertEqual(client.get(order_path).status_code, 401)
        evidence = client.get(order_path, headers=self.headers); reconciliation = client.get(reconciliation_path, headers=self.headers)
        self.assertEqual((evidence.status_code, evidence.json()["paper_only"], evidence.json()["status"], evidence.json()["filled_quantity"], len(evidence.json()["events"])), (200, True, "PARTIALLY_FILLED", "4", 7))
        self.assertEqual((reconciliation.status_code, reconciliation.json()["complete"], reconciliation.json()["reconciled_account"]["buying_power"]), (200, True, "900"))
        self.assertEqual(client.post(order_path, headers=self.headers).status_code, 405)

    def test_risk_decision_evidence_is_authenticated_and_read_only(self) -> None:
        decisions = SQLiteRiskDecisionStore(); intent_id = uuid4()
        decisions.append(RiskDecision.create(intent_id, RiskDecisionType.REJECT, ["order_notional_limit"]))
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), risk_decisions=decisions))
        path = f"/risk/decisions/{intent_id}"
        self.assertEqual(client.get(path).status_code, 401)
        response = client.get(path, headers=self.headers)
        self.assertEqual((response.status_code, response.json()["decisions"][0]["decision"], response.json()["decisions"][0]["reasons"]), (200, "REJECT", ["order_notional_limit"]))
        self.assertEqual(client.post(path, headers=self.headers).status_code, 405)

    def test_research_artifacts_are_authenticated_and_read_only(self) -> None:
        now = datetime(2026, 7, 1, tzinfo=timezone.utc); strategies = SQLiteStrategyRegistry(); experiments = SQLiteExperimentStore(); promotions = SQLitePromotionLedger()
        card = StrategyRunCard.create(strategy_version="trend-v1", family="trend_following", hypothesis="Trend persists after costs.", required_datasets=("bars-v1",), feature_versions=("return:v1",), universe_rules="Liquid ETFs", entry_logic="Enter on trend", exit_logic="Exit on reversal", sizing_policy="Volatility target", risk_policy="risk-v1", cost_model_version="cost-v1", capacity_model="capacity-v1", expected_regimes=("BULL",), parameter_schema={"lookback":"integer"}, failure_conditions=("whipsaw",), limitations=("no short borrow",)); strategies.append(card)
        experiment = experiments.append("trend-v1", "bars-v1", ("return:v1",), "cost-v1", {"lookback": 20}, {"total_return": Decimal("0.1"), "periods": 10})
        promotion = PromotionDecision(uuid4(), card.strategy_id, "trend-v1", PromotionStatus.BLOCKED, ("missing_stress_evidence",), 10, Decimal("0.1"), now); promotions.append(promotion)
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), strategy_registry=strategies, experiment_store=experiments, promotion_ledger=promotions))
        strategy_path = f"/research/strategies/{card.strategy_id}"; experiment_path = f"/research/experiments/{experiment.experiment_id}"; promotion_path = f"/research/promotions/{promotion.decision_id}"
        self.assertEqual(client.get(strategy_path).status_code, 401)
        self.assertEqual((client.get(strategy_path, headers=self.headers).json()["family"], client.get(experiment_path, headers=self.headers).json()["report"]["total_return"], client.get(promotion_path, headers=self.headers).json()["status"]), ("trend_following", "0.1", "BLOCKED"))
        self.assertEqual(client.post(experiment_path, headers=self.headers).status_code, 405)

    def test_backtest_launch_is_authenticated_idempotent_and_research_only(self) -> None:
        strategies = SQLiteStrategyRegistry(); experiments = SQLiteExperimentStore()
        card = StrategyRunCard.create(strategy_version="trend-v1", family="trend_following", hypothesis="Trend persists after costs.", required_datasets=("bars-v1",), feature_versions=("return:v1",), universe_rules="Liquid ETFs", entry_logic="Enter on trend", exit_logic="Exit on reversal", sizing_policy="Volatility target", risk_policy="risk-v1", cost_model_version="cost-v1", capacity_model="capacity-v1", expected_regimes=("BULL",), parameter_schema={"fast_window":"integer", "slow_window":"integer"}, failure_conditions=("whipsaw",), limitations=("no short borrow",)); strategies.append(card)
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), strategy_registry=strategies, experiment_store=experiments))
        payload = {"strategy_id": str(card.strategy_id), "dataset_version": "fixture-v1", "parameters": {"fast_window": 2, "slow_window": 3}, "closes": ["10", "11", "12", "13"], "percentage_per_turnover": "0.01", "pessimistic_cost_multiplier": "3", "idempotency_key": "backtest-1"}
        self.assertEqual(client.post("/research/backtests", json=payload).status_code, 401)
        first = client.post("/research/backtests", json=payload, headers=self.headers); replay = client.post("/research/backtests", json=payload, headers=self.headers)
        self.assertEqual((first.status_code, first.json()["experiment_id"], replay.json()["experiment_id"]), (201, replay.json()["experiment_id"], replay.json()["experiment_id"]))
        self.assertEqual(first.json()["report"]["pessimistic_cost_multiplier"], "3")
        self.assertLess(Decimal(first.json()["report"]["pessimistic_total_return"]), Decimal(first.json()["report"]["total_return"]))
        self.assertEqual(client.post("/research/backtests", json={**payload, "parameters": {"fast_window": 1, "slow_window": 3}}, headers=self.headers).status_code, 422)
        self.assertEqual(client.post("/research/backtests", json={**payload, "idempotency_key": "backtest-invalid-stress", "pessimistic_cost_multiplier": "0.5"}, headers=self.headers).status_code, 422)

    def test_backtest_launch_requires_complete_non_overlapping_walk_forward_protocol(self) -> None:
        strategies = SQLiteStrategyRegistry(); experiments = SQLiteExperimentStore()
        card = StrategyRunCard.create(strategy_version="momentum-v1", family="momentum", hypothesis="Momentum persists after costs.", required_datasets=("bars-v1",), feature_versions=("return:v1",), universe_rules="Liquid ETFs", entry_logic="Enter on momentum", exit_logic="Exit on reversal", sizing_policy="Volatility target", risk_policy="risk-v1", cost_model_version="cost-v1", capacity_model="capacity-v1", expected_regimes=("BULL",), parameter_schema={"lookback":"integer"}, failure_conditions=("whipsaw",), limitations=("no short borrow",)); strategies.append(card)
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), strategy_registry=strategies, experiment_store=experiments))
        payload = {"strategy_id": str(card.strategy_id), "dataset_version": "fixture-v2", "parameters": {"lookback": 2}, "closes": [str(value) for value in range(100, 145)], "idempotency_key": "backtest-walk-forward", "walk_forward_train_size": 10, "walk_forward_validation_size": 4, "walk_forward_test_size": 5, "walk_forward_step": 5, "walk_forward_purge": 1, "walk_forward_embargo": 1}
        response = client.post("/research/backtests", json=payload, headers=self.headers)
        self.assertEqual((response.status_code, response.json()["report"]["walk_forward_status"], response.json()["report"]["independent_bar_engine_reconciled"]), (201, "EXECUTED", "1"))
        self.assertEqual(client.post("/research/backtests", json={**payload, "idempotency_key": "partial-walk-forward", "walk_forward_step": None}, headers=self.headers).status_code, 422)
        self.assertEqual(client.post("/research/backtests", json={**payload, "idempotency_key": "overlap-walk-forward", "walk_forward_step": 4}, headers=self.headers).status_code, 422)

    def test_strategy_creation_is_authenticated_and_idempotent(self) -> None:
        registry = SQLiteStrategyRegistry(); client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), strategy_registry=registry))
        payload = {"strategy_version":"momentum-v1", "family":"momentum", "hypothesis":"Momentum persists after costs.", "required_datasets":["bars-v1"], "feature_versions":["return:v1"], "universe_rules":"Liquid ETFs", "entry_logic":"Enter on momentum", "exit_logic":"Exit on reversal", "sizing_policy":"Volatility target", "risk_policy":"risk-v1", "cost_model_version":"cost-v1", "capacity_model":"capacity-v1", "expected_regimes":["BULL"], "parameter_schema":{"lookback":"integer"}, "failure_conditions":["whipsaw"], "limitations":["no short borrow"], "idempotency_key":"strategy-1"}
        self.assertEqual(client.post("/research/strategies", json=payload).status_code, 401)
        first = client.post("/research/strategies", json=payload, headers=self.headers); replay = client.post("/research/strategies", json=payload, headers=self.headers)
        self.assertEqual((first.status_code, first.json()["strategy_id"], replay.json()["strategy_id"]), (201, replay.json()["strategy_id"], replay.json()["strategy_id"]))
        self.assertEqual(client.post("/research/strategies", json={**payload, "family":"breakout"}, headers=self.headers).status_code, 422)

    def test_audit_event_round_trip(self) -> None:
        created = self.client.post(
            "/audit/events",
            json={"event_type": "risk.rejected", "actor": "risk-engine", "payload": {"reason": "stale"}},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201)
        events = self.client.get("/audit/events", headers=self.headers)
        self.assertEqual(events.status_code, 200)
        self.assertEqual(events.json()[0]["event_id"], created.json()["event_id"])
        self.assertEqual(events.json()[0]["actor"], "local-operator")

    def test_operational_endpoints_require_bearer_token(self) -> None:
        self.assertEqual(self.client.get("/audit/events").status_code, 401)

    def test_unconfigured_authentication_fails_closed(self) -> None:
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator(None)))
        self.assertEqual(client.get("/audit/events").status_code, 503)

    def test_rate_limit_blocks_excess_requests(self) -> None:
        self.assertEqual(self.client.get("/audit/events", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/audit/events", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/audit/events", headers=self.headers).status_code, 429)

    def test_metrics_are_protected_and_report_activity(self) -> None:
        self.client.get("/health/live")
        response = self.client.get("/observability/metrics", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health.liveness.requests"], 1)

    def test_portfolio_risk_report_is_protected_and_explainable(self) -> None:
        response = self.client.post("/risk/portfolio", headers=self.headers, json={"equity": 1000, "exposures": [{"instrument_id": "US:NYSE:SPY", "notional": 600, "asset_class": "EQUITY", "sector": "INDEX"}], "shocks": {"EQUITY": -0.1}, "maximum_gross_notional": 500, "maximum_single_weight": 0.5, "maximum_scenario_loss": 50})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["approved"])
        self.assertIn("gross_exposure_limit", response.json()["reasons"])

    def test_return_ingestion_health_and_commands_are_operator_protected(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=timezone.utc); store = SQLitePortfolioReturnStore()
        class Provider:
            provider_name = "fixture"
            def fetch_returns(self, account_id, *, start, end): return [PortfolioReturnObservation(uuid4(), account_id, start, end, Decimal("0.01"), self.provider_name, "source", end)]
        store.ingest_authorized(Provider(), "paper", start=now - timedelta(days=1), end=now, actor="operator", idempotency_key="returns-1")
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), return_history=store))
        self.assertEqual(client.get("/data-health/return-providers/fixture").status_code, 401)
        health = client.get("/data-health/return-providers/fixture", headers=self.headers); evidence = client.get("/data-health/return-ingestion/accounts/paper", headers=self.headers)
        self.assertTrue(health.json()["healthy"]); self.assertEqual(evidence.json()["commands"][0]["actor"], "operator")
        store.close()

    def test_return_ingestion_cadence_is_authenticated_and_reports_due_work(self) -> None:
        store = SQLitePortfolioReturnStore(); client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), return_history=store))
        payload = {"account_id": "paper", "provider": "fixture", "interval_seconds": 60, "grace_seconds": 0}
        self.assertEqual(client.post("/data-health/return-ingestion/cadence", json=payload).status_code, 401)
        created = client.post("/data-health/return-ingestion/cadence", json=payload, headers=self.headers)
        self.assertEqual(created.status_code, 201)
        due = client.get("/data-health/return-ingestion/due", headers=self.headers)
        self.assertEqual((due.status_code, due.json()[0]["provider"], due.json()[0]["approved_by"]), (200, "fixture", "local-operator"))
        store.close()

    def test_investment_history_is_authenticated_point_in_time_and_read_only(self) -> None:
        now = datetime(2026, 5, 1, tzinfo=timezone.utc); thesis_id = uuid4(); fact_id = uuid4()
        investments = SQLiteInvestmentStore()
        thesis = InvestmentThesis(thesis_id, "US:NASDAQ:ACME", "v1", "Cash generation supports value", 365, ("margin recovery",), ("cash flow falls",), ("filing:q1",), ThesisStatus.ACTIVE, now)
        fact = SourceBackedFact(fact_id, thesis.instrument_id, "free_cash_flow", Decimal("100"), "USD", now - timedelta(days=1), now, now, "fundamental:fixture:q1:0", "fundamental://fixture/q1")
        investments.append_thesis(thesis); investments.append_fact(fact)
        valuation = DcfValuation(uuid4(), thesis_id, thesis.instrument_id, now, "USD", Decimal("100"), Decimal("0.03"), Decimal("0.1"), Decimal("0.02"), 5, Decimal("10"), (fact_id,))
        investments.append_valuation(valuation)
        investments.append_fundamental_analytics(FundamentalAnalytics(uuid4(), thesis_id, now, "USD", Decimal("0.1"), Decimal("0.1"), Decimal("0.12"), Decimal("0.5"), Decimal("30"), (fact_id,)))
        investments.append_recommendation(InvestmentRecommendation(uuid4(), thesis_id, Decimal("0.1"), Decimal("0.2"), Decimal("0.7"), Decimal("0.03"), Decimal("0.08"), ("valuation",), now))
        investments.append_company_research(CompanyResearchRecord(uuid4(), thesis_id, thesis.instrument_id, now, "Demand exceeds plan", "Demand normalizes", "Margins compress", ("launch",), ("two revenue misses",), "bounded by downside", ("US:NYSE:ALT",), ("filing:q1",)))
        discover_themes_from_company_research(investments, thesis_id, (ThemeDiscoveryRule("Product launch", ("launch",), "themes-v1"),), now)
        investments.set_review_schedule(InvestmentReviewSchedule(thesis_id, 30, (ThesisExpectation("free_cash_flow", Decimal("200"), None),), now, "committee")); investments.run_due_thesis_drift(now)
        drift = ThesisDriftAssessment(thesis_id, now, (fact_id,), ("free_cash_flow",))
        investments.append_review(ThesisReview(uuid4(), thesis_id, ThesisReviewOutcome.REVISE, now, "Cash flow fell below plan", ("filing:q1",), drift, now + timedelta(days=90)))
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), investment_store=investments))
        self.assertEqual(client.get(f"/investments/theses/{thesis_id}").status_code, 401)
        response = client.get(f"/investments/theses/{thesis_id}", params={"as_of": now.isoformat()}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual((payload["thesis"]["status"], payload["facts"][0]["source_reference"], payload["analytics"][0]["net_debt"], payload["reviews"][0]["drift"]["detected"], payload["company_research"][0]["bear_case"], payload["theme_discoveries"][0]["theme"], payload["review_schedule"]["cadence_days"], payload["scheduled_drift_history"][0]["detected"]), ("ACTIVE", "fundamental:fixture:q1:0", "30", True, "Margins compress", "Product launch", 30, True))
        self.assertGreater(Decimal(payload["valuations"][0]["intrinsic_value_per_share"]), Decimal("0"))
        self.assertEqual(client.post(f"/investments/theses/{thesis_id}", headers=self.headers).status_code, 405)
        investments.close()

    def test_fundamental_materialization_api_is_authenticated_attributed_and_replay_safe(self) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc); investments = SQLiteInvestmentStore(); fundamentals = SQLiteFundamentalStore()
        fundamentals.ingest([FundamentalFact("US:NASDAQ:ACME", StatementType.CASH_FLOW, "free_cash_flow", now - timedelta(days=90), now,
            now, now, now, Decimal("100"), "USD", "USD", "fixture-filings", "acme-q1-fcf")])
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), investment_store=investments, fundamental_store=fundamentals))
        payload = {"instrument_id": "US:NASDAQ:ACME", "as_of": now.isoformat(), "fact_names": ["free_cash_flow"], "idempotency_key": "materialize-q1"}
        self.assertEqual(client.post("/investments/fundamental-materializations", json=payload).status_code, 401)
        created = client.post("/investments/fundamental-materializations", json=payload, headers=self.headers)
        replay = client.post("/investments/fundamental-materializations", json=payload, headers=self.headers)
        self.assertEqual((created.status_code, created.json()["actor"], replay.json()["fact_ids"]), (201, "local-operator", created.json()["fact_ids"]))
        commands = client.get("/investments/fundamental-materializations/US:NASDAQ:ACME", headers=self.headers)
        self.assertEqual((commands.status_code, commands.json()[0]["status"], commands.json()[0]["actor"]), (200, "SUCCEEDED", "local-operator"))
        investments.close(); fundamentals.close()

    def test_fundamental_materialization_api_retains_and_safely_reports_source_failure(self) -> None:
        class FailingFundamentals:
            def available_as_of(self, *args, **kwargs): raise RuntimeError("upstream unavailable")
        investments = SQLiteInvestmentStore()
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"), InMemoryRateLimiter(max_requests=10), investment_store=investments, fundamental_store=FailingFundamentals()))
        payload = {"instrument_id": "US:NASDAQ:ACME", "as_of": "2026-06-01T00:00:00+00:00", "fact_names": ["free_cash_flow"], "idempotency_key": "materialize-outage"}
        response = client.post("/investments/fundamental-materializations", json=payload, headers=self.headers)
        history = client.get("/investments/fundamental-materializations/US:NASDAQ:ACME", headers=self.headers)
        self.assertEqual((response.status_code, response.json()["detail"], history.json()[0]["status"]), (502, "Fundamental evidence source unavailable.", "FAILED"))
        self.assertEqual(history.json()[0]["error"], "upstream unavailable")
        investments.close()

    def test_investment_portfolio_history_is_operator_read_only(self) -> None:
        now = datetime(2026, 7, 1, tzinfo=timezone.utc); store = SQLiteInvestmentStore()
        store.set_investment_portfolio_policy(InvestmentPortfolioPolicy('core', 'USD', Decimal('1000'), Decimal('0.8'), 'committee', now))
        store.append_investment_holding(InvestmentHolding('core', 'US:NYSE:SPY', Decimal('500'), now, 'custody:1'))
        store.append_theme_exposure(InvestmentThemeExposure('US:NYSE:SPY', 'AI', Decimal('0.2'), now, 'theme:1'))
        store.append_macro_sensitivity(InvestmentMacroSensitivity('US:NYSE:SPY', 'US_CPI', Decimal('-0.1'), now, 'macro:1'))
        store.append_performance_snapshot(InvestmentPerformanceSnapshot('core', now, Decimal('500'), Decimal('0.05'), 'custody:nav:1'))
        store.append_rebalance_decision(InvestmentRebalanceDecision(uuid4(), 'core', now, 'Review concentration', ('assessment:1',), {'US:NYSE:SPY': Decimal('0.8')}, 'committee'))
        client = TestClient(build_app(PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator('test-token'), InMemoryRateLimiter(max_requests=10), investment_store=store))
        self.assertEqual(client.get('/investments/portfolios/core').status_code, 401)
        result = client.get('/investments/portfolios/core', headers=self.headers)
        self.assertEqual((result.status_code, result.json()['assessment']['approved'], result.json()['themes'][0]['theme'], result.json()['performance'][0]['net_asset_value'], result.json()['rebalance_decisions'][0]['rationale']), (200, False, 'AI', '500', 'Review concentration'))
        store.close()


if __name__ == "__main__":
    unittest.main()
