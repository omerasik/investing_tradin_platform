import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from trade_platform.config import PlatformConfig
from trade_platform.persistence import (
    PersistenceError,
    PersistenceTarget,
    PostgresDatabase,
    SQLiteDatabase,
    open_database,
)
from trade_platform.postgres_backfill import backfill_sqlite_to_postgres, inspect_sqlite_source
from trade_platform.postgres_schema import IMMUTABLE_TABLES, INITIAL_SCHEMA_SQL, drop_schema_sql


class PersistenceTests(unittest.TestCase):
    def test_postgres_transaction_preserves_domain_rejection_reason(self) -> None:
        class TransactionContext:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class Connection:
            def transaction(self):
                return TransactionContext()

        database = PostgresDatabase.__new__(PostgresDatabase)
        database.dsn = "postgresql://fixture"
        database.connection = Connection()
        with (
            self.assertRaisesRegex(ValueError, "domain_integrity_rejected"),
            database.transaction(),
        ):
            raise ValueError("domain_integrity_rejected")

    def test_sqlite_adapter_is_transactional_and_backend_selection_is_explicit(self) -> None:
        database = SQLiteDatabase()
        with database.transaction() as transaction:
            transaction.execute("CREATE TABLE example (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            transaction.execute("INSERT INTO example VALUES (1, 'ok')")
        self.assertEqual(database.connection.execute("SELECT value FROM example").fetchone()[0], "ok")
        database.close()
        with self.assertRaisesRegex(PersistenceError, "postgres_dsn"):
            open_database(PersistenceTarget.POSTGRES, "not-a-dsn")

    def test_paper_and_production_configuration_require_postgres(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires_postgres"):
            PlatformConfig(environment="paper")
        with self.assertRaisesRegex(ValueError, "location_required"):
            PlatformConfig(persistence_target=PersistenceTarget.POSTGRES, persistence_location="sqlite")

    def test_schema_declares_normalized_domains_and_immutable_evidence(self) -> None:
        schema = "\n".join(INITIAL_SCHEMA_SQL)
        for table in ("instruments", "dataset_versions", "market_data_provenance", "strategy_versions", "quant_validation_artifacts", "validation_packages", "paper_order_intents", "fills", "audit_events"):
            self.assertIn(f"CREATE TABLE {table}", schema)
        self.assertIn("NUMERIC", schema)
        self.assertIn("REFERENCES", schema)
        self.assertIn("quant_validation_artifacts", IMMUTABLE_TABLES)
        self.assertIn("validation_package_artifacts", IMMUTABLE_TABLES)
        self.assertTrue(drop_schema_sql())

    def test_backfill_dry_run_reports_counts_and_checksums_without_target_writes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE paper_orders (id TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO paper_orders VALUES ('one')")
            connection.commit(); connection.close()
            inspected = inspect_sqlite_source(path)
            dry_run = backfill_sqlite_to_postgres(path, "postgresql://not-used", dry_run=True)
            self.assertEqual(inspected.row_counts["paper_orders"], 1)
            self.assertEqual(inspected.checksums, dry_run.checksums)
            self.assertTrue(dry_run.dry_run)
            with self.assertRaisesRegex(Exception, "identity_mapping"):
                backfill_sqlite_to_postgres(path, "postgresql://example", dry_run=False)


if __name__ == "__main__":
    unittest.main()
