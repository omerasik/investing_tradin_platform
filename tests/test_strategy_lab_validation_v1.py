"""Phase R6 -- cycles, preregistration, holdout gate and holdout validation (offline).

Fixture data only. Every "authorized" packet here is a TEST packet with fixture
thresholds and a fixture fee schedule: none of it is an owner decision.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from tests.test_strategy_sdk_v1 import _rows, _walk
from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.public_archive_research_bars_v1 import ResearchBarsError, _refuse_holdout
from trade_platform.strategy_lab_authority_rerun_v1 import AuthorityRerunV1
from trade_platform.strategy_lab_policies_v1 import CostPolicyV1, FeeScheduleV1, SlippageScenarioV1
from trade_platform.strategy_lab_validation_v1 import (
    CURRENT_CYCLE_V1,
    STATUS_AUTHORIZED,
    STATUS_DRAFT,
    CriterionV1,
    HoldoutOpeningV1,
    PreregistrationV1,
    StrategyLabValidationError,
    new_research_cycle_v1,
    validate_on_holdout_v1,
)
from trade_platform.strategy_sdk_v1 import BarsV1

NOW = datetime(2026, 10, 8, tzinfo=UTC)


def fixture_cost_policy() -> dict[str, Any]:
    fees = FeeScheduleV1(venue="BYBIT", product="USDT_PERPETUAL", tier="FIXTURE", maker_fee_bps="1",
                         taker_fee_bps="5", verified_by="test", verified_on="2026-10-08",
                         source_reference="test fixture, not a real schedule")
    scenarios = (SlippageScenarioV1("fixture-low", "1", "fixture"), SlippageScenarioV1("fixture-high", "4", "fixture"))
    return CostPolicyV1(fee_schedule=fees, slippage_scenarios=scenarios).policy().payload


def established_rerun(study: Any, trial_ids: list[str], status: str = "ESTABLISHED") -> AuthorityRerunV1:
    identity = {"study_content_hash": study.content_hash, "candidate_set_hash": "c" * 64,
                "authoritative_selection": {"status": status,
                                            "selected": [{"rank": i, "trial_id": t} for i, t in enumerate(trial_ids, 1)]}}
    return AuthorityRerunV1(identity, "r" * 64)


def authorized_packet(study: Any, trials: list[str], cycle: Any, **overrides: Any) -> PreregistrationV1:
    values: dict[str, Any] = {
        "study": study, "rerun": established_rerun(study, trials), "cycle": cycle,
        "holdout_end_exclusive": cycle.holdout_start + timedelta(days=3),
        "acceptance_criteria": (CriterionV1("trades", ">=", "1"),),
        "minimum_trades": 1, "cost_policy": fixture_cost_policy(), "incubation_days": 14,
        "authorized_by": "test", "authorized_on": "2026-10-08",
    }
    values.update(overrides)
    return PreregistrationV1(**values)


class CycleTests(unittest.TestCase):
    def test_the_current_cycle_is_the_immutable_boundary(self) -> None:
        self.assertEqual(datetime(2026, 8, 20, tzinfo=UTC), CURRENT_CYCLE_V1.holdout_start)

    def test_a_new_cycle_is_prospective_only(self) -> None:
        cycle = new_research_cycle_v1(holdout_start=NOW + timedelta(days=30), registered_at=NOW, note="t")
        self.assertEqual("cycle-2026-11-07", cycle.cycle_id)
        with self.assertRaises(StrategyLabValidationError):
            new_research_cycle_v1(holdout_start=NOW - timedelta(days=1), registered_at=NOW, note="retroactive")
        with self.assertRaises(StrategyLabValidationError):
            new_research_cycle_v1(holdout_start=datetime(2026, 8, 1, tzinfo=UTC),
                                  registered_at=datetime(2026, 7, 1, tzinfo=UTC), note="before current")


class PreregistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="r6-"))
        self.study, _ = build_window_and_study(self.temp, family="trend_ma_cross")
        self.trials = [str(next(iter(self.study.trials())).trial_id)]

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_an_empty_packet_is_draft_and_names_every_owner_gap(self) -> None:
        packet = PreregistrationV1(study=self.study, rerun=established_rerun(self.study, self.trials))
        self.assertEqual(STATUS_DRAFT, packet.status)
        self.assertEqual(
            {"MISSING_OWNER_HOLDOUT_END_OR_7", "MISSING_OWNER_ACCEPTANCE_CRITERIA_OR_7",
             "MISSING_OWNER_MINIMUM_TRADES_OR_7", "MISSING_VERIFIED_FEE_SCHEDULE_AND_STRESS_ENVELOPE_OR_6",
             "MISSING_OWNER_INCUBATION_LENGTH_OR_7", "MISSING_OWNER_AUTHORIZATION"},
            set(packet.unresolved))
        self.assertEqual(CURRENT_CYCLE_V1.cycle_id, packet.identity()["cycle"]["cycle_id"])

    def test_a_gross_cost_policy_or_an_unestablished_rerun_never_authorizes(self) -> None:
        cycle = new_research_cycle_v1(holdout_start=NOW + timedelta(days=30), registered_at=NOW, note="t")
        self.assertEqual(STATUS_AUTHORIZED, authorized_packet(self.study, self.trials, cycle).status)
        gross = CostPolicyV1().policy().payload
        self.assertEqual(STATUS_DRAFT, authorized_packet(self.study, self.trials, cycle, cost_policy=gross).status)
        failed = established_rerun(self.study, self.trials, status="FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED")
        packet = authorized_packet(self.study, self.trials, cycle, rerun=failed)
        self.assertIn("AUTHORITY_RERUN_SELECTION_NOT_ESTABLISHED", packet.unresolved)

    def test_identity_is_deterministic_and_binds_the_owner_inputs(self) -> None:
        cycle = new_research_cycle_v1(holdout_start=NOW + timedelta(days=30), registered_at=NOW, note="t")
        a = authorized_packet(self.study, self.trials, cycle)
        b = authorized_packet(self.study, self.trials, cycle)
        c = authorized_packet(self.study, self.trials, cycle, minimum_trades=2)
        self.assertEqual(a.content_hash, b.content_hash)
        self.assertNotEqual(a.content_hash, c.content_hash)


class HoldoutGateTests(unittest.TestCase):
    def test_only_a_registry_issued_opening_unlocks_a_holdout_day(self) -> None:
        with self.assertRaises(ResearchBarsError):
            _refuse_holdout(date(2026, 8, 25))
        with self.assertRaises(StrategyLabValidationError):
            HoldoutOpeningV1("cycle-2026-08-20", datetime(2026, 8, 20, tzinfo=UTC),
                             datetime(2026, 9, 1, tzinfo=UTC), "p" * 64, "forger")
        # Reaching the private issuer deliberately: no public path mints a token.
        opening = HoldoutOpeningV1("cycle-2026-08-20", datetime(2026, 8, 20, tzinfo=UTC),
                                   datetime(2026, 9, 1, tzinfo=UTC), "p" * 64, "test",
                                   validation._REGISTRY_ISSUER)
        _refuse_holdout(date(2026, 8, 25), opening)
        with self.assertRaises(ResearchBarsError):
            _refuse_holdout(date(2026, 9, 1), opening)  # outside the opened span
        with self.assertRaises(ResearchBarsError):
            _refuse_holdout(date(2026, 8, 25), object())


class HoldoutValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="r6v-"))
        self.study, _ = build_window_and_study(self.temp, family="mean_reversion_z")
        self.trials = [str(t.trial_id) for t in self.study.trials()
                       if self.study.parameter_space.typed_point(t.parameters)["exit_z"]
                       < self.study.parameter_space.typed_point(t.parameters)["entry_z"]][:2]
        self.cycle = new_research_cycle_v1(holdout_start=datetime(2100, 1, 1, tzinfo=UTC),
                                           registered_at=NOW, note="test cycle")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _opening(self, packet: PreregistrationV1) -> HoldoutOpeningV1:
        assert packet.holdout_end_exclusive is not None
        return HoldoutOpeningV1(self.cycle.cycle_id, self.cycle.holdout_start, packet.holdout_end_exclusive,
                                packet.content_hash, "test", validation._REGISTRY_ISSUER)

    def _bars(self, days: int = 2) -> BarsV1:
        return BarsV1.from_rows(_rows(_walk(days * 1440, seed=3), start=self.cycle.holdout_start))

    def test_every_candidate_is_judged_under_every_lag_and_scenario(self) -> None:
        packet = authorized_packet(self.study, self.trials, self.cycle)
        run = validate_on_holdout_v1(packet, self._opening(packet), self._bars())
        self.assertEqual(len(self.trials), len(run.identity["candidates"]))
        for item in run.identity["candidates"]:
            self.assertEqual([2_000_000, 5_000_000, 30_000_000, 60_000_000], [lag["lag_micros"] for lag in item["lags"]])
            for lag in item["lags"]:
                self.assertEqual(["fixture-low", "fixture-high"], [s["scenario"] for s in lag["scenarios"]])
                for scenario in lag["scenarios"]:
                    self.assertTrue(scenario["metrics"]["cost_mode"].startswith("NET_OF:"))
            self.assertIn(item["state"], {"INCUBATING", "HOLDOUT_FAILED_REJECTED"})
            self.assertNotEqual("PROFESSIONALLY_VALIDATED", item["state"])
        self.assertEqual("CONDITIONAL_T2_HOLDOUT_INCUBATION_REQUIRED", run.identity["claim_ceiling"])

    def test_an_edge_that_vanishes_under_the_cost_envelope_is_rejected(self) -> None:
        criteria = (CriterionV1("total_return", ">", "0"),)
        cheap = authorized_packet(self.study, self.trials, self.cycle, acceptance_criteria=criteria)
        run = validate_on_holdout_v1(cheap, self._opening(cheap), self._bars())
        for item in run.identity["candidates"]:
            baseline = item["lags"][0]["scenarios"]
            if any(not s["passed"] for s in baseline):
                self.assertEqual("HOLDOUT_FAILED_REJECTED", item["state"])
                self.assertIn("HOLDOUT_CRITERIA_NOT_MET_UNDER_THE_APPROVED_COST_ENVELOPE", item["reasons"])

    def test_bars_outside_the_opening_or_a_foreign_opening_are_refused(self) -> None:
        packet = authorized_packet(self.study, self.trials, self.cycle)
        early = BarsV1.from_rows(_rows(_walk(100), start=self.cycle.holdout_start - timedelta(days=1)))
        with self.assertRaises(StrategyLabValidationError):
            validate_on_holdout_v1(packet, self._opening(packet), early)
        other = authorized_packet(self.study, self.trials, self.cycle, minimum_trades=5)
        with self.assertRaises(StrategyLabValidationError):
            validate_on_holdout_v1(packet, self._opening(other), self._bars())
        draft = PreregistrationV1(study=self.study, rerun=established_rerun(self.study, self.trials), cycle=self.cycle)
        with self.assertRaises(StrategyLabValidationError):
            validate_on_holdout_v1(draft, self._opening(packet), self._bars())

    def test_decimal_cost_is_charged_per_side_on_position_changes(self) -> None:
        from trade_platform.strategy_lab_authority_rerun_v1 import decimal_metrics_v1

        bars = BarsV1.from_rows(_rows([Decimal("100")] * 6))
        held = [0, 1, 1, -1, 0, 0]
        net = decimal_metrics_v1(bars, held, cost_bps_per_side=Decimal("10"), cost_label="x")
        # Flat prices: the only P&L is cost on |1| + |2| + |1| units of turnover at 10 bps per side.
        self.assertEqual(3, net["trades"])  # 0->1, 1->-1, -1->0
        expected = Decimal("0.999") * Decimal("0.998") * Decimal("0.999") - 1
        self.assertEqual(format(expected.quantize(Decimal("1E-18")), "f"), net["total_return"])
        self.assertEqual("NET_OF:x", net["cost_mode"])


if __name__ == "__main__":
    unittest.main()
