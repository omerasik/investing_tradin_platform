"""PostgreSQL persistence adapters for immutable validation and promotion evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from .persistence import PostgresDatabase
from .quant_validation import QuantValidationError, StrategyValidationPackage, _wire
from .strategy_promotion import PromotionDecision, PromotionStatus, StrategyActivation


class PostgresQuantValidationStore:
    """Content-addressed PostgreSQL evidence; mappings are explicit, never inferred."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        strategy_versions: dict[str, UUID],
        dataset_versions: dict[str, UUID],
    ) -> None:
        self._database = database
        self._strategy_versions = dict(strategy_versions)
        self._dataset_versions = dict(dataset_versions)

    def _identity(self, strategy_version: str, dataset_version: str) -> tuple[UUID, UUID]:
        try:
            return self._strategy_versions[strategy_version], self._dataset_versions[
                dataset_version
            ]
        except KeyError as error:
            raise QuantValidationError("postgres_validation_identity_mapping_required") from error

    def append(self, artifact: Any) -> UUID:
        if isinstance(artifact, StrategyValidationPackage):
            return self._append_package(artifact)
        identity = artifact.identity
        payload = _wire(artifact)
        candidate = dict(payload)
        candidate.pop("identity", None)
        actual_hash = hashlib.sha256(
            json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if identity.content_hash != actual_hash:
            raise QuantValidationError("validation_artifact_content_hash_mismatch")
        strategy_id, dataset_id = self._identity(
            artifact.strategy_version, artifact.dataset_version
        )
        evidence_type = str(
            payload.get("evidence_type", type(artifact).__name__.removesuffix("Evidence").lower())
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO quant_validation_artifacts (quant_artifact_id, artifact_type, strategy_version_id, dataset_version_id, artifact_version, content_hash, passed, evaluated_at, payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (
                        identity.artifact_id,
                        evidence_type,
                        strategy_id,
                        dataset_id,
                        identity.artifact_version,
                        identity.content_hash,
                        payload.get("passed"),
                        identity.evaluated_at,
                        json.dumps(payload, sort_keys=True),
                    ),
                )
            return identity.artifact_id
        except Exception as error:
            raise QuantValidationError("postgres_validation_artifact_persistence_failed") from error

    def _append_package(self, package: StrategyValidationPackage) -> UUID:
        strategy_id, dataset_id = self._identity(package.strategy_version, package.dataset_version)
        payload = _wire(package)
        candidate = dict(payload)
        candidate.pop("identity", None)
        if (
            package.identity.content_hash
            != hashlib.sha256(
                json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        ):
            raise QuantValidationError("validation_package_content_hash_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO validation_packages (package_id, strategy_version_id, dataset_version_id, cost_model_version, content_hash, status, created_at, limitations, feature_versions) VALUES (%s,%s,%s,%s,%s,'REVIEW_REQUIRED_OR_BLOCKED',%s,%s::jsonb,%s::jsonb)",
                    (
                        package.identity.artifact_id,
                        strategy_id,
                        dataset_id,
                        package.cost_model_version,
                        package.identity.content_hash,
                        package.identity.evaluated_at,
                        json.dumps(package.limitations),
                        json.dumps(package.feature_versions),
                    ),
                )
                for evidence_type, artifact_id in package.evidence_ids.items():
                    cursor.execute(
                        "INSERT INTO validation_package_artifacts (package_id, quant_artifact_id, evidence_type) VALUES (%s,%s,%s)",
                        (package.identity.artifact_id, artifact_id, evidence_type),
                    )
            return package.identity.artifact_id
        except Exception as error:
            raise QuantValidationError("postgres_validation_package_persistence_failed") from error

    def get(self, artifact_id: UUID) -> dict[str, Any]:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT artifact_type, payload FROM quant_validation_artifacts WHERE quant_artifact_id = %s",
                    (artifact_id,),
                )
                row = cursor.fetchone()
                if row is not None:
                    payload = dict(row[1])
                    identity = payload.get("identity", {})
                    candidate = dict(payload)
                    candidate.pop("identity", None)
                    actual_hash = hashlib.sha256(
                        json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    if identity.get("content_hash") != actual_hash:
                        raise QuantValidationError("validation_artifact_content_hash_mismatch")
                    payload["artifact_type"] = str(row[0])
                    return payload
                cursor.execute(
                    "SELECT p.package_id, s.strategy_id, s.version, d.version, p.feature_versions, p.cost_model_version, p.content_hash, p.created_at, p.limitations, p.status FROM validation_packages p JOIN strategy_versions s ON s.strategy_version_id = p.strategy_version_id JOIN dataset_versions d ON d.dataset_version_id = p.dataset_version_id WHERE p.package_id = %s",
                    (artifact_id,),
                )
                package = cursor.fetchone()
                if package is None:
                    raise KeyError(str(artifact_id))
                cursor.execute(
                    "SELECT evidence_type, quant_artifact_id FROM validation_package_artifacts WHERE package_id = %s",
                    (artifact_id,),
                )
                result = {
                    "artifact_type": "StrategyValidationPackage",
                    "identity": {
                        "artifact_id": str(package[0]),
                        "artifact_version": "validation-package-v1",
                        "content_hash": str(package[6]),
                        "evaluated_at": package[7].isoformat(),
                    },
                    "strategy_id": str(package[1]),
                    "strategy_version": str(package[2]),
                    "dataset_version": str(package[3]),
                    "feature_versions": tuple(package[4]),
                    "cost_model_version": str(package[5]),
                    "limitations": list(package[8]),
                    "promotion_status": str(package[9]),
                    "evidence_ids": {str(row[0]): str(row[1]) for row in cursor.fetchall()},
                }
                return result
        except KeyError:
            raise
        except Exception as error:
            raise QuantValidationError("postgres_validation_read_failed") from error

    def close(self) -> None:
        self._database.close()


class PostgresPromotionLedger:
    """Append-only PostgreSQL promotion and activation history with no execution authority."""

    def __init__(self, database: PostgresDatabase, *, strategy_versions: dict[str, UUID]) -> None:
        self._database, self._strategy_versions = database, dict(strategy_versions)

    def append(self, decision: PromotionDecision, *, package_id: UUID) -> None:
        try:
            strategy_version_id = self._strategy_versions[decision.strategy_version]
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO strategy_promotion_decisions (decision_id, package_id, strategy_version_id, status, reasons, held_out_periods, held_out_total_return, decided_at) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
                    (
                        decision.decision_id,
                        package_id,
                        strategy_version_id,
                        decision.status.value,
                        json.dumps(decision.reasons),
                        decision.held_out_periods,
                        decision.held_out_total_return,
                        decision.decided_at,
                    ),
                )
        except KeyError as error:
            raise ValueError("postgres_promotion_strategy_mapping_required") from error
        except Exception as error:
            raise ValueError("postgres_promotion_persistence_failed") from error

    def append_activation(self, activation: StrategyActivation) -> None:
        activation.validate()
        try:
            strategy_version_id = self._strategy_versions[activation.strategy_version]
            with self._database.transaction() as connection, connection.cursor() as cursor:
                if activation.active:
                    cursor.execute(
                        "SELECT status, strategy_version_id FROM strategy_promotion_decisions WHERE decision_id = %s",
                        (activation.promotion_decision_id,),
                    )
                    decision = cursor.fetchone()
                    if (
                        decision is None
                        or str(decision[0]) != PromotionStatus.REVIEW_REQUIRED.value
                        or UUID(str(decision[1])) != strategy_version_id
                    ):
                        raise ValueError(
                            "strategy_activation_requires_reviewable_matching_promotion"
                        )
                cursor.execute(
                    "INSERT INTO strategy_activation_events (activation_id, strategy_version_id, active, actor, effective_at, ingested_at, promotion_decision_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        activation.activation_id,
                        strategy_version_id,
                        activation.active,
                        activation.actor,
                        activation.effective_at,
                        activation.ingested_at,
                        activation.promotion_decision_id,
                    ),
                )
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("postgres_activation_persistence_failed") from error

    def strategy_enabled_as_of(self, strategy_version: str, decision_at: datetime) -> bool:
        try:
            strategy_version_id = self._strategy_versions[strategy_version]
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT active FROM strategy_activation_events WHERE strategy_version_id = %s AND effective_at <= %s AND ingested_at <= %s ORDER BY effective_at DESC, ingested_at DESC, activation_id DESC LIMIT 1",
                    (strategy_version_id, decision_at, decision_at),
                )
                row = cursor.fetchone()
                return row is not None and bool(row[0])
        except KeyError as error:
            raise ValueError("postgres_promotion_strategy_mapping_required") from error
        except Exception as error:
            raise ValueError("postgres_activation_read_failed") from error
