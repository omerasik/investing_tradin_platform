"""Explicit PostgreSQL composition for the durable paper-runtime core.

This module is deliberately separate from the legacy SQLite research runtime.
It composes only PostgreSQL authorities and exposes no order-submission method
until the PostgreSQL pre-trade evidence stores are present and wired.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .broker_adapter import PaperBrokerAdapter
from .broker_sync import PaperBrokerSyncService
from .config import PlatformConfig
from .persistence import PersistenceTarget, PostgresDatabase
from .portfolio_evidence import OmsReconciledAccountStore
from .postgres_decision_authorities import (
    PostgresInstrumentStore,
    PostgresModelRegistry,
    PostgresSignalStore,
)
from .postgres_market_context import (
    PostgresExecutionEvidenceStore,
    PostgresPortfolioReturnStore,
    PostgresQuoteStore,
)
from .postgres_paper_oms import PostgresBrokerEventStore, PostgresPaperOms
from .postgres_pretrade import PostgresPolicyRegistry, PostgresPreTradeAssessmentStore
from .postgres_quant_validation import PostgresPromotionLedger, PostgresQuantValidationStore
from .risk import PostgresKillSwitchRegistry, PostgresRiskStore


class PostgresRuntimeCompositionError(ValueError):
    """Raised when a PostgreSQL authority graph cannot be built safely."""


@dataclass(frozen=True, slots=True)
class PostgresRuntimeIdentityMap:
    """Explicit semantic-to-relational identities; values are never inferred."""

    risk_policy_versions: dict[str, UUID]
    strategy_versions: dict[str, UUID]
    dataset_versions: dict[str, UUID]

    def risk_policy_id(self, version: str) -> UUID:
        try:
            return self.risk_policy_versions[version]
        except KeyError as error:
            raise PostgresRuntimeCompositionError(
                "postgres_risk_policy_identity_mapping_required"
            ) from error


@dataclass(slots=True)
class PostgresPaperCoreAuthorities:
    """PostgreSQL-only authorities already safe to compose in paper mode.

    ``broker`` has immutable policy and keyed assessment stores, but the graph
    intentionally remains non-submission-ready until every upstream pre-trade
    evidence authority is PostgreSQL-backed.
    """

    database: PostgresDatabase
    oms: PostgresPaperOms
    events: PostgresBrokerEventStore
    reconciled_accounts: OmsReconciledAccountStore
    broker: PaperBrokerSyncService
    kill_switches: PostgresKillSwitchRegistry
    risk: PostgresRiskStore
    validation: PostgresQuantValidationStore
    promotions: PostgresPromotionLedger
    policies: PostgresPolicyRegistry
    assessments: PostgresPreTradeAssessmentStore
    quotes: PostgresQuoteStore
    execution_evidence: PostgresExecutionEvidenceStore
    return_history: PostgresPortfolioReturnStore
    instruments: PostgresInstrumentStore
    signals: PostgresSignalStore
    models: PostgresModelRegistry

    @property
    def submission_ready(self) -> bool:
        return False

    def close(self) -> None:
        # Every adapter shares this connection; the composition root owns its
        # lifetime and closes it exactly once.
        self.database.close()


def build_postgres_paper_core(
    *,
    config: PlatformConfig,
    adapter: PaperBrokerAdapter,
    identities: PostgresRuntimeIdentityMap,
    risk_policy_version: str,
) -> PostgresPaperCoreAuthorities:
    """Compose existing PostgreSQL authorities without any SQLite fallback."""
    if config.persistence_target is not PersistenceTarget.POSTGRES:
        raise PostgresRuntimeCompositionError("postgres_persistence_target_required")
    if not config.paper_trading_enabled or config.live_trading_enabled:
        raise PostgresRuntimeCompositionError("paper_only_runtime_required")
    if adapter.capabilities.network_connected:
        raise PostgresRuntimeCompositionError("network_connected_adapter_not_permitted")

    risk_policy_id = identities.risk_policy_id(risk_policy_version)
    database = config.create_database()
    if not isinstance(database, PostgresDatabase):
        database.close()
        raise PostgresRuntimeCompositionError("postgres_database_adapter_required")
    try:
        oms = PostgresPaperOms(database)
        events = PostgresBrokerEventStore(database, adapter.configuration.account_id)
        reconciled_accounts = OmsReconciledAccountStore(oms)
        policies = PostgresPolicyRegistry(database)
        assessments = PostgresPreTradeAssessmentStore(
            database, integrity_key=config.assessment_integrity_key()
        )
        broker = PaperBrokerSyncService(
            oms,
            adapter,
            events,
            assessment_store=assessments,
            policy_registry=policies,
        )
        return PostgresPaperCoreAuthorities(
            database=database,
            oms=oms,
            events=events,
            reconciled_accounts=reconciled_accounts,
            broker=broker,
            kill_switches=PostgresKillSwitchRegistry(database),
            risk=PostgresRiskStore(database, risk_policy_id),
            validation=PostgresQuantValidationStore(
                database,
                strategy_versions=identities.strategy_versions,
                dataset_versions=identities.dataset_versions,
            ),
            promotions=PostgresPromotionLedger(
                database, strategy_versions=identities.strategy_versions
            ),
            policies=policies,
            assessments=assessments,
            quotes=PostgresQuoteStore(database),
            execution_evidence=PostgresExecutionEvidenceStore(database),
            return_history=PostgresPortfolioReturnStore(database),
            instruments=PostgresInstrumentStore(database),
            signals=PostgresSignalStore(database),
            models=PostgresModelRegistry(database),
        )
    except Exception:
        database.close()
        raise
