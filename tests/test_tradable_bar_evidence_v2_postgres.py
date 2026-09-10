"""Real PostgreSQL evidence for Module 3J.2b.1's dataset-bound tradable-bar reader.

Every rate, price, quantity, venue and provider identifier is a FIXTURE.
Nothing here was retrieved from or verified against any crypto venue or data
provider; no real OHLCV, mark price or index price is claimed, and no
network call is made. Instruments are registered under
``operator_dashboard.RESERVED_TEST_FIXTURE_PREFIX`` (``TESTFIXTURE:``).
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

REGISTERED_AT = datetime(2026, 2, 1, tzinfo=UTC)
BAR1_OPEN = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
RESEARCH_RUN_AT = datetime(2026, 4, 1, tzinfo=UTC)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class TradableBarEvidenceV2PostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

        # Every test method gets its own isolated instrument/venue/source
        # identity (suffixed by the test method name): unlike this suite's
        # other Postgres test files (one giant test method each), this file
        # uses one method per required-test-list item for readability, and
        # setUp() runs fresh before each one against the SAME shared database
        # with no reset between methods.
        tag = "".join(character for character in self._testMethodName if character.isalnum()).upper()[:24]
        self._tag = tag

        from trade_platform.crypto_derivatives_features import (
            PostgresCryptoDerivativesFeatureCalculator,
        )
        from trade_platform.crypto_instruments import (
            CryptoInstrumentKind,
            CryptoInstrumentSpecification,
            CryptoSettlementType,
            PostgresCryptoInstrumentAuthority,
            ReferencePriceRequirement,
            SettlementStyle,
        )
        from trade_platform.domain import AssetClass
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
        from trade_platform.tradable_bar_evidence_v2 import (
            BAR_TIMESTAMP_SEMANTICS_MARKER_V1,
            PostgresTradableBarEvidenceReaderV2,
        )

        self.dsn = os.environ["POSTGRES_TEST_DSN"]
        self.database = PostgresDatabase(self.dsn)
        self.master = PostgresProfessionalInstrumentMaster(self.database)
        self.crypto = PostgresCryptoInstrumentAuthority(self.database)
        self.pipeline = PostgresHistoricalMarketDataPipeline(self.database)
        self.calculator = PostgresCryptoDerivativesFeatureCalculator(self.database)
        self.reader = PostgresTradableBarEvidenceReaderV2(self.database)
        self.marker = BAR_TIMESTAMP_SEMANTICS_MARKER_V1
        self.AdjustmentStatus = AdjustmentStatus
        self.AssetScope = AssetScope
        self.AuthorizedHistoricalSource = AuthorizedHistoricalSource
        self.ObservationKind = ObservationKind
        self.RawHistoricalObservation = RawHistoricalObservation
        self.CryptoInstrumentKind = CryptoInstrumentKind
        self.CryptoInstrumentSpecification = CryptoInstrumentSpecification
        self.CryptoSettlementType = CryptoSettlementType
        self.ReferencePriceRequirement = ReferencePriceRequirement
        self.SettlementStyle = SettlementStyle
        self.PostgresDatabase = PostgresDatabase
        self.PostgresTradableBarEvidenceReaderV2 = PostgresTradableBarEvidenceReaderV2

        venue = f"TESTFIX3J2B1{tag}CEX"[:32]
        namespace = f"TESTFIX_3J2B1_{tag}_PROVIDER"
        self.venue = venue
        self.perpetual_identifier = f"BTCUSDT-PERP-3J2B1-{tag}"
        self.spot_identifier = f"BTCUSDT-SPOT-3J2B1-{tag}"

        def register(
            suffix: str, symbol: str, identifier: str, *,
            instrument_type: InstrumentType, representation: RepresentationKind,
        ) -> str:
            instrument_id = f"TESTFIXTURE:3J2B1:{tag}:{suffix}"
            self.master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id, asset_class=AssetClass.CRYPTO,
                    instrument_type=instrument_type, exchange_name=venue, venue=venue,
                    mic=None, canonical_symbol=symbol, listing_date=date(2026, 1, 2),
                    base_currency="BTC", quote_currency="USDT", settlement_currency="USDT",
                    contract_multiplier=Decimal(1), contract_size=Decimal(1),
                    tick_size=Decimal("0.01"), lot_size=Decimal("0.00001"),
                    price_precision=2, quantity_precision=5, trading_timezone="UTC",
                    market_session_type=SessionType.CRYPTO_24X7,
                    representation_kind=representation, registered_at=REGISTERED_AT,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                )
            )
            self.master.add_identifier_mapping(
                IdentifierMapping(
                    instrument_id=instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
                    namespace=namespace, value=identifier, valid_from=REGISTERED_AT,
                    valid_until=None, ingested_at=REGISTERED_AT,
                    source_reference="fixture:provider-identifier",
                )
            )
            return instrument_id

        self.perpetual_id = register(
            "BTCUSDT:PERP", f"TESTFIX3J2B1{tag}P"[:32], self.perpetual_identifier,
            instrument_type=InstrumentType.CRYPTO_PERPETUAL,
            representation=RepresentationKind.PERPETUAL,
        )
        self.spot_id = register(
            "BTCUSDT:SPOT", f"TESTFIX3J2B1{tag}S"[:32], self.spot_identifier,
            instrument_type=InstrumentType.SPOT_CRYPTO, representation=RepresentationKind.SPOT,
        )
        # A second, independent provider identifier for the SAME instrument --
        # two vendors' symbols for one perpetual -- used only by the ambiguous
        # provider identity test below.
        self.perpetual_alt_identifier = f"BTCUSDT-PERP-3J2B1-{tag}-ALT"
        self.master.add_identifier_mapping(
            IdentifierMapping(
                instrument_id=self.perpetual_id, source_kind=IdentifierSourceKind.PROVIDER,
                namespace=namespace, value=self.perpetual_alt_identifier, valid_from=REGISTERED_AT,
                valid_until=None, ingested_at=REGISTERED_AT,
                source_reference="fixture:provider-identifier-alt",
            )
        )

        self.crypto.specify_instrument(
            CryptoInstrumentSpecification(
                instrument_id=self.perpetual_id, venue=venue, kind=CryptoInstrumentKind.PERPETUAL,
                base_asset="BTC", quote_asset="USDT", settlement_asset="USDT",
                settlement_style=SettlementStyle.LINEAR,
                settlement_type=CryptoSettlementType.CASH_SETTLED,
                contract_multiplier=Decimal(1), contract_size=Decimal(1),
                reference_price_requirement=ReferencePriceRequirement.MARK_AND_INDEX,
                index_reference=f"TESTFIX_3J2B1_{tag}_BTCUSDT_INDEX", registered_at=REGISTERED_AT,
                source_reference="fixture:crypto-specification",
            )
        )
        self.crypto.specify_instrument(
            CryptoInstrumentSpecification(
                instrument_id=self.spot_id, venue=venue, kind=CryptoInstrumentKind.SPOT,
                base_asset="BTC", quote_asset="USDT", settlement_asset=None, settlement_style=None,
                settlement_type=CryptoSettlementType.PHYSICAL_DELIVERY,
                contract_multiplier=Decimal(1), contract_size=Decimal(1),
                reference_price_requirement=ReferencePriceRequirement.NONE, index_reference=None,
                registered_at=REGISTERED_AT, source_reference="fixture:crypto-specification",
            )
        )

        self.source = AuthorizedHistoricalSource(
            provider="TESTFIX_3J2B1_MD", dataset_name=f"crypto-3j2b1-full-{tag}",
            provider_identifier_namespace=namespace, provider_terms_version=f"crypto-3j2b1-full-{tag}-v1",
            authorization_reference=f"fixture://authorization/crypto-3j2b1-full-{tag}",
            authorized_at=REGISTERED_AT, created_at=REGISTERED_AT, asset_scope=AssetScope.CRYPTO.value,
            authorized_observation_kinds=frozenset(
                {ObservationKind.OHLCV, ObservationKind.MARK_PRICE, ObservationKind.INDEX_PRICE}
            ),
        )
        self.pipeline.register_source(self.source)

    def ohlcv_raw(
        self, *, event_at: datetime, effective_at: datetime, open_price: Decimal, high: Decimal,
        low: Decimal, close: Decimal, volume: Decimal = Decimal("10"), interval: str = "1m",
        marker: str | None = "__default__", ingested_at: datetime | None = None,
        revision: int = 0, identifier: str | None = None,
    ):
        payload: dict[str, object] = {
            "open": str(open_price), "high": str(high), "low": str(low), "close": str(close),
            "volume": str(volume), "interval": interval,
        }
        marker_value = self.marker if marker == "__default__" else marker
        if marker_value is not None:
            payload["bar_timestamp_semantics"] = marker_value
        resolved_identifier = identifier or self.perpetual_identifier
        return self.RawHistoricalObservation(
            source_id=self.source.source_id, observation_kind=self.ObservationKind.OHLCV,
            provider_identifier=resolved_identifier, provider_symbol=resolved_identifier, exchange=self.venue,
            event_at=event_at, effective_at=effective_at,
            ingested_at=ingested_at or (effective_at + timedelta(seconds=30)),
            adjustment_status=self.AdjustmentStatus.AS_REPORTED, revision=revision,
            provenance_uri=f"fixture://ohlcv/{resolved_identifier}/{event_at.isoformat()}/{revision}",
            raw_payload=payload,
        )

    def reference_raw(self, *, kind, event_at: datetime, price: str):
        payload = {
            "price": price, "price_asset": "USDT", "observed_at": event_at.isoformat(),
            "methodology_reference": "fixture://methodology/3j2b1-v1",
        }
        return self.RawHistoricalObservation(
            source_id=self.source.source_id, observation_kind=kind,
            provider_identifier=self.perpetual_identifier, provider_symbol=self.perpetual_identifier,
            exchange=self.venue, event_at=event_at, effective_at=event_at,
            ingested_at=event_at + timedelta(minutes=1), adjustment_status=self.AdjustmentStatus.AS_REPORTED,
            revision=0, provenance_uri=f"fixture://{kind.value}/{event_at.isoformat()}",
            raw_payload=payload,
        )

    def capture_and_normalize(self, raw_observation, *, normalized_at: datetime | None = None):
        (raw_id,) = self.pipeline.capture_raw([raw_observation])
        return self.pipeline.normalize(
            raw_id, "crypto-3j2b1-v1", normalized_at or (raw_observation.ingested_at + timedelta(seconds=30))
        )

    def test_end_to_end_bar_reader(self) -> None:
        from dataclasses import replace

        from trade_platform.crypto_derivatives_features import crypto_mark_index_basis_definition
        from trade_platform.feature_authority import PostgresFeatureAuthority
        from trade_platform.historical_market_data import (
            HistoricalDataQualityError,
            ObservationKind,
        )

        bar_opens = [BAR1_OPEN + timedelta(minutes=index) for index in range(3)]
        closes = [Decimal("50000"), Decimal("50100"), Decimal("50250")]
        normalized_ohlcv = []
        raw_ids = []
        for index, (open_at, close_price) in enumerate(zip(bar_opens, closes, strict=True)):
            observation = self.ohlcv_raw(
                event_at=open_at, effective_at=open_at + timedelta(minutes=1),
                open_price=close_price - 20, high=close_price + 20, low=close_price - 30,
                close=close_price,
            )
            (raw_id,) = self.pipeline.capture_raw([observation])
            raw_ids.append(raw_id)
            normalized_ohlcv.append(
                self.pipeline.normalize(
                    raw_id, "crypto-3j2b1-v1", observation.ingested_at + timedelta(seconds=30)
                )
            )

        mark = self.capture_and_normalize(
            self.reference_raw(kind=ObservationKind.MARK_PRICE, event_at=BAR1_OPEN, price="50010")
        )
        index_price = self.capture_and_normalize(
            self.reference_raw(kind=ObservationKind.INDEX_PRICE, event_at=BAR1_OPEN, price="50000")
        )

        # 3I.2's dedicated crypto source fixture already carries no OHLCV
        # capability; this module's own source proves the CRYPTO scope's
        # existing OHLCV eligibility only needed widening the fixture's
        # declared kind set, not any new enum/schema authority.
        dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-dataset", "crypto-3j2b1-v1",
            tuple(item.normalized_observation_id for item in (*normalized_ohlcv, mark, index_price)),
            datetime(2026, 3, 2, tzinfo=UTC),
        )

        series = self.reader.series(
            dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
            interval="1m", research_run_at=RESEARCH_RUN_AT,
        )
        self.assertEqual(len(series.bars), 3)
        self.assertEqual([bar.bar_open_at for bar in series.bars], bar_opens)
        self.assertEqual([bar.close for bar in series.bars], closes)
        self.assertEqual(series.bars[0].dataset_content_hash, dataset.content_hash)
        self.assertEqual(series.bars[0].source_id, self.source.source_id)
        self.assertEqual(
            [bar.raw_observation_id for bar in series.bars],
            [item.raw_observation_id for item in normalized_ohlcv],
        )
        self.assertEqual(
            [bar.normalized_observation_id for bar in series.bars],
            [item.normalized_observation_id for item in normalized_ohlcv],
        )
        # Mark and index price never surface through the OHLCV-only reader,
        # and cannot be mistaken for tradable evidence.
        returned_normalized_ids = {bar.normalized_observation_id for bar in series.bars}
        self.assertNotIn(mark.normalized_observation_id, returned_normalized_ids)
        self.assertNotIn(index_price.normalized_observation_id, returned_normalized_ids)

        # ---- canonical entry-bar primitive -----------------------------------
        self.assertEqual(series.first_eligible_bar_after(bar_opens[0]).bar_open_at, bar_opens[1])
        self.assertEqual(
            series.first_eligible_bar_after(bar_opens[0] - timedelta(seconds=1)).bar_open_at, bar_opens[0]
        )
        self.assertIsNone(series.first_eligible_bar_after(bar_opens[-1]))

        # ---- basis materialization shares the exact same dataset UUID --------
        # (name, semantic_version) is unique across the whole shared test
        # database, and another test file may already own the canonical
        # "crypto_mark_index_basis"/"1.0.0" pair, so this test registers its
        # own isolated version tag rather than colliding with it.
        basis_definition = replace(
            crypto_mark_index_basis_definition(REGISTERED_AT), semantic_version=f"1.0.0-{self._tag.lower()}"
        )
        PostgresFeatureAuthority(self.database).register(basis_definition)
        basis = self.calculator.materialize_crypto_mark_index_basis(
            feature_id=basis_definition.feature_id, instrument_id=self.perpetual_id,
            dataset_version_id=dataset.dataset_version_id,
            event_at=BAR1_OPEN, decision_at=dataset.created_at + timedelta(minutes=5),
        )
        self.assertIsNotNone(basis)
        assert basis is not None
        self.assertEqual(basis.dataset_version, str(dataset.dataset_version_id))

        # ---- wrong dataset / instrument / interval -> unavailable, not raised
        self.assertEqual(
            self.reader.series(
                dataset_version_id=uuid4(), instrument_id=self.perpetual_id, interval="1m",
                research_run_at=RESEARCH_RUN_AT,
            ).bars,
            (),
        )
        self.assertEqual(
            self.reader.series(
                dataset_version_id=dataset.dataset_version_id, instrument_id="TESTFIXTURE:3J2B1:NOPE:PERP",
                interval="1m", research_run_at=RESEARCH_RUN_AT,
            ).bars,
            (),
        )
        self.assertEqual(
            self.reader.series(
                dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
                interval="5m", research_run_at=RESEARCH_RUN_AT,
            ).bars,
            (),
        )

        # ---- future/unsealed-at-cutoff dataset -> unavailable -----------------
        self.assertEqual(
            self.reader.series(
                dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
                interval="1m", research_run_at=datetime(2026, 3, 1, 12, tzinfo=UTC),
            ).bars,
            (),
        )

        # ---- non-PERPETUAL rejected for this research boundary ---------------
        spot_observation = self.ohlcv_raw(
            event_at=BAR1_OPEN, effective_at=BAR1_OPEN + timedelta(minutes=1),
            open_price=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
            identifier=self.spot_identifier,
        )
        spot_normalized = self.capture_and_normalize(spot_observation)
        spot_dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-spot-dataset", "crypto-3j2b1-v1",
            (spot_normalized.normalized_observation_id,), datetime(2026, 3, 2, tzinfo=UTC),
        )
        self.assertEqual(
            self.reader.series(
                dataset_version_id=spot_dataset.dataset_version_id, instrument_id=self.spot_id,
                interval="1m", research_run_at=RESEARCH_RUN_AT,
            ).bars,
            (),
        )

        # ---- rejected normalized bars can never enter a sealed dataset --------
        for position, (label, payload_overrides) in enumerate((
            ("non_positive_price", {"open_price": Decimal("0")}),
            ("negative_volume", {"volume": Decimal("-1")}),
            ("impossible_ohlc", {"low": Decimal("999999")}),
        )):
            with self.subTest(label):
                offset = timedelta(hours=1 + position)
                base = {
                    "event_at": BAR1_OPEN + offset, "effective_at": BAR1_OPEN + offset + timedelta(minutes=1),
                    "open_price": Decimal("100"), "high": Decimal("101"), "low": Decimal("99"), "close": Decimal("100.5"),
                }
                base.update(payload_overrides)
                bad = self.capture_and_normalize(self.ohlcv_raw(**base))
                self.assertNotEqual(bad.quality_status.value, "VALIDATED")
                with self.assertRaises(HistoricalDataQualityError):
                    self.pipeline.seal_dataset(
                        self.source.source_id, f"crypto-3j2b1-rejected-{label}", "crypto-3j2b1-v1",
                        (bad.normalized_observation_id,), datetime(2026, 3, 2, tzinfo=UTC),
                    )

    def test_missing_marker_rejected(self) -> None:
        from trade_platform.tradable_bar_evidence_v2 import TradableBarEvidenceV2Error

        observation = self.ohlcv_raw(
            event_at=BAR1_OPEN, effective_at=BAR1_OPEN + timedelta(minutes=1),
            open_price=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
            marker=None,
        )
        normalized = self.capture_and_normalize(observation)
        dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-no-marker", "crypto-3j2b1-v1",
            (normalized.normalized_observation_id,), datetime(2026, 3, 2, tzinfo=UTC),
        )
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "missing_or_malformed_bar_timestamp_semantics_marker"):
            self.reader.series(
                dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
                interval="1m", research_run_at=RESEARCH_RUN_AT,
            )

    def test_malformed_marker_rejected(self) -> None:
        from trade_platform.tradable_bar_evidence_v2 import TradableBarEvidenceV2Error

        observation = self.ohlcv_raw(
            event_at=BAR1_OPEN, effective_at=BAR1_OPEN + timedelta(minutes=1),
            open_price=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
            marker="SOME_OTHER_MARKER_V1",
        )
        normalized = self.capture_and_normalize(observation)
        dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-bad-marker", "crypto-3j2b1-v1",
            (normalized.normalized_observation_id,), datetime(2026, 3, 2, tzinfo=UTC),
        )
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "missing_or_malformed_bar_timestamp_semantics_marker"):
            self.reader.series(
                dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
                interval="1m", research_run_at=RESEARCH_RUN_AT,
            )

    def test_bar_close_not_after_open_rejected(self) -> None:
        from trade_platform.tradable_bar_evidence_v2 import TradableBarEvidenceV2Error

        observation = self.ohlcv_raw(
            event_at=BAR1_OPEN, effective_at=BAR1_OPEN,  # effective_at == event_at
            open_price=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
            ingested_at=BAR1_OPEN + timedelta(seconds=30),
        )
        normalized = self.capture_and_normalize(observation)
        dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-bad-close", "crypto-3j2b1-v1",
            (normalized.normalized_observation_id,), datetime(2026, 3, 2, tzinfo=UTC),
        )
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "bar_close_not_after_bar_open"):
            self.reader.series(
                dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
                interval="1m", research_run_at=RESEARCH_RUN_AT,
            )

    def test_incorrect_1m_duration_rejected(self) -> None:
        from trade_platform.tradable_bar_evidence_v2 import TradableBarEvidenceV2Error

        observation = self.ohlcv_raw(
            event_at=BAR1_OPEN, effective_at=BAR1_OPEN + timedelta(minutes=2),
            open_price=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
            ingested_at=BAR1_OPEN + timedelta(minutes=2, seconds=30),
        )
        normalized = self.capture_and_normalize(observation)
        dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-bad-duration", "crypto-3j2b1-v1",
            (normalized.normalized_observation_id,), datetime(2026, 3, 2, tzinfo=UTC),
        )
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "invalid_bar_interval_duration"):
            self.reader.series(
                dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
                interval="1m", research_run_at=RESEARCH_RUN_AT,
            )

    def test_ingestion_before_bar_close_rejected(self) -> None:
        from trade_platform.tradable_bar_evidence_v2 import TradableBarEvidenceV2Error

        observation = self.ohlcv_raw(
            event_at=BAR1_OPEN, effective_at=BAR1_OPEN + timedelta(minutes=1),
            open_price=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
            ingested_at=BAR1_OPEN + timedelta(seconds=10),  # before bar close at +60s
        )
        normalized = self.capture_and_normalize(observation)
        dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-early-ingest", "crypto-3j2b1-v1",
            (normalized.normalized_observation_id,), datetime(2026, 3, 2, tzinfo=UTC),
        )
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "bar_ingested_before_close"):
            self.reader.series(
                dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
                interval="1m", research_run_at=RESEARCH_RUN_AT,
            )

    def test_normalization_before_ingestion_rejected(self) -> None:
        from trade_platform.historical_market_data import HistoricalMarketDataError

        observation = self.ohlcv_raw(
            event_at=BAR1_OPEN, effective_at=BAR1_OPEN + timedelta(minutes=1),
            open_price=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
        )
        (raw_id,) = self.pipeline.capture_raw([observation])
        with self.assertRaises(HistoricalMarketDataError):
            self.pipeline.normalize(raw_id, "crypto-3j2b1-v1", observation.ingested_at - timedelta(seconds=1))

    def test_same_event_higher_revision_selected(self) -> None:
        event_at = BAR1_OPEN + timedelta(hours=2)
        effective_at = event_at + timedelta(minutes=1)
        first = self.capture_and_normalize(
            self.ohlcv_raw(
                event_at=event_at, effective_at=effective_at, open_price=Decimal("100"),
                high=Decimal("101"), low=Decimal("99"), close=Decimal("100.1"), revision=0,
            )
        )
        second = self.capture_and_normalize(
            self.ohlcv_raw(
                event_at=event_at, effective_at=effective_at, open_price=Decimal("100"),
                high=Decimal("105"), low=Decimal("99"), close=Decimal("104.9"), revision=1,
                ingested_at=effective_at + timedelta(minutes=5),
            )
        )
        dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-revisions", "crypto-3j2b1-v1",
            (first.normalized_observation_id, second.normalized_observation_id),
            datetime(2026, 3, 2, tzinfo=UTC),
        )
        series = self.reader.series(
            dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
            interval="1m", research_run_at=RESEARCH_RUN_AT,
        )
        self.assertEqual(len(series.bars), 1)
        self.assertEqual(series.bars[0].close, Decimal("104.9"))
        self.assertEqual(series.bars[0].revision, 1)

    def test_ambiguous_provider_identity_fails_closed(self) -> None:
        from trade_platform.tradable_bar_evidence_v2 import TradableBarEvidenceV2Error

        event_at = BAR1_OPEN + timedelta(hours=3)
        effective_at = event_at + timedelta(minutes=1)
        first = self.capture_and_normalize(
            self.ohlcv_raw(
                event_at=event_at, effective_at=effective_at, open_price=Decimal("100"),
                high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
                identifier=self.perpetual_identifier,
            )
        )
        second = self.capture_and_normalize(
            self.ohlcv_raw(
                event_at=event_at, effective_at=effective_at, open_price=Decimal("100"),
                high=Decimal("101"), low=Decimal("99"), close=Decimal("100.9"),
                identifier=self.perpetual_alt_identifier,
            )
        )
        dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-ambiguous", "crypto-3j2b1-v1",
            (first.normalized_observation_id, second.normalized_observation_id),
            datetime(2026, 3, 2, tzinfo=UTC),
        )
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "ambiguous_provider_identity_for_bar"):
            self.reader.series(
                dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
                interval="1m", research_run_at=RESEARCH_RUN_AT,
            )

    def test_restart_preserves_reads(self) -> None:
        observation = self.ohlcv_raw(
            event_at=BAR1_OPEN + timedelta(hours=4), effective_at=BAR1_OPEN + timedelta(hours=4, minutes=1),
            open_price=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
        )
        normalized = self.capture_and_normalize(observation)
        dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-restart", "crypto-3j2b1-v1",
            (normalized.normalized_observation_id,), datetime(2026, 3, 2, tzinfo=UTC),
        )
        restarted_reader = self.PostgresTradableBarEvidenceReaderV2(self.PostgresDatabase(self.dsn))
        series = restarted_reader.series(
            dataset_version_id=dataset.dataset_version_id, instrument_id=self.perpetual_id,
            interval="1m", research_run_at=RESEARCH_RUN_AT,
        )
        self.assertEqual(len(series.bars), 1)
        self.assertEqual(series.bars[0].close, Decimal("100.5"))

    def test_sealed_hash_changes_when_ohlcv_content_changes(self) -> None:
        event_at = BAR1_OPEN + timedelta(hours=5)
        effective_at = event_at + timedelta(minutes=1)
        base = self.capture_and_normalize(
            self.ohlcv_raw(
                event_at=event_at, effective_at=effective_at, open_price=Decimal("100"),
                high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
            )
        )
        base_dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-hash-base", "crypto-3j2b1-v1",
            (base.normalized_observation_id,), datetime(2026, 3, 2, tzinfo=UTC),
        )

        other_event_at = BAR1_OPEN + timedelta(hours=6)
        other_effective_at = other_event_at + timedelta(minutes=1)
        changed = self.capture_and_normalize(
            self.ohlcv_raw(
                event_at=other_event_at, effective_at=other_effective_at, open_price=Decimal("100"),
                high=Decimal("125"), low=Decimal("99"), close=Decimal("120.0"),
            )
        )
        changed_dataset = self.pipeline.seal_dataset(
            self.source.source_id, "crypto-3j2b1-hash-changed", "crypto-3j2b1-v1",
            (changed.normalized_observation_id,), datetime(2026, 3, 2, tzinfo=UTC),
        )
        self.assertNotEqual(base_dataset.content_hash, changed_dataset.content_hash)


if __name__ == "__main__":
    unittest.main()
