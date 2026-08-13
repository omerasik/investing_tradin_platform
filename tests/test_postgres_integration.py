"""Runs in CI or locally only when POSTGRES_TEST_DSN names an ephemeral database."""

import hashlib
import os
import threading
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PostgresIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        self.config = Config("alembic.ini")
        self.config.set_main_option("sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(self.config, "head")
        import psycopg
        self.connection = psycopg.connect(os.environ["POSTGRES_TEST_DSN"])
        self.now = datetime.now(UTC)
        self.exchange_id, self.instrument_id = uuid4(), uuid4()
        self.dataset_id, self.dataset_version_id = uuid4(), uuid4()
        self.strategy_id, self.strategy_version_id = uuid4(), uuid4()
        self.signal_id, self.policy_id, self.policy_version_id = uuid4(), uuid4(), uuid4()
        suffix = str(self.exchange_id)[:8]
        dataset_hash = hashlib.sha256(f"dataset:{self.dataset_version_id}".encode()).hexdigest()
        policy_hash = hashlib.sha256(f"policy:{self.policy_version_id}".encode()).hexdigest()
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO exchanges VALUES (%s, %s, 'Integration', 'UTC', %s)", (self.exchange_id, f"XI{suffix[:6]}", self.now))
            cursor.execute("INSERT INTO instruments VALUES (%s, %s, 'TEST', 'EQUITY', 'USD', 0.01, 1, %s, NULL, %s)", (self.instrument_id, self.exchange_id, self.now, self.now))
            cursor.execute("INSERT INTO datasets VALUES (%s, %s, 'fixture', 'terms-v1', %s)", (self.dataset_id, f"integration-data-{suffix}", self.now))
            cursor.execute("INSERT INTO dataset_versions VALUES (%s, %s, 'v1', %s, NULL, NULL, %s)", (self.dataset_version_id, self.dataset_id, dataset_hash, self.now))
            cursor.execute("INSERT INTO strategy_definitions VALUES (%s, 'test', 'test', %s)", (self.strategy_id, self.now))
            cursor.execute("INSERT INTO strategy_versions VALUES (%s, %s, 'v1', '{}'::jsonb, 'cost-v1', 'capacity-v1', '{}'::jsonb, %s)", (self.strategy_version_id, self.strategy_id, self.now))
            cursor.execute("INSERT INTO accounts VALUES (%s, 'PAPER', 'USD', %s)", (f"integration-paper-{suffix}", self.now))
            cursor.execute("INSERT INTO signals VALUES (%s, %s, %s, 'VALIDATED', %s, %s + interval '1 day', '{}'::jsonb)", (self.signal_id, self.strategy_version_id, self.instrument_id, self.now, self.now))
            cursor.execute("INSERT INTO risk_policies VALUES (%s, %s, %s)", (self.policy_id, f"integration-policy-{suffix}", self.now))
            cursor.execute("INSERT INTO risk_policy_versions VALUES (%s, %s, 'v1', '{}'::jsonb, %s, %s)", (self.policy_version_id, self.policy_id, policy_hash, self.now))

    def tearDown(self) -> None:
        self.connection.close()

    def test_migration_creates_schema_and_immutable_constraint(self) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.quant_validation_artifacts')")
            self.assertEqual(cursor.fetchone()[0], "quant_validation_artifacts")
            cursor.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'quant_validation_artifacts'::regclass")
            self.assertIn("quant_validation_artifacts_immutable", {row[0] for row in cursor.fetchall()})

    def test_atomic_oms_fill_and_risk_idempotency(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_repositories import (
            PostgresCriticalRepository,
            PostgresRepositoryError,
        )

        repository = PostgresCriticalRepository(PostgresDatabase(os.environ["POSTGRES_TEST_DSN"]))
        intent_id = uuid4()
        account_id = f"integration-paper-{str(self.exchange_id)[:8]}"
        repository.create_order_with_event(intent_id=intent_id, signal_id=self.signal_id, account_id=account_id, instrument_id=self.instrument_id, side="BUY", quantity=Decimal(2), limit_price=Decimal(100), status="PROPOSED", created_at=self.now)
        first_fill = repository.ingest_fill(external_fill_id="fill-1", intent_id=intent_id, quantity=Decimal(1), price=Decimal(100), occurred_at=self.now)
        self.assertEqual(first_fill, repository.ingest_fill(external_fill_id="fill-1", intent_id=intent_id, quantity=Decimal(1), price=Decimal(100), occurred_at=self.now))
        with self.assertRaises(PostgresRepositoryError):
            repository.ingest_fill(external_fill_id="fill-1", intent_id=intent_id, quantity=Decimal(2), price=Decimal(100), occurred_at=self.now)
        first = repository.reserve_and_record_decision(account_id=account_id, intent_id=intent_id, policy_version_id=self.policy_version_id, business_date=self.now.date(), notional=Decimal(70), daily_limit=Decimal(100), approved=True, reasons=(), decided_at=self.now)
        self.assertTrue(repository.reserve_and_record_decision(account_id=account_id, intent_id=intent_id, policy_version_id=self.policy_version_id, business_date=self.now.date(), notional=Decimal(70), daily_limit=Decimal(100), approved=True, reasons=(), decided_at=self.now).idempotent)
        with self.assertRaisesRegex(PostgresRepositoryError, "daily_notional"):
            repository.reserve_and_record_decision(account_id=account_id, intent_id=uuid4(), policy_version_id=self.policy_version_id, business_date=self.now.date(), notional=Decimal(31), daily_limit=Decimal(100), approved=True, reasons=(), decided_at=self.now)
        self.assertIsNotNone(first.reservation_id)
        repository._database.close()

    def test_validation_package_rolls_back_on_unknown_artifact(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_repositories import (
            PostgresCriticalRepository,
            PostgresRepositoryError,
        )

        repository = PostgresCriticalRepository(PostgresDatabase(os.environ["POSTGRES_TEST_DSN"]))
        package_id = uuid4()
        with self.assertRaises(PostgresRepositoryError):
            repository.create_validation_package(package_id=package_id, strategy_version_id=self.strategy_version_id, dataset_version_id=self.dataset_version_id, cost_model_version="cost-v1", content_hash="c" * 64, status="BLOCKED", created_at=self.now, limitations=(), artifacts={"capacity": uuid4()})
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM validation_packages WHERE package_id = %s", (package_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
        repository._database.close()

    def test_concurrent_reservations_cannot_exceed_daily_limit(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_repositories import (
            PostgresCriticalRepository,
            PostgresRepositoryError,
        )

        account_id = f"integration-paper-{str(self.exchange_id)[:8]}"
        start = threading.Barrier(2)
        outcomes: list[str] = []

        def reserve() -> None:
            repository = PostgresCriticalRepository(PostgresDatabase(os.environ["POSTGRES_TEST_DSN"]))
            try:
                start.wait(timeout=5)
                repository.reserve_and_record_decision(account_id=account_id, intent_id=uuid4(), policy_version_id=self.policy_version_id, business_date=self.now.date(), notional=Decimal(60), daily_limit=Decimal(100), approved=True, reasons=(), decided_at=self.now)
                outcomes.append("reserved")
            except PostgresRepositoryError:
                outcomes.append("blocked")
            finally:
                repository._database.close()

        workers = (threading.Thread(target=reserve), threading.Thread(target=reserve))
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertEqual(sorted(outcomes), ["blocked", "reserved"])


if __name__ == "__main__":
    unittest.main()
