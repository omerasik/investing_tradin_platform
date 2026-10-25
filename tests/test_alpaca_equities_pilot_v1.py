"""Offline contract, exact decimals, calendar and failure evidence for P3A."""

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from trade_platform.alpaca_equities_pilot_v1 import (
    BASE_URL,
    DATASET,
    NAMESPACE,
    PROVIDER,
    AlpacaIexFixtureAdapterV1,
    validate_pilot_coverage,
)
from trade_platform.data_providers import (
    HttpResponse,
    ProviderConfiguration,
    ProviderError,
    ProviderHealthRegistry,
)
from trade_platform.historical_market_data import (
    AdjustmentStatus,
    AuthorizedHistoricalSource,
    ObservationKind,
)
from trade_platform.professional_instruments import mvp_instrument_universe
from trade_platform.provider_ingestion import (
    HistoricalIngestionRequest,
    ingest_raw_historical_pages,
)

NOW = datetime(2025, 1, 6, 18, tzinfo=UTC)
START = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
SOURCE_ID = UUID("d692ed64-17d1-57c4-961c-8456721d35ee")


def source():
    return AuthorizedHistoricalSource(
        PROVIDER, DATASET, NAMESPACE, "fixture-contract-only-v1", "test-authorization://p3a/offline-only",
        datetime(2020, 1, 1, tzinfo=UTC), datetime(2020, 1, 1, tzinfo=UTC),
        source_id=SOURCE_ID, authorized_observation_kinds=frozenset({ObservationKind.OHLCV}),
    )


def scope(symbol="AAPL", **overrides):
    return {"symbol": symbol, "start": START.isoformat(), "end": (START + timedelta(minutes=3)).isoformat(),
            "feed": "iex", "adjustment": "raw", "timeframe": "1Min", "asof": "-", **overrides}


def fixture_pages(symbol="AAPL"):
    # Keep JSON number lexemes exact; never round the provider-shaped fixtures
    # through Python float while loading or preparing an HTTP response body.
    text = Path("tests/fixtures/alpaca_iex_pilot_v1.json").read_text(encoding="utf-8")
    data = json.loads(text, parse_float=Decimal)
    def encode(obj):
        if isinstance(obj, Decimal):
            return str(obj)
        raise TypeError
    bodies = [json.dumps(page, default=encode) for page in data[symbol]]
    # Restore fractional numeric lexemes without ever constructing a float.
    for index, body in enumerate(bodies):
        for row in data[symbol][index]["bars"]:
            for value in row.values():
                if isinstance(value, Decimal):
                    body = body.replace(json.dumps(str(value)), str(value))
        bodies[index] = body
    return {None: HttpResponse(200, bodies[0]), "fixture-second-page": HttpResponse(200, bodies[1])}


class FixtureMaster:
    """Test-only PIT master/calendar stand-in, never a production authority."""
    def resolve_identifier_point_in_time(self, namespace, value, effective_at, known_at):
        assert namespace == NAMESPACE
        return next(i for i in mvp_instrument_universe(datetime(2020, 1, 1, tzinfo=UTC)) if i.canonical_symbol == value)

    def is_open(self, venue, observed_at, *, known_at=None):
        local = observed_at.astimezone(ZoneInfo("America/New_York"))
        return local.weekday() < 5 and local.date().isoformat() != "2025-01-01" and time(9, 30) <= local.time() < time(16)


class RecordingSink:
    def __init__(self):
        self.records = []

    def capture_raw(self, observations):
        self.records.extend(observations)
        return tuple(o.raw_observation_id for o in observations)


class AlpacaPilotTests(unittest.TestCase):
    def adapter(self, pages=None, **kwargs):
        return AlpacaIexFixtureAdapterV1(source(), FixtureMaster(), fixture_pages=fixture_pages() if pages is None else pages,
                                        now=lambda: NOW, **kwargs)

    def test_disabled_default_has_no_network_or_credential_path(self):
        adapter = AlpacaIexFixtureAdapterV1(source(), FixtureMaster())
        self.assertEqual(adapter.capabilities.license_status, "EXTERNAL_BLOCKED")
        self.assertFalse(adapter.configuration.terms_accepted)
        with self.assertRaisesRegex(ProviderError, "real_access_external_blocked"):
            adapter.fetch_raw_page(SOURCE_ID, scope(), None)

    def test_non_fixture_authorization_and_terms_or_credentials_are_rejected(self):
        for configuration in (
            ProviderConfiguration(PROVIDER, BASE_URL, terms_accepted=True),
            # An environment-variable reference, never a credential; rejection is under test.
            ProviderConfiguration(PROVIDER, BASE_URL, secret_reference="env:FIXTURE_ONLY"),  # pragma: allowlist secret
            ProviderConfiguration(PROVIDER, "https://example.invalid"),
        ):
            with self.subTest(configuration=configuration), self.assertRaisesRegex(ProviderError, "fixture_authorization"):
                self.adapter(configuration=configuration).fetch_raw_page(SOURCE_ID, scope(), None)
        adapter = self.adapter()
        adapter.source = replace(source(), authorization_reference="owner-reference-not-a-fixture")
        with self.assertRaisesRegex(ProviderError, "fixture_authorization"):
            adapter.fetch_raw_page(SOURCE_ID, scope(), None)

    def test_exact_decimals_identical_raw_ids_and_distinct_feed_listing(self):
        page = self.adapter().fetch_raw_page(SOURCE_ID, scope(), None)
        again = self.adapter().fetch_raw_page(SOURCE_ID, scope(), None)
        self.assertEqual(page, again)
        first = page.records[0]
        self.assertEqual(Decimal(str(first.raw_payload["open"])), Decimal("174.123456789012"))
        self.assertEqual(first.exchange, "IEXG")
        self.assertEqual(first.raw_payload["listing_mic"], "XNAS")
        self.assertEqual(first.raw_payload["feed"], "iex")
        self.assertEqual(first.adjustment_status, AdjustmentStatus.RAW)
        self.assertEqual(first.effective_at, START + timedelta(minutes=1))
        self.assertEqual(first.ingested_at, NOW)
        self.assertEqual(first.raw_payload["evidence_tier"], "T0_SYNTHETIC")
        self.assertIn("asof=-", first.provenance_uri)

    def test_both_instruments_paginate_through_existing_orchestrator(self):
        for symbol in ("AAPL", "SPY"):
            adapter = AlpacaIexFixtureAdapterV1(source(), FixtureMaster(), fixture_pages=fixture_pages(symbol), now=lambda: NOW)
            sink = RecordingSink()
            result = ingest_raw_historical_pages(adapter, HistoricalIngestionRequest(SOURCE_ID, scope(symbol), maximum_pages=40),
                                                 sink, ProviderHealthRegistry(lambda: NOW), now=lambda: NOW)
            self.assertEqual(result.state.value, "HEALTHY")
            self.assertEqual(len(sink.records), 3)
            validate_pilot_coverage(sink.records, scope(symbol), FixtureMaster(), known_at=NOW)

    def test_feed_adjustment_symbol_remapping_and_window_are_explicit(self):
        for change in ({"feed": "sip"}, {"adjustment": "split"}, {"adjustment": "all"},
                       {"asof": "2025-01-06"}, {"symbol": "META"}, {"timeframe": "1Day"},
                       {"end": "2025-01-08T14:30:00Z"}, {"start": "2025-01-02T09:30:00-05:00"}):
            with self.subTest(change=change), self.assertRaises(ProviderError):
                self.adapter().fetch_raw_page(SOURCE_ID, scope(**change), None)

    def test_invalid_shapes_ohlc_numbers_duplicates_and_time_fail(self):
        base = json.loads(fixture_pages()[None].body)
        for change in ({"t": "2025-01-02T14:30:00.1Z"}, {"t": "2025-01-02T14:33:00Z"},
                       {"o": True}, {"v": -1}, {"h": 1}, {"c": "NaN"},
                       {"o": "174.123456789012345678"}, {"v": "1e18"},
                       {"t": "2025-01-02T14:30:00.000000001Z"}):
            item = json.loads(json.dumps(base))
            item["bars"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ProviderError):
                self.adapter({None: HttpResponse(200, json.dumps(item))}).fetch_raw_page(SOURCE_ID, scope(), None)
        for bars in ([], [base["bars"][0]] * 2, list(reversed(base["bars"]))):
            with self.subTest(bars=bars), self.assertRaises(ProviderError):
                self.adapter({None: HttpResponse(200, json.dumps({**base, "bars": bars}))}).fetch_raw_page(SOURCE_ID, scope(), None)
        for body in ('{"symbol":"AAPL","symbol":"SPY"}', '[]', '{'):
            with self.subTest(body=body), self.assertRaises(ProviderError):
                self.adapter({None: HttpResponse(200, body)}).fetch_raw_page(SOURCE_ID, scope(), None)

    def test_missing_pages_http_errors_and_page_bound_never_succeed(self):
        for pages, maximum in (({None: fixture_pages()[None]}, 40), (fixture_pages(), 1),
                               ({None: HttpResponse(429, "rate limited")}, 40),
                               ({None: HttpResponse(403, "not entitled")}, 40)):
            sink = RecordingSink()
            result = ingest_raw_historical_pages(self.adapter(pages), HistoricalIngestionRequest(SOURCE_ID, scope(), maximum_pages=maximum),
                                                 sink, ProviderHealthRegistry(lambda: NOW), now=lambda: NOW)
            self.assertEqual(result.state.value, "ERROR")

    def test_cursor_cannot_be_reused_for_another_scope(self):
        page = self.adapter().fetch_raw_page(SOURCE_ID, scope(), None)
        with self.assertRaisesRegex(ProviderError, "cursor_scope_mismatch"):
            self.adapter().fetch_raw_page(SOURCE_ID, scope(end="2025-01-02T14:34:00Z"), page.next_cursor)
        with self.assertRaises(ProviderError):
            self.adapter().fetch_raw_page(SOURCE_ID, scope(), "[]")

    def test_provider_cursor_loop_and_invalid_or_exhausted_cursor_fail(self):
        adapter = self.adapter()
        first = adapter.fetch_raw_page(SOURCE_ID, scope(), None)
        pages = fixture_pages()
        pages["fixture-second-page"] = pages[None]
        with self.assertRaisesRegex(ProviderError, "pagination_not_complete"):
            self.adapter(pages).fetch_raw_page(SOURCE_ID, scope(), first.next_cursor)
        cursor = json.loads(first.next_cursor)
        cursor["page"] = 40
        with self.assertRaisesRegex(ProviderError, "cursor_scope_mismatch"):
            adapter.fetch_raw_page(SOURCE_ID, scope(), json.dumps(cursor))
        pages[None] = HttpResponse(200, fixture_pages()[None].body.replace('"fixture-second-page"', '123'))
        with self.assertRaisesRegex(ProviderError, "invalid_page_token"):
            self.adapter(pages).fetch_raw_page(SOURCE_ID, scope(), None)

    def test_unknown_identity_and_missing_calendar_fail_as_provider_errors(self):
        from trade_platform.professional_instruments import (
            InstrumentResolutionError,
            ProfessionalCalendarError,
        )

        class MissingIdentity(FixtureMaster):
            def resolve_identifier_point_in_time(self, *args):
                raise InstrumentResolutionError("missing")

        class MissingCalendar(FixtureMaster):
            def is_open(self, *args, **kwargs):
                raise ProfessionalCalendarError("missing")

        for master, error in ((MissingIdentity(), "instrument_evidence_missing"), (MissingCalendar(), "calendar_evidence_missing")):
            adapter = AlpacaIexFixtureAdapterV1(source(), master, fixture_pages=fixture_pages(), now=lambda: NOW)
            with self.subTest(error=error), self.assertRaisesRegex(ProviderError, error):
                adapter.fetch_raw_page(SOURCE_ID, scope(), None)

    def test_missing_minute_or_entire_session_and_cross_page_duplicate_fail_coverage(self):
        rows = self.adapter().fetch_raw_page(SOURCE_ID, scope(), None).records
        for observations, request_scope in ((rows, scope()), (rows * 2, scope()),
                                            (rows, scope(end="2025-01-03T21:00:00Z"))):
            with self.subTest(request_scope=request_scope), self.assertRaisesRegex(ProviderError, "pilot_coverage"):
                validate_pilot_coverage(observations, request_scope, FixtureMaster(), known_at=NOW)

    def test_holiday_and_outside_session_bars_fail(self):
        for instant in ("2025-01-01T14:30:00Z", "2025-01-02T14:29:00Z"):
            body = json.loads(fixture_pages()[None].body)
            body["bars"] = [{**body["bars"][0], "t": instant}]
            end = (datetime.fromisoformat(instant) + timedelta(minutes=1)).isoformat()
            with self.subTest(instant=instant), self.assertRaisesRegex(ProviderError, "outside_registered_session"):
                self.adapter({None: HttpResponse(200, json.dumps(body))}).fetch_raw_page(
                    SOURCE_ID, scope(start=instant, end=end), None)
