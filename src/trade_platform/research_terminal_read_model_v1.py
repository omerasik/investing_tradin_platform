"""UI-2 -- read-only projections for the one-terminal research workflow.

``RESEARCH_ONLY``. Bounded, typed views over the R4 (Strategy Lab), R4.7
(Decimal authority reruns), C.1.1 (walk-forward), R6 (cycles, preregistrations, holdout, candidate
states), R8 (live signals), R9 (account policies) and R10 (paper incubation)
tables for the protected operator API. Nothing here writes, launches, opens a
holdout, approves a policy or promotes a candidate.

Every view states its claim and never lifts it:

* search results are ``SEARCH_NON_AUTHORITATIVE``; a Decimal authority rerun
  makes a *selection* numerically authoritative, never the economics (gross,
  T2 ``CONDITIONAL``);
* a C.1.1 walk-forward report is ``WALK_FORWARD_DEVELOPMENT`` /
  ``SEARCH_NON_AUTHORITATIVE`` / ``GROSS_NON_PROMOTABLE``, never validation;
* a candidate is ``INCUBATING`` or ``REJECTED`` only through a recorded R6 state,
  and nothing reaches a validated state;
* live signals are ``NOT_VALIDATED_<authority>`` proposals;
* an account policy is ``UNCONFIGURED`` until the owner completes it (OR-11);
* paper incubation is unit-exposure, gross, never cost-complete;
* a candidate lifecycle view is ``READINESS_ASSESSMENT_NOT_A_DECISION``: it links
  the stored ids (study -> rerun -> walk-forward -> preregistration -> validation
  -> exit criteria), restates the readiness and exit-readiness modules' blockers,
  keeps ``DRAFT`` exit criteria distinct from ``AUTHORIZED`` ones, and lists the
  evidence it does not derive (runner liveness, live/replay parity, an account
  policy not bound by authorized exit criteria) in ``evidence_not_loaded``;
* a comparison restates :mod:`trade_platform.research_candidate_comparison_v1`
  verbatim (no composite score, no default ranking, holdout ``NOT_READ``) with
  the search / Decimal rerun / walk-forward / validation stages kept apart
  (:data:`EVIDENCE_STAGES_V1`);
* per-candidate paper evidence restates the live/replay fill parity status and
  the forward funding section verbatim; evidence this read does not load is
  ``NOT_LOADED`` (never ``INCOMPLETE``, which is a loaded assessment's own
  verdict), and funding is always ``NON_COSTABLE``.

Open owner gates are listed as gates (``OPEN``), never as defaults.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, model_validator

from .paper_incubation_report_v1 import incubation_report_v1
from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1, identity_hash_v1


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...


def _json(raw: object) -> Any:
    return json.loads(raw) if isinstance(raw, (str, bytes)) else raw


#: The labels every terminal surface uses, with what each one may and may not mean.
AUTHORITY_LEGEND_V1: dict[str, str] = {
    "SEARCH_NON_AUTHORITATIVE": "float64 search tier; ranks only, never a decision",
    "DECIMAL_SELECTION_ESTABLISHED": "the Decimal rerun reproduces the selection; economics stay gross and conditional",
    "CONDITIONAL": "T2 archive evidence under the OR-5 2 s lag policy; never professional",
    "INCUBATING": "passed a preregistered holdout; forward paper evidence only; not validated",
    "REJECTED": "failed the holdout (terminal)",
    "NOT_VALIDATED_RESEARCH_WATCH": "a live proposal of a frozen candidate; not validated",
    "NOT_VALIDATED_INCUBATING": "a live proposal of an incubating candidate; not validated",
    "UNCONFIGURED": "an account policy missing owner values (OR-11); yields no risk policy",
}

#: Owner gates the terminal waits on. Prepared, never decided here.
OWNER_GATES_V1: list[dict[str, str]] = [
    {"gate": "OR-7", "topic": "validation / holdout packet (end, criteria, minimum trades, incubation length)"},
    {"gate": "OR-11", "topic": "account capital, sizing and risk limits"},
    {"gate": "OR-9", "topic": "which candidates to watch, alerts"},
    {"gate": "OR-6 fee schedule", "topic": "verified venue fee schedule and slippage stress envelope"},
]

_STATE_LABEL = {"INCUBATING": "INCUBATING", "HOLDOUT_FAILED_REJECTED": "REJECTED"}


class OwnerGateView(BaseModel):
    gate: str
    topic: str
    status: Literal["OPEN", "SATISFIED"]
    evidence: str


class RerunSelectionItemView(BaseModel):
    rank: int
    trial_id: UUID
    metric_value: str


class RerunView(BaseModel):
    rerun_hash: str
    study_id: UUID
    study_label: str
    strategy_family: str
    candidate_set_hash: str
    selection_status: str
    claim: Literal["DECIMAL_SELECTION_ESTABLISHED", "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED"]
    economics: Literal["GROSS_CONDITIONAL_NON_PROMOTABLE"] = "GROSS_CONDITIONAL_NON_PROMOTABLE"
    rerun_count: int
    metric: str
    selected: list[RerunSelectionItemView]
    recorded_at: datetime


class WalkForwardFoldView(BaseModel):
    fold: int
    status: str
    test_start: datetime
    test_end_exclusive: datetime
    selected_trial_id: UUID | None
    train_metric_value: str | None
    test_metrics: dict[str, Any]
    flags: list[str]


class WalkForwardView(BaseModel):
    """One recorded C.1.1 walk-forward report: development evidence, never validation."""

    report_hash: str
    study_id: UUID
    study_label: str
    study_content_hash: str
    dataset_content_hash: str
    split_hash: str
    evaluation_class: Literal["WALK_FORWARD_DEVELOPMENT"]
    numeric_tier: Literal["SEARCH_NON_AUTHORITATIVE"]
    cost_mode: Literal["GROSS_NON_PROMOTABLE"]
    promotable: Literal[False] = False
    rule: dict[str, Any]
    multiple_testing_trial_count: int
    evaluation_end_exclusive: datetime
    folds: list[WalkForwardFoldView]
    authority_reasons: list[str]
    recorded_at: datetime


class CycleView(BaseModel):
    cycle_id: str
    holdout_start: datetime
    holdout_state: Literal["UNOPENED", "OPENED"]
    holdout_end_exclusive: datetime | None
    opened_at: datetime | None
    preregistrations: dict[str, int]
    validated: bool


class CandidateStateView(BaseModel):
    study_id: UUID
    study_label: str
    trial_id: UUID
    state: str
    label: Literal["INCUBATING", "REJECTED"]
    reasons: list[str]
    recorded_at: datetime


class ValidationView(BaseModel):
    cycle: CycleView
    candidates: list[CandidateStateView]
    validated_claim: Literal["NONE_VALIDATED"] = "NONE_VALIDATED"


class LiveSignalView(BaseModel):
    signal_id: UUID
    symbol: str
    authority: str
    claim: str
    target_from: int
    target_to: int
    bar_open_at: datetime
    decided_at: datetime
    study_id: UUID
    trial_id: UUID
    explanation: dict[str, str]


class AccountView(BaseModel):
    account_id: str
    kind: str
    display_name: str
    base_currency: str
    policy_status: Literal["NO_POLICY", "UNCONFIGURED", "ACTIVE"]
    policy_version_id: UUID | None
    unresolved: list[str]
    recorded_at: datetime | None


class IncubationView(BaseModel):
    state: Literal["AVAILABLE", "NO_FILLS"]
    report: dict[str, Any]


class TerminalOverview(BaseModel):
    generated_at: datetime
    studies: int
    finished_studies: int
    reruns: dict[str, int]
    cycle: CycleView
    candidate_states: dict[str, int]
    signals: dict[str, int]
    latest_signal_at: datetime | None
    incubation_fills: dict[str, int]
    accounts: dict[str, int]
    owner_gates: list[OwnerGateView]
    authority_legend: dict[str, str]


def read_reruns_v1(cursor: _Cursor, *, limit: int) -> list[RerunView]:
    cursor.execute(
        "SELECT r.rerun_hash, r.study_id, s.label, s.strategy_family, r.candidate_set_hash, r.selection_status, "
        "r.rerun_count, r.identity, r.recorded_at FROM strategy_lab_authority_reruns r "
        "JOIN strategy_lab_studies s ON s.study_id = r.study_id ORDER BY r.recorded_at DESC, r.rerun_hash LIMIT %s",
        (limit,))
    out = []
    for row in cursor.fetchall():
        selection = _json(row[7]).get("authoritative_selection", {})
        established = row[5] == "ESTABLISHED"
        out.append(RerunView(
            rerun_hash=str(row[0]).strip(), study_id=row[1], study_label=str(row[2]), strategy_family=str(row[3]),
            candidate_set_hash=str(row[4]).strip(), selection_status=str(row[5]),
            claim="DECIMAL_SELECTION_ESTABLISHED" if established else "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED",
            rerun_count=int(row[6]), metric=str(selection.get("rule", {}).get("metric", "")),
            selected=[RerunSelectionItemView(rank=int(item["rank"]), trial_id=UUID(str(item["trial_id"])),
                                             metric_value=str(item["metric_value"]))
                      for item in selection.get("selected", [])] if established else [],
            recorded_at=row[8]))
    return out


class TerminalObjectNotFound(LookupError):
    """The named study, trial or report has no recorded evidence (a 404, never an empty default)."""


_WALK_FORWARD_SELECT = (
    "SELECT w.report_hash, w.study_id, s.label, w.identity, w.recorded_at FROM strategy_lab_walk_forward_reports w "
    "JOIN strategy_lab_studies s ON s.study_id = w.study_id")


def _walk_forward_view(row: tuple[Any, ...]) -> WalkForwardView:
    identity = _json(row[3])
    report_hash = str(row[0]).strip()
    if identity_hash_v1(identity) != report_hash:
        raise ValueError("stored_walk_forward_report_does_not_rederive")
    folds = [WalkForwardFoldView(
        fold=int(fold["fold"]), status=str(fold["status"]), test_start=fold["test_start"],
        test_end_exclusive=fold["test_end_exclusive"],
        selected_trial_id=None if fold["selected"] is None else UUID(str(fold["selected"]["trial_id"])),
        train_metric_value=None if fold["selected"] is None else str(fold["selected"]["train_metric_value"]),
        test_metrics={} if fold["selected"] is None else dict(fold["selected"]["test_metrics"]),
        flags=[str(flag) for flag in fold["flags"]]) for fold in identity["folds"]]
    return WalkForwardView(
        report_hash=report_hash, study_id=row[1], study_label=str(row[2]),
        study_content_hash=str(identity["study_content_hash"]),
        dataset_content_hash=str(identity["dataset"]["content_hash"]), split_hash=str(identity["split_hash"]),
        evaluation_class=identity["evaluation_class"], numeric_tier=identity["numeric_tier"],
        cost_mode=identity["cost_mode"], promotable=identity["promotable"], rule=dict(identity["rule"]),
        multiple_testing_trial_count=int(identity["multiple_testing_trial_count"]),
        evaluation_end_exclusive=identity["plan"]["evaluation_end_exclusive"], folds=folds,
        authority_reasons=[str(reason) for reason in identity["authority_reasons"]], recorded_at=row[4])


def read_walk_forward_v1(cursor: _Cursor, *, limit: int, study_id: UUID | None = None) -> list[WalkForwardView]:
    """Recorded walk-forward reports, newest first; a stored identity that does not re-derive is refused."""
    where, params = ("", (limit,)) if study_id is None else (" WHERE w.study_id = %s", (study_id, limit))
    cursor.execute(f"{_WALK_FORWARD_SELECT}{where} ORDER BY w.recorded_at DESC, w.report_hash LIMIT %s", params)
    return [_walk_forward_view(row) for row in cursor.fetchall()]


def read_walk_forward_report_v1(cursor: _Cursor, report_hash: str) -> WalkForwardView:
    """One recorded walk-forward report by its content hash (refused if it does not re-derive)."""
    cursor.execute(f"{_WALK_FORWARD_SELECT} WHERE w.report_hash = %s", (report_hash,))
    row = cursor.fetchone()
    if row is None:
        raise TerminalObjectNotFound("walk_forward_report_not_found")
    return _walk_forward_view(row)


def current_cycle_id_v1() -> str:
    """The current cycle's id, as ``ResearchCycleV1.cycle_id`` derives it (kept import-light for the API)."""
    return f"cycle-{UNTOUCHED_HOLDOUT_BOUNDARY_V1.date().isoformat()}"


def read_cycle_v1(cursor: _Cursor) -> CycleView:
    """The current research cycle (its holdout boundary is immutable)."""
    start = UNTOUCHED_HOLDOUT_BOUNDARY_V1
    cycle_id = current_cycle_id_v1()
    cursor.execute("SELECT holdout_end_exclusive, opened_at FROM strategy_lab_holdout_openings WHERE cycle_id=%s",
                   (cycle_id,))
    opening = cursor.fetchone()
    cursor.execute("SELECT status, count(*) FROM strategy_lab_preregistrations WHERE cycle_id=%s GROUP BY status",
                   (cycle_id,))
    preregistrations = {str(status): int(count) for status, count in cursor.fetchall()}
    cursor.execute("SELECT count(*) FROM strategy_lab_holdout_validations WHERE cycle_id=%s", (cycle_id,))
    validated = int((cursor.fetchone() or (0,))[0]) > 0
    return CycleView(cycle_id=cycle_id, holdout_start=start,
                     holdout_state="OPENED" if opening else "UNOPENED",
                     holdout_end_exclusive=opening[0] if opening else None,
                     opened_at=opening[1] if opening else None, preregistrations=preregistrations,
                     validated=validated)


def read_validation_v1(cursor: _Cursor, *, limit: int) -> ValidationView:
    cycle = read_cycle_v1(cursor)
    cursor.execute(
        "SELECT c.study_id, s.label, c.trial_id, c.state, c.reasons, c.recorded_at FROM strategy_lab_candidate_states c "
        "JOIN strategy_lab_studies s ON s.study_id = c.study_id ORDER BY c.recorded_at DESC, c.trial_id LIMIT %s",
        (limit,))
    candidates = [CandidateStateView(study_id=row[0], study_label=str(row[1]), trial_id=row[2], state=str(row[3]),
                                     label=_STATE_LABEL[str(row[3])],  # type: ignore[arg-type]
                                     reasons=[str(r) for r in _json(row[4])], recorded_at=row[5])
                  for row in cursor.fetchall()]
    return ValidationView(cycle=cycle, candidates=candidates)


def read_live_signals_v1(cursor: _Cursor, *, limit: int) -> list[LiveSignalView]:
    cursor.execute(
        "SELECT signal_id, symbol, authority, target_from, target_to, bar_open_at, decided_at, study_id, trial_id, "
        "identity FROM live_strategy_signals ORDER BY decided_at DESC, signal_id LIMIT %s", (limit,))
    out = []
    for row in cursor.fetchall():
        identity = _json(row[9])
        out.append(LiveSignalView(
            signal_id=row[0], symbol=str(row[1]), authority=str(row[2]), claim=f"NOT_VALIDATED_{row[2]}",
            target_from=int(row[3]), target_to=int(row[4]), bar_open_at=row[5], decided_at=row[6], study_id=row[7],
            trial_id=row[8], explanation={str(k): str(v) for k, v in identity.get("explanation", {}).items()}))
    return out


def read_accounts_v1(cursor: _Cursor) -> list[AccountView]:
    cursor.execute(
        "SELECT a.account_id, a.kind, a.display_name, a.base_currency, p.policy_version_id, p.status, p.identity, "
        "p.recorded_at FROM account_contexts a LEFT JOIN LATERAL (SELECT policy_version_id, status, identity, "
        "recorded_at FROM account_policy_versions v WHERE v.account_id = a.account_id "
        "ORDER BY recorded_at DESC, policy_version_id LIMIT 1) p ON TRUE ORDER BY a.account_id")
    out = []
    for row in cursor.fetchall():
        identity = _json(row[6]) if row[6] is not None else {}
        out.append(AccountView(
            account_id=str(row[0]), kind=str(row[1]), display_name=str(row[2]), base_currency=str(row[3]),
            policy_status="NO_POLICY" if row[4] is None else str(row[5]),  # type: ignore[arg-type]
            policy_version_id=row[4], unresolved=[str(r) for r in identity.get("unresolved", [])],
            recorded_at=row[7]))
    return out


def read_incubation_v1(cursor: _Cursor, *, now: datetime | None = None) -> IncubationView:
    """The R10 report over the current cycle's recorded fills, under its OR-6 policy (gross until opened).

    The OR-7 incubation bindings are resolved from the persisted registry rows of
    the same cycle; no caller-supplied packet or length reaches the report.
    """
    from .paper_account_v1 import cycle_cost_policy_v1, cycle_fill_rows_v1
    from .paper_incubation_registry_v1 import resolve_incubation_bindings_v1

    cycle_id = current_cycle_id_v1()
    fills = []
    for content_hash, raw in cycle_fill_rows_v1(cursor, cycle_id)[0]:
        identity = _json(raw)
        if identity_hash_v1(identity) != str(content_hash).strip():
            raise ValueError("stored_fill_identity_does_not_rederive")
        fills.append(identity)
    cost = cycle_cost_policy_v1(cursor, cycle_id)
    resolution = resolve_incubation_bindings_v1(cursor, cycle_id=cycle_id)
    report = incubation_report_v1(fills, cost, as_of=now or datetime.now(UTC), resolution=resolution)
    return IncubationView(state="AVAILABLE" if fills else "NO_FILLS", report=report)


#: Evidence the per-candidate read does not derive, so its blockers stay listed (never assumed satisfied).
NOT_LOADED_RUNNER_LIVENESS = "RUNNER_LIVENESS_NOT_DERIVED_BY_THIS_READ"
NOT_LOADED_PARITY = "LIVE_REPLAY_FILL_PARITY_NOT_LOADED_BY_THIS_READ"
NOT_LOADED_ACCOUNT_POLICY = "ACCOUNT_POLICY_NOT_BOUND_BY_AUTHORIZED_EXIT_CRITERIA"

_STAGE_CLAIM = {
    "SEARCH_ONLY": "SEARCH_NON_AUTHORITATIVE",
    "DECIMAL_SELECTION_ESTABLISHED": "DECIMAL_SELECTION_ESTABLISHED",
    "HOLDOUT_REJECTED": "REJECTED",
    "INCUBATION_INCOMPLETE": "INCUBATING",
    "INCUBATION_EVIDENCE_FAILED": "INCUBATING",
    "FORWARD_EVIDENCE_INCOMPLETE": "INCUBATING",
    "ELIGIBLE_FOR_OWNER_AUTHORIZED_VALIDATION_DECISION": "INCUBATING",
    "EVIDENCE_INCONSISTENT": "EVIDENCE_INCONSISTENT",
}


class WalkForwardLinkView(BaseModel):
    report_hash: str
    fold_count: int
    folds_selecting_trial: int
    recorded_at: datetime


class CandidateLinksView(BaseModel):
    """The identities one candidate's evidence chain carries, study -> incubation (ids only, never new ones)."""

    study_id: UUID
    study_label: str
    trial_id: UUID
    cycle_id: str
    rerun_hash: str | None
    walk_forward: list[WalkForwardLinkView]
    preregistration_hash: str | None
    preregistration_status: Literal["DRAFT", "AUTHORIZED"] | None
    holdout_opened_for_this_preregistration: bool
    validation_hash: str | None
    criteria_hash: str | None


class CandidateReadinessSectionView(BaseModel):
    stage: str
    canonical_state: str | None
    blockers: list[str]
    owner_gates: list[str]
    evidence: dict[str, Any]
    assessment_hash: str
    claim: Literal["READINESS_ASSESSMENT_NOT_A_DECISION"] = "READINESS_ASSESSMENT_NOT_A_DECISION"
    promotion: Literal["PROFESSIONALLY_VALIDATED_RESERVED_NEVER_PROMOTED_HERE"] = (
        "PROFESSIONALLY_VALIDATED_RESERVED_NEVER_PROMOTED_HERE")


class ExitCriteriaSectionView(BaseModel):
    """The candidate's forward exit criteria (migration 0065): AUTHORIZED and DRAFT are never conflated."""

    status: Literal["NOT_RECORDED", "DRAFT", "AUTHORIZED", "REFUSED"]
    criteria_hash: str | None
    recorded_at: datetime | None
    authorized_by: str | None
    authorized_on: str | None
    unresolved: list[str]
    thresholds: dict[str, Any]
    stage: str
    evidence_inputs: dict[str, str]
    blockers: list[str]
    refusals: list[str]
    owner_gates: list[str]
    claim: Literal["EXIT_READINESS_ASSESSMENT_NOT_A_DECISION"] = "EXIT_READINESS_ASSESSMENT_NOT_A_DECISION"
    account_activation: Literal["NEVER_ACTIVATES_AN_ACCOUNT"] = "NEVER_ACTIVATES_AN_ACCOUNT"


class CandidateLifecycleView(BaseModel):
    """One candidate's readiness and exit criteria, read from its stored chain. Never a decision."""

    links: CandidateLinksView
    claim: Literal["SEARCH_NON_AUTHORITATIVE", "DECIMAL_SELECTION_ESTABLISHED", "INCUBATING", "REJECTED",
                   "EVIDENCE_INCONSISTENT"]
    economics: Literal["GROSS_NON_PROMOTABLE", "VERIFIED_FEES_NOT_COST_COMPLETE"]
    readiness: CandidateReadinessSectionView
    exit_criteria: ExitCriteriaSectionView
    evidence_not_loaded: list[str]
    validated_claim: Literal["NONE_VALIDATED"] = "NONE_VALIDATED"
    as_of: datetime


class _CycleContext:
    """Cycle-wide inputs read once per request: opening, OR-6 policy, registry resolution, fills."""

    def __init__(self, cursor: _Cursor, as_of: datetime) -> None:
        from .paper_account_v1 import cycle_cost_policy_v1, cycle_fill_rows_v1
        from .paper_incubation_registry_v1 import (
            IncubationRegistryError,
            resolve_incubation_bindings_v1,
        )

        self.cycle_id = current_cycle_id_v1()
        self.as_of = as_of
        cursor.execute("SELECT cycle_id, preregistration_hash FROM strategy_lab_holdout_openings WHERE cycle_id=%s",
                       (self.cycle_id,))
        row = cursor.fetchone()
        self.opening = None if row is None else {"cycle_id": str(row[0]), "preregistration_hash": str(row[1]).strip()}
        self.cost = cycle_cost_policy_v1(cursor, self.cycle_id)
        self.resolution: Any = None
        self.resolution_refusal: str | None = None
        try:
            self.resolution = resolve_incubation_bindings_v1(cursor, cycle_id=self.cycle_id)
        except IncubationRegistryError as error:
            self.resolution_refusal = f"INCUBATION_REGISTRY_REFUSED:{error}"
        self.fills: list[dict[str, Any]] = []
        for content_hash, raw in cycle_fill_rows_v1(cursor, self.cycle_id)[0]:
            identity = _json(raw)
            if identity_hash_v1(identity) != str(content_hash).strip():
                raise ValueError("stored_fill_identity_does_not_rederive")
            self.fills.append(identity)


def _selected_trials(rerun_identity: dict[str, Any]) -> list[str]:
    selection = rerun_identity.get("authoritative_selection") or {}
    if selection.get("status") != "ESTABLISHED":
        return []
    return [str(item.get("trial_id")) for item in selection.get("selected", [])]


def _candidate_lifecycle(cursor: _Cursor, context: _CycleContext, study_id: UUID,
                         trial_id: UUID) -> CandidateLifecycleView:
    from .candidate_lifecycle_readiness_v1 import CandidateEvidenceV1, assess_candidate_readiness_v1
    from .incubation_exit_criteria_v1 import (
        STAGE_REFUSED,
        STATUS_AUTHORIZED,
        ExitCriteriaError,
        assess_exit_readiness_v1,
        resolve_exit_criteria_v1,
    )

    study, trial, cycle_id = str(study_id), str(trial_id), context.cycle_id
    cursor.execute("SELECT label FROM strategy_lab_studies WHERE study_id=%s", (study_id,))
    label_row = cursor.fetchone()
    if label_row is None:
        raise TerminalObjectNotFound("strategy_lab_study_not_found")

    # Preregistration: the one the cycle's holdout was opened for, else the latest naming this trial.
    cursor.execute("SELECT preregistration_hash, identity FROM strategy_lab_preregistrations WHERE cycle_id=%s "
                   "AND study_id=%s ORDER BY recorded_at DESC, preregistration_hash", (cycle_id, study_id))
    packets = [(str(h).strip(), _json(raw)) for h, raw in cursor.fetchall()]
    packets = [(h, p) for h, p in packets if trial in [str(item) for item in p.get("candidates", [])]]
    opened = context.opening["preregistration_hash"] if context.opening else None
    packet_hash, packet = next(((h, p) for h, p in packets if h == opened), packets[0] if packets else (None, None))

    # Decimal rerun: the packet's, else the latest established one naming the trial, else the study's latest.
    cursor.execute("SELECT rerun_hash, identity FROM strategy_lab_authority_reruns WHERE study_id=%s "
                   "ORDER BY recorded_at DESC, rerun_hash", (study_id,))
    reruns = [(str(h).strip(), _json(raw)) for h, raw in cursor.fetchall()]
    if packet is not None:
        rerun = next(((h, r) for h, r in reruns if h == packet.get("rerun_hash")), (None, None))
    else:
        rerun = next(((h, r) for h, r in reruns if trial in _selected_trials(r)), reruns[0] if reruns else (None, None))

    validation_hash, validation = None, None
    if packet_hash is not None and packet_hash == opened:
        cursor.execute("SELECT validation_hash, identity FROM strategy_lab_holdout_validations WHERE cycle_id=%s "
                       "AND preregistration_hash=%s", (cycle_id, packet_hash))
        row = cursor.fetchone()
        if row is not None:
            validation_hash, validation = str(row[0]).strip(), _json(row[1])
    cursor.execute("SELECT study_id, trial_id, state, evidence_hash, recorded_at FROM strategy_lab_candidate_states "
                   "WHERE study_id=%s ORDER BY recorded_at, trial_id", (study_id,))
    states = [{"study_id": str(r[0]), "trial_id": str(r[1]), "state": str(r[2]), "evidence_hash": str(r[3]).strip(),
               "recorded_at": r[4]} for r in cursor.fetchall()]
    fills = [item for item in context.fills if (str(item.get("study_id")), str(item.get("trial_id"))) == (study, trial)]

    not_loaded = [NOT_LOADED_RUNNER_LIVENESS, NOT_LOADED_PARITY]
    criteria, criteria_refusal = None, None
    try:
        criteria = resolve_exit_criteria_v1(cursor, cycle_id=cycle_id, study_id=study, trial_id=trial)
    except ExitCriteriaError as error:
        criteria_refusal = f"EXIT_CRITERIA_REGISTRY_REFUSED:{error}"
    account_policy = None
    bound_account = (criteria.packet.frozen["criteria"]["required_account_policy_hash"]
                     if criteria is not None and criteria.packet.status == STATUS_AUTHORIZED else None)
    if bound_account is not None:
        cursor.execute("SELECT status, identity, content_hash FROM account_policy_versions WHERE content_hash=%s",
                       (bound_account,))
        row = cursor.fetchone()
        if row is not None:
            account_policy = {"status": str(row[0]), "unresolved": list(_json(row[1]).get("unresolved", [])),
                              "content_hash": str(row[2]).strip()}
    if account_policy is None:
        not_loaded.append(NOT_LOADED_ACCOUNT_POLICY)

    evidence = CandidateEvidenceV1(
        study_id=study, trial_id=trial, rerun_identity=rerun[1], rerun_hash=rerun[0], packet_identity=packet,
        packet_hash=packet_hash, opening=context.opening, validation_identity=validation,
        validation_hash=validation_hash, states=states, fills=fills, account_policy=account_policy)
    readiness = assess_candidate_readiness_v1(evidence, cost_policy=context.cost, as_of=context.as_of)
    exit_ready = assess_exit_readiness_v1(evidence, cost_policy=context.cost, as_of=context.as_of,
                                          resolution=context.resolution, criteria=criteria)
    refusals = [*exit_ready.refusals, *(r for r in (context.resolution_refusal, criteria_refusal) if r)]
    packet_obj = None if criteria is None else criteria.packet
    frozen = None if packet_obj is None else packet_obj.frozen

    cursor.execute("SELECT report_hash, identity, recorded_at FROM strategy_lab_walk_forward_reports WHERE study_id=%s "
                   "ORDER BY recorded_at DESC, report_hash LIMIT 20", (study_id,))
    walks = []
    for report_hash, raw, recorded_at in cursor.fetchall():
        identity = _json(raw)
        if identity_hash_v1(identity) != str(report_hash).strip():
            raise ValueError("stored_walk_forward_report_does_not_rederive")
        picks = [fold for fold in identity["folds"]
                 if fold["selected"] is not None and str(fold["selected"]["trial_id"]) == trial]
        walks.append(WalkForwardLinkView(report_hash=str(report_hash).strip(), fold_count=len(identity["folds"]),
                                         folds_selecting_trial=len(picks), recorded_at=recorded_at))

    return CandidateLifecycleView(
        links=CandidateLinksView(
            study_id=study_id, study_label=str(label_row[0]), trial_id=trial_id, cycle_id=cycle_id,
            rerun_hash=rerun[0], walk_forward=walks, preregistration_hash=packet_hash,
            preregistration_status=None if packet is None else packet.get("status"),
            holdout_opened_for_this_preregistration=packet_hash is not None and packet_hash == opened,
            validation_hash=validation_hash, criteria_hash=None if packet_obj is None else packet_obj.content_hash),
        claim=_STAGE_CLAIM[readiness.stage],  # type: ignore[arg-type]
        economics="VERIFIED_FEES_NOT_COST_COMPLETE" if context.cost.promotable_cost_basis else "GROSS_NON_PROMOTABLE",
        readiness=CandidateReadinessSectionView(
            stage=readiness.stage, canonical_state=readiness.canonical_state, blockers=list(readiness.blockers),
            owner_gates=list(readiness.owner_gates), evidence=dict(readiness.evidence),
            assessment_hash=readiness.assessment_hash),
        exit_criteria=ExitCriteriaSectionView(
            status="REFUSED" if criteria_refusal else "NOT_RECORDED" if packet_obj is None else packet_obj.status,
            criteria_hash=None if packet_obj is None else packet_obj.content_hash,
            recorded_at=None if criteria is None else criteria.recorded_at,
            authorized_by=None if frozen is None else frozen["authorized_by"],
            authorized_on=None if frozen is None else frozen["authorized_on"],
            unresolved=[] if packet_obj is None else list(packet_obj.unresolved),
            thresholds=dict(exit_ready.thresholds),
            stage=STAGE_REFUSED if refusals else exit_ready.stage, evidence_inputs=dict(exit_ready.evidence_inputs),
            blockers=list(exit_ready.blockers), refusals=refusals, owner_gates=list(exit_ready.owner_gates)),
        evidence_not_loaded=not_loaded, as_of=context.as_of)


def read_candidate_lifecycle_v1(cursor: _Cursor, study_id: UUID, trial_id: UUID, *,
                                now: datetime | None = None) -> CandidateLifecycleView:
    """One candidate's lifecycle readiness and exit criteria in the current cycle (read-only, never a decision).

    The chain is read from the stored rows (study -> Decimal rerun -> preregistration
    -> opening -> validation -> states -> forward fills -> exit criteria) and assessed
    by :mod:`trade_platform.candidate_lifecycle_readiness_v1` and
    :mod:`trade_platform.incubation_exit_criteria_v1`. Runner liveness and live/replay
    parity are not derived here; their blockers stay and ``evidence_not_loaded`` says so.
    """
    return _candidate_lifecycle(cursor, _CycleContext(cursor, now or datetime.now(UTC)), study_id, trial_id)


def read_candidate_lifecycles_v1(cursor: _Cursor, *, limit: int,
                                 now: datetime | None = None) -> list[CandidateLifecycleView]:
    """Every candidate the stored evidence names (Decimal selections, states, exit criteria), newest first."""
    cursor.execute(
        "SELECT study_id, trial_id FROM ("
        "SELECT c.study_id, c.trial_id, c.recorded_at AS at FROM strategy_lab_candidate_states c "
        "UNION ALL SELECT e.study_id, e.trial_id, e.recorded_at FROM strategy_lab_incubation_exit_criteria e "
        "UNION ALL SELECT r.study_id, (sel->>'trial_id')::uuid, r.recorded_at FROM strategy_lab_authority_reruns r "
        "CROSS JOIN LATERAL jsonb_array_elements(r.identity->'authoritative_selection'->'selected') sel "
        "WHERE r.selection_status = 'ESTABLISHED') named "
        "GROUP BY study_id, trial_id ORDER BY max(at) DESC, study_id, trial_id LIMIT %s", (limit,))
    pairs = [(row[0], row[1]) for row in cursor.fetchall()]
    if not pairs:
        return []
    context = _CycleContext(cursor, now or datetime.now(UTC))
    return [_candidate_lifecycle(cursor, context, study_id, trial_id) for study_id, trial_id in pairs]


# --- Compare and per-candidate paper evidence -------------------------------------------------

#: The evidence stages a comparison keeps apart, in workflow order. None of them is a validation.
EVIDENCE_STAGES_V1: list[dict[str, str]] = [
    {"stage": "SEARCH", "label": "Search tier", "claim": "SEARCH_NON_AUTHORITATIVE",
     "meaning": "float64 in-sample search over every planned trial; ranks only, never a decision"},
    {"stage": "DECIMAL_RERUN", "label": "Decimal rerun", "claim": "DECIMAL_SELECTION_ESTABLISHED",
     "meaning": "the frozen set recomputed in Decimal reproduces the selection; economics stay gross and T2-conditional"},
    {"stage": "WALK_FORWARD", "label": "Walk-forward development", "claim": "WALK_FORWARD_DEVELOPMENT",
     "meaning": "purged out-of-sample folds before the holdout; development evidence, never validation"},
    {"stage": "VALIDATION", "label": "Holdout validation", "claim": "NONE_VALIDATED",
     "meaning": "the one-shot preregistered holdout (OR-7): INCUBATING or REJECTED at most; "
                "PROFESSIONALLY_VALIDATED is reserved and never produced here"},
]

#: No evidence was loaded by this read (distinct from a loaded assessment that is ``INCOMPLETE``).
NOT_LOADED = "NOT_LOADED"
PARITY_REPORT_NOT_PERSISTED = "NO_PERSISTED_PARITY_REPORT_REPLAY_NOT_RECONSTRUCTED_BY_THIS_READ"
FUNDING_BRIDGES_NOT_LOADED = "FORWARD_FUNDING_BRIDGES_NOT_PERSISTED_NOT_LOADED_BY_THIS_READ"
NO_PAPER_FILL_IN_CYCLE = "CANDIDATE_HAS_NO_PAPER_FILL_IN_THE_CURRENT_CYCLE"
INCUBATION_REPORT_REFUSED_PREFIX = "INCUBATION_REPORT_REFUSED:"


class ParityEvidenceView(BaseModel):
    """Live/replay paper-fill parity for one candidate: the report's own status verbatim, or ``NOT_LOADED``."""

    status: str
    loaded: bool
    reasons: list[str]
    report_hash: str | None
    summary: dict[str, Any]
    live_side: dict[str, Any]
    claim: Literal["READ_ONLY_PARITY_EVIDENCE_NOT_A_PROMOTION_DECISION"] = (
        "READ_ONLY_PARITY_EVIDENCE_NOT_A_PROMOTION_DECISION")

    @model_validator(mode="after")
    def loaded_is_never_conflated(self) -> ParityEvidenceView:
        if self.loaded == (self.status == NOT_LOADED) or (not self.loaded and self.report_hash is not None):
            raise ValueError("parity_not_loaded_must_be_stated_as_not_loaded")
        return self


class FundingEvidenceView(BaseModel):
    """Forward funding evidence for one candidate: the section's state verbatim, or ``NOT_LOADED``. Never a cost."""

    status: str
    loaded: bool
    costable: Literal[False] = False
    cost_status: Literal["NON_COSTABLE"] = "NON_COSTABLE"
    charge: Literal["NOT_CALCULATED"] = "NOT_CALCULATED"
    reasons: list[str]
    completeness_blockers: list[str]
    economic_blockers: list[str]
    evidence_hash: str | None
    section: dict[str, Any] | None

    @model_validator(mode="after")
    def loaded_is_never_conflated(self) -> FundingEvidenceView:
        if self.loaded == (self.status == NOT_LOADED):
            raise ValueError("funding_not_loaded_must_be_stated_as_not_loaded")
        return self


class CandidatePaperEvidenceView(BaseModel):
    """One candidate's paper evidence: incubation facts, live/replay parity and forward funding. Never a decision."""

    study_id: UUID
    study_content_hash: str
    trial_id: UUID
    trial_content_hash: str
    cycle_id: str
    symbol: str | None
    incubation: dict[str, Any] | None
    parity: ParityEvidenceView
    funding: FundingEvidenceView
    claim: Literal["PAPER_EVIDENCE_NOT_A_PROMOTION_DECISION"] = "PAPER_EVIDENCE_NOT_A_PROMOTION_DECISION"
    execution_authority: Literal["NONE"] = "NONE"
    validated_claim: Literal["NONE_VALIDATED"] = "NONE_VALIDATED"
    as_of: datetime


class ComparisonView(BaseModel):
    """The research candidate comparison verbatim, with the stage legend, cycle state and paper evidence per row."""

    claim: Literal["RESEARCH_COMPARISON_NON_AUTHORITATIVE"]
    composite_score: Literal["NONE"]
    holdout: Literal["NOT_READ"]
    validated_claim: Literal["NONE_VALIDATED"] = "NONE_VALIDATED"
    evidence_stages: list[dict[str, str]]
    cycle: CycleView
    comparison: dict[str, Any]
    paper_evidence: list[CandidatePaperEvidenceView]
    as_of: datetime


def parity_evidence_view_v1(report: Any, *, trial_id: str, live_side: dict[str, Any],
                            not_loaded_reasons: list[str] | None = None) -> ParityEvidenceView:
    """A :class:`~trade_platform.paper_fill_parity_v1.FillParityReportV1` (or ``None``) as a terminal view.

    The status is the report's own assessment, rendered generically (whatever value
    the parity module produces); a report that does not re-derive its hash or does
    not name the candidate is refused. ``None`` is ``NOT_LOADED``, never ``INCOMPLETE``.
    """
    if report is None:
        return ParityEvidenceView(status=NOT_LOADED, loaded=False,
                                  reasons=sorted(set(not_loaded_reasons or [PARITY_REPORT_NOT_PERSISTED])),
                                  report_hash=None, summary={}, live_side=live_side)
    identity = dict(report.identity)
    if identity_hash_v1(identity) != str(report.content_hash):
        raise ValueError("stored_parity_report_does_not_rederive")
    if trial_id not in [str(item.get("trial_id")) for item in identity.get("inputs", {}).get("candidates", [])]:
        raise ValueError("parity_report_is_not_of_this_candidate")
    assessment = identity["assessment"]
    return ParityEvidenceView(status=str(assessment["status"]), loaded=True,
                              reasons=[str(reason) for reason in assessment["reasons"]],
                              report_hash=str(report.content_hash), summary=dict(identity.get("summary", {})),
                              live_side=live_side)


def funding_evidence_view_v1(section: dict[str, Any] | None, *, bridges_loaded: bool,
                             not_loaded_reasons: list[str]) -> FundingEvidenceView:
    """A candidate's ``funding_evidence`` report section as a terminal view; always ``NON_COSTABLE``.

    The section must re-derive its ``evidence_hash`` and must not claim to be costable
    (v1 never is; a claim otherwise is refused, not shown). Without loaded bridges the
    status is ``NOT_LOADED`` and the section is kept beside it for its blockers.
    """
    if section is not None:
        body = {key: value for key, value in section.items() if key != "evidence_hash"}
        if identity_hash_v1(body) != section.get("evidence_hash"):
            raise ValueError("funding_evidence_section_does_not_rederive")
        if section.get("costable") is not False:
            raise ValueError("funding_evidence_section_claims_to_be_costable")
    loaded = section is not None and bridges_loaded
    return FundingEvidenceView(
        status=str(section["state"]) if loaded and section is not None else NOT_LOADED, loaded=loaded,
        reasons=[] if loaded else sorted(set(not_loaded_reasons)),
        completeness_blockers=[] if section is None else [str(item) for item in section["completeness_blockers"]],
        economic_blockers=[] if section is None else [str(item) for item in section["economic_blockers"]],
        evidence_hash=None if section is None else str(section["evidence_hash"]), section=section)


class _PaperContext:
    """The cycle's incubation report, built once per request from the stored fills (no caller input)."""

    def __init__(self, cursor: _Cursor, as_of: datetime) -> None:
        from .paper_incubation_report_v1 import PaperIncubationError

        self.cycle = _CycleContext(cursor, as_of)
        self.report: dict[str, Any] | None = None
        self.refusal: str | None = None
        try:
            self.report = incubation_report_v1(self.cycle.fills, self.cycle.cost, as_of=as_of,
                                               resolution=self.cycle.resolution)
        except PaperIncubationError as error:
            self.refusal = f"{INCUBATION_REPORT_REFUSED_PREFIX}{error}"


def _candidate_paper_evidence(cursor: _Cursor, context: _PaperContext, study_id: UUID,
                              trial_id: UUID) -> CandidatePaperEvidenceView:
    cursor.execute("SELECT content_hash, identity FROM strategy_lab_studies WHERE study_id=%s", (study_id,))
    study = cursor.fetchone()
    if study is None:
        raise TerminalObjectNotFound("strategy_lab_study_not_found")
    if identity_hash_v1(_json(study[1])) != str(study[0]).strip():
        raise ValueError("stored_study_identity_does_not_rederive")
    cursor.execute("SELECT content_hash FROM strategy_lab_trials WHERE study_id=%s AND trial_id=%s",
                   (study_id, trial_id))
    trial = cursor.fetchone()
    if trial is None:
        raise TerminalObjectNotFound("strategy_lab_trial_not_found_in_study")
    cursor.execute("SELECT count(*) FROM live_strategy_signals WHERE study_id=%s AND trial_id=%s",
                   (study_id, trial_id))
    signals = int((cursor.fetchone() or (0,))[0])
    study_text, trial_text = str(study_id), str(trial_id)
    fills = [item for item in context.cycle.fills
             if (str(item.get("study_id")), str(item.get("trial_id"))) == (study_text, trial_text)]
    statuses: dict[str, int] = {}
    for item in fills:
        statuses[str(item.get("status"))] = statuses.get(str(item.get("status")), 0) + 1
    live_side = {"live_signals_recorded": signals, "paper_fills_in_cycle": len(fills), "fills_by_status": statuses}
    entry = next((item for item in (context.report or {}).get("candidates", [])
                  if (str(item["study_id"]), str(item["trial_id"])) == (study_text, trial_text)), None)
    funding_reasons = [FUNDING_BRIDGES_NOT_LOADED]
    if context.refusal is not None:
        funding_reasons.append(context.refusal)
    elif entry is None:
        funding_reasons.append(NO_PAPER_FILL_IN_CYCLE)
    incubation = None if entry is None else {
        key: entry[key] for key in ("fills", "fills_by_status", "chain_breaks", "fills_skipped_unknown_position",
                                    "chain_inconsistencies", "open_position", "gross_return_sum", "closed_sides",
                                    "elapsed_days", "required_days", "funding", "cost_complete", "net", "state")
        if key in entry}
    return CandidatePaperEvidenceView(
        study_id=study_id, study_content_hash=str(study[0]).strip(), trial_id=trial_id,
        trial_content_hash=str(trial[0]).strip(), cycle_id=context.cycle.cycle_id,
        symbol=None if entry is None else str(entry["symbol"]), incubation=incubation,
        # No parity report is persisted and the replay is not reconstructed from sealed T4 by a read.
        parity=parity_evidence_view_v1(None, trial_id=trial_text, live_side=live_side),
        funding=funding_evidence_view_v1(None if entry is None else entry.get("funding_evidence"),
                                         bridges_loaded=False, not_loaded_reasons=funding_reasons),
        as_of=context.cycle.as_of)


def read_candidate_paper_evidence_v1(cursor: _Cursor, study_id: UUID, trial_id: UUID, *,
                                     now: datetime | None = None) -> CandidatePaperEvidenceView:
    """One candidate's paper evidence in the current cycle, resolved from stored rows only (read-only)."""
    return _candidate_paper_evidence(cursor, _PaperContext(cursor, now or datetime.now(UTC)), study_id, trial_id)


def read_terminal_comparison_v1(cursor: _Cursor, *, study_ids: list[UUID], sort: Any,
                                now: datetime | None = None) -> ComparisonView:
    """The recorded comparison of the selected studies' frozen sets plus each row's paper evidence.

    Identities are resolved server-side: each study's latest recorded frozen set,
    its reruns and walk-forward reports, every one re-derived by
    :mod:`trade_platform.research_candidate_comparison_v1`. ``sort`` is ``None`` or
    an explicit operator choice; no default ranking exists. The holdout is not read.
    """
    from .research_candidate_comparison_v1 import (
        CandidateComparisonNotFound,
        StudySelectionV1,
        read_candidate_comparison_v1,
    )

    try:
        result = read_candidate_comparison_v1(cursor, studies=[StudySelectionV1(study) for study in study_ids],
                                              sort=sort)
    except CandidateComparisonNotFound as error:
        raise TerminalObjectNotFound(str(error)) from error
    payload = result.payload()
    as_of = now or datetime.now(UTC)
    context = _PaperContext(cursor, as_of)
    paper = [_candidate_paper_evidence(cursor, context, UUID(row["identity"]["study_id"]),
                                       UUID(row["identity"]["trial_id"])) for row in payload["rows"]]
    return ComparisonView(claim=payload["claim"], composite_score=payload["composite_score"],
                          holdout=payload["holdout"], evidence_stages=[dict(item) for item in EVIDENCE_STAGES_V1],
                          cycle=read_cycle_v1(cursor), comparison=payload, paper_evidence=paper, as_of=as_of)


def _counts(cursor: _Cursor, query: str) -> dict[str, int]:
    cursor.execute(query)
    return {str(key): int(count) for key, count in cursor.fetchall()}


def read_terminal_overview_v1(cursor: _Cursor, *, now: datetime | None = None) -> TerminalOverview:
    cursor.execute(
        "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM strategy_lab_trial_queue q "
        "WHERE q.study_id = s.study_id) AND NOT EXISTS (SELECT 1 FROM strategy_lab_trial_queue q "
        "WHERE q.study_id = s.study_id AND q.state NOT IN ('SUCCEEDED','FAILED','CANCELLED'))) "
        "FROM strategy_lab_studies s")
    studies, finished = cursor.fetchone() or (0, 0)
    cycle = read_cycle_v1(cursor)
    reruns = _counts(cursor, "SELECT selection_status, count(*) FROM strategy_lab_authority_reruns GROUP BY 1")
    states = {_STATE_LABEL.get(key, key): value for key, value in _counts(
        cursor, "SELECT state, count(*) FROM strategy_lab_candidate_states GROUP BY 1").items()}
    signals = _counts(cursor, "SELECT authority, count(*) FROM live_strategy_signals GROUP BY 1")
    cursor.execute("SELECT max(decided_at) FROM live_strategy_signals")
    latest = (cursor.fetchone() or (None,))[0]
    fills = _counts(cursor, "SELECT status, count(*) FROM paper_incubation_fills GROUP BY 1")
    accounts: dict[str, int] = {}
    for account in read_accounts_v1(cursor):
        accounts[account.policy_status] = accounts.get(account.policy_status, 0) + 1
    from .research_watchlist_v1 import read_latest_active_watchlist_v1

    watchlist = read_latest_active_watchlist_v1(cursor)
    gates = [
        OwnerGateView(gate="OR-7", topic=OWNER_GATES_V1[0]["topic"],
                      status="SATISFIED" if cycle.holdout_state == "OPENED" else "OPEN",
                      evidence=f"holdout {cycle.holdout_state.lower()} for {cycle.cycle_id}"),
        OwnerGateView(gate="OR-11", topic=OWNER_GATES_V1[1]["topic"],
                      status="SATISFIED" if accounts.get("ACTIVE") else "OPEN",
                      evidence=f"{accounts.get('ACTIVE', 0)} active account policies"),
        OwnerGateView(gate="OR-9", topic=OWNER_GATES_V1[2]["topic"],
                      status="SATISFIED" if watchlist is not None else "OPEN",
                      evidence="no ACTIVE watch list recorded" if watchlist is None else
                      f"watch list {watchlist.watchlist_id} ({len(watchlist.entries)} candidates)"),
        OwnerGateView(gate="OR-6 fee schedule", topic=OWNER_GATES_V1[3]["topic"], status="OPEN",
                      evidence="every study and report runs under the gross cost policy"),
    ]
    return TerminalOverview(
        generated_at=now or datetime.now(UTC), studies=int(studies), finished_studies=int(finished), reruns=reruns,
        cycle=cycle, candidate_states=states, signals=signals, latest_signal_at=latest, incubation_fills=fills,
        accounts=accounts, owner_gates=gates, authority_legend=dict(AUTHORITY_LEGEND_V1))


__all__ = [
    "AUTHORITY_LEGEND_V1",
    "EVIDENCE_STAGES_V1",
    "NOT_LOADED",
    "AccountView",
    "CandidatePaperEvidenceView",
    "CandidateStateView",
    "ComparisonView",
    "CycleView",
    "FundingEvidenceView",
    "IncubationView",
    "LiveSignalView",
    "ParityEvidenceView",
    "RerunView",
    "TerminalOverview",
    "ValidationView",
    "WalkForwardFoldView",
    "WalkForwardView",
    "funding_evidence_view_v1",
    "parity_evidence_view_v1",
    "read_accounts_v1",
    "read_candidate_paper_evidence_v1",
    "read_cycle_v1",
    "read_incubation_v1",
    "read_live_signals_v1",
    "read_reruns_v1",
    "read_terminal_comparison_v1",
    "read_terminal_overview_v1",
    "read_validation_v1",
    "read_walk_forward_v1",
]
