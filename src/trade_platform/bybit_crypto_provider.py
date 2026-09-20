from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from .data_providers import (
    HttpResponse,
    HttpTransport,
    MinimumIntervalRequestPacer,
    ProviderConfiguration,
    ProviderConfigurationError,
    ProviderError,
    RetryPolicy,
)
from .historical_market_data import (
    AdjustmentStatus,
    ObservationKind,
    RawHistoricalObservation,
)
from .market_observation_payloads import OpenInterestUnit
from .provider_ingestion import RawHistoricalPage
from .tradable_bar_evidence_v2 import BAR_TIMESTAMP_SEMANTICS_MARKER_V1

BYBIT_PROVIDER_NAME: Final = "bybit"
BYBIT_DEFAULT_BASE_URL: Final = "https://api.bybit.com"
BYBIT_V5_SYMBOL_NAMESPACE: Final = "bybit_v5_symbol"
BYBIT_EXCHANGE: Final = "BYBIT"
BYBIT_PROVIDER_VERSION: Final = "bybit-v5-public-market-v1"
BYBIT_LINEAR_CATEGORY: Final = "linear"

BYBIT_KLINE_INTERVAL: Final = "1"
BYBIT_OPEN_INTEREST_INTERVAL: Final = "5min"
BYBIT_KLINE_PAGE_LIMIT: Final = 1000
BYBIT_OPEN_INTEREST_PAGE_LIMIT: Final = 200

MARK_PRICE_METHODOLOGY_REFERENCE: Final = "bybit:v5:mark-price-kline:close"
INDEX_PRICE_METHODOLOGY_REFERENCE: Final = "bybit:v5:index-price-kline:close"

TIME_CURSOR_PREFIX: Final = "t:"
PAGE_CURSOR_PREFIX: Final = "c:"

SUPPORTED_OBSERVATION_KINDS: Final[frozenset[ObservationKind]] = frozenset(
    {
        ObservationKind.OHLCV,
        ObservationKind.MARK_PRICE,
        ObservationKind.INDEX_PRICE,
        ObservationKind.OPEN_INTEREST,
    }
)

UNSUPPORTED_OBSERVATION_KINDS: Final[frozenset[ObservationKind]] = frozenset(
    {ObservationKind.FUNDING_RATE_REALIZED, ObservationKind.FUNDING_RATE_INDICATIVE}
)

BYBIT_ENDPOINTS: Final[dict[ObservationKind, str]] = {
    ObservationKind.OHLCV: "/v5/market/kline",
    ObservationKind.MARK_PRICE: "/v5/market/mark-price-kline",
    ObservationKind.INDEX_PRICE: "/v5/market/index-price-kline",
    ObservationKind.OPEN_INTEREST: "/v5/market/open-interest",
}

BYBIT_ENDPOINT_FAMILIES: Final[dict[ObservationKind, str]] = {
    ObservationKind.OHLCV: "trade-kline",
    ObservationKind.MARK_PRICE: "mark-kline",
    ObservationKind.INDEX_PRICE: "index-kline",
    ObservationKind.OPEN_INTEREST: "open-interest",
}

_REFERENCE_PRICE_METHODOLOGIES: Final[dict[ObservationKind, str]] = {
    ObservationKind.MARK_PRICE: MARK_PRICE_METHODOLOGY_REFERENCE,
    ObservationKind.INDEX_PRICE: INDEX_PRICE_METHODOLOGY_REFERENCE,
}

BYBIT_AMBIGUOUS_SCOPE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "base",
        "categories",
        "category_override",
        "cursor",
        "endTime",
        "end_time",
        "exchange",
        "instrument_id",
        "intervalTime",
        "interval_time",
        "intervals",
        "kind",
        "kinds",
        "limit",
        "observation_kinds",
        "priceType",
        "price_type",
        "product_type",
        "provider",
        "quote",
        "settlement",
        "settlement_currency",
        "startTime",
        "start_time",
        "symbols",
        "venue",
    }
)

_KLINE_KINDS: Final[frozenset[ObservationKind]] = frozenset(
    {ObservationKind.OHLCV, ObservationKind.MARK_PRICE, ObservationKind.INDEX_PRICE}
)

_BAR_WIDTH: Final = timedelta(minutes=1)
_NORMALIZED_BAR_INTERVAL: Final = "1m"
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

# Bybit V5 answers HTTP 200 with a non-zero envelope `retCode` for provider-level
# failures. Exactly one of those codes is a documented transient rate-limit
# condition; every other code stays a non-retryable contract failure.
BYBIT_RATE_LIMIT_RETURN_CODE: Final = 10006
BYBIT_RATE_LIMIT_RESET_HEADER: Final = "X-Bapi-Limit-Reset-Timestamp"
# The reset header is untrusted provider input. A timestamp further ahead than this
# is treated as unusable (and falls back to RetryPolicy backoff) rather than parked
# on for an unbounded sleep.
_MAXIMUM_RATE_LIMIT_RESET_HORIZON: Final = timedelta(minutes=5)
_RETRY_AFTER_HEADER: Final = "Retry-After"
_TRADE_KLINE_FIELDS: Final = 7
_REFERENCE_KLINE_FIELDS: Final = 5
_MINIMUM_ASSET_LENGTH: Final = 2
_MAXIMUM_ASSET_LENGTH: Final = 12
_USER_AGENT: Final = "trade-platform-paper-research/0.1"


class BybitUnsupportedObservationKindError(ProviderConfigurationError):
    pass


@dataclass(frozen=True, slots=True)
class BybitScope:
    observation_kind: ObservationKind
    category: str
    symbol: str
    interval: str
    base_asset: str
    quote_asset: str
    settlement_asset: str
    start: datetime
    end: datetime


def _scope_text(scope: dict[str, object], key: str) -> str:
    raw = scope.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ProviderConfigurationError(f"invalid_bybit_scope_{key}")
    return raw.strip()


def _scope_asset(scope: dict[str, object], key: str) -> str:
    value = _scope_text(scope, key)
    if not _MINIMUM_ASSET_LENGTH <= len(value) <= _MAXIMUM_ASSET_LENGTH:
        raise ProviderConfigurationError(f"invalid_bybit_scope_{key}")
    if not value.isalnum() or value != value.upper():
        raise ProviderConfigurationError(f"invalid_bybit_scope_{key}")
    return value


def _scope_instant(scope: dict[str, object], key: str) -> datetime:
    raw = _scope_text(scope, key)
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ProviderConfigurationError(f"invalid_bybit_scope_{key}") from error
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ProviderConfigurationError(f"bybit_scope_{key}_must_be_utc")
    return value


def parse_bybit_scope(scope: dict[str, object]) -> BybitScope:
    ambiguous = sorted(BYBIT_AMBIGUOUS_SCOPE_KEYS.intersection(scope))
    if ambiguous:
        raise ProviderConfigurationError("ambiguous_bybit_scope_field:" + ",".join(ambiguous))

    kind_value = _scope_text(scope, "observation_kind")
    try:
        observation_kind = ObservationKind(kind_value)
    except ValueError as error:
        raise BybitUnsupportedObservationKindError(
            f"bybit_unsupported_observation_kind:{kind_value}"
        ) from error
    if observation_kind not in SUPPORTED_OBSERVATION_KINDS:
        raise BybitUnsupportedObservationKindError(
            f"bybit_unsupported_observation_kind:{observation_kind.value}"
        )

    category = _scope_text(scope, "category")
    if category != BYBIT_LINEAR_CATEGORY:
        raise ProviderConfigurationError(f"unsupported_bybit_category:{category}")

    symbol = _scope_text(scope, "symbol")
    if not symbol.isalnum() or symbol != symbol.upper():
        raise ProviderConfigurationError("invalid_bybit_scope_symbol")

    interval = _scope_text(scope, "interval")
    expected_interval = (
        BYBIT_OPEN_INTEREST_INTERVAL
        if observation_kind is ObservationKind.OPEN_INTEREST
        else BYBIT_KLINE_INTERVAL
    )
    if interval != expected_interval:
        raise ProviderConfigurationError(
            f"unsupported_bybit_interval:{observation_kind.value}:{interval}"
        )

    base_asset = _scope_asset(scope, "base_asset")
    quote_asset = _scope_asset(scope, "quote_asset")
    settlement_asset = _scope_asset(scope, "settlement_asset")
    if base_asset == quote_asset:
        raise ProviderConfigurationError("bybit_scope_base_and_quote_must_differ")
    if settlement_asset != quote_asset:
        raise ProviderConfigurationError("linear_bybit_scope_must_settle_in_quote_asset")

    start = _scope_instant(scope, "start")
    end = _scope_instant(scope, "end")
    if start >= end:
        raise ProviderConfigurationError("invalid_bybit_scope_window")

    return BybitScope(
        observation_kind=observation_kind,
        category=category,
        symbol=symbol,
        interval=interval,
        base_asset=base_asset,
        quote_asset=quote_asset,
        settlement_asset=settlement_asset,
        start=start,
        end=end,
    )


def _epoch_milliseconds(value: datetime) -> int:
    delta = value - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def _from_milliseconds(value: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=value)


def _provider_milliseconds(value: str, label: str) -> int:
    if not value.isdigit():
        raise ProviderError(f"{label}:{value}")
    return int(value)


def _decimal_text(value: str, label: str) -> str:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ProviderError(f"{label}:{value}") from error
    if not parsed.is_finite():
        raise ProviderError(f"{label}:{value}")
    return value


def _parse_envelope(body: str) -> dict[str, object]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProviderError("bybit_invalid_json_response") from error
    if not isinstance(parsed, dict):
        raise ProviderError("bybit_unexpected_envelope_shape")
    return parsed


@dataclass(frozen=True, slots=True)
class _BybitEnvelope:
    """A structurally valid Bybit V5 envelope; `return_code` may still be a failure."""

    return_code: int
    message: str
    result: object

    @property
    def rate_limited(self) -> bool:
        return self.return_code == BYBIT_RATE_LIMIT_RETURN_CODE

    def provider_error(self) -> ProviderError:
        return ProviderError(f"bybit_provider_error:{self.return_code}:{self.message}")


def _validated_envelope(body: str) -> _BybitEnvelope:
    """Parse and structurally validate an envelope; malformed evidence fails closed."""
    envelope = _parse_envelope(body)
    return_code = envelope.get("retCode")
    if not isinstance(return_code, int) or isinstance(return_code, bool):
        raise ProviderError("bybit_unexpected_envelope_shape")
    if not isinstance(envelope.get("time"), int):
        raise ProviderError("bybit_unexpected_envelope_shape")
    message = envelope.get("retMsg")
    return _BybitEnvelope(
        return_code=return_code,
        message=message if isinstance(message, str) else "",
        result=envelope.get("result"),
    )


def _envelope_result(
    envelope: _BybitEnvelope, *, category: str, symbol: str
) -> dict[str, object]:
    if envelope.return_code != 0:
        raise envelope.provider_error()
    result = envelope.result
    if not isinstance(result, dict):
        raise ProviderError("bybit_unexpected_result_shape")
    result_category = result.get("category")
    if result_category is not None and result_category != category:
        raise ProviderError(f"bybit_response_category_mismatch:{result_category}")
    result_symbol = result.get("symbol")
    if result_symbol is not None and result_symbol != symbol:
        raise ProviderError(f"bybit_response_symbol_mismatch:{result_symbol}")
    return result


def _header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup; HTTP header casing is server-chosen."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered and isinstance(value, str):
            return value
    return None


def _result_rows(result: dict[str, object]) -> list[object]:
    rows = result.get("list")
    if not isinstance(rows, list):
        raise ProviderError("bybit_unexpected_result_shape")
    return rows


def _kline_row(observation_kind: ObservationKind, row: object) -> tuple[str, ...]:
    expected = (
        _TRADE_KLINE_FIELDS
        if observation_kind is ObservationKind.OHLCV
        else _REFERENCE_KLINE_FIELDS
    )
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) < expected:
        raise ProviderError("bybit_unexpected_kline_row_shape")
    fields = tuple(row[:expected])
    if not all(isinstance(field, str) and field.strip() for field in fields):
        raise ProviderError("bybit_unexpected_kline_row_shape")
    return tuple(str(field) for field in fields)


def _provenance_uri(scope: BybitScope) -> str:
    family = BYBIT_ENDPOINT_FAMILIES[scope.observation_kind]
    return f"bybit://v5/{family}/{scope.category}/{scope.symbol}"


class UrlLibBybitTransport:
    def get(self, url: str, timeout_seconds: float) -> HttpResponse:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT})
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
                return HttpResponse(
                    response.status, response.read().decode("utf-8"), dict(response.headers.items())
                )
        except HTTPError as error:
            return HttpResponse(
                error.code,
                error.read().decode("utf-8", errors="replace"),
                dict(error.headers.items()),
            )
        except URLError as error:
            raise ProviderError("provider_network_error") from error


class BybitCryptoHistoricalAdapter:
    name = BYBIT_PROVIDER_NAME

    def __init__(
        self,
        configuration: ProviderConfiguration,
        *,
        transport: HttpTransport | None = None,
        retry_policy: RetryPolicy = RetryPolicy(),
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        pacer: MinimumIntervalRequestPacer | None = None,
    ) -> None:
        configuration.validate()
        retry_policy.validate()
        if configuration.provider != self.name:
            raise ProviderConfigurationError("invalid_bybit_configuration")
        # A shared pacer must enforce exactly the configured interval; a second,
        # differently-configured limiter would be a conflicting hidden policy.
        if pacer is not None and pacer.minimum_interval != configuration.minimum_request_interval:
            raise ProviderConfigurationError("bybit_pacer_interval_mismatch")
        self._configuration = configuration
        self._transport: HttpTransport = transport or UrlLibBybitTransport()
        self._retry_policy = retry_policy
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._pacer = pacer or MinimumIntervalRequestPacer(
            configuration.minimum_request_interval, sleep=sleep
        )

    def fetch_raw_page(
        self, source_id: UUID, scope: dict[str, object], cursor: str | None
    ) -> RawHistoricalPage:
        if not self._configuration.terms_accepted:
            raise ProviderConfigurationError("provider_terms_not_accepted")
        parsed = parse_bybit_scope(scope)
        if parsed.observation_kind is ObservationKind.OPEN_INTEREST:
            return self._open_interest_page(source_id, parsed, cursor)
        return self._kline_page(source_id, parsed, cursor)

    def _kline_page(
        self, source_id: UUID, scope: BybitScope, cursor: str | None
    ) -> RawHistoricalPage:
        start_ms = _epoch_milliseconds(scope.start)
        end_ms = _epoch_milliseconds(scope.end)
        window_end_ms = self._kline_window_end(cursor, start_ms, end_ms)
        result = self._request(
            BYBIT_ENDPOINTS[scope.observation_kind],
            {
                "category": scope.category,
                "symbol": scope.symbol,
                "interval": scope.interval,
                "start": str(start_ms),
                "end": str(window_end_ms),
                "limit": str(BYBIT_KLINE_PAGE_LIMIT),
            },
            category=scope.category,
            symbol=scope.symbol,
        )
        retrieved_at = self._now()
        rows = _result_rows(result)
        records: list[RawHistoricalObservation] = []
        earliest_open_ms: int | None = None
        for row in rows:
            fields = _kline_row(scope.observation_kind, row)
            open_ms = _provider_milliseconds(fields[0], "bybit_unparseable_kline_start_time")
            if earliest_open_ms is None or open_ms < earliest_open_ms:
                earliest_open_ms = open_ms
            bar_open_at = _from_milliseconds(open_ms)
            bar_close_at = bar_open_at + _BAR_WIDTH
            if bar_open_at < scope.start or bar_close_at > scope.end:
                continue
            if bar_close_at > retrieved_at:
                continue
            records.append(
                self._observation(
                    source_id,
                    scope,
                    event_at=bar_open_at if scope.observation_kind is ObservationKind.OHLCV
                    else bar_close_at,
                    effective_at=bar_close_at,
                    ingested_at=retrieved_at,
                    payload=(
                        _trade_kline_payload(fields, open_ms)
                        if scope.observation_kind is ObservationKind.OHLCV
                        else _reference_price_payload(
                            scope, fields, open_ms=open_ms, bar_close_at=bar_close_at
                        )
                    ),
                )
            )
        records.sort(key=lambda record: record.event_at)
        return RawHistoricalPage(
            tuple(records),
            _next_time_cursor(earliest_open_ms, start_ms=start_ms, window_end_ms=window_end_ms),
            BYBIT_PROVIDER_VERSION,
            retrieved_at,
        )

    def _kline_window_end(self, cursor: str | None, start_ms: int, end_ms: int) -> int:
        if cursor is None:
            window_end_ms = end_ms - 1
        else:
            if cursor.startswith(PAGE_CURSOR_PREFIX):
                raise ProviderConfigurationError("bybit_cursor_kind_mismatch")
            if not cursor.startswith(TIME_CURSOR_PREFIX):
                raise ProviderConfigurationError("invalid_bybit_resume_cursor")
            digits = cursor[len(TIME_CURSOR_PREFIX) :]
            if not digits.isdigit():
                raise ProviderConfigurationError("invalid_bybit_resume_cursor")
            window_end_ms = int(digits)
            if not start_ms <= window_end_ms <= end_ms - 1:
                raise ProviderConfigurationError("invalid_bybit_resume_cursor")
        if window_end_ms < start_ms:
            raise ProviderConfigurationError("invalid_bybit_scope_window")
        return window_end_ms

    def _open_interest_page(
        self, source_id: UUID, scope: BybitScope, cursor: str | None
    ) -> RawHistoricalPage:
        start_ms = _epoch_milliseconds(scope.start)
        end_ms = _epoch_milliseconds(scope.end)
        if end_ms - 1 < start_ms:
            raise ProviderConfigurationError("invalid_bybit_scope_window")
        query = {
            "category": scope.category,
            "symbol": scope.symbol,
            "intervalTime": scope.interval,
            "startTime": str(start_ms),
            "endTime": str(end_ms - 1),
            "limit": str(BYBIT_OPEN_INTEREST_PAGE_LIMIT),
        }
        if cursor is not None:
            if cursor.startswith(TIME_CURSOR_PREFIX):
                raise ProviderConfigurationError("bybit_cursor_kind_mismatch")
            if not cursor.startswith(PAGE_CURSOR_PREFIX):
                raise ProviderConfigurationError("invalid_bybit_resume_cursor")
            provider_cursor = cursor[len(PAGE_CURSOR_PREFIX) :]
            if not provider_cursor.strip():
                raise ProviderConfigurationError("invalid_bybit_resume_cursor")
            query["cursor"] = provider_cursor
        result = self._request(
            BYBIT_ENDPOINTS[scope.observation_kind],
            query,
            category=scope.category,
            symbol=scope.symbol,
        )
        retrieved_at = self._now()
        records: list[RawHistoricalObservation] = []
        for row in _result_rows(result):
            payload, observed_at = _open_interest_payload(scope, row)
            if observed_at < scope.start or observed_at >= scope.end:
                continue
            if observed_at > retrieved_at:
                continue
            records.append(
                self._observation(
                    source_id,
                    scope,
                    event_at=observed_at,
                    effective_at=observed_at,
                    ingested_at=retrieved_at,
                    payload=payload,
                )
            )
        records.sort(key=lambda record: record.event_at)
        return RawHistoricalPage(
            tuple(records),
            _next_page_cursor(result),
            BYBIT_PROVIDER_VERSION,
            retrieved_at,
        )

    def _observation(
        self,
        source_id: UUID,
        scope: BybitScope,
        *,
        event_at: datetime,
        effective_at: datetime,
        ingested_at: datetime,
        payload: dict[str, object],
    ) -> RawHistoricalObservation:
        return RawHistoricalObservation(
            source_id=source_id,
            observation_kind=scope.observation_kind,
            provider_identifier=scope.symbol,
            provider_symbol=scope.symbol,
            exchange=BYBIT_EXCHANGE,
            event_at=event_at,
            effective_at=effective_at,
            ingested_at=ingested_at,
            adjustment_status=AdjustmentStatus.RAW,
            revision=0,
            provenance_uri=_provenance_uri(scope),
            raw_payload=payload,
        )

    def _request(
        self, path: str, query: dict[str, str], *, category: str, symbol: str
    ) -> dict[str, object]:
        url = f"{self._configuration.base_url.rstrip('/')}{path}?{urlencode(query)}"
        envelope = self._envelope_with_retry(url)
        return _envelope_result(envelope, category=category, symbol=symbol)

    def _envelope_with_retry(self, url: str) -> _BybitEnvelope:
        """The single bounded request authority for this adapter.

        Exactly two conditions are retryable, both under `RetryPolicy.maximum_attempts`
        and both paced by the shared `MinimumIntervalRequestPacer`:

        * an authorized transient HTTP status (`_RETRYABLE_STATUS_CODES`), and
        * an HTTP 200 carrying a structurally valid envelope whose `retCode` is
          exactly `BYBIT_RATE_LIMIT_RETURN_CODE`.

        Everything else — any other non-zero `retCode`, malformed JSON, a malformed
        envelope, a non-retryable status — fails closed on the attempt that saw it.
        Exhaustion re-raises the last failure with its canonical identity intact, so a
        rate limit that outlives the policy never degrades into an empty or stale page.
        """
        failure = ProviderError("bybit_http_status:network")
        for attempt in range(self._retry_policy.maximum_attempts):
            self._pacer.before_request()
            response = self._transport.get(url, self._configuration.request_timeout_seconds)
            if response.status_code == 200:
                envelope = _validated_envelope(response.body)
                if not envelope.rate_limited:
                    return envelope
                failure = envelope.provider_error()
                delay = self._rate_limit_delay(response.headers, attempt)
            else:
                failure = ProviderError(f"bybit_http_status:{response.status_code}")
                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    break
                delay = self._status_delay(response.headers, attempt)
            if attempt + 1 == self._retry_policy.maximum_attempts:
                break
            self._sleep(delay)
        raise failure

    def _status_delay(self, headers: dict[str, str], attempt: int) -> float:
        retry_after = _header(headers, _RETRY_AFTER_HEADER)
        if retry_after is not None and retry_after.replace(".", "", 1).isdigit():
            return float(retry_after)
        return self._backoff_delay(attempt)

    def _rate_limit_delay(self, headers: dict[str, str], attempt: int) -> float:
        reset_delay = self._reset_header_delay(headers)
        if reset_delay is not None:
            return reset_delay
        return self._backoff_delay(attempt)

    def _reset_header_delay(self, headers: dict[str, str]) -> float | None:
        """Seconds until the provider-published reset instant, or None if unusable.

        Absent, malformed, already-elapsed and implausibly distant reset timestamps all
        return None so the caller falls back to RetryPolicy backoff. The returned delay
        is therefore always strictly positive; a negative sleep is unrepresentable here.
        """
        raw = _header(headers, BYBIT_RATE_LIMIT_RESET_HEADER)
        if raw is None:
            return None
        text = raw.strip()
        if not text.isdigit():
            return None
        try:
            reset_at = _from_milliseconds(int(text))
        except (OverflowError, ValueError, OSError):
            return None
        remaining = reset_at - self._now()
        if remaining <= timedelta(0) or remaining > _MAXIMUM_RATE_LIMIT_RESET_HORIZON:
            return None
        return remaining.total_seconds()

    def _backoff_delay(self, attempt: int) -> float:
        return self._retry_policy.base_delay.total_seconds() * (2**attempt)


def _next_time_cursor(
    earliest_open_ms: int | None, *, start_ms: int, window_end_ms: int
) -> str | None:
    if earliest_open_ms is None:
        return None
    next_window_end_ms = earliest_open_ms - 1
    if next_window_end_ms < start_ms:
        return None
    if next_window_end_ms >= window_end_ms:
        raise ProviderError("bybit_kline_cursor_did_not_advance")
    return f"{TIME_CURSOR_PREFIX}{next_window_end_ms}"


def _next_page_cursor(result: dict[str, object]) -> str | None:
    provider_cursor = result.get("nextPageCursor")
    if provider_cursor is None:
        return None
    if not isinstance(provider_cursor, str):
        raise ProviderError("bybit_unexpected_open_interest_cursor")
    if not provider_cursor.strip():
        return None
    return f"{PAGE_CURSOR_PREFIX}{provider_cursor}"


def _trade_kline_payload(fields: tuple[str, ...], open_ms: int) -> dict[str, object]:
    return {
        "bar_timestamp_semantics": BAR_TIMESTAMP_SEMANTICS_MARKER_V1,
        "interval": _NORMALIZED_BAR_INTERVAL,
        "open": _decimal_text(fields[1], "bybit_unparseable_kline_value"),
        "high": _decimal_text(fields[2], "bybit_unparseable_kline_value"),
        "low": _decimal_text(fields[3], "bybit_unparseable_kline_value"),
        "close": _decimal_text(fields[4], "bybit_unparseable_kline_value"),
        "volume": _decimal_text(fields[5], "bybit_unparseable_kline_value"),
        "provider_category": BYBIT_LINEAR_CATEGORY,
        "provider_interval": BYBIT_KLINE_INTERVAL,
        "provider_turnover": _decimal_text(fields[6], "bybit_unparseable_kline_value"),
        "provider_bar_open_ms": open_ms,
        "provider_row": list(fields),
    }


def _reference_price_payload(
    scope: BybitScope, fields: tuple[str, ...], *, open_ms: int, bar_close_at: datetime
) -> dict[str, object]:
    return {
        "price": _decimal_text(fields[4], "bybit_unparseable_kline_value"),
        "price_asset": scope.quote_asset,
        "observed_at": bar_close_at.isoformat(),
        "methodology_reference": _REFERENCE_PRICE_METHODOLOGIES[scope.observation_kind],
        "provider_category": BYBIT_LINEAR_CATEGORY,
        "provider_interval": BYBIT_KLINE_INTERVAL,
        "provider_bar_open_ms": open_ms,
        "provider_open": _decimal_text(fields[1], "bybit_unparseable_kline_value"),
        "provider_high": _decimal_text(fields[2], "bybit_unparseable_kline_value"),
        "provider_low": _decimal_text(fields[3], "bybit_unparseable_kline_value"),
        "provider_close": _decimal_text(fields[4], "bybit_unparseable_kline_value"),
    }


def _open_interest_payload(
    scope: BybitScope, row: object
) -> tuple[dict[str, object], datetime]:
    if not isinstance(row, dict):
        raise ProviderError("bybit_unexpected_open_interest_row_shape")
    raw_value = row.get("openInterest")
    raw_timestamp = row.get("timestamp")
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ProviderError("bybit_unexpected_open_interest_row_shape")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        raise ProviderError("bybit_unexpected_open_interest_row_shape")
    timestamp_ms = _provider_milliseconds(raw_timestamp, "bybit_unparseable_open_interest_time")
    observed_at = _from_milliseconds(timestamp_ms)
    payload: dict[str, object] = {
        "open_interest": _decimal_text(raw_value, "bybit_unparseable_open_interest"),
        "unit": OpenInterestUnit.BASE_ASSET.value,
        "unit_asset": scope.base_asset,
        "observed_at": observed_at.isoformat(),
        "provider_category": BYBIT_LINEAR_CATEGORY,
        "provider_interval_time": BYBIT_OPEN_INTEREST_INTERVAL,
        "provider_timestamp_ms": timestamp_ms,
    }
    single = row.get("singleOpenInterest")
    if isinstance(single, str) and single.strip():
        payload["provider_single_open_interest"] = single
    return payload, observed_at
