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
    EVIDENCE_STAGES_V1,
    AccountView,
    CandidateLifecycleView,
    CandidateLinksView,
    CandidatePaperEvidenceView,
    CandidateReadinessSectionView,
    CandidateStateView,
    ComparisonView,
    CycleView,
    ExitCriteriaSectionView,
    FundingEvidenceView,
    IncubationView,
    LiveSignalView,
    OwnerGateView,
    ParityEvidenceView,
    RerunView,
    TerminalOverview,
    ValidationView,
    WalkForwardView,
    funding_evidence_view_v1,
    parity_evidence_view_v1,
)
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

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


def _parity(live: dict[str, object] | None = None) -> ParityEvidenceView:
    return parity_evidence_view_v1(None, trial_id="t", live_side=live or {})


def _funding_section(study: str, trial: str) -> dict[str, object]:
    from trade_platform.paper_funding_evidence_v1 import funding_evidence_section_v1

    return funding_evidence_section_v1(None, study_id=study, trial_id=trial, symbol="ETHUSDT", holdings=[],
                                       fills_skipped=0, as_of_micros=1, binding_validation_hash=None,
                                       fee_schedule_verified=False)


def _paper(study: UUID, trial: UUID) -> CandidatePaperEvidenceView:
    return CandidatePaperEvidenceView(
        study_id=study, study_content_hash="d" * 64, trial_id=trial, trial_content_hash="e" * 64,
        cycle_id="cycle-2026-08-20", symbol=None, incubation=None, parity=_parity(),
        funding=funding_evidence_view_v1(None, bridges_loaded=False, not_loaded_reasons=["X"]), as_of=NOW)


def _comparison(study: UUID, trial: UUID) -> ComparisonView:
    return ComparisonView(claim="RESEARCH_COMPARISON_NON_AUTHORITATIVE", composite_score="NONE", holdout="NOT_READ",
                          evidence_stages=list(EVIDENCE_STAGES_V1), cycle=CYCLE,
                          comparison={"rows": [], "sort": None}, paper_evidence=[_paper(study, trial)], as_of=NOW)


class _Report:
    """The public shape of ``FillParityReportV1`` (identity, content_hash) for a given status."""

    def __init__(self, status: str, trial: str, reasons: list[str]) -> None:
        self.identity = {"inputs": {"candidates": [{"trial_id": trial}]}, "summary": {"outcomes": {"MATCH": 1}},
                         "assessment": {"status": status, "reasons": reasons}}
        self.content_hash = identity_hash_v1(self.identity)


class CompareAndPaperEvidenceApiTests(unittest.TestCase):
    base = "/operator-dashboard/research-terminal"

    def setUp(self) -> None:
        self.queries = Mock()
        self.study, self.trial = uuid4(), uuid4()
        self.queries.terminal_comparison.return_value = _comparison(self.study, self.trial)
        self.queries.terminal_candidate_paper_evidence.return_value = _paper(self.study, self.trial)
        self.client = TestClient(build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"),
            InMemoryRateLimiter(max_requests=200), operator_dashboard_queries=self.queries,
        ))
        self.headers = {"Authorization": "Bearer test-token"}

    def test_both_routes_are_protected_get_only_and_restate_their_claims(self) -> None:
        for path in (f"compare?study_id={self.study}", f"candidates/{self.study}/{self.trial}/paper-evidence"):
            with self.subTest(path=path):
                url = f"{self.base}/{path}"
                self.assertEqual(401, self.client.get(url).status_code)
                response = self.client.get(url, headers=self.headers)
                self.assertEqual(200, response.status_code, response.text)
                self.assertNotIn("test-token", response.text)
                for method in (self.client.post, self.client.put, self.client.delete):
                    self.assertEqual(405, method(url.split("?")[0], headers=self.headers).status_code)
        body = self.client.get(f"{self.base}/compare?study_id={self.study}", headers=self.headers).json()
        self.assertEqual(("NONE", "NOT_READ", "NONE_VALIDATED"),
                         (body["composite_score"], body["holdout"], body["validated_claim"]))
        self.assertEqual(["SEARCH", "DECIMAL_RERUN", "WALK_FORWARD", "VALIDATION"],
                         [stage["stage"] for stage in body["evidence_stages"]])
        paper = body["paper_evidence"][0]
        self.assertEqual(("NOT_LOADED", False, "NOT_LOADED", False, "NON_COSTABLE"),
                         (paper["parity"]["status"], paper["parity"]["loaded"], paper["funding"]["status"],
                          paper["funding"]["costable"], paper["funding"]["cost_status"]))
        self.queries.terminal_candidate_paper_evidence.assert_called_with(self.study, self.trial)

    def test_no_default_sort_and_a_sort_is_explicit_and_complete(self) -> None:
        from trade_platform.research_candidate_comparison_v1 import (
            ComparisonSortV1,
            SortDimensionV1,
        )

        other = uuid4()
        self.client.get(f"{self.base}/compare?study_id={self.study}&study_id={other}", headers=self.headers)
        self.queries.terminal_comparison.assert_called_with(study_ids=[self.study, other], sort=None)
        response = self.client.get(f"{self.base}/compare?study_id={self.study}&sort_dimension=DECIMAL_BASELINE"
                                   "&sort_metric=total_return&sort_direction=DESC", headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        self.queries.terminal_comparison.assert_called_with(
            study_ids=[self.study], sort=ComparisonSortV1(SortDimensionV1.DECIMAL_BASELINE, "total_return", "DESC"))
        many = "&".join(f"study_id={uuid4()}" for _ in range(21))
        for bad in ("", f"study_id={self.study}&study_id={self.study}", many, "study_id=x",
                    f"study_id={self.study}&sort_metric=total_return",
                    f"study_id={self.study}&sort_dimension=SEARCH&sort_metric=total_return",
                    f"study_id={self.study}&sort_dimension=COMPOSITE&sort_metric=m&sort_direction=DESC",
                    f"study_id={self.study}&sort_dimension=SEARCH&sort_metric=Bad-Metric&sort_direction=DESC",
                    f"study_id={self.study}&sort_dimension=SEARCH&sort_metric=m&sort_direction=BEST"):
            with self.subTest(query=bad):
                self.assertEqual(422, self.client.get(f"{self.base}/compare?{bad}", headers=self.headers).status_code)
        for bad in (f"candidates/{self.study}/x/paper-evidence", f"candidates/x/{self.trial}/paper-evidence"):
            self.assertEqual(422, self.client.get(f"{self.base}/{bad}", headers=self.headers).status_code)

    def test_absent_evidence_is_404_and_unverifiable_evidence_is_503(self) -> None:
        from trade_platform.operator_dashboard import DashboardObjectNotFound, DashboardQueryError

        self.queries.terminal_comparison.side_effect = DashboardObjectNotFound("strategy_lab_study_not_found")
        self.queries.terminal_candidate_paper_evidence.side_effect = DashboardQueryError("operator_dashboard_query_failed")
        self.assertEqual(404, self.client.get(f"{self.base}/compare?study_id={self.study}",
                                              headers=self.headers).status_code)
        self.assertEqual(503, self.client.get(f"{self.base}/candidates/{self.study}/{self.trial}/paper-evidence",
                                              headers=self.headers).status_code)


class PaperEvidenceViewTests(unittest.TestCase):
    def test_parity_status_is_rendered_verbatim_and_not_loaded_is_never_incomplete(self) -> None:
        self.assertEqual(("NOT_LOADED", False, None), (_parity().status, _parity().loaded, _parity().report_hash))
        for status in ("PASS", "FAIL", "INCOMPLETE", "A_FUTURE_STATUS"):
            with self.subTest(status=status):
                report = _Report(status, "t", [] if status == "PASS" else ["R"])
                view = parity_evidence_view_v1(report, trial_id="t", live_side={})
                self.assertEqual((status, True, report.content_hash), (view.status, view.loaded, view.report_hash))
        for loaded, status in ((False, "INCOMPLETE"), (True, "NOT_LOADED"), (False, "PASS")):
            with self.subTest(loaded=loaded, status=status), self.assertRaises(ValueError):
                ParityEvidenceView(status=status, loaded=loaded, reasons=[], report_hash=None, summary={},
                                   live_side={})

    def test_a_parity_report_must_rederive_and_name_the_candidate(self) -> None:
        tampered = _Report("PASS", "t", [])
        tampered.identity["assessment"]["status"] = "FAIL"
        with self.assertRaises(ValueError):
            parity_evidence_view_v1(tampered, trial_id="t", live_side={})
        with self.assertRaises(ValueError):
            parity_evidence_view_v1(_Report("PASS", "other", []), trial_id="t", live_side={})

    def test_funding_is_non_costable_and_its_section_must_rederive(self) -> None:
        section = _funding_section("s", "t")
        view = funding_evidence_view_v1(section, bridges_loaded=False, not_loaded_reasons=["B", "A", "A"])
        self.assertEqual(("NOT_LOADED", False, ["A", "B"]), (view.status, view.loaded, view.reasons))
        self.assertIn("MISSING_VERIFIED_FEE_SCHEDULE_OR_6", view.economic_blockers)
        self.assertEqual(("NON_COSTABLE", False, "NOT_CALCULATED"), (view.cost_status, view.costable, view.charge))
        loaded = funding_evidence_view_v1(section, bridges_loaded=True, not_loaded_reasons=["A"])
        self.assertEqual(("NO_FUNDING_EVIDENCE_SUPPLIED", True, []), (loaded.status, loaded.loaded, loaded.reasons))
        self.assertEqual("NOT_LOADED", funding_evidence_view_v1(None, bridges_loaded=True,
                                                                not_loaded_reasons=["A"]).status)
        with self.assertRaises(ValueError):
            funding_evidence_view_v1({**section, "state": "EVIDENCE_COMPLETE"}, bridges_loaded=True,
                                     not_loaded_reasons=[])
        costable = {key: value for key, value in section.items() if key != "evidence_hash"}
        costable["costable"] = True
        costable["evidence_hash"] = identity_hash_v1(costable)
        with self.assertRaises(ValueError):
            funding_evidence_view_v1(costable, bridges_loaded=True, not_loaded_reasons=[])
        with self.assertRaises(ValueError):
            FundingEvidenceView(**{**view.model_dump(), "costable": True})
        with self.assertRaises(ValueError):
            ComparisonView(**{**_comparison(uuid4(), uuid4()).model_dump(), "composite_score": "WEIGHTED"})


class _ScriptedCursor:
    def __init__(self, rows: list[object]) -> None:
        self.rows = list(rows)
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.queries.append((query, params))

    def fetchone(self) -> object:
        return self.rows.pop(0)

    def fetchall(self) -> list[object]:  # pragma: no cover - not used by this read
        raise AssertionError("unexpected fetchall")


class _Context:
    def __init__(self, fills: list[dict[str, object]], report: dict[str, object] | None,
                 refusal: str | None = None) -> None:
        self.cycle = Mock(fills=fills, cycle_id="cycle-2026-08-20", as_of=NOW)
        self.report, self.refusal = report, refusal


class CandidatePaperEvidenceReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.study, self.trial = uuid4(), uuid4()
        self.identity = {"schema_version": "strategy-lab-study-v1", "label": "x"}
        self.study_row = (identity_hash_v1(self.identity), self.identity)

    def test_identities_are_resolved_and_rederived_server_side(self) -> None:
        from trade_platform.research_terminal_read_model_v1 import _candidate_paper_evidence

        fill = {"study_id": str(self.study), "trial_id": str(self.trial), "status": "FILLED"}
        entry = {"study_id": str(self.study), "trial_id": str(self.trial), "symbol": "ETHUSDT", "fills": 1,
                 "funding_evidence": _funding_section(str(self.study), str(self.trial)), "state": "INCUBATING"}
        cursor = _ScriptedCursor([self.study_row, ("e" * 64,), (3,)])
        view = _candidate_paper_evidence(cursor, _Context([fill], {"candidates": [entry]}),  # type: ignore[arg-type]
                                         self.study, self.trial)
        self.assertEqual((self.study_row[0], "e" * 64, "ETHUSDT"), (view.study_content_hash, view.trial_content_hash,
                                                                     view.symbol))
        self.assertEqual({"live_signals_recorded": 3, "paper_fills_in_cycle": 1, "fills_by_status": {"FILLED": 1}},
                         view.parity.live_side)
        self.assertEqual(("NOT_LOADED", ["NO_PERSISTED_PARITY_REPORT_REPLAY_NOT_RECONSTRUCTED_BY_THIS_READ"]),
                         (view.parity.status, view.parity.reasons))
        self.assertEqual(("NOT_LOADED", ["FORWARD_FUNDING_BRIDGES_NOT_PERSISTED_NOT_LOADED_BY_THIS_READ"]),
                         (view.funding.status, view.funding.reasons))
        self.assertEqual("NO_FUNDING_EVIDENCE_SUPPLIED", (view.funding.section or {})["state"])
        self.assertTrue(all("holdout" not in query.lower() for query, _ in cursor.queries))

        empty = _candidate_paper_evidence(_ScriptedCursor([self.study_row, ("e" * 64,), (0,)]),
                                          _Context([], {"candidates": []}), self.study, self.trial)  # type: ignore[arg-type]
        self.assertIn("CANDIDATE_HAS_NO_PAPER_FILL_IN_THE_CURRENT_CYCLE", empty.funding.reasons)
        self.assertIsNone(empty.funding.section)
        refused = _candidate_paper_evidence(_ScriptedCursor([self.study_row, ("e" * 64,), (0,)]),
                                            _Context([], None, "INCUBATION_REPORT_REFUSED:x"),  # type: ignore[arg-type]
                                            self.study, self.trial)
        self.assertIn("INCUBATION_REPORT_REFUSED:x", refused.funding.reasons)

    def test_unknown_or_unverifiable_identities_are_refused(self) -> None:
        from trade_platform.research_terminal_read_model_v1 import (
            TerminalObjectNotFound,
            _candidate_paper_evidence,
        )

        context = _Context([], {"candidates": []})
        with self.assertRaises(TerminalObjectNotFound):
            _candidate_paper_evidence(_ScriptedCursor([None]), context, self.study, self.trial)  # type: ignore[arg-type]
        with self.assertRaises(TerminalObjectNotFound):
            _candidate_paper_evidence(_ScriptedCursor([self.study_row, None]), context,  # type: ignore[arg-type]
                                      self.study, self.trial)
        with self.assertRaises(ValueError):
            _candidate_paper_evidence(_ScriptedCursor([("0" * 64, self.identity)]), context,  # type: ignore[arg-type]
                                      self.study, self.trial)


class RuntimeImageImportTests(unittest.TestCase):
    def test_the_api_imports_without_numerical_libraries(self) -> None:
        # The runtime image installs requirements-runtime.txt only: no numpy, polars or duckdb.
        code = ("import sys\n"
                "for name in ('numpy', 'polars', 'duckdb', 'pyarrow'): sys.modules[name] = None\n"
                "import trade_platform.api, trade_platform.research_terminal_read_model_v1\n"
                "import trade_platform.research_candidate_comparison_v1, trade_platform.paper_funding_evidence_v1\n"
                "import trade_platform.paper_incubation_report_v1\n")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False,
                                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
        self.assertEqual(0, result.returncode, result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
