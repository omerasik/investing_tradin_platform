"""Pure unit coverage for Module 3G.1f.1 OpenFIGI current-identity enrichment.

No real network call is made anywhere in this file -- ``OpenFigiMappingClient``
is exercised only against a patched ``urlopen``, and every fixture body below
is modeled on the two real, owner-authorized anonymous probes run against
OpenFIGI's live ``/v3/mapping`` endpoint for AAPL/XNAS on 2026-09-07.
"""

import json
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from trade_platform.audit import AuditEvent
from trade_platform.domain import AssetClass
from trade_platform.openfigi_identity import (
    OPENFIGI_COMPOSITE_FIGI_NAMESPACE,
    OPENFIGI_FIGI_NAMESPACE,
    OPENFIGI_SHARE_CLASS_FIGI_NAMESPACE,
    OpenFigiAmbiguousMappingError,
    OpenFigiMappingCandidate,
    OpenFigiMappingClient,
    OpenFigiMappingJob,
    OpenFigiMappingMismatchError,
    OpenFigiMappingNotFoundError,
    OpenFigiRequestError,
    OpenFigiUnsupportedInstrumentError,
    build_us_common_stock_mapping_job,
    record_openfigi_capture_evidence,
    validate_and_build_identifier_mappings,
)
from trade_platform.professional_instruments import (
    IdentifierSourceKind,
    InstrumentType,
    LifecycleStatus,
    ProfessionalInstrument,
    RepresentationKind,
    SessionType,
)

REGISTERED_AT = datetime(2023, 1, 1, tzinfo=UTC)
CAPTURED_AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def aapl_instrument() -> ProfessionalInstrument:
    return ProfessionalInstrument(
        instrument_id="US:XNAS:AAPL",
        asset_class=AssetClass.EQUITY,
        instrument_type=InstrumentType.COMMON_STOCK,
        exchange_name="NASDAQ",
        venue="XNAS",
        mic="XNAS",
        canonical_symbol="AAPL",
        listing_date=REGISTERED_AT.date(),
        base_currency="USD",
        quote_currency="USD",
        settlement_currency="USD",
        contract_multiplier=Decimal(1),
        contract_size=Decimal(1),
        tick_size=Decimal("0.01"),
        lot_size=Decimal(1),
        price_precision=2,
        quantity_precision=0,
        trading_timezone="America/New_York",
        market_session_type=SessionType.US_EQUITY,
        representation_kind=RepresentationKind.DIRECT,
        registered_at=REGISTERED_AT,
        lifecycle_status=LifecycleStatus.ACTIVE,
    )


def aapl_candidate(**overrides: object) -> OpenFigiMappingCandidate:
    """The real candidate returned by the second, successful bounded probe."""
    fields: dict[str, object] = {
        "figi": "BBG000B9XRY4",
        "ticker": "AAPL",
        "exch_code": "US",
        "security_type": "Common Stock",
        "security_type2": "Common Stock",
        "market_sector": "Equity",
        "composite_figi": "BBG000B9XRY4",
        "share_class_figi": "BBG001S5N8V8",
    }
    fields.update(overrides)
    return OpenFigiMappingCandidate(**fields)  # type: ignore[arg-type]


class BuildJobTests(unittest.TestCase):
    def test_us_common_stock_builds_the_one_proven_job_shape(self) -> None:
        job = build_us_common_stock_mapping_job(aapl_instrument())
        self.assertEqual(
            job,
            OpenFigiMappingJob(
                id_type="TICKER", id_value="AAPL", exch_code="US", market_sec_des="Equity"
            ),
        )
        self.assertEqual(
            job.to_payload(),
            {"idType": "TICKER", "idValue": "AAPL", "exchCode": "US", "marketSecDes": "Equity"},
        )

    def test_non_equity_asset_class_is_unsupported(self) -> None:
        from dataclasses import replace

        etf = replace(
            aapl_instrument(),
            instrument_id="US:ARCX:SPY",
            asset_class=AssetClass.ETF,
            instrument_type=InstrumentType.ETF,
        )
        with self.assertRaises(OpenFigiUnsupportedInstrumentError):
            build_us_common_stock_mapping_job(etf)

    def test_non_usd_instrument_is_unsupported(self) -> None:
        from dataclasses import replace

        foreign = replace(aapl_instrument(), base_currency="EUR")
        with self.assertRaises(OpenFigiUnsupportedInstrumentError):
            build_us_common_stock_mapping_job(foreign)


class ValidateAndBuildTests(unittest.TestCase):
    def test_unambiguous_response_creates_three_standard_mappings(self) -> None:
        mappings = validate_and_build_identifier_mappings(
            aapl_instrument(),
            (aapl_candidate(),),
            captured_at=CAPTURED_AT,
            request_hash="req-hash",
            response_hash="resp-hash",
        )
        self.assertEqual(len(mappings), 3)
        by_namespace = {mapping.namespace: mapping for mapping in mappings}
        self.assertEqual(
            set(by_namespace),
            {
                OPENFIGI_FIGI_NAMESPACE,
                OPENFIGI_COMPOSITE_FIGI_NAMESPACE,
                OPENFIGI_SHARE_CLASS_FIGI_NAMESPACE,
            },
        )
        self.assertEqual(by_namespace[OPENFIGI_FIGI_NAMESPACE].value, "BBG000B9XRY4")
        self.assertEqual(by_namespace[OPENFIGI_SHARE_CLASS_FIGI_NAMESPACE].value, "BBG001S5N8V8")
        for mapping in mappings:
            self.assertEqual(mapping.source_kind, IdentifierSourceKind.STANDARD)
            self.assertEqual(mapping.valid_from, CAPTURED_AT)
            self.assertIsNone(mapping.valid_until)
            self.assertEqual(mapping.ingested_at, CAPTURED_AT)
            self.assertIn("req-hash", mapping.source_reference)
            self.assertIn("resp-hash", mapping.source_reference)

    def test_missing_share_class_figi_is_handled_without_fabrication(self) -> None:
        mappings = validate_and_build_identifier_mappings(
            aapl_instrument(),
            (aapl_candidate(share_class_figi=None),),
            captured_at=CAPTURED_AT,
            request_hash="req-hash",
            response_hash="resp-hash",
        )
        namespaces = {mapping.namespace for mapping in mappings}
        self.assertEqual(
            namespaces, {OPENFIGI_FIGI_NAMESPACE, OPENFIGI_COMPOSITE_FIGI_NAMESPACE}
        )
        self.assertNotIn(OPENFIGI_SHARE_CLASS_FIGI_NAMESPACE, namespaces)

    def test_zero_candidates_raises_not_found(self) -> None:
        with self.assertRaises(OpenFigiMappingNotFoundError):
            validate_and_build_identifier_mappings(
                aapl_instrument(), (), captured_at=CAPTURED_AT,
                request_hash="req-hash", response_hash="resp-hash",
            )

    def test_multiple_candidates_raises_ambiguous_and_persists_nothing(self) -> None:
        with self.assertRaises(OpenFigiAmbiguousMappingError):
            validate_and_build_identifier_mappings(
                aapl_instrument(),
                (aapl_candidate(), aapl_candidate(figi="BBG000OTHER0")),
                captured_at=CAPTURED_AT,
                request_hash="req-hash",
                response_hash="resp-hash",
            )

    def test_ticker_mismatch_raises_and_persists_nothing(self) -> None:
        with self.assertRaises(OpenFigiMappingMismatchError):
            validate_and_build_identifier_mappings(
                aapl_instrument(),
                (aapl_candidate(ticker="MSFT"),),
                captured_at=CAPTURED_AT,
                request_hash="req-hash",
                response_hash="resp-hash",
            )

    def test_exch_code_mismatch_raises_and_persists_nothing(self) -> None:
        with self.assertRaises(OpenFigiMappingMismatchError):
            validate_and_build_identifier_mappings(
                aapl_instrument(),
                (aapl_candidate(exch_code="CA"),),
                captured_at=CAPTURED_AT,
                request_hash="req-hash",
                response_hash="resp-hash",
            )

    def test_security_type_mismatch_raises_and_persists_nothing(self) -> None:
        with self.assertRaises(OpenFigiMappingMismatchError):
            validate_and_build_identifier_mappings(
                aapl_instrument(),
                (aapl_candidate(security_type="Preferred Stock"),),
                captured_at=CAPTURED_AT,
                request_hash="req-hash",
                response_hash="resp-hash",
            )

    def test_market_sector_mismatch_raises_and_persists_nothing(self) -> None:
        with self.assertRaises(OpenFigiMappingMismatchError):
            validate_and_build_identifier_mappings(
                aapl_instrument(),
                (aapl_candidate(market_sector="Corp"),),
                captured_at=CAPTURED_AT,
                request_hash="req-hash",
                response_hash="resp-hash",
            )

    def test_naive_captured_at_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_and_build_identifier_mappings(
                aapl_instrument(),
                (aapl_candidate(),),
                captured_at=datetime(2026, 9, 7, 12, 0),  # noqa: DTZ001 -- intentionally naive
                request_hash="req-hash",
                response_hash="resp-hash",
            )


class MappingClientTests(unittest.TestCase):
    def test_unresolved_job_shape_returns_zero_candidates(self) -> None:
        """Real observed shape from probe #1: only a "warning" key, no "data" key."""
        payload = json.dumps([{"warning": "No identifier found."}]).encode()
        with patch("trade_platform.openfigi_identity.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = payload
            client = OpenFigiMappingClient()
            candidates, request_hash, response_hash = client.map_jobs(
                (OpenFigiMappingJob(id_type="TICKER", id_value="AAPL", mic_code="XNAS"),)
            )
        self.assertEqual(candidates, ())
        self.assertTrue(request_hash)
        self.assertTrue(response_hash)

    def test_resolved_job_shape_returns_one_candidate(self) -> None:
        """Real observed shape from probe #2: a "data" list with one candidate."""
        raw_candidate = {
            "figi": "BBG000B9XRY4",
            "name": "APPLE INC",
            "ticker": "AAPL",
            "exchCode": "US",
            "compositeFIGI": "BBG000B9XRY4",
            "securityType": "Common Stock",
            "marketSector": "Equity",
            "shareClassFIGI": "BBG001S5N8V8",
            "securityType2": "Common Stock",
            "securityDescription": "AAPL",
        }
        payload = json.dumps([{"data": [raw_candidate]}]).encode()
        with patch("trade_platform.openfigi_identity.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = payload
            client = OpenFigiMappingClient()
            candidates, _, _ = client.map_jobs(
                (OpenFigiMappingJob(id_type="TICKER", id_value="AAPL", exch_code="US"),)
            )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].figi, "BBG000B9XRY4")
        self.assertEqual(candidates[0].share_class_figi, "BBG001S5N8V8")

    def test_job_batch_size_is_bounded(self) -> None:
        client = OpenFigiMappingClient()
        with self.assertRaises(ValueError):
            client.map_jobs(())

    def test_response_shape_mismatch_raises_request_error(self) -> None:
        payload = json.dumps({"not": "a list"}).encode()
        with patch("trade_platform.openfigi_identity.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = payload
            client = OpenFigiMappingClient()
            with self.assertRaises(OpenFigiRequestError):
                client.map_jobs((OpenFigiMappingJob(id_type="TICKER", id_value="AAPL"),))

    def test_undecodable_response_raises_request_error(self) -> None:
        with patch("trade_platform.openfigi_identity.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = b"not json"
            client = OpenFigiMappingClient()
            with self.assertRaises(OpenFigiRequestError):
                client.map_jobs((OpenFigiMappingJob(id_type="TICKER", id_value="AAPL"),))


class EvidenceRecordingTests(unittest.TestCase):
    def test_evidence_records_identifiers_and_hashes_only(self) -> None:
        class FakeAuditStore:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, object]]] = []

            def append(
                self, event_type: str, actor: str, payload: dict[str, object]
            ) -> AuditEvent:
                self.calls.append((event_type, actor, payload))
                from uuid import uuid4

                return AuditEvent(uuid4(), event_type, CAPTURED_AT, actor, payload)

            def recent(self, limit: int = 100) -> list[AuditEvent]:
                return []

            def query(self, **kwargs: object) -> tuple[list[AuditEvent], bool]:
                return [], False

            def get(self, event_id: object) -> AuditEvent | None:
                return None

        store = FakeAuditStore()
        mappings = validate_and_build_identifier_mappings(
            aapl_instrument(),
            (aapl_candidate(),),
            captured_at=CAPTURED_AT,
            request_hash="req-hash",
            response_hash="resp-hash",
        )
        record_openfigi_capture_evidence(
            store,
            instrument_id="US:XNAS:AAPL",
            mappings=mappings,
            request_hash="req-hash",
            response_hash="resp-hash",
            captured_at=CAPTURED_AT,
        )
        self.assertEqual(len(store.calls), 1)
        event_type, actor, payload = store.calls[0]
        self.assertEqual(event_type, "openfigi_identity_enrichment_captured")
        self.assertEqual(actor, "openfigi_identity_enrichment")
        self.assertEqual(payload["request_content_hash"], "req-hash")
        self.assertEqual(payload["response_content_hash"], "resp-hash")
        encoded_payload = json.dumps(payload)
        for descriptive_marker in ("APPLE INC", "securityDescription", "ticker", "exchCode"):
            self.assertNotIn(descriptive_marker, encoded_payload)


if __name__ == "__main__":
    unittest.main()
