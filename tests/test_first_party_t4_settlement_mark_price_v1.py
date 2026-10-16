"""OR-6 F3 forward -- venue-published mark price observations around a funding settlement instant.

Every record is a FIXTURE written under a temporary archive root by the R3A
fixture archive (a host 10 s behind the venue, a record every 0.5 s host time,
ticker ``ts`` = venue time - 50 ms). Record ``k`` of a window starting at
``BASE`` is stamped 00:10:09.950 + 0.5 s * k; ``k % 3 == 0`` carries a mark,
``k % 3 == 1`` an index, ``k % 3 == 2`` trades. So 00:11:00.000 falls between
the index of ``k = 100`` (00:10:59.950) and the mark of ``k = 102``
(00:11:00.950); the mark of ``k = 99`` (00:10:59.450) is the last before it.
Hourly acceptance objects are built by hand, as in the forward-bar tests.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from tests.test_first_party_t4_forward_bars_v1 import LATER, SEALED_AT, _acceptance
from tests.test_first_party_t4_v1 import (
    BASE,
    OFFSET,
    RESOLUTION,
    RTT,
    SECOND,
    _standard_samples,
    _TempRoots,
)
from trade_platform.first_party_capture_hourly_acceptance_v1 import HourVerdictV1
from trade_platform.first_party_t4_forward_bars_v1 import forward_bars_from_seal_v1
from trade_platform.first_party_t4_normalization_v1 import COMPONENT_INDEX
from trade_platform.first_party_t4_seal_v1 import (
    discover_t4_segments_v1,
    normalize_t4_segment_v1,
    restore_t4_seal_without_replay_v1,
    seal_t4_segment_v1,
)
from trade_platform.first_party_t4_settlement_mark_price_v1 import (
    PRICE_QUANTUM_V1,
    UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION,
    UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY,
    VENUE_PUBLISHED_MARK_PRICE,
    T4MarkEvidenceDatasetV1,
    T4MarkEvidenceError,
    T4MarkObservationV1,
    T4MarkPriceRefusalV1,
    T4SettlementMarkStatusV1,
    mark_evidence_from_seal_v1,
    mark_observation_from_reference_v1,
    mark_observations_from_segment_output_v1,
    resolve_settlement_mark_price_v1,
)

EXACT = T4SettlementMarkStatusV1.EXACT_VENUE_TIMESTAMP_MATCH
NEARBY = T4SettlementMarkStatusV1.NEARBY_ONLY_UNRESOLVED_SELECTION_CONVENTION
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _ms(value: datetime) -> int:
    return (value - _EPOCH) // timedelta(milliseconds=1)


SETTLEMENT = _ms(datetime(2026, 9, 20, 0, 11, tzinfo=UTC))


def _venue_ms(sequence: int, window_start: int = BASE) -> int:
    return (window_start + sequence * SECOND // 2 + OFFSET) // 1_000_000 - 50


def _retime(changes: dict[int, dict[str, Any]]) -> Any:
    """A tamper hook that rewrites the ticker message of the given record sequences."""

    def tamper(sequence: int, payload: str) -> str:
        if sequence not in changes:
            return payload
        message = json.loads(payload)
        change = dict(changes[sequence])
        if "ts" in change:
            message["ts"] = change.pop("ts")
        for name, value in change.items():
            if value is None:
                message["data"].pop(name, None)
            else:
                message["data"][name] = value
        return json.dumps(message, separators=(",", ":"))

    return tamper


class _MarkFixture(_TempRoots):
    def session(self, **session: Any) -> UUID:
        session.setdefault("windows", [(BASE, BASE + 240 * SECOND)])
        session.setdefault("samples", _standard_samples())
        return self.archive.session(**session)

    def sealed(self, store: str = "research") -> list[T4MarkEvidenceDatasetV1]:
        datasets = []
        for plan in discover_t4_segments_v1(self.capture).segments:
            seal = seal_t4_segment_v1(plan, store=self.store(store), sealed_at=SEALED_AT)
            datasets.append(mark_evidence_from_seal_v1(seal, store=self.store(store)))
        return datasets

    def resolve(
        self,
        datasets: list[Any],
        settled_at_ms: Any = SETTLEMENT,
        *,
        session: UUID | None = None,
        acceptance: Any = None,
        known_by: datetime = LATER,
        symbol: str = "BTCUSDT",
    ) -> Any:
        if acceptance is None:
            acceptance = _acceptance(datasets[0].session_id if session is None else session)
        return resolve_settlement_mark_price_v1(
            datasets, symbol=symbol, settled_at_ms=settled_at_ms, acceptance=acceptance, known_by=known_by,
        )

    def refused(self, code: str, *args: Any, **kwargs: Any) -> None:
        with self.assertRaises(T4MarkPriceRefusalV1) as caught:
            self.resolve(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)


class MarkEvidenceTests(_MarkFixture):
    def test_sealed_marks_carry_separate_clocks_and_exact_frame_scale_prices(self) -> None:
        session = self.session()
        (dataset,) = self.sealed()
        self.assertEqual(dataset.session_id, session)
        self.assertEqual(dataset.exchange_symbol, "BTCUSDT")
        self.assertTrue(dataset.marks)
        for mark in dataset.marks:
            self.assertEqual(mark.evidence_kind, VENUE_PUBLISHED_MARK_PRICE)
            self.assertEqual(mark.price.as_tuple().exponent, PRICE_QUANTUM_V1.as_tuple().exponent)
            clock = mark.clock
            self.assertEqual(clock.clock_bound_nanos, OFFSET + RTT // 2 + RESOLUTION)
            self.assertTrue(clock.clock_bound_evidence.startswith("session-rtt-sample-bracket-max-upper-v1:"))
            # event, arrival and knowledge are kept apart; knowledge is the upper bound.
            self.assertGreaterEqual(clock.market_knowledge_micros * 1_000, clock.arrival_utc_nanos + OFFSET)
            self.assertGreaterEqual(clock.market_knowledge_micros, clock.exchange_ts_millis * 1_000)
        components = {row.component for row in dataset.ticker_rows}
        self.assertEqual(components, {"MARK", "INDEX"})

    def test_live_normalization_and_sealed_replay_give_identical_mark_observations(self) -> None:
        self.session()
        (dataset,) = self.sealed()
        (plan,) = discover_t4_segments_v1(self.capture).segments
        output, _, _ = normalize_t4_segment_v1(plan)
        live = mark_observations_from_segment_output_v1(output)
        self.assertEqual(live, dataset.marks)
        self.assertEqual([item.payload() for item in live], [item.payload() for item in dataset.marks])

    def test_an_index_or_trade_is_never_a_mark_observation(self) -> None:
        self.session()
        (plan,) = discover_t4_segments_v1(self.capture).segments
        output, _, _ = normalize_t4_segment_v1(plan)
        index = next(item for item in output.reference_prices if item.component == COMPONENT_INDEX)
        with self.assertRaisesRegex(T4MarkEvidenceError, "requires_a_mark_reference_row"):
            mark_observation_from_reference_v1(index)
        with self.assertRaisesRegex(T4MarkEvidenceError, "requires_a_mark_reference_row"):
            mark_observation_from_reference_v1(output.trades[0])  # type: ignore[arg-type]
        mark = mark_observations_from_segment_output_v1(output)[0]
        with self.assertRaisesRegex(T4MarkEvidenceError, "requires_a_mark_reference_row"):
            T4MarkObservationV1(replace(mark.clock, component=COMPONENT_INDEX), mark.price)
        with self.assertRaisesRegex(T4MarkEvidenceError, "not_at_frame_scale"):
            T4MarkObservationV1(mark.clock, Decimal("84000.1"))

    def test_the_bridge_names_the_same_evidence_kind_and_still_has_no_mark_path(self) -> None:
        from trade_platform import forward_funding_price_bridge_v1 as bridge

        self.assertEqual(bridge.VENUE_PUBLISHED_MARK_PRICE, VENUE_PUBLISHED_MARK_PRICE)
        self.assertEqual(bridge.BRIDGE_CONTRACT_V1["price_evidence_kind"], bridge.TRADE_PRICE_PROXY)
        self.assertEqual(bridge.BRIDGE_CONTRACT_V1["mark_price_evidence"], "NO_PROVEN_FORWARD_MARK_PRICE_RESOLVER_V1")

    def test_trade_price_evidence_is_refused_by_the_resolver(self) -> None:
        session = self.session()
        (plan,) = discover_t4_segments_v1(self.capture).segments
        seal = seal_t4_segment_v1(plan, store=self.store(), sealed_at=SEALED_AT)
        bars = forward_bars_from_seal_v1(seal, store=self.store())
        self.refused("trade_price_evidence_is_never_mark_price_evidence", [bars], session=session)

    def test_a_seal_restored_without_raw_replay_backs_nothing(self) -> None:
        self.session()
        (plan,) = discover_t4_segments_v1(self.capture).segments
        seal = seal_t4_segment_v1(plan, store=self.store(), sealed_at=SEALED_AT)
        restored = restore_t4_seal_without_replay_v1(
            seal.identity, frame_manifests=seal.frame_manifests, sealed_at=seal.sealed_at, store=self.store(),
        )
        with self.assertRaisesRegex(T4MarkEvidenceError, "rebuilt_from_raw_capture"):
            mark_evidence_from_seal_v1(restored, store=self.store())

    def test_a_tampered_reference_frame_is_refused(self) -> None:
        self.session()
        (plan,) = discover_t4_segments_v1(self.capture).segments
        seal = seal_t4_segment_v1(plan, store=self.store(), sealed_at=SEALED_AT)
        store = self.store()
        manifest = store.load_manifest(seal.frame_manifests["T4_REFERENCE_PRICE"])
        path = store.object_path(manifest.objects[0])
        path.write_bytes(path.read_bytes()[:-8] + b"\x00" * 8)
        with self.assertRaises(ValueError):
            mark_evidence_from_seal_v1(seal, store=store)


class ResolutionTests(_MarkFixture):
    def test_an_exact_venue_timestamp_match_is_exact_and_still_not_the_settlement_mark(self) -> None:
        self.session(tamper=_retime({102: {"ts": SETTLEMENT}}))
        result = self.resolve(self.sealed())
        self.assertIs(result.status, EXACT)
        self.assertEqual(result.mark_price, Decimal("84000.20").quantize(PRICE_QUANTUM_V1))
        self.assertEqual([item.clock.record_sequence for item in result.exact], [102])
        self.assertEqual(result.nearby, ())
        self.assertEqual(result.bracket_before.record_sequence, 100)
        self.assertEqual(result.bracket_after.record_sequence, 103)  # closes the set at the instant
        self.assertEqual(result.knowable_at_micros, result.bracket_after.market_knowledge_micros)
        self.assertEqual(result.unresolved_decisions, (UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY,))
        self.assertEqual(result.symbol_identity["canonical_key"], "BYBIT:linear:BTCUSDT")
        self.assertEqual(result.max_clock_bound_nanos, OFFSET + RTT // 2 + RESOLUTION)
        self.assertEqual(result.payload()["evidence_kind"], VENUE_PUBLISHED_MARK_PRICE)

    def test_without_an_exact_observation_nearby_marks_are_reported_and_none_is_selected(self) -> None:
        self.session()
        result = self.resolve(self.sealed())
        self.assertIs(result.status, NEARBY)
        self.assertIsNone(result.mark_price)
        self.assertIsNone(result.payload()["mark_price"])
        self.assertEqual(result.exact, ())
        self.assertEqual(
            [(item.side, item.offset_millis, item.observation.clock.record_sequence) for item in result.nearby],
            [("BEFORE", _venue_ms(99) - SETTLEMENT, 99), ("AFTER", _venue_ms(102) - SETTLEMENT, 102)],
        )
        self.assertEqual([item.offset_millis for item in result.nearby], [-550, 950])
        self.assertIn(UNRESOLVED_OWNER_DECISION_NEARBY_MARK_SELECTION_CONVENTION, result.unresolved_decisions)
        self.assertIn(UNRESOLVED_OWNER_DECISION_SETTLEMENT_MARK_IDENTITY, result.unresolved_decisions)

    def test_duplicate_exact_marks_agree_and_distinct_ones_are_ambiguous(self) -> None:
        same = {102: {"ts": SETTLEMENT}, 103: {"ts": SETTLEMENT, "indexPrice": None, "markPrice": "84000.20"}}
        self.session(tamper=_retime(same))
        result = self.resolve(self.sealed())
        self.assertIs(result.status, EXACT)
        self.assertEqual([item.clock.record_sequence for item in result.exact], [102, 103])

    def test_distinct_marks_stamped_at_the_instant_are_refused_as_ambiguous(self) -> None:
        distinct = {102: {"ts": SETTLEMENT}, 103: {"ts": SETTLEMENT, "indexPrice": None, "markPrice": "84009.00"}}
        self.session(tamper=_retime(distinct))
        self.refused("ambiguous_ordering_distinct_marks_stamped_at_the_instant", self.sealed())

    def test_same_sealed_inputs_give_identical_resolution_hashes(self) -> None:
        self.session(tamper=_retime({102: {"ts": SETTLEMENT}}))
        first = self.resolve(self.sealed())
        again = self.resolve(self.sealed("other"), known_by=datetime(2030, 1, 1, tzinfo=UTC))
        self.assertEqual(first.resolution_hash, again.resolution_hash)
        self.assertEqual(first.payload(), again.payload())
        nearby = self.resolve(self.sealed(), settled_at_ms=SETTLEMENT + 1)
        self.assertIs(nearby.status, NEARBY)
        self.assertNotEqual(nearby.resolution_hash, first.resolution_hash)

    def test_a_result_not_yet_knowable_is_refused(self) -> None:
        self.session()
        datasets = self.sealed()
        result = self.resolve(datasets)
        self.resolve(datasets, known_by=result.knowable_at)
        self.refused("mark_evidence_not_yet_knowable_at_known_by", datasets,
                     known_by=result.knowable_at - timedelta(microseconds=1))
        self.refused("known_by_must_be_an_aware_utc_instant", datasets, known_by=LATER.replace(tzinfo=None))

    def test_a_non_complete_unjudged_or_foreign_hour_is_refused(self) -> None:
        session = self.session()
        datasets = self.sealed()
        for acceptance, code in (
            (_acceptance(session, verdict=HourVerdictV1.HEAD_UNPROVEN_EXCEEDS_BOUND),
             "hour_not_complete:HEAD_UNPROVEN_EXCEEDS_BOUND"),
            (_acceptance(session, hours_judged=0), "hour_not_judged"),
            (_acceptance(session, symbol="ETHUSDT"), "hourly_acceptance_does_not_cover_the_symbol"),
            (_acceptance(UUID(int=7)), "hour_proven_by_another_capture_session"),
        ):
            self.refused(code, datasets, acceptance=acceptance)

    def test_malformed_instants_symbols_and_inputs_are_refused(self) -> None:
        self.session()
        datasets = self.sealed()
        for instant in (True, -1, 0, float(SETTLEMENT), str(SETTLEMENT)):
            self.refused("settlement_instant_must_be_positive_integer_venue_millis", datasets, instant)
        self.refused("symbol_outside_the_funding_universe", datasets, symbol="DOGEUSDT")
        self.refused("dataset_symbol_differs_from_the_settlement_symbol", datasets, symbol="ETHUSDT")
        self.refused("dataset_supplied_more_than_once", [datasets[0], datasets[0]])
        self.refused("no_mark_evidence_dataset_supplied", [], session=datasets[0].session_id)

    def test_an_instant_outside_the_segment_is_never_extrapolated(self) -> None:
        self.session()
        datasets = self.sealed()
        for instant in (_venue_ms(0) - 1, _venue_ms(0), _venue_ms(480) + 1):
            self.refused("settlement_instant_not_bracketed_by_one_sealed_segment", datasets, instant)


class SegmentBoundaryTests(_MarkFixture):
    def test_nothing_is_carried_across_a_reconnect_gap(self) -> None:
        session = self.session(windows=[(BASE, BASE + 100 * SECOND), (BASE + 130 * SECOND, BASE + 240 * SECOND)])
        datasets = self.sealed()
        self.assertEqual(len(datasets), 2)
        self.assertEqual({item.session_id for item in datasets}, {session})
        # 00:12:00 lies in the gap: one segment ends before it, the next starts after it.
        gap = _ms(datetime(2026, 9, 20, 0, 12, tzinfo=UTC))
        self.refused("settlement_instant_not_bracketed_by_one_sealed_segment", datasets, gap, session=session)
        # Inside the first segment the same evidence still resolves, from that segment only.
        result = self.resolve(datasets, session=session)
        self.assertIs(result.status, NEARBY)

    def test_an_exact_mark_that_opens_a_segment_is_not_proven_complete(self) -> None:
        second_start = BASE + 130 * SECOND
        session = self.session(windows=[(BASE, BASE + 100 * SECOND), (second_start, BASE + 240 * SECOND)])
        datasets = self.sealed()
        opening = _venue_ms(0, second_start)  # the new segment's first ticker: a snapshot carrying a mark
        self.assertTrue(any(mark.exchange_ts_millis == opening for item in datasets for mark in item.marks))
        self.refused("settlement_instant_not_bracketed_by_one_sealed_segment", datasets, opening, session=session)

    def test_a_restart_gives_a_second_session_whose_marks_must_agree(self) -> None:
        first = self.session(tamper=_retime({102: {"ts": SETTLEMENT}}))
        self.session(tamper=_retime({102: {"ts": SETTLEMENT}}))
        datasets = self.sealed()
        self.assertEqual(len({item.session_id for item in datasets}), 2)
        result = self.resolve(datasets, session=first)
        self.assertIs(result.status, EXACT)
        self.assertEqual(result.dataset.session_id, first)
        self.assertEqual(len(result.corroborating), 1)

    def test_conflicting_marks_from_another_session_are_refused(self) -> None:
        first = self.session(tamper=_retime({102: {"ts": SETTLEMENT}}))
        self.session(tamper=_retime({102: {"ts": SETTLEMENT, "markPrice": "84999.90"}}))
        self.refused("conflicting_mark_observations_at_the_instant", self.sealed(), session=first)

    def test_another_session_seeing_a_mark_where_the_accepted_one_proved_none_conflicts(self) -> None:
        first = self.session()
        self.session(tamper=_retime({102: {"ts": SETTLEMENT}}))
        self.refused("conflicting_mark_observations_at_the_instant", self.sealed(), session=first)


if __name__ == "__main__":
    unittest.main()
