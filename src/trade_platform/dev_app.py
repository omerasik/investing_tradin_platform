"""Development application factory for local research against PostgreSQL."""

from __future__ import annotations

import os

from fastapi import FastAPI

from .api import build_app
from .audit import SQLiteAuditStore
from .config import PlatformConfig
from .operator_dashboard import PostgresOperatorDashboardQueries
from .persistence import PersistenceTarget, PostgresDatabase
from .security import InMemoryRateLimiter, OperatorAuthenticator


def create_dev_app(database: PostgresDatabase | None = None) -> FastAPI:
    dsn = (
        os.environ.get("POSTGRES_DSN")
        or os.environ.get("POSTGRES_TEST_DSN")
        or "postgresql://postgres:postgres@127.0.0.1:5439/trade_platform"  # pragma: allowlist secret
    )
    token = os.environ.get("TRADE_PLATFORM_OPERATOR_TOKEN", "local-dev-operator-token")
    env_name = os.environ.get("TRADE_PLATFORM_ENVIRONMENT", "local_research")

    db = database or PostgresDatabase(dsn)
    config = PlatformConfig(
        environment=env_name,
        persistence_target=PersistenceTarget.POSTGRES,
        persistence_location=dsn,
    )

    return build_app(
        config=config,
        audit_store=SQLiteAuditStore(),
        authenticator=OperatorAuthenticator(token),
        rate_limiter=InMemoryRateLimiter(max_requests=10_000),
        operator_dashboard_queries=PostgresOperatorDashboardQueries(db),
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run(create_dev_app(), host="127.0.0.1", port=port)
