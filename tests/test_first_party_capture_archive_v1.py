"""Phase 3Z.2 -- the capture archive must preserve, prove, and refuse.

Covers verbatim payload preservation, deterministic record identity, the
two-clock model and its fail-closed rules, partition immutability and manifest
integrity, PARTIAL versus COMPLETE, replay reproducing the exact sequence, and
the refusals that stop synthetic or REST-rebuilt data masquerading as capture.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from uuid import uuid4

from trade_platform.first_party_capture_archive_v1 import (
    CLOCK_DIVERGENCE_TOLERANCE_NANOS,
    END_PROOF_CLEAN_CLOSE,
    END_PROOF_CONNECTION_LOST,
    MANIFEST_FILE_NAME,
    OPEN_MARKER_NAME,
    PARTITION_STATUS_COMPLETE,
    PARTITION_STATUS_PARTIAL,
    CaptureClockMonitorV1,
    CaptureClockReadingV1,
    CaptureCoverageIntervalV1,
    CaptureGapKindV1,
    CaptureGapV1,
    CaptureLifecycleEventV1,
    CaptureLifecycleKindV1,
    CapturePartitionWriterV1,
    FirstPartyCaptureArchiveError,
    FirstPartyCaptureRecordV1,
    build_capture_record_v1,
    derive_session_gaps_v1,
    find_partitions_v1,
    measure_clock_resolution_nanos,
    read_lifecycle_v1,
    read_partition_status_v1,
    replay_partition_v1,
)
from trade_platform.first_party_capture_authority_v1 import (
    first_party_bybit_capture_contract_v1,
)

CONTRACT = first_party_bybit_capture_contract_v1()
DAY = date(2026, 9, 23)

#: Deliberately awkward: unsorted keys, odd spacing and a trailing-zero decimal
#: that any re-serialization would quietly rewrite.
TICKER_PAYLOAD = (
    '{"topic":"tickers.BTCUSDT","type":"snapshot","ts":1790132704184,'
    '"data":{"symbol":"BTCUSDT","markPrice":"63000.50","indexPrice":"63001.00",'
    '"lastPrice": "63000.5"}}'
)
TRADE_PAYLOAD = (
    '{"topic":"publicTrade.BTCUSDT","type":"snapshot","ts":1790132704999,'
    '"data":[{"s":"BTCUSDT","p":"63000.5","v":"0.010","S":"Buy","i":"abc","T":1790132704990}]}'
)


def _reading(index: int) -> CaptureClockReadingV1:
    return CaptureClockReadingV1(
        arrival_utc_nanos=1_790_132_695_000_000_000 + index * 1_000_000,
        arrival_monotonic_nanos=35_552_500_000_000 + index * 1_000_000,
    )


def _record(session_id, index: int, payload: str = TICKER_PAYLOAD) -> FirstPartyCaptureRecordV1:
    return build_capture_record_v1(
        contract=CONTRACT,
        session_id=session_id,
        sequence=index,
        clock=_reading(index),
        payload_text=payload,
    )


class RecordIdentityTests(unittest.TestCase):
    def test_payload_is_preserved_verbatim(self) -> None:
        record = _record(uuid4(), 0)
        self.assertEqual(TICKER_PAYLOAD, record.payload_text)
        # Round-tripping through the archive must not normalize the payload.
        restored = FirstPartyCaptureRecordV1.from_json_line(record.to_json_line())
        self.assertEqual(TICKER_PAYLOAD, restored.payload_text)
        self.assertNotEqual(
            json.dumps(json.loads(TICKER_PAYLOAD), separators=(",", ":")),
            restored.payload_text,
            "a re-serialized payload would silently differ from what Bybit sent",
        )

    def test_content_hash_is_deterministic_and_verified(self) -> None:
        session = uuid4()
        self.assertEqual(_record(session, 3).content_hash, _record(session, 3).content_hash)
        self.assertTrue(_record(session, 3).integrity_verified())

    def test_hash_covers_arrival_so_an_edited_record_fails(self) -> None:
        record = _record(uuid4(), 0)
        forged = dataclasses.replace(record, arrival_utc_nanos=record.arrival_utc_nanos - 5_000)
        self.assertFalse(forged.integrity_verified())

    def test_edited_payload_fails_on_read(self) -> None:
        record = _record(uuid4(), 0)
        tampered = json.loads(record.to_json_line())
        tampered["payload"] = TICKER_PAYLOAD.replace("63000.50", "99999.00")
        with self.assertRaises(FirstPartyCaptureArchiveError):
            FirstPartyCaptureRecordV1.from_json_line(json.dumps(tampered))

    def test_arrival_and_exchange_timestamps_stay_distinct(self) -> None:
        record = _record(uuid4(), 0)
        self.assertEqual(1790132704184, record.exchange_timestamp_millis)
        self.assertNotEqual(
            record.exchange_timestamp_millis * 1_000_000, record.arrival_utc_nanos
        )


class RecordRefusalTests(unittest.TestCase):
    def test_generated_records_are_refused(self) -> None:
        payload = TICKER_PAYLOAD[:-1] + ',"generated":true}'
        with self.assertRaises(FirstPartyCaptureArchiveError) as caught:
            _record(uuid4(), 0, payload)
        self.assertIn("generated_records_are_not_capture_evidence", str(caught.exception))

    def test_foreign_topic_is_refused(self) -> None:
        payload = TICKER_PAYLOAD.replace("tickers.BTCUSDT", "orderbook.50.BTCUSDT")
        with self.assertRaises(FirstPartyCaptureArchiveError):
            _record(uuid4(), 0, payload)

    def test_foreign_symbol_is_refused(self) -> None:
        payload = TICKER_PAYLOAD.replace("tickers.BTCUSDT", "tickers.ETHUSDT")
        with self.assertRaises(FirstPartyCaptureArchiveError):
            _record(uuid4(), 0, payload)

    def test_payload_symbol_must_agree_with_its_topic(self) -> None:
        payload = TICKER_PAYLOAD.replace('"symbol":"BTCUSDT"', '"symbol":"ETHUSDT"')
        with self.assertRaises(FirstPartyCaptureArchiveError) as caught:
            _record(uuid4(), 0, payload)
        self.assertIn("disagrees_with_topic", str(caught.exception))

    def test_trade_payload_symbol_must_agree(self) -> None:
        payload = TRADE_PAYLOAD.replace('"s":"BTCUSDT"', '"s":"ETHUSDT"')
        with self.assertRaises(FirstPartyCaptureArchiveError):
            _record(uuid4(), 0, payload)

    def test_non_json_payload_is_refused(self) -> None:
        with self.assertRaises(FirstPartyCaptureArchiveError):
            _record(uuid4(), 0, "not json at all")

    def test_unknown_message_type_is_refused(self) -> None:
        payload = TICKER_PAYLOAD.replace('"type":"snapshot"', '"type":"rest_rebuild"')
        with self.assertRaises(FirstPartyCaptureArchiveError):
            _record(uuid4(), 0, payload)


class ClockIntegrityTests(unittest.TestCase):
    def test_measured_resolution_is_reported_not_assumed(self) -> None:
        resolution = measure_clock_resolution_nanos()
        self.assertGreaterEqual(resolution, 1)
        self.assertIsInstance(resolution, int)

    def test_first_reading_is_accepted_without_a_comparison(self) -> None:
        verdict = CaptureClockMonitorV1().observe(_reading(0))
        self.assertTrue(verdict.accepted)
        self.assertFalse(verdict.discontinuity)
        self.assertIsNone(verdict.wall_delta_nanos)

    def test_steady_readings_are_accepted(self) -> None:
        monitor = CaptureClockMonitorV1()
        monitor.observe(_reading(0))
        verdict = monitor.observe(_reading(1))
        self.assertTrue(verdict.accepted)
        self.assertFalse(verdict.discontinuity)
        self.assertEqual(0, verdict.divergence_nanos)

    def test_wall_clock_regression_fails_closed(self) -> None:
        monitor = CaptureClockMonitorV1()
        monitor.observe(_reading(5))
        regressed = CaptureClockReadingV1(
            arrival_utc_nanos=_reading(5).arrival_utc_nanos - 1_000_000,
            arrival_monotonic_nanos=_reading(6).arrival_monotonic_nanos,
        )
        verdict = monitor.observe(regressed)
        self.assertFalse(verdict.accepted)
        self.assertIn("wall_clock_regressed_within_session", verdict.reasons)

    def test_monotonic_regression_fails_closed(self) -> None:
        monitor = CaptureClockMonitorV1()
        monitor.observe(_reading(5))
        regressed = CaptureClockReadingV1(
            arrival_utc_nanos=_reading(6).arrival_utc_nanos,
            arrival_monotonic_nanos=_reading(5).arrival_monotonic_nanos - 1,
        )
        verdict = monitor.observe(regressed)
        self.assertFalse(verdict.accepted)
        self.assertIn("monotonic_counter_regressed_within_session", verdict.reasons)

    def test_suspend_style_divergence_is_a_discontinuity_not_a_smooth_gap(self) -> None:
        """Wall clock jumps far further than the monotonic counter: a host suspend."""
        monitor = CaptureClockMonitorV1()
        base = _reading(0)
        monitor.observe(base)
        after_suspend = CaptureClockReadingV1(
            arrival_utc_nanos=base.arrival_utc_nanos + 600_000_000_000,
            arrival_monotonic_nanos=base.arrival_monotonic_nanos + 1_000_000,
        )
        verdict = monitor.observe(after_suspend)
        self.assertTrue(verdict.accepted)
        self.assertTrue(verdict.discontinuity)
        self.assertIn("wall_versus_monotonic_divergence_exceeds_tolerance", verdict.reasons)

    def test_divergence_inside_tolerance_is_not_a_discontinuity(self) -> None:
        monitor = CaptureClockMonitorV1()
        base = _reading(0)
        monitor.observe(base)
        jittered = CaptureClockReadingV1(
            arrival_utc_nanos=base.arrival_utc_nanos + CLOCK_DIVERGENCE_TOLERANCE_NANOS // 2,
            arrival_monotonic_nanos=base.arrival_monotonic_nanos,
        )
        self.assertFalse(monitor.observe(jittered).discontinuity)

    def test_reset_starts_a_fresh_continuity_claim(self) -> None:
        monitor = CaptureClockMonitorV1()
        monitor.observe(_reading(10))
        monitor.reset()
        verdict = monitor.observe(_reading(0))
        self.assertTrue(verdict.accepted)
        self.assertIsNone(verdict.wall_delta_nanos)


class PartitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.session = uuid4()

    def _writer(self, session=None) -> CapturePartitionWriterV1:
        return CapturePartitionWriterV1(
            root=self.root,
            contract=CONTRACT,
            session_id=session or self.session,
            day=DAY,
            clock_resolution_nanos=15_600_000,
        )

    def test_open_partition_is_partial_and_marked(self) -> None:
        writer = self._writer()
        writer.append_record(_record(self.session, 0))
        self.assertTrue((writer.directory / OPEN_MARKER_NAME).exists())
        partition = read_partition_status_v1(writer.directory)
        self.assertEqual(PARTITION_STATUS_PARTIAL, partition.status)
        self.assertIn("partition_has_no_manifest", partition.reasons)
        writer.close_without_finalizing()

    def test_finalized_partition_is_complete_and_unmarked(self) -> None:
        writer = self._writer()
        writer.append_record(_record(self.session, 0))
        writer.append_record(_record(self.session, 1, TRADE_PAYLOAD))
        writer.declare_coverage(
            CaptureCoverageIntervalV1(
                start_utc_nanos=_reading(0).arrival_utc_nanos,
                end_utc_nanos=_reading(1).arrival_utc_nanos,
                end_proof=END_PROOF_CLEAN_CLOSE,
                record_count=2,
            )
        )
        writer.finalize()
        self.assertFalse((writer.directory / OPEN_MARKER_NAME).exists())
        partition = read_partition_status_v1(writer.directory)
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status)
        self.assertEqual((), partition.reasons)
        self.assertEqual(2, partition.record_count)
        self.assertEqual(1, len(partition.coverage))

    def test_finalized_partition_cannot_be_reopened(self) -> None:
        writer = self._writer()
        writer.append_record(_record(self.session, 0))
        writer.finalize()
        with self.assertRaises(FirstPartyCaptureArchiveError):
            self._writer()

    def test_finalize_twice_is_refused(self) -> None:
        writer = self._writer()
        writer.finalize()
        with self.assertRaises(FirstPartyCaptureArchiveError):
            writer.finalize()

    def test_append_after_finalize_is_refused(self) -> None:
        writer = self._writer()
        writer.finalize()
        with self.assertRaises(FirstPartyCaptureArchiveError):
            writer.append_record(_record(self.session, 0))

    def test_a_foreign_session_record_is_refused(self) -> None:
        writer = self._writer()
        with self.assertRaises(FirstPartyCaptureArchiveError):
            writer.append_record(_record(uuid4(), 0))
        writer.close_without_finalizing()

    def test_tampered_records_file_breaks_the_manifest(self) -> None:
        writer = self._writer()
        writer.append_record(_record(self.session, 0))
        writer.finalize()
        records = writer.directory / "records.ndjson"
        records.write_text(records.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        partition = read_partition_status_v1(writer.directory)
        self.assertEqual(PARTITION_STATUS_PARTIAL, partition.status)
        self.assertTrue(
            any(reason.startswith("manifest_file_checksum_mismatch") for reason in partition.reasons)
        )

    def test_tampered_manifest_fails_its_own_hash(self) -> None:
        writer = self._writer()
        writer.append_record(_record(self.session, 0))
        writer.finalize()
        path = writer.directory / MANIFEST_FILE_NAME
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["record_count"] = 9999
        path.write_text(json.dumps(manifest), encoding="utf-8")
        partition = read_partition_status_v1(writer.directory)
        self.assertEqual(PARTITION_STATUS_PARTIAL, partition.status)
        self.assertIn("manifest_content_hash_mismatch", partition.reasons)

    def test_partitions_are_discoverable_under_a_root(self) -> None:
        writer = self._writer()
        writer.finalize()
        self.assertEqual((writer.directory,), find_partitions_v1(self.root))

    def test_layout_is_object_storage_shaped(self) -> None:
        writer = self._writer()
        writer.close_without_finalizing()
        relative = writer.directory.relative_to(self.root).as_posix()
        self.assertEqual(
            f"v1/exchange=BYBIT/instrument=CRYPTO-BYBIT-BTCUSDT-PERP/"
            f"date=2026-09-23/session={self.session}",
            relative,
        )


class ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.session = uuid4()
        self.writer = CapturePartitionWriterV1(
            root=self.root, contract=CONTRACT, session_id=self.session, day=DAY
        )
        self.written = [
            _record(self.session, 0),
            _record(self.session, 1, TRADE_PAYLOAD),
            _record(self.session, 2),
        ]
        for record in self.written:
            self.writer.append_record(record)

    def test_replay_reproduces_the_exact_sequence_and_hashes(self) -> None:
        self.writer.finalize()
        replayed = list(replay_partition_v1(self.writer.directory))
        self.assertEqual([r.content_hash for r in self.written], [r.content_hash for r in replayed])
        self.assertEqual([r.payload_text for r in self.written], [r.payload_text for r in replayed])
        self.assertEqual([0, 1, 2], [r.sequence for r in replayed])

    def test_replay_refuses_an_unproven_partition(self) -> None:
        self.writer.close_without_finalizing()
        with self.assertRaises(FirstPartyCaptureArchiveError):
            list(replay_partition_v1(self.writer.directory))

    def test_partial_replay_is_possible_when_explicitly_allowed(self) -> None:
        self.writer.close_without_finalizing()
        self.assertEqual(
            3, len(list(replay_partition_v1(self.writer.directory, require_complete=False)))
        )

    def test_out_of_order_records_fail_closed_on_replay(self) -> None:
        self.writer.finalize()
        records = self.writer.directory / "records.ndjson"
        lines = records.read_text(encoding="utf-8").splitlines()
        records.write_text("\n".join([lines[0], lines[2], lines[1]]) + "\n", encoding="utf-8")
        with self.assertRaises(FirstPartyCaptureArchiveError):
            list(replay_partition_v1(self.writer.directory, require_complete=False))

    def test_lifecycle_round_trips(self) -> None:
        self.writer.append_lifecycle(
            CaptureLifecycleEventV1(
                kind=CaptureLifecycleKindV1.SUBSCRIPTIONS_ACKNOWLEDGED.value,
                arrival_utc_nanos=_reading(0).arrival_utc_nanos,
                arrival_monotonic_nanos=_reading(0).arrival_monotonic_nanos,
                detail="tickers.BTCUSDT",
            )
        )
        self.writer.finalize()
        events = read_lifecycle_v1(self.writer.directory)
        self.assertEqual(1, len(events))
        self.assertEqual(
            CaptureLifecycleKindV1.SUBSCRIPTIONS_ACKNOWLEDGED.value, events[0].kind
        )


class CoverageAndGapTests(unittest.TestCase):
    def test_quiet_coverage_and_an_unobserved_hole_are_different(self) -> None:
        covered = CaptureCoverageIntervalV1(
            start_utc_nanos=1_000, end_utc_nanos=9_000, end_proof=END_PROOF_CLEAN_CLOSE,
            record_count=0,
        )
        self.assertEqual(0, covered.record_count)  # quiet, but positively covered
        self.assertEqual((), derive_session_gaps_v1([covered]))

    def test_a_hole_between_windows_is_an_explicit_gap(self) -> None:
        gaps = derive_session_gaps_v1(
            [
                CaptureCoverageIntervalV1(1_000, 5_000, END_PROOF_CONNECTION_LOST, 10),
                CaptureCoverageIntervalV1(8_000, 9_000, END_PROOF_CLEAN_CLOSE, 4),
            ]
        )
        self.assertEqual(1, len(gaps))
        self.assertEqual(5_000, gaps[0].start_utc_nanos)
        self.assertEqual(8_000, gaps[0].end_utc_nanos)

    def test_abutting_windows_produce_no_gap(self) -> None:
        self.assertEqual(
            (),
            derive_session_gaps_v1(
                [
                    CaptureCoverageIntervalV1(1_000, 5_000, END_PROOF_CLEAN_CLOSE, 3),
                    CaptureCoverageIntervalV1(5_000, 9_000, END_PROOF_CLEAN_CLOSE, 3),
                ]
            ),
        )

    def test_an_interval_cannot_end_before_it_starts(self) -> None:
        with self.assertRaises(FirstPartyCaptureArchiveError):
            CaptureCoverageIntervalV1(9_000, 1_000, END_PROOF_CLEAN_CLOSE, 0)

    def test_gap_kinds_name_the_cause(self) -> None:
        gap = CaptureGapV1(1, 2, CaptureGapKindV1.RECORDER_NOT_RUNNING.value)
        self.assertEqual("RECORDER_NOT_RUNNING", gap.kind)


if __name__ == "__main__":
    unittest.main()
