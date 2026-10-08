"""Phase R10 -- the policy-neutral paper incubation report (pure ``Decimal``, import-light).

Split from :mod:`trade_platform.paper_incubation_v1` so the protected API and its
read models can build the report without the live-bar and strategy stack (the
runtime image carries no numerical libraries). The rules are documented there:
unit exposure (OR-11), gross unless a verified fee schedule (OR-6), funding not
modelled, a restart-safe position chain, incubation length UNRESOLVED (OR-7).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from .strategy_lab_policies_v1 import CostPolicyV1

SIZING_UNIT_EXPOSURE_PENDING_OR_11: Final = "UNIT_EXPOSURE_PENDING_OR_11"
FILL_STATUS_FILLED: Final = "FILLED"
FILL_STATUS_BAR_NOT_OBSERVED: Final = "FILL_BAR_NOT_OBSERVED"
FILL_STATUS_OPEN_AMBIGUOUS: Final = "FILL_OPEN_SEQUENCE_AMBIGUOUS"
FILL_STATUSES: Final = (FILL_STATUS_FILLED, FILL_STATUS_BAR_NOT_OBSERVED, FILL_STATUS_OPEN_AMBIGUOUS)
RETURN_QUANTUM_V1: Final = Decimal("1e-12")
UNRESOLVED_OR7_INCUBATION_DAYS: Final = "UNRESOLVED_OR7_INCUBATION_DAYS"

_MINUTE: Final = 60_000_000
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class PaperIncubationError(ValueError):
    """Raised when a signal, a fill or a report cannot be established honestly."""


def _micros(value: datetime) -> int:
    if value.tzinfo is None:
        raise PaperIncubationError("instant_must_be_timezone_aware")
    return (value - _EPOCH) // timedelta(microseconds=1)


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


__all__ = [
    "FILL_STATUSES",
    "FILL_STATUS_BAR_NOT_OBSERVED",
    "FILL_STATUS_FILLED",
    "FILL_STATUS_OPEN_AMBIGUOUS",
    "RETURN_QUANTUM_V1",
    "SIZING_UNIT_EXPOSURE_PENDING_OR_11",
    "UNRESOLVED_OR7_INCUBATION_DAYS",
    "PaperIncubationError",
    "incubation_report_v1",
]
