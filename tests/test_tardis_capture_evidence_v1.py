"""Phase 3D.9S.2A -- raw Tardis capture record identity, ordering and coverage.

Every fixture here is written from the documented Bybit V5 / Tardis raw-feed
schema rather than copied out of the vendor's data, so no vendor payload is
retained in this repository. The real-data proof lives in
``test_tardis_capture_engineering_pilot_v1`` and reads a local, read-only sample
directory only when one is configured.
"""

from __future__ import annotations

import json
import unittest
from datetime import date

from trade_platform.tardis_capture_evidence_v1 import (
    TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
    TardisCaptureEvidenceError,
    TardisCaptureGapKindV1,
    TardisChannelV1,
    TardisMessageTypeV1,
    build_tardis_capture_coverage,
    join_contiguous_streams,
    parse_recorder_timestamp_nanos,
    parse_tardis_capture_line,
    parse_tardis_capture_stream,
)

SYMBOL = "BTCUSDT"
SOURCE_DATE = date(2026, 5, 1)
MINUTE_NANOS = 60 * 1_000_000_000
DAY_START_NANOS = parse_recorder_timestamp_nanos("2026-05-01T00:00:00.0000000Z")


def ticker_line(
    arrival: str,
    *,
    message_type: str = "delta",
    mark: str | None = None,
    index: str | None = None,
    cross_sequence: int = 1_000,
    exchange_millis: int = 1_777_593_600_000,
    topic: str = f"tickers.{SYMBOL}",
    symbol: str = SYMBOL,
    extra_data: dict[str, object] | None = None,
) -> str:
    data: dict[str, object] = {"symbol": symbol}
    if mark is not None:
        data["markPrice"] = mark
    if index is not None:
        data["indexPrice"] = index
    if extra_data:
        data.update(extra_data)
    payload = {
        "topic": topic,
        "type": message_type,
        "data": data,
        "cs": cross_sequence,
        "ts": exchange_millis,
    }
    return f"{arrival} {json.dumps(payload, separators=(',', ':'))}"


class ParseCaptureLineTests(unittest.TestCase):
    def test_accepts_a_well_formed_ticker_delta(self) -> None:
        record = parse_tardis_capture_line(
            ticker_line("2026-05-01T00:00:00.0301131Z", mark="76296.10", index="76340.20"),
            channel=TardisChannelV1.TICKERS,
            symbol=SYMBOL,
            source_date=SOURCE_DATE,
        )
        self.assertEqual(record.originating_exchange, "BYBIT")
        self.assertEqual(record.tardis_exchange_id, "bybit")
        self.assertEqual(record.channel, TardisChannelV1.TICKERS)
        self.assertEqual(record.message_type, TardisMessageTypeV1.DELTA)
        self.assertEqual(record.parser_semantic_version, TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION)
        self.assertEqual(record.source_date, SOURCE_DATE)
        self.assertEqual(record.cross_sequence, 1_000)
        self.assertEqual(record.local_timestamp_text, "2026-05-01T00:00:00.0301131Z")

    def test_hundred_nanosecond_precision_survives_parsing_and_hashing(self) -> None:
        earlier = parse_tardis_capture_line(
            ticker_line("2026-05-01T00:00:00.0301131Z", mark="1"),
            channel=TardisChannelV1.TICKERS,
            symbol=SYMBOL,
            source_date=SOURCE_DATE,
        )
        later = parse_tardis_capture_line(
            ticker_line("2026-05-01T00:00:00.0301132Z", mark="1"),
            channel=TardisChannelV1.TICKERS,
            symbol=SYMBOL,
            source_date=SOURCE_DATE,
        )
        # The seventh fractional digit is below datetime resolution, so only the
        # exact nanosecond view and the hash can tell these two apart.
        self.assertEqual(earlier.local_timestamp, later.local_timestamp)
        self.assertEqual(later.local_timestamp_nanos - earlier.local_timestamp_nanos, 100)
        self.assertNotEqual(earlier.content_hash, later.content_hash)

    def test_raw_capture_hash_is_deterministic_and_identity_bound(self) -> None:
        line = ticker_line("2026-05-01T00:00:00.0301131Z", mark="76296.10", index="76340.20")
        first = parse_tardis_capture_line(
            line, channel=TardisChannelV1.TICKERS, symbol=SYMBOL, source_date=SOURCE_DATE
        )
        second = parse_tardis_capture_line(
            line, channel=TardisChannelV1.TICKERS, symbol=SYMBOL, source_date=SOURCE_DATE
        )
        other_day = parse_tardis_capture_line(
            line, channel=TardisChannelV1.TICKERS, symbol=SYMBOL, source_date=date(2026, 6, 1)
        )
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.record_id, second.record_id)
        self.assertNotEqual(first.content_hash, other_day.content_hash)

    def test_generated_record_is_rejected(self) -> None:
        line = ticker_line("2026-05-01T00:00:00.0301131Z", mark="1", index="1")
        payload = json.loads(line.split(" ", 1)[1])
        payload["generated"] = True
        generated = f"2026-05-01T00:00:00.0301131Z {json.dumps(payload, separators=(',', ':'))}"
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            parse_tardis_capture_line(
                generated, channel=TardisChannelV1.TICKERS, symbol=SYMBOL, source_date=SOURCE_DATE
            )
        self.assertEqual(str(raised.exception), "tardis_generated_record_rejected")

    def test_generated_marker_inside_the_payload_is_also_rejected(self) -> None:
        line = ticker_line(
            "2026-05-01T00:00:00.0301131Z", mark="1", index="1", extra_data={"generated": True}
        )
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            parse_tardis_capture_line(
                line, channel=TardisChannelV1.TICKERS, symbol=SYMBOL, source_date=SOURCE_DATE
            )
        self.assertEqual(str(raised.exception), "tardis_generated_record_rejected")

    def test_missing_arrival_timestamp_is_rejected(self) -> None:
        body = ticker_line("2026-05-01T00:00:00.0301131Z", mark="1").split(" ", 1)[1]
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            parse_tardis_capture_line(
                body, channel=TardisChannelV1.TICKERS, symbol=SYMBOL, source_date=SOURCE_DATE
            )
        self.assertEqual(str(raised.exception), "tardis_capture_line_missing_arrival_timestamp")

    def test_non_utc_arrival_timestamp_is_rejected(self) -> None:
        line = ticker_line("2026-05-01T00:00:00.030113+02:00", mark="1")
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            parse_tardis_capture_line(
                line, channel=TardisChannelV1.TICKERS, symbol=SYMBOL, source_date=SOURCE_DATE
            )
        self.assertEqual(str(raised.exception), "tardis_local_timestamp_must_be_utc")

    def test_topic_identity_mismatch_is_rejected(self) -> None:
        line = ticker_line("2026-05-01T00:00:00.0301131Z", mark="1", topic="tickers.ETHUSDT")
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            parse_tardis_capture_line(
                line, channel=TardisChannelV1.TICKERS, symbol=SYMBOL, source_date=SOURCE_DATE
            )
        self.assertEqual(str(raised.exception), "tardis_capture_topic_identity_mismatch")

    def test_payload_symbol_mismatch_is_rejected(self) -> None:
        line = ticker_line("2026-05-01T00:00:00.0301131Z", mark="1", symbol="ETHUSDT")
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            parse_tardis_capture_line(
                line, channel=TardisChannelV1.TICKERS, symbol=SYMBOL, source_date=SOURCE_DATE
            )
        self.assertEqual(str(raised.exception), "tardis_capture_payload_symbol_mismatch")

    def test_malformed_json_is_rejected(self) -> None:
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            parse_tardis_capture_line(
                "2026-05-01T00:00:00.0301131Z {not-json",
                channel=TardisChannelV1.TICKERS,
                symbol=SYMBOL,
                source_date=SOURCE_DATE,
            )
        self.assertEqual(str(raised.exception), "tardis_capture_message_malformed_json")

    def test_unknown_message_type_is_rejected(self) -> None:
        line = ticker_line("2026-05-01T00:00:00.0301131Z", message_type="patch", mark="1")
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            parse_tardis_capture_line(
                line, channel=TardisChannelV1.TICKERS, symbol=SYMBOL, source_date=SOURCE_DATE
            )
        self.assertEqual(str(raised.exception), "tardis_capture_message_type_unknown")


class CaptureStreamTests(unittest.TestCase):
    def test_arrival_order_regression_fails_closed(self) -> None:
        lines = [
            ticker_line("2026-05-01T00:00:01.0000000Z", mark="1"),
            ticker_line("2026-05-01T00:00:00.9000000Z", mark="1"),
        ]
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            parse_tardis_capture_stream(
                lines,
                channel=TardisChannelV1.TICKERS,
                symbol=SYMBOL,
                source_date=SOURCE_DATE,
                declared_start_nanos=DAY_START_NANOS,
                declared_end_nanos=DAY_START_NANOS + MINUTE_NANOS,
            )
        self.assertEqual(str(raised.exception), "tardis_capture_arrival_order_regression")

    def test_record_outside_the_declared_window_fails_closed(self) -> None:
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            parse_tardis_capture_stream(
                [ticker_line("2026-05-01T00:01:30.0000000Z", mark="1")],
                channel=TardisChannelV1.TICKERS,
                symbol=SYMBOL,
                source_date=SOURCE_DATE,
                declared_start_nanos=DAY_START_NANOS,
                declared_end_nanos=DAY_START_NANOS + MINUTE_NANOS,
            )
        self.assertEqual(str(raised.exception), "tardis_capture_record_outside_declared_window")

    def test_declared_window_is_coverage_even_when_the_channel_is_quiet(self) -> None:
        # One record near the window start must not shrink coverage to a point.
        stream = parse_tardis_capture_stream(
            [ticker_line("2026-05-01T00:00:00.5000000Z", mark="1")],
            channel=TardisChannelV1.TICKERS,
            symbol=SYMBOL,
            source_date=SOURCE_DATE,
            declared_start_nanos=DAY_START_NANOS,
            declared_end_nanos=DAY_START_NANOS + MINUTE_NANOS,
        )
        interval = stream.interval()
        self.assertEqual(interval.start_nanos, DAY_START_NANOS)
        self.assertEqual(interval.end_nanos, DAY_START_NANOS + MINUTE_NANOS)
        self.assertTrue(interval.covers_nanos(DAY_START_NANOS + MINUTE_NANOS - 1))
        self.assertFalse(interval.covers_nanos(DAY_START_NANOS + MINUTE_NANOS))


def _window(offset_minutes: int, *, cross_sequence: int, arrival_offset_seconds: int = 0):
    start = DAY_START_NANOS + offset_minutes * MINUTE_NANOS
    arrival_nanos = start + arrival_offset_seconds * 1_000_000_000
    arrival_seconds = arrival_nanos // 1_000_000_000
    from datetime import UTC, datetime, timedelta

    stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=arrival_seconds)
    return parse_tardis_capture_stream(
        [
            ticker_line(
                stamp.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"), mark="1", cross_sequence=cross_sequence
            )
        ],
        channel=TardisChannelV1.TICKERS,
        symbol=SYMBOL,
        source_date=SOURCE_DATE,
        declared_start_nanos=start,
        declared_end_nanos=start + MINUTE_NANOS,
    )


class CaptureCoverageTests(unittest.TestCase):
    def test_abutting_windows_are_one_recording_with_no_gap(self) -> None:
        coverage = build_tardis_capture_coverage(
            [
                _window(0, cross_sequence=100).interval(),
                _window(1, cross_sequence=200).interval(),
            ]
        )
        self.assertEqual(coverage.gaps, ())
        self.assertTrue(coverage.covers_nanos(DAY_START_NANOS + MINUTE_NANOS + 5))

    def test_a_hole_is_represented_and_never_bridged(self) -> None:
        coverage = build_tardis_capture_coverage(
            [
                _window(0, cross_sequence=100).interval(),
                _window(5, cross_sequence=900).interval(),
            ]
        )
        self.assertEqual(len(coverage.gaps), 1)
        gap = coverage.gaps[0]
        self.assertEqual(gap.kind, TardisCaptureGapKindV1.SEQUENCE_ADVANCED_ACROSS_HOLE)
        self.assertEqual(gap.preceding_last_cross_sequence, 100)
        self.assertEqual(gap.following_first_cross_sequence, 900)
        midpoint = (gap.start_nanos + gap.end_nanos) // 2
        self.assertFalse(coverage.covers_nanos(midpoint))
        self.assertIs(coverage.gap_containing_nanos(midpoint), gap)
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            coverage.require_covered(midpoint)
        self.assertEqual(str(raised.exception), "tardis_capture_instant_inside_collection_gap")

    def test_sequence_regression_across_a_hole_is_its_own_kind(self) -> None:
        coverage = build_tardis_capture_coverage(
            [
                _window(0, cross_sequence=900).interval(),
                _window(5, cross_sequence=100).interval(),
            ]
        )
        self.assertEqual(coverage.gaps[0].kind, TardisCaptureGapKindV1.SEQUENCE_REGRESSION)

    def test_instant_outside_all_coverage_fails_closed(self) -> None:
        coverage = build_tardis_capture_coverage([_window(0, cross_sequence=100).interval()])
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            coverage.require_covered(DAY_START_NANOS + 10 * MINUTE_NANOS)
        self.assertEqual(str(raised.exception), "tardis_capture_instant_outside_captured_coverage")

    def test_overlapping_windows_are_refused(self) -> None:
        first = _window(0, cross_sequence=100).interval()
        overlapping = parse_tardis_capture_stream(
            [ticker_line("2026-05-01T00:00:30.0000000Z", mark="1")],
            channel=TardisChannelV1.TICKERS,
            symbol=SYMBOL,
            source_date=SOURCE_DATE,
            declared_start_nanos=DAY_START_NANOS + MINUTE_NANOS // 2,
            declared_end_nanos=DAY_START_NANOS + 2 * MINUTE_NANOS,
        ).interval()
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            build_tardis_capture_coverage([first, overlapping])
        self.assertEqual(str(raised.exception), "tardis_capture_intervals_overlap")


class JoinContiguousStreamsTests(unittest.TestCase):
    def test_abutting_windows_join_into_one_continuous_stream(self) -> None:
        joined = join_contiguous_streams(
            [_window(1, cross_sequence=200), _window(0, cross_sequence=100)]
        )
        self.assertEqual(joined.declared_start_nanos, DAY_START_NANOS)
        self.assertEqual(joined.declared_end_nanos, DAY_START_NANOS + 2 * MINUTE_NANOS)
        self.assertEqual(len(joined.records), 2)
        self.assertEqual(
            [r.local_timestamp_nanos for r in joined.records],
            sorted(r.local_timestamp_nanos for r in joined.records),
        )

    def test_a_hole_refuses_to_join(self) -> None:
        with self.assertRaises(TardisCaptureEvidenceError) as raised:
            join_contiguous_streams([_window(0, cross_sequence=100), _window(5, cross_sequence=900)])
        self.assertEqual(str(raised.exception), "tardis_capture_join_windows_not_contiguous")


if __name__ == "__main__":
    unittest.main()
