"""Disposable-PostgreSQL acceptance coverage for the Module 1B demo contract.

The test owns a distinct database below the configured local/CI PostgreSQL
instance.  It deliberately never clears shared evidence and therefore can run
alongside the broader integration suite.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, urlunparse

import psycopg
from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
from trade_platform.persistence import PostgresDatabase
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _test_dsn(source_dsn: str, database_name: str) -> str:
    parsed = urlparse(source_dsn)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class Module1BDemoAcceptanceTests(unittest.TestCase):
    """A clear entrypoint for first seed, replay, coherence, PIT, and discovery."""

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest("Module 1B acceptance requires a local or CI disposable PostgreSQL DSN")
        cls.database_name = f"module1b_demo_acceptance_{os.getpid()}"
        cls.dsn = _test_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')

        from alembic import command
        from alembic.config import Config

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", cls.dsn.replace("postgresql://", "postgresql+psycopg://", 1))
        old_dsn = os.environ.get("POSTGRES_TEST_DSN")
        try:
            os.environ["POSTGRES_TEST_DSN"] = cls.dsn
            command.upgrade(config, "head")
        finally:
            if old_dsn is not None:
                os.environ["POSTGRES_TEST_DSN"] = old_dsn
        cls.database = PostgresDatabase(cls.dsn)
        cls.queries = PostgresOperatorDashboardQueries(cls.database)
        spec = importlib.util.spec_from_file_location("module1b_demo_seed", ROOT / "scripts" / "seed_demo_evidence.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("module1b_demo_seed_module_unavailable")
        cls.seed_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.seed_module
        spec.loader.exec_module(cls.seed_module)
        cls.seed_result = {}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    def test_01_empty_database_returns_explicit_unavailable_discovery(self) -> None:
        references = self.queries.workspace_references()
        self.assertEqual(references.state, "UNAVAILABLE")
        pages = (
            self.queries.instruments(limit=20, offset=0),
            self.queries.strategies(limit=20, offset=0),
            self.queries.experiments(strategy_id=None, limit=20, offset=0),
            self.queries.investment_theses(limit=20, offset=0),
            self.queries.investment_portfolios(limit=20, offset=0),
            self.queries.paper_orders(limit=20, offset=0),
            self.queries.feature_definitions(family=None, limit=20, offset=0),
            self.queries.signals(as_of=self.seed_module.DEMO_AT, status=None, instrument=None, strategy_version=None, limit=20, offset=0),
            self.queries.risk_decisions(limit=20, offset=0),
            self.queries.news_events(instrument=None, entity=None, category=None, start=None, end=None, correction_state=None, limit=20, offset=0),
        )
        self.assertTrue(all(page.state == "UNAVAILABLE" for page in pages))

        app = build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("module1b-token"),
            InMemoryRateLimiter(max_requests=100), operator_dashboard_queries=self.queries,
        )
        client = TestClient(app)
        headers = {"Authorization": "Bearer module1b-token"}
        for path in (
            "/operator-dashboard/workspace-references",
            "/operator-dashboard/instruments?limit=20&offset=0",
            "/operator-dashboard/strategies?limit=20&offset=0",
            "/operator-dashboard/experiments?limit=20&offset=0",
            "/operator-dashboard/investment-theses?limit=20&offset=0",
            "/operator-dashboard/investment-portfolios?limit=20&offset=0",
            "/operator-dashboard/paper-orders?limit=20&offset=0",
        ):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)
                response = client.get(path, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn(self.dsn, response.text)
                self.assertEqual(client.post(path.split("?")[0], headers=headers).status_code, 405)
        self.assertEqual(client.get("/operator-dashboard/instruments?limit=101", headers=headers).status_code, 422)
        self.assertEqual(client.get("/operator-dashboard/strategies?offset=-1", headers=headers).status_code, 422)

    def test_02_first_seed_replay_and_conflict_are_safe(self) -> None:
        first = self.seed_module.seed_demo_evidence(self.dsn)
        self.__class__.seed_result = first
        tables = (
            "professional_instruments", "historical_dataset_versions", "feature_materializations",
            "strategy_versions", "research_experiments", "strategy_scorecards", "regime_runs",
            "portfolio_construction_runs", "investment_theses", "paper_order_intents", "oms_events",
            "fills", "reconciliations", "news_document_revisions", "sre_service_versions",
        )
        with self.database.transaction() as connection, connection.cursor() as cursor:
            before = {}
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                before[table] = cursor.fetchone()[0]
        second = self.seed_module.seed_demo_evidence(self.dsn)
        with self.database.transaction() as connection, connection.cursor() as cursor:
            after = {}
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                after[table] = cursor.fetchone()[0]
        self.assertEqual(first, second)
        self.assertEqual(before, after)
        with patch.object(self.seed_module, "digest", return_value="f" * 64), self.assertRaisesRegex(
            RuntimeError, "demo_seed_conflicting_scenario_identity"
        ):
            self.seed_module.seed_demo_evidence(self.dsn)

    def test_03_cross_domain_coherence_and_pit(self) -> None:
        seed_result = getattr(self.__class__, "seed_result", None) or self.seed_module.seed_demo_evidence(self.dsn)
        stable_id = self.seed_module.stable_id
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT e.dataset_version_id,v.strategy_id FROM research_experiments e "
                "JOIN strategy_versions v USING(strategy_version_id) WHERE e.experiment_id=%s",
                (stable_id("experiment"),),
            )
            experiment = cursor.fetchone()
            cursor.execute(
                "SELECT strategy_id,research_run_id,dataset_version FROM strategy_scorecards WHERE scorecard_id=%s",
                (seed_result["scorecard_id"],),
            )
            scorecard = cursor.fetchone()
            cursor.execute("SELECT regime_run_id FROM portfolio_construction_runs WHERE run_id=%s", (stable_id("portfolio-run"),))
            portfolio = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM portfolio_sleeve_inputs WHERE run_id=%s", (stable_id("portfolio-run"),))
            sleeves = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM oms_events WHERE intent_id=%s", (stable_id("paper-intent"),))
            oms_events = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM fills WHERE intent_id=%s", (stable_id("paper-intent"),))
            fills = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM news_event_lineage WHERE relation='RETRACTS'")
            retractions = cursor.fetchone()[0]
        self.assertEqual(experiment[1], scorecard[0])
        self.assertEqual(scorecard[1], stable_id("experiment"))
        self.assertEqual(portfolio[0], stable_id("regime-run"))
        self.assertEqual((sleeves, oms_events, fills, retractions), (1, 4, 2, 1))

        feature_page = self.queries.feature_definitions(family="PRICE_RETURNS", limit=20, offset=0)
        feature_id = next(item.feature_definition_id for item in feature_page.items if item.feature_name == "demo_return")
        old = self.queries.feature_materializations(
            feature_id=feature_id, instrument="DEMO:XNAS:DEMO_EQ_A", dataset_version=self.seed_module.DEMO_SEED_VERSION,
            decision_time=self.seed_module.DEMO_AT - timedelta(hours=13), limit=20, offset=0,
        )
        current = self.queries.feature_materializations(
            feature_id=feature_id, instrument="DEMO:XNAS:DEMO_EQ_A", dataset_version=self.seed_module.DEMO_SEED_VERSION,
            decision_time=self.seed_module.DEMO_AT, limit=20, offset=0,
        )
        self.assertEqual(old.items, [])
        self.assertEqual(len(current.items), 1)
        self.assertLessEqual(current.items[0].knowledge_time, self.seed_module.DEMO_AT)

    def test_04_all_demo_discovery_projections_are_available_and_bounded(self) -> None:
        pages = {
            "instruments": self.queries.instruments(limit=20, offset=0),
            "features": self.queries.feature_definitions(family=None, limit=20, offset=0),
            "strategy": self.queries.strategies(limit=20, offset=0),
            "backtest": self.queries.experiments(strategy_id=self.seed_module.stable_id("strategy"), limit=20, offset=0),
            "investment": self.queries.investment_theses(limit=20, offset=0),
            "portfolio": self.queries.investment_portfolios(limit=20, offset=0),
            "paper_oms": self.queries.paper_orders(limit=20, offset=0),
            "signals": self.queries.signals(as_of=self.seed_module.DEMO_AT, status=None, instrument=None, strategy_version=None, limit=20, offset=0),
            "risk": self.queries.risk_decisions(limit=20, offset=0),
            "news": self.queries.news_events(instrument="DEMO:XNAS:DEMO_EQ_A", entity=None, category=None, start=None, end=None, correction_state=None, limit=20, offset=0),
        }
        self.assertTrue(all(page.state == "AVAILABLE" and page.items for page in pages.values()), pages)
        self.assertEqual([item.canonical_symbol for item in pages["instruments"].items[:3]], ["DEMO_EQ_A", "DEMO_EQ_B", "DEMO_ETF"])
        references = self.queries.workspace_references()
        self.assertEqual(references.state, "AVAILABLE")
        self.assertEqual(references.strategy_id, self.seed_module.stable_id("strategy"))
        self.assertEqual(references.experiment_id, self.seed_module.stable_id("experiment"))
        self.assertEqual(references.paper_account_id, "demo-paper")


if __name__ == "__main__":
    unittest.main()
