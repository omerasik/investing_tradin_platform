"""Phase 3D.8A Postgres evidence: real-data status only from canonical persisted lineage.

Every price and quantity is a synthetic FIXTURE served by a scripted in-process
transport; nothing is retrieved from Bybit and no socket is opened. The
canonical Bybit source is created by the real onboarding (offline, from the
captured snapshot), two parent datasets are acquired through the real Phase
3B.1 service and composed through the real Phase 3D.3 service. The class owns
a DISPOSABLE database beneath the configured local/CI PostgreSQL instance; the
real research database is never referenced.
"""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock
from urllib.parse import urlparse
from uuid import UUID, uuid4

import psycopg

# The module, not its TestCase class: a class imported here would be collected
# and run a second time by unittest discovery.
from tests import test_historical_dataset_composition_v1_postgres as composition_fixture
from tests.test_historical_dataset_composition_v1_postgres import (
    INSTRUMENT_ID,
    LOCAL_HOSTS,
    ONBOARDED_AT,
    ROOT,
    START,
    SYMBOL,
    WINDOW,
    _disposable_dsn,
)

COMPOSE_NOW = datetime(2026, 9, 16, 2, 0, tzinfo=UTC)
RESEARCH_AT = datetime(2026, 9, 16, 3, 0, tzinfo=UTC)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class RealMarketDataProvenancePostgresTests(unittest.TestCase):
    database_name: str
    dsn: str
    database: Any
    source_id: UUID
    parents: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest("provenance tests require a local or CI disposable PostgreSQL")
        cls.database_name = f"real_data_provenance_phase3d8a_{os.getpid()}"
        cls.dsn = _disposable_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')

        from alembic import command
        from alembic.config import Config

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option(
            "sqlalchemy.url", cls.dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        old_dsn = os.environ.get("POSTGRES_TEST_DSN")
        try:
            os.environ["POSTGRES_TEST_DSN"] = cls.dsn
            command.upgrade(config, "head")
        finally:
            if old_dsn is not None:
                os.environ["POSTGRES_TEST_DSN"] = old_dsn

        from trade_platform.bybit_instrument_onboarding import (
            captured_btcusdt_snapshot_v1,
            onboard_bybit_btcusdt_perpetual_v1,
        )
        from trade_platform.historical_dataset_composition_v1 import (
            HistoricalDatasetCompositionService,
        )
        from trade_platform.persistence import PostgresDatabase

        cls.database = PostgresDatabase(cls.dsn)
        cls.source_id = onboard_bybit_btcusdt_perpetual_v1(
            cls.database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        ).source_id
        # Reuse the Phase 3D.3 acquisition fixture verbatim (scripted transport, no socket).
        acquire = composition_fixture.DatasetCompositionPostgresTests._acquire_parent.__func__  # type: ignore[attr-defined]
        cls.parents = {
            name: acquire(cls, name, START + index * WINDOW, START + (index + 1) * WINDOW,
                          "bybit-v5-md-phase3d8a")
            for index, name in enumerate("AB")
        }
        request = composition_fixture.DatasetCompositionPostgresTests._request.__get__(cls)  # type: ignore[attr-defined]
        composition = request("A", "B")
        composition = replace(composition, normalization_version="bybit-v5-md-phase3d8a")
        cls.composite = HistoricalDatasetCompositionService.for_postgres(
            cls.database, now=lambda: COMPOSE_NOW
        ).compose(composition)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    def _authority(self) -> Any:
        from trade_platform.real_market_data_provenance_v1 import (
            PostgresRealMarketDataProvenanceAuthorityV1,
        )

        return PostgresRealMarketDataProvenanceAuthorityV1(self.database)

    def _mark_dataset(self, source: Any, name: str) -> UUID:
        """One MARK_PRICE observation of the real instrument, sealed under ``source``."""
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            RawHistoricalObservation,
        )

        pipeline = PostgresHistoricalMarketDataPipeline(self.database)
        pipeline.register_source(source)
        event_at = datetime(2026, 9, 16, 5, 0, tzinfo=UTC)
        (raw_id,) = pipeline.capture_raw(
            [
                RawHistoricalObservation(
                    source_id=source.source_id, observation_kind=ObservationKind.MARK_PRICE,
                    provider_identifier=SYMBOL, provider_symbol=SYMBOL, exchange="BYBIT",
                    event_at=event_at, effective_at=event_at, ingested_at=event_at,
                    adjustment_status=AdjustmentStatus.AS_REPORTED, revision=0,
                    provenance_uri=f"fixture://3d8a/{name}",
                    raw_payload={"price": "27010.5", "price_asset": "USDT",
                                 "observed_at": event_at.isoformat()},
                )
            ]
        )
        normalized = pipeline.normalize(raw_id, "3d8a-v1", event_at)
        return pipeline.seal_dataset(
            source.source_id, name, "3d8a-v1", (normalized.normalized_observation_id,),
            event_at + timedelta(minutes=1),
        ).dataset_version_id

    def _other_source(self, **overrides: object) -> Any:
        from trade_platform.historical_market_data import (
            AssetScope,
            AuthorizedHistoricalSource,
            ObservationKind,
        )

        values: dict[str, object] = {
            "provider": "bybit",
            "dataset_name": "bybit_v5_public_market_linear",
            "provider_identifier_namespace": "bybit_v5_symbol",
            "provider_terms_version": f"free-text-{uuid4().hex[:8]}",
            "authorization_reference": "operator note: this is the bybit feed",
            "authorized_at": ONBOARDED_AT,
            "created_at": ONBOARDED_AT,
            "asset_scope": AssetScope.CRYPTO.value,
            "authorized_observation_kinds": frozenset({ObservationKind.MARK_PRICE}),
        }
        values.update(overrides)
        return AuthorizedHistoricalSource(**values)  # type: ignore[arg-type]

    def _counts(self) -> tuple[int, ...]:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            counts = []
            for table in ("historical_dataset_versions", "historical_dataset_members",
                          "historical_data_sources", "feature_materializations"):
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                counts.append(int(str(cursor.fetchone()[0])))
            return tuple(counts)

    def test_canonical_daily_and_composite_datasets_are_real(self) -> None:
        from trade_platform.real_market_data_provenance_v1 import STATUS_REAL_DATA

        before = self._counts()
        for dataset_version_id, members in (
            (self.parents["A"].dataset_version_id, 32),
            (self.parents["B"].dataset_version_id, 32),
            (self.composite.dataset_version_id, 64),
        ):
            verdict = self._authority().prove(dataset_version_id)
            self.assertEqual(verdict.status, STATUS_REAL_DATA, verdict.reasons)
            self.assertTrue(verdict.is_proven_real())
            self.assertEqual(verdict.source_id, self.source_id)
            self.assertEqual(verdict.member_count, members)
            self.assertEqual(verdict.instrument_ids, (INSTRUMENT_ID,))
        composite = self._authority().prove(self.composite.dataset_version_id)
        self.assertEqual(composite.dataset_content_hash, self.composite.dataset_content_hash)
        self.assertEqual(
            dict(composite.member_count_by_kind),
            {"INDEX_PRICE": 20, "MARK_PRICE": 20, "OHLCV": 20, "OPEN_INTEREST": 4},
        )
        # Read-only: proving writes nothing.
        self.assertEqual(self._counts(), before)

    def test_composite_bars_bind_to_its_provenance(self) -> None:
        from trade_platform.tradable_bar_evidence_v2 import PostgresTradableBarEvidenceReaderV2

        verdict = self._authority().prove(self.composite.dataset_version_id)
        series = PostgresTradableBarEvidenceReaderV2(self.database).series(
            dataset_version_id=self.composite.dataset_version_id, instrument_id=INSTRUMENT_ID,
            interval="1m", research_run_at=RESEARCH_AT,
        )
        self.assertEqual(len(series.bars), 20)
        self.assertTrue(all(bar.source_id == verdict.source_id for bar in series.bars))
        self.assertTrue(
            all(bar.dataset_content_hash.strip() == verdict.dataset_content_hash for bar in series.bars)
        )

    def test_free_text_bybit_source_never_grants_real_status(self) -> None:
        from trade_platform.real_market_data_provenance_v1 import STATUS_UNAVAILABLE

        impostor = self._mark_dataset(self._other_source(), "3d8a-free-text-bybit")
        verdict = self._authority().prove(impostor)
        self.assertEqual(verdict.status, STATUS_UNAVAILABLE)
        self.assertIn("source_id_not_canonical", verdict.reasons)
        self.assertFalse(verdict.is_proven_real())

    def test_fixture_source_stays_synthetic(self) -> None:
        from trade_platform.real_market_data_provenance_v1 import STATUS_SYNTHETIC

        fixture = self._mark_dataset(
            self._other_source(
                provider="TESTFIX_3D8A", dataset_name="3d8a-fixture",
                authorization_reference="fixture://authorization/3d8a",
            ),
            "3d8a-fixture-dataset",
        )
        self.assertEqual(self._authority().prove(fixture).status, STATUS_SYNTHETIC)

    def test_unknown_dataset_and_contract_drift_fail_closed(self) -> None:
        from trade_platform import real_market_data_provenance_v1 as provenance

        missing = self._authority().prove(uuid4())
        self.assertEqual((missing.status, missing.reasons),
                         (provenance.STATUS_UNAVAILABLE, ("dataset_not_found",)))
        drifted = replace(
            provenance.canonical_bybit_source_contract_v1(),
            authorization_reference="a different authorization",
        )
        with mock.patch.object(provenance, "canonical_bybit_source_contract_v1", return_value=drifted):
            verdict = self._authority().prove(self.parents["A"].dataset_version_id)
        self.assertEqual(verdict.status, provenance.STATUS_UNAVAILABLE)
        self.assertEqual(verdict.reasons, ("source_contract_mismatch:authorization_reference",))


if __name__ == "__main__":
    unittest.main()
