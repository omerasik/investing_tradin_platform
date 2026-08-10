import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory
from uuid import uuid4

from trade_platform.investments import (CompanyResearchRecord, DcfValuation, InvestmentRecommendation, InvestmentReviewSchedule, InvestmentValidationError, ThemeDiscoveryRule,
    InvestmentHolding, InvestmentMacroSensitivity, InvestmentPerformanceSnapshot, InvestmentPortfolioPolicy, InvestmentRebalanceDecision, InvestmentThemeExposure, SQLiteInvestmentStore, SourceBackedFact, ThesisExpectation, ThesisReview, ThesisReviewOutcome, draft_thesis,
    calculate_fundamental_analytics, discover_themes_from_company_research, materialize_fundamental_facts, materialize_fundamental_facts_authorized, plan_rebalance, run_due_thesis_drift_with_alerts, scenario_value)
from trade_platform.fundamentals import FundamentalFact, SQLiteFundamentalStore, StatementType
from trade_platform.operational_alerts import SQLiteOperationalAlertStore


class InvestmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.thesis = draft_thesis("US:NYSE:SPY", "Long-term diversified equity exposure", 365, ("valuation drawdown",), ("experiment:baseline-v1",))

    def test_thesis_round_trip_is_append_only(self) -> None:
        store = SQLiteInvestmentStore(); store.append_thesis(self.thesis)
        self.assertEqual(store.get_thesis(self.thesis.thesis_id), self.thesis)
        with self.assertRaisesRegex(InvestmentValidationError, "duplicate_investment_thesis"):
            store.append_thesis(self.thesis)

    def test_recommendation_requires_evidence_and_bounded_weight(self) -> None:
        with self.assertRaisesRegex(InvestmentValidationError, "invalid_investment_recommendation"):
            InvestmentRecommendation(recommendation_id=self.thesis.thesis_id, thesis_id=self.thesis.thesis_id, target_weight=Decimal("0.6"), maximum_weight=Decimal("0.5"), confidence=Decimal("0.7"), expected_return_low=Decimal("-0.1"), expected_return_high=Decimal("0.1"), rationale_evidence_ids=("evidence",), as_of=self.thesis.created_at)

    def test_rebalance_plan_is_explicit_and_bounded(self) -> None:
        actions = plan_rebalance({"US:NYSE:SPY": Decimal("0.2")}, {"US:NYSE:SPY": Decimal("0.4"), "US:NASDAQ:QQQ": Decimal("0.2")})
        self.assertEqual([(action.instrument_id, action.delta_weight) for action in actions], [("US:NASDAQ:QQQ", Decimal("0.2")), ("US:NYSE:SPY", Decimal("0.2"))])
        with self.assertRaisesRegex(InvestmentValidationError, "target_allocation_exceeds_limit"):
            plan_rebalance({}, {"A": Decimal("0.8"), "B": Decimal("0.3")})

    def test_scenario_reports_weighted_loss(self) -> None:
        self.assertEqual(scenario_value(Decimal("1000"), Decimal("0.3"), Decimal("-0.2")), Decimal("-60.00"))

    def test_source_backed_facts_are_point_in_time_and_revision_safe(self) -> None:
        store = SQLiteInvestmentStore()
        observed = datetime(2026, 1, 1, tzinfo=timezone.utc)
        original = SourceBackedFact(uuid4(), self.thesis.instrument_id, "free_cash_flow", Decimal("100"), "USD",
            observed, observed + timedelta(days=1), observed + timedelta(days=1), "annual-report-2025", "https://issuer.example/report", 0)
        later_revision = SourceBackedFact(uuid4(), self.thesis.instrument_id, "free_cash_flow", Decimal("80"), "USD",
            observed, observed + timedelta(days=10), observed + timedelta(days=10), "annual-report-2025-amended", "https://issuer.example/amendment", 1)
        store.append_fact(original); store.append_fact(later_revision)
        self.assertEqual(store.facts_as_of(self.thesis.instrument_id, observed + timedelta(days=5)), (original,))
        self.assertEqual(store.facts_as_of(self.thesis.instrument_id, observed + timedelta(days=11)), (later_revision,))
        with self.assertRaisesRegex(InvestmentValidationError, "duplicate_investment_fact"):
            store.append_fact(original)

    def test_valuation_requires_visible_source_facts_and_records_drift_review(self) -> None:
        store = SQLiteInvestmentStore(); store.append_thesis(self.thesis)
        observed = datetime(2026, 2, 1, tzinfo=timezone.utc)
        fact = SourceBackedFact(uuid4(), self.thesis.instrument_id, "free_cash_flow", Decimal("90"), "USD",
            observed, observed, observed, "annual-report-2025", "https://issuer.example/report")
        store.append_fact(fact)
        valuation = DcfValuation(uuid4(), self.thesis.thesis_id, self.thesis.instrument_id, observed, "USD", Decimal("90"),
            Decimal("0.03"), Decimal("0.10"), Decimal("0.02"), 5, Decimal("10"), (fact.fact_id,))
        self.assertGreater(valuation.intrinsic_value_per_share(), Decimal("0"))
        store.append_valuation(valuation)
        unavailable = DcfValuation(uuid4(), self.thesis.thesis_id, self.thesis.instrument_id, observed, "USD", Decimal("90"),
            Decimal("0.03"), Decimal("0.10"), Decimal("0.02"), 5, Decimal("10"), (uuid4(),))
        with self.assertRaisesRegex(InvestmentValidationError, "valuation_uses_unavailable_fact"):
            store.append_valuation(unavailable)
        drift = store.assess_thesis_drift(self.thesis.thesis_id, (ThesisExpectation("free_cash_flow", Decimal("100"), None),), observed)
        self.assertTrue(drift.drift_detected)
        review = ThesisReview(uuid4(), self.thesis.thesis_id, ThesisReviewOutcome.REVISE, observed, "Cash flow missed thesis floor",
            ("annual-report-2025",), drift, observed + timedelta(days=90))
        store.append_review(review)
        self.assertEqual(store.reviews_for_thesis(self.thesis.thesis_id), (review,))

    def test_recommendation_and_review_history_survives_reopen(self) -> None:
        with TemporaryDirectory() as directory:
            path = f"{directory}/investments.sqlite"
            store = SQLiteInvestmentStore(path); store.append_thesis(self.thesis)
            recommendation = InvestmentRecommendation(uuid4(), self.thesis.thesis_id, Decimal("0.2"), Decimal("0.3"),
                Decimal("0.7"), Decimal("0.03"), Decimal("0.08"), ("valuation:dcf-1",), self.thesis.created_at)
            store.append_fact(SourceBackedFact(uuid4(), self.thesis.instrument_id, "free_cash_flow", Decimal("100"), "USD",
                self.thesis.created_at, self.thesis.created_at, self.thesis.created_at, "annual-report-2025", "https://issuer.example/report"))
            no_drift = store.assess_thesis_drift(self.thesis.thesis_id, (ThesisExpectation("free_cash_flow", None, Decimal("200")),), self.thesis.created_at)
            self.assertFalse(no_drift.drift_detected)
            review = ThesisReview(uuid4(), self.thesis.thesis_id, ThesisReviewOutcome.REAFFIRM, self.thesis.created_at,
                "No disconfirming evidence is available yet", ("valuation:dcf-1",), no_drift, self.thesis.created_at + timedelta(days=90))
            store.append_recommendation(recommendation); store.append_review(review)
            store.close()
            reopened = SQLiteInvestmentStore(path)
            self.assertEqual(reopened.recommendations_for_thesis(self.thesis.thesis_id), (recommendation,))
            self.assertEqual(reopened.reviews_for_thesis(self.thesis.thesis_id), (review,))
            reopened.close()

    def test_materialized_fundamentals_preserve_point_in_time_provenance(self) -> None:
        investment_store = SQLiteInvestmentStore(); investment_store.append_thesis(self.thesis)
        fundamentals = SQLiteFundamentalStore(); observed = datetime(2026, 3, 31, tzinfo=timezone.utc)
        filed = observed + timedelta(days=20)
        initial = FundamentalFact(self.thesis.instrument_id, StatementType.CASH_FLOW, "free_cash_flow", observed - timedelta(days=90),
            observed, filed, filed, filed, Decimal("100"), "USD", "USD", "filing-provider", "acme-2026q1-fcf")
        revision = FundamentalFact(self.thesis.instrument_id, StatementType.CASH_FLOW, "free_cash_flow", observed - timedelta(days=90),
            observed, filed + timedelta(days=30), filed + timedelta(days=30), filed + timedelta(days=30), Decimal("80"), "USD", "USD", "filing-provider", "acme-2026q1-fcf", 1)
        fundamentals.ingest([initial, revision])
        first = materialize_fundamental_facts(investment_store, fundamentals, self.thesis.instrument_id, filed, fact_names=("free_cash_flow",))
        self.assertEqual(first[0].value, Decimal("100"))
        self.assertEqual(first[0].source_reference, "fundamental:filing-provider:acme-2026q1-fcf:0")
        later = materialize_fundamental_facts(investment_store, fundamentals, self.thesis.instrument_id, revision.ingested_at, fact_names=("free_cash_flow",))
        self.assertEqual(later[0].value, Decimal("80"))
        self.assertEqual(investment_store.facts_as_of(self.thesis.instrument_id, revision.ingested_at)[0].value, Decimal("80"))

    def test_authorized_materialization_is_idempotent_and_attributable(self) -> None:
        investments = SQLiteInvestmentStore(); fundamentals = SQLiteFundamentalStore(); now = datetime(2026, 4, 1, tzinfo=timezone.utc)
        fundamentals.ingest([FundamentalFact(self.thesis.instrument_id, StatementType.CASH_FLOW, "free_cash_flow", now - timedelta(days=90), now,
            now, now, now, Decimal("100"), "USD", "USD", "filing-provider", "acme-2026q1-fcf")])
        first = materialize_fundamental_facts_authorized(investments, fundamentals, self.thesis.instrument_id, now,
            fact_names=("free_cash_flow",), actor="investment-operator", idempotency_key="materialize-q1")
        replay = materialize_fundamental_facts_authorized(investments, fundamentals, self.thesis.instrument_id, now,
            fact_names=("free_cash_flow",), actor="investment-operator", idempotency_key="materialize-q1")
        self.assertEqual(replay, first)
        command = investments.materialization_command("materialize-q1")
        self.assertEqual((command[0], command[1], command[3]), ("investment-operator", self.thesis.instrument_id, (first[0].fact_id,)))
        with self.assertRaisesRegex(InvestmentValidationError, "materialization_idempotency_conflict"):
            materialize_fundamental_facts_authorized(investments, fundamentals, self.thesis.instrument_id, now,
                fact_names=("free_cash_flow",), actor="other-operator", idempotency_key="materialize-q1")
        with self.assertRaisesRegex(InvestmentValidationError, "materialization_actor_and_idempotency_required"):
            materialize_fundamental_facts_authorized(investments, fundamentals, self.thesis.instrument_id, now,
                fact_names=("free_cash_flow",), actor="", idempotency_key="missing-actor")
        class FailingFundamentals:
            def available_as_of(self, *args, **kwargs):
                raise RuntimeError("provider_read_failed")
        with self.assertRaisesRegex(RuntimeError, "provider_read_failed"):
            materialize_fundamental_facts_authorized(investments, FailingFundamentals(), self.thesis.instrument_id, now,
                fact_names=("free_cash_flow",), actor="investment-operator", idempotency_key="materialize-failed")
        failed = next(command for command in investments.materialization_commands(self.thesis.instrument_id) if command[0] == "materialize-failed")
        self.assertEqual((failed[0], failed[3], failed[5]), ("materialize-failed", "FAILED", "provider_read_failed"))

    def test_fundamental_analytics_are_source_bound_and_point_in_time(self) -> None:
        store = SQLiteInvestmentStore(); store.append_thesis(self.thesis); now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        values = {"revenue": [(Decimal("100"), now - timedelta(days=365)), (Decimal("120"), now)], "net_income": [(Decimal("12"), now)], "free_cash_flow": [(Decimal("18"), now)], "total_debt": [(Decimal("40"), now)], "cash_and_equivalents": [(Decimal("10"), now)], "total_equity": [(Decimal("80"), now)]}
        for metric, entries in values.items():
            for value, observed in entries:
                store.append_fact(SourceBackedFact(uuid4(), self.thesis.instrument_id, metric, value, "USD", observed, observed, observed, f"filing:{metric}:{observed.year}", "fundamental://fixture/q"))
        analysis = calculate_fundamental_analytics(store, self.thesis.thesis_id, now)
        self.assertEqual((analysis.revenue_growth, analysis.net_margin, analysis.free_cash_flow_margin, analysis.debt_to_equity, analysis.net_debt), (Decimal("0.2"), Decimal("0.1"), Decimal("0.15"), Decimal("0.5"), Decimal("30")))
        store.append_fundamental_analytics(analysis)

    def test_investment_holdings_require_separate_investment_policy(self) -> None:
        store = SQLiteInvestmentStore(); now = datetime(2026, 7, 1, tzinfo=timezone.utc)
        holding = InvestmentHolding('long-term-core', 'US:NYSE:SPY', Decimal('1000'), now, 'custody:daily:1')
        with self.assertRaisesRegex(InvestmentValidationError, 'investment_portfolio_policy_unavailable'): store.append_investment_holding(holding)
        store.set_investment_portfolio_policy(InvestmentPortfolioPolicy('long-term-core', 'USD', Decimal('10000'), Decimal('0.4'), 'investment-committee', now))
        store.append_investment_holding(holding)
        store.append_investment_holding(InvestmentHolding('long-term-core', 'US:NASDAQ:ACME', Decimal('500'), now, 'custody:daily:2'))
        later = now + timedelta(days=1); store.append_investment_holding(InvestmentHolding('long-term-core', 'US:NYSE:SPY', Decimal('100'), later, 'custody:daily:3'))
        assessment = store.assess_investment_portfolio('long-term-core', later)
        self.assertEqual(assessment.total_value, Decimal('600'))
        self.assertFalse(assessment.approved); self.assertIn('investment_single_weight_exceeded', assessment.reasons)

    def test_company_research_requires_matching_thesis_and_structured_cases(self) -> None:
        store = SQLiteInvestmentStore(); now = datetime(2026, 7, 1, tzinfo=timezone.utc)
        record = CompanyResearchRecord(uuid4(), self.thesis.thesis_id, self.thesis.instrument_id, now, 'Demand accelerates', 'Demand normalizes', 'Margins contract', ('Product launch',), ('Revenue misses twice',), 'Bounded by downside', ('US:NYSE:ALT',), ('filing:1',))
        with self.assertRaisesRegex(InvestmentValidationError, 'company_research_thesis_unavailable'): store.append_company_research(record)
        store.append_thesis(self.thesis); store.append_company_research(record)

    def test_theme_discovery_is_deterministic_and_bound_to_company_research_evidence(self) -> None:
        store = SQLiteInvestmentStore(); now = datetime(2026, 7, 1, tzinfo=timezone.utc); store.append_thesis(self.thesis)
        record = CompanyResearchRecord(uuid4(), self.thesis.thesis_id, self.thesis.instrument_id, now, 'AI infrastructure demand grows', 'AI demand normalizes', 'Margins fall', ('Data center buildout',), ('Demand stops',), 'bounded', (), ('filing:1',))
        store.append_company_research(record)
        rules = (ThemeDiscoveryRule('AI infrastructure', ('AI', 'data center'), 'themes-v1'),)
        first = discover_themes_from_company_research(store, self.thesis.thesis_id, rules, now); second = discover_themes_from_company_research(store, self.thesis.thesis_id, rules, now)
        history = store.theme_discoveries_for_thesis(self.thesis.thesis_id, now)
        self.assertEqual((first[0].matched_phrases, first[0].score, first[0].evidence_ids, second[0].discovery_id, len(history)), (('AI', 'data center'), Decimal('1'), ('filing:1',), first[0].discovery_id, 1))

    def test_themes_and_macro_sensitivities_require_source_evidence(self) -> None:
        store = SQLiteInvestmentStore(); now = datetime(2026, 7, 1, tzinfo=timezone.utc)
        store.append_theme_exposure(InvestmentThemeExposure('US:NASDAQ:ACME', 'AI infrastructure', Decimal('0.6'), now, 'research:theme:1'))
        store.append_macro_sensitivity(InvestmentMacroSensitivity('US:NASDAQ:ACME', 'US_CPI', Decimal('-0.2'), now, 'macro:release:1'))

    def test_rebalance_decision_is_evidence_bound_and_investment_only(self) -> None:
        store = SQLiteInvestmentStore(); now = datetime(2026, 7, 1, tzinfo=timezone.utc)
        decision = InvestmentRebalanceDecision(uuid4(), 'core', now, 'Reduce concentration', ('assessment:1',), {'US:NYSE:SPY': Decimal('0.5')}, 'investment-committee')
        with self.assertRaisesRegex(InvestmentValidationError, 'investment_portfolio_policy_unavailable'): store.append_rebalance_decision(decision)
        store.set_investment_portfolio_policy(InvestmentPortfolioPolicy('core', 'USD', Decimal('10000'), Decimal('0.6'), 'investment-committee', now))
        excessive = InvestmentRebalanceDecision(uuid4(), 'core', now, 'Increase concentration', ('assessment:2',), {'US:NYSE:SPY': Decimal('0.7')}, 'investment-committee')
        with self.assertRaisesRegex(InvestmentValidationError, 'investment_rebalance_single_weight_exceeded'): store.append_rebalance_decision(excessive)
        store.append_rebalance_decision(decision)

    def test_performance_snapshot_requires_investment_policy(self) -> None:
        store = SQLiteInvestmentStore(); now = datetime(2026, 7, 1, tzinfo=timezone.utc); snapshot = InvestmentPerformanceSnapshot('core', now, Decimal('1000'), Decimal('0.1'), 'custody:nav:1')
        with self.assertRaisesRegex(InvestmentValidationError, 'investment_portfolio_policy_unavailable'): store.append_performance_snapshot(snapshot)
        store.set_investment_portfolio_policy(InvestmentPortfolioPolicy('core', 'USD', Decimal('10000'), Decimal('0.6'), 'investment-committee', now)); store.append_performance_snapshot(snapshot)

    def test_due_review_schedule_persists_and_runs_deterministic_drift(self) -> None:
        store = SQLiteInvestmentStore(); now = datetime(2026, 7, 1, tzinfo=timezone.utc); store.append_thesis(self.thesis)
        schedule = InvestmentReviewSchedule(self.thesis.thesis_id, 30, (ThesisExpectation('free_cash_flow', Decimal('1'), None),), now, 'investment-committee')
        store.set_review_schedule(schedule)
        result = store.run_due_thesis_drift(now)
        self.assertEqual((len(result), result[0].drift_detected, store.due_review_schedules(now)), (1, True, ()))

    def test_due_drift_creates_deduplicated_operator_alert_without_execution(self) -> None:
        store = SQLiteInvestmentStore(); alerts = SQLiteOperationalAlertStore(); now = datetime(2026, 7, 1, tzinfo=timezone.utc); store.append_thesis(self.thesis)
        store.set_review_schedule(InvestmentReviewSchedule(self.thesis.thesis_id, 1, (ThesisExpectation('free_cash_flow', Decimal('1'), None),), now, 'investment-committee'))
        result = run_due_thesis_drift_with_alerts(store, alerts, now)
        alert = alerts.active()[0]
        self.assertEqual((result[0].drift_detected, alert.source, alert.code, alert.resource), (True, 'investment_review', 'THESIS_DRIFT_DETECTED', f'thesis:{self.thesis.thesis_id}'))


if __name__ == "__main__":
    unittest.main()
