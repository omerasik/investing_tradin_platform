import os
import unittest
from datetime import timedelta
from decimal import Decimal

from tests.test_observability_sre_v2 import NOW, SPAN, TRACE, alert_policy, service


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ObservabilitySreV2PostgresTests(unittest.TestCase):
    def test_correlated_sre_evidence_is_idempotent_immutable_and_restartable(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.observability_sre_v2 import (
            AlertEventV2,
            AlertSeverity,
            AlertStatus,
            AlertV2,
            CandidateSloPolicyV2,
            DependencyProbeV2,
            Environment,
            FailureDrillV2,
            IncidentV2,
            PostgresSreV2Store,
            ProbeStatus,
            SliWindowV2,
            TelemetryEventV2,
            TelemetryKind,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase

        dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")
        database = PostgresDatabase(dsn)
        item = service()
        slo = CandidateSloPolicyV2(
            item.service_version_id, "dependency-availability", "healthy/total",
            Decimal("0.99"), 3600, 10, NOW,
        )
        policy = alert_policy(item)
        telemetry = TelemetryEventV2(
            item.service_version_id, TRACE, SPAN, None, TelemetryKind.SPAN,
            "postgres.probe", "ERROR", NOW, Decimal("50"),
            (("environment", "CI"),),
        )
        probe = DependencyProbeV2(
            item.service_version_id, "postgres", "DEPENDENCY",
            ProbeStatus.UNAVAILABLE, NOW, Decimal("50"), "CONNECTION_REFUSED",
            TRACE, (("evidence_uri", "fixture://cycle207/postgres"),),
        )
        window = SliWindowV2(
            slo.slo_policy_version_id, NOW, NOW + timedelta(hours=1), 0, 1, 1, 10
        )
        drill = FailureDrillV2(
            item.service_version_id, "postgres_unavailable", "reject_connections",
            "risk_and_submission_blocked", "risk_and_submission_blocked",
            NOW, NOW + timedelta(seconds=3), Decimal("3"),
            "fixture://cycle207/drill", Environment.CI,
        )
        alert = AlertV2(
            policy.alert_policy_version_id, "postgres:primary", NOW,
            "fixture://cycle207/postgres", TRACE,
        )
        events = (
            AlertEventV2(alert.alert_id, 0, AlertStatus.OPEN, "probe", NOW, ()),
            AlertEventV2(alert.alert_id, 1, AlertStatus.ACKNOWLEDGED, "operator", NOW + timedelta(seconds=1), ()),
            AlertEventV2(alert.alert_id, 2, AlertStatus.RESOLVED, "operator", NOW + timedelta(seconds=3), (("resolution", "fixture_recovery"),)),
        )
        incident = IncidentV2(
            alert.alert_id, AlertSeverity.CRITICAL, "platform-oncall", "runbook://database",
            NOW, NOW + timedelta(seconds=3), "fixture://cycle207/post-incident-review",
            (("impact", "fixture dependency unavailable"),),
        )
        store = PostgresSreV2Store(database)
        store.publish_service_bundle(item, slo, policy)
        store.publish_service_bundle(item, slo, policy)
        store.append_evidence((telemetry,), (probe,), (window,), (drill,))
        store.append_evidence((telemetry,), (probe,), (window,), (drill,))
        self.assertEqual(store.publish_alert(alert, events), alert.alert_id)
        self.assertEqual(store.publish_alert(alert, events), alert.alert_id)
        self.assertEqual(store.publish_incident(incident), incident.incident_id)
        self.assertEqual(store.latest_alert_status(alert.alert_id), AlertStatus.RESOLVED)
        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE sre_slo_policy_versions SET approved=TRUE WHERE slo_policy_version_id=%s",
                (slo.slo_policy_version_id,),
            )
        database.close()

        reopened = PostgresDatabase(dsn)
        restarted = PostgresSreV2Store(reopened)
        self.assertEqual(restarted.latest_alert_status(alert.alert_id), AlertStatus.RESOLVED)
        with reopened.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM sre_telemetry_events WHERE trace_id=%s", (TRACE,))
            self.assertEqual(int(cursor.fetchone()[0]), 1)
            cursor.execute("SELECT evidence_state,claim_status FROM sre_sli_windows WHERE sli_window_id=%s", (window.sli_window_id,))
            self.assertEqual(cursor.fetchone(), ("INSUFFICIENT_BASELINE", "ENGINEERING_EVIDENCE_ONLY"))
            cursor.execute("SELECT deployment_status FROM sre_service_versions WHERE service_version_id=%s", (item.service_version_id,))
            self.assertEqual(cursor.fetchone()[0], "EVIDENCE_ONLY")
        reopened.close()


if __name__ == "__main__":
    unittest.main()
