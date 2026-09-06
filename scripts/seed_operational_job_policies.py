"""Register the Module 3E scheduler/worker's approved job entry points.

This is a deliberate, explicit deployment step -- distinct from ``alembic upgrade
head`` -- run once per environment (and again only when a job's approved interval,
owner, or runbook changes, which requires bumping ``--version``). It does not start
the worker, and the worker will not execute a job whose name has no approved policy
registered here (or, symmetrically, whose registered policy has no runner in
``trade_platform.scheduler.default_job_registry()``): both sides of that mapping are
independently governed. Registration is idempotent -- re-running with the same
``--version`` and ``--approved-at`` is a no-op (see
``PostgresOperationalJobStore.append_policy``); re-running with the same
``--version`` but a changed argument (a different interval, owner, or
``--approved-at``) fails closed with ``job_policy_version_conflict`` rather than
silently overwriting the approved policy -- pass an explicit, fixed
``--approved-at`` if you need this command to be safely re-runnable (e.g. from an
idempotent deployment script), otherwise each invocation stamps a fresh approval
time and only the first one for a given ``--version`` succeeds.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from trade_platform.domain import utc_now
from trade_platform.operational_alerts import AlertSeverity, PostgresOperationalAlertStore
from trade_platform.operational_jobs import (
    PostgresOperationalJobStore,
    build_alert_route_policy,
    build_job_policy,
)
from trade_platform.persistence import PostgresDatabase

_JOB_DEFINITIONS: tuple[tuple[str, timedelta, timedelta, str], ...] = (
    (
        "operational_job_monitor",
        timedelta(minutes=1),
        timedelta(minutes=2),
        "runbook:operational-job-monitor",
    ),
    (
        "postgres_dependency_probe",
        timedelta(minutes=1),
        timedelta(minutes=2),
        "runbook:postgres-dependency-probe",
    ),
    (
        "retention_evaluation_sweep",
        timedelta(hours=1),
        timedelta(minutes=30),
        "runbook:retention-evaluation-sweep",
    ),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-dsn", required=True, help="postgresql:// DSN to register policies in")
    parser.add_argument(
        "--version", default="v1", help="Policy version stamp; bump to approve a changed interval/owner"
    )
    parser.add_argument("--owner", default="platform-operations", help="Team/owner of record for these jobs")
    parser.add_argument(
        "--approved-by", required=True, help="Named human or role approving this policy version"
    )
    parser.add_argument(
        "--route-destination",
        default="platform-operations-primary",
        help="Opaque LOCAL_OUTBOX destination reference for overdue-job alerts",
    )
    parser.add_argument(
        "--approved-at",
        default=None,
        help="ISO-8601 UTC approval timestamp; defaults to now. Pin this for a "
        "safely re-runnable (idempotent) invocation.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    approved_at = utc_now() if args.approved_at is None else datetime.fromisoformat(args.approved_at)
    database = PostgresDatabase(args.postgres_dsn)
    try:
        alerts = PostgresOperationalAlertStore(database)
        jobs = PostgresOperationalJobStore(database, alerts=alerts)
        for job_name, interval, grace, runbook_uri in _JOB_DEFINITIONS:
            policy = build_job_policy(
                job_name=job_name,
                version=args.version,
                interval=interval,
                grace=grace,
                owner=args.owner,
                runbook_uri=runbook_uri,
                approved_by=args.approved_by,
                approved_at=approved_at,
            )
            recorded = jobs.append_policy(policy)
            print(f"job policy registered: {recorded.job_name} {recorded.version} (interval={interval})")

        route = build_alert_route_policy(
            route_name="operational-job-overdue-primary",
            version=args.version,
            alert_code="OPERATIONAL_JOB_OVERDUE",
            minimum_severity=AlertSeverity.WARNING,
            destination_reference=args.route_destination,
            owner=args.owner,
            approved_by=args.approved_by,
            approved_at=approved_at,
        )
        recorded_route = jobs.append_route_policy(route)
        print(f"alert route registered: {recorded_route.route_name} {recorded_route.version}")
    finally:
        database.close()


if __name__ == "__main__":
    main()
