"""Lawful, provider-neutral event metadata with explicit source confidence and uncertainty."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from .domain import utc_now


class MarketIntelligenceValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SourceProfile:
    source_id: str
    provider: str
    license_approved: bool
    reliability: Decimal

    def __post_init__(self) -> None:
        if not self.source_id or not self.provider or not Decimal("0") <= self.reliability <= Decimal("1"):
            raise MarketIntelligenceValidationError("invalid_source_profile")


@dataclass(frozen=True, slots=True)
class NewsEvent:
    event_id: UUID
    source_id: str
    source_item_id: str
    entity_id: str
    headline: str
    source_url: str
    published_at: datetime
    ingested_at: datetime
    topics: tuple[str, ...]
    sentiment: Decimal | None
    extraction_confidence: Decimal
    data_version: str

    def __post_init__(self) -> None:
        if (not self.source_id or not self.source_item_id or not self.entity_id or not self.headline or not self.source_url
                or not self.data_version or self.published_at > self.ingested_at
                or not Decimal("0") <= self.extraction_confidence <= Decimal("1")
                or self.sentiment is not None and not Decimal("-1") <= self.sentiment <= Decimal("1")):
            raise MarketIntelligenceValidationError("invalid_news_event")


@dataclass(frozen=True, slots=True)
class EventAssessment:
    event_id: UUID
    source_reliability: Decimal
    extraction_confidence: Decimal
    confidence: Decimal
    uncertainty: Decimal
    eligible_for_research: bool
    reason: str | None


def assess_event(event: NewsEvent, source: SourceProfile) -> EventAssessment:
    if event.source_id != source.source_id:
        raise MarketIntelligenceValidationError("source_profile_mismatch")
    confidence = source.reliability * event.extraction_confidence
    eligible = source.license_approved and event.sentiment is not None
    reason = None if eligible else "license_not_approved" if not source.license_approved else "sentiment_unavailable"
    return EventAssessment(event.event_id, source.reliability, event.extraction_confidence, confidence, Decimal("1") - confidence, eligible, reason)


class SQLiteNewsEventStore:
    """Append-only metadata store; no content acquisition or provider code belongs here."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS news_events (
            event_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_item_id TEXT NOT NULL,
            entity_id TEXT NOT NULL, headline TEXT NOT NULL, source_url TEXT NOT NULL,
            published_at TEXT NOT NULL, ingested_at TEXT NOT NULL, topics TEXT NOT NULL,
            sentiment TEXT, extraction_confidence TEXT NOT NULL, data_version TEXT NOT NULL,
            UNIQUE(source_id, source_item_id))""")
        self._connection.commit()

    def append(self, event: NewsEvent) -> None:
        try:
            self._connection.execute("INSERT INTO news_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                str(event.event_id), event.source_id, event.source_item_id, event.entity_id, event.headline,
                event.source_url, event.published_at.isoformat(), event.ingested_at.isoformat(), "\u001f".join(event.topics),
                None if event.sentiment is None else str(event.sentiment), str(event.extraction_confidence), event.data_version))
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise MarketIntelligenceValidationError("duplicate_source_event") from error

    def for_entity_as_of(self, entity_id: str, decision_at: datetime) -> list[NewsEvent]:
        rows = self._connection.execute("SELECT * FROM news_events WHERE entity_id = ? AND published_at <= ? AND ingested_at <= ? ORDER BY published_at", (entity_id, decision_at.isoformat(), decision_at.isoformat())).fetchall()
        return [NewsEvent(UUID(row[0]), row[1], row[2], row[3], row[4], row[5], datetime.fromisoformat(row[6]), datetime.fromisoformat(row[7]), tuple(filter(None, row[8].split("\u001f"))), None if row[9] is None else Decimal(row[9]), Decimal(row[10]), row[11]) for row in rows]


def fixture_event(source_id: str, source_item_id: str, entity_id: str, headline: str, published_at: datetime, sentiment: Decimal | None = None) -> NewsEvent:
    """Constructs only a local fixture shape; it never fetches provider content."""
    return NewsEvent(uuid4(), source_id, source_item_id, entity_id, headline, "https://example.invalid/event", published_at, utc_now(), ("fixture",), sentiment, Decimal("0.8"), "fixture-v1")
