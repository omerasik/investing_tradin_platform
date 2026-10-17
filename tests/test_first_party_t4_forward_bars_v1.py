"""Forward paper incubation -- proven T4 bars and instant price references.

Every record is a FIXTURE written under a temporary archive root by the R3A
fixture archive (a host 10 s behind the venue, clock samples every 60 s, a
trade message every 1.5 s). The hourly acceptance objects are built here by
hand: the fixture covers minutes, not hours, and the acceptance rules have
their own tests.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from tests.test_first_party_t4_v1 import (
    BASE,
    OFFSET,
    RESOLUTION,
    RTT,
    SECOND,
    _standard_samples,
    _TempRoots,
)
from trade_platform.first_party_capture_hourly_acceptance_v1 import (
    HOUR_NANOS,
    HourlyAcceptanceV1,
    HourlySegmentV1,
    HourVerdictV1,
    SymbolHoursV1,
)
from trade_platform.first_party_t4_forward_bars_v1 import (
    PRICE_QUANTUM_V1,
    T4ForwardBarError,
    T4ForwardDatasetBarsV1,
    T4PriceConventionV1,
    T4PriceReferenceRefusalV1,
    forward_bars_from_seal_v1,
    forward_bars_from_segment_output_v1,
    resolve_t4_price_reference_v1,
)
from trade_platform.first_party_t4_seal_v1 import (
    discover_t4_segments_v1,
    normalize_t4_segment_v1,
    restore_t4_seal_without_replay_v1,
    seal_t4_segment_v1,
)

OPEN = T4PriceConventionV1.FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT
CLOSE = T4PriceConventionV1.LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT
SEALED_AT = datetime(2026, 9, 21, tzinfo=UTC)
LATER = datetime(2026, 9, 22, tzinfo=UTC)
HOUR = BASE // HOUR_NANOS * HOUR_NANOS


def _venue_minute(minutes_after_ten: int) -> datetime:
    """A venue-clock minute boundary; the fixture's venue time starts near 00:10:10."""
    return datetime(2026, 9, 20, 0, 10, tzinfo=UTC) + timedelta(minutes=minutes_after_ten)


def _micros(value: datetime) -> int:
    return (value - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)


def _acceptance(
    session: UUID | None,
    *,
    verdict: HourVerdictV1 = HourVerdictV1.COMPLETE,
    symbol: str = "BTCUSDT",
    hours_judged: int = 1,
) -> HourlyAcceptanceV1:
    segment = HourlySegmentV1(HOUR, verdict, session_id=session, unproven_nanos=0)
    return HourlyAcceptanceV1(
        required_consecutive_hours=24,
        max_unproven_nanos=30 * SECOND,
        first_hour_start_utc_nanos=HOUR,
        hours_judged=hours_judged,
        symbols=(SymbolHoursV1(symbol, (segment,) if hours_judged else ()),),
        joint_complete=(verdict is HourVerdictV1.COMPLETE,) if hours_judged else (),
        runs=(),
    )


class _ForwardFixture(_TempRoots):
    def seal(self, store: str = "research", sealed_at: datetime = SEALED_AT, **session: Any) -> Any:
        session.setdefault("windows", [(BASE, BASE + 240 * SECOND)])
        session.setdefault("samples", _standard_samples())
        self.session_id = self.archive.session(**session)
        plans = discover_t4_segments_v1(self.capture).segments
        self.assertEqual(len(plans), 1)
        self.plan = plans[0]
        return seal_t4_segment_v1(self.plan, store=self.store(store), sealed_at=sealed_at)

    def dataset(self, **session: Any) -> T4ForwardDatasetBarsV1:
        seal = self.seal(**session)
        return forward_bars_from_seal_v1(seal, store=self.store())

    def resolve(
        self,
        dataset: T4ForwardDatasetBarsV1 | list[T4ForwardDatasetBarsV1],
        instant: Any,
        convention: Any,
        *,
        acceptance: HourlyAcceptanceV1 | None = None,
        known_by: datetime = LATER,
    ) -> Any:
        datasets = dataset if isinstance(dataset, list) else [dataset]
        return resolve_t4_price_reference_v1(
            datasets,
            instant=instant,
            convention=convention,
            acceptance=_acceptance(datasets[0].session_id) if acceptance is None else acceptance,
            known_by=known_by,
        )


class SealedBarTests(_ForwardFixture):
    def test_only_minutes_the_segment_proved_have_bars_with_exact_frame_scale_prices(self) -> None:
        dataset = self.dataset()
        opens = [bar.bar_open_at for bar in dataset.bars]
        # 00:10 is the segment's first trade minute (no open proof); 00:14 is still
        # open when the segment ends (no close proof). Nothing is filled for either.
        self.assertEqual(opens, [_venue_minute(1), _venue_minute(2), _venue_minute(3)])
        for bar in dataset.bars:
            self.assertEqual(bar.open_price.as_tuple().exponent, PRICE_QUANTUM_V1.as_tuple().exponent)
            self.assertGreaterEqual(bar.complete_market_knowledge_micros, bar.bar_close_micros)
            self.assertLessEqual(bar.open_market_knowledge_micros, bar.complete_market_knowledge_micros)
            self.assertEqual(bar.clock.max_clock_bound_nanos, OFFSET + RTT // 2 + RESOLUTION)
            self.assertTrue(bar.clock.clock_bound_evidence)
            for evidence in bar.clock.clock_bound_evidence:
                self.assertTrue(evidence.startswith("session-rtt-sample-bracket-max-upper-v1:prior="))
        self.assertEqual(dataset.session_id, self.session_id)
        self.assertEqual(dataset.exchange_symbol, "BTCUSDT")

    def test_live_normalization_and_sealed_replay_give_identical_bars(self) -> None:
        seal = self.seal()
        replayed = forward_bars_from_seal_v1(seal, store=self.store())
        output, _, _ = normalize_t4_segment_v1(self.plan)
        live = forward_bars_from_segment_output_v1(output, exchange_symbol="BTCUSDT")
        self.assertEqual([bar.bar_content_hash for bar in live], [bar.bar_content_hash for bar in replayed.bars])
        self.assertEqual(live, replayed.bars)

    def test_a_seal_restored_without_raw_replay_backs_nothing(self) -> None:
        seal = self.seal()
        restored = restore_t4_seal_without_replay_v1(
            seal.identity, frame_manifests=seal.frame_manifests, sealed_at=seal.sealed_at,
            store=self.store(),
        )
        with self.assertRaisesRegex(T4ForwardBarError, "rebuilt_from_raw_capture"):
            forward_bars_from_seal_v1(restored, store=self.store())

    def test_a_tampered_sealed_frame_is_refused(self) -> None:
        seal = self.seal()
        store = self.store()
        manifest = store.load_manifest(seal.frame_manifests["T4_OHLCV_1M"])
        path = store.object_path(manifest.objects[0])
        path.write_bytes(path.read_bytes()[:-8] + b"\x00" * 8)
        with self.assertRaises(ValueError):
            forward_bars_from_seal_v1(seal, store=store)

    def test_knowledge_and_counts_that_do_not_rederive_are_refused(self) -> None:
        self.seal()
        output, _, _ = normalize_t4_segment_v1(self.plan)
        bar = output.bars[0]
        output.bars[0] = replace(bar, complete_market_knowledge_micros=bar.complete_market_knowledge_micros - 1)
        with self.assertRaisesRegex(T4ForwardBarError, "completion_knowledge_does_not_rederive"):
            forward_bars_from_segment_output_v1(output, exchange_symbol="BTCUSDT")
        output.bars[0] = replace(bar, open_market_knowledge_micros=bar.open_market_knowledge_micros + 1)
        with self.assertRaisesRegex(T4ForwardBarError, "open_knowledge_does_not_rederive"):
            forward_bars_from_segment_output_v1(output, exchange_symbol="BTCUSDT")
        output.bars[0] = replace(bar, trade_count=bar.trade_count + 1)
        with self.assertRaisesRegex(T4ForwardBarError, "trade_count_does_not_rederive"):
            forward_bars_from_segment_output_v1(output, exchange_symbol="BTCUSDT")


class PriceReferenceTests(_ForwardFixture):
    def test_close_convention_is_the_last_trade_before_the_instant(self) -> None:
        dataset = self.dataset()
        output, _, _ = normalize_t4_segment_v1(self.plan)
        instant = _venue_minute(2)
        reference = self.resolve(dataset, instant, CLOSE)
        before = [t for t in output.trades if t.trade_ts_millis * 1_000 < _micros(instant)]
        last = max(before, key=lambda t: t.ordering_key)
        self.assertEqual(reference.price, last.price.quantize(PRICE_QUANTUM_V1))
        self.assertEqual(reference.bar.bar_open_at, _venue_minute(1))
        self.assertEqual(reference.knowable_at, reference.bar.complete_market_knowledge_at)
        self.assertGreater(reference.knowable_at, instant)  # knowable only after the instant
        self.assertEqual(reference.dataset_content_hash, dataset.dataset_content_hash)
        self.assertEqual(reference.hour.session_id, self.session_id)

    def test_open_convention_is_the_first_trade_at_or_after_the_instant_not_the_rest_open(self) -> None:
        dataset = self.dataset()
        output, _, _ = normalize_t4_segment_v1(self.plan)
        instant = _venue_minute(2)
        reference = self.resolve(dataset, instant, OPEN)
        at_or_after = [t for t in output.trades if t.trade_ts_millis * 1_000 >= _micros(instant)]
        before = [t for t in output.trades if t.trade_ts_millis * 1_000 < _micros(instant)]
        first = min(at_or_after, key=lambda t: t.ordering_key)
        previous = max(before, key=lambda t: t.ordering_key)
        self.assertEqual(reference.price, first.price.quantize(PRICE_QUANTUM_V1))
        # The fixture's prices differ trade to trade, so the REST-style open (the
        # previous trade's price) is a different number: the convention matters.
        self.assertNotEqual(reference.price, previous.price.quantize(PRICE_QUANTUM_V1))
        self.assertNotEqual(
            reference.reference_hash, self.resolve(dataset, instant, CLOSE).reference_hash
        )

    def test_same_sealed_inputs_give_identical_reference_hashes(self) -> None:
        first = self.dataset()
        again = forward_bars_from_seal_v1(
            seal_t4_segment_v1(self.plan, store=self.store("other"), sealed_at=datetime(2027, 1, 1, tzinfo=UTC)),
            store=self.store("other"),
        )
        self.assertEqual(first, again)
        for convention in (OPEN, CLOSE):
            one = self.resolve(first, _venue_minute(2), convention)
            two = self.resolve(again, _venue_minute(2), convention, known_by=datetime(2030, 1, 1, tzinfo=UTC))
            self.assertEqual(one.reference_hash, two.reference_hash)
            self.assertEqual(one, two)

    def test_a_minute_without_a_proven_bar_is_refused_never_filled(self) -> None:
        dataset = self.dataset()
        for instant, convention in (
            (_venue_minute(0), OPEN),    # first trade minute: no open proof
            (_venue_minute(5), CLOSE),   # 00:14 still open at the segment end
            (_venue_minute(4), OPEN),
            (_venue_minute(30), CLOSE),  # outside the segment
        ):
            with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
                self.resolve(dataset, instant, convention)
            self.assertEqual(caught.exception.code, "no_proven_bar_for_the_minute")

    def test_the_convention_must_be_declared_and_the_instant_exact(self) -> None:
        dataset = self.dataset()
        cases = (
            (_venue_minute(2), OPEN.value, "price_convention_must_be_declared"),
            (_venue_minute(2), None, "price_convention_must_be_declared"),
            (_venue_minute(2) + timedelta(seconds=1), OPEN, "instant_not_on_the_minute_grid"),
            (_venue_minute(2).replace(tzinfo=None), OPEN, "instant_must_be_an_aware_utc_instant"),
        )
        for instant, convention, code in cases:
            with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
                self.resolve(dataset, instant, convention)
            self.assertEqual(caught.exception.code, code)

    def test_a_non_complete_unjudged_or_foreign_hour_is_refused(self) -> None:
        dataset = self.dataset()
        cases = (
            (_acceptance(self.session_id, verdict=HourVerdictV1.COVERAGE_ENDS_BEFORE_HOUR_END),
             "hour_not_complete:COVERAGE_ENDS_BEFORE_HOUR_END"),
            (_acceptance(self.session_id, verdict=HourVerdictV1.UNPROVEN_GAP_EXCEEDS_BOUND),
             "hour_not_complete:UNPROVEN_GAP_EXCEEDS_BOUND"),
            (_acceptance(self.session_id, hours_judged=0), "hour_not_judged"),
            (_acceptance(self.session_id, symbol="ETHUSDT"), "hourly_acceptance_does_not_cover_the_symbol"),
            (_acceptance(UUID(int=7)), "hour_proven_by_another_capture_session"),
        )
        for acceptance, code in cases:
            with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
                self.resolve(dataset, _venue_minute(2), CLOSE, acceptance=acceptance)
            self.assertEqual(caught.exception.code, code)

    def test_a_malformed_hourly_acceptance_is_refused_not_an_index_error(self) -> None:
        dataset = self.dataset()
        complete = _acceptance(self.session_id)
        segment = complete.symbols[0].segments[0]
        cases = (
            replace(complete, symbols=(SymbolHoursV1("BTCUSDT", ()),)),             # hour judged, no segment
            replace(complete, symbols=(SymbolHoursV1("BTCUSDT", (segment, segment)),)),  # extra segment
            replace(complete, symbols=(SymbolHoursV1("BTCUSDT", (replace(segment, hour_start_utc_nanos=0),)),)),
        )
        for acceptance in cases:
            with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
                self.resolve(dataset, _venue_minute(2), CLOSE, acceptance=acceptance)
            self.assertEqual(caught.exception.code, "hourly_acceptance_malformed")

    def test_a_reference_not_yet_knowable_is_refused(self) -> None:
        dataset = self.dataset()
        reference = self.resolve(dataset, _venue_minute(2), CLOSE)
        self.resolve(dataset, _venue_minute(2), CLOSE, known_by=reference.knowable_at)
        with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
            self.resolve(
                dataset, _venue_minute(2), CLOSE,
                known_by=reference.knowable_at - timedelta(microseconds=1),
            )
        self.assertEqual(caught.exception.code, "reference_not_yet_knowable_at_known_by")
        with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
            self.resolve(dataset, _venue_minute(2), CLOSE, known_by=LATER.replace(tzinfo=None))
        self.assertEqual(caught.exception.code, "known_by_must_be_an_aware_utc_instant")

    def test_sequence_ambiguous_open_and_close_are_refused(self) -> None:
        def ambiguous(sequence: int, payload: str) -> str:
            message = json.loads(payload)
            if message["topic"].startswith("publicTrade"):
                # Both entries of a message share (T, seq); give them two prices.
                message["data"][1]["p"] = str(Decimal(message["data"][0]["p"]) + 1)
            return json.dumps(message, separators=(",", ":"))

        dataset = self.dataset(tamper=ambiguous)
        bar = dataset.bar_at(_micros(_venue_minute(2)))
        assert bar is not None
        self.assertTrue(bar.open_is_sequence_ambiguous)
        for convention, code in (
            (OPEN, "open_price_is_sequence_ambiguous"),
            (CLOSE, "close_price_is_sequence_ambiguous"),
        ):
            with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
                self.resolve(dataset, _venue_minute(2), convention)
            self.assertEqual(caught.exception.code, code)

    def test_a_minute_proven_by_two_datasets_is_refused_not_chosen(self) -> None:
        first = self.dataset()
        self.archive.session(windows=[(BASE, BASE + 240 * SECOND)], samples=_standard_samples())
        plans = discover_t4_segments_v1(self.capture).segments
        self.assertEqual(len(plans), 2)
        other_plan = next(plan for plan in plans if plan.partition.session_id != self.session_id)
        second = forward_bars_from_seal_v1(
            seal_t4_segment_v1(other_plan, store=self.store(), sealed_at=SEALED_AT), store=self.store()
        )
        self.assertNotEqual(first.dataset_version_id, second.dataset_version_id)
        with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
            self.resolve([first, second], _venue_minute(2), CLOSE)
        self.assertEqual(caught.exception.code, "minute_proven_by_more_than_one_dataset")
        with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
            self.resolve([], _venue_minute(2), CLOSE, acceptance=_acceptance(self.session_id))
        self.assertEqual(caught.exception.code, "datasets_must_name_exactly_one_exchange_symbol")


NEXT_HOUR = HOUR + HOUR_NANOS  # 01:00:00Z, host (arrival) clock


def _two_hours(
    first: tuple[HourVerdictV1, UUID | None],
    second: tuple[HourVerdictV1, UUID | None] | None,
) -> HourlyAcceptanceV1:
    """Acceptance judging 00:00Z and, unless ``second`` is ``None``, 01:00Z."""
    verdicts = [first] if second is None else [first, second]
    segments = tuple(
        HourlySegmentV1(
            HOUR + index * HOUR_NANOS, verdict, session_id=session,
            unproven_nanos=0 if session is not None else None,
        )
        for index, (verdict, session) in enumerate(verdicts)
    )
    return HourlyAcceptanceV1(
        required_consecutive_hours=24,
        max_unproven_nanos=30 * SECOND,
        first_hour_start_utc_nanos=HOUR,
        hours_judged=len(segments),
        symbols=(SymbolHoursV1("BTCUSDT", segments),),
        joint_complete=tuple(segment.complete for segment in segments),
        runs=(),
    )


class ClockDomainTests(_ForwardFixture):
    """Bars are minutes of *venue event* time; hour verdicts judge *host arrival* time.

    The fixture host runs ~9.94 s behind the venue (the real 2026-09-24 host was
    ~9.47 s behind), so the trades of the venue minute starting at 01:00Z
    arrive from about 00:59:50Z host time: in the *previous* capture hour.
    """

    def straddling_dataset(self) -> T4ForwardDatasetBarsV1:
        start = NEXT_HOUR - 150 * SECOND  # host 00:57:30Z .. 01:01:30Z
        samples = [start - 5 * SECOND + step * 60 * SECOND for step in range(6)]
        return self.dataset(windows=[(start, start + 240 * SECOND)], samples=samples)

    def test_the_bar_after_the_hour_has_records_that_arrived_before_it(self) -> None:
        dataset = self.straddling_dataset()
        output, _, _ = normalize_t4_segment_v1(self.plan)
        bar_open = _micros(datetime(2026, 9, 20, 1, 0, tzinfo=UTC))
        self.assertIsNotNone(dataset.bar_at(bar_open))
        arrivals = [
            trade.arrival.arrival_utc_nanos for trade in output.trades
            if bar_open <= trade.trade_ts_millis * 1_000 < bar_open + 60_000_000
        ]
        # Event time in hour 01; the first arrivals in hour 00.
        self.assertLess(min(arrivals), NEXT_HOUR)
        self.assertGreaterEqual(max(arrivals), NEXT_HOUR)

    def test_an_incomplete_arrival_hour_holding_the_bars_records_is_refused(self) -> None:
        dataset = self.straddling_dataset()
        instant = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)
        # 01:00Z is COMPLETE, but 00:00Z -- where this bar's first records arrived -- is not.
        acceptance = _two_hours(
            (HourVerdictV1.HEAD_UNPROVEN_EXCEEDS_BOUND, None), (HourVerdictV1.COMPLETE, self.session_id)
        )
        with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
            self.resolve(dataset, instant, OPEN, acceptance=acceptance)
        self.assertEqual(caught.exception.code, "hour_not_complete:HEAD_UNPROVEN_EXCEEDS_BOUND")

    def test_an_arrival_hour_proven_by_another_session_is_refused(self) -> None:
        dataset = self.straddling_dataset()
        acceptance = _two_hours(
            (HourVerdictV1.COMPLETE, UUID(int=7)), (HourVerdictV1.COMPLETE, self.session_id)
        )
        with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
            self.resolve(dataset, datetime(2026, 9, 20, 1, 0, tzinfo=UTC), OPEN, acceptance=acceptance)
        self.assertEqual(caught.exception.code, "hour_proven_by_another_capture_session")

    def test_a_bar_spanning_two_complete_arrival_hours_names_both(self) -> None:
        dataset = self.straddling_dataset()
        acceptance = _two_hours(
            (HourVerdictV1.COMPLETE, self.session_id), (HourVerdictV1.COMPLETE, self.session_id)
        )
        reference = self.resolve(dataset, datetime(2026, 9, 20, 1, 0, tzinfo=UTC), OPEN, acceptance=acceptance)
        self.assertEqual(
            [hour.hour_start_utc_nanos for hour in reference.hours], [HOUR, NEXT_HOUR]
        )
        self.assertEqual(reference.hour.hour_start_utc_nanos, HOUR)
        clock = reference.bar.clock
        self.assertLess(clock.first_arrival_utc_nanos, NEXT_HOUR)
        self.assertGreaterEqual(clock.last_arrival_utc_nanos, NEXT_HOUR)
        self.assertEqual(
            reference.payload()["hours"], [hour.payload() for hour in reference.hours]
        )
        # Knowledge never precedes the bounded arrival of the evidence it rests on.
        self.assertGreaterEqual(
            reference.bar.complete_market_knowledge_micros * 1_000,
            clock.last_arrival_utc_nanos + clock.min_clock_bound_nanos,
        )

    def test_a_bar_whose_records_all_arrived_in_one_hour_needs_only_that_hour(self) -> None:
        dataset = self.straddling_dataset()
        # The venue minute 00:59 ends at the hour, but its records -- the closing one
        # included -- all arrived before 01:00Z host time; 01:00Z is not judged yet.
        acceptance = _two_hours((HourVerdictV1.COMPLETE, self.session_id), None)
        reference = self.resolve(dataset, datetime(2026, 9, 20, 1, 0, tzinfo=UTC), CLOSE, acceptance=acceptance)
        self.assertEqual([hour.hour_start_utc_nanos for hour in reference.hours], [HOUR])
        # The bar after the hour cannot be judged against a single hour.
        with self.assertRaises(T4PriceReferenceRefusalV1) as caught:
            self.resolve(dataset, datetime(2026, 9, 20, 1, 0, tzinfo=UTC), OPEN, acceptance=acceptance)
        self.assertEqual(caught.exception.code, "hour_not_judged")


if __name__ == "__main__":
    unittest.main()
