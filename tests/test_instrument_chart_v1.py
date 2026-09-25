"""Phase R5 UI-1b: chart buckets are exact, catalogued-only and fail closed."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.evidence_catalog_v1 import NO_TIMING_AUTHORITY_V1
from trade_platform.evidence_tier_authority_v1 import canonical_bybit_rest_timing_contract_v1
from trade_platform.instrument_chart_v1 import (
    ChartSeriesNotFound,
    ChartSeriesRefView,
    InstrumentChartError,
    chart_series_payload_hash_v1,
    find_chart_series_v1,
    list_chart_series_v1,
    read_chart_series_v1,
)
from trade_platform.research_data_plane_v1 import T2_ARCHIVE_OHLCV_1M_FRAME, ResearchFrameStoreV1
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator

START = datetime(2026, 9, 21, tzinfo=UTC)


def _bars() -> list[tuple]:
    """25 one-minute bars with a 5-minute hole after minute 12 (never filled)."""
    rows = []
    minutes = [*range(13), *range(18, 30)]
    for step, minute in enumerate(minutes):
        opened = START + timedelta(minutes=minute)
        base = Decimal(100 + step)
        rows.append((
            opened, opened + timedelta(minutes=1), None,
            base, base + Decimal("0.75"), base - Decimal("0.25"), base + Decimal("0.5"),
            Decimal("1.001") * (step + 1), Decimal("10"), 3, 0, "false", "false", 0,
            f"t{step}a", f"t{step}z",
        ))
    return rows


class _ScriptedCursor:
    def __init__(self, results: list[list[tuple]]) -> None:
        self._results = list(results)
        self._current: list[tuple] = []

    def execute(self, query: str, params: tuple = ()) -> None:
        self._current = self._results.pop(0)

    def fetchall(self) -> list[tuple]:
        return self._current


class ChartSeriesReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.rows = _bars()
        self.manifest = ResearchFrameStoreV1(self.root).write_frame(
            T2_ARCHIVE_OHLCV_1M_FRAME, self.rows, lineage={"test": "instrument_chart"},
        )
        self.series = ChartSeriesRefView(
            manifest_hash=self.manifest.manifest_hash, frame_kind="T2_ARCHIVE_OHLCV_1M", label="fixture",
            dataset_version_id=uuid4(), instrument="BTCUSDT", row_count=0, tier_ceiling=NO_TIMING_AUTHORITY_V1,
        )

    def test_buckets_equal_decimal_reference_and_never_fill_gaps(self) -> None:
        view = read_chart_series_v1(self.series, research_root=self.root, max_points=10)
        self.assertEqual(25, view.bars_in_frame_for_instrument)
        self.assertEqual(3, view.bars_per_bucket)
        self.assertEqual(9, len(view.buckets))
        self.assertEqual(25, sum(bucket.bar_count for bucket in view.buckets))
        for index, bucket in enumerate(view.buckets):
            chunk = self.rows[index * 3:(index + 1) * 3]
            self.assertEqual(chunk[0][0], bucket.first_bar_at)
            self.assertEqual(chunk[-1][0], bucket.last_bar_at)
            self.assertEqual(chunk[0][3], Decimal(bucket.open))
            self.assertEqual(max(row[4] for row in chunk), Decimal(bucket.high))
            self.assertEqual(min(row[5] for row in chunk), Decimal(bucket.low))
            self.assertEqual(chunk[-1][6], Decimal(bucket.close))
            self.assertEqual(sum(row[7] for row in chunk), Decimal(bucket.volume))
        # The bucket straddling the hole spans 12:00 -> 18:00 with 3 real bars, not 7.
        straddle = view.buckets[4]
        self.assertEqual((START + timedelta(minutes=12), START + timedelta(minutes=19), 3),
                         (straddle.first_bar_at, straddle.last_bar_at, straddle.bar_count))
        self.assertEqual(NO_TIMING_AUTHORITY_V1, view.series.tier_ceiling)
        self.assertEqual(25, view.series.row_count)

    def test_reads_are_deterministic(self) -> None:
        first = read_chart_series_v1(self.series, research_root=self.root, max_points=7)
        second = read_chart_series_v1(self.series, research_root=self.root, max_points=7)
        self.assertEqual(chart_series_payload_hash_v1(first), chart_series_payload_hash_v1(second))

    def test_a_corrupt_object_fails_closed(self) -> None:
        store = ResearchFrameStoreV1(self.root)
        path = store.object_path(self.manifest.objects[0])
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 0xFF
        path.write_bytes(bytes(data))
        with self.assertRaises(InstrumentChartError):
            read_chart_series_v1(self.series, research_root=self.root)

    def test_foreign_instrument_and_bad_bounds_are_refused(self) -> None:
        with self.assertRaises(ChartSeriesNotFound):
            read_chart_series_v1(self.series, research_root=self.root, instrument="ETHUSDT")
        for points in (0, 1_001):
            with self.assertRaises(InstrumentChartError):
                read_chart_series_v1(self.series, research_root=self.root, max_points=points)


class ChartEndpointTests(ChartSeriesReadTests):
    def _client(self, research_root: Path | None, *, known: bool = True) -> TestClient:
        from trade_platform.operator_dashboard import DashboardObjectNotFound

        queries = Mock()
        if known:
            queries.chart_series_ref.return_value = self.series
        else:
            queries.chart_series_ref.side_effect = DashboardObjectNotFound("chart_series_not_found")
        return TestClient(build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"),
            InMemoryRateLimiter(max_requests=100), operator_dashboard_queries=queries,
            research_data_root=research_root,
        ))

    def test_endpoint_is_protected_bounded_and_fails_closed(self) -> None:
        headers = {"Authorization": "Bearer test-token"}
        path = f"/operator-dashboard/chart-series/{self.manifest.manifest_hash}"
        client = self._client(self.root)
        self.assertEqual(401, client.get(path).status_code)
        response = client.get(f"{path}?max_points=5", headers=headers)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(5, len(response.json()["buckets"]))
        self.assertEqual(422, client.get(f"{path}?max_points=5000", headers=headers).status_code)
        self.assertEqual(404, client.get(f"{path}?instrument=ETHUSDT", headers=headers).status_code)
        self.assertEqual(503, self._client(None).get(path, headers=headers).status_code)
        self.assertEqual(404, self._client(self.root, known=False).get(path, headers=headers).status_code)

    # The inherited read tests already ran once; skip them here.
    test_buckets_equal_decimal_reference_and_never_fill_gaps = None  # type: ignore[assignment]
    test_reads_are_deterministic = None  # type: ignore[assignment]
    test_a_corrupt_object_fails_closed = None  # type: ignore[assignment]
    test_foreign_instrument_and_bad_bounds_are_refused = None  # type: ignore[assignment]


class ChartCatalogTests(unittest.TestCase):
    def _cursor(self, manifest_hash: str) -> _ScriptedCursor:
        rest = canonical_bybit_rest_timing_contract_v1().source_id
        return _ScriptedCursor([
            [("a" * 64 + " ", uuid4(), 216_000, rest, "composite-v1")],
            [(uuid4(), "BTCUSDT", "2026-09-21", "2026-09-23", manifest_hash)],
            [],
        ])

    def test_catalog_carries_each_source_ceiling(self) -> None:
        page = list_chart_series_v1(self._cursor("b" * 64))
        ceilings = {item.frame_kind: item.tier_ceiling for item in page.items}
        self.assertEqual({"OHLCV": "T1_RETROSPECTIVE", "T2_ARCHIVE_OHLCV_1M": NO_TIMING_AUTHORITY_V1}, ceilings)
        self.assertEqual("a" * 64, page.items[0].manifest_hash)

    def test_only_catalogued_well_formed_hashes_resolve(self) -> None:
        self.assertEqual("T2_ARCHIVE_OHLCV_1M", find_chart_series_v1(self._cursor("b" * 64), "b" * 64).frame_kind)
        for candidate in ("c" * 64, "../" + "b" * 61, "B" * 64, "b" * 63):
            with self.subTest(candidate=candidate), self.assertRaises(ChartSeriesNotFound):
                find_chart_series_v1(self._cursor("b" * 64), candidate)


if __name__ == "__main__":
    unittest.main()
