"""Real PostgreSQL evidence for the Cycle 11 provider-neutral vertical slice."""

import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class HistoricalMarketDataPostgresTests(unittest.TestCase):
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

    def test_raw_normalized_dataset_and_leakage_safe_restart_query(self) -> None:
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            AuthorizedHistoricalSource,
            HistoricalDataQualityError,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            QualityStatus,
            RawHistoricalObservation,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.professional_instruments import (
            IdentifierMapping,
            IdentifierSourceKind,
            PostgresProfessionalInstrumentMaster,
            mvp_instrument_universe,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        master = PostgresProfessionalInstrumentMaster(database)
        registered_at = datetime(2023, 1, 1, tzinfo=UTC)
        instrument = replace(
            mvp_instrument_universe(registered_at)[0],
            instrument_id="US:XNAS:CYCLE11_AAPL",
            canonical_symbol="CYCLE11_AAPL",
        )
        master.register(instrument)
        master.add_identifier_mapping(
            IdentifierMapping(
                instrument.instrument_id, IdentifierSourceKind.PROVIDER,
                "CYCLE11:SECURITY_ID", "provider-aapl-1", registered_at, None,
                registered_at, "fixture://cycle11/provider-map",
            )
        )

        pipeline = PostgresHistoricalMarketDataPipeline(database)
        source = AuthorizedHistoricalSource(
            "cycle11_fixture", "us-equities-fixture", "CYCLE11:SECURITY_ID",
            "test-terms-v1", "test-authorization://cycle11-only", registered_at,
            registered_at,
        )
        pipeline.register_source(source)
        event = datetime(2024, 1, 2, 21, tzinfo=UTC)
        raw = [
            RawHistoricalObservation(
                source.source_id, ObservationKind.OHLCV, "provider-aapl-1", "AAPL",
                "XNAS", event, event, event + timedelta(hours=1), AdjustmentStatus.RAW,
                0, "fixture://cycle11/bar/rev0",
                {"interval": "1d", "open": "100", "high": "102", "low": "99", "close": "101", "volume": "1000"},
            ),
            RawHistoricalObservation(
                source.source_id, ObservationKind.OHLCV, "provider-aapl-1", "AAPL",
                "XNAS", event, event, event + timedelta(hours=2), AdjustmentStatus.RAW,
                1, "fixture://cycle11/bar/rev1",
                {"interval": "1d", "open": "100", "high": "103", "low": "99", "close": "102", "volume": "1100"},
            ),
            RawHistoricalObservation(
                source.source_id, ObservationKind.OHLCV, "provider-aapl-1", "AAPL",
                "XNAS", event + timedelta(days=1), event + timedelta(days=1),
                event + timedelta(days=1, hours=1), AdjustmentStatus.LATEST_ADJUSTED,
                0, "fixture://cycle11/bar/latest-adjusted",
                {"interval": "1d", "open": "50", "high": "51", "low": "49", "close": "50", "volume": "2200"},
            ),
            RawHistoricalObservation(
                source.source_id, ObservationKind.DIVIDEND, "provider-aapl-1", "AAPL",
                "XNAS", event + timedelta(days=2), event + timedelta(days=5),
                event + timedelta(days=2, hours=1), AdjustmentStatus.AS_REPORTED,
                0, "fixture://cycle11/dividend", {"amount": "0.24", "currency": "USD"},
            ),
            RawHistoricalObservation(
                source.source_id, ObservationKind.SPLIT, "provider-aapl-1", "AAPL",
                "XNAS", event + timedelta(days=3), event + timedelta(days=6),
                event + timedelta(days=3, hours=1), AdjustmentStatus.AS_REPORTED,
                0, "fixture://cycle11/split", {"ratio": "2"},
            ),
            RawHistoricalObservation(
                source.source_id, ObservationKind.SYMBOL_CHANGE, "provider-aapl-1", "AAPL",
                "XNAS", event + timedelta(days=4), event + timedelta(days=7),
                event + timedelta(days=4, hours=1), AdjustmentStatus.AS_REPORTED,
                0, "fixture://cycle11/symbol", {"old_symbol": "AAPL", "new_symbol": "APPLX"},
            ),
            RawHistoricalObservation(
                source.source_id, ObservationKind.DELISTING, "provider-aapl-1", "APPLX",
                "XNAS", event + timedelta(days=5), event + timedelta(days=8),
                event + timedelta(days=5, hours=1), AdjustmentStatus.AS_REPORTED,
                0, "fixture://cycle11/delisting", {"reason": "fixture merger"},
            ),
            RawHistoricalObservation(
                source.source_id, ObservationKind.OHLCV, "provider-aapl-1", "AAPL",
                "XNAS", event + timedelta(days=6), event + timedelta(days=6),
                event + timedelta(days=6, hours=1), AdjustmentStatus.RAW,
                0, "fixture://cycle11/bad-bar",
                {"interval": "1d", "open": "10", "high": "9", "low": "8", "close": "10", "volume": "-1"},
            ),
        ]
        raw_ids = pipeline.capture_raw(raw)
        normalized_at = event + timedelta(days=7)
        normalized = tuple(
            pipeline.normalize(raw_id, "us-equity-normalizer-v1", normalized_at)
            for raw_id in raw_ids
        )
        self.assertEqual(normalized[-1].quality_status, QualityStatus.REJECTED)
        self.assertIn("impossible_ohlc", normalized[-1].quality_issues)
        with self.assertRaises(HistoricalDataQualityError):
            pipeline.seal_dataset(
                source.source_id, "bad-v1", "us-equity-normalizer-v1",
                tuple(item.normalized_observation_id for item in normalized),
                normalized_at + timedelta(hours=1),
            )

        dataset = pipeline.seal_dataset(
            source.source_id, "cycle11-v1", "us-equity-normalizer-v1",
            tuple(item.normalized_observation_id for item in normalized[:-1]),
            normalized_at + timedelta(hours=1),
        )
        before_seal = pipeline.research_query(
            dataset.dataset_version_id, instrument.instrument_id, event,
            event + timedelta(days=10), normalized_at,
        )
        self.assertEqual(before_seal, ())
        results = pipeline.research_query(
            dataset.dataset_version_id, instrument.instrument_id, event,
            event + timedelta(days=10), normalized_at + timedelta(hours=2),
        )
        self.assertEqual(len(results), 5)
        self.assertEqual(results[0].revision, 1)
        self.assertEqual(results[0].normalized_value["close"], "102")
        self.assertEqual(
            {item.observation_kind for item in results},
            {ObservationKind.OHLCV, ObservationKind.DIVIDEND, ObservationKind.SPLIT,
             ObservationKind.SYMBOL_CHANGE, ObservationKind.DELISTING},
        )
        self.assertTrue(all(item.data_version == "cycle11-v1" for item in results))
        explicit_latest = pipeline.research_query(
            dataset.dataset_version_id, instrument.instrument_id, event,
            event + timedelta(days=10), normalized_at + timedelta(hours=2),
            allow_latest_adjusted=True,
        )
        self.assertEqual(len(explicit_latest), 6)

        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE historical_raw_observations SET provider_symbol='MUTATED' WHERE raw_observation_id=%s",
                (raw_ids[0],),
            )
        database.close()
        reopened = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        recovered = PostgresHistoricalMarketDataPipeline(reopened).research_query(
            dataset.dataset_version_id, instrument.instrument_id, event,
            event + timedelta(days=10), normalized_at + timedelta(hours=2),
        )
        self.assertEqual(recovered, results)
        reopened.close()
