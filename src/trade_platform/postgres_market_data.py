"""PostgreSQL-backed implementation of the provider-neutral historical bar authority.

Mirrors :class:`trade_platform.market_data.SQLiteBarStore`'s validation and PIT-safety
semantics exactly (both reuse :func:`~trade_platform.market_data.assess_bars` and
:func:`~trade_platform.market_data.batch_digest` so there is exactly one place that
decides what makes an ``OHLCVBar`` batch valid and exactly one place that derives its
version stamp), but persists with exact ``NUMERIC`` precision, enforces uniqueness at
the database level via an immutable, append-only table, and treats a retried ingest of
the same identity as idempotent only when its content is byte-for-byte the same --
otherwise it fails closed rather than silently overwriting history.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import cast

from .domain import DataProcessingStatus, OHLCVBar
from .market_data import DataQualityError, assess_bars, batch_digest
from .persistence import PostgresDatabase


def _bar_content_hash(bar: OHLCVBar) -> str:
    """Identity is (instrument_id, interval, event_at, revision); this is everything else.

    ``ingested_at`` is deliberately excluded: a retried ingestion attempt naturally
    carries a later wall-clock ``ingested_at`` on each attempt, and that alone must
    not make an otherwise-identical bar look like a conflicting duplicate.
    """
    normalized = "|".join((
        bar.instrument_id, bar.interval, bar.event_at.isoformat(), str(bar.revision),
        bar.effective_at.isoformat(), str(bar.open), str(bar.high), str(bar.low), str(bar.close),
        str(bar.volume), bar.provider, bar.source_identifier, bar.original_timezone,
        bar.data_version, str(bar.quality_score), bar.processing_status.value,
    ))
    return hashlib.sha256(normalized.encode()).hexdigest()


class PostgresHistoricalBarStore:
    """Production-capable :class:`~trade_platform.market_data.HistoricalBarStore`."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def ingest(self, bars: list[OHLCVBar]) -> str:
        assessed = assess_bars(bars)
        rejected = [bar for bar in assessed if bar.processing_status is DataProcessingStatus.REJECTED]
        if rejected:
            raise DataQualityError("batch contains rejected data-quality records")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                for bar in assessed:
                    content_hash = _bar_content_hash(bar)
                    cursor.execute(
                        "INSERT INTO historical_bars VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (instrument_id,interval,event_at,revision) DO NOTHING "
                        "RETURNING instrument_id",
                        (
                            bar.instrument_id, bar.interval, bar.event_at, bar.effective_at, bar.ingested_at,
                            bar.open, bar.high, bar.low, bar.close, bar.volume, bar.provider,
                            bar.source_identifier, bar.original_timezone, bar.revision, bar.data_version,
                            bar.quality_score, bar.processing_status.value, content_hash,
                        ),
                    )
                    if cursor.fetchone() is not None:
                        continue
                    cursor.execute(
                        "SELECT content_hash FROM historical_bars "
                        "WHERE instrument_id=%s AND interval=%s AND event_at=%s AND revision=%s",
                        (bar.instrument_id, bar.interval, bar.event_at, bar.revision),
                    )
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != content_hash:
                        raise DataQualityError("conflicting_duplicate_bar_revision")
        except DataQualityError:
            raise
        except Exception as error:
            raise DataQualityError("historical_bar_persistence_failed") from error
        return batch_digest(assessed)

    def available_as_of(self, instrument_id: str, interval: str, decision_at: datetime) -> list[OHLCVBar]:
        """Only return records known and effective at the historical decision timestamp."""
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT instrument_id,interval,event_at,effective_at,ingested_at,open,high,low,close,"
                "volume,provider,source_identifier,original_timezone,revision,data_version,"
                "quality_score,processing_status FROM historical_bars "
                "WHERE instrument_id=%s AND interval=%s AND effective_at<=%s AND ingested_at<=%s "
                "ORDER BY event_at, revision",
                (instrument_id, interval, decision_at, decision_at),
            )
            rows = cursor.fetchall()
        return [_row_to_bar(row) for row in rows]

    def read_range(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]:
        """All stored revisions in an event-time window, for Data Health and research reads."""
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT instrument_id,interval,event_at,effective_at,ingested_at,open,high,low,close,"
                "volume,provider,source_identifier,original_timezone,revision,data_version,"
                "quality_score,processing_status FROM historical_bars "
                "WHERE instrument_id=%s AND interval=%s AND event_at>=%s AND event_at<=%s "
                "ORDER BY event_at, revision",
                (instrument_id, interval, start, end),
            )
            rows = cursor.fetchall()
        return [_row_to_bar(row) for row in rows]

    def known_series(self) -> list[tuple[str, str]]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT instrument_id, interval FROM historical_bars ORDER BY instrument_id, interval")
            rows = cursor.fetchall()
        return [(str(row[0]), str(row[1])) for row in rows]


def _row_to_bar(row: tuple[object, ...]) -> OHLCVBar:
    return OHLCVBar(
        str(row[0]), str(row[1]), cast(datetime, row[2]), cast(datetime, row[3]), cast(datetime, row[4]),
        cast(Decimal, row[5]), cast(Decimal, row[6]), cast(Decimal, row[7]), cast(Decimal, row[8]), cast(Decimal, row[9]),
        str(row[10]), str(row[11]), str(row[12]), cast(int, row[13]),
        str(row[14]), cast(Decimal, row[15]), DataProcessingStatus(str(row[16])),
    )
