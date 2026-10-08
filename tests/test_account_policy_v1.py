"""Phase R9 -- account policies have no economic defaults and feed the existing RiskEngine explicitly.

Every value below is a TEST FIXTURE, not an owner decision (OR-11).
"""

from __future__ import annotations

import os
import unittest
import uuid
from decimal import Decimal

from trade_platform.account_policy_v1 import (
    ACCOUNT_FIELDS_V1,
    PROP_FIELDS_V1,
    RISK_FIELDS_V1,
    STATUS_ACTIVE,
    STATUS_UNCONFIGURED,
    AccountContextV1,
    AccountKindV1,
    AccountPolicyError,
    AccountPolicyV1,
)
from trade_platform.domain import RiskPolicy

FIXTURE_VALUES: dict[str, object] = {
    "minimum_data_quality": "0.9", "maximum_spread_fraction": "0.003", "maximum_order_notional": "500",
    "maximum_position_notional": "1500", "maximum_daily_order_notional": "4000", "maximum_event_risk": "0.4",
    "maximum_expected_slippage_fraction": "0.002", "max_market_age_seconds": 15, "maximum_per_trade_loss": "20",
    "maximum_stop_distance_fraction": "0.03", "stop_gap_buffer_fraction": "0.01",
    "paper_starting_capital": "10000", "paper_order_notional": "250", "maximum_leverage": "2",
    "daily_loss_limit": "150", "maximum_drawdown_limit": "800", "allowed_symbols": ["BTCUSDT", "ETHUSDT"],
}
PROP_VALUES: dict[str, object] = {
    "prop_firm": "fixture-prop", "prop_daily_loss_limit": "100", "prop_max_trailing_drawdown": "500",
    "prop_profit_target": "800", "prop_min_trading_days": 5,
}


def personal(account_id: str = "personal-paper") -> AccountContextV1:
    return AccountContextV1(account_id, AccountKindV1.PERSONAL_PAPER, "Personal paper", "USDT")


class AccountPolicyTests(unittest.TestCase):
    def test_an_empty_policy_is_unconfigured_and_names_every_owner_value(self) -> None:
        policy = AccountPolicyV1(personal(), {})
        self.assertEqual(STATUS_UNCONFIGURED, policy.status)
        self.assertEqual(len(RISK_FIELDS_V1) + len(ACCOUNT_FIELDS_V1) + 1, len(policy.unresolved))
        self.assertIn("MISSING_OWNER_PAPER_STARTING_CAPITAL_OR_11", policy.unresolved)
        with self.assertRaises(AccountPolicyError):
            policy.risk_policy()

    def test_an_active_policy_yields_a_risk_policy_with_no_dataclass_default(self) -> None:
        policy = AccountPolicyV1(personal(), FIXTURE_VALUES, "owner", "2026-10-08")
        self.assertEqual(STATUS_ACTIVE, policy.status)
        risk = policy.risk_policy()
        defaults = RiskPolicy()
        self.assertEqual(Decimal("500"), risk.maximum_order_notional)
        self.assertNotEqual(defaults.maximum_order_notional, risk.maximum_order_notional)
        self.assertEqual(15, risk.max_market_age_seconds)
        self.assertTrue(risk.per_trade_controls_configured)
        self.assertEqual(set(RISK_FIELDS_V1), set(policy.risk_policy_document_payload()))

    def test_prop_accounts_also_need_the_prop_rules(self) -> None:
        prop = AccountContextV1("prop-paper", AccountKindV1.PROP_PAPER, "Prop paper", "USD")
        self.assertEqual(STATUS_UNCONFIGURED, AccountPolicyV1(prop, FIXTURE_VALUES, "owner", "2026-10-08").status)
        self.assertEqual(len(PROP_FIELDS_V1), sum(r.startswith("MISSING_OWNER_PROP_") for r in
                                                  AccountPolicyV1(prop, FIXTURE_VALUES, "owner", "2026-10-08")
                                                  .unresolved))
        self.assertEqual(STATUS_ACTIVE,
                         AccountPolicyV1(prop, {**FIXTURE_VALUES, **PROP_VALUES}, "owner", "2026-10-08").status)
        with self.assertRaises(AccountPolicyError):
            AccountPolicyV1(personal(), {**FIXTURE_VALUES, **PROP_VALUES})  # prop rules on a personal account

    def test_invented_or_malformed_values_are_refused(self) -> None:
        for name, bad in (("maximum_order_notional", 500.0), ("maximum_order_notional", "-1"),
                          ("minimum_data_quality", "1.5"), ("max_market_age_seconds", 0),
                          ("allowed_symbols", []), ("maximum_stop_distance_fraction", "0"),
                          ("surprise_field", "1")):
            with self.subTest(name=name), self.assertRaises(AccountPolicyError):
                AccountPolicyV1(personal(), {**FIXTURE_VALUES, name: bad})

    def test_identity_is_content_addressed(self) -> None:
        a = AccountPolicyV1(personal(), FIXTURE_VALUES, "owner", "2026-10-08")
        b = AccountPolicyV1(personal(), dict(reversed(list(FIXTURE_VALUES.items()))), "owner", "2026-10-08")
        c = AccountPolicyV1(personal(), {**FIXTURE_VALUES, "paper_order_notional": "300"}, "owner", "2026-10-08")
        self.assertEqual(a.content_hash, b.content_hash)
        self.assertNotEqual(a.content_hash, c.content_hash)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class AccountPolicyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")

    def test_only_active_versions_are_served_and_they_reproduce(self) -> None:
        from trade_platform.account_policy_v1 import PostgresAccountPolicyStoreV1
        from trade_platform.persistence import PostgresDatabase

        account = personal(f"test-{uuid.uuid4().hex[:12]}")
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            store = PostgresAccountPolicyStoreV1(database)
            store.register_account(account)
            store.record_policy(AccountPolicyV1(account, {}))
            self.assertIsNone(store.latest_active(account))
            active = AccountPolicyV1(account, FIXTURE_VALUES, "owner", "2026-10-08")
            store.record_policy(active)
            loaded = store.latest_active(account)
            assert loaded is not None
            self.assertEqual(active.content_hash, loaded.content_hash)
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
