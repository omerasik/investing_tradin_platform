"""Evidence-based comparison of frozen Strategy Lab candidates (read-only projection).

``RESEARCH_ONLY``. One row per frozen candidate of one or more studies, built
from recorded, content-addressed performance evidence: the frozen candidate set
(R4.4), the stored search result, the Decimal authority rerun of that set
(R4.7), the study's C.1.1 walk-forward reports and recorded R6 candidate
states. Nothing here writes, runs a search, opens or reads the holdout,
emits a signal or touches a migration.

What it is not
--------------
It is not a ranking of quality. It computes no composite score, applies no
weight or acceptance threshold, and makes no profitability or trade-readiness claim.
Every row restates ``SEARCH_NON_AUTHORITATIVE``, ``GROSS_NON_PROMOTABLE``,
``NOT_VALIDATED`` and ``NO_EXECUTION_AUTHORITY`` with the study's own
authority reasons, carries ``promotable = False`` and
``execution_authority = "NONE"``.

Dimensions stay separate
------------------------
A row never blends one kind of evidence into another:

* ``search`` -- the float64 in-sample search metrics, verbatim stored text,
  with the multiple-testing denominator and the freeze rule's cutoff tie.
* ``decimal_rerun`` -- the latest recorded Decimal rerun *of this frozen set*:
  selection status, whether this candidate is in the Decimal selection, the
  baseline-lag reconciliation and metrics, and every OR-5 sweep lag kept
  (absent sweep lags are listed, never filled). ``NO_DECIMAL_RERUN_RECORDED``
  when none exists.
* ``walk_forward`` -- each recorded walk-forward report of the study kept
  apart (different plans are never merged): the folds whose train side
  selected *this* candidate, their test-side gross metrics, and per-metric
  dispersion over exactly those folds (count, missing, min, max, nearest-rank
  median, exact ``max - min`` spread only with two or more values, sign
  counts). A report that never selected the candidate gives no dispersion.
  Walk-forward is ``WALK_FORWARD_DEVELOPMENT``, never validation.
* ``economics`` -- gross versus costed: the study's cost mode, the rerun's
  recorded cost and funding modes, and verified costs / net profitability,
  which this projection can only state as ``UNAVAILABLE`` (no verified fee
  schedule, OR-6).
* ``cost_fragility`` -- the recorded Decimal baseline's gross break-even cost
  per side and exact proposed-slippage remainders. The existing prepared
  CONSERVATIVE_MEDIUM grid is explicitly sourced, proposed and never in net;
  study-bound scenarios remain distinct. Missing authoritative development
  evidence yields UNAVAILABLE, never a search-tier substitute. The metric is
  based on summed step returns, not compounded total-return break-even.
* ``provenance`` -- every dataset binding with its evidence tier and the
  claim that tier allows (T2 ``CONDITIONAL``, T1 descriptive, T0 engineering,
  T3/T4 professional tier yet not validated), the OR-5 baseline lag and
  claim ceiling, and the study's evaluation bound against the untouched
  holdout boundary.

A value that is not recorded is ``MISSING`` (``RecordedMetricV1.state``),
never zero; a recorded value that is not plain numeric text stays verbatim
and is reported ``NOT_NUMERIC`` where a number is needed.

Sorting
-------
Only by one operator-chosen recorded metric (:class:`ComparisonSortV1`):
``SEARCH`` (stored search metric) or ``DECIMAL_BASELINE`` (the rerun's
baseline-lag Decimal metric), in an explicit direction -- no default. Values
compare as exact ``Decimal``. Equal values share a dense ``sort_position``
and report ``tied_with``; rows whose value is ``MISSING`` or ``NOT_NUMERIC``
get no position and follow every positioned row. Inside a tie and among the
unpositioned rows the order is the identity order (study input order, then
frozen rank), which is declared as a tie-break, not a preference. Without a
sort the rows stay in identity order (``UNSORTED``). Walk-forward dispersion
is computed, not recorded, and is never a sort key.

Cross-study differences
-----------------------
``differences`` lists, as facts, where the compared studies are not like for
like (bound datasets, evidence tiers, baseline lags, cost modes, freeze rules,
multiple-testing denominators, strategy families). It judges nothing.

Identity and determinism
------------------------
Frozen candidate, study, candidate set, rerun and walk-forward identities are
kept verbatim; every stored identity must re-derive to its hash and bind to
the same study (and the rerun to the same frozen set), else the projection
refuses (:class:`CandidateComparisonError`). The output carries no clock and
``output_hash`` is SHA-256 over its canonical JSON (Decimal only, no float).

Consumer contract (import-light: stdlib dataclasses, no numpy/pydantic)
----------------------------------------------------------------------
* :func:`compare_candidates_v1` ``(evidence, *, sort) -> CandidateComparisonV1``
  -- pure projection over :class:`StudyComparisonEvidenceV1` inputs.
* :func:`read_candidate_comparison_v1` ``(cursor, *, studies, sort)`` -- loads
  the evidence of up to :data:`MAX_STUDIES_V1` :class:`StudySelectionV1`
  entries (study id + optional candidate set hash; latest recorded set
  otherwise) and projects it. A study without a frozen set is listed in
  ``studies_without_frozen_set`` with no rows; an unknown study raises
  :class:`CandidateComparisonNotFound`.
* :meth:`CandidateComparisonV1.payload` -- the JSON-ready dict (lists, text,
  ints, booleans, ``None``) that ``output_hash`` is computed over.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from enum import StrEnum
from typing import Any, Final, Literal, Protocol
from uuid import UUID

from .research_cost_fragility_v1 import CostFragilityV1, cost_fragility_v1
from .strategy_lab_read_model_v1 import _authority as _study_authority
from .strategy_lab_read_model_v1 import economics_v1
from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1, identity_hash_v1

COMPARISON_SCHEMA_VERSION_V1: Final = "research-candidate-comparison-v1"
MAX_STUDIES_V1: Final = 20
RESTATED_CLAIMS_V1: Final = ("SEARCH_NON_AUTHORITATIVE", "GROSS_NON_PROMOTABLE", "NOT_VALIDATED",
                             "NO_EXECUTION_AUTHORITY")
TIE_BREAK_V1: Final = "STUDY_INPUT_ORDER_THEN_FROZEN_RANK_NOT_A_PREFERENCE"

#: The claim each evidence tier allows; none of them is a validation.
TIER_CLAIMS_V1: Final[Mapping[str, str]] = {
    "T0_SYNTHETIC": "ENGINEERING_ONLY",
    "T1_RETROSPECTIVE": "DESCRIPTIVE_NO_PERFORMANCE_CLAIM",
    "T2_EVENT_TIME": "CONDITIONAL",
    "T3_PUBLICATION_TIME": "PROFESSIONAL_TIER_NOT_VALIDATED",
    "T4_FIRST_PARTY_CAPTURE": "PROFESSIONAL_TIER_NOT_VALIDATED",
}

MetricState = Literal["RECORDED", "MISSING"]
SortState = Literal["VALUE", "MISSING", "NOT_NUMERIC", "UNSORTED"]


class CandidateComparisonError(ValueError):
    """Stored evidence does not re-derive or does not bind together: the comparison refuses."""


class CandidateComparisonNotFound(LookupError):
    """A requested study does not exist."""


class SortDimensionV1(StrEnum):
    SEARCH = "SEARCH"
    DECIMAL_BASELINE = "DECIMAL_BASELINE"


@dataclass(frozen=True, slots=True)
class ComparisonSortV1:
    """One recorded metric of one dimension, in an explicit direction. No default exists."""

    dimension: SortDimensionV1
    metric: str
    direction: Literal["ASC", "DESC"]

    def __post_init__(self) -> None:
        if not isinstance(self.dimension, SortDimensionV1):
            raise CandidateComparisonError("sort_dimension_unknown")
        if not isinstance(self.metric, str) or not self.metric or len(self.metric) > 64 \
                or not self.metric.replace("_", "").isalnum() or not self.metric[0].isalpha():
            raise CandidateComparisonError("sort_metric_malformed")
        if self.direction not in ("ASC", "DESC"):
            raise CandidateComparisonError("sort_direction_must_be_explicit")


@dataclass(frozen=True, slots=True)
class StudySelectionV1:
    study_id: UUID
    #: ``None`` selects the latest recorded frozen set of the study.
    candidate_set_hash: str | None = None


# --- inputs ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StudyComparisonEvidenceV1:
    """Everything recorded for one study's frozen set, as stored (identities verbatim)."""

    study_id: str
    study_content_hash: str
    strategy_family: str
    label: str
    study_identity: Mapping[str, Any]
    candidate_set_hash: str
    candidate_set: Mapping[str, Any]
    #: ``trial_id -> recorded parameters`` (exact text).
    trial_parameters: Mapping[str, Mapping[str, Any]]
    #: ``trial_id -> {"outcome", "result_content_hash", "metrics"}`` of the stored search results.
    search_results: Mapping[str, Mapping[str, Any]]
    #: ``(rerun_hash, identity)`` of Decimal reruns of this candidate set, in recorded order.
    reruns: Sequence[tuple[str, Mapping[str, Any]]] = ()
    #: ``(report_hash, identity)`` of the study's walk-forward reports, in recorded order.
    walk_forward: Sequence[tuple[str, Mapping[str, Any]]] = ()
    #: ``{"trial_id", "state", "evidence_hash", "reasons"}`` in recorded order (the latest wins).
    candidate_states: Sequence[Mapping[str, Any]] = ()


# --- outputs -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecordedMetricV1:
    key: str
    state: MetricState
    #: The stored text, verbatim; ``None`` only when ``MISSING``.
    value: str | None


@dataclass(frozen=True, slots=True)
class CandidateIdentityV1:
    study_id: str
    study_content_hash: str
    study_label: str
    strategy_family: str
    manifest_hash: str
    candidate_set_hash: str
    freeze_rule: dict[str, Any]
    trial_id: str
    trial_content_hash: str
    result_content_hash: str
    frozen_rank: int
    parameters: dict[str, str]


@dataclass(frozen=True, slots=True)
class SearchDimensionV1:
    claim: Literal["SEARCH_NON_AUTHORITATIVE"]
    outcome: str
    metrics: list[RecordedMetricV1]
    frozen_metric: str
    frozen_metric_value: str
    multiple_testing_trial_count: int
    eligible_count: int
    ineligible_count: int
    cutoff_tie: bool


@dataclass(frozen=True, slots=True)
class RerunLagV1:
    lag_micros: int
    reconciliation: str
    held_divergence_bars: int | None
    trade_count_divergence: int | None
    metrics: list[RecordedMetricV1]


@dataclass(frozen=True, slots=True)
class DecimalRerunDimensionV1:
    claim: Literal["DECIMAL_SELECTION_ESTABLISHED", "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED",
                   "NO_DECIMAL_RERUN_RECORDED"]
    economics: Literal["GROSS_CONDITIONAL_NON_PROMOTABLE"]
    reruns_recorded: int
    rerun_hash: str | None
    selection_status: str | None
    #: ``None`` when no rerun is recorded or the selection is not established (fail closed).
    in_decimal_selection: bool | None
    decimal_rank: int | None
    decimal_selection_changed_versus_search: bool | None
    claim_ceiling: str | None
    baseline: RerunLagV1 | None
    sweep: list[RerunLagV1]
    #: Sweep lags the rerun declares but holds no result for this candidate.
    sweep_lags_missing: list[int]


@dataclass(frozen=True, slots=True)
class FoldTestV1:
    fold: int
    test_start: str
    test_end_exclusive: str
    train_metric_value: str | None
    selection_tie: bool | None
    flags: list[str]
    metrics: list[RecordedMetricV1]


@dataclass(frozen=True, slots=True)
class FoldDispersionV1:
    """Facts over the folds that selected the candidate; a computed spread is not a recorded metric."""

    metric: str
    folds_with_value: int
    folds_missing: int
    folds_not_numeric: int
    min: str | None
    max: str | None
    median_nearest_rank: str | None
    #: Exact ``max - min``; only with two or more numeric values.
    spread: str | None
    positive: int
    zero: int
    negative: int


@dataclass(frozen=True, slots=True)
class WalkForwardReportFactsV1:
    report_hash: str
    split_hash: str
    dataset_content_hash: str
    rule: dict[str, Any]
    evaluation_end_exclusive: str
    multiple_testing_trial_count: int
    fold_count: int
    folds_excluded_no_eligible_trial: int
    folds_selected_other_trial: int
    folds_selected_this_candidate: list[int]
    candidate_folds: list[FoldTestV1]
    dispersion: list[FoldDispersionV1]


@dataclass(frozen=True, slots=True)
class WalkForwardDimensionV1:
    claim: Literal["WALK_FORWARD_DEVELOPMENT", "NO_WALK_FORWARD_REPORT_RECORDED"]
    numeric_tier: Literal["SEARCH_NON_AUTHORITATIVE"]
    cost_mode: Literal["GROSS_NON_PROMOTABLE"]
    reports: list[WalkForwardReportFactsV1]


@dataclass(frozen=True, slots=True)
class EconomicsDimensionV1:
    completeness: Literal["GROSS_ONLY", "COST_SCENARIO_APPLIED_UNVERIFIED"]
    study_cost_mode: str
    decimal_cost_mode: RecordedMetricV1
    decimal_funding_mode: RecordedMetricV1
    verified_costs: Literal["UNAVAILABLE"]
    net_profitability: Literal["UNAVAILABLE"]
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class DatasetProvenanceV1:
    role: str
    dataset_version_id: str
    content_hash: str
    evidence_tier: str
    tier_claim: str
    knowledge_upper_bound_exclusive: str | None


@dataclass(frozen=True, slots=True)
class ProvenanceDimensionV1:
    datasets: list[DatasetProvenanceV1]
    baseline_lag_micros: int | None
    timing_claim_ceiling: str | None
    evaluation_upper_bound_exclusive: str | None
    holdout_boundary: str
    #: ``None`` when the study identity records no evaluation bound.
    evaluation_before_holdout: bool | None


@dataclass(frozen=True, slots=True)
class CandidateStateFactV1:
    state: str
    evidence_hash: str
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class CandidateComparisonRowV1:
    identity: CandidateIdentityV1
    search: SearchDimensionV1
    decimal_rerun: DecimalRerunDimensionV1
    walk_forward: WalkForwardDimensionV1
    economics: EconomicsDimensionV1
    cost_fragility: CostFragilityV1
    provenance: ProvenanceDimensionV1
    #: The latest recorded R6 state; ``None`` = none recorded (never inferred).
    candidate_state: CandidateStateFactV1 | None
    restated_claims: list[str]
    authority_reasons: list[str]
    promotable: Literal[False]
    execution_authority: Literal["NONE"]
    sort_state: SortState
    sort_value: str | None
    #: Dense position in the chosen direction, 1 first; ``None`` unless ``sort_state == "VALUE"``.
    sort_position: int | None
    #: Other rows holding exactly the same sort value.
    tied_with: int


@dataclass(frozen=True, slots=True)
class CrossStudyDifferencesV1:
    dataset_content_hashes: list[str]
    evidence_tiers: list[str]
    baseline_lags_micros: list[int | None]
    cost_modes: list[str]
    freeze_rules: list[str]
    multiple_testing_trial_counts: list[int]
    strategy_families: list[str]
    #: Named facts, e.g. ``DIFFERENT_BOUND_DATASETS``; empty means none of these differ.
    differences: list[str]


@dataclass(frozen=True, slots=True)
class CandidateComparisonV1:
    schema_version: str
    claim: Literal["RESEARCH_COMPARISON_NON_AUTHORITATIVE"]
    restated_claims: list[str]
    composite_score: Literal["NONE"]
    sort: dict[str, str] | None
    tie_break: str
    studies: list[str]
    studies_without_frozen_set: list[str]
    rows: list[CandidateComparisonRowV1]
    sort_counts: dict[str, int]
    cross_study: CrossStudyDifferencesV1
    holdout: Literal["NOT_READ"]
    output_hash: str

    def payload(self) -> dict[str, Any]:
        return _plain(self)  # type: ignore[no-any-return]


# --- helpers -----------------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _exact(text: str | None) -> Decimal | None:
    """The exact finite value of numeric text; anything else is ``None``."""
    if text is None or text in ("true", "false"):
        return None
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _number_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "0") else text


def _metrics(raw: Mapping[str, Any] | None, keys: Sequence[str]) -> list[RecordedMetricV1]:
    stored = raw or {}
    out = []
    for key in keys:
        text = _text(stored.get(key))
        out.append(RecordedMetricV1(key, "MISSING" if text is None else "RECORDED", text))
    return out


def _require_hash(identity: Mapping[str, Any], expected: str, what: str) -> None:
    if identity_hash_v1(dict(identity)) != expected:
        raise CandidateComparisonError(f"stored_{what}_does_not_rederive")


def _instant(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# --- dimensions --------------------------------------------------------------------------------


def _rerun_lag(result: Mapping[str, Any], keys: Sequence[str]) -> RerunLagV1:
    return RerunLagV1(
        lag_micros=int(result["lag_micros"]), reconciliation=str(result.get("reconciliation")),
        held_divergence_bars=_int_or_none(result.get("held_divergence_bars")),
        trade_count_divergence=_int_or_none(result.get("trade_count_divergence")),
        metrics=_metrics(result.get("metrics"), keys),
    )


def _decimal_dimension(trial_id: str, reruns: Sequence[tuple[str, Mapping[str, Any]]]) -> DecimalRerunDimensionV1:
    if not reruns:
        return DecimalRerunDimensionV1(
            claim="NO_DECIMAL_RERUN_RECORDED", economics="GROSS_CONDITIONAL_NON_PROMOTABLE", reruns_recorded=0,
            rerun_hash=None, selection_status=None, in_decimal_selection=None, decimal_rank=None,
            decimal_selection_changed_versus_search=None, claim_ceiling=None, baseline=None, sweep=[],
            sweep_lags_missing=[])
    rerun_hash, identity = reruns[-1]  # the latest recorded rerun of this frozen set
    selection = identity.get("authoritative_selection") or {}
    status = str(selection.get("status"))
    established = status == "ESTABLISHED"
    baseline_lag = _int_or_none(identity.get("baseline_lag_micros"))
    results = sorted((r for r in identity.get("results", []) if str(r.get("trial_id")) == trial_id),
                     key=lambda r: int(r["lag_micros"]))
    keys = sorted({str(k) for r in results for k in (r.get("metrics") or {})})
    by_lag = {int(r["lag_micros"]): r for r in results}
    sweep_lags = [int(v) for v in identity.get("sweep_lags_micros", [])]
    chosen = {str(item["trial_id"]): item for item in selection.get("selected", [])} if established else {}
    changed = selection.get("changed_versus_search_selection")
    return DecimalRerunDimensionV1(
        claim="DECIMAL_SELECTION_ESTABLISHED" if established else "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED",
        economics="GROSS_CONDITIONAL_NON_PROMOTABLE", reruns_recorded=len(reruns), rerun_hash=rerun_hash,
        selection_status=status, in_decimal_selection=(trial_id in chosen) if established else None,
        decimal_rank=int(chosen[trial_id]["rank"]) if trial_id in chosen else None,
        decimal_selection_changed_versus_search=changed if isinstance(changed, bool) else None,
        claim_ceiling=_text(identity.get("claim_ceiling")),
        baseline=_rerun_lag(by_lag[baseline_lag], keys) if baseline_lag in by_lag else None,
        sweep=[_rerun_lag(by_lag[lag], keys) for lag in sweep_lags if lag in by_lag],
        sweep_lags_missing=[lag for lag in sweep_lags if lag not in by_lag],
    )


def _dispersion(metric: str, texts: Sequence[str | None]) -> FoldDispersionV1:
    values = [v for v in (_exact(t) for t in texts if t is not None) if v is not None]
    missing = sum(1 for t in texts if t is None)
    ordered = sorted(values)
    spread = None
    if len(ordered) >= 2:
        with localcontext() as ctx:
            ctx.prec = 200
            ctx.traps[Inexact] = True  # the spread is exact or the projection refuses
            spread = _number_text(ordered[-1] - ordered[0])
    return FoldDispersionV1(
        metric=metric, folds_with_value=len(ordered), folds_missing=missing,
        folds_not_numeric=len(texts) - missing - len(ordered),
        min=_number_text(ordered[0]) if ordered else None, max=_number_text(ordered[-1]) if ordered else None,
        median_nearest_rank=_number_text(ordered[(len(ordered) + 1) // 2 - 1]) if ordered else None,
        spread=spread, positive=sum(1 for v in ordered if v > 0), zero=sum(1 for v in ordered if v == 0),
        negative=sum(1 for v in ordered if v < 0),
    )


def _walk_forward_dimension(trial_id: str, reports: Sequence[tuple[str, Mapping[str, Any]]],
                            ) -> WalkForwardDimensionV1:
    facts = []
    for report_hash, identity in reports:
        folds = list(identity.get("folds", []))
        mine = [f for f in folds if isinstance(f.get("selected"), Mapping)
                and str(f["selected"].get("trial_id")) == trial_id]
        keys = sorted({str(k) for f in mine for k in (f["selected"].get("test_metrics") or {})})
        candidate_folds = [FoldTestV1(
            fold=int(f["fold"]), test_start=str(f.get("test_start")),
            test_end_exclusive=str(f.get("test_end_exclusive")),
            train_metric_value=_text(f["selected"].get("train_metric_value")),
            selection_tie=f.get("selection_tie") if isinstance(f.get("selection_tie"), bool) else None,
            flags=[str(flag) for flag in f.get("flags", [])],
            metrics=_metrics(f["selected"].get("test_metrics"), keys)) for f in mine]
        excluded = sum(1 for f in folds if f.get("selected") is None)
        facts.append(WalkForwardReportFactsV1(
            report_hash=report_hash, split_hash=str(identity.get("split_hash")),
            dataset_content_hash=str((identity.get("dataset") or {}).get("content_hash")),
            rule=dict(identity.get("rule") or {}),
            evaluation_end_exclusive=str((identity.get("plan") or {}).get("evaluation_end_exclusive")),
            multiple_testing_trial_count=int(identity["multiple_testing_trial_count"]),
            fold_count=len(folds), folds_excluded_no_eligible_trial=excluded,
            folds_selected_other_trial=len(folds) - excluded - len(mine),
            folds_selected_this_candidate=[fold.fold for fold in candidate_folds], candidate_folds=candidate_folds,
            dispersion=[_dispersion(key, [next(m.value for m in fold.metrics if m.key == key)
                                          for fold in candidate_folds]) for key in keys],
        ))
    return WalkForwardDimensionV1(
        claim="WALK_FORWARD_DEVELOPMENT" if facts else "NO_WALK_FORWARD_REPORT_RECORDED",
        numeric_tier="SEARCH_NON_AUTHORITATIVE", cost_mode="GROSS_NON_PROMOTABLE", reports=facts)


def _economics(identity: Mapping[str, Any], decimal: DecimalRerunDimensionV1) -> EconomicsDimensionV1:
    view = economics_v1(dict(identity))
    baseline = {m.key: m for m in decimal.baseline.metrics} if decimal.baseline else {}
    cost = baseline.get("cost_mode", RecordedMetricV1("cost_mode", "MISSING", None))
    funding = baseline.get("funding_mode", RecordedMetricV1("funding_mode", "MISSING", None))
    costed = cost.value is not None and cost.value.startswith("NET_OF:")
    reasons = list(view.net_profitability_reasons)
    if decimal.baseline is None:
        reasons.append("NO_DECIMAL_BASELINE_RECORDED")
    if funding.state == "MISSING":
        reasons.append("FUNDING_NOT_CHARGED_IN_RECORDED_RERUN")
    return EconomicsDimensionV1(
        completeness="COST_SCENARIO_APPLIED_UNVERIFIED" if costed else "GROSS_ONLY", study_cost_mode=view.cost_mode,
        decimal_cost_mode=cost, decimal_funding_mode=funding, verified_costs="UNAVAILABLE",
        net_profitability="UNAVAILABLE", reasons=list(dict.fromkeys(reasons)),
    )


def _provenance(identity: Mapping[str, Any]) -> ProvenanceDimensionV1:
    datasets = [DatasetProvenanceV1(
        role=str(b.get("role", "")), dataset_version_id=str(b.get("dataset_version_id")),
        content_hash=str(b.get("content_hash")), evidence_tier=str(b.get("evidence_tier")),
        tier_claim=TIER_CLAIMS_V1.get(str(b.get("evidence_tier")), "UNKNOWN_TIER_NO_CLAIM"),
        knowledge_upper_bound_exclusive=_text(b.get("knowledge_upper_bound_exclusive")),
    ) for b in identity.get("datasets", []) if isinstance(b, Mapping)]
    policies = identity.get("policies")
    raw_timing = policies.get("timing") if isinstance(policies, Mapping) else None
    timing: Mapping[str, Any] = raw_timing if isinstance(raw_timing, Mapping) else {}
    bound_text = _text(identity.get("evaluation_upper_bound_exclusive"))
    bound = _instant(bound_text)
    return ProvenanceDimensionV1(
        datasets=datasets, baseline_lag_micros=_int_or_none(timing.get("baseline_dissemination_lag_micros")),
        timing_claim_ceiling=_text(timing.get("claim_ceiling")), evaluation_upper_bound_exclusive=bound_text,
        holdout_boundary=UNTOUCHED_HOLDOUT_BOUNDARY_V1.isoformat(),
        evaluation_before_holdout=None if bound is None else bound <= UNTOUCHED_HOLDOUT_BOUNDARY_V1,
    )


# --- projection --------------------------------------------------------------------------------


def _verify(evidence: StudyComparisonEvidenceV1) -> None:
    _require_hash(evidence.study_identity, evidence.study_content_hash, "study_identity")
    _require_hash(evidence.candidate_set, evidence.candidate_set_hash, "candidate_set")
    if str(evidence.candidate_set.get("study_id")) != evidence.study_id:
        raise CandidateComparisonError("candidate_set_is_not_from_this_study")
    for rerun_hash, identity in evidence.reruns:
        _require_hash(identity, rerun_hash, "rerun")
        if (str(identity.get("candidate_set_hash")), str(identity.get("study_content_hash"))) != \
                (evidence.candidate_set_hash, evidence.study_content_hash):
            raise CandidateComparisonError("rerun_is_not_of_this_frozen_set")
        if str(identity.get("manifest_hash")) != str(evidence.candidate_set.get("manifest_hash")):
            raise CandidateComparisonError("rerun_is_not_of_this_manifest")
    for report_hash, identity in evidence.walk_forward:
        _require_hash(identity, report_hash, "walk_forward_report")
        if str(identity.get("study_content_hash")) != evidence.study_content_hash:
            raise CandidateComparisonError("walk_forward_report_is_not_of_this_study")


def _study_rows(evidence: StudyComparisonEvidenceV1, search_keys: Sequence[str]) -> list[CandidateComparisonRowV1]:
    _verify(evidence)
    cset = evidence.candidate_set
    rule = dict(cset.get("rule") or {})
    authority = list(_study_authority(dict(evidence.study_identity)).reasons)
    provenance = _provenance(evidence.study_identity)
    states: dict[str, Mapping[str, Any]] = {}
    for recorded in evidence.candidate_states:
        states[str(recorded["trial_id"])] = recorded  # recorded order: the latest wins
    rows = []
    for candidate in cset.get("candidates", []):
        trial_id = str(candidate["trial_id"])
        result = evidence.search_results.get(trial_id)
        if result is None or str(result.get("result_content_hash")) != str(candidate["result_content_hash"]):
            raise CandidateComparisonError("frozen_candidate_search_result_not_recorded_as_frozen")
        decimal = _decimal_dimension(trial_id, evidence.reruns)
        state = states.get(trial_id)
        rows.append(CandidateComparisonRowV1(
            identity=CandidateIdentityV1(
                study_id=evidence.study_id, study_content_hash=evidence.study_content_hash,
                study_label=evidence.label, strategy_family=evidence.strategy_family,
                manifest_hash=str(cset.get("manifest_hash")), candidate_set_hash=evidence.candidate_set_hash,
                freeze_rule=rule, trial_id=trial_id, trial_content_hash=str(candidate["trial_content_hash"]),
                result_content_hash=str(candidate["result_content_hash"]), frozen_rank=int(candidate["rank"]),
                parameters={str(k): _text(v) or "" for k, v in
                            sorted((evidence.trial_parameters.get(trial_id) or {}).items())}),
            search=SearchDimensionV1(
                claim="SEARCH_NON_AUTHORITATIVE", outcome=str(result.get("outcome")),
                metrics=_metrics(result.get("metrics"), search_keys), frozen_metric=str(rule.get("metric")),
                frozen_metric_value=str(candidate["metric_value"]),
                multiple_testing_trial_count=int(cset["multiple_testing_trial_count"]),
                eligible_count=int(cset["eligible_count"]), ineligible_count=int(cset["ineligible_count"]),
                cutoff_tie=bool(cset["cutoff_tie"])),
            decimal_rerun=decimal,
            walk_forward=_walk_forward_dimension(trial_id, evidence.walk_forward),
            economics=_economics(evidence.study_identity, decimal), provenance=provenance,
            cost_fragility=cost_fragility_v1(
                baseline={m.key: m.value for m in decimal.baseline.metrics} if decimal.baseline else {},
                search=result.get("metrics") or {},
                lag_micros=decimal.baseline.lag_micros if decimal.baseline else None,
                cost_policy=(evidence.study_identity.get("policies") or {}).get("cost") or {},
                selection_established=decimal.selection_status == "ESTABLISHED",
                before_holdout=provenance.evaluation_before_holdout),
            candidate_state=None if state is None else CandidateStateFactV1(
                state=str(state["state"]), evidence_hash=str(state.get("evidence_hash")),
                reasons=[str(r) for r in state.get("reasons", [])]),
            restated_claims=list(RESTATED_CLAIMS_V1), authority_reasons=authority, promotable=False,
            execution_authority="NONE", sort_state="UNSORTED", sort_value=None, sort_position=None, tied_with=0,
        ))
    return rows


def _sort_text(row: CandidateComparisonRowV1, sort: ComparisonSortV1) -> str | None:
    if sort.dimension is SortDimensionV1.SEARCH:
        metrics = row.search.metrics
    else:
        metrics = row.decimal_rerun.baseline.metrics if row.decimal_rerun.baseline else []
    return next((m.value for m in metrics if m.key == sort.metric), None)


def _apply_sort(rows: list[CandidateComparisonRowV1], sort: ComparisonSortV1 | None) -> list[CandidateComparisonRowV1]:
    if sort is None:
        return rows
    present: list[tuple[CandidateComparisonRowV1, str, Decimal]] = []
    absent: list[CandidateComparisonRowV1] = []
    for row in rows:
        text = _sort_text(row, sort)
        value = _exact(text)
        if text is None or value is None:
            absent.append(_replace_sort(row, "MISSING" if text is None else "NOT_NUMERIC", text, None, 0))
        else:
            present.append((row, text, value))
    descending = sort.direction == "DESC"
    # Python's sort is stable: equal values keep the identity order (the declared tie-break).
    present.sort(key=lambda item: item[2], reverse=descending)
    position = {value: index + 1 for index, value in enumerate(sorted({v for _, _, v in present}, reverse=descending))}
    counts: dict[Decimal, int] = {}
    for _, _, value in present:
        counts[value] = counts.get(value, 0) + 1
    return [_replace_sort(row, "VALUE", text, position[value], counts[value] - 1)
            for row, text, value in present] + absent


def _replace_sort(row: CandidateComparisonRowV1, state: SortState, text: str | None, position: int | None,
                  tied_with: int) -> CandidateComparisonRowV1:
    values = {f.name: getattr(row, f.name) for f in fields(row)}
    values.update(sort_state=state, sort_value=text, sort_position=position, tied_with=tied_with)
    return CandidateComparisonRowV1(**values)


def _cross_study(evidence: Sequence[StudyComparisonEvidenceV1],
                 rows: Sequence[CandidateComparisonRowV1]) -> CrossStudyDifferencesV1:
    def distinct(values: Sequence[Any]) -> list[Any]:
        return list(dict.fromkeys(values))

    dataset_sets = distinct([",".join(sorted(str(b.get("content_hash")) for b in e.study_identity.get("datasets", [])))
                             for e in evidence])
    by_study = {row.identity.study_id: row for row in rows}
    tiers = distinct([d.evidence_tier for row in by_study.values() for d in row.provenance.datasets])
    lags = distinct([row.provenance.baseline_lag_micros for row in by_study.values()])
    costs = distinct([economics_v1(dict(e.study_identity)).cost_mode for e in evidence])
    rules = distinct([json.dumps(dict(e.candidate_set.get("rule") or {}), sort_keys=True, separators=(",", ":"))
                      for e in evidence])
    denominators = distinct([int(e.candidate_set["multiple_testing_trial_count"]) for e in evidence])
    families = distinct([e.strategy_family for e in evidence])
    checks = (("DIFFERENT_BOUND_DATASETS", dataset_sets), ("DIFFERENT_EVIDENCE_TIERS", tiers),
              ("DIFFERENT_BASELINE_LAGS", lags), ("DIFFERENT_COST_MODES", costs),
              ("DIFFERENT_FREEZE_RULES", rules), ("DIFFERENT_MULTIPLE_TESTING_DENOMINATORS", denominators),
              ("DIFFERENT_STRATEGY_FAMILIES", families))
    return CrossStudyDifferencesV1(
        dataset_content_hashes=dataset_sets, evidence_tiers=tiers, baseline_lags_micros=lags, cost_modes=costs,
        freeze_rules=rules, multiple_testing_trial_counts=denominators, strategy_families=families,
        differences=[name for name, values in checks if len(values) > 1],
    )


def compare_candidates_v1(
    evidence: Sequence[StudyComparisonEvidenceV1], *, sort: ComparisonSortV1 | None,
    studies_without_frozen_set: Sequence[str] = (),
) -> CandidateComparisonV1:
    """Project the recorded evidence of each study's frozen set into comparison rows (see module docstring)."""
    if len(evidence) + len(studies_without_frozen_set) > MAX_STUDIES_V1:
        raise CandidateComparisonError("too_many_studies")
    ids = [e.study_id for e in evidence] + list(studies_without_frozen_set)
    if len(set(ids)) != len(ids):
        raise CandidateComparisonError("duplicate_study")
    if sort is not None and not isinstance(sort, ComparisonSortV1):
        raise CandidateComparisonError("sort_must_be_a_comparison_sort")
    search_keys = sorted({str(k) for e in evidence for c in e.candidate_set.get("candidates", [])
                          for k in ((e.search_results.get(str(c["trial_id"])) or {}).get("metrics") or {})})
    rows = [row for e in evidence for row in _study_rows(e, search_keys)]
    rows = _apply_sort(rows, sort)
    sort_counts = {state: sum(1 for row in rows if row.sort_state == state)
                   for state in ("VALUE", "MISSING", "NOT_NUMERIC", "UNSORTED")}
    body: dict[str, Any] = {
        "schema_version": COMPARISON_SCHEMA_VERSION_V1, "claim": "RESEARCH_COMPARISON_NON_AUTHORITATIVE",
        "restated_claims": list(RESTATED_CLAIMS_V1), "composite_score": "NONE",
        "sort": None if sort is None else {"dimension": sort.dimension.value, "metric": sort.metric,
                                           "direction": sort.direction},
        "tie_break": TIE_BREAK_V1, "studies": ids, "studies_without_frozen_set": list(studies_without_frozen_set),
        "rows": rows, "sort_counts": {k: v for k, v in sort_counts.items() if v},
        "cross_study": _cross_study(evidence, rows), "holdout": "NOT_READ",
    }
    return CandidateComparisonV1(**body, output_hash=identity_hash_v1(_plain(body)))


# --- persisted evidence ------------------------------------------------------------------------


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...


def _json(raw: object) -> Any:
    return json.loads(raw) if isinstance(raw, (str, bytes)) else raw


def _exact_json(raw: object) -> Any:
    """JSON with every float kept as its exact text (callers select ``jsonb::text``)."""
    return json.loads(raw, parse_float=str) if isinstance(raw, (str, bytes)) else raw


def _load_study(cursor: _Cursor, selection: StudySelectionV1) -> StudyComparisonEvidenceV1 | None:
    cursor.execute("SELECT content_hash, strategy_family, label, identity FROM strategy_lab_studies "
                   "WHERE study_id=%s", (selection.study_id,))
    study = cursor.fetchone()
    if study is None:
        raise CandidateComparisonNotFound("strategy_lab_study_not_found")
    cursor.execute("SELECT candidate_set_hash, identity FROM strategy_lab_candidate_sets WHERE study_id=%s "
                   "ORDER BY recorded_at, candidate_set_hash", (selection.study_id,))
    sets = [(str(h).strip(), _json(identity)) for h, identity in cursor.fetchall()]
    if selection.candidate_set_hash is not None:
        sets = [item for item in sets if item[0] == selection.candidate_set_hash]
        if not sets:
            raise CandidateComparisonNotFound("candidate_set_not_found_for_study")
    if not sets:
        return None
    set_hash, cset = sets[-1]
    trial_ids = [str(c["trial_id"]) for c in cset.get("candidates", [])]
    cursor.execute("SELECT t.trial_id::text, t.parameters::text, r.outcome, r.result_content_hash, r.metrics::text "
                   "FROM strategy_lab_trials t LEFT JOIN strategy_lab_trial_results r ON r.trial_id = t.trial_id "
                   "WHERE t.study_id=%s AND t.trial_id::text = ANY(%s)", (selection.study_id, trial_ids))
    parameters: dict[str, Any] = {}
    results: dict[str, Any] = {}
    for trial_id, raw_parameters, outcome, result_hash, raw_metrics in cursor.fetchall():
        parameters[str(trial_id)] = _exact_json(raw_parameters)
        if outcome is not None:
            results[str(trial_id)] = {"outcome": str(outcome), "result_content_hash": str(result_hash).strip(),
                                      "metrics": _exact_json(raw_metrics) if raw_metrics is not None else {}}
    cursor.execute("SELECT rerun_hash, identity FROM strategy_lab_authority_reruns WHERE study_id=%s "
                   "AND candidate_set_hash=%s ORDER BY recorded_at, rerun_hash", (selection.study_id, set_hash))
    reruns = [(str(h).strip(), _json(identity)) for h, identity in cursor.fetchall()]
    cursor.execute("SELECT report_hash, identity FROM strategy_lab_walk_forward_reports WHERE study_id=%s "
                   "ORDER BY recorded_at, report_hash", (selection.study_id,))
    reports = [(str(h).strip(), _json(identity)) for h, identity in cursor.fetchall()]
    cursor.execute("SELECT trial_id::text, state, evidence_hash, reasons FROM strategy_lab_candidate_states "
                   "WHERE study_id=%s ORDER BY recorded_at, trial_id, evidence_hash", (selection.study_id,))
    states = [{"trial_id": str(r[0]), "state": str(r[1]), "evidence_hash": str(r[2]).strip(),
               "reasons": list(_json(r[3]))} for r in cursor.fetchall()]
    return StudyComparisonEvidenceV1(
        study_id=str(selection.study_id), study_content_hash=str(study[0]).strip(), strategy_family=str(study[1]),
        label=str(study[2]), study_identity=_json(study[3]), candidate_set_hash=set_hash, candidate_set=cset,
        trial_parameters=parameters, search_results=results, reruns=reruns, walk_forward=reports,
        candidate_states=states,
    )


def read_candidate_comparison_v1(
    cursor: _Cursor, *, studies: Sequence[StudySelectionV1], sort: ComparisonSortV1 | None,
) -> CandidateComparisonV1:
    """Load each selected study's recorded evidence and project it (read-only, bounded)."""
    if not studies:
        raise CandidateComparisonError("no_study_selected")
    if len(studies) > MAX_STUDIES_V1:
        raise CandidateComparisonError("too_many_studies")
    if len({s.study_id for s in studies}) != len(studies):
        raise CandidateComparisonError("duplicate_study")
    evidence: list[StudyComparisonEvidenceV1] = []
    without: list[str] = []
    for selection in studies:
        loaded = _load_study(cursor, selection)
        if loaded is None:
            without.append(str(selection.study_id))
        else:
            evidence.append(loaded)
    return compare_candidates_v1(evidence, sort=sort, studies_without_frozen_set=without)


__all__ = [
    "COMPARISON_SCHEMA_VERSION_V1",
    "MAX_STUDIES_V1",
    "RESTATED_CLAIMS_V1",
    "TIER_CLAIMS_V1",
    "TIE_BREAK_V1",
    "CandidateComparisonError",
    "CandidateComparisonNotFound",
    "CandidateComparisonRowV1",
    "CandidateComparisonV1",
    "CandidateIdentityV1",
    "CandidateStateFactV1",
    "ComparisonSortV1",
    "CrossStudyDifferencesV1",
    "DatasetProvenanceV1",
    "DecimalRerunDimensionV1",
    "EconomicsDimensionV1",
    "FoldDispersionV1",
    "FoldTestV1",
    "ProvenanceDimensionV1",
    "RecordedMetricV1",
    "RerunLagV1",
    "SearchDimensionV1",
    "SortDimensionV1",
    "StudyComparisonEvidenceV1",
    "StudySelectionV1",
    "WalkForwardDimensionV1",
    "WalkForwardReportFactsV1",
    "compare_candidates_v1",
    "read_candidate_comparison_v1",
]
