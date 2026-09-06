"""Module 3G.1d Phase 1: read-only Databento cost/metadata/symbology preflight.

Purpose, and only purpose: let an operator determine the exact viable pilot
configuration and its real Databento cost *before* authorizing any ingestion.
This module makes real network calls once a credential exists, but ONLY to
endpoints Databento's own documentation states are billed at zero dollars:

* "You will only be billed for usage of time series data. Access to metadata,
  symbology, and account management is free." -- Historical API, Basics
  (fetched 2026-09-06, https://databento.com/docs/api-reference-historical).
* "The process of converting between one symbology type to another is called
  symbology resolution. This conversion can be done, for no cost, with the
  symbology.resolve endpoint." -- same page.

Hard structural boundary: this module defines NO method that calls
``batch.submit_job``, ``timeseries.get_range``, ``timeseries.get_range_async``,
or any other endpoint that streams or stages billable time-series data. The
only two RPC families reachable through :class:`DatabentoCostPreflightClient`
are ``metadata.*`` (GET) and ``symbology.resolve`` (POST) -- confirmed against
Databento's current HTTP API reference (curl examples), not assumed from the
Python client or from this repository's own ``databento_provider.py``:

* ``GET https://hist.databento.com/v0/metadata.list_datasets``
* ``GET https://hist.databento.com/v0/metadata.list_schemas``
* ``GET https://hist.databento.com/v0/metadata.get_dataset_range``
* ``GET https://hist.databento.com/v0/metadata.get_cost`` -> a bare JSON
  number, "The cost in US dollars."
* ``GET https://hist.databento.com/v0/metadata.get_billable_size`` -> a bare
  JSON number, "The size in number of bytes used for billing."
* ``POST https://hist.databento.com/v0/symbology.resolve`` -> a JSON object
  with ``result``/``partial``/``not_found`` keys.

Authentication is the same documented HTTP Basic Auth (API key as username,
blank password) already implemented by ``UrlLibDatabentoTransport`` in
``databento_provider.py`` -- reused here verbatim, no new transport code.

Still gated exactly like the disabled-by-default adapter: nothing in this
module makes a real request unless a caller supplies a ``ProviderConfiguration``
with ``terms_accepted=True`` and a resolvable ``secret_reference``. This is a
repository policy choice stricter than Databento's own billing model (which
would allow an unauthenticated-terms metadata call); it keeps a single,
uniform gate in front of every real Databento network call this codebase can
make, metadata or not.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .config import EnvironmentSecretResolver
from .data_providers import ProviderConfiguration, ProviderConfigurationError, ProviderError
from .databento_provider import DatabentoHttpTransport, UrlLibDatabentoTransport

_METADATA_BASE_PATH = "v0"


def _join_symbols(symbols: tuple[str, ...]) -> str:
    return ",".join(symbols)


def _parse_json_number(body: str) -> Decimal:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProviderError("databento_invalid_json_response") from error
    if isinstance(parsed, bool) or not isinstance(parsed, (int, float)):
        raise ProviderError("databento_unexpected_json_shape")
    try:
        return Decimal(str(parsed))
    except InvalidOperation as error:
        raise ProviderError("databento_unparseable_cost_value") from error


def _parse_json_object(body: str) -> dict[str, object]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProviderError("databento_invalid_json_response") from error
    if not isinstance(parsed, dict):
        raise ProviderError("databento_unexpected_json_shape")
    return parsed


def _parse_json_string_array(body: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProviderError("databento_invalid_json_response") from error
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ProviderError("databento_unexpected_json_shape")
    return tuple(parsed)


class SymbolAvailability(StrEnum):
    """Per-symbol classification an operator needs before authorizing a pull."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    AMBIGUOUS = "AMBIGUOUS_REQUIRES_SYMBOLOGY_RESOLUTION"


@dataclass(frozen=True, slots=True)
class DatasetRange:
    start: str
    end: str
    schema_ranges: dict[str, tuple[str, str]]


@dataclass(frozen=True, slots=True)
class SymbologyResolution:
    resolved: dict[str, tuple[str, ...]]
    partial: tuple[str, ...]
    not_found: tuple[str, ...]

    def classify(self, symbol: str) -> SymbolAvailability:
        if symbol in self.not_found:
            return SymbolAvailability.UNAVAILABLE
        if symbol in self.partial:
            return SymbolAvailability.AMBIGUOUS
        mapped = self.resolved.get(symbol)
        if not mapped:
            return SymbolAvailability.UNAVAILABLE
        if len(set(mapped)) > 1:
            # Same raw symbol resolved to more than one distinct instrument
            # id across the window -- a rename/relist/PIT identity change,
            # not a simple continuous mapping. Requires human symbology review.
            return SymbolAvailability.AMBIGUOUS
        return SymbolAvailability.AVAILABLE


class DatabentoCostPreflightClient:
    """Read-only wrapper around Databento's documented $0 metadata/symbology API.

    Structurally incapable of submitting a batch job or streaming time series:
    no such method exists on this class, and it is never given a transport
    method that could reach ``batch.submit_job`` or ``timeseries.get_range``.
    """

    def __init__(
        self,
        configuration: ProviderConfiguration,
        *,
        transport: DatabentoHttpTransport | None = None,
    ) -> None:
        configuration.validate()
        if configuration.provider != "databento":
            raise ProviderConfigurationError("invalid_databento_configuration")
        self._configuration = configuration
        self._transport = transport or UrlLibDatabentoTransport()

    def _resolve_api_key(self) -> str:
        if not self._configuration.terms_accepted:
            raise ProviderConfigurationError("provider_terms_not_accepted")
        if self._configuration.secret_reference is None:
            raise ProviderConfigurationError("databento_secret_reference_required")
        return EnvironmentSecretResolver().resolve_bytes(self._configuration.secret_reference).decode()

    def _get(self, method: str, params: dict[str, object]) -> str:
        api_key = self._resolve_api_key()
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self._configuration.base_url.rstrip('/')}/{_METADATA_BASE_PATH}/{method}"
        if query:
            url = f"{url}?{query}"
        response = self._transport.get(url, api_key, self._configuration.request_timeout_seconds)
        if response.status_code not in (200, 206):
            raise ProviderError(f"databento_http_status:{response.status_code}")
        return response.body

    def _post(self, method: str, form: dict[str, str]) -> str:
        api_key = self._resolve_api_key()
        url = f"{self._configuration.base_url.rstrip('/')}/{_METADATA_BASE_PATH}/{method}"
        response = self._transport.post(url, form, api_key, self._configuration.request_timeout_seconds)
        if response.status_code not in (200, 206):
            raise ProviderError(f"databento_http_status:{response.status_code}")
        return response.body

    def list_datasets(self) -> tuple[str, ...]:
        return _parse_json_string_array(self._get("metadata.list_datasets", {}))

    def list_schemas(self, dataset: str) -> tuple[str, ...]:
        return _parse_json_string_array(self._get("metadata.list_schemas", {"dataset": dataset}))

    def get_dataset_range(self, dataset: str) -> DatasetRange:
        payload = _parse_json_object(self._get("metadata.get_dataset_range", {"dataset": dataset}))
        start, end, schema = payload.get("start"), payload.get("end"), payload.get("schema")
        if not isinstance(start, str) or not isinstance(end, str) or not isinstance(schema, dict):
            raise ProviderError("databento_unexpected_json_shape")
        schema_ranges: dict[str, tuple[str, str]] = {}
        for schema_name, bounds in schema.items():
            if not isinstance(bounds, dict) or not isinstance(bounds.get("start"), str) or not isinstance(bounds.get("end"), str):
                raise ProviderError("databento_unexpected_json_shape")
            schema_ranges[schema_name] = (bounds["start"], bounds["end"])
        return DatasetRange(start, end, schema_ranges)

    def get_cost(self, dataset: str, symbols: tuple[str, ...], schema: str, start: date, end: date) -> Decimal:
        body = self._get("metadata.get_cost", {
            "dataset": dataset, "symbols": _join_symbols(symbols), "schema": schema,
            "start": start.isoformat(), "end": end.isoformat(), "stype_in": "raw_symbol",
        })
        return _parse_json_number(body)

    def get_billable_size(self, dataset: str, symbols: tuple[str, ...], schema: str, start: date, end: date) -> int:
        body = self._get("metadata.get_billable_size", {
            "dataset": dataset, "symbols": _join_symbols(symbols), "schema": schema,
            "start": start.isoformat(), "end": end.isoformat(), "stype_in": "raw_symbol",
        })
        size = _parse_json_number(body)
        if size != size.to_integral_value():
            raise ProviderError("databento_unexpected_json_shape")
        return int(size)

    def resolve_symbols(
        self, dataset: str, symbols: tuple[str, ...], start_date: date, end_date: date,
    ) -> SymbologyResolution:
        payload = _parse_json_object(self._post("symbology.resolve", {
            "dataset": dataset, "symbols": _join_symbols(symbols),
            "stype_in": "raw_symbol", "stype_out": "instrument_id",
            "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        }))
        result, partial, not_found = payload.get("result"), payload.get("partial"), payload.get("not_found")
        if not isinstance(result, dict) or not isinstance(partial, list) or not isinstance(not_found, list):
            raise ProviderError("databento_unexpected_json_shape")
        resolved: dict[str, tuple[str, ...]] = {}
        for symbol, mappings in result.items():
            if not isinstance(mappings, list) or not all(isinstance(item, dict) and isinstance(item.get("s"), str) for item in mappings):
                raise ProviderError("databento_unexpected_json_shape")
            resolved[symbol] = tuple(item["s"] for item in mappings)
        return SymbologyResolution(resolved, tuple(partial), tuple(not_found))


@dataclass(frozen=True, slots=True)
class PilotLegRequest:
    """One leg (e.g. "daily" or "minute") of a candidate pilot -- request only, no evidence."""

    name: str
    dataset: str
    schema: str
    symbols: tuple[str, ...]
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class PilotLegPreflightResult:
    leg: PilotLegRequest
    dataset_range: DatasetRange
    symbol_status: dict[str, SymbolAvailability]
    estimated_cost_usd: Decimal
    estimated_billable_size_bytes: int


@dataclass(frozen=True, slots=True)
class PilotPreflightReport:
    legs: tuple[PilotLegPreflightResult, ...]

    @property
    def combined_estimated_cost_usd(self) -> Decimal:
        return sum((leg.estimated_cost_usd for leg in self.legs), Decimal("0"))

    @property
    def combined_estimated_billable_size_bytes(self) -> int:
        return sum(leg.estimated_billable_size_bytes for leg in self.legs)

    @property
    def any_symbol_not_available(self) -> bool:
        return any(
            status is not SymbolAvailability.AVAILABLE
            for leg in self.legs
            for status in leg.symbol_status.values()
        )


def run_pilot_cost_preflight(
    client: DatabentoCostPreflightClient, legs: tuple[PilotLegRequest, ...],
) -> PilotPreflightReport:
    """Execute every read-only check for each leg and return one combined report.

    Never calls anything but the free ``metadata.*``/``symbology.resolve``
    methods on ``client`` -- no batch submission, no data download, whatever
    the caller passes as ``legs``.
    """
    results: list[PilotLegPreflightResult] = []
    for leg in legs:
        dataset_range = client.get_dataset_range(leg.dataset)
        resolution = client.resolve_symbols(leg.dataset, leg.symbols, leg.start, leg.end)
        symbol_status = {symbol: resolution.classify(symbol) for symbol in leg.symbols}
        cost = client.get_cost(leg.dataset, leg.symbols, leg.schema, leg.start, leg.end)
        billable_size = client.get_billable_size(leg.dataset, leg.symbols, leg.schema, leg.start, leg.end)
        results.append(PilotLegPreflightResult(leg, dataset_range, symbol_status, cost, billable_size))
    return PilotPreflightReport(tuple(results))


#: Module 3G.1d Phase 1 owner-approved candidate pilot (see
#: docs/MODULE_3G1D_PHASE1_COST_PREFLIGHT.md). TWTR is deliberately excluded --
#: reserved as a later delisting/PIT validation case, per owner instruction.
PHASE1_DAILY_LEG = PilotLegRequest(
    name="daily",
    dataset="EQUS.SUMMARY",
    schema="ohlcv-1d",
    symbols=(
        "AAPL", "MSFT", "AMZN", "GOOGL", "NVDA", "META", "TSLA", "JPM", "KO", "XOM",
        "SPY", "QQQ", "GLD", "VTI", "IVV",
    ),
    start=date(2020, 1, 1),
    end=date(2026, 9, 1),
)

PHASE1_MINUTE_LEG = PilotLegRequest(
    name="minute",
    dataset="EQUS.MINI",
    schema="ohlcv-1m",
    symbols=("AAPL", "MSFT", "NVDA", "SPY", "QQQ"),
    start=date(2026, 8, 1),
    end=date(2026, 9, 1),
)

PHASE1_PILOT_LEGS: tuple[PilotLegRequest, ...] = (PHASE1_DAILY_LEG, PHASE1_MINUTE_LEG)
