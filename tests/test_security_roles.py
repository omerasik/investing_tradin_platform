import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.security import (
    ROLE_PERMISSIONS,
    InMemoryRateLimiter,
    OperatorAuthenticator,
    OperatorPermission,
    OperatorRole,
)

AUDIT_PAYLOAD = {
    "event_type": "operator.note",
    "actor": "ignored-client-actor",
    "payload": {"scope": "paper"},
}
RISK_PAYLOAD = {
    "equity": 1_000,
    "exposures": [],
    "shocks": {"EQUITY": -0.1},
    "maximum_gross_notional": 500,
    "maximum_single_weight": 0.5,
    "maximum_scenario_loss": 50,
}


class SecurityRoleTests(unittest.TestCase):
    def _client(self, role: OperatorRole | None) -> TestClient:
        return TestClient(
            build_app(
                PlatformConfig(),
                SQLiteAuditStore(),
                OperatorAuthenticator("role-token", "role-subject", role),
                InMemoryRateLimiter(max_requests=20),
            )
        )

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer role-token"}

    def test_role_matrix_is_explicit_and_operator_is_the_only_superset(self) -> None:
        self.assertEqual(
            ROLE_PERMISSIONS[OperatorRole.VIEWER],
            frozenset({OperatorPermission.READ_EVIDENCE}),
        )
        self.assertEqual(
            ROLE_PERMISSIONS[OperatorRole.RESEARCHER],
            frozenset(
                {
                    OperatorPermission.READ_EVIDENCE,
                    OperatorPermission.RUN_RESEARCH,
                }
            ),
        )
        self.assertEqual(
            ROLE_PERMISSIONS[OperatorRole.DATA_STEWARD],
            frozenset(
                {
                    OperatorPermission.READ_EVIDENCE,
                    OperatorPermission.MANAGE_DATA,
                }
            ),
        )
        self.assertEqual(
            ROLE_PERMISSIONS[OperatorRole.RISK_REVIEWER],
            frozenset(
                {
                    OperatorPermission.READ_EVIDENCE,
                    OperatorPermission.REVIEW_RISK,
                    OperatorPermission.ACKNOWLEDGE_ALERT,
                }
            ),
        )
        self.assertEqual(
            ROLE_PERMISSIONS[OperatorRole.AUDITOR],
            frozenset(
                {
                    OperatorPermission.READ_EVIDENCE,
                    OperatorPermission.WRITE_AUDIT,
                }
            ),
        )
        self.assertEqual(
            ROLE_PERMISSIONS[OperatorRole.OPERATOR],
            frozenset(OperatorPermission),
        )

    def test_viewer_can_read_but_cannot_write_or_review_risk(self) -> None:
        client = self._client(OperatorRole.VIEWER)
        self.assertEqual(client.get("/audit/events", headers=self.headers).status_code, 200)

        audit = client.post("/audit/events", headers=self.headers, json=AUDIT_PAYLOAD)
        risk = client.post("/risk/portfolio", headers=self.headers, json=RISK_PAYLOAD)
        self.assertEqual((audit.status_code, risk.status_code), (403, 403))
        self.assertEqual((audit.json()["detail"], risk.json()["detail"]), ("Forbidden.", "Forbidden."))
        self.assertNotIn("www-authenticate", audit.headers)

    def test_specialized_roles_cannot_cross_permission_boundaries(self) -> None:
        auditor = self._client(OperatorRole.AUDITOR)
        audit = auditor.post("/audit/events", headers=self.headers, json=AUDIT_PAYLOAD)
        denied_risk = auditor.post("/risk/portfolio", headers=self.headers, json=RISK_PAYLOAD)
        self.assertEqual((audit.status_code, audit.json()["actor"]), (201, "role-subject"))
        self.assertEqual(denied_risk.status_code, 403)

        reviewer = self._client(OperatorRole.RISK_REVIEWER)
        risk = reviewer.post("/risk/portfolio", headers=self.headers, json=RISK_PAYLOAD)
        denied_audit = reviewer.post("/audit/events", headers=self.headers, json=AUDIT_PAYLOAD)
        self.assertEqual((risk.status_code, risk.json()["approved"]), (200, True))
        self.assertEqual(denied_audit.status_code, 403)

    def test_research_data_and_alert_commands_require_their_named_roles(self) -> None:
        strategy_payload = {
            "strategy_version": "role-test-v1",
            "family": "transparent_fixture",
            "hypothesis": "Permission routing is enforced.",
            "required_datasets": ["fixture-v1"],
            "feature_versions": ["return:v1"],
            "universe_rules": "Fixture only",
            "entry_logic": "No execution",
            "exit_logic": "No execution",
            "sizing_policy": "Zero capital",
            "risk_policy": "risk-v1",
            "cost_model_version": "cost-v1",
            "capacity_model": "fixture-v1",
            "expected_regimes": ["UNKNOWN"],
            "parameter_schema": {"lookback": "integer"},
            "failure_conditions": ["missing fixture"],
            "limitations": ["research only"],
            "idempotency_key": "role-test-strategy",
        }
        cadence_payload = {
            "account_id": "paper",
            "provider": "fixture",
            "interval_seconds": 60,
            "grace_seconds": 0,
        }
        commands = (
            (OperatorRole.RESEARCHER, "/research/strategies", strategy_payload),
            (OperatorRole.DATA_STEWARD, "/data-health/return-ingestion/cadence", cadence_payload),
            (OperatorRole.RISK_REVIEWER, f"/alerts/{uuid4()}/acknowledge", None),
        )
        for role, path, payload in commands:
            with self.subTest(role=role):
                allowed = self._client(role).post(path, headers=self.headers, json=payload)
                denied = self._client(OperatorRole.VIEWER).post(
                    path,
                    headers=self.headers,
                    json=payload,
                )
                self.assertEqual(allowed.status_code, 503)
                self.assertEqual(denied.status_code, 403)

    def test_environment_role_defaults_to_viewer_and_invalid_role_fails_closed(self) -> None:
        with patch.dict(
            "os.environ",
            {"TRADE_PLATFORM_OPERATOR_TOKEN": "env-token"},
            clear=True,
        ):
            authenticator = OperatorAuthenticator.from_environment()
        self.assertEqual(authenticator.role, OperatorRole.VIEWER)

        client = TestClient(
            build_app(
                PlatformConfig(),
                SQLiteAuditStore(),
                authenticator,
                InMemoryRateLimiter(max_requests=20),
            )
        )
        headers = {"Authorization": "Bearer env-token"}
        self.assertEqual(client.get("/audit/events", headers=headers).status_code, 200)
        self.assertEqual(
            client.post("/audit/events", headers=headers, json=AUDIT_PAYLOAD).status_code,
            403,
        )

        invalid = self._client(None)
        response = invalid.get("/audit/events", headers=self.headers)
        self.assertEqual((response.status_code, response.json()["detail"]), (503, "Operator role is not configured."))


if __name__ == "__main__":
    unittest.main()
