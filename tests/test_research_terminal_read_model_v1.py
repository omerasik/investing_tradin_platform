"""UI-2 -- research terminal reads are protected, GET-only and cannot express a higher claim."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.research_terminal_read_model_v1 import (
    AUTHORITY_LEGEND_V1,
    AccountView,
    CandidateLifecycleView,
    CandidateLinksView,
    CandidateReadinessSectionView,
    CandidateStateView,
    CycleView,
    ExitCriteriaSectionView,
    IncubationView,
    LiveSignalView,
    OwnerGateView,
    RerunView,
    TerminalOverview,
    ValidationView,
    WalkForwardView,
)
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator

NOW = datetime(2026, 9, 3, tzinfo=UTC)
CYCLE = CycleView(cycle_id="cycle-2026-08-20", holdout_start=datetime(2026, 8, 20, tzinfo=UTC),
                  holdout_state="UNOPENED", holdout_end_exclusive=None, opened_at=None, preregistrations={},
                  validated=False)
def _walk(study: UUID) -> WalkForwardView:
    return WalkForwardView(
        report_hash="c" * 64, study_id=study, study_label="l", study_content_hash="d" * 64,
        dataset_content_hash="e" * 64, split_hash="f" * 64, evaluation_class="WALK_FORWARD_DEVELOPMENT",
        numeric_tier="SEARCH_NON_AUTHORITATIVE", cost_mode="GROSS_NON_PROMOTABLE", rule={}, multiple_testing_trial_count=4,
        evaluation_end_exclusive=datetime(2026, 8, 1, tzinfo=UTC), folds=[], authority_reasons=["SEARCH_TIER"],
        recorded_at=NOW)


def _lifecycle(study: UUID, trial: UUID) -> CandidateLifecycleView:
    return CandidateLifecycleView(
        links=CandidateLinksView(study_id=study, study_label="l", trial_id=trial, cycle_id="cycle-2026-08-20",
                                 rerun_hash="a" * 64, walk_forward=[], preregistration_hash="b" * 64,
                                 preregistration_status="DRAFT", holdout_opened_for_this_preregistration=False,
                                 validation_hash=None, criteria_hash=None),
        claim="DECIMAL_SELECTION_ESTABLISHED", economics="GROSS_NON_PROMOTABLE",
        readiness=CandidateReadinessSectionView(stage="DECIMAL_SELECTION_ESTABLISHED", canonical_state=None,
                                                blockers=["HOLDOUT_NOT_OPENED_FOR_THIS_PREREGISTRATION"],
                                                owner_gates=["OR-7"], evidence={}, assessment_hash="0" * 64),
        exit_criteria=ExitCriteriaSectionView(status="DRAFT", criteria_hash="9" * 64, recorded_at=NOW,
                                              authorized_by=None, authorized_on=None,
                                              unresolved=["MISSING_OWNER_AUTHORIZATION"], thresholds={},
                                              stage="EXIT_CRITERIA_UNRESOLVED", evidence_inputs={}, blockers=[],
                                              refusals=[], owner_gates=["OR-7"]),
        evidence_not_loaded=["RUNNER_LIVENESS_NOT_DERIVED_BY_THIS_READ"], as_of=NOW)


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

    def test_walk_forward_and_candidate_routes_are_protected_bounded_and_404_when_absent(self) -> None:
        from trade_platform.operator_dashboard import DashboardObjectNotFound

        study, trial = uuid4(), uuid4()
        self.queries.terminal_walk_forward.return_value = [_walk(study)]
        self.queries.terminal_walk_forward_report.return_value = _walk(study)
        self.queries.terminal_candidates.return_value = [_lifecycle(study, trial)]
        self.queries.terminal_candidate.return_value = _lifecycle(study, trial)
        base = "/operator-dashboard/research-terminal"
        for path in (f"walk-forward?limit=5&study_id={study}", f"walk-forward/{'c' * 64}", "candidates?limit=5",
                     f"candidates/{study}/{trial}"):
            with self.subTest(path=path):
                self.assertEqual(401, self.client.get(f"{base}/{path}").status_code)
                response = self.client.get(f"{base}/{path}", headers=self.headers)
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(405, self.client.post(f"{base}/{path.split('?')[0]}", headers=self.headers).status_code)
        self.queries.terminal_walk_forward.assert_called_with(limit=5, study_id=study)
        self.queries.terminal_candidate.assert_called_with(study, trial)
        body = self.client.get(f"{base}/candidates/{study}/{trial}", headers=self.headers).json()
        self.assertEqual(("NONE_VALIDATED", "DRAFT", "NEVER_ACTIVATES_AN_ACCOUNT"),
                         (body["validated_claim"], body["exit_criteria"]["status"],
                          body["exit_criteria"]["account_activation"]))
        for bad in ("walk-forward/NOTAHASH", "walk-forward?limit=201", "candidates?limit=101", "candidates/x/y"):
            self.assertEqual(422, self.client.get(f"{base}/{bad}", headers=self.headers).status_code, bad)
        self.queries.terminal_candidate.side_effect = DashboardObjectNotFound("strategy_lab_study_not_found")
        self.assertEqual(404, self.client.get(f"{base}/candidates/{study}/{trial}", headers=self.headers).status_code)

    def test_lifecycle_and_walk_forward_views_cannot_express_a_higher_claim(self) -> None:
        study, trial = uuid4(), uuid4()
        good = _lifecycle(study, trial).model_dump()
        for field, value in (("claim", "VALIDATED"), ("validated_claim", "VALIDATED"),
                             ("economics", "NET_PROMOTABLE")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                CandidateLifecycleView(**{**good, field: value})
        with self.assertRaises(ValueError):
            CandidateLifecycleView(**{**good, "exit_criteria": {**good["exit_criteria"], "status": "APPROVED"}})
        with self.assertRaises(ValueError):
            CandidateLifecycleView(**{**good, "readiness": {**good["readiness"], "promotion": "PROMOTED"}})
        walk = _walk(study).model_dump()
        for field, value in (("promotable", True), ("cost_mode", "NET"), ("numeric_tier", "DECIMAL_AUTHORITATIVE")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                WalkForwardView(**{**walk, field: value})

    def test_the_cycle_id_is_r6s(self) -> None:
        from trade_platform.research_terminal_read_model_v1 import current_cycle_id_v1
        from trade_platform.strategy_lab_validation_v1 import CURRENT_CYCLE_V1

        self.assertEqual(CURRENT_CYCLE_V1.cycle_id, current_cycle_id_v1())


class RuntimeImageImportTests(unittest.TestCase):
    def test_the_api_imports_without_numerical_libraries(self) -> None:
        # The runtime image installs requirements-runtime.txt only: no numpy, polars or duckdb.
        code = ("import sys\n"
                "for name in ('numpy', 'polars', 'duckdb', 'pyarrow'): sys.modules[name] = None\n"
                "import trade_platform.api, trade_platform.research_terminal_read_model_v1\n")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False,
                                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
        self.assertEqual(0, result.returncode, result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
