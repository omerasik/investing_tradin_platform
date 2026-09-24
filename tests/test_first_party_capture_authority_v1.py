"""Phase 3Z.2 -- the first-party capture contract must be deterministic and unforgeable.

These tests pin the first-party source identity, prove the proven 3D.9S.2B
availability semantics were carried over without drift, and assert that neither
the Bybit REST authority nor the dormant Tardis authority moved.
"""

from __future__ import annotations

import unittest
from uuid import UUID

from trade_platform.canonical_captured_source_authority_v1 import (
    BAR_TEMPORAL_RULES_V1 as TARDIS_BAR_RULES,
)
from trade_platform.canonical_captured_source_authority_v1 import (
    GAP_AUTHORITY_RULES_V1 as TARDIS_GAP_RULES,
)
from trade_platform.canonical_captured_source_authority_v1 import (
    TICKER_AVAILABILITY_RULES_V1 as TARDIS_TICKER_RULES,
)
from trade_platform.canonical_captured_source_authority_v1 import (
    canonical_tardis_captured_bybit_source_contract_v1,
)
from trade_platform.evidence_tier_authority_v1 import authorized_timing_contracts_v1
from trade_platform.first_party_capture_authority_v1 import (
    BAR_TEMPORAL_RULES_V1,
    GAP_AUTHORITY_RULES_V1,
    MEASUREMENT_SAMPLE_SYMBOLS_V1,
    TICKER_AVAILABILITY_RULES_V1,
    BybitPublicChannelV1,
    FirstPartyCaptureAuthorityError,
    FirstPartyCaptureContractV1,
    authorized_first_party_capture_contracts_v1,
    first_party_bybit_capture_contract_v1,
    first_party_bybit_measurement_contract_v1,
    first_party_bybit_measurement_contracts_v1,
    first_party_bybit_source_id_v1,
    resolve_first_party_capture_contract_v1,
)
from trade_platform.real_market_data_provenance_v1 import canonical_bybit_source_contract_v1

PINNED_FIRST_PARTY_SOURCE_ID = UUID("1a4abe35-ff28-5f3a-9a12-592634f7ccb4")
PINNED_FIRST_PARTY_CONTRACT_HASH = (
    "d76a51fcd3b7420e55f02acb52ddb6ee9b668ec6e09ec5962139f5d0ea675753"  # pragma: allowlist secret
)
PINNED_REST_SOURCE_ID = UUID("a337be59-2019-5458-bc17-dd33750fa359")
PINNED_TARDIS_SOURCE_ID = UUID("b4f161e8-3ebd-53f5-bfdb-e527f598b08a")
PINNED_TARDIS_CONTRACT_HASH = (
    "3fda40738e2ee176dcd7df19b9616c33f636f97181dc4b1a8df8330693c2c105"  # pragma: allowlist secret
)


class ContractIdentityTests(unittest.TestCase):
    def test_source_id_and_hash_are_deterministic(self) -> None:
        first = first_party_bybit_capture_contract_v1()
        second = first_party_bybit_capture_contract_v1()
        self.assertEqual(first.source_id, second.source_id)
        self.assertEqual(first.content_hash(), second.content_hash())
        self.assertEqual(first.source_id, first_party_bybit_source_id_v1())

    def test_contract_cannot_be_minted_outside_the_authority(self) -> None:
        template = first_party_bybit_capture_contract_v1()
        with self.assertRaises(FirstPartyCaptureAuthorityError):
            FirstPartyCaptureContractV1(
                schema_version=template.schema_version,
                capture_provider=template.capture_provider,
                originating_exchange=template.originating_exchange,
                origin_transport=template.origin_transport,
                endpoint=template.endpoint,
                instrument_scope=template.instrument_scope,
                exchange_symbol=template.exchange_symbol,
                authorized_channels=template.authorized_channels,
                capture_methodology=template.capture_methodology,
                source_availability_clock=template.source_availability_clock,
                credential_required=template.credential_required,
                generated_records_permitted=template.generated_records_permitted,
                provider_captured_observations=template.provider_captured_observations,
                platform_derived_artifacts=template.platform_derived_artifacts,
                ticker_availability_rules=template.ticker_availability_rules,
                bar_temporal_rules=template.bar_temporal_rules,
                gap_authority_rules=template.gap_authority_rules,
                clock_semantics=template.clock_semantics,
                capture_refusal_rules=template.capture_refusal_rules,
                record_schema_version=template.record_schema_version,
                capture_semantic_version=template.capture_semantic_version,
                provider_terms_version=template.provider_terms_version,
                authorization_reference=template.authorization_reference,
            )

    def test_recorder_and_exchange_are_separate_actors(self) -> None:
        contract = first_party_bybit_capture_contract_v1()
        self.assertEqual("trade_platform", contract.capture_provider)
        self.assertEqual("BYBIT", contract.originating_exchange)
        self.assertNotEqual(contract.capture_provider, contract.originating_exchange)

    def test_public_feed_needs_no_credential(self) -> None:
        contract = first_party_bybit_capture_contract_v1()
        self.assertFalse(contract.credential_required)
        self.assertTrue(contract.endpoint.startswith("wss://stream.bybit.com/v5/public/"))

    def test_only_two_channels_are_authorized(self) -> None:
        contract = first_party_bybit_capture_contract_v1()
        self.assertEqual(
            (BybitPublicChannelV1.PUBLIC_TRADE.value, BybitPublicChannelV1.TICKERS.value),
            contract.authorized_channels,
        )
        self.assertEqual(("publicTrade.BTCUSDT", "tickers.BTCUSDT"), contract.topics())

    def test_generated_records_are_not_permitted(self) -> None:
        self.assertFalse(first_party_bybit_capture_contract_v1().generated_records_permitted)

    def test_provider_published_and_platform_derived_stay_separate(self) -> None:
        contract = first_party_bybit_capture_contract_v1()
        self.assertEqual((), tuple(
            set(contract.provider_captured_observations)
            & set(contract.platform_derived_artifacts)
        ))

    def test_identity_differs_from_the_dormant_tardis_contract(self) -> None:
        self.assertNotEqual(
            first_party_bybit_source_id_v1(),
            canonical_tardis_captured_bybit_source_contract_v1().source_id,
        )


class ProvenSemanticsCarriedOverTests(unittest.TestCase):
    """The rules 3D.9S.2A proved must not drift when re-stated first-party."""

    def test_ticker_availability_rules_match(self) -> None:
        self.assertEqual(TARDIS_TICKER_RULES, TICKER_AVAILABILITY_RULES_V1)

    def test_bar_temporal_rules_match(self) -> None:
        self.assertEqual(TARDIS_BAR_RULES, BAR_TEMPORAL_RULES_V1)

    def test_gap_authority_rules_match(self) -> None:
        self.assertEqual(TARDIS_GAP_RULES, GAP_AUTHORITY_RULES_V1)

    def test_clock_semantics_refuse_a_nanosecond_accuracy_claim(self) -> None:
        semantics = first_party_bybit_capture_contract_v1().clock_semantics
        self.assertIn(
            "arrival_utc_resolution_is_measured_and_recorded_never_assumed_nanosecond",
            semantics,
        )
        self.assertIn("arrival_is_not_event_at_not_effective_at_and_not_ingested_at", semantics)


class MeasurementContractTests(unittest.TestCase):
    """Phase R1A: measurement contracts are capture contracts, never a universe."""

    def test_production_v1_identity_is_pinned_and_unchanged(self) -> None:
        contract = first_party_bybit_capture_contract_v1()
        self.assertEqual(PINNED_FIRST_PARTY_SOURCE_ID, contract.source_id)
        self.assertEqual(PINNED_FIRST_PARTY_CONTRACT_HASH, contract.content_hash())

    def test_every_sample_symbol_has_a_distinct_deterministic_identity(self) -> None:
        contracts = first_party_bybit_measurement_contracts_v1()
        self.assertEqual(MEASUREMENT_SAMPLE_SYMBOLS_V1, tuple(c.exchange_symbol for c in contracts))
        ids = {contract.source_id for contract in contracts}
        self.assertEqual(len(contracts), len(ids))
        self.assertNotIn(first_party_bybit_source_id_v1(), ids)
        again = first_party_bybit_measurement_contracts_v1()
        self.assertEqual([c.content_hash() for c in contracts], [c.content_hash() for c in again])

    def test_measurement_btcusdt_is_not_the_production_contract(self) -> None:
        measurement = first_party_bybit_measurement_contract_v1("BTCUSDT")
        production = first_party_bybit_capture_contract_v1()
        self.assertEqual(production.topics(), measurement.topics())
        self.assertNotEqual(production.source_id, measurement.source_id)
        self.assertIn("Not a production capture universe", measurement.authorization_reference)

    def test_a_symbol_outside_the_pinned_sample_is_refused(self) -> None:
        with self.assertRaises(FirstPartyCaptureAuthorityError):
            first_party_bybit_measurement_contract_v1("PEPEUSDT")

    def test_measurement_contracts_keep_every_v1_rule(self) -> None:
        production = first_party_bybit_capture_contract_v1()
        for contract in first_party_bybit_measurement_contracts_v1():
            self.assertEqual(production.authorized_channels, contract.authorized_channels)
            self.assertEqual(production.clock_semantics, contract.clock_semantics)
            self.assertEqual(production.gap_authority_rules, contract.gap_authority_rules)
            self.assertEqual(production.capture_refusal_rules, contract.capture_refusal_rules)
            self.assertEqual(production.record_schema_version, contract.record_schema_version)
            self.assertFalse(contract.credential_required)
            self.assertEqual(
                (f"publicTrade.{contract.exchange_symbol}", f"tickers.{contract.exchange_symbol}"),
                contract.topics(),
            )

    def test_closed_set_resolution(self) -> None:
        for contract in authorized_first_party_capture_contracts_v1():
            self.assertEqual(contract, resolve_first_party_capture_contract_v1(contract.source_id))
            self.assertEqual(
                contract, resolve_first_party_capture_contract_v1(str(contract.source_id))
            )
        self.assertIsNone(resolve_first_party_capture_contract_v1(PINNED_TARDIS_SOURCE_ID))
        self.assertIsNone(resolve_first_party_capture_contract_v1("not-a-source"))

    def test_measurement_contracts_are_not_registered_for_evidence_tiers(self) -> None:
        registered = {contract.source_id for contract in authorized_timing_contracts_v1()}
        self.assertIn(first_party_bybit_source_id_v1(), registered)
        for contract in first_party_bybit_measurement_contracts_v1():
            self.assertNotIn(contract.source_id, registered)


class UnchangedIdentityTests(unittest.TestCase):
    def test_bybit_rest_identity_unchanged(self) -> None:
        self.assertEqual(PINNED_REST_SOURCE_ID, canonical_bybit_source_contract_v1().source_id)

    def test_dormant_tardis_identity_and_hash_unchanged(self) -> None:
        tardis = canonical_tardis_captured_bybit_source_contract_v1()
        self.assertEqual(PINNED_TARDIS_SOURCE_ID, tardis.source_id)
        self.assertEqual(PINNED_TARDIS_CONTRACT_HASH, tardis.content_hash())


if __name__ == "__main__":
    unittest.main()
