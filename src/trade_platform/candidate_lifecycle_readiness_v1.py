"""Candidate lifecycle readiness -- what one candidate still lacks on the way to the end of incubation.

``RESEARCH_ONLY``, read-only, NON-promoting. Given the stored evidence chain of
one Strategy Lab candidate (study + trial), this derives where the candidate
stands and lists, in path order, every piece of evidence and every owner
approval still missing between it and the end of paper incubation. It never
writes, never records a candidate state, never fills a missing owner value and
never treats an absent record as satisfied.

Readiness stages (an assessment vocabulary, never a persisted candidate state)
-----------------------------------------------------------------------------
=================================================  ===============================================
``SEARCH_ONLY``                                     no ``ESTABLISHED`` Decimal selection names it
``DECIMAL_SELECTION_ESTABLISHED``                   selected in Decimal; preregistration / one-shot
                                                    holdout opening / holdout validation pending
``HOLDOUT_REJECTED``                                canonical ``HOLDOUT_FAILED_REJECTED``; terminal
``INCUBATION_INCOMPLETE``                           canonical ``INCUBATING``; the packet-bound
                                                    window is unresolved or not yet evidenced
``INCUBATION_EVIDENCE_FAILED``                      canonical ``INCUBATING``, but the forward
                                                    evidence contradicts itself (refused report,
                                                    chain inconsistency, live/replay parity)
``FORWARD_EVIDENCE_INCOMPLETE``                     window elapsed; the forward evidence is not
                                                    complete enough for any owner decision
``ELIGIBLE_FOR_OWNER_AUTHORIZED_VALIDATION_DECISION``  nothing missing but the owner's decision
``EVIDENCE_INCONSISTENT``                           the stored records contradict each other
=================================================  ===============================================

The canonical candidate states stay those of
:mod:`trade_platform.strategy_lab_validation_v1` (``INCUBATING``,
``HOLDOUT_FAILED_REJECTED``; migration 0057 admits no other). No state is added:
an incubation *performance* failure has neither owner criteria nor a recorded
state, so ``INCUBATION_EVIDENCE_FAILED`` describes the evidence, not the
candidate, and the candidate stays ``INCUBATING``. ``PROFESSIONALLY_VALIDATED``
is reserved; nothing here reaches or proposes it. Even the eligible stage only
says a future owner-authorized decision has its evidence -- it is not one.

The elapsed incubation clock and the required length are never computed here:
they come from :func:`~trade_platform.paper_incubation_report_v1.bind_incubation_v1`
and :func:`~trade_platform.paper_incubation_report_v1.incubation_report_v1`
(packet-bound length, evidenced forward-observation segments, restart gaps never
bridged, look-ahead refused). A refusal there is a finding here, never bypassed.

Blockers
--------
Each blocker is ``BLOCKED_OWNER_DECISION_OR_<n>:<detail>`` when it waits on the
owner (OR-6 fees and stress envelope, OR-7 holdout/acceptance/incubation and the
forward exit criteria, OR-11 account and risk), else an engineering or evidence
code. ``blockers`` lists the current stage's own blockers first, then every
later requirement along the path that is not evidenced yet. In this version the
eligible stage is unreachable by construction: no authority persists the owner's
forward exit criteria or runner liveness, and the R10 report models neither
forward funding nor sizing -- each is listed, never assumed.

Import-light (no numpy/pyarrow): the protected API may serve it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from .activation_readiness_v1 import OR6, OR7, OR11, gate_of
from .paper_incubation_report_v1 import (
    BINDING_SOURCE_REGISTRY_V1,
    FILL_STATUS_FILLED,
    PACKET_STATUS_AUTHORIZED,
    PACKET_STATUS_DRAFT,
    SIZING_UNIT_EXPOSURE_PENDING_OR_11,
    STATE_HOLDOUT_FAILED_REJECTED,
    STATE_INCUBATING,
    STATE_PROFESSIONALLY_VALIDATED_RESERVED,
    WINDOW_ELAPSED,
    WINDOW_INCOMPLETE,
    PaperIncubationError,
    bind_incubation_v1,
    incubation_report_v1,
)
from .strategy_lab_policies_v1 import CostPolicyV1
from .strategy_lab_study_v1 import identity_hash_v1

SCHEMA_VERSION_V1: Final = "candidate-lifecycle-readiness-v1"

STAGE_SEARCH_ONLY: Final = "SEARCH_ONLY"
STAGE_DECIMAL_SELECTION_ESTABLISHED: Final = "DECIMAL_SELECTION_ESTABLISHED"
STAGE_HOLDOUT_REJECTED: Final = "HOLDOUT_REJECTED"
STAGE_INCUBATION_INCOMPLETE: Final = "INCUBATION_INCOMPLETE"
STAGE_INCUBATION_EVIDENCE_FAILED: Final = "INCUBATION_EVIDENCE_FAILED"
STAGE_FORWARD_EVIDENCE_INCOMPLETE: Final = "FORWARD_EVIDENCE_INCOMPLETE"
STAGE_ELIGIBLE: Final = "ELIGIBLE_FOR_OWNER_AUTHORIZED_VALIDATION_DECISION"
STAGE_EVIDENCE_INCONSISTENT: Final = "EVIDENCE_INCONSISTENT"
STAGES_V1: Final = (STAGE_SEARCH_ONLY, STAGE_DECIMAL_SELECTION_ESTABLISHED, STAGE_HOLDOUT_REJECTED,
                    STAGE_INCUBATION_INCOMPLETE, STAGE_INCUBATION_EVIDENCE_FAILED,
                    STAGE_FORWARD_EVIDENCE_INCOMPLETE, STAGE_ELIGIBLE, STAGE_EVIDENCE_INCONSISTENT)

#: Mirror of :data:`trade_platform.strategy_lab_authority_rerun_v1.SELECTION_ESTABLISHED`
#: (not imported: that module pulls the numerical stack). A test pins it equal.
SELECTION_ESTABLISHED: Final = "ESTABLISHED"
#: Mirror of :data:`trade_platform.account_policy_v1.STATUS_ACTIVE`; a test pins it equal.
ACCOUNT_POLICY_ACTIVE: Final = "ACTIVE"

PROMOTION_V1: Final = "PROFESSIONALLY_VALIDATED_RESERVED_NEVER_PROMOTED_HERE"

# Path requirements not yet evidenced, by step. Owner gates carry their prefix.
NEED_DECIMAL_SELECTION: Final = "ESTABLISHED_DECIMAL_SELECTION_OF_THIS_CANDIDATE_NOT_RECORDED"
NEED_PREREGISTRATION: Final = f"{OR7}:NO_PREREGISTRATION_RECORDED_FOR_THIS_CANDIDATE"
NEED_HOLDOUT_OPENING: Final = "HOLDOUT_NOT_OPENED_FOR_THIS_PREREGISTRATION"
NEED_HOLDOUT_VALIDATION: Final = "HOLDOUT_VALIDATION_NOT_RECORDED"
NEED_FORWARD_FILLS: Final = "NO_FORWARD_PAPER_FILL_EVIDENCE"
NEED_INCUBATION_WINDOW: Final = "PACKET_BOUND_INCUBATION_WINDOW_NOT_EVIDENCED"
NEED_FEE_SCHEDULE: Final = f"{OR6}:MISSING_VERIFIED_FEE_SCHEDULE_AND_STRESS_ENVELOPE"
NEED_FORWARD_FUNDING: Final = "FORWARD_FUNDING_NOT_MODELLED_IN_PAPER_INCUBATION"
NEED_COST_COMPLETE: Final = "FORWARD_EVIDENCE_NOT_COST_COMPLETE"
NEED_SIZING: Final = f"{OR11}:PAPER_SIZING_IS_UNIT_EXPOSURE"
NEED_ACCOUNT_POLICY: Final = f"{OR11}:NO_ACCOUNT_POLICY_RECORDED"
NEED_EXIT_CRITERIA: Final = f"{OR7}:MISSING_OWNER_FORWARD_INCUBATION_EXIT_CRITERIA"
NEED_LIVENESS: Final = "MISSING_PERSISTED_RUNNER_LIVENESS_EVIDENCE"
NEED_PARITY: Final = "MISSING_LIVE_REPLAY_FILL_PARITY_EVIDENCE"
#: A binding from caller-supplied identities (``bind_incubation_v1``) is never authority for
#: eligibility; only one resolved from the persisted registry (``paper_incubation_registry_v1``) is.
NEED_REGISTRY_BINDING: Final = "INCUBATION_BINDING_NOT_REGISTRY_RESOLVED"

#: R6 preregistration gaps -> owner gate. An unknown reason stays an engineering finding.
_PACKET_GATES: Final[dict[str, str]] = {
    "MISSING_OWNER_HOLDOUT_END_OR_7": OR7,
    "MISSING_OWNER_ACCEPTANCE_CRITERIA_OR_7": OR7,
    "MISSING_OWNER_MINIMUM_TRADES_OR_7": OR7,
    "MISSING_OWNER_INCUBATION_LENGTH_OR_7": OR7,
    "MISSING_OWNER_AUTHORIZATION": OR7,
    "MISSING_VERIFIED_FEE_SCHEDULE_AND_STRESS_ENVELOPE_OR_6": OR6,
}


class CandidateReadinessError(ValueError):
    """Raised when the assessment's own inputs are malformed (not when the evidence is incomplete)."""


@dataclass(frozen=True, slots=True)
class CandidateEvidenceV1:
    """The stored evidence chain of one candidate, as read from its authorities.

    Every identity is the stored JSON (``identity`` columns) with its stored hash:
    ``rerun_*`` from ``strategy_lab_authority_reruns``, ``packet_*`` from
    ``strategy_lab_preregistrations``, ``validation_*`` from
    ``strategy_lab_holdout_validations``. ``opening`` is the cycle's stored
    opening (``cycle_id``, ``preregistration_hash``); ``states`` the study's
    ``strategy_lab_candidate_states`` rows; ``fills`` this candidate's
    ``paper_incubation_fills`` identities. ``replay_fill_hashes`` maps a signal
    id to the content hash of its replayed fill, or is ``None`` when no replay
    was run. ``account_policy`` is the stored account policy version
    (``status`` plus its identity's ``unresolved``), or ``None``.
    """

    study_id: str
    trial_id: str
    rerun_identity: Mapping[str, Any] | None = None
    rerun_hash: str | None = None
    packet_identity: Mapping[str, Any] | None = None
    packet_hash: str | None = None
    opening: Mapping[str, Any] | None = None
    validation_identity: Mapping[str, Any] | None = None
    validation_hash: str | None = None
    states: Sequence[Mapping[str, Any]] = ()
    fills: Sequence[Mapping[str, Any]] = ()
    replay_fill_hashes: Mapping[str, str] | None = None
    account_policy: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("study_id", "trial_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise CandidateReadinessError(f"{name}_required")
        for identity, digest in (("rerun_identity", "rerun_hash"), ("packet_identity", "packet_hash"),
                                 ("validation_identity", "validation_hash")):
            if (getattr(self, identity) is None) != (getattr(self, digest) is None):
                raise CandidateReadinessError(f"{identity}_and_{digest}_go_together")


@dataclass(frozen=True, slots=True)
class CandidateReadinessV1:
    study_id: str
    trial_id: str
    stage: str
    canonical_state: str | None
    blockers: tuple[str, ...]
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def owner_gates(self) -> tuple[str, ...]:
        return tuple(sorted({gate for gate in (gate_of(item) for item in self.blockers) if gate}))

    def payload(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION_V1, "study_id": self.study_id, "trial_id": self.trial_id,
                "stage": self.stage, "canonical_state": self.canonical_state, "blockers": list(self.blockers),
                "owner_gates": list(self.owner_gates), "evidence": dict(self.evidence),
                "promotion": PROMOTION_V1, "claim": "READINESS_ASSESSMENT_NOT_A_DECISION"}

    @property
    def assessment_hash(self) -> str:
        return identity_hash_v1(self.payload())


class _Inconsistent(Exception):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Inconsistent(reason)


def _cost_blockers(cost_policy: CostPolicyV1) -> list[str]:
    return [] if cost_policy.promotable_cost_basis else [NEED_FEE_SCHEDULE]


def _account_blockers(account_policy: Mapping[str, Any] | None) -> list[str]:
    if account_policy is None:
        return [NEED_ACCOUNT_POLICY]
    if account_policy.get("status") == ACCOUNT_POLICY_ACTIVE:
        return []
    unresolved = account_policy.get("unresolved")
    reasons = [str(item) for item in unresolved] if isinstance(unresolved, list) else []
    return [f"{OR11}:{reason}" for reason in reasons] or [f"{OR11}:ACCOUNT_POLICY_NOT_ACTIVE"]


def _later(cost_policy: CostPolicyV1, account_policy: Mapping[str, Any] | None, *,
           forward_started: bool) -> list[str]:
    """Requirements of the forward incubation path that no record evidences yet."""
    out: list[str] = []
    if not forward_started:
        out += [NEED_FORWARD_FILLS, NEED_INCUBATION_WINDOW, NEED_PARITY]
    out += _cost_blockers(cost_policy)
    out += [NEED_FORWARD_FUNDING, NEED_COST_COMPLETE, NEED_SIZING]
    out += _account_blockers(account_policy)
    out += [NEED_EXIT_CRITERIA, NEED_LIVENESS]
    return out


def _packet_blockers(packet: Mapping[str, Any]) -> list[str]:
    status, unresolved = packet.get("status"), packet.get("unresolved")
    _require(status in (PACKET_STATUS_AUTHORIZED, PACKET_STATUS_DRAFT), "PREREGISTRATION_STATUS_UNKNOWN")
    if not isinstance(unresolved, list) or not all(isinstance(item, str) for item in unresolved):
        raise _Inconsistent("PREREGISTRATION_UNRESOLVED_MALFORMED")
    _require((status == PACKET_STATUS_AUTHORIZED) == (not unresolved),
             "PREREGISTRATION_STATUS_CONTRADICTS_ITS_UNRESOLVED_REASONS")
    return [f"{_PACKET_GATES[r]}:{r}" if r in _PACKET_GATES else f"PREREGISTRATION_GAP:{r}"
            for r in sorted(set(unresolved))]


def _parity(fills: Sequence[Mapping[str, Any]], replay: Mapping[str, str] | None) -> tuple[list[str], list[str]]:
    """``(failures, blockers)`` of live vs replayed fills (content hashes per signal)."""
    if replay is None:
        return [], [NEED_PARITY]
    live = {str(item["signal_id"]): identity_hash_v1(dict(item)) for item in fills}
    differing = sorted(key for key in live.keys() | replay.keys() if live.get(key) != replay.get(key))
    return ([f"LIVE_REPLAY_FILL_PARITY_VIOLATION:{len(differing)}"] if differing else []), []


def assess_candidate_readiness_v1(evidence: CandidateEvidenceV1, *, cost_policy: CostPolicyV1,
                                  as_of: datetime) -> CandidateReadinessV1:
    """The candidate's readiness stage and every missing item on its path. Never writes, never promotes.

    ``cost_policy`` is the OR-6 policy the candidate's paper fills were recorded
    under; ``as_of`` is a reading instant only (forward evidence after it is
    look-ahead and refused by the R10 report).
    """
    if not isinstance(cost_policy, CostPolicyV1):
        raise CandidateReadinessError("cost_policy_must_be_an_or6_cost_policy")
    if as_of.tzinfo is None:
        raise CandidateReadinessError("as_of_must_be_timezone_aware")
    try:
        return _assess(evidence, cost_policy, as_of)
    except _Inconsistent as finding:
        return _result(evidence, STAGE_EVIDENCE_INCONSISTENT, _canonical(evidence), [str(finding)], {})


def _canonical(evidence: CandidateEvidenceV1) -> str | None:
    """The candidate's recorded state as the R6 lifecycle reads it (a rejection is terminal)."""
    latest: str | None = None
    for event in evidence.states:
        if str(event.get("trial_id")) != evidence.trial_id:
            continue
        if latest == STATE_HOLDOUT_FAILED_REJECTED:
            continue
        latest = str(event.get("state"))
    return latest


def _result(evidence: CandidateEvidenceV1, stage: str, state: str | None, blockers: Sequence[str],
            facts: Mapping[str, Any]) -> CandidateReadinessV1:
    if stage not in STAGES_V1:  # pragma: no cover - internal invariant
        raise CandidateReadinessError("unknown_stage")
    return CandidateReadinessV1(evidence.study_id, evidence.trial_id, stage, state,
                                tuple(dict.fromkeys(blockers)), dict(facts))


def _assess(evidence: CandidateEvidenceV1, cost_policy: CostPolicyV1, as_of: datetime) -> CandidateReadinessV1:
    ev = evidence
    mine = [event for event in ev.states if str(event.get("trial_id")) == ev.trial_id]
    for event in ev.states:
        _require("study_id" not in event or str(event["study_id"]) == ev.study_id, "STATE_EVENT_OF_ANOTHER_STUDY")
    for item in ev.fills:
        _require((str(item.get("study_id")), str(item.get("trial_id"))) == (ev.study_id, ev.trial_id),
                 "FILL_OF_ANOTHER_CANDIDATE")
    state = _canonical(ev)
    later_layers = (ev.packet_identity, ev.validation_identity, mine or None, ev.fills or None)

    # Decimal selection -------------------------------------------------------
    facts: dict[str, Any] = {}
    if ev.rerun_identity is None:
        _require(all(layer is None for layer in later_layers), "LATER_EVIDENCE_WITHOUT_A_DECIMAL_SELECTION")
        return _result(ev, STAGE_SEARCH_ONLY, state,
                       [NEED_DECIMAL_SELECTION, NEED_PREREGISTRATION, *_later(cost_policy, ev.account_policy,
                                                                             forward_started=False)], facts)
    _require(identity_hash_v1(dict(ev.rerun_identity)) == ev.rerun_hash, "RERUN_IDENTITY_DOES_NOT_REDERIVE")
    _require(str(ev.rerun_identity.get("study_id")) == ev.study_id, "RERUN_IS_OF_ANOTHER_STUDY")
    selection = ev.rerun_identity.get("authoritative_selection") or {}
    selected = [str(item.get("trial_id")) for item in selection.get("selected", [])]
    facts["rerun_hash"] = ev.rerun_hash
    if selection.get("status") != SELECTION_ESTABLISHED or ev.trial_id not in selected:
        _require(all(layer is None for layer in later_layers), "LATER_EVIDENCE_WITHOUT_A_DECIMAL_SELECTION")
        why = (f"DECIMAL_SELECTION_NOT_ESTABLISHED:{selection.get('status')}"
               if selection.get("status") != SELECTION_ESTABLISHED else "CANDIDATE_NOT_IN_THE_DECIMAL_SELECTION")
        return _result(ev, STAGE_SEARCH_ONLY, state,
                       [why, NEED_PREREGISTRATION, *_later(cost_policy, ev.account_policy, forward_started=False)],
                       facts)

    # Preregistration ---------------------------------------------------------
    pending_selected = STAGE_DECIMAL_SELECTION_ESTABLISHED
    if ev.packet_identity is None:
        _require(all(layer is None for layer in later_layers[1:]), "LATER_EVIDENCE_WITHOUT_A_PREREGISTRATION")
        return _result(ev, pending_selected, state,
                       [NEED_PREREGISTRATION, NEED_HOLDOUT_OPENING, NEED_HOLDOUT_VALIDATION,
                        *_later(cost_policy, ev.account_policy, forward_started=False)], facts)
    packet = ev.packet_identity
    _require(identity_hash_v1(dict(packet)) == ev.packet_hash, "PREREGISTRATION_IDENTITY_DOES_NOT_REDERIVE")
    _require(str(packet.get("study_id")) == ev.study_id, "PREREGISTRATION_IS_OF_ANOTHER_STUDY")
    _require(packet.get("rerun_hash") == ev.rerun_hash, "PREREGISTRATION_BINDS_ANOTHER_RERUN")
    _require(packet.get("rerun_selection_status") == SELECTION_ESTABLISHED,
             "PREREGISTRATION_SELECTION_STATUS_DIFFERS_FROM_THE_RERUN")
    _require(ev.trial_id in [str(item) for item in packet.get("candidates", [])],
             "CANDIDATE_NOT_IN_THE_PREREGISTRATION")
    packet_gaps = _packet_blockers(packet)
    cycle_id = str((packet.get("cycle") or {}).get("cycle_id"))
    facts.update({"preregistration_hash": ev.packet_hash, "preregistration_status": packet["status"],
                  "cycle_id": cycle_id})

    # One-shot holdout opening ------------------------------------------------
    opening = ev.opening
    if opening is not None:
        _require(str(opening.get("cycle_id")) == cycle_id, "OPENING_IS_OF_ANOTHER_CYCLE")
    opened_here = opening is not None and str(opening.get("preregistration_hash")).strip() == ev.packet_hash
    if not opened_here:
        _require(all(layer is None for layer in later_layers[1:]), "LATER_EVIDENCE_WITHOUT_THIS_PACKETS_OPENING")
        first =[f"CYCLE_HOLDOUT_SPENT_BY_ANOTHER_PREREGISTRATION:{str(opening.get('preregistration_hash')).strip()}"
                 ] if opening is not None else [*packet_gaps, NEED_HOLDOUT_OPENING]
        return _result(ev, pending_selected, state,
                       [*first, NEED_HOLDOUT_VALIDATION, *_later(cost_policy, ev.account_policy,
                                                                 forward_started=False)], facts)
    _require(not packet_gaps, "HOLDOUT_OPENED_FOR_A_DRAFT_PREREGISTRATION")

    # Holdout validation ------------------------------------------------------
    if ev.validation_identity is None:
        _require(not mine and not ev.fills, "CANDIDATE_STATE_OR_FILLS_WITHOUT_A_HOLDOUT_VALIDATION")
        return _result(ev, pending_selected, state,
                       [NEED_HOLDOUT_VALIDATION, *_later(cost_policy, ev.account_policy, forward_started=False)],
                       facts)
    validation = ev.validation_identity
    _require(identity_hash_v1(dict(validation)) == ev.validation_hash, "VALIDATION_IDENTITY_DOES_NOT_REDERIVE")
    _require(validation.get("preregistration_hash") == ev.packet_hash, "VALIDATION_IS_OF_ANOTHER_PREREGISTRATION")
    _require(str(validation.get("cycle_id")) == cycle_id, "VALIDATION_IS_OF_ANOTHER_CYCLE")
    _require(str(validation.get("study_id")) == ev.study_id, "VALIDATION_IS_OF_ANOTHER_STUDY")
    judged = [item for item in validation.get("candidates", []) if str(item.get("trial_id")) == ev.trial_id]
    _require(len(judged) == 1, "CANDIDATE_NOT_JUDGED_EXACTLY_ONCE_BY_THE_VALIDATION")
    verdict = judged[0].get("state")
    from_this = [event for event in mine if str(event.get("evidence_hash")).strip() == ev.validation_hash]
    _require(len(from_this) == len(mine), "CANDIDATE_STATE_FROM_ANOTHER_VALIDATION")
    _require(len(from_this) == 1, "CANDIDATE_NEEDS_EXACTLY_ONE_STATE_EVENT_FROM_ITS_VALIDATION")
    _require(from_this[0].get("state") == verdict, "CANDIDATE_STATE_DIFFERS_FROM_THE_VALIDATION_VERDICT")
    facts["validation_hash"] = ev.validation_hash
    if verdict == STATE_HOLDOUT_FAILED_REJECTED:
        _require(not ev.fills, "PAPER_FILLS_FOR_A_REJECTED_CANDIDATE")
        reasons = [str(item) for item in judged[0].get("reasons", [])]
        return _result(ev, STAGE_HOLDOUT_REJECTED, state,
                       [f"HOLDOUT_FAILED_REJECTED_TERMINAL:{r}" for r in reasons]
                       or ["HOLDOUT_FAILED_REJECTED_TERMINAL"], facts)
    _require(verdict == STATE_INCUBATING, "VALIDATION_VERDICT_UNKNOWN")

    # Incubation: packet-bound window and evidenced elapsed time (R10) -------
    try:
        binding = bind_incubation_v1(packet_identity=packet, packet_hash=str(ev.packet_hash),
                                     validation_identity=validation, validation_hash=str(ev.validation_hash),
                                     states=ev.states, trial_id=ev.trial_id)
    except PaperIncubationError as error:
        raise _Inconsistent(f"INCUBATION_BINDING_REFUSED:{error}") from error
    facts.update({"symbol": binding.symbol, "required_days": binding.required_days,
                  "incubating_since_micros": binding.incubating_since_micros, "binding_source": binding.source})
    provenance = [] if binding.source == BINDING_SOURCE_REGISTRY_V1 else [NEED_REGISTRY_BINDING]
    if not ev.fills:
        return _result(ev, STAGE_INCUBATION_INCOMPLETE, state,
                       [NEED_FORWARD_FILLS, NEED_INCUBATION_WINDOW,
                        *_later(cost_policy, ev.account_policy, forward_started=False), *provenance], facts)
    try:
        report = incubation_report_v1(ev.fills, cost_policy, as_of=as_of, bindings=(binding,))
    except PaperIncubationError as error:
        return _result(ev, STAGE_INCUBATION_EVIDENCE_FAILED, state, [f"INCUBATION_REPORT_REFUSED:{error}"], facts)
    (candidate,) = report["candidates"]
    incubation = candidate["incubation"]
    facts.update({"window": incubation["window"], "evidenced_elapsed_micros": incubation["evidenced_elapsed_micros"],
                  "evidenced_segments": len(incubation["evidenced_segments"]), "fills": candidate["fills"],
                  "chain_breaks": candidate["chain_breaks"], "elapsed_days": candidate["elapsed_days"]})
    failures, parity_blockers = _parity(ev.fills, ev.replay_fill_hashes)
    if candidate["chain_inconsistencies"]:
        failures.append(f"FORWARD_POSITION_CHAIN_INCONSISTENT:{candidate['chain_inconsistencies']}")
    if failures:
        return _result(ev, STAGE_INCUBATION_EVIDENCE_FAILED, state, failures, facts)

    forward: list[str] = list(parity_blockers)
    unfilled = candidate["fills"] - candidate["fills_by_status"][FILL_STATUS_FILLED]
    if candidate["chain_breaks"] or unfilled or candidate["fills_skipped_unknown_position"]:
        forward.append(f"{OR7}:FORWARD_EVIDENCE_GAP_TOLERANCE_UNDECIDED:chain_breaks={candidate['chain_breaks']},"
                       f"unfilled={unfilled},skipped={candidate['fills_skipped_unknown_position']}")
    forward += _cost_blockers(cost_policy)
    if report["funding"] == "NOT_MODELLED":
        forward.append(NEED_FORWARD_FUNDING)
    if report["cost_complete"] is not True:
        forward.append(NEED_COST_COMPLETE)
    if report["sizing"] == SIZING_UNIT_EXPOSURE_PENDING_OR_11:
        forward.append(NEED_SIZING)
    forward += _account_blockers(ev.account_policy)
    # Neither is persisted by any authority yet; listed until one exists, never assumed.
    forward += [NEED_EXIT_CRITERIA, NEED_LIVENESS, *provenance]

    if incubation["window"] != WINDOW_ELAPSED:
        window = ([f"INCUBATION_WINDOW_INCOMPLETE:{candidate['elapsed_days']}/{binding.required_days}_DAYS"]
                  if incubation["window"] == WINDOW_INCOMPLETE
                  else [f"{OR7}:{reason}" for reason in incubation["unresolved"]] or [NEED_INCUBATION_WINDOW])
        return _result(ev, STAGE_INCUBATION_INCOMPLETE, state, [*window, *forward], facts)
    if forward:
        return _result(ev, STAGE_FORWARD_EVIDENCE_INCOMPLETE, state, forward, facts)
    return _result(ev, STAGE_ELIGIBLE, state, [], facts)  # pragma: no cover - unreachable in v1 (see docstring)


__all__ = [
    "ACCOUNT_POLICY_ACTIVE",
    "PROMOTION_V1",
    "SELECTION_ESTABLISHED",
    "STAGES_V1",
    "STAGE_DECIMAL_SELECTION_ESTABLISHED",
    "STAGE_ELIGIBLE",
    "STAGE_EVIDENCE_INCONSISTENT",
    "STAGE_FORWARD_EVIDENCE_INCOMPLETE",
    "STAGE_HOLDOUT_REJECTED",
    "STAGE_INCUBATION_EVIDENCE_FAILED",
    "STAGE_INCUBATION_INCOMPLETE",
    "STAGE_SEARCH_ONLY",
    "STATE_PROFESSIONALLY_VALIDATED_RESERVED",
    "CandidateEvidenceV1",
    "CandidateReadinessError",
    "CandidateReadinessV1",
    "assess_candidate_readiness_v1",
]
