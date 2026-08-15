"""Real PostgreSQL persistence and operational signal-gate evidence."""

import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class DataHealthPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace(
                "postgresql://", "postgresql+psycopg://", 1
            ),
        )
        command.upgrade(config, "head")

    def test_persisted_block_prevents_validation_then_clean_run_reopens(self) -> None:
        from trade_platform.data_health import (
            DataHealthObservation,
            DataHealthPolicy,
            DataHealthScope,
            PostgresDataHealthStore,
            build_assessment,
        )
        from trade_platform.domain import AssetClass, Instrument, OrderSide
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.postgres_decision_authorities import (
            PostgresInstrumentStore,
            PostgresSignalStore,
        )
        from trade_platform.signal_engine import (
            SignalEngine,
            SignalEngineError,
            SignalProposal,
            ValidationStage,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        instrument_id = "US:XNAS:CYCLE12_HEALTH"
        PostgresInstrumentStore(database).register(
            Instrument(
                instrument_id, "C12", "XNAS", AssetClass.ETF, "USD",
                Decimal("0.01"), Decimal("1"),
            )
        )
        proposal = SignalProposal.create(
            instrument_id=instrument_id, asset_class=AssetClass.ETF,
            strategy_version="cycle12-strategy-v1", direction=OrderSide.BUY,
            entry_low=Decimal("100"), entry_high=Decimal("101"),
            invalidation_level=Decimal("98"), stop_level=Decimal("99"),
            target_logic="fixture target", expected_holding_period=timedelta(days=1),
            strength=Decimal("0.7"), confidence=Decimal("0.8"),
            expected_return=Decimal("0.02"), expected_volatility=Decimal("0.1"),
            expected_downside=Decimal("-0.01"), risk_reward_estimate=Decimal("2"),
            regime="BULL", liquidity_score=Decimal("1"), data_quality_score=Decimal("1"),
            news_risk=Decimal("0"), event_risk=Decimal("0"),
            correlation_impact=Decimal("0"), recommended_quantity=Decimal("1"),
            expires_at=datetime.now(UTC) + timedelta(days=1), explanation="fixture",
            contradicting_evidence=(),
        )
        signals = PostgresSignalStore(database)
        signals.append(proposal)
        policy_time = proposal.created_at
        policy = DataHealthPolicy(
            "cycle12-health-v1", policy_time, policy_time, timedelta(days=1),
            timedelta(0), Decimal("0.05"), 1,
        )
        health = PostgresDataHealthStore(database)
        blocked = build_assessment(
            [], policy, scope_type=DataHealthScope.INSTRUMENT,
            scope_value=instrument_id, evaluated_at=policy_time + timedelta(seconds=1),
        )
        health.persist(blocked)
        first_validation = replace(
            SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}),
            assessed_at=policy_time + timedelta(seconds=2),
        )
        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO runtime_signal_validations VALUES (%s,%s,'VALIDATED','[]'::jsonb,'[]'::jsonb,%s)",
                (uuid4(), proposal.signal_id, first_validation.assessed_at),
            )
        with self.assertRaisesRegex(SignalEngineError, "blocked_by_data_health"):
            signals.append_validation(first_validation)

        clean_observation = DataHealthObservation(
            "cycle12-fixture", instrument_id, policy_time, policy_time, 0,
            Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"),
            Decimal("1000"), "UTC", "UTC", True,
        )
        clean = build_assessment(
            [clean_observation], policy, scope_type=DataHealthScope.INSTRUMENT,
            scope_value=instrument_id, evaluated_at=policy_time + timedelta(seconds=3),
        )
        health.persist(clean)
        second_validation = replace(
            SignalEngine().validate(proposal, {stage: True for stage in ValidationStage}),
            assessed_at=policy_time + timedelta(seconds=4),
        )
        signals.append_validation(second_validation)
        self.assertEqual(health.active_blocks(instrument_id, proposal.strategy_version, "ETF", second_validation.assessed_at), ())
        self.assertEqual(health.get(blocked.assessment_id), blocked)

        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE data_health_assessments SET blocking=false WHERE assessment_id=%s",
                (blocked.assessment_id,),
            )
        database.close()
        reopened = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.assertEqual(PostgresDataHealthStore(reopened).get(blocked.assessment_id), blocked)
        reopened.close()
