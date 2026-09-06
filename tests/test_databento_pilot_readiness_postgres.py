"""Real PostgreSQL evidence that the Databento pilot source registers through the
EXISTING historical-source authority (no second registry) and that a
readiness-checked configuration built from that persisted source is usable.
"""

import os
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class DatabentoPilotSourceRegistrationPostgresTests(unittest.TestCase):
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
        from uuid import uuid4

        from trade_platform.historical_market_data import PostgresHistoricalMarketDataPipeline
        from trade_platform.persistence import PostgresDatabase

        self.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.pipeline = PostgresHistoricalMarketDataPipeline(self.database)
        self.suffix = uuid4().hex[:8]

    def tearDown(self) -> None:
        self.database.close()

    def test_databento_pilot_source_registers_persists_and_rejects_a_duplicate(self) -> None:
        from trade_platform.historical_market_data import (
            AuthorizedHistoricalSource,
            HistoricalDataAuthorizationError,
        )

        registered_at = datetime(2026, 1, 1, tzinfo=UTC)
        namespace = f"DATABENTO:INSTRUMENT_ID:{self.suffix}"
        source = AuthorizedHistoricalSource(
            "databento", f"databento-us-equities-pilot-{self.suffix}", namespace,
            "databento-terms-v1", f"test-authorization://3g1c-pilot/{self.suffix}",
            registered_at, registered_at,
        )
        self.pipeline.register_source(source)

        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT provider, dataset_name, provider_identifier_namespace, provider_terms_version, "
                "authorization_reference, asset_scope FROM historical_data_sources WHERE source_id=%s",
                (source.source_id,),
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(
            row,
            ("databento", f"databento-us-equities-pilot-{self.suffix}", namespace,
             "databento-terms-v1", f"test-authorization://3g1c-pilot/{self.suffix}", "US_EQUITIES_ETFS"),
        )

        # Re-registering the identical (provider, dataset_name, provider_terms_version)
        # identity is rejected, not silently duplicated -- prevents an accidental
        # retry from creating a second authorization record for the same rights grant.
        with self.assertRaises(HistoricalDataAuthorizationError):
            self.pipeline.register_source(source)

    def test_a_registered_pilot_source_passes_full_readiness_assessment(self) -> None:
        from trade_platform.data_providers import ProviderConfiguration
        from trade_platform.databento_pilot_readiness import (
            ActivationAttestation,
            ActivationChecklistCode,
            DatabentoPilotConfiguration,
            assess_pilot_readiness,
        )
        from trade_platform.historical_market_data import AuthorizedHistoricalSource

        secret_env_var = f"DATABENTO_PILOT_PG_TEST_KEY_{self.suffix}"  # pragma: allowlist secret
        os.environ[secret_env_var] = "fake-pilot-key"  # pragma: allowlist secret
        self.addCleanup(os.environ.pop, secret_env_var, None)

        registered_at = datetime(2026, 1, 1, tzinfo=UTC)
        namespace = f"DATABENTO:INSTRUMENT_ID:{self.suffix}"
        source = AuthorizedHistoricalSource(
            "databento", f"databento-us-equities-pilot-{self.suffix}", namespace,
            "databento-terms-v1", f"test-authorization://3g1c-pilot/{self.suffix}",
            registered_at, registered_at,
        )
        self.pipeline.register_source(source)

        config = DatabentoPilotConfiguration(
            source=source,
            provider_configuration=ProviderConfiguration(
                "databento", "https://hist.databento.com", terms_accepted=True,
                secret_reference=f"env:{secret_env_var}",
            ),
            symbols=("AAPL", "MSFT", "SPY"), dataset="EQUS.SUMMARY", schema="ohlcv-1d",
            start=registered_at.date(), end=(registered_at + timedelta(days=180)).date(),
            chunk_size=timedelta(days=31), cost_ceiling_usd=Decimal("125"),
            estimated_cost_usd=Decimal("5"), execution_approved=True,
            attestation=ActivationAttestation(confirmed=frozenset(ActivationChecklistCode)),
        )

        class _ForbiddenTransport:
            def post(self, *args: object, **kwargs: object) -> object:
                raise AssertionError("no network call is permitted in this readiness test")

            def get(self, *args: object, **kwargs: object) -> object:
                raise AssertionError("no network call is permitted in this readiness test")

        report = assess_pilot_readiness(config, transport=_ForbiddenTransport())
        self.assertTrue(report.ready)
        self.assertTrue(len(report.chunk_plan) >= 1)


if __name__ == "__main__":
    unittest.main()
