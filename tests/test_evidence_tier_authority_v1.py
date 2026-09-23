"""Phase 3Z.1 -- the evidence-tier authority must classify, never flatter.

These tests prove the tier is derived from a closed contract set keyed by
deterministic source identity, that no caller can claim a tier, that a provider
name reaches nothing, and that the canonical Bybit REST evidence resolves to
``T1_RETROSPECTIVE`` and only that. They also pin the identities Phase 3Z.1 is
forbidden to move: the REST provenance contract hash, the dormant Tardis
captured-source identity, the unresolved preregistration blocker and the
untouched holdout.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from trade_platform.canonical_captured_source_authority_v1 import (
    canonical_tardis_captured_bybit_source_contract_v1,
)
from trade_platform.evidence_tier_authority_v1 import (
    EVIDENCE_TIER_AUTHORITY_SCHEMA_VERSION,
    PROFESSIONAL_EVIDENCE_TIERS_V1,
    EvidenceTierAuthorityError,
    EvidenceTierV1,
    EvidenceTierVerdictV1,
    EvidenceTimingContractV1,
    EvidenceTimingFactsV1,
    TimingAuthorityV1,
    authorized_timing_contracts_v1,
    canonical_bybit_rest_timing_contract_v1,
    evaluate_evidence_tier_v1,
    require_conditional_research_tier_v1,
    require_professional_evidence_tier_v1,
)
from trade_platform.open_to_open_preregistration_v1 import (
    UNRESOLVED_FEATURE_DECISION_TIMES,
    OpenToOpenPreregistrationV1Error,
    build_open_to_open_preregistration_v1,
    require_authorized_for_holdout_with_evidence_tier_v1,
)
from trade_platform.open_to_open_validation_orchestration_v1 import (
    derive_open_to_open_evaluation_span_v1,
)
from trade_platform.real_market_data_provenance_v1 import (
    STATUS_REAL_DATA,
    STATUS_SYNTHETIC,
    DatasetLineageFactsV1,
    PersistedSourceFactsV1,
    canonical_bybit_source_contract_v1,
    evaluate_real_market_data_provenance_v1,
)

CONTRACT = canonical_bybit_source_contract_v1()
DATASET_HASH = "c" * 64
CREATED_AT = datetime(2026, 9, 21, 22, 9, tzinfo=UTC)
DATASET_ID = uuid5(NAMESPACE_URL, "3z1-dataset")

#: The canonical 150-day composite. Named here only to assert it is untouched.
CANONICAL_REST_DATASET_ID = UUID("f07ebfc1-19ce-4757-af4b-10525f6a6992")

#: Pinned on main before Phase 3Z.1. If this phase moves either, it has broken
#: an identity it was told to leave byte-for-byte alone.
PINNED_REST_SOURCE_ID = UUID("a337be59-2019-5458-bc17-dd33750fa359")
PINNED_TARDIS_SOURCE_ID = UUID("b4f161e8-3ebd-53f5-bfdb-e527f598b08a")
PINNED_TARDIS_CONTRACT_HASH = (
    "3fda40738e2ee176dcd7df19b9616c33f636f97181dc4b1a8df8330693c2c105"  # pragma: allowlist secret
)


def _source(**overrides: object) -> PersistedSourceFactsV1:
    values: dict[str, object] = {
        "source_id": CONTRACT.source_id,
        "provider": CONTRACT.provider,
        "dataset_name": CONTRACT.dataset_name,
        "provider_identifier_namespace": CONTRACT.provider_identifier_namespace,
        "provider_terms_version": CONTRACT.provider_terms_version,
        "authorization_reference": CONTRACT.authorization_reference,
        "asset_scope": CONTRACT.asset_scope,
        "observation_kinds": CONTRACT.observation_kinds,
    }
    values.update(overrides)
    return PersistedSourceFactsV1(**values)  # type: ignore[arg-type]


def _lineage(**overrides: object) -> DatasetLineageFactsV1:
    values: dict[str, object] = {
        "dataset_version_id": DATASET_ID,
        "found": True,
        "status": "SEALED",
        "version": "bybit-research-composite-v1:fixture",
        "normalization_version": "bybit-v5-md-v1",
        "content_hash": DATASET_HASH,
        "source_id": CONTRACT.source_id,
        "valid_from": datetime(2026, 4, 22, tzinfo=UTC),
        "valid_until": datetime(2026, 9, 19, tzinfo=UTC),
        "created_at": CREATED_AT,
        "source": _source(),
        "member_count": 691200,
        "lineage_complete_member_count": 691200,
        "instrument_ids": ("CRYPTO:BYBIT:BTCUSDT:PERP",),
        "member_count_by_kind": (("MARK_PRICE", 216000),),
    }
    values.update(overrides)
    return DatasetLineageFactsV1(**values)  # type: ignore[arg-type]


def _real_provenance(**overrides: object):
    return evaluate_real_market_data_provenance_v1(_lineage(**overrides))


def _timing_facts(**overrides: object) -> EvidenceTimingFactsV1:
    values: dict[str, object] = {
        "dataset_version_id": DATASET_ID,
        "dataset_content_hash": DATASET_HASH,
        "source_id": CONTRACT.source_id,
        "declared_timing_authority": TimingAuthorityV1.NONE.value,
        "observations_with_knowledge_time": 0,
        "observations_missing_knowledge_time": 0,
        "distinct_knowledge_time_count": 1,
    }
    values.update(overrides)
    return EvidenceTimingFactsV1(**values)  # type: ignore[arg-type]


class TimingContractTests(unittest.TestCase):
    def test_contract_cannot_be_minted_outside_the_authority(self) -> None:
        with self.assertRaises(EvidenceTierAuthorityError):
            EvidenceTimingContractV1(
                schema_version="evidence-timing-contract-v1",
                source_id=CONTRACT.source_id,
                timing_authority=TimingAuthorityV1.PUBLISHER_PUBLICATION_TIMESTAMP.value,
                granted_tier=EvidenceTierV1.T3_PUBLICATION_TIME.value,
                requires_declared_publication_lag=False,
                tier_ceiling_reason=None,
                authorization_reference="forged",
            )

    def test_closed_set_holds_only_the_bybit_rest_timing_contract(self) -> None:
        contracts = authorized_timing_contracts_v1()
        self.assertEqual(1, len(contracts))
        self.assertEqual(CONTRACT.source_id, contracts[0].source_id)
        self.assertEqual(TimingAuthorityV1.NONE.value, contracts[0].timing_authority)
        self.assertEqual(EvidenceTierV1.T1_RETROSPECTIVE.value, contracts[0].granted_tier)

    def test_contract_hash_is_deterministic(self) -> None:
        self.assertEqual(
            canonical_bybit_rest_timing_contract_v1().content_hash(),
            canonical_bybit_rest_timing_contract_v1().content_hash(),
        )

    def test_dormant_tardis_source_is_granted_no_timing_authority(self) -> None:
        tardis = canonical_tardis_captured_bybit_source_contract_v1()
        self.assertNotIn(
            tardis.source_id, {contract.source_id for contract in authorized_timing_contracts_v1()}
        )


class TierDerivationTests(unittest.TestCase):
    def test_canonical_bybit_rest_evidence_resolves_to_t1_only(self) -> None:
        verdict = evaluate_evidence_tier_v1(_timing_facts(), _real_provenance())
        self.assertEqual(EvidenceTierV1.T1_RETROSPECTIVE.value, verdict.tier)
        self.assertEqual((), verdict.reasons)
        self.assertTrue(verdict.provenance_proven_real)
        self.assertTrue(verdict.descriptive_research_eligible)
        self.assertFalse(verdict.professional_evidence_eligible)
        self.assertFalse(verdict.conditional_research_eligible)
        self.assertFalse(verdict.is_professional_evidence())

    def test_verdict_hash_and_evidence_id_are_deterministic(self) -> None:
        first = evaluate_evidence_tier_v1(_timing_facts(), _real_provenance())
        second = evaluate_evidence_tier_v1(_timing_facts(), _real_provenance())
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.evidence_id, second.evidence_id)
        self.assertTrue(first.integrity_verified())

    def test_caller_cannot_claim_a_higher_tier(self) -> None:
        """Declaring publication-time authority over a REST source is refused."""
        verdict = evaluate_evidence_tier_v1(
            _timing_facts(
                declared_timing_authority=(
                    TimingAuthorityV1.PUBLISHER_PUBLICATION_TIMESTAMP.value
                ),
                observations_with_knowledge_time=216000,
                distinct_knowledge_time_count=216000,
            ),
            _real_provenance(),
        )
        self.assertEqual(EvidenceTierV1.T1_RETROSPECTIVE.value, verdict.tier)
        self.assertIn(
            "evidence_tier_declared_timing_authority_not_granted_by_source_contract",
            verdict.reasons,
        )
        self.assertFalse(verdict.professional_evidence_eligible)

    def test_provider_name_alone_grants_nothing(self) -> None:
        """A row calling itself "sec" with an unregistered source id stays T1."""
        foreign = uuid5(NAMESPACE_URL, "pretend-sec-source")
        verdict = evaluate_evidence_tier_v1(
            _timing_facts(
                source_id=foreign,
                declared_timing_authority=(
                    TimingAuthorityV1.PUBLISHER_PUBLICATION_TIMESTAMP.value
                ),
            ),
            _real_provenance(source_id=foreign, source=_source(source_id=foreign, provider="sec")),
        )
        self.assertEqual(EvidenceTierV1.T1_RETROSPECTIVE.value, verdict.tier)
        self.assertIn(
            "evidence_tier_source_has_no_authorized_timing_contract", verdict.reasons
        )
        self.assertFalse(verdict.professional_evidence_eligible)
        self.assertFalse(verdict.descriptive_research_eligible)

    def test_verdict_cannot_be_forged(self) -> None:
        with self.assertRaises(EvidenceTierAuthorityError):
            EvidenceTierVerdictV1(
                schema_version=EVIDENCE_TIER_AUTHORITY_SCHEMA_VERSION,
                tier=EvidenceTierV1.T4_FIRST_PARTY_CAPTURE.value,
                reasons=(),
                dataset_version_id=DATASET_ID,
                dataset_content_hash=DATASET_HASH,
                source_id=CONTRACT.source_id,
                timing_authority=TimingAuthorityV1.PLATFORM_RECORDER_ARRIVAL_TIMESTAMP.value,
                timing_contract_content_hash="d" * 64,
                provenance_evidence_id=None,
                provenance_proven_real=True,
                professional_evidence_eligible=True,
                conditional_research_eligible=False,
                descriptive_research_eligible=True,
                declared_publication_lag_nanos=None,
                publication_lag_assumption_reference=None,
                content_hash="e" * 64,
                evidence_id=DATASET_ID,
            )

    def test_edited_verdict_fails_its_own_integrity_check(self) -> None:
        import dataclasses

        verdict = evaluate_evidence_tier_v1(_timing_facts(), _real_provenance())
        forged = dataclasses.replace(
            verdict,
            tier=EvidenceTierV1.T4_FIRST_PARTY_CAPTURE.value,
            professional_evidence_eligible=True,
        )
        self.assertFalse(forged.integrity_verified())
        self.assertFalse(forged.is_professional_evidence())


class TierSemanticsTests(unittest.TestCase):
    def test_t0_synthetic_is_never_economically_eligible(self) -> None:
        synthetic = _real_provenance(source=_source(provider="demo-synthetic-fixture"))
        self.assertEqual(STATUS_SYNTHETIC, synthetic.status)
        verdict = evaluate_evidence_tier_v1(_timing_facts(), synthetic)
        self.assertEqual(EvidenceTierV1.T0_SYNTHETIC.value, verdict.tier)
        self.assertFalse(verdict.professional_evidence_eligible)
        self.assertFalse(verdict.conditional_research_eligible)
        self.assertFalse(verdict.descriptive_research_eligible)

    def test_tier_requires_a_provenance_verdict_at_all(self) -> None:
        verdict = evaluate_evidence_tier_v1(_timing_facts(), None)
        self.assertIn(
            "evidence_tier_requires_a_real_market_data_provenance_verdict", verdict.reasons
        )
        self.assertFalse(verdict.descriptive_research_eligible)

    def test_evidence_must_be_bound_to_the_same_dataset(self) -> None:
        other = uuid5(NAMESPACE_URL, "another-dataset")
        verdict = evaluate_evidence_tier_v1(
            _timing_facts(dataset_version_id=other), _real_provenance()
        )
        self.assertIn("evidence_tier_provenance_dataset_mismatch", verdict.reasons)
        self.assertFalse(verdict.professional_evidence_eligible)

    def test_content_hash_mismatch_is_refused(self) -> None:
        verdict = evaluate_evidence_tier_v1(
            _timing_facts(dataset_content_hash="9" * 64), _real_provenance()
        )
        self.assertIn("evidence_tier_provenance_content_hash_mismatch", verdict.reasons)

    def test_professional_tiers_are_exactly_t3_and_t4(self) -> None:
        self.assertEqual(
            (EvidenceTierV1.T3_PUBLICATION_TIME, EvidenceTierV1.T4_FIRST_PARTY_CAPTURE),
            PROFESSIONAL_EVIDENCE_TIERS_V1,
        )
        self.assertNotIn(EvidenceTierV1.T0_SYNTHETIC, PROFESSIONAL_EVIDENCE_TIERS_V1)
        self.assertNotIn(EvidenceTierV1.T1_RETROSPECTIVE, PROFESSIONAL_EVIDENCE_TIERS_V1)
        self.assertNotIn(EvidenceTierV1.T2_EVENT_TIME, PROFESSIONAL_EVIDENCE_TIERS_V1)

    def test_lag_assumption_is_refused_outside_event_time(self) -> None:
        verdict = evaluate_evidence_tier_v1(
            _timing_facts(
                declared_publication_lag_nanos=0,
                publication_lag_assumption_reference="zero",
            ),
            _real_provenance(),
        )
        self.assertIn(
            "evidence_tier_publication_lag_assumption_only_applies_to_event_time",
            verdict.reasons,
        )


class HypotheticalTierTests(unittest.TestCase):
    """Tier semantics for authorities not yet registered.

    Phase 3Z.1 registers one source. These prove the *rules* a future T2/T3/T4
    contract will meet, using a locally issued contract, without enrolling any
    source into the shipped closed set.
    """

    @staticmethod
    def _patched(monkey_tier: TimingAuthorityV1):
        from trade_platform import evidence_tier_authority_v1 as module

        # Reaches a private issuer deliberately: the point is to exercise the
        # tier rules a future contract will meet without enrolling any source
        # into the shipped closed set.
        return module._issue_contract(
            source_id=CONTRACT.source_id,
            timing_authority=monkey_tier,
            tier_ceiling_reason=None,
            authorization_reference="test-only",
        )

    def _evaluate(self, authority: TimingAuthorityV1, **fact_overrides: object):
        from trade_platform import evidence_tier_authority_v1 as module

        contract = self._patched(authority)
        original = module.authorized_timing_contracts_v1
        module.authorized_timing_contracts_v1 = lambda: (contract,)  # type: ignore[assignment]
        try:
            return evaluate_evidence_tier_v1(
                _timing_facts(declared_timing_authority=authority.value, **fact_overrides),
                _real_provenance(),
            )
        finally:
            module.authorized_timing_contracts_v1 = original  # type: ignore[assignment]

    def test_t2_without_a_declared_lag_is_not_conditional_eligible(self) -> None:
        verdict = self._evaluate(TimingAuthorityV1.VENUE_EVENT_TIMESTAMP)
        self.assertEqual(EvidenceTierV1.T2_EVENT_TIME.value, verdict.tier)
        self.assertIn(
            "evidence_tier_t2_requires_a_declared_bound_publication_lag_assumption",
            verdict.reasons,
        )
        self.assertFalse(verdict.conditional_research_eligible)

    def test_t2_with_a_declared_lag_is_conditional_but_never_professional(self) -> None:
        verdict = self._evaluate(
            TimingAuthorityV1.VENUE_EVENT_TIMESTAMP,
            declared_publication_lag_nanos=250_000_000,
            publication_lag_assumption_reference="test-only assumption, not an owner decision",
        )
        self.assertEqual(EvidenceTierV1.T2_EVENT_TIME.value, verdict.tier)
        self.assertEqual((), verdict.reasons)
        self.assertTrue(verdict.conditional_research_eligible)
        self.assertTrue(verdict.is_conditional_research_evidence())
        self.assertFalse(verdict.professional_evidence_eligible)
        self.assertFalse(verdict.is_professional_evidence())
        self.assertEqual(250_000_000, verdict.declared_publication_lag_nanos)
        with self.assertRaises(EvidenceTierAuthorityError):
            require_professional_evidence_tier_v1(
                verdict,
                dataset_version_id=DATASET_ID,
                dataset_content_hash=DATASET_HASH,
            )
        require_conditional_research_tier_v1(
            verdict, dataset_version_id=DATASET_ID, dataset_content_hash=DATASET_HASH
        )

    def test_t3_and_t4_may_be_professional_evidence(self) -> None:
        for authority, tier in (
            (TimingAuthorityV1.PUBLISHER_PUBLICATION_TIMESTAMP, EvidenceTierV1.T3_PUBLICATION_TIME),
            (
                TimingAuthorityV1.PLATFORM_RECORDER_ARRIVAL_TIMESTAMP,
                EvidenceTierV1.T4_FIRST_PARTY_CAPTURE,
            ),
        ):
            with self.subTest(authority=authority):
                verdict = self._evaluate(
                    authority,
                    observations_with_knowledge_time=216000,
                    distinct_knowledge_time_count=216000,
                )
                self.assertEqual(tier.value, verdict.tier)
                self.assertEqual((), verdict.reasons)
                self.assertTrue(verdict.is_professional_evidence())
                require_professional_evidence_tier_v1(
                    verdict,
                    dataset_version_id=DATASET_ID,
                    dataset_content_hash=DATASET_HASH,
                )

    def test_collapsed_knowledge_times_block_professional_eligibility(self) -> None:
        verdict = self._evaluate(
            TimingAuthorityV1.PLATFORM_RECORDER_ARRIVAL_TIMESTAMP,
            observations_with_knowledge_time=216000,
            distinct_knowledge_time_count=1,
        )
        self.assertIn("evidence_tier_knowledge_times_collapse_to_one_instant", verdict.reasons)
        self.assertFalse(verdict.is_professional_evidence())

    def test_missing_knowledge_times_block_professional_eligibility(self) -> None:
        verdict = self._evaluate(
            TimingAuthorityV1.PUBLISHER_PUBLICATION_TIMESTAMP,
            observations_with_knowledge_time=10,
            observations_missing_knowledge_time=1,
            distinct_knowledge_time_count=10,
        )
        self.assertIn("evidence_tier_observations_missing_knowledge_time", verdict.reasons)
        self.assertFalse(verdict.is_professional_evidence())

    def test_professional_eligibility_still_requires_real_provenance(self) -> None:
        from trade_platform import evidence_tier_authority_v1 as module

        contract = self._patched(TimingAuthorityV1.PUBLISHER_PUBLICATION_TIMESTAMP)
        original = module.authorized_timing_contracts_v1
        module.authorized_timing_contracts_v1 = lambda: (contract,)  # type: ignore[assignment]
        try:
            verdict = evaluate_evidence_tier_v1(
                _timing_facts(
                    declared_timing_authority=(
                        TimingAuthorityV1.PUBLISHER_PUBLICATION_TIMESTAMP.value
                    ),
                    observations_with_knowledge_time=10,
                    distinct_knowledge_time_count=10,
                ),
                _real_provenance(status="DRAFT"),
            )
        finally:
            module.authorized_timing_contracts_v1 = original  # type: ignore[assignment]
        self.assertNotEqual(STATUS_REAL_DATA, _real_provenance(status="DRAFT").status)
        self.assertFalse(verdict.provenance_proven_real)
        self.assertFalse(verdict.is_professional_evidence())


class _Bar:
    """The two fields ``derive_open_to_open_evaluation_span_v1`` reads."""

    def __init__(self, open_at: datetime) -> None:
        self.bar_open_at = open_at
        self.bar_close_at = open_at + timedelta(minutes=1)


class _BarSeries:
    def __init__(self, first_open: datetime, last_open: datetime) -> None:
        self.bars = (_Bar(first_open), _Bar(last_open))

    def validate(self) -> None:
        return None


def _evaluation_span(days: int = 150):
    start = datetime(2026, 4, 22, tzinfo=UTC)
    last_open = start + timedelta(days=days) - timedelta(minutes=1)
    return derive_open_to_open_evaluation_span_v1(
        bar_series=_BarSeries(start, last_open)  # type: ignore[arg-type]
    )


class ProfessionalGateTests(unittest.TestCase):
    @staticmethod
    def _span():
        return _evaluation_span()

    def _draft_packet(self):
        return build_open_to_open_preregistration_v1(
            dataset_version_id=DATASET_ID,
            dataset_content_hash=DATASET_HASH,
            evaluation_span=self._span(),
            created_at=CREATED_AT,
            market_data_provenance=_real_provenance(),
        )

    def test_t1_evidence_cannot_pass_the_professional_gate(self) -> None:
        packet = self._draft_packet()
        verdict = evaluate_evidence_tier_v1(_timing_facts(), _real_provenance())
        with self.assertRaises(OpenToOpenPreregistrationV1Error):
            require_authorized_for_holdout_with_evidence_tier_v1(packet, verdict)

    def test_gate_is_strictly_stronger_and_requires_a_verdict(self) -> None:
        packet = self._draft_packet()
        with self.assertRaises(TypeError):
            require_authorized_for_holdout_with_evidence_tier_v1(packet)  # type: ignore[call-arg]

    def test_direct_professional_requirement_refuses_t1(self) -> None:
        verdict = evaluate_evidence_tier_v1(_timing_facts(), _real_provenance())
        with self.assertRaises(EvidenceTierAuthorityError):
            require_professional_evidence_tier_v1(
                verdict, dataset_version_id=DATASET_ID, dataset_content_hash=DATASET_HASH
            )


class UnchangedIdentityTests(unittest.TestCase):
    """Phase 3Z.1 is forbidden to move any of these."""

    def test_bybit_rest_source_identity_is_unchanged(self) -> None:
        self.assertEqual(PINNED_REST_SOURCE_ID, CONTRACT.source_id)

    def test_dormant_tardis_identities_are_unchanged(self) -> None:
        tardis = canonical_tardis_captured_bybit_source_contract_v1()
        self.assertEqual(PINNED_TARDIS_SOURCE_ID, tardis.source_id)
        self.assertEqual(PINNED_TARDIS_CONTRACT_HASH, tardis.content_hash())

    def test_rest_provenance_verdict_is_still_issued_unchanged(self) -> None:
        provenance = _real_provenance()
        self.assertEqual(STATUS_REAL_DATA, provenance.status)
        self.assertTrue(provenance.is_proven_real())
        self.assertIsNone(provenance.captured_source_authority_evidence_id)

    def test_preregistration_blocker_remains_unresolved(self) -> None:
        packet = build_open_to_open_preregistration_v1(
            dataset_version_id=CANONICAL_REST_DATASET_ID,
            dataset_content_hash=DATASET_HASH,
            evaluation_span=ProfessionalGateTests._span(),
            created_at=CREATED_AT,
            market_data_provenance=_real_provenance(
                dataset_version_id=CANONICAL_REST_DATASET_ID
            ),
            distinct_feature_decision_at_count=1,
        )
        self.assertIn(UNRESOLVED_FEATURE_DECISION_TIMES, packet.unresolved_reasons)
        self.assertFalse(packet.authorized_for_holdout)

    def test_untouched_holdout_stays_closed(self) -> None:
        packet = ProfessionalGateTests()._draft_packet()
        verdict = evaluate_evidence_tier_v1(_timing_facts(), _real_provenance())
        self.assertFalse(packet.authorized_for_holdout)
        with self.assertRaises(OpenToOpenPreregistrationV1Error):
            require_authorized_for_holdout_with_evidence_tier_v1(packet, verdict)

    def test_this_phase_authorizes_no_new_data_source(self) -> None:
        """One timing contract, over a source that was already authorized."""
        self.assertEqual(
            (CONTRACT.source_id,),
            tuple(contract.source_id for contract in authorized_timing_contracts_v1()),
        )

    def test_no_economic_assumption_is_defaulted(self) -> None:
        """No lag, fee, spread or threshold is invented by this authority."""
        verdict = evaluate_evidence_tier_v1(_timing_facts(), _real_provenance())
        self.assertIsNone(verdict.declared_publication_lag_nanos)
        self.assertIsNone(verdict.publication_lag_assumption_reference)
        self.assertNotIsInstance(verdict.declared_publication_lag_nanos, Decimal)


if __name__ == "__main__":
    unittest.main()
