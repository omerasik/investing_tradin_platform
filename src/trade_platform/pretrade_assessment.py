"""Non-executing coordinator for checked signal, risk context and portfolio evidence."""

import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from .broker_adapter import BrokerAccountSnapshot
from .domain import (
    MarketSnapshot,
    OrderIntent,
    PortfolioState,
    RiskDecision,
    RiskDecisionType,
    utc_now,
)
from .instruments import SQLiteInstrumentStore
from .model_registry import SQLiteModelRegistry
from .portfolio_risk import (
    PortfolioExposure,
    PortfolioRiskDecision,
    StressResult,
    StressScenario,
    evaluate_portfolio,
)
from .pretrade_context import build_pre_trade_execution_context
from .risk import RiskEngine
from .signal_engine import SignalEngineError, SQLiteSignalStore

if TYPE_CHECKING:
    from .execution_evidence import SQLiteExecutionEvidenceStore
    from .policy_registry import SQLitePolicyRegistry
    from .portfolio_evidence import SQLiteReconciledAccountStore
    from .quotes import SQLiteQuoteStore
    from .return_history import SQLitePortfolioReturnStore


@dataclass(frozen=True, slots=True)
class PreTradeAssessment:
    assessment_id: UUID
    risk_decision: RiskDecision
    portfolio_decision: PortfolioRiskDecision | None
    evidence_block_reason: str | None = None
    assessed_at: datetime = field(default_factory=utc_now)
    risk_policy_version: str = "risk:default"
    portfolio_policy_version: str = "portfolio:default"
    risk_policy_digest: str = ""
    portfolio_policy_digest: str = ""
    input_evidence: "PreTradeInputEvidence | None" = None

    @property
    def approved(self) -> bool:
        return self.evidence_block_reason is None and self.risk_decision.decision.value == "APPROVE" and self.portfolio_decision is not None and self.portfolio_decision.approved

    @property
    def digest(self) -> str:
        portfolio = self.portfolio_decision
        payload = {
            "assessment_id": str(self.assessment_id),
            "risk_decision": {
                "decision_id": str(self.risk_decision.decision_id), "intent_id": str(self.risk_decision.intent_id),
                "decision": self.risk_decision.decision.value, "reasons": self.risk_decision.reasons,
            },
            "portfolio_decision": None if portfolio is None else {
                "approved": portfolio.approved, "reasons": portfolio.reasons, "gross": str(portfolio.gross),
                "net": str(portfolio.net), "stress_loss": str(portfolio.stress_loss),
                "var_loss": None if portfolio.var_loss is None else str(portfolio.var_loss),
                "cvar_loss": None if portfolio.cvar_loss is None else str(portfolio.cvar_loss),
                "drawdown_loss": None if portfolio.drawdown_loss is None else str(portfolio.drawdown_loss),
                "stress_results": [
                    {"scenario": result.scenario, "loss": str(result.loss), "contributions": {name: str(value) for name, value in sorted(result.contributions.items())}}
                    for result in portfolio.stress_results
                ],
            },
            "evidence_block_reason": self.evidence_block_reason,
            "assessed_at": self.assessed_at.isoformat(),
            "risk_policy_version": self.risk_policy_version,
            "portfolio_policy_version": self.portfolio_policy_version,
            "risk_policy_digest": self.risk_policy_digest,
            "portfolio_policy_digest": self.portfolio_policy_digest,
            "input_evidence": None if self.input_evidence is None else self.input_evidence.to_payload(),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def projected_exposure_fingerprint(exposures: list[PortfolioExposure]) -> str:
    """Stable trace token for the exact projected set assessed by portfolio risk."""
    rows = [
        {"instrument_id": item.instrument_id, "notional": str(item.notional), "asset_class": item.asset_class,
         "sector": item.sector, "country": item.country, "currency": item.currency, "broker": item.broker,
         "exchange": item.exchange, "cluster": item.correlation_cluster, "beta": str(item.beta),
         "duration": str(item.duration), "factors": {key: str(value) for key, value in sorted(item.factors.items())}}
        for item in sorted(exposures, key=lambda value: value.instrument_id)
    ]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PreTradeInputEvidence:
    market_reference: str
    market_observed_at: datetime
    halt_reference: str
    halt_observed_at: datetime
    event_reference: str
    event_observed_at: datetime
    slippage_reference: str
    slippage_observed_at: datetime
    exposure_reference: str
    exposure_observed_at: datetime
    projected_exposure_digest: str
    return_history_reference: str = ""
    return_history_observed_at: datetime | None = None

    def to_payload(self) -> dict[str, str | None]:
        return {
            "market_reference": self.market_reference,
            "market_observed_at": self.market_observed_at.isoformat(),
            "halt_reference": self.halt_reference,
            "halt_observed_at": self.halt_observed_at.isoformat(),
            "event_reference": self.event_reference,
            "event_observed_at": self.event_observed_at.isoformat(),
            "slippage_reference": self.slippage_reference,
            "slippage_observed_at": self.slippage_observed_at.isoformat(),
            "exposure_reference": self.exposure_reference,
            "exposure_observed_at": self.exposure_observed_at.isoformat(),
            "projected_exposure_digest": self.projected_exposure_digest,
            "return_history_reference": self.return_history_reference,
            "return_history_observed_at": None if self.return_history_observed_at is None else self.return_history_observed_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "PreTradeInputEvidence":
        try:
            return cls(
                str(payload["market_reference"]), datetime.fromisoformat(str(payload["market_observed_at"])),
                str(payload["halt_reference"]), datetime.fromisoformat(str(payload["halt_observed_at"])),
                str(payload["event_reference"]), datetime.fromisoformat(str(payload["event_observed_at"])),
                str(payload["slippage_reference"]), datetime.fromisoformat(str(payload["slippage_observed_at"])),
                str(payload["exposure_reference"]), datetime.fromisoformat(str(payload["exposure_observed_at"])),
                str(payload["projected_exposure_digest"]),
                str(payload.get("return_history_reference", "")),
                None if payload.get("return_history_observed_at") is None else datetime.fromisoformat(str(payload["return_history_observed_at"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid_persisted_pretrade_input_evidence") from error

    def validate(self, *, observed_at: datetime, market: MarketSnapshot, exposures: list[PortfolioExposure], maximum_age: timedelta, requires_return_history: bool = False) -> None:
        if maximum_age <= timedelta(0) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("invalid_pretrade_evidence_time")
        references = (self.market_reference, self.halt_reference, self.event_reference, self.slippage_reference, self.exposure_reference, self.projected_exposure_digest)
        if any(not value.strip() for value in references):
            raise ValueError("missing_pretrade_input_provenance")
        timestamps = (self.market_observed_at, self.halt_observed_at, self.event_observed_at, self.slippage_observed_at, self.exposure_observed_at)
        if any(value.tzinfo is None or value.utcoffset() is None or value > observed_at or observed_at - value > maximum_age for value in timestamps):
            raise ValueError("stale_or_invalid_pretrade_input_evidence")
        if self.market_observed_at != market.observed_at:
            raise ValueError("market_evidence_timestamp_mismatch")
        if self.projected_exposure_digest != projected_exposure_fingerprint(exposures):
            raise ValueError("projected_exposure_evidence_mismatch")
        if requires_return_history:
            if not self.return_history_reference.strip() or self.return_history_observed_at is None:
                raise ValueError("missing_return_history_provenance")
            if self.return_history_observed_at.tzinfo is None or self.return_history_observed_at.utcoffset() is None or self.return_history_observed_at > observed_at:
                raise ValueError("invalid_return_history_evidence_time")


class SQLitePreTradeAssessmentStore:
    """Append-only, intent-idempotent provenance for combined pre-trade evidence."""

    def __init__(self, database_path: str | Path = ":memory:", *, integrity_key: bytes | None = None) -> None:
        if integrity_key is not None and not integrity_key:
            raise ValueError("invalid_assessment_integrity_key")
        self._integrity_key = integrity_key
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS pretrade_assessments (
            assessment_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL UNIQUE,
            risk_decision_id TEXT NOT NULL, risk_decision TEXT NOT NULL, risk_reasons_json TEXT NOT NULL,
            portfolio_approved INTEGER, portfolio_reasons_json TEXT, gross TEXT, net TEXT, stress_loss TEXT,
            var_loss TEXT, cvar_loss TEXT, drawdown_loss TEXT, evidence_block_reason TEXT, assessed_at TEXT NOT NULL,
            risk_policy_version TEXT NOT NULL DEFAULT 'risk:default', portfolio_policy_version TEXT NOT NULL DEFAULT 'portfolio:default',
            risk_policy_digest TEXT NOT NULL DEFAULT '', portfolio_policy_digest TEXT NOT NULL DEFAULT '',
            input_evidence_json TEXT, assessment_digest TEXT NOT NULL DEFAULT '', assessment_mac TEXT NOT NULL DEFAULT '',
            stress_results_json TEXT NOT NULL DEFAULT '[]')""")
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(pretrade_assessments)")}
        if "risk_policy_version" not in columns:
            self._connection.execute("ALTER TABLE pretrade_assessments ADD COLUMN risk_policy_version TEXT NOT NULL DEFAULT 'risk:default'")
        if "portfolio_policy_version" not in columns:
            self._connection.execute("ALTER TABLE pretrade_assessments ADD COLUMN portfolio_policy_version TEXT NOT NULL DEFAULT 'portfolio:default'")
        if "risk_policy_digest" not in columns:
            self._connection.execute("ALTER TABLE pretrade_assessments ADD COLUMN risk_policy_digest TEXT NOT NULL DEFAULT ''")
        if "portfolio_policy_digest" not in columns:
            self._connection.execute("ALTER TABLE pretrade_assessments ADD COLUMN portfolio_policy_digest TEXT NOT NULL DEFAULT ''")
        if "input_evidence_json" not in columns:
            self._connection.execute("ALTER TABLE pretrade_assessments ADD COLUMN input_evidence_json TEXT")
        if "assessment_digest" not in columns:
            self._connection.execute("ALTER TABLE pretrade_assessments ADD COLUMN assessment_digest TEXT NOT NULL DEFAULT ''")
        if "assessment_mac" not in columns:
            self._connection.execute("ALTER TABLE pretrade_assessments ADD COLUMN assessment_mac TEXT NOT NULL DEFAULT ''")
        if "stress_results_json" not in columns:
            self._connection.execute("ALTER TABLE pretrade_assessments ADD COLUMN stress_results_json TEXT NOT NULL DEFAULT '[]'")
        self._connection.commit()

    @property
    def keyed_integrity_enabled(self) -> bool:
        return self._integrity_key is not None

    def _mac(self, digest: str) -> str:
        if self._integrity_key is None:
            return ""
        return hmac.new(self._integrity_key, digest.encode(), hashlib.sha256).hexdigest()

    def append(self, assessment: PreTradeAssessment) -> None:
        portfolio = assessment.portfolio_decision
        try:
            self._connection.execute(
                "INSERT INTO pretrade_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(assessment.assessment_id), str(assessment.risk_decision.intent_id), str(assessment.risk_decision.decision_id), assessment.risk_decision.decision.value, json.dumps(assessment.risk_decision.reasons), None if portfolio is None else int(portfolio.approved), None if portfolio is None else json.dumps(portfolio.reasons), None if portfolio is None else str(portfolio.gross), None if portfolio is None else str(portfolio.net), None if portfolio is None else str(portfolio.stress_loss), None if portfolio is None or portfolio.var_loss is None else str(portfolio.var_loss), None if portfolio is None or portfolio.cvar_loss is None else str(portfolio.cvar_loss), None if portfolio is None or portfolio.drawdown_loss is None else str(portfolio.drawdown_loss), assessment.evidence_block_reason, assessment.assessed_at.isoformat(), assessment.risk_policy_version, assessment.portfolio_policy_version, assessment.risk_policy_digest, assessment.portfolio_policy_digest, None if assessment.input_evidence is None else json.dumps(assessment.input_evidence.to_payload(), sort_keys=True), assessment.digest, self._mac(assessment.digest), json.dumps([{"scenario": result.scenario, "loss": str(result.loss), "contributions": {name: str(value) for name, value in sorted(result.contributions.items())}} for result in (() if portfolio is None else portfolio.stress_results)], sort_keys=True)),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("duplicate_pretrade_assessment_intent") from error
        self._connection.commit()

    def get_for_intent(self, intent_id: UUID) -> PreTradeAssessment | None:
        row = self._connection.execute("SELECT * FROM pretrade_assessments WHERE intent_id = ?", (str(intent_id),)).fetchone()
        if row is None:
            return None
        stress_results = tuple(StressResult(item["scenario"], Decimal(item["loss"]), {name: Decimal(value) for name, value in item["contributions"].items()}) for item in json.loads(row[22]))
        portfolio = None if row[5] is None else PortfolioRiskDecision(bool(row[5]), tuple(json.loads(row[6])), Decimal(row[7]), Decimal(row[8]), Decimal(row[9]), None if row[10] is None else Decimal(row[10]), None if row[11] is None else Decimal(row[11]), None if row[12] is None else Decimal(row[12]), stress_results)
        risk = RiskDecision(UUID(row[2]), UUID(row[1]), RiskDecisionType(row[3]), tuple(json.loads(row[4])), datetime.fromisoformat(row[14]))
        evidence = None if row[19] is None else PreTradeInputEvidence.from_payload(json.loads(row[19]))
        assessment = PreTradeAssessment(UUID(row[0]), risk, portfolio, row[13], datetime.fromisoformat(row[14]), row[15], row[16], row[17], row[18], evidence)
        if assessment.digest != row[20]:
            raise ValueError("pretrade_assessment_digest_mismatch")
        if self._integrity_key is not None and not hmac.compare_digest(self._mac(assessment.digest), row[21]):
            raise ValueError("pretrade_assessment_mac_mismatch")
        return assessment

    def close(self) -> None:
        self._connection.close()


def assess_pretrade(
    *,
    intent: OrderIntent,
    signal_store: SQLiteSignalStore,
    market: MarketSnapshot,
    portfolio_state: PortfolioState,
    instruments: SQLiteInstrumentStore,
    broker_snapshot: BrokerAccountSnapshot | None,
    broker_healthy: bool,
    models: SQLiteModelRegistry,
    model_id: UUID | None,
    strategy_enabled: bool,
    instrument_halted: bool,
    quote_available: bool,
    expected_slippage_fraction: Decimal,
    event_risk: Decimal,
    projected_exposures: list[PortfolioExposure],
    portfolio_equity: Decimal,
    scenario: StressScenario | tuple[StressScenario, ...],
    historical_returns: list[Decimal] | None = None,
    observed_at: datetime,
    maximum_broker_snapshot_age: timedelta = timedelta(minutes=5),
    assessment_store: SQLitePreTradeAssessmentStore | None = None,
    input_evidence: PreTradeInputEvidence | None = None,
    maximum_input_evidence_age: timedelta = timedelta(minutes=5),
    execution_evidence_store: "SQLiteExecutionEvidenceStore | None" = None,
    quote_store: "SQLiteQuoteStore | None" = None,
    reconciled_accounts: "SQLiteReconciledAccountStore | None" = None,
    return_history_store: "SQLitePortfolioReturnStore | None" = None,
    risk_policy_version: str,
    portfolio_policy_version: str,
    policy_registry: "SQLitePolicyRegistry",
    kill_switch_registry: Any | None = None,
) -> PreTradeAssessment:
    """Assess all local evidence without constructing an order or calling a broker adapter."""
    if assessment_store is not None:
        existing = assessment_store.get_for_intent(intent.intent_id)
        if existing is not None:
            return existing

    risk_policy_digest = ""
    portfolio_policy_digest = ""

    def policy_reject(reason: str) -> PreTradeAssessment:
        assessment = PreTradeAssessment(
            uuid4(), RiskDecision(uuid4(), intent.intent_id, RiskDecisionType.REJECT, (reason,), observed_at), None, reason,
            risk_policy_version=risk_policy_version,
            portfolio_policy_version=portfolio_policy_version,
            risk_policy_digest=risk_policy_digest,
            portfolio_policy_digest=portfolio_policy_digest,
            input_evidence=input_evidence,
        )
        if assessment_store is not None:
            assessment_store.append(assessment)
        return assessment

    try:
        risk_document = policy_registry.get("risk", risk_policy_version)
        risk_engine = RiskEngine(
            policy_registry.resolve_risk_policy(risk_policy_version),
            kill_switches=kill_switch_registry,
        )
        risk_policy_digest = risk_document.digest
    except ValueError as error:
        return policy_reject(f"risk_policy_evidence_unavailable:{error}")
    try:
        portfolio_document = policy_registry.get("portfolio", portfolio_policy_version)
        portfolio_policy = policy_registry.resolve_portfolio_policy(portfolio_policy_version)
        portfolio_policy_digest = portfolio_document.digest
    except ValueError as error:
        return policy_reject(f"portfolio_policy_evidence_unavailable:{error}")
    requires_return_history = any(value is not None for value in (
        portfolio_policy.maximum_var_loss, portfolio_policy.maximum_cvar_loss,
        portfolio_policy.maximum_drawdown_loss,
    ))

    def reject(reason: str) -> PreTradeAssessment:
        assessment = PreTradeAssessment(
            uuid4(), risk_engine.reject_unassessable(intent.intent_id, reason), None, reason,
            risk_policy_version=risk_policy_version,
            portfolio_policy_version=portfolio_policy_version,
            risk_policy_digest=risk_policy_digest,
            portfolio_policy_digest=portfolio_policy_digest,
            input_evidence=input_evidence,
        )
        if assessment_store is not None:
            assessment_store.append(assessment)
        return assessment

    if input_evidence is None:
        reason = "pretrade_input_evidence_unavailable"
        return reject(reason)
    try:
        input_evidence.validate(observed_at=observed_at, market=market, exposures=projected_exposures, maximum_age=maximum_input_evidence_age, requires_return_history=requires_return_history)
    except ValueError as error:
        reason = f"pretrade_input_evidence_invalid:{error}"
        return reject(reason)
    if execution_evidence_store is None or quote_store is None or reconciled_accounts is None:
        reason = "stored_execution_evidence_unavailable" if execution_evidence_store is None else "stored_quote_evidence_unavailable" if quote_store is None else "stored_account_evidence_unavailable"
        return reject(reason)
    if requires_return_history and return_history_store is None:
        return reject("stored_return_history_evidence_unavailable")
    try:
        from .execution_evidence import ExecutionEvidenceError
        from .portfolio_evidence import PortfolioEvidenceError
        from .quotes import QuoteDataError

        quote = quote_store.latest_as_of(intent.instrument_id, observed_at)
        stored_market = quote.market_snapshot()
        market_reference = f"{quote.provider}:{quote.source_reference}:{quote.quote_id}"
        if market != stored_market or not quote_available:
            reason = "stored_quote_evidence_invalid:stored_quote_evidence_mismatch"
            return reject(reason)
        try:
            account_evidence, expected_exposures = reconciled_accounts.projected_exposures_as_of(account_id=intent.account_id, intent=intent, decision_at=observed_at, instruments=instruments, quotes=quote_store)
            expected_portfolio_state = reconciled_accounts.portfolio_state_as_of(intent.account_id, observed_at)
        except PortfolioEvidenceError as error:
            evidence_type = "account" if "account" in str(error) else "portfolio"
            reason = f"stored_{evidence_type}_evidence_invalid:{error}"
            return reject(reason)
        if broker_snapshot != account_evidence.snapshot or broker_healthy != account_evidence.healthy:
            reason = "stored_account_evidence_invalid:stored_account_evidence_mismatch"
            return reject(reason)
        if portfolio_state != expected_portfolio_state or projected_exposures != expected_exposures:
            reason = "stored_portfolio_evidence_invalid:stored_portfolio_evidence_mismatch"
            return reject(reason)
        snapshot = execution_evidence_store.snapshot_as_of(intent.instrument_id, intent.side, observed_at)
        expected_evidence = snapshot.pretrade_input_evidence(
            market_reference=market_reference, market=stored_market,
            exposure_reference=account_evidence.exposure_reference, exposure_observed_at=account_evidence.snapshot.as_of,
            exposures=expected_exposures,
        )
        if requires_return_history:
            from .return_history import ReturnHistoryError

            try:
                if return_history_store is None:
                    raise ReturnHistoryError("return_history_store_unavailable")
                return_history = return_history_store.evidence_for_policy(
                    intent.account_id, observed_at,
                    minimum_observations=portfolio_policy.minimum_historical_observations,
                    window_observations=portfolio_policy.return_history_window_observations,
                    maximum_age_seconds=portfolio_policy.maximum_return_history_age_seconds,
                )
            except ReturnHistoryError as error:
                return reject(f"stored_return_history_evidence_invalid:{error}")
            expected_evidence = replace(
                expected_evidence, return_history_reference=return_history.reference,
                return_history_observed_at=return_history.observed_at,
            )
            historical_returns = return_history.returns
        if input_evidence != expected_evidence or instrument_halted != snapshot.halt.halted or event_risk != snapshot.event.risk or expected_slippage_fraction != snapshot.slippage.expected_fraction:
            raise ValueError("stored_execution_evidence_mismatch")
    except QuoteDataError as error:
        reason = f"stored_quote_evidence_invalid:{error}"
        return reject(reason)
    except (ExecutionEvidenceError, ValueError) as error:
        reason = f"stored_execution_evidence_invalid:{error}"
        return reject(reason)
    try:
        signal = signal_store.risk_signal(intent.signal_id, as_of=observed_at)
    except SignalEngineError as error:
        reason = f"detailed_signal_evidence_unavailable:{error}"
        return reject(reason)
    execution = build_pre_trade_execution_context(
        instrument_id=intent.instrument_id, account_id=intent.account_id, observed_at=observed_at,
        instruments=instruments, broker_snapshot=account_evidence.snapshot, broker_healthy=account_evidence.healthy,
        models=models, model_id=model_id, strategy_enabled=strategy_enabled,
        instrument_halted=instrument_halted, quote_available=quote_available,
        expected_slippage_fraction=expected_slippage_fraction, event_risk=event_risk,
        maximum_broker_snapshot_age=maximum_broker_snapshot_age,
    )
    risk_decision = risk_engine.assess(intent, signal, market, portfolio_state, execution)
    portfolio_decision = evaluate_portfolio(expected_exposures, portfolio_equity, scenario, portfolio_policy, historical_returns=historical_returns)
    assessment = PreTradeAssessment(uuid4(), risk_decision, portfolio_decision, risk_policy_version=risk_policy_version, portfolio_policy_version=portfolio_policy_version, risk_policy_digest=risk_policy_digest, portfolio_policy_digest=portfolio_policy_digest, input_evidence=input_evidence)
    if assessment_store is not None:
        assessment_store.append(assessment)
    return assessment
