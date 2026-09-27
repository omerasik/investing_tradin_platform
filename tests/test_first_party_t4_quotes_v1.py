"""Phase R3A.2 -- first-party T4 top-of-book and funding sidecars of a sealed segment.

FIXTURE capture only, written under a temporary root by the R3A fixture archive
(host exactly 10 s behind the venue); nothing touches the real archive.
"""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from typing import Any
from uuid import uuid4

from tests.test_first_party_t4_v1 import (
    BASE,
    BOUND,
    SECOND,
    _record,
    _standard_samples,
    _TempRoots,
    _ticker,
)
from trade_platform.first_party_t4_quotes_v1 import (
    BOOK_CROSSED,
    BOOK_LOCKED,
    BOOK_NORMAL,
    T4QuoteNormalizerV1,
    T4QuotesError,
    describe_level1_spread_v1,
)

FUNDING = {"fundingRate": "-0.00005304", "nextFundingTime": "1790265600000",
           "fundingIntervalHour": "8", "fundingCap": "0.00333"}
BOOK = {"bid1Price": "84000.00", "bid1Size": "1.500", "ask1Price": "84000.10", "ask1Size": "0.200"}


class QuoteStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = uuid4()
        self.normalizer = T4QuoteNormalizerV1(exchange_symbol="BTCUSDT", session_id=self.session)
        self.sequence = 0

    def feed(self, host: int, kind: str, **fields: str) -> None:
        try:
            self.normalizer.feed(_record(self.sequence, host, _ticker(host, kind, **fields), self.session), BOUND)
        finally:
            self.sequence += 1

    def test_no_row_until_every_level1_field_is_observed_and_delta_changes_only_its_fields(self) -> None:
        self.feed(BASE, "delta", bid1Price="84000.00", bid1Size="1.5")
        self.assertEqual(self.normalizer.top_of_book, [])
        self.feed(BASE + SECOND, "delta", ask1Price="84000.10", ask1Size="0.2")
        self.feed(BASE + 2 * SECOND, "delta", ask1Size="0.9")
        first, second = self.normalizer.top_of_book
        self.assertEqual((first.bid_price, first.ask_price, first.ask_size), (Decimal("84000.00"), Decimal("84000.10"), Decimal("0.2")))
        self.assertEqual((second.bid_size, second.ask_size), (Decimal("1.5"), Decimal("0.9")))
        self.assertEqual(second.arrival.record_sequence, 2)
        self.assertEqual(self.normalizer.counts.book_records_before_state_complete, 1)

    def test_snapshot_replaces_state_and_an_uncarried_field_becomes_unobserved(self) -> None:
        self.feed(BASE, "snapshot", **BOOK)
        self.feed(BASE + SECOND, "snapshot", bid1Price="1", bid1Size="1", ask1Price="2")
        self.feed(BASE + 2 * SECOND, "delta", markPrice="84000.0")  # carries no level-1 field
        self.assertEqual(len(self.normalizer.top_of_book), 1)
        self.assertEqual(self.normalizer.counts.book_records_before_state_complete, 1)

    def test_an_empty_level_is_unobserved_not_filled(self) -> None:
        self.feed(BASE, "snapshot", **BOOK)
        self.feed(BASE + SECOND, "delta", ask1Price="", ask1Size="")
        self.feed(BASE + 2 * SECOND, "delta", bid1Size="0")
        self.assertEqual(len(self.normalizer.top_of_book), 1)
        self.assertEqual(self.normalizer.counts.empty_level_updates, 3)

    def test_locked_and_crossed_quotes_are_kept_and_labelled(self) -> None:
        self.feed(BASE, "snapshot", **BOOK)
        self.feed(BASE + SECOND, "delta", ask1Price="84000.00")
        self.feed(BASE + 2 * SECOND, "delta", ask1Price="83999.90")
        states = [quote.book_state for quote in self.normalizer.top_of_book]
        self.assertEqual(states, [BOOK_NORMAL, BOOK_LOCKED, BOOK_CROSSED])
        self.assertEqual((self.normalizer.counts.locked_rows, self.normalizer.counts.crossed_rows), (1, 1))

    def test_funding_is_published_state_and_needs_every_field(self) -> None:
        self.feed(BASE, "delta", fundingRate="0.0001")
        self.assertEqual(self.normalizer.funding, [])
        self.feed(BASE + SECOND, "snapshot", **FUNDING)
        self.feed(BASE + 2 * SECOND, "delta", fundingRate="-0.00006")
        first, second = self.normalizer.funding
        self.assertEqual(first.published_funding_rate, Decimal("-0.00005304"))
        self.assertEqual((first.next_funding_time_millis, first.funding_interval_hours), (1790265600000, 8))
        self.assertEqual(second.published_funding_rate, Decimal("-0.00006"))
        self.assertEqual(second.funding_cap, Decimal("0.00333"))

    def test_malformed_values_and_regressions_fail_closed(self) -> None:
        with self.assertRaisesRegex(T4QuotesError, "malformed"):
            self.feed(BASE, "snapshot", bid1Price="abc")
        with self.assertRaisesRegex(T4QuotesError, "not_a_valid_level_value"):
            self.feed(BASE + SECOND, "snapshot", bid1Price="-1")
        with self.assertRaisesRegex(T4QuotesError, "not_a_valid_level_value"):
            self.feed(BASE + SECOND + 1, "snapshot", bid1Price="0")
        with self.assertRaisesRegex(T4QuotesError, "positive_integer"):
            self.feed(BASE + 2 * SECOND, "snapshot", fundingIntervalHour="0")
        self.feed(BASE + 5 * SECOND, "snapshot", **BOOK)
        with self.assertRaisesRegex(T4QuotesError, "arrival_regressed"):
            self.feed(BASE + 3 * SECOND, "delta", ask1Size="1")


class QuoteKnowledgeTests(unittest.TestCase):
    def test_a_row_is_known_no_earlier_than_any_field_it_carries(self) -> None:
        from trade_platform.first_party_t4_normalization_v1 import ArrivalClockBoundEvidenceV1

        session = uuid4()
        normalizer = T4QuoteNormalizerV1(exchange_symbol="BTCUSDT", session_id=session)
        wide = ArrivalClockBoundEvidenceV1(BOUND.venue_minus_host_upper_bound_nanos + 50_000_000, "p", "n")
        bid = {"bid1Price": "84000.00", "bid1Size": "1.5"}
        normalizer.feed(_record(0, BASE, _ticker(BASE, "delta", **bid), session), wide)
        later = BASE + 1_000_000  # 1 ms later, but under a tighter bound
        normalizer.feed(_record(1, later, _ticker(later, "delta", ask1Price="84000.10", ask1Size="0.2"), session), BOUND)
        (quote,) = normalizer.top_of_book
        own = quote.arrival.market_knowledge_micros
        self.assertLess(own, quote.market_knowledge_micros)  # the emitting record alone would be too early
        self.assertEqual(quote.market_knowledge_micros, -(-(BASE + wide.venue_minus_host_upper_bound_nanos) // 1_000))


def _with_quotes(sequence: int, payload: str) -> str:
    """Tamper hook: add level-1 and funding fields to the fixture's ticker stream."""
    message = json.loads(payload)
    if not message["topic"].startswith("tickers"):
        return payload
    if message["type"] == "snapshot":
        message["data"].update(BOOK | FUNDING)
    elif sequence % 3 == 0:
        message["data"]["ask1Price"] = "84000.20" if sequence % 2 else "84000.10"
    else:
        message["data"]["bid1Size"] = f"1.{sequence % 10}"
    return json.dumps(message, separators=(",", ":"))


class QuoteSidecarTests(_TempRoots):
    def _seal(self) -> Any:
        from trade_platform.first_party_t4_seal_v1 import (
            discover_t4_segments_v1,
            seal_t4_segment_v1,
        )

        self.archive.session(windows=[(BASE, BASE + 120 * SECOND)], samples=_standard_samples(), tamper=_with_quotes)
        plan = discover_t4_segments_v1(self.capture).segments[0]
        return seal_t4_segment_v1(plan, store=self.store())

    def test_derivation_is_deterministic_and_binds_the_parent_seal(self) -> None:
        from trade_platform.first_party_t4_quotes_v1 import derive_t4_quotes_v1
        from trade_platform.research_data_plane_v1 import T4_TOP_OF_BOOK_FRAME

        seal = self._seal()
        written = derive_t4_quotes_v1(seal, capture_root=self.capture, store=self.store())
        rebuilt = derive_t4_quotes_v1(seal, capture_root=self.capture)
        self.assertEqual(written.content_hash, rebuilt.content_hash)
        self.assertEqual(written.quote_dataset_id, rebuilt.quote_dataset_id)
        self.assertEqual(written.identity["parent_seal_content_hash"], seal.content_hash)
        self.assertEqual(rebuilt.frame_manifests, {})
        counts = written.identity["counts"]
        self.assertGreater(counts["top_of_book_rows"], 0)
        self.assertGreater(counts["funding_rows"], 0)
        self.assertEqual(counts["records"], seal.identity["segment"]["admitted_records"])
        # Every row carries the same knowledge time the seal's doctrine gives its record.
        store = self.store()
        manifest = store.load_manifest(written.frame_manifests[T4_TOP_OF_BOOK_FRAME.kind])
        store.verify(manifest)
        self.assertEqual(manifest.row_count, counts["top_of_book_rows"])
        for quote in written.top_of_book:
            self.assertGreaterEqual(quote.arrival.market_knowledge_micros * 1_000, quote.arrival.arrival_utc_nanos)

    def test_a_seal_not_rebuilt_from_raw_capture_is_refused(self) -> None:
        from trade_platform.first_party_t4_quotes_v1 import derive_t4_quotes_v1
        from trade_platform.first_party_t4_seal_v1 import restore_t4_seal_without_replay_v1

        seal = self._seal()
        restored = restore_t4_seal_without_replay_v1(
            seal.identity, frame_manifests=seal.frame_manifests, sealed_at=seal.sealed_at, store=self.store()
        )
        with self.assertRaisesRegex(T4QuotesError, "raw_replayed"):
            derive_t4_quotes_v1(restored, capture_root=self.capture)


class SpreadDescriptionTests(unittest.TestCase):
    def test_time_weighted_quantiles_are_exact_and_skip_locked_and_crossed(self) -> None:
        session = uuid4()
        normalizer = T4QuoteNormalizerV1(exchange_symbol="BTCUSDT", session_id=session)
        hosts = [BASE, BASE + SECOND, BASE + 10 * SECOND, BASE + 11 * SECOND, BASE + 12 * SECOND]
        asks = ["84000.10", "84000.50", "84000.00", "84000.10", "84000.10"]
        for sequence, (host, ask) in enumerate(zip(hosts, asks, strict=True)):
            fields = BOOK | {"ask1Price": ask} if sequence == 0 else {"ask1Price": ask}
            kind = "snapshot" if sequence == 0 else "delta"
            normalizer.feed(_record(sequence, host, _ticker(host, kind, **fields), session), BOUND)
        summary = describe_level1_spread_v1(
            normalizer.top_of_book, unobserved_from=normalizer.book_unobserved_from, tick_size=Decimal("0.1")
        )
        self.assertEqual(summary["book_states"], {BOOK_NORMAL: 4, BOOK_LOCKED: 1, BOOK_CROSSED: 0})
        # 1 tick held 1 s + 1 s, 5 ticks held 9 s; the last quote has no observed end.
        self.assertEqual(summary["observed_nanos_time_weighted"], 11 * SECOND)
        self.assertEqual(summary["spread_ticks_by_time"]["0.5"], "5")
        self.assertEqual(summary["spread_ticks_by_message"]["0.5"], "1")
        self.assertEqual(summary["share_of_time_at_one_tick"], "0.181818")
        with self.assertRaisesRegex(T4QuotesError, "tick_size"):
            describe_level1_spread_v1(normalizer.top_of_book, unobserved_from=(), tick_size=Decimal(0))

    def test_a_quote_stops_standing_when_the_book_becomes_unobserved(self) -> None:
        session = uuid4()
        normalizer = T4QuoteNormalizerV1(exchange_symbol="BTCUSDT", session_id=session)
        steps = [(BASE, "snapshot", BOOK), (BASE + SECOND, "delta", {"ask1Price": ""}),
                 (BASE + 30 * SECOND, "delta", {"ask1Price": "84000.10"}),
                 (BASE + 31 * SECOND, "delta", {"bid1Size": "2"})]
        for sequence, (host, kind, fields) in enumerate(steps):
            normalizer.feed(_record(sequence, host, _ticker(host, kind, **fields), session), BOUND)
        self.assertEqual(normalizer.book_unobserved_from, [BASE + SECOND])
        summary = describe_level1_spread_v1(
            normalizer.top_of_book, unobserved_from=normalizer.book_unobserved_from, tick_size=Decimal("0.1")
        )
        # 1 s before the ask vanished + 1 s for the restored quote; never the 29 s it was unobserved.
        self.assertEqual(summary["observed_nanos_time_weighted"], 2 * SECOND)

    def test_quotes_from_two_segments_are_refused(self) -> None:
        quotes = []
        for session in (uuid4(), uuid4()):
            normalizer = T4QuoteNormalizerV1(exchange_symbol="BTCUSDT", session_id=session)
            normalizer.feed(_record(0, BASE, _ticker(BASE, "snapshot", **BOOK), session), BOUND)
            quotes.extend(normalizer.top_of_book)
        with self.assertRaisesRegex(T4QuotesError, "one_ordered_segment"):
            describe_level1_spread_v1(quotes, unobserved_from=(), tick_size=Decimal("0.1"))


if __name__ == "__main__":
    unittest.main()
