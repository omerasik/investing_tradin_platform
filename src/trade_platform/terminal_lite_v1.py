"""Read-only Lite composition of the existing API, queries and chart data plane.

No execution authority, command ledger, scheduler, provider or broker is wired.
Use a SELECT-only PostgreSQL login, independently migrated by the local launcher.
The platform's legacy `paper_trading_enabled` flag describes build capability;
it does not enable paper execution in this composition.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import build_app
from .config import PlatformConfig
from .operator_dashboard import DashboardQueryError, PostgresOperatorDashboardQueries
from .persistence import PersistenceTarget, PostgresDatabase
from .security import InMemoryRateLimiter, OperatorAuthenticator


def create_lite_app() -> FastAPI:
    from psycopg.conninfo import conninfo_to_dict

    dsn = os.environ["LITE_READ_DSN"]  # deliberately never reads POSTGRES_DSN
    settings = conninfo_to_dict(dsn)
    if settings.get("dbname") != "trade_terminal_lite" or settings.get("user") != "lite_reader":
        raise ValueError("lite_requires_separate_database_and_reader")
    if os.environ.get("TRADE_PLATFORM_LITE") != "1":
        raise ValueError("lite_query_resource_profile_required")
    root = Path(os.environ["LITE_RESEARCH_ROOT"])
    if not (root / "lite-provenance.json").is_file():
        raise ValueError("lite_verified_subset_required")
    if sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) > 16 * 1024 * 1024:
        raise ValueError("lite_subset_exceeds_disk_budget")
    from .public_archive_research_bars_v1 import load_research_bar_dataset_v1
    from .research_data_plane_v1 import ResearchFrameStoreV1
    from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1

    report = json.loads((root / "lite-provenance.json").read_text(encoding="utf-8"))
    if (
        report.get("version") != "terminal-lite-v1"
        or report.get("classification") != "REAL_HISTORICAL_T2_EVENT_TIME"
        or sorted(item["symbol"] for item in report["datasets"])
        != ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    ):
        raise ValueError("lite_three_market_subset_required")
    if date.fromisoformat(report["utc_day"]) >= UNTOUCHED_HOLDOUT_BOUNDARY_V1.date():
        raise ValueError("lite_holdout_forbidden")
    store = ResearchFrameStoreV1(root)
    expected_catalogue = {f"{item['dataset_version_id']}.json" for item in report["datasets"]}
    if {
        path.name for path in (store.root / "datasets/public-archive-bars").glob("*.json")
    } != expected_catalogue:
        raise ValueError("lite_catalogue_must_match_verified_subset")
    for item in report["datasets"]:
        dataset = load_research_bar_dataset_v1(store, UUID(item["dataset_version_id"]))
        if (
            dataset.content_hash != item["content_hash"]
            or dataset.identity["bar_frame"]["row_count"] > 1440
            or dataset.identity["symbol"] != item["symbol"]
            or dataset.bar_frame_manifest_hash != item["manifest_hash"]
            or dataset.identity["first_utc_day"] != report["utc_day"]
            or dataset.identity["last_utc_day"] != report["utc_day"]
        ):
            raise ValueError("lite_subset_identity_or_size_mismatch")
    database = PostgresDatabase(dsn)
    try:
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, current_setting('server_version_num')::int"
            )
            name, user, version = cursor.fetchone()
            cursor.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user"
            )
            privileges = cursor.fetchone()
            cursor.execute("SHOW default_transaction_read_only")
            readonly = cursor.fetchone()[0]
            if (name, user) != (
                "trade_terminal_lite",
                "lite_reader",
            ) or not 160000 <= version < 170000:
                raise ValueError("lite_database_identity_or_version_mismatch")
            cursor.execute(
                "SELECT has_database_privilege(current_user, current_database(), 'CREATE'), "
                "has_schema_privilege(current_user, 'public', 'CREATE'), "
                "EXISTS (SELECT 1 FROM information_schema.role_table_grants "
                "WHERE grantee=current_user AND table_schema='public' AND privilege_type<>'SELECT')"
            )
            if any(privileges) or readonly != "on" or any(cursor.fetchone()):
                raise ValueError("lite_reader_must_be_select_only")
            cursor.execute("SELECT version_num FROM alembic_version")
            if cursor.fetchall() != [("20261020_0067",)]:
                raise ValueError("lite_schema_revision_mismatch")
        queries = PostgresOperatorDashboardQueries(database)
        app = build_app(
            config=PlatformConfig(
                environment="local_research",
                persistence_target=PersistenceTarget.POSTGRES,
                persistence_location=dsn,
            ),
            authenticator=OperatorAuthenticator(os.environ["TRADE_PLATFORM_OPERATOR_TOKEN"]),
            rate_limiter=InMemoryRateLimiter(max_requests=300),
            operator_dashboard_queries=queries,
            research_data_root=root,
        )
    except Exception:
        database.close()
        raise

    @app.middleware("http")
    async def read_only(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "LITE_READ_ONLY: research commands, holdout, paper execution and brokers disabled"
                },
            )
        return await call_next(request)

    @app.get("/health/lite-ready")
    def lite_ready():
        # The generic local health route describes platform build capabilities;
        # this deployment probe describes only Lite's actual execution surface.
        try:
            queries.workspace_references()
        except DashboardQueryError:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {
            "status": "ready",
            "environment": "lite",
            "paper_execution_enabled": False,
            "live_trading_enabled": False,
            "research_commands_enabled": False,
        }

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            async with previous_lifespan(application) as state:
                yield state
        finally:
            database.close()

    app.router.lifespan_context = lifespan

    return app
