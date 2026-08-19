import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from trade_platform.operational_alerts import AlertSeverity
from trade_platform.operational_jobs import (
    OperationalJobError,
    OperationalJobStatus,
    build_alert_route_policy,
    build_job_policy,
    build_job_run,
    evaluate_job_due,
)


class OperationalJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 19, 12, tzinfo=UTC)
        self.policy = build_job_policy(
            job_name="signal-expiry-monitor",
            version="v1",
            interval=timedelta(minutes=5),
            grace=timedelta(minutes=2),
            owner="paper-operations",
            runbook_uri="runbook:signal-expiry-monitor",
            approved_by="operator",
            approved_at=self.now,
        )

    def test_due_and_overdue_are_distinct_and_do_not_execute_work(self) -> None:
        current = evaluate_job_due(
            self.policy, last_successful_at=None, as_of=self.now + timedelta(minutes=4)
        )
        due = evaluate_job_due(
            self.policy, last_successful_at=None, as_of=self.now + timedelta(minutes=5)
        )
        overdue = evaluate_job_due(
            self.policy,
            last_successful_at=None,
            as_of=self.now + timedelta(minutes=7, seconds=1),
        )
        self.assertFalse(current.due)
        self.assertTrue(due.due)
        self.assertFalse(due.overdue)
        self.assertTrue(overdue.overdue)

    def test_success_resets_deadline_and_future_success_is_rejected(self) -> None:
        succeeded_at = self.now + timedelta(minutes=4)
        state = evaluate_job_due(
            self.policy,
            last_successful_at=succeeded_at,
            as_of=self.now + timedelta(minutes=8),
        )
        self.assertEqual(state.due_at, self.now + timedelta(minutes=9))
        self.assertFalse(state.due)
        with self.assertRaisesRegex(OperationalJobError, "invalid_job_success_time"):
            evaluate_job_due(
                self.policy,
                last_successful_at=self.now + timedelta(minutes=10),
                as_of=self.now + timedelta(minutes=8),
            )

    def test_run_contract_is_terminal_idempotency_evidence(self) -> None:
        run = build_job_run(
            policy_id=self.policy.policy_id,
            idempotency_key="signal-expiry-monitor:2026-08-19T12:05:00Z",
            scheduled_for=self.now + timedelta(minutes=5),
            started_at=self.now + timedelta(minutes=5, seconds=1),
            completed_at=self.now + timedelta(minutes=5, seconds=2),
            status=OperationalJobStatus.SUCCEEDED,
            summary={"expired": "0", "authority": "evidence-only"},
        )
        self.assertEqual(run.status, OperationalJobStatus.SUCCEEDED)
        self.assertEqual(len(run.content_hash), 64)
        with self.assertRaisesRegex(OperationalJobError, "invalid_operational_job_run"):
            build_job_run(
                policy_id=uuid4(),
                idempotency_key="bad-order",
                scheduled_for=self.now,
                started_at=self.now - timedelta(seconds=1),
                completed_at=self.now,
                status=OperationalJobStatus.FAILED,
                summary={},
            )

    def test_route_is_local_reference_only_and_contains_no_endpoint(self) -> None:
        route = build_alert_route_policy(
            route_name="paper-ops-warning",
            version="v1",
            alert_code="OPERATIONAL_JOB_OVERDUE",
            minimum_severity=AlertSeverity.WARNING,
            destination_reference="paper-operations-primary",
            owner="paper-operations",
            approved_by="operator",
            approved_at=self.now,
        )
        self.assertEqual(route.channel, "LOCAL_OUTBOX")
        with self.assertRaisesRegex(OperationalJobError, "invalid_alert_route_policy"):
            build_alert_route_policy(
                route_name="network-route",
                version="v1",
                alert_code="*",
                minimum_severity=AlertSeverity.WARNING,
                destination_reference="https://not-allowed.invalid/hook",
                owner="paper-operations",
                approved_by="operator",
                approved_at=self.now,
            )


if __name__ == "__main__":
    unittest.main()
