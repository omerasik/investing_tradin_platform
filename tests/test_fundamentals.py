from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trade_platform.fundamentals import FundamentalDataError, FundamentalFact, SQLiteFundamentalStore, StatementType


UTC = timezone.utc
PERIOD_START = datetime(2025, 1, 1, tzinfo=UTC)
PERIOD_END = datetime(2025, 3, 31, 23, 59, tzinfo=UTC)
FILED = datetime(2025, 5, 1, 12, tzinfo=UTC)


def fact(value: str, revision: int = 0, *, filed_at: datetime = FILED, ingested_at: datetime | None = None) -> FundamentalFact:
    return FundamentalFact("US:NASDAQ:ACME", StatementType.INCOME_STATEMENT, "revenue", PERIOD_START, PERIOD_END, filed_at, filed_at, ingested_at or filed_at, Decimal(value), "USD", "USD", "fixture-filings", "acme-2025q1-revenue", revision, Decimal(value) / Decimal("1000000"), "millions-v1")


class FundamentalStoreTests(unittest.TestCase):
    def test_as_of_query_uses_only_available_filing_and_revision(self) -> None:
        store = SQLiteFundamentalStore()
        initial = fact("1000000")
        revision_time = FILED + timedelta(days=30)
        revised = fact("900000", 1, filed_at=revision_time)
        delayed = fact("800000", 2, filed_at=revision_time + timedelta(days=1), ingested_at=revision_time + timedelta(days=4))
        store.ingest([initial, revised, delayed])
        self.assertEqual([item.as_reported_value for item in store.available_as_of("US:NASDAQ:ACME", FILED, fact_name="revenue")], [Decimal("1000000")])
        self.assertEqual([item.as_reported_value for item in store.available_as_of("US:NASDAQ:ACME", revision_time, fact_name="revenue")], [Decimal("900000")])
        self.assertEqual([item.as_reported_value for item in store.available_as_of("US:NASDAQ:ACME", delayed.ingested_at, fact_name="revenue")], [Decimal("800000")])

    def test_as_reported_standardized_and_history_survive_reopen(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fundamentals.sqlite"
            store = SQLiteFundamentalStore(path)
            store.ingest([fact("1000000"), fact("900000", 1, filed_at=FILED + timedelta(days=30))])
            store.close()
            reopened = SQLiteFundamentalStore(path)
            try:
                history = reopened.history("US:NASDAQ:ACME", "revenue", PERIOD_END)
                self.assertEqual([(item.as_reported_value, item.standardized_value, item.standardization_version) for item in history], [(Decimal("1000000"), Decimal("1"), "millions-v1"), (Decimal("900000"), Decimal("0.9"), "millions-v1")])
            finally:
                reopened.close()

    def test_invalid_standardization_semantics_and_duplicates_are_rejected(self) -> None:
        store = SQLiteFundamentalStore()
        invalid = FundamentalFact("US:NASDAQ:ACME", StatementType.INCOME_STATEMENT, "revenue", PERIOD_START, PERIOD_END, FILED, FILED, FILED, Decimal("1"), "USD", "USD", "fixture", "x", standardized_value=Decimal("1"))
        with self.assertRaisesRegex(FundamentalDataError, "standardized_value_requires_version"):
            store.ingest([invalid])
        store.ingest([fact("1000000")])
        with self.assertRaisesRegex(FundamentalDataError, "duplicate"):
            store.ingest([fact("999999")])

