"""Pure unit evidence for Module 3J.2a (Subject-Aware Strategy Lab Feature
Binding V2). No PostgreSQL: a fake in-memory reader stands in for
``PostgresFeatureAuthority`` and deliberately does NOT gate decision time or
dataset itself, so every test here proves this module's OWN defensive
fail-closed re-checks rather than merely re-proving the authority's SQL.
"""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from trade_platform.feature_authority import (
    FeatureDefinitionVersion,
    FeatureFamily,
    FeatureMaterializationV2,
    FeatureQualityStatus,
    FeatureSubjectType,
)
from trade_platform.strategy_feature_binding_v2 import (
    AlignedFeatureMatrixV2,
    AuthoritativeFeatureSeriesV2,
    ResearchFeatureBundleRequestV2,
    ResearchFeatureBundleStatus,
    ResearchFeatureRequirementV2,
    ResearchQualityPolicyV2,
    StrategyFeatureBindingV2Error,
    SubjectAwareResearchFeatureBundle,
    align_exact_event_feature_matrix,
    build_research_feature_bundle,
)

START = datetime(2025, 1, 1, tzinfo=UTC)
DATASET_ID = UUID("00000000-0000-0000-0000-0000000000d1")
OTHER_DATASET_ID = UUID("00000000-0000-0000-0000-0000000000d2")


def feature_id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"3j2a-feature:{name}")


def definition(name: str, *, semantic_version: str = "1.0.0") -> FeatureDefinitionVersion:
    return FeatureDefinitionVersion(
        name, FeatureFamily.DERIVATIVES, semantic_version, "quant", "3J.2a fixture feature.",
        ("FIXTURE_DATASET",), ("value",), "1d", "event/effective/knowledge bounded", 0, {},
        "reject", "reject", "reject_future_knowledge", None, None, "dimensionless", "3j2a-fixture-v1",
        START, None, feature_id(name),
    )


def materialization(
    name: str,
    subject_type: FeatureSubjectType,
    subject_id: str,
    dataset_version: str,
    event_at: datetime,
    *,
    value: Decimal | None = Decimal("1"),
    quality: FeatureQualityStatus = FeatureQualityStatus.VALIDATED,
    effective_at: datetime | None = None,
    knowledge_at: datetime | None = None,
    computed_at: datetime | None = None,
) -> FeatureMaterializationV2:
    effective_at = effective_at or event_at
    knowledge_at = knowledge_at or effective_at
    computed_at = computed_at or knowledge_at
    return FeatureMaterializationV2.create(
        feature_id=feature_id(name), subject_type=subject_type, subject_id=subject_id,
        dataset_version=dataset_version, event_at=event_at, effective_at=effective_at,
        knowledge_at=knowledge_at, computed_at=computed_at,
        source_observation_manifest=(f"fixture:{name}:{event_at.isoformat()}",),
        value=value, quality_status=quality,
    )


def requirement(
    name: str, subject_type: FeatureSubjectType, *, semantic_version: str = "1.0.0"
) -> ResearchFeatureRequirementV2:
    return ResearchFeatureRequirementV2(feature_id(name), name, semantic_version, subject_type)


class _FakeReaderV2:
    """Deliberately naive: returns exactly what was registered, no gating."""

    def __init__(self) -> None:
        self._definitions: dict[UUID, FeatureDefinitionVersion] = {}
        self._rows: dict[tuple[UUID, FeatureSubjectType, str, str], tuple[FeatureMaterializationV2, ...]] = {}

    def register_definition(self, item: FeatureDefinitionVersion) -> None:
        self._definitions[item.feature_id] = item

    def register_rows(
        self,
        feature: UUID,
        subject_type: FeatureSubjectType,
        subject_id: str,
        dataset_version: str,
        rows: tuple[FeatureMaterializationV2, ...],
    ) -> None:
        self._rows[(feature, subject_type, subject_id, dataset_version)] = rows

    def definition(self, feature_id: UUID) -> FeatureDefinitionVersion:
        return self._definitions[feature_id]

    def latest_as_of_subject(
        self,
        feature_id: UUID,
        subject_type: FeatureSubjectType,
        subject_id: str,
        dataset_version: str,
        decision_at: datetime,
    ) -> tuple[FeatureMaterializationV2, ...]:
        return self._rows.get((feature_id, subject_type, subject_id, dataset_version), ())


def _bundle_request(
    *,
    subject_type: FeatureSubjectType = FeatureSubjectType.INSTRUMENT,
    subject_id: str = "TESTFIX:3J2A:INSTRUMENT",
    dataset_version_id: UUID = DATASET_ID,
    decision_at: datetime = START + timedelta(days=10),
    requirements: tuple[ResearchFeatureRequirementV2, ...],
    quality_policy: ResearchQualityPolicyV2 = ResearchQualityPolicyV2.VALIDATED_ONLY,
) -> ResearchFeatureBundleRequestV2:
    return ResearchFeatureBundleRequestV2(
        subject_type, subject_id, dataset_version_id, decision_at, requirements, quality_policy
    )


class ResearchFeatureRequirementV2Tests(unittest.TestCase):
    def test_requirement_carries_no_concrete_subject(self) -> None:
        req = requirement("spread", FeatureSubjectType.FUTURES_SERIES)
        self.assertFalse(hasattr(req, "subject_id"))


class InstrumentAndFuturesSeriesResolutionTests(unittest.TestCase):
    def test_valid_v2_instrument_feature_series_resolves(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        row = materialization("momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START)
        reader.register_rows(req.feature_id, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (row,))
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:EQ", requirements=(req,)
        )
        outcome = build_research_feature_bundle(reader, request)
        self.assertEqual(outcome.status, ResearchFeatureBundleStatus.AVAILABLE)
        assert outcome.bundle is not None
        self.assertEqual(outcome.bundle.feature_series[0].materializations, (row,))

    def test_valid_v2_futures_series_feature_series_resolves(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("normalized_spread", FeatureSubjectType.FUTURES_SERIES)
        reader.register_definition(definition("normalized_spread"))
        row = materialization(
            "normalized_spread", FeatureSubjectType.FUTURES_SERIES, "TESTFIX:3J2A:SERIES:GC",
            str(DATASET_ID), START,
        )
        reader.register_rows(
            req.feature_id, FeatureSubjectType.FUTURES_SERIES, "TESTFIX:3J2A:SERIES:GC", str(DATASET_ID), (row,)
        )
        request = _bundle_request(
            subject_type=FeatureSubjectType.FUTURES_SERIES, subject_id="TESTFIX:3J2A:SERIES:GC", requirements=(req,)
        )
        outcome = build_research_feature_bundle(reader, request)
        self.assertEqual(outcome.status, ResearchFeatureBundleStatus.AVAILABLE)
        assert outcome.bundle is not None
        self.assertEqual(outcome.bundle.subject_type, FeatureSubjectType.FUTURES_SERIES)

    def test_futures_series_subject_is_never_expressed_as_instrument_id(self) -> None:
        # Structural: FeatureMaterializationV2 (the only type this module
        # speaks) has no instrument_id field at all -- a FUTURES_SERIES value
        # cannot be fabricated into one even by accident.
        self.assertNotIn("instrument_id", FeatureMaterializationV2.__dataclass_fields__)


class RejectionInvariantTests(unittest.TestCase):
    def test_wrong_subject_type_rejected_at_request_construction(self) -> None:
        req = requirement("spread", FeatureSubjectType.FUTURES_SERIES)
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "wrong_subject_type"):
            _bundle_request(subject_type=FeatureSubjectType.INSTRUMENT, requirements=(req,)).validate()

    def test_wrong_subject_type_in_returned_row_rejected(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        # Misbehaving reader: rows keyed under INSTRUMENT but the row itself
        # claims FUTURES_SERIES -- must be caught defensively.
        bad_row = materialization(
            "momentum", FeatureSubjectType.FUTURES_SERIES, "TESTFIX:3J2A:EQ", str(DATASET_ID), START
        )
        reader.register_rows(
            req.feature_id, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (bad_row,)
        )
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:EQ", requirements=(req,)
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "subject_type_mismatch"):
            build_research_feature_bundle(reader, request)

    def test_wrong_subject_id_rejected(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        bad_row = materialization(
            "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:OTHER", str(DATASET_ID), START
        )
        reader.register_rows(
            req.feature_id, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (bad_row,)
        )
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:EQ", requirements=(req,)
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "subject_id_mismatch"):
            build_research_feature_bundle(reader, request)

    def test_unknown_subject_produces_unavailable_not_a_fabricated_result(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        # Nothing registered for this subject at all -- the existing
        # authority's own write-side subject-existence enforcement is what
        # guarantees no row can ever exist for a truly unknown subject.
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:UNKNOWN", requirements=(req,)
        )
        outcome = build_research_feature_bundle(reader, request)
        self.assertEqual(outcome.status, ResearchFeatureBundleStatus.UNAVAILABLE)
        self.assertIn("momentum:required_feature_unavailable", outcome.reasons)

    def test_wrong_feature_id_name_rejected(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        # Definition registered under this feature_id disagrees on name.
        reader.register_definition(replace(definition("momentum"), name="not_momentum"))
        request = _bundle_request(requirements=(req,))
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "research_feature_version_mismatch"):
            build_research_feature_bundle(reader, request)

    def test_semantic_version_mismatch_rejected(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT, semantic_version="2.0.0")
        reader.register_definition(definition("momentum", semantic_version="1.0.0"))
        request = _bundle_request(requirements=(req,))
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "research_feature_version_mismatch"):
            build_research_feature_bundle(reader, request)

    def test_cross_dataset_feature_rejected(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        # Misbehaving reader: row claims a different dataset than requested.
        wrong_dataset_row = materialization(
            "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(OTHER_DATASET_ID), START
        )
        reader.register_rows(
            req.feature_id, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID),
            (wrong_dataset_row,),
        )
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:EQ",
            dataset_version_id=DATASET_ID, requirements=(req,),
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "feature_series_dataset_mismatch"):
            build_research_feature_bundle(reader, request)

    def test_bundle_mixing_two_dataset_uuids_rejected(self) -> None:
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        row = materialization(
            "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(OTHER_DATASET_ID), START
        )
        series = AuthoritativeFeatureSeriesV2(
            req, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(OTHER_DATASET_ID), (row,)
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "research_bundle_mixed_dataset"):
            SubjectAwareResearchFeatureBundle.create(
                dataset_version_id=DATASET_ID, subject_type=FeatureSubjectType.INSTRUMENT,
                subject_id="TESTFIX:3J2A:EQ", decision_at=START + timedelta(days=1),
                quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY, feature_series=(series,),
            )

    def test_mixed_instrument_futures_series_bundle_rejected(self) -> None:
        instrument_req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        series_req = requirement("normalized_spread", FeatureSubjectType.FUTURES_SERIES)
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, requirements=(instrument_req, series_req)
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "research_bundle_wrong_subject_type"):
            request.validate()

    def test_different_subject_ids_in_one_bundle_rejected(self) -> None:
        req_a = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        req_b = requirement("breakout", FeatureSubjectType.INSTRUMENT)
        row_a = materialization("momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ_A", str(DATASET_ID), START)
        row_b = materialization("breakout", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ_B", str(DATASET_ID), START)
        series_a = AuthoritativeFeatureSeriesV2(
            req_a, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ_A", str(DATASET_ID), (row_a,)
        )
        series_b = AuthoritativeFeatureSeriesV2(
            req_b, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ_B", str(DATASET_ID), (row_b,)
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "research_bundle_mixed_subject"):
            SubjectAwareResearchFeatureBundle.create(
                dataset_version_id=DATASET_ID, subject_type=FeatureSubjectType.INSTRUMENT,
                subject_id="TESTFIX:3J2A:EQ_A", decision_at=START + timedelta(days=1),
                quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY, feature_series=(series_a, series_b),
            )

    def test_future_knowledge_at_rejected_even_with_past_event_and_effective(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        decision_at = START + timedelta(days=1)
        future_known = materialization(
            "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START,
            effective_at=START, knowledge_at=decision_at + timedelta(days=1),
            computed_at=decision_at + timedelta(days=1),
        )
        reader.register_rows(
            req.feature_id, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (future_known,)
        )
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:EQ",
            decision_at=decision_at, requirements=(req,),
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "future_feature_knowledge"):
            build_research_feature_bundle(reader, request)

    def test_future_event_and_effective_timestamp_rejected(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        decision_at = START
        future_event = materialization(
            "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID),
            decision_at + timedelta(days=1),
        )
        reader.register_rows(
            req.feature_id, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (future_event,)
        )
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:EQ",
            decision_at=decision_at, requirements=(req,),
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "future_feature_knowledge"):
            build_research_feature_bundle(reader, request)

    def test_future_computed_at_rejected_even_with_past_knowledge(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        decision_at = START + timedelta(days=1)
        future_computed = materialization(
            "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START,
            effective_at=START, knowledge_at=START, computed_at=decision_at + timedelta(days=1),
        )
        reader.register_rows(
            req.feature_id, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (future_computed,)
        )
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:EQ",
            decision_at=decision_at, requirements=(req,),
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "future_feature_knowledge"):
            build_research_feature_bundle(reader, request)

    def test_degraded_feature_excluded_under_validated_only(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        degraded = materialization(
            "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START,
            value=None, quality=FeatureQualityStatus.DEGRADED,
        )
        reader.register_rows(
            req.feature_id, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (degraded,)
        )
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:EQ", requirements=(req,)
        )
        outcome = build_research_feature_bundle(reader, request)
        self.assertEqual(outcome.status, ResearchFeatureBundleStatus.UNAVAILABLE)

    def test_rejected_feature_excluded(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        rejected = materialization(
            "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START,
            value=None, quality=FeatureQualityStatus.REJECTED,
        )
        reader.register_rows(
            req.feature_id, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (rejected,)
        )
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:EQ", requirements=(req,)
        )
        outcome = build_research_feature_bundle(reader, request)
        self.assertEqual(outcome.status, ResearchFeatureBundleStatus.UNAVAILABLE)

    def test_missing_feature_produces_unavailable_fail_closed_result(self) -> None:
        reader = _FakeReaderV2()
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        reader.register_definition(definition("momentum"))
        # No rows registered at all for this exact key.
        request = _bundle_request(
            subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:3J2A:EQ", requirements=(req,)
        )
        outcome = build_research_feature_bundle(reader, request)
        self.assertEqual(outcome.status, ResearchFeatureBundleStatus.UNAVAILABLE)
        self.assertIsNone(outcome.bundle)

    def test_duplicate_conflicting_event_identity_rejected(self) -> None:
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        first = materialization("momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START)
        conflicting = materialization(
            "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START,
            value=Decimal("999"),
        )
        series = AuthoritativeFeatureSeriesV2(
            req, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (first, conflicting)
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "duplicate_event"):
            series.validate()


class DeterminismAndOrderingTests(unittest.TestCase):
    def test_deterministic_materialization_ordering(self) -> None:
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        out_of_order = (
            materialization("momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START + timedelta(days=1)),
            materialization("momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START),
        )
        series = AuthoritativeFeatureSeriesV2(
            req, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), out_of_order
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "not_chronological"):
            series.validate()

    def test_irregular_feature_series_remains_valid(self) -> None:
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        irregular = (
            materialization("momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START),
            materialization(
                "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID),
                START + timedelta(days=5),
            ),
            materialization(
                "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID),
                START + timedelta(days=6, hours=3),
            ),
        )
        series = AuthoritativeFeatureSeriesV2(
            req, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), irregular
        )
        series.validate()  # does not raise

    def test_bundle_feature_series_ordered_deterministically_by_feature_id(self) -> None:
        req_a = requirement("breakout", FeatureSubjectType.INSTRUMENT)
        req_b = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        row_a = materialization("breakout", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START)
        row_b = materialization("momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START)
        series_a = AuthoritativeFeatureSeriesV2(req_a, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (row_a,))
        series_b = AuthoritativeFeatureSeriesV2(req_b, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), (row_b,))
        forward = SubjectAwareResearchFeatureBundle.create(
            dataset_version_id=DATASET_ID, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id="TESTFIX:3J2A:EQ", decision_at=START + timedelta(days=1),
            quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY, feature_series=(series_a, series_b),
        )
        backward = SubjectAwareResearchFeatureBundle.create(
            dataset_version_id=DATASET_ID, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id="TESTFIX:3J2A:EQ", decision_at=START + timedelta(days=1),
            quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY, feature_series=(series_b, series_a),
        )
        self.assertEqual(forward.feature_series, backward.feature_series)
        self.assertEqual(forward.content_hash, backward.content_hash)


class BundleContentHashTests(unittest.TestCase):
    def _series(
        self, name: str = "momentum", *, subject_id: str = "TESTFIX:3J2A:EQ",
        dataset_version_id: UUID = DATASET_ID, semantic_version: str = "1.0.0",
        content: str = "1",
    ) -> AuthoritativeFeatureSeriesV2:
        req = requirement(name, FeatureSubjectType.INSTRUMENT, semantic_version=semantic_version)
        row = materialization(
            name, FeatureSubjectType.INSTRUMENT, subject_id, str(dataset_version_id), START,
            value=Decimal(content),
        )
        return AuthoritativeFeatureSeriesV2(req, FeatureSubjectType.INSTRUMENT, subject_id, str(dataset_version_id), (row,))

    def _bundle(self, series: AuthoritativeFeatureSeriesV2, *, subject_id: str = "TESTFIX:3J2A:EQ", dataset_version_id: UUID = DATASET_ID, decision_at: datetime = START + timedelta(days=1)) -> SubjectAwareResearchFeatureBundle:
        return SubjectAwareResearchFeatureBundle.create(
            dataset_version_id=dataset_version_id, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=subject_id, decision_at=decision_at,
            quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY, feature_series=(series,),
        )

    def test_deterministic_bundle_hash_for_identical_evidence(self) -> None:
        series = self._series()
        self.assertEqual(self._bundle(series).content_hash, self._bundle(series).content_hash)

    def test_changed_feature_content_hash_changes_bundle_hash(self) -> None:
        baseline = self._bundle(self._series(content="1"))
        changed = self._bundle(self._series(content="2"))
        self.assertNotEqual(baseline.content_hash, changed.content_hash)

    def test_changed_feature_version_changes_bundle_identity(self) -> None:
        baseline = self._bundle(self._series(semantic_version="1.0.0"))
        changed = self._bundle(self._series(semantic_version="2.0.0"))
        self.assertNotEqual(baseline.content_hash, changed.content_hash)
        self.assertNotEqual(baseline.bundle_id, changed.bundle_id)

    def test_changed_subject_changes_bundle_identity(self) -> None:
        baseline = self._bundle(self._series(subject_id="TESTFIX:3J2A:EQ_A"), subject_id="TESTFIX:3J2A:EQ_A")
        changed = self._bundle(self._series(subject_id="TESTFIX:3J2A:EQ_B"), subject_id="TESTFIX:3J2A:EQ_B")
        self.assertNotEqual(baseline.content_hash, changed.content_hash)

    def test_changed_sealed_dataset_changes_bundle_identity(self) -> None:
        baseline = self._bundle(self._series(dataset_version_id=DATASET_ID), dataset_version_id=DATASET_ID)
        changed = self._bundle(self._series(dataset_version_id=OTHER_DATASET_ID), dataset_version_id=OTHER_DATASET_ID)
        self.assertNotEqual(baseline.content_hash, changed.content_hash)

    def test_changed_decision_time_changes_bundle_identity(self) -> None:
        baseline = self._bundle(self._series(), decision_at=START + timedelta(days=1))
        changed = self._bundle(self._series(), decision_at=START + timedelta(days=2))
        self.assertNotEqual(baseline.content_hash, changed.content_hash)


class ExactEventAlignmentTests(unittest.TestCase):
    def _bundle(self) -> SubjectAwareResearchFeatureBundle:
        req = requirement("momentum", FeatureSubjectType.INSTRUMENT)
        rows = (
            materialization("momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), START, value=Decimal("1")),
            materialization(
                "momentum", FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID),
                START + timedelta(days=2), value=Decimal("2"),
            ),
        )
        series = AuthoritativeFeatureSeriesV2(req, FeatureSubjectType.INSTRUMENT, "TESTFIX:3J2A:EQ", str(DATASET_ID), rows)
        return SubjectAwareResearchFeatureBundle.create(
            dataset_version_id=DATASET_ID, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id="TESTFIX:3J2A:EQ", decision_at=START + timedelta(days=10),
            quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY, feature_series=(series,),
        )

    def test_exact_event_alignment_succeeds_for_identical_timestamps(self) -> None:
        bundle = self._bundle()
        matrix = align_exact_event_feature_matrix(bundle, (START, START + timedelta(days=2)))
        self.assertEqual(matrix.values["momentum"], (Decimal("1"), Decimal("2")))
        self.assertIsInstance(matrix, AlignedFeatureMatrixV2)

    def test_absent_exact_timestamp_does_not_forward_fill(self) -> None:
        bundle = self._bundle()
        matrix = align_exact_event_feature_matrix(bundle, (START + timedelta(days=1),))
        self.assertEqual(matrix.values["momentum"], (None,))

    def test_absent_exact_timestamp_does_not_nearest_match(self) -> None:
        bundle = self._bundle()
        # One minute away from a real event_at on both sides -- nearest-match
        # would silently pick day 0 or day 2; exact alignment must not.
        matrix = align_exact_event_feature_matrix(bundle, (START + timedelta(days=2, minutes=-1),))
        self.assertEqual(matrix.values["momentum"], (None,))


class StructuralScopeTests(unittest.TestCase):
    def test_module_persists_nothing_and_creates_no_new_authority(self) -> None:
        import trade_platform.strategy_feature_binding_v2 as module

        source = module.__doc__ or ""
        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE", ".transaction("):
            self.assertNotIn(forbidden, source)
        for forbidden_import in ("persistence", "paper_execution", "signal_engine", "risk", "order"):
            self.assertNotIn(f"from .{forbidden_import} import", source)


if __name__ == "__main__":
    unittest.main()
