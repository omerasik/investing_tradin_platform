import json
import os
import unittest
from datetime import date
from decimal import Decimal

from trade_platform.data_providers import (
    HttpResponse,
    ProviderConfiguration,
    ProviderConfigurationError,
    ProviderError,
)
from trade_platform.databento_cost_preflight import (
    PHASE1_DAILY_LEG,
    PHASE1_MINUTE_LEG,
    PHASE1_PILOT_LEGS,
    DatabentoCostPreflightClient,
    PilotLegRequest,
    SymbolAvailability,
    SymbologyResolution,
    run_pilot_cost_preflight,
)

SECRET_ENV_VAR = "DATABENTO_TEST_API_KEY"  # pragma: allowlist secret
API_KEY = "s3cr3t"  # pragma: allowlist secret


def _config(**overrides: object) -> ProviderConfiguration:
    defaults: dict[str, object] = {
        "provider": "databento",
        "base_url": "https://hist.databento.com",
        "terms_accepted": True,
        "secret_reference": f"env:{SECRET_ENV_VAR}",
    }
    defaults.update(overrides)
    return ProviderConfiguration(**defaults)  # type: ignore[arg-type]


class ScriptedMetadataTransport:
    """Fake DatabentoHttpTransport for the metadata/symbology-only client.

    Deliberately has no notion of batch.submit_job or timeseries.get_range --
    those paths simply are not modeled here, mirroring that the real client
    never constructs a URL for them.
    """

    def __init__(self, *, gets: dict[str, HttpResponse], posts: dict[str, HttpResponse], expected_api_key: str = API_KEY) -> None:
        self._gets = gets
        self._posts = posts
        self._expected_api_key = expected_api_key
        self.get_urls: list[str] = []
        self.post_calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, api_key: str, timeout_seconds: float) -> HttpResponse:
        assert api_key == self._expected_api_key
        self.get_urls.append(url)
        for method, response in self._gets.items():
            if method in url:
                return response
        raise AssertionError(f"unexpected GET: {url}")

    def post(self, url: str, form: dict[str, str], api_key: str, timeout_seconds: float) -> HttpResponse:
        assert api_key == self._expected_api_key
        self.post_calls.append((url, form))
        for method, response in self._posts.items():
            if method in url:
                return response
        raise AssertionError(f"unexpected POST: {url}")


class DatabentoCostPreflightClientTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ[SECRET_ENV_VAR] = API_KEY
        self.addCleanup(os.environ.pop, SECRET_ENV_VAR, None)

    def test_no_batch_or_timeseries_method_exists(self) -> None:
        forbidden = {"submit_job", "get_range", "get_range_async", "list_jobs", "list_files", "download"}
        exposed = {name for name in dir(DatabentoCostPreflightClient) if not name.startswith("_")}
        self.assertFalse(forbidden & exposed, f"preflight client must never expose: {forbidden & exposed}")

    def test_terms_not_accepted_is_rejected_before_any_request(self) -> None:
        client = DatabentoCostPreflightClient(_config(terms_accepted=False), transport=ScriptedMetadataTransport(gets={}, posts={}))
        with self.assertRaisesRegex(ProviderConfigurationError, "provider_terms_not_accepted"):
            client.list_datasets()

    def test_missing_secret_reference_is_rejected_before_any_request(self) -> None:
        client = DatabentoCostPreflightClient(_config(secret_reference=None), transport=ScriptedMetadataTransport(gets={}, posts={}))
        with self.assertRaisesRegex(ProviderConfigurationError, "databento_secret_reference_required"):
            client.list_datasets()

    def test_list_datasets_parses_bare_json_array(self) -> None:
        transport = ScriptedMetadataTransport(
            gets={"metadata.list_datasets": HttpResponse(200, json.dumps(["EQUS.SUMMARY", "EQUS.MINI"]))}, posts={},
        )
        client = DatabentoCostPreflightClient(_config(), transport=transport)
        self.assertEqual(client.list_datasets(), ("EQUS.SUMMARY", "EQUS.MINI"))
        self.assertIn("metadata.list_datasets", transport.get_urls[0])

    def test_get_dataset_range_parses_nested_schema_object(self) -> None:
        body = json.dumps({
            "start": "2010-01-01T00:00:00.000000000Z", "end": "2026-09-01T00:00:00.000000000Z",
            "schema": {"ohlcv-1d": {"start": "2010-01-01T00:00:00.000000000Z", "end": "2026-09-01T00:00:00.000000000Z"}},
        })
        transport = ScriptedMetadataTransport(gets={"metadata.get_dataset_range": HttpResponse(200, body)}, posts={})
        client = DatabentoCostPreflightClient(_config(), transport=transport)
        result = client.get_dataset_range("EQUS.SUMMARY")
        self.assertEqual(result.start, "2010-01-01T00:00:00.000000000Z")
        self.assertEqual(result.schema_ranges["ohlcv-1d"], ("2010-01-01T00:00:00.000000000Z", "2026-09-01T00:00:00.000000000Z"))

    def test_get_cost_parses_bare_json_number_as_decimal(self) -> None:
        transport = ScriptedMetadataTransport(gets={"metadata.get_cost": HttpResponse(200, "2.587353944778")}, posts={})
        client = DatabentoCostPreflightClient(_config(), transport=transport)
        cost = client.get_cost("EQUS.SUMMARY", ("AAPL",), "ohlcv-1d", date(2020, 1, 1), date(2026, 9, 1))
        self.assertEqual(cost, Decimal("2.587353944778"))
        self.assertIn("symbols=AAPL", transport.get_urls[0])
        self.assertIn("stype_in=raw_symbol", transport.get_urls[0])

    def test_get_billable_size_parses_bare_integer(self) -> None:
        transport = ScriptedMetadataTransport(gets={"metadata.get_billable_size": HttpResponse(200, "1990595")}, posts={})
        client = DatabentoCostPreflightClient(_config(), transport=transport)
        size = client.get_billable_size("EQUS.SUMMARY", ("AAPL",), "ohlcv-1d", date(2020, 1, 1), date(2026, 9, 1))
        self.assertEqual(size, 1990595)

    def test_unexpected_http_status_raises_provider_error(self) -> None:
        transport = ScriptedMetadataTransport(gets={"metadata.get_cost": HttpResponse(500, "internal error")}, posts={})
        client = DatabentoCostPreflightClient(_config(), transport=transport)
        with self.assertRaisesRegex(ProviderError, "databento_http_status:500"):
            client.get_cost("EQUS.SUMMARY", ("AAPL",), "ohlcv-1d", date(2020, 1, 1), date(2026, 9, 1))

    def test_resolve_symbols_uses_post_not_get(self) -> None:
        body = json.dumps({
            "result": {"AAPL": [{"d0": "2020-01-01", "d1": "2026-09-01", "s": "12345"}]},
            "symbols": ["AAPL"], "partial": [], "not_found": [],
        })
        transport = ScriptedMetadataTransport(gets={}, posts={"symbology.resolve": HttpResponse(200, body)})
        client = DatabentoCostPreflightClient(_config(), transport=transport)
        resolution = client.resolve_symbols("EQUS.SUMMARY", ("AAPL",), date(2020, 1, 1), date(2026, 9, 1))
        self.assertEqual(resolution.resolved["AAPL"], ("12345",))
        self.assertEqual(resolution.classify("AAPL"), SymbolAvailability.AVAILABLE)


class SymbologyResolutionClassificationTests(unittest.TestCase):
    def test_not_found_symbol_is_unavailable(self) -> None:
        resolution = SymbologyResolution(resolved={}, partial=(), not_found=("TWTR",))
        self.assertEqual(resolution.classify("TWTR"), SymbolAvailability.UNAVAILABLE)

    def test_partial_symbol_is_ambiguous(self) -> None:
        resolution = SymbologyResolution(resolved={}, partial=("META",), not_found=())
        self.assertEqual(resolution.classify("META"), SymbolAvailability.AMBIGUOUS)

    def test_multiple_distinct_instrument_ids_is_ambiguous(self) -> None:
        resolution = SymbologyResolution(resolved={"FB": ("111", "222")}, partial=(), not_found=())
        self.assertEqual(resolution.classify("FB"), SymbolAvailability.AMBIGUOUS)

    def test_single_stable_instrument_id_is_available(self) -> None:
        resolution = SymbologyResolution(resolved={"AAPL": ("999", "999")}, partial=(), not_found=())
        self.assertEqual(resolution.classify("AAPL"), SymbolAvailability.AVAILABLE)


class RunPilotCostPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ[SECRET_ENV_VAR] = API_KEY
        self.addCleanup(os.environ.pop, SECRET_ENV_VAR, None)

    def test_combines_cost_and_size_across_legs(self) -> None:
        range_body = json.dumps({"start": "2018-01-01T00:00:00.000000000Z", "end": "2026-09-01T00:00:00.000000000Z", "schema": {}})
        resolve_body = json.dumps({
            "result": {"AAPL": [{"d0": "2020-01-01", "d1": "2026-09-01", "s": "1"}]},
            "symbols": ["AAPL"], "partial": [], "not_found": [],
        })
        transport = ScriptedMetadataTransport(
            gets={
                "metadata.get_dataset_range": HttpResponse(200, range_body),
                "metadata.get_cost": HttpResponse(200, "1.50"),
                "metadata.get_billable_size": HttpResponse(200, "1000"),
            },
            posts={"symbology.resolve": HttpResponse(200, resolve_body)},
        )
        client = DatabentoCostPreflightClient(_config(), transport=transport)
        legs = (
            PilotLegRequest("a", "EQUS.SUMMARY", "ohlcv-1d", ("AAPL",), date(2020, 1, 1), date(2026, 9, 1)),
            PilotLegRequest("b", "EQUS.MINI", "ohlcv-1m", ("AAPL",), date(2026, 8, 1), date(2026, 9, 1)),
        )
        report = run_pilot_cost_preflight(client, legs)
        self.assertEqual(report.combined_estimated_cost_usd, Decimal("3.00"))
        self.assertEqual(report.combined_estimated_billable_size_bytes, 2000)
        self.assertFalse(report.any_symbol_not_available)

    def test_phase1_pilot_legs_exclude_twtr(self) -> None:
        all_symbols = {symbol for leg in PHASE1_PILOT_LEGS for symbol in leg.symbols}
        self.assertNotIn("TWTR", all_symbols)
        self.assertEqual(PHASE1_DAILY_LEG.dataset, "EQUS.SUMMARY")
        self.assertEqual(PHASE1_MINUTE_LEG.dataset, "EQUS.MINI")
        self.assertEqual(len(PHASE1_DAILY_LEG.symbols), 15)
        self.assertEqual(len(PHASE1_MINUTE_LEG.symbols), 5)


if __name__ == "__main__":
    unittest.main()
