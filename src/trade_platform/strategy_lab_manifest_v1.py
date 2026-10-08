"""Phase R4.4 -- study result manifest and frozen candidate sets.

``RESEARCH_ONLY``. Two content-addressed records over a *finished* study
(migration ``20261006_0054``):

* :class:`StudyManifestV1` -- the complete trial accounting of one study: the
  planned trial count (the multiple-testing denominator every later statistic
  must use), the queue's final state counts, outcome counts and every result
  hash in ordinal order. Cancelled and inadmissible trials stay in the count;
  nothing is dropped to flatter a later test.
* :class:`CandidateSetV1` -- candidates frozen from one manifest by an explicit
  :class:`CandidateSelectionRuleV1` (a metric key, a direction, ``top_k``).
  The rule is an operator input recorded in the identity; this module chooses
  no metric, no ``k`` and no threshold. Ranking compares the *stored* metric
  text as exact ``Decimal`` values, breaks ties by trial content hash, and
  reports whether the cutoff fell inside a tie (``cutoff_tie``), so a
  selection that depended on the tie-break is visible.

Neither record is authoritative. Results are search-tier evidence and the
numeric doctrine is open (OR-3), so a candidate set records
``authoritative_rerun = PENDING_OWNER_DECISION_OR_3``: under any doctrine the
frozen candidates are exactly what an authoritative recomputation must take,
and nothing here performs or substitutes for it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final

from .persistence import PostgresDatabase
from .strategy_lab_ledger_v1 import (
    NUMERIC_TIER_SEARCH_NON_AUTHORITATIVE,
    PostgresStrategyLabLedgerV1,
    TrialOutcomeV1,
)
from .strategy_lab_study_v1 import REASON_SEARCH_TIER_ONLY, StudySpecV1, identity_hash_v1

MANIFEST_SCHEMA_VERSION_V1: Final = "strategy-lab-study-manifest-v1"
CANDIDATE_SET_SCHEMA_VERSION_V1: Final = "strategy-lab-candidate-set-v1"
AUTHORITATIVE_RERUN_PENDING_OR_3: Final = "PENDING_OWNER_DECISION_OR_3"
AUTHORITATIVE_RERUN_REQUIRED_OR_3: Final = "REQUIRED_DECIMAL_RERUN_OR_3"
TIE_BREAK_V1: Final = "TRIAL_CONTENT_HASH_ASCENDING"

_METRIC: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class StrategyLabManifestError(ValueError):
    """Raised when a manifest or candidate set cannot be built honestly."""


class RankDirectionV1(StrEnum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"


@dataclass(frozen=True, slots=True)
class StudyManifestV1:
    study_id: str
    identity: Mapping[str, Any]
    manifest_hash: str

    @property
    def planned_trial_count(self) -> int:
        return int(self.identity["planned_trial_count"])

    @property
    def results(self) -> list[dict[str, Any]]:
        return list(self.identity["results"])


@dataclass(frozen=True, slots=True)
class CandidateSelectionRuleV1:
    metric: str
    direction: RankDirectionV1
    top_k: int

    def __post_init__(self) -> None:
        if not isinstance(self.metric, str) or not _METRIC.match(self.metric):
            raise StrategyLabManifestError("selection_metric_must_be_a_metric_key")
        if not isinstance(self.direction, RankDirectionV1):
            raise StrategyLabManifestError("selection_direction_unknown")
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int) or self.top_k <= 0:
            raise StrategyLabManifestError("selection_top_k_must_be_a_positive_int")

    def payload(self) -> dict[str, Any]:
        return {"metric": self.metric, "direction": self.direction.value, "top_k": self.top_k,
                "tie_break": TIE_BREAK_V1}


@dataclass(frozen=True, slots=True)
class CandidateSetV1:
    study_id: str
    manifest_hash: str
    identity: Mapping[str, Any]
    candidate_set_hash: str

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return list(self.identity["candidates"])


def build_study_manifest_v1(ledger: PostgresStrategyLabLedgerV1, study: StudySpecV1) -> StudyManifestV1:
    """Manifest of a finished study; refuses an unfinished one or a declaration the ledger does not hold."""
    stored_hash, planned = ledger.stored_study(study.study_id)
    if stored_hash != study.content_hash or planned != study.planned_trial_count:
        raise StrategyLabManifestError("study_declaration_differs_from_the_ledger")
    progress = ledger.progress(study.study_id)
    if not progress.finished:
        raise StrategyLabManifestError("study_not_finished")
    by_trial = {trial.trial_id: trial for trial in study.trials()}
    results = ledger.results(study.study_id)
    rows: list[dict[str, Any]] = []
    outcomes = dict.fromkeys((o.value for o in TrialOutcomeV1), 0)
    for result in results:
        trial = by_trial.get(result.trial_id)
        if trial is None:
            raise StrategyLabManifestError("result_for_an_unplanned_trial")
        outcomes[result.outcome.value] += 1
        rows.append({
            "ordinal": trial.ordinal,
            "trial_id": str(trial.trial_id),
            "trial_content_hash": trial.content_hash,
            "result_content_hash": result.result_content_hash,
            "outcome": result.outcome.value,
            "metrics": dict(result.metrics),
        })
    rows.sort(key=lambda row: int(row["ordinal"]))
    identity = {
        "schema_version": MANIFEST_SCHEMA_VERSION_V1,
        "study_id": str(study.study_id),
        "study_content_hash": study.content_hash,
        "planned_trial_count": study.planned_trial_count,
        "final_states": dict(progress.states),
        "outcomes": outcomes,
        "results": rows,
        "numeric_tier": NUMERIC_TIER_SEARCH_NON_AUTHORITATIVE,
        "authority_reasons": list(study.authority().reasons),
    }
    return StudyManifestV1(str(study.study_id), identity, identity_hash_v1(identity))


def _metric_value(raw: object) -> Decimal | None:
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _rerun_marker(manifest: StudyManifestV1) -> str:
    """OR-3-bound studies (R4.6) require the Decimal rerun; earlier ones stay pending."""
    if REASON_SEARCH_TIER_ONLY in manifest.identity.get("authority_reasons", []):
        return AUTHORITATIVE_RERUN_REQUIRED_OR_3
    return AUTHORITATIVE_RERUN_PENDING_OR_3


def freeze_candidates_v1(manifest: StudyManifestV1, rule: CandidateSelectionRuleV1) -> CandidateSetV1:
    """Freeze the rule's top ``k`` eligible results; refuses when fewer than ``k`` are eligible."""
    eligible: list[tuple[Decimal, dict[str, Any]]] = []
    ineligible = 0
    for row in manifest.results:
        value = _metric_value(row["metrics"].get(rule.metric)) if row["outcome"] == TrialOutcomeV1.EVALUATED.value else None
        if value is None:
            ineligible += 1
        else:
            eligible.append((value, row))
    if len(eligible) < rule.top_k:
        raise StrategyLabManifestError("fewer_eligible_results_than_top_k")
    sign = -1 if rule.direction is RankDirectionV1.HIGHER_IS_BETTER else 1
    eligible.sort(key=lambda item: (sign * item[0], item[1]["trial_content_hash"]))
    chosen = eligible[: rule.top_k]
    cutoff_tie = len(eligible) > rule.top_k and eligible[rule.top_k - 1][0] == eligible[rule.top_k][0]
    candidates = [
        {
            "rank": rank,
            "trial_id": row["trial_id"],
            "trial_content_hash": row["trial_content_hash"],
            "result_content_hash": row["result_content_hash"],
            "metric_value": format(value.normalize(), "f"),
        }
        for rank, (value, row) in enumerate(chosen, start=1)
    ]
    identity = {
        "schema_version": CANDIDATE_SET_SCHEMA_VERSION_V1,
        "study_id": manifest.study_id,
        "manifest_hash": manifest.manifest_hash,
        "rule": rule.payload(),
        "candidates": candidates,
        "eligible_count": len(eligible),
        "ineligible_count": ineligible,
        "cutoff_tie": cutoff_tie,
        "multiple_testing_trial_count": manifest.planned_trial_count,
        "numeric_tier": NUMERIC_TIER_SEARCH_NON_AUTHORITATIVE,
        "authoritative_rerun": _rerun_marker(manifest),
    }
    return CandidateSetV1(manifest.study_id, manifest.manifest_hash, identity, identity_hash_v1(identity))


class PostgresStrategyLabManifestStoreV1:
    """Append-only persistence of manifests and candidate sets (idempotent by hash)."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            yield cursor

    def record_manifest(self, manifest: StudyManifestV1) -> bool:
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_study_manifests (manifest_hash, study_id, planned_trial_count, "
                "result_count, identity, recorded_at) VALUES (%s,%s,%s,%s,%s::jsonb,%s) "
                "ON CONFLICT (manifest_hash) DO NOTHING RETURNING manifest_hash",
                (manifest.manifest_hash, manifest.study_id, manifest.planned_trial_count, len(manifest.results),
                 json.dumps(dict(manifest.identity), sort_keys=True), datetime.now(UTC)),
            )
            return cursor.fetchone() is not None

    def record_candidate_set(self, candidates: CandidateSetV1) -> bool:
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_candidate_sets (candidate_set_hash, manifest_hash, study_id, "
                "candidate_count, numeric_tier, authoritative_rerun, identity, recorded_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (candidate_set_hash) DO NOTHING "
                "RETURNING candidate_set_hash",
                (candidates.candidate_set_hash, candidates.manifest_hash, candidates.study_id,
                 len(candidates.candidates), NUMERIC_TIER_SEARCH_NON_AUTHORITATIVE,
                 candidates.identity["authoritative_rerun"], json.dumps(dict(candidates.identity), sort_keys=True),
                 datetime.now(UTC)),
            )
            return cursor.fetchone() is not None

    def candidate_sets(self, study_id: str) -> tuple[CandidateSetV1, ...]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT candidate_set_hash, manifest_hash, identity FROM strategy_lab_candidate_sets "
                "WHERE study_id=%s ORDER BY recorded_at, candidate_set_hash",
                (study_id,),
            )
            rows = cursor.fetchall()
        out = []
        for set_hash, manifest_hash, identity in rows:
            payload = json.loads(identity) if isinstance(identity, (str, bytes)) else identity
            if identity_hash_v1(payload) != str(set_hash).strip():
                raise StrategyLabManifestError("stored_candidate_set_hash_mismatch")
            out.append(CandidateSetV1(study_id, str(manifest_hash).strip(), payload, str(set_hash).strip()))
        return tuple(out)


__all__ = [
    "AUTHORITATIVE_RERUN_PENDING_OR_3",
    "CANDIDATE_SET_SCHEMA_VERSION_V1",
    "MANIFEST_SCHEMA_VERSION_V1",
    "TIE_BREAK_V1",
    "CandidateSelectionRuleV1",
    "CandidateSetV1",
    "PostgresStrategyLabManifestStoreV1",
    "RankDirectionV1",
    "StrategyLabManifestError",
    "StudyManifestV1",
    "build_study_manifest_v1",
    "freeze_candidates_v1",
]
