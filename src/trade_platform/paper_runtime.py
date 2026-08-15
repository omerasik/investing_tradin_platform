"""Fail-closed composition root for a durable, simulated-paper execution runtime."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from .broker_adapter import PaperBrokerAdapter
from .broker_sync import PaperBrokerSyncService, SQLiteBrokerEventStore
from .config import PlatformConfig
from .execution_evidence import SQLiteExecutionEvidenceStore
from .instruments import SQLiteInstrumentStore
from .model_registry import SQLiteModelRegistry
from .paper_execution import PaperOrder
from .paper_oms import SQLitePaperOms
from .persistence import PersistenceTarget
from .policy_registry import SQLitePolicyRegistry
from .portfolio_evidence import OmsReconciledAccountStore
from .portfolio_risk import StressScenario
from .pretrade_assessment import PreTradeAssessment, SQLitePreTradeAssessmentStore, assess_pretrade
from .quotes import SQLiteQuoteStore
from .return_history import (
    PortfolioReturnProvider,
    ReturnIngestionRetryPolicy,
    SQLitePortfolioReturnStore,
)
from .signal_engine import SQLiteSignalStore
from .strategy_promotion import SQLitePromotionLedger


class PaperRuntimeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaperPolicySelection:
    risk_policy_version: str
    portfolio_policy_version: str
    model_id: UUID


@dataclass(frozen=True, slots=True)
class PaperAssessmentResult:
    assessment: PreTradeAssessment
    paper_order: PaperOrder | None


@dataclass(slots=True)
class PaperRuntime:
    """Durable components that must be composed together for paper submission."""

    oms: SQLitePaperOms
    events: SQLiteBrokerEventStore
    policies: SQLitePolicyRegistry
    assessments: SQLitePreTradeAssessmentStore
    instruments: SQLiteInstrumentStore
    signals: SQLiteSignalStore
    models: SQLiteModelRegistry
    execution_evidence: SQLiteExecutionEvidenceStore
    quotes: SQLiteQuoteStore
    return_history: SQLitePortfolioReturnStore
    promotions: SQLitePromotionLedger
    reconciled_accounts: OmsReconciledAccountStore
    broker: PaperBrokerSyncService
    policy_selection: PaperPolicySelection
    stress_scenarios: tuple[StressScenario, ...]

    def close(self) -> None:
        self.assessments.close()
        self.promotions.close()
        self.quotes.close()
        self.return_history.close()
        self.execution_evidence.close()
        self.models.close()
        self.signals.close()
        self.instruments.close()
        self.policies.close()
        self.events.close()
        self.oms.close()

    def assess(self, intent, *, observed_at: datetime) -> PreTradeAssessment:
        """Derive every pre-trade input from this runtime's durable authorities."""
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise PaperRuntimeError("assessment_time_must_be_timezone_aware")
        quote = self.quotes.latest_as_of(intent.instrument_id, observed_at)
        account_evidence, exposures = self.reconciled_accounts.projected_exposures_as_of(
            account_id=intent.account_id, intent=intent, decision_at=observed_at,
            instruments=self.instruments, quotes=self.quotes,
        )
        snapshot = self.execution_evidence.snapshot_as_of(intent.instrument_id, intent.side, observed_at)
        try:
            signal = self.signals.risk_signal(intent.signal_id, as_of=observed_at)
            strategy_enabled = self.promotions.strategy_enabled_as_of(signal.strategy_version, observed_at)
        except ValueError:
            strategy_enabled = False
        if any(self.instruments.get(instrument_id).quote_currency != account_evidence.snapshot.cash.currency for instrument_id in account_evidence.snapshot.positions):
            raise PaperRuntimeError("multi_currency_equity_evidence_unavailable")
        equity = account_evidence.snapshot.cash.amount + sum(
            (position.quantity * self.quotes.latest_as_of(instrument_id, observed_at).last for instrument_id, position in account_evidence.snapshot.positions.items()),
            Decimal("0"),
        )
        input_evidence = snapshot.pretrade_input_evidence(
            market_reference=f"{quote.provider}:{quote.source_reference}:{quote.quote_id}", market=quote.market_snapshot(),
            exposure_reference=account_evidence.exposure_reference, exposure_observed_at=account_evidence.snapshot.as_of,
            exposures=exposures,
        )
        portfolio_policy = self.policies.resolve_portfolio_policy(self.policy_selection.portfolio_policy_version)
        requires_history = any(value is not None for value in (portfolio_policy.maximum_var_loss, portfolio_policy.maximum_cvar_loss, portfolio_policy.maximum_drawdown_loss))
        if requires_history:
            return_history = self.return_history.evidence_for_policy(
                intent.account_id, observed_at,
                minimum_observations=portfolio_policy.minimum_historical_observations,
                window_observations=portfolio_policy.return_history_window_observations,
                maximum_age_seconds=portfolio_policy.maximum_return_history_age_seconds,
            )
            input_evidence = replace(
                input_evidence, return_history_reference=return_history.reference,
                return_history_observed_at=return_history.observed_at,
            )
        return assess_pretrade(
            intent=intent, signal_store=self.signals, market=quote.market_snapshot(),
            portfolio_state=self.reconciled_accounts.portfolio_state_as_of(intent.account_id, observed_at),
            instruments=self.instruments, broker_snapshot=account_evidence.snapshot, broker_healthy=account_evidence.healthy,
            models=self.models, model_id=self.policy_selection.model_id, strategy_enabled=strategy_enabled,
            instrument_halted=snapshot.halt.halted, quote_available=True,
            expected_slippage_fraction=snapshot.slippage.expected_fraction, event_risk=snapshot.event.risk,
            projected_exposures=exposures, portfolio_equity=equity, scenario=self.stress_scenarios,
            observed_at=observed_at, assessment_store=self.assessments, input_evidence=input_evidence,
            execution_evidence_store=self.execution_evidence, quote_store=self.quotes,
            reconciled_accounts=self.reconciled_accounts,
            return_history_store=self.return_history,
            risk_policy_version=self.policy_selection.risk_policy_version,
            portfolio_policy_version=self.policy_selection.portfolio_policy_version,
            policy_registry=self.policies,
        )

    def assess_and_submit(self, intent, *, observed_at: datetime) -> PaperAssessmentResult:
        assessment = self.assess(intent, observed_at=observed_at)
        return PaperAssessmentResult(assessment, self.broker.submit_after_assessment(intent, assessment) if assessment.approved else None)

    def ingest_return_history(self, provider: PortfolioReturnProvider, *, account_id: str, start: datetime, end: datetime, actor: str, idempotency_key: str, retry_policy: ReturnIngestionRetryPolicy = ReturnIngestionRetryPolicy()) -> int:
        """Import return evidence through the runtime's only durable return authority.

        This operation has no access to OMS transitions or broker submission. It
        only persists provider-attributed historical risk observations and the
        store's corresponding health/run provenance.
        """
        capabilities = getattr(provider, "capabilities", None)
        if capabilities is not None and not capabilities.supports_account_performance:
            raise PaperRuntimeError("return_provider_account_performance_unsupported")
        if not account_id.strip() or not actor.strip() or not idempotency_key.strip() or start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None:
            raise PaperRuntimeError("invalid_return_history_ingestion_request")
        try:
            return self.return_history.ingest_authorized(provider, account_id, start=start, end=end, actor=actor, idempotency_key=idempotency_key, retry_policy=retry_policy)
        except ValueError as error:
            raise PaperRuntimeError(f"return_history_ingestion_failed:{error}") from error


def build_paper_runtime(
    *,
    config: PlatformConfig,
    database_path: str | Path,
    adapter: PaperBrokerAdapter,
    policy_selection: PaperPolicySelection,
) -> PaperRuntime:
    """Open a paper-only runtime only when policy and keyed-integrity evidence exist."""
    if config.persistence_target is PersistenceTarget.POSTGRES:
        # This legacy constructor owns SQLite repositories.  A PostgreSQL
        # configuration must never pass through it while the dedicated
        # composition root is being completed; an explicit stop is safer than
        # a durable-looking runtime backed by local SQLite authorities.
        raise PaperRuntimeError("postgres_paper_runtime_requires_explicit_composition")
    path = Path(database_path)
    if str(path) == ":memory:":
        raise PaperRuntimeError("durable_paper_database_required")
    if not config.paper_trading_enabled:
        raise PaperRuntimeError("paper_trading_required")
    policies = SQLitePolicyRegistry(path)
    try:
        assessments = config.create_assessment_store(path)
    except Exception:
        policies.close()
        raise
    oms = SQLitePaperOms(path)
    events = SQLiteBrokerEventStore(path)
    instruments = SQLiteInstrumentStore(path)
    signals = SQLiteSignalStore(path)
    models = SQLiteModelRegistry(path)
    execution_evidence = SQLiteExecutionEvidenceStore(path)
    quotes = SQLiteQuoteStore(path)
    return_history = SQLitePortfolioReturnStore(path)
    promotions = SQLitePromotionLedger(path)
    reconciled_accounts = OmsReconciledAccountStore(oms)
    try:
        policies.resolve_risk_policy(policy_selection.risk_policy_version)
        policies.resolve_portfolio_policy(policy_selection.portfolio_policy_version)
        stress_scenarios = policies.resolve_portfolio_scenarios(policy_selection.portfolio_policy_version)
        try:
            model_approved = models.is_approved(policy_selection.model_id)
        except KeyError:
            model_approved = False
        if not model_approved:
            raise PaperRuntimeError("selected_model_not_approved")
        broker = PaperBrokerSyncService(oms, adapter, events, assessment_store=assessments, policy_registry=policies)
    except Exception:
        assessments.close()
        promotions.close()
        quotes.close()
        return_history.close()
        execution_evidence.close()
        models.close()
        signals.close()
        instruments.close()
        policies.close()
        events.close()
        oms.close()
        raise
    return PaperRuntime(oms, events, policies, assessments, instruments, signals, models, execution_evidence, quotes, return_history, promotions, reconciled_accounts, broker, policy_selection, stress_scenarios)
