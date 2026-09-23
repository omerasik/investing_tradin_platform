"""Phase 3Z.2 -- recorder semantics, proven offline.

The network path is proven by a bounded real smoke capture against the official
public feed; everything that must *not* happen -- coverage opening before
acknowledgement, a reconnect presented as continuous, a clock step absorbed
silently, a rejected message vanishing inside covered time, a crash labelled as
a clean stop -- is proven here with fixtures, because those conditions cannot be
summoned on demand from a live exchange.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from collections.abc import Callable
from datetime import date
from pathlib import Path
from unittest import mock
from uuid import uuid4

from trade_platform import bybit_public_websocket_recorder_v1 as recorder_module
from trade_platform.bybit_public_websocket_recorder_v1 import (
    RECONNECT_BACKOFF_SECONDS_V1,
    BybitPublicCaptureRecorderV1,
    BybitPublicPeerClosedError,
    BybitPublicWebSocketError,
    CaptureClockRegressionError,
    CaptureContractViolationError,
    iter_health_lines,
)
from trade_platform.first_party_capture_archive_v1 import (
    END_PROOF_CLOCK_DISCONTINUITY,
    END_PROOF_CONNECTION_LOST,
    END_PROOF_CONTRACT_VIOLATION,
    END_PROOF_OPERATOR_BOUNDED_STOP,
    END_PROOF_OPERATOR_INTERRUPT,
    END_PROOF_PEER_CLOSED,
    END_PROOF_RECORDER_FAILURE,
    END_PROOF_UTC_DAY_ROLLOVER,
    PARTITION_STATUS_COMPLETE,
    PARTITION_STATUS_PARTIAL,
    CaptureClockReadingV1,
    CaptureGapKindV1,
    CaptureLifecycleKindV1,
    CapturePartitionV1,
    CapturePartitionWriterV1,
    FirstPartyCaptureArchiveError,
    derive_archive_availability_v1,
    find_partitions_v1,
    read_lifecycle_v1,
    read_partition_status_v1,
    replay_partition_v1,
    verify_partition_v1,
)
from trade_platform.first_party_capture_authority_v1 import (
    first_party_bybit_capture_contract_v1,
)

CONTRACT = first_party_bybit_capture_contract_v1()
DAY = date(2026, 9, 23)

TICKER_SNAPSHOT = (
    '{"topic":"tickers.BTCUSDT","type":"snapshot","ts":1790132704184,'
    '"data":{"symbol":"BTCUSDT","markPrice":"63000.50","indexPrice":"63001.00"}}'
)
TICKER_DELTA = (
    '{"topic":"tickers.BTCUSDT","type":"delta","ts":1790132705184,'
    '"data":{"symbol":"BTCUSDT","markPrice":"63010.50"}}'
)
TRADE = (
    '{"topic":"publicTrade.BTCUSDT","type":"snapshot","ts":1790132704999,'
    '"data":[{"s":"BTCUSDT","p":"63000.5","v":"0.010","S":"Buy","i":"abc","T":1790132704990}]}'
)
SUBSCRIBE_ACK = '{"success":true,"op":"subscribe","conn_id":"abc"}'
PONG_REPLY = '{"success":true,"ret_msg":"pong","conn_id":"abc","op":"ping"}'


def _reading(index: int, *, wall_step: int = 1_000_000) -> CaptureClockReadingV1:
    return CaptureClockReadingV1(
        arrival_utc_nanos=1_790_132_695_000_000_000 + index * wall_step,
        arrival_monotonic_nanos=35_552_500_000_000 + index * 1_000_000,
    )


class _Harness:
    """Drive the recorder's record/coverage path without opening a socket."""

    def __init__(self, root: Path) -> None:
        self.recorder = BybitPublicCaptureRecorderV1(archive_root=root, contract=CONTRACT)
        self.recorder._session_id = uuid4()
        self.session_id = self.recorder._session_id

    def acknowledge(self, reading: CaptureClockReadingV1) -> None:
        pending = set(CONTRACT.topics())
        self.recorder._handle_subscription_reply(SUBSCRIBE_ACK, pending, reading)

    def record(self, payload: str, reading: CaptureClockReadingV1) -> None:
        self.recorder._record(payload, reading)

    def lose_connection(self, detail: str = "socket closed") -> None:
        self.recorder._close_coverage(END_PROOF_CONNECTION_LOST, detail)
        self.recorder._clock.reset()

    def finish(self, end_proof: str = END_PROOF_OPERATOR_BOUNDED_STOP) -> Path | None:
        """End the session the way run() does, without its real-clock lifecycle."""
        recorder = self.recorder
        recorder._close_coverage(end_proof)
        writer = recorder._writer
        if writer is None:
            return None
        directory = writer.directory
        recorder._finalize_writer()
        return directory

    def close(self) -> None:
        """Release handles on a partition a test deliberately left unfinished."""
        writer = self.recorder._writer
        if writer is not None:
            writer.close_without_finalizing()


class _TempRootTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)


class SubscriptionGateTests(_TempRootTest):
    def test_coverage_opens_only_after_acknowledgement(self) -> None:
        harness = _Harness(self.root)
        self.assertFalse(harness.recorder.health().subscriptions_acknowledged)
        self.assertFalse(harness.recorder.health().coverage_open)
        harness.acknowledge(_reading(0))
        health = harness.recorder.health()
        self.assertTrue(health.subscriptions_acknowledged)
        self.assertTrue(health.coverage_open)
        harness.finish()

    def test_a_rejected_subscription_is_refused(self) -> None:
        harness = _Harness(self.root)
        with self.assertRaises(BybitPublicWebSocketError):
            harness.recorder._handle_subscription_reply(
                '{"success":false,"op":"subscribe","ret_msg":"bad topic"}',
                set(CONTRACT.topics()),
                _reading(0),
            )

    def test_data_before_the_acknowledgement_stays_inside_coverage(self) -> None:
        """Bybit may push a snapshot before its subscribe ack; nothing may fall outside."""
        harness = _Harness(self.root)
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.record(TICKER_SNAPSHOT, _reading(2))
        harness.acknowledge(_reading(3))
        harness.record(TICKER_SNAPSHOT, _reading(4))
        directory = harness.finish()
        assert directory is not None
        partition = read_partition_status_v1(directory)
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
        self.assertEqual(1, len(partition.coverage))
        self.assertEqual(partition.record_count, partition.coverage[0].record_count)
        self.assertEqual(3, verify_partition_v1(directory).record_count)

    def test_acknowledgement_is_recorded_in_the_lifecycle(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        directory = harness.finish()
        assert directory is not None
        kinds = [event.kind for event in read_lifecycle_v1(directory)]
        self.assertIn("SUBSCRIPTIONS_ACKNOWLEDGED", kinds)


class CoverageBoundaryTests(_TempRootTest):
    """Issue 1: every covered record lies inside its half-open window."""

    def _covered(self, directory: Path) -> tuple[CapturePartitionV1, list[int]]:
        partition = read_partition_status_v1(directory)
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
        arrivals = [record.arrival_utc_nanos for record in replay_partition_v1(directory)]
        return partition, arrivals

    def test_first_and_last_records_belong_to_coverage(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.record(TRADE, _reading(2))
        harness.record(TICKER_DELTA, _reading(3))
        directory = harness.finish()
        assert directory is not None
        partition, arrivals = self._covered(directory)
        window = partition.coverage[0]
        self.assertTrue(window.contains(arrivals[0]))
        self.assertTrue(window.contains(arrivals[-1]))
        # The exclusive end is representational: last proven + 1, never a guess.
        self.assertEqual(arrivals[-1], window.last_proven_utc_nanos)
        self.assertEqual(arrivals[-1] + 1, window.end_utc_nanos)
        self.assertFalse(window.contains(window.end_utc_nanos))

    def test_a_record_before_acknowledgement_is_the_inclusive_start(self) -> None:
        harness = _Harness(self.root)
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.acknowledge(_reading(2))
        directory = harness.finish()
        assert directory is not None
        partition, arrivals = self._covered(directory)
        self.assertEqual(arrivals[0], partition.coverage[0].start_utc_nanos)
        self.assertTrue(partition.coverage[0].contains(arrivals[0]))
        # The later acknowledgement is itself a proof and extends the window.
        self.assertEqual(_reading(2).arrival_utc_nanos, partition.coverage[0].last_proven_utc_nanos)

    def test_every_record_is_inside_exactly_one_declared_window(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.record(TICKER_DELTA, _reading(2))
        harness.lose_connection()
        harness.acknowledge(_reading(10))
        harness.record(TICKER_SNAPSHOT, _reading(11))
        harness.record(TRADE, _reading(12))
        directory = harness.finish()
        assert directory is not None
        partition, arrivals = self._covered(directory)
        for arrival in arrivals:
            owners = [window for window in partition.coverage if window.contains(arrival)]
            self.assertEqual(1, len(owners))
        self.assertEqual(
            [2, 2], [window.record_count for window in partition.coverage]
        )

    def test_a_gap_starts_strictly_outside_proven_coverage(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.lose_connection()
        harness.acknowledge(_reading(10))
        harness.record(TICKER_SNAPSHOT, _reading(11))
        directory = harness.finish()
        assert directory is not None
        partition, arrivals = self._covered(directory)
        (gap,) = partition.gaps
        before, after = partition.coverage
        self.assertEqual(CaptureGapKindV1.CONNECTION_LOSS.value, gap.kind)
        self.assertEqual(before.end_utc_nanos, gap.start_utc_nanos)
        self.assertFalse(before.contains(gap.start_utc_nanos))
        self.assertEqual(after.start_utc_nanos, gap.end_utc_nanos)
        for arrival in arrivals:
            self.assertFalse(gap.start_utc_nanos <= arrival < (gap.end_utc_nanos or arrival + 1))

    def test_an_acknowledged_window_with_no_records_still_closes(self) -> None:
        """A reconnect that proves only its acknowledgement must not break the close."""
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.lose_connection()
        harness.acknowledge(_reading(10))
        directory = harness.finish()
        assert directory is not None
        partition = read_partition_status_v1(directory)
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
        last = partition.coverage[-1]
        self.assertEqual(0, last.record_count)
        self.assertEqual(_reading(10).arrival_utc_nanos, last.start_utc_nanos)
        self.assertEqual(_reading(10).arrival_utc_nanos, last.last_proven_utc_nanos)

    def test_a_window_cannot_restart_inside_a_closed_one(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(5))
        harness.lose_connection()
        with self.assertRaises(CaptureClockRegressionError):
            harness.acknowledge(_reading(5))  # same wall tick as the last proof
        harness.close()


class ClockAndGapTests(_TempRootTest):
    def test_a_suspend_style_step_closes_coverage_and_opens_a_gap(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        # Wall clock leaps ten minutes while the monotonic counter barely moves.
        suspended = CaptureClockReadingV1(
            arrival_utc_nanos=_reading(1).arrival_utc_nanos + 600_000_000_000,
            arrival_monotonic_nanos=_reading(2).arrival_monotonic_nanos,
        )
        harness.record(TICKER_SNAPSHOT, suspended)
        directory = harness.finish()
        assert directory is not None
        partition = read_partition_status_v1(directory)
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
        self.assertEqual(1, harness.recorder.health().clock_discontinuities)
        (gap,) = partition.gaps
        self.assertEqual(CaptureGapKindV1.CLOCK_DISCONTINUITY.value, gap.kind)
        self.assertEqual(END_PROOF_CLOCK_DISCONTINUITY, partition.coverage[0].end_proof)
        self.assertEqual(suspended.arrival_utc_nanos, gap.end_utc_nanos)
        self.assertEqual(2, verify_partition_v1(directory).record_count)

    def test_a_wall_clock_regression_fails_closed(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(5))
        regressed = CaptureClockReadingV1(
            arrival_utc_nanos=_reading(5).arrival_utc_nanos - 10_000_000,
            arrival_monotonic_nanos=_reading(6).arrival_monotonic_nanos,
        )
        with self.assertRaises(CaptureClockRegressionError):
            harness.record(TICKER_SNAPSHOT, regressed)
        self.assertEqual(1, harness.recorder.health().clock_discontinuities)
        self.assertFalse(harness.recorder.health().coverage_open)
        harness.close()

    def test_quiet_coverage_is_not_a_gap(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.record(TICKER_SNAPSHOT, _reading(2))
        directory = harness.finish()
        assert directory is not None
        partition = read_partition_status_v1(directory)
        self.assertEqual((), partition.gaps)
        self.assertEqual(1, len(partition.coverage))
        self.assertEqual(END_PROOF_OPERATOR_BOUNDED_STOP, partition.coverage[0].end_proof)


class MessageClassificationTests(_TempRootTest):
    """Issue 2: nothing the contract refuses disappears inside covered time."""

    def _violates(self, harness: _Harness, payload: str, reading: CaptureClockReadingV1) -> str:
        with self.assertRaises(CaptureContractViolationError) as caught:
            harness.record(payload, reading)
        self.assertFalse(harness.recorder.health().coverage_open)
        return str(caught.exception)

    def _assert_rejections_are_uncovered(self, directory: Path, expected: int) -> None:
        partition = read_partition_status_v1(directory)
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
        rejected = [
            event
            for event in read_lifecycle_v1(directory)
            if event.kind == CaptureLifecycleKindV1.MESSAGE_REJECTED.value
        ]
        self.assertEqual(expected, len(rejected))
        for event in rejected:
            self.assertIsNotNone(event.payload_text, "a rejection keeps its verbatim text")
            for window in partition.coverage:
                self.assertFalse(window.contains(event.arrival_utc_nanos))
        verify_partition_v1(directory)

    def test_unrelated_control_frames_are_ignored_without_touching_coverage(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.record(PONG_REPLY, _reading(2))
        harness.record('{"op":"pong","success":true}', _reading(3))
        harness.record(SUBSCRIBE_ACK, _reading(4))
        harness.record(TICKER_DELTA, _reading(5))
        self.assertTrue(harness.recorder.health().coverage_open)
        self.assertEqual(0, harness.recorder.health().contract_violations)
        directory = harness.finish()
        assert directory is not None
        partition = read_partition_status_v1(directory)
        self.assertEqual(2, partition.record_count)
        self.assertEqual(1, len(partition.coverage))
        self.assertEqual((), partition.gaps)

    def test_malformed_json_is_a_violation_not_noise(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        reason = self._violates(harness, '{"topic":"tickers.BTCUSDT",', _reading(2))
        self.assertIn("not_valid_json", reason)
        directory = harness.finish()
        assert directory is not None
        partition = read_partition_status_v1(directory)
        self.assertEqual(END_PROOF_CONTRACT_VIOLATION, partition.coverage[0].end_proof)
        self.assertEqual(CaptureGapKindV1.CONTRACT_VIOLATION.value, partition.gaps[0].kind)
        self._assert_rejections_are_uncovered(directory, 1)

    def test_unknown_or_failed_control_frames_are_violations(self) -> None:
        for payload in (
            '{"op":"auth","success":true}',
            '{"op":"ping","success":false,"ret_msg":"error"}',
            '{"hello":"world"}',
            "[1,2,3]",
        ):
            with self.subTest(payload=payload):
                harness = _Harness(Path(tempfile.mkdtemp(dir=self.root)))
                harness.acknowledge(_reading(0))
                self._violates(harness, payload, _reading(1))
                harness.close()

    def test_authorized_topic_with_malformed_schema_is_a_violation(self) -> None:
        cases = {
            "missing_data": '{"topic":"tickers.BTCUSDT","type":"delta","ts":1}',
            "ticker_data_not_object": '{"topic":"tickers.BTCUSDT","type":"delta","ts":1,"data":[]}',
            "trade_data_empty": '{"topic":"publicTrade.BTCUSDT","type":"snapshot","ts":1,"data":[]}',
            "trade_entry_not_object": (
                '{"topic":"publicTrade.BTCUSDT","type":"snapshot","ts":1,"data":["x"]}'
            ),
            "missing_symbol": (
                '{"topic":"tickers.BTCUSDT","type":"delta","ts":1,"data":{"markPrice":"1"}}'
            ),
            "missing_ts": TICKER_DELTA.replace('"ts":1790132705184,', ""),
            "string_ts": TICKER_DELTA.replace('"ts":1790132705184', '"ts":"1790132705184"'),
            "boolean_ts": TICKER_DELTA.replace('"ts":1790132705184', '"ts":true'),
            "generated": TICKER_SNAPSHOT[:-1] + ',"generated":true}',
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                harness = _Harness(Path(tempfile.mkdtemp(dir=self.root)))
                harness.acknowledge(_reading(0))
                harness.record(TICKER_SNAPSHOT, _reading(1))
                self._violates(harness, payload, _reading(2))
                directory = harness.finish()
                assert directory is not None
                self._assert_rejections_are_uncovered(directory, 1)
                self.assertEqual(1, read_partition_status_v1(directory).record_count)

    def test_authorized_topic_with_wrong_symbol_is_a_violation(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        reason = self._violates(
            harness, TRADE.replace('"s":"BTCUSDT"', '"s":"ETHUSDT"'), _reading(1)
        )
        self.assertIn("disagrees_with_topic", reason)
        directory = harness.finish()
        assert directory is not None
        self._assert_rejections_are_uncovered(directory, 1)

    def test_unknown_or_missing_message_type_is_a_violation(self) -> None:
        for payload in (
            TICKER_DELTA.replace('"type":"delta"', '"type":"patch"'),
            TICKER_DELTA.replace('"type":"delta",', ""),
        ):
            with self.subTest(payload=payload):
                harness = _Harness(Path(tempfile.mkdtemp(dir=self.root)))
                harness.acknowledge(_reading(0))
                self._violates(harness, payload, _reading(1))
                harness.close()

    def test_a_foreign_topic_is_a_violation_not_a_silent_drop(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        self._violates(
            harness, '{"topic":"orderbook.50.BTCUSDT","type":"snapshot","ts":1,"data":{}}', _reading(1)
        )
        self.assertEqual(
            {"captured_topic_is_not_authorized_by_the_contract": 1},
            dict(harness.recorder.health().rejection_reasons),
        )
        harness.close()

    def test_coverage_resumes_only_in_a_new_window_after_a_violation(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        self._violates(harness, "garbage", _reading(2))
        harness.recorder._clock.reset()
        harness.acknowledge(_reading(10))
        harness.record(TICKER_SNAPSHOT, _reading(11))
        directory = harness.finish()
        assert directory is not None
        partition = read_partition_status_v1(directory)
        self.assertEqual(2, len(partition.coverage))
        (gap,) = partition.gaps
        self.assertEqual(partition.coverage[0].end_utc_nanos, gap.start_utc_nanos)
        self.assertEqual(partition.coverage[1].start_utc_nanos, gap.end_utc_nanos)
        self._assert_rejections_are_uncovered(directory, 1)


class ReconnectTests(_TempRootTest):
    def test_ticker_state_cannot_silently_cross_a_reconnect(self) -> None:
        """A connection loss closes coverage and leaves a gap before the next window."""
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.record(TICKER_DELTA, _reading(2))
        harness.lose_connection()
        # After reconnect the exchange re-sends a snapshot, which the existing
        # reconstruction treats as an authoritative state reset.
        harness.acknowledge(_reading(10))
        harness.record(TICKER_SNAPSHOT, _reading(11))
        directory = harness.finish()
        assert directory is not None

        partition = read_partition_status_v1(directory)
        self.assertEqual(2, len(partition.coverage))
        self.assertEqual(END_PROOF_CONNECTION_LOST, partition.coverage[0].end_proof)
        self.assertIn(
            CaptureGapKindV1.CONNECTION_LOSS.value, {gap.kind for gap in partition.gaps}
        )
        replayed = list(replay_partition_v1(directory))
        self.assertEqual("snapshot", replayed[-1].message_type)

    def test_utc_midnight_rolls_the_partition_without_claiming_the_hole(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        next_day = CaptureClockReadingV1(
            arrival_utc_nanos=_reading(1).arrival_utc_nanos + 86_400 * 1_000_000_000,
            arrival_monotonic_nanos=_reading(2).arrival_monotonic_nanos + 86_400 * 1_000_000_000,
        )
        harness.record(TICKER_DELTA, next_day)
        harness.finish()
        first, second = find_partitions_v1(self.root)
        day_one, day_two = read_partition_status_v1(first), read_partition_status_v1(second)
        self.assertEqual(PARTITION_STATUS_COMPLETE, day_one.status, day_one.reasons)
        self.assertEqual(PARTITION_STATUS_COMPLETE, day_two.status, day_two.reasons)
        self.assertEqual(END_PROOF_UTC_DAY_ROLLOVER, day_one.coverage[-1].end_proof)
        self.assertEqual(next_day.arrival_utc_nanos, day_two.coverage[0].start_utc_nanos)
        verify_partition_v1(first)
        verify_partition_v1(second)
        (gap,) = derive_archive_availability_v1(self.root).gaps
        self.assertEqual(CaptureGapKindV1.PARTITION_ROLLOVER.value, gap.kind)

    def test_a_gap_pending_at_midnight_ends_open_in_the_old_partition(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.lose_connection()
        next_day = CaptureClockReadingV1(
            arrival_utc_nanos=_reading(1).arrival_utc_nanos + 86_400 * 1_000_000_000,
            arrival_monotonic_nanos=_reading(2).arrival_monotonic_nanos,
        )
        harness.acknowledge(next_day)
        harness.finish()
        first, second = find_partitions_v1(self.root)
        (open_gap,) = read_partition_status_v1(first).gaps
        self.assertIsNone(open_gap.end_utc_nanos)
        self.assertEqual((), read_partition_status_v1(second).gaps)
        (derived,) = derive_archive_availability_v1(self.root).gaps
        self.assertEqual(CaptureGapKindV1.CONNECTION_LOSS.value, derived.kind)
        self.assertEqual(next_day.arrival_utc_nanos, derived.end_utc_nanos)

    def test_backoff_is_bounded(self) -> None:
        self.assertEqual(
            tuple(sorted(RECONNECT_BACKOFF_SECONDS_V1)), RECONNECT_BACKOFF_SECONDS_V1
        )
        self.assertLessEqual(max(RECONNECT_BACKOFF_SECONDS_V1), 60.0)

    def test_a_restart_is_a_new_session_never_a_continuation(self) -> None:
        first = _Harness(self.root)
        first.acknowledge(_reading(0))
        first.record(TICKER_SNAPSHOT, _reading(1))
        first_dir = first.finish()

        second = _Harness(self.root)
        second.acknowledge(_reading(100))
        second.record(TICKER_SNAPSHOT, _reading(101))
        second_dir = second.finish()

        self.assertNotEqual(first.session_id, second.session_id)
        self.assertNotEqual(first_dir, second_dir)
        self.assertEqual(2, len(find_partitions_v1(self.root)))


_REAL_SLEEP: Callable[[float], None] = time.sleep


class SessionEndProofTests(_TempRootTest):
    """Issue 4: how a session ended is what happened, never what ``finally`` implies."""

    def _recorder(self) -> BybitPublicCaptureRecorderV1:
        return BybitPublicCaptureRecorderV1(archive_root=self.root, contract=CONTRACT)

    @staticmethod
    def _one_connection(
        recorder: BybitPublicCaptureRecorderV1, records: int, then: BaseException | None
    ) -> None:
        pending = set(CONTRACT.topics())
        recorder._connected = True
        recorder._handle_subscription_reply(SUBSCRIBE_ACK, pending, CaptureClockReadingV1.now())
        for _ in range(records):
            recorder._record(TICKER_SNAPSHOT, CaptureClockReadingV1.now())
        recorder._connected = False
        if then is not None:
            raise then

    def _only_partition(self) -> tuple[Path, CapturePartitionV1]:
        (directory,) = find_partitions_v1(self.root)
        return directory, read_partition_status_v1(directory)

    def test_reaching_a_bound_is_an_operator_bounded_stop(self) -> None:
        recorder = self._recorder()
        with mock.patch.object(
            recorder, "_run_one_connection",
            side_effect=lambda **_: self._one_connection(recorder, 3, None),
        ):
            health = recorder.run(max_records=3)
        self.assertEqual(END_PROOF_OPERATOR_BOUNDED_STOP, health.session_end_proof)
        directory, partition = self._only_partition()
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
        self.assertEqual(END_PROOF_OPERATOR_BOUNDED_STOP, partition.coverage[-1].end_proof)
        self.assertEqual((), partition.gaps)
        self.assertEqual(3, verify_partition_v1(directory).record_count)

    def test_ctrl_c_is_an_operator_interrupt_not_a_bounded_stop(self) -> None:
        recorder = self._recorder()
        with mock.patch.object(
            recorder, "_run_one_connection",
            side_effect=lambda **_: self._one_connection(recorder, 2, KeyboardInterrupt()),
        ), self.assertRaises(KeyboardInterrupt):
            recorder.run()
        _, partition = self._only_partition()
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
        self.assertEqual(END_PROOF_OPERATOR_INTERRUPT, partition.coverage[-1].end_proof)
        self.assertEqual((), partition.gaps)

    def test_an_unexpected_failure_is_never_labelled_a_clean_stop(self) -> None:
        recorder = self._recorder()
        with mock.patch.object(
            recorder, "_run_one_connection",
            side_effect=lambda **_: self._one_connection(recorder, 2, RuntimeError("boom")),
        ), self.assertRaises(RuntimeError):
            recorder.run()
        directory, partition = self._only_partition()
        self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
        window = partition.coverage[-1]
        self.assertEqual(END_PROOF_RECORDER_FAILURE, window.end_proof)
        (gap,) = partition.gaps
        self.assertEqual(CaptureGapKindV1.RECORDER_FAILURE.value, gap.kind)
        self.assertEqual(window.end_utc_nanos, gap.start_utc_nanos)
        self.assertIsNone(gap.end_utc_nanos, "nothing after a failure is proven")
        kinds = [event.kind for event in read_lifecycle_v1(directory)]
        self.assertIn(CaptureLifecycleKindV1.RECORDER_FAILED.value, kinds)
        self.assertEqual(2, verify_partition_v1(directory).record_count)

    def test_peer_close_and_network_loss_are_distinct_interruptions(self) -> None:
        for error, proof in (
            (BybitPublicPeerClosedError("close frame"), END_PROOF_PEER_CLOSED),
            (OSError("reset"), END_PROOF_CONNECTION_LOST),
        ):
            with self.subTest(proof=proof):
                root = Path(tempfile.mkdtemp(dir=self.root))
                recorder = BybitPublicCaptureRecorderV1(archive_root=root, contract=CONTRACT)
                calls = iter([error, None])
                with mock.patch.object(
                    recorder, "_run_one_connection",
                    side_effect=lambda _r=recorder, _c=calls, **_: self._one_connection(
                        _r, 1, next(_c)
                    ),
                ), mock.patch.object(
                    # Skip the real backoff, but let the wall clock move on so
                    # the next window provably starts after the last one.
                    recorder_module.time, "sleep", side_effect=lambda _: _REAL_SLEEP(0.05)
                ):
                    recorder.run(max_records=2)
                (directory,) = find_partitions_v1(root)
                partition = read_partition_status_v1(directory)
                self.assertEqual(PARTITION_STATUS_COMPLETE, partition.status, partition.reasons)
                self.assertEqual(
                    [proof, END_PROOF_OPERATOR_BOUNDED_STOP],
                    [window.end_proof for window in partition.coverage],
                )
                self.assertEqual(CaptureGapKindV1.CONNECTION_LOSS.value, partition.gaps[0].kind)

    def test_a_failure_that_breaks_finalization_leaves_the_partition_partial(self) -> None:
        recorder = self._recorder()
        with mock.patch.object(
            recorder, "_run_one_connection",
            side_effect=lambda **_: self._one_connection(recorder, 1, RuntimeError("disk")),
        ), mock.patch.object(
            CapturePartitionWriterV1, "finalize",
            side_effect=FirstPartyCaptureArchiveError("cannot finalize"),
        ), self.assertRaises(RuntimeError):
            recorder.run()
        _, partition = self._only_partition()
        self.assertEqual(PARTITION_STATUS_PARTIAL, partition.status)
        self.assertEqual((), partition.coverage)


class HealthReportingTests(_TempRootTest):
    def test_health_lines_describe_every_partition(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.finish()
        lines = list(iter_health_lines(self.root))
        self.assertEqual(1, len(lines))
        self.assertIn(PARTITION_STATUS_COMPLETE, lines[0])
        self.assertIn("records=1", lines[0])

    def test_health_summary_states_whether_capture_is_valid_now(self) -> None:
        harness = _Harness(self.root)
        self.assertIn("NOT_COVERING", harness.recorder.health().summary())
        harness.acknowledge(_reading(0))
        self.assertIn("NOT_COVERING", harness.recorder.health().summary())  # not connected
        harness.finish()

    def test_health_reports_rejections_by_reason(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        with self.assertRaises(CaptureContractViolationError):
            harness.record("garbage", _reading(1))
        summary = harness.recorder.health().summary()
        self.assertIn("contract_violations=1", summary)
        self.assertIn("unidentified_message_is_not_valid_json=1", summary)
        harness.close()

    def test_an_empty_root_reports_nothing_rather_than_guessing(self) -> None:
        self.assertEqual([], list(iter_health_lines(self.root)))


class ArchiveIsolationTests(unittest.TestCase):
    def test_default_root_is_outside_the_working_tree(self) -> None:
        from trade_platform.first_party_capture_archive_v1 import default_archive_root

        root = default_archive_root()
        self.assertNotIn(
            "trade_investing_panel", str(root), "raw capture must never land inside the repo"
        )

    def test_writer_never_touches_postgres(self) -> None:
        """The archive is filesystem-only: no DSN, no cursor, no migration."""
        import inspect

        from trade_platform import first_party_capture_archive_v1 as module

        source = inspect.getsource(module)
        for forbidden in ("psycopg", "PostgresDatabase", "INSERT INTO", "cursor"):
            self.assertNotIn(forbidden, source)

    def test_partition_writer_is_append_only_per_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session = uuid4()
            writer = CapturePartitionWriterV1(
                root=root, contract=CONTRACT, session_id=session, day=DAY
            )
            writer.finalize()
            with self.assertRaises(FirstPartyCaptureArchiveError):
                CapturePartitionWriterV1(
                    root=root, contract=CONTRACT, session_id=session, day=DAY
                )


if __name__ == "__main__":
    unittest.main()
