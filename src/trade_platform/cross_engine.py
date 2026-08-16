"""Independent reconciliation evidence for research and event-driven simulation outputs."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from .corporate_actions import CorporateAction, CorporateActionType
from .domain import OrderSide, utc_now
from .event_backtest import (
    EventDrivenBacktester,
    ExecutionAssumptions,
    MarketEvent,
    SimulatedOrderType,
    create_order,
)
from .paper_execution import PaperOrder, Position
from .research import BacktestResult, CostModel, run_vectorized_backtest


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


class GoldenRunValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GoldenMarketBar:
    """An immutable raw execution quote paired with its total-return research close."""

    occurred_at: datetime
    adjusted_close: Decimal
    bid: Decimal
    ask: Decimal
    available_volume: Decimal
    instrument_id: str | None = None

    def validate(self, instrument_id: str, *, require_single_price: bool = True) -> None:
        if not instrument_id.strip() or (self.instrument_id is not None and not self.instrument_id.strip()) or self.occurred_at.tzinfo is None or self.adjusted_close <= 0:
            raise GoldenRunValidationError("invalid_golden_market_bar")
        # A golden run has deliberately exact close-auction semantics. Any spread,
        # latency, fee or impact belongs in a separate pessimistic event scenario.
        if self.bid <= 0 or self.ask < self.bid or self.available_volume <= 0:
            raise GoldenRunValidationError("invalid_golden_market_quote")
        if require_single_price and self.ask != self.bid:
            raise GoldenRunValidationError("golden_run_requires_single_price_liquid_quote")

    def resolved_instrument_id(self, default_instrument_id: str) -> str:
        return self.instrument_id or default_instrument_id


@dataclass(frozen=True, slots=True)
class GoldenRunReport:
    artifact_id: UUID
    dataset_version: str
    strategy_version: str
    instrument_id: str
    reconciled: bool
    differences: tuple[str, ...]
    vector_final_equity: Decimal
    event_final_equity: Decimal
    final_event_exposure: Decimal
    expected_event_exposure: Decimal
    fill_count: int
    applied_corporate_actions: tuple[str, ...]
    payload_digest: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GoldenExecutionScenario:
    """Versioned event assumptions deliberately separated from vector parity."""

    version: str
    latency: timedelta = timedelta(0)
    maximum_participation: Decimal = Decimal("1")
    commission_bps: Decimal = Decimal("0")
    fixed_fee: Decimal = Decimal("0")
    impact_coefficient: Decimal = Decimal("0")

    def assumptions(self) -> ExecutionAssumptions:
        if not self.version.strip():
            raise GoldenRunValidationError("golden_execution_scenario_requires_version")
        try:
            item = ExecutionAssumptions(self.latency, self.maximum_participation, self.commission_bps, self.fixed_fee, self.impact_coefficient)
            item.validate()
        except ValueError as error:
            raise GoldenRunValidationError("invalid_golden_execution_scenario") from error
        return item

    def non_parity_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.latency:
            reasons.append("latency")
        if self.maximum_participation < Decimal("1"):
            reasons.append("participation_limit")
        if self.commission_bps or self.fixed_fee:
            reasons.append("commission_or_fixed_fee")
        if self.impact_coefficient:
            reasons.append("market_impact")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class RealisticGoldenRunReport:
    """Durable event-realism evidence with raw and explained divergences separate."""

    artifact_id: UUID
    dataset_version: str
    strategy_version: str
    initial_instrument_id: str
    scenario_version: str
    assumptions: dict[str, str]
    exact_parity: bool
    reconciled: bool
    differences: tuple[str, ...]
    explained_by: tuple[str, ...]
    unexplained_differences: tuple[str, ...]
    vector_final_equity: Decimal
    event_final_equity: Decimal
    final_event_exposure: Decimal
    expected_event_exposure: Decimal
    fill_count: int
    partially_filled_orders: int
    working_orders: int
    applied_corporate_actions: tuple[str, ...]
    payload_digest: str
    created_at: datetime


def run_golden_vector_event_reconciliation(
    *, dataset_version: str, strategy_version: str, instrument_id: str, bars: tuple[GoldenMarketBar, ...],
    signals: tuple[Decimal, ...], corporate_actions: tuple[CorporateAction, ...] = (),
) -> GoldenRunReport:
    """Reconcile one transparent close-auction vector run with the event engine.

    The vector side receives total-return-adjusted closes; the event side receives
    raw single-price quotes and applies announced, ingested split/dividend events.
    Orders are submitted at each observed close and filled by a second immutable
    close-auction event at that same timestamp. This explicitly models the
    vector engine's existing close-to-next-close semantics and must never be
    presented as a latency/spread/market-impact execution result.
    """
    if not dataset_version.strip() or not strategy_version.strip() or len(bars) < 2 or len(bars) != len(signals):
        raise GoldenRunValidationError("invalid_golden_run_inputs")
    if any(not Decimal("0") <= signal <= Decimal("1") for signal in signals):
        raise GoldenRunValidationError("golden_run_requires_bounded_long_flat_signals")
    previous_time: datetime | None = None
    for bar in bars:
        bar.validate(instrument_id)
        if previous_time is not None and bar.occurred_at <= previous_time:
            raise GoldenRunValidationError("golden_run_bars_must_be_strictly_chronological")
        previous_time = bar.occurred_at
    action_keys: set[tuple[str, str, int]] = set()
    for action in corporate_actions:
        action.validate()
        key = (action.provider, action.source_identifier, action.revision)
        if key in action_keys or action.instrument_id != instrument_id:
            raise GoldenRunValidationError("invalid_golden_run_corporate_action")
        action_keys.add(key)
        if action.action_type not in {CorporateActionType.SPLIT, CorporateActionType.CASH_DIVIDEND}:
            raise GoldenRunValidationError("golden_run_unsupported_corporate_action")
        if action.announced_at > action.effective_at or action.ingested_at > action.effective_at:
            raise GoldenRunValidationError("golden_run_corporate_action_not_point_in_time_available")
    vector = run_vectorized_backtest([bar.adjusted_close for bar in bars], list(signals), CostModel())
    engine = EventDrivenBacktester(ExecutionAssumptions(maximum_participation=Decimal("1")), initial_cash=Decimal("1"))
    pending_actions = sorted(corporate_actions, key=lambda item: (item.effective_at, item.provider, item.source_identifier, item.revision))
    applied_actions: list[str] = []
    submitted_orders = 0
    for index, bar in enumerate(bars):
        while pending_actions and pending_actions[0].effective_at <= bar.occurred_at:
            action = pending_actions.pop(0)
            engine.apply_corporate_actions([action], as_of=bar.occurred_at)
            applied_actions.append(f"{action.provider}:{action.source_identifier}:{action.revision}")
        event = MarketEvent(instrument_id, bar.occurred_at, bar.bid, bar.ask, bar.available_volume)
        engine.process(event)
        # The last signal has no next period and must not create an unmeasured order.
        if index == len(bars) - 1:
            continue
        result_before_trade = engine.result()
        current_position = next((item for item in result_before_trade.positions if item.instrument_id == instrument_id), Position(instrument_id, Decimal("0"), Decimal("0")))
        equity_before_trade = result_before_trade.cash.amount + current_position.quantity * bar.bid
        desired_quantity = signals[index] * equity_before_trade / bar.bid
        delta = desired_quantity - current_position.quantity
        if delta == 0:
            continue
        if abs(delta) > bar.available_volume:
            raise GoldenRunValidationError("golden_run_insufficient_close_auction_liquidity")
        engine.submit(create_order(instrument_id, OrderSide.BUY if delta > 0 else OrderSide.SELL, SimulatedOrderType.MARKET, abs(delta), bar.occurred_at))
        submitted_orders += 1
        # A second same-timestamp close-auction event is intentional and keeps
        # the event-side order timing identical to the vector convention above.
        engine.process(event)
    if pending_actions:
        raise GoldenRunValidationError("golden_run_corporate_action_after_last_bar")
    event_result = engine.result()
    final_bar = bars[-1]
    final_position = next((item for item in event_result.positions if item.instrument_id == instrument_id), Position(instrument_id, Decimal("0"), Decimal("0")))
    event_final_equity = event_result.cash.amount + final_position.quantity * final_bar.bid
    expected_exposure = signals[-2]
    event_exposure = Decimal("0") if event_final_equity == 0 else final_position.quantity * final_bar.bid / event_final_equity
    differences: list[str] = []
    tolerance = Decimal("0.00000001")
    if abs(vector.equity_curve[-1] - event_final_equity) > tolerance:
        differences.append("final_equity_mismatch")
    if abs(event_exposure - expected_exposure) > tolerance:
        differences.append("final_exposure_mismatch")
    payload = {
        "dataset_version": dataset_version, "strategy_version": strategy_version, "instrument_id": instrument_id,
        "bars": [{"at": item.occurred_at.isoformat(), "adjusted_close": str(item.adjusted_close), "bid": str(item.bid), "ask": str(item.ask), "volume": str(item.available_volume)} for item in bars],
        "signals": [str(item) for item in signals],
        "corporate_actions": [f"{item.provider}:{item.source_identifier}:{item.revision}" for item in corporate_actions],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return GoldenRunReport(uuid4(), dataset_version, strategy_version, instrument_id, not differences, tuple(differences), vector.equity_curve[-1], event_final_equity, event_exposure, expected_exposure, len(event_result.fills), tuple(applied_actions), digest, utc_now())


def run_realistic_golden_vector_event_reconciliation(
    *, dataset_version: str, strategy_version: str, instrument_id: str, bars: tuple[GoldenMarketBar, ...],
    signals: tuple[Decimal, ...], scenario: GoldenExecutionScenario,
    corporate_actions: tuple[CorporateAction, ...] = (),
) -> RealisticGoldenRunReport:
    """Run the same data and strategy through event realism without hiding gaps.

    Vector output remains the no-cost next-period baseline. Event divergence is
    reconciled only when every difference is attributable to persisted execution
    assumptions or an observed unfilled/partial order. Corporate actions must
    still reconcile economically through the supplied adjusted-close series.
    """
    assumptions = scenario.assumptions()
    if not dataset_version.strip() or not strategy_version.strip() or not instrument_id.strip() or len(bars) < 2 or len(bars) != len(signals):
        raise GoldenRunValidationError("invalid_realistic_golden_run_inputs")
    if any(not Decimal("0") <= signal <= Decimal("1") for signal in signals):
        raise GoldenRunValidationError("golden_run_requires_bounded_long_flat_signals")
    previous_time: datetime | None = None
    for bar in bars:
        bar.validate(instrument_id, require_single_price=False)
        if previous_time is not None and bar.occurred_at <= previous_time:
            raise GoldenRunValidationError("golden_run_bars_must_be_strictly_chronological")
        previous_time = bar.occurred_at
    pending_actions = sorted(corporate_actions, key=lambda item: (item.effective_at, item.provider, item.source_identifier, item.revision))
    action_keys: set[tuple[str, str, int]] = set()
    active_instrument_id = instrument_id
    for action in pending_actions:
        action.validate(); key = (action.provider, action.source_identifier, action.revision)
        if key in action_keys or action.instrument_id != active_instrument_id:
            raise GoldenRunValidationError("invalid_golden_run_corporate_action")
        action_keys.add(key)
        if action.action_type not in {CorporateActionType.SPLIT, CorporateActionType.CASH_DIVIDEND, CorporateActionType.SYMBOL_CHANGE}:
            raise GoldenRunValidationError("golden_run_unsupported_corporate_action")
        if action.announced_at > action.effective_at or action.ingested_at > action.effective_at:
            raise GoldenRunValidationError("golden_run_corporate_action_not_point_in_time_available")
        if action.effective_at <= bars[0].occurred_at:
            raise GoldenRunValidationError("golden_run_corporate_action_before_first_bar")
        if action.action_type is CorporateActionType.SYMBOL_CHANGE:
            active_instrument_id = action.successor_instrument_id  # type: ignore[assignment]
    expected_bar_instruments = [instrument_id]
    active_instrument_id = instrument_id
    for index in range(1, len(bars)):
        for action in sorted(corporate_actions, key=lambda item: (item.effective_at, item.provider, item.source_identifier, item.revision)):
            if action.effective_at <= bars[index].occurred_at and action.action_type is CorporateActionType.SYMBOL_CHANGE:
                active_instrument_id = action.successor_instrument_id  # type: ignore[assignment]
        expected_bar_instruments.append(active_instrument_id)
    if any(bar.instrument_id is not None and bar.instrument_id != expected_bar_instruments[index] for index, bar in enumerate(bars)):
        raise GoldenRunValidationError("golden_run_symbol_timeline_mismatch")
    vector = run_vectorized_backtest([bar.adjusted_close for bar in bars], list(signals), CostModel())
    engine = EventDrivenBacktester(assumptions, initial_cash=Decimal("1"))
    applied_actions: list[str] = []
    for index, bar in enumerate(bars):
        current_instrument_id = bar.resolved_instrument_id(instrument_id)
        while pending_actions and pending_actions[0].effective_at <= bar.occurred_at:
            action = pending_actions.pop(0)
            engine.apply_corporate_actions([action], as_of=bar.occurred_at)
            applied_actions.append(f"{action.provider}:{action.source_identifier}:{action.revision}")
        event = MarketEvent(current_instrument_id, bar.occurred_at, bar.bid, bar.ask, bar.available_volume)
        engine.process(event)
        if index == len(bars) - 1:
            continue
        state = engine.result()
        position = next((item for item in state.positions if item.instrument_id == current_instrument_id), Position(current_instrument_id, Decimal("0"), Decimal("0")))
        mark = bar.bid if signals[index] == 0 else bar.ask
        equity = state.cash.amount + position.quantity * mark
        desired_quantity = signals[index] * equity / mark
        delta = desired_quantity - position.quantity
        if delta:
            engine.submit(create_order(current_instrument_id, OrderSide.BUY if delta > 0 else OrderSide.SELL, SimulatedOrderType.MARKET, abs(delta), bar.occurred_at))
            engine.process(event)
    if pending_actions:
        raise GoldenRunValidationError("golden_run_corporate_action_after_last_bar")
    state = engine.result(); final_bar = bars[-1]; final_instrument_id = final_bar.resolved_instrument_id(instrument_id)
    position = next((item for item in state.positions if item.instrument_id == final_instrument_id), Position(final_instrument_id, Decimal("0"), Decimal("0")))
    event_equity = state.cash.amount + position.quantity * final_bar.bid
    expected_exposure = signals[-2]
    exposure = Decimal("0") if event_equity == 0 else position.quantity * final_bar.bid / event_equity
    differences: list[str] = []
    tolerance = Decimal("0.00000001")
    if abs(vector.equity_curve[-1] - event_equity) > tolerance:
        differences.append("final_equity_mismatch")
    if abs(exposure - expected_exposure) > tolerance:
        differences.append("final_exposure_mismatch")
    partial = sum(1 for item in state.orders if item.status.value == "PARTIALLY_FILLED")
    working = sum(1 for item in state.orders if item.status.value in {"PENDING_LATENCY", "WORKING"})
    explanations = list(scenario.non_parity_reasons())
    if any(bar.ask != bar.bid for bar in bars):
        explanations.append("bid_ask_spread")
    if partial:
        explanations.append("partial_fill")
    if working:
        explanations.append("unfilled_order")
    unexplained = tuple(differences) if differences and not explanations else ()
    payload = {
        "dataset_version": dataset_version, "strategy_version": strategy_version, "instrument_id": instrument_id,
        "scenario": {"version": scenario.version, "latency_seconds": str(scenario.latency.total_seconds()), "maximum_participation": str(scenario.maximum_participation), "commission_bps": str(scenario.commission_bps), "fixed_fee": str(scenario.fixed_fee), "impact_coefficient": str(scenario.impact_coefficient)},
        "bars": [{"at": item.occurred_at.isoformat(), "instrument_id": item.resolved_instrument_id(instrument_id), "adjusted_close": str(item.adjusted_close), "bid": str(item.bid), "ask": str(item.ask), "volume": str(item.available_volume)} for item in bars],
        "signals": [str(item) for item in signals], "actions": [f"{item.provider}:{item.source_identifier}:{item.revision}" for item in corporate_actions],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return RealisticGoldenRunReport(uuid4(), dataset_version, strategy_version, instrument_id, scenario.version, payload["scenario"], not differences, not unexplained, tuple(differences), tuple(sorted(set(explanations))), unexplained, vector.equity_curve[-1], event_equity, exposure, expected_exposure, len(state.fills), partial, working, tuple(applied_actions), digest, utc_now())


class SQLiteGoldenRunStore:
    """Append-only durable golden-run evidence; it has no promotion/execution authority."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS golden_run_reports (
            artifact_id TEXT PRIMARY KEY, payload_digest TEXT NOT NULL UNIQUE, dataset_version TEXT NOT NULL,
            strategy_version TEXT NOT NULL, instrument_id TEXT NOT NULL, report_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS realistic_golden_run_reports (
            artifact_id TEXT PRIMARY KEY, payload_digest TEXT NOT NULL UNIQUE, dataset_version TEXT NOT NULL,
            strategy_version TEXT NOT NULL, instrument_id TEXT NOT NULL, report_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
        self._connection.commit()

    def append(self, report: GoldenRunReport) -> GoldenRunReport:
        payload = self._serialize(report)
        try:
            self._connection.execute("INSERT INTO golden_run_reports VALUES (?, ?, ?, ?, ?, ?, ?)", (str(report.artifact_id), report.payload_digest, report.dataset_version, report.strategy_version, report.instrument_id, json.dumps(payload, sort_keys=True), report.created_at.isoformat()))
        except sqlite3.IntegrityError as error:
            raise GoldenRunValidationError("duplicate_golden_run_payload") from error
        self._connection.commit()
        return report

    def get(self, artifact_id: UUID) -> GoldenRunReport:
        row = self._connection.execute("SELECT report_json FROM golden_run_reports WHERE artifact_id = ?", (str(artifact_id),)).fetchone()
        if row is None:
            raise KeyError(str(artifact_id))
        return self._deserialize(json.loads(row[0]))

    def append_realistic(self, report: RealisticGoldenRunReport) -> RealisticGoldenRunReport:
        payload = self._serialize_realistic(report)
        try:
            self._connection.execute("INSERT INTO realistic_golden_run_reports VALUES (?, ?, ?, ?, ?, ?, ?)", (str(report.artifact_id), report.payload_digest, report.dataset_version, report.strategy_version, report.initial_instrument_id, json.dumps(payload, sort_keys=True), report.created_at.isoformat()))
        except sqlite3.IntegrityError as error:
            raise GoldenRunValidationError("duplicate_realistic_golden_run_payload") from error
        self._connection.commit()
        return report

    def get_realistic(self, artifact_id: UUID) -> RealisticGoldenRunReport:
        row = self._connection.execute("SELECT report_json FROM realistic_golden_run_reports WHERE artifact_id = ?", (str(artifact_id),)).fetchone()
        if row is None:
            raise KeyError(str(artifact_id))
        item = json.loads(row[0])
        return RealisticGoldenRunReport(UUID(item["artifact_id"]), item["dataset_version"], item["strategy_version"], item["initial_instrument_id"], item["scenario_version"], item["assumptions"], bool(item["exact_parity"]), bool(item["reconciled"]), tuple(item["differences"]), tuple(item["explained_by"]), tuple(item["unexplained_differences"]), Decimal(item["vector_final_equity"]), Decimal(item["event_final_equity"]), Decimal(item["final_event_exposure"]), Decimal(item["expected_event_exposure"]), int(item["fill_count"]), int(item["partially_filled_orders"]), int(item["working_orders"]), tuple(item["applied_corporate_actions"]), item["payload_digest"], datetime.fromisoformat(item["created_at"]))

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _serialize(report: GoldenRunReport) -> dict[str, object]:
        return {"artifact_id": str(report.artifact_id), "dataset_version": report.dataset_version, "strategy_version": report.strategy_version, "instrument_id": report.instrument_id, "reconciled": report.reconciled, "differences": report.differences, "vector_final_equity": str(report.vector_final_equity), "event_final_equity": str(report.event_final_equity), "final_event_exposure": str(report.final_event_exposure), "expected_event_exposure": str(report.expected_event_exposure), "fill_count": report.fill_count, "applied_corporate_actions": report.applied_corporate_actions, "payload_digest": report.payload_digest, "created_at": report.created_at.isoformat()}

    @staticmethod
    def _deserialize(item: dict[str, object]) -> GoldenRunReport:
        return GoldenRunReport(UUID(str(item["artifact_id"])), str(item["dataset_version"]), str(item["strategy_version"]), str(item["instrument_id"]), bool(item["reconciled"]), tuple(item["differences"]), Decimal(str(item["vector_final_equity"])), Decimal(str(item["event_final_equity"])), Decimal(str(item["final_event_exposure"])), Decimal(str(item["expected_event_exposure"])), int(item["fill_count"]), tuple(item["applied_corporate_actions"]), str(item["payload_digest"]), datetime.fromisoformat(str(item["created_at"])))

    @staticmethod
    def _serialize_realistic(report: RealisticGoldenRunReport) -> dict[str, object]:
        return {"artifact_id": str(report.artifact_id), "dataset_version": report.dataset_version, "strategy_version": report.strategy_version, "initial_instrument_id": report.initial_instrument_id, "scenario_version": report.scenario_version, "assumptions": report.assumptions, "exact_parity": report.exact_parity, "reconciled": report.reconciled, "differences": report.differences, "explained_by": report.explained_by, "unexplained_differences": report.unexplained_differences, "vector_final_equity": str(report.vector_final_equity), "event_final_equity": str(report.event_final_equity), "final_event_exposure": str(report.final_event_exposure), "expected_event_exposure": str(report.expected_event_exposure), "fill_count": report.fill_count, "partially_filled_orders": report.partially_filled_orders, "working_orders": report.working_orders, "applied_corporate_actions": report.applied_corporate_actions, "payload_digest": report.payload_digest, "created_at": report.created_at.isoformat()}


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
