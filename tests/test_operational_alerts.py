from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trade_platform.operational_alerts import AlertSeverity, AlertStatus, SQLiteFailureDrillStore, SQLiteOperationalAlertStore
from trade_platform.shadow_mode import record_failure_drill


class OperationalAlertTests(unittest.TestCase):
    def test_active_alerts_deduplicate_and_persist_transitions(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.sqlite"
            alerts = SQLiteOperationalAlertStore(path)
            first = alerts.raise_alert(source="broker_sync", code="RECONCILIATION_FAILED", severity=AlertSeverity.CRITICAL, resource="account:paper", details={"discrepancies": "cash_mismatch"})
            self.assertEqual(alerts.raise_alert(source="broker_sync", code="RECONCILIATION_FAILED", severity=AlertSeverity.CRITICAL, resource="account:paper", details={}), first)
            acknowledged = alerts.transition(first.alert_id, AlertStatus.ACKNOWLEDGED, actor="operator")
            resolved = alerts.transition(acknowledged.alert_id, AlertStatus.RESOLVED, actor="operator")
            self.assertEqual(resolved.status, AlertStatus.RESOLVED)
            self.assertEqual(alerts.active(), ())
            alerts.close()
            reopened = SQLiteOperationalAlertStore(path)
            self.assertEqual(reopened.get(first.alert_id).status, AlertStatus.RESOLVED)
            reopened.close()

    def test_failure_drill_results_are_durable(self) -> None:
        drill = record_failure_drill("reconciliation_mismatch", "risk_block", "risk_block")
        store = SQLiteFailureDrillStore()
        store.append(drill)
        self.assertEqual(store.get(drill.drill_id), drill)
        store.close()


if __name__ == "__main__":
    unittest.main()
