"""Activation readiness -- what stops the next research/paper action, derived, never decided.

``RESEARCH_ONLY``. One deterministic read model over the existing authorities
(Strategy Lab ledger/manifests/reruns, the R6 holdout registry, the R9 account
policies, the OR-9 watch list, the R10 fills). It answers six questions:

=====================  ==========================================================
``research_run``        Can a Strategy Lab search run? (windows + OR-3/OR-5 policies)
``candidate_freeze``    Is there a finished study whose top-k is not yet frozen?
``decimal_rerun``       Is there a frozen candidate set without an ESTABLISHED rerun?
``holdout_open``        Is there an AUTHORIZED preregistration for the unopened cycle?
``research_watch``      Is there an ACTIVE watch list, and may forward bars be evaluated?
``paper_incubation``    Are there INCUBATING candidates and an ACTIVE account policy?
=====================  ==========================================================

Each answer is ``READY`` or ``BLOCKED`` with exact reason codes, the identities
and policies it is bound to, the evidence it read and the one next action. A
reason that waits on the owner is written ``BLOCKED_OWNER_DECISION_OR_<n>:<detail>``
(OR-6 fees/slippage, OR-7 holdout/acceptance, OR-9 watch list, OR-11 account and
risk); anything else is engineering or evidence work. Nothing here writes,
fills a missing owner value, or treats a missing record as satisfied: absence is
always a blocking reason. ``state_hash`` covers the answers (not the clock), so
the same authority state always yields the same hash.

Import-light (no numpy/pyarrow): the protected API serves it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from pydantic import BaseModel

from .strategy_lab_policies_v1 import (
    gross_cost_policy_v1,
    or3_numeric_policy_v1,
    or5_t2_timing_policy_v1,
)
from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1, identity_hash_v1

OR6: Final = "BLOCKED_OWNER_DECISION_OR_6"
OR7: Final = "BLOCKED_OWNER_DECISION_OR_7"
OR9: Final = "BLOCKED_OWNER_DECISION_OR_9"
OR11: Final = "BLOCKED_OWNER_DECISION_OR_11"

#: R6 preregistration gaps -> the gate that closes them. Unknown reasons are engineering.
_PREREGISTRATION_GATES: Final[dict[str, str]] = {
    "MISSING_OWNER_HOLDOUT_END_OR_7": OR7,
    "MISSING_OWNER_ACCEPTANCE_CRITERIA_OR_7": OR7,
    "MISSING_OWNER_MINIMUM_TRADES_OR_7": OR7,
    "MISSING_OWNER_INCUBATION_LENGTH_OR_7": OR7,
    "MISSING_OWNER_AUTHORIZATION": OR7,
    "MISSING_VERIFIED_FEE_SCHEDULE_AND_STRESS_ENVELOPE_OR_6": OR6,
}
_LABEL_SYMBOL: Final = re.compile(r" on ([A-Z0-9]{2,20}) ")

Status = Literal["READY", "BLOCKED"]
SubjectStatus = Literal["READY", "BLOCKED", "DONE"]
AnswerKey = Literal["research_run", "candidate_freeze", "decimal_rerun", "holdout_open", "research_watch",
                    "paper_incubation"]


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...


def _json(raw: object) -> Any:
    return json.loads(raw) if isinstance(raw, (str, bytes)) else raw


class ReadinessSubject(BaseModel):
    subject: str
    status: SubjectStatus
    reasons: list[str]
    identities: dict[str, str]


class ReadinessAnswer(BaseModel):
    key: AnswerKey
    question: str
    status: Status
    reasons: list[str]
    owner_gates: list[str]
    identities: dict[str, Any]
    evidence: dict[str, Any]
    next_action: str
    subjects: list[ReadinessSubject]


class ActivationReadiness(BaseModel):
    generated_at: datetime
    cycle_id: str
    holdout_state: Literal["UNOPENED", "OPENED"]
    answers: list[ReadinessAnswer]
    owner_gates_open: list[str]
    state_hash: str


def gate_of(reason: str) -> str | None:
    """``OR-7`` for ``BLOCKED_OWNER_DECISION_OR_7:...``; ``None`` for engineering/evidence reasons."""
    match = re.match(r"^BLOCKED_OWNER_DECISION_(OR_\d+)", reason)
    return match.group(1).replace("_", "-") if match else None


def _answer(key: AnswerKey, question: str, subjects: Sequence[ReadinessSubject], reasons: Sequence[str], *,
            identities: Mapping[str, Any], evidence: Mapping[str, Any], next_action: str,
            ready: bool | None = None) -> ReadinessAnswer:
    ready = any(s.status == "READY" for s in subjects) if ready is None else ready
    ordered = list(dict.fromkeys(reasons))
    gates = sorted({gate for gate in (gate_of(r) for r in ordered) if gate})
    return ReadinessAnswer(key=key, question=question, status="READY" if ready else "BLOCKED",
                           reasons=[] if ready else ordered, owner_gates=[] if ready else gates,
                           identities=dict(identities), evidence=dict(evidence), next_action=next_action,
                           subjects=list(subjects))


# ---------------------------------------------------------------------------
# Inputs read from the authorities
# ---------------------------------------------------------------------------


def read_window_catalogue_v1(data_root: Path | None) -> list[dict[str, Any]] | None:
    """Catalogued T2 research windows (identity summary only, not re-verified here).

    ``None`` when no research data root is configured in this process; the
    Strategy Lab re-proves a window's bytes whenever it binds one.
    """
    if data_root is None:
        return None
    directory = Path(data_root) / "v1" / "datasets" / "public-archive-bars"  # ResearchFrameStoreV1 layout
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        identity = payload["identity"]
        out.append({
            "dataset_version_id": str(payload["dataset_version_id"]), "content_hash": str(payload["content_hash"]),
            "symbol": str(identity["symbol"]), "first_utc_day": str(identity["first_utc_day"]),
            "last_utc_day": str(identity["last_utc_day"]), "bars": int(identity["bar_frame"]["row_count"]),
            "not_published_days": list(identity["not_published_days"]),
            "rejected_days": [str(d["utc_day"]) for d in identity.get("rejected_days", [])],
            "continuous": not identity["day_gaps"],
        })
    return out


def _studies(cursor: _Cursor) -> list[dict[str, Any]]:
    cursor.execute(
        "SELECT s.study_id, s.label, s.strategy_family, s.planned_trial_count, s.identity, "
        "count(q.trial_id) FILTER (WHERE q.state IN ('SUCCEEDED','FAILED','CANCELLED')), count(q.trial_id) "
        "FROM strategy_lab_studies s LEFT JOIN strategy_lab_trial_queue q ON q.study_id = s.study_id "
        "GROUP BY s.study_id ORDER BY s.label, s.study_id")
    out = []
    for row in cursor.fetchall():
        identity = _json(row[4])
        datasets = identity.get("datasets", []) if isinstance(identity, dict) else []
        match = _LABEL_SYMBOL.search(f"{row[1]} ")
        out.append({"study_id": str(row[0]), "label": str(row[1]), "family": str(row[2]), "planned": int(row[3]),
                    "terminal": int(row[5]), "queued": int(row[6]),
                    "symbol": match.group(1) if match else "",
                    "datasets": [str(d.get("dataset_version_id")) for d in datasets if isinstance(d, dict)]})
    return out


def _latest_by_study(cursor: _Cursor, query: str) -> dict[str, tuple[Any, ...]]:
    cursor.execute(query)
    out: dict[str, tuple[Any, ...]] = {}
    for row in cursor.fetchall():
        out.setdefault(str(row[0]), row)  # rows arrive newest first per study
    return out


# ---------------------------------------------------------------------------
# The six answers
# ---------------------------------------------------------------------------


def _research_run(studies: list[dict[str, Any]], windows: list[dict[str, Any]] | None) -> ReadinessAnswer:
    policies = {"numeric_OR_3": or3_numeric_policy_v1().slot, "timing_OR_5": or5_t2_timing_policy_v1().slot,
                "cost_OR_6": gross_cost_policy_v1().policy().slot}
    bound_windows = sorted({d for s in studies for d in s["datasets"]})
    if windows is None:
        reasons = [] if bound_windows else ["NO_RESEARCH_WINDOW_KNOWN_TO_THIS_PROCESS"]
        evidence: dict[str, Any] = {"window_catalogue": "NOT_CONFIGURED_IN_THIS_PROCESS",
                                    "windows_bound_by_studies": len(bound_windows)}
        subjects = [ReadinessSubject(subject=w, status="READY", reasons=[], identities={"dataset_version_id": w})
                    for w in bound_windows]
    else:
        reasons = [] if windows else ["NO_RESEARCH_WINDOW_CATALOGUED"]
        evidence = {"window_catalogue": "CATALOGUED_NOT_REVERIFIED_HERE", "windows": len(windows)}
        subjects = [ReadinessSubject(
            subject=f"{w['symbol']} {w['first_utc_day']}..{w['last_utc_day']}", status="READY",
            reasons=[] if w["continuous"] else [f"DECLARED_DAY_GAPS:{','.join(w['not_published_days'] + w['rejected_days'])}"],
            identities={"dataset_version_id": w["dataset_version_id"], "content_hash": w["content_hash"],
                        "bars": str(w["bars"])}) for w in windows]
    return _answer("research_run", "Can research run?", subjects, reasons, identities=policies, evidence={
        **evidence, "claim": "SEARCH_NON_AUTHORITATIVE / gross / T2 CONDITIONAL",
        "evaluation_bound": UNTOUCHED_HOLDOUT_BOUNDARY_V1.isoformat()},
        next_action="Run a Strategy Lab search on a catalogued window (family x window)." if subjects
        else "Build a research window from acquired T2 days.")


def _freeze_and_rerun(cursor: _Cursor, studies: list[dict[str, Any]]) -> tuple[ReadinessAnswer, ReadinessAnswer]:
    frozen = _latest_by_study(cursor, "SELECT study_id, candidate_set_hash, candidate_count FROM "
                                      "strategy_lab_candidate_sets ORDER BY study_id, recorded_at DESC")
    reruns = _latest_by_study(cursor, "SELECT study_id, rerun_hash, selection_status, candidate_set_hash FROM "
                                      "strategy_lab_authority_reruns ORDER BY study_id, "
                                      "(selection_status='ESTABLISHED') DESC, recorded_at DESC")
    freeze_subjects, rerun_subjects = [], []
    for s in studies:
        sid = s["study_id"]
        finished = s["queued"] == s["planned"] and s["terminal"] == s["planned"]
        ids = {"study_id": sid}
        if sid in frozen:
            freeze_subjects.append(ReadinessSubject(subject=s["label"], status="DONE", reasons=[],
                                                    identities={**ids, "candidate_set_hash": str(frozen[sid][1]).strip()}))
        elif finished:
            freeze_subjects.append(ReadinessSubject(subject=s["label"], status="READY", reasons=[], identities=ids))
        else:
            freeze_subjects.append(ReadinessSubject(
                subject=s["label"], status="BLOCKED",
                reasons=[f"STUDY_SEARCH_NOT_FINISHED:{s['terminal']}/{s['planned']}"], identities=ids))
        rerun = reruns.get(sid)
        if rerun is not None and str(rerun[2]) == "ESTABLISHED":
            rerun_subjects.append(ReadinessSubject(subject=s["label"], status="DONE", reasons=[], identities={
                **ids, "rerun_hash": str(rerun[1]).strip(), "candidate_set_hash": str(rerun[3]).strip()}))
        elif sid in frozen and rerun is None:
            rerun_subjects.append(ReadinessSubject(subject=s["label"], status="READY", reasons=[], identities={
                **ids, "candidate_set_hash": str(frozen[sid][1]).strip()}))
        elif rerun is not None:
            rerun_subjects.append(ReadinessSubject(
                subject=s["label"], status="BLOCKED",
                reasons=[f"DECIMAL_SELECTION_NOT_ESTABLISHED:{rerun[2]}_ENGINEERING_REVIEW"],
                identities={**ids, "rerun_hash": str(rerun[1]).strip()}))
        else:
            rerun_subjects.append(ReadinessSubject(subject=s["label"], status="BLOCKED",
                                                   reasons=["CANDIDATES_NOT_FROZEN"], identities=ids))

    def pending(subjects: list[ReadinessSubject], none: str) -> list[str]:
        if not subjects:
            return ["NO_STUDY_REGISTERED"]
        blocked = [r for s in subjects for r in s.reasons]
        return blocked or [none]

    freeze = _answer("candidate_freeze", "Can a candidate set be frozen?", freeze_subjects,
                     pending(freeze_subjects, "NOTHING_PENDING_EVERY_FINISHED_STUDY_IS_FROZEN"),
                     identities={"selection_rule": "top-k by sharpe_daily_annualized, HIGHER_IS_BETTER (CLI default)"},
                     evidence={"studies": len(studies), "frozen": len(frozen)},
                     next_action="Freeze the top-k of each finished study." if any(
                         s.status == "READY" for s in freeze_subjects) else "Nothing to freeze now.")
    rerun = _answer("decimal_rerun", "Can the Decimal authority rerun run?", rerun_subjects,
                    pending(rerun_subjects, "NOTHING_PENDING_EVERY_FROZEN_SET_IS_ESTABLISHED"),
                    identities={"numeric_OR_3": or3_numeric_policy_v1().slot},
                    evidence={"established": sum(1 for s in rerun_subjects if s.status == "DONE")},
                    next_action="Run the Decimal rerun of each frozen set." if any(
                        s.status == "READY" for s in rerun_subjects) else "Nothing to rerun now.")
    return freeze, rerun


def _cycle(cursor: _Cursor, cycle_id: str) -> dict[str, Any]:
    cursor.execute("SELECT holdout_end_exclusive, preregistration_hash, opened_at FROM strategy_lab_holdout_openings "
                   "WHERE cycle_id=%s", (cycle_id,))
    opening = cursor.fetchone()
    cursor.execute("SELECT validation_hash FROM strategy_lab_holdout_validations WHERE cycle_id=%s", (cycle_id,))
    validation = cursor.fetchone()
    cursor.execute("SELECT preregistration_hash, study_id, status, identity FROM strategy_lab_preregistrations "
                   "WHERE cycle_id=%s ORDER BY recorded_at DESC, preregistration_hash", (cycle_id,))
    packets = [{"hash": str(r[0]).strip(), "study_id": str(r[1]), "status": str(r[2]),
                "unresolved": list(_json(r[3]).get("unresolved", []))} for r in cursor.fetchall()]
    return {"cycle_id": cycle_id, "opening": opening,
            "validation": None if validation is None else str(validation[0]).strip(), "packets": packets}


def _holdout_open(cycle_id: str, cycle: Mapping[str, Any], established: int,
                  holdout_start: datetime) -> ReadinessAnswer:
    opening = cycle["opening"]
    packets = cycle["packets"]
    evidence = {"cycle_id": cycle_id, "holdout_start": holdout_start.isoformat(),
                "one_shot": "ONE_AUTHORIZED_PREREGISTRATION_OPENS_THIS_CYCLE_ONCE_AND_BINDS_ONE_STUDY",
                "preregistrations": {p["status"]: sum(1 for q in packets if q["status"] == p["status"]) for p in packets}}
    if opening is not None:
        return _answer("holdout_open", "Can the holdout open?", [], ["HOLDOUT_ALREADY_OPENED_FOR_THIS_CYCLE"],
                       identities={"preregistration_hash": str(opening[1]).strip(),
                                   "holdout_end_exclusive": opening[0].isoformat()},
                       evidence={**evidence, "opened_at": opening[2].isoformat()}, ready=False,
                       next_action="The cycle's holdout is spent; validate on it, or register a later cycle.")
    subjects = []
    for packet in packets:
        reasons = [f"{_PREREGISTRATION_GATES.get(r, 'ENGINEERING')}:{r}" for r in packet["unresolved"]]
        subjects.append(ReadinessSubject(subject=f"preregistration {packet['hash'][:12]}",
                                         status="READY" if packet["status"] == "AUTHORIZED" else "BLOCKED",
                                         reasons=reasons, identities={"preregistration_hash": packet["hash"],
                                                                      "study_id": packet["study_id"]}))
    reasons = [r for s in subjects for r in s.reasons]
    if not packets:
        reasons = [f"{OR7}:NO_PREREGISTRATION_RECORDED_FOR_THIS_CYCLE",
                   f"{OR7}:MISSING_OWNER_HOLDOUT_END_ACCEPTANCE_CRITERIA_MINIMUM_TRADES_INCUBATION_LENGTH",
                   f"{OR6}:MISSING_VERIFIED_FEE_SCHEDULE_AND_STRESS_ENVELOPE_OR_6"]
    if established == 0:
        reasons.append("NO_ESTABLISHED_DECIMAL_SELECTION_TO_PREREGISTER")
    return _answer("holdout_open", "Can the holdout open?", subjects, reasons,
                   identities={"cycle_id": cycle_id}, evidence=evidence,
                   next_action=("Open the holdout with the AUTHORIZED preregistration (one shot)."
                                if any(s.status == "READY" for s in subjects)
                                else "Owner: decide OR-7 and OR-6, then authorize one study's preregistration."))


def _research_watch(cursor: _Cursor, cycle: Mapping[str, Any]) -> ReadinessAnswer:
    from .research_watchlist_v1 import check_watch_entries_v1, read_latest_active_watchlist_v1

    watchlist = read_latest_active_watchlist_v1(cursor)
    reasons: list[str] = []
    subjects: list[ReadinessSubject] = []
    identities: dict[str, Any] = {}
    if watchlist is None:
        reasons.append(f"{OR9}:NO_ACTIVE_WATCH_LIST")
    else:
        identities = {"watchlist_id": watchlist.watchlist_id, "watchlist_hash": watchlist.content_hash}
        problems = check_watch_entries_v1(cursor, watchlist.entries)
        for entry in watchlist.entries:
            tag = f"{entry.study_id}:{entry.trial_id}"
            mine = [p for p in problems if p.endswith(tag)]
            subjects.append(ReadinessSubject(subject=f"{entry.symbol} trial {str(entry.trial_id)[:8]}",
                                             status="BLOCKED" if mine else "READY", reasons=mine,
                                             identities=entry.payload()))
        reasons.extend(problems)
    opening = cycle["opening"]
    if opening is None:
        reasons.append(f"{OR7}:FORWARD_BARS_OF_THIS_CYCLE_ARE_NOT_EVALUATED_UNTIL_ITS_HOLDOUT_IS_OPENED")
    ready = not reasons and any(s.status == "READY" for s in subjects)
    return _answer("research_watch", "Can a research watch run?", subjects, reasons, identities=identities,
                   evidence={"authority": "NOT_VALIDATED_RESEARCH_WATCH",
                             "forward_bars_from": None if opening is None else opening[0].isoformat()},
                   ready=ready,
                   next_action="Start the research watch runner for the active watch list." if ready
                   else "Owner: choose the watch list (OR-9); forward evaluation also waits on the holdout (OR-7).")


def _paper_incubation(cursor: _Cursor, cycle: Mapping[str, Any]) -> ReadinessAnswer:
    cursor.execute("SELECT DISTINCT ON (c.study_id, c.trial_id) c.study_id, c.trial_id, c.state FROM "
                   "strategy_lab_candidate_states c JOIN strategy_lab_holdout_validations v "
                   "ON v.validation_hash = c.evidence_hash WHERE v.cycle_id=%s "
                   "ORDER BY c.study_id, c.trial_id, c.recorded_at DESC", (cycle["cycle_id"],))
    latest = cursor.fetchall()
    incubating = [(str(r[0]), str(r[1])) for r in latest if str(r[2]) == "INCUBATING"]
    cursor.execute("SELECT a.account_id, p.policy_version_id, p.status, p.identity FROM account_contexts a "
                   "LEFT JOIN LATERAL (SELECT policy_version_id, status, identity FROM account_policy_versions v "
                   "WHERE v.account_id = a.account_id ORDER BY (status='ACTIVE') DESC, recorded_at DESC LIMIT 1) p "
                   "ON TRUE ORDER BY a.account_id")
    accounts = cursor.fetchall()
    cursor.execute("SELECT status, count(*) FROM paper_incubation_fills GROUP BY status ORDER BY status")
    fills = {str(k): int(v) for k, v in cursor.fetchall()}
    reasons: list[str] = []
    if not incubating:
        if cycle["opening"] is None:
            reasons.append(f"{OR7}:NO_INCUBATING_CANDIDATE_HOLDOUT_NOT_OPENED")
        elif cycle["validation"] is None:
            reasons.append("HOLDOUT_VALIDATION_NOT_RECORDED")
        else:
            reasons.append("NO_CANDIDATE_PASSED_THIS_CYCLES_HOLDOUT")
    active = [a for a in accounts if a[2] == "ACTIVE"]
    subjects = []
    for account in accounts:
        unresolved = [] if account[3] is None else list(_json(account[3]).get("unresolved", []))
        status: SubjectStatus = "READY" if account[2] == "ACTIVE" else "BLOCKED"
        subjects.append(ReadinessSubject(
            subject=f"account {account[0]}", status=status,
            reasons=[] if status == "READY" else ([f"{OR11}:{r}" for r in unresolved] or [f"{OR11}:NO_POLICY_VERSION"]),
            identities={"account_id": str(account[0]),
                        **({} if account[1] is None else {"policy_version_id": str(account[1])})}))
    if not accounts:
        reasons.append(f"{OR11}:NO_PAPER_ACCOUNT_REGISTERED")
    elif not active:
        reasons.extend(r for s in subjects for r in s.reasons)
    ready = bool(incubating) and bool(active)
    return _answer("paper_incubation", "Can paper incubation run?", subjects, reasons,
                   identities={"incubating_candidates": len(incubating),
                               "active_account_policies": [str(a[1]) for a in active]},
                   evidence={"fills": fills, "sizing": "unit exposure until an ACTIVE account policy is bound",
                             "claim": "INCUBATING, not validated; never execution authority"},
                   ready=ready,
                   next_action="Start paper incubation of the INCUBATING candidates." if ready
                   else "Owner: complete the account policy (OR-11); incubation also needs a passed holdout (OR-7).")


def read_activation_readiness_v1(cursor: _Cursor, *, windows: list[dict[str, Any]] | None = None,
                                 now: datetime | None = None, cycle_id: str | None = None) -> ActivationReadiness:
    """Readiness for the current cycle, or for another registered cycle (an engineering test cycle)."""
    current = f"cycle-{UNTOUCHED_HOLDOUT_BOUNDARY_V1.date().isoformat()}"
    cycle_id = cycle_id or current
    holdout_start = UNTOUCHED_HOLDOUT_BOUNDARY_V1
    if cycle_id != current:
        cursor.execute("SELECT holdout_start FROM strategy_lab_research_cycles WHERE cycle_id=%s", (cycle_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError("readiness_cycle_not_registered")
        holdout_start = row[0]
    studies = _studies(cursor)
    freeze, rerun = _freeze_and_rerun(cursor, studies)
    cycle = _cycle(cursor, cycle_id)
    established = sum(1 for s in rerun.subjects if s.status == "DONE")
    answers = [_research_run(studies, windows), freeze, rerun,
               _holdout_open(cycle_id, cycle, established, holdout_start),
               _research_watch(cursor, cycle), _paper_incubation(cursor, cycle)]
    gates = sorted({g for a in answers for g in a.owner_gates})
    state_hash = identity_hash_v1({"cycle_id": cycle_id, "answers": [a.model_dump(mode="json") for a in answers]})
    return ActivationReadiness(generated_at=now or datetime.now(UTC), cycle_id=cycle_id,
                               holdout_state="UNOPENED" if cycle["opening"] is None else "OPENED",
                               answers=answers, owner_gates_open=gates, state_hash=state_hash)


__all__ = [
    "OR6",
    "OR7",
    "OR9",
    "OR11",
    "ActivationReadiness",
    "ReadinessAnswer",
    "ReadinessSubject",
    "gate_of",
    "read_activation_readiness_v1",
    "read_window_catalogue_v1",
]
