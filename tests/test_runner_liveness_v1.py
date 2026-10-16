"""Runner liveness -- proven forward processing vs heartbeats, restarts, clocks, capture and seals."""

from __future__ import annotations

import unittest
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid5

from trade_platform.first_party_capture_hourly_acceptance_v1 import (
    HOUR_NANOS,
    HourlyAcceptanceV1,
    HourlySegmentV1,
    HourVerdictV1,
    SymbolHoursV1,
)
from trade_platform.runner_liveness_v1 import (
    CAPTURE_HOUR_PREFIX,
    KIND_BARS,
    KIND_HEARTBEAT,
    KIND_STARTED,
    STATUS_PROVEN,
    DiscontinuityV1,
    RunnerLivenessError,
    RunnerProgressRecorderV1,
    RunnerProgressV1,
    SealedBarsEvidenceV1,
    SessionRefusalV1,
    UnprovenReasonV1,
    derive_runner_liveness_v1,
)
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

DAY0 = datetime(2026, 10, 1, tzinfo=UTC)  # past: the shared test database asserts global invariants
DAY0_MICROS = int(DAY0.timestamp() * 1_000_000)
MINUTE = 60_000_000
SECOND_NANOS = 1_000_000_000
SYMBOL = "BTCUSDT"
NS = UUID("6f1d7e0a-6b52-4a51-9a0e-5d7c4b1b0001")
SOURCE = uuid5(NS, "capture-source")
CAPTURE = [uuid5(NS, f"capture-hour-{hour}") for hour in range(4)]
STUDY = uuid5(NS, "study")
TRIAL = str(uuid5(NS, "trial"))
OTHER_TRIAL = str(uuid5(NS, "other-trial"))
BOUNDS = {"max_progress_gap_nanos": 180 * SECOND_NANOS, "max_clock_divergence_nanos": 5 * SECOND_NANOS,
          "max_processing_lag_nanos": 120 * SECOND_NANOS}


def at(minute: float) -> datetime:
    return DAY0 + timedelta(minutes=minute)


def minute_micros(minute: int) -> int:
    return DAY0_MICROS + minute * MINUTE


class MemoryStore:
    """The PostgreSQL store's contract in memory: idempotent per key, conflicts refused."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def record(self, progress: RunnerProgressV1) -> bool:
        held = self.rows.get(progress.idempotency_key)
        if held is not None:
            if held["content_hash"] != progress.content_hash:
                raise RunnerLivenessError("progress_conflicts_with_a_recorded_record")
            return False
        self.rows[progress.idempotency_key] = {"content_hash": progress.content_hash,
                                               "identity": dict(progress.identity)}
        return True

    def all(self) -> list[dict[str, Any]]:
        return list(self.rows.values())


class Gate:
    def __init__(self, admits: Callable[[int], bool] = lambda _: True) -> None:
        self._admits = admits

    def admits(self, bar_open_micros: int) -> bool:
        return self._admits(bar_open_micros)

    def payload(self) -> dict[str, Any]:
        return {"cycle_id": "cycle-test", "holdout_start": "2026-08-20T00:00:00+00:00",
                "holdout_end_exclusive": "2026-09-01T00:00:00+00:00"}


def candidate(trial: str = TRIAL) -> SimpleNamespace:
    return SimpleNamespace(study=SimpleNamespace(study_id=STUDY, content_hash="c" * 64), trial_id=trial,
                           symbol=SYMBOL, authority="INCUBATING", authority_evidence_hash="e" * 64)


def bar(minute: int, *, segment: int = 0, capture: UUID | None = None, completed_after_seconds: float = 0.5,
        manifest: str | None = None) -> SimpleNamespace:
    """The live bar of ``minute``, completed by an arrival just after the minute closes."""
    completed = (minute_micros(minute) + MINUTE) * 1000 + int(completed_after_seconds * SECOND_NANOS)
    return SimpleNamespace(
        symbol=SYMBOL, segment=segment, session_id=capture or CAPTURE[minute // 60],
        completed_by_arrival_nanos=completed,
        bar=SimpleNamespace(bar_open_micros=minute_micros(minute), closing_record_hash=f"r{minute}",
                            trade_manifest_hash=manifest or f"m{minute}"))


class Clocks:
    """True time drives the monotonic clock; ``offset`` is a host wall-clock step on top of it."""

    def __init__(self, start: datetime) -> None:
        self.base = start
        self.offset = timedelta(0)
        self.monotonic = 10 * SECOND_NANOS

    @property
    def wall(self) -> datetime:
        return self.base + self.offset

    def advance_to(self, base: datetime) -> None:
        self.monotonic += int((base - self.base).total_seconds() * SECOND_NANOS)
        self.base = base


def run_session(store: MemoryStore, *, start_minute: float, minutes: Iterable[int],
                bars_for: Callable[[int], list[SimpleNamespace]] | None = None,
                warmup: Iterable[int] = (), gate: Gate | None = None, trial: str = TRIAL,
                clock_hook: Callable[[Clocks, int], None] | None = None,
                session: UUID | None = None, heartbeat_nanos: int = 60 * SECOND_NANOS) -> RunnerProgressRecorderV1:
    """A runner session started at ``start_minute`` (+2 s) polling once a second after each minute closes."""
    clocks = Clocks(at(start_minute) + timedelta(seconds=2))
    recorder = RunnerProgressRecorderV1(store, runner="paper_incubation", symbol=SYMBOL, capture_source_id=SOURCE,
                                        candidates=[candidate(trial)], holdout_gate=gate or Gate(),
                                        heartbeat_interval_nanos=heartbeat_nanos,
                                        runner_session_id=session or uuid5(NS, f"runner-{start_minute}-{trial}"),
                                        wall_clock=lambda: clocks.wall, monotonic_clock=lambda: clocks.monotonic)
    recorder.start()
    if warmup:
        clocks.advance_to(clocks.base + timedelta(seconds=1))
        recorder.on_poll([bar(m) for m in warmup])
    for minute in minutes:
        clocks.advance_to(at(minute + 1) + timedelta(seconds=1))
        if clock_hook is not None:
            clock_hook(clocks, minute)
        recorder.on_poll(bars_for(minute) if bars_for is not None else [bar(minute)])
    return recorder


def acceptance(verdicts: list[HourVerdictV1] | None = None, *, hours: int = 2,
               sessions: list[UUID] | None = None) -> HourlyAcceptanceV1:
    verdicts = verdicts or [HourVerdictV1.COMPLETE] * hours
    sessions = sessions or CAPTURE
    first = DAY0_MICROS * 1000
    segments = tuple(HourlySegmentV1(first + index * HOUR_NANOS, verdict, session_id=sessions[index],
                                     unproven_nanos=2 * SECOND_NANOS) for index, verdict in enumerate(verdicts))
    joint = tuple(segment.complete for segment in segments)
    return HourlyAcceptanceV1(24, 10 * SECOND_NANOS, first, len(segments), (SymbolHoursV1(SYMBOL, segments),),
                              joint, ())


def sealed(hour: int, minutes: Iterable[int], *, overrides: dict[int, str] | None = None) -> SealedBarsEvidenceV1:
    overrides = overrides or {}
    return SealedBarsEvidenceV1(str(uuid5(NS, f"dataset-{hour}")), f"{hour:064x}", str(SOURCE), str(CAPTURE[hour]),
                                SYMBOL, {minute_micros(m): (overrides.get(m, f"m{m}"), f"r{m}") for m in minutes})


FULL_SEALS = [sealed(0, range(60)), sealed(1, range(60, 120))]


def derive(store: MemoryStore, *, start: int = 0, end: int = 120, accept: HourlyAcceptanceV1 | None = None,
           seals: list[SealedBarsEvidenceV1] | None = None, trial: str = TRIAL, **bounds: int):
    return derive_runner_liveness_v1(store.all(), study_id=str(STUDY), trial_id=trial, symbol=SYMBOL,
                                     window_start=at(start), window_end=at(end), acceptance=accept or acceptance(),
                                     sealed=FULL_SEALS if seals is None else seals, **{**BOUNDS, **bounds})


def reason_at(result, minute: int) -> str | None:
    micros = minute_micros(minute)
    for item in result.intervals:
        if item.start_micros <= micros < item.end_micros:
            return STATUS_PROVEN if item.status == STATUS_PROVEN else item.reason
    raise AssertionError("minute outside the window")


class RecorderTests(unittest.TestCase):
    def test_records_are_hash_chained_content_addressed_and_typed(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(3), warmup=[-3, -2])
        rows = sorted(store.all(), key=lambda row: row["identity"]["sequence"])
        self.assertEqual([KIND_STARTED, KIND_BARS, KIND_BARS, KIND_BARS], [row["identity"]["kind"] for row in rows])
        previous = None
        for row in rows:
            self.assertEqual(identity_hash_v1(row["identity"]), row["content_hash"])
            self.assertEqual(previous, row["identity"]["previous_progress_hash"])
            previous = row["content_hash"]
        self.assertIsNotNone(rows[0]["identity"]["binding"])
        self.assertTrue(all(row["identity"]["binding"] is None for row in rows[1:]))
        # Replayed bars are counted as warm-up and never listed as processed evidence.
        self.assertEqual(2, rows[-1]["identity"]["warmup_bars_total"])
        self.assertEqual(3, sum(len(row["identity"]["bars"]) for row in rows))

    def test_heartbeat_only_when_due_and_claims_no_market_data(self) -> None:
        store = MemoryStore()
        clocks = Clocks(at(0))
        recorder = RunnerProgressRecorderV1(store, runner="live_signals", symbol=SYMBOL, capture_source_id=SOURCE,
                                            candidates=[candidate()], holdout_gate=Gate(),
                                            heartbeat_interval_nanos=60 * SECOND_NANOS,
                                            runner_session_id=uuid5(NS, "hb"), wall_clock=lambda: clocks.wall,
                                            monotonic_clock=lambda: clocks.monotonic)
        recorder.start()
        clocks.advance_to(at(0.5))
        self.assertIsNone(recorder.on_poll([]))
        clocks.advance_to(at(1.01))
        heartbeat = recorder.on_poll([])
        assert heartbeat is not None
        self.assertEqual(KIND_HEARTBEAT, heartbeat.identity["kind"])
        self.assertEqual("PROCESS_ALIVE_NO_MARKET_DATA_CLAIM", heartbeat.identity["claim"])
        self.assertEqual([], heartbeat.identity["bars"])

    def test_refuses_mismatched_symbols_and_unstarted_polls(self) -> None:
        store = MemoryStore()
        with self.assertRaises(RunnerLivenessError):
            RunnerProgressRecorderV1(store, runner="x", symbol="ETHUSDT", capture_source_id=SOURCE,
                                     candidates=[candidate()], holdout_gate=Gate())
        recorder = RunnerProgressRecorderV1(store, runner="x", symbol=SYMBOL, capture_source_id=SOURCE,
                                            candidates=[candidate()], holdout_gate=Gate())
        with self.assertRaises(RunnerLivenessError):
            recorder.on_poll([])
        recorder.start()
        with self.assertRaises(RunnerLivenessError):
            recorder.start()

    def test_store_refuses_different_content_under_a_recorded_key(self) -> None:
        store = MemoryStore()
        recorder = run_session(store, start_minute=0, minutes=[0])
        row = next(row for row in store.all() if row["identity"]["sequence"] == 1)
        forged = RunnerProgressV1.build({**row["identity"], "warmup_bars_total": 99})
        self.assertEqual(f"runner-liveness:{recorder.runner_session_id}:1", forged.idempotency_key)
        with self.assertRaises(RunnerLivenessError):
            store.record(forged)
        self.assertFalse(store.record(RunnerProgressV1.build(row["identity"])))  # same content: idempotent


class ReconstructionTests(unittest.TestCase):
    def test_continuous_processing_is_proven_and_deterministic(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(120))
        result = derive(store)
        self.assertEqual(120 * MINUTE, result.proven_micros)
        self.assertEqual({}, result.unproven_micros_by_reason)
        self.assertEqual(0, result.payload["restarts"])
        proven = [item for item in result.intervals if item.status == STATUS_PROVEN]
        # One interval per capture session (hour), bound to its sealed dataset and hour.
        self.assertEqual([str(CAPTURE[0]), str(CAPTURE[1])], [item.capture_session_id for item in proven])
        self.assertEqual(1, len(proven[0].datasets))
        self.assertEqual((DAY0_MICROS * 1000,), proven[0].hours)
        # Deterministic: the same evidence in another order gives the same result.
        shuffled = MemoryStore()
        shuffled.rows = dict(reversed(list(store.rows.items())))
        self.assertEqual(result.content_hash, derive(shuffled).content_hash)

    def test_quiet_minutes_inside_one_feed_segment_count_only_where_processing_is_proven(self) -> None:
        store = MemoryStore()
        quiet = {10, 11, 12}
        run_session(store, start_minute=0, minutes=range(60),
                    bars_for=lambda m: [] if m in quiet else [bar(m)])
        seals = [sealed(0, [m for m in range(60) if m not in quiet])]
        result = derive(store, end=60, seals=seals)
        self.assertEqual(60 * MINUTE, result.proven_micros)  # a quiet minute, every record processed
        # A sealed bar the runner never processed is not a quiet minute.
        missed = derive(store, end=60, seals=[sealed(0, range(60))])
        self.assertEqual(UnprovenReasonV1.SEALED_BAR_NOT_PROCESSED.value, reason_at(missed, 11))

    def test_heartbeats_alone_prove_nothing(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(30), bars_for=lambda m: [])
        result = derive(store, end=30)
        self.assertEqual(0, result.proven_micros)
        self.assertEqual(UnprovenReasonV1.ALIVE_WITHOUT_DATA_PROOF.value, reason_at(result, 10))
        kinds = {row["identity"]["kind"] for row in store.all()}
        self.assertEqual({KIND_STARTED, KIND_HEARTBEAT}, kinds)

    def test_restart_is_a_new_session_and_never_bridged_nor_back_filled(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(30))
        # Restart at minute 40: the feed replays minutes 0..38 as warm-up.
        run_session(store, start_minute=40, minutes=range(40, 60), warmup=range(39))
        result = derive(store, end=60)
        self.assertEqual(1, result.payload["restarts"])
        self.assertEqual(STATUS_PROVEN, reason_at(result, 29))
        for minute in range(31, 40):
            self.assertEqual(UnprovenReasonV1.NO_RUNNER_PROGRESS.value, reason_at(result, minute))
        self.assertEqual(STATUS_PROVEN, reason_at(result, 40))
        self.assertEqual(50 * MINUTE, result.proven_micros)
        sessions = {item.runner_session_id for item in result.intervals if item.status == STATUS_PROVEN}
        self.assertEqual(2, len(sessions))

    def test_history_before_the_first_session_is_never_claimed(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=30, minutes=range(30, 60), warmup=range(29))
        result = derive(store, end=60)
        self.assertEqual(30 * MINUTE, result.proven_micros)
        self.assertEqual(UnprovenReasonV1.NO_RUNNER_PROGRESS.value, reason_at(result, 5))

    def test_host_clock_jump_and_backwards_break_the_chain(self) -> None:
        def jump(clocks: Clocks, minute: int) -> None:
            if minute == 20:  # the host wall clock steps 10 min ahead; monotonic time does not
                clocks.offset = timedelta(minutes=10)

        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(60), clock_hook=jump)
        result = derive(store, end=60)
        kinds = [item["kind"] for item in result.payload["runner_sessions"][0]["discontinuities"]]
        self.assertIn(DiscontinuityV1.HOST_CLOCK_JUMP.value, kinds)
        self.assertNotEqual(STATUS_PROVEN, reason_at(result, 20))
        self.assertEqual(STATUS_PROVEN, reason_at(result, 19))

        def backwards(clocks: Clocks, minute: int) -> None:
            if minute == 20:
                clocks.offset = timedelta(minutes=-5)

        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(60), clock_hook=backwards)
        result = derive(store, end=60)
        kinds = [item["kind"] for item in result.payload["runner_sessions"][0]["discontinuities"]]
        self.assertIn(DiscontinuityV1.HOST_CLOCK_BACKWARDS.value, kinds)

    def test_missing_progress_record_and_progress_gap_are_unproven(self) -> None:
        store = MemoryStore()
        recorder = run_session(store, start_minute=0, minutes=range(60))
        del store.rows[f"runner-liveness:{recorder.runner_session_id}:21"]  # the record of minute 20
        result = derive(store, end=60)
        self.assertEqual(STATUS_PROVEN, reason_at(result, 19))
        self.assertNotEqual(STATUS_PROVEN, reason_at(result, 20))
        self.assertEqual(STATUS_PROVEN, reason_at(result, 21))
        kinds = [item["kind"] for item in result.payload["runner_sessions"][0]["discontinuities"]]
        self.assertIn(DiscontinuityV1.SEQUENCE_GAP.value, kinds)
        # A stall longer than the progress bound breaks the chain even with every record present.
        stalled = MemoryStore()
        run_session(stalled, start_minute=0, minutes=[*range(20), *range(25, 60)])
        result = derive(stalled, end=60)
        for minute in range(20, 25):
            self.assertNotEqual(STATUS_PROVEN, reason_at(result, minute))

    def test_feed_segment_break_splits_processing(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=[m for m in range(60) if m != 30],
                    bars_for=lambda m: [bar(m, segment=0 if m < 30 else 1)])
        result = derive(store, end=60, seals=[sealed(0, [m for m in range(60) if m != 30])])
        self.assertEqual(UnprovenReasonV1.ALIVE_WITHOUT_DATA_PROOF.value, reason_at(result, 30))
        self.assertEqual(59 * MINUTE, result.proven_micros)

    def test_capture_hours_must_be_complete_by_the_processing_session(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(120))
        result = derive(store, accept=acceptance([HourVerdictV1.COMPLETE, HourVerdictV1.NO_PROVEN_COVERAGE]))
        self.assertEqual(CAPTURE_HOUR_PREFIX + "NO_PROVEN_COVERAGE", reason_at(result, 90))
        self.assertEqual(60 * MINUTE, result.proven_micros)
        not_judged = derive(store, accept=acceptance(hours=1))
        self.assertEqual(UnprovenReasonV1.CAPTURE_HOUR_NOT_JUDGED.value, reason_at(not_judged, 90))
        other = derive(store, accept=acceptance(sessions=[CAPTURE[2], CAPTURE[1]]))
        self.assertEqual(UnprovenReasonV1.CAPTURE_HOUR_SESSION_MISMATCH.value, reason_at(other, 10))
        no_symbol = replace(acceptance(), symbols=(SymbolHoursV1("ETHUSDT", acceptance().symbols[0].segments),))
        self.assertEqual(UnprovenReasonV1.CAPTURE_ACCEPTANCE_DOES_NOT_COVER_SYMBOL.value,
                         reason_at(derive(store, accept=no_symbol), 10))

    def test_sealed_evidence_must_agree(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(120))
        unsealed = derive(store, seals=[FULL_SEALS[0]])
        self.assertEqual(UnprovenReasonV1.BAR_NOT_SEALED.value, reason_at(unsealed, 90))
        conflict = derive(store, seals=[sealed(0, range(60), overrides={7: "forged"}), FULL_SEALS[1]])
        for minute in (0, 7, 59):  # the whole hour is refused
            self.assertEqual(UnprovenReasonV1.SEAL_CONFLICT.value, reason_at(conflict, minute))
        self.assertEqual(STATUS_PROVEN, reason_at(conflict, 60))

    def test_holdout_gate_late_bars_and_other_candidates(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(60), gate=Gate(lambda micros: micros >= minute_micros(30)))
        result = derive(store, end=60)
        self.assertEqual(UnprovenReasonV1.CANDIDATE_NOT_EVALUATED.value, reason_at(result, 10))
        self.assertEqual(30 * MINUTE, result.proven_micros)

        late = MemoryStore()
        run_session(late, start_minute=0, minutes=range(60),
                    bars_for=lambda m: [bar(m, completed_after_seconds=-200 if m == 40 else 0.5)])
        result = derive(late, end=60)
        self.assertEqual(UnprovenReasonV1.BAR_PROCESSED_LATE.value, reason_at(result, 40))

        watched_other = MemoryStore()
        run_session(watched_other, start_minute=0, minutes=range(60), trial=OTHER_TRIAL)
        result = derive(watched_other, end=60)
        self.assertEqual(0, result.proven_micros)
        self.assertEqual([], result.payload["runner_sessions"])

    def test_tampered_or_incomplete_sessions_are_refused_whole(self) -> None:
        store = MemoryStore()
        recorder = run_session(store, start_minute=0, minutes=range(30))
        key = f"runner-liveness:{recorder.runner_session_id}:5"
        tampered = MemoryStore()
        tampered.rows = {k: dict(v) for k, v in store.rows.items()}
        tampered.rows[key] = {"content_hash": store.rows[key]["content_hash"],
                              "identity": {**store.rows[key]["identity"], "warmup_bars_total": 7}}
        result = derive(tampered, end=30)
        self.assertEqual(0, result.proven_micros)
        self.assertEqual([SessionRefusalV1.CONTENT_HASH_DOES_NOT_REDERIVE.value],
                         [item["reason"] for item in result.payload["refused_sessions"]])

        headless = MemoryStore()
        headless.rows = {k: v for k, v in store.rows.items() if not k.endswith(":0")}
        self.assertEqual(SessionRefusalV1.RUNNER_SESSION_WITHOUT_START.value,
                         derive(headless, end=30).payload["refused_sessions"][0]["reason"])

        rechained = MemoryStore()
        rechained.rows = dict(store.rows)
        forged = RunnerProgressV1.build({**store.rows[key]["identity"], "previous_progress_hash": "0" * 64})
        rechained.rows[key] = {"content_hash": forged.content_hash, "identity": dict(forged.identity)}
        self.assertEqual(SessionRefusalV1.HASH_CHAIN_BROKEN.value,
                         derive(rechained, end=30).payload["refused_sessions"][0]["reason"])

    def test_bounds_and_window_are_explicit(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(5))
        with self.assertRaises(RunnerLivenessError):
            derive(store, end=5, max_processing_lag_nanos=0)
        with self.assertRaises(TypeError):
            derive_runner_liveness_v1(store.all(), study_id=str(STUDY), trial_id=TRIAL, symbol=SYMBOL,  # type: ignore[call-arg]
                                      window_start=at(0), window_end=at(5), acceptance=acceptance(), sealed=[])
        with self.assertRaises(RunnerLivenessError):
            derive_runner_liveness_v1(store.all(), study_id=str(STUDY), trial_id=TRIAL, symbol=SYMBOL,
                                      window_start=at(0) + timedelta(seconds=1), window_end=at(5),
                                      acceptance=acceptance(), sealed=[], **BOUNDS)
        result = derive(store, end=5)
        self.assertEqual(BOUNDS["max_progress_gap_nanos"], result.payload["bounds"]["max_progress_gap_nanos"])
        self.assertEqual("PROVEN_FORWARD_PROCESSING_LOWER_BOUND_NOT_VALIDATED", result.payload["claim"])

    def test_hourly_segment_boundary_minute_stays_unproven(self) -> None:
        store = MemoryStore()
        # The new hourly segment proves no bar for its first minute, live or sealed.
        run_session(store, start_minute=0, minutes=range(120), bars_for=lambda m: [] if m == 60 else [bar(m)])
        result = derive(store, seals=[FULL_SEALS[0], sealed(1, range(61, 120))])
        self.assertEqual(UnprovenReasonV1.NOT_SEALED.value, reason_at(result, 60))
        self.assertEqual(119 * MINUTE, result.proven_micros)

    def test_malformed_binding_is_refused(self) -> None:
        store = MemoryStore()
        recorder = run_session(store, start_minute=0, minutes=range(5))
        key = f"runner-liveness:{recorder.runner_session_id}:0"
        identity = dict(store.rows[key]["identity"])
        identity["binding"] = {"symbol": SYMBOL}
        identity["binding_hash"] = identity_hash_v1(identity["binding"])
        forged = RunnerProgressV1.build(identity)
        store.rows[key] = {"content_hash": forged.content_hash, "identity": dict(forged.identity)}
        result = derive(store, end=5)
        self.assertEqual(0, result.proven_micros)
        self.assertEqual(1, len(result.payload["refused_sessions"]))

    def test_two_concurrent_runners_disagreeing_prove_nothing_there(self) -> None:
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(60), session=uuid5(NS, "a"))
        run_session(store, start_minute=0, minutes=range(60), session=uuid5(NS, "b"),
                    bars_for=lambda m: [bar(m, manifest="other" if m == 15 else None)])
        result = derive(store, end=60)
        self.assertEqual(UnprovenReasonV1.RUNNER_EVIDENCE_CONFLICT.value, reason_at(result, 15))
        self.assertEqual(STATUS_PROVEN, reason_at(result, 14))



if __name__ == "__main__":
    unittest.main()
