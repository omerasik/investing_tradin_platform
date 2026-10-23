"""Exact symbol discovery through the protected API over isolated, session-local stored rows."""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
from trade_platform.persistence import PostgresDatabase
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator
from trade_platform.strategy_lab_study_v1 import identity_hash_v1


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class TerminalSignalDiscoveryPostgresTests(unittest.TestCase):
    def test_older_symbol_survives_global_fifty_and_integrity_remains_fail_closed(self) -> None:
        from psycopg.types.json import Jsonb

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.addCleanup(database.close)
        # Shadows only this connection's relation; no authority, holdout, worker or persistent fixture is changed.
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE live_strategy_signals (signal_id uuid, symbol text, authority text, "
                           "target_from integer, target_to integer, bar_open_at timestamptz, decided_at timestamptz, "
                           "study_id uuid, trial_id uuid, identity jsonb, content_hash text)")
            oldest = datetime(2026, 6, 1, tzinfo=UTC)
            rows = []
            # Each market is older than 50 other-market rows in one of the three rounds below.
            for index in range(153):
                symbol = ("BTCUSDT", "ETHUSDT", "SOLUSDT")[index // 51]
                study, trial = uuid4(), uuid4()
                at = oldest + timedelta(minutes=index // 2)  # ties also exercise the UUID ordering
                micros = int(at.timestamp()) * 1_000_000
                identity = {"candidate": {"study_id": str(study), "trial_id": str(trial)}, "symbol": symbol,
                            "authority": "RESEARCH_WATCH", "target_from": 0, "target_to": 1,
                            "evidence": {"bar_open_micros": micros, "complete_market_knowledge_micros": micros}}
                content_hash = identity_hash_v1(identity)
                signal = uuid5(uuid5(NAMESPACE_URL, "trade_platform.live_signals_v1"), f"live-signal:{content_hash}")
                rows.append((signal, symbol, "RESEARCH_WATCH", 0, 1, at, at, study, trial, Jsonb(identity), content_hash))
            cursor.executemany("INSERT INTO live_strategy_signals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        client = TestClient(build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("fixture-signal-view"),
            InMemoryRateLimiter(max_requests=100), operator_dashboard_queries=PostgresOperatorDashboardQueries(database)))
        headers = {"Authorization": "Bearer fixture-signal-view"}
        url = "/operator-dashboard/research-terminal/signals"
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            with self.subTest(symbol=symbol):
                # Move this market behind every other market without changing any identity-bound field.
                with database.transaction() as connection, connection.cursor() as cursor:
                    cursor.execute("UPDATE live_strategy_signals SET decided_at = bar_open_at + "
                                   "CASE WHEN symbol = %s THEN interval '0 days' ELSE interval '1 day' END", (symbol,))
                self.assertEqual(client.get(url, params={"symbol": symbol}).status_code, 401)
                global_rows = client.get(url, params={"limit": 50}, headers=headers).json()
                self.assertEqual(len(global_rows), 50)
                self.assertNotIn(symbol, {row["symbol"] for row in global_rows})
                response = client.get(url, params={"limit": 50, "symbol": symbol}, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                expected = sorted((row for row in rows if row[1] == symbol), key=lambda row: (-row[6].timestamp(), row[0]))[:50]
                self.assertEqual([row["signal_id"] for row in result], [str(row[0]) for row in expected])
                self.assertEqual({row["symbol"] for row in result}, {symbol})
                self.assertEqual(result[0]["study_id"], str(expected[0][7]))
                self.assertEqual(result[0]["trial_id"], str(expected[0][8]))
                self.assertEqual(result, client.get(url, params={"limit": 50, "symbol": symbol}, headers=headers).json())
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM live_strategy_signals WHERE symbol = 'ETHUSDT'")
        self.assertEqual(client.get(url, params={"symbol": "ETHUSDT"}, headers=headers).json(), [])
        for column, value in (("content_hash", "0" * 64), ("trial_id", uuid4()), ("signal_id", uuid4())):
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(f"UPDATE live_strategy_signals SET {column} = %s WHERE symbol = 'BTCUSDT'", (value,))
            response = client.get(url, params={"symbol": "BTCUSDT"}, headers=headers)
            self.assertEqual(response.status_code, 503, response.text)
            # Restore before testing a different corruption independently.
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("DELETE FROM live_strategy_signals WHERE symbol = 'BTCUSDT'")
                cursor.executemany("INSERT INTO live_strategy_signals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows[:51])
