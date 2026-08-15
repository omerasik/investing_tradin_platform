import unittest
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

from trade_platform.broker_adapter import (
    BrokerCapabilities,
    BrokerConfiguration,
    BrokerMode,
    SandboxPaperBrokerAdapter,
)
from trade_platform.config import PlatformConfig
from trade_platform.paper_execution import CashBalance
from trade_platform.paper_runtime import (
    PaperPolicySelection,
    PaperRuntimeError,
    build_paper_runtime,
)
from trade_platform.persistence import PersistenceTarget, PostgresDatabase
from trade_platform.postgres_paper_oms import PostgresBrokerEventStore, PostgresPaperOms
from trade_platform.postgres_quant_validation import (
    PostgresPromotionLedger,
    PostgresQuantValidationStore,
)
from trade_platform.postgres_runtime import (
    PostgresRuntimeCompositionError,
    PostgresRuntimeIdentityMap,
    build_postgres_paper_core,
)
from trade_platform.risk import PostgresKillSwitchRegistry, PostgresRiskStore


class PostgresRuntimeCompositionTests(unittest.TestCase):
    @staticmethod
    def config() -> PlatformConfig:
        return PlatformConfig(
            environment="paper",
            persistence_target=PersistenceTarget.POSTGRES,
            persistence_location="postgresql://test.invalid/trade_platform",
            assessment_integrity_key_reference="env:TEST_ASSESSMENT_KEY",
        )

    @staticmethod
    def adapter() -> SandboxPaperBrokerAdapter:
        return SandboxPaperBrokerAdapter(
            BrokerConfiguration("deterministic", BrokerMode.SIMULATED_PAPER, "paper"),
            cash=CashBalance("USD", Decimal(1000)),
        )

    def test_legacy_builder_rejects_postgres_before_any_sqlite_construction(self) -> None:
        config = self.config()
        with (
            patch("trade_platform.paper_runtime.SQLitePolicyRegistry") as sqlite_policy,
            self.assertRaisesRegex(
                PaperRuntimeError, "postgres_paper_runtime_requires_explicit_composition"
            ),
        ):
            build_paper_runtime(
                config=config,
                database_path="would-have-been-sqlite.db",
                adapter=self.adapter(),
                policy_selection=PaperPolicySelection("risk:v1", "portfolio:v1", uuid4()),
            )
        sqlite_policy.assert_not_called()

    def test_core_composition_contains_only_postgres_safety_authorities(self) -> None:
        database = Mock(spec=PostgresDatabase)
        # Runtime type validation intentionally rejects generic database mocks;
        # a concrete instance shell avoids opening a network connection.
        database.__class__ = PostgresDatabase
        identities = PostgresRuntimeIdentityMap(
            {"risk:v1": uuid4()}, {"strategy:v1": uuid4()}, {"dataset:v1": uuid4()}
        )
        with patch.object(PlatformConfig, "create_database", return_value=database):
            core = build_postgres_paper_core(
                config=self.config(),
                adapter=self.adapter(),
                identities=identities,
                risk_policy_version="risk:v1",
            )
        self.assertIsInstance(core.oms, PostgresPaperOms)
        self.assertIsInstance(core.events, PostgresBrokerEventStore)
        self.assertIsInstance(core.kill_switches, PostgresKillSwitchRegistry)
        self.assertIsInstance(core.risk, PostgresRiskStore)
        self.assertIsInstance(core.validation, PostgresQuantValidationStore)
        self.assertIsInstance(core.promotions, PostgresPromotionLedger)
        self.assertFalse(core.submission_ready)
        authorities = (
            core.oms,
            core.events,
            core.reconciled_accounts,
            core.broker,
            core.kill_switches,
            core.risk,
            core.validation,
            core.promotions,
        )
        self.assertFalse(any(type(value).__name__.startswith("SQLite") for value in authorities))
        core.close()
        database.close.assert_called_once_with()

    def test_composition_rejects_missing_identity_and_network_adapter(self) -> None:
        with self.assertRaisesRegex(
            PostgresRuntimeCompositionError,
            "postgres_risk_policy_identity_mapping_required",
        ):
            build_postgres_paper_core(
                config=self.config(),
                adapter=self.adapter(),
                identities=PostgresRuntimeIdentityMap({}, {}, {}),
                risk_policy_version="missing",
            )

        adapter = Mock()
        adapter.capabilities = BrokerCapabilities(True, True, True, True, True, True)
        adapter.configuration = BrokerConfiguration(
            "network", BrokerMode.SANDBOX_PAPER, "paper"
        )
        with self.assertRaisesRegex(
            PostgresRuntimeCompositionError, "network_connected_adapter_not_permitted"
        ):
            build_postgres_paper_core(
                config=self.config(),
                adapter=adapter,
                identities=PostgresRuntimeIdentityMap(
                    {"risk:v1": uuid4()}, {}, {}
                ),
                risk_policy_version="risk:v1",
            )


if __name__ == "__main__":
    unittest.main()
