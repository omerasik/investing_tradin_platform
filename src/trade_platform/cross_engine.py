"""Independent reconciliation evidence for research and event-driven simulation outputs."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .paper_execution import PaperOrder, Position
from .research import BacktestResult


@dataclass(frozen=True, slots=True)
class CrossEngineReport:
    reconciled: bool
    differences: tuple[str, ...]
    vector_final_equity: Decimal
    event_final_equity: Decimal


@dataclass(frozen=True, slots=True)
class IndependentBarEngineReport:
    """Comparison with an independently implemented close-to-close engine.

    It validates research accounting, not execution quality.  The event-driven
    reconciliation below remains the stronger execution validation boundary.
    """

    reconciled: bool
    differences: tuple[str, ...]
    vector_final_equity: Decimal
    independent_final_equity: Decimal
    vector_turnover: Decimal
    independent_turnover: Decimal


def run_independent_bar_backtest(
    closes: tuple[Decimal, ...], signals: tuple[Decimal, ...], *, fixed_per_turnover: Decimal,
    percentage_per_turnover: Decimal, spread_fraction_per_turnover: Decimal,
) -> tuple[Decimal, Decimal]:
    """Independently account for the documented signal-at-t, return-at-t+1 model."""
    if len(closes) < 2 or len(closes) != len(signals) or any(item <= 0 for item in closes):
        raise ValueError("invalid_independent_bar_inputs")
    if any(item < 0 for item in (fixed_per_turnover, percentage_per_turnover, spread_fraction_per_turnover)):
        raise ValueError("invalid_independent_bar_costs")
    equity, turnover, previous_position = Decimal("1"), Decimal("0"), Decimal("0")
    for index in range(1, len(closes)):
        position = signals[index - 1]
        traded = abs(position - previous_position)
        costs = Decimal("0") if traded == 0 else fixed_per_turnover + traded * (percentage_per_turnover + spread_fraction_per_turnover)
        equity *= Decimal("1") + position * ((closes[index] - closes[index - 1]) / closes[index - 1]) - costs
        turnover += traded
        previous_position = position
    return equity, turnover


def reconcile_independent_bar_engine(
    vector_result: BacktestResult, closes: tuple[Decimal, ...], signals: tuple[Decimal, ...], *,
    fixed_per_turnover: Decimal, percentage_per_turnover: Decimal, spread_fraction_per_turnover: Decimal,
    tolerance: Decimal = Decimal("0.00000001"),
) -> IndependentBarEngineReport:
    independent_equity, independent_turnover = run_independent_bar_backtest(
        closes, signals, fixed_per_turnover=fixed_per_turnover,
        percentage_per_turnover=percentage_per_turnover, spread_fraction_per_turnover=spread_fraction_per_turnover,
    )
    differences: list[str] = []
    if abs(vector_result.equity_curve[-1] - independent_equity) > tolerance:
        differences.append("final_equity_mismatch")
    if abs(vector_result.turnover - independent_turnover) > tolerance:
        differences.append("turnover_mismatch")
    return IndependentBarEngineReport(
        not differences, tuple(differences), vector_result.equity_curve[-1], independent_equity,
        vector_result.turnover, independent_turnover,
    )


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """Engine-neutral fill evidence used by promotion reconciliation."""

    order_key: str
    instrument_id: str
    occurred_at: datetime
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal


def compare_execution_records(
    expected: tuple[ExecutionRecord, ...], actual: tuple[ExecutionRecord, ...], *,
    tolerance: Decimal = Decimal("0.0001"), timestamp_tolerance: timedelta = timedelta(0),
) -> tuple[str, ...]:
    """Compare order/fill timing, prices, quantities and fees without hiding differences."""
    differences: list[str] = []
    if len(expected) != len(actual):
        differences.append("fill_count_mismatch")
    for index, (left, right) in enumerate(zip(expected, actual)):
        prefix = f"fill:{index}"
        if (left.order_key, left.instrument_id, left.side) != (right.order_key, right.instrument_id, right.side):
            differences.append(f"{prefix}:identity_mismatch")
        if abs(left.occurred_at - right.occurred_at) > timestamp_tolerance:
            differences.append(f"{prefix}:timestamp_mismatch")
        if abs(left.quantity - right.quantity) > tolerance:
            differences.append(f"{prefix}:quantity_mismatch")
        if abs(left.price - right.price) > tolerance:
            differences.append(f"{prefix}:price_mismatch")
        if abs(left.fee - right.fee) > tolerance:
            differences.append(f"{prefix}:fee_mismatch")
    return tuple(differences)


def reconcile_engines(vector_result: BacktestResult, event_final_equity: Decimal,
                      expected_positions: dict[str, Decimal], event_positions: dict[str, Position],
                      expected_filled_quantity: Decimal, event_orders: list[PaperOrder],
                      tolerance: Decimal = Decimal("0.0001"), *,
                      expected_executions: tuple[ExecutionRecord, ...] = (),
                      event_executions: tuple[ExecutionRecord, ...] = (),
                      timestamp_tolerance: timedelta = timedelta(0)) -> CrossEngineReport:
    differences: list[str] = []
    if abs(vector_result.equity_curve[-1] - event_final_equity) > tolerance:
        differences.append("final_equity_mismatch")
    filled = sum((order.filled_quantity for order in event_orders), Decimal("0"))
    if abs(filled - expected_filled_quantity) > tolerance:
        differences.append("fill_quantity_mismatch")
    for instrument_id in set(expected_positions) | set(event_positions):
        actual = event_positions.get(instrument_id)
        if actual is None or abs(expected_positions.get(instrument_id, Decimal("0")) - actual.quantity) > tolerance:
            differences.append(f"position_mismatch:{instrument_id}")
    differences.extend(compare_execution_records(expected_executions, event_executions, tolerance=tolerance, timestamp_tolerance=timestamp_tolerance))
    return CrossEngineReport(not differences, tuple(differences), vector_result.equity_curve[-1], event_final_equity)
