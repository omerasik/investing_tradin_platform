"""Phase R2A.1 -- the knowledge-time doctrine must refuse, never collapse.

Differential tests over the same observations under every evidence tier:

* T1 (and T0) inputs never yield an admissible decision; they fail closed with
  a principled reason, not by collapsing onto an operational instant.
* Synthetic T2/T3/T4 fixtures yield distinct, correct decision times.
* Recomputing a feature at a different wall time, or re-normalizing it at a
  different platform time, does not move any historical decision time.

The T2/T3/T4 verdicts are issued by the real evidence-tier authority over a
locally issued contract, exactly as ``tests.test_evidence_tier_authority_v1``
does, without enrolling any source into the shipped closed set.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from trade_platform import evidence_tier_authority_v1 as tier_module
from trade_platform.evidence_tier_authority_v1 import (
    EvidenceTierV1,
    EvidenceTierVerdictV1,
    EvidenceTimingFactsV1,
    TimingAuthorityV1,
    evaluate_evidence_tier_v1,
)
from trade_platform.knowledge_time_doctrine_v1 import (
    STRICT_ALL_INPUTS_RULE_V1,
    ArrivalClockBoundV1,
    ClaimCeilingV1,
    DecisionTimeV1,
    DeclaredComputeLatencyV1,
    FeatureKnowledgeV1,
    KnowledgeTimeDoctrineError,
    ObservationKnowledgeV1,
    derive_observation_knowledge_v1,
    historical_decision_time_v1,
    live_decision_time_v1,
    propagate_feature_knowledge_v1,
    require_admissible_decision_v1,
    result_claim_ceiling_v1,
)
from trade_platform.real_market_data_provenance_v1 import (
    DatasetLineageFactsV1,
    PersistedSourceFactsV1,
    canonical_bybit_source_contract_v1,
    evaluate_real_market_data_provenance_v1,
)

CONTRACT = canonical_bybit_source_contract_v1()
DATASET_ID = uuid5(NAMESPACE_URL, "r2a1-dataset")
DATASET_HASH = "d" * 64
SEALED_AT = datetime(2026, 9, 21, 22, 9, 39, tzinfo=UTC)

EVENT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
LAG_NANOS = 250_000_000
LATENCY = DeclaredComputeLatencyV1(1_000_000_000, "test-only declared compute latency")


def _provenance(provider: str = CONTRACT.provider, **overrides: object):
    source = PersistedSourceFactsV1(
        source_id=CONTRACT.source_id,
        provider=provider,
        dataset_name=CONTRACT.dataset_name,
        provider_identifier_namespace=CONTRACT.provider_identifier_namespace,
        provider_terms_version=CONTRACT.provider_terms_version,
        authorization_reference=CONTRACT.authorization_reference,
        asset_scope=CONTRACT.asset_scope,
        observation_kinds=CONTRACT.observation_kinds,
    )
    values: dict[str, object] = {
        "dataset_version_id": DATASET_ID,
        "found": True,
        "status": "SEALED",
        "version": "r2a1-fixture",
        "normalization_version": "bybit-v5-md-v1",
        "content_hash": DATASET_HASH,
        "source_id": CONTRACT.source_id,
        "valid_from": datetime(2026, 4, 22, tzinfo=UTC),
        "valid_until": datetime(2026, 9, 19, tzinfo=UTC),
        "created_at": SEALED_AT,
        "source": source,
        "member_count": 100,
        "lineage_complete_member_count": 100,
        "instrument_ids": ("CRYPTO:BYBIT:BTCUSDT:PERP",),
        "member_count_by_kind": (("MARK_PRICE", 100),),
    }
    values.update(overrides)
    return evaluate_real_market_data_provenance_v1(DatasetLineageFactsV1(**values))  # type: ignore[arg-type]


def _facts(authority: TimingAuthorityV1, **overrides: object) -> EvidenceTimingFactsV1:
    values: dict[str, object] = {
        "dataset_version_id": DATASET_ID,
        "dataset_content_hash": DATASET_HASH,
        "source_id": CONTRACT.source_id,
        "declared_timing_authority": authority.value,
        "observations_with_knowledge_time": 100,
        "observations_missing_knowledge_time": 0,
        "distinct_knowledge_time_count": 100,
    }
    values.update(overrides)
    return EvidenceTimingFactsV1(**values)  # type: ignore[arg-type]


def _verdict(authority: TimingAuthorityV1, **overrides: object) -> EvidenceTierVerdictV1:
    """A real verdict over a locally issued contract. Never enrols a source."""
    if authority is TimingAuthorityV1.NONE:
        return evaluate_evidence_tier_v1(_facts(authority, **overrides), _provenance())
    contract = tier_module._issue_contract(
        source_id=CONTRACT.source_id,
        timing_authority=authority,
        tier_ceiling_reason=None,
        authorization_reference="test-only",
    )
    original = tier_module.authorized_timing_contracts_v1
    tier_module.authorized_timing_contracts_v1 = lambda: (contract,)  # type: ignore[assignment]
    try:
        return evaluate_evidence_tier_v1(_facts(authority, **overrides), _provenance())
    finally:
        tier_module.authorized_timing_contracts_v1 = original  # type: ignore[assignment]


def _t1() -> EvidenceTierVerdictV1:
    return _verdict(TimingAuthorityV1.NONE)


def _t2() -> EvidenceTierVerdictV1:
    return _verdict(
        TimingAuthorityV1.VENUE_EVENT_TIMESTAMP,
        declared_publication_lag_nanos=LAG_NANOS,
        publication_lag_assumption_reference="test-only assumption, not an owner decision",
    )


def _t3() -> EvidenceTierVerdictV1:
    return _verdict(TimingAuthorityV1.PUBLISHER_PUBLICATION_TIMESTAMP)


def _t4() -> EvidenceTierVerdictV1:
    return _verdict(TimingAuthorityV1.PLATFORM_RECORDER_ARRIVAL_TIMESTAMP)


def _bound(nanos: int) -> ArrivalClockBoundV1:
    return ArrivalClockBoundV1(nanos, "session:test lifecycle clock samples")


def _observation(
    verdict: EvidenceTierVerdictV1,
    *,
    event_at: datetime = EVENT,
    reference: str = "obs-1",
    platform_recorded_at: datetime = SEALED_AT,
    dataset_version_id: UUID = DATASET_ID,
    dataset_content_hash: str = DATASET_HASH,
    **clocks: object,
) -> ObservationKnowledgeV1:
    return derive_observation_knowledge_v1(
        verdict,
        dataset_version_id=dataset_version_id,
        dataset_content_hash=dataset_content_hash,
        observation_reference=reference,
        event_at=event_at,
        platform_recorded_at=platform_recorded_at,
        **clocks,  # type: ignore[arg-type]
    )


def _feature(*inputs: ObservationKnowledgeV1, event_at: datetime = EVENT) -> FeatureKnowledgeV1:
    return propagate_feature_knowledge_v1(inputs, event_at=event_at)


class FixtureSanityTests(unittest.TestCase):
    def test_fixture_verdicts_have_the_intended_tiers_and_claims(self) -> None:
        self.assertEqual(EvidenceTierV1.T1_RETROSPECTIVE.value, _t1().tier)
        self.assertTrue(_t1().descriptive_research_eligible)
        self.assertTrue(_t2().is_conditional_research_evidence())
        self.assertTrue(_t3().is_professional_evidence())
        self.assertTrue(_t4().is_professional_evidence())


class ObservationKnowledgeTests(unittest.TestCase):
    def test_t1_knowledge_time_is_undefined_not_the_seal_time(self) -> None:
        knowledge = _observation(_t1())
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.DESCRIPTIVE, knowledge.claim_ceiling)
        self.assertIn("knowledge_time_undefined_for_retrospective_evidence", knowledge.reasons)
        self.assertTrue(knowledge.integrity_verified())

    def test_t1_refuses_a_smuggled_publication_or_arrival_clock(self) -> None:
        with_publication = _observation(_t1(), publication_at=EVENT)
        self.assertIn(
            "knowledge_time_publication_at_supplied_for_a_non_publication_tier",
            with_publication.reasons,
        )
        with_arrival = _observation(_t1(), arrival_at=EVENT, arrival_clock_bound=_bound(0))
        self.assertIn(
            "knowledge_time_arrival_clock_supplied_for_a_non_capture_tier", with_arrival.reasons
        )
        self.assertIsNone(with_arrival.market_knowledge_at)

    def test_t0_synthetic_is_undefined_and_carries_no_claim(self) -> None:
        verdict = evaluate_evidence_tier_v1(
            _facts(TimingAuthorityV1.NONE), _provenance(provider="demo-synthetic-fixture")
        )
        self.assertEqual(EvidenceTierV1.T0_SYNTHETIC.value, verdict.tier)
        knowledge = _observation(verdict)
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.NONE, knowledge.claim_ceiling)

    def test_t2_is_event_time_plus_the_bound_lag(self) -> None:
        knowledge = _observation(_t2())
        self.assertEqual(EVENT + timedelta(milliseconds=250), knowledge.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.CONDITIONAL, knowledge.claim_ceiling)
        self.assertEqual(LAG_NANOS, knowledge.publication_lag_nanos)
        self.assertEqual((), knowledge.reasons)

    def test_t2_without_a_lag_is_undefined(self) -> None:
        verdict = _verdict(TimingAuthorityV1.VENUE_EVENT_TIMESTAMP)
        knowledge = _observation(verdict)
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertIn(
            "knowledge_time_t2_requires_a_bound_publication_lag_verdict", knowledge.reasons
        )

    def test_t2_sub_microsecond_lag_rounds_later_never_earlier(self) -> None:
        verdict = _verdict(
            TimingAuthorityV1.VENUE_EVENT_TIMESTAMP,
            declared_publication_lag_nanos=1,
            publication_lag_assumption_reference="test-only",
        )
        self.assertEqual(EVENT + timedelta(microseconds=1), _observation(verdict).market_knowledge_at)

    def test_t2_negative_lag_is_refused(self) -> None:
        verdict = _verdict(
            TimingAuthorityV1.VENUE_EVENT_TIMESTAMP,
            declared_publication_lag_nanos=-1,
            publication_lag_assumption_reference="test-only",
        )
        knowledge = _observation(verdict)
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertIn(
            "knowledge_time_t2_publication_lag_must_be_non_negative_integer", knowledge.reasons
        )

    def test_t3_is_the_publishers_own_time(self) -> None:
        published = EVENT + timedelta(hours=6)
        knowledge = _observation(_t3(), publication_at=published)
        self.assertEqual(published, knowledge.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.PROFESSIONAL, knowledge.claim_ceiling)
        self.assertEqual("publisher_publication_time", knowledge.knowledge_basis)

    def test_t3_without_publication_time_is_undefined_and_descriptive(self) -> None:
        knowledge = _observation(_t3())
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.DESCRIPTIVE, knowledge.claim_ceiling)
        self.assertIn("knowledge_time_t3_requires_publisher_publication_time", knowledge.reasons)

    def test_t4_adds_the_host_behind_venue_bound_to_arrival(self) -> None:
        # The R1A host read ~9.47 s behind Bybit. Using the raw arrival would
        # make the value knowable before it arrived on the venue timescale.
        arrival = EVENT + timedelta(milliseconds=20)
        bound = 9_470_000_000 + 180_000_000
        knowledge = _observation(
            _t4(),
            arrival_at=arrival,
            arrival_clock_bound=_bound(bound),
            platform_recorded_at=arrival + timedelta(seconds=1),
        )
        self.assertEqual(arrival + timedelta(microseconds=bound // 1000), knowledge.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.PROFESSIONAL, knowledge.claim_ceiling)

    def test_t4_host_ahead_of_venue_adds_nothing(self) -> None:
        arrival = EVENT + timedelta(milliseconds=20)
        knowledge = _observation(
            _t4(), arrival_at=arrival, arrival_clock_bound=_bound(-5_000_000_000),
            platform_recorded_at=arrival,
        )
        self.assertEqual(arrival, knowledge.market_knowledge_at)

    def test_t4_without_clock_evidence_is_undefined_and_descriptive(self) -> None:
        arrival = EVENT + timedelta(milliseconds=20)
        knowledge = _observation(_t4(), arrival_at=arrival, platform_recorded_at=arrival)
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.DESCRIPTIVE, knowledge.claim_ceiling)
        self.assertIn("knowledge_time_t4_arrival_clock_offset_unbounded", knowledge.reasons)

    def test_t4_recorded_before_arrival_on_the_same_host_clock_is_refused(self) -> None:
        arrival = EVENT + timedelta(milliseconds=20)
        knowledge = _observation(
            _t4(), arrival_at=arrival, arrival_clock_bound=_bound(0),
            platform_recorded_at=arrival - timedelta(microseconds=1),
        )
        self.assertIn("knowledge_time_platform_recorded_before_arrival", knowledge.reasons)

    def test_knowledge_before_the_event_is_refused_not_clamped(self) -> None:
        arrival = EVENT - timedelta(seconds=1)
        knowledge = _observation(
            _t4(), arrival_at=arrival, arrival_clock_bound=_bound(0),
            platform_recorded_at=EVENT,
        )
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertIn("knowledge_time_market_knowledge_precedes_event", knowledge.reasons)

    def test_a_collapsed_t4_verdict_carries_no_claim(self) -> None:
        verdict = _verdict(
            TimingAuthorityV1.PLATFORM_RECORDER_ARRIVAL_TIMESTAMP, distinct_knowledge_time_count=1
        )
        arrival = EVENT + timedelta(milliseconds=20)
        knowledge = _observation(
            verdict, arrival_at=arrival, arrival_clock_bound=_bound(0), platform_recorded_at=arrival
        )
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.NONE, knowledge.claim_ceiling)

    def test_a_tampered_verdict_is_refused(self) -> None:
        forged = replace(_t1(), tier=EvidenceTierV1.T4_FIRST_PARTY_CAPTURE.value)
        knowledge = _observation(
            forged, arrival_at=EVENT, arrival_clock_bound=_bound(0), platform_recorded_at=EVENT
        )
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.NONE, knowledge.claim_ceiling)
        self.assertIn("knowledge_time_evidence_tier_verdict_integrity_failed", knowledge.reasons)

    def test_structural_defects_raise(self) -> None:
        with self.assertRaises(KnowledgeTimeDoctrineError):
            _observation(_t1(), event_at=EVENT.replace(tzinfo=None))
        with self.assertRaises(KnowledgeTimeDoctrineError):
            _observation(_t1(), reference=" ")
        with self.assertRaises(KnowledgeTimeDoctrineError):
            _observation(_t4(), arrival_at=EVENT, arrival_clock_bound=ArrivalClockBoundV1(0, " "))

    def test_objects_cannot_be_minted_or_edited(self) -> None:
        knowledge = _observation(_t2())
        with self.assertRaises(KnowledgeTimeDoctrineError):
            ObservationKnowledgeV1(**{
                name: getattr(knowledge, name)
                for name in ObservationKnowledgeV1.__dataclass_fields__
                if name != "_issuer"
            })
        edited = replace(knowledge, market_knowledge_at=EVENT)
        self.assertFalse(edited.integrity_verified())


class PropagationTests(unittest.TestCase):
    def test_feature_knowledge_is_the_maximum_and_claim_the_minimum(self) -> None:
        early = _observation(_t2(), reference="mark")
        late = _observation(
            _t3(), reference="index", publication_at=EVENT + timedelta(seconds=3)
        )
        feature = _feature(early, late)
        self.assertEqual(EVENT + timedelta(seconds=3), feature.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.CONDITIONAL, feature.claim_ceiling)
        self.assertEqual(1, len(feature.publication_lag_bindings))
        self.assertEqual(feature.content_hash, _feature(late, early).content_hash)

    def test_one_undefined_input_makes_the_feature_undefined(self) -> None:
        feature = _feature(
            _observation(_t1(), reference="mark"),
            _observation(_t3(), reference="index", publication_at=EVENT),
        )
        self.assertIsNone(feature.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.DESCRIPTIVE, feature.claim_ceiling)
        self.assertIn("feature_knowledge_input_market_knowledge_undefined", feature.reasons)
        self.assertIn(
            "input:knowledge_time_undefined_for_retrospective_evidence", feature.reasons
        )

    def test_feature_event_after_its_knowledge_is_refused(self) -> None:
        feature = _feature(_observation(_t2()), event_at=EVENT + timedelta(hours=1))
        self.assertIsNone(feature.market_knowledge_at)
        self.assertIn(
            "feature_knowledge_market_knowledge_precedes_feature_event", feature.reasons
        )

    def test_duplicate_input_is_refused(self) -> None:
        item = _observation(_t2())
        self.assertIn("feature_knowledge_duplicate_input", _feature(item, item).reasons)

    def test_empty_or_forged_input_raises(self) -> None:
        with self.assertRaises(KnowledgeTimeDoctrineError):
            propagate_feature_knowledge_v1((), event_at=EVENT)
        forged = replace(_observation(_t2()), market_knowledge_at=EVENT - timedelta(days=1))
        with self.assertRaises(KnowledgeTimeDoctrineError):
            _feature(forged)


class DecisionTimeDifferentialTests(unittest.TestCase):
    """The same economic observation under each tier."""

    def _decision(self, verdict: EvidenceTierVerdictV1, **clocks: Any) -> DecisionTimeV1:
        feature = _feature(_observation(verdict, **clocks))
        return historical_decision_time_v1((feature,), compute_latency=LATENCY)

    def test_t1_input_cannot_produce_an_admissible_decision(self) -> None:
        decision = self._decision(_t1())
        self.assertIsNone(decision.decision_at)
        self.assertFalse(decision.is_admissible())
        self.assertIn("decision_input_has_no_market_knowledge_time", decision.reasons)
        self.assertIn(
            "input:knowledge_time_undefined_for_retrospective_evidence", decision.reasons
        )
        self.assertIn("decision_claim_ceiling_is_descriptive", decision.reasons)
        with self.assertRaisesRegex(KnowledgeTimeDoctrineError, "retrospective"):
            require_admissible_decision_v1(decision, minimum_claim=ClaimCeilingV1.PROFESSIONAL)

    def test_t1_series_refuses_instead_of_collapsing_onto_one_instant(self) -> None:
        # The pre-R2A failure: 216,000 values sharing one seal time. Under the
        # doctrine every one of them is refused with the same principled reason
        # and none gets a decision time at all.
        decisions = [
            self._decision(_t1(), event_at=EVENT + timedelta(minutes=minute))
            for minute in range(5)
        ]
        self.assertTrue(all(item.decision_at is None for item in decisions))
        self.assertTrue(all(not item.is_admissible() for item in decisions))

    def test_t2_t3_t4_produce_distinct_correct_decision_times(self) -> None:
        arrival = EVENT + timedelta(milliseconds=40)
        cases = {
            "T2": (
                self._decision(_t2()),
                EVENT + timedelta(milliseconds=250) + timedelta(seconds=1),
                ClaimCeilingV1.CONDITIONAL,
            ),
            "T3": (
                self._decision(_t3(), publication_at=EVENT + timedelta(minutes=5)),
                EVENT + timedelta(minutes=5, seconds=1),
                ClaimCeilingV1.PROFESSIONAL,
            ),
            "T4": (
                self._decision(
                    _t4(), arrival_at=arrival, arrival_clock_bound=_bound(2_000_000),
                    platform_recorded_at=arrival + timedelta(seconds=30),
                ),
                arrival + timedelta(milliseconds=2) + timedelta(seconds=1),
                ClaimCeilingV1.PROFESSIONAL,
            ),
        }
        for name, (decision, expected, claim) in cases.items():
            with self.subTest(tier=name):
                self.assertTrue(decision.is_admissible(), decision.reasons)
                self.assertEqual(expected, decision.decision_at)
                self.assertEqual(claim, decision.claim_ceiling)
        self.assertEqual(3, len({item[0].decision_at for item in cases.values()}))

    def test_distinct_events_give_distinct_decision_times(self) -> None:
        decisions = {
            self._decision(_t2(), event_at=EVENT + timedelta(minutes=minute)).decision_at
            for minute in range(10)
        }
        self.assertEqual(10, len(decisions))

    def test_conditional_decision_carries_its_lag_binding(self) -> None:
        decision = self._decision(_t2())
        self.assertEqual(1, len(decision.publication_lag_bindings))
        self.assertEqual(LAG_NANOS, decision.publication_lag_bindings[0].publication_lag_nanos)
        with self.assertRaisesRegex(KnowledgeTimeDoctrineError, "below_required"):
            require_admissible_decision_v1(decision, minimum_claim=ClaimCeilingV1.PROFESSIONAL)
        self.assertEqual(
            decision.decision_at,
            require_admissible_decision_v1(decision, minimum_claim=ClaimCeilingV1.CONDITIONAL),
        )


class WallClockInvarianceTests(unittest.TestCase):
    def test_recomputing_later_does_not_move_a_historical_decision_time(self) -> None:
        published = EVENT + timedelta(minutes=5)
        first = _observation(_t3(), publication_at=published, platform_recorded_at=SEALED_AT)
        # Re-normalized and re-sealed a year later on a different host.
        again = _observation(
            _t3(), publication_at=published, platform_recorded_at=SEALED_AT + timedelta(days=365)
        )
        one = historical_decision_time_v1((_feature(first),), compute_latency=LATENCY)
        two = historical_decision_time_v1((_feature(again),), compute_latency=LATENCY)
        self.assertEqual(one.decision_at, two.decision_at)
        self.assertEqual(published + timedelta(seconds=1), one.decision_at)
        # The replay key is stable too, not just the instant.
        self.assertEqual(one.decision_time_id, two.decision_time_id)
        self.assertEqual(first.knowledge_id, again.knowledge_id)
        self.assertNotEqual(first.content_hash, again.content_hash)

    def test_live_decision_time_waits_for_the_computation(self) -> None:
        feature = _feature(_observation(_t3(), publication_at=EVENT + timedelta(minutes=5)))
        late = live_decision_time_v1(
            (feature,), computed_at=EVENT + timedelta(hours=2), computed_at_clock_bound=_bound(0)
        )
        self.assertEqual(EVENT + timedelta(hours=2), late.decision_at)
        self.assertTrue(late.is_admissible())

    def test_live_computation_before_knowledge_is_refused_not_clamped(self) -> None:
        feature = _feature(_observation(_t3(), publication_at=EVENT + timedelta(minutes=5)))
        early = live_decision_time_v1(
            (feature,), computed_at=EVENT, computed_at_clock_bound=_bound(0)
        )
        self.assertIsNone(early.decision_at)
        self.assertIn("live_computed_before_market_knowledge", early.reasons)

    def test_live_computed_at_is_moved_onto_the_venue_timescale(self) -> None:
        # Host 9.47 s behind: a computation read at T on the host finished no
        # later than T + 9.47 s on the venue clock.
        feature = _feature(_observation(_t3(), publication_at=EVENT))
        computed = EVENT + timedelta(seconds=1)
        decision = live_decision_time_v1(
            (feature,), computed_at=computed, computed_at_clock_bound=_bound(9_470_000_000)
        )
        self.assertEqual(computed + timedelta(seconds=9.47), decision.decision_at)

    def test_compute_latency_is_declared_and_bound_into_identity(self) -> None:
        feature = _feature(_observation(_t2()))
        other = DeclaredComputeLatencyV1(2_000_000_000, "test-only alternative")
        one = historical_decision_time_v1((feature,), compute_latency=LATENCY)
        two = historical_decision_time_v1((feature,), compute_latency=other)
        self.assertNotEqual(one.content_hash, two.content_hash)
        assert one.decision_at is not None and two.decision_at is not None
        self.assertEqual(timedelta(seconds=1), two.decision_at - one.decision_at)
        for bad in (
            DeclaredComputeLatencyV1(-1, "x"),
            DeclaredComputeLatencyV1(0, " "),
            DeclaredComputeLatencyV1(True, "x"),  # type: ignore[arg-type]
        ):
            with self.subTest(latency=bad), self.assertRaises(KnowledgeTimeDoctrineError):
                historical_decision_time_v1((feature,), compute_latency=bad)


class ResultClaimCeilingTests(unittest.TestCase):
    def test_strict_rule_binds_execution_marking_inputs_too(self) -> None:
        decision_input = _feature(_observation(_t3(), publication_at=EVENT))
        t1_bars = _feature(_observation(_t1(), reference="bar"))
        ceiling = result_claim_ceiling_v1(
            decision_inputs=(decision_input,), execution_marking_inputs=(t1_bars,)
        )
        self.assertEqual(STRICT_ALL_INPUTS_RULE_V1, ceiling.rule)
        self.assertEqual(ClaimCeilingV1.DESCRIPTIVE, ceiling.claim_ceiling)
        self.assertTrue(ceiling.integrity_verified())
        roles = {role for role, _, _ in ceiling.input_claims}
        self.assertEqual({"DECISION", "EXECUTION_MARKING"}, roles)

    def test_all_professional_inputs_keep_a_professional_ceiling(self) -> None:
        arrival = EVENT + timedelta(milliseconds=5)
        ceiling = result_claim_ceiling_v1(
            decision_inputs=(
                _feature(_observation(_t3(), publication_at=EVENT)),
                _feature(
                    _observation(
                        _t4(), reference="t4", arrival_at=arrival,
                        arrival_clock_bound=_bound(0), platform_recorded_at=arrival,
                    )
                ),
            )
        )
        self.assertEqual(ClaimCeilingV1.PROFESSIONAL, ceiling.claim_ceiling)

    def test_requires_decision_inputs(self) -> None:
        with self.assertRaises(KnowledgeTimeDoctrineError):
            result_claim_ceiling_v1(decision_inputs=())


class ReviewFindingTests(unittest.TestCase):
    """Independent PIT review of R2A.1: identity, binding and ordering defects."""

    def test_verdict_must_belong_to_the_observations_dataset(self) -> None:
        other = uuid5(NAMESPACE_URL, "some-t1-dataset")
        arrival = EVENT + timedelta(milliseconds=5)
        borrowed = _observation(
            _t4(), dataset_version_id=other, arrival_at=arrival,
            arrival_clock_bound=_bound(0), platform_recorded_at=arrival,
        )
        self.assertIsNone(borrowed.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.NONE, borrowed.claim_ceiling)
        self.assertIn("knowledge_time_verdict_dataset_mismatch", borrowed.reasons)
        wrong_bytes = _observation(
            _t4(), dataset_content_hash="e" * 64, arrival_at=arrival,
            arrival_clock_bound=_bound(0), platform_recorded_at=arrival,
        )
        self.assertIn("knowledge_time_verdict_content_hash_mismatch", wrong_bytes.reasons)
        self.assertEqual(ClaimCeilingV1.NONE, wrong_bytes.claim_ceiling)

    def test_intact_verdict_with_reasons_is_not_bound_and_carries_no_claim(self) -> None:
        verdict = _verdict(
            TimingAuthorityV1.PUBLISHER_PUBLICATION_TIMESTAMP,
            declared_timing_authority=TimingAuthorityV1.NONE.value,
        )
        self.assertTrue(verdict.integrity_verified())
        self.assertTrue(verdict.reasons)
        knowledge = _observation(verdict, publication_at=EVENT)
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertEqual(ClaimCeilingV1.NONE, knowledge.claim_ceiling)

    def test_tier_clock_inputs_are_part_of_identity(self) -> None:
        arrival = EVENT + timedelta(milliseconds=5)
        base: dict[str, Any] = {
            "arrival_at": arrival, "platform_recorded_at": arrival + timedelta(seconds=1)
        }
        a = _observation(_t4(), arrival_clock_bound=ArrivalClockBoundV1(1000, "sess A"), **base)
        b = _observation(_t4(), arrival_clock_bound=ArrivalClockBoundV1(1000, "sess B"), **base)
        self.assertEqual(a.market_knowledge_at, b.market_knowledge_at)
        self.assertNotEqual(a.market_content_hash, b.market_content_hash)
        # Same knowledge instant reached from a different arrival and bound.
        c = _observation(
            _t4(), arrival_at=arrival - timedelta(microseconds=1),
            arrival_clock_bound=ArrivalClockBoundV1(2000, "sess A"),
            platform_recorded_at=base["platform_recorded_at"],
        )
        self.assertEqual(a.market_knowledge_at, c.market_knowledge_at)
        self.assertNotEqual(a.market_content_hash, c.market_content_hash)
        p1 = _observation(_t3(), publication_at=EVENT)
        p2 = _observation(_t3(), publication_at=EVENT + timedelta(seconds=1))
        self.assertNotEqual(p1.market_content_hash, p2.market_content_hash)

    def test_identity_does_not_depend_on_input_order(self) -> None:
        t1 = _observation(_t1(), reference="a")
        t3_missing = _observation(_t3(), reference="b")
        one, two = _feature(t1, t3_missing), _feature(t3_missing, t1)
        self.assertEqual(one.content_hash, two.content_hash)
        f_a = _feature(t1)
        f_b = _feature(t3_missing)
        d1 = historical_decision_time_v1((f_a, f_b), compute_latency=LATENCY)
        d2 = historical_decision_time_v1((f_b, f_a), compute_latency=LATENCY)
        self.assertEqual(d1.decision_time_id, d2.decision_time_id)

    def test_identity_does_not_depend_on_the_written_utc_offset(self) -> None:
        plus_three = timezone(timedelta(hours=3))
        utc = _observation(_t3(), publication_at=EVENT)
        local = _observation(
            _t3(), event_at=EVENT.astimezone(plus_three),
            publication_at=EVENT.astimezone(plus_three),
            platform_recorded_at=SEALED_AT.astimezone(plus_three),
        )
        self.assertEqual(utc.content_hash, local.content_hash)

    def test_non_integer_t2_lag_is_refused(self) -> None:
        for lag in (True, 1500.7):
            with self.subTest(lag=lag):
                verdict = _verdict(
                    TimingAuthorityV1.VENUE_EVENT_TIMESTAMP,
                    declared_publication_lag_nanos=lag,
                    publication_lag_assumption_reference="test-only",
                )
                knowledge = _observation(verdict)
                self.assertIsNone(knowledge.market_knowledge_at)
                self.assertIn(
                    "knowledge_time_t2_publication_lag_must_be_non_negative_integer",
                    knowledge.reasons,
                )

    def test_zero_t2_lag_is_the_event_itself(self) -> None:
        verdict = _verdict(
            TimingAuthorityV1.VENUE_EVENT_TIMESTAMP,
            declared_publication_lag_nanos=0,
            publication_lag_assumption_reference="test-only",
        )
        self.assertEqual(EVENT, _observation(verdict).market_knowledge_at)

    def test_duplicate_features_are_refused_in_decisions_and_results(self) -> None:
        feature = _feature(_observation(_t2()))
        decision = historical_decision_time_v1((feature, feature), compute_latency=LATENCY)
        self.assertFalse(decision.is_admissible())
        self.assertIn("decision_duplicate_feature_input", decision.reasons)
        with self.assertRaises(KnowledgeTimeDoctrineError):
            result_claim_ceiling_v1(decision_inputs=(feature,), execution_marking_inputs=(feature,))

    def test_multi_feature_decision_takes_latest_knowledge_and_lowest_claim(self) -> None:
        conditional_early = _feature(_observation(_t2(), reference="t2"))
        professional_late = _feature(
            _observation(_t3(), reference="t3", publication_at=EVENT + timedelta(minutes=10))
        )
        decision = historical_decision_time_v1(
            (conditional_early, professional_late), compute_latency=LATENCY
        )
        self.assertEqual(EVENT + timedelta(minutes=10, seconds=1), decision.decision_at)
        self.assertEqual(ClaimCeilingV1.CONDITIONAL, decision.claim_ceiling)

    def test_unknown_tier_is_refused(self) -> None:
        forged = replace(_t4(), tier="T5_ORACLE")
        knowledge = _observation(forged)
        self.assertIsNone(knowledge.market_knowledge_at)
        self.assertIn("knowledge_time_unknown_evidence_tier", knowledge.reasons)


if __name__ == "__main__":
    unittest.main()
