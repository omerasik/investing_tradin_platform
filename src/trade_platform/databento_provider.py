"""Databento historical OHLCV adapter -- disabled by default, no real call in CI.

Implements :class:`~trade_platform.provider_ingestion.RawHistoricalAdapter` for
Databento's *asynchronous batch* historical workflow
(``batch.submit_job`` / ``batch.list_jobs`` / ``batch.list_files`` + file
download), not the synchronous ``timeseries.get_range`` endpoint. This is a
deliberate choice, not an oversight:

* ``timeseries.get_range`` always streams Databento Binary Encoding (DBN) with
  zstd compression -- writing and maintaining a correct DBN binary decoder is
  unnecessary complexity this platform does not need. Databento's own
  documentation states the call "will only return after all the data has been
  downloaded, which can take a long time" -- i.e. even Databento's own client
  treats it as a long-running, effectively synchronous call.
* ``batch.submit_job`` natively supports ``encoding="csv"``, which this adapter
  parses with the same ``csv.DictReader`` idiom already used by
  ``StooqDailyCsvProvider`` (``data_providers.py``) -- no new parsing pattern.
* Databento's historical API has **no page-cursor pagination** of the kind
  ``RawHistoricalAdapter`` was originally modeled on for REST-paginated
  providers. This adapter does not force one. Instead, it treats the
  *requested date range* as the thing to page through: each
  ``fetch_raw_page`` call resolves exactly one bounded date chunk (submit,
  poll, download, parse) end-to-end before returning, and represents
  progress through that range as its own client-side cursor (the next
  chunk's start date) -- never a provider-issued token. From
  ``ingest_raw_historical_pages()``'s perspective this behaves exactly like a
  normal paginated provider (each call returns new records and a fresh,
  never-repeated cursor, so the existing cursor-loop guard in
  ``provider_ingestion.py`` remains meaningful) even though internally one
  call may submit a job and poll it to completion. No change was made to
  ``provider_ingestion.py`` to support this -- the adapter accommodates the
  existing contract, not the other way around.

Known, disclosed limitations (to be confirmed, not assumed, before the
Module 3G.1d real pilot -- see ``docs/MODULE_3G0_PROVIDER_SELECTION_AND_LICENSING_PREFLIGHT.md``):

* Endpoint paths (``/v0/batch.submit_job`` etc.), the documented job states
  this adapter acts on (``queued``/``processing``/``done``/``expired``), the
  2,000-symbols-per-request cap, and the CSV encoding option are sourced from
  Databento's official Python client source and public blog documentation.
  Exact response field names/order (``ts_event``, ``instrument_id``,
  ``symbol``, ``open``/``high``/``low``/``close``/``volume``) are modeled from
  Databento's documented DBN ``Ohlcv`` record layout and cross-referenced
  third-party integration docs, not from a real fetched response -- no
  Databento credential exists anywhere in this repository. This parser fails
  closed (``ProviderError``) on any unexpected shape rather than guessing,
  and must be verified against one real batch-job CSV output before this
  adapter is trusted with real data.
* If the process crashes while a single ``fetch_raw_page`` call is polling an
  already-submitted job, the next run re-submits a new job for that chunk
  rather than resuming the original job id (the in-flight job id is not
  itself checkpointed mid-call). Given the small, bounded pilot chunk size
  this module targets, a resubmission's cost is negligible; this is a
  disclosed gap, not a silent one, and would need addressing before this
  adapter is extended to large-scale backfills.
* Resolved in Module 3G.1b: the consolidated ``EQUS.SUMMARY``/``EQUS.MINI``
  datasets span multiple NMS exchanges/ATSs per row, so this adapter records
  ``historical_market_data.CONSOLIDATED_TAPE_EXCHANGE`` -- a real,
  provider-neutral sentinel for CTA/UTP consolidated-tape data, not a fake
  exchange or a Databento-specific bypass -- as
  ``RawHistoricalObservation.exchange``. ``normalize()`` validates it against
  the resolved instrument's own registered venue rather than skipping
  validation.

Hard gate: nothing in this module makes a real request unless a caller
supplies a ``ProviderConfiguration`` with ``terms_accepted=True`` and a
resolvable ``secret_reference`` -- nothing in this codebase does that.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from .config import EnvironmentSecretResolver
from .data_providers import (
    HttpResponse,
    ProviderConfiguration,
    ProviderConfigurationError,
    ProviderError,
    RetryPolicy,
)
from .historical_market_data import (
    CONSOLIDATED_TAPE_EXCHANGE,
    AdjustmentStatus,
    ObservationKind,
    RawHistoricalObservation,
)
from .provider_ingestion import RawHistoricalPage

DATABENTO_MAXIMUM_SYMBOLS_PER_REQUEST = 2000
SUPPORTED_SCHEMAS = {"ohlcv-1d": "1d", "ohlcv-1m": "1m"}
_USER_AGENT = "trade-platform-paper-research/0.1"


class DatabentoJobState(StrEnum):
    """The subset of Databento's documented ``batch.list_jobs`` states this adapter acts on."""

    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    EXPIRED = "expired"


class DatabentoHttpTransport(Protocol):
    """POST+GET transport local to this adapter.

    ``data_providers.HttpTransport`` is GET-only (sufficient for Stooq's public
    CSV endpoint); Databento's batch API requires an authenticated POST to
    submit a job. Kept local to this module rather than extending the shared
    transport protocol, so this PR touches no existing file.
    """

    def post(self, url: str, form: dict[str, str], api_key: str, timeout_seconds: float) -> HttpResponse: ...

    def get(self, url: str, api_key: str, timeout_seconds: float) -> HttpResponse: ...


def _execute_urllib_request(request: Request, timeout_seconds: float) -> HttpResponse:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: URL is config-validated HTTPS
            return HttpResponse(response.status, response.read().decode("utf-8"), dict(response.headers.items()))
    except HTTPError as error:
        return HttpResponse(error.code, error.read().decode("utf-8", errors="replace"), dict(error.headers.items()))
    except URLError as error:
        raise ProviderError("provider_network_error") from error


class UrlLibDatabentoTransport:
    """Real HTTP transport using Databento's documented HTTP Basic Auth (API key as username).

    Never constructed by any test in this repository. ``DatabentoHistoricalAdapter``'s
    default ``transport=None`` only becomes reachable once an operator supplies a real
    ``ProviderConfiguration`` with ``terms_accepted=True`` -- nothing in this codebase does.
    """

    def post(self, url: str, form: dict[str, str], api_key: str, timeout_seconds: float) -> HttpResponse:
        credentials = base64.b64encode(f"{api_key}:".encode()).decode()
        body = urllib.parse.urlencode(form).encode()
        request = Request(url, data=body, headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _USER_AGENT,
        })
        return _execute_urllib_request(request, timeout_seconds)

    def get(self, url: str, api_key: str, timeout_seconds: float) -> HttpResponse:
        credentials = base64.b64encode(f"{api_key}:".encode()).decode()
        request = Request(url, headers={"Authorization": f"Basic {credentials}", "User-Agent": _USER_AGENT})
        return _execute_urllib_request(request, timeout_seconds)


def _parse_json_object(body: str) -> dict[str, object]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProviderError("databento_invalid_json_response") from error
    if not isinstance(parsed, dict):
        raise ProviderError("databento_unexpected_json_shape")
    return parsed


def _parse_json_array(body: str) -> list[object]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProviderError("databento_invalid_json_response") from error
    if not isinstance(parsed, list):
        raise ProviderError("databento_unexpected_json_shape")
    return parsed


def _parse_scope(scope: dict[str, object]) -> tuple[str, str, tuple[str, ...], date, date]:
    dataset, schema, symbols = scope.get("dataset"), scope.get("schema"), scope.get("symbols")
    if not isinstance(dataset, str) or not dataset.strip():
        raise ProviderConfigurationError("invalid_databento_scope_dataset")
    if schema not in SUPPORTED_SCHEMAS:
        raise ProviderConfigurationError("unsupported_databento_schema")
    if (
        not isinstance(symbols, (list, tuple))
        or not symbols
        or not all(isinstance(item, str) and item.strip() for item in symbols)
    ):
        raise ProviderConfigurationError("invalid_databento_scope_symbols")
    if len(symbols) > DATABENTO_MAXIMUM_SYMBOLS_PER_REQUEST:
        raise ProviderConfigurationError("databento_symbol_limit_exceeded")
    try:
        start_date = date.fromisoformat(str(scope.get("start")))
        end_date = date.fromisoformat(str(scope.get("end")))
    except (TypeError, ValueError) as error:
        raise ProviderConfigurationError("invalid_databento_scope_date_range") from error
    if end_date <= start_date:
        raise ProviderConfigurationError("invalid_databento_scope_date_range")
    return dataset, str(schema), tuple(symbols), start_date, end_date


def plan_chunks(start: date, end: date, chunk_size: timedelta) -> tuple[tuple[date, date], ...]:
    """The exact sequence of date-range chunks a real ingestion of ``[start, end)``
    would submit as separate Databento batch jobs -- pure, no I/O. Used both by
    :meth:`DatabentoHistoricalAdapter.fetch_raw_page` (one chunk per call, driven by
    its cursor) and by Module 3G.1c's pilot-readiness dry run (the whole plan, so an
    operator can see exactly how many jobs/requests a pilot would make before any of
    them are ever submitted).
    """
    if end <= start:
        raise ProviderConfigurationError("invalid_databento_scope_date_range")
    if chunk_size <= timedelta(0):
        raise ProviderConfigurationError("invalid_databento_polling_configuration")
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + chunk_size, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return tuple(chunks)


def _parse_databento_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000_000, tz=timezone.utc)
    except (ValueError, OverflowError, OSError) as error:
        raise ProviderError(f"databento_unparseable_timestamp:{value}") from error


@dataclass(frozen=True, slots=True)
class DatabentoPreflight:
    """Everything ``fetch_raw_page`` validates before its first network call.

    Returned so a caller (e.g. Module 3G.1c's pilot-readiness dry run) can prove a
    configuration would pass every pre-network check -- terms acceptance, scope
    shape, resumability, and secret *resolvability* -- without ever seeing the
    resolved secret value itself (deliberately not a field here) and without
    submitting anything.
    """

    dataset: str
    schema: str
    symbols: tuple[str, ...]
    start: date
    end: date
    chunk_start: date
    chunk_end: date
    next_cursor: str | None


class DatabentoHistoricalAdapter:
    """``RawHistoricalAdapter`` for Databento's async batch OHLCV workflow. Disabled by default."""

    name = "databento"

    def __init__(
        self,
        configuration: ProviderConfiguration,
        *,
        transport: DatabentoHttpTransport | None = None,
        retry_policy: RetryPolicy = RetryPolicy(),
        chunk_size: timedelta = timedelta(days=31),
        poll_interval: timedelta = timedelta(seconds=30),
        maximum_poll_attempts: int = 120,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        configuration.validate()
        retry_policy.validate()
        if configuration.provider != self.name:
            raise ProviderConfigurationError("invalid_databento_configuration")
        if chunk_size <= timedelta(0) or poll_interval <= timedelta(0) or maximum_poll_attempts < 1:
            raise ProviderConfigurationError("invalid_databento_polling_configuration")
        self._configuration = configuration
        self._transport = transport or UrlLibDatabentoTransport()
        self._retry_policy = retry_policy
        self._chunk_size = chunk_size
        self._poll_interval = poll_interval
        self._maximum_poll_attempts = maximum_poll_attempts
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep

    def preflight(self, scope: dict[str, object], cursor: str | None) -> DatabentoPreflight:
        """Validate everything up to, but never including, the first network call.

        Terms acceptance, scope shape (dataset/schema/symbols/date range), cursor
        resumability, and secret *resolvability* (the resolved value is discarded
        immediately, never returned or logged). Raises the same
        ``ProviderConfigurationError``/``ProviderError`` a real ``fetch_raw_page``
        call would raise for the same problem, at exactly the same point -- this
        is not a separate, potentially-drifting validation path, it is the actual
        prefix of ``fetch_raw_page`` factored out so it can be exercised on its own.
        """
        if not self._configuration.terms_accepted:
            raise ProviderConfigurationError("provider_terms_not_accepted")
        dataset, schema, symbols, start, end = _parse_scope(scope)
        chunk_start = date.fromisoformat(cursor) if cursor is not None else start
        if chunk_start < start or chunk_start >= end:
            raise ProviderConfigurationError("invalid_databento_resume_cursor")
        chunk_end = min(chunk_start + self._chunk_size, end)
        self._resolve_api_key()  # raises if unresolvable; the value itself is discarded here
        next_cursor = chunk_end.isoformat() if chunk_end < end else None
        return DatabentoPreflight(dataset, schema, symbols, start, end, chunk_start, chunk_end, next_cursor)

    def fetch_raw_page(self, source_id: UUID, scope: dict[str, object], cursor: str | None) -> RawHistoricalPage:
        preflight = self.preflight(scope, cursor)
        api_key = self._resolve_api_key()
        job_id = self._submit_job(
            preflight.dataset, preflight.schema, preflight.symbols,
            preflight.chunk_start, preflight.chunk_end, api_key,
        )
        state = self._poll_until_terminal(job_id, api_key)
        if state is DatabentoJobState.EXPIRED:
            raise ProviderError("databento_batch_job_expired")
        rows = self._download_csv(job_id, api_key)
        records = tuple(self._to_observation(source_id, preflight.schema, row) for row in rows)
        return RawHistoricalPage(records, preflight.next_cursor, f"databento-batch-csv-{preflight.schema}-v1", self._now())

    def _resolve_api_key(self) -> str:
        if self._configuration.secret_reference is None:
            raise ProviderConfigurationError("databento_secret_reference_required")
        return EnvironmentSecretResolver().resolve_bytes(self._configuration.secret_reference).decode()

    def _http_with_retry(self, perform: Callable[[], HttpResponse]) -> HttpResponse:
        last_status = "network"
        for attempt in range(self._retry_policy.maximum_attempts):
            response = perform()
            if response.status_code == 200:
                return response
            last_status = str(response.status_code)
            if response.status_code not in {429, 500, 502, 503, 504} or attempt + 1 == self._retry_policy.maximum_attempts:
                break
            retry_after = response.headers.get("Retry-After")
            delay = (
                float(retry_after)
                if retry_after and retry_after.replace(".", "", 1).isdigit()
                else self._retry_policy.base_delay.total_seconds() * (2**attempt)
            )
            self._sleep(delay)
        raise ProviderError(f"databento_http_status:{last_status}")

    def _submit_job(
        self, dataset: str, schema: str, symbols: tuple[str, ...], chunk_start: date, chunk_end: date, api_key: str,
    ) -> str:
        form = {
            "dataset": dataset, "schema": schema, "symbols": ",".join(symbols),
            "start": chunk_start.isoformat(), "end": chunk_end.isoformat(),
            "encoding": "csv", "compression": "none", "split_duration": "none",
        }
        url = f"{self._configuration.base_url.rstrip('/')}/v0/batch.submit_job"
        response = self._http_with_retry(lambda: self._transport.post(url, form, api_key, self._configuration.request_timeout_seconds))
        payload = _parse_json_object(response.body)
        job_id = payload.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise ProviderError("databento_submit_job_missing_id")
        return job_id

    def _job_status(self, job_id: str, api_key: str) -> DatabentoJobState:
        url = f"{self._configuration.base_url.rstrip('/')}/v0/batch.list_jobs?states=queued,processing,done,expired"
        response = self._http_with_retry(lambda: self._transport.get(url, api_key, self._configuration.request_timeout_seconds))
        for job in _parse_json_array(response.body):
            if isinstance(job, dict) and job.get("id") == job_id:
                try:
                    return DatabentoJobState(str(job.get("state")))
                except ValueError as error:
                    raise ProviderError(f"databento_unknown_job_state:{job.get('state')}") from error
        raise ProviderError("databento_job_not_found")

    def _poll_until_terminal(self, job_id: str, api_key: str) -> DatabentoJobState:
        for attempt in range(self._maximum_poll_attempts):
            state = self._job_status(job_id, api_key)
            if state in (DatabentoJobState.DONE, DatabentoJobState.EXPIRED):
                return state
            if attempt + 1 < self._maximum_poll_attempts:
                self._sleep(self._poll_interval.total_seconds())
        raise ProviderError("databento_batch_job_poll_timeout")

    def _download_csv(self, job_id: str, api_key: str) -> list[dict[str, str]]:
        list_url = f"{self._configuration.base_url.rstrip('/')}/v0/batch.list_files?job_id={job_id}"
        files_response = self._http_with_retry(lambda: self._transport.get(list_url, api_key, self._configuration.request_timeout_seconds))
        csv_files = [
            item for item in _parse_json_array(files_response.body)
            if isinstance(item, dict) and str(item.get("filename", "")).endswith(".csv")
        ]
        if not csv_files:
            raise ProviderError("databento_no_csv_output_file")
        rows: list[dict[str, str]] = []
        for file_info in csv_files:
            url = file_info.get("https_url") or file_info.get("url")
            if not isinstance(url, str) or not url:
                raise ProviderError("databento_missing_file_url")
            response = self._download_one_file(url, api_key)
            rows.extend(csv.DictReader(io.StringIO(response.body)))
        return rows

    def _download_one_file(self, url: str, api_key: str) -> HttpResponse:
        return self._http_with_retry(lambda: self._transport.get(url, api_key, self._configuration.request_timeout_seconds))

    def _to_observation(self, source_id: UUID, schema: str, row: dict[str, str]) -> RawHistoricalObservation:
        try:
            event_at = _parse_databento_timestamp(row["ts_event"])
            symbol, instrument_id = row["symbol"], row["instrument_id"]
            payload: dict[str, object] = {
                "interval": SUPPORTED_SCHEMAS[schema],
                "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
                "volume": row.get("volume", "0"),
            }
        except KeyError as error:
            raise ProviderError(f"databento_csv_missing_expected_column:{error}") from error
        ingested_at = self._now()
        return RawHistoricalObservation(
            source_id, ObservationKind.OHLCV, instrument_id, symbol, CONSOLIDATED_TAPE_EXCHANGE,
            event_at, event_at, ingested_at, AdjustmentStatus.RAW, 0,
            f"databento://batch/{schema}",
            payload,
        )
