"""Phase R4.6 -- owner policies OR-3/OR-5/OR-6 are exact, identity-bound and never invent economics."""

from __future__ import annotations

import json
import re
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from trade_platform.strategy_lab_policies_v1 import (
    COST_MODE_FEE_SCHEDULED,
    COST_MODE_GROSS,
    FUNDING_HOLDOUT_RULE_V2,
    FUNDING_NOT_MODELLED_T2,
    FUNDING_SEARCH_RULE_V2,
    OR6_SCHEMA_VERSION_V1,
    CostPolicyV1,
    CostPolicyV2,
    FeeScheduleV1,
    SlippageScenarioV1,
    StrategyLabPolicyError,
    gross_cost_policy_v1,
    gross_cost_policy_v2,
    or3_numeric_policy_v1,
    or5_lags_v1,
    or5_t2_timing_policy_v1,
    rebuild_cost_policy_v1,
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


#: Frozen OR-6 v1 identities. Studies bind these slots; they must never move.
GROSS_V1_SLOT = "or6-cost-policy-v1:53233636d27380f758606c94fee33ebd68783999568fb6a6cdfea7acf1112244"
FIXTURE_V1_SLOT = "or6-cost-policy-v1:dac5070de25f8bd5297b95c572e005e9ed30492c150e7198a5bcb8745d864292"
GROSS_V2_SLOT = "or6-cost-policy-v2:1a56899daa53b4ae737e1e9974d446c45ea9269378641777df5be4d4cc181625"
FIXTURE_V2_SLOT = "or6-cost-policy-v2:8d142db4dd1ed291e2f7dc0f9dfaf992db6386df1b981df73c6cbc25f0305688"


def _scenarios() -> tuple[SlippageScenarioV1, ...]:
    return (SlippageScenarioV1("stress-a", "3", "fixture scenario"),)


class CostPolicySuccessionTests(unittest.TestCase):
    def test_historical_v1_identities_are_byte_stable(self) -> None:
        self.assertEqual(GROSS_V1_SLOT, gross_cost_policy_v1().policy().slot)
        self.assertEqual(FIXTURE_V1_SLOT, CostPolicyV1(_fees(), _scenarios()).policy().slot)
        payload = gross_cost_policy_v1().policy().payload
        self.assertEqual("OR-6 approved by the owner 2026-10-08", payload["decision"])  # historical text, kept
        self.assertEqual(FUNDING_NOT_MODELLED_T2, payload["funding"])

    def test_v2_has_its_own_identity(self) -> None:
        self.assertEqual(GROSS_V2_SLOT, gross_cost_policy_v2().policy().slot)
        self.assertEqual(FIXTURE_V2_SLOT, CostPolicyV2(_fees(), _scenarios()).policy().slot)
        self.assertNotEqual(gross_cost_policy_v1().policy().content_hash,
                            gross_cost_policy_v2().policy().content_hash)
        self.assertEqual(OR6_SCHEMA_VERSION_V1, gross_cost_policy_v2().policy().payload["supersedes"])

    def test_v2_funding_states_search_and_holdout_rules_separately(self) -> None:
        funding = gross_cost_policy_v2().policy().payload["funding"]
        self.assertEqual(FUNDING_SEARCH_RULE_V2, funding["search"])
        self.assertEqual(FUNDING_HOLDOUT_RULE_V2, funding["holdout_validation"])
        self.assertEqual("FAIL_CLOSED_VALIDATION_REFUSED", funding["missing_or_irregular_history"])
        self.assertNotIn("NOT_MODELLED", funding["holdout_validation"])

    def test_v2_claims_no_owner_approval_of_fees_or_slippage(self) -> None:
        payload = CostPolicyV2(_fees(), _scenarios()).policy().payload
        self.assertNotIn("approved by the owner", payload["decision"])
        self.assertIn("owner-gated", payload["decision"])
        self.assertEqual("NOT_ASSERTED_RECORDED_ONLY_BY_AN_AUTHORIZED_PREREGISTRATION",
                         payload["owner_approval_of_inputs"])

    def test_v2_defaults_fail_closed_and_invent_no_economics(self) -> None:
        gross = gross_cost_policy_v2()
        self.assertEqual(COST_MODE_GROSS, gross.mode)
        self.assertFalse(gross.promotable_cost_basis)
        self.assertIsNone(gross.policy().payload["venue_fees"])
        self.assertEqual([], gross.policy().payload["slippage_scenarios"])
        with self.assertRaises(StrategyLabPolicyError):
            gross.total_cost_bps_per_side("stress-a")
        with self.assertRaises(StrategyLabPolicyError):
            CostPolicyV2(fill_liquidity="MAKER")
        policy = CostPolicyV2(_fees(), _scenarios())
        self.assertEqual(Decimal("8"), policy.total_cost_bps_per_side("stress-a"))
        self.assertIsInstance(policy, CostPolicyV1)  # every typed CostPolicyV1 consumer accepts it

    def test_round_trip_rebuilds_either_version_exactly(self) -> None:
        for policy in (gross_cost_policy_v1(), gross_cost_policy_v2(), CostPolicyV1(_fees(), _scenarios()),
                       CostPolicyV2(_fees(), _scenarios())):
            stored = json.loads(json.dumps(policy.policy().payload, sort_keys=True))
            rebuilt = rebuild_cost_policy_v1(stored)
            self.assertIs(type(policy), type(rebuilt))
            self.assertEqual(policy.policy().slot, rebuilt.policy().slot)

    def test_rebuild_refuses_anything_inexact(self) -> None:
        good = CostPolicyV2(_fees(), _scenarios()).policy().payload
        cross_version = {**good, "schema_version": OR6_SCHEMA_VERSION_V1}
        for bad in (None, [], {**good, "schema_version": "or6-cost-policy-v9"}, cross_version,
                    {**good, "extra": "x"}, {k: v for k, v in good.items() if k != "fill_liquidity"},
                    {**good, "funding": FUNDING_NOT_MODELLED_T2},
                    {**good, "venue_fees": {**good["venue_fees"], "taker_fee_bps": "-1"}},
                    {**good, "slippage_scenarios": [{"name": "stress-a", "cost_bps_per_side": 3.0,
                                                     "rationale": "float"}]}):
            with self.assertRaises(StrategyLabPolicyError, msg=repr(bad)):
                rebuild_cost_policy_v1(bad)

    def test_v2_holdout_rule_is_the_validators_funding_rule(self) -> None:
        from trade_platform.bybit_funding_history_v1 import FUNDING_CHARGE_RULE_V1
        from trade_platform.strategy_lab_validation_v1 import FUNDING_RULE_V1

        self.assertEqual(FUNDING_RULE_V1, FUNDING_HOLDOUT_RULE_V2)
        self.assertTrue(FUNDING_HOLDOUT_RULE_V2.endswith(":" + FUNDING_CHARGE_RULE_V1))


if __name__ == "__main__":
    unittest.main()
