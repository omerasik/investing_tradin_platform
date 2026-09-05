"""Tests for the canonical runtime composition root (trade_platform.runtime_app).

Covers the no-fallback rule: a protected runtime (paper/production) must never
silently serve a SQLite- or in-memory-backed application, and must fail closed
when PostgreSQL is missing, invalid, or unreachable.
"""

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from trade_platform.operational_alerts import PostgresOperationalAlertStore
from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
from trade_platform.postgres_paper_oms import PostgresPaperOms
from trade_platform.runtime_app import (
    RuntimeCompositionError,
    RuntimeMode,
    compose_protected_postgres_app,
    create_runtime_app_from_environment,
)


def _env(overrides: dict[str, str | None]):
    def get(name: str) -> str | None:
        if name in overrides:
            return overrides[name]
        return os.environ.get(name)

    return get


class LocalResearchModeTests(unittest.TestCase):
    def test_default_environment_is_local_research_and_never_touches_postgres(self) -> None:
        app = create_runtime_app_from_environment(env=_env({"TRADE_PLATFORM_ENVIRONMENT": None}))
        client = TestClient(app)
        response = client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        # Local research mode never requires a durable Postgres authority to be "ready".
        self.assertEqual(client.get("/health/ready").status_code, 200)

    def test_explicit_local_research_environment(self) -> None:
        app = create_runtime_app_from_environment(env=_env({"TRADE_PLATFORM_ENVIRONMENT": "local_research"}))
        self.assertEqual(TestClient(app).get("/health/live").status_code, 200)


class PersistenceTargetRejectionTests(unittest.TestCase):
    def test_unknown_environment_name_is_rejected(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            create_runtime_app_from_environment(env=_env({"TRADE_PLATFORM_ENVIRONMENT": "staging-typo"}))

    def test_paper_without_postgres_dsn_fails_closed(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            create_runtime_app_from_environment(
                env=_env({"TRADE_PLATFORM_ENVIRONMENT": "paper", "POSTGRES_DSN": None})
            )

    def test_production_always_fails_closed_today(self) -> None:
        """Production identity/secret-manager authorities do not exist yet; this must
        never silently serve a partially-wired production app, even with a valid DSN."""
        with self.assertRaises(RuntimeCompositionError):
            create_runtime_app_from_environment(
                env=_env(
                    {
                        "TRADE_PLATFORM_ENVIRONMENT": "production",
                        "POSTGRES_DSN": "postgresql://postgres:postgres@127.0.0.1:5439/trade_platform",
                    }
                )
            )

    def test_paper_with_unreachable_dsn_fails_closed_not_sqlite(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            compose_protected_postgres_app(
                environment=RuntimeMode.PAPER,
                dsn="postgresql://postgres:postgres@127.0.0.1:1/does_not_exist",
                operator_token="test-token",
            )

    def test_compose_protected_postgres_app_rejects_local_research_mode(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            compose_protected_postgres_app(
                environment=RuntimeMode.LOCAL_RESEARCH,  # type: ignore[arg-type]
                dsn="postgresql://postgres:postgres@127.0.0.1:5439/trade_platform",
                operator_token="test-token",
            )


class DatabaseUnavailableStartupTests(unittest.TestCase):
    def test_invalid_dsn_scheme_fails_closed(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            compose_protected_postgres_app(
                environment=RuntimeMode.PAPER,
                dsn="not-a-postgres-dsn",
                operator_token="test-token",
            )


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ProtectedRuntimePostgresCompositionTests(unittest.TestCase):
    """Runs against the disposable PostgreSQL service used by test_postgres_integration.py."""

    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        self.dsn = os.environ["POSTGRES_TEST_DSN"]
        self.config = Config("alembic.ini")
        self.config.set_main_option(
            "sqlalchemy.url", self.dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(self.config, "head")

    def test_protected_runtime_composes_postgres_authority_graph(self) -> None:
        app = compose_protected_postgres_app(
            environment=RuntimeMode.PAPER, dsn=self.dsn, operator_token="paper-test-token"
        )
        try:
            authorities = app.state.postgres_authorities
            # isinstance checks on the composed graph, not config strings: prove no
            # safety-critical SQLite implementation exists in the protected graph.
            self.assertIsInstance(authorities.paper_oms, PostgresPaperOms)
            self.assertIsInstance(authorities.alert_store, PostgresOperationalAlertStore)
            self.assertIsInstance(
                authorities.operator_dashboard_queries, PostgresOperatorDashboardQueries
            )
            self.assertIs(app.state.paper_oms, authorities.paper_oms)
            self.assertIs(app.state.alert_store, authorities.alert_store)
            self.assertIs(
                app.state.operator_dashboard_queries, authorities.operator_dashboard_queries
            )
            # Explicitly unavailable, never silently SQLite.
            self.assertIsNone(app.state.risk_decisions)
            self.assertIsNone(app.state.promotion_ledger)
            self.assertIsNone(app.state.investment_store)
            self.assertIsNone(app.state.strategy_registry)

            client = TestClient(app)
            self.assertEqual(client.get("/health/live").status_code, 200)
            ready = client.get("/health/ready")
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["environment"], "paper")
            self.assertFalse(ready.json()["live_trading_enabled"])

            headers = {"Authorization": "Bearer paper-test-token"}
            command_center = client.get(
                "/operator-dashboard/command-center", headers=headers
            )
            self.assertEqual(command_center.status_code, 200)
            self.assertEqual(
                command_center.json()["live_trading_enabled"], False
            )
        finally:
            authorities.close()

    def test_readiness_fails_closed_when_postgres_becomes_unreachable(self) -> None:
        app = compose_protected_postgres_app(
            environment=RuntimeMode.PAPER, dsn=self.dsn, operator_token="paper-test-token"
        )
        authorities = app.state.postgres_authorities
        try:
            client = TestClient(app)
            self.assertEqual(client.get("/health/ready").status_code, 200)
            authorities.database.close()
            with mock.patch.object(
                authorities.operator_dashboard_queries,
                "workspace_references",
                side_effect=Exception("connection closed"),
            ):
                # Bounded readiness probe must fail closed (503), not hang or 500 silently
                # into a false "ok".
                from trade_platform.operator_dashboard import DashboardQueryError

                with mock.patch.object(
                    authorities.operator_dashboard_queries,
                    "workspace_references",
                    side_effect=DashboardQueryError("postgres_unreachable"),
                ):
                    response = client.get("/health/ready")
                    self.assertEqual(response.status_code, 503)
        finally:
            pass  # database already closed above


if __name__ == "__main__":
    unittest.main()
