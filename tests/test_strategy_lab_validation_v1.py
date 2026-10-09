"""Phase R6 -- cycles, preregistration, holdout gate and holdout validation (offline).

Fixture data only. Every "authorized" packet here is a TEST packet with fixture
thresholds and a fixture fee schedule: none of it is an owner decision. Test
cycles and openings are minted through the private issuers deliberately (no
public path does that); the registry tests run against PostgreSQL.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.test_bybit_funding_history_v1 import FundingPages, eight_hourly, fixture_funding_history
from tests.test_strategy_lab_e2e_fixture import FIRST_DAY, SyntheticDays, build_window_and_study
from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.bybit_funding_history_v1 import acquire_funding_history_v1
from trade_platform.public_archive_research_bars_v1 import (
    ResearchBarsError,
    _days,
    _refuse_holdout,
    acquire_and_derive_day_v1,
    build_research_bar_dataset_v1,
)
from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
from trade_platform.strategy_lab_authority_rerun_v1 import AuthorityRerunV1, decimal_metrics_v1
from trade_platform.strategy_lab_policies_v1 import CostPolicyV1, FeeScheduleV1, SlippageScenarioV1
from trade_platform.strategy_lab_validation_v1 import (
    CURRENT_CYCLE_V1,
    STATUS_AUTHORIZED,
    STATUS_DRAFT,
    CriterionV1,
    HoldoutOpeningV1,
    PreregistrationV1,
    ResearchCycleV1,
    StrategyLabValidationError,
    candidate_lifecycle_v1,
    validate_on_holdout_v1,
)
from trade_platform.strategy_sdk_v1 import BarsV1

TEST_CYCLE_START = datetime(2026, 6, 1, tzinfo=UTC)  # the fixture archive days; a TEST cycle only


def test_cycle(start: datetime = TEST_CYCLE_START) -> ResearchCycleV1:
    return validation._issue_cycle(start, datetime(2026, 5, 1, tzinfo=UTC), "test cycle")


def fixture_cost_policy(**fee_overrides: Any) -> dict[str, Any]:
    fees = {"venue": "BYBIT", "product": "USDT_PERPETUAL", "tier": "FIXTURE", "maker_fee_bps": "1",
            "taker_fee_bps": "5", "verified_by": "test", "verified_on": "2026-10-08",
            "source_reference": "test fixture, not a real schedule", **fee_overrides}
    scenarios = (SlippageScenarioV1("fixture-low", "1", "fixture"), SlippageScenarioV1("fixture-high", "4", "fixture"))
    return CostPolicyV1(fee_schedule=FeeScheduleV1(**fees), slippage_scenarios=scenarios).policy().payload


def established_rerun(study: Any, trial_ids: list[str], status: str = "ESTABLISHED") -> AuthorityRerunV1:
    identity = {"study_content_hash": study.content_hash, "candidate_set_hash": "c" * 64,
                "authoritative_selection": {"status": status,
                                            "selected": [{"rank": i, "trial_id": t} for i, t in enumerate(trial_ids, 1)]}}
    return AuthorityRerunV1(identity, "r" * 64)


def authorized_packet(study: Any, trials: list[str], cycle: ResearchCycleV1, **overrides: Any) -> PreregistrationV1:
    values: dict[str, Any] = {
        "study": study, "rerun": established_rerun(study, trials), "symbol": "BTCUSDT", "cycle": cycle,
        "holdout_end_exclusive": cycle.holdout_start + timedelta(days=2),
        "acceptance_criteria": (CriterionV1("trades", ">=", "1"),),
        "minimum_trades": 1, "cost_policy": fixture_cost_policy(), "incubation_days": 14,
        "authorized_by": "test", "authorized_on": "2026-10-08",
    }
    values.update(overrides)
    return PreregistrationV1(**values)


def opening_for(packet: PreregistrationV1) -> HoldoutOpeningV1:
    assert packet.holdout_end_exclusive is not None
    return validation._issue_opening(packet.cycle.cycle_id, packet.cycle.holdout_start,
                                     packet.holdout_end_exclusive, packet.content_hash, "test")


class _Study(unittest.TestCase):
    family = "mean_reversion_z"

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="r6-"))
        self.study, self.data_root = build_window_and_study(self.temp, family=self.family)
        self.trials = [str(t.trial_id) for t in self.study.trials()
                       if self.study.parameter_space.typed_point(t.parameters).get("exit_z", Decimal(0))
                       < self.study.parameter_space.typed_point(t.parameters).get("entry_z", Decimal(1))][:2]

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)


class CycleTests(unittest.TestCase):
    def test_the_current_cycle_is_the_immutable_boundary(self) -> None:
        self.assertEqual(datetime(2026, 8, 20, tzinfo=UTC), CURRENT_CYCLE_V1.holdout_start)
        self.assertEqual("cycle-2026-08-20", CURRENT_CYCLE_V1.cycle_id)

    def test_cycles_cannot_be_minted_or_named_freely(self) -> None:
        with self.assertRaises(StrategyLabValidationError):
            ResearchCycleV1(datetime(2026, 8, 20, tzinfo=UTC), None, "forged")
        with self.assertRaises(StrategyLabValidationError):
            test_cycle(datetime(2026, 9, 1, 12, tzinfo=UTC))  # not a whole UTC day
        # The id is derived, never chosen: no "cycle-2026-08-21" over the current start.
        self.assertEqual("cycle-2026-06-01", test_cycle().cycle_id)


class PreregistrationTests(_Study):
    def test_an_empty_packet_is_draft_and_names_every_owner_gap(self) -> None:
        packet = PreregistrationV1(study=self.study, rerun=established_rerun(self.study, self.trials), symbol="BTCUSDT")
        self.assertEqual(STATUS_DRAFT, packet.status)
        self.assertEqual(
            {"MISSING_OWNER_HOLDOUT_END_OR_7", "MISSING_OWNER_ACCEPTANCE_CRITERIA_OR_7",
             "MISSING_OWNER_MINIMUM_TRADES_OR_7", "MISSING_VERIFIED_FEE_SCHEDULE_AND_STRESS_ENVELOPE_OR_6",
             "MISSING_OWNER_INCUBATION_LENGTH_OR_7", "MISSING_OWNER_AUTHORIZATION"},
            set(packet.unresolved))

    def test_an_unverified_or_gross_cost_basis_never_authorizes(self) -> None:
        cycle = test_cycle()
        self.assertEqual(STATUS_AUTHORIZED, authorized_packet(self.study, self.trials, cycle).status)
        for cost in (CostPolicyV1().policy().payload,
                     {**fixture_cost_policy(), "venue_fees": {**fixture_cost_policy()["venue_fees"],
                                                              "taker_fee_bps": "-20"}},
                     {**fixture_cost_policy(), "mode": "FEE_SCHEDULED", "extra": "x"}):
            with self.subTest(cost=cost.get("venue_fees")):
                self.assertIn("MISSING_VERIFIED_FEE_SCHEDULE_AND_STRESS_ENVELOPE_OR_6",
                              authorized_packet(self.study, self.trials, cycle, cost_policy=cost).unresolved)
        failed = established_rerun(self.study, self.trials, status="FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED")
        self.assertIn("AUTHORITY_RERUN_SELECTION_NOT_ESTABLISHED",
                      authorized_packet(self.study, self.trials, cycle, rerun=failed).unresolved)

    def test_the_identity_is_frozen_against_later_mutation(self) -> None:
        cost = fixture_cost_policy()
        packet = authorized_packet(self.study, self.trials, test_cycle(), cost_policy=cost)
        before = packet.content_hash
        cost["venue_fees"]["taker_fee_bps"] = "-50"
        self.assertEqual(before, packet.content_hash)
        self.assertEqual("5", packet.frozen["cost_policy"]["venue_fees"]["taker_fee_bps"])

    def test_malformed_owner_fields_are_refused(self) -> None:
        cycle = test_cycle()
        for overrides in ({"minimum_trades": 0}, {"minimum_trades": -1}, {"incubation_days": True},
                          {"authorized_by": "  "}, {"authorized_on": "yesterday"},
                          {"holdout_end_exclusive": cycle.holdout_start + timedelta(hours=5)}):
            with self.subTest(overrides=overrides), self.assertRaises((StrategyLabValidationError, ValueError)):
                authorized_packet(self.study, self.trials, cycle, **overrides)


class HoldoutGateTests(unittest.TestCase):
    def test_only_a_registry_issued_opening_unlocks_holdout_days(self) -> None:
        with self.assertRaises(ResearchBarsError):
            _refuse_holdout(date(2026, 8, 25))
        with self.assertRaises(StrategyLabValidationError):
            HoldoutOpeningV1("cycle-2026-08-20", datetime(2026, 8, 20, tzinfo=UTC),
                             datetime(2026, 9, 1, tzinfo=UTC), "p" * 64, "forger")
        unissued = HoldoutOpeningV1("cycle-2026-08-20", datetime(2026, 8, 20, tzinfo=UTC),
                                    datetime(2026, 9, 1, tzinfo=UTC), "q" * 64, "test", validation._REGISTRY_ISSUER)
        with self.assertRaises(ResearchBarsError):
            _refuse_holdout(date(2026, 8, 25), unissued)  # the issuer object alone is not an issued opening
        opening = validation._issue_opening("cycle-2026-08-20", datetime(2026, 8, 20, tzinfo=UTC),
                                            datetime(2026, 9, 1, tzinfo=UTC), "p" * 64, "test")
        _refuse_holdout(date(2026, 8, 25), opening)
        forged = dataclasses.replace(opening, holdout_end_exclusive=datetime(2026, 10, 1, tzinfo=UTC))
        with self.assertRaises(ResearchBarsError):
            _refuse_holdout(date(2026, 9, 25), forged)  # a replace() copy is not an issued opening
        with self.assertRaises(ResearchBarsError):
            _refuse_holdout(date(2026, 9, 1), opening)
        with self.assertRaises(ResearchBarsError):
            _refuse_holdout(date(2026, 8, 25), object())

    def test_every_window_day_is_checked_not_only_the_last(self) -> None:
        later = validation._issue_opening("cycle-2026-10-20", datetime(2026, 10, 20, tzinfo=UTC),
                                          datetime(2026, 10, 25, tzinfo=UTC), "p" * 64, "test")
        with self.assertRaises(ResearchBarsError):
            _days(date(2026, 8, 20), date(2026, 10, 21), later)
        self.assertEqual(2, len(_days(date(2026, 10, 20), date(2026, 10, 21), later)))


class HoldoutValidationTests(_Study):
    def _holdout(self, packet: PreregistrationV1, *, days: int = 2, symbol: str = "BTCUSDT") -> Any:
        # The fixture study window already holds FIRST_DAY and FIRST_DAY+1 (the test cycle's span).
        store = ResearchFrameStoreV1(self.data_root)
        archive = self.data_root.parent / "archive"
        fetch = SyntheticDays()
        for offset in range(days):
            acquire_and_derive_day_v1(archive, symbol, FIRST_DAY + timedelta(days=offset), store=store, evict=True,
                                      fetch=fetch)
        return build_research_bar_dataset_v1(archive, symbol, FIRST_DAY, FIRST_DAY + timedelta(days=days - 1),
                                             store=store)

    def _funding(self, *, days: int = 2, symbol: str = "BTCUSDT", rate: str = "0.0001") -> Any:
        return fixture_funding_history(ResearchFrameStoreV1(self.data_root).root, symbol, FIRST_DAY, days, rate=rate)

    def test_every_candidate_is_judged_under_every_lag_and_scenario(self) -> None:
        packet = authorized_packet(self.study, self.trials, test_cycle())
        run = validate_on_holdout_v1(packet, opening_for(packet), self._holdout(packet),
                                     store=ResearchFrameStoreV1(self.data_root), funding=self._funding())
        self.assertEqual(len(self.trials), len(run.identity["candidates"]))
        for item in run.identity["candidates"]:
            self.assertEqual([2_000_000, 5_000_000, 30_000_000, 60_000_000],
                             [lag["lag_micros"] for lag in item["lags"]])
            for lag in item["lags"]:
                self.assertEqual(["fixture-low", "fixture-high"], [s["scenario"] for s in lag["scenarios"]])
                for scenario in lag["scenarios"]:
                    self.assertTrue(scenario["metrics"]["cost_mode"].startswith("NET_OF:"))
                    self.assertIsNone(scenario["metrics"]["break_even_bps_per_side"])
            self.assertIn(item["state"], {"INCUBATING", "HOLDOUT_FAILED_REJECTED"})
        self.assertEqual("CONDITIONAL_T2_HOLDOUT_INCUBATION_REQUIRED", run.identity["claim_ceiling"])
        self.assertEqual(64, len(run.identity["holdout"]["dataset_content_hash"]))

    def test_a_partial_or_foreign_holdout_dataset_is_refused(self) -> None:
        packet = authorized_packet(self.study, self.trials, test_cycle())
        store = ResearchFrameStoreV1(self.data_root)
        funding = self._funding()
        with self.assertRaises(StrategyLabValidationError):
            validate_on_holdout_v1(packet, opening_for(packet), self._holdout(packet, days=1), store=store,
                                   funding=funding)
        other = authorized_packet(self.study, self.trials, test_cycle(), minimum_trades=5)
        with self.assertRaises(StrategyLabValidationError):
            validate_on_holdout_v1(packet, opening_for(other), self._holdout(packet), store=store, funding=funding)
        stretched = dataclasses.replace(opening_for(packet), holdout_end_exclusive=datetime(2030, 1, 1, tzinfo=UTC))
        with self.assertRaises(StrategyLabValidationError):
            validate_on_holdout_v1(packet, stretched, self._holdout(packet), store=store, funding=funding)

    def test_a_swapped_or_relabelled_holdout_dataset_is_refused(self) -> None:
        packet = authorized_packet(self.study, self.trials, test_cycle())
        genuine = self._holdout(packet)
        funding = self._funding()
        relabelled = dataclasses.replace(genuine, content_hash="0" * 64)
        with self.assertRaises(StrategyLabValidationError):
            validate_on_holdout_v1(packet, opening_for(packet), relabelled, store=ResearchFrameStoreV1(self.data_root),
                                   funding=funding)
        swapped = dataclasses.replace(genuine, bar_frame_manifest_hash="f" * 64)
        run = validate_on_holdout_v1(packet, opening_for(packet), swapped, store=ResearchFrameStoreV1(self.data_root),
                                     funding=funding)
        # The caller's frame pointer is ignored: bars come from the re-proven dataset.
        self.assertEqual(genuine.content_hash, run.identity["holdout"]["dataset_content_hash"])

    def test_a_forged_cycle_cannot_enter_a_packet(self) -> None:
        forged = dataclasses.replace(CURRENT_CYCLE_V1, holdout_start=datetime(2026, 8, 21, tzinfo=UTC))
        with self.assertRaises(StrategyLabValidationError):
            authorized_packet(self.study, self.trials, forged)

    def test_published_funding_is_charged_and_bound(self) -> None:
        packet = authorized_packet(self.study, self.trials, test_cycle())
        funding = self._funding()
        run = validate_on_holdout_v1(packet, opening_for(packet), self._holdout(packet),
                                     store=ResearchFrameStoreV1(self.data_root), funding=funding)
        self.assertEqual(validation.FUNDING_RULE_V1, packet.frozen["funding_rule"])
        self.assertEqual(funding.content_hash, run.identity["funding"]["content_hash"])
        self.assertEqual("T2_EVENT_TIME", run.identity["funding"]["evidence_tier"])
        self.assertEqual("CONDITIONAL_T2_HOLDOUT_INCUBATION_REQUIRED", run.identity["claim_ceiling"])
        for item in run.identity["candidates"]:
            self.assertNotIn(validation.REASON_FUNDING, item["reasons"])
            for lag in item["lags"]:
                for scenario in lag["scenarios"]:
                    self.assertTrue(scenario["metrics"]["funding_mode"].endswith(funding.content_hash))
                    self.assertEqual(6, scenario["metrics"]["funding_events_in_span"])  # 00/08/16 UTC on both days

    def test_a_missing_funding_observation_refuses_the_whole_validation(self) -> None:
        packet = authorized_packet(self.study, self.trials, test_cycle())
        store = ResearchFrameStoreV1(self.data_root)
        rates = eight_hourly(FIRST_DAY, 2)
        del rates[sorted(rates)[2]]  # day 1 16:00 never published
        gapped = acquire_funding_history_v1(store.root, "BTCUSDT", FIRST_DAY, FIRST_DAY + timedelta(days=1),
                                            fetch=FundingPages(rates), now=lambda: datetime(2026, 10, 9, tzinfo=UTC))
        with self.assertRaisesRegex(StrategyLabValidationError, "funding_history_not_complete"):
            validate_on_holdout_v1(packet, opening_for(packet), self._holdout(packet), store=store, funding=gapped)

    def test_foreign_partial_or_relabelled_funding_refuses_the_whole_validation(self) -> None:
        packet = authorized_packet(self.study, self.trials, test_cycle())
        store = ResearchFrameStoreV1(self.data_root)
        holdout = self._holdout(packet)
        for funding in (self._funding(days=1), self._funding(symbol="ETHUSDT"),
                        dataclasses.replace(self._funding(), content_hash="0" * 64)):
            with self.subTest(funding=funding.identity["last_utc_day"]), self.assertRaises(StrategyLabValidationError):
                validate_on_holdout_v1(packet, opening_for(packet), holdout, store=store, funding=funding)

    def test_a_held_position_at_an_instant_without_a_bar_is_rejected_not_costed(self) -> None:
        # Settlements published 30 s past each 8-hour mark: uniform and COMPLETE, but no bar opens
        # at them, so any candidate holding a position there has no reference price.
        packet = authorized_packet(self.study, self.trials, test_cycle())
        store = ResearchFrameStoreV1(self.data_root)
        offset = {at + 30_000: rate for at, rate in eight_hourly(FIRST_DAY, 2).items()}
        funding = acquire_funding_history_v1(store.root, "BTCUSDT", FIRST_DAY, FIRST_DAY + timedelta(days=1),
                                             fetch=FundingPages(offset), now=lambda: datetime(2026, 10, 9, tzinfo=UTC))
        run = validate_on_holdout_v1(packet, opening_for(packet), self._holdout(packet), store=store, funding=funding)
        exposed = [item for item in run.identity["candidates"]
                   if any(s["metrics"]["funding_not_costable"] for lag in item["lags"] for s in lag["scenarios"])]
        self.assertTrue(exposed, "the fixture candidates hold positions across 8-hour marks")
        for item in exposed:
            self.assertIn(validation.REASON_FUNDING_NOT_COSTABLE, item["reasons"])
            self.assertEqual(1, item["reasons"].count(validation.REASON_FUNDING_NOT_COSTABLE))
            self.assertEqual("HOLDOUT_FAILED_REJECTED", item["state"])
        self.assertEqual("NOT_CHARGED", run.identity["funding"]["calculated_semantics"]["flat_at_the_instant"])

    def test_a_packet_under_the_pre_funding_rule_is_not_validated(self) -> None:
        packet = authorized_packet(self.study, self.trials, test_cycle())
        legacy = {**packet.frozen, "funding_rule": validation.REASON_FUNDING}
        object.__setattr__(packet, "_frozen", json.dumps(legacy, sort_keys=True, separators=(",", ":")))
        with self.assertRaises(StrategyLabValidationError):
            validate_on_holdout_v1(packet, opening_for(packet), self._holdout(packet),
                                   store=ResearchFrameStoreV1(self.data_root), funding=self._funding())

    def test_decimal_cost_is_charged_per_side_on_position_changes(self) -> None:
        bars = BarsV1.from_rows([(datetime(2026, 6, 1, tzinfo=UTC) + timedelta(minutes=i),
                                  datetime(2026, 6, 1, tzinfo=UTC) + timedelta(minutes=i + 1), None,
                                  Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"))
                                 for i in range(6)])
        net = decimal_metrics_v1(bars, [0, 1, 1, -1, 0, 0], cost_bps_per_side=Decimal("10"), cost_label="x")
        self.assertEqual(3, net["trades"])
        expected = Decimal("0.999") * Decimal("0.998") * Decimal("0.999") - 1
        self.assertEqual(format(expected.quantize(Decimal("1E-18")), "f"), net["total_return"])

    def test_a_rejection_is_terminal_in_the_lifecycle(self) -> None:
        events = [{"trial_id": "t", "state": "HOLDOUT_FAILED_REJECTED"}, {"trial_id": "t", "state": "INCUBATING"}]
        self.assertEqual({"t": "HOLDOUT_FAILED_REJECTED"}, candidate_lifecycle_v1(events))


if __name__ == "__main__":
    unittest.main()
