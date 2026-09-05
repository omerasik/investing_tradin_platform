"""Signed double-submit-cookie CSRF protection for browser mutation paths.

Bearer-token API calls -- the only authentication path any route or test in this
codebase exercises today, see :mod:`trade_platform.security` -- are immune to CSRF: a
browser never attaches an ``Authorization`` header automatically, so a forged
cross-site request cannot present one. CSRF only becomes a real risk once a request
carries an *ambient* credential a browser attaches automatically -- a cookie. This
middleware activates only when it observes the first-party operator-session cookie
(:data:`SESSION_COOKIE_NAME`) on a state-changing request; a pure bearer-token request
-- the shape every existing route and test uses -- never carries that cookie and is
left completely untouched by this middleware.

The token itself is never stored server-side: it is an HMAC of the session id under a
deployment secret, so any party who can read the session cookie's raw session id but
does not hold the CSRF secret cannot forge a valid token, and the server needs no
session-keyed CSRF table to validate one.
"""

from __future__ import annotations

import hashlib
import hmac

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

__all__ = [
    "CSRF_HEADER_NAME",
    "SESSION_COOKIE_NAME",
    "CsrfProtectionMiddleware",
    "derive_csrf_token",
]

SESSION_COOKIE_NAME = "trade_platform_session"
CSRF_HEADER_NAME = "x-csrf-token"
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def derive_csrf_token(*, csrf_secret: str, session_id: str) -> str:
    """Deterministic, non-reversible token bound to one session id."""
    if not csrf_secret or not session_id:
        raise ValueError("csrf_secret_and_session_id_required")
    return hmac.new(csrf_secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()


class CsrfProtectionMiddleware(BaseHTTPMiddleware):
    """Rejects a cookie-authenticated mutation unless a matching CSRF header is present."""

    def __init__(self, app: ASGIApp, *, csrf_secret: str) -> None:
        super().__init__(app)
        if not csrf_secret:
            raise ValueError("csrf_secret_required")
        self._csrf_secret = csrf_secret

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in _MUTATING_METHODS:
            session_id = request.cookies.get(SESSION_COOKIE_NAME)
            if session_id:
                expected = derive_csrf_token(csrf_secret=self._csrf_secret, session_id=session_id)
                presented = request.headers.get(CSRF_HEADER_NAME, "")
                if not presented or not hmac.compare_digest(presented, expected):
                    return JSONResponse(
                        {"detail": "CSRF token missing or invalid."}, status_code=403
                    )
        return await call_next(request)
