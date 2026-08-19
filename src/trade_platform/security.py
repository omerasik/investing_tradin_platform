"""Local deployment security boundaries; secrets are supplied only through the environment."""

import hmac
import os
import time
from collections import deque
from dataclasses import dataclass, field

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


@dataclass(frozen=True, slots=True)
class OperatorAuthenticator:
    token: str | None
    subject: str = "local-operator"

    @classmethod
    def from_environment(cls) -> "OperatorAuthenticator":
        return cls(os.getenv("TRADE_PLATFORM_OPERATOR_TOKEN"), os.getenv("TRADE_PLATFORM_OPERATOR_SUBJECT", "local-operator"))

    def verify(self, authorization: str | None) -> str:
        if not self.token:
            raise AuthenticationUnavailableError("Operator authentication is not configured.")
        expected = f"Bearer {self.token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise PermissionError("Invalid operator credentials.")
        if not self.subject.strip():
            raise AuthenticationUnavailableError("Operator subject is not configured.")
        return self.subject


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


def protected_operator(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    authenticator: OperatorAuthenticator = request.app.state.authenticator
    limiter: InMemoryRateLimiter = request.app.state.rate_limiter
    try:
        subject = authenticator.verify(authorization)
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
    return subject
