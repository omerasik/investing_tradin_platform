"""Phase 3D.9S.2A -- causal Bybit ticker state reconstruction and PIT basis availability.

Fixtures are written from the documented Bybit V5 ``tickers`` schema, not copied
from vendor data.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from decimal import Decimal

from trade_platform.bybit_ticker_state_reconstruction_v1 import (
    BASIS_QUANTUM_V1,
    BybitTickerStateReconstructionError,
    ReferencePriceComponentV1,
    compute_basis_v1,
    reconstruct_bybit_ticker_basis,
)
from trade_platform.tardis_capture_evidence_v1 import (
    TardisChannelV1,
    parse_tardis_capture_line,
)

SYMBOL = "BTCUSDT"
SOURCE_DATE = date(2026, 5, 1)


def record(
    arrival: str,
    *,
    message_type: str = "delta",
    mark: str | None = None,
    index: str | None = None,
    exchange_millis: int = 1_777_593_600_000,
    cross_sequence: int = 1_000,
    channel: TardisChannelV1 = TardisChannelV1.TICKERS,
):
    data: dict[str, object] = {"symbol": SYMBOL}
    if mark is not None:
        data["markPrice"] = mark
    if index is not None:
        data["indexPrice"] = index
    payload = {
        "topic": f"{channel.value}.{SYMBOL}",
        "type": message_type,
        "data": data,
        "cs": cross_sequence,
        "ts": exchange_millis,
    }
    line = f"{arrival} {json.dumps(payload, separators=(',', ':'))}"
    return parse_tardis_capture_line(
        line, channel=channel, symbol=SYMBOL, source_date=SOURCE_DATE
    )


def arrival(second: int, fraction: str = "0000000") -> str:
    return f"2026-05-01T00:00:{second:02d}.{fraction}Z"


class SnapshotAndDeltaSemanticsTests(unittest.TestCase):
    def test_snapshot_initializes_both_components(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [record(arrival(0), message_type="snapshot", mark="100.5", index="100.0")],
            symbol=SYMBOL,
        )
        self.assertEqual(result.snapshot_count, 1)
        self.assertTrue(result.final_state.is_complete)
        self.assertEqual(len(result.observations), 1)
        observation = result.observations[0]
        self.assertEqual(observation.mark_value, Decimal("100.5"))
        self.assertEqual(observation.index_value, Decimal("100.0"))
        self.assertEqual(observation.basis_value, Decimal("0.005").quantize(BASIS_QUANTUM_V1))

    def test_delta_with_only_mark_preserves_the_prior_index(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [
                record(arrival(0), message_type="snapshot", mark="100.5", index="100.0"),
                record(arrival(1), mark="101.0"),
            ],
            symbol=SYMBOL,
        )
        latest = result.observations[-1]
        self.assertEqual(latest.mark_value, Decimal("101.0"))
        self.assertEqual(latest.index_value, Decimal("100.0"))
        # The index component still points at the snapshot that carried it.
        self.assertEqual(
            latest.index_record_content_hash, result.observations[0].index_record_content_hash
        )
        self.assertEqual(result.mark_update_count, 2)
        self.assertEqual(result.index_update_count, 1)

    def test_delta_with_only_index_preserves_the_prior_mark(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [
                record(arrival(0), message_type="snapshot", mark="100.5", index="100.0"),
                record(arrival(1), index="101.0"),
            ],
            symbol=SYMBOL,
        )
        latest = result.observations[-1]
        self.assertEqual(latest.mark_value, Decimal("100.5"))
        self.assertEqual(latest.index_value, Decimal("101.0"))
        self.assertEqual(
            latest.mark_record_content_hash, result.observations[0].mark_record_content_hash
        )

    def test_absent_field_means_unchanged_not_null(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [
                record(arrival(0), message_type="snapshot", mark="100.5", index="100.0"),
                record(arrival(1), mark="101.0"),
                record(arrival(2), mark="102.0"),
            ],
            symbol=SYMBOL,
        )
        self.assertEqual(len(result.observations), 3)
        self.assertTrue(all(o.index_value == Decimal("100.0") for o in result.observations))

    def test_no_observation_exists_before_both_components_are_known(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [
                record(arrival(0), index="100.0"),
                record(arrival(1), index="100.1"),
                record(arrival(2), mark="100.5"),
            ],
            symbol=SYMBOL,
        )
        self.assertEqual(result.records_before_state_complete, 2)
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0].index_value, Decimal("100.1"))

    def test_a_message_carrying_neither_component_emits_nothing(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [
                record(arrival(0), message_type="snapshot", mark="100.5", index="100.0"),
                record(arrival(1)),
            ],
            symbol=SYMBOL,
        )
        self.assertEqual(len(result.observations), 1)

    def test_reconnect_snapshot_re_establishes_authoritative_state(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [
                record(arrival(0), message_type="snapshot", mark="100.5", index="100.0"),
                # A reconnect snapshot that carries only the index resets mark to
                # unobserved: state may not survive a re-declaration.
                record(arrival(5), message_type="snapshot", index="200.0"),
                record(arrival(6), mark="201.0"),
            ],
            symbol=SYMBOL,
        )
        self.assertEqual(result.snapshot_count, 2)
        self.assertEqual(result.state_reset_count, 1)
        self.assertEqual(len(result.observations), 2)
        latest = result.observations[-1]
        self.assertEqual(latest.mark_value, Decimal("201.0"))
        self.assertEqual(latest.index_value, Decimal("200.0"))


class AvailabilityCausalityTests(unittest.TestCase):
    def test_availability_is_the_max_of_exact_component_arrivals(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [
                record(arrival(0), message_type="snapshot", mark="100.5", index="100.0"),
                record(arrival(7, "1234567"), mark="101.0"),
            ],
            symbol=SYMBOL,
        )
        latest = result.observations[-1]
        self.assertEqual(
            latest.research_available_at_nanos,
            max(latest.mark_local_timestamp_nanos, latest.index_local_timestamp_nanos),
        )
        self.assertEqual(latest.research_available_at_nanos, latest.mark_local_timestamp_nanos)
        self.assertGreater(latest.research_available_at_nanos, latest.index_local_timestamp_nanos)

    def test_a_later_index_moves_availability_forward_not_the_mark_arrival(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [
                record(arrival(0), message_type="snapshot", mark="100.5", index="100.0"),
                record(arrival(9), index="100.2"),
            ],
            symbol=SYMBOL,
        )
        latest = result.observations[-1]
        self.assertEqual(latest.research_available_at_nanos, latest.index_local_timestamp_nanos)
        self.assertLess(latest.mark_local_timestamp_nanos, latest.research_available_at_nanos)

    def test_no_future_component_can_leak_into_an_earlier_observation(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [
                record(arrival(0), message_type="snapshot", mark="100.5", index="100.0"),
                record(arrival(1), mark="101.0"),
                record(arrival(2), mark="102.0"),
            ],
            symbol=SYMBOL,
        )
        for observation in result.observations:
            self.assertLessEqual(
                observation.research_available_at_nanos,
                max(observation.index_local_timestamp_nanos, observation.mark_local_timestamp_nanos),
            )
        # The first observation never sees the later mark values at all.
        self.assertEqual(result.observations[0].mark_value, Decimal("100.5"))

    def test_availability_instants_are_distinct_per_update(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [
                record(arrival(0), message_type="snapshot", mark="100.5", index="100.0"),
                record(arrival(1), mark="101.0"),
                record(arrival(2), index="100.1"),
                record(arrival(3), mark="101.5"),
            ],
            symbol=SYMBOL,
        )
        self.assertEqual(len(result.distinct_availability_instants), 4)

    def test_arrival_order_regression_fails_closed(self) -> None:
        with self.assertRaises(BybitTickerStateReconstructionError) as raised:
            reconstruct_bybit_ticker_basis(
                [
                    record(arrival(5), message_type="snapshot", mark="100.5", index="100.0"),
                    record(arrival(1), mark="101.0"),
                ],
                symbol=SYMBOL,
            )
        self.assertEqual(str(raised.exception), "bybit_ticker_arrival_order_regression")


class FailClosedTests(unittest.TestCase):
    def test_empty_component_value_fails_closed(self) -> None:
        with self.assertRaises(BybitTickerStateReconstructionError) as raised:
            reconstruct_bybit_ticker_basis(
                [record(arrival(0), message_type="snapshot", mark="", index="100.0")], symbol=SYMBOL
            )
        self.assertEqual(str(raised.exception), "bybit_ticker_mark_empty")

    def test_non_numeric_component_value_fails_closed(self) -> None:
        with self.assertRaises(BybitTickerStateReconstructionError) as raised:
            reconstruct_bybit_ticker_basis(
                [record(arrival(0), message_type="snapshot", mark="abc", index="100.0")],
                symbol=SYMBOL,
            )
        self.assertEqual(str(raised.exception), "bybit_ticker_mark_malformed")

    def test_non_finite_component_value_fails_closed(self) -> None:
        with self.assertRaises(BybitTickerStateReconstructionError) as raised:
            reconstruct_bybit_ticker_basis(
                [record(arrival(0), message_type="snapshot", mark="NaN", index="100.0")],
                symbol=SYMBOL,
            )
        self.assertEqual(str(raised.exception), "bybit_ticker_mark_not_finite")

    def test_zero_index_fails_closed(self) -> None:
        with self.assertRaises(BybitTickerStateReconstructionError) as raised:
            reconstruct_bybit_ticker_basis(
                [record(arrival(0), message_type="snapshot", mark="100.5", index="0")],
                symbol=SYMBOL,
            )
        self.assertEqual(str(raised.exception), "bybit_ticker_index_not_positive")

    def test_compute_basis_refuses_a_non_positive_index(self) -> None:
        with self.assertRaises(BybitTickerStateReconstructionError):
            compute_basis_v1(mark_value=Decimal("1"), index_value=Decimal("0"))

    def test_the_wrong_channel_fails_closed(self) -> None:
        with self.assertRaises(BybitTickerStateReconstructionError) as raised:
            reconstruct_bybit_ticker_basis(
                [
                    record(
                        arrival(0),
                        message_type="snapshot",
                        mark="1",
                        index="1",
                        channel=TardisChannelV1.PUBLIC_TRADE,
                    )
                ],
                symbol=SYMBOL,
            )
        self.assertEqual(str(raised.exception), "bybit_ticker_wrong_channel")


class DeterminismTests(unittest.TestCase):
    def test_repeat_processing_is_bit_for_bit_deterministic(self) -> None:
        records = [
            record(arrival(0), message_type="snapshot", mark="100.5", index="100.0"),
            record(arrival(1), mark="101.0"),
            record(arrival(2), index="100.1"),
        ]
        first = reconstruct_bybit_ticker_basis(records, symbol=SYMBOL)
        second = reconstruct_bybit_ticker_basis(list(records), symbol=SYMBOL)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(
            [o.content_hash for o in first.observations],
            [o.content_hash for o in second.observations],
        )
        self.assertEqual(
            [o.observation_id for o in first.observations],
            [o.observation_id for o in second.observations],
        )

    def test_basis_is_quantized_before_it_is_hashed(self) -> None:
        result = reconstruct_bybit_ticker_basis(
            [record(arrival(0), message_type="snapshot", mark="100.0000001", index="100.0")],
            symbol=SYMBOL,
        )
        observation = result.observations[0]
        self.assertEqual(observation.basis_quantum, str(BASIS_QUANTUM_V1))
        self.assertEqual(
            observation.basis_value,
            compute_basis_v1(mark_value=Decimal("100.0000001"), index_value=Decimal("100.0")),
        )
        self.assertEqual(-observation.basis_value.as_tuple().exponent, 18)

    def test_component_enum_covers_exactly_mark_and_index(self) -> None:
        self.assertEqual(
            {c.value for c in ReferencePriceComponentV1},
            {"MARK", "INDEX"},
        )


if __name__ == "__main__":
    unittest.main()
