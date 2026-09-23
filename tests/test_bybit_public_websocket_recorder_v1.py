"""Phase 3Z.2 -- recorder semantics, proven offline.

The network path is proven by a bounded real smoke capture against the official
public feed; everything that must *not* happen -- coverage opening before
acknowledgement, a reconnect presented as continuous, a clock step absorbed
silently, a restart looking like one session -- is proven here with fixtures,
because those conditions cannot be summoned on demand from a live exchange.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from uuid import uuid4

from trade_platform.bybit_public_websocket_recorder_v1 import (
    RECONNECT_BACKOFF_SECONDS_V1,
    BybitPublicCaptureRecorderV1,
    BybitPublicWebSocketError,
    iter_health_lines,
)
from trade_platform.first_party_capture_archive_v1 import (
    END_PROOF_CLEAN_CLOSE,
    END_PROOF_CLOCK_DISCONTINUITY,
    END_PROOF_CONNECTION_LOST,
    PARTITION_STATUS_COMPLETE,
    CaptureClockReadingV1,
    CaptureGapKindV1,
    CapturePartitionWriterV1,
    FirstPartyCaptureArchiveError,
    find_partitions_v1,
    read_lifecycle_v1,
    read_partition_status_v1,
    replay_partition_v1,
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
SUBSCRIBE_ACK = '{"success":true,"op":"subscribe","conn_id":"abc"}'


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

    def finish(self) -> Path | None:
        recorder = self.recorder
        recorder._close_coverage(END_PROOF_CLEAN_CLOSE)
        writer = recorder._writer
        if writer is None:
            return None
        writer.finalize()
        return writer.directory

    def close(self) -> None:
        """Release handles on a partition a test deliberately left unfinished."""
        writer = self.recorder._writer
        if writer is not None:
            writer.close_without_finalizing()


class SubscriptionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

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
        self.assertEqual(1, len(partition.coverage))
        self.assertEqual(partition.record_count, partition.coverage[0].record_count)
        self.assertLessEqual(
            partition.coverage[0].start_utc_nanos, _reading(1).arrival_utc_nanos
        )

    def test_acknowledgement_is_recorded_in_the_lifecycle(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        directory = harness.finish()
        assert directory is not None
        kinds = [event.kind for event in read_lifecycle_v1(directory)]
        self.assertIn("SUBSCRIPTIONS_ACKNOWLEDGED", kinds)


class ClockAndGapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

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
        self.assertEqual(1, harness.recorder.health().clock_discontinuities)
        self.assertGreaterEqual(len(partition.gaps), 1)
        self.assertIn(
            CaptureGapKindV1.CLOCK_DISCONTINUITY.value, {gap.kind for gap in partition.gaps}
        )
        self.assertIn(
            END_PROOF_CLOCK_DISCONTINUITY, {item.end_proof for item in partition.coverage}
        )

    def test_a_wall_clock_regression_fails_closed(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(5))
        regressed = CaptureClockReadingV1(
            arrival_utc_nanos=_reading(5).arrival_utc_nanos - 10_000_000,
            arrival_monotonic_nanos=_reading(6).arrival_monotonic_nanos,
        )
        with self.assertRaises(BybitPublicWebSocketError):
            harness.record(TICKER_SNAPSHOT, regressed)
        self.assertEqual(1, harness.recorder.health().clock_discontinuities)
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
        self.assertEqual(END_PROOF_CLEAN_CLOSE, partition.coverage[0].end_proof)


class ReconnectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_ticker_state_cannot_silently_cross_a_reconnect(self) -> None:
        """A connection loss closes coverage and leaves a gap before the next window."""
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT, _reading(1))
        harness.record(TICKER_DELTA, _reading(2))

        recorder = harness.recorder
        recorder._close_coverage(END_PROOF_CONNECTION_LOST, "socket closed")
        recorder._clock.reset()
        # After reconnect the exchange re-sends a snapshot, which the existing
        # reconstruction treats as an authoritative state reset.
        harness.acknowledge(_reading(10))
        harness.record(TICKER_SNAPSHOT, _reading(11))
        directory = harness.finish()
        assert directory is not None

        partition = read_partition_status_v1(directory)
        self.assertEqual(2, len(partition.coverage))
        self.assertIn(END_PROOF_CONNECTION_LOST, {i.end_proof for i in partition.coverage})
        self.assertIn(
            CaptureGapKindV1.CONNECTION_LOSS.value, {gap.kind for gap in partition.gaps}
        )
        replayed = list(replay_partition_v1(directory))
        self.assertEqual("snapshot", replayed[-1].message_type)

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


class RecorderRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_unauthorized_payloads_are_not_written(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record('{"topic":"orderbook.50.BTCUSDT","type":"snapshot","data":{}}', _reading(1))
        harness.record('{"op":"pong","success":true}', _reading(2))
        harness.record(TICKER_SNAPSHOT, _reading(3))
        directory = harness.finish()
        assert directory is not None
        self.assertEqual(1, read_partition_status_v1(directory).record_count)

    def test_a_generated_record_cannot_masquerade_as_capture(self) -> None:
        harness = _Harness(self.root)
        harness.acknowledge(_reading(0))
        harness.record(TICKER_SNAPSHOT[:-1] + ',"generated":true}', _reading(1))
        directory = harness.finish()
        assert directory is not None
        self.assertEqual(0, read_partition_status_v1(directory).record_count)


class HealthReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

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
