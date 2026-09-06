import asyncio
import os
import unittest

from fastapi.testclient import TestClient

from trade_platform.runtime_app import RuntimeCompositionError
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
        from trade_platform.domain import utc_now
        from trade_platform.scheduler import JobContext, SchedulerWorker

        runtime = create_worker_runtime_from_environment(
            env=_env({"POSTGRES_DSN": os.environ["POSTGRES_TEST_DSN"], "TRADE_PLATFORM_WORKER_POLL_SECONDS": "5"})
        )
        self.assertIsInstance(runtime.worker, SchedulerWorker)
        self.assertIsInstance(runtime.worker.context, JobContext)
        self.assertFalse(runtime.is_ready(now=utc_now()))
        asyncio.run(self._run_one_tick(runtime))
        self.assertTrue(runtime.last_tick_ok)
        self.assertTrue(runtime.is_ready(now=utc_now()))

    @staticmethod
    async def _run_one_tick(runtime) -> None:
        runtime.start()
        await asyncio.sleep(0.05)
        await runtime.stop()


if __name__ == "__main__":
    unittest.main()
