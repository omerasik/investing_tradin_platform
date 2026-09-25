"""Phase R3B -- archive bars vs REST klines, offline.

Every archive file and REST page here is a FIXTURE served by an in-memory fake
fetcher; no network call is made.
"""

from __future__ import annotations

import gzip
import json
import shutil
import tempfile
import unittest
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from trade_platform.bybit_archive_rest_crosscheck_v1 import (
    RestKlineV1,
    acquire_rest_klines_day_v1,
    build_crosscheck_report_v1,
    compare_day_v1,
    parse_rest_kline_page_v1,
    rest_kline_url_v1,
    urllib_rest_kline_fetch_v1,
    write_crosscheck_report_v1,
)
from trade_platform.bybit_public_archive_v1 import (
    ARCHIVE_SCHEMA_V1,
    BybitPublicArchiveError,
    HttpResponseV1,
    acquire_archive_day_v1,
    iter_archive_trades_v1,
)

DAY = date(2026, 9, 20)
DAY_START = int(datetime(2026, 9, 20, tzinfo=UTC).timestamp())
DAY_MS = DAY_START * 1000
LATER = datetime(2026, 9, 22, tzinfo=UTC)

# (offset seconds, price, size, rpi)
TRADES = [
    (0.5, "100.0", "0.001", "0"), (10.25, "101.0", "0.002", "0"), (59.5, "99.5", "0.001", "0"),
    (60.0, "102.0", "0.003", "0"), (61.0, "103.0", "0.001", "1"),
    (185.0, "104.0", "0.001", "0"),
]


def _csv(rows: list[tuple[float, str, str, str]]) -> str:
    lines = [",".join(ARCHIVE_SCHEMA_V1)]
    for index, (offset, price, size, rpi) in enumerate(rows):
        notional = str(Decimal(price) * Decimal(size))
        lines.append(
            f"{DAY_START + offset:.4f},BTCUSDT,Buy,{size},{price},PlusTick,id-{index:06d},8.6e+09,"
            f"{size},{notional},{rpi}"
        )
    return "\n".join(lines) + "\n"


def _kline(minute: int, o: str, h: str, low: str, c: str, v: str, t: str) -> list[str]:
    return [str(DAY_MS + minute * 60_000), o, h, low, c, v, t]


#: What the fixture's trades imply, all trades included.
MATCHING = [
    _kline(0, "100.0", "101.0", "99.5", "99.5", "0.004", "0.4015"),
    _kline(1, "102.0", "103.0", "102.0", "103.0", "0.004", "0.4090"),
    _kline(2, "103.0", "103.0", "103.0", "103.0", "0", "0"),  # quiet minute, REST convention
    _kline(3, "104.0", "104.0", "104.0", "104.0", "0.001", "0.1040"),
]


def _page(rows: list[list[str]], *, symbol: str = "BTCUSDT", code: int = 0) -> bytes:
    body = {"retCode": code, "retMsg": "OK", "result": {"category": "linear", "symbol": symbol,
                                                           "list": list(reversed(rows))}}
    return json.dumps(body).encode()


class FakeRest:
    def __init__(self, rows: list[list[str]], *, status: int = 200) -> None:
        self.rows = rows
        self.status = status
        self.urls: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResponseV1:
        self.urls.append(url)
        start = int(url.split("start=")[1].split("&")[0])
        end = int(url.split("end=")[1].split("&")[0])
        rows = [row for row in self.rows if start <= int(row[0]) <= end]
        return HttpResponseV1(self.status, {}, _page(rows) if self.status == 200 else b"")


class FakeArchive:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResponseV1:
        start = int(headers["Range"].removeprefix("bytes=").split("-")[0])
        if start >= len(self.body):
            return HttpResponseV1(416, {}, b"")
        return HttpResponseV1(206, {"content-range": f"bytes {start}-{len(self.body) - 1}/{len(self.body)}",
                                    "etag": '"e"'}, self.body[start:])


def _trades() -> list:
    import io

    return list(iter_archive_trades_v1(io.StringIO(_csv(TRADES)), symbol="BTCUSDT", day=DAY))


def _parsed(rows: list[list[str]]) -> list[RestKlineV1]:
    return parse_rest_kline_page_v1(_page(rows), symbol="BTCUSDT", start_ms=DAY_MS, end_ms=DAY_MS + 1439 * 60_000)


class CrosscheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="r3b2-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_matching_sources_are_consistent_and_a_quiet_rest_minute_is_not_a_disagreement(self) -> None:
        counts = compare_day_v1(_trades(), _parsed(MATCHING), variant="all_trades")
        self.assertEqual(counts["minutes_compared"], 3)
        self.assertEqual(counts["minutes_consistent"], 3)
        self.assertEqual(counts["rest_only_zero_volume_minutes"], 1)
        self.assertEqual(counts["turnover"]["exact"], 3)
        self.assertEqual(counts["inconsistent_minutes_sample"], [])

    def test_disagreements_are_counted_per_field_and_never_bridged(self) -> None:
        rows = [list(row) for row in MATCHING]
        rows[0][2] = "101.5"                       # high differs
        rows[3][5], rows[3][6] = "0.002", "0.2080"  # volume and turnover differ
        rows.append(_kline(9, "105.0", "105.0", "105.0", "105.0", "0.001", "0.1050"))  # REST only, traded
        del rows[1]                                # archive only
        trades = _trades()
        counts = compare_day_v1(trades, _parsed(rows), variant="all_trades")
        self.assertEqual(counts["field_mismatches"]["high"], 1)
        self.assertEqual(counts["field_mismatches"]["volume"], 1)
        self.assertEqual(counts["turnover"]["beyond_rest_print_resolution"], 1)
        self.assertEqual(counts["archive_only_minutes"], 1)
        self.assertEqual(counts["rest_only_minutes_with_volume"], 1)
        self.assertEqual(counts["minutes_consistent"], 0)
        self.assertEqual(counts["inconsistent_minutes_with_rpi_trades"], 1)
        self.assertEqual(counts["inconsistent_minutes_sample"][0], "2026-09-20T00:00Z")
        self.assertEqual(_trades(), trades)  # the archive side is read, never changed

    def test_turnover_within_the_rest_print_resolution_is_classified_not_equated(self) -> None:
        rows = [list(row) for row in MATCHING]
        rows[0][6] = "0.402"  # archive 0.4015: within one unit of the printed 3rd decimal
        counts = compare_day_v1(_trades(), _parsed(rows), variant="all_trades")
        self.assertEqual(counts["turnover"]["within_rest_print_resolution"], 1)
        self.assertEqual(counts["minutes_consistent"], 3)

    def test_a_prior_trade_open_is_diagnosed_but_still_counted_as_a_disagreement(self) -> None:
        rows = [list(row) for row in MATCHING]
        rows[0] = _kline(0, "98.0", "101.0", "98.0", "99.5", "0.004", "0.4015")    # opens at prior 98.0
        rows[1] = _kline(1, "99.5", "103.0", "99.5", "103.0", "0.004", "0.4090")   # opens at minute 0 close
        rows[3] = _kline(3, "103.0", "104.0", "103.0", "104.0", "0.001", "0.1040")  # last trade was minute 1
        counts = compare_day_v1(_trades(), _parsed(rows), variant="all_trades", prior_close=Decimal("98.0"))
        self.assertEqual(counts["field_mismatches"]["open"], 3)
        self.assertEqual(counts["field_mismatches"]["low"], 3)
        self.assertEqual(counts["minutes_consistent"], 0)
        convention = counts["prior_trade_open_convention"]
        self.assertEqual(convention["open_mismatches_matching_it"], 3)
        self.assertEqual(convention["low_mismatches_matching_it"], 3)
        unknown_prior = compare_day_v1(_trades(), _parsed(rows), variant="all_trades")
        self.assertEqual(unknown_prior["prior_trade_open_convention"]["open_mismatches_matching_it"], 2)

    def test_rpi_variant_is_measured_separately(self) -> None:
        without_rpi = [list(row) for row in MATCHING]
        without_rpi[1] = _kline(1, "102.0", "102.0", "102.0", "102.0", "0.003", "0.3060")
        excluding = compare_day_v1(_trades(), _parsed(without_rpi), variant="excluding_rpi")
        including = compare_day_v1(_trades(), _parsed(without_rpi), variant="all_trades")
        self.assertEqual(excluding["minutes_consistent"], 3)
        self.assertEqual(including["minutes_consistent"], 2)
        self.assertEqual(including["inconsistent_minutes_with_rpi_trades"], 1)
        with self.assertRaises(BybitPublicArchiveError):
            compare_day_v1(_trades(), _parsed(MATCHING), variant="rpi_only")

    def test_strict_parse_refuses_untrusted_defects(self) -> None:
        window = {"symbol": "BTCUSDT", "start_ms": DAY_MS, "end_ms": DAY_MS + 60_000}
        bad = [
            _page(MATCHING[:1], code=10001),
            _page(MATCHING[:1], symbol="ETHUSDT"),
            _page([_kline(5, "1", "1", "1", "1", "0", "0")]),                      # outside window
            _page([MATCHING[0], MATCHING[0]]),                                      # repeated minute
            _page([[str(DAY_MS + 1), "1", "1", "1", "1", "0", "0"]]),              # not minute aligned
            _page([_kline(0, "1", "0.5", "1", "1", "0", "0")]),                    # high below open
            _page([_kline(0, "1", "1", "1", "1", "-1", "0")]),                     # negative volume
            _page([_kline(0, "NaN", "1", "1", "1", "0", "0")]),
            _page([_kline(0, "1", "1", "1", "1", "0", "0")[:6]]),                  # six fields
            b"not json",
        ]
        for body in bad:
            with self.subTest(body=body[:60]), self.assertRaises(BybitPublicArchiveError):
                parse_rest_kline_page_v1(body, **window)

    def test_fetch_is_confined_to_the_public_kline_endpoint(self) -> None:
        with self.assertRaises(BybitPublicArchiveError):
            urllib_rest_kline_fetch_v1("https://api.bybit.com/v5/order/create?category=linear&", {})
        with self.assertRaises(BybitPublicArchiveError):
            rest_kline_url_v1("btc/usdt", 0, 0)

    def test_acquisition_is_checkpointed_refuses_an_open_day_and_keeps_nothing_on_failure(self) -> None:
        fake = FakeRest(MATCHING)
        day = acquire_rest_klines_day_v1(self.temp, "BTCUSDT", DAY, fetch=fake, now=lambda: LATER)
        self.assertEqual(len(fake.urls), 2)
        self.assertEqual(len(day.klines), 4)
        again = acquire_rest_klines_day_v1(self.temp, "BTCUSDT", DAY, fetch=fake, now=lambda: LATER)
        self.assertEqual(len(fake.urls), 2)  # re-read from the kept pages
        self.assertEqual(again, day)
        with self.assertRaises(BybitPublicArchiveError):
            acquire_rest_klines_day_v1(self.temp, "BTCUSDT", date(2026, 9, 21), fetch=fake,
                                       now=lambda: datetime(2026, 9, 21, 23, 59, tzinfo=UTC))
        with self.assertRaises(BybitPublicArchiveError):
            acquire_rest_klines_day_v1(self.temp, "BTCUSDT", date(2026, 9, 19), fetch=FakeRest([], status=503),
                                       now=lambda: LATER)
        self.assertEqual(list((self.temp / "rest-kline" / "v1" / "symbol=BTCUSDT").glob("*2026-09-19*")), [])

    def test_report_is_deterministic_binds_both_sources_and_detects_a_swapped_file(self) -> None:
        archive = self.temp / "archive"
        manifest = acquire_archive_day_v1(archive, "BTCUSDT", DAY,
                                          fetch=FakeArchive(gzip.compress(_csv(TRADES).encode(), mtime=0)))
        rest = acquire_rest_klines_day_v1(self.temp, "BTCUSDT", DAY, fetch=FakeRest(MATCHING), now=lambda: LATER)
        report = build_crosscheck_report_v1(archive, [manifest], [rest])
        self.assertEqual(report.content_hash, build_crosscheck_report_v1(archive, [manifest], [rest]).content_hash)
        self.assertFalse(report.identity["bridged"])
        self.assertEqual(report.identity["days"][0]["archive_file_sha256"], manifest.sha256)
        self.assertEqual(report.identity["totals"]["all_trades"]["minutes_consistent"], 3)
        path = write_crosscheck_report_v1(self.temp, report)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["content_hash"], report.content_hash)
        with self.assertRaises(BybitPublicArchiveError):
            build_crosscheck_report_v1(archive, [manifest], [])
        stored = next(archive.rglob("*.csv.gz"))
        stored.write_bytes(gzip.compress(_csv(TRADES[:2]).encode(), mtime=0))
        with self.assertRaises(BybitPublicArchiveError):
            build_crosscheck_report_v1(archive, [manifest], [rest])


if __name__ == "__main__":
    unittest.main()
