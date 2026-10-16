from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.operator_dashboard import DashboardObjectNotFound
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator
from trade_platform.strategy_lab_read_model_v1 import (
    StrategyLabAuthorityView,
    StrategyLabEconomicsView,
    StrategyLabPageInfo,
    StrategyLabStudyDetailView,
    StrategyLabStudyPage,
    StrategyLabStudySummaryView,
    StrategyLabTrialPage,
    economics_v1,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _summary() -> StrategyLabStudySummaryView:
    return StrategyLabStudySummaryView(
        study_id=uuid4(), content_hash="a" * 64, strategy_family="sma_cross", strategy_version="1.0.0",
        label="fixture", planned_trial_count=18, evaluation_upper_bound_exclusive=NOW, registered_at=NOW,
        queue_states={"SUCCEEDED": 18}, result_count=18,
        authority=StrategyLabAuthorityView(reasons=["NUMERIC_POLICY_UNSET_PENDING_OR_3"]),
    )


class StrategyLabReadApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queries = Mock()
        summary = _summary()
        self.study_id = summary.study_id
        self.queries.strategy_lab_studies.return_value = StrategyLabStudyPage(
            state="AVAILABLE", items=[summary],
            page=StrategyLabPageInfo(limit=50, offset=0, returned=1, has_more=False),
        )
        self.queries.strategy_lab_study.return_value = StrategyLabStudyDetailView(
            summary=summary, strategy={"family": "sma_cross"}, parameter_space=[], datasets=[], search={},
            numeric_policy_slot="UNSET_PENDING_OWNER_DECISION_OR_3",
            cost_policy_slot="UNSET_PENDING_OWNER_DECISION_OR_6", manifests=[], candidate_sets=[],
        )
        self.client = TestClient(build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"),
            InMemoryRateLimiter(max_requests=100), operator_dashboard_queries=self.queries,
        ))
        self.headers = {"Authorization": "Bearer test-token"}

    def test_reads_are_protected_typed_get_only_and_never_authoritative(self) -> None:
        for path in ("/operator-dashboard/strategy-lab/studies?limit=20",
                     f"/operator-dashboard/strategy-lab/studies/{self.study_id}"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
                response = self.client.get(path, headers=self.headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn('"authority_status":"NON_AUTHORITATIVE"', response.text.replace(" ", ""))
                self.assertIn('"promotable":false', response.text.replace(" ", ""))
                self.assertNotIn("test-token", response.text)
                self.assertEqual(self.client.post(path.split("?")[0], headers=self.headers).status_code, 405)
        self.queries.strategy_lab_studies.assert_called_with(
            limit=20, offset=0, strategy_family=None, instrument=None, windows=None)

    def test_explorer_reads_are_protected_get_only_and_validated(self) -> None:
        trial_id = uuid4()
        self.queries.strategy_lab_trials.return_value = StrategyLabTrialPage(
            state="UNAVAILABLE", study_id=self.study_id, sort="trades", direction="desc", outcome="EVALUATED",
            frozen_only=True, metric_keys=["trades"], items=[], total=0,
            page=StrategyLabPageInfo(limit=10, offset=20, returned=0, has_more=False),
        )
        trials = f"/operator-dashboard/strategy-lab/studies/{self.study_id}/trials"
        query = "?limit=10&offset=20&sort=trades&direction=desc&outcome=EVALUATED&frozen_only=true"
        self.assertEqual(self.client.get(trials + query).status_code, 401)
        response = self.client.get(trials + query, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["numeric_tier"], "SEARCH_NON_AUTHORITATIVE")
        self.assertEqual(response.json()["economics"], "GROSS_NON_PROMOTABLE")
        self.queries.strategy_lab_trials.assert_called_with(
            self.study_id, limit=10, offset=20, sort="trades", direction="desc", outcome="EVALUATED", frozen_only=True)
        for bad in ("?sort=Trades", "?sort=x;drop", "?direction=up", "?outcome=VALIDATED", "?limit=201", "?offset=-1"):
            with self.subTest(bad=bad):
                self.assertEqual(self.client.get(trials + bad, headers=self.headers).status_code, 422)
        for path in (trials, f"{trials}/{trial_id}"):
            for method in ("post", "put", "patch", "delete"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(getattr(self.client, method)(path, headers=self.headers).status_code, 405)
        self.queries.strategy_lab_trial.side_effect = DashboardObjectNotFound("strategy_lab_trial_not_found")
        self.assertEqual(self.client.get(f"{trials}/{trial_id}", headers=self.headers).status_code, 404)

    def test_study_filters_are_validated_and_instrument_needs_the_catalogue(self) -> None:
        studies = "/operator-dashboard/strategy-lab/studies"
        response = self.client.get(studies + "?strategy_family=trend_ma_cross", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.queries.strategy_lab_studies.assert_called_with(
            limit=50, offset=0, strategy_family="trend_ma_cross", instrument=None, windows=None)
        for bad in ("?strategy_family=Trend", "?instrument=eth", "?instrument=ETH-USDT"):
            with self.subTest(bad=bad):
                self.assertEqual(self.client.get(studies + bad, headers=self.headers).status_code, 422)
        # No research window catalogue in this process: an instrument cannot be resolved -> fail closed.
        self.assertEqual(self.client.get(studies + "?instrument=ETHUSDT", headers=self.headers).status_code, 503)

    def test_invalid_input_fails_closed_and_missing_study_is_404(self) -> None:
        self.assertEqual(self.client.get("/operator-dashboard/strategy-lab/studies/not-a-uuid",
                                         headers=self.headers).status_code, 422)
        self.assertEqual(self.client.get("/operator-dashboard/strategy-lab/studies?limit=101",
                                         headers=self.headers).status_code, 422)
        self.queries.strategy_lab_study.side_effect = DashboardObjectNotFound("strategy_lab_study_not_found")
        self.assertEqual(self.client.get(f"/operator-dashboard/strategy-lab/studies/{uuid4()}",
                                         headers=self.headers).status_code, 404)

    def test_the_view_types_cannot_express_authority(self) -> None:
        with self.assertRaises(ValueError):
            StrategyLabAuthorityView(authority_status="AUTHORITATIVE", reasons=[])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            StrategyLabAuthorityView(promotable=True, reasons=[])  # type: ignore[arg-type]


class StrategyLabEconomicsTests(unittest.TestCase):
    def test_verified_costs_and_net_are_never_available(self) -> None:
        gross = economics_v1({"cost_policy_slot": "or6-cost-policy-v1:" + "a" * 64, "policies": {"cost": {
            "mode": "GROSS_NON_PROMOTABLE", "venue_fees": None, "slippage_scenarios": [],
            "funding": "NOT_MODELLED_REPORT_EXPOSURE_FAIL_CLOSED_FOR_COST_COMPLETE_PROMOTION",
            "gross_search_reports": "BREAK_EVEN_COST_BPS_PER_SIDE"}}})
        self.assertEqual((gross.verified_costs, gross.net_profitability), ("UNAVAILABLE", "UNAVAILABLE"))
        self.assertEqual(gross.hypothetical_costs, "NONE_BOUND")
        self.assertEqual(gross.break_even_metric, "break_even_bps_per_side")
        self.assertEqual(gross.net_profitability_reasons, [
            "NO_VERIFIED_FEE_SCHEDULE_OR_6", "COST_MODE_GROSS_NON_PROMOTABLE",
            "NO_APPROVED_SLIPPAGE_SCENARIO_OR_6", "FUNDING_NOT_MODELLED"])
        legacy = economics_v1({"cost_policy_slot": "UNSET_PENDING_OWNER_DECISION_OR_6"})
        self.assertEqual(legacy.verified_cost_reasons, ["COST_POLICY_UNSET_OR_6"])
        # A fee number inside an identity is still not a verification reference.
        fees = economics_v1({"policies": {"cost": {"mode": "X", "venue_fees": {"taker_bps": "5.5"},
                                                   "slippage_scenarios": [{"name": "s1", "bps_per_side": "1"}]}}})
        self.assertEqual(fees.verified_costs, "UNAVAILABLE")
        self.assertEqual(fees.verified_cost_reasons, ["VENUE_FEES_NOT_VERIFIED_BY_THIS_VIEW"])
        self.assertEqual(fees.hypothetical_costs, "BOUND_TO_STUDY")
        for field in ("verified_costs", "net_profitability"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                StrategyLabEconomicsView(**{**gross.model_dump(), field: "AVAILABLE"})


if __name__ == "__main__":
    unittest.main()
