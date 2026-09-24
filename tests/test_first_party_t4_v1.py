"""Phase R3A -- first-party T4 normalization, sealing, provenance, tier and V3 features.

Every record here is a FIXTURE written under a temporary archive root: a
synthetic host running exactly 10 s behind the venue, RTT-bounded clock samples
every 60 s, a ticker snapshot/delta stream and a public-trade stream. Nothing is
read from or written to the real capture archive or the real research data root.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from trade_platform.first_party_capture_archive_v1 import (
    END_PROOF_CONNECTION_LOST,
    END_PROOF_OPERATOR_BOUNDED_STOP,
    CaptureClockReadingV1,
    CaptureCoverageIntervalV1,
    CaptureGapV1,
    CaptureLifecycleEventV1,
    CaptureLifecycleKindV1,
    CapturePartitionWriterV1,
    build_capture_record_v1,
    nanos_to_datetime,
)
from trade_platform.first_party_capture_authority_v1 import (
    first_party_bybit_capture_contract_v1,
    first_party_bybit_measurement_contract_v1,
)
from trade_platform.first_party_t4_normalization_v1 import (
    ArrivalClockBoundEvidenceV1,
    SessionClockEvidenceV1,
    T4NormalizationError,
    T4SegmentNormalizerV1,
    market_knowledge_micros_v1,
    parse_clock_offset_sample_v1,
)

CONTRACT = first_party_bybit_capture_contract_v1()
DAY = date(2026, 9, 20)
RESOLUTION = 15_625_000
OFFSET = 10_000_000_000  # venue - host, fixture only
RTT = 100_000_000
BASE = (datetime(2026, 9, 20, 0, 10, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(
    microseconds=1
) * 1_000
SECOND = 1_000_000_000


def _sample_event(host_send: int) -> CaptureLifecycleEventV1:
    receive = host_send + RTT
    server = (host_send + receive) // 2 + OFFSET
    detail = {
        "host_clock_resolution_nanos": RESOLUTION,
        "host_receive_utc_nanos": receive,
        "host_send_utc_nanos": host_send,
        "offset_bound_nanos": RTT // 2 + RESOLUTION,
        "offset_estimate_nanos": server - (host_send + receive) // 2,
        "server_utc_nanos": server,
    }
    return CaptureLifecycleEventV1(
        kind=CaptureLifecycleKindV1.CLOCK_OFFSET_SAMPLE.value,
        arrival_utc_nanos=receive + 1_000_000,
        arrival_monotonic_nanos=receive + 1_000_000 - BASE + SECOND,
        detail=json.dumps(detail, sort_keys=True, separators=(",", ":")),
    )


def _venue_ms(host: int, lag_ms: int) -> int:
    return (host + OFFSET) // 1_000_000 - lag_ms


def _ticker(host: int, kind: str, **fields: str) -> str:
    return json.dumps(
        {
            "topic": "tickers.BTCUSDT",
            "type": kind,
            "data": {"symbol": "BTCUSDT", **fields},
            "cs": host,
            "ts": _venue_ms(host, 50),
        },
        separators=(",", ":"),
    )


def _trade(host: int, seq: int, price: str, *, count: int = 1) -> str:
    ts = _venue_ms(host, 50)
    return json.dumps(
        {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "ts": ts,
            "data": [
                {
                    "T": ts - 10, "s": "BTCUSDT", "S": "Buy" if seq % 2 else "Sell",
                    "v": "0.010", "p": price, "L": "ZeroPlusTick",
                    "i": str(UUID(int=seq * 100 + index)), "BT": False, "RPI": False, "seq": seq,
                }
                for index in range(count)
            ],
        },
        separators=(",", ":"),
    )


class FixtureArchive:
    """Writes COMPLETE (or crashed) partitions under a temporary root."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def session(
        self,
        *,
        windows: list[tuple[int, int]],
        samples: list[int],
        crash: bool = False,
        day: date = DAY,
        session_id: UUID | None = None,
        contract: Any = CONTRACT,
        tamper: Any = None,
        end_proofs: list[str] | None = None,
        started: int | None = None,
    ) -> UUID:
        """One partition: records every 0.5 s host time inside each window."""
        session_id = uuid4() if session_id is None else session_id
        writer = CapturePartitionWriterV1(
            root=self.root, contract=contract, session_id=session_id, day=day,
            clock_resolution_nanos=RESOLUTION,
        )
        if started is not None:
            writer.append_lifecycle(CaptureLifecycleEventV1(
                kind=CaptureLifecycleKindV1.SESSION_STARTED.value, arrival_utc_nanos=started,
                arrival_monotonic_nanos=started - BASE + SECOND, detail=str(session_id),
            ))
        for send in samples:
            writer.append_lifecycle(_sample_event(send))
        sequence = 0
        price = 84_000
        for index, (start, end) in enumerate(windows):
            host = start
            count = 0
            first = True
            while host <= end:
                if first:
                    payload = _ticker(host, "snapshot", markPrice="84000.10", indexPrice="84001.20")
                    first = False
                elif sequence % 3 == 0:
                    payload = _ticker(host, "delta", markPrice=f"{price}.{sequence % 10}0")
                elif sequence % 3 == 1:
                    payload = _ticker(host, "delta", indexPrice=f"{price + 1}.{sequence % 10}0")
                else:
                    payload = _trade(host, 10_000 + sequence, f"{price + sequence % 7}.10", count=2)
                if tamper is not None:
                    payload = tamper(sequence, payload)
                record = build_capture_record_v1(
                    contract=contract, session_id=session_id, sequence=sequence,
                    clock=CaptureClockReadingV1(host, host - BASE + SECOND), payload_text=payload,
                )
                writer.append_record(record)
                sequence += 1
                count += 1
                last = host
                host += SECOND // 2
            if crash:
                writer.close_without_finalizing()
                return session_id
            end_proof = END_PROOF_CONNECTION_LOST if index < len(windows) - 1 else END_PROOF_OPERATOR_BOUNDED_STOP
            if end_proofs is not None:
                end_proof = end_proofs[index]
            writer.declare_coverage(CaptureCoverageIntervalV1(start, last, end_proof, count))
            if index < len(windows) - 1:
                kind = "CLOCK_DISCONTINUITY" if end_proof == "CLOCK_DISCONTINUITY" else "CONNECTION_LOSS"
                writer.declare_gap(CaptureGapV1(last + 1, windows[index + 1][0], kind))
        writer.finalize()
        return session_id


class _TempRoots(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="t4-fixture-"))
        self.capture = self.temp / "capture"
        self.data = self.temp / "research"
        self.archive = FixtureArchive(self.capture)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def store(self, name: str = "research") -> Any:
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

        return ResearchFrameStoreV1(self.temp / name)


def _standard_samples(until_seconds: int = 260) -> list[int]:
    return [BASE - 5 * SECOND + step * 60 * SECOND for step in range(until_seconds // 60 + 2)]


# ---------------------------------------------------------------------------
# Clock evidence
# ---------------------------------------------------------------------------


class ClockEvidenceTests(unittest.TestCase):
    def test_sample_is_rederived_from_raw_readings(self) -> None:
        sample = parse_clock_offset_sample_v1(_sample_event(BASE), session_resolution_nanos=RESOLUTION)
        self.assertEqual(sample.offset_estimate_nanos, OFFSET)
        self.assertEqual(sample.venue_minus_host_upper_nanos, OFFSET + RTT // 2 + RESOLUTION)

    def test_a_stated_bound_that_does_not_rederive_is_refused(self) -> None:
        event = _sample_event(BASE)
        detail = json.loads(event.detail or "{}")
        detail["offset_bound_nanos"] = 1
        forged = replace(event, detail=json.dumps(detail))
        with self.assertRaisesRegex(T4NormalizationError, "stated_bound_not_derivable"):
            parse_clock_offset_sample_v1(forged, session_resolution_nanos=RESOLUTION)

    def test_a_sample_on_another_resolution_is_refused(self) -> None:
        with self.assertRaisesRegex(T4NormalizationError, "resolution_differs"):
            parse_clock_offset_sample_v1(_sample_event(BASE), session_resolution_nanos=1)

    def test_a_sample_recorded_before_it_was_received_is_refused(self) -> None:
        event = replace(_sample_event(BASE), arrival_utc_nanos=BASE)
        with self.assertRaisesRegex(T4NormalizationError, "recorded_before_received"):
            parse_clock_offset_sample_v1(event, session_resolution_nanos=RESOLUTION)

    def test_only_bracketed_arrivals_get_a_bound_and_it_is_the_bracket_max(self) -> None:
        low = parse_clock_offset_sample_v1(_sample_event(BASE), session_resolution_nanos=RESOLUTION)
        high_event = _sample_event(BASE + 60 * SECOND)
        detail = json.loads(high_event.detail or "{}")
        detail["server_utc_nanos"] += 7_000_000
        detail["offset_estimate_nanos"] += 7_000_000
        high = parse_clock_offset_sample_v1(
            replace(high_event, detail=json.dumps(detail)), session_resolution_nanos=RESOLUTION
        )
        clock = SessionClockEvidenceV1(uuid4(), [high, low])
        self.assertIsNone(clock.bound_for(BASE))  # before the first receive
        self.assertIsNone(clock.bound_for(BASE + 61 * SECOND))  # after the last send
        bound = clock.bound_for(BASE + 30 * SECOND)
        assert bound is not None
        self.assertEqual(
            bound.venue_minus_host_upper_bound_nanos,
            max(low.venue_minus_host_upper_nanos, high.venue_minus_host_upper_nanos),
        )
        self.assertIn(low.sample_hash, bound.evidence_reference)
        self.assertIn(high.sample_hash, bound.evidence_reference)

    def test_overlapping_samples_are_not_one_coherent_series(self) -> None:
        first = parse_clock_offset_sample_v1(_sample_event(BASE), session_resolution_nanos=RESOLUTION)
        second = parse_clock_offset_sample_v1(
            _sample_event(BASE + RTT // 2), session_resolution_nanos=RESOLUTION
        )
        with self.assertRaisesRegex(T4NormalizationError, "overlap"):
            SessionClockEvidenceV1(uuid4(), [first, second])

    def test_knowledge_arithmetic_equals_the_doctrine(self) -> None:
        from trade_platform.knowledge_time_doctrine_v1 import HostClockBoundV1

        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        for arrival in (BASE, BASE + 1, BASE + 999, BASE + 1_001, BASE + 123_456_789):
            for bound in (-5_000, 0, 1, 999, 1_001, OFFSET + 65_625_001):
                expected = HostClockBoundV1(bound, "x").venue_upper_bound(nanos_to_datetime(arrival))
                micros = market_knowledge_micros_v1(arrival, bound)
                self.assertEqual(epoch + timedelta(microseconds=micros), expected)
                self.assertGreaterEqual(micros * 1_000, arrival + max(0, bound))


# ---------------------------------------------------------------------------
# Streaming normalization
# ---------------------------------------------------------------------------


def _record(sequence: int, host: int, payload: str, session: UUID) -> Any:
    return build_capture_record_v1(
        contract=CONTRACT, session_id=session, sequence=sequence,
        clock=CaptureClockReadingV1(host, host - BASE + SECOND), payload_text=payload,
    )


BOUND = ArrivalClockBoundEvidenceV1(OFFSET + RTT // 2 + RESOLUTION, "p", "n")


class TickerStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = uuid4()
        self.normalizer = T4SegmentNormalizerV1(exchange_symbol="BTCUSDT", session_id=self.session)

    def feed(self, sequence: int, host: int, payload: str) -> None:
        self.normalizer.feed(_record(sequence, host, payload, self.session), BOUND)

    def test_no_basis_before_both_components_and_delta_changes_only_its_field(self) -> None:
        self.feed(0, BASE, _ticker(BASE, "delta", markPrice="100.0"))
        self.assertEqual(self.normalizer.output.basis, [])
        self.feed(1, BASE + SECOND, _ticker(BASE + SECOND, "delta", indexPrice="99.0"))
        self.feed(2, BASE + 2 * SECOND, _ticker(BASE + 2 * SECOND, "delta", bid1Price="1"))
        basis = self.normalizer.output.basis
        self.assertEqual(len(basis), 1)  # a record touching neither component emits nothing
        self.assertEqual(basis[0].mark.price, basis[0].mark.price.__class__("100.0"))
        # Knowledge is the later component, never the emitting record alone.
        self.assertEqual(
            basis[0].market_knowledge_micros,
            max(basis[0].mark.arrival.market_knowledge_micros, basis[0].index.arrival.market_knowledge_micros),
        )
        self.assertEqual(basis[0].event_micros, max(basis[0].mark.event_micros, basis[0].index.event_micros))

    def test_snapshot_replaces_state_and_a_missing_component_becomes_unobserved(self) -> None:
        self.feed(0, BASE, _ticker(BASE, "snapshot", markPrice="100.0", indexPrice="99.0"))
        self.feed(1, BASE + SECOND, _ticker(BASE + SECOND, "snapshot", markPrice="101.0"))
        self.feed(2, BASE + 2 * SECOND, _ticker(BASE + 2 * SECOND, "delta", markPrice="102.0"))
        self.assertEqual(len(self.normalizer.output.basis), 1)  # only the first snapshot
        self.assertEqual(self.normalizer.counts.ticker_records_before_state_complete, 2)

    def test_a_clock_bound_that_puts_knowledge_before_the_venue_event_fails_closed(self) -> None:
        tiny = ArrivalClockBoundEvidenceV1(0, "p", "n")
        record = _record(0, BASE, _ticker(BASE, "snapshot", markPrice="1", indexPrice="1"), self.session)
        with self.assertRaisesRegex(T4NormalizationError, "knowledge_before_venue_event"):
            self.normalizer.feed(record, tiny)

    def test_cross_sequence_regression_is_refused(self) -> None:
        self.feed(0, BASE + SECOND, _ticker(BASE + SECOND, "snapshot", markPrice="1", indexPrice="1"))
        payload = json.loads(_ticker(BASE + 2 * SECOND, "delta", markPrice="2"))
        payload["cs"] = 1
        with self.assertRaisesRegex(T4NormalizationError, "cross_sequence_regressed"):
            self.feed(1, BASE + 2 * SECOND, json.dumps(payload))


class TradeBarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = uuid4()
        self.normalizer = T4SegmentNormalizerV1(exchange_symbol="BTCUSDT", session_id=self.session)
        self.sequence = 0

    def trade(self, host: int, seq: int, price: str, *, trade_ts: int | None = None, count: int = 1) -> None:
        payload = json.loads(_trade(host, seq, price, count=count))
        if trade_ts is not None:
            for entry in payload["data"]:
                entry["T"] = trade_ts
            payload["ts"] = max(payload["ts"], trade_ts)
        self.normalizer.feed(_record(self.sequence, host, json.dumps(payload), self.session), BOUND)
        self.sequence += 1

    def minute_ms(self, minute: int, second: float = 0) -> int:
        return (BASE + OFFSET) // 1_000_000 // 60_000 * 60_000 + minute * 60_000 + int(second * 1000)

    def host_for(self, venue_ms: int) -> int:
        return venue_ms * 1_000_000 - OFFSET + 50_000_000

    def test_bars_need_an_open_proof_and_a_close_proof(self) -> None:
        stamps = [(1, 5), (1, 30), (2, 1), (2, 59), (3, 0), (3, 20)]
        for index, (minute, second) in enumerate(stamps):
            venue = self.minute_ms(minute, second)
            self.trade(self.host_for(venue), 100 + index, f"{100 + index}.0", trade_ts=venue)
        output = self.normalizer.finish()
        # Minute 1: first trade of the segment, no open proof. Minute 3: no close.
        self.assertEqual([bar.trade_count for bar in output.bars], [2])
        self.assertEqual(self.normalizer.counts.minutes_without_open_proof, 1)
        self.assertEqual(self.normalizer.counts.minutes_open_at_segment_end, 1)
        bar = output.bars[0]
        close_micros = self.minute_ms(3) * 1_000
        self.assertEqual(bar.bar_close_micros, close_micros)
        closing = output.trades[4].arrival.market_knowledge_micros
        self.assertEqual(
            bar.complete_market_knowledge_micros,
            max(close_micros, closing, *(t.arrival.market_knowledge_micros for t in output.trades[2:4])),
        )
        self.assertGreaterEqual(bar.complete_market_knowledge_micros, closing)
        self.assertEqual(bar.open_market_knowledge_micros, output.trades[2].arrival.market_knowledge_micros)

    def test_trade_order_regression_is_refused_not_resorted(self) -> None:
        venue = self.minute_ms(1, 10)
        self.trade(self.host_for(venue), 200, "1.0", trade_ts=venue)
        with self.assertRaisesRegex(T4NormalizationError, "trade_order_regressed"):
            self.trade(self.host_for(venue) + 1, 199, "1.0", trade_ts=venue)

    def test_a_repeated_trade_id_is_refused(self) -> None:
        venue = self.minute_ms(1, 10)
        self.trade(self.host_for(venue), 300, "1.0", trade_ts=venue)
        with self.assertRaisesRegex(T4NormalizationError, "trade_id_repeated"):
            self.trade(self.host_for(venue) + 1, 300, "1.0", trade_ts=venue)

    def test_a_trade_stamped_after_its_message_is_refused(self) -> None:
        payload = json.loads(_trade(BASE, 5, "1.0"))
        payload["data"][0]["T"] = payload["ts"] + 1
        with self.assertRaisesRegex(T4NormalizationError, "trade_time_after_its_message"):
            self.normalizer.feed(_record(0, BASE, json.dumps(payload), self.session), BOUND)

    def test_same_seq_group_with_two_prices_is_reported_ambiguous(self) -> None:
        first = self.minute_ms(1, 50)
        self.trade(self.host_for(first), 400, "1.0", trade_ts=first)
        venue = self.minute_ms(2, 0)
        payload = json.loads(_trade(self.host_for(venue), 401, "5.0", count=2))
        for entry in payload["data"]:
            entry["T"] = venue
        payload["data"][1]["p"] = "6.0"
        self.normalizer.feed(_record(self.sequence, self.host_for(venue), json.dumps(payload), self.session), BOUND)
        self.sequence += 1
        close = self.minute_ms(3, 1)
        self.trade(self.host_for(close), 402, "7.0", trade_ts=close)
        bar = self.normalizer.finish().bars[0]
        self.assertTrue(bar.open_is_sequence_ambiguous)
        self.assertTrue(bar.close_is_sequence_ambiguous)
        self.assertEqual(str(bar.open_price), "5.0")  # arrival position, never the trade id


# ---------------------------------------------------------------------------
# Discovery, sealing, verification
# ---------------------------------------------------------------------------


class DiscoveryAndSealTests(_TempRoots):
    def _seal_one(self, **kwargs: Any) -> Any:
        from trade_platform.first_party_t4_seal_v1 import (
            discover_t4_segments_v1,
            seal_t4_segment_v1,
        )

        discovery = discover_t4_segments_v1(self.capture)
        self.assertEqual(len(discovery.segments), 1)
        return seal_t4_segment_v1(discovery.segments[0], store=self.store(), **kwargs)

    def _seal_one_of(self, expected: int, *, day: date) -> Any:
        from trade_platform.first_party_t4_seal_v1 import (
            discover_t4_segments_v1,
            seal_t4_segment_v1,
        )

        discovery = discover_t4_segments_v1(self.capture)
        self.assertEqual(len(discovery.segments), expected)
        plan = next(item for item in discovery.segments if item.partition.utc_day == day.isoformat())
        return seal_t4_segment_v1(plan, store=self.store())

    def test_partial_partitions_and_measurement_sources_contribute_nothing(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import discover_t4_segments_v1

        self.archive.session(windows=[(BASE, BASE + 10 * SECOND)], samples=_standard_samples(), crash=True)
        self.archive.session(
            windows=[(BASE, BASE + 10 * SECOND)], samples=_standard_samples(),
            contract=first_party_bybit_measurement_contract_v1("ETHUSDT"),
            tamper=lambda _, text: text.replace("BTCUSDT", "ETHUSDT"),
        )
        discovery = discover_t4_segments_v1(self.capture)
        self.assertEqual(discovery.segments, ())
        self.assertEqual([item.scope for item in discovery.exclusions], ["PARTITION"])
        self.assertIn("partition_has_no_manifest", discovery.exclusions[0].reasons)

    def test_a_complete_partition_without_clock_samples_is_not_t4(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import discover_t4_segments_v1

        self.archive.session(windows=[(BASE, BASE + 10 * SECOND)], samples=[])
        discovery = discover_t4_segments_v1(self.capture)
        self.assertEqual(discovery.segments, ())
        self.assertEqual(
            discovery.exclusions[0].reasons, ("session_has_no_bracketing_clock_offset_samples",)
        )

    def test_each_window_is_its_own_segment_and_a_still_open_tail_is_pending(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import discover_t4_segments_v1

        windows = [(BASE, BASE + 60 * SECOND), (BASE + 70 * SECOND, BASE + 200 * SECOND)]
        samples = [BASE - 5 * SECOND, BASE + 55 * SECOND, BASE + 115 * SECOND, BASE + 175 * SECOND]
        self.archive.session(
            windows=windows, samples=samples, end_proofs=["CONNECTION_LOST", "UTC_DAY_ROLLOVER"]
        )
        discovery = discover_t4_segments_v1(self.capture)
        # Window 0 ends inside the bracketed range: final. Window 1's bracket ends
        # only because no later sample exists yet: pending, never sealed short.
        self.assertEqual(len(discovery.segments), 1)
        # Each segment binds only the contiguous samples it uses: the window ends
        # at +60 s, so its bracket closes with the +115 s sample, not +175 s.
        self.assertEqual(len(discovery.segments[0].clock.samples), 3)
        pending = [item for item in discovery.exclusions if item.scope == "SEGMENT_PENDING"]
        self.assertEqual([item.reasons for item in pending], [("awaiting_a_later_clock_sample",)])
        unbracketed = [item for item in discovery.exclusions if item.scope == "WINDOW_UNBRACKETED"]
        self.assertEqual([item.duration_nanos for item in unbracketed], [25 * SECOND])

    def test_a_provably_finished_session_seals_its_final_bracket(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import discover_t4_segments_v1

        windows = [(BASE, BASE + 60 * SECOND), (BASE + 70 * SECOND, BASE + 200 * SECOND)]
        samples = [BASE - 5 * SECOND, BASE + 55 * SECOND, BASE + 115 * SECOND, BASE + 175 * SECOND]
        self.archive.session(windows=windows, samples=samples)  # ends in an operator stop
        discovery = discover_t4_segments_v1(self.capture)
        self.assertEqual(len(discovery.segments), 2)
        self.assertEqual(discovery.segments[1].end_arrival_nanos, BASE + 175 * SECOND)
        self.assertNotIn("SEGMENT_PENDING", {item.scope for item in discovery.exclusions})

    def test_a_sample_taken_before_the_session_started_is_not_its_evidence(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import discover_t4_segments_v1

        samples = [BASE - 5 * SECOND, BASE + 55 * SECOND, BASE + 115 * SECOND]
        self.archive.session(
            windows=[(BASE, BASE + 100 * SECOND)], samples=samples, started=BASE - 2 * SECOND
        )
        discovery = discover_t4_segments_v1(self.capture)
        self.assertEqual(len(discovery.segments), 1)
        # The -5 s sample predates the session: arrivals before the +55 s sample
        # have no bracket.
        self.assertEqual(discovery.segments[0].start_arrival_nanos, BASE + 55 * SECOND + RTT)

    def test_a_bracket_may_not_span_a_recorded_clock_discontinuity(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import discover_t4_segments_v1

        windows = [(BASE, BASE + 90 * SECOND), (BASE + 95 * SECOND, BASE + 200 * SECOND)]
        samples = [BASE - 5 * SECOND, BASE + 55 * SECOND, BASE + 115 * SECOND,
                   BASE + 175 * SECOND, BASE + 235 * SECOND]
        self.archive.session(
            windows=windows, samples=samples,
            end_proofs=["CLOCK_DISCONTINUITY", "OPERATOR_BOUNDED_STOP"],
        )
        discovery = discover_t4_segments_v1(self.capture)
        # The pair (+55, +115) spans the step at +90: nothing between +55 and
        # +115 is bracketed, on either side of the step.
        for plan in discovery.segments:
            self.assertFalse(plan.start_arrival_nanos < BASE + 115 * SECOND
                             and plan.end_arrival_nanos > BASE + 55 * SECOND + RTT)
        self.assertEqual(len(discovery.segments), 2)

    def test_a_bracket_wider_than_the_sampling_tolerance_is_refused(self) -> None:
        from trade_platform.first_party_t4_normalization_v1 import MAX_CLOCK_BRACKET_NANOS

        low = parse_clock_offset_sample_v1(_sample_event(BASE), session_resolution_nanos=RESOLUTION)
        high = parse_clock_offset_sample_v1(
            _sample_event(BASE + MAX_CLOCK_BRACKET_NANOS + SECOND), session_resolution_nanos=RESOLUTION
        )
        clock = SessionClockEvidenceV1(uuid4(), [low, high])
        self.assertIsNone(clock.bound_for(BASE + 10 * SECOND))
        self.assertEqual(clock.admissible_intervals(), ())

    def test_a_restored_seal_cannot_be_relabelled_as_rebuilt(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import restore_t4_seal_without_replay_v1

        self.archive.session(windows=[(BASE, BASE + 120 * SECOND)], samples=_standard_samples())
        seal = self._seal_one()
        restored = restore_t4_seal_without_replay_v1(
            seal.identity, frame_manifests=seal.frame_manifests, sealed_at=seal.sealed_at,
            store=self.store(),
        )
        forged = replace(restored, raw_replayed=True)
        self.assertTrue(restored.integrity_verified())
        self.assertFalse(forged.integrity_verified())

    def test_sealing_is_deterministic_and_excludes_every_platform_clock(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import (
            discover_t4_segments_v1,
            seal_t4_segment_v1,
        )

        self.archive.session(windows=[(BASE, BASE + 240 * SECOND)], samples=_standard_samples())
        plan = discover_t4_segments_v1(self.capture).segments[0]
        first = seal_t4_segment_v1(plan, store=self.store("a"), sealed_at=datetime(2026, 9, 21, tzinfo=UTC))
        again = seal_t4_segment_v1(plan, store=self.store("b"), sealed_at=datetime(2027, 1, 1, tzinfo=UTC))
        self.assertTrue(first.integrity_verified())
        self.assertEqual(first.content_hash, again.content_hash)
        self.assertEqual(first.dataset_version_id, again.dataset_version_id)
        facts = first.timing_facts
        self.assertEqual(facts.observations_missing_knowledge_time, 0)
        self.assertGreater(facts.distinct_knowledge_time_count, 1)
        counts = first.identity["counts"]
        self.assertGreater(counts["bars"], 0)
        self.assertEqual(
            facts.observations_with_knowledge_time, counts["reference_prices"] + counts["trades"]
        )

    def test_the_seal_cannot_be_fabricated_or_edited(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import FirstPartyT4SealError, FirstPartyT4SealV1

        self.archive.session(windows=[(BASE, BASE + 120 * SECOND)], samples=_standard_samples())
        seal = self._seal_one()
        with self.assertRaises(FirstPartyT4SealError):
            FirstPartyT4SealV1(**{name: getattr(seal, name) for name in (
                "schema_version", "identity", "content_hash", "dataset_version_id", "source_id",
                "timing_facts", "frame_manifests", "sealed_at", "raw_replayed", "seal_token")})
        edited = dict(seal.identity)
        edited["timing_facts"] = {**edited["timing_facts"], "distinct_knowledge_time_count": 10**9}
        object.__setattr__(seal, "identity", edited)
        self.assertFalse(seal.integrity_verified())

    def test_verify_rebuilds_from_raw_and_detects_tampered_frames(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import (
            FirstPartyT4SealError,
            verify_t4_dataset_v1,
        )

        self.archive.session(windows=[(BASE, BASE + 120 * SECOND)], samples=_standard_samples())
        seal = self._seal_one()
        store = self.store()
        rebuilt = verify_t4_dataset_v1(
            seal.identity, frame_manifests=seal.frame_manifests, sealed_at=seal.sealed_at,
            store=store, capture_root=self.capture,
        )
        self.assertEqual(rebuilt.content_hash, seal.content_hash)
        self.assertTrue(rebuilt.raw_replayed)
        manifest = store.load_manifest(seal.frame_manifests["T4_TRADE"])
        path = store.object_path(manifest.objects[0])
        path.write_bytes(path.read_bytes()[:-8] + b"\x00" * 8)
        with self.assertRaisesRegex(FirstPartyT4SealError, "sealed_frame_not_verifiable"):
            verify_t4_dataset_v1(
                seal.identity, frame_manifests=seal.frame_manifests, sealed_at=seal.sealed_at,
                store=store, capture_root=self.capture,
            )

    def test_a_forged_wider_segment_is_refused(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import (
            FirstPartyT4SealError,
            verify_t4_dataset_v1,
        )

        self.archive.session(windows=[(BASE, BASE + 120 * SECOND)], samples=_standard_samples())
        seal = self._seal_one()
        forged = json.loads(json.dumps(seal.identity))
        forged["segment"]["start_arrival_nanos"] -= SECOND
        with self.assertRaises(FirstPartyT4SealError):
            verify_t4_dataset_v1(
                forged, frame_manifests=seal.frame_manifests, sealed_at=seal.sealed_at,
                store=self.store(), capture_root=self.capture,
            )

    def test_a_record_rewritten_on_disk_breaks_the_rebuild(self) -> None:
        from trade_platform.first_party_capture_archive_v1 import FirstPartyCaptureArchiveError
        from trade_platform.first_party_t4_seal_v1 import (
            FirstPartyT4SealError,
            verify_t4_dataset_v1,
        )

        self.archive.session(windows=[(BASE, BASE + 120 * SECOND)], samples=_standard_samples())
        seal = self._seal_one()
        records = next(self.capture.rglob("records.ndjson"))
        records.write_text(records.read_text(encoding="utf-8").replace("84001.20", "84001.30"), encoding="utf-8")
        with self.assertRaises((FirstPartyT4SealError, FirstPartyCaptureArchiveError)):
            verify_t4_dataset_v1(
                seal.identity, frame_manifests=seal.frame_manifests, sealed_at=seal.sealed_at,
                store=self.store(), capture_root=self.capture,
            )

    def test_a_later_completed_partition_does_not_change_an_existing_dataset(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import (
            discover_t4_segments_v1,
            verify_t4_dataset_v1,
        )

        session = self.archive.session(
            windows=[(BASE, BASE + 200 * SECOND)],
            samples=[BASE - 5 * SECOND, BASE + 55 * SECOND, BASE + 115 * SECOND],
            end_proofs=["UTC_DAY_ROLLOVER"],
        )
        # Until a later sample exists the tail is pending: nothing is sealed.
        self.assertEqual(discover_t4_segments_v1(self.capture).segments, ())
        next_day = BASE + 86_400 * SECOND
        self.archive.session(
            windows=[(next_day, next_day + 10 * SECOND)], samples=[next_day - 2 * SECOND, next_day + 20 * SECOND],
            day=DAY + timedelta(days=1), session_id=session,
        )
        # A day later the gap is wider than the sampling tolerance: the bracket
        # breaks for good, so the segment is final and its tail excluded.
        seal = self._seal_one_of(2, day=DAY)
        self.assertEqual(seal.identity["segment"]["excluded_tail_nanos"], 85 * SECOND)
        self.archive.session(
            windows=[(next_day + 86_400 * SECOND, next_day + 86_410 * SECOND)],
            samples=[next_day + 86_398 * SECOND, next_day + 86_420 * SECOND],
            day=DAY + timedelta(days=2), session_id=session,
        )
        rebuilt = verify_t4_dataset_v1(
            seal.identity, frame_manifests=seal.frame_manifests, sealed_at=seal.sealed_at,
            store=self.store(), capture_root=self.capture,
        )
        self.assertEqual(rebuilt.content_hash, seal.content_hash)


# ---------------------------------------------------------------------------
# Provenance, tier, doctrine and V3 features
# ---------------------------------------------------------------------------


class T4AuthorityTests(_TempRoots):
    def setUp(self) -> None:
        super().setUp()
        from trade_platform.first_party_t4_seal_v1 import (
            discover_t4_segments_v1,
            seal_t4_segment_v1,
        )

        self.archive.session(windows=[(BASE, BASE + 300 * SECOND)], samples=_standard_samples(320))
        self.plan = discover_t4_segments_v1(self.capture).segments[0]
        self.sealed_at = datetime(2026, 9, 21, tzinfo=UTC)
        self.seal = seal_t4_segment_v1(self.plan, store=self.store(), sealed_at=self.sealed_at)

    def test_provenance_and_tier_from_the_seal_are_t4_professional(self) -> None:
        from trade_platform.evidence_tier_authority_v1 import EvidenceTierV1
        from trade_platform.first_party_t4_dataset_v1 import issue_t4_evidence_tier_v1

        provenance, verdict = issue_t4_evidence_tier_v1(self.seal)
        self.assertTrue(provenance.is_proven_real())
        self.assertIsNone(provenance.dataset_created_at)
        self.assertEqual(verdict.tier, EvidenceTierV1.T4_FIRST_PARTY_CAPTURE.value)
        self.assertEqual(verdict.reasons, ())
        self.assertTrue(verdict.is_professional_evidence())
        self.assertEqual(verdict.dataset_content_hash, self.seal.content_hash)

    def test_caller_supplied_timing_facts_cannot_forge_t4(self) -> None:
        from trade_platform.evidence_tier_authority_v1 import (
            evaluate_evidence_tier_v1,
            first_party_sealed_timing_facts_v1,
        )
        from trade_platform.real_market_data_provenance_v1 import (
            evaluate_first_party_capture_provenance_v1,
        )

        provenance = evaluate_first_party_capture_provenance_v1(self.seal)
        honest = first_party_sealed_timing_facts_v1(provenance)
        inflated = replace(honest, distinct_knowledge_time_count=honest.distinct_knowledge_time_count + 1)
        verdict = evaluate_evidence_tier_v1(inflated, provenance)
        self.assertIn("evidence_tier_timing_facts_disagree_with_the_sealed_capture", verdict.reasons)
        self.assertFalse(verdict.is_professional_evidence())

    def test_a_seal_restored_without_raw_replay_proves_nothing(self) -> None:
        from trade_platform.first_party_t4_dataset_v1 import (
            FirstPartyT4DatasetError,
            FirstPartyT4SealedClockResolverV1,
        )
        from trade_platform.first_party_t4_seal_v1 import restore_t4_seal_without_replay_v1
        from trade_platform.real_market_data_provenance_v1 import (
            evaluate_first_party_capture_provenance_v1,
        )

        restored = restore_t4_seal_without_replay_v1(
            self.seal.identity, frame_manifests=self.seal.frame_manifests,
            sealed_at=self.sealed_at, store=self.store(),
        )
        provenance = evaluate_first_party_capture_provenance_v1(restored)
        self.assertFalse(provenance.is_proven_real())
        self.assertIn("first_party_seal_not_rebuilt_from_raw_capture", provenance.reasons)
        with self.assertRaises(FirstPartyT4DatasetError):
            FirstPartyT4SealedClockResolverV1(restored, store=self.store())

    def test_the_first_party_source_cannot_qualify_through_the_persisted_lineage_path(self) -> None:
        from trade_platform.real_market_data_provenance_v1 import (
            DatasetLineageFactsV1,
            PersistedSourceFactsV1,
            evaluate_real_market_data_provenance_v1,
            first_party_capture_source_contract_v1,
        )

        projection = first_party_capture_source_contract_v1()
        facts = DatasetLineageFactsV1(
            dataset_version_id=self.seal.dataset_version_id, found=True, status="SEALED",
            version="x", normalization_version="x", content_hash=self.seal.content_hash,
            source_id=projection.source_id,
            source=PersistedSourceFactsV1(
                projection.source_id, projection.provider, projection.dataset_name,
                projection.provider_identifier_namespace, projection.provider_terms_version,
                projection.authorization_reference, projection.asset_scope,
                projection.observation_kinds,
            ),
            member_count=10, lineage_complete_member_count=10,
        )
        verdict = evaluate_real_market_data_provenance_v1(facts)
        self.assertFalse(verdict.is_proven_real())
        self.assertIn("source_id_not_canonical", verdict.reasons)

    def _features(self, computed_at: datetime, resolver: Any = None) -> Any:
        from trade_platform.first_party_t4_dataset_v1 import (
            FirstPartyT4SealedClockResolverV1,
            build_t4_basis_features_v3,
            issue_t4_evidence_tier_v1,
        )

        _, verdict = issue_t4_evidence_tier_v1(self.seal)
        resolver = FirstPartyT4SealedClockResolverV1(self.seal, store=self.store()) if resolver is None else resolver
        rows = build_t4_basis_features_v3(
            self.seal, verdict=verdict, resolver=resolver, store=self.store(),
            feature_id=uuid4() if not hasattr(self, "feature_id") else self.feature_id,
            computed_at=computed_at,
        )
        return verdict, resolver, rows

    def test_v3_features_carry_distinct_professional_decision_times_invariant_to_recompute(self) -> None:
        from trade_platform.knowledge_time_doctrine_v1 import (
            ClaimCeilingV1,
            DeclaredComputeLatencyV1,
        )
        from trade_platform.open_to_open_validation_orchestration_v1 import (
            count_distinct_historical_decision_times_v1,
            historical_feature_decision_v1,
        )

        self.feature_id = uuid4()
        verdict, resolver, rows = self._features(datetime(2026, 9, 22, tzinfo=UTC))
        self.assertGreater(len(rows), 2)
        self.assertEqual(len({row.market_knowledge_at for row in rows}), len(rows))
        self.assertTrue(all(row.claim_ceiling is ClaimCeilingV1.PROFESSIONAL for row in rows))
        latency = DeclaredComputeLatencyV1(0, "fixture: engineering proof only")
        count = count_distinct_historical_decision_times_v1(
            rows, compute_latency=latency, evidence_tiers={verdict.evidence_id: verdict},
            clock_resolver=resolver, minimum_claim=ClaimCeilingV1.PROFESSIONAL,
        )
        self.assertEqual(count, len(rows))
        # Recomputing later changes neither identity nor any decision time.
        _, _, later = self._features(datetime(2031, 1, 1, tzinfo=UTC))
        self.assertEqual([row.content_hash for row in rows], [row.content_hash for row in later])
        for before, after in zip(rows, later, strict=True):
            first = historical_feature_decision_v1(
                before, compute_latency=latency, evidence_tiers={verdict.evidence_id: verdict},
                clock_resolver=resolver,
            )
            second = historical_feature_decision_v1(
                after, compute_latency=latency, evidence_tiers={verdict.evidence_id: verdict},
                clock_resolver=resolver,
            )
            self.assertEqual(first.decision_at, second.decision_at)
            self.assertEqual(first.decision_time_id, second.decision_time_id)
            self.assertGreaterEqual(first.decision_at, before.event_at)

    def test_no_feature_value_uses_a_component_that_arrived_after_it(self) -> None:
        self.feature_id = uuid4()
        _, resolver, rows = self._features(datetime(2026, 9, 22, tzinfo=UTC))
        for row in rows:
            knowledge = row.verified_feature_knowledge_v1(
                {self._verdict().evidence_id: self._verdict()}, resolver
            )
            self.assertEqual(knowledge.market_knowledge_at, row.market_knowledge_at)
            self.assertGreaterEqual(row.market_knowledge_at, row.event_at)

    def _verdict(self) -> Any:
        from trade_platform.first_party_t4_dataset_v1 import issue_t4_evidence_tier_v1

        return issue_t4_evidence_tier_v1(self.seal)[1]

    def test_rows_do_not_verify_against_another_datasets_resolver(self) -> None:
        from trade_platform.feature_authority import FeatureAuthorityError
        from trade_platform.first_party_t4_dataset_v1 import FirstPartyT4SealedClockResolverV1
        from trade_platform.first_party_t4_seal_v1 import seal_t4_segment_v1

        self.feature_id = uuid4()
        verdict, _, rows = self._features(datetime(2026, 9, 22, tzinfo=UTC))
        other_plan = replace(self.plan, end_arrival_nanos=self.plan.end_arrival_nanos - 30 * SECOND)
        other = seal_t4_segment_v1(other_plan, store=self.store(), sealed_at=self.sealed_at)
        self.assertNotEqual(other.dataset_version_id, self.seal.dataset_version_id)
        foreign = FirstPartyT4SealedClockResolverV1(other, store=self.store())
        with self.assertRaises(FeatureAuthorityError):
            rows[0].verified_feature_knowledge_v1({verdict.evidence_id: verdict}, foreign)

    def test_an_unregistered_resolver_cannot_back_a_decision(self) -> None:
        from trade_platform.knowledge_time_doctrine_v1 import DeclaredComputeLatencyV1
        from trade_platform.open_to_open_validation_orchestration_v1 import (
            OpenToOpenValidationOrchestrationV1Error,
            historical_feature_decision_v1,
        )

        self.feature_id = uuid4()
        verdict, resolver, rows = self._features(datetime(2026, 9, 22, tzinfo=UTC))

        def impostor(dataset: UUID, references: Any) -> Any:
            return resolver(dataset, references)

        with self.assertRaisesRegex(OpenToOpenValidationOrchestrationV1Error, "not_authorized"):
            historical_feature_decision_v1(
                rows[0], compute_latency=DeclaredComputeLatencyV1(0, "fixture"),
                evidence_tiers={verdict.evidence_id: verdict}, clock_resolver=impostor,
            )


if __name__ == "__main__":
    unittest.main()
