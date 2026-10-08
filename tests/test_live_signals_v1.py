"""Phase R8 core -- live bars equal sealed-path bars; live signals equal a replay of the same bars."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from tests.test_first_party_t4_v1 import BASE, SECOND, FixtureArchive, _standard_samples
from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from tests.test_strategy_sdk_v1 import _walk
from trade_platform.first_party_capture_authority_v1 import first_party_bybit_capture_contract_v1
from trade_platform.first_party_t4_normalization_v1 import T4MinuteBarV1
from trade_platform.live_signals_v1 import (
    LiveBarFeedV1,
    LiveBarV1,
    LiveSignalsError,
    LiveStrategyRunnerV1,
    WatchedCandidateV1,
)
from trade_platform.strategy_sdk_v1 import FAMILIES_V1, BarsV1

CONTRACT = first_party_bybit_capture_contract_v1()


def _bar(minute: int, close: Decimal, previous: Decimal) -> T4MinuteBarV1:
    high, low = max(previous, close) + Decimal("0.5"), min(previous, close) - Decimal("0.5")
    open_micros = int(datetime(2026, 10, 9, tzinfo=UTC).timestamp() * 1_000_000) + minute * 60_000_000
    return T4MinuteBarV1(open_micros, previous, high, low, close, Decimal("1"), Decimal("1"), 2, 0, 0, "f", "l",
                         open_micros, open_micros + 60_000_000, False, False, f"r{minute}", f"m{minute}")


def _live_bars(closes: list[Decimal], *, segment_break: int | None = None) -> list[LiveBarV1]:
    out, previous, session = [], closes[0], uuid4()
    for minute, close in enumerate(closes):
        bar = _bar(minute, close, previous)
        segment = 1 if segment_break is not None and minute >= segment_break else 0
        out.append(LiveBarV1("BTCUSDT", segment, session, bar, (bar.bar_open_micros + 60_000_000) * 1000 + 5))
        previous = close
    return out


class LiveFeedParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="live-"))
        self.capture = self.temp / "capture"
        self.archive = FixtureArchive(self.capture)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_live_bars_are_exactly_the_sealed_path_bars_and_polling_is_idempotent(self) -> None:
        from trade_platform.first_party_t4_seal_v1 import (
            discover_t4_segments_v1,
            normalize_t4_segment_v1,
        )

        self.archive.session(windows=[(BASE, BASE + 300 * SECOND)], samples=_standard_samples(320))
        feed = LiveBarFeedV1(self.capture, CONTRACT)
        live = feed.poll()
        self.assertEqual([], feed.poll())  # nothing new: idempotent
        (plan,) = discover_t4_segments_v1(self.capture).segments
        sealed, _, _ = normalize_t4_segment_v1(plan)
        live_by_open = {bar.bar.bar_open_micros: bar.bar for bar in live}
        self.assertTrue(sealed.bars)
        for bar in sealed.bars:
            mine = live_by_open[bar.bar_open_micros]
            self.assertEqual((bar.open_price, bar.high_price, bar.low_price, bar.close_price, bar.trade_count,
                              bar.trade_manifest_hash),
                             (mine.open_price, mine.high_price, mine.low_price, mine.close_price, mine.trade_count,
                              mine.trade_manifest_hash))
        self.assertEqual([], feed.refusals)


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

    def test_signals_are_exactly_the_target_changes_of_a_replay(self) -> None:
        closes = _walk(600, seed=21)
        bars = _live_bars(closes)
        runner = LiveStrategyRunnerV1([self._candidate()], clock=lambda: datetime(2030, 1, 1, tzinfo=UTC))
        signals = []
        for bar in bars:  # one bar at a time, as live
            signals.extend(runner.on_bars([bar]))
        family = FAMILIES_V1["mean_reversion_z"]
        params = self._candidate().parameters
        replay = family.targets_decimal(BarsV1.from_rows([b.row() for b in bars]), params)
        expected = [i for i in range(1, len(replay)) if replay[i] != replay[i - 1]]
        self.assertEqual(expected, [
            (int(s.identity["evidence"]["bar_open_micros"]) - bars[0].bar.bar_open_micros) // 60_000_000
            for s in signals])
        for signal in signals:
            self.assertEqual("NOT_VALIDATED_RESEARCH_WATCH", signal.identity["claim"])
            self.assertIn("z", signal.identity["explanation"])
            self.assertGreaterEqual(signal.decided_at, datetime(2030, 1, 1, tzinfo=UTC))
        self.assertTrue(signals)

    def test_a_broken_segment_resets_the_window(self) -> None:
        closes = _walk(200, seed=4)
        runner = LiveStrategyRunnerV1([self._candidate()])
        for bar in _live_bars(closes, segment_break=150):
            runner.on_bars([bar])
        self.assertEqual(50, len(runner._history["BTCUSDT"]))

    def test_only_research_watch_or_incubating_and_only_this_sdk_version(self) -> None:
        with self.assertRaises(LiveSignalsError):
            WatchedCandidateV1(self.study, self.trial, "BTCUSDT", "VALIDATED", "e" * 64)
        with self.assertRaises(LiveSignalsError):
            WatchedCandidateV1(self.study, str(uuid4()), "BTCUSDT", "INCUBATING", "e" * 64)


def _unused(_: Any, __: timedelta) -> None:
    return None


if __name__ == "__main__":
    unittest.main()
