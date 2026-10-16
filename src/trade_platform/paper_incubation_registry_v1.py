"""Phase R10 -- incubation bindings resolved from the persisted R6 registry (read-only, import-light).

The paper incubation report (:mod:`trade_platform.paper_incubation_report_v1`)
accepts an OR-7 binding only inside an :class:`IncubationResolutionV1`, and this
module is the only issuer. It reads one research cycle's rows itself -- the
caller names a cycle, never a packet, a validation, a state or a hash -- and
re-proves the chain the registry wrote (migrations 0053, 0056, 0057):

1. the cycle is registered, and its holdout start is the opening's;
2. the cycle's holdout opening (at most one: primary key) names an
   ``AUTHORIZED`` preregistration;
3. that preregistration row's identity re-derives to its hash, and its stored
   columns (study, cycle, status) and holdout span equal the identity and the
   opening;
4. the packet's study row re-derives to the packet's ``study_content_hash``;
5. the packet's Decimal authority rerun row re-derives, is of that study and
   candidate set, has selection ``ESTABLISHED``, and its selected candidates are
   exactly the packet's;
6. the cycle's holdout validation (at most one: unique) re-derives, names that
   packet, study, cycle, symbol, span and dataset content hash, judges exactly
   the packet's candidates, and was recorded after the opening;
7. the study's candidate states written under that validation reproduce the
   validation's judgements one for one, were recorded after it, and no state
   under it belongs to another study.

Any forged, stale, mismatched or ambiguous row is refused with an explicit
reason (:class:`IncubationRegistryError`); nothing is repaired or defaulted.
A cycle whose holdout is not opened, or opened but not validated, resolves to
no binding with an explicit cycle-level ``UNRESOLVED`` reason: the report then
states ``UNRESOLVED_OR7_INCUBATION_DAYS``. Each ``INCUBATING`` candidate is
then bound by the report module's chain check, which keeps an unauthorized
packet's ``incubation_days`` ``UNRESOLVED`` (``MISSING_OWNER_AUTHORIZATION``).

The incubating instant is the persisted state row's ``recorded_at``; it must
not precede the validation, nor the validation the database-stamped
``opened_at``. Elapsed time stays the report's evidenced lower bound: nothing
here reads a clock. Nothing here writes, opens a holdout or promotes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from .paper_incubation_report_v1 import (
    PACKET_STATUS_AUTHORIZED,
    STATE_INCUBATING,
    IncubationResolutionV1,
    PaperIncubationError,
    _bind_stored_chain_v1,
    _issue_resolution_v1,
)
from .strategy_lab_study_v1 import identity_hash_v1

REGISTRY_SOURCE_V1: Final = "PERSISTED_PREREGISTRATION_REGISTRY_V1"
UNRESOLVED_HOLDOUT_NOT_OPENED: Final = "HOLDOUT_NOT_OPENED_FOR_CYCLE_OR_7"
UNRESOLVED_HOLDOUT_NOT_VALIDATED: Final = "HOLDOUT_OPENED_NOT_VALIDATED"
UNRESOLVED_NO_INCUBATING_CANDIDATE: Final = "NO_CANDIDATE_JUDGED_INCUBATING"

#: Mirrors of the R6 schema versions (strategy_lab_validation_v1 pulls the numerical stack; a test pins them).
PREREGISTRATION_SCHEMA_VERSION: Final = "strategy-lab-preregistration-v1"
VALIDATION_SCHEMA_VERSION: Final = "strategy-lab-holdout-validation-v1"
SELECTION_ESTABLISHED: Final = "ESTABLISHED"


class IncubationRegistryError(PaperIncubationError):
    """Raised when the persisted registry chain for a cycle is forged, stale, mismatched or ambiguous."""


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


def _json(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else json.loads(raw)
    if not isinstance(value, dict):
        raise IncubationRegistryError("stored_identity_is_not_an_object")
    return value


def _hash(raw: Any) -> str:
    return str(raw).strip()


def _instant(raw: Any, name: str) -> datetime:
    if not isinstance(raw, datetime) or raw.tzinfo is None:
        raise IncubationRegistryError(f"{name}_must_be_a_persisted_timezone_aware_instant")
    return raw


def _iso(raw: Any, name: str) -> str:
    return _instant(raw, name).astimezone(UTC).isoformat()


def _at_most_one(rows: list[tuple[Any, ...]], name: str) -> tuple[Any, ...] | None:
    if len(rows) > 1:
        raise IncubationRegistryError(f"{name}_is_ambiguous")
    return rows[0] if rows else None


def _exactly_one(rows: list[tuple[Any, ...]], missing: str, name: str) -> tuple[Any, ...]:
    row = _at_most_one(rows, name)
    if row is None:
        raise IncubationRegistryError(missing)
    return row


def _rederive(identity: Mapping[str, Any], stored_hash: str, name: str) -> None:
    if identity_hash_v1(dict(identity)) != stored_hash:
        raise IncubationRegistryError(f"{name}_identity_does_not_rederive")


def resolve_incubation_bindings_v1(cursor: _Cursor, *, cycle_id: str) -> IncubationResolutionV1:
    """Resolve every ``INCUBATING`` candidate of ``cycle_id`` from the persisted registry (read-only)."""
    if not isinstance(cycle_id, str) or not cycle_id.strip():
        raise IncubationRegistryError("cycle_id_required")
    cursor.execute("SELECT cycle_id, holdout_start FROM strategy_lab_research_cycles WHERE cycle_id=%s", (cycle_id,))
    cycle = _exactly_one(cursor.fetchall(), "research_cycle_not_registered", "research_cycle")
    cycle_start = _iso(cycle[1], "cycle_holdout_start")

    cursor.execute("SELECT preregistration_hash, preregistration_status, holdout_start, holdout_end_exclusive, "
                   "opened_at FROM strategy_lab_holdout_openings WHERE cycle_id=%s", (cycle_id,))
    opening = _at_most_one(cursor.fetchall(), "holdout_opening")
    if opening is None:
        cursor.execute("SELECT count(*) FROM strategy_lab_holdout_validations WHERE cycle_id=%s", (cycle_id,))
        if int(cursor.fetchall()[0][0]):
            raise IncubationRegistryError("validation_without_a_holdout_opening")
        return _issue_resolution_v1(cycle_id, REGISTRY_SOURCE_V1, (), (UNRESOLVED_HOLDOUT_NOT_OPENED,))
    packet_hash = _hash(opening[0])
    if opening[1] != PACKET_STATUS_AUTHORIZED:
        raise IncubationRegistryError("opening_does_not_name_an_authorized_preregistration")
    if _iso(opening[2], "opening_holdout_start") != cycle_start:
        raise IncubationRegistryError("opening_holdout_start_is_not_the_cycles")
    opening_end = _iso(opening[3], "opening_holdout_end")
    opened_at = _instant(opening[4], "opened_at")

    packet = _packet(cursor, packet_hash, cycle_id, cycle_start, opening_end)
    _study_and_rerun(cursor, packet)

    row = _validation_row(cursor, cycle_id)
    if row is None:
        return _issue_resolution_v1(cycle_id, REGISTRY_SOURCE_V1, (), (UNRESOLVED_HOLDOUT_NOT_VALIDATED,))
    validation_hash, validation = _hash(row[0]), _json(row[3])
    _rederive(validation, validation_hash, "validation")
    validated_at = _instant(row[4], "validation_recorded_at")
    holdout = validation.get("holdout") or {}
    if validation.get("schema_version") != VALIDATION_SCHEMA_VERSION:
        raise IncubationRegistryError("validation_schema_version_is_not_current")
    if _hash(row[1]) != packet_hash or validation.get("preregistration_hash") != packet_hash:
        raise IncubationRegistryError("validation_is_not_of_the_opened_preregistration")
    if validation.get("cycle_id") != cycle_id or validation.get("study_id") != packet["study_id"]:
        raise IncubationRegistryError("validation_cycle_or_study_is_not_the_packets")
    if (holdout.get("start"), holdout.get("end_exclusive"), holdout.get("symbol")) != (
            packet["holdout_start"], packet["holdout_end_exclusive"], packet["symbol"]):
        raise IncubationRegistryError("validation_holdout_is_not_the_preregistered_span_and_symbol")
    if holdout.get("dataset_content_hash") != _hash(row[2]):
        raise IncubationRegistryError("validation_dataset_hash_does_not_match_its_row")
    judged = {str(item["trial_id"]): str(item["state"]) for item in validation.get("candidates", [])}
    if [str(item["trial_id"]) for item in validation.get("candidates", [])] != list(packet["candidates"]):
        raise IncubationRegistryError("validation_candidates_are_not_the_preregistered_ones")
    if validated_at < opened_at:
        raise IncubationRegistryError("validation_recorded_before_the_holdout_was_opened")

    states = _state_rows(cursor, packet["study_id"], validation_hash)
    under = [state for state in states if state["evidence_hash"] == validation_hash]
    if any(state["study_id"] != packet["study_id"] for state in under):
        raise IncubationRegistryError("a_state_under_this_validation_is_of_another_study")
    if sorted((state["trial_id"], state["state"]) for state in under) != sorted(judged.items()):
        raise IncubationRegistryError("candidate_states_do_not_reproduce_the_validation")
    if any(state["recorded_at"] < validated_at for state in under):
        raise IncubationRegistryError("candidate_state_recorded_before_its_validation")
    study_states = [state for state in states if state["study_id"] == packet["study_id"]]
    bindings = [
        _bind_stored_chain_v1(packet_identity=packet, packet_hash=packet_hash, validation_identity=validation,
                              validation_hash=validation_hash, states=study_states, trial_id=trial_id)
        for trial_id in packet["candidates"] if judged[trial_id] == STATE_INCUBATING
    ]
    return _issue_resolution_v1(cycle_id, REGISTRY_SOURCE_V1, bindings,
                                () if bindings else (UNRESOLVED_NO_INCUBATING_CANDIDATE,))


def _validation_row(cursor: _Cursor, cycle_id: str) -> tuple[Any, ...] | None:
    cursor.execute("SELECT validation_hash, preregistration_hash, dataset_content_hash, identity, recorded_at "
                   "FROM strategy_lab_holdout_validations WHERE cycle_id=%s", (cycle_id,))
    return _at_most_one(cursor.fetchall(), "holdout_validation")


def _state_rows(cursor: _Cursor, study_id: str, validation_hash: str) -> list[dict[str, Any]]:
    """Every state of the study (a rejection under any evidence is terminal) and every state under the validation."""
    cursor.execute("SELECT study_id, trial_id, state, evidence_hash, recorded_at FROM strategy_lab_candidate_states "
                   "WHERE study_id=%s OR evidence_hash=%s ORDER BY recorded_at, trial_id", (study_id, validation_hash))
    return [{"study_id": str(r[0]), "trial_id": str(r[1]), "state": str(r[2]), "evidence_hash": _hash(r[3]),
             "recorded_at": _instant(r[4], "state_recorded_at")} for r in cursor.fetchall()]


def _packet(cursor: _Cursor, packet_hash: str, cycle_id: str, cycle_start: str, opening_end: str) -> dict[str, Any]:
    cursor.execute("SELECT preregistration_hash, study_id, cycle_id, status, identity FROM "
                   "strategy_lab_preregistrations WHERE preregistration_hash=%s", (packet_hash,))
    row = _exactly_one(cursor.fetchall(), "opened_preregistration_not_recorded", "preregistration")
    packet = _json(row[4])
    _rederive(packet, packet_hash, "preregistration")
    if packet.get("schema_version") != PREREGISTRATION_SCHEMA_VERSION:
        raise IncubationRegistryError("preregistration_schema_version_is_not_current")
    if (str(row[1]), str(row[2]), str(row[3])) != (packet.get("study_id"), cycle_id, packet.get("status")):
        raise IncubationRegistryError("preregistration_row_contradicts_its_identity")
    if row[3] != PACKET_STATUS_AUTHORIZED:
        raise IncubationRegistryError("opened_preregistration_is_not_authorized")
    cycle = packet.get("cycle") or {}
    if (cycle.get("cycle_id"), cycle.get("holdout_start"), packet.get("holdout_start")) != (
            cycle_id, cycle_start, cycle_start):
        raise IncubationRegistryError("preregistration_cycle_is_not_the_registered_cycle")
    if packet.get("holdout_end_exclusive") != opening_end:
        raise IncubationRegistryError("preregistration_holdout_end_is_not_the_opened_end")
    candidates = packet.get("candidates")
    if (not isinstance(candidates, list) or not candidates or len(set(candidates)) != len(candidates)
            or not all(isinstance(item, str) for item in candidates)):
        raise IncubationRegistryError("preregistration_candidates_malformed")
    return packet


def _study_and_rerun(cursor: _Cursor, packet: Mapping[str, Any]) -> None:
    cursor.execute("SELECT content_hash, identity FROM strategy_lab_studies WHERE study_id=%s", (packet["study_id"],))
    study = _exactly_one(cursor.fetchall(), "preregistered_study_not_recorded", "study")
    _rederive(_json(study[1]), _hash(study[0]), "study")
    if _hash(study[0]) != packet.get("study_content_hash"):
        raise IncubationRegistryError("preregistered_study_content_hash_is_not_the_recorded_study")
    cursor.execute("SELECT rerun_hash, study_id, candidate_set_hash, selection_status, identity FROM "
                   "strategy_lab_authority_reruns WHERE rerun_hash=%s", (packet.get("rerun_hash"),))
    rerun = _exactly_one(cursor.fetchall(), "preregistered_rerun_not_recorded", "authority_rerun")
    identity = _json(rerun[4])
    _rederive(identity, _hash(rerun[0]), "authority_rerun")
    selection = identity.get("authoritative_selection") or {}
    if (str(rerun[1]), _hash(rerun[2])) != (packet["study_id"], packet.get("candidate_set_hash")):
        raise IncubationRegistryError("rerun_is_not_of_the_preregistered_study_and_candidate_set")
    if (identity.get("study_content_hash"), identity.get("candidate_set_hash")) != (
            packet.get("study_content_hash"), packet.get("candidate_set_hash")):
        raise IncubationRegistryError("rerun_identity_is_not_of_the_preregistered_study_and_candidate_set")
    if not (rerun[3] == selection.get("status") == packet.get("rerun_selection_status") == SELECTION_ESTABLISHED):
        raise IncubationRegistryError("rerun_selection_is_not_established")
    if [str(item["trial_id"]) for item in selection.get("selected", [])] != list(packet["candidates"]):
        raise IncubationRegistryError("preregistered_candidates_are_not_the_rerun_selection")


__all__ = [
    "REGISTRY_SOURCE_V1",
    "UNRESOLVED_HOLDOUT_NOT_OPENED",
    "UNRESOLVED_HOLDOUT_NOT_VALIDATED",
    "UNRESOLVED_NO_INCUBATING_CANDIDATE",
    "IncubationRegistryError",
    "resolve_incubation_bindings_v1",
]
