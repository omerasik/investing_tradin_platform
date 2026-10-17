"""Frozen-candidate comparison: separate evidence dimensions, explicit missing values, no implied ranking."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from trade_platform.research_candidate_comparison_v1 import (
    RESTATED_CLAIMS_V1,
    CandidateComparisonError,
    ComparisonSortV1,
    SortDimensionV1,
    StudyComparisonEvidenceV1,
    compare_candidates_v1,
)
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

T2 = "T2_EVENT_TIME"


def _study_identity(dataset_hash: str, *, tier: str = T2, lag: int = 2_000_000) -> dict[str, Any]:
    return {
        "schema_version": "strategy-lab-study-v1",
        "strategy": {"family": "trend_ma_cross", "version": "1"},
        "parameter_space": [],
        "datasets": [{"role": "bars", "dataset_version_id": str(uuid4()), "content_hash": dataset_hash,
                      "evidence_tier": tier, "knowledge_upper_bound_exclusive": "2026-08-19T00:00:00+00:00"}],
        "search": {"mode": "EXHAUSTIVE", "max_trials": 4, "seed": None},
        "evaluation_upper_bound_exclusive": "2026-08-19T00:00:00+00:00",
        "numeric_policy_slot": "or3:x", "cost_policy_slot": "or6:x",
        "policies": {"cost": {"mode": "GROSS_NON_PROMOTABLE", "venue_fees": None},
                     "timing": {"baseline_dissemination_lag_micros": lag,
                                "claim_ceiling": "CONDITIONAL_NEVER_T3_OR_T4_PROFESSIONAL"}},
    }


def _evidence(metrics: dict[str, dict[str, Any]], *, dataset_hash: str = "d" * 64, rerun: str | None = None,
              decimal: dict[str, dict[str, Any]] | None = None, folds: list[dict[str, Any]] | None = None,
              states: list[dict[str, Any]] | None = None, top_k_metric: str = "total_return",
              denominator: int = 4) -> StudyComparisonEvidenceV1:
    """One study whose frozen set is ``metrics``' trials, in the given order (rank 1 first)."""
    study_identity = _study_identity(dataset_hash)
    study_hash = identity_hash_v1(study_identity)
    study_id = str(uuid4())
    trials = {name: str(uuid4()) for name in metrics}
    results = {trials[name]: {"outcome": "EVALUATED", "result_content_hash": f"{name:a<64}"[:64],
                              "metrics": dict(values)} for name, values in metrics.items()}
    cset = {
        "schema_version": "strategy-lab-candidate-set-v1", "study_id": study_id, "manifest_hash": "m" * 64,
        "rule": {"metric": top_k_metric, "direction": "HIGHER_IS_BETTER", "top_k": len(metrics),
                 "tie_break": "TRIAL_CONTENT_HASH_ASCENDING"},
        "candidates": [{"rank": rank, "trial_id": trials[name], "trial_content_hash": f"{name:c<64}"[:64],
                        "result_content_hash": results[trials[name]]["result_content_hash"],
                        "metric_value": str(values.get(top_k_metric, "0"))}
                       for rank, (name, values) in enumerate(metrics.items(), start=1)],
        "eligible_count": len(metrics), "ineligible_count": 0, "cutoff_tie": False,
        "multiple_testing_trial_count": denominator, "numeric_tier": "SEARCH_NON_AUTHORITATIVE",
        "authoritative_rerun": "REQUIRED_DECIMAL_RERUN_OR_3",
    }
    set_hash = identity_hash_v1(cset)
    reruns = []
    if rerun is not None:
        lags = [2_000_000, 5_000_000, 30_000_000, 60_000_000]
        results_list = [{"trial_id": trials[name], "lag_micros": lag, "reconciliation": "RECONCILED",
                         "held_divergence_bars": 0, "trade_count_divergence": 0,
                         "metrics": {"cost_mode": "GROSS_NON_PROMOTABLE", **values}}
                        for name, values in (decimal or {}).items() for lag in lags[:3]]  # 60 s sweep absent
        identity = {
            "schema_version": "strategy-lab-authority-rerun-v1", "study_id": study_id,
            "study_content_hash": study_hash, "manifest_hash": "m" * 64, "candidate_set_hash": set_hash,
            "baseline_lag_micros": 2_000_000, "sweep_lags_micros": lags[1:], "results": results_list,
            "authoritative_selection": {"status": rerun, "selected": [
                {"rank": 1, "trial_id": trials[next(iter(metrics))], "metric_value": "1"}],
                "changed_versus_search_selection": False},
            "claim_ceiling": "CONDITIONAL_T2_GROSS_NOT_PROMOTED",
        }
        reruns.append((identity_hash_v1(identity), identity))
    reports = []
    if folds is not None:
        resolved = [{**fold, "selected": None if fold["selected"] is None else
                     {**fold["selected"], "trial_id": trials[fold["selected"]["trial_id"]]}} for fold in folds]
        identity = {"study_content_hash": study_hash, "split_hash": "s" * 64, "dataset": {"content_hash": dataset_hash},
                    "rule": {"metric": "total_return", "direction": "HIGHER_IS_BETTER"},
                    "plan": {"evaluation_end_exclusive": "2026-08-01T00:00:00+00:00"},
                    "multiple_testing_trial_count": denominator, "folds": resolved}
        reports.append((identity_hash_v1(identity), identity))
    return StudyComparisonEvidenceV1(
        study_id=study_id, study_content_hash=study_hash, strategy_family="trend_ma_cross", label="fixture",
        study_identity=study_identity, candidate_set_hash=set_hash, candidate_set=cset,
        trial_parameters={trials[name]: {"fast": "5"} for name in metrics}, search_results=results,
        reruns=reruns, walk_forward=reports,
        candidate_states=[{**s, "trial_id": trials[s["trial_id"]]} for s in states or []])


def _fold(n: int, trial: str | None, total_return: str | None) -> dict[str, Any]:
    return {"fold": n, "test_start": f"2026-0{n}-01", "test_end_exclusive": f"2026-0{n}-15", "flags": [],
            "selection_tie": False,
            "selected": None if trial is None else {"trial_id": trial, "train_metric_value": "0.1",
                                                    "test_metrics": {"total_return": total_return, "trades": 3}}}


class CandidateComparisonTests(unittest.TestCase):
    def test_dimensions_stay_separate_and_every_row_restates_its_claims(self) -> None:
        evidence = _evidence({"a": {"total_return": "0.5", "trades": 10}, "b": {"total_return": "0.2"}})
        result = compare_candidates_v1([evidence], sort=None)
        self.assertEqual(2, len(result.rows))
        self.assertEqual("NONE", result.composite_score)
        self.assertEqual("NOT_READ", result.holdout)
        for row in result.rows:
            self.assertEqual(list(RESTATED_CLAIMS_V1), row.restated_claims)
            self.assertIs(False, row.promotable)
            self.assertEqual("NONE", row.execution_authority)
            self.assertEqual("SEARCH_NON_AUTHORITATIVE", row.search.claim)
            self.assertEqual("NO_DECIMAL_RERUN_RECORDED", row.decimal_rerun.claim)
            self.assertEqual("NO_WALK_FORWARD_REPORT_RECORDED", row.walk_forward.claim)
            self.assertEqual(("GROSS_ONLY", "UNAVAILABLE", "UNAVAILABLE"),
                             (row.economics.completeness, row.economics.verified_costs,
                              row.economics.net_profitability))
            self.assertEqual("CONDITIONAL", row.provenance.datasets[0].tier_claim)
            self.assertIs(True, row.provenance.evaluation_before_holdout)
            self.assertIn("COST_POLICY_GROSS_NON_PROMOTABLE_NO_VERIFIED_FEE_SCHEDULE", row.authority_reasons)
            self.assertEqual("UNSORTED", row.sort_state)
        # Identities are preserved verbatim.
        self.assertEqual((evidence.study_id, evidence.study_content_hash, evidence.candidate_set_hash),
                         (result.rows[0].identity.study_id, result.rows[0].identity.study_content_hash,
                          result.rows[0].identity.candidate_set_hash))
        # A metric not recorded for a candidate is MISSING, never zero.
        trades_b = next(m for m in result.rows[1].search.metrics if m.key == "trades")
        self.assertEqual(("MISSING", None), (trades_b.state, trades_b.value))

    def test_sort_is_one_recorded_metric_with_explicit_ties_and_missing_values(self) -> None:
        evidence = _evidence({"a": {"total_return": "0.20"}, "b": {"total_return": "0.5"},
                              "c": {"total_return": "0.2"}, "d": {}, "e": {"total_return": "n/a"}})
        result = compare_candidates_v1(
            [evidence], sort=ComparisonSortV1(SortDimensionV1.SEARCH, "total_return", "DESC"))
        order = [(row.identity.frozen_rank, row.sort_state, row.sort_position, row.tied_with) for row in result.rows]
        self.assertEqual([(2, "VALUE", 1, 0), (1, "VALUE", 2, 1), (3, "VALUE", 2, 1),
                          (4, "MISSING", None, 0), (5, "NOT_NUMERIC", None, 0)], order)
        self.assertEqual("0.20", result.rows[1].sort_value)  # verbatim, compared exactly as Decimal
        self.assertEqual({"VALUE": 3, "MISSING": 1, "NOT_NUMERIC": 1}, result.sort_counts)
        ascending = compare_candidates_v1(
            [evidence], sort=ComparisonSortV1(SortDimensionV1.SEARCH, "total_return", "ASC"))
        self.assertEqual([1, 3, 2, 4, 5], [row.identity.frozen_rank for row in ascending.rows])

    def test_sort_requires_an_explicit_well_formed_choice(self) -> None:
        for args in ((SortDimensionV1.SEARCH, "total_return", "desc"), (SortDimensionV1.SEARCH, "Total-Return", "ASC"),
                     ("SEARCH", "total_return", "ASC")):
            with self.subTest(args=args), self.assertRaises(CandidateComparisonError):
                ComparisonSortV1(*args)  # type: ignore[arg-type]

    def test_decimal_rerun_is_restated_with_missing_sweep_lags_and_fail_closed_selection(self) -> None:
        established = _evidence({"a": {"total_return": "1"}, "b": {"total_return": "0.5"}}, rerun="ESTABLISHED",
                                decimal={"a": {"total_return": "0.9"}})
        rows = compare_candidates_v1(
            [established], sort=ComparisonSortV1(SortDimensionV1.DECIMAL_BASELINE, "total_return", "DESC")).rows
        a, b = rows
        self.assertEqual(("DECIMAL_SELECTION_ESTABLISHED", True, 1), (a.decimal_rerun.claim,
                                                                     a.decimal_rerun.in_decimal_selection,
                                                                     a.decimal_rerun.decimal_rank))
        self.assertEqual(2_000_000, a.decimal_rerun.baseline.lag_micros)  # type: ignore[union-attr]
        self.assertEqual([5_000_000, 30_000_000], [lag.lag_micros for lag in a.decimal_rerun.sweep])
        self.assertEqual([60_000_000], a.decimal_rerun.sweep_lags_missing)
        self.assertEqual("GROSS_NON_PROMOTABLE", a.economics.decimal_cost_mode.value)
        self.assertIn("FUNDING_NOT_CHARGED_IN_RECORDED_RERUN", a.economics.reasons)
        # b has no Decimal result: no baseline, sorts as MISSING, not in the selection.
        self.assertEqual((None, False, "MISSING"), (b.decimal_rerun.baseline, b.decimal_rerun.in_decimal_selection,
                                                    b.sort_state))
        failed = _evidence({"a": {"total_return": "1"}}, rerun="FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED",
                           decimal={"a": {"total_return": "0.9"}})
        (row,) = compare_candidates_v1([failed], sort=None).rows
        self.assertEqual(("FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED", None, None),
                         (row.decimal_rerun.claim, row.decimal_rerun.in_decimal_selection,
                          row.decimal_rerun.decimal_rank))

    def test_walk_forward_dispersion_is_facts_over_this_candidates_folds_only(self) -> None:
        folds = [_fold(1, "a", "0.10"), _fold(2, "a", "-0.025"), _fold(3, "b", "0.9"), _fold(4, None, None),
                 _fold(5, "a", None)]
        rows = compare_candidates_v1([_evidence({"a": {}, "b": {}}, folds=folds)], sort=None).rows
        (report,) = rows[0].walk_forward.reports
        self.assertEqual("WALK_FORWARD_DEVELOPMENT", rows[0].walk_forward.claim)
        self.assertEqual(([1, 2, 5], 1, 1, 5), (report.folds_selected_this_candidate,
                                                report.folds_excluded_no_eligible_trial,
                                                report.folds_selected_other_trial, report.fold_count))
        dispersion = {d.metric: d for d in report.dispersion}["total_return"]
        self.assertEqual((2, 1, "-0.025", "0.1", "-0.025", "0.125", 1, 0, 1),
                         (dispersion.folds_with_value, dispersion.folds_missing, dispersion.min, dispersion.max,
                          dispersion.median_nearest_rank, dispersion.spread, dispersion.positive, dispersion.zero,
                          dispersion.negative))
        missing = next(m for m in report.candidate_folds[2].metrics if m.key == "total_return")
        self.assertEqual("MISSING", missing.state)
        (b_report,) = rows[1].walk_forward.reports
        b_dispersion = {d.metric: d for d in b_report.dispersion}["total_return"]
        self.assertEqual((1, None), (b_dispersion.folds_with_value, b_dispersion.spread))  # one value: no spread

    def test_stored_evidence_must_rederive_and_bind_to_the_same_study(self) -> None:
        base = _evidence({"a": {"total_return": "1"}}, rerun="ESTABLISHED", decimal={"a": {}},
                         folds=[_fold(1, "a", "0.1")])
        tampered_set = {**base.candidate_set, "cutoff_tie": True}
        other = _evidence({"a": {"total_return": "1"}}, rerun="ESTABLISHED", decimal={"a": {}},
                          folds=[_fold(1, "a", "0.1")])
        trial = next(iter(base.search_results))
        cases = {
            "candidate_set": {"candidate_set": tampered_set},
            "rerun_of_other_set": {"reruns": other.reruns},
            "walk_forward_of_other_study": {"walk_forward": other.walk_forward},
            "search_result_hash": {"search_results": {trial: {**base.search_results[trial],
                                                              "result_content_hash": "0" * 64}}},
            "study_identity": {"study_identity": {**base.study_identity, "label": "x"}},
        }
        for name, change in cases.items():
            values = {field: getattr(base, field) for field in base.__dataclass_fields__}
            values.update(change)
            with self.subTest(name), self.assertRaises(CandidateComparisonError):
                compare_candidates_v1([StudyComparisonEvidenceV1(**values)], sort=None)

    def test_output_is_deterministic_float_free_and_states_cross_study_differences(self) -> None:
        one = _evidence({"a": {"total_return": "0.1"}}, states=[{"trial_id": "a", "state": "INCUBATING",
                                                                 "evidence_hash": "e" * 64, "reasons": []}])
        two = _evidence({"x": {"total_return": "0.3"}}, dataset_hash="f" * 64, denominator=9)
        sort = ComparisonSortV1(SortDimensionV1.SEARCH, "total_return", "DESC")
        first = compare_candidates_v1([one, two], sort=sort)
        self.assertEqual(first.output_hash, compare_candidates_v1([one, two], sort=sort).output_hash)
        self.assertNotEqual(first.output_hash, compare_candidates_v1([one, two], sort=None).output_hash)
        self.assertEqual(identity_hash_v1({k: v for k, v in first.payload().items() if k != "output_hash"}),
                         first.output_hash)
        json.dumps(first.payload(), allow_nan=False)
        self.assertNotIn("float", json.dumps(first.payload()))
        self.assertEqual(["DIFFERENT_BOUND_DATASETS", "DIFFERENT_MULTIPLE_TESTING_DENOMINATORS"],
                         first.cross_study.differences)
        by_study = {row.identity.study_id: row for row in first.rows}
        self.assertEqual("INCUBATING", by_study[one.study_id].candidate_state.state)  # type: ignore[union-attr]
        self.assertIsNone(by_study[two.study_id].candidate_state)
        with self.assertRaises(CandidateComparisonError):
            compare_candidates_v1([one, one], sort=None)

    def test_the_module_imports_without_numerical_libraries(self) -> None:
        code = ("import sys\n"
                "for name in ('numpy', 'polars', 'duckdb', 'pyarrow'): sys.modules[name] = None\n"
                "import trade_platform.research_candidate_comparison_v1\n")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False,
                                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
        self.assertEqual(0, result.returncode, result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
