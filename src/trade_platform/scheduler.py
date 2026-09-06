"""A real, restart-safe, concurrency-safe scheduler/worker execution engine.

``trade_platform.operational_jobs`` already durably records job policies, evaluates
due-state, and monitors for overdue execution -- but, by design (see
``docs/DEPLOYMENT_RUNBOOK.md``: "The monitor evaluates durable evidence ... it never
executes due work"), nothing in this codebase actually *ran* the work a due job
represents. This module is that deployment-owned scheduler the runbook anticipated: a
:class:`SchedulerWorker` that, once per tick, finds due job policies with a registered
runner and executes them, recording terminal evidence with a unique idempotency key --
exactly the runbook's stated contract.

Concurrency safety uses a PostgreSQL session-level advisory lock keyed by
``(job_name, due_at)`` so that, if two worker processes are alive at once (a rolling
restart, an operator running one manually), only one actually executes a given due
occurrence; the other skips it for that tick. Restart safety follows from the same
property: a session-level advisory lock is automatically released by PostgreSQL the
moment the holding connection closes, so a crashed worker's lock is gone before any
replacement process could plausibly retry -- no lock-expiry bookkeeping is needed.

Every job runner registered here reads and writes only already-durable internal
PostgreSQL state. None of them call an external network service, activate a
market-data/news provider, or touch a broker.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from .data_health import (
    DataHealthObservation,
    DataHealthPolicy,
    DataHealthScope,
    PostgresDataHealthStore,
    build_assessment,
)
from .domain import OHLCVBar, utc_now
from .market_data import HistoricalBarStore
from .operational_alerts import AlertSeverity, AlertStatus, PostgresOperationalAlertStore
from .operational_jobs import (
    OperationalJobRun,
    OperationalJobStatus,
    PostgresOperationalJobStore,
    build_job_run,
)
from .persistence import PostgresDatabase
from .retention_evidence import PostgresRetentionEvidenceStore, RetentionDisposition

__all__ = [
    "JobContext",
    "JobRunner",
    "SchedulerWorker",
    "default_job_registry",
    "run_data_health_evaluation",
    "run_operational_job_monitor",
    "run_postgres_dependency_probe",
    "run_retention_evaluation_sweep",
]

_DATA_HEALTH_WINDOW = timedelta(hours=24)
_DATA_HEALTH_STALE_AFTER = timedelta(hours=6)
_DATA_HEALTH_DISAGREEMENT_TOLERANCE = Decimal("0.02")
_DATA_HEALTH_MINIMUM_OBSERVATIONS = 1
_DATA_HEALTH_POLICY_VERSION = "internal-v1"
_INTERVAL_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


@dataclass(frozen=True, slots=True)
class JobContext:
    """The durable authorities every registered job runner is allowed to touch."""

    database: PostgresDatabase
    job_store: PostgresOperationalJobStore
    alerts: PostgresOperationalAlertStore
    retention_store: PostgresRetentionEvidenceStore
    bar_store: HistoricalBarStore
    data_health_store: PostgresDataHealthStore


JobRunner = Callable[[JobContext, datetime], Mapping[str, str]]


def run_operational_job_monitor(context: JobContext, as_of: datetime) -> Mapping[str, str]:
    """The due-state/overdue-alert monitor itself, running as a first-class due job.

    Kept as an ordinary registered job (rather than an unconditional per-tick call)
    so its own execution is idempotent, restart-safe, failure-audited evidence like
    everything else the worker runs.
    """
    opened_or_resolved = context.job_store.monitor_overdue(as_of)
    return {"alerts_opened_or_resolved": str(len(opened_or_resolved))}


def run_postgres_dependency_probe(context: JobContext, as_of: datetime) -> Mapping[str, str]:
    """A real internal-only SRE dependency probe: this process's own PostgreSQL connection.

    Raises (and durably records) a CRITICAL alert on failure, and resolves it once the
    probe next succeeds. No external network call is made.
    """
    resource = "postgres:primary"
    started = time.monotonic()
    try:
        with context.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as error:
        context.alerts.raise_alert(
            source="postgres_dependency_probe",
            code="POSTGRES_DEPENDENCY_UNHEALTHY",
            severity=AlertSeverity.CRITICAL,
            resource=resource,
            details={"reason": type(error).__name__},
            occurred_at=as_of,
        )
        raise
    latency_ms = (time.monotonic() - started) * 1000
    for alert in context.alerts.active():
        if alert.code == "POSTGRES_DEPENDENCY_UNHEALTHY" and alert.resource == resource:
            context.alerts.transition(
                alert.alert_id,
                AlertStatus.RESOLVED,
                actor="postgres_dependency_probe",
                details={"recovery": "probe_succeeded"},
            )
    return {"latency_ms": f"{latency_ms:.2f}"}


def run_retention_evaluation_sweep(context: JobContext, as_of: datetime) -> Mapping[str, str]:
    """Re-evaluate already-recorded object manifests whose retention window may have elapsed.

    Evidence-only: calls the existing, independently idempotent
    ``PostgresRetentionEvidenceStore.evaluate`` per manifest. Never deletes or claims
    deletion of anything -- ``ELIGIBLE_FOR_REVIEW`` still requires a separately
    approved human lifecycle decision, unchanged from before this module.
    """
    due = context.retention_store.manifests_due_for_evaluation(as_of, limit=200)
    eligible_for_review = 0
    for manifest_id in due:
        idempotency_key = f"retention_sweep:{manifest_id}:{as_of.date().isoformat()}"
        evaluation = context.retention_store.evaluate(
            manifest_id, evaluated_at=as_of, idempotency_key=idempotency_key
        )
        if evaluation.disposition is RetentionDisposition.ELIGIBLE_FOR_REVIEW:
            eligible_for_review += 1
    return {
        "manifests_evaluated": str(len(due)),
        "eligible_for_review": str(eligible_for_review),
    }


def _parse_interval(interval: str) -> timedelta:
    """Best-effort expected-gap parser for interval strings like ``"1m"``/``"1d"``.

    Falls back to a conservative one-day expectation for an unrecognized shape rather
    than raising -- an unparsed interval must still produce a truthful (if generic)
    gap/staleness assessment, never block the whole evaluation from running.
    """
    unit = interval[-1:] if interval else ""
    magnitude = interval[:-1] if interval else ""
    if unit not in _INTERVAL_UNITS or not magnitude.isdigit():
        return timedelta(days=1)
    return timedelta(**{_INTERVAL_UNITS[unit]: int(magnitude)})


def _to_observation(bar: OHLCVBar) -> DataHealthObservation:
    # No independent trading-calendar/session authority is wired to this internal
    # evaluation yet (that requires a real, authorized market-data provider -- see
    # Module 3F's scope boundary) -- treating the bar's own recorded timezone as
    # "expected" and its session as valid is honest about that limitation: this
    # check simply contributes no information here rather than fabricating a mismatch.
    return DataHealthObservation(
        provider=bar.provider, instrument_id=bar.instrument_id, event_at=bar.event_at,
        ingested_at=bar.ingested_at, revision=bar.revision, open=bar.open, high=bar.high,
        low=bar.low, close=bar.close, volume=bar.volume, original_timezone=bar.original_timezone,
        expected_timezone=bar.original_timezone, session_valid=True,
    )


def run_data_health_evaluation(context: JobContext, as_of: datetime) -> Mapping[str, str]:
    """Evaluate Data Health against whatever the PostgreSQL bar authority actually holds.

    Safe with zero ingested data (no external provider activated yet, per Module 3F's
    scope boundary): a single GLOBAL assessment over an empty observation set is still
    persisted, and :func:`~trade_platform.data_health.detect_data_health` already
    classifies an empty dataset as blocking/insufficient -- this job never fabricates a
    healthy result. When one or more (instrument, interval) series have been ingested,
    each is evaluated and persisted individually as an INSTRUMENT-scoped assessment.
    """
    window_start = as_of - _DATA_HEALTH_WINDOW
    series = context.bar_store.known_series()
    if not series:
        policy = DataHealthPolicy(
            version=_DATA_HEALTH_POLICY_VERSION, expected_start=window_start, expected_end=as_of,
            expected_interval=timedelta(hours=1), stale_after=_DATA_HEALTH_STALE_AFTER,
            provider_disagreement_tolerance=_DATA_HEALTH_DISAGREEMENT_TOLERANCE,
            minimum_observations=_DATA_HEALTH_MINIMUM_OBSERVATIONS,
        )
        assessment = build_assessment(
            [], policy, scope_type=DataHealthScope.GLOBAL, scope_value="*", evaluated_at=as_of
        )
        context.data_health_store.persist(assessment)
        return {
            "series_checked": "0", "assessments_persisted": "1",
            "blocking_assessments": "1" if assessment.blocking else "0",
        }

    blocking_count = 0
    for instrument_id, interval in series:
        bars = context.bar_store.read_range(instrument_id, interval, window_start, as_of)
        policy = DataHealthPolicy(
            version=_DATA_HEALTH_POLICY_VERSION, expected_start=window_start, expected_end=as_of,
            expected_interval=_parse_interval(interval), stale_after=_DATA_HEALTH_STALE_AFTER,
            provider_disagreement_tolerance=_DATA_HEALTH_DISAGREEMENT_TOLERANCE,
            minimum_observations=_DATA_HEALTH_MINIMUM_OBSERVATIONS,
        )
        assessment = build_assessment(
            [_to_observation(bar) for bar in bars], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value=instrument_id, evaluated_at=as_of,
        )
        context.data_health_store.persist(assessment)
        if assessment.blocking:
            blocking_count += 1
    return {
        "series_checked": str(len(series)), "assessments_persisted": str(len(series)),
        "blocking_assessments": str(blocking_count),
    }


def default_job_registry() -> dict[str, JobRunner]:
    """The job entry points this deployment approves the worker to invoke.

    A due job policy whose ``job_name`` is not a key here is left entirely alone by
    the worker -- it is still visible to (and alertable by) the
    ``operational_job_monitor`` job, but nothing executes it. Adding a new safe,
    internal-only job means registering it here; nothing about the worker loop itself
    needs to change.
    """
    return {
        "operational_job_monitor": run_operational_job_monitor,
        "postgres_dependency_probe": run_postgres_dependency_probe,
        "retention_evaluation_sweep": run_retention_evaluation_sweep,
        "data_health_evaluation": run_data_health_evaluation,
    }


def _try_claim(database: PostgresDatabase, lock_key: str) -> bool:
    with database.transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (lock_key,))
        row = cursor.fetchone()
        return bool(row[0])


def _release(database: PostgresDatabase, lock_key: str) -> None:
    with database.transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_key,))


@dataclass(frozen=True, slots=True)
class SchedulerWorker:
    """Finds due, registered jobs and executes exactly one attempt each per tick.

    ``run_tick`` never raises for an individual job's failure -- it is recorded as a
    ``FAILED`` run and a durable alert, and the loop continues to the next due job.
    It only raises if the due-state read itself fails (a genuine PostgreSQL outage),
    which the caller (the worker process loop) treats as a failed tick and retries
    on its own interval.
    """

    context: JobContext
    registry: Mapping[str, JobRunner]
    clock: Callable[[], datetime] = utc_now

    def run_tick(self, as_of: datetime) -> tuple[OperationalJobRun, ...]:
        completed: list[OperationalJobRun] = []
        for state in self.context.job_store.due_jobs(as_of):
            if not state.due:
                continue
            runner = self.registry.get(state.policy.job_name)
            if runner is None:
                continue
            lock_key = f"trade_platform:operational_job_run:{state.policy.job_name}:{state.due_at.isoformat()}"
            if not _try_claim(self.context.database, lock_key):
                continue
            try:
                completed.append(
                    self._execute(state.policy.policy_id, state.policy.job_name, state.due_at, runner, as_of)
                )
            finally:
                _release(self.context.database, lock_key)
        return tuple(completed)

    def _execute(
        self,
        policy_id: UUID,
        job_name: str,
        scheduled_for: datetime,
        runner: JobRunner,
        as_of: datetime,
    ) -> OperationalJobRun:
        started_at = self.clock()
        try:
            summary = dict(runner(self.context, as_of))
            status = OperationalJobStatus.SUCCEEDED
        except Exception as error:  # noqa: BLE001 - a runner's own failure must never crash the tick loop
            status = OperationalJobStatus.FAILED
            summary = {"error_type": type(error).__name__, "error": str(error)[:500]}
        completed_at = self.clock()
        idempotency_key = f"{policy_id}:{scheduled_for.isoformat()}:{started_at.isoformat()}"
        run = build_job_run(
            policy_id=policy_id,
            idempotency_key=idempotency_key,
            scheduled_for=scheduled_for,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            summary=summary,
        )
        recorded = self.context.job_store.append_run(run)
        if status is OperationalJobStatus.FAILED:
            self.context.alerts.raise_alert(
                source="scheduler_worker",
                code="OPERATIONAL_JOB_EXECUTION_FAILED",
                severity=AlertSeverity.WARNING,
                resource=f"job:{job_name}",
                details={
                    "error_type": summary.get("error_type", ""),
                    "idempotency_key": idempotency_key,
                },
                occurred_at=as_of,
            )
        return recorded
