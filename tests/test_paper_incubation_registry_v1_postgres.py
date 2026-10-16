"""Phase R10 -- the OR-7 registry resolver over the real R4/R6 tables (real PostgreSQL).

The real cycle-2026-08-20 is NEVER opened here, and the database refuses to open
any other span that has not passed, so a bound chain cannot exist in a test
database (the genuine bound path is covered offline with R6's own objects). This
proves the resolver's queries and row decoding against the real schema: a real
study, Decimal rerun and DRAFT packet re-derive from their stored rows, an
unopened cycle resolves to ``UNRESOLVED`` with no default, and the terminal's
incubation view carries that resolution. No REJECTED row or feature definition
is written.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

REAL_CYCLE = "cycle-2026-08-20"


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class IncubationRegistryPostgresTests(unittest.TestCase):
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
        self.temp = Path(tempfile.mkdtemp(prefix="r10-registry-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_the_resolver_reads_the_real_rows_and_an_unopened_cycle_stays_unresolved(self) -> None:
        from tests.test_activation_readiness_v1_postgres import recorded_established_study
        from trade_platform import paper_incubation_registry_v1 as registry
        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
        from trade_platform.paper_incubation_report_v1 import UNRESOLVED_OR7_INCUBATION_DAYS
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_validation_v1 import (
            PostgresHoldoutRegistryV1,
            PreregistrationV1,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            fixture = recorded_established_study(self.temp, database)
            study, rerun = fixture["study"], fixture["rerun"]
            self.assertEqual("ESTABLISHED", rerun.selection_status)
            draft = PreregistrationV1(study=study, rerun=rerun, symbol="BTCUSDT")  # DRAFT on the real cycle
            PostgresHoldoutRegistryV1(database).record_preregistration(draft)
            start = datetime(2026, 8, 20, tzinfo=UTC).isoformat()
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM strategy_lab_holdout_openings WHERE cycle_id=%s", (REAL_CYCLE,))
                self.assertEqual(0, cursor.fetchone()[0], "the real holdout must stay closed")
                resolution = registry.resolve_incubation_bindings_v1(cursor, cycle_id=REAL_CYCLE)
                self.assertEqual(((), (registry.UNRESOLVED_HOLDOUT_NOT_OPENED,)),
                                 (resolution.bindings, resolution.unresolved))
                with self.assertRaisesRegex(registry.IncubationRegistryError, "research_cycle_not_registered"):
                    registry.resolve_incubation_bindings_v1(cursor, cycle_id="cycle-1999-01-01")
                # The stored packet re-derives and its row agrees with it; a DRAFT is never an opened packet.
                with self.assertRaisesRegex(registry.IncubationRegistryError,
                                            "opened_preregistration_is_not_authorized"):
                    registry._packet(cursor, draft.content_hash, REAL_CYCLE, start, start)
                with self.assertRaisesRegex(registry.IncubationRegistryError, "opened_preregistration_not_recorded"):
                    registry._packet(cursor, "0" * 64, REAL_CYCLE, start, start)
                # The real study and Decimal rerun rows re-derive and agree with the packet.
                registry._study_and_rerun(cursor, draft.identity())
                self.assertIsNone(registry._validation_row(cursor, REAL_CYCLE))
                self.assertEqual([], registry._state_rows(cursor, str(study.study_id), "0" * 64))
            # The terminal's incubation view carries the registry resolution, never a default length.
            view = PostgresOperatorDashboardQueries(database).terminal_incubation()
            self.assertEqual({"cycle_id": REAL_CYCLE, "source": registry.REGISTRY_SOURCE_V1, "bound_candidates": 0,
                              "unresolved": [registry.UNRESOLVED_HOLDOUT_NOT_OPENED]},
                             view.report["preregistration_registry"])
            self.assertTrue(all(c["required_days"] == UNRESOLVED_OR7_INCUBATION_DAYS
                                for c in view.report["candidates"]))
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
