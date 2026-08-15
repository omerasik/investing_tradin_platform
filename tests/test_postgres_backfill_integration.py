"""Real PostgreSQL proof for deterministic mapped legacy migration."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from trade_platform.legacy_migration_fixture import create_representative_legacy_fixture
from trade_platform.postgres_backfill import BackfillError, backfill_sqlite_to_postgres


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PostgresBackfillIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        cls.dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", cls.dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")

    def _seed_authorities(self, ids: dict[str, str]) -> dict[str, dict[str, str]]:
        import psycopg

        now = datetime(2026, 1, 15, 15, tzinfo=UTC)
        exchange_id = uuid4()
        instrument_id = uuid4()
        dataset_id = uuid4()
        dataset_version_id = uuid4()
        strategy_id = uuid4()
        strategy_version_id = uuid4()
        signal_id = uuid4()
        policy_id = uuid4()
        policy_version_id = uuid4()
        account_id = f"migration-paper-{str(exchange_id)[:8]}"
        suffix = str(exchange_id)[:8]
        connection = psycopg.connect(self.dsn)
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO exchanges VALUES (%s,%s,'Migration','UTC',%s)",
                (exchange_id, f"XM{suffix[:6]}", now),
            )
            cursor.execute(
                "INSERT INTO instruments VALUES "
                "(%s,%s,'MIGR','EQUITY','USD',0.01,1,%s,NULL,%s)",
                (instrument_id, exchange_id, now, now),
            )
            cursor.execute(
                "INSERT INTO datasets VALUES (%s,%s,'legacy-fixture','terms-v1',%s)",
                (dataset_id, f"migration-dataset-{suffix}", now),
            )
            cursor.execute(
                "INSERT INTO dataset_versions VALUES (%s,%s,'v1',%s,NULL,NULL,%s)",
                (
                    dataset_version_id,
                    dataset_id,
                    hashlib.sha256(f"migration-dataset:{suffix}".encode()).hexdigest(),
                    now,
                ),
            )
            cursor.execute(
                "INSERT INTO strategy_definitions VALUES (%s,'migration','fixture',%s)",
                (strategy_id, now),
            )
            cursor.execute(
                "INSERT INTO strategy_versions VALUES "
                "(%s,%s,'v1','{}'::jsonb,'cost-v1','capacity-v1','{}'::jsonb,%s)",
                (strategy_version_id, strategy_id, now),
            )
            cursor.execute(
                "INSERT INTO accounts VALUES (%s,'PAPER','USD',%s)", (account_id, now)
            )
            cursor.execute(
                "INSERT INTO signals VALUES "
                "(%s,%s,%s,'VALIDATED',%s,%s + interval '1 day','{}'::jsonb)",
                (signal_id, strategy_version_id, instrument_id, now, now),
            )
            cursor.execute(
                "INSERT INTO risk_policies VALUES (%s,%s,%s)",
                (policy_id, f"migration-policy-{suffix}", now),
            )
            cursor.execute(
                "INSERT INTO risk_policy_versions VALUES "
                "(%s,%s,'v1','{}'::jsonb,%s,%s)",
                (
                    policy_version_id,
                    policy_id,
                    hashlib.sha256(f"migration-policy:{suffix}".encode()).hexdigest(),
                    now,
                ),
            )
        connection.close()
        return {
            "accounts": {ids["legacy_account"]: account_id},
            "signals": {ids["signal_id"]: str(signal_id)},
            "instruments": {ids["legacy_instrument"]: str(instrument_id)},
            "strategy_versions": {
                ids["legacy_strategy_version"]: str(strategy_version_id)
            },
            "dataset_versions": {ids["legacy_dataset_version"]: str(dataset_version_id)},
            "policy_versions": {"risk:legacy-risk-v1": f"migrated-risk-{suffix}"},
            "risk_decision_policies": {
                ids["risk_decision_id"]: str(policy_version_id)
            },
            "risk_reservations": {ids["intent_id"]: ids["reservation_id"]},
            "broker_accounts": {"legacy-broker": account_id},
            "promotion_packages": {
                ids["promotion_decision_id"]: ids["validation_package_id"]
            },
        }

    def test_real_mapped_apply_is_reconciled_idempotent_and_fail_closed(self) -> None:
        import psycopg

        from trade_platform.domain import OrderStatus
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_paper_oms import PostgresPaperOms

        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite"
            ids = create_representative_legacy_fixture(path)
            mapping = self._seed_authorities(ids)

            dry_run = backfill_sqlite_to_postgres(
                path, self.dsn, dry_run=True, identity_mapping=mapping
            )
            self.assertTrue(dry_run.dry_run)
            self.assertIsNotNone(dry_run.migration_run_id)
            self.assertFalse(dry_run.unsupported_records)
            self.assertEqual(dry_run.row_counts["paper_fills"], 2)

            applied = backfill_sqlite_to_postgres(
                path, self.dsn, dry_run=False, identity_mapping=mapping
            )
            self.assertTrue(applied.post_write_reconciled)
            self.assertEqual(
                applied.destination_counts,
                {
                    table: count
                    for table, count in dry_run.row_counts.items()
                    if table != "research_experiments"
                },
            )
            self.assertGreater(applied.migrated_rows, 20)

            identical = backfill_sqlite_to_postgres(
                path, self.dsn, dry_run=False, identity_mapping=mapping
            )
            self.assertEqual(identical.migration_run_id, applied.migration_run_id)

            reopened_database = PostgresDatabase(self.dsn)
            reopened = PostgresPaperOms(reopened_database).get(UUID(ids["intent_id"]))
            self.assertEqual(reopened.status, OrderStatus.FILLED)
            self.assertEqual(reopened.filled_quantity, Decimal("2.000000000000"))
            reopened_database.close()

            connection = psycopg.connect(self.dsn)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT integrity_status, canonical_manifest FROM validation_packages "
                    "WHERE package_id = %s",
                    (UUID(ids["validation_package_id"]),),
                )
                self.assertEqual(cursor.fetchone(), ("LEGACY_UNVERIFIABLE", None))
                cursor.execute(
                    "SELECT report FROM postgres_backfill_runs WHERE run_id = %s",
                    (applied.migration_run_id,),
                )
                persisted = cursor.fetchone()[0]
                self.assertEqual(persisted["reconciliation_result"], "MATCHED")
                self.assertEqual(persisted["source_counts"], dry_run.row_counts)
                cursor.execute(
                    "SELECT price FROM fills WHERE fill_id = %s",
                    (UUID(ids["partial_fill_id"]),),
                )
                self.assertEqual(cursor.fetchone()[0], Decimal("10.000000000001"))
                cursor.execute(
                    "SELECT notional FROM risk_reservations WHERE reservation_id = %s",
                    (UUID(ids["reservation_id"]),),
                )
                self.assertEqual(cursor.fetchone()[0], Decimal("20.000000000002"))
            connection.close()

            missing_mapping = json.loads(json.dumps(mapping))
            missing_mapping["accounts"].clear()
            with self.assertRaisesRegex(BackfillError, "unmapped_accounts"):
                backfill_sqlite_to_postgres(
                    path, self.dsn, dry_run=False, identity_mapping=missing_mapping
                )

            source = sqlite3.connect(path)
            source.execute(
                "UPDATE paper_fills SET price = '11.000000000001' "
                "WHERE fill_id = ?",
                (ids["partial_fill_id"],),
            )
            source.commit()
            source.close()
            with self.assertRaisesRegex(BackfillError, "destination_conflict"):
                backfill_sqlite_to_postgres(
                    path, self.dsn, dry_run=False, identity_mapping=mapping
                )

            source = sqlite3.connect(path)
            source.execute("CREATE TABLE unknown_financial_evidence (id TEXT PRIMARY KEY)")
            source.execute("INSERT INTO unknown_financial_evidence VALUES ('unknown')")
            source.commit()
            source.close()
            with self.assertRaisesRegex(BackfillError, "unsupported_records"):
                backfill_sqlite_to_postgres(
                    path, self.dsn, dry_run=False, identity_mapping=mapping
                )


if __name__ == "__main__":
    unittest.main()
