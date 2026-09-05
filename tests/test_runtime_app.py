"""Tests for the canonical runtime composition root (trade_platform.runtime_app).

Covers the no-fallback rule: a protected runtime (paper/production) must never
silently serve a SQLite- or in-memory-backed application, and must fail closed
when PostgreSQL is missing, invalid, or unreachable.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from trade_platform.operational_alerts import PostgresOperationalAlertStore
from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
from trade_platform.postgres_paper_oms import PostgresPaperOms
from trade_platform.runtime_app import (
    RuntimeCompositionError,
    RuntimeMode,
    _compose_production_identity_authorities,
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

    def test_production_without_identity_and_secret_configuration_fails_closed(self) -> None:
        """Module 3D wires real production authorities, but production must still never
        start with any of them missing -- here, none of the new required settings are
        configured at all (and the DSN is unreachable), so this must fail closed."""
        with self.assertRaises(RuntimeCompositionError):
            create_runtime_app_from_environment(
                env=_env(
                    {
                        "TRADE_PLATFORM_ENVIRONMENT": "production",
                        "POSTGRES_DSN": "postgresql://postgres:postgres@127.0.0.1:5439/trade_platform",  # pragma: allowlist secret
                    }
                )
            )

    def test_paper_with_unreachable_dsn_fails_closed_not_sqlite(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            compose_protected_postgres_app(
                environment=RuntimeMode.PAPER,
                dsn="postgresql://postgres:postgres@127.0.0.1:1/does_not_exist",  # pragma: allowlist secret
                operator_token="test-token",
            )

    def test_compose_protected_postgres_app_rejects_local_research_mode(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            compose_protected_postgres_app(
                environment=RuntimeMode.LOCAL_RESEARCH,  # type: ignore[arg-type]
                dsn="postgresql://postgres:postgres@127.0.0.1:5439/trade_platform",  # pragma: allowlist secret
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


class ProductionIdentityCompositionFailClosedTests(unittest.TestCase):
    """These never need a real database: every case fails before touching Postgres."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.secrets_dir = Path(self._tempdir.name)
        csrf_path = self.secrets_dir / "CSRF_SIGNING_KEY"
        csrf_path.write_text("unit-test-csrf-secret", encoding="utf-8")  # pragma: allowlist secret
        if os.name == "posix":
            csrf_path.chmod(0o600)
        self.valid_kwargs: dict[str, str | None] = {
            "oidc_issuer": "https://identity.example.test/tenant",
            "oidc_audience": "trade-platform",
            "oidc_jwks_url": "https://identity.example.test/jwks.json",
            "identity_policy_name": "operator-console",
            "secrets_directory": str(self.secrets_dir),
        }

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_each_required_setting_is_individually_required(self) -> None:
        for missing in self.valid_kwargs:
            kwargs = dict(self.valid_kwargs)
            kwargs[missing] = None
            with self.subTest(missing=missing), self.assertRaises(RuntimeCompositionError):
                _compose_production_identity_authorities(database=object(), **kwargs)  # type: ignore[arg-type]

    def test_missing_secrets_directory_fails_closed(self) -> None:
        kwargs = dict(self.valid_kwargs)
        kwargs["secrets_directory"] = str(self.secrets_dir / "does-not-exist")
        with self.assertRaises(RuntimeCompositionError):
            _compose_production_identity_authorities(database=object(), **kwargs)  # type: ignore[arg-type]

    def test_missing_csrf_secret_file_fails_closed(self) -> None:
        (self.secrets_dir / "CSRF_SIGNING_KEY").unlink()
        with self.assertRaises(RuntimeCompositionError):
            _compose_production_identity_authorities(database=object(), **self.valid_kwargs)  # type: ignore[arg-type]

    def test_insecure_oidc_issuer_fails_closed(self) -> None:
        kwargs = dict(self.valid_kwargs)
        kwargs["oidc_issuer"] = "http://identity.example.test/tenant"
        with self.assertRaises(RuntimeCompositionError):
            _compose_production_identity_authorities(database=object(), **kwargs)  # type: ignore[arg-type]

    def test_compose_protected_postgres_app_requires_production_settings(self) -> None:
        with self.assertRaises(RuntimeCompositionError):
            compose_protected_postgres_app(
                environment=RuntimeMode.PRODUCTION,
                dsn="postgresql://postgres:postgres@127.0.0.1:1/does_not_exist",  # pragma: allowlist secret
                operator_token=None,
            )


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ProductionRuntimePostgresCompositionTests(unittest.TestCase):
    """Runs against the disposable PostgreSQL service used by test_postgres_integration.py."""

    def setUp(self) -> None:
        from datetime import UTC, datetime, timedelta

        from alembic import command
        from alembic.config import Config

        from trade_platform.external_identity import (
            PostgresIdentitySecurityStore,
            build_external_identity_mapping_policy,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.security import OperatorRole

        self.dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.dsn.replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(config, "head")

        self._tempdir = tempfile.TemporaryDirectory()
        self.secrets_dir = Path(self._tempdir.name)
        csrf_path = self.secrets_dir / "CSRF_SIGNING_KEY"
        csrf_path.write_text("integration-csrf-secret", encoding="utf-8")  # pragma: allowlist secret
        if os.name == "posix":
            csrf_path.chmod(0o600)

        self.policy_name = f"production-runtime-{datetime.now(UTC).timestamp()}"
        database = PostgresDatabase(self.dsn)
        try:
            store = PostgresIdentitySecurityStore(database)
            store.append_policy(
                build_external_identity_mapping_policy(
                    policy_name=self.policy_name,
                    version="v1",
                    issuer="https://identity.example.test/tenant",
                    audience="trade-platform",
                    group_role_map={"trade-operators": OperatorRole.OPERATOR},
                    required_authentication_methods=frozenset({"mfa"}),
                    maximum_session_age=timedelta(hours=1),
                    approved_by="security-owner",
                    approved_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )
        finally:
            database.close()

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_production_composes_real_identity_secret_and_audit_authorities(self) -> None:
        from trade_platform.csrf import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, derive_csrf_token
        from trade_platform.external_identity import (
            ExternalSessionAuthenticator,
            PostgresSessionRevocationStore,
        )
        from trade_platform.oidc_identity import JwksExternalTokenVerifier
        from trade_platform.postgres_audit import PostgresAuditStore

        app = compose_protected_postgres_app(
            environment=RuntimeMode.PRODUCTION,
            dsn=self.dsn,
            operator_token=None,
            oidc_issuer="https://identity.example.test/tenant",
            oidc_audience="trade-platform",
            oidc_jwks_url="https://identity.example.test/jwks.json",
            identity_policy_name=self.policy_name,
            secrets_directory=str(self.secrets_dir),
        )
        try:
            production = app.state.production_identity_authorities
            self.assertIsInstance(production.authenticator, ExternalSessionAuthenticator)
            self.assertIsInstance(production.authenticator.verifier, JwksExternalTokenVerifier)
            self.assertIsInstance(production.revocation_store, PostgresSessionRevocationStore)
            self.assertIsInstance(production.audit_store, PostgresAuditStore)
            self.assertIs(app.state.authenticator, production.authenticator)
            self.assertIs(app.state.authorization_decision_sink, production.authorization_decision_sink)

            client = TestClient(app)
            self.assertEqual(client.get("/health/live").status_code, 200)
            unauthenticated = client.get("/audit/events")
            self.assertEqual(unauthenticated.status_code, 401)

            # A cookie-authenticated mutation without a matching CSRF header is rejected,
            # even against a route CSRF has no other opinion about.
            client.cookies.set(SESSION_COOKIE_NAME, "some-session")
            no_csrf = client.post("/audit/events", json={"event_type": "x", "actor": "y", "payload": {}})
            self.assertEqual(no_csrf.status_code, 403)
            token = derive_csrf_token(csrf_secret=production.csrf_secret, session_id="some-session")
            # Still 401 (no bearer credential) -- proves CSRF ran *and* real auth still applies.
            with_csrf = client.post(
                "/audit/events",
                headers={CSRF_HEADER_NAME: token},
                json={"event_type": "x", "actor": "y", "payload": {}},
            )
            self.assertEqual(with_csrf.status_code, 401)
        finally:
            app.state.postgres_authorities.close()


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
