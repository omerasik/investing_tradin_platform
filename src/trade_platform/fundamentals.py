"""Auditable point-in-time fundamental facts without restatement leakage."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path


class FundamentalDataError(ValueError):
    pass


class StatementType(StrEnum):
    INCOME_STATEMENT = "INCOME_STATEMENT"
    BALANCE_SHEET = "BALANCE_SHEET"
    CASH_FLOW = "CASH_FLOW"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class FundamentalFact:
    instrument_id: str
    statement_type: StatementType
    fact_name: str
    reporting_period_start: datetime
    reporting_period_end: datetime
    filed_at: datetime
    effective_at: datetime
    ingested_at: datetime
    as_reported_value: Decimal
    unit: str
    currency: str
    provider: str
    source_identifier: str
    revision: int = 0
    standardized_value: Decimal | None = None
    standardization_version: str | None = None

    def validate(self) -> None:
        timestamps = (self.reporting_period_start, self.reporting_period_end, self.filed_at, self.effective_at, self.ingested_at)
        if not all((self.instrument_id.strip(), self.fact_name.strip(), self.unit.strip(), self.currency.strip(), self.provider.strip(), self.source_identifier.strip())):
            raise FundamentalDataError("invalid_fundamental_identity")
        if any(item.tzinfo is None for item in timestamps):
            raise FundamentalDataError("fundamental_timestamps_must_be_timezone_aware")
        if self.revision < 0 or self.reporting_period_end < self.reporting_period_start:
            raise FundamentalDataError("invalid_fundamental_period_or_revision")
        if self.filed_at < self.reporting_period_end or self.effective_at < self.filed_at or self.ingested_at < self.filed_at:
            raise FundamentalDataError("invalid_fundamental_timestamp_semantics")
        if (self.standardized_value is None) != (self.standardization_version is None):
            raise FundamentalDataError("standardized_value_requires_version")


class SQLiteFundamentalStore:
    """Append-only facts; an as-of query selects the latest known revision per fact period."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fundamental_facts (
                instrument_id TEXT NOT NULL, statement_type TEXT NOT NULL, fact_name TEXT NOT NULL,
                reporting_period_start TEXT NOT NULL, reporting_period_end TEXT NOT NULL, filed_at TEXT NOT NULL,
                effective_at TEXT NOT NULL, ingested_at TEXT NOT NULL, as_reported_value TEXT NOT NULL,
                unit TEXT NOT NULL, currency TEXT NOT NULL, provider TEXT NOT NULL, source_identifier TEXT NOT NULL,
                revision INTEGER NOT NULL, standardized_value TEXT, standardization_version TEXT,
                PRIMARY KEY (provider, source_identifier, revision)
            )
            """
        )
        self._connection.commit()

    def ingest(self, facts: list[FundamentalFact]) -> None:
        if not facts:
            raise FundamentalDataError("empty_fundamental_batch")
        for fact in facts:
            fact.validate()
            try:
                self._connection.execute(
                    "INSERT INTO fundamental_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        fact.instrument_id, fact.statement_type.value, fact.fact_name, fact.reporting_period_start.isoformat(), fact.reporting_period_end.isoformat(),
                        fact.filed_at.isoformat(), fact.effective_at.isoformat(), fact.ingested_at.isoformat(), str(fact.as_reported_value), fact.unit, fact.currency,
                        fact.provider, fact.source_identifier, fact.revision, self._decimal_or_none(fact.standardized_value), fact.standardization_version,
                    ),
                )
            except sqlite3.IntegrityError as error:
                self._connection.rollback()
                raise FundamentalDataError("duplicate_fundamental_fact_revision") from error
        self._connection.commit()

    def available_as_of(self, instrument_id: str, decision_at: datetime, *, fact_name: str | None = None) -> list[FundamentalFact]:
        if not instrument_id.strip() or decision_at.tzinfo is None:
            raise FundamentalDataError("invalid_fundamental_as_of_query")
        sql = "SELECT * FROM fundamental_facts WHERE instrument_id = ? AND filed_at <= ? AND effective_at <= ? AND ingested_at <= ?"
        params: list[str] = [instrument_id, decision_at.isoformat(), decision_at.isoformat(), decision_at.isoformat()]
        if fact_name is not None:
            sql += " AND fact_name = ?"
            params.append(fact_name)
        sql += " ORDER BY reporting_period_end, filed_at DESC, revision DESC, ingested_at DESC"
        rows = self._connection.execute(sql, params).fetchall()
        latest: dict[tuple[str, str, str, str, str], FundamentalFact] = {}
        for row in rows:
            fact = self._from_row(row)
            key = (fact.statement_type.value, fact.fact_name, fact.reporting_period_start.isoformat(), fact.reporting_period_end.isoformat(), fact.unit)
            latest.setdefault(key, fact)
        return list(latest.values())

    def history(self, instrument_id: str, fact_name: str, reporting_period_end: datetime) -> list[FundamentalFact]:
        if reporting_period_end.tzinfo is None:
            raise FundamentalDataError("fundamental_timestamps_must_be_timezone_aware")
        rows = self._connection.execute(
            "SELECT * FROM fundamental_facts WHERE instrument_id = ? AND fact_name = ? AND reporting_period_end = ? ORDER BY filed_at, revision",
            (instrument_id, fact_name, reporting_period_end.isoformat()),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _decimal_or_none(value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> FundamentalFact:
        return FundamentalFact(
            str(row[0]), StatementType(str(row[1])), str(row[2]), datetime.fromisoformat(str(row[3])), datetime.fromisoformat(str(row[4])),
            datetime.fromisoformat(str(row[5])), datetime.fromisoformat(str(row[6])), datetime.fromisoformat(str(row[7])), Decimal(str(row[8])),
            str(row[9]), str(row[10]), str(row[11]), str(row[12]), int(row[13]),
            None if row[14] is None else Decimal(str(row[14])), None if row[15] is None else str(row[15]),
        )
