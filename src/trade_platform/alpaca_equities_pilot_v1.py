"""P3A offline Alpaca IEX contract pilot; real access remains EXTERNAL_BLOCKED.

No HTTP transport, credentials, source registration, scheduler or activation switch
is provided. Explicit test-authorized sources and provider-shaped response fixtures
exercise the existing raw/normalized/bar/Data Health/sealed-dataset authorities.
Alpaca Basic's free entitlement is not evidence of this operator's data rights.

Only unadjusted one-minute AAPL/SPY bars are modeled. IEX is the execution feed,
not the primary listing venue, not SIP and not an official closing-price source.
Missing IEX minutes can mean no eligible trades; the conservative pilot coverage
gate rejects them instead of inventing bars. Corrections have no published revision
identifier in this response: revision stays zero and changed evidence conflicts in
the existing immutable capture store, requiring explicit reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from types import MappingProxyType
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID, uuid5

from .data_providers import HttpResponse, ProviderCapabilities, ProviderConfiguration, ProviderError
from .historical_market_data import (
    AdjustmentStatus,
    AssetScope,
    AuthorizedHistoricalSource,
    ObservationKind,
    RawHistoricalObservation,
    normalize_payload,
)
from .professional_instruments import InstrumentMasterError, ProfessionalInstrument
from .provider_ingestion import RawHistoricalPage

PROVIDER = "alpaca_iex"
DATASET = "iex-raw-1min"
NAMESPACE = "ALPACA:IEX:RAW:1MIN:SYMBOL:V1"
VERSION = "alpaca-iex-raw-1min-fixture-v1"
BASE_URL = "https://data.alpaca.markets"
PILOT_INSTRUMENTS = MappingProxyType({"AAPL": "US:XNAS:AAPL", "SPY": "US:ARCX:SPY"})
MINUTE = timedelta(minutes=1)
MAXIMUM_PAGES = 40
PAGE_LIMIT = 100


class PilotInstrumentMaster(Protocol):
    def resolve_identifier_point_in_time(
        self, namespace: str, value: str, effective_at: datetime, known_at: datetime,
    ) -> ProfessionalInstrument: ...

    def is_open(self, venue: str, observed_at: datetime, *, known_at: datetime | None = None) -> bool: ...


def _instant(value: object) -> datetime:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?(?:Z|\+00:00)", value) is None:
        raise ProviderError("alpaca_timestamp_required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ProviderError("alpaca_invalid_timestamp") from error
    if parsed.utcoffset() != timedelta(0) or parsed.second or parsed.microsecond:
        raise ProviderError("alpaca_requires_utc_minute_grid")
    return parsed.astimezone(UTC)


def pilot_scope(scope: dict[str, object]) -> tuple[str, datetime, datetime]:
    if set(scope) != {"symbol", "start", "end", "feed", "adjustment", "timeframe", "asof"}:
        raise ProviderError("alpaca_exact_scope_required")
    symbol = scope["symbol"]
    if not isinstance(symbol, str) or symbol not in PILOT_INSTRUMENTS:
        raise ProviderError("alpaca_pilot_symbol_not_supported")
    if (scope["feed"], scope["adjustment"], scope["timeframe"], scope["asof"]) != ("iex", "raw", "1Min", "-"):
        raise ProviderError("alpaca_requires_iex_raw_1min_no_symbol_remapping")
    start, end = _instant(scope["start"]), _instant(scope["end"])
    if not timedelta(0) < end - start <= timedelta(days=5):
        raise ProviderError("alpaca_pilot_window_must_be_at_most_five_days")
    return symbol, start, end


def _scope_hash(scope: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cursor(scope: dict[str, object], token: str, page: int) -> str:
    return json.dumps({"scope": _scope_hash(scope), "page_token": token, "page": page}, sort_keys=True)


def _token(scope: dict[str, object], cursor: str | None) -> tuple[str | None, int]:
    if cursor is None:
        return None, 0
    try:
        item = json.loads(cursor)
        if set(item) != {"scope", "page_token", "page"} or item["scope"] != _scope_hash(scope):
            raise ValueError
        token = item["page_token"]
        if not isinstance(token, str) or not token.strip() or len(token) > 4096:
            raise ValueError
        page = item["page"]
        if type(page) is not int or not 0 < page < MAXIMUM_PAGES:
            raise ValueError
        return token, page
    except (ValueError, TypeError, KeyError) as error:
        raise ProviderError("alpaca_cursor_scope_mismatch") from error


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderError("alpaca_duplicate_json_key")
        result[key] = value
    return result


class _NumberLexeme(str):
    """Keep exact JSON numerics distinguishable from opaque string tokens."""


def _instrument(instruments: PilotInstrumentMaster, symbol: str, event: datetime, known_at: datetime) -> ProfessionalInstrument:
    try:
        instrument = instruments.resolve_identifier_point_in_time(NAMESPACE, symbol, event, known_at)
    except InstrumentMasterError as error:
        raise ProviderError("alpaca_instrument_evidence_missing") from error
    if instrument.instrument_id != PILOT_INSTRUMENTS[symbol]:
        raise ProviderError("alpaca_canonical_instrument_mismatch")
    return instrument


def _is_open(instruments: PilotInstrumentMaster, venue: str, event: datetime, known_at: datetime) -> bool:
    try:
        return instruments.is_open(venue, event, known_at=known_at)
    except InstrumentMasterError as error:
        raise ProviderError("alpaca_calendar_evidence_missing") from error


class AlpacaIexFixtureAdapterV1:
    """RawHistoricalAdapter with an explicit offline-only admission boundary.

    Mapping keys are provider page tokens (None for the first page). No supplied
    fixture, or any non-test authorization, fails before parsing or capture.
    """

    name = PROVIDER
    capabilities = ProviderCapabilities(
        PROVIDER, frozenset({"EQUITY", "ETF"}), frozenset({"1m"}),
        True, False, True, False, 200, None, "EXTERNAL_BLOCKED",
    )

    def __init__(
        self, source: AuthorizedHistoricalSource, instruments: PilotInstrumentMaster, *,
        fixture_pages: Mapping[str | None, HttpResponse] | None = None,
        configuration: ProviderConfiguration | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.source = source
        self.instruments = instruments
        self.configuration = configuration or ProviderConfiguration(PROVIDER, BASE_URL)
        self._pages = None if fixture_pages is None else MappingProxyType(dict(fixture_pages))
        self._now = now

    def fetch_raw_page(self, source_id: UUID, scope: dict[str, object], cursor: str | None) -> RawHistoricalPage:
        self.configuration.validate()
        if self._pages is None:
            raise ProviderError("alpaca_real_access_external_blocked")
        self.source.validate()
        if (
            source_id != self.source.source_id
            or (self.source.provider, self.source.dataset_name, self.source.provider_identifier_namespace)
            != (PROVIDER, DATASET, NAMESPACE)
            or self.source.scope() is not AssetScope.US_EQUITIES_ETFS
            or self.source.resolved_capabilities() != frozenset({ObservationKind.OHLCV})
            or not self.source.authorization_reference.startswith("test-authorization://")
            or (self.configuration.provider, self.configuration.base_url) != (PROVIDER, BASE_URL)
            or self.configuration.secret_reference is not None
            or self.configuration.terms_accepted
        ):
            raise ProviderError("alpaca_offline_fixture_authorization_required")
        symbol, start, end = pilot_scope(scope)
        retrieved = self._now()
        if retrieved.utcoffset() != timedelta(0) or end > retrieved - timedelta(minutes=15):
            raise ProviderError("alpaca_pilot_requires_closed_historical_window")
        token, page_number = _token(scope, cursor)
        if token not in self._pages:
            raise ProviderError("alpaca_fixture_page_missing")
        response = self._pages[token]
        if response.status_code != 200:
            # No auto-retry, fallback, upgrade, or successful partial retrieval.
            raise ProviderError(f"alpaca_http_status:{response.status_code}")
        if len(response.body.encode()) > 262144:
            raise ProviderError("alpaca_response_size_limit")
        try:
            body = json.loads(response.body, parse_float=_NumberLexeme, parse_int=_NumberLexeme, object_pairs_hook=_json_object)
        except (ValueError, TypeError) as error:
            raise ProviderError("alpaca_invalid_json") from error
        if not isinstance(body, dict) or set(body) != {"symbol", "bars", "next_page_token"} or body["symbol"] != symbol:
            raise ProviderError("alpaca_response_envelope_mismatch")
        rows = body["bars"]
        next_token = body["next_page_token"]
        if next_token is not None and (type(next_token) is not str or not next_token.strip() or len(next_token) > 4096):
            raise ProviderError("alpaca_invalid_page_token")
        if next_token is not None and (next_token == token or page_number + 1 >= MAXIMUM_PAGES):
            raise ProviderError("alpaca_pagination_not_complete_within_bound")
        if not isinstance(rows, list) or not rows or len(rows) > PAGE_LIMIT:
            raise ProviderError("alpaca_empty_or_oversized_page")
        # API end is inclusive; this pilot contract is [start, end).
        query = {**{k: str(v) for k, v in scope.items() if k != "symbol"},
                 "end": (end - timedelta(microseconds=1)).isoformat(), "sort": "asc", "limit": str(PAGE_LIMIT), "currency": "USD"}
        if token is not None:
            query["page_token"] = token
        provenance = f"{BASE_URL}/v2/stocks/{symbol}/bars?{urlencode(query)}"
        records: list[RawHistoricalObservation] = []
        previous: datetime | None = None
        for row in rows:
            if not isinstance(row, dict) or not {"t", "o", "h", "l", "c", "v", "n", "vw"} <= set(row):
                raise ProviderError("alpaca_invalid_bar_shape")
            event = _instant(row["t"])
            if not start <= event < end or (previous is not None and event <= previous):
                raise ProviderError("alpaca_duplicate_regressed_or_outside_window_bar")
            previous = event
            instrument = _instrument(self.instruments, symbol, event, retrieved)
            if not _is_open(self.instruments, instrument.venue, event, retrieved):
                raise ProviderError("alpaca_bar_outside_registered_session")
            payload: dict[str, object] = {"interval": "1m"}
            for key, field in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v")):
                value = row[field]
                try:
                    if not isinstance(value, str) or not Decimal(value).is_finite():
                        raise InvalidOperation
                    # Existing historical_bars NUMERIC(30,12) is authoritative.
                    # Admit exact representability only; never silently round a
                    # provider value to make the fixed PostgreSQL contract fit.
                    number = Decimal(value)
                    with localcontext() as context:
                        context.prec = 50
                        canonical = number.quantize(Decimal("0.000000000001"))
                    if canonical != number or number.copy_abs() >= Decimal("1e18"):
                        raise ProviderError("alpaca_numeric_exceeds_bar_precision")
                except (InvalidOperation, ValueError) as error:
                    raise ProviderError("alpaca_invalid_numeric_bar") from error
                payload[key] = format(canonical, ".12f")
            _, issues = normalize_payload(ObservationKind.OHLCV, payload)
            if issues:
                raise ProviderError("alpaca_invalid_ohlcv:" + ",".join(issues))
            payload.update({
                "feed": "iex", "execution_mic": "IEXG", "listing_mic": instrument.mic,
                "adjustment": "raw", "asof": "-", "currency": "USD", "provider_version": VERSION,
                "response_body": response.body, "response_sha256": hashlib.sha256(response.body.encode()).hexdigest(),
                "request_scope": dict(scope), "request_cursor": cursor, "provider_bar": row,
                "fixture_only": True, "evidence_tier": "T0_SYNTHETIC",
            })
            record = RawHistoricalObservation(
                source_id, ObservationKind.OHLCV, symbol, symbol, "IEXG", event, event + MINUTE,
                retrieved, AdjustmentStatus.RAW, 0, provenance, payload,
                uuid5(source_id, f"{VERSION}:{symbol}:{event.isoformat()}:0"),
            )
            record.validate()
            records.append(record)
        return RawHistoricalPage(tuple(records), None if next_token is None else _cursor(scope, next_token, page_number + 1), VERSION, retrieved)


def validate_pilot_coverage(
    records: Sequence[RawHistoricalObservation], scope: dict[str, object],
    instruments: PilotInstrumentMaster, *, known_at: datetime,
) -> None:
    """Required pre-seal gate, beyond successful pagination; no zero-fill.

    Both completely absent sessions and a missing minute fail. Closed sessions,
    holidays, DST and early closes use the existing PIT calendar authority.
    This gate is deliberately stricter than IEX's no-trade-minute behaviour.
    """
    symbol, start, end = pilot_scope(scope)
    if known_at.utcoffset() != timedelta(0) or known_at < end:
        raise ProviderError("alpaca_invalid_coverage_knowledge_time")
    instrument = _instrument(instruments, symbol, start, known_at)
    expected: set[datetime] = set()
    instant = start
    while instant < end:
        if _is_open(instruments, instrument.venue, instant, known_at):
            expected.add(instant)
        instant += MINUTE
    actual = [r.event_at for r in records]
    if not expected or len(actual) != len(set(actual)) or actual != sorted(actual) or set(actual) != expected:
        raise ProviderError("alpaca_incomplete_duplicate_or_regressed_pilot_coverage")
    for record in records:
        record.validate()
        if (
            record.source_id != records[0].source_id
            or record.provider_identifier != symbol or record.provider_symbol != symbol
            or record.exchange != "IEXG" or record.adjustment_status is not AdjustmentStatus.RAW
            or record.observation_kind is not ObservationKind.OHLCV
            or record.effective_at != record.event_at + MINUTE or record.ingested_at > known_at
            or record.raw_payload.get("request_scope") != scope
            or record.raw_payload.get("fixture_only") is not True
        ):
            raise ProviderError("alpaca_pilot_evidence_mismatch")
