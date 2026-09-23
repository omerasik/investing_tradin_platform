"""Phase 3D.9S.2A -- deterministic 1m OHLCV reconstruction from captured public trades.

Fixtures are written from the documented Bybit V5 ``publicTrade`` schema, not
copied from vendor data.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from trade_platform.bybit_trade_bar_reconstruction_v1 import (
    BYBIT_BTCUSDT_LINEAR_PERPETUAL_V1,
    BybitTradeBarReconstructionError,
    LinearContractQuantitySemanticsV1,
    bar_close_nanos,
    bar_open_nanos,
    build_one_minute_trade_bars,
    first_strictly_later_bar,
    parse_captured_public_trades,
)
from trade_platform.tardis_capture_evidence_v1 import (
    TardisChannelV1,
    parse_tardis_capture_line,
)

SYMBOL = "BTCUSDT"
SOURCE_DATE = date(2026, 5, 1)
MINUTE_ZERO_MILLIS = 1_777_593_600_000  # 2026-05-01T00:00:00Z

#: A deliberately inverse-style contract: quantity is quoted in USD, so
#: price * quantity is not turnover and must not be computed.
INVERSE_CONTRACT = LinearContractQuantitySemanticsV1(
    base_asset="BTC", quote_asset="USD", quantity_is_base_asset=False
)


def trade_record(
    arrival: str,
    trades: list[dict[str, object]],
    *,
    message_type: str = "snapshot",
    exchange_millis: int = MINUTE_ZERO_MILLIS,
):
    payload = {
        "topic": f"publicTrade.{SYMBOL}",
        "type": message_type,
        "ts": exchange_millis,
        "data": [{"s": SYMBOL, "BT": False, **entry} for entry in trades],
    }
    line = f"{arrival} {json.dumps(payload, separators=(',', ':'))}"
    return parse_tardis_capture_line(
        line, channel=TardisChannelV1.PUBLIC_TRADE, symbol=SYMBOL, source_date=SOURCE_DATE
    )


def trade(
    *,
    trade_id: str,
    millis: int,
    price: str,
    quantity: str,
    sequence: int,
    side: str = "Buy",
) -> dict[str, object]:
    return {"i": trade_id, "T": millis, "p": price, "v": quantity, "S": side, "seq": sequence}


def arrival(second: int, fraction: str = "0000000") -> str:
    return f"2026-05-01T00:00:{second:02d}.{fraction}Z"


class ParseCapturedTradesTests(unittest.TestCase):
    def test_every_entry_in_a_message_becomes_its_own_trade(self) -> None:
        record = trade_record(
            arrival(1),
            [
                trade(trade_id="a", millis=MINUTE_ZERO_MILLIS + 10, price="100", quantity="1", sequence=1),
                trade(trade_id="b", millis=MINUTE_ZERO_MILLIS + 10, price="101", quantity="2", sequence=1),
            ],
        )
        trades = parse_captured_public_trades([record], symbol=SYMBOL)
        self.assertEqual(len(trades), 2)
        # Both inherit the arrival instant of the single message that carried them.
        self.assertEqual({t.local_timestamp_nanos for t in trades}, {record.local_timestamp_nanos})

    def test_missing_sequence_fails_closed(self) -> None:
        payload = {
            "topic": f"publicTrade.{SYMBOL}",
            "type": "snapshot",
            "ts": MINUTE_ZERO_MILLIS,
            "data": [{"s": SYMBOL, "i": "a", "T": MINUTE_ZERO_MILLIS, "p": "1", "v": "1", "S": "Buy"}],
        }
        record = parse_tardis_capture_line(
            f"{arrival(1)} {json.dumps(payload, separators=(',', ':'))}",
            channel=TardisChannelV1.PUBLIC_TRADE,
            symbol=SYMBOL,
            source_date=SOURCE_DATE,
        )
        with self.assertRaises(BybitTradeBarReconstructionError) as raised:
            parse_captured_public_trades([record], symbol=SYMBOL)
        self.assertEqual(str(raised.exception), "bybit_trade_sequence_missing")

    def test_non_positive_quantity_fails_closed(self) -> None:
        record = trade_record(
            arrival(1),
            [trade(trade_id="a", millis=MINUTE_ZERO_MILLIS, price="100", quantity="0", sequence=1)],
        )
        with self.assertRaises(BybitTradeBarReconstructionError) as raised:
            parse_captured_public_trades([record], symbol=SYMBOL)
        self.assertEqual(str(raised.exception), "bybit_trade_quantity_not_positive")

    def test_wrong_channel_fails_closed(self) -> None:
        payload = {
            "topic": f"tickers.{SYMBOL}",
            "type": "delta",
            "data": {"symbol": SYMBOL, "markPrice": "1"},
            "ts": MINUTE_ZERO_MILLIS,
        }
        record = parse_tardis_capture_line(
            f"{arrival(1)} {json.dumps(payload, separators=(',', ':'))}",
            channel=TardisChannelV1.TICKERS,
            symbol=SYMBOL,
            source_date=SOURCE_DATE,
        )
        with self.assertRaises(BybitTradeBarReconstructionError) as raised:
            parse_captured_public_trades([record], symbol=SYMBOL)
        self.assertEqual(str(raised.exception), "bybit_trade_wrong_channel")


class OneMinuteBarTests(unittest.TestCase):
    def _three_trade_minute(self):
        return parse_captured_public_trades(
            [
                trade_record(
                    arrival(5),
                    [
                        trade(
                            trade_id="t1",
                            millis=MINUTE_ZERO_MILLIS + 5_000,
                            price="100.0",
                            quantity="1.5",
                            sequence=10,
                        )
                    ],
                ),
                trade_record(
                    arrival(20),
                    [
                        trade(
                            trade_id="t2",
                            millis=MINUTE_ZERO_MILLIS + 20_000,
                            price="105.0",
                            quantity="2.0",
                            sequence=20,
                        ),
                        trade(
                            trade_id="t3",
                            millis=MINUTE_ZERO_MILLIS + 30_000,
                            price="95.0",
                            quantity="0.5",
                            sequence=30,
                        ),
                    ],
                ),
            ],
            symbol=SYMBOL,
        )

    def test_one_minute_ohlcv_is_built_from_the_observed_trades_only(self) -> None:
        bars = build_one_minute_trade_bars(self._three_trade_minute(), symbol=SYMBOL)
        self.assertEqual(len(bars), 1)
        bar = bars[0]
        self.assertEqual(bar.bar_open_at, datetime(2026, 5, 1, tzinfo=UTC))
        self.assertEqual(bar.bar_close_at, datetime(2026, 5, 1, 0, 1, tzinfo=UTC))
        self.assertEqual(bar.open_price, Decimal("100.0"))
        self.assertEqual(bar.high_price, Decimal("105.0"))
        self.assertEqual(bar.low_price, Decimal("95.0"))
        self.assertEqual(bar.close_price, Decimal("95.0"))
        self.assertEqual(bar.trade_count, 3)
        self.assertEqual(bar.first_trade_id, "t1")
        self.assertEqual(bar.last_trade_id, "t3")

    def test_base_volume_and_quote_turnover_carry_explicit_units(self) -> None:
        bar = build_one_minute_trade_bars(self._three_trade_minute(), symbol=SYMBOL)[0]
        self.assertEqual(bar.base_volume, Decimal("4.00000000"))
        self.assertEqual(bar.base_volume_unit, "BTC")
        # 100.0*1.5 + 105.0*2.0 + 95.0*0.5 = 407.5
        self.assertEqual(bar.quote_turnover, Decimal("407.50000000"))
        self.assertEqual(bar.quote_turnover_unit, "USDT")
        self.assertTrue(BYBIT_BTCUSDT_LINEAR_PERPETUAL_V1.quantity_is_base_asset)

    def test_turnover_is_refused_when_quantity_is_not_base_denominated(self) -> None:
        bar = build_one_minute_trade_bars(
            self._three_trade_minute(), symbol=SYMBOL, contract=INVERSE_CONTRACT
        )[0]
        self.assertIsNone(bar.quote_turnover)
        self.assertIsNone(bar.quote_turnover_unit)
        # Base volume is still a genuine sum of contract quantities.
        self.assertEqual(bar.base_volume, Decimal("4.00000000"))

    def test_a_minute_without_trades_produces_no_bar(self) -> None:
        trades = parse_captured_public_trades(
            [
                trade_record(
                    arrival(5),
                    [
                        trade(
                            trade_id="t1",
                            millis=MINUTE_ZERO_MILLIS + 5_000,
                            price="100.0",
                            quantity="1",
                            sequence=1,
                        )
                    ],
                ),
                trade_record(
                    arrival(5),
                    [
                        trade(
                            trade_id="t2",
                            millis=MINUTE_ZERO_MILLIS + 125_000,
                            price="110.0",
                            quantity="1",
                            sequence=2,
                        )
                    ],
                ),
            ],
            symbol=SYMBOL,
        )
        bars = build_one_minute_trade_bars(trades, symbol=SYMBOL)
        # Minutes 0 and 2 traded; minute 1 did not and gets no forward-filled bar.
        self.assertEqual([b.bar_open_at.minute for b in bars], [0, 2])

    def test_a_trade_exactly_on_the_boundary_opens_the_new_minute(self) -> None:
        trades = parse_captured_public_trades(
            [
                trade_record(
                    arrival(59),
                    [
                        trade(
                            trade_id="t1",
                            millis=MINUTE_ZERO_MILLIS + 60_000,
                            price="100.0",
                            quantity="1",
                            sequence=1,
                        )
                    ],
                )
            ],
            symbol=SYMBOL,
        )
        bars = build_one_minute_trade_bars(trades, symbol=SYMBOL)
        self.assertEqual(bars[0].bar_open_at, datetime(2026, 5, 1, 0, 1, tzinfo=UTC))


class DeterministicOrderingTests(unittest.TestCase):
    def _mixed_minute(self):
        return parse_captured_public_trades(
            [
                trade_record(
                    arrival(10),
                    [
                        trade(
                            trade_id="zzz",
                            millis=MINUTE_ZERO_MILLIS + 1_000,
                            price="100.0",
                            quantity="1",
                            sequence=5,
                        ),
                        trade(
                            trade_id="aaa",
                            millis=MINUTE_ZERO_MILLIS + 1_000,
                            price="101.0",
                            quantity="1",
                            sequence=7,
                        ),
                        trade(
                            trade_id="mmm",
                            millis=MINUTE_ZERO_MILLIS + 500,
                            price="99.0",
                            quantity="1",
                            sequence=3,
                        ),
                    ],
                )
            ],
            symbol=SYMBOL,
        )

    def test_ordering_is_by_exchange_time_then_sequence_then_trade_id(self) -> None:
        bar = build_one_minute_trade_bars(self._mixed_minute(), symbol=SYMBOL)[0]
        # Insertion order was zzz, aaa, mmm. Declared order is mmm, zzz, aaa.
        self.assertEqual(bar.first_trade_id, "mmm")
        self.assertEqual(bar.last_trade_id, "aaa")
        self.assertEqual(bar.open_price, Decimal("99.0"))
        self.assertEqual(bar.close_price, Decimal("101.0"))

    def test_shuffled_input_produces_a_bit_for_bit_identical_bar(self) -> None:
        trades = self._mixed_minute()
        forward = build_one_minute_trade_bars(trades, symbol=SYMBOL)
        backward = build_one_minute_trade_bars(tuple(reversed(trades)), symbol=SYMBOL)
        self.assertEqual(
            [b.content_hash for b in forward], [b.content_hash for b in backward]
        )
        self.assertEqual(forward[0].trade_manifest_hash, backward[0].trade_manifest_hash)
        self.assertEqual(forward[0].bar_id, backward[0].bar_id)

    def test_a_non_economic_tiebreak_is_reported_not_hidden(self) -> None:
        trades = parse_captured_public_trades(
            [
                trade_record(
                    arrival(10),
                    [
                        trade(
                            trade_id="a",
                            millis=MINUTE_ZERO_MILLIS + 1_000,
                            price="100.0",
                            quantity="1",
                            sequence=5,
                        ),
                        trade(
                            trade_id="b",
                            millis=MINUTE_ZERO_MILLIS + 1_000,
                            price="102.0",
                            quantity="1",
                            sequence=5,
                        ),
                    ],
                )
            ],
            symbol=SYMBOL,
        )
        bar = build_one_minute_trade_bars(trades, symbol=SYMBOL)[0]
        self.assertTrue(bar.open_is_sequence_ambiguous)
        self.assertTrue(bar.close_is_sequence_ambiguous)

    def test_an_unambiguous_minute_is_not_flagged(self) -> None:
        bar = build_one_minute_trade_bars(self._mixed_minute(), symbol=SYMBOL)[0]
        self.assertFalse(bar.open_is_sequence_ambiguous)
        self.assertFalse(bar.close_is_sequence_ambiguous)


def _minute_trade(*, arrival_at: str, trade_id: str, offset_seconds: int, price: str, sequence: int):
    return trade_record(
        arrival_at,
        [
            trade(
                trade_id=trade_id,
                millis=MINUTE_ZERO_MILLIS + offset_seconds * 1_000,
                price=price,
                quantity="1",
                sequence=sequence,
            )
        ],
    )


class AvailabilityTests(unittest.TestCase):
    def _three_minutes(self):
        return parse_captured_public_trades(
            [
                _minute_trade(
                    arrival_at=arrival(30, "5000000"),
                    trade_id="t1",
                    offset_seconds=30,
                    price="100.0",
                    sequence=1,
                ),
                _minute_trade(
                    arrival_at="2026-05-01T00:01:30.0000000Z",
                    trade_id="t2",
                    offset_seconds=90,
                    price="110.0",
                    sequence=2,
                ),
                _minute_trade(
                    arrival_at="2026-05-01T00:02:30.0000000Z",
                    trade_id="t3",
                    offset_seconds=150,
                    price="120.0",
                    sequence=3,
                ),
            ],
            symbol=SYMBOL,
        )

    def test_open_availability_is_the_first_selected_trades_arrival(self) -> None:
        first = build_one_minute_trade_bars(self._three_minutes(), symbol=SYMBOL)[0]
        self.assertEqual(
            first.open_available_at, datetime(2026, 5, 1, 0, 0, 30, 500_000, tzinfo=UTC)
        )
        # The OPEN is knowable inside its own minute; it proves nothing about the
        # completed aggregate.
        self.assertGreater(first.open_available_at, first.bar_open_at)
        self.assertLess(first.open_available_at, first.bar_close_at)

    def test_completed_bar_is_not_knowable_before_the_minute_closes(self) -> None:
        """Last trade arrives at 00:00:30.5, but a further trade could still print."""
        first = build_one_minute_trade_bars(self._three_minutes(), symbol=SYMBOL)[0]
        self.assertEqual(first.bar_complete_available_at, first.bar_close_at)
        self.assertGreater(
            first.bar_complete_available_at_nanos, first.open_available_at_nanos
        )

    def test_a_final_trade_delayed_past_the_close_pushes_completion_later(self) -> None:
        trades = parse_captured_public_trades(
            [
                _minute_trade(
                    arrival_at=arrival(30, "5000000"),
                    trade_id="t1",
                    offset_seconds=30,
                    price="100.0",
                    sequence=1,
                ),
                # Printed at 00:00:59 but only recorded at 00:01:20.1234567.
                _minute_trade(
                    arrival_at="2026-05-01T00:01:20.1234567Z",
                    trade_id="t2",
                    offset_seconds=59,
                    price="101.0",
                    sequence=2,
                ),
            ],
            symbol=SYMBOL,
        )
        bar = build_one_minute_trade_bars(trades, symbol=SYMBOL)[0]
        latest_arrival = max(t.local_timestamp_nanos for t in trades)
        self.assertEqual(bar.bar_complete_available_at_nanos, latest_arrival)
        self.assertGreater(bar.bar_complete_available_at_nanos, bar_close_nanos(bar))
        # Open availability stays with the OPEN trade and is unaffected.
        self.assertEqual(bar.open_available_at, datetime(2026, 5, 1, 0, 0, 30, 500_000, tzinfo=UTC))

    def test_completed_availability_is_never_truncated_to_microseconds(self) -> None:
        trades = parse_captured_public_trades(
            [
                _minute_trade(
                    arrival_at="2026-05-01T00:01:20.1234567Z",
                    trade_id="t1",
                    offset_seconds=30,
                    price="100.0",
                    sequence=1,
                )
            ],
            symbol=SYMBOL,
        )
        bar = build_one_minute_trade_bars(trades, symbol=SYMBOL)[0]
        # The 100 ns Tardis tick survives in the integer field; the datetime
        # projection truncates it, which is exactly why the integer is canonical.
        self.assertEqual(bar.bar_complete_available_at_nanos % 1_000, 700)
        self.assertLess(
            bar.bar_complete_available_at.timestamp(),
            bar.bar_complete_available_at_nanos / 1_000_000_000 + 1e-9,
        )
        self.assertGreaterEqual(
            bar.bar_complete_available_at_nanos,
            int(bar.bar_complete_available_at.timestamp() * 1_000_000_000),
        )

    def test_both_availabilities_are_deterministic_under_reversed_input(self) -> None:
        trades = self._three_minutes()
        forward = build_one_minute_trade_bars(trades, symbol=SYMBOL)
        reversed_bars = build_one_minute_trade_bars(tuple(reversed(trades)), symbol=SYMBOL)
        self.assertEqual(
            [(b.open_available_at_nanos, b.bar_complete_available_at_nanos) for b in forward],
            [(b.open_available_at_nanos, b.bar_complete_available_at_nanos) for b in reversed_bars],
        )
        self.assertEqual(
            [b.content_hash for b in forward], [b.content_hash for b in reversed_bars]
        )

    def test_entry_bar_must_open_strictly_later_than_completed_bar_availability(self) -> None:
        bars = build_one_minute_trade_bars(self._three_minutes(), symbol=SYMBOL)
        availability = bars[0].bar_complete_available_at_nanos
        entry = first_strictly_later_bar(bars, research_available_at_nanos=availability)
        self.assertIsNotNone(entry)
        assert entry is not None
        # The 00:01 bar opens exactly when the 00:00 bar completes, so it is
        # refused; the first admissible entry is 00:02.
        self.assertEqual(entry.bar_open_at, datetime(2026, 5, 1, 0, 2, tzinfo=UTC))
        self.assertGreater(bar_open_nanos(entry), availability)

    def test_a_bar_opening_exactly_at_availability_is_refused(self) -> None:
        bars = build_one_minute_trade_bars(self._three_minutes(), symbol=SYMBOL)
        exactly_at_open = bar_open_nanos(bars[1])
        entry = first_strictly_later_bar(bars, research_available_at_nanos=exactly_at_open)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.bar_open_at, datetime(2026, 5, 1, 0, 2, tzinfo=UTC))

    def test_no_entry_bar_exists_after_the_last_observed_minute(self) -> None:
        bars = build_one_minute_trade_bars(self._three_minutes(), symbol=SYMBOL)
        entry = first_strictly_later_bar(
            bars, research_available_at_nanos=bars[-1].bar_complete_available_at_nanos
        )
        self.assertIsNone(entry)


if __name__ == "__main__":
    unittest.main()
