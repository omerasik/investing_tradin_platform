"""PostgreSQL evidence for append-only historical provider-ingestion checkpoints."""

import os
import unittest
from datetime import UTC, datetime, timedelta


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ProviderIngestionPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_append_only_checkpoint_latest_read_preserves_error_and_stale_evidence(self) -> None:
        from trade_platform.data_providers import ProviderOperationalStatus
        from trade_platform.historical_market_data import (
            AuthorizedHistoricalSource,
            PostgresHistoricalMarketDataPipeline,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.provider_ingestion import (
            PostgresHistoricalIngestionCheckpointStore,
            ProviderIngestionCheckpoint,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        now = datetime(2026, 8, 16, 12, tzinfo=UTC)
        source = AuthorizedHistoricalSource(
            "cycle16_fixture", "checkpoint-fixture", "CYCLE16:SECURITY_ID", "test-terms-v1",
            "test-authorization://cycle16-only", now, now,
        )
        PostgresHistoricalMarketDataPipeline(database).register_source(source)
        store = PostgresHistoricalIngestionCheckpointStore(database)
        scope = {"instrument": "US:XNAS:CYCLE16_AAPL", "interval": "1d"}
        error = ProviderIngestionCheckpoint(
            source.source_id, "fixture_raw", scope, ProviderOperationalStatus.ERROR,
            "fixture-v1", "page-2", 100, "provider_http_status:429", now,
        )
        stale = ProviderIngestionCheckpoint(
            source.source_id, "fixture_raw", scope, ProviderOperationalStatus.STALE,
            "fixture-v1", None, 120, None, now + timedelta(minutes=1),
        )
        store.record(error)
        store.record(stale)

        self.assertEqual(
            store.latest(source.source_id, "fixture_raw", scope, now + timedelta(minutes=2)), stale
        )
        database.close()


if __name__ == "__main__":
    unittest.main()
