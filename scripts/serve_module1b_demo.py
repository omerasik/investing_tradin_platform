"""Serve a seeded Module 1B dashboard without configured UUID references."""

from __future__ import annotations

import json
import os
from pathlib import Path

import uvicorn

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
from trade_platform.persistence import PersistenceTarget, PostgresDatabase
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator


def application():
    dsn = os.environ["POSTGRES_TEST_DSN"]
    config_path = Path(os.environ["MODULE1B_DASHBOARD_CONFIG_PATH"])
    token_path = Path(os.environ["MODULE1B_DASHBOARD_TOKEN_PATH"])
    token_path.write_text("module1b-fixture-token", encoding="utf-8")
    # The deliberately minimal config proves PostgreSQL discovery, rather than
    # legacy UUID settings, supplies the dashboard workspace references.
    config_path.write_text(json.dumps({
        "api_base_url": "http://127.0.0.1:8768",
        "operator_token_file": str(token_path.resolve()),
        "dashboard_origin": "http://127.0.0.1:3001",
    }), encoding="utf-8")
    database = PostgresDatabase(dsn)
    return build_app(
        PlatformConfig(
            environment="ci", persistence_target=PersistenceTarget.POSTGRES,
            persistence_location=dsn,
        ),
        SQLiteAuditStore(), OperatorAuthenticator("module1b-fixture-token"),
        InMemoryRateLimiter(max_requests=10_000),
        operator_dashboard_queries=PostgresOperatorDashboardQueries(database),
    )


if __name__ == "__main__":
    uvicorn.run(application(), host="127.0.0.1", port=8768)
