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

Runner liveness replaces the fill clock when it is supplied
-----------------------------------------------------------
With a :class:`~trade_platform.runner_liveness_v1.RunnerLivenessV1` for a bound
candidate (derive it with :func:`incubation_liveness_v1`), the elapsed clock is
``PROVEN_FORWARD_PROCESSING_INTERVALS_LOWER_BOUND``: the union of its ``PROVEN``
intervals -- live bars processed by one unbroken runner session, bound to
manifest-proven capture hours and sealed bars -- clipped to
``[INCUBATING instant, as_of]``. Heartbeats, restarts, capture gaps, clock
discontinuities and unsealed minutes earn nothing and are listed by reason,
together with any time the liveness window does not cover; nothing is
bridged. A quiet minute counts only where the runner provably processed the
feed around it. The liveness must be issued by the derivation, be of this
candidate and symbol, have counted only sessions that watched the candidate as
``INCUBATING`` on this binding's validation, end no later than ``as_of`` (no
look-ahead) and have no refused session (a tampered, headless or re-chained
session refuses the report: ``runner_liveness_session_refused:<reason>``). The
fill-chain segments stay in the report as ``fill_segments``.

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
   so the evidence is cost-complete (each candidate's ``funding_evidence``
   section, :mod:`trade_platform.paper_funding_evidence_v1`, binds forward
   funding evidence to its holding intervals but is never a cost: at best
   ``EVIDENCE_COMPLETE``, ``ECONOMICALLY_COSTABLE`` is reserved);
4. OR-11 sizing and capital;
5. persisted runner liveness evidence (supplied as above), without which quiet
   periods never count toward the window, and an owner tolerance for the time
   it leaves unproven (the exit criteria's minimum evidence coverage);
6. an explicit, recorded owner authorization of the transition -- never an
   automatic promotion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final

from .first_party_capture_hourly_acceptance_v1 import HourlyAcceptanceV1
from .paper_funding_evidence_v1 import (
    FUNDING_EVIDENCE_STATES_V1,
    HOLDING_CHAIN_BROKEN,
    HOLDING_CLOSED_BY_FILL,
    HOLDING_OPEN_AT_AS_OF,
    PAPER_FUNDING_EVIDENCE_SCHEMA_VERSION_V1,
    CandidateFundingEvidenceV1,
    PaperFundingEvidenceError,
    funding_evidence_section_v1,
)
from .runner_liveness_v1 import (
    STATUS_PROVEN,
    STATUS_UNPROVEN,
    RunnerLivenessV1,
    SealedBarsEvidenceV1,
    derive_runner_liveness_v1,
)
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
ELAPSED_CLOCK_LIVENESS_V1: Final = "PROVEN_FORWARD_PROCESSING_INTERVALS_LOWER_BOUND"
#: Every report refusal about runner liveness starts with this.
LIVENESS_REFUSAL_PREFIX: Final = "runner_liveness_"
#: Incubation time the supplied liveness window does not cover (never proven).
UNPROVEN_OUTSIDE_LIVENESS_WINDOW: Final = "OUTSIDE_THE_LIVENESS_WINDOW"
#: Mirror of :data:`trade_platform.live_signals_v1.AUTHORITY_INCUBATING` (a test pins it equal).
AUTHORITY_INCUBATING: Final = "INCUBATING"

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


def incubation_liveness_v1(records: Sequence[Mapping[str, Any]], binding: IncubationBindingV1, *,
                           study_content_hash: str, as_of: datetime, acceptance: HourlyAcceptanceV1,
                           sealed: Sequence[SealedBarsEvidenceV1], max_progress_gap_nanos: int,
                           max_clock_divergence_nanos: int, max_processing_lag_nanos: int) -> RunnerLivenessV1:
    """The runner liveness of one bound candidate over ``[its INCUBATING minute, the minute of as_of)``.

    ``records`` are the stored progress rows
    (:meth:`~trade_platform.runner_liveness_v1.PostgresRunnerLivenessStoreV1.for_candidate`);
    only sessions that watched the candidate as ``INCUBATING`` on the binding's
    validation and ``study_content_hash`` (the preregistered study version) count.
    The bounds are the caller's stated engineering tolerances (no default).
    """
    if not isinstance(binding, IncubationBindingV1) or not binding.issued:
        raise PaperIncubationError("incubation_binding_is_issued_only_by_bind_incubation_v1")
    start = binding.incubating_since_micros // _MINUTE * _MINUTE
    end = _micros(as_of) // _MINUTE * _MINUTE
    if end <= start:
        raise PaperIncubationError(f"{LIVENESS_REFUSAL_PREFIX}window_is_empty_before_as_of")
    return derive_runner_liveness_v1(
        records, study_id=binding.study_id, trial_id=binding.trial_id, symbol=binding.symbol,
        window_start=_EPOCH + timedelta(microseconds=start), window_end=_EPOCH + timedelta(microseconds=end),
        acceptance=acceptance, sealed=sealed, max_progress_gap_nanos=max_progress_gap_nanos,
        max_clock_divergence_nanos=max_clock_divergence_nanos, max_processing_lag_nanos=max_processing_lag_nanos,
        watched_as={"study_content_hash": study_content_hash, "authority": AUTHORITY_INCUBATING,
                    "authority_evidence_hash": binding.validation_hash})


def _clip(low: int, high: int, start: int, end: int) -> int:
    return max(0, min(high, end) - max(low, start))


def _liveness_section(liveness: RunnerLivenessV1, binding: IncubationBindingV1, as_of_micros: int,
                      items: Sequence[Mapping[str, Any]]) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    """The proven processing segments inside ``[INCUBATING, as_of]`` and the liveness facts; refuses misuse."""
    payload = liveness.payload
    candidate = payload["candidate"]
    if (candidate["study_id"], candidate["trial_id"], candidate["symbol"]) != (
            binding.study_id, binding.trial_id, binding.symbol):
        raise PaperIncubationError(f"{LIVENESS_REFUSAL_PREFIX}is_of_another_candidate")
    watched = candidate.get("watched_as") or {}
    if (watched.get("authority"), watched.get("authority_evidence_hash")) != (
            AUTHORITY_INCUBATING, binding.validation_hash) or not watched.get("study_content_hash"):
        raise PaperIncubationError(f"{LIVENESS_REFUSAL_PREFIX}not_bound_to_this_incubation_evidence")
    window = payload["window"]
    if int(window["end_micros"]) > as_of_micros:
        raise PaperIncubationError(f"{LIVENESS_REFUSAL_PREFIX}window_ends_after_the_report_as_of")
    if payload["refused_sessions"]:
        reasons = sorted({str(item["reason"]) for item in payload["refused_sessions"]})
        raise PaperIncubationError(f"{LIVENESS_REFUSAL_PREFIX}session_refused:{','.join(reasons)}")
    low, high = binding.incubating_since_micros, max(binding.incubating_since_micros, as_of_micros)
    segments: list[tuple[int, int]] = []
    unproven: dict[str, int] = {}
    for item in liveness.intervals:
        start, end = max(low, item.start_micros), min(high, item.end_micros)
        if end <= start:
            continue
        if item.status == STATUS_PROVEN:
            if segments and start <= segments[-1][1]:  # adjacent capture hours: no gap, nothing bridged
                segments[-1] = (segments[-1][0], max(segments[-1][1], end))
            else:
                segments.append((start, end))
        elif item.status == STATUS_UNPROVEN and item.reason is not None:
            unproven[item.reason] = unproven.get(item.reason, 0) + end - start
    outside = (high - low) - _clip(low, high, int(window["start_micros"]), int(window["end_micros"]))
    if outside:
        unproven[UNPROVEN_OUTSIDE_LIVENESS_WINDOW] = outside
    discontinuities: dict[str, int] = {}
    for session in payload["runner_sessions"]:
        for item in session["discontinuities"]:
            discontinuities[item["kind"]] = discontinuities.get(item["kind"], 0) + 1
    outside_fills = sum(1 for item in items if not any(
        start <= int(item["decided_at_micros"]) < end for start, end in segments))
    return segments, {
        "liveness_hash": liveness.content_hash,
        "claim": payload["claim"],
        "window": dict(window),
        "bounds": dict(payload["bounds"]),
        "watched_as": dict(watched),
        "runner_sessions": [{"runner_session_id": item["runner_session_id"],
                             "start_progress_hash": item["start_progress_hash"],
                             "binding_hash": item["binding_hash"]} for item in payload["runner_sessions"]],
        "restarts": int(payload["restarts"]),
        "excluded_sessions": [dict(item) for item in payload["excluded_sessions"]],
        "discontinuities": dict(sorted(discontinuities.items())),
        "proven_micros": sum(end - start for start, end in segments),
        "unproven_micros_by_reason": dict(sorted(unproven.items())),
        "fills_decided_outside_proven_processing": outside_fills,
    }


def _incubation(items: Sequence[Mapping[str, Any]], binding: IncubationBindingV1 | None,
                as_of_micros: int, unbound: Sequence[str],
                liveness: RunnerLivenessV1 | None = None) -> dict[str, Any]:
    for item in items:
        observed = [int(item["decided_at_micros"])]
        if item["status"] != FILL_STATUS_BAR_NOT_OBSERVED:
            observed.append(int(item["fill_bar_open_micros"]))
        if max(observed) > as_of_micros:
            raise PaperIncubationError("fill_evidence_after_the_report_as_of")
        if binding is not None and min(observed) < binding.incubating_since_micros:
            raise PaperIncubationError("fill_decided_before_the_candidate_was_incubating")
    fill_segments = evidenced_segments_v1(items)
    segments, section, clock = fill_segments, None, ELAPSED_CLOCK_V1
    if liveness is not None:
        if binding is None:  # pragma: no cover - the report refuses liveness without a binding
            raise PaperIncubationError(f"{LIVENESS_REFUSAL_PREFIX}without_an_incubation_binding")
        segments, section = _liveness_section(liveness, binding, as_of_micros, items)
        clock = ELAPSED_CLOCK_LIVENESS_V1
    elapsed = sum(end - start for start, end in segments)
    required = None if binding is None else binding.required_days
    unresolved = list(unbound) if binding is None else list(binding.unresolved)
    if required is None:
        window = WINDOW_UNRESOLVED
    else:
        window = WINDOW_ELAPSED if elapsed >= required * _DAY_MICROS else WINDOW_INCOMPLETE
    out: dict[str, Any] = {
        "binding": None if binding is None else binding.payload(),
        "required_days": UNRESOLVED_OR7_INCUBATION_DAYS if required is None else required,
        "unresolved": unresolved,
        "elapsed_clock": clock,
        "evidenced_segments": [{"start_micros": start, "end_micros": end} for start, end in segments],
        "evidenced_elapsed_micros": elapsed,
        "window": window,
    }
    if section is not None:
        out["fill_segments"] = [{"start_micros": start, "end_micros": end} for start, end in fill_segments]
        out["runner_liveness"] = section
    return out


# ---------------------------------------------------------------------------


def incubation_report_v1(fills: Sequence[Mapping[str, Any]], cost_policy: CostPolicyV1, *,
                         as_of: datetime, bindings: Sequence[IncubationBindingV1] = (),
                         resolution: IncubationResolutionV1 | None = None,
                         liveness: Sequence[RunnerLivenessV1] = (),
                         funding: Sequence[CandidateFundingEvidenceV1] = ()) -> dict[str, Any]:
    """Per candidate and symbol: unit-exposure round trips on the fills' own evidence.

    ``fills`` are fill identities. ``resolution`` is the cycle's registry-resolved
    OR-7 bindings (the authoritative path); ``bindings`` are bindings from
    :func:`bind_incubation_v1` (caller-supplied identities, labelled so in each
    candidate's ``incubation.binding.source``). At most one of the two. A candidate
    without a binding has an ``UNRESOLVED`` requirement (the resolution's
    cycle-level reasons, else :data:`UNRESOLVED_NO_BINDING`). ``liveness`` holds
    at most one issued runner liveness per bound candidate (see the module
    docstring); a bound candidate with liveness is reported even without fills.
    ``as_of`` is a reading instant only. Never promotes. ``funding`` holds at most
    one :class:`~trade_platform.paper_funding_evidence_v1.CandidateFundingEvidenceV1`
    per reported candidate; each candidate's ``funding_evidence`` section lists it
    against the candidate's holding intervals and never changes a return or cost
    (``funding`` stays ``NOT_MODELLED``).
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
    proofs: dict[tuple[str, str], RunnerLivenessV1] = {}
    for item in liveness:
        if not isinstance(item, RunnerLivenessV1) or not item.issued:
            raise PaperIncubationError(f"{LIVENESS_REFUSAL_PREFIX}is_issued_only_by_derive_runner_liveness_v1")
        key = (str(item.payload["candidate"]["study_id"]), str(item.payload["candidate"]["trial_id"]))
        if key in proofs:
            raise PaperIncubationError(f"{LIVENESS_REFUSAL_PREFIX}one_per_candidate")
        if key not in by_candidate:
            raise PaperIncubationError(f"{LIVENESS_REFUSAL_PREFIX}without_an_incubation_binding")
        proofs[key] = item
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {
        (study_id, trial_id, by_candidate[(study_id, trial_id)].symbol): [] for study_id, trial_id in proofs}
    seen: set[str] = set()
    for fill in fills:
        if fill["cost_policy_hash"] != policy_hash:
            raise PaperIncubationError("fill_was_recorded_under_another_cost_policy")
        if str(fill["signal_id"]) in seen:
            raise PaperIncubationError("one_fill_per_signal")
        seen.add(str(fill["signal_id"]))
        groups.setdefault((str(fill["study_id"]), str(fill["trial_id"]), str(fill["symbol"])), []).append(fill)
    evidence: dict[tuple[str, str], CandidateFundingEvidenceV1] = {}
    for entry in funding:
        if not isinstance(entry, CandidateFundingEvidenceV1):
            raise PaperIncubationError("funding_evidence_is_issued_only_by_candidate_funding_evidence_v1")
        if entry.key in evidence:
            raise PaperIncubationError("funding_evidence_one_per_candidate")
        evidence[entry.key] = entry
    reported = {(study_id, trial_id) for study_id, trial_id, _ in groups}
    if set(evidence) - reported:
        raise PaperIncubationError("funding_evidence_for_a_candidate_not_in_the_report")
    candidates = []
    for (study_id, trial_id, symbol), items in sorted(groups.items()):
        items = sorted(items, key=lambda item: (int(item["decided_at_micros"]), str(item["signal_id"])))
        bound = by_candidate.get((study_id, trial_id))
        if bound is not None and bound.symbol != symbol:
            raise PaperIncubationError("fill_symbol_is_not_the_preregistered_symbol")
        try:
            candidates.append(_candidate_report(study_id, trial_id, symbol, items, cost_policy, as_of_micros, bound,
                                                unbound, proofs.get((study_id, trial_id)),
                                                evidence.get((study_id, trial_id))))
        except PaperFundingEvidenceError as error:
            raise PaperIncubationError(str(error)) from error
    return {
        "schema_version": "paper-incubation-report-v1",
        "as_of": as_of.astimezone(UTC).isoformat(),
        "preregistration_registry": None if resolution is None else resolution.payload(),
        "sizing": SIZING_UNIT_EXPOSURE_PENDING_OR_11,
        "cost_mode": cost_policy.mode,
        "cost_policy_hash": policy_hash,
        "funding": "NOT_MODELLED",
        "funding_evidence": {"schema_version": PAPER_FUNDING_EVIDENCE_SCHEMA_VERSION_V1,
                             "states": list(FUNDING_EVIDENCE_STATES_V1), "costable": False,
                             "charge": "NOT_CALCULATED", "changes_returns": False},
        "cost_complete": False,
        "candidates": candidates,
        "state": STATE_INCUBATING,
        "lifecycle_exit": "OWNER_UNDECIDED_NO_AUTOMATIC_PROMOTION",
        "claim": "PAPER_INCUBATION_NOT_VALIDATED",
    }


def _candidate_report(study_id: str, trial_id: str, symbol: str, items: Sequence[Mapping[str, Any]],
                      cost_policy: CostPolicyV1, as_of_micros: int,
                      binding: IncubationBindingV1 | None, unbound: Sequence[str],
                      liveness: RunnerLivenessV1 | None = None,
                      funding: CandidateFundingEvidenceV1 | None = None) -> dict[str, Any]:
    incubation = _incubation(items, binding, as_of_micros, unbound, liveness)
    # The paper ledger starts flat. The chain of known positions holds only while every
    # fill's signal names the previous one (one runner, no restart in between) and every
    # fill was taken; after a break it resumes only at a fill from flat.
    position, entry_price, entry_micros = 0, Decimal(0), 0
    chain_intact = False
    last_signal: str | None = None
    breaks = skipped = inconsistent = 0
    round_trips: list[dict[str, Any]] = []
    # Holding intervals for the funding evidence section only; they change no figure.
    holdings: list[dict[str, Any]] = []

    def broken_off(until: int) -> None:
        if position != 0:
            holdings.append({"direction": position, "start_micros": entry_micros,
                             "end_micros": max(entry_micros, until), "end_kind": HOLDING_CHAIN_BROKEN})

    for item in items:
        linked = item.get("previous_signal_id") is not None and item["previous_signal_id"] == last_signal
        last_signal = str(item["signal_id"])
        if chain_intact and (not linked or item["status"] != FILL_STATUS_FILLED):
            breaks += 1
            broken_off(int(item["decided_at_micros"]))
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
            broken_off(int(item["decided_at_micros"]))
            chain_intact, position = False, 0
            continue
        price = Decimal(str(item["fill_price"]))
        at = int(item["fill_bar_open_micros"])
        if position != 0:
            gross = (Decimal(position) * (price / entry_price - 1)).quantize(RETURN_QUANTUM_V1)
            round_trips.append({"direction": position, "entry_bar_open_micros": entry_micros,
                                "exit_bar_open_micros": at, "holding_minutes": (at - entry_micros) // _MINUTE,
                                "gross_return": format(gross, "f")})
            holdings.append({"direction": position, "start_micros": entry_micros, "end_micros": at,
                             "end_kind": HOLDING_CLOSED_BY_FILL})
        position = int(item["target_to"])
        entry_price, entry_micros = price, at
    if chain_intact and position != 0:
        holdings.append({"direction": position, "start_micros": entry_micros, "end_micros": as_of_micros,
                         "end_kind": HOLDING_OPEN_AT_AS_OF})
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
        # Auditable forward funding evidence, never a cost: no figure above depends on it.
        "funding_evidence": funding_evidence_section_v1(
            funding, study_id=study_id, trial_id=trial_id, symbol=symbol, holdings=holdings,
            fills_skipped=skipped + inconsistent, as_of_micros=as_of_micros,
            binding_validation_hash=None if binding is None else binding.validation_hash,
            fee_schedule_verified=cost_policy.fee_schedule is not None),
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
    "AUTHORITY_INCUBATING",
    "BINDING_SOURCES_V1",
    "BINDING_SOURCE_CALLER_SUPPLIED_V1",
    "BINDING_SOURCE_REGISTRY_V1",
    "ELAPSED_CLOCK_LIVENESS_V1",
    "ELAPSED_CLOCK_V1",
    "FILL_STATUSES",
    "FILL_STATUS_BAR_NOT_OBSERVED",
    "FILL_STATUS_FILLED",
    "FILL_STATUS_OPEN_AMBIGUOUS",
    "LIVENESS_REFUSAL_PREFIX",
    "RETURN_QUANTUM_V1",
    "SIZING_UNIT_EXPOSURE_PENDING_OR_11",
    "STATE_PROFESSIONALLY_VALIDATED_RESERVED",
    "UNPROVEN_OUTSIDE_LIVENESS_WINDOW",
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
    "incubation_liveness_v1",
    "incubation_report_v1",
]
