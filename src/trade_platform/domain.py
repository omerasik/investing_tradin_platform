"""Canonical, dependency-free contracts shared by research, risk and paper execution."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    FOREX = "FOREX"
    COMMODITY = "COMMODITY"
    CRYPTO = "CRYPTO"


class SignalStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    BLOCKED_BY_RISK = "BLOCKED_BY_RISK"
    BLOCKED_BY_DATA = "BLOCKED_BY_DATA"
    EXPIRED = "EXPIRED"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PROPOSED = "PROPOSED"
    RISK_PENDING = "RISK_PENDING"
    RISK_REJECTED = "RISK_REJECTED"
    APPROVED = "APPROVED"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class RiskDecisionType(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class DataProcessingStatus(str, Enum):
    RAW = "RAW"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    symbol: str
    venue: str
    asset_class: AssetClass
    quote_currency: str
    tick_size: Decimal
    lot_size: Decimal
    delisted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    instrument_id: str
    observed_at: datetime
    bid: Decimal
    ask: Decimal
    last: Decimal
    data_quality_score: Decimal

    @property
    def spread_fraction(self) -> Decimal:
        if self.bid <= 0 or self.ask < self.bid:
            return Decimal("Infinity")
        return (self.ask - self.bid) / self.ask


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    instrument_id: str
    interval: str
    event_at: datetime
    effective_at: datetime
    ingested_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    provider: str
    source_identifier: str
    original_timezone: str
    revision: int = 0
    data_version: str = ""
    quality_score: Decimal = Decimal("0")
    processing_status: DataProcessingStatus = DataProcessingStatus.RAW


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: UUID
    instrument_id: str
    strategy_version: str
    status: SignalStatus
    created_at: datetime
    expires_at: datetime
    data_quality_score: Decimal
    direction: OrderSide
    explanation: str

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= utc_now()


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: UUID
    signal_id: UUID
    instrument_id: str
    account_id: str
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal
    created_at: datetime = field(default_factory=utc_now)

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.limit_price


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    minimum_data_quality: Decimal = Decimal("0.95")
    maximum_spread_fraction: Decimal = Decimal("0.01")
    maximum_order_notional: Decimal = Decimal("10000")
    maximum_position_notional: Decimal = Decimal("25000")
    maximum_daily_order_notional: Decimal = Decimal("50000")
    maximum_event_risk: Decimal = Decimal("0.50")
    maximum_expected_slippage_fraction: Decimal = Decimal("0.02")
    max_market_age_seconds: int = 60


@dataclass(frozen=True, slots=True)
class PortfolioState:
    account_id: str
    position_notionals: Mapping[str, Decimal]
    reconciliation_complete: bool
    risk_increase_allowed: bool = True

    def position_notional(self, instrument_id: str) -> Decimal:
        return self.position_notionals.get(instrument_id, Decimal("0"))


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_id: UUID
    intent_id: UUID
    decision: RiskDecisionType
    reasons: tuple[str, ...]
    decided_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(cls, intent_id: UUID, decision: RiskDecisionType, reasons: list[str]) -> "RiskDecision":
        return cls(decision_id=uuid4(), intent_id=intent_id, decision=decision, reasons=tuple(reasons))
