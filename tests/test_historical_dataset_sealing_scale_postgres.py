"""Phase 3D.7R.1: scalable canonical dataset sealing, proven on real PostgreSQL.

``PostgresHistoricalMarketDataPipeline.seal_dataset`` used to select its members
with one bind placeholder per member, which PostgreSQL rejects above 65,535
parameters -- the real 150-day composite (691,200 members) hit exactly that. These
tests prove the fix changes persistence mechanics only:

* the content hash is byte-identical to the pre-3D.7R.1 algorithm (a verbatim
  reference implementation below), including typed payload and OHLCV
  volume-semantics components, and is independent of member input order;
* every fail-closed check still fails closed, with the same error, writing nothing;
* no statement binds one parameter per member, and a dataset of more than 65,535
  members seals with its exact member set persisted.

The class owns a DISPOSABLE database beneath the configured local/CI PostgreSQL
instance; the research database is never referenced. All market values are
synthetic FIXTURES served by an in-process transport or inserted directly into the
disposable database; no socket to any provider is opened.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self, cast
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
NORMALIZATION_VERSION = "bybit-v5-md-phase3d7r1"
WINDOW = timedelta(minutes=10)

SCALE_MEMBERS = 70_000  # strictly above PostgreSQL's 65,535 bind-parameter ceiling
SCALE_NORMALIZATION = "scale-fixture-normalizer-phase3d7r1"
SCALE_EVENT_START = datetime(2020, 1, 1, tzinfo=UTC)
SCALE_NORMALIZED_AT = datetime(2020, 3, 1, tzinfo=UTC)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)
_FIVE = timedelta(minutes=5)


def _ms(value: datetime) -> int:
    return int((value - _EPOCH).total_seconds()) * 1000


def _envelope(result: dict[str, object]) -> str:
    return json.dumps(
        {"retCode": 0, "retMsg": "OK", "result": result, "retExtInfo": {}, "time": _ms(ACQUIRE_NOW)}
    )


class _RoutingTransport:
    def __init__(self, routes: dict[str, list[object]]) -> None:
        self._routes = {path: list(bodies) for path, bodies in routes.items()}

    def get(self, url: str, timeout_seconds: float) -> object:
        for path, bodies in self._routes.items():
            if path in url:
                if not bodies:
                    raise AssertionError(f"exhausted route: {path}")
                return bodies.pop(0)
        raise AssertionError(f"unrouted request: {url}")


def _fixture_transport(start: datetime, end: datetime) -> _RoutingTransport:
    from trade_platform.data_providers import HttpResponse

    opens = [start + index * _MINUTE for index in range(int((end - start) / _MINUTE))]
    instants = [start + index * _FIVE for index in range(int((end - start) / _FIVE))]

    def minute(value: datetime) -> int:
        return int((value - START) / _MINUTE)

    def klines(rows: list[list[str]]) -> Any:
        return HttpResponse(
            200, _envelope({"category": "linear", "symbol": SYMBOL, "list": list(reversed(rows))})
        )

    trade = [
        [str(_ms(o)), "27000.0", "27100.0", "26900.0", f"{27000 + minute(o)}.0", "12.5", "337500.0"]
        for o in opens
    ]
    mark = [[str(_ms(o)), "27000.0", "27100.0", "26900.0", f"{27010 + minute(o)}.0"] for o in opens]
    index = [[str(_ms(o)), "27000.0", "27100.0", "26900.0", f"{27005 + minute(o)}.0"] for o in opens]
    interest = HttpResponse(
        200,
        _envelope(
            {
                "category": "linear",
                "symbol": SYMBOL,
                "list": [
                    {"openInterest": f"{461000 + 10 * (minute(i) // 5)}.0", "timestamp": str(_ms(i))}
                    for i in reversed(instants)
                ],
                "nextPageCursor": "",
            }
        ),
    )
    return _RoutingTransport(
        {
            "/v5/market/kline": [klines(trade)],
            "/v5/market/mark-price-kline": [klines(mark)],
            "/v5/market/index-price-kline": [klines(index)],
            "/v5/market/open-interest": [interest],
        }
    )


class _CursorSpy:
    """Delegating cursor that records how many bind parameters each statement carries."""

    def __init__(self, inner: Any, counts: list[int]) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_counts", counts)

    def __enter__(self) -> Self:
        self._inner.__enter__()
        return self

    def __exit__(self, *exc: object) -> Any:
        return self._inner.__exit__(*exc)

    def execute(self, query: Any, params: Any = None) -> Any:
        self._counts.append(0 if params is None else len(params))
        return self._inner.execute(query, params)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._inner)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)


class _ConnectionSpy:
    def __init__(self, inner: Any, counts: list[int]) -> None:
        self._inner = inner
        self._counts = counts

    def cursor(self, *args: Any, **kwargs: Any) -> _CursorSpy:
        return _CursorSpy(self._inner.cursor(*args, **kwargs), self._counts)


class _ParameterSpyDatabase:
    """Wraps a PostgresDatabase; every statement's bind-parameter count is recorded."""

    def __init__(self, database: Any) -> None:
        self._database = database
        self.counts: list[int] = []

    @contextmanager
    def transaction(self) -> Iterator[_ConnectionSpy]:
        with self._database.transaction() as connection:
            yield _ConnectionSpy(connection, self.counts)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ScalableDatasetSealingPostgresTests(unittest.TestCase):
    database_name: str
    dsn: str
    database: Any
    source_id: UUID
    parent_ids: tuple[UUID, UUID]
    parent_hashes: tuple[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest("sealing scale tests require a local or CI disposable PostgreSQL")
        cls.database_name = f"historical_sealing_phase3d7r1_{os.getpid()}"
        cls.dsn = urlunparse(urlparse(source_dsn)._replace(path=f"/{cls.database_name}"))
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
        first = cls._acquire("A", START, START + WINDOW)
        second = cls._acquire("B", START + WINDOW, START + 2 * WINDOW)
        cls.parent_ids = (first[0], second[0])
        cls.parent_hashes = (first[1], second[1])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()
        with psycopg.connect(os.environ["POSTGRES_TEST_DSN"], autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    # ---- fixtures ----------------------------------------------------------

    @classmethod
    def _acquire(cls, name: str, start: datetime, end: datetime) -> tuple[UUID, str]:
        """Seal one real typed Bybit-shaped dataset through the Phase 3B.1 service."""
        from trade_platform.bybit_crypto_provider import BybitCryptoHistoricalAdapter
        from trade_platform.data_providers import ProviderConfiguration
        from trade_platform.historical_acquisition import (
            AcquisitionStatus,
            HistoricalAcquisitionRequest,
            HistoricalAcquisitionService,
            acquisition_fingerprint,
        )
        from trade_platform.historical_market_data import ObservationKind

        transport = _fixture_transport(start, end)
        service = HistoricalAcquisitionService.for_postgres(
            cls.database,
            adapter_factory=lambda configuration, now: BybitCryptoHistoricalAdapter(
                configuration, transport=transport, now=now, sleep=lambda _s: None
            ),
            now=lambda: ACQUIRE_NOW,
        )
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
            normalization_version=NORMALIZATION_VERSION,
            dataset_version=f"phase3d7r1-parent-{name}",
            maximum_pages_per_kind=8,
            materialize_features=False,
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
        return result.dataset_version_id, result.dataset_content_hash

    def _pipeline(self, database: Any = None) -> Any:
        from trade_platform.historical_market_data import PostgresHistoricalMarketDataPipeline

        return PostgresHistoricalMarketDataPipeline(database or self.database)

    def _rows(self, sql: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    def _members(self, dataset_version_id: UUID) -> list[UUID]:
        return [
            cast(UUID, row[0])
            for row in self._rows(
                "SELECT normalized_observation_id FROM historical_dataset_members "
                "WHERE dataset_version_id=%s",
                (dataset_version_id,),
            )
        ]

    def _dataset_count(self) -> int:
        return int(self._rows("SELECT COUNT(*) FROM historical_dataset_versions")[0][0])

    def _reference_digest(
        self, member_ids: list[UUID], *, typed: bool = True, volume: bool = True
    ) -> str:
        """The pre-3D.7R.1 content-hash algorithm, verbatim, fetched in bounded chunks.

        ``typed``/``volume`` may be switched off only to prove those components
        genuinely contribute to the hash; with both on this is the old algorithm.
        """
        from trade_platform import historical_market_data as hmd

        keys = sorted(member_ids, key=str)
        digest = hashlib.sha256()
        for offset in range(0, len(keys), 20_000):
            chunk = keys[offset : offset + 20_000]
            placeholders = ",".join(["%s"] * len(chunk))
            rows = self._rows(
                "SELECT n.normalized_observation_id,n.normalization_version,n.quality_status,"
                "n.normalized_value,n.normalized_at,r.source_id,r.event_at,r.ingested_at,"
                "r.raw_payload_sha256,r.observation_kind,"
                f"{hmd._TYPED_PAYLOAD_COLUMNS},{hmd._VOLUME_SEMANTICS_COLUMNS} "
                "FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
                f"{hmd._TYPED_PAYLOAD_JOINS} {hmd._VOLUME_SEMANTICS_JOIN} "
                f"WHERE n.normalized_observation_id IN ({placeholders})",  # nosec B608 - test reference
                tuple(chunk),
            )
            self.assertEqual(len(rows), len(chunk))
            for row in sorted(rows, key=lambda item: str(item[0])):
                digest.update(
                    "|".join(
                        (
                            str(row[0]),
                            str(row[8]),
                            str(row[9]),
                            hmd._canonical(row[3]),
                            *(hmd._sealed_typed_components(row) if typed else ()),
                            *(hmd._sealed_volume_semantics_components(row, 30) if volume else ()),
                        )
                    ).encode()
                )
        return digest.hexdigest()

    # ---- content-hash compatibility -----------------------------------------

    def test_sealed_hash_is_identical_to_pre_3d7r1_algorithm_and_input_order_free(self) -> None:
        for dataset_id, stored in zip(self.parent_ids, self.parent_hashes, strict=True):
            members = self._members(dataset_id)
            # 10 one-minute OHLCV/mark/index bars + 2 five-minute OI instants.
            self.assertEqual(len(members), 32)
            self.assertEqual(self._reference_digest(members), stored)
        union = self._members(self.parent_ids[0]) + self._members(self.parent_ids[1])
        pipeline = self._pipeline()
        expected = self._reference_digest(union)
        shuffled = list(union)
        random.Random(3710).shuffle(shuffled)
        for ordering in (union, list(reversed(union)), shuffled, sorted(union, key=str)):
            digest, _valid_from, _valid_until = pipeline._proven_member_digest(
                self.source_id, NORMALIZATION_VERSION, ordering, ACQUIRE_NOW
            )
            self.assertEqual(digest, expected)

        # A new dataset sealed over the union persists exactly its members and
        # carries the reference hash (seal inputs deliberately shuffled).
        before = self._dataset_count()
        dataset = pipeline.seal_dataset(
            self.source_id, "phase3d7r1-union", NORMALIZATION_VERSION, tuple(shuffled), ACQUIRE_NOW
        )
        self.assertEqual(self._dataset_count(), before + 1)
        self.assertEqual(dataset.content_hash, expected)
        persisted = self._members(dataset.dataset_version_id)
        self.assertEqual(len(persisted), len(union))
        self.assertEqual(set(persisted), set(union))
        bounds = self._rows(
            "SELECT MIN(r.event_at),MAX(r.event_at) FROM historical_normalized_observations n "
            "JOIN historical_raw_observations r USING(raw_observation_id) "
            "WHERE n.normalized_observation_id = ANY(%s::uuid[])",
            (union,),
        )[0]
        self.assertEqual((dataset.valid_from, dataset.valid_until), bounds)

    def test_typed_payloads_and_volume_semantics_still_contribute_to_the_hash(self) -> None:
        members = self._members(self.parent_ids[0])
        kinds = {
            str(row[0]): int(row[1])
            for row in self._rows(
                "SELECT r.observation_kind, COUNT(vs.normalized_observation_id) "
                "FROM historical_dataset_members m "
                "JOIN historical_normalized_observations n USING(normalized_observation_id) "
                "JOIN historical_raw_observations r USING(raw_observation_id) "
                "LEFT JOIN historical_ohlcv_volume_semantics vs USING(normalized_observation_id) "
                "WHERE m.dataset_version_id=%s GROUP BY 1",
                (self.parent_ids[0],),
            )
        }
        self.assertEqual(set(kinds), {"OHLCV", "MARK_PRICE", "INDEX_PRICE", "OPEN_INTEREST"})
        self.assertEqual(kinds["OHLCV"], 10, "every OHLCV member carries typed volume semantics")
        stored = self.parent_hashes[0]
        self.assertEqual(self._reference_digest(members), stored)
        self.assertNotEqual(self._reference_digest(members, typed=False), stored)
        self.assertNotEqual(self._reference_digest(members, volume=False), stored)

    # ---- fail-closed validation is unchanged ---------------------------------

    def test_every_fail_closed_check_still_refuses_and_writes_nothing(self) -> None:
        from trade_platform.historical_market_data import HistoricalMarketDataError

        pipeline = self._pipeline()
        members = tuple(self._members(self.parent_ids[0]))
        cases: list[tuple[str, UUID, str, tuple[UUID, ...], datetime]] = [
            ("dataset_member_not_found", self.source_id, NORMALIZATION_VERSION,
             (*members, uuid4()), ACQUIRE_NOW),
            ("dataset_source_mismatch", uuid4(), NORMALIZATION_VERSION, members, ACQUIRE_NOW),
            ("dataset_normalization_version_mismatch", self.source_id, "other-normalizer",
             members, ACQUIRE_NOW),
            ("dataset_created_before_member_available", self.source_id, NORMALIZATION_VERSION,
             members, ACQUIRE_NOW - timedelta(microseconds=1)),
            ("invalid_dataset_members", self.source_id, NORMALIZATION_VERSION,
             (*members, members[0]), ACQUIRE_NOW),
            ("invalid_dataset_members", self.source_id, NORMALIZATION_VERSION, (), ACQUIRE_NOW),
        ]
        for code, source_id, normalization, candidate, created_at in cases:
            before = (self._dataset_count(), len(self._rows("SELECT 1 FROM historical_dataset_members")))
            with self.subTest(code=code), self.assertRaises(HistoricalMarketDataError) as raised:
                pipeline.seal_dataset(
                    source_id, f"phase3d7r1-refused-{uuid4()}", normalization, candidate, created_at
                )
            self.assertEqual(str(raised.exception), code)
            after = (self._dataset_count(), len(self._rows("SELECT 1 FROM historical_dataset_members")))
            self.assertEqual(after, before, f"{code} must write nothing")

    # ---- scale ---------------------------------------------------------------

    def _scale_members(self) -> list[UUID]:
        """Persist SCALE_MEMBERS legacy (untyped) validated OHLCV fixtures in one statement pair."""
        from trade_platform.historical_market_data import (
            AssetScope,
            AuthorizedHistoricalSource,
            ObservationKind,
        )

        pipeline = self._pipeline()
        source = AuthorizedHistoricalSource(
            "phase3d7r1_scale_fixture", "phase3d7r1-scale-fixture", "PHASE3D7R1:ID",
            "test-terms-v1", "test-authorization://phase3d7r1-scale-only",
            ONBOARDED_AT, ONBOARDED_AT, asset_scope=AssetScope.CRYPTO.value,
            authorized_observation_kinds=frozenset({ObservationKind.OHLCV}),
        )
        pipeline.register_source(source)
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO historical_raw_observations (raw_observation_id,source_id,observation_kind,"
                "provider_identifier,provider_symbol,exchange,event_at,effective_at,ingested_at,"
                "adjustment_status,revision,provenance_uri,raw_payload,raw_payload_sha256) "
                "SELECT gen_random_uuid(),%s,'OHLCV','phase3d7r1-scale','SCALE','BYBIT',"
                "%s + i * interval '1 minute',%s + (i + 1) * interval '1 minute',"
                "%s + (i + 1) * interval '1 minute','RAW',0,'fixture://phase3d7r1/scale/' || i,"
                "p.payload,encode(sha256(convert_to(p.payload::text,'UTF8')),'hex') "
                "FROM generate_series(0,%s - 1) AS i, LATERAL (SELECT jsonb_build_object("
                "'interval','1m','open','1','high','2','low','1','close',(1 + i)::text,"
                "'volume','3') AS payload) p",
                (source.source_id, SCALE_EVENT_START, SCALE_EVENT_START, SCALE_EVENT_START,
                 SCALE_MEMBERS),
            )
            cursor.execute(
                "INSERT INTO historical_normalized_observations (normalized_observation_id,"
                "raw_observation_id,instrument_id,normalization_version,normalized_value,"
                "quality_status,quality_issues,normalized_at) "
                "SELECT gen_random_uuid(),r.raw_observation_id,%s,%s,r.raw_payload,'VALIDATED',"
                "'[]'::jsonb,%s FROM historical_raw_observations r WHERE r.source_id=%s "
                "RETURNING normalized_observation_id",
                (INSTRUMENT_ID, SCALE_NORMALIZATION, SCALE_NORMALIZED_AT, source.source_id),
            )
            ids = [cast(UUID, row[0]) for row in cursor.fetchall()]
        self.assertEqual(len(ids), SCALE_MEMBERS)
        self._scale_source_id = source.source_id
        return ids

    def test_more_than_65535_members_seal_exactly_with_bounded_parameters(self) -> None:
        members = self._scale_members()

        # Negative control: the pre-3D.7R.1 statement shape really is refused here.
        placeholders = ",".join(["%s"] * len(members))
        with self.assertRaises(psycopg.Error), psycopg.connect(self.dsn) as connection:
            connection.execute(
                "SELECT 1 FROM historical_normalized_observations "  # nosec B608 - negative control
                f"WHERE normalized_observation_id IN ({placeholders})",
                members,
            )

        spy = _ParameterSpyDatabase(self.database)
        shuffled = list(members)
        random.Random(65536).shuffle(shuffled)
        dataset = self._pipeline(spy).seal_dataset(
            self._scale_source_id, "phase3d7r1-scale", SCALE_NORMALIZATION,
            tuple(shuffled), SCALE_NORMALIZED_AT,
        )
        # No statement carries one bind parameter per member: the member set is a
        # single array parameter in both the proof read and the member insert.
        self.assertTrue(spy.counts, "seal_dataset executed no statement")
        self.assertLessEqual(max(spy.counts), 8, spy.counts)

        persisted = self._members(dataset.dataset_version_id)
        self.assertEqual(len(persisted), SCALE_MEMBERS)
        self.assertEqual(set(persisted), set(members))
        self.assertEqual(dataset.content_hash, self._reference_digest(members))
        self.assertEqual(dataset.valid_from, SCALE_EVENT_START)
        self.assertEqual(
            dataset.valid_until, SCALE_EVENT_START + (SCALE_MEMBERS - 1) * _MINUTE
        )


if __name__ == "__main__":
    unittest.main()
