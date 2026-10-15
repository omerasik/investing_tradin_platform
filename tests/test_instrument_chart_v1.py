"""Phase R5 UI-1b: chart buckets are exact, catalogued-only and fail closed."""

from __future__ import annotations

import json
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
    CHART_TIMEFRAMES_V1,
    ChartSeriesNotFound,
    ChartSeriesRefView,
    ChartTimeframeUnavailable,
    InstrumentChartError,
    chart_series_payload_hash_v1,
    find_chart_series_v1,
    list_chart_series_v1,
    list_research_window_series_v1,
    read_chart_candles_v1,
    read_chart_series_v1,
)
from trade_platform.research_data_plane_v1 import T2_ARCHIVE_OHLCV_1M_FRAME, ResearchFrameStoreV1
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator
from trade_platform.strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1, identity_hash_v1

# Before the untouched holdout (2026-08-20): T2 bars at or after it are never charted.
START = datetime(2026, 8, 1, tzinfo=UTC)


def _row(opened: datetime, step: int) -> tuple:
    base = Decimal(100 + step)
    return (
        opened, opened + timedelta(minutes=1), None,
        base, base + Decimal("0.75"), base - Decimal("0.25"), base + Decimal("0.5"),
        Decimal("1.001") * (step + 1), Decimal("10"), 3, 0, "false", "false", 0,
        f"t{step}a", f"t{step}z",
    )


def _bars() -> list[tuple]:
    """25 one-minute bars with a 5-minute hole after minute 12 (never filled)."""
    minutes = [*range(13), *range(18, 30)]
    return [_row(START + timedelta(minutes=minute), step) for step, minute in enumerate(minutes)]


def _write_window(store: ResearchFrameStoreV1, symbol: str, first: str, last: str, rows: list[tuple]) -> dict:
    """A T2 research window identity file in the ResearchFrameStoreV1 layout."""
    manifest = store.write_frame(T2_ARCHIVE_OHLCV_1M_FRAME, rows, lineage={"test": symbol})
    identity = {
        "schema_version": "public-archive-bar-dataset-v1", "source_id": str(uuid4()), "symbol": symbol,
        "first_utc_day": first, "last_utc_day": last, "files": [{"utc_day": first}],
        "not_published_days": [], "day_gaps": [],
        "bar_frame": {"logical_content_hash": manifest.logical_content_hash, "row_count": manifest.row_count},
        "last_bar_close_at": rows[-1][1].isoformat(),
    }
    payload = {"identity": identity, "content_hash": identity_hash_v1(identity),
               "dataset_version_id": str(uuid4()), "bar_frame_manifest_hash": manifest.manifest_hash}
    directory = store.root / "datasets" / "public-archive-bars"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{payload['dataset_version_id']}.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


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
            timeframes=list(CHART_TIMEFRAMES_V1),
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

    def test_candles_endpoint_is_protected_bounded_and_fails_closed(self) -> None:
        headers = {"Authorization": "Bearer test-token"}
        path = f"/operator-dashboard/chart-series/{self.manifest.manifest_hash}/candles"
        client = self._client(self.root)
        self.assertEqual(401, client.get(f"{path}?timeframe=1h").status_code)
        response = client.get(f"{path}?timeframe=1m&limit=4", headers=headers)
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual(4, len(body["candles"]))
        self.assertEqual("2026-08-01T00:26:00Z", body["earlier_end"])
        for bad in ("timeframe=5m", "timeframe=1h&limit=5001", "timeframe=1h&end=2026-08-01T00:10:00"):
            self.assertEqual(422, client.get(f"{path}?{bad}", headers=headers).status_code, bad)
        paged = client.get(f"{path}?timeframe=1m&limit=2&end=2026-08-01T00%3A26%3A00Z", headers=headers).json()
        self.assertEqual(["2026-08-01T00:24:00Z", "2026-08-01T00:25:00Z"], [c["open_at"] for c in paged["candles"]])
        self.assertEqual(503, self._client(None).get(f"{path}?timeframe=1h", headers=headers).status_code)
        self.assertEqual(404, self._client(self.root, known=False).get(f"{path}?timeframe=1h", headers=headers).status_code)
        self.series = self.series.model_copy(update={"frame_kind": "OHLCV", "timeframes": []})
        self.assertEqual(404, self._client(self.root).get(f"{path}?timeframe=1h", headers=headers).status_code)

    # The inherited read tests already ran once; skip them here.
    test_buckets_equal_decimal_reference_and_never_fill_gaps = None  # type: ignore[assignment]
    test_reads_are_deterministic = None  # type: ignore[assignment]
    test_a_corrupt_object_fails_closed = None  # type: ignore[assignment]
    test_foreign_instrument_and_bad_bounds_are_refused = None  # type: ignore[assignment]


class ChartCandleTests(unittest.TestCase):
    """Timeframe candles: UTC-aligned, exact, partial candles flagged, gaps never filled."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def _series(self, rows: list[tuple], **update: object) -> ChartSeriesRefView:
        manifest = ResearchFrameStoreV1(self.root).write_frame(
            T2_ARCHIVE_OHLCV_1M_FRAME, rows, lineage={"test": "instrument_chart_candles"},
        )
        return ChartSeriesRefView(
            manifest_hash=manifest.manifest_hash, frame_kind="T2_ARCHIVE_OHLCV_1M", label="fixture",
            dataset_version_id=uuid4(), instrument="ETHUSDT", row_count=manifest.row_count,
            tier_ceiling=NO_TIMING_AUTHORITY_V1, timeframes=list(CHART_TIMEFRAMES_V1),
        ).model_copy(update=update)

    def test_hour_candles_align_to_utc_and_flag_partial_buckets(self) -> None:
        # 00:50..02:59 with minutes 01:10-01:14 missing: 10 + 55 + 60 = 125 bars.
        opened = [START + timedelta(minutes=m) for m in range(50, 180) if not 70 <= m < 75]
        rows = [_row(at, step) for step, at in enumerate(opened)]
        view = read_chart_candles_v1(self._series(rows), research_root=self.root, timeframe="1h")
        self.assertEqual(
            [(START + timedelta(hours=h), START + timedelta(hours=h + 1)) for h in range(3)],
            [(c.open_at, c.close_at) for c in view.candles],
        )
        self.assertEqual([(10, False), (55, False), (60, True)], [(c.bar_count, c.complete) for c in view.candles])
        self.assertEqual(2, view.partial_candles)
        self.assertEqual([], view.gaps)  # every hour has bars: the hole shows as a partial candle, not a gap
        hour = [row for row in rows if START + timedelta(hours=1) <= row[0] < START + timedelta(hours=2)]
        middle = view.candles[1]
        self.assertEqual((hour[0][0], hour[-1][0]), (middle.first_bar_at, middle.last_bar_at))
        self.assertEqual(hour[0][3], Decimal(middle.open))
        self.assertEqual(max(r[4] for r in hour), Decimal(middle.high))
        self.assertEqual(min(r[5] for r in hour), Decimal(middle.low))
        self.assertEqual(hour[-1][6], Decimal(middle.close))
        self.assertEqual(sum(r[7] for r in hour), Decimal(middle.volume))
        self.assertEqual((125, 130, 5), (view.coverage.source_bars, view.coverage.expected_source_bars,
                                         view.coverage.missing_source_bars))
        self.assertEqual(START + timedelta(hours=3), view.coverage.last_bar_close_at)

    def test_minute_gaps_are_declared_and_never_filled(self) -> None:
        view = read_chart_candles_v1(self._series(_bars()), research_root=self.root, timeframe="1m")
        self.assertEqual(25, len(view.candles))
        self.assertTrue(all(c.complete and c.bar_count == 1 for c in view.candles))
        self.assertEqual(1, len(view.gaps))
        gap = view.gaps[0]
        self.assertEqual((START + timedelta(minutes=12), START + timedelta(minutes=18), 5),
                         (gap.after_open_at, gap.before_open_at, gap.missing_candles))

    def test_four_hour_and_day_buckets_open_on_utc_boundaries(self) -> None:
        opened = [START + timedelta(hours=3, minutes=m) for m in range(0, 26 * 60, 30)]
        rows = [_row(at, step) for step, at in enumerate(opened)]
        series = self._series(rows)
        four = read_chart_candles_v1(series, research_root=self.root, timeframe="4h")
        self.assertTrue(all(c.open_at.hour % 4 == 0 and c.open_at.minute == 0 for c in four.candles))
        self.assertEqual(START, four.candles[0].open_at)  # 03:00 lands in the 00:00 bucket
        day = read_chart_candles_v1(series, research_root=self.root, timeframe="1d")
        self.assertEqual([START, START + timedelta(days=1)], [c.open_at for c in day.candles])
        self.assertTrue(all(not c.complete and c.expected_bar_count == 1440 for c in day.candles))

    def test_paging_is_deterministic_and_never_cuts_a_bucket(self) -> None:
        rows = [_row(START + timedelta(minutes=m), m) for m in range(180)]
        series = self._series(rows)
        latest = read_chart_candles_v1(series, research_root=self.root, timeframe="1h", limit=2)
        self.assertEqual([START + timedelta(hours=1), START + timedelta(hours=2)], [c.open_at for c in latest.candles])
        self.assertEqual(START + timedelta(hours=1), latest.earlier_end)
        # An ``end`` inside a bucket keeps that bucket whole rather than truncating it.
        mid = read_chart_candles_v1(series, research_root=self.root, timeframe="1h",
                                    end=START + timedelta(hours=1, minutes=30))
        self.assertEqual([(START, 60), (START + timedelta(hours=1), 60)],
                         [(c.open_at, c.bar_count) for c in mid.candles])
        earlier = read_chart_candles_v1(series, research_root=self.root, timeframe="1h", end=latest.earlier_end)
        self.assertEqual([START], [c.open_at for c in earlier.candles])
        self.assertIsNone(earlier.earlier_end)
        again = read_chart_candles_v1(series, research_root=self.root, timeframe="1h", limit=2)
        self.assertEqual(latest.model_dump(), again.model_dump())

    def test_t2_holdout_bars_are_never_charted(self) -> None:
        boundary = UNTOUCHED_HOLDOUT_BOUNDARY_V1
        rows = [_row(boundary + timedelta(minutes=m), m) for m in range(-90, 30)]
        series = self._series(rows)
        view = read_chart_candles_v1(series, research_root=self.root, timeframe="1h")
        self.assertTrue(all(c.close_at <= boundary for c in view.candles))
        self.assertEqual((90, 30), (view.coverage.source_bars, view.coverage.holdout_excluded_bars))
        self.assertEqual(boundary, view.coverage.last_bar_close_at)
        self.assertTrue(any("untouched holdout" in item for item in view.limitations))
        bucketed = read_chart_series_v1(series, research_root=self.root)
        self.assertEqual((90, 30), (bucketed.bars_in_frame_for_instrument, bucketed.holdout_excluded_bars))
        self.assertTrue(all(b.last_bar_at < boundary for b in bucketed.buckets))
        inside = self._series([_row(boundary + timedelta(minutes=m), m) for m in range(10)])
        self.assertEqual("UNAVAILABLE", read_chart_candles_v1(inside, research_root=self.root, timeframe="1m").state)

    def test_off_grid_or_duplicate_source_bars_fail_closed(self) -> None:
        off_grid = [_row(START, 0), _row(START + timedelta(minutes=1, seconds=30), 1)]
        with self.assertRaisesRegex(InstrumentChartError, "off_the_declared_interval_grid"):
            read_chart_candles_v1(self._series(off_grid), research_root=self.root, timeframe="1m")
        duplicate = [_row(START, 0), _row(START, 1)]
        with self.assertRaisesRegex(InstrumentChartError, "share_an_open_time"):
            read_chart_candles_v1(self._series(duplicate), research_root=self.root, timeframe="1h")

    def test_unoffered_timeframes_and_bounds_are_refused(self) -> None:
        series = self._series(_bars())
        with self.assertRaises(ChartTimeframeUnavailable):
            read_chart_candles_v1(series, research_root=self.root, timeframe="5m")
        with self.assertRaises(ChartTimeframeUnavailable):
            read_chart_candles_v1(series.model_copy(update={"timeframes": []}), research_root=self.root, timeframe="1h")
        with self.assertRaises(ChartTimeframeUnavailable):
            read_chart_candles_v1(series.model_copy(update={"frame_kind": "OHLCV"}), research_root=self.root,
                                  timeframe="1h")
        for limit in (0, 5_001):
            with self.assertRaises(InstrumentChartError):
                read_chart_candles_v1(series, research_root=self.root, timeframe="1h", limit=limit)
        with self.assertRaises(InstrumentChartError):
            read_chart_candles_v1(series, research_root=self.root, timeframe="1h", end=datetime(2026, 8, 1))


class ResearchWindowCatalogTests(unittest.TestCase):
    """T2 research windows: listed from proven identities only, never inside the holdout."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.store = ResearchFrameStoreV1(self.root)
        self.directory = self.store.root / "datasets" / "public-archive-bars"

    def _window(self, symbol: str, first: str, last: str, rows: list[tuple]) -> dict:
        return _write_window(self.store, symbol, first, last, rows)

    def test_proven_windows_are_listed_with_provenance(self) -> None:
        payload = self._window("SOLUSDT", "2026-08-01", "2026-08-01", _bars())
        self._window("BTCUSDT", "2026-08-01", "2026-08-01", _bars()[:3])
        items = list_research_window_series_v1(self.root)
        self.assertEqual(["BTCUSDT", "SOLUSDT"], [item.instrument for item in items])
        sol = items[1]
        self.assertEqual(payload["bar_frame_manifest_hash"], sol.manifest_hash)
        self.assertEqual(payload["content_hash"], sol.window.content_hash)
        self.assertEqual((NO_TIMING_AUTHORITY_V1, 25), (sol.tier_ceiling, sol.row_count))
        self.assertEqual(list(CHART_TIMEFRAMES_V1), sol.timeframes)
        view = read_chart_candles_v1(sol, research_root=self.root, timeframe="1h")
        self.assertEqual(25, view.coverage.source_bars)
        self.assertEqual([], list_research_window_series_v1(None))

    def test_tampered_or_holdout_windows_are_left_out(self) -> None:
        tampered = self._window("ETHUSDT", "2026-08-01", "2026-08-01", _bars())
        tampered["identity"]["symbol"] = "XRPUSDT"
        (self.directory / f"{tampered['dataset_version_id']}.json").write_text(json.dumps(tampered), encoding="utf-8")
        holdout = [_row(UNTOUCHED_HOLDOUT_BOUNDARY_V1 + timedelta(minutes=m), m) for m in range(3)]
        self._window("BTCUSDT", "2026-08-20", "2026-08-20", holdout)
        (self.directory / "broken.json").write_text("{", encoding="utf-8")
        self.assertEqual([], list_research_window_series_v1(self.root))

    def test_a_frame_differing_from_its_window_identity_fails_closed(self) -> None:
        self._window("SOLUSDT", "2026-08-01", "2026-08-01", _bars())
        item = list_research_window_series_v1(self.root)[0]
        forged = item.model_copy(update={"window": item.window.model_copy(update={"bar_logical_content_hash": "0" * 64})})
        with self.assertRaisesRegex(InstrumentChartError, "differs_from_its_identity"):
            read_chart_candles_v1(forged, research_root=self.root, timeframe="1h")


class ChartCatalogEndpointTests(unittest.TestCase):
    def test_catalog_endpoint_lists_research_windows_before_postgres_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            window = _write_window(ResearchFrameStoreV1(root), "BTCUSDT", "2026-08-01", "2026-08-01", _bars())
            queries = Mock()
            queries.chart_series.return_value = list_chart_series_v1(ChartCatalogTests()._cursor("b" * 64))
            client = TestClient(build_app(
                PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("test-token"),
                InMemoryRateLimiter(max_requests=100), operator_dashboard_queries=queries, research_data_root=root,
            ))
            headers = {"Authorization": "Bearer test-token"}
            items = client.get("/operator-dashboard/chart-series", headers=headers).json()["items"]
            self.assertEqual(window["bar_frame_manifest_hash"], items[0]["manifest_hash"])
            self.assertEqual(window["content_hash"], items[0]["window"]["content_hash"])
            self.assertEqual(["T2_ARCHIVE_OHLCV_1M", "OHLCV", "T2_ARCHIVE_OHLCV_1M"], [i["frame_kind"] for i in items])
            self.assertEqual([], items[1]["timeframes"])
            # A window resolves without the PostgreSQL catalogue.
            candles = client.get(f"/operator-dashboard/chart-series/{items[0]['manifest_hash']}/candles?timeframe=1d",
                                 headers=headers)
            self.assertEqual(200, candles.status_code, candles.text)
            queries.chart_series_ref.assert_not_called()


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
