"""Provider contracts for attributable long-horizon investment evidence.

This module deliberately has no execution dependencies.  It converts provider
responses into the immutable, point-in-time facts consumed by investment research.
"""

import json
import sqlite3
import time
from datetime import time as clock_time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .data_providers import HttpResponse, HttpTransport, ProviderCapabilities, ProviderConfiguration, ProviderConfigurationError, ProviderError, ProviderHealth, ProviderHealthRegistry, RetryPolicy, UrlLibTransport
from .investments import InvestmentValidationError, SQLiteInvestmentStore, SourceBackedFact
from .operational_alerts import AlertSeverity, AlertStatus, SQLiteOperationalAlertStore


@dataclass(frozen=True, slots=True)
class ProviderInvestmentFact:
    instrument_id: str
    metric: str
    value: Decimal
    unit: str
    observed_at: datetime
    available_at: datetime
    source_identifier: str
    source_url: str
    revision: int = 0
    provider_name: str = ""

    def validate(self) -> None:
        timestamps = (self.observed_at, self.available_at)
        if (not self.instrument_id or not self.metric or not self.unit or not self.source_identifier or not self.source_url
                or not self.value.is_finite() or self.revision < 0 or any(item.tzinfo is None or item.utcoffset() is None for item in timestamps)
                or self.available_at < self.observed_at):
            raise ProviderError("invalid_investment_provider_fact")


class InvestmentEvidenceProvider(Protocol):
    name: str
    capabilities: ProviderCapabilities

    def fetch_facts(self, instrument_id: str, *, start: datetime, end: datetime) -> list[ProviderInvestmentFact]: ...


@dataclass(frozen=True, slots=True)
class InvestmentEvidencePage:
    facts: tuple[ProviderInvestmentFact, ...]
    next_cursor: str | None


class PaginatedInvestmentEvidenceProvider(Protocol):
    name: str

    def fetch_page(self, instrument_id: str, *, start: datetime, end: datetime, cursor: str | None) -> InvestmentEvidencePage: ...


def collect_investment_evidence_pages(provider: PaginatedInvestmentEvidenceProvider, instrument_id: str, *, start: datetime, end: datetime, maximum_pages: int = 1000) -> list[ProviderInvestmentFact]:
    if not instrument_id or start.tzinfo is None or end.tzinfo is None or start > end or maximum_pages < 1:
        raise ProviderConfigurationError("invalid_investment_pagination_request")
    cursor: str | None = None; seen: set[str] = set(); facts: list[ProviderInvestmentFact] = []
    for _ in range(maximum_pages):
        page = provider.fetch_page(instrument_id, start=start, end=end, cursor=cursor)
        facts.extend(page.facts)
        if page.next_cursor is None: return facts
        if page.next_cursor in seen: raise ProviderError("investment_provider_pagination_cursor_loop")
        seen.add(page.next_cursor); cursor = page.next_cursor
    raise ProviderError("investment_provider_pagination_limit_exceeded")


@dataclass(frozen=True, slots=True)
class CachedInvestmentEvidence:
    expires_at: datetime
    facts: tuple[ProviderInvestmentFact, ...]


class InMemoryInvestmentEvidenceCache:
    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now; self._items: dict[tuple[str, str, datetime, datetime], CachedInvestmentEvidence] = {}

    def get(self, provider: str, instrument_id: str, start: datetime, end: datetime) -> list[ProviderInvestmentFact] | None:
        item = self._items.get((provider, instrument_id, start, end))
        return None if item is None or item.expires_at <= self._now() else list(item.facts)

    def put(self, provider: str, instrument_id: str, start: datetime, end: datetime, facts: list[ProviderInvestmentFact], ttl: timedelta) -> None:
        if ttl <= timedelta(0): raise ProviderConfigurationError("investment_evidence_cache_ttl_must_be_positive")
        self._items[(provider, instrument_id, start, end)] = CachedInvestmentEvidence(self._now() + ttl, tuple(facts))


class RetryingInvestmentEvidenceProvider:
    """Bounded retry wrapper for provider outages/throttling; it preserves source identity."""

    def __init__(self, provider: InvestmentEvidenceProvider, *, retry_policy: RetryPolicy = RetryPolicy(), sleep: Callable[[float], None] = __import__("time").sleep) -> None:
        retry_policy.validate(); self._provider = provider; self._retry_policy = retry_policy; self._sleep = sleep
        self.name, self.capabilities = provider.name, provider.capabilities

    def fetch_facts(self, instrument_id: str, *, start: datetime, end: datetime) -> list[ProviderInvestmentFact]:
        for attempt in range(self._retry_policy.maximum_attempts):
            try: return self._provider.fetch_facts(instrument_id, start=start, end=end)
            except ProviderError:
                if attempt + 1 == self._retry_policy.maximum_attempts: raise
                self._sleep(self._retry_policy.base_delay.total_seconds() * (2 ** attempt))
        raise AssertionError("retry loop must return or raise")


class FallbackInvestmentEvidenceProvider:
    """Select exactly one successful provider, recording health and caching its raw evidence."""

    name = "investment_evidence_fallback"

    def __init__(self, providers: tuple[InvestmentEvidenceProvider, ...], health: ProviderHealthRegistry, cache: InMemoryInvestmentEvidenceCache, *, cache_ttl: timedelta = timedelta(minutes=5)) -> None:
        if not providers: raise ProviderConfigurationError("at_least_one_investment_evidence_provider_is_required")
        self._providers, self._health, self._cache, self._cache_ttl = providers, health, cache, cache_ttl
        self.capabilities = ProviderCapabilities(self.name, frozenset({"EQUITY", "ETF", "FUND"}), frozenset({"fundamental"}), True, False, any(item.capabilities.supports_pagination for item in providers), any(item.capabilities.supports_revisions for item in providers), None, None, "DEPENDENT_ON_SELECTED_PROVIDER")

    def fetch_facts(self, instrument_id: str, *, start: datetime, end: datetime) -> list[ProviderInvestmentFact]:
        failures: list[str] = []
        for provider in self._providers:
            cached = self._cache.get(provider.name, instrument_id, start, end)
            if cached is not None:
                return cached
        for provider in self._providers:
            try:
                facts = provider.fetch_facts(instrument_id, start=start, end=end)
                if not facts: raise ProviderError("investment_provider_returned_empty_batch")
                for fact in facts: fact.validate()
            except ProviderError as error:
                failures.append(f"{provider.name}:{error}"); self._health.failure(provider.name, str(error)); continue
            self._health.success(provider.name); self._cache.put(provider.name, instrument_id, start, end, facts, self._cache_ttl); return facts
        raise ProviderError("all_investment_evidence_providers_failed:" + ";".join(failures))


class FixtureInvestmentEvidenceProvider:
    """Deterministic fixture adapter; production adapters must implement the same contract."""

    def __init__(self, name: str, facts: tuple[ProviderInvestmentFact, ...], *, credential_reference: str | None = None) -> None:
        if not name: raise ProviderConfigurationError("investment_evidence_provider_name_required")
        self.name, self._facts = name, facts
        self.capabilities = ProviderCapabilities(name, frozenset({"EQUITY", "ETF", "FUND"}), frozenset({"fundamental"}), True, False, False, True, None, credential_reference, "FIXTURE")

    def fetch_facts(self, instrument_id: str, *, start: datetime, end: datetime) -> list[ProviderInvestmentFact]:
        return [item if item.provider_name else replace(item, provider_name=self.name) for item in self._facts if item.instrument_id == instrument_id and start <= item.observed_at <= end]


class HttpsJsonInvestmentEvidenceProvider:
    """Configurable HTTPS JSON evidence source.

    A deployment may supply a transport that resolves the configuration's secret
    reference into an authorization header. This adapter never accepts, stores,
    or exposes a credential value.
    """

    def __init__(self, configuration: ProviderConfiguration, *, endpoint_path: str = "/v1/investment-facts", transport: HttpTransport | None = None,
                 retry_policy: RetryPolicy = RetryPolicy(), monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        configuration.validate(); retry_policy.validate()
        if not endpoint_path.startswith("/") or "?" in endpoint_path:
            raise ProviderConfigurationError("invalid_investment_evidence_endpoint_path")
        self.name, self._configuration, self._endpoint_path = configuration.provider, configuration, endpoint_path
        self._transport, self._retry_policy, self._monotonic, self._sleep, self._last_request_at = transport or UrlLibTransport(), retry_policy, monotonic, sleep, None
        self.capabilities = ProviderCapabilities(self.name, frozenset({"EQUITY", "ETF", "FUND"}), frozenset({"fundamental"}), True, False, True, True, None, configuration.secret_reference, "TERMS_ACCEPTANCE_REQUIRED" if not configuration.terms_accepted else "CONFIGURED")

    def fetch_facts(self, instrument_id: str, *, start: datetime, end: datetime) -> list[ProviderInvestmentFact]:
        return collect_investment_evidence_pages(self, instrument_id, start=start, end=end)

    def fetch_page(self, instrument_id: str, *, start: datetime, end: datetime, cursor: str | None) -> InvestmentEvidencePage:
        if not self._configuration.terms_accepted:
            raise ProviderConfigurationError("provider_terms_not_accepted")
        if not instrument_id or start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None or end < start:
            raise ProviderConfigurationError("unsupported_investment_provider_request")
        self._respect_rate_limit()
        query = {"instrument_id": instrument_id, "start": start.isoformat(), "end": end.isoformat()}
        if cursor is not None: query["cursor"] = cursor
        response = self._request_with_retry(f"{self._configuration.base_url.rstrip('/')}{self._endpoint_path}?{urlencode(query)}")
        try:
            payload = json.loads(response.body)
            records = payload if isinstance(payload, list) else payload["facts"]
            next_cursor = None if isinstance(payload, list) else payload.get("next_cursor")
            if not isinstance(records, list) or next_cursor is not None and not isinstance(next_cursor, str): raise ValueError("invalid page")
            facts = tuple(self._parse_fact(instrument_id, item) for item in records)
        except (KeyError, TypeError, ValueError, ArithmeticError, json.JSONDecodeError) as error:
            raise ProviderError("invalid_investment_provider_json") from error
        return InvestmentEvidencePage(facts, next_cursor)

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is not None:
            delay = self._configuration.minimum_request_interval.total_seconds() - (self._monotonic() - self._last_request_at)
            if delay > 0: self._sleep(delay)
        self._last_request_at = self._monotonic()

    def _request_with_retry(self, url: str) -> HttpResponse:
        status = "network"
        for attempt in range(self._retry_policy.maximum_attempts):
            response = self._transport.get(url, self._configuration.request_timeout_seconds); status = str(response.status_code)
            if response.status_code == 200: return response
            if response.status_code not in {429, 500, 502, 503, 504} or attempt + 1 == self._retry_policy.maximum_attempts: break
            retry_after = response.headers.get("Retry-After")
            self._sleep(float(retry_after) if retry_after and retry_after.isdigit() else self._retry_policy.base_delay.total_seconds() * (2 ** attempt))
        raise ProviderError(f"investment_provider_http_status:{status}")

    def _parse_fact(self, requested_instrument_id: str, value: object) -> ProviderInvestmentFact:
        if not isinstance(value, dict): raise ValueError("record must be object")
        instrument_id = str(value.get("instrument_id", requested_instrument_id))
        if instrument_id != requested_instrument_id: raise ProviderError("investment_provider_instrument_mismatch")
        source_url = str(value.get("source_url", ""))
        if not source_url.startswith("https://"): raise ProviderError("investment_provider_source_url_requires_https")
        fact = ProviderInvestmentFact(instrument_id, str(value["metric"]), Decimal(str(value["value"])), str(value["unit"]),
            self._parse_timestamp(value["observed_at"]), self._parse_timestamp(value["available_at"]), str(value["source_identifier"]), source_url,
            int(value.get("revision", 0)), self.name)
        fact.validate(); return fact

    @staticmethod
    def _parse_timestamp(value: object) -> datetime:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None: raise ValueError("timestamp must include timezone")
        return result.astimezone(timezone.utc)


class SecCompanyFactsTransport(Protocol):
    def get(self, url: str, timeout_seconds: float, user_agent: str) -> HttpResponse: ...


class UrlLibSecCompanyFactsTransport:
    """SEC-compatible HTTPS transport with the caller's required identifying User-Agent."""
    def get(self, url: str, timeout_seconds: float, user_agent: str) -> HttpResponse:
        try:
            request = Request(url, headers={"Accept": "application/json", "User-Agent": user_agent})
            with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: fixed SEC HTTPS host
                return HttpResponse(response.status, response.read().decode("utf-8"), dict(response.headers.items()))
        except HTTPError as error:
            return HttpResponse(error.code, error.read().decode("utf-8", errors="replace"), dict(error.headers.items()))
        except URLError as error:
            raise ProviderError("sec_companyfacts_network_error") from error


class SecCompanyFactsProvider:
    """Public SEC Company Facts adapter for configured CIK mappings.

    SEC facts commonly disclose only dates, not publication times.  Filing dates
    are therefore made visible only at the following UTC midnight, preventing
    same-day intraday look-ahead. This is investment research data only.
    """

    name = "sec_companyfacts"
    _base_url = "https://data.sec.gov/api/xbrl/companyfacts"

    def __init__(self, *, cik_by_instrument: dict[str, str], metric_concepts: dict[str, tuple[str, str]], user_agent: str,
                 terms_accepted: bool, request_timeout_seconds: float = 10.0, minimum_request_interval: timedelta = timedelta(milliseconds=100),
                 transport: SecCompanyFactsTransport | None = None, retry_policy: RetryPolicy = RetryPolicy(), monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        retry_policy.validate()
        if (not cik_by_instrument or not metric_concepts or not user_agent.strip() or request_timeout_seconds <= 0
                or minimum_request_interval < timedelta(0) or any(not key or not value or not value[0] or not value[1] for key, value in metric_concepts.items())
                or any(not instrument or not str(cik).isdigit() for instrument, cik in cik_by_instrument.items())):
            raise ProviderConfigurationError("invalid_sec_companyfacts_configuration")
        self._ciks = {instrument: str(cik).zfill(10) for instrument, cik in cik_by_instrument.items()}; self._concepts = dict(metric_concepts)
        self._user_agent, self._terms_accepted, self._timeout, self._minimum_interval = user_agent, terms_accepted, request_timeout_seconds, minimum_request_interval
        self._transport, self._retry_policy, self._monotonic, self._sleep, self._last_request_at = transport or UrlLibSecCompanyFactsTransport(), retry_policy, monotonic, sleep, None
        self.capabilities = ProviderCapabilities(self.name, frozenset({"EQUITY"}), frozenset({"fundamental"}), True, False, False, True, 10, None, "SEC_PUBLIC_DATA_TERMS_ACCEPTANCE_REQUIRED" if not terms_accepted else "SEC_PUBLIC_DATA_CONFIGURED")

    def fetch_facts(self, instrument_id: str, *, start: datetime, end: datetime) -> list[ProviderInvestmentFact]:
        if not self._terms_accepted: raise ProviderConfigurationError("provider_terms_not_accepted")
        if instrument_id not in self._ciks or start.tzinfo is None or end.tzinfo is None or end < start:
            raise ProviderConfigurationError("unsupported_sec_companyfacts_request")
        self._respect_rate_limit(); cik = self._ciks[instrument_id]
        response = self._request_with_retry(f"{self._base_url}/CIK{cik}.json")
        try: payload = json.loads(response.body); facts = self._parse_payload(instrument_id, cik, payload, start, end)
        except (KeyError, TypeError, ValueError, ArithmeticError, json.JSONDecodeError) as error: raise ProviderError("invalid_sec_companyfacts_json") from error
        if not facts: raise ProviderError("sec_companyfacts_returned_empty_batch")
        return facts

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is not None:
            delay = self._minimum_interval.total_seconds() - (self._monotonic() - self._last_request_at)
            if delay > 0: self._sleep(delay)
        self._last_request_at = self._monotonic()

    def _request_with_retry(self, url: str) -> HttpResponse:
        status = "network"
        for attempt in range(self._retry_policy.maximum_attempts):
            response = self._transport.get(url, self._timeout, self._user_agent); status = str(response.status_code)
            if response.status_code == 200: return response
            if response.status_code not in {429, 500, 502, 503, 504} or attempt + 1 == self._retry_policy.maximum_attempts: break
            delay = response.headers.get("Retry-After")
            self._sleep(float(delay) if delay and delay.isdigit() else self._retry_policy.base_delay.total_seconds() * (2 ** attempt))
        raise ProviderError(f"sec_companyfacts_http_status:{status}")

    def _parse_payload(self, instrument_id: str, cik: str, payload: object, start: datetime, end: datetime) -> list[ProviderInvestmentFact]:
        if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict): raise ValueError("missing facts")
        values: list[ProviderInvestmentFact] = []
        for metric, (taxonomy, concept) in sorted(self._concepts.items()):
            concept_data = payload["facts"][taxonomy][concept]; unit_values = concept_data["units"]["USD"]
            for item in unit_values:
                observed_at = datetime.combine(datetime.fromisoformat(item["end"]).date(), clock_time.min, timezone.utc)
                filed_date = datetime.fromisoformat(item["filed"]).date()
                available_at = datetime.combine(filed_date + timedelta(days=1), clock_time.min, timezone.utc)
                if not start <= observed_at <= end or available_at > end: continue
                accession = str(item["accn"]); document = str(item.get("form", "filing"))
                source_id = f"{cik}:{taxonomy}:{concept}:{accession}:{item['end']}"
                source_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
                fact = ProviderInvestmentFact(instrument_id, metric, Decimal(str(item["val"])), "USD", observed_at, available_at, source_id, source_url, int(item.get("fy", 0)), self.name)
                fact.validate(); values.append(fact)
        return sorted(values, key=lambda item: (item.metric, item.observed_at, item.source_identifier))


def synchronize_investment_provider_health_alerts(health: ProviderHealthRegistry, alerts: SQLiteOperationalAlertStore, provider_names: tuple[str, ...]) -> None:
    """Persist provider-health failures and resolve them only after an explicit healthy observation."""
    if not provider_names or any(not value.strip() for value in provider_names):
        raise ProviderConfigurationError("invalid_investment_provider_health_targets")
    active = {(item.source, item.code, item.resource): item for item in alerts.active()}
    for provider in sorted(set(provider_names)):
        resource = f"provider:{provider}"; observation = health.get(provider)
        key = ("investment_provider", "HEALTH_UNAVAILABLE", resource)
        if observation is None or not observation.healthy:
            details = {"checked_at": "never" if observation is None else observation.checked_at.isoformat(), "reason": "no_observation" if observation is None else str(observation.reason), "consecutive_failures": "0" if observation is None else str(observation.consecutive_failures)}
            alerts.raise_alert(source=key[0], code=key[1], severity=AlertSeverity.WARNING, resource=resource, details=details)
        elif key in active:
            alerts.transition(active[key].alert_id, AlertStatus.RESOLVED, actor="investment-provider-monitor", details={"checked_at": observation.checked_at.isoformat(), "reason": "healthy_observation"})


class SQLiteInvestmentProviderHealthStore:
    """Append-only provider-health observations for restart-safe monitoring and audit."""
    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("CREATE TABLE IF NOT EXISTS investment_provider_health_observations (observation_id TEXT PRIMARY KEY, provider TEXT NOT NULL, checked_at TEXT NOT NULL, healthy INTEGER NOT NULL, reason TEXT, consecutive_failures INTEGER NOT NULL)")
        self._connection.execute("CREATE TABLE IF NOT EXISTS investment_provider_health_cadences (provider TEXT PRIMARY KEY, interval_seconds INTEGER NOT NULL, grace_seconds INTEGER NOT NULL, approved_by TEXT NOT NULL)")
        self._connection.commit()

    def append(self, observation) -> None:
        if not observation.provider or observation.checked_at.tzinfo is None or observation.checked_at.utcoffset() is None or observation.consecutive_failures < 0:
            raise ProviderConfigurationError("invalid_investment_provider_health_observation")
        self._connection.execute("INSERT INTO investment_provider_health_observations VALUES (?, ?, ?, ?, ?, ?)", (str(uuid4()), observation.provider, observation.checked_at.isoformat(), int(observation.healthy), observation.reason, observation.consecutive_failures)); self._connection.commit()

    def latest(self, provider: str):
        row = self._connection.execute("SELECT provider, checked_at, healthy, reason, consecutive_failures FROM investment_provider_health_observations WHERE provider = ? ORDER BY checked_at DESC, observation_id DESC LIMIT 1", (provider,)).fetchone()
        return None if row is None else ProviderHealth(row[0], datetime.fromisoformat(row[1]), bool(row[2]), row[3], row[4])

    def set_cadence(self, cadence: "InvestmentProviderHealthCadence") -> None:
        self._connection.execute("INSERT OR REPLACE INTO investment_provider_health_cadences VALUES (?, ?, ?, ?)", (cadence.provider, int(cadence.interval.total_seconds()), int(cadence.grace.total_seconds()), cadence.approved_by)); self._connection.commit()

    def due_cadences(self, as_of: datetime) -> tuple[tuple["InvestmentProviderHealthCadence", ProviderHealth | None, bool, bool], ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None: raise ProviderConfigurationError("provider_health_as_of_must_be_timezone_aware")
        rows = self._connection.execute("SELECT * FROM investment_provider_health_cadences ORDER BY provider").fetchall(); results = []
        for row in rows:
            cadence = InvestmentProviderHealthCadence(row[0], timedelta(seconds=row[1]), timedelta(seconds=row[2]), row[3]); latest = self.latest(cadence.provider)
            due = latest is None or as_of >= latest.checked_at + cadence.interval; overdue = latest is None or as_of > latest.checked_at + cadence.interval + cadence.grace
            results.append((cadence, latest, due, overdue))
        return tuple(results)

    def close(self) -> None:
        self._connection.close()


@dataclass(frozen=True, slots=True)
class InvestmentProviderHealthCadence:
    provider: str
    interval: timedelta
    grace: timedelta
    approved_by: str

    def __post_init__(self) -> None:
        if not self.provider or self.interval <= timedelta(0) or self.grace < timedelta(0) or not self.approved_by:
            raise ProviderConfigurationError("invalid_investment_provider_health_cadence")


def persist_investment_provider_health(health: ProviderHealthRegistry, store: SQLiteInvestmentProviderHealthStore, provider_names: tuple[str, ...]) -> None:
    if not provider_names or any(not value.strip() for value in provider_names): raise ProviderConfigurationError("invalid_investment_provider_health_targets")
    for provider in sorted(set(provider_names)):
        observation = health.get(provider)
        if observation is not None: store.append(observation)


def synchronize_persisted_investment_provider_health_alerts(store: SQLiteInvestmentProviderHealthStore, alerts: SQLiteOperationalAlertStore, provider_names: tuple[str, ...]) -> None:
    if not provider_names or any(not value.strip() for value in provider_names): raise ProviderConfigurationError("invalid_investment_provider_health_targets")
    active = {(item.source, item.code, item.resource): item for item in alerts.active()}
    for provider in sorted(set(provider_names)):
        resource = f"provider:{provider}"; observation = store.latest(provider); key = ("investment_provider", "HEALTH_UNAVAILABLE", resource)
        if observation is None or not observation.healthy:
            details = {"checked_at": "never" if observation is None else observation.checked_at.isoformat(), "reason": "no_observation" if observation is None else str(observation.reason), "consecutive_failures": "0" if observation is None else str(observation.consecutive_failures)}
            alerts.raise_alert(source=key[0], code=key[1], severity=AlertSeverity.WARNING, resource=resource, details=details)
        elif key in active:
            alerts.transition(active[key].alert_id, AlertStatus.RESOLVED, actor="investment-provider-monitor", details={"checked_at": observation.checked_at.isoformat(), "reason": "healthy_observation"})


def synchronize_investment_provider_health_cadence_alerts(store: SQLiteInvestmentProviderHealthStore, alerts: SQLiteOperationalAlertStore, as_of: datetime) -> None:
    active = {(item.source, item.code, item.resource): item for item in alerts.active()}
    for cadence, latest, _, overdue in store.due_cadences(as_of):
        resource = f"provider:{cadence.provider}"; key = ("investment_provider", "HEALTH_CADENCE_OVERDUE", resource)
        if overdue:
            alerts.raise_alert(source=key[0], code=key[1], severity=AlertSeverity.WARNING, resource=resource, details={"approved_by": cadence.approved_by, "last_checked_at": "never" if latest is None else latest.checked_at.isoformat()})
        elif key in active:
            alerts.transition(active[key].alert_id, AlertStatus.RESOLVED, actor="investment-provider-monitor", details={"observed_at": as_of.isoformat(), "reason": "cadence_current"})


class InvestmentProviderHealthMonitorJob:
    """Runs only due provider checks; callers own scheduling and credential-aware transport."""
    def __init__(self, health: ProviderHealthRegistry, store: SQLiteInvestmentProviderHealthStore, alerts: SQLiteOperationalAlertStore) -> None:
        self._health, self._store, self._alerts = health, store, alerts

    def run_due(self, as_of: datetime, check_provider: Callable[[str], None]) -> tuple[str, ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None: raise ProviderConfigurationError("provider_health_as_of_must_be_timezone_aware")
        due = tuple(cadence.provider for cadence, _, is_due, _ in self._store.due_cadences(as_of) if is_due)
        if not due:
            synchronize_investment_provider_health_cadence_alerts(self._store, self._alerts, as_of)
            return ()
        for provider in due:
            try:
                check_provider(provider)
            except Exception:
                # Provider errors must be observable, but raw exception strings can leak credentials or transport detail.
                self._health.failure(provider, "provider_health_check_failed")
            else:
                self._health.success(provider)
        persist_investment_provider_health(self._health, self._store, due)
        synchronize_persisted_investment_provider_health_alerts(self._store, self._alerts, due)
        synchronize_investment_provider_health_cadence_alerts(self._store, self._alerts, as_of)
        return due


def ingest_investment_provider_facts(store: SQLiteInvestmentStore, provider: InvestmentEvidenceProvider, instrument_id: str, *, start: datetime, end: datetime, ingested_at: datetime | None = None) -> tuple[SourceBackedFact, ...]:
    """Fetch, validate and idempotently persist provider facts with deterministic provenance IDs."""
    timestamp = datetime.now(timezone.utc) if ingested_at is None else ingested_at
    if timestamp.tzinfo is None or timestamp.utcoffset() is None: raise InvestmentValidationError("investment_provider_ingested_at_must_be_timezone_aware")
    records = provider.fetch_facts(instrument_id, start=start, end=end); result: list[SourceBackedFact] = []
    for item in records:
        item.validate()
        if item.instrument_id != instrument_id: raise ProviderError("investment_provider_instrument_mismatch")
        if timestamp < item.available_at: raise InvestmentValidationError("investment_provider_fact_not_yet_ingestable")
        source_provider = item.provider_name or provider.name
        reference = f"investment-provider:{source_provider}:{item.source_identifier}:{item.revision}"
        identity = f"{item.instrument_id}|{item.metric}|{reference}|{item.observed_at.isoformat()}"
        result.append(SourceBackedFact(uuid5(NAMESPACE_URL, identity), item.instrument_id, item.metric, item.value, item.unit, item.observed_at, item.available_at, timestamp, reference, item.source_url, item.revision))
    store.ingest_source_backed_facts(tuple(result))
    return tuple(result)
