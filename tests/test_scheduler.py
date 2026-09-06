"""Unit tests for the individual job-runner functions, using fakes (no PostgreSQL).

SchedulerWorker.run_tick's dispatch/claim loop uses real PostgreSQL session-level
advisory locks (see trade_platform.scheduler docstring) and is exercised end-to-end,
including concurrency, in tests/test_scheduler_postgres.py instead.
"""

import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from trade_platform.operational_alerts import AlertSeverity, AlertStatus
from trade_platform.retention_evidence import RetentionDisposition
from trade_platform.scheduler import (
    JobContext,
    run_operational_job_monitor,
    run_postgres_dependency_probe,
    run_retention_evaluation_sweep,
)

_NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)


class _FakeCursor:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail

    def execute(self, *args: object, **kwargs: object) -> None:
        if self.fail:
            raise RuntimeError("connection reset")

    def fetchone(self) -> tuple[int]:
        return (1,)


class _FakeConnection:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail

    @contextmanager
    def cursor(self):
        yield _FakeCursor(fail=self.fail)


class FakeDatabase:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    @contextmanager
    def transaction(self):
        yield _FakeConnection(fail=self.fail)


class FakeAlertStore:
    def __init__(self) -> None:
        self.raised: list[dict[str, object]] = []
        self.transitions: list[tuple[object, AlertStatus]] = []
        self._active: list[SimpleNamespace] = []

    def raise_alert(self, *, source, code, severity, resource, details, occurred_at=None):
        alert = SimpleNamespace(alert_id=uuid4(), code=code, resource=resource, severity=severity)
        self.raised.append({"source": source, "code": code, "resource": resource, "details": details})
        self._active.append(alert)
        return alert

    def transition(self, alert_id, status, *, actor, details=None):
        self.transitions.append((alert_id, status))
        self._active = [item for item in self._active if item.alert_id != alert_id]

    def active(self):
        return tuple(self._active)


class FakeJobStore:
    def __init__(self, opened: tuple[object, ...] = ()) -> None:
        self.opened = opened
        self.monitor_calls: list[datetime] = []

    def monitor_overdue(self, as_of: datetime):
        self.monitor_calls.append(as_of)
        return self.opened


class FakeRetentionStore:
    def __init__(self, due_ids: tuple[object, ...], evaluations: dict[object, object]) -> None:
        self.due_ids = due_ids
        self.evaluations = evaluations
        self.evaluate_calls: list[object] = []

    def manifests_due_for_evaluation(self, as_of: datetime, *, limit: int = 200):
        return self.due_ids

    def evaluate(self, manifest_id, *, evaluated_at, idempotency_key):
        self.evaluate_calls.append(manifest_id)
        return self.evaluations[manifest_id]


def _context(**overrides: object) -> JobContext:
    defaults = {
        "database": FakeDatabase(),
        "job_store": FakeJobStore(),
        "alerts": FakeAlertStore(),
        "retention_store": FakeRetentionStore((), {}),
    }
    defaults.update(overrides)
    return JobContext(**defaults)  # type: ignore[arg-type]


class OperationalJobMonitorRunnerTests(unittest.TestCase):
    def test_delegates_to_job_store_and_reports_count(self) -> None:
        job_store = FakeJobStore(opened=(object(), object()))
        context = _context(job_store=job_store)
        summary = run_operational_job_monitor(context, _NOW)
        self.assertEqual(summary, {"alerts_opened_or_resolved": "2"})
        self.assertEqual(job_store.monitor_calls, [_NOW])


class PostgresDependencyProbeRunnerTests(unittest.TestCase):
    def test_successful_probe_reports_latency_and_resolves_existing_alert(self) -> None:
        alerts = FakeAlertStore()
        existing = alerts.raise_alert(
            source="postgres_dependency_probe",
            code="POSTGRES_DEPENDENCY_UNHEALTHY",
            severity=AlertSeverity.CRITICAL,
            resource="postgres:primary",
            details={},
        )
        context = _context(database=FakeDatabase(fail=False), alerts=alerts)
        summary = run_postgres_dependency_probe(context, _NOW)
        self.assertIn("latency_ms", summary)
        self.assertEqual(alerts.transitions, [(existing.alert_id, AlertStatus.RESOLVED)])

    def test_failed_probe_raises_alert_and_reraises(self) -> None:
        alerts = FakeAlertStore()
        context = _context(database=FakeDatabase(fail=True), alerts=alerts)
        with self.assertRaises(RuntimeError):
            run_postgres_dependency_probe(context, _NOW)
        self.assertEqual(len(alerts.raised), 1)
        self.assertEqual(alerts.raised[0]["code"], "POSTGRES_DEPENDENCY_UNHEALTHY")
        self.assertEqual(alerts.raised[0]["resource"], "postgres:primary")

    def test_probe_only_resolves_its_own_alert_code_and_resource(self) -> None:
        alerts = FakeAlertStore()
        unrelated = alerts.raise_alert(
            source="other", code="OTHER_ALERT", severity=AlertSeverity.WARNING,
            resource="postgres:primary", details={},
        )
        context = _context(database=FakeDatabase(fail=False), alerts=alerts)
        run_postgres_dependency_probe(context, _NOW)
        self.assertEqual(alerts.transitions, [])
        self.assertIn(unrelated, alerts.active())


class RetentionEvaluationSweepRunnerTests(unittest.TestCase):
    def test_evaluates_each_due_manifest_and_counts_eligible_for_review(self) -> None:
        manifest_a, manifest_b = uuid4(), uuid4()
        evaluations = {
            manifest_a: SimpleNamespace(disposition=RetentionDisposition.RETAIN),
            manifest_b: SimpleNamespace(disposition=RetentionDisposition.ELIGIBLE_FOR_REVIEW),
        }
        retention_store = FakeRetentionStore((manifest_a, manifest_b), evaluations)
        context = _context(retention_store=retention_store)
        summary = run_retention_evaluation_sweep(context, _NOW)
        self.assertEqual(summary, {"manifests_evaluated": "2", "eligible_for_review": "1"})
        self.assertEqual(retention_store.evaluate_calls, [manifest_a, manifest_b])

    def test_no_due_manifests_is_a_clean_no_op(self) -> None:
        context = _context(retention_store=FakeRetentionStore((), {}))
        summary = run_retention_evaluation_sweep(context, _NOW)
        self.assertEqual(summary, {"manifests_evaluated": "0", "eligible_for_review": "0"})


if __name__ == "__main__":
    unittest.main()
