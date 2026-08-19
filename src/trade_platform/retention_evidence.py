"""Durable, non-destructive retention and object-manifest evidence.

This authority catalogs hashes and evaluates policy deadlines.  It neither
stores object bytes nor exposes deletion, network, or provider operations.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, cast
from uuid import UUID, uuid4

from .persistence import PersistenceError, PostgresDatabase


class RetentionEvidenceError(ValueError):
    pass


class RetentionClassification(str, Enum):
    BACKUP = "BACKUP"
    RAW_DATA = "RAW_DATA"
    MODEL_ARTIFACT = "MODEL_ARTIFACT"
    CONFIGURATION = "CONFIGURATION"
    AUDIT_EVIDENCE = "AUDIT_EVIDENCE"


class ObjectEvidenceKind(str, Enum):
    DATABASE_BACKUP = "DATABASE_BACKUP"
    RAW_PROVIDER_PAYLOAD = "RAW_PROVIDER_PAYLOAD"
    MODEL_ARTIFACT = "MODEL_ARTIFACT"
    CONFIGURATION_SNAPSHOT = "CONFIGURATION_SNAPSHOT"
    AUDIT_EXPORT = "AUDIT_EXPORT"


class RetentionDisposition(str, Enum):
    RETAIN = "RETAIN"
    ELIGIBLE_FOR_REVIEW = "ELIGIBLE_FOR_REVIEW"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    policy_id: UUID
    policy_name: str
    version: str
    classification: RetentionClassification
    retention_seconds: int
    legal_hold: bool
    owner: str
    approved_by: str
    approved_at: datetime
    enabled: bool
    disposition_authority: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ObjectEvidenceManifest:
    manifest_id: UUID
    object_reference: str
    object_kind: ObjectEvidenceKind
    media_type: str
    byte_size: int
    sha256: str
    source_reference: str
    policy_id: UUID
    captured_at: datetime
    storage_state: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class RetentionEvaluation:
    evaluation_id: UUID
    manifest_id: UUID
    policy_id: UUID
    idempotency_key: str
    evaluated_at: datetime
    retain_until: datetime
    disposition: RetentionDisposition
    reason: str
    content_hash: str


def build_retention_policy(
    *,
    policy_name: str,
    version: str,
    classification: RetentionClassification,
    retention: timedelta,
    legal_hold: bool,
    owner: str,
    approved_by: str,
    approved_at: datetime,
    enabled: bool = True,
) -> RetentionPolicy:
    policy = RetentionPolicy(
        uuid4(),
        policy_name,
        version,
        classification,
        int(retention.total_seconds()),
        legal_hold,
        owner,
        approved_by,
        approved_at,
        enabled,
        "REVIEW_ONLY_NO_DELETE",
        "",
    )
    policy = replace(policy, content_hash=_policy_hash(policy))
    _validate_policy(policy)
    return policy


def build_object_manifest(
    *,
    object_reference: str,
    object_kind: ObjectEvidenceKind,
    media_type: str,
    byte_size: int,
    sha256: str,
    source_reference: str,
    policy_id: UUID,
    captured_at: datetime,
) -> ObjectEvidenceManifest:
    manifest = ObjectEvidenceManifest(
        uuid4(),
        object_reference,
        object_kind,
        media_type,
        byte_size,
        sha256,
        source_reference,
        policy_id,
        captured_at,
        "MANIFEST_ONLY",
        "",
    )
    manifest = replace(manifest, content_hash=_manifest_hash(manifest))
    _validate_manifest(manifest)
    return manifest


def evaluate_retention(
    policy: RetentionPolicy,
    manifest: ObjectEvidenceManifest,
    *,
    evaluated_at: datetime,
    idempotency_key: str,
) -> RetentionEvaluation:
    _validate_policy(policy)
    _validate_manifest(manifest)
    _aware(evaluated_at, "retention_evaluation_time_must_be_timezone_aware")
    if manifest.policy_id != policy.policy_id or manifest.captured_at < policy.approved_at:
        raise RetentionEvidenceError("retention_policy_manifest_mismatch")
    if evaluated_at < manifest.captured_at or not idempotency_key.strip():
        raise RetentionEvidenceError("invalid_retention_evaluation")
    retain_until = manifest.captured_at + timedelta(seconds=policy.retention_seconds)
    if not policy.enabled:
        disposition, reason = RetentionDisposition.RETAIN, "POLICY_DISABLED"
    elif policy.legal_hold:
        disposition, reason = RetentionDisposition.RETAIN, "LEGAL_HOLD"
    elif evaluated_at < retain_until:
        disposition, reason = RetentionDisposition.RETAIN, "RETENTION_WINDOW_ACTIVE"
    else:
        disposition, reason = (
            RetentionDisposition.ELIGIBLE_FOR_REVIEW,
            "RETENTION_WINDOW_ELAPSED_REVIEW_REQUIRED",
        )
    evaluation = RetentionEvaluation(
        uuid4(),
        manifest.manifest_id,
        policy.policy_id,
        idempotency_key,
        evaluated_at,
        retain_until,
        disposition,
        reason,
        "",
    )
    return replace(evaluation, content_hash=_evaluation_hash(evaluation))


class PostgresRetentionEvidenceStore:
    """Append-only manifest and lifecycle evidence; intentionally no delete API."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append_policy(self, policy: RetentionPolicy) -> RetentionPolicy:
        _validate_policy(policy)
        if _policy_hash(policy) != policy.content_hash:
            raise RetentionEvidenceError("retention_policy_hash_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT policy_id,policy_name,version,classification,retention_seconds,"
                    "legal_hold,owner,approved_by,approved_at,enabled,disposition_authority,"
                    "content_hash FROM retention_policy_versions WHERE policy_name=%s AND version=%s",
                    (policy.policy_name, policy.version),
                )
                row = cursor.fetchone()
                if row is not None:
                    recovered = self._policy_from_row(row)
                    if recovered.content_hash != policy.content_hash:
                        raise RetentionEvidenceError("retention_policy_version_conflict")
                    return recovered
                cursor.execute(
                    "INSERT INTO retention_policy_versions VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        policy.policy_id,
                        policy.policy_name,
                        policy.version,
                        policy.classification.value,
                        policy.retention_seconds,
                        policy.legal_hold,
                        policy.owner,
                        policy.approved_by,
                        policy.approved_at,
                        policy.enabled,
                        policy.disposition_authority,
                        policy.content_hash,
                    ),
                )
                return policy
        except (RetentionEvidenceError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("retention_policy_persistence_uncertain") from error

    def append_manifest(self, manifest: ObjectEvidenceManifest) -> ObjectEvidenceManifest:
        _validate_manifest(manifest)
        if _manifest_hash(manifest) != manifest.content_hash:
            raise RetentionEvidenceError("object_manifest_hash_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT manifest_id,object_reference,object_kind,media_type,byte_size,sha256,"
                    "source_reference,policy_id,captured_at,storage_state,content_hash FROM "
                    "object_evidence_manifests WHERE object_reference=%s",
                    (manifest.object_reference,),
                )
                row = cursor.fetchone()
                if row is not None:
                    recovered = self._manifest_from_row(row)
                    if recovered.content_hash != manifest.content_hash:
                        raise RetentionEvidenceError("object_manifest_reference_conflict")
                    return recovered
                policy = self._select_policy(cursor, manifest.policy_id)
                if manifest.captured_at < policy.approved_at:
                    raise RetentionEvidenceError("retention_policy_manifest_mismatch")
                cursor.execute(
                    "INSERT INTO object_evidence_manifests VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        manifest.manifest_id,
                        manifest.object_reference,
                        manifest.object_kind.value,
                        manifest.media_type,
                        manifest.byte_size,
                        manifest.sha256,
                        manifest.source_reference,
                        manifest.policy_id,
                        manifest.captured_at,
                        manifest.storage_state,
                        manifest.content_hash,
                    ),
                )
                return manifest
        except (RetentionEvidenceError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("object_manifest_persistence_uncertain") from error

    def evaluate(
        self,
        manifest_id: UUID,
        *,
        evaluated_at: datetime,
        idempotency_key: str,
    ) -> RetentionEvaluation:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT evaluation_id,manifest_id,policy_id,idempotency_key,evaluated_at,"
                    "retain_until,disposition,reason,content_hash FROM retention_evaluations "
                    "WHERE idempotency_key=%s",
                    (idempotency_key,),
                )
                existing = cursor.fetchone()
                cursor.execute(
                    "SELECT manifest_id,object_reference,object_kind,media_type,byte_size,sha256,"
                    "source_reference,policy_id,captured_at,storage_state,content_hash FROM "
                    "object_evidence_manifests WHERE manifest_id=%s",
                    (manifest_id,),
                )
                manifest_row = cursor.fetchone()
                if manifest_row is None:
                    raise RetentionEvidenceError("object_manifest_not_found")
                manifest = self._manifest_from_row(manifest_row)
                policy = self._select_policy(cursor, manifest.policy_id)
                expected = evaluate_retention(
                    policy,
                    manifest,
                    evaluated_at=evaluated_at,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    recovered = self._evaluation_from_row(existing)
                    if (
                        recovered.manifest_id != expected.manifest_id
                        or recovered.policy_id != expected.policy_id
                        or recovered.evaluated_at != expected.evaluated_at
                        or recovered.retain_until != expected.retain_until
                        or recovered.disposition != expected.disposition
                        or recovered.reason != expected.reason
                    ):
                        raise RetentionEvidenceError("retention_evaluation_idempotency_conflict")
                    return recovered
                cursor.execute(
                    "INSERT INTO retention_evaluations VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        expected.evaluation_id,
                        expected.manifest_id,
                        expected.policy_id,
                        expected.idempotency_key,
                        expected.evaluated_at,
                        expected.retain_until,
                        expected.disposition.value,
                        expected.reason,
                        expected.content_hash,
                    ),
                )
                return expected
        except (RetentionEvidenceError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("retention_evaluation_persistence_uncertain") from error

    def get_manifest(self, manifest_id: UUID) -> ObjectEvidenceManifest:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT manifest_id,object_reference,object_kind,media_type,byte_size,sha256,"
                    "source_reference,policy_id,captured_at,storage_state,content_hash FROM "
                    "object_evidence_manifests WHERE manifest_id=%s",
                    (manifest_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RetentionEvidenceError("object_manifest_not_found")
                return self._manifest_from_row(row)
        except (RetentionEvidenceError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("object_manifest_read_uncertain") from error

    @staticmethod
    def _select_policy(cursor: Any, policy_id: UUID) -> RetentionPolicy:
        cursor.execute(
            "SELECT policy_id,policy_name,version,classification,retention_seconds,legal_hold,"
            "owner,approved_by,approved_at,enabled,disposition_authority,content_hash FROM "
            "retention_policy_versions WHERE policy_id=%s",
            (policy_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RetentionEvidenceError("retention_policy_not_found")
        return PostgresRetentionEvidenceStore._policy_from_row(row)

    @staticmethod
    def _policy_from_row(row: tuple[object, ...]) -> RetentionPolicy:
        policy = RetentionPolicy(
            UUID(str(row[0])),
            str(row[1]),
            str(row[2]),
            RetentionClassification(str(row[3])),
            int(str(row[4])),
            bool(row[5]),
            str(row[6]),
            str(row[7]),
            cast(datetime, row[8]),
            bool(row[9]),
            str(row[10]),
            str(row[11]),
        )
        if _policy_hash(policy) != policy.content_hash:
            raise RetentionEvidenceError("retention_policy_hash_mismatch")
        return policy

    @staticmethod
    def _manifest_from_row(row: tuple[object, ...]) -> ObjectEvidenceManifest:
        manifest = ObjectEvidenceManifest(
            UUID(str(row[0])),
            str(row[1]),
            ObjectEvidenceKind(str(row[2])),
            str(row[3]),
            int(str(row[4])),
            str(row[5]),
            str(row[6]),
            UUID(str(row[7])),
            cast(datetime, row[8]),
            str(row[9]),
            str(row[10]),
        )
        if _manifest_hash(manifest) != manifest.content_hash:
            raise RetentionEvidenceError("object_manifest_hash_mismatch")
        return manifest

    @staticmethod
    def _evaluation_from_row(row: tuple[object, ...]) -> RetentionEvaluation:
        evaluation = RetentionEvaluation(
            UUID(str(row[0])),
            UUID(str(row[1])),
            UUID(str(row[2])),
            str(row[3]),
            cast(datetime, row[4]),
            cast(datetime, row[5]),
            RetentionDisposition(str(row[6])),
            str(row[7]),
            str(row[8]),
        )
        if _evaluation_hash(evaluation) != evaluation.content_hash:
            raise RetentionEvidenceError("retention_evaluation_hash_mismatch")
        return evaluation


def _validate_policy(policy: RetentionPolicy) -> None:
    _aware(policy.approved_at, "retention_policy_time_must_be_timezone_aware")
    if (
        not policy.policy_name.strip()
        or not policy.version.strip()
        or policy.retention_seconds <= 0
        or not policy.owner.strip()
        or not policy.approved_by.strip()
        or policy.disposition_authority != "REVIEW_ONLY_NO_DELETE"
    ):
        raise RetentionEvidenceError("invalid_retention_policy")


def _validate_manifest(manifest: ObjectEvidenceManifest) -> None:
    _aware(manifest.captured_at, "object_manifest_time_must_be_timezone_aware")
    if (
        not _opaque_reference(manifest.object_reference)
        or not _opaque_reference(manifest.source_reference)
        or len(manifest.media_type) > 127
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*", manifest.media_type)
        or manifest.byte_size < 0
        or not re.fullmatch(r"[0-9a-f]{64}", manifest.sha256)
        or manifest.storage_state != "MANIFEST_ONLY"
    ):
        raise RetentionEvidenceError("invalid_object_manifest")


def _opaque_reference(value: str) -> bool:
    return bool(
        0 < len(value) <= 255
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value)
        and ".." not in value
        and "//" not in value
        and not value.endswith("/")
    )


def _policy_hash(policy: RetentionPolicy) -> str:
    return _hash(
        {
            "policy_name": policy.policy_name,
            "version": policy.version,
            "classification": policy.classification.value,
            "retention_seconds": policy.retention_seconds,
            "legal_hold": policy.legal_hold,
            "owner": policy.owner,
            "approved_by": policy.approved_by,
            "approved_at": _iso(policy.approved_at),
            "enabled": policy.enabled,
            "disposition_authority": policy.disposition_authority,
        }
    )


def _manifest_hash(manifest: ObjectEvidenceManifest) -> str:
    return _hash(
        {
            "object_reference": manifest.object_reference,
            "object_kind": manifest.object_kind.value,
            "media_type": manifest.media_type,
            "byte_size": manifest.byte_size,
            "sha256": manifest.sha256,
            "source_reference": manifest.source_reference,
            "policy_id": str(manifest.policy_id),
            "captured_at": _iso(manifest.captured_at),
            "storage_state": manifest.storage_state,
        }
    )


def _evaluation_hash(evaluation: RetentionEvaluation) -> str:
    return _hash(
        {
            "manifest_id": str(evaluation.manifest_id),
            "policy_id": str(evaluation.policy_id),
            "idempotency_key": evaluation.idempotency_key,
            "evaluated_at": _iso(evaluation.evaluated_at),
            "retain_until": _iso(evaluation.retain_until),
            "disposition": evaluation.disposition.value,
            "reason": evaluation.reason,
        }
    )


def _aware(value: datetime, message: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RetentionEvidenceError(message)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
