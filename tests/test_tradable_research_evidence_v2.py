"""Pure unit evidence for Module 3J.2b.1's composite feature+bar evidence bridge.

No PostgreSQL: both ``SubjectAwareResearchFeatureBundle`` and
``AuthoritativeTradableBarSeriesV2`` are plain in-memory dataclasses, so
fixtures are built directly rather than read through Postgres.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.feature_authority import (
    FeatureMaterializationV2,
    FeatureQualityStatus,
    FeatureSubjectType,
)
from trade_platform.strategy_feature_binding_v2 import (
    AuthoritativeFeatureSeriesV2,
    ResearchFeatureRequirementV2,
    ResearchQualityPolicyV2,
    StrategyFeatureBindingV2Error,
    SubjectAwareResearchFeatureBundle,
)
from trade_platform.tradable_bar_evidence_v2 import (
    AuthoritativeTradableBarSeriesV2,
    AuthoritativeTradableBarV2,
    TradableBarEvidenceV2Error,
)
from trade_platform.tradable_research_evidence_v2 import (
    SubjectAwareTradableResearchEvidenceV2,
    TradableResearchEvidenceV2Error,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
DATASET_ID = uuid4()
OTHER_DATASET_ID = uuid4()
INSTRUMENT = "TESTFIXTURE:3J2B1:BTCUSDT:PERP"
OTHER_INSTRUMENT = "TESTFIXTURE:3J2B1:ETHUSDT:PERP"


FEATURE_ID = uuid4()


def _materialization(
    dataset_version_id, subject_id: str, event_at: datetime, subject_type: FeatureSubjectType,
    *, value: Decimal = Decimal("0.01"),
) -> FeatureMaterializationV2:
    return FeatureMaterializationV2.create(
        feature_id=FEATURE_ID,
        subject_type=subject_type,
        subject_id=subject_id,
        dataset_version=str(dataset_version_id),
        event_at=event_at,
        effective_at=event_at,
        knowledge_at=event_at,
        computed_at=event_at,
        source_observation_manifest=("fixture:manifest",),
        value=value,
        quality_status=FeatureQualityStatus.VALIDATED,
    )


def _bundle(
    *, dataset_version_id=DATASET_ID, subject_id: str = INSTRUMENT,
    subject_type: FeatureSubjectType = FeatureSubjectType.INSTRUMENT, value: Decimal = Decimal("0.01"),
) -> SubjectAwareResearchFeatureBundle:
    materialization = _materialization(dataset_version_id, subject_id, START, subject_type, value=value)
    requirement = ResearchFeatureRequirementV2(
        feature_id=materialization.feature_id, name="crypto_mark_index_basis",
        semantic_version="1.0.0", expected_subject_type=subject_type,
    )
    series = AuthoritativeFeatureSeriesV2(
        requirement=requirement, subject_type=subject_type, subject_id=subject_id,
        dataset_version=str(dataset_version_id), materializations=(materialization,),
    )
    return SubjectAwareResearchFeatureBundle.create(
        dataset_version_id=dataset_version_id, subject_type=subject_type, subject_id=subject_id,
        decision_at=START + timedelta(hours=1), quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY,
        feature_series=(series,),
    )


def _bar(
    dataset_version_id, instrument_id: str, bar_open_at: datetime, *,
    dataset_content_hash: str = "e" * 64, raw_payload_sha256: str = "f" * 64,
    open_price: Decimal = Decimal("100"),
) -> AuthoritativeTradableBarV2:
    return AuthoritativeTradableBarV2(
        dataset_version_id=dataset_version_id, dataset_content_hash=dataset_content_hash, source_id=uuid4(),
        normalized_observation_id=uuid4(), raw_observation_id=uuid4(), raw_payload_sha256=raw_payload_sha256,
        instrument_id=instrument_id, interval="1m", bar_open_at=bar_open_at,
        bar_close_at=bar_open_at + timedelta(minutes=1), normalized_at=bar_open_at + timedelta(minutes=2),
        revision=0, open=open_price, high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
        volume=Decimal("10"), provenance_uri="fixture://bar",
    )


def _bar_series(
    *, dataset_version_id=DATASET_ID, instrument_id: str = INSTRUMENT
) -> AuthoritativeTradableBarSeriesV2:
    bars = (_bar(dataset_version_id, instrument_id, START), _bar(dataset_version_id, instrument_id, START + timedelta(minutes=1)))
    series = AuthoritativeTradableBarSeriesV2(dataset_version_id, instrument_id, "1m", bars)
    series.validate()
    return series


class SubjectAwareTradableResearchEvidenceV2Tests(unittest.TestCase):
    def test_valid_pairing_combines(self) -> None:
        evidence = SubjectAwareTradableResearchEvidenceV2.create(
            feature_bundle=_bundle(), bar_series=_bar_series()
        )
        self.assertEqual(len(evidence.content_hash), 64)

    def test_different_dataset_uuid_rejected(self) -> None:
        with self.assertRaisesRegex(TradableResearchEvidenceV2Error, "dataset_mismatch"):
            SubjectAwareTradableResearchEvidenceV2.create(
                feature_bundle=_bundle(dataset_version_id=DATASET_ID),
                bar_series=_bar_series(dataset_version_id=OTHER_DATASET_ID),
            )

    def test_different_instrument_rejected(self) -> None:
        with self.assertRaisesRegex(TradableResearchEvidenceV2Error, "instrument_mismatch"):
            SubjectAwareTradableResearchEvidenceV2.create(
                feature_bundle=_bundle(subject_id=INSTRUMENT),
                bar_series=_bar_series(instrument_id=OTHER_INSTRUMENT),
            )

    def test_futures_series_feature_bundle_rejected(self) -> None:
        with self.assertRaisesRegex(TradableResearchEvidenceV2Error, "requires_instrument_subject"):
            SubjectAwareTradableResearchEvidenceV2.create(
                feature_bundle=_bundle(
                    subject_id="TESTFIXTURE:3J2B1:SERIES:GC", subject_type=FeatureSubjectType.FUTURES_SERIES
                ),
                bar_series=_bar_series(instrument_id="TESTFIXTURE:3J2B1:SERIES:GC"),
            )

    def test_deterministic_composite_hash(self) -> None:
        # Reuses the same already-created bundle/series instances: this proves
        # create() is a pure function of its inputs, not that building a fresh
        # FeatureMaterializationV2 fixture is itself deterministic (it isn't --
        # materialization_id is randomly assigned on creation by design).
        bundle = _bundle()
        series = _bar_series()
        first = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=series)
        second = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=series)
        self.assertEqual(first.content_hash, second.content_hash)

    def test_changed_bar_evidence_changes_composite_hash(self) -> None:
        base = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=_bundle(), bar_series=_bar_series())
        different_bars = AuthoritativeTradableBarSeriesV2(
            DATASET_ID, INSTRUMENT, "1m", (_bar(DATASET_ID, INSTRUMENT, START),)
        )
        different_bars.validate()
        changed = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=_bundle(), bar_series=different_bars)
        self.assertNotEqual(base.content_hash, changed.content_hash)

    def test_changed_feature_bundle_hash_changes_composite_hash(self) -> None:
        base = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=_bundle(), bar_series=_bar_series())
        changed_bundle = _bundle()  # a fresh materialization_id/content_hash each call
        changed = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=changed_bundle, bar_series=_bar_series())
        self.assertNotEqual(base.content_hash, changed.content_hash)

    def test_malformed_bar_series_cannot_be_composed(self) -> None:
        # Directly constructed, never passed through AuthoritativeTradableBarSeriesV2.validate()
        # itself -- proves create() invokes the bar series's OWN authoritative
        # validation rather than trusting a caller-supplied series merely
        # because its dataset/instrument strings match.
        bad_bar = _bar(DATASET_ID, INSTRUMENT, START, open_price=Decimal("0"))
        malformed_series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", (bad_bar,))
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "non_positive_bar_price"):
            SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=_bundle(), bar_series=malformed_series)

    def test_malformed_feature_bundle_cannot_be_composed(self) -> None:
        # Directly constructed, bypassing SubjectAwareResearchFeatureBundle.create()
        # (and its own validate() call): a blank subject_id would otherwise
        # slip through if this module only compared dataset/instrument strings.
        good = _bundle()
        malformed_bundle = SubjectAwareResearchFeatureBundle(
            dataset_version_id=good.dataset_version_id, subject_type=good.subject_type, subject_id="",
            decision_at=good.decision_at, quality_policy=good.quality_policy,
            feature_series=good.feature_series, content_hash=good.content_hash, bundle_id=good.bundle_id,
        )
        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "research_bundle_subject_missing"):
            SubjectAwareTradableResearchEvidenceV2.create(
                feature_bundle=malformed_bundle, bar_series=_bar_series()
            )

    def test_composition_fails_closed_before_content_hashing(self) -> None:
        from unittest.mock import patch

        bad_bar = _bar(DATASET_ID, INSTRUMENT, START, open_price=Decimal("-1"))
        malformed_series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", (bad_bar,))
        bundle = _bundle()  # built before patching hashlib -- create() itself hashes internally too
        with (
            patch("trade_platform.tradable_research_evidence_v2.hashlib.sha256") as mock_sha256,
            self.assertRaises(TradableBarEvidenceV2Error),
        ):
            SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=malformed_series)
        mock_sha256.assert_not_called()

    def test_changed_raw_payload_sha256_changes_composite_hash(self) -> None:
        # Otherwise-identical bar identity/timing; only raw_payload_sha256 differs.
        base_bar = _bar(DATASET_ID, INSTRUMENT, START, raw_payload_sha256="f" * 64)
        base_series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", (base_bar,))
        base_series.validate()
        changed_bar = _bar(DATASET_ID, INSTRUMENT, START, raw_payload_sha256="1" + "f" * 63)
        changed_series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", (changed_bar,))
        changed_series.validate()

        bundle = _bundle()
        base = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=base_series)
        changed = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=changed_series)
        self.assertNotEqual(base.content_hash, changed.content_hash)

    def test_changed_dataset_content_hash_changes_composite_hash(self) -> None:
        base_bar = _bar(DATASET_ID, INSTRUMENT, START, dataset_content_hash="e" * 64)
        base_series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", (base_bar,))
        base_series.validate()
        changed_bar = _bar(DATASET_ID, INSTRUMENT, START, dataset_content_hash="2" + "e" * 63)
        changed_series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", (changed_bar,))
        changed_series.validate()

        bundle = _bundle()
        base = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=base_series)
        changed = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=changed_series)
        self.assertNotEqual(base.content_hash, changed.content_hash)

    def test_no_durable_composite_table(self) -> None:
        import trade_platform.tradable_research_evidence_v2 as module

        source = module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            contents = handle.read()
        self.assertNotIn("CREATE TABLE", contents.upper())
        self.assertNotIn("INSERT INTO", contents.upper())


if __name__ == "__main__":
    unittest.main()
