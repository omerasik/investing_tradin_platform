"""Fail-closed assembly of risk execution context from local authorities."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from .broker_adapter import BrokerAccountSnapshot
from .instruments import SQLiteInstrumentStore
from .model_registry import SQLiteModelRegistry
from .risk import PreTradeExecutionContext


def build_pre_trade_execution_context(
    *,
    instrument_id: str,
    account_id: str,
    observed_at: datetime,
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
    maximum_broker_snapshot_age: timedelta = timedelta(minutes=5),
) -> PreTradeExecutionContext:
    """Build only from known local records; missing/stale records produce blocking values."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None or maximum_broker_snapshot_age <= timedelta(0):
        raise ValueError("invalid_pre_trade_context_time")
    instrument = instruments.get(instrument_id)
    session_open = instrument.delisted_at is None and instruments.is_trading_session(instrument.venue, observed_at)
    snapshot_current = (
        broker_snapshot is not None
        and broker_snapshot.account_id == account_id
        and broker_snapshot.as_of.tzinfo is not None
        and timedelta(0) <= observed_at - broker_snapshot.as_of <= maximum_broker_snapshot_age
    )
    broker_state_certain = broker_healthy and snapshot_current
    try:
        model_approved = model_id is not None and models.is_approved(model_id)
    except KeyError:
        model_approved = False
    return PreTradeExecutionContext(
        session_open=session_open,
        instrument_halted=instrument_halted,
        quote_available=quote_available,
        expected_slippage_fraction=expected_slippage_fraction,
        event_risk=event_risk,
        available_buying_power=broker_snapshot.buying_power if broker_state_certain and broker_snapshot is not None else Decimal("0"),
        broker_state_certain=broker_state_certain,
        strategy_enabled=strategy_enabled,
        model_approved=model_approved,
    )
