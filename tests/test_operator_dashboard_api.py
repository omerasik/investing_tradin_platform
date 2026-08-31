import unittest
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.operator_dashboard import (
    CovarianceEvidenceView,
    DependencyHealthView,
    FeatureDefinitionPage,
    FeatureDefinitionView,
    FeatureMaterializationPage,
    FeatureMaterializationView,
    IncidentView,
    NewsEventPage,
    NewsEventView,
    PageInfo,
    PortfolioConstructionView,
    RegimeDimensionView,
    RegimeProbabilityView,
    RegimeRunView,
    RiskDecisionPage,
    RiskDecisionView,
    SignalLifecycleEventView,
    SignalPage,
    SignalView,
    SloEvidenceView,
    SreOverviewView,
    StrategyScorecardView,
)
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator


class OperatorDashboardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        now = datetime(2026, 8, 18, tzinfo=UTC)
        identity = uuid4()
        self.queries = Mock()
        definition = FeatureDefinitionView(
            feature_definition_id=identity, feature_name="simple_return", family="PRICE_RETURNS",
            semantic_version="1.0.0", status="ACTIVE", required_dataset_types=["OHLCV"],
            required_fields=["close", "knowledge_at"], frequency="1d", timestamp_semantics="PIT",
            lookback=1, parameters={"horizon": 1}, missing_value_policy="reject", outlier_policy="reject",
            leakage_policy="reject_future_knowledge", units="decimal", calculation_version="transparent-v1",
            created_at=now, retired_at=None,
        )
        self.queries.feature_definitions.return_value = FeatureDefinitionPage(
            state="AVAILABLE", items=[definition], page=PageInfo(limit=50, offset=0, returned=1, has_more=False)
        )
        self.queries.feature_definition.return_value = definition
        self.queries.feature_materializations.return_value = FeatureMaterializationPage(
            state="AVAILABLE", decision_time=now,
            items=[FeatureMaterializationView(
                materialization_id=uuid4(), instrument="fixture:SPY", feature_definition_id=identity,
                feature_name="simple_return", semantic_version="1.0.0", dataset_version="fixture-v1",
                event_time=now, effective_time=now, knowledge_time=now, computed_time=now,
                value="0.01", quality_state="VALIDATED", content_hash="a" * 64,
                source_manifest=["raw:bar-1"],
            )], page=PageInfo(limit=50, offset=0, returned=1, has_more=False),
        )
        self.queries.signals.return_value = SignalPage(
            state="AVAILABLE", as_of=now,
            items=[SignalView(
                signal_id=identity, instrument="fixture:SPY", strategy_version="trend-v1",
                direction="BUY", status="VALIDATED", expiry_state="CURRENT",
                created_at=now, expires_at=now, strength="0.8", confidence="0.7",
                data_quality_score="1", explanation="Fixture signal",
                contradicting_evidence=["fixture risk"], validation_id=uuid4(),
                passed_stages=["data", "risk"], failed_stages=[], latest_reason="all_validation_stages_passed",
                lifecycle=[SignalLifecycleEventView(
                    event_id=uuid4(), from_status="CANDIDATE", to_status="VALIDATED",
                    actor="signal_validation", reason="all_validation_stages_passed",
                    evidence_references=["signal-validation:fixture"], occurred_at=now,
                )], research_or_paper_only=True, automatic_authority=False,
            )], page=PageInfo(limit=50, offset=0, returned=1, has_more=False),
        )
        self.queries.risk_decisions.return_value = RiskDecisionPage(
            state="AVAILABLE",
            items=[RiskDecisionView(
                risk_decision_id=identity, intent_id=uuid4(), policy_version_id=uuid4(),
                policy_name="paper-risk", policy_version="v1", policy_content_hash="r" * 64,
                policy_limits={"max_notional": "1000"}, approved=False,
                reasons=["daily_notional_limit"], decided_at=now, reservation_id=None,
                account_id=None, business_date=None, reserved_notional=None,
                reservation_created_at=None, research_or_paper_only=True, automatic_authority=False,
            )], page=PageInfo(limit=50, offset=0, returned=1, has_more=False),
        )
        self.queries.strategy_scorecard.return_value = StrategyScorecardView(
            scorecard_id=identity, schema_version="scorecard-v2", strategy_id=uuid4(), strategy_version="trend-v1",
            research_run_id=uuid4(), dataset_version="fixture-v1", feature_versions=["return:1"],
            cost_model_version="cost-v1", evaluated_at=now, knowledge_cutoff=now, status="REVIEW_REQUIRED",
            limitations=["Synthetic fixture"], dataset_health_status="HEALTHY", validation_package_id=None,
            validation_package_content_hash=None, evidence_classification="SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",
            evidence_manifest_references=["run:hash"], content_hash="b" * 64, groups=[], complexity_components=[],
        )
        self.queries.regime_run.return_value = RegimeRunView(
            regime_assessment_id=identity, model_version_id=uuid4(), model_version="v1", rule_version="rule-v1",
            dataset_version="fixture-v1", instrument="fixture:SPY", as_of_timestamp=now, knowledge_timestamp=now,
            status="REVIEW_REQUIRED", limitations=["fixture"], evidence_hash="c" * 64,
            dimensions=[RegimeDimensionView(
                observation_id=uuid4(), event_time=now, method="RULE_BASED", dimension="TREND",
                evidence_state="MEASURED", hard_label="UP", probabilities=[RegimeProbabilityView(state="UP", probability="0.8")],
                uncertainty="0.2", input_materialization_ids=[], content_hash="d" * 64,
            )], risk_effects=[],
            risk_boundary="REGIME MAY REDUCE OR BLOCK RISK; REGIME CANNOT INCREASE GLOBAL RISK LIMITS",
        )
        self.queries.portfolio_construction.return_value = PortfolioConstructionView(
            portfolio_construction_run_id=identity, policy_version_id=uuid4(), policy_version="v1",
            regime_run_id=uuid4(), constructed_at=now, status="REVIEW_REQUIRED", review_only=True,
            automatic_authority=False, equity="100000", target_volatility="0.1", cash_weight="0.2",
            gross_weight="0.8", net_weight="0.8", portfolio_volatility="0.09", stressed_volatility="0.11",
            risk_gate_approved=True, risk_gate_reasons=[], limitations=["fixture"], content_hash="e" * 64,
            covariance=CovarianceEvidenceView(
                covariance_id=uuid4(), dataset_version="fixture-v1", dataset_content_hash="f" * 64,
                estimation_version="sample-v1", observations=252, as_of=now, uncertainty="0.2",
                correlation_stress="0.3", source_provider="fixture", source_terms_version="terms-v1", provider_backed=False,
                classification="NO_REAL_PROVIDER_BACKED_COVARIANCE_EVIDENCE",
            ), sleeves=[], constraints=[],
        )
        self.queries.news_events.return_value = NewsEventPage(
            state="EXTERNAL_BLOCKED", provider_state="EXTERNAL_BLOCKED",
            items=[NewsEventView(
                event_id=identity, document_revision_id=uuid4(), source="fixture-news", source_version="v1",
                source_terms_version="terms-v1", published_at=now, source_updated_at=now, ingested_at=now,
                correction_or_retraction_at=now, revision=1, revision_kind="RETRACTION", headline="Fixture retracted",
                category="EARNINGS", novelty="0.5", credibility="0.4", uncertainty="0.6", urgency="0.5",
                horizon="DAYS", assessment_status="WITHDRAWN", rights_state="NOT_APPROVED",
                authorization_state="NOT_AUTHORIZED", provider_activated=False, content_fingerprint="1" * 64,
                provenance_reference="fixture://news/1", limitations=["fixture"], entities=[], correction_chain=[],
            )], page=PageInfo(limit=50, offset=0, returned=1, has_more=False),
        )
        self.queries.sre_overview.return_value = SreOverviewView(
            state="BLOCKED", service_version_id=identity, subsystem="api", version="v1", environment="CI",
            deployment_status="EVIDENCE_ONLY", postgres_state="HEALTHY", provider_state="UNAVAILABLE",
            ingestion_checkpoint_freshness="STALE", dataset_freshness="STALE", feature_freshness="STALE",
            research_job_health="HEALTHY", signal_freshness="UNAVAILABLE", risk_status="HEALTHY",
            reconciliation_status="HEALTHY", backup_restore_status="PASSED", kill_switch_state="HEALTHY",
            dependencies=[DependencyHealthView(dependency="postgresql", status="HEALTHY", checked_at=now, latency_ms="1", reason=None)],
            slos=[SloEvidenceView(
                slo_policy_version_id=uuid4(), name="availability", indicator="good/total", target="0.99",
                target_state="TARGET", window_seconds=3600, measured_value="0.98", measured_state="MEASURED",
                window_start=now, window_end=now, claim_status="ENGINEERING_EVIDENCE_ONLY",
            )], incidents=[IncidentView(
                incident_id=uuid4(), severity="CRITICAL", subsystem="POSTGRES_DOWN", opened_at=now,
                acknowledged_at=now, resolved_at=None, status="DECLARED", reason="database unavailable",
                evidence_reference="runbook://postgres",
            )], failure_drills=[],
        )
        self.queries.command_summaries.return_value = []
        self.client = TestClient(build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"),
            InMemoryRateLimiter(max_requests=100), operator_dashboard_queries=self.queries,
        ))
        self.headers = {"Authorization": "Bearer test-token"}

    def test_all_authority_reads_are_protected_typed_and_get_only(self) -> None:
        now = "2026-08-18T00:00:00Z"
        identity = self.queries.feature_definition.return_value.feature_definition_id
        paths = [
            "/operator-dashboard/feature-definitions",
            f"/operator-dashboard/feature-definitions/{identity}",
            f"/operator-dashboard/feature-materializations?feature_id={identity}&instrument=fixture%3ASPY&dataset_version=fixture-v1&decision_time={now}",
            f"/operator-dashboard/signals?as_of={now}&limit=20",
            "/operator-dashboard/risk-decisions?limit=20&offset=0",
            f"/operator-dashboard/strategy-scorecards/{identity}",
            f"/operator-dashboard/regime-runs/{identity}",
            f"/operator-dashboard/portfolio-construction-runs/{identity}",
            "/operator-dashboard/news-events?limit=20",
            "/operator-dashboard/sre-overview",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
                self.assertEqual(self.client.get(path, headers={"Authorization": "Bearer invalid"}).status_code, 401)
                response = self.client.get(path, headers=self.headers)
                self.assertEqual(response.status_code, 200, response.text)
                serialized = response.text.casefold()
                self.assertNotIn("postgresql://", serialized)
                self.assertNotIn("password", serialized)
                self.assertNotIn("test-token", serialized)
                self.assertEqual(self.client.post(path.split("?")[0], headers=self.headers).status_code, 405)

    def test_query_validation_fails_closed_without_calling_authority(self) -> None:
        identity = self.queries.feature_definition.return_value.feature_definition_id
        naive = self.client.get(
            f"/operator-dashboard/feature-materializations?feature_id={identity}&instrument=fixture%3ASPY&dataset_version=v1&decision_time=2026-01-01T00%3A00%3A00",
            headers=self.headers,
        )
        oversized = self.client.get("/operator-dashboard/feature-definitions?limit=101", headers=self.headers)
        inverted = self.client.get(
            "/operator-dashboard/news-events?start=2026-08-19T00%3A00%3A00Z&end=2026-08-18T00%3A00%3A00Z",
            headers=self.headers,
        )
        invalid_id = self.client.get("/operator-dashboard/regime-runs/not-a-uuid", headers=self.headers)
        invalid_signal = self.client.get("/operator-dashboard/signals?as_of=2026-01-01T00%3A00%3A00&status=UNKNOWN", headers=self.headers)
        self.assertEqual((naive.status_code, oversized.status_code, inverted.status_code, invalid_id.status_code, invalid_signal.status_code), (422, 422, 422, 422, 422))


if __name__ == "__main__":
    unittest.main()
