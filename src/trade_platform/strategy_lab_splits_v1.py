"""Phase R4.3 -- time-interval purged walk-forward splits for Strategy Lab studies.

``RESEARCH_ONLY``. Splits observations by *time*, not by index, using each
observation's decision time and the time its label (outcome) becomes known.
The existing :func:`trade_platform.strategy_validation.purged_walk_forward_splits`
works on positions with fixed purge counts; it cannot see that an observation
decided before a test fold may have an outcome that is only known inside it.
This module can, so it is the split mechanism for studies whose observations
carry R2A decision times.

Rules
-----
* An observation is ``(decision_at, label_end_at)`` with
  ``label_end_at >= decision_at``; decision times are non-decreasing.
* Test fold ``k`` is ``[first_test_start + k * step, ... + test_duration)``;
  folds are generated while the fold ends at or before
  ``evaluation_end_exclusive``, which may never pass the untouched holdout.
* Test members are observations decided inside the fold whose label is known
  strictly before ``evaluation_end_exclusive``; the others are reported as
  *censored* -- their outcome lies beyond the study's knowledge bound.
* Train candidates are observations decided in the train window
  (``ROLLING``: ``[test_start - train_duration, test_start)``; ``ANCHORED``:
  ``[train_anchor, test_start)``). A candidate is *purged* when its label is
  known at or after ``test_start - embargo``: its outcome would overlap the
  test fold (or the embargo before it). Purged indices are reported, never
  silently dropped.
* Every duration is a required input. Nothing here chooses a window, a step,
  an embargo or a horizon; a fold that ends up with no train or no test
  member is refused (fail closed), not skipped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any, Final

from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1, identity_hash_v1

SPLIT_SCHEMA_VERSION_V1: Final = "strategy-lab-purged-walk-forward-v1"


class StrategyLabSplitError(ValueError):
    """Raised when a split plan or its observations are incomplete or incoherent."""


class SplitModeV1(StrEnum):
    ROLLING = "ROLLING"
    ANCHORED = "ANCHORED"


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise StrategyLabSplitError(f"{name}_must_be_timezone_aware")
    return value.astimezone(UTC)


def _positive(value: object, name: str, *, allow_zero: bool = False) -> timedelta:
    if not isinstance(value, timedelta) or value < timedelta(0) or (value == timedelta(0) and not allow_zero):
        raise StrategyLabSplitError(f"{name}_must_be_a_{'nonnegative' if allow_zero else 'positive'}_timedelta")
    return value


def _micros(value: timedelta) -> int:
    return (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds


def _instant(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class ObservationSpanV1:
    decision_at: datetime
    label_end_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_at", _utc(self.decision_at, "decision_at"))
        object.__setattr__(self, "label_end_at", _utc(self.label_end_at, "label_end_at"))
        if self.label_end_at < self.decision_at:
            raise StrategyLabSplitError("label_end_before_decision")


@dataclass(frozen=True, slots=True)
class WalkForwardPlanV1:
    mode: SplitModeV1
    test_duration: timedelta
    step: timedelta
    embargo: timedelta
    first_test_start: datetime
    evaluation_end_exclusive: datetime
    train_duration: timedelta | None = None
    train_anchor: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SplitModeV1):
            raise StrategyLabSplitError("split_mode_unknown")
        _positive(self.test_duration, "test_duration")
        _positive(self.step, "step")
        _positive(self.embargo, "embargo", allow_zero=True)
        object.__setattr__(self, "first_test_start", _utc(self.first_test_start, "first_test_start"))
        end = _utc(self.evaluation_end_exclusive, "evaluation_end_exclusive")
        object.__setattr__(self, "evaluation_end_exclusive", end)
        if end > UNTOUCHED_HOLDOUT_BOUNDARY_V1:
            raise StrategyLabSplitError("evaluation_end_crosses_the_untouched_holdout")
        if self.mode is SplitModeV1.ROLLING:
            if self.train_anchor is not None:
                raise StrategyLabSplitError("rolling_mode_takes_no_train_anchor")
            _positive(self.train_duration, "train_duration")
        else:
            if self.train_duration is not None:
                raise StrategyLabSplitError("anchored_mode_takes_no_train_duration")
            anchor = _utc(self.train_anchor, "train_anchor")
            object.__setattr__(self, "train_anchor", anchor)
            if anchor >= self.first_test_start:
                raise StrategyLabSplitError("train_anchor_must_precede_the_first_test")
        if self.first_test_start + self.test_duration > end:
            raise StrategyLabSplitError("no_complete_test_fold_before_the_evaluation_end")

    def payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "test_duration_micros": _micros(self.test_duration),
            "step_micros": _micros(self.step),
            "embargo_micros": _micros(self.embargo),
            "first_test_start": _instant(self.first_test_start),
            "evaluation_end_exclusive": _instant(self.evaluation_end_exclusive),
            "train_duration_micros": None if self.train_duration is None else _micros(self.train_duration),
            "train_anchor": None if self.train_anchor is None else _instant(self.train_anchor),
        }


@dataclass(frozen=True, slots=True)
class PurgedSplitV1:
    fold: int
    train_start: datetime
    test_start: datetime
    test_end_exclusive: datetime
    train_indices: tuple[int, ...]
    purged_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    censored_indices: tuple[int, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "fold": self.fold,
            "train_start": _instant(self.train_start),
            "test_start": _instant(self.test_start),
            "test_end_exclusive": _instant(self.test_end_exclusive),
            "train_indices": list(self.train_indices),
            "purged_indices": list(self.purged_indices),
            "test_indices": list(self.test_indices),
            "censored_indices": list(self.censored_indices),
        }


@dataclass(frozen=True, slots=True)
class PurgedWalkForwardV1:
    plan: WalkForwardPlanV1
    observations_hash: str
    splits: tuple[PurgedSplitV1, ...]

    @property
    def content_hash(self) -> str:
        return identity_hash_v1({
            "schema_version": SPLIT_SCHEMA_VERSION_V1,
            "plan": self.plan.payload(),
            "observations_hash": self.observations_hash,
            "splits": [split.payload() for split in self.splits],
        })


def observations_hash_v1(observations: Sequence[ObservationSpanV1]) -> str:
    return identity_hash_v1({
        "observations": [[_instant(o.decision_at), _instant(o.label_end_at)] for o in observations],
    })


def purged_walk_forward_v1(
    observations: Sequence[ObservationSpanV1], plan: WalkForwardPlanV1
) -> PurgedWalkForwardV1:
    if not observations:
        raise StrategyLabSplitError("no_observations")
    if not all(isinstance(o, ObservationSpanV1) for o in observations):
        raise StrategyLabSplitError("observations_must_be_spans")
    decisions = [o.decision_at for o in observations]
    if any(later < earlier for earlier, later in pairwise(decisions)):
        raise StrategyLabSplitError("decision_times_must_be_non_decreasing")
    end = plan.evaluation_end_exclusive
    splits: list[PurgedSplitV1] = []
    fold = 0
    while True:
        test_start = plan.first_test_start + fold * plan.step
        test_end = test_start + plan.test_duration
        if test_end > end:
            break
        if plan.mode is SplitModeV1.ROLLING and plan.train_duration is not None:
            train_start = test_start - plan.train_duration
        elif plan.mode is SplitModeV1.ANCHORED and plan.train_anchor is not None:
            train_start = plan.train_anchor
        else:
            raise StrategyLabSplitError("split_plan_incoherent")
        purge_from = test_start - plan.embargo
        train: list[int] = []
        purged: list[int] = []
        test: list[int] = []
        censored: list[int] = []
        for index, observation in enumerate(observations):
            if train_start <= observation.decision_at < test_start:
                (purged if observation.label_end_at >= purge_from else train).append(index)
            elif test_start <= observation.decision_at < test_end:
                (test if observation.label_end_at < end else censored).append(index)
        if not train:
            raise StrategyLabSplitError(f"split_fold_without_train_members:{fold}")
        if not test:
            raise StrategyLabSplitError(f"split_fold_without_test_members:{fold}")
        splits.append(PurgedSplitV1(fold, train_start, test_start, test_end, tuple(train), tuple(purged),
                                    tuple(test), tuple(censored)))
        fold += 1
    return PurgedWalkForwardV1(plan, observations_hash_v1(observations), tuple(splits))


__all__ = [
    "SPLIT_SCHEMA_VERSION_V1",
    "ObservationSpanV1",
    "PurgedSplitV1",
    "PurgedWalkForwardV1",
    "SplitModeV1",
    "StrategyLabSplitError",
    "WalkForwardPlanV1",
    "observations_hash_v1",
    "purged_walk_forward_v1",
]
