"""Real PostgreSQL evidence for Module 3J.1c (Crypto Basis & Funding Feature Pack).

Fixture instruments use the ``TESTFIXTURE:3J1C:`` prefix so they can never
displace real records on the shared CI database. Every mark price, index
price, funding rate, provider identifier and timestamp is a FIXTURE; nothing
here was retrieved from or verified against any crypto venue, and no alpha
claim is made.
"""

import hashlib
import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

_VALUE_SCALE = Decimal("1E-12")
_ANNUALIZATION_YEAR_SECONDS = 365 * 24 * 60 * 60


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class CryptoDerivativesFeaturesPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_crypto_derivatives_features_end_to_end(self) -> None:
        from trade_platform.crypto_derivatives_features import (
            CRYPTO_FUNDING_FORECAST_ERROR,
            CRYPTO_MARK_INDEX_BASIS,
            CRYPTO_REALIZED_FUNDING_ANNUALIZED,
            CryptoDerivativesFeatureError,
            PostgresCryptoDerivativesFeatureCalculator,
            crypto_funding_forecast_error_definition,
            crypto_mark_index_basis_definition,
            crypto_realized_funding_annualized_definition,
        )
        from trade_platform.crypto_instruments import (
            CryptoFundingConvention,
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
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            AssetScope,
            AuthorizedHistoricalSource,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            RawHistoricalObservation,
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
        crypto = PostgresCryptoInstrumentAuthority(database)
        pipeline = PostgresHistoricalMarketDataPipeline(database)
        feature_authority = PostgresFeatureAuthority(database)
        calculator = PostgresCryptoDerivativesFeatureCalculator(database)

        registered_at = datetime(2025, 1, 2, tzinfo=UTC)
        namespace = "TESTFIX_3J1C_CRYPTO_PROVIDER"
        _venue_counter = iter(range(1, 200))

        def next_venue() -> str:
            return f"TESTFIXCEX3J1C{next(_venue_counter):03d}"

        # ---- instrument fixtures -----------------------------------------------
        def add_identifier(instrument_id: str, provider_identifier: str) -> None:
            master.add_identifier_mapping(
                IdentifierMapping(
                    instrument_id=instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
                    namespace=namespace, value=provider_identifier, valid_from=registered_at,
                    valid_until=None, ingested_at=registered_at,
                    source_reference="fixture:provider-identifier",
                )
            )

        venue_by_symbol: dict[str, str] = {}

        def register_perpetual(suffix: str) -> str:
            venue = next_venue()
            instrument_id = f"TESTFIXTURE:3J1C:{suffix}"
            symbol = f"TESTFIX3J1C{suffix}"
            venue_by_symbol[symbol] = venue
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id, asset_class=AssetClass.CRYPTO,
                    instrument_type=InstrumentType.CRYPTO_PERPETUAL, exchange_name=venue,
                    venue=venue, mic=None, canonical_symbol=symbol, listing_date=date(2024, 1, 2),
                    base_currency="BTC", quote_currency="USDT", settlement_currency="USDT",
                    contract_multiplier=Decimal(1), contract_size=Decimal(1),
                    tick_size=Decimal("0.01"), lot_size=Decimal("0.00001"),
                    price_precision=2, quantity_precision=5, trading_timezone="UTC",
                    market_session_type=SessionType.CRYPTO_24X7,
                    representation_kind=RepresentationKind.PERPETUAL, registered_at=registered_at,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                )
            )
            crypto.specify_instrument(
                CryptoInstrumentSpecification(
                    instrument_id=instrument_id, venue=venue, kind=CryptoInstrumentKind.PERPETUAL,
                    base_asset="BTC", quote_asset="USDT", settlement_asset="USDT",
                    settlement_style=SettlementStyle.LINEAR,
                    settlement_type=CryptoSettlementType.CASH_SETTLED,
                    contract_multiplier=Decimal(1), contract_size=Decimal(1),
                    reference_price_requirement=ReferencePriceRequirement.MARK_AND_INDEX,
                    index_reference=f"TESTFIX_3J1C_{suffix}_INDEX", registered_at=registered_at,
                    source_reference="fixture:crypto-specification",
                )
            )
            add_identifier(instrument_id, suffix)
            return instrument_id

        def register_spot(suffix: str) -> str:
            venue = next_venue()
            instrument_id = f"TESTFIXTURE:3J1C:{suffix}"
            symbol = f"TESTFIX3J1C{suffix}"
            venue_by_symbol[symbol] = venue
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id, asset_class=AssetClass.CRYPTO,
                    instrument_type=InstrumentType.SPOT_CRYPTO, exchange_name=venue,
                    venue=venue, mic=None, canonical_symbol=symbol, listing_date=date(2024, 1, 2),
                    base_currency="BTC", quote_currency="USDT", settlement_currency="USDT",
                    contract_multiplier=Decimal(1), contract_size=Decimal(1),
                    tick_size=Decimal("0.01"), lot_size=Decimal("0.00001"),
                    price_precision=2, quantity_precision=5, trading_timezone="UTC",
                    market_session_type=SessionType.CRYPTO_24X7,
                    representation_kind=RepresentationKind.SPOT, registered_at=registered_at,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                )
            )
            crypto.specify_instrument(
                CryptoInstrumentSpecification(
                    instrument_id=instrument_id, venue=venue, kind=CryptoInstrumentKind.SPOT,
                    base_asset="BTC", quote_asset="USDT", settlement_asset=None,
                    settlement_style=None, settlement_type=CryptoSettlementType.PHYSICAL_DELIVERY,
                    contract_multiplier=Decimal(1), contract_size=Decimal(1),
                    reference_price_requirement=ReferencePriceRequirement.NONE,
                    index_reference=None, registered_at=registered_at,
                    source_reference="fixture:crypto-specification",
                )
            )
            add_identifier(instrument_id, suffix)
            return instrument_id

        def register_convention(
            instrument_id: str, *, version: int = 1, interval_hours: Decimal = Decimal(8),
            known_at: datetime = registered_at, effective_from: datetime = registered_at,
            settlement_asset: str = "USDT",
        ) -> "CryptoFundingConvention":
            convention = CryptoFundingConvention(
                instrument_id=instrument_id, convention_version=version,
                interval_hours=interval_hours, first_funding_offset_hours=Decimal(0),
                funding_settlement_asset=settlement_asset, effective_from=effective_from,
                known_at=known_at, source_reference=f"fixture:convention:{instrument_id}:{version}",
                source_hash="a" * 64,
            )
            crypto.register_funding_convention(convention)
            return convention

        # ---- source authorization ------------------------------------------------
        crypto_source = AuthorizedHistoricalSource(
            provider="TESTFIX_3J1C_CRYPTO", dataset_name="3j1c-fixture",
            provider_identifier_namespace=namespace, provider_terms_version="v1",
            authorization_reference="fixture://authorization/3j1c",
            authorized_at=registered_at, created_at=registered_at, asset_scope=AssetScope.CRYPTO.value,
            authorized_observation_kinds=frozenset({
                ObservationKind.MARK_PRICE, ObservationKind.INDEX_PRICE,
                ObservationKind.FUNDING_RATE_REALIZED, ObservationKind.FUNDING_RATE_INDICATIVE,
            }),
        )
        pipeline.register_source(crypto_source)

        # ---- payload / capture helpers ---------------------------------------------
        def capture_and_normalize(
            kind: "ObservationKind", identifier: str, symbol: str, venue: str,
            payload: dict[str, object], *, event_at: datetime, ingested_at: datetime | None = None,
            normalized_at: datetime | None = None, revision: int = 0,
        ) -> object:
            # The caller-passed `venue` is a placeholder -- the instrument's real
            # registered venue (keyed by its unique symbol) is what the pipeline's
            # own exchange/instrument coherence check requires.
            resolved_venue = venue_by_symbol.get(symbol, venue)
            resolved_ingested_at = ingested_at or event_at
            (raw_id,) = pipeline.capture_raw(
                [
                    RawHistoricalObservation(
                        source_id=crypto_source.source_id, observation_kind=kind,
                        provider_identifier=identifier, provider_symbol=symbol, exchange=resolved_venue,
                        event_at=event_at, effective_at=event_at, ingested_at=resolved_ingested_at,
                        adjustment_status=AdjustmentStatus.AS_REPORTED, revision=revision,
                        provenance_uri=f"fixture://{kind.value}/{identifier}/{revision}/{event_at.isoformat()}",
                        raw_payload=payload,
                    )
                ]
            )
            return pipeline.normalize(raw_id, "3j1c-v1", normalized_at or resolved_ingested_at)

        def capture_mark(
            identifier: str, symbol: str, venue: str, price: str, event_at: datetime, **kwargs: object,
        ) -> object:
            payload = {"price": price, "price_asset": "USDT", "observed_at": event_at.isoformat()}
            return capture_and_normalize(
                ObservationKind.MARK_PRICE, identifier, symbol, venue, payload,
                event_at=event_at, **kwargs,  # type: ignore[arg-type]
            )

        def capture_index(
            identifier: str, symbol: str, venue: str, price: str, event_at: datetime, **kwargs: object,
        ) -> object:
            payload = {"price": price, "price_asset": "USDT", "observed_at": event_at.isoformat()}
            return capture_and_normalize(
                ObservationKind.INDEX_PRICE, identifier, symbol, venue, payload,
                event_at=event_at, **kwargs,  # type: ignore[arg-type]
            )

        def capture_realized(
            identifier: str, symbol: str, venue: str, rate: str, target_at: datetime,
            *, published_at: datetime | None = None, settlement_asset: str = "USDT", **kwargs: object,
        ) -> object:
            payload = {
                "funding_rate": rate, "target_funding_at": target_at.isoformat(),
                "published_at": (published_at or target_at).isoformat(),
                "settlement_asset": settlement_asset,
            }
            return capture_and_normalize(
                ObservationKind.FUNDING_RATE_REALIZED, identifier, symbol, venue, payload,
                event_at=target_at, **kwargs,  # type: ignore[arg-type]
            )

        def capture_indicative(
            identifier: str, symbol: str, venue: str, rate: str, target_at: datetime,
            published_at: datetime, *, settlement_asset: str = "USDT", **kwargs: object,
        ) -> object:
            payload = {
                "funding_rate": rate, "target_funding_at": target_at.isoformat(),
                "published_at": published_at.isoformat(), "settlement_asset": settlement_asset,
            }
            return capture_and_normalize(
                ObservationKind.FUNDING_RATE_INDICATIVE, identifier, symbol, venue, payload,
                event_at=published_at, **kwargs,  # type: ignore[arg-type]
            )

        # ---- raw-evidence bypass helpers (construct edge cases the validated
        # pipeline structurally refuses, same technique 3J.1b's own suite uses) ----
        def bypass_reference_price(
            *, instrument_id: str, kind: "ObservationKind", identifier: str, event_at: datetime,
            price: str, price_asset: str, revision: int = 0,
        ) -> UUID:
            raw_id, normalized_id = uuid4(), uuid4()
            payload = {"price": price, "price_asset": price_asset, "observed_at": event_at.isoformat()}
            payload_json = json.dumps(payload, sort_keys=True)
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO historical_raw_observations VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,'AS_REPORTED',%s,%s,%s::jsonb,%s)",
                    (
                        raw_id, crypto_source.source_id, kind.value, identifier, identifier,
                        "TESTFIXCEX3J1CBYPASS", event_at, event_at, event_at, revision,
                        f"fixture://bypass/{kind.value}/{identifier}/{revision}/{event_at.isoformat()}",
                        payload_json, hashlib.sha256(payload_json.encode()).hexdigest(),
                    ),
                )
                cursor.execute(
                    "INSERT INTO historical_normalized_observations VALUES "
                    "(%s,%s,%s,'3j1c-v1',%s::jsonb,'VALIDATED','[]'::jsonb,%s)",
                    (
                        normalized_id, raw_id, instrument_id,
                        json.dumps({"canonical_payload_table": "crypto_reference_price_observations"}),
                        event_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO crypto_reference_price_observations VALUES (%s,%s,%s,%s,NULL)",
                    (normalized_id, Decimal(price), price_asset, event_at),
                )
            return normalized_id

        def bypass_funding(
            *, instrument_id: str, kind: "ObservationKind", identifier: str, event_at: datetime,
            funding_rate: str, target_funding_at: datetime, published_at: datetime,
            settlement_asset: str, convention_id: UUID, convention_version: int,
            revision: int = 0,
        ) -> UUID:
            raw_id, normalized_id = uuid4(), uuid4()
            payload = {
                "funding_rate": funding_rate, "target_funding_at": target_funding_at.isoformat(),
                "published_at": published_at.isoformat(), "settlement_asset": settlement_asset,
            }
            payload_json = json.dumps(payload, sort_keys=True)
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO historical_raw_observations VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,'AS_REPORTED',%s,%s,%s::jsonb,%s)",
                    (
                        raw_id, crypto_source.source_id, kind.value, identifier, identifier,
                        "TESTFIXCEX3J1CBYPASS", event_at, event_at, event_at, revision,
                        f"fixture://bypass/{kind.value}/{identifier}/{revision}/{event_at.isoformat()}",
                        payload_json, hashlib.sha256(payload_json.encode()).hexdigest(),
                    ),
                )
                cursor.execute(
                    "INSERT INTO historical_normalized_observations VALUES "
                    "(%s,%s,%s,'3j1c-v1',%s::jsonb,'VALIDATED','[]'::jsonb,%s)",
                    (
                        normalized_id, raw_id, instrument_id,
                        json.dumps({"canonical_payload_table": "crypto_funding_observations"}),
                        event_at,
                    ),
                )
                cursor.execute(
                    "INSERT INTO crypto_funding_observations VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        normalized_id, Decimal(funding_rate), target_funding_at, published_at,
                        settlement_asset, convention_id, convention_version,
                    ),
                )
            return normalized_id

        # =========================================================================
        # ---- register the three 3J.1c feature definitions (invariants 1, 2, 3) --
        # =========================================================================
        mib_def = crypto_mark_index_basis_definition(registered_at)
        fund_def = crypto_realized_funding_annualized_definition(registered_at)
        fcast_def = crypto_funding_forecast_error_definition(registered_at)
        feature_authority.register(mib_def)
        feature_authority.register(fund_def)
        feature_authority.register(fcast_def)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT name, family FROM feature_definition_versions WHERE name IN (%s,%s,%s)",
                (CRYPTO_MARK_INDEX_BASIS, CRYPTO_REALIZED_FUNDING_ANNUALIZED, CRYPTO_FUNDING_FORECAST_ERROR),
            )
            rows = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
        self.assertEqual(len(rows), 3)  # invariant 1
        self.assertTrue(all(family == "DERIVATIVES" for family in rows.values()))  # invariant 2
        self.assertEqual(mib_def.parameters, {})
        self.assertEqual(fund_def.parameters["annualization_basis"], "ACT_365_FIXED")  # invariant 27
        self.assertEqual(fund_def.parameters["annualization_year_seconds"], _ANNUALIZATION_YEAR_SECONDS)

        # =========================================================================
        # ==== MARK/INDEX BASIS -- Scenario A: happy path (invariants 4, 18, 43,
        # 46, 47, 48, 49) ==========================================================
        # =========================================================================
        mib_happy_id = register_perpetual("MIBHAPPY")
        a_event_at = datetime(2025, 3, 1, 8, tzinfo=UTC)
        a_mark = capture_mark("MIBHAPPY", "TESTFIX3J1CMIBHAPPY", "IGNORED", "58234.123456789012345670", a_event_at)
        a_index = capture_index("MIBHAPPY", "TESTFIX3J1CMIBHAPPY", "IGNORED", "58200.000000000000000001", a_event_at)
        a_seal_at = a_event_at + timedelta(minutes=30)
        d_a = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-mib-happy", "3j1c-v1",
            (a_mark.normalized_observation_id, a_index.normalized_observation_id), a_seal_at,
        )
        a_decision_at = a_seal_at + timedelta(minutes=30)
        result_mib = calculator.materialize_crypto_mark_index_basis(
            feature_id=mib_def.feature_id, instrument_id=mib_happy_id,
            dataset_version_id=d_a.dataset_version_id, event_at=a_event_at, decision_at=a_decision_at,
        )
        self.assertIsNotNone(result_mib)
        assert result_mib is not None
        self.assertEqual(result_mib.subject_type, FeatureSubjectType.INSTRUMENT)  # invariant 3
        self.assertEqual(result_mib.subject_id, mib_happy_id)
        self.assertEqual(result_mib.dataset_version, str(d_a.dataset_version_id))
        expected_basis = (
            (Decimal("58234.123456789012345670") - Decimal("58200.000000000000000001"))
            / Decimal("58200.000000000000000001")
        ).quantize(_VALUE_SCALE)
        self.assertEqual(result_mib.value, expected_basis)  # invariant 18: quantized before write
        self.assertEqual(result_mib.event_at, a_event_at)
        self.assertEqual(result_mib.effective_at, a_event_at)
        self.assertEqual(result_mib.knowledge_at, d_a.created_at)
        self.assertEqual(result_mib.quality_status.value, "VALIDATED")
        for prefix in (  # invariant 43
            "historical_dataset_version_id:", "historical_dataset_content_hash:", "source_id:",
            "instrument_id:", "mark_normalized_observation_id:", "mark_raw_observation_id:",
            "mark_event_at:", "mark_revision:", "mark_ingested_at:",
            "index_normalized_observation_id:", "index_raw_observation_id:", "index_event_at:",
            "index_revision:", "index_ingested_at:", "price_asset:USDT",
        ):
            self.assertTrue(any(token.startswith(prefix) for token in result_mib.source_observation_manifest), prefix)

        # invariant 47: identical replay idempotent.
        replay_mib = calculator.materialize_crypto_mark_index_basis(
            feature_id=mib_def.feature_id, instrument_id=mib_happy_id,
            dataset_version_id=d_a.dataset_version_id, event_at=a_event_at, decision_at=a_decision_at,
        )
        assert replay_mib is not None
        self.assertEqual(replay_mib.content_hash, result_mib.content_hash)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT value FROM feature_materializations WHERE materialization_id=%s",
                (result_mib.materialization_id,),
            )
            stored_mib_value = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM feature_materializations WHERE feature_id=%s AND "
                "subject_type='INSTRUMENT' AND subject_id=%s AND dataset_version=%s",
                (mib_def.feature_id, mib_happy_id, str(d_a.dataset_version_id)),
            )
            count = int(str(cursor.fetchone()[0]))
        self.assertEqual(count, 1)
        self.assertEqual(Decimal(str(stored_mib_value)), result_mib.value)  # invariant 46

        # invariant 48: same natural identity, altered content -> conflict.
        conflicting_mib = replace(result_mib, value=Decimal("1"), content_hash="f" * 64)
        with self.assertRaisesRegex(FeatureAuthorityError, "conflict"):
            feature_authority.materialize_subject(conflicting_mib)

        # invariant 49: immutable evidence rejects UPDATE/DELETE.
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection, connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE feature_materializations SET value=0 WHERE materialization_id=%s",
                (result_mib.materialization_id,),
            )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection, connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM feature_materializations WHERE materialization_id=%s",
                (result_mib.materialization_id,),
            )

        # =========================================================================
        # ==== MARK/INDEX BASIS -- Scenario B: mismatch group (invariants 5, 6, 7,
        # 8, 9) =====================================================================
        # =========================================================================
        mib_mismatch_id = register_perpetual("MIBMISMATCH")
        b_t1 = datetime(2025, 3, 5, 8, tzinfo=UTC)
        b_t2 = datetime(2025, 3, 5, 16, tzinfo=UTC)
        b_t3 = datetime(2025, 3, 6, 8, tzinfo=UTC)
        b_t4 = datetime(2025, 3, 7, 8, tzinfo=UTC)
        b_mark_t1 = capture_mark("MIBMISMATCH", "TESTFIX3J1CMIBMISMATCH", "IGNORED", "100", b_t1)
        b_index_t2 = capture_index("MIBMISMATCH", "TESTFIX3J1CMIBMISMATCH", "IGNORED", "99", b_t2)
        b_mark_t3 = capture_mark("MIBMISMATCH", "TESTFIX3J1CMIBMISMATCH", "IGNORED", "101", b_t3)
        b_index_t4 = capture_index("MIBMISMATCH", "TESTFIX3J1CMIBMISMATCH", "IGNORED", "102", b_t4)
        d_b = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-mib-mismatch", "3j1c-v1",
            (
                b_mark_t1.normalized_observation_id, b_index_t2.normalized_observation_id,
                b_mark_t3.normalized_observation_id, b_index_t4.normalized_observation_id,
            ),
            b_t4 + timedelta(hours=1),
        )
        b_decision_at = b_t4 + timedelta(hours=2)
        # invariant 5: both exist, but not at the same instant -> no row. Also
        # covers invariant 8 ("mark vs mark impossible") -- there is simply no
        # index at b_t1 for any pairing to form.
        self.assertIsNone(
            calculator.materialize_crypto_mark_index_basis(
                feature_id=mib_def.feature_id, instrument_id=mib_mismatch_id,
                dataset_version_id=d_b.dataset_version_id, event_at=b_t1, decision_at=b_decision_at,
            )
        )
        # invariant 6: mark exists, no index at all near b_t3 -> no row.
        self.assertIsNone(
            calculator.materialize_crypto_mark_index_basis(
                feature_id=mib_def.feature_id, instrument_id=mib_mismatch_id,
                dataset_version_id=d_b.dataset_version_id, event_at=b_t3, decision_at=b_decision_at,
            )
        )
        # invariant 7/9: index exists, no mark at all at b_t4 -> no row.
        self.assertIsNone(
            calculator.materialize_crypto_mark_index_basis(
                feature_id=mib_def.feature_id, instrument_id=mib_mismatch_id,
                dataset_version_id=d_b.dataset_version_id, event_at=b_t4, decision_at=b_decision_at,
            )
        )

        # =========================================================================
        # ==== MARK/INDEX BASIS -- Scenario C: cross-dataset pair rejected
        # (invariant 10) ============================================================
        # =========================================================================
        mib_cross_id = register_perpetual("MIBCROSS")
        c_event_at = datetime(2025, 4, 1, 8, tzinfo=UTC)
        c_mark = capture_mark("MIBCROSS", "TESTFIX3J1CMIBCROSS", "IGNORED", "200", c_event_at)
        c_index = capture_index("MIBCROSS", "TESTFIX3J1CMIBCROSS", "IGNORED", "198", c_event_at)
        d_c_mark = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-mib-cross-mark", "3j1c-v1",
            (c_mark.normalized_observation_id,), c_event_at + timedelta(hours=1),
        )
        pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-mib-cross-index", "3j1c-v1",
            (c_index.normalized_observation_id,), c_event_at + timedelta(hours=1),
        )
        self.assertIsNone(
            calculator.materialize_crypto_mark_index_basis(
                feature_id=mib_def.feature_id, instrument_id=mib_cross_id,
                dataset_version_id=d_c_mark.dataset_version_id, event_at=c_event_at,
                decision_at=c_event_at + timedelta(hours=2),
            )
        )

        # =========================================================================
        # ==== MARK/INDEX BASIS -- Scenario D: different-instrument pair rejected
        # (invariant 11) ============================================================
        # =========================================================================
        mib_diff_a_id = register_perpetual("MIBDIFFA")
        register_perpetual("MIBDIFFB")
        d_event_at = datetime(2025, 4, 5, 8, tzinfo=UTC)
        d_mark_a = capture_mark("MIBDIFFA", "TESTFIX3J1CMIBDIFFA", "IGNORED", "300", d_event_at)
        d_index_b = capture_index("MIBDIFFB", "TESTFIX3J1CMIBDIFFB", "IGNORED", "295", d_event_at)
        d_dataset = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-mib-diff-instrument", "3j1c-v1",
            (d_mark_a.normalized_observation_id, d_index_b.normalized_observation_id),
            d_event_at + timedelta(hours=1),
        )
        self.assertIsNone(
            calculator.materialize_crypto_mark_index_basis(
                feature_id=mib_def.feature_id, instrument_id=mib_diff_a_id,
                dataset_version_id=d_dataset.dataset_version_id, event_at=d_event_at,
                decision_at=d_event_at + timedelta(hours=2),
            )
        )

        # =========================================================================
        # ==== MARK/INDEX BASIS -- Scenario E: different price asset pair rejected
        # (invariant 12) ============================================================
        # =========================================================================
        mib_price_mismatch_id = register_perpetual("MIBPRICEMM")
        e_event_at = datetime(2025, 4, 10, 8, tzinfo=UTC)
        e_mark_id = bypass_reference_price(
            instrument_id=mib_price_mismatch_id, kind=ObservationKind.MARK_PRICE,
            identifier="MIBPRICEMM-MARK", event_at=e_event_at, price="400", price_asset="USDT",
        )
        e_index_id = bypass_reference_price(
            instrument_id=mib_price_mismatch_id, kind=ObservationKind.INDEX_PRICE,
            identifier="MIBPRICEMM-INDEX", event_at=e_event_at, price="398", price_asset="USDC",
        )
        d_e = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-mib-price-mismatch", "3j1c-v1",
            (e_mark_id, e_index_id), e_event_at + timedelta(hours=1),
        )
        self.assertIsNone(
            calculator.materialize_crypto_mark_index_basis(
                feature_id=mib_def.feature_id, instrument_id=mib_price_mismatch_id,
                dataset_version_id=d_e.dataset_version_id, event_at=e_event_at,
                decision_at=e_event_at + timedelta(hours=2),
            )
        )

        # =========================================================================
        # ==== MARK/INDEX BASIS -- Scenario F: ambiguous mark identity rejected
        # (invariant 13) ============================================================
        # =========================================================================
        mib_ambig_mark_id = register_perpetual("MIBAMBIGMARK")
        add_identifier(mib_ambig_mark_id, "MIBAMBIGMARK-B")
        f_event_at = datetime(2025, 5, 1, 8, tzinfo=UTC)
        f_mark_a = capture_mark("MIBAMBIGMARK", "TESTFIX3J1CMIBAMBIGMARK", "IGNORED", "500", f_event_at)
        f_mark_b = capture_mark("MIBAMBIGMARK-B", "TESTFIX3J1CMIBAMBIGMARK", "IGNORED", "505", f_event_at)
        d_f = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-mib-ambiguous-mark", "3j1c-v1",
            (f_mark_a.normalized_observation_id, f_mark_b.normalized_observation_id),
            f_event_at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(CryptoDerivativesFeatureError, "ambiguous_mark_observation_identity"):
            calculator.materialize_crypto_mark_index_basis(
                feature_id=mib_def.feature_id, instrument_id=mib_ambig_mark_id,
                dataset_version_id=d_f.dataset_version_id, event_at=f_event_at,
                decision_at=f_event_at + timedelta(hours=2),
            )

        # =========================================================================
        # ==== MARK/INDEX BASIS -- Scenario G: ambiguous index identity rejected
        # (invariant 14) ============================================================
        # =========================================================================
        mib_ambig_index_id = register_perpetual("MIBAMBIGINDEX")
        add_identifier(mib_ambig_index_id, "MIBAMBIGINDEX-B")
        g_event_at = datetime(2025, 5, 5, 8, tzinfo=UTC)
        g_mark = capture_mark("MIBAMBIGINDEX", "TESTFIX3J1CMIBAMBIGINDEX", "IGNORED", "600", g_event_at)
        g_index_a = capture_index("MIBAMBIGINDEX", "TESTFIX3J1CMIBAMBIGINDEX", "IGNORED", "598", g_event_at)
        g_index_b = capture_index("MIBAMBIGINDEX-B", "TESTFIX3J1CMIBAMBIGINDEX", "IGNORED", "599", g_event_at)
        d_g = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-mib-ambiguous-index", "3j1c-v1",
            (
                g_mark.normalized_observation_id, g_index_a.normalized_observation_id,
                g_index_b.normalized_observation_id,
            ),
            g_event_at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(CryptoDerivativesFeatureError, "ambiguous_index_observation_identity"):
            calculator.materialize_crypto_mark_index_basis(
                feature_id=mib_def.feature_id, instrument_id=mib_ambig_index_id,
                dataset_version_id=d_g.dataset_version_id, event_at=g_event_at,
                decision_at=g_event_at + timedelta(hours=2),
            )

        # =========================================================================
        # ==== MARK/INDEX BASIS -- Scenario H: revision resolution + future-leak
        # (invariants 15, 16, 17, 44) ================================================
        # =========================================================================
        mib_revision_id = register_perpetual("MIBREVISION")
        h_event_at = datetime(2025, 6, 1, 8, tzinfo=UTC)
        h_rev1_ingested_at = h_event_at + timedelta(days=2)
        h_mark_rev0 = capture_mark("MIBREVISION", "TESTFIX3J1CMIBREVISION", "IGNORED", "700", h_event_at, revision=0)
        h_index_rev0 = capture_index("MIBREVISION", "TESTFIX3J1CMIBREVISION", "IGNORED", "695", h_event_at, revision=0)
        h_mark_rev1 = capture_mark(
            "MIBREVISION", "TESTFIX3J1CMIBREVISION", "IGNORED", "710", h_event_at,
            ingested_at=h_rev1_ingested_at, normalized_at=h_rev1_ingested_at, revision=1,
        )
        h_index_rev1 = capture_index(
            "MIBREVISION", "TESTFIX3J1CMIBREVISION", "IGNORED", "705", h_event_at,
            ingested_at=h_rev1_ingested_at, normalized_at=h_rev1_ingested_at, revision=1,
        )
        d_h_v0_seal_at = h_event_at + timedelta(hours=1)
        d_h_v0 = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-mib-revision-v0", "3j1c-v1",
            (h_mark_rev0.normalized_observation_id, h_index_rev0.normalized_observation_id),
            d_h_v0_seal_at,
        )
        d_h_v1_seal_at = h_rev1_ingested_at + timedelta(hours=1)
        d_h_v1 = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-mib-revision-v1", "3j1c-v1",
            (
                h_mark_rev0.normalized_observation_id, h_index_rev0.normalized_observation_id,
                h_mark_rev1.normalized_observation_id, h_index_rev1.normalized_observation_id,
            ),
            d_h_v1_seal_at,
        )
        result_h_early = calculator.materialize_crypto_mark_index_basis(
            feature_id=mib_def.feature_id, instrument_id=mib_revision_id,
            dataset_version_id=d_h_v0.dataset_version_id, event_at=h_event_at,
            decision_at=d_h_v0_seal_at + timedelta(minutes=30),
        )
        self.assertIsNotNone(result_h_early)
        assert result_h_early is not None
        self.assertIn("mark_revision:0", result_h_early.source_observation_manifest)
        self.assertIn("index_revision:0", result_h_early.source_observation_manifest)
        # invariant 17: revision 1 does not exist in any knowable dataset yet.
        with self.assertRaisesRegex(CryptoDerivativesFeatureError, "dataset_not_knowable_at_decision_at"):
            calculator.materialize_crypto_mark_index_basis(
                feature_id=mib_def.feature_id, instrument_id=mib_revision_id,
                dataset_version_id=d_h_v1.dataset_version_id, event_at=h_event_at,
                decision_at=d_h_v0_seal_at + timedelta(minutes=30),
            )
        # invariants 15/16: once knowable, the highest PIT-visible revision wins.
        result_h_late = calculator.materialize_crypto_mark_index_basis(
            feature_id=mib_def.feature_id, instrument_id=mib_revision_id,
            dataset_version_id=d_h_v1.dataset_version_id, event_at=h_event_at,
            decision_at=d_h_v1_seal_at + timedelta(minutes=30),
        )
        self.assertIsNotNone(result_h_late)
        assert result_h_late is not None
        self.assertIn("mark_revision:1", result_h_late.source_observation_manifest)
        self.assertIn("index_revision:1", result_h_late.source_observation_manifest)
        # invariant 44: changed source revision changes manifest/hash.
        self.assertNotEqual(result_h_early.content_hash, result_h_late.content_hash)
        self.assertNotEqual(
            result_h_early.source_observation_manifest, result_h_late.source_observation_manifest,
        )

        # =========================================================================
        # ==== REALIZED FUNDING ANNUALIZED -- Scenario I: happy path (invariants
        # 19, 28, 46, 47) ============================================================
        # =========================================================================
        fund_happy_id = register_perpetual("FUNDHAPPY")
        register_convention(fund_happy_id, version=1, interval_hours=Decimal(8))
        i_target_at = datetime(2025, 7, 1, 8, tzinfo=UTC)
        i_realized = capture_realized(
            "FUNDHAPPY", "TESTFIX3J1CFUNDHAPPY", "IGNORED", "0.000123456789", i_target_at,
        )
        d_i = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fund-happy", "3j1c-v1",
            (i_realized.normalized_observation_id,), i_target_at + timedelta(hours=1),
        )
        i_decision_at = i_target_at + timedelta(hours=2)
        result_fund = calculator.materialize_crypto_realized_funding_annualized(
            feature_id=fund_def.feature_id, instrument_id=fund_happy_id,
            dataset_version_id=d_i.dataset_version_id, event_at=i_target_at, decision_at=i_decision_at,
        )
        self.assertIsNotNone(result_fund)
        assert result_fund is not None
        self.assertEqual(result_fund.subject_type, FeatureSubjectType.INSTRUMENT)
        expected_annualized = (
            Decimal("0.000123456789") * Decimal(_ANNUALIZATION_YEAR_SECONDS) / Decimal(28800)
        ).quantize(_VALUE_SCALE)
        self.assertEqual(result_fund.value, expected_annualized)  # invariant 28
        self.assertEqual(result_fund.event_at, i_target_at)
        self.assertIn("annualization_basis:ACT_365_FIXED", result_fund.source_observation_manifest)
        replay_fund = calculator.materialize_crypto_realized_funding_annualized(
            feature_id=fund_def.feature_id, instrument_id=fund_happy_id,
            dataset_version_id=d_i.dataset_version_id, event_at=i_target_at, decision_at=i_decision_at,
        )
        assert replay_fund is not None
        self.assertEqual(replay_fund.content_hash, result_fund.content_hash)  # invariant 47
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT value FROM feature_materializations WHERE materialization_id=%s",
                (result_fund.materialization_id,),
            )
            stored_fund_value = cursor.fetchone()[0]
        self.assertEqual(Decimal(str(stored_fund_value)), result_fund.value)  # invariant 46

        # =========================================================================
        # ==== REALIZED FUNDING ANNUALIZED -- Scenario J: indicative cannot enter
        # (invariant 20) ============================================================
        # =========================================================================
        fund_indicative_only_id = register_perpetual("FUNDINDICONLY")
        register_convention(fund_indicative_only_id, version=1, interval_hours=Decimal(8))
        j_target_at = datetime(2025, 7, 5, 8, tzinfo=UTC)
        j_published_at = j_target_at - timedelta(hours=4)
        j_indicative = capture_indicative(
            "FUNDINDICONLY", "TESTFIX3J1CFUNDINDICONLY", "IGNORED", "0.0001", j_target_at, j_published_at,
        )
        d_j = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fund-indicative-only", "3j1c-v1",
            (j_indicative.normalized_observation_id,), j_target_at + timedelta(hours=1),
        )
        self.assertIsNone(
            calculator.materialize_crypto_realized_funding_annualized(
                feature_id=fund_def.feature_id, instrument_id=fund_indicative_only_id,
                dataset_version_id=d_j.dataset_version_id, event_at=j_target_at,
                decision_at=j_target_at + timedelta(hours=2),
            )
        )

        # =========================================================================
        # ==== REALIZED FUNDING ANNUALIZED -- Scenario K: ineligible crypto kind
        # (invariant 21) ============================================================
        # =========================================================================
        fund_ineligible_id = register_spot("FUNDINELIGIBLE")
        fund_ineligible_conv_owner_id = register_perpetual("FUNDINELIGCONVOWNER")
        k_convention = register_convention(
            fund_ineligible_conv_owner_id, version=1, interval_hours=Decimal(4),
        )
        k_target_at = datetime(2025, 7, 10, 8, tzinfo=UTC)
        k_normalized_id = bypass_funding(
            instrument_id=fund_ineligible_id, kind=ObservationKind.FUNDING_RATE_REALIZED,
            identifier="FUNDINELIGIBLE-BYPASS", event_at=k_target_at, funding_rate="0.0001",
            target_funding_at=k_target_at, published_at=k_target_at, settlement_asset="USDT",
            convention_id=k_convention.convention_id, convention_version=k_convention.convention_version,
        )
        d_k = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fund-ineligible", "3j1c-v1",
            (k_normalized_id,), k_target_at + timedelta(hours=1),
        )
        self.assertIsNone(
            calculator.materialize_crypto_realized_funding_annualized(
                feature_id=fund_def.feature_id, instrument_id=fund_ineligible_id,
                dataset_version_id=d_k.dataset_version_id, event_at=k_target_at,
                decision_at=k_target_at + timedelta(hours=2),
            )
        )

        # =========================================================================
        # ==== REALIZED FUNDING ANNUALIZED -- Scenario L: convention ID mismatch
        # (invariant 22) ============================================================
        # =========================================================================
        fund_conv_owner_id = register_perpetual("FUNDCONVOWNER")
        l_convention = register_convention(fund_conv_owner_id, version=1, interval_hours=Decimal(8))
        fund_conv_wrong_instrument_id = register_perpetual("FUNDCONVWRONGINST")
        l_target_at = datetime(2025, 8, 1, 8, tzinfo=UTC)
        l_normalized_id = bypass_funding(
            instrument_id=fund_conv_wrong_instrument_id, kind=ObservationKind.FUNDING_RATE_REALIZED,
            identifier="FUNDCONVWRONGINST-BYPASS", event_at=l_target_at, funding_rate="0.0002",
            target_funding_at=l_target_at, published_at=l_target_at, settlement_asset="USDT",
            convention_id=l_convention.convention_id, convention_version=l_convention.convention_version,
        )
        d_l = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fund-conv-id-mismatch", "3j1c-v1",
            (l_normalized_id,), l_target_at + timedelta(hours=1),
        )
        self.assertIsNone(
            calculator.materialize_crypto_realized_funding_annualized(
                feature_id=fund_def.feature_id, instrument_id=fund_conv_wrong_instrument_id,
                dataset_version_id=d_l.dataset_version_id, event_at=l_target_at,
                decision_at=l_target_at + timedelta(hours=2),
            )
        )

        # =========================================================================
        # ==== REALIZED FUNDING ANNUALIZED -- Scenario M: convention version
        # mismatch (invariant 23) ==================================================
        # =========================================================================
        fund_conv_version_id = register_perpetual("FUNDCONVVERSION")
        m_convention = register_convention(fund_conv_version_id, version=1, interval_hours=Decimal(8))
        m_target_at = datetime(2025, 8, 5, 8, tzinfo=UTC)
        m_normalized_id = bypass_funding(
            instrument_id=fund_conv_version_id, kind=ObservationKind.FUNDING_RATE_REALIZED,
            identifier="FUNDCONVVERSION-BYPASS", event_at=m_target_at, funding_rate="0.0002",
            target_funding_at=m_target_at, published_at=m_target_at, settlement_asset="USDT",
            convention_id=m_convention.convention_id, convention_version=2,
        )
        d_m = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fund-conv-version-mismatch", "3j1c-v1",
            (m_normalized_id,), m_target_at + timedelta(hours=1),
        )
        self.assertIsNone(
            calculator.materialize_crypto_realized_funding_annualized(
                feature_id=fund_def.feature_id, instrument_id=fund_conv_version_id,
                dataset_version_id=d_m.dataset_version_id, event_at=m_target_at,
                decision_at=m_target_at + timedelta(hours=2),
            )
        )

        # =========================================================================
        # ==== REALIZED FUNDING ANNUALIZED -- Scenario N: later-known convention
        # cannot validate a historical feature (invariant 24) ======================
        # =========================================================================
        fund_late_conv_id = register_perpetual("FUNDLATECONV")
        n_known_at = datetime(2025, 12, 1, tzinfo=UTC)
        n_convention = register_convention(
            fund_late_conv_id, version=1, interval_hours=Decimal(8), known_at=n_known_at,
        )
        n_target_at = datetime(2025, 8, 10, 8, tzinfo=UTC)
        n_normalized_id = bypass_funding(
            instrument_id=fund_late_conv_id, kind=ObservationKind.FUNDING_RATE_REALIZED,
            identifier="FUNDLATECONV-BYPASS", event_at=n_target_at, funding_rate="0.0002",
            target_funding_at=n_target_at, published_at=n_target_at, settlement_asset="USDT",
            convention_id=n_convention.convention_id, convention_version=n_convention.convention_version,
        )
        d_n = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fund-late-convention", "3j1c-v1",
            (n_normalized_id,), n_target_at + timedelta(hours=1),
        )
        self.assertIsNone(
            calculator.materialize_crypto_realized_funding_annualized(
                feature_id=fund_def.feature_id, instrument_id=fund_late_conv_id,
                dataset_version_id=d_n.dataset_version_id, event_at=n_target_at,
                decision_at=n_target_at + timedelta(hours=2),  # well before n_known_at
            )
        )
        # Once the convention itself becomes knowable, the same evidence resolves.
        result_n_late = calculator.materialize_crypto_realized_funding_annualized(
            feature_id=fund_def.feature_id, instrument_id=fund_late_conv_id,
            dataset_version_id=d_n.dataset_version_id, event_at=n_target_at,
            decision_at=n_known_at + timedelta(hours=1),
        )
        self.assertIsNotNone(result_n_late)

        # =========================================================================
        # ==== REALIZED FUNDING ANNUALIZED -- Scenario O: non-integral interval
        # seconds rejected (invariant 26) ==========================================
        # =========================================================================
        fund_noninteg_id = register_perpetual("FUNDNONINTEG")
        register_convention(fund_noninteg_id, version=1, interval_hours=Decimal("0.0001"))
        o_target_at = datetime(2025, 8, 15, 8, tzinfo=UTC)
        o_realized = capture_realized(
            "FUNDNONINTEG", "TESTFIX3J1CFUNDNONINTEG", "IGNORED", "0.0001", o_target_at,
        )
        d_o = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fund-noninteg", "3j1c-v1",
            (o_realized.normalized_observation_id,), o_target_at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(CryptoDerivativesFeatureError, "non_integral_interval_seconds"):
            calculator.materialize_crypto_realized_funding_annualized(
                feature_id=fund_def.feature_id, instrument_id=fund_noninteg_id,
                dataset_version_id=d_o.dataset_version_id, event_at=o_target_at,
                decision_at=o_target_at + timedelta(hours=2),
            )

        # =========================================================================
        # ==== REALIZED FUNDING ANNUALIZED -- Scenario P: revision resolution +
        # future-leak + ambiguous identity (invariants 37, 39, 41-bonus) ===========
        # =========================================================================
        fund_revision_id = register_perpetual("FUNDREVISION")
        register_convention(fund_revision_id, version=1, interval_hours=Decimal(8))
        p_target_at = datetime(2025, 9, 1, 8, tzinfo=UTC)
        p_rev1_ingested_at = p_target_at + timedelta(days=2)
        p_rev0 = capture_realized(
            "FUNDREVISION", "TESTFIX3J1CFUNDREVISION", "IGNORED", "0.0001", p_target_at, revision=0,
        )
        p_rev1 = capture_realized(
            "FUNDREVISION", "TESTFIX3J1CFUNDREVISION", "IGNORED", "0.00015", p_target_at,
            ingested_at=p_rev1_ingested_at, normalized_at=p_rev1_ingested_at, revision=1,
        )
        d_p_v0_seal_at = p_target_at + timedelta(hours=1)
        d_p_v0 = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fund-revision-v0", "3j1c-v1",
            (p_rev0.normalized_observation_id,), d_p_v0_seal_at,
        )
        d_p_v1_seal_at = p_rev1_ingested_at + timedelta(hours=1)
        d_p_v1 = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fund-revision-v1", "3j1c-v1",
            (p_rev0.normalized_observation_id, p_rev1.normalized_observation_id), d_p_v1_seal_at,
        )
        result_p_early = calculator.materialize_crypto_realized_funding_annualized(
            feature_id=fund_def.feature_id, instrument_id=fund_revision_id,
            dataset_version_id=d_p_v0.dataset_version_id, event_at=p_target_at,
            decision_at=d_p_v0_seal_at + timedelta(minutes=30),
        )
        assert result_p_early is not None
        self.assertIn("realized_revision:0", result_p_early.source_observation_manifest)
        # invariant 39: revision 1 does not exist in any knowable dataset yet.
        with self.assertRaisesRegex(CryptoDerivativesFeatureError, "dataset_not_knowable_at_decision_at"):
            calculator.materialize_crypto_realized_funding_annualized(
                feature_id=fund_def.feature_id, instrument_id=fund_revision_id,
                dataset_version_id=d_p_v1.dataset_version_id, event_at=p_target_at,
                decision_at=d_p_v0_seal_at + timedelta(minutes=30),
            )
        # invariant 37: once knowable, highest revision DESC/ingested_at DESC wins.
        result_p_late = calculator.materialize_crypto_realized_funding_annualized(
            feature_id=fund_def.feature_id, instrument_id=fund_revision_id,
            dataset_version_id=d_p_v1.dataset_version_id, event_at=p_target_at,
            decision_at=d_p_v1_seal_at + timedelta(minutes=30),
        )
        assert result_p_late is not None
        self.assertIn("realized_revision:1", result_p_late.source_observation_manifest)
        self.assertNotEqual(result_p_early.content_hash, result_p_late.content_hash)

        # bonus: ambiguous realized provider identity fails closed for this
        # feature too (shares _select_funding with crypto_funding_forecast_error).
        fund_ambig_id = register_perpetual("FUNDAMBIG")
        add_identifier(fund_ambig_id, "FUNDAMBIG-B")
        register_convention(fund_ambig_id, version=1, interval_hours=Decimal(8))
        p_ambig_target_at = datetime(2025, 9, 5, 8, tzinfo=UTC)
        p_ambig_a = capture_realized(
            "FUNDAMBIG", "TESTFIX3J1CFUNDAMBIG", "IGNORED", "0.0001", p_ambig_target_at,
        )
        p_ambig_b = capture_realized(
            "FUNDAMBIG-B", "TESTFIX3J1CFUNDAMBIG", "IGNORED", "0.00012", p_ambig_target_at,
        )
        d_p_ambig = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fund-ambiguous", "3j1c-v1",
            (p_ambig_a.normalized_observation_id, p_ambig_b.normalized_observation_id),
            p_ambig_target_at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(
            CryptoDerivativesFeatureError, "ambiguous_realized_funding_observation_identity",
        ):
            calculator.materialize_crypto_realized_funding_annualized(
                feature_id=fund_def.feature_id, instrument_id=fund_ambig_id,
                dataset_version_id=d_p_ambig.dataset_version_id, event_at=p_ambig_target_at,
                decision_at=p_ambig_target_at + timedelta(hours=2),
            )

        # =========================================================================
        # ==== FUNDING FORECAST ERROR -- Scenario Q: happy path + absent-before-
        # realized-visible (invariants 29, 30, 40, 43, 46, 47, 48, 49, 50) =========
        # =========================================================================
        fcast_happy_id = register_perpetual("FCASTHAPPY")
        register_convention(fcast_happy_id, version=1, interval_hours=Decimal(8))
        q_target_at = datetime(2025, 10, 1, 8, tzinfo=UTC)
        q_published_at = q_target_at - timedelta(hours=4)
        q_indicative = capture_indicative(
            "FCASTHAPPY", "TESTFIX3J1CFCASTHAPPY", "IGNORED", "0.00009", q_target_at, q_published_at,
        )
        q_indicative_seal_at = q_published_at + timedelta(minutes=30)
        d_q_indicative_only = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fcast-happy-indicative-only", "3j1c-v1",
            (q_indicative.normalized_observation_id,), q_indicative_seal_at,
        )
        # invariant 40: absent before the realized observation is itself visible.
        self.assertIsNone(
            calculator.materialize_crypto_funding_forecast_error(
                feature_id=fcast_def.feature_id, instrument_id=fcast_happy_id,
                dataset_version_id=d_q_indicative_only.dataset_version_id,
                target_funding_at=q_target_at, decision_at=q_indicative_seal_at + timedelta(minutes=30),
            )
        )
        q_realized = capture_realized(
            "FCASTHAPPY", "TESTFIX3J1CFCASTHAPPY", "IGNORED", "0.0001", q_target_at,
        )
        q_seal_at = q_target_at + timedelta(hours=1)
        d_q = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fcast-happy", "3j1c-v1",
            (q_indicative.normalized_observation_id, q_realized.normalized_observation_id), q_seal_at,
        )
        q_decision_at = q_seal_at + timedelta(hours=1)
        result_fcast = calculator.materialize_crypto_funding_forecast_error(
            feature_id=fcast_def.feature_id, instrument_id=fcast_happy_id,
            dataset_version_id=d_q.dataset_version_id, target_funding_at=q_target_at,
            decision_at=q_decision_at,
        )
        self.assertIsNotNone(result_fcast)
        assert result_fcast is not None
        self.assertEqual(result_fcast.subject_type, FeatureSubjectType.INSTRUMENT)
        self.assertEqual(
            result_fcast.value, (Decimal("0.0001") - Decimal("0.00009")).quantize(_VALUE_SCALE),
        )
        self.assertEqual(result_fcast.event_at, q_target_at)  # invariant 30
        for prefix in (  # invariant 43
            "historical_dataset_version_id:", "historical_dataset_content_hash:", "source_id:",
            "instrument_id:", "realized_normalized_observation_id:", "realized_raw_observation_id:",
            "realized_revision:", "realized_ingested_at:", "realized_event_at:",
            "realized_target_funding_at:", "indicative_normalized_observation_id:",
            "indicative_raw_observation_id:", "indicative_revision:", "indicative_ingested_at:",
            "indicative_published_at:", "indicative_target_funding_at:", "convention_id:",
            "convention_version:", "funding_settlement_asset:",
        ):
            self.assertTrue(any(token.startswith(prefix) for token in result_fcast.source_observation_manifest), prefix)
        replay_fcast = calculator.materialize_crypto_funding_forecast_error(
            feature_id=fcast_def.feature_id, instrument_id=fcast_happy_id,
            dataset_version_id=d_q.dataset_version_id, target_funding_at=q_target_at,
            decision_at=q_decision_at,
        )
        assert replay_fcast is not None
        self.assertEqual(replay_fcast.content_hash, result_fcast.content_hash)  # invariant 47
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT value FROM feature_materializations WHERE materialization_id=%s",
                (result_fcast.materialization_id,),
            )
            stored_fcast_value = cursor.fetchone()[0]
        self.assertEqual(Decimal(str(stored_fcast_value)), result_fcast.value)  # invariant 46
        conflicting_fcast = replace(result_fcast, value=Decimal("1"), content_hash="e" * 64)
        with self.assertRaisesRegex(FeatureAuthorityError, "conflict"):
            feature_authority.materialize_subject(conflicting_fcast)  # invariant 48
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection, connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE feature_materializations SET value=0 WHERE materialization_id=%s",
                (result_fcast.materialization_id,),
            )  # invariant 49

        # =========================================================================
        # ==== FUNDING FORECAST ERROR -- Scenario R: latest-before-target selection
        # + revision resolution (invariants 32, 35, 36) ============================
        # =========================================================================
        fcast_select_id = register_perpetual("FCASTSELECT")
        register_convention(fcast_select_id, version=1, interval_hours=Decimal(8))
        r_target_at = datetime(2025, 10, 5, 8, tzinfo=UTC)
        r_realized = capture_realized(
            "FCASTSELECT", "TESTFIX3J1CFCASTSELECT", "IGNORED", "0.0002", r_target_at,
        )
        r_early_published_at = r_target_at - timedelta(hours=16)
        r_late_published_at = r_target_at - timedelta(hours=4)
        r_early_indicative = capture_indicative(
            "FCASTSELECT", "TESTFIX3J1CFCASTSELECT", "IGNORED", "0.00005", r_target_at, r_early_published_at,
        )
        # invariant 35: only the early estimate is sealed -> it is selected
        # because no later eligible pre-target estimate exists yet.
        d_r_early_only = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fcast-select-early-only", "3j1c-v1",
            (r_realized.normalized_observation_id, r_early_indicative.normalized_observation_id),
            r_target_at + timedelta(hours=1),
        )
        result_r_early = calculator.materialize_crypto_funding_forecast_error(
            feature_id=fcast_def.feature_id, instrument_id=fcast_select_id,
            dataset_version_id=d_r_early_only.dataset_version_id, target_funding_at=r_target_at,
            decision_at=r_target_at + timedelta(hours=2),
        )
        assert result_r_early is not None
        self.assertEqual(
            result_r_early.value, (Decimal("0.0002") - Decimal("0.00005")).quantize(_VALUE_SCALE),
        )
        self.assertIn(
            f"indicative_published_at:{r_early_published_at.isoformat()}",
            result_r_early.source_observation_manifest,
        )
        # Now add a later eligible pre-target estimate, with two revisions of it,
        # in a new sealed dataset -- invariant 36: revision DESC/ingested_at DESC
        # picks the highest revision at the winning (latest) publish instant.
        r_late_rev0 = capture_indicative(
            "FCASTSELECT", "TESTFIX3J1CFCASTSELECT", "IGNORED", "0.00007", r_target_at, r_late_published_at,
            revision=0,
        )
        r_late_rev1_ingested_at = r_late_published_at + timedelta(hours=1)
        r_late_rev1 = capture_indicative(
            "FCASTSELECT", "TESTFIX3J1CFCASTSELECT", "IGNORED", "0.00008", r_target_at, r_late_published_at,
            ingested_at=r_late_rev1_ingested_at, normalized_at=r_late_rev1_ingested_at, revision=1,
        )
        d_r_full_seal_at = r_target_at + timedelta(hours=1)
        d_r_full = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fcast-select-full", "3j1c-v1",
            (
                r_realized.normalized_observation_id, r_early_indicative.normalized_observation_id,
                r_late_rev0.normalized_observation_id, r_late_rev1.normalized_observation_id,
            ),
            d_r_full_seal_at,
        )
        result_r_full = calculator.materialize_crypto_funding_forecast_error(
            feature_id=fcast_def.feature_id, instrument_id=fcast_select_id,
            dataset_version_id=d_r_full.dataset_version_id, target_funding_at=r_target_at,
            decision_at=d_r_full_seal_at + timedelta(hours=1),
        )
        assert result_r_full is not None
        # invariant 32: the latest eligible pre-target instant is selected...
        self.assertIn(
            f"indicative_published_at:{r_late_published_at.isoformat()}",
            result_r_full.source_observation_manifest,
        )
        # invariant 36: ...and at that instant, the highest revision (1) wins.
        self.assertIn("indicative_revision:1", result_r_full.source_observation_manifest)
        self.assertEqual(
            result_r_full.value, (Decimal("0.0002") - Decimal("0.00008")).quantize(_VALUE_SCALE),
        )

        # =========================================================================
        # ==== FUNDING FORECAST ERROR -- Scenario S: indicative at/after target
        # instant rejected (invariants 33, 34) ======================================
        # =========================================================================
        fcast_bad_timing_id = register_perpetual("FCASTBADTIMING")
        s_convention = register_convention(fcast_bad_timing_id, version=1, interval_hours=Decimal(8))
        s_target_at = datetime(2025, 11, 1, 8, tzinfo=UTC)
        s_realized = capture_realized(
            "FCASTBADTIMING", "TESTFIX3J1CFCASTBADTIMING", "IGNORED", "0.0003", s_target_at,
        )
        # An indicative published AT the exact target instant -- the validated
        # pipeline itself refuses this (published_at must be strictly before the
        # target), so it is constructed directly at the evidence layer.
        s_at_target_id = bypass_funding(
            instrument_id=fcast_bad_timing_id, kind=ObservationKind.FUNDING_RATE_INDICATIVE,
            identifier="FCASTBADTIMING-AT-BYPASS", event_at=s_target_at, funding_rate="0.00011",
            target_funding_at=s_target_at, published_at=s_target_at, settlement_asset="USDT",
            convention_id=s_convention.convention_id, convention_version=s_convention.convention_version,
        )
        s_after_published_at = s_target_at + timedelta(hours=1)
        s_after_target_id = bypass_funding(
            instrument_id=fcast_bad_timing_id, kind=ObservationKind.FUNDING_RATE_INDICATIVE,
            identifier="FCASTBADTIMING-AFTER-BYPASS", event_at=s_after_published_at,
            funding_rate="0.00012", target_funding_at=s_target_at, published_at=s_after_published_at,
            settlement_asset="USDT", convention_id=s_convention.convention_id,
            convention_version=s_convention.convention_version,
        )
        d_s = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fcast-bad-timing", "3j1c-v1",
            (s_realized.normalized_observation_id, s_at_target_id, s_after_target_id),
            s_after_published_at + timedelta(hours=1),
        )
        self.assertIsNone(
            calculator.materialize_crypto_funding_forecast_error(
                feature_id=fcast_def.feature_id, instrument_id=fcast_bad_timing_id,
                dataset_version_id=d_s.dataset_version_id, target_funding_at=s_target_at,
                decision_at=s_after_published_at + timedelta(hours=2),
            )
        )

        # =========================================================================
        # ==== FUNDING FORECAST ERROR -- Scenario T: cross-dataset pair + convention
        # mismatch rejected (invariants 31, 38) =====================================
        # =========================================================================
        fcast_cross_id = register_perpetual("FCASTCROSS")
        t_convention = register_convention(fcast_cross_id, version=1, interval_hours=Decimal(8))
        t_target_at = datetime(2025, 11, 5, 8, tzinfo=UTC)
        t_published_at = t_target_at - timedelta(hours=4)
        t_realized = capture_realized(
            "FCASTCROSS", "TESTFIX3J1CFCASTCROSS", "IGNORED", "0.0004", t_target_at,
        )
        t_indicative = capture_indicative(
            "FCASTCROSS", "TESTFIX3J1CFCASTCROSS", "IGNORED", "0.0001", t_target_at, t_published_at,
        )
        d_t_realized_only = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fcast-cross-realized", "3j1c-v1",
            (t_realized.normalized_observation_id,), t_target_at + timedelta(hours=1),
        )
        pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fcast-cross-indicative", "3j1c-v1",
            (t_indicative.normalized_observation_id,), t_target_at + timedelta(hours=1),
        )
        # invariant 38: indicative is not a member of the dataset being queried.
        self.assertIsNone(
            calculator.materialize_crypto_funding_forecast_error(
                feature_id=fcast_def.feature_id, instrument_id=fcast_cross_id,
                dataset_version_id=d_t_realized_only.dataset_version_id, target_funding_at=t_target_at,
                decision_at=t_target_at + timedelta(hours=2),
            )
        )
        # invariant 31: same dataset, but a different convention version bound to
        # the indicative side -- constructed at the evidence layer since a real
        # convention rebind mid-flight is not reachable through the validated
        # pipeline for two observations minted moments apart.
        register_convention(fcast_cross_id, version=2, interval_hours=Decimal(4), known_at=t_target_at)
        u_target_at = datetime(2025, 11, 10, 8, tzinfo=UTC)
        u_published_at = u_target_at - timedelta(hours=4)
        u_realized_id = bypass_funding(
            instrument_id=fcast_cross_id, kind=ObservationKind.FUNDING_RATE_REALIZED,
            identifier="FCASTCROSS-CONV-REALIZED", event_at=u_target_at, funding_rate="0.0005",
            target_funding_at=u_target_at, published_at=u_target_at, settlement_asset="USDT",
            convention_id=t_convention.convention_id, convention_version=t_convention.convention_version,
        )
        u_indicative_id = bypass_funding(
            instrument_id=fcast_cross_id, kind=ObservationKind.FUNDING_RATE_INDICATIVE,
            identifier="FCASTCROSS-CONV-INDICATIVE", event_at=u_published_at, funding_rate="0.0001",
            target_funding_at=u_target_at, published_at=u_published_at, settlement_asset="USDT",
            convention_id=t_convention.convention_id, convention_version=2,
        )
        d_u = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fcast-conv-mismatch", "3j1c-v1",
            (u_realized_id, u_indicative_id), u_target_at + timedelta(hours=1),
        )
        self.assertIsNone(
            calculator.materialize_crypto_funding_forecast_error(
                feature_id=fcast_def.feature_id, instrument_id=fcast_cross_id,
                dataset_version_id=d_u.dataset_version_id, target_funding_at=u_target_at,
                decision_at=u_target_at + timedelta(hours=2),
            )
        )

        # =========================================================================
        # ==== FUNDING FORECAST ERROR -- Scenario V: provider ambiguity, realized
        # and indicative sides (invariants 41, 42) ==================================
        # =========================================================================
        fcast_ambig_id = register_perpetual("FCASTAMBIG")
        add_identifier(fcast_ambig_id, "FCASTAMBIG-REALB")
        add_identifier(fcast_ambig_id, "FCASTAMBIG-INDIB")
        register_convention(fcast_ambig_id, version=1, interval_hours=Decimal(8))
        v1_target_at = datetime(2025, 12, 1, 8, tzinfo=UTC)
        v1_realized_a = capture_realized(
            "FCASTAMBIG", "TESTFIX3J1CFCASTAMBIG", "IGNORED", "0.0001", v1_target_at,
        )
        v1_realized_b = capture_realized(
            "FCASTAMBIG-REALB", "TESTFIX3J1CFCASTAMBIG", "IGNORED", "0.00013", v1_target_at,
        )
        d_v1 = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fcast-ambiguous-realized", "3j1c-v1",
            (v1_realized_a.normalized_observation_id, v1_realized_b.normalized_observation_id),
            v1_target_at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(
            CryptoDerivativesFeatureError, "ambiguous_realized_funding_observation_identity",
        ):
            calculator.materialize_crypto_funding_forecast_error(
                feature_id=fcast_def.feature_id, instrument_id=fcast_ambig_id,
                dataset_version_id=d_v1.dataset_version_id, target_funding_at=v1_target_at,
                decision_at=v1_target_at + timedelta(hours=2),
            )

        v2_target_at = datetime(2025, 12, 5, 8, tzinfo=UTC)
        v2_published_at = v2_target_at - timedelta(hours=4)
        v2_realized = capture_realized(
            "FCASTAMBIG", "TESTFIX3J1CFCASTAMBIG", "IGNORED", "0.0002", v2_target_at,
        )
        v2_indicative_a = capture_indicative(
            "FCASTAMBIG", "TESTFIX3J1CFCASTAMBIG", "IGNORED", "0.00009", v2_target_at, v2_published_at,
        )
        v2_indicative_b = capture_indicative(
            "FCASTAMBIG-INDIB", "TESTFIX3J1CFCASTAMBIG", "IGNORED", "0.00010", v2_target_at, v2_published_at,
        )
        d_v2 = pipeline.seal_dataset(
            crypto_source.source_id, "3j1c-dataset-fcast-ambiguous-indicative", "3j1c-v1",
            (
                v2_realized.normalized_observation_id, v2_indicative_a.normalized_observation_id,
                v2_indicative_b.normalized_observation_id,
            ),
            v2_target_at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(
            CryptoDerivativesFeatureError, "ambiguous_indicative_funding_observation_identity",
        ):
            calculator.materialize_crypto_funding_forecast_error(
                feature_id=fcast_def.feature_id, instrument_id=fcast_ambig_id,
                dataset_version_id=d_v2.dataset_version_id, target_funding_at=v2_target_at,
                decision_at=v2_target_at + timedelta(hours=2),
            )

        # =========================================================================
        # ==== Invariant 50: restart preserves all three materializations ========
        # =========================================================================
        far_future_decision_at = max(
            result_mib.knowledge_at, result_fund.knowledge_at, result_fcast.knowledge_at,
        ) + timedelta(days=365)
        database.close()
        reopened = PostgresDatabase(dsn)
        reopened_authority = PostgresFeatureAuthority(reopened)
        self.assertEqual(
            reopened_authority.latest_as_of_subject(
                mib_def.feature_id, FeatureSubjectType.INSTRUMENT, mib_happy_id,
                str(d_a.dataset_version_id), far_future_decision_at,
            ),
            (result_mib,),
        )
        self.assertEqual(
            reopened_authority.latest_as_of_subject(
                fund_def.feature_id, FeatureSubjectType.INSTRUMENT, fund_happy_id,
                str(d_i.dataset_version_id), far_future_decision_at,
            ),
            (result_fund,),
        )
        self.assertEqual(
            reopened_authority.latest_as_of_subject(
                fcast_def.feature_id, FeatureSubjectType.INSTRUMENT, fcast_happy_id,
                str(d_q.dataset_version_id), far_future_decision_at,
            ),
            (result_fcast,),
        )

        # =========================================================================
        # ==== Invariant 53: no new table/store/migration exists ==================
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
                ("%crypto_derivatives%", "%3j1c%"),
            )
            self.assertEqual(cursor.fetchall(), [])
        reopened.close()

        # Sanity: the exact facts this module claims, still true at the end.
        self.assertEqual(mib_def.units, "dimensionless")
        self.assertEqual(fund_def.units, "1/year")
        self.assertEqual(fcast_def.units, "dimensionless")
        self.assertEqual(FeatureFamily.DERIVATIVES.value, "DERIVATIVES")


if __name__ == "__main__":
    unittest.main()
