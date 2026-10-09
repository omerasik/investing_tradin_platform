from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from trade_platform.strategy_lab_splits_v1 import (
    ObservationSpanV1,
    SplitModeV1,
    StrategyLabSplitError,
    WalkForwardPlanV1,
    purged_walk_forward_v1,
)
from trade_platform.strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1

T0 = datetime(2026, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)


def _obs(hours: int, label_hours: int = 2) -> list[ObservationSpanV1]:
    """One observation per hour, each label known ``label_hours`` after its decision."""
    return [ObservationSpanV1(T0 + i * H, T0 + (i + label_hours) * H) for i in range(hours)]


def _plan(**overrides: object) -> WalkForwardPlanV1:
    values: dict[str, object] = {
        "mode": SplitModeV1.ROLLING,
        "train_duration": 10 * H,
        "test_duration": 5 * H,
        "step": 5 * H,
        "embargo": timedelta(0),
        "first_test_start": T0 + 10 * H,
        "evaluation_end_exclusive": T0 + 30 * H,
    }
    values.update(overrides)
    return WalkForwardPlanV1(**values)  # type: ignore[arg-type]


class PurgedWalkForwardTests(unittest.TestCase):
    def test_rolling_folds_purge_labels_that_reach_the_test_fold(self) -> None:
        result = purged_walk_forward_v1(_obs(30), _plan())
        self.assertEqual([s.fold for s in result.splits], [0, 1, 2, 3])
        first = result.splits[0]
        # Train window [0h, 10h): decisions 0..9; labels at d+2h, so 8 and 9 reach >= 10h -> purged.
        self.assertEqual(first.train_indices, tuple(range(8)))
        self.assertEqual(first.purged_indices, (8, 9))
        self.assertEqual(first.test_indices, (10, 11, 12, 13, 14))
        last = result.splits[-1]
        # Test [25h, 30h): decisions 25..29; labels 27..31h; only those < 30h are test members.
        self.assertEqual(last.test_indices, (25, 26, 27))
        self.assertEqual(last.censored_indices, (28, 29))

    def test_no_train_member_has_a_label_inside_or_after_the_test_start(self) -> None:
        observations = _obs(30, label_hours=3)
        for split in purged_walk_forward_v1(observations, _plan(embargo=2 * H)).splits:
            for index in split.train_indices:
                self.assertLess(observations[index].label_end_at, split.test_start - 2 * H)
            for index in split.test_indices:
                self.assertLess(observations[index].label_end_at, T0 + 30 * H)
            self.assertFalse(set(split.train_indices) & set(split.test_indices))

    def test_embargo_widens_the_purge(self) -> None:
        plain = purged_walk_forward_v1(_obs(30), _plan()).splits[0]
        embargoed = purged_walk_forward_v1(_obs(30), _plan(embargo=3 * H)).splits[0]
        self.assertEqual(embargoed.purged_indices, (5, 6, 7, 8, 9))
        self.assertLess(len(embargoed.train_indices), len(plain.train_indices))

    def test_anchored_mode_expands_from_the_anchor(self) -> None:
        result = purged_walk_forward_v1(
            _obs(30), _plan(mode=SplitModeV1.ANCHORED, train_duration=None, train_anchor=T0))
        self.assertEqual([s.train_start for s in result.splits], [T0] * 4)
        self.assertEqual([len(s.train_indices) for s in result.splits], [8, 13, 18, 23])

    def test_identity_is_deterministic_and_binds_observations_and_plan(self) -> None:
        a = purged_walk_forward_v1(_obs(30), _plan())
        self.assertEqual(a.content_hash, purged_walk_forward_v1(_obs(30), _plan()).content_hash)
        self.assertNotEqual(a.content_hash, purged_walk_forward_v1(_obs(30, label_hours=1), _plan()).content_hash)
        self.assertNotEqual(a.content_hash, purged_walk_forward_v1(_obs(30), _plan(embargo=H)).content_hash)

    def test_empty_folds_fail_closed(self) -> None:
        sparse = [o for i, o in enumerate(_obs(30)) if not 15 <= i < 20]
        with self.assertRaisesRegex(StrategyLabSplitError, "without_test_members:1"):
            purged_walk_forward_v1(sparse, _plan())
        with self.assertRaisesRegex(StrategyLabSplitError, "without_train_members:0"):
            purged_walk_forward_v1(_obs(30, label_hours=20), _plan())

    def test_plan_requires_explicit_coherent_inputs(self) -> None:
        bad = (
            {"train_duration": None},
            {"train_anchor": T0},
            {"mode": SplitModeV1.ANCHORED},
            {"mode": SplitModeV1.ANCHORED, "train_duration": None, "train_anchor": T0 + 10 * H},
            {"step": timedelta(0)},
            {"embargo": -H},
            {"evaluation_end_exclusive": T0 + 12 * H},
            {"evaluation_end_exclusive": UNTOUCHED_HOLDOUT_BOUNDARY_V1 + H},
            {"first_test_start": datetime(2026, 1, 1)},
        )
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(StrategyLabSplitError):
                _plan(**overrides)

    def test_observations_must_be_ordered_and_causal(self) -> None:
        with self.assertRaises(StrategyLabSplitError):
            ObservationSpanV1(T0 + H, T0)
        with self.assertRaises(StrategyLabSplitError):
            purged_walk_forward_v1(list(reversed(_obs(30))), _plan())
        with self.assertRaises(StrategyLabSplitError):
            purged_walk_forward_v1([], _plan())


if __name__ == "__main__":
    unittest.main()
