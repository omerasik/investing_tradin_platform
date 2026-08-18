"""Serve Cycle 208 browser evidence from an already-seeded disposable PostgreSQL DB."""

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
    config_path = Path(os.environ["CYCLE208_DASHBOARD_CONFIG_PATH"])
    token_path = Path(os.environ["CYCLE208_DASHBOARD_TOKEN_PATH"])
    database = PostgresDatabase(dsn)
    with database.transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT feature_id,instrument_id,dataset_version,computed_at FROM feature_materializations "
            "ORDER BY computed_at DESC,materialization_id DESC LIMIT 1"
        )
        feature = cursor.fetchone()
        cursor.execute("SELECT scorecard_id FROM strategy_scorecards ORDER BY evaluated_at DESC,scorecard_id DESC LIMIT 1")
        scorecard = cursor.fetchone()
        cursor.execute("SELECT run_id FROM regime_runs ORDER BY evaluated_at DESC,run_id DESC LIMIT 1")
        regime = cursor.fetchone()
        cursor.execute("SELECT run_id FROM portfolio_construction_runs ORDER BY constructed_at DESC,run_id DESC LIMIT 1")
        portfolio = cursor.fetchone()
        cursor.execute(
            "SELECT l.instrument_id FROM news_document_entity_links l JOIN news_document_revisions d USING(document_revision_id) "
            "ORDER BY d.published_at DESC,l.entity_link_id DESC LIMIT 1"
        )
        news = cursor.fetchone()
        cursor.execute("SELECT service_version_id FROM sre_service_versions ORDER BY created_at DESC,service_version_id DESC LIMIT 1")
        service = cursor.fetchone()
    if any(item is None for item in (feature, scorecard, regime, portfolio, news, service)):
        raise RuntimeError("cycle208_postgres_evidence_incomplete")
    token_path.write_text("fixture-token", encoding="utf-8")
    config_path.write_text(json.dumps({
        "api_base_url": "http://127.0.0.1:8766",
        "operator_token_file": str(token_path.resolve()),
        "dashboard_origin": "http://127.0.0.1:3000",
        "feature_definition_id": str(feature[0]),
        "feature_instrument": str(feature[1]),
        "feature_dataset_version": str(feature[2]),
        "feature_decision_time": feature[3].isoformat(),
        "scorecard_id": str(scorecard[0]),
        "regime_run_id": str(regime[0]),
        "portfolio_construction_run_id": str(portfolio[0]),
        "news_instrument": str(news[0]),
        "sre_service_version_id": str(service[0]),
    }, indent=2), encoding="utf-8")
    return build_app(
        PlatformConfig(
            environment="ci", persistence_target=PersistenceTarget.POSTGRES,
            persistence_location=dsn,
        ),
        SQLiteAuditStore(), OperatorAuthenticator("fixture-token"),
        InMemoryRateLimiter(max_requests=10_000),
        operator_dashboard_queries=PostgresOperatorDashboardQueries(database),
    )


if __name__ == "__main__":
    uvicorn.run(application(), host="127.0.0.1", port=8766)
