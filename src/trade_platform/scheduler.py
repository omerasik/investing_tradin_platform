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
from datetime import datetime
from uuid import UUID

from .domain import utc_now
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
    "run_operational_job_monitor",
    "run_postgres_dependency_probe",
    "run_retention_evaluation_sweep",
]


@dataclass(frozen=True, slots=True)
class JobContext:
    """The durable authorities every registered job runner is allowed to touch."""

    database: PostgresDatabase
    job_store: PostgresOperationalJobStore
    alerts: PostgresOperationalAlertStore
    retention_store: PostgresRetentionEvidenceStore


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
