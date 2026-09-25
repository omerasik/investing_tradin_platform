"""Development application factory for local research against PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from .api import build_app
from .audit import SQLiteAuditStore
from .config import PlatformConfig
from .first_party_capture_archive_v1 import default_archive_root
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

    # The dev API runs on the recorder host, so it reads the default capture
    # archive unless pointed elsewhere; the protected runtime stays UNCONFIGURED.
    capture_root = os.environ.get("TRADE_PLATFORM_CAPTURE_ARCHIVE_ROOT")
    # Same for the R2B research data plane (charts); the default mirrors
    # research_data_plane_v1.default_research_data_root without importing pyarrow.
    research_root = os.environ.get("TRADE_PLATFORM_RESEARCH_DATA_ROOT")

    return build_app(
        config=config,
        audit_store=SQLiteAuditStore(),
        authenticator=OperatorAuthenticator(token),
        rate_limiter=InMemoryRateLimiter(max_requests=10_000),
        operator_dashboard_queries=PostgresOperatorDashboardQueries(db),
        capture_archive_root=Path(capture_root) if capture_root else default_archive_root(),
        research_data_root=(
            Path(research_root) if research_root else Path.home() / ".trade_platform" / "research-data"
        ),
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run(create_dev_app(), host="127.0.0.1", port=port)
