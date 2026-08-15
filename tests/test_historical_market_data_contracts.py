"""Local contracts for the Cycle 11 historical data pipeline."""

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from trade_platform.historical_market_data import (
    AdjustmentStatus,
    AuthorizedHistoricalSource,
    HistoricalDataAuthorizationError,
    HistoricalMarketDataError,
    ObservationKind,
    RawHistoricalObservation,
    normalize_payload,
)


class HistoricalMarketDataContractTests(unittest.TestCase):
    def test_normalization_rejects_impossible_bars_without_repair(self) -> None:
        normalized, issues = normalize_payload(
            ObservationKind.OHLCV,
            {"interval": "1d", "open": "10", "high": "9", "low": "8", "close": "10", "volume": "-1"},
        )
        self.assertEqual(normalized["close"], "10")
        self.assertEqual(issues, ("impossible_ohlc", "negative_volume"))

    def test_source_requires_explicit_authorization_reference(self) -> None:
        now = datetime(2024, 1, 1, tzinfo=UTC)
        source = AuthorizedHistoricalSource(
            "provider", "dataset", "PROVIDER:ID", "terms-v1", "", now, now
        )
        with self.assertRaises(HistoricalDataAuthorizationError):
            source.validate()

    def test_raw_observation_requires_pit_timestamp_semantics(self) -> None:
        now = datetime(2024, 1, 1, tzinfo=UTC)
        observation = RawHistoricalObservation(
            uuid4(), ObservationKind.OHLCV, "id", "SYM", "XNAS", now, now, now,
            AdjustmentStatus.RAW, 0, "fixture://raw", {"close": float("nan")},
        )
        with self.assertRaisesRegex(HistoricalMarketDataError, "canonical_json"):
            observation.validate()
