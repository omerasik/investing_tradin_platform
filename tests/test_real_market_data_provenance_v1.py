"""Phase 3D.8A: fail-closed real-market-data provenance and its validation effect.

Pure tests: the verdict is a function of persisted-lineage *facts*; the facts
here are FIXTURES shaped like what the Postgres authority reads. The Postgres
path itself is proven in ``test_real_market_data_provenance_v1_postgres``.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from tests import test_open_to_open_validation_orchestration_v1 as orchestration_fixture
from trade_platform.open_to_open_validation_orchestration_v1 import (
    DATA_QUALITY_BLOCKED_REASON,
    EXECUTION_REALISM_BLOCKED_REASON,
    REAL_DATA_SCORECARD_LIMITATIONS,
    REQUIRED_SCORECARD_LIMITATIONS,
    OpenToOpenValidationOrchestrationV1Error,
    run_open_to_open_professional_validation_v1,
)
from trade_platform.real_market_data_provenance_v1 import (
    PROVENANCE_SCHEMA_VERSION,
    STATUS_REAL_DATA,
    STATUS_SYNTHETIC,
    STATUS_UNAVAILABLE,
    DatasetLineageFactsV1,
    PersistedSourceFactsV1,
    RealMarketDataProvenanceError,
    RealMarketDataProvenanceV1,
    canonical_bybit_source_contract_v1,
    evaluate_real_market_data_provenance_v1,
)
from trade_platform.tradable_research_evidence_v2 import SubjectAwareTradableResearchEvidenceV2

CONTRACT = canonical_bybit_source_contract_v1()
DATASET_HASH = "c" * 64
CREATED_AT = datetime(2026, 9, 21, 22, 9, tzinfo=UTC)


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


def _facts(dataset_version_id: object = None, **overrides: object) -> DatasetLineageFactsV1:
    values: dict[str, object] = {
        "dataset_version_id": dataset_version_id or uuid5(NAMESPACE_URL, "3d8a-dataset"),
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
        "member_count_by_kind": (("INDEX_PRICE", 216000), ("MARK_PRICE", 216000),
                                 ("OHLCV", 216000), ("OPEN_INTEREST", 43200)),
    }
    values.update(overrides)
    return DatasetLineageFactsV1(**values)  # type: ignore[arg-type]


class ProvenanceVerdictTests(unittest.TestCase):
    def test_canonical_sealed_lineage_complete_dataset_is_real(self) -> None:
        verdict = evaluate_real_market_data_provenance_v1(_facts())
        self.assertEqual(verdict.status, STATUS_REAL_DATA)
        self.assertEqual(verdict.reasons, ())
        self.assertTrue(verdict.is_proven_real())
        self.assertEqual(verdict.source_contract_content_hash, CONTRACT.content_hash())
        self.assertEqual(verdict.schema_version, PROVENANCE_SCHEMA_VERSION)

    def test_canonical_source_is_the_deterministic_onboarding_identity(self) -> None:
        self.assertEqual(str(CONTRACT.source_id), "a337be59-2019-5458-bc17-dd33750fa359")
        self.assertEqual(CONTRACT.provider, "bybit")
        self.assertEqual(CONTRACT.dataset_name, "bybit_v5_public_market_linear")
        self.assertEqual(CONTRACT.provider_identifier_namespace, "bybit_v5_symbol")
        self.assertEqual(CONTRACT.asset_scope, "CRYPTO")
        self.assertEqual(
            CONTRACT.observation_kinds, ("INDEX_PRICE", "MARK_PRICE", "OHLCV", "OPEN_INTEREST")
        )

    def test_rest_verdict_identity_is_unchanged_by_the_captured_source_authority(self) -> None:
        """Phase 3D.9S.2B added a second authority; this one is byte-identical.

        Pinned literals, not a recomputation: a REST verdict's content hash and
        evidence id are downstream identity and may not drift when an unrelated
        authority is added beside it.
        """
        verdict = evaluate_real_market_data_provenance_v1(_facts())
        self.assertEqual(
            CONTRACT.content_hash(),
            "a48f8dd55e6260027f3f2e1ba2b6f2463141dcaee4dfb142c09e3018fb6c8929",
        )
        self.assertEqual(
            verdict.content_hash,
            "9286eaa7c9402a35432957f4f2c5fe6fdd4e5ff744cf8f63ab053f664f11ee4e",
        )
        self.assertEqual(str(verdict.evidence_id), "e700f2f1-dfc7-5889-bc5a-e4fe6efb45d2")
        # The captured-source field is absent from a REST identity payload, which
        # is exactly why the hash above is unchanged.
        self.assertIsNone(verdict.captured_source_authority_evidence_id)
        self.assertNotIn("captured_source_authority_evidence_id", verdict.identity_payload())

    def test_synthetic_marker_is_synthetic_never_real(self) -> None:
        for field_name, text in (
            ("provider", "TESTFIX_fixture_provider"),
            ("dataset_name", "demo-bybit"),
            ("authorization_reference", "fixture://authorization/bybit"),
        ):
            verdict = evaluate_real_market_data_provenance_v1(
                _facts(source=_source(**{field_name: text}))
            )
            self.assertEqual(verdict.status, STATUS_SYNTHETIC, field_name)
            self.assertFalse(verdict.is_proven_real())

    def test_unresolved_or_unknown_lineage_is_unavailable(self) -> None:
        missing = evaluate_real_market_data_provenance_v1(
            DatasetLineageFactsV1(dataset_version_id=uuid4(), found=False)
        )
        self.assertEqual((missing.status, missing.reasons), (STATUS_UNAVAILABLE, ("dataset_not_found",)))
        unresolved = evaluate_real_market_data_provenance_v1(_facts(source=None))
        self.assertEqual(unresolved.status, STATUS_UNAVAILABLE)
        self.assertIn("source_lineage_unresolved", unresolved.reasons)

    def test_free_text_bybit_with_another_source_id_is_not_real(self) -> None:
        impostor = uuid4()
        verdict = evaluate_real_market_data_provenance_v1(
            _facts(source_id=impostor, source=_source(source_id=impostor))
        )
        self.assertEqual(verdict.status, STATUS_UNAVAILABLE)
        self.assertEqual(verdict.reasons, ("source_id_not_canonical",))
        self.assertIsNone(verdict.source_contract_content_hash)

    def test_every_source_contract_field_mismatch_fails_closed(self) -> None:
        for field_name, value in (
            ("provider", "Bybit"),
            ("dataset_name", "bybit_v5_public_market_inverse"),
            ("provider_identifier_namespace", "bybit_symbol"),
            ("provider_terms_version", "other-terms"),
            ("authorization_reference", CONTRACT.authorization_reference + " (edited)"),
            ("asset_scope", "FUTURES"),
            ("observation_kinds", ("MARK_PRICE", "OHLCV")),
        ):
            verdict = evaluate_real_market_data_provenance_v1(
                _facts(source=_source(**{field_name: value}))
            )
            self.assertEqual(verdict.status, STATUS_UNAVAILABLE, field_name)
            self.assertIn(f"source_contract_mismatch:{field_name}", verdict.reasons)

    def test_unsealed_empty_or_incomplete_lineage_fails_closed(self) -> None:
        for overrides, reason in (
            ({"status": "DRAFT"}, "dataset_not_sealed"),
            ({"member_count": 0, "lineage_complete_member_count": 0}, "dataset_has_no_members"),
            ({"lineage_complete_member_count": 691199}, "member_lineage_incomplete"),
            ({"content_hash": ""}, "dataset_content_hash_missing"),
        ):
            verdict = evaluate_real_market_data_provenance_v1(_facts(**overrides))
            self.assertEqual(verdict.status, STATUS_UNAVAILABLE, reason)
            self.assertIn(reason, verdict.reasons)
            self.assertFalse(verdict.is_proven_real())

    def test_no_caller_can_construct_or_edit_a_real_verdict(self) -> None:
        verdict = evaluate_real_market_data_provenance_v1(_facts())
        values = {name: getattr(verdict, name) for name in verdict.__dataclass_fields__}
        values.pop("_issuer")
        with self.assertRaises(RealMarketDataProvenanceError):
            RealMarketDataProvenanceV1(**values)
        unavailable = evaluate_real_market_data_provenance_v1(_facts(status="DRAFT"))
        edited = replace(unavailable, status=STATUS_REAL_DATA, reasons=())
        self.assertFalse(edited.integrity_verified())
        self.assertFalse(edited.is_proven_real())

    def test_identity_is_deterministic_and_provenance_significant(self) -> None:
        first = evaluate_real_market_data_provenance_v1(_facts())
        self.assertEqual(first.content_hash, evaluate_real_market_data_provenance_v1(_facts()).content_hash)
        # Same instants in another time zone: identical identity.
        cest = timezone(timedelta(hours=2))
        shifted = _facts(
            valid_from=datetime(2026, 4, 22, 2, tzinfo=cest),
            valid_until=datetime(2026, 9, 19, 2, tzinfo=cest),
            created_at=CREATED_AT.astimezone(cest),
        )
        self.assertEqual(first.content_hash, evaluate_real_market_data_provenance_v1(shifted).content_hash)
        for overrides in (
            {"content_hash": "d" * 64},
            {"dataset_version_id": uuid4()},
            {"member_count": 691201, "lineage_complete_member_count": 691201},
            {"valid_until": datetime(2026, 9, 20, tzinfo=UTC)},
        ):
            other = evaluate_real_market_data_provenance_v1(_facts(**overrides))
            self.assertNotEqual(first.content_hash, other.content_hash, overrides)
            self.assertNotEqual(first.evidence_id, other.evidence_id, overrides)


def _real_evidence_and_provenance(
    status_overrides: dict[str, object] | None = None,
) -> tuple[SubjectAwareTradableResearchEvidenceV2, RealMarketDataProvenanceV1]:
    """Fixture evidence whose bars carry the canonical source identity and content hash."""
    base = orchestration_fixture._fixture_evidence()
    bars = tuple(
        replace(bar, source_id=CONTRACT.source_id, dataset_content_hash=DATASET_HASH)
        for bar in base.bar_series.bars
    )
    bar_series = orchestration_fixture._bar_series(bars)
    evidence = SubjectAwareTradableResearchEvidenceV2.create(
        feature_bundle=base.feature_bundle, bar_series=bar_series
    )
    facts = _facts(
        dataset_version_id=orchestration_fixture.DATASET_ID,
        instrument_ids=(orchestration_fixture.INSTRUMENT,),
        **(status_overrides or {}),
    )
    return evidence, evaluate_real_market_data_provenance_v1(facts)


class ValidationProvenanceEffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence, cls.provenance = _real_evidence_and_provenance()
        base_request = orchestration_fixture._request(cls.evidence)
        cls.without = run_open_to_open_professional_validation_v1(base_request)
        cls.real_request = replace(base_request, market_data_provenance=cls.provenance)
        cls.real = run_open_to_open_professional_validation_v1(cls.real_request)

    def test_fixture_only_limitation_disappears_only_for_proven_real_data(self) -> None:
        self.assertTrue(self.provenance.is_proven_real())
        self.assertNotIn(DATA_QUALITY_BLOCKED_REASON, self.real.blocking_reasons)
        self.assertNotIn(DATA_QUALITY_BLOCKED_REASON, self.real.scorecard.limitations)
        self.assertNotIn(DATA_QUALITY_BLOCKED_REASON, self.real.validation_package.limitations)
        self.assertEqual(self.real.data_quality.status, "AVAILABLE")
        self.assertTrue(self.real.data_quality.proven_real_market_data)
        self.assertIs(self.real.validation_package.validation_metadata["fixture_only"], False)
        # Without provenance nothing changes: the fixture limitation stays.
        self.assertIn(DATA_QUALITY_BLOCKED_REASON, self.without.blocking_reasons)
        self.assertEqual(tuple(self.without.scorecard.limitations), REQUIRED_SCORECARD_LIMITATIONS)
        self.assertIsNone(self.without.data_quality.market_data_provenance_status)

    def test_execution_realism_and_authority_limitations_remain(self) -> None:
        self.assertEqual(tuple(self.real.scorecard.limitations), REAL_DATA_SCORECARD_LIMITATIONS)
        for limitation in (
            "NO_REAL_TOP_OF_BOOK", "NO_BROKER_FILL_EVIDENCE", "NO_AUTHORIZED_EXECUTION_REALISM",
            "NO_FUNDING_ACCOUNTING", "REALIZED_ON_EXIT_NOT_MARK_TO_MARKET",
            "NO_INTRATRADE_DRAWDOWN_VISIBILITY", "RESEARCH_ONLY_NO_PAPER_OR_LIVE_AUTHORITY",
        ):
            self.assertIn(limitation, self.real.scorecard.limitations)
            self.assertIn(limitation, self.real.validation_package.limitations)
        self.assertIn(EXECUTION_REALISM_BLOCKED_REASON, self.real.blocking_reasons)
        self.assertIn("RESEARCH_ONLY_NO_PAPER_OR_LIVE_AUTHORITY", self.real.blocking_reasons)
        self.assertEqual(self.real.status, "BLOCKED")
        metadata = self.real.validation_package.validation_metadata
        self.assertIs(metadata["live_authority"], False)
        self.assertIs(metadata["paper_authority"], False)

    def test_synthetic_or_unavailable_provenance_keeps_the_fixture_limitation(self) -> None:
        for overrides in (
            {"source": _source(authorization_reference="fixture://authorization/x")},
            {"status": "DRAFT"},
        ):
            _, provenance = _real_evidence_and_provenance(overrides)
            self.assertFalse(provenance.is_proven_real())
            result = run_open_to_open_professional_validation_v1(
                replace(self.real_request, market_data_provenance=provenance)
            )
            self.assertIn(DATA_QUALITY_BLOCKED_REASON, result.blocking_reasons)
            self.assertIn(DATA_QUALITY_BLOCKED_REASON, result.scorecard.limitations)
            self.assertEqual(result.data_quality.market_data_provenance_status, provenance.status)

    def test_provenance_must_describe_the_exact_evidence(self) -> None:
        other_dataset = evaluate_real_market_data_provenance_v1(
            _facts(instrument_ids=(orchestration_fixture.INSTRUMENT,))
        )
        with self.assertRaisesRegex(OpenToOpenValidationOrchestrationV1Error, "dataset_mismatch"):
            replace(self.real_request, market_data_provenance=other_dataset).validate()
        # Real verdict, but the bars come from another source (the plain fixture bars).
        fixture_request = orchestration_fixture._request(orchestration_fixture._fixture_evidence())
        with self.assertRaisesRegex(OpenToOpenValidationOrchestrationV1Error, "bar_lineage_mismatch"):
            replace(fixture_request, market_data_provenance=self.provenance).validate()
        other_instrument = evaluate_real_market_data_provenance_v1(
            _facts(dataset_version_id=orchestration_fixture.DATASET_ID)
        )
        with self.assertRaisesRegex(OpenToOpenValidationOrchestrationV1Error, "instrument_mismatch"):
            replace(self.real_request, market_data_provenance=other_instrument).validate()
        tampered = replace(self.provenance, member_count=1)
        with self.assertRaisesRegex(OpenToOpenValidationOrchestrationV1Error, "integrity_failed"):
            replace(self.real_request, market_data_provenance=tampered).validate()

    def test_provenance_is_bound_into_deterministic_identity(self) -> None:
        base_request = replace(self.real_request, market_data_provenance=None)
        self.assertEqual(base_request.content_hash(), orchestration_fixture._request(self.evidence).content_hash())
        self.assertNotEqual(self.real_request.content_hash(), base_request.content_hash())
        self.assertNotEqual(self.real.content_hash, self.without.content_hash)
        self.assertNotEqual(self.real.data_quality.content_hash, self.without.data_quality.content_hash)
        # A materially different real verdict (different sealed membership) is a different run.
        _, other = _real_evidence_and_provenance(
            {"member_count": 4608, "lineage_complete_member_count": 4608}
        )
        self.assertTrue(other.is_proven_real())
        other_request = replace(self.real_request, market_data_provenance=other)
        self.assertNotEqual(other_request.content_hash(), self.real_request.content_hash())
        rerun = run_open_to_open_professional_validation_v1(self.real_request)
        self.assertEqual(rerun.content_hash, self.real.content_hash)


if __name__ == "__main__":
    unittest.main()
