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
    #: When this filing's facts become visible to PIT research, per an explicit,
    #: versioned, platform-derived policy (see ``available_point_in_time`` and
    #: ``trade_platform.sec_edgar``'s ``compute_research_available_at``). This is
    #: NEVER an SEC-provided fact -- SEC's own documentation states there is no
    #: timestamp for the exact moment a filing becomes available on sec.gov, and
    #: ``accepted_at`` alone is not a safe proxy for after-hours/delayed filings.
    #: Always >= ``accepted_at``; never earlier.
    research_available_at: datetime
    #: Version string for the policy that produced ``research_available_at``
    #: (e.g. ``"sec-research-availability-v1"``). Required so a future policy
    #: correction is auditable rather than silently reinterpreting old rows.
    availability_policy_version: str
    filing_record_id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        for value, name in ((self.instrument_id, "instrument_id"), (self.filing_id, "filing_id"),
                            (self.form_type, "form_type"), (self.fiscal_period, "fiscal_period"),
                            (self.provenance_uri, "provenance_uri"),
                            (self.availability_policy_version, "availability_policy_version")):
            _text(value, name)
        for timestamp, name in ((self.filing_at, "filing_at"), (self.accepted_at, "accepted_at"),
                                (self.ingested_at, "ingested_at"),
                                (self.research_available_at, "research_available_at")):
            _aware(timestamp, name)
        if (
            self.reporting_period_end < self.reporting_period_start
            or self.filing_at.date() < self.reporting_period_end
            or self.accepted_at < self.filing_at
            or self.ingested_at < self.accepted_at
            or self.research_available_at < self.accepted_at
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
                    "INSERT INTO pit_fundamental_filings VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (filing.filing_record_id, filing.source_id, filing.instrument_id, filing.filing_id,
                     filing.form_type, filing.filing_at, filing.accepted_at, filing.reporting_period_start,
                     filing.reporting_period_end, filing.fiscal_year, filing.fiscal_period, filing.revision,
                     filing.ingested_at, filing.provenance_uri, filing.raw_payload_sha256,
                     filing.research_available_at, filing.availability_policy_version),
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

    def find_by_accession(self, source_id: UUID, filing_id: str) -> FundamentalFiling | None:
        """The already-ingested filing for this exact accession, at any revision, if any.

        Used by provider ingestion adapters (see ``trade_platform.sec_edgar``) to make
        re-running an ingestion batch idempotent rather than merely fail-safe: an
        accession already ingested is recognized and skipped instead of colliding with
        the ``UNIQUE(source_id, filing_id, revision)`` constraint.
        """
        _text(filing_id, "filing_id")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT filing_record_id FROM pit_fundamental_filings "
                "WHERE source_id=%s AND filing_id=%s ORDER BY revision DESC LIMIT 1",
                (source_id, filing_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._filing_by_record_id(UUID(str(row[0])))

    def _filing_by_record_id(self, filing_record_id: UUID) -> FundamentalFiling:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT filing_record_id,source_id,instrument_id,filing_id,form_type,filing_at,"
                "accepted_at,reporting_period_start,reporting_period_end,fiscal_year,fiscal_period,"
                "revision,ingested_at,provenance_uri,raw_payload_sha256,research_available_at,"
                "availability_policy_version FROM pit_fundamental_filings WHERE filing_record_id=%s",
                (filing_record_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise PitFundamentalError(f"filing_not_found:{filing_record_id}")
        return FundamentalFiling(
            source_id=UUID(str(row[1])), instrument_id=str(row[2]), filing_id=str(row[3]),
            form_type=str(row[4]), filing_at=cast(datetime, row[5]), accepted_at=cast(datetime, row[6]),
            reporting_period_start=cast(date, row[7]), reporting_period_end=cast(date, row[8]),
            fiscal_year=int(str(row[9])), fiscal_period=str(row[10]), revision=int(str(row[11])),
            ingested_at=cast(datetime, row[12]), provenance_uri=str(row[13]), raw_payload_sha256=str(row[14]),
            research_available_at=cast(datetime, row[15]), availability_policy_version=str(row[16]),
            filing_record_id=UUID(str(row[0])),
        )

    def latest_revision_for_reporting_period(
        self, source_id: UUID, instrument_id: str, form_type: str, reporting_period_end: date,
    ) -> int | None:
        """The highest revision already stored for this (source, instrument, form family
        ignoring an amendment's ``/A`` suffix, reporting period).

        Used by provider ingestion adapters to assign the *next* revision number to an
        amendment (10-K/A, 10-Q/A) without overwriting the original filing -- both remain
        stored as distinct rows; PIT visibility between them is governed entirely by each
        row's own ``research_available_at``/``ingested_at``, never by deleting or mutating
        the earlier one (this table is DB-trigger immutable; there is no update path).
        This is a platform-derived lineage-matching *policy* (same-registrant, same form
        family, same reporting period), not an SEC-asserted amendment link -- callers
        should version and label it accordingly (e.g. ``sec-amendment-lineage-v1``).
        """
        _text(instrument_id, "instrument_id")
        _text(form_type, "form_type")
        form_base = form_type.removesuffix("/A")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT MAX(revision) FROM pit_fundamental_filings WHERE source_id=%s AND instrument_id=%s "
                "AND reporting_period_end=%s AND (form_type=%s OR form_type=%s)",
                (source_id, instrument_id, reporting_period_end, form_base, form_base + "/A"),
            )
            row = cursor.fetchone()
        return None if row is None or row[0] is None else int(row[0])

    #: Explicit, stable column list for both PIT queries below -- deliberately
    #: not ``SELECT f.*,x.*`` so adding a filing column (as this module just
    #: did for the two-clock correction) can never silently shift the fact
    #: columns' positional offsets that ``_from_row`` depends on.
    _FILING_COLUMNS = (
        "f.filing_record_id", "f.source_id", "f.instrument_id", "f.filing_id", "f.form_type",
        "f.filing_at", "f.accepted_at", "f.reporting_period_start", "f.reporting_period_end",
        "f.fiscal_year", "f.fiscal_period", "f.revision", "f.ingested_at", "f.provenance_uri",
        "f.raw_payload_sha256", "f.research_available_at", "f.availability_policy_version",
    )
    _FACT_COLUMNS = (
        "x.fact_id", "x.taxonomy_namespace", "x.concept", "x.statement_type", "x.as_reported_value",
        "x.unit", "x.currency", "x.standardized_metric", "x.standardized_value",
        "x.standardization_version", "x.dimensions",
    )

    @staticmethod
    def _bare(column: str) -> str:
        """Strip the ``f.``/``x.`` join-disambiguation prefix.

        ``ranked`` (the CTE below) has no ``f``/``x`` aliases of its own -- its output
        columns are named after the inner SELECT's targets, unprefixed. The outer
        SELECT reads from ``ranked``, so it must reference the bare names; only the
        inner SELECT (against the actual joined tables) needs the prefixed form.
        """
        return column.split(".", 1)[1]

    def available_as_of(
        self, instrument_id: str, as_of: datetime, *, standardized_metric: str | None = None,
    ) -> tuple[AvailableFilingFact, ...]:
        """Single-clock current/live-state view: one timestamp gates both real-world
        validity and system knowledge. Kept unchanged for existing production callers
        (feature_platform.py, investment_engine_v2.py, investments.py) -- use
        ``available_point_in_time`` instead for an honest historical backfill.
        """
        _aware(as_of, "fundamental_as_of")
        inner_columns = ",".join(self._FILING_COLUMNS + self._FACT_COLUMNS)
        outer_columns = ",".join(self._bare(c) for c in self._FILING_COLUMNS + self._FACT_COLUMNS)
        statement = (
            f"WITH ranked AS (SELECT {inner_columns},ROW_NUMBER() OVER "  # nosec B608 - fixed internal column allow-list
            "(PARTITION BY f.source_id,f.filing_id,x.taxonomy_namespace,x.concept,x.unit "
            "ORDER BY f.revision DESC,f.accepted_at DESC,f.ingested_at DESC) rank FROM pit_fundamental_filings f "
            "JOIN pit_fundamental_facts x ON x.filing_record_id=f.filing_record_id WHERE f.instrument_id=%s "
            "AND f.accepted_at<=%s AND f.ingested_at<=%s "
            "AND (%s::text IS NULL OR x.standardized_metric=%s::text)) "
            f"SELECT {outer_columns} FROM ranked WHERE rank=1 "
            "ORDER BY reporting_period_end,concept"
        )
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(statement, (instrument_id, as_of, as_of, standardized_metric, standardized_metric))
            rows = cursor.fetchall()
        return tuple(self._from_row(row) for row in rows)

    def available_point_in_time(
        self,
        instrument_id: str,
        effective_at: datetime,
        known_at: datetime,
        *,
        standardized_metric: str | None = None,
    ) -> tuple[AvailableFilingFact, ...]:
        """Two-clock historical-backfill-safe view.

        ``effective_at`` gates real-world/research availability
        (``research_available_at`` -- itself already >= the real SEC
        ``accepted_at``, per an explicit versioned policy, never earlier).
        ``known_at`` gates actual platform ingestion (``ingested_at``,
        the honest timestamp this system actually learned the filing --
        never backdated to the historical fact date).

        A filing accepted publicly in 2024 but first ingested by this platform
        in 2026 is correctly invisible for any ``known_at`` before the real
        2026 ingestion, even when ``effective_at`` is set to 2024 -- that is
        the whole point of separating the two clocks, not a bug.
        """
        _aware(effective_at, "fundamental_effective_at")
        _aware(known_at, "fundamental_known_at")
        if known_at < effective_at:
            raise PitFundamentalError("fundamental_known_before_effective")
        inner_columns = ",".join(self._FILING_COLUMNS + self._FACT_COLUMNS)
        outer_columns = ",".join(self._bare(c) for c in self._FILING_COLUMNS + self._FACT_COLUMNS)
        statement = (
            f"WITH ranked AS (SELECT {inner_columns},ROW_NUMBER() OVER "  # nosec B608 - fixed internal column allow-list
            # Partitioned by (source, instrument, form family ignoring an amendment's
            # "/A" suffix, reporting period, concept, unit) rather than by filing_id --
            # an amendment is a DIFFERENT accession number from its original, so
            # available_as_of()'s same-filing_id revision-supersession above cannot
            # rank across them. This is the amendment-lineage-v1 policy match itself
            # (see trade_platform.sec_edgar), applied here as the visibility ranking.
            "(PARTITION BY f.source_id,f.instrument_id,regexp_replace(f.form_type,'/A$',''),"
            "f.reporting_period_end,x.taxonomy_namespace,x.concept,x.unit "
            "ORDER BY f.revision DESC,f.research_available_at DESC,f.ingested_at DESC) rank "
            "FROM pit_fundamental_filings f "
            "JOIN pit_fundamental_facts x ON x.filing_record_id=f.filing_record_id WHERE f.instrument_id=%s "
            "AND f.research_available_at<=%s AND f.ingested_at<=%s "
            "AND (%s::text IS NULL OR x.standardized_metric=%s::text)) "
            f"SELECT {outer_columns} FROM ranked WHERE rank=1 "
            "ORDER BY reporting_period_end,concept"
        )
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(statement, (instrument_id, effective_at, known_at, standardized_metric, standardized_metric))
            rows = cursor.fetchall()
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> AvailableFilingFact:
        filing = FundamentalFiling(
            source_id=UUID(str(row[1])), instrument_id=str(row[2]), filing_id=str(row[3]),
            form_type=str(row[4]), filing_at=cast(datetime, row[5]), accepted_at=cast(datetime, row[6]),
            reporting_period_start=cast(date, row[7]), reporting_period_end=cast(date, row[8]),
            fiscal_year=int(str(row[9])), fiscal_period=str(row[10]), revision=int(str(row[11])),
            ingested_at=cast(datetime, row[12]), provenance_uri=str(row[13]), raw_payload_sha256=str(row[14]),
            research_available_at=cast(datetime, row[15]), availability_policy_version=str(row[16]),
            filing_record_id=UUID(str(row[0])),
        )
        fact = FilingFact(
            # x.filing_record_id is never selected separately -- the JOIN condition
            # (x.filing_record_id = f.filing_record_id) already guarantees it equals
            # row[0], the filing's own id.
            filing_record_id=UUID(str(row[0])), taxonomy_namespace=str(row[18]), concept=str(row[19]),
            statement_type=StatementType(str(row[20])), as_reported_value=Decimal(str(row[21])),
            unit=str(row[22]), currency=None if row[23] is None else str(row[23]),
            standardized_metric=None if row[24] is None else str(row[24]),
            standardized_value=None if row[25] is None else Decimal(str(row[25])),
            standardization_version=None if row[26] is None else str(row[26]),
            dimensions=cast(dict[str, object], row[27]), fact_id=UUID(str(row[17])),
        )
        return AvailableFilingFact(filing, fact)
