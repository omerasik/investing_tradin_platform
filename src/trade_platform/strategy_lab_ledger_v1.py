"""Phase R4.1 -- PostgreSQL study / trial ledger and resumable trial queue.

``RESEARCH_ONLY``. Persists the content-addressed identities of
:mod:`trade_platform.strategy_lab_study_v1` (migration ``20261006_0053``) and
runs the trial queue the Strategy Lab workers draw from. It evaluates nothing
itself: an evaluator supplied by a later phase computes a trial's metrics, and
this ledger only records, under a lease, which worker produced what.

Idempotence and resume
----------------------
* :meth:`register_study` inserts the study, every planned trial and one queue
  row per trial in one transaction. Resubmitting the same declaration is a
  no-op that returns the stored study; a different declaration can never
  collide because ids are content addresses (a hash collision is reported as a
  conflict, never merged).
* :meth:`claim` takes up to ``limit`` claimable trials in ordinal order with
  ``FOR UPDATE SKIP LOCKED``: ``PENDING`` ones and ``CLAIMED`` ones whose lease
  has expired (a dead worker's claim). Each claim increments the attempt and is
  recorded as an event; a reclaimed lease also records ``LEASE_EXPIRED`` for
  the abandoned attempt. Concurrent workers never receive the same trial.
* :meth:`complete` / :meth:`fail` require the caller to still hold the exact
  lease (owner and attempt, not expired). A worker that lost its lease -- to
  expiry and a reclaim, or to cancellation -- is refused and writes nothing.
* ``SUCCEEDED`` and ``CANCELLED`` are terminal in the database. ``FAILED`` is
  re-queued only by an explicit :meth:`retry_failed`, which is recorded.

Authority
---------
Every result is stored as ``SEARCH_NON_AUTHORITATIVE`` with the cost-policy
slot unset (both enforced by CHECK). Metric values are canonical identity
values (text, int, bool, null) -- a ``float`` is refused, so a binary-float
search figure is never persisted as if it were an exact value; an evaluator
that computes in floating point must render the figures it reports as text
and own that rendering. The result content hash binds the trial content hash,
the outcome, the metrics and the numeric tier -- not the attempt or worker --
so a deterministic evaluator's rerun is comparable byte for byte.

Lease timing uses the ledger's injected clock (UTC). Workers on one host share
it; a multi-host deployment would need the database clock instead.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .persistence import PostgresDatabase
from .strategy_lab_study_v1 import (
    COST_POLICY_UNSET_V1,
    StrategyLabStudyError,
    StudySpecV1,
    identity_hash_v1,
)

RESULT_SCHEMA_VERSION_V1: Final = "strategy-lab-trial-result-v1"
NUMERIC_TIER_SEARCH_NON_AUTHORITATIVE: Final = "SEARCH_NON_AUTHORITATIVE"

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.strategy_lab_ledger_v1")
_WORKER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_REASON: Final = re.compile(r"^[a-z][a-z0-9_:.-]{0,199}$")


class StrategyLabLedgerError(ValueError):
    """Raised on a ledger conflict, a lost lease or an invalid request."""


class TrialStateV1(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TrialOutcomeV1(StrEnum):
    EVALUATED = "EVALUATED"
    #: The strategy refused the parameter point (e.g. fast >= slow). It still
    #: counts toward the study's multiple-testing denominator.
    INADMISSIBLE_PARAMETERS = "INADMISSIBLE_PARAMETERS"


@dataclass(frozen=True, slots=True)
class RegisteredStudyV1:
    study_id: UUID
    content_hash: str
    planned_trial_count: int
    created: bool


@dataclass(frozen=True, slots=True)
class TrialClaimV1:
    trial_id: UUID
    study_id: UUID
    ordinal: int
    trial_content_hash: str
    parameters: Mapping[str, str]
    attempt: int
    worker: str
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class TrialResultV1:
    trial_id: UUID
    study_id: UUID
    attempt: int
    outcome: TrialOutcomeV1
    metrics: Mapping[str, Any]
    result_content_hash: str
    numeric_tier: str
    worker: str
    produced_at: datetime


@dataclass(frozen=True, slots=True)
class StudyProgressV1:
    study_id: UUID
    planned_trial_count: int
    states: Mapping[str, int]
    results: int

    @property
    def finished(self) -> bool:
        open_states = (TrialStateV1.PENDING, TrialStateV1.CLAIMED, TrialStateV1.FAILED)
        return all(self.states.get(state.value, 0) == 0 for state in open_states)


def _worker(value: str) -> str:
    if not isinstance(value, str) or not _WORKER.match(value):
        raise StrategyLabLedgerError("worker_id_invalid")
    return value


def _reason(value: str) -> str:
    if not isinstance(value, str) or not _REASON.match(value):
        raise StrategyLabLedgerError("reason_must_be_a_lowercase_code")
    return value


def _json(raw: object) -> dict[str, Any]:
    value = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    if not isinstance(value, dict):
        raise StrategyLabLedgerError("stored_json_not_an_object")
    return value


def result_content_hash_v1(
    *, trial_content_hash: str, outcome: TrialOutcomeV1, metrics: Mapping[str, Any]
) -> str:
    """Content hash of one trial result; independent of attempt, worker and time."""
    try:
        return identity_hash_v1(
            {
                "schema_version": RESULT_SCHEMA_VERSION_V1,
                "trial_content_hash": trial_content_hash,
                "outcome": outcome.value,
                "numeric_tier": NUMERIC_TIER_SEARCH_NON_AUTHORITATIVE,
                "cost_policy_slot": COST_POLICY_UNSET_V1,
                "metrics": dict(metrics),
            }
        )
    except StrategyLabStudyError as error:
        raise StrategyLabLedgerError(f"metrics_refused:{error}") from error


class PostgresStrategyLabLedgerV1:
    """The R4 study/trial ledger over migration ``20261006_0053``."""

    def __init__(self, database: PostgresDatabase, *, clock: Callable[[], datetime] | None = None) -> None:
        self._database = database
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise StrategyLabLedgerError("ledger_clock_must_be_timezone_aware")
        return now.astimezone(UTC)

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            yield cursor

    @staticmethod
    def _event(cursor: Any, trial_id: UUID, study_id: UUID, attempt: int, kind: str,
               worker: str | None, detail: Mapping[str, Any], at: datetime) -> None:
        cursor.execute(
            "INSERT INTO strategy_lab_trial_events (event_id, trial_id, study_id, attempt, "
            "event_type, worker, detail, occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
            (uuid5(_NAMESPACE, f"event:{trial_id}:{attempt}:{kind}"), trial_id, study_id, attempt,
             kind, worker, json.dumps(dict(detail), sort_keys=True), at),
        )

    # -- registration --------------------------------------------------------

    def register_study(self, spec: StudySpecV1) -> RegisteredStudyV1:
        identity = spec.identity()
        content_hash = spec.content_hash
        study_id = spec.study_id
        planned = spec.planned_trial_count
        now = self._now()
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_studies (study_id, content_hash, schema_version, "
                "strategy_family, identity, planned_trial_count, evaluation_upper_bound_exclusive, "
                "numeric_policy_slot, cost_policy_slot, label, registered_at) VALUES "
                "(%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s) ON CONFLICT (study_id) DO NOTHING "
                "RETURNING study_id",
                (study_id, content_hash, identity["schema_version"], spec.strategy.family,
                 json.dumps(identity, sort_keys=True), planned, spec.evaluation_upper_bound_exclusive,
                 spec.numeric_policy_slot, spec.cost_policy_slot, spec.label, now),
            )
            created = cursor.fetchone() is not None
            if not created:
                cursor.execute(
                    "SELECT content_hash, planned_trial_count FROM strategy_lab_studies WHERE study_id=%s",
                    (study_id,),
                )
                row = cursor.fetchone()
                if row is None or str(row[0]).strip() != content_hash or int(row[1]) != planned:
                    raise StrategyLabLedgerError("study_identity_conflict")
            else:
                trial_rows = []
                queue_rows = []
                for trial in spec.trials():
                    trial_rows.append((trial.trial_id, study_id, trial.content_hash, trial.ordinal,
                                       trial.space_index, json.dumps(dict(trial.parameters), sort_keys=True)))
                    queue_rows.append((trial.trial_id, study_id, trial.ordinal, now))
                cursor.executemany(
                    "INSERT INTO strategy_lab_trials (trial_id, study_id, content_hash, ordinal, "
                    "space_index, parameters) VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                    trial_rows,
                )
                cursor.executemany(
                    "INSERT INTO strategy_lab_trial_queue (trial_id, study_id, ordinal, state, attempt, "
                    "lease_owner, lease_expires_at, updated_at) VALUES (%s,%s,%s,'PENDING',0,NULL,NULL,%s)",
                    queue_rows,
                )
            cursor.execute("SELECT count(*) FROM strategy_lab_trials WHERE study_id=%s", (study_id,))
            stored = int(cursor.fetchone()[0])
        if stored != planned:
            raise StrategyLabLedgerError("stored_trial_count_differs_from_the_plan")
        return RegisteredStudyV1(study_id, content_hash, planned, created)

    # -- queue ---------------------------------------------------------------

    def claim(self, study_id: UUID, *, worker: str, limit: int, lease_seconds: float) -> tuple[TrialClaimV1, ...]:
        _worker(worker)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise StrategyLabLedgerError("claim_limit_must_be_positive")
        if not lease_seconds > 0:
            raise StrategyLabLedgerError("lease_seconds_must_be_positive")
        now = self._now()
        expires = now + timedelta(seconds=lease_seconds)
        with self._cursor() as cursor:
            cursor.execute(
                "WITH picked AS (SELECT trial_id, state AS prior_state, lease_owner AS prior_owner "
                "FROM strategy_lab_trial_queue WHERE study_id=%s AND (state='PENDING' OR "
                "(state='CLAIMED' AND lease_expires_at<=%s)) ORDER BY ordinal LIMIT %s "
                "FOR UPDATE SKIP LOCKED) "
                "UPDATE strategy_lab_trial_queue q SET state='CLAIMED', attempt=q.attempt+1, "
                "lease_owner=%s, lease_expires_at=%s, updated_at=%s FROM picked "
                "WHERE q.trial_id=picked.trial_id "
                "RETURNING q.trial_id, q.ordinal, q.attempt, picked.prior_state, picked.prior_owner",
                (study_id, now, limit, worker, expires, now),
            )
            claimed = cursor.fetchall()
            claims: list[TrialClaimV1] = []
            for trial_id, ordinal, attempt, prior_state, prior_owner in sorted(claimed, key=lambda r: r[1]):
                if prior_state == TrialStateV1.CLAIMED.value:
                    self._event(cursor, trial_id, study_id, attempt - 1, "LEASE_EXPIRED", prior_owner,
                                {"reclaimed_by": worker}, now)
                self._event(cursor, trial_id, study_id, attempt, "CLAIMED", worker,
                            {"lease_expires_at": expires.isoformat()}, now)
                cursor.execute(
                    "SELECT content_hash, parameters FROM strategy_lab_trials WHERE trial_id=%s", (trial_id,)
                )
                content_hash, parameters = cursor.fetchone()
                claims.append(TrialClaimV1(
                    trial_id=trial_id, study_id=study_id, ordinal=int(ordinal),
                    trial_content_hash=str(content_hash).strip(),
                    parameters={str(k): str(v) for k, v in _json(parameters).items()},
                    attempt=int(attempt), worker=worker, lease_expires_at=expires,
                ))
        return tuple(claims)

    def _hold_lease(self, cursor: Any, claim: TrialClaimV1, now: datetime) -> None:
        cursor.execute(
            "SELECT state, attempt, lease_owner, lease_expires_at FROM strategy_lab_trial_queue "
            "WHERE trial_id=%s FOR UPDATE",
            (claim.trial_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise StrategyLabLedgerError("trial_not_queued")
        state, attempt, owner, expires = row
        if state != TrialStateV1.CLAIMED.value or int(attempt) != claim.attempt or owner != claim.worker:
            raise StrategyLabLedgerError("lease_lost")
        if expires <= now:
            raise StrategyLabLedgerError("lease_expired")

    def complete(self, claim: TrialClaimV1, *, outcome: TrialOutcomeV1, metrics: Mapping[str, Any]) -> TrialResultV1:
        if not isinstance(outcome, TrialOutcomeV1):
            raise StrategyLabLedgerError("trial_outcome_unknown")
        result_hash = result_content_hash_v1(
            trial_content_hash=claim.trial_content_hash, outcome=outcome, metrics=metrics
        )
        now = self._now()
        with self._cursor() as cursor:
            self._hold_lease(cursor, claim, now)
            cursor.execute(
                "INSERT INTO strategy_lab_trial_results (trial_id, study_id, attempt, result_content_hash, "
                "numeric_tier, cost_policy_slot, outcome, metrics, worker, produced_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)",
                (claim.trial_id, claim.study_id, claim.attempt, result_hash,
                 NUMERIC_TIER_SEARCH_NON_AUTHORITATIVE, COST_POLICY_UNSET_V1, outcome.value,
                 json.dumps(dict(metrics), sort_keys=True), claim.worker, now),
            )
            cursor.execute(
                "UPDATE strategy_lab_trial_queue SET state='SUCCEEDED', lease_owner=NULL, "
                "lease_expires_at=NULL, updated_at=%s WHERE trial_id=%s",
                (now, claim.trial_id),
            )
            self._event(cursor, claim.trial_id, claim.study_id, claim.attempt, "SUCCEEDED", claim.worker,
                        {"result_content_hash": result_hash, "outcome": outcome.value}, now)
        return TrialResultV1(claim.trial_id, claim.study_id, claim.attempt, outcome, dict(metrics), result_hash,
                             NUMERIC_TIER_SEARCH_NON_AUTHORITATIVE, claim.worker, now)

    def fail(self, claim: TrialClaimV1, *, reason: str) -> None:
        _reason(reason)
        now = self._now()
        with self._cursor() as cursor:
            self._hold_lease(cursor, claim, now)
            cursor.execute(
                "UPDATE strategy_lab_trial_queue SET state='FAILED', lease_owner=NULL, "
                "lease_expires_at=NULL, updated_at=%s WHERE trial_id=%s",
                (now, claim.trial_id),
            )
            self._event(cursor, claim.trial_id, claim.study_id, claim.attempt, "FAILED", claim.worker,
                        {"reason": reason}, now)

    def retry_failed(self, study_id: UUID, *, reason: str) -> int:
        _reason(reason)
        now = self._now()
        with self._cursor() as cursor:
            cursor.execute(
                "UPDATE strategy_lab_trial_queue SET state='PENDING', updated_at=%s "
                "WHERE study_id=%s AND state='FAILED' RETURNING trial_id, attempt",
                (now, study_id),
            )
            rows = cursor.fetchall()
            for trial_id, attempt in rows:
                self._event(cursor, trial_id, study_id, attempt, "RETRY_REQUESTED", None, {"reason": reason}, now)
        return len(rows)

    def cancel_study(self, study_id: UUID, *, reason: str) -> int:
        """Cancel every unfinished trial; completed results stay. Held leases become void."""
        _reason(reason)
        now = self._now()
        with self._cursor() as cursor:
            cursor.execute(
                "UPDATE strategy_lab_trial_queue SET state='CANCELLED', lease_owner=NULL, "
                "lease_expires_at=NULL, updated_at=%s WHERE study_id=%s AND state IN "
                "('PENDING','CLAIMED','FAILED') RETURNING trial_id, attempt",
                (now, study_id),
            )
            rows = cursor.fetchall()
            for trial_id, attempt in rows:
                self._event(cursor, trial_id, study_id, attempt, "CANCELLED", None, {"reason": reason}, now)
        return len(rows)

    # -- read models ---------------------------------------------------------

    def progress(self, study_id: UUID) -> StudyProgressV1:
        with self._cursor() as cursor:
            cursor.execute("SELECT planned_trial_count FROM strategy_lab_studies WHERE study_id=%s", (study_id,))
            row = cursor.fetchone()
            if row is None:
                raise StrategyLabLedgerError("study_not_registered")
            cursor.execute(
                "SELECT state, count(*) FROM strategy_lab_trial_queue WHERE study_id=%s GROUP BY state",
                (study_id,),
            )
            states = {str(state): int(count) for state, count in cursor.fetchall()}
            cursor.execute("SELECT count(*) FROM strategy_lab_trial_results WHERE study_id=%s", (study_id,))
            results = int(cursor.fetchone()[0])
        return StudyProgressV1(study_id, int(row[0]), dict(sorted(states.items())), results)

    def results(self, study_id: UUID) -> tuple[TrialResultV1, ...]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT r.trial_id, r.attempt, r.outcome, r.metrics, r.result_content_hash, r.numeric_tier, "
                "r.worker, r.produced_at FROM strategy_lab_trial_results r "
                "JOIN strategy_lab_trials t ON t.trial_id=r.trial_id WHERE r.study_id=%s ORDER BY t.ordinal",
                (study_id,),
            )
            rows = cursor.fetchall()
        return tuple(
            TrialResultV1(trial_id, study_id, int(attempt), TrialOutcomeV1(outcome), _json(metrics),
                          str(result_hash).strip(), str(tier), str(worker), produced_at)
            for trial_id, attempt, outcome, metrics, result_hash, tier, worker, produced_at in rows
        )

    def events(self, trial_id: UUID) -> tuple[tuple[int, str, str | None], ...]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT attempt, event_type, worker FROM strategy_lab_trial_events WHERE trial_id=%s "
                "ORDER BY occurred_at, attempt, event_type",
                (trial_id,),
            )
            return tuple((int(a), str(e), w) for a, e, w in cursor.fetchall())


__all__ = [
    "NUMERIC_TIER_SEARCH_NON_AUTHORITATIVE",
    "RESULT_SCHEMA_VERSION_V1",
    "PostgresStrategyLabLedgerV1",
    "RegisteredStudyV1",
    "StrategyLabLedgerError",
    "StudyProgressV1",
    "TrialClaimV1",
    "TrialOutcomeV1",
    "TrialResultV1",
    "TrialStateV1",
    "result_content_hash_v1",
]
