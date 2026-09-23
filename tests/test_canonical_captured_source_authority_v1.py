"""Phase 3D.9S.2B -- the canonical captured-source authority.

No fixture here is, or pretends to be, an acquired dataset: the lineage facts
describe a hypothetical future captured dataset so the *authority path* can be
proven. Nothing spans the 150-day research window, and the preregistration
blocker is asserted to survive this phase intact.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from trade_platform.bybit_trade_bar_reconstruction_v1 import (
    build_one_minute_trade_bars,
    parse_captured_public_trades,
)
from trade_platform.canonical_captured_source_authority_v1 import (
    CAPTURE_PROVIDER_TARDIS,
    CAPTURED_SOURCE_AUTHORITY_SCHEMA_VERSION,
    DISTINCT_TEMPORAL_CONCEPTS_V1,
    STATUS_CAPTURED_SOURCE_AUTHORIZED,
    STATUS_CAPTURED_SOURCE_UNAUTHORIZED,
    CanonicalCapturedSourceAuthorityError,
    CanonicalCapturedSourceContractV1,
    CapturedSourceAuthorityVerdictV1,
    CapturedSourceEvidenceFactsV1,
    bar_temporal_reasons_v1,
    basis_availability_reasons_v1,
    canonical_tardis_captured_bybit_source_contract_v1,
    evaluate_captured_source_authority_v1,
)
from trade_platform.open_to_open_preregistration_v1 import (
    build_open_to_open_preregistration_v1,
)
from trade_platform.open_to_open_validation_orchestration_v1 import (
    derive_open_to_open_evaluation_span_v1,
)
from trade_platform.real_market_data_provenance_v1 import (
    STATUS_REAL_DATA,
    STATUS_UNAVAILABLE,
    DatasetLineageFactsV1,
    PersistedSourceFactsV1,
    authorized_source_contracts_v1,
    canonical_bybit_source_contract_v1,
    canonical_tardis_captured_source_contract_v1,
    evaluate_real_market_data_provenance_v1,
)
from trade_platform.tardis_capture_evidence_v1 import (
    TardisChannelV1,
    parse_tardis_capture_line,
)

CONTRACT = canonical_tardis_captured_bybit_source_contract_v1()
PROJECTION = canonical_tardis_captured_source_contract_v1()
CAPTURED_DATASET_ID = uuid5(NAMESPACE_URL, "3d9s2b-hypothetical-captured-dataset")
CAPTURE_MANIFEST_HASH = "d" * 64
DATASET_HASH = "e" * 64
CREATED_AT = datetime(2026, 9, 23, 12, tzinfo=UTC)


def _evidence(**overrides: Any) -> CapturedSourceEvidenceFactsV1:
    values: dict[str, Any] = {
        "dataset_version_id": CAPTURED_DATASET_ID,
        "capture_provider": CONTRACT.capture_provider,
        "originating_exchange": CONTRACT.originating_exchange,
        "capture_provider_exchange_identity": CONTRACT.capture_provider_exchange_identity,
        "origin_transport": CONTRACT.origin_transport,
        "instrument_scope": CONTRACT.instrument_scope,
        "channels": CONTRACT.authorized_channels,
        "capture_methodology": CONTRACT.capture_methodology,
        "source_availability_clock": CONTRACT.source_availability_clock,
        "exchange_timestamp_semantics": CONTRACT.exchange_timestamp_semantics,
        "parser_semantic_version": CONTRACT.parser_semantic_version,
        "capture_semantic_version": CONTRACT.capture_semantic_version,
        "provider_terms_version": CONTRACT.provider_terms_version,
        "authorization_reference": CONTRACT.authorization_reference,
        "capture_manifest_hash": CAPTURE_MANIFEST_HASH,
        "contains_generated_records": False,
        "earliest_source_available_at_nanos": 1_777_593_600_000_000_000,
        "latest_source_available_at_nanos": 1_777_593_660_000_000_000,
        "declared_coverage_window_count": 1,
        "spans_unproven_gap": False,
    }
    values.update(overrides)
    return CapturedSourceEvidenceFactsV1(**values)


class CapturedSourceContractTests(unittest.TestCase):
    def test_contract_identity_and_content_hash_are_deterministic(self) -> None:
        self.assertEqual(str(CONTRACT.source_id), "b4f161e8-3ebd-53f5-bfdb-e527f598b08a")
        self.assertEqual(
            CONTRACT.content_hash(),
            "3fda40738e2ee176dcd7df19b9616c33f636f97181dc4b1a8df8330693c2c105",  # pragma: allowlist secret
        )
        self.assertEqual(
            CONTRACT.content_hash(),
            canonical_tardis_captured_bybit_source_contract_v1().content_hash(),
        )

    def test_capture_provider_and_originating_exchange_are_distinct_concepts(self) -> None:
        self.assertEqual(CONTRACT.capture_provider, "tardis")
        self.assertEqual(CONTRACT.originating_exchange, "BYBIT")
        # Tardis' own label for the venue is a third thing again: a vendor
        # string, never the venue and never the capture provider.
        self.assertEqual(CONTRACT.capture_provider_exchange_identity, "bybit")
        self.assertNotEqual(CONTRACT.capture_provider, CONTRACT.originating_exchange)
        self.assertNotEqual(
            CONTRACT.originating_exchange, CONTRACT.capture_provider_exchange_identity
        )

    def test_authorized_scope_is_exactly_the_proven_one(self) -> None:
        self.assertEqual(CONTRACT.instrument_scope, "CRYPTO:BYBIT:BTCUSDT:PERP")
        self.assertEqual(CONTRACT.authorized_channels, ("publicTrade", "tickers"))
        self.assertEqual(CONTRACT.origin_transport, "BYBIT_V5_PUBLIC_WEBSOCKET")
        self.assertEqual(
            CONTRACT.capture_methodology, "HISTORICALLY_RECORDED_REALTIME_WEBSOCKET"
        )
        self.assertEqual(
            CONTRACT.source_availability_clock, "TARDIS_RECORDER_LOCAL_TIMESTAMP"
        )
        self.assertFalse(CONTRACT.generated_records_permitted)

    def test_published_and_derived_evidence_are_separate_lists(self) -> None:
        self.assertEqual(
            CONTRACT.provider_captured_observations,
            ("BYBIT_INDEX_PRICE_UPDATE", "BYBIT_MARK_PRICE_UPDATE", "BYBIT_PUBLIC_TRADE"),
        )
        self.assertEqual(
            CONTRACT.platform_derived_artifacts,
            (
                "PLATFORM_PIT_MARK_INDEX_BASIS",
                "PLATFORM_RECONSTRUCTED_1M_OHLCV",
                "PLATFORM_STRATEGY_FEATURES",
            ),
        )
        # Reconstructed OHLCV and the basis are never claimed as published.
        for derived in CONTRACT.platform_derived_artifacts:
            self.assertNotIn(derived, CONTRACT.provider_captured_observations)
        self.assertEqual(CONTRACT.published_observation_kinds, ("INDEX_PRICE", "MARK_PRICE"))
        self.assertNotIn("OHLCV", CONTRACT.published_observation_kinds)

    def test_source_availability_is_its_own_temporal_concept(self) -> None:
        self.assertIn("source_available_at", DISTINCT_TEMPORAL_CONCEPTS_V1)
        for other in ("event_at", "effective_at", "ingested_at", "normalized_at"):
            self.assertIn(other, DISTINCT_TEMPORAL_CONCEPTS_V1)
        self.assertEqual(len(set(DISTINCT_TEMPORAL_CONCEPTS_V1)), 7)
        # The recorder clock is named as itself, never as one of the others.
        self.assertNotIn("effective", CONTRACT.source_availability_clock.casefold())
        self.assertNotIn("ingested", CONTRACT.source_availability_clock.casefold())

    def test_a_contract_cannot_be_fabricated_outside_this_authority(self) -> None:
        with self.assertRaises(CanonicalCapturedSourceAuthorityError):
            CanonicalCapturedSourceContractV1(
                **{
                    name: getattr(CONTRACT, name)
                    for name in CONTRACT.__slots__
                    if not name.startswith("_")
                }
            )


class CapturedSourceAuthorityVerdictTests(unittest.TestCase):
    def test_exact_capture_semantics_are_authorized(self) -> None:
        verdict = evaluate_captured_source_authority_v1(_evidence())
        self.assertEqual(verdict.status, STATUS_CAPTURED_SOURCE_AUTHORIZED)
        self.assertEqual(verdict.reasons, ())
        self.assertTrue(verdict.is_authorized())
        self.assertEqual(verdict.schema_version, CAPTURED_SOURCE_AUTHORITY_SCHEMA_VERSION)
        self.assertEqual(verdict.contract_content_hash, CONTRACT.content_hash())
        self.assertEqual(verdict.source_id, CONTRACT.source_id)

    def test_wrong_channel_fails(self) -> None:
        verdict = evaluate_captured_source_authority_v1(
            _evidence(channels=(TardisChannelV1.TICKERS.value, "orderbook.500"))
        )
        self.assertEqual(verdict.status, STATUS_CAPTURED_SOURCE_UNAUTHORIZED)
        self.assertIn("captured_source_semantics_mismatch:channels", verdict.reasons)
        self.assertFalse(verdict.is_authorized())

    def test_wrong_originating_exchange_fails_even_with_the_right_capture_provider(self) -> None:
        verdict = evaluate_captured_source_authority_v1(
            _evidence(originating_exchange="BINANCE")
        )
        self.assertIn(
            "captured_source_semantics_mismatch:originating_exchange", verdict.reasons
        )
        self.assertFalse(verdict.is_authorized())

    def test_wrong_instrument_fails(self) -> None:
        verdict = evaluate_captured_source_authority_v1(
            _evidence(instrument_scope="CRYPTO:BYBIT:ETHUSDT:PERP")
        )
        self.assertIn("captured_source_semantics_mismatch:instrument_scope", verdict.reasons)

    def test_wrong_timestamp_semantics_fail(self) -> None:
        verdict = evaluate_captured_source_authority_v1(
            _evidence(source_availability_clock="EXCHANGE_MESSAGE_TIMESTAMP")
        )
        self.assertIn(
            "captured_source_semantics_mismatch:source_availability_clock", verdict.reasons
        )

    def test_a_generated_vendor_record_fails(self) -> None:
        verdict = evaluate_captured_source_authority_v1(
            _evidence(contains_generated_records=True)
        )
        self.assertIn("captured_source_contains_generated_records", verdict.reasons)

    def test_a_missing_recorder_timestamp_fails(self) -> None:
        verdict = evaluate_captured_source_authority_v1(
            _evidence(latest_source_available_at_nanos=None)
        )
        self.assertIn("captured_source_recorder_timestamp_missing", verdict.reasons)

    def test_capture_availability_may_not_move_earlier(self) -> None:
        verdict = evaluate_captured_source_authority_v1(
            _evidence(
                earliest_source_available_at_nanos=1_777_593_660_000_000_000,
                latest_source_available_at_nanos=1_777_593_600_000_000_000,
            )
        )
        self.assertIn("captured_source_availability_moved_earlier", verdict.reasons)

    def test_a_malformed_capture_manifest_hash_fails(self) -> None:
        for bad in ("", "not-a-hash", "D" * 64, "d" * 63):
            with self.subTest(bad=bad):
                verdict = evaluate_captured_source_authority_v1(
                    _evidence(capture_manifest_hash=bad)
                )
                self.assertIn(
                    "captured_source_capture_manifest_hash_malformed", verdict.reasons
                )

    def test_an_unproven_gap_fails_closed(self) -> None:
        verdict = evaluate_captured_source_authority_v1(_evidence(spans_unproven_gap=True))
        self.assertIn("captured_source_spans_unproven_capture_gap", verdict.reasons)
        verdict = evaluate_captured_source_authority_v1(
            _evidence(declared_coverage_window_count=0)
        )
        self.assertIn("captured_source_no_declared_coverage_window", verdict.reasons)

    def test_an_edited_verdict_fails_its_own_integrity_check(self) -> None:
        verdict = evaluate_captured_source_authority_v1(_evidence())
        # A verdict cannot be minted outside this module at all, so an edited
        # copy cannot even be constructed to be re-checked.
        with self.assertRaises(CanonicalCapturedSourceAuthorityError):
            CapturedSourceAuthorityVerdictV1(
                schema_version=verdict.schema_version,
                status=verdict.status,
                reasons=verdict.reasons,
                dataset_version_id=verdict.dataset_version_id,
                source_id=verdict.source_id,
                contract_content_hash=verdict.contract_content_hash,
                capture_manifest_hash=verdict.capture_manifest_hash,
                content_hash=verdict.content_hash,
                evidence_id=verdict.evidence_id,
            )


MINUTE_ZERO_MILLIS = 1_777_593_600_000  # 2026-05-01T00:00:00Z


def _one_trade_record(arrival_text: str):
    """One captured ``publicTrade`` message, written from the documented schema."""
    payload = {
        "topic": "publicTrade.BTCUSDT",
        "type": "snapshot",
        "ts": MINUTE_ZERO_MILLIS,
        "data": [
            {
                "s": "BTCUSDT",
                "BT": False,
                "i": "t1",
                "T": MINUTE_ZERO_MILLIS + 30_000,
                "p": "100.0",
                "v": "1",
                "S": "Buy",
                "seq": 1,
            }
        ],
    }
    line = f"{arrival_text} {json.dumps(payload, separators=(',', ':'))}"
    return parse_tardis_capture_line(
        line,
        channel=TardisChannelV1.PUBLIC_TRADE,
        symbol="BTCUSDT",
        source_date=date(2026, 5, 1),
    )


class CapturedAvailabilityRuleTests(unittest.TestCase):
    def _bars(self, *, delayed: bool):
        arrival = "2026-05-01T00:01:20.1234567Z" if delayed else "2026-05-01T00:00:30.5000000Z"
        return build_one_minute_trade_bars(
            parse_captured_public_trades([_one_trade_record(arrival)], symbol="BTCUSDT"),
            symbol="BTCUSDT",
        )

    def test_completed_bar_availability_never_precedes_the_minute_close(self) -> None:
        bar = self._bars(delayed=False)[0]
        self.assertEqual(bar_temporal_reasons_v1(bar), ())
        self.assertEqual(bar.bar_complete_available_at, bar.bar_close_at)
        self.assertLess(bar.open_available_at, bar.bar_close_at)

    def test_completed_bar_availability_never_precedes_the_final_arrival(self) -> None:
        bar = self._bars(delayed=True)[0]
        self.assertEqual(bar_temporal_reasons_v1(bar), ())
        self.assertGreater(bar.bar_complete_available_at, bar.bar_close_at)
        self.assertEqual(bar.bar_complete_available_at_nanos % 1_000, 700)

    def test_a_bar_completing_before_its_close_boundary_is_refused(self) -> None:
        honest = self._bars(delayed=False)[0]
        tampered = type(honest)(
            **{
                name: (
                    honest.bar_complete_available_at_nanos - 1
                    if name == "bar_complete_available_at_nanos"
                    else getattr(honest, name)
                )
                for name in honest.__slots__
            }
        )
        self.assertIn(
            "bar_complete_available_at_precedes_minute_close", bar_temporal_reasons_v1(tampered)
        )

    def test_basis_availability_is_the_max_of_both_component_clocks(self) -> None:
        from trade_platform.bybit_ticker_state_reconstruction_v1 import PitBasisObservationV1

        record_id = uuid5(NAMESPACE_URL, "captured-basis-fixture")
        honest = PitBasisObservationV1(
            symbol="BTCUSDT",
            basis_value=Decimal("0.0001"),
            mark_value=Decimal("100.01"),
            index_value=Decimal("100.00"),
            mark_record_id=record_id,
            mark_record_content_hash="a" * 64,
            index_record_id=record_id,
            index_record_content_hash="b" * 64,
            mark_exchange_timestamp=None,
            index_exchange_timestamp=None,
            mark_local_timestamp_nanos=1_777_593_600_000_000_000,
            index_local_timestamp_nanos=1_777_593_601_000_000_000,
            research_available_at_nanos=1_777_593_601_000_000_000,
            emitting_record_id=record_id,
            emitting_record_content_hash="c" * 64,
            formula="mark_minus_index_over_index",
            formula_semantic_version="1.0.0",
            basis_quantum="1E-10",
            content_hash="d" * 64,
        )
        self.assertEqual(basis_availability_reasons_v1(honest), ())

        earlier = PitBasisObservationV1(
            **{
                name: (
                    honest.mark_local_timestamp_nanos
                    if name == "research_available_at_nanos"
                    else getattr(honest, name)
                )
                for name in honest.__slots__
            }
        )
        self.assertIn(
            "basis_source_available_at_is_not_max_of_component_arrivals",
            basis_availability_reasons_v1(earlier),
        )

        unbound = PitBasisObservationV1(
            **{
                name: ("" if name == "index_record_content_hash" else getattr(honest, name))
                for name in honest.__slots__
            }
        )
        self.assertIn(
            "basis_component_evidence_identity_not_bound",
            basis_availability_reasons_v1(unbound),
        )


def _captured_source(**overrides: Any) -> PersistedSourceFactsV1:
    values: dict[str, Any] = {
        "source_id": PROJECTION.source_id,
        "provider": PROJECTION.provider,
        "dataset_name": PROJECTION.dataset_name,
        "provider_identifier_namespace": PROJECTION.provider_identifier_namespace,
        "provider_terms_version": PROJECTION.provider_terms_version,
        "authorization_reference": PROJECTION.authorization_reference,
        "asset_scope": PROJECTION.asset_scope,
        "observation_kinds": PROJECTION.observation_kinds,
    }
    values.update(overrides)
    return PersistedSourceFactsV1(**values)


def _captured_facts(**overrides: Any) -> DatasetLineageFactsV1:
    """A hypothetical future captured dataset. Deliberately not a research span."""
    values: dict[str, Any] = {
        "dataset_version_id": CAPTURED_DATASET_ID,
        "found": True,
        "status": "SEALED",
        "version": "tardis-captured-bybit-v1:hypothetical",
        "normalization_version": "bybit-v5-md-v1",
        "content_hash": DATASET_HASH,
        "source_id": PROJECTION.source_id,
        "valid_from": datetime(2026, 5, 1, tzinfo=UTC),
        "valid_until": datetime(2026, 5, 1, 0, 20, tzinfo=UTC),
        "created_at": CREATED_AT,
        "source": _captured_source(),
        "member_count": 40,
        "lineage_complete_member_count": 40,
        "instrument_ids": ("CRYPTO:BYBIT:BTCUSDT:PERP",),
        "member_count_by_kind": (("INDEX_PRICE", 20), ("MARK_PRICE", 20)),
    }
    values.update(overrides)
    return DatasetLineageFactsV1(**values)


class CapturedSourceProvenanceIntegrationTests(unittest.TestCase):
    def test_the_authorized_set_is_exactly_two_exact_contracts(self) -> None:
        authorities = authorized_source_contracts_v1()
        self.assertEqual(len(authorities), 2)
        self.assertEqual(authorities[0].source_id, canonical_bybit_source_contract_v1().source_id)
        self.assertEqual(authorities[1].source_id, CONTRACT.source_id)
        self.assertNotEqual(authorities[0].source_id, authorities[1].source_id)

    def test_captured_dataset_qualifies_only_with_its_companion_authority(self) -> None:
        authority = evaluate_captured_source_authority_v1(_evidence())
        verdict = evaluate_real_market_data_provenance_v1(
            _captured_facts(), captured_source_authority=authority
        )
        self.assertEqual(verdict.status, STATUS_REAL_DATA)
        self.assertEqual(verdict.reasons, ())
        self.assertTrue(verdict.is_proven_real())
        # The capture evidence is bound into the provenance identity.
        self.assertEqual(verdict.captured_source_authority_evidence_id, authority.evidence_id)
        self.assertEqual(verdict.source_contract_content_hash, PROJECTION.content_hash())

    def test_captured_dataset_without_capture_evidence_fails_closed(self) -> None:
        verdict = evaluate_real_market_data_provenance_v1(_captured_facts())
        self.assertEqual(verdict.status, STATUS_UNAVAILABLE)
        self.assertIn("captured_source_authority_evidence_missing", verdict.reasons)
        self.assertFalse(verdict.is_proven_real())
        self.assertIsNone(verdict.captured_source_authority_evidence_id)

    def test_an_unauthorized_capture_cannot_carry_a_captured_dataset(self) -> None:
        authority = evaluate_captured_source_authority_v1(
            _evidence(contains_generated_records=True)
        )
        verdict = evaluate_real_market_data_provenance_v1(
            _captured_facts(), captured_source_authority=authority
        )
        self.assertIn("captured_source_authority_not_authorized", verdict.reasons)
        self.assertFalse(verdict.is_proven_real())

    def test_capture_evidence_for_another_dataset_is_refused(self) -> None:
        authority = evaluate_captured_source_authority_v1(
            _evidence(dataset_version_id=uuid5(NAMESPACE_URL, "another-dataset"))
        )
        verdict = evaluate_real_market_data_provenance_v1(
            _captured_facts(), captured_source_authority=authority
        )
        self.assertIn("captured_source_authority_dataset_mismatch", verdict.reasons)

    def test_free_text_tardis_provider_grants_nothing(self) -> None:
        impostor = uuid5(NAMESPACE_URL, "free-text-tardis-source")
        authority = evaluate_captured_source_authority_v1(_evidence())
        verdict = evaluate_real_market_data_provenance_v1(
            _captured_facts(
                source_id=impostor,
                source=_captured_source(source_id=impostor, provider=CAPTURE_PROVIDER_TARDIS),
            ),
            captured_source_authority=authority,
        )
        self.assertEqual(verdict.status, STATUS_UNAVAILABLE)
        self.assertIn("source_id_not_canonical", verdict.reasons)
        self.assertFalse(verdict.is_proven_real())

    def test_a_captured_source_row_with_drifted_fields_is_refused(self) -> None:
        authority = evaluate_captured_source_authority_v1(_evidence())
        verdict = evaluate_real_market_data_provenance_v1(
            _captured_facts(source=_captured_source(provider_terms_version="whatever")),
            captured_source_authority=authority,
        )
        self.assertIn("source_contract_mismatch:provider_terms_version", verdict.reasons)


class _Bar:
    def __init__(self, open_at: datetime) -> None:
        self.bar_open_at = open_at
        self.bar_close_at = open_at + timedelta(minutes=1)


class _BarSeries:
    def __init__(self, first_open: datetime, last_open: datetime) -> None:
        self.bars = (_Bar(first_open), _Bar(last_open))

    def validate(self) -> None:
        return None


class PreregistrationBlockerSurvivesTests(unittest.TestCase):
    """3D.9S.2B authorizes a contract; it clears no evidence blocker."""

    def test_full_span_feature_decision_time_blocker_is_still_unresolved(self) -> None:
        evaluation_start = datetime(2026, 4, 22, tzinfo=UTC)
        span = derive_open_to_open_evaluation_span_v1(
            bar_series=_BarSeries(  # type: ignore[arg-type]
                evaluation_start,
                evaluation_start + timedelta(days=150) - timedelta(minutes=1),
            )
        )
        authority = evaluate_captured_source_authority_v1(_evidence())
        provenance = evaluate_real_market_data_provenance_v1(
            _captured_facts(), captured_source_authority=authority
        )
        self.assertTrue(provenance.is_proven_real())
        packet = build_open_to_open_preregistration_v1(
            dataset_version_id=CAPTURED_DATASET_ID,
            dataset_content_hash=DATASET_HASH,
            evaluation_span=span,
            created_at=CREATED_AT,
            market_data_provenance=provenance,
            distinct_feature_decision_at_count=None,
        )
        self.assertIn("UNPROVEN_DISTINCT_FEATURE_DECISION_TIMES", packet.unresolved_reasons)

    def test_this_phase_acquires_no_data_and_activates_nothing(self) -> None:
        # The authorization reference states the boundary in the artifact itself.
        reference = CONTRACT.authorization_reference.casefold()
        self.assertIn("no paid subscription", reference)
        self.assertIn("no stored credential", reference)
        self.assertIn("no historical acquisition", reference)
        self.assertIn("no broker, account, order, execution or live-trading authority", reference)


class ExistingBybitRestAuthorityUnchangedTests(unittest.TestCase):
    def test_bybit_rest_contract_identity_and_hash_are_byte_for_byte_unchanged(self) -> None:
        rest = canonical_bybit_source_contract_v1()
        self.assertEqual(str(rest.source_id), "a337be59-2019-5458-bc17-dd33750fa359")
        self.assertEqual(
            rest.content_hash(),
            "a48f8dd55e6260027f3f2e1ba2b6f2463141dcaee4dfb142c09e3018fb6c8929",  # pragma: allowlist secret
        )
        self.assertNotEqual(rest.source_id, PROJECTION.source_id)
        self.assertNotEqual(rest.content_hash(), PROJECTION.content_hash())

    def test_capture_evidence_offered_for_the_rest_source_is_refused(self) -> None:
        rest = canonical_bybit_source_contract_v1()
        facts = _captured_facts(
            source_id=rest.source_id,
            source=PersistedSourceFactsV1(
                source_id=rest.source_id,
                provider=rest.provider,
                dataset_name=rest.dataset_name,
                provider_identifier_namespace=rest.provider_identifier_namespace,
                provider_terms_version=rest.provider_terms_version,
                authorization_reference=rest.authorization_reference,
                asset_scope=rest.asset_scope,
                observation_kinds=rest.observation_kinds,
            ),
        )
        verdict = evaluate_real_market_data_provenance_v1(
            facts, captured_source_authority=evaluate_captured_source_authority_v1(_evidence())
        )
        self.assertIn(
            "captured_source_authority_not_applicable_to_this_source", verdict.reasons
        )


if __name__ == "__main__":
    unittest.main()
