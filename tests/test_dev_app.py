import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from trade_platform.dev_app import create_dev_app
from trade_platform.persistence import PostgresDatabase


class DevAppTests(unittest.TestCase):
    def test_dev_app_liveness_readiness_and_disabled_live_trading(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TRADE_PLATFORM_OPERATOR_TOKEN": "test-dev-token",
                "POSTGRES_DSN": "postgresql://postgres:postgres@127.0.0.1:5439/trade_platform",  # pragma: allowlist secret
            },
        ):
            mock_db = MagicMock(spec=PostgresDatabase)
            app = create_dev_app(database=mock_db)
            client = TestClient(app)

            live_res = client.get("/health/live")
            self.assertEqual(live_res.status_code, 200)
            self.assertEqual(live_res.json(), {"status": "ok"})

            ready_res = client.get("/health/ready")
            self.assertEqual(ready_res.status_code, 200)
            payload = ready_res.json()
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["environment"], "local_research")
            self.assertEqual(payload["paper_trading_enabled"], True)
            self.assertEqual(payload["live_trading_enabled"], False)

    def test_dev_app_unauthenticated_request_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {"TRADE_PLATFORM_OPERATOR_TOKEN": "test-dev-token"},
        ):
            mock_db = MagicMock(spec=PostgresDatabase)
            app = create_dev_app(database=mock_db)
            client = TestClient(app)

            res = client.get("/audit/events")
            self.assertEqual(res.status_code, 401)

            auth_res = client.get(
                "/audit/events",
                headers={"Authorization": "Bearer test-dev-token"},
            )
            self.assertEqual(auth_res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
