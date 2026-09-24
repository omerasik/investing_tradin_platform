"""Phase R1A -- capacity measurement reports facts and never overclaims.

Covers exact histogram quantiles, per-symbol accounting fed by the recorder
observer, the RTT-bounded clock-offset arithmetic and its fail-soft sampler,
and the report: projections labelled as projections, compression ratios from
real compaction results, and the timestamp delta never presented as latency.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from trade_platform.first_party_capture_archive_v1 import (
    CaptureClockReadingV1,
    CompactionResultV1,
    build_capture_record_v1,
)
from trade_platform.first_party_capture_authority_v1 import (
    first_party_bybit_measurement_contract_v1,
    first_party_bybit_measurement_contracts_v1,
)
from trade_platform.first_party_capture_measurement_v1 import (
    DELTA_CAVEAT_V1,
    DELTA_LABEL_V1,
    PROJECTION_LABEL_V1,
    CapacityMeasurementV1,
    ClockOffsetSampleV1,
    SymbolCaptureStatsV1,
    TimestampDeltaHistogramV1,
    build_capacity_report_v1,
    parse_bybit_server_time_nanos_v1,
    render_capacity_report_text_v1,
    sample_bybit_server_clock_offset_v1,
    sample_process_resources_v1,
    write_capacity_report_v1,
)

ETH = first_party_bybit_measurement_contract_v1("ETHUSDT")
TS_MILLIS = 1_790_132_704_184
TICKER = (
    f'{{"topic":"tickers.ETHUSDT","type":"delta","ts":{TS_MILLIS},'
    '"data":{"symbol":"ETHUSDT","bid1Price":"2683.35"}}'
)


def _record(arrival_utc_nanos: int, sequence: int = 0):
    return build_capture_record_v1(
        contract=ETH,
        session_id=uuid4(),
        sequence=sequence,
        clock=CaptureClockReadingV1(
            arrival_utc_nanos=arrival_utc_nanos, arrival_monotonic_nanos=sequence
        ),
        payload_text=TICKER,
    )


class HistogramTests(unittest.TestCase):
    def test_nearest_rank_quantiles_are_exact(self) -> None:
        histogram = TimestampDeltaHistogramV1()
        for value in range(1, 101):
            histogram.add(value)
        self.assertEqual(50, histogram.quantile(0.50))
        self.assertEqual(90, histogram.quantile(0.90))
        self.assertEqual(99, histogram.quantile(0.99))
        self.assertEqual(1, histogram.quantile(0.0))
        self.assertEqual(100, histogram.quantile(1.0))
        summary = histogram.summary()
        self.assertEqual((100, 1, 100), (summary["count"], summary["min"], summary["max"]))

    def test_negative_deltas_are_kept_as_measured(self) -> None:
        histogram = TimestampDeltaHistogramV1()
        for value in (-9_310, -9_309, -9_309, -9_200):
            histogram.add(value)
        self.assertEqual(-9_309, histogram.quantile(0.5))
        self.assertEqual(-9_310, histogram.summary()["min"])

    def test_empty_and_invalid(self) -> None:
        histogram = TimestampDeltaHistogramV1()
        self.assertIsNone(histogram.quantile(0.5))
        with self.assertRaises(ValueError):
            histogram.quantile(1.5)


class SymbolStatsTests(unittest.TestCase):
    def test_observer_accounts_records_bytes_and_deltas(self) -> None:
        stats = SymbolCaptureStatsV1(ETH)
        arrival = TS_MILLIS * 1_000_000 + 123_456_789  # 123.456789 ms after ts
        stats.observe(_record(arrival), 700)
        stats.observe(_record(arrival - 9_500 * 1_000_000, 1), 300)
        self.assertEqual((2, 1_000), (stats.records, stats.bytes))
        self.assertEqual({"tickers": 2}, stats.records_by_channel)
        summary = stats.deltas["tickers"].summary()
        # Floor division: 123.46 ms -> 123; the second is 9.5 s *before* ts.
        self.assertEqual((-9_377, 123), (summary["min"], summary["max"]))


class ClockOffsetTests(unittest.TestCase):
    def test_offset_estimate_and_bound(self) -> None:
        sample = ClockOffsetSampleV1(
            host_send_utc_nanos=1_000_000_000,
            host_receive_utc_nanos=1_300_000_000,
            server_utc_nanos=10_600_000_000,
            host_clock_resolution_nanos=15_625_000,
        )
        self.assertEqual(150_000_000, sample.half_round_trip_nanos)
        self.assertEqual(9_450_000_000, sample.offset_estimate_nanos)
        self.assertEqual(165_625_000, sample.offset_bound_nanos)
        payload = sample.to_payload()
        self.assertEqual(9_450_000_000, payload["offset_estimate_nanos"])

    def test_server_time_parsing_fails_closed(self) -> None:
        good = '{"retCode":0,"result":{"timeSecond":"1","timeNano":"1790211519392534135"}}'
        self.assertEqual(1_790_211_519_392_534_135, parse_bybit_server_time_nanos_v1(good))
        for bad in (
            '{"retCode":10001,"result":{"timeNano":"1"}}',
            '{"retCode":0,"result":{"timeNano":"12.5"}}',
            '{"retCode":0,"result":null}',
            "[]",
        ):
            with self.assertRaises(ValueError):
                parse_bybit_server_time_nanos_v1(bad)

    def test_sampler_uses_the_two_host_readings(self) -> None:
        readings = iter([1_000, 2_000])
        sample = sample_bybit_server_clock_offset_v1(
            fetch=lambda _: '{"retCode":0,"result":{"timeNano":"11500"}}',
            clock_ns=lambda: next(readings),
        )
        assert sample is not None
        self.assertEqual((1_000, 2_000, 11_500), (
            sample.host_send_utc_nanos, sample.host_receive_utc_nanos, sample.server_utc_nanos
        ))
        self.assertEqual(10_000, sample.offset_estimate_nanos)

    def test_sampler_returns_none_when_the_endpoint_fails(self) -> None:
        def broken(_: float) -> str:
            raise OSError("network down")

        self.assertIsNone(sample_bybit_server_clock_offset_v1(fetch=broken))
        self.assertIsNone(
            sample_bybit_server_clock_offset_v1(fetch=lambda _: '{"retCode":1}')
        )


class ResourceSampleTests(unittest.TestCase):
    def test_resource_sample_is_sane(self) -> None:
        sample = sample_process_resources_v1()
        self.assertGreaterEqual(sample.cpu_seconds, 0.0)
        if sample.peak_working_set_bytes is not None:
            self.assertGreater(sample.peak_working_set_bytes, 0)


class ReportTests(unittest.TestCase):
    def _report(self) -> dict:
        contracts = first_party_bybit_measurement_contracts_v1()
        measurement = CapacityMeasurementV1()
        measurement.sample_resources()
        stats = measurement.stats_for(ETH)
        arrival = TS_MILLIS * 1_000_000
        for index in range(10):
            stats.observe(_record(arrival + index, index), 1_000)
        measurement.sample_resources()
        compaction = CompactionResultV1(
            directory=Path("x"),
            session_id=uuid4(),
            source_id=ETH.source_id,
            uncompressed_bytes=10_000,
            compressed_bytes=1_600,
            already_compacted=False,
        )
        return build_capacity_report_v1(
            measurement,
            contracts=contracts,
            measured_seconds=100.0,
            compactions=(compaction,),
            session_summaries={str(ETH.source_id): {"reconnects": 1}},
            disk_free_start_bytes=5_000_000,
            disk_free_end_bytes=4_000_000,
            failures={"GMTUSDT:RuntimeError:x": 2},
            restarts=2,
            end_proof="OPERATOR_BOUNDED_STOP",
            clock_samples=(
                {"offset_estimate_nanos": 9_400_000_000, "offset_bound_nanos": 170_000_000},
                {"offset_estimate_nanos": 9_450_000_000, "offset_bound_nanos": 160_000_000},
            ),
            clock_sample_failures=1,
        )

    def test_report_carries_measured_facts_and_labels(self) -> None:
        report = self._report()
        eth = next(item for item in report["symbols"] if item["exchange_symbol"] == "ETHUSDT")
        self.assertEqual(2, eth["turnover_rank_when_sampled"])
        self.assertEqual(10, eth["records"])
        self.assertAlmostEqual(0.1, eth["records_per_second"])
        self.assertAlmostEqual(10_000 * 86_400 / 100.0, eth["raw_bytes_per_day_projected"])
        self.assertAlmostEqual(6.25, eth["compression_ratio"])
        self.assertAlmostEqual(
            eth["raw_bytes_per_day_projected"] / 6.25, eth["compressed_bytes_per_day_projected"]
        )
        self.assertEqual({"reconnects": 1}, eth["recorder"])
        self.assertIn("tickers", eth[DELTA_LABEL_V1])
        self.assertEqual(PROJECTION_LABEL_V1, report["projection_label"])
        self.assertEqual(DELTA_CAVEAT_V1, report["delta_caveat"])
        self.assertEqual(9_400_000_000, report["clock_offset_summary"]["min_offset_estimate_nanos"])
        self.assertEqual(170_000_000, report["clock_offset_summary"]["max_offset_bound_nanos"])
        self.assertEqual(1, report["clock_offset_sample_failures"])
        # A symbol never compacted has no measured ratio, so totals stay unknown.
        self.assertIsNone(report["totals"]["compressed_bytes_per_day_projected"])

    def test_the_delta_is_never_called_latency(self) -> None:
        report = self._report()
        text = json.dumps(report)
        self.assertNotIn('"latency', text)
        self.assertIn("not latency", report["delta_caveat"])
        rendered = render_capacity_report_text_v1(report)
        self.assertIn("not latency", rendered)
        self.assertIn("lane failure x2", rendered)

    def test_report_is_written_as_json_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = write_capacity_report_v1(self._report(), Path(temp) / "reports", stamp="T")
            self.assertEqual(self._report()["symbols"][0]["exchange_symbol"],
                             json.loads(path.read_text(encoding="utf-8"))["symbols"][0]["exchange_symbol"])
            self.assertTrue(path.with_suffix(".txt").exists())


if __name__ == "__main__":
    unittest.main()
