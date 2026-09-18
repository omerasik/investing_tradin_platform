import asyncio
import json
import os
import unittest

from fastapi.testclient import TestClient

from trade_platform.runtime_app import RuntimeCompositionError
from trade_platform.scheduler import default_job_registry
from trade_platform.worker_app import app, create_worker_runtime_from_environment


def _env(overrides: dict[str, str | None]):
    def get(name: str) -> str | None:
        if name in overrides:
            return overrides[name]
        return os.environ.get(name)

    return get


class WorkerCompositionFailClosedTests(unittest.TestCase):
    def test_missing_postgres_dsn_fails_closed(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            create_worker_runtime_from_environment(env=_env({"POSTGRES_DSN": None}))

    def test_invalid_dsn_scheme_fails_closed(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            create_worker_runtime_from_environment(
                env=_env({"POSTGRES_DSN": "not-a-postgres-dsn"})
            )

    def test_unreachable_dsn_fails_closed(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            create_worker_runtime_from_environment(
                env=_env(
                    {
                        "POSTGRES_DSN": "postgresql://postgres:postgres@127.0.0.1:1/does_not_exist"  # pragma: allowlist secret
                    }
                )
            )

    def test_non_integer_poll_seconds_fails_closed(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            create_worker_runtime_from_environment(
                env=_env(
                    {
                        "POSTGRES_DSN": "postgresql://postgres:postgres@127.0.0.1:1/does_not_exist",  # pragma: allowlist secret
                        "TRADE_PLATFORM_WORKER_POLL_SECONDS": "not-a-number",
                    }
                )
            )

    def test_too_small_poll_seconds_fails_closed(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            create_worker_runtime_from_environment(
                env=_env(
                    {
                        "POSTGRES_DSN": "postgresql://postgres:postgres@127.0.0.1:1/does_not_exist",  # pragma: allowlist secret
                        "TRADE_PLATFORM_WORKER_POLL_SECONDS": "1",
                    }
                )
            )


_UNREACHABLE_DSN = "postgresql://postgres:postgres@127.0.0.1:1/does_not_exist"  # pragma: allowlist secret


def _scheduled_authorization_document() -> str:
    return json.dumps(
        {
            "authorization_version": "fixture-authorization-v1",
            "job_name": "scheduled_bybit_btcusdt_acquisition_fixture",
            "job_policy_version": "fixture-policy-v1",
            "provider": "bybit",
            "source_id": "33333333-3333-3333-3333-333333333333",
            "instrument_id": "CRYPTO:BYBIT:BTCUSDT:PERP",
            "provider_symbol": "BTCUSDT",
            "observation_kinds": ["OHLCV", "MARK_PRICE", "INDEX_PRICE", "OPEN_INTEREST"],
            "normalization_version": "bybit-v5-md-fixture",
            "materialize_features": True,
            "window_size_seconds": 600,
            "schedule_anchor": "2026-06-01T00:00:00+00:00",
            "finality_lag_seconds": 120,
            "maximum_pages_per_kind": 2,
            "maximum_catchup_windows_per_invocation": 3,
            "minimum_request_interval_seconds": 0.5,
            "authorized_by": "fixture-operator",
            "authorization_reference": "FIXTURE-AUTHORIZATION-1",
            "authorized_at": "2026-05-31T00:00:00+00:00",
        }
    )


def _scheduled_env() -> dict[str, str | None]:
    from trade_platform.scheduled_historical_acquisition_v1 import (
        SCHEDULED_ACQUISITION_AUTHORIZATION_ENV,
        SCHEDULED_ACQUISITION_MODE_ENV,
        SCHEDULED_ACQUISITION_MODE_V1,
        SCHEDULED_ACQUISITION_TERMS_ENV,
    )

    return {
        SCHEDULED_ACQUISITION_MODE_ENV: SCHEDULED_ACQUISITION_MODE_V1,
        SCHEDULED_ACQUISITION_AUTHORIZATION_ENV: _scheduled_authorization_document(),
        SCHEDULED_ACQUISITION_TERMS_ENV: "accepted",
    }


class WorkerScheduledAcquisitionOptInFailClosedTests(unittest.TestCase):
    """Partial provider-scheduler configuration must fail startup before any connection."""

    def test_partial_scheduled_acquisition_configuration_fails_startup(self) -> None:
        full = _scheduled_env()
        for missing in full:
            overrides = {**full, missing: None, "POSTGRES_DSN": _UNREACHABLE_DSN}
            with self.subTest(missing=missing), self.assertRaises(RuntimeCompositionError) as caught:
                create_worker_runtime_from_environment(env=_env(overrides))
            # Rejected by the opt-in parser -- never reached (or degraded at) the database.
            self.assertTrue(str(caught.exception).startswith("scheduled_acquisition_configuration_invalid"))

    def test_invalid_terms_or_secret_fails_startup(self) -> None:
        from trade_platform.scheduled_historical_acquisition_v1 import (
            SCHEDULED_ACQUISITION_AUTHORIZATION_ENV,
            SCHEDULED_ACQUISITION_TERMS_ENV,
        )

        document = json.loads(_scheduled_authorization_document())
        document["secret_reference"] = "vault://bybit/key"
        for overrides in (
            {SCHEDULED_ACQUISITION_TERMS_ENV: "true"},
            {SCHEDULED_ACQUISITION_AUTHORIZATION_ENV: json.dumps(document)},
        ):
            env = {**_scheduled_env(), **overrides, "POSTGRES_DSN": _UNREACHABLE_DSN}
            with self.assertRaises(RuntimeCompositionError) as caught:
                create_worker_runtime_from_environment(env=_env(env))
            self.assertTrue(str(caught.exception).startswith("scheduled_acquisition_configuration_invalid"))

    def test_complete_configuration_still_requires_a_reachable_database(self) -> None:
        with self.assertRaises(RuntimeCompositionError) as caught:
            create_worker_runtime_from_environment(
                env=_env({**_scheduled_env(), "POSTGRES_DSN": _UNREACHABLE_DSN})
            )
        self.assertEqual(str(caught.exception), "postgres_unreachable_or_invalid_dsn")


class WorkerAppLifespanFailClosedTests(unittest.TestCase):
    def test_app_import_is_always_safe_but_startup_fails_closed_without_postgres_dsn(self) -> None:
        # Importing trade_platform.worker_app (done at module load above) must never
        # touch PostgreSQL or raise -- only entering the TestClient context (which
        # triggers the ASGI lifespan) may.
        original = os.environ.pop("POSTGRES_DSN", None)
        try:
            with self.assertRaises(RuntimeCompositionError), TestClient(app):
                pass
        finally:
            if original is not None:
                os.environ["POSTGRES_DSN"] = original


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class WorkerRuntimePostgresTests(unittest.TestCase):
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

    def test_worker_runtime_composes_and_reports_readiness_after_a_tick(self) -> None:
        from trade_platform.data_health import PostgresDataHealthStore
        from trade_platform.domain import utc_now
        from trade_platform.postgres_market_data import PostgresHistoricalBarStore
        from trade_platform.scheduler import JobContext, SchedulerWorker

        runtime = create_worker_runtime_from_environment(
            env=_env({"POSTGRES_DSN": os.environ["POSTGRES_TEST_DSN"], "TRADE_PLATFORM_WORKER_POLL_SECONDS": "5"})
        )
        self.assertIsInstance(runtime.worker, SchedulerWorker)
        self.assertIsInstance(runtime.worker.context, JobContext)
        # The worker's context is Postgres-backed for Data Health -- no SQLite/in-memory
        # fallback and no silent degradation (Module 3F requirement).
        self.assertIsInstance(runtime.worker.context.bar_store, PostgresHistoricalBarStore)
        self.assertIsInstance(runtime.worker.context.data_health_store, PostgresDataHealthStore)
        self.assertIn("data_health_evaluation", runtime.worker.registry)
        # Normal startup is provider-network-inert: only the internal jobs exist and
        # no scheduled-acquisition runner (the only provider-capable object) was built.
        self.assertEqual(set(runtime.worker.registry), set(default_job_registry()))
        self.assertIsNone(runtime.scheduled_acquisition)
        self.assertFalse(runtime.is_ready(now=utc_now()))
        asyncio.run(self._run_one_tick(runtime))
        self.assertTrue(runtime.last_tick_ok)
        self.assertTrue(runtime.is_ready(now=utc_now()))

    def test_explicit_opt_in_registers_exactly_one_authorized_runner_without_calling_out(self) -> None:
        from trade_platform.scheduled_historical_acquisition_v1 import (
            ScheduledHistoricalAcquisitionRunnerV1,
        )

        runtime = create_worker_runtime_from_environment(
            env=_env({**_scheduled_env(), "POSTGRES_DSN": os.environ["POSTGRES_TEST_DSN"]})
        )
        try:
            self.assertIsInstance(runtime.scheduled_acquisition, ScheduledHistoricalAcquisitionRunnerV1)
            self.assertEqual(
                set(runtime.worker.registry) - set(default_job_registry()),
                {"scheduled_bybit_btcusdt_acquisition_fixture"},
            )
            assert runtime.scheduled_acquisition is not None
            self.assertTrue(runtime.scheduled_acquisition.configuration.terms_accepted)
            self.assertIsNone(runtime.scheduled_acquisition.configuration.secret_reference)
            # Composition alone never ticks, and no enabled policy exists for this job
            # name in the shared database, so no provider request can be made here.
        finally:
            runtime.database.close()

    @staticmethod
    async def _run_one_tick(runtime) -> None:
        runtime.start()
        await asyncio.sleep(0.05)
        await runtime.stop()


if __name__ == "__main__":
    unittest.main()
