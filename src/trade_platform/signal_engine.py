"""Durable, non-executing signal proposals and their deterministic validation chain."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

from .domain import AssetClass, OrderSide, Signal, SignalStatus, utc_now


class SignalEngineError(ValueError):
    pass


class DetailedSignalStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    BLOCKED_BY_RISK = "BLOCKED_BY_RISK"
    BLOCKED_BY_DATA = "BLOCKED_BY_DATA"
    WAITING_FOR_ENTRY = "WAITING_FOR_ENTRY"
    ACTIVE = "ACTIVE"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ValidationStage(str, Enum):
    DATA = "data"
    STRATEGY = "strategy"
    REGIME = "regime"
    LIQUIDITY = "liquidity"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    BROKER = "broker"
    EXECUTION = "execution"
    USER_POLICY = "user_policy"


@dataclass(frozen=True, slots=True)
class SignalProposal:
    signal_id: UUID
    instrument_id: str
    asset_class: AssetClass
    strategy_version: str
    direction: OrderSide
    entry_low: Decimal
    entry_high: Decimal
    invalidation_level: Decimal
    stop_level: Decimal
    target_logic: str
    expected_holding_period: timedelta
    strength: Decimal
    confidence: Decimal
    expected_return: Decimal
    expected_volatility: Decimal
    expected_downside: Decimal
    risk_reward_estimate: Decimal
    regime: str
    liquidity_score: Decimal
    data_quality_score: Decimal
    news_risk: Decimal
    event_risk: Decimal
    correlation_impact: Decimal
    recommended_quantity: Decimal
    expires_at: datetime
    explanation: str
    contradicting_evidence: tuple[str, ...]
    created_at: datetime

    @classmethod
    def create(cls, **kwargs: object) -> "SignalProposal":
        return cls(signal_id=uuid4(), created_at=utc_now(), **kwargs)  # type: ignore[arg-type]

    def validate(self) -> None:
        text = (self.instrument_id, self.strategy_version, self.target_logic, self.regime, self.explanation)
        if not all(value.strip() for value in text):
            raise SignalEngineError("signal_requires_text_fields")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None or self.expires_at <= self.created_at:
            raise SignalEngineError("signal_requires_future_timezone_aware_expiry")
        if self.entry_low <= 0 or self.entry_high < self.entry_low or self.invalidation_level <= 0 or self.stop_level <= 0:
            raise SignalEngineError("invalid_signal_price_levels")
        if self.expected_holding_period <= timedelta(0) or self.recommended_quantity <= 0:
            raise SignalEngineError("invalid_holding_period_or_quantity")
        fractions = (self.strength, self.confidence, self.liquidity_score, self.data_quality_score, self.news_risk, self.event_risk)
        if any(not Decimal("0") <= value <= Decimal("1") for value in fractions):
            raise SignalEngineError("signal_scores_must_be_unit_interval")
        if self.expected_volatility < 0 or self.expected_downside > 0 or self.risk_reward_estimate < 0:
            raise SignalEngineError("invalid_signal_risk_estimates")


@dataclass(frozen=True, slots=True)
class SignalValidationResult:
    assessment_id: UUID
    signal_id: UUID
    status: DetailedSignalStatus
    passed_stages: tuple[ValidationStage, ...]
    failures: tuple[ValidationStage, ...]
    assessed_at: datetime


@dataclass(frozen=True, slots=True)
class SignalLifecycleEvent:
    event_id: UUID
    signal_id: UUID
    from_status: DetailedSignalStatus | None
    to_status: DetailedSignalStatus
    actor: str
    occurred_at: datetime


_SIGNAL_TRANSITIONS: dict[DetailedSignalStatus, set[DetailedSignalStatus]] = {
    DetailedSignalStatus.CANDIDATE: {DetailedSignalStatus.VALIDATED, DetailedSignalStatus.BLOCKED_BY_DATA, DetailedSignalStatus.BLOCKED_BY_RISK},
    DetailedSignalStatus.VALIDATED: {DetailedSignalStatus.WAITING_FOR_ENTRY, DetailedSignalStatus.EXPIRED, DetailedSignalStatus.INVALIDATED, DetailedSignalStatus.CANCELLED},
    DetailedSignalStatus.WAITING_FOR_ENTRY: {DetailedSignalStatus.ACTIVE, DetailedSignalStatus.EXPIRED, DetailedSignalStatus.INVALIDATED, DetailedSignalStatus.CANCELLED},
    DetailedSignalStatus.ACTIVE: {DetailedSignalStatus.PARTIALLY_FILLED, DetailedSignalStatus.FILLED, DetailedSignalStatus.EXPIRED, DetailedSignalStatus.INVALIDATED, DetailedSignalStatus.CANCELLED},
    DetailedSignalStatus.PARTIALLY_FILLED: {DetailedSignalStatus.FILLED, DetailedSignalStatus.INVALIDATED, DetailedSignalStatus.CANCELLED},
    DetailedSignalStatus.FILLED: {DetailedSignalStatus.CLOSED},
    DetailedSignalStatus.BLOCKED_BY_DATA: set(),
    DetailedSignalStatus.BLOCKED_BY_RISK: set(),
    DetailedSignalStatus.INVALIDATED: set(),
    DetailedSignalStatus.EXPIRED: set(),
    DetailedSignalStatus.CLOSED: set(),
    DetailedSignalStatus.CANCELLED: set(),
}


class SignalEngine:
    """Validates a proposal but intentionally has no order construction or broker interface."""

    def validate(self, proposal: SignalProposal, stage_results: dict[ValidationStage, bool]) -> SignalValidationResult:
        proposal.validate()
        if set(stage_results) != set(ValidationStage):
            raise SignalEngineError("signal_validation_requires_all_stages")
        passed = tuple(stage for stage in ValidationStage if stage_results[stage])
        failures = tuple(stage for stage in ValidationStage if not stage_results[stage])
        if ValidationStage.DATA in failures:
            status = DetailedSignalStatus.BLOCKED_BY_DATA
        elif failures:
            status = DetailedSignalStatus.BLOCKED_BY_RISK
        else:
            status = DetailedSignalStatus.VALIDATED
        return SignalValidationResult(uuid4(), proposal.signal_id, status, passed, failures, utc_now())


class SQLiteSignalStore:
    """Append-only research/paper signal evidence; it cannot update an order or account."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("CREATE TABLE IF NOT EXISTS signals (signal_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, current_status TEXT NOT NULL DEFAULT 'CANDIDATE')")
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(signals)")}
        if "current_status" not in columns:
            self._connection.execute("ALTER TABLE signals ADD COLUMN current_status TEXT NOT NULL DEFAULT 'CANDIDATE'")
        self._connection.execute("CREATE TABLE IF NOT EXISTS signal_validations (assessment_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL, status TEXT NOT NULL, passed_json TEXT NOT NULL, failures_json TEXT NOT NULL, assessed_at TEXT NOT NULL, FOREIGN KEY(signal_id) REFERENCES signals(signal_id))")
        self._connection.execute("CREATE TABLE IF NOT EXISTS signal_lifecycle_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, signal_id TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, actor TEXT NOT NULL, occurred_at TEXT NOT NULL, FOREIGN KEY(signal_id) REFERENCES signals(signal_id))")
        self._connection.commit()

    def append(self, proposal: SignalProposal) -> None:
        proposal.validate()
        payload = {
            name: value.value if isinstance(value, Enum) else str(value) if isinstance(value, (Decimal, timedelta, UUID, datetime)) else list(value) if isinstance(value, tuple) else value
            for name, value in ((name, getattr(proposal, name)) for name in proposal.__dataclass_fields__)
        }
        try:
            self._connection.execute("INSERT INTO signals VALUES (?, ?, ?, ?)", (str(proposal.signal_id), json.dumps(payload, sort_keys=True), proposal.created_at.isoformat(), DetailedSignalStatus.CANDIDATE.value))
        except sqlite3.IntegrityError as error:
            raise SignalEngineError("duplicate_signal") from error
        self._connection.commit()

    def append_validation(self, result: SignalValidationResult) -> None:
        if self._connection.execute("SELECT 1 FROM signals WHERE signal_id = ?", (str(result.signal_id),)).fetchone() is None:
            raise SignalEngineError("unknown_signal")
        try:
            self._connection.execute("INSERT INTO signal_validations VALUES (?, ?, ?, ?, ?, ?)", (str(result.assessment_id), str(result.signal_id), result.status.value, json.dumps([value.value for value in result.passed_stages]), json.dumps([value.value for value in result.failures]), result.assessed_at.isoformat()))
        except sqlite3.IntegrityError as error:
            raise SignalEngineError("duplicate_signal_validation") from error
        self._connection.commit()
        self.transition(result.signal_id, result.status, actor="signal_validation", occurred_at=result.assessed_at)

    def status(self, signal_id: UUID) -> DetailedSignalStatus:
        row = self._connection.execute("SELECT current_status FROM signals WHERE signal_id = ?", (str(signal_id),)).fetchone()
        if row is None:
            raise SignalEngineError("unknown_signal")
        return DetailedSignalStatus(row[0])

    def transition(self, signal_id: UUID, to_status: DetailedSignalStatus, *, actor: str, occurred_at: datetime | None = None) -> SignalLifecycleEvent:
        if not actor.strip():
            raise SignalEngineError("signal_transition_actor_required")
        current = self.status(signal_id)
        if to_status not in _SIGNAL_TRANSITIONS[current]:
            raise SignalEngineError(f"invalid_signal_transition:{current.value}:{to_status.value}")
        timestamp = utc_now() if occurred_at is None else occurred_at
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise SignalEngineError("signal_transition_time_must_be_timezone_aware")
        event = SignalLifecycleEvent(uuid4(), signal_id, current, to_status, actor, timestamp)
        self._connection.execute("INSERT INTO signal_lifecycle_events (event_id, signal_id, from_status, to_status, actor, occurred_at) VALUES (?, ?, ?, ?, ?, ?)", (str(event.event_id), str(signal_id), current.value, to_status.value, actor, timestamp.isoformat()))
        self._connection.execute("UPDATE signals SET current_status = ? WHERE signal_id = ?", (to_status.value, str(signal_id)))
        self._connection.commit()
        return event

    def lifecycle_events(self, signal_id: UUID) -> tuple[SignalLifecycleEvent, ...]:
        self.status(signal_id)
        rows = self._connection.execute("SELECT event_id, signal_id, from_status, to_status, actor, occurred_at FROM signal_lifecycle_events WHERE signal_id = ? ORDER BY sequence", (str(signal_id),)).fetchall()
        return tuple(SignalLifecycleEvent(UUID(row[0]), UUID(row[1]), None if row[2] is None else DetailedSignalStatus(row[2]), DetailedSignalStatus(row[3]), row[4], datetime.fromisoformat(row[5])) for row in rows)

    def risk_signal(self, signal_id: UUID, *, as_of: datetime) -> Signal:
        """Expose only a current detailed `VALIDATED` proposal to the shared risk contract."""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise SignalEngineError("risk_signal_as_of_must_be_timezone_aware")
        row = self._connection.execute("SELECT payload_json, current_status FROM signals WHERE signal_id = ?", (str(signal_id),)).fetchone()
        if row is None:
            raise SignalEngineError("unknown_signal")
        payload = json.loads(row[0])
        if DetailedSignalStatus(row[1]) is not DetailedSignalStatus.VALIDATED:
            raise SignalEngineError("risk_signal_requires_current_validated_status")
        expires_at = datetime.fromisoformat(payload["expires_at"])
        if expires_at <= as_of:
            raise SignalEngineError("risk_signal_is_expired")
        return Signal(signal_id, payload["instrument_id"], payload["strategy_version"], SignalStatus.VALIDATED,
                      datetime.fromisoformat(payload["created_at"]), expires_at, Decimal(payload["data_quality_score"]),
                      OrderSide(payload["direction"]), payload["explanation"])

    def get_validation(self, assessment_id: UUID) -> SignalValidationResult:
        row = self._connection.execute("SELECT * FROM signal_validations WHERE assessment_id = ?", (str(assessment_id),)).fetchone()
        if row is None:
            raise KeyError(str(assessment_id))
        return SignalValidationResult(UUID(row[0]), UUID(row[1]), DetailedSignalStatus(row[2]), tuple(ValidationStage(value) for value in json.loads(row[3])), tuple(ValidationStage(value) for value in json.loads(row[4])), datetime.fromisoformat(row[5]))

    def close(self) -> None:
        self._connection.close()
