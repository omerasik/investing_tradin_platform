import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from trade_platform.risk import SQLiteKillSwitchRegistry


class KillSwitchPersistenceTests(unittest.TestCase):
    def test_scopes_are_append_only_and_survive_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "switches.sqlite"
            registry = SQLiteKillSwitchRegistry(path)
            registry.activate("ACCOUNT:paper-main", actor="risk-operator")
            registry.activate("BROKER:local-sandbox")
            self.assertTrue(registry.blocks("US:NYSE:SPY", "trend-v1", account_id="paper-main"))
            self.assertTrue(registry.blocks("US:NYSE:SPY", "trend-v1", broker_name="local-sandbox"))
            with self.assertRaises(PermissionError):
                registry.deactivate("ACCOUNT:paper-main", explicit_operator_approval=False)
            registry.deactivate("ACCOUNT:paper-main", explicit_operator_approval=True)
            self.assertFalse(registry.blocks("US:NYSE:SPY", "trend-v1", account_id="paper-main"))
            self.assertEqual(len(registry.events("ACCOUNT:paper-main")), 2)
            registry.close()
            reopened = SQLiteKillSwitchRegistry(path)
            self.assertTrue(reopened.blocks("US:NYSE:SPY", "trend-v1", broker_name="local-sandbox"))
            reopened.close()

    def test_invalid_scope_is_rejected(self) -> None:
        registry = SQLiteKillSwitchRegistry()
        with self.assertRaisesRegex(ValueError, "invalid_kill"):
            registry.activate("LIVE")
        registry.close()


if __name__ == "__main__":
    unittest.main()
