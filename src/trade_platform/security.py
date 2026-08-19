"""Local deployment security boundaries; secrets are supplied only through the environment."""

import hmac
import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from fastapi import Header, HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

API_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'; object-src 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(), payment=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply one fail-closed header policy to success and error responses."""

    def __init__(self, app: ASGIApp, *, production: bool) -> None:
        super().__init__(app)
        self._production = production

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in API_SECURITY_HEADERS.items():
            response.headers[name] = value
        if self._production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


class AuthenticationUnavailableError(RuntimeError):
    """Raised when a mutating/operational endpoint has no configured operator credential."""


class OperatorRole(StrEnum):
    """Deployment-assigned role for the temporary bearer-token identity boundary."""

    VIEWER = "viewer"
    RESEARCHER = "researcher"
    DATA_STEWARD = "data_steward"
    RISK_REVIEWER = "risk_reviewer"
    AUDITOR = "auditor"
    OPERATOR = "operator"


class OperatorPermission(StrEnum):
    READ_EVIDENCE = "read_evidence"
    RUN_RESEARCH = "run_research"
    MANAGE_DATA = "manage_data"
    REVIEW_RISK = "review_risk"
    ACKNOWLEDGE_ALERT = "acknowledge_alert"
    WRITE_AUDIT = "write_audit"


ROLE_PERMISSIONS: dict[OperatorRole, frozenset[OperatorPermission]] = {
    OperatorRole.VIEWER: frozenset({OperatorPermission.READ_EVIDENCE}),
    OperatorRole.RESEARCHER: frozenset(
        {OperatorPermission.READ_EVIDENCE, OperatorPermission.RUN_RESEARCH}
    ),
    OperatorRole.DATA_STEWARD: frozenset(
        {OperatorPermission.READ_EVIDENCE, OperatorPermission.MANAGE_DATA}
    ),
    OperatorRole.RISK_REVIEWER: frozenset(
        {
            OperatorPermission.READ_EVIDENCE,
            OperatorPermission.REVIEW_RISK,
            OperatorPermission.ACKNOWLEDGE_ALERT,
        }
    ),
    OperatorRole.AUDITOR: frozenset(
        {OperatorPermission.READ_EVIDENCE, OperatorPermission.WRITE_AUDIT}
    ),
    OperatorRole.OPERATOR: frozenset(OperatorPermission),
}


@dataclass(frozen=True, slots=True)
class OperatorPrincipal:
    subject: str
    role: OperatorRole


@dataclass(frozen=True, slots=True)
class OperatorAuthenticator:
    token: str | None
    subject: str = "local-operator"
    role: OperatorRole | None = OperatorRole.OPERATOR

    @classmethod
    def from_environment(cls) -> "OperatorAuthenticator":
        role_value = os.getenv("TRADE_PLATFORM_OPERATOR_ROLE", OperatorRole.VIEWER.value)
        try:
            role = OperatorRole(role_value)
        except ValueError:
            role = None
        return cls(
            os.getenv("TRADE_PLATFORM_OPERATOR_TOKEN"),
            os.getenv("TRADE_PLATFORM_OPERATOR_SUBJECT", "local-operator"),
            role,
        )

    def verify(self, authorization: str | None) -> OperatorPrincipal:
        if not self.token:
            raise AuthenticationUnavailableError("Operator authentication is not configured.")
        expected = f"Bearer {self.token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise PermissionError("Invalid operator credentials.")
        if not self.subject.strip():
            raise AuthenticationUnavailableError("Operator subject is not configured.")
        if not isinstance(self.role, OperatorRole):
            raise AuthenticationUnavailableError("Operator role is not configured.")
        return OperatorPrincipal(self.subject.strip(), self.role)


@dataclass(slots=True)
class InMemoryRateLimiter:
    max_requests: int = 60
    window_seconds: float = 60.0
    _requests: dict[str, deque[float]] = field(default_factory=dict)

    def check(self, key: str) -> None:
        now = time.monotonic()
        events = self._requests.setdefault(key, deque())
        while events and events[0] <= now - self.window_seconds:
            events.popleft()
        if len(events) >= self.max_requests:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded.")
        events.append(now)


def _authenticated_principal(
    request: Request,
    authorization: str | None,
) -> OperatorPrincipal:
    authenticator: OperatorAuthenticator = request.app.state.authenticator
    limiter: InMemoryRateLimiter = request.app.state.rate_limiter
    try:
        principal = authenticator.verify(authorization)
    except AuthenticationUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    client = request.client.host if request.client else "unknown"
    limiter.check(client)
    return principal


@dataclass(frozen=True, slots=True)
class RequireOperatorPermission:
    """FastAPI dependency that authenticates and enforces one explicit permission."""

    permission: OperatorPermission

    def __call__(
        self,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> str:
        principal = _authenticated_principal(request, authorization)
        if self.permission not in ROLE_PERMISSIONS[principal.role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden.",
            )
        return principal.subject


protected_operator = RequireOperatorPermission(OperatorPermission.READ_EVIDENCE)
research_operator = RequireOperatorPermission(OperatorPermission.RUN_RESEARCH)
data_steward_operator = RequireOperatorPermission(OperatorPermission.MANAGE_DATA)
risk_reviewer_operator = RequireOperatorPermission(OperatorPermission.REVIEW_RISK)
alert_reviewer_operator = RequireOperatorPermission(OperatorPermission.ACKNOWLEDGE_ALERT)
audit_writer_operator = RequireOperatorPermission(OperatorPermission.WRITE_AUDIT)
