"""Phase 3D.9S.2A -- the zero-cost Tardis capture engineering verdict.

Two layers. The synthetic layer proves every verdict rule from fixtures written
against the documented schema. The integration layer replays the actual free
first-of-month Tardis sample and is **skipped unless**
``TARDIS_FREE_SAMPLE_DIR`` points at a local, read-only directory holding it:
vendor capture data is never retained in this repository, so the real-data proof
stays local by construction. Expected file names in that directory are
``bybit_<channel>_<YYYY-MM-DD>_o<minute offset>.ndjson``.
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from trade_platform.bybit_ticker_state_reconstruction_v1 import reconstruct_bybit_ticker_basis
from trade_platform.bybit_trade_bar_reconstruction_v1 import (
    build_one_minute_trade_bars,
    parse_captured_public_trades,
)
from trade_platform.tardis_capture_engineering_pilot_v1 import (
    UNTOUCHED_HOLDOUT_BOUNDARY_V1,
    TardisCaptureEngineeringPilotError,
    TardisCaptureEngineeringVerdictV1,
    evaluate_tardis_capture_engineering_pilot,
)
from trade_platform.tardis_capture_evidence_v1 import (
    TardisChannelV1,
    build_tardis_capture_coverage,
    join_contiguous_streams,
    parse_tardis_capture_stream,
)

SYMBOL = "BTCUSDT"
SOURCE_DATE = date(2026, 5, 1)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MINUTE_NANOS = 60 * 1_000_000_000
SAMPLE_DIR_ENV = "TARDIS_FREE_SAMPLE_DIR"


def window_nanos(day: date, offset_minutes: int) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(minutes=offset_minutes)
    start_nanos = int((start - EPOCH).total_seconds()) * 1_000_000_000
    return start_nanos, start_nanos + MINUTE_NANOS


def ticker_line(arrival: str, *, message_type: str, mark: str | None, index: str | None, cs: int) -> str:
    data: dict[str, object] = {"symbol": SYMBOL}
    if mark is not None:
        data["markPrice"] = mark
    if index is not None:
        data["indexPrice"] = index
    payload = {
        "topic": f"tickers.{SYMBOL}",
        "type": message_type,
        "data": data,
        "cs": cs,
        "ts": 1_777_593_600_000,
    }
    return f"{arrival} {json.dumps(payload, separators=(',', ':'))}"


def trade_line(arrival: str, *, trade_id: str, millis: int, price: str, quantity: str, seq: int) -> str:
    payload = {
        "topic": f"publicTrade.{SYMBOL}",
        "type": "snapshot",
        "ts": millis,
        "data": [
            {"s": SYMBOL, "i": trade_id, "T": millis, "p": price, "v": quantity, "S": "Buy", "seq": seq}
        ],
    }
    return f"{arrival} {json.dumps(payload, separators=(',', ':'))}"


def build_synthetic_pilot_inputs(*, day: date = SOURCE_DATE):
    """Two abutting captured minutes plus a later, disjoint minute (a real hole)."""
    base_millis = int((datetime(day.year, day.month, day.day, tzinfo=UTC) - EPOCH).total_seconds() * 1000)

    def stamp(offset_minutes: int, second: int, fraction: str = "0000000") -> str:
        moment = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(
            minutes=offset_minutes, seconds=second
        )
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + fraction + "Z"

    minute_zero = parse_tardis_capture_stream(
        [
            ticker_line(stamp(0, 1), message_type="snapshot", mark="100.5", index="100.0", cs=10),
            ticker_line(stamp(0, 2), message_type="delta", mark="100.6", index=None, cs=11),
            ticker_line(stamp(0, 3), message_type="delta", mark=None, index="100.1", cs=12),
            ticker_line(stamp(0, 4), message_type="delta", mark="100.7", index=None, cs=13),
        ],
        channel=TardisChannelV1.TICKERS,
        symbol=SYMBOL,
        source_date=day,
        declared_start_nanos=window_nanos(day, 0)[0],
        declared_end_nanos=window_nanos(day, 0)[1],
    )
    minute_one = parse_tardis_capture_stream(
        [
            ticker_line(stamp(1, 5), message_type="delta", mark="100.8", index=None, cs=14),
            ticker_line(stamp(1, 6), message_type="delta", mark=None, index="100.2", cs=15),
        ],
        channel=TardisChannelV1.TICKERS,
        symbol=SYMBOL,
        source_date=day,
        declared_start_nanos=window_nanos(day, 1)[0],
        declared_end_nanos=window_nanos(day, 1)[1],
    )
    far_minute = parse_tardis_capture_stream(
        [ticker_line(stamp(30, 1), message_type="snapshot", mark="101.0", index="100.5", cs=900)],
        channel=TardisChannelV1.TICKERS,
        symbol=SYMBOL,
        source_date=day,
        declared_start_nanos=window_nanos(day, 30)[0],
        declared_end_nanos=window_nanos(day, 30)[1],
    )
    ticker_stream = join_contiguous_streams([minute_zero, minute_one])
    ticker_coverage = build_tardis_capture_coverage(
        [minute_zero.interval(), minute_one.interval(), far_minute.interval()]
    )

    trade_minutes = []
    for offset in (0, 1, 2):
        trade_minutes.append(
            parse_tardis_capture_stream(
                [
                    trade_line(
                        stamp(offset, 10),
                        trade_id=f"t{offset}a",
                        millis=base_millis + offset * 60_000 + 10_000,
                        price="100.0",
                        quantity="1.5",
                        seq=100 + offset * 2,
                    ),
                    trade_line(
                        stamp(offset, 40),
                        trade_id=f"t{offset}b",
                        millis=base_millis + offset * 60_000 + 40_000,
                        price="101.0",
                        quantity="0.5",
                        seq=101 + offset * 2,
                    ),
                ],
                channel=TardisChannelV1.PUBLIC_TRADE,
                symbol=SYMBOL,
                source_date=day,
                declared_start_nanos=window_nanos(day, offset)[0],
                declared_end_nanos=window_nanos(day, offset)[1],
            )
        )
    far_trade_minute = parse_tardis_capture_stream(
        [
            trade_line(
                stamp(30, 10),
                trade_id="t30a",
                millis=base_millis + 30 * 60_000 + 10_000,
                price="105.0",
                quantity="1",
                seq=900,
            )
        ],
        channel=TardisChannelV1.PUBLIC_TRADE,
        symbol=SYMBOL,
        source_date=day,
        declared_start_nanos=window_nanos(day, 30)[0],
        declared_end_nanos=window_nanos(day, 30)[1],
    )
    trade_stream = join_contiguous_streams(trade_minutes)
    trade_coverage = build_tardis_capture_coverage(
        [*(s.interval() for s in trade_minutes), far_trade_minute.interval()]
    )

    reconstruction = reconstruct_bybit_ticker_basis(ticker_stream.records, symbol=SYMBOL)
    trades = parse_captured_public_trades(trade_stream.records, symbol=SYMBOL)
    bars = build_one_minute_trade_bars(trades, symbol=SYMBOL)
    return reconstruction, bars, trades, ticker_coverage, trade_coverage


def evaluate(**overrides):
    reconstruction, bars, trades, ticker_coverage, trade_coverage = build_synthetic_pilot_inputs()
    kwargs = {
        "symbol": SYMBOL,
        "ticker_reconstruction": reconstruction,
        "ticker_reconstruction_replay": reconstruction,
        "bars": bars,
        "bars_replay": bars,
        "ticker_coverage": ticker_coverage,
        "trade_coverage": trade_coverage,
        "trade_count": len(trades),
        "minimum_distinct_availability_instants": 3,
    }
    kwargs.update(overrides)
    return evaluate_tardis_capture_engineering_pilot(**kwargs)


class PilotVerdictTests(unittest.TestCase):
    def test_a_sound_capture_is_proven(self) -> None:
        report = evaluate()
        self.assertEqual(report.reasons, ())
        self.assertEqual(report.verdict, TardisCaptureEngineeringVerdictV1.PROVEN)
        self.assertTrue(report.is_proven)
        self.assertEqual(report.verdict.value, "TARDIS_CAPTURE_ENGINEERING_PROVEN")

    def test_a_passing_verdict_authorizes_nothing(self) -> None:
        report = evaluate()
        self.assertFalse(report.is_real_data_research_evidence)
        self.assertFalse(report.canonical_source_authorized)
        self.assertEqual(report.lifecycle, "RESEARCH_ENGINEERING_EVIDENCE_ONLY")

    def test_mark_and_index_are_proven_to_arrive_independently(self) -> None:
        report = evaluate()
        self.assertGreater(report.mark_update_count, 0)
        self.assertGreater(report.index_update_count, 0)
        self.assertLess(
            report.both_component_message_count,
            min(report.mark_update_count, report.index_update_count),
        )

    def test_many_distinct_availability_instants_exist(self) -> None:
        report = evaluate()
        self.assertEqual(report.basis_observation_count, report.distinct_availability_instant_count)
        self.assertGreaterEqual(report.distinct_availability_instant_count, 3)

    def test_a_strictly_later_entry_bar_remains_selectable(self) -> None:
        report = evaluate()
        self.assertGreater(report.strict_entry_feasible_count, 0)

    def test_the_report_carries_no_performance_claim(self) -> None:
        report = evaluate()
        forbidden = {"pnl", "return", "sharpe", "hit_rate", "threshold", "alpha", "profit"}
        fields = {name for name in report.__slots__}
        self.assertEqual(fields & forbidden, set())

    def test_collection_gaps_are_represented_and_fail_closed(self) -> None:
        report = evaluate()
        self.assertGreaterEqual(report.ticker_gap_count, 1)
        self.assertGreaterEqual(report.trade_gap_count, 1)
        self.assertEqual(report.reasons, ())

    def test_content_hash_is_deterministic(self) -> None:
        self.assertEqual(evaluate().content_hash, evaluate().content_hash)
        self.assertEqual(evaluate().report_id, evaluate().report_id)


class PilotFailureTests(unittest.TestCase):
    def test_a_non_deterministic_ticker_replay_fails(self) -> None:
        other, *_ = build_synthetic_pilot_inputs(day=date(2026, 6, 1))
        report = evaluate(ticker_reconstruction_replay=other)
        self.assertEqual(report.verdict, TardisCaptureEngineeringVerdictV1.FAILED)
        self.assertIn("ticker_reconstruction_replay_not_deterministic", report.reasons)

    def test_a_non_deterministic_bar_replay_fails(self) -> None:
        _, other_bars, *_ = build_synthetic_pilot_inputs(day=date(2026, 6, 1))
        report = evaluate(bars_replay=other_bars)
        self.assertIn("trade_bar_replay_not_deterministic", report.reasons)

    def test_evidence_reaching_the_untouched_holdout_fails_closed(self) -> None:
        reconstruction, bars, trades, ticker_coverage, trade_coverage = build_synthetic_pilot_inputs(
            day=date(2026, 8, 21)
        )
        report = evaluate_tardis_capture_engineering_pilot(
            symbol=SYMBOL,
            ticker_reconstruction=reconstruction,
            ticker_reconstruction_replay=reconstruction,
            bars=bars,
            bars_replay=bars,
            ticker_coverage=ticker_coverage,
            trade_coverage=trade_coverage,
            trade_count=len(trades),
            minimum_distinct_availability_instants=3,
        )
        self.assertEqual(report.verdict, TardisCaptureEngineeringVerdictV1.FAILED)
        self.assertIn("pilot_evidence_reaches_untouched_holdout", report.reasons)
        self.assertGreaterEqual(report.latest_evidence_at, UNTOUCHED_HOLDOUT_BOUNDARY_V1)

    def test_too_few_distinct_availability_instants_fails(self) -> None:
        report = evaluate(minimum_distinct_availability_instants=10_000)
        self.assertIn("too_few_distinct_basis_availability_instants", report.reasons)

    def test_no_bars_fails(self) -> None:
        report = evaluate(bars=(), bars_replay=())
        self.assertIn("no_trade_bar_reconstructed", report.reasons)

    def test_missing_coverage_cannot_even_be_evaluated(self) -> None:
        reconstruction, bars, trades, ticker_coverage, trade_coverage = build_synthetic_pilot_inputs()
        empty = type(ticker_coverage)(
            channel=ticker_coverage.channel,
            symbol=ticker_coverage.symbol,
            intervals=(),
            gaps=(),
            content_hash="",
        )
        with self.assertRaises(TardisCaptureEngineeringPilotError) as raised:
            evaluate_tardis_capture_engineering_pilot(
                symbol=SYMBOL,
                ticker_reconstruction=reconstruction,
                ticker_reconstruction_replay=reconstruction,
                bars=bars,
                bars_replay=bars,
                ticker_coverage=empty,
                trade_coverage=trade_coverage,
                trade_count=len(trades),
                minimum_distinct_availability_instants=3,
            )
        self.assertEqual(str(raised.exception), "tardis_pilot_requires_capture_coverage")


@unittest.skipUnless(
    os.environ.get(SAMPLE_DIR_ENV),
    f"{SAMPLE_DIR_ENV} is not set; the free Tardis sample is never retained in this repository",
)
class FreeSampleIntegrationTests(unittest.TestCase):
    """Replays the actual zero-cost first-of-month sample from a local directory."""

    DAYS = (date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1))
    OFFSETS = tuple(range(20))

    def _stream(self, sample_dir: Path, day: date, channel: TardisChannelV1):
        streams = []
        for offset in self.OFFSETS:
            path = sample_dir / f"bybit_{channel.value}_{day.isoformat()}_o{offset}.ndjson"
            if not path.exists():
                self.skipTest(f"missing free sample slice {path.name}")
            start, end = window_nanos(day, offset)
            streams.append(
                parse_tardis_capture_stream(
                    path.read_text(encoding="utf-8").splitlines(),
                    channel=channel,
                    symbol=SYMBOL,
                    source_date=day,
                    declared_start_nanos=start,
                    declared_end_nanos=end,
                )
            )
        return join_contiguous_streams(streams)

    def test_the_free_sample_proves_the_capture_path_end_to_end(self) -> None:
        sample_dir = Path(os.environ[SAMPLE_DIR_ENV])
        ticker_streams = {day: self._stream(sample_dir, day, TardisChannelV1.TICKERS) for day in self.DAYS}
        trade_streams = {
            day: self._stream(sample_dir, day, TardisChannelV1.PUBLIC_TRADE) for day in self.DAYS
        }
        ticker_coverage = build_tardis_capture_coverage([s.interval() for s in ticker_streams.values()])
        trade_coverage = build_tardis_capture_coverage([s.interval() for s in trade_streams.values()])
        # Four disjoint first-of-month captures leave three real collection gaps.
        self.assertEqual(len(ticker_coverage.gaps), 3)
        self.assertEqual(len(trade_coverage.gaps), 3)

        for day in self.DAYS:
            with self.subTest(day=day):
                reconstruction = reconstruct_bybit_ticker_basis(
                    ticker_streams[day].records, symbol=SYMBOL
                )
                trades = parse_captured_public_trades(trade_streams[day].records, symbol=SYMBOL)
                bars = build_one_minute_trade_bars(trades, symbol=SYMBOL)
                report = evaluate_tardis_capture_engineering_pilot(
                    symbol=SYMBOL,
                    ticker_reconstruction=reconstruction,
                    ticker_reconstruction_replay=reconstruct_bybit_ticker_basis(
                        ticker_streams[day].records, symbol=SYMBOL
                    ),
                    bars=bars,
                    # Reversing the input proves the bar builder never relies on
                    # the order trades happened to be read in.
                    bars_replay=build_one_minute_trade_bars(tuple(reversed(trades)), symbol=SYMBOL),
                    ticker_coverage=ticker_coverage,
                    trade_coverage=trade_coverage,
                    trade_count=len(trades),
                    minimum_distinct_availability_instants=1_000,
                )
                self.assertEqual(report.reasons, ())
                self.assertTrue(report.is_proven)
                self.assertLess(report.latest_evidence_at, UNTOUCHED_HOLDOUT_BOUNDARY_V1)
                self.assertEqual(
                    report.basis_observation_count, report.distinct_availability_instant_count
                )
                self.assertEqual(report.bar_count, len(self.OFFSETS))
                self.assertGreater(report.strict_entry_feasible_count, 0)


if __name__ == "__main__":
    unittest.main()
