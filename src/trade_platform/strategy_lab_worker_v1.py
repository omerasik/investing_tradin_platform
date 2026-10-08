"""Phase R4.2 -- bounded, resumable Strategy Lab trial workers.

``RESEARCH_ONLY``. Drains a registered study's trial queue
(:class:`~trade_platform.strategy_lab_ledger_v1.PostgresStrategyLabLedgerV1`)
through an injected evaluator. This module owns *how trials are run* -- claim,
evaluate, record, renew, stop -- and nothing about *what a trial computes*.

The evaluator
-------------
A :class:`TrialEvaluatorV1` receives the study declaration and one trial's
exact typed parameters and returns a :class:`TrialEvaluationV1`: an outcome
and metrics made of canonical identity values (text, int, bool, null). How it
computes them -- and therefore which numeric doctrine it uses -- is the
evaluator's business; the ledger stores every result as
``SEARCH_NON_AUTHORITATIVE`` regardless, and nothing here can make a result
authoritative while OR-3 is open. Nothing here supplies a cost, fee or fill
assumption either: an evaluator that needs one cannot get it from this module.

Failure, stop and resume semantics
----------------------------------
* An evaluator exception fails that trial with reason
  ``evaluator_raised:<exception type>`` and the worker continues; the failure
  is evidence, never swallowed, and the trial is retried only by an explicit
  operator ``retry_failed``. An evaluation that returns invalid metrics (a
  ``float``, say) fails the trial with ``evaluator_returned_invalid_metrics``.
* A lost lease (expiry and reclaim by another worker, or cancellation of the
  study) is not an error of this worker: the trial is skipped and counted.
* ``should_stop`` is consulted before every claim and every trial. A stopped
  worker leaves its unstarted claims to lease expiry, after which any worker
  reclaims them -- so a killed or stopped worker never loses or duplicates a
  result, and rerunning a worker on a finished study is a no-op.
* Leases are renewed before each trial when less than half remains.

:func:`run_study_pool_v1` runs ``workers`` such loops in separate processes,
each with its own database connection; the queue's ``SKIP LOCKED`` claim
guarantees no two receive one trial.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from .persistence import PostgresDatabase
from .strategy_lab_ledger_v1 import (
    PostgresStrategyLabLedgerV1,
    StrategyLabLedgerError,
    TrialClaimV1,
    TrialOutcomeV1,
    result_content_hash_v1,
)
from .strategy_lab_study_v1 import StudySpecV1

#: Reason codes this module writes on a failed trial.
REASON_EVALUATOR_RAISED = "evaluator_raised"
REASON_INVALID_METRICS = "evaluator_returned_invalid_metrics"


@dataclass(frozen=True, slots=True)
class TrialEvaluationV1:
    outcome: TrialOutcomeV1
    metrics: Mapping[str, Any] = field(default_factory=dict)


class TrialEvaluatorV1(Protocol):
    def __call__(self, study: StudySpecV1, parameters: Mapping[str, int | Decimal | str]) -> TrialEvaluationV1: ...


@dataclass(frozen=True, slots=True)
class WorkerReportV1:
    worker: str
    claimed: int
    succeeded: int
    failed: int
    lost_leases: int
    stopped: bool


def _reason_code(error: BaseException) -> str:
    return f"{REASON_EVALUATOR_RAISED}:{type(error).__name__.lower()}"[:200]


def run_trial_worker_v1(
    ledger: PostgresStrategyLabLedgerV1,
    study: StudySpecV1,
    evaluator: TrialEvaluatorV1,
    *,
    worker: str,
    batch_size: int = 8,
    lease_seconds: float = 300.0,
    should_stop: Callable[[], bool] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> WorkerReportV1:
    """Drain ``study`` until nothing is claimable or ``should_stop`` says stop.

    The study is (re-)registered first, which is a no-op for an existing one and
    proves the declaration this worker holds is the one the ledger stores.
    """
    now = clock or (lambda: datetime.now(UTC))
    stop = should_stop or (lambda: False)
    ledger.register_study(study)
    claimed = succeeded = failed = lost = 0
    while not stop():
        batch = ledger.claim(study.study_id, worker=worker, limit=batch_size, lease_seconds=lease_seconds)
        if not batch:
            return WorkerReportV1(worker, claimed, succeeded, failed, lost, stopped=False)
        claimed += len(batch)
        for claim in batch:
            if stop():
                return WorkerReportV1(worker, claimed, succeeded, failed, lost, stopped=True)
            try:
                held = _renew_if_needed(ledger, claim, lease_seconds, now())
                outcome = _evaluate(study, evaluator, held)
                if isinstance(outcome, str):
                    ledger.fail(held, reason=outcome)
                    failed += 1
                else:
                    ledger.complete(held, outcome=outcome.outcome, metrics=outcome.metrics)
                    succeeded += 1
            except StrategyLabLedgerError as error:
                if str(error) not in {"lease_lost", "lease_expired"}:
                    raise
                lost += 1
    return WorkerReportV1(worker, claimed, succeeded, failed, lost, stopped=True)


def _renew_if_needed(
    ledger: PostgresStrategyLabLedgerV1, claim: TrialClaimV1, lease_seconds: float, now: datetime
) -> TrialClaimV1:
    if (claim.lease_expires_at - now).total_seconds() < lease_seconds / 2:
        return ledger.renew(claim, lease_seconds=lease_seconds)
    return claim


def _evaluate(study: StudySpecV1, evaluator: TrialEvaluatorV1, claim: TrialClaimV1) -> TrialEvaluationV1 | str:
    """The evaluation, or the reason code the trial fails with."""
    try:
        evaluation = evaluator(study, study.parameter_space.typed_point(claim.parameters))
    except Exception as error:  # noqa: BLE001 - every evaluator failure is recorded, never swallowed
        return _reason_code(error)
    if not isinstance(evaluation, TrialEvaluationV1) or not isinstance(evaluation.outcome, TrialOutcomeV1):
        return REASON_INVALID_METRICS
    try:
        result_content_hash_v1(trial_content_hash=claim.trial_content_hash, outcome=evaluation.outcome,
                               metrics=evaluation.metrics, cost_policy_slot=study.cost_policy_slot)
    except StrategyLabLedgerError:
        return REASON_INVALID_METRICS
    return evaluation


def _pool_worker(dsn: str, study: StudySpecV1, evaluator: TrialEvaluatorV1, worker: str,
                 batch_size: int, lease_seconds: float) -> WorkerReportV1:
    database = PostgresDatabase(dsn)
    try:
        return run_trial_worker_v1(PostgresStrategyLabLedgerV1(database), study, evaluator, worker=worker,
                                   batch_size=batch_size, lease_seconds=lease_seconds)
    finally:
        database.close()


def run_study_pool_v1(
    dsn: str,
    study: StudySpecV1,
    evaluator: TrialEvaluatorV1,
    *,
    workers: int,
    batch_size: int = 8,
    lease_seconds: float = 300.0,
    worker_prefix: str = "pool",
) -> tuple[WorkerReportV1, ...]:
    """Run ``workers`` worker processes over ``study``; the evaluator must be picklable.

    The study is registered once up front so the processes never race to insert it.
    """
    if isinstance(workers, bool) or not isinstance(workers, int) or not 0 < workers <= (os.cpu_count() or 1):
        raise ValueError("workers_must_be_between_1_and_cpu_count")
    database = PostgresDatabase(dsn)
    try:
        PostgresStrategyLabLedgerV1(database).register_study(study)
    finally:
        database.close()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_pool_worker, dsn, study, evaluator, f"{worker_prefix}-{os.getpid()}-{index}",
                        batch_size, lease_seconds)
            for index in range(workers)
        ]
        return tuple(future.result() for future in futures)


__all__ = [
    "REASON_EVALUATOR_RAISED",
    "REASON_INVALID_METRICS",
    "TrialEvaluationV1",
    "TrialEvaluatorV1",
    "WorkerReportV1",
    "run_study_pool_v1",
    "run_trial_worker_v1",
]
