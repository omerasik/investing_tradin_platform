"""Phase R10 core -- policy-neutral paper incubation of INCUBATING candidates.

``RESEARCH_ONLY`` / paper only. No broker, no order router, no account, no
capital. This turns the R8 signals of candidates that passed a preregistered
holdout (``INCUBATING``) into paper fills on forward T4 evidence and reports
what that evidence shows. Nothing here promotes a candidate: the state stays
``INCUBATING`` and promotion criteria are an owner decision (OR-7).

Fills (:class:`PaperIncubationEngineV1`)
----------------------------------------
A signal decided at ``decided_at`` (venue-knowledge clock, R8) fills at the open
of the next strictly later 1-minute bar -- the minute boundary strictly after
the decision, the Strategy Lab execution convention -- at that bar's first trade
price, in ``Decimal`` quantized to the column scale before hashing (a price the
scale cannot hold exactly is refused). The fill bar must be a proven live bar:

* the expected minute has no proven bar (no trade in it, or a capture gap made
  it unprovable) -> ``FILL_BAR_NOT_OBSERVED``, no fill, never a later bar's open;
* the bar's open is sequence-ambiguous (two first trades at one venue sequence
  with different prices) -> ``FILL_OPEN_SEQUENCE_AMBIGUOUS``, no fill.

Only ``INCUBATING`` signals are incubated; a ``RESEARCH_WATCH`` signal is
refused. Each fill is content-addressed over its signal, its bar evidence and
the cost policy identity. The store keeps one fill per signal; a second,
different fill for the same signal (live vs a later replay of the same bars) is
a parity violation and raises -- the same function serves both.

Economics (:func:`incubation_report_v1`) -- policy-neutral
---------------------------------------------------------
* Sizing is unit exposure (target -1/0/+1) until OR-11 sets capital and sizing:
  returns are per unit of notional, summed, never compounded; no P&L in money.
* Costs follow OR-6 (:class:`~trade_platform.strategy_lab_policies_v1.CostPolicyV1`)
  and are charged on the two sides of every closed round trip: without a
  verified fee schedule the report is ``GROSS_NON_PROMOTABLE`` and states the
  break-even cost per side; with one, a net figure per declared slippage
  scenario. Observed spread is never a slippage model.
* Funding is not modelled and the venue's per-instrument funding interval is not
  assumed: holding time is reported and the evidence is never cost-complete.
* The paper ledger starts flat. The position chain holds only while every fill's
  signal names the previous signal (``previous_signal_id``: one runner, no
  restart in between) and every fill was taken; after any break it resumes only
  at a fill from flat, so round trips are booked only where every position is
  known, with breaks, skipped fills and inconsistencies counted.
* The incubation length is OR-7's: until decided it is ``UNRESOLVED``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .live_signals_v1 import AUTHORITY_INCUBATING, LiveBarV1, LiveSignalV1
from .persistence import PostgresDatabase
from .strategy_lab_policies_v1 import CostPolicyV1
from .strategy_lab_study_v1 import identity_hash_v1

PAPER_FILL_SCHEMA_VERSION_V1: Final = "paper-incubation-fill-v1"
SIZING_UNIT_EXPOSURE_PENDING_OR_11: Final = "UNIT_EXPOSURE_PENDING_OR_11"
FILL_STATUS_FILLED: Final = "FILLED"
FILL_STATUS_BAR_NOT_OBSERVED: Final = "FILL_BAR_NOT_OBSERVED"
FILL_STATUS_OPEN_AMBIGUOUS: Final = "FILL_OPEN_SEQUENCE_AMBIGUOUS"
FILL_STATUSES: Final = (FILL_STATUS_FILLED, FILL_STATUS_BAR_NOT_OBSERVED, FILL_STATUS_OPEN_AMBIGUOUS)
#: ``paper_incubation_fills.fill_price`` is NUMERIC(38,18).
PRICE_QUANTUM_V1: Final = Decimal("1e-18")
RETURN_QUANTUM_V1: Final = Decimal("1e-12")
UNRESOLVED_OR7_INCUBATION_DAYS: Final = "UNRESOLVED_OR7_INCUBATION_DAYS"

_MINUTE: Final = 60_000_000
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.paper_incubation_v1")


class PaperIncubationError(ValueError):
    """Raised when a signal, a fill or a report cannot be established honestly."""


def _micros(value: datetime) -> int:
    if value.tzinfo is None:
        raise PaperIncubationError("instant_must_be_timezone_aware")
    return (value - _EPOCH) // timedelta(microseconds=1)


def expected_fill_bar_open_micros_v1(decided_at: datetime) -> int:
    """The open of the next 1-minute bar strictly later than the decision."""
    return (_micros(decided_at) // _MINUTE + 1) * _MINUTE


def _exact_price(value: Decimal) -> str:
    try:
        quantized = value.quantize(PRICE_QUANTUM_V1)
    except InvalidOperation as error:
        raise PaperIncubationError("fill_price_out_of_column_range") from error
    if quantized != value or quantized <= 0:
        raise PaperIncubationError("fill_price_not_exact_at_column_scale")
    return format(quantized, "f")


@dataclass(frozen=True, slots=True)
class PendingSignalV1:
    """An INCUBATING signal awaiting its fill bar."""

    signal_id: UUID
    signal_content_hash: str
    study_id: UUID
    trial_id: UUID
    symbol: str
    target_from: int
    target_to: int
    decided_at: datetime
    previous_signal_id: str | None

    @classmethod
    def from_signal(cls, signal: LiveSignalV1) -> PendingSignalV1:
        """In memory, for tests and replays; the operator path fills from the *stored* signal."""
        identity = signal.identity
        if identity["authority"] != AUTHORITY_INCUBATING:
            raise PaperIncubationError("only_incubating_signals_are_paper_incubated")
        return cls(signal.signal_id, identity_hash_v1(dict(identity)), UUID(identity["candidate"]["study_id"]),
                   UUID(identity["candidate"]["trial_id"]), str(identity["symbol"]), int(identity["target_from"]),
                   int(identity["target_to"]), signal.decided_at, identity.get("previous_signal_id"))

    @property
    def fill_bar_open_micros(self) -> int:
        return expected_fill_bar_open_micros_v1(self.decided_at)


@dataclass(frozen=True, slots=True)
class PaperFillV1:
    identity: Mapping[str, Any]
    fill_id: UUID
    content_hash: str

    @property
    def status(self) -> str:
        return str(self.identity["status"])


def _fill(pending: PendingSignalV1, status: str, bar: LiveBarV1 | None, cost_policy_hash: str) -> PaperFillV1:
    evidence: dict[str, Any] | None = None
    price: str | None = None
    if bar is not None:
        evidence = {"tier": "T4_FIRST_PARTY_CAPTURE_FORWARD_UNSEALED", "session_id": str(bar.session_id),
                    "bar_open_micros": bar.bar.bar_open_micros, "first_trade_reference": bar.bar.first_trade_reference,
                    "open_market_knowledge_micros": bar.bar.open_market_knowledge_micros,
                    "trade_manifest_hash": bar.bar.trade_manifest_hash}
        if status == FILL_STATUS_FILLED:
            price = _exact_price(bar.bar.open_price)
    identity = {
        "schema_version": PAPER_FILL_SCHEMA_VERSION_V1,
        "signal_id": str(pending.signal_id),
        "signal_content_hash": pending.signal_content_hash,
        "study_id": str(pending.study_id),
        "trial_id": str(pending.trial_id),
        "symbol": pending.symbol,
        "target_from": pending.target_from,
        "target_to": pending.target_to,
        "previous_signal_id": pending.previous_signal_id,
        "decided_at_micros": _micros(pending.decided_at),
        "fill_bar_open_micros": pending.fill_bar_open_micros,
        "status": status,
        "fill_price": price,
        "fill_rule": "next strictly later 1-minute bar open, first trade price",
        "fill_liquidity": "TAKER",
        "sizing": SIZING_UNIT_EXPOSURE_PENDING_OR_11,
        "cost_policy_hash": cost_policy_hash,
        "evidence": evidence,
        "claim": "PAPER_INCUBATION_NOT_VALIDATED",
    }
    content_hash = identity_hash_v1(identity)
    return PaperFillV1(identity, uuid5(_NAMESPACE, f"paper-fill:{content_hash}"), content_hash)


class PaperIncubationEngineV1:
    """Resolves pending INCUBATING signals against completed bars, in bar order per symbol."""

    def __init__(self, cost_policy: CostPolicyV1) -> None:
        self._cost_policy_hash = cost_policy.policy().content_hash
        self._pending: dict[str, list[PendingSignalV1]] = {}

    @property
    def pending(self) -> int:
        return sum(len(items) for items in self._pending.values())

    def add(self, signals: Iterable[PendingSignalV1]) -> None:
        for signal in signals:
            queue = self._pending.setdefault(signal.symbol, [])
            if all(item.signal_id != signal.signal_id for item in queue):
                queue.append(signal)
            queue.sort(key=lambda item: (item.fill_bar_open_micros, str(item.signal_id)))

    def on_bars(self, bars: Sequence[LiveBarV1]) -> list[PaperFillV1]:
        fills: list[PaperFillV1] = []
        for bar in bars:
            queue = self._pending.get(bar.symbol, [])
            opened = bar.bar.bar_open_micros
            while queue and queue[0].fill_bar_open_micros <= opened:
                pending = queue.pop(0)
                if pending.fill_bar_open_micros < opened:
                    # Bars arrive in order: the expected minute was never proven.
                    fills.append(_fill(pending, FILL_STATUS_BAR_NOT_OBSERVED, None, self._cost_policy_hash))
                elif bar.bar.open_is_sequence_ambiguous:
                    fills.append(_fill(pending, FILL_STATUS_OPEN_AMBIGUOUS, bar, self._cost_policy_hash))
                else:
                    fills.append(_fill(pending, FILL_STATUS_FILLED, bar, self._cost_policy_hash))
        return fills


def fill_parity_v1(live: Sequence[PaperFillV1], replay: Sequence[PaperFillV1]) -> list[str]:
    """Signals whose live and replayed fills differ (or exist on one side only)."""
    left = {str(fill.identity["signal_id"]): fill.content_hash for fill in live}
    right = {str(fill.identity["signal_id"]): fill.content_hash for fill in replay}
    return sorted(key for key in left.keys() | right.keys() if left.get(key) != right.get(key))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def incubation_report_v1(fills: Sequence[Mapping[str, Any]], cost_policy: CostPolicyV1, *,
                         as_of: datetime, incubation_days: int | None = None) -> dict[str, Any]:
    """Per candidate and symbol: unit-exposure round trips on the fills' own evidence.

    ``fills`` are fill identities. ``incubation_days`` is OR-7's decision; while it
    is ``None`` the required length is ``UNRESOLVED``. Never promotes.
    """
    policy_hash = cost_policy.policy().content_hash
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for fill in fills:
        if fill["cost_policy_hash"] != policy_hash:
            raise PaperIncubationError("fill_was_recorded_under_another_cost_policy")
        groups.setdefault((str(fill["study_id"]), str(fill["trial_id"]), str(fill["symbol"])), []).append(fill)
    candidates = []
    for (study_id, trial_id, symbol), items in sorted(groups.items()):
        items = sorted(items, key=lambda item: (int(item["decided_at_micros"]), str(item["signal_id"])))
        candidates.append(_candidate_report(study_id, trial_id, symbol, items, cost_policy, as_of, incubation_days))
    return {
        "schema_version": "paper-incubation-report-v1",
        "as_of": as_of.astimezone(UTC).isoformat(),
        "sizing": SIZING_UNIT_EXPOSURE_PENDING_OR_11,
        "cost_mode": cost_policy.mode,
        "cost_policy_hash": policy_hash,
        "funding": "NOT_MODELLED",
        "cost_complete": False,
        "candidates": candidates,
        "state": "INCUBATING",
        "claim": "PAPER_INCUBATION_NOT_VALIDATED",
    }


def _candidate_report(study_id: str, trial_id: str, symbol: str, items: Sequence[Mapping[str, Any]],
                      cost_policy: CostPolicyV1, as_of: datetime,
                      incubation_days: int | None) -> dict[str, Any]:
    # The paper ledger starts flat. The chain of known positions holds only while every
    # fill's signal names the previous one (one runner, no restart in between) and every
    # fill was taken; after a break it resumes only at a fill from flat.
    position, entry_price, entry_micros = 0, Decimal(0), 0
    chain_intact = False
    last_signal: str | None = None
    breaks = skipped = inconsistent = 0
    round_trips: list[dict[str, Any]] = []
    for item in items:
        linked = item.get("previous_signal_id") is not None and item["previous_signal_id"] == last_signal
        last_signal = str(item["signal_id"])
        if chain_intact and (not linked or item["status"] != FILL_STATUS_FILLED):
            breaks += 1
            chain_intact, position = False, 0
        if item["status"] != FILL_STATUS_FILLED:
            continue
        if not chain_intact:
            if int(item["target_from"]) != 0:
                skipped += 1  # the position before this fill is unknown: wait for a flat start
                continue
            chain_intact = True
        if int(item["target_from"]) != position:
            # Impossible on a linked chain of one runner; counted, never booked.
            inconsistent += 1
            chain_intact, position = False, 0
            continue
        price = Decimal(str(item["fill_price"]))
        at = int(item["fill_bar_open_micros"])
        if position != 0:
            gross = (Decimal(position) * (price / entry_price - 1)).quantize(RETURN_QUANTUM_V1)
            round_trips.append({"direction": position, "entry_bar_open_micros": entry_micros,
                                "exit_bar_open_micros": at, "holding_minutes": (at - entry_micros) // _MINUTE,
                                "gross_return": format(gross, "f")})
        position = int(item["target_to"])
        entry_price, entry_micros = price, at
    gross_total = sum((Decimal(trip["gross_return"]) for trip in round_trips), Decimal(0))
    # Costs are charged on the sides of closed round trips (entry and exit, one unit each);
    # an open or broken-off entry has earned no return and is not counted.
    sides = 2 * len(round_trips)
    first = int(items[0]["decided_at_micros"])
    elapsed_days = (_micros(as_of) - first) / Decimal(86_400_000_000)
    report: dict[str, Any] = {
        "study_id": study_id, "trial_id": trial_id, "symbol": symbol,
        "fills": len(items),
        "fills_by_status": {status: sum(1 for item in items if item["status"] == status) for status in FILL_STATUSES},
        "chain_breaks": breaks,
        "fills_skipped_unknown_position": skipped,
        "chain_inconsistencies": inconsistent,
        "open_position": position if chain_intact else None,
        "round_trips": round_trips,
        "gross_return_sum": format(gross_total.quantize(RETURN_QUANTUM_V1), "f"),
        "closed_sides": sides,
        "holding_minutes": sum(trip["holding_minutes"] for trip in round_trips),
        "funding": "NOT_MODELLED",
        "cost_complete": False,
        "elapsed_days": format(elapsed_days.quantize(Decimal("0.001")), "f"),
        "required_days": incubation_days if incubation_days is not None else UNRESOLVED_OR7_INCUBATION_DAYS,
        "state": "INCUBATING",
    }
    if sides:
        # The cost per side at which the summed gross return is exactly zero.
        report["break_even_cost_bps_per_side"] = format((gross_total / sides * 10_000).quantize(Decimal("0.0001")),
                                                        "f")
    if cost_policy.fee_schedule is None:
        report["net"] = "GROSS_NON_PROMOTABLE_NO_VERIFIED_FEE_SCHEDULE"
    else:
        report["net"] = {
            scenario.name: format((gross_total - cost_policy.total_cost_bps_per_side(scenario.name) / 10_000
                                   * sides).quantize(RETURN_QUANTUM_V1), "f")
            for scenario in cost_policy.slippage_scenarios
        } or "NO_SLIPPAGE_SCENARIO_DECLARED"
    return report


# ---------------------------------------------------------------------------
# Persistence (migration 20261009_0060)
# ---------------------------------------------------------------------------


class PostgresPaperIncubationStoreV1:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def record(self, fill: PaperFillV1) -> bool:
        """Insert once per signal; a different fill for a recorded signal is a parity violation."""
        identity = fill.identity
        if identity_hash_v1(dict(identity)) != fill.content_hash:
            raise PaperIncubationError("fill_identity_does_not_rederive")
        price = identity["fill_price"]
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO paper_incubation_fills (fill_id, content_hash, signal_id, signal_content_hash, "
                "study_id, trial_id, symbol, status, target_from, target_to, decided_at, fill_bar_open_at, "
                "fill_price, cost_policy_hash, identity, recorded_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) "
                "ON CONFLICT (signal_id) DO NOTHING RETURNING fill_id",
                (fill.fill_id, fill.content_hash, UUID(identity["signal_id"]), identity["signal_content_hash"],
                 UUID(identity["study_id"]),
                 UUID(identity["trial_id"]), identity["symbol"], identity["status"], identity["target_from"],
                 identity["target_to"], _EPOCH + timedelta(microseconds=int(identity["decided_at_micros"])),
                 _EPOCH + timedelta(microseconds=int(identity["fill_bar_open_micros"])),
                 None if price is None else Decimal(price), identity["cost_policy_hash"],
                 json.dumps(dict(identity), sort_keys=True), datetime.now(UTC)),
            )
            if cursor.fetchone() is not None:
                return True
            cursor.execute("SELECT content_hash FROM paper_incubation_fills WHERE signal_id=%s",
                           (UUID(identity["signal_id"]),))
            row = cursor.fetchone()
        if row is None or str(row[0]).strip() != fill.content_hash:
            raise PaperIncubationError("fill_parity_violation_for_a_recorded_signal")
        return False

    def pending_signals(self, symbol: str) -> list[PendingSignalV1]:
        """INCUBATING signals of ``symbol`` without a recorded fill, oldest decision first."""
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT s.signal_id, s.content_hash, s.study_id, s.trial_id, s.symbol, s.target_from, s.target_to, "
                "s.decided_at, s.identity->>'previous_signal_id' FROM live_strategy_signals s "
                "LEFT JOIN paper_incubation_fills f "
                "ON f.signal_id = s.signal_id WHERE f.signal_id IS NULL AND s.authority = %s AND s.symbol = %s "
                "ORDER BY s.decided_at, s.signal_id", (AUTHORITY_INCUBATING, symbol))
            rows = cursor.fetchall()
        return [PendingSignalV1(r[0], str(r[1]).strip(), r[2], r[3], r[4], int(r[5]), int(r[6]), r[7], r[8])
                for r in rows]

    def fills(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT content_hash, identity FROM paper_incubation_fills "
                           "WHERE (%s::text IS NULL OR symbol = %s) ORDER BY decided_at, signal_id", (symbol, symbol))
            rows = cursor.fetchall()
        out = []
        for content_hash, raw in rows:
            identity = raw if isinstance(raw, dict) else json.loads(raw)
            if identity_hash_v1(identity) != str(content_hash).strip():
                raise PaperIncubationError("stored_fill_identity_does_not_rederive")
            out.append(identity)
        return out


__all__ = [
    "FILL_STATUSES",
    "FILL_STATUS_BAR_NOT_OBSERVED",
    "FILL_STATUS_FILLED",
    "FILL_STATUS_OPEN_AMBIGUOUS",
    "PAPER_FILL_SCHEMA_VERSION_V1",
    "SIZING_UNIT_EXPOSURE_PENDING_OR_11",
    "UNRESOLVED_OR7_INCUBATION_DAYS",
    "PaperFillV1",
    "PaperIncubationEngineV1",
    "PaperIncubationError",
    "PendingSignalV1",
    "PostgresPaperIncubationStoreV1",
    "expected_fill_bar_open_micros_v1",
    "fill_parity_v1",
    "incubation_report_v1",
]
