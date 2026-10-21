"""Fail-closed deployment smoke configuration and credential-safe output."""
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts.smoke_terminal_runtime import SmokeRefused, configuration, main, probe


class TerminalRuntimeSmokeTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "TRADE_PLATFORM_ENVIRONMENT": "paper",
            "POSTGRES_DSN": "fixture-dsn-not-connected",
            "TRADE_PLATFORM_OPERATOR_TOKEN": "fixture-smoke-token",
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_local_and_production_modes_are_refused_before_any_import_or_connection(self):
        for environment in ("", "local_research", "production"):
            with self.subTest(environment=environment), patch.dict(
                os.environ, {"TRADE_PLATFORM_ENVIRONMENT": environment}
            ), self.assertRaisesRegex(SmokeRefused, "EXPLICIT_PAPER"):
                configuration("thin", require_proven_price=False)

    def test_missing_database_or_token_is_refused(self):
        for name in ("POSTGRES_DSN", "TRADE_PLATFORM_OPERATOR_TOKEN"):
            with self.subTest(name=name), patch.dict(os.environ, {name: " "}), \
                    self.assertRaisesRegex(SmokeRefused, name):
                configuration("thin", require_proven_price=False)

    def test_thin_does_not_import_analytical_packages_or_claim_price_readiness(self):
        with patch("scripts.smoke_terminal_runtime.importlib.util.find_spec", return_value=None), \
                patch("scripts.smoke_terminal_runtime.importlib.import_module") as load:
            result = configuration("thin", require_proven_price=False)
        load.assert_not_called()
        self.assertEqual(result["price_policy"], "NOT_CONFIGURED")
        self.assertFalse(any(result["analytical_modules_present"].values()))

    def test_missing_analytical_import_fails_instead_of_silent_thin_fallback(self):
        with patch("scripts.smoke_terminal_runtime.importlib.import_module",
                   side_effect=ImportError("fixture missing")), \
                self.assertRaisesRegex(SmokeRefused, "ANALYTICAL_IMPORT_UNAVAILABLE"):
            configuration("analytical", require_proven_price=False)

    def test_proven_price_requirement_cannot_pass_without_pinned_policy(self):
        with self.assertRaisesRegex(SmokeRefused, "PINNED_POLICY"):
            configuration("thin", require_proven_price=True)

    def test_partial_or_wrong_policy_pin_refuses_configuration(self):
        with patch.dict(os.environ, {"TRADE_PLATFORM_TERMINAL_PRICE_SELECTION_HASH": "0" * 64}), \
                self.assertRaisesRegex(ValueError, "PRICE_SELECTION_PATH_AND_HASH_REQUIRED"):
            configuration("thin", require_proven_price=False)

    def test_third_party_failure_output_does_not_disclose_credentials(self):
        output = io.StringIO()
        with patch("sys.argv", ["smoke", "--profile", "thin"]), \
                patch("scripts.smoke_terminal_runtime.configuration",
                      side_effect=RuntimeError("fixture-private-token-and-dsn")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "FAIL", "reason": "RuntimeError"})

    def test_proven_requirement_refuses_successful_read_with_no_stored_mark(self):
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse

        from tests.test_research_terminal_read_model_v1 import CYCLE, NOW, _paper

        study, trial = uuid4(), uuid4()
        app = FastAPI()

        @app.get("/health/live")
        def live():
            return {"status": "live"}

        @app.get("/health/ready")
        def ready():
            return {"status": "ready", "environment": "paper",
                    "paper_trading_enabled": True, "live_trading_enabled": False}

        @app.get("/operator-dashboard/research-terminal/overview")
        def overview(request: Request):
            if not request.headers.get("Authorization"):
                return JSONResponse({}, status_code=401)
            return {"generated_at": NOW.isoformat(), "studies": 0, "finished_studies": 0,
                    "reruns": {}, "cycle": CYCLE.model_dump(mode="json"), "candidate_states": {},
                    "signals": {}, "latest_signal_at": None, "incubation_fills": {},
                    "accounts": {}, "owner_gates": [], "authority_legend": {}}

        @app.get("/operator-dashboard/research-terminal/candidates/{study_id}/{trial_id}/paper-evidence")
        def candidate(study_id: str, trial_id: str):
            return _paper(study, trial).model_dump(mode="json")

        with self.assertRaisesRegex(SmokeRefused, "PROVEN_PRICE_EVIDENCE_UNAVAILABLE"):
            probe(app, token="fixture-token", study_id=study, trial_id=trial,
                  require_proven_price=True)


if __name__ == "__main__":
    unittest.main()
