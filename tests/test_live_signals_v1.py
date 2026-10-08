"""Phase R8 core -- live bars equal sealed-path bars; live signals equal a replay of the same bars."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from tests.test_first_party_t4_v1 import BASE, SECOND, FixtureArchive, _standard_samples
from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from tests.test_strategy_sdk_v1 import _walk
from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.first_party_capture_authority_v1 import first_party_bybit_capture_contract_v1
from trade_platform.first_party_t4_normalization_v1 import T4MinuteBarV1
from trade_platform.live_signals_v1 import (
    LiveBarFeedV1,
    LiveBarV1,
    LiveHoldoutGateV1,
    LiveSignalsError,
    LiveStrategyRunnerV1,
    WatchedCandidateV1,
)
from trade_platform.strategy_sdk_v1 import FAMILIES_V1, BarsV1

CONTRACT = first_party_bybit_capture_contract_v1()
DAY0 = datetime(2026, 10, 1, tzinfo=UTC)  # past: the shared test database asserts global invariants


def _bar(minute: int, close: Decimal, previous: Decimal) -> T4MinuteBarV1:
    high, low = max(previous, close) + Decimal("0.5"), min(previous, close) - Decimal("0.5")
    open_micros = int(DAY0.timestamp() * 1_000_000) + minute * 60_000_000
    return T4MinuteBarV1(open_micros, previous, high, low, close, Decimal("1"), Decimal("1"), 2, 0, 0, "f", "l",
                         open_micros, open_micros + 60_000_000, False, False, f"r{minute}", f"m{minute}")


def _live_bars(closes: list[Decimal], *, segment_break: int | None = None, bound: int = 0) -> list[LiveBarV1]:
    out, previous, session = [], closes[0], uuid4()
    for minute, close in enumerate(closes):
        bar = _bar(minute, close, previous)
        segment = 1 if segment_break is not None and minute >= segment_break else 0
        out.append(LiveBarV1("BTCUSDT", segment, session, bar, (bar.bar_open_micros + 60_000_000) * 1000 + 5, bound))
        previous = close
    return out


def opened_gate(end: datetime = DAY0) -> LiveHoldoutGateV1:
    """The current cycle's gate as if its holdout had been opened with ``end`` (a registry-issued token)."""
    cycle = validation.CURRENT_CYCLE_V1
    # Its own preregistration hash: issued openings are process-global, so this fixture must
    # never coincide with a key another test builds as a forgery.
    opening = validation._issue_opening(cycle.cycle_id, cycle.holdout_start, end, "1ive" * 16, "live-test")
    return LiveHoldoutGateV1.for_cycle(cycle, opening)


class LiveFeedParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="live-"))
        self.capture = self.temp / "capture"
        self.archive = FixtureArchive(self.capture)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _assert_live_equals_sealed(self, feed: LiveBarFeedV1) -> list[LiveBarV1]:
        from trade_platform.first_party_t4_seal_v1 import (
            discover_t4_segments_v1,
            normalize_t4_segment_v1,
        )

        live = feed.poll()
        self.assertEqual([], feed.poll())  # nothing new: idempotent
        sealed = {}
        for plan in discover_t4_segments_v1(self.capture).segments:
            output, _, _ = normalize_t4_segment_v1(plan)
            sealed.update({bar.bar_open_micros: bar for bar in output.bars})
        self.assertTrue(sealed)
        self.assertEqual(sorted(sealed), [bar.bar.bar_open_micros for bar in live])  # no extra, none missing
        for item in live:
            bar = sealed[item.bar.bar_open_micros]
            self.assertEqual((bar.open_price, bar.high_price, bar.low_price, bar.close_price, bar.trade_count,
                              bar.trade_manifest_hash),
                             (item.bar.open_price, item.bar.high_price, item.bar.low_price, item.bar.close_price,
                              item.bar.trade_count, item.bar.trade_manifest_hash))
        self.assertEqual([], feed.refusals)
        return live

    def test_live_bars_are_exactly_the_sealed_path_bars_and_polling_is_idempotent(self) -> None:
        self.archive.session(windows=[(BASE, BASE + 300 * SECOND)], samples=_standard_samples(320))
        live = self._assert_live_equals_sealed(LiveBarFeedV1(self.capture, CONTRACT))
        self.assertEqual(1, len({bar.segment for bar in live}))

    def test_a_recorded_interruption_inside_a_session_is_never_bridged(self) -> None:
        # A 15 s reconnect: shorter than the gap tolerance, so only the lifecycle event reveals it.
        self.archive.session(windows=[(BASE, BASE + 150 * SECOND), (BASE + 165 * SECOND, BASE + 400 * SECOND)],
                             samples=_standard_samples(420), interruption_events=True)
        feed = LiveBarFeedV1(self.capture, CONTRACT)
        live = self._assert_live_equals_sealed(feed)
        self.assertEqual(2, len({bar.segment for bar in live}))
        self.assertEqual(1, feed.continuity_breaks)

    def test_an_outage_longer_than_the_tolerance_breaks_the_segment(self) -> None:
        self.archive.session(windows=[(BASE, BASE + 150 * SECOND), (BASE + 260 * SECOND, BASE + 500 * SECOND)],
                             samples=_standard_samples(520))
        feed = LiveBarFeedV1(self.capture, CONTRACT)
        live = self._assert_live_equals_sealed(feed)
        self.assertEqual(2, len({bar.segment for bar in live}))


class LiveRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="live-run-"))
        self.study, _ = build_window_and_study(self.temp, family="mean_reversion_z")
        self.trial = next(str(t.trial_id) for t in self.study.trials()
                          if self.study.parameter_space.typed_point(t.parameters) ==
                          {"lookback_bars": 30, "entry_z": Decimal("1.5"), "exit_z": Decimal("0.5"),
                           "direction": "long_short"})

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _candidate(self, authority: str = "RESEARCH_WATCH") -> WatchedCandidateV1:
        return WatchedCandidateV1(self.study, self.trial, "BTCUSDT", authority, "e" * 64)

    def _run(self, bars: list[LiveBarV1], gate: LiveHoldoutGateV1, start: datetime = DAY0) -> tuple[
            LiveStrategyRunnerV1, list]:
        runner = LiveStrategyRunnerV1([self._candidate()], holdout_gate=gate, clock=lambda: start)
        signals = []
        for bar in bars:  # one bar at a time, as live
            signals.extend(runner.on_bars([bar]))
        return runner, signals

    def test_signals_are_exactly_the_target_changes_of_a_replay(self) -> None:
        bars = _live_bars(_walk(600, seed=21))
        _, signals = self._run(bars, opened_gate())
        family = FAMILIES_V1["mean_reversion_z"]
        replay = family.targets_decimal(BarsV1.from_rows([b.row() for b in bars]), self._candidate().parameters)
        expected = [i for i in range(1, len(replay)) if replay[i] != replay[i - 1]]
        self.assertTrue(signals)
        self.assertEqual(expected, [
            (int(s.identity["evidence"]["bar_open_micros"]) - bars[0].bar.bar_open_micros) // 60_000_000
            for s in signals])
        for signal in signals:
            self.assertEqual("NOT_VALIDATED_RESEARCH_WATCH", signal.identity["claim"])
            self.assertIn("z", signal.identity["explanation"])
            self.assertEqual(DAY0.isoformat(), signal.identity["holdout"]["holdout_end_exclusive"])

    def test_the_decision_instant_is_on_the_venue_knowledge_clock(self) -> None:
        # Host 9.5 s behind the venue: the decision can never precede the bar's market knowledge
        # nor the host reading plus the bound.
        bound = 9_500_000_123
        bars = _live_bars(_walk(600, seed=21), bound=bound)
        _, signals = self._run(bars, opened_gate())
        self.assertTrue(signals)
        start_micros = int(DAY0.timestamp() * 1_000_000)
        for signal in signals:
            decided = int(signal.decided_at.timestamp() * 1_000_000)
            self.assertGreaterEqual(decided, signal.identity["evidence"]["complete_market_knowledge_micros"])
            self.assertGreaterEqual(decided, start_micros + 9_500_001)
            self.assertGreaterEqual(decided, signal.identity["evidence"]["bar_open_micros"] + 60_000_000)

    def test_bars_completed_before_the_runner_started_only_warm_up(self) -> None:
        bars = _live_bars(_walk(600, seed=21))
        runner, signals = self._run(bars, opened_gate(), start=datetime(2030, 1, 1, tzinfo=UTC))
        self.assertEqual([], signals)
        self.assertEqual(len(bars), runner.warmup_bars)

    def test_no_forward_bar_is_evaluated_inside_an_unopened_holdout(self) -> None:
        cycle = validation.CURRENT_CYCLE_V1
        bars = _live_bars(_walk(600, seed=21))
        runner, signals = self._run(bars, LiveHoldoutGateV1.for_cycle(cycle, None))
        self.assertEqual([], signals)
        self.assertEqual(len(bars), runner.held_back_by_holdout)
        # Opened, with an end after the first 300 bars: only bars at or after the end are evaluated.
        end = datetime.fromtimestamp(bars[300].bar.bar_open_micros / 1_000_000, tz=UTC)
        runner, signals = self._run(bars, opened_gate(end))
        self.assertEqual(300, runner.held_back_by_holdout)
        self.assertTrue(signals)
        self.assertTrue(all(s.identity["evidence"]["bar_open_micros"] >= bars[300].bar.bar_open_micros
                            for s in signals))

    def test_the_gate_comes_only_from_an_issued_cycle_and_its_opening(self) -> None:
        with self.assertRaises(LiveSignalsError):
            LiveHoldoutGateV1("cycle-2026-08-20", DAY0, None)
        other = validation._issue_opening("cycle-2026-10-20", datetime(2026, 10, 20, tzinfo=UTC),
                                          datetime(2026, 11, 1, tzinfo=UTC), "p" * 64, "test")
        with self.assertRaises(LiveSignalsError):
            LiveHoldoutGateV1.for_cycle(validation.CURRENT_CYCLE_V1, other)

    def test_a_broken_segment_resets_the_window(self) -> None:
        runner = LiveStrategyRunnerV1([self._candidate()], holdout_gate=opened_gate(), clock=lambda: DAY0)
        for bar in _live_bars(_walk(200, seed=4), segment_break=150):
            runner.on_bars([bar])
        self.assertEqual(50, len(runner._history["BTCUSDT"]))

    def test_authority_comes_only_from_recorded_evidence(self) -> None:
        from tests.test_strategy_lab_validation_v1 import established_rerun
        from trade_platform.live_signals_v1 import watched_from_rerun_v1, watched_from_states_v1

        rerun = established_rerun(self.study, [self.trial])
        watched = watched_from_rerun_v1(self.study, rerun, states=[], symbol="BTCUSDT")
        self.assertEqual([("RESEARCH_WATCH", self.trial)], [(w.authority, w.trial_id) for w in watched])
        failed = established_rerun(self.study, [self.trial], status="FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED")
        with self.assertRaises(LiveSignalsError):
            watched_from_rerun_v1(self.study, failed, states=[], symbol="BTCUSDT")
        states = [{"trial_id": self.trial, "state": "INCUBATING", "evidence_hash": "v" * 64}]
        self.assertEqual(["INCUBATING"], [w.authority for w in watched_from_states_v1(self.study, states,
                                                                                      symbol="BTCUSDT")])
        rejected = [{"trial_id": self.trial, "state": "HOLDOUT_FAILED_REJECTED", "evidence_hash": "v" * 64},
                    {"trial_id": self.trial, "state": "INCUBATING", "evidence_hash": "w" * 64}]
        self.assertEqual([], watched_from_states_v1(self.study, rejected, symbol="BTCUSDT"))
        # A trial with any recorded R6 state is never a RESEARCH_WATCH candidate.
        for recorded in (rejected[:1], states):
            self.assertEqual([], watched_from_rerun_v1(self.study, rerun, states=recorded, symbol="BTCUSDT"))

    def test_only_research_watch_or_incubating_and_only_this_sdk_version(self) -> None:
        with self.assertRaises(LiveSignalsError):
            WatchedCandidateV1(self.study, self.trial, "BTCUSDT", "VALIDATED", "e" * 64)
        with self.assertRaises(LiveSignalsError):
            WatchedCandidateV1(self.study, str(uuid4()), "BTCUSDT", "INCUBATING", "e" * 64)


if __name__ == "__main__":
    unittest.main()
