"""Explicit real-evidence acceptance harness for `terminal_lite.py verify`.

Not an ordinary unittest-discovery module: this requires the separately prepared
local Lite database and retained private filesystem evidence. Missing inputs fail
rather than skip. Normal CI exercises portable Lite isolation/provenance tests;
no private data, extra database migration or CI skip exception is needed.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_platform.terminal_lite_evidence_v1 import report_days
from trade_platform.terminal_lite_v1 import create_lite_app


class LiteAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("LITE_TEST_READ_DSN") or not os.environ.get(
            "LITE_TEST_RESEARCH_ROOT"
        ):
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
        self.report = json.loads(
            (Path(os.environ["LITE_TEST_RESEARCH_ROOT"]) / "lite-provenance.json").read_text()
        )
        self.days = report_days(self.report)
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
            prefix = f"/operator-dashboard/chart-series/{series['manifest_hash']}/candles?"
            minute = self.get(prefix + "timeframe=1m&limit=2000")
            hourly = self.get(prefix + "timeframe=1h")
            daily = self.get(prefix + "timeframe=1d")
            four_hour = self.get(prefix + "timeframe=4h")
            self.assertEqual(min(2000, 1440 * len(self.days)), len(minute["candles"]))
            self.assertEqual(24 * len(self.days), len(hourly["candles"]))
            self.assertEqual(len(self.days), len(daily["candles"]))
            self.assertEqual(6 * len(self.days), len(four_hour["candles"]))
            self.assertEqual(1440 * len(self.days), hourly["coverage"]["source_bars"])
            self.assertEqual(0, hourly["coverage"]["missing_source_bars"])
            self.assertEqual(0, hourly["partial_candles"])
            self.assertEqual(str(self.days[0]), hourly["coverage"]["first_bar_at"][:10])
            self.assertEqual(str(self.days[-1]), daily["candles"][-1]["open_at"][:10])
            self.assertEqual("NO_TIMING_AUTHORITY", series["tier_ceiling"])
            sample = minute["candles"][-60:]
            candle = hourly["candles"][-1]
            self.assertEqual(sample[0]["open"], candle["open"])
            self.assertEqual(sample[-1]["close"], candle["close"])
            self.assertEqual(max(Decimal(c["high"]) for c in sample), Decimal(candle["high"]))
            self.assertEqual(sum(Decimal(c["volume"]) for c in sample), Decimal(candle["volume"]))
            if len(self.days) > 1:
                earlier = self.get(
                    prefix
                    + urlencode({"timeframe": "1m", "end": minute["earlier_end"], "limit": 2000})
                )
                self.assertLess(earlier["candles"][-1]["open_at"], minute["candles"][0]["open_at"])
                self.assertEqual(0, earlier["partial_candles"])

    def test_research_results_or_explicit_absence_and_unsupported_views(self):
        from collections import Counter

        from scripts.terminal_lite_research import DATASET_HASH, DATASET_ID, STUDY_ID

        studies = self.get("/operator-dashboard/strategy-lab/studies")
        self.assertIn(studies["total"], (0, 1))
        if studies["total"]:
            study = studies["items"][0]
            self.assertEqual(str(STUDY_ID), study["study_id"])
            self.assertEqual("BTCUSDT", study["instrument"])
            self.assertEqual(str(DATASET_ID), study["datasets"][0]["dataset_version_id"])
            self.assertEqual(DATASET_HASH, study["datasets"][0]["content_hash"])
            self.assertEqual((84, 84), (study["planned_trial_count"], study["result_count"]))
            detail = self.get(f"/operator-dashboard/strategy-lab/studies/{STUDY_ID}")
            self.assertFalse(detail["summary"]["authority"]["promotable"])
            self.assertEqual("UNAVAILABLE", detail["economics"]["net_profitability"])
            self.assertEqual([], detail["candidate_sets"])
            trials = self.get(f"/operator-dashboard/strategy-lab/studies/{STUDY_ID}/trials?limit=100")
            self.assertEqual(84, trials["total"])
            self.assertEqual(Counter(EVALUATED=64, INADMISSIBLE_PARAMETERS=20),
                             Counter(item["outcome"] for item in trials["items"]))
            first = next(item for item in trials["items"] if item["outcome"] == "EVALUATED")
            self.assertEqual("20160", first["metrics"]["bars_evaluated"])
            self.assertEqual("SEARCH_NON_AUTHORITATIVE", first["numeric_tier"])
            self.get(f"/operator-dashboard/strategy-lab/studies/{STUDY_ID}/trials/{first['trial_id']}")
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
            self.assertIn(connection.execute("SELECT count(*) FROM strategy_lab_trials").fetchone(),
                          ((0,), (84,)))
            self.assertEqual(
                (False,),
                connection.execute(
                    "SELECT has_table_privilege(current_user,'strategy_lab_trials','INSERT')"
                ).fetchone(),
            )
