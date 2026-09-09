"""Real PostgreSQL evidence for Module 3J.1b (Cross-Asset Open Interest Change
Feature).

Fixture instruments use the ``TESTFIXTURE:3J1B:`` prefix so they can never
displace real records on the shared CI database. Every open-interest figure,
provider identifier and timestamp is a FIXTURE; nothing here was retrieved
from or verified against any exchange or crypto venue, and no alpha claim is
made.
"""

import hashlib
import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

_VALUE_SCALE = Decimal("1E-12")


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class OpenInterestFeaturesPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_open_interest_change_end_to_end(self) -> None:
        from trade_platform.crypto_instruments import (
            CryptoInstrumentKind,
            CryptoInstrumentSpecification,
            CryptoSettlementType,
            PostgresCryptoInstrumentAuthority,
            ReferencePriceRequirement,
            SettlementStyle,
        )
        from trade_platform.domain import AssetClass
        from trade_platform.feature_authority import (
            FeatureAuthorityError,
            FeatureFamily,
            FeatureSubjectType,
            PostgresFeatureAuthority,
        )
        from trade_platform.futures_contracts import (
            FuturesContractSeries,
            FuturesContractSpecification,
            PostgresFuturesContractAuthority,
            SettlementType,
        )
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            AssetScope,
            AuthorizedHistoricalSource,
            HistoricalDataQualityError,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            RawHistoricalObservation,
        )
        from trade_platform.open_interest_features import (
            OPEN_INTEREST_CHANGE,
            OpenInterestFeatureError,
            PostgresOpenInterestFeatureCalculator,
            open_interest_change_definition,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.professional_instruments import (
            IdentifierMapping,
            IdentifierSourceKind,
            InstrumentType,
            LifecycleStatus,
            PostgresProfessionalInstrumentMaster,
            ProfessionalInstrument,
            RepresentationKind,
            SessionType,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        database = PostgresDatabase(dsn)
        master = PostgresProfessionalInstrumentMaster(database)
        contracts = PostgresFuturesContractAuthority(database)
        crypto = PostgresCryptoInstrumentAuthority(database)
        pipeline = PostgresHistoricalMarketDataPipeline(database)
        feature_authority = PostgresFeatureAuthority(database)
        calculator = PostgresOpenInterestFeatureCalculator(database)

        registered_at = datetime(2024, 1, 2, tzinfo=UTC)
        fut_namespace = "TESTFIX_3J1B_FUT_PROVIDER"
        crypto_namespace = "TESTFIX_3J1B_CRYPTO_PROVIDER"
        fut_venue = "XCEC"
        # A module-unique venue -- the shared CI/dev Postgres database is never
        # reset between test files, and tests/test_crypto_market_observations_postgres.py
        # already claims the (TESTFIXCEX, BTC, USDT, PERPETUAL) undated identity.
        crypto_venue = "TESTFIXCEX3J1B"
        #: A second venue for the unit/unit_asset-mismatch fixture instrument --
        #: ``crypto_undated_identity_idx`` allows only one PERPETUAL per
        #: (venue, base_asset, quote_asset), and cryp_base_id already claims
        #: (TESTFIXCEX3J1B, BTC, USDT, PERPETUAL).
        crypto_venue_mm = "TESTFIXCEX3J1B2"

        # ---- instrument fixtures ----------------------------------------------
        series_id = "TESTFIXTURE:3J1B:SERIES:GC"
        contracts.register_series(
            FuturesContractSeries(
                series_id=series_id, root_symbol="TESTFIX3J1BGC", exchange_name="COMEX",
                venue=fut_venue, mic=fut_venue, asset_class=AssetClass.COMMODITY,
                underlying_reference="Gold", currency="USD", contract_multiplier=Decimal(100),
                unit_of_measure="TROY_OUNCE", tick_size=Decimal("0.10"), tick_value=Decimal("10.00"),
                price_precision=2, quantity_precision=0, settlement_type=SettlementType.CASH_SETTLED,
                trading_timezone="America/New_York", session_type=SessionType.FUTURES_23X5,
                registered_at=registered_at, source_reference="fixture:series",
            )
        )
        _contract_months = iter(range(1, 12))

        def register_futures(suffix: str, symbol: str) -> str:
            instrument_id = f"TESTFIXTURE:3J1B:{suffix}"
            expiration_date = date(2026, 3, 26)
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id, asset_class=AssetClass.COMMODITY,
                    instrument_type=InstrumentType.FUTURE, exchange_name="COMEX",
                    venue=fut_venue, mic=fut_venue, canonical_symbol=symbol,
                    listing_date=date(2023, 1, 3), base_currency="USD", quote_currency="USD",
                    settlement_currency="USD", contract_multiplier=Decimal(100),
                    contract_size=Decimal(100), tick_size=Decimal("0.10"), lot_size=Decimal(1),
                    price_precision=2, quantity_precision=0,
                    trading_timezone="America/New_York",
                    market_session_type=SessionType.FUTURES_23X5,
                    representation_kind=RepresentationKind.FUTURE, registered_at=registered_at,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                    contract_code=symbol, expiration_date=expiration_date, first_notice_date=None,
                    last_trade_date=date(2026, 3, 24), roll_rule="TESTFIX_3J1B_ROLL_V1",
                    continuous_parent_id=None,
                )
            )
            contracts.specify_contract(
                FuturesContractSpecification(
                    instrument_id=instrument_id, series_id=series_id, contract_code=symbol,
                    contract_year=2026, contract_month=next(_contract_months),
                    first_trade_date=date(2023, 1, 3), last_trade_date=date(2026, 3, 24),
                    expiration_date=expiration_date, settlement_date=expiration_date + timedelta(days=1),
                    settlement_type=SettlementType.CASH_SETTLED, contract_multiplier=Decimal(100),
                    tick_size=Decimal("0.10"), tick_value=Decimal("10.00"), registered_at=registered_at,
                    source_reference="fixture:contract",
                )
            )
            return instrument_id

        def add_identifier(instrument_id: str, value: str, namespace: str = fut_namespace) -> None:
            master.add_identifier_mapping(
                IdentifierMapping(
                    instrument_id=instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
                    namespace=namespace, value=value, valid_from=registered_at,
                    valid_until=None, ingested_at=registered_at,
                    source_reference="fixture:provider-identifier",
                )
            )

        def register_crypto(suffix: str, symbol: str, identifier: str, *, venue: str = crypto_venue) -> str:
            instrument_id = f"TESTFIXTURE:3J1B:{suffix}"
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id, asset_class=AssetClass.CRYPTO,
                    instrument_type=InstrumentType.CRYPTO_PERPETUAL, exchange_name=venue,
                    venue=venue, mic=None, canonical_symbol=symbol,
                    listing_date=date(2024, 1, 2), base_currency="BTC", quote_currency="USDT",
                    settlement_currency="USDT", contract_multiplier=Decimal(1),
                    contract_size=Decimal(1), tick_size=Decimal("0.01"), lot_size=Decimal("0.00001"),
                    price_precision=2, quantity_precision=5, trading_timezone="UTC",
                    market_session_type=SessionType.CRYPTO_24X7,
                    representation_kind=RepresentationKind.PERPETUAL, registered_at=registered_at,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                )
            )
            master.add_identifier_mapping(
                IdentifierMapping(
                    instrument_id=instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
                    namespace=crypto_namespace, value=identifier, valid_from=registered_at,
                    valid_until=None, ingested_at=registered_at,
                    source_reference="fixture:provider-identifier",
                )
            )
            crypto.specify_instrument(
                CryptoInstrumentSpecification(
                    instrument_id=instrument_id, venue=venue,
                    kind=CryptoInstrumentKind.PERPETUAL, base_asset="BTC", quote_asset="USDT",
                    settlement_asset="USDT", settlement_style=SettlementStyle.LINEAR,
                    settlement_type=CryptoSettlementType.CASH_SETTLED,
                    contract_multiplier=Decimal(1), contract_size=Decimal(1),
                    reference_price_requirement=ReferencePriceRequirement.MARK_AND_INDEX,
                    index_reference=f"TESTFIX_3J1B_{suffix}_INDEX", registered_at=registered_at,
                    source_reference="fixture:crypto-specification",
                )
            )
            return instrument_id

        fut_main_id = register_futures("FUTMAIN", "TESTFIX3J1BFUTMAIN")
        fut_other_id = register_futures("FUTOTHER", "TESTFIX3J1BFUTOTHER")
        fut_cross_id = register_futures("FUTCROSS", "TESTFIX3J1BFUTCROSS")
        fut_rev_id = register_futures("FUTREV", "TESTFIX3J1BFUTREV")
        fut_seq_id = register_futures("FUTSEQ", "TESTFIX3J1BFUTSEQ")
        fut_ambig_id = register_futures("FUTAMBIG", "TESTFIX3J1BFUTAMBIG")
        fut_ambig2_id = register_futures("FUTAMBIG2", "TESTFIX3J1BFUTAMBIG2")
        for suffix, identifier_id in (
            ("FUTMAIN", fut_main_id), ("FUTOTHER", fut_other_id), ("FUTCROSS", fut_cross_id),
            ("FUTREV", fut_rev_id), ("FUTSEQ", fut_seq_id),
        ):
            add_identifier(identifier_id, suffix)
        add_identifier(fut_ambig_id, "AMBIGA")
        add_identifier(fut_ambig_id, "AMBIGB")
        add_identifier(fut_ambig2_id, "AMBIGC")
        add_identifier(fut_ambig2_id, "AMBIGD")

        cryp_base_id = register_crypto("CRYPTOBASE", "TESTFIX3J1BCRYPTOBASE", "CRYPTOBASE")
        cryp_mismatch_id = register_crypto(
            "CRYPTOMM", "TESTFIX3J1BCRYPTOMM", "CRYPTOMM", venue=crypto_venue_mm
        )

        # ---- sources -----------------------------------------------------------
        fut_source = AuthorizedHistoricalSource(
            provider="TESTFIX_3J1B_FUT", dataset_name="oi-feature-fixture-futures",
            provider_identifier_namespace=fut_namespace, provider_terms_version="v1",
            authorization_reference="fixture://authorization/oi-features-futures",
            authorized_at=registered_at, created_at=registered_at, asset_scope=AssetScope.FUTURES.value,
            authorized_observation_kinds=frozenset(
                {ObservationKind.OPEN_INTEREST, ObservationKind.SETTLEMENT_PRICE}
            ),
        )
        crypto_source = AuthorizedHistoricalSource(
            provider="TESTFIX_3J1B_CRYPTO", dataset_name="oi-feature-fixture-crypto",
            provider_identifier_namespace=crypto_namespace, provider_terms_version="v1",
            authorization_reference="fixture://authorization/oi-features-crypto",
            authorized_at=registered_at, created_at=registered_at, asset_scope=AssetScope.CRYPTO.value,
            authorized_observation_kinds=frozenset({ObservationKind.OPEN_INTEREST}),
        )
        pipeline.register_source(fut_source)
        pipeline.register_source(crypto_source)

        # ---- payload / capture helpers ------------------------------------------
        def oi_payload(value: str, unit: str, observed_at: datetime, unit_asset: str | None = None) -> dict[str, object]:
            payload: dict[str, object] = {
                "open_interest": value, "unit": unit, "observed_at": observed_at.isoformat(),
            }
            if unit_asset is not None:
                payload["unit_asset"] = unit_asset
            return payload

        def settlement_payload(observed_on: date) -> dict[str, object]:
            return {
                "settlement_price": "2350.40", "price_currency": "USD",
                "settlement_date": observed_on.isoformat(),
                "settlement_effective_at": f"{observed_on.isoformat()}T18:00:00+00:00",
                "finality": "FINAL", "quote_unit": "USD_PER_TROY_OUNCE",
            }

        def capture_and_normalize(
            source: AuthorizedHistoricalSource, kind: "ObservationKind", identifier: str,
            symbol: str, venue: str, payload: dict[str, object], *,
            event_at: datetime, ingested_at: datetime | None = None, normalized_at: datetime | None = None,
            revision: int = 0,
        ) -> object:
            resolved_ingested_at = ingested_at or event_at
            (raw_id,) = pipeline.capture_raw(
                [
                    RawHistoricalObservation(
                        source_id=source.source_id, observation_kind=kind,
                        provider_identifier=identifier, provider_symbol=symbol, exchange=venue,
                        event_at=event_at, effective_at=event_at, ingested_at=resolved_ingested_at,
                        adjustment_status=AdjustmentStatus.AS_REPORTED, revision=revision,
                        provenance_uri=f"fixture://{kind.value}/{identifier}/{revision}/{event_at.isoformat()}",
                        raw_payload=payload,
                    )
                ]
            )
            return pipeline.normalize(raw_id, "3j1b-v1", normalized_at or resolved_ingested_at)

        def capture_oi(
            source: AuthorizedHistoricalSource, identifier: str, symbol: str, venue: str,
            value: str, unit: str, event_at: datetime, *,
            unit_asset: str | None = None, ingested_at: datetime | None = None,
            normalized_at: datetime | None = None, revision: int = 0,
        ) -> object:
            return capture_and_normalize(
                source, ObservationKind.OPEN_INTEREST, identifier, symbol, venue,
                oi_payload(value, unit, event_at, unit_asset), event_at=event_at,
                ingested_at=ingested_at, normalized_at=normalized_at, revision=revision,
            )

        # =========================================================================
        # ---- register the single 3J.1b feature definition (invariants 1, 2, 8) --
        # =========================================================================
        oi_def = open_interest_change_definition(registered_at)
        feature_authority.register(oi_def)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT feature_id, family FROM feature_definition_versions WHERE name=%s",
                (OPEN_INTEREST_CHANGE,),
            )
            rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0][1]), "DERIVATIVES")
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM feature_definition_versions WHERE name LIKE %s",
                ("%open_interest%",),
            )
            names = {str(row[0]) for row in cursor.fetchall()}
        # Invariant 8: only the change is registered, never a raw level feature.
        self.assertEqual(names, {OPEN_INTEREST_CHANGE})

        # =========================================================================
        # ==== Scenario A: happy-path futures CONTRACTS delta (invariants 3, 4, 9,
        # 15, 23, 26, 27, 29, 30, 31, 32) ==========================================
        # =========================================================================
        a_prior_at = datetime(2025, 3, 1, 18, tzinfo=UTC)
        a_current_at = datetime(2025, 3, 2, 18, tzinfo=UTC)
        a_prior_value = Decimal("1000.100000000000000000")
        a_current_value = Decimal("1200.123456789012345670")
        a_prior_obs = capture_oi(
            fut_source, "FUTMAIN", "TESTFIX3J1BFUTMAIN", fut_venue, str(a_prior_value),
            "CONTRACTS", a_prior_at,
        )
        a_current_obs = capture_oi(
            fut_source, "FUTMAIN", "TESTFIX3J1BFUTMAIN", fut_venue, str(a_current_value),
            "CONTRACTS", a_current_at,
        )
        # A second instrument's OI observation, same event instant, same dataset,
        # with no prior of its own -- proves invariants 9 and 15 together: it
        # must never borrow fut_main's earlier point from the same dataset.
        a_other_obs = capture_oi(
            fut_source, "FUTOTHER", "TESTFIX3J1BFUTOTHER", fut_venue, "999", "CONTRACTS", a_current_at,
        )
        a_seal_at = datetime(2025, 3, 2, 19, tzinfo=UTC)
        d_a = pipeline.seal_dataset(
            fut_source.source_id, "3j1b-dataset-happy-futures", "3j1b-v1",
            (
                a_prior_obs.normalized_observation_id, a_current_obs.normalized_observation_id,
                a_other_obs.normalized_observation_id,
            ),
            a_seal_at,
        )
        a_decision_at = datetime(2025, 3, 2, 20, tzinfo=UTC)

        result_a = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=fut_main_id,
            dataset_version_id=d_a.dataset_version_id, event_at=a_current_at, decision_at=a_decision_at,
        )
        self.assertIsNotNone(result_a)
        assert result_a is not None
        self.assertEqual(result_a.subject_type, FeatureSubjectType.INSTRUMENT)  # invariant 3
        self.assertEqual(result_a.subject_id, fut_main_id)
        self.assertEqual(result_a.dataset_version, str(d_a.dataset_version_id))
        expected_delta = (a_current_value - a_prior_value).quantize(_VALUE_SCALE)  # invariant 29
        self.assertEqual(result_a.value, expected_delta)  # invariants 4, 23
        self.assertEqual(result_a.event_at, a_current_at)
        self.assertEqual(result_a.effective_at, a_current_at)  # invariant 26
        self.assertEqual(result_a.knowledge_at, d_a.created_at)  # invariant 26
        self.assertEqual(result_a.computed_at, result_a.knowledge_at)
        self.assertEqual(result_a.quality_status.value, "VALIDATED")

        # Invariant 9/15: the other instrument genuinely has no prior of its own.
        result_a_other = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=fut_other_id,
            dataset_version_id=d_a.dataset_version_id, event_at=a_current_at, decision_at=a_decision_at,
        )
        self.assertIsNone(result_a_other)

        # Invariant 27: deterministic manifest, required categories present.
        manifest = result_a.source_observation_manifest
        for prefix in (
            "historical_dataset_version_id:", "historical_dataset_content_hash:", "source_id:",
            "instrument_id:", "current_normalized_observation_id:", "current_raw_observation_id:",
            "current_event_at:", "current_revision:", "current_ingested_at:",
            "prior_normalized_observation_id:", "prior_raw_observation_id:", "prior_event_at:",
            "prior_revision:", "prior_ingested_at:", "unit:", "unit_asset:",
        ):
            self.assertTrue(any(token.startswith(prefix) for token in manifest), prefix)
        self.assertIn("unit:CONTRACTS", manifest)
        self.assertIn("unit_asset:NULL", manifest)

        # Invariant 31: identical replay is idempotent -- same hash, one row.
        result_a_replay = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=fut_main_id,
            dataset_version_id=d_a.dataset_version_id, event_at=a_current_at, decision_at=a_decision_at,
        )
        assert result_a_replay is not None
        self.assertEqual(result_a_replay.content_hash, result_a.content_hash)
        self.assertEqual(result_a_replay.source_observation_manifest, result_a.source_observation_manifest)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT value FROM feature_materializations WHERE materialization_id=%s",
                (result_a.materialization_id,),
            )
            stored_value = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM feature_materializations WHERE feature_id=%s AND "
                "subject_type='INSTRUMENT' AND subject_id=%s AND dataset_version=%s",
                (oi_def.feature_id, fut_main_id, str(d_a.dataset_version_id)),
            )
            count = int(str(cursor.fetchone()[0]))
        self.assertEqual(count, 1)
        # Invariant 30: persisted Decimal equals the value that entered the hash.
        self.assertEqual(Decimal(str(stored_value)), result_a.value)

        # Invariant 32: same natural identity, altered value -> conflict.
        conflicting = replace(result_a, value=Decimal("1"), content_hash="f" * 64)
        with self.assertRaisesRegex(FeatureAuthorityError, "conflict"):
            feature_authority.materialize_subject(conflicting)

        # =========================================================================
        # ==== Scenario B: crypto BASE_ASSET happy path (invariants 5, 7) =========
        # =========================================================================
        b_prior_at = datetime(2025, 4, 1, 8, tzinfo=UTC)
        b_current_at = datetime(2025, 4, 2, 8, tzinfo=UTC)
        b_prior_obs = capture_oi(
            crypto_source, "CRYPTOBASE", "TESTFIX3J1BCRYPTOBASE", crypto_venue, "500",
            "BASE_ASSET", b_prior_at, unit_asset="BTC",
        )
        b_current_obs = capture_oi(
            crypto_source, "CRYPTOBASE", "TESTFIX3J1BCRYPTOBASE", crypto_venue, "650",
            "BASE_ASSET", b_current_at, unit_asset="BTC",
        )
        b_seal_at = datetime(2025, 4, 2, 9, tzinfo=UTC)
        d_b = pipeline.seal_dataset(
            crypto_source.source_id, "3j1b-dataset-crypto-base", "3j1b-v1",
            (b_prior_obs.normalized_observation_id, b_current_obs.normalized_observation_id),
            b_seal_at,
        )
        b_decision_at = datetime(2025, 4, 2, 10, tzinfo=UTC)
        result_b = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=cryp_base_id,
            dataset_version_id=d_b.dataset_version_id, event_at=b_current_at, decision_at=b_decision_at,
        )
        self.assertIsNotNone(result_b)
        assert result_b is not None
        self.assertEqual(result_b.value, Decimal("150").quantize(_VALUE_SCALE))
        self.assertEqual(result_b.subject_id, cryp_base_id)
        # Invariant 7: futures and crypto reuse the exact same feature definition.
        self.assertEqual(result_b.feature_id, result_a.feature_id)

        # =========================================================================
        # ==== Scenario C: crypto QUOTE_NOTIONAL happy path (invariant 6) ========
        # =========================================================================
        c_prior_at = datetime(2025, 4, 10, 8, tzinfo=UTC)
        c_current_at = datetime(2025, 4, 11, 8, tzinfo=UTC)
        c_prior_obs = capture_oi(
            crypto_source, "CRYPTOBASE", "TESTFIX3J1BCRYPTOBASE", crypto_venue, "10000",
            "QUOTE_NOTIONAL", c_prior_at, unit_asset="USDT",
        )
        c_current_obs = capture_oi(
            crypto_source, "CRYPTOBASE", "TESTFIX3J1BCRYPTOBASE", crypto_venue, "12500",
            "QUOTE_NOTIONAL", c_current_at, unit_asset="USDT",
        )
        c_seal_at = datetime(2025, 4, 11, 9, tzinfo=UTC)
        d_c = pipeline.seal_dataset(
            crypto_source.source_id, "3j1b-dataset-crypto-quote", "3j1b-v1",
            (c_prior_obs.normalized_observation_id, c_current_obs.normalized_observation_id),
            c_seal_at,
        )
        c_decision_at = datetime(2025, 4, 11, 10, tzinfo=UTC)
        result_c = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=cryp_base_id,
            dataset_version_id=d_c.dataset_version_id, event_at=c_current_at, decision_at=c_decision_at,
        )
        self.assertIsNotNone(result_c)
        assert result_c is not None
        self.assertEqual(result_c.value, Decimal("2500").quantize(_VALUE_SCALE))
        self.assertIn("unit:QUOTE_NOTIONAL", result_c.source_observation_manifest)
        self.assertIn("unit_asset:USDT", result_c.source_observation_manifest)

        # =========================================================================
        # ==== Scenario E: cross-dataset pair rejected (invariant 10) ============
        # =========================================================================
        e_prior_at = datetime(2025, 5, 1, 18, tzinfo=UTC)
        e_current_at = datetime(2025, 5, 2, 18, tzinfo=UTC)
        e_prior_obs = capture_oi(
            fut_source, "FUTCROSS", "TESTFIX3J1BFUTCROSS", fut_venue, "300", "CONTRACTS", e_prior_at,
        )
        e_current_obs = capture_oi(
            fut_source, "FUTCROSS", "TESTFIX3J1BFUTCROSS", fut_venue, "400", "CONTRACTS", e_current_at,
        )
        pipeline.seal_dataset(
            fut_source.source_id, "3j1b-dataset-cross-prior", "3j1b-v1",
            (e_prior_obs.normalized_observation_id,), datetime(2025, 5, 1, 19, tzinfo=UTC),
        )
        d_e_current = pipeline.seal_dataset(
            fut_source.source_id, "3j1b-dataset-cross-current", "3j1b-v1",
            (e_current_obs.normalized_observation_id,), datetime(2025, 5, 2, 19, tzinfo=UTC),
        )
        result_e = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=fut_cross_id,
            dataset_version_id=d_e_current.dataset_version_id, event_at=e_current_at,
            decision_at=datetime(2025, 5, 2, 20, tzinfo=UTC),
        )
        self.assertIsNone(result_e)

        # =========================================================================
        # ==== Invariant 11: no dataset can ever be non-SEALED -- proven directly
        # at the PostgreSQL level (CHECK constraint), the same way 3J.1a proved
        # curve-referencing invariants at the schema level. ======================
        # =========================================================================
        with self.assertRaises(Exception), database.transaction() as connection, connection.cursor() as cursor:  # noqa: B017
            cursor.execute(
                "INSERT INTO historical_dataset_versions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT')",
                (
                    uuid4(), fut_source.source_id, "3j1b-unsealed-attempt", "3j1b-v1", "0" * 64,
                    datetime(2025, 5, 1, tzinfo=UTC), None, datetime(2025, 5, 1, tzinfo=UTC),
                ),
            )

        # =========================================================================
        # ==== Invariant 12: dataset not yet knowable at decision_at =============
        # =========================================================================
        with self.assertRaisesRegex(OpenInterestFeatureError, "dataset_not_knowable_at_decision_at"):
            calculator.materialize_open_interest_change(
                feature_id=oi_def.feature_id, instrument_id=fut_main_id,
                dataset_version_id=d_a.dataset_version_id, event_at=a_current_at,
                decision_at=a_seal_at - timedelta(minutes=30),
            )

        # =========================================================================
        # ==== Invariant 13: wrong observation kind rejected ======================
        # =========================================================================
        h_event_at = datetime(2025, 3, 5, 18, tzinfo=UTC)
        h_settlement_obs = capture_and_normalize(
            fut_source, ObservationKind.SETTLEMENT_PRICE, "FUTMAIN", "TESTFIX3J1BFUTMAIN",
            fut_venue, settlement_payload(date(2025, 3, 5)), event_at=h_event_at,
        )
        d_h = pipeline.seal_dataset(
            fut_source.source_id, "3j1b-dataset-wrong-kind", "3j1b-v1",
            (h_settlement_obs.normalized_observation_id,), h_event_at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(OpenInterestFeatureError, "current_observation_not_found"):
            calculator.materialize_open_interest_change(
                feature_id=oi_def.feature_id, instrument_id=fut_main_id,
                dataset_version_id=d_h.dataset_version_id, event_at=h_event_at,
                decision_at=h_event_at + timedelta(hours=2),
            )

        # =========================================================================
        # ==== Invariant 14: missing/invalid typed OI payload fails upstream ======
        # =========================================================================
        with self.assertRaises(HistoricalDataQualityError):
            capture_oi(
                fut_source, "FUTMAIN", "TESTFIX3J1BFUTMAIN", fut_venue, "-1", "CONTRACTS",
                datetime(2025, 3, 6, 18, tzinfo=UTC),
            )

        # =========================================================================
        # ==== Scenario K: revision resolution (invariants 16, 17, 28) ===========
        #
        # A sealed dataset's own created_at must be >= every member's own
        # ingested_at/normalized_at (seal_dataset's own invariant), so once a
        # dataset is knowable at decision_at, every one of its members is too --
        # a per-row PIT gate can never differ from the dataset-level one *within
        # a single sealed dataset*. So "a revision learned after decision_at
        # cannot leak" is proven the same way 3J.1a's own suite proves the
        # analogous invariant: two independently-sealed dataset identities, one
        # that predates revision 1's existence and one that postdates it --
        # never one dataset queried at two different decision_at values.
        # =========================================================================
        k_prior_at = datetime(2025, 6, 1, 18, tzinfo=UTC)
        k_current_at = datetime(2025, 6, 2, 18, tzinfo=UTC)
        k_rev1_ingested_at = k_current_at + timedelta(days=2)
        k_prior_obs = capture_oi(
            fut_source, "FUTREV", "TESTFIX3J1BFUTREV", fut_venue, "1000", "CONTRACTS", k_prior_at,
        )
        k_rev0_obs = capture_oi(
            fut_source, "FUTREV", "TESTFIX3J1BFUTREV", fut_venue, "1300", "CONTRACTS", k_current_at,
            revision=0,
        )
        k_rev1_obs = capture_oi(
            fut_source, "FUTREV", "TESTFIX3J1BFUTREV", fut_venue, "1350", "CONTRACTS", k_current_at,
            ingested_at=k_rev1_ingested_at, normalized_at=k_rev1_ingested_at, revision=1,
        )
        # Sealed immediately after revision 0 -- revision 1 does not exist yet.
        d_k_v0_seal_at = k_current_at + timedelta(hours=1)
        d_k_v0 = pipeline.seal_dataset(
            fut_source.source_id, "3j1b-dataset-revision-v0", "3j1b-v1",
            (k_prior_obs.normalized_observation_id, k_rev0_obs.normalized_observation_id),
            d_k_v0_seal_at,
        )
        # Sealed only after revision 1 has actually been ingested.
        d_k_v1_seal_at = k_rev1_ingested_at + timedelta(hours=1)
        d_k_v1 = pipeline.seal_dataset(
            fut_source.source_id, "3j1b-dataset-revision-v1", "3j1b-v1",
            (
                k_prior_obs.normalized_observation_id, k_rev0_obs.normalized_observation_id,
                k_rev1_obs.normalized_observation_id,
            ),
            d_k_v1_seal_at,
        )
        # Invariant 17: at this decision point revision 1 does not exist in any
        # knowable dataset yet, so only revision 0's value can ever be produced.
        result_k_early = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=fut_rev_id,
            dataset_version_id=d_k_v0.dataset_version_id, event_at=k_current_at,
            decision_at=d_k_v0_seal_at + timedelta(minutes=30),
        )
        self.assertIsNotNone(result_k_early)
        assert result_k_early is not None
        self.assertEqual(result_k_early.value, Decimal("300").quantize(_VALUE_SCALE))
        self.assertIn("current_revision:0", result_k_early.source_observation_manifest)
        with self.assertRaisesRegex(OpenInterestFeatureError, "dataset_not_knowable_at_decision_at"):
            calculator.materialize_open_interest_change(
                feature_id=oi_def.feature_id, instrument_id=fut_rev_id,
                dataset_version_id=d_k_v1.dataset_version_id, event_at=k_current_at,
                decision_at=d_k_v0_seal_at + timedelta(minutes=30),
            )

        # Invariant 16: once knowable, the highest PIT-visible revision wins.
        result_k_late = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=fut_rev_id,
            dataset_version_id=d_k_v1.dataset_version_id, event_at=k_current_at,
            decision_at=d_k_v1_seal_at + timedelta(minutes=30),
        )
        self.assertIsNotNone(result_k_late)
        assert result_k_late is not None
        self.assertEqual(result_k_late.value, Decimal("350").quantize(_VALUE_SCALE))
        self.assertIn("current_revision:1", result_k_late.source_observation_manifest)

        # Invariant 28: a changed revision changes provenance/content identity.
        self.assertNotEqual(result_k_early.content_hash, result_k_late.content_hash)
        self.assertNotEqual(
            result_k_early.source_observation_manifest, result_k_late.source_observation_manifest
        )
        self.assertNotEqual(result_k_early.dataset_version, result_k_late.dataset_version)

        # =========================================================================
        # ==== Scenario L: most recent eligible prior chosen; future event never
        # selected as prior (invariants 18, 19) ===================================
        # =========================================================================
        l_t0 = datetime(2025, 7, 1, 18, tzinfo=UTC)
        l_t1 = datetime(2025, 7, 2, 18, tzinfo=UTC)
        l_t2 = datetime(2025, 7, 3, 18, tzinfo=UTC)
        l_t3 = datetime(2025, 7, 4, 18, tzinfo=UTC)
        l_obs0 = capture_oi(fut_source, "FUTSEQ", "TESTFIX3J1BFUTSEQ", fut_venue, "100", "CONTRACTS", l_t0)
        l_obs1 = capture_oi(fut_source, "FUTSEQ", "TESTFIX3J1BFUTSEQ", fut_venue, "150", "CONTRACTS", l_t1)
        l_obs2 = capture_oi(fut_source, "FUTSEQ", "TESTFIX3J1BFUTSEQ", fut_venue, "210", "CONTRACTS", l_t2)
        l_obs3 = capture_oi(
            fut_source, "FUTSEQ", "TESTFIX3J1BFUTSEQ", fut_venue, "999999", "CONTRACTS", l_t3,
        )
        l_seal_at = l_t3 + timedelta(hours=1)
        d_l = pipeline.seal_dataset(
            fut_source.source_id, "3j1b-dataset-sequence", "3j1b-v1",
            (
                l_obs0.normalized_observation_id, l_obs1.normalized_observation_id,
                l_obs2.normalized_observation_id, l_obs3.normalized_observation_id,
            ),
            l_seal_at,
        )
        result_l = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=fut_seq_id,
            dataset_version_id=d_l.dataset_version_id, event_at=l_t2, decision_at=l_seal_at + timedelta(hours=1),
        )
        self.assertIsNotNone(result_l)
        assert result_l is not None
        self.assertEqual(result_l.value, Decimal("60").quantize(_VALUE_SCALE))  # 210 - 150, never 210-100 or future
        self.assertIn(f"prior_event_at:{l_t1.isoformat()}", result_l.source_observation_manifest)

        # =========================================================================
        # ==== Scenario M1: CONTRACTS vs BASE_ASSET mismatch (invariant 20) ======
        # =========================================================================
        m1_prior_at = datetime(2025, 8, 1, 8, tzinfo=UTC)
        m1_current_at = datetime(2025, 8, 2, 8, tzinfo=UTC)
        m1_prior_obs = capture_oi(
            crypto_source, "CRYPTOMM", "TESTFIX3J1BCRYPTOMM", crypto_venue_mm, "500", "BASE_ASSET",
            m1_prior_at, unit_asset="BTC",
        )
        m1_current_obs = capture_oi(
            crypto_source, "CRYPTOMM", "TESTFIX3J1BCRYPTOMM", crypto_venue_mm, "10", "CONTRACTS",
            m1_current_at,
        )
        d_m1 = pipeline.seal_dataset(
            crypto_source.source_id, "3j1b-dataset-unit-mismatch-contracts-base", "3j1b-v1",
            (m1_prior_obs.normalized_observation_id, m1_current_obs.normalized_observation_id),
            m1_current_at + timedelta(hours=1),
        )
        result_m1 = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=cryp_mismatch_id,
            dataset_version_id=d_m1.dataset_version_id, event_at=m1_current_at,
            decision_at=m1_current_at + timedelta(hours=2),
        )
        self.assertIsNone(result_m1)

        # =========================================================================
        # ==== Scenario M2: BASE_ASSET vs QUOTE_NOTIONAL mismatch (invariant 21) =
        # =========================================================================
        m2_prior_at = datetime(2025, 8, 10, 8, tzinfo=UTC)
        m2_current_at = datetime(2025, 8, 11, 8, tzinfo=UTC)
        m2_prior_obs = capture_oi(
            crypto_source, "CRYPTOMM", "TESTFIX3J1BCRYPTOMM", crypto_venue_mm, "500", "BASE_ASSET",
            m2_prior_at, unit_asset="BTC",
        )
        m2_current_obs = capture_oi(
            crypto_source, "CRYPTOMM", "TESTFIX3J1BCRYPTOMM", crypto_venue_mm, "20000", "QUOTE_NOTIONAL",
            m2_current_at, unit_asset="USDT",
        )
        d_m2 = pipeline.seal_dataset(
            crypto_source.source_id, "3j1b-dataset-unit-mismatch-base-quote", "3j1b-v1",
            (m2_prior_obs.normalized_observation_id, m2_current_obs.normalized_observation_id),
            m2_current_at + timedelta(hours=1),
        )
        result_m2 = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=cryp_mismatch_id,
            dataset_version_id=d_m2.dataset_version_id, event_at=m2_current_at,
            decision_at=m2_current_at + timedelta(hours=2),
        )
        self.assertIsNone(result_m2)

        # =========================================================================
        # ==== Scenario M3: same unit, different unit_asset (invariant 22) =======
        # This pairing cannot occur through the normal validated pipeline for one
        # instrument (validate_crypto_open_interest ties unit_asset deterministically
        # to the instrument's own base/quote asset), so it is constructed directly
        # at the historical evidence layer -- the same "prove the edge case
        # directly" technique 3J.1a's own Postgres suite uses for schema-level
        # invariants that the normal API path structurally prevents.
        # =========================================================================
        m3_prior_at = datetime(2025, 8, 20, 8, tzinfo=UTC)
        m3_current_at = datetime(2025, 8, 21, 8, tzinfo=UTC)

        def insert_raw_oi_bypassing_validation(
            *, source_id: object, identifier: str, event_at: datetime, value: str, unit: str,
            unit_asset: str | None,
        ) -> tuple[object, object]:
            raw_id = uuid4()
            normalized_id = uuid4()
            payload = {"open_interest": value, "unit": unit, "observed_at": event_at.isoformat()}
            if unit_asset is not None:
                payload["unit_asset"] = unit_asset
            payload_json = json.dumps(payload, sort_keys=True)
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO historical_raw_observations VALUES "
                    "(%s,%s,'OPEN_INTEREST',%s,%s,%s,%s,%s,%s,'AS_REPORTED',0,%s,%s::jsonb,%s)",
                    (
                        raw_id, source_id, identifier, "TESTFIX3J1BCRYPTOMM", crypto_venue_mm,
                        event_at, event_at, event_at,
                        f"fixture://bypass/{identifier}/{event_at.isoformat()}", payload_json,
                        hashlib.sha256(payload_json.encode()).hexdigest(),
                    ),
                )
                cursor.execute(
                    "INSERT INTO historical_normalized_observations VALUES "
                    "(%s,%s,%s,'3j1b-v1',%s::jsonb,'VALIDATED','[]'::jsonb,%s)",
                    (
                        normalized_id, raw_id, cryp_mismatch_id,
                        json.dumps({"canonical_payload_table": "futures_open_interest_observations"}),
                        event_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO open_interest_observations VALUES (%s,%s,%s,%s,%s)",
                    (normalized_id, Decimal(value), unit, unit_asset, event_at),
                )
            return raw_id, normalized_id

        _m3_prior_raw, m3_prior_normalized = insert_raw_oi_bypassing_validation(
            source_id=crypto_source.source_id, identifier="CRYPTOMM-BYPASS-PRIOR",
            event_at=m3_prior_at, value="500", unit="BASE_ASSET", unit_asset="BTC",
        )
        _m3_current_raw, m3_current_normalized = insert_raw_oi_bypassing_validation(
            source_id=crypto_source.source_id, identifier="CRYPTOMM-BYPASS-CURRENT",
            event_at=m3_current_at, value="8", unit="BASE_ASSET", unit_asset="ETH",
        )
        d_m3 = pipeline.seal_dataset(
            crypto_source.source_id, "3j1b-dataset-unit-asset-mismatch", "3j1b-v1",
            (m3_prior_normalized, m3_current_normalized), m3_current_at + timedelta(hours=1),
        )
        result_m3 = calculator.materialize_open_interest_change(
            feature_id=oi_def.feature_id, instrument_id=cryp_mismatch_id,
            dataset_version_id=d_m3.dataset_version_id, event_at=m3_current_at,
            decision_at=m3_current_at + timedelta(hours=2),
        )
        self.assertIsNone(result_m3)

        # =========================================================================
        # ==== Scenario N1: ambiguous current observation identity (invariant 24) =
        # =========================================================================
        n1_event_at = datetime(2025, 9, 1, 18, tzinfo=UTC)
        n1_obs_a = capture_oi(
            fut_source, "AMBIGA", "TESTFIX3J1BFUTAMBIG", fut_venue, "100", "CONTRACTS", n1_event_at,
        )
        n1_obs_b = capture_oi(
            fut_source, "AMBIGB", "TESTFIX3J1BFUTAMBIG", fut_venue, "105", "CONTRACTS", n1_event_at,
        )
        d_n1 = pipeline.seal_dataset(
            fut_source.source_id, "3j1b-dataset-ambiguous-current", "3j1b-v1",
            (n1_obs_a.normalized_observation_id, n1_obs_b.normalized_observation_id),
            n1_event_at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(OpenInterestFeatureError, "ambiguous_current_observation_identity"):
            calculator.materialize_open_interest_change(
                feature_id=oi_def.feature_id, instrument_id=fut_ambig_id,
                dataset_version_id=d_n1.dataset_version_id, event_at=n1_event_at,
                decision_at=n1_event_at + timedelta(hours=2),
            )

        # =========================================================================
        # ==== Scenario N2: ambiguous prior observation identity (invariant 25) ==
        # =========================================================================
        n2_prior_at = datetime(2025, 9, 4, 18, tzinfo=UTC)
        n2_current_at = datetime(2025, 9, 5, 18, tzinfo=UTC)
        n2_prior_c = capture_oi(
            fut_source, "AMBIGC", "TESTFIX3J1BFUTAMBIG2", fut_venue, "200", "CONTRACTS", n2_prior_at,
        )
        n2_prior_d = capture_oi(
            fut_source, "AMBIGD", "TESTFIX3J1BFUTAMBIG2", fut_venue, "210", "CONTRACTS", n2_prior_at,
        )
        n2_current = capture_oi(
            fut_source, "AMBIGC", "TESTFIX3J1BFUTAMBIG2", fut_venue, "500", "CONTRACTS", n2_current_at,
        )
        d_n2 = pipeline.seal_dataset(
            fut_source.source_id, "3j1b-dataset-ambiguous-prior", "3j1b-v1",
            (
                n2_prior_c.normalized_observation_id, n2_prior_d.normalized_observation_id,
                n2_current.normalized_observation_id,
            ),
            n2_current_at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(OpenInterestFeatureError, "ambiguous_prior_observation_identity"):
            calculator.materialize_open_interest_change(
                feature_id=oi_def.feature_id, instrument_id=fut_ambig2_id,
                dataset_version_id=d_n2.dataset_version_id, event_at=n2_current_at,
                decision_at=n2_current_at + timedelta(hours=2),
            )

        # =========================================================================
        # ==== Invariant 33: immutable evidence rejects UPDATE/DELETE =============
        # =========================================================================
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE feature_materializations SET value=0 WHERE materialization_id=%s",
                (result_a.materialization_id,),
            )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM feature_materializations WHERE materialization_id=%s",
                (result_a.materialization_id,),
            )

        # =========================================================================
        # ==== Invariant 34: restart preserves the materialization ===============
        # =========================================================================
        far_future_decision_at = result_a.knowledge_at + timedelta(days=365)
        database.close()
        reopened = PostgresDatabase(dsn)
        reopened_authority = PostgresFeatureAuthority(reopened)
        self.assertEqual(
            reopened_authority.latest_as_of_subject(
                oi_def.feature_id, FeatureSubjectType.INSTRUMENT, fut_main_id,
                str(d_a.dataset_version_id), far_future_decision_at,
            ),
            (result_a,),
        )

        # =========================================================================
        # ==== Invariants 36/37: no new table/store/migration introduced ==========
        # =========================================================================
        with reopened.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name LIKE %s",
                ("%feature_material%",),
            )
            self.assertEqual([str(row[0]) for row in cursor.fetchall()], ["feature_materializations"])
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                "AND (table_name LIKE %s OR table_name LIKE %s)",
                ("%open_interest_feature%", "%3j1b%"),
            )
            self.assertEqual(cursor.fetchall(), [])
        reopened.close()

        # Sanity: the exact facts this module claims, still true at the end.
        self.assertEqual(oi_def.units, "native_open_interest_unit")
        self.assertEqual(oi_def.name, OPEN_INTEREST_CHANGE)
        self.assertEqual(FeatureFamily.DERIVATIVES.value, "DERIVATIVES")


if __name__ == "__main__":
    unittest.main()
