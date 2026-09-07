"""Real PostgreSQL evidence for Module 3G.1f.2 SEC EDGAR PIT fundamentals authority.

Companion to test_professional_instrument_master.py, test_pilot_instruments.py, and
test_openfigi_identity_postgres.py (all left completely untouched): every integration
test file in this suite shares one Postgres database for the whole CI run with no
reset between files, so this file registers its own uniquely-namespaced fixture
instruments (``SECTEST:...``) rather than reusing shared fixtures.

No real network call is made anywhere in this file -- filing headers and XBRL facts
come from fixture payloads modeled on SEC's own publicly documented JSON schema, fed
through the real parsing/orchestration code path (``sec_edgar.parse_submissions_response``,
``parse_company_facts_response``, ``ingest_filing_from_company_facts``). ``SecEdgarClient``
itself is never instantiated here.
"""

import hashlib
import json
import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class SecEdgarPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_two_clock_pit_amendment_lineage_cik_mapping_and_provenance(self) -> None:
        from trade_platform.audit import SQLiteAuditStore
        from trade_platform.domain import AssetClass
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.pit_fundamentals import (
            AuthorizedFilingSource,
            PitFundamentalError,
            PostgresPitFundamentalStore,
        )
        from trade_platform.professional_instruments import (
            IdentifierMapping,
            IdentifierSourceKind,
            InstrumentMappingConflictError,
            InstrumentType,
            LifecycleStatus,
            PostgresProfessionalInstrumentMaster,
            ProfessionalCalendarError,
            ProfessionalInstrument,
            RepresentationKind,
            SessionType,
            SymbolMapping,
            standard_calendars,
        )
        from trade_platform.sec_edgar import (
            SEC_CIK_NAMESPACE,
            ingest_filing_from_company_facts,
            parse_company_facts_response,
            parse_submissions_response,
            register_sec_cik_mapping,
            select_supported_filings,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        master = PostgresProfessionalInstrumentMaster(database)
        fundamental_store = PostgresPitFundamentalStore(database)
        audit_store = SQLiteAuditStore(":memory:")
        registered_at = datetime(2023, 1, 1, tzinfo=UTC)

        def build(instrument_id: str, venue: str, symbol: str) -> ProfessionalInstrument:
            return ProfessionalInstrument(
                instrument_id=instrument_id, asset_class=AssetClass.EQUITY,
                instrument_type=InstrumentType.COMMON_STOCK, exchange_name=venue, venue=venue,
                mic=venue, canonical_symbol=symbol, listing_date=date(2000, 1, 1),
                base_currency="USD", quote_currency="USD", settlement_currency="USD",
                contract_multiplier=Decimal(1), contract_size=Decimal(1), tick_size=Decimal("0.01"),
                lot_size=Decimal(1), price_precision=2, quantity_precision=0,
                trading_timezone="America/New_York", market_session_type=SessionType.US_EQUITY,
                representation_kind=RepresentationKind.DIRECT, registered_at=registered_at,
                lifecycle_status=LifecycleStatus.ACTIVE,
            )

        target = build("SECTEST:XNAS:AAPL", "XNAS", "AAPL")
        companion = build("SECTEST:ARCX:METAFIX", "ARCX", "FB")
        master.register(target)
        master.register(companion)
        # standard_calendars() uses fixed, deterministic calendar_ids per venue --
        # test_professional_instrument_master.py (run earlier, alphabetically, in
        # this shared-database suite) already registers the same XNAS/ARCX rows.
        # A duplicate here is the expected, benign outcome of two test files
        # legitimately wanting the same real calendar convention, not a conflict.
        for calendar, sessions in standard_calendars(registered_at):
            try:
                master.add_calendar(calendar)
            except ProfessionalCalendarError:
                pass
            for session in sessions:
                try:
                    master.add_weekly_session(session)
                except ProfessionalCalendarError:
                    pass

        # Unrelated pre-existing history (a rename + an OpenFIGI-style STANDARD
        # identifier, using symbols unique to this test file to avoid colliding with
        # test_openfigi_identity_postgres.py's own "FB"/"META" companion fixture in
        # this shared database) that this whole SEC ingestion path must leave
        # byte-for-byte alone.
        renamed_at = datetime(2023, 6, 9, tzinfo=UTC)
        master.add_symbol_mapping(
            SymbolMapping(companion.instrument_id, "ARCX", "SECFIXOLD", registered_at, renamed_at,
                          registered_at, "fixture://secfix-old")
        )
        master.add_symbol_mapping(
            SymbolMapping(companion.instrument_id, "ARCX", "SECFIXNEW", renamed_at, None,
                          renamed_at, "fixture://secfix-new")
        )
        openfigi_mapping = IdentifierMapping(
            companion.instrument_id, IdentifierSourceKind.STANDARD, "OPENFIGI:FIGI",
            "BBG000SECFIX1", registered_at, None, registered_at, "fixture://openfigi-secfix",
        )
        master.add_identifier_mapping(openfigi_mapping)

        def unrelated_snapshot() -> object:
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT instrument_id,venue,symbol,valid_from,valid_until,ingested_at,"
                    "source_reference FROM professional_symbol_mappings WHERE instrument_id=%s "
                    "ORDER BY valid_from", (companion.instrument_id,),
                )
                symbols = cursor.fetchall()
                cursor.execute(
                    "SELECT instrument_id,source_kind,namespace,identifier_value,valid_from,"
                    "valid_until,ingested_at,source_reference FROM professional_identifier_mappings "
                    "WHERE instrument_id=%s ORDER BY namespace", (companion.instrument_id,),
                )
                identifiers = cursor.fetchall()
            return symbols, identifiers

        before = unrelated_snapshot()

        source = AuthorizedFilingSource(
            "SEC_EDGAR", "sec-fair-access-2026-09-07", "test-authorization://sec-edgar-pilot",
            registered_at, registered_at,
        )
        fundamental_store.register_source(source)

        captured_at = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
        register_sec_cik_mapping(
            master, instrument_id=target.instrument_id, cik10="0000320193",
            captured_at=captured_at, source_reference="https://data.sec.gov/submissions/CIK0000320193.json",
        )
        # Item 15: wrong CIK/instrument mapping fails closed at the DB layer.
        with self.assertRaises(InstrumentMappingConflictError):
            register_sec_cik_mapping(
                master, instrument_id=companion.instrument_id, cik10="0000320193",
                captured_at=captured_at, source_reference="fixture://duplicate-cik",
            )
        self.assertEqual(
            master.resolve_identifier(SEC_CIK_NAMESPACE, "0000320193", captured_at).instrument_id,
            target.instrument_id,
        )

        submissions_fixture = {
            "cik": 320193, "name": "Apple Inc.", "tickers": ["AAPL"], "exchanges": ["Nasdaq"],
            "filings": {"recent": {
                "accessionNumber": [
                    "0000320193-24-000010", "0000320193-24-000011", "0000320193-24-000099",
                ],
                "form": ["10-Q", "10-Q/A", "10-K"],
                "filingDate": ["2024-02-02", "2024-03-01", "2024-11-04"],
                "reportDate": ["2023-12-30", "2023-12-30", "2024-09-28"],
                "acceptanceDateTime": [
                    "2024-02-01T18:04:28.000Z", "2024-03-01T12:00:00.000Z", "2024-11-01T22:15:00.000Z",
                ],
                "fileNumber": ["001-36743"] * 3, "items": [""] * 3, "size": [1, 2, 3],
                "isXBRL": [1, 1, 1], "isInlineXBRL": [1, 1, 1],
                "primaryDocument": ["a.htm", "b.htm", "c.htm"],
                "primaryDocDescription": ["10-Q", "10-Q/A", "10-K"],
            }},
        }
        submissions = parse_submissions_response(submissions_fixture)
        headers_by_accession = {header.accession_number: header for header in select_supported_filings(submissions.filings)}

        companyfacts_fixture = {
            "cik": 320193, "entityName": "Apple Inc.",
            "facts": {"us-gaap": {
                "Revenues": {"units": {"USD": [
                    {"start": "2023-10-01", "end": "2023-12-30", "val": 119575000000,
                     "accn": "0000320193-24-000010", "fy": 2024, "fp": "Q1", "form": "10-Q",
                     "filed": "2024-02-02"},
                    {"start": "2023-10-01", "end": "2023-12-30", "val": 119575500000,
                     "accn": "0000320193-24-000011", "fy": 2024, "fp": "Q1", "form": "10-Q/A",
                     "filed": "2024-03-01"},
                    {"start": "2023-10-01", "end": "2024-09-28", "val": 391000000000,
                     "accn": "0000320193-24-000099", "fy": 2024, "fp": "FY", "form": "10-K",
                     "filed": "2024-11-04"},
                ]}},
                "Assets": {"units": {"USD": [
                    {"end": "2023-12-30", "val": 353000000000,
                     "accn": "0000320193-24-000010", "fy": 2024, "fp": "Q1", "form": "10-Q",
                     "filed": "2024-02-02"},
                ]}},
            }},
        }
        companyfacts_bytes = json.dumps(companyfacts_fixture, sort_keys=True).encode()
        companyfacts_hash = hashlib.sha256(companyfacts_bytes).hexdigest()
        all_facts = parse_company_facts_response(json.loads(companyfacts_bytes))
        source_uri = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
        ingested_at = datetime(2026, 9, 7, 13, 0, tzinfo=UTC)

        filings_by_accession = {}
        for accession, header in headers_by_accession.items():
            facts_for_accession = tuple(f for f in all_facts if f.accession_number == accession)
            filing = ingest_filing_from_company_facts(
                fundamental_store, master, audit_store,
                source_id=source.source_id, instrument_id=target.instrument_id, venue="XNAS",
                filing_header=header, facts_for_this_accession=facts_for_accession,
                companyfacts_response_hash=companyfacts_hash, companyfacts_source_uri=source_uri,
                ingested_at=ingested_at,
            )
            self.assertIsNotNone(filing)
            filings_by_accession[accession] = filing

        original = filings_by_accession["0000320193-24-000010"]
        amendment = filings_by_accession["0000320193-24-000011"]
        after_hours_10k = filings_by_accession["0000320193-24-000099"]

        # Item 9/12/J: amendment supersedes the original for the SAME reporting
        # period via an incrementing revision, both preserved as distinct rows.
        self.assertEqual(original.revision, 0)
        self.assertEqual(amendment.revision, 1)
        self.assertEqual(original.reporting_period_end, amendment.reporting_period_end)

        # Item 14: provenance checksum is deterministic and matches the exact bytes used.
        self.assertEqual(original.raw_payload_sha256, companyfacts_hash)
        self.assertEqual(len(original.raw_payload_sha256), 64)

        # Item 8/9: original visible once its own availability boundary passes;
        # amendment only visible once ITS OWN (later) boundary passes.
        just_before_amendment = amendment.research_available_at - timedelta(seconds=1)
        visible_before_amendment = fundamental_store.available_point_in_time(
            target.instrument_id, just_before_amendment, ingested_at, standardized_metric="revenue",
        )
        self.assertEqual(len(visible_before_amendment), 1)
        self.assertEqual(visible_before_amendment[0].fact.as_reported_value, Decimal("119575000000"))
        self.assertEqual(visible_before_amendment[0].filing.filing_id, original.filing_id)

        visible_after_amendment = fundamental_store.available_point_in_time(
            target.instrument_id, amendment.research_available_at, ingested_at,
            standardized_metric="revenue",
        )
        # Item 10: only the FY2024-Q1 group's latest revision (the amendment) shows up
        # for this reporting period -- the original's row does not cross-contaminate.
        q1_visible = [
            item for item in visible_after_amendment
            if item.filing.reporting_period_end == date(2023, 12, 30)
        ]
        self.assertEqual(len(q1_visible), 1)
        self.assertEqual(q1_visible[0].fact.as_reported_value, Decimal("119575500000"))
        self.assertEqual(q1_visible[0].filing.filing_id, amendment.filing_id)

        # Item 7: the after-hours 10-K (accepted Fri 2024-11-01 18:15 ET, past the
        # 5:30pm cutoff) must NOT be visible before its rolled-forward Monday-open
        # availability, even though its own accepted_at already passed.
        self.assertGreater(after_hours_10k.research_available_at, after_hours_10k.accepted_at)
        premature = fundamental_store.available_point_in_time(
            target.instrument_id,
            after_hours_10k.accepted_at + timedelta(hours=1),
            ingested_at, standardized_metric=None,
        )
        self.assertFalse(any(item.filing.filing_id == after_hours_10k.filing_id for item in premature))
        on_time = fundamental_store.available_point_in_time(
            target.instrument_id, after_hours_10k.research_available_at, ingested_at, standardized_metric=None,
        )
        self.assertTrue(any(item.filing.filing_id == after_hours_10k.filing_id for item in on_time))

        # Item 11/12: instant (Assets) vs duration (Revenues) facts for the SAME
        # accession remain distinct, not merged or overwritten.
        original_facts = fundamental_store.available_point_in_time(
            target.instrument_id, original.research_available_at, ingested_at,
        )
        original_concepts = {
            item.fact.concept for item in original_facts if item.filing.filing_id == original.filing_id
        }
        self.assertEqual(original_concepts, {"Revenues", "Assets"})

        # Item 13: idempotent replay -- re-ingesting the identical accession is a no-op.
        replay = ingest_filing_from_company_facts(
            fundamental_store, master, audit_store,
            source_id=source.source_id, instrument_id=target.instrument_id, venue="XNAS",
            filing_header=headers_by_accession["0000320193-24-000010"],
            facts_for_this_accession=tuple(
                f for f in all_facts if f.accession_number == "0000320193-24-000010"
            ),
            companyfacts_response_hash=companyfacts_hash, companyfacts_source_uri=source_uri,
            ingested_at=datetime(2026, 9, 9, tzinfo=UTC),
        )
        self.assertEqual(replay, original)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM pit_fundamental_filings WHERE source_id=%s AND filing_id=%s",
                (source.source_id, "0000320193-24-000010"),
            )
            self.assertEqual(cursor.fetchone()[0], 1)

        # Item 2 (top-level bug fix): known_at before real ingestion sees nothing,
        # even for an effective_at long after the filing's own availability.
        self.assertEqual(
            fundamental_store.available_point_in_time(
                target.instrument_id, original.research_available_at, datetime(2026, 9, 6, tzinfo=UTC),
            ),
            (),
        )
        with self.assertRaises(PitFundamentalError):
            fundamental_store.available_point_in_time(
                target.instrument_id, ingested_at, datetime(2026, 1, 1, tzinfo=UTC),
            )

        # Item 16: unrelated FB/META history and OpenFIGI identifier remain untouched.
        after = unrelated_snapshot()
        self.assertEqual(before, after)

        # Item 17: restart -- a fresh connection over the same database still resolves.
        restarted_store = PostgresPitFundamentalStore(PostgresDatabase(os.environ["POSTGRES_TEST_DSN"]))
        restarted_visible = restarted_store.available_point_in_time(
            target.instrument_id, amendment.research_available_at, ingested_at, standardized_metric="revenue",
        )
        self.assertTrue(any(item.filing.filing_id == amendment.filing_id for item in restarted_visible))

        database.close()


if __name__ == "__main__":
    unittest.main()
