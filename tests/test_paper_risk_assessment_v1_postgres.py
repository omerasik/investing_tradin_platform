"""Paper risk assessment read path over the real account policy tables (fixture values, never owner decisions)."""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.test_paper_risk_assessment_v1 import VALUES
from trade_platform.account_policy_v1 import AccountContextV1, AccountKindV1, AccountPolicyV1
from trade_platform.paper_risk_assessment_v1 import (
    ALLOWED,
    UNRESOLVED,
    ExecutionContextV1,
    PaperRiskRequestV1,
    read_paper_risk_assessment_v1,
)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PaperRiskAssessmentPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")

    def test_the_stored_latest_version_decides_and_a_draft_supersedes_it(self) -> None:
        from trade_platform.account_policy_v1 import PostgresAccountPolicyStoreV1
        from trade_platform.persistence import PostgresDatabase

        account = AccountContextV1(f"test-{uuid.uuid4().hex[:12]}", AccountKindV1.PERSONAL_PAPER, "Fixture", "USDT")
        cycle = f"test-cycle-{uuid.uuid4().hex[:12]}"  # no fills, no opened holdout: gross, flat
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])

        def assess(expected: str | None = None) -> dict[str, object]:
            as_of = datetime.now(UTC)
            request = PaperRiskRequestV1(account.account_id, "s1", "t1", "BTCUSDT", 1, as_of,
                                         ExecutionContextV1.PAPER_INCUBATION, expected,
                                         as_of - timedelta(seconds=1), Decimal("100"),
                                         as_of - timedelta(seconds=1))
            with database.transaction() as connection, connection.cursor() as cursor:
                return read_paper_risk_assessment_v1(cursor, request, cycle_id=cycle)

        try:
            store = PostgresAccountPolicyStoreV1(database)
            store.register_account(account)
            active = AccountPolicyV1(account, VALUES, "fixture-owner", "2026-09-01")
            store.record_policy(active)
            out = assess(str(active.policy_version_id))
            self.assertEqual(ALLOWED, out["verdict"], out)
            self.assertEqual("10", out["sizing"]["open_quantity"])  # type: ignore[index]
            store.record_policy(AccountPolicyV1(account, {"paper_starting_capital": "1"}))
            out = assess()
            self.assertEqual(UNRESOLVED, out["verdict"])
            self.assertIn("ACCOUNT_POLICY_UNCONFIGURED", out["unresolved_reasons"])  # type: ignore[operator]
            self.assertIsNone(out["sizing"])
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
