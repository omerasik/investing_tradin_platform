"""PostgreSQL persistence adapters for immutable validation and promotion evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from .persistence import PostgresDatabase
from .quant_validation import (
    ArtifactIdentity,
    QuantValidationError,
    StrategyValidationPackage,
    _wire,
    verify_validation_package,
)
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
        verify_validation_package(package)
        if (
            strategy_id != package.strategy_version_id
            or dataset_id != package.dataset_version_id
        ):
            raise QuantValidationError("validation_package_version_identity_mapping_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT strategy_id FROM strategy_versions WHERE strategy_version_id = %s",
                    (strategy_id,),
                )
                strategy = cursor.fetchone()
                cursor.execute(
                    "SELECT dataset_id FROM dataset_versions WHERE dataset_version_id = %s",
                    (dataset_id,),
                )
                dataset = cursor.fetchone()
                if (
                    strategy is None
                    or UUID(str(strategy[0])) != package.strategy_id
                    or dataset is None
                    or UUID(str(dataset[0])) != package.dataset_id
                ):
                    raise QuantValidationError(
                        "validation_package_definition_identity_mapping_mismatch"
                    )
                for evidence_type, artifact_id in package.evidence_ids.items():
                    cursor.execute(
                        "SELECT content_hash FROM quant_validation_artifacts WHERE quant_artifact_id = %s",
                        (artifact_id,),
                    )
                    evidence = cursor.fetchone()
                    if (
                        evidence is None
                        or str(evidence[0]) != package.evidence_hashes[evidence_type]
                    ):
                        raise QuantValidationError(
                            "validation_package_evidence_hash_mismatch"
                        )
                cursor.execute(
                    "INSERT INTO validation_packages (package_id, strategy_version_id, dataset_version_id, cost_model_version, content_hash, status, created_at, limitations, feature_versions, manifest_version, canonical_manifest, integrity_status) VALUES (%s,%s,%s,%s,%s,'REVIEW_REQUIRED_OR_BLOCKED',%s,%s::jsonb,%s::jsonb,%s,%s,'VERIFIED') ON CONFLICT (package_id) DO NOTHING RETURNING package_id",
                    (
                        package.identity.artifact_id,
                        strategy_id,
                        dataset_id,
                        package.cost_model_version,
                        package.identity.content_hash,
                        package.identity.evaluated_at,
                        json.dumps(package.limitations),
                        json.dumps(package.feature_versions),
                        package.manifest_version,
                        package.canonical_manifest,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is not None:
                    for evidence_type, artifact_id in package.evidence_ids.items():
                        cursor.execute(
                            "INSERT INTO validation_package_artifacts (package_id, quant_artifact_id, evidence_type) VALUES (%s,%s,%s)",
                            (package.identity.artifact_id, artifact_id, evidence_type),
                        )
                else:
                    cursor.execute(
                        "SELECT strategy_version_id, dataset_version_id, content_hash, manifest_version, canonical_manifest, integrity_status FROM validation_packages WHERE package_id = %s",
                        (package.identity.artifact_id,),
                    )
                    existing = cursor.fetchone()
                    cursor.execute(
                        "SELECT evidence_type, quant_artifact_id FROM validation_package_artifacts WHERE package_id = %s",
                        (package.identity.artifact_id,),
                    )
                    memberships = {
                        str(row[0]): UUID(str(row[1])) for row in cursor.fetchall()
                    }
                    if (
                        existing is None
                        or UUID(str(existing[0])) != strategy_id
                        or UUID(str(existing[1])) != dataset_id
                        or str(existing[2]) != package.identity.content_hash
                        or str(existing[3]) != package.manifest_version
                        or str(existing[4]) != package.canonical_manifest
                        or str(existing[5]) != "VERIFIED"
                        or memberships != package.evidence_ids
                    ):
                        raise QuantValidationError(
                            "validation_package_idempotency_conflict"
                        )
            return package.identity.artifact_id
        except QuantValidationError:
            raise
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
                    "SELECT p.package_id, p.strategy_version_id, p.dataset_version_id, p.cost_model_version, p.content_hash, p.created_at, p.limitations, p.status, p.feature_versions, p.manifest_version, p.canonical_manifest, p.integrity_status, s.strategy_id, d.dataset_id FROM validation_packages p JOIN strategy_versions s ON s.strategy_version_id = p.strategy_version_id JOIN dataset_versions d ON d.dataset_version_id = p.dataset_version_id WHERE p.package_id = %s",
                    (artifact_id,),
                )
                package = cursor.fetchone()
                if package is None:
                    raise KeyError(str(artifact_id))
                if str(package[11]) != "VERIFIED":
                    raise QuantValidationError("validation_package_legacy_unverifiable")
                try:
                    manifest = json.loads(str(package[10]))
                    identity = ArtifactIdentity(
                        UUID(str(manifest["package_id"])),
                        str(manifest["manifest_version"]),
                        str(package[4]),
                        datetime.fromisoformat(str(manifest["evaluated_at"])),
                    )
                    evidence_manifest = dict(manifest["evidence"])
                    recovered = StrategyValidationPackage(
                        identity,
                        str(package[9]),
                        str(package[10]),
                        UUID(str(manifest["strategy"]["strategy_id"])),
                        UUID(str(manifest["strategy"]["strategy_version_id"])),
                        str(manifest["strategy"]["version"]),
                        UUID(str(manifest["dataset"]["dataset_id"])),
                        UUID(str(manifest["dataset"]["dataset_version_id"])),
                        str(manifest["dataset"]["version"]),
                        tuple(manifest["feature_versions"]),
                        str(manifest["cost_model_version"]),
                        {
                            name: UUID(str(item["artifact_id"]))
                            for name, item in evidence_manifest.items()
                        },
                        {
                            name: str(item["content_hash"])
                            for name, item in evidence_manifest.items()
                        },
                        tuple(manifest["limitations"]),
                        str(manifest["promotion_eligibility_state"]),
                        dict(manifest["validation_metadata"]),
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    raise QuantValidationError(
                        "invalid_validation_package_manifest"
                    ) from error
                verify_validation_package(recovered)
                if (
                    recovered.identity.artifact_id != UUID(str(package[0]))
                    or recovered.strategy_version_id != UUID(str(package[1]))
                    or recovered.dataset_version_id != UUID(str(package[2]))
                    or recovered.cost_model_version != str(package[3])
                    or recovered.identity.evaluated_at != package[5]
                    or list(recovered.limitations) != list(package[6])
                    or recovered.promotion_status != str(package[7])
                    or list(recovered.feature_versions) != list(package[8])
                    or recovered.strategy_id != UUID(str(package[12]))
                    or recovered.dataset_id != UUID(str(package[13]))
                ):
                    raise QuantValidationError(
                        "validation_package_relational_projection_mismatch"
                    )
                cursor.execute(
                    "SELECT p.evidence_type, p.quant_artifact_id, a.content_hash FROM validation_package_artifacts p JOIN quant_validation_artifacts a ON a.quant_artifact_id = p.quant_artifact_id WHERE p.package_id = %s",
                    (artifact_id,),
                )
                memberships = {
                    str(row[0]): (UUID(str(row[1])), str(row[2]))
                    for row in cursor.fetchall()
                }
                if memberships != {
                    name: (
                        recovered.evidence_ids[name],
                        recovered.evidence_hashes[name],
                    )
                    for name in recovered.evidence_ids
                }:
                    raise QuantValidationError(
                        "validation_package_evidence_membership_mismatch"
                    )
                result = _wire(recovered)
                result["artifact_type"] = "StrategyValidationPackage"
                return result
        except KeyError:
            raise
        except QuantValidationError:
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
