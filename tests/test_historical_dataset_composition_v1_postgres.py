"""Real-PostgreSQL evidence for the Phase 3D.3 dataset composition authority.

Every price and quantity is a synthetic FIXTURE served by a scripted in-process
transport while the *parent* datasets are acquired through the real Phase 3B.1
service; nothing is retrieved from Bybit and no socket is opened. The composition
itself is then run against those real sealed parents and is proven to consume
only their exact members, seal once through the existing pipeline, and recompute
canonical features on the new dataset identity.

The class owns a DISPOSABLE database beneath the configured local/CI PostgreSQL
instance (same pattern as ``test_historical_acquisition_postgres``); the real
research database is never referenced.

Parents (10-minute windows, all on 2026-09-16, contiguous):
``A`` 00:00-00:10, ``B`` 00:10-00:20, ``C`` 00:20-00:30, ``D`` 00:30-00:40 and
``E`` 00:40-00:50 (``E`` is acquired under a different normalization version).
Each test composes a *different* parent subset so no test depends on another's
composed dataset.
"""

from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

SYMBOL = "BTCUSDT"
INSTRUMENT_ID = "CRYPTO:BYBIT:BTCUSDT:PERP"
ONBOARDED_AT = datetime(2026, 9, 15, tzinfo=UTC)
START = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
ACQUIRE_NOW = datetime(2026, 9, 16, 1, 0, tzinfo=UTC)
COMPOSE_NOW = datetime(2026, 9, 16, 2, 0, tzinfo=UTC)
RESEARCH_AT = datetime(2026, 9, 16, 3, 0, tzinfo=UTC)
NORMALIZATION_VERSION = "bybit-v5-md-phase3d3"
OTHER_NORMALIZATION_VERSION = "bybit-v5-md-phase3d3-other"
WINDOW = timedelta(minutes=10)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)
_FIVE = timedelta(minutes=5)


def _ms(value: datetime) -> int:
    return int((value - _EPOCH).total_seconds()) * 1000


def _minutes(value: datetime) -> int:
    return int((value - START) / _MINUTE)


def _envelope(result: dict[str, object]) -> str:
    return json.dumps(
        {"retCode": 0, "retMsg": "OK", "result": result, "retExtInfo": {}, "time": _ms(ACQUIRE_NOW)}
    )


def _oi_value(instant: datetime) -> Decimal:
    return Decimal(461000 + 10 * (_minutes(instant) // 5))


class RoutingTransport:
    def __init__(self, routes: dict[str, list[object]]) -> None:
        self._routes = {path: list(bodies) for path, bodies in routes.items()}

    def get(self, url: str, timeout_seconds: float) -> object:
        for path, bodies in self._routes.items():
            if path in url:
                if not bodies:
                    raise AssertionError(f"exhausted route: {path}")
                return bodies.pop(0)
        raise AssertionError(f"unrouted request: {url}")


def _disposable_dsn(source_dsn: str, database_name: str) -> str:
    return urlunparse(urlparse(source_dsn)._replace(path=f"/{database_name}"))


def _fixture_transport(start: datetime, end: datetime) -> RoutingTransport:
    from trade_platform.data_providers import HttpResponse

    opens = [start + index * _MINUTE for index in range(int((end - start) / _MINUTE))]
    instants = [start + index * _FIVE for index in range(int((end - start) / _FIVE))]

    def klines(rows: list[list[str]]) -> HttpResponse:
        return HttpResponse(
            200, _envelope({"category": "linear", "symbol": SYMBOL, "list": list(reversed(rows))})
        )

    trade = [
        [str(_ms(o)), "27000.0", "27100.0", "26900.0", f"{27000 + _minutes(o)}.0", "12.5", "337500.0"]
        for o in opens
    ]
    mark = [
        [str(_ms(o)), "27000.0", "27100.0", "26900.0", f"{27010 + _minutes(o)}.0"] for o in opens
    ]
    index = [
        [str(_ms(o)), "27000.0", "27100.0", "26900.0", f"{27005 + _minutes(o)}.0"] for o in opens
    ]
    interest = HttpResponse(
        200,
        _envelope(
            {
                "category": "linear",
                "symbol": SYMBOL,
                "list": [
                    {"openInterest": f"{_oi_value(i)}.0", "timestamp": str(_ms(i))}
                    for i in reversed(instants)
                ],
                "nextPageCursor": "",
            }
        ),
    )
    return RoutingTransport(
        {
            "/v5/market/kline": [klines(trade)],
            "/v5/market/mark-price-kline": [klines(mark)],
            "/v5/market/index-price-kline": [klines(index)],
            "/v5/market/open-interest": [interest],
        }
    )


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class DatasetCompositionPostgresTests(unittest.TestCase):
    database_name: str
    dsn: str
    database: Any
    source_id: UUID
    parents: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest("composition tests require a local or CI disposable PostgreSQL")
        cls.database_name = f"historical_composition_phase3d3_{os.getpid()}"
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
        from trade_platform.persistence import PostgresDatabase

        cls.database = PostgresDatabase(cls.dsn)
        onboarding = onboard_bybit_btcusdt_perpetual_v1(
            cls.database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )
        cls.source_id = onboarding.source_id
        cls.parents = {}
        for index, name in enumerate("ABCDE"):
            normalization = OTHER_NORMALIZATION_VERSION if name == "E" else NORMALIZATION_VERSION
            cls.parents[name] = cls._acquire_parent(
                name, START + index * WINDOW, START + (index + 1) * WINDOW, normalization
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    # ---- fixtures ----------------------------------------------------------

    @classmethod
    def _acquire_parent(
        cls, name: str, start: datetime, end: datetime, normalization: str
    ) -> Any:
        from trade_platform.bybit_crypto_provider import BybitCryptoHistoricalAdapter
        from trade_platform.data_providers import ProviderConfiguration
        from trade_platform.historical_acquisition import (
            AcquisitionStatus,
            HistoricalAcquisitionRequest,
            HistoricalAcquisitionService,
            acquisition_fingerprint,
        )
        from trade_platform.historical_dataset_composition_v1 import CompositionParent
        from trade_platform.historical_market_data import ObservationKind

        transport = _fixture_transport(start, end)

        def adapter_factory(configuration: Any, now: Any) -> Any:
            return BybitCryptoHistoricalAdapter(
                configuration, transport=transport, now=now, sleep=lambda _s: None
            )

        service = HistoricalAcquisitionService.for_postgres(
            cls.database, adapter_factory=adapter_factory, now=lambda: ACQUIRE_NOW
        )
        version = f"phase3d3-parent-{name}"
        request = HistoricalAcquisitionRequest(
            source_id=cls.source_id,
            instrument_id=INSTRUMENT_ID,
            provider="bybit",
            provider_symbol=SYMBOL,
            start=start,
            end=end,
            observation_kinds=frozenset(
                {
                    ObservationKind.OHLCV,
                    ObservationKind.MARK_PRICE,
                    ObservationKind.INDEX_PRICE,
                    ObservationKind.OPEN_INTEREST,
                }
            ),
            normalization_version=normalization,
            dataset_version=version,
            maximum_pages_per_kind=8,
            materialize_features=True,
            idempotency_key="",
        )
        request = replace(request, idempotency_key=acquisition_fingerprint(request))
        result = service.acquire(
            request,
            ProviderConfiguration(
                provider="bybit",
                base_url="https://api.bybit.com",
                terms_accepted=True,
                secret_reference=None,
            ),
        )
        assert result.status is AcquisitionStatus.SUCCEEDED, result.failure_code
        assert result.dataset_version_id is not None and result.dataset_content_hash is not None
        return CompositionParent(
            result.dataset_version_id, version, result.dataset_content_hash, start, end
        )

    def _request(self, *names: str, features: bool = False) -> Any:
        from trade_platform.historical_dataset_composition_v1 import DatasetCompositionRequest
        from trade_platform.historical_market_data import ObservationKind

        parents = tuple(self.parents[name] for name in names)
        return DatasetCompositionRequest(
            source_id=self.source_id,
            instrument_id=INSTRUMENT_ID,
            provider="bybit",
            provider_symbol=SYMBOL,
            normalization_version=NORMALIZATION_VERSION,
            observation_kinds=frozenset(
                {
                    ObservationKind.OHLCV,
                    ObservationKind.MARK_PRICE,
                    ObservationKind.INDEX_PRICE,
                    ObservationKind.OPEN_INTEREST,
                }
            ),
            start=parents[0].start,
            end=parents[-1].end,
            parents=parents,
            materialize_features=features,
        )

    def _service(self) -> Any:
        from trade_platform.historical_dataset_composition_v1 import (
            HistoricalDatasetCompositionService,
        )

        return HistoricalDatasetCompositionService.for_postgres(
            self.database, now=lambda: COMPOSE_NOW
        )

    def _scalar(self, sql: str, params: tuple[object, ...] = ()) -> Any:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()[0]

    def _counts(self) -> tuple[int, int, int]:
        return (
            int(self._scalar("SELECT COUNT(*) FROM historical_raw_observations")),
            int(self._scalar("SELECT COUNT(*) FROM historical_normalized_observations")),
            int(self._scalar("SELECT COUNT(*) FROM historical_dataset_versions")),
        )

    def _member_ids(self, dataset_version_id: UUID) -> list[UUID]:
        from trade_platform.historical_dataset_composition_v1 import PostgresCompositionEvidence

        return list(PostgresCompositionEvidence(self.database).dataset_member_ids(dataset_version_id))

    def _refused(self, request: Any, prefix: str) -> None:
        from trade_platform.historical_dataset_composition_v1 import DatasetCompositionError

        before = self._counts()
        with self.assertRaises(DatasetCompositionError) as raised:
            self._service().compose(request)
        self.assertTrue(raised.exception.code.startswith(prefix), raised.exception.code)
        self.assertEqual(self._counts(), before, "a refused composition must write nothing")

    def _seal_extra(self, version: str, member_ids: list[UUID]) -> Any:
        from trade_platform.historical_market_data import PostgresHistoricalMarketDataPipeline

        return PostgresHistoricalMarketDataPipeline(self.database).seal_dataset(
            self.source_id, version, NORMALIZATION_VERSION, tuple(member_ids), ACQUIRE_NOW
        )

    def _stray_ohlcv_revision(self, event_at: datetime) -> UUID:
        """Persist a normalized revision-1 OHLCV row that no dataset contains."""
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            RawHistoricalObservation,
        )

        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT provider_identifier,provider_symbol,exchange,effective_at,"
                "provenance_uri,raw_payload FROM historical_raw_observations "
                "WHERE source_id=%s AND observation_kind='OHLCV' AND event_at=%s AND revision=0",
                (self.source_id, event_at),
            )
            row = cursor.fetchone()
        assert row is not None
        pipeline = PostgresHistoricalMarketDataPipeline(self.database)
        raw_id = pipeline.capture_raw(
            [
                RawHistoricalObservation(
                    source_id=self.source_id,
                    observation_kind=ObservationKind.OHLCV,
                    provider_identifier=row[0],
                    provider_symbol=row[1],
                    exchange=row[2],
                    event_at=event_at,
                    effective_at=row[3],
                    ingested_at=ACQUIRE_NOW,
                    adjustment_status=AdjustmentStatus.RAW,
                    revision=1,
                    provenance_uri=row[4],
                    raw_payload=row[5],
                )
            ]
        )[0]
        return pipeline.normalize(raw_id, NORMALIZATION_VERSION, ACQUIRE_NOW).normalized_observation_id

    # ---- tests ---------------------------------------------------------------

    def test_happy_path_replay_features_and_research_compatibility(self) -> None:
        from trade_platform.feature_authority import PostgresFeatureAuthority
        from trade_platform.historical_acquisition import (
            HistoricalAcquisitionRequest,
            PostgresAcquisitionFeatureMaterializer,
            acquisition_fingerprint,
            sealed_dataset_matches_request,
        )
        from trade_platform.historical_dataset_composition_v1 import (
            PostgresCompositionEvidence,
            composed_dataset_version,
        )
        from trade_platform.historical_market_data import ObservationKind
        from trade_platform.strategy_feature_binding_v2 import (
            FeatureSubjectType,
            ResearchFeatureBundleRequestV2,
            ResearchFeatureBundleStatus,
            ResearchFeatureRequirementV2,
            build_research_feature_bundle,
        )
        from trade_platform.tradable_bar_evidence_v2 import PostgresTradableBarEvidenceReaderV2
        from trade_platform.tradable_research_evidence_v2 import (
            SubjectAwareTradableResearchEvidenceV2,
            TradableResearchEvidenceV2Error,
        )

        request = self._request("A", "B", "C", features=True)
        target_version = composed_dataset_version(request)
        parent_state = {
            name: (p.content_hash, len(self._member_ids(p.dataset_version_id)))
            for name, p in self.parents.items()
        }
        counts_before = self._counts()

        # No socket connect may be initiated by Python code during composition.
        with mock.patch("socket.socket.connect", side_effect=AssertionError("network call")):
            result = self._service().compose(request)

        self.assertFalse(result.already_completed)
        raw_b, norm_b, datasets_b = counts_before
        self.assertEqual(self._counts(), (raw_b, norm_b, datasets_b + 1))  # zero raw/normalized
        self.assertEqual(
            {
                name: (p.content_hash, len(self._member_ids(p.dataset_version_id)))
                for name, p in self.parents.items()
            },
            parent_state,
            "parents must be unchanged",
        )
        self.assertEqual(result.dataset_version, target_version)
        self.assertEqual(
            dict(result.member_count_by_kind),
            {
                ObservationKind.OHLCV: 30,
                ObservationKind.MARK_PRICE: 30,
                ObservationKind.INDEX_PRICE: 30,
                ObservationKind.OPEN_INTEREST: 6,
            },
        )

        # Exactly the union of the parents' members, each once.
        union = [
            m for name in "ABC" for m in self._member_ids(self.parents[name].dataset_version_id)
        ]
        self.assertEqual(sorted(map(str, self._member_ids(result.dataset_version_id))),
                         sorted(map(str, union)))
        self.assertEqual(len(set(union)), len(union))

        # Post-compose exact sealed-dataset identity proof, independent of the service.
        stored = PostgresCompositionEvidence(self.database).existing_sealed_dataset(
            self.source_id, target_version
        )
        assert stored is not None
        combined = HistoricalAcquisitionRequest(
            source_id=self.source_id, instrument_id=INSTRUMENT_ID, provider="bybit",
            provider_symbol=SYMBOL, start=START, end=START + 3 * WINDOW,
            observation_kinds=request.observation_kinds,
            normalization_version=NORMALIZATION_VERSION, dataset_version=target_version,
            maximum_pages_per_kind=1, materialize_features=False, idempotency_key="",
        )
        combined = replace(combined, idempotency_key=acquisition_fingerprint(combined))
        self.assertTrue(sealed_dataset_matches_request(stored, combined))
        self.assertEqual(stored.content_hash, result.dataset_content_hash)
        self.assertEqual(stored.dataset_version_id, result.dataset_version_id)

        # Canonical features are recomputed on the combined identity.
        assert result.feature_counts is not None
        self.assertEqual(result.feature_counts.crypto_mark_index_basis, 30)
        # 6 OI observations -> 5 changes; three separate parents would give 3 x 1 = 3.
        self.assertEqual(result.feature_counts.open_interest_change, 5)
        feature_total = int(self._scalar("SELECT COUNT(*) FROM feature_materializations"))

        # Idempotent replay: same dataset, nothing resealed, no duplicate features.
        again = self._service().compose(request)
        self.assertTrue(again.already_completed)
        self.assertEqual(again.dataset_version_id, result.dataset_version_id)
        self.assertEqual(again.feature_counts, result.feature_counts)
        self.assertEqual(self._counts(), (raw_b, norm_b, datasets_b + 1))
        self.assertEqual(
            int(self._scalar("SELECT COUNT(*) FROM feature_materializations")), feature_total
        )
        self.assertEqual(
            int(self._scalar(
                "SELECT COUNT(*) FROM historical_dataset_versions WHERE source_id=%s AND version=%s",
                (self.source_id, target_version),
            )),
            1,
        )
        # A replay without features never materializes anything either.
        plain = self._service().compose(replace(request, materialize_features=False))
        self.assertIsNone(plain.feature_counts)
        self.assertEqual(
            int(self._scalar("SELECT COUNT(*) FROM feature_materializations")), feature_total
        )

        # Dataset-bound research authorities read the new dataset ...
        reader = PostgresTradableBarEvidenceReaderV2(self.database)
        series = reader.series(
            dataset_version_id=result.dataset_version_id,
            instrument_id=INSTRUMENT_ID,
            interval="1m",
            research_run_at=RESEARCH_AT,
        )
        self.assertEqual(len(series.bars), 30)
        self.assertEqual(
            [b.bar_open_at for b in series.bars], [START + i * _MINUTE for i in range(30)]
        )
        self.assertEqual({b.dataset_content_hash for b in series.bars},
                         {result.dataset_content_hash})

        authority = PostgresFeatureAuthority(self.database)
        materializer = PostgresAcquisitionFeatureMaterializer(self.database)
        basis_id = materializer.resolve_basis_feature_id(COMPOSE_NOW)
        definition = authority.definition(basis_id)
        requirement = ResearchFeatureRequirementV2(
            basis_id, definition.name, definition.semantic_version, FeatureSubjectType.INSTRUMENT
        )

        def bundle_for(dataset_version_id: UUID) -> Any:
            outcome = build_research_feature_bundle(
                authority,
                ResearchFeatureBundleRequestV2(
                    subject_type=FeatureSubjectType.INSTRUMENT,
                    subject_id=INSTRUMENT_ID,
                    dataset_version_id=dataset_version_id,
                    decision_at=RESEARCH_AT,
                    requirements=(requirement,),
                ),
            )
            self.assertIs(outcome.status, ResearchFeatureBundleStatus.AVAILABLE, outcome.reasons)
            return outcome.bundle

        evidence = SubjectAwareTradableResearchEvidenceV2.create(
            feature_bundle=bundle_for(result.dataset_version_id), bar_series=series
        )
        self.assertEqual(len(evidence.feature_bundle.feature_series[0].materializations), 30)
        # ... while the exact-dataset equality is unchanged: a parent's features
        # can never be paired with the composed dataset's bars.
        with self.assertRaises(TradableResearchEvidenceV2Error):
            SubjectAwareTradableResearchEvidenceV2.create(
                feature_bundle=bundle_for(self.parents["A"].dataset_version_id), bar_series=series
            )

    def test_oi_predecessor_continuity_across_parent_boundary(self) -> None:
        from trade_platform.historical_acquisition import PostgresAcquisitionFeatureMaterializer

        request = self._request("A", "B", features=True)
        result = self._service().compose(request)
        assert result.feature_counts is not None
        # A: OI 00:00,00:05 ; B: OI 00:10,00:15 -> combined 4 obs, 3 changes.
        # Parents alone contribute 1 + 1 = 2; the boundary value is the extra one.
        self.assertEqual(result.feature_counts.open_interest_change, 3)

        oi_id = PostgresAcquisitionFeatureMaterializer(self.database).resolve_open_interest_feature_id(
            COMPOSE_NOW
        )
        boundary = START + 2 * _FIVE  # first OI event of parent B: 00:10

        def row(dataset_version_id: UUID) -> Any:
            with self.database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT value,source_observation_manifest FROM feature_materializations "
                    "WHERE feature_id=%s AND dataset_version=%s AND event_at=%s",
                    (oi_id, str(dataset_version_id), boundary),
                )
                return cursor.fetchone()

        # Parent B, taken alone, has no predecessor for its first OI event ...
        self.assertIsNone(row(self.parents["B"].dataset_version_id))
        # ... while under the combined dataset it uses parent A's final observation.
        combined = row(result.dataset_version_id)
        self.assertIsNotNone(combined)
        self.assertEqual(Decimal(str(combined[0])), Decimal("10"))
        manifest = combined[1] if isinstance(combined[1], list) else json.loads(combined[1])
        prior_events = [
            datetime.fromisoformat(token.split(":", 1)[1])
            for token in manifest
            if token.startswith("prior_event_at:")
        ]
        self.assertEqual(prior_events, [START + _FIVE])
        prior_ids = [t.split(":", 1)[1] for t in manifest if t.startswith("prior_normalized_observation_id:")]
        parent_a_members = {str(m) for m in self._member_ids(self.parents["A"].dataset_version_id)}
        self.assertTrue(prior_ids[0] in parent_a_members)

    def test_unrelated_source_evidence_never_enters_the_composition(self) -> None:
        stray_event = START + 15 * _MINUTE  # inside parent B's window
        stray_id = self._stray_ohlcv_revision(stray_event)
        request = self._request("B", "C")
        result = self._service().compose(request)
        members = set(self._member_ids(result.dataset_version_id))
        self.assertNotIn(stray_id, members)
        # parent D/E evidence sits in the same source but is not an authorized parent
        for name in "ADE":
            self.assertFalse(members & set(self._member_ids(self.parents[name].dataset_version_id)))
        self.assertEqual(sum(result.member_count_by_kind.values()), 64)

    def test_target_version_semantic_conflict(self) -> None:
        from trade_platform.historical_dataset_composition_v1 import composed_dataset_version

        request = self._request("C", "D")
        c_members = self._member_ids(self.parents["C"].dataset_version_id)
        squatter = self._seal_extra(composed_dataset_version(request), c_members[:-1])
        self._refused(request, "composition_version_conflict")
        # the conflicting evidence is left exactly as it was
        self.assertEqual(len(self._member_ids(squatter.dataset_version_id)), len(c_members) - 1)

    def test_missing_and_mismatched_parents_write_nothing(self) -> None:
        from trade_platform.historical_dataset_composition_v1 import CompositionParent

        base = self._request("A", "B", "C")
        b = self.parents["B"]
        ghost = CompositionParent(uuid4(), "phase3d3-no-such-parent", "0" * 64, b.start, b.end)
        self._refused(replace(base, parents=(base.parents[0], ghost, base.parents[2])),
                      "parent_not_sealed_or_missing")
        self._refused(
            replace(base, parents=(base.parents[0], replace(b, content_hash="f" * 64), base.parents[2])),
            "parent_content_hash_mismatch",
        )
        self._refused(
            replace(base, parents=(base.parents[0], replace(b, dataset_version_id=uuid4()), base.parents[2])),
            "parent_dataset_version_id_mismatch",
        )
        self._refused(replace(base, source_id=uuid4()), "historical_source_not_found")

    def test_wrong_normalization_parent(self) -> None:
        self._refused(self._request("D", "E"), "parent_identity_proof_failed")

    def test_parent_gap_overlap_duplicate_and_order(self) -> None:
        self._refused(self._request("A", "C"), "parent_window_gap")
        a, b = self.parents["A"], self.parents["B"]
        base = self._request("A", "B")
        self._refused(
            replace(base, parents=(a, replace(b, start=b.start - _FIVE))), "parent_window_overlap"
        )
        self._refused(replace(base, parents=(a, b, b), end=b.end), "duplicate_parent")
        self._refused(replace(base, parents=(b, a)), "parent_windows_unordered")

    def test_parent_with_extra_revision_member(self) -> None:
        stray = self._stray_ohlcv_revision(START + 12 * _MINUTE)
        b = self.parents["B"]
        polluted = self._seal_extra(
            "phase3d3-parent-B-extra-revision",
            [*self._member_ids(b.dataset_version_id), stray],
        )
        from trade_platform.historical_dataset_composition_v1 import CompositionParent

        parent = CompositionParent(
            polluted.dataset_version_id, polluted.version, polluted.content_hash, b.start, b.end
        )
        base = self._request("A", "B", "C")
        self._refused(
            replace(base, parents=(base.parents[0], parent, base.parents[2])),
            "parent_identity_proof_failed",
        )

    def test_parent_event_set_mismatch(self) -> None:
        b = self.parents["B"]
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT m.normalized_observation_id FROM historical_dataset_members m "
                "JOIN historical_normalized_observations n "
                "ON n.normalized_observation_id=m.normalized_observation_id "
                "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
                "WHERE m.dataset_version_id=%s AND r.observation_kind='OHLCV' "
                "ORDER BY r.event_at LIMIT 1",
                (b.dataset_version_id,),
            )
            dropped = UUID(str(cursor.fetchone()[0]))
        short = self._seal_extra(
            "phase3d3-parent-B-short",
            [m for m in self._member_ids(b.dataset_version_id) if m != dropped],
        )
        from trade_platform.historical_dataset_composition_v1 import CompositionParent

        parent = CompositionParent(
            short.dataset_version_id, short.version, short.content_hash, b.start, b.end
        )
        base = self._request("A", "B", "C")
        self._refused(
            replace(base, parents=(base.parents[0], parent, base.parents[2])),
            "parent_identity_proof_failed",
        )


if __name__ == "__main__":
    unittest.main()
