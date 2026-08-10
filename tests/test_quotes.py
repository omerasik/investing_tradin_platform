import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from trade_platform.quotes import QuoteDataError, QuoteObservation, SQLiteQuoteStore


class QuoteStoreTests(unittest.TestCase):
    def test_current_quote_is_provider_attributed_and_converts_to_market_snapshot(self) -> None:
        now = datetime.now(timezone.utc); store = SQLiteQuoteStore()
        quote = QuoteObservation(uuid4(), "US:NYSE:SPY", Decimal("99.9"), Decimal("100"), Decimal("100"), Decimal("0.99"), "paper-feed", "quote-1", now, now)
        store.append(quote)
        selected = store.latest_as_of("US:NYSE:SPY", now)
        self.assertEqual(selected.market_snapshot().spread_fraction, Decimal("0.001"))
        self.assertEqual(selected.provider, "paper-feed")
        store.close()

    def test_future_ingested_quote_is_not_available_as_of_decision(self) -> None:
        now = datetime.now(timezone.utc); store = SQLiteQuoteStore()
        store.append(QuoteObservation(uuid4(), "US:NYSE:SPY", Decimal("99"), Decimal("100"), Decimal("100"), Decimal("1"), "feed", "quote-2", now, now + timedelta(minutes=1)))
        with self.assertRaisesRegex(QuoteDataError, "unavailable"):
            store.latest_as_of("US:NYSE:SPY", now)
        store.close()


if __name__ == "__main__":
    unittest.main()
