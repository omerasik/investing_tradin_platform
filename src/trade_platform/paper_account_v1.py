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

* money: every amount (capital, notionals, limits, P&L) is in the account's
  ``base_currency``; an opening in a symbol not quoted in it is refused
  (``SYMBOL_NOT_QUOTED_IN_ACCOUNT_CURRENCY``), so units never mix;
* sizing: a unit of target is ``paper_order_notional``; a new position's
  quantity is that notional over its fill price, rounded toward zero to
  ``QUANTITY_QUANTUM_V1`` -- a quantity of zero opens nothing
  (``OPENING_REFUSED_ZERO_QUANTITY_AFTER_ROUNDING``);
* timeline: a decision and its execution are separate events; between them an
  approved opening is a pending reservation, and a fill price is usable only
  from its open's proven market-knowledge time (a FILLED fill without that proof
  is refused);
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
An executed order moves its (study, trial) position to the order's account
target -- the decided target if its opening was approved, flat otherwise -- so a
rejected opening still executes its closing leg, also of an opening that was
still pending when it was decided. Executions of one instant run in decision
order. The account's intended position is the latest decision's target; a
decision whose unit fill was not observed (no proven bar, ambiguous open)
changes nothing (``NOT_FILLED``). The daily order notional counts the leg that
closes the expected position, pending or held. The drawdown peak is sampled at
every decision and after every execution. Equity marks open positions at the
latest paper fill price of the symbol known strictly before the instant --
coarse, causal, and stated. Costs: each executed leg pays ``notional x (taker fee + scenario) / 1e4``
for every declared OR-6 scenario; without a verified fee schedule the ledger is
gross and non-promotable. Risk checks use the most severe scenario's equity.
Funding is not modelled.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final

from .account_policy_v1 import NOT_APPLICABLE_V1, STATUS_ACTIVE, AccountKindV1, AccountPolicyV1
from .strategy_lab_policies_v1 import CostPolicyV1
from .strategy_lab_study_v1 import identity_hash_v1

PAPER_ACCOUNT_SCHEMA_VERSION_V1: Final = "paper-account-ledger-v1"
QUANTITY_QUANTUM_V1: Final = Decimal("1e-12")
MONEY_QUANTUM_V1: Final = Decimal("1e-8")
GROSS: Final = "GROSS"
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_BPS: Final = Decimal(10_000)
_BAR_MICROS: Final = 60_000_000

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


def _known_at(fill: Mapping[str, Any]) -> int:
    """When the fill's outcome became knowable.

    A price is usable only from its open's proven market-knowledge time; a FILLED
    fill without that proof is refused (never the bar open, which is look-ahead).
    A non-fill with bar evidence (ambiguous open) is known when that open was; one
    with no bar at all (the expected minute was never proven) cannot be decided
    before that minute has ended, so it is known at the bar's close.
    """
    evidence = fill.get("evidence") or {}
    known = evidence.get("open_market_knowledge_micros")
    if known is not None:
        return int(known)
    if str(fill["status"]) == "FILLED":
        raise PaperAccountError("filled_price_without_its_market_knowledge_time")
    return int(fill["fill_bar_open_micros"]) + _BAR_MICROS


def _execute(fill: Mapping[str, Any], item: dict[str, Any], positions: dict[tuple[str, str], _Position],
             realized: dict[str, Decimal], scenarios: Mapping[str, Decimal], orders: list[dict[str, Any]],
             key: tuple[str, str]) -> None:
    """Book one decided order at its fill: close what is held, then open an approved target."""
    pos = positions[key]
    order = item["order"]
    if str(fill["status"]) != "FILLED":
        orders.append({**order, "decision": "NOT_FILLED", "fill_status": str(fill["status"])})
        return
    price = Decimal(str(fill["fill_price"]))
    legs: list[dict[str, str]] = []
    if pos.units:
        leg_notional = abs(pos.quantity) * price
        for name, bps in scenarios.items():
            realized[name] += pos.quantity * (price - pos.entry_price) - leg_notional * bps / _BPS
        legs.append({"leg": "CLOSE", "quantity": _text(-pos.quantity),
                     "notional": _text(_q(leg_notional, MONEY_QUANTUM_V1))})
        positions[key] = pos = _Position(0, Decimal(0), Decimal(0), Decimal(0))
    # Rounded toward zero, so the booked notional never exceeds the approved one.
    quantity = (item["open_notional"] / price).quantize(QUANTITY_QUANTUM_V1, rounding=ROUND_DOWN) * item["sign"]
    if item["approved_open"] and quantity == 0:
        # Never a zero-size position carrying a non-zero target.
        decision = "OPENING_REFUSED_ZERO_QUANTITY_AFTER_ROUNDING"
        order = {**order, "reasons": [*order["reasons"], "ZERO_QUANTITY_AFTER_ROUNDING"]}
    elif item["approved_open"]:
        for name, bps in scenarios.items():
            realized[name] -= abs(quantity) * price * bps / _BPS
        positions[key] = _Position(item["desired"], quantity, price, abs(quantity) * price)
        legs.append({"leg": "OPEN", "quantity": _text(quantity),
                     "notional": _text(_q(abs(quantity) * price, MONEY_QUANTUM_V1))})
        decision = "APPROVED_FILLED"
    elif item["desired"] != 0:
        decision = "OPENING_REJECTED_CLOSE_FILLED" if legs else "OPENING_REJECTED_NOTHING_TO_CLOSE"
    else:
        decision = "APPROVED_FILLED" if legs else "NOTHING_TO_CLOSE"
    orders.append({**order, "decision": decision, "fill_price": _text(price),
                   "fill_bar_open_at": _at(int(fill["fill_bar_open_micros"])).isoformat(), "legs": legs})


def build_paper_account_ledger_v1(
    policy: AccountPolicyV1, fills: Iterable[Mapping[str, Any]], signals: Mapping[str, Mapping[str, Any]],
    cost_policy: CostPolicyV1, *, as_of: datetime,
) -> dict[str, Any]:
    """The account ledger over recorded R10 fill identities, deterministic and content-addressed.

    ``signals`` maps a signal id to its stored R8 identity (for the bar's market
    knowledge time); each is checked against its fill's ``signal_content_hash``.
    Each fill is two events: the *decision* (risk checks, at ``decided_at``)
    and the *execution* (at the fill price's market-knowledge time). A decision
    sees only executions known strictly before it; pending approved orders are
    reserved at their requested notional. Decisions after ``as_of`` are ignored;
    an execution after ``as_of`` stays ``PENDING_EXECUTION``.
    """
    if policy.status != STATUS_ACTIVE:
        raise PaperAccountError("paper_account_requires_an_active_policy:" + ",".join(policy.unresolved))
    if policy.account.kind is not AccountKindV1.PERSONAL_PAPER:
        # A prop firm's own loss/drawdown rules are not enforced here yet: refuse rather than ignore them.
        raise PaperAccountError("prop_account_rules_are_not_enforced_by_paper_ledger_v1")
    values = policy.values
    money_unit = policy.account.base_currency  # every money value and the P&L are in this unit
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
    # (instant, 0 decision | 1 execution, decided, (study, trial, signal), fill). At an equal instant a
    # decision comes first (the execution is not yet known to it); executions of one instant run in
    # decision order, so two orders of one key filled on the same bar book in the order they were decided.
    events: list[tuple[int, int, int, tuple[str, str, str], Mapping[str, Any]]] = []
    seen: set[str] = set()
    for fill in fills:
        decided, known = int(fill["decided_at_micros"]), _known_at(fill)
        if known <= decided:
            raise PaperAccountError("fill_price_known_before_its_decision")
        if str(fill["signal_id"]) in seen:
            raise PaperAccountError("a_signal_has_more_than_one_fill")
        seen.add(str(fill["signal_id"]))
        signal = signals.get(str(fill["signal_id"]))
        if signal is not None and identity_hash_v1(dict(signal)) != str(fill.get("signal_content_hash", "")):
            raise PaperAccountError("stored_signal_does_not_match_its_fill")
        if decided > cutoff:
            continue
        tie = (str(fill["study_id"]), str(fill["trial_id"]), str(fill["signal_id"]))
        events.append((decided, 0, decided, tie, fill))
        if known <= cutoff:
            events.append((known, 1, decided, tie, fill))
    events.sort(key=lambda event: (event[0], event[1], event[2], event[3]))

    positions: dict[tuple[str, str], _Position] = {}
    intended: dict[tuple[str, str], int] = {}
    symbol_of: dict[tuple[str, str], str] = {}
    marks: list[tuple[int, str, Decimal]] = []  # (known at micros, symbol, price)
    realized: dict[str, Decimal] = {name: Decimal(0) for name in scenarios}
    reserved_by_day: dict[str, Decimal] = {}  # decision-day reservations, like the RiskEngine's daily ledger
    day_start_equity: dict[str, Decimal] = {}
    peak = capital
    pending: dict[str, dict[str, Any]] = {}
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

    latest: dict[tuple[str, str], str] = {}  # the key's latest decided order (signal id)

    executed_at: int | None = None  # an instant whose executions are booked but not yet in the peak

    for instant, kind, _decided, tie, fill in events:
        if executed_at is not None and instant != executed_at:
            # Every execution of that instant is booked: sample once, never a half-updated state.
            peak = max(peak, equity(severe, executed_at + 1))
            executed_at = None
        key = (tie[0], tie[1])
        symbol = str(fill["symbol"])
        symbol_of[key] = symbol
        pos = positions.setdefault(key, _Position(0, Decimal(0), Decimal(0), Decimal(0)))
        if kind == 1:
            item = pending.pop(tie[2], None)
            if item is not None:  # None: no account order (already at target, or rejected from flat)
                _execute(fill, item, positions, realized, scenarios, orders, key)
                if latest.get(key) == tie[2]:  # no later decision on this key: the account holds what it holds
                    intended[key] = positions[key].units
            if str(fill["status"]) == "FILLED":
                marks.append((instant, symbol, Decimal(str(fill["fill_price"]))))
            executed_at = instant
            continue
        decided = instant
        day = _at(decided).date().isoformat()
        desired = int(fill["target_to"])
        order: dict[str, Any] = {"signal_id": tie[2], "study_id": key[0], "trial_id": key[1],
                                 "symbol": symbol, "decided_at": _at(decided).isoformat(),
                                 "account_units_from": intended.get(key, pos.units), "target_to": desired,
                                 "reasons": []}
        if desired == intended.get(key, pos.units):
            orders.append({**order, "decision": "NO_ORDER_ALREADY_AT_TARGET"})
            continue
        equity_now = equity(severe, decided)
        day_start_equity.setdefault(day, equity_now)
        peak = max(peak, equity_now)
        reserved = [p for p in pending.values() if p["approved_open"] and p["key"] != key]
        # The leg this order will close at execution: the expected position after any pending order of
        # this key (its reserved opening, or flat), else the position held now.
        same_key = [p for p in pending.values() if p["key"] == key]
        closing_notional = Decimal(0)
        if same_key:
            closing_notional = max(same_key, key=lambda p: (p["decided"], p["signal_id"]))["open_notional"]
        elif pos.units:
            closing_notional = abs(pos.quantity) * (mark(symbol, decided) or pos.entry_price)
        opening_reasons: list[str] = []
        notional = unit * abs(desired)
        if desired != 0:
            gross_open = (sum(p.entry_notional for k, p in positions.items() if p.units and k != key)
                          + sum(p["open_notional"] for p in reserved))
            net_symbol = (sum(p.entry_notional * (1 if p.units > 0 else -1)
                              for k, p in positions.items() if p.units and k != key and symbol_of[k] == symbol)
                          + sum(p["open_notional"] * p["sign"] for p in reserved if p["symbol"] == symbol))
            day_legs = reserved_by_day.get(day, Decimal(0)) + closing_notional
            checks = [
                (symbol in allowed, "SYMBOL_NOT_ALLOWED"),
                # Linear contracts settle P&L in their quote currency: it must be the account's money unit.
                (symbol.endswith(money_unit) and len(symbol) > len(money_unit),
                 "SYMBOL_NOT_QUOTED_IN_ACCOUNT_CURRENCY"),
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
        approved_open = desired != 0 and not opening_reasons
        for reason in opening_reasons:
            rejections[reason] = rejections.get(reason, 0) + 1
        reserved_by_day[day] = reserved_by_day.get(day, Decimal(0)) + closing_notional + (
            notional if approved_open else Decimal(0))
        intended[key] = desired if approved_open else 0
        if desired != 0 and not approved_open and not pos.units and not same_key:
            orders.append({**order, "decision": "REJECTED", "reasons": opening_reasons})  # nothing to execute
            continue
        latest[key] = tie[2]
        pending[tie[2]] = {"key": key, "symbol": symbol, "desired": desired, "approved_open": approved_open,
                           "open_notional": notional if approved_open else Decimal(0), "decided": decided,
                           "signal_id": tie[2], "sign": 1 if desired > 0 else -1,
                           "order": {**order, "reasons": opening_reasons}}

    for item in pending.values():  # decided, execution not yet known at as_of
        orders.append({**item["order"], "decision": "PENDING_EXECUTION"})
    orders.sort(key=lambda o: (o["decided_at"], o["study_id"], o["trial_id"], o["signal_id"]))
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
        "money_unit": money_unit,
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
    try:
        # Every field is read as stored: no default stands in for a missing one.
        fees = payload["venue_fees"]
        policy = CostPolicyV1(
            fee_schedule=None if fees is None else FeeScheduleV1(**fees),
            slippage_scenarios=tuple(SlippageScenarioV1(**s) for s in payload["slippage_scenarios"]),
            fill_liquidity=str(payload["fill_liquidity"]))
    except (KeyError, TypeError, ValueError) as error:
        raise PaperAccountError("stored_cost_policy_does_not_rebuild_exactly") from error
    if policy.policy().payload != dict(payload):
        raise PaperAccountError("stored_cost_policy_does_not_rebuild_exactly")
    return policy


def cycle_cost_policy_v1(cursor: Any, cycle_id: str) -> CostPolicyV1:
    """The OR-6 policy of the cycle's opened (AUTHORIZED) preregistration; gross while none is opened."""
    import json

    cursor.execute("SELECT p.identity FROM strategy_lab_holdout_openings o JOIN strategy_lab_preregistrations p "
                   "ON p.preregistration_hash = o.preregistration_hash WHERE o.cycle_id=%s", (cycle_id,))
    rows = cursor.fetchall()
    if not rows:
        return cost_policy_from_payload_v1(None)  # no opened holdout: gross is the honest state
    if len(rows) != 1:
        raise PaperAccountError("cycle_has_more_than_one_opening")
    identity = rows[0][0] if isinstance(rows[0][0], dict) else json.loads(rows[0][0])
    if identity.get("cost_policy") is None:
        # An opened (AUTHORIZED) packet always carries its verified OR-6 policy; never fall back to gross.
        raise PaperAccountError("opened_preregistration_without_its_cost_policy")
    return cost_policy_from_payload_v1(identity["cost_policy"])


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
    fills, signals = fills_signals_from_rows(*cycle_fill_rows_v1(cursor, cycle_id))
    return build_paper_account_ledger_v1(policy, fills, signals, cycle_cost_policy_v1(cursor, cycle_id), as_of=as_of)


#: A fill belongs to a cycle when its INCUBATING signal's evidence is that cycle's recorded holdout validation.
_CYCLE_FILLS: Final = (
    "FROM paper_incubation_fills f JOIN live_strategy_signals s ON s.signal_id = f.signal_id "
    "JOIN strategy_lab_holdout_validations v ON v.validation_hash = s.identity->>'authority_evidence_hash' "
    "WHERE v.cycle_id = %s AND s.authority = 'INCUBATING'")


def cycle_fill_rows_v1(cursor: Any, cycle_id: str) -> tuple[list[Any], list[Any]]:
    """(fill rows, signal rows) of one research cycle only -- never another cycle's or a fixture's evidence."""
    cursor.execute(f"SELECT f.content_hash, f.identity {_CYCLE_FILLS} ORDER BY f.decided_at, f.signal_id",
                   (cycle_id,))
    fill_rows = cursor.fetchall()
    cursor.execute(f"SELECT s.signal_id, s.content_hash, s.identity {_CYCLE_FILLS} ORDER BY s.signal_id",
                   (cycle_id,))
    return fill_rows, cursor.fetchall()


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
    for signal_id, content_hash, raw in signal_rows:
        identity = raw if isinstance(raw, dict) else json.loads(raw)
        if identity_hash_v1(identity) != str(content_hash).strip():
            raise PaperAccountError("stored_signal_identity_does_not_rederive")
        signals[str(signal_id)] = identity
    return fills, signals


__all__ = [
    "EVIDENCE_CONTROLS_V1",
    "GROSS",
    "PAPER_ACCOUNT_SCHEMA_VERSION_V1",
    "PaperAccountError",
    "build_paper_account_ledger_v1",
    "cost_policy_from_payload_v1",
    "cycle_cost_policy_v1",
    "cycle_fill_rows_v1",
    "fills_signals_from_rows",
    "read_paper_account_v1",
    "summarize_ledger_v1",
]
