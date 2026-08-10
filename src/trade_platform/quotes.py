"""Durable, provider-attributed executable quote observations for paper risk checks."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from .domain import MarketSnapshot


class QuoteDataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class QuoteObservation:
    quote_id: UUID
    instrument_id: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    quality_score: Decimal
    provider: str
    source_reference: str
    observed_at: datetime
    ingested_at: datetime

    def validate(self) -> None:
        if not self.instrument_id.strip() or not self.provider.strip() or not self.source_reference.strip() or min(self.bid, self.ask, self.last) <= 0 or self.ask < self.bid or not Decimal("0") <= self.quality_score <= Decimal("1") or self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None or self.ingested_at.tzinfo is None or self.ingested_at.utcoffset() is None or self.ingested_at < self.observed_at:
            raise QuoteDataError("invalid_quote_observation")

    def market_snapshot(self) -> MarketSnapshot:
        return MarketSnapshot(self.instrument_id, self.observed_at, self.bid, self.ask, self.last, self.quality_score)


class SQLiteQuoteStore:
    """Append-only quotes; retrieval excludes records not yet ingested at decision time."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS quote_observations (
            quote_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL, bid TEXT NOT NULL, ask TEXT NOT NULL,
            last TEXT NOT NULL, quality_score TEXT NOT NULL, provider TEXT NOT NULL, source_reference TEXT NOT NULL,
            observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
        self._connection.commit()

    def append(self, quote: QuoteObservation) -> None:
        quote.validate()
        try:
            self._connection.execute("INSERT INTO quote_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(quote.quote_id), quote.instrument_id, str(quote.bid), str(quote.ask), str(quote.last), str(quote.quality_score), quote.provider, quote.source_reference, quote.observed_at.isoformat(), quote.ingested_at.isoformat()))
        except sqlite3.IntegrityError as error:
            raise QuoteDataError("duplicate_quote_observation") from error
        self._connection.commit()

    def latest_as_of(self, instrument_id: str, decision_at: datetime) -> QuoteObservation:
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise QuoteDataError("quote_decision_time_must_be_timezone_aware")
        row = self._connection.execute("SELECT * FROM quote_observations WHERE instrument_id = ? AND observed_at <= ? AND ingested_at <= ? ORDER BY observed_at DESC, ingested_at DESC, rowid DESC LIMIT 1", (instrument_id, decision_at.isoformat(), decision_at.isoformat())).fetchone()
        if row is None:
            raise QuoteDataError("quote_unavailable_as_of")
        return QuoteObservation(UUID(row[0]), row[1], Decimal(row[2]), Decimal(row[3]), Decimal(row[4]), Decimal(row[5]), row[6], row[7], datetime.fromisoformat(row[8]), datetime.fromisoformat(row[9]))

    def close(self) -> None:
        self._connection.close()
