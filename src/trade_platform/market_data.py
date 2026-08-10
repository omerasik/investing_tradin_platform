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


def ingest_from_provider(
    provider: HistoricalBarProvider, store: "SQLiteBarStore", instrument_id: str, interval: str, start: datetime, end: datetime
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
        digest = hashlib.sha256()
        for bar in assessed:
            normalized = "|".join((bar.instrument_id, bar.interval, bar.event_at.isoformat(), str(bar.revision), bar.provider, bar.source_identifier))
            digest.update(normalized.encode())
            self._connection.execute(
                "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (bar.instrument_id, bar.interval, bar.event_at.isoformat(), bar.effective_at.isoformat(),
                 bar.ingested_at.isoformat(), str(bar.open), str(bar.high), str(bar.low), str(bar.close),
                 str(bar.volume), bar.provider, bar.source_identifier, bar.original_timezone, bar.revision,
                 bar.data_version, str(bar.quality_score), bar.processing_status.value),
            )
        self._connection.commit()
        return digest.hexdigest()

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
