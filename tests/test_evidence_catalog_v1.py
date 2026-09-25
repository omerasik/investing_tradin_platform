"""Phase R5 UI-1: the Evidence & Data read model never claims more than the code does."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.bybit_public_archive_v1 import (
    T2_PUBLICATION_LAG_SLOT_V1,
    bybit_public_trade_archive_contract_v1,
)
from trade_platform.config import PlatformConfig
from trade_platform.evidence_catalog_v1 import (
    NO_TIMING_AUTHORITY_V1,
    read_capture_availability_v1,
    read_evidence_catalog_v1,
    timing_sources_v1,
)
from trade_platform.evidence_tier_authority_v1 import (
    canonical_bybit_rest_timing_contract_v1,
    first_party_bybit_capture_timing_contract_v1,
)
from trade_platform.first_party_capture_archive_v1 import (
    END_PROOF_OPERATOR_BOUNDED_STOP,
    CaptureClockReadingV1,
    CaptureCoverageIntervalV1,
    CaptureLifecycleEventV1,
    CapturePartitionWriterV1,
    build_capture_record_v1,
)
from trade_platform.first_party_capture_authority_v1 import (
    first_party_bybit_capture_contract_v1,
    first_party_bybit_measurement_contract_v1,
)
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator

NOW = datetime(2026, 9, 25, tzinfo=UTC)
DAY = date(2026, 9, 23)
TICKER = (
    '{"topic":"tickers.BTCUSDT","type":"snapshot","ts":1790132704184,'
    '"data":{"symbol":"BTCUSDT","markPrice":"63000.50","indexPrice":"63001.00"}}'
)


def _reading(index: int) -> CaptureClockReadingV1:
    return CaptureClockReadingV1(
        arrival_utc_nanos=1_790_132_695_000_000_000 + index * 1_000_000_000,
        arrival_monotonic_nanos=35_552_500_000_000 + index * 1_000_000_000,
    )


def _clock_sample(index: int, offset_nanos: int) -> CaptureLifecycleEventV1:
    reading = _reading(index)
    return CaptureLifecycleEventV1(
        kind="CLOCK_OFFSET_SAMPLE",
        arrival_utc_nanos=reading.arrival_utc_nanos,
        arrival_monotonic_nanos=reading.arrival_monotonic_nanos,
        detail=json.dumps({"offset_estimate_nanos": offset_nanos, "offset_bound_nanos": 180_000_000}),
    )


def _partition(root: Path, *, contract=None, first: int = 0, count: int = 3, finalize: bool = True,
               clock_offsets: tuple[tuple[int, int], ...] = ()) -> Path:
    contract = contract or first_party_bybit_capture_contract_v1()
    session = uuid4()
    writer = CapturePartitionWriterV1(root=root, contract=contract, session_id=session, day=DAY)
    for index, offset in clock_offsets:
        writer.append_lifecycle(_clock_sample(index, offset))
    payload = TICKER.replace("BTCUSDT", contract.exchange_symbol)
    for step in range(count):
        writer.append_record(build_capture_record_v1(
            contract=contract, session_id=session, sequence=step, clock=_reading(first + step),
            payload_text=payload,
        ))
    if not finalize:
        writer.close_without_finalizing()
        return writer.directory
    writer.declare_coverage(CaptureCoverageIntervalV1(
        start_utc_nanos=_reading(first).arrival_utc_nanos,
        last_proven_utc_nanos=_reading(first + count - 1).arrival_utc_nanos,
        end_proof=END_PROOF_OPERATOR_BOUNDED_STOP,
        record_count=count,
    ))
    writer.finalize()
    return writer.directory


class TimingSourceTests(unittest.TestCase):
    def test_ceilings_come_from_the_registered_contracts_and_the_archive_has_none(self) -> None:
        sources = {view.source_id: view for view in timing_sources_v1()}
        rest = canonical_bybit_rest_timing_contract_v1()
        capture = first_party_bybit_capture_timing_contract_v1()
        archive = bybit_public_trade_archive_contract_v1()
        self.assertEqual(3, len(sources))
        self.assertEqual("T1_RETROSPECTIVE", sources[rest.source_id].tier_ceiling)
        self.assertEqual(rest.content_hash(), sources[rest.source_id].timing_contract_hash)
        self.assertEqual("T4_FIRST_PARTY_CAPTURE", sources[capture.source_id].tier_ceiling)
        # The free archive is authorized for acquisition, not for timing: its T2
        # contract is withheld until OR-5, so it must never display as T2.
        self.assertEqual(NO_TIMING_AUTHORITY_V1, sources[archive.source_id].tier_ceiling)
        self.assertIsNone(sources[archive.source_id].timing_contract_hash)
        self.assertEqual(T2_PUBLICATION_LAG_SLOT_V1, sources[archive.source_id].publication_lag_state)
        self.assertTrue(sources[archive.source_id].requires_declared_publication_lag)


class _ScriptedCursor:
    """Returns one scripted result per ``execute``, in order; records the SQL."""

    def __init__(self, results: list[list[tuple]]) -> None:
        self._results = list(results)
        self._current: list[tuple] = []
        self.statements: list[str] = []

    def execute(self, query: str, params: tuple = ()) -> None:
        self.statements.append(query)
        self._current = self._results.pop(0)

    def fetchall(self) -> list[tuple]:
        return self._current


class EvidenceCatalogTests(unittest.TestCase):
    def test_historical_sources_take_the_ceiling_of_their_source_id_only(self) -> None:
        rest_source = canonical_bybit_rest_timing_contract_v1().source_id
        unknown_source = uuid4()
        t4_id, archive_id = uuid4(), uuid4()
        cursor = _ScriptedCursor([
            [
                (rest_source, "bybit", "linear_klines", 4, 4, NOW, False),
                # A provider name that *sounds* first-party grants nothing.
                (unknown_source, "first_party_capture", "anything", 1, 1, NOW, False),
            ],
            [(7,)],
            [(t4_id, "a" * 64 + "  ", uuid4(), uuid4(), DAY, 0, NOW, NOW, 10, 9, NOW, NOW)],
            [(1,)],
            [(archive_id, "b" * 64, uuid4(), "BTCUSDT", DAY, DAY, 1, 100, T2_PUBLICATION_LAG_SLOT_V1, NOW)],
            [("OHLCV", 2, 691_200, 12_345)],
        ])
        view = read_evidence_catalog_v1(cursor, now=NOW)
        ceilings = {item.source_id: item.tier_ceiling for item in view.historical_sources}
        self.assertEqual("T1_RETROSPECTIVE", ceilings[rest_source])
        self.assertEqual(NO_TIMING_AUTHORITY_V1, ceilings[unknown_source])
        self.assertEqual("AVAILABLE", view.state)
        self.assertEqual(7, view.t4_dataset_total)
        self.assertEqual("a" * 64, view.t4_datasets[0].content_hash)
        self.assertEqual(T2_PUBLICATION_LAG_SLOT_V1, view.public_archive_datasets[0].publication_lag_slot)
        self.assertEqual(691_200, view.research_frames[0].row_count)
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT") for statement in cursor.statements))

    def test_without_the_analytics_extra_the_archive_row_is_dropped_and_said_so(self) -> None:
        # The hardened API container has no pyarrow; the API must start and not invent the contract.
        with patch("trade_platform.evidence_catalog_v1._public_archive_contract", return_value=None):
            view = read_evidence_catalog_v1(_ScriptedCursor([[], [(0,)], [], [(0,)], [], []]), now=NOW)
        self.assertEqual(
            {"T1_RETROSPECTIVE", "T4_FIRST_PARTY_CAPTURE"}, {item.tier_ceiling for item in view.timing_sources},
        )
        self.assertTrue(any("analytics extra absent" in item for item in view.limitations))

    def test_an_empty_catalog_is_unavailable_not_healthy(self) -> None:
        view = read_evidence_catalog_v1(_ScriptedCursor([[], [(0,)], [], [(0,)], [], []]), now=NOW)
        self.assertEqual("UNAVAILABLE", view.state)
        self.assertEqual(3, len(view.timing_sources))


class CaptureAvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "capture"

    def test_no_root_is_unconfigured_and_a_missing_root_is_unavailable(self) -> None:
        self.assertEqual("UNCONFIGURED", read_capture_availability_v1(None, now=NOW).state)
        self.assertEqual("UNAVAILABLE", read_capture_availability_v1(self.root, now=NOW).state)

    def test_only_finalized_partitions_prove_coverage_and_gaps_are_not_bridged(self) -> None:
        _partition(self.root, first=0, count=3, clock_offsets=((0, 9_400_000_000),))
        crashed = _partition(self.root, first=5, count=2, finalize=False,
                             clock_offsets=((5, 9_390_000_000),))
        _partition(self.root, first=10, count=2)
        view = read_capture_availability_v1(self.root, now=NOW)
        (source,) = view.sources
        self.assertEqual("AVAILABLE", view.state)
        self.assertEqual("PRODUCTION", source.purpose)
        self.assertEqual(2, len(source.windows))
        self.assertEqual(5, source.proven_record_count)  # the crashed session's 2 records prove nothing
        self.assertEqual(3.0, source.proven_seconds)
        (gap,) = source.gaps
        self.assertEqual("SESSION_BOUNDARY", gap.kind)
        (excluded,) = source.not_finalized
        self.assertEqual(crashed.relative_to(self.root).as_posix(), excluded.partition)
        self.assertNotIn(str(self.root), excluded.partition)
        self.assertIn("partition_has_no_manifest", excluded.reasons)
        # The newest sample wins even when it sits in an unfinalized partition:
        # it is clock evidence, not coverage.
        self.assertAlmostEqual(9.39, source.latest_clock_offset.offset_estimate_seconds)

    def test_sources_are_never_merged_and_measurement_is_labelled(self) -> None:
        _partition(self.root, contract=first_party_bybit_measurement_contract_v1("ETHUSDT"))
        view = read_capture_availability_v1(self.root, now=NOW)
        (source,) = view.sources
        self.assertEqual(("ETHUSDT", "MEASUREMENT"), (source.exchange_symbol, source.purpose))
        self.assertIsNone(source.latest_clock_offset)


class EvidenceEndpointTests(unittest.TestCase):
    def _client(self, capture_root: Path | None) -> TestClient:
        queries = Mock()
        queries.evidence_catalog.return_value = read_evidence_catalog_v1(
            _ScriptedCursor([[], [(0,)], [], [(0,)], [], []]), now=NOW,
        )
        return TestClient(build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"),
            InMemoryRateLimiter(max_requests=100), operator_dashboard_queries=queries,
            capture_archive_root=capture_root,
        ))

    def test_reads_are_protected_and_get_only(self) -> None:
        client = self._client(None)
        headers = {"Authorization": "Bearer test-token"}
        for path in ("/operator-dashboard/evidence-catalog", "/operator-dashboard/capture-availability"):
            with self.subTest(path=path):
                self.assertEqual(401, client.get(path).status_code)
                response = client.get(path, headers=headers)
                self.assertEqual(200, response.status_code, response.text)
                self.assertNotIn("test-token", response.text)
                self.assertEqual(405, client.post(path, headers=headers).status_code)
        self.assertEqual(
            "UNCONFIGURED",
            client.get("/operator-dashboard/capture-availability", headers=headers).json()["state"],
        )


if __name__ == "__main__":
    unittest.main()
