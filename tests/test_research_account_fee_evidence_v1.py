"""Synthetic actual-account attestations: never real financial evidence."""
import unittest
from dataclasses import replace
from decimal import localcontext

from tests.test_research_candidate_comparison_v1 import _evidence
from tests.test_research_cost_fragility_v1 import BASE, assess
from trade_platform.research_account_fee_evidence_v1 import DevelopmentFeeBindingV1
from trade_platform.research_candidate_comparison_v1 import compare_candidates_v1
from trade_platform.strategy_lab_study_v1 import identity_hash_v1


def binding_for(study_hash="s", set_hash="c", datasets=("d",)):
    evidence = {
        "schema_version": "actual-account-fee-evidence-v1", "account_id": "fixture-account",
        "verification_status": "VERIFIED", "verification_source": "ACTUAL_ACCOUNT_FEE_STATEMENT",
        "valid_from": "2025-01-01T00:00:00Z", "valid_until_exclusive": "2026-09-01T00:00:00Z",
        "fee_schedule": {"venue": "BYBIT", "product": "USDT_PERPETUAL", "tier": "fixture-vip",
            "maker_fee_bps": "0", "taker_fee_bps": "0.123456789012345678901234567890123456789",
            "verified_by": "fixture-verifier", "verified_on": "2026-10-01",
            "source_reference": "fixture-only-account-statement:sha256:example"}}
    return DevelopmentFeeBindingV1("fixture-account", "BYBIT", "USDT_PERPETUAL", "fixture-vip",
        study_hash, set_hash, datasets, "2025-08-20T00:00:00Z", "2026-08-19T00:00:00Z",
        "2026-10-23T00:00:00Z", identity_hash_v1(evidence), evidence)


def project(binding):
    return assess(fee_binding=binding, study_content_hash="s", candidate_set_hash="c",
        dataset_content_hashes=("d",), evaluation_end_exclusive="2026-08-19T00:00:00Z")


def change_evidence(binding, **changes):
    evidence = {**binding.evidence, **changes}
    try:
        digest = identity_hash_v1(evidence)
    except ValueError:
        digest = "malformed"
    return replace(binding, evidence=evidence, evidence_hash=digest)


class AccountFeeTests(unittest.TestCase):
    def test_exact_fee_and_scenario_arithmetic_without_approval(self):
        binding = binding_for()
        with localcontext() as ctx:
            ctx.prec = 6
            result = project(binding)
        self.assertEqual("VERIFIED", result.verified_account_fees)
        self.assertEqual("2.376543210987654322098765432109876543211",
            result.account_fee_evidence.remaining_after_fee_bps_per_side)
        self.assertEqual("-0.123456789012345677901234567890123456789",
            result.scenarios[1].remaining_after_verified_fee_and_scenario_bps_per_side)
        self.assertEqual("0.000000000000000001", result.scenarios[1].remaining_bps_per_side)
        self.assertEqual(binding.evidence_hash, result.account_fee_evidence.evidence_hash)
        self.assertEqual(dict(binding.evidence), result.account_fee_evidence.evidence)
        self.assertEqual("NOT_ASSERTED", result.account_fee_evidence.owner_approval)
        self.assertEqual("UNAVAILABLE", result.net_profitability)
        self.assertEqual("NOT_INFERRED", result.independent_validation)

    def test_all_context_dimensions_and_identity_fail_closed(self):
        binding = binding_for()
        for key, value in (("account_id", "other-account"), ("venue", "other"), ("product", "spot"),
            ("tier", "other-vip"), ("study_content_hash", "other"), ("candidate_set_hash", "other"),
            ("dataset_content_hashes", ("other",)), ("evidence_hash", "tampered")):
            with self.subTest(key=key):
                result = project(replace(binding, **{key: value}))
                self.assertEqual("UNAVAILABLE", result.verified_account_fees)
                self.assertIsNone(result.account_fee_evidence.taker_fee_bps_per_side)
                self.assertIsNone(result.account_fee_evidence.remaining_after_fee_bps_per_side)
                self.assertTrue(result.account_fee_evidence.reasons)
                self.assertTrue(all(s.remaining_after_verified_fee_and_scenario_bps_per_side is None
                                    for s in result.scenarios))

    def test_missing_public_unverified_stale_future_and_incomplete_evidence(self):
        binding = binding_for()
        cases = [None, change_evidence(binding, verification_status="UNVERIFIED"),
            change_evidence(binding, verification_source="PUBLIC_FEE_TABLE"),
            change_evidence(binding, valid_from="2025-08-21T00:00:00Z"),
            change_evidence(binding, valid_until_exclusive="2026-08-18T00:00:00Z"),
            change_evidence(binding, valid_until_exclusive="2026-08-19"),
            change_evidence(binding, fee_schedule={}),
            replace(binding, observation_start=binding.observation_end_exclusive),
            replace(binding, observation_end_exclusive="2026-08-18T00:00:00Z"),
            replace(binding, assessed_at="2026-08-01T00:00:00Z")]
        for key, value in (("verified_on", "2026-10-24"), ("verified_on", "invalid"),
                           ("verified_by", ""), ("source_reference", ""),
                           ("taker_fee_bps", 0.5), ("taker_fee_bps", "1e99999"),
                           ("taker_fee_bps", "NaN"), ("taker_fee_bps", "-1")):
            cases.append(change_evidence(binding, fee_schedule={**binding.evidence["fee_schedule"], key: value}))
        for item in cases:
            with self.subTest(item=item):
                self.assertEqual("UNAVAILABLE", project(item).verified_account_fees)
        self.assertEqual("VERIFIED", project(change_evidence(binding,
            valid_from=binding.observation_start,
            valid_until_exclusive=binding.observation_end_exclusive)).verified_account_fees)

    def test_valid_fee_evidence_cannot_supply_missing_gross_authority(self):
        result = assess(fee_binding=binding_for(), study_content_hash="s", candidate_set_hash="c",
            dataset_content_hashes=("d",), evaluation_end_exclusive="2026-08-19T00:00:00Z", baseline={})
        self.assertEqual("VERIFIED", result.verified_account_fees)
        self.assertEqual("UNAVAILABLE", result.status)
        self.assertIsNone(result.account_fee_evidence.remaining_after_fee_bps_per_side)
        self.assertTrue(all(s.remaining_after_verified_fee_and_scenario_bps_per_side is None
                            for s in result.scenarios))

    def test_comparison_provenance_and_financial_boundaries(self):
        evidence = _evidence({"a": {}}, rerun="ESTABLISHED", decimal={"a": BASE})
        binding = binding_for(evidence.study_content_hash, evidence.candidate_set_hash,
            tuple(d["content_hash"] for d in evidence.study_identity["datasets"]))
        original = compare_candidates_v1([evidence], sort=None)
        result = compare_candidates_v1([replace(evidence, fee_binding=binding)], sort=None)
        row = result.rows[0]
        self.assertEqual("VERIFIED", row.cost_fragility.verified_account_fees)
        self.assertEqual(original.rows[0].identity, row.identity)
        self.assertEqual(original.rows[0].decimal_rerun, row.decimal_rerun)
        self.assertEqual(original.rows[0].provenance, row.provenance)
        self.assertEqual(original.rows[0].economics, row.economics)
        self.assertNotEqual(original.output_hash, result.output_hash)
        self.assertEqual("NOT_READ", result.holdout)
        self.assertFalse(row.promotable)
        self.assertEqual("NONE", row.execution_authority)
        payload = result.payload()
        self.assertEqual(binding.evidence_hash, payload["rows"][0]["cost_fragility"]["account_fee_evidence"]["evidence_hash"])
        self.assertEqual(result.output_hash, compare_candidates_v1([replace(evidence, fee_binding=binding)], sort=None).output_hash)


    def test_existing_reader_binds_by_study_hash_without_choosing_a_fee_default(self):
        from unittest.mock import Mock, patch
        from uuid import UUID

        from trade_platform.research_candidate_comparison_v1 import (
            StudySelectionV1,
            read_candidate_comparison_v1,
        )

        evidence = _evidence({"a": {}}, rerun="ESTABLISHED", decimal={"a": BASE})
        binding = binding_for(evidence.study_content_hash, evidence.candidate_set_hash,
            tuple(d["content_hash"] for d in evidence.study_identity["datasets"]))
        with patch("trade_platform.research_candidate_comparison_v1._load_study", return_value=evidence):
            for installed, status in ((None, "UNAVAILABLE"), ({"other-study": binding}, "UNAVAILABLE"),
                                      ({evidence.study_content_hash: binding}, "VERIFIED")):
                result = read_candidate_comparison_v1(Mock(), studies=[StudySelectionV1(UUID(evidence.study_id))],
                                                      sort=None, fee_bindings=installed)
                self.assertEqual(status, result.rows[0].cost_fragility.verified_account_fees)


    def test_terminal_server_clock_refuses_future_verification(self):
        from datetime import datetime
        from unittest.mock import Mock, patch
        from uuid import UUID

        from trade_platform.research_terminal_read_model_v1 import read_terminal_comparison_v1

        evidence = _evidence({"a": {}}, rerun="ESTABLISHED", decimal={"a": BASE})
        binding = binding_for(evidence.study_content_hash, evidence.candidate_set_hash,
            tuple(d["content_hash"] for d in evidence.study_identity["datasets"]))
        class Projected(Exception):
            pass

        def check_reader(*args, **kwargs):
            installed = kwargs["fee_bindings"][evidence.study_content_hash]
            result = compare_candidates_v1([replace(evidence, fee_binding=installed)], sort=None)
            self.assertEqual("2026-09-01T00:00:00+00:00", installed.assessed_at)
            self.assertEqual("UNAVAILABLE", result.rows[0].cost_fragility.verified_account_fees)
            self.assertIn("FEE_EVIDENCE_STALE_OR_OUTSIDE_OBSERVATION_PERIOD",
                          result.rows[0].cost_fragility.account_fee_evidence.reasons)
            raise Projected

        with patch("trade_platform.research_candidate_comparison_v1.read_candidate_comparison_v1",
                   side_effect=check_reader), self.assertRaises(Projected):
            read_terminal_comparison_v1(Mock(), study_ids=[UUID(evidence.study_id)], sort=None,
                now=datetime.fromisoformat("2026-09-01T00:00:00+00:00"),
                fee_bindings={evidence.study_content_hash: binding})
