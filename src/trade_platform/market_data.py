"""Provider-neutral historical OHLCV validation and versioned local storage."""

import hashlib
import sqlite3
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from .domain import DataProcessingStatus, OHLCVBar


class DataQualityError(ValueError):
    pass


class HistoricalBarProvider(Protocol):
    """Provider boundary: adapters return raw, attributable records and never bypass validation."""

    name: str

    def fetch_bars(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]: ...


class HistoricalBarStore(Protocol):
    """Provider-neutral historical OHLCV bar authority boundary.

    Both :class:`SQLiteBarStore` (local/demo/test) and the PostgreSQL-backed
    ``PostgresHistoricalBarStore`` (``postgres_market_data.py``, production/staging)
    satisfy this structurally -- callers such as :func:`ingest_from_provider` and the
    Data Health worker job depend on this boundary rather than on either concrete
    implementation.
    """

    def ingest(self, bars: list[OHLCVBar]) -> str: ...

    def available_as_of(self, instrument_id: str, interval: str, decision_at: datetime) -> list[OHLCVBar]: ...

    def read_range(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]: ...

    def known_series(self) -> list[tuple[str, str]]: ...


class FixtureBarProvider:
    """Deterministic test adapter; production providers must implement the same narrow contract."""

    name = "fixture"

    def __init__(self, bars: list[OHLCVBar]) -> None:
        self._bars = tuple(bars)

    def fetch_bars(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]:
        return [
            bar for bar in self._bars
            if bar.instrument_id == instrument_id and bar.interval == interval and start <= bar.event_at <= end
        ]


class PrecomputedBarProvider:
    """``HistoricalBarProvider`` over bars already produced by a real, named source.

    For providers whose bars are derived from an already-validated upstream pipeline
    (e.g. ``historical_market_data.py``'s raw-capture/normalize path) rather than
    fetched live -- the pipeline computes the bars once, then hands them to
    :func:`ingest_from_provider` through this adapter so the existing provenance check
    (``bar.provider`` must be in ``provenance_names``) still runs unchanged. Unlike
    :class:`FixtureBarProvider`, ``name`` reflects the real upstream provider, not a
    fixed test literal.
    """

    def __init__(self, name: str, bars: list[OHLCVBar]) -> None:
        if not name.strip():
            raise DataQualityError("precomputed_bar_provider_requires_a_name")
        self.name = name
        self._bars = tuple(bars)

    def fetch_bars(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]:
        return [
            bar for bar in self._bars
            if bar.instrument_id == instrument_id and bar.interval == interval and start <= bar.event_at <= end
        ]


def ingest_from_provider(
    provider: HistoricalBarProvider, store: HistoricalBarStore, instrument_id: str, interval: str, start: datetime, end: datetime
) -> str:
    bars = provider.fetch_bars(instrument_id, interval, start, end)
    permitted_provenance = getattr(provider, "provenance_names", frozenset({provider.name}))
    if any(bar.provider not in permitted_provenance for bar in bars):
        raise DataQualityError("provider_provenance_mismatch")
    return store.ingest(bars)


def assess_bars(bars: list[OHLCVBar]) -> list[OHLCVBar]:
    """Validate a same-instrument/interval batch without silently repairing its history."""
    if not bars:
        raise DataQualityError("empty_batch")
    seen: set[tuple[str, str, object, int]] = set()
    previous_event = None
    assessed: list[OHLCVBar] = []
    for bar in bars:
        errors: list[str] = []
        key = (bar.instrument_id, bar.interval, bar.event_at, bar.revision)
        if key in seen:
            errors.append("duplicate_bar")
        seen.add(key)
        if previous_event is not None and bar.event_at <= previous_event:
            errors.append("time_regression")
        previous_event = bar.event_at
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            errors.append("non_positive_price")
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            errors.append("invalid_ohlc_range")
        if bar.volume < 0:
            errors.append("negative_volume")
        if bar.effective_at < bar.event_at or bar.ingested_at < bar.event_at:
            errors.append("invalid_timestamp_semantics")
        score = Decimal("1") - Decimal("0.2") * len(errors)
        if errors:
            assessed.append(replace(bar, quality_score=max(score, Decimal("0")), processing_status=DataProcessingStatus.REJECTED))
        else:
            assessed.append(replace(bar, quality_score=score, processing_status=DataProcessingStatus.VALIDATED))
    return assessed


def batch_digest(bars: list[OHLCVBar]) -> str:
    """A single deterministic version stamp for an assessed batch, shared by every store."""
    digest = hashlib.sha256()
    for bar in bars:
        normalized = "|".join((bar.instrument_id, bar.interval, bar.event_at.isoformat(), str(bar.revision), bar.provider, bar.source_identifier))
        digest.update(normalized.encode())
    return digest.hexdigest()


class SQLiteBarStore:
    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS bars (
          instrument_id TEXT NOT NULL, interval TEXT NOT NULL, event_at TEXT NOT NULL,
          effective_at TEXT NOT NULL, ingested_at TEXT NOT NULL, open TEXT NOT NULL, high TEXT NOT NULL,
          low TEXT NOT NULL, close TEXT NOT NULL, volume TEXT NOT NULL, provider TEXT NOT NULL,
          source_identifier TEXT NOT NULL, original_timezone TEXT NOT NULL, revision INTEGER NOT NULL,
          data_version TEXT NOT NULL, quality_score TEXT NOT NULL, processing_status TEXT NOT NULL,
          PRIMARY KEY (instrument_id, interval, event_at, revision))""")
        self._connection.commit()

    def ingest(self, bars: list[OHLCVBar]) -> str:
        assessed = assess_bars(bars)
        rejected = [bar for bar in assessed if bar.processing_status is DataProcessingStatus.REJECTED]
        if rejected:
            raise DataQualityError("batch contains rejected data-quality records")
        for bar in assessed:
            self._connection.execute(
                "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (bar.instrument_id, bar.interval, bar.event_at.isoformat(), bar.effective_at.isoformat(),
                 bar.ingested_at.isoformat(), str(bar.open), str(bar.high), str(bar.low), str(bar.close),
                 str(bar.volume), bar.provider, bar.source_identifier, bar.original_timezone, bar.revision,
                 bar.data_version, str(bar.quality_score), bar.processing_status.value),
            )
        self._connection.commit()
        return batch_digest(assessed)

    def available_as_of(self, instrument_id: str, interval: str, decision_at: datetime) -> list[OHLCVBar]:
        """Only return records known and effective at the historical decision timestamp."""
        rows = self._connection.execute(
            "SELECT * FROM bars WHERE instrument_id = ? AND interval = ? "
            "AND effective_at <= ? AND ingested_at <= ? ORDER BY event_at, revision",
            (instrument_id, interval, decision_at.isoformat(), decision_at.isoformat()),
        ).fetchall()
        return [
            OHLCVBar(row[0], row[1], datetime.fromisoformat(row[2]), datetime.fromisoformat(row[3]),
                     datetime.fromisoformat(row[4]), Decimal(row[5]), Decimal(row[6]), Decimal(row[7]),
                     Decimal(row[8]), Decimal(row[9]), row[10], row[11], row[12], row[13], row[14],
                     Decimal(row[15]), DataProcessingStatus(row[16]))
            for row in rows
        ]

    def read_range(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]:
        """All stored revisions in an event-time window, for Data Health and research reads."""
        rows = self._connection.execute(
            "SELECT * FROM bars WHERE instrument_id = ? AND interval = ? "
            "AND event_at >= ? AND event_at <= ? ORDER BY event_at, revision",
            (instrument_id, interval, start.isoformat(), end.isoformat()),
        ).fetchall()
        return [
            OHLCVBar(row[0], row[1], datetime.fromisoformat(row[2]), datetime.fromisoformat(row[3]),
                     datetime.fromisoformat(row[4]), Decimal(row[5]), Decimal(row[6]), Decimal(row[7]),
                     Decimal(row[8]), Decimal(row[9]), row[10], row[11], row[12], row[13], row[14],
                     Decimal(row[15]), DataProcessingStatus(row[16]))
            for row in rows
        ]

    def known_series(self) -> list[tuple[str, str]]:
        rows = self._connection.execute(
            "SELECT DISTINCT instrument_id, interval FROM bars ORDER BY instrument_id, interval"
        ).fetchall()
        return [(row[0], row[1]) for row in rows]
