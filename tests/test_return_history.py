import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from trade_platform.broker_adapter import BrokerConfiguration, BrokerMode
from trade_platform.data_providers import HttpResponse, ProviderConfiguration
from trade_platform.return_history import AccountPerformanceSnapshot, HttpsAccountPerformanceProvider, PortfolioReturnObservation, PortfolioReturnPage, ReturnHistoryError, ReturnIngestionCadence, ReturnIngestionRetryPolicy, ReturnProviderTransientError, SandboxAccountPerformanceProvider, SQLitePortfolioReturnStore


class PortfolioReturnHistoryTests(unittest.TestCase):
    def test_append_only_history_obeys_period_and_ingestion_as_of_semantics(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        store = SQLitePortfolioReturnStore()
        first = PortfolioReturnObservation(uuid4(), "paper", now - timedelta(days=3), now - timedelta(days=2), Decimal("-0.02"), "paper-oms", "return-1", now - timedelta(days=2))
        delayed = PortfolioReturnObservation(uuid4(), "paper", now - timedelta(days=2), now - timedelta(days=1), Decimal("0.01"), "paper-oms", "return-2", now + timedelta(days=1))
        future = PortfolioReturnObservation(uuid4(), "paper", now, now + timedelta(days=1), Decimal("0.03"), "paper-oms", "return-3", now + timedelta(days=1))
        store.append(first); store.append(delayed); store.append(future)
        self.assertEqual(store.returns_as_of("paper", now), [Decimal("-0.02")])
        self.assertEqual(store.returns_as_of("paper", now + timedelta(days=1)), [Decimal("-0.02"), Decimal("0.01"), Decimal("0.03")])
        with self.assertRaisesRegex(ReturnHistoryError, "duplicate"):
            store.append(first)
        with self.assertRaisesRegex(ReturnHistoryError, "invalid"):
            store.append(PortfolioReturnObservation(uuid4(), "paper", now, now + timedelta(days=1), Decimal("-1"), "paper-oms", "invalid", now + timedelta(days=1)))
        store.close()

    def test_policy_window_freshness_and_provider_contract_are_enforced(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        store = SQLitePortfolioReturnStore()
        rows = [
            PortfolioReturnObservation(uuid4(), "paper", now - timedelta(days=3), now - timedelta(days=2), Decimal("-0.02"), "fixture", "one", now - timedelta(days=2)),
            PortfolioReturnObservation(uuid4(), "paper", now - timedelta(days=2), now - timedelta(hours=1), Decimal("0.01"), "fixture", "two", now - timedelta(hours=1)),
        ]
        for row in rows:
            store.append(row)
        self.assertEqual(store.returns_for_policy("paper", now, minimum_observations=2, window_observations=2, maximum_age_seconds=3600), [Decimal("-0.02"), Decimal("0.01")])
        evidence = store.evidence_for_policy("paper", now, minimum_observations=2, window_observations=2, maximum_age_seconds=3600)
        self.assertEqual(evidence.returns, [Decimal("-0.02"), Decimal("0.01")])
        self.assertEqual(evidence.observed_at, now - timedelta(hours=1))
        self.assertEqual(evidence.reference, store.evidence_for_policy("paper", now, minimum_observations=2, window_observations=2, maximum_age_seconds=3600).reference)
        with self.assertRaisesRegex(ReturnHistoryError, "stale"):
            store.returns_for_policy("paper", now + timedelta(hours=2), minimum_observations=2, window_observations=2, maximum_age_seconds=3600)
        with self.assertRaisesRegex(ReturnHistoryError, "insufficient"):
            store.returns_for_policy("paper", now, minimum_observations=3, window_observations=3, maximum_age_seconds=3600)
        store.close()

    def test_provider_ingestion_requires_matching_account_provider_and_range(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)

        class FixtureProvider:
            provider_name = "fixture"

            def fetch_returns(self, account_id, *, start, end):
                return [PortfolioReturnObservation(uuid4(), account_id, start, end, Decimal("0.01"), self.provider_name, "fixture-1", end)]

        class BadProvider(FixtureProvider):
            def fetch_returns(self, account_id, *, start, end):
                return [PortfolioReturnObservation(uuid4(), "other", start, end, Decimal("0.01"), self.provider_name, "fixture-2", end)]

        store = SQLitePortfolioReturnStore()
        self.assertEqual(store.ingest(FixtureProvider(), "paper", start=now - timedelta(days=1), end=now), 1)
        with self.assertRaisesRegex(ReturnHistoryError, "contract"):
            store.ingest(BadProvider(), "paper", start=now - timedelta(days=1), end=now)
        store.close()

    def test_sandbox_performance_provider_derives_attributable_returns_from_nav_marks(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        provider = SandboxAccountPerformanceProvider(BrokerConfiguration("sandbox", BrokerMode.SANDBOX_PAPER, "paper"))
        provider.record_snapshot(AccountPerformanceSnapshot("paper", now - timedelta(days=2), Decimal("100"), "nav-1", now - timedelta(days=2)))
        provider.record_snapshot(AccountPerformanceSnapshot("paper", now - timedelta(days=1), Decimal("102"), "nav-2", now - timedelta(days=1)))
        provider.record_snapshot(AccountPerformanceSnapshot("paper", now, Decimal("99.96"), "nav-3", now))
        returns = provider.fetch_returns("paper", start=now - timedelta(days=2), end=now)
        self.assertFalse(provider.capabilities.network_connected)
        self.assertEqual([item.return_fraction for item in returns], [Decimal("0.02"), Decimal("-0.02")])
        self.assertEqual(returns[0].source_reference, "nav-1->nav-2")
        store = SQLitePortfolioReturnStore()
        self.assertEqual(store.ingest(provider, "paper", start=now - timedelta(days=2), end=now), 2)
        self.assertEqual(store.returns_as_of("paper", now), [Decimal("0.02"), Decimal("-0.02")])
        with self.assertRaisesRegex(ReturnHistoryError, "start_snapshot"):
            provider.fetch_returns("paper", start=now - timedelta(days=2, seconds=-1), end=now)
        store.close()

    def test_ingestion_retries_pages_and_persists_run_and_health_evidence(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        row = PortfolioReturnObservation(uuid4(), "paper", now - timedelta(days=2), now - timedelta(days=1), Decimal("0.01"), "paged", "one", now - timedelta(days=1))
        class PagedProvider:
            provider_name = "paged"
            def __init__(self): self.calls = 0
            def fetch_return_page(self, account_id, *, start, end, cursor):
                self.calls += 1
                if self.calls == 1: raise ReturnProviderTransientError("throttled")
                return PortfolioReturnPage((row,), None)
        sleeps: list[float] = []; store = SQLitePortfolioReturnStore(); provider = PagedProvider()
        self.assertEqual(store.ingest(provider, "paper", start=now - timedelta(days=2), end=now, retry_policy=ReturnIngestionRetryPolicy(2, timedelta(seconds=2)), now=lambda: now, sleep=sleeps.append), 1)
        self.assertEqual(sleeps, [2.0]); self.assertEqual(store.provider_health("paged"), (now, True, None, 0))
        self.assertEqual(store.ingestion_runs("paper")[0][4:6], ("SUCCEEDED", 1))
        store.close()

    def test_https_provider_requires_credential_reference_and_normalizes_paged_json(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=timezone.utc); identity = uuid4()
        class Transport:
            def get(self, url, timeout_seconds):
                self.url = url
                return HttpResponse(200, '{"observations":[{"observation_id":"' + str(identity) + '","period_start":"2026-01-08T00:00:00+00:00","period_end":"2026-01-09T00:00:00+00:00","return_fraction":"0.01","source_reference":"remote-1","ingested_at":"2026-01-09T00:00:00+00:00"}],"next_cursor":"next"}')
        with self.assertRaisesRegex(ReturnHistoryError, "credential"):
            HttpsAccountPerformanceProvider(ProviderConfiguration("remote", "https://api.example"))
        transport = Transport(); provider = HttpsAccountPerformanceProvider(ProviderConfiguration("remote", "https://api.example", secret_reference="env:PERFORMANCE_TOKEN"), transport=transport)
        page = provider.fetch_return_page("paper", start=now - timedelta(days=2), end=now, cursor=None)
        self.assertEqual((page.observations[0].provider, page.next_cursor), ("remote", "next"))
        self.assertIn("/v1/accounts/paper/returns?", transport.url)

    def test_durable_cadence_identifies_due_and_overdue_return_ingestion(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=timezone.utc); store = SQLitePortfolioReturnStore()
        store.set_ingestion_cadence(ReturnIngestionCadence("paper", "fixture", timedelta(hours=1), timedelta(minutes=10), "risk-committee", now - timedelta(hours=2)))
        due = store.due_ingestion_cadences(now); self.assertEqual((due[0][0].provider, due[0][1], due[0][2]), ("fixture", None, True))
        class Provider:
            provider_name = "fixture"
            def fetch_returns(self, account_id, *, start, end): return [PortfolioReturnObservation(uuid4(), account_id, start, end, Decimal("0.01"), self.provider_name, "cadence", end)]
        store.ingest(Provider(), "paper", start=now - timedelta(hours=1), end=now, now=lambda: now)
        self.assertEqual(store.due_ingestion_cadences(now + timedelta(minutes=30)), [])
        due = store.due_ingestion_cadences(now + timedelta(hours=2)); self.assertTrue(due[0][2])
        store.close()


if __name__ == "__main__":
    unittest.main()
