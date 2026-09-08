"""Real PostgreSQL evidence for Module 3I.3 deterministic futures term structure.

Fixture instruments live under ``operator_dashboard.RESERVED_TEST_FIXTURE_PREFIX``
so they cannot displace real instruments on the shared CI database's operator
discovery page.

Every price, quantity, contract date and provider identifier is a FIXTURE.
Nothing here was retrieved from or verified against any exchange; no real
settlement price or contract specification is claimed. FUTURES ONLY: no crypto
dated future participates anywhere in this file.
"""

import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

SETTLEMENT = "SETTLEMENT_PRICE"


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class FuturesTermStructurePostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_futures_term_structure_end_to_end(self) -> None:
        from trade_platform.domain import AssetClass
        from trade_platform.futures_contracts import (
            FuturesContractSeries,
            FuturesContractSpecification,
            PostgresFuturesContractAuthority,
            SettlementType,
        )
        from trade_platform.futures_term_structure import (
            CurveClassification,
            FuturesTermStructureDerivationError,
            FuturesTermStructureMethod,
            FuturesTermStructureMethodError,
            PostgresFuturesTermStructureAuthority,
            SessionPolicy,
            SettlementFinalityPolicy,
        )
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            AssetScope,
            AuthorizedHistoricalSource,
            HistoricalMarketDataError,
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

        registered_at = datetime(2024, 1, 2, tzinfo=UTC)
        namespace = "TESTFIX_TS_PROVIDER"
        venue = "XCEC"
        series_id = "TESTFIXTURE:TS:SERIES:GC"
        other_series_id = "TESTFIXTURE:TS:SERIES:OTHER"
        crypto_series_id = "TESTFIXTURE:TS:SERIES:CRYPTO"

        # ---- instrument fixtures ----------------------------------------------
        def register_instrument(
            suffix: str, symbol: str, *, instrument_type: InstrumentType,
            asset_class: AssetClass = AssetClass.COMMODITY,
            representation: RepresentationKind = RepresentationKind.FUTURE,
            continuous_parent: str | None = None, instrument_venue: str = venue,
            expiration_date: date = date(2026, 12, 26), last_trade_date: date | None = None,
        ) -> str:
            instrument_id = f"TESTFIXTURE:TS:{suffix}"
            extra: dict[str, object] = {}
            if instrument_type is InstrumentType.FUTURE:
                extra = {
                    "contract_code": symbol,
                    "expiration_date": expiration_date,
                    "first_notice_date": None,
                    "last_trade_date": last_trade_date or (expiration_date - timedelta(days=2)),
                    "roll_rule": "TESTFIX_TS_ROLL_V1",
                    "continuous_parent_id": continuous_parent,
                }
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id, asset_class=asset_class,
                    instrument_type=instrument_type, exchange_name="COMEX",
                    venue=instrument_venue, mic=instrument_venue, canonical_symbol=symbol,
                    listing_date=date(2023, 1, 3), base_currency="USD", quote_currency="USD",
                    settlement_currency="USD", contract_multiplier=Decimal(100),
                    contract_size=Decimal(100), tick_size=Decimal("0.10"),
                    lot_size=Decimal(1), price_precision=2, quantity_precision=0,
                    trading_timezone="America/New_York",
                    market_session_type=SessionType.FUTURES_23X5,
                    representation_kind=representation, registered_at=registered_at,
                    lifecycle_status=LifecycleStatus.ACTIVE, **extra,  # type: ignore[arg-type]
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

        front_id = register_instrument(
            "ZFRONT", "TESTFIXTSZFRONT", instrument_type=InstrumentType.FUTURE,
            expiration_date=date(2025, 12, 26),
        )
        back_id = register_instrument(
            "ABACK", "TESTFIXTSABACK", instrument_type=InstrumentType.FUTURE,
            expiration_date=date(2026, 3, 26),
        )
        gap_id = register_instrument(
            "GAP", "TESTFIXTSGAP", instrument_type=InstrumentType.FUTURE,
            expiration_date=date(2026, 6, 26),
        )
        mismatch_id = register_instrument(
            "MISMATCH", "TESTFIXTSMISMATCH", instrument_type=InstrumentType.FUTURE,
            expiration_date=date(2026, 9, 26),
        )
        equity_id = register_instrument(
            "EQUITY", "TESTFIXTSEQ", instrument_type=InstrumentType.COMMON_STOCK,
            asset_class=AssetClass.EQUITY, representation=RepresentationKind.DIRECT,
            instrument_venue="XNAS",
        )
        continuous_id = register_instrument(
            "CONTINUOUS", "TESTFIXTSCONT", instrument_type=InstrumentType.FUTURE,
            continuous_parent=front_id, expiration_date=date(2027, 12, 26),
        )
        otherseries_id = register_instrument(
            "OTHERCTR", "TESTFIXTSOTHER", instrument_type=InstrumentType.FUTURE,
            expiration_date=date(2026, 1, 26),
        )

        def make_series(sid: str, root: str, asset_class: AssetClass) -> FuturesContractSeries:
            return FuturesContractSeries(
                series_id=sid, root_symbol=root, exchange_name="COMEX", venue=venue, mic=venue,
                asset_class=asset_class, underlying_reference="Gold", currency="USD",
                contract_multiplier=Decimal(100), unit_of_measure="TROY_OUNCE",
                tick_size=Decimal("0.10"), tick_value=Decimal("10.00"), price_precision=2,
                quantity_precision=0, settlement_type=SettlementType.CASH_SETTLED,
                trading_timezone="America/New_York", session_type=SessionType.FUTURES_23X5,
                registered_at=registered_at, source_reference="fixture:series",
            )

        series = make_series(series_id, "TESTFIXTSGC", AssetClass.COMMODITY)
        other_series = make_series(other_series_id, "TESTFIXTSOTHERROOT", AssetClass.COMMODITY)
        crypto_series = make_series(crypto_series_id, "TESTFIXTSCRYPTOROOT", AssetClass.CRYPTO)
        contracts.register_series(series)
        contracts.register_series(other_series)
        contracts.register_series(crypto_series)

        def specify(instrument_id: str, sid: str, code: str, year: int, month: int) -> None:
            spec = FuturesContractSpecification(
                instrument_id=instrument_id, series_id=sid, contract_code=code,
                contract_year=year, contract_month=month,
                first_trade_date=date(2023, 1, 3),
                last_trade_date=date(year, month, 1) + timedelta(days=200),
                expiration_date=date(year, month, 1) + timedelta(days=202),
                settlement_date=date(year, month, 1) + timedelta(days=204),
                settlement_type=SettlementType.CASH_SETTLED,
                contract_multiplier=Decimal(100), tick_size=Decimal("0.10"),
                tick_value=Decimal("10.00"), registered_at=registered_at,
                source_reference="fixture:contract",
            )
            contracts.specify_contract(spec)

        specify(front_id, series_id, "ZFRONT", 2025, 6)
        specify(back_id, series_id, "ABACK", 2025, 9)
        specify(gap_id, series_id, "GAPCTR", 2025, 12)
        specify(mismatch_id, series_id, "MISMTCH", 2026, 3)
        specify(otherseries_id, other_series_id, "OTHERCTR", 2025, 7)

        # ---- source authorization -----------------------------------------
        source = AuthorizedHistoricalSource(
            provider="TESTFIX_TS", dataset_name="term-structure-fixture",
            provider_identifier_namespace=namespace, provider_terms_version="v1",
            authorization_reference="fixture://authorization/term-structure",
            authorized_at=registered_at, created_at=registered_at,
            asset_scope=AssetScope.FUTURES.value,
            authorized_observation_kinds=frozenset(
                {ObservationKind.OHLCV, ObservationKind.SETTLEMENT_PRICE}
            ),
        )
        pipeline.register_source(source)

        def raw(
            kind: ObservationKind, payload: dict[str, object], *, identifier: str,
            symbol: str, revision: int = 0,
            event_at: datetime = datetime(2025, 6, 20, 18, tzinfo=UTC),
            ingested_at: datetime | None = None,
        ) -> RawHistoricalObservation:
            return RawHistoricalObservation(
                source_id=source.source_id, observation_kind=kind, provider_identifier=identifier,
                provider_symbol=symbol, exchange=venue, event_at=event_at, effective_at=event_at,
                ingested_at=ingested_at or event_at, adjustment_status=AdjustmentStatus.AS_REPORTED,
                revision=revision, provenance_uri=f"fixture://{kind.value}/{identifier}/{revision}",
                raw_payload=payload,
            )

        def settlement_payload(
            price: str, settlement_date: date, finality: str, *,
            currency: str = "USD", unit: str = "USD_PER_TROY_OUNCE",
        ) -> dict[str, object]:
            return {
                "settlement_price": price, "price_currency": currency,
                "settlement_date": settlement_date.isoformat(),
                "settlement_effective_at": f"{settlement_date.isoformat()}T18:00:00+00:00",
                "finality": finality, "quote_unit": unit,
            }

        def capture_and_normalize(
            identifier: str, symbol: str, kind: ObservationKind, payload: dict[str, object],
            *, event_at: datetime, ingested_at: datetime, revision: int = 0,
        ) -> object:
            (raw_id,) = pipeline.capture_raw(
                [raw(kind, payload, identifier=identifier, symbol=symbol, revision=revision,
                     event_at=event_at, ingested_at=ingested_at)]
            )
            return pipeline.normalize(raw_id, "ts-v1", ingested_at)

        # ==== main curve: front + back FINAL settlements on 2025-06-20 ========
        as_of = date(2025, 6, 20)
        t0 = datetime(2025, 6, 20, 19, tzinfo=UTC)
        front_event_at = datetime(2025, 6, 20, 18, tzinfo=UTC)

        front_rev0 = capture_and_normalize(
            "ZFRONT", "TESTFIXTSZFRONT", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("100.00", as_of, "FINAL"),
            event_at=front_event_at, ingested_at=t0,
        )
        back_original = capture_and_normalize(
            "ABACK", "TESTFIXTSABACK", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("105.00", as_of, "FINAL"),
            event_at=front_event_at, ingested_at=t0,
        )
        # Invariant 2/14/15: an OHLCV observation exists for a contract with no
        # settlement at all. It must never stand in for a missing settlement,
        # and no interpolated point may fill the gap.
        gap_ohlcv = capture_and_normalize(
            "GAP", "TESTFIXTSGAP", ObservationKind.OHLCV,
            {
                "open": "1.0", "high": "1.0", "low": "1.0", "close": "1.0", "volume": "1",
                "interval": "1d",
            },
            event_at=front_event_at, ingested_at=t0,
        )
        dataset_seal_at = datetime(2025, 6, 20, 20, tzinfo=UTC)
        d1 = pipeline.seal_dataset(
            source.source_id, "ts-dataset-main", "ts-v1",
            (
                front_rev0.normalized_observation_id, back_original.normalized_observation_id,
                gap_ohlcv.normalized_observation_id,
            ),
            dataset_seal_at,
        )

        method_a = FuturesTermStructureMethod(
            method_name="TESTFIX_TS_STRICT_FINAL_CLASSIFY_TIGHT", method_version=1,
            minimum_point_count=2, settlement_finality_policy=SettlementFinalityPolicy.FINAL_ONLY,
            session_policy=SessionPolicy.SAME_SESSION_STRICT, classification_enabled=True,
            classification_minimum_point_count=2, classification_flat_threshold=Decimal("0.01"),
            carry_enabled=False, effective_from=registered_at, known_at=registered_at,
        )
        term_structure.register_method(method_a)
        knowledge_at = dataset_seal_at + timedelta(minutes=1)

        curve1, points1 = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d1.dataset_version_id,
            method_id=method_a.method_id, as_of=as_of, knowledge_at=knowledge_at,
        )
        self.assertEqual(curve1.point_count, 2)
        self.assertFalse(curve1.contains_preliminary_point)
        self.assertEqual(curve1.classification, CurveClassification.CONTANGO)
        # Invariant 16: ordering follows 3H.1 expiration identity, never symbol
        # text -- "ABACK" sorts before "ZFRONT" lexicographically, but the front
        # (June) contract must still come first because it expires first.
        self.assertEqual([point.instrument_id for point in points1], [front_id, back_id])
        # Invariant 2/14/15: the gap contract never appears despite its OHLCV row.
        self.assertNotIn(gap_id, {point.instrument_id for point in points1})

        # Invariant 22: identical inputs are idempotent, never a fresh row.
        curve1_replay, _ = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d1.dataset_version_id,
            method_id=method_a.method_id, as_of=as_of, knowledge_at=knowledge_at,
        )
        self.assertEqual(curve1_replay.curve_id, curve1.curve_id)
        self.assertEqual(curve1_replay.content_hash, curve1.content_hash)

        # Invariant 21: a different method (even same series/dataset/as_of/
        # knowledge_at) produces a different curve identity and content hash.
        method_b = FuturesTermStructureMethod(
            method_name="TESTFIX_TS_STRICT_FINAL_CLASSIFY_LOOSE", method_version=1,
            minimum_point_count=2, settlement_finality_policy=SettlementFinalityPolicy.FINAL_ONLY,
            session_policy=SessionPolicy.SAME_SESSION_STRICT, classification_enabled=True,
            classification_minimum_point_count=2, classification_flat_threshold=Decimal("0.5"),
            carry_enabled=False, effective_from=registered_at, known_at=registered_at,
        )
        term_structure.register_method(method_b)
        curve2, _ = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d1.dataset_version_id,
            method_id=method_b.method_id, as_of=as_of, knowledge_at=knowledge_at,
        )
        self.assertNotEqual(curve2.curve_id, curve1.curve_id)
        self.assertNotEqual(curve2.content_hash, curve1.content_hash)
        self.assertEqual(curve2.classification, CurveClassification.FLAT)

        # Invariant 25: fewer than the method's minimum point count is rejected.
        method_min3 = FuturesTermStructureMethod(
            method_name="TESTFIX_TS_STRICT_MIN3", method_version=1, minimum_point_count=3,
            settlement_finality_policy=SettlementFinalityPolicy.FINAL_ONLY,
            session_policy=SessionPolicy.SAME_SESSION_STRICT, classification_enabled=False,
            carry_enabled=False, effective_from=registered_at, known_at=registered_at,
        )
        term_structure.register_method(method_min3)
        with self.assertRaises(FuturesTermStructureDerivationError) as raised:
            term_structure.derive_curve(
                series_id=series_id, dataset_version_id=d1.dataset_version_id,
                method_id=method_min3.method_id, as_of=as_of, knowledge_at=knowledge_at,
            )
        self.assertIn("insufficient_point_count", str(raised.exception))

        # Invariant 28: a series flagged CRYPTO can never enter this authority,
        # regardless of dataset/method validity.
        with self.assertRaises(FuturesTermStructureDerivationError) as raised:
            term_structure.derive_curve(
                series_id=crypto_series_id, dataset_version_id=d1.dataset_version_id,
                method_id=method_a.method_id, as_of=as_of, knowledge_at=knowledge_at,
            )
        self.assertIn("crypto_dated_future_not_eligible", str(raised.exception))

        # ==== preliminary-only scenario (invariants 10 and 11) ================
        as_of_prelim = date(2025, 6, 21)
        prelim_event_at = datetime(2025, 6, 21, 18, tzinfo=UTC)
        front_prelim = capture_and_normalize(
            "ZFRONT", "TESTFIXTSZFRONT", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("101.00", as_of_prelim, "PRELIMINARY"),
            event_at=prelim_event_at, ingested_at=prelim_event_at,
        )
        back_prelim = capture_and_normalize(
            "ABACK", "TESTFIXTSABACK", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("106.00", as_of_prelim, "PRELIMINARY"),
            event_at=prelim_event_at, ingested_at=prelim_event_at,
        )
        d_prelim = pipeline.seal_dataset(
            source.source_id, "ts-dataset-prelim", "ts-v1",
            (front_prelim.normalized_observation_id, back_prelim.normalized_observation_id),
            prelim_event_at + timedelta(hours=1),
        )
        prelim_knowledge_at = prelim_event_at + timedelta(hours=2)

        # Invariant 10: FINAL_ONLY excludes every preliminary point -- absent,
        # never a silent fallback -- leaving too few points for the curve.
        with self.assertRaises(FuturesTermStructureDerivationError) as raised:
            term_structure.derive_curve(
                series_id=series_id, dataset_version_id=d_prelim.dataset_version_id,
                method_id=method_a.method_id, as_of=as_of_prelim,
                knowledge_at=prelim_knowledge_at,
            )
        self.assertIn("no_eligible_settlement_points", str(raised.exception))

        # Invariant 11: an explicit allowing method accepts the same evidence.
        method_allow_prelim = FuturesTermStructureMethod(
            method_name="TESTFIX_TS_STRICT_ALLOW_PRELIM", method_version=1,
            minimum_point_count=2,
            settlement_finality_policy=SettlementFinalityPolicy.LATEST_KNOWN_ALLOW_PRELIMINARY,
            session_policy=SessionPolicy.SAME_SESSION_STRICT, classification_enabled=False,
            carry_enabled=False, effective_from=registered_at, known_at=registered_at,
        )
        term_structure.register_method(method_allow_prelim)
        curve_prelim, points_prelim = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_prelim.dataset_version_id,
            method_id=method_allow_prelim.method_id, as_of=as_of_prelim,
            knowledge_at=prelim_knowledge_at,
        )
        self.assertTrue(curve_prelim.contains_preliminary_point)
        self.assertEqual(curve_prelim.point_count, 2)
        self.assertTrue(all(
            point.settlement_finality.value == "PRELIMINARY" for point in points_prelim
        ))

        # ==== mixed currency (invariant 6) =====================================
        as_of_currency = date(2025, 6, 23)
        currency_event_at = datetime(2025, 6, 23, 18, tzinfo=UTC)
        front_usd = capture_and_normalize(
            "ZFRONT", "TESTFIXTSZFRONT", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("102.00", as_of_currency, "FINAL", currency="USD"),
            event_at=currency_event_at, ingested_at=currency_event_at,
        )
        back_eur = capture_and_normalize(
            "ABACK", "TESTFIXTSABACK", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("107.00", as_of_currency, "FINAL", currency="EUR"),
            event_at=currency_event_at, ingested_at=currency_event_at,
        )
        d_currency = pipeline.seal_dataset(
            source.source_id, "ts-dataset-currency", "ts-v1",
            (front_usd.normalized_observation_id, back_eur.normalized_observation_id),
            currency_event_at + timedelta(hours=1),
        )
        with self.assertRaises(FuturesTermStructureDerivationError) as raised:
            term_structure.derive_curve(
                series_id=series_id, dataset_version_id=d_currency.dataset_version_id,
                method_id=method_a.method_id, as_of=as_of_currency,
                knowledge_at=currency_event_at + timedelta(hours=2),
            )
        self.assertIn("mixed_quote_currency_in_curve", str(raised.exception))

        # ==== mixed quote unit (invariant 7) ===================================
        as_of_unit = date(2025, 6, 24)
        unit_event_at = datetime(2025, 6, 24, 18, tzinfo=UTC)
        front_unit_a = capture_and_normalize(
            "ZFRONT", "TESTFIXTSZFRONT", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("103.00", as_of_unit, "FINAL", unit="USD_PER_TROY_OUNCE"),
            event_at=unit_event_at, ingested_at=unit_event_at,
        )
        back_unit_b = capture_and_normalize(
            "ABACK", "TESTFIXTSABACK", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("108.00", as_of_unit, "FINAL", unit="USD_PER_CONTRACT"),
            event_at=unit_event_at, ingested_at=unit_event_at,
        )
        d_unit = pipeline.seal_dataset(
            source.source_id, "ts-dataset-unit", "ts-v1",
            (front_unit_a.normalized_observation_id, back_unit_b.normalized_observation_id),
            unit_event_at + timedelta(hours=1),
        )
        with self.assertRaises(FuturesTermStructureDerivationError) as raised:
            term_structure.derive_curve(
                series_id=series_id, dataset_version_id=d_unit.dataset_version_id,
                method_id=method_a.method_id, as_of=as_of_unit,
                knowledge_at=unit_event_at + timedelta(hours=2),
            )
        self.assertIn("mixed_quote_unit_in_curve", str(raised.exception))

        # ==== mixed session under strict policy (invariant 12) ================
        as_of_session = date(2025, 6, 25)
        session_event_at = datetime(2025, 6, 25, 18, tzinfo=UTC)
        front_session = capture_and_normalize(
            "ZFRONT", "TESTFIXTSZFRONT", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("104.00", as_of_session, "FINAL"),
            event_at=session_event_at, ingested_at=session_event_at,
        )
        # The back leg only settled on an unrelated earlier date -- a different
        # session -- using an event_at not already claimed by another fixture.
        stale_session_date = date(2025, 6, 16)
        back_wrong_session = capture_and_normalize(
            "ABACK", "TESTFIXTSABACK", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("109.00", stale_session_date, "FINAL"),
            event_at=datetime(2025, 6, 16, 18, tzinfo=UTC),
            ingested_at=datetime(2025, 6, 16, 18, tzinfo=UTC),
        )
        d_session = pipeline.seal_dataset(
            source.source_id, "ts-dataset-session", "ts-v1",
            (front_session.normalized_observation_id, back_wrong_session.normalized_observation_id),
            session_event_at + timedelta(hours=1),
        )
        with self.assertRaises(FuturesTermStructureDerivationError) as raised:
            term_structure.derive_curve(
                series_id=series_id, dataset_version_id=d_session.dataset_version_id,
                method_id=method_a.method_id, as_of=as_of_session,
                knowledge_at=session_event_at + timedelta(hours=2),
            )
        # The back leg is silently omitted (never mixed in), which then leaves
        # too few points -- fail closed, never a same-day fiction.
        self.assertIn("insufficient_point_count", str(raised.exception))

        # ==== staleness tolerance: reject beyond tolerance (invariant 13) =====
        as_of_stale = date(2025, 6, 27)
        stale_event_at = datetime(2025, 6, 27, 18, tzinfo=UTC)
        front_stale = capture_and_normalize(
            "ZFRONT", "TESTFIXTSZFRONT", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("110.00", as_of_stale, "FINAL"),
            event_at=stale_event_at, ingested_at=stale_event_at,
        )
        # A date well outside the tolerance window, and not reused by any other
        # fixture leg (event_at must stay unique per source/identifier/revision).
        too_stale_date = date(2025, 6, 13)
        back_too_stale = capture_and_normalize(
            "ABACK", "TESTFIXTSABACK", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("115.00", too_stale_date, "FINAL"),
            event_at=datetime(2025, 6, 13, 18, tzinfo=UTC),
            ingested_at=datetime(2025, 6, 13, 18, tzinfo=UTC),
        )
        d_stale = pipeline.seal_dataset(
            source.source_id, "ts-dataset-too-stale", "ts-v1",
            (front_stale.normalized_observation_id, back_too_stale.normalized_observation_id),
            stale_event_at + timedelta(hours=1),
        )
        method_tolerance_1d = FuturesTermStructureMethod(
            method_name="TESTFIX_TS_TOLERANCE_1D", method_version=1, minimum_point_count=2,
            settlement_finality_policy=SettlementFinalityPolicy.FINAL_ONLY,
            session_policy=SessionPolicy.SAME_SESSION_WITH_STALENESS_TOLERANCE,
            max_staleness_days=1, classification_enabled=False, carry_enabled=False,
            effective_from=registered_at, known_at=registered_at,
        )
        term_structure.register_method(method_tolerance_1d)
        with self.assertRaises(FuturesTermStructureDerivationError) as raised:
            term_structure.derive_curve(
                series_id=series_id, dataset_version_id=d_stale.dataset_version_id,
                method_id=method_tolerance_1d.method_id, as_of=as_of_stale,
                knowledge_at=stale_event_at + timedelta(hours=2),
            )
        self.assertIn("insufficient_point_count", str(raised.exception))

        # ==== staleness tolerance: accept within tolerance (positive case) ====
        as_of_tolerant = date(2025, 6, 28)
        tolerant_event_at = datetime(2025, 6, 28, 18, tzinfo=UTC)
        front_tolerant = capture_and_normalize(
            "ZFRONT", "TESTFIXTSZFRONT", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("120.00", as_of_tolerant, "FINAL"),
            event_at=tolerant_event_at, ingested_at=tolerant_event_at,
        )
        mildly_stale_date = as_of_tolerant - timedelta(days=2)
        back_mildly_stale = capture_and_normalize(
            "ABACK", "TESTFIXTSABACK", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("125.00", mildly_stale_date, "FINAL"),
            event_at=tolerant_event_at - timedelta(days=2),
            ingested_at=tolerant_event_at - timedelta(days=2),
        )
        d_tolerant = pipeline.seal_dataset(
            source.source_id, "ts-dataset-tolerant", "ts-v1",
            (front_tolerant.normalized_observation_id, back_mildly_stale.normalized_observation_id),
            tolerant_event_at + timedelta(hours=1),
        )
        method_tolerance_2d = FuturesTermStructureMethod(
            method_name="TESTFIX_TS_TOLERANCE_2D", method_version=1, minimum_point_count=2,
            settlement_finality_policy=SettlementFinalityPolicy.FINAL_ONLY,
            session_policy=SessionPolicy.SAME_SESSION_WITH_STALENESS_TOLERANCE,
            max_staleness_days=2, classification_enabled=False, carry_enabled=False,
            effective_from=registered_at, known_at=registered_at,
        )
        term_structure.register_method(method_tolerance_2d)
        curve_tolerant, points_tolerant = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_tolerant.dataset_version_id,
            method_id=method_tolerance_2d.method_id, as_of=as_of_tolerant,
            knowledge_at=tolerant_event_at + timedelta(hours=2),
        )
        self.assertEqual(curve_tolerant.point_count, 2)
        by_instrument = {point.instrument_id: point for point in points_tolerant}
        self.assertFalse(by_instrument[front_id].is_stale)
        self.assertTrue(by_instrument[back_id].is_stale)

        # ==== revision leakage across knowledge_at (invariants 8, 9, 20, 24) ===
        front_rev1 = capture_and_normalize(
            "ZFRONT", "TESTFIXTSZFRONT", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("100.50", as_of, "FINAL"),
            event_at=front_event_at, ingested_at=t0 + timedelta(days=2), revision=1,
        )
        d_early = pipeline.seal_dataset(
            source.source_id, "ts-dataset-revision-early", "ts-v1",
            (front_rev0.normalized_observation_id, back_original.normalized_observation_id),
            t0 + timedelta(hours=1),
        )
        d_late = pipeline.seal_dataset(
            source.source_id, "ts-dataset-revision-late", "ts-v1",
            (
                front_rev0.normalized_observation_id, front_rev1.normalized_observation_id,
                back_original.normalized_observation_id,
            ),
            t0 + timedelta(days=2, hours=1),
        )
        curve_early, points_early = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_early.dataset_version_id,
            method_id=method_a.method_id, as_of=as_of, knowledge_at=d_early.created_at,
        )
        front_price_early = next(
            point.settlement_price for point in points_early if point.instrument_id == front_id
        )
        self.assertEqual(front_price_early, Decimal("100.00"))

        # Invariant 9: the later dataset is not yet known at an earlier time.
        with self.assertRaises(FuturesTermStructureDerivationError) as raised:
            term_structure.derive_curve(
                series_id=series_id, dataset_version_id=d_late.dataset_version_id,
                method_id=method_a.method_id, as_of=as_of, knowledge_at=t0,
            )
        self.assertIn("dataset_not_known_at_knowledge_at", str(raised.exception))

        # Invariant 8: once known, the later dataset's later revision wins --
        # but only because it is a member of a *different*, later-sealed
        # dataset, never because "today" was queried against the old one.
        curve_late, points_late = term_structure.derive_curve(
            series_id=series_id, dataset_version_id=d_late.dataset_version_id,
            method_id=method_a.method_id, as_of=as_of, knowledge_at=d_late.created_at,
        )
        front_price_late = next(
            point.settlement_price for point in points_late if point.instrument_id == front_id
        )
        self.assertEqual(front_price_late, Decimal("100.50"))

        # Invariant 20: a changed settlement price changes the curve hash.
        self.assertNotEqual(curve_early.content_hash, curve_late.content_hash)

        # Invariant 24: the later final settlement produced a *new* artifact; it
        # did not mutate the earlier preliminary/early curve in place.
        self.assertNotEqual(curve_early.curve_id, curve_late.curve_id)
        reloaded_early, _ = term_structure.get_curve(curve_early.curve_id)
        self.assertEqual(reloaded_early.content_hash, curve_early.content_hash)

        # ==== invariant 18: a conflicting same-revision value already fails
        # upstream, and derivation must not be able to mask that. ==============
        with self.assertRaises(HistoricalMarketDataError) as raised:
            pipeline.capture_raw(
                [raw(
                    ObservationKind.SETTLEMENT_PRICE,
                    settlement_payload("999.99", as_of, "FINAL"),
                    identifier="ZFRONT", symbol="TESTFIXTSZFRONT", revision=0,
                    event_at=front_event_at, ingested_at=t0,
                )]
            )
        self.assertIn("raw_historical_observation_conflict", str(raised.exception))

        # ==== manual point-row attempts against curve1 (invariants 1,3,4,5,17,19) ==
        mismatch_settlement = capture_and_normalize(
            "MISMATCH", "TESTFIXTSMISMATCH", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("200.00", as_of, "FINAL"),
            event_at=front_event_at, ingested_at=t0,
        )
        otherseries_settlement = capture_and_normalize(
            "OTHERCTR", "TESTFIXTSOTHER", ObservationKind.SETTLEMENT_PRICE,
            settlement_payload("50.00", as_of, "FINAL"),
            event_at=front_event_at, ingested_at=t0,
        )

        def insert_point(**overrides: object) -> None:
            row: dict[str, object] = {
                "curve_id": curve1.curve_id, "sequence": 900, "instrument_id": front_id,
                "contract_expiration_date": date(2025, 12, 26), "settlement_session_date": as_of,
                "is_stale": False, "settlement_price": Decimal("100.00"),
                "settlement_finality": "FINAL", "provider_revision": 0,
                "normalized_observation_id": front_rev0.normalized_observation_id,
                "time_to_expiry_days": 189, "point_hash": "0" * 64,
            }
            row.update(overrides)
            columns = (
                "curve_id", "sequence", "instrument_id", "contract_expiration_date",
                "settlement_session_date", "is_stale", "settlement_price",
                "settlement_finality", "provider_revision", "normalized_observation_id",
                "time_to_expiry_days", "point_hash",
            )
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO futures_term_structure_points VALUES ("
                    + ",".join(["%s"] * 12) + ")",
                    tuple(row[column] for column in columns),
                )

        # Invariant 1: a non-settlement (OHLCV) observation can never be a point.
        with self.assertRaises(Exception):  # noqa: B017
            insert_point(
                sequence=901, instrument_id=gap_id,
                contract_expiration_date=date(2026, 6, 26),
                normalized_observation_id=gap_ohlcv.normalized_observation_id,
                settlement_price=Decimal("999.00"), time_to_expiry_days=1,
            )

        # Invariant 3: a continuous synthetic instrument is never a real contract.
        with self.assertRaises(Exception):  # noqa: B017
            insert_point(sequence=902, instrument_id=continuous_id)

        # Invariant 5 (equity leg): an equity instrument is never a futures contract.
        with self.assertRaises(Exception):  # noqa: B017
            insert_point(sequence=903, instrument_id=equity_id)

        # Invariant 4: a contract from the wrong futures series is refused even
        # though it has its own genuine settlement observation.
        with self.assertRaises(Exception):  # noqa: B017
            insert_point(
                sequence=904, instrument_id=otherseries_id,
                contract_expiration_date=date(2026, 1, 26),
                normalized_observation_id=otherseries_settlement.normalized_observation_id,
                settlement_price=Decimal("50.00"), time_to_expiry_days=1,
            )

        # Invariant 17: the same contract cannot appear twice on one curve.
        with self.assertRaises(Exception):  # noqa: B017
            insert_point(sequence=905, instrument_id=front_id)

        # Invariant 19: a point's price may never diverge from the canonical
        # settlement payload its normalized_observation_id references.
        with self.assertRaises(Exception):  # noqa: B017
            insert_point(
                sequence=906, instrument_id=mismatch_id,
                contract_expiration_date=date(2026, 9, 26),
                normalized_observation_id=mismatch_settlement.normalized_observation_id,
                settlement_price=Decimal("201.00"), time_to_expiry_days=1,
            )

        # ==== invariant 23: immutable evidence rejects UPDATE and DELETE =======
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE futures_term_structure_methods SET minimum_point_count=99 "
                "WHERE method_id=%s",
                (method_a.method_id,),
            )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM futures_term_structure_methods WHERE method_id=%s",
                (method_a.method_id,),
            )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE futures_term_structure_curves SET point_count=99 WHERE curve_id=%s",
                (curve1.curve_id,),
            )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM futures_term_structure_curves WHERE curve_id=%s",
                (curve1.curve_id,),
            )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE futures_term_structure_points SET is_stale=true "
                "WHERE curve_id=%s AND instrument_id=%s",
                (curve1.curve_id, front_id),
            )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM futures_term_structure_points "
                "WHERE curve_id=%s AND instrument_id=%s",
                (curve1.curve_id, front_id),
            )

        # ==== invariants 26/27: the schema itself forbids an undeclared
        # classification or an undeclared carry day-count convention, in
        # addition to the dataclass-level checks proven in the pure unit tests.
        def insert_raw_method(**overrides: object) -> None:
            row: dict[str, object] = {
                "method_id": uuid4(), "method_name": f"RAWCHECK:{uuid4()}", "method_version": 1,
                "allowed_observation_kind": "SETTLEMENT_PRICE", "minimum_point_count": 1,
                "settlement_finality_policy": "FINAL_ONLY", "session_policy": "SAME_SESSION_STRICT",
                "max_staleness_days": None, "stale_points_flagged": False,
                "classification_permitted_with_stale_points": False,
                "classification_enabled": False, "classification_minimum_point_count": None,
                "classification_flat_threshold": None, "carry_enabled": False,
                "day_count_convention": None, "method_definition": "{}",
                "effective_from": registered_at, "known_at": registered_at,
                "content_hash": "0" * 64, "created_at": registered_at,
            }
            row.update(overrides)
            columns = (
                "method_id", "method_name", "method_version", "allowed_observation_kind",
                "minimum_point_count", "settlement_finality_policy", "session_policy",
                "max_staleness_days", "stale_points_flagged",
                "classification_permitted_with_stale_points", "classification_enabled",
                "classification_minimum_point_count", "classification_flat_threshold",
                "carry_enabled", "day_count_convention", "method_definition",
                "effective_from", "known_at", "content_hash", "created_at",
            )
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO futures_term_structure_methods VALUES ("
                    + ",".join(["%s"] * 20) + ")",
                    tuple(row[column] for column in columns),
                )

        # Invariant 26: classification enabled with no threshold/min-count declared.
        with self.assertRaises(Exception):  # noqa: B017
            insert_raw_method(classification_enabled=True)

        # Invariant 27: carry enabled with no day-count convention declared.
        with self.assertRaises(Exception):  # noqa: B017
            insert_raw_method(carry_enabled=True)

        # ==== method-registry tamper evidence ==================================
        with self.assertRaises(FuturesTermStructureMethodError) as raised:
            term_structure.get_method(uuid4())
        self.assertIn("term_structure_method_not_found", str(raised.exception))

        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT content_hash FROM futures_term_structure_methods WHERE method_id=%s",
                (method_a.method_id,),
            )
            stored_hash = str(cursor.fetchone()[0])
        self.assertEqual(stored_hash, method_a.content_hash())
