"""SEC EDGAR PIT fundamentals/filings authority. Module 3G.1f.2.

SEC EDGAR (data.sec.gov) is a free, zero-cost, no-API-key official U.S.
government source. This module registers it through the existing
``AuthorizedFilingSource``/``PostgresPitFundamentalStore`` authority in
``trade_platform.pit_fundamentals`` -- it never introduces a parallel
fundamentals store.

Scope is deliberately bounded to what Module 3G.1f.2 authorizes: company
identity + filing headers via the submissions API, structured facts via the
XBRL company-facts API, for 10-K/10-Q filings and their amendments. 8-K
filing headers are parsed (``select_supported_filings`` includes "8-K"), but
this module's fact-ingestion path only accepts 10-K/10-Q/amendments --
8-K therefore remains header/metadata-only, never claimed as fully ingested.
Form 3/4/5 insider ownership and 13F institutional holdings are explicitly
out of scope (reserved for a future Module 3G.1f.2b) and this module parses
neither.

Two platform-derived policies are used, each versioned and never presented
as an SEC-provided fact:

- ``RESEARCH_AVAILABILITY_POLICY_VERSION`` (``compute_research_available_at``):
  SEC's own documentation states there is no timestamp for the exact moment a
  filing becomes available on sec.gov, and that a submission accepted at/after
  5:30pm ET (or on a non-business day) is not disseminated until the next
  business day. This computes a conservative ``research_available_at`` that is
  never earlier than the real ``accepted_at``, using the existing professional
  market-calendar authority (``PostgresProfessionalInstrumentMaster.is_open``)
  only to determine which calendar days are real trading days -- the 5:30pm ET
  cutoff and the 9:30am ET reopening anchor are fixed, documented constants of
  the policy itself, not calendar facts.
- ``AMENDMENT_LINEAGE_POLICY_VERSION`` (``PostgresPitFundamentalStore
  .latest_revision_for_reporting_period``): amendment-vs-original matching by
  (source, instrument, form family ignoring "/A", reporting period) is a
  platform-derived lineage-matching policy, not an SEC-asserted link between
  accession numbers.

Provenance limitation, disclosed rather than hidden: this module records a
SHA-256 of each HTTP response body plus its source URI as evidence, but does
not archive the raw response bytes anywhere (no raw-payload blob store exists
in this codebase yet). This is hash-and-URI provenance, not full immutable
source-content capture -- if data.sec.gov's historical response for a given
URL ever became unavailable, the hash alone cannot reconstruct it. Persisting
raw payloads durably would be a separate, explicitly-authorized module.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as time_of_day
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID
from zoneinfo import ZoneInfo

from .audit import AuditStore
from .fundamentals import StatementType
from .pit_fundamentals import (
    FilingFact,
    FundamentalFiling,
    PostgresPitFundamentalStore,
)
from .professional_instruments import (
    IdentifierMapping,
    IdentifierSourceKind,
    PostgresProfessionalInstrumentMaster,
)

SEC_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik10}.json"
SEC_COMPANYFACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"

SEC_CIK_NAMESPACE = "SEC:CIK"

#: Forms whose *headers* this module parses from the submissions API.
SUPPORTED_HEADER_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K"})
#: Forms whose *facts* this module will ingest into pit_fundamental_filings/facts.
#: 8-K is intentionally excluded here -- header-only, per this module's disclosed scope.
SUPPORTED_FACT_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A"})

RESEARCH_AVAILABILITY_POLICY_VERSION = "sec-research-availability-v1"
AMENDMENT_LINEAGE_POLICY_VERSION = "sec-amendment-lineage-v1"
CONCEPT_STANDARDIZATION_VERSION = "sec-us-gaap-standardization-v1"

_EASTERN = ZoneInfo("America/New_York")
#: SEC's own documented cutoff: submissions accepted at/after this local ET time
#: (or on a non-business day) are not disseminated until the next business day.
_SEC_AFTER_HOURS_CUTOFF_ET = time_of_day(17, 30)
#: Reg NMS core session open, America/New_York -- the conservative "next eligible
#: market session" anchor used when a filing must wait for the next business day.
_REGULAR_SESSION_OPEN_ET = time_of_day(9, 30)
_MAX_BUSINESS_DAY_SCAN_DAYS = 10

#: Minimal, explicit, versioned concept->(statement_type, standardized_metric) map.
#: Concepts NOT listed here are still captured faithfully (raw value, unit, dimensions)
#: with statement_type=OTHER and no standardized_metric -- never guessed.
_CONCEPT_STANDARDIZATION: dict[tuple[str, str], tuple[StatementType, str]] = {
    ("us-gaap", "Revenues"): (StatementType.INCOME_STATEMENT, "revenue"),
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"): (
        StatementType.INCOME_STATEMENT, "revenue",
    ),
    ("us-gaap", "OperatingIncomeLoss"): (StatementType.INCOME_STATEMENT, "operating_income"),
    ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"): (
        StatementType.CASH_FLOW, "operating_cash_flow",
    ),
    ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"): (
        StatementType.CASH_FLOW, "capital_expenditures",
    ),
    ("us-gaap", "PaymentsOfDividends"): (StatementType.CASH_FLOW, "dividends_paid"),
    ("us-gaap", "PaymentsForRepurchaseOfCommonStock"): (StatementType.CASH_FLOW, "share_repurchases"),
}


class SecEdgarError(ValueError):
    """Base error for the SEC EDGAR PIT fundamentals adapter."""


class SecUserAgentNotConfiguredError(SecEdgarError):
    """Raised when no SEC_USER_AGENT is configured before a real request."""


class SecRequestError(SecEdgarError):
    """Raised for a transport-level failure calling data.sec.gov."""


class SecRateLimitError(SecEdgarError):
    """Raised when data.sec.gov signals blocking/rate-limiting and retries are exhausted."""


class SecResponseShapeError(SecEdgarError):
    """Raised when a response does not match the documented submissions/companyfacts shape."""


class SecUnsupportedFormError(SecEdgarError):
    """Raised when asked to ingest facts for a form outside SUPPORTED_FACT_FORMS."""


class SecAccessionMismatchError(SecEdgarError):
    """Raised when supplied facts do not all belong to the filing header's own accession."""


def _validate_cik10(cik10: str) -> None:
    if len(cik10) != 10 or not cik10.isdigit():
        raise SecEdgarError(f"invalid_cik10:{cik10}")


@dataclass(frozen=True, slots=True)
class SecFilingHeader:
    accession_number: str
    form: str
    filing_date: date
    report_date: date | None
    acceptance_date_time: datetime
    file_number: str | None
    items: str | None
    size: int | None
    is_xbrl: bool
    is_inline_xbrl: bool
    primary_document: str | None
    primary_doc_description: str | None


@dataclass(frozen=True, slots=True)
class SecSubmissions:
    cik10: str
    entity_name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    filings: tuple[SecFilingHeader, ...]


@dataclass(frozen=True, slots=True)
class SecXbrlFact:
    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    start: date | None
    end: date
    accession_number: str
    fiscal_year: int
    fiscal_period: str
    form: str
    filed: date
    frame: str | None = None


def select_supported_filings(filings: tuple[SecFilingHeader, ...]) -> tuple[SecFilingHeader, ...]:
    return tuple(header for header in filings if header.form in SUPPORTED_HEADER_FORMS)


def parse_submissions_response(payload: dict[str, Any]) -> SecSubmissions:
    try:
        cik10 = str(payload["cik"]).zfill(10)
        entity_name = str(payload["name"])
        tickers = tuple(str(value) for value in payload.get("tickers", ()))
        exchanges = tuple(str(value) for value in payload.get("exchanges", ()))
        recent = payload["filings"]["recent"]
        accession_numbers = recent["accessionNumber"]
        forms = recent["form"]
        filing_dates = recent["filingDate"]
        report_dates = recent["reportDate"]
        acceptance_date_times = recent["acceptanceDateTime"]
        file_numbers = recent.get("fileNumber", [None] * len(accession_numbers))
        items = recent.get("items", [None] * len(accession_numbers))
        sizes = recent.get("size", [None] * len(accession_numbers))
        is_xbrl_flags = recent.get("isXBRL", [0] * len(accession_numbers))
        is_inline_xbrl_flags = recent.get("isInlineXBRL", [0] * len(accession_numbers))
        primary_documents = recent.get("primaryDocument", [None] * len(accession_numbers))
        primary_doc_descriptions = recent.get("primaryDocDescription", [None] * len(accession_numbers))
    except (KeyError, TypeError) as error:
        raise SecResponseShapeError("sec_submissions_response_shape_unexpected") from error
    count = len(accession_numbers)
    parallel_lengths = {
        len(forms), len(filing_dates), len(report_dates), len(acceptance_date_times),
        len(file_numbers), len(items), len(sizes), len(is_xbrl_flags), len(is_inline_xbrl_flags),
        len(primary_documents), len(primary_doc_descriptions),
    }
    if parallel_lengths != {count}:
        raise SecResponseShapeError("sec_submissions_parallel_arrays_length_mismatch")
    headers = []
    for index in range(count):
        report_date_raw = report_dates[index]
        headers.append(
            SecFilingHeader(
                accession_number=str(accession_numbers[index]),
                form=str(forms[index]),
                filing_date=date.fromisoformat(str(filing_dates[index])),
                report_date=None if not report_date_raw else date.fromisoformat(str(report_date_raw)),
                acceptance_date_time=_parse_sec_datetime(str(acceptance_date_times[index])),
                file_number=None if file_numbers[index] is None else str(file_numbers[index]),
                items=None if items[index] is None else str(items[index]),
                size=None if sizes[index] is None else int(sizes[index]),
                is_xbrl=bool(is_xbrl_flags[index]),
                is_inline_xbrl=bool(is_inline_xbrl_flags[index]),
                primary_document=None if primary_documents[index] is None else str(primary_documents[index]),
                primary_doc_description=(
                    None if primary_doc_descriptions[index] is None else str(primary_doc_descriptions[index])
                ),
            )
        )
    return SecSubmissions(cik10, entity_name, tickers, exchanges, tuple(headers))


def _parse_sec_datetime(value: str) -> datetime:
    """SEC's acceptanceDateTime is documented as UTC ("Z"-suffixed or offset-naive-UTC)."""
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise SecResponseShapeError(f"sec_acceptance_date_time_unparseable:{value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_EASTERN)
    return parsed


def parse_company_facts_response(payload: dict[str, Any]) -> tuple[SecXbrlFact, ...]:
    try:
        taxonomies = payload["facts"]
    except (KeyError, TypeError) as error:
        raise SecResponseShapeError("sec_companyfacts_response_shape_unexpected") from error
    facts: list[SecXbrlFact] = []
    for taxonomy, concepts in taxonomies.items():
        if not isinstance(concepts, dict):
            raise SecResponseShapeError("sec_companyfacts_taxonomy_shape_unexpected")
        for concept, concept_body in concepts.items():
            units = concept_body.get("units", {}) if isinstance(concept_body, dict) else {}
            for unit, entries in units.items():
                for entry in entries:
                    try:
                        facts.append(
                            SecXbrlFact(
                                taxonomy=str(taxonomy),
                                concept=str(concept),
                                unit=str(unit),
                                value=Decimal(str(entry["val"])),
                                start=date.fromisoformat(entry["start"]) if entry.get("start") else None,
                                end=date.fromisoformat(entry["end"]),
                                accession_number=str(entry["accn"]),
                                fiscal_year=int(entry["fy"]),
                                fiscal_period=str(entry["fp"]),
                                form=str(entry["form"]),
                                filed=date.fromisoformat(entry["filed"]),
                                frame=entry.get("frame"),
                            )
                        )
                    except (KeyError, TypeError, ValueError) as error:
                        raise SecResponseShapeError(
                            f"sec_companyfacts_entry_shape_unexpected:{taxonomy}:{concept}"
                        ) from error
    return tuple(facts)


class SecEdgarClient:
    """Fair-access, rate-limited HTTP client for data.sec.gov. No account, no API key.

    Never exceeds an internal safety ceiling stricter than SEC's documented 10
    requests/second (see ``min_request_interval_seconds``). 429/403/5xx responses
    fail closed with bounded exponential backoff, then raise rather than retry
    forever.
    """

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        min_request_interval_seconds: float = 0.25,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        resolved = user_agent if user_agent is not None else os.environ.get("SEC_USER_AGENT")
        if resolved is None or not resolved.strip():
            raise SecUserAgentNotConfiguredError("SEC_USER_AGENT_NOT_CONFIGURED")
        if min_request_interval_seconds < 0.1:
            raise SecEdgarError("sec_min_request_interval_below_safety_ceiling")
        self._user_agent = resolved.strip()
        self._min_request_interval_seconds = min_request_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._last_request_monotonic: float | None = None

    def _throttle(self) -> None:
        if self._last_request_monotonic is not None:
            elapsed = time.monotonic() - self._last_request_monotonic
            remaining = self._min_request_interval_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_monotonic = time.monotonic()

    def _get_json(self, url: str) -> tuple[dict[str, Any], str]:
        attempt = 0
        while True:
            self._throttle()
            request = Request(
                url, headers={"User-Agent": self._user_agent, "Accept": "application/json"}
            )
            try:
                # url is always built from the fixed data.sec.gov templates above with
                # only a pre-validated 10-digit CIK interpolated -- never caller-supplied.
                with urlopen(request, timeout=self._timeout_seconds) as response:  # nosec B310
                    raw_body = response.read()
                break
            except HTTPError as error:
                if error.code in (403, 429, 500, 502, 503, 504) and attempt < self._max_retries:
                    attempt += 1
                    time.sleep(min(2**attempt, 8))
                    continue
                raise SecRateLimitError(f"sec_edgar_request_failed:{error.code}") from error
            except URLError as error:
                raise SecRequestError("sec_edgar_request_failed") from error
        response_hash = hashlib.sha256(raw_body).hexdigest()
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as error:
            raise SecResponseShapeError("sec_edgar_response_undecodable") from error
        if not isinstance(payload, dict):
            raise SecResponseShapeError("sec_edgar_response_not_an_object")
        return payload, response_hash

    def get_submissions(self, cik10: str) -> tuple[SecSubmissions, str]:
        _validate_cik10(cik10)
        payload, response_hash = self._get_json(SEC_SUBMISSIONS_URL_TEMPLATE.format(cik10=cik10))
        return parse_submissions_response(payload), response_hash

    def get_company_facts(self, cik10: str) -> tuple[tuple[SecXbrlFact, ...], str]:
        _validate_cik10(cik10)
        payload, response_hash = self._get_json(SEC_COMPANYFACTS_URL_TEMPLATE.format(cik10=cik10))
        return parse_company_facts_response(payload), response_hash


def register_sec_cik_mapping(
    master: PostgresProfessionalInstrumentMaster,
    *,
    instrument_id: str,
    cik10: str,
    captured_at: datetime,
    source_reference: str,
) -> IdentifierMapping:
    """Map a CIK to an *explicitly caller-chosen* instrument -- never inferred.

    A CIK identifies the SEC registrant/entity, not automatically every
    separately-traded security/share class it may issue; the caller (an
    onboarding script or operator action, not this function) is responsible
    for confirming that ``instrument_id`` is the correct security for
    ``cik10`` before calling this. Like OpenFIGI's FIGI, a CIK is permanent
    once assigned by SEC -- ``valid_until=None``, current-identity only, never
    a historical-lineage claim.
    """
    _validate_cik10(cik10)
    mapping = IdentifierMapping(
        instrument_id=instrument_id,
        source_kind=IdentifierSourceKind.STANDARD,
        namespace=SEC_CIK_NAMESPACE,
        value=cik10,
        valid_from=captured_at,
        valid_until=None,
        ingested_at=captured_at,
        source_reference=source_reference,
    )
    master.add_identifier_mapping(mapping)
    return mapping


def _is_business_day(
    calendar_master: PostgresProfessionalInstrumentMaster, venue: str, day: date, known_at: datetime,
) -> bool:
    noon = datetime.combine(day, time_of_day(12, 0), _EASTERN)
    return calendar_master.is_open(venue, noon, known_at=known_at)


def compute_research_available_at(
    accepted_at: datetime,
    *,
    venue: str,
    calendar_master: PostgresProfessionalInstrumentMaster,
    known_at: datetime,
) -> datetime:
    """Conservative, versioned, platform-derived research-availability estimate.

    Never earlier than ``accepted_at``. Not an SEC-provided fact: SEC's own
    documentation states there is no timestamp for the exact moment a filing
    becomes available on sec.gov. If the filing was accepted on a business day
    at/before 5:30pm ET, it is treated as available at ``accepted_at`` itself;
    otherwise it is pushed to the next eligible market session's open (using
    the real calendar to skip weekends/holidays, but a fixed 9:30am ET as the
    reopening anchor -- a policy constant, not a calendar-sourced fact).
    """
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise SecEdgarError("accepted_at_must_be_timezone_aware")
    local = accepted_at.astimezone(_EASTERN)
    if local.timetz().replace(tzinfo=None) <= _SEC_AFTER_HOURS_CUTOFF_ET and _is_business_day(
        calendar_master, venue, local.date(), known_at
    ):
        return accepted_at
    candidate = local.date() + timedelta(days=1)
    for _ in range(_MAX_BUSINESS_DAY_SCAN_DAYS):
        if _is_business_day(calendar_master, venue, candidate, known_at):
            return datetime.combine(candidate, _REGULAR_SESSION_OPEN_ET, _EASTERN)
        candidate += timedelta(days=1)
    raise SecEdgarError("research_availability_business_day_not_found")


def standardize_xbrl_fact(fact: SecXbrlFact) -> tuple[StatementType, str | None, Decimal | None, str | None]:
    """Best-effort, explicit, versioned standardization for a small known set of
    us-gaap concepts. Unmapped concepts are NOT guessed -- they return
    (OTHER, None, None, None), preserved as raw facts only.
    """
    mapped = _CONCEPT_STANDARDIZATION.get((fact.taxonomy, fact.concept))
    if mapped is None:
        return StatementType.OTHER, None, None, None
    statement_type, standardized_metric = mapped
    return statement_type, standardized_metric, fact.value, CONCEPT_STANDARDIZATION_VERSION


def build_filing_fact(fact: SecXbrlFact, filing_record_id: UUID) -> FilingFact:
    statement_type, standardized_metric, standardized_value, standardization_version = (
        standardize_xbrl_fact(fact)
    )
    return FilingFact(
        filing_record_id=filing_record_id,
        taxonomy_namespace=fact.taxonomy,
        concept=fact.concept,
        statement_type=statement_type,
        as_reported_value=fact.value,
        unit=fact.unit,
        currency=fact.unit if fact.unit in {"USD", "EUR", "GBP", "JPY"} else None,
        standardized_metric=standardized_metric,
        standardized_value=standardized_value,
        standardization_version=standardization_version,
        dimensions={
            "start": fact.start.isoformat() if fact.start else None,
            "end": fact.end.isoformat(),
            "frame": fact.frame,
            "fiscal_year": fact.fiscal_year,
            "fiscal_period": fact.fiscal_period,
            "form": fact.form,
            "filed": fact.filed.isoformat(),
        },
    )


def ingest_filing_from_company_facts(
    fundamental_store: PostgresPitFundamentalStore,
    calendar_master: PostgresProfessionalInstrumentMaster,
    audit_store: AuditStore,
    *,
    source_id: UUID,
    instrument_id: str,
    venue: str,
    filing_header: SecFilingHeader,
    facts_for_this_accession: tuple[SecXbrlFact, ...],
    companyfacts_response_hash: str,
    companyfacts_source_uri: str,
    ingested_at: datetime,
) -> FundamentalFiling | None:
    """Ingest one filing's facts, idempotently, preserving amendment lineage.

    Returns the persisted (or already-existing) ``FundamentalFiling``, or
    ``None`` if ``facts_for_this_accession`` is empty (nothing to ingest --
    not an error, since not every supported header form necessarily has
    matching XBRL facts in a given company-facts pull).

    Never overwrites an existing accession (checked via
    ``find_by_accession`` first: idempotent replay, not merely fail-safe).
    Never assigns a revision that would overwrite a different accession's
    row -- amendments get their own, later revision number via
    ``latest_revision_for_reporting_period``, and the original stays exactly
    as first ingested.
    """
    if filing_header.form not in SUPPORTED_FACT_FORMS:
        raise SecUnsupportedFormError(f"unsupported_fact_form:{filing_header.form}")
    if any(fact.accession_number != filing_header.accession_number for fact in facts_for_this_accession):
        raise SecAccessionMismatchError(
            f"fact_accession_mismatch:{filing_header.accession_number}"
        )
    if not facts_for_this_accession:
        return None

    existing = fundamental_store.find_by_accession(source_id, filing_header.accession_number)
    if existing is not None:
        return existing

    if filing_header.report_date is None:
        raise SecEdgarError(f"filing_missing_report_date:{filing_header.accession_number}")
    reporting_period_end = filing_header.report_date
    reporting_period_start = _infer_period_start(filing_header.form, reporting_period_end)
    fiscal_year = facts_for_this_accession[0].fiscal_year
    fiscal_period = facts_for_this_accession[0].fiscal_period

    prior_revision = fundamental_store.latest_revision_for_reporting_period(
        source_id, instrument_id, filing_header.form, reporting_period_end
    )
    revision = 0 if prior_revision is None else prior_revision + 1

    research_available_at = compute_research_available_at(
        filing_header.acceptance_date_time, venue=venue, calendar_master=calendar_master, known_at=ingested_at,
    )
    filing = FundamentalFiling(
        source_id=source_id,
        instrument_id=instrument_id,
        filing_id=filing_header.accession_number,
        form_type=filing_header.form,
        # The submissions API's `filingDate` is date-only (no time-of-day), and SEC's
        # documented after-hours rule means a late-evening acceptance can carry an
        # earlier calendar date than the assigned filing_date -- combining filing_date
        # with midnight would then land AFTER acceptance_date_time and fail this
        # dataclass's ordering invariant. `acceptance_date_time` is the only sub-day
        # timestamp SEC actually gives us, so it is used honestly for both fields
        # rather than fabricating a distinct "filed" instant we don't have evidence for.
        filing_at=filing_header.acceptance_date_time,
        accepted_at=filing_header.acceptance_date_time,
        reporting_period_start=reporting_period_start,
        reporting_period_end=reporting_period_end,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        revision=revision,
        ingested_at=ingested_at,
        provenance_uri=companyfacts_source_uri,
        raw_payload_sha256=companyfacts_response_hash,
        research_available_at=research_available_at,
        availability_policy_version=RESEARCH_AVAILABILITY_POLICY_VERSION,
    )
    facts = tuple(build_filing_fact(fact, filing.filing_record_id) for fact in facts_for_this_accession)
    fundamental_store.ingest(filing, facts)
    audit_store.append(
        event_type="sec_edgar_filing_ingested",
        actor="sec_edgar_ingestion",
        payload={
            "instrument_id": instrument_id,
            "source_id": str(source_id),
            "accession_number": filing_header.accession_number,
            "form": filing_header.form,
            "revision": revision,
            "amendment_lineage_policy_version": AMENDMENT_LINEAGE_POLICY_VERSION,
            "availability_policy_version": RESEARCH_AVAILABILITY_POLICY_VERSION,
            "companyfacts_response_content_hash": companyfacts_response_hash,
            "companyfacts_source_uri": companyfacts_source_uri,
            "fact_count": len(facts),
            "ingested_at": ingested_at.isoformat(),
        },
    )
    return filing


def _infer_period_start(form: str, reporting_period_end: date) -> date:
    """A conservative, versioned floor for reporting_period_start.

    The submissions API's reportDate is a single point-in-time (period end);
    it does not itself state the period's start. ``FundamentalFiling`` requires
    both, so this derives a safe (never-later-than-the-true-start) floor from
    the form family alone -- a quarter for 10-Q, a year for 10-K -- which is
    conservative for any downstream duration-based consumer even when the
    exact fiscal boundary differs. Not an SEC-asserted period-start fact.
    """
    if form.startswith("10-K"):
        return reporting_period_end - timedelta(days=366)
    return reporting_period_end - timedelta(days=100)
