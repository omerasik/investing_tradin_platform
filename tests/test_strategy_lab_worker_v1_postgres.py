"""Phase R4.2 -- Strategy Lab trial workers against real PostgreSQL.

Synthetic studies and evaluators only (T0 bindings, random implementation hash
per study, past clock readings); no REJECTED row or feature definition is
written.
"""

from __future__ import annotations

import os
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.test_strategy_lab_ledger_v1_postgres import _Clock, _study


def square_evaluator(study, parameters: Mapping[str, int | Decimal | str]):  # type: ignore[no-untyped-def]
    """Deterministic, picklable, exact: metrics are canonical text and ints."""
    from trade_platform.strategy_lab_ledger_v1 import TrialOutcomeV1
    from trade_platform.strategy_lab_worker_v1 import TrialEvaluationV1

    window = int(parameters["window"])
    if window % 7 == 0:
        return TrialEvaluationV1(TrialOutcomeV1.INADMISSIBLE_PARAMETERS, {})
    return TrialEvaluationV1(TrialOutcomeV1.EVALUATED, {"score": str(Decimal(window) / 8), "trades": window * window})


def flaky_evaluator(study, parameters):  # type: ignore[no-untyped-def]
    if int(parameters["window"]) == 2:
        raise ZeroDivisionError("synthetic")
    if int(parameters["window"]) == 3:
        from trade_platform.strategy_lab_ledger_v1 import TrialOutcomeV1
        from trade_platform.strategy_lab_worker_v1 import TrialEvaluationV1

        return TrialEvaluationV1(TrialOutcomeV1.EVALUATED, {"score": 0.5})  # a float: refused
    return square_evaluator(study, parameters)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class StrategyLabWorkerPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def setUp(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1

        self.clock = _Clock(datetime(2026, 9, 2, 12, tzinfo=UTC))
        self.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.ledger = PostgresStrategyLabLedgerV1(self.database, clock=self.clock)

    def tearDown(self) -> None:
        self.database.close()

    def test_worker_drains_a_study_and_a_rerun_is_a_noop(self) -> None:
        from trade_platform.strategy_lab_ledger_v1 import result_content_hash_v1
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1

        spec = _study(10)
        report = run_trial_worker_v1(self.ledger, spec, square_evaluator, worker="solo", batch_size=3,
                                     clock=self.clock)
        self.assertEqual((report.claimed, report.succeeded, report.failed, report.stopped), (10, 10, 0, False))
        results = self.ledger.results(spec.study_id)
        self.assertEqual([r.outcome.value for r in results].count("INADMISSIBLE_PARAMETERS"), 1)
        trials = {t.trial_id: t for t in spec.trials()}
        for result in results:
            evaluation = square_evaluator(spec, spec.parameter_space.typed_point(trials[result.trial_id].parameters))
            self.assertEqual(result.result_content_hash, result_content_hash_v1(
                trial_content_hash=trials[result.trial_id].content_hash, outcome=evaluation.outcome,
                metrics=evaluation.metrics))
        again = run_trial_worker_v1(self.ledger, spec, square_evaluator, worker="solo", clock=self.clock)
        self.assertEqual(again.claimed, 0)
        self.assertTrue(self.ledger.progress(spec.study_id).finished)

    def test_evaluator_failures_are_recorded_not_swallowed(self) -> None:
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1

        spec = _study(4)
        report = run_trial_worker_v1(self.ledger, spec, flaky_evaluator, worker="flaky", clock=self.clock)
        self.assertEqual((report.succeeded, report.failed), (2, 2))
        self.assertEqual(self.ledger.progress(spec.study_id).states, {"FAILED": 2, "SUCCEEDED": 2})
        reasons = {}
        for trial in spec.trials():
            for attempt, kind, _ in self.ledger.events(trial.trial_id):
                if kind == "FAILED":
                    reasons[trial.parameters["window"]] = attempt
        self.assertEqual(sorted(reasons), ["2", "3"])
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT detail->>'reason' FROM strategy_lab_trial_events WHERE study_id=%s AND event_type='FAILED' "
                "ORDER BY detail->>'reason'", (spec.study_id,))
            self.assertEqual([row[0] for row in cursor.fetchall()],
                             ["evaluator_raised:zerodivisionerror", "evaluator_returned_invalid_metrics"])
        self.ledger.retry_failed(spec.study_id, reason="fixed_evaluator")
        run_trial_worker_v1(self.ledger, spec, square_evaluator, worker="fixed", clock=self.clock)
        self.assertEqual(self.ledger.progress(spec.study_id).states, {"SUCCEEDED": 4})

    def test_stop_then_resume_after_lease_expiry_gives_exactly_one_result_per_trial(self) -> None:
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1

        spec = _study(9)
        done = {"n": 0}

        def stop_after_two() -> bool:
            return done["n"] >= 2

        def counting(study, parameters):  # type: ignore[no-untyped-def]
            done["n"] += 1
            return square_evaluator(study, parameters)

        first = run_trial_worker_v1(self.ledger, spec, counting, worker="stopper", batch_size=4,
                                    lease_seconds=60, should_stop=stop_after_two, clock=self.clock)
        self.assertTrue(first.stopped)
        self.assertEqual((first.claimed, first.succeeded), (4, 2))
        self.assertEqual(self.ledger.progress(spec.study_id).states, {"CLAIMED": 2, "PENDING": 5, "SUCCEEDED": 2})
        self.clock.now += timedelta(seconds=61)
        second = run_trial_worker_v1(self.ledger, spec, square_evaluator, worker="resumer", clock=self.clock)
        self.assertEqual(second.succeeded, 7)
        results = self.ledger.results(spec.study_id)
        self.assertEqual(len(results), 9)
        self.assertEqual(len({r.trial_id for r in results}), 9)

    def test_process_pool_completes_every_trial_once(self) -> None:
        from trade_platform.strategy_lab_worker_v1 import run_study_pool_v1

        spec = _study(40)
        reports = run_study_pool_v1(os.environ["POSTGRES_TEST_DSN"], spec, square_evaluator, workers=2,
                                    batch_size=5, worker_prefix="test-pool")
        self.assertEqual(sum(r.succeeded for r in reports), 40)
        self.assertEqual(sum(r.failed + r.lost_leases for r in reports), 0)
        results = self.ledger.results(spec.study_id)
        self.assertEqual(len({r.trial_id for r in results}), 40)
        self.assertEqual({r.numeric_tier for r in results}, {"SEARCH_NON_AUTHORITATIVE"})

    def test_pool_size_is_bounded(self) -> None:
        from trade_platform.strategy_lab_worker_v1 import run_study_pool_v1

        for bad in (0, (os.cpu_count() or 1) + 1):
            with self.subTest(workers=bad), self.assertRaises(ValueError):
                run_study_pool_v1(os.environ["POSTGRES_TEST_DSN"], _study(1), square_evaluator, workers=bad)


if __name__ == "__main__":
    unittest.main()
