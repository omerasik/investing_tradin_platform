"""Authorized fixtures: proven slices crossing 01:00, gaps and recorder restarts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from itertools import pairwise
from unittest import TestCase
from unittest.mock import patch

from tests.test_first_party_t4_v1 import BASE, SECOND, _TempRoots
from tests.test_live_signals_v1 import opened_gate
from tests.test_paper_fill_parity_v1 import (
    GROSS,
    MINUTE,
    FillParityTests,
    _sealed,
)
from trade_platform.first_party_t4_seal_v1 import (
    discover_t4_segments_v1,
    plan_t4_replay_slice_v1,
    seal_t4_segment_v1,
    verify_t4_dataset_v1,
)
from trade_platform.paper_fill_parity_v1 import (
    PaperFillParityError,
    _signal_facts,
    assess_fill_parity_v1,
)
from trade_platform.sealed_replay_continuity_v1 import sealed_replay_bars_from_seals_v1

SEALED_AT = datetime(2026, 9, 21, tzinfo=UTC)
LATER = SEALED_AT + timedelta(days=1)


class SealedContinuityTests(_TempRoots):
    def slices(self):
        # Host 00:55 through 01:05, venue 10 s ahead. Independently normalized
        # right slice warms up from the identical raw records near 01:00.
        start = BASE + 45 * 60 * SECOND
        self.archive.session(windows=[(start, start + 600 * SECOND)],
                             samples=[start - 5 * SECOND + n * 60 * SECOND for n in range(12)])
        plan = discover_t4_segments_v1(self.capture).segments[0]
        parent = seal_t4_segment_v1(plan, store=self.store(), sealed_at=SEALED_AT)
        left_plan = plan_t4_replay_slice_v1(parent.identity, start_arrival_nanos=plan.start_arrival_nanos,
                                           end_arrival_nanos_inclusive=start + 390 * SECOND,
                                           capture_root=self.capture)
        right_plan = plan_t4_replay_slice_v1(parent.identity, start_arrival_nanos=start + 300 * SECOND,
                                            end_arrival_nanos_inclusive=plan.end_arrival_nanos,
                                            capture_root=self.capture)
        left = seal_t4_segment_v1(left_plan,
                                  store=self.store(), sealed_at=SEALED_AT)
        right = seal_t4_segment_v1(right_plan,
                                   store=self.store(), sealed_at=SEALED_AT)
        return left, right

    def test_proven_warmup_overlap_preserves_window_across_hour(self):
        seals = self.slices()
        bars = sealed_replay_bars_from_seals_v1(seals, store=self.store(), as_of=LATER)
        self.assertEqual({0}, {bar.segment for bar in bars})
        self.assertEqual(9, len(bars))
        hour = int(datetime(2026, 9, 20, 1, tzinfo=UTC).timestamp()) * 1_000_000
        self.assertIn(hour, {bar.bar.bar_open_micros for bar in bars})
        self.assertEqual(2, len({bar.seal_content_hash for bar in bars}))
        self.assertTrue(all(bar.continuity_hash for bar in bars))
        self.assertTrue(all(a.bar.bar_close_micros == b.bar.bar_open_micros for a, b in pairwise(bars)))
        self.assertEqual(bars, sealed_replay_bars_from_seals_v1(list(reversed(seals)),
                                                              store=self.store(), as_of=LATER))
        rebuilt = [verify_t4_dataset_v1(seal.identity, frame_manifests=seal.frame_manifests,
                                        sealed_at=seal.sealed_at, store=self.store(), capture_root=self.capture)
                   for seal in seals]
        self.assertEqual(bars, sealed_replay_bars_from_seals_v1(rebuilt, store=self.store(), as_of=LATER))

    def test_future_seals_contribute_nothing(self):
        seals = self.slices()
        self.assertEqual((), sealed_replay_bars_from_seals_v1(seals, store=self.store(),
                                                             as_of=SEALED_AT - timedelta(seconds=1)))

    def test_decimal_context_cannot_change_replay_proof(self):
        seals = self.slices()
        expected = sealed_replay_bars_from_seals_v1(seals, store=self.store(), as_of=LATER)
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(expected, sealed_replay_bars_from_seals_v1(seals, store=self.store(), as_of=LATER))

    def test_tampered_seal_and_unreplayed_seal_are_refused(self):
        seal, _ = self.slices()
        for changed in (replace(seal, content_hash="f" * 64), replace(seal, raw_replayed=False)):
            with self.assertRaisesRegex(PaperFillParityError, "intact_raw_replayed"):
                sealed_replay_bars_from_seals_v1([changed], store=self.store(), as_of=LATER)

    def test_missing_minute_resets_history(self):
        seals = self.slices()
        import trade_platform.sealed_replay_continuity_v1 as module
        original = module._sealed_minute_bars

        def missing(seal, store):
            bars = original(seal, store)
            return [bar for bar in bars if bar.bar_open_micros != bars[0].bar_open_micros + 2 * MINUTE]

        # A read changing only the minute frame cannot evade the independent
        # forward proof, before any continuity decision is made.
        with (patch.object(module, "_sealed_minute_bars", side_effect=missing),
              self.assertRaisesRegex(PaperFillParityError, "forward_count_mismatch")):
            sealed_replay_bars_from_seals_v1(seals, store=self.store(), as_of=LATER)

    def test_disjoint_seals_cannot_bridge_missing_market_minutes(self):
        left, right = self.slices()
        plan = discover_t4_segments_v1(self.capture).segments[0]
        right = seal_t4_segment_v1(replace(plan, start_arrival_nanos=BASE + 52 * 60 * SECOND),
                                   store=self.store(), sealed_at=SEALED_AT)
        bars = sealed_replay_bars_from_seals_v1([left, right], store=self.store(), as_of=LATER)
        self.assertEqual({0, 1}, {bar.segment for bar in bars})

    def test_hourly_recorder_restart_never_joins(self):
        start = BASE + 45 * 60 * SECOND
        for low, high in ((start, start + 300 * SECOND), (start + 301 * SECOND, start + 600 * SECOND)):
            self.archive.session(windows=[(low, high)],
                                 samples=[low - 5 * SECOND + n * 60 * SECOND for n in range(7)])
        seals = [seal_t4_segment_v1(plan, store=self.store(), sealed_at=SEALED_AT)
                 for plan in discover_t4_segments_v1(self.capture).segments]
        bars = sealed_replay_bars_from_seals_v1(seals, store=self.store(), as_of=LATER)
        self.assertEqual(2, len({bar.session_id for bar in bars}))
        self.assertEqual({0, 1}, {bar.segment for bar in bars})

    def test_ambiguous_overlapping_sessions_are_refused(self):
        left, _ = self.slices()
        start = BASE + 45 * 60 * SECOND
        session = self.archive.session(windows=[(start, start + 600 * SECOND)],
                                       samples=[start - 5 * SECOND + n * 60 * SECOND for n in range(12)])
        plan = next(plan for plan in discover_t4_segments_v1(self.capture).segments
                    if plan.partition.session_id == session)
        conflicting = seal_t4_segment_v1(plan, store=self.store(), sealed_at=SEALED_AT)
        with self.assertRaisesRegex(PaperFillParityError, "ambiguous_overlapping_minute"):
            sealed_replay_bars_from_seals_v1([left, conflicting], store=self.store(), as_of=LATER)

    def test_sealed_bar_with_future_finalized_knowledge_is_excluded(self):
        self.slices()
        plan = discover_t4_segments_v1(self.capture).segments[0]
        plan = replace(plan, end_arrival_nanos=plan.end_arrival_nanos - 8 * SECOND)
        # Sealing uses host time; finalized knowledge has the independently
        # measured +10 s venue-minus-host bound. Do not equate those clocks.
        sealed_at = datetime.fromtimestamp(plan.end_arrival_nanos / SECOND, tz=UTC)
        seal = seal_t4_segment_v1(plan, store=self.store(), sealed_at=sealed_at)
        bars = sealed_replay_bars_from_seals_v1([seal], store=self.store(), as_of=sealed_at)
        known = int(sealed_at.timestamp()) * 1_000_000
        self.assertTrue(all(bar.bar.complete_market_knowledge_micros <= known for bar in bars))
        all_bars = sealed_replay_bars_from_seals_v1([seal], store=self.store(), as_of=LATER)
        self.assertLess(len(bars), len(all_bars))


class ContinuityParityTests(TestCase):
    # Reuse the established R8/R10 fixture, without rerunning its entire suite.
    setUpClass = classmethod(FillParityTests.setUpClass.__func__)
    tearDownClass = classmethod(FillParityTests.tearDownClass.__func__)
    _live = FillParityTests._live

    def assess(self, replay, bars=None):
        live = self.bars if bars is None else bars
        signals, fills = self._live(live)
        return assess_fill_parity_v1(candidates=[self.candidate], holdout_gate=opened_gate(), cost_policy=GROSS,
                                     replay_bars=replay, live_bars=live, live_signals=signals, live_fills=fills)

    def test_hour_boundary_continuity_and_assessment_hash_are_deterministic(self):
        # Contract-level input from the independently verified adapter retains
        # one strategy segment, even as seal provenance changes at 01:00.
        replay = [replace(bar, seal_content_hash=("a" if i < 60 else "b") * 64,
                          continuity_hash="c" * 64) for i, bar in enumerate(_sealed(self.bars))]
        report = self.assess(replay)
        self.assertEqual("PASS", report.status)
        self.assertEqual(report.content_hash, self.assess(list(reversed(replay))).content_hash)
        self.assertEqual("c" * 64, next(o for o in report.observations if o.identity["kind"] == "BAR")
                         .identity["facts"]["knowledge"]["sealed_finalized"]["continuity_hash"])

    def test_unproven_boundary_is_incomplete_not_false_signal_failure(self):
        replay = [replace(bar, segment=0 if i < 60 else 1, continuity_hash="c" * 64)
                  for i, bar in enumerate(_sealed(self.bars))]
        report = self.assess(replay)
        self.assertEqual("INCOMPLETE", report.status)
        self.assertIn("REPLAY_WINDOW_NOT_SEALED", report.reasons)
        self.assertNotIn("SIGNAL_ABSENT_REPLAY", report.reasons)

    def test_missing_sealed_minute_is_incomplete_and_does_not_carry_window(self):
        replay = [replace(bar, segment=0 if i < 60 else 1, continuity_hash="c" * 64)
                  for i, bar in enumerate(_sealed(self.bars)) if i != 60]
        report = self.assess(replay)
        self.assertEqual("INCOMPLETE", report.status)
        self.assertIn("REPLAY_BAR_NOT_SEALED", report.reasons)

    def test_exact_price_difference_remains_failure(self):
        replay = [replace(bar, continuity_hash="c" * 64) for bar in _sealed(self.bars)]
        replay[60] = replace(replay[60], bar=replace(replay[60].bar,
                                                   open_price=replay[60].bar.open_price
                                                   + Decimal("0.000000000000000001")))
        self.assertEqual("FAIL", self.assess(replay).status)

    def test_sealed_decimal_scale_preserves_exact_explanation_parity(self):
        replay = [replace(bar, bar=replace(bar.bar, close_price=bar.bar.close_price.quantize(Decimal("1e-18"))))
                  for bar in _sealed(self.bars)]
        self.assertEqual("PASS", self.assess(replay).status)

    def test_explanation_numeric_comparison_keeps_text_and_subquantum_differences(self):
        signals, _ = self._live(self.bars)
        identity = signals[0].identity
        explanation = dict(identity["explanation"])
        explanation["close"] = "29993.7"
        left = _signal_facts(dict(identity, explanation=explanation))
        explanation["close"] = "29993.700000000000000000"
        self.assertEqual(left, _signal_facts(dict(identity, explanation=explanation)))
        explanation["close"] = "29993.700000000000000001"
        self.assertNotEqual(left, _signal_facts(dict(identity, explanation=explanation)))
        explanation["close"] = "29993.7"
        explanation["rule"] = str(explanation["rule"]) + "0"
        self.assertNotEqual(left, _signal_facts(dict(identity, explanation=explanation)))
