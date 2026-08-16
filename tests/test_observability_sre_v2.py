import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.observability_sre_v2 import (
    AlertEventV2,
    AlertPolicyV2,
    AlertSeverity,
    AlertStatus,
    AlertV2,
    CandidateSloPolicyV2,
    DependencyProbeV2,
    Environment,
    FailureDrillV2,
    IncidentV2,
    ProbeStatus,
    ServiceVersionV2,
    SliWindowV2,
    SreV2Error,
    TelemetryEventV2,
    TelemetryKind,
)

NOW = datetime(2025, 3, 1, 12, tzinfo=UTC)
TRACE = hashlib.md5(b"cycle207-trace", usedforsecurity=False).hexdigest()
SPAN = hashlib.md5(b"span", usedforsecurity=False).hexdigest()[:16]


def service() -> ServiceVersionV2:
    return ServiceVersionV2(
        uuid4(), "trade-platform", "cycle207-v1", hashlib.sha1(b"fixture", usedforsecurity=False).hexdigest(),
        Environment.CI, ("postgres", "data-health", "reconciliation"), NOW,
    )


def alert_policy(item: ServiceVersionV2) -> AlertPolicyV2:
    return AlertPolicyV2(
        item.service_version_id, "DEPENDENCY_UNAVAILABLE", AlertSeverity.CRITICAL,
        "platform-oncall", "runbook://dependency-unavailable", "page-primary-then-secondary",
        300, (("probe_status", "UNAVAILABLE"),), NOW,
    )


class ObservabilitySreV2Tests(unittest.TestCase):
    def test_correlated_telemetry_and_business_probe_are_secret_safe(self) -> None:
        item = service()
        event = TelemetryEventV2(
            item.service_version_id, TRACE, SPAN, None, TelemetryKind.SPAN,
            "reconciliation.probe", "INFO", NOW, Decimal("12.5"),
            (("build_sha", item.build_sha), ("environment", item.environment.value)),
        )
        probe = DependencyProbeV2(
            item.service_version_id, "reconciliation", "BUSINESS_HEALTH",
            ProbeStatus.UNAVAILABLE, NOW, Decimal("12.5"), "MISMATCH",
            TRACE, (("evidence_uri", "fixture://cycle207/reconciliation"),),
        )
        self.assertEqual(event.trace_id, probe.trace_id)
        with self.assertRaisesRegex(SreV2Error, "sensitive"):
            TelemetryEventV2(
                item.service_version_id, TRACE, SPAN, None, TelemetryKind.LOG,
                "unsafe", "INFO", NOW, None, (("authorization_token", "value"),),
            )

    def test_candidate_slo_never_claims_attainment_without_baseline(self) -> None:
        item = service()
        slo = CandidateSloPolicyV2(
            item.service_version_id, "api-availability", "good_requests/requests",
            Decimal("0.999"), 86400, 30, NOW,
        )
        window = SliWindowV2(
            slo.slo_policy_version_id, NOW, NOW + timedelta(days=1), 999, 1000, 1, 30
        )
        self.assertEqual(window.measured_ratio, Decimal("0.999"))
        self.assertEqual(window.evidence_state, "INSUFFICIENT_BASELINE")
        self.assertFalse(slo.approved)
        self.assertEqual(slo.status, "CANDIDATE_ONLY")

    def test_actionable_alert_has_owner_runbook_escalation_and_valid_lifecycle(self) -> None:
        item = service()
        policy = alert_policy(item)
        alert = AlertV2(
            policy.alert_policy_version_id, "postgres:primary", NOW,
            "fixture://cycle207/probe", TRACE,
        )
        opened = AlertEventV2(alert.alert_id, 0, AlertStatus.OPEN, "probe", NOW, ())
        acknowledged = AlertEventV2(
            alert.alert_id, 1, AlertStatus.ACKNOWLEDGED, "operator", NOW + timedelta(minutes=1), ()
        )
        resolved = AlertEventV2(
            alert.alert_id, 2, AlertStatus.RESOLVED, "operator", NOW + timedelta(minutes=3),
            (("resolution", "dependency_recovered"),),
        )
        self.assertEqual((opened.status, acknowledged.status, resolved.status), tuple(AlertStatus))
        self.assertEqual(policy.owner, "platform-oncall")
        self.assertTrue(policy.runbook_uri.startswith("runbook://"))

    def test_incident_resolution_requires_post_incident_review(self) -> None:
        item = service(); policy = alert_policy(item)
        alert = AlertV2(policy.alert_policy_version_id, "postgres:primary", NOW, "fixture://evidence", TRACE)
        with self.assertRaisesRegex(SreV2Error, "invalid_incident"):
            IncidentV2(
                alert.alert_id, AlertSeverity.CRITICAL, "platform-oncall",
                "runbook://database", NOW, NOW + timedelta(minutes=5), None,
                (("impact", "fixture outage"),),
            )
        incident = IncidentV2(
            alert.alert_id, AlertSeverity.CRITICAL, "platform-oncall", "runbook://database",
            NOW, NOW + timedelta(minutes=5), "fixture://cycle207/post-incident-review",
            (("impact", "fixture outage"),),
        )
        self.assertEqual(incident.status, "RESOLVED")

    def test_failure_drill_measures_recovery_without_claiming_deployment(self) -> None:
        item = service()
        drill = FailureDrillV2(
            item.service_version_id, "postgres_unavailable", "reject_connections",
            "risk_and_submission_blocked", "risk_and_submission_blocked",
            NOW, NOW + timedelta(seconds=4), Decimal("4"),
            "fixture://cycle207/drill", Environment.CI,
        )
        self.assertTrue(drill.passed)
        self.assertEqual(item.deployment_status, "EVIDENCE_ONLY")


if __name__ == "__main__":
    unittest.main()
