"""Pure, offline tests for the Module 3J.1c crypto derivatives features.

No PostgreSQL required. See ``tests/test_crypto_derivatives_features_postgres.py``
for the end-to-end Feature Authority materialization evidence.
"""

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trade_platform.crypto_derivatives_features import (
    _VALUE_SCALE,
    CALCULATION_VERSION_FUNDING_FORECAST_ERROR,
    CALCULATION_VERSION_MARK_INDEX_BASIS,
    CALCULATION_VERSION_REALIZED_FUNDING_ANNUALIZED,
    CRYPTO_FUNDING_FORECAST_ERROR,
    CRYPTO_MARK_INDEX_BASIS,
    CRYPTO_REALIZED_FUNDING_ANNUALIZED,
    CryptoDerivativesFeatureError,
    _aware,
    _ConventionInfo,
    _DatasetInfo,
    _funding_forecast_error_manifest_tokens,
    _FundingObservation,
    _mark_index_basis_manifest_tokens,
    _realized_funding_annualized_manifest_tokens,
    _ReferencePriceObservation,
    _validate_and_convert_interval,
    crypto_funding_forecast_error_definition,
    crypto_mark_index_basis_definition,
    crypto_realized_funding_annualized_definition,
)
from trade_platform.feature_authority import FeatureFamily


class DefinitionTests(unittest.TestCase):
    def test_mark_index_basis_definition(self) -> None:
        created_at = datetime(2026, 9, 10, tzinfo=UTC)
        definition = crypto_mark_index_basis_definition(created_at)
        definition.validate()
        self.assertEqual(definition.name, CRYPTO_MARK_INDEX_BASIS)
        self.assertEqual(definition.family, FeatureFamily.DERIVATIVES)
        self.assertEqual(definition.semantic_version, "1.0.0")
        self.assertEqual(definition.calculation_version, CALCULATION_VERSION_MARK_INDEX_BASIS)
        self.assertEqual(definition.required_dataset_types, ("MARK_PRICE", "INDEX_PRICE"))
        self.assertIsNone(definition.expected_minimum)
        self.assertIsNone(definition.expected_maximum)
        self.assertEqual(definition.leakage_policy, "reject_future_knowledge")
        self.assertEqual(definition.missing_value_policy, "fail_closed_no_materialization")
        self.assertEqual(definition.units, "dimensionless")

    def test_realized_funding_annualized_definition(self) -> None:
        created_at = datetime(2026, 9, 10, tzinfo=UTC)
        definition = crypto_realized_funding_annualized_definition(created_at)
        definition.validate()
        self.assertEqual(definition.name, CRYPTO_REALIZED_FUNDING_ANNUALIZED)
        self.assertEqual(definition.family, FeatureFamily.DERIVATIVES)
        self.assertEqual(
            definition.calculation_version, CALCULATION_VERSION_REALIZED_FUNDING_ANNUALIZED
        )
        self.assertEqual(definition.required_dataset_types, ("FUNDING_RATE_REALIZED",))
        self.assertEqual(definition.units, "1/year")
        # Invariant: ACT_365_FIXED is an immutable, explicit definition parameter.
        self.assertEqual(definition.parameters["annualization_basis"], "ACT_365_FIXED")
        self.assertEqual(definition.parameters["annualization_year_seconds"], 365 * 24 * 60 * 60)

    def test_funding_forecast_error_definition(self) -> None:
        created_at = datetime(2026, 9, 10, tzinfo=UTC)
        definition = crypto_funding_forecast_error_definition(created_at)
        definition.validate()
        self.assertEqual(definition.name, CRYPTO_FUNDING_FORECAST_ERROR)
        self.assertEqual(definition.family, FeatureFamily.DERIVATIVES)
        self.assertEqual(
            definition.calculation_version, CALCULATION_VERSION_FUNDING_FORECAST_ERROR
        )
        self.assertEqual(
            definition.required_dataset_types, ("FUNDING_RATE_REALIZED", "FUNDING_RATE_INDICATIVE")
        )
        self.assertEqual(definition.units, "dimensionless")

    def test_all_three_are_derivatives_instrument_subject(self) -> None:
        created_at = datetime(2026, 9, 10, tzinfo=UTC)
        for definition in (
            crypto_mark_index_basis_definition(created_at),
            crypto_realized_funding_annualized_definition(created_at),
            crypto_funding_forecast_error_definition(created_at),
        ):
            self.assertEqual(definition.family, FeatureFamily.DERIVATIVES)


class AwareHelperTests(unittest.TestCase):
    def test_naive_datetime_rejected(self) -> None:
        with self.assertRaisesRegex(
            CryptoDerivativesFeatureError, "event_at_must_be_timezone_aware"
        ):
            _aware(datetime(2025, 1, 1), "event_at")  # noqa: DTZ001

    def test_aware_datetime_accepted(self) -> None:
        _aware(datetime(2025, 1, 1, tzinfo=UTC), "event_at")


class MarkIndexBasisManifestTests(unittest.TestCase):
    def _evidence(self) -> tuple[_DatasetInfo, _ReferencePriceObservation, _ReferencePriceObservation]:
        dataset = _DatasetInfo(
            dataset_version_id=uuid4(), content_hash="a" * 64, source_id=uuid4(),
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        mark = _ReferencePriceObservation(
            normalized_observation_id=uuid4(), raw_observation_id=uuid4(),
            provider_identifier="PROV1", event_at=datetime(2025, 1, 2, tzinfo=UTC),
            effective_at=datetime(2025, 1, 2, tzinfo=UTC), ingested_at=datetime(2025, 1, 2, tzinfo=UTC),
            revision=0, normalized_at=datetime(2025, 1, 2, 1, tzinfo=UTC),
            price=Decimal("100"), price_asset="USDT",
        )
        index = _ReferencePriceObservation(
            normalized_observation_id=uuid4(), raw_observation_id=uuid4(),
            provider_identifier="PROV1", event_at=datetime(2025, 1, 2, tzinfo=UTC),
            effective_at=datetime(2025, 1, 2, tzinfo=UTC), ingested_at=datetime(2025, 1, 2, tzinfo=UTC),
            revision=0, normalized_at=datetime(2025, 1, 2, 1, tzinfo=UTC),
            price=Decimal("99"), price_asset="USDT",
        )
        return dataset, mark, index

    def test_deterministic_order_and_required_categories(self) -> None:
        dataset, mark, index = self._evidence()
        tokens = _mark_index_basis_manifest_tokens(
            dataset=dataset, instrument_id="TESTFIXTURE:X", mark=mark, index=index,
        )
        for expected_index, prefix in enumerate((
            "historical_dataset_version_id:", "historical_dataset_content_hash:", "source_id:",
            "instrument_id:", "mark_normalized_observation_id:", "mark_raw_observation_id:",
            "mark_event_at:", "mark_revision:", "mark_ingested_at:",
            "index_normalized_observation_id:", "index_raw_observation_id:", "index_event_at:",
            "index_revision:", "index_ingested_at:", "price_asset:",
        )):
            self.assertTrue(tokens[expected_index].startswith(prefix), f"token {expected_index} missing {prefix!r}")

    def test_same_evidence_produces_identical_manifest(self) -> None:
        dataset, mark, index = self._evidence()
        first = _mark_index_basis_manifest_tokens(
            dataset=dataset, instrument_id="TESTFIXTURE:X", mark=mark, index=index,
        )
        second = _mark_index_basis_manifest_tokens(
            dataset=dataset, instrument_id="TESTFIXTURE:X", mark=mark, index=index,
        )
        self.assertEqual(first, second)

    def test_changed_revision_changes_manifest(self) -> None:
        from dataclasses import replace

        dataset, mark, index = self._evidence()
        base = _mark_index_basis_manifest_tokens(
            dataset=dataset, instrument_id="TESTFIXTURE:X", mark=mark, index=index,
        )
        revised = _mark_index_basis_manifest_tokens(
            dataset=dataset, instrument_id="TESTFIXTURE:X", mark=replace(mark, revision=1), index=index,
        )
        self.assertNotEqual(base, revised)


class FundingManifestTests(unittest.TestCase):
    def _funding(self, **overrides: object) -> _FundingObservation:
        fields: dict[str, object] = {
            "normalized_observation_id": uuid4(), "raw_observation_id": uuid4(),
            "provider_identifier": "PROV1", "event_at": datetime(2025, 1, 2, tzinfo=UTC),
            "effective_at": datetime(2025, 1, 2, tzinfo=UTC),
            "ingested_at": datetime(2025, 1, 2, tzinfo=UTC), "revision": 0,
            "normalized_at": datetime(2025, 1, 2, 1, tzinfo=UTC), "funding_rate": Decimal("0.0001"),
            "convention_id": uuid4(), "convention_version": 1,
            "target_funding_at": datetime(2025, 1, 2, tzinfo=UTC),
            "published_at": datetime(2025, 1, 2, tzinfo=UTC), "settlement_asset": "USDT",
        }
        fields.update(overrides)
        return _FundingObservation(**fields)  # type: ignore[arg-type]

    def test_realized_funding_annualized_manifest_categories(self) -> None:
        dataset = _DatasetInfo(
            dataset_version_id=uuid4(), content_hash="b" * 64, source_id=uuid4(),
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        realized = self._funding()
        convention = _ConventionInfo(
            convention_id=realized.convention_id, interval_hours=Decimal(8),
            interval_seconds=Decimal(28800), funding_settlement_asset="USDT",
        )
        tokens = _realized_funding_annualized_manifest_tokens(
            dataset=dataset, instrument_id="TESTFIXTURE:X", realized=realized, convention=convention,
        )
        for prefix in (
            "historical_dataset_version_id:", "historical_dataset_content_hash:", "source_id:",
            "instrument_id:", "realized_normalized_observation_id:", "realized_raw_observation_id:",
            "realized_target_funding_at:", "realized_revision:", "realized_ingested_at:",
            "convention_id:", "convention_version:", "interval_hours:", "funding_settlement_asset:",
            "annualization_basis:ACT_365_FIXED",
        ):
            self.assertTrue(any(token.startswith(prefix) for token in tokens), prefix)

    def test_forecast_error_manifest_categories(self) -> None:
        dataset = _DatasetInfo(
            dataset_version_id=uuid4(), content_hash="c" * 64, source_id=uuid4(),
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        convention_id = uuid4()
        realized = self._funding(convention_id=convention_id)
        indicative = self._funding(
            convention_id=convention_id,
            event_at=datetime(2025, 1, 1, 20, tzinfo=UTC),
            published_at=datetime(2025, 1, 1, 20, tzinfo=UTC),
        )
        tokens = _funding_forecast_error_manifest_tokens(
            dataset=dataset, instrument_id="TESTFIXTURE:X", realized=realized, indicative=indicative,
        )
        for prefix in (
            "historical_dataset_version_id:", "historical_dataset_content_hash:", "source_id:",
            "instrument_id:", "realized_normalized_observation_id:", "realized_raw_observation_id:",
            "realized_revision:", "realized_ingested_at:", "realized_event_at:",
            "realized_target_funding_at:", "indicative_normalized_observation_id:",
            "indicative_raw_observation_id:", "indicative_revision:", "indicative_ingested_at:",
            "indicative_published_at:", "indicative_target_funding_at:", "convention_id:",
            "convention_version:", "funding_settlement_asset:",
        ):
            self.assertTrue(any(token.startswith(prefix) for token in tokens), prefix)


class IntervalConversionTests(unittest.TestCase):
    def test_zero_interval_rejected(self) -> None:
        with self.assertRaisesRegex(
            CryptoDerivativesFeatureError, "non_positive_funding_interval"
        ):
            _validate_and_convert_interval(Decimal("0"))

    def test_negative_interval_rejected(self) -> None:
        with self.assertRaisesRegex(
            CryptoDerivativesFeatureError, "non_positive_funding_interval"
        ):
            _validate_and_convert_interval(Decimal("-8"))

    def test_non_integral_interval_seconds_rejected(self) -> None:
        with self.assertRaisesRegex(
            CryptoDerivativesFeatureError, "non_integral_interval_seconds"
        ):
            _validate_and_convert_interval(Decimal("0.0001"))

    def test_whole_hour_interval_converts_exactly(self) -> None:
        self.assertEqual(_validate_and_convert_interval(Decimal("8")), Decimal("28800"))

    def test_fractional_but_whole_second_interval_converts_exactly(self) -> None:
        self.assertEqual(_validate_and_convert_interval(Decimal("0.1")), Decimal("360"))


class ValueScaleTests(unittest.TestCase):
    def test_value_scale_matches_feature_materializations_column_scale(self) -> None:
        self.assertEqual(_VALUE_SCALE, Decimal("1E-12"))

    def test_basis_division_quantizes_without_error(self) -> None:
        mark = Decimal("58234.123456789012345670")
        index = Decimal("58200.000000000000000001")
        basis = ((mark - index) / index).quantize(_VALUE_SCALE)
        self.assertEqual(basis.as_tuple().exponent, -12)

    def test_annualization_quantizes_without_error(self) -> None:
        rate = Decimal("0.000123456789")
        annualized = (rate * Decimal(365 * 24 * 60 * 60) / Decimal(28800)).quantize(_VALUE_SCALE)
        self.assertEqual(annualized.as_tuple().exponent, -12)


if __name__ == "__main__":
    unittest.main()
