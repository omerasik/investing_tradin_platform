"""Phase R2A.2 -- the three-clock doctrine wired into feature values and decisions.

Pure tests (no database). The Postgres half lives in
``tests.test_feature_three_clock_v3_postgres``. Verdicts are issued by the real
evidence-tier authority over locally issued contracts (the R2A.1 fixtures), so
no source is enrolled into the shipped closed set.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from tests.test_knowledge_time_doctrine_v1 import (
    DATASET_HASH,
    DATASET_ID,
    EVENT,
    LAG_NANOS,
    LATENCY,
    SEALED_AT,
    _bound,
    _observation,
    _provenance,
    _t1,
    _t2,
    _t3,
    _t4,
)
from tests.test_open_to_open_preregistration_v1 import _authorized_inputs, _span
from trade_platform import feature_authority as feature_module
from trade_platform.evidence_tier_authority_v1 import EvidenceTierAuthorityError
from trade_platform.feature_authority import (
    FeatureAuthorityError,
    FeatureMaterializationV2,
    FeatureMaterializationV3,
    FeatureQualityStatus,
    FeatureSubjectType,
)
from trade_platform.knowledge_time_doctrine_v1 import (
    ClaimCeilingV1,
    DeclaredComputeLatencyV1,
    KnowledgeTimeDoctrineError,
    ObservationKnowledgeV1,
    SealedObservationClocksV1,
    historical_decision_time_v1,
    restore_persisted_feature_knowledge_v1,
    result_claim_ceiling_v1,
)
from trade_platform.open_to_open_preregistration_v1 import (
    OpenToOpenPreregistrationV1Error,
    build_open_to_open_preregistration_v1,
    require_professional_historical_decisions_v1,
)
from trade_platform.open_to_open_validation_orchestration_v1 import (
    OpenToOpenValidationOrchestrationV1Error,
    canonical_feature_decision_at,
    count_distinct_historical_decision_times_v1,
    historical_feature_decision_v1,
    legacy_platform_availability_at_v2,
)

FEATURE_ID = uuid5(NAMESPACE_URL, "r2a2-fixture-basis-v3")
SUBJECT = "CRYPTO:BYBIT:BTCUSDT:PERP"
BAR = timedelta(minutes=1)
#: A bar-derived observation is complete at its close, one bar after the open.
CLOSE = EVENT + BAR
PUBLISHED = CLOSE + timedelta(seconds=3)
ARRIVAL = CLOSE + timedelta(milliseconds=180)
#: Measured venue-minus-host bound for one session, e.g. a host ~9.47 s behind.
BOUND_NANOS = 9_650_000_000
MANIFEST = ("historical_dataset_version_id:fixture", "mark:1", "index:2")
#: Genuine verdicts, keyed as a decision path receives them.
TIERS = {verdict.evidence_id: verdict for verdict in (_t1(), _t2(), _t3(), _t4())}


#: The fixtures' "sealed evidence": the clock facts each observation was really
#: recorded with, keyed by reference. Verification resolves facts from here,
#: never from the row being verified.
SEALED: dict[str, SealedObservationClocksV1] = {}


class _FixtureSealedResolver:
    def __call__(
        self, dataset_version_id: Any, references: Any
    ) -> dict[str, SealedObservationClocksV1]:
        if dataset_version_id != DATASET_ID:
            return {}
        return {reference: SEALED[reference] for reference in references if reference in SEALED}


RESOLVER = _FixtureSealedResolver()


@contextmanager
def _fixture_resolver_authorized() -> Iterator[None]:
    """Admit the fixture resolver to the professional gate, as a registered one would be."""
    original = feature_module.authorized_sealed_clock_resolver_types_v1
    feature_module.authorized_sealed_clock_resolver_types_v1 = (  # type: ignore[assignment]
        lambda: (_FixtureSealedResolver,)
    )
    try:
        yield
    finally:
        feature_module.authorized_sealed_clock_resolver_types_v1 = original  # type: ignore[assignment]


#: The fixture resolver is admitted for this module (as a registered resolver
#: would be); one test withdraws it to prove unregistered resolvers refuse.
_MODULE_AUTHORIZATION = ExitStack()


def setUpModule() -> None:
    _MODULE_AUTHORIZATION.enter_context(_fixture_resolver_authorized())


def tearDownModule() -> None:
    _MODULE_AUTHORIZATION.close()


def _obs(verdict: Any, reference: str, **clocks: Any) -> ObservationKnowledgeV1:
    clocks.setdefault("event_at", CLOSE)
    facts = SealedObservationClocksV1(
        event_at=clocks["event_at"],
        platform_recorded_at=clocks.get("platform_recorded_at", SEALED_AT),
        publication_at=clocks.get("publication_at"),
        arrival_at=clocks.get("arrival_at"),
        arrival_clock_bound=clocks.get("arrival_clock_bound"),
    )
    # One reference per distinct set of market facts (platform time excluded,
    # as in the doctrine's market identity), so sealed facts never collide.
    fingerprint = hashlib.sha256(repr(replace(facts, platform_recorded_at=SEALED_AT)).encode())
    reference = f"{reference}|{fingerprint.hexdigest()[:16]}"
    SEALED[reference] = facts
    return _observation(verdict, reference=reference, **clocks)


def _materialize(
    *inputs: ObservationKnowledgeV1,
    event_at: datetime = EVENT,
    computed_at: datetime | None = None,
    value: Decimal = Decimal("0.000512345678"),
) -> FeatureMaterializationV3:
    recorded = max(item.platform_recorded_at for item in inputs)
    return FeatureMaterializationV3.create(
        feature_id=FEATURE_ID, subject_type=FeatureSubjectType.INSTRUMENT, subject_id=SUBJECT,
        dataset_version=str(DATASET_ID), event_at=event_at, effective_at=event_at + BAR,
        inputs=inputs,
        computed_at=recorded if computed_at is None else computed_at,
        source_observation_manifest=MANIFEST, value=value,
        quality_status=FeatureQualityStatus.VALIDATED,
    )


def _t4_pair(event_at: datetime = EVENT, **overrides: Any) -> FeatureMaterializationV3:
    close = event_at + BAR
    clocks = {"arrival_at": close + timedelta(milliseconds=180), "arrival_clock_bound": _bound(BOUND_NANOS)}
    clocks.update(overrides)
    return _materialize(
        _obs(_t4(), f"mark@{event_at.isoformat()}", event_at=close, **clocks),
        _obs(_t4(), f"index@{event_at.isoformat()}", event_at=close, **clocks),
        event_at=event_at,
    )


class LegacyIdentityPreservationTests(unittest.TestCase):
    """Existing V2 identities and 3D.9A packet identities must not move."""

    def test_v2_content_hash_formula_is_unchanged(self) -> None:
        row = FeatureMaterializationV2.create(
            feature_id=FEATURE_ID, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=SUBJECT, dataset_version=str(DATASET_ID), event_at=EVENT,
            effective_at=CLOSE, knowledge_at=SEALED_AT, computed_at=SEALED_AT,
            source_observation_manifest=MANIFEST, value=Decimal("0.000512345678"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        self.assertEqual(PINNED_V2_HASH, row.content_hash)

    def test_v2_decision_ordering_is_the_unchanged_platform_instant(self) -> None:
        row = FeatureMaterializationV2.create(
            feature_id=FEATURE_ID, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=SUBJECT, dataset_version=str(DATASET_ID), event_at=EVENT,
            effective_at=CLOSE, knowledge_at=SEALED_AT,
            computed_at=SEALED_AT + timedelta(minutes=2), source_observation_manifest=MANIFEST,
            value=Decimal("0.0005"), quality_status=FeatureQualityStatus.VALIDATED,
        )
        self.assertEqual(SEALED_AT + timedelta(minutes=2), canonical_feature_decision_at(row))
        self.assertEqual(canonical_feature_decision_at(row), legacy_platform_availability_at_v2(row))

    def test_v2_value_refuses_a_market_decision(self) -> None:
        row = FeatureMaterializationV2.create(
            feature_id=FEATURE_ID, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=SUBJECT, dataset_version=str(DATASET_ID), event_at=EVENT,
            effective_at=CLOSE, knowledge_at=SEALED_AT, computed_at=SEALED_AT,
            source_observation_manifest=MANIFEST, value=Decimal("0.0005"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        with self.assertRaisesRegex(OpenToOpenValidationOrchestrationV1Error, "no_market_knowledge"):
            canonical_feature_decision_at(row, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER)
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            historical_feature_decision_v1(row, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER)  # type: ignore[arg-type]

    def test_preregistration_packet_identity_is_unchanged(self) -> None:
        packet = build_open_to_open_preregistration_v1(
            dataset_version_id=DATASET_ID, dataset_content_hash=DATASET_HASH,
            evaluation_span=_span(), created_at=SEALED_AT,
            market_data_provenance=_provenance(), distinct_feature_decision_at_count=1,
        )
        self.assertEqual(PINNED_DRAFT_PACKET_HASH, packet.content_hash)


class T1CannotDecideTests(unittest.TestCase):
    def test_t1_value_has_no_market_knowledge_and_only_a_descriptive_claim(self) -> None:
        row = _materialize(_obs(_t1(), "mark"), _obs(_t1(), "index"))
        row.validate()
        self.assertIsNone(row.market_knowledge_at)
        self.assertIs(ClaimCeilingV1.DESCRIPTIVE, row.claim_ceiling)
        # The old instant survives only under its honest name.
        self.assertEqual(SEALED_AT, row.platform_recorded_at)
        self.assertEqual(SEALED_AT, row.knowledge_at)

    def test_t1_value_cannot_drive_a_historical_decision(self) -> None:
        row = _materialize(_obs(_t1(), "mark"), _obs(_t1(), "index"))
        decision = historical_feature_decision_v1(row, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER)
        self.assertIsNone(decision.decision_at)
        self.assertIn("input:knowledge_time_undefined_for_retrospective_evidence", decision.reasons)
        with self.assertRaisesRegex(OpenToOpenValidationOrchestrationV1Error, "not_admissible"):
            canonical_feature_decision_at(row, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER)
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            count_distinct_historical_decision_times_v1(
                (row,), compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER, minimum_claim=ClaimCeilingV1.CONDITIONAL
            )

    def test_a_v3_decision_needs_a_declared_latency(self) -> None:
        with self.assertRaisesRegex(OpenToOpenValidationOrchestrationV1Error, "compute_latency"):
            canonical_feature_decision_at(_t4_pair())

    def test_one_t1_input_makes_the_whole_value_undecidable(self) -> None:
        row = _materialize(
            _obs(_t4(), "mark", arrival_at=ARRIVAL, arrival_clock_bound=_bound(BOUND_NANOS)),
            _obs(_t1(), "index"),
        )
        self.assertIsNone(row.market_knowledge_at)
        self.assertIs(ClaimCeilingV1.DESCRIPTIVE, row.claim_ceiling)


class DistinctCausalDecisionTests(unittest.TestCase):
    def _decision(self, row: FeatureMaterializationV3) -> datetime:
        return canonical_feature_decision_at(row, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER)

    def test_t2_t3_t4_decisions_are_distinct_and_causal(self) -> None:
        t2 = _materialize(_obs(_t2(), "mark"), _obs(_t2(), "index"))
        t3 = _materialize(
            _obs(_t3(), "mark", publication_at=PUBLISHED),
            _obs(_t3(), "index", publication_at=PUBLISHED),
        )
        t4 = _t4_pair()
        latency = timedelta(microseconds=LATENCY.latency_nanos // 1000)
        self.assertEqual(CLOSE + timedelta(microseconds=LAG_NANOS // 1000) + latency, self._decision(t2))
        self.assertEqual(PUBLISHED + latency, self._decision(t3))
        self.assertEqual(
            ARRIVAL + timedelta(microseconds=BOUND_NANOS // 1000) + latency, self._decision(t4)
        )
        decisions = {self._decision(row) for row in (t2, t3, t4)}
        self.assertEqual(3, len(decisions))
        for row in (t2, t3, t4):
            # Causal: never before the bar closed, let alone before it opened.
            self.assertGreater(self._decision(row), CLOSE)
            assert row.market_knowledge_at is not None
            self.assertGreaterEqual(row.market_knowledge_at, CLOSE)
        self.assertIs(ClaimCeilingV1.CONDITIONAL, t2.claim_ceiling)
        self.assertIs(ClaimCeilingV1.PROFESSIONAL, t4.claim_ceiling)

    def test_consecutive_bars_carry_distinct_decision_times(self) -> None:
        rows = [_t4_pair(EVENT + index * BAR) for index in range(5)]
        self.assertEqual(
            5,
            count_distinct_historical_decision_times_v1(
                rows, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER, minimum_claim=ClaimCeilingV1.PROFESSIONAL
            ),
        )

    def test_the_host_clock_bound_is_session_evidence_not_a_constant(self) -> None:
        tight = _t4_pair(arrival_clock_bound=_bound(40_000_000))
        loose = _t4_pair()
        self.assertLess(self._decision(tight), self._decision(loose))
        unbounded = _materialize(
            _obs(_t4(), "mark", arrival_at=ARRIVAL), _obs(_t4(), "index", arrival_at=ARRIVAL)
        )
        self.assertIsNone(unbounded.market_knowledge_at)
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            self._decision(unbounded)


class OperationalClocksDoNotMoveHistoryTests(unittest.TestCase):
    def test_recomputing_later_is_the_same_row_and_the_same_decision(self) -> None:
        first = _t4_pair()
        later = replace(first, computed_at=first.computed_at + timedelta(days=7))
        recomputed = _materialize(
            *_t4_inputs(), computed_at=first.computed_at + timedelta(days=7)
        )
        self.assertEqual(first.content_hash, recomputed.content_hash)
        later.validate()
        self.assertEqual(
            canonical_feature_decision_at(first, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER),
            canonical_feature_decision_at(recomputed, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER),
        )

    def test_renormalizing_later_moves_no_market_knowledge(self) -> None:
        early = _t4_pair()
        late_recorded = SEALED_AT + timedelta(days=30)
        late = _materialize(
            *_t4_inputs(platform_recorded_at=late_recorded),
        )
        self.assertEqual(late_recorded, late.platform_recorded_at)
        self.assertEqual(early.market_knowledge_at, late.market_knowledge_at)
        self.assertEqual(early.feature_knowledge_hash, late.feature_knowledge_hash)
        self.assertEqual(early.content_hash, late.content_hash)
        self.assertEqual(
            historical_feature_decision_v1(early, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER).decision_time_id,
            historical_feature_decision_v1(late, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER).decision_time_id,
        )

    def test_seal_or_storage_time_cannot_make_t1_evidence_known(self) -> None:
        for recorded in (SEALED_AT, SEALED_AT + timedelta(days=365)):
            row = _materialize(
                _obs(_t1(), "mark", platform_recorded_at=recorded),
                _obs(_t1(), "index", platform_recorded_at=recorded),
            )
            self.assertIsNone(row.market_knowledge_at)
            self.assertIsNone(historical_feature_decision_v1(row, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER).decision_at)


class PropagationTests(unittest.TestCase):
    def test_feature_knowledge_is_the_max_of_its_actual_inputs(self) -> None:
        later_arrival = ARRIVAL + timedelta(seconds=2)
        row = _materialize(
            _obs(_t4(), "mark", arrival_at=ARRIVAL, arrival_clock_bound=_bound(BOUND_NANOS)),
            _obs(_t4(), "index", arrival_at=later_arrival, arrival_clock_bound=_bound(BOUND_NANOS)),
        )
        self.assertEqual(later_arrival + timedelta(microseconds=BOUND_NANOS // 1000), row.market_knowledge_at)

    def test_claim_ceiling_is_the_minimum_of_its_inputs(self) -> None:
        row = _materialize(
            _obs(_t4(), "mark", arrival_at=ARRIVAL, arrival_clock_bound=_bound(BOUND_NANOS)),
            _obs(_t2(), "index"),
        )
        self.assertIs(ClaimCeilingV1.CONDITIONAL, row.claim_ceiling)
        self.assertIsNotNone(row.market_knowledge_at)


class PersistedKnowledgeIntegrityTests(unittest.TestCase):
    def test_round_trip_restores_the_identical_doctrine_object(self) -> None:
        row = _t4_pair()
        restored = row.integrity_checked_knowledge_v1()
        self.assertEqual(row.feature_knowledge_hash, restored.market_content_hash)
        self.assertEqual(row.market_knowledge_at, restored.market_knowledge_at)

    def test_an_earlier_knowledge_time_in_the_payload_is_refused(self) -> None:
        row = _t4_pair()
        forged = dict(row.feature_knowledge)
        forged["market_knowledge_at"] = CLOSE.isoformat()
        with self.assertRaises(FeatureAuthorityError):
            replace(row, feature_knowledge=forged).validate()
        with self.assertRaises(FeatureAuthorityError):
            replace(row, market_knowledge_at=CLOSE).validate()

    def test_an_upgraded_claim_is_refused(self) -> None:
        row = _materialize(_obs(_t1(), "mark"), _obs(_t1(), "index"))
        with self.assertRaises(FeatureAuthorityError):
            replace(row, claim_ceiling=ClaimCeilingV1.PROFESSIONAL).validate()

    def test_restore_refuses_incoherent_payloads(self) -> None:
        row = _materialize(_obs(_t1(), "mark"), _obs(_t1(), "index"))
        payload = dict(row.feature_knowledge)
        payload["reasons"] = []
        with self.assertRaises(KnowledgeTimeDoctrineError):
            restore_persisted_feature_knowledge_v1(
                payload, platform_recorded_at=SEALED_AT,
                expected_market_content_hash=row.feature_knowledge_hash,
            )


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _forged_professional(row: FeatureMaterializationV3, verdict_id: Any = None) -> FeatureMaterializationV3:
    """A fully self-consistent row claiming PROFESSIONAL knowledge it was never derived to."""
    known = CLOSE.isoformat()
    inputs = []
    for item in row.knowledge_inputs:
        market = dict(item["market"])
        market.update(
            claim_ceiling="PROFESSIONAL", market_knowledge_at=known, reasons=[],
            knowledge_basis="recorder_arrival_plus_venue_clock_bound",
        )
        if verdict_id is not None:
            market["verdict_evidence_id"] = str(verdict_id)
        inputs.append({"market": market, "platform_recorded_at": item["platform_recorded_at"]})
    inputs.sort(key=lambda item: _sha(item["market"]))
    knowledge = dict(row.feature_knowledge)
    knowledge.update(
        input_knowledge_hashes=[_sha(item["market"]) for item in inputs],
        market_knowledge_at=known, claim_ceiling="PROFESSIONAL", reasons=[],
    )
    knowledge_hash = _sha(knowledge)
    content_hash = FeatureMaterializationV3._hash(
        feature_id=row.feature_id, subject_type=row.subject_type, subject_id=row.subject_id,
        dataset_version=row.dataset_version, event_at=row.event_at, effective_at=row.effective_at,
        market_knowledge_at=CLOSE, claim_ceiling=ClaimCeilingV1.PROFESSIONAL,
        feature_knowledge_hash=knowledge_hash,
        source_observation_manifest=row.source_observation_manifest, value=row.value,
        quality_status=row.quality_status,
    )
    return replace(
        row, knowledge_inputs=tuple(inputs), feature_knowledge=knowledge,
        feature_knowledge_hash=knowledge_hash, market_knowledge_at=CLOSE,
        claim_ceiling=ClaimCeilingV1.PROFESSIONAL, content_hash=content_hash,
    )


class ForgeryTests(unittest.TestCase):
    """Integrity is not provenance: a self-consistent forged row must still refuse."""

    def test_a_forged_row_is_self_consistent_yet_never_decides(self) -> None:
        t1_row = _materialize(_obs(_t1(), "mark"), _obs(_t1(), "index"))
        forged = _forged_professional(t1_row)
        forged.validate()  # integrity alone cannot tell -- which is the point
        with self.assertRaises(FeatureAuthorityError):
            forged.verified_feature_knowledge_v1(TIERS, RESOLVER)
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            canonical_feature_decision_at(forged, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER)

    def test_a_forged_row_naming_a_genuine_t4_verdict_still_refuses(self) -> None:
        t4 = _t4()
        forged = _forged_professional(
            _materialize(_obs(_t1(), "mark"), _obs(_t1(), "index")), verdict_id=t4.evidence_id
        )
        with self.assertRaises(FeatureAuthorityError):
            forged.verified_feature_knowledge_v1({t4.evidence_id: t4}, RESOLVER)

    def test_a_forged_earlier_arrival_is_caught(self) -> None:
        row = _t4_pair()
        tampered_inputs = []
        for item in row.knowledge_inputs:
            market = dict(item["market"])
            market["arrival_at"] = (ARRIVAL - timedelta(seconds=30)).isoformat()
            tampered_inputs.append({"market": market, "platform_recorded_at": item["platform_recorded_at"]})
        with self.assertRaises(FeatureAuthorityError):
            replace(row, knowledge_inputs=tuple(tampered_inputs)).verified_feature_knowledge_v1(TIERS, RESOLVER)

    def test_a_genuine_row_verifies_only_against_its_own_verdict(self) -> None:
        row = _t4_pair()
        self.assertEqual(
            row.feature_knowledge_hash, row.verified_feature_knowledge_v1(TIERS, RESOLVER).market_content_hash
        )
        t2 = _t2()
        with self.assertRaises(FeatureAuthorityError):
            row.verified_feature_knowledge_v1({t2.evidence_id: t2}, RESOLVER)

    def test_the_gate_refuses_a_forged_row(self) -> None:
        packet = ProfessionalGateTests()._packet()
        t4 = _t4()
        forged = [
            _forged_professional(
                _materialize(_obs(_t1(), f"m{i}"), _obs(_t1(), f"i{i}"), event_at=EVENT + i * BAR),
                verdict_id=t4.evidence_id,
            )
            for i in range(2)
        ]
        with self.assertRaisesRegex(OpenToOpenPreregistrationV1Error, "not_admissible"):
            require_professional_historical_decisions_v1(
                packet, t4, forged, compute_latency=LATENCY, clock_resolver=RESOLVER
            )

    def test_invented_clock_facts_rederived_with_a_genuine_verdict_refuse(self) -> None:
        """Second-round review: a *correctly derived* row on invented T4 clock facts."""
        genuine = _t4_pair()
        references = [
            str(item["market"]["observation_reference"]) for item in genuine.knowledge_inputs
        ]
        # Same sealed observations, but the forger claims arrival at the close and
        # a zero clock bound (the real host ran ~9.47 s behind the venue).
        forged_inputs = [
            _observation(
                _t4(), reference=reference, event_at=CLOSE, arrival_at=CLOSE,
                arrival_clock_bound=_bound(0).__class__(0, "invented-clock-evidence"),
            )
            for reference in references
        ]
        forged = _materialize(*forged_inputs)
        forged.validate()
        self.assertIs(ClaimCeilingV1.PROFESSIONAL, forged.claim_ceiling)
        self.assertLess(forged.market_knowledge_at, genuine.market_knowledge_at)  # type: ignore[operator]
        with self.assertRaises(FeatureAuthorityError):
            forged.verified_feature_knowledge_v1(TIERS, RESOLVER)
        with self.assertRaises(OpenToOpenValidationOrchestrationV1Error):
            canonical_feature_decision_at(
                forged, compute_latency=LATENCY, evidence_tiers=TIERS, clock_resolver=RESOLVER
            )

    def test_an_observation_absent_from_sealed_evidence_refuses(self) -> None:
        row = _t4_pair()
        with self.assertRaisesRegex(FeatureAuthorityError, "sealed_evidence"):
            row.verified_feature_knowledge_v1(TIERS, lambda dataset, references: {})

    def test_integrity_restored_knowledge_cannot_decide_or_claim(self) -> None:
        restored = _t4_pair().integrity_checked_knowledge_v1()
        with self.assertRaises(KnowledgeTimeDoctrineError):
            historical_decision_time_v1((restored,), compute_latency=LATENCY)
        with self.assertRaises(KnowledgeTimeDoctrineError):
            result_claim_ceiling_v1(decision_inputs=(restored,))

    def test_an_unregistered_resolver_backs_no_decision_at_all(self) -> None:
        packet = ProfessionalGateTests()._packet()
        rows = (_t4_pair(EVENT), _t4_pair(EVENT + BAR))
        _MODULE_AUTHORIZATION.close()
        try:
            with self.assertRaisesRegex(
                OpenToOpenPreregistrationV1Error, "resolver_not_authorized"
            ):
                require_professional_historical_decisions_v1(
                    packet, _t4(), rows, compute_latency=LATENCY, clock_resolver=RESOLVER
                )
            with self.assertRaisesRegex(
                OpenToOpenValidationOrchestrationV1Error, "resolver_not_authorized"
            ):
                count_distinct_historical_decision_times_v1(
                    rows, compute_latency=LATENCY, evidence_tiers=TIERS,
                    clock_resolver=RESOLVER, minimum_claim=ClaimCeilingV1.PROFESSIONAL,
                )
        finally:
            _MODULE_AUTHORIZATION.enter_context(_fixture_resolver_authorized())

    def test_knowledge_before_completion_is_refused(self) -> None:
        row = _t4_pair()
        with self.assertRaises(FeatureAuthorityError):
            replace(row, effective_at=row.market_knowledge_at + timedelta(seconds=1)).validate()  # type: ignore[operator]


class ProfessionalGateTests(unittest.TestCase):
    def _packet(self, count: int = 2) -> Any:
        inputs = _authorized_inputs()
        inputs["market_data_provenance"] = _provenance()
        inputs["distinct_feature_decision_at_count"] = count
        return build_open_to_open_preregistration_v1(
            dataset_version_id=DATASET_ID, dataset_content_hash=DATASET_HASH,
            evaluation_span=_span(), created_at=SEALED_AT, **inputs,
        )

    def test_t4_three_clock_values_pass(self) -> None:
        packet = self._packet()
        self.assertTrue(packet.authorized_for_holdout)
        rows = (_t4_pair(EVENT), _t4_pair(EVENT + BAR))
        self.assertIsNone(
            require_professional_historical_decisions_v1(
                packet, _t4(), rows, compute_latency=LATENCY, clock_resolver=RESOLVER
            )
        )

    def test_t1_t2_and_legacy_values_refuse(self) -> None:
        packet = self._packet()
        t1 = (_materialize(_obs(_t1(), "m"), _obs(_t1(), "i")),)
        t2 = (_materialize(_obs(_t2(), "m"), _obs(_t2(), "i")),)
        for rows in (t1, t2):
            with self.assertRaisesRegex(OpenToOpenPreregistrationV1Error, "not_admissible"):
                require_professional_historical_decisions_v1(
                    packet, _t4(), rows, compute_latency=LATENCY, clock_resolver=RESOLVER
                )
        legacy = FeatureMaterializationV2.create(
            feature_id=FEATURE_ID, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=SUBJECT, dataset_version=str(DATASET_ID), event_at=EVENT,
            effective_at=CLOSE, knowledge_at=SEALED_AT, computed_at=SEALED_AT,
            source_observation_manifest=MANIFEST, value=Decimal("0.0005"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        with self.assertRaisesRegex(OpenToOpenPreregistrationV1Error, "three_clock"):
            require_professional_historical_decisions_v1(
                packet, _t4(), (legacy,), compute_latency=LATENCY, clock_resolver=RESOLVER  # type: ignore[arg-type]
            )

    def test_t1_dataset_verdict_still_refuses_first(self) -> None:
        with self.assertRaises(EvidenceTierAuthorityError):
            require_professional_historical_decisions_v1(
                self._packet(), _t1(), (_t4_pair(),), compute_latency=LATENCY, clock_resolver=RESOLVER
            )

    def test_count_must_match_the_packet_and_not_collapse(self) -> None:
        with self.assertRaisesRegex(OpenToOpenPreregistrationV1Error, "differs"):
            require_professional_historical_decisions_v1(
                self._packet(count=3), _t4(), (_t4_pair(EVENT), _t4_pair(EVENT + BAR)),
                compute_latency=LATENCY, clock_resolver=RESOLVER,
            )
        with self.assertRaisesRegex(OpenToOpenPreregistrationV1Error, "UNPROVEN_DISTINCT"):
            require_professional_historical_decisions_v1(
                self._packet(), _t4(), (_t4_pair(EVENT),), compute_latency=LATENCY, clock_resolver=RESOLVER
            )

    def test_latency_is_declared_never_defaulted(self) -> None:
        with self.assertRaises(TypeError):
            require_professional_historical_decisions_v1(  # type: ignore[call-arg]
                self._packet(), _t4(), (_t4_pair(),)
            )
        with self.assertRaises(KnowledgeTimeDoctrineError):
            DeclaredComputeLatencyV1(1, " ").validate()


def _t4_inputs(**overrides: Any) -> tuple[ObservationKnowledgeV1, ObservationKnowledgeV1]:
    clocks: dict[str, Any] = {"arrival_at": ARRIVAL, "arrival_clock_bound": _bound(BOUND_NANOS)}
    clocks.update(overrides)
    return (
        _obs(_t4(), f"mark@{EVENT.isoformat()}", **clocks),
        _obs(_t4(), f"index@{EVENT.isoformat()}", **clocks),
    )


PINNED_V2_HASH = "37ff7fc48707ff81d4efdac1ec3ac10844a4e0b4d50dcd19ceba01256695bd2c"  # pragma: allowlist secret
PINNED_DRAFT_PACKET_HASH = "dbfb0598c054b489d35d2193b8acc485e2f4f08ddb7495ea83f6e5c6313ab2c4"  # pragma: allowlist secret

if __name__ == "__main__":
    unittest.main()
