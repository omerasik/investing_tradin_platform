"""Real PostgreSQL evidence for Module 3G.1f.1 OpenFIGI current-identity enrichment.

Companion to test_professional_instrument_master.py and test_pilot_instruments.py
(left completely untouched): every integration test file in this suite shares one
Postgres database for the whole CI run with no reset between files, so this file
registers its own uniquely-namespaced fixture instruments (``OPENFIGITEST:...``)
rather than reusing ``mvp_instrument_universe()``, matching the isolation pattern
``test_pilot_instruments.py`` already established with its own ``PILOT:...`` ids.

No real OpenFIGI network call is made -- ``FakeOpenFigiMappingClient`` returns
fixture candidates modeled on the two real, owner-authorized anonymous probes
run against OpenFIGI's live ``/v3/mapping`` endpoint on 2026-09-07.
"""

import os
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class OpenFigiIdentityPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_enrichment_persists_only_standard_identifiers_and_never_touches_symbol_history(
        self,
    ) -> None:
        from trade_platform.audit import SQLiteAuditStore
        from trade_platform.domain import AssetClass
        from trade_platform.openfigi_identity import (
            OPENFIGI_COMPOSITE_FIGI_NAMESPACE,
            OPENFIGI_FIGI_NAMESPACE,
            OPENFIGI_SHARE_CLASS_FIGI_NAMESPACE,
            OpenFigiMappingCandidate,
            enrich_us_common_stock_with_openfigi_identity,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.professional_instruments import (
            IdentifierMapping,
            IdentifierSourceKind,
            InstrumentMappingConflictError,
            InstrumentResolutionError,
            InstrumentType,
            LifecycleStatus,
            PostgresProfessionalInstrumentMaster,
            ProfessionalInstrument,
            RepresentationKind,
            SessionType,
            SymbolMapping,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        master = PostgresProfessionalInstrumentMaster(database)
        audit_store = SQLiteAuditStore(":memory:")
        registered_at = datetime(2023, 1, 1, tzinfo=UTC)

        def build(instrument_id: str, venue: str, mic: str, symbol: str) -> ProfessionalInstrument:
            return ProfessionalInstrument(
                instrument_id=instrument_id,
                asset_class=AssetClass.EQUITY,
                instrument_type=InstrumentType.COMMON_STOCK,
                exchange_name=venue,
                venue=venue,
                mic=mic,
                canonical_symbol=symbol,
                listing_date=date(2000, 1, 1),
                base_currency="USD",
                quote_currency="USD",
                settlement_currency="USD",
                contract_multiplier=Decimal(1),
                contract_size=Decimal(1),
                tick_size=Decimal("0.01"),
                lot_size=Decimal(1),
                price_precision=2,
                quantity_precision=0,
                trading_timezone="America/New_York",
                market_session_type=SessionType.US_EQUITY,
                representation_kind=RepresentationKind.DIRECT,
                registered_at=registered_at,
                lifecycle_status=LifecycleStatus.ACTIVE,
            )

        target = build("OPENFIGITEST:XNAS:AAPL", "XNAS", "XNAS", "AAPL")
        companion = build("OPENFIGITEST:ARCX:METAFIX", "ARCX", "ARCX", "FB")
        master.register(target)
        master.register(companion)

        # Simulate a real historical rename (FB -> META) on a DIFFERENT instrument,
        # sourced from another provider, so this test can prove OpenFIGI enrichment
        # of the target instrument leaves it byte-for-byte untouched (Section 12, item 11).
        renamed_at = datetime(2023, 6, 9, tzinfo=UTC)
        fb_symbol_old = SymbolMapping(
            companion.instrument_id, "ARCX", "FB", registered_at, renamed_at,
            registered_at, "fixture://fb",
        )
        fb_symbol_new = SymbolMapping(
            companion.instrument_id, "ARCX", "META", renamed_at, None, renamed_at, "fixture://meta",
        )
        master.add_symbol_mapping(fb_symbol_old)
        master.add_symbol_mapping(fb_symbol_new)
        fb_identifier = IdentifierMapping(
            companion.instrument_id, IdentifierSourceKind.PROVIDER, "OPENFIGITEST:CIK",
            "0001326801", registered_at, None, registered_at, "fixture://fb-cik",
        )
        master.add_identifier_mapping(fb_identifier)

        def symbol_mapping_snapshot() -> object:
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT instrument_id, venue, symbol, valid_from, valid_until, "
                    "ingested_at, source_reference FROM professional_symbol_mappings "
                    "WHERE instrument_id=%s ORDER BY valid_from",
                    (companion.instrument_id,),
                )
                return cursor.fetchall()

        before = symbol_mapping_snapshot()

        captured_at = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

        class FakeOpenFigiMappingClient:
            """Stands in for a real OpenFIGI call using the real probe #2 fixture."""

            def map_jobs(self, jobs: tuple[object, ...]) -> tuple[tuple, str, str]:
                candidate = OpenFigiMappingCandidate(
                    figi="BBG000B9XRY4",
                    ticker="AAPL",
                    exch_code="US",
                    security_type="Common Stock",
                    security_type2="Common Stock",
                    market_sector="Equity",
                    composite_figi="BBG000B9XRY4",
                    share_class_figi="BBG001S5N8V8",
                )
                return (candidate,), "fixture-request-hash", "fixture-response-hash"

        mappings = enrich_us_common_stock_with_openfigi_identity(
            master,
            FakeOpenFigiMappingClient(),  # type: ignore[arg-type]
            audit_store,
            instrument_id=target.instrument_id,
            as_of=captured_at,
            captured_at=captured_at,
        )
        self.assertEqual(len(mappings), 3)

        # Item 1/D: three STANDARD rows resolvable right after capture.
        for namespace, value in (
            (OPENFIGI_FIGI_NAMESPACE, "BBG000B9XRY4"),
            (OPENFIGI_COMPOSITE_FIGI_NAMESPACE, "BBG000B9XRY4"),
            (OPENFIGI_SHARE_CLASS_FIGI_NAMESPACE, "BBG001S5N8V8"),
        ):
            resolved = master.resolve_identifier(namespace, value, captured_at)
            self.assertEqual(resolved.instrument_id, target.instrument_id)

        # Item 3: duplicate FIGI across a different instrument fails closed at the DB layer.
        with self.assertRaises(InstrumentMappingConflictError):
            master.add_identifier_mapping(
                IdentifierMapping(
                    companion.instrument_id, IdentifierSourceKind.STANDARD, OPENFIGI_FIGI_NAMESPACE,
                    "BBG000B9XRY4", captured_at, None, captured_at, "fixture://duplicate-figi",
                )
            )

        # Items 8/9: PIT resolution before the real capture timestamp must fail --
        # OpenFIGI is current-state-only and must never claim earlier knowledge.
        before_capture = datetime(2026, 1, 1, tzinfo=UTC)
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_identifier_point_in_time(
                OPENFIGI_FIGI_NAMESPACE, "BBG000B9XRY4",
                effective_at=before_capture, known_at=before_capture,
            )
        with self.assertRaises(InstrumentResolutionError):
            master.resolve_identifier_point_in_time(
                OPENFIGI_FIGI_NAMESPACE, "BBG000B9XRY4",
                effective_at=captured_at, known_at=before_capture,
            )

        # Item 10: PIT resolution strictly after capture succeeds.
        after_capture = datetime(2026, 9, 8, tzinfo=UTC)
        resolved = master.resolve_identifier_point_in_time(
            OPENFIGI_FIGI_NAMESPACE, "BBG000B9XRY4",
            effective_at=captured_at, known_at=after_capture,
        )
        self.assertEqual(resolved.instrument_id, target.instrument_id)

        # Item 11: the FB/META symbol history and provider identifier are untouched.
        after = symbol_mapping_snapshot()
        self.assertEqual(before, after)
        self.assertEqual(
            master.resolve_symbol("META", "ARCX", renamed_at).instrument_id, companion.instrument_id
        )
        self.assertEqual(
            master.resolve_identifier("OPENFIGITEST:CIK", "0001326801", captured_at).instrument_id,
            companion.instrument_id,
        )

        # Item 13: no new ProfessionalInstrument was created by the OpenFIGI path.
        with self.assertRaises(InstrumentResolutionError):
            master.get_as_of("BBG000B9XRY4", captured_at)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM professional_instruments WHERE instrument_id=%s",
                ("BBG000B9XRY4",),
            )
            self.assertIsNone(cursor.fetchone())

        # Item 14: no SymbolMapping row was written for the OpenFIGI-enriched instrument.
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM professional_symbol_mappings WHERE instrument_id=%s",
                (target.instrument_id,),
            )
            self.assertIsNone(cursor.fetchone())

        # Item 12: restart -- a fresh master/connection over the same database still resolves.
        restarted_master = PostgresProfessionalInstrumentMaster(PostgresDatabase(
            os.environ["POSTGRES_TEST_DSN"]
        ))
        resolved_after_restart = restarted_master.resolve_identifier(
            OPENFIGI_FIGI_NAMESPACE, "BBG000B9XRY4", captured_at
        )
        self.assertEqual(resolved_after_restart.instrument_id, target.instrument_id)

        # Audit evidence was recorded once, with identifiers/hashes only.
        recent = audit_store.recent(limit=10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].event_type, "openfigi_identity_enrichment_captured")
        self.assertEqual(recent[0].payload["request_content_hash"], "fixture-request-hash")


if __name__ == "__main__":
    unittest.main()
