"""Stage C.1.1 -- walk-forward reports against real PostgreSQL (migration 0063).

Fixture archive days only (synthetic bytes, no network). The study and report
identities are deterministic, so a rerun against a shared database is an
idempotent no-op; no REJECTED row or feature definition is written.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class WalkForwardPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="walk-forward-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_report_is_recorded_once_immutable_and_checked_as_development_evidence(self) -> None:
        import psycopg

        from tests.test_strategy_lab_e2e_fixture import build_window_and_study
        from tests.test_strategy_lab_walk_forward_v1 import RULE, SPACE, _plan
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_walk_forward_v1 import (
            PostgresWalkForwardStoreV1,
            run_walk_forward_v1,
        )
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1
        from trade_platform.strategy_sdk_v1 import BarStrategyEvaluatorV1, load_bars_v1

        study, data_root = build_window_and_study(self.temp, family="trend_ma_cross", space=SPACE)
        (binding,) = study.datasets
        bars = load_bars_v1(data_root, binding.dataset_version_id, expected_content_hash=binding.content_hash)
        report = run_walk_forward_v1(study, _plan(), RULE, bars=bars)
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            # Registered and finished like the SDK Postgres test, so no queued trial is left behind.
            run_trial_worker_v1(PostgresStrategyLabLedgerV1(database), study, BarStrategyEvaluatorV1(data_root),
                                worker="walk-forward-pg-test")
            store = PostgresWalkForwardStoreV1(database)
            store.record(report)
            self.assertFalse(store.record(report))  # idempotent by hash
            self.assertIn(report.report_hash, {item.report_hash for item in store.reports(str(study.study_id))})
            # The CLI export uses the same report but only reads the ledger. Verify
            # actual server enforcement, not just a mocked read-only declaration.
            from scripts.strategy_lab import run_read_only_walk_forward_command

            lookup = PostgresStrategyLabLedgerV1.stored_study

            def read_only_lookup(ledger, study_id):
                with database.connection.cursor() as cursor:
                    cursor.execute("SHOW transaction_read_only")
                    self.assertEqual("on", cursor.fetchone()[0])
                return lookup(ledger, study_id)

            with patch.object(PostgresStrategyLabLedgerV1, "stored_study", read_only_lookup), \
                    patch("scripts.strategy_lab.PostgresWalkForwardStoreV1", side_effect=AssertionError("no registration")):
                exported = run_read_only_walk_forward_command(
                    study=study, database=database, data_root=data_root, plan=_plan(), rule=RULE,
                    export_dir=self.temp / "exports")
            self.assertEqual("CALCULATED", exported["outcome"])
            self.assertEqual(report.report_hash, exported["report_hash"])
            self.assertEqual("NOT_REGISTERED", exported["registration_status"])
            self.assertEqual([item.report_hash for item in store.reports(str(study.study_id))], [report.report_hash])
            # PostgresDatabase.transaction re-raises driver errors as PersistenceError; the cause is the trigger's.
            with self.assertRaises(PersistenceError) as refused, database.transaction() as connection, \
                    connection.cursor() as cursor:
                cursor.execute("UPDATE strategy_lab_walk_forward_reports SET fold_count=1 WHERE report_hash=%s",
                               (report.report_hash,))
            self.assertIsInstance(refused.exception.__cause__, psycopg.errors.RaiseException)
            self.assertIn("immutable", str(refused.exception.__cause__))
            # The table itself refuses non-development classes and a holdout-crossing end.
            for column, value in (("evaluation_class", "HOLDOUT_VALIDATION"), ("numeric_tier", "DECIMAL_AUTHORITATIVE"),
                                  ("cost_mode", "NET"), ("evaluation_end_exclusive",
                                                         datetime(2026, 8, 20, 0, 0, 1, tzinfo=UTC))):
                values = {"evaluation_class": "WALK_FORWARD_DEVELOPMENT", "numeric_tier": "SEARCH_NON_AUTHORITATIVE",
                          "cost_mode": "GROSS_NON_PROMOTABLE",
                          "evaluation_end_exclusive": datetime(2026, 6, 3, tzinfo=UTC), column: value}
                with self.subTest(column=column), self.assertRaises(PersistenceError) as violated:
                    self._insert_raw(database, study.study_id, values)
                self.assertIsInstance(violated.exception.__cause__, psycopg.errors.CheckViolation)
        finally:
            database.close()

    @staticmethod
    def _insert_raw(database, study_id, values) -> None:  # type: ignore[no-untyped-def]
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_walk_forward_reports (report_hash, study_id, split_hash, fold_count, "
                "evaluation_class, numeric_tier, cost_mode, evaluation_end_exclusive, identity, recorded_at) "
                "VALUES (%s,%s,%s,1,%s,%s,%s,%s,'{}'::jsonb,%s)",
                ("f" * 64, study_id, "e" * 64, values["evaluation_class"], values["numeric_tier"],
                 values["cost_mode"], values["evaluation_end_exclusive"],
                 datetime(2026, 9, 1, tzinfo=UTC) - timedelta(days=1)),
            )


if __name__ == "__main__":
    unittest.main()
