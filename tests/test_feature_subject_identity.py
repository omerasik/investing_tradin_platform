"""Pure unit tests for Module 3J.0 generalized Feature Authority subject identity.

No database, no network.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trade_platform.feature_authority import (
    FeatureAuthorityError,
    FeatureHashVersion,
    FeatureMaterialization,
    FeatureMaterializationV2,
    FeatureQualityStatus,
    FeatureSubjectType,
)

FEATURE_ID = uuid4()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def v2(**overrides: object) -> FeatureMaterializationV2:
    defaults: dict[str, object] = {
        "feature_id": FEATURE_ID,
        "subject_type": FeatureSubjectType.INSTRUMENT,
        "subject_id": "TESTFIX:INSTR",
        "dataset_version": "fixture-v1",
        "event_at": T0,
        "effective_at": T0,
        "knowledge_at": T0,
        "computed_at": T0,
        "source_observation_manifest": ("raw:1",),
        "value": Decimal("0.01"),
        "quality_status": FeatureQualityStatus.VALIDATED,
    }
    defaults.update(overrides)
    return FeatureMaterializationV2.create(**defaults)  # type: ignore[arg-type]


class FeatureSubjectTypeTests(unittest.TestCase):
    def test_only_instrument_and_futures_series_are_supported(self) -> None:
        self.assertEqual(
            {member.value for member in FeatureSubjectType}, {"INSTRUMENT", "FUTURES_SERIES"}
        )
        with self.assertRaises(ValueError):
            FeatureSubjectType("ACCOUNT")


class FeatureMaterializationV2Tests(unittest.TestCase):
    def test_content_hash_is_deterministic(self) -> None:
        self.assertEqual(v2().content_hash, v2().content_hash)
        self.assertEqual(len(v2().content_hash), 64)

    def test_subject_type_change_changes_identity(self) -> None:
        instrument = v2(subject_type=FeatureSubjectType.INSTRUMENT, subject_id="SHARED")
        series = v2(subject_type=FeatureSubjectType.FUTURES_SERIES, subject_id="SHARED")
        self.assertNotEqual(instrument.content_hash, series.content_hash)

    def test_subject_id_change_changes_identity(self) -> None:
        self.assertNotEqual(
            v2(subject_id="A").content_hash, v2(subject_id="B").content_hash
        )

    def test_v2_hash_never_collides_with_v1_hash_for_the_same_instrument(self) -> None:
        v1 = FeatureMaterialization.create(
            feature_id=FEATURE_ID, instrument_id="TESTFIX:INSTR", dataset_version="fixture-v1",
            event_at=T0, effective_at=T0, knowledge_at=T0, computed_at=T0,
            source_observation_manifest=("raw:1",), value=Decimal("0.01"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        generalized = v2(subject_type=FeatureSubjectType.INSTRUMENT, subject_id="TESTFIX:INSTR")
        self.assertNotEqual(v1.content_hash, generalized.content_hash)

    def test_validate_requires_aware_timestamps_and_manifest(self) -> None:
        with self.assertRaises(FeatureAuthorityError):
            v2(source_observation_manifest=()).validate()
        with self.assertRaises(FeatureAuthorityError):
            v2(subject_id="").validate()

    def test_validate_requires_value_when_validated_quality(self) -> None:
        with self.assertRaises(FeatureAuthorityError):
            v2(value=None, quality_status=FeatureQualityStatus.VALIDATED).validate()
        v2(value=None, quality_status=FeatureQualityStatus.REJECTED).validate()

    def test_validate_enforces_temporal_order(self) -> None:
        from datetime import timedelta

        with self.assertRaises(FeatureAuthorityError):
            v2(effective_at=T0 - timedelta(seconds=1)).validate()
        with self.assertRaises(FeatureAuthorityError):
            v2(computed_at=T0, knowledge_at=T0 + timedelta(seconds=1)).validate()

    def test_hash_version_enum_has_exactly_v1_and_v2(self) -> None:
        self.assertEqual({member.value for member in FeatureHashVersion}, {"V1", "V2"})


if __name__ == "__main__":
    unittest.main()
