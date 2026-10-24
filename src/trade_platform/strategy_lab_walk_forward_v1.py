"""Stage C.1.1 -- purged walk-forward evaluation of a Strategy Lab study over development data.

``RESEARCH_ONLY``. Joins the R4.3 time-interval splits
(:mod:`trade_platform.strategy_lab_splits_v1`) to the R4.6 SDK
(:mod:`trade_platform.strategy_sdk_v1`): every planned trial of a registered
study is evaluated once over the development window, each fold *selects* one
trial on its train side by an explicit rule, and that trial is scored on the
fold's test side. The report is content-addressed and recorded append-only
(migration ``20261016_0063``).

Observations
------------
One observation per open-to-open interval ``j`` (``[open_j, open_{j+1})``,
the unit the SDK's returns are measured on), for every bar but the last:

* ``decision_at`` -- the decision instant ``close_i + lag`` of the latest bar
  ``i`` whose decision fills at ``open_j`` (the execution convention's
  :func:`~trade_platform.strategy_sdk_v1.execution_indices_v1`). Where none
  fills at ``open_j`` the earlier decision is carried; before the first fill
  of a segment the position is flat by convention and ``open_j`` is used (no
  information is used there). This depends only on the bars and the OR-5 lag,
  never on a trial, so every trial shares one split.
* ``label_end_at`` -- ``close_{j+1} + lag``: the interval's return needs
  ``open_{j+1}``, which a T2 bar makes known only when that bar is known.

So a train interval whose return becomes known at or after
``test_start - embargo`` is purged, and a test interval whose return is known
only at or after the evaluation end is censored (the split module's rules).

No look-ahead and no holdout
----------------------------
* Bars are cut to those known (close + lag) strictly before the plan's
  ``evaluation_end_exclusive`` *before* any target is computed, so no trial
  ever sees data past the plan end. The plan end may not pass the study's
  evaluation bound, the bound dataset's knowledge bound or the untouched
  holdout (2026-08-20); the split plan refuses the last on its own.
* Targets are causal (bars ``0..i`` only, never across a declared missing
  day); selection on a fold reads train members only.

Fail closed, nothing chosen here
--------------------------------
Every split duration, the embargo, the selection metric and its direction are
operator inputs recorded in the identity; nothing has a default. A fold
without train or test members refuses the whole evaluation (split module). A
fold where no trial has a defined selection metric on its train side is
*recorded* as ``EXCLUDED_NO_ELIGIBLE_TRIAL_ON_TRAIN``, never skipped silently.
A fold whose test side spans a declared missing UTC day is flagged.

Not validation
--------------
Metrics are the float64 SEARCH tier, gross (no verified fee schedule, OR-6;
funding not modelled -- only window crossings are counted). The report is
``WALK_FORWARD_DEVELOPMENT`` / ``SEARCH_NON_AUTHORITATIVE`` /
``GROSS_NON_PROMOTABLE`` by CHECK, cites the study's planned trial count as
its multiple-testing denominator, and promotes nothing. No aggregate
out-of-sample "performance" is computed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import numpy as np

from .persistence import PostgresDatabase
from .strategy_lab_manifest_v1 import TIE_BREAK_V1, RankDirectionV1
from .strategy_lab_splits_v1 import (
    ObservationSpanV1,
    PurgedWalkForwardV1,
    WalkForwardPlanV1,
    purged_walk_forward_v1,
)
from .strategy_lab_study_v1 import StudySpecV1, identity_hash_v1
from .strategy_sdk_v1 import (
    FAMILIES_V1,
    BarsV1,
    execution_indices_v1,
    restrict_to_bound_v1,
)

WALK_FORWARD_SCHEMA_VERSION_V1: Final = "strategy-lab-walk-forward-v1"
EVALUATION_CLASS_V1: Final = "WALK_FORWARD_DEVELOPMENT"
NUMERIC_TIER_V1: Final = "SEARCH_NON_AUTHORITATIVE"
COST_MODE_V1: Final = "GROSS_NON_PROMOTABLE"
REASON_WALK_FORWARD_NOT_VALIDATION: Final = "WALK_FORWARD_DEVELOPMENT_IS_NOT_HOLDOUT_VALIDATION"
FOLD_SELECTED: Final = "SELECTED_ON_TRAIN"
FOLD_EXCLUDED_NO_ELIGIBLE: Final = "EXCLUDED_NO_ELIGIBLE_TRIAL_ON_TRAIN"
FLAG_DECLARED_DAY_GAP_IN_TEST: Final = "DECLARED_DAY_GAP_INSIDE_TEST_FOLD"

#: Metrics :func:`interval_metrics_f64` computes; a selection rule must name one.
WALK_FORWARD_METRICS_V1: Final = frozenset({
    "total_return", "gross_return_sum", "max_drawdown", "break_even_bps_per_side", "exposure",
})
_METRIC: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_US: Final = timedelta(microseconds=1)
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_FUNDING_GRID_US: Final = 8 * 3_600_000_000


class StrategyLabWalkForwardError(ValueError):
    """Raised when a walk-forward evaluation cannot be performed honestly."""


@dataclass(frozen=True, slots=True)
class WalkForwardSelectionRuleV1:
    """How a fold picks its trial on the train side. Both fields are operator inputs."""

    metric: str
    direction: RankDirectionV1

    def __post_init__(self) -> None:
        if not isinstance(self.metric, str) or not _METRIC.match(self.metric) or self.metric not in WALK_FORWARD_METRICS_V1:
            raise StrategyLabWalkForwardError("selection_metric_is_not_a_walk_forward_metric")
        if not isinstance(self.direction, RankDirectionV1):
            raise StrategyLabWalkForwardError("selection_direction_unknown")

    def payload(self) -> dict[str, Any]:
        return {"metric": self.metric, "direction": self.direction.value, "tie_break": TIE_BREAK_V1}


@dataclass(frozen=True, slots=True)
class WalkForwardReportV1:
    study_id: str
    identity: Mapping[str, Any]
    report_hash: str

    @property
    def folds(self) -> list[dict[str, Any]]:
        return list(self.identity["folds"])


def _at(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=int(micros))


def interval_observations_v1(bars: BarsV1, lag_us: int) -> list[ObservationSpanV1]:
    """One :class:`ObservationSpanV1` per open-to-open interval ``0..n-2`` (see the module docstring)."""
    if bars.size < 2:
        raise StrategyLabWalkForwardError("fewer_than_two_bars")
    exec_index = execution_indices_v1(bars, lag_us)
    filled_by: np.ndarray = np.full(bars.size, -1, dtype=np.int64)
    for i in range(bars.size):
        j = int(exec_index[i])
        if j >= 0:
            filled_by[j] = i  # exec_index is non-decreasing: the latest decision wins, as in held_positions_v1
    observations: list[ObservationSpanV1] = []
    carried: int | None = None
    for j in range(bars.size - 1):
        if j == 0 or bars.segment[j] != bars.segment[j - 1]:
            carried = None
        if filled_by[j] >= 0:
            carried = int(bars.close_us[filled_by[j]]) + lag_us
        decision = int(bars.open_us[j]) if carried is None else carried
        observations.append(ObservationSpanV1(_at(decision), _at(int(bars.close_us[j + 1]) + lag_us)))
    return observations


def _text(value: float | None) -> str | None:
    if value is None or not np.isfinite(value):
        return None
    return format(value, ".12g")


def interval_metrics_f64(bars: BarsV1, held: Sequence[int] | np.ndarray, indices: Sequence[int]) -> dict[str, Any]:
    """Gross float64 SEARCH metrics over the given intervals only (in index order).

    Position changes are counted where they happen inside the member set: a
    position carried into the fold is not a trade of the fold. Funding is not
    modelled; held intervals crossing a 00/08/16 UTC boundary are counted.
    """
    if not indices:
        raise StrategyLabWalkForwardError("no_intervals_to_measure")
    idx: np.ndarray = np.asarray(indices, dtype=np.int64)
    h: np.ndarray = np.asarray(held, dtype=float)
    r = bars.open_f[idx + 1] / bars.open_f[idx] - 1.0
    s = h[idx] * r
    previous = np.where(idx > 0, h[np.maximum(idx - 1, 0)], 0.0)
    changes = np.abs(h[idx] - previous)
    turnover = float(changes.sum())
    equity: np.ndarray = np.cumprod(1.0 + s)
    peak = np.maximum.accumulate(np.concatenate(([1.0], equity)))[1:]
    gross_sum = float(s.sum())
    start, end = bars.open_us[idx], bars.open_us[idx + 1]
    crossings = int(np.count_nonzero(((end // _FUNDING_GRID_US) > (start // _FUNDING_GRID_US)) & (h[idx] != 0)))
    return {
        "total_return": _text(float(equity[-1] - 1.0)),
        "gross_return_sum": _text(gross_sum),
        "max_drawdown": _text(float(np.max(1.0 - equity / peak))),
        "trades": int(np.count_nonzero(changes)),
        "turnover": _text(turnover),
        "exposure": _text(float(np.mean(np.abs(h[idx])))),
        "break_even_bps_per_side": _text(gross_sum / turnover * 1e4) if turnover > 0 else None,
        "funding_window_crossings": crossings,
        "intervals": int(idx.size),
    }


def _decimal(raw: object) -> Decimal | None:
    if not isinstance(raw, str):
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _held_decision_indices_v1(bars: BarsV1, lag_us: int) -> np.ndarray:
    """Trial-independent execution schedule, equivalent to SDK held_positions_v1.

    Use the unchanged SDK's strictly-later-open eligibility. The latest decision
    reaching an open wins; carry it only inside its segment and force the final
    interval flat. No target/price arithmetic is performed here. This one local
    O(bars) array replaces every trial's Python pending dict and two bar loops;
    it is never cached across datasets, cuts, lags or calls. The SDK source (and
    therefore strategy, study and trial identities) stays unchanged.
    """
    execution = execution_indices_v1(bars, lag_us)
    decisions = np.flatnonzero(execution >= 0)
    held: np.ndarray = np.full(bars.size, -1, dtype=np.int64)
    np.maximum.at(held, execution[decisions], decisions)
    np.maximum.accumulate(held, out=held)
    held[held < bars.segment_start] = -1
    last = np.concatenate((bars.segment[1:] != bars.segment[:-1], [True]))
    held[last] = -1
    held.setflags(write=False)
    return held


def _positions_from_schedule_v1(targets: np.ndarray, schedule: np.ndarray) -> np.ndarray:
    """Exact -1/0/+1 values in the float dtype already used by fold metrics."""
    held: np.ndarray = np.asarray(targets[schedule], dtype=float)
    held[schedule < 0] = 0.0
    return held


def run_walk_forward_v1(
    study: StudySpecV1, plan: WalkForwardPlanV1, rule: WalkForwardSelectionRuleV1, *, bars: BarsV1,
) -> WalkForwardReportV1:
    """Evaluate every planned trial of ``study`` under ``plan``; select per fold by ``rule``."""
    if not isinstance(rule, WalkForwardSelectionRuleV1):
        raise StrategyLabWalkForwardError("selection_rule_required")
    family = FAMILIES_V1.get(study.strategy.family)
    if family is None or family.spec() != study.strategy:
        raise StrategyLabWalkForwardError("study_strategy_is_not_this_sdk_family_version")
    lag = study.timing_lag
    if lag is None:
        raise StrategyLabWalkForwardError("t2_study_without_the_or_5_timing_policy")
    if len(study.datasets) != 1:
        raise StrategyLabWalkForwardError("walk_forward_takes_one_bound_dataset")
    (binding,) = study.datasets
    end = plan.evaluation_end_exclusive
    if end > study.evaluation_upper_bound_exclusive:
        raise StrategyLabWalkForwardError("plan_end_past_the_study_evaluation_bound")
    if end > binding.knowledge_upper_bound_exclusive:
        raise StrategyLabWalkForwardError("plan_end_past_the_bound_dataset_knowledge")
    lag_us = lag // _US
    # Cut first: no target is ever computed from a bar not known before the plan end.
    bars = restrict_to_bound_v1(bars, end, lag)
    split: PurgedWalkForwardV1 = purged_walk_forward_v1(interval_observations_v1(bars, lag_us), plan)
    schedule = _held_decision_indices_v1(bars, lag_us)

    sign = -1 if rule.direction is RankDirectionV1.HIGHER_IS_BETTER else 1
    candidates: list[list[tuple[Decimal, str, dict[str, Any]]]] = [[] for _ in split.splits]
    ineligible = [0] * len(split.splits)
    trials = list(study.trials())
    inadmissible = 0
    for trial in trials:
        typed = study.parameter_space.typed_point(trial.parameters)
        if family.inadmissible(typed) is not None:
            inadmissible += 1
            continue
        targets, ties = family.targets_f64(bars, typed)
        held = _positions_from_schedule_v1(targets, schedule)
        for k, fold in enumerate(split.splits):
            train_value = _decimal(interval_metrics_f64(bars, held, fold.train_indices)[rule.metric])
            if train_value is None:
                ineligible[k] += 1
                continue
            test = interval_metrics_f64(bars, held, fold.test_indices)
            candidates[k].append((train_value, trial.content_hash, {
                "trial_id": str(trial.trial_id), "trial_content_hash": trial.content_hash,
                "parameters": dict(trial.parameters), "near_tie_decisions": ties, "test_metrics": test,
            }))

    folds: list[dict[str, Any]] = []
    for k, fold in enumerate(split.splits):
        test_segments = {int(bars.segment[i]) for i in fold.test_indices}
        record: dict[str, Any] = {
            "fold": fold.fold,
            "train_start": fold.train_start.isoformat(timespec="microseconds"),
            "test_start": fold.test_start.isoformat(timespec="microseconds"),
            "test_end_exclusive": fold.test_end_exclusive.isoformat(timespec="microseconds"),
            "train_intervals": len(fold.train_indices), "purged_intervals": len(fold.purged_indices),
            "test_intervals": len(fold.test_indices), "censored_intervals": len(fold.censored_indices),
            "eligible_trials": len(candidates[k]), "ineligible_trials": ineligible[k],
            "flags": [FLAG_DECLARED_DAY_GAP_IN_TEST] if len(test_segments) > 1 else [],
        }
        ranked = sorted(candidates[k], key=lambda item: (sign * item[0], item[1]))
        if not ranked:
            record["status"] = FOLD_EXCLUDED_NO_ELIGIBLE
            record["selected"] = None
        else:
            value, _, chosen = ranked[0]
            record["status"] = FOLD_SELECTED
            record["selected"] = {**chosen, "train_metric_value": format(value.normalize(), "f")}
            record["selection_tie"] = len(ranked) > 1 and ranked[1][0] == value
        folds.append(record)

    identity = {
        "schema_version": WALK_FORWARD_SCHEMA_VERSION_V1,
        "evaluation_class": EVALUATION_CLASS_V1,
        "numeric_tier": NUMERIC_TIER_V1,
        "cost_mode": COST_MODE_V1,
        "promotable": False,
        "study_id": str(study.study_id),
        "study_content_hash": study.content_hash,
        "dataset": {"dataset_version_id": str(binding.dataset_version_id), "content_hash": binding.content_hash},
        "timing_lag_micros": lag_us,
        "bars_evaluated": bars.size,
        "last_bar_close_at": _at(int(bars.close_us[-1])).isoformat(timespec="microseconds"),
        "plan": plan.payload(),
        "split_hash": split.content_hash,
        "observations_hash": split.observations_hash,
        "rule": rule.payload(),
        "multiple_testing_trial_count": study.planned_trial_count,
        "trials_evaluated": len(trials) - inadmissible,
        "trials_inadmissible": inadmissible,
        "folds": folds,
        "authority_reasons": [*study.authority().reasons, REASON_WALK_FORWARD_NOT_VALIDATION],
    }
    return WalkForwardReportV1(str(study.study_id), identity, identity_hash_v1(identity))


class PostgresWalkForwardStoreV1:
    """Append-only persistence of walk-forward reports (idempotent by hash)."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            yield cursor

    def record(self, report: WalkForwardReportV1) -> bool:
        identity = report.identity
        if identity_hash_v1(dict(identity)) != report.report_hash:
            raise StrategyLabWalkForwardError("report_hash_mismatch")
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_walk_forward_reports (report_hash, study_id, split_hash, fold_count, "
                "evaluation_class, numeric_tier, cost_mode, evaluation_end_exclusive, identity, recorded_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (report_hash) DO NOTHING "
                "RETURNING report_hash",
                (report.report_hash, report.study_id, identity["split_hash"], len(identity["folds"]),
                 identity["evaluation_class"], identity["numeric_tier"], identity["cost_mode"],
                 datetime.fromisoformat(identity["plan"]["evaluation_end_exclusive"]),
                 json.dumps(dict(identity), sort_keys=True), datetime.now(UTC)),
            )
            return cursor.fetchone() is not None

    def reports(self, study_id: str) -> tuple[WalkForwardReportV1, ...]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT report_hash, identity FROM strategy_lab_walk_forward_reports "
                "WHERE study_id=%s ORDER BY recorded_at, report_hash",
                (study_id,),
            )
            rows = cursor.fetchall()
        out = []
        for report_hash, raw in rows:
            payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            if identity_hash_v1(payload) != str(report_hash).strip():
                raise StrategyLabWalkForwardError("stored_report_hash_mismatch")
            out.append(WalkForwardReportV1(study_id, payload, str(report_hash).strip()))
        return tuple(out)


__all__ = [
    "COST_MODE_V1",
    "EVALUATION_CLASS_V1",
    "FLAG_DECLARED_DAY_GAP_IN_TEST",
    "FOLD_EXCLUDED_NO_ELIGIBLE",
    "FOLD_SELECTED",
    "NUMERIC_TIER_V1",
    "REASON_WALK_FORWARD_NOT_VALIDATION",
    "WALK_FORWARD_METRICS_V1",
    "WALK_FORWARD_SCHEMA_VERSION_V1",
    "PostgresWalkForwardStoreV1",
    "StrategyLabWalkForwardError",
    "WalkForwardReportV1",
    "WalkForwardSelectionRuleV1",
    "interval_metrics_f64",
    "interval_observations_v1",
    "run_walk_forward_v1",
]
