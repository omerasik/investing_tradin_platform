"""Canonical runtime composition root: environment -> persistence target -> FastAPI app.

This is the *only* place that is allowed to decide, from environment/configuration,
which persistence authorities back the served FastAPI application. It exists because
the module-level ``app = build_app()`` object in :mod:`trade_platform.api` (the object
Docker actually serves) intentionally defaults every optional store to ``None`` rather
than to a SQLite implementation -- ``build_app`` itself never silently constructs SQLite
authorities for the caller. The defect this module fixes is that nothing previously
composed the already-existing PostgreSQL authority graph (see
:mod:`trade_platform.postgres_runtime`) and handed it to ``build_app`` for the
container's default entrypoint, so the container ran an unconfigured, read-nothing API.

No-fallback rule (absolute):
    - Missing PostgreSQL configuration in a protected runtime mode -> startup failure.
    - Unreachable PostgreSQL in a protected runtime mode -> startup failure.
    - A required PostgreSQL authority that cannot be composed -> startup failure.
    - PostgreSQL failure NEVER falls back to SQLite or in-memory state for a protected
      runtime mode. There is no code path in this module that does that.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from fastapi import FastAPI

from .api import build_app
from .audit import AuditStore, SQLiteAuditStore
from .config import PlatformConfig
from .csrf import CsrfProtectionMiddleware
from .external_identity import (
    ExternalIdentityError,
    ExternalIdentityMappingPolicy,
    ExternalSessionAuthenticator,
    PostgresIdentitySecurityStore,
    PostgresSessionRevocationStore,
)
from .observability import MetricsRegistry
from .oidc_identity import JwksExternalTokenVerifier, OidcConfigurationError
from .operational_alerts import PostgresOperationalAlertStore
from .operator_dashboard import PostgresOperatorDashboardQueries
from .persistence import PersistenceError, PersistenceTarget, PostgresDatabase
from .postgres_audit import PostgresAuditStore
from .postgres_paper_oms import PostgresPaperOms
from .secrets_manager import FileSecretProvider, SecretUnavailableError
from .security import AuthorizationDecisionSink, InMemoryRateLimiter, OperatorAuthenticator

__all__ = [
    "ProductionIdentityAuthorities",
    "ProtectedPostgresAuthorities",
    "RuntimeCompositionError",
    "RuntimeMode",
    "compose_protected_postgres_app",
    "create_runtime_app_from_environment",
]


class RuntimeMode(StrEnum):
    """The only recognized runtime modes for this composition root."""

    LOCAL_RESEARCH = "local_research"
    PAPER = "paper"
    PRODUCTION = "production"


class RuntimeCompositionError(RuntimeError):
    """Raised whenever a protected runtime cannot be composed safely.

    Raising this (instead of degrading to SQLite or an unconfigured app) is the
    fail-closed behavior this module exists to guarantee for PAPER and PRODUCTION.
    """


class ProtectedPostgresAuthorities:
    """The PostgreSQL authority graph wired into a protected-runtime ``app.state``.

    Holds exactly the authorities the read/audit-only FastAPI app in :mod:`trade_platform.api`
    needs. This composition root owns the single underlying connection's lifecycle
    (see ``close``) -- individual authorities never open their own connections.
    """

    __slots__ = ("alert_store", "database", "operator_dashboard_queries", "paper_oms")

    def __init__(
        self,
        database: PostgresDatabase,
        paper_oms: PostgresPaperOms,
        alert_store: PostgresOperationalAlertStore,
        operator_dashboard_queries: PostgresOperatorDashboardQueries,
    ) -> None:
        self.database = database
        self.paper_oms = paper_oms
        self.alert_store = alert_store
        self.operator_dashboard_queries = operator_dashboard_queries

    def close(self) -> None:
        self.database.close()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeCompositionError(f"required_environment_variable_missing:{name}")
    return value


def _require_production_setting(value: str | None, name: str) -> str:
    if value is None or not value.strip():
        raise RuntimeCompositionError(f"required_environment_variable_missing:{name}")
    return value.strip()


@dataclass(slots=True)
class ProductionIdentityAuthorities:
    """The identity, secret-management and durable-audit authorities production requires.

    Composed only for ``RuntimeMode.PRODUCTION`` -- see
    ``docs/MODULE_3D_PRODUCTION_IDENTITY_SECRETS_AUDIT.md``. Each field replaces a
    documented Module 3C blocker: ``authenticator`` replaces the local static-token
    boundary with real JWKS-verified external identity plus durable revocation;
    ``audit_store`` replaces the dev/paper-only ``SQLiteAuditStore``;
    ``authorization_decision_sink`` is the same durable Postgres authority that made
    ``requires_durable_authorization_audit`` satisfiable; ``secret_provider`` replaces
    raw environment variables as production's final secret authority.
    """

    authenticator: ExternalSessionAuthenticator
    audit_store: PostgresAuditStore
    authorization_decision_sink: AuthorizationDecisionSink
    revocation_store: PostgresSessionRevocationStore
    secret_provider: FileSecretProvider
    csrf_secret: str


def _compose_production_identity_authorities(
    *,
    database: PostgresDatabase,
    oidc_issuer: str | None,
    oidc_audience: str | None,
    oidc_jwks_url: str | None,
    identity_policy_name: str | None,
    secrets_directory: str | None,
) -> ProductionIdentityAuthorities:
    """Compose the identity/secret/audit authorities required to start production.

    Fails closed with :class:`RuntimeCompositionError` for any missing configuration,
    unreachable secrets directory, missing CSRF signing secret, or missing/unapproved
    identity mapping policy -- production never starts partially wired.
    """
    issuer = _require_production_setting(oidc_issuer, "TRADE_PLATFORM_OIDC_ISSUER")
    audience = _require_production_setting(oidc_audience, "TRADE_PLATFORM_OIDC_AUDIENCE")
    jwks_url = _require_production_setting(oidc_jwks_url, "TRADE_PLATFORM_OIDC_JWKS_URL")
    policy_name = _require_production_setting(
        identity_policy_name, "TRADE_PLATFORM_IDENTITY_POLICY_NAME"
    )
    secrets_dir = _require_production_setting(secrets_directory, "TRADE_PLATFORM_SECRETS_DIR")

    try:
        secret_provider = FileSecretProvider(Path(secrets_dir))
    except SecretUnavailableError as error:
        raise RuntimeCompositionError("production_secrets_directory_unavailable") from error

    try:
        csrf_secret = secret_provider.get_secret("CSRF_SIGNING_KEY")
    except SecretUnavailableError as error:
        raise RuntimeCompositionError("production_csrf_secret_unavailable") from error

    identity_store = PostgresIdentitySecurityStore(database)
    try:
        policy: ExternalIdentityMappingPolicy | None = identity_store.latest_enabled_policy(
            policy_name
        )
    except (ExternalIdentityError, PersistenceError) as error:
        raise RuntimeCompositionError("production_identity_policy_lookup_failed") from error
    if policy is None:
        raise RuntimeCompositionError(
            f"production_identity_mapping_policy_not_configured:{policy_name}"
        )

    try:
        verifier = JwksExternalTokenVerifier.from_jwks_url(
            issuer=issuer, audience=audience, jwks_url=jwks_url
        )
    except OidcConfigurationError as error:
        raise RuntimeCompositionError("production_oidc_verifier_misconfigured") from error

    revocation_store = PostgresSessionRevocationStore(database)
    authenticator = ExternalSessionAuthenticator(
        verifier=verifier,
        policy=policy,
        revocation_store=revocation_store,
    )
    return ProductionIdentityAuthorities(
        authenticator=authenticator,
        audit_store=PostgresAuditStore(database),
        authorization_decision_sink=identity_store,
        revocation_store=revocation_store,
        secret_provider=secret_provider,
        csrf_secret=csrf_secret,
    )


def compose_protected_postgres_app(
    *,
    environment: RuntimeMode,
    dsn: str,
    operator_token: str | None,
    oidc_issuer: str | None = None,
    oidc_audience: str | None = None,
    oidc_jwks_url: str | None = None,
    identity_policy_name: str | None = None,
    secrets_directory: str | None = None,
) -> FastAPI:
    """Build the protected (PAPER or PRODUCTION-shaped) PostgreSQL-backed application.

    Fails closed -- raises :class:`RuntimeCompositionError` -- rather than returning an
    app backed by SQLite or in-memory state, for any of: an invalid DSN, an unreachable
    database, or a PostgreSQL authority that cannot be composed. For
    ``RuntimeMode.PRODUCTION`` this additionally requires and composes real external
    identity (JWKS-verified, durably revocable), a file-based production secret
    provider, and a durable PostgreSQL audit authority -- see
    :func:`_compose_production_identity_authorities`. ``PAPER`` behavior is unchanged
    from Module 3C.
    """
    if environment not in {RuntimeMode.PAPER, RuntimeMode.PRODUCTION}:
        raise RuntimeCompositionError("compose_protected_postgres_app_requires_protected_mode")

    try:
        database = PostgresDatabase(dsn)
    except PersistenceError as error:
        raise RuntimeCompositionError("postgres_unreachable_or_invalid_dsn") from error

    try:
        authorities = ProtectedPostgresAuthorities(
            database=database,
            paper_oms=PostgresPaperOms(database),
            alert_store=PostgresOperationalAlertStore(database),
            operator_dashboard_queries=PostgresOperatorDashboardQueries(database),
        )
    except Exception as error:
        database.close()
        raise RuntimeCompositionError("postgres_authority_composition_failed") from error

    production_authorities: ProductionIdentityAuthorities | None = None
    if environment is RuntimeMode.PRODUCTION:
        try:
            production_authorities = _compose_production_identity_authorities(
                database=database,
                oidc_issuer=oidc_issuer,
                oidc_audience=oidc_audience,
                oidc_jwks_url=oidc_jwks_url,
                identity_policy_name=identity_policy_name,
                secrets_directory=secrets_directory,
            )
        except RuntimeCompositionError:
            authorities.close()
            raise
        except Exception as error:
            authorities.close()
            raise RuntimeCompositionError("production_identity_composition_failed") from error

    try:
        config = PlatformConfig(
            environment=environment.value,
            persistence_target=PersistenceTarget.POSTGRES,
            persistence_location=dsn,
        )
        audit_store: AuditStore = (
            production_authorities.audit_store
            if production_authorities is not None
            else SQLiteAuditStore()
        )
        authenticator = (
            production_authorities.authenticator
            if production_authorities is not None
            else (
                OperatorAuthenticator.from_environment()
                if operator_token is None
                else OperatorAuthenticator(operator_token)
            )
        )
        authorization_decision_sink: AuthorizationDecisionSink | None = (
            production_authorities.authorization_decision_sink
            if production_authorities is not None
            else None
        )
        app = build_app(
            config=config,
            audit_store=audit_store,
            authenticator=authenticator,
            authorization_decision_sink=authorization_decision_sink,
            rate_limiter=InMemoryRateLimiter(max_requests=10_000),
            metrics=MetricsRegistry(),
            alert_store=authorities.alert_store,
            paper_oms=authorities.paper_oms,
            operator_dashboard_queries=authorities.operator_dashboard_queries,
            # Not wired for the protected runtime -- explicitly left unavailable
            # (returns 503, never silently SQLite). See
            # docs/MODULE_3C_POSTGRES_RUNTIME_WIRING.md for why:
            #   - risk_decisions: PostgresRiskStore has no decisions_for_intent
            #     projection yet (future migration blocker).
            #   - promotion_ledger: PostgresPromotionLedger has no by-id read
            #     projection yet (future migration blocker).
            #   - return_history: PostgresPortfolioReturnStore only supports
            #     append/observations_as_of, not the ingestion-cadence tracking
            #     API these routes need (future migration blocker).
            #   - investment_store, fundamental_store, agent_research_store,
            #     strategy_registry, experiment_store: no Postgres sibling exists
            #     (out of scope for this module; not required for protected
            #     paper-API startup).
        )
        if production_authorities is not None:
            app.add_middleware(
                CsrfProtectionMiddleware, csrf_secret=production_authorities.csrf_secret
            )
    except Exception:
        authorities.close()
        raise

    app.state.postgres_authorities = authorities
    app.state.production_identity_authorities = production_authorities

    @app.on_event("shutdown")
    def _close_postgres_authorities() -> None:  # pragma: no cover - exercised via lifespan
        authorities.close()

    return app


def _build_local_research_app() -> FastAPI:
    """Unchanged local/dev composition: SQLite-default ``build_app()``.

    Keeps ``scripts/dev.py --reset-db --demo`` and Module 1A/1B local flows exactly as
    they were; those already target ``trade_platform.dev_app:create_dev_app`` for the
    Postgres-backed dev flow, or this default local composition for the pure-SQLite one.
    """
    return build_app(config=PlatformConfig(environment=RuntimeMode.LOCAL_RESEARCH.value))


def create_runtime_app_from_environment(
    *, env: Callable[[str], str | None] = os.environ.get
) -> FastAPI:
    """The single canonical entrypoint: environment -> composed FastAPI app.

    Reads:
      - ``TRADE_PLATFORM_ENVIRONMENT``: ``local_research`` (default), ``paper`` or
        ``production``.
      - ``POSTGRES_DSN``: required for ``paper``/``production``; a ``postgres://`` or
        ``postgresql://`` connection string.
      - ``TRADE_PLATFORM_OPERATOR_TOKEN``: optional override for ``paper``, otherwise
        ``OperatorAuthenticator.from_environment()`` is used. Ignored for ``production``,
        which never uses the static local-bearer boundary.
      - ``production`` additionally requires ``TRADE_PLATFORM_OIDC_ISSUER``,
        ``TRADE_PLATFORM_OIDC_AUDIENCE``, ``TRADE_PLATFORM_OIDC_JWKS_URL``,
        ``TRADE_PLATFORM_IDENTITY_POLICY_NAME`` and ``TRADE_PLATFORM_SECRETS_DIR`` --
        see :func:`_compose_production_identity_authorities` and
        ``docs/MODULE_3D_PRODUCTION_IDENTITY_SECRETS_AUDIT.md``.

    Fail-closed behavior:
      - ``paper`` or ``production`` with no ``POSTGRES_DSN`` -> raises
        :class:`RuntimeCompositionError` before any app object is returned.
      - ``paper`` or ``production`` with an unreachable/invalid Postgres DSN -> raises
        :class:`RuntimeCompositionError`.
      - ``production`` with any required identity/secret/audit configuration missing,
        an unreachable secrets directory, a missing CSRF signing secret, or no approved
        identity mapping policy -> raises :class:`RuntimeCompositionError`. Production
        never starts partially wired.
    """
    raw_environment = (env("TRADE_PLATFORM_ENVIRONMENT") or RuntimeMode.LOCAL_RESEARCH.value).strip().lower()
    try:
        mode = RuntimeMode(raw_environment)
    except ValueError as error:
        raise RuntimeCompositionError(f"unknown_runtime_environment:{raw_environment}") from error

    if mode is RuntimeMode.LOCAL_RESEARCH:
        return _build_local_research_app()

    dsn = env("POSTGRES_DSN")
    if not dsn:
        raise RuntimeCompositionError("required_environment_variable_missing:POSTGRES_DSN")

    if mode is RuntimeMode.PRODUCTION:
        return compose_protected_postgres_app(
            environment=mode,
            dsn=dsn,
            operator_token=None,
            oidc_issuer=env("TRADE_PLATFORM_OIDC_ISSUER"),
            oidc_audience=env("TRADE_PLATFORM_OIDC_AUDIENCE"),
            oidc_jwks_url=env("TRADE_PLATFORM_OIDC_JWKS_URL"),
            identity_policy_name=env("TRADE_PLATFORM_IDENTITY_POLICY_NAME"),
            secrets_directory=env("TRADE_PLATFORM_SECRETS_DIR"),
        )

    return compose_protected_postgres_app(
        environment=mode,
        dsn=dsn,
        operator_token=env("TRADE_PLATFORM_OPERATOR_TOKEN"),
    )


# Import-time composition mirrors ``trade_platform.api``'s ``app = build_app()`` module
# object, which is what an ASGI server target string (``trade_platform.runtime_app:app``)
# needs. Unlike ``api.py``'s default, this one reads the real environment and fails
# closed for paper/production rather than silently defaulting to an unconfigured app.
app = create_runtime_app_from_environment()
