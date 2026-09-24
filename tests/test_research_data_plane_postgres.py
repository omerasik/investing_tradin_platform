"""Phase R2B -- PostgreSQL -> columnar frames -> catalog, on a disposable database.

The canonical ``CRYPTO:BYBIT:BTCUSDT:PERP`` instrument and its authorized Bybit
source are onboarded through the existing onboarding, so the dataset carries the
real T1 timing contract. Every price, volume and timestamp is a FIXTURE written
through the existing Historical Data Authority; no provider call is made.

LIVE BYBIT CALLS PERFORMED: NO
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import psycopg

from tests.test_crypto_liquidity_capacity_v1_postgres import (
    LOCAL_HOSTS,
    ONBOARDED_AT,
    _disposable_dsn,
    _migrate,
)

_ANALYTICS = all(importlib.util.find_spec(name) for name in ("pyarrow", "duckdb"))

SYMBOL = "BTCUSDT"
INSTRUMENT_ID = "CRYPTO:BYBIT:BTCUSDT:PERP"
NORMALIZATION_VERSION = "bybit-v5-md-r2b"
START = datetime(2026, 9, 16, tzinfo=UTC)
MINUTES = 12
SEALED_AT = datetime(2026, 9, 18, tzinfo=UTC)
DECISION_AT = datetime(2026, 9, 19, tzinfo=UTC)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
@unittest.skipUnless(_ANALYTICS, "analytics extra (pyarrow, duckdb) not installed")
class ResearchDataPlanePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest("R2B export test requires a local or CI disposable PostgreSQL")
        cls.database_name = f"research_data_plane_r2b_{os.getpid()}"
        cls.dsn = _disposable_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')
        _migrate(cls.dsn)
        cls.root = Path(tempfile.mkdtemp(prefix="r2b-pg-"))
        cls._build()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()
        shutil.rmtree(cls.root, ignore_errors=True)
        with psycopg.connect(os.environ["POSTGRES_TEST_DSN"], autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    @classmethod
    def _build(cls) -> None:
        from trade_platform.bybit_instrument_onboarding import (
            captured_btcusdt_snapshot_v1,
            onboard_bybit_btcusdt_perpetual_v1,
        )
        from trade_platform.crypto_derivatives_features import (
            PostgresCryptoDerivativesFeatureCalculator,
            crypto_mark_index_basis_definition,
        )
        from trade_platform.feature_authority import PostgresFeatureAuthority
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            RawHistoricalObservation,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.tradable_bar_evidence_v2 import BAR_TIMESTAMP_SEMANTICS_MARKER_V1

        cls.database = PostgresDatabase(cls.dsn)
        onboarding = onboard_bybit_btcusdt_perpetual_v1(
            cls.database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )
        pipeline = PostgresHistoricalMarketDataPipeline(cls.database)

        def raw(kind: ObservationKind, event_at: datetime, effective_at: datetime, payload: dict[str, object]) -> RawHistoricalObservation:
            return RawHistoricalObservation(
                source_id=onboarding.source_id, observation_kind=kind, provider_identifier=SYMBOL,
                provider_symbol=SYMBOL, exchange="BYBIT", event_at=event_at,
                effective_at=effective_at, ingested_at=effective_at + timedelta(minutes=1),
                adjustment_status=AdjustmentStatus.AS_REPORTED, revision=0,
                provenance_uri=f"fixture://r2b/{kind.value}/{event_at.isoformat()}",
                raw_payload=payload,
            )

        observations = []
        for minute in range(MINUTES):
            bar_open = START + timedelta(minutes=minute)
            close = Decimal(27000) + minute
            observations.append(raw(ObservationKind.OHLCV, bar_open, bar_open + timedelta(minutes=1), {
                "bar_timestamp_semantics": BAR_TIMESTAMP_SEMANTICS_MARKER_V1, "interval": "1m",
                "open": str(close - 5), "high": str(close + 10), "low": str(close - 10),
                "close": str(close), "volume": "12.5", "provider_category": "linear",
                "provider_interval": "1", "provider_turnover": str(Decimal("1000.5") + minute),
            }))
            mark_at = bar_open + timedelta(minutes=1)
            for kind, price in ((ObservationKind.MARK_PRICE, close + Decimal("1.25")),
                                (ObservationKind.INDEX_PRICE, close)):
                observations.append(raw(kind, mark_at, mark_at, {
                    "price": str(price), "price_asset": "USDT", "observed_at": mark_at.isoformat(),
                }))
            if minute % 5 == 0:
                observations.append(raw(ObservationKind.OPEN_INTEREST, bar_open, bar_open, {
                    "open_interest": str(Decimal("51234.5") + minute), "unit": "BASE_ASSET",
                    "unit_asset": "BTC", "observed_at": bar_open.isoformat(),
                }))
        raw_ids = pipeline.capture_raw(observations)
        normalized = tuple(
            pipeline.normalize(raw_id, NORMALIZATION_VERSION, item.ingested_at + timedelta(seconds=30)).normalized_observation_id
            for raw_id, item in zip(raw_ids, observations, strict=True)
        )
        cls.dataset = pipeline.seal_dataset(
            onboarding.source_id, "bybit-r2b-fixture", NORMALIZATION_VERSION, normalized, SEALED_AT
        )
        authority = PostgresFeatureAuthority(cls.database)
        definition = crypto_mark_index_basis_definition(ONBOARDED_AT)
        authority.register(definition)
        cls.basis_feature_id = definition.feature_id
        cls.basis_written = PostgresCryptoDerivativesFeatureCalculator(
            cls.database
        ).materialize_crypto_mark_index_basis_batch(
            feature_id=definition.feature_id, instrument_id=INSTRUMENT_ID,
            dataset_version_id=cls.dataset.dataset_version_id,
            event_ats=[START + timedelta(minutes=m + 1) for m in range(MINUTES)],
            decision_at=DECISION_AT,
        )

    def test_export_reconcile_parity_and_catalog(self) -> None:
        from trade_platform.research_data_export_v1 import (
            PostgresResearchFrameCatalogV1,
            build_mark_index_basis_frame_v1,
            compare_feature_frame_with_authority_v1,
            export_dataset_frames_v1,
            reconcile_frame_with_source_v1,
        )
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

        store = ResearchFrameStoreV1(self.root)
        manifests = export_dataset_frames_v1(self.database, store, self.dataset.dataset_version_id)
        self.assertEqual({"REFERENCE_PRICE", "OHLCV", "OPEN_INTEREST"}, set(manifests))
        self.assertEqual(2 * MINUTES, manifests["REFERENCE_PRICE"].row_count)
        self.assertEqual(MINUTES, manifests["OHLCV"].row_count)
        self.assertEqual(3, manifests["OPEN_INTEREST"].row_count)
        for manifest in manifests.values():
            # The T1 source stays T1; the export never derives a knowledge time.
            self.assertEqual("T1_RETROSPECTIVE", manifest.lineage["source_granted_evidence_tier"])
            self.assertTrue(all(row[8] is None for row in store.iter_rows(manifest)))
            self.assertEqual(self.dataset.content_hash, manifest.lineage["dataset_content_hash"])
            reconciliation = reconcile_frame_with_source_v1(self.database, store, manifest)
            self.assertTrue(reconciliation.reconciled, reconciliation)

        again = export_dataset_frames_v1(self.database, store, self.dataset.dataset_version_id)
        self.assertEqual(
            {kind: item.manifest_hash for kind, item in manifests.items()},
            {kind: item.manifest_hash for kind, item in again.items()},
        )

        frame, hit = build_mark_index_basis_frame_v1(
            store, manifests["REFERENCE_PRICE"], semantic_version="1.0.0",
            calculation_version="derivatives-crypto-mark-index-basis-3j1c-v1",
            eligible_instrument=lambda instrument: instrument == INSTRUMENT_ID,
        )
        self.assertFalse(hit)
        self.assertEqual(self.basis_written, frame.row_count)
        parity = compare_feature_frame_with_authority_v1(
            self.database, store, frame, feature_id=self.basis_feature_id,
            dataset_version=str(self.dataset.dataset_version_id),
        )
        self.assertTrue(parity.equal, parity)
        self.assertTrue(build_mark_index_basis_frame_v1(
            store, manifests["REFERENCE_PRICE"], semantic_version="1.0.0",
            calculation_version="derivatives-crypto-mark-index-basis-3j1c-v1",
            eligible_instrument=lambda instrument: instrument == INSTRUMENT_ID,
        )[1])

        catalog = PostgresResearchFrameCatalogV1(self.database)
        for manifest in (*manifests.values(), frame):
            catalog.register(manifest)
            catalog.register(manifest)  # idempotent
        listed = catalog.manifests_for_dataset(self.dataset.dataset_version_id)
        self.assertEqual(
            sorted(item.manifest_hash for item in (*manifests.values(), frame)),
            sorted(item["manifest_hash"] for item in listed),
        )

    def test_a_tampered_source_value_breaks_reconciliation(self) -> None:
        from dataclasses import replace

        from trade_platform.research_data_export_v1 import (
            export_dataset_frames_v1,
            reconcile_frame_with_source_v1,
        )
        from trade_platform.research_data_plane_v1 import (
            REFERENCE_PRICE_FRAME,
            ResearchFrameStoreV1,
        )

        store = ResearchFrameStoreV1(self.root)
        genuine = export_dataset_frames_v1(self.database, store, self.dataset.dataset_version_id)
        rows = list(store.iter_rows(genuine["REFERENCE_PRICE"]))
        rows[0] = (*rows[0][:11], rows[0][11] + Decimal("0.01"), rows[0][12])
        forged = store.write_frame(REFERENCE_PRICE_FRAME, rows, lineage=genuine["REFERENCE_PRICE"].lineage)
        store.verify(forged)  # a self-consistent frame ...
        self.assertFalse(reconcile_frame_with_source_v1(self.database, store, forged).reconciled)
        del replace  # imported only to mirror the pure tests' idiom


if __name__ == "__main__":
    unittest.main()
