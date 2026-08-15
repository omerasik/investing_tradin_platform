"""SEC-style point-in-time filing facts and transparent research metrics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from .fundamentals import StatementType
from .persistence import PostgresDatabase


class PitFundamentalError(ValueError):
    pass


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PitFundamentalError(f"{name}_must_be_timezone_aware")


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise PitFundamentalError(f"invalid_{name}")


@dataclass(frozen=True, slots=True)
class AuthorizedFilingSource:
    provider: str
    terms_version: str
    authorization_reference: str
    authorized_at: datetime
    created_at: datetime
    source_id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        for value, name in ((self.provider, "provider"), (self.terms_version, "terms_version"),
                            (self.authorization_reference, "authorization_reference")):
            _text(value, name)
        _aware(self.authorized_at, "authorized_at")
        _aware(self.created_at, "created_at")
        if self.created_at < self.authorized_at:
            raise PitFundamentalError("source_created_before_authorization")


@dataclass(frozen=True, slots=True)
class FundamentalFiling:
    source_id: UUID
    instrument_id: str
    filing_id: str
    form_type: str
    filing_at: datetime
    accepted_at: datetime
    reporting_period_start: date
    reporting_period_end: date
    fiscal_year: int
    fiscal_period: str
    revision: int
    ingested_at: datetime
    provenance_uri: str
    raw_payload_sha256: str
    filing_record_id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        for value, name in ((self.instrument_id, "instrument_id"), (self.filing_id, "filing_id"),
                            (self.form_type, "form_type"), (self.fiscal_period, "fiscal_period"),
                            (self.provenance_uri, "provenance_uri")):
            _text(value, name)
        for timestamp, name in ((self.filing_at, "filing_at"), (self.accepted_at, "accepted_at"),
                                (self.ingested_at, "ingested_at")):
            _aware(timestamp, name)
        if (
            self.reporting_period_end < self.reporting_period_start
            or self.filing_at.date() < self.reporting_period_end
            or self.accepted_at < self.filing_at
            or self.ingested_at < self.accepted_at
            or self.revision < 0
            or self.fiscal_year < 1900
            or len(self.raw_payload_sha256) != 64
        ):
            raise PitFundamentalError("invalid_filing_semantics")


@dataclass(frozen=True, slots=True)
class FilingFact:
    filing_record_id: UUID
    taxonomy_namespace: str
    concept: str
    statement_type: StatementType
    as_reported_value: Decimal
    unit: str
    currency: str | None
    standardized_metric: str | None = None
    standardized_value: Decimal | None = None
    standardization_version: str | None = None
    dimensions: dict[str, object] = field(default_factory=dict)
    fact_id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        for value, name in ((self.taxonomy_namespace, "taxonomy_namespace"),
                            (self.concept, "concept"), (self.unit, "unit")):
            _text(value, name)
        if self.currency is not None and len(self.currency) != 3:
            raise PitFundamentalError("invalid_fact_currency")
        standardized = (self.standardized_metric, self.standardized_value, self.standardization_version)
        if any(value is None for value in standardized) and any(value is not None for value in standardized):
            raise PitFundamentalError("incomplete_fact_standardization")


@dataclass(frozen=True, slots=True)
class AvailableFilingFact:
    filing: FundamentalFiling
    fact: FilingFact


@dataclass(frozen=True, slots=True)
class FundamentalResearchMetrics:
    revenue: Decimal
    operating_margin: Decimal
    free_cash_flow: Decimal
    debt: Decimal
    shares: Decimal
    dilution: Decimal | None
    nopat: Decimal
    invested_capital: Decimal
    roic: Decimal
    capital_allocation: Decimal
    formula_version: str = "transparent-fundamentals-v1"


def derive_research_metrics(
    standardized: dict[str, Decimal], *, prior_shares: Decimal | None = None,
) -> FundamentalResearchMetrics:
    required = {
        "revenue", "operating_income", "operating_cash_flow", "capital_expenditures",
        "total_debt", "shares_outstanding", "tax_rate", "invested_capital",
        "dividends_paid", "share_repurchases",
    }
    missing = sorted(required - standardized.keys())
    if missing:
        raise PitFundamentalError("missing_standardized_metrics:" + ",".join(missing))
    revenue = standardized["revenue"]
    invested = standardized["invested_capital"]
    shares = standardized["shares_outstanding"]
    if revenue == 0 or invested == 0 or shares <= 0 or not Decimal("0") <= standardized["tax_rate"] <= Decimal("1"):
        raise PitFundamentalError("invalid_metric_denominator")
    operating_income = standardized["operating_income"]
    nopat = operating_income * (Decimal("1") - standardized["tax_rate"])
    if prior_shares is not None and prior_shares <= 0:
        raise PitFundamentalError("invalid_prior_shares")
    dilution = None if prior_shares is None else (shares - prior_shares) / prior_shares
    return FundamentalResearchMetrics(
        revenue, operating_income / revenue,
        standardized["operating_cash_flow"] - standardized["capital_expenditures"],
        standardized["total_debt"], shares, dilution, nopat, invested, nopat / invested,
        standardized["dividends_paid"] + standardized["share_repurchases"] + standardized["capital_expenditures"],
    )


class PostgresPitFundamentalStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register_source(self, source: AuthorizedFilingSource) -> None:
        source.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO pit_fundamental_sources VALUES (%s,%s,%s,%s,%s,%s)",
                               (source.source_id, source.provider, source.terms_version,
                                source.authorization_reference, source.authorized_at, source.created_at))
        except Exception as error:
            raise PitFundamentalError("filing_source_registration_failed") from error

    def ingest(self, filing: FundamentalFiling, facts: tuple[FilingFact, ...]) -> None:
        filing.validate()
        if not facts or any(fact.filing_record_id != filing.filing_record_id for fact in facts):
            raise PitFundamentalError("invalid_filing_fact_membership")
        for fact in facts:
            fact.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO pit_fundamental_filings VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (filing.filing_record_id, filing.source_id, filing.instrument_id, filing.filing_id,
                     filing.form_type, filing.filing_at, filing.accepted_at, filing.reporting_period_start,
                     filing.reporting_period_end, filing.fiscal_year, filing.fiscal_period, filing.revision,
                     filing.ingested_at, filing.provenance_uri, filing.raw_payload_sha256),
                )
                for fact in facts:
                    dimensions = json.dumps(fact.dimensions, sort_keys=True, separators=(",", ":"))
                    content = "|".join((str(filing.filing_record_id), fact.taxonomy_namespace, fact.concept,
                                        str(fact.as_reported_value), fact.unit, dimensions))
                    cursor.execute(
                        "INSERT INTO pit_fundamental_facts VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
                        (fact.fact_id, fact.filing_record_id, fact.taxonomy_namespace, fact.concept,
                         fact.statement_type.value, fact.as_reported_value, fact.unit, fact.currency,
                         fact.standardized_metric, fact.standardized_value, fact.standardization_version,
                         dimensions, hashlib.sha256(content.encode()).hexdigest()),
                    )
        except Exception as error:
            raise PitFundamentalError("fundamental_filing_ingestion_failed") from error

    def available_as_of(
        self, instrument_id: str, as_of: datetime, *, standardized_metric: str | None = None,
    ) -> tuple[AvailableFilingFact, ...]:
        _aware(as_of, "fundamental_as_of")
        statement = (
            "WITH ranked AS (SELECT f.*,x.*,ROW_NUMBER() OVER (PARTITION BY f.source_id,f.filing_id,x.taxonomy_namespace,x.concept,x.unit "
            "ORDER BY f.revision DESC,f.accepted_at DESC,f.ingested_at DESC) rank FROM pit_fundamental_filings f "
            "JOIN pit_fundamental_facts x ON x.filing_record_id=f.filing_record_id WHERE f.instrument_id=%s "
            "AND f.accepted_at<=%s AND f.ingested_at<=%s AND (%s IS NULL OR x.standardized_metric=%s)) "
            "SELECT * FROM ranked WHERE rank=1 "
            "ORDER BY reporting_period_end,concept"
        )
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(statement, (instrument_id, as_of, as_of, standardized_metric, standardized_metric))
            rows = cursor.fetchall()
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> AvailableFilingFact:
        filing = FundamentalFiling(
            UUID(str(row[1])), str(row[2]), str(row[3]), str(row[4]), cast(datetime, row[5]),
            cast(datetime, row[6]), cast(date, row[7]), cast(date, row[8]), int(str(row[9])),
            str(row[10]), int(str(row[11])), cast(datetime, row[12]), str(row[13]), str(row[14]),
            UUID(str(row[0])),
        )
        fact = FilingFact(
            UUID(str(row[16])), str(row[17]), str(row[18]), StatementType(str(row[19])),
            Decimal(str(row[20])), str(row[21]), None if row[22] is None else str(row[22]),
            None if row[23] is None else str(row[23]),
            None if row[24] is None else Decimal(str(row[24])),
            None if row[25] is None else str(row[25]), cast(dict[str, object], row[26]), UUID(str(row[15])),
        )
        return AvailableFilingFact(filing, fact)
