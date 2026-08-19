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
from .operational_alerts import PostgresOperationalAlertStore
from .paper_runtime import PaperPolicySelection, PaperRuntime, PaperRuntimeError
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
from .postgres_recovery import PostgresRecoveryGate
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
    recovery: PostgresRecoveryGate
    alerts: PostgresOperationalAlertStore

    @property
    def submission_ready(self) -> bool:
        return False

    def close(self) -> None:
        # Every adapter shares this connection; the composition root owns its
        # lifetime and closes it exactly once.
        self.database.close()


@dataclass(slots=True)
class ConfiguredPostgresPaperRuntime:
    """Submission-capable facade retaining every PostgreSQL core authority."""

    paper: PaperRuntime
    core: PostgresPaperCoreAuthorities

    @property
    def submission_ready(self) -> bool:
        return True

    def assess(self, intent, *, observed_at):
        return self.paper.assess(intent, observed_at=observed_at)

    def assess_and_submit(self, intent, *, observed_at):
        return self.paper.assess_and_submit(intent, observed_at=observed_at)

    def close(self) -> None:
        self.paper.close()


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
        alerts = PostgresOperationalAlertStore(database)
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
            risk=PostgresRiskStore(database, risk_policy_id, alert_store=alerts),
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
            recovery=PostgresRecoveryGate(database),
            alerts=alerts,
        )
    except Exception:
        database.close()
        raise


def build_postgres_paper_runtime(
    *,
    config: PlatformConfig,
    adapter: PaperBrokerAdapter,
    identities: PostgresRuntimeIdentityMap,
    policy_selection: PaperPolicySelection,
) -> ConfiguredPostgresPaperRuntime:
    """Build the actual PostgreSQL paper workflow or fail before submission."""
    core = build_postgres_paper_core(
        config=config,
        adapter=adapter,
        identities=identities,
        risk_policy_version=policy_selection.risk_policy_version,
    )
    try:
        risk_policy = core.policies.resolve_risk_policy(policy_selection.risk_policy_version)
        if not risk_policy.per_trade_controls_configured:
            raise PaperRuntimeError("per_trade_risk_policy_required")
        core.policies.resolve_portfolio_policy(policy_selection.portfolio_policy_version)
        scenarios = core.policies.resolve_portfolio_scenarios(
            policy_selection.portfolio_policy_version
        )
        if not core.models.is_approved(policy_selection.model_id):
            raise PaperRuntimeError("selected_model_not_approved")
        paper = PaperRuntime(
            core.oms,
            core.events,
            core.policies,  # type: ignore[arg-type]
            core.assessments,  # type: ignore[arg-type]
            core.instruments,  # type: ignore[arg-type]
            core.signals,  # type: ignore[arg-type]
            core.models,  # type: ignore[arg-type]
            core.execution_evidence,  # type: ignore[arg-type]
            core.quotes,  # type: ignore[arg-type]
            core.return_history,  # type: ignore[arg-type]
            core.promotions,  # type: ignore[arg-type]
            core.reconciled_accounts,
            core.broker,
            policy_selection,
            scenarios,
            core.risk,
            core.kill_switches,
            core.recovery,
        )
        return ConfiguredPostgresPaperRuntime(paper, core)
    except Exception:
        core.close()
        raise
