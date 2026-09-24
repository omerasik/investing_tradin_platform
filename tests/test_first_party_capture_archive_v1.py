"""Phase 3Z.2 -- the capture archive must preserve, prove, and refuse.

Covers verbatim payload preservation, deterministic record identity, the
two-clock model and its fail-closed rules, partition immutability and manifest
integrity, PARTIAL versus COMPLETE, replay reproducing the exact sequence, and
the refusals that stop synthetic or REST-rebuilt data masquerading as capture.
"""

from __future__ import annotations

import dataclasses
import gzip
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from trade_platform.first_party_capture_archive_v1 import (
    BACKUP_ALREADY_PRESENT,
    BACKUP_COPIED_AND_VERIFIED,
    BACKUP_SKIPPED_NOT_COMPLETE,
    CLOCK_DIVERGENCE_TOLERANCE_NANOS,
    COMPACTED_RECORDS_FILE_NAME,
    COMPACTION_FILE_NAME,
    END_PROOF_CONNECTION_LOST,
    END_PROOF_DISK_BUDGET_STOP,
    END_PROOF_OPERATOR_BOUNDED_STOP,
    END_PROOF_UTC_DAY_ROLLOVER,
    END_PROOFS_V1,
    GAP_KIND_FOR_END_PROOF_V1,
    MANIFEST_FILE_NAME,
    OPEN_MARKER_NAME,
    PARTITION_STATUS_COMPLETE,
    PARTITION_STATUS_PARTIAL,
    RECORDS_FILE_NAME,
    REQUESTABLE_END_PROOFS_V1,
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
    ProvenWindowV1,
    backup_archive_v1,
    backup_partition_v1,
    build_capture_record_v1,
    compact_partition_v1,
    compaction_candidates_v1,
    derive_archive_availability_by_source_v1,
    derive_archive_availability_v1,
    derive_capture_gaps_v1,
    find_partitions_v1,
    knowledge_bound_utc_nanos,
    measure_clock_resolution_nanos,
    nanos_to_datetime,
    open_partition_bytes_v1,
    read_lifecycle_v1,
    read_partition_status_v1,
    replay_partition_v1,
    session_source_id_v1,
    sha256_file,
    utc_day_of_nanos,
    verify_partition_v1,
)
from trade_platform.first_party_capture_authority_v1 import (
    FirstPartyCaptureContractV1,
    first_party_bybit_capture_contract_v1,
    first_party_bybit_measurement_contract_v1,
)

CONTRACT = first_party_bybit_capture_contract_v1()
DAY = date(2026, 9, 23)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SESSION_A = uuid4()
SESSION_B = uuid4()

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


def _record(
    session_id, index: int, payload: str = TICKER_PAYLOAD, *, sequence: int | None = None
) -> FirstPartyCaptureRecordV1:
    return build_capture_record_v1(
        contract=CONTRACT,
        session_id=session_id,
        sequence=index if sequence is None else sequence,
        clock=_reading(index),
        payload_text=payload,
    )


def _cover(
    writer: CapturePartitionWriterV1, first: int, last: int, count: int
) -> CaptureCoverageIntervalV1:
    """Declare the window a test's records were written inside."""
    interval = CaptureCoverageIntervalV1(
        start_utc_nanos=_reading(first).arrival_utc_nanos,
        last_proven_utc_nanos=_reading(last).arrival_utc_nanos,
        end_proof=END_PROOF_OPERATOR_BOUNDED_STOP,
        record_count=count,
    )
    writer.declare_coverage(interval)
    return interval


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
        _cover(writer, 0, 1, 2)
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
        _cover(writer, 0, 0, 1)
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
        _cover(writer, 0, 0, 1)
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
        _cover(writer, 0, 0, 1)
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
        # The partition path reads as a high-entropy token to the secret
        # scanner. It is a filesystem layout assertion, not a credential.
        expected = (
            "v1/exchange=BYBIT/instrument=CRYPTO-BYBIT-BTCUSDT-PERP/"  # pragma: allowlist secret
            f"date=2026-09-23/session={self.session}"
        )
        self.assertEqual(expected, relative)


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
        _cover(self.writer, 0, 2, 3)

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
    """Half-open ``[start, last_proven + 1)`` semantics and derived gaps."""

    @staticmethod
    def _window(start: int, last: int, proof: str, session=None) -> ProvenWindowV1:
        return ProvenWindowV1(
            session_id=session or SESSION_A,
            interval=CaptureCoverageIntervalV1(start, last, proof, 0),
        )

    def test_half_open_boundaries_include_first_and_last_proof_only(self) -> None:
        window = CaptureCoverageIntervalV1(1_000, 4_999, END_PROOF_OPERATOR_BOUNDED_STOP, 2)
        self.assertEqual(5_000, window.end_utc_nanos)
        self.assertTrue(window.contains(1_000))
        self.assertTrue(window.contains(4_999))
        self.assertFalse(window.contains(999))
        self.assertFalse(window.contains(5_000))

    def test_a_single_instant_window_contains_that_instant(self) -> None:
        window = CaptureCoverageIntervalV1(7_000, 7_000, END_PROOF_OPERATOR_BOUNDED_STOP, 1)
        self.assertTrue(window.contains(7_000))
        self.assertEqual(7_001, window.end_utc_nanos)

    def test_the_exclusive_end_is_derived_and_cannot_be_forged(self) -> None:
        payload = CaptureCoverageIntervalV1(
            1_000, 4_999, END_PROOF_OPERATOR_BOUNDED_STOP, 0
        ).to_payload()
        self.assertEqual(4_999, payload["last_proven_utc_nanos"])
        self.assertEqual(5_000, payload["end_utc_nanos_exclusive"])
        payload["end_utc_nanos_exclusive"] = 9_000
        with self.assertRaises(FirstPartyCaptureArchiveError):
            CaptureCoverageIntervalV1.from_payload(payload)

    def test_quiet_coverage_and_an_unobserved_hole_are_different(self) -> None:
        quiet = self._window(1_000, 9_000, END_PROOF_OPERATOR_BOUNDED_STOP)
        self.assertEqual(0, quiet.interval.record_count)  # quiet, but positively covered
        self.assertEqual((), derive_capture_gaps_v1([quiet]))

    def test_a_hole_between_windows_starts_at_the_exclusive_end(self) -> None:
        gaps = derive_capture_gaps_v1(
            [
                self._window(1_000, 4_999, END_PROOF_CONNECTION_LOST),
                self._window(8_000, 9_000, END_PROOF_OPERATOR_BOUNDED_STOP),
            ]
        )
        (gap,) = gaps
        self.assertEqual(5_000, gap.start_utc_nanos)
        self.assertEqual(8_000, gap.end_utc_nanos)
        self.assertEqual(CaptureGapKindV1.CONNECTION_LOSS.value, gap.kind)

    def test_abutting_windows_produce_no_gap_but_one_nanosecond_does(self) -> None:
        first = self._window(1_000, 4_999, END_PROOF_UTC_DAY_ROLLOVER)
        self.assertEqual(
            (), derive_capture_gaps_v1([first, self._window(5_000, 9_000, END_PROOF_OPERATOR_BOUNDED_STOP)])
        )
        (gap,) = derive_capture_gaps_v1(
            [first, self._window(5_001, 9_000, END_PROOF_OPERATOR_BOUNDED_STOP)]
        )
        self.assertEqual((5_000, 5_001), (gap.start_utc_nanos, gap.end_utc_nanos))
        self.assertEqual(CaptureGapKindV1.PARTITION_ROLLOVER.value, gap.kind)

    def test_a_gap_between_sessions_is_a_session_boundary_whatever_ended_it(self) -> None:
        (gap,) = derive_capture_gaps_v1(
            [
                self._window(1_000, 2_000, END_PROOF_OPERATOR_BOUNDED_STOP, SESSION_A),
                self._window(9_000, 9_500, END_PROOF_OPERATOR_BOUNDED_STOP, SESSION_B),
            ]
        )
        self.assertEqual(CaptureGapKindV1.SESSION_BOUNDARY.value, gap.kind)

    def test_overlapping_sessions_merge_rather_than_invent_a_gap(self) -> None:
        self.assertEqual(
            (),
            derive_capture_gaps_v1(
                [
                    self._window(1_000, 9_000, END_PROOF_OPERATOR_BOUNDED_STOP, SESSION_A),
                    self._window(2_000, 3_000, END_PROOF_OPERATOR_BOUNDED_STOP, SESSION_B),
                    self._window(9_001, 9_500, END_PROOF_OPERATOR_BOUNDED_STOP, SESSION_B),
                ]
            ),
        )

    def test_malformed_intervals_and_gaps_are_refused(self) -> None:
        with self.assertRaises(FirstPartyCaptureArchiveError):
            CaptureCoverageIntervalV1(9_000, 1_000, END_PROOF_OPERATOR_BOUNDED_STOP, 0)
        with self.assertRaises(FirstPartyCaptureArchiveError):
            CaptureCoverageIntervalV1(1_000, 2_000, "CLEAN_CLOSE", 0)
        with self.assertRaises(FirstPartyCaptureArchiveError):
            CaptureCoverageIntervalV1(1_000, 2_000, END_PROOF_OPERATOR_BOUNDED_STOP, -1)
        with self.assertRaises(FirstPartyCaptureArchiveError):
            CaptureGapV1(5, 2, CaptureGapKindV1.CONNECTION_LOSS.value)
        with self.assertRaises(FirstPartyCaptureArchiveError):
            CaptureGapV1(1, 2, "RECORDER_NOT_RUNNING")

    def test_gap_overlap_is_judged_on_half_open_bounds(self) -> None:
        window = CaptureCoverageIntervalV1(1_000, 4_999, END_PROOF_CONNECTION_LOST, 0)
        kind = CaptureGapKindV1.CONNECTION_LOSS.value
        self.assertFalse(CaptureGapV1(5_000, 6_000, kind).overlaps(window))
        self.assertFalse(CaptureGapV1(5_000, None, kind).overlaps(window))
        self.assertTrue(CaptureGapV1(4_999, 6_000, kind).overlaps(window))
        self.assertTrue(CaptureGapV1(500, None, kind).overlaps(window))
        self.assertFalse(CaptureGapV1(500, 1_000, kind).overlaps(window))


class ClockConversionTests(unittest.TestCase):
    """No conversion may move an arrival earlier than the measured reading."""

    def test_datetime_conversion_never_moves_an_arrival_earlier(self) -> None:
        base = _reading(0).arrival_utc_nanos
        for offset in (0, 1, 999, 1_000, 1_001, 123_456_789, 999_999_999):
            nanos = base + offset
            converted = nanos_to_datetime(nanos)
            as_nanos = (converted - EPOCH) // timedelta(microseconds=1) * 1_000
            self.assertGreaterEqual(as_nanos, nanos)
            self.assertLess(as_nanos - nanos, 1_000)

    def test_utc_day_is_exact_at_the_last_nanosecond_before_midnight(self) -> None:
        midnight = int((datetime(2026, 9, 24, tzinfo=UTC) - EPOCH).total_seconds()) * 1_000_000_000
        self.assertEqual(date(2026, 9, 23), utc_day_of_nanos(midnight - 1))
        self.assertEqual(date(2026, 9, 24), utc_day_of_nanos(midnight))

    def test_knowledge_bound_is_never_earlier_than_the_reading(self) -> None:
        arrival = _reading(0).arrival_utc_nanos
        self.assertEqual(arrival + 15_625_000, knowledge_bound_utc_nanos(arrival, 15_625_000))
        self.assertGreater(knowledge_bound_utc_nanos(arrival, 1), arrival)
        with self.assertRaises(FirstPartyCaptureArchiveError):
            knowledge_bound_utc_nanos(arrival, 0)


class WriterInvariantTests(unittest.TestCase):
    """The writer refuses coverage bookkeeping that would misplace a record."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.session = uuid4()
        self.writer = CapturePartitionWriterV1(
            root=Path(self._temp.name), contract=CONTRACT, session_id=self.session, day=DAY
        )
        self.addCleanup(self.writer.close_without_finalizing)

    def test_a_window_ending_at_the_last_record_cannot_exclude_it(self) -> None:
        """The pre-fix bug: end = last arrival under half-open semantics."""
        self.writer.append_record(_record(self.session, 0))
        self.writer.append_record(_record(self.session, 1))
        with self.assertRaises(FirstPartyCaptureArchiveError):
            self.writer.declare_coverage(
                CaptureCoverageIntervalV1(
                    _reading(0).arrival_utc_nanos,
                    _reading(1).arrival_utc_nanos - 1,
                    END_PROOF_OPERATOR_BOUNDED_STOP,
                    2,
                )
            )

    def test_the_window_count_must_match_the_records_it_claims(self) -> None:
        self.writer.append_record(_record(self.session, 0))
        with self.assertRaises(FirstPartyCaptureArchiveError):
            _cover(self.writer, 0, 0, 2)

    def test_finalize_refuses_records_no_window_claims(self) -> None:
        self.writer.append_record(_record(self.session, 0))
        with self.assertRaises(FirstPartyCaptureArchiveError):
            self.writer.finalize()

    def test_declarations_are_ordered_and_disjoint(self) -> None:
        _cover(self.writer, 0, 5, 0)
        with self.assertRaises(FirstPartyCaptureArchiveError):
            _cover(self.writer, 5, 6, 0)  # starts at 5, inside [0, 5 + 1)
        kind = CaptureGapKindV1.CONNECTION_LOSS.value
        with self.assertRaises(FirstPartyCaptureArchiveError):
            self.writer.declare_gap(CaptureGapV1(_reading(5).arrival_utc_nanos, None, kind))
        self.writer.declare_gap(CaptureGapV1(_reading(5).arrival_utc_nanos + 1, None, kind))
        with self.assertRaises(FirstPartyCaptureArchiveError):
            _cover(self.writer, 9, 9, 0)  # nothing follows an open-ended gap

    def test_a_gap_cannot_swallow_unclaimed_records(self) -> None:
        self.writer.append_record(_record(self.session, 0))
        with self.assertRaises(FirstPartyCaptureArchiveError):
            self.writer.declare_gap(
                CaptureGapV1(_reading(0).arrival_utc_nanos, None, CaptureGapKindV1.CONNECTION_LOSS.value)
            )

    def test_a_record_from_another_utc_day_is_refused(self) -> None:
        other_day = build_capture_record_v1(
            contract=CONTRACT,
            session_id=self.session,
            sequence=0,
            clock=CaptureClockReadingV1(
                arrival_utc_nanos=_reading(0).arrival_utc_nanos + 86_400 * 1_000_000_000,
                arrival_monotonic_nanos=_reading(0).arrival_monotonic_nanos,
            ),
            payload_text=TICKER_PAYLOAD,
        )
        with self.assertRaises(FirstPartyCaptureArchiveError):
            self.writer.append_record(other_day)


def _rewrite_manifest(directory: Path, mutate) -> None:
    """Forge a manifest that is internally hash-consistent, to prove the deeper checks."""
    from trade_platform.first_party_capture_archive_v1 import _canonical_json, _sha256_text

    path = directory / MANIFEST_FILE_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.pop("manifest_content_hash")
    mutate(manifest)
    manifest["files"] = {
        name: sha256_file(directory / name) for name in manifest["files"]
    }
    manifest["manifest_content_hash"] = _sha256_text(_canonical_json(manifest))
    path.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")


class ManifestConsistencyTests(unittest.TestCase):
    """A COMPLETE partition needs consistent metadata, not just matching hashes."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.session = uuid4()
        writer = CapturePartitionWriterV1(
            root=Path(self._temp.name), contract=CONTRACT, session_id=self.session, day=DAY
        )
        writer.append_record(_record(self.session, 0))
        writer.append_record(_record(self.session, 1, TRADE_PAYLOAD))
        _cover(writer, 0, 1, 2)
        writer.declare_gap(
            CaptureGapV1(_reading(1).arrival_utc_nanos + 1, _reading(5).arrival_utc_nanos,
                         CaptureGapKindV1.CONNECTION_LOSS.value)
        )
        writer.append_record(_record(self.session, 5, sequence=2))
        _cover(writer, 5, 5, 1)
        writer.finalize()
        self.directory = writer.directory

    def _reasons(self) -> tuple[str, ...]:
        partition = read_partition_status_v1(self.directory)
        self.assertEqual(PARTITION_STATUS_PARTIAL, partition.status)
        return partition.reasons

    def test_an_honest_partition_verifies_end_to_end(self) -> None:
        verification = verify_partition_v1(self.directory)
        self.assertEqual(3, verification.record_count)
        self.assertEqual({"publicTrade": 1, "tickers": 2}, dict(verification.records_by_channel))
        self.assertEqual(2, len(verification.coverage))

    def test_coverage_counts_must_sum_to_the_manifest_count(self) -> None:
        _rewrite_manifest(self.directory, lambda m: m.__setitem__("record_count", 4))
        self.assertIn("coverage_record_counts_do_not_sum_to_the_manifest_count", self._reasons())

    def test_a_consistent_but_inflated_count_fails_on_replay(self) -> None:
        def inflate(manifest):
            manifest["record_count"] = 4
            manifest["coverage"][1]["record_count"] = 2

        _rewrite_manifest(self.directory, inflate)
        with self.assertRaises(FirstPartyCaptureArchiveError) as caught:
            verify_partition_v1(self.directory)
        self.assertIn("count", str(caught.exception))

    def test_a_record_outside_declared_coverage_fails_on_replay(self) -> None:
        def shift(manifest):
            window = manifest["coverage"][0]
            window["start_utc_nanos"] = _reading(1).arrival_utc_nanos  # excludes record 0
            window["record_count"] = 2
            manifest["first_arrival_utc_nanos"] = _reading(1).arrival_utc_nanos

        _rewrite_manifest(self.directory, shift)
        with self.assertRaises(FirstPartyCaptureArchiveError) as caught:
            verify_partition_v1(self.directory)
        self.assertIn("outside_every_declared_coverage_window", str(caught.exception))

    def test_a_gap_overlapping_coverage_is_refused(self) -> None:
        def overlap(manifest):
            manifest["gaps"][0]["start_utc_nanos"] = _reading(1).arrival_utc_nanos

        _rewrite_manifest(self.directory, overlap)
        self.assertIn("declared_gap_overlaps_proven_coverage", self._reasons())

    def test_overlapping_windows_are_refused(self) -> None:
        def overlap(manifest):
            manifest["coverage"][1]["start_utc_nanos"] = _reading(1).arrival_utc_nanos
            manifest["gaps"] = []

        _rewrite_manifest(self.directory, overlap)
        self.assertIn("coverage_windows_overlap_or_are_unordered", self._reasons())

    def test_the_contract_hash_must_be_the_authorized_contract(self) -> None:
        _rewrite_manifest(
            self.directory, lambda m: m.__setitem__("contract_content_hash", "0" * 64)
        )
        reasons = self._reasons()
        self.assertIn("manifest_contract_hash_is_not_the_authorized_contract", reasons)
        self.assertIn("session_and_manifest_disagree:contract_content_hash", reasons)

    def test_session_metadata_must_agree_with_the_manifest(self) -> None:
        session_path = self.directory / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        session["session_id"] = str(uuid4())
        session_path.write_text(json.dumps(session), encoding="utf-8")
        _rewrite_manifest(self.directory, lambda m: None)
        self.assertIn("session_and_manifest_disagree:session_id", self._reasons())

    def test_a_torn_record_line_fails_closed(self) -> None:
        records = self.directory / "records.ndjson"
        records.write_text(records.read_text(encoding="utf-8") + '{"schema_version":', encoding="utf-8")
        _rewrite_manifest(self.directory, lambda m: None)
        with self.assertRaises(FirstPartyCaptureArchiveError):
            verify_partition_v1(self.directory)


class CrashAndRestartAvailabilityTests(unittest.TestCase):
    """Issue 3: a crashed session proves nothing, and the hole it leaves is visible."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def _complete_session(self, indices: list[int]) -> Path:
        session = uuid4()
        writer = CapturePartitionWriterV1(root=self.root, contract=CONTRACT, session_id=session, day=DAY)
        for index in indices:
            writer.append_record(_record(session, index))
        _cover(writer, indices[0], indices[-1], len(indices))
        writer.finalize()
        return writer.directory

    def test_a_crashed_partition_contributes_no_coverage_and_the_hole_is_derived(self) -> None:
        before = self._complete_session([0, 1, 2])
        crashed_session = uuid4()
        crashed = CapturePartitionWriterV1(
            root=self.root, contract=CONTRACT, session_id=crashed_session, day=DAY
        )
        crashed.append_record(_record(crashed_session, 5))
        crashed.append_record(_record(crashed_session, 6))
        crashed.close_without_finalizing()  # a hard crash never gets further
        after = self._complete_session([10, 11])

        availability = derive_archive_availability_v1(self.root)
        self.assertEqual(3, len(find_partitions_v1(self.root)))
        self.assertNotIn(crashed.directory, (before, after))
        self.assertEqual(((crashed.directory, ("partition_has_no_manifest",)),), availability.excluded)
        self.assertEqual(2, len(availability.windows))
        (gap,) = availability.gaps
        self.assertEqual(CaptureGapKindV1.SESSION_BOUNDARY.value, gap.kind)
        self.assertEqual(_reading(2).arrival_utc_nanos + 1, gap.start_utc_nanos)
        self.assertEqual(_reading(10).arrival_utc_nanos, gap.end_utc_nanos)
        # The crashed session's records sit in the hole, claimed by nothing.
        for index in (5, 6):
            arrival = _reading(index).arrival_utc_nanos
            self.assertTrue(gap.start_utc_nanos <= arrival < gap.end_utc_nanos)
            self.assertFalse(any(w.interval.contains(arrival) for w in availability.windows))

    def test_a_crashed_partition_can_never_be_replayed_as_evidence(self) -> None:
        session = uuid4()
        crashed = CapturePartitionWriterV1(root=self.root, contract=CONTRACT, session_id=session, day=DAY)
        crashed.append_record(_record(session, 0))
        crashed.close_without_finalizing()
        self.assertTrue((crashed.directory / OPEN_MARKER_NAME).exists())
        with self.assertRaises(FirstPartyCaptureArchiveError):
            verify_partition_v1(crashed.directory)

    def test_an_empty_archive_proves_nothing(self) -> None:
        availability = derive_archive_availability_v1(self.root)
        self.assertEqual(((), (), ()), (availability.windows, availability.gaps, availability.excluded))


# -- Phase R1A --------------------------------------------------------------


def _measurement_payload(symbol: str) -> str:
    return TICKER_PAYLOAD.replace("BTCUSDT", symbol)


def _complete_partition(
    root: Path,
    *,
    contract: FirstPartyCaptureContractV1 = CONTRACT,
    count: int = 3,
    first: int = 0,
) -> Path:
    """A finalized partition of ``count`` ticker records under ``contract``."""
    session = uuid4()
    writer = CapturePartitionWriterV1(
        root=root, contract=contract, session_id=session, day=DAY, clock_resolution_nanos=15_625_000
    )
    payload = _measurement_payload(contract.exchange_symbol)
    for offset in range(count):
        writer.append_record(
            build_capture_record_v1(
                contract=contract,
                session_id=session,
                sequence=offset,
                clock=_reading(first + offset),
                payload_text=payload,
            )
        )
    _cover(writer, first, first + count - 1, count)
    return writer.finalize().parent


class _RootTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "archive"
        self.addCleanup(self._temp.cleanup)


class WriterByteAccountingTests(_RootTest):
    def test_bytes_written_equal_the_records_file_size(self) -> None:
        session = uuid4()
        writer = CapturePartitionWriterV1(root=self.root, contract=CONTRACT, session_id=session, day=DAY)
        returned = [writer.append_record(_record(session, index)) for index in range(4)]
        self.assertEqual(sum(returned), writer.bytes_written)
        self.assertEqual(writer.bytes_written, open_partition_bytes_v1(writer.directory))
        _cover(writer, 0, 3, 4)
        directory = writer.finalize().parent
        self.assertEqual(writer.bytes_written, (directory / RECORDS_FILE_NAME).stat().st_size)


class DiskBudgetEndProofTests(unittest.TestCase):
    def test_disk_budget_stop_is_a_recognized_end_proof_without_a_declared_gap(self) -> None:
        interval = CaptureCoverageIntervalV1(
            start_utc_nanos=1, last_proven_utc_nanos=2, end_proof=END_PROOF_DISK_BUDGET_STOP,
            record_count=0,
        )
        self.assertEqual(END_PROOF_DISK_BUDGET_STOP, interval.end_proof)
        self.assertIn(END_PROOF_DISK_BUDGET_STOP, END_PROOFS_V1)
        self.assertIn(END_PROOF_DISK_BUDGET_STOP, REQUESTABLE_END_PROOFS_V1)
        self.assertNotIn(END_PROOF_DISK_BUDGET_STOP, GAP_KIND_FOR_END_PROOF_V1)


class CompactionTests(_RootTest):
    def test_compaction_is_lossless_and_leaves_the_manifest_untouched(self) -> None:
        directory = _complete_partition(self.root, count=5)
        manifest_before = (directory / MANIFEST_FILE_NAME).read_bytes()
        records_before = [record.content_hash for record in replay_partition_v1(directory)]
        raw_size = (directory / RECORDS_FILE_NAME).stat().st_size

        result = compact_partition_v1(directory)

        self.assertFalse(result.already_compacted)
        self.assertEqual(raw_size, result.uncompressed_bytes)
        self.assertEqual((directory / COMPACTED_RECORDS_FILE_NAME).stat().st_size, result.compressed_bytes)
        self.assertFalse((directory / RECORDS_FILE_NAME).exists())
        self.assertEqual(manifest_before, (directory / MANIFEST_FILE_NAME).read_bytes())
        partition = read_partition_status_v1(directory)
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
        self.assertEqual(records_before, [r.content_hash for r in replay_partition_v1(directory)])
        self.assertEqual(5, verify_partition_v1(directory).record_count)

    def test_compressed_output_is_deterministic(self) -> None:
        directory = _complete_partition(self.root)
        raw = (directory / RECORDS_FILE_NAME).read_bytes()
        compact_partition_v1(directory)
        packed = (directory / COMPACTED_RECORDS_FILE_NAME).read_bytes()
        self.assertEqual(b"\x00\x00\x00\x00", packed[4:8], "gzip mtime must be zero")
        self.assertEqual(raw, gzip.decompress(packed))

    def test_a_tampered_compressed_file_is_not_complete(self) -> None:
        directory = _complete_partition(self.root)
        compact_partition_v1(directory)
        path = directory / COMPACTED_RECORDS_FILE_NAME
        data = bytearray(path.read_bytes())
        data[-9] ^= 0xFF
        path.write_bytes(bytes(data))
        partition = read_partition_status_v1(directory)
        self.assertEqual(PARTITION_STATUS_PARTIAL, partition.status)
        self.assertIn("compacted_records_checksum_mismatch", partition.reasons)
        with self.assertRaises(FirstPartyCaptureArchiveError):
            list(replay_partition_v1(directory))

    def test_an_edited_compaction_record_is_not_complete(self) -> None:
        directory = _complete_partition(self.root)
        compact_partition_v1(directory)
        path = directory / COMPACTION_FILE_NAME
        record = json.loads(path.read_text(encoding="utf-8"))
        record["uncompressed_bytes"] += 1
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertIn(
            "compaction_content_hash_mismatch", read_partition_status_v1(directory).reasons
        )

    def test_a_compaction_rebound_to_another_manifest_is_refused(self) -> None:
        directory = _complete_partition(self.root)
        compact_partition_v1(directory)
        path = directory / COMPACTION_FILE_NAME
        record = json.loads(path.read_text(encoding="utf-8"))
        record.pop("compaction_content_hash")
        record["manifest_content_hash"] = "0" * 64
        record["compaction_content_hash"] = hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertIn(
            "compaction_is_bound_to_another_manifest", read_partition_status_v1(directory).reasons
        )

    def test_an_interrupted_compaction_is_finished_only_after_proof(self) -> None:
        directory = _complete_partition(self.root)
        raw = (directory / RECORDS_FILE_NAME).read_bytes()
        compact_partition_v1(directory)
        # Simulate a crash after COMPACTION.json was written but before the
        # original was removed: both forms present. The original stays the
        # authority until the compacted form is re-proven.
        (directory / RECORDS_FILE_NAME).write_bytes(raw)
        self.assertEqual(PARTITION_STATUS_COMPLETE, read_partition_status_v1(directory).status)
        result = compact_partition_v1(directory)
        self.assertTrue(result.already_compacted)
        self.assertFalse((directory / RECORDS_FILE_NAME).exists())
        self.assertEqual(PARTITION_STATUS_COMPLETE, read_partition_status_v1(directory).status)

    def test_an_unproven_partition_is_never_compacted(self) -> None:
        session = uuid4()
        writer = CapturePartitionWriterV1(root=self.root, contract=CONTRACT, session_id=session, day=DAY)
        writer.append_record(_record(session, 0))
        writer.close_without_finalizing()
        with self.assertRaises(FirstPartyCaptureArchiveError):
            compact_partition_v1(writer.directory)
        self.assertTrue((writer.directory / RECORDS_FILE_NAME).exists())

    def test_candidates_are_finalized_uncompacted_partitions_only(self) -> None:
        finalized = _complete_partition(self.root)
        compacted = _complete_partition(self.root, first=10)
        compact_partition_v1(compacted)
        session = uuid4()
        open_writer = CapturePartitionWriterV1(
            root=self.root, contract=CONTRACT, session_id=session, day=DAY
        )
        open_writer.append_record(_record(session, 30))
        self.addCleanup(open_writer.close_without_finalizing)
        self.assertEqual((finalized,), compaction_candidates_v1(self.root))


class ContractResolutionTests(_RootTest):
    def test_a_measurement_partition_is_proven_against_its_own_contract(self) -> None:
        contract = first_party_bybit_measurement_contract_v1("ETHUSDT")
        directory = _complete_partition(self.root, contract=contract)
        self.assertEqual(str(contract.source_id), session_source_id_v1(directory))
        partition = read_partition_status_v1(directory)
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
        self.assertEqual(3, verify_partition_v1(directory).record_count)
        # Judged against the production contract it is someone else's evidence.
        forced = read_partition_status_v1(directory, contract=CONTRACT)
        self.assertEqual(PARTITION_STATUS_PARTIAL, forced.status)
        self.assertIn("manifest_source_is_not_the_authorized_first_party_source", forced.reasons)

    def test_an_unknown_source_falls_back_to_production_and_fails(self) -> None:
        directory = _complete_partition(self.root)
        session_path = directory / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        session["source_id"] = str(uuid4())
        session_path.write_text(json.dumps(session), encoding="utf-8")
        self.assertEqual(PARTITION_STATUS_PARTIAL, read_partition_status_v1(directory).status)

    def test_availability_is_never_merged_across_sources(self) -> None:
        production = _complete_partition(self.root, count=2)
        eth = first_party_bybit_measurement_contract_v1("ETHUSDT")
        _complete_partition(self.root, contract=eth, count=4, first=100)
        default = derive_archive_availability_v1(self.root)
        self.assertEqual(1, len(default.windows))
        self.assertEqual((), default.excluded)
        self.assertEqual(2, default.windows[0].interval.record_count)
        self.assertEqual(
            read_partition_status_v1(production).session_id, default.windows[0].session_id
        )
        sources, unattributed = derive_archive_availability_by_source_v1(self.root)
        self.assertEqual((), unattributed)
        self.assertEqual(
            [(CONTRACT.source_id, 2), (eth.source_id, 4)],
            [
                (item.contract.source_id, item.availability.windows[0].interval.record_count)
                for item in sources
            ],
        )

    def test_an_unattributable_partition_is_reported(self) -> None:
        directory = _complete_partition(self.root)
        session_path = directory / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        session["source_id"] = str(uuid4())
        session_path.write_text(json.dumps(session), encoding="utf-8")
        sources, unattributed = derive_archive_availability_by_source_v1(self.root)
        self.assertEqual((), sources)
        self.assertEqual(
            ((directory, ("partition_source_is_not_an_authorized_contract",)),), unattributed
        )


class BackupTests(_RootTest):
    def setUp(self) -> None:
        super().setUp()
        self.destination = Path(self._temp.name) / "backup"

    def test_backup_copies_and_re_verifies(self) -> None:
        directory = _complete_partition(self.root)
        compacted = _complete_partition(self.root, first=20)
        compact_partition_v1(compacted)
        outcomes = backup_archive_v1(self.root, self.destination)
        self.assertEqual({BACKUP_COPIED_AND_VERIFIED}, {outcome.action for outcome in outcomes})
        for source in (directory, compacted):
            target = self.destination / source.relative_to(self.root)
            self.assertEqual(3, verify_partition_v1(target).record_count)
            self.assertFalse(target.with_name(target.name + ".partial").exists())

    def test_a_second_backup_finds_the_partition_already_present(self) -> None:
        _complete_partition(self.root)
        backup_archive_v1(self.root, self.destination)
        (outcome,) = backup_archive_v1(self.root, self.destination)
        self.assertEqual(BACKUP_ALREADY_PRESENT, outcome.action)

    def test_a_different_partition_at_the_destination_is_never_overwritten(self) -> None:
        directory = _complete_partition(self.root)
        (outcome,) = backup_archive_v1(self.root, self.destination)
        manifest = outcome.destination_directory / MANIFEST_FILE_NAME
        altered = json.loads(manifest.read_text(encoding="utf-8"))
        altered["manifest_content_hash"] = "f" * 64
        manifest.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaises(FirstPartyCaptureArchiveError):
            backup_partition_v1(directory, archive_root=self.root, destination_root=self.destination)

    def test_an_open_partition_is_skipped(self) -> None:
        session = uuid4()
        writer = CapturePartitionWriterV1(root=self.root, contract=CONTRACT, session_id=session, day=DAY)
        writer.append_record(_record(session, 0))
        self.addCleanup(writer.close_without_finalizing)
        (outcome,) = backup_archive_v1(self.root, self.destination)
        self.assertEqual(BACKUP_SKIPPED_NOT_COMPLETE, outcome.action)
        self.assertFalse(outcome.destination_directory.exists())

    def test_backing_up_onto_itself_is_refused(self) -> None:
        _complete_partition(self.root)
        with self.assertRaises(FirstPartyCaptureArchiveError):
            backup_archive_v1(self.root, self.root)


if __name__ == "__main__":
    unittest.main()
