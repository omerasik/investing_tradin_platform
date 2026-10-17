"""Phase R10 -- exit-criteria packets in the real migration-0065 table (real PostgreSQL).

Only DRAFT preregistrations of the real cycle-2026-08-20 exist in a test
database (its holdout is never opened here), so packets are bound to one. This
proves the store's preregistration checks, idempotency, the one-authorized
rule, immutability, the database clock and the resolver's row decoding. No
REJECTED row or feature definition is written.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

REAL_CYCLE = "cycle-2026-08-20"


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ExitCriteriaPostgresTests(unittest.TestCase):
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
        self.temp = Path(tempfile.mkdtemp(prefix="r10-exit-criteria-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_the_store_binds_records_once_and_resolves_the_stored_rows(self) -> None:
        from tests.test_activation_readiness_v1_postgres import recorded_established_study
        from trade_platform.incubation_exit_criteria_v1 import (
            STATUS_AUTHORIZED,
            STATUS_DRAFT,
            ExitCriteriaError,
            ExitCriteriaPacketV1,
            ForwardCriterionV1,
            PostgresExitCriteriaRegistryV1,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1
        from trade_platform.strategy_lab_validation_v1 import (
            PostgresHoldoutRegistryV1,
            PreregistrationV1,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            fixture = recorded_established_study(self.temp, database)
            study, rerun = fixture["study"], fixture["rerun"]
            prereg = PreregistrationV1(study=study, rerun=rerun, symbol="BTCUSDT")
            PostgresHoldoutRegistryV1(database).record_preregistration(prereg)
            trial = str(prereg.identity()["candidates"][0])
            binding = {"cycle_id": REAL_CYCLE, "study_id": str(study.study_id), "trial_id": trial,
                       "preregistration_hash": prereg.content_hash, "symbol": "BTCUSDT"}
            store = PostgresExitCriteriaRegistryV1(database)

            draft = ExitCriteriaPacketV1(**binding)
            store.record(draft)  # idempotent across runs of the shared database
            self.assertFalse(store.record(draft))
            owner_values = {
                "performance_criteria": (ForwardCriterionV1("net_total_return", ">", "0"),),
                "minimum_evidence_coverage": "0.9", "minimum_round_trips": 10, "maximum_chain_breaks": 0,
                "maximum_unfilled_signals": 0, "maximum_skipped_fills": 0,
                "required_cost_policy_hash": gross_cost_policy_v1().policy().content_hash,
                "required_account_policy_hash": "c" * 64, "authorized_by": "pg-test", "authorized_on": "2026-08-19"}
            authorized = ExitCriteriaPacketV1(**binding, **owner_values)
            self.assertEqual(STATUS_AUTHORIZED, authorized.status)
            store.record(authorized)
            conflicting = ExitCriteriaPacketV1(**binding, **{**owner_values, "maximum_chain_breaks": 1})
            with self.assertRaisesRegex(ExitCriteriaError, "conflicting_authorized_exit_criteria"):
                store.record(conflicting)

            resolved = store.resolve(cycle_id=REAL_CYCLE, study_id=binding["study_id"], trial_id=trial)
            assert resolved is not None
            self.assertEqual((authorized.content_hash, True), (resolved.criteria_hash, resolved.issued))
            # recorded_at is the database's clock, never the caller's.
            self.assertLessEqual(resolved.recorded_at, datetime.now(UTC) + timedelta(minutes=1))

            # The database bypass is refused too: a second AUTHORIZED row, an edit, a delete.
            with self.assertRaises(PersistenceError), database.transaction() as connection, \
                    connection.cursor() as cursor:
                cursor.execute("UPDATE strategy_lab_incubation_exit_criteria SET status='DRAFT' "
                               "WHERE criteria_hash=%s", (authorized.content_hash,))
            with self.assertRaises(PersistenceError), database.transaction() as connection, \
                    connection.cursor() as cursor:
                cursor.execute("DELETE FROM strategy_lab_incubation_exit_criteria WHERE criteria_hash=%s",
                               (draft.content_hash,))
            with self.assertRaises(PersistenceError), database.transaction() as connection, \
                    connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO strategy_lab_incubation_exit_criteria (criteria_hash, criteria_id, cycle_id, "
                    "study_id, trial_id, preregistration_hash, status, identity) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'AUTHORIZED',%s::jsonb)",
                    (conflicting.content_hash, conflicting.criteria_id, REAL_CYCLE, study.study_id, trial,
                     prereg.content_hash, json.dumps(conflicting.identity())))

            # The store refuses a packet for a trial the preregistration does not name, or another symbol.
            with self.assertRaisesRegex(ExitCriteriaError, "trial_is_not_a_preregistered_candidate"):
                store.record(ExitCriteriaPacketV1(**{**binding, "trial_id": "00000000-0000-4000-8000-000000000000"}))
            with self.assertRaisesRegex(ExitCriteriaError, "symbol_is_not_the_preregistered_symbol"):
                store.record(ExitCriteriaPacketV1(**{**binding, "symbol": "ETHUSDT"}))
            with self.assertRaisesRegex(ExitCriteriaError, "preregistration_not_recorded"):
                store.record(ExitCriteriaPacketV1(**{**binding, "preregistration_hash": "0" * 64}))
            self.assertEqual(STATUS_DRAFT, draft.status)
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
