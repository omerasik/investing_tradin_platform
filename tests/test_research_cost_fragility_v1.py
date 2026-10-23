"""Deterministic gross sensitivity, precision, missing evidence and projection identity."""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import localcontext

from tests.test_research_candidate_comparison_v1 import _evidence
from trade_platform.research_candidate_comparison_v1 import compare_candidates_v1
from trade_platform.research_cost_fragility_v1 import cost_fragility_v1
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

BASE = {"numeric_tier": "DECIMAL_AUTHORITATIVE", "cost_mode": "GROSS_NON_PROMOTABLE",
        "turnover": "10.000000000000000000", "break_even_bps_per_side": "2.500000000000000001"}


def assess(**changes):
    return cost_fragility_v1(**{"baseline": BASE, "search": {"break_even_bps_per_side": "99"},
                               "lag_micros": 2_000_000, "cost_policy": {},
                               "before_holdout": True, "selection_established": True, **changes})


class CostFragilityTests(unittest.TestCase):
    def test_exact_remainder_ignores_ambient_precision_and_preserves_recorded_scale(self):
        with localcontext() as ctx:
            ctx.prec = 6
            result = assess()
        self.assertEqual("2.500000000000000001", result.threshold_bps_per_side)
        self.assertEqual(["1.500000000000000001", "0.000000000000000001", "-2.499999999999999999"],
                         [s.remaining_bps_per_side for s in result.scenarios])
        self.assertEqual(["ABOVE", "ABOVE", "BELOW"], [s.relation for s in result.scenarios])
        self.assertTrue(all(not s.in_net for s in result.scenarios))
        self.assertEqual("99", result.search_threshold_bps_per_side)
        self.assertEqual("UNAVAILABLE", result.net_profitability)
        self.assertEqual("NOT_INFERRED", result.independent_validation)

    def test_zero_negative_and_equal_are_not_profitable_claims(self):
        for value, expected in (("0", "NO_POSITIVE_GROSS_HEADROOM"), ("-1", "NO_POSITIVE_GROSS_HEADROOM"),
                                ("2.5", "POSITIVE_GROSS_HEADROOM")):
            with self.subTest(value=value):
                result = assess(baseline={**BASE, "break_even_bps_per_side": value})
                self.assertEqual(expected, result.status)
                if value == "2.5":
                    self.assertEqual(("EQUAL", "0.0"), (result.scenarios[1].relation,
                                                         result.scenarios[1].remaining_bps_per_side))

    def test_missing_invalid_costed_funded_and_search_only_never_get_headroom(self):
        cases = [{}, {**BASE, "turnover": "0"}, {**BASE, "turnover": None},
                 {**BASE, "cost_mode": "NET_OF:example"}, {**BASE, "funding_mode": "CHARGED:hash"},
                 {**BASE, "numeric_tier": "SEARCH_NON_AUTHORITATIVE"}]
        cases += [{**BASE, "break_even_bps_per_side": value}
                  for value in (None, "NaN", "Infinity", "n/a", True, 2.5, "1e99999")]
        for baseline in cases:
            with self.subTest(baseline=baseline):
                result = assess(baseline=baseline)
                self.assertEqual("UNAVAILABLE", result.status)
                self.assertIsNone(result.threshold_bps_per_side)
                self.assertTrue(all(s.remaining_bps_per_side is None for s in result.scenarios))
        for boundary in (False, None):
            self.assertEqual("UNAVAILABLE", assess(before_holdout=boundary).status)
        self.assertEqual("UNAVAILABLE", assess(selection_established=False).status)
        self.assertEqual("UNAVAILABLE", assess(lag_micros=None).status)

    def test_bound_scenarios_and_prepared_proposal_never_verify_fees_or_spread(self):
        result = assess(cost_policy={"venue_fees": {"taker_fee_bps": "1"}, "slippage_scenarios": [
            {"name": "bound_stress", "cost_bps_per_side": "3", "rationale": "Recorded stress proposal"},
            {"name": "invalid", "cost_bps_per_side": "NaN", "rationale": "Invalid"}]})
        self.assertEqual(4, len(result.scenarios))
        self.assertEqual("STUDY_BOUND_COST_POLICY_NOT_APPROVAL", result.scenarios[-1].source)
        self.assertEqual("-0.499999999999999999", result.scenarios[-1].remaining_bps_per_side)
        self.assertIn("MALFORMED_BOUND_SCENARIO_UNAVAILABLE", result.reasons)
        self.assertEqual("UNAVAILABLE", result.verified_account_fees)
        self.assertIn("UNAVAILABLE", result.observed_spread_comparison)
        self.assertIn("ACCOUNT_FEES_NOT_VERIFIED_BY_THIS_PROJECTION", result.reasons)

    def test_comparison_uses_only_this_candidates_latest_bound_decimal_evidence(self):
        evidence = _evidence({"a": {"break_even_bps_per_side": "999"}, "b": {}}, rerun="ESTABLISHED",
                             decimal={"a": BASE})
        result = compare_candidates_v1([evidence], sort=None)
        a, b = result.rows
        self.assertEqual("2.500000000000000001", a.cost_fragility.threshold_bps_per_side)
        self.assertEqual("UNAVAILABLE", b.cost_fragility.status)
        self.assertEqual(evidence.candidate_set["candidates"][0]["trial_id"], a.identity.trial_id)
        self.assertEqual(evidence.reruns[0][0], a.decimal_rerun.rerun_hash)
        self.assertEqual("NOT_READ", result.holdout)
        self.assertFalse(a.promotable)
        self.assertEqual("NONE", a.execution_authority)
        later = {**evidence.reruns[0][1], "results": []}
        new = replace(evidence, reruns=[*evidence.reruns, (identity_hash_v1(later), later)])
        self.assertEqual("UNAVAILABLE", compare_candidates_v1([new], sort=None).rows[0].cost_fragility.status)


if __name__ == "__main__":
    unittest.main()
