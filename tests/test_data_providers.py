import unittest
from dataclasses import replace
from datetime import datetime, time, timezone
from decimal import Decimal

from trade_platform.data_providers import (
    BarPage,
    FallbackHistoricalProvider,
    HttpResponse,
    InMemoryBarCache,
    ProviderCapabilities,
    ProviderConfiguration,
    ProviderConfigurationError,
    ProviderError,
    ProviderHealthRegistry,
    RetryPolicy,
    StooqDailyCsvProvider,
    collect_pages,
)
from trade_platform.domain import OHLCVBar
from trade_platform.market_data import SQLiteBarStore, ingest_from_provider

NOW = datetime(2025, 1, 3, tzinfo=timezone.utc)
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = datetime(2025, 1, 4, tzinfo=timezone.utc)


class Responses:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.calls = 0

    def get(self, url: str, timeout_seconds: float) -> HttpResponse:
        self.calls += 1
        return self.responses.pop(0)


def bar() -> OHLCVBar:
    return OHLCVBar("US:NYSE:SPY", "1d", NOW, NOW, NOW, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), Decimal("1"), "test", "id", "UTC")


class StubProvider:
    capabilities = ProviderCapabilities("stub", frozenset({"ETF"}), frozenset({"1d"}), True, False, False, False, None, None, "TEST")

    def __init__(self, name: str, outcome: list[OHLCVBar] | Exception) -> None:
        self.name = name
        self.outcome = outcome
        self.calls = 0

    def fetch_bars(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class PagedProvider:
    name = "paged"

    def __init__(self) -> None:
        self.cursors: list[str | None] = []

    def fetch_page(self, instrument_id: str, interval: str, start: datetime, end: datetime, cursor: str | None) -> BarPage:
        self.cursors.append(cursor)
        return BarPage((bar(),), "next" if cursor is None else None)


class DataProviderTests(unittest.TestCase):
    def test_public_csv_adapter_requires_terms_then_retries_and_normalizes_time(self) -> None:
        csv = "Date,Open,High,Low,Close,Volume\n2025-01-02,100,102,99,101,123\n"
        transport = Responses(HttpResponse(429, "", {"Retry-After": "2"}), HttpResponse(200, csv))
        sleeps: list[float] = []
        provider = StooqDailyCsvProvider(ProviderConfiguration("stooq_daily", "https://stooq.com", terms_accepted=False), symbol_map={"US:NYSE:SPY": "spy.us"}, market_timezone="America/New_York", market_close=time(16), transport=transport, retry_policy=RetryPolicy(2), now=lambda: NOW, sleep=sleeps.append)
        with self.assertRaisesRegex(ProviderConfigurationError, "terms_not_accepted"):
            provider.fetch_bars("US:NYSE:SPY", "1d", START, END)
        provider = StooqDailyCsvProvider(ProviderConfiguration("stooq_daily", "https://stooq.com", terms_accepted=True), symbol_map={"US:NYSE:SPY": "spy.us"}, market_timezone="America/New_York", market_close=time(16), transport=transport, retry_policy=RetryPolicy(2), now=lambda: NOW, sleep=sleeps.append)
        result = provider.fetch_bars("US:NYSE:SPY", "1d", START, END)
        self.assertEqual((transport.calls, sleeps, result[0].event_at, result[0].provider, result[0].source_identifier), (2, [2.0], datetime(2025, 1, 2, 21, tzinfo=timezone.utc), "stooq_daily", "spy.us:2025-01-02"))

    def test_fallback_cache_health_and_pagination_are_explicit(self) -> None:
        health = ProviderHealthRegistry(lambda: NOW)
        fallback = FallbackHistoricalProvider((StubProvider("bad", ProviderError("outage")), StubProvider("good", [bar()])), health, InMemoryBarCache(lambda: NOW))
        self.assertEqual(fallback.fetch_bars("US:NYSE:SPY", "1d", START, END)[0].provider, "test")
        self.assertFalse(health.get("bad").healthy)
        self.assertTrue(health.get("good").healthy)
        paged = PagedProvider()
        self.assertEqual(len(collect_pages(paged, "US:NYSE:SPY", "1d", START, END)), 2)
        self.assertEqual(paged.cursors, [None, "next"])

    def test_fallback_preserves_selected_provider_provenance_for_ingestion(self) -> None:
        provider = StubProvider("good", [replace(bar(), provider="good")])
        provider.capabilities = ProviderCapabilities("good", frozenset({"ETF"}), frozenset({"1d"}), True, False, False, False, None, None, "TEST")
        fallback = FallbackHistoricalProvider((provider,), ProviderHealthRegistry(lambda: NOW), InMemoryBarCache(lambda: NOW))
        self.assertTrue(ingest_from_provider(fallback, SQLiteBarStore(), "US:NYSE:SPY", "1d", START, END))

    def test_provider_errors_do_not_silently_fallback_or_allow_invalid_config(self) -> None:
        with self.assertRaisesRegex(ProviderConfigurationError, "https"):
            ProviderConfiguration("x", "http://unsafe.example").validate()
        with self.assertRaisesRegex(ProviderError, "all_providers_failed"):
            FallbackHistoricalProvider((StubProvider("bad", ProviderError("outage")),), ProviderHealthRegistry(lambda: NOW), InMemoryBarCache(lambda: NOW)).fetch_bars("US:NYSE:SPY", "1d", START, END)
