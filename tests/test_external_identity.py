import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.external_identity import (
    ExternalIdentityError,
    ExternalSessionAuthenticator,
    VerifiedExternalSession,
    build_external_identity_mapping_policy,
)
from trade_platform.security import (
    AuthorizationDecision,
    AuthorizationOutcome,
    InMemoryRateLimiter,
    OperatorPermission,
    OperatorRole,
)


class MemoryRevocationStore:
    def __init__(self) -> None:
        self.revoked: set[str] = set()

    def revoke(self, session_id_hash: str, *, revoked_by: str, reason: str) -> None:
        self.revoked.add(session_id_hash)

    def is_revoked(self, session_id_hash: str) -> bool:
        return session_id_hash in self.revoked


class FixtureVerifier:
    def __init__(self, session: VerifiedExternalSession) -> None:
        self.session = session
        self.observed_token: str | None = None

    def verify_token(self, token: str) -> VerifiedExternalSession:
        self.observed_token = token
        return self.session


class MemoryDurableDecisionSink:
    durable = True

    def __init__(self, *, fail: bool = False) -> None:
        self.decisions: list[AuthorizationDecision] = []
        self.fail = fail

    def append_decision(self, decision: AuthorizationDecision) -> AuthorizationDecision:
        if self.fail:
            raise RuntimeError("fixture audit failure")
        self.decisions.append(decision)
        return decision


class ExternalIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 22, 12, tzinfo=UTC)
        self.policy = build_external_identity_mapping_policy(
            policy_name="operator-console",
            version="identity-map-v1",
            issuer="https://identity.example.test/tenant",
            audience="trade-platform",
            group_role_map={
                "trade-viewers": OperatorRole.VIEWER,
                "trade-risk-reviewers": OperatorRole.RISK_REVIEWER,
            },
            required_authentication_methods=frozenset({"mfa"}),
            maximum_session_age=timedelta(hours=1),
            approved_by="security-owner",
            approved_at=self.now - timedelta(days=1),
        )
        self.session = VerifiedExternalSession(
            issuer=self.policy.issuer,
            subject="operator-123",
            audiences=frozenset({self.policy.audience}),
            issued_at=self.now - timedelta(minutes=5),
            expires_at=self.now + timedelta(minutes=55),
            session_id="provider-session-secret",
            groups=frozenset({"trade-risk-reviewers"}),
            authentication_methods=frozenset({"password", "mfa"}),
        )

    def _authenticator(
        self, session: VerifiedExternalSession | None = None
    ) -> tuple[ExternalSessionAuthenticator, FixtureVerifier]:
        verifier = FixtureVerifier(session or self.session)
        return (
            ExternalSessionAuthenticator(verifier, self.policy, clock=lambda: self.now),
            verifier,
        )

    def test_verified_claims_map_to_one_server_owned_role(self) -> None:
        authenticator, verifier = self._authenticator()
        principal = authenticator.verify("Bearer opaque-provider-token")
        self.assertEqual(
            (principal.subject, principal.role, principal.authentication_method),
            ("operator-123", OperatorRole.RISK_REVIEWER, "verified_external_session"),
        )
        self.assertEqual(verifier.observed_token, "opaque-provider-token")
        self.assertEqual(len(principal.session_id_hash or ""), 64)
        self.assertNotIn("provider-session-secret", repr(principal))

    def test_trust_time_assurance_and_ambiguous_role_fail_closed(self) -> None:
        cases = (
            replace(self.session, issuer="https://attacker.example.test"),
            replace(self.session, expires_at=self.now - timedelta(minutes=5)),
            replace(self.session, authentication_methods=frozenset({"password"})),
            replace(
                self.session,
                groups=frozenset({"trade-viewers", "trade-risk-reviewers"}),
            ),
            replace(self.session, groups=frozenset({"unmapped"})),
        )
        for session in cases:
            with self.subTest(session=session), self.assertRaises(PermissionError):
                self._authenticator(session)[0].verify("Bearer opaque-provider-token")

    def test_policy_rejects_insecure_issuer_and_empty_mapping(self) -> None:
        for issuer, mapping in (
            ("http://identity.example.test", self.policy.group_role_map),
            (self.policy.issuer, {}),
        ):
            with self.subTest(issuer=issuer, mapping=mapping), self.assertRaises(
                ExternalIdentityError
            ):
                build_external_identity_mapping_policy(
                    policy_name="bad",
                    version="v1",
                    issuer=issuer,
                    audience="trade-platform",
                    group_role_map=mapping,
                    required_authentication_methods=frozenset({"mfa"}),
                    maximum_session_age=timedelta(hours=1),
                    approved_by="security-owner",
                    approved_at=self.now,
                )

    def test_tampered_or_future_approved_policy_fails_closed(self) -> None:
        self.policy.group_role_map["trade-risk-reviewers"] = OperatorRole.OPERATOR
        with self.assertRaisesRegex(PermissionError, "policy_hash_mismatch"):
            self._authenticator()[0].verify("Bearer opaque-provider-token")

        future_policy = build_external_identity_mapping_policy(
            policy_name="future-console",
            version="v1",
            issuer="https://identity.example.test/tenant",
            audience="trade-platform",
            group_role_map={"trade-risk-reviewers": OperatorRole.RISK_REVIEWER},
            required_authentication_methods=frozenset({"mfa"}),
            maximum_session_age=timedelta(hours=1),
            approved_by="security-owner",
            approved_at=self.now + timedelta(days=1),
        )
        with self.assertRaisesRegex(PermissionError, "not_yet_approved"):
            ExternalSessionAuthenticator(
                FixtureVerifier(self.session), future_policy, clock=lambda: self.now
            ).verify("Bearer opaque-provider-token")

    def test_revoked_session_is_rejected_even_though_the_jwt_has_not_expired(self) -> None:
        import hashlib

        revocation_store = MemoryRevocationStore()
        authenticator = ExternalSessionAuthenticator(
            FixtureVerifier(self.session),
            self.policy,
            clock=lambda: self.now,
            revocation_store=revocation_store,
        )
        # Not yet revoked: authenticates normally.
        authenticator.verify("Bearer opaque-provider-token")

        revocation_store.revoke(
            hashlib.sha256(self.session.session_id.encode()).hexdigest(),
            revoked_by="security-owner",
            reason="device compromise",
        )
        with self.assertRaisesRegex(PermissionError, "external_session_revoked"):
            authenticator.verify("Bearer opaque-provider-token")

    def test_api_requires_durable_audit_and_records_allow_and_deny(self) -> None:
        authenticator, _ = self._authenticator()
        with self.assertRaisesRegex(
            ValueError, "external_session_requires_durable_authorization_audit"
        ):
            build_app(authenticator=authenticator)

        sink = MemoryDurableDecisionSink()
        client = TestClient(
            build_app(
                PlatformConfig(),
                SQLiteAuditStore(),
                authenticator,
                InMemoryRateLimiter(max_requests=20),
                authorization_decision_sink=sink,
            )
        )
        headers = {"Authorization": "Bearer opaque-provider-token"}
        allowed = client.get("/audit/events", headers=headers)
        denied = client.post(
            "/audit/events",
            headers=headers,
            json={"event_type": "note", "actor": "ignored", "payload": {}},
        )
        unauthenticated = client.get("/audit/events")
        self.assertEqual(
            (allowed.status_code, denied.status_code, unauthenticated.status_code),
            (200, 403, 401),
        )
        self.assertEqual(
            [decision.outcome for decision in sink.decisions],
            [
                AuthorizationOutcome.ALLOW,
                AuthorizationOutcome.DENY,
                AuthorizationOutcome.DENY,
            ],
        )
        self.assertEqual(
            [decision.requested_permission for decision in sink.decisions],
            [
                OperatorPermission.READ_EVIDENCE,
                OperatorPermission.WRITE_AUDIT,
                OperatorPermission.READ_EVIDENCE,
            ],
        )
        evidence = repr(sink.decisions)
        self.assertNotIn("opaque-provider-token", evidence)
        self.assertNotIn("provider-session-secret", evidence)
        self.assertIn("identity-map-v1", evidence)

    def test_audit_failure_blocks_an_otherwise_allowed_request(self) -> None:
        authenticator, _ = self._authenticator()
        client = TestClient(
            build_app(
                PlatformConfig(),
                SQLiteAuditStore(),
                authenticator,
                InMemoryRateLimiter(max_requests=20),
                authorization_decision_sink=MemoryDurableDecisionSink(fail=True),
            )
        )
        response = client.get(
            "/audit/events", headers={"Authorization": "Bearer opaque-provider-token"}
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Authorization audit is unavailable.")


if __name__ == "__main__":
    unittest.main()
