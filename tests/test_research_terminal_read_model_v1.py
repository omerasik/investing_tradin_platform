"""UI-2 -- research terminal reads are protected, GET-only and cannot express a higher claim."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.research_terminal_read_model_v1 import (
    AUTHORITY_LEGEND_V1,
    AccountView,
    CandidateStateView,
    CycleView,
    IncubationView,
    LiveSignalView,
    OwnerGateView,
    RerunView,
    TerminalOverview,
    ValidationView,
)
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator

NOW = datetime(2026, 9, 3, tzinfo=UTC)
CYCLE = CycleView(cycle_id="cycle-2026-08-20", holdout_start=datetime(2026, 8, 20, tzinfo=UTC),
                  holdout_state="UNOPENED", holdout_end_exclusive=None, opened_at=None, preregistrations={},
                  validated=False)
PATHS = ("overview", "reruns?limit=10", "validation?limit=10", "signals?limit=10", "accounts", "incubation")


class ResearchTerminalApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queries = Mock()
        self.queries.terminal_overview.return_value = TerminalOverview(
            generated_at=NOW, studies=3, finished_studies=3, reruns={"ESTABLISHED": 3}, cycle=CYCLE,
            candidate_states={}, signals={}, latest_signal_at=None, incubation_fills={}, accounts={},
            owner_gates=[OwnerGateView(gate="OR-7", topic="t", status="OPEN", evidence="holdout unopened")],
            authority_legend=dict(AUTHORITY_LEGEND_V1))
        self.queries.terminal_reruns.return_value = [RerunView(
            rerun_hash="a" * 64, study_id=uuid4(), study_label="l", strategy_family="trend_ma_cross",
            candidate_set_hash="b" * 64, selection_status="ESTABLISHED", claim="DECIMAL_SELECTION_ESTABLISHED",
            rerun_count=10, metric="sharpe_daily_annualized", selected=[], recorded_at=NOW)]
        self.queries.terminal_validation.return_value = ValidationView(cycle=CYCLE, candidates=[])
        self.queries.terminal_signals.return_value = [LiveSignalView(
            signal_id=uuid4(), symbol="SOLUSDT", authority="RESEARCH_WATCH", claim="NOT_VALIDATED_RESEARCH_WATCH",
            target_from=0, target_to=1, bar_open_at=NOW, decided_at=NOW, study_id=uuid4(), trial_id=uuid4(),
            explanation={})]
        self.queries.terminal_accounts.return_value = [AccountView(
            account_id="personal", kind="PERSONAL", display_name="Personal", base_currency="USDT",
            policy_status="UNCONFIGURED", policy_version_id=uuid4(), unresolved=["MISSING_OWNER_CAPITAL_OR_11"],
            recorded_at=NOW)]
        self.queries.terminal_incubation.return_value = IncubationView(state="NO_FILLS", report={"state": "INCUBATING"})
        self.client = TestClient(build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"),
            InMemoryRateLimiter(max_requests=100), operator_dashboard_queries=self.queries,
        ))
        self.headers = {"Authorization": "Bearer test-token"}

    def test_every_terminal_read_is_protected_and_get_only(self) -> None:
        for path in PATHS:
            url = f"/operator-dashboard/research-terminal/{path}"
            with self.subTest(path=path):
                self.assertEqual(self.client.get(url).status_code, 401)
                response = self.client.get(url, headers=self.headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn("test-token", response.text)
                self.assertEqual(self.client.post(url.split("?")[0], headers=self.headers).status_code, 405)
        self.queries.terminal_reruns.assert_called_with(limit=10)
        self.assertEqual(self.client.get("/operator-dashboard/research-terminal/signals?limit=501",
                                         headers=self.headers).status_code, 422)

    def test_the_views_cannot_express_a_higher_claim(self) -> None:
        with self.assertRaises(ValueError):
            CandidateStateView(study_id=uuid4(), study_label="l", trial_id=uuid4(), state="VALIDATED",
                               label="VALIDATED", reasons=[], recorded_at=NOW)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            RerunView(rerun_hash="a" * 64, study_id=uuid4(), study_label="l", strategy_family="f",
                      candidate_set_hash="b" * 64, selection_status="ESTABLISHED", claim="AUTHORITATIVE",  # type: ignore[arg-type]
                      economics="NET_PROMOTABLE", rerun_count=1, metric="m", selected=[],  # type: ignore[arg-type]
                      recorded_at=NOW)
        with self.assertRaises(ValueError):
            ValidationView(cycle=CYCLE, candidates=[], validated_claim="VALIDATED")  # type: ignore[arg-type]
        self.assertNotIn("VALIDATED", [key for key in AUTHORITY_LEGEND_V1 if not key.startswith("NOT_")])


if __name__ == "__main__":
    unittest.main()
