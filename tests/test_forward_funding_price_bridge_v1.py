"""OR-6 F3 forward -- the settled-funding / proven-T4-price evidence bridge.

Offline. Funding bodies are synthetic fixtures shaped like the public endpoint's
response (no rate is a real observation). Bars are hand-built
:class:`T4ForwardBarV1` objects: the sealing and re-derivation rules have their
own tests in ``test_first_party_t4_forward_bars_v1``; here only the binding is
under test, and every price still goes through ``resolve_t4_price_reference_v1``.
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from tests.test_first_party_t4_forward_bars_v1 import _acceptance as t4_acceptance
from tests.test_first_party_t4_forward_bars_v1 import _ForwardFixture, _venue_minute
from trade_platform import forward_funding_price_bridge_v1 as bridge
from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.bybit_forward_settled_funding_v1 import (
    ForwardFundingError,
    ForwardFundingObservationV1,
    ForwardSettledFundingStoreV1,
    ObservationClockBoundV1,
    PublishedFundingIntervalV1,
    observe_forward_settled_funding_v1,
)
from trade_platform.bybit_funding_history_v1 import FUNDING_CHARGE_RULE_V1
from trade_platform.first_party_capture_hourly_acceptance_v1 import (
    HOUR_NANOS,
    HourlyAcceptanceV1,
    HourlySegmentV1,
    HourVerdictV1,
    SymbolHoursV1,
)
from trade_platform.first_party_t4_forward_bars_v1 import (
    PRICE_QUANTUM_V1,
    T4BarClockEvidenceV1,
    T4ForwardBarV1,
    T4ForwardDatasetBarsV1,
    T4PriceConventionV1,
    forward_bars_from_seal_v1,
    resolve_t4_price_reference_v1,
)
from trade_platform.first_party_t4_normalization_v1 import sha256_json_v1
from trade_platform.first_party_t4_settlement_mark_price_v1 import (
    PRICE_QUANTUM_V1 as MARK_PRICE_QUANTUM_V1,
)
from trade_platform.first_party_t4_settlement_mark_price_v1 import (
    UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION,
    UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY,
    T4MarkEvidenceDatasetV1,
    T4MarkObservationV1,
    T4TickerRowClockV1,
    mark_evidence_from_seal_v1,
    resolve_settlement_mark_price_v1,
)
from trade_platform.forward_funding_price_bridge_v1 import (
    CONFIRMED,
    EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED,
    EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED,
    EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK,
    FIRST_MINUTE_OF_AN_HOURLY_SEGMENT_IS_NOT_PROVABLE,
    HOST_ARRIVAL_UTC,
    LONGS_PAY_SHORTS,
    MARK_EVIDENCE_EXACT,
    MARK_EVIDENCE_NEARBY,
    MARK_EVIDENCE_REFUSED,
    MARK_EVIDENCE_UNAVAILABLE,
    NEARBY_MARKS_REPORTED_NONE_SELECTED,
    NO_NEARBY_MARK_WITHIN_THE_CALLER_TOLERANCE,
    PRICE_EVIDENCE_REFUSED,
    REFUSED_FUNDING_EVIDENCE,
    REFUSED_PRICE_EVIDENCE,
    SHORTS_PAY_LONGS,
    TRADE_PRICE_PROXY,
    UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION,
    VENUE_PUBLISHED_MARK_PRICE,
    ZERO_RATE_NO_TRANSFER,
    ForwardFundingPriceBridgeError,
    ForwardFundingPriceBridgeV1,
    FundingPriceConventionApprovalV1,
    bridge_forward_funding_prices_v1,
    payer_direction_v1,
    resolve_funding_price_convention_approval_v1,
)

OPEN = T4PriceConventionV1.FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT
CLOSE = T4PriceConventionV1.LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_H = 3_600_000
_MINUTE_US = 60_000_000
DAY0 = datetime(2026, 6, 1, tzinfo=UTC)
D0 = (DAY0 - _EPOCH) // timedelta(milliseconds=1)
END = D0 + 24 * _H
BOUND = ObservationClockBoundV1(9_470_000_000, "clock-bound-v1:prior=fixture:next=fixture")
INTERVAL_8H = PublishedFundingIntervalV1(8, "t4-funding:fixture:fundingIntervalHour", D0 * 1000 - 1)
RATES = {D0: "0.0001", D0 + 8 * _H: "-0.00005", D0 + 16 * _H: "0.000125"}
RECEIVED = END + 5_000  # received 5 s after the window closes
AS_OF = DAY0 + timedelta(hours=25)
SESSION = UUID(int=11)


def _observe(rates: Mapping[int, str], *, received_ms: int = RECEIVED,
             bound: ObservationClockBoundV1 | None = BOUND) -> ForwardFundingObservationV1:
    rows = [{"symbol": "BTCUSDT", "fundingRate": rate, "fundingRateTimestamp": str(at)}
            for at, rate in sorted(rates.items(), reverse=True)]
    body = json.dumps({"retCode": 0, "retMsg": "OK", "result": {"category": "linear", "list": rows},
                       "retExtInfo": {}, "time": received_ms}).encode()
    observation = observe_forward_settled_funding_v1(
        body, symbol="BTCUSDT", start_ms=D0, end_ms=END - 1,
        observed_at_host_utc_nanos=received_ms * 1_000_000, clock_bound=bound)
    _BODIES[observation.idempotency_key] = body  # the bridge only reads observations back from a store
    return observation


_BODIES: dict[str, bytes] = {}


def _store(observations: Sequence[ForwardFundingObservationV1]) -> ForwardSettledFundingStoreV1:
    root = Path(tempfile.mkdtemp(prefix="funding-price-bridge-"))
    _ROOTS.append(root)
    store = ForwardSettledFundingStoreV1(root)
    for observation in observations:
        store.append(observation, _BODIES[observation.idempotency_key])
    return store


_ROOTS: list[Path] = []


def tearDownModule() -> None:
    for root in _ROOTS:
        shutil.rmtree(root, ignore_errors=True)


def _bar(open_micros: int, *, open_price: str = "65000.5", close_price: str = "65001",
         knowledge_lag_micros: int = 2_000_000, close_ambiguous: bool = False) -> T4ForwardBarV1:
    prices = sorted(Decimal(p).quantize(PRICE_QUANTUM_V1) for p in (open_price, close_price))
    return T4ForwardBarV1(
        exchange_symbol="BTCUSDT", bar_open_micros=open_micros,
        open_price=Decimal(open_price).quantize(PRICE_QUANTUM_V1), high_price=prices[1],
        low_price=prices[0], close_price=Decimal(close_price).quantize(PRICE_QUANTUM_V1), trade_count=3,
        first_trade_reference=f"first:{open_micros}", last_trade_reference=f"last:{open_micros}",
        open_market_knowledge_micros=open_micros + 1_000_000,
        complete_market_knowledge_micros=open_micros + _MINUTE_US + knowledge_lag_micros,
        open_is_sequence_ambiguous=False, close_is_sequence_ambiguous=close_ambiguous,
        trade_manifest_hash="m" * 64,
        clock=T4BarClockEvidenceV1("o" * 64, "c" * 64, "CLOSE_BOUNDARY", 300_000_000, 350_000_000,
                                   ("session-rtt-sample-bracket-max-upper-v1:fixture",), 2,
                                   # Records arrive (host clock) inside the bar minute.
                                   first_arrival_utc_nanos=open_micros * 1000 + 1_000_000_000,
                                   last_arrival_utc_nanos=(open_micros + _MINUTE_US) * 1000 - 1))


def _dataset(bars: Sequence[T4ForwardBarV1], *, number: int = 1, symbol: str = "BTCUSDT") -> T4ForwardDatasetBarsV1:
    return T4ForwardDatasetBarsV1(
        dataset_version_id=UUID(int=number), dataset_content_hash=f"{number:064x}", source_id=UUID(int=99),
        session_id=SESSION, instrument="BTCUSDT-PERP", exchange_symbol=symbol,
        segment_clock_evidence_hash="e" * 64, bars=tuple(bars))


def _close_bars(**kwargs: Any) -> list[T4ForwardBarV1]:
    """The last minute before each settlement: what CLOSE reads."""
    return [_bar(at * 1000 - _MINUTE_US, **kwargs) for at in sorted(RATES)]


def _open_bars() -> list[T4ForwardBarV1]:
    """The minute starting at each settlement: what OPEN reads (absent in hourly segments)."""
    return [_bar(at * 1000) for at in sorted(RATES)]


def _acceptance(verdicts: Mapping[int, HourVerdictV1] | None = None) -> HourlyAcceptanceV1:
    first = (D0 - _H) * 1_000_000
    segments = []
    for index in range(26):
        start = first + index * HOUR_NANOS
        verdict = (verdicts or {}).get(start, HourVerdictV1.COMPLETE)
        complete = verdict is HourVerdictV1.COMPLETE
        segments.append(HourlySegmentV1(start, verdict, session_id=SESSION if complete else None,
                                        unproven_nanos=0 if complete else None))
    return HourlyAcceptanceV1(
        required_consecutive_hours=24, max_unproven_nanos=10_000_000_000, first_hour_start_utc_nanos=first,
        hours_judged=26, symbols=(SymbolHoursV1("BTCUSDT", tuple(segments)),),
        joint_complete=tuple(s.verdict is HourVerdictV1.COMPLETE for s in segments), runs=())


def _run(observations: Sequence[ForwardFundingObservationV1] | None = None, *,
         datasets: Sequence[T4ForwardDatasetBarsV1] | None = None, convention: Any = CLOSE,
         as_of: datetime = AS_OF, interval: PublishedFundingIntervalV1 | None = INTERVAL_8H,
         acceptance: HourlyAcceptanceV1 | None = None, symbol: str = "BTCUSDT",
         approval: object = None, marks: Sequence[Any] = (), tolerance: Any = None) -> ForwardFundingPriceBridgeV1:
    return bridge_forward_funding_prices_v1(
        _store([_observe(RATES)] if observations is None else observations), symbol=symbol, start_ms=D0, end_ms=END,
        as_of=as_of, published_interval=interval,
        datasets=[_dataset(_close_bars())] if datasets is None else datasets,
        acceptance=_acceptance() if acceptance is None else acceptance, convention=convention, approval=approval,
        mark_evidence=list(marks), mark_nearby_tolerance_ms=tolerance)


# Hand-built mark evidence. The host is 9.47 s behind the venue, so a ticker the
# venue stamps at a settlement hour arrives in the previous host-arrival hour.
_HOST_LAG_NS = 9_470_000_000
_MARK_BOUND_NS = 9_800_000_000
_INDEX, _MARK = "INDEX", "MARK"


def _ticker(sequence: int, ts_ms: int, component: str, bound_ns: int = _MARK_BOUND_NS) -> T4TickerRowClockV1:
    record = f"{sequence:064x}"
    return T4TickerRowClockV1(
        f"t4-ref:{record}:{component}", component, "delta", ts_ms, SESSION, sequence, record,
        ts_ms * 1_000_000 - _HOST_LAG_NS, bound_ns, "session-rtt-sample-bracket-max-upper-v1:fixture")


def _mark_dataset(events: Sequence[tuple[Any, ...]], *, number: int = 50,
                  symbol: str = "BTCUSDT") -> T4MarkEvidenceDatasetV1:
    """``events`` are ``(venue_ts_ms, component, price or None[, clock_bound_ns])``."""
    rows, marks = [], []
    for sequence, event in enumerate(sorted(events, key=lambda item: item[0]), start=1):
        row = _ticker(sequence, event[0], event[1], *event[3:])
        rows.append(row)
        if event[1] == _MARK:
            marks.append(T4MarkObservationV1(row, Decimal(event[2]).quantize(MARK_PRICE_QUANTUM_V1)))
    return T4MarkEvidenceDatasetV1(
        dataset_version_id=UUID(int=number), dataset_content_hash=f"{number:064x}", source_id=UUID(int=99),
        session_id=SESSION, instrument="BTCUSDT-PERP", exchange_symbol=symbol,
        segment_clock_evidence_hash="e" * 64, ticker_rows=tuple(rows), marks=tuple(marks))


EXACT_MARK = "65000.25"


def _exact_events(at: int, price: str = EXACT_MARK) -> list[tuple[Any, ...]]:
    return [(at - 500, _INDEX, None), (at - 200, _MARK, "65000.10"), (at, _MARK, price), (at + 700, _INDEX, None)]


def _nearby_events(at: int) -> list[tuple[Any, ...]]:
    return [(at - 500, _INDEX, None), (at - 200, _MARK, "65000.10"), (at + 300, _MARK, "65000.40"),
            (at + 700, _INDEX, None)]


def _marks(events_for: Any = _exact_events, settlements: Sequence[int] = tuple(sorted(RATES)),
           **kwargs: Any) -> T4MarkEvidenceDatasetV1:
    return _mark_dataset([event for at in settlements for event in events_for(at)], **kwargs)


class EvidenceBindingTests(unittest.TestCase):
    def test_complete_funding_and_proven_close_prices_bind_but_stay_not_costable(self) -> None:
        observation = _observe(RATES)
        result = _run([observation])
        self.assertEqual(result.status, EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED)
        self.assertEqual(result.identity["funding_window"]["status"], "COMPLETE")
        self.assertEqual([s["settled_at_ms"] for s in result.settlements], sorted(RATES))
        self.assertEqual([s["payer_direction"] for s in result.settlements],
                         [LONGS_PAY_SHORTS, SHORTS_PAY_LONGS, LONGS_PAY_SHORTS])
        for settlement in result.settlements:
            self.assertEqual(settlement["status"], EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED)
            self.assertEqual(settlement["rate"], RATES[settlement["settled_at_ms"]])  # verbatim text
            price = settlement["price"]
            self.assertEqual(price["evidence_kind"], TRADE_PRICE_PROXY)
            self.assertEqual(price["relation_to_historical_charge_rule"],
                             "DIFFERS_FROM_THE_HISTORICAL_POSITION_VALUE_REFERENCE")
            self.assertEqual(price["bar_open_micros"], settlement["settled_at_ms"] * 1000 - _MINUTE_US)
            self.assertEqual(price["price"], str(Decimal("65001").quantize(PRICE_QUANTUM_V1)))
            self.assertEqual(price["max_clock_bound_nanos"], 350_000_000)
            # The hour holding the minute before the settlement is the previous UTC hour.
            self.assertEqual(price["hour_time_domain"], HOST_ARRIVAL_UTC)
            self.assertEqual([hour["hour_start_utc_nanos"] for hour in price["hours"]],
                             [(settlement["settled_at_ms"] - _H) * 1_000_000])
            self.assertEqual(price["first_arrival_utc_nanos"], price["bar_open_micros"] * 1000 + 1_000_000_000)
            # Funding is known the next day; the price seconds after the instant: funding decides.
            self.assertEqual(settlement["funding_knowledge_at_micros"], observation.knowledge_at_micros)
            self.assertEqual(settlement["funding_observation_id"], str(observation.observation_id))
            self.assertEqual(settlement["evidence_knowable_at_micros"], observation.knowledge_at_micros)
            self.assertNotIn("charge", settlement)
        self.assertIn("PRICE_IS_A_TRADE_PROXY_NOT_THE_VENUE_MARK_PRICE", result.reasons)
        self.assertIn(UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION, result.unresolved_decisions)
        self.assertIn(UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY, result.unresolved_decisions)
        self.assertIn(UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION, result.unresolved_decisions)
        self.assertIn("FORWARD_FUNDING_ACQUISITION:UNRESOLVED_OWNER_AUTHORIZATION", result.unresolved_decisions)

    def test_the_open_convention_matches_the_historical_rule_but_is_still_unapproved(self) -> None:
        result = _run(datasets=[_dataset(_open_bars())], convention=OPEN)
        self.assertEqual(result.status, EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED)
        for settlement in result.settlements:
            self.assertEqual(settlement["price"]["relation_to_historical_charge_rule"],
                             "SAME_SELECTION_AS_THE_HISTORICAL_POSITION_VALUE_REFERENCE")
            self.assertEqual(settlement["price"]["bar_open_micros"], settlement["settled_at_ms"] * 1000)

    def test_hourly_segments_cannot_prove_the_open_at_a_settlement_hour(self) -> None:
        # Hourly segments prove the previous hour's last minute, never the new hour's first.
        result = _run(datasets=[_dataset(_close_bars())], convention=OPEN)
        self.assertEqual(result.status, REFUSED_PRICE_EVIDENCE)
        for settlement in result.settlements:
            self.assertEqual(settlement["status"], PRICE_EVIDENCE_REFUSED)
            self.assertEqual(settlement["price_refusal"], "no_proven_bar_for_the_minute")
            self.assertEqual(settlement["structural_note"], FIRST_MINUTE_OF_AN_HOURLY_SEGMENT_IS_NOT_PROVABLE)
            self.assertIsNone(settlement["price"])
            self.assertIsNone(settlement["evidence_knowable_at_micros"])
        missing_close = _run(datasets=[_dataset(_open_bars())], convention=CLOSE)
        self.assertEqual(missing_close.status, REFUSED_PRICE_EVIDENCE)
        self.assertTrue(all(s["structural_note"] is None for s in missing_close.settlements))

    def test_identical_inputs_give_identical_hashes_in_any_order(self) -> None:
        early, late = _observe(RATES), _observe(RATES, received_ms=RECEIVED + 60_000)
        one = _run([early, late], datasets=[_dataset(_close_bars()[:2], number=1),
                                            _dataset(_close_bars()[2:], number=2)])
        two = _run([late, early], datasets=[_dataset(_close_bars()[2:], number=2),
                                            _dataset(_close_bars()[:2], number=1)])
        self.assertEqual(one.content_hash, two.content_hash)
        self.assertEqual(one.identity, two.identity)
        self.assertNotEqual(one.content_hash, _run([early, late], convention=CLOSE,
                                                   as_of=AS_OF + timedelta(hours=1)).content_hash)
        # The earliest observation that published a record decides its funding knowledge.
        for settlement in one.settlements:
            self.assertEqual(settlement["funding_knowledge_at_micros"], early.knowledge_at_micros)

    def test_price_knowledge_decides_when_it_is_later(self) -> None:
        lag = (RECEIVED - D0) * 1000 + 3_600_000_000  # complete an hour after funding is known
        result = _run(datasets=[_dataset(_close_bars(knowledge_lag_micros=lag))], as_of=AS_OF + timedelta(hours=24))
        first = result.settlements[0]
        self.assertEqual(first["evidence_knowable_at_micros"], first["price"]["knowable_at_micros"])
        self.assertGreater(first["price"]["knowable_at_micros"], first["funding_knowledge_at_micros"])

    def test_the_bridge_never_confirms(self) -> None:
        results = [
            _run(),
            _run(datasets=[_dataset(_open_bars())], convention=OPEN),
            _run(datasets=[_dataset(_close_bars()), _dataset(_open_bars(), number=2)]),
        ]
        for result in results:
            self.assertNotEqual(result.status, CONFIRMED)
            self.assertTrue(all(s["status"] != CONFIRMED for s in result.settlements))
            self.assertTrue(all(s["price"] is None or s["price"]["evidence_kind"] == TRADE_PRICE_PROXY
                                for s in result.settlements))

    def test_payer_direction_is_the_published_rule_on_the_exact_rate_text(self) -> None:
        self.assertEqual(payer_direction_v1("0.0001"), LONGS_PAY_SHORTS)
        self.assertEqual(payer_direction_v1("-0.00005"), SHORTS_PAY_LONGS)
        self.assertEqual(payer_direction_v1("0"), ZERO_RATE_NO_TRANSFER)
        self.assertEqual(payer_direction_v1("-0.0000"), ZERO_RATE_NO_TRANSFER)

    def test_the_contract_names_the_historical_rule_and_the_proxy(self) -> None:
        self.assertEqual(bridge.BRIDGE_CONTRACT_V1["historical_charge_rule"], FUNDING_CHARGE_RULE_V1)
        self.assertEqual(bridge.BRIDGE_CONTRACT_V1["price_evidence_kind"], TRADE_PRICE_PROXY)
        self.assertEqual(bridge.BRIDGE_CONTRACT_V1["charge"], "NOT_CALCULATED_PRICE_CONVENTION_UNAPPROVED")
        self.assertEqual(set(bridge.RELATION_TO_HISTORICAL_CHARGE_RULE_V1), {c.value for c in T4PriceConventionV1})


class FundingRefusalTests(unittest.TestCase):
    def test_a_missing_settlement_refuses_the_window_never_fills_it(self) -> None:
        rates = {at: rate for at, rate in RATES.items() if at != D0 + 8 * _H}
        result = _run([_observe(rates)])
        self.assertEqual(result.status, REFUSED_FUNDING_EVIDENCE)
        self.assertTrue(any(r.startswith("FUNDING_WINDOW:SPACING:") for r in result.reasons))
        self.assertEqual([s["settled_at_ms"] for s in result.settlements], [D0, D0 + 16 * _H])

    def test_an_observation_without_a_clock_bound_is_never_available(self) -> None:
        result = _run([_observe(RATES, bound=None)])
        self.assertEqual(result.status, REFUSED_FUNDING_EVIDENCE)
        self.assertTrue(any(r.startswith("FUNDING_WINDOW:NOT_COVERED:") for r in result.reasons))
        self.assertIn("FUNDING_WINDOW:NO_RECORD_IN_WINDOW", result.reasons)
        self.assertEqual(result.settlements, [])

    def test_funding_not_yet_known_or_window_not_closed_is_refused(self) -> None:
        observation = _observe(RATES)
        known = _EPOCH + timedelta(microseconds=int(observation.knowledge_at_micros or 0))
        self.assertEqual(_run([observation], as_of=known).status,
                         EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED)
        before = _run([observation], as_of=known - timedelta(microseconds=1))
        self.assertEqual(before.status, REFUSED_FUNDING_EVIDENCE)
        self.assertEqual(before.identity["funding_window"]["irregularities"][-1], "NO_RECORD_IN_WINDOW")
        open_window = _run([observation], as_of=DAY0 + timedelta(hours=12))
        self.assertIn("FUNDING_WINDOW:WINDOW_NOT_CLOSED_AS_OF", open_window.reasons)

    def test_missing_interval_evidence_and_rate_conflicts_are_refused(self) -> None:
        self.assertIn("FUNDING_WINDOW:PUBLISHED_INTERVAL_EVIDENCE_MISSING", _run(interval=None).reasons)
        conflicting = dict(RATES)
        conflicting[D0 + 8 * _H] = "-0.00006"
        result = _run([_observe(RATES), _observe(conflicting, received_ms=RECEIVED + 1_000)])
        self.assertEqual(result.status, REFUSED_FUNDING_EVIDENCE)
        self.assertTrue(any(r.startswith("FUNDING_WINDOW:SETTLED_RATE_CONFLICT:") for r in result.reasons))
        self.assertNotIn(D0 + 8 * _H, [s["settled_at_ms"] for s in result.settlements])


class PriceRefusalTests(unittest.TestCase):
    def test_an_ambiguous_boundary_price_is_refused(self) -> None:
        bars = _close_bars()
        bars[1] = replace(bars[1], close_is_sequence_ambiguous=True)
        result = _run(datasets=[_dataset(bars)])
        self.assertEqual(result.status, REFUSED_PRICE_EVIDENCE)
        refused = [s for s in result.settlements if s["status"] == PRICE_EVIDENCE_REFUSED]
        self.assertEqual([s["settled_at_ms"] for s in refused], [D0 + 8 * _H])
        self.assertEqual(refused[0]["price_refusal"], "close_price_is_sequence_ambiguous")
        self.assertIn(f"PRICE_REFUSED:{refused[0]['settled_at']}:close_price_is_sequence_ambiguous", result.reasons)

    def test_a_coverage_gap_hour_refuses_its_settlement_price(self) -> None:
        gap_hour = (D0 + 7 * _H) * 1_000_000
        result = _run(acceptance=_acceptance({gap_hour: HourVerdictV1.UNPROVEN_GAP_EXCEEDS_BOUND}))
        refused = [s for s in result.settlements if s["status"] == PRICE_EVIDENCE_REFUSED]
        self.assertEqual([s["price_refusal"] for s in refused], ["hour_not_complete:UNPROVEN_GAP_EXCEEDS_BOUND"])
        self.assertEqual(result.status, REFUSED_PRICE_EVIDENCE)

    def test_a_price_not_knowable_at_the_decision_instant_is_refused(self) -> None:
        lag = (AS_OF - DAY0) // timedelta(microseconds=1) + 1  # the first price is knowable 1 us after as_of
        result = _run(datasets=[_dataset(_close_bars(knowledge_lag_micros=lag))])
        self.assertEqual({s["price_refusal"] for s in result.settlements}, {"reference_not_yet_knowable_at_known_by"})

    def test_no_price_dataset_refuses_every_settlement(self) -> None:
        result = _run(datasets=[])
        self.assertEqual(result.status, REFUSED_PRICE_EVIDENCE)
        self.assertEqual({s["price_refusal"] for s in result.settlements},
                         {"datasets_must_name_exactly_one_exchange_symbol"})


    def test_a_malformed_hourly_acceptance_refuses_the_price(self) -> None:
        good = _acceptance()
        short = replace(good, symbols=(replace(good.symbols[0], segments=good.symbols[0].segments[:-1]),))
        result = _run(acceptance=short)
        self.assertEqual(result.status, REFUSED_PRICE_EVIDENCE)
        self.assertEqual({s["price_refusal"] for s in result.settlements}, {"hourly_acceptance_malformed"})


class FundingStoreTests(unittest.TestCase):
    def test_only_the_forward_store_is_accepted_as_funding_evidence(self) -> None:
        with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "must_come_from_the_forward_funding_store"):
            bridge_forward_funding_prices_v1(
                [_observe(RATES)], symbol="BTCUSDT", start_ms=D0, end_ms=END, as_of=AS_OF,  # type: ignore[arg-type]
                published_interval=INTERVAL_8H, datasets=[_dataset(_close_bars())], acceptance=_acceptance(),
                convention=CLOSE, mark_evidence=[], mark_nearby_tolerance_ms=None)

    def test_a_stored_record_that_no_longer_reproduces_refuses_the_bridge(self) -> None:
        store = _store([_observe(RATES)])
        root = _ROOTS[-1] / "forward-settled-funding" / "bybit-v5" / "symbol=BTCUSDT" / "observations"
        record = next(root.glob("*.json"))
        stored = json.loads(record.read_text(encoding="utf-8"))
        stored["provenance"]["knowledge_at_micros"] -= 3_600_000_000  # back-date the knowledge claim
        record.write_text(json.dumps(stored, sort_keys=True, indent=1), encoding="utf-8")
        with self.assertRaisesRegex(ForwardFundingError, "does_not_reproduce"):
            bridge_forward_funding_prices_v1(
                store, symbol="BTCUSDT", start_ms=D0, end_ms=END, as_of=AS_OF, published_interval=INTERVAL_8H,
                datasets=[_dataset(_close_bars())], acceptance=_acceptance(), convention=CLOSE,
                mark_evidence=[], mark_nearby_tolerance_ms=None)


class SealedPriceEvidenceTests(_ForwardFixture):
    """The real path: prices from a sealed, raw-replayed fixture segment via ``forward_bars_from_seal_v1``."""

    def _bridge(self, dataset: T4ForwardDatasetBarsV1, convention: T4PriceConventionV1,
                funding_root: str, marks: Sequence[T4MarkEvidenceDatasetV1] = ()) -> ForwardFundingPriceBridgeV1:
        settled = _venue_minute(2)  # a fixture minute, standing in for a settlement instant
        first = (settled - _EPOCH) // timedelta(milliseconds=1)
        rates = {first: "0.0001", first + 8 * _H: "0"}
        start, end = first - 4 * _H, first + 12 * _H
        received = end + 5_000
        rows = [{"symbol": "BTCUSDT", "fundingRate": r, "fundingRateTimestamp": str(at)} for at, r in rates.items()]
        body = json.dumps({"retCode": 0, "retMsg": "OK", "result": {"category": "linear", "list": rows},
                           "retExtInfo": {}, "time": received}).encode()
        observation = observe_forward_settled_funding_v1(
            body, symbol="BTCUSDT", start_ms=start, end_ms=end - 1, observed_at_host_utc_nanos=received * 1_000_000,
            clock_bound=BOUND)
        store = ForwardSettledFundingStoreV1(self.temp / funding_root)
        store.append(observation, body)
        return bridge_forward_funding_prices_v1(
            store, symbol="BTCUSDT", start_ms=start, end_ms=end, as_of=_EPOCH + timedelta(milliseconds=received + 60_000),
            published_interval=PublishedFundingIntervalV1(8, "t4-funding:fixture", start * 1000),
            datasets=[dataset], acceptance=t4_acceptance(dataset.session_id), convention=convention,
            mark_evidence=list(marks), mark_nearby_tolerance_ms=None)

    def test_sealed_ticker_marks_sit_beside_the_sealed_trade_proxy(self) -> None:
        seal = self.seal()
        dataset = forward_bars_from_seal_v1(seal, store=self.store())
        marks = mark_evidence_from_seal_v1(seal, store=self.store())  # the same seal's reference frame
        result = self._bridge(dataset, CLOSE, "funding-marks", marks=[marks])
        bound, missing = result.settlements
        self.assertEqual(bound["price"]["evidence_kind"], TRADE_PRICE_PROXY)
        mark = bound["mark_price"]
        self.assertEqual(mark["evidence_kind"], VENUE_PUBLISHED_MARK_PRICE)
        direct = resolve_settlement_mark_price_v1(
            [marks], symbol="BTCUSDT", settled_at_ms=bound["settled_at_ms"],
            acceptance=t4_acceptance(dataset.session_id), known_by=_venue_minute(60))
        self.assertEqual(mark["resolution_hash"], direct.resolution_hash)
        self.assertEqual(mark["evidence_class"],
                         MARK_EVIDENCE_EXACT if direct.mark_price is not None else MARK_EVIDENCE_NEARBY)
        self.assertEqual(missing["mark_price"]["evidence_class"], MARK_EVIDENCE_REFUSED)
        self.assertEqual(missing["mark_price"]["refusal"], "settlement_instant_not_bracketed_by_one_sealed_segment")
        self.assertEqual(result.identity["mark_evidence_datasets"],
                         [[str(marks.dataset_version_id), marks.dataset_content_hash]])

    def test_sealed_prices_bind_and_missing_bars_refuse(self) -> None:
        dataset = self.dataset()
        for convention, relation in (
            (CLOSE, "DIFFERS_FROM_THE_HISTORICAL_POSITION_VALUE_REFERENCE"),
            (OPEN, "SAME_SELECTION_AS_THE_HISTORICAL_POSITION_VALUE_REFERENCE"),
        ):
            result = self._bridge(dataset, convention, f"funding-{convention.value}")
            self.assertEqual(result.identity["funding_window"]["status"], "COMPLETE")
            self.assertEqual(result.status, REFUSED_PRICE_EVIDENCE)  # the segment ends minutes later
            bound, missing = result.settlements
            self.assertEqual(bound["status"], EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED)
            self.assertEqual(bound["price"]["relation_to_historical_charge_rule"], relation)
            self.assertEqual(bound["price"]["session_id"], str(self.session_id))
            self.assertEqual(missing["price_refusal"], "no_proven_bar_for_the_minute")
            self.assertEqual(missing["payer_direction"], ZERO_RATE_NO_TRANSFER)
            self.assertIsNone(missing["structural_note"])  # 08:12 is not an hour head
            reference = resolve_t4_price_reference_v1(
                [dataset], instant=_venue_minute(2), convention=convention,
                acceptance=t4_acceptance(dataset.session_id), known_by=_venue_minute(60))
            self.assertEqual(bound["price"]["price"], str(reference.price))
            self.assertEqual(bound["price"]["reference_hash"], reference.reference_hash)
            # A second, independently written store of the same evidence gives the same hash.
            again = self._bridge(dataset, convention, f"again-{convention.value}")
            self.assertEqual(result.content_hash, again.content_hash)


class MarkEvidenceTests(unittest.TestCase):
    """Venue mark evidence per settlement, beside -- never instead of -- the trade proxy."""

    def _classes(self, result: ForwardFundingPriceBridgeV1) -> list[str]:
        return [s["mark_price"]["evidence_class"] for s in result.settlements]

    def test_an_exact_timestamp_match_is_labelled_as_not_the_proven_settlement_mark(self) -> None:
        dataset = _marks()
        result = _run(marks=[dataset])
        self.assertEqual(result.status, EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED)
        self.assertEqual(self._classes(result), [MARK_EVIDENCE_EXACT] * 3)
        self.assertIn(EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK, result.reasons)
        self.assertEqual(result.identity["mark_evidence_classes"],
                         {MARK_EVIDENCE_EXACT: 3, MARK_EVIDENCE_NEARBY: 0, MARK_EVIDENCE_REFUSED: 0,
                          MARK_EVIDENCE_UNAVAILABLE: 0})
        for settlement in result.settlements:
            mark, price = settlement["mark_price"], settlement["price"]
            self.assertEqual(settlement["status"], EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED)
            self.assertEqual(price["evidence_kind"], TRADE_PRICE_PROXY)  # the proxy is kept, not replaced
            self.assertEqual(mark["evidence_kind"], VENUE_PUBLISHED_MARK_PRICE)
            self.assertEqual(mark["meaning"], EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK)
            self.assertEqual(mark["mark_price"], str(Decimal(EXACT_MARK).quantize(MARK_PRICE_QUANTUM_V1)))
            self.assertNotEqual(mark["mark_price"], price["price"])
            self.assertEqual(mark["nearby"], [])
            self.assertIn(UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY, mark["unresolved_decisions"])
            self.assertEqual(mark["hour_time_domain"], HOST_ARRIVAL_UTC)
            direct = resolve_settlement_mark_price_v1(
                [dataset], symbol="BTCUSDT", settled_at_ms=settlement["settled_at_ms"], acceptance=_acceptance(),
                known_by=AS_OF)
            self.assertEqual(mark["resolution_hash"], direct.resolution_hash)
            self.assertEqual(mark["knowable_at_micros"], direct.knowable_at_micros)
            # The mark carries its own knowledge; the funding/proxy pair's is unchanged.
            self.assertEqual(settlement["evidence_knowable_at_micros"],
                             max(settlement["funding_knowledge_at_micros"], price["knowable_at_micros"]))
            self.assertNotIn("charge", settlement)
            self.assertNotEqual(settlement["status"], CONFIRMED)

    def test_nearby_marks_are_reported_and_none_is_selected(self) -> None:
        result = _run(marks=[_marks(_nearby_events)])
        self.assertEqual(self._classes(result), [MARK_EVIDENCE_NEARBY] * 3)
        self.assertIn(NEARBY_MARKS_REPORTED_NONE_SELECTED, result.reasons)
        for settlement in result.settlements:
            mark = settlement["mark_price"]
            self.assertIsNone(mark["mark_price"])
            self.assertIsNone(mark["resolution"]["mark_price"])
            self.assertEqual(mark["meaning"], NEARBY_MARKS_REPORTED_NONE_SELECTED)
            self.assertEqual([(n["side"], n["offset_millis"]) for n in mark["nearby"]], [("BEFORE", -200), ("AFTER", 300)])
            self.assertEqual([n["within_caller_tolerance"] for n in mark["nearby"]], [None, None])
            self.assertIsNone(mark["nearby_tolerance_ms"])
            self.assertIn(UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION, mark["unresolved_decisions"])

    def test_a_caller_tolerance_only_restricts_and_is_recorded(self) -> None:
        dataset = _marks(_nearby_events)
        open_ended, within, outside = (_run(marks=[dataset], tolerance=t) for t in (None, 250, 100))
        self.assertEqual(self._classes(within), [MARK_EVIDENCE_NEARBY] * 3)
        for settlement in within.settlements:
            mark = settlement["mark_price"]
            self.assertIsNone(mark["mark_price"])  # within tolerance is still not a selection
            self.assertEqual([n["within_caller_tolerance"] for n in mark["nearby"]], [True, False])
            self.assertEqual(mark["nearby_tolerance_ms"], 250)
        self.assertEqual(self._classes(outside), [MARK_EVIDENCE_REFUSED] * 3)
        self.assertEqual({s["mark_price"]["refusal"] for s in outside.settlements},
                         {NO_NEARBY_MARK_WITHIN_THE_CALLER_TOLERANCE})
        self.assertEqual([within.identity["mark_nearby_tolerance_ms"], outside.identity["mark_nearby_tolerance_ms"]],
                         [250, 100])
        self.assertEqual(len({open_ended.content_hash, within.content_hash, outside.content_hash}), 3)
        # A tolerance never changes an exact match.
        self.assertEqual(self._classes(_run(marks=[_marks()], tolerance=0)), [MARK_EVIDENCE_EXACT] * 3)
        for tolerance in (True, -1, 1.5, "250", Decimal(250)):
            with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "tolerance_must_be_none_or_non_negative"):
                _run(marks=[dataset], tolerance=tolerance)

    def test_no_mark_evidence_is_unavailable_and_changes_no_status(self) -> None:
        result = _run()
        self.assertEqual(result.status, EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED)
        self.assertEqual(self._classes(result), [MARK_EVIDENCE_UNAVAILABLE] * 3)
        for settlement in result.settlements:
            self.assertEqual(settlement["mark_price"]["refusal"], "no_mark_evidence_dataset_supplied")
            self.assertIsNone(settlement["mark_price"]["resolution_hash"])
            self.assertIn(f"MARK_UNAVAILABLE:{settlement['settled_at']}:no_mark_evidence_dataset_supplied",
                          result.reasons)

    def test_resolver_refusals_are_kept_verbatim(self) -> None:
        only_first = _marks(settlements=[D0])
        result = _run(marks=[only_first])
        self.assertEqual(self._classes(result), [MARK_EVIDENCE_EXACT, MARK_EVIDENCE_REFUSED, MARK_EVIDENCE_REFUSED])
        refused = result.settlements[1]["mark_price"]
        self.assertEqual(refused["refusal"], "settlement_instant_not_bracketed_by_one_sealed_segment")
        self.assertIsNone(refused["mark_price"])
        self.assertEqual(result.status, EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED)  # proxy unaffected
        ambiguous = _mark_dataset([*_exact_events(D0), (D0, _MARK, "65009"), *_exact_events(D0 + 8 * _H),
                                   *_exact_events(D0 + 16 * _H)])
        first = _run(marks=[ambiguous]).settlements[0]["mark_price"]
        self.assertEqual(first["refusal"], "ambiguous_ordering_distinct_marks_stamped_at_the_instant")
        twice = _run(marks=[only_first, only_first]).settlements[0]["mark_price"]
        self.assertEqual(twice["refusal"], "dataset_supplied_more_than_once")

    def test_mark_hours_are_host_arrival_hours_not_venue_hours(self) -> None:
        dataset = _marks()
        settled = D0 + 8 * _H
        # The venue hour starting at the settlement is a gap: every cited row arrived before it.
        venue_gap = _acceptance({settled * 1_000_000: HourVerdictV1.UNPROVEN_GAP_EXCEEDS_BOUND})
        mark = _run(marks=[dataset], acceptance=venue_gap).settlements[1]["mark_price"]
        self.assertEqual(mark["evidence_class"], MARK_EVIDENCE_EXACT)
        self.assertEqual([h["hour_start_utc_nanos"] for h in mark["resolution"]["hours"]],
                         [(settled - _H) * 1_000_000])
        # The host-arrival hour the rows landed in is the one that refuses.
        arrival_gap = _acceptance({(settled - _H) * 1_000_000: HourVerdictV1.UNPROVEN_GAP_EXCEEDS_BOUND})
        refused = _run(marks=[dataset], acceptance=arrival_gap).settlements[1]["mark_price"]
        self.assertEqual(refused["refusal"], "hour_not_complete:UNPROVEN_GAP_EXCEEDS_BOUND")

    def test_a_mark_not_knowable_at_the_decision_instant_is_refused(self) -> None:
        last = D0 + 16 * _H
        as_of_us = (AS_OF - _EPOCH) // timedelta(microseconds=1)
        # The row closing the set at the instant is only provably known 1 us after the decision instant.
        late_bound = (as_of_us + 1) * 1000 - ((last + 700) * 1_000_000 - _HOST_LAG_NS)
        events = [*_exact_events(D0), *_exact_events(D0 + 8 * _H), *_exact_events(last)[:-1],
                  (last + 700, _INDEX, None, late_bound)]
        dataset = _mark_dataset(events)
        result = _run(marks=[dataset])
        self.assertEqual(self._classes(result), [MARK_EVIDENCE_EXACT, MARK_EVIDENCE_EXACT, MARK_EVIDENCE_REFUSED])
        self.assertEqual(result.settlements[2]["mark_price"]["refusal"], "mark_evidence_not_yet_knowable_at_known_by")
        self.assertEqual(result.settlements[2]["status"], EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED)
        later = _run(marks=[dataset], as_of=AS_OF + timedelta(microseconds=1))
        self.assertEqual(later.settlements[2]["mark_price"]["evidence_class"], MARK_EVIDENCE_EXACT)
        self.assertEqual(later.settlements[2]["mark_price"]["knowable_at_micros"], as_of_us + 1)

    def test_identities_are_deterministic_and_tampering_shows(self) -> None:
        one, two = _marks(settlements=[D0], number=50), _marks(settlements=[D0 + 8 * _H, D0 + 16 * _H], number=51)
        forward, backward = _run(marks=[one, two]), _run(marks=[two, one])
        self.assertEqual(forward.content_hash, backward.content_hash)
        self.assertEqual(self._classes(forward), [MARK_EVIDENCE_EXACT] * 3)
        for settlement in forward.settlements:
            mark = settlement["mark_price"]
            self.assertEqual(sha256_json_v1(mark["resolution"]), mark["resolution_hash"])  # self-proving
        # A different mark at an instant changes the identity, even with the same dataset id.
        altered = _mark_dataset([*_exact_events(D0, price="65000.26")], number=50)
        changed = _run(marks=[altered, two])
        self.assertNotEqual(changed.content_hash, forward.content_hash)
        self.assertEqual(changed.settlements[0]["mark_price"]["mark_price"],
                         str(Decimal("65000.26").quantize(MARK_PRICE_QUANTUM_V1)))
        # Off-scale prices never reach a hash: the observation refuses them.
        with self.assertRaises(ValueError):
            T4MarkObservationV1(one.marks[0].clock, Decimal("65000.1"))

    def test_trade_evidence_and_foreign_symbols_are_never_mark_evidence(self) -> None:
        bars = _dataset(_close_bars())
        reference = resolve_t4_price_reference_v1([bars], instant=_EPOCH + timedelta(milliseconds=D0),
                                                  convention=CLOSE, acceptance=_acceptance(), known_by=AS_OF)
        for item in (bars, reference):
            with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "trade_price_evidence_is_never_mark"):
                _run(marks=[item])
        with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "must_come_from_a_sealed_reference_frame"):
            _run(marks=[{"markPrice": "65000"}])
        with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "mark_dataset_symbol_differs"):
            _run(marks=[_marks(symbol="ETHUSDT")])
        with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "mark_evidence_must_be_a_declared_sequence"):
            bridge_forward_funding_prices_v1(
                _store([_observe(RATES)]), symbol="BTCUSDT", start_ms=D0, end_ms=END, as_of=AS_OF,
                published_interval=INTERVAL_8H, datasets=[bars], acceptance=_acceptance(), convention=CLOSE,
                mark_evidence=None, mark_nearby_tolerance_ms=None)  # type: ignore[arg-type]

    def test_the_contract_records_the_mark_path_and_its_limits(self) -> None:
        contract = bridge.BRIDGE_CONTRACT_V1
        self.assertEqual(contract["mark_price_evidence_kind"], VENUE_PUBLISHED_MARK_PRICE)
        self.assertEqual(contract["mark_exact_meaning"], EXACT_TIMESTAMP_MATCH_IS_NOT_THE_PROVEN_SETTLEMENT_MARK)
        self.assertEqual(contract["mark_evidence_classes"],
                         [MARK_EVIDENCE_EXACT, MARK_EVIDENCE_NEARBY, MARK_EVIDENCE_REFUSED, MARK_EVIDENCE_UNAVAILABLE])
        self.assertEqual(contract["price_hour_time_domain"], HOST_ARRIVAL_UTC)
        self.assertEqual(contract["charge"], "NOT_CALCULATED_PRICE_CONVENTION_UNAPPROVED")
        params = inspect.signature(bridge_forward_funding_prices_v1).parameters
        for name in ("mark_evidence", "mark_nearby_tolerance_ms"):
            self.assertIs(params[name].default, inspect.Parameter.empty, name)  # no default


class CallerErrorTests(unittest.TestCase):
    def test_symbols_must_agree_and_stay_in_the_universe(self) -> None:
        with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "differs_from_the_funding_symbol"):
            _run(datasets=[_dataset(_close_bars(), symbol="ETHUSDT")])
        with self.assertRaisesRegex(ForwardFundingError, "outside_the_universe"):
            _run(symbol="DOGEUSDT")

    def test_convention_and_decision_instant_must_be_explicit(self) -> None:
        for convention in (None, CLOSE.value):
            with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "price_convention_must_be_declared"):
                _run(convention=convention)
        with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "as_of_must_be_an_aware_utc_instant"):
            _run(as_of=AS_OF.replace(tzinfo=None))

    def test_only_the_unresolved_approval_exists_and_a_holdout_opening_is_not_one(self) -> None:
        approval = resolve_funding_price_convention_approval_v1()
        self.assertEqual(approval.state, UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION)
        self.assertEqual(_run(approval=approval).content_hash, _run().content_hash)
        for state in ("APPROVED", CONFIRMED, ""):
            with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "not_defined_in_v1"):
                FundingPriceConventionApprovalV1(state, ())
        opening = validation.HoldoutOpeningV1("cycle-2026-08-20", datetime(2026, 8, 20, tzinfo=UTC),
                                              datetime(2026, 9, 1, tzinfo=UTC), "q" * 64, "test",
                                              validation._REGISTRY_ISSUER)
        with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "holdout_opening_is_not"):
            _run(approval=opening)
        with self.assertRaisesRegex(ForwardFundingPriceBridgeError, "must_be_the_resolved_approval"):
            _run(approval="APPROVED")

    def test_the_module_has_no_network_or_historical_acquisition_path(self) -> None:
        tree = ast.parse(inspect.getsource(bridge))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom | ast.Import)
                    for alias in node.names}
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        for forbidden in ("urllib", "http", "socket", "requests"):
            self.assertFalse(any(str(m).split(".")[0] == forbidden for m in modules), forbidden)
        for name in ("acquire_funding_history_v1", "load_funding_history_v1", "urllib_funding_fetch_v1",
                     "acquire_forward_settled_funding_v1"):
            self.assertNotIn(name, imported)


if __name__ == "__main__":
    unittest.main()
