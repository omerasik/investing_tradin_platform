"""Runs in CI or locally only when POSTGRES_TEST_DSN names an ephemeral database."""

import os
import unittest


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

    def tearDown(self) -> None:
        self.connection.close()

    def test_migration_creates_schema_and_immutable_constraint(self) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.quant_validation_artifacts')")
            self.assertEqual(cursor.fetchone()[0], "quant_validation_artifacts")
            cursor.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'quant_validation_artifacts'::regclass")
            self.assertIn("quant_validation_artifacts_immutable", {row[0] for row in cursor.fetchall()})


if __name__ == "__main__":
    unittest.main()
