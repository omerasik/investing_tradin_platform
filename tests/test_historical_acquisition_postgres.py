"""End-to-end PostgreSQL evidence for the Phase 3B.1 acquisition service.

Every price, quantity and provider response here is a synthetic FIXTURE served
by a scripted in-process transport; nothing is retrieved from or verified
against Bybit, and no socket is opened. The real canonical
``CRYPTO:BYBIT:BTCUSDT:PERP`` is onboarded through the existing idempotent
onboarding, then one :class:`HistoricalAcquisitionService.acquire` call drives
the full pipeline -- provider ingestion, checkpoints, normalization, coverage
validation, one sealed dataset and canonical feature materialization -- proving
the Phase 3A 30/30/30/6 shape end-to-end (96 raw, 96 validated, 0 rejected, one
sealed dataset, 30 tradable bars, 30 mark/index basis features, 5
open-interest-change features).

The canonical feature *names* are tagged with a fixture semantic version here so
this file does not collide with the database-wide feature-definition-singleton
invariants other integration tests assert on the shared CI database. The service
uses the unmodified canonical definitions in production.
"""

from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

VENUE = "BYBIT"
SYMBOL = "BTCUSDT"
INSTRUMENT_ID = "CRYPTO:BYBIT:BTCUSDT:PERP"

ONBOARDED_AT = datetime(2026, 6, 1, tzinfo=UTC)
START = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 7, 1, 0, 30, tzinfo=UTC)
NOW = datetime(2026, 7, 1, 1, 0, tzinfo=UTC)  # after the window closes
NORMALIZATION_VERSION = "bybit-v5-md-phase3b1"
DATASET_VERSION = "bybit-v5-linear-btcusdt-phase3b1-acquisition"
FIXTURE_FEATURE_VERSION = "1.0.0-phase3b1"
FIXTURE_OI_FEATURE_NAME = "phase3b1_oi_delta"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)
_FIVE = timedelta(minutes=5)


def _ms(value: datetime) -> int:
    return int((value - _EPOCH).total_seconds()) * 1000


def _envelope(result: dict[str, object]) -> str:
    return json.dumps(
        {"retCode": 0, "retMsg": "OK", "result": result, "retExtInfo": {}, "time": _ms(NOW)}
    )


def _kline_envelope(rows: list[list[str]]) -> str:
    return _envelope({"category": "linear", "symbol": SYMBOL, "list": rows})


def _open_interest_envelope(rows: list[dict[str, str]]) -> str:
    return _envelope(
        {"category": "linear", "symbol": SYMBOL, "list": rows, "nextPageCursor": ""}
    )


def _trade_row(bar_open: datetime, close: str) -> list[str]:
    return [str(_ms(bar_open)), "27000.0", "27100.0", "26900.0", close, "12.5", "337500.0"]


def _reference_row(bar_open: datetime, close: str) -> list[str]:
    return [str(_ms(bar_open)), "27000.0", "27100.0", "26900.0", close]


class RoutingTransport:
    def __init__(self, routes: dict[str, list[object]]) -> None:
        self._routes = {path: list(bodies) for path, bodies in routes.items()}
        self.urls: list[str] = []

    def get(self, url: str, timeout_seconds: float) -> object:
        self.urls.append(url)
        for path, bodies in self._routes.items():
            if path in url:
                if not bodies:
                    raise AssertionError(f"exhausted route: {path}")
                return bodies.pop(0)
        raise AssertionError(f"unrouted request: {url}")


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class HistoricalAcquisitionPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace(
                "postgresql://", "postgresql+psycopg://", 1
            ),
        )
        command.upgrade(config, "head")

    def test_operator_acquisition_end_to_end(self) -> None:
        from trade_platform.bybit_crypto_provider import BybitCryptoHistoricalAdapter
        from trade_platform.bybit_instrument_onboarding import (
            captured_btcusdt_snapshot_v1,
            onboard_bybit_btcusdt_perpetual_v1,
        )
        from trade_platform.crypto_derivatives_features import (
            crypto_mark_index_basis_definition,
        )
        from trade_platform.data_providers import HttpResponse, ProviderConfiguration
        from trade_platform.historical_acquisition import (
            AcquisitionStatus,
            HistoricalAcquisitionRequest,
            HistoricalAcquisitionService,
            PostgresAcquisitionFeatureMaterializer,
            acquisition_fingerprint,
        )
        from trade_platform.historical_market_data import (
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
        )
        from trade_platform.open_interest_features import open_interest_change_definition
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.tradable_bar_evidence_v2 import (
            PostgresTradableBarEvidenceReaderV2,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        database = PostgresDatabase(dsn)

        # ---- real canonical BTCUSDT onboarding (idempotent) -------------------
        onboarding = onboard_bybit_btcusdt_perpetual_v1(
            database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )
        self.assertEqual(onboarding.instrument_id, INSTRUMENT_ID)
        source_id = onboarding.source_id

        # ---- scripted provider responses (no socket is ever opened) -----------
        bar_opens = [START + index * _MINUTE for index in range(30)]
        oi_instants = [START + index * _FIVE for index in range(6)]
        trade_closes = [f"{27000 + index}.0" for index in range(30)]
        mark_closes = [f"{27010 + index}.0" for index in range(30)]
        index_closes = [f"{27005 + index}.0" for index in range(30)]
        oi_values = [f"{461000 + index * 10}.0" for index in range(6)]

        transport = RoutingTransport(
            {
                "/v5/market/kline": [
                    HttpResponse(
                        200,
                        _kline_envelope(
                            [
                                _trade_row(bar_open, close)
                                for bar_open, close in reversed(
                                    list(zip(bar_opens, trade_closes, strict=True))
                                )
                            ]
                        ),
                    )
                ],
                "/v5/market/mark-price-kline": [
                    HttpResponse(
                        200,
                        _kline_envelope(
                            [
                                _reference_row(bar_open, close)
                                for bar_open, close in reversed(
                                    list(zip(bar_opens, mark_closes, strict=True))
                                )
                            ]
                        ),
                    )
                ],
                "/v5/market/index-price-kline": [
                    HttpResponse(
                        200,
                        _kline_envelope(
                            [
                                _reference_row(bar_open, close)
                                for bar_open, close in reversed(
                                    list(zip(bar_opens, index_closes, strict=True))
                                )
                            ]
                        ),
                    )
                ],
                "/v5/market/open-interest": [
                    HttpResponse(
                        200,
                        _open_interest_envelope(
                            [
                                {"openInterest": value, "timestamp": str(_ms(instant))}
                                for instant, value in reversed(
                                    list(zip(oi_instants, oi_values, strict=True))
                                )
                            ]
                        ),
                    )
                ],
            }
        )

        def adapter_factory(
            configuration: ProviderConfiguration, now: object
        ) -> BybitCryptoHistoricalAdapter:
            return BybitCryptoHistoricalAdapter(
                configuration,
                transport=transport,  # type: ignore[arg-type]
                now=now,  # type: ignore[arg-type]
                sleep=lambda _seconds: None,
            )

        # Fixture-tagged canonical definitions: same formula/calculation version,
        # names/versions tagged only to avoid the shared-DB singleton invariants.
        materializer = PostgresAcquisitionFeatureMaterializer(
            database,
            basis_definition_factory=lambda created_at: replace(
                crypto_mark_index_basis_definition(created_at),
                semantic_version=FIXTURE_FEATURE_VERSION,
            ),
            open_interest_definition_factory=lambda created_at: replace(
                open_interest_change_definition(created_at),
                name=FIXTURE_OI_FEATURE_NAME,
                semantic_version=FIXTURE_FEATURE_VERSION,
            ),
        )

        service = HistoricalAcquisitionService.for_postgres(
            database,
            adapter_factory=adapter_factory,
            feature_materializer=materializer,
            now=lambda: NOW,
        )

        request = HistoricalAcquisitionRequest(
            source_id=source_id,
            instrument_id=INSTRUMENT_ID,
            provider="bybit",
            provider_symbol=SYMBOL,
            start=START,
            end=END,
            observation_kinds=frozenset(
                {
                    ObservationKind.OHLCV,
                    ObservationKind.MARK_PRICE,
                    ObservationKind.INDEX_PRICE,
                    ObservationKind.OPEN_INTEREST,
                }
            ),
            normalization_version=NORMALIZATION_VERSION,
            dataset_version=DATASET_VERSION,
            maximum_pages_per_kind=8,
            materialize_features=True,
            idempotency_key="",
        )
        request = replace(request, idempotency_key=acquisition_fingerprint(request))

        configuration = ProviderConfiguration(
            provider="bybit",
            base_url="https://api.bybit.com",
            terms_accepted=True,
            secret_reference=None,
        )

        result = service.acquire(request, configuration)

        # ---- the acquisition result --------------------------------------------
        self.assertEqual(result.status, AcquisitionStatus.SUCCEEDED, result.failure_code)
        self.assertFalse(result.already_completed)
        self.assertIsNotNone(result.dataset_version_id)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(
            result.raw_counts,
            {
                ObservationKind.OHLCV: 30,
                ObservationKind.MARK_PRICE: 30,
                ObservationKind.INDEX_PRICE: 30,
                ObservationKind.OPEN_INTEREST: 6,
            },
        )
        self.assertEqual(sum(result.raw_counts.values()), 96)
        self.assertEqual(sum(result.normalized_counts.values()), 96)
        self.assertIsNotNone(result.feature_counts)
        assert result.feature_counts is not None
        self.assertEqual(result.feature_counts.crypto_mark_index_basis, 30)
        self.assertEqual(result.feature_counts.open_interest_change, 5)
        self.assertEqual(len(result.checkpoints), 4)
        dataset_version_id = result.dataset_version_id
        assert dataset_version_id is not None

        # ---- raw / normalized / dataset persisted evidence ---------------------
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_raw_observations "
                "WHERE source_id=%s AND event_at>=%s AND event_at<=%s",
                (source_id, START, END),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 96)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r "
                "ON r.raw_observation_id=n.raw_observation_id "
                "WHERE r.source_id=%s AND r.event_at>=%s AND r.event_at<=%s "
                "AND n.quality_status='VALIDATED'",
                (source_id, START, END),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 96)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r "
                "ON r.raw_observation_id=n.raw_observation_id "
                "WHERE r.source_id=%s AND r.event_at>=%s AND r.event_at<=%s "
                "AND n.quality_status<>'VALIDATED'",
                (source_id, START, END),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)
            cursor.execute(
                "SELECT COUNT(*) FROM historical_dataset_versions "
                "WHERE source_id=%s AND version=%s AND status='SEALED'",
                (source_id, DATASET_VERSION),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 1)

        # ---- research_query returns every acquired observation ------------------
        pipeline = PostgresHistoricalMarketDataPipeline(database)
        observations = pipeline.research_query(
            dataset_version_id, INSTRUMENT_ID, START, END, NOW
        )
        self.assertEqual(len(observations), 96)

        # ---- tradable-bar reader over the sealed 1m trade bars ------------------
        reader = PostgresTradableBarEvidenceReaderV2(database)
        series = reader.series(
            dataset_version_id=dataset_version_id,
            instrument_id=INSTRUMENT_ID,
            interval="1m",
            research_run_at=NOW,
        )
        self.assertEqual(len(series.bars), 30)
        self.assertEqual([bar.bar_open_at for bar in series.bars], bar_opens)
        self.assertEqual({bar.dataset_content_hash for bar in series.bars}, {result.dataset_content_hash})

        # ---- both feature families share the one sealed dataset -----------------
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT d.name, COUNT(*), COUNT(DISTINCT m.dataset_version) "
                "FROM feature_materializations m "
                "JOIN feature_definition_versions d ON d.feature_id=m.feature_id "
                "WHERE d.semantic_version=%s GROUP BY d.name ORDER BY d.name",
                (FIXTURE_FEATURE_VERSION,),
            )
            feature_rows = {
                str(row[0]): (int(row[1]), int(row[2])) for row in cursor.fetchall()
            }
        self.assertEqual(feature_rows["crypto_mark_index_basis"], (30, 1))
        self.assertEqual(feature_rows[FIXTURE_OI_FEATURE_NAME], (5, 1))
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT m.dataset_version FROM feature_materializations m "
                "JOIN feature_definition_versions d ON d.feature_id=m.feature_id "
                "WHERE d.semantic_version=%s",
                (FIXTURE_FEATURE_VERSION,),
            )
            dataset_versions = {str(row[0]) for row in cursor.fetchall()}
        self.assertEqual(dataset_versions, {str(dataset_version_id)})

        # ---- funding was never acquired for this source ------------------------
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_raw_observations "
                "WHERE source_id=%s AND observation_kind LIKE 'FUNDING%%'",
                (source_id,),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)


if __name__ == "__main__":
    unittest.main()
