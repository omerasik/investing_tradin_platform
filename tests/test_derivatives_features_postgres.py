"""Real PostgreSQL evidence for Module 3J.1a (Derivatives Feature Foundation +
Futures Curve Feature Pack).

Fixture instruments/series use the ``TESTFIXTURE:3J1A:`` prefix so they can
never displace real records on the shared CI database. Every price, contract
date and provider identifier is a FIXTURE; nothing here was retrieved from or
verified against any exchange, and no alpha/performance claim is made.
"""

import os
import unittest
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

SETTLEMENT = "SETTLEMENT_PRICE"


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class DerivativesFeaturesPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_derivatives_futures_curve_features_end_to_end(self) -> None:
        from trade_platform.derivatives_features import (
            CALCULATION_VERSION,
            FUTURES_ANNUALIZED_CALENDAR_SPREAD_RATE,
            FUTURES_CURVE_CURVATURE,
            FUTURES_FRONT_BACK_NORMALIZED_SPREAD,
            DerivativesFeatureError,
            PostgresDerivativesFeatureCalculator,
            derivatives_feature_definitions,
        )
        from trade_platform.domain import AssetClass
        from trade_platform.feature_authority import (
            FeatureAuthorityError,
            FeatureFamily,
            FeatureMaterializationV2,
            FeatureQualityStatus,
            FeatureSubjectType,
            PostgresFeatureAuthority,
        )
        from trade_platform.futures_contracts import (
            FuturesContractSeries,
            FuturesContractSpecification,
            PostgresFuturesContractAuthority,
            SettlementType,
        )
        from trade_platform.futures_term_structure import (
            DayCountConvention,
            FuturesTermStructureMethod,
            PostgresFuturesTermStructureAuthority,
            SessionPolicy,
            SettlementFinalityPolicy,
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
        contracts = PostgresFuturesContractAuthority(database)
        pipeline = PostgresHistoricalMarketDataPipeline(database)
        term_structure = PostgresFuturesTermStructureAuthority(database)
        feature_authority = PostgresFeatureAuthority(database)
        calculator = PostgresDerivativesFeatureCalculator(database)

        registered_at = datetime(2024, 1, 2, tzinfo=UTC)
        namespace = "TESTFIX_3J1A_PROVIDER"
        venue = "XCEC"
        series_id = "TESTFIXTURE:3J1A:SERIES:GC"
        other_series_id = "TESTFIXTURE:3J1A:SERIES:OTHER"

        # ---- instrument/series/contract fixtures ------------------------------
        def register_instrument(
            suffix: str, symbol: str, *, expiration_date: date, instrument_venue: str = venue,
        ) -> str:
            instrument_id = f"TESTFIXTURE:3J1A:{suffix}"
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id, asset_class=AssetClass.COMMODITY,
                    instrument_type=InstrumentType.FUTURE, exchange_name="COMEX",
                    venue=instrument_venue, mic=instrument_venue, canonical_symbol=symbol,
                    listing_date=date(2023, 1, 3), base_currency="USD", quote_currency="USD",
                    settlement_currency="USD", contract_multiplier=Decimal(100),
                    contract_size=Decimal(100), tick_size=Decimal("0.10"), lot_size=Decimal(1),
                    price_precision=2, quantity_precision=0,
                    trading_timezone="America/New_York",
                    market_session_type=SessionType.FUTURES_23X5,
                    representation_kind=RepresentationKind.FUTURE, registered_at=registered_at,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                    contract_code=symbol, expiration_date=expiration_date, first_notice_date=None,
                    last_trade_date=expiration_date - timedelta(days=2), roll_rule="TESTFIX_3J1A_ROLL_V1",
                    continuous_parent_id=None,
                )
            )
            master.add_identifier_mapping(
                IdentifierMapping(
                    instrument_id=instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
                    namespace=namespace, value=suffix, valid_from=registered_at,
                    valid_until=None, ingested_at=registered_at,
                    source_reference="fixture:provider-identifier",
                )
            )
            return instrument_id

        # Ticker text deliberately disagrees with expiration order (invariant
        # 20): "ABACK" < "MMID" < "ZFRONT" lexically, but FRONT expires first,
        # MID second, BACK last -- ordering must follow 3H.1 expiration
        # identity, never symbol text.
        front_id = register_instrument("ZFRONT", "TESTFIX3J1AZFRONT", expiration_date=date(2025, 6, 26))
        mid_id = register_instrument("MMID", "TESTFIX3J1AMMID", expiration_date=date(2025, 9, 26))
        back_id = register_instrument("ABACK", "TESTFIX3J1AABACK", expiration_date=date(2025, 12, 26))
        solo_id = register_instrument("SOLO", "TESTFIX3J1ASOLO", expiration_date=date(2026, 3, 26))
        other_front_id = register_instrument(
            "OFRONT", "TESTFIX3J1AOFRONT", expiration_date=date(2025, 6, 26)
        )
        other_back_id = register_instrument(
            "OBACK", "TESTFIX3J1AOBACK", expiration_date=date(2025, 9, 26)
        )

        def make_series(sid: str, root: str) -> FuturesContractSeries:
            return FuturesContractSeries(
                series_id=sid, root_symbol=root, exchange_name="COMEX", venue=venue, mic=venue,
                asset_class=AssetClass.COMMODITY, underlying_reference="Gold", currency="USD",
                contract_multiplier=Decimal(100), unit_of_measure="TROY_OUNCE",
                tick_size=Decimal("0.10"), tick_value=Decimal("10.00"), price_precision=2,
                quantity_precision=0, settlement_type=SettlementType.CASH_SETTLED,
                trading_timezone="America/New_York", session_type=SessionType.FUTURES_23X5,
                registered_at=registered_at, source_reference="fixture:series",
            )

        series = make_series(series_id, "TESTFIX3J1AGC")
        other_series = make_series(other_series_id, "TESTFIX3J1AOTHER")
        contracts.register_series(series)
        contracts.register_series(other_series)

        def specify(instrument_id: str, sid: str, code: str, year: int, month: int, expiry: date) -> None:
            contracts.specify_contract(
                FuturesContractSpecification(
                    instrument_id=instrument_id, series_id=sid, contract_code=code,
                    contract_year=year, contract_month=month, first_trade_date=date(2023, 1, 3),
                    last_trade_date=expiry - timedelta(days=2), expiration_date=expiry,
                    settlement_date=expiry + timedelta(days=1), settlement_type=SettlementType.CASH_SETTLED,
                    contract_multiplier=Decimal(100), tick_size=Decimal("0.10"),
                    tick_value=Decimal("10.00"), registered_at=registered_at,
                    source_reference="fixture:contract",
                )
            )

        specify(front_id, series_id, "ZFRONT", 2025, 6, date(2025, 6, 26))
        specify(mid_id, series_id, "MMID", 2025, 9, date(2025, 9, 26))
        specify(back_id, series_id, "ABACK", 2025, 12, date(2025, 12, 26))
        specify(solo_id, series_id, "SOLO", 2026, 3, date(2026, 3, 26))
        specify(other_front_id, other_series_id, "OFRONT", 2025, 6, date(2025, 6, 26))
        specify(other_back_id, other_series_id, "OBACK", 2025, 9, date(2025, 9, 26))

        source = AuthorizedHistoricalSource(
            provider="TESTFIX_3J1A", dataset_name="derivatives-feature-fixture",
            provider_identifier_namespace=namespace, provider_terms_version="v1",
            authorization_reference="fixture://authorization/derivatives-features",
            authorized_at=registered_at, created_at=registered_at,
            asset_scope=AssetScope.FUTURES.value,
            authorized_observation_kinds=frozenset({ObservationKind.SETTLEMENT_PRICE}),
        )
        pipeline.register_source(source)

        def raw(
            payload: dict[str, object], *, identifier: str, symbol: str, revision: int = 0,
            event_at: datetime, ingested_at: datetime | None = None,
        ) -> RawHistoricalObservation:
            return RawHistoricalObservation(
                source_id=source.source_id, observation_kind=ObservationKind.SETTLEMENT_PRICE,
                provider_identifier=identifier, provider_symbol=symbol, exchange=venue,
                event_at=event_at, effective_at=event_at, ingested_at=ingested_at or event_at,
                adjustment_status=AdjustmentStatus.AS_REPORTED, revision=revision,
                provenance_uri=f"fixture://settlement/{identifier}/{revision}", raw_payload=payload,
            )

        def settlement_payload(price: str, settlement_date: date, finality: str) -> dict[str, object]:
            return {
                "settlement_price": price, "price_currency": "USD",
                "settlement_date": settlement_date.isoformat(),
                "settlement_effective_at": f"{settlement_date.isoformat()}T18:00:00+00:00",
                "finality": finality, "quote_unit": "USD_PER_TROY_OUNCE",
            }

        def capture_and_normalize(
            identifier: str, symbol: str, payload: dict[str, object], *,
            event_at: datetime, ingested_at: datetime, revision: int = 0,
        ) -> object:
            (raw_id,) = pipeline.capture_raw(
                [raw(payload, identifier=identifier, symbol=symbol, revision=revision,
                     event_at=event_at, ingested_at=ingested_at)]
            )
            return pipeline.normalize(raw_id, "3j1a-v1", ingested_at)

        # ==== main 3-point curve: FRONT/MID/BACK FINAL settlements =============
        as_of = date(2025, 3, 20)
        t0 = datetime(2025, 3, 20, 19, tzinfo=UTC)
        event_at = datetime(2025, 3, 20, 18, tzinfo=UTC)

        front_obs = capture_and_normalize(
            "ZFRONT", "TESTFIX3J1AZFRONT", settlement_payload("100.00", as_of, "FINAL"),
            event_at=event_at, ingested_at=t0,
        )
        mid_obs = capture_and_normalize(
            "MMID", "TESTFIX3J1AMMID", settlement_payload("103.00", as_of, "FINAL"),
            event_at=event_at, ingested_at=t0,
        )
        back_obs = capture_and_normalize(
            "ABACK", "TESTFIX3J1AABACK", settlement_payload("110.00", as_of, "FINAL"),
            event_at=event_at, ingested_at=t0,
        )
        dataset_seal_at = datetime(2025, 3, 20, 20, tzinfo=UTC)
        d_main = pipeline.seal_dataset(
            source.source_id, "3j1a-dataset-main", "3j1a-v1",
            (front_obs.normalized_observation_id, mid_obs.normalized_observation_id,
             back_obs.normalized_observation_id),
            dataset_seal_at,
        )

        method_carry = FuturesTermStructureMethod(
            method_name="TESTFIX_3J1A_CARRY_ACT365F", method_version=1, minimum_point_count=2,
            settlement_finality_policy=SettlementFinalityPolicy.FINAL_ONLY,
            session_policy=SessionPolicy.SAME_SESSION_STRICT, classification_enabled=False,
            carry_enabled=True, day_count_convention=DayCountConvention.ACT_365F,
            effective_from=registered_at, known_at=registered_at,
        )
        term_structure.register_method(method_carry)
        knowledge_at = dataset_seal_at + timedelta(minutes=1)
        curve_main, points_main = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_main.dataset_version_id,
            method_id=method_carry.method_id, as_of=as_of, knowledge_at=knowledge_at,
        )
        self.assertEqual(curve_main.point_count, 3)
        # Invariant 20: expiration order, not ticker text ("ABACK" < "MMID" <
        # "ZFRONT" lexically, but front/mid/back expiration order must win).
        self.assertEqual(
            [point.instrument_id for point in points_main], [front_id, mid_id, back_id]
        )

        # ---- register the 3J.1a feature definitions (invariants 1, 3) --------
        spread_def, rate_def, curvature_def = derivatives_feature_definitions(registered_at)
        feature_authority.register(spread_def)
        feature_authority.register(rate_def)
        feature_authority.register(curvature_def)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT family FROM feature_definition_versions WHERE feature_id IN (%s,%s,%s)",
                (spread_def.feature_id, rate_def.feature_id, curvature_def.feature_id),
            )
            families = {str(row[0]) for row in cursor.fetchall()}
        self.assertEqual(families, {"DERIVATIVES"})
        # Pre-existing family still accepted alongside the widened CHECK.
        legacy_definition_id = uuid4()
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO feature_definition_versions VALUES (%s,'testfixture_3j1a_legacy_family',"
                "'PRICE_RETURNS','1.0.0','quant','legacy family fixture','[\"OHLCV\"]'::jsonb,"
                "'[\"close\"]'::jsonb,'1d','event/effective/knowledge bounded',1,'{}'::jsonb,"
                "'reject','reject','reject_future_knowledge',NULL,NULL,'decimal','fixture-v1',%s,NULL)",
                (legacy_definition_id, registered_at),
            )
        # Invariant 2: an arbitrary unsupported family is still rejected.
        with self.assertRaises(Exception), database.transaction() as connection, connection.cursor() as cursor:  # noqa: B017
                cursor.execute(
                    "INSERT INTO feature_definition_versions VALUES (%s,'testfixture_3j1a_bogus_family',"
                    "'BOGUS_FAMILY','1.0.0','quant','bogus family fixture','[\"OHLCV\"]'::jsonb,"
                    "'[\"close\"]'::jsonb,'1d','event/effective/knowledge bounded',1,'{}'::jsonb,"
                    "'reject','reject','reject_future_knowledge',NULL,NULL,'decimal','fixture-v1',%s,NULL)",
                    (uuid4(), registered_at),
                )

        # Invariant 4: a pre-existing V1 hash is completely untouched.
        from trade_platform.feature_authority import FeatureMaterialization

        v1_value = FeatureMaterialization.create(
            feature_id=spread_def.feature_id, instrument_id="fixture:UNREGISTERED_3J1A_LEGACY",
            dataset_version="fixture-v1", event_at=t0, effective_at=t0, knowledge_at=t0,
            computed_at=t0, source_observation_manifest=("raw:legacy",), value=Decimal("0.01"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        feature_authority.materialize(v1_value)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT content_hash, hash_version FROM feature_materializations WHERE materialization_id=%s",
                (v1_value.materialization_id,),
            )
            stored_hash, stored_hash_version = cursor.fetchone()
        self.assertEqual(str(stored_hash).strip(), v1_value.content_hash)
        self.assertEqual(str(stored_hash_version), "V1")

        # Invariant 5: a pre-existing (unrelated) V2 INSTRUMENT materialization
        # is unaffected by anything this module does.
        unrelated_instrument_id = "TESTFIXTURE:3J1A:UNRELATED_INSTRUMENT"
        master.register(
            ProfessionalInstrument(
                instrument_id=unrelated_instrument_id, asset_class=AssetClass.EQUITY,
                instrument_type=InstrumentType.COMMON_STOCK, exchange_name="NASDAQ", venue="XNAS",
                mic="XNAS", canonical_symbol="TESTFIX3J1AEQ", listing_date=date(2023, 1, 3),
                base_currency="USD", quote_currency="USD", settlement_currency="USD",
                contract_multiplier=Decimal(1), contract_size=Decimal(1), tick_size=Decimal("0.01"),
                lot_size=Decimal(1), price_precision=2, quantity_precision=0,
                trading_timezone="America/New_York", market_session_type=SessionType.US_EQUITY,
                representation_kind=RepresentationKind.DIRECT, registered_at=registered_at,
                lifecycle_status=LifecycleStatus.ACTIVE,
            )
        )
        v2_instrument_value = FeatureMaterializationV2.create(
            feature_id=spread_def.feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=unrelated_instrument_id, dataset_version="fixture-v2-instrument",
            event_at=t0, effective_at=t0, knowledge_at=t0, computed_at=t0,
            source_observation_manifest=("fixture:unrelated",), value=Decimal("0.02"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        feature_authority.materialize_subject(v2_instrument_value)
        self.assertEqual(
            feature_authority.latest_as_of_subject(
                spread_def.feature_id, FeatureSubjectType.INSTRUMENT, unrelated_instrument_id,
                "fixture-v2-instrument", t0,
            ),
            (v2_instrument_value,),
        )

        # ==== invariant 6: valid FUTURES_SERIES spread materializes ============
        spread_value = calculator.materialize_normalized_spread(
            feature_id=spread_def.feature_id, curve_id=curve_main.curve_id, series_id=series_id,
            dataset_version_id=d_main.dataset_version_id, method_id=method_carry.method_id,
        )
        self.assertEqual(spread_value.subject_type, FeatureSubjectType.FUTURES_SERIES)
        self.assertEqual(spread_value.subject_id, series_id)
        self.assertEqual(spread_value.dataset_version, str(d_main.dataset_version_id))
        # The two NEAREST-expiration points are FRONT and MID (sequence 1/2),
        # never FRONT and BACK -- curvature (below) is what reads all three.
        # Materialized values are quantized to the value column's own
        # NUMERIC(38,12) scale (see derivatives_features._VALUE_SCALE).
        value_scale = Decimal("1E-12")
        expected_spread = (Decimal("103.00") - Decimal("100.00")) / Decimal("100.00")
        self.assertEqual(spread_value.value, expected_spread.quantize(value_scale))
        self.assertEqual(spread_value.quality_status, FeatureQualityStatus.VALIDATED)  # invariant 23
        self.assertEqual(
            spread_value.event_at, datetime.combine(as_of, time.min, tzinfo=UTC)
        )
        self.assertEqual(spread_value.knowledge_at, curve_main.knowledge_at)

        rate_value = calculator.materialize_annualized_calendar_spread_rate(
            feature_id=rate_def.feature_id, curve_id=curve_main.curve_id, series_id=series_id,
            dataset_version_id=d_main.dataset_version_id, method_id=method_carry.method_id,
        )
        from trade_platform.derivatives_features import year_fraction

        expected_yf = year_fraction(date(2025, 6, 26), date(2025, 9, 26), DayCountConvention.ACT_365F)
        self.assertEqual(rate_value.value, (expected_spread / expected_yf).quantize(value_scale))

        curvature_value = calculator.materialize_curve_curvature(
            feature_id=curvature_def.feature_id, curve_id=curve_main.curve_id, series_id=series_id,
            dataset_version_id=d_main.dataset_version_id, method_id=method_carry.method_id,
        )
        from trade_platform.derivatives_features import curve_curvature

        expected_curvature = curve_curvature(
            front_price=Decimal("100.00"), mid_price=Decimal("103.00"), back_price=Decimal("110.00"),
            front_expiration=date(2025, 6, 26), mid_expiration=date(2025, 9, 26),
            back_expiration=date(2025, 12, 26), convention=DayCountConvention.ACT_365F,
        )
        self.assertEqual(curvature_value.value, expected_curvature.quantize(value_scale))

        # Invariant 24: manifest always carries every required canonical
        # evidence category (never a human-readable label alone).
        manifest = curvature_value.source_observation_manifest
        for required_prefix in (
            "historical_dataset_version_id:", "historical_dataset_content_hash:", "source_id:",
            "curve_id:", "curve_content_hash:", "method_id:", "method_version:",
            "method_content_hash:",
        ):
            self.assertTrue(
                any(token.startswith(required_prefix) for token in manifest),
                f"manifest missing required category {required_prefix!r}",
            )
        self.assertEqual(
            sum(1 for token in manifest if token.startswith("curve_point:")), 3,
        )
        self.assertEqual(
            sum(1 for token in manifest if token.startswith("settlement_observation:")), 3,
        )

        # Invariant 25/26: identical inputs replay idempotently with a
        # deterministic, identical manifest -- never a second row.
        spread_value_replay = calculator.materialize_normalized_spread(
            feature_id=spread_def.feature_id, curve_id=curve_main.curve_id, series_id=series_id,
            dataset_version_id=d_main.dataset_version_id, method_id=method_carry.method_id,
        )
        self.assertEqual(spread_value_replay.source_observation_manifest, spread_value.source_observation_manifest)
        self.assertEqual(spread_value_replay.content_hash, spread_value.content_hash)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM feature_materializations WHERE feature_id=%s AND "
                "subject_type='FUTURES_SERIES' AND subject_id=%s AND dataset_version=%s",
                (spread_def.feature_id, series_id, str(d_main.dataset_version_id)),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 1)

        # Invariant 27: same natural identity, different value -> conflict.
        from dataclasses import replace

        conflicting = replace(spread_value, value=Decimal("99.99"), content_hash="f" * 64)
        with self.assertRaisesRegex(FeatureAuthorityError, "conflict"):
            feature_authority.materialize_subject(conflicting)

        # ==== invariant 7: series/curve not found -> reject =====================
        with self.assertRaisesRegex(DerivativesFeatureError, "curve_not_found"):
            calculator.materialize_normalized_spread(
                feature_id=spread_def.feature_id, curve_id=uuid4(), series_id=series_id,
                dataset_version_id=d_main.dataset_version_id, method_id=method_carry.method_id,
            )

        # ==== invariant 8: an INSTRUMENT subject can never carry a curve-shaped
        # feature identity -- the series_id used as a fabricated INSTRUMENT
        # subject_id is rejected by the existing 3J.0 subject-existence trigger.
        with self.assertRaises(FeatureAuthorityError):
            feature_authority.materialize_subject(
                FeatureMaterializationV2.create(
                    feature_id=spread_def.feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
                    subject_id=series_id, dataset_version=str(d_main.dataset_version_id),
                    event_at=t0, effective_at=t0, knowledge_at=t0, computed_at=t0,
                    source_observation_manifest=("fixture:wrong-subject-type",), value=Decimal("0.09"),
                    quality_status=FeatureQualityStatus.VALIDATED,
                )
            )

        # ==== other-series curve for invariant 9 (curve from wrong series) =====
        other_front_obs = capture_and_normalize(
            "OFRONT", "TESTFIX3J1AOFRONT", settlement_payload("50.00", as_of, "FINAL"),
            event_at=event_at, ingested_at=t0,
        )
        other_back_obs = capture_and_normalize(
            "OBACK", "TESTFIX3J1AOBACK", settlement_payload("52.00", as_of, "FINAL"),
            event_at=event_at, ingested_at=t0,
        )
        d_other = pipeline.seal_dataset(
            source.source_id, "3j1a-dataset-other-series", "3j1a-v1",
            (other_front_obs.normalized_observation_id, other_back_obs.normalized_observation_id),
            dataset_seal_at,
        )
        curve_other, _ = term_structure.derive_curve(
            series_id=other_series_id, dataset_version_id=d_other.dataset_version_id,
            method_id=method_carry.method_id, as_of=as_of, knowledge_at=knowledge_at,
        )
        with self.assertRaisesRegex(DerivativesFeatureError, "curve_series_mismatch"):
            calculator.materialize_normalized_spread(
                feature_id=spread_def.feature_id, curve_id=curve_other.curve_id, series_id=series_id,
                dataset_version_id=d_other.dataset_version_id, method_id=method_carry.method_id,
            )

        # ==== invariant 10: curve dataset differs from requested identity ======
        with self.assertRaisesRegex(DerivativesFeatureError, "curve_dataset_mismatch"):
            calculator.materialize_normalized_spread(
                feature_id=spread_def.feature_id, curve_id=curve_main.curve_id, series_id=series_id,
                dataset_version_id=d_other.dataset_version_id, method_id=method_carry.method_id,
            )

        # ==== invariant 11: no curve can ever reference a dataset that does not
        # exist -- proven directly at the PostgreSQL level (FK), not merely by
        # this module's own defensive re-check. ================================
        with self.assertRaises(Exception), database.transaction() as connection, connection.cursor() as cursor:  # noqa: B017
                cursor.execute(
                    "INSERT INTO futures_term_structure_curves VALUES ("
                    + ",".join(["%s"] * 13) + ")",
                    (
                        uuid4(), series_id, uuid4(), method_carry.method_id, as_of, knowledge_at,
                        "USD", "USD_PER_TROY_OUNCE", 2, False, None, "0" * 64, knowledge_at,
                    ),
                )

        # ==== invariant 12: dataset not yet knowable at the curve's own
        # knowledge_at -- this module's own defensive re-check, proven by
        # directly constructing a curve row (bypassing derive_curve, the same
        # way the 3I.3 test suite proves schema-level defenses) whose
        # knowledge_at predates its own dataset's sealing. ======================
        early_curve_id = uuid4()
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO futures_term_structure_curves VALUES (" + ",".join(["%s"] * 13) + ")",
                (
                    early_curve_id, series_id, d_main.dataset_version_id, method_carry.method_id,
                    as_of, dataset_seal_at - timedelta(hours=1), "USD", "USD_PER_TROY_OUNCE", 2,
                    False, None, "1" * 64, dataset_seal_at - timedelta(hours=1),
                ),
            )
            for sequence, (instrument_id, obs, price, expiry) in enumerate(
                (
                    (front_id, front_obs, Decimal("100.00"), date(2025, 6, 26)),
                    (mid_id, mid_obs, Decimal("103.00"), date(2025, 9, 26)),
                ),
                start=1,
            ):
                cursor.execute(
                    "INSERT INTO futures_term_structure_points VALUES (" + ",".join(["%s"] * 12) + ")",
                    (
                        early_curve_id, sequence, instrument_id, expiry, as_of, False, price,
                        "FINAL", 0, obs.normalized_observation_id, 1, "2" * 64,
                    ),
                )
        with self.assertRaisesRegex(
            DerivativesFeatureError, "dataset_not_knowable_at_curve_knowledge_at"
        ):
            calculator.materialize_normalized_spread(
                feature_id=spread_def.feature_id, curve_id=early_curve_id, series_id=series_id,
                dataset_version_id=d_main.dataset_version_id, method_id=method_carry.method_id,
            )

        # ==== invariants 13/14: fewer points than a feature requires -----------
        solo_obs = capture_and_normalize(
            "SOLO", "TESTFIX3J1ASOLO", settlement_payload("200.00", as_of, "FINAL"),
            event_at=event_at, ingested_at=t0,
        )
        d_solo = pipeline.seal_dataset(
            source.source_id, "3j1a-dataset-solo", "3j1a-v1",
            (solo_obs.normalized_observation_id,), dataset_seal_at,
        )
        method_min1 = FuturesTermStructureMethod(
            method_name="TESTFIX_3J1A_MIN1", method_version=1, minimum_point_count=1,
            settlement_finality_policy=SettlementFinalityPolicy.FINAL_ONLY,
            session_policy=SessionPolicy.SAME_SESSION_STRICT, classification_enabled=False,
            carry_enabled=True, day_count_convention=DayCountConvention.ACT_365F,
            effective_from=registered_at, known_at=registered_at,
        )
        term_structure.register_method(method_min1)
        curve_solo, _ = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_solo.dataset_version_id,
            method_id=method_min1.method_id, as_of=as_of, knowledge_at=knowledge_at,
        )
        self.assertEqual(curve_solo.point_count, 1)
        with self.assertRaisesRegex(DerivativesFeatureError, "insufficient_curve_points_for_spread"):
            calculator.materialize_normalized_spread(
                feature_id=spread_def.feature_id, curve_id=curve_solo.curve_id, series_id=series_id,
                dataset_version_id=d_solo.dataset_version_id, method_id=method_min1.method_id,
            )

        # Two-point curve (front+mid only, from a fresh dataset) -- enough for
        # spread/rate, not enough for curvature. Also demonstrates invariant
        # 15: a genuine third settlement observation exists in the historical
        # evidence for this series/date (back_obs, part of d_main) but is
        # never independently substituted into this curve's frozen 2 points.
        d_two = pipeline.seal_dataset(
            source.source_id, "3j1a-dataset-two-point", "3j1a-v1",
            (front_obs.normalized_observation_id, mid_obs.normalized_observation_id),
            dataset_seal_at,
        )
        curve_two, _points_two = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_two.dataset_version_id,
            method_id=method_carry.method_id, as_of=as_of, knowledge_at=knowledge_at,
        )
        self.assertEqual(curve_two.point_count, 2)
        spread_two = calculator.materialize_normalized_spread(
            feature_id=spread_def.feature_id, curve_id=curve_two.curve_id, series_id=series_id,
            dataset_version_id=d_two.dataset_version_id, method_id=method_carry.method_id,
        )
        self.assertEqual(spread_two.value, expected_spread.quantize(value_scale))
        with self.assertRaisesRegex(
            DerivativesFeatureError, "insufficient_curve_points_for_curvature"
        ):
            calculator.materialize_curve_curvature(
                feature_id=curvature_def.feature_id, curve_id=curve_two.curve_id, series_id=series_id,
                dataset_version_id=d_two.dataset_version_id, method_id=method_carry.method_id,
            )

        # ==== invariant 18: missing carry/day-count convention -> fail closed ==
        method_no_carry = FuturesTermStructureMethod(
            method_name="TESTFIX_3J1A_NO_CARRY", method_version=1, minimum_point_count=2,
            settlement_finality_policy=SettlementFinalityPolicy.FINAL_ONLY,
            session_policy=SessionPolicy.SAME_SESSION_STRICT, classification_enabled=False,
            carry_enabled=False, effective_from=registered_at, known_at=registered_at,
        )
        term_structure.register_method(method_no_carry)
        # Reuse d_main -- the same sealed dataset can back curves under two
        # different methods; a distinct curve identity only needs a distinct
        # method_id here, not a second (content-hash-colliding) dataset seal.
        curve_no_carry, _ = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_main.dataset_version_id,
            method_id=method_no_carry.method_id, as_of=as_of, knowledge_at=knowledge_at,
        )
        with self.assertRaisesRegex(DerivativesFeatureError, "missing_carry_day_count_convention"):
            calculator.materialize_annualized_calendar_spread_rate(
                feature_id=rate_def.feature_id, curve_id=curve_no_carry.curve_id, series_id=series_id,
                dataset_version_id=d_main.dataset_version_id, method_id=method_no_carry.method_id,
            )
        with self.assertRaisesRegex(DerivativesFeatureError, "missing_carry_day_count_convention"):
            calculator.materialize_curve_curvature(
                feature_id=curvature_def.feature_id, curve_id=curve_no_carry.curve_id,
                series_id=series_id, dataset_version_id=d_main.dataset_version_id,
                method_id=method_no_carry.method_id,
            )
        # (Spread itself needs no carry convention at all -- its formula never
        # calls year_fraction -- already proven independently by the pure
        # unit tests in tests/test_derivatives_features.py.)

        # ==== invariant 17 (Postgres-level case): equal front/mid expiration
        # dates -> zero year fraction -> annualized rate fails closed even
        # though the curve itself derived successfully. =========================
        tie_a_id = register_instrument(
            "TIEA", "TESTFIX3J1ATIEA", expiration_date=date(2025, 3, 26)
        )
        tie_b_id = register_instrument(
            "TIEB", "TESTFIX3J1ATIEB", expiration_date=date(2025, 3, 26)
        )
        # Distinct (contract_year, contract_month) identity -- required by
        # the schema's own uniqueness constraint -- but a deliberately
        # identical expiration_date, since nothing ties expiration_date to
        # contract_month; this is exactly the tie condition being tested.
        specify(tie_a_id, series_id, "TIEA", 2025, 3, date(2025, 3, 26))
        specify(tie_b_id, series_id, "TIEB", 2025, 4, date(2025, 3, 26))
        as_of_tie = date(2025, 1, 20)
        tie_event_at = datetime(2025, 1, 20, 18, tzinfo=UTC)
        tie_a_obs = capture_and_normalize(
            "TIEA", "TESTFIX3J1ATIEA", settlement_payload("70.00", as_of_tie, "FINAL"),
            event_at=tie_event_at, ingested_at=tie_event_at,
        )
        tie_b_obs = capture_and_normalize(
            "TIEB", "TESTFIX3J1ATIEB", settlement_payload("71.00", as_of_tie, "FINAL"),
            event_at=tie_event_at, ingested_at=tie_event_at,
        )
        d_tie = pipeline.seal_dataset(
            source.source_id, "3j1a-dataset-tie", "3j1a-v1",
            (tie_a_obs.normalized_observation_id, tie_b_obs.normalized_observation_id),
            tie_event_at + timedelta(hours=1),
        )
        curve_tie, _ = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_tie.dataset_version_id,
            method_id=method_carry.method_id, as_of=as_of_tie,
            knowledge_at=tie_event_at + timedelta(hours=2),
        )
        with self.assertRaisesRegex(DerivativesFeatureError, "non_positive_year_fraction"):
            calculator.materialize_annualized_calendar_spread_rate(
                feature_id=rate_def.feature_id, curve_id=curve_tie.curve_id, series_id=series_id,
                dataset_version_id=d_tie.dataset_version_id, method_id=method_carry.method_id,
            )

        # ==== invariant 22: a preliminary point produces DEGRADED, not
        # VALIDATED. ==============================================================
        method_allow_prelim = FuturesTermStructureMethod(
            method_name="TESTFIX_3J1A_ALLOW_PRELIM", method_version=1, minimum_point_count=2,
            settlement_finality_policy=SettlementFinalityPolicy.LATEST_KNOWN_ALLOW_PRELIMINARY,
            session_policy=SessionPolicy.SAME_SESSION_STRICT, classification_enabled=False,
            carry_enabled=True, day_count_convention=DayCountConvention.ACT_365F,
            effective_from=registered_at, known_at=registered_at,
        )
        term_structure.register_method(method_allow_prelim)
        as_of_prelim = date(2025, 3, 21)
        prelim_event_at = datetime(2025, 3, 21, 18, tzinfo=UTC)
        front_prelim = capture_and_normalize(
            "ZFRONT", "TESTFIX3J1AZFRONT", settlement_payload("101.00", as_of_prelim, "PRELIMINARY"),
            event_at=prelim_event_at, ingested_at=prelim_event_at,
        )
        mid_prelim = capture_and_normalize(
            "MMID", "TESTFIX3J1AMMID", settlement_payload("104.00", as_of_prelim, "PRELIMINARY"),
            event_at=prelim_event_at, ingested_at=prelim_event_at,
        )
        d_prelim = pipeline.seal_dataset(
            source.source_id, "3j1a-dataset-prelim", "3j1a-v1",
            (front_prelim.normalized_observation_id, mid_prelim.normalized_observation_id),
            prelim_event_at + timedelta(hours=1),
        )
        curve_prelim, _ = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_prelim.dataset_version_id,
            method_id=method_allow_prelim.method_id, as_of=as_of_prelim,
            knowledge_at=prelim_event_at + timedelta(hours=2),
        )
        spread_prelim = calculator.materialize_normalized_spread(
            feature_id=spread_def.feature_id, curve_id=curve_prelim.curve_id, series_id=series_id,
            dataset_version_id=d_prelim.dataset_version_id, method_id=method_allow_prelim.method_id,
        )
        self.assertEqual(spread_prelim.quality_status, FeatureQualityStatus.DEGRADED)

        # ==== invariant 21: a later, independently-derived curve/dataset never
        # leaks into or mutates an earlier materialization; each lives under
        # its own dataset_version identity forever. ============================
        front_revised = capture_and_normalize(
            "ZFRONT", "TESTFIX3J1AZFRONT", settlement_payload("100.50", as_of, "FINAL"),
            event_at=event_at, ingested_at=t0 + timedelta(days=2), revision=1,
        )
        d_late = pipeline.seal_dataset(
            source.source_id, "3j1a-dataset-late-revision", "3j1a-v1",
            (front_revised.normalized_observation_id, mid_obs.normalized_observation_id),
            t0 + timedelta(days=2, hours=1),
        )
        curve_late, _ = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_late.dataset_version_id,
            method_id=method_carry.method_id, as_of=as_of, knowledge_at=d_late.created_at,
        )
        spread_late = calculator.materialize_normalized_spread(
            feature_id=spread_def.feature_id, curve_id=curve_late.curve_id, series_id=series_id,
            dataset_version_id=d_late.dataset_version_id, method_id=method_carry.method_id,
        )
        self.assertNotEqual(spread_late.value, spread_two.value)
        self.assertNotEqual(spread_late.dataset_version, spread_two.dataset_version)
        far_future_decision_at = spread_late.knowledge_at + timedelta(days=365)
        # Querying under the EARLIER dataset's identity, even far in the
        # future, never returns the later curve's row.
        self.assertEqual(
            feature_authority.latest_as_of_subject(
                spread_def.feature_id, FeatureSubjectType.FUTURES_SERIES, series_id,
                spread_two.dataset_version, far_future_decision_at,
            ),
            (spread_two,),
        )
        self.assertEqual(
            feature_authority.latest_as_of_subject(
                spread_def.feature_id, FeatureSubjectType.FUTURES_SERIES, series_id,
                spread_late.dataset_version, far_future_decision_at,
            ),
            (spread_late,),
        )

        # ==== invariant 28: immutable evidence rejects UPDATE/DELETE ===========
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE feature_materializations SET value=0 WHERE materialization_id=%s",
                (spread_value.materialization_id,),
            )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM feature_materializations WHERE materialization_id=%s",
                (spread_value.materialization_id,),
            )

        # ==== invariant 29: restart preserves materializations =================
        database.close()
        reopened = PostgresDatabase(dsn)
        reopened_authority = PostgresFeatureAuthority(reopened)
        self.assertEqual(
            reopened_authority.latest_as_of_subject(
                spread_def.feature_id, FeatureSubjectType.FUTURES_SERIES, series_id,
                str(d_main.dataset_version_id), far_future_decision_at,
            ),
            (spread_value,),
        )

        # ==== invariant 31: no new parallel feature-materialization authority ==
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
                ("%derivative%", "%3j1%"),
            )
            self.assertEqual(cursor.fetchall(), [])
        reopened.close()

        # Sanity: exactly the seven expected calculation-version/unit facts
        # this module claims, still true at the very end of the run.
        self.assertEqual(spread_def.units, "dimensionless")
        self.assertEqual(rate_def.units, "1/year")
        self.assertEqual(curvature_def.units, "year^-2")
        self.assertEqual(spread_def.calculation_version, CALCULATION_VERSION)
        self.assertEqual(
            {FUTURES_FRONT_BACK_NORMALIZED_SPREAD, FUTURES_ANNUALIZED_CALENDAR_SPREAD_RATE,
             FUTURES_CURVE_CURVATURE},
            {spread_def.name, rate_def.name, curvature_def.name},
        )
        self.assertEqual(FeatureFamily.DERIVATIVES.value, "DERIVATIVES")


if __name__ == "__main__":
    unittest.main()
