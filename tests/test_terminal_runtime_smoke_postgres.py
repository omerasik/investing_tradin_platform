"""Actual protected composition and read smoke, without fixture evidence writes."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class TerminalRuntimeSmokePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config("alembic.ini"), "head")

    def test_protected_analytical_startup_and_authenticated_read(self):
        root = Path(__file__).resolve().parents[1]
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("TRADE_PLATFORM_")}
        environment.update({
            "PYTHONPATH": str(root / "src"), "TRADE_PLATFORM_ENVIRONMENT": "paper",
            "POSTGRES_DSN": os.environ["POSTGRES_TEST_DSN"],
            "TRADE_PLATFORM_OPERATOR_TOKEN": "fixture-smoke-token",
        })
        process = subprocess.run(
            [sys.executable, str(root / "scripts" / "smoke_terminal_runtime.py"),
             "--profile", "analytical"],
            cwd=root, env=environment, capture_output=True, text=True, timeout=30, check=False,
        )
        # The CLI's public output is credential-safe; never echo subprocess stderr.
        self.assertEqual(process.returncode, 0, process.stdout)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["authenticated_evidence_read"], "PASS")
        self.assertTrue(all(result["analytical_modules_present"].values()))
        self.assertEqual(result["proven_price"], "NOT_ASSESSED")
        self.assertEqual(result["research_validation"], "NOT_ASSESSED")
        self.assertEqual(result["price_policy"], "NOT_CONFIGURED")


if __name__ == "__main__":
    unittest.main()
