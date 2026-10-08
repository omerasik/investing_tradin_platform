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
"""

from __future__ import annotations

import json
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


class StrategyLabStudyPage(BaseModel):
    state: Literal["AVAILABLE", "UNAVAILABLE"]
    items: list[StrategyLabStudySummaryView]
    page: StrategyLabPageInfo


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


def _json(raw: object) -> Any:
    return json.loads(raw) if isinstance(raw, (str, bytes)) else raw


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


def _summary(row: tuple[Any, ...]) -> tuple[StrategyLabStudySummaryView, dict[str, Any]]:
    identity = _json(row[3])
    view = StrategyLabStudySummaryView(
        study_id=row[0], content_hash=str(row[1]).strip(), strategy_family=str(row[2]),
        strategy_version=str(identity["strategy"]["version"]), label=str(row[4]),
        planned_trial_count=int(row[5]), evaluation_upper_bound_exclusive=row[6], registered_at=row[7],
        queue_states={str(k): int(v) for k, v in sorted(_json(row[8]).items())}, result_count=int(row[9]),
        authority=_authority(identity),
    )
    return view, identity


def read_strategy_lab_studies_v1(cursor: _Cursor, *, limit: int, offset: int) -> StrategyLabStudyPage:
    cursor.execute(_SUMMARY_SQL + " ORDER BY s.registered_at DESC, s.study_id LIMIT %s OFFSET %s",
                   (limit + 1, offset))
    rows = cursor.fetchall()
    selected = rows[:limit]
    items = [_summary(row)[0] for row in selected]
    return StrategyLabStudyPage(
        state="AVAILABLE" if items else "UNAVAILABLE", items=items,
        page=StrategyLabPageInfo(limit=limit, offset=offset, returned=len(items), has_more=len(rows) > limit),
    )


def read_strategy_lab_study_v1(cursor: _Cursor, study_id: UUID) -> StrategyLabStudyDetailView:
    cursor.execute(_SUMMARY_SQL + " WHERE s.study_id=%s", (study_id,))
    row = cursor.fetchone()
    if row is None:
        raise StrategyLabObjectNotFound("strategy_lab_study_not_found")
    summary, identity = _summary(row)
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
    cursor.execute(
        "SELECT candidate_set_hash, manifest_hash, identity, recorded_at FROM strategy_lab_candidate_sets "
        "WHERE study_id=%s ORDER BY recorded_at, candidate_set_hash",
        (study_id,),
    )
    candidate_sets = []
    for set_hash, manifest_hash, raw, recorded_at in cursor.fetchall():
        payload = _json(raw)
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
    return StrategyLabStudyDetailView(
        summary=summary, strategy=dict(identity["strategy"]), parameter_space=list(identity["parameter_space"]),
        datasets=list(identity["datasets"]), search=dict(identity["search"]),
        numeric_policy_slot=str(identity["numeric_policy_slot"]), cost_policy_slot=str(identity["cost_policy_slot"]),
        manifests=manifests, candidate_sets=candidate_sets,
    )


__all__ = [
    "StrategyLabAuthorityView",
    "StrategyLabCandidateSetView",
    "StrategyLabCandidateView",
    "StrategyLabManifestView",
    "StrategyLabObjectNotFound",
    "StrategyLabPageInfo",
    "StrategyLabStudyDetailView",
    "StrategyLabStudyPage",
    "StrategyLabStudySummaryView",
    "read_strategy_lab_studies_v1",
    "read_strategy_lab_study_v1",
]
