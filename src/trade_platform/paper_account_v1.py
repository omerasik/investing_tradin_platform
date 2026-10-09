"""Phase R10.2 -- the paper account ledger: recorded paper fills under the owner's account policy.

``RESEARCH_ONLY`` / paper only. No broker, no order, no capital. R10 records
policy-neutral unit fills (target -1/0/+1, next strictly later bar open). This
module answers what one paper *account* would have done with those decisions
under its owner-approved :class:`~trade_platform.account_policy_v1.AccountPolicyV1`
and the cycle's OR-6 cost policy: which orders a pre-trade risk check admits,
the money P&L, and the drawdown. It is a deterministic projection of recorded
evidence -- the same fills, signals and policies always give the same ledger
and hash -- so live operation and any later replay agree by construction, and
nothing new is persisted.

It is a paper risk *simulation*, never execution authority. It does not feed
INCUBATING proposals to the execution :class:`~trade_platform.risk.RiskEngine`
(that would label them ``VALIDATED``); it applies the policy's own values,
checked at each signal's recorded decision instant with only evidence known
then:

* sizing: a unit of target is ``paper_order_notional`` (quote currency); a new
  position's quantity is that notional over its fill price;
* allowed symbols, ``maximum_order_notional``, net ``maximum_position_notional``
  per symbol, ``maximum_daily_order_notional`` per UTC day (every executed leg
  counts), ``maximum_leverage`` (gross open notional over equity);
* ``daily_loss_limit`` (equity change since the UTC day's first evaluation) and
  ``maximum_drawdown_limit`` (peak-to-current equity), both blocking new risk;
* ``max_market_age_seconds``: decision instant minus the signal bar's complete
  market-knowledge time;
* controls with no paper-v1 evidence source (spread, expected slippage, event
  risk, data quality, protective stops) are ``NOT_APPLICABLE`` only where the
  owner declared so; a numeric limit for one of them fails the order closed
  (``*_EVIDENCE_UNAVAILABLE_IN_PAPER_V1``).

Only risk-*increasing* legs are checked: closing a position is always allowed.
A rejected opening still executes its closing leg. A decision whose unit fill
was not observed (no proven bar, ambiguous open) changes nothing
(``NOT_FILLED``). Equity marks open positions at the latest paper fill price of
the symbol whose bar opened strictly before the decision -- coarse, causal, and
stated. Costs: each executed leg pays ``notional x (taker fee + scenario) / 1e4``
for every declared OR-6 scenario; without a verified fee schedule the ledger is
gross and non-promotable. Risk checks use the most severe scenario's equity.
Funding is not modelled.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from .account_policy_v1 import NOT_APPLICABLE_V1, STATUS_ACTIVE, AccountPolicyV1
from .strategy_lab_policies_v1 import CostPolicyV1
from .strategy_lab_study_v1 import identity_hash_v1

PAPER_ACCOUNT_SCHEMA_VERSION_V1: Final = "paper-account-ledger-v1"
QUANTITY_QUANTUM_V1: Final = Decimal("1e-12")
MONEY_QUANTUM_V1: Final = Decimal("1e-8")
GROSS: Final = "GROSS"
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_BPS: Final = Decimal(10_000)

#: Policy control -> the reason a numeric (not NOT_APPLICABLE) value fails closed in paper v1.
EVIDENCE_CONTROLS_V1: Final[dict[str, str]] = {
    "maximum_spread_fraction": "SPREAD_EVIDENCE_UNAVAILABLE_IN_PAPER_V1",
    "maximum_expected_slippage_fraction": "SLIPPAGE_EVIDENCE_UNAVAILABLE_IN_PAPER_V1",
    "maximum_event_risk": "EVENT_RISK_EVIDENCE_UNAVAILABLE_IN_PAPER_V1",
    "minimum_data_quality": "DATA_QUALITY_SCORE_UNAVAILABLE_IN_PAPER_V1",
    "maximum_per_trade_loss": "PROTECTIVE_STOP_UNAVAILABLE_STRATEGIES_EMIT_NO_STOP",
}


class PaperAccountError(ValueError):
    """Raised when the ledger's inputs cannot be used honestly."""


def _q(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum)


def _text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", "0"} else text


def _at(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


@dataclass
class _Position:
    units: int
    quantity: Decimal  # signed
    entry_price: Decimal
    entry_notional: Decimal  # unsigned


def _scenarios(cost: CostPolicyV1) -> dict[str, Decimal]:
    """Cost bps per side by scenario; ``{GROSS: 0}`` without a verified fee schedule."""
    if cost.fee_schedule is None:
        return {GROSS: Decimal(0)}
    if not cost.slippage_scenarios:
        return {"FEES_ONLY": Decimal(cost.fee_schedule.taker_fee_bps)}
    return {s.name: cost.total_cost_bps_per_side(s.name) for s in cost.slippage_scenarios}


def build_paper_account_ledger_v1(
    policy: AccountPolicyV1, fills: Iterable[Mapping[str, Any]], signals: Mapping[str, Mapping[str, Any]],
    cost_policy: CostPolicyV1, *, as_of: datetime,
) -> dict[str, Any]:
    """The account ledger over recorded R10 fill identities, deterministic and content-addressed.

    ``signals`` maps a signal id to its stored R8 identity (for the bar's market
    knowledge time). Fills decided after ``as_of`` are ignored.
    """
    if policy.status != STATUS_ACTIVE:
        raise PaperAccountError("paper_account_requires_an_active_policy:" + ",".join(policy.unresolved))
    values = policy.values
    unit = Decimal(values["paper_order_notional"])
    capital = Decimal(values["paper_starting_capital"])
    allowed = set(values["allowed_symbols"])
    limits = {name: Decimal(values[name]) for name in (
        "maximum_order_notional", "maximum_position_notional", "maximum_daily_order_notional", "maximum_leverage",
        "daily_loss_limit", "maximum_drawdown_limit")}
    max_age = timedelta(seconds=int(values["max_market_age_seconds"]))
    evidence_failures = [reason for name, reason in EVIDENCE_CONTROLS_V1.items()
                         if values.get(name) not in (None, NOT_APPLICABLE_V1)]
    scenarios = _scenarios(cost_policy)
    severe = max(scenarios, key=lambda name: (scenarios[name], name))
    cutoff = int((as_of.astimezone(UTC) - _EPOCH) / timedelta(microseconds=1))
    ordered = sorted((f for f in fills if int(f["decided_at_micros"]) <= cutoff),
                     key=lambda f: (int(f["decided_at_micros"]), str(f["signal_id"])))

    positions: dict[tuple[str, str], _Position] = {}
    symbol_of: dict[tuple[str, str], str] = {}
    marks: list[tuple[int, str, Decimal]] = []  # (fill bar open micros, symbol, price)
    realized: dict[str, Decimal] = {name: Decimal(0) for name in scenarios}
    leg_notional_by_day: dict[str, Decimal] = {}
    day_start_equity: dict[str, Decimal] = {}
    peak = capital
    orders: list[dict[str, Any]] = []
    rejections: dict[str, int] = {}

    def mark(symbol: str, before: int) -> Decimal | None:
        known = [price for known_at, sym, price in marks if sym == symbol and known_at < before]
        return known[-1] if known else None

    def equity(name: str, before: int) -> Decimal:
        total = capital + realized[name]
        for key, pos in positions.items():
            if pos.units:
                price = mark(symbol_of[key], before)
                if price is not None:
                    total += pos.quantity * (price - pos.entry_price)
        return total

    for fill in ordered:
        key = (str(fill["study_id"]), str(fill["trial_id"]))
        symbol = str(fill["symbol"])
        symbol_of[key] = symbol
        decided = int(fill["decided_at_micros"])
        day = _at(decided).date().isoformat()
        pos = positions.setdefault(key, _Position(0, Decimal(0), Decimal(0), Decimal(0)))
        desired = int(fill["target_to"])
        order: dict[str, Any] = {"signal_id": str(fill["signal_id"]), "study_id": key[0], "trial_id": key[1],
                                 "symbol": symbol, "decided_at": _at(decided).isoformat(),
                                 "account_units_from": pos.units, "target_to": desired, "reasons": []}
        if desired == pos.units:
            orders.append({**order, "decision": "NO_ORDER_ALREADY_AT_TARGET"})
            continue
        equity_now = equity(severe, decided)
        day_start_equity.setdefault(day, equity_now)
        peak = max(peak, equity_now)
        opening_reasons: list[str] = []
        if desired != 0:
            notional = unit * abs(desired)
            gross_open = sum(p.entry_notional for k, p in positions.items() if p.units and k != key)
            net_symbol = sum(p.entry_notional * (1 if p.units > 0 else -1)
                             for k, p in positions.items() if p.units and k != key and symbol_of[k] == symbol)
            closing_notional = pos.entry_notional if pos.units else Decimal(0)
            day_legs = leg_notional_by_day.get(day, Decimal(0)) + closing_notional
            checks = [
                (symbol in allowed, "SYMBOL_NOT_ALLOWED"),
                (notional <= limits["maximum_order_notional"], "ORDER_NOTIONAL_LIMIT"),
                (abs(net_symbol + notional * (1 if desired > 0 else -1)) <= limits["maximum_position_notional"],
                 "POSITION_NOTIONAL_LIMIT"),
                (day_legs + notional <= limits["maximum_daily_order_notional"], "DAILY_ORDER_NOTIONAL_LIMIT"),
                (equity_now > 0 and (gross_open + notional) / equity_now <= limits["maximum_leverage"],
                 "LEVERAGE_LIMIT"),
                (day_start_equity[day] - equity_now < limits["daily_loss_limit"], "DAILY_LOSS_LIMIT_REACHED"),
                (peak - equity_now < limits["maximum_drawdown_limit"], "DRAWDOWN_LIMIT_REACHED"),
            ]
            opening_reasons = [reason for ok, reason in checks if not ok]
            identity = signals.get(order["signal_id"])
            knowledge = None if identity is None else identity.get("evidence", {}).get("complete_market_knowledge_micros")
            if knowledge is None:
                opening_reasons.append("MARKET_AGE_UNASSESSABLE_SIGNAL_EVIDENCE_MISSING")
            elif _at(decided) - _at(int(knowledge)) > max_age:
                opening_reasons.append("STALE_MARKET_DATA")
            opening_reasons.extend(evidence_failures)
        status = str(fill["status"])
        if status != "FILLED":
            orders.append({**order, "decision": "NOT_FILLED", "fill_status": status,
                           "reasons": opening_reasons})
            continue
        price = Decimal(str(fill["fill_price"]))
        opened = int(fill["fill_bar_open_micros"])
        legs: list[dict[str, str]] = []
        if pos.units:  # the closing leg always executes
            leg_notional = abs(pos.quantity) * price
            for name, bps in scenarios.items():
                realized[name] += pos.quantity * (price - pos.entry_price) - leg_notional * bps / _BPS
            leg_notional_by_day[day] = leg_notional_by_day.get(day, Decimal(0)) + leg_notional
            legs.append({"leg": "CLOSE", "quantity": _text(-pos.quantity), "notional": _text(_q(leg_notional,
                                                                                               MONEY_QUANTUM_V1))})
            positions[key] = pos = _Position(0, Decimal(0), Decimal(0), Decimal(0))
        decision = "APPROVED_FILLED"
        if desired != 0:
            if opening_reasons:
                decision = "OPENING_REJECTED_CLOSE_FILLED" if legs else "REJECTED"
                for reason in opening_reasons:
                    rejections[reason] = rejections.get(reason, 0) + 1
            else:
                notional = unit * abs(desired)
                quantity = _q(notional / price, QUANTITY_QUANTUM_V1) * (1 if desired > 0 else -1)
                for name, bps in scenarios.items():
                    realized[name] -= abs(quantity) * price * bps / _BPS
                leg_notional_by_day[day] = leg_notional_by_day.get(day, Decimal(0)) + abs(quantity) * price
                positions[key] = _Position(desired, quantity, price, abs(quantity) * price)
                legs.append({"leg": "OPEN", "quantity": _text(quantity),
                             "notional": _text(_q(abs(quantity) * price, MONEY_QUANTUM_V1))})
        # The fill price is known from its open's market-knowledge time (not the bar-open instant).
        known_at = fill.get("evidence", {}).get("open_market_knowledge_micros")
        marks.append((int(known_at) if known_at is not None else opened, symbol, price))
        orders.append({**order, "decision": decision, "fill_price": _text(price),
                       "fill_bar_open_at": _at(opened).isoformat(), "legs": legs, "reasons": opening_reasons})

    end = cutoff + 1
    final_equity = {name: _text(_q(equity(name, end), MONEY_QUANTUM_V1)) for name in scenarios}
    open_positions = [{"study_id": k[0], "trial_id": k[1], "symbol": symbol_of[k], "units": p.units,
                       "quantity": _text(p.quantity), "entry_price": _text(p.entry_price)}
                      for k, p in sorted(positions.items()) if p.units]
    identity = {
        "schema_version": PAPER_ACCOUNT_SCHEMA_VERSION_V1,
        "account_id": policy.account.account_id,
        "policy_version_id": str(policy.policy_version_id),
        "policy_content_hash": policy.content_hash,
        "cost_policy_hash": cost_policy.policy().content_hash,
        "cost_mode": cost_policy.mode,
        "risk_scenario": severe,
        "as_of": as_of.astimezone(UTC).isoformat(),
        "sizing": "FIXED_NOTIONAL_PER_UNIT_TARGET",
        "marking": "LATEST_PAPER_FILL_PRICE_OF_THE_SYMBOL_KNOWN_BEFORE_THE_DECISION",
        "not_applicable_controls": list(policy.not_applicable),
        "starting_capital": _text(capital),
        "equity_by_scenario": final_equity,
        "realized_by_scenario": {n: _text(_q(v, MONEY_QUANTUM_V1)) for n, v in realized.items()},
        "open_positions": open_positions,
        "orders": orders,
        "rejections_by_reason": dict(sorted(rejections.items())),
        "funding": "NOT_MODELLED",
        "cost_complete": False,
        "promotable": cost_policy.promotable_cost_basis,
        "claim": "PAPER_ACCOUNT_SIMULATION_NOT_EXECUTION_AUTHORITY",
    }
    return {**identity, "content_hash": identity_hash_v1(identity)}


def summarize_ledger_v1(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Counts for terminal display (the full ledger stays the evidence)."""
    decisions: dict[str, int] = {}
    for order in ledger["orders"]:
        decisions[order["decision"]] = decisions.get(order["decision"], 0) + 1
    return {"decisions": dict(sorted(decisions.items())), "rejections_by_reason": ledger["rejections_by_reason"],
            "equity_by_scenario": ledger["equity_by_scenario"], "open_positions": len(ledger["open_positions"])}


def cost_policy_from_payload_v1(payload: Mapping[str, Any] | None) -> CostPolicyV1:
    """Rebuild a stored OR-6 payload exactly, or fail closed (gross never stands in for a corrupt one)."""
    from .strategy_lab_policies_v1 import FeeScheduleV1, SlippageScenarioV1, gross_cost_policy_v1

    if payload is None:
        return gross_cost_policy_v1()
    fees = payload.get("venue_fees")
    policy = CostPolicyV1(fee_schedule=None if fees is None else FeeScheduleV1(**fees),
                          slippage_scenarios=tuple(SlippageScenarioV1(**s) for s in payload.get("slippage_scenarios", [])),
                          fill_liquidity=str(payload.get("fill_liquidity", "TAKER")))
    if policy.policy().payload != dict(payload):
        raise PaperAccountError("stored_cost_policy_does_not_rebuild_exactly")
    return policy


def cycle_cost_policy_v1(cursor: Any, cycle_id: str) -> CostPolicyV1:
    """The OR-6 policy of the cycle's opened (AUTHORIZED) preregistration; gross while none is opened."""
    import json

    cursor.execute("SELECT p.identity FROM strategy_lab_holdout_openings o JOIN strategy_lab_preregistrations p "
                   "ON p.preregistration_hash = o.preregistration_hash WHERE o.cycle_id=%s", (cycle_id,))
    row = cursor.fetchone()
    if row is None:
        return cost_policy_from_payload_v1(None)
    identity = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return cost_policy_from_payload_v1(identity.get("cost_policy"))


def read_paper_account_v1(cursor: Any, account_id: str, *, cycle_id: str, as_of: datetime) -> dict[str, Any]:
    """The ledger of one account's latest ACTIVE policy over every recorded paper fill (read-only)."""
    import json

    from .account_policy_v1 import AccountContextV1, AccountKindV1

    cursor.execute("SELECT kind, display_name, base_currency FROM account_contexts WHERE account_id=%s", (account_id,))
    row = cursor.fetchone()
    if row is None:
        raise PaperAccountError("account_not_registered")
    account = AccountContextV1(account_id, AccountKindV1(str(row[0])), str(row[1]), str(row[2]))
    cursor.execute("SELECT identity FROM account_policy_versions WHERE account_id=%s AND status='ACTIVE' "
                   "ORDER BY recorded_at DESC, policy_version_id LIMIT 1", (account_id,))
    row = cursor.fetchone()
    if row is None:
        raise PaperAccountError("BLOCKED_OWNER_DECISION_OR_11:NO_ACTIVE_ACCOUNT_POLICY")
    stored = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    policy = AccountPolicyV1(account, stored["values"], stored["approved_by"], stored["approved_on"])
    if policy.identity() != stored:
        raise PaperAccountError("stored_account_policy_does_not_reproduce")
    cursor.execute("SELECT content_hash, identity FROM paper_incubation_fills ORDER BY decided_at, signal_id")
    fill_rows = cursor.fetchall()
    cursor.execute("SELECT s.signal_id, s.identity FROM live_strategy_signals s JOIN paper_incubation_fills f "
                   "ON f.signal_id = s.signal_id")
    fills, signals = fills_signals_from_rows(fill_rows, cursor.fetchall())
    return build_paper_account_ledger_v1(policy, fills, signals, cycle_cost_policy_v1(cursor, cycle_id), as_of=as_of)


def fills_signals_from_rows(fill_rows: Sequence[Any], signal_rows: Sequence[Any]) -> tuple[list[Any], dict[str, Any]]:
    """Stored ``identity`` JSON of fills and signals, re-derived against their content hashes."""
    import json

    fills = []
    for content_hash, raw in fill_rows:
        identity = raw if isinstance(raw, dict) else json.loads(raw)
        if identity_hash_v1(identity) != str(content_hash).strip():
            raise PaperAccountError("stored_fill_identity_does_not_rederive")
        fills.append(identity)
    signals = {}
    for signal_id, raw in signal_rows:
        signals[str(signal_id)] = raw if isinstance(raw, dict) else json.loads(raw)
    return fills, signals


__all__ = [
    "EVIDENCE_CONTROLS_V1",
    "GROSS",
    "PAPER_ACCOUNT_SCHEMA_VERSION_V1",
    "PaperAccountError",
    "build_paper_account_ledger_v1",
    "cost_policy_from_payload_v1",
    "cycle_cost_policy_v1",
    "fills_signals_from_rows",
    "read_paper_account_v1",
    "summarize_ledger_v1",
]
