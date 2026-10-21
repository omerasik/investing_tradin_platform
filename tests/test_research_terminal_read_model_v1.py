"""UI-2 -- research terminal reads are protected, GET-only and cannot express a higher claim."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
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
    PaperEconomicsSectionView,
    ParityEvidenceView,
    RerunView,
    TerminalOverview,
    ValidationView,
    WalkForwardView,
    funding_evidence_view_v1,
    parity_evidence_view_v1,
    stored_funding_evidence_view_v1,
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
    from trade_platform.research_terminal_read_model_v1 import _risk_not_loaded

    return CandidatePaperEvidenceView(
        study_id=study, study_content_hash="d" * 64, trial_id=trial, trial_content_hash="e" * 64,
        cycle_id="cycle-2026-08-20", symbol=None, incubation=None, parity=_parity(),
        funding=funding_evidence_view_v1(None, not_loaded_reasons=["X"]),
        economics=PaperEconomicsSectionView(status="NOT_LOADED", reasons=["CANDIDATE_HAS_NO_PAPER_FILL_IN_THE_CURRENT_CYCLE"]),
        risk=_risk_not_loaded(["NO_STORED_INCUBATING_SIGNAL_TARGET_FOR_CANDIDATE_IN_CYCLE"]), as_of=NOW)


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
        self.assertEqual(("NOT_LOADED", "RETURN_PER_UNIT_NOTIONAL_SUMMED_NOT_COMPOUNDED_PENDING_OR_11",
                          "NONE_NOT_A_CURRENCY_AMOUNT"),
                         (paper["economics"]["status"], paper["economics"]["units"], paper["economics"]["money"]))
        self.assertEqual(("NOT_LOADED", False, None, False),
                         (paper["risk"]["status"], paper["risk"]["loaded"], paper["risk"]["sizing"],
                          paper["risk"]["execution_authority"]))
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
        view = funding_evidence_view_v1(section, not_loaded_reasons=["B", "A", "A"])
        self.assertEqual(("NOT_LOADED", False, ["A", "B"]), (view.status, view.loaded, view.reasons))
        self.assertIn("MISSING_VERIFIED_FEE_SCHEDULE_OR_6", view.economic_blockers)
        self.assertEqual(("NON_COSTABLE", False, "NOT_CALCULATED"), (view.cost_status, view.costable, view.charge))
        self.assertEqual("NOT_LOADED", funding_evidence_view_v1(None, not_loaded_reasons=["A"]).status)
        with self.assertRaises(ValueError):
            funding_evidence_view_v1({**section, "state": "EVIDENCE_COMPLETE"}, not_loaded_reasons=["A"])
        costable = {key: value for key, value in section.items() if key != "evidence_hash"}
        costable["costable"] = True
        costable["evidence_hash"] = identity_hash_v1(costable)
        with self.assertRaises(ValueError):
            funding_evidence_view_v1(costable, not_loaded_reasons=["A"])
        with self.assertRaises(ValueError):  # a stored record whose section claims to be costable is refused
            stored_funding_evidence_view_v1(_stored_funding_row("s", "t", costable), study_id="s", trial_id="t",
                                            cycle_id=CYCLE.cycle_id)
        with self.assertRaises(ValueError):
            FundingEvidenceView(**{**view.model_dump(), "costable": True})
        with self.assertRaises(ValueError):
            ComparisonView(**{**_comparison(uuid4(), uuid4()).model_dump(), "composite_score": "WEIGHTED"})


def _stored_parity_row(study: str, trial: str, status: str = "INCOMPLETE") -> object:
    from trade_platform.paper_evidence_store_v1 import StoredFillParityV1

    at = datetime(2026, 8, 2, tzinfo=UTC)
    return StoredFillParityV1(
        assessment_id=uuid4(), report_content_hash="c" * 64, cycle_id=CYCLE.cycle_id, study_id=UUID(study),
        trial_id=UUID(trial), symbol="BTCUSDT", status=status,
        reasons=() if status == "PASS" else ("REPLAY_BAR_NOT_OBSERVED",), as_of=at,
        window_start=at - timedelta(days=1), window_end=at - timedelta(hours=22), source_hashes={},
        identity={"summary": {"outcomes": {"MATCH": 59}}}, recorded_at=at + timedelta(hours=1), report_id=uuid4(),
        schema_version="paper-fill-parity-v1", study_content_hash="d" * 64, authority_evidence_hash="v" * 64,
        holdout_start=datetime(2026, 8, 20, tzinfo=UTC), holdout_end_exclusive=None)


def _stored_funding_row(study: str, trial: str, section: dict[str, object] | None = None) -> object:
    from trade_platform.paper_evidence_store_v1 import StoredFundingEvidenceV1

    section = section or _funding_section(study, trial)
    at = datetime(2026, 8, 2, tzinfo=UTC)
    return StoredFundingEvidenceV1(
        record_id=uuid4(), evidence_hash=str(section["evidence_hash"]), cycle_id=CYCLE.cycle_id,
        study_id=UUID(study), trial_id=UUID(trial), symbol="ETHUSDT", state=str(section["state"]),
        completeness_blockers=tuple(section["completeness_blockers"]),  # type: ignore[arg-type]
        economic_blockers=tuple(section["economic_blockers"]), as_of=at,  # type: ignore[arg-type]
        window_start=None, window_end=None, bridge_hashes={}, evidence=section, recorded_at=at + timedelta(minutes=5),
        schema_version="paper-funding-evidence-v1", evidence_validation_hash=None)


class StoredPaperEvidencePathTests(unittest.TestCase):
    """Parity and funding come only from the store: stored -> verbatim, None -> NOT_LOADED, refusal -> REFUSED."""

    def setUp(self) -> None:
        self.study, self.trial = uuid4(), uuid4()
        self.identity = {"schema_version": "strategy-lab-study-v1", "label": "x"}
        self.study_row = (identity_hash_v1(self.identity), self.identity)

    def read(self, parity: object = None, funding: object = None) -> CandidatePaperEvidenceView:
        from unittest.mock import patch

        from trade_platform.paper_evidence_store_v1 import PaperEvidenceStoreError
        from trade_platform.research_terminal_read_model_v1 import _candidate_paper_evidence

        def answer(value: object) -> dict[str, object]:
            if isinstance(value, PaperEvidenceStoreError):
                return {"side_effect": value}
            return {"return_value": value}

        cursor = _ScriptedCursor([self.study_row, ("e" * 64,), (0,)])
        with patch("trade_platform.paper_evidence_store_v1.read_fill_parity_v1", **answer(parity)) as parity_read, \
                patch("trade_platform.paper_evidence_store_v1.read_funding_evidence_v1", **answer(funding)) as funding_read:
            view = _candidate_paper_evidence(cursor, _Context([], {"candidates": []}),  # type: ignore[arg-type]
                                             self.study, self.trial)
        for read in (parity_read, funding_read):
            self.assertEqual({"cycle_id": "cycle-2026-08-20", "study_id": self.study, "trial_id": self.trial,
                              "as_of": NOW}, read.call_args.kwargs)
        return view

    def test_a_stored_row_is_restated_verbatim_with_its_instants(self) -> None:
        for status in ("PASS", "FAIL", "INCOMPLETE"):
            with self.subTest(status=status):
                parity = _stored_parity_row(str(self.study), str(self.trial), status)
                funding = _stored_funding_row(str(self.study), str(self.trial))
                view = self.read(parity, funding)
                self.assertEqual((status, True, "c" * 64, list(parity.reasons), parity.assessment_id),
                                 (view.parity.status, view.parity.loaded, view.parity.report_hash, view.parity.reasons,
                                  view.parity.assessment_id))
                self.assertEqual((parity.as_of, parity.recorded_at, parity.window_start, parity.window_end),
                                 (view.parity.evidence_as_of, view.parity.recorded_at, view.parity.window_start,
                                  view.parity.window_end))
                self.assertEqual({"outcomes": {"MATCH": 59}}, view.parity.summary)
                self.assertEqual(("NO_FUNDING_EVIDENCE_SUPPLIED", True, [], funding.evidence_hash, funding.record_id),
                                 (view.funding.status, view.funding.loaded, view.funding.reasons,
                                  view.funding.evidence_hash, view.funding.record_id))
                self.assertEqual(("NON_COSTABLE", False, "NOT_CALCULATED", dict(funding.evidence)),
                                 (view.funding.cost_status, view.funding.costable, view.funding.charge,
                                  view.funding.section))
                self.assertEqual(list(funding.economic_blockers), view.funding.economic_blockers)
                # Stored funding is evidence only: no cycle fill, nothing projected, never a cost.
                self.assertEqual(("NOT_LOADED", None), (view.economics.status, view.economics.funding))

    def test_nothing_stored_is_not_loaded_with_its_reason(self) -> None:
        view = self.read()
        self.assertEqual(("NOT_LOADED", False, ["NO_STORED_PARITY_ASSESSMENT_FOR_CANDIDATE_IN_CYCLE"], None),
                         (view.parity.status, view.parity.loaded, view.parity.reasons, view.parity.assessment_id))
        self.assertEqual(("NOT_LOADED", False, None),
                         (view.funding.status, view.funding.loaded, view.funding.record_id))
        self.assertEqual(["CANDIDATE_HAS_NO_PAPER_FILL_IN_THE_CURRENT_CYCLE",
                          "NO_STORED_FUNDING_EVIDENCE_FOR_CANDIDATE_IN_CYCLE"], view.funding.reasons)

    def test_a_refused_stored_row_is_refused_never_not_loaded_nor_a_500(self) -> None:
        from trade_platform.paper_evidence_store_v1 import PaperEvidenceStoreError

        view = self.read(PaperEvidenceStoreError("STORED_FILL_PARITY_DOES_NOT_REDERIVE"),
                         PaperEvidenceStoreError("FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS"))
        self.assertEqual(("REFUSED", False, ["STORED_EVIDENCE_REFUSED:STORED_FILL_PARITY_DOES_NOT_REDERIVE"], None),
                         (view.parity.status, view.parity.loaded, view.parity.reasons, view.parity.report_hash))
        self.assertEqual(("REFUSED", False, ["STORED_EVIDENCE_REFUSED:FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS"],
                          None, None, "NON_COSTABLE"),
                         (view.funding.status, view.funding.loaded, view.funding.reasons, view.funding.evidence_hash,
                          view.funding.section, view.funding.cost_status))
        # A row of another candidate is refused too (the store never returns one; the view does not trust it).
        other = self.read(_stored_parity_row(str(uuid4()), str(self.trial)),
                          _stored_funding_row(str(self.study), str(uuid4())))
        self.assertEqual(("REFUSED", "REFUSED"), (other.parity.status, other.funding.status))

    def test_refused_not_loaded_and_verbatim_statuses_never_mix(self) -> None:
        from trade_platform.research_terminal_read_model_v1 import (
            refused_funding_view_v1,
            refused_parity_view_v1,
        )

        refused = refused_parity_view_v1("X", live_side={}).model_dump()
        stored = self.read(_stored_parity_row(str(self.study), str(self.trial)),
                           _stored_funding_row(str(self.study), str(self.trial)))
        loaded = stored.parity.model_dump()
        for payload in ({**refused, "loaded": True}, {**refused, "status": "NOT_LOADED"},
                        {**refused, "reasons": ["X"]}, {**refused, "reasons": []},
                        {**refused, "report_hash": "c" * 64},
                        {**loaded, "status": "REFUSED"}, {**loaded, "status": "NOT_LOADED"},
                        {**loaded, "loaded": False},
                        {**_parity().model_dump(), "reasons": ["STORED_EVIDENCE_REFUSED:X"]},
                        {**_parity().model_dump(), "status": "INCOMPLETE"}):
            with self.subTest(payload=str(payload)[:70]), self.assertRaises(ValueError):
                ParityEvidenceView(**payload)
        refused_f = refused_funding_view_v1("X").model_dump()
        loaded_f = stored.funding.model_dump()
        for payload in ({**refused_f, "loaded": True}, {**refused_f, "status": "NOT_LOADED"},
                        {**refused_f, "evidence_hash": "a" * 64}, {**loaded_f, "status": "NOT_LOADED"},
                        {**loaded_f, "status": "REFUSED", "loaded": False},
                        {**loaded_f, "record_id": None}, {**loaded_f, "costable": True}):
            with self.subTest(payload=str(payload)[:70]), self.assertRaises(ValueError):
                FundingEvidenceView(**payload)


#: Holdout names a paper-evidence read may mention: registry and recorded gate boundaries, never holdout data.
ALLOWED_HOLDOUT_NAMES = {"strategy_lab_holdout_validations", "holdout_start", "holdout_end_exclusive"}


class _ScriptedCursor:
    """``fetchone`` rows in order (the trailing ``None`` is the absent stored signal target); ``fetchall`` empty."""

    def __init__(self, rows: list[object]) -> None:
        self.rows = list(rows)
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.queries.append((query, params))

    def fetchone(self) -> object:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[object]:  # the candidate's exit criteria: none recorded
        return []


class _Context:
    def __init__(self, fills: list[dict[str, object]], report: dict[str, object] | None,
                 refusal: str | None = None) -> None:
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v2

        self.cycle = Mock(fills=fills, cycle_id="cycle-2026-08-20", as_of=NOW, cost=gross_cost_policy_v2(),
                          resolution=None)
        self.report, self.refusal, self.cost_approval = report, refusal, None


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
        self.assertEqual(("NOT_LOADED", ["NO_STORED_PARITY_ASSESSMENT_FOR_CANDIDATE_IN_CYCLE"]),
                         (view.parity.status, view.parity.reasons))
        self.assertEqual(("NOT_LOADED", ["NO_STORED_FUNDING_EVIDENCE_FOR_CANDIDATE_IN_CYCLE"]),
                         (view.funding.status, view.funding.reasons))
        self.assertEqual("NO_FUNDING_EVIDENCE_SUPPLIED", (view.funding.section or {})["state"])
        # The holdout is never read: the only holdout names are the validation registry the cycle's INCUBATING
        # signals are bound to (as for the cycle's fills) and a stored parity row's recorded gate boundaries.
        for query, _ in cursor.queries:
            self.assertEqual([], [word for word in query.lower().replace(",", " ").split()
                                  if "holdout" in word and word not in ALLOWED_HOLDOUT_NAMES])
        # A fill that is not a stored paper fill identity is refused by the projection, never shown as figures.
        self.assertEqual(("REFUSED", ["PAPER_ECONOMICS_REFUSED:fill_schema_version_unknown"]),
                         (view.economics.status, view.economics.reasons))
        self.assertIsNone(view.economics.net)
        self.assertEqual(("NOT_LOADED", ["NO_ACCOUNT_BOUND_BY_AUTHORIZED_EXIT_CRITERIA",
                                         "NO_STORED_INCUBATING_SIGNAL_TARGET_FOR_CANDIDATE_IN_CYCLE"]),
                         (view.risk.status, view.risk.reasons))

        empty = _candidate_paper_evidence(_ScriptedCursor([self.study_row, ("e" * 64,), (0,)]),
                                          _Context([], {"candidates": []}), self.study, self.trial)  # type: ignore[arg-type]
        self.assertIn("CANDIDATE_HAS_NO_PAPER_FILL_IN_THE_CURRENT_CYCLE", empty.funding.reasons)
        self.assertIsNone(empty.funding.section)
        self.assertEqual(("NOT_LOADED", ["CANDIDATE_HAS_NO_PAPER_FILL_IN_THE_CURRENT_CYCLE"]),
                         (empty.economics.status, empty.economics.reasons))
        refused = _candidate_paper_evidence(_ScriptedCursor([self.study_row, ("e" * 64,), (0,)]),
                                            _Context([], None, "INCUBATION_REPORT_REFUSED:x"),  # type: ignore[arg-type]
                                            self.study, self.trial)
        self.assertIn("INCUBATION_REPORT_REFUSED:x", refused.funding.reasons)
        self.assertEqual(("NOT_LOADED", ["INCUBATION_REPORT_REFUSED:x"]),
                         (refused.economics.status, refused.economics.reasons))

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


class _EconomicsContext:
    """The per-request paper context as ``_candidate_economics`` reads it (stored policy, approval, no input)."""

    def __init__(self, policy: object, approval: object = None, refusal: str | None = None) -> None:
        from tests.test_paper_economics_v1 import AS_OF

        self.cycle = Mock(cost=policy, as_of=AS_OF, resolution=None)
        self.cost_approval, self.refusal = approval, refusal


class PaperEconomicsSectionTests(unittest.TestCase):
    """The terminal restates ``paper_economics_v1`` verbatim: every component apart, units never money."""

    def economics(self, fills: list[dict[str, object]], policy: object, approval: object = None) -> object:
        from tests.test_paper_economics_v1 import STUDY, TRIAL
        from trade_platform.research_terminal_read_model_v1 import _candidate_economics

        return _candidate_economics(_EconomicsContext(policy, approval), STUDY, TRIAL, fills)  # type: ignore[arg-type]

    def test_gross_policy_projects_realized_apart_from_unrealized_and_every_cost_is_missing(self) -> None:
        from tests.test_paper_economics_v1 import GROSS, chain
        from trade_platform.paper_economics_v1 import UNITS_V1

        view = self.economics(chain((1, 1, "100"), (5, 0, "110"), (9, -1, "100")), GROSS)
        self.assertEqual(("PROJECTED", [], UNITS_V1), (view.status, view.reasons, view.units))
        self.assertEqual("0.1", view.gross_realized["value"].rstrip("0"))
        # The open short has no stored proven mark: UNAVAILABLE with the module's own reason, never a guess.
        self.assertEqual(("UNAVAILABLE", None, ["PRICE_SELECTION_POLICY_NOT_CONFIGURED"], -1, False),
                         (view.gross_unrealized.status, view.gross_unrealized.value, view.gross_unrealized.reasons,
                          view.gross_unrealized.exposure, view.gross_unrealized.in_net))
        self.assertEqual("SEALED_T4_PUBLIC_TRADE_REFERENCE_NOT_VENUE_SETTLEMENT_MARK", view.unrealized_mark_source)
        self.assertEqual(("MISSING", None, ["MISSING_VERIFIED_FEE_SCHEDULE_OR_6"]),
                         (view.fees.status, view.fees.value, view.fees.reasons))
        self.assertEqual(("MISSING", None, ["NO_SLIPPAGE_SCENARIO_DECLARED"]),
                         (view.slippage.status, view.slippage.by_scenario, view.slippage.reasons))
        self.assertEqual(("NON_COSTABLE_EVIDENCE", None, "NOT_CALCULATED"),
                         (view.funding.status, view.funding.value, view.funding.detail["charge"]))
        self.assertIn("FUNDING_EVIDENCE_IS_NEVER_A_CHARGE_IN_V1", view.funding.reasons)
        self.assertEqual(("NOT_COSTABLE", None), (view.net.status, view.net.by_scenario))
        # No sensitivity row exists unless a stored policy declares one: nothing is hard-coded here.
        self.assertEqual([], view.sensitivity_rows)
        self.assertEqual(64, len(view.economics_hash or ""))

    def test_sensitivity_rows_come_only_from_the_stored_policy_and_never_enter_net(self) -> None:
        from tests.test_paper_economics_v1 import CONSERVATIVE_MEDIUM, chain
        from trade_platform.strategy_lab_policies_v1 import CostPolicyV2

        stored = CostPolicyV2(fee_schedule=None, slippage_scenarios=CONSERVATIVE_MEDIUM)
        view = self.economics(chain((1, 1, "100"), (5, 0, "110"), policy=stored), stored)
        self.assertEqual([("conservative-medium-high", "5"), ("conservative-medium-low", "1"),
                          ("conservative-medium-mid", "2.5")],
                         [(row.name, row.cost_bps_per_side) for row in view.sensitivity_rows])
        self.assertTrue(all(row.status == "PROPOSED_SENSITIVITY_SCENARIO" and row.in_net is False
                            and row.source == "COST_POLICY_SCENARIO_NOT_OWNER_APPROVED" for row in view.sensitivity_rows))
        self.assertEqual(("MISSING", ["SLIPPAGE_ENVELOPE_NOT_OWNER_APPROVED_OR_6"]),
                         (view.slippage.status, view.slippage.reasons))
        self.assertEqual("NOT_COSTABLE", view.net.status)

    def test_an_authorized_packet_verifies_fees_and_slippage_but_funding_keeps_net_not_costable(self) -> None:
        from tests.test_paper_economics_v1 import VERIFIED_POLICY, approval, chain

        view = self.economics(chain((1, 1, "100"), (5, 0, "110"), policy=VERIFIED_POLICY), VERIFIED_POLICY,
                              approval())
        self.assertEqual(("VERIFIED", "VERIFIED", "NON_COSTABLE_EVIDENCE"),
                         (view.fees.status, view.slippage.status, view.funding.status))
        self.assertIsNotNone(view.fees.value)
        self.assertEqual(("NOT_COSTABLE", None), (view.net.status, view.net.by_scenario))
        self.assertTrue(any(reason.startswith("COMPONENT_NOT_VERIFIED:funding:") for reason in view.net.reasons))
        self.assertEqual("owner", view.cost_approval["authorized_by"])

    def test_refusals_and_absences_are_never_figures(self) -> None:
        from tests.test_paper_economics_v1 import GROSS, VERIFIED_POLICY, approval, chain, packet
        from trade_platform.research_terminal_read_model_v1 import _candidate_economics

        refused = self.economics(chain((1, 1, "100"), policy=VERIFIED_POLICY), VERIFIED_POLICY,
                                 approval(packet(policy=GROSS)))
        self.assertEqual(("REFUSED", ["PAPER_ECONOMICS_REFUSED:cost_approval_is_of_another_cost_policy"]),
                         (refused.status, refused.reasons))
        self.assertIsNone(refused.net)
        self.assertEqual(("NOT_LOADED", ["CANDIDATE_HAS_NO_PAPER_FILL_IN_THE_CURRENT_CYCLE"]),
                         (self.economics([], GROSS).status, self.economics([], GROSS).reasons))
        upstream = _candidate_economics(_EconomicsContext(GROSS, refusal="INCUBATION_REPORT_REFUSED:x"),  # type: ignore[arg-type]
                                        "s", "t", chain((1, 1, "100")))
        self.assertEqual(("NOT_LOADED", ["INCUBATION_REPORT_REFUSED:x"]), (upstream.status, upstream.reasons))

    def test_a_proven_mark_alone_makes_unrealized_available_and_payloads_must_rederive(self) -> None:
        from dataclasses import replace

        from tests.test_paper_economics_v1 import SOURCE_HASH, STUDY, TRIAL, chain, project
        from trade_platform.paper_economics_v1 import ProvenMarkV1
        from trade_platform.research_terminal_read_model_v1 import economics_section_view_v1

        fills = chain((1, 1, "100"))
        opened = fills[0]["fill_bar_open_micros"]
        mark = ProvenMarkV1("BTCUSDT", "105", opened + 120_000_000, opened + 120_300_000, SOURCE_HASH)
        view = economics_section_view_v1(project(fills, marks=(mark,)), study_id=STUDY, trial_id=TRIAL)
        self.assertEqual(("AVAILABLE", "T4_PROVEN_BAR_OPEN_FIRST_TRADE", False),
                         (view.gross_unrealized.status, view.gross_unrealized.mark["evidence_kind"],
                          view.gross_unrealized.in_net))
        self.assertEqual("0.05", view.gross_unrealized.value.rstrip("0"))
        result = project(fills)
        with self.assertRaises(ValueError):
            economics_section_view_v1(replace(result, content_hash="0" * 64), study_id=STUDY, trial_id=TRIAL)
        with self.assertRaises(ValueError):
            economics_section_view_v1(result, study_id=STUDY, trial_id=str(uuid4()))

    def test_the_views_cannot_conflate_states_or_express_a_figure_they_do_not_have(self) -> None:
        from tests.test_paper_economics_v1 import GROSS, chain
        from trade_platform.research_terminal_read_model_v1 import (
            EconomicsComponentView,
            EconomicsNetView,
            SensitivityRowView,
            UnrealizedEvidenceView,
        )

        projected = self.economics(chain((1, 1, "100"), (5, 0, "110")), GROSS)
        data = projected.model_dump()
        bad = [
            {**data, "status": "NOT_LOADED"},                       # figures under NOT_LOADED
            {**data, "reasons": ["X"]},                              # a projection restated with invented reasons
            {**data, "net": {**data["net"], "status": "CONFIRMED", "by_scenario": {"s": "0.1"}}},  # unverified costs
            {**data, "funding": {**data["funding"], "status": "VERIFIED", "value": "-0.01"}},       # funding a charge
            {**data, "units": "USD"},
            {**data, "money": "USDT"},
            {"status": "REFUSED", "reasons": []},
        ]
        for payload in bad:
            with self.subTest(payload=str(payload)[:80]), self.assertRaises(ValueError):
                PaperEconomicsSectionView(**payload)
        with self.assertRaises(ValueError):
            EconomicsComponentView(status="MISSING", value="-0.001", reasons=["X"], detail={})
        with self.assertRaises(ValueError):
            EconomicsComponentView(status="NOT_LOADED", value=None, reasons=["X"], detail={})
        with self.assertRaises(ValueError):
            EconomicsNetView(status="NOT_COSTABLE", scope="x", by_scenario={"s": "0.1"}, reasons=["X"])
        with self.assertRaises(ValueError):
            UnrealizedEvidenceView(status="AVAILABLE", value="0.1", reasons=[], exposure=1, mark=None, detail={})
        with self.assertRaises(ValueError):
            SensitivityRowView(name="n", status="PROPOSED_SENSITIVITY_SCENARIO", source="s", cost_bps_per_side="1",
                               rationale="r", value="-0.0002", in_net=True, scenario_hash="a" * 64)


#: A stored INCUBATING signal decided after the fixture policy was approved and recorded (2026-09-01).
DECIDED = datetime(2026, 9, 2, 0, 0, 1, tzinfo=UTC)


def _signal_row(study: UUID, trial: UUID, *, target_to: int = 1, tamper: bool = False) -> tuple[object, ...]:
    knowledge = int((DECIDED.timestamp() - 1) * 1_000_000)
    identity = {"authority": "INCUBATING", "candidate": {"study_id": str(study), "trial_id": str(trial)},
                "symbol": "BTCUSDT", "target_from": 0, "target_to": target_to, "authority_evidence_hash": "a" * 64,
                "evidence": {"complete_market_knowledge_micros": knowledge}}
    content_hash = identity_hash_v1(identity)
    if tamper:
        identity = {**identity, "target_to": -target_to}
    return (uuid4(), content_hash, identity, DECIDED, study, trial, "BTCUSDT", 0, target_to)


class _RoutingCursor:
    """Answers each read by the table it names: stored account rows, policy versions, signals; no fills."""

    def __init__(self, *, account: object, policies: list[object], signals: list[object],
                 bound: object = None) -> None:
        self.account, self.policies, self.signals, self.bound = account, policies, signals, bound
        self.sql = ""
        self.queries: list[str] = []

    def execute(self, sql: str, params: object = ()) -> None:
        self.sql = sql
        self.queries.append(sql)

    def fetchone(self) -> object:
        if "FROM account_contexts" in self.sql:
            return self.account
        if "WHERE content_hash" in self.sql:
            return self.bound
        if "FROM live_strategy_signals" in self.sql:
            return self.signals[0] if self.signals else None
        raise AssertionError(self.sql)

    def fetchall(self) -> list[object]:
        if "FROM account_policy_versions WHERE account_id" in self.sql:
            return list(self.policies)
        if "live_strategy_signals" in self.sql and "DISTINCT ON" in self.sql:
            return list(self.signals)
        return []  # fills, openings, exit criteria: none recorded


class PaperRiskViewTests(unittest.TestCase):
    """Risk restates ``paper_risk_assessment_v1`` for a recorded target only; NOT_LOADED is never a verdict."""

    def setUp(self) -> None:
        from tests.test_paper_risk_assessment_v1 import ACCOUNT_ROW, policy, row

        self.study, self.trial = uuid4(), uuid4()
        self.active = policy()
        self.account_row, self.row = ACCOUNT_ROW, row

    def candidate_risk(self, cursor: _RoutingCursor, criteria: object = None) -> object:
        from unittest.mock import patch

        from trade_platform.research_terminal_read_model_v1 import _candidate_risk

        context = Mock(cycle=Mock(cycle_id="cycle-2026-08-20", as_of=NOW))
        with patch("trade_platform.incubation_exit_criteria_v1.resolve_exit_criteria_v1", return_value=criteria):
            return _candidate_risk(cursor, context, self.study, self.trial)  # type: ignore[arg-type]

    def bound_criteria(self, content_hash: str) -> object:
        return Mock(packet=Mock(status="AUTHORIZED",
                                frozen={"criteria": {"required_account_policy_hash": content_hash}}))

    def test_without_a_recorded_target_or_bound_account_risk_is_not_loaded(self) -> None:
        cursor = _RoutingCursor(account=self.account_row, policies=[self.row(self.active)], signals=[])
        view = self.candidate_risk(cursor)
        self.assertEqual(("NOT_LOADED", False, None, None, "NONE"),
                         (view.status, view.loaded, view.sizing, view.account_id, view.account_source))
        self.assertEqual(["NO_ACCOUNT_BOUND_BY_AUTHORIZED_EXIT_CRITERIA",
                          "NO_STORED_INCUBATING_SIGNAL_TARGET_FOR_CANDIDATE_IN_CYCLE"], view.reasons)
        # A recorded target without a bound account is still not assessed: no account is ever guessed.
        signal = _signal_row(self.study, self.trial)
        view = self.candidate_risk(_RoutingCursor(account=self.account_row, policies=[self.row(self.active)],
                                                  signals=[signal]))
        self.assertEqual(("NOT_LOADED", ["NO_ACCOUNT_BOUND_BY_AUTHORIZED_EXIT_CRITERIA"], 1),
                         (view.status, view.reasons, view.requested_target["target_to"]))
        view = self.candidate_risk(_RoutingCursor(account=self.account_row, policies=[], signals=[signal]),
                                   self.bound_criteria("f" * 64))
        self.assertEqual(("NOT_LOADED", ["BOUND_ACCOUNT_POLICY_VERSION_NOT_RECORDED"]), (view.status, view.reasons))

    def test_a_recorded_target_under_the_bound_account_is_assessed_at_its_decision_instant(self) -> None:
        signal = _signal_row(self.study, self.trial)
        cursor = _RoutingCursor(account=self.account_row, policies=[self.row(self.active)], signals=[signal],
                                bound=(str(self.active.policy_version_id), "fixture-paper"))
        view = self.candidate_risk(cursor, self.bound_criteria(self.active.content_hash))
        # Every control passes, but no reference price is stored for a signal: sizing is withheld, UNRESOLVED.
        self.assertEqual(("UNRESOLVED", True, "OPEN", None), (view.status, view.loaded, view.order_action, view.sizing))
        self.assertEqual(["REFERENCE_PRICE_MISSING"], view.unresolved_reasons)
        self.assertEqual(("AUTHORIZED_EXIT_CRITERIA_BINDING", DECIDED, "LATEST_STORED_INCUBATING_SIGNAL"),
                         (view.account_source, view.assessed_as_of, view.requested_target["source"]))
        self.assertEqual(str(self.active.policy_version_id), view.policy_resolution["expected_policy_version_id"])
        self.assertEqual(("ACTIVE", []), (view.current_policy.status, view.current_policy.unresolved))
        self.assertFalse(view.execution_authority)
        self.assertEqual({"PASS", "NOT_APPLICABLE", "EVIDENCE_ONLY"}, {item["status"] for item in view.controls})

    def test_an_unconfigured_or_later_policy_is_unresolved_never_active(self) -> None:
        from tests.test_paper_risk_assessment_v1 import PAPER
        from trade_platform.account_policy_v1 import AccountPolicyV1

        unconfigured = AccountPolicyV1(PAPER, {})
        signal = _signal_row(self.study, self.trial)
        view = self.candidate_risk(
            _RoutingCursor(account=self.account_row, policies=[self.row(unconfigured)], signals=[signal],
                           bound=(str(unconfigured.policy_version_id), "fixture-paper")),
            self.bound_criteria(unconfigured.content_hash))
        self.assertEqual(("UNRESOLVED", None), (view.status, view.sizing))
        self.assertIn("ACCOUNT_POLICY_UNCONFIGURED", view.unresolved_reasons)
        self.assertTrue(any(reason.startswith("MISSING_OWNER_") and reason.endswith("_OR_11")
                            for reason in view.unresolved_reasons))
        self.assertEqual(("UNRESOLVED", "UNCONFIGURED"), (view.current_policy.status, view.current_policy.policy_status))
        self.assertTrue(all(item["status"] == "NOT_EVALUATED" for item in view.controls))
        # A policy recorded after the signal's decision is current now, but was not known when it was decided.
        later = _RoutingCursor(account=self.account_row, policies=[self.row(self.active, recorded=NOW)],
                               signals=[signal], bound=(str(self.active.policy_version_id), "fixture-paper"))
        view = self.candidate_risk(later, self.bound_criteria(self.active.content_hash))
        self.assertEqual(("UNRESOLVED", ["NO_ACCOUNT_POLICY_KNOWN_AT_AS_OF"]), (view.status, view.unresolved_reasons))
        self.assertEqual("ACTIVE", view.current_policy.status)

    def test_a_signal_row_that_does_not_rederive_is_refused(self) -> None:
        cursor = _RoutingCursor(account=self.account_row, policies=[], signals=[_signal_row(self.study, self.trial,
                                                                                            tamper=True)])
        with self.assertRaises(ValueError):
            self.candidate_risk(cursor)

    def test_the_paper_account_read_assesses_each_recorded_target_under_the_named_account(self) -> None:
        from trade_platform.research_terminal_read_model_v1 import read_account_risk_v1

        signal = _signal_row(self.study, self.trial)
        view = read_account_risk_v1(_RoutingCursor(account=self.account_row, policies=[self.row(self.active)],
                                                   signals=[signal]), "fixture-paper", now=NOW)
        self.assertEqual(("ACTIVE", [], 1), (view.current_policy.status, view.reasons, len(view.candidates)))
        self.assertEqual(("UNRESOLVED", "OPERATOR_SELECTED_ACCOUNT"),
                         (view.candidates[0].status, view.candidates[0].account_source))
        empty = read_account_risk_v1(_RoutingCursor(account=None, policies=[], signals=[]), "nobody", now=NOW)
        self.assertEqual(("UNRESOLVED", ["ACCOUNT_NOT_REGISTERED"], [], ["NO_STORED_INCUBATING_SIGNAL_TARGET_FOR_CANDIDATE_IN_CYCLE"]),
                         (empty.current_policy.status, empty.current_policy.unresolved, empty.candidates, empty.reasons))

    def test_the_views_cannot_conflate_not_loaded_with_a_verdict(self) -> None:
        from trade_platform.research_terminal_read_model_v1 import (
            AccountPolicyResolutionView,
            PaperRiskView,
            _risk_not_loaded,
        )

        not_loaded = _risk_not_loaded(["X"]).model_dump()
        for payload in ({**not_loaded, "status": "BLOCKED"}, {**not_loaded, "loaded": True},
                        {**not_loaded, "reasons": []}, {**not_loaded, "sizing": {"open_quantity": "1"}},
                        {**not_loaded, "status": "UNRESOLVED", "loaded": True, "assessment_hash": "a" * 64,
                         "account_id": "a", "requested_target": {}, "unresolved_reasons": ["U"],
                         "sizing": {"open_quantity": "1"}},
                        {**not_loaded, "status": "BLOCKED", "loaded": True, "assessment_hash": "a" * 64,
                         "account_id": "a", "requested_target": {}, "blocking_reasons": []},
                        {**not_loaded, "execution_authority": True}):
            with self.subTest(payload=str(payload)[:60]), self.assertRaises(ValueError):
                PaperRiskView(**payload)
        signal = _signal_row(self.study, self.trial)
        view = self.candidate_risk(
            _RoutingCursor(account=self.account_row, policies=[self.row(self.active)], signals=[signal],
                           bound=(str(self.active.policy_version_id), "fixture-paper")),
            self.bound_criteria(self.active.content_hash))
        current = view.current_policy.model_dump()
        for payload in ({**current, "status": "ACTIVE", "policy_status": "UNCONFIGURED"},
                        {**current, "status": "ACTIVE", "unresolved": ["MISSING_OWNER_X_OR_11"]},
                        {**current, "status": "UNRESOLVED"}):
            with self.subTest(payload=str(payload)[:60]), self.assertRaises(ValueError):
                AccountPolicyResolutionView(**payload)


class TerminalStoredIdentityAcceptanceTests(unittest.TestCase):
    """List views must verify the same immutable identities as detailed financial reads."""

    def assert_refused(self, reader: object, row: tuple, changed: dict[int, object], **kwargs: object) -> None:
        cursor = Mock()
        cursor.fetchall.return_value = [row]
        self.assertEqual(1, len(reader(cursor, **kwargs)))  # type: ignore[operator]
        for index, value in changed.items():
            with self.subTest(column=index):
                corrupted = list(row)
                corrupted[index] = value
                cursor.fetchall.return_value = [tuple(corrupted)]
                with self.assertRaises(ValueError):
                    reader(cursor, **kwargs)  # type: ignore[operator]

    def test_rerun_list_rejects_tampering_and_cross_study_claims(self) -> None:
        from trade_platform.research_terminal_read_model_v1 import read_reruns_v1

        study, trial = uuid4(), uuid4()
        identity = {"study_id": str(study), "candidate_set_hash": "b" * 64, "rerun_count": 1,
                    "authoritative_selection": {"status": "ESTABLISHED", "rule": {"metric": "total_return"},
                                                "selected": [{"rank": 1, "trial_id": str(trial), "metric_value": "0.1"}]}}
        row = (identity_hash_v1(identity), study, "fixture", "trend_ma_cross", "b" * 64,
               "ESTABLISHED", 1, identity, NOW)
        self.assert_refused(read_reruns_v1, row, {0: "0" * 64, 1: uuid4(), 4: "c" * 64,
                                                5: "FAIL", 6: 2, 7: {**identity, "rerun_count": 2}}, limit=10)

    def test_signal_list_rejects_tampering_and_cross_candidate_targets(self) -> None:
        from uuid import NAMESPACE_URL, uuid5

        from trade_platform.research_terminal_read_model_v1 import read_live_signals_v1

        study, trial = uuid4(), uuid4()
        identity = {"candidate": {"study_id": str(study), "trial_id": str(trial)}, "symbol": "BTCUSDT",
                    "authority": "RESEARCH_WATCH", "target_from": 0, "target_to": 1,
                    "evidence": {"bar_open_micros": int(NOW.timestamp()) * 1_000_000,
                                 "complete_market_knowledge_micros": int(NOW.timestamp()) * 1_000_000}}
        content_hash = identity_hash_v1(identity)
        signal_id = uuid5(uuid5(NAMESPACE_URL, "trade_platform.live_signals_v1"), f"live-signal:{content_hash}")
        row = (signal_id, "BTCUSDT", "RESEARCH_WATCH", 0, 1, NOW, NOW, study, trial, identity, content_hash)
        self.assert_refused(read_live_signals_v1, row, {1: "ETHUSDT", 2: "INCUBATING", 3: -1, 4: -1,
                                                      0: uuid4(), 5: NOW + timedelta(seconds=1),
                                                      6: NOW - timedelta(microseconds=1),
                                                      7: uuid4(), 8: uuid4(), 10: "0" * 64}, limit=10)

    def test_account_list_rejects_tampering_and_unearned_active_policy(self) -> None:
        from tests.test_paper_risk_assessment_v1 import PAPER
        from trade_platform.account_policy_v1 import AccountPolicyV1
        from trade_platform.research_terminal_read_model_v1 import read_accounts_v1

        policy = AccountPolicyV1(PAPER, {})
        row = (PAPER.account_id, PAPER.kind.value, PAPER.display_name, PAPER.base_currency,
               policy.policy_version_id, policy.status, policy.identity(), NOW, policy.content_hash)
        self.assert_refused(read_accounts_v1, row, {0: "other-account", 3: "USD", 4: uuid4(),
                                                   5: "ACTIVE", 8: "0" * 64})

    def test_walk_forward_hash_cannot_be_navigated_under_another_study(self) -> None:
        from trade_platform.research_terminal_read_model_v1 import read_walk_forward_v1

        study = uuid4()
        identity = {"study_id": str(study), "study_content_hash": "a" * 64,
                    "dataset": {"content_hash": "b" * 64}, "split_hash": "c" * 64,
                    "evaluation_class": "WALK_FORWARD_DEVELOPMENT", "numeric_tier": "SEARCH_NON_AUTHORITATIVE",
                    "cost_mode": "GROSS_NON_PROMOTABLE", "promotable": False, "rule": {},
                    "multiple_testing_trial_count": 1, "plan": {"evaluation_end_exclusive": NOW.isoformat()},
                    "folds": [], "authority_reasons": ["SEARCH_TIER"]}
        row = (identity_hash_v1(identity), study, "fixture", identity, NOW)
        self.assert_refused(read_walk_forward_v1, row, {0: "0" * 64, 1: uuid4()}, limit=10)


class TerminalHistoricalRegistryAcceptanceTests(unittest.TestCase):
    def test_dashboard_reads_guard_and_authorities_in_one_read_only_snapshot(self) -> None:
        from unittest.mock import MagicMock

        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries

        database = MagicMock()
        cursor = database.transaction.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        operation = Mock(return_value="fixture-evidence")
        queries = PostgresOperatorDashboardQueries(database)
        self.assertEqual("fixture-evidence", queries._read(operation))
        cursor.execute.assert_called_once_with("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        operation.assert_called_once_with(cursor)

    def test_later_registry_knowledge_refuses_before_any_current_authority_is_called(self) -> None:
        from unittest.mock import patch

        from trade_platform.research_terminal_read_model_v1 import _CycleContext

        cursor = Mock()
        cursor.fetchone.return_value = (NOW + timedelta(microseconds=1),)
        with (
            patch("trade_platform.paper_account_v1.cycle_cost_policy_v1") as cost,
            patch("trade_platform.paper_incubation_registry_v1.resolve_incubation_bindings_v1") as registry,
            self.assertRaisesRegex(ValueError, "^terminal_historical_registry_knowledge_unavailable$"),
        ):
            _CycleContext(cursor, NOW)
        cost.assert_not_called()
        registry.assert_not_called()
        self.assertEqual(1, cursor.execute.call_count)

    def test_metadata_boundary_is_inclusive_and_absent_rows_add_no_authority(self) -> None:
        from trade_platform.research_terminal_read_model_v1 import (
            _require_current_registry_knowledge,
        )

        for row in ((NOW,), (NOW - timedelta(microseconds=1),), (None,), None):
            with self.subTest(row=row):
                cursor = Mock()
                cursor.fetchone.return_value = row
                _require_current_registry_knowledge(cursor, "cycle-2026-08-20", NOW)
        with self.assertRaisesRegex(ValueError, "^terminal_as_of_must_be_timezone_aware$"):
            _require_current_registry_knowledge(Mock(), "cycle-2026-08-20", NOW.replace(tzinfo=None))

    def test_incubation_report_preserves_registry_refusal(self) -> None:
        from unittest.mock import patch

        from trade_platform.research_terminal_read_model_v1 import read_incubation_v1

        context = Mock(resolution_refusal="INCUBATION_REGISTRY_REFUSED:fixture-invalid")
        with patch("trade_platform.research_terminal_read_model_v1._CycleContext", return_value=context), \
                self.assertRaisesRegex(ValueError, "^INCUBATION_REGISTRY_REFUSED:fixture-invalid$"):
            read_incubation_v1(Mock(), now=NOW)


class RuntimeImageImportTests(unittest.TestCase):
    def test_configured_prices_are_explicitly_unavailable_without_analytical_dependencies(self) -> None:
        code = '''
import sys
for name in ('numpy', 'polars', 'duckdb', 'pyarrow'):
    sys.modules[name] = None
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock
from fastapi.testclient import TestClient
from tests.test_research_terminal_read_model_v1 import _paper
from trade_platform.api import build_app
from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v2
from trade_platform.research_terminal_read_model_v1 import _candidate_economics
from trade_platform.security import OperatorAuthenticator
from trade_platform.strategy_lab_study_v1 import identity_hash_v1
from trade_platform.terminal_paper_prices_v1 import TerminalPaperPricesV1
from uuid import uuid4
now = datetime.now(UTC)
paper = _paper(uuid4(), uuid4())
STUDY, TRIAL = str(paper.study_id), str(paper.trial_id)
GROSS = gross_cost_policy_v2()
entry = {"schema_version": "paper-incubation-fill-v1", "signal_id": str(uuid4()),
         "study_id": STUDY, "trial_id": TRIAL, "symbol": "BTCUSDT", "status": "FILLED",
         "target_from": 0, "target_to": 1, "previous_signal_id": None,
         "decided_at_micros": 1788220770000000, "fill_bar_open_micros": 1788220800000000,
         "fill_price": "100", "cost_policy_hash": GROSS.policy().content_hash,
         "evidence": {"session_id": "fixture-session"}}
selection = {"cycle_id": paper.cycle_id, "study_id": str(paper.study_id),
             "trial_id": str(paper.trial_id), "symbol": "BTCUSDT", "session_id": "fixture-session",
             "entry_fill_hash": identity_hash_v1(entry), "max_age_micros": 1000000,
             "capture_max_unproven_nanos": 0}
policy = {"schema_version": "terminal-paper-price-selection-v1", "policy_reference": "fixture-policy",
          "declared_at": now.isoformat(), "selections": [selection]}
prices = TerminalPaperPricesV1(policy, expected_hash=identity_hash_v1(policy),
                               research_root=Path("unused-research"), capture_root=Path("unused-capture"))
prices.installed_at = now
resolution = prices.resolve(Mock(), cycle_id=paper.cycle_id, study_id=str(paper.study_id),
                            trial_id=str(paper.trial_id), entry=entry, as_of=now)
assert resolution.payload() == {"status": "UNAVAILABLE", "mark": None, "evidence": None,
                                "reasons": ["PROVEN_PRICE_RUNTIME_DEPENDENCIES_UNAVAILABLE"]}
context = Mock(cycle=Mock(cost=GROSS, resolution=None, as_of=now, cycle_id=paper.cycle_id),
               refusal=None, cost_approval=None, prices=prices, cursor=Mock())
paper.economics = _candidate_economics(context, STUDY, TRIAL, [entry])
assert paper.economics.gross_unrealized.status == "UNAVAILABLE"
assert paper.economics.gross_unrealized.reasons == resolution.payload()["reasons"]
assert paper.economics.net.status == "NOT_COSTABLE"
queries = Mock()
queries.terminal_candidate_paper_evidence.return_value = paper
client = TestClient(build_app(authenticator=OperatorAuthenticator("fixture-token"),
                             operator_dashboard_queries=queries))
path = f"/operator-dashboard/research-terminal/candidates/{paper.study_id}/{paper.trial_id}/paper-evidence"
assert client.get(path).status_code == 401
response = client.get(path, headers={"Authorization": "Bearer fixture-token"})
assert response.status_code == 200, response.text
economics = response.json()["economics"]
assert economics["price_resolution"]["status"] == "UNAVAILABLE"
assert economics["price_resolution"]["reasons"] == resolution.payload()["reasons"]
assert economics["gross_unrealized"]["value"] is None
assert economics["net"]["status"] == "NOT_COSTABLE"
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30, check=False,
                                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
        self.assertEqual(0, result.returncode, result.stderr[-2000:])

    def test_the_api_imports_without_numerical_libraries(self) -> None:
        # The runtime image installs requirements-runtime.txt only: no numpy, polars or duckdb.
        code = ("import sys\n"
                "for name in ('numpy', 'polars', 'duckdb', 'pyarrow'): sys.modules[name] = None\n"
                "import trade_platform.api, trade_platform.research_terminal_read_model_v1\n"
                "import trade_platform.research_candidate_comparison_v1, trade_platform.paper_funding_evidence_v1\n"
                "import trade_platform.paper_incubation_report_v1\n"
                "import trade_platform.paper_economics_v1, trade_platform.paper_risk_assessment_v1\n"
                "import trade_platform.incubation_exit_criteria_v1, trade_platform.operator_dashboard\n")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False,
                                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
        self.assertEqual(0, result.returncode, result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
