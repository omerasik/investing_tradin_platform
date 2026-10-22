"""Fail-closed deployment smoke configuration and credential-safe output."""
import contextlib
import io
import json
import os
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

from scripts.smoke_terminal_runtime import (
    SmokeRefused,
    configuration,
    describe_clock_sample,
    main,
    probe,
    sample_postgres_clock,
)


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

    def test_clock_sample_reports_bracket_uncertainty_and_observed_relation(self):
        before = datetime(2026, 10, 22, tzinfo=UTC)
        after = before + timedelta(microseconds=200)
        for offset, expected, bounds in (
            (300, "DB_AHEAD_OF_APP_BRACKET", [100, 300]),
            (-100, "DB_BEHIND_APP_BRACKET", [-300, -100]),
            (100, "OVERLAPS_APP_BRACKET", [-100, 100]),
        ):
            with self.subTest(offset=offset):
                result = describe_clock_sample(before, before + timedelta(microseconds=offset), after)
                self.assertEqual(result["app_bracket_width_us"], 200)
                self.assertEqual(result["db_minus_app_interval_us"], bounds)
                self.assertEqual(result["observed_relation"], expected)
                self.assertEqual(result["scope"], "ONE_READ_ONLY_SAMPLE_NOT_CONTINUOUS_CLOCK_HEALTH")
        shifted = before.astimezone(timezone(timedelta(hours=2)))
        self.assertEqual(describe_clock_sample(shifted, before, after)["app_before_utc"],
                         before.isoformat())

    def test_clock_sample_refuses_invalid_time_evidence(self):
        now = datetime(2026, 10, 22, tzinfo=UTC)
        with self.assertRaisesRegex(SmokeRefused, "CLOCK_SAMPLE_REQUIRES_AWARE_TIMES"):
            describe_clock_sample(now, now.replace(tzinfo=None), now)
        with self.assertRaisesRegex(SmokeRefused, "CLOCK_SAMPLE_APP_TIME_REVERSED"):
            describe_clock_sample(now, now, now - timedelta(microseconds=1))

    def test_clock_query_uses_existing_app_connection_in_read_only_transaction(self):
        before = datetime(2026, 10, 22, tzinfo=UTC)
        cursor = Mock()
        cursor.fetchone.return_value = (before + timedelta(microseconds=80),)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        transaction_exited = False

        @contextmanager
        def transaction():
            nonlocal transaction_exited
            yield connection
            transaction_exited = True

        def application_now():
            if cursor.execute.call_count == 1:
                cursor.fetchone.assert_not_called()
                return before
            self.assertEqual(cursor.execute.call_count, 2)
            cursor.fetchone.assert_called_once_with()
            self.assertFalse(transaction_exited)
            return before + timedelta(microseconds=100)

        database = SimpleNamespace(transaction=Mock(side_effect=transaction))
        app = SimpleNamespace(state=SimpleNamespace(
            postgres_authorities=SimpleNamespace(database=database)))
        with patch("scripts.smoke_terminal_runtime._application_now", side_effect=application_now) as now:
            result = sample_postgres_clock(app)
        self.assertEqual(now.call_count, 2)
        self.assertTrue(transaction_exited)
        self.assertEqual(result["db_minus_app_interval_us"], [-20, 80])
        self.assertEqual(cursor.execute.call_args_list[0].args, ("SET TRANSACTION READ ONLY",))
        self.assertEqual(cursor.execute.call_args_list[1].args, ("SELECT clock_timestamp()",))
        self.assertEqual(cursor.execute.call_count, 2)
        database.transaction.assert_called_once_with()

    def test_clock_query_refuses_missing_canonical_source(self):
        app = SimpleNamespace(state=SimpleNamespace())
        with self.assertRaisesRegex(SmokeRefused, "POSTGRES_CLOCK_SOURCE_UNAVAILABLE"):
            sample_postgres_clock(app)

    def test_clock_query_refuses_invalid_result_shape(self):
        cursor = Mock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor

        @contextmanager
        def transaction():
            yield connection

        app = SimpleNamespace(state=SimpleNamespace(
            postgres_authorities=SimpleNamespace(
                database=SimpleNamespace(transaction=transaction))))
        for row in (None, (), ("invalid",), (datetime(2026, 10, 22, tzinfo=UTC), None)):
            with self.subTest(row=row):
                cursor.fetchone.return_value = row
                with self.assertRaisesRegex(SmokeRefused, "POSTGRES_CLOCK_SAMPLE_UNAVAILABLE"):
                    sample_postgres_clock(app)

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
        with patch("scripts.smoke_terminal_runtime.sample_postgres_clock",
                   return_value={"observed_relation": "OVERLAPS_APP_BRACKET"}) as sample:
            result = probe(app, token="fixture-token", study_id=study, trial_id=trial,
                           require_proven_price=False)
        sample.assert_called_once_with(app)
        self.assertEqual(result["authenticated_evidence_read"], "PASS")
        self.assertEqual(result["application_postgres_clock"]["observed_relation"],
                         "OVERLAPS_APP_BRACKET")
        with patch("scripts.smoke_terminal_runtime.sample_postgres_clock",
                   return_value={"observed_relation": "OVERLAPS_APP_BRACKET"}) as sample:
            default_result = probe(app, token="fixture-token", study_id=None, trial_id=None,
                                   require_proven_price=False)
        sample.assert_called_once_with(app)
        self.assertEqual(default_result["proven_price"], "NOT_ASSESSED")


if __name__ == "__main__":
    unittest.main()
