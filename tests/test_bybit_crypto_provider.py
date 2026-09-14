from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from trade_platform.bybit_crypto_provider import (
    BYBIT_PROVIDER_VERSION,
    BYBIT_V5_SYMBOL_NAMESPACE,
    INDEX_PRICE_METHODOLOGY_REFERENCE,
    MARK_PRICE_METHODOLOGY_REFERENCE,
    BybitCryptoHistoricalAdapter,
    BybitUnsupportedObservationKindError,
)
from trade_platform.data_providers import (
    HttpResponse,
    ProviderConfiguration,
    ProviderConfigurationError,
    ProviderError,
    ProviderHealthRegistry,
    ProviderOperationalStatus,
    RetryPolicy,
)
from trade_platform.historical_market_data import (
    AdjustmentStatus,
    ObservationKind,
    normalize_payload,
)
from trade_platform.market_observation_payloads import (
    CRYPTO_SUPPORTED_OPEN_INTEREST_UNITS,
    parse_open_interest_payload,
)
from trade_platform.provider_ingestion import (
    HistoricalIngestionRequest,
    ingest_raw_historical_pages,
)
from trade_platform.tradable_bar_evidence_v2 import BAR_TIMESTAMP_SEMANTICS_MARKER_V1

NOW = datetime(2026, 6, 1, 0, 30, tzinfo=UTC)
WINDOW_START = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)


def _milliseconds(value: datetime) -> int:
    return int((value - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * 1000


def _configuration(**overrides: object) -> ProviderConfiguration:
    defaults: dict[str, object] = {
        "provider": "bybit",
        "base_url": "https://api.bybit.com",
        "terms_accepted": True,
        "secret_reference": None,
    }
    defaults.update(overrides)
    return ProviderConfiguration(**defaults)  # type: ignore[arg-type]


def _envelope(result: dict[str, object], *, return_code: int = 0, message: str = "OK") -> str:
    return json.dumps(
        {
            "retCode": return_code,
            "retMsg": message,
            "result": result,
            "retExtInfo": {},
            "time": 1780000000000,
        }
    )


def _kline_result(rows: list[list[str]], *, category: str = "linear", symbol: str = "BTCUSDT") -> str:
    return _envelope({"category": category, "symbol": symbol, "list": rows})


def _open_interest_result(
    rows: list[dict[str, str]], *, next_page_cursor: str | None = None
) -> str:
    result: dict[str, object] = {"category": "linear", "symbol": "BTCUSDT", "list": rows}
    if next_page_cursor is not None:
        result["nextPageCursor"] = next_page_cursor
    return _envelope(result)


def _trade_row(bar_open: datetime, close: str, *, volume: str = "12.5") -> list[str]:
    return [
        str(_milliseconds(bar_open)),
        "27000.0",
        "27100.0",
        "26900.0",
        close,
        volume,
        "337500.0",
    ]


def _reference_row(bar_open: datetime, close: str) -> list[str]:
    return [str(_milliseconds(bar_open)), "27000.0", "27100.0", "26900.0", close]


class ScriptedTransport:
    def __init__(self, bodies: list[HttpResponse]) -> None:
        self._bodies = list(bodies)
        self.urls: list[str] = []

    def get(self, url: str, timeout_seconds: float) -> HttpResponse:
        self.urls.append(url)
        if not self._bodies:
            raise AssertionError(f"unscripted request: {url}")
        return self._bodies.pop(0)


class RoutingTransport:
    def __init__(self, routes: dict[str, list[HttpResponse]]) -> None:
        self._routes = {path: list(bodies) for path, bodies in routes.items()}
        self.urls: list[str] = []

    def get(self, url: str, timeout_seconds: float) -> HttpResponse:
        self.urls.append(url)
        for path, bodies in self._routes.items():
            if path in url:
                if not bodies:
                    raise AssertionError(f"exhausted route: {path}")
                return bodies.pop(0)
        raise AssertionError(f"unrouted request: {url}")


class RecordingSink:
    def __init__(self) -> None:
        self.batches: list[int] = []
        self.captured: list[object] = []

    def capture_raw(self, observations: list[object]) -> tuple[UUID, ...]:
        self.batches.append(len(observations))
        self.captured.extend(observations)
        return tuple(uuid4() for _ in observations)


def _scope(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "observation_kind": ObservationKind.OHLCV.value,
        "category": "linear",
        "symbol": "BTCUSDT",
        "interval": "1",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "settlement_asset": "USDT",
        "start": WINDOW_START.isoformat(),
        "end": WINDOW_END.isoformat(),
    }
    base.update(overrides)
    return base


def _open_interest_scope(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "observation_kind": ObservationKind.OPEN_INTEREST.value,
        "interval": "5min",
        "end": datetime(2026, 6, 1, 0, 15, tzinfo=UTC).isoformat(),
    }
    base.update(overrides)
    return _scope(**base)


def _adapter(
    transport: object, *, now: datetime = NOW, **overrides: object
) -> BybitCryptoHistoricalAdapter:
    return BybitCryptoHistoricalAdapter(
        _configuration(**overrides),  # type: ignore[arg-type]
        transport=transport,  # type: ignore[arg-type]
        now=lambda: now,
        sleep=lambda _seconds: None,
    )


class BybitScopeGateTests(unittest.TestCase):
    def test_terms_not_accepted_makes_zero_network_calls(self) -> None:
        transport = ScriptedTransport([])
        adapter = _adapter(transport, terms_accepted=False)
        with self.assertRaisesRegex(ProviderConfigurationError, "provider_terms_not_accepted"):
            adapter.fetch_raw_page(uuid4(), _scope(), None)
        self.assertEqual(transport.urls, [])

    def test_terms_not_accepted_blocks_every_supported_kind(self) -> None:
        transport = ScriptedTransport([])
        adapter = _adapter(transport, terms_accepted=False)
        for kind in (
            ObservationKind.OHLCV,
            ObservationKind.MARK_PRICE,
            ObservationKind.INDEX_PRICE,
            ObservationKind.OPEN_INTEREST,
        ):
            scope = (
                _open_interest_scope()
                if kind is ObservationKind.OPEN_INTEREST
                else _scope(observation_kind=kind.value)
            )
            with self.subTest(kind=kind), self.assertRaises(ProviderConfigurationError):
                adapter.fetch_raw_page(uuid4(), scope, None)
        self.assertEqual(transport.urls, [])

    def test_wrong_provider_configuration_is_rejected_at_construction(self) -> None:
        with self.assertRaisesRegex(ProviderConfigurationError, "invalid_bybit_configuration"):
            BybitCryptoHistoricalAdapter(_configuration(provider="binance"))

    def test_plain_http_base_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ProviderConfigurationError, "provider_requires_https_base_url"
        ):
            BybitCryptoHistoricalAdapter(_configuration(base_url="http://api.bybit.com"))

    def test_unsupported_category_makes_zero_network_calls(self) -> None:
        transport = ScriptedTransport([])
        adapter = _adapter(transport)
        with self.assertRaisesRegex(
            ProviderConfigurationError, "unsupported_bybit_category:inverse"
        ):
            adapter.fetch_raw_page(uuid4(), _scope(category="inverse"), None)
        self.assertEqual(transport.urls, [])

    def test_unsupported_interval_per_kind(self) -> None:
        transport = ScriptedTransport([])
        adapter = _adapter(transport)
        for scope, expected in (
            (_scope(interval="5"), "unsupported_bybit_interval:OHLCV:5"),
            (
                _scope(observation_kind=ObservationKind.MARK_PRICE.value, interval="5min"),
                "unsupported_bybit_interval:MARK_PRICE:5min",
            ),
            (
                _open_interest_scope(interval="1"),
                "unsupported_bybit_interval:OPEN_INTEREST:1",
            ),
        ):
            with self.subTest(expected=expected), self.assertRaisesRegex(
                ProviderConfigurationError, expected
            ):
                adapter.fetch_raw_page(uuid4(), scope, None)
        self.assertEqual(transport.urls, [])

    def test_funding_kinds_fail_closed_with_an_unsupported_kind_error(self) -> None:
        transport = ScriptedTransport([])
        adapter = _adapter(transport)
        for kind in (
            ObservationKind.FUNDING_RATE_REALIZED,
            ObservationKind.FUNDING_RATE_INDICATIVE,
        ):
            with self.subTest(kind=kind), self.assertRaisesRegex(
                BybitUnsupportedObservationKindError,
                f"bybit_unsupported_observation_kind:{kind.value}",
            ):
                adapter.fetch_raw_page(uuid4(), _scope(observation_kind=kind.value), None)
        self.assertEqual(transport.urls, [])

    def test_settlement_price_and_equity_kinds_are_unsupported(self) -> None:
        adapter = _adapter(ScriptedTransport([]))
        for kind in (ObservationKind.SETTLEMENT_PRICE, ObservationKind.DIVIDEND):
            with self.subTest(kind=kind), self.assertRaises(BybitUnsupportedObservationKindError):
                adapter.fetch_raw_page(uuid4(), _scope(observation_kind=kind.value), None)

    def test_ambiguous_scope_fields_are_rejected_and_harmless_metadata_is_ignored(self) -> None:
        transport = ScriptedTransport(
            [HttpResponse(200, _kline_result([_trade_row(WINDOW_START, "27050.0")]))]
        )
        adapter = _adapter(transport)
        with self.assertRaisesRegex(
            ProviderConfigurationError, "ambiguous_bybit_scope_field:intervalTime,symbols"
        ):
            adapter.fetch_raw_page(
                uuid4(), _scope(symbols=["BTCUSDT"], intervalTime="5min"), None
            )
        self.assertEqual(transport.urls, [])

        page = adapter.fetch_raw_page(
            uuid4(),
            _scope(pilot_run="3J-phase-1", requested_by="operator", backfill_batch=3),
            None,
        )
        self.assertEqual(len(page.records), 1)

    def test_non_utc_and_inverted_windows_are_rejected(self) -> None:
        adapter = _adapter(ScriptedTransport([]))
        with self.assertRaisesRegex(ProviderConfigurationError, "bybit_scope_start_must_be_utc"):
            adapter.fetch_raw_page(uuid4(), _scope(start="2026-06-01T00:00:00+02:00"), None)
        with self.assertRaisesRegex(ProviderConfigurationError, "bybit_scope_start_must_be_utc"):
            adapter.fetch_raw_page(uuid4(), _scope(start="2026-06-01T00:00:00"), None)
        with self.assertRaisesRegex(ProviderConfigurationError, "invalid_bybit_scope_window"):
            adapter.fetch_raw_page(
                uuid4(), _scope(start=WINDOW_END.isoformat(), end=WINDOW_START.isoformat()), None
            )

    def test_lowercase_symbol_and_mismatched_settlement_asset_are_rejected(self) -> None:
        adapter = _adapter(ScriptedTransport([]))
        with self.assertRaisesRegex(ProviderConfigurationError, "invalid_bybit_scope_symbol"):
            adapter.fetch_raw_page(uuid4(), _scope(symbol="btcusdt"), None)
        with self.assertRaisesRegex(
            ProviderConfigurationError, "linear_bybit_scope_must_settle_in_quote_asset"
        ):
            adapter.fetch_raw_page(uuid4(), _scope(settlement_asset="BTC"), None)


class BybitTransportFailureTests(unittest.TestCase):
    def test_malformed_json_fails_closed(self) -> None:
        adapter = _adapter(ScriptedTransport([HttpResponse(200, "{not json")]))
        with self.assertRaisesRegex(ProviderError, "bybit_invalid_json_response"):
            adapter.fetch_raw_page(uuid4(), _scope(), None)

    def test_non_zero_return_code_is_never_an_empty_page(self) -> None:
        body = _envelope({"list": []}, return_code=10001, message="params error")
        adapter = _adapter(ScriptedTransport([HttpResponse(200, body)]))
        with self.assertRaisesRegex(ProviderError, "bybit_provider_error:10001:params error"):
            adapter.fetch_raw_page(uuid4(), _scope(), None)

    def test_provider_response_error_is_not_retried(self) -> None:
        body = _envelope({"list": []}, return_code=10001, message="params error")
        transport = ScriptedTransport([HttpResponse(200, body)])
        adapter = _adapter(transport, )
        with self.assertRaises(ProviderError):
            adapter.fetch_raw_page(uuid4(), _scope(), None)
        self.assertEqual(len(transport.urls), 1)

    def test_malformed_result_shapes_fail_closed(self) -> None:
        cases = (
            (json.dumps({"retMsg": "OK", "result": {}, "time": 1}), "bybit_unexpected_envelope_shape"),
            (
                json.dumps({"retCode": 0, "retMsg": "OK", "result": {}}),
                "bybit_unexpected_envelope_shape",
            ),
            (_envelope({"list": "rows"}), "bybit_unexpected_result_shape"),
            (
                json.dumps(
                    {"retCode": 0, "retMsg": "OK", "result": ["nope"], "time": 1780000000000}
                ),
                "bybit_unexpected_result_shape",
            ),
            (
                _kline_result([["1780000000000", "1", "2"]]),
                "bybit_unexpected_kline_row_shape",
            ),
            (
                _kline_result([{"start": "1780000000000"}]),  # type: ignore[list-item]
                "bybit_unexpected_kline_row_shape",
            ),
            (_kline_result([], symbol="ETHUSDT"), "bybit_response_symbol_mismatch:ETHUSDT"),
            (_kline_result([], category="inverse"), "bybit_response_category_mismatch:inverse"),
        )
        for body, expected in cases:
            with self.subTest(expected=expected):
                adapter = _adapter(ScriptedTransport([HttpResponse(200, body)]))
                with self.assertRaisesRegex(ProviderError, expected):
                    adapter.fetch_raw_page(uuid4(), _scope(), None)

    def test_unparseable_provider_numbers_fail_closed(self) -> None:
        row = _trade_row(WINDOW_START, "not-a-number")
        adapter = _adapter(ScriptedTransport([HttpResponse(200, _kline_result([row]))]))
        with self.assertRaisesRegex(ProviderError, "bybit_unparseable_kline_value:not-a-number"):
            adapter.fetch_raw_page(uuid4(), _scope(), None)

        bad_time = _trade_row(WINDOW_START, "27050.0")
        bad_time[0] = "yesterday"
        adapter = _adapter(ScriptedTransport([HttpResponse(200, _kline_result([bad_time]))]))
        with self.assertRaisesRegex(
            ProviderError, "bybit_unparseable_kline_start_time:yesterday"
        ):
            adapter.fetch_raw_page(uuid4(), _scope(), None)

    def test_retryable_status_is_retried_with_retry_after_then_succeeds(self) -> None:
        transport = ScriptedTransport(
            [
                HttpResponse(429, "", {"Retry-After": "3"}),
                HttpResponse(503, ""),
                HttpResponse(200, _kline_result([_trade_row(WINDOW_START, "27050.0")])),
            ]
        )
        sleeps: list[float] = []
        adapter = BybitCryptoHistoricalAdapter(
            _configuration(),
            transport=transport,  # type: ignore[arg-type]
            retry_policy=RetryPolicy(maximum_attempts=3, base_delay=timedelta(seconds=2)),
            now=lambda: NOW,
            sleep=sleeps.append,
        )
        page = adapter.fetch_raw_page(uuid4(), _scope(), None)
        self.assertEqual(len(page.records), 1)
        self.assertEqual(sleeps, [3.0, 4.0])
        self.assertEqual(len(transport.urls), 3)

    def test_nonretryable_status_fails_after_a_single_call(self) -> None:
        transport = ScriptedTransport([HttpResponse(404, "")])
        sleeps: list[float] = []
        adapter = BybitCryptoHistoricalAdapter(
            _configuration(),
            transport=transport,  # type: ignore[arg-type]
            retry_policy=RetryPolicy(maximum_attempts=3, base_delay=timedelta(seconds=1)),
            now=lambda: NOW,
            sleep=sleeps.append,
        )
        with self.assertRaisesRegex(ProviderError, "bybit_http_status:404"):
            adapter.fetch_raw_page(uuid4(), _scope(), None)
        self.assertEqual(len(transport.urls), 1)
        self.assertEqual(sleeps, [])

    def test_exhausted_retries_report_the_last_status(self) -> None:
        transport = ScriptedTransport([HttpResponse(503, ""), HttpResponse(503, "")])
        adapter = BybitCryptoHistoricalAdapter(
            _configuration(),
            transport=transport,  # type: ignore[arg-type]
            retry_policy=RetryPolicy(maximum_attempts=2, base_delay=timedelta(seconds=0)),
            now=lambda: NOW,
            sleep=lambda _seconds: None,
        )
        with self.assertRaisesRegex(ProviderError, "bybit_http_status:503"):
            adapter.fetch_raw_page(uuid4(), _scope(), None)
        self.assertEqual(len(transport.urls), 2)


class BybitOhlcvMappingTests(unittest.TestCase):
    def test_trade_kline_maps_to_ohlcv_with_bar_semantics(self) -> None:
        source_id = uuid4()
        transport = ScriptedTransport(
            [HttpResponse(200, _kline_result([_trade_row(WINDOW_START, "27050.0")]))]
        )
        adapter = _adapter(transport)
        page = adapter.fetch_raw_page(source_id, _scope(), None)

        self.assertEqual(page.provider_version, BYBIT_PROVIDER_VERSION)
        self.assertEqual(page.retrieved_at, NOW)
        self.assertIn("/v5/market/kline?", transport.urls[0])
        self.assertIn("category=linear", transport.urls[0])
        self.assertIn("symbol=BTCUSDT", transport.urls[0])
        self.assertIn("limit=1000", transport.urls[0])

        (record,) = page.records
        self.assertEqual(record.source_id, source_id)
        self.assertEqual(record.observation_kind, ObservationKind.OHLCV)
        self.assertEqual(record.provider_identifier, "BTCUSDT")
        self.assertEqual(record.provider_symbol, "BTCUSDT")
        self.assertEqual(record.exchange, "BYBIT")
        self.assertEqual(record.event_at, WINDOW_START)
        self.assertEqual(record.effective_at, WINDOW_START + timedelta(minutes=1))
        self.assertEqual(record.ingested_at, NOW)
        self.assertEqual(record.adjustment_status, AdjustmentStatus.RAW)
        self.assertEqual(record.revision, 0)
        self.assertEqual(record.provenance_uri, "bybit://v5/trade-kline/linear/BTCUSDT")
        self.assertEqual(
            record.raw_payload["bar_timestamp_semantics"], BAR_TIMESTAMP_SEMANTICS_MARKER_V1
        )
        self.assertEqual(record.raw_payload["interval"], "1m")
        self.assertEqual(record.raw_payload["close"], "27050.0")
        self.assertEqual(record.raw_payload["volume"], "12.5")
        self.assertEqual(record.raw_payload["provider_turnover"], "337500.0")
        self.assertNotIn("price", record.raw_payload)
        self.assertNotIn("methodology_reference", record.raw_payload)
        record.validate()

        normalized, issues = normalize_payload(ObservationKind.OHLCV, record.raw_payload)
        self.assertEqual(issues, ())
        self.assertEqual(normalized["interval"], "1m")
        self.assertEqual(Decimal(str(normalized["close"])), Decimal("27050.0"))

    def test_incomplete_candle_is_suppressed(self) -> None:
        partial_open = datetime(2026, 6, 1, 0, 2, tzinfo=UTC)
        transport = ScriptedTransport(
            [
                HttpResponse(
                    200,
                    _kline_result(
                        [
                            _trade_row(partial_open, "27080.0"),
                            _trade_row(WINDOW_START + timedelta(minutes=1), "27060.0"),
                            _trade_row(WINDOW_START, "27050.0"),
                        ]
                    ),
                )
            ]
        )
        adapter = _adapter(transport, now=datetime(2026, 6, 1, 0, 2, 30, tzinfo=UTC))
        page = adapter.fetch_raw_page(uuid4(), _scope(), None)
        self.assertEqual(
            [record.event_at for record in page.records],
            [WINDOW_START, WINDOW_START + timedelta(minutes=1)],
        )

    def test_bar_closing_after_requested_end_is_suppressed(self) -> None:
        beyond = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
        transport = ScriptedTransport(
            [
                HttpResponse(
                    200,
                    _kline_result([_trade_row(beyond, "27090.0"), _trade_row(WINDOW_START, "1")]),
                )
            ]
        )
        adapter = _adapter(transport)
        page = adapter.fetch_raw_page(uuid4(), _scope(), None)
        self.assertEqual([record.event_at for record in page.records], [WINDOW_START])

    def test_reverse_chronological_provider_rows_become_chronological_records(self) -> None:
        opens = [WINDOW_START + timedelta(minutes=offset) for offset in range(4)]
        rows = [_trade_row(bar_open, "27050.0") for bar_open in reversed(opens)]
        transport = ScriptedTransport([HttpResponse(200, _kline_result(rows))])
        adapter = _adapter(transport)
        page = adapter.fetch_raw_page(uuid4(), _scope(), None)
        self.assertEqual([record.event_at for record in page.records], opens)


class BybitReferencePriceMappingTests(unittest.TestCase):
    def _reference_page(self, kind: ObservationKind, close: str):
        transport = ScriptedTransport(
            [HttpResponse(200, _kline_result([_reference_row(WINDOW_START, close)]))]
        )
        adapter = _adapter(transport)
        page = adapter.fetch_raw_page(uuid4(), _scope(observation_kind=kind.value), None)
        return transport, page

    def test_mark_price_uses_the_mark_kline_close_only(self) -> None:
        transport, page = self._reference_page(ObservationKind.MARK_PRICE, "27011.5")
        self.assertIn("/v5/market/mark-price-kline?", transport.urls[0])
        (record,) = page.records
        bar_close_at = WINDOW_START + timedelta(minutes=1)
        self.assertEqual(record.observation_kind, ObservationKind.MARK_PRICE)
        self.assertEqual(record.event_at, bar_close_at)
        self.assertEqual(record.effective_at, bar_close_at)
        self.assertEqual(record.ingested_at, NOW)
        self.assertEqual(record.provenance_uri, "bybit://v5/mark-kline/linear/BTCUSDT")
        self.assertEqual(record.raw_payload["price"], "27011.5")
        self.assertEqual(record.raw_payload["price_asset"], "USDT")
        self.assertEqual(record.raw_payload["observed_at"], bar_close_at.isoformat())
        self.assertEqual(
            record.raw_payload["methodology_reference"], MARK_PRICE_METHODOLOGY_REFERENCE
        )
        self.assertEqual(record.raw_payload["provider_open"], "27000.0")
        self.assertNotIn("volume", record.raw_payload)
        self.assertNotIn("bar_timestamp_semantics", record.raw_payload)
        record.validate()

    def test_index_price_uses_the_index_kline_close_only(self) -> None:
        transport, page = self._reference_page(ObservationKind.INDEX_PRICE, "27009.25")
        self.assertIn("/v5/market/index-price-kline?", transport.urls[0])
        (record,) = page.records
        self.assertEqual(record.observation_kind, ObservationKind.INDEX_PRICE)
        self.assertEqual(record.provenance_uri, "bybit://v5/index-kline/linear/BTCUSDT")
        self.assertEqual(record.raw_payload["price"], "27009.25")
        self.assertEqual(
            record.raw_payload["methodology_reference"], INDEX_PRICE_METHODOLOGY_REFERENCE
        )

    def test_mark_and_index_never_substitute_for_each_other_or_for_a_trade_close(self) -> None:
        self.assertNotEqual(MARK_PRICE_METHODOLOGY_REFERENCE, INDEX_PRICE_METHODOLOGY_REFERENCE)
        mark_transport, mark_page = self._reference_page(ObservationKind.MARK_PRICE, "27011.5")
        index_transport, index_page = self._reference_page(ObservationKind.INDEX_PRICE, "27009.25")

        self.assertNotIn("index-price-kline", mark_transport.urls[0])
        self.assertNotIn("mark-price-kline", index_transport.urls[0])
        self.assertNotEqual(
            mark_page.records[0].provenance_uri, index_page.records[0].provenance_uri
        )
        self.assertNotEqual(
            mark_page.records[0].raw_payload["methodology_reference"],
            index_page.records[0].raw_payload["methodology_reference"],
        )

        trade_transport = ScriptedTransport(
            [HttpResponse(200, _kline_result([_trade_row(WINDOW_START, "27050.0")]))]
        )
        trade_page = _adapter(trade_transport).fetch_raw_page(uuid4(), _scope(), None)
        self.assertIn("/v5/market/kline?", trade_transport.urls[0])
        self.assertEqual(trade_page.records[0].observation_kind, ObservationKind.OHLCV)
        self.assertNotIn("price", trade_page.records[0].raw_payload)

    def test_incomplete_reference_candle_is_suppressed(self) -> None:
        transport = ScriptedTransport(
            [
                HttpResponse(
                    200,
                    _kline_result(
                        [
                            _reference_row(datetime(2026, 6, 1, 0, 2, tzinfo=UTC), "27080.0"),
                            _reference_row(WINDOW_START, "27011.5"),
                        ]
                    ),
                )
            ]
        )
        adapter = _adapter(transport, now=datetime(2026, 6, 1, 0, 2, 30, tzinfo=UTC))
        page = adapter.fetch_raw_page(
            uuid4(), _scope(observation_kind=ObservationKind.MARK_PRICE.value), None
        )
        self.assertEqual(
            [record.event_at for record in page.records], [WINDOW_START + timedelta(minutes=1)]
        )


class BybitOpenInterestMappingTests(unittest.TestCase):
    def test_open_interest_maps_to_base_asset_unit_and_base_asset(self) -> None:
        observed_at = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
        transport = ScriptedTransport(
            [
                HttpResponse(
                    200,
                    _open_interest_result(
                        [
                            {
                                "openInterest": "461134.0000000",
                                "timestamp": str(_milliseconds(observed_at)),
                                "singleOpenInterest": "230567.0",
                            }
                        ]
                    ),
                )
            ]
        )
        adapter = _adapter(transport)
        page = adapter.fetch_raw_page(uuid4(), _open_interest_scope(), None)

        self.assertIn("/v5/market/open-interest?", transport.urls[0])
        self.assertIn("intervalTime=5min", transport.urls[0])
        self.assertIn("limit=200", transport.urls[0])
        (record,) = page.records
        self.assertEqual(record.observation_kind, ObservationKind.OPEN_INTEREST)
        self.assertEqual(record.event_at, observed_at)
        self.assertEqual(record.effective_at, observed_at)
        self.assertEqual(record.ingested_at, NOW)
        self.assertEqual(record.provenance_uri, "bybit://v5/open-interest/linear/BTCUSDT")
        self.assertEqual(record.raw_payload["open_interest"], "461134.0000000")
        self.assertEqual(record.raw_payload["unit"], "BASE_ASSET")
        self.assertEqual(record.raw_payload["unit_asset"], "BTC")
        self.assertEqual(record.raw_payload["observed_at"], observed_at.isoformat())
        self.assertEqual(record.raw_payload["provider_single_open_interest"], "230567.0")
        record.validate()

        payload, issues = parse_open_interest_payload(
            record.raw_payload, supported_units=CRYPTO_SUPPORTED_OPEN_INTEREST_UNITS
        )
        self.assertEqual(issues, ())
        assert payload is not None
        self.assertEqual(payload.open_interest, Decimal("461134.0000000"))
        self.assertEqual(payload.unit.value, "BASE_ASSET")
        self.assertEqual(payload.unit_asset, "BTC")
        self.assertEqual(payload.observed_at, observed_at)

    def test_open_interest_is_never_converted_to_contracts_or_notional(self) -> None:
        observed_at = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
        transport = ScriptedTransport(
            [
                HttpResponse(
                    200,
                    _open_interest_result(
                        [
                            {
                                "openInterest": "461134.0",
                                "timestamp": str(_milliseconds(observed_at)),
                                "singleOpenInterest": "230567.0",
                            }
                        ]
                    ),
                )
            ]
        )
        (record,) = _adapter(transport).fetch_raw_page(uuid4(), _open_interest_scope(), None).records
        self.assertNotIn(record.raw_payload["unit"], {"CONTRACTS", "QUOTE_NOTIONAL"})
        self.assertEqual(record.raw_payload["open_interest"], "461134.0")
        self.assertNotEqual(record.raw_payload["open_interest"], "230567.0")

    def test_malformed_open_interest_rows_fail_closed(self) -> None:
        for row in (
            {"timestamp": "1780000000000"},
            {"openInterest": "1.0"},
            {"openInterest": "", "timestamp": "1780000000000"},
        ):
            with self.subTest(row=row):
                adapter = _adapter(
                    ScriptedTransport([HttpResponse(200, _open_interest_result([row]))])
                )
                with self.assertRaisesRegex(
                    ProviderError, "bybit_unexpected_open_interest_row_shape"
                ):
                    adapter.fetch_raw_page(uuid4(), _open_interest_scope(), None)

    def test_open_interest_outside_the_requested_window_is_dropped(self) -> None:
        inside = datetime(2026, 6, 1, 0, 10, tzinfo=UTC)
        outside = datetime(2026, 6, 1, 0, 15, tzinfo=UTC)
        transport = ScriptedTransport(
            [
                HttpResponse(
                    200,
                    _open_interest_result(
                        [
                            {"openInterest": "3.0", "timestamp": str(_milliseconds(outside))},
                            {"openInterest": "2.0", "timestamp": str(_milliseconds(inside))},
                        ]
                    ),
                )
            ]
        )
        page = _adapter(transport).fetch_raw_page(uuid4(), _open_interest_scope(), None)
        self.assertEqual([record.event_at for record in page.records], [inside])


class BybitPaginationTests(unittest.TestCase):
    def test_kline_pagination_walks_backwards_without_duplicates(self) -> None:
        source_id = uuid4()
        scope_start = WINDOW_START - timedelta(minutes=2)
        older = [WINDOW_START, WINDOW_START + timedelta(minutes=1)]
        newer = [WINDOW_START + timedelta(minutes=2), WINDOW_START + timedelta(minutes=3)]
        transport = ScriptedTransport(
            [
                HttpResponse(
                    200,
                    _kline_result([_trade_row(bar, "27050.0") for bar in reversed(newer)]),
                ),
                HttpResponse(
                    200,
                    _kline_result([_trade_row(bar, "27050.0") for bar in reversed(older)]),
                ),
                HttpResponse(200, _kline_result([])),
            ]
        )
        adapter = _adapter(transport)
        health = ProviderHealthRegistry(lambda: NOW)
        sink = RecordingSink()
        outcome = ingest_raw_historical_pages(
            adapter,
            HistoricalIngestionRequest(source_id, _scope(start=scope_start.isoformat())),
            sink,
            health,
            now=lambda: NOW,
        )
        self.assertEqual(outcome.state, ProviderOperationalStatus.HEALTHY)
        self.assertEqual(sink.batches, [2, 2])
        self.assertEqual(len(transport.urls), 3)
        self.assertIn(f"end={_milliseconds(WINDOW_END) - 1}", transport.urls[0])
        self.assertIn(f"end={_milliseconds(newer[0]) - 1}", transport.urls[1])
        self.assertIn(f"end={_milliseconds(older[0]) - 1}", transport.urls[2])
        for url in transport.urls:
            self.assertIn(f"start={_milliseconds(scope_start)}", url)
        self.assertEqual(len(set(transport.urls)), 3)
        captured = [record.event_at for record in sink.captured]  # type: ignore[attr-defined]
        self.assertEqual(captured, [*newer, *older])
        self.assertEqual(len(set(captured)), 4)

    def test_kline_pagination_stops_when_the_next_window_precedes_the_scope_start(self) -> None:
        transport = ScriptedTransport(
            [HttpResponse(200, _kline_result([_trade_row(WINDOW_START, "27050.0")]))]
        )
        page = _adapter(transport).fetch_raw_page(uuid4(), _scope(), None)
        self.assertIsNone(page.next_cursor)

    def test_kline_resume_cursor_must_be_a_time_cursor_inside_the_scope(self) -> None:
        adapter = _adapter(ScriptedTransport([]))
        for cursor in ("c:lastid%3D1", "2026-06-01", "t:abc", f"t:{_milliseconds(WINDOW_END)}"):
            with self.subTest(cursor=cursor), self.assertRaises(ProviderConfigurationError):
                adapter.fetch_raw_page(uuid4(), _scope(), cursor)

    def test_a_provider_page_that_does_not_advance_the_window_fails_closed(self) -> None:
        transport = ScriptedTransport(
            [
                HttpResponse(
                    200,
                    _kline_result([_trade_row(WINDOW_START + timedelta(minutes=3), "27050.0")]),
                )
            ]
        )
        adapter = _adapter(transport)
        cursor = f"t:{_milliseconds(WINDOW_START + timedelta(minutes=1))}"
        with self.assertRaisesRegex(ProviderError, "bybit_kline_cursor_did_not_advance"):
            adapter.fetch_raw_page(uuid4(), _scope(), cursor)

    def test_open_interest_uses_the_provider_page_cursor(self) -> None:
        source_id = uuid4()
        first = datetime(2026, 6, 1, 0, 10, tzinfo=UTC)
        second = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
        third = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        transport = ScriptedTransport(
            [
                HttpResponse(
                    200,
                    _open_interest_result(
                        [
                            {"openInterest": "3.0", "timestamp": str(_milliseconds(first))},
                            {"openInterest": "2.0", "timestamp": str(_milliseconds(second))},
                        ],
                        next_page_cursor="lastid%3D42%26",
                    ),
                ),
                HttpResponse(
                    200,
                    _open_interest_result(
                        [{"openInterest": "1.0", "timestamp": str(_milliseconds(third))}],
                        next_page_cursor="",
                    ),
                ),
            ]
        )
        adapter = _adapter(transport)
        first_page = adapter.fetch_raw_page(source_id, _open_interest_scope(), None)
        self.assertEqual(first_page.next_cursor, "c:lastid%3D42%26")
        self.assertEqual([record.event_at for record in first_page.records], [second, first])

        second_page = adapter.fetch_raw_page(
            source_id, _open_interest_scope(), first_page.next_cursor
        )
        self.assertIsNone(second_page.next_cursor)
        self.assertEqual([record.event_at for record in second_page.records], [third])
        self.assertIn("cursor=lastid%253D42%2526", transport.urls[1])

    def test_open_interest_rejects_a_time_cursor(self) -> None:
        adapter = _adapter(ScriptedTransport([]))
        with self.assertRaisesRegex(ProviderConfigurationError, "bybit_cursor_kind_mismatch"):
            adapter.fetch_raw_page(uuid4(), _open_interest_scope(), "t:1780000000000")

    def test_repeated_provider_cursor_is_caught_by_ingestion_loop_protection(self) -> None:
        observed_at = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
        body = HttpResponse(
            200,
            _open_interest_result(
                [{"openInterest": "2.0", "timestamp": str(_milliseconds(observed_at))}],
                next_page_cursor="stuck",
            ),
        )
        transport = ScriptedTransport([body, body, body])
        adapter = _adapter(transport)
        outcome = ingest_raw_historical_pages(
            adapter,
            HistoricalIngestionRequest(uuid4(), _open_interest_scope()),
            RecordingSink(),
            ProviderHealthRegistry(lambda: NOW),
            now=lambda: NOW,
        )
        self.assertEqual(outcome.state, ProviderOperationalStatus.ERROR)
        self.assertEqual(outcome.checkpoint.error_code, "provider_pagination_cursor_loop")
        self.assertEqual(len(transport.urls), 2)

    def test_page_bound_exhaustion_is_an_error_outcome(self) -> None:
        observed_at = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
        transport = ScriptedTransport(
            [
                HttpResponse(
                    200,
                    _open_interest_result(
                        [{"openInterest": "2.0", "timestamp": str(_milliseconds(observed_at))}],
                        next_page_cursor=f"page-{index}",
                    ),
                )
                for index in range(3)
            ]
        )
        outcome = ingest_raw_historical_pages(
            _adapter(transport),
            HistoricalIngestionRequest(uuid4(), _open_interest_scope(), maximum_pages=2),
            RecordingSink(),
            ProviderHealthRegistry(lambda: NOW),
            now=lambda: NOW,
        )
        self.assertEqual(outcome.state, ProviderOperationalStatus.ERROR)
        self.assertEqual(outcome.checkpoint.error_code, "provider_pagination_limit_exceeded")

    def test_provider_errors_become_error_outcomes_through_ingestion(self) -> None:
        transport = ScriptedTransport([HttpResponse(200, "{not json")])
        outcome = ingest_raw_historical_pages(
            _adapter(transport),
            HistoricalIngestionRequest(uuid4(), _scope()),
            RecordingSink(),
            ProviderHealthRegistry(lambda: NOW),
            now=lambda: NOW,
        )
        self.assertEqual(outcome.state, ProviderOperationalStatus.ERROR)
        self.assertEqual(outcome.checkpoint.error_code, "bybit_invalid_json_response")
        self.assertEqual(outcome.checkpoint.adapter_name, "bybit")


class BybitAllFourKindsTests(unittest.TestCase):
    def test_one_source_contributes_all_four_supported_kinds(self) -> None:
        source_id = uuid4()
        observed_at = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
        transport = RoutingTransport(
            {
                "/v5/market/mark-price-kline": [
                    HttpResponse(200, _kline_result([_reference_row(WINDOW_START, "27011.5")]))
                ],
                "/v5/market/index-price-kline": [
                    HttpResponse(200, _kline_result([_reference_row(WINDOW_START, "27009.25")]))
                ],
                "/v5/market/open-interest": [
                    HttpResponse(
                        200,
                        _open_interest_result(
                            [
                                {
                                    "openInterest": "461134.0",
                                    "timestamp": str(_milliseconds(observed_at)),
                                }
                            ]
                        ),
                    )
                ],
                "/v5/market/kline": [
                    HttpResponse(200, _kline_result([_trade_row(WINDOW_START, "27050.0")]))
                ],
            }
        )
        adapter = _adapter(transport)
        kinds = []
        for scope in (
            _scope(),
            _scope(observation_kind=ObservationKind.MARK_PRICE.value),
            _scope(observation_kind=ObservationKind.INDEX_PRICE.value),
            _open_interest_scope(),
        ):
            page = adapter.fetch_raw_page(source_id, scope, None)
            for record in page.records:
                record.validate()
                self.assertEqual(record.source_id, source_id)
                self.assertEqual(record.provider_identifier, "BTCUSDT")
                kinds.append(record.observation_kind)
        self.assertEqual(
            sorted(kind.value for kind in kinds),
            ["INDEX_PRICE", "MARK_PRICE", "OHLCV", "OPEN_INTEREST"],
        )

    def test_symbol_namespace_constant_is_the_documented_provider_namespace(self) -> None:
        self.assertEqual(BYBIT_V5_SYMBOL_NAMESPACE, "bybit_v5_symbol")


if __name__ == "__main__":
    unittest.main()
