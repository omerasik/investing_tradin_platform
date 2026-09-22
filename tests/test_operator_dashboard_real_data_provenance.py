"""Phase 3D.8C: operator-surface classification from the canonical provenance authority.

Pure checks (no database): the dashboard classifier only grants
``REAL_DATA_RESEARCH_EVIDENCE`` for a verdict the Phase 3D.8A authority issued
and proved real, synthetic markers keep precedence, a missing lineage stays
``UNAVAILABLE``, and a request proves each distinct dataset id only once.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from trade_platform import operator_dashboard
from trade_platform.operator_dashboard import (
    _provenance_fields,
    _RequestScopedProvenance,
    classify_canonical_historical_dataset_evidence,
)
from trade_platform.real_market_data_provenance_v1 import (
    DatasetLineageFactsV1,
    PersistedSourceFactsV1,
    canonical_bybit_source_contract_v1,
    evaluate_real_market_data_provenance_v1,
)

CONTRACT = canonical_bybit_source_contract_v1()
REAL = "REAL_DATA_RESEARCH_EVIDENCE"
SYNTHETIC = "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY"
UNAVAILABLE = "UNAVAILABLE"


def _source(**overrides: Any) -> PersistedSourceFactsV1:
    values: dict[str, Any] = {
        "source_id": CONTRACT.source_id, "provider": CONTRACT.provider,
        "dataset_name": CONTRACT.dataset_name,
        "provider_identifier_namespace": CONTRACT.provider_identifier_namespace,
        "provider_terms_version": CONTRACT.provider_terms_version,
        "authorization_reference": CONTRACT.authorization_reference,
        "asset_scope": CONTRACT.asset_scope, "observation_kinds": CONTRACT.observation_kinds,
    }
    values.update(overrides)
    return PersistedSourceFactsV1(**values)


def _facts(**overrides: Any) -> DatasetLineageFactsV1:
    values: dict[str, Any] = {
        "dataset_version_id": uuid4(), "found": True, "status": "SEALED",
        "version": "bybit-research-composite-v1:test", "normalization_version": "v1",
        "content_hash": "c" * 64, "source_id": CONTRACT.source_id,
        "valid_from": datetime(2026, 4, 22, tzinfo=UTC),
        "valid_until": datetime(2026, 9, 18, 23, 59, tzinfo=UTC),
        "created_at": datetime(2026, 9, 20, tzinfo=UTC), "source": _source(),
        "member_count": 10, "lineage_complete_member_count": 10,
        "instrument_ids": ("CRYPTO:BYBIT:BTCUSDT-PERP",),
        "member_count_by_kind": (("OHLCV", 10),),
    }
    values.update(overrides)
    return DatasetLineageFactsV1(**values)


class _CountingAuthority:
    def __init__(self, verdicts: dict[UUID, Any]) -> None:
        self.verdicts = verdicts
        self.calls: list[UUID] = []

    def prove(self, dataset_version_id: UUID) -> Any:
        self.calls.append(dataset_version_id)
        return self.verdicts[dataset_version_id]


class CanonicalHistoricalDatasetClassificationTests(unittest.TestCase):
    def test_proven_canonical_lineage_is_real(self) -> None:
        verdict = evaluate_real_market_data_provenance_v1(_facts())
        self.assertEqual(
            classify_canonical_historical_dataset_evidence(verdict, synthetic_marker=False), REAL
        )
        fields = _provenance_fields(verdict, synthetic_marker=False)
        self.assertEqual(fields["provenance_reasons"], [])
        self.assertEqual(fields["provenance_content_hash"], verdict.content_hash)
        self.assertEqual(fields["provenance_evidence_id"], verdict.evidence_id)

    def test_unsealed_dataset_is_unavailable(self) -> None:
        verdict = evaluate_real_market_data_provenance_v1(_facts(status="PENDING"))
        self.assertEqual(
            classify_canonical_historical_dataset_evidence(verdict, synthetic_marker=False),
            UNAVAILABLE,
        )
        self.assertIn(
            "dataset_not_sealed", _provenance_fields(verdict, synthetic_marker=False)["provenance_reasons"]
        )

    def test_unknown_dataset_and_missing_lineage_are_unavailable(self) -> None:
        missing = evaluate_real_market_data_provenance_v1(
            DatasetLineageFactsV1(dataset_version_id=uuid4(), found=False)
        )
        self.assertEqual(
            classify_canonical_historical_dataset_evidence(missing, synthetic_marker=False), UNAVAILABLE
        )
        self.assertEqual(
            classify_canonical_historical_dataset_evidence(None, synthetic_marker=False), UNAVAILABLE
        )
        self.assertEqual(
            _provenance_fields(None, synthetic_marker=False)["provenance_reasons"],
            ["no_historical_dataset_lineage"],
        )

    def test_free_text_bybit_source_is_unavailable(self) -> None:
        impostor = uuid4()
        verdict = evaluate_real_market_data_provenance_v1(
            _facts(source_id=impostor, source=_source(source_id=impostor))
        )
        self.assertEqual(verdict.status, UNAVAILABLE)
        self.assertEqual(
            classify_canonical_historical_dataset_evidence(verdict, synthetic_marker=False), UNAVAILABLE
        )

    def test_source_contract_drift_is_unavailable(self) -> None:
        drifted = replace(CONTRACT, provider_terms_version="drifted-terms")
        verdict = evaluate_real_market_data_provenance_v1(_facts(), drifted)
        self.assertEqual(
            classify_canonical_historical_dataset_evidence(verdict, synthetic_marker=False), UNAVAILABLE
        )

    def test_synthetic_marker_and_synthetic_verdict_win(self) -> None:
        real = evaluate_real_market_data_provenance_v1(_facts())
        self.assertEqual(
            classify_canonical_historical_dataset_evidence(real, synthetic_marker=True), SYNTHETIC
        )
        fixture = evaluate_real_market_data_provenance_v1(
            _facts(source=_source(provider="TESTFIX_fixture"))
        )
        self.assertEqual(
            classify_canonical_historical_dataset_evidence(fixture, synthetic_marker=False), SYNTHETIC
        )
        self.assertEqual(
            classify_canonical_historical_dataset_evidence(None, synthetic_marker=True), SYNTHETIC
        )

    def test_legacy_allowlist_stays_empty_and_is_not_the_real_data_authority(self) -> None:
        self.assertEqual(operator_dashboard._AUTHORIZED_REAL_MARKET_DATA_PROVIDERS, frozenset())
        # A legacy provider-text path with "bybit" still cannot grant real status.
        synthetic, real_verified, complete = operator_dashboard._provenance_flags("bybit")
        self.assertEqual(
            operator_dashboard.classify_research_evidence(
                synthetic_provenance=synthetic, real_data_provenance_verified=real_verified,
                lineage_complete=complete,
            ),
            UNAVAILABLE,
        )

    def test_request_scope_proves_each_distinct_dataset_once(self) -> None:
        composite, daily = uuid4(), uuid4()
        authority = _CountingAuthority({
            composite: evaluate_real_market_data_provenance_v1(_facts(dataset_version_id=composite)),
            daily: evaluate_real_market_data_provenance_v1(_facts(dataset_version_id=daily)),
        })
        provenance = _RequestScopedProvenance(authority)  # type: ignore[arg-type]
        for dataset_version_id in (composite, composite, daily, composite, None, daily):
            provenance.resolve(dataset_version_id)
        self.assertEqual(sorted(map(str, authority.calls)), sorted(map(str, (composite, daily))))
        # A fresh request re-proves: nothing is cached across requests.
        _RequestScopedProvenance(authority).resolve(composite)  # type: ignore[arg-type]
        self.assertEqual(authority.calls.count(composite), 2)


if __name__ == "__main__":
    unittest.main()
