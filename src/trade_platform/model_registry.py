"""Durable model metadata and approval evidence; this module does not execute models."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

from .domain import utc_now


class ModelStatus(str, Enum):
    REGISTERED = "REGISTERED"
    APPROVED = "APPROVED"
    DISABLED = "DISABLED"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class ModelVersion:
    model_id: UUID
    name: str
    version: str
    task: str
    feature_version: str
    dataset_version: str
    artifact_reference: str
    created_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(cls, *, name: str, version: str, task: str, feature_version: str, dataset_version: str, artifact_reference: str) -> "ModelVersion":
        values = (name, version, task, feature_version, dataset_version, artifact_reference)
        if any(not value.strip() for value in values):
            raise ValueError("model_version_fields_are_required")
        return cls(uuid4(), name, version, task, feature_version, dataset_version, artifact_reference)


@dataclass(frozen=True, slots=True)
class ModelValidation:
    validation_id: UUID
    model_id: UUID
    metrics: dict[str, Decimal]
    economic_value_after_cost: Decimal
    evidence_reference: str
    validated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(cls, *, model_id: UUID, metrics: dict[str, Decimal], economic_value_after_cost: Decimal, evidence_reference: str) -> "ModelValidation":
        if not metrics or any(not name.strip() or not value.is_finite() for name, value in metrics.items()):
            raise ValueError("finite_named_validation_metrics_are_required")
        if not economic_value_after_cost.is_finite() or not evidence_reference.strip():
            raise ValueError("validation_evidence_is_required")
        return cls(uuid4(), model_id, dict(metrics), economic_value_after_cost, evidence_reference)


@dataclass(frozen=True, slots=True)
class ModelApprovalEvent:
    event_id: UUID
    model_id: UUID
    status: ModelStatus
    actor: str
    reason: str
    validation_id: UUID | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ModelDriftObservation:
    observation_id: UUID
    model_id: UUID
    metric: str
    score: Decimal
    threshold: Decimal
    observed_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(cls, *, model_id: UUID, metric: str, score: Decimal, threshold: Decimal) -> "ModelDriftObservation":
        if not metric.strip() or not score.is_finite() or not threshold.is_finite() or score < 0 or threshold <= 0:
            raise ValueError("invalid_drift_observation")
        return cls(uuid4(), model_id, metric, score, threshold)


@dataclass(frozen=True, slots=True)
class ModelPrediction:
    prediction_id: UUID
    model_id: UUID
    instrument_id: str
    horizon: str
    prediction: Decimal
    confidence: Decimal
    calibration: Decimal
    feature_version: str
    data_version: str
    regime: str
    explanation: str
    uncertainty: Decimal
    issued_at: datetime
    expires_at: datetime

    @classmethod
    def create(cls, *, model_id: UUID, instrument_id: str, horizon: str, prediction: Decimal, confidence: Decimal, calibration: Decimal, feature_version: str, data_version: str, regime: str, explanation: str, uncertainty: Decimal, issued_at: datetime, expires_at: datetime) -> "ModelPrediction":
        text = (instrument_id, horizon, feature_version, data_version, regime, explanation)
        numbers = (prediction, confidence, calibration, uncertainty)
        if any(not value.strip() for value in text) or any(not value.is_finite() for value in numbers):
            raise ValueError("complete_finite_prediction_evidence_is_required")
        if not Decimal("0") <= confidence <= Decimal("1") or not Decimal("0") <= calibration <= Decimal("1") or uncertainty < 0:
            raise ValueError("invalid_prediction_confidence_or_uncertainty")
        if issued_at.tzinfo is None or issued_at.utcoffset() is None or expires_at.tzinfo is None or expires_at.utcoffset() is None or expires_at <= issued_at:
            raise ValueError("prediction_expiry_must_follow_timezone_aware_issue_time")
        return cls(uuid4(), model_id, instrument_id, horizon, prediction, confidence, calibration, feature_version, data_version, regime, explanation, uncertainty, issued_at, expires_at)


class SQLiteModelRegistry:
    """Append-only validation/approval/drift evidence with fail-closed approval lookup."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS model_versions (
                model_id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL, task TEXT NOT NULL,
                feature_version TEXT NOT NULL, dataset_version TEXT NOT NULL, artifact_reference TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(name, version))"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS model_validations (
                validation_id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES model_versions(model_id),
                metrics_json TEXT NOT NULL, economic_value_after_cost TEXT NOT NULL,
                evidence_reference TEXT NOT NULL, validated_at TEXT NOT NULL)"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS model_approval_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                model_id TEXT NOT NULL REFERENCES model_versions(model_id), status TEXT NOT NULL,
                actor TEXT NOT NULL, reason TEXT NOT NULL, validation_id TEXT,
                occurred_at TEXT NOT NULL)"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS model_drift_observations (
                observation_id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES model_versions(model_id),
                metric TEXT NOT NULL, score TEXT NOT NULL, threshold TEXT NOT NULL, observed_at TEXT NOT NULL)"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS model_predictions (
                prediction_id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES model_versions(model_id),
                instrument_id TEXT NOT NULL, horizon TEXT NOT NULL, prediction TEXT NOT NULL,
                confidence TEXT NOT NULL, calibration TEXT NOT NULL, feature_version TEXT NOT NULL,
                data_version TEXT NOT NULL, regime TEXT NOT NULL, explanation TEXT NOT NULL,
                uncertainty TEXT NOT NULL, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL)"""
        )
        self._connection.commit()

    def register(self, model: ModelVersion) -> None:
        try:
            self._connection.execute(
                "INSERT INTO model_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(model.model_id), model.name, model.version, model.task, model.feature_version,
                 model.dataset_version, model.artifact_reference, model.created_at.isoformat()),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("duplicate_model_version") from error
        self._connection.commit()

    def get(self, model_id: UUID) -> ModelVersion:
        row = self._connection.execute("SELECT * FROM model_versions WHERE model_id = ?", (str(model_id),)).fetchone()
        if row is None:
            raise KeyError(str(model_id))
        return ModelVersion(UUID(row[0]), row[1], row[2], row[3], row[4], row[5], row[6], datetime.fromisoformat(row[7]))

    def append_validation(self, validation: ModelValidation) -> None:
        self.get(validation.model_id)
        try:
            self._connection.execute(
                "INSERT INTO model_validations VALUES (?, ?, ?, ?, ?, ?)",
                (str(validation.validation_id), str(validation.model_id),
                 json.dumps({name: str(value) for name, value in validation.metrics.items()}, sort_keys=True),
                 str(validation.economic_value_after_cost), validation.evidence_reference, validation.validated_at.isoformat()),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("duplicate_model_validation") from error
        self._connection.commit()

    def approve(self, model_id: UUID, *, validation_id: UUID, actor: str, reason: str, explicit_reapproval: bool = False) -> ModelApprovalEvent:
        self.get(model_id)
        if not actor.strip() or not reason.strip():
            raise ValueError("approval_actor_and_reason_are_required")
        row = self._connection.execute(
            "SELECT model_id FROM model_validations WHERE validation_id = ?", (str(validation_id),)
        ).fetchone()
        if row is None or row[0] != str(model_id):
            raise ValueError("approval_requires_matching_validation")
        if self.status(model_id) is ModelStatus.DISABLED and not explicit_reapproval:
            raise PermissionError("disabled_model_requires_explicit_reapproval")
        return self._append_event(model_id, ModelStatus.APPROVED, actor, reason, validation_id)

    def disable(self, model_id: UUID, *, actor: str, reason: str) -> ModelApprovalEvent:
        self.get(model_id)
        if not actor.strip() or not reason.strip():
            raise ValueError("disable_actor_and_reason_are_required")
        return self._append_event(model_id, ModelStatus.DISABLED, actor, reason, None)

    def status(self, model_id: UUID) -> ModelStatus:
        self.get(model_id)
        row = self._connection.execute(
            "SELECT status FROM model_approval_events WHERE model_id = ? ORDER BY sequence DESC LIMIT 1", (str(model_id),)
        ).fetchone()
        return ModelStatus(row[0]) if row is not None else ModelStatus.REGISTERED

    def is_approved(self, model_id: UUID) -> bool:
        return self.status(model_id) is ModelStatus.APPROVED

    def record_drift(self, observation: ModelDriftObservation, *, actor: str = "drift-monitor") -> bool:
        self.get(observation.model_id)
        self._connection.execute(
            "INSERT INTO model_drift_observations VALUES (?, ?, ?, ?, ?, ?)",
            (str(observation.observation_id), str(observation.model_id), observation.metric,
             str(observation.score), str(observation.threshold), observation.observed_at.isoformat()),
        )
        self._connection.commit()
        breached = observation.score >= observation.threshold
        if breached and self.status(observation.model_id) is ModelStatus.APPROVED:
            self.disable(observation.model_id, actor=actor, reason=f"drift_threshold_breached:{observation.metric}")
        return breached

    def append_prediction(self, prediction: ModelPrediction) -> None:
        model = self.get(prediction.model_id)
        if not self.is_approved(prediction.model_id):
            raise PermissionError("prediction_requires_approved_model")
        if prediction.feature_version != model.feature_version:
            raise ValueError("prediction_feature_version_mismatch")
        try:
            self._connection.execute(
                "INSERT INTO model_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(prediction.prediction_id), str(prediction.model_id), prediction.instrument_id, prediction.horizon,
                 str(prediction.prediction), str(prediction.confidence), str(prediction.calibration), prediction.feature_version,
                 prediction.data_version, prediction.regime, prediction.explanation, str(prediction.uncertainty),
                 prediction.issued_at.isoformat(), prediction.expires_at.isoformat()),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("duplicate_model_prediction") from error
        self._connection.commit()

    def active_prediction(self, model_id: UUID, instrument_id: str, horizon: str, *, as_of: datetime) -> ModelPrediction:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("prediction_as_of_must_be_timezone_aware")
        row = self._connection.execute(
            """SELECT * FROM model_predictions WHERE model_id = ? AND instrument_id = ? AND horizon = ?
               AND issued_at <= ? AND expires_at > ? ORDER BY issued_at DESC, rowid DESC LIMIT 1""",
            (str(model_id), instrument_id, horizon, as_of.isoformat(), as_of.isoformat()),
        ).fetchone()
        if row is None:
            raise KeyError("no_active_model_prediction")
        return ModelPrediction(UUID(row[0]), UUID(row[1]), row[2], row[3], Decimal(row[4]), Decimal(row[5]), Decimal(row[6]), row[7], row[8], row[9], row[10], Decimal(row[11]), datetime.fromisoformat(row[12]), datetime.fromisoformat(row[13]))

    def events(self, model_id: UUID) -> tuple[ModelApprovalEvent, ...]:
        self.get(model_id)
        rows = self._connection.execute(
            "SELECT event_id, model_id, status, actor, reason, validation_id, occurred_at FROM model_approval_events WHERE model_id = ? ORDER BY sequence",
            (str(model_id),),
        ).fetchall()
        return tuple(ModelApprovalEvent(UUID(row[0]), UUID(row[1]), ModelStatus(row[2]), row[3], row[4], None if row[5] is None else UUID(row[5]), datetime.fromisoformat(row[6])) for row in rows)

    def _append_event(self, model_id: UUID, status: ModelStatus, actor: str, reason: str, validation_id: UUID | None) -> ModelApprovalEvent:
        event = ModelApprovalEvent(uuid4(), model_id, status, actor, reason, validation_id, utc_now())
        self._connection.execute(
            "INSERT INTO model_approval_events (event_id, model_id, status, actor, reason, validation_id, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(event.event_id), str(model_id), status.value, actor, reason,
             None if validation_id is None else str(validation_id), event.occurred_at.isoformat()),
        )
        self._connection.commit()
        return event

    def close(self) -> None:
        self._connection.close()
