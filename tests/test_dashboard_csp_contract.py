import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DashboardCspContractTests(unittest.TestCase):
    def test_static_headers_cannot_restore_unsafe_inline_csp(self) -> None:
        config = (ROOT / "web" / "next.config.ts").read_text(encoding="utf-8")
        self.assertNotIn("Content-Security-Policy", config)
        self.assertNotIn("unsafe-inline", config)

    def test_proxy_generates_propagates_and_returns_one_request_nonce(self) -> None:
        proxy = (ROOT / "web" / "proxy.ts").read_text(encoding="utf-8")
        for required in (
            "crypto.randomUUID()",
            'requestHeaders.set("x-nonce", nonce)',
            'requestHeaders.set("Content-Security-Policy", policy)',
            'response.headers.set("Content-Security-Policy", policy)',
            "'strict-dynamic'",
            "style-src 'self' 'nonce-${nonce}'",
        ):
            self.assertIn(required, proxy)
        self.assertNotIn("unsafe-inline", proxy)

    def test_browser_contract_checks_script_binding_and_nonce_rotation(self) -> None:
        e2e = (ROOT / "web" / "e2e" / "cycle208.spec.ts").read_text(
            encoding="utf-8"
        )
        for required in (
            "scriptNonces.every((value) => value === nonce)",
            "secondNonce).not.toBe(nonce)",
            "policy).not.toContain(\"'unsafe-inline'\")",
        ):
            self.assertIn(required, e2e)


if __name__ == "__main__":
    unittest.main()
