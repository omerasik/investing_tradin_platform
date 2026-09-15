"""Real PostgreSQL evidence for source-backed Bybit BTCUSDT onboarding.

No network call is made here. The onboarding is driven entirely by the frozen
captured ``instruments-info`` snapshot committed in
``bybit_instrument_onboarding``; the one live request that produced it was a
development step, never part of import, construction or CI.

Unlike the adapter's fixture tests, this suite deliberately registers the REAL
canonical instrument ``CRYPTO:BYBIT:BTCUSDT:PERP`` -- that identity is exactly
what is under test.

Onboarding therefore happens exactly once, in :meth:`setUpClass`, and every
test reads back what it persisted. That is not a convenience: every table
involved carries this platform's ``prevent_immutable_mutation()`` trigger, so
onboarding evidence can never be updated or deleted, and registering a
canonical instrument is a genuinely once-ever act. The suite consequently
expects the fresh database CI provides (and that a local full-suite re-run must
recreate) rather than pretending it can tidy up after itself.
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest import mock

from trade_platform.bybit_instrument_onboarding import (
    BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
    CAPTURED_BTCUSDT_PAYLOAD_SHA256,
    CAPTURED_BTCUSDT_RETRIEVED_AT,
    BybitInstrumentOnboardingResult,
    BybitOnboardingConflictError,
    bybit_authorized_historical_source,
    captured_btcusdt_envelope_v1,
    captured_btcusdt_snapshot_v1,
    onboard_bybit_btcusdt_perpetual_v1,
    parse_bybit_instrument_metadata,
)
from trade_platform.crypto_instruments import (
    CryptoFundingConventionError,
    CryptoInstrumentKind,
    CryptoResolutionError,
    PostgresCryptoInstrumentAuthority,
    ReferencePriceRequirement,
    SettlementStyle,
)
from trade_platform.domain import AssetClass
from trade_platform.historical_market_data import (
    ObservationKind,
    PostgresHistoricalMarketDataPipeline,
)
from trade_platform.persistence import PostgresDatabase
from trade_platform.professional_instruments import (
    InstrumentType,
    PostgresProfessionalInstrumentMaster,
    RepresentationKind,
)

VENUE = "BYBIT"
SYMBOL = "BTCUSDT"
NAMESPACE = "bybit_v5_symbol"
LAUNCH_TIME = datetime(2020, 3, 15, tzinfo=UTC)

#: Immediately after the capture, and firmly in the past -- this shared database
#: carries global invariants that a future-dated row would violate.
ONBOARDED_AT = CAPTURED_BTCUSDT_RETRIEVED_AT + timedelta(seconds=30)
READ_AT = ONBOARDED_AT + timedelta(minutes=1)

#: Every table a successful onboarding writes, keyed by ``instrument_id``.
_TABLES_BY_INSTRUMENT: tuple[str, ...] = (
    "professional_instruments",
    "professional_symbol_mappings",
    "professional_identifier_mappings",
    "crypto_instrument_specifications",
    "crypto_venue_trading_rules",
)
#: The two remaining tables, keyed by ``source_id`` instead.
_TABLES_BY_SOURCE: tuple[str, ...] = (
    "historical_data_sources",
    "historical_source_capabilities",
)


class _AtomicityProbeError(Exception):
    """Sentinel raised mid-onboarding to prove the outer transaction rolls
    back every earlier write from the same call. Deliberately not a subclass
    of any domain error, so it propagates out of
    ``onboard_bybit_btcusdt_perpetual_v1`` completely unchanged rather than
    being caught and reinterpreted by an authority along the way."""


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class BybitInstrumentOnboardingPostgresTests(unittest.TestCase):
    database: PostgresDatabase
    first_result: BybitInstrumentOnboardingResult
    atomicity_probe_raised: bool
    atomicity_probe_remnants: dict[str, int]

    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

        cls.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])

        # Prove the atomic rollback BEFORE any real onboarding exists: once the
        # instrument is genuinely onboarded, a repeat call never reaches the
        # write path at all (it is classified as a no-op), so this probe would
        # no longer exercise anything if it ran after ``first_result`` below.
        cls._probe_atomic_rollback()

        cls.first_result = onboard_bybit_btcusdt_perpetual_v1(
            cls.database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )

    @classmethod
    def _probe_atomic_rollback(cls) -> None:
        """Inject a failure at the LAST authority write (``register_source``),
        so every earlier write in the sequence -- the professional instrument,
        both its mappings, the crypto specification and the venue trading
        rules -- has already executed for real against this database by the
        time the sentinel fires. Only the still-open outer transaction's
        rollback can undo them, so this is a genuine test of that rollback,
        not of whether ``register_source`` itself was ever called.
        """
        probe_snapshot = captured_btcusdt_snapshot_v1()
        with mock.patch.object(
            PostgresHistoricalMarketDataPipeline,
            "register_source",
            side_effect=_AtomicityProbeError("sentinel_atomicity_failure"),
        ):
            try:
                onboard_bybit_btcusdt_perpetual_v1(cls.database, probe_snapshot, ONBOARDED_AT)
            except _AtomicityProbeError:
                cls.atomicity_probe_raised = True
            else:
                cls.atomicity_probe_raised = False

        source_id = bybit_authorized_historical_source(probe_snapshot, ONBOARDED_AT).source_id
        cls.atomicity_probe_remnants = {
            table: cls._count(table, "instrument_id", BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID)
            for table in _TABLES_BY_INSTRUMENT
        } | {
            table: cls._count(table, "source_id", source_id) for table in _TABLES_BY_SOURCE
        }

    @classmethod
    def _count(cls, table: str, column: str, key: object) -> int:
        with cls.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                # table/column are drawn only from the fixed tuples above; key
                # is the sole bound parameter.
                f"SELECT COUNT(*) FROM {table} WHERE {column}=%s",  # nosec B608
                (key,),
            )
            row = cursor.fetchone()
        assert row is not None
        return int(row[0])

    def setUp(self) -> None:
        self.snapshot = captured_btcusdt_snapshot_v1()
        self.source_id = bybit_authorized_historical_source(
            self.snapshot, ONBOARDED_AT
        ).source_id

    def _query(self, statement: str, parameters: tuple[object, ...]) -> list[tuple[object, ...]]:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return [tuple(row) for row in cursor.fetchall()]

    # ---- atomicity ---------------------------------------------------------

    def test_atomic_rollback_leaves_no_canonical_remnants(self) -> None:
        """``setUpClass`` runs the injected-failure probe before performing the
        real onboarding used by every other test in this class; see
        :meth:`_probe_atomic_rollback`. This asserts on what that probe found:
        the sentinel exception propagated out of onboarding unchanged, and not
        one of the seven records a successful onboarding writes was left
        behind by the writes that ran before the sentinel fired.
        """
        self.assertTrue(
            self.atomicity_probe_raised,
            "the sentinel exception did not propagate out of onboarding",
        )
        for table, count in self.atomicity_probe_remnants.items():
            with self.subTest(table=table):
                self.assertEqual(
                    0, count, f"{table} retained a row after a rolled-back onboarding"
                )

    # ---- onboarding ------------------------------------------------------

    def test_onboarding_registers_the_canonical_instrument_through_existing_authorities(
        self,
    ) -> None:
        result = self.first_result

        self.assertFalse(result.already_onboarded)
        self.assertEqual(BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID, result.instrument_id)
        self.assertEqual(CAPTURED_BTCUSDT_PAYLOAD_SHA256, result.canonical_payload_hash)

        master = PostgresProfessionalInstrumentMaster(self.database)
        instrument = master.get_as_of(BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID, READ_AT)

        self.assertIs(AssetClass.CRYPTO, instrument.asset_class)
        self.assertIs(InstrumentType.CRYPTO_PERPETUAL, instrument.instrument_type)
        self.assertIs(RepresentationKind.PERPETUAL, instrument.representation_kind)
        self.assertEqual(VENUE, instrument.venue)
        self.assertEqual("Bybit", instrument.exchange_name)
        self.assertIsNone(instrument.mic)
        self.assertEqual(SYMBOL, instrument.canonical_symbol)
        self.assertEqual(LAUNCH_TIME.date(), instrument.listing_date)
        self.assertEqual(ONBOARDED_AT, instrument.registered_at)

    def test_launch_time_drives_listing_and_validity_while_knowledge_time_is_onboarding(
        self,
    ) -> None:
        symbol_rows = self._query(
            "SELECT valid_from,valid_until,ingested_at FROM professional_symbol_mappings "
            "WHERE instrument_id=%s",
            (BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,),
        )
        identifier_rows = self._query(
            "SELECT valid_from,valid_until,ingested_at FROM professional_identifier_mappings "
            "WHERE instrument_id=%s",
            (BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,),
        )

        for rows in (symbol_rows, identifier_rows):
            self.assertEqual(1, len(rows))
            valid_from, valid_until, ingested_at = rows[0]
            self.assertEqual(LAUNCH_TIME, valid_from)
            self.assertIsNone(valid_until)
            self.assertEqual(ONBOARDED_AT, ingested_at)
            # Knowledge time is never backdated onto the historical fact.
            self.assertNotEqual(valid_from, ingested_at)

    # ---- resolution ------------------------------------------------------

    def test_provider_namespace_resolves_btcusdt_back_to_the_canonical_instrument(
        self,
    ) -> None:
        master = PostgresProfessionalInstrumentMaster(self.database)

        resolved = master.resolve_identifier(NAMESPACE, SYMBOL, READ_AT)
        self.assertEqual(BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID, resolved.instrument_id)

        point_in_time = master.resolve_identifier_point_in_time(
            NAMESPACE, SYMBOL, READ_AT, READ_AT
        )
        self.assertEqual(
            BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID, point_in_time.instrument_id
        )

        by_symbol = master.resolve_symbol_point_in_time(SYMBOL, VENUE, READ_AT, READ_AT)
        self.assertEqual(BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID, by_symbol.instrument_id)

    def test_crypto_authority_resolve_returns_the_same_instrument(self) -> None:
        crypto = PostgresCryptoInstrumentAuthority(self.database)

        resolved = crypto.resolve(
            VENUE, "BTC", "USDT", CryptoInstrumentKind.PERPETUAL, known_at=READ_AT
        )
        self.assertEqual(BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID, resolved)
        self.assertEqual(
            resolved,
            crypto.resolve_display_symbol(SYMBOL, known_at=READ_AT, venue=VENUE),
        )

        specification = crypto.get_specification(resolved, known_at=READ_AT)
        self.assertIs(CryptoInstrumentKind.PERPETUAL, specification.kind)
        self.assertIs(SettlementStyle.LINEAR, specification.settlement_style)
        self.assertEqual("USDT", specification.settlement_asset)
        self.assertIs(
            ReferencePriceRequirement.MARK_AND_INDEX,
            specification.reference_price_requirement,
        )
        self.assertIsNone(specification.expiry_at)
        self.assertIsNone(specification.index_reference)
        self.assertEqual(self.snapshot.source_reference, specification.source_reference)

        # Asking for spot on the same pair must never return the perpetual.
        with self.assertRaises(CryptoResolutionError):
            crypto.resolve(
                VENUE, "BTC", "USDT", CryptoInstrumentKind.SPOT, known_at=READ_AT
            )

    # ---- venue trading rules --------------------------------------------

    def test_venue_rules_point_in_time_read_returns_the_exact_provider_limits(
        self,
    ) -> None:
        crypto = PostgresCryptoInstrumentAuthority(self.database)

        rules = crypto.venue_trading_rules_point_in_time(
            BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
            effective_at=self.snapshot.retrieved_at,
            known_at=READ_AT,
        )

        self.assertEqual(1, rules.rule_version)
        self.assertEqual(Decimal("0.10"), rules.tick_size)
        self.assertEqual(Decimal("0.001"), rules.quantity_step)
        self.assertEqual(Decimal("0.001"), rules.min_quantity)
        self.assertEqual(Decimal("5"), rules.min_notional)
        self.assertEqual(2, rules.price_precision)
        self.assertEqual(3, rules.quantity_precision)
        self.assertEqual(CAPTURED_BTCUSDT_PAYLOAD_SHA256, rules.source_hash)
        self.assertEqual(self.snapshot.retrieved_at, rules.effective_from)
        self.assertEqual(ONBOARDED_AT, rules.known_at)

    def test_neither_provider_maximum_is_collapsed_into_max_quantity(self) -> None:
        rows = self._query(
            "SELECT max_quantity FROM crypto_venue_trading_rules WHERE instrument_id=%s",
            (BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,),
        )
        self.assertEqual(1, len(rows))
        self.assertIsNone(rows[0][0])
        # Both maxima survive in the immutable snapshot instead.
        self.assertEqual(Decimal("1500.000"), self.snapshot.max_order_qty)
        self.assertEqual(Decimal("150.000"), self.snapshot.max_market_order_qty)

    # ---- funding ---------------------------------------------------------

    def test_no_funding_convention_is_written_in_this_phase(self) -> None:
        rows = self._query(
            "SELECT COUNT(*) FROM crypto_funding_conventions WHERE instrument_id=%s",
            (BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,),
        )
        self.assertEqual(0, rows[0][0])

        crypto = PostgresCryptoInstrumentAuthority(self.database)
        with self.assertRaises(CryptoFundingConventionError):
            crypto.funding_convention_point_in_time(
                BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
                effective_at=READ_AT,
                known_at=READ_AT,
            )

    # ---- source authority ------------------------------------------------

    def test_source_capabilities_are_exactly_the_four_approved_kinds(self) -> None:
        result = self.first_result

        rows = self._query(
            "SELECT observation_kind FROM historical_source_capabilities WHERE source_id=%s",
            (result.source_id,),
        )
        kinds = {str(row[0]) for row in rows}

        self.assertEqual(
            {
                ObservationKind.OHLCV.value,
                ObservationKind.MARK_PRICE.value,
                ObservationKind.INDEX_PRICE.value,
                ObservationKind.OPEN_INTEREST.value,
            },
            kinds,
        )
        self.assertNotIn(ObservationKind.FUNDING_RATE_REALIZED.value, kinds)
        self.assertNotIn(ObservationKind.FUNDING_RATE_INDICATIVE.value, kinds)

    def test_registered_source_records_the_operator_pilot_authority(self) -> None:
        result = self.first_result

        rows = self._query(
            "SELECT provider,provider_identifier_namespace,asset_scope,"
            "authorization_reference,authorized_at,created_at "
            "FROM historical_data_sources WHERE source_id=%s",
            (result.source_id,),
        )
        self.assertEqual(1, len(rows))
        provider, namespace, scope, reference, authorized_at, created_at = rows[0]

        self.assertEqual("bybit", provider)
        self.assertEqual(NAMESPACE, namespace)
        self.assertEqual("CRYPTO", scope)
        self.assertIn(
            "operator-approved public Bybit V5 market-data pilot authority", str(reference)
        )
        self.assertIn(CAPTURED_BTCUSDT_PAYLOAD_SHA256, str(reference))
        self.assertEqual(ONBOARDED_AT, authorized_at)
        self.assertEqual(ONBOARDED_AT, created_at)

    # ---- idempotency and duplicate behaviour ------------------------------

    def test_repeating_identical_onboarding_is_a_deterministic_no_op(self) -> None:
        first = self.first_result

        before = {
            table: self._count(table, "instrument_id", BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID)
            for table in _TABLES_BY_INSTRUMENT
        } | {
            table: self._count(table, "source_id", first.source_id) for table in _TABLES_BY_SOURCE
        }

        second = onboard_bybit_btcusdt_perpetual_v1(
            self.database, captured_btcusdt_snapshot_v1(), READ_AT
        )

        self.assertFalse(first.already_onboarded)
        self.assertTrue(second.already_onboarded)
        self.assertEqual(first.instrument_id, second.instrument_id)
        self.assertEqual(first.source_id, second.source_id)
        self.assertEqual(first.canonical_payload_hash, second.canonical_payload_hash)

        # The second call must perform zero writes: every table's row count is
        # exactly what it was before the repeat call, not merely "one" -- an
        # unchanged count is what proves no row was appended, updated away and
        # reinserted, or otherwise touched (the immutable-evidence trigger on
        # every one of these tables rules out an in-place update entirely, so
        # an unchanged count after a successful, exception-free call means no
        # write statement reached the database at all).
        for table in _TABLES_BY_INSTRUMENT:
            with self.subTest(table=table):
                after = self._count(table, "instrument_id", BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID)
                self.assertEqual(1, after)
                self.assertEqual(before[table], after)

        for table, expected_count in (
            ("historical_data_sources", 1),
            ("historical_source_capabilities", 4),
        ):
            with self.subTest(table=table):
                after = self._count(table, "source_id", first.source_id)
                self.assertEqual(expected_count, after)
                self.assertEqual(before[table], after)

    def test_onboarding_different_evidence_fails_closed_without_overwriting(self) -> None:
        envelope = captured_btcusdt_envelope_v1()
        result = envelope["result"]
        assert isinstance(result, dict)
        rows = result["list"]
        assert isinstance(rows, list)
        payload = dict(rows[0])
        payload["priceFilter"] = {"maxPrice": "1999999.80", "minPrice": "0.10", "tickSize": "0.20"}
        rows[0] = payload
        revised = parse_bybit_instrument_metadata(
            envelope, retrieved_at=CAPTURED_BTCUSDT_RETRIEVED_AT
        )
        self.assertNotEqual(
            self.snapshot.canonical_payload_hash, revised.canonical_payload_hash
        )

        with self.assertRaises(BybitOnboardingConflictError):
            onboard_bybit_btcusdt_perpetual_v1(self.database, revised, READ_AT)

        # Nothing was mutated in place: the original rule version still stands.
        crypto = PostgresCryptoInstrumentAuthority(self.database)
        stored = crypto.venue_trading_rules_point_in_time(
            BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
            effective_at=self.snapshot.retrieved_at,
            known_at=READ_AT,
        )
        self.assertEqual(Decimal("0.10"), stored.tick_size)
        self.assertEqual(CAPTURED_BTCUSDT_PAYLOAD_SHA256, stored.source_hash)
        self.assertEqual(
            1,
            self._query(
                "SELECT COUNT(*) FROM crypto_venue_trading_rules WHERE instrument_id=%s",
                (BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,),
            )[0][0],
        )


if __name__ == "__main__":
    unittest.main()
