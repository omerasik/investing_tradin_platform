"""Pure, offline tests for the Module 3J.1b ``open_interest_change`` feature.

No PostgreSQL required. See ``tests/test_open_interest_features_postgres.py``
for the end-to-end Feature Authority materialization evidence.
"""

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trade_platform.feature_authority import FeatureFamily
from trade_platform.open_interest_features import (
    _VALUE_SCALE,
    CALCULATION_VERSION,
    OPEN_INTEREST_CHANGE,
    OpenInterestFeatureError,
    _aware,
    _DatasetInfo,
    _manifest_tokens,
    _OIObservation,
    open_interest_change_definition,
)


class OpenInterestChangeDefinitionTests(unittest.TestCase):
    def test_single_cross_asset_definition(self) -> None:
        created_at = datetime(2026, 9, 9, tzinfo=UTC)
        definition = open_interest_change_definition(created_at)
        definition.validate()
        self.assertEqual(definition.name, OPEN_INTEREST_CHANGE)
        self.assertEqual(definition.family, FeatureFamily.DERIVATIVES)
        self.assertEqual(definition.semantic_version, "1.0.0")
        self.assertEqual(definition.calculation_version, CALCULATION_VERSION)
        self.assertEqual(definition.required_dataset_types, ("OPEN_INTEREST",))
        self.assertIsNone(definition.expected_minimum)
        self.assertIsNone(definition.expected_maximum)
        self.assertEqual(definition.leakage_policy, "reject_future_knowledge")
        self.assertEqual(definition.missing_value_policy, "fail_closed_no_materialization")
        # Never claims one universal physical unit -- an explicit semantic marker.
        self.assertEqual(definition.units, "native_open_interest_unit")


class AwareHelperTests(unittest.TestCase):
    def test_naive_datetime_rejected(self) -> None:
        with self.assertRaisesRegex(OpenInterestFeatureError, "event_at_must_be_timezone_aware"):
            _aware(datetime(2025, 1, 1), "event_at")  # noqa: DTZ001

    def test_aware_datetime_accepted(self) -> None:
        _aware(datetime(2025, 1, 1, tzinfo=UTC), "event_at")


class ManifestTokenTests(unittest.TestCase):
    def _evidence(self) -> tuple[_DatasetInfo, _OIObservation, _OIObservation]:
        dataset = _DatasetInfo(
            dataset_version_id=uuid4(), content_hash="a" * 64, source_id=uuid4(),
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        current = _OIObservation(
            normalized_observation_id=uuid4(), raw_observation_id=uuid4(),
            provider_identifier="PROV1", event_at=datetime(2025, 1, 2, tzinfo=UTC),
            effective_at=datetime(2025, 1, 2, tzinfo=UTC), ingested_at=datetime(2025, 1, 2, tzinfo=UTC),
            revision=0, normalized_at=datetime(2025, 1, 2, 1, tzinfo=UTC),
            open_interest=Decimal("120"), unit="CONTRACTS", unit_asset=None,
        )
        prior = _OIObservation(
            normalized_observation_id=uuid4(), raw_observation_id=uuid4(),
            provider_identifier="PROV1", event_at=datetime(2025, 1, 1, tzinfo=UTC),
            effective_at=datetime(2025, 1, 1, tzinfo=UTC), ingested_at=datetime(2025, 1, 1, tzinfo=UTC),
            revision=0, normalized_at=datetime(2025, 1, 1, 1, tzinfo=UTC),
            open_interest=Decimal("100"), unit="CONTRACTS", unit_asset=None,
        )
        return dataset, current, prior

    def test_deterministic_order_and_required_categories(self) -> None:
        dataset, current, prior = self._evidence()
        tokens = _manifest_tokens(
            dataset=dataset, instrument_id="TESTFIXTURE:X", current=current, prior=prior,
        )
        for index, prefix in enumerate((
            "historical_dataset_version_id:", "historical_dataset_content_hash:", "source_id:",
            "instrument_id:", "current_normalized_observation_id:", "current_raw_observation_id:",
            "current_event_at:", "current_revision:", "current_ingested_at:",
            "prior_normalized_observation_id:", "prior_raw_observation_id:", "prior_event_at:",
            "prior_revision:", "prior_ingested_at:", "unit:", "unit_asset:",
        )):
            self.assertTrue(tokens[index].startswith(prefix), f"token {index} missing {prefix!r}")

    def test_null_unit_asset_uses_explicit_marker(self) -> None:
        dataset, current, prior = self._evidence()
        tokens = _manifest_tokens(
            dataset=dataset, instrument_id="TESTFIXTURE:X", current=current, prior=prior,
        )
        self.assertIn("unit_asset:NULL", tokens)

    def test_same_evidence_produces_identical_manifest(self) -> None:
        dataset, current, prior = self._evidence()
        first = _manifest_tokens(dataset=dataset, instrument_id="TESTFIXTURE:X", current=current, prior=prior)
        second = _manifest_tokens(dataset=dataset, instrument_id="TESTFIXTURE:X", current=current, prior=prior)
        self.assertEqual(first, second)

    def test_changed_revision_changes_manifest(self) -> None:
        from dataclasses import replace

        dataset, current, prior = self._evidence()
        base = _manifest_tokens(dataset=dataset, instrument_id="TESTFIXTURE:X", current=current, prior=prior)
        revised_current = replace(current, revision=1)
        revised = _manifest_tokens(
            dataset=dataset, instrument_id="TESTFIXTURE:X", current=revised_current, prior=prior,
        )
        self.assertNotEqual(base, revised)


class ValueScaleTests(unittest.TestCase):
    def test_value_scale_matches_feature_materializations_column_scale(self) -> None:
        self.assertEqual(_VALUE_SCALE, Decimal("1E-12"))

    def test_high_precision_delta_quantizes_without_error(self) -> None:
        delta = Decimal("100.123456789012345670") - Decimal("50.000000000000000000")
        quantized = delta.quantize(_VALUE_SCALE)
        self.assertEqual(quantized, Decimal("50.123456789012"))


if __name__ == "__main__":
    unittest.main()
