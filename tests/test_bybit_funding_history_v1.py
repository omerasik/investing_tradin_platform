"""OR-6 F3 -- Bybit-published settled funding: strict parse, gated acquisition, completeness, charge rule.

Offline. Every page is a synthetic fixture shaped like the public endpoint's
response; no rate here is a real observation.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.bybit_funding_history_v1 import (
    COMPLETE,
    IRREGULAR_REFUSED,
    FundingHistoryError,
    FundingHistoryV1,
    acquire_funding_history_v1,
    funding_steps_v1,
    load_funding_history_v1,
    parse_funding_page_v1,
    require_covering_complete_v1,
)
from trade_platform.bybit_public_archive_v1 import HttpResponseV1
from trade_platform.public_archive_research_bars_v1 import ResearchBarsError
from trade_platform.strategy_lab_authority_rerun_v1 import _authoritative, decimal_metrics_v1
from trade_platform.strategy_sdk_v1 import BarsV1

DAY = date(2026, 6, 1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_H = 3_600_000


def _ms(value: datetime) -> int:
    return (value - _EPOCH) // timedelta(milliseconds=1)


class FundingPages:
    """A fake endpoint: ``rates`` maps settlement ms -> rate text; pages come back newest first."""

    def __init__(self, rates: Mapping[int, str], *, symbol: str = "BTCUSDT", server_time: int = 1) -> None:
        self.rates, self.symbol, self.server_time, self.calls = dict(rates), symbol, server_time, 0

    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResponseV1:
        self.calls += 1
        query = dict(part.split("=") for part in url.split("?")[1].split("&"))
        start, end = int(query["startTime"]), int(query["endTime"])
        rows = [{"symbol": self.symbol, "fundingRate": rate, "fundingRateTimestamp": str(at)}
                for at, rate in sorted(self.rates.items(), reverse=True) if start <= at <= end]
        body = {"retCode": 0, "retMsg": "OK", "result": {"category": "linear", "list": rows},
                "retExtInfo": {}, "time": self.server_time}
        return HttpResponseV1(200, {}, json.dumps(body).encode())


def eight_hourly(first: date, days: int, rate: str = "0.0001") -> dict[int, str]:
    start = _ms(datetime(first.year, first.month, first.day, tzinfo=UTC))
    return {start + i * 8 * _H: rate for i in range(days * 3)}


def fixture_funding_history(root: Path, symbol: str, first: date, days: int, *, opening: Any = None,
                            rate: str = "0.0001") -> FundingHistoryV1:
    """A COMPLETE 8-hourly fixture history (shared with the R6 validation tests)."""
    return acquire_funding_history_v1(root, symbol, first, first + timedelta(days=days - 1), holdout_opening=opening,
                                      fetch=FundingPages(eight_hourly(first, days, rate), symbol=symbol),
                                      now=lambda: datetime(2026, 10, 9, tzinfo=UTC))


def minute_bars(start: datetime, prices: Sequence[str], *, skip: Sequence[int] = ()) -> BarsV1:
    rows = []
    for i, price in enumerate(prices):
        if i in skip:
            continue
        at = start + timedelta(minutes=i)
        value = Decimal(price)
        rows.append((at, at + timedelta(minutes=1), None, value, value, value, value))
    return BarsV1.from_rows(rows)


class _Root(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="funding-"))
        self.root = self.base / "root"

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def history(self, rates: Mapping[int, str], days: int = 1, first: date = DAY) -> FundingHistoryV1:
        # Each fixture its own root: a stored page is authoritative and is never refetched.
        self.root = Path(tempfile.mkdtemp(prefix="funding-", dir=self.base))
        return acquire_funding_history_v1(self.root, "BTCUSDT", first, first + timedelta(days=days - 1),
                                          fetch=FundingPages(rates), now=lambda: datetime(2026, 10, 9, tzinfo=UTC))


class StrictParseTests(unittest.TestCase):
    def _page(self, rows: list[Any], **overrides: Any) -> bytes:
        return json.dumps({"retCode": 0, "result": {"category": "linear", "list": rows}, **overrides}).encode()

    def test_every_defect_is_refused(self) -> None:
        good = {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingRateTimestamp": "1000"}
        cases = {
            "ret_code": self._page([good], retCode=10001),
            "symbol": self._page([{**good, "symbol": "ETHUSDT"}]),
            "extra_field": self._page([{**good, "extra": "1"}]),
            "rate_number": self._page([{**good, "fundingRate": 0.0001}]),
            "rate_text": self._page([{**good, "fundingRate": "abc"}]),
            "rate_nan": self._page([{**good, "fundingRate": "NaN"}]),
            "stamp": self._page([{**good, "fundingRateTimestamp": "1e3"}]),
            "stamp_non_ascii": self._page([{**good, "fundingRateTimestamp": "١٠٠٠"}]),
            "stamp_superscript": self._page([{**good, "fundingRateTimestamp": "²"}]),
            "rate_exponent": self._page([{**good, "fundingRate": "1E-4"}]),
            "rate_padded": self._page([{**good, "fundingRate": " 0.0001"}]),
            "rate_underscore": self._page([{**good, "fundingRate": "0.000_1"}]),
            "rate_plus": self._page([{**good, "fundingRate": "+0.0001"}]),
            "outside": self._page([{**good, "fundingRateTimestamp": "5000"}]),
            "repeat": self._page([good, good]),
            "truncated": self._page([{**good, "fundingRateTimestamp": str(i)} for i in range(200)]),
            "not_json": b"<html>",
        }
        for name, body in cases.items():
            with self.subTest(name), self.assertRaises(FundingHistoryError):
                parse_funding_page_v1(body, symbol="BTCUSDT", start_ms=0, end_ms=4000)

    def test_rates_are_kept_as_published_text_ascending(self) -> None:
        rows = [{"symbol": "BTCUSDT", "fundingRate": "-0.00002871", "fundingRateTimestamp": "2000"},
                {"symbol": "BTCUSDT", "fundingRate": "0.00010000", "fundingRateTimestamp": "1000"}]
        events = parse_funding_page_v1(self._page(rows), symbol="BTCUSDT", start_ms=0, end_ms=4000)
        self.assertEqual([(1000, "0.00010000"), (2000, "-0.00002871")], [(e.settled_at_ms, e.rate_text) for e in events])


class AcquisitionTests(_Root):
    def test_complete_window_is_checkpointed_and_reproven(self) -> None:
        fetch = FundingPages(eight_hourly(DAY, 2))
        now = lambda: datetime(2026, 10, 9, tzinfo=UTC)
        first = acquire_funding_history_v1(self.root, "BTCUSDT", DAY, DAY + timedelta(days=1), fetch=fetch, now=now)
        self.assertEqual(COMPLETE, first.status)
        self.assertEqual(8 * _H, first.identity["completeness"]["interval_ms"])
        self.assertEqual(6, len(first.events()))
        self.assertEqual(2, fetch.calls)
        again = acquire_funding_history_v1(self.root, "BTCUSDT", DAY, DAY + timedelta(days=1), fetch=fetch, now=now)
        self.assertEqual(2, fetch.calls)  # stored pages are re-read, never refetched
        self.assertEqual((first.content_hash, first.provenance_hash), (again.content_hash, again.provenance_hash))
        self.assertEqual(first, load_funding_history_v1(self.root, first.dataset_version_id))

    def test_rerun_determinism_identity_is_content_provenance_is_bytes(self) -> None:
        other = Path(tempfile.mkdtemp(prefix="funding-b-"))
        self.addCleanup(shutil.rmtree, other, True)
        now = lambda: datetime(2026, 10, 9, tzinfo=UTC)
        a = acquire_funding_history_v1(self.root, "BTCUSDT", DAY, DAY, fetch=FundingPages(eight_hourly(DAY, 1)), now=now)
        b = acquire_funding_history_v1(other, "BTCUSDT", DAY, DAY,
                                       fetch=FundingPages(eight_hourly(DAY, 1), server_time=99), now=now)
        self.assertEqual(a.content_hash, b.content_hash)  # same published records -> same identity
        self.assertNotEqual(a.provenance_hash, b.provenance_hash)  # different raw bytes stay distinguishable

    def test_a_changed_raw_page_is_refused(self) -> None:
        dataset = self.history(eight_hourly(DAY, 1))
        (page,) = (self.root / "funding-history").rglob("*.json")
        page.write_bytes(page.read_bytes().replace(b"0.0001", b"0.0002"))
        with self.assertRaises(FundingHistoryError):
            load_funding_history_v1(self.root, dataset.dataset_version_id)

    def test_the_declared_window_must_be_exactly_its_stored_pages(self) -> None:
        dataset = acquire_funding_history_v1(self.root, "BTCUSDT", DAY, DAY + timedelta(days=1),
                                             fetch=FundingPages(eight_hourly(DAY, 2)),
                                             now=lambda: datetime(2026, 10, 9, tzinfo=UTC))
        (path,) = (self.root / "datasets").rglob("*.json")
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["identity"]["last_utc_day"] = DAY.isoformat()  # claims one day, still lists two pages
        path.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaises(FundingHistoryError):
            load_funding_history_v1(self.root, dataset.dataset_version_id)

    def test_holdout_days_need_an_issued_opening_and_open_days_are_refused(self) -> None:
        holdout_day = date(2026, 8, 25)
        with self.assertRaises(ResearchBarsError):
            self.history(eight_hourly(holdout_day, 1), first=holdout_day)
        with self.assertRaises(ResearchBarsError):
            self.history(eight_hourly(date(2026, 8, 19), 2), days=2, first=date(2026, 8, 19))  # straddles
        opening = validation._issue_opening("cycle-2026-08-20", datetime(2026, 8, 20, tzinfo=UTC),
                                            datetime(2026, 9, 1, tzinfo=UTC), "p" * 64, "test")
        admitted = fixture_funding_history(self.root, "BTCUSDT", holdout_day, 1, opening=opening)
        self.assertEqual(COMPLETE, admitted.status)
        with self.assertRaises(ResearchBarsError):  # catalogued holdout funding is gated on read too
            load_funding_history_v1(self.root, admitted.dataset_version_id)
        self.assertEqual(admitted, load_funding_history_v1(self.root, admitted.dataset_version_id,
                                                           holdout_opening=opening))
        with self.assertRaises(FundingHistoryError):  # the day has not closed: its last settlement may be pending
            acquire_funding_history_v1(self.root, "BTCUSDT", DAY, DAY, fetch=FundingPages(eight_hourly(DAY, 1)),
                                       now=lambda: datetime(2026, 6, 1, 20, tzinfo=UTC))


class CompletenessTests(_Root):
    def test_a_missing_observation_is_refused_never_inferred(self) -> None:
        rates = eight_hourly(DAY, 2)
        del rates[sorted(rates)[3]]
        dataset = self.history(rates, days=2)
        self.assertEqual(IRREGULAR_REFUSED, dataset.status)
        self.assertIsNone(dataset.identity["completeness"]["interval_ms"])
        self.assertEqual(5, len(dataset.events()))  # nothing was filled in
        bars = minute_bars(datetime(2026, 6, 1, tzinfo=UTC), ["100"] * 5)
        with self.assertRaises(FundingHistoryError):
            require_covering_complete_v1(dataset, bars, "BTCUSDT")

    def test_an_interval_change_or_a_loose_edge_is_irregular(self) -> None:
        start = _ms(datetime(2026, 6, 1, tzinfo=UTC))
        changed = {start: "0.0001", start + 8 * _H: "0.0001", start + 12 * _H: "0.0001", start + 16 * _H: "0.0001",
                   start + 20 * _H: "0.0001"}
        self.assertEqual(IRREGULAR_REFUSED, self.history(changed).status)
        late_start = {start + 8 * _H: "0.0001", start + 16 * _H: "0.0001"}  # 00:00 not published
        self.assertIn("LEADING_EDGE_NOT_PROVEN", " ".join(self.history(late_start).identity["completeness"]
                                                          ["irregularities"]))

    def test_an_empty_window_is_irregular(self) -> None:
        self.assertEqual(IRREGULAR_REFUSED, self.history({}).status)

    def test_coverage_symbol_and_span_are_required(self) -> None:
        dataset = self.history(eight_hourly(DAY, 1))
        inside = minute_bars(datetime(2026, 6, 1, 7, 58, tzinfo=UTC), ["100"] * 4)
        require_covering_complete_v1(dataset, inside, "BTCUSDT")
        with self.assertRaises(FundingHistoryError):
            require_covering_complete_v1(dataset, inside, "ETHUSDT")
        with self.assertRaises(FundingHistoryError):
            require_covering_complete_v1(dataset, minute_bars(datetime(2026, 6, 1, 23, 58, tzinfo=UTC), ["1"] * 4),
                                         "BTCUSDT")


class ChargeRuleTests(_Root):
    """Bars 07:58..08:02 on DAY (index 2 opens at 08:00), one 8-hourly history with a fixture rate."""

    def setUp(self) -> None:
        super().setUp()
        self.bars = minute_bars(datetime(2026, 6, 1, 7, 58, tzinfo=UTC), ["100", "100", "102", "102", "102"])

    def steps(self, held: list[int], rate: str = "0.0001", cost: str = "0", bars: BarsV1 | None = None) -> Any:
        history = self.history(eight_hourly(DAY, 1, rate))
        with _authoritative():
            return funding_steps_v1(bars or self.bars, held, history, cost_fraction=Decimal(cost))

    def test_no_crossing_charges_nothing(self) -> None:
        result = self.steps([0, 0, 0, -1, 0])  # flat at 08:00 on both sides, short only afterwards
        self.assertEqual((1, 0, 1, 0), (result.events_in_span, result.charged, result.flat,
                                        result.ambiguous_worse_charged))
        self.assertTrue(all(step == 0 for step in result.steps))

    def test_long_pays_a_positive_rate_on_the_value_at_the_instant(self) -> None:
        result = self.steps([0, 1, 1, 1, 0])
        self.assertEqual(1, result.charged)
        self.assertEqual(Decimal("-0.0001") * Decimal("102") / Decimal("100"), result.steps[1])

    def test_short_receives_a_positive_rate_and_long_receives_a_negative_one(self) -> None:
        self.assertEqual(Decimal("0.0001") * Decimal("1.02"), self.steps([0, -1, -1, 0, 0]).steps[1])
        self.assertEqual(Decimal("0.00005") * Decimal("1.02"), self.steps([0, 1, 1, 0, 0], rate="-0.00005").steps[1])

    def test_multiple_crossings_each_charge_once(self) -> None:
        bars = minute_bars(datetime(2026, 6, 1, tzinfo=UTC), ["100"] * (16 * 60 + 2))
        held = [0] + [1] * (bars.size - 1)
        result = self.steps(held, bars=bars)
        self.assertEqual((3, 2, 1, 0), (result.events_in_span, result.charged, result.flat,
                                        result.ambiguous_worse_charged))  # 00:00 is the span start: flat
        charged = [i for i, step in enumerate(result.steps) if step]
        self.assertEqual([8 * 60 - 1, 16 * 60 - 1], charged)
        self.assertEqual([Decimal("-0.0001")] * 2, [result.steps[i] for i in charged])

    def test_a_change_filled_at_the_instant_charges_the_worse_outcome_once(self) -> None:
        exit_at = self.steps([0, 1, 0, 0, 0])  # long before, flat after: paying is worse
        self.assertEqual((1, Decimal("-0.000102")), (exit_at.ambiguous_worse_charged, exit_at.steps[1]))
        enter_at = self.steps([0, 0, 1, 1, 0])  # flat before, long after: paying is worse, booked at T on k-1
        self.assertEqual((Decimal("-0.0001"), Decimal(0)), (enter_at.steps[1], enter_at.steps[2]))
        grown = self.steps([0, 1, 2, 2, 0], cost="0.001")  # post-fill value compounds from T: x interval growth
        self.assertEqual(-2 * Decimal("0.0001") * (1 + Decimal("0.02") - Decimal("0.001")), grown.steps[1])
        receive = self.steps([0, 1, 0, 0, 0], rate="-0.0001")  # long would receive: the worse outcome is flat
        self.assertTrue(all(step == 0 for step in receive.steps))
        flip = self.steps([0, 1, -1, -1, 0], cost="0.0005")  # long pays 1.02x vs short receives
        self.assertEqual((Decimal("-0.000102"), Decimal(0)), (flip.steps[1], flip.steps[2]))

    def test_a_position_at_a_first_bar_opening_on_a_settlement_is_refused(self) -> None:
        bars = minute_bars(datetime(2026, 6, 1, 8, tzinfo=UTC), ["100", "100"])
        self.assertEqual(1, self.steps([0, 0], bars=bars).flat)
        with self.assertRaises(FundingHistoryError):  # held_positions_v1 never produces it; refused, not approximated
            self.steps([1, 1], bars=bars)

    def test_a_missing_bar_at_the_instant_is_flat_or_not_costable(self) -> None:
        gapped = minute_bars(datetime(2026, 6, 1, 7, 58, tzinfo=UTC), ["100", "100", "102", "102", "102"], skip=[2])
        self.assertEqual(1, self.steps([0, 0, 0, 0], bars=gapped).flat)
        held = self.steps([0, 1, 1, 0], bars=gapped)
        self.assertEqual(["NO_BAR_AT_FUNDING_INSTANT_WHILE_HELD:2026-06-01T08:00:00+00:00"], list(held.not_costable))
        self.assertTrue(all(step == 0 for step in held.steps))  # nothing interpolated

    def test_metrics_compound_funding_and_gross_keeps_its_keys(self) -> None:
        history = self.history(eight_hourly(DAY, 1))
        gross = decimal_metrics_v1(self.bars, [0, 1, 1, 1, 0])
        self.assertNotIn("funding_mode", gross)
        net = decimal_metrics_v1(self.bars, [0, 1, 1, 1, 0], cost_bps_per_side=Decimal("10"), cost_label="x",
                                 funding=history)
        # Interval 1: +2 % price, 10 bps entry, the 08:00 funding on 1.02x value; interval 4: 10 bps exit.
        expected = (1 + Decimal("0.02") - Decimal("0.001") - Decimal("0.000102")) * Decimal("0.999") - 1
        self.assertEqual(format(expected.quantize(Decimal("1E-18")), "f"), net["total_return"])
        self.assertEqual(f"CHARGED:bybit-funding-charge-v1:{history.content_hash}", net["funding_mode"])
        self.assertEqual("-0.000102000000000000", net["funding_sum_of_step_fractions"])
        rerun = decimal_metrics_v1(self.bars, [0, 1, 1, 1, 0], cost_bps_per_side=Decimal("10"), cost_label="x",
                                   funding=load_funding_history_v1(self.root, history.dataset_version_id))
        self.assertEqual(net, rerun)


if __name__ == "__main__":
    unittest.main()
