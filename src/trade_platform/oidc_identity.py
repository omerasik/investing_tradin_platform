"""Production-grade external identity verification: RFC 7517 JWKS + RFC 7519 JWT.

This is the first real implementation of the :class:`~trade_platform.external_identity.ExternalTokenVerifier`
boundary. That boundary was deliberately left as an injected, unimplemented
``Protocol`` -- the codebase's own module docstring says a deployment "must provide
signature/key/issuer validation ... before composition" -- specifically so production
could never be faked into believing an unsigned or unverified token had been checked.
:class:`JwksExternalTokenVerifier` performs actual asymmetric signature verification
against keys published by a configured OpenID Connect / OAuth2 issuer's JWKS endpoint.

This module never accepts the ``none`` algorithm or any symmetric (``HS*``) algorithm --
a symmetric secret shared with a browser-facing identity provider would let any holder
of that secret mint arbitrary operator identities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import jwt

from .external_identity import VerifiedExternalSession

__all__ = [
    "JwksExternalTokenVerifier",
    "OidcConfigurationError",
    "OidcVerificationError",
    "SigningKeySource",
]

_ALLOWED_ALGORITHMS: tuple[str, ...] = (
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "PS256",
    "PS384",
    "PS512",
)


class OidcConfigurationError(ValueError):
    """A JWKS/OIDC verifier was misconfigured; fail closed before any request is served."""


class OidcVerificationError(RuntimeError):
    """A presented token failed cryptographic, structural, or claim-shape verification."""


class SigningKeySource(Protocol):
    """The subset of :class:`jwt.PyJWKClient` this verifier depends on.

    Kept as a narrow structural protocol (rather than requiring the concrete
    ``PyJWKClient`` type) so tests can exercise real signature verification against a
    fixture key pair without performing any network JWKS fetch.
    """

    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def _require_https_url(value: str, *, field_name: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise OidcConfigurationError(f"{field_name}_must_be_https_url")
    return value


@dataclass(frozen=True, slots=True)
class JwksExternalTokenVerifier:
    """Verifies bearer tokens against a live JWKS endpoint; never trusts an unsigned claim.

    ``key_source`` performs its own bounded, cached HTTPS fetch (the real
    ``PyJWKClient`` built by :meth:`from_jwks_url` caches keys by key id and only
    refreshes on an unseen ``kid``), so a compromised or misbehaving issuer cannot
    force unbounded network calls per request.
    """

    issuer: str
    audience: str
    key_source: SigningKeySource
    algorithms: tuple[str, ...] = _ALLOWED_ALGORITHMS
    leeway_seconds: int = 60

    def __post_init__(self) -> None:
        _require_https_url(self.issuer, field_name="issuer")
        if self.issuer.endswith("/"):
            raise OidcConfigurationError("issuer_must_not_have_trailing_slash")
        if not self.audience.strip():
            raise OidcConfigurationError("audience_required")
        if not self.algorithms or any(
            algorithm not in _ALLOWED_ALGORITHMS for algorithm in self.algorithms
        ):
            raise OidcConfigurationError("unsupported_signing_algorithm_configured")
        if self.leeway_seconds < 0:
            raise OidcConfigurationError("leeway_seconds_must_be_non_negative")

    @classmethod
    def from_jwks_url(
        cls,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: tuple[str, ...] = _ALLOWED_ALGORITHMS,
        leeway_seconds: int = 60,
    ) -> JwksExternalTokenVerifier:
        _require_https_url(jwks_url, field_name="jwks_url")
        return cls(
            issuer=issuer,
            audience=audience,
            key_source=jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300),
            algorithms=algorithms,
            leeway_seconds=leeway_seconds,
        )

    def verify_token(self, token: str) -> VerifiedExternalSession:
        try:
            signing_key = self.key_source.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.algorithms),
                issuer=self.issuer,
                audience=self.audience,
                leeway=self.leeway_seconds,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as error:
            raise OidcVerificationError("jwt_verification_failed") from error
        except Exception as error:
            raise OidcVerificationError("jwt_signing_key_resolution_failed") from error

        if not isinstance(claims, dict):
            raise OidcVerificationError("jwt_claims_malformed")
        return _session_from_claims(claims)


def _session_from_claims(claims: dict[str, Any]) -> VerifiedExternalSession:
    subject = claims.get("sub")
    session_id = claims.get("sid") or claims.get("jti")
    groups_claim = claims.get("groups", claims.get("roles", []))
    amr_claim = claims.get("amr", [])
    audience_claim = claims.get("aud")

    if not isinstance(subject, str) or not subject.strip():
        raise OidcVerificationError("jwt_subject_missing")
    if not isinstance(session_id, str) or not session_id.strip():
        raise OidcVerificationError("jwt_session_identifier_missing")
    if not isinstance(groups_claim, list) or not all(isinstance(item, str) for item in groups_claim):
        raise OidcVerificationError("jwt_groups_claim_malformed")
    if not isinstance(amr_claim, list) or not all(isinstance(item, str) for item in amr_claim):
        raise OidcVerificationError("jwt_amr_claim_malformed")
    if isinstance(audience_claim, str):
        audiences = frozenset({audience_claim})
    elif isinstance(audience_claim, list) and all(isinstance(item, str) for item in audience_claim):
        audiences = frozenset(audience_claim)
    else:
        raise OidcVerificationError("jwt_audience_claim_malformed")

    issuer = claims.get("iss")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    if not isinstance(issuer, str) or not issuer.strip():
        raise OidcVerificationError("jwt_issuer_missing")
    if not isinstance(issued_at, (int, float)) or not isinstance(expires_at, (int, float)):
        raise OidcVerificationError("jwt_time_claims_malformed")

    return VerifiedExternalSession(
        issuer=issuer,
        subject=subject.strip(),
        audiences=audiences,
        issued_at=datetime.fromtimestamp(float(issued_at), tz=UTC),
        expires_at=datetime.fromtimestamp(float(expires_at), tz=UTC),
        session_id=session_id.strip(),
        groups=frozenset(groups_claim),
        authentication_methods=frozenset(amr_claim),
    )
