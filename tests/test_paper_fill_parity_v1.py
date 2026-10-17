"""Live/replay paper-fill parity on a bounded fixture: 90 one-minute bars, five INCUBATING signals.

The candidate (mean reversion, 30-bar lookback) signals at minutes 34, 56, 66, 73 and 83;
each fills two minutes later (the decision lands exactly on the next minute boundary, and
the fill bar is the next *strictly later* one). The sealed replay carries the same bars with
a different, finalized knowledge time (+2 s) in the same minute.
"""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.test_live_signals_v1 import DAY0, _live_bars, opened_gate
from tests.test_paper_incubation_v1 import _run
from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from tests.test_strategy_sdk_v1 import _walk
from trade_platform.live_signals_v1 import LiveBarV1, LiveStrategyRunnerV1, WatchedCandidateV1
from trade_platform.paper_fill_parity_v1 import (
    ASSESSMENT_FAIL,
    ASSESSMENT_INCOMPLETE,
    ASSESSMENT_PASS,
    OUTCOME_DIVERGED,
    OUTCOME_MATCH,
    OUTCOME_UNAVAILABLE,
    FillParityReportV1,
    PaperFillParityError,
    SealedReplayBarV1,
    assess_fill_parity_v1,
)
from trade_platform.paper_incubation_v1 import (
    FILL_STATUS_BAR_NOT_OBSERVED,
    FILL_STATUS_FILLED,
    FILL_STATUS_OPEN_AMBIGUOUS,
    PaperFillV1,
    PaperIncubationEngineV1,
)
from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1

GROSS = gross_cost_policy_v1()
MINUTE = 60_000_000
SEAL = "5" * 64
SIGNAL_MINUTES = (34, 56, 66, 73, 83)
SEALED_KNOWLEDGE_SHIFT = 2_000_000  # the sealed bracketed bound: same minute, a different instant


DAY0_MICROS = int(DAY0.timestamp()) * 1_000_000


def _sealed(bars: list[LiveBarV1]) -> list[SealedReplayBarV1]:
    return [SealedReplayBarV1(bar.symbol, bar.segment, bar.session_id, dataclasses.replace(
        bar.bar, open_market_knowledge_micros=bar.bar.open_market_knowledge_micros + SEALED_KNOWLEDGE_SHIFT,
        complete_market_knowledge_micros=bar.bar.complete_market_knowledge_micros + SEALED_KNOWLEDGE_SHIFT),
        SEAL) for bar in bars]


def _edit(bars: list[LiveBarV1], minute: int, **changes: Any) -> list[LiveBarV1]:
    opened = DAY0_MICROS + minute * MINUTE
    return [dataclasses.replace(bar, bar=dataclasses.replace(bar.bar, **changes))
            if bar.bar.bar_open_micros == opened else bar for bar in bars]


def _drop(bars: list[LiveBarV1], minute: int) -> list[LiveBarV1]:
    return [bar for bar in bars if bar.bar.bar_open_micros != DAY0_MICROS + minute * MINUTE]


class FillParityTests(unittest.TestCase):
    temp: Path
    bars: list[LiveBarV1]
    candidate: WatchedCandidateV1

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = Path(tempfile.mkdtemp(prefix="parity-"))
        study, _ = build_window_and_study(cls.temp, family="mean_reversion_z")
        trial = next(str(t.trial_id) for t in study.trials()
                     if study.parameter_space.typed_point(t.parameters) ==
                     {"lookback_bars": 30, "entry_z": Decimal("1.5"), "exit_z": Decimal("0.5"),
                      "direction": "long_short"})
        cls.candidate = WatchedCandidateV1(study, trial, "BTCUSDT", "INCUBATING", "v" * 64)
        cls.bars = _live_bars(_walk(90, seed=21))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def _live(self, bars: list[LiveBarV1], runners: list[tuple[int, Any]] | None = None
              ) -> tuple[list[Any], list[PaperFillV1]]:
        """Live signals (one runner, or ``(feed_until_minute, clock)`` runners) and their R10 fills."""
        signals: list[Any] = []
        for until, clock in runners or [(len(bars), DAY0)]:
            runner = LiveStrategyRunnerV1([self.candidate], holdout_gate=opened_gate(), clock=lambda c=clock: c)
            for bar in bars:
                if bar.bar.bar_open_micros < DAY0_MICROS + until * MINUTE:
                    signals.extend(runner.on_bars([bar]))
        return signals, _run(PaperIncubationEngineV1(GROSS), signals, bars)

    def _assess(self, live_bars: list[LiveBarV1], replay: list[LiveBarV1], signals: list[Any],
                fills: list[PaperFillV1]) -> FillParityReportV1:
        return assess_fill_parity_v1(candidates=[self.candidate], holdout_gate=opened_gate(), cost_policy=GROSS,
                                     replay_bars=_sealed(replay), live_bars=live_bars, live_signals=signals,
                                     live_fills=fills)

    @staticmethod
    def _signal_obs(report: FillParityReportV1, minute: int) -> Any:
        return next(o for o in report.observations if o.identity["kind"] == "SIGNAL"
                    and o.identity["key"]["signal_bar_open_micros"] == DAY0_MICROS + minute * MINUTE)

    # -- PASS ---------------------------------------------------------------------------

    def test_identical_evidence_passes_with_distinct_knowledge_times(self) -> None:
        signals, fills = self._live(self.bars)
        self.assertEqual([DAY0_MICROS + m * MINUTE for m in SIGNAL_MINUTES],
                         [int(s.identity["evidence"]["bar_open_micros"]) for s in signals])
        report = self._assess(self.bars, self.bars, signals, fills)
        self.assertEqual((ASSESSMENT_PASS, ()), (report.status, report.reasons))
        self.assertEqual(5, report.identity["summary"]["matched_signals_with_both_fills_filled"])
        self.assertEqual({OUTCOME_MATCH}, {o.outcome for o in report.observations})
        knowledge = self._signal_obs(report, 34).identity["facts"]["knowledge"]
        live, sealed = knowledge["live_provisional"], knowledge["sealed_finalized"]
        self.assertEqual("LIVE_PROVISIONAL_NO_LATER_SAMPLE_YET", live["clock_bound"])
        self.assertEqual(SEALED_KNOWLEDGE_SHIFT, sealed["decided_at_micros"] - live["decided_at_micros"])
        self.assertEqual(SEALED_KNOWLEDGE_SHIFT, sealed["fill_open_market_knowledge_micros"]
                         - live["fill_open_market_knowledge_micros"])
        dims = self._signal_obs(report, 34).identity["facts"]["dimensions"]
        self.assertEqual(DAY0_MICROS + 36 * MINUTE, dims["timestamp"]["live"])

    def test_the_report_is_content_addressed_and_order_independent(self) -> None:
        signals, fills = self._live(self.bars)
        first = self._assess(self.bars, self.bars, signals, fills)
        again = assess_fill_parity_v1(candidates=[self.candidate], holdout_gate=opened_gate(), cost_policy=GROSS,
                                      replay_bars=list(reversed(_sealed(self.bars))),
                                      live_bars=list(reversed(self.bars)), live_signals=list(reversed(signals)),
                                      live_fills=list(reversed(fills)))
        self.assertEqual(first.content_hash, again.content_hash)
        self.assertEqual(first.report_id, again.report_id)
        self.assertEqual([o.content_hash for o in first.observations], first.identity["observations"])

    # -- gap ------------------------------------------------------------------------------

    def test_a_capture_gap_on_both_sides_matches_as_an_unobserved_fill_bar(self) -> None:
        gapped = _drop(self.bars, 58)  # the fill bar of the minute-56 signal
        signals, fills = self._live(gapped)
        report = self._assess(gapped, gapped, signals, fills)
        self.assertEqual(ASSESSMENT_PASS, report.status)
        dims = self._signal_obs(report, 56).identity["facts"]["dimensions"]
        self.assertEqual(FILL_STATUS_BAR_NOT_OBSERVED, dims["fill_eligibility"]["live"])
        self.assertIsNone(dims["price"]["live"])  # never a later bar's open
        self.assertFalse(dims["signed_exposure"]["live"]["taken"])

    def test_a_capture_gap_on_the_live_side_only_fails(self) -> None:
        gapped = _drop(self.bars, 58)
        signals, fills = self._live(gapped)
        report = self._assess(gapped, self.bars, signals, fills)
        self.assertEqual(ASSESSMENT_FAIL, report.status)
        self.assertIn("BAR_ABSENT_FROM_LIVE", report.reasons)
        self.assertIn("FILL_ELIGIBILITY_DIVERGED", report.reasons)
        self.assertIn("PRICE_DIVERGED", report.reasons)

    # -- ambiguous open -------------------------------------------------------------------------

    def test_an_ambiguous_open_on_both_sides_is_never_filled_and_matches(self) -> None:
        ambiguous = _edit(self.bars, 68, open_is_sequence_ambiguous=True)  # fill bar of minute 66
        signals, fills = self._live(ambiguous)
        report = self._assess(ambiguous, ambiguous, signals, fills)
        self.assertEqual(ASSESSMENT_PASS, report.status)
        dims = self._signal_obs(report, 66).identity["facts"]["dimensions"]
        self.assertEqual(FILL_STATUS_OPEN_AMBIGUOUS, dims["fill_eligibility"]["replay"])
        self.assertIsNone(dims["price"]["replay"])
        self.assertEqual(4, report.identity["summary"]["matched_signal_fill_statuses"][FILL_STATUS_FILLED])

    def test_an_open_ambiguous_on_one_side_only_fails(self) -> None:
        ambiguous = _edit(self.bars, 68, open_is_sequence_ambiguous=True)
        signals, fills = self._live(ambiguous)
        report = self._assess(ambiguous, self.bars, signals, fills)
        self.assertEqual(ASSESSMENT_FAIL, report.status)
        self.assertEqual(("BAR_DIVERGED", "FILL_ELIGIBILITY_DIVERGED", "PRICE_DIVERGED",
                          "SIGNED_EXPOSURE_DIVERGED"), report.reasons)

    # -- restart ---------------------------------------------------------------------------------

    def test_a_live_restart_gap_is_unavailable_never_a_divergence_or_a_match(self) -> None:
        # Runner A ran through minute 56; runner B started after minute 66 completed (warm-up).
        restarted = DAY0 + timedelta(minutes=67, microseconds=1)
        signals, fills = self._live(self.bars, [(57, DAY0), (len(self.bars), restarted)])
        self.assertEqual([34, 56, 73, 83], [(int(s.identity["evidence"]["bar_open_micros"]) - DAY0_MICROS) // MINUTE
                                            for s in signals])
        report = self._assess(self.bars, self.bars, signals, fills)
        self.assertEqual((ASSESSMENT_INCOMPLETE, ("LIVE_RUNNER_RESTART_GAP",)), (report.status, report.reasons))
        gap = self._signal_obs(report, 66)
        self.assertEqual(OUTCOME_UNAVAILABLE, gap.outcome)
        self.assertIsNone(gap.identity["facts"]["next_live_signal"]["previous_signal_id"])
        self.assertEqual(OUTCOME_MATCH, self._signal_obs(report, 73).outcome)  # warm-up restored the state

    def test_a_missing_signal_inside_an_unbroken_live_chain_fails(self) -> None:
        signals, fills = self._live(self.bars)
        # Drop minute 66 but keep minute 73 naming it: the runner provably ran and emitted nothing.
        kept = [s for s in signals if int(s.identity["evidence"]["bar_open_micros"]) != DAY0_MICROS + 66 * MINUTE]
        dropped = {str(s.signal_id) for s in signals} - {str(s.signal_id) for s in kept}
        report = self._assess(self.bars, self.bars, kept, [f for f in fills
                                                           if f.identity["signal_id"] not in dropped])
        self.assertEqual(ASSESSMENT_FAIL, report.status)
        self.assertEqual(("SIGNAL_ABSENT_LIVE",), report.reasons)
        sequence = [o for o in report.observations if o.identity["kind"] == "LIVE_SEQUENCE"]
        self.assertEqual(["LIVE_SIGNAL_PREDECESSOR_NOT_SUPPLIED"], [r for o in sequence for r in o.reasons])

    # -- unavailable live observation ----------------------------------------------------------

    def test_replay_beyond_the_live_window_is_unavailable_never_a_match(self) -> None:
        live_bars = self.bars[:70]  # live observed through minute 69
        signals, fills = self._live(live_bars)
        report = self._assess(live_bars, self.bars, signals, fills)
        self.assertEqual((ASSESSMENT_INCOMPLETE, ("LIVE_NOT_OBSERVED_AT_BAR",)), (report.status, report.reasons))
        for minute in (73, 83):
            self.assertEqual(OUTCOME_UNAVAILABLE, self._signal_obs(report, minute).outcome)
        self.assertEqual(3, report.identity["summary"]["matched_signals_with_both_fills_filled"])
        # Replay bars past the window are not live-comparable bars either.
        self.assertFalse([o for o in report.observations if o.identity["kind"] == "BAR"
                          and o.identity["key"]["bar_open_micros"] >= DAY0_MICROS + 70 * MINUTE])

    def test_a_pending_live_fill_is_unavailable(self) -> None:
        live_bars = self.bars[:36]  # minute-34 signal decided, its fill bar (36) not yet observed live
        signals, fills = self._live(live_bars)
        self.assertEqual([], fills)
        report = self._assess(live_bars, self.bars[:40], signals, fills)
        self.assertEqual((ASSESSMENT_INCOMPLETE, ("LIVE_FILL_BAR_NOT_YET_OBSERVED", "NO_MATCHED_FILLED_OBSERVATION")),
                         (report.status, report.reasons))
        dims = self._signal_obs(report, 34).identity["facts"]["dimensions"]
        self.assertEqual(OUTCOME_MATCH, dims["target"]["outcome"])
        self.assertEqual(OUTCOME_UNAVAILABLE, dims["price"]["outcome"])

    def test_an_unrecorded_live_fill_is_unavailable(self) -> None:
        signals, fills = self._live(self.bars)
        report = self._assess(self.bars, self.bars, signals, fills[1:])
        self.assertEqual((ASSESSMENT_INCOMPLETE, ("LIVE_FILL_NOT_RECORDED",)), (report.status, report.reasons))

    def test_nothing_to_compare_is_incomplete(self) -> None:
        report = self._assess(self.bars[:30], self.bars[:30], [], [])
        self.assertEqual((ASSESSMENT_INCOMPLETE, ("NO_MATCHED_FILLED_OBSERVATION",)), (report.status, report.reasons))

    def test_live_bars_not_yet_sealed_are_unavailable(self) -> None:
        signals, fills = self._live(self.bars)
        # Sealed through minute 83: the minute-83 signal replays, its fill bar (85) is not sealed yet.
        report = self._assess(self.bars, self.bars[:84], signals, fills)
        self.assertEqual(ASSESSMENT_INCOMPLETE, report.status)
        self.assertEqual(("REPLAY_BAR_NOT_SEALED", "REPLAY_FILL_BAR_NOT_SEALED"), report.reasons)

    # -- FAIL: price and sequence ----------------------------------------------------------------

    def test_a_different_live_fill_price_fails_exactly(self) -> None:
        opened = self.bars[36].bar.open_price
        moved = _edit(self.bars, 36, open_price=opened + Decimal("1e-8"))  # no tolerance
        signals, fills = self._live(moved)
        report = self._assess(moved, self.bars, signals, fills)
        self.assertEqual(("BAR_DIVERGED", "PRICE_DIVERGED"), report.reasons)
        dims = self._signal_obs(report, 34).identity["facts"]["dimensions"]
        self.assertEqual(OUTCOME_DIVERGED, dims["price"]["outcome"])
        self.assertEqual(OUTCOME_MATCH, dims["timestamp"]["outcome"])

    def test_a_first_trade_sequence_mismatch_fails_as_provenance(self) -> None:
        moved = _edit(self.bars, 36, first_trade_reference="seq-other")
        signals, fills = self._live(moved)
        report = self._assess(moved, self.bars, signals, fills)
        self.assertEqual(("BAR_DIVERGED", "PROVENANCE_DIVERGED"), report.reasons)
        bar = next(o for o in report.observations if o.identity["kind"] == "BAR" and o.outcome == OUTCOME_DIVERGED)
        self.assertEqual(["first_trade_reference"], bar.identity["facts"]["differing_fields"])

    def test_a_late_live_decision_moves_the_fill_minute_and_fails(self) -> None:
        # A runner whose clock reads a minute late decides after the next boundary.
        signals, _ = self._live(self.bars)
        late = [dataclasses.replace(s, decided_at=s.decided_at + timedelta(minutes=1)) for s in signals]
        fills = _run(PaperIncubationEngineV1(GROSS), late, self.bars)
        report = self._assess(self.bars, self.bars, late, fills)
        self.assertEqual(ASSESSMENT_FAIL, report.status)
        self.assertIn("TIMESTAMP_DIVERGED", report.reasons)

    # -- refusals ------------------------------------------------------------------------------------

    def test_evidence_that_does_not_rederive_is_refused(self) -> None:
        signals, fills = self._live(self.bars)
        forged = dataclasses.replace(fills[0], identity={**fills[0].identity, "fill_price": "1"})
        with self.assertRaises(PaperFillParityError):
            self._assess(self.bars, self.bars, signals, [forged, *fills[1:]])
        tampered = dataclasses.replace(signals[0], identity={**signals[0].identity, "target_to": 0})
        with self.assertRaises(PaperFillParityError):
            self._assess(self.bars, self.bars, [tampered, *signals[1:]], fills)

    def test_only_incubating_candidates_and_sealed_bars_are_assessed(self) -> None:
        watch = WatchedCandidateV1(self.candidate.study, self.candidate.trial_id, "BTCUSDT", "RESEARCH_WATCH",
                                   "v" * 64)
        with self.assertRaises(PaperFillParityError):
            assess_fill_parity_v1(candidates=[watch], holdout_gate=opened_gate(), cost_policy=GROSS,
                                  replay_bars=[], live_bars=[], live_signals=[], live_fills=[])
        with self.assertRaises(PaperFillParityError):
            SealedReplayBarV1("BTCUSDT", 0, self.bars[0].session_id, self.bars[0].bar, "")
        with self.assertRaises(PaperFillParityError):
            self._assess([self.bars[0], self.bars[0]], self.bars, [], [])


if __name__ == "__main__":
    unittest.main()
