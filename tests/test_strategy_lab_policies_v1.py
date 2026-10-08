"""Phase R4.6 -- owner policies OR-3/OR-5/OR-6 are exact, identity-bound and never invent economics."""

from __future__ import annotations

import re
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from trade_platform.strategy_lab_policies_v1 import (
    COST_MODE_FEE_SCHEDULED,
    COST_MODE_GROSS,
    CostPolicyV1,
    FeeScheduleV1,
    SlippageScenarioV1,
    StrategyLabPolicyError,
    gross_cost_policy_v1,
    or3_numeric_policy_v1,
    or5_lags_v1,
    or5_t2_timing_policy_v1,
)

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261008_0055_strategy_lab_owner_policies.py"


def _fees() -> FeeScheduleV1:
    return FeeScheduleV1(venue="BYBIT", product="USDT_PERPETUAL", tier="TEST_TIER", maker_fee_bps="1",
                         taker_fee_bps="5", verified_by="test", verified_on="2026-10-08",
                         source_reference="fixture, not a real schedule")


class PolicyTests(unittest.TestCase):
    def test_the_migration_pins_exactly_the_code_or3_identity(self) -> None:
        pinned = re.search(r"or3-numeric-policy-v1:[0-9a-f]{64}", MIGRATION.read_text(encoding="utf-8"))
        assert pinned is not None
        self.assertEqual(or3_numeric_policy_v1().slot, pinned.group(0))

    def test_or3_states_the_doctrine_not_an_implementation_detail(self) -> None:
        payload = or3_numeric_policy_v1().payload
        self.assertEqual("DECIMAL_WINS", payload["boundary_rule"])
        self.assertEqual("FAIL_CLOSED", payload["unestablished_authority_rule"])
        self.assertEqual("SEARCH_NON_AUTHORITATIVE", payload["search_tier"]["label"])
        self.assertIn("PAPER_POSITIONS_CASH_FILLS_AND_PNL", payload["authoritative_rerun_scope"])
        self.assertFalse(payload["library_float_behaviour_in_identity"])

    def test_or5_is_two_seconds_with_the_mandatory_sweep(self) -> None:
        self.assertEqual((timedelta(seconds=2), timedelta(seconds=5), timedelta(seconds=30),
                          timedelta(seconds=60)), or5_lags_v1())
        payload = or5_t2_timing_policy_v1().payload
        self.assertEqual("CONDITIONAL_NEVER_T3_OR_T4_PROFESSIONAL", payload["claim_ceiling"])
        self.assertEqual("SEPARATE_CLOCK_FILE_AVAILABILITY_NOT_DISSEMINATION", payload["archive_last_modified"])

    def test_without_a_verified_fee_schedule_costs_fail_closed(self) -> None:
        gross = gross_cost_policy_v1()
        self.assertEqual(COST_MODE_GROSS, gross.mode)
        self.assertFalse(gross.promotable_cost_basis)
        self.assertEqual([], gross.policy().payload["slippage_scenarios"])
        with self.assertRaises(StrategyLabPolicyError):
            gross.total_cost_bps_per_side("any")

    def test_a_cost_dependent_number_needs_fees_and_a_declared_scenario(self) -> None:
        scenario = SlippageScenarioV1("stress-a", "3", "fixture scenario")
        policy = CostPolicyV1(fee_schedule=_fees(), slippage_scenarios=(scenario,))
        self.assertEqual(COST_MODE_FEE_SCHEDULED, policy.mode)
        self.assertTrue(policy.promotable_cost_basis)
        self.assertEqual(Decimal("8"), policy.total_cost_bps_per_side("stress-a"))
        with self.assertRaises(StrategyLabPolicyError):
            policy.total_cost_bps_per_side("undeclared")
        self.assertFalse(CostPolicyV1(fee_schedule=_fees()).promotable_cost_basis)
        self.assertNotEqual(policy.policy().slot, gross_cost_policy_v1().policy().slot)

    def test_invented_or_malformed_inputs_are_refused(self) -> None:
        with self.assertRaises(StrategyLabPolicyError):
            SlippageScenarioV1("stress-a", 3.0, "float")  # type: ignore[arg-type]
        with self.assertRaises(StrategyLabPolicyError):
            SlippageScenarioV1("stress-a", "-1", "negative")
        with self.assertRaises(StrategyLabPolicyError):
            CostPolicyV1(slippage_scenarios=(SlippageScenarioV1("a", "1", "x"), SlippageScenarioV1("a", "2", "y")))
        with self.assertRaises(StrategyLabPolicyError):
            CostPolicyV1(fill_liquidity="MAKER")
        with self.assertRaises(StrategyLabPolicyError):
            FeeScheduleV1(venue="BYBIT", product="P", tier="T", maker_fee_bps="1", taker_fee_bps="5",
                          verified_by="", verified_on="2026-10-08", source_reference="x")


if __name__ == "__main__":
    unittest.main()
