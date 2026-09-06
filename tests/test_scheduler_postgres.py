import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class SchedulerWorkerPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def setUp(self) -> None:
        from trade_platform.operational_alerts import PostgresOperationalAlertStore
        from trade_platform.operational_jobs import PostgresOperationalJobStore
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.retention_evidence import PostgresRetentionEvidenceStore
        from trade_platform.scheduler import JobContext, SchedulerWorker, default_job_registry

        self.dsn = os.environ["POSTGRES_TEST_DSN"]
        self.now = datetime(2026, 9, 6, 12, tzinfo=UTC)
        self.suffix = uuid4().hex[:8]
        self.database = PostgresDatabase(self.dsn)
        self.alerts = PostgresOperationalAlertStore(self.database)
        self.job_store = PostgresOperationalJobStore(self.database, alerts=self.alerts)
        self.retention_store = PostgresRetentionEvidenceStore(self.database)
        self.context = JobContext(
            database=self.database,
            job_store=self.job_store,
            alerts=self.alerts,
            retention_store=self.retention_store,
        )
        self.registry = dict(default_job_registry())
        self.worker = SchedulerWorker(context=self.context, registry=self.registry, clock=lambda: self.now)

    def tearDown(self) -> None:
        self.database.close()

    def _policy(self, base_name: str, *, interval: timedelta = timedelta(minutes=5)):
        """A policy under a per-test-unique job name.

        Job names are the identity ``due_jobs()`` groups on (``DISTINCT ON (job_name)``,
        latest ``approved_at`` wins), so reusing a fixed name like ``operational_job_monitor``
        across independent test methods against the same live database would make them
        interfere with each other. Each test instead registers its own uniquely-named
        policy and maps that exact name to the real runner function it wants to exercise
        (see the individual tests) -- the runner functions themselves are indifferent to
        what job name they are invoked under.
        """
        from trade_platform.operational_jobs import build_job_policy

        job_name = f"{base_name}-{self.suffix}"
        policy = build_job_policy(
            job_name=job_name,
            version="v1",
            interval=interval,
            grace=timedelta(minutes=2),
            owner="staging-operations",
            runbook_uri=f"runbook:{base_name}",
            approved_by="integration-operator",
            approved_at=self.now - timedelta(minutes=10),
        )
        self.job_store.append_policy(policy)
        return policy

    def test_registered_due_job_is_executed_and_recorded_idempotently(self) -> None:
        from trade_platform.scheduler import run_operational_job_monitor

        policy = self._policy("operational_job_monitor")
        self.registry[policy.job_name] = run_operational_job_monitor
        due_at = self.now

        run_ids_first = {run.run_id for run in self.worker.run_tick(due_at)}
        self.assertEqual(len(run_ids_first), 1)

        state = next(
            state for state in self.job_store.due_jobs(due_at) if state.policy.policy_id == policy.policy_id
        )
        self.assertFalse(state.due)
        self.assertEqual(state.last_successful_at, due_at)

        # Not due again immediately -- a second tick at the same instant does nothing.
        self.assertEqual(self.worker.run_tick(due_at), ())

    def test_unregistered_job_name_is_left_untouched(self) -> None:
        policy = self._policy("no-such-runner-registered")
        completed = self.worker.run_tick(self.now)
        self.assertEqual(completed, ())
        state = next(
            state for state in self.job_store.due_jobs(self.now) if state.policy.policy_id == policy.policy_id
        )
        self.assertTrue(state.due)

    def test_postgres_dependency_probe_job_records_latency(self) -> None:
        from trade_platform.scheduler import run_postgres_dependency_probe

        policy = self._policy("postgres_dependency_probe")
        self.registry[policy.job_name] = run_postgres_dependency_probe
        completed = self.worker.run_tick(self.now)
        self.assertEqual(len(completed), 1)
        run = completed[0]
        self.assertEqual(run.status.value, "SUCCEEDED")
        self.assertIn("latency_ms", run.summary)

    def test_failed_job_run_is_recorded_and_raises_a_durable_alert(self) -> None:
        from trade_platform.scheduler import SchedulerWorker

        def _always_fails(context, as_of):
            raise RuntimeError("synthetic runner failure")

        policy = self._policy("failing-job")
        registry = {policy.job_name: _always_fails}
        worker = SchedulerWorker(context=self.context, registry=registry, clock=lambda: self.now)

        completed = worker.run_tick(self.now)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].status.value, "FAILED")
        self.assertEqual(completed[0].summary["error_type"], "RuntimeError")
        self.assertTrue(
            any(
                alert.code == "OPERATIONAL_JOB_EXECUTION_FAILED"
                and alert.resource == f"job:{policy.job_name}"
                for alert in self.alerts.active()
            )
        )
        # Still due (only a SUCCEEDED run advances due-state) -- the worker will retry it.
        state = next(
            state for state in self.job_store.due_jobs(self.now) if state.policy.policy_id == policy.policy_id
        )
        self.assertTrue(state.due)

    def test_a_held_advisory_lock_blocks_a_concurrent_claim_and_releases_cleanly(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.scheduler import _release, _try_claim, run_operational_job_monitor

        policy = self._policy("operational_job_monitor")
        self.registry[policy.job_name] = run_operational_job_monitor
        due_at = next(
            state.due_at
            for state in self.job_store.due_jobs(self.now)
            if state.policy.policy_id == policy.policy_id
        )
        lock_key = f"trade_platform:operational_job_run:{policy.job_name}:{due_at.isoformat()}"

        other_connection = PostgresDatabase(self.dsn)
        try:
            self.assertTrue(_try_claim(other_connection, lock_key))
            # A second worker (this test's own database/session) must not also acquire it.
            self.assertEqual(self.worker.run_tick(due_at), ())
            state = next(
                state
                for state in self.job_store.due_jobs(due_at)
                if state.policy.policy_id == policy.policy_id
            )
            self.assertTrue(state.due)  # untouched: the other session held the lock
            _release(other_connection, lock_key)
        finally:
            other_connection.close()

        # Now that the lock is released, the worker can claim and execute it.
        completed = self.worker.run_tick(due_at)
        self.assertEqual(len(completed), 1)

    def test_retention_evaluation_sweep_job_evaluates_elapsed_manifests(self) -> None:
        from trade_platform.retention_evidence import (
            ObjectEvidenceKind,
            RetentionClassification,
            build_object_manifest,
            build_retention_policy,
        )

        retention_policy = build_retention_policy(
            policy_name=f"sweep-policy-{self.suffix}",
            version="v1",
            classification=RetentionClassification.CONFIGURATION,
            retention=timedelta(days=1),
            legal_hold=False,
            owner="staging-operations",
            approved_by="integration-operator",
            approved_at=self.now - timedelta(days=10),
        )
        self.retention_store.append_policy(retention_policy)
        manifest = build_object_manifest(
            object_reference=f"config-snapshot-{self.suffix}",
            object_kind=ObjectEvidenceKind.CONFIGURATION_SNAPSHOT,
            media_type="application/json",
            byte_size=128,
            sha256="a" * 64,
            source_reference="staging-config-store",
            policy_id=retention_policy.policy_id,
            captured_at=self.now - timedelta(days=5),
        )
        self.retention_store.append_manifest(manifest)

        from trade_platform.scheduler import run_retention_evaluation_sweep

        policy = self._policy("retention_evaluation_sweep")
        self.registry[policy.job_name] = run_retention_evaluation_sweep
        completed = self.worker.run_tick(self.now)
        self.assertEqual(len(completed), 1)
        # The sweep is deliberately global (every due manifest, not just this test's),
        # so other tests' manifests sharing this database may also be swept in the same
        # tick -- assert our own manifest was among them via its actual evaluation state
        # rather than an exact aggregate count.
        self.assertGreaterEqual(int(completed[0].summary["manifests_evaluated"]), 1)
        self.assertGreaterEqual(int(completed[0].summary["eligible_for_review"]), 1)

        evaluated = self.retention_store.manifests_due_for_evaluation(self.now)
        self.assertNotIn(manifest.manifest_id, evaluated)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class RetentionManifestsDueForEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def setUp(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.retention_evidence import PostgresRetentionEvidenceStore

        self.now = datetime(2026, 9, 6, 12, tzinfo=UTC)
        self.suffix = uuid4().hex[:8]
        self.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.store = PostgresRetentionEvidenceStore(self.database)

    def tearDown(self) -> None:
        self.database.close()

    def _policy_and_manifest(self, *, retention_seconds: int, captured_at: datetime):
        from trade_platform.retention_evidence import (
            ObjectEvidenceKind,
            RetentionClassification,
            build_object_manifest,
            build_retention_policy,
        )

        policy = build_retention_policy(
            policy_name=f"due-eval-{uuid4().hex[:8]}",
            version="v1",
            classification=RetentionClassification.CONFIGURATION,
            retention=timedelta(seconds=retention_seconds),
            legal_hold=False,
            owner="staging-operations",
            approved_by="integration-operator",
            approved_at=self.now - timedelta(days=30),
        )
        self.store.append_policy(policy)
        manifest = build_object_manifest(
            object_reference=f"object-{uuid4().hex}",
            object_kind=ObjectEvidenceKind.CONFIGURATION_SNAPSHOT,
            media_type="application/json",
            byte_size=1,
            sha256="b" * 64,
            source_reference="staging-config-store",
            policy_id=policy.policy_id,
            captured_at=captured_at,
        )
        self.store.append_manifest(manifest)
        return policy, manifest

    def test_never_evaluated_manifest_is_due(self) -> None:
        _, manifest = self._policy_and_manifest(
            retention_seconds=86400, captured_at=self.now - timedelta(hours=1)
        )
        self.assertIn(manifest.manifest_id, self.store.manifests_due_for_evaluation(self.now))

    def test_manifest_still_within_retention_window_is_not_due(self) -> None:
        _, manifest = self._policy_and_manifest(
            retention_seconds=86400, captured_at=self.now - timedelta(hours=1)
        )
        self.store.evaluate(
            manifest.manifest_id, evaluated_at=self.now, idempotency_key=f"k-{manifest.manifest_id}"
        )
        self.assertNotIn(manifest.manifest_id, self.store.manifests_due_for_evaluation(self.now))

    def test_manifest_whose_window_elapses_after_first_evaluation_becomes_due_again(self) -> None:
        _, manifest = self._policy_and_manifest(
            retention_seconds=60, captured_at=self.now - timedelta(seconds=30)
        )
        self.store.evaluate(
            manifest.manifest_id, evaluated_at=self.now, idempotency_key=f"k1-{manifest.manifest_id}"
        )
        self.assertNotIn(manifest.manifest_id, self.store.manifests_due_for_evaluation(self.now))
        later = self.now + timedelta(minutes=5)
        self.assertIn(manifest.manifest_id, self.store.manifests_due_for_evaluation(later))

    def test_manifest_already_flagged_eligible_for_review_does_not_resurface(self) -> None:
        _, manifest = self._policy_and_manifest(
            retention_seconds=1, captured_at=self.now - timedelta(days=1)
        )
        evaluation = self.store.evaluate(
            manifest.manifest_id, evaluated_at=self.now, idempotency_key=f"k-{manifest.manifest_id}"
        )
        self.assertEqual(evaluation.disposition.value, "ELIGIBLE_FOR_REVIEW")
        self.assertNotIn(
            manifest.manifest_id, self.store.manifests_due_for_evaluation(self.now + timedelta(days=1))
        )

    def test_limit_and_time_validation(self) -> None:
        from trade_platform.retention_evidence import RetentionEvidenceError

        with self.assertRaises(RetentionEvidenceError):
            self.store.manifests_due_for_evaluation(self.now, limit=0)
        with self.assertRaises(RetentionEvidenceError):
            self.store.manifests_due_for_evaluation(datetime(2026, 9, 6, 12))  # noqa: DTZ001 - deliberately naive


if __name__ == "__main__":
    unittest.main()
