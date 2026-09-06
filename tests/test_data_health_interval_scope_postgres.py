"""Real PostgreSQL evidence for the Data Health interval-scope remediation.

Module 3G.1b discovered that ``scheduler.run_data_health_evaluation`` collided when
the same instrument had more than one interval (e.g. "1d" and "1m") evaluated at the
same tick, because assessments were scoped by ``instrument_id`` alone. Migration
20260906_0039 adds ``interval`` as an explicit identity dimension
(``data_health_assessments`` gains an ``interval`` column and its uniqueness becomes
``(scope_type, scope_value, interval, evaluated_at)``); ``data_health.py`` and
``scheduler.py`` are updated to use it. This file proves the fix directly against
``data_health.py``'s public surface (``build_assessment``/``PostgresDataHealthStore``),
independent of the bridge -- see ``test_historical_bar_bridge_postgres.py`` for the
end-to-end Databento-shaped proof.
"""

import os
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class DataHealthIntervalScopePostgresTests(unittest.TestCase):
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
        from trade_platform.data_health import PostgresDataHealthStore
        from trade_platform.persistence import PostgresDatabase

        self.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.store = PostgresDataHealthStore(self.database)
        self.suffix = uuid4().hex[:8]
        self.now = datetime(2026, 9, 6, 12, tzinfo=UTC)

    def tearDown(self) -> None:
        self.database.close()

    def _policy(self) -> "object":
        from trade_platform.data_health import DataHealthPolicy

        # expected_start == expected_end == the single observation's own event_at, and
        # minimum_observations=1: exactly enough for one clean observation to be
        # genuinely non-blocking (mirrors test_data_health.py's own clean-dataset
        # fixture), so the "healthy interval" half of the mixed-blocking tests below
        # is actually healthy, not incidentally blocked by an unrelated window/count
        # mismatch.
        return DataHealthPolicy(
            f"interval-scope-fixture-{self.suffix}", self.now, self.now,
            timedelta(minutes=1), timedelta(0), Decimal("0.05"), 1,
        )

    def _clean_observation(self, instrument_id: str, event_at: datetime) -> "object":
        from trade_platform.data_health import DataHealthObservation

        return DataHealthObservation(
            "fixture", instrument_id, event_at, event_at, 0, Decimal("100"), Decimal("101"),
            Decimal("99"), Decimal("100"), Decimal("1000"), "UTC", "UTC", True,
        )

    def test_same_instrument_daily_and_minute_at_identical_evaluated_at_both_persist(self) -> None:
        from trade_platform.data_health import DataHealthScope, build_assessment

        instrument_id = f"US:XNAS:SCOPE{self.suffix}"
        policy = self._policy()
        daily = build_assessment(
            [self._clean_observation(instrument_id, self.now)], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value=instrument_id,
            evaluated_at=self.now, interval="1d",
        )
        minute = build_assessment(
            [self._clean_observation(instrument_id, self.now)], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value=instrument_id,
            evaluated_at=self.now, interval="1m",
        )
        self.store.persist(daily)  # would previously collide with the row below
        self.store.persist(minute)

        self.assertEqual(self.store.get(daily.assessment_id).interval, "1d")
        self.assertEqual(self.store.get(minute.assessment_id).interval, "1m")
        # No loss of prior immutable evidence -- both rows independently retrievable.
        self.assertEqual(self.store.get(daily.assessment_id), daily)
        self.assertEqual(self.store.get(minute.assessment_id), minute)

    def test_a_second_assessment_at_the_same_identity_is_rejected_deterministically(self) -> None:
        """Covers both "replay is deterministic" and "conflicting duplicate fails".

        data_health_assessments is an append-only EVIDENCE log, not an
        idempotent-or-reject cache like PostgresHistoricalBarStore: it has no
        content-aware replay path, by design (double-execution of the same
        evaluation tick is prevented at the job-scheduling layer, tested
        separately in test_scheduler_postgres.py's advisory-lock coverage). What
        this store must guarantee is that a second attempt at the SAME
        (scope_type, scope_value, interval, evaluated_at) identity is always
        rejected -- deterministically, whether its content is identical to or
        different from the first -- never silently duplicated and never silently
        overwriting the original.
        """
        from trade_platform.data_health import DataHealthError, DataHealthScope, build_assessment

        instrument_id = f"US:XNAS:REPLAY{self.suffix}"
        policy = self._policy()
        first = build_assessment(
            [self._clean_observation(instrument_id, self.now)], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value=instrument_id,
            evaluated_at=self.now, interval="1d",
        )
        self.store.persist(first)

        identical_content_replay = build_assessment(
            [self._clean_observation(instrument_id, self.now)], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value=instrument_id,
            evaluated_at=self.now, interval="1d",
        )
        with self.assertRaises(DataHealthError):
            self.store.persist(identical_content_replay)

        conflicting_content = build_assessment(
            [self._clean_observation(instrument_id, self.now + timedelta(seconds=30))], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value=instrument_id,
            evaluated_at=self.now, interval="1d",
        )
        with self.assertRaises(DataHealthError):
            self.store.persist(conflicting_content)

        # The original, first-persisted row is untouched by either failed attempt.
        self.assertEqual(self.store.get(first.assessment_id), first)

    def test_instrument_blocking_reflects_a_blocked_minute_series_even_with_a_healthy_daily_series(self) -> None:
        from trade_platform.data_health import DataHealthAction, DataHealthScope, build_assessment

        instrument_id = f"US:XNAS:MIXED{self.suffix}"
        policy = self._policy()
        healthy_daily = build_assessment(
            [self._clean_observation(instrument_id, self.now)], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value=instrument_id,
            evaluated_at=self.now, interval="1d",
        )
        self.assertFalse(healthy_daily.blocking)
        blocked_minute = build_assessment(
            [], policy,  # empty observation set is a blocking MISSING_BARS/INCOMPLETE_DATASET finding
            scope_type=DataHealthScope.INSTRUMENT, scope_value=instrument_id,
            evaluated_at=self.now, interval="1m",
        )
        self.assertTrue(blocked_minute.blocking)
        self.store.persist(healthy_daily)
        self.store.persist(blocked_minute)

        blocks = self.store.active_blocks(instrument_id, "any-strategy", "EQUITY", self.now + timedelta(seconds=1))
        self.assertEqual(len(blocks), 1)
        scope, value, interval, action = blocks[0]
        self.assertEqual((scope, value, interval), (DataHealthScope.INSTRUMENT, instrument_id, "1m"))
        self.assertIn(action, (DataHealthAction.BLOCK_INSTRUMENT, DataHealthAction.BLOCK_STRATEGY))
        with self.assertRaisesRegex(Exception, "signal_validation_blocked_by_data_health"):
            self.store.require_signal_validation_allowed(instrument_id, "any-strategy", "EQUITY", self.now + timedelta(seconds=1))

    def test_instrument_blocking_reflects_a_blocked_daily_series_even_with_a_healthy_minute_series(self) -> None:
        """The reverse ordering of the test above -- neither position masks the other."""
        from trade_platform.data_health import DataHealthScope, build_assessment

        instrument_id = f"US:XNAS:MIXED2{self.suffix}"
        policy = self._policy()
        blocked_daily = build_assessment(
            [], policy, scope_type=DataHealthScope.INSTRUMENT, scope_value=instrument_id,
            evaluated_at=self.now, interval="1d",
        )
        healthy_minute = build_assessment(
            [self._clean_observation(instrument_id, self.now)], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value=instrument_id,
            evaluated_at=self.now, interval="1m",
        )
        self.store.persist(blocked_daily)
        self.store.persist(healthy_minute)

        blocks = self.store.active_blocks(instrument_id, "any-strategy", "EQUITY", self.now + timedelta(seconds=1))
        self.assertEqual([interval for _scope, _value, interval, _action in blocks], ["1d"])

    def test_asset_class_and_global_scope_blocking_is_unaffected_by_interval_partitioning(self) -> None:
        """Regression check: scopes with no interval dimension behave exactly as before."""
        from trade_platform.data_health import DataHealthScope, build_assessment

        asset_class = f"REGRESSION_CLASS_{self.suffix}"
        policy = self._policy()
        blocked_asset_class = build_assessment(
            [], policy, scope_type=DataHealthScope.ASSET_CLASS, scope_value=asset_class,
            evaluated_at=self.now,
        )
        self.assertEqual(blocked_asset_class.interval, "")
        self.store.persist(blocked_asset_class)

        blocks = self.store.active_blocks(
            f"US:XNAS:UNRELATED{self.suffix}", "any-strategy", asset_class, self.now + timedelta(seconds=1),
        )
        self.assertEqual(
            [(scope, interval) for scope, _value, interval, _action in blocks],
            [(DataHealthScope.ASSET_CLASS, "")],
        )


if __name__ == "__main__":
    unittest.main()
