"""UI-2 acceptance, offline -- each terminal handoff keeps its identity and fails closed on missing evidence.

The real-table path (search -> rerun -> terminal API) is
``tests.test_research_terminal_e2e_acceptance_postgres``; this module pins the
handoffs and refusals that need no database: market data -> walk-forward ->
terminal view, risk preview, readiness, incubation report and runner liveness.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.test_candidate_lifecycle_readiness_v1 import AS_OF, GROSS, chain, run
from tests.test_runner_liveness_v1 import BOUNDS, acceptance
from tests.test_strategy_lab_walk_forward_v1 import RULE, SPACE, _plan
from trade_platform.account_policy_v1 import (
    AccountContextV1,
    AccountKindV1,
    AccountPolicyError,
    AccountPolicyV1,
)
from trade_platform.candidate_lifecycle_readiness_v1 import (
    NEED_FEE_SCHEDULE,
    NEED_FORWARD_FUNDING,
    NEED_LIVENESS,
    NEED_REGISTRY_BINDING,
    STAGE_ELIGIBLE,
    STAGE_FORWARD_EVIDENCE_INCOMPLETE,
    assess_candidate_readiness_v1,
)
from trade_platform.paper_incubation_report_v1 import (
    UNRESOLVED_NO_BINDING,
    UNRESOLVED_OR7_INCUBATION_DAYS,
    incubation_report_v1,
)
from trade_platform.research_terminal_read_model_v1 import WalkForwardView, read_walk_forward_v1
from trade_platform.runner_liveness_v1 import derive_runner_liveness_v1
from trade_platform.strategy_lab_splits_v1 import StrategyLabSplitError
from trade_platform.strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1
from trade_platform.strategy_lab_walk_forward_v1 import run_walk_forward_v1
from trade_platform.strategy_sdk_v1 import load_bars_v1

NOW = datetime(2026, 10, 1, tzinfo=UTC)


class _Cursor:
    """Serves stored ``strategy_lab_walk_forward_reports`` rows as the read model queries them."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        assert query.lstrip().upper().startswith("SELECT")  # the terminal never writes

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None


class MarketDataToWalkForwardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.test_strategy_lab_e2e_fixture import build_window_and_study

        cls.temp = Path(tempfile.mkdtemp(prefix="ui2-acceptance-"))
        cls.study, data_root = build_window_and_study(cls.temp, family="trend_ma_cross", space=SPACE)
        (cls.binding,) = cls.study.datasets
        cls.bars = load_bars_v1(data_root, cls.binding.dataset_version_id,
                                expected_content_hash=cls.binding.content_hash)
        cls.report = run_walk_forward_v1(cls.study, _plan(), RULE, bars=cls.bars)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def _row(self, identity: dict[str, Any], report_hash: str | None = None) -> tuple[Any, ...]:
        return (report_hash or self.report.report_hash, self.study.study_id, "ui2 fixture", identity, NOW)

    def test_the_terminal_view_carries_the_study_and_dataset_identities(self) -> None:
        (view,) = read_walk_forward_v1(_Cursor([self._row(dict(self.report.identity))]), limit=10)
        self.assertEqual(self.report.report_hash, view.report_hash)
        self.assertEqual((self.study.study_id, self.study.content_hash, self.binding.content_hash),
                         (view.study_id, view.study_content_hash, view.dataset_content_hash))
        self.assertEqual(("WALK_FORWARD_DEVELOPMENT", "SEARCH_NON_AUTHORITATIVE", "GROSS_NON_PROMOTABLE", False),
                         (view.evaluation_class, view.numeric_tier, view.cost_mode, view.promotable))
        self.assertIn("WALK_FORWARD_DEVELOPMENT_IS_NOT_HOLDOUT_VALIDATION", view.authority_reasons)
        self.assertEqual([fold["fold"] for fold in self.report.folds], [fold.fold for fold in view.folds])
        trials = {trial.trial_id for trial in self.study.trials()}
        for fold, stored in zip(view.folds, self.report.folds, strict=True):
            self.assertIn(fold.selected_trial_id, trials)
            self.assertEqual(stored["selected"]["test_metrics"], fold.test_metrics)
        self.assertLessEqual(view.evaluation_end_exclusive, UNTOUCHED_HOLDOUT_BOUNDARY_V1)

    def test_a_tampered_stored_report_is_refused(self) -> None:
        forged = {**dict(self.report.identity), "numeric_tier": "DECIMAL_AUTHORITATIVE"}
        with self.assertRaisesRegex(ValueError, "stored_walk_forward_report_does_not_rederive"):
            read_walk_forward_v1(_Cursor([self._row(forged)]), limit=10)

    def test_the_view_cannot_express_a_higher_claim(self) -> None:
        (view,) = read_walk_forward_v1(_Cursor([self._row(dict(self.report.identity))]), limit=10)
        for field, value in (("numeric_tier", "DECIMAL_AUTHORITATIVE"), ("cost_mode", "NET"),
                             ("evaluation_class", "HOLDOUT_VALIDATION"), ("promotable", True)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                WalkForwardView(**{**view.model_dump(), field: value})

    def test_a_plan_into_the_untouched_holdout_is_refused(self) -> None:
        with self.assertRaises(StrategyLabSplitError):
            _plan(evaluation_end_exclusive=UNTOUCHED_HOLDOUT_BOUNDARY_V1 + timedelta(days=1))


class FailClosedHandoffTests(unittest.TestCase):
    def test_risk_preview_without_owner_values_yields_no_risk_policy(self) -> None:
        account = AccountContextV1("ui2-offline", AccountKindV1.PERSONAL_PAPER, "UI-2", "USDT")
        policy = AccountPolicyV1(account, {})
        self.assertEqual("UNCONFIGURED", policy.status)
        self.assertTrue(policy.unresolved)
        self.assertTrue(all(reason.endswith("_OR_11") for reason in policy.unresolved))
        with self.assertRaisesRegex(AccountPolicyError, "account_policy_not_active"):
            policy.risk_policy()

    def test_a_report_without_a_registry_binding_never_states_a_length(self) -> None:
        fills = run(1, [60, 120])
        report = incubation_report_v1(fills, GROSS, as_of=AS_OF)
        (candidate,) = report["candidates"]
        self.assertEqual((False, "NOT_MODELLED", "PAPER_INCUBATION_NOT_VALIDATED"),
                         (report["cost_complete"], report["funding"], report["claim"]))
        self.assertEqual("GROSS_NON_PROMOTABLE", report["cost_mode"])
        self.assertEqual("GROSS_NON_PROMOTABLE_NO_VERIFIED_FEE_SCHEDULE", candidate["net"])
        self.assertEqual((UNRESOLVED_OR7_INCUBATION_DAYS, "UNRESOLVED", [UNRESOLVED_NO_BINDING]),
                         (candidate["required_days"], candidate["incubation"]["window"],
                          candidate["incubation"]["unresolved"]))
        self.assertEqual("INCUBATING", candidate["state"])

    def test_an_elapsed_window_still_lists_fees_funding_liveness_and_the_registry(self) -> None:
        # Even 28 elapsed days of caller-supplied evidence with parity and an ACTIVE account stay blocked.
        from trade_platform.strategy_lab_study_v1 import identity_hash_v1

        fills = run(1, [28 * 24 * 60 + 1])
        replay = {item["signal_id"]: identity_hash_v1(item) for item in fills}
        result = assess_candidate_readiness_v1(
            chain(fills=fills, replay_fill_hashes=replay, account_policy={"status": "ACTIVE", "unresolved": []}),
            cost_policy=GROSS, as_of=AS_OF)
        self.assertEqual(STAGE_FORWARD_EVIDENCE_INCOMPLETE, result.stage)
        self.assertNotEqual(STAGE_ELIGIBLE, result.stage)
        for blocker in (NEED_FEE_SCHEDULE, NEED_FORWARD_FUNDING, NEED_LIVENESS, NEED_REGISTRY_BINDING):
            self.assertIn(blocker, result.blockers)
        self.assertEqual("INCUBATING", result.canonical_state)

    def test_monitoring_without_runner_progress_proves_nothing(self) -> None:
        start = datetime(2026, 10, 1, tzinfo=UTC)
        result = derive_runner_liveness_v1(
            [], study_id="6f1c2a52-0000-4000-8000-000000000001", trial_id="6f1c2a52-0000-4000-8000-0000000000a1",
            symbol="BTCUSDT", window_start=start, window_end=start + timedelta(hours=2), acceptance=acceptance(),
            sealed=[], **BOUNDS)
        self.assertEqual(0, result.proven_micros)
        self.assertEqual(["NO_RUNNER_PROGRESS"], list(result.unproven_micros_by_reason))


if __name__ == "__main__":
    unittest.main()
