"""Provider-neutral, checkpointable raw historical-ingestion orchestration.

The module deliberately has no HTTP client and no selected provider.  Concrete
adapters supply attributable raw pages; the orchestration records the actual adapter,
version stamp, continuation cursor and an explicit health result.  It never retries
through a different provider or manufactures a replacement record.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from .data_providers import ProviderError, ProviderHealthRegistry, ProviderOperationalStatus
from .historical_market_data import HistoricalMarketDataError, RawHistoricalObservation
from .persistence import PostgresDatabase


class ProviderIngestionError(ValueError):
    pass


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProviderIngestionError(f"{name}_must_be_timezone_aware")


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ProviderIngestionError(f"invalid_{name}")


def _canonical_scope(scope: dict[str, object]) -> str:
    if not scope:
        raise ProviderIngestionError("checkpoint_scope_required")
    try:
        return json.dumps(scope, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ProviderIngestionError("checkpoint_scope_not_canonical_json") from error


@dataclass(frozen=True, slots=True)
class RawHistoricalPage:
    """One provider response page, including evidence needed to resume it."""

    records: tuple[RawHistoricalObservation, ...]
    next_cursor: str | None
    provider_version: str
    retrieved_at: datetime

    def validate(self, source_id: UUID) -> None:
        _text(self.provider_version, "provider_version")
        _aware(self.retrieved_at, "page_retrieved_at")
        if self.next_cursor is not None and not self.next_cursor.strip():
            raise ProviderIngestionError("invalid_provider_page_cursor")
        for record in self.records:
            if record.source_id != source_id:
                raise ProviderIngestionError("provider_page_source_mismatch")
            record.validate()


class RawHistoricalAdapter(Protocol):
    """A selected provider will implement this only after authorization."""

    name: str

    def fetch_raw_page(self, source_id: UUID, scope: dict[str, object], cursor: str | None) -> RawHistoricalPage: ...


class RawHistoricalCaptureSink(Protocol):
    def capture_raw(self, observations: list[RawHistoricalObservation]) -> tuple[UUID, ...]: ...


@dataclass(frozen=True, slots=True)
class HistoricalIngestionRequest:
    source_id: UUID
    scope: dict[str, object]
    maximum_pages: int = 1000
    resume_cursor: str | None = None
    stale_after: timedelta = timedelta(hours=24)

    def validate(self) -> None:
        _canonical_scope(self.scope)
        if self.maximum_pages < 1:
            raise ProviderIngestionError("maximum_pages_must_be_positive")
        if self.resume_cursor is not None and not self.resume_cursor.strip():
            raise ProviderIngestionError("invalid_resume_cursor")
        if self.stale_after < timedelta(0):
            raise ProviderIngestionError("stale_after_must_not_be_negative")


@dataclass(frozen=True, slots=True)
class ProviderIngestionCheckpoint:
    """Append-only checkpoint record for a single raw-ingestion attempt."""

    source_id: UUID
    adapter_name: str
    scope: dict[str, object]
    state: ProviderOperationalStatus
    provider_version: str | None
    resume_cursor: str | None
    captured_count: int
    error_code: str | None
    recorded_at: datetime
    checkpoint_id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        _text(self.adapter_name, "adapter_name")
        _canonical_scope(self.scope)
        _aware(self.recorded_at, "checkpoint_recorded_at")
        if self.provider_version is not None:
            _text(self.provider_version, "provider_version")
        if self.resume_cursor is not None and not self.resume_cursor.strip():
            raise ProviderIngestionError("invalid_checkpoint_cursor")
        if self.captured_count < 0:
            raise ProviderIngestionError("invalid_checkpoint_captured_count")
        if self.state is ProviderOperationalStatus.ERROR:
            if not self.error_code:
                raise ProviderIngestionError("error_checkpoint_requires_error_code")
        elif self.error_code is not None:
            raise ProviderIngestionError("non_error_checkpoint_has_error_code")


@dataclass(frozen=True, slots=True)
class HistoricalIngestionOutcome:
    state: ProviderOperationalStatus
    checkpoint: ProviderIngestionCheckpoint
    captured_raw_ids: tuple[UUID, ...]


class PostgresHistoricalIngestionCheckpointStore:
    """Persists append-only operational evidence; no checkpoint is overwritten."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def record(self, checkpoint: ProviderIngestionCheckpoint) -> None:
        checkpoint.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO historical_ingestion_checkpoints VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)",
                    (
                        checkpoint.checkpoint_id, checkpoint.source_id, checkpoint.adapter_name,
                        _canonical_scope(checkpoint.scope), checkpoint.state.value,
                        checkpoint.provider_version, checkpoint.resume_cursor,
                        checkpoint.captured_count, checkpoint.error_code, checkpoint.recorded_at,
                    ),
                )
        except Exception as error:
            raise ProviderIngestionError("historical_ingestion_checkpoint_persistence_failed") from error

    def latest(
        self, source_id: UUID, adapter_name: str, scope: dict[str, object], as_of: datetime
    ) -> ProviderIngestionCheckpoint | None:
        _text(adapter_name, "adapter_name")
        canonical_scope = _canonical_scope(scope)
        _aware(as_of, "checkpoint_as_of")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT checkpoint_id,state,provider_version,resume_cursor,captured_count,error_code,recorded_at "
                "FROM historical_ingestion_checkpoints WHERE source_id=%s AND adapter_name=%s "
                "AND scope=%s::jsonb AND recorded_at<=%s ORDER BY recorded_at DESC,checkpoint_id DESC LIMIT 1",
                (source_id, adapter_name, canonical_scope, as_of),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return ProviderIngestionCheckpoint(
            source_id, adapter_name, scope, ProviderOperationalStatus(str(row[1])),
            None if row[2] is None else str(row[2]), None if row[3] is None else str(row[3]),
            int(row[4]), None if row[5] is None else str(row[5]), row[6], UUID(str(row[0])),
        )


def ingest_raw_historical_pages(
    adapter: RawHistoricalAdapter,
    request: HistoricalIngestionRequest,
    capture_sink: RawHistoricalCaptureSink,
    health: ProviderHealthRegistry,
    *,
    now: Callable[[], datetime],
) -> HistoricalIngestionOutcome:
    """Capture one adapter's pages with durable-resume evidence.

    A provider error, malformed page, cursor loop or exhausted page bound becomes an
    `ERROR` outcome. A successful but aged response becomes `STALE`. Neither condition
    invokes another adapter, and records already captured remain attributable to this
    attempt rather than being hidden or substituted.
    """

    request.validate()
    _text(adapter.name, "adapter_name")
    checked_at = now()
    _aware(checked_at, "ingestion_checked_at")
    cursor = request.resume_cursor
    seen_cursors: set[str] = set()
    captured: list[UUID] = []
    provider_version: str | None = None
    latest_retrieved_at: datetime | None = None

    def outcome(state: ProviderOperationalStatus, error_code: str | None = None) -> HistoricalIngestionOutcome:
        checkpoint = ProviderIngestionCheckpoint(
            request.source_id, adapter.name, request.scope, state, provider_version, cursor,
            len(captured), error_code, checked_at,
        )
        checkpoint.validate()
        return HistoricalIngestionOutcome(state, checkpoint, tuple(captured))

    for _ in range(request.maximum_pages):
        try:
            page = adapter.fetch_raw_page(request.source_id, request.scope, cursor)
            page.validate(request.source_id)
            provider_version = page.provider_version
            latest_retrieved_at = page.retrieved_at
            if page.records:
                captured.extend(capture_sink.capture_raw(list(page.records)))
        except (ProviderError, HistoricalMarketDataError, ProviderIngestionError) as error:
            health.failure(adapter.name, str(error))
            return outcome(ProviderOperationalStatus.ERROR, str(error))

        if page.next_cursor is None:
            if latest_retrieved_at is not None and checked_at - latest_retrieved_at > request.stale_after:
                health.stale(adapter.name, "provider_response_stale")
                cursor = None
                return outcome(ProviderOperationalStatus.STALE)
            health.success(adapter.name)
            cursor = None
            return outcome(ProviderOperationalStatus.HEALTHY)
        if page.next_cursor in seen_cursors:
            health.failure(adapter.name, "provider_pagination_cursor_loop")
            return outcome(ProviderOperationalStatus.ERROR, "provider_pagination_cursor_loop")
        seen_cursors.add(page.next_cursor)
        cursor = page.next_cursor

    health.failure(adapter.name, "provider_pagination_limit_exceeded")
    return outcome(ProviderOperationalStatus.ERROR, "provider_pagination_limit_exceeded")
