"""Immutable, PostgreSQL-native strategy scorecard evidence.

Raw metric observations are authoritative. Components are transparent navigation
aids and this module deliberately has no strategy-promotion or execution power.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from statistics import median
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .persistence import PostgresDatabase
from .quant_validation import REQUIRED_EVIDENCE


class ScorecardV2Error(ValueError):
    pass


class EvidenceState(StrEnum):
    MEASURED = "MEASURED"
    ASSUMED = "ASSUMED"
    UNAVAILABLE = "UNAVAILABLE"


class ScorecardStatus(StrEnum):
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class MetricFamily(StrEnum):
    PERFORMANCE = "PERFORMANCE"
    ROBUSTNESS = "ROBUSTNESS"
    EXECUTION = "EXECUTION"
    RISK = "RISK"
    DATA_QUALITY = "DATA_QUALITY"
    SIGNAL_DECAY = "SIGNAL_DECAY"


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScorecardV2Error("scorecard_timestamp_must_be_timezone_aware")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class MetricObservation:
    family: MetricFamily
    name: str
    state: EvidenceState
    value: Decimal | None
    unit: str
    dimensions: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.name.strip() or not self.unit.strip():
            raise ScorecardV2Error("scorecard_metric_identity_missing")
        if self.state is EvidenceState.UNAVAILABLE and self.value is not None:
            raise ScorecardV2Error("unavailable_scorecard_metric_has_value")
        if self.state is not EvidenceState.UNAVAILABLE and self.value is None:
            raise ScorecardV2Error("available_scorecard_metric_missing_value")


@dataclass(frozen=True, slots=True)
class ScoreComponentV2:
    name: str
    formula_version: str
    value: Decimal | None
    rationale: str

    def validate(self) -> None:
        if not all(item.strip() for item in (self.name, self.formula_version, self.rationale)):
            raise ScorecardV2Error("invalid_scorecard_component")


@dataclass(frozen=True, slots=True)
class StrategyScorecardV2:
    scorecard_schema_version: str
    strategy_id: UUID
    strategy_version: str
    research_run_id: UUID
    dataset_version: str
    feature_versions: tuple[str, ...]
    cost_model_version: str
    evaluated_at: datetime
    knowledge_cutoff: datetime
    status: ScorecardStatus
    limitations: tuple[str, ...]
    metrics: tuple[MetricObservation, ...]
    components: tuple[ScoreComponentV2, ...]
    dataset_health_status: str
    data_health_assessment_ids: tuple[UUID, ...]
    evidence_manifest: dict[str, str]
    scorecard_id: UUID = field(init=False)

    def __post_init__(self) -> None:
        """Use the evidence hash as the immutable, replay-safe identity."""
        object.__setattr__(
            self,
            "scorecard_id",
            uuid5(NAMESPACE_URL, f"strategy-scorecard-v2:{self.content_hash()}"),
        )

    def content_hash(self) -> str:
        payload = {
            "schema": self.scorecard_schema_version, "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version, "research_run_id": self.research_run_id,
            "dataset_version": self.dataset_version, "feature_versions": self.feature_versions,
            "cost_model_version": self.cost_model_version, "evaluated_at": self.evaluated_at,
            "knowledge_cutoff": self.knowledge_cutoff, "status": self.status.value,
            "limitations": self.limitations, "metrics": self.metrics, "components": self.components,
            "dataset_health_status": self.dataset_health_status,
            "data_health_assessment_ids": self.data_health_assessment_ids,
            "evidence_manifest": self.evidence_manifest,
        }
        return hashlib.sha256(_canonical(payload).encode()).hexdigest()

    def validate(self) -> None:
        if not all(item.strip() for item in (self.scorecard_schema_version, self.strategy_version, self.dataset_version, self.cost_model_version, self.dataset_health_status)):
            raise ScorecardV2Error("scorecard_identity_missing")
        if not self.feature_versions or len(set(self.feature_versions)) != len(self.feature_versions):
            raise ScorecardV2Error("scorecard_feature_versions_invalid")
        _aware(self.evaluated_at); _aware(self.knowledge_cutoff)
        if self.knowledge_cutoff > self.evaluated_at or not self.metrics:
            raise ScorecardV2Error("scorecard_temporal_or_metric_evidence_missing")
        for metric in self.metrics: metric.validate()
        for component in self.components: component.validate()
        if self.dataset_health_status == "BLOCKED" and self.status is not ScorecardStatus.BLOCKED:
            raise ScorecardV2Error("blocked_data_health_requires_blocked_scorecard")
        if not self.limitations:
            raise ScorecardV2Error("scorecard_limitations_required")
        if not self.evidence_manifest or any(
            not name.strip() or len(content_hash) != 64
            for name, content_hash in self.evidence_manifest.items()
        ):
            raise ScorecardV2Error("scorecard_evidence_manifest_invalid")


def performance_metrics(returns: tuple[Decimal, ...], *, periods_per_year: int = 252) -> tuple[MetricObservation, ...]:
    """Metrics stay unavailable where annualization/statistics are not meaningful."""
    if periods_per_year < 1 or any(value <= Decimal("-1") for value in returns):
        raise ScorecardV2Error("invalid_scorecard_returns")
    if not returns:
        unavailable = (
            "total_return", "cagr", "annualized_return", "annualized_volatility", "sharpe",
            "sortino", "calmar", "max_drawdown", "max_drawdown_duration",
            "downside_deviation", "hit_rate", "win_loss_ratio", "payoff_ratio",
            "profit_factor", "average_trade", "median_trade",
        )
        return tuple(
            MetricObservation(MetricFamily.PERFORMANCE, name, EvidenceState.UNAVAILABLE, None, "fraction")
            for name in unavailable
        ) + (
            MetricObservation(MetricFamily.PERFORMANCE, "number_of_trades", EvidenceState.MEASURED, Decimal("0"), "count"),
        )
    equity = Decimal("1"); peak = equity; drawdowns: list[Decimal] = []; durations: list[int] = []; underwater = 0
    for value in returns:
        equity *= Decimal("1") + value; peak = max(peak, equity); drawdown = equity / peak - Decimal("1")
        drawdowns.append(drawdown); underwater = underwater + 1 if drawdown < 0 else 0; durations.append(underwater)
    average = sum(returns, Decimal("0")) / Decimal(len(returns)); downside = tuple(min(value, Decimal("0")) for value in returns)
    downside_deviation = (sum((value * value for value in downside), Decimal("0")) / Decimal(len(downside))).sqrt()
    variance = sum(((value - average) ** 2 for value in returns), Decimal("0")) / Decimal(len(returns))
    volatility = variance.sqrt() * Decimal(periods_per_year).sqrt()
    losses = tuple(value for value in returns if value < 0); gains = tuple(value for value in returns if value > 0)
    annualized = None if len(returns) < periods_per_year else equity ** (Decimal(periods_per_year) / Decimal(len(returns))) - Decimal("1")
    sharpe = None if volatility == 0 else average / variance.sqrt() * Decimal(periods_per_year).sqrt()
    sortino = None if downside_deviation == 0 else average / downside_deviation * Decimal(periods_per_year).sqrt()
    max_drawdown = min(drawdowns, default=Decimal("0")); calmar = None if annualized is None or max_drawdown == 0 else annualized / abs(max_drawdown)
    def measured(name: str, value: Decimal | None, unit: str) -> MetricObservation:
        return MetricObservation(MetricFamily.PERFORMANCE, name, EvidenceState.MEASURED if value is not None else EvidenceState.UNAVAILABLE, value, unit)
    return (
        measured("total_return", equity - Decimal("1"), "fraction"), measured("cagr", annualized, "fraction"),
        measured("annualized_return", annualized, "fraction"), measured("annualized_volatility", volatility, "fraction"),
        measured("sharpe", sharpe, "ratio"), measured("sortino", sortino, "ratio"), measured("calmar", calmar, "ratio"),
        measured("max_drawdown", max_drawdown, "fraction"), measured("max_drawdown_duration", Decimal(max(durations, default=0)), "periods"),
        measured("downside_deviation", downside_deviation, "fraction"), measured("hit_rate", Decimal(len(gains)) / Decimal(len(returns)), "fraction"),
        measured("win_loss_ratio", None if not losses else Decimal(len(gains)) / Decimal(len(losses)), "ratio"),
        measured("payoff_ratio", None if not losses or not gains else (sum(gains, Decimal("0")) / Decimal(len(gains))) / abs(sum(losses, Decimal("0")) / Decimal(len(losses))), "ratio"),
        measured("profit_factor", None if not losses else sum(gains, Decimal("0")) / abs(sum(losses, Decimal("0"))), "ratio"),
        measured("average_trade", average, "fraction"), measured("median_trade", Decimal(str(median(returns))), "fraction"),
        measured("number_of_trades", Decimal(len(returns)), "count"),
    )


def trade_activity_metrics(
    *, turnover: Decimal | None, exposure: Decimal | None, average_holding_period: Decimal | None
) -> tuple[MetricObservation, ...]:
    """Retain activity evidence without treating an absent estimate as zero."""
    values = (
        ("turnover", turnover, "fraction"),
        ("exposure", exposure, "fraction"),
        ("average_holding_period", average_holding_period, "periods"),
    )
    if any(value is not None and value < 0 for _, value, _ in values):
        raise ScorecardV2Error("invalid_trade_activity_metrics")
    return tuple(
        MetricObservation(
            MetricFamily.EXECUTION,
            name,
            EvidenceState.MEASURED if value is not None else EvidenceState.UNAVAILABLE,
            value,
            unit,
        )
        for name, value, unit in values
    )


def tail_risk_metrics(
    returns: tuple[Decimal, ...], *, confidence: Decimal = Decimal("0.05")
) -> tuple[MetricObservation, ...]:
    """Historical tail risk only; it is unavailable when the sample is too small."""
    if not returns or confidence <= 0 or confidence >= 1:
        raise ScorecardV2Error("invalid_tail_risk_inputs")
    tail_count = int(Decimal(len(returns)) * confidence)
    if tail_count < 1:
        unavailable = MetricObservation(
            MetricFamily.RISK, "value_at_risk", EvidenceState.UNAVAILABLE, None, "fraction"
        )
        return (
            unavailable,
            MetricObservation(
                MetricFamily.RISK,
                "conditional_value_at_risk",
                EvidenceState.UNAVAILABLE,
                None,
                "fraction",
            ),
        )
    tail = tuple(sorted(returns)[:tail_count])
    return (
        MetricObservation(MetricFamily.RISK, "value_at_risk", EvidenceState.MEASURED, tail[-1], "fraction"),
        MetricObservation(
            MetricFamily.RISK,
            "conditional_value_at_risk",
            EvidenceState.MEASURED,
            sum(tail, Decimal("0")) / Decimal(len(tail)),
            "fraction",
        ),
    )


def complexity_components(
    *, parameter_count: int, turnover: Decimal, sample_size: int
) -> tuple[ScoreComponentV2, ...]:
    """Transparent diagnostics, never an authority to promote a strategy."""
    if parameter_count < 0 or turnover < 0 or sample_size < 1:
        raise ScorecardV2Error("invalid_complexity_inputs")
    penalty = (
        Decimal(parameter_count) * Decimal("2")
        + turnover * Decimal("10")
        + (Decimal("100") / Decimal(sample_size))
    )
    return (
        ScoreComponentV2(
            "complexity_penalty",
            "complexity-v1",
            penalty,
            "Parameter count, turnover, and sample size are disclosed; this is non-authoritative.",
        ),
    )


class PostgresStrategyScorecardStore:
    def __init__(self, database: PostgresDatabase) -> None: self._database = database

    def publish(self, scorecard: StrategyScorecardV2) -> UUID:
        scorecard.validate(); content_hash = scorecard.content_hash()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO strategy_scorecards VALUES "
                    "(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s) "
                    "ON CONFLICT (scorecard_id) DO NOTHING RETURNING content_hash",
                    (
                        scorecard.scorecard_id,
                        scorecard.scorecard_schema_version,
                        scorecard.strategy_id,
                        scorecard.strategy_version,
                        scorecard.research_run_id,
                        json.dumps(scorecard.feature_versions),
                        scorecard.dataset_version,
                        scorecard.cost_model_version,
                        scorecard.evaluated_at,
                        scorecard.knowledge_cutoff,
                        scorecard.status.value,
                        json.dumps(scorecard.limitations),
                        scorecard.dataset_health_status,
                        json.dumps([str(item) for item in scorecard.data_health_assessment_ids]),
                        json.dumps(scorecard.evidence_manifest, sort_keys=True),
                        content_hash,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute("SELECT content_hash FROM strategy_scorecards WHERE scorecard_id=%s", (scorecard.scorecard_id,))
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != content_hash:
                        raise ScorecardV2Error("scorecard_replay_conflict")
                    return scorecard.scorecard_id
                for metric in scorecard.metrics:
                    cursor.execute("INSERT INTO scorecard_metric_observations VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)", (uuid4(), scorecard.scorecard_id, metric.family.value, metric.name, metric.state.value, metric.value, metric.unit, json.dumps(metric.dimensions)))
                for component in scorecard.components:
                    cursor.execute("INSERT INTO scorecard_components VALUES (%s,%s,%s,%s,%s,%s)", (uuid4(), scorecard.scorecard_id, component.name, component.formula_version, component.value, component.rationale))
                for assessment_id in scorecard.data_health_assessment_ids:
                    cursor.execute(
                        "INSERT INTO scorecard_data_health_assessments VALUES (%s,%s)",
                        (scorecard.scorecard_id, assessment_id),
                    )
                return scorecard.scorecard_id
        except ScorecardV2Error: raise
        except Exception as error: raise ScorecardV2Error("scorecard_publication_failed") from error

    def link_validation_package(self, scorecard_id: UUID, package_id: UUID) -> None:
        """Link only a verified, complete package whose manifest binds this exact scorecard."""
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT s.strategy_id,s.strategy_version,s.dataset_version,s.feature_versions,"
                    "s.cost_model_version,p.content_hash,p.canonical_manifest,p.integrity_status "
                    "FROM strategy_scorecards s JOIN validation_packages p ON p.package_id=%s "
                    "WHERE s.scorecard_id=%s",
                    (package_id, scorecard_id),
                )
                row = cursor.fetchone()
                if row is None or str(row[7]) != "VERIFIED":
                    raise ScorecardV2Error("scorecard_validation_package_missing_or_unverified")
                package_hash, manifest = str(row[5]), str(row[6])
                if hashlib.sha256(manifest.encode()).hexdigest() != package_hash:
                    raise ScorecardV2Error("scorecard_validation_package_hash_mismatch")
                payload = json.loads(manifest)
                matches = (
                    payload["strategy"]["strategy_id"] == str(row[0])
                    and payload["strategy"]["version"] == str(row[1])
                    and payload["dataset"]["version"] == str(row[2])
                    and tuple(payload["feature_versions"]) == tuple(row[3])
                    and payload["cost_model_version"] == str(row[4])
                    and set(payload["evidence"]) == set(REQUIRED_EVIDENCE)
                )
                if not matches:
                    raise ScorecardV2Error("scorecard_validation_package_binding_mismatch")
                cursor.execute(
                    "INSERT INTO scorecard_validation_packages VALUES (%s,%s,%s) "
                    "ON CONFLICT (scorecard_id) DO NOTHING RETURNING scorecard_id",
                    (scorecard_id, package_id, package_hash),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        "SELECT package_id,package_content_hash FROM scorecard_validation_packages "
                        "WHERE scorecard_id=%s",
                        (scorecard_id,),
                    )
                    existing = cursor.fetchone()
                    if existing is None or UUID(str(existing[0])) != package_id or str(existing[1]) != package_hash:
                        raise ScorecardV2Error("scorecard_validation_package_replay_conflict")
        except ScorecardV2Error:
            raise
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ScorecardV2Error("scorecard_validation_package_manifest_invalid") from error
        except Exception as error:
            raise ScorecardV2Error("scorecard_validation_package_link_failed") from error

    def promotion_eligible(self, scorecard_id: UUID, *, strategy_version: str, dataset_version: str, feature_versions: tuple[str, ...], cost_model_version: str) -> bool:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT s.strategy_version,s.dataset_version,s.feature_versions,s.cost_model_version,"
                "s.status,s.dataset_health_status,s.content_hash "
                "FROM strategy_scorecards s "
                "JOIN scorecard_validation_packages l ON l.scorecard_id=s.scorecard_id "
                "JOIN validation_packages p ON p.package_id=l.package_id "
                "WHERE s.scorecard_id=%s AND p.content_hash=l.package_content_hash "
                "AND p.integrity_status='VERIFIED'",
                (scorecard_id,),
            )
            row = cursor.fetchone()
        return bool(
            row
            and str(row[0]) == strategy_version
            and str(row[1]) == dataset_version
            and tuple(row[2]) == feature_versions
            and str(row[3]) == cost_model_version
            and str(row[4]) == ScorecardStatus.REVIEW_REQUIRED.value
            and str(row[5]) != "BLOCKED"
            and len(str(row[6])) == 64
        )
