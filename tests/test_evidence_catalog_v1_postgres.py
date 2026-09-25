"""Phase R5 UI-1: the evidence catalog's SQL runs read-only against the migrated schema."""

import os
import unittest


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class EvidenceCatalogPostgresTests(unittest.TestCase):
    def test_catalog_reads_every_catalog_table_in_a_read_only_transaction(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.bybit_public_archive_v1 import T2_PUBLICATION_LAG_SLOT_V1
        from trade_platform.evidence_catalog_v1 import NO_TIMING_AUTHORITY_V1
        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
        from trade_platform.persistence import PostgresDatabase

        dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(config, "head")
        database = PostgresDatabase(dsn)
        try:
            # Shared database: assert only what holds whatever other tests persisted.
            view = PostgresOperatorDashboardQueries(database).evidence_catalog()
        finally:
            database.close()
        self.assertEqual(3, len(view.timing_sources))
        self.assertEqual(len(view.t4_datasets), min(view.t4_dataset_total, 100))
        self.assertEqual(len(view.public_archive_datasets), min(view.public_archive_dataset_total, 100))
        self.assertTrue(all(item.publication_lag_slot == T2_PUBLICATION_LAG_SLOT_V1 for item in view.public_archive_datasets))
        self.assertTrue(all(item.distinct_knowledge_time_count <= item.observation_count for item in view.t4_datasets))
        ceilings = {item.tier_ceiling for item in view.historical_sources}
        self.assertTrue(ceilings <= {"T1_RETROSPECTIVE", "T4_FIRST_PARTY_CAPTURE", NO_TIMING_AUTHORITY_V1})


if __name__ == "__main__":
    unittest.main()
