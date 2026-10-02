"""Phase R3B.3 -- archive (T2) vs first-party capture (T4) trade overlap. FIXTURE trades only."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_platform.bybit_archive_t4_overlap_v1 import (
    ArchiveT4OverlapError,
    T4TradeRowV1,
    compare_archive_with_t4_v1,
)
from trade_platform.bybit_public_archive_v1 import ArchiveTradeV1

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
START_MS = (datetime(2026, 9, 20, 12, tzinfo=UTC) - EPOCH) // timedelta(milliseconds=1)
FILES = {"BTCUSDT2026-09-20.csv.gz": {"utc_day": "2026-09-20", "sha256": "a" * 64,
                                      "http_last_modified": "Mon, 21 Sep 2026 01:12:08 GMT"}}


def _t4(index: int, ms: int, **overrides: object) -> T4TradeRowV1:
    event = EPOCH + timedelta(milliseconds=ms)
    trade = T4TradeRowV1(
        trade_id=f"id-{index}", trade_ts_millis=ms, side="Buy", rpi="false", price=Decimal("84000.1"),
        quantity=Decimal("0.010"), event_at=event, market_knowledge_at=event + timedelta(milliseconds=180),
    )
    return replace(trade, **overrides)  # type: ignore[arg-type]


def _archive(index: int, micros: int, **overrides: object) -> ArchiveTradeV1:
    trade = ArchiveTradeV1(
        row_index=index, trade_id=f"id-{index}", timestamp_text=str(Decimal(micros) / 1_000_000),
        trade_ts_micros=micros, side="Buy", price=Decimal("84000.1"), quantity=Decimal("0.010"),
        tick_direction="ZeroPlusTick", rpi=False, published_foreign_notional=Decimal("840.001"),
    )
    return replace(trade, **overrides)  # type: ignore[arg-type]


def _pair(count: int) -> tuple[list[T4TradeRowV1], list[ArchiveTradeV1]]:
    t4 = [_t4(i, START_MS + i * 10) for i in range(count)]
    archive = [_archive(i, (START_MS + i * 10) * 1_000 + 300) for i in range(count)]
    return t4, archive


class OverlapTests(unittest.TestCase):
    def test_identical_tapes_match_fully_and_hash_deterministically(self) -> None:
        t4, archive = _pair(50)
        first = compare_archive_with_t4_v1(parent_seal_content_hash="s" * 64, t4_trades=t4,
                                           archive_trades=archive, archive_files=FILES)
        again = compare_archive_with_t4_v1(parent_seal_content_hash="s" * 64, t4_trades=t4,
                                           archive_trades=iter(archive), archive_files=FILES)
        counts = first.identity["counts"]
        self.assertEqual((counts["matched"], counts["archive_only_inside_span"], counts["t4_only"]), (50, 0, 0))
        self.assertEqual(first.content_hash, again.content_hash)
        self.assertEqual(first.identity["archive_sub_millisecond_remainder_micros"]["0.5"], 300)
        context = first.identity["context_for_or5_not_a_lag"]
        self.assertEqual(context["t4_market_knowledge_minus_event_micros_upper_bound"]["1"], 180_000)
        self.assertEqual(context["archive_http_last_modified"]["BTCUSDT2026-09-20.csv.gz"],
                         "Mon, 21 Sep 2026 01:12:08 GMT")

    def test_capture_misses_boundary_trades_and_archive_omissions_are_separated(self) -> None:
        t4, archive = _pair(50)
        missing_inside = t4.pop(25)
        archive.append(_archive(900, START_MS * 1_000 + 700))       # first millisecond: boundary
        archive.append(_archive(901, (START_MS - 5) * 1_000))       # before the span: ignored
        del archive[10]                                             # archive omission
        report = compare_archive_with_t4_v1(parent_seal_content_hash="s" * 64, t4_trades=t4,
                                            archive_trades=archive, archive_files=FILES)
        counts = report.identity["counts"]
        self.assertEqual(counts["archive_only_inside_span"], 1)
        self.assertEqual(report.identity["ids"]["archive_only_inside_span"], [missing_inside.trade_id])
        self.assertEqual(counts["archive_only_at_span_boundary"], 1)
        self.assertEqual((counts["t4_only"], report.identity["ids"]["t4_only"]), (1, ["id-10"]))

    def test_field_disagreements_are_counted_by_id_never_repaired(self) -> None:
        t4, archive = _pair(10)
        archive[1] = replace(archive[1], price=Decimal("84000.2"))
        archive[2] = replace(archive[2], side="Sell")
        archive[3] = replace(archive[3], rpi=True)
        archive[4] = replace(archive[4], trade_ts_micros=archive[4].trade_ts_micros + 1_000)
        archive[5] = replace(archive[5], quantity=Decimal("0.011"))
        counts = compare_archive_with_t4_v1(parent_seal_content_hash="s" * 64, t4_trades=t4,
                                            archive_trades=archive, archive_files=FILES).identity["counts"]
        self.assertEqual(
            [counts[f"{name}_disagreements"] for name in ("price", "side", "rpi", "millisecond", "quantity")],
            [1, 1, 1, 1, 1],
        )
        self.assertEqual(counts["matched"], 10)

    def test_a_span_day_without_its_archive_file_is_refused(self) -> None:
        t4, archive = _pair(5)
        late = START_MS + 13 * 3_600_000  # the next UTC day
        with self.assertRaisesRegex(ArchiveT4OverlapError, "archive_file_missing_for_span_day:2026-09-21"):
            compare_archive_with_t4_v1(parent_seal_content_hash="s" * 64, t4_trades=[*t4, _t4(99, late)],
                                       archive_trades=archive, archive_files=FILES)
        with self.assertRaisesRegex(ArchiveT4OverlapError, "no_captured_trades"):
            compare_archive_with_t4_v1(parent_seal_content_hash="s" * 64, t4_trades=[],
                                       archive_trades=archive, archive_files=FILES)


if __name__ == "__main__":
    unittest.main()
