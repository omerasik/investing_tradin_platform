"""Pure unit coverage for Module 3G.1f.2 SEC EDGAR PIT fundamentals adapter.

No real network call is made anywhere in this file -- ``SecEdgarClient`` is
exercised only against a patched ``urlopen``, and every fixture payload below
is modeled on SEC's own long-stable, publicly documented submissions/company-
facts JSON schema. This has NOT been independently re-verified via a live
call in this session (blocked on SEC_USER_AGENT not being configured) --
Section D of the final report flags this explicitly.
"""

import json
import unittest
import unittest.mock
from dataclasses import replace
from datetime import UTC, date, datetime
from datetime import time as time_of_day
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from trade_platform.audit import AuditEvent
from trade_platform.fundamentals import StatementType
from trade_platform.pit_fundamentals import FundamentalFiling
from trade_platform.sec_edgar import (
    AMENDMENT_LINEAGE_POLICY_VERSION,
    RESEARCH_AVAILABILITY_POLICY_VERSION,
    SEC_CIK_NAMESPACE,
    SecAccessionMismatchError,
    SecEdgarClient,
    SecEdgarError,
    SecFilingHeader,
    SecRateLimitError,
    SecRequestError,
    SecResponseShapeError,
    SecUnsupportedFormError,
    SecUserAgentNotConfiguredError,
    build_filing_fact,
    compute_research_available_at,
    ingest_filing_from_company_facts,
    parse_company_facts_response,
    parse_submissions_response,
    register_sec_cik_mapping,
    select_supported_filings,
    standardize_xbrl_fact,
)

EASTERN = ZoneInfo("America/New_York")

SUBMISSIONS_FIXTURE = {
    "cik": 320193,
    "name": "Apple Inc.",
    "tickers": ["AAPL"],
    "exchanges": ["Nasdaq"],
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-24-000010", "0000320193-24-000011", "0000320193-24-000099",
            ],
            "form": ["10-Q", "10-Q/A", "10-K"],
            "filingDate": ["2024-02-02", "2024-03-01", "2024-11-04"],
            "reportDate": ["2023-12-30", "2023-12-30", "2024-09-28"],
            "acceptanceDateTime": [
                "2024-02-01T18:04:28.000Z", "2024-03-01T12:00:00.000Z", "2024-11-01T22:15:00.000Z",
            ],
            "fileNumber": ["001-36743", "001-36743", "001-36743"],
            "items": ["", "", ""],
            "size": [12345, 12346, 99999],
            "isXBRL": [1, 1, 1],
            "isInlineXBRL": [1, 1, 1],
            "primaryDocument": ["aapl-20231230.htm", "aapl-20231230a.htm", "aapl-20240928.htm"],
            "primaryDocDescription": ["10-Q", "10-Q/A", "10-K"],
        },
    },
}

COMPANYFACTS_FIXTURE = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "units": {
                    "USD": [
                        {
                            "start": "2023-10-01", "end": "2023-12-30", "val": 119575000000,
                            "accn": "0000320193-24-000010", "fy": 2024, "fp": "Q1", "form": "10-Q",
                            "filed": "2024-02-02",
                        },
                    ],
                },
            },
            "Assets": {
                "label": "Assets",
                "units": {
                    "USD": [
                        {
                            "end": "2023-12-30", "val": 353000000000,
                            "accn": "0000320193-24-000010", "fy": 2024, "fp": "Q1", "form": "10-Q",
                            "filed": "2024-02-02",
                        },
                    ],
                },
            },
        },
    },
}


class SubmissionsParsingTests(unittest.TestCase):
    def test_parses_all_filing_headers(self) -> None:
        submissions = parse_submissions_response(SUBMISSIONS_FIXTURE)
        self.assertEqual(submissions.cik10, "0000320193")
        self.assertEqual(submissions.entity_name, "Apple Inc.")
        self.assertEqual(submissions.tickers, ("AAPL",))
        self.assertEqual(len(submissions.filings), 3)
        first = submissions.filings[0]
        self.assertEqual(first.accession_number, "0000320193-24-000010")
        self.assertEqual(first.form, "10-Q")
        self.assertEqual(first.report_date, date(2023, 12, 30))
        self.assertEqual(first.acceptance_date_time, datetime(2024, 2, 1, 18, 4, 28, tzinfo=UTC))
        self.assertTrue(first.is_xbrl)

    def test_select_supported_filings_includes_8k_header_but_excludes_others(self) -> None:
        with_8k = dict(SUBMISSIONS_FIXTURE)
        recent = dict(with_8k["filings"]["recent"])
        recent["form"] = ["10-Q", "10-Q/A", "8-K"]
        with_8k = {**SUBMISSIONS_FIXTURE, "filings": {"recent": recent}}
        submissions = parse_submissions_response(with_8k)
        selected = select_supported_filings(submissions.filings)
        self.assertEqual({header.form for header in selected}, {"10-Q", "10-Q/A", "8-K"})

    def test_malformed_response_raises_shape_error(self) -> None:
        with self.assertRaises(SecResponseShapeError):
            parse_submissions_response({"cik": 1, "name": "X"})

    def test_parallel_array_length_mismatch_raises_shape_error(self) -> None:
        broken = json.loads(json.dumps(SUBMISSIONS_FIXTURE))
        broken["filings"]["recent"]["form"] = ["10-Q"]
        with self.assertRaises(SecResponseShapeError):
            parse_submissions_response(broken)


class CompanyFactsParsingTests(unittest.TestCase):
    def test_parses_duration_and_instant_facts_distinctly(self) -> None:
        facts = parse_company_facts_response(COMPANYFACTS_FIXTURE)
        self.assertEqual(len(facts), 2)
        by_concept = {fact.concept: fact for fact in facts}
        self.assertEqual(by_concept["Revenues"].start, date(2023, 10, 1))
        self.assertEqual(by_concept["Revenues"].end, date(2023, 12, 30))
        self.assertIsNone(by_concept["Assets"].start)
        self.assertEqual(by_concept["Assets"].end, date(2023, 12, 30))
        self.assertEqual(by_concept["Revenues"].value, Decimal("119575000000"))

    def test_malformed_entry_raises_shape_error(self) -> None:
        broken = json.loads(json.dumps(COMPANYFACTS_FIXTURE))
        del broken["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]["val"]
        with self.assertRaises(SecResponseShapeError):
            parse_company_facts_response(broken)


class StandardizationTests(unittest.TestCase):
    def test_known_concept_is_standardized(self) -> None:
        fact = parse_company_facts_response(COMPANYFACTS_FIXTURE)[0]
        statement_type, metric, value, version = standardize_xbrl_fact(fact)
        self.assertEqual(statement_type, StatementType.INCOME_STATEMENT)
        self.assertEqual(metric, "revenue")
        self.assertEqual(value, fact.value)
        self.assertTrue(version)

    def test_unknown_concept_is_never_guessed(self) -> None:
        facts = parse_company_facts_response(COMPANYFACTS_FIXTURE)
        assets_fact = next(f for f in facts if f.concept == "Assets")
        statement_type, metric, value, version = standardize_xbrl_fact(assets_fact)
        self.assertEqual(statement_type, StatementType.OTHER)
        self.assertIsNone(metric)
        self.assertIsNone(value)
        self.assertIsNone(version)

    def test_build_filing_fact_preserves_period_and_frame_in_dimensions(self) -> None:
        from uuid import uuid4

        fact = parse_company_facts_response(COMPANYFACTS_FIXTURE)[0]
        built = build_filing_fact(fact, uuid4())
        self.assertEqual(built.dimensions["start"], "2023-10-01")
        self.assertEqual(built.dimensions["end"], "2023-12-30")
        self.assertEqual(built.dimensions["fiscal_year"], 2024)
        self.assertEqual(built.dimensions["fiscal_period"], "Q1")


class FakeCalendarMaster:
    """Weekday=open at noon ET, weekend=closed. No real DB, no real calendar rows."""

    def is_open(self, venue: str, observed_at: datetime, *, known_at: datetime | None = None) -> bool:
        del venue, known_at
        local = observed_at.astimezone(EASTERN)
        return local.weekday() < 5 and local.time() == time_of_day(12, 0)


class ResearchAvailabilityPolicyTests(unittest.TestCase):
    def test_same_business_day_before_cutoff_is_available_at_acceptance(self) -> None:
        accepted = datetime(2024, 2, 1, 18, 4, 28, tzinfo=UTC)  # ~13:04 ET, before 17:30 cutoff
        result = compute_research_available_at(
            accepted, venue="XNAS", calendar_master=FakeCalendarMaster(), known_at=accepted,
        )
        self.assertEqual(result, accepted)

    def test_after_hours_friday_filing_rolls_to_next_monday_open(self) -> None:
        # 2024-11-01 is a Friday; 22:15 UTC on that date is 18:15 EDT, after the
        # 17:30 ET cutoff -- must roll to the next business day (Monday) open.
        accepted = datetime(2024, 11, 1, 22, 15, tzinfo=UTC)
        result = compute_research_available_at(
            accepted, venue="XNAS", calendar_master=FakeCalendarMaster(), known_at=accepted,
        )
        self.assertEqual(result.astimezone(EASTERN).date(), date(2024, 11, 4))
        self.assertEqual(result.astimezone(EASTERN).time(), time_of_day(9, 30))
        self.assertGreaterEqual(result, accepted)

    def test_never_earlier_than_accepted_at(self) -> None:
        accepted = datetime(2024, 11, 1, 22, 15, tzinfo=UTC)
        result = compute_research_available_at(
            accepted, venue="XNAS", calendar_master=FakeCalendarMaster(), known_at=accepted,
        )
        self.assertGreaterEqual(result, accepted)

    def test_naive_accepted_at_is_rejected(self) -> None:
        with self.assertRaises(SecEdgarError):
            compute_research_available_at(
                datetime(2024, 2, 1, 18, 0),  # naive  # noqa: DTZ001
                venue="XNAS", calendar_master=FakeCalendarMaster(), known_at=datetime(2024, 2, 1, tzinfo=UTC),
            )


class FakeFundamentalStore:
    def __init__(self) -> None:
        self.ingested: list[tuple[FundamentalFiling, tuple[object, ...]]] = []
        self._by_accession: dict[str, FundamentalFiling] = {}

    def find_by_accession(self, source_id: object, filing_id: str) -> FundamentalFiling | None:
        del source_id
        return self._by_accession.get(filing_id)

    def latest_revision_for_reporting_period(
        self, source_id: object, instrument_id: str, form_type: str, reporting_period_end: date,
    ) -> int | None:
        del source_id, instrument_id, form_type, reporting_period_end
        return None

    def ingest(self, filing: FundamentalFiling, facts: tuple[object, ...]) -> None:
        self.ingested.append((filing, facts))
        self._by_accession[filing.filing_id] = filing


class FakeAuditStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def append(self, event_type: str, actor: str, payload: dict[str, object]) -> AuditEvent:
        self.calls.append(payload)
        from uuid import uuid4

        return AuditEvent(uuid4(), event_type, datetime(2026, 9, 7, tzinfo=UTC), actor, payload)

    def recent(self, limit: int = 100) -> list[AuditEvent]:
        return []

    def query(self, **kwargs: object) -> tuple[list[AuditEvent], bool]:
        return [], False

    def get(self, event_id: object) -> AuditEvent | None:
        return None


class IngestOrchestrationTests(unittest.TestCase):
    def _filing_header(self) -> SecFilingHeader:
        return SecFilingHeader(
            accession_number="0000320193-24-000010", form="10-Q",
            filing_date=date(2024, 2, 2), report_date=date(2023, 12, 30),
            acceptance_date_time=datetime(2024, 2, 1, 18, 4, 28, tzinfo=UTC),
            file_number="001-36743", items=None, size=12345, is_xbrl=True, is_inline_xbrl=True,
            primary_document="aapl-20231230.htm", primary_doc_description="10-Q",
        )

    def test_unsupported_form_raises(self) -> None:
        eight_k = SecFilingHeader(
            accession_number="0000320193-24-000123", form="8-K", filing_date=date(2024, 5, 1),
            report_date=date(2024, 4, 30), acceptance_date_time=datetime(2024, 5, 1, 12, tzinfo=UTC),
            file_number=None, items=None, size=None, is_xbrl=False, is_inline_xbrl=False,
            primary_document=None, primary_doc_description=None,
        )
        facts = parse_company_facts_response(COMPANYFACTS_FIXTURE)
        with self.assertRaises(SecUnsupportedFormError):
            ingest_filing_from_company_facts(
                FakeFundamentalStore(), FakeCalendarMaster(), FakeAuditStore(),
                source_id=None, instrument_id="US:XNAS:AAPL", venue="XNAS",  # type: ignore[arg-type]
                filing_header=eight_k, facts_for_this_accession=facts,
                companyfacts_response_hash="h", companyfacts_source_uri="uri",
                ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
            )

    def test_accession_mismatch_raises(self) -> None:
        header = self._filing_header()
        facts = parse_company_facts_response(COMPANYFACTS_FIXTURE)
        mismatched = (replace(facts[0], accession_number="wrong"),)
        with self.assertRaises(SecAccessionMismatchError):
            ingest_filing_from_company_facts(
                FakeFundamentalStore(), FakeCalendarMaster(), FakeAuditStore(),
                source_id=None, instrument_id="US:XNAS:AAPL", venue="XNAS",  # type: ignore[arg-type]
                filing_header=header, facts_for_this_accession=mismatched,
                companyfacts_response_hash="h", companyfacts_source_uri="uri",
                ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
            )

    def test_empty_facts_returns_none_without_error(self) -> None:
        header = self._filing_header()
        result = ingest_filing_from_company_facts(
            FakeFundamentalStore(), FakeCalendarMaster(), FakeAuditStore(),
            source_id=None, instrument_id="US:XNAS:AAPL", venue="XNAS",  # type: ignore[arg-type]
            filing_header=header, facts_for_this_accession=(),
            companyfacts_response_hash="h", companyfacts_source_uri="uri",
            ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
        )
        self.assertIsNone(result)

    def test_successful_ingestion_records_evidence_without_descriptive_payload(self) -> None:
        header = self._filing_header()
        facts = parse_company_facts_response(COMPANYFACTS_FIXTURE)
        store = FakeFundamentalStore()
        audit = FakeAuditStore()
        filing = ingest_filing_from_company_facts(
            store, FakeCalendarMaster(), audit,
            source_id="fixture-source", instrument_id="US:XNAS:AAPL", venue="XNAS",  # type: ignore[arg-type]
            filing_header=header, facts_for_this_accession=facts,
            companyfacts_response_hash="h" * 64, companyfacts_source_uri="https://data.sec.gov/x",
            ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
        )
        self.assertIsNotNone(filing)
        assert filing is not None
        self.assertEqual(filing.revision, 0)
        self.assertEqual(filing.availability_policy_version, RESEARCH_AVAILABILITY_POLICY_VERSION)
        self.assertEqual(len(store.ingested), 1)
        self.assertEqual(len(audit.calls), 1)
        self.assertEqual(audit.calls[0]["amendment_lineage_policy_version"], AMENDMENT_LINEAGE_POLICY_VERSION)

    def test_idempotent_replay_skips_reingestion(self) -> None:
        header = self._filing_header()
        facts = parse_company_facts_response(COMPANYFACTS_FIXTURE)
        store = FakeFundamentalStore()
        audit = FakeAuditStore()
        first = ingest_filing_from_company_facts(
            store, FakeCalendarMaster(), audit,
            source_id="fixture-source", instrument_id="US:XNAS:AAPL", venue="XNAS",  # type: ignore[arg-type]
            filing_header=header, facts_for_this_accession=facts,
            companyfacts_response_hash="h" * 64, companyfacts_source_uri="https://data.sec.gov/x",
            ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
        )
        second = ingest_filing_from_company_facts(
            store, FakeCalendarMaster(), audit,
            source_id="fixture-source", instrument_id="US:XNAS:AAPL", venue="XNAS",  # type: ignore[arg-type]
            filing_header=header, facts_for_this_accession=facts,
            companyfacts_response_hash="h" * 64, companyfacts_source_uri="https://data.sec.gov/x",
            ingested_at=datetime(2026, 9, 8, tzinfo=UTC),
        )
        self.assertEqual(first, second)
        self.assertEqual(len(store.ingested), 1)
        self.assertEqual(len(audit.calls), 1)


class CikMappingTests(unittest.TestCase):
    def test_registers_standard_identifier_mapping(self) -> None:
        class FakeMaster:
            def __init__(self) -> None:
                self.added: list[object] = []

            def add_identifier_mapping(self, mapping: object) -> None:
                self.added.append(mapping)

        master = FakeMaster()
        captured_at = datetime(2026, 9, 7, tzinfo=UTC)
        mapping = register_sec_cik_mapping(
            master, instrument_id="US:XNAS:AAPL", cik10="0000320193",
            captured_at=captured_at, source_reference="https://data.sec.gov/submissions/CIK0000320193.json",
        )
        self.assertEqual(mapping.namespace, SEC_CIK_NAMESPACE)
        self.assertEqual(mapping.value, "0000320193")
        self.assertIsNone(mapping.valid_until)
        self.assertEqual(len(master.added), 1)

    def test_invalid_cik_rejected(self) -> None:
        class FakeMaster:
            def add_identifier_mapping(self, mapping: object) -> None:
                pass

        with self.assertRaises(SecEdgarError):
            register_sec_cik_mapping(
                FakeMaster(), instrument_id="US:XNAS:AAPL", cik10="not-a-cik",
                captured_at=datetime(2026, 9, 7, tzinfo=UTC), source_reference="uri",
            )


class SecEdgarClientTests(unittest.TestCase):
    def test_missing_user_agent_raises_before_any_request(self) -> None:
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(SecUserAgentNotConfiguredError):
            SecEdgarClient()

    def test_get_submissions_parses_real_response_shape(self) -> None:
        payload = json.dumps(SUBMISSIONS_FIXTURE).encode()
        with patch("trade_platform.sec_edgar.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = payload
            client = SecEdgarClient(user_agent="Test App test@example.com")
            submissions, response_hash = client.get_submissions("0000320193")
        self.assertEqual(submissions.entity_name, "Apple Inc.")
        self.assertTrue(response_hash)

    def test_invalid_cik_rejected_before_any_request(self) -> None:
        client = SecEdgarClient(user_agent="Test App test@example.com")
        with self.assertRaises(SecEdgarError):
            client.get_submissions("123")

    def test_undecodable_response_raises(self) -> None:
        with patch("trade_platform.sec_edgar.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = b"not json"
            client = SecEdgarClient(user_agent="Test App test@example.com")
            with self.assertRaises(SecResponseShapeError):
                client.get_submissions("0000320193")

    def test_retries_on_429_then_succeeds(self) -> None:
        from urllib.error import HTTPError

        payload = json.dumps(SUBMISSIONS_FIXTURE).encode()
        call_count = {"n": 0}

        def side_effect(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise HTTPError("https://data.sec.gov/x", 429, "Too Many Requests", {}, None)
            mock_response = unittest.mock.MagicMock()
            mock_response.read.return_value = payload
            mock_context = unittest.mock.MagicMock()
            mock_context.__enter__.return_value = mock_response
            return mock_context

        with patch("trade_platform.sec_edgar.urlopen", side_effect=side_effect), patch(
            "trade_platform.sec_edgar.time.sleep"
        ):
            client = SecEdgarClient(user_agent="Test App test@example.com")
            submissions, _ = client.get_submissions("0000320193")
        self.assertEqual(submissions.entity_name, "Apple Inc.")
        self.assertEqual(call_count["n"], 2)

    def test_exhausted_retries_raise_rate_limit_error(self) -> None:
        from urllib.error import HTTPError

        with patch(
            "trade_platform.sec_edgar.urlopen",
            side_effect=HTTPError("https://data.sec.gov/x", 429, "Too Many Requests", {}, None),
        ), patch("trade_platform.sec_edgar.time.sleep"):
            client = SecEdgarClient(user_agent="Test App test@example.com", max_retries=1)
            with self.assertRaises(SecRateLimitError):
                client.get_submissions("0000320193")

    def test_non_retryable_http_error_raises_rate_limit_error_immediately(self) -> None:
        from urllib.error import HTTPError

        with patch(
            "trade_platform.sec_edgar.urlopen",
            side_effect=HTTPError("https://data.sec.gov/x", 404, "Not Found", {}, None),
        ):
            client = SecEdgarClient(user_agent="Test App test@example.com")
            with self.assertRaises(SecRateLimitError):
                client.get_submissions("0000320193")

    def test_connection_error_raises_request_error(self) -> None:
        from urllib.error import URLError

        with patch("trade_platform.sec_edgar.urlopen", side_effect=URLError("connection refused")):
            client = SecEdgarClient(user_agent="Test App test@example.com")
            with self.assertRaises(SecRequestError):
                client.get_submissions("0000320193")


if __name__ == "__main__":
    unittest.main()
