"""Disposable-PostgreSQL coverage for the Module 2B-5 professional Paper OMS workspace.

Owns its own database (dropped and recreated in setUpClass) so filter and
truthfulness assertions never race against the shared cycle208/demo fixtures
used by tests/test_operator_dashboard_postgres.py (see docs/MODULE_2B5_PAPER_OPERATIONS_AUDIT.md
and the project memory note on shared-DB test isolation).
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import psycopg

from trade_platform.operator_dashboard import (
    DashboardObjectNotFound,
    PostgresOperatorDashboardQueries,
)
from trade_platform.persistence import PostgresDatabase

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _test_dsn(source_dsn: str, database_name: str) -> str:
    parsed = urlparse(source_dsn)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PaperOrderDiscoveryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest("Module 2B-5 paper discovery coverage requires a local or CI disposable PostgreSQL DSN")
        cls.database_name = f"module2b5_paper_{os.getpid()}"
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
        page = self.queries.paper_orders(
            account_id=None, instrument=None, side=None, lifecycle_status=None,
            fill_state=None, reconciliation_state=None, limit=20, offset=0,
        )
        self.assertEqual((page.state, page.items, page.page.returned, page.page.has_more), ("UNAVAILABLE", [], 0, False))

    def test_02_filters_quantity_and_reconciliation_truthfulness(self) -> None:
        database = self.database
        now = datetime(2026, 1, 1, tzinfo=UTC)
        ids = {name: uuid4() for name in (
            "instrument_a", "instrument_b", "strategy", "strategy_version",
            "signal_a", "signal_b", "intent_a", "intent_b", "oms_event", "fill", "reconciliation_a",
        )}

        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO instruments VALUES (%s,NULL,'M2B5A','EQUITY','USD',0.01,1,%s,NULL,%s)",
                (ids["instrument_a"], now - timedelta(days=30), now - timedelta(days=30)),
            )
            cursor.execute(
                "INSERT INTO instruments VALUES (%s,NULL,'M2B5B','EQUITY','USD',0.01,1,%s,NULL,%s)",
                (ids["instrument_b"], now - timedelta(days=30), now - timedelta(days=30)),
            )
            cursor.execute("INSERT INTO accounts VALUES (%s,'PAPER','USD',%s)", ("m2b5-acct-a", now - timedelta(days=1)))
            cursor.execute("INSERT INTO accounts VALUES (%s,'PAPER','USD',%s)", ("m2b5-acct-b", now - timedelta(days=1)))
            cursor.execute(
                "INSERT INTO strategy_definitions VALUES (%s,'TREND','module2b5 fixture',%s)",
                (ids["strategy"], now - timedelta(days=2)),
            )
            cursor.execute(
                "INSERT INTO strategy_versions VALUES (%s,%s,'trend-m2b5-v1','[]'::jsonb,'cost-v1','capacity-v1','{}'::jsonb,%s)",
                (ids["strategy_version"], ids["strategy"], now - timedelta(days=2)),
            )
            cursor.execute(
                "INSERT INTO signals VALUES (%s,%s,%s,'RESEARCH_ONLY',%s,%s,'{}'::jsonb)",
                (ids["signal_a"], ids["strategy_version"], ids["instrument_a"], now - timedelta(hours=2), now + timedelta(days=1)),
            )
            cursor.execute(
                "INSERT INTO signals VALUES (%s,%s,%s,'RESEARCH_ONLY',%s,%s,'{}'::jsonb)",
                (ids["signal_b"], ids["strategy_version"], ids["instrument_b"], now - timedelta(hours=2), now + timedelta(days=1)),
            )
            # intent_a: fully filled BUY on instrument_a/acct-a, created first (older).
            cursor.execute(
                "INSERT INTO paper_order_intents VALUES (%s,%s,'m2b5-acct-a',%s,'BUY',10,100,'PROPOSED',%s)",
                (ids["intent_a"], ids["signal_a"], ids["instrument_a"], now),
            )
            # intent_b: unfilled SELL on instrument_b/acct-b, created later (newer) --
            # no reconciliation row exists for acct-b at all.
            cursor.execute(
                "INSERT INTO paper_order_intents VALUES (%s,%s,'m2b5-acct-b',%s,'SELL',5,50,'PROPOSED',%s)",
                (ids["intent_b"], ids["signal_b"], ids["instrument_b"], now + timedelta(hours=1)),
            )
            cursor.execute(
                "INSERT INTO oms_events VALUES (%s,%s,'ORDER_STATUS_CHANGED',%s,%s::jsonb)",
                (ids["oms_event"], ids["intent_a"], now + timedelta(minutes=5), json.dumps({"from": "PROPOSED", "to": "FILLED"})),
            )
            cursor.execute(
                "INSERT INTO fills VALUES (%s,'m2b5-fill-1',%s,%s,10,100)",
                (ids["fill"], ids["intent_a"], now + timedelta(minutes=5)),
            )
            # Only acct-a has a persisted reconciliation row; acct-b's absence must
            # resolve to UNAVAILABLE, never HEALTHY -- the truthfulness invariant.
            cursor.execute(
                "INSERT INTO reconciliations VALUES (%s,'m2b5-acct-a','paper-broker-sim',%s,TRUE,'[]'::jsonb)",
                (ids["reconciliation_a"], now + timedelta(minutes=6)),
            )

        queries = self.queries

        all_orders = queries.paper_orders(
            account_id=None, instrument=None, side=None, lifecycle_status=None,
            fill_state=None, reconciliation_state=None, limit=20, offset=0,
        )
        self.assertEqual(all_orders.state, "AVAILABLE")
        self.assertEqual([item.intent_id for item in all_orders.items], [ids["intent_b"], ids["intent_a"]])

        by_intent = {item.intent_id: item for item in all_orders.items}
        self.assertEqual(by_intent[ids["intent_a"]].quantity, "10")
        self.assertEqual(by_intent[ids["intent_a"]].fill_state, "PARTIAL_OR_FINAL_FILL")
        self.assertEqual(by_intent[ids["intent_a"]].lifecycle_status, "FILLED")
        self.assertEqual(by_intent[ids["intent_a"]].reconciliation_state, "HEALTHY")

        self.assertEqual(by_intent[ids["intent_b"]].quantity, "5")
        self.assertEqual(by_intent[ids["intent_b"]].fill_state, "UNFILLED")
        self.assertEqual(by_intent[ids["intent_b"]].lifecycle_status, "PROPOSED")
        self.assertEqual(by_intent[ids["intent_b"]].reconciliation_state, "UNAVAILABLE")

        account_filtered = queries.paper_orders(
            account_id="m2b5-acct-a", instrument=None, side=None, lifecycle_status=None,
            fill_state=None, reconciliation_state=None, limit=20, offset=0,
        )
        self.assertEqual([item.intent_id for item in account_filtered.items], [ids["intent_a"]])

        instrument_filtered = queries.paper_orders(
            account_id=None, instrument="M2B5B", side=None, lifecycle_status=None,
            fill_state=None, reconciliation_state=None, limit=20, offset=0,
        )
        self.assertEqual([item.intent_id for item in instrument_filtered.items], [ids["intent_b"]])

        side_filtered = queries.paper_orders(
            account_id=None, instrument=None, side="SELL", lifecycle_status=None,
            fill_state=None, reconciliation_state=None, limit=20, offset=0,
        )
        self.assertEqual([item.intent_id for item in side_filtered.items], [ids["intent_b"]])

        reconciliation_unavailable = queries.paper_orders(
            account_id=None, instrument=None, side=None, lifecycle_status=None,
            fill_state=None, reconciliation_state="UNAVAILABLE", limit=20, offset=0,
        )
        self.assertEqual([item.intent_id for item in reconciliation_unavailable.items], [ids["intent_b"]])
        self.assertTrue(all(item.reconciliation_state != "HEALTHY" for item in reconciliation_unavailable.items))

        unfilled = queries.paper_orders(
            account_id=None, instrument=None, side=None, lifecycle_status=None,
            fill_state="UNFILLED", reconciliation_state=None, limit=20, offset=0,
        )
        self.assertEqual([item.intent_id for item in unfilled.items], [ids["intent_b"]])

        self.__class__.ids = ids

    def test_03_paper_order_and_reconciliation_detail_evidence(self) -> None:
        ids = self.__class__.ids
        queries = self.queries

        filled_order = queries.paper_order(ids["intent_a"])
        self.assertEqual(filled_order.paper_only, True)
        self.assertEqual(filled_order.account_id, "m2b5-acct-a")
        self.assertEqual(filled_order.status, "FILLED")
        self.assertEqual(filled_order.filled_quantity, "10")
        self.assertEqual(filled_order.average_fill_price, "100")
        self.assertEqual(len(filled_order.fills), 1)
        self.assertEqual(filled_order.fills[0].external_fill_id, "m2b5-fill-1")
        self.assertEqual(len(filled_order.events), 1)
        self.assertEqual(filled_order.events[0].event_type, "ORDER_STATUS_CHANGED")

        unfilled_order = queries.paper_order(ids["intent_b"])
        self.assertEqual((unfilled_order.status, unfilled_order.filled_quantity, unfilled_order.average_fill_price, unfilled_order.fills), ("PROPOSED", "0", None, []))

        with self.assertRaises(DashboardObjectNotFound):
            queries.paper_order(uuid4())

        reconciled = queries.paper_reconciliation("m2b5-acct-a")
        self.assertEqual((reconciled.account_id, reconciled.source, reconciled.complete, reconciled.discrepancies), ("m2b5-acct-a", "paper-broker-sim", True, []))
        self.assertIsNone(reconciled.reconciled_account)

        # acct-b has no reconciliation row at all -- must raise (surfaced as EMPTY),
        # never fabricate a HEALTHY/complete reconciliation.
        with self.assertRaises(DashboardObjectNotFound):
            queries.paper_reconciliation("m2b5-acct-b")


if __name__ == "__main__":
    unittest.main()
