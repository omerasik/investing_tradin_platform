import unittest
from datetime import timedelta
from decimal import Decimal

from trade_platform.domain import DataProcessingStatus, OHLCVBar, utc_now
from trade_platform.market_data import (
    DataQualityError,
    FixtureBarProvider,
    SQLiteBarStore,
    assess_bars,
    ingest_from_provider,
)


def bar(minutes: int = 0, **changes: object) -> OHLCVBar:
    event = utc_now() + timedelta(minutes=minutes)
    values: dict[str, object] = {"instrument_id": "US:NYSE:SPY", "interval": "1m", "event_at": event, "effective_at": event, "ingested_at": event, "open": Decimal("100"), "high": Decimal("101"), "low": Decimal("99"), "close": Decimal("100.5"), "volume": Decimal("1000"), "provider": "fixture", "source_identifier": f"bar-{minutes}", "original_timezone": "UTC"}
    values.update(changes)
    return OHLCVBar(**values)


class MarketDataTests(unittest.TestCase):
    def test_valid_bars_get_full_quality_score(self) -> None:
        assessed = assess_bars([bar(0), bar(1)])
        self.assertTrue(all(item.processing_status is DataProcessingStatus.VALIDATED for item in assessed))
        self.assertEqual(assessed[0].quality_score, Decimal("1"))

    def test_invalid_prices_are_rejected(self) -> None:
        assessed = assess_bars([bar(close=Decimal("-1"))])
        self.assertEqual(assessed[0].processing_status, DataProcessingStatus.REJECTED)
        self.assertLess(assessed[0].quality_score, Decimal("1"))

    def test_duplicate_timestamp_is_rejected(self) -> None:
        first = bar(); duplicate = bar(event_at=first.event_at, effective_at=first.effective_at, ingested_at=first.ingested_at)
        self.assertEqual(assess_bars([first, duplicate])[1].processing_status, DataProcessingStatus.REJECTED)

    def test_store_refuses_invalid_batch_and_versions_valid_input(self) -> None:
        store = SQLiteBarStore()
        with self.assertRaises(DataQualityError):
            store.ingest([bar(volume=Decimal("-1"))])
        version = store.ingest([bar(0), bar(1)])
        self.assertEqual(len(version), 64)

    def test_as_of_query_prevents_future_data_leakage(self) -> None:
        store = SQLiteBarStore(); first, future = bar(0), bar(1)
        store.ingest([first, future])
        visible = store.available_as_of(first.instrument_id, first.interval, first.ingested_at)
        self.assertEqual([item.source_identifier for item in visible], [first.source_identifier])

    def test_fixture_provider_uses_narrow_instrument_and_time_range(self) -> None:
        first, second = bar(0), bar(1)
        provider = FixtureBarProvider([first, second])
        version = ingest_from_provider(provider, SQLiteBarStore(), first.instrument_id, first.interval, first.event_at, first.event_at)
        self.assertEqual(len(version), 64)

    def test_provider_provenance_mismatch_is_rejected(self) -> None:
        mismatched = bar(provider="wrong")
        with self.assertRaises(DataQualityError):
            ingest_from_provider(FixtureBarProvider([mismatched]), SQLiteBarStore(), mismatched.instrument_id, mismatched.interval, mismatched.event_at, mismatched.event_at)


if __name__ == "__main__":
    unittest.main()
