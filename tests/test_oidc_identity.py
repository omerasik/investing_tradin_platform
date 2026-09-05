import time
import unittest
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from trade_platform.oidc_identity import (
    JwksExternalTokenVerifier,
    OidcConfigurationError,
    OidcVerificationError,
)

_ISSUER = "https://identity.example.test/tenant"
_AUDIENCE = "trade-platform"


@dataclass
class _FakeSigningKey:
    key: Any


class _FakeKeySource:
    """Stands in for ``jwt.PyJWKClient`` -- returns a fixture key, no network fetch."""

    def __init__(self, key: Any) -> None:
        self._key = key
        self.calls = 0

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        self.calls += 1
        return _FakeSigningKey(self._key)


def _rsa_keypair() -> tuple[Any, Any]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _encode(private_key: Any, claims: dict[str, Any], *, algorithm: str = "RS256") -> str:
    return jwt.encode(claims, private_key, algorithm=algorithm, headers={"kid": "test-key"})


def _base_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": "operator-123",
        "sid": "session-abc",
        "iat": now,
        "exp": now + 300,
        "groups": ["trade-risk-reviewers"],
        "amr": ["password", "mfa"],
    }
    claims.update(overrides)
    return claims


class JwksExternalTokenVerifierConfigurationTests(unittest.TestCase):
    def test_rejects_non_https_issuer(self) -> None:
        with self.assertRaises(OidcConfigurationError):
            JwksExternalTokenVerifier(
                issuer="http://identity.example.test",
                audience=_AUDIENCE,
                key_source=_FakeKeySource(None),
            )

    def test_rejects_trailing_slash_issuer(self) -> None:
        with self.assertRaises(OidcConfigurationError):
            JwksExternalTokenVerifier(
                issuer=_ISSUER + "/", audience=_AUDIENCE, key_source=_FakeKeySource(None)
            )

    def test_rejects_empty_audience(self) -> None:
        with self.assertRaises(OidcConfigurationError):
            JwksExternalTokenVerifier(issuer=_ISSUER, audience=" ", key_source=_FakeKeySource(None))

    def test_rejects_unsupported_algorithm(self) -> None:
        with self.assertRaises(OidcConfigurationError):
            JwksExternalTokenVerifier(
                issuer=_ISSUER,
                audience=_AUDIENCE,
                key_source=_FakeKeySource(None),
                algorithms=("HS256",),
            )

    def test_from_jwks_url_rejects_non_https_jwks_url(self) -> None:
        with self.assertRaises(OidcConfigurationError):
            JwksExternalTokenVerifier.from_jwks_url(
                issuer=_ISSUER, audience=_AUDIENCE, jwks_url="http://identity.example.test/jwks.json"
            )


class JwksExternalTokenVerifierVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key, self.public_key = _rsa_keypair()
        self.key_source = _FakeKeySource(self.public_key)
        self.verifier = JwksExternalTokenVerifier(
            issuer=_ISSUER, audience=_AUDIENCE, key_source=self.key_source
        )

    def test_valid_token_maps_to_verified_session(self) -> None:
        token = _encode(self.private_key, _base_claims())
        session = self.verifier.verify_token(token)
        self.assertEqual(session.subject, "operator-123")
        self.assertEqual(session.session_id, "session-abc")
        self.assertEqual(session.groups, frozenset({"trade-risk-reviewers"}))
        self.assertEqual(session.authentication_methods, frozenset({"password", "mfa"}))
        self.assertEqual(session.audiences, frozenset({_AUDIENCE}))
        self.assertEqual(self.key_source.calls, 1)

    def test_wrong_signing_key_is_rejected(self) -> None:
        _, other_public_key = _rsa_keypair()
        token = _encode(self.private_key, _base_claims())
        verifier = JwksExternalTokenVerifier(
            issuer=_ISSUER, audience=_AUDIENCE, key_source=_FakeKeySource(other_public_key)
        )
        with self.assertRaises(OidcVerificationError):
            verifier.verify_token(token)

    def test_expired_token_is_rejected(self) -> None:
        now = int(time.time())
        token = _encode(self.private_key, _base_claims(iat=now - 1000, exp=now - 500))
        with self.assertRaises(OidcVerificationError):
            self.verifier.verify_token(token)

    def test_wrong_audience_is_rejected(self) -> None:
        token = _encode(self.private_key, _base_claims(aud="some-other-service"))
        with self.assertRaises(OidcVerificationError):
            self.verifier.verify_token(token)

    def test_wrong_issuer_is_rejected(self) -> None:
        token = _encode(self.private_key, _base_claims(iss="https://attacker.example.test"))
        with self.assertRaises(OidcVerificationError):
            self.verifier.verify_token(token)

    def test_none_algorithm_is_rejected(self) -> None:
        token = jwt.encode(_base_claims(), key=None, algorithm="none")
        with self.assertRaises(OidcVerificationError):
            self.verifier.verify_token(token)

    def test_missing_subject_is_rejected(self) -> None:
        claims = _base_claims()
        del claims["sub"]
        token = _encode(self.private_key, claims)
        with self.assertRaises(OidcVerificationError):
            self.verifier.verify_token(token)

    def test_missing_session_identifier_is_rejected(self) -> None:
        claims = _base_claims()
        del claims["sid"]
        token = _encode(self.private_key, claims)
        with self.assertRaises(OidcVerificationError):
            self.verifier.verify_token(token)

    def test_jti_is_accepted_as_session_identifier_fallback(self) -> None:
        claims = _base_claims()
        del claims["sid"]
        claims["jti"] = "fallback-session-id"
        token = _encode(self.private_key, claims)
        session = self.verifier.verify_token(token)
        self.assertEqual(session.session_id, "fallback-session-id")

    def test_malformed_groups_claim_is_rejected(self) -> None:
        token = _encode(self.private_key, _base_claims(groups="not-a-list"))
        with self.assertRaises(OidcVerificationError):
            self.verifier.verify_token(token)

    def test_string_audience_claim_is_accepted(self) -> None:
        token = _encode(self.private_key, _base_claims(aud=_AUDIENCE))
        session = self.verifier.verify_token(token)
        self.assertEqual(session.audiences, frozenset({_AUDIENCE}))


if __name__ == "__main__":
    unittest.main()
