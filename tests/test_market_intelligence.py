import unittest
from datetime import timedelta
from decimal import Decimal

from trade_platform.domain import utc_now
from trade_platform.market_intelligence import (
    MarketIntelligenceValidationError,
    SourceProfile,
    SQLiteNewsEventStore,
    assess_event,
    fixture_event,
)


class MarketIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.published_at = utc_now() - timedelta(hours=1)
        self.event = fixture_event("fixture-news", "item-1", "US:NYSE:SPY", "Fixture event", self.published_at, Decimal("0.25"))

    def test_assessment_exposes_uncertainty_and_requires_license(self) -> None:
        assessment = assess_event(self.event, SourceProfile("fixture-news", "Fixture", True, Decimal("0.75")))
        self.assertTrue(assessment.eligible_for_research)
        self.assertEqual(assessment.confidence, Decimal("0.600"))
        self.assertEqual(assessment.uncertainty, Decimal("0.400"))
        blocked = assess_event(self.event, SourceProfile("fixture-news", "Fixture", False, Decimal("0.75")))
        self.assertFalse(blocked.eligible_for_research)
        self.assertEqual(blocked.reason, "license_not_approved")

    def test_store_is_append_only_unique_by_provider_item(self) -> None:
        store = SQLiteNewsEventStore()
        store.append(self.event)
        with self.assertRaisesRegex(MarketIntelligenceValidationError, "duplicate_source_event"):
            store.append(self.event)

    def test_point_in_time_query_excludes_future_ingest(self) -> None:
        store = SQLiteNewsEventStore()
        future = fixture_event("fixture-news", "item-2", "US:NYSE:SPY", "Future ingestion", self.published_at, Decimal("0.1"))
        future = type(future)(future.event_id, future.source_id, future.source_item_id, future.entity_id, future.headline, future.source_url, future.published_at, utc_now() + timedelta(hours=1), future.topics, future.sentiment, future.extraction_confidence, future.data_version)
        store.append(self.event); store.append(future)
        self.assertEqual([event.source_item_id for event in store.for_entity_as_of("US:NYSE:SPY", utc_now())], ["item-1"])

    def test_invalid_source_and_event_are_rejected(self) -> None:
        with self.assertRaisesRegex(MarketIntelligenceValidationError, "invalid_source_profile"):
            SourceProfile("", "Fixture", True, Decimal("0.5"))
        with self.assertRaisesRegex(MarketIntelligenceValidationError, "invalid_news_event"):
            fixture_event("fixture-news", "bad", "US:NYSE:SPY", "", self.published_at)


if __name__ == "__main__":
    unittest.main()
