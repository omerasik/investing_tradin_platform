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
    StrategyLabPageInfo,
    StrategyLabStudyDetailView,
    StrategyLabStudyPage,
    StrategyLabStudySummaryView,
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
        self.queries.strategy_lab_studies.assert_called_with(limit=20, offset=0)

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


if __name__ == "__main__":
    unittest.main()
