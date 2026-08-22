import os
import unittest
from datetime import UTC, datetime, timedelta


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ExternalIdentityPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace(
                "postgresql://", "postgresql+psycopg://", 1
            ),
        )
        command.upgrade(config, "head")

    def test_policy_and_decision_survive_restart_and_are_immutable(self) -> None:
        from trade_platform.external_identity import (
            ExternalIdentityError,
            PostgresIdentitySecurityStore,
            build_external_identity_mapping_policy,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.security import (
            AuthorizationOutcome,
            OperatorPermission,
            OperatorRole,
            build_authorization_decision,
        )

        now = datetime(2026, 8, 22, 12, tzinfo=UTC)
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        store = PostgresIdentitySecurityStore(database)
        policy = build_external_identity_mapping_policy(
            policy_name="integration-operator-console",
            version="v1",
            issuer="https://identity.example.test/tenant",
            audience="trade-platform",
            group_role_map={"integration-viewers": OperatorRole.VIEWER},
            required_authentication_methods=frozenset({"mfa"}),
            maximum_session_age=timedelta(minutes=30),
            approved_by="integration-security-owner",
            approved_at=now,
        )
        self.assertEqual(store.append_policy(policy), policy)
        self.assertEqual(store.append_policy(policy), policy)

        decision = build_authorization_decision(
            subject="integration-operator",
            role=OperatorRole.VIEWER,
            requested_permission=OperatorPermission.READ_EVIDENCE,
            outcome=AuthorizationOutcome.ALLOW,
            reason="ROLE_PERMISSION_ALLOWED",
            authentication_method="verified_external_session",
            session_id_hash="a" * 64,
            mapping_policy_id=policy.policy_id,
            mapping_policy_version=policy.version,
            occurred_at=now + timedelta(minutes=1),
        )
        store.append_decision(decision)
        database.close()

        reopened = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        restarted = PostgresIdentitySecurityStore(reopened)
        self.assertEqual(restarted.recent_decisions(1), [decision])
        with (
            self.assertRaises(PersistenceError),
            reopened.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE authorization_decisions SET reason='tampered' WHERE decision_id=%s",
                (decision.decision_id,),
            )
        with self.assertRaises(ExternalIdentityError):
            restarted.append_decision(
                decision.__class__(
                    decision.decision_id,
                    decision.occurred_at,
                    decision.subject,
                    decision.role,
                    decision.requested_permission,
                    decision.outcome,
                    "tampered",
                    decision.authentication_method,
                    decision.session_id_hash,
                    decision.mapping_policy_id,
                    decision.mapping_policy_version,
                    decision.content_hash,
                )
            )
        reopened.close()


if __name__ == "__main__":
    unittest.main()
