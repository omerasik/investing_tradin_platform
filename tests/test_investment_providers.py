from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trade_platform.data_providers import HttpResponse, ProviderCapabilities, ProviderConfiguration, ProviderConfigurationError, ProviderError, ProviderHealthRegistry, RetryPolicy
from trade_platform.investment_providers import (FallbackInvestmentEvidenceProvider, FixtureInvestmentEvidenceProvider,
    HttpsJsonInvestmentEvidenceProvider, InMemoryInvestmentEvidenceCache, InvestmentEvidencePage, InvestmentProviderHealthCadence, InvestmentProviderHealthMonitorJob, ProviderInvestmentFact, RetryingInvestmentEvidenceProvider, SecCompanyFactsProvider,
    collect_investment_evidence_pages, ingest_investment_provider_facts, persist_investment_provider_health, SQLiteInvestmentProviderHealthStore, synchronize_investment_provider_health_alerts, synchronize_investment_provider_health_cadence_alerts, synchronize_persisted_investment_provider_health_alerts)
from trade_platform.investments import SQLiteInvestmentStore
from trade_platform.operational_alerts import SQLiteOperationalAlertStore


NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def fact() -> ProviderInvestmentFact:
    return ProviderInvestmentFact("US:NASDAQ:ACME", "free_cash_flow", Decimal("100"), "USD", NOW - timedelta(days=1), NOW - timedelta(hours=1), "acme-q1-fcf", "https://example.test/acme/q1", 1)


class FailingProvider:
    name = "failing"
    capabilities = ProviderCapabilities(name, frozenset({"EQUITY"}), frozenset({"fundamental"}), True, False, False, True, None, None, "TEST")
    def __init__(self) -> None: self.calls = 0
    def fetch_facts(self, instrument_id: str, *, start: datetime, end: datetime) -> list[ProviderInvestmentFact]:
        self.calls += 1; raise ProviderError("outage")


class PagedProvider:
    name = "paged"
    def __init__(self) -> None: self.cursors: list[str | None] = []
    def fetch_page(self, instrument_id: str, *, start: datetime, end: datetime, cursor: str | None) -> InvestmentEvidencePage:
        self.cursors.append(cursor); return InvestmentEvidencePage((fact(),), "next" if cursor is None else None)


class Responses:
    def __init__(self, *responses: HttpResponse) -> None: self.responses, self.urls = list(responses), []
    def get(self, url: str, timeout_seconds: float) -> HttpResponse:
        self.urls.append(url); return self.responses.pop(0)


class SecResponses:
    def __init__(self, *responses: HttpResponse) -> None: self.responses, self.calls = list(responses), []
    def get(self, url: str, timeout_seconds: float, user_agent: str) -> HttpResponse:
        self.calls.append((url, user_agent)); return self.responses.pop(0)


class InvestmentProviderTests(unittest.TestCase):
    def test_fallback_health_cache_retry_and_provenance_bound_ingestion(self) -> None:
        bad, sleeps = FailingProvider(), []
        good = FixtureInvestmentEvidenceProvider("public-filings", (fact(),))
        retry = RetryingInvestmentEvidenceProvider(bad, retry_policy=RetryPolicy(2, timedelta(seconds=2)), sleep=sleeps.append)
        health = ProviderHealthRegistry(lambda: NOW)
        fallback = FallbackInvestmentEvidenceProvider((retry, good), health, InMemoryInvestmentEvidenceCache(lambda: NOW))
        store = SQLiteInvestmentStore()
        first = ingest_investment_provider_facts(store, fallback, "US:NASDAQ:ACME", start=NOW - timedelta(days=2), end=NOW, ingested_at=NOW)
        second = ingest_investment_provider_facts(store, fallback, "US:NASDAQ:ACME", start=NOW - timedelta(days=2), end=NOW, ingested_at=NOW)
        self.assertEqual((bad.calls, sleeps, health.get("failing").healthy, health.get("public-filings").healthy, first[0].source_reference, len(store.facts_as_of("US:NASDAQ:ACME", NOW)), second[0].fact_id), (2, [2.0], False, True, "investment-provider:public-filings:acme-q1-fcf:1", 1, first[0].fact_id))
        store.close()

    def test_pagination_and_future_availability_are_fail_closed(self) -> None:
        paged = PagedProvider()
        self.assertEqual(len(collect_investment_evidence_pages(paged, "US:NASDAQ:ACME", start=NOW - timedelta(days=2), end=NOW)), 2)
        self.assertEqual(paged.cursors, [None, "next"])
        store = SQLiteInvestmentStore(); provider = FixtureInvestmentEvidenceProvider("fixture", (fact(),))
        with self.assertRaisesRegex(Exception, "not_yet_ingestable"):
            ingest_investment_provider_facts(store, provider, "US:NASDAQ:ACME", start=NOW - timedelta(days=2), end=NOW, ingested_at=NOW - timedelta(days=2))
        store.close()

    def test_https_json_adapter_requires_terms_retries_normalizes_and_preserves_secret_reference(self) -> None:
        payload = '{"facts":[{"metric":"free_cash_flow","value":"100","unit":"USD","observed_at":"2026-06-30T20:00:00-04:00","available_at":"2026-07-01T00:30:00Z","source_identifier":"acme-q1","source_url":"https://filings.example/acme-q1","revision":2}]}'
        responses, sleeps = Responses(HttpResponse(429, "", {"Retry-After":"3"}), HttpResponse(200, payload)), []
        config = ProviderConfiguration("filings-api", "https://provider.example", terms_accepted=False, secret_reference="INVESTMENT_PROVIDER_TOKEN")
        provider = HttpsJsonInvestmentEvidenceProvider(config, transport=responses, retry_policy=RetryPolicy(2), sleep=sleeps.append)
        with self.assertRaisesRegex(ProviderConfigurationError, "terms_not_accepted"):
            provider.fetch_facts("US:NASDAQ:ACME", start=NOW - timedelta(days=2), end=NOW)
        config = ProviderConfiguration("filings-api", "https://provider.example", terms_accepted=True, secret_reference="INVESTMENT_PROVIDER_TOKEN")
        provider = HttpsJsonInvestmentEvidenceProvider(config, transport=responses, retry_policy=RetryPolicy(2), sleep=sleeps.append)
        facts = provider.fetch_facts("US:NASDAQ:ACME", start=NOW - timedelta(days=2), end=NOW)
        self.assertEqual((sleeps, facts[0].observed_at, facts[0].provider_name, provider.capabilities.credential_reference, len(responses.urls)), ([3.0], datetime(2026, 7, 1, 0, tzinfo=timezone.utc), "filings-api", "INVESTMENT_PROVIDER_TOKEN", 2))

    def test_https_json_adapter_rejects_non_https_source_or_naive_timestamp(self) -> None:
        body = '{"facts":[{"metric":"free_cash_flow","value":"1","unit":"USD","observed_at":"2026-07-01T00:00:00","available_at":"2026-07-01T00:00:00","source_identifier":"x","source_url":"http://bad"}]}'
        provider = HttpsJsonInvestmentEvidenceProvider(ProviderConfiguration("filings", "https://provider.example", terms_accepted=True), transport=Responses(HttpResponse(200, body)))
        with self.assertRaisesRegex(ProviderError, "source_url_requires_https"):
            provider.fetch_facts("US:NASDAQ:ACME", start=NOW - timedelta(days=2), end=NOW)

    def test_sec_companyfacts_public_adapter_requires_terms_and_uses_conservative_filing_availability(self) -> None:
        body = '{"facts":{"us-gaap":{"NetCashProvidedByUsedInOperatingActivities":{"units":{"USD":[{"end":"2026-06-30","filed":"2026-06-30","val":100,"accn":"0001","fy":2026}]}}}}}'
        transport = SecResponses(HttpResponse(429, "", {"Retry-After":"1"}), HttpResponse(200, body)); sleeps = []
        kwargs = {"cik_by_instrument":{"US:NASDAQ:ACME":"320193"}, "metric_concepts":{"free_cash_flow":("us-gaap", "NetCashProvidedByUsedInOperatingActivities")}, "user_agent":"Research Operator contact@example.test", "terms_accepted":False, "transport":transport, "retry_policy":RetryPolicy(2), "sleep":sleeps.append}
        provider = SecCompanyFactsProvider(**kwargs)
        with self.assertRaisesRegex(ProviderConfigurationError, "terms_not_accepted"):
            provider.fetch_facts("US:NASDAQ:ACME", start=NOW - timedelta(days=2), end=NOW)
        kwargs["terms_accepted"] = True; provider = SecCompanyFactsProvider(**kwargs)
        facts = provider.fetch_facts("US:NASDAQ:ACME", start=NOW - timedelta(days=2), end=NOW)
        self.assertEqual((facts[0].available_at, facts[0].source_identifier, facts[0].provider_name, sleeps, transport.calls[-1][1]), (datetime(2026, 7, 1, tzinfo=timezone.utc), "0000320193:us-gaap:NetCashProvidedByUsedInOperatingActivities:0001:2026-06-30", "sec_companyfacts", [1.0], "Research Operator contact@example.test"))

    def test_provider_health_monitor_alerts_failure_and_resolves_only_after_success(self) -> None:
        health = ProviderHealthRegistry(lambda: NOW); alerts = SQLiteOperationalAlertStore()
        health.failure("sec_companyfacts", "http_429")
        synchronize_investment_provider_health_alerts(health, alerts, ("sec_companyfacts",))
        opened = alerts.active()[0]
        self.assertEqual((opened.source, opened.code, opened.details["reason"]), ("investment_provider", "HEALTH_UNAVAILABLE", "http_429"))
        synchronize_investment_provider_health_alerts(health, alerts, ("sec_companyfacts",))
        self.assertEqual(len(alerts.active()), 1)
        health.success("sec_companyfacts")
        synchronize_investment_provider_health_alerts(health, alerts, ("sec_companyfacts",))
        self.assertEqual(alerts.active(), ())

    def test_provider_health_is_restart_safe_before_alert_synchronization(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "investment-health.sqlite"; health = ProviderHealthRegistry(lambda: NOW); health.failure("sec_companyfacts", "network")
            store = SQLiteInvestmentProviderHealthStore(path); persist_investment_provider_health(health, store, ("sec_companyfacts",)); store.close()
            reopened = SQLiteInvestmentProviderHealthStore(path); alerts = SQLiteOperationalAlertStore()
            synchronize_persisted_investment_provider_health_alerts(reopened, alerts, ("sec_companyfacts",))
            self.assertEqual((reopened.latest("sec_companyfacts").reason, alerts.active()[0].details["reason"]), ("network", "network"))
            reopened.close()

    def test_provider_health_cadence_marks_missing_or_stale_checks_overdue(self) -> None:
        health = ProviderHealthRegistry(lambda: NOW - timedelta(hours=2)); health.success("sec_companyfacts")
        store = SQLiteInvestmentProviderHealthStore(); store.set_cadence(InvestmentProviderHealthCadence("sec_companyfacts", timedelta(hours=1), timedelta(minutes=10), "data-committee")); persist_investment_provider_health(health, store, ("sec_companyfacts",))
        alerts = SQLiteOperationalAlertStore(); synchronize_investment_provider_health_cadence_alerts(store, alerts, NOW)
        self.assertEqual((store.due_cadences(NOW)[0][2:], alerts.active()[0].code), ((True, True), "HEALTH_CADENCE_OVERDUE"))
        health = ProviderHealthRegistry(lambda: NOW); health.success("sec_companyfacts"); persist_investment_provider_health(health, store, ("sec_companyfacts",)); synchronize_investment_provider_health_cadence_alerts(store, alerts, NOW)
        self.assertEqual(alerts.active(), ())

    def test_monitor_job_checks_only_due_providers_and_records_recovery_without_raw_error(self) -> None:
        clock = [NOW]; health = ProviderHealthRegistry(lambda: clock[0]); store = SQLiteInvestmentProviderHealthStore(); alerts = SQLiteOperationalAlertStore()
        store.set_cadence(InvestmentProviderHealthCadence("sec_companyfacts", timedelta(hours=1), timedelta(minutes=5), "data-committee")); job = InvestmentProviderHealthMonitorJob(health, store, alerts); calls = []
        due = job.run_due(NOW, lambda provider: (_ for _ in ()).throw(RuntimeError("token=do-not-record")))
        self.assertEqual((due, store.latest("sec_companyfacts").reason, alerts.active()[0].details["reason"]), (("sec_companyfacts",), "provider_health_check_failed", "provider_health_check_failed"))
        self.assertEqual(job.run_due(NOW + timedelta(minutes=30), lambda provider: calls.append(provider)), ())
        clock[0] = NOW + timedelta(hours=2); self.assertEqual(job.run_due(clock[0], lambda provider: calls.append(provider)), ("sec_companyfacts",))
        self.assertEqual((calls, alerts.active()), (["sec_companyfacts"], ()))


if __name__ == "__main__":
    unittest.main()
