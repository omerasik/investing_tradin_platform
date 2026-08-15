import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from trade_platform.policy_registry import PolicyDocument, PolicyRegistryError, SQLitePolicyRegistry


class PolicyRegistryTests(unittest.TestCase):
    def test_documents_are_immutable_content_addressed_and_persistent(self) -> None:
        document = PolicyDocument("risk", "2026-01", {"maximum_order_notional": "1000"}, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "policies.sqlite"
            registry = SQLitePolicyRegistry(path); registry.append(document)
            self.assertEqual(registry.resolve_risk_policy("2026-01").maximum_order_notional, Decimal("1000"))
            self.assertEqual(registry.get("risk", "2026-01").digest, document.digest)
            with self.assertRaisesRegex(PolicyRegistryError, "duplicate"):
                registry.append(document)
            registry._connection.execute("UPDATE policy_documents SET payload_json = ? WHERE policy_type = ? AND version = ?", ('{"maximum_order_notional":"1"}', "risk", "2026-01"))
            registry._connection.commit()
            with self.assertRaisesRegex(PolicyRegistryError, "digest_mismatch"):
                registry.get("risk", "2026-01")
            registry.close()
            reopened = SQLitePolicyRegistry(path)
            with self.assertRaisesRegex(PolicyRegistryError, "digest_mismatch"):
                reopened.get("risk", "2026-01")
            with self.assertRaisesRegex(PolicyRegistryError, "unknown"):
                reopened.get("portfolio", "2026-01")
            reopened.close()

    def test_portfolio_policy_decoding_requires_complete_typed_limits(self) -> None:
        registry = SQLitePolicyRegistry()
        registry.append(PolicyDocument("portfolio", "2026-01", {
            "maximum_gross_notional": "10000",
            "maximum_single_weight": "0.2",
            "maximum_scenario_loss": "500",
            "maximum_group_notionals": {"country:US": "7500"},
            "maximum_factor_notionals": {"market": "6000"},
            "stress_scenarios": {"risk_off": {"ETF": "-0.1"}},
        }, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        policy = registry.resolve_portfolio_policy("2026-01")
        self.assertEqual(policy.maximum_gross_notional, Decimal("10000"))
        self.assertEqual(policy.maximum_group_notionals, {"country:US": Decimal("7500")})
        self.assertEqual(registry.resolve_portfolio_scenarios("2026-01")[0].shocks, {"ETF": Decimal("-0.1")})
        registry.append(PolicyDocument("portfolio", "history-without-minimum", {"maximum_gross_notional": "10000", "maximum_single_weight": "1", "maximum_scenario_loss": "500", "maximum_var_loss": "0.1", "minimum_historical_observations": 1}, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        with self.assertRaisesRegex(PolicyRegistryError, "invalid_portfolio"):
            registry.resolve_portfolio_policy("history-without-minimum")
        registry.append(PolicyDocument("portfolio", "incomplete", {"maximum_gross_notional": "10000"}, "risk-committee", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        with self.assertRaisesRegex(PolicyRegistryError, "invalid_portfolio"):
            registry.resolve_portfolio_policy("incomplete")
        registry.close()


if __name__ == "__main__":
    unittest.main()
