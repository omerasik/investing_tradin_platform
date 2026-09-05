"""Provider-independent verified-session mapping and immutable PostgreSQL evidence.

The verifier is deliberately an injected boundary: this module never decodes an
unsigned token and never accepts client-supplied roles. A deployment must provide
signature/key/issuer validation and the durable evidence store before composition.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .domain import utc_now
from .persistence import PersistenceError, PostgresDatabase
from .security import (
    AuthorizationDecision,
    AuthorizationOutcome,
    OperatorPermission,
    OperatorPrincipal,
    OperatorRole,
)


class ExternalIdentityError(ValueError):
    """Fail-closed external identity or mapping-policy rejection."""


@dataclass(frozen=True, slots=True)
class VerifiedExternalSession:
    issuer: str
    subject: str
    audiences: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    session_id: str
    groups: frozenset[str]
    authentication_methods: frozenset[str]


class ExternalTokenVerifier(Protocol):
    """Deployment adapter that returns claims only after cryptographic verification."""

    def verify_token(self, token: str) -> VerifiedExternalSession: ...


@dataclass(frozen=True, slots=True)
class ExternalIdentityMappingPolicy:
    policy_id: UUID
    policy_name: str
    version: str
    issuer: str
    audience: str
    group_role_map: dict[str, OperatorRole]
    required_authentication_methods: frozenset[str]
    maximum_session_age_seconds: int
    approved_by: str
    approved_at: datetime
    enabled: bool
    content_hash: str


def build_external_identity_mapping_policy(
    *,
    policy_name: str,
    version: str,
    issuer: str,
    audience: str,
    group_role_map: dict[str, OperatorRole],
    required_authentication_methods: frozenset[str],
    maximum_session_age: timedelta,
    approved_by: str,
    approved_at: datetime,
    enabled: bool = True,
) -> ExternalIdentityMappingPolicy:
    seconds = int(maximum_session_age.total_seconds())
    payload = {
        "policy_name": policy_name,
        "version": version,
        "issuer": issuer,
        "audience": audience,
        "group_role_map": {key: value.value for key, value in sorted(group_role_map.items())},
        "required_authentication_methods": sorted(required_authentication_methods),
        "maximum_session_age_seconds": seconds,
        "approved_by": approved_by,
        "approved_at": approved_at.isoformat(),
        "enabled": enabled,
    }
    policy = ExternalIdentityMappingPolicy(
        uuid4(),
        policy_name,
        version,
        issuer,
        audience,
        dict(group_role_map),
        frozenset(required_authentication_methods),
        seconds,
        approved_by,
        approved_at,
        enabled,
        _hash(payload),
    )
    _validate_policy(policy)
    return policy


class SessionRevocationStore(Protocol):
    """Durable revocation authority, checked on every verified-session authentication.

    Exists because a JWT's own ``exp`` claim cannot be shortened after issuance: an
    operator's access must be revocable immediately (compromised device, offboarding,
    incident response) without waiting for token expiry. Checked by hashed session id
    only -- the store never needs to see, or store, the raw external session token.
    """

    def revoke(self, session_id_hash: str, *, revoked_by: str, reason: str) -> None: ...

    def is_revoked(self, session_id_hash: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ExternalSessionAuthenticator:
    verifier: ExternalTokenVerifier
    policy: ExternalIdentityMappingPolicy
    clock: Callable[[], datetime] = utc_now
    clock_skew: timedelta = timedelta(seconds=60)
    requires_durable_authorization_audit: bool = True
    revocation_store: SessionRevocationStore | None = None

    def verify(self, authorization: str | None) -> OperatorPrincipal:
        token = _bearer_token(authorization)
        try:
            session = self.verifier.verify_token(token)
        except Exception as error:
            raise PermissionError("external_session_verification_failed") from error
        now = self.clock()
        self._validate_session(session, now)
        mapped_roles = {
            self.policy.group_role_map[group]
            for group in session.groups
            if group in self.policy.group_role_map
        }
        if len(mapped_roles) != 1:
            reason = "external_session_role_unmapped" if not mapped_roles else "external_session_role_ambiguous"
            raise PermissionError(reason)
        role = next(iter(mapped_roles))
        return OperatorPrincipal(
            subject=session.subject.strip(),
            role=role,
            authentication_method="verified_external_session",
            session_id_hash=hashlib.sha256(session.session_id.encode()).hexdigest(),
            mapping_policy_id=self.policy.policy_id,
            mapping_policy_version=self.policy.version,
        )

    def _validate_session(self, session: VerifiedExternalSession, now: datetime) -> None:
        _validate_policy(self.policy)
        if _policy_hash(self.policy) != self.policy.content_hash:
            raise PermissionError("external_identity_policy_hash_mismatch")
        for value in (now, session.issued_at, session.expires_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise PermissionError("external_session_time_not_aware")
        if not self.policy.enabled:
            raise PermissionError("external_identity_policy_disabled")
        if self.policy.approved_at > now + self.clock_skew:
            raise PermissionError("external_identity_policy_not_yet_approved")
        if session.issuer != self.policy.issuer or self.policy.audience not in session.audiences:
            raise PermissionError("external_session_trust_mismatch")
        if not session.subject.strip() or not session.session_id.strip():
            raise PermissionError("external_session_identity_missing")
        if session.issued_at > now + self.clock_skew or session.expires_at <= now - self.clock_skew:
            raise PermissionError("external_session_time_invalid")
        if now - session.issued_at > timedelta(seconds=self.policy.maximum_session_age_seconds) + self.clock_skew:
            raise PermissionError("external_session_too_old")
        if not self.policy.required_authentication_methods.issubset(session.authentication_methods):
            raise PermissionError("external_session_authentication_method_insufficient")
        if self.revocation_store is not None:
            session_id_hash = hashlib.sha256(session.session_id.encode()).hexdigest()
            if self.revocation_store.is_revoked(session_id_hash):
                raise PermissionError("external_session_revoked")


class PostgresIdentitySecurityStore:
    """Immutable authority for mapping-policy versions and authorization decisions."""

    durable = True

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append_policy(
        self, policy: ExternalIdentityMappingPolicy
    ) -> ExternalIdentityMappingPolicy:
        _validate_policy(policy)
        if _policy_hash(policy) != policy.content_hash:
            raise ExternalIdentityError("external_identity_policy_hash_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT policy_id,policy_name,version,issuer,audience,group_role_map,"
                    "required_authentication_methods,maximum_session_age_seconds,approved_by,"
                    "approved_at,enabled,content_hash FROM external_identity_mapping_policies "
                    "WHERE policy_name=%s AND version=%s",
                    (policy.policy_name, policy.version),
                )
                row = cursor.fetchone()
                if row is not None:
                    restored = self._policy_from_row(row)
                    if restored.content_hash != policy.content_hash:
                        raise ExternalIdentityError("external_identity_policy_version_conflict")
                    return restored
                cursor.execute(
                    "INSERT INTO external_identity_mapping_policies VALUES "
                    "(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)",
                    (
                        policy.policy_id,
                        policy.policy_name,
                        policy.version,
                        policy.issuer,
                        policy.audience,
                        json.dumps({key: value.value for key, value in policy.group_role_map.items()}, sort_keys=True),
                        sorted(policy.required_authentication_methods),
                        policy.maximum_session_age_seconds,
                        policy.approved_by,
                        policy.approved_at,
                        policy.enabled,
                        policy.content_hash,
                    ),
                )
                return policy
        except (ExternalIdentityError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("external_identity_policy_persistence_uncertain") from error

    def append_decision(self, decision: AuthorizationDecision) -> AuthorizationDecision:
        _validate_decision(decision)
        if _decision_hash(decision) != decision.content_hash:
            raise ExternalIdentityError("authorization_decision_hash_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO authorization_decisions VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        decision.decision_id,
                        decision.occurred_at,
                        decision.subject,
                        decision.role.value if decision.role else None,
                        decision.requested_permission.value,
                        decision.outcome.value,
                        decision.reason,
                        decision.authentication_method,
                        decision.session_id_hash,
                        decision.mapping_policy_id,
                        decision.mapping_policy_version,
                        decision.content_hash,
                    ),
                )
            return decision
        except (ExternalIdentityError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("authorization_decision_persistence_uncertain") from error

    def latest_enabled_policy(self, policy_name: str) -> ExternalIdentityMappingPolicy | None:
        """The most recently approved *enabled* policy for ``policy_name``, or ``None``.

        Used by protected-runtime composition to load the active mapping policy at
        startup without a deployment having to pass policy internals through
        environment variables.
        """
        if not policy_name.strip():
            raise ExternalIdentityError("policy_name_required")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT policy_id,policy_name,version,issuer,audience,group_role_map,"
                    "required_authentication_methods,maximum_session_age_seconds,approved_by,"
                    "approved_at,enabled,content_hash FROM external_identity_mapping_policies "
                    "WHERE policy_name=%s AND enabled=TRUE "
                    "ORDER BY approved_at DESC, policy_id DESC LIMIT 1",
                    (policy_name,),
                )
                row = cursor.fetchone()
            return self._policy_from_row(row) if row is not None else None
        except (ExternalIdentityError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("external_identity_policy_read_uncertain") from error

    def recent_decisions(self, limit: int = 100) -> list[AuthorizationDecision]:
        if not 1 <= limit <= 1000:
            raise ExternalIdentityError("authorization_decision_limit_invalid")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT decision_id,occurred_at,subject,role,requested_permission,outcome,"
                    "reason,authentication_method,session_id_hash,mapping_policy_id,"
                    "mapping_policy_version,content_hash "
                    "FROM authorization_decisions ORDER BY occurred_at DESC,decision_id DESC LIMIT %s",
                    (limit,),
                )
                rows = cursor.fetchall()
            return [self._decision_from_row(row) for row in rows]
        except (ExternalIdentityError, PersistenceError):
            raise
        except Exception as error:
            raise PersistenceError("authorization_decision_read_uncertain") from error

    @staticmethod
    def _policy_from_row(row: tuple[object, ...]) -> ExternalIdentityMappingPolicy:
        mapping = cast(dict[str, object], row[5])
        return ExternalIdentityMappingPolicy(
            row[0],  # type: ignore[arg-type]
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            {str(key): OperatorRole(str(value)) for key, value in mapping.items()},
            frozenset(str(value) for value in cast(list[object], row[6])),
            int(cast(Any, row[7])),
            str(row[8]),
            row[9],  # type: ignore[arg-type]
            bool(row[10]),
            str(row[11]),
        )

    @staticmethod
    def _decision_from_row(row: tuple[object, ...]) -> AuthorizationDecision:
        return AuthorizationDecision(
            row[0],  # type: ignore[arg-type]
            row[1],  # type: ignore[arg-type]
            str(row[2]),
            OperatorRole(str(row[3])) if row[3] is not None else None,
            OperatorPermission(str(row[4])),
            AuthorizationOutcome(str(row[5])),
            str(row[6]),
            str(row[7]),
            str(row[8]) if row[8] is not None else None,
            row[9],  # type: ignore[arg-type]
            str(row[10]) if row[10] is not None else None,
            str(row[11]),
        )


class PostgresSessionRevocationStore:
    """Durable, insert-only revocation ledger for verified external sessions.

    Modeled as an append-only event log (``operator_session_events``, immutable at the
    schema level -- see migration ``20260906_0037``) rather than a mutable row, matching
    every other durable-evidence store in this codebase: a revocation is a fact that
    happened at a point in time, not a field that gets flipped.
    """

    durable = True

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def revoke(self, session_id_hash: str, *, revoked_by: str, reason: str) -> None:
        _validate_session_hash(session_id_hash)
        if not revoked_by.strip() or not reason.strip():
            raise ExternalIdentityError("session_revocation_invalid")
        occurred_at = utc_now()
        content_hash = _hash(
            {
                "session_id_hash": session_id_hash,
                "event_type": "revoked",
                "occurred_at": occurred_at.isoformat(),
                "actor": revoked_by,
                "reason": reason,
            }
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO operator_session_events VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        session_id_hash,
                        "revoked",
                        occurred_at,
                        revoked_by,
                        reason,
                        content_hash,
                    ),
                )
        except PersistenceError:
            raise
        except Exception as error:
            raise PersistenceError("session_revocation_persistence_uncertain") from error

    def is_revoked(self, session_id_hash: str) -> bool:
        _validate_session_hash(session_id_hash)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM operator_session_events "
                    "WHERE session_id_hash=%s AND event_type='revoked' LIMIT 1",
                    (session_id_hash,),
                )
                return cursor.fetchone() is not None
        except PersistenceError:
            raise
        except Exception as error:
            raise PersistenceError("session_revocation_read_uncertain") from error


def _validate_session_hash(session_id_hash: str) -> None:
    if len(session_id_hash) != 64 or any(
        character not in "0123456789abcdef" for character in session_id_hash
    ):
        raise ExternalIdentityError("session_id_hash_invalid")


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise PermissionError("external_session_missing")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme != "Bearer" or not token or token.strip() != token or " " in token:
        raise PermissionError("external_session_malformed")
    return token


def _validate_policy(policy: ExternalIdentityMappingPolicy) -> None:
    parsed = urlsplit(policy.issuer)
    valid_issuer = (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and not policy.issuer.endswith("/")
    )
    aware = policy.approved_at.tzinfo is not None and policy.approved_at.utcoffset() is not None
    if (
        not policy.policy_name.strip()
        or not policy.version.strip()
        or not valid_issuer
        or not policy.audience.strip()
        or not policy.group_role_map
        or any(not key.strip() for key in policy.group_role_map)
        or not policy.required_authentication_methods
        or any(not value.strip() for value in policy.required_authentication_methods)
        or not 60 <= policy.maximum_session_age_seconds <= 86_400
        or not policy.approved_by.strip()
        or not aware
    ):
        raise ExternalIdentityError("external_identity_policy_invalid")


def _policy_hash(policy: ExternalIdentityMappingPolicy) -> str:
    return _hash(
        {
            "policy_name": policy.policy_name,
            "version": policy.version,
            "issuer": policy.issuer,
            "audience": policy.audience,
            "group_role_map": {key: value.value for key, value in sorted(policy.group_role_map.items())},
            "required_authentication_methods": sorted(policy.required_authentication_methods),
            "maximum_session_age_seconds": policy.maximum_session_age_seconds,
            "approved_by": policy.approved_by,
            "approved_at": policy.approved_at.isoformat(),
            "enabled": policy.enabled,
        }
    )


def _validate_decision(decision: AuthorizationDecision) -> None:
    aware = decision.occurred_at.tzinfo is not None and decision.occurred_at.utcoffset() is not None
    if (
        not aware
        or not decision.subject.strip()
        or not decision.reason.strip()
        or not decision.authentication_method.strip()
        or (decision.session_id_hash is not None and (len(decision.session_id_hash) != 64 or any(character not in "0123456789abcdef" for character in decision.session_id_hash)))
    ):
        raise ExternalIdentityError("authorization_decision_invalid")


def _decision_hash(decision: AuthorizationDecision) -> str:
    return _hash(
        {
            "occurred_at": decision.occurred_at.isoformat(),
            "subject": decision.subject,
            "role": decision.role.value if decision.role is not None else None,
            "requested_permission": decision.requested_permission.value,
            "outcome": decision.outcome.value,
            "reason": decision.reason,
            "authentication_method": decision.authentication_method,
            "session_id_hash": decision.session_id_hash,
            "mapping_policy_id": str(decision.mapping_policy_id) if decision.mapping_policy_id else None,
            "mapping_policy_version": decision.mapping_policy_version,
        }
    )


def _hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
