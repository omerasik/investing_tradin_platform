"""Build risk inputs from durable reconciliation evidence rather than caller assertions."""

from datetime import datetime, timedelta

from .domain import PortfolioState, utc_now
from .paper_execution import Position
from .paper_oms import SQLitePaperOms


def portfolio_state_from_oms_reconciliation(oms: SQLitePaperOms, account_id: str, positions: dict[str, Position], *, maximum_reconciliation_age: timedelta = timedelta(minutes=5), now: datetime | None = None) -> PortfolioState:
    """Only a recent complete OMS reconciliation permits a risk increase."""
    if maximum_reconciliation_age <= timedelta(0):
        raise ValueError("maximum_reconciliation_age_must_be_positive")
    decision_time = now or utc_now()
    reconciliation = oms.latest_reconciliation(account_id)
    current = reconciliation is not None and reconciliation.complete and reconciliation.occurred_at <= decision_time and reconciliation.occurred_at >= decision_time - maximum_reconciliation_age
    return PortfolioState(account_id, {instrument_id: position.quantity * position.average_cost for instrument_id, position in positions.items()}, reconciliation_complete=current, risk_increase_allowed=current)
