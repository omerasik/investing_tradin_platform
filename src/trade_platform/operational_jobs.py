"""Durable job monitoring and local alert-routing boundaries.

This module records job policy/run evidence and enqueues local alert-delivery
work. It does not schedule processes, call providers, or deliver externally.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, cast
from uuid import UUID, uuid4

from .operational_alerts import (
    AlertSeverity,
    AlertStatus,
    OperationalAlert,
    PostgresOperationalAlertStore,
)
from .persistence import PersistenceError, PostgresDatabase


class OperationalJobError(ValueError):
    pass


class OperationalJobStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class OperationalJobPolicy:
    policy_id: UUID
    job_name: str
    version: str
    interval_seconds: int
    grace_seconds: int
    owner: str
    runbook_uri: str
    approved_by: str
    approved_at: datetime
    enabled: bool
    content_hash: str


@dataclass(frozen=True, slots=True)
class OperationalJobRun:
    run_id: UUID
    policy_id: UUID
    idempotency_key: str
    scheduled_for: datetime
    started_at: datetime
    completed_at: datetime
    status: OperationalJobStatus
    summary: dict[str, str]
    content_hash: str


@dataclass(frozen=True, slots=True)
class OperationalJobDueState:
    policy: OperationalJobPolicy
    last_successful_at: datetime | None
    due_at: datetime
    due: bool
    overdue: bool


@dataclass(frozen=True, slots=True)
class AlertRoutePolicy:
    route_policy_id: UUID
    route_name: str
    version: str
    alert_code: str
    minimum_severity: AlertSeverity
    channel: str
    destination_reference: str
    owner: str
    approved_by: str
    approved_at: datetime
    enabled: bool
    content_hash: str


@dataclass(frozen=True, slots=True)
class AlertOutboxDelivery:
    delivery_id: UUID
    alert_id: UUID
    route_policy_id: UUID
    channel: str
    destination_reference: str
    status: str
    enqueued_at: datetime
    payload: dict[str, str]


def build_job_policy(
    *,
    job_name: str,
    version: str,
    interval: timedelta,
    grace: timedelta,
    owner: str,
    runbook_uri: str,
    approved_by: str,
    approved_at: datetime,
    enabled: bool = True,
) -> OperationalJobPolicy:
    interval_seconds = int(interval.total_seconds())
    grace_seconds = int(grace.total_seconds())
    payload = {
        "job_name": job_name,
        "version": version,
        "interval_seconds": interval_seconds,
        "grace_seconds": grace_seconds,
        "owner": owner,
        "runbook_uri": runbook_uri,
        "approved_by": approved_by,
        "approved_at": approved_at.isoformat(),
        "enabled": enabled,
    }
    policy = OperationalJobPolicy(
        uuid4(),
        job_name,
        version,
        interval_seconds,
        grace_seconds,
        owner,
        runbook_uri,
        approved_by,
        approved_at,
        enabled,
        _hash(payload),
    )
    _validate_job_policy(policy)
    return policy


def build_job_run(
    *,
    policy_id: UUID,
    idempotency_key: str,
    scheduled_for: datetime,
    started_at: datetime,
    completed_at: datetime,
    status: OperationalJobStatus,
    summary: dict[str, str],
) -> OperationalJobRun:
    payload = {
        "policy_id": str(policy_id),
        "idempotency_key": idempotency_key,
        "scheduled_for": scheduled_for.isoformat(),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "status": status.value,
        "summary": summary,
    }
    run = OperationalJobRun(
        uuid4(),
        policy_id,
        idempotency_key,
        scheduled_for,
        started_at,
        completed_at,
        status,
        dict(summary),
        _hash(payload),
    )
    _validate_job_run(run)
    return run


def evaluate_job_due(
    policy: OperationalJobPolicy,
    *,
    last_successful_at: datetime | None,
    as_of: datetime,
) -> OperationalJobDueState:
    _validate_job_policy(policy)
    _aware(as_of, "job_due_time_must_be_timezone_aware")
    if last_successful_at is not None:
        _aware(last_successful_at, "job_success_time_must_be_timezone_aware")
        if last_successful_at < policy.approved_at or last_successful_at > as_of:
            raise OperationalJobError("invalid_job_success_time")
    base = policy.approved_at if last_successful_at is None else last_successful_at
    due_at = base + timedelta(seconds=policy.interval_seconds)
    due = policy.enabled and as_of >= due_at
    overdue = due and as_of > due_at + timedelta(seconds=policy.grace_seconds)
    return OperationalJobDueState(policy, last_successful_at, due_at, due, overdue)


def build_alert_route_policy(
    *,
    route_name: str,
    version: str,
    alert_code: str,
    minimum_severity: AlertSeverity,
    destination_reference: str,
    owner: str,
    approved_by: str,
    approved_at: datetime,
    enabled: bool = True,
) -> AlertRoutePolicy:
    payload = {
        "route_name": route_name,
        "version": version,
        "alert_code": alert_code,
        "minimum_severity": minimum_severity.value,
        "channel": "LOCAL_OUTBOX",
        "destination_reference": destination_reference,
        "owner": owner,
        "approved_by": approved_by,
        "approved_at": approved_at.isoformat(),
        "enabled": enabled,
    }
    policy = AlertRoutePolicy(
        uuid4(),
        route_name,
        version,
        alert_code,
        minimum_severity,
        "LOCAL_OUTBOX",
        destination_reference,
        owner,
        approved_by,
        approved_at,
        enabled,
        _hash(payload),
    )
    _validate_route_policy(policy)
    return policy


class PostgresOperationalJobStore:
    """PostgreSQL authority for evidence-only job monitoring and local routing."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        alerts: PostgresOperationalAlertStore,
    ) -> None:
        self._database = database
        self._alerts = alerts

    def append_policy(self, policy: OperationalJobPolicy) -> OperationalJobPolicy:
        _validate_job_policy(policy)
        if _job_policy_hash(policy) != policy.content_hash:
            raise OperationalJobError("job_policy_hash_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT policy_id,job_name,version,interval_seconds,grace_seconds,owner,"
                    "runbook_uri,approved_by,approved_at,enabled,content_hash FROM "
                    "operational_job_policy_versions WHERE job_name=%s AND version=%s",
                    (policy.job_name, policy.version),
                )
                row = cursor.fetchone()
                if row is not None:
                    recovered = self._policy_from_row(row)
                    if recovered.content_hash != policy.content_hash:
                        raise OperationalJobError("job_policy_version_conflict")
                    return recovered
                cursor.execute(
                    "INSERT INTO operational_job_policy_versions VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        policy.policy_id,
                        policy.job_name,
                        policy.version,
                        policy.interval_seconds,
                        policy.grace_seconds,
                        policy.owner,
                        policy.runbook_uri,
                        policy.approved_by,
                        policy.approved_at,
                        policy.enabled,
                        policy.content_hash,
                    ),
                )
                return policy
        except (OperationalJobError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("operational_job_policy_persistence_uncertain") from error

    def append_run(self, run: OperationalJobRun) -> OperationalJobRun:
        _validate_job_run(run)
        if _job_run_hash(run) != run.content_hash:
            raise OperationalJobError("job_run_hash_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT run_id,policy_id,idempotency_key,scheduled_for,started_at,completed_at,"
                    "status,summary,content_hash FROM operational_job_runs WHERE idempotency_key=%s",
                    (run.idempotency_key,),
                )
                row = cursor.fetchone()
                if row is not None:
                    recovered = self._run_from_row(row)
                    if recovered.content_hash != run.content_hash:
                        raise OperationalJobError("job_run_idempotency_conflict")
                    return recovered
                cursor.execute(
                    "INSERT INTO operational_job_runs VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
                    (
                        run.run_id,
                        run.policy_id,
                        run.idempotency_key,
                        run.scheduled_for,
                        run.started_at,
                        run.completed_at,
                        run.status.value,
                        json.dumps(run.summary, sort_keys=True),
                        run.content_hash,
                    ),
                )
                return run
        except (OperationalJobError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("operational_job_run_persistence_uncertain") from error

    def append_route_policy(self, policy: AlertRoutePolicy) -> AlertRoutePolicy:
        _validate_route_policy(policy)
        if _route_policy_hash(policy) != policy.content_hash:
            raise OperationalJobError("alert_route_policy_hash_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT route_policy_id,route_name,version,alert_code,minimum_severity,channel,"
                    "destination_reference,owner,approved_by,approved_at,enabled,content_hash FROM "
                    "operational_alert_route_policy_versions WHERE route_name=%s AND version=%s",
                    (policy.route_name, policy.version),
                )
                row = cursor.fetchone()
                if row is not None:
                    recovered = self._route_policy_from_row(row)
                    if recovered.content_hash != policy.content_hash:
                        raise OperationalJobError("alert_route_policy_version_conflict")
                    return recovered
                cursor.execute(
                    "INSERT INTO operational_alert_route_policy_versions VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        policy.route_policy_id,
                        policy.route_name,
                        policy.version,
                        policy.alert_code,
                        policy.minimum_severity.value,
                        policy.channel,
                        policy.destination_reference,
                        policy.owner,
                        policy.approved_by,
                        policy.approved_at,
                        policy.enabled,
                        policy.content_hash,
                    ),
                )
                return policy
        except (OperationalJobError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("alert_route_policy_persistence_uncertain") from error

    def due_jobs(self, as_of: datetime) -> tuple[OperationalJobDueState, ...]:
        _aware(as_of, "job_due_time_must_be_timezone_aware")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                return self._due_jobs(cursor, as_of)
        except PersistenceError:
            raise
        except Exception as error:
            raise PersistenceError("operational_job_due_read_uncertain") from error

    def monitor_overdue(self, as_of: datetime) -> tuple[OperationalAlert, ...]:
        """Open and locally route overdue alerts; never execute the due jobs."""
        _aware(as_of, "job_due_time_must_be_timezone_aware")
        opened: list[OperationalAlert] = []
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                for state in self._due_jobs(cursor, as_of):
                    if not state.overdue:
                        fingerprint = (
                            "operational_job_monitor:OPERATIONAL_JOB_OVERDUE:"
                            f"job:{state.policy.job_name}"
                        )
                        cursor.execute(
                            "SELECT alert_id FROM operational_alerts WHERE "
                            "payload->>'fingerprint'=%s AND status <> 'RESOLVED'",
                            (fingerprint,),
                        )
                        active = cursor.fetchone()
                        if active is not None:
                            self._alerts.transition_in_transaction(
                                connection,
                                UUID(str(active[0])),
                                AlertStatus.RESOLVED,
                                actor="operational_job_monitor",
                                details={"recovery": "successful_run_or_current_schedule"},
                                occurred_at=as_of,
                            )
                        continue
                    alert = self._alerts.raise_alert_in_transaction(
                        connection,
                        source="operational_job_monitor",
                        code="OPERATIONAL_JOB_OVERDUE",
                        severity=AlertSeverity.WARNING,
                        resource=f"job:{state.policy.job_name}",
                        details={
                            "policy_version": state.policy.version,
                            "due_at": state.due_at.isoformat(),
                            "owner": state.policy.owner,
                            "runbook_uri": state.policy.runbook_uri,
                        },
                        occurred_at=as_of,
                    )
                    self._enqueue_routes(cursor, alert, as_of)
                    opened.append(alert)
                return tuple(opened)
        except (OperationalJobError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("operational_job_monitor_persistence_uncertain") from error

    def route_alert(self, alert_id: UUID, *, enqueued_at: datetime) -> tuple[AlertOutboxDelivery, ...]:
        _aware(enqueued_at, "alert_route_time_must_be_timezone_aware")
        alert = self._alerts.get(alert_id)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                return self._enqueue_routes(cursor, alert, enqueued_at)
        except PersistenceError:
            raise
        except Exception as error:
            raise PersistenceError("alert_route_persistence_uncertain") from error

    def outbox(self, alert_id: UUID) -> tuple[AlertOutboxDelivery, ...]:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT delivery_id,alert_id,route_policy_id,channel,destination_reference,"
                    "status,enqueued_at,payload FROM operational_alert_delivery_outbox "
                    "WHERE alert_id=%s ORDER BY enqueued_at,delivery_id",
                    (alert_id,),
                )
                return tuple(self._delivery_from_row(row) for row in cursor.fetchall())
        except PersistenceError:
            raise
        except Exception as error:
            raise PersistenceError("alert_outbox_read_uncertain") from error

    @staticmethod
    def _due_jobs(cursor: Any, as_of: datetime) -> tuple[OperationalJobDueState, ...]:
        cursor.execute(
            "SELECT DISTINCT ON (job_name) policy_id,job_name,version,interval_seconds,"
            "grace_seconds,owner,runbook_uri,approved_by,approved_at,enabled,content_hash "
            "FROM operational_job_policy_versions WHERE approved_at<=%s "
            "ORDER BY job_name,approved_at DESC,policy_id DESC",
            (as_of,),
        )
        states: list[OperationalJobDueState] = []
        for row in cursor.fetchall():
            policy = PostgresOperationalJobStore._policy_from_row(row)
            cursor.execute(
                "SELECT completed_at FROM operational_job_runs WHERE policy_id=%s "
                "AND status='SUCCEEDED' AND completed_at<=%s "
                "ORDER BY completed_at DESC,run_id DESC LIMIT 1",
                (policy.policy_id, as_of),
            )
            latest = cursor.fetchone()
            states.append(
                evaluate_job_due(
                    policy,
                    last_successful_at=None if latest is None else latest[0],
                    as_of=as_of,
                )
            )
        return tuple(states)

    @staticmethod
    def _enqueue_routes(
        cursor: Any, alert: OperationalAlert, enqueued_at: datetime
    ) -> tuple[AlertOutboxDelivery, ...]:
        cursor.execute(
            "SELECT DISTINCT ON (route_name) route_policy_id,route_name,version,alert_code,"
            "minimum_severity,channel,destination_reference,owner,approved_by,approved_at,"
            "enabled,content_hash FROM operational_alert_route_policy_versions "
            "WHERE approved_at<=%s ORDER BY route_name,approved_at DESC,route_policy_id DESC",
            (enqueued_at,),
        )
        deliveries: list[AlertOutboxDelivery] = []
        severity_rank = {AlertSeverity.WARNING: 1, AlertSeverity.CRITICAL: 2}
        for row in cursor.fetchall():
            policy = PostgresOperationalJobStore._route_policy_from_row(row)
            if (
                not policy.enabled
                or policy.alert_code not in {"*", alert.code}
                or severity_rank[alert.severity] < severity_rank[policy.minimum_severity]
            ):
                continue
            cursor.execute(
                "SELECT delivery_id,alert_id,route_policy_id,channel,destination_reference,"
                "status,enqueued_at,payload FROM operational_alert_delivery_outbox "
                "WHERE alert_id=%s AND route_policy_id=%s",
                (alert.alert_id, policy.route_policy_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                deliveries.append(PostgresOperationalJobStore._delivery_from_row(existing))
                continue
            payload = {
                "alert_id": str(alert.alert_id),
                "code": alert.code,
                "severity": alert.severity.value,
                "resource": alert.resource,
                "route_name": policy.route_name,
            }
            delivery = AlertOutboxDelivery(
                uuid4(),
                alert.alert_id,
                policy.route_policy_id,
                policy.channel,
                policy.destination_reference,
                "PENDING_EXTERNAL_DELIVERY",
                enqueued_at,
                payload,
            )
            cursor.execute(
                "INSERT INTO operational_alert_delivery_outbox VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (
                    delivery.delivery_id,
                    delivery.alert_id,
                    delivery.route_policy_id,
                    delivery.channel,
                    delivery.destination_reference,
                    delivery.status,
                    delivery.enqueued_at,
                    json.dumps(delivery.payload, sort_keys=True),
                ),
            )
            deliveries.append(delivery)
        return tuple(deliveries)

    @staticmethod
    def _policy_from_row(row: tuple[object, ...]) -> OperationalJobPolicy:
        policy = OperationalJobPolicy(
            UUID(str(row[0])), str(row[1]), str(row[2]), int(str(row[3])), int(str(row[4])),
            str(row[5]), str(row[6]), str(row[7]), cast(datetime, row[8]), bool(row[9]), str(row[10]),
        )
        if _job_policy_hash(policy) != policy.content_hash:
            raise OperationalJobError("job_policy_hash_mismatch")
        return policy

    @staticmethod
    def _run_from_row(row: tuple[object, ...]) -> OperationalJobRun:
        run = OperationalJobRun(
            UUID(str(row[0])), UUID(str(row[1])), str(row[2]), cast(datetime, row[3]),
            cast(datetime, row[4]), cast(datetime, row[5]), OperationalJobStatus(str(row[6])),
            dict(cast(dict[str, str], row[7])), str(row[8]),
        )
        if _job_run_hash(run) != run.content_hash:
            raise OperationalJobError("job_run_hash_mismatch")
        return run

    @staticmethod
    def _route_policy_from_row(row: tuple[object, ...]) -> AlertRoutePolicy:
        policy = AlertRoutePolicy(
            UUID(str(row[0])), str(row[1]), str(row[2]), str(row[3]), AlertSeverity(str(row[4])),
            str(row[5]), str(row[6]), str(row[7]), str(row[8]), cast(datetime, row[9]),
            bool(row[10]), str(row[11]),
        )
        if _route_policy_hash(policy) != policy.content_hash:
            raise OperationalJobError("alert_route_policy_hash_mismatch")
        return policy

    @staticmethod
    def _delivery_from_row(row: tuple[object, ...]) -> AlertOutboxDelivery:
        return AlertOutboxDelivery(
            UUID(str(row[0])), UUID(str(row[1])), UUID(str(row[2])), str(row[3]), str(row[4]),
            str(row[5]), cast(datetime, row[6]), dict(cast(dict[str, str], row[7])),
        )


def _validate_job_policy(policy: OperationalJobPolicy) -> None:
    _aware(policy.approved_at, "job_policy_time_must_be_timezone_aware")
    if (
        not policy.job_name.strip()
        or not policy.version.strip()
        or policy.interval_seconds <= 0
        or policy.grace_seconds < 0
        or not policy.owner.strip()
        or not policy.runbook_uri.strip()
        or not policy.approved_by.strip()
    ):
        raise OperationalJobError("invalid_operational_job_policy")


def _validate_job_run(run: OperationalJobRun) -> None:
    for value in (run.scheduled_for, run.started_at, run.completed_at):
        _aware(value, "job_run_time_must_be_timezone_aware")
    if (
        not run.idempotency_key.strip()
        or run.started_at < run.scheduled_for
        or run.completed_at < run.started_at
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in run.summary.items())
    ):
        raise OperationalJobError("invalid_operational_job_run")


def _validate_route_policy(policy: AlertRoutePolicy) -> None:
    _aware(policy.approved_at, "alert_route_policy_time_must_be_timezone_aware")
    if (
        not policy.route_name.strip()
        or not policy.version.strip()
        or not policy.alert_code.strip()
        or policy.channel != "LOCAL_OUTBOX"
        or not policy.destination_reference.strip()
        or len(policy.destination_reference) > 128
        or not policy.destination_reference[0].isalnum()
        or not policy.destination_reference.replace("-", "").replace("_", "").isalnum()
        or not policy.owner.strip()
        or not policy.approved_by.strip()
    ):
        raise OperationalJobError("invalid_alert_route_policy")


def _job_policy_hash(policy: OperationalJobPolicy) -> str:
    return _hash(
        {
            "job_name": policy.job_name,
            "version": policy.version,
            "interval_seconds": policy.interval_seconds,
            "grace_seconds": policy.grace_seconds,
            "owner": policy.owner,
            "runbook_uri": policy.runbook_uri,
            "approved_by": policy.approved_by,
            "approved_at": policy.approved_at.isoformat(),
            "enabled": policy.enabled,
        }
    )


def _job_run_hash(run: OperationalJobRun) -> str:
    return _hash(
        {
            "policy_id": str(run.policy_id),
            "idempotency_key": run.idempotency_key,
            "scheduled_for": run.scheduled_for.isoformat(),
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat(),
            "status": run.status.value,
            "summary": run.summary,
        }
    )


def _route_policy_hash(policy: AlertRoutePolicy) -> str:
    return _hash(
        {
            "route_name": policy.route_name,
            "version": policy.version,
            "alert_code": policy.alert_code,
            "minimum_severity": policy.minimum_severity.value,
            "channel": policy.channel,
            "destination_reference": policy.destination_reference,
            "owner": policy.owner,
            "approved_by": policy.approved_by,
            "approved_at": policy.approved_at.isoformat(),
            "enabled": policy.enabled,
        }
    )


def _aware(value: datetime, message: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OperationalJobError(message)


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
