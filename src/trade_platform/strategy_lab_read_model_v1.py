"""Phase R4.5 -- read-only operator projections of the Strategy Lab ledger.

``RESEARCH_ONLY``. Bounded, typed views over migrations ``0053``/``0054`` for
the protected operator API: study list, one study's identity and progress, its
manifests and frozen candidate sets. Nothing here writes, launches or approves.

Authority is restated, never inferred upward: every view carries
``authority_status = NON_AUTHORITATIVE`` and ``promotable = false`` with the
reasons re-derived from the stored identity by the same rules as
:meth:`trade_platform.strategy_lab_study_v1.StudySpecV1.authority`, and every
candidate set shows ``authoritative_rerun = PENDING_OWNER_DECISION_OR_3``. A
consumer cannot read a search-tier candidate as approved for anything.

Stage B.1.2a explorer reads (still read-only, still bounded):

* the study list filters by strategy family and by instrument, where the
  instrument is resolved only through the research window catalogue by
  ``(dataset_version_id, content_hash)`` -- never parsed from a label; an
  unmatched binding is ``UNRESOLVED``;
* one study's trials are sorted, filtered and paged server-side over the stored
  metric text (``numeric`` cast of the exact stored string, never a float), with
  every metric returned verbatim as stored;
* Decimal authority reruns are restated with their per-candidate
  reconciliation; recorded R6 candidate states are listed, never inferred;
* the economics view separates gross search results, hypothetical costs bound
  to the study, verified costs and net profitability. Its types cannot express
  verified costs or net profitability as available: no verified fee schedule
  exists (OR-6) and nothing here invents one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from .evidence_tier_authority_v1 import PROFESSIONAL_EVIDENCE_TIERS_V1
from .strategy_lab_study_v1 import (
    COST_POLICY_UNSET_V1,
    NUMERIC_POLICY_UNSET_V1,
    REASON_COST_POLICY_GROSS,
    REASON_COST_POLICY_UNSET,
    REASON_NON_PROFESSIONAL_TIER,
    REASON_NUMERIC_POLICY_UNSET,
    REASON_SEARCH_TIER_ONLY,
    UNTOUCHED_HOLDOUT_BOUNDARY_V1,
)


class StrategyLabObjectNotFound(LookupError):
    """The requested study does not exist."""


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...


_PROFESSIONAL = {tier.value for tier in PROFESSIONAL_EVIDENCE_TIERS_V1}


class StrategyLabPageInfo(BaseModel):
    limit: int
    offset: int
    returned: int
    has_more: bool


class StrategyLabAuthorityView(BaseModel):
    authority_status: Literal["NON_AUTHORITATIVE"] = "NON_AUTHORITATIVE"
    promotable: Literal[False] = False
    reasons: list[str]


class StrategyLabDatasetRefView(BaseModel):
    """One dataset binding of a study, and the catalogued research window it resolves to (if any)."""

    role: str
    dataset_version_id: str
    content_hash: str
    evidence_tier: str
    knowledge_upper_bound_exclusive: str | None
    resolution: Literal["RESEARCH_WINDOW_CATALOGUE", "UNRESOLVED"] = "UNRESOLVED"
    symbol: str | None = None
    first_utc_day: str | None = None
    last_utc_day: str | None = None
    bars: int | None = None
    continuous: bool | None = None
    not_published_days: list[str] = []
    rejected_days: list[str] = []


class StrategyLabStudySummaryView(BaseModel):
    study_id: UUID
    content_hash: str
    strategy_family: str
    strategy_version: str
    label: str
    planned_trial_count: int
    evaluation_upper_bound_exclusive: datetime
    registered_at: datetime
    queue_states: dict[str, int]
    result_count: int
    authority: StrategyLabAuthorityView
    #: Resolved only through the research window catalogue; ``None`` when any binding is unresolved.
    instrument: str | None = None
    instrument_source: Literal["RESEARCH_WINDOW_CATALOGUE", "UNRESOLVED"] = "UNRESOLVED"
    datasets: list[StrategyLabDatasetRefView] = []


class StrategyLabStudyFacets(BaseModel):
    strategy_families: list[str] = []
    instruments: list[str] = []
    #: ``False`` when this process has no research window catalogue: instruments cannot be resolved.
    instrument_catalogue: bool = False


class StrategyLabStudyPage(BaseModel):
    state: Literal["AVAILABLE", "UNAVAILABLE"]
    items: list[StrategyLabStudySummaryView]
    page: StrategyLabPageInfo
    total: int | None = None
    filters: dict[str, str] = {}
    facets: StrategyLabStudyFacets = StrategyLabStudyFacets()


class StrategyLabManifestView(BaseModel):
    manifest_hash: str
    planned_trial_count: int
    result_count: int
    outcomes: dict[str, int]
    recorded_at: datetime


class StrategyLabCandidateView(BaseModel):
    rank: int
    trial_id: UUID
    trial_content_hash: str
    result_content_hash: str
    metric_value: str


class StrategyLabCandidateSetView(BaseModel):
    candidate_set_hash: str
    manifest_hash: str
    rule: dict[str, Any]
    candidates: list[StrategyLabCandidateView]
    eligible_count: int
    ineligible_count: int
    cutoff_tie: bool
    multiple_testing_trial_count: int
    numeric_tier: Literal["SEARCH_NON_AUTHORITATIVE"]
    authoritative_rerun: Literal["PENDING_OWNER_DECISION_OR_3", "REQUIRED_DECIMAL_RERUN_OR_3"]
    recorded_at: datetime


class StrategyLabRerunSelectedView(BaseModel):
    rank: int
    trial_id: UUID
    metric_value: str


class StrategyLabRerunView(BaseModel):
    """One Decimal authority rerun of a frozen set: selection authority only, never the economics."""

    rerun_hash: str
    candidate_set_hash: str
    selection_status: str
    claim: Literal["DECIMAL_SELECTION_ESTABLISHED", "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED"]
    economics: Literal["GROSS_CONDITIONAL_NON_PROMOTABLE"] = "GROSS_CONDITIONAL_NON_PROMOTABLE"
    claim_ceiling: str | None
    rerun_count: int
    rule: dict[str, Any]
    baseline_lag_micros: int | None
    sweep_lags_micros: list[int]
    selected: list[StrategyLabRerunSelectedView]
    decimal_cutoff: str | None
    cutoff_tie: bool | None
    changed_versus_search_selection: bool | None
    undefined_metric_trials: list[str]
    reason: str | None
    #: Per-(trial, lag) reconciliation outcomes, e.g. ``{"RECONCILED": 40}``.
    reconciliation: dict[str, int]
    held_divergence_bars: int
    trade_count_divergence: int
    recorded_at: datetime


class StrategyLabCandidateStateView(BaseModel):
    trial_id: UUID
    state: str
    evidence_hash: str
    reasons: list[str]
    recorded_at: datetime


class StrategyLabHoldoutView(BaseModel):
    """The study's position against the untouched holdout. Reported, never opened from here."""

    boundary: datetime
    evaluation_upper_bound_exclusive: datetime
    evaluation_before_boundary: bool
    cycle_id: str
    state: Literal["UNOPENED", "OPENED"]


class StrategyLabEconomicsView(BaseModel):
    """Gross vs hypothetical vs verified vs net. Verified and net cannot be expressed as available."""

    gross_results: Literal["GROSS_SEARCH_TIER"] = "GROSS_SEARCH_TIER"
    cost_mode: str
    cost_policy_slot: str
    funding: str | None
    fill_liquidity: str | None
    #: Slippage scenarios bound into the study identity, verbatim (hypothetical, owner-named).
    hypothetical_slippage_scenarios: list[dict[str, Any]]
    hypothetical_costs: Literal["BOUND_TO_STUDY", "NONE_BOUND"]
    break_even_metric: str | None
    verified_costs: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    verified_cost_reasons: list[str]
    net_profitability: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    net_profitability_reasons: list[str]


class StrategyLabStudyDetailView(BaseModel):
    summary: StrategyLabStudySummaryView
    strategy: dict[str, Any]
    parameter_space: list[dict[str, Any]]
    datasets: list[dict[str, Any]]
    search: dict[str, Any]
    numeric_policy_slot: str
    cost_policy_slot: str
    manifests: list[StrategyLabManifestView]
    candidate_sets: list[StrategyLabCandidateSetView]
    policies: dict[str, Any] = {}
    reruns: list[StrategyLabRerunView] = []
    candidate_states: list[StrategyLabCandidateStateView] = []
    economics: StrategyLabEconomicsView | None = None
    holdout: StrategyLabHoldoutView | None = None


#: Trial outcome filters the explorer accepts (``PENDING`` = no stored result yet).
TRIAL_OUTCOME_FILTERS_V1 = ("ALL", "EVALUATED", "INADMISSIBLE_PARAMETERS", "PENDING")
#: Exact numeric text admitted to the server-side sort; anything else sorts as missing (last).
_NUMERIC_TEXT = r"^-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$"
_METRIC_KEY = r"^[a-z][a-z0-9_]{0,63}$"


class StrategyLabTrialRerunView(BaseModel):
    """A trial's Decimal rerun state, restated from the stored rerun record."""

    rerun_hash: str
    reasons: list[str]
    #: Reconciliation per lag (micros, as text), e.g. ``{"2000000": "RECONCILED"}``.
    reconciliation_by_lag: dict[str, str]
    decimal_rank: int | None
    decimal_metric_value: str | None


class StrategyLabTrialView(BaseModel):
    trial_id: UUID
    ordinal: int
    trial_content_hash: str
    parameters: dict[str, str]
    outcome: Literal["EVALUATED", "INADMISSIBLE_PARAMETERS", "PENDING"]
    numeric_tier: str | None
    cost_policy_slot: str | None
    result_content_hash: str | None
    #: Stored search-tier metrics, verbatim text (``None`` only where the stored value is null).
    metrics: dict[str, str | None]
    produced_at: datetime | None
    frozen_rank: int | None
    rerun: StrategyLabTrialRerunView | None
    candidate_state: str | None


class StrategyLabTrialPage(BaseModel):
    state: Literal["AVAILABLE", "UNAVAILABLE"]
    study_id: UUID
    numeric_tier: Literal["SEARCH_NON_AUTHORITATIVE"] = "SEARCH_NON_AUTHORITATIVE"
    economics: Literal["GROSS_NON_PROMOTABLE"] = "GROSS_NON_PROMOTABLE"
    sort: str
    direction: Literal["asc", "desc"]
    outcome: str
    frozen_only: bool
    metric_keys: list[str]
    items: list[StrategyLabTrialView]
    page: StrategyLabPageInfo
    total: int


class StrategyLabTrialRerunLagView(BaseModel):
    lag_micros: int
    reconciliation: str
    held_divergence_bars: int | None
    trade_count_divergence: int | None
    float_near_tie_decisions: int | None
    metrics: dict[str, str | None]


class StrategyLabTrialDetailView(BaseModel):
    trial: StrategyLabTrialView
    study_label: str
    strategy_family: str
    rerun_lags: list[StrategyLabTrialRerunLagView]
    candidate_states: list[StrategyLabCandidateStateView]
    economics: StrategyLabEconomicsView


def _json(raw: object) -> Any:
    return json.loads(raw) if isinstance(raw, (str, bytes)) else raw


def _exact_json(raw: object) -> Any:
    """Parse JSON keeping every number as its exact text (callers select ``jsonb::text``)."""
    if isinstance(raw, (str, bytes)):
        return json.loads(raw, parse_float=str, parse_int=str)
    return raw


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None or isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return None


#: A window catalogue entry is ``read_window_catalogue_v1``'s dict; keyed here by (id, content hash).
WindowIndex = dict[tuple[str, str], Mapping[str, Any]]


def window_index_v1(windows: Sequence[Mapping[str, Any]] | None) -> WindowIndex | None:
    if windows is None:
        return None
    return {(str(w["dataset_version_id"]), str(w["content_hash"])): w for w in windows}


def _dataset_refs(identity: dict[str, Any], windows: WindowIndex | None) -> list[StrategyLabDatasetRefView]:
    refs = []
    for binding in identity.get("datasets", []):
        if not isinstance(binding, dict):
            continue
        key = (str(binding.get("dataset_version_id")), str(binding.get("content_hash")))
        window = (windows or {}).get(key)
        base = {"role": str(binding.get("role", "")), "dataset_version_id": key[0], "content_hash": key[1],
                "evidence_tier": str(binding.get("evidence_tier", "")),
                "knowledge_upper_bound_exclusive": _text(binding.get("knowledge_upper_bound_exclusive"))}
        if window is None:
            refs.append(StrategyLabDatasetRefView(**base))  # type: ignore[arg-type]
            continue
        refs.append(StrategyLabDatasetRefView(
            **base, resolution="RESEARCH_WINDOW_CATALOGUE", symbol=str(window["symbol"]),  # type: ignore[arg-type]
            first_utc_day=str(window["first_utc_day"]), last_utc_day=str(window["last_utc_day"]),
            bars=int(window["bars"]), continuous=bool(window["continuous"]),
            not_published_days=[str(d) for d in window.get("not_published_days", [])],
            rejected_days=[str(d) for d in window.get("rejected_days", [])],
        ))
    return refs


def _instrument(refs: list[StrategyLabDatasetRefView]) -> str | None:
    symbols = {ref.symbol for ref in refs}
    if not refs or None in symbols or len(symbols) != 1:
        return None
    return next(iter(symbols))


def economics_v1(identity: dict[str, Any]) -> StrategyLabEconomicsView:
    """Restate the study's cost identity; verified costs and net stay UNAVAILABLE with reasons."""
    raw_policies = identity.get("policies")
    policies: dict[str, Any] = raw_policies if isinstance(raw_policies, dict) else {}
    raw_cost = policies.get("cost")
    cost: dict[str, Any] = raw_cost if isinstance(raw_cost, dict) else {}
    mode = str(cost.get("mode") or identity.get("cost_policy_slot") or COST_POLICY_UNSET_V1)
    scenarios = [dict(s) for s in cost.get("slippage_scenarios") or [] if isinstance(s, dict)]
    if not cost:
        verified = ["COST_POLICY_UNSET_OR_6"]
    elif cost.get("venue_fees") is None:
        verified = ["NO_VERIFIED_FEE_SCHEDULE_OR_6"]
    else:  # a fee value in an identity is not a verification reference
        verified = ["VENUE_FEES_NOT_VERIFIED_BY_THIS_VIEW"]
    net = [*verified, f"COST_MODE_{mode}"]
    if not scenarios:
        net.append("NO_APPROVED_SLIPPAGE_SCENARIO_OR_6")
    if cost.get("funding") is None or str(cost.get("funding", "")).startswith("NOT_MODELLED"):
        net.append("FUNDING_NOT_MODELLED")
    return StrategyLabEconomicsView(
        cost_mode=mode, cost_policy_slot=str(identity.get("cost_policy_slot", "")),
        funding=_text(cost.get("funding")), fill_liquidity=_text(cost.get("fill_liquidity")),
        hypothetical_slippage_scenarios=scenarios,
        hypothetical_costs="BOUND_TO_STUDY" if scenarios else "NONE_BOUND",
        break_even_metric="break_even_bps_per_side"
        if cost.get("gross_search_reports") == "BREAK_EVEN_COST_BPS_PER_SIDE" else None,
        verified_cost_reasons=verified, net_profitability_reasons=list(dict.fromkeys(net)),
    )


def _authority(identity: dict[str, Any]) -> StrategyLabAuthorityView:
    policies = identity.get("policies")
    if isinstance(policies, dict):
        # R4.6 policy-bound study: re-derive the reasons from the stored identity.
        study_reasons = [REASON_SEARCH_TIER_ONLY]
        if isinstance(policies.get("cost"), dict) and policies["cost"].get("mode") == "GROSS_NON_PROMOTABLE":
            study_reasons.append(REASON_COST_POLICY_GROSS)
        if any(binding.get("evidence_tier") not in _PROFESSIONAL for binding in identity.get("datasets", [])):
            study_reasons.append(REASON_NON_PROFESSIONAL_TIER)
        return StrategyLabAuthorityView(reasons=study_reasons)
    reasons: list[str] = []
    if identity.get("numeric_policy_slot") == NUMERIC_POLICY_UNSET_V1:
        reasons.append(REASON_NUMERIC_POLICY_UNSET)
    if identity.get("cost_policy_slot") == COST_POLICY_UNSET_V1:
        reasons.append(REASON_COST_POLICY_UNSET)
    if any(binding.get("evidence_tier") not in _PROFESSIONAL for binding in identity.get("datasets", [])):
        reasons.append(REASON_NON_PROFESSIONAL_TIER)
    # The only admissible slot values are the unset markers (CHECK-enforced); any
    # other value would be a contract this reader does not know -> still refuse authority.
    if len(reasons) < 2:
        reasons.append("UNKNOWN_POLICY_SLOT_VALUE")
    return StrategyLabAuthorityView(reasons=reasons)


_SUMMARY_SQL = (
    "SELECT s.study_id, s.content_hash, s.strategy_family, s.identity, s.label, s.planned_trial_count, "
    "s.evaluation_upper_bound_exclusive, s.registered_at, "
    "COALESCE((SELECT jsonb_object_agg(state, n) FROM (SELECT state, count(*) AS n FROM strategy_lab_trial_queue q "
    "WHERE q.study_id=s.study_id GROUP BY state) g), '{}'::jsonb), "
    "(SELECT count(*) FROM strategy_lab_trial_results r WHERE r.study_id=s.study_id) "
    "FROM strategy_lab_studies s"
)
#: Binds a study to catalogued windows by exact ``dataset_version_id:content_hash`` pairs.
_BINDS_ANY_SQL = (
    "EXISTS (SELECT 1 FROM jsonb_array_elements(s.identity->'datasets') b "
    "WHERE (b->>'dataset_version_id') || ':' || (b->>'content_hash') = ANY(%s))"
)


def _summary(row: tuple[Any, ...], windows: WindowIndex | None = None,
             ) -> tuple[StrategyLabStudySummaryView, dict[str, Any]]:
    identity = _json(row[3])
    refs = _dataset_refs(identity, windows)
    instrument = _instrument(refs)
    view = StrategyLabStudySummaryView(
        study_id=row[0], content_hash=str(row[1]).strip(), strategy_family=str(row[2]),
        strategy_version=str(identity["strategy"]["version"]), label=str(row[4]),
        planned_trial_count=int(row[5]), evaluation_upper_bound_exclusive=row[6], registered_at=row[7],
        queue_states={str(k): int(v) for k, v in sorted(_json(row[8]).items())}, result_count=int(row[9]),
        authority=_authority(identity), instrument=instrument,
        instrument_source="RESEARCH_WINDOW_CATALOGUE" if instrument else "UNRESOLVED", datasets=refs,
    )
    return view, identity


def read_strategy_lab_studies_v1(
    cursor: _Cursor, *, limit: int, offset: int, strategy_family: str | None = None,
    instrument: str | None = None, windows: Sequence[Mapping[str, Any]] | None = None,
) -> StrategyLabStudyPage:
    """Studies newest first, filtered server-side.

    ``instrument`` matches only studies bound to a catalogued window of that
    symbol by exact identity; with no catalogue (``windows is None``) an
    instrument filter matches nothing rather than guessing from labels.
    """
    index = window_index_v1(windows)
    clauses: list[str] = []
    params: list[Any] = []
    if strategy_family is not None:
        clauses.append("s.strategy_family = %s")
        params.append(strategy_family)
    if instrument is not None:
        pairs = [f"{key[0]}:{key[1]}" for key, w in (index or {}).items() if str(w["symbol"]) == instrument]
        clauses.append(_BINDS_ANY_SQL)
        params.append(pairs)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cursor.execute(f"SELECT count(*) FROM strategy_lab_studies s{where}", tuple(params))  # nosec B608
    total = int((cursor.fetchone() or (0,))[0])
    cursor.execute(_SUMMARY_SQL + where + " ORDER BY s.registered_at DESC, s.study_id LIMIT %s OFFSET %s",
                   (*params, limit + 1, offset))
    rows = cursor.fetchall()
    items = [_summary(row, index)[0] for row in rows[:limit]]
    cursor.execute("SELECT DISTINCT strategy_family FROM strategy_lab_studies ORDER BY 1")
    families = [str(r[0]) for r in cursor.fetchall()]
    cursor.execute("SELECT DISTINCT b->>'dataset_version_id', b->>'content_hash' FROM strategy_lab_studies s, "
                   "jsonb_array_elements(s.identity->'datasets') b")
    bound = {(str(r[0]), str(r[1])) for r in cursor.fetchall()}
    instruments = sorted({str(w["symbol"]) for key, w in (index or {}).items() if key in bound})
    filters = {k: v for k, v in (("strategy_family", strategy_family), ("instrument", instrument)) if v is not None}
    return StrategyLabStudyPage(
        state="AVAILABLE" if items else "UNAVAILABLE", items=items, total=total, filters=filters,
        page=StrategyLabPageInfo(limit=limit, offset=offset, returned=len(items), has_more=len(rows) > limit),
        facets=StrategyLabStudyFacets(strategy_families=families, instruments=instruments,
                                      instrument_catalogue=index is not None),
    )


def _summary_row(cursor: _Cursor, study_id: UUID) -> tuple[Any, ...]:
    cursor.execute(_SUMMARY_SQL + " WHERE s.study_id=%s", (study_id,))
    row = cursor.fetchone()
    if row is None:
        raise StrategyLabObjectNotFound("strategy_lab_study_not_found")
    return row


def _candidate_sets(cursor: _Cursor, study_id: UUID) -> list[StrategyLabCandidateSetView]:
    cursor.execute(
        "SELECT candidate_set_hash, manifest_hash, identity::text, recorded_at FROM strategy_lab_candidate_sets "
        "WHERE study_id=%s ORDER BY recorded_at, candidate_set_hash",
        (study_id,),
    )
    candidate_sets = []
    for set_hash, manifest_hash, raw, recorded_at in cursor.fetchall():
        payload = _exact_json(raw)
        candidate_sets.append(StrategyLabCandidateSetView(
            candidate_set_hash=str(set_hash).strip(), manifest_hash=str(manifest_hash).strip(),
            rule=dict(payload["rule"]),
            candidates=[StrategyLabCandidateView(**candidate) for candidate in payload["candidates"]],
            eligible_count=int(payload["eligible_count"]), ineligible_count=int(payload["ineligible_count"]),
            cutoff_tie=bool(payload["cutoff_tie"]),
            multiple_testing_trial_count=int(payload["multiple_testing_trial_count"]),
            numeric_tier=payload["numeric_tier"], authoritative_rerun=payload["authoritative_rerun"],
            recorded_at=recorded_at,
        ))
    return candidate_sets


def _rerun_rows(cursor: _Cursor, study_id: UUID) -> list[tuple[str, str, str, int, dict[str, Any], datetime]]:
    cursor.execute(
        "SELECT rerun_hash, candidate_set_hash, selection_status, rerun_count, identity::text, recorded_at "
        "FROM strategy_lab_authority_reruns WHERE study_id=%s ORDER BY recorded_at, rerun_hash", (study_id,))
    return [(str(r[0]).strip(), str(r[1]).strip(), str(r[2]), int(r[3]), _exact_json(r[4]), r[5])
            for r in cursor.fetchall()]


def _rerun_view(row: tuple[str, str, str, int, dict[str, Any], datetime]) -> StrategyLabRerunView:
    rerun_hash, set_hash, status, count, identity, recorded_at = row
    selection = identity.get("authoritative_selection") or {}
    established = status == "ESTABLISHED"
    baseline = _int_or_none(identity.get("baseline_lag_micros"))
    reconciliation: dict[str, int] = {}
    held = trades = 0
    for result in identity.get("results", []):
        key = str(result.get("reconciliation", "UNKNOWN"))
        reconciliation[key] = reconciliation.get(key, 0) + 1
        held += _int_or_none(result.get("held_divergence_bars")) or 0
        trades += _int_or_none(result.get("trade_count_divergence")) or 0
    return StrategyLabRerunView(
        rerun_hash=rerun_hash, candidate_set_hash=set_hash, selection_status=status,
        claim="DECIMAL_SELECTION_ESTABLISHED" if established else "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED",
        claim_ceiling=_text(identity.get("claim_ceiling")), rerun_count=count,
        rule=dict(selection.get("rule") or {}), baseline_lag_micros=baseline,
        sweep_lags_micros=[int(v) for v in identity.get("sweep_lags_micros", [])],
        # An unestablished selection lists nothing as selected (fail closed).
        selected=[StrategyLabRerunSelectedView(rank=int(item["rank"]), trial_id=UUID(str(item["trial_id"])),
                                               metric_value=str(item["metric_value"]))
                  for item in selection.get("selected", [])] if established else [],
        decimal_cutoff=_text(selection.get("decimal_cutoff")),
        cutoff_tie=selection.get("cutoff_tie") if isinstance(selection.get("cutoff_tie"), bool) else None,
        changed_versus_search_selection=selection.get("changed_versus_search_selection")
        if isinstance(selection.get("changed_versus_search_selection"), bool) else None,
        undefined_metric_trials=[str(t) for t in selection.get("undefined_metric_trials", [])],
        reason=_text(selection.get("reason")), reconciliation=dict(sorted(reconciliation.items())),
        held_divergence_bars=held, trade_count_divergence=trades, recorded_at=recorded_at,
    )


def _candidate_states(cursor: _Cursor, study_id: UUID, trial_id: UUID | None = None,
                      ) -> list[StrategyLabCandidateStateView]:
    query = ("SELECT trial_id, state, evidence_hash, reasons::text, recorded_at FROM strategy_lab_candidate_states "
             "WHERE study_id=%s")
    params: tuple[Any, ...] = (study_id,)
    if trial_id is not None:
        query += " AND trial_id=%s"
        params = (study_id, trial_id)
    cursor.execute(query + " ORDER BY recorded_at, trial_id, evidence_hash", params)
    return [StrategyLabCandidateStateView(trial_id=r[0], state=str(r[1]), evidence_hash=str(r[2]).strip(),
                                          reasons=[str(x) for x in _exact_json(r[3])], recorded_at=r[4])
            for r in cursor.fetchall()]


def _holdout(cursor: _Cursor, evaluation_upper_bound_exclusive: datetime) -> StrategyLabHoldoutView:
    cycle_id = f"cycle-{UNTOUCHED_HOLDOUT_BOUNDARY_V1.date().isoformat()}"  # as ResearchCycleV1 derives it
    cursor.execute("SELECT 1 FROM strategy_lab_holdout_openings WHERE cycle_id=%s", (cycle_id,))
    opened = cursor.fetchone() is not None
    return StrategyLabHoldoutView(
        boundary=UNTOUCHED_HOLDOUT_BOUNDARY_V1, evaluation_upper_bound_exclusive=evaluation_upper_bound_exclusive,
        evaluation_before_boundary=evaluation_upper_bound_exclusive <= UNTOUCHED_HOLDOUT_BOUNDARY_V1,
        cycle_id=cycle_id, state="OPENED" if opened else "UNOPENED",
    )


def read_strategy_lab_study_v1(cursor: _Cursor, study_id: UUID, *,
                               windows: Sequence[Mapping[str, Any]] | None = None) -> StrategyLabStudyDetailView:
    summary, identity = _summary(_summary_row(cursor, study_id), window_index_v1(windows))
    cursor.execute(
        "SELECT manifest_hash, planned_trial_count, result_count, identity, recorded_at "
        "FROM strategy_lab_study_manifests WHERE study_id=%s ORDER BY recorded_at, manifest_hash",
        (study_id,),
    )
    manifests = [
        StrategyLabManifestView(
            manifest_hash=str(m[0]).strip(), planned_trial_count=int(m[1]), result_count=int(m[2]),
            outcomes={str(k): int(v) for k, v in _json(m[3]).get("outcomes", {}).items()}, recorded_at=m[4],
        )
        for m in cursor.fetchall()
    ]
    candidate_sets = _candidate_sets(cursor, study_id)
    reruns = [_rerun_view(row) for row in _rerun_rows(cursor, study_id)]
    return StrategyLabStudyDetailView(
        summary=summary, strategy=dict(identity["strategy"]), parameter_space=list(identity["parameter_space"]),
        datasets=list(identity["datasets"]), search=dict(identity["search"]),
        numeric_policy_slot=str(identity["numeric_policy_slot"]), cost_policy_slot=str(identity["cost_policy_slot"]),
        manifests=manifests, candidate_sets=candidate_sets,
        policies=dict(identity.get("policies") or {}), reruns=reruns,
        candidate_states=_candidate_states(cursor, study_id), economics=economics_v1(identity),
        holdout=_holdout(cursor, summary.evaluation_upper_bound_exclusive),
    )


class _TrialContext:
    """Frozen ranks, rerun state and candidate states of one study, joined onto trial rows."""

    def __init__(self, cursor: _Cursor, study_id: UUID) -> None:
        sets = _candidate_sets(cursor, study_id)
        # The latest frozen set is the one the reruns and validation bind to.
        self.frozen: dict[UUID, int] = {c.trial_id: c.rank for c in sets[-1].candidates} if sets else {}
        self.reruns = _rerun_rows(cursor, study_id)
        self.states: dict[UUID, str] = {}
        for state in _candidate_states(cursor, study_id):
            self.states[state.trial_id] = state.state  # recorded order: the latest wins

    def rerun(self, trial_id: UUID) -> StrategyLabTrialRerunView | None:
        for rerun_hash, _, status, _, identity, _ in reversed(self.reruns):
            results = [r for r in identity.get("results", []) if str(r.get("trial_id")) == str(trial_id)]
            if not results:
                continue
            selected = {str(item["trial_id"]): item for item in
                        (identity.get("authoritative_selection") or {}).get("selected", [])}
            chosen = selected.get(str(trial_id)) if status == "ESTABLISHED" else None
            reasons = list(dict.fromkeys(str(x) for r in results for x in r.get("reasons", [])))
            return StrategyLabTrialRerunView(
                rerun_hash=rerun_hash, reasons=reasons,
                reconciliation_by_lag={str(r.get("lag_micros")): str(r.get("reconciliation")) for r in results},
                decimal_rank=int(chosen["rank"]) if chosen else None,
                decimal_metric_value=str(chosen["metric_value"]) if chosen else None,
            )
        return None


_TRIAL_SQL = (
    "SELECT t.trial_id, t.ordinal, t.content_hash, t.parameters::text, r.outcome, r.numeric_tier, "
    "r.cost_policy_slot, r.result_content_hash, r.metrics::text, r.produced_at "
    "FROM strategy_lab_trials t LEFT JOIN strategy_lab_trial_results r ON r.trial_id = t.trial_id"
)


def _trial_view(row: tuple[Any, ...], context: _TrialContext) -> StrategyLabTrialView:
    metrics = _exact_json(row[8]) if row[8] is not None else {}
    parameters = _exact_json(row[3])
    return StrategyLabTrialView(
        trial_id=row[0], ordinal=int(row[1]), trial_content_hash=str(row[2]).strip(),
        parameters={str(k): _text(v) or "" for k, v in sorted(parameters.items())},
        outcome=str(row[4]) if row[4] is not None else "PENDING",  # type: ignore[arg-type]
        numeric_tier=_text(row[5]), cost_policy_slot=_text(row[6]),
        result_content_hash=str(row[7]).strip() if row[7] is not None else None,
        metrics={str(k): _text(v) for k, v in sorted(metrics.items())}, produced_at=row[9],
        frozen_rank=context.frozen.get(row[0]), rerun=context.rerun(row[0]), candidate_state=context.states.get(row[0]),
    )


def read_strategy_lab_trials_v1(
    cursor: _Cursor, study_id: UUID, *, limit: int, offset: int, sort: str = "ordinal",
    direction: Literal["asc", "desc"] = "asc", outcome: str = "ALL", frozen_only: bool = False,
) -> StrategyLabTrialPage:
    """One study's trials, sorted, filtered and paged in SQL.

    ``sort`` is ``ordinal`` or a stored metric key; the metric sorts by the
    ``numeric`` value of its exact stored text, values that are not plain
    numeric text (absent, null, inadmissible) sort last in either direction,
    and the trial ordinal breaks every tie. Raises ``ValueError`` on an
    unknown outcome, a malformed sort key or a direction other than asc/desc.
    """
    if outcome not in TRIAL_OUTCOME_FILTERS_V1:
        raise ValueError("unknown_trial_outcome_filter")
    if direction not in ("asc", "desc"):
        raise ValueError("unknown_sort_direction")
    if sort != "ordinal" and not re.fullmatch(_METRIC_KEY, sort):
        raise ValueError("malformed_sort_key")
    _summary_row(cursor, study_id)
    context = _TrialContext(cursor, study_id)
    clauses = ["t.study_id = %s"]
    params: list[Any] = [study_id]
    if outcome == "PENDING":
        clauses.append("r.trial_id IS NULL")
    elif outcome != "ALL":
        clauses.append("r.outcome = %s")
        params.append(outcome)
    if frozen_only:
        clauses.append("t.trial_id = ANY(%s)")
        params.append(list(context.frozen))
    where = " WHERE " + " AND ".join(clauses)
    cursor.execute("SELECT count(*) FROM strategy_lab_trials t LEFT JOIN strategy_lab_trial_results r "  # nosec B608
                   "ON r.trial_id = t.trial_id" + where, tuple(params))
    total = int((cursor.fetchone() or (0,))[0])
    order = direction.upper()  # validated above: ASC or DESC only
    if sort == "ordinal":
        order_sql = f" ORDER BY t.ordinal {order}"
        order_params: tuple[Any, ...] = ()
    else:
        order_sql = (f" ORDER BY CASE WHEN (r.metrics->>%s) ~ %s THEN (r.metrics->>%s)::numeric END "
                     f"{order} NULLS LAST, t.ordinal ASC")
        order_params = (sort, _NUMERIC_TEXT, sort)
    cursor.execute(_TRIAL_SQL + where + order_sql + " LIMIT %s OFFSET %s",
                   (*params, *order_params, limit + 1, offset))
    rows = cursor.fetchall()
    items = [_trial_view(row, context) for row in rows[:limit]]
    cursor.execute("SELECT DISTINCT jsonb_object_keys(metrics) FROM strategy_lab_trial_results WHERE study_id=%s "
                   "AND outcome='EVALUATED' ORDER BY 1", (study_id,))
    metric_keys = [str(r[0]) for r in cursor.fetchall()]
    return StrategyLabTrialPage(
        state="AVAILABLE" if items else "UNAVAILABLE", study_id=study_id, sort=sort, direction=direction,
        outcome=outcome, frozen_only=frozen_only, metric_keys=metric_keys, items=items, total=total,
        page=StrategyLabPageInfo(limit=limit, offset=offset, returned=len(items), has_more=len(rows) > limit),
    )


def read_strategy_lab_trial_v1(cursor: _Cursor, study_id: UUID, trial_id: UUID) -> StrategyLabTrialDetailView:
    """One trial with its stored search metrics and every recorded Decimal rerun lag, verbatim."""
    summary, identity = _summary(_summary_row(cursor, study_id))
    cursor.execute(_TRIAL_SQL + " WHERE t.study_id=%s AND t.trial_id=%s", (study_id, trial_id))
    row = cursor.fetchone()
    if row is None:
        raise StrategyLabObjectNotFound("strategy_lab_trial_not_found")
    context = _TrialContext(cursor, study_id)
    trial = _trial_view(row, context)
    lags: list[StrategyLabTrialRerunLagView] = []
    if trial.rerun is not None:
        identity_by_hash = {r[0]: r[4] for r in context.reruns}
        for result in identity_by_hash[trial.rerun.rerun_hash].get("results", []):
            if str(result.get("trial_id")) != str(trial_id):
                continue
            lags.append(StrategyLabTrialRerunLagView(
                lag_micros=int(result["lag_micros"]), reconciliation=str(result.get("reconciliation")),
                held_divergence_bars=_int_or_none(result.get("held_divergence_bars")),
                trade_count_divergence=_int_or_none(result.get("trade_count_divergence")),
                float_near_tie_decisions=_int_or_none(result.get("float_near_tie_decisions")),
                metrics={str(k): _text(v) for k, v in sorted((result.get("metrics") or {}).items())},
            ))
        lags.sort(key=lambda lag: lag.lag_micros)
    return StrategyLabTrialDetailView(
        trial=trial, study_label=summary.label, strategy_family=summary.strategy_family, rerun_lags=lags,
        candidate_states=_candidate_states(cursor, study_id, trial_id), economics=economics_v1(identity),
    )


__all__ = [
    "TRIAL_OUTCOME_FILTERS_V1",
    "StrategyLabAuthorityView",
    "StrategyLabCandidateSetView",
    "StrategyLabCandidateStateView",
    "StrategyLabCandidateView",
    "StrategyLabDatasetRefView",
    "StrategyLabEconomicsView",
    "StrategyLabHoldoutView",
    "StrategyLabManifestView",
    "StrategyLabObjectNotFound",
    "StrategyLabPageInfo",
    "StrategyLabRerunView",
    "StrategyLabStudyDetailView",
    "StrategyLabStudyFacets",
    "StrategyLabStudyPage",
    "StrategyLabStudySummaryView",
    "StrategyLabTrialDetailView",
    "StrategyLabTrialPage",
    "StrategyLabTrialRerunView",
    "StrategyLabTrialView",
    "economics_v1",
    "read_strategy_lab_studies_v1",
    "read_strategy_lab_study_v1",
    "read_strategy_lab_trial_v1",
    "read_strategy_lab_trials_v1",
    "window_index_v1",
]


