"""OR-6 F3 forward -- settled funding contract, authorization gate, two clocks, as-of windows, store.

Offline. Every body is a synthetic fixture shaped like the public endpoint's
response; no rate here is a real observation and nothing touches the network.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import tempfile
import unittest
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from trade_platform import bybit_forward_settled_funding_v1 as forward
from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.bybit_forward_settled_funding_v1 import (
    AVAILABLE_FROM_KNOWLEDGE_TIME,
    COMPLETE,
    INDICATIVE_TICKER_FUNDING_STATE,
    IRREGULAR_REFUSED,
    NOT_AVAILABLE_CLOCK_UNCERTAINTY_MISSING,
    SETTLED_FUNDING_HISTORY,
    SOURCE_CONTRACT_V1,
    UNRECOGNIZED_FUNDING_PAYLOAD,
    UNRESOLVED_OWNER_AUTHORIZATION,
    ForwardFundingAcquisitionAuthorizationV1,
    ForwardFundingError,
    ForwardFundingObservationV1,
    ForwardSettledFundingStoreV1,
    ObservationClockBoundV1,
    PublishedFundingIntervalV1,
    acquire_forward_settled_funding_v1,
    canonical_symbol_identity_v1,
    classify_bybit_funding_payload_v1,
    forward_funding_window_as_of_v1,
    observe_forward_settled_funding_v1,
    require_forward_funding_acquisition_authorized_v1,
    resolve_forward_funding_acquisition_authorization_v1,
)
from trade_platform.bybit_funding_history_v1 import (
    FUNDING_HISTORY_SCHEMA_VERSION_V1,
    acquire_funding_history_v1,
)
from trade_platform.bybit_public_archive_v1 import HttpResponseV1
from trade_platform.public_archive_research_bars_v1 import ResearchBarsError

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_H = 3_600_000
DAY0 = datetime(2026, 6, 1, tzinfo=UTC)
BOUND = ObservationClockBoundV1(9_470_000_000, "clock-bound-v1:prior=fixture:next=fixture")


def _ms(value: datetime) -> int:
    return (value - _EPOCH) // timedelta(milliseconds=1)


D0 = _ms(DAY0)
INTERVAL_8H = PublishedFundingIntervalV1(8, "t4-funding:fixture:fundingIntervalHour", D0 * 1000 - 1)


def _body(rates: Mapping[int, str], *, server_time_ms: int, symbol: str = "BTCUSDT") -> bytes:
    rows = [{"symbol": symbol, "fundingRate": rate, "fundingRateTimestamp": str(at)}
            for at, rate in sorted(rates.items(), reverse=True)]
    return json.dumps({"retCode": 0, "retMsg": "OK", "result": {"category": "linear", "list": rows},
                       "retExtInfo": {}, "time": server_time_ms}).encode()


def _observe(rates: Mapping[int, str], *, received_ms: int, start_ms: int = D0, end_ms: int = D0 + 24 * _H - 1,
             bound: ObservationClockBoundV1 | None = BOUND, symbol: str = "BTCUSDT",
             server_time_ms: int | None = None) -> tuple[ForwardFundingObservationV1, bytes]:
    body = _body({at: r for at, r in rates.items() if start_ms <= at <= end_ms},
                 server_time_ms=received_ms if server_time_ms is None else server_time_ms, symbol=symbol)
    return observe_forward_settled_funding_v1(
        body, symbol=symbol, start_ms=start_ms, end_ms=end_ms,
        observed_at_host_utc_nanos=received_ms * 1_000_000, clock_bound=bound), body


RATES = {D0: "0.0001", D0 + 8 * _H: "-0.00005", D0 + 16 * _H: "0.000125"}
NEXT_DAY = D0 + 24 * _H + 5_000  # received 5 s after the window closes


def _window(observations: list[ForwardFundingObservationV1], *, as_of_micros: int, start_ms: int = D0,
            end_ms: int = D0 + 24 * _H, interval: PublishedFundingIntervalV1 | None = INTERVAL_8H,
            ) -> forward.ForwardFundingWindowV1:
    return forward_funding_window_as_of_v1(observations, symbol="BTCUSDT", start_ms=start_ms, end_ms=end_ms,
                                           as_of_micros=as_of_micros, published_interval=interval)


class SourceContractTests(unittest.TestCase):
    def test_contract_is_forward_settled_and_distinct_from_history_and_t4(self) -> None:
        self.assertEqual(SOURCE_CONTRACT_V1["record_kind"], "SETTLED_PUBLISHED")
        self.assertEqual(SOURCE_CONTRACT_V1["access"], "PUBLIC_UNAUTHENTICATED_GET")
        self.assertNotEqual(forward.FORWARD_FUNDING_SCHEMA_VERSION_V1, FUNDING_HISTORY_SCHEMA_VERSION_V1)
        self.assertIn(FUNDING_HISTORY_SCHEMA_VERSION_V1, SOURCE_CONTRACT_V1["distinct_from"])
        self.assertIn("bybit-v5-tickers-published-funding-state-not-settlement", SOURCE_CONTRACT_V1["distinct_from"])

    def test_canonical_symbol_universe_only(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            self.assertEqual(canonical_symbol_identity_v1(symbol)["canonical_key"], f"BYBIT:linear:{symbol}")
        for symbol in ("XRPUSDT", "btcusdt", "BTC-USDT", ""):
            with self.assertRaises(ForwardFundingError):
                canonical_symbol_identity_v1(symbol)

    def test_module_has_no_network_client(self) -> None:
        source = inspect.getsource(forward)
        for needle in ("urllib", "urlopen", "requests", "httpx", "socket"):
            self.assertNotIn(needle, source)


class AuthorizationGateTests(unittest.TestCase):
    def test_resolves_unresolved_and_always_refuses(self) -> None:
        authorization = resolve_forward_funding_acquisition_authorization_v1()
        self.assertEqual(authorization.state, UNRESOLVED_OWNER_AUTHORIZATION)
        with self.assertRaisesRegex(ForwardFundingError, "not_authorized:UNRESOLVED_OWNER_AUTHORIZATION"):
            require_forward_funding_acquisition_authorized_v1(authorization)
        with self.assertRaisesRegex(ForwardFundingError, "authorization_missing"):
            require_forward_funding_acquisition_authorized_v1(None)
        with self.assertRaisesRegex(ForwardFundingError, "not_authorized"):
            acquire_forward_settled_funding_v1("BTCUSDT", authorization=authorization)

    def test_no_authorized_state_can_be_constructed(self) -> None:
        for state in ("AUTHORIZED", "OWNER_AUTHORIZED", ""):
            with self.assertRaises(ForwardFundingError):
                ForwardFundingAcquisitionAuthorizationV1(state, ())

    def test_holdout_opening_is_not_forward_authority(self) -> None:
        opening = validation.HoldoutOpeningV1("cycle-2026-08-20", datetime(2026, 8, 20, tzinfo=UTC),
                                              datetime(2026, 9, 1, tzinfo=UTC), "q" * 64, "test",
                                              validation._REGISTRY_ISSUER)
        with self.assertRaisesRegex(ForwardFundingError, "holdout_opening_is_not_forward_funding_authorization"):
            require_forward_funding_acquisition_authorized_v1(opening)
        with self.assertRaisesRegex(ForwardFundingError, "holdout_opening_is_not_forward_funding_authorization"):
            acquire_forward_settled_funding_v1("BTCUSDT", authorization=opening)

    def test_historical_holdout_gate_is_unchanged(self) -> None:
        def never(url: str, headers: Mapping[str, str]) -> HttpResponseV1:
            raise AssertionError("the holdout gate must refuse before any fetch")

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        with self.assertRaises(ResearchBarsError):
            acquire_funding_history_v1(root, "BTCUSDT", date(2026, 8, 20), date(2026, 8, 20), fetch=never,
                                       now=lambda: datetime(2026, 10, 1, tzinfo=UTC))


class PayloadKindTests(unittest.TestCase):
    def test_indicative_ticker_funding_is_never_settled(self) -> None:
        rest_tickers = json.dumps({"retCode": 0, "result": {"category": "linear", "list": [
            {"symbol": "BTCUSDT", "fundingRate": "0.0001", "nextFundingTime": str(D0 + 8 * _H),
             "fundingIntervalHour": "8", "fundingCap": "0.005"}]}, "time": D0}).encode()
        ws_tickers = json.dumps({"topic": "tickers.BTCUSDT", "type": "snapshot", "ts": D0,
                                 "data": {"symbol": "BTCUSDT", "fundingRate": "0.0001",
                                          "nextFundingTime": str(D0 + 8 * _H)}}).encode()
        t4_frame_row = json.dumps({"published_funding_rate": "0.0001", "next_funding_at": "2026-06-01T08:00:00Z",
                                   "fundingRateTimestamp": str(D0)}).encode()
        # A settled-looking row that also carries an indicative field is still indicative.
        mixed = _body({D0: "0.0001"}, server_time_ms=D0 + 1).replace(
            b'"fundingRateTimestamp"', b'"nextFundingTime": "1", "fundingRateTimestamp"')
        for body in (rest_tickers, ws_tickers, t4_frame_row, mixed):
            self.assertEqual(classify_bybit_funding_payload_v1(body), INDICATIVE_TICKER_FUNDING_STATE)
            with self.assertRaisesRegex(ForwardFundingError, "indicative_ticker_funding_is_never_settled"):
                observe_forward_settled_funding_v1(body, symbol="BTCUSDT", start_ms=D0, end_ms=D0 + _H,
                                                   observed_at_host_utc_nanos=(D0 + _H) * 1_000_000,
                                                   clock_bound=BOUND)

    def test_classification(self) -> None:
        self.assertEqual(classify_bybit_funding_payload_v1(_body(RATES, server_time_ms=NEXT_DAY)),
                         SETTLED_FUNDING_HISTORY)
        self.assertEqual(classify_bybit_funding_payload_v1(b"not json"), UNRECOGNIZED_FUNDING_PAYLOAD)
        self.assertEqual(classify_bybit_funding_payload_v1(b'{"retCode":0,"result":{"list":[{"x":1}]}}'),
                         UNRECOGNIZED_FUNDING_PAYLOAD)

    def test_strict_parse_is_inherited(self) -> None:
        for bad in ({D0: "1e-4"}, {D0: "0.0001 "}):
            with self.assertRaises(ForwardFundingError):
                _observe(bad, received_ms=NEXT_DAY)
        with self.assertRaisesRegex(ForwardFundingError, "outside_the_requested_window"):
            observe_forward_settled_funding_v1(_body({D0 - _H: "0.0001"}, server_time_ms=NEXT_DAY),
                                               symbol="BTCUSDT", start_ms=D0, end_ms=D0 + 24 * _H - 1,
                                               observed_at_host_utc_nanos=NEXT_DAY * 1_000_000, clock_bound=BOUND)
        body = json.dumps({"retCode": 0, "result": {"category": "linear", "list": []}}).encode()
        with self.assertRaisesRegex(ForwardFundingError, "server_time_missing"):
            observe_forward_settled_funding_v1(body, symbol="BTCUSDT", start_ms=D0, end_ms=D0 + _H,
                                               observed_at_host_utc_nanos=NEXT_DAY * 1_000_000, clock_bound=BOUND)


class ObservationTests(unittest.TestCase):
    def test_raw_sha256_identity_and_idempotency_key(self) -> None:
        first, body = _observe(RATES, received_ms=NEXT_DAY)
        again, _ = _observe(RATES, received_ms=NEXT_DAY)
        later, _ = _observe(RATES, received_ms=NEXT_DAY + 60_000)
        self.assertEqual(first.provenance["raw_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(first.idempotency_key, again.idempotency_key)
        self.assertEqual(first.content_hash, later.content_hash)  # identity = published content only
        self.assertNotEqual(first.provenance["raw_sha256"], later.provenance["raw_sha256"])
        self.assertNotEqual(first.idempotency_key, later.idempotency_key)
        self.assertNotEqual(first.observation_id, later.observation_id)
        self.assertEqual([e.rate_text for e in first.events()], ["0.0001", "-0.00005", "0.000125"])
        self.assertEqual(first.identity["symbol"]["canonical_key"], "BYBIT:linear:BTCUSDT")
        self.assertNotIn("raw_sha256", first.identity)

    def test_settlement_and_observation_clocks_stay_separate(self) -> None:
        observation, _ = _observe(RATES, received_ms=NEXT_DAY)
        self.assertEqual(observation.events()[0].settled_at_ms, D0)
        self.assertEqual(observation.provenance["observed_at_host_utc_nanos"], NEXT_DAY * 1_000_000)
        self.assertEqual(observation.knowledge_at_micros, NEXT_DAY * 1000 + 9_470_000)
        self.assertEqual(observation.availability, AVAILABLE_FROM_KNOWLEDGE_TIME)

    def test_missing_uncertainty_is_never_available(self) -> None:
        observation, _ = _observe(RATES, received_ms=NEXT_DAY, bound=None)
        self.assertIsNone(observation.knowledge_at_micros)
        self.assertEqual(observation.availability, NOT_AVAILABLE_CLOCK_UNCERTAINTY_MISSING)
        far_future = _ms(datetime(2100, 1, 1, tzinfo=UTC)) * 1000
        self.assertFalse(observation.is_available_at(far_future))
        window = _window([observation], as_of_micros=far_future)
        self.assertEqual(window.status, IRREGULAR_REFUSED)
        self.assertEqual(window.events(), [])
        for bad in ("", "   "):
            with self.assertRaises(ForwardFundingError):
                ObservationClockBoundV1(0, bad)
        with self.assertRaises(ForwardFundingError):
            ObservationClockBoundV1(True, "x")  # type: ignore[arg-type]

    def test_clock_contradictions_refuse(self) -> None:
        tight = ObservationClockBoundV1(0, "clock-bound-v1:fixture")
        with self.assertRaisesRegex(ForwardFundingError, "server_time_contradicts"):
            _observe(RATES, received_ms=NEXT_DAY, bound=tight, server_time_ms=NEXT_DAY + 1)
        with self.assertRaisesRegex(ForwardFundingError, "settlement_after_the_published_server_time"):
            _observe(RATES, received_ms=NEXT_DAY, bound=None, server_time_ms=D0 + 16 * _H - 1)
        with self.assertRaisesRegex(ForwardFundingError, "settlement_after"):
            _observe(RATES, received_ms=D0 + 16 * _H - 1, bound=tight, server_time_ms=D0 + 16 * _H - 2)


class AsOfAvailabilityTests(unittest.TestCase):
    def test_available_only_from_knowledge_time(self) -> None:
        observation, _ = _observe(RATES, received_ms=NEXT_DAY)
        known = observation.knowledge_at_micros
        assert known is not None
        self.assertFalse(observation.is_available_at(known - 1))
        self.assertTrue(observation.is_available_at(known))
        before = _window([observation], as_of_micros=known - 1)
        self.assertEqual(before.status, IRREGULAR_REFUSED)
        self.assertEqual(before.events(), [])
        self.assertEqual(before.identity["observations"], [])
        at = _window([observation], as_of_micros=known)
        self.assertEqual(at.status, COMPLETE, at.irregularities)
        self.assertEqual(at.identity["completeness"]["interval_ms"], 8 * _H)

    def test_later_acquisition_never_appears_earlier(self) -> None:
        midday, _ = _observe({at: r for at, r in RATES.items() if at <= D0 + 8 * _H},
                             received_ms=D0 + 12 * _H + 5_000)
        full, _ = _observe(RATES, received_ms=NEXT_DAY)
        assert midday.knowledge_at_micros is not None and full.knowledge_at_micros is not None
        as_of = midday.knowledge_at_micros
        half = _window([midday], as_of_micros=as_of, end_ms=D0 + 12 * _H)
        self.assertEqual(half.status, COMPLETE, half.irregularities)
        self.assertEqual(len(half.events()), 2)
        # Appending later evidence (in any order) changes nothing about an earlier as-of answer.
        for pool in ([midday, full], [full, midday]):
            self.assertEqual(_window(pool, as_of_micros=as_of, end_ms=D0 + 12 * _H).content_hash, half.content_hash)
            whole_early = _window(pool, as_of_micros=as_of)
            self.assertEqual(whole_early.status, IRREGULAR_REFUSED)
            self.assertIn("WINDOW_NOT_CLOSED_AS_OF", whole_early.irregularities)
            self.assertNotIn(D0 + 16 * _H, [e.settled_at_ms for e in whole_early.events()])
            just_before = _window(pool, as_of_micros=full.knowledge_at_micros - 1)
            self.assertNotIn(str(full.observation_id), just_before.identity["observations"])
            self.assertEqual(just_before.status, IRREGULAR_REFUSED)
            self.assertEqual(_window(pool, as_of_micros=full.knowledge_at_micros).status, COMPLETE)


class CompletenessTests(unittest.TestCase):
    def setUp(self) -> None:
        observation, _ = _observe(RATES, received_ms=NEXT_DAY)
        assert observation.knowledge_at_micros is not None
        self.observation, self.as_of = observation, observation.knowledge_at_micros

    def test_complete_on_uniform_spacing_matching_the_published_interval(self) -> None:
        window = _window([self.observation], as_of_micros=self.as_of)
        self.assertEqual(window.status, COMPLETE)
        self.assertEqual([e.settled_at_ms for e in window.events()], sorted(RATES))

    def test_interval_mismatch_missing_and_late_interval_refuse(self) -> None:
        four = PublishedFundingIntervalV1(4, "t4-funding:fixture", D0 * 1000)
        self.assertEqual(_window([self.observation], as_of_micros=self.as_of, interval=four).status,
                         IRREGULAR_REFUSED)
        missing = _window([self.observation], as_of_micros=self.as_of, interval=None)
        self.assertIn("PUBLISHED_INTERVAL_EVIDENCE_MISSING", missing.irregularities)
        late = PublishedFundingIntervalV1(8, "t4-funding:fixture", self.as_of + 1)
        self.assertIn("PUBLISHED_INTERVAL_NOT_KNOWN_AS_OF",
                      _window([self.observation], as_of_micros=self.as_of, interval=late).irregularities)
        for bad in (0, -8):
            with self.assertRaises(ForwardFundingError):
                PublishedFundingIntervalV1(bad, "x", 0)

    def test_missing_record_is_refused_never_interpolated(self) -> None:
        gapped, _ = _observe({D0: "0.0001", D0 + 16 * _H: "0.000125"}, received_ms=NEXT_DAY)
        assert gapped.knowledge_at_micros is not None
        window = _window([gapped], as_of_micros=gapped.knowledge_at_micros)
        self.assertEqual(window.status, IRREGULAR_REFUSED)
        self.assertTrue(any(item.startswith("SPACING:") for item in window.irregularities))
        self.assertEqual([e.settled_at_ms for e in window.events()], [D0, D0 + 16 * _H])
        self.assertIsNone(window.identity["completeness"]["interval_ms"])

    def test_irregular_interval_and_edges_refuse(self) -> None:
        irregular, _ = _observe({D0: "0.0001", D0 + 8 * _H: "0.0001", D0 + 12 * _H: "0.0001",
                                 D0 + 16 * _H: "0.0001"}, received_ms=NEXT_DAY)
        assert irregular.knowledge_at_micros is not None
        self.assertEqual(_window([irregular], as_of_micros=irregular.knowledge_at_micros).status, IRREGULAR_REFUSED)
        tail, _ = _observe({D0: "0.0001", D0 + 8 * _H: "0.0001"}, received_ms=NEXT_DAY)
        assert tail.knowledge_at_micros is not None
        self.assertTrue(any(item.startswith("TRAILING_EDGE_NOT_PROVEN")
                            for item in _window([tail], as_of_micros=tail.knowledge_at_micros).irregularities))
        single, _ = _observe({D0: "0.0001"}, received_ms=NEXT_DAY)
        assert single.knowledge_at_micros is not None
        self.assertIn("ONE_RECORD_PROVES_NO_INTERVAL",
                      _window([single], as_of_micros=single.knowledge_at_micros, end_ms=D0 + 8 * _H).irregularities)
        empty, _ = _observe({}, received_ms=NEXT_DAY)
        assert empty.knowledge_at_micros is not None
        self.assertIn("NO_RECORD_IN_WINDOW", _window([empty], as_of_micros=empty.knowledge_at_micros).irregularities)

    def test_uncovered_span_refuses(self) -> None:
        morning, _ = _observe(RATES, received_ms=NEXT_DAY, end_ms=D0 + 10 * _H)
        evening, _ = _observe(RATES, received_ms=NEXT_DAY, start_ms=D0 + 14 * _H)
        as_of = max(morning.knowledge_at_micros or 0, evening.knowledge_at_micros or 0)
        window = _window([morning, evening], as_of_micros=as_of)
        self.assertEqual([e.settled_at_ms for e in window.events()], sorted(RATES))
        self.assertTrue(any(item.startswith("NOT_COVERED:") for item in window.irregularities))
        self.assertEqual(window.status, IRREGULAR_REFUSED)

    def test_conflicting_published_rates_refuse(self) -> None:
        other, _ = _observe({**RATES, D0 + 8 * _H: "-0.00006"}, received_ms=NEXT_DAY + 1_000)
        assert other.knowledge_at_micros is not None
        window = _window([self.observation, other], as_of_micros=other.knowledge_at_micros)
        self.assertEqual(window.status, IRREGULAR_REFUSED)
        self.assertIn("SETTLED_RATE_CONFLICT:2026-06-01T08:00:00+00:00", window.irregularities)
        self.assertNotIn(D0 + 8 * _H, [e.settled_at_ms for e in window.events()])

    def test_other_symbols_are_not_mixed_in(self) -> None:
        eth, _ = _observe(RATES, received_ms=NEXT_DAY, symbol="ETHUSDT")
        window = _window([eth], as_of_micros=self.as_of + 10**9)
        self.assertEqual(window.events(), [])
        self.assertEqual(window.status, IRREGULAR_REFUSED)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.store = ForwardSettledFundingStoreV1(self.root)

    def test_append_only_idempotent_and_reproducible(self) -> None:
        first, body = _observe(RATES, received_ms=NEXT_DAY)
        second, second_body = _observe(RATES, received_ms=NEXT_DAY + 60_000)
        self.assertTrue(self.store.append(first, body))
        self.assertFalse(self.store.append(first, body))  # same idempotency key: no-op
        self.assertTrue(self.store.append(second, second_body))
        loaded = self.store.load("BTCUSDT")
        self.assertEqual(sorted(o.idempotency_key for o in loaded),
                         sorted([first.idempotency_key, second.idempotency_key]))
        self.assertEqual(self.store.load("ETHUSDT"), [])
        unbounded, unbounded_body = _observe(RATES, received_ms=NEXT_DAY, bound=None)
        self.assertTrue(self.store.append(unbounded, unbounded_body))
        reloaded = {o.idempotency_key: o for o in self.store.load("BTCUSDT")}
        self.assertIsNone(reloaded[unbounded.idempotency_key].knowledge_at_micros)

    def test_wrong_body_or_forged_record_refuses(self) -> None:
        first, body = _observe(RATES, received_ms=NEXT_DAY)
        with self.assertRaisesRegex(ForwardFundingError, "raw_body_does_not_match"):
            self.store.append(first, body + b" ")
        forged = ForwardFundingObservationV1(first.identity, first.content_hash,
                                             {**first.provenance, "knowledge_at_micros": 1}, first.provenance_hash,
                                             first.idempotency_key)
        with self.assertRaisesRegex(ForwardFundingError, "does_not_reproduce"):
            self.store.append(forged, body)

    def test_tampered_raw_or_record_refuses_on_load(self) -> None:
        first, body = _observe(RATES, received_ms=NEXT_DAY)
        self.store.append(first, body)
        base = self.root / "forward-settled-funding" / "bybit-v5" / "symbol=BTCUSDT"
        raw = base / "raw" / f"{first.provenance['raw_sha256']}.json"
        record = base / "observations" / f"{first.observation_id}.json"
        original_record = record.read_bytes()
        stored = json.loads(original_record)
        stored["provenance"]["knowledge_at_micros"] = 1
        record.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaisesRegex(ForwardFundingError, "does_not_reproduce"):
            self.store.load("BTCUSDT")
        record.write_bytes(original_record)
        raw.write_bytes(body.replace(b"0.0001", b"0.0002"))
        with self.assertRaisesRegex(ForwardFundingError, "raw_body_changed"):
            self.store.load("BTCUSDT")
        with self.assertRaisesRegex(ForwardFundingError, "immutable_record_conflict"):
            self.store.append(first, body)  # the stored raw body is never rewritten


if __name__ == "__main__":
    unittest.main()
