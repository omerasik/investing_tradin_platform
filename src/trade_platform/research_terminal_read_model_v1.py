"""UI-2 -- read-only projections for the one-terminal research workflow.

``RESEARCH_ONLY``. Bounded, typed views over the R4 (Strategy Lab), R4.7
(Decimal authority reruns), R6 (cycles, preregistrations, holdout, candidate
states), R8 (live signals), R9 (account policies) and R10 (paper incubation)
tables for the protected operator API. Nothing here writes, launches, opens a
holdout, approves a policy or promotes a candidate.

Every view states its claim and never lifts it:

* search results are ``SEARCH_NON_AUTHORITATIVE``; a Decimal authority rerun
  makes a *selection* numerically authoritative, never the economics (gross,
  T2 ``CONDITIONAL``);
* a candidate is ``INCUBATING`` or ``REJECTED`` only through a recorded R6 state,
  and nothing reaches a validated state;
* live signals are ``NOT_VALIDATED_<authority>`` proposals;
* an account policy is ``UNCONFIGURED`` until the owner completes it (OR-11);
* paper incubation is unit-exposure, gross, never cost-complete.

Open owner gates are listed as gates (``OPEN``), never as defaults.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from .paper_incubation_v1 import incubation_report_v1
from .strategy_lab_policies_v1 import gross_cost_policy_v1
from .strategy_lab_study_v1 import identity_hash_v1
from .strategy_lab_validation_v1 import CURRENT_CYCLE_V1


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


def read_cycle_v1(cursor: _Cursor) -> CycleView:
    """The current research cycle (its holdout boundary is immutable)."""
    cycle = CURRENT_CYCLE_V1
    cursor.execute("SELECT holdout_end_exclusive, opened_at FROM strategy_lab_holdout_openings WHERE cycle_id=%s",
                   (cycle.cycle_id,))
    opening = cursor.fetchone()
    cursor.execute("SELECT status, count(*) FROM strategy_lab_preregistrations WHERE cycle_id=%s GROUP BY status",
                   (cycle.cycle_id,))
    preregistrations = {str(status): int(count) for status, count in cursor.fetchall()}
    cursor.execute("SELECT count(*) FROM strategy_lab_holdout_validations WHERE cycle_id=%s", (cycle.cycle_id,))
    validated = int((cursor.fetchone() or (0,))[0]) > 0
    return CycleView(cycle_id=cycle.cycle_id, holdout_start=cycle.holdout_start,
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
    """The R10 report over every recorded fill, under the gross OR-6 policy (no verified fee schedule)."""
    cursor.execute("SELECT content_hash, identity FROM paper_incubation_fills ORDER BY decided_at, signal_id")
    fills = []
    for content_hash, raw in cursor.fetchall():
        identity = _json(raw)
        if identity_hash_v1(identity) != str(content_hash).strip():
            raise ValueError("stored_fill_identity_does_not_rederive")
        fills.append(identity)
    report = incubation_report_v1(fills, gross_cost_policy_v1(), as_of=now or datetime.now(UTC))
    return IncubationView(state="AVAILABLE" if fills else "NO_FILLS", report=report)


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
    gates = [
        OwnerGateView(gate="OR-7", topic=OWNER_GATES_V1[0]["topic"],
                      status="SATISFIED" if cycle.holdout_state == "OPENED" else "OPEN",
                      evidence=f"holdout {cycle.holdout_state.lower()} for {cycle.cycle_id}"),
        OwnerGateView(gate="OR-11", topic=OWNER_GATES_V1[1]["topic"],
                      status="SATISFIED" if accounts.get("ACTIVE") else "OPEN",
                      evidence=f"{accounts.get('ACTIVE', 0)} active account policies"),
        OwnerGateView(gate="OR-9", topic=OWNER_GATES_V1[2]["topic"], status="OPEN",
                      evidence="no watch list is recorded by this system"),
        OwnerGateView(gate="OR-6 fee schedule", topic=OWNER_GATES_V1[3]["topic"], status="OPEN",
                      evidence="every study and report runs under the gross cost policy"),
    ]
    return TerminalOverview(
        generated_at=now or datetime.now(UTC), studies=int(studies), finished_studies=int(finished), reruns=reruns,
        cycle=cycle, candidate_states=states, signals=signals, latest_signal_at=latest, incubation_fills=fills,
        accounts=accounts, owner_gates=gates, authority_legend=dict(AUTHORITY_LEGEND_V1))


__all__ = [
    "AUTHORITY_LEGEND_V1",
    "AccountView",
    "CandidateStateView",
    "CycleView",
    "IncubationView",
    "LiveSignalView",
    "RerunView",
    "TerminalOverview",
    "ValidationView",
    "read_accounts_v1",
    "read_cycle_v1",
    "read_incubation_v1",
    "read_live_signals_v1",
    "read_reruns_v1",
    "read_terminal_overview_v1",
    "read_validation_v1",
]
