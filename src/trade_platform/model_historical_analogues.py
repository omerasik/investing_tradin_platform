"""Point-in-time historical-analogue explanation evidence.

The evaluator compares supplied normalized feature snapshots. It does not load,
train, invoke, or serve a model and cannot create a prediction or trading action.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg import sql

from .model_evaluation import (
    BinaryModelEvaluationReport,
    ModelEvaluationOutcome,
    PostgresModelEvaluationStore,
    validate_binary_model_evaluation_report,
)
from .persistence import PostgresDatabase


class HistoricalAnalogueError(ValueError):
    pass


class HistoricalAnalogueOutcome(StrEnum):
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    DIVERGENCE_REVIEW_REQUIRED = "DIVERGENCE_REVIEW_REQUIRED"
    BLOCKED_INSUFFICIENT_EVIDENCE = "BLOCKED_INSUFFICIENT_EVIDENCE"
    BLOCKED_POLICY_DISABLED = "BLOCKED_POLICY_DISABLED"


_NONE = "NONE"


@dataclass(frozen=True, slots=True)
class HistoricalAnaloguePolicy:
    policy_id: UUID
    version: str
    minimum_similarity: Decimal
    minimum_analogues: int
    maximum_analogues: int
    minimum_distinct_regimes: int
    minimum_distinct_source_families: int
    maximum_probability_outcome_gap: Decimal
    approved_by: str
    approved_at: datetime
    enabled: bool
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        version: str,
        minimum_similarity: Decimal,
        minimum_analogues: int,
        maximum_analogues: int,
        minimum_distinct_regimes: int,
        minimum_distinct_source_families: int,
        maximum_probability_outcome_gap: Decimal,
        approved_by: str,
        approved_at: datetime,
        enabled: bool = True,
    ) -> HistoricalAnaloguePolicy:
        payload = _policy_payload(
            version,
            minimum_similarity,
            minimum_analogues,
            maximum_analogues,
            minimum_distinct_regimes,
            minimum_distinct_source_families,
            maximum_probability_outcome_gap,
            approved_by,
            approved_at,
            enabled,
        )
        content_hash = _hash(payload)
        return cls(
            uuid5(NAMESPACE_URL, f"historical-analogue-policy:{content_hash}"),
            version,
            minimum_similarity,
            minimum_analogues,
            maximum_analogues,
            minimum_distinct_regimes,
            minimum_distinct_source_families,
            maximum_probability_outcome_gap,
            approved_by,
            approved_at,
            enabled,
            content_hash,
        )

    def __post_init__(self) -> None:
        if (
            not self.version.strip()
            or not self.approved_by.strip()
            or not _aware(self.approved_at)
            or not self.minimum_similarity.is_finite()
            or not self.maximum_probability_outcome_gap.is_finite()
            or self.minimum_similarity <= 0
            or self.minimum_similarity > 1
            or self.maximum_probability_outcome_gap <= 0
            or self.maximum_probability_outcome_gap > 1
            or self.minimum_similarity != _quantize(self.minimum_similarity)
            or self.maximum_probability_outcome_gap
            != _quantize(self.maximum_probability_outcome_gap)
            or self.minimum_analogues < 1
            or self.maximum_analogues < self.minimum_analogues
            or self.minimum_distinct_regimes < 1
            or self.minimum_distinct_regimes > self.minimum_analogues
            or self.minimum_distinct_source_families < 1
            or self.minimum_distinct_source_families > self.minimum_analogues
            or self.content_hash != _policy_hash(self)
        ):
            raise HistoricalAnalogueError("invalid_historical_analogue_policy")


@dataclass(frozen=True, slots=True)
class ModelExplanationTarget:
    target_id: UUID
    model_id: UUID
    dataset_version: str
    feature_version: str
    instrument_id: str
    observed_at: datetime
    available_at: datetime
    predicted_probability: Decimal
    confidence: Decimal
    regime: str
    normalized_features: tuple[tuple[str, Decimal], ...]
    source_reference: str
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        target_id: UUID,
        model_id: UUID,
        dataset_version: str,
        feature_version: str,
        instrument_id: str,
        observed_at: datetime,
        available_at: datetime,
        predicted_probability: Decimal,
        confidence: Decimal,
        regime: str,
        normalized_features: dict[str, Decimal],
        source_reference: str,
    ) -> ModelExplanationTarget:
        ordered = tuple(sorted(normalized_features.items()))
        draft = cls(
            target_id,
            model_id,
            dataset_version,
            feature_version,
            instrument_id,
            observed_at,
            available_at,
            predicted_probability,
            confidence,
            regime,
            ordered,
            source_reference,
            "",
        )
        return replace(draft, content_hash=_target_hash(draft))


@dataclass(frozen=True, slots=True)
class HistoricalAnalogueCandidate:
    analogue_id: UUID
    model_id: UUID
    dataset_version: str
    feature_version: str
    instrument_id: str
    regime: str
    observed_at: datetime
    available_at: datetime
    outcome_available_at: datetime
    normalized_features: tuple[tuple[str, Decimal], ...]
    actual_outcome: int
    realized_return: Decimal
    source_family: str
    source_reference: str
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        analogue_id: UUID,
        model_id: UUID,
        dataset_version: str,
        feature_version: str,
        instrument_id: str,
        regime: str,
        observed_at: datetime,
        available_at: datetime,
        outcome_available_at: datetime,
        normalized_features: dict[str, Decimal],
        actual_outcome: int,
        realized_return: Decimal,
        source_family: str,
        source_reference: str,
    ) -> HistoricalAnalogueCandidate:
        ordered = tuple(sorted(normalized_features.items()))
        draft = cls(
            analogue_id,
            model_id,
            dataset_version,
            feature_version,
            instrument_id,
            regime,
            observed_at,
            available_at,
            outcome_available_at,
            ordered,
            actual_outcome,
            realized_return,
            source_family,
            source_reference,
            "",
        )
        return replace(draft, content_hash=_candidate_hash(draft))


@dataclass(frozen=True, slots=True)
class HistoricalAnalogueMatch:
    analogue_id: UUID
    analogue_content_hash: str
    weighted_distance: Decimal
    similarity: Decimal
    selected: bool
    selection_rank: int | None


@dataclass(frozen=True, slots=True)
class HistoricalAnalogueReport:
    report_id: UUID
    policy_id: UUID
    policy_content_hash: str
    evaluation_report_id: UUID
    evaluation_report_content_hash: str
    target_id: UUID
    target_content_hash: str
    model_id: UUID
    evaluated_at: datetime
    screened_count: int
    selected_count: int
    distinct_regime_count: int
    distinct_source_family_count: int
    mean_similarity: Decimal | None
    weighted_outcome_frequency: Decimal | None
    weighted_realized_return: Decimal | None
    probability_outcome_gap: Decimal | None
    analogue_evidence_hash: str
    outcome: HistoricalAnalogueOutcome
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    model_invocation_authority: str
    prediction_authority: str
    action_authority: str
    content_hash: str


def evaluate_historical_analogues(
    policy: HistoricalAnaloguePolicy,
    evaluation: BinaryModelEvaluationReport,
    target: ModelExplanationTarget,
    candidates: tuple[HistoricalAnalogueCandidate, ...],
    *,
    evaluated_at: datetime,
) -> tuple[HistoricalAnalogueReport, tuple[HistoricalAnalogueMatch, ...]]:
    validate_binary_model_evaluation_report(evaluation)
    _validate_policy(policy)
    _validate_target(target)
    ordered_candidates = tuple(sorted(candidates, key=lambda item: str(item.analogue_id)))
    if (
        evaluation.outcome is not ModelEvaluationOutcome.REVIEW_ELIGIBLE
        or target.model_id != evaluation.model_id
        or target.dataset_version != evaluation.dataset_version
        or target.feature_version != evaluation.feature_version
        or set(dict(target.normalized_features)) != set(evaluation.feature_importance)
        or target.available_at < evaluation.evaluated_at
        or policy.approved_at > target.available_at
        or not _aware(evaluated_at)
        or evaluated_at < target.available_at
        or len({item.analogue_id for item in ordered_candidates}) != len(ordered_candidates)
        or len({item.content_hash for item in ordered_candidates}) != len(ordered_candidates)
    ):
        raise HistoricalAnalogueError("invalid_historical_analogue_context")
    for candidate in ordered_candidates:
        _validate_candidate(candidate)
        if (
            candidate.model_id != target.model_id
            or candidate.dataset_version != target.dataset_version
            or candidate.feature_version != target.feature_version
            or set(dict(candidate.normalized_features)) != set(dict(target.normalized_features))
            or candidate.observed_at >= target.observed_at
            or candidate.available_at > target.available_at
            or candidate.outcome_available_at > target.available_at
        ):
            raise HistoricalAnalogueError("historical_analogue_not_point_in_time_or_version_bound")

    target_features = dict(target.normalized_features)
    total_importance = sum(evaluation.feature_importance.values(), Decimal("0"))
    if total_importance <= 0:
        raise HistoricalAnalogueError("historical_analogue_requires_positive_feature_importance")
    weights = {
        name: value / total_importance for name, value in evaluation.feature_importance.items()
    }
    scored: list[tuple[HistoricalAnalogueCandidate, Decimal, Decimal]] = []
    for candidate in ordered_candidates:
        candidate_features = dict(candidate.normalized_features)
        distance = _quantize(
            sum(
                (
                    weights[name] * abs(target_features[name] - candidate_features[name])
                    for name in sorted(weights)
                ),
                Decimal("0"),
            )
        )
        similarity = _quantize(Decimal("1") - distance)
        scored.append((candidate, distance, similarity))
    eligible = sorted(
        (item for item in scored if item[2] >= policy.minimum_similarity),
        key=lambda item: (-item[2], item[0].observed_at, str(item[0].analogue_id)),
    )[: policy.maximum_analogues]
    selected_ids = {item[0].analogue_id: rank for rank, item in enumerate(eligible, 1)}
    matches = tuple(
        HistoricalAnalogueMatch(
            candidate.analogue_id,
            candidate.content_hash,
            distance,
            similarity,
            candidate.analogue_id in selected_ids,
            selected_ids.get(candidate.analogue_id),
        )
        for candidate, distance, similarity in sorted(
            scored,
            key=lambda item: str(item[0].analogue_id),
        )
    )
    selected = tuple(item[0] for item in eligible)
    distinct_regimes = len({item.regime for item in selected})
    distinct_sources = len({item.source_family for item in selected})
    weight_sum = sum((item[2] for item in eligible), Decimal("0"))
    mean_similarity: Decimal | None = None
    weighted_outcome: Decimal | None = None
    weighted_return: Decimal | None = None
    probability_gap: Decimal | None = None
    if eligible:
        mean_similarity = _quantize(weight_sum / Decimal(len(eligible)))
        weighted_outcome = _quantize(
            sum(
                similarity * Decimal(candidate.actual_outcome)
                for candidate, _, similarity in eligible
            )
            / weight_sum
        )
        weighted_return = _quantize(
            sum(similarity * candidate.realized_return for candidate, _, similarity in eligible)
            / weight_sum
        )
        probability_gap = _quantize(abs(target.predicted_probability - weighted_outcome))
    reasons: list[str] = []
    if not policy.enabled:
        reasons.append("historical_analogue_policy_disabled")
    if len(selected) < policy.minimum_analogues:
        reasons.append("minimum_historical_analogues_not_met")
    if distinct_regimes < policy.minimum_distinct_regimes:
        reasons.append("minimum_historical_regime_diversity_not_met")
    if distinct_sources < policy.minimum_distinct_source_families:
        reasons.append("minimum_historical_source_diversity_not_met")
    if probability_gap is not None and probability_gap > policy.maximum_probability_outcome_gap:
        reasons.append("historical_outcome_diverges_from_prediction")
    if not policy.enabled:
        outcome = HistoricalAnalogueOutcome.BLOCKED_POLICY_DISABLED
    elif any(reason.startswith("minimum_") for reason in reasons):
        outcome = HistoricalAnalogueOutcome.BLOCKED_INSUFFICIENT_EVIDENCE
    elif "historical_outcome_diverges_from_prediction" in reasons:
        outcome = HistoricalAnalogueOutcome.DIVERGENCE_REVIEW_REQUIRED
    else:
        outcome = HistoricalAnalogueOutcome.READY_FOR_REVIEW
    evidence_hash = _match_evidence_hash(matches)
    report_id = uuid5(
        NAMESPACE_URL,
        f"historical-analogue:{policy.content_hash}:{evaluation.content_hash}:"
        f"{target.content_hash}:{evidence_hash}:{evaluated_at.isoformat()}",
    )
    draft = HistoricalAnalogueReport(
        report_id,
        policy.policy_id,
        policy.content_hash,
        evaluation.report_id,
        evaluation.content_hash,
        target.target_id,
        target.content_hash,
        target.model_id,
        evaluated_at,
        len(matches),
        len(selected),
        distinct_regimes,
        distinct_sources,
        mean_similarity,
        weighted_outcome,
        weighted_return,
        probability_gap,
        evidence_hash,
        outcome,
        tuple(reasons) or ("historical_analogue_thresholds_met",),
        (
            "normalized_fixture_similarity_is_not_semantic_or_causal_explanation",
            "historical_outcomes_do_not_establish_future_model_quality_or_economic_value",
            "analogue_evidence_has_no_model_prediction_signal_order_risk_or_approval_authority",
        ),
        _NONE,
        _NONE,
        _NONE,
        "",
    )
    return replace(draft, content_hash=_report_hash(draft)), matches


class PostgresHistoricalAnalogueStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append_policy(self, policy: HistoricalAnaloguePolicy) -> None:
        _validate_policy(policy)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO historical_analogue_policy_versions VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (policy_id) "
                "DO NOTHING RETURNING content_hash",
                (
                    policy.policy_id,
                    policy.version,
                    policy.minimum_similarity,
                    policy.minimum_analogues,
                    policy.maximum_analogues,
                    policy.minimum_distinct_regimes,
                    policy.minimum_distinct_source_families,
                    policy.maximum_probability_outcome_gap,
                    policy.approved_by,
                    policy.approved_at,
                    policy.enabled,
                    policy.content_hash,
                ),
            )
            self._verify_insert(
                cursor,
                "historical_analogue_policy_versions",
                "policy_id",
                policy.policy_id,
                policy.content_hash,
            )

    def append_target(self, target: ModelExplanationTarget) -> None:
        _validate_target(target)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO model_explanation_targets VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT "
                "(target_id) DO NOTHING RETURNING content_hash",
                (
                    target.target_id,
                    target.model_id,
                    target.dataset_version,
                    target.feature_version,
                    target.instrument_id,
                    target.observed_at,
                    target.available_at,
                    target.predicted_probability,
                    target.confidence,
                    target.regime,
                    json.dumps(_feature_json(target.normalized_features)),
                    target.source_reference,
                    target.content_hash,
                ),
            )
            self._verify_insert(
                cursor,
                "model_explanation_targets",
                "target_id",
                target.target_id,
                target.content_hash,
            )

    def append_candidate(self, candidate: HistoricalAnalogueCandidate) -> None:
        _validate_candidate(candidate)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO historical_analogue_candidates VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s) ON CONFLICT "
                "(analogue_id) DO NOTHING RETURNING content_hash",
                (
                    candidate.analogue_id,
                    candidate.model_id,
                    candidate.dataset_version,
                    candidate.feature_version,
                    candidate.instrument_id,
                    candidate.regime,
                    candidate.observed_at,
                    candidate.available_at,
                    candidate.outcome_available_at,
                    json.dumps(_feature_json(candidate.normalized_features)),
                    candidate.actual_outcome,
                    candidate.realized_return,
                    candidate.source_family,
                    candidate.source_reference,
                    candidate.content_hash,
                ),
            )
            self._verify_insert(
                cursor,
                "historical_analogue_candidates",
                "analogue_id",
                candidate.analogue_id,
                candidate.content_hash,
            )

    def append_report(
        self,
        report: HistoricalAnalogueReport,
        candidates: tuple[HistoricalAnalogueCandidate, ...],
    ) -> None:
        policy = self.policy(report.policy_id)
        target = self.target(report.target_id)
        evaluation = PostgresModelEvaluationStore(self._database).report(
            report.evaluation_report_id,
        )
        registered_candidates = tuple(self.candidate(item.analogue_id) for item in candidates)
        expected, matches = evaluate_historical_analogues(
            policy,
            evaluation,
            target,
            registered_candidates,
            evaluated_at=report.evaluated_at,
        )
        if expected != report:
            raise HistoricalAnalogueError("historical_analogue_report_not_reproducible")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            self._verify_reference(
                cursor,
                "historical_analogue_policy_versions",
                "policy_id",
                report.policy_id,
                report.policy_content_hash,
            )
            self._verify_reference(
                cursor,
                "model_evaluation_reports",
                "report_id",
                report.evaluation_report_id,
                report.evaluation_report_content_hash,
            )
            self._verify_reference(
                cursor,
                "model_explanation_targets",
                "target_id",
                report.target_id,
                report.target_content_hash,
            )
            for match in matches:
                self._verify_reference(
                    cursor,
                    "historical_analogue_candidates",
                    "analogue_id",
                    match.analogue_id,
                    match.analogue_content_hash,
                )
            cursor.execute(
                "INSERT INTO historical_analogue_reports VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,"
                "%s::jsonb,%s,%s,%s,%s) ON CONFLICT (report_id) DO NOTHING RETURNING content_hash",
                (
                    report.report_id,
                    report.policy_id,
                    report.policy_content_hash,
                    report.evaluation_report_id,
                    report.evaluation_report_content_hash,
                    report.target_id,
                    report.target_content_hash,
                    report.model_id,
                    report.evaluated_at,
                    report.screened_count,
                    report.selected_count,
                    report.distinct_regime_count,
                    report.distinct_source_family_count,
                    report.mean_similarity,
                    report.weighted_outcome_frequency,
                    report.weighted_realized_return,
                    report.probability_outcome_gap,
                    report.analogue_evidence_hash,
                    report.outcome.value,
                    json.dumps(report.reasons),
                    json.dumps(report.limitations),
                    report.model_invocation_authority,
                    report.prediction_authority,
                    report.action_authority,
                    report.content_hash,
                ),
            )
            self._verify_insert(
                cursor,
                "historical_analogue_reports",
                "report_id",
                report.report_id,
                report.content_hash,
            )
            for match in matches:
                cursor.execute(
                    "INSERT INTO historical_analogue_report_members VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (
                        report.report_id,
                        match.analogue_id,
                        match.analogue_content_hash,
                        match.weighted_distance,
                        match.similarity,
                        match.selected,
                        match.selection_rank,
                    ),
                )

    def policy(self, policy_id: UUID) -> HistoricalAnaloguePolicy:
        row = self._read_one("historical_analogue_policy_versions", "policy_id", policy_id)
        policy = HistoricalAnaloguePolicy(
            UUID(str(row[0])),
            str(row[1]),
            Decimal(str(row[2])),
            int(str(row[3])),
            int(str(row[4])),
            int(str(row[5])),
            int(str(row[6])),
            Decimal(str(row[7])),
            str(row[8]),
            cast(datetime, row[9]),
            bool(row[10]),
            str(row[11]),
        )
        _validate_policy(policy)
        return policy

    def target(self, target_id: UUID) -> ModelExplanationTarget:
        row = self._read_one("model_explanation_targets", "target_id", target_id)
        target = ModelExplanationTarget(
            UUID(str(row[0])),
            UUID(str(row[1])),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            cast(datetime, row[5]),
            cast(datetime, row[6]),
            Decimal(str(row[7])),
            Decimal(str(row[8])),
            str(row[9]),
            _features_from_json(row[10]),
            str(row[11]),
            str(row[12]),
        )
        _validate_target(target)
        return target

    def candidate(self, analogue_id: UUID) -> HistoricalAnalogueCandidate:
        row = self._read_one("historical_analogue_candidates", "analogue_id", analogue_id)
        candidate = HistoricalAnalogueCandidate(
            UUID(str(row[0])),
            UUID(str(row[1])),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]),
            cast(datetime, row[6]),
            cast(datetime, row[7]),
            cast(datetime, row[8]),
            _features_from_json(row[9]),
            int(str(row[10])),
            Decimal(str(row[11])),
            str(row[12]),
            str(row[13]),
            str(row[14]),
        )
        _validate_candidate(candidate)
        return candidate

    def report(
        self,
        report_id: UUID,
    ) -> tuple[HistoricalAnalogueReport, tuple[HistoricalAnalogueMatch, ...]]:
        row = self._read_one("historical_analogue_reports", "report_id", report_id)
        report = HistoricalAnalogueReport(
            UUID(str(row[0])),
            UUID(str(row[1])),
            str(row[2]),
            UUID(str(row[3])),
            str(row[4]),
            UUID(str(row[5])),
            str(row[6]),
            UUID(str(row[7])),
            cast(datetime, row[8]),
            int(str(row[9])),
            int(str(row[10])),
            int(str(row[11])),
            int(str(row[12])),
            _optional_decimal(row[13]),
            _optional_decimal(row[14]),
            _optional_decimal(row[15]),
            _optional_decimal(row[16]),
            str(row[17]),
            HistoricalAnalogueOutcome(str(row[18])),
            tuple(cast(list[str], row[19])),
            tuple(cast(list[str], row[20])),
            str(row[21]),
            str(row[22]),
            str(row[23]),
            str(row[24]),
        )
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT analogue_id,analogue_content_hash,weighted_distance,similarity,selected,"
                "selection_rank FROM historical_analogue_report_members WHERE report_id=%s "
                "ORDER BY analogue_id",
                (report_id,),
            )
            matches = tuple(
                HistoricalAnalogueMatch(
                    UUID(str(item[0])),
                    str(item[1]),
                    Decimal(str(item[2])),
                    Decimal(str(item[3])),
                    bool(item[4]),
                    int(str(item[5])) if item[5] is not None else None,
                )
                for item in cursor.fetchall()
            )
        if (
            report.content_hash != _report_hash(report)
            or report.analogue_evidence_hash != _match_evidence_hash(matches)
            or report.screened_count != len(matches)
            or report.selected_count != sum(item.selected for item in matches)
        ):
            raise HistoricalAnalogueError("historical_analogue_report_hash_mismatch")
        return report, matches

    def _read_one(self, table: str, id_column: str, identity: UUID) -> tuple[object, ...]:
        query = sql.SQL("SELECT * FROM {} WHERE {}=%s").format(
            sql.Identifier(table),
            sql.Identifier(id_column),
        )
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(query, (identity,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(str(identity))
        return cast(tuple[object, ...], row)

    @staticmethod
    def _verify_insert(
        cursor: Any, table: str, id_column: str, identity: UUID, content_hash: str
    ) -> None:
        if cursor.fetchone() is not None:
            return
        query = sql.SQL("SELECT content_hash FROM {} WHERE {}=%s").format(
            sql.Identifier(table),
            sql.Identifier(id_column),
        )
        cursor.execute(query, (identity,))
        row = cursor.fetchone()
        if row is None or str(row[0]) != content_hash:
            raise HistoricalAnalogueError(f"conflicting_{table}")

    @staticmethod
    def _verify_reference(
        cursor: Any, table: str, id_column: str, identity: UUID, content_hash: str
    ) -> None:
        query = sql.SQL("SELECT content_hash FROM {} WHERE {}=%s").format(
            sql.Identifier(table),
            sql.Identifier(id_column),
        )
        cursor.execute(query, (identity,))
        row = cursor.fetchone()
        if row is None or str(row[0]) != content_hash:
            raise HistoricalAnalogueError(f"{table}_not_registered_or_mismatched")


def _validate_policy(policy: HistoricalAnaloguePolicy) -> None:
    policy.__post_init__()


def _validate_target(target: ModelExplanationTarget) -> None:
    features = dict(target.normalized_features)
    decimal_values = (target.predicted_probability, target.confidence, *features.values())
    if (
        not target.dataset_version.strip()
        or not target.feature_version.strip()
        or not target.instrument_id.strip()
        or not target.regime.strip()
        or not target.source_reference.strip()
        or not _aware(target.observed_at)
        or not _aware(target.available_at)
        or target.observed_at > target.available_at
        or any(not value.is_finite() for value in decimal_values)
        or target.predicted_probability < 0
        or target.predicted_probability > 1
        or target.confidence < 0
        or target.confidence > 1
        or not features
        or len(features) != len(target.normalized_features)
        or target.normalized_features != tuple(sorted(target.normalized_features))
        or any(not name.strip() or value < 0 or value > 1 for name, value in features.items())
        or any(value != _quantize(value, 18) for value in decimal_values)
        or target.content_hash != _target_hash(target)
    ):
        raise HistoricalAnalogueError("invalid_or_tampered_model_explanation_target")


def _validate_candidate(candidate: HistoricalAnalogueCandidate) -> None:
    features = dict(candidate.normalized_features)
    values = (*features.values(), candidate.realized_return)
    if (
        not candidate.dataset_version.strip()
        or not candidate.feature_version.strip()
        or not candidate.instrument_id.strip()
        or not candidate.regime.strip()
        or not candidate.source_family.strip()
        or not candidate.source_reference.strip()
        or not all(
            _aware(value)
            for value in (
                candidate.observed_at,
                candidate.available_at,
                candidate.outcome_available_at,
            )
        )
        or not candidate.observed_at <= candidate.available_at <= candidate.outcome_available_at
        or candidate.actual_outcome not in (0, 1)
        or not features
        or len(features) != len(candidate.normalized_features)
        or candidate.normalized_features != tuple(sorted(candidate.normalized_features))
        or any(not name.strip() or value < 0 or value > 1 for name, value in features.items())
        or any(not value.is_finite() for value in values)
        or abs(candidate.realized_return) >= Decimal("1000000000000")
        or any(value != _quantize(value, 18) for value in values)
        or candidate.content_hash != _candidate_hash(candidate)
    ):
        raise HistoricalAnalogueError("invalid_or_tampered_historical_analogue_candidate")


def _policy_payload(
    version: str,
    minimum_similarity: Decimal,
    minimum_analogues: int,
    maximum_analogues: int,
    minimum_distinct_regimes: int,
    minimum_distinct_source_families: int,
    maximum_probability_outcome_gap: Decimal,
    approved_by: str,
    approved_at: datetime,
    enabled: bool,
) -> dict[str, object]:
    return {
        "version": version,
        "minimum_similarity": _decimal_text(minimum_similarity),
        "minimum_analogues": minimum_analogues,
        "maximum_analogues": maximum_analogues,
        "minimum_distinct_regimes": minimum_distinct_regimes,
        "minimum_distinct_source_families": minimum_distinct_source_families,
        "maximum_probability_outcome_gap": _decimal_text(maximum_probability_outcome_gap),
        "approved_by": approved_by,
        "approved_at": approved_at.isoformat(),
        "enabled": enabled,
    }


def _policy_hash(policy: HistoricalAnaloguePolicy) -> str:
    return _hash(
        _policy_payload(
            policy.version,
            policy.minimum_similarity,
            policy.minimum_analogues,
            policy.maximum_analogues,
            policy.minimum_distinct_regimes,
            policy.minimum_distinct_source_families,
            policy.maximum_probability_outcome_gap,
            policy.approved_by,
            policy.approved_at,
            policy.enabled,
        )
    )


def _target_hash(target: ModelExplanationTarget) -> str:
    return _hash(
        {
            "target_id": str(target.target_id),
            "model_id": str(target.model_id),
            "dataset_version": target.dataset_version,
            "feature_version": target.feature_version,
            "instrument_id": target.instrument_id,
            "observed_at": target.observed_at.isoformat(),
            "available_at": target.available_at.isoformat(),
            "predicted_probability": _decimal_text(target.predicted_probability),
            "confidence": _decimal_text(target.confidence),
            "regime": target.regime,
            "normalized_features": _feature_json(target.normalized_features),
            "source_reference": target.source_reference,
        }
    )


def _candidate_hash(candidate: HistoricalAnalogueCandidate) -> str:
    return _hash(
        {
            "analogue_id": str(candidate.analogue_id),
            "model_id": str(candidate.model_id),
            "dataset_version": candidate.dataset_version,
            "feature_version": candidate.feature_version,
            "instrument_id": candidate.instrument_id,
            "regime": candidate.regime,
            "observed_at": candidate.observed_at.isoformat(),
            "available_at": candidate.available_at.isoformat(),
            "outcome_available_at": candidate.outcome_available_at.isoformat(),
            "normalized_features": _feature_json(candidate.normalized_features),
            "actual_outcome": candidate.actual_outcome,
            "realized_return": _decimal_text(candidate.realized_return),
            "source_family": candidate.source_family,
            "source_reference": candidate.source_reference,
        }
    )


def _match_evidence_hash(matches: tuple[HistoricalAnalogueMatch, ...]) -> str:
    return _hash(
        [
            {
                "analogue_id": str(item.analogue_id),
                "analogue_content_hash": item.analogue_content_hash,
                "weighted_distance": _decimal_text(item.weighted_distance),
                "similarity": _decimal_text(item.similarity),
                "selected": item.selected,
                "selection_rank": item.selection_rank,
            }
            for item in sorted(matches, key=lambda value: str(value.analogue_id))
        ]
    )


def _report_hash(report: HistoricalAnalogueReport) -> str:
    return _hash(
        {
            "report_id": str(report.report_id),
            "policy_id": str(report.policy_id),
            "policy_content_hash": report.policy_content_hash,
            "evaluation_report_id": str(report.evaluation_report_id),
            "evaluation_report_content_hash": report.evaluation_report_content_hash,
            "target_id": str(report.target_id),
            "target_content_hash": report.target_content_hash,
            "model_id": str(report.model_id),
            "evaluated_at": report.evaluated_at.isoformat(),
            "screened_count": report.screened_count,
            "selected_count": report.selected_count,
            "distinct_regime_count": report.distinct_regime_count,
            "distinct_source_family_count": report.distinct_source_family_count,
            "mean_similarity": _optional_decimal_text(report.mean_similarity),
            "weighted_outcome_frequency": _optional_decimal_text(report.weighted_outcome_frequency),
            "weighted_realized_return": _optional_decimal_text(report.weighted_realized_return),
            "probability_outcome_gap": _optional_decimal_text(report.probability_outcome_gap),
            "analogue_evidence_hash": report.analogue_evidence_hash,
            "outcome": report.outcome.value,
            "reasons": report.reasons,
            "limitations": report.limitations,
            "model_invocation_authority": report.model_invocation_authority,
            "prediction_authority": report.prediction_authority,
            "action_authority": report.action_authority,
        }
    )


def _feature_json(features: tuple[tuple[str, Decimal], ...]) -> dict[str, str]:
    return {name: _decimal_text(value) for name, value in features}


def _features_from_json(value: object) -> tuple[tuple[str, Decimal], ...]:
    raw = cast(dict[str, str], value)
    return tuple(sorted((name, Decimal(str(item))) for name, item in raw.items()))


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _quantize(value: Decimal, places: int = 12) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
