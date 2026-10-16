from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import UUID, uuid4

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
    _TrialContext,
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


class _StubCursor:
    """Answers the three _TrialContext reads by table name."""

    def __init__(self, rows: dict[str, list[tuple[object, ...]]]) -> None:
        self.rows = rows
        self.pending: list[tuple[object, ...]] = []

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.pending = next(rows for table, rows in self.rows.items() if f"FROM {table}" in query)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.pending

    def fetchone(self) -> tuple[object, ...] | None:
        return self.pending[0] if self.pending else None


class StrategyLabTrialRerunBindingTests(unittest.TestCase):
    def _set(self, set_hash: str, trial: str, at: datetime) -> tuple[object, ...]:
        payload = {"rule": {"metric": "m"}, "candidates": [{"rank": 1, "trial_id": trial, "trial_content_hash": "a" * 64,
                                                             "result_content_hash": "b" * 64, "metric_value": "1"}],
                   "eligible_count": 1, "ineligible_count": 0, "cutoff_tie": False, "multiple_testing_trial_count": 1,
                   "numeric_tier": "SEARCH_NON_AUTHORITATIVE", "authoritative_rerun": "REQUIRED_DECIMAL_RERUN_OR_3"}
        return (set_hash, "c" * 64, json.dumps(payload), at)

    def _rerun(self, rerun_hash: str, set_hash: str, status: str, trial: str, at: datetime) -> tuple[object, ...]:
        identity = {"results": [{"trial_id": trial, "lag_micros": 2000000, "reconciliation": "RECONCILED"}],
                    "authoritative_selection": {"selected": [{"rank": 1, "trial_id": trial, "metric_value": "1.0"}]}}
        return (rerun_hash, set_hash, status, 1, json.dumps(identity), at)

    def test_trial_reruns_bind_to_the_displayed_set_and_fail_closed(self) -> None:
        trial = str(uuid4())
        old, new = "1" * 64, "2" * 64
        cursor = _StubCursor({
            "strategy_lab_candidate_sets": [self._set(old, trial, NOW), self._set(new, trial, NOW.replace(day=4))],
            # The newest rerun is of the OLD set; it must not be joined onto the latest set's trials.
            "strategy_lab_authority_reruns": [
                self._rerun("e" * 64, new, "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED", trial, NOW.replace(day=5)),
                self._rerun("f" * 64, old, "ESTABLISHED", trial, NOW.replace(day=6)),
            ],
            "strategy_lab_candidate_states": [],
        })
        rerun = _TrialContext(cursor, uuid4()).rerun(UUID(trial))
        assert rerun is not None
        self.assertEqual(rerun.rerun_hash, "e" * 64)
        self.assertEqual(rerun.selection_status, "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED")
        # An unestablished selection never yields a Decimal rank, even if the record lists one.
        self.assertIsNone(rerun.decimal_rank)
        self.assertIsNone(rerun.decimal_metric_value)


class _OrderedStubCursor:
    """Answers each read with the rows of the first marker found in the query text."""

    def __init__(self, rows: list[tuple[str, list[tuple[object, ...]]]]) -> None:
        self.rows = rows
        self.pending: list[tuple[object, ...]] = []
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.queries.append((query, params))
        self.pending = next(rows for marker, rows in self.rows if marker in query)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.pending

    def fetchone(self) -> tuple[object, ...] | None:
        return self.pending[0] if self.pending else None


_SPACE = [{"kind": "INTEGER", "name": "fast", "values": ["1", "2", "3"]},
          {"kind": "CATEGORICAL", "name": "mode", "values": ["p", "q", "r"]},
          {"kind": "DECIMAL", "name": "band", "values": ["0", "0.5"]}]
_GROSS_POLICY = {"cost": {"mode": "GROSS_NON_PROMOTABLE", "venue_fees": None, "slippage_scenarios": [],
                          "gross_search_reports": "BREAK_EVEN_COST_BPS_PER_SIDE"}}


def _analytics_cursor(trial_rows: list[tuple[object, ...]], *, policies: object = None) -> _OrderedStubCursor:
    identity = {"parameter_space": _SPACE, "datasets": [], **({"policies": policies} if policies else {})}
    t1, t2 = uuid4(), uuid4()
    return _OrderedStubCursor([
        ("FROM strategy_lab_studies", [("a" * 64, json.dumps(identity))]),
        ("FROM strategy_lab_study_manifests", [("m" * 64,)]),
        ("FROM strategy_lab_trial_results WHERE", [(str(t2), "r" * 64), (str(t1), "s" * 64)]),
        ("count(*) FROM strategy_lab_trials", [(len(trial_rows),)]),
        ("LEFT JOIN strategy_lab_trial_queue", trial_rows),
        ("FROM strategy_lab_candidate_sets", []),
        ("FROM strategy_lab_authority_reruns", []),
        ("FROM strategy_lab_candidate_states", []),
    ])


class StrategyLabHeatmapTests(unittest.TestCase):
    def _read(self, rows: list[tuple[object, ...]], **selection: object):  # type: ignore[no-untyped-def]
        from trade_platform.strategy_lab_read_model_v1 import read_strategy_lab_heatmap_v1

        selection = {"x": "fast", "y": "mode", "metric": "total_return", "fixed": {"band": "0.5"}, **selection}
        cursor = _analytics_cursor(rows)
        return read_strategy_lab_heatmap_v1(cursor, uuid4(), **selection), cursor  # type: ignore[arg-type]

    def test_cells_restate_each_stored_trial_and_never_aggregate(self) -> None:
        a, b, c, d, e, f = (uuid4() for _ in range(6))
        rows = [  # trial_id, ordinal, x, y, outcome, metric text, reason, queue state
            (a, 0, "1", "p", "EVALUATED", "0.50", None, "SUCCEEDED"),
            (b, 1, "2", "p", "EVALUATED", "-0.25", None, "SUCCEEDED"),
            (c, 2, "3", "p", None, None, None, "FAILED"),
            (d, 3, "1", "q", "INADMISSIBLE_PARAMETERS", None, "fast_not_shorter_than_slow", "SUCCEEDED"),
            (e, 4, "2", "q", "EVALUATED", "0.5", None, "SUCCEEDED"),  # ties "0.50" exactly
            (f, 5, "2", "q", "EVALUATED", "9", None, "SUCCEEDED"),    # a second trial in one cell
            (uuid4(), 6, "3", "q", "EVALUATED", None, None, "SUCCEEDED"),
            (uuid4(), 7, "1", "r", None, None, None, "CLAIMED"),
            (uuid4(), 8, "2", "r", None, None, None, "CANCELLED"),
        ]
        view, cursor = self._read(rows)
        self.assertEqual(view.state, "AVAILABLE")
        self.assertEqual((view.numeric_tier, view.economics), ("SEARCH_NON_AUTHORITATIVE", "GROSS_NON_PROMOTABLE"))
        cells = {(cell.x, cell.y): cell for cell in view.cells}
        self.assertEqual([(cell.x, cell.y) for cell in view.cells][:4], [("1", "p"), ("2", "p"), ("3", "p"), ("1", "q")])
        self.assertEqual((cells["1", "p"].state, cells["1", "p"].value, cells["1", "p"].sign), ("VALUE", "0.50", 1))
        self.assertEqual((cells["2", "p"].value, cells["2", "p"].sign, cells["2", "p"].rank), ("-0.25", -1, 1))
        self.assertEqual(cells["1", "p"].rank, 2)
        self.assertEqual(cells["1", "p"].trial_ids, [a])
        self.assertEqual(cells["3", "p"].state, "FAILED")
        self.assertEqual((cells["1", "q"].state, cells["1", "q"].reason),
                         ("INADMISSIBLE_PARAMETERS", "fast_not_shorter_than_slow"))
        self.assertEqual((cells["2", "q"].state, cells["2", "q"].value, cells["2", "q"].trial_ids),
                         ("AMBIGUOUS", None, [e, f]))
        self.assertEqual(cells["3", "q"].state, "METRIC_UNAVAILABLE")
        self.assertEqual((cells["1", "r"].state, cells["2", "r"].state, cells["3", "r"].state),
                         ("PENDING", "CANCELLED", "NO_TRIAL"))
        self.assertEqual(view.counts, {"VALUE": 2, "METRIC_UNAVAILABLE": 1, "INADMISSIBLE_PARAMETERS": 1, "FAILED": 1,
                                       "CANCELLED": 1, "PENDING": 1, "NO_TRIAL": 1, "AMBIGUOUS": 1})
        self.assertEqual(view.distinct_values, 2)
        # Every remaining parameter is bound in SQL by exact text, the axes and metric by name.
        query, params = cursor.queries[-1]
        self.assertIn("t.parameters->>%s = %s", query)
        self.assertEqual(params[:3], ("fast", "mode", "total_return"))
        self.assertEqual(params[-2:], ("band", "0.5"))
        self.assertEqual(view.evidence.manifest_hash, "m" * 64)
        self.assertEqual(view.evidence.result_count, 2)

    def test_ranks_follow_exact_decimal_order_not_text_or_float(self) -> None:
        rows = [(uuid4(), i, x, "p", "EVALUATED", text, None, "SUCCEEDED")
                for i, (x, text) in enumerate([("1", "10"), ("2", "9.99999999999999999999"), ("3", "1E+1")])]
        view, _ = self._read(rows)
        ranks = {cell.x: cell.rank for cell in view.cells if cell.y == "p"}
        self.assertEqual(ranks, {"1": 2, "2": 1, "3": 2})  # "10" == "1E+1" exactly; no float rounding to 10

    def test_unfixed_or_invalid_selections_fail_closed_without_cells(self) -> None:
        view, cursor = self._read([], fixed={})
        self.assertEqual((view.state, view.cells), ("SELECTION_REQUIRED", []))
        self.assertEqual(view.reasons, ["FIXED_VALUE_REQUIRED:band"])
        self.assertEqual(view.required_fixed, ["band"])
        self.assertFalse(any("LEFT JOIN strategy_lab_trial_queue" in q for q, _ in cursor.queries))
        for selection, reason in (
            ({"fixed": {"band": "0.50"}}, "FIXED_VALUE_NOT_RECORDED:band"),  # no numeric coercion
            ({"fixed": {"band": "0.5", "fast": "1"}}, "FIXED_PARAMETER_IS_AN_AXIS:fast"),
            ({"fixed": {"band": "0.5", "lag": "1"}}, "FIXED_PARAMETER_NOT_RECORDED:lag"),
            ({"y": "fast"}, "X_AND_Y_MUST_DIFFER"),
            ({"x": "slow"}, "X_PARAMETER_NOT_RECORDED"),
            ({"metric": "Total-Return"}, "METRIC_KEY_MALFORMED"),
        ):
            with self.subTest(reason=reason):
                view, _ = self._read([], **selection)
                self.assertEqual(view.state, "SELECTION_INVALID")
                self.assertIn(reason, view.reasons)
                self.assertEqual(view.cells, [])
        view, _ = self._read([], x=None, metric=None)
        self.assertEqual(view.state, "SELECTION_REQUIRED")
        self.assertIn("X_PARAMETER_REQUIRED", view.reasons)
        self.assertIn("METRIC_REQUIRED", view.reasons)


class StrategyLabBreakEvenDistributionTests(unittest.TestCase):
    def _rows(self) -> list[tuple[object, ...]]:
        values = ["-3", "-0.5", "0", "0.0", "2", "10", None, "NaN"]
        rows: list[tuple[object, ...]] = [("EVALUATED", "SUCCEEDED", v) for v in values]
        return rows + [("INADMISSIBLE_PARAMETERS", "SUCCEEDED", None), (None, "FAILED", None),
                       (None, "PENDING", None), (None, "CANCELLED", None)]

    def test_counts_split_exactly_by_sign_over_every_evaluated_trial(self) -> None:
        from trade_platform.strategy_lab_read_model_v1 import (
            read_strategy_lab_break_even_distribution_v1,
        )

        view = read_strategy_lab_break_even_distribution_v1(_analytics_cursor(self._rows(), policies=_GROSS_POLICY),
                                                            uuid4())
        self.assertEqual(view.state, "AVAILABLE")
        self.assertEqual((view.claim, view.verified_costs, view.net_profitability),
                         ("GROSS_RESULT_COST_SENSITIVITY", "UNAVAILABLE", "UNAVAILABLE"))
        self.assertEqual(view.trial_outcomes, {"CANCELLED": 1, "EVALUATED": 8, "FAILED": 1,
                                               "INADMISSIBLE_PARAMETERS": 1, "PENDING": 1})
        self.assertEqual((view.eligible_count, view.positive, view.zero, view.negative, view.unavailable),
                         (8, 2, 2, 2, 2))
        negative = [b for b in view.bins if b.side == "NEGATIVE"]
        positive = [b for b in view.bins if b.side == "POSITIVE"]
        self.assertEqual((len(negative), len(positive)), (10, 10))
        self.assertEqual((negative[0].lower, negative[0].upper, negative[-1].upper), ("-3", "-2.7", "0"))
        self.assertEqual([b.count for b in negative], [1, 0, 0, 0, 0, 0, 0, 0, 1, 0])  # -0.5 in [-0.6, -0.3)
        self.assertEqual((positive[0].lower, positive[0].upper, positive[-1].upper), ("0", "1", "10"))
        self.assertEqual([b.count for b in positive], [0, 1, 0, 0, 0, 0, 0, 0, 0, 1])  # 2 in (1, 2]
        self.assertTrue(all(b.lower_inclusive and not b.upper_inclusive for b in negative))
        self.assertTrue(all(b.upper_inclusive and not b.lower_inclusive for b in positive))
        self.assertEqual(sum(b.count for b in view.bins), view.positive + view.negative)
        self.assertEqual(view.order_statistics, {"min": "-3", "p25": "-0.5", "median": "0", "p75": "2", "max": "10"})

    def test_a_study_without_break_even_reporting_is_unavailable_not_zero(self) -> None:
        from trade_platform.strategy_lab_read_model_v1 import (
            read_strategy_lab_break_even_distribution_v1,
        )

        view = read_strategy_lab_break_even_distribution_v1(_analytics_cursor(self._rows()), uuid4())
        self.assertEqual(view.state, "UNAVAILABLE")
        self.assertEqual(view.reasons, ["STUDY_COST_POLICY_DOES_NOT_REPORT_BREAK_EVEN"])
        self.assertEqual((view.positive, view.zero, view.negative, view.bins, view.order_statistics), (0, 0, 0, [], {}))
        self.assertEqual(view.trial_outcomes["EVALUATED"], 8)  # the recorded outcomes are still restated

    def test_the_view_cannot_claim_a_verified_cost(self) -> None:
        from trade_platform.strategy_lab_read_model_v1 import (
            read_strategy_lab_break_even_distribution_v1,
        )

        view = read_strategy_lab_break_even_distribution_v1(_analytics_cursor([], policies=_GROSS_POLICY), uuid4())
        self.assertEqual((view.state, view.reasons), ("UNAVAILABLE", ["NO_EVALUATED_TRIAL"]))
        for field, value in (("claim", "VERIFIED_COST"), ("verified_costs", "AVAILABLE"),
                             ("net_profitability", "AVAILABLE"), ("numeric_tier", "DECIMAL")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                type(view)(**{**view.model_dump(), field: value})


class StrategyLabAnalyticsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        from trade_platform.strategy_lab_read_model_v1 import (
            StrategyLabBreakEvenDistributionView,
            StrategyLabEvidenceIdentityView,
            StrategyLabHeatmapView,
        )

        self.queries = Mock()
        self.study_id = uuid4()
        evidence = StrategyLabEvidenceIdentityView(study_id=self.study_id, study_content_hash="a" * 64,
                                                   manifest_hash=None, results_digest="b" * 64, result_count=0,
                                                   trial_count=0)
        self.queries.strategy_lab_heatmap.return_value = StrategyLabHeatmapView(
            state="SELECTION_REQUIRED", evidence=evidence, dimensions=[], x="fast", y="slow", metric="trades",
            fixed={}, required_fixed=["band"], reasons=["FIXED_VALUE_REQUIRED:band"])
        self.queries.strategy_lab_break_even_distribution.return_value = StrategyLabBreakEvenDistributionView(
            state="UNAVAILABLE", evidence=evidence, reasons=["NO_EVALUATED_TRIAL"], trial_outcomes={},
            eligible_count=0, positive=0, zero=0, negative=0, unavailable=0, bins=[], order_statistics={},
            selection=[])
        self.client = TestClient(build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"),
            InMemoryRateLimiter(max_requests=100), operator_dashboard_queries=self.queries,
        ))
        self.headers = {"Authorization": "Bearer test-token"}

    def test_analytics_reads_are_protected_get_only_and_validated(self) -> None:
        base = f"/operator-dashboard/strategy-lab/studies/{self.study_id}"
        heatmap = f"{base}/heatmap?x=fast&y=slow&metric=trades&fixed=band:0.0005&fixed=direction:long_only"
        for path in (heatmap, f"{base}/break-even-distribution"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
                response = self.client.get(path, headers=self.headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["numeric_tier"], "SEARCH_NON_AUTHORITATIVE")
                for method in ("post", "put", "patch", "delete"):
                    self.assertEqual(getattr(self.client, method)(path.split("?")[0], headers=self.headers).status_code,
                                     405)
        self.queries.strategy_lab_heatmap.assert_called_with(
            self.study_id, x="fast", y="slow", metric="trades", fixed={"band": "0.0005", "direction": "long_only"})
        self.assertEqual(self.client.get(f"{base}/break-even-distribution", headers=self.headers).json()["claim"],
                         "GROSS_RESULT_COST_SENSITIVITY")
        for bad in ("?x=Fast", "?metric=a;b", "?fixed=band", "?fixed=band:1&fixed=band:2", "?fixed=band:1%20or%201",
                    "?fixed=Band:1"):
            with self.subTest(bad=bad):
                self.assertEqual(self.client.get(f"{base}/heatmap{bad}", headers=self.headers).status_code, 422)
        self.queries.strategy_lab_break_even_distribution.side_effect = DashboardObjectNotFound("x")
        self.assertEqual(self.client.get(f"{base}/break-even-distribution", headers=self.headers).status_code, 404)


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
