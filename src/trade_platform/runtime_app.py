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
from enum import StrEnum

from fastapi import FastAPI

from .api import build_app
from .audit import SQLiteAuditStore
from .config import PlatformConfig
from .observability import MetricsRegistry
from .operational_alerts import PostgresOperationalAlertStore
from .operator_dashboard import PostgresOperatorDashboardQueries
from .persistence import PersistenceError, PersistenceTarget, PostgresDatabase
from .postgres_paper_oms import PostgresPaperOms
from .security import InMemoryRateLimiter, OperatorAuthenticator

__all__ = [
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


def compose_protected_postgres_app(
    *,
    environment: RuntimeMode,
    dsn: str,
    operator_token: str | None,
) -> FastAPI:
    """Build the protected (PAPER or PRODUCTION-shaped) PostgreSQL-backed application.

    Fails closed -- raises :class:`RuntimeCompositionError` -- rather than returning an
    app backed by SQLite or in-memory state, for any of: an invalid DSN, an unreachable
    database, or a PostgreSQL authority that cannot be composed.
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

    try:
        config = PlatformConfig(
            environment=environment.value,
            persistence_target=PersistenceTarget.POSTGRES,
            persistence_location=dsn,
        )
        app = build_app(
            config=config,
            audit_store=SQLiteAuditStore(),
            authenticator=OperatorAuthenticator.from_environment()
            if operator_token is None
            else OperatorAuthenticator(operator_token),
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
    except Exception:
        authorities.close()
        raise

    app.state.postgres_authorities = authorities

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
      - ``TRADE_PLATFORM_OPERATOR_TOKEN``: optional override, otherwise
        ``OperatorAuthenticator.from_environment()`` is used.

    Fail-closed behavior:
      - ``paper`` or ``production`` with no ``POSTGRES_DSN`` -> raises
        :class:`RuntimeCompositionError` before any app object is returned.
      - ``paper`` or ``production`` with an unreachable/invalid Postgres DSN -> raises
        :class:`RuntimeCompositionError`.
      - ``production`` additionally always fails closed today: production identity,
        secret-manager and production-grade audit-durability authorities do not exist
        yet in this codebase, so this factory refuses to pretend the platform is
        production-ready rather than serving a partially-wired production app.
    """
    raw_environment = (env("TRADE_PLATFORM_ENVIRONMENT") or RuntimeMode.LOCAL_RESEARCH.value).strip().lower()
    try:
        mode = RuntimeMode(raw_environment)
    except ValueError as error:
        raise RuntimeCompositionError(f"unknown_runtime_environment:{raw_environment}") from error

    if mode is RuntimeMode.LOCAL_RESEARCH:
        return _build_local_research_app()

    if mode is RuntimeMode.PRODUCTION:
        # Deliberate, documented fail-closed: do not fake a green production path.
        # See docs/MODULE_3C_POSTGRES_RUNTIME_WIRING.md "Remaining blockers".
        raise RuntimeCompositionError(
            "production_mode_not_yet_supported: production identity, secret-manager and "
            "production-grade audit-durability authorities are not implemented; refusing "
            "to start rather than serve an unready production app"
        )

    dsn = env("POSTGRES_DSN")
    if not dsn:
        raise RuntimeCompositionError("required_environment_variable_missing:POSTGRES_DSN")
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
