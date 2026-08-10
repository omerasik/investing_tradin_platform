"""Capability-aware market-data provider contracts and a public daily CSV adapter.

Providers only return attributable raw records. Validation, persistence, and
point-in-time access remain owned by ``market_data.py``.  Secrets are referenced
by name only; no credential value is accepted or stored here.
"""

import csv
import io
import time
from dataclasses import dataclass, field
from datetime import datetime, time as clock_time, timedelta, timezone
from decimal import Decimal
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .domain import OHLCVBar


class ProviderError(RuntimeError):
    pass


class ProviderConfigurationError(ProviderError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    provider: str
    asset_classes: frozenset[str]
    intervals: frozenset[str]
    supports_historical: bool
    supports_streaming: bool
    supports_pagination: bool
    supports_revisions: bool
    rate_limit_per_minute: int | None
    credential_reference: str | None
    license_status: str


@dataclass(frozen=True, slots=True)
class ProviderConfiguration:
    provider: str
    base_url: str
    terms_accepted: bool = False
    secret_reference: str | None = None
    request_timeout_seconds: float = 10.0
    minimum_request_interval: timedelta = timedelta(0)

    def validate(self) -> None:
        if not self.provider or not self.base_url.startswith("https://"):
            raise ProviderConfigurationError("provider_requires_https_base_url")
        if self.request_timeout_seconds <= 0 or self.minimum_request_interval < timedelta(0):
            raise ProviderConfigurationError("invalid_provider_configuration")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    maximum_attempts: int = 3
    base_delay: timedelta = timedelta(seconds=1)

    def validate(self) -> None:
        if self.maximum_attempts < 1 or self.base_delay < timedelta(0):
            raise ProviderConfigurationError("invalid_retry_policy")


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    def get(self, url: str, timeout_seconds: float) -> HttpResponse: ...


class UrlLibTransport:
    """Small production HTTP transport; callers opt in through provider config."""

    def get(self, url: str, timeout_seconds: float) -> HttpResponse:
        try:
            request = Request(url, headers={"Accept": "text/csv", "User-Agent": "trade-platform-paper-research/0.1"})
            with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: URL is config-validated HTTPS
                return HttpResponse(response.status, response.read().decode("utf-8"), dict(response.headers.items()))
        except HTTPError as error:
            return HttpResponse(error.code, error.read().decode("utf-8", errors="replace"), dict(error.headers.items()))
        except URLError as error:
            raise ProviderError("provider_network_error") from error


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    checked_at: datetime
    healthy: bool
    reason: str | None
    consecutive_failures: int


class ProviderHealthRegistry:
    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now
        self._health: dict[str, ProviderHealth] = {}

    def success(self, provider: str) -> None:
        self._health[provider] = ProviderHealth(provider, self._now(), True, None, 0)

    def failure(self, provider: str, reason: str) -> None:
        previous = self._health.get(provider)
        failures = 1 if previous is None else previous.consecutive_failures + 1
        self._health[provider] = ProviderHealth(provider, self._now(), False, reason, failures)

    def get(self, provider: str) -> ProviderHealth | None:
        return self._health.get(provider)


class HistoricalProvider(Protocol):
    name: str
    capabilities: ProviderCapabilities

    def fetch_bars(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]: ...


@dataclass(frozen=True, slots=True)
class BarPage:
    bars: tuple[OHLCVBar, ...]
    next_cursor: str | None


class PaginatedHistoricalProvider(Protocol):
    name: str

    def fetch_page(self, instrument_id: str, interval: str, start: datetime, end: datetime, cursor: str | None) -> BarPage: ...


def collect_pages(provider: PaginatedHistoricalProvider, instrument_id: str, interval: str, start: datetime, end: datetime, *, maximum_pages: int = 1000) -> list[OHLCVBar]:
    if maximum_pages < 1:
        raise ProviderConfigurationError("maximum_pages_must_be_positive")
    cursor: str | None = None
    seen_cursors: set[str] = set()
    bars: list[OHLCVBar] = []
    for _ in range(maximum_pages):
        page = provider.fetch_page(instrument_id, interval, start, end, cursor)
        bars.extend(page.bars)
        if page.next_cursor is None:
            return bars
        if page.next_cursor in seen_cursors:
            raise ProviderError("provider_pagination_cursor_loop")
        seen_cursors.add(page.next_cursor)
        cursor = page.next_cursor
    raise ProviderError("provider_pagination_limit_exceeded")


@dataclass(frozen=True, slots=True)
class CachedBars:
    expires_at: datetime
    bars: tuple[OHLCVBar, ...]


class InMemoryBarCache:
    """Development cache; production cache must preserve the same provenance key."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now
        self._items: dict[tuple[str, str, str, datetime, datetime], CachedBars] = {}

    def get(self, provider: str, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar] | None:
        item = self._items.get((provider, instrument_id, interval, start, end))
        if item is None or item.expires_at <= self._now():
            return None
        return list(item.bars)

    def put(self, provider: str, instrument_id: str, interval: str, start: datetime, end: datetime, bars: list[OHLCVBar], ttl: timedelta) -> None:
        if ttl <= timedelta(0):
            raise ProviderConfigurationError("cache_ttl_must_be_positive")
        self._items[(provider, instrument_id, interval, start, end)] = CachedBars(self._now() + ttl, tuple(bars))


class FallbackHistoricalProvider:
    """Provider fallback with health evidence; it never blends incompatible responses."""

    name = "fallback"

    def __init__(self, providers: tuple[HistoricalProvider, ...], health: ProviderHealthRegistry, cache: InMemoryBarCache, *, cache_ttl: timedelta = timedelta(minutes=5)) -> None:
        if not providers:
            raise ProviderConfigurationError("at_least_one_provider_is_required")
        self._providers = providers
        self._health = health
        self._cache = cache
        self._cache_ttl = cache_ttl
        self.provenance_names = frozenset(item.name for item in providers)
        self.capabilities = ProviderCapabilities("fallback", frozenset().union(*(item.capabilities.asset_classes for item in providers)), frozenset().union(*(item.capabilities.intervals for item in providers)), True, False, any(item.capabilities.supports_pagination for item in providers), any(item.capabilities.supports_revisions for item in providers), None, None, "DEPENDENT_ON_SELECTED_PROVIDER")

    def fetch_bars(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]:
        failures: list[str] = []
        for provider in self._providers:
            cached = self._cache.get(provider.name, instrument_id, interval, start, end)
            if cached is not None:
                return cached
            try:
                bars = provider.fetch_bars(instrument_id, interval, start, end)
                if not bars:
                    raise ProviderError("provider_returned_empty_batch")
            except ProviderError as error:
                failures.append(f"{provider.name}:{error}")
                self._health.failure(provider.name, str(error))
                continue
            self._health.success(provider.name)
            self._cache.put(provider.name, instrument_id, interval, start, end, bars, self._cache_ttl)
            return bars
        raise ProviderError("all_providers_failed:" + ";".join(failures))


class StooqDailyCsvProvider:
    """Public daily CSV adapter, disabled until an operator records terms acceptance.

    The source offers date-only daily values, so callers must supply the market
    close timezone and time.  This avoids inventing intraday timestamp semantics.
    """

    name = "stooq_daily"
    capabilities = ProviderCapabilities(
        provider=name,
        asset_classes=frozenset({"EQUITY", "ETF"}),
        intervals=frozenset({"1d"}),
        supports_historical=True,
        supports_streaming=False,
        supports_pagination=False,
        supports_revisions=False,
        rate_limit_per_minute=None,
        credential_reference=None,
        license_status="TERMS_ACCEPTANCE_REQUIRED",
    )

    def __init__(self, configuration: ProviderConfiguration, *, symbol_map: dict[str, str], market_timezone: str, market_close: clock_time, retry_policy: RetryPolicy = RetryPolicy(), transport: HttpTransport | None = None, now: Callable[[], datetime] | None = None, monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        configuration.validate()
        retry_policy.validate()
        if configuration.provider != self.name or not symbol_map or market_close.tzinfo is not None:
            raise ProviderConfigurationError("invalid_stooq_configuration")
        self._configuration = configuration
        self._symbol_map = dict(symbol_map)
        self._timezone = ZoneInfo(market_timezone)
        self._market_close = market_close
        self._retry_policy = retry_policy
        self._transport = transport or UrlLibTransport()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_request_at: float | None = None

    def fetch_bars(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]:
        if not self._configuration.terms_accepted:
            raise ProviderConfigurationError("provider_terms_not_accepted")
        if interval not in self.capabilities.intervals or instrument_id not in self._symbol_map or start.tzinfo is None or end.tzinfo is None or end < start:
            raise ProviderConfigurationError("unsupported_provider_request")
        self._respect_rate_limit()
        url = f"{self._configuration.base_url.rstrip('/')}/q/d/l/?s={self._symbol_map[instrument_id]}&i=d"
        response = self._request_with_retry(url)
        return self._parse_csv(instrument_id, response.body, start, end)

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is not None:
            delay = self._configuration.minimum_request_interval.total_seconds() - (self._monotonic() - self._last_request_at)
            if delay > 0:
                self._sleep(delay)
        self._last_request_at = self._monotonic()

    def _request_with_retry(self, url: str) -> HttpResponse:
        last_status = "network"
        for attempt in range(self._retry_policy.maximum_attempts):
            response = self._transport.get(url, self._configuration.request_timeout_seconds)
            if response.status_code == 200:
                return response
            last_status = str(response.status_code)
            if response.status_code not in {429, 500, 502, 503, 504} or attempt + 1 == self._retry_policy.maximum_attempts:
                break
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else self._retry_policy.base_delay.total_seconds() * (2 ** attempt)
            self._sleep(delay)
        raise ProviderError(f"provider_http_status:{last_status}")

    def _parse_csv(self, instrument_id: str, payload: str, start: datetime, end: datetime) -> list[OHLCVBar]:
        records: list[OHLCVBar] = []
        try:
            rows = csv.DictReader(io.StringIO(payload))
            for row in rows:
                date = datetime.fromisoformat(row["Date"])
                event_at = datetime.combine(date.date(), self._market_close, self._timezone).astimezone(timezone.utc)
                if not start <= event_at <= end:
                    continue
                close = Decimal(row["Close"])
                if close <= 0:
                    continue
                records.append(OHLCVBar(instrument_id, "1d", event_at, event_at, self._now(), Decimal(row["Open"]), Decimal(row["High"]), Decimal(row["Low"]), close, Decimal(row.get("Volume") or "0"), self.name, f"{self._symbol_map[instrument_id]}:{row['Date']}", self._timezone.key, data_version="stooq-daily-csv-v1"))
        except (KeyError, ValueError, ArithmeticError) as error:
            raise ProviderError("invalid_provider_csv") from error
        if not records:
            raise ProviderError("provider_returned_empty_batch")
        return records
