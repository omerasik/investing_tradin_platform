"""Phase R4.1 -- the Strategy Lab ledger against real PostgreSQL.

Synthetic studies only: every study binds a fixture dataset hash and a random
implementation hash (so reruns against one database never collide), every
clock reading is in the past, and no REJECTED row or feature definition is
written, so no shared-database invariant is touched.
"""

from __future__ import annotations

import hashlib
import os
import threading
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def _study(trials: int = 6):  # type: ignore[no-untyped-def]
    from trade_platform.evidence_tier_authority_v1 import EvidenceTierV1
    from trade_platform.strategy_lab_study_v1 import (
        DatasetBindingV1,
        InputRoleV1,
        ParameterDomainV1,
        ParameterKindV1,
        ParameterSpaceV1,
        SearchModeV1,
        SearchPlanV1,
        StrategySpecV1,
        StudySpecV1,
    )

    impl = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    strategy = StrategySpecV1(
        family="ledger_fixture", version="1.0.0", implementation_sha256=impl,
        execution_convention="next_bar_open",
        input_roles=(InputRoleV1("bars", frozenset({EvidenceTierV1.T0_SYNTHETIC})),),
        parameter_schema={"window": ParameterKindV1.INTEGER},
    )
    return StudySpecV1(
        strategy=strategy,
        parameter_space=ParameterSpaceV1.of(ParameterDomainV1.integer_range("window", start=1, stop_inclusive=trials, step=1)),
        datasets=(DatasetBindingV1("bars", UUID(int=7), "c" * 64, EvidenceTierV1.T0_SYNTHETIC,
                                   datetime(2026, 6, 1, tzinfo=UTC)),),
        search=SearchPlanV1(SearchModeV1.EXHAUSTIVE, max_trials=trials),
        evaluation_upper_bound_exclusive=datetime(2026, 7, 1, tzinfo=UTC),
        label="ledger fixture",
    )


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class StrategyLabLedgerPostgresTests(unittest.TestCase):
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

        self.clock = _Clock(datetime(2026, 9, 1, 12, tzinfo=UTC))
        self.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.ledger = PostgresStrategyLabLedgerV1(self.database, clock=self.clock)

    def tearDown(self) -> None:
        self.database.close()

    def test_registration_is_idempotent_and_queues_every_trial(self) -> None:
        spec = _study(6)
        first = self.ledger.register_study(spec)
        self.assertTrue(first.created)
        again = self.ledger.register_study(spec)
        self.assertFalse(again.created)
        self.assertEqual(first.study_id, again.study_id)
        progress = self.ledger.progress(spec.study_id)
        self.assertEqual(progress.planned_trial_count, 6)
        self.assertEqual(progress.states, {"PENDING": 6})
        self.assertFalse(progress.finished)

    def test_claim_complete_and_resume_to_finish(self) -> None:
        from trade_platform.strategy_lab_ledger_v1 import TrialOutcomeV1

        spec = _study(5)
        self.ledger.register_study(spec)
        claims = self.ledger.claim(spec.study_id, worker="w1", limit=3, lease_seconds=60)
        self.assertEqual([c.ordinal for c in claims], [0, 1, 2])
        self.assertEqual({c.attempt for c in claims}, {1})
        self.assertEqual(claims[0].parameters, {"window": "1"})
        for claim in claims:
            self.ledger.complete(claim, outcome=TrialOutcomeV1.EVALUATED, metrics={"trades": claim.ordinal})
        # A fresh ledger (a restarted worker) resumes with exactly the rest.
        rest = self.ledger.claim(spec.study_id, worker="w2", limit=10, lease_seconds=60)
        self.assertEqual([c.ordinal for c in rest], [3, 4])
        self.ledger.complete(rest[0], outcome=TrialOutcomeV1.INADMISSIBLE_PARAMETERS, metrics={})
        self.ledger.complete(rest[1], outcome=TrialOutcomeV1.EVALUATED, metrics={"trades": 4})
        progress = self.ledger.progress(spec.study_id)
        self.assertEqual(progress.states, {"SUCCEEDED": 5})
        self.assertTrue(progress.finished)
        results = self.ledger.results(spec.study_id)
        self.assertEqual(len(results), 5)
        self.assertEqual({r.numeric_tier for r in results}, {"SEARCH_NON_AUTHORITATIVE"})
        self.assertEqual(self.ledger.claim(spec.study_id, worker="w3", limit=10, lease_seconds=60), ())

    def test_concurrent_workers_never_share_a_trial(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1

        spec = _study(60)
        self.ledger.register_study(spec)
        got: dict[str, list[UUID]] = {}
        errors: list[BaseException] = []

        def work(name: str) -> None:
            database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
            ledger = PostgresStrategyLabLedgerV1(database, clock=self.clock)
            try:
                mine: list[UUID] = []
                while batch := ledger.claim(spec.study_id, worker=name, limit=4, lease_seconds=300):
                    mine.extend(c.trial_id for c in batch)
                got[name] = mine
            except BaseException as error:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(error)
            finally:
                database.close()

        threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        claimed = [trial for trials in got.values() for trial in trials]
        self.assertEqual(len(claimed), 60)
        self.assertEqual(len(set(claimed)), 60)

    def test_expired_lease_is_reclaimed_and_the_old_holder_is_refused(self) -> None:
        from trade_platform.strategy_lab_ledger_v1 import StrategyLabLedgerError, TrialOutcomeV1

        spec = _study(1)
        self.ledger.register_study(spec)
        (old,) = self.ledger.claim(spec.study_id, worker="dead", limit=1, lease_seconds=30)
        self.assertEqual(self.ledger.claim(spec.study_id, worker="other", limit=1, lease_seconds=30), ())
        self.clock.now += timedelta(seconds=31)
        with self.assertRaisesRegex(StrategyLabLedgerError, "lease_expired"):
            self.ledger.complete(old, outcome=TrialOutcomeV1.EVALUATED, metrics={})
        (new,) = self.ledger.claim(spec.study_id, worker="alive", limit=1, lease_seconds=30)
        self.assertEqual(new.attempt, 2)
        with self.assertRaisesRegex(StrategyLabLedgerError, "lease_lost"):
            self.ledger.complete(old, outcome=TrialOutcomeV1.EVALUATED, metrics={})
        self.ledger.complete(new, outcome=TrialOutcomeV1.EVALUATED, metrics={"trades": 1})
        self.assertEqual(
            self.ledger.events(new.trial_id),
            ((1, "CLAIMED", "dead"), (1, "LEASE_EXPIRED", "dead"), (2, "CLAIMED", "alive"), (2, "SUCCEEDED", "alive")),
        )

    def test_failure_retry_and_cancellation(self) -> None:
        from trade_platform.strategy_lab_ledger_v1 import StrategyLabLedgerError, TrialOutcomeV1

        spec = _study(3)
        self.ledger.register_study(spec)
        a, b, c = self.ledger.claim(spec.study_id, worker="w", limit=3, lease_seconds=60)
        self.ledger.fail(a, reason="evaluator_raised:valueerror")
        self.ledger.complete(b, outcome=TrialOutcomeV1.EVALUATED, metrics={})
        self.assertEqual(self.ledger.progress(spec.study_id).states, {"CLAIMED": 1, "FAILED": 1, "SUCCEEDED": 1})
        self.assertEqual(self.ledger.claim(spec.study_id, worker="w", limit=3, lease_seconds=60), ())
        self.assertEqual(self.ledger.retry_failed(spec.study_id, reason="operator_retry"), 1)
        (retried,) = self.ledger.claim(spec.study_id, worker="w", limit=3, lease_seconds=60)
        self.assertEqual((retried.trial_id, retried.attempt), (a.trial_id, 2))
        self.assertEqual(self.ledger.cancel_study(spec.study_id, reason="operator_cancel"), 2)
        with self.assertRaisesRegex(StrategyLabLedgerError, "lease_lost"):
            self.ledger.complete(c, outcome=TrialOutcomeV1.EVALUATED, metrics={})
        progress = self.ledger.progress(spec.study_id)
        self.assertEqual(progress.states, {"CANCELLED": 2, "SUCCEEDED": 1})
        self.assertTrue(progress.finished)
        self.assertEqual(self.ledger.retry_failed(spec.study_id, reason="operator_retry"), 0)

    def test_database_refuses_authority_and_terminal_mutation(self) -> None:
        from trade_platform.persistence import PersistenceError
        from trade_platform.strategy_lab_ledger_v1 import TrialOutcomeV1

        spec = _study(1)
        self.ledger.register_study(spec)
        (claim,) = self.ledger.claim(spec.study_id, worker="w", limit=1, lease_seconds=60)
        self.ledger.complete(claim, outcome=TrialOutcomeV1.EVALUATED, metrics={})
        statements = (
            ("UPDATE strategy_lab_trial_queue SET state='PENDING' WHERE trial_id=%s", (claim.trial_id,)),
            ("DELETE FROM strategy_lab_trial_queue WHERE trial_id=%s", (claim.trial_id,)),
            ("UPDATE strategy_lab_trial_results SET numeric_tier='AUTHORITATIVE' WHERE trial_id=%s", (claim.trial_id,)),
            ("UPDATE strategy_lab_studies SET label='x' WHERE study_id=%s", (spec.study_id,)),
            ("UPDATE strategy_lab_studies SET cost_policy_slot='TAKER_5BPS' WHERE study_id=%s", (spec.study_id,)),
        )
        for sql, params in statements:
            with self.subTest(sql=sql), self.assertRaises(PersistenceError), \
                    self.database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(sql, params)

    def test_check_constraints_pin_owner_slots_and_holdout(self) -> None:
        from trade_platform.persistence import PersistenceError

        insert = (
            "INSERT INTO strategy_lab_studies (study_id, content_hash, schema_version, strategy_family, identity, "
            "planned_trial_count, evaluation_upper_bound_exclusive, numeric_policy_slot, cost_policy_slot, label, "
            "registered_at) VALUES (%s,%s,'strategy-lab-study-v1','f','{}'::jsonb,1,%s,%s,%s,'',%s)"
        )
        good = ("UNSET_PENDING_OWNER_DECISION_OR_3", "UNSET_PENDING_OWNER_DECISION_OR_6")
        cases = (
            (datetime(2026, 8, 20, 0, 0, 1, tzinfo=UTC), *good),
            (datetime(2026, 7, 1, tzinfo=UTC), "FLOAT64_SEARCH", good[1]),
            (datetime(2026, 7, 1, tzinfo=UTC), good[0], "VIP0_TAKER"),
        )
        for bound, numeric, cost in cases:
            with self.subTest(bound=bound, numeric=numeric, cost=cost), self.assertRaises(PersistenceError), \
                    self.database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(insert, (uuid.uuid4(), hashlib.sha256(uuid.uuid4().bytes).hexdigest(), bound,
                                        numeric, cost, datetime(2026, 9, 1, tzinfo=UTC)))


if __name__ == "__main__":
    unittest.main()
