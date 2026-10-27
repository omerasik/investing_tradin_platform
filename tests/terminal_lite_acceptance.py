"""Explicit real-evidence acceptance harness for `terminal_lite.py verify`.

Not an ordinary unittest-discovery module: this requires the separately prepared
local Lite database and retained private filesystem evidence. Missing inputs fail
rather than skip. Normal CI exercises portable Lite isolation/provenance tests;
no private data, extra database migration or CI skip exception is needed.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_platform.terminal_lite_v1 import create_lite_app


class LiteAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("LITE_TEST_READ_DSN") or not os.environ.get("LITE_TEST_RESEARCH_ROOT"):
            raise RuntimeError("explicit_disposable_lite_database_and_subset_required")

    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "LITE_READ_DSN": os.environ["LITE_TEST_READ_DSN"],
                "LITE_RESEARCH_ROOT": os.environ["LITE_TEST_RESEARCH_ROOT"],
                "TRADE_PLATFORM_OPERATOR_TOKEN": "fixture-lite-test-token",
                "TRADE_PLATFORM_LITE": "1",
            },
        )
        self.env.start()
        self.client = TestClient(create_lite_app())
        self.client.__enter__()
        self.headers = {"Authorization": "Bearer fixture-lite-test-token"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()

    def get(self, path):
        result = self.client.get(path, headers=self.headers)
        self.assertEqual(200, result.status_code, result.text)
        return result.json()

    def test_auth_and_all_mutation_methods_fail_closed(self):
        ready = self.client.get("/health/lite-ready")
        self.assertEqual(200, ready.status_code)
        self.assertFalse(ready.json()["paper_execution_enabled"])
        self.assertFalse(ready.json()["research_commands_enabled"])
        path = "/operator-dashboard/research-terminal/overview"
        self.assertEqual(401, self.client.get(path).status_code)
        self.assertEqual(
            401, self.client.get(path, headers={"Authorization": "Bearer incorrect"}).status_code
        )
        self.get(path)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            result = self.client.request(
                method,
                "/operator-dashboard/research-terminal/owner-commands",
                headers=self.headers,
                json={},
            )
            self.assertEqual(403, result.status_code)
            self.assertIn("LITE_READ_ONLY", result.text)

    def test_charts_are_real_subset_with_exact_decimal_aggregation(self):
        from decimal import Decimal

        catalog = self.get("/operator-dashboard/chart-series")
        self.assertEqual(
            ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            sorted(item["instrument"] for item in catalog["items"]),
        )
        for series in catalog["items"]:
            minute = self.get(
                f"/operator-dashboard/chart-series/{series['manifest_hash']}/candles?timeframe=1m"
            )
            hourly = self.get(
                f"/operator-dashboard/chart-series/{series['manifest_hash']}/candles?timeframe=1h"
            )
            self.assertEqual(1440, len(minute["candles"]))
            self.assertEqual(24, len(hourly["candles"]))
            self.assertEqual(0, hourly["coverage"]["missing_source_bars"])
            self.assertEqual("NO_TIMING_AUTHORITY", series["tier_ceiling"])
            first = minute["candles"][:60]
            candle = hourly["candles"][0]
            self.assertEqual(first[0]["open"], candle["open"])
            self.assertEqual(first[-1]["close"], candle["close"])
            self.assertEqual(max(Decimal(c["high"]) for c in first), Decimal(candle["high"]))
            self.assertEqual(sum(Decimal(c["volume"]) for c in first), Decimal(candle["volume"]))

    def test_empty_research_views_and_missing_comparison_remain_explicit(self):
        studies = self.get("/operator-dashboard/strategy-lab/studies")
        self.assertEqual(0, studies["total"])
        for name in ("reruns", "walk-forward", "candidates", "accounts", "signals", "commands"):
            self.assertEqual([], self.get(f"/operator-dashboard/research-terminal/{name}"))
        self.get("/operator-dashboard/evidence-catalog")
        self.get("/operator-dashboard/research-terminal/activation")
        response = self.client.get(
            f"/operator-dashboard/research-terminal/compare?study_id={uuid4()}",
            headers=self.headers,
        )
        self.assertEqual(404, response.status_code)
        self.assertEqual(
            404,
            self.client.get(
                f"/operator-dashboard/strategy-lab/studies/{uuid4()}", headers=self.headers
            ).status_code,
        )

    def test_database_identity_canonical_schema_and_no_execution_rows(self):
        import psycopg

        with psycopg.connect(os.environ["LITE_TEST_READ_DSN"]) as connection:
            self.assertEqual(
                ("trade_terminal_lite", "lite_reader"),
                connection.execute("SELECT current_database(),current_user").fetchone(),
            )
            self.assertEqual(
                ("20261020_0067",),
                connection.execute("SELECT version_num FROM alembic_version").fetchone(),
            )
            self.assertEqual(
                (0,), connection.execute("SELECT count(*) FROM strategy_lab_trials").fetchone()
            )
            self.assertEqual(
                (False,),
                connection.execute(
                    "SELECT has_table_privilege(current_user,'strategy_lab_trials','INSERT')"
                ).fetchone(),
            )
