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
* per-candidate paper evidence restates the latest *stored* live/replay fill
  parity assessment and forward funding record known at the read's ``as_of``
  (:mod:`trade_platform.paper_evidence_store_v1`) verbatim; nothing stored is
  ``NOT_LOADED`` (never ``INCOMPLETE``, which is a loaded assessment's own
  verdict), a stored row that does not re-derive is ``REFUSED`` with its code
  (never ``NOT_LOADED``, never a 500), and funding is always ``NON_COSTABLE``
  -- stored funding never becomes an economics cost;
* per-candidate paper economics restate :mod:`trade_platform.paper_economics_v1`
  verbatim, projected server-side from the cycle's stored fills under the
  cycle's stored OR-6 policy, the owner approval being only the opened
  ``AUTHORIZED`` preregistration packet. Realized gross and unrealized evidence
  stay apart; unrealized is ``AVAILABLE`` only with a proven T4 mark and a
  server-installed explicit price policy; without it an open position's unrealized
  figure is ``UNAVAILABLE`` with its exact reason. Fees, slippage, funding,
  proposed sensitivity rows (``in_net: false``, only from the stored policy)
  and net are each shown with their own status; every figure is
  :data:`~trade_platform.paper_economics_v1.UNITS_V1`, never money. No caller
  input reaches the projection;
* per-candidate paper risk restates
  :func:`~trade_platform.paper_risk_assessment_v1.read_paper_risk_assessment_v1`
  verbatim (``ALLOWED`` / ``BLOCKED`` / ``UNRESOLVED``, sizing only when
  ``ALLOWED``) for a *recorded* request only: the account the candidate's
  ``AUTHORIZED`` exit criteria bind (or, on the paper-account read, the named
  account) and the candidate's latest stored ``INCUBATING`` signal target,
  assessed at that signal's decision instant. Without either the risk view is
  ``NOT_LOADED`` with its reason -- never an invented target, account or price.
  The current authoritative policy (latest version known now, whatever its
  status) is shown beside it and is ``ACTIVE`` only when the resolver says so.

Open owner gates are listed as gates (``OPEN``), never as defaults.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, model_validator

from .paper_economics_v1 import (
    CLAIM_V1 as ECONOMICS_CLAIM_V1,
)
from .paper_economics_v1 import (
    COMPONENT_STATUSES_V1,
    MARK_KINDS_V1,
    NET_CONFIRMED,
    STATUS_NON_COSTABLE_EVIDENCE,
    STATUS_VERIFIED,
    UNITS_V1,
    UNREALIZED_AVAILABLE,
)
from .paper_incubation_report_v1 import FILL_STATUS_FILLED, incubation_report_v1
from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1, identity_hash_v1

if TYPE_CHECKING:
    from .terminal_paper_prices_v1 import TerminalPaperPricesV1


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


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
        cursor.execute("SELECT cycle_id, preregistration_hash FROM strategy_lab_holdout_openings "
                       "WHERE cycle_id=%s AND opened_at <= %s", (self.cycle_id, as_of))
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
        fill_rows = cycle_fill_rows_v1(cursor, self.cycle_id)[0]
        hashes = [str(row[0]).strip() for row in fill_rows]
        recorded = {}
        if hashes:
            cursor.execute("SELECT content_hash, recorded_at FROM paper_incubation_fills "
                           "WHERE content_hash = ANY(%s)", (hashes,))
            recorded = {str(row[0]).strip(): row[1] for row in cursor.fetchall()}
        for content_hash, raw in fill_rows:
            identity = _json(raw)
            if identity_hash_v1(identity) != str(content_hash).strip():
                raise ValueError("stored_fill_identity_does_not_rederive")
            if str(content_hash).strip() not in recorded:
                raise ValueError("stored_fill_recording_time_missing")
            if recorded[str(content_hash).strip()] > as_of:
                continue
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
#: A stored row exists but did not re-derive: refused, never shown as absent nor as its verbatim status.
STORED_EVIDENCE_REFUSED = "REFUSED"
STORED_EVIDENCE_REFUSED_PREFIX = "STORED_EVIDENCE_REFUSED:"
PARITY_REPORT_NOT_PERSISTED = "NO_STORED_PARITY_ASSESSMENT_FOR_CANDIDATE_IN_CYCLE"
FUNDING_BRIDGES_NOT_LOADED = "NO_STORED_FUNDING_EVIDENCE_FOR_CANDIDATE_IN_CYCLE"
NO_PAPER_FILL_IN_CYCLE = "CANDIDATE_HAS_NO_PAPER_FILL_IN_THE_CURRENT_CYCLE"
INCUBATION_REPORT_REFUSED_PREFIX = "INCUBATION_REPORT_REFUSED:"
_UNLOADED_STATUSES = (NOT_LOADED, STORED_EVIDENCE_REFUSED)


def _check_unloaded(status: str, loaded: bool, reasons: list[str], hashes: tuple[object, ...], code: str) -> None:
    """NOT_LOADED, REFUSED and a loaded verbatim status never mix (shared by the parity and funding views)."""
    if loaded == (status in _UNLOADED_STATUSES):
        raise ValueError(code)
    refused = [reason for reason in reasons if reason.startswith(STORED_EVIDENCE_REFUSED_PREFIX)]
    if status == STORED_EVIDENCE_REFUSED and (not reasons or len(refused) != len(reasons)):
        raise ValueError(code)
    if status != STORED_EVIDENCE_REFUSED and refused:
        raise ValueError(code)
    if not loaded and (not reasons or any(item is not None for item in hashes)):
        raise ValueError(code)


class ParityEvidenceView(BaseModel):
    """Live/replay paper-fill parity for one candidate: the stored assessment's status verbatim, ``NOT_LOADED``
    (nothing stored) or ``REFUSED`` (a stored row that does not re-derive)."""

    status: str
    loaded: bool
    reasons: list[str]
    report_hash: str | None
    summary: dict[str, Any]
    live_side: dict[str, Any]
    assessment_id: UUID | None = None
    evidence_as_of: datetime | None = None
    recorded_at: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    claim: Literal["READ_ONLY_PARITY_EVIDENCE_NOT_A_PROMOTION_DECISION"] = (
        "READ_ONLY_PARITY_EVIDENCE_NOT_A_PROMOTION_DECISION")

    @model_validator(mode="after")
    def loaded_is_never_conflated(self) -> ParityEvidenceView:
        _check_unloaded(self.status, self.loaded, self.reasons,
                        (self.report_hash, self.assessment_id, self.evidence_as_of, self.recorded_at),
                        "parity_not_loaded_must_be_stated_as_not_loaded")
        return self


class FundingEvidenceView(BaseModel):
    """Forward funding evidence for one candidate: the stored section's state verbatim, ``NOT_LOADED`` or
    ``REFUSED``. Never a cost."""

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
    record_id: UUID | None = None
    evidence_as_of: datetime | None = None
    recorded_at: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    bridge_hashes: dict[str, str] = {}

    @model_validator(mode="after")
    def loaded_is_never_conflated(self) -> FundingEvidenceView:
        # A NOT_LOADED view may keep the cycle report's own section beside it for its blockers; its hash is
        # then that section's, so only the stored-row identities must be absent when nothing was loaded.
        _check_unloaded(self.status, self.loaded, self.reasons,
                        (self.record_id, self.evidence_as_of, self.recorded_at),
                        "funding_not_loaded_must_be_stated_as_not_loaded")
        if self.status == STORED_EVIDENCE_REFUSED and (self.section is not None or self.evidence_hash is not None):
            raise ValueError("funding_refused_carries_no_evidence")
        if self.loaded and (self.section is None or self.evidence_hash is None or self.record_id is None):
            raise ValueError("funding_loaded_is_a_stored_record")
        return self


#: Economics section states: nothing projected (no fill / report refused upstream), the projection refused
#: its stored inputs, or the module's projection restated.
ECONOMICS_NOT_LOADED: Final = "NOT_LOADED"
ECONOMICS_REFUSED: Final = "REFUSED"
ECONOMICS_PROJECTED: Final = "PROJECTED"
NO_STORED_PROVEN_MARK = "NO_STORED_PROVEN_T4_MARK_SOURCE_READ_BY_THIS_TERMINAL"
PAPER_ECONOMICS_REFUSED_PREFIX = "PAPER_ECONOMICS_REFUSED:"
#: Risk-view reasons when no recorded request exists (never an invented target, account or price).
RISK_NO_STORED_TARGET = "NO_STORED_INCUBATING_SIGNAL_TARGET_FOR_CANDIDATE_IN_CYCLE"
RISK_NO_BOUND_ACCOUNT = "NO_ACCOUNT_BOUND_BY_AUTHORIZED_EXIT_CRITERIA"
RISK_BOUND_POLICY_NOT_RECORDED = "BOUND_ACCOUNT_POLICY_VERSION_NOT_RECORDED"
RISK_EXIT_CRITERIA_REFUSED_PREFIX = "EXIT_CRITERIA_REGISTRY_REFUSED:"
RISK_VERDICTS_V1 = ("ALLOWED", "BLOCKED", "UNRESOLVED")


class EconomicsComponentView(BaseModel):
    """One cost component (fees, slippage or funding) with its own status, verbatim from the module."""

    status: str
    value: str | None
    by_scenario: dict[str, Any] | None = None
    reasons: list[str]
    detail: dict[str, Any]

    @model_validator(mode="after")
    def status_is_the_modules(self) -> EconomicsComponentView:
        if self.status not in COMPONENT_STATUSES_V1:
            raise ValueError("economics_component_status_unknown")
        if self.status != STATUS_VERIFIED and (self.value is not None or self.by_scenario is not None):
            raise ValueError("economics_unverified_component_carries_no_figure")
        if self.status != STATUS_VERIFIED and not self.reasons:
            raise ValueError("economics_unverified_component_states_its_reasons")
        return self


class UnrealizedEvidenceView(BaseModel):
    """Unrealized evidence of an open position: ``AVAILABLE`` only with a proven T4 mark. Never part of net."""

    status: Literal["AVAILABLE", "UNAVAILABLE", "NOT_APPLICABLE_FLAT"]
    value: str | None
    reasons: list[str]
    exposure: int | None
    mark: dict[str, Any] | None
    detail: dict[str, Any]
    in_net: Literal[False] = False

    @model_validator(mode="after")
    def available_only_with_a_proven_mark(self) -> UnrealizedEvidenceView:
        proven = self.mark is not None and self.mark.get("evidence_kind") in MARK_KINDS_V1
        if (self.status == UNREALIZED_AVAILABLE) != (self.value is not None) or (
                self.status == UNREALIZED_AVAILABLE and not proven):
            raise ValueError("unrealized_is_available_only_with_a_proven_t4_mark")
        if self.status == "UNAVAILABLE" and not self.reasons:
            raise ValueError("unrealized_unavailable_states_its_reasons")
        return self


class SensitivityRowView(BaseModel):
    """A proposed, unapproved slippage scenario: labelled, never in net."""

    name: str
    status: Literal["PROPOSED_SENSITIVITY_SCENARIO"]
    source: str
    cost_bps_per_side: str
    rationale: str
    value: str
    in_net: Literal[False]
    scenario_hash: str


class EconomicsNetView(BaseModel):
    status: Literal["CONFIRMED", "NOT_COSTABLE", "INCOMPLETE"]
    scope: str
    by_scenario: dict[str, str] | None
    reasons: list[str]

    @model_validator(mode="after")
    def figure_only_when_confirmed(self) -> EconomicsNetView:
        if (self.status == NET_CONFIRMED) != (self.by_scenario is not None):
            raise ValueError("net_figure_only_when_confirmed")
        if self.status != NET_CONFIRMED and not self.reasons:
            raise ValueError("net_not_confirmed_states_its_reasons")
        return self


class PaperEconomicsSectionView(BaseModel):
    """One candidate's paper economics by component (``paper_economics_v1`` verbatim). Units are never money.

    ``NOT_LOADED`` (nothing to project), ``REFUSED`` (the projection refused its
    stored inputs) and ``PROJECTED`` are distinct; a projected net is only ever
    ``CONFIRMED`` / ``NOT_COSTABLE`` / ``INCOMPLETE`` as the module decided.
    """

    status: Literal["NOT_LOADED", "REFUSED", "PROJECTED"]
    reasons: list[str]
    units: Literal["RETURN_PER_UNIT_NOTIONAL_SUMMED_NOT_COMPOUNDED_PENDING_OR_11"] = (
        "RETURN_PER_UNIT_NOTIONAL_SUMMED_NOT_COMPOUNDED_PENDING_OR_11")
    money: Literal["NONE_NOT_A_CURRENCY_AMOUNT"] = "NONE_NOT_A_CURRENCY_AMOUNT"
    sign_convention: str | None = None
    economics_hash: str | None = None
    economics_id: UUID | None = None
    symbol: str | None = None
    cost_policy: dict[str, Any] | None = None
    cost_approval: dict[str, Any] | None = None
    exposure: dict[str, Any] | None = None
    gross_realized: dict[str, Any] | None = None
    gross_unrealized: UnrealizedEvidenceView | None = None
    unrealized_mark_source: str = NO_STORED_PROVEN_MARK
    price_resolution: dict[str, Any] | None = None
    fees: EconomicsComponentView | None = None
    slippage: EconomicsComponentView | None = None
    funding: EconomicsComponentView | None = None
    sensitivity_rows: list[SensitivityRowView] = []
    net: EconomicsNetView | None = None
    settlement_boundaries: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    claim: Literal["PAPER_ECONOMICS_PROJECTION_RESEARCH_ONLY_NOT_VALIDATED"] = (
        "PAPER_ECONOMICS_PROJECTION_RESEARCH_ONLY_NOT_VALIDATED")

    @model_validator(mode="after")
    def states_are_never_conflated(self) -> PaperEconomicsSectionView:
        parts = (self.economics_hash, self.gross_realized, self.gross_unrealized, self.fees, self.slippage,
                 self.funding, self.net)
        if self.status != ECONOMICS_PROJECTED:
            if any(part is not None for part in parts) or self.sensitivity_rows or not self.reasons:
                raise ValueError("economics_not_projected_carries_no_figure_and_states_why")
            return self
        fees, slippage, funding, net = self.fees, self.slippage, self.funding, self.net
        if (any(part is None for part in parts) or fees is None or slippage is None or funding is None
                or net is None or self.reasons):
            raise ValueError("economics_projection_is_complete_and_restated_verbatim")
        if funding.status != STATUS_NON_COSTABLE_EVIDENCE:
            raise ValueError("funding_is_never_a_charge_in_v1")
        verified = all(item.status == STATUS_VERIFIED for item in (fees, slippage, funding))
        if net.status == NET_CONFIRMED and not verified:
            raise ValueError("net_confirmed_requires_every_component_verified")
        return self


class AccountPolicyResolutionView(BaseModel):
    """An account's authoritative policy version at ``as_of`` (``resolve_account_policy_v1``). Never an activation.

    ``ACTIVE`` only when the resolver finds the latest known version ``ACTIVE``
    with nothing blocking or unresolved; an ``UNCONFIGURED`` latest version is
    ``UNRESOLVED`` with every ``MISSING_OWNER_*_OR_11`` reason carried.
    """

    account_id: str
    as_of: datetime
    status: Literal["ACTIVE", "UNRESOLVED", "BLOCKED"]
    policy_status: str | None
    policy_version_id: str | None
    policy_content_hash: str | None
    policy_recorded_at: str | None
    approved_by: str | None
    approved_on: str | None
    account: dict[str, Any] | None
    not_applicable_controls: list[str]
    versions_recorded: int
    versions_known_at_as_of: int
    blocking: list[str]
    unresolved: list[str]
    integrity_failures: list[dict[str, str]]
    claim: Literal["ACCOUNT_POLICY_RESOLUTION_NEVER_ACTIVATES_AN_ACCOUNT"] = (
        "ACCOUNT_POLICY_RESOLUTION_NEVER_ACTIVATES_AN_ACCOUNT")

    @model_validator(mode="after")
    def active_only_when_resolved(self) -> AccountPolicyResolutionView:
        usable = self.policy_status == "ACTIVE" and not self.blocking and not self.unresolved
        expected = "BLOCKED" if self.blocking else "ACTIVE" if usable else "UNRESOLVED"
        if self.status != expected or (expected == "UNRESOLVED" and not self.unresolved):
            raise ValueError("account_policy_is_active_only_when_the_resolver_says_so")
        return self


class PaperRiskView(BaseModel):
    """A recorded paper target under the account's stored policy (``paper_risk_assessment_v1`` verbatim).

    ``NOT_LOADED`` means no recorded request exists (no stored target, no bound
    account) -- never ``BLOCKED`` or ``UNRESOLVED``, which are the module's own
    verdicts. Sizing is present only for ``ALLOWED``.
    """

    status: Literal["NOT_LOADED", "ALLOWED", "BLOCKED", "UNRESOLVED"]
    loaded: bool
    reasons: list[str]
    account_id: str | None
    account_source: Literal["AUTHORIZED_EXIT_CRITERIA_BINDING", "OPERATOR_SELECTED_ACCOUNT", "NONE"]
    requested_target: dict[str, Any] | None
    assessed_as_of: datetime | None
    policy_resolution: dict[str, Any] | None
    current_policy: AccountPolicyResolutionView | None
    order_action: str | None
    controls: list[dict[str, Any]]
    blocking_reasons: list[str]
    unresolved_reasons: list[str]
    sizing: dict[str, Any] | None
    sizing_absent_reasons: list[str]
    assessment_hash: str | None
    assessment_id: str | None
    execution_authority: Literal[False] = False
    claim: Literal["PAPER_RISK_ASSESSMENT_NOT_EXECUTION_AUTHORITY"] = "PAPER_RISK_ASSESSMENT_NOT_EXECUTION_AUTHORITY"

    @model_validator(mode="after")
    def not_loaded_is_never_a_verdict(self) -> PaperRiskView:
        if self.loaded == (self.status == NOT_LOADED):
            raise ValueError("risk_not_loaded_must_be_stated_as_not_loaded")
        if not self.loaded:
            if (self.assessment_hash is not None or self.sizing is not None or self.controls
                    or self.order_action is not None or not self.reasons):
                raise ValueError("risk_not_loaded_carries_no_assessment_and_states_why")
            return self
        if self.assessment_hash is None or self.requested_target is None or self.account_id is None:
            raise ValueError("risk_verdict_needs_its_assessment_target_and_account")
        if self.sizing is not None and self.status != "ALLOWED":
            raise ValueError("sizing_only_when_allowed")
        if self.status == "BLOCKED" and not self.blocking_reasons:
            raise ValueError("blocked_states_its_reasons")
        if self.status == "UNRESOLVED" and (self.blocking_reasons or not self.unresolved_reasons):
            raise ValueError("unresolved_states_its_reasons")
        return self


class AccountRiskView(BaseModel):
    """One named account: its current authoritative policy and each candidate's latest recorded target assessed."""

    account_id: str
    as_of: datetime
    cycle_id: str
    current_policy: AccountPolicyResolutionView
    candidates: list[PaperRiskView]
    reasons: list[str]
    execution_authority: Literal[False] = False
    claim: Literal["PAPER_RISK_ASSESSMENT_NOT_EXECUTION_AUTHORITY"] = "PAPER_RISK_ASSESSMENT_NOT_EXECUTION_AUTHORITY"


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
    economics: PaperEconomicsSectionView
    risk: PaperRiskView
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


def _verified_section(section: dict[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in section.items() if key != "evidence_hash"}
    if identity_hash_v1(body) != section.get("evidence_hash"):
        raise ValueError("funding_evidence_section_does_not_rederive")
    if section.get("costable") is not False:
        raise ValueError("funding_evidence_section_claims_to_be_costable")
    return section


def funding_evidence_view_v1(section: dict[str, Any] | None, *, not_loaded_reasons: list[str]) -> FundingEvidenceView:
    """``NOT_LOADED`` funding (no stored record), keeping the cycle report's own section beside it for its blockers.

    The section must re-derive its ``evidence_hash`` and must not claim to be costable
    (v1 never is; a claim otherwise is refused, not shown). Loaded funding comes only
    from a stored record (:func:`stored_funding_evidence_view_v1`).
    """
    if section is not None:
        _verified_section(section)
    return FundingEvidenceView(
        status=NOT_LOADED, loaded=False, reasons=sorted(set(not_loaded_reasons)),
        completeness_blockers=[] if section is None else [str(item) for item in section["completeness_blockers"]],
        economic_blockers=[] if section is None else [str(item) for item in section["economic_blockers"]],
        evidence_hash=None if section is None else str(section["evidence_hash"]), section=section)


def stored_funding_evidence_view_v1(stored: Any, *, study_id: str, trial_id: str,
                                    cycle_id: str) -> FundingEvidenceView:
    """A stored, re-derived funding record (``paper_evidence_store_v1``): its state verbatim, always NON_COSTABLE."""
    if (stored.cycle_id, str(stored.study_id), str(stored.trial_id)) != (cycle_id, study_id, trial_id):
        return refused_funding_view_v1("STORED_ROW_NOT_OF_THIS_CANDIDATE")
    section = _verified_section(dict(stored.evidence))
    if (section.get("state"), section.get("evidence_hash")) != (stored.state, stored.evidence_hash):
        raise ValueError("stored_funding_record_contradicts_its_section")
    return FundingEvidenceView(
        status=str(stored.state), loaded=True, reasons=[],
        completeness_blockers=[str(item) for item in stored.completeness_blockers],
        economic_blockers=[str(item) for item in stored.economic_blockers],
        evidence_hash=str(stored.evidence_hash), section=section, record_id=stored.record_id,
        evidence_as_of=stored.as_of, recorded_at=stored.recorded_at, window_start=stored.window_start,
        window_end=stored.window_end, bridge_hashes={str(k): str(v) for k, v in dict(stored.bridge_hashes).items()})


def refused_funding_view_v1(code: str) -> FundingEvidenceView:
    return FundingEvidenceView(status=STORED_EVIDENCE_REFUSED, loaded=False,
                               reasons=[f"{STORED_EVIDENCE_REFUSED_PREFIX}{code}"], completeness_blockers=[],
                               economic_blockers=[], evidence_hash=None, section=None)


def stored_parity_evidence_view_v1(stored: Any, *, study_id: str, trial_id: str, cycle_id: str,
                                   live_side: dict[str, Any]) -> ParityEvidenceView:
    """A stored, re-derived parity assessment (``paper_evidence_store_v1``): its status verbatim with its instants."""
    if (stored.cycle_id, str(stored.study_id), str(stored.trial_id)) != (cycle_id, study_id, trial_id):
        return refused_parity_view_v1("STORED_ROW_NOT_OF_THIS_CANDIDATE", live_side=live_side)
    return ParityEvidenceView(
        status=str(stored.status), loaded=True, reasons=[str(reason) for reason in stored.reasons],
        report_hash=str(stored.report_content_hash), summary=dict(dict(stored.identity).get("summary", {})),
        live_side=live_side, assessment_id=stored.assessment_id, evidence_as_of=stored.as_of,
        recorded_at=stored.recorded_at, window_start=stored.window_start, window_end=stored.window_end)


def refused_parity_view_v1(code: str, *, live_side: dict[str, Any]) -> ParityEvidenceView:
    return ParityEvidenceView(status=STORED_EVIDENCE_REFUSED, loaded=False,
                              reasons=[f"{STORED_EVIDENCE_REFUSED_PREFIX}{code}"], report_hash=None, summary={},
                              live_side=live_side)


def _component(raw: dict[str, Any], *, value_key: str | None = "value") -> EconomicsComponentView:
    known = {"status", "value", "by_scenario", "reasons"}
    return EconomicsComponentView(
        status=str(raw["status"]), value=None if value_key is None else raw.get(value_key),
        by_scenario=raw.get("by_scenario"), reasons=[str(reason) for reason in raw["reasons"]],
        detail={key: value for key, value in raw.items() if key not in known})


def economics_section_view_v1(result: Any, *, study_id: str, trial_id: str) -> PaperEconomicsSectionView:
    """A verified :class:`~trade_platform.paper_economics_v1.PaperEconomicsV1` restated for one candidate.

    The payload must re-derive its content hash and name the candidate exactly
    once; every component's status and reasons are carried verbatim.
    """
    from .paper_economics_v1 import verify_paper_economics_v1

    verify_paper_economics_v1(result)
    payload = result.payload
    if payload["units"] != UNITS_V1 or payload["claim"] != ECONOMICS_CLAIM_V1:
        raise ValueError("paper_economics_units_or_claim_unknown")
    mine = [item for item in payload["candidates"] if (item["study_id"], item["trial_id"]) == (study_id, trial_id)]
    if len(mine) != 1:
        raise ValueError("paper_economics_does_not_name_the_candidate_once")
    entry = mine[0]
    unrealized = entry["gross_unrealized"]
    return PaperEconomicsSectionView(
        status=ECONOMICS_PROJECTED, reasons=[], sign_convention=str(payload["sign_convention"]),
        economics_hash=result.content_hash, economics_id=result.economics_id, symbol=str(entry["symbol"]),
        cost_policy=dict(payload["cost_policy"]), cost_approval=payload["cost_approval"],
        exposure=dict(entry["exposure"]), gross_realized=dict(entry["gross_realized"]),
        gross_unrealized=UnrealizedEvidenceView(
            status=unrealized["status"], value=unrealized["value"], reasons=list(unrealized["reasons"]),
            exposure=unrealized["exposure"], mark=unrealized["mark"],
            detail={key: value for key, value in unrealized.items()
                    if key not in {"status", "value", "reasons", "exposure", "mark"}}),
        fees=_component(entry["fees"]), slippage=_component(entry["slippage"], value_key=None),
        funding=_component(entry["funding"]),
        sensitivity_rows=[SensitivityRowView(**row) for row in entry["sensitivity_rows"]],
        net=EconomicsNetView(**entry["net"]), settlement_boundaries=dict(entry["settlement_boundaries"]),
        provenance={**entry["provenance"], "incubation_report_hash": payload["incubation_report_hash"],
                    "as_of": payload["as_of"]})


def _economics_not_projected(status: Literal["NOT_LOADED", "REFUSED"], reasons: list[str]) -> PaperEconomicsSectionView:
    return PaperEconomicsSectionView(status=status, reasons=sorted(set(reasons)))


def _signal_target(row: tuple[Any, ...], cycle_id: str) -> dict[str, Any]:
    """A stored INCUBATING signal row ``(signal_id, content_hash, identity, decided_at, study_id, trial_id,
    symbol, target_from, target_to)`` as the recorded paper target; refused unless it re-derives."""
    identity = _json(row[2])
    content_hash = str(row[1]).strip()
    if identity_hash_v1(identity) != content_hash:
        raise ValueError("stored_signal_identity_does_not_rederive")
    candidate = identity.get("candidate") or {}
    if (identity.get("authority") != "INCUBATING" or str(candidate.get("study_id")) != str(row[4])
            or str(candidate.get("trial_id")) != str(row[5]) or identity.get("symbol") != str(row[6])
            or int(identity.get("target_from")) != int(row[7]) or int(identity.get("target_to")) != int(row[8])):
        raise ValueError("stored_signal_row_contradicts_its_identity")
    knowledge = (identity.get("evidence") or {}).get("complete_market_knowledge_micros")
    return {"source": "LATEST_STORED_INCUBATING_SIGNAL", "cycle_id": cycle_id, "signal_id": str(row[0]),
            "signal_content_hash": content_hash, "study_id": str(row[4]), "trial_id": str(row[5]),
            "symbol": str(row[6]), "target_from": int(row[7]), "target_to": int(row[8]),
            "decided_at": row[3].astimezone(UTC).isoformat(),
            "market_knowledge_micros": None if knowledge is None else int(knowledge)}


_SIGNAL_TARGET_SELECT = (
    "SELECT s.signal_id, s.content_hash, s.identity, s.decided_at, s.study_id, s.trial_id, s.symbol, "
    "s.target_from, s.target_to FROM live_strategy_signals s JOIN strategy_lab_holdout_validations v "
    "ON v.validation_hash = s.identity->>'authority_evidence_hash' "
    "WHERE v.cycle_id = %s AND s.authority = 'INCUBATING' AND s.decided_at <= %s")


def _latest_signal_target(cursor: _Cursor, cycle_id: str, study_id: UUID, trial_id: UUID,
                          as_of: datetime) -> dict[str, Any] | None:
    cursor.execute(f"{_SIGNAL_TARGET_SELECT} AND s.study_id = %s AND s.trial_id = %s "
                   "ORDER BY s.decided_at DESC, s.signal_id DESC LIMIT 1", (cycle_id, as_of, study_id, trial_id))
    row = cursor.fetchone()
    return None if row is None else _signal_target(row, cycle_id)


def _policy_resolution_view(resolution: Any) -> AccountPolicyResolutionView:
    payload = resolution.payload()
    status = "BLOCKED" if resolution.blocking else "ACTIVE" if resolution.usable else "UNRESOLVED"
    return AccountPolicyResolutionView(
        account_id=resolution.account_id, as_of=resolution.as_of, status=status,  # type: ignore[arg-type]
        policy_status=payload["policy_status"], policy_version_id=payload["policy_version_id"],
        policy_content_hash=payload["policy_content_hash"], policy_recorded_at=payload["policy_recorded_at"],
        approved_by=payload["approved_by"], approved_on=payload["approved_on"], account=payload["account"],
        not_applicable_controls=list(payload["not_applicable_controls"]),
        versions_recorded=payload["versions_recorded"], versions_known_at_as_of=payload["versions_known_at_as_of"],
        blocking=list(resolution.blocking), unresolved=list(resolution.unresolved),
        integrity_failures=list(payload["integrity_failures"]))


def read_account_policy_resolution_v1(cursor: _Cursor, account_id: str, *, as_of: datetime,
                                      expected_policy_version_id: str | None = None) -> AccountPolicyResolutionView:
    """The account's authoritative policy at ``as_of``, re-derived from stored rows (read-only)."""
    from .paper_risk_assessment_v1 import resolve_account_policy_v1

    cursor.execute("SELECT kind, display_name, base_currency FROM account_contexts WHERE account_id=%s",
                   (account_id,))
    account_row = cursor.fetchone()
    cursor.execute("SELECT policy_version_id, content_hash, account_id, status, identity, recorded_at "
                   "FROM account_policy_versions WHERE account_id=%s ORDER BY recorded_at, policy_version_id",
                   (account_id,))
    return _policy_resolution_view(resolve_account_policy_v1(
        account_id, account_row, cursor.fetchall(), as_of=as_of,
        expected_policy_version_id=expected_policy_version_id))


def _risk_not_loaded(reasons: list[str], *, account_id: str | None = None,
                     source: Literal["AUTHORIZED_EXIT_CRITERIA_BINDING", "OPERATOR_SELECTED_ACCOUNT", "NONE"] = "NONE",
                     target: dict[str, Any] | None = None,
                     current: AccountPolicyResolutionView | None = None) -> PaperRiskView:
    return PaperRiskView(
        status="NOT_LOADED", loaded=False, reasons=sorted(set(reasons)), account_id=account_id,
        account_source=source, requested_target=target, assessed_as_of=None, policy_resolution=None,
        current_policy=current, order_action=None, controls=[], blocking_reasons=[], unresolved_reasons=[],
        sizing=None, sizing_absent_reasons=[], assessment_hash=None, assessment_id=None)


def _assess_risk(cursor: _Cursor, *, account_id: str, target: dict[str, Any], cycle_id: str,
                 source: Literal["AUTHORIZED_EXIT_CRITERIA_BINDING", "OPERATOR_SELECTED_ACCOUNT"],
                 expected_policy_version_id: str | None, current: AccountPolicyResolutionView) -> PaperRiskView:
    """The module's assessment of a *recorded* target, at that target's decision instant (no look-ahead)."""
    from .paper_risk_assessment_v1 import (
        ExecutionContextV1,
        PaperRiskRequestV1,
        read_paper_risk_assessment_v1,
    )

    decided_at = datetime.fromisoformat(target["decided_at"])
    knowledge = target["market_knowledge_micros"]
    request = PaperRiskRequestV1(
        account_id=account_id, study_id=target["study_id"], trial_id=target["trial_id"], symbol=target["symbol"],
        target_to=target["target_to"], as_of=decided_at, execution_context=ExecutionContextV1.PAPER_INCUBATION,
        expected_policy_version_id=expected_policy_version_id,
        market_knowledge_at=None if knowledge is None else _EPOCH + timedelta(microseconds=knowledge),
        # No reference price is stored for a signal; the module then withholds sizing (REFERENCE_PRICE_MISSING).
        reference_price=None, reference_price_known_at=None)
    assessment = read_paper_risk_assessment_v1(cursor, request, cycle_id=cycle_id)
    verdict = str(assessment["verdict"])
    if verdict not in RISK_VERDICTS_V1 or assessment["execution_authority"] is not False:
        raise ValueError("paper_risk_assessment_verdict_unknown")
    body = {key: value for key, value in assessment.items() if key not in {"content_hash", "assessment_id"}}
    if identity_hash_v1(body) != assessment["content_hash"]:
        raise ValueError("paper_risk_assessment_does_not_rederive")
    return PaperRiskView(
        status=verdict, loaded=True,  # type: ignore[arg-type]
        reasons=[*assessment["blocking_reasons"], *assessment["unresolved_reasons"]], account_id=account_id,
        account_source=source, requested_target=target, assessed_as_of=decided_at,
        policy_resolution=dict(assessment["resolution"]), current_policy=current,
        order_action=str(assessment["order_action"]), controls=list(assessment["controls"]),
        blocking_reasons=list(assessment["blocking_reasons"]), unresolved_reasons=list(assessment["unresolved_reasons"]),
        sizing=assessment["sizing"], sizing_absent_reasons=list(assessment["sizing_absent_reasons"]),
        assessment_hash=str(assessment["content_hash"]), assessment_id=str(assessment["assessment_id"]))


def _candidate_risk(cursor: _Cursor, context: _PaperContext, study_id: UUID, trial_id: UUID) -> PaperRiskView:
    """The candidate's recorded target under the account its AUTHORIZED exit criteria bind, else NOT_LOADED."""
    from .incubation_exit_criteria_v1 import (
        STATUS_AUTHORIZED,
        ExitCriteriaError,
        resolve_exit_criteria_v1,
    )

    cycle_id, as_of = context.cycle.cycle_id, context.cycle.as_of
    target = _latest_signal_target(cursor, cycle_id, study_id, trial_id, as_of)
    reasons = [] if target is not None else [RISK_NO_STORED_TARGET]
    account_id: str | None = None
    expected: str | None = None
    try:
        criteria = resolve_exit_criteria_v1(cursor, cycle_id=cycle_id, study_id=str(study_id), trial_id=str(trial_id))
    except ExitCriteriaError as error:
        criteria, reasons = None, [*reasons, f"{RISK_EXIT_CRITERIA_REFUSED_PREFIX}{error}"]
    bound = (criteria.packet.frozen["criteria"]["required_account_policy_hash"]
             if criteria is not None and criteria.packet.status == STATUS_AUTHORIZED else None)
    if bound is None:
        if not any(reason.startswith(RISK_EXIT_CRITERIA_REFUSED_PREFIX) for reason in reasons):
            reasons.append(RISK_NO_BOUND_ACCOUNT)
    else:
        cursor.execute("SELECT policy_version_id, account_id FROM account_policy_versions WHERE content_hash=%s",
                       (bound,))
        row = cursor.fetchone()
        if row is None:
            reasons.append(RISK_BOUND_POLICY_NOT_RECORDED)
        else:
            expected, account_id = str(row[0]), str(row[1])
    if account_id is None:
        return _risk_not_loaded(reasons, target=target)
    current = read_account_policy_resolution_v1(cursor, account_id, as_of=as_of)
    if target is None:
        return _risk_not_loaded(reasons, account_id=account_id, target=target, current=current,
                                source="AUTHORIZED_EXIT_CRITERIA_BINDING")
    return _assess_risk(cursor, account_id=account_id, target=target, cycle_id=cycle_id,
                        source="AUTHORIZED_EXIT_CRITERIA_BINDING", expected_policy_version_id=expected,
                        current=current)


#: How many candidates' latest recorded targets the paper-account risk read assesses (bounded, newest first).
ACCOUNT_RISK_CANDIDATE_LIMIT_V1 = 50


def read_account_risk_v1(cursor: _Cursor, account_id: str, *, now: datetime | None = None) -> AccountRiskView:
    """A named account's current authoritative policy and each candidate's latest recorded target assessed.

    The account is the operator's choice of an existing account (already accepted by the
    paper-account read); every other input comes from stored rows. No target exists
    without a stored ``INCUBATING`` signal in the current cycle.
    """
    as_of = now or datetime.now(UTC)
    cycle_id = current_cycle_id_v1()
    current = read_account_policy_resolution_v1(cursor, account_id, as_of=as_of)
    cursor.execute(
        "SELECT signal_id, content_hash, identity, decided_at, study_id, trial_id, symbol, target_from, target_to "
        "FROM (SELECT DISTINCT ON (s.study_id, s.trial_id) s.signal_id, s.content_hash, s.identity, s.decided_at, "
        "s.study_id, s.trial_id, s.symbol, s.target_from, s.target_to FROM live_strategy_signals s "
        "JOIN strategy_lab_holdout_validations v ON v.validation_hash = s.identity->>'authority_evidence_hash' "
        "WHERE v.cycle_id = %s AND s.authority = 'INCUBATING' AND s.decided_at <= %s "
        "ORDER BY s.study_id, s.trial_id, s.decided_at DESC, s.signal_id DESC) latest "
        "ORDER BY decided_at DESC, signal_id DESC LIMIT %s", (cycle_id, as_of, ACCOUNT_RISK_CANDIDATE_LIMIT_V1))
    targets = [_signal_target(row, cycle_id) for row in cursor.fetchall()]
    candidates = [_assess_risk(cursor, account_id=account_id, target=target, cycle_id=cycle_id,
                               source="OPERATOR_SELECTED_ACCOUNT", expected_policy_version_id=None, current=current)
                  for target in targets]
    return AccountRiskView(account_id=account_id, as_of=as_of, cycle_id=cycle_id, current_policy=current,
                           candidates=candidates, reasons=[] if targets else [RISK_NO_STORED_TARGET])


class _PaperContext:
    """The cycle's incubation report, built once per request from the stored fills (no caller input)."""

    def __init__(self, cursor: _Cursor, as_of: datetime, prices: TerminalPaperPricesV1 | None = None) -> None:
        from .paper_economics_v1 import OwnerCostApprovalV1
        from .paper_incubation_report_v1 import PaperIncubationError

        self.cycle = _CycleContext(cursor, as_of)
        self.prices = prices
        self.cursor = cursor
        self.report: dict[str, Any] | None = None
        self.refusal: str | None = None
        try:
            self.report = incubation_report_v1(self.cycle.fills, self.cycle.cost, as_of=as_of,
                                               resolution=self.cycle.resolution)
        except PaperIncubationError as error:
            self.refusal = f"{INCUBATION_REPORT_REFUSED_PREFIX}{error}"
        # OR-6 owner approval is recorded only by an AUTHORIZED preregistration; the cycle's cost policy is
        # the opened packet's, so the opened packet is the only approval this read may pass on.
        self.cost_approval: Any = None
        if self.cycle.opening is not None:
            cursor.execute("SELECT preregistration_hash, identity FROM strategy_lab_preregistrations "
                           "WHERE preregistration_hash=%s", (self.cycle.opening["preregistration_hash"],))
            row = cursor.fetchone()
            if row is not None and _json(row[1]).get("status") == "AUTHORIZED":
                self.cost_approval = OwnerCostApprovalV1(_json(row[1]), str(row[0]).strip())


def _candidate_economics(context: _PaperContext, study: str, trial: str,
                         fills: list[dict[str, Any]]) -> PaperEconomicsSectionView:
    """``paper_economics_v1`` over this candidate's stored fills only; never a caller input, never a mark guess."""
    from .paper_economics_v1 import PaperEconomicsError, paper_economics_v1

    if context.refusal is not None:
        return _economics_not_projected(ECONOMICS_NOT_LOADED, [context.refusal])
    if not fills:
        return _economics_not_projected(ECONOMICS_NOT_LOADED, [NO_PAPER_FILL_IN_CYCLE])
    try:
        result = paper_economics_v1(
            [{"identity": item, "content_hash": identity_hash_v1(item)} for item in fills], context.cycle.cost,
            as_of=context.cycle.as_of, cost_approval=context.cost_approval, resolution=context.cycle.resolution)
    except PaperEconomicsError as error:
        return _economics_not_projected(ECONOMICS_REFUSED, [f"{PAPER_ECONOMICS_REFUSED_PREFIX}{error}"])
    view = economics_section_view_v1(result, study_id=study, trial_id=trial)
    prices = getattr(context, "prices", None)
    if view.gross_unrealized is None or view.gross_unrealized.status != "UNAVAILABLE":
        return view
    if (view.exposure or {}).get("open_position_signed") is None:
        return view
    entries = [item for item in fills if item["status"] == FILL_STATUS_FILLED]
    if not entries:
        return view
    if prices is None:
        resolution_payload: dict[str, Any] = {"status": "UNAVAILABLE", "reasons": ["PRICE_SELECTION_POLICY_NOT_CONFIGURED"],
                              "mark": None, "evidence": None}
    else:
        resolution = prices.resolve(context.cursor, cycle_id=context.cycle.cycle_id,
                                    study_id=study, trial_id=trial, entry=entries[-1], as_of=context.cycle.as_of)
        resolution_payload = {**resolution.payload(), "selection_policy": prices.provenance()}
        if resolution.status == "AVAILABLE" and resolution.mark is not None:
            result = paper_economics_v1(
                [{"identity": item, "content_hash": identity_hash_v1(item)} for item in fills], context.cycle.cost,
                as_of=context.cycle.as_of, cost_approval=context.cost_approval, resolution=context.cycle.resolution,
                marks=[resolution.mark])
            view = economics_section_view_v1(result, study_id=study, trial_id=trial)
    view.price_resolution = resolution_payload
    view.unrealized_mark_source = "SEALED_T4_PUBLIC_TRADE_REFERENCE_NOT_VENUE_SETTLEMENT_MARK"
    if view.gross_unrealized is not None and resolution_payload["status"] == "UNAVAILABLE":
        view.gross_unrealized.detail["projection_reasons"] = view.gross_unrealized.reasons
        view.gross_unrealized.reasons = list(resolution_payload["reasons"])
    return view


def _stored_parity(cursor: _Cursor, context: _PaperContext, study_id: UUID, trial_id: UUID,
                   live_side: dict[str, Any]) -> ParityEvidenceView:
    """The latest stored parity assessment known at the read's ``as_of``; absent is NOT_LOADED, unverifiable REFUSED."""
    from .paper_evidence_store_v1 import PaperEvidenceStoreError, read_fill_parity_v1

    cycle_id = context.cycle.cycle_id
    try:
        stored = read_fill_parity_v1(cursor, cycle_id=cycle_id, study_id=study_id, trial_id=trial_id,
                                     as_of=context.cycle.as_of)
    except PaperEvidenceStoreError as error:
        return refused_parity_view_v1(str(error), live_side=live_side)
    if stored is None:
        return parity_evidence_view_v1(None, trial_id=str(trial_id), live_side=live_side)
    return stored_parity_evidence_view_v1(stored, study_id=str(study_id), trial_id=str(trial_id), cycle_id=cycle_id,
                                          live_side=live_side)


def _stored_funding(cursor: _Cursor, context: _PaperContext, study_id: UUID, trial_id: UUID,
                    entry: dict[str, Any] | None, not_loaded_reasons: list[str]) -> FundingEvidenceView:
    """The latest stored funding record known at ``as_of``; absent is NOT_LOADED (the report's section kept beside
    it for its blockers), unverifiable REFUSED. Never fed into economics as a cost."""
    from .paper_evidence_store_v1 import PaperEvidenceStoreError, read_funding_evidence_v1

    cycle_id = context.cycle.cycle_id
    try:
        stored = read_funding_evidence_v1(cursor, cycle_id=cycle_id, study_id=study_id, trial_id=trial_id,
                                          as_of=context.cycle.as_of)
    except PaperEvidenceStoreError as error:
        return refused_funding_view_v1(str(error))
    if stored is None:
        return funding_evidence_view_v1(None if entry is None else entry.get("funding_evidence"),
                                        not_loaded_reasons=not_loaded_reasons)
    return stored_funding_evidence_view_v1(stored, study_id=str(study_id), trial_id=str(trial_id), cycle_id=cycle_id)


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
        parity=_stored_parity(cursor, context, study_id, trial_id, live_side),
        funding=_stored_funding(cursor, context, study_id, trial_id, entry, funding_reasons),
        economics=_candidate_economics(context, study_text, trial_text, fills),
        risk=_candidate_risk(cursor, context, study_id, trial_id),
        as_of=context.cycle.as_of)


def read_candidate_paper_evidence_v1(cursor: _Cursor, study_id: UUID, trial_id: UUID, *,
                                     now: datetime | None = None,
                                     prices: TerminalPaperPricesV1 | None = None) -> CandidatePaperEvidenceView:
    """One candidate's paper evidence in the current cycle, resolved from stored rows only (read-only)."""
    return _candidate_paper_evidence(cursor, _PaperContext(cursor, now or datetime.now(UTC), prices), study_id, trial_id)


def read_terminal_comparison_v1(cursor: _Cursor, *, study_ids: list[UUID], sort: Any,
                                now: datetime | None = None,
                                prices: TerminalPaperPricesV1 | None = None) -> ComparisonView:
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
    context = _PaperContext(cursor, as_of, prices)
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
    "ACCOUNT_RISK_CANDIDATE_LIMIT_V1",
    "AUTHORITY_LEGEND_V1",
    "EVIDENCE_STAGES_V1",
    "NOT_LOADED",
    "AccountPolicyResolutionView",
    "AccountRiskView",
    "AccountView",
    "CandidatePaperEvidenceView",
    "CandidateStateView",
    "ComparisonView",
    "CycleView",
    "EconomicsComponentView",
    "EconomicsNetView",
    "FundingEvidenceView",
    "IncubationView",
    "LiveSignalView",
    "PaperEconomicsSectionView",
    "PaperRiskView",
    "ParityEvidenceView",
    "RerunView",
    "SensitivityRowView",
    "TerminalOverview",
    "UnrealizedEvidenceView",
    "ValidationView",
    "WalkForwardFoldView",
    "WalkForwardView",
    "economics_section_view_v1",
    "funding_evidence_view_v1",
    "parity_evidence_view_v1",
    "read_account_policy_resolution_v1",
    "read_account_risk_v1",
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
    "refused_funding_view_v1",
    "refused_parity_view_v1",
    "stored_funding_evidence_view_v1",
    "stored_parity_evidence_view_v1",
]
