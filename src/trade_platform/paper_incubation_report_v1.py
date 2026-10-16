"""Phase R10 -- the policy-neutral paper incubation report (pure ``Decimal``, import-light).

Split from :mod:`trade_platform.paper_incubation_v1` so the protected API and its
read models can build the report without the live-bar and strategy stack (the
runtime image carries no numerical libraries). The rules are documented there:
unit exposure (OR-11), gross unless a verified fee schedule (OR-6), funding not
modelled, a restart-safe position chain.

Incubation length is bound to the preregistration packet (OR-7)
---------------------------------------------------------------
The caller never states how long a candidate must incubate. The authoritative
source is an :class:`IncubationResolutionV1`, issued by
:func:`trade_platform.paper_incubation_registry_v1.resolve_incubation_bindings_v1`
from the *persisted* R6 registry rows of one research cycle (the terminal and
the operator CLI use it). :func:`bind_incubation_v1` remains for callers that
read the stored identities themselves; its bindings are labelled
``CALLER_SUPPLIED_IDENTITIES_NOT_REGISTRY_RESOLVED`` in every report. Either way
each :class:`IncubationBindingV1` is checked here against the stored evidence
chain:

* the frozen preregistration packet identity, whose hash must re-derive;
* the holdout validation identity, whose hash must re-derive and which must
  name that packet, its cycle and its study, and judge the candidate
  ``INCUBATING``;
* the candidate's persisted state events: exactly one ``INCUBATING`` event
  whose evidence is that validation, and no rejection (terminal).

The required length is the packet's ``incubation_days`` only when the packet is
``AUTHORIZED`` (``authorized_by`` and ``authorized_on`` present, no unresolved
reason). Otherwise the requirement is ``UNRESOLVED`` with the packet's reasons
(e.g. ``MISSING_OWNER_AUTHORIZATION``): a value prepared in an unapproved packet
(such as 28 days) is never reported as required, and no default exists. A
packet whose status contradicts its own fields is refused.

Elapsed incubation time is evidence, never a caller input
---------------------------------------------------------
The report's ``as_of`` is only a reading instant: it can shorten the clock,
never lengthen it, and any fill evidence after it is refused (look-ahead).
Elapsed time is the union of *evidenced forward-observation segments*: a
segment is a run of fills chained by their signals' ``previous_signal_id``
(the in-memory link of one running process: no restart in between), spanning
from its first decision to its last observed instant (the fill bar open where the bar
was observed, else the decision). A restart starts a new segment; the gap
between segments is never bridged, and overlapping segments are counted once.
A fill decided before the persisted ``INCUBATING`` instant is refused. The
clock is therefore a deterministic *lower bound*: a quiet but running runner
leaves no persisted evidence between signals and earns no elapsed time.

Lifecycle exit requirements (identified, NOT implemented)
---------------------------------------------------------
Nothing here leaves ``INCUBATING``; ``PROFESSIONALLY_VALIDATED`` is reserved and
unreachable. A window marked ``ELAPSED_EXIT_CRITERIA_OWNER_UNDECIDED`` is
still ``INCUBATING``. Exit needs, at least:

1. an ``AUTHORIZED`` OR-7 packet with ``incubation_days`` and an evidenced
   elapsed clock that reaches it (this module);
2. owner-decided forward pass/fail criteria (metrics, thresholds, minimum
   closed round trips, tolerated chain breaks and unfilled signals) and a
   forward-failure terminal state -- both undecided;
3. a verified OR-6 fee schedule (net, not gross) and forward funding modelled,
   so the evidence is cost-complete;
4. OR-11 sizing and capital;
5. persisted runner liveness evidence (heartbeats / capture coverage), without
   which quiet periods never count toward the window;
6. an explicit, recorded owner authorization of the transition -- never an
   automatic promotion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final

from .strategy_lab_policies_v1 import CostPolicyV1
from .strategy_lab_study_v1 import identity_hash_v1

SIZING_UNIT_EXPOSURE_PENDING_OR_11: Final = "UNIT_EXPOSURE_PENDING_OR_11"
FILL_STATUS_FILLED: Final = "FILLED"
FILL_STATUS_BAR_NOT_OBSERVED: Final = "FILL_BAR_NOT_OBSERVED"
FILL_STATUS_OPEN_AMBIGUOUS: Final = "FILL_OPEN_SEQUENCE_AMBIGUOUS"
FILL_STATUSES: Final = (FILL_STATUS_FILLED, FILL_STATUS_BAR_NOT_OBSERVED, FILL_STATUS_OPEN_AMBIGUOUS)
RETURN_QUANTUM_V1: Final = Decimal("1e-12")
UNRESOLVED_OR7_INCUBATION_DAYS: Final = "UNRESOLVED_OR7_INCUBATION_DAYS"

#: Mirrors of :mod:`trade_platform.strategy_lab_validation_v1` (not imported: that module
#: pulls the numerical stack). A test pins them equal.
STATE_INCUBATING: Final = "INCUBATING"
STATE_HOLDOUT_FAILED_REJECTED: Final = "HOLDOUT_FAILED_REJECTED"
PACKET_STATUS_AUTHORIZED: Final = "AUTHORIZED"
PACKET_STATUS_DRAFT: Final = "DRAFT"
UNRESOLVED_PACKET_INCUBATION: Final = "MISSING_OWNER_INCUBATION_LENGTH_OR_7"
UNRESOLVED_PACKET_AUTHORIZATION: Final = "MISSING_OWNER_AUTHORIZATION"

#: Reserved; this module can never produce it.
STATE_PROFESSIONALLY_VALIDATED_RESERVED: Final = "PROFESSIONALLY_VALIDATED"
UNRESOLVED_NO_BINDING: Final = "NO_AUTHORIZED_PREREGISTRATION_BINDING"
BINDING_SOURCE_REGISTRY_V1: Final = "PERSISTED_PREREGISTRATION_REGISTRY_V1"
BINDING_SOURCE_CALLER_SUPPLIED_V1: Final = "CALLER_SUPPLIED_IDENTITIES_NOT_REGISTRY_RESOLVED"
BINDING_SOURCES_V1: Final = (BINDING_SOURCE_REGISTRY_V1, BINDING_SOURCE_CALLER_SUPPLIED_V1)
WINDOW_UNRESOLVED: Final = "UNRESOLVED"
WINDOW_INCOMPLETE: Final = "INCOMPLETE"
WINDOW_ELAPSED: Final = "ELAPSED_EXIT_CRITERIA_OWNER_UNDECIDED"
ELAPSED_CLOCK_V1: Final = "EVIDENCED_FORWARD_OBSERVATION_SEGMENTS_LOWER_BOUND"

_MINUTE: Final = 60_000_000
_DAY_MICROS: Final = 86_400_000_000
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_BINDING_ISSUER: Final = object()


class PaperIncubationError(ValueError):
    """Raised when a signal, a fill or a report cannot be established honestly."""


def _micros(value: datetime) -> int:
    if value.tzinfo is None:
        raise PaperIncubationError("instant_must_be_timezone_aware")
    return (value - _EPOCH) // timedelta(microseconds=1)


# ---------------------------------------------------------------------------
# Packet binding
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IncubationBindingV1:
    """One candidate's incubation requirement and start, bound to its preregistration packet.

    Issued only by the chain check: through the registry resolver (``source`` =
    :data:`BINDING_SOURCE_REGISTRY_V1`), or through :func:`bind_incubation_v1` from
    identities a caller read (:data:`BINDING_SOURCE_CALLER_SUPPLIED_V1`; kept for
    compatibility, and labelled as such in every report). ``required_days`` is
    ``None`` exactly when ``unresolved`` is non-empty.
    """

    study_id: str
    trial_id: str
    symbol: str
    cycle_id: str
    preregistration_hash: str
    validation_hash: str
    incubating_since_micros: int
    required_days: int | None
    unresolved: tuple[str, ...]
    source: str = ""
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _BINDING_ISSUER:
            raise PaperIncubationError("incubation_binding_is_issued_only_by_bind_incubation_v1")
        if self.source not in BINDING_SOURCES_V1:
            raise PaperIncubationError("incubation_binding_source_unknown")

    @property
    def key(self) -> tuple[Any, ...]:
        return (self.study_id, self.trial_id, self.symbol, self.cycle_id, self.preregistration_hash,
                self.validation_hash, self.incubating_since_micros, self.required_days, self.unresolved,
                self.source)

    @property
    def issued(self) -> bool:
        """False for a ``dataclasses.replace`` copy: it carries the issuer but not an issued identity."""
        return self.key in _ISSUED_BINDINGS

    def payload(self) -> dict[str, Any]:
        return {"preregistration_hash": self.preregistration_hash, "cycle_id": self.cycle_id,
                "validation_hash": self.validation_hash, "incubating_since_micros": self.incubating_since_micros,
                "source": self.source}


_ISSUED_BINDINGS: set[tuple[Any, ...]] = set()


def _required_days(packet: Mapping[str, Any]) -> tuple[int | None, tuple[str, ...]]:
    """The packet's incubation length if it is authorized; otherwise ``UNRESOLVED`` reasons."""
    status = packet.get("status")
    if status not in (PACKET_STATUS_AUTHORIZED, PACKET_STATUS_DRAFT):
        raise PaperIncubationError("preregistration_status_unknown")
    stated = packet.get("unresolved")
    if not isinstance(stated, list) or not all(isinstance(item, str) for item in stated):
        raise PaperIncubationError("preregistration_unresolved_reasons_malformed")
    days = packet.get("incubation_days")
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days < 1):
        raise PaperIncubationError("preregistration_incubation_days_malformed")
    own: list[str] = []
    if days is None:
        own.append(UNRESOLVED_PACKET_INCUBATION)
    by, on = packet.get("authorized_by"), packet.get("authorized_on")
    authorized = isinstance(by, str) and bool(by.strip()) and isinstance(on, str) and bool(on.strip())
    if authorized:
        try:
            date.fromisoformat(str(on))
        except ValueError as error:
            raise PaperIncubationError("preregistration_authorized_on_malformed") from error
    else:
        own.append(UNRESOLVED_PACKET_AUTHORIZATION)
    if any(reason not in stated for reason in own):
        raise PaperIncubationError("preregistration_unresolved_reasons_omit_a_gap")
    if (status == PACKET_STATUS_AUTHORIZED) != (not stated):
        raise PaperIncubationError("preregistration_status_contradicts_its_unresolved_reasons")
    if status != PACKET_STATUS_AUTHORIZED:
        return None, tuple(sorted(set(stated)))
    return days, ()


def bind_incubation_v1(*, packet_identity: Mapping[str, Any], packet_hash: str,
                       validation_identity: Mapping[str, Any], validation_hash: str,
                       states: Sequence[Mapping[str, Any]], trial_id: str) -> IncubationBindingV1:
    """Bind one candidate's incubation to stored identities the *caller* read.

    ``packet_identity`` / ``validation_identity`` are the stored identities
    (``strategy_lab_preregistrations.identity``, ``strategy_lab_holdout_validations.identity``);
    ``states`` are the candidate state rows of the packet's study. Every hash must
    re-derive and every cross-reference must agree, or the binding is refused.
    The chain is self-consistent but not proven to be the persisted one, so the
    binding is labelled :data:`BINDING_SOURCE_CALLER_SUPPLIED_V1` in every report;
    the authoritative path is the registry resolver
    (:mod:`trade_platform.paper_incubation_registry_v1`).
    """
    return _bind_stored_chain_v1(packet_identity=packet_identity, packet_hash=packet_hash,
                                 validation_identity=validation_identity, validation_hash=validation_hash,
                                 states=states, trial_id=trial_id, source=BINDING_SOURCE_CALLER_SUPPLIED_V1)


def _bind_stored_chain_v1(*, packet_identity: Mapping[str, Any], packet_hash: str,
                          validation_identity: Mapping[str, Any], validation_hash: str,
                          states: Sequence[Mapping[str, Any]], trial_id: str, source: str) -> IncubationBindingV1:
    """The chain check behind both binding paths (see :func:`bind_incubation_v1`)."""
    if identity_hash_v1(dict(packet_identity)) != packet_hash:
        raise PaperIncubationError("preregistration_identity_does_not_rederive")
    if identity_hash_v1(dict(validation_identity)) != validation_hash:
        raise PaperIncubationError("validation_identity_does_not_rederive")
    study_id = str(packet_identity["study_id"])
    cycle_id = str(packet_identity["cycle"]["cycle_id"])
    if validation_identity["preregistration_hash"] != packet_hash:
        raise PaperIncubationError("validation_is_not_of_this_preregistration")
    if str(validation_identity["cycle_id"]) != cycle_id:
        raise PaperIncubationError("validation_cycle_is_not_the_preregistered_cycle")
    if str(validation_identity["study_id"]) != study_id:
        raise PaperIncubationError("validation_study_is_not_the_preregistered_study")
    if trial_id not in [str(item) for item in packet_identity["candidates"]]:
        raise PaperIncubationError("candidate_is_not_preregistered_in_this_packet")
    judged = [item for item in validation_identity["candidates"] if str(item["trial_id"]) == trial_id]
    if len(judged) != 1 or judged[0]["state"] != STATE_INCUBATING:
        raise PaperIncubationError("candidate_was_not_judged_incubating_by_this_validation")
    events = [event for event in states if str(event["trial_id"]) == trial_id]
    if any("study_id" in event and str(event["study_id"]) != study_id for event in events):
        raise PaperIncubationError("candidate_state_is_of_another_study")
    if any(event["state"] == STATE_HOLDOUT_FAILED_REJECTED for event in events):
        raise PaperIncubationError("candidate_is_rejected")
    starts = [event for event in events
              if event["state"] == STATE_INCUBATING and str(event["evidence_hash"]).strip() == validation_hash]
    if len(starts) != 1:
        raise PaperIncubationError("candidate_needs_exactly_one_incubating_state_from_this_validation")
    recorded_at = starts[0]["recorded_at"]
    if not isinstance(recorded_at, datetime):
        raise PaperIncubationError("incubating_state_instant_must_be_a_persisted_datetime")
    required, unresolved = _required_days(packet_identity)
    binding = IncubationBindingV1(study_id, trial_id, str(packet_identity["symbol"]), cycle_id, packet_hash,
                                  validation_hash, _micros(recorded_at), required, unresolved, source=source,
                                  _issuer=_BINDING_ISSUER)
    _ISSUED_BINDINGS.add(binding.key)
    return binding


@dataclass(frozen=True, slots=True)
class IncubationResolutionV1:
    """One research cycle's incubation bindings, as resolved from the persisted registry.

    Issued only by the registry resolver. ``unresolved`` holds the cycle-level
    reasons a candidate without a binding is ``UNRESOLVED`` (e.g. the holdout is
    not opened); it is never a default requirement.
    """

    cycle_id: str
    source: str
    bindings: tuple[IncubationBindingV1, ...]
    unresolved: tuple[str, ...]
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _BINDING_ISSUER:
            raise PaperIncubationError("incubation_resolution_is_issued_only_by_the_registry")

    @property
    def key(self) -> tuple[Any, ...]:
        return (self.cycle_id, self.source, tuple(binding.key for binding in self.bindings), self.unresolved)

    @property
    def issued(self) -> bool:
        return self.key in _ISSUED_RESOLUTIONS and all(binding.issued for binding in self.bindings)

    def payload(self) -> dict[str, Any]:
        return {"cycle_id": self.cycle_id, "source": self.source, "bound_candidates": len(self.bindings),
                "unresolved": list(self.unresolved)}


_ISSUED_RESOLUTIONS: set[tuple[Any, ...]] = set()


def _issue_resolution_v1(cycle_id: str, source: str, bindings: Sequence[IncubationBindingV1],
                         unresolved: Sequence[str]) -> IncubationResolutionV1:
    """Called only by the registry resolver, with bindings it issued from rows it read."""
    if source != BINDING_SOURCE_REGISTRY_V1:
        raise PaperIncubationError("incubation_resolution_is_issued_only_by_the_registry")
    if any(not isinstance(binding, IncubationBindingV1) or not binding.issued
           or binding.source != BINDING_SOURCE_REGISTRY_V1 for binding in bindings):
        raise PaperIncubationError("incubation_binding_is_issued_only_by_the_registry")
    if any(binding.cycle_id != cycle_id for binding in bindings):
        raise PaperIncubationError("binding_is_of_another_cycle")
    if bindings and unresolved:
        raise PaperIncubationError("a_resolution_with_bindings_has_no_cycle_level_gap")
    ordered = tuple(sorted(bindings, key=lambda binding: (binding.study_id, binding.trial_id)))
    if len({(binding.study_id, binding.trial_id) for binding in ordered}) != len(ordered):
        raise PaperIncubationError("one_binding_per_candidate")
    resolution = IncubationResolutionV1(cycle_id, source, ordered, tuple(sorted(set(unresolved))), _BINDING_ISSUER)
    _ISSUED_RESOLUTIONS.add(resolution.key)
    return resolution


# ---------------------------------------------------------------------------
# Evidenced elapsed time
# ---------------------------------------------------------------------------


def evidenced_segments_v1(items: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    """Merged ``[start, end]`` micros of linked fill runs; restarts split, overlaps count once.

    ``items`` are one candidate's fills on one symbol, ordered by decision.
    """
    runs: list[list[int]] = []
    run_of: dict[str, int] = {}
    for item in items:
        decided = int(item["decided_at_micros"])
        observed = int(item["fill_bar_open_micros"]) if item["status"] != FILL_STATUS_BAR_NOT_OBSERVED else decided
        # A signal naming an earlier one was decided by the same running process: one run.
        previous = item.get("previous_signal_id")
        index = None if previous is None else run_of.get(str(previous))
        if index is None:
            index = len(runs)
            runs.append([decided, observed])
        else:
            runs[index][1] = max(runs[index][1], observed)
        run_of[str(item["signal_id"])] = index
    merged: list[tuple[int, int]] = []
    for start, end in sorted((run[0], run[1]) for run in runs):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _incubation(items: Sequence[Mapping[str, Any]], binding: IncubationBindingV1 | None,
                as_of_micros: int, unbound: Sequence[str]) -> dict[str, Any]:
    for item in items:
        observed = [int(item["decided_at_micros"])]
        if item["status"] != FILL_STATUS_BAR_NOT_OBSERVED:
            observed.append(int(item["fill_bar_open_micros"]))
        if max(observed) > as_of_micros:
            raise PaperIncubationError("fill_evidence_after_the_report_as_of")
        if binding is not None and min(observed) < binding.incubating_since_micros:
            raise PaperIncubationError("fill_decided_before_the_candidate_was_incubating")
    segments = evidenced_segments_v1(items)
    elapsed = sum(end - start for start, end in segments)
    required = None if binding is None else binding.required_days
    unresolved = list(unbound) if binding is None else list(binding.unresolved)
    if required is None:
        window = WINDOW_UNRESOLVED
    else:
        window = WINDOW_ELAPSED if elapsed >= required * _DAY_MICROS else WINDOW_INCOMPLETE
    return {
        "binding": None if binding is None else binding.payload(),
        "required_days": UNRESOLVED_OR7_INCUBATION_DAYS if required is None else required,
        "unresolved": unresolved,
        "elapsed_clock": ELAPSED_CLOCK_V1,
        "evidenced_segments": [{"start_micros": start, "end_micros": end} for start, end in segments],
        "evidenced_elapsed_micros": elapsed,
        "window": window,
    }


# ---------------------------------------------------------------------------


def incubation_report_v1(fills: Sequence[Mapping[str, Any]], cost_policy: CostPolicyV1, *,
                         as_of: datetime, bindings: Sequence[IncubationBindingV1] = (),
                         resolution: IncubationResolutionV1 | None = None) -> dict[str, Any]:
    """Per candidate and symbol: unit-exposure round trips on the fills' own evidence.

    ``fills`` are fill identities. ``resolution`` is the cycle's registry-resolved
    OR-7 bindings (the authoritative path); ``bindings`` are bindings from
    :func:`bind_incubation_v1` (caller-supplied identities, labelled so in each
    candidate's ``incubation.binding.source``). At most one of the two. A candidate
    without a binding has an ``UNRESOLVED`` requirement (the resolution's
    cycle-level reasons, else :data:`UNRESOLVED_NO_BINDING`). ``as_of`` is a
    reading instant only. Never promotes.
    """
    policy_hash = cost_policy.policy().content_hash
    as_of_micros = _micros(as_of)
    if resolution is not None and bindings:
        raise PaperIncubationError("pass_a_registry_resolution_or_bindings_not_both")
    if resolution is not None and (not isinstance(resolution, IncubationResolutionV1) or not resolution.issued):
        raise PaperIncubationError("incubation_resolution_is_issued_only_by_the_registry")
    by_candidate: dict[tuple[str, str], IncubationBindingV1] = {}
    for binding in (bindings if resolution is None else resolution.bindings):
        if not isinstance(binding, IncubationBindingV1) or not binding.issued:
            raise PaperIncubationError("incubation_binding_is_issued_only_by_bind_incubation_v1")
        key = (binding.study_id, binding.trial_id)
        if key in by_candidate:
            raise PaperIncubationError("one_binding_per_candidate")
        by_candidate[key] = binding
    unbound = list(resolution.unresolved) if resolution is not None and resolution.unresolved else [
        UNRESOLVED_NO_BINDING]
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    seen: set[str] = set()
    for fill in fills:
        if fill["cost_policy_hash"] != policy_hash:
            raise PaperIncubationError("fill_was_recorded_under_another_cost_policy")
        if str(fill["signal_id"]) in seen:
            raise PaperIncubationError("one_fill_per_signal")
        seen.add(str(fill["signal_id"]))
        groups.setdefault((str(fill["study_id"]), str(fill["trial_id"]), str(fill["symbol"])), []).append(fill)
    candidates = []
    for (study_id, trial_id, symbol), items in sorted(groups.items()):
        items = sorted(items, key=lambda item: (int(item["decided_at_micros"]), str(item["signal_id"])))
        bound = by_candidate.get((study_id, trial_id))
        if bound is not None and bound.symbol != symbol:
            raise PaperIncubationError("fill_symbol_is_not_the_preregistered_symbol")
        candidates.append(_candidate_report(study_id, trial_id, symbol, items, cost_policy, as_of_micros, bound,
                                            unbound))
    return {
        "schema_version": "paper-incubation-report-v1",
        "as_of": as_of.astimezone(UTC).isoformat(),
        "preregistration_registry": None if resolution is None else resolution.payload(),
        "sizing": SIZING_UNIT_EXPOSURE_PENDING_OR_11,
        "cost_mode": cost_policy.mode,
        "cost_policy_hash": policy_hash,
        "funding": "NOT_MODELLED",
        "cost_complete": False,
        "candidates": candidates,
        "state": STATE_INCUBATING,
        "lifecycle_exit": "OWNER_UNDECIDED_NO_AUTOMATIC_PROMOTION",
        "claim": "PAPER_INCUBATION_NOT_VALIDATED",
    }


def _candidate_report(study_id: str, trial_id: str, symbol: str, items: Sequence[Mapping[str, Any]],
                      cost_policy: CostPolicyV1, as_of_micros: int,
                      binding: IncubationBindingV1 | None, unbound: Sequence[str]) -> dict[str, Any]:
    incubation = _incubation(items, binding, as_of_micros, unbound)
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
    # Rounded down: the reported clock never exceeds the evidence.
    elapsed_days = (Decimal(incubation["evidenced_elapsed_micros"]) / _DAY_MICROS).quantize(
        Decimal("0.001"), rounding=ROUND_DOWN)
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
        "elapsed_days": format(elapsed_days, "f"),
        "required_days": incubation["required_days"],
        "incubation": incubation,
        "state": STATE_INCUBATING,
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
    "BINDING_SOURCES_V1",
    "BINDING_SOURCE_CALLER_SUPPLIED_V1",
    "BINDING_SOURCE_REGISTRY_V1",
    "ELAPSED_CLOCK_V1",
    "FILL_STATUSES",
    "FILL_STATUS_BAR_NOT_OBSERVED",
    "FILL_STATUS_FILLED",
    "FILL_STATUS_OPEN_AMBIGUOUS",
    "RETURN_QUANTUM_V1",
    "SIZING_UNIT_EXPOSURE_PENDING_OR_11",
    "STATE_PROFESSIONALLY_VALIDATED_RESERVED",
    "UNRESOLVED_NO_BINDING",
    "UNRESOLVED_OR7_INCUBATION_DAYS",
    "WINDOW_ELAPSED",
    "WINDOW_INCOMPLETE",
    "WINDOW_UNRESOLVED",
    "IncubationBindingV1",
    "IncubationResolutionV1",
    "PaperIncubationError",
    "bind_incubation_v1",
    "evidenced_segments_v1",
    "incubation_report_v1",
]
