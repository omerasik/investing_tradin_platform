import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trade_platform.csrf import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    CsrfProtectionMiddleware,
    derive_csrf_token,
)

_CSRF_SECRET = "unit-test-csrf-secret"  # pragma: allowlist secret


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CsrfProtectionMiddleware, csrf_secret=_CSRF_SECRET)

    @app.get("/read")
    def read() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/mutate")
    def mutate() -> dict[str, bool]:
        return {"ok": True}

    return app


class CsrfProtectionMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_build_app())

    def test_bearer_only_requests_are_never_affected(self) -> None:
        response = self.client.post(
            "/mutate", headers={"Authorization": "Bearer some-token"}
        )
        self.assertEqual(response.status_code, 200)

    def test_get_requests_are_never_affected(self) -> None:
        self.client.cookies.set(SESSION_COOKIE_NAME, "session-id")
        response = self.client.get("/read")
        self.assertEqual(response.status_code, 200)

    def test_cookie_mutation_without_csrf_header_is_rejected(self) -> None:
        self.client.cookies.set(SESSION_COOKIE_NAME, "session-id")
        response = self.client.post("/mutate")
        self.assertEqual(response.status_code, 403)

    def test_cookie_mutation_with_wrong_csrf_header_is_rejected(self) -> None:
        self.client.cookies.set(SESSION_COOKIE_NAME, "session-id")
        response = self.client.post("/mutate", headers={CSRF_HEADER_NAME: "wrong-token"})
        self.assertEqual(response.status_code, 403)

    def test_cookie_mutation_with_correct_csrf_header_is_allowed(self) -> None:
        self.client.cookies.set(SESSION_COOKIE_NAME, "session-id")
        token = derive_csrf_token(csrf_secret=_CSRF_SECRET, session_id="session-id")
        response = self.client.post("/mutate", headers={CSRF_HEADER_NAME: token})
        self.assertEqual(response.status_code, 200)

    def test_token_bound_to_session_id_is_rejected_for_a_different_session(self) -> None:
        self.client.cookies.set(SESSION_COOKIE_NAME, "session-id")
        token = derive_csrf_token(csrf_secret=_CSRF_SECRET, session_id="a-different-session")
        response = self.client.post("/mutate", headers={CSRF_HEADER_NAME: token})
        self.assertEqual(response.status_code, 403)

    def test_derive_csrf_token_requires_secret_and_session_id(self) -> None:
        with self.assertRaises(ValueError):
            derive_csrf_token(csrf_secret="", session_id="session-id")
        with self.assertRaises(ValueError):
            derive_csrf_token(csrf_secret=_CSRF_SECRET, session_id="")

    def test_middleware_requires_csrf_secret(self) -> None:
        app = FastAPI()
        with self.assertRaises(ValueError):
            app.add_middleware(CsrfProtectionMiddleware, csrf_secret="")
            TestClient(app).get("/")


if __name__ == "__main__":
    unittest.main()
