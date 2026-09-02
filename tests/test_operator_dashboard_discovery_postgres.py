"""Disposable-PostgreSQL coverage for the Module 2B-3 regime/portfolio discovery lists.

Owns its own database (dropped and recreated in setUpClass) so pagination and
ordering assertions never race against the shared cycle208/demo fixtures used
by tests/test_operator_dashboard_postgres.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
from trade_platform.persistence import PostgresDatabase
from trade_platform.professional_instruments import (
    PostgresProfessionalInstrumentMaster,
    mvp_instrument_universe,
)
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _test_dsn(source_dsn: str, database_name: str) -> str:
    parsed = urlparse(source_dsn)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


def _digest(name: str) -> str:
    return hashlib.sha256(f"module2b3-discovery:{name}".encode()).hexdigest()


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class RegimeAndPortfolioDiscoveryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest("Module 2B-3 discovery coverage requires a local or CI disposable PostgreSQL DSN")
        cls.database_name = f"module2b3_discovery_{os.getpid()}"
        cls.dsn = _test_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')

        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
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

    @classmethod
    def tearDownClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    def test_01_empty_database_returns_explicit_unavailable_discovery(self) -> None:
        regime_page = self.queries.regime_runs(
            instrument=None, status=None, model_version_id=None, dataset_version=None, limit=20, offset=0,
        )
        portfolio_page = self.queries.portfolio_construction_runs(
            status=None, policy_version_id=None, regime_run_id=None, limit=20, offset=0,
        )
        self.assertEqual((regime_page.state, regime_page.items, regime_page.page.returned, regime_page.page.has_more), ("UNAVAILABLE", [], 0, False))
        self.assertEqual((portfolio_page.state, portfolio_page.items, portfolio_page.page.returned, portfolio_page.page.has_more), ("UNAVAILABLE", [], 0, False))

    def test_02_multiple_regime_and_portfolio_runs_deterministic_order_pagination_filters(self) -> None:
        database = self.database
        now = datetime(2024, 1, 1, tzinfo=UTC)
        instrument_a = replace(
            mvp_instrument_universe(datetime(2023, 1, 1, tzinfo=UTC))[0],
            instrument_id="US:XNYS:M2B3A", canonical_symbol="M2B3A",
        )
        instrument_b = replace(
            mvp_instrument_universe(datetime(2023, 1, 1, tzinfo=UTC))[1],
            instrument_id="US:XNYS:M2B3B", canonical_symbol="M2B3B",
        )
        PostgresProfessionalInstrumentMaster(database).register(instrument_a)
        PostgresProfessionalInstrumentMaster(database).register(instrument_b)

        ids = {name: uuid4() for name in (
            "source", "dataset_version", "regime_model", "regime_model_version",
            "regime_run_1", "regime_run_2", "regime_run_3",
            "obs_trend", "obs_vol",
            "policy_a", "policy_version_a", "policy_b", "policy_version_b",
            "portfolio_run_1", "portfolio_run_2", "portfolio_run_3",
            "target_1", "target_2", "target_3",
            "non_allowlisted_source", "non_allowlisted_dataset_version", "covariance_1",
        )}
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO historical_data_sources VALUES (%s,%s,'discovery-inputs','FIXTURE','test-only',%s,%s,'US_EQUITIES_ETFS',%s)",
                (ids["source"], "module2b3-discovery-source", "fixture://module2b3/discovery", now - timedelta(days=5), now - timedelta(days=5)),
            )
            cursor.execute(
                "INSERT INTO historical_dataset_versions VALUES (%s,%s,'module2b3-v1','fixture-normalization-v1',%s,%s,NULL,%s,'SEALED')",
                (ids["dataset_version"], ids["source"], _digest("historical-dataset"), now - timedelta(days=5), now - timedelta(days=4)),
            )
            cursor.execute(
                "INSERT INTO regime_model_versions VALUES (%s,%s,'regime-discovery-v1','rule-v1','RESEARCH_ONLY',%s,'[]'::jsonb,'{}'::jsonb,%s,%s)",
                (ids["regime_model_version"], ids["regime_model"], now - timedelta(days=5), now - timedelta(days=4), _digest("regime-model")),
            )
            # Three runs across two instruments, two statuses and three distinct
            # dataset_version labels, staggered in time to exercise DESC ordering.
            regime_runs = (
                (ids["regime_run_1"], "dataset-v1", instrument_a.instrument_id, now, "REVIEW_REQUIRED"),
                (ids["regime_run_2"], "dataset-v2", instrument_a.instrument_id, now + timedelta(hours=1), "BLOCKED"),
                (ids["regime_run_3"], "dataset-v3", instrument_b.instrument_id, now + timedelta(hours=2), "REVIEW_REQUIRED"),
            )
            for run_id, dataset_version, instrument_id, evaluated_at, status in regime_runs:
                cursor.execute(
                    "INSERT INTO regime_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'[\"Synthetic fixture\"]'::jsonb,'{}'::jsonb,%s)",
                    (run_id, ids["regime_model_version"], ids["dataset_version"], dataset_version,
                     _digest("historical-dataset"), instrument_id, evaluated_at, status, _digest(f"regime-run:{run_id}")),
                )
            # Only regime_run_3 carries observed dimensions: one measured (with
            # uncertainty), one explicitly UNAVAILABLE -- proving UNAVAILABLE never
            # collapses to zero and a run with no observations summarizes as UNAVAILABLE.
            cursor.execute(
                "INSERT INTO regime_observations VALUES (%s,%s,'RULE_BASED','TREND','MEASURED','BULL_TREND',"
                "'{\"BULL_TREND\":0.8,\"BEAR_TREND\":0.2}'::jsonb,0.2,'[]'::jsonb,%s)",
                (ids["obs_trend"], now + timedelta(hours=2), _digest("observation-trend")),
            )
            cursor.execute(
                "INSERT INTO regime_observations VALUES (%s,%s,'RULE_BASED','VOLATILITY','UNAVAILABLE',NULL,'{}'::jsonb,NULL,'[]'::jsonb,%s)",
                (ids["obs_vol"], now + timedelta(hours=2), _digest("observation-volatility")),
            )
            cursor.execute("INSERT INTO regime_run_observations VALUES (%s,%s,0)", (ids["regime_run_3"], ids["obs_trend"]))
            cursor.execute("INSERT INTO regime_run_observations VALUES (%s,%s,1)", (ids["regime_run_3"], ids["obs_vol"]))

            policy = json.dumps({"target_volatility": "0.12"})
            cursor.execute(
                "INSERT INTO portfolio_construction_policy_versions VALUES (%s,%s,'policy-a-v1','construction-v1','RESEARCH_ONLY',%s::jsonb,FALSE,%s,%s)",
                (ids["policy_version_a"], ids["policy_a"], policy, now - timedelta(days=3), _digest("policy-a")),
            )
            cursor.execute(
                "INSERT INTO portfolio_construction_policy_versions VALUES (%s,%s,'policy-b-v1','construction-v1','RESEARCH_ONLY',%s::jsonb,FALSE,%s,%s)",
                (ids["policy_version_b"], ids["policy_b"], policy, now - timedelta(days=3), _digest("policy-b")),
            )
            portfolio_runs = (
                (ids["portfolio_run_1"], ids["policy_version_a"], ids["regime_run_1"], now, "REVIEW_REQUIRED"),
                (ids["portfolio_run_2"], ids["policy_version_a"], ids["regime_run_2"], now + timedelta(hours=1), "BLOCKED"),
                (ids["portfolio_run_3"], ids["policy_version_b"], ids["regime_run_3"], now + timedelta(hours=2), "REVIEW_REQUIRED"),
            )
            for run_id, policy_version_id, regime_run_id, constructed_at, status in portfolio_runs:
                cursor.execute(
                    "INSERT INTO portfolio_construction_runs VALUES (%s,%s,%s,%s,%s,%s,100000,'[\"Synthetic fixture\"]'::jsonb,'{}'::jsonb,%s)",
                    (run_id, policy_version_id, regime_run_id, _digest(f"regime-run:{regime_run_id}"), constructed_at, status, _digest(f"portfolio-run:{run_id}")),
                )
            targets = (
                (ids["target_1"], ids["portfolio_run_1"]),
                (ids["target_2"], ids["portfolio_run_2"]),
                (ids["target_3"], ids["portfolio_run_3"]),
            )
            for target_id, run_id in targets:
                cursor.execute(
                    "INSERT INTO portfolio_target_candidates VALUES (%s,%s,'REVIEW_REQUIRED',FALSE,0.4,0.6,0.6,0.09,0.11,%s,%s)",
                    (target_id, run_id, now, _digest(f"target:{run_id}")),
                )
            # A source shaped like the Module 1B demo seed: NOT named 'FIXTURE' in either
            # provider or provider_identifier_namespace, with a populated authorization
            # reference -- exactly the shape that previously fooled the naive
            # (namespace != 'FIXTURE' and authorization_reference truthy) heuristic into
            # rendering PROVIDER_BACKED_COVARIANCE for synthetic evidence.
            cursor.execute(
                "INSERT INTO historical_data_sources VALUES (%s,%s,'demo-ohlcv','DEMO:INSTRUMENT',%s,%s,%s,'US_EQUITIES_ETFS',%s)",
                (ids["non_allowlisted_source"], "SYNTHETIC_DEMO_ENGINEERING_EVIDENCE", "module2b3-discovery-v1",
                 "demo://module2b3-discovery/terms", now - timedelta(days=3), now - timedelta(days=3)),
            )
            cursor.execute(
                "INSERT INTO historical_dataset_versions VALUES (%s,%s,'module2b3-discovery-v1','fixture-normalization-v1',%s,%s,NULL,%s,'SEALED')",
                (ids["non_allowlisted_dataset_version"], ids["non_allowlisted_source"], _digest("non-allowlisted-dataset"), now - timedelta(days=3), now - timedelta(days=2)),
            )
            cursor.execute(
                "INSERT INTO portfolio_covariance_estimates VALUES (%s,%s,%s,'module2b3-discovery-v1',%s,'demo-covariance-v1',3,%s,'[]'::jsonb,'[]'::jsonb,0.1,0.2,%s)",
                (ids["covariance_1"], ids["portfolio_run_1"], ids["non_allowlisted_dataset_version"],
                 _digest("non-allowlisted-dataset-content"), now, _digest("covariance-1")),
            )
            cursor.execute("INSERT INTO portfolio_risk_gate_evidence VALUES (%s,TRUE,60000,60000,5000,'[]'::jsonb,'{}'::jsonb,FALSE,%s)", (ids["portfolio_run_1"], _digest("risk-gate:1")))
            cursor.execute("INSERT INTO portfolio_risk_gate_evidence VALUES (%s,FALSE,60000,60000,5000,'[\"stress_loss_exceeds_limit\"]'::jsonb,'{}'::jsonb,FALSE,%s)", (ids["portfolio_run_2"], _digest("risk-gate:2")))
            cursor.execute("INSERT INTO portfolio_risk_gate_evidence VALUES (%s,TRUE,60000,60000,5000,'[]'::jsonb,'{}'::jsonb,FALSE,%s)", (ids["portfolio_run_3"], _digest("risk-gate:3")))

        queries = self.queries

        # --- Regime discovery: deterministic ordering (evaluated_at DESC) ---
        all_regime = queries.regime_runs(instrument=None, status=None, model_version_id=None, dataset_version=None, limit=20, offset=0)
        self.assertEqual(
            [item.run_id for item in all_regime.items],
            [ids["regime_run_3"], ids["regime_run_2"], ids["regime_run_1"]],
        )
        self.assertEqual((all_regime.state, all_regime.page.returned, all_regime.page.has_more), ("AVAILABLE", 3, False))

        # --- Regime discovery: bounded pagination ---
        page_1 = queries.regime_runs(instrument=None, status=None, model_version_id=None, dataset_version=None, limit=2, offset=0)
        page_2 = queries.regime_runs(instrument=None, status=None, model_version_id=None, dataset_version=None, limit=2, offset=2)
        self.assertEqual(([item.run_id for item in page_1.items], page_1.page.has_more), ([ids["regime_run_3"], ids["regime_run_2"]], True))
        self.assertEqual(([item.run_id for item in page_2.items], page_2.page.has_more), ([ids["regime_run_1"]], False))

        # --- Regime discovery: safe filters ---
        by_instrument = queries.regime_runs(instrument=instrument_b.instrument_id, status=None, model_version_id=None, dataset_version=None, limit=20, offset=0)
        self.assertEqual([item.run_id for item in by_instrument.items], [ids["regime_run_3"]])
        by_status = queries.regime_runs(instrument=None, status="BLOCKED", model_version_id=None, dataset_version=None, limit=20, offset=0)
        self.assertEqual([item.run_id for item in by_status.items], [ids["regime_run_2"]])
        by_dataset_version = queries.regime_runs(instrument=None, status=None, model_version_id=None, dataset_version="dataset-v1", limit=20, offset=0)
        self.assertEqual([item.run_id for item in by_dataset_version.items], [ids["regime_run_1"]])
        by_model_version = queries.regime_runs(instrument=None, status=None, model_version_id=ids["regime_model_version"], dataset_version=None, limit=20, offset=0)
        self.assertEqual(len(by_model_version.items), 3)
        no_match = queries.regime_runs(instrument=None, status=None, model_version_id=uuid4(), dataset_version=None, limit=20, offset=0)
        self.assertEqual((no_match.state, no_match.items), ("UNAVAILABLE", []))

        # --- Regime discovery: dimension/uncertainty summaries never fabricate zero ---
        run_1_summary = next(item for item in all_regime.items if item.run_id == ids["regime_run_1"])
        run_3_summary = next(item for item in all_regime.items if item.run_id == ids["regime_run_3"])
        self.assertEqual((run_1_summary.dimension_summary, run_1_summary.uncertainty_summary), ([], "UNAVAILABLE"))
        trend_dim = next(dim for dim in run_3_summary.dimension_summary if dim.dimension == "TREND")
        vol_dim = next(dim for dim in run_3_summary.dimension_summary if dim.dimension == "VOLATILITY")
        self.assertEqual((trend_dim.hard_label, trend_dim.top_probability_state, trend_dim.uncertainty), ("BULL_TREND", "BULL_TREND", "0.2"))
        self.assertEqual((vol_dim.hard_label, vol_dim.uncertainty), (None, None))
        self.assertIn("VOLATILITY=UNAVAILABLE", run_3_summary.uncertainty_summary)
        self.assertIn("TREND=0.2", run_3_summary.uncertainty_summary)

        # --- Portfolio discovery: deterministic ordering (constructed_at DESC) ---
        all_portfolio = queries.portfolio_construction_runs(status=None, policy_version_id=None, regime_run_id=None, limit=20, offset=0)
        self.assertEqual(
            [item.run_id for item in all_portfolio.items],
            [ids["portfolio_run_3"], ids["portfolio_run_2"], ids["portfolio_run_1"]],
        )
        self.assertEqual((all_portfolio.state, all_portfolio.page.returned, all_portfolio.page.has_more), ("AVAILABLE", 3, False))
        for item in all_portfolio.items:
            self.assertEqual((item.review_only, item.automatic_authority), (True, False))

        # --- Portfolio discovery: bounded pagination ---
        portfolio_page_1 = queries.portfolio_construction_runs(status=None, policy_version_id=None, regime_run_id=None, limit=2, offset=0)
        portfolio_page_2 = queries.portfolio_construction_runs(status=None, policy_version_id=None, regime_run_id=None, limit=2, offset=2)
        self.assertEqual(([item.run_id for item in portfolio_page_1.items], portfolio_page_1.page.has_more), ([ids["portfolio_run_3"], ids["portfolio_run_2"]], True))
        self.assertEqual(([item.run_id for item in portfolio_page_2.items], portfolio_page_2.page.has_more), ([ids["portfolio_run_1"]], False))

        # --- Portfolio discovery: safe filters ---
        by_policy = queries.portfolio_construction_runs(status=None, policy_version_id=ids["policy_version_b"], regime_run_id=None, limit=20, offset=0)
        self.assertEqual([item.run_id for item in by_policy.items], [ids["portfolio_run_3"]])
        by_regime_run = queries.portfolio_construction_runs(status=None, policy_version_id=None, regime_run_id=ids["regime_run_2"], limit=20, offset=0)
        self.assertEqual([item.run_id for item in by_regime_run.items], [ids["portfolio_run_2"]])
        by_status = queries.portfolio_construction_runs(status="BLOCKED", policy_version_id=None, regime_run_id=None, limit=20, offset=0)
        self.assertEqual([item.run_id for item in by_status.items], [ids["portfolio_run_2"]])
        blocked_gate = next(item for item in all_portfolio.items if item.run_id == ids["portfolio_run_2"])
        approved_gate = next(item for item in all_portfolio.items if item.run_id == ids["portfolio_run_1"])
        self.assertEqual((blocked_gate.risk_gate_approved, approved_gate.risk_gate_approved), (False, True))
        no_portfolio_match = queries.portfolio_construction_runs(status=None, policy_version_id=uuid4(), regime_run_id=None, limit=20, offset=0)
        self.assertEqual((no_portfolio_match.state, no_portfolio_match.items), ("UNAVAILABLE", []))

        # --- Covariance provider-backed status must be fail-closed (Module 2B-3 fix) ---
        # A source that is NOT literally named 'FIXTURE' and carries a populated
        # authorization_reference (exactly the Module 1B demo seed's shape) must still
        # classify as NOT provider-backed: no real provider is on the authorized allowlist.
        detail = queries.portfolio_construction(ids["portfolio_run_1"])
        self.assertFalse(detail.covariance.provider_backed)
        self.assertEqual(detail.covariance.source_provider, "SYNTHETIC_DEMO_ENGINEERING_EVIDENCE")
        self.assertIn("NO_REAL_PROVIDER_BACKED_COVARIANCE_EVIDENCE", detail.covariance.classification)

    def test_03_discovery_api_layer_validates_and_never_leaks_secrets(self) -> None:
        app = build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("module2b3-discovery-token"),
            InMemoryRateLimiter(max_requests=100), operator_dashboard_queries=self.queries,
        )
        client = TestClient(app)
        headers = {"Authorization": "Bearer module2b3-discovery-token"}
        for path in ("/operator-dashboard/regime-runs?limit=20&offset=0", "/operator-dashboard/portfolio-construction-runs?limit=20&offset=0"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)
                response = client.get(path, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                serialized = response.text.casefold()
                self.assertNotIn(self.dsn.casefold(), serialized)
                self.assertNotIn("password", serialized)
                self.assertNotIn("module2b3-discovery-token", serialized)
                self.assertEqual(client.post(path.split("?")[0], headers=headers).status_code, 405)
        self.assertEqual(client.get("/operator-dashboard/regime-runs?limit=101", headers=headers).status_code, 422)
        self.assertEqual(client.get("/operator-dashboard/regime-runs?status=NOT_A_STATUS", headers=headers).status_code, 422)
        self.assertEqual(client.get("/operator-dashboard/regime-runs?model_version_id=not-a-uuid", headers=headers).status_code, 422)
        self.assertEqual(client.get("/operator-dashboard/portfolio-construction-runs?offset=-1", headers=headers).status_code, 422)
        self.assertEqual(client.get("/operator-dashboard/portfolio-construction-runs?status=NOT_A_STATUS", headers=headers).status_code, 422)
        self.assertEqual(client.get("/operator-dashboard/portfolio-construction-runs?regime_run_id=not-a-uuid", headers=headers).status_code, 422)


if __name__ == "__main__":
    unittest.main()
